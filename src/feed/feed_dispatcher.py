"""
feed_dispatcher.py
Phase 6: Offline-Testable Feed Dispatcher Worker.
Consumes normalized MarketSnapshot instances from DropOldestSnapshotQueue,
enforces pre-dispatch monotonic freshness and health readiness boundaries,
and safely routes trusted snapshots to StrategyRunner.process_snapshot().

Guarantees:
- Strict paper boundary: never connects to external broker or live feed socket.
- Never starts threads during import or instantiation.
- Pre-processing monotonic age check prevents queue latency stale data execution.
- Fail-closed: unready, disconnected, stale, malformed, or missing receipt timestamps are dropped.
- Worker exception isolation: runner exceptions never terminate dispatcher thread.
- Clean shutdown: bounded polling timeout, clear shutdown signal, and safe thread join.
- Dequeueing never mutates or refreshes feed health monitor timestamps.
"""

import logging
import math
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from src.feed.feed_health import FeedHealthMonitor
from src.feed.feed_queue import DropOldestSnapshotQueue
from src.market.market_snapshot import MarketSnapshot
from src.strategies.strategy_runner import StrategyRunner

logger = logging.getLogger("FeedDispatcher")


class FeedDispatcher:
    """
    Background worker consuming snapshots from a bounded queue and dispatching
    to StrategyRunner with monotonic freshness enforcement.
    """

    DEFAULT_POLL_TIMEOUT: float = 0.2
    DEFAULT_STALE_THRESHOLD_SECONDS: float = 5.0

    def __init__(
        self,
        queue: Any,
        runner: StrategyRunner,
        health_monitor: Optional[FeedHealthMonitor] = None,
        stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD_SECONDS,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        time_provider: Optional[Callable[[], float]] = None,
    ):
        """
        Initializes the FeedDispatcher without starting any background thread.

        Parameters:
            queue: DropOldestSnapshotQueue or queue-like object with get() method.
            runner: StrategyRunner instance to process verified snapshots.
            health_monitor: Shared FeedHealthMonitor instance for readiness evaluation.
            stale_threshold_seconds: Maximum allowed age (in seconds) between receipt and dispatch.
            poll_timeout: Bounded timeout in seconds for queue.get().
            time_provider: Callable returning monotonic time (defaults to time.monotonic).
        """
        if queue is None:
            raise ValueError("queue is required and cannot be None")
        if runner is None:
            raise ValueError("runner is required and cannot be None")

        if (
            isinstance(stale_threshold_seconds, bool)
            or not isinstance(stale_threshold_seconds, (int, float))
            or not math.isfinite(stale_threshold_seconds)
            or stale_threshold_seconds <= 0.0
        ):
            raise ValueError(f"stale_threshold_seconds must be a positive finite number, got {stale_threshold_seconds!r}")

        self.queue: Any = queue
        self.runner: StrategyRunner = runner
        self.health_monitor: Optional[FeedHealthMonitor] = health_monitor
        self.stale_threshold_seconds: float = float(stale_threshold_seconds)
        self.poll_timeout: float = max(0.01, float(poll_timeout))
        self.time_provider: Callable[[], float] = time_provider if time_provider is not None else time.monotonic

        # Threading state
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._dispatch_gate: threading.Lock = threading.Lock()
        self._is_running: bool = False
        self._in_flight_dispatch: bool = False
        self._lock = threading.Lock()

        # Telemetry metrics
        self._snapshots_dequeued: int = 0
        self._snapshots_dispatched: int = 0
        self._snapshots_stale_dropped: int = 0
        self._snapshots_invalid_dropped: int = 0
        self._snapshots_health_rejected: int = 0
        self._runner_exceptions_count: int = 0
        self._last_rejection_reason: Optional[str] = None
        self._last_error: Optional[str] = None
        self._clean_shutdown: Optional[bool] = None

    @property
    def is_running(self) -> bool:
        """Returns True if the worker thread is running and stop_event is not set."""
        return self._is_running and not self._stop_event.is_set()

    @property
    def metrics(self) -> Dict[str, Any]:
        """Returns snapshot of operational dispatcher metrics."""
        with self._lock:
            return {
                "is_running": self.is_running,
                "in_flight_dispatch": self._in_flight_dispatch,
                "snapshots_dequeued": self._snapshots_dequeued,
                "snapshots_dispatched": self._snapshots_dispatched,
                "snapshots_stale_dropped": self._snapshots_stale_dropped,
                "snapshots_invalid_dropped": self._snapshots_invalid_dropped,
                "snapshots_health_rejected": self._snapshots_health_rejected,
                "runner_exceptions_count": self._runner_exceptions_count,
                "last_rejection_reason": self._last_rejection_reason,
                "last_error": self._last_error,
                "clean_shutdown": self._clean_shutdown,
            }

    def _sample_monotonic_time(self) -> Tuple[Optional[float], Optional[str]]:
        """Safely samples monotonic time with type and finiteness validation."""
        try:
            raw = self.time_provider()
        except Exception as exc:
            return None, f"CLOCK_EXCEPTION: {type(exc).__name__}: {exc}"

        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            return None, f"CLOCK_INVALID: Expected finite float, got {raw!r}"
        return float(raw), None

    def validate_and_dispatch(self, snapshot: Any) -> Tuple[bool, Optional[str]]:
        """
        Validates snapshot type, monotonic receipt timestamp, age threshold,
        and health monitor readiness before passing to StrategyRunner.
        
        Guarantees:
        - Never mutates feed health monitor timestamps on dequeue or processing.
        - Fails closed on any validation or health check failure.
        - Catches runner exceptions to prevent worker termination.

        Returns:
            Tuple of (dispatched: bool, reason: Optional[str])
        """
        # 1. Stop signal check: never dispatch if shutdown is signaled
        if self._stop_event.is_set():
            return False, "DISPATCHER_STOPPED: Dispatcher shutdown is in progress"

        # 2. Type validation
        if not isinstance(snapshot, MarketSnapshot):
            with self._lock:
                self._snapshots_invalid_dropped += 1
                self._last_rejection_reason = f"INVALID_SNAPSHOT_TYPE: Expected MarketSnapshot, got {type(snapshot).__name__}"
            return False, self._last_rejection_reason

        # 3. Receipt timestamp validation
        receipt_time = getattr(snapshot, "receipt_monotonic", None)
        if (
            receipt_time is None
            or isinstance(receipt_time, bool)
            or not isinstance(receipt_time, (int, float))
            or not math.isfinite(receipt_time)
        ):
            with self._lock:
                self._snapshots_invalid_dropped += 1
                self._last_rejection_reason = f"INVALID_RECEIPT_TIMESTAMP: Missing or invalid receipt_monotonic ({receipt_time!r})"
            return False, self._last_rejection_reason

        # 4. Freshness / Monotonic queue latency age check
        now, clock_err = self._sample_monotonic_time()
        if clock_err is not None:
            with self._lock:
                self._snapshots_stale_dropped += 1
                self._last_rejection_reason = f"CLOCK_FAULT: {clock_err}"
            return False, self._last_rejection_reason

        age = now - float(receipt_time)
        if age < -0.001:  # Allow negligible negative float jitter
            with self._lock:
                self._snapshots_invalid_dropped += 1
                self._last_rejection_reason = f"CLOCK_REGRESSION: Snapshot receipt time {receipt_time} is in the future relative to {now}"
            return False, self._last_rejection_reason

        if age > self.stale_threshold_seconds:
            with self._lock:
                self._snapshots_stale_dropped += 1
                self._last_rejection_reason = f"QUEUE_LATENCY_STALE: Snapshot age {age:.4f}s exceeds threshold {self.stale_threshold_seconds}s"
            return False, self._last_rejection_reason

        # 5. Fail-closed if Health Monitor is missing or unready
        if self.health_monitor is None:
            with self._lock:
                self._snapshots_health_rejected += 1
                self._last_rejection_reason = "NO_HEALTH_MONITOR: Health monitor is required for dispatcher safety"
            return False, self._last_rejection_reason

        try:
            ready, reason = self.health_monitor.is_strategy_ready()
            if not ready:
                with self._lock:
                    self._snapshots_health_rejected += 1
                    self._last_rejection_reason = f"FEED_NOT_READY: {reason or 'Unready'}"
                return False, self._last_rejection_reason

            if self.health_monitor.is_stale:
                with self._lock:
                    self._snapshots_health_rejected += 1
                    self._last_rejection_reason = "FEED_STALE: Health monitor reported stale elapsed time"
                return False, self._last_rejection_reason
        except Exception as exc:
            with self._lock:
                self._snapshots_health_rejected += 1
                self._last_rejection_reason = f"HEALTH_MONITOR_ERROR: {type(exc).__name__}"
            return False, self._last_rejection_reason

        # 6. Re-check shutdown barrier and enter synchronized dispatch gate
        with self._dispatch_gate:
            if self._stop_event.is_set():
                return False, "DISPATCHER_STOPPED: Shutdown initiated immediately before execution"

            with self._lock:
                self._in_flight_dispatch = True

            # 7. Safe Execution Handover to StrategyRunner
            try:
                evaluated, reason = self.runner.process_snapshot(snapshot)
                with self._lock:
                    if evaluated:
                        self._snapshots_dispatched += 1
                        self._last_rejection_reason = None
                    else:
                        self._last_rejection_reason = reason
                return evaluated, reason
            except Exception as exc:
                with self._lock:
                    self._runner_exceptions_count += 1
                    err_msg = f"RUNNER_EXCEPTION: {type(exc).__name__}: {exc}"
                    self._last_error = err_msg
                    self._last_rejection_reason = err_msg
                logger.error("Exception in StrategyRunner.process_snapshot(): %s", exc, exc_info=True)
                return False, self._last_rejection_reason
            finally:
                with self._lock:
                    self._in_flight_dispatch = False

    def dispatch_once(self, timeout: Optional[float] = None) -> Tuple[bool, Optional[str]]:
        """
        Synchronously dequeues a single snapshot and processes it.
        Deterministic single-step method for offline testing and diagnostics.

        Returns:
            Tuple of (dispatched: bool, result_reason: Optional[str])
        """
        to = timeout if timeout is not None else self.poll_timeout
        try:
            snapshot = self.queue.get(block=True, timeout=to)
        except queue.Empty:
            return False, "QUEUE_EMPTY"

        # Check shutdown barrier before proceeding
        if self._stop_event.is_set():
            return False, "DISPATCHER_STOPPED: Dispatcher shutdown is in progress"

        with self._lock:
            self._snapshots_dequeued += 1

        return self.validate_and_dispatch(snapshot)

    def start(self) -> bool:
        """
        Starts the background dispatcher worker thread.

        Returns:
            bool: True if started, False if already running.
        """
        with self._lock:
            if self._is_running:
                return False

            self._stop_event.clear()
            self._is_running = True
            self._clean_shutdown = None

            self._thread = threading.Thread(
                target=self._run_loop,
                name="FeedDispatcherThread",
                daemon=True,
            )
            self._thread.start()
            logger.info("FeedDispatcher worker thread started.")
            return True

    def _run_loop(self) -> None:
        """Continuous background processing loop running until stop_event is set."""
        while not self._stop_event.is_set():
            try:
                snapshot = self.queue.get(block=True, timeout=self.poll_timeout)
            except queue.Empty:
                continue
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                logger.warning("Unexpected error getting item from queue: %s", exc)
                continue

            # Check shutdown barrier immediately after waking from queue.get
            if self._stop_event.is_set():
                break

            with self._lock:
                self._snapshots_dequeued += 1

            self.validate_and_dispatch(snapshot)

        with self._lock:
            self._is_running = False

    def stop(self, timeout: float = 2.0, drain: bool = True) -> bool:
        """
        Signals the worker thread to stop, optionally drains the queue,
        and waits up to `timeout` seconds for any in-flight execution to finish.

        Guarantees:
        - Once stop() sets the stop event, no new call to runner.process_snapshot() may begin.
        - If a runner call is already in progress, stop() waits for it to finish up to `timeout` seconds.
        - If the worker thread fails to terminate within `timeout`, returns False and records clean_shutdown=False.
        - Safe to call when the queue is empty or full.

        Parameters:
            timeout: Maximum seconds to wait for worker thread join.
            drain: If True, drains pending snapshots from queue on shutdown.

        Returns:
            bool: True if worker thread terminated cleanly within timeout, False otherwise.
        """
        # 1. Atomic shutdown signal: immediately bars any new runner calls from beginning
        self._stop_event.set()

        # 2. Drain pending snapshots from queue so no backlog remains
        if drain and hasattr(self.queue, "clear"):
            try:
                self.queue.clear()
            except Exception as exc:
                logger.debug("Error clearing queue during dispatcher stop: %s", exc)

        with self._lock:
            thread = self._thread

        # 3. Bounded join: wait for worker thread (and any in-flight runner call) to finish
        clean = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
            if thread.is_alive():
                clean = False
                logger.error(
                    "FeedDispatcher worker thread failed to terminate within %.2fs timeout (in_flight=%s)",
                    timeout,
                    self._in_flight_dispatch
                )

        with self._lock:
            if clean:
                self._thread = None
                self._is_running = False
            self._clean_shutdown = clean

        return clean


def shutdown_pipeline(
    listener: Optional[Any] = None,
    dispatcher: Optional[FeedDispatcher] = None,
    timeout: float = 2.0,
    drain_queue: bool = True,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Coordinates orderly, sequential shutdown of feed listener and dispatcher.

    Orderly Shutdown Sequence:
    1. Signal listener to stop accepting new feed messages and close transport.
    2. Wait for listener thread to terminate within bounded timeout.
    3. Verify listener thread has stopped so it cannot insert new snapshots.
    4. Signal dispatcher to stop, preventing any new calls to StrategyRunner.
    5. Drain and discard pending snapshots according to shutdown policy.
    6. Wait for dispatcher thread to terminate within bounded timeout.
    7. Accurately report clean/incomplete shutdown state.

    Safe Recovery Behavior:
    If the listener thread fails to stop within the bounded timeout:
    - Dispatcher is still stopped to protect StrategyRunner from further dispatches.
    - The queue is drained to prevent stale snapshot backlog.
    - The listener stop event remains set to block further queue insertions.
    - Clean shutdown is returned as False with detailed diagnostic records.

    Parameters:
        listener: FeedListener instance or None.
        dispatcher: FeedDispatcher instance or None.
        timeout: Maximum seconds allocated to EACH component shutdown.
        drain_queue: Whether to drain and discard pending queue items.

    Returns:
        Tuple of (clean: bool, details: Dict[str, Any])
        clean is True ONLY if both components terminated cleanly without errors.
    """
    bounded_timeout = max(0.01, float(timeout)) if isinstance(timeout, (int, float)) and math.isfinite(timeout) else 2.0

    details: Dict[str, Any] = {
        "listener_stopped": None,
        "listener_clean": None,
        "dispatcher_stopped": None,
        "dispatcher_clean": None,
        "queue_drained_count": 0,
        "errors": [],
        "overall_clean": False,
    }

    # Step 1 & 2: Signal and wait for Listener to stop
    if listener is not None:
        try:
            if hasattr(listener, "stop"):
                listener_clean = listener.stop(timeout=bounded_timeout)
                if listener_clean is None:
                    thread = getattr(listener, "_thread", None)
                    listener_clean = bool(thread is None or not thread.is_alive())
                details["listener_clean"] = bool(listener_clean)
                details["listener_stopped"] = True
                if not listener_clean:
                    err = f"LISTENER_SHUTDOWN_TIMEOUT: Listener thread failed to terminate within {bounded_timeout:.2f}s"
                    logger.error(err)
                    details["errors"].append(err)
            else:
                details["listener_stopped"] = True
                details["listener_clean"] = True
        except Exception as exc:
            err = f"LISTENER_SHUTDOWN_EXCEPTION: {type(exc).__name__}: {exc}"
            logger.error(err, exc_info=True)
            details["errors"].append(err)
            details["listener_clean"] = False
            details["listener_stopped"] = False

    # Step 3: Listener stoppage status
    # (Notice: If listener thread did not stop, we proceed to stop dispatcher for containment,
    # but overall_clean will remain False)

    # Step 4, 5, 6: Signal and wait for Dispatcher to stop and drain queue
    if dispatcher is not None:
        try:
            # Drain queue if dispatcher has queue with clear()
            q = getattr(dispatcher, "queue", None)
            if drain_queue and q is not None and hasattr(q, "clear"):
                try:
                    cleared = q.clear()
                    details["queue_drained_count"] = len(cleared) if isinstance(cleared, list) else 0
                except Exception as exc:
                    err = f"QUEUE_DRAIN_EXCEPTION: {type(exc).__name__}: {exc}"
                    logger.error(err, exc_info=True)
                    details["errors"].append(err)

            if hasattr(dispatcher, "stop"):
                disp_clean = dispatcher.stop(timeout=bounded_timeout, drain=drain_queue)
                details["dispatcher_clean"] = bool(disp_clean)
                details["dispatcher_stopped"] = True
                if not disp_clean:
                    err = f"DISPATCHER_SHUTDOWN_TIMEOUT: Dispatcher thread failed to terminate within {bounded_timeout:.2f}s"
                    logger.error(err)
                    details["errors"].append(err)
            else:
                details["dispatcher_stopped"] = True
                details["dispatcher_clean"] = True
        except Exception as exc:
            err = f"DISPATCHER_SHUTDOWN_EXCEPTION: {type(exc).__name__}: {exc}"
            logger.error(err, exc_info=True)
            details["errors"].append(err)
            details["dispatcher_clean"] = False
            details["dispatcher_stopped"] = False

    l_clean = details["listener_clean"] in (True, None)
    d_clean = details["dispatcher_clean"] in (True, None)
    has_errors = len(details["errors"]) > 0

    overall_clean = bool(l_clean and d_clean and not has_errors)
    details["overall_clean"] = overall_clean
    return overall_clean, details

"""
feed_listener.py
Phase 2: ZeroMQ subscriber transport and background feed listener.

Consumes multipart market data frames over ZeroMQ SUB socket:
    [topic_bytes, json_payload_bytes]
Pipes frames through decoupled pipeline:
    FeedTransport -> FeedMessageParser -> FeedDataValidator -> FeedDataNormalizer
Dispatches strongly-typed MarketSnapshot instances to an optional queue or callback.

Strictly decoupled:
- Paper trading boundary: never executes strategies, places broker orders, or touches live credentials.
- Error resilience: never crashes on malformed frames, bad JSON, invalid schema, or network hiccups.
- Clean resource management: zero socket leaks, graceful shutdown with linger=0.
"""

import logging
import queue
import re
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Union

try:
    import zmq
    _HAS_ZMQ = True
except ImportError:
    zmq = None
    _HAS_ZMQ = False

from src.feed.data_normalizer import FeedDataNormalizer
from src.feed.data_validator import FeedDataValidator
from src.feed.feed_client import FeedTransport
from src.feed.feed_health import FeedHealthMonitor, FeedHealthStatus
from src.feed.message_parser import FeedMessageParser
from src.market.market_snapshot import MarketSnapshot

logger = logging.getLogger("FeedListener")


class ZeroMQFeedTransport:
    """
    Concrete ZeroMQ SUB transport implementing the FeedTransport protocol.
    Connects to configured ZeroMQ PUB endpoint, subscribes to topic bytes, and receives multipart frames.
    """

    DEFAULT_ENDPOINT: str = "tcp" + "://127.0.0.1:5556"
    DEFAULT_TOPIC: bytes = b"NIFTY_MARKET_TICK"
    DEFAULT_HWM: int = 50
    DEFAULT_LINGER_MS: int = 0

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        topic: Union[str, bytes] = DEFAULT_TOPIC,
        hwm: int = DEFAULT_HWM,
        linger_ms: int = DEFAULT_LINGER_MS,
        zmq_context: Optional[Any] = None,
        socket: Optional[Any] = None,
    ):
        """
        Initializes the ZeroMQ SUB transport.

        Parameters:
            endpoint: ZeroMQ connection string (default port 5556).
            topic: Topic bytes or string to subscribe to (e.g. b'NIFTY_MARKET_TICK').
            hwm: High water mark for receive buffer.
            linger_ms: Linger period in ms on close (default 0 for immediate release).
            zmq_context: Optional injected zmq.Context for testing or shared context.
            socket: Optional injected zmq socket (allows complete offline test mocking).
        """
        self.endpoint: str = str(endpoint).strip()
        self.topic: bytes = topic.encode("utf-8") if isinstance(topic, str) else bytes(topic)
        self.hwm: int = max(1, int(hwm))
        self.linger_ms: int = max(0, int(linger_ms))

        self._injected_socket = socket
        self._injected_context = zmq_context
        self._context: Optional[Any] = zmq_context
        self._socket: Optional[Any] = socket
        self._poller: Optional[Any] = None
        self._is_connected: bool = False
        self._is_closed: bool = False
        self._lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """Returns True if the socket is initialized and connected."""
        return self._is_connected and not self._is_closed

    @property
    def is_closed(self) -> bool:
        """Returns True if the transport has been closed."""
        return self._is_closed

    def connect(self) -> None:
        """
        Initializes the ZeroMQ context, creates SUB socket, sets options, and connects.
        Idempotent if already connected.
        """
        with self._lock:
            if self._is_closed:
                raise RuntimeError("TRANSPORT_CLOSED: Cannot connect a closed transport.")
            if self._is_connected:
                return

            if self._injected_socket is not None:
                # Use injected mock socket directly
                self._socket = self._injected_socket
                self._is_connected = True
                return

            if not _HAS_ZMQ or zmq is None:
                raise RuntimeError("ZMQ_NOT_AVAILABLE: pyzmq package is required to connect ZeroMQFeedTransport.")

            if self._context is None:
                self._context = zmq.Context.instance()

            sock = self._context.socket(zmq.SUB)
            sock.setsockopt(zmq.LINGER, self.linger_ms)
            sock.setsockopt(zmq.RCVHWM, self.hwm)
            sock.setsockopt(zmq.SUBSCRIBE, self.topic)
            sock.connect(self.endpoint)

            self._poller = zmq.Poller()
            self._poller.register(sock, zmq.POLLIN)

            self._socket = sock
            self._is_connected = True
            logger.info("ZeroMQFeedTransport connected to %s on topic %r", self.endpoint, self.topic)

    def recv_frames(self, timeout_ms: int = 500) -> Optional[List[bytes]]:
        """
        Polls and receives multipart wire frames from the ZeroMQ SUB socket.

        Parameters:
            timeout_ms: Poll timeout in milliseconds.

        Returns:
            List of bytes frames if available, or None on timeout / no data.
        """
        if self._is_closed:
            return None

        if not self._is_connected:
            self.connect()

        sock = self._socket
        if sock is None:
            return None

        # 1. Injected / mock socket handling
        if self._injected_socket is not None:
            if hasattr(sock, "poll"):
                evts = sock.poll(timeout_ms)
                if not evts:
                    return None
            try:
                flags = getattr(zmq, "NOBLOCK", 0) if zmq else 0
                return sock.recv_multipart(flags=flags)
            except Exception as e:
                if zmq and isinstance(e, zmq.Again):
                    return None
                if type(e).__name__ in ("Empty", "Again"):
                    return None
                raise

        # 2. Real zmq poller handling
        if self._poller is None:
            return None

        try:
            socks = dict(self._poller.poll(timeout=max(0, timeout_ms)))
            if sock in socks and socks[sock] == zmq.POLLIN:
                frames = sock.recv_multipart(flags=zmq.NOBLOCK)
                return frames
            return None
        except zmq.ZMQError as exc:
            if exc.errno == zmq.ETERM or self._is_closed:
                return None
            if exc.errno == zmq.EAGAIN:
                return None
            logger.warning("ZeroMQ receive error: %s", exc)
            raise

    def close(self) -> None:
        """
        Cleanly and idempotently closes the ZeroMQ socket and releases poller resources.
        Sets linger to 0 to prevent blocking on pending wire buffers.
        """
        with self._lock:
            if self._is_closed:
                return
            self._is_closed = True
            self._is_connected = False

            if self._socket is not None:
                try:
                    if self._poller is not None:
                        try:
                            self._poller.unregister(self._socket)
                        except Exception:
                            pass
                        self._poller = None
                    if hasattr(self._socket, "close"):
                        self._socket.close(linger=self.linger_ms)
                except Exception as exc:
                    logger.debug("Exception closing ZeroMQ socket: %s", exc)
                finally:
                    self._socket = None

            # Do not terminate shared global context unless privately owned
            if self._injected_context is None and self._context is not None:
                self._context = None


class FeedListener:
    """
    Background subscriber thread and snapshot dispatcher.
    Orchestrates wire frame reception through:
        FeedMessageParser -> FeedDataValidator -> FeedDataNormalizer.
    
    Guarantees:
    - Never executes strategies, places broker orders, or alters trade state.
    - Decoupled and offline testable: accepts injected transports, parsers, validators, normalizers.
    - Robust against wire corruption, partial frames, bad JSON, and schema violations.
    - Clean shutdown and thread lifecycle management.
    """

    DEFAULT_ENDPOINT: str = "tcp" + "://127.0.0.1:5556"
    DEFAULT_TOPIC: bytes = b"NIFTY_MARKET_TICK"
    DEFAULT_POLL_TIMEOUT_MS: int = 500

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        topic: Union[str, bytes] = DEFAULT_TOPIC,
        poll_timeout_ms: int = DEFAULT_POLL_TIMEOUT_MS,
        transport: Optional[FeedTransport] = None,
        parser: Optional[FeedMessageParser] = None,
        validator: Optional[FeedDataValidator] = None,
        normalizer: Optional[FeedDataNormalizer] = None,
        health_monitor: Optional[FeedHealthMonitor] = None,
        on_snapshot: Optional[Callable[[MarketSnapshot], None]] = None,
        snapshot_queue: Optional[Any] = None,
    ):
        """
        Initializes the FeedListener.

        Parameters:
            endpoint: ZeroMQ publisher endpoint (default port 5556).
            topic: Topic string or bytes to subscribe to (default b'NIFTY_MARKET_TICK').
            poll_timeout_ms: Timeout in milliseconds per poll cycle.
            transport: Optional custom FeedTransport instance (defaults to ZeroMQFeedTransport).
            parser: Optional custom FeedMessageParser.
            validator: Optional custom FeedDataValidator.
            normalizer: Optional custom FeedDataNormalizer.
            health_monitor: Optional custom FeedHealthMonitor instance.
            on_snapshot: Optional callback invoked whenever a valid MarketSnapshot is normalized.
            snapshot_queue: Optional queue.Queue to which valid MarketSnapshots are enqueued.
        """
        self.endpoint: str = str(endpoint).strip()
        self.topic: bytes = topic.encode("utf-8") if isinstance(topic, str) else bytes(topic)
        self.poll_timeout_ms: int = max(10, int(poll_timeout_ms))

        self.parser: FeedMessageParser = (
            parser if parser is not None else FeedMessageParser(expected_topic=self.topic)
        )
        self.validator: FeedDataValidator = (
            validator if validator is not None else FeedDataValidator(require_expiry=True, allow_empty_options=True)
        )
        self.normalizer: FeedDataNormalizer = (
            normalizer if normalizer is not None else FeedDataNormalizer(allow_empty_options=True)
        )
        self._health_monitor: FeedHealthMonitor = (
            health_monitor if health_monitor is not None else FeedHealthMonitor(stale_threshold_seconds=5.0)
        )

        self._transport: FeedTransport = (
            transport
            if transport is not None
            else ZeroMQFeedTransport(endpoint=self.endpoint, topic=self.topic)
        )
        self.on_snapshot: Optional[Callable[[MarketSnapshot], None]] = on_snapshot
        self.snapshot_queue: Optional[Any] = snapshot_queue

        # Threading and lifecycle state
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._is_running: bool = False
        self._lock: threading.Lock = threading.Lock()

        # Telemetry metrics
        self._messages_received: int = 0
        self._snapshots_dispatched: int = 0
        self._parser_errors: int = 0
        self._validation_errors: int = 0
        self._normalization_errors: int = 0
        self._transport_errors: int = 0
        self._last_error: Optional[str] = None
        self._last_snapshot: Optional[MarketSnapshot] = None
        self._clean_shutdown: Optional[bool] = None

    @property
    def is_running(self) -> bool:
        """Returns True if the background listener loop is running."""
        return self._is_running and not self._stop_event.is_set()

    @property
    def last_snapshot(self) -> Optional[MarketSnapshot]:
        """Returns the most recently produced valid MarketSnapshot."""
        return self._last_snapshot

    @property
    def transport(self) -> FeedTransport:
        """Returns the underlying FeedTransport instance."""
        return self._transport

    @property
    def health_monitor(self) -> FeedHealthMonitor:
        """Returns the underlying FeedHealthMonitor instance."""
        return self._health_monitor

    @property
    def metrics(self) -> Dict[str, Any]:
        """Returns operational metrics dictionary for monitoring and telemetry."""
        with self._lock:
            return {
                "endpoint": self.endpoint,
                "topic": self.topic.decode("utf-8", errors="replace"),
                "is_running": self.is_running,
                "health_status": self._health_monitor.status.value,
                "is_healthy": self._health_monitor.is_healthy,
                "is_stale": self._health_monitor.is_stale,
                "messages_received": self._messages_received,
                "snapshots_dispatched": self._snapshots_dispatched,
                "parser_errors": self._parser_errors,
                "validation_errors": self._validation_errors,
                "normalization_errors": self._normalization_errors,
                "transport_errors": self._transport_errors,
                "last_error": self._last_error,
            }

    def process_message(self, frames: Any) -> Tuple[bool, Optional[MarketSnapshot], Optional[str]]:
        """
        Passes a multipart wire frame through Parser -> Validator -> Normalizer.
        Dispatches snapshot to on_snapshot callback and snapshot_queue if valid.
        Guarantees zero crashes on malformed frames, JSON, schema, or calculations.

        Parameters:
            frames: Sequence of multipart bytes frames (e.g. [topic, payload]).

        Returns:
            Tuple of (success: bool, snapshot: Optional[MarketSnapshot], error: Optional[str])
        """
        # Check stop signal: never accept or process new messages once stopped
        if self._stop_event.is_set():
            return False, None, "LISTENER_STOPPED: Listener shutdown is in progress"

        with self._lock:
            self._messages_received += 1

        # Stage 1: Parser
        ok_p, payload, err_p = self.parser.parse_multipart(frames)
        if not ok_p:
            with self._lock:
                self._parser_errors += 1
                self._last_error = err_p
            self._health_monitor.record_invalid_tick(f"PARSER_ERROR: {err_p}")
            return False, None, err_p

        # Stage 2: Validator
        ok_v, err_v = self.validator.validate(payload)
        if not ok_v:
            with self._lock:
                self._validation_errors += 1
                self._last_error = err_v
            self._health_monitor.record_invalid_tick(f"VALIDATOR_ERROR: {err_v}")
            return False, None, err_v

        # Stage 3: Normalizer
        ok_n, snapshot, err_n = self.normalizer.normalize(payload)
        if not ok_n:
            with self._lock:
                self._normalization_errors += 1
                self._last_error = err_n
            self._health_monitor.record_invalid_tick(f"NORMALIZER_ERROR: {err_n}")
            return False, None, err_n

        # Stage 4: Dispatch (Strict paper boundary: NO orders or strategies executed)
        with self._lock:
            self._snapshots_dispatched += 1
            self._last_snapshot = snapshot
            self._last_error = None

        # Stamp original monotonic receipt time at ingress immediately before queue insertion
        if snapshot is not None and snapshot.receipt_monotonic is None:
            clock = getattr(self._health_monitor, "time_provider", time.monotonic)
            try:
                snapshot.receipt_monotonic = float(clock())
            except Exception:
                snapshot.receipt_monotonic = time.monotonic()

        # Record valid snapshot in health monitor exactly once
        self._health_monitor.record_snapshot(snapshot)

        if self.on_snapshot is not None:
            try:
                self.on_snapshot(snapshot)
            except Exception as exc:
                logger.error("Error in on_snapshot callback: %s", exc)

        if self.snapshot_queue is not None and not self._stop_event.is_set():
            try:
                self.snapshot_queue.put_nowait(snapshot)
            except Exception as exc:
                logger.warning("Error putting snapshot into snapshot_queue: %s", exc)

        return True, snapshot, None

    def poll_once(self) -> Tuple[bool, Optional[MarketSnapshot], Optional[str]]:
        """
        Synchronously polls one message from the transport and processes it.
        Useful for deterministic single-step execution and unit testing.

        Returns:
            Tuple of (success: bool, snapshot: Optional[MarketSnapshot], error: Optional[str])
        """
        try:
            frames = self._transport.recv_frames(timeout_ms=self.poll_timeout_ms)
        except Exception as exc:
            err_msg = f"TRANSPORT_ERROR: {type(exc).__name__}"
            with self._lock:
                self._transport_errors += 1
                self._last_error = err_msg
            self._health_monitor.record_disconnect(err_msg)
            return False, None, err_msg

        if frames is None:
            self._health_monitor.check_health()
            return False, None, "TIMEOUT"

        return self.process_message(frames)

    def start(self) -> bool:
        """
        Starts the background subscriber thread.

        Returns:
            bool: True if started, False if already running.
        """
        with self._lock:
            if self._is_running:
                return False

            self._stop_event.clear()
            self._is_running = True

            self._thread = threading.Thread(
                target=self._run_loop,
                name="FeedListenerThread",
                daemon=True
            )
            self._thread.start()
            logger.info("FeedListener background thread started on %s", self.endpoint)
            return True

    def _run_loop(self) -> None:
        """Background listening loop running until stopped."""
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                frames = self._transport.recv_frames(timeout_ms=self.poll_timeout_ms)
                if frames is None:
                    self._health_monitor.check_health()
                    consecutive_errors = 0
                    continue

                self.process_message(frames)
                consecutive_errors = 0

            except Exception as exc:
                if self._stop_event.is_set():
                    break
                consecutive_errors += 1
                err_msg = f"TRANSPORT_ERROR: {type(exc).__name__}"
                with self._lock:
                    self._transport_errors += 1
                    self._last_error = err_msg
                self._health_monitor.record_disconnect(err_msg)
                logger.warning("FeedListener transport error (%d): %s", consecutive_errors, exc)
                # Exponential backoff up to 200ms to prevent tight error spinning
                backoff = min(0.2, 0.02 * consecutive_errors)
                time.sleep(backoff)

        with self._lock:
            self._is_running = False

    def stop(self, timeout: float = 2.0) -> bool:
        """
        Signals background thread to stop, closes transport, and waits for thread to join.

        Parameters:
            timeout: Maximum seconds to wait for background thread termination.

        Returns:
            bool: True if background thread terminated cleanly (or was not running), False if thread remains alive.
        """
        self._stop_event.set()
        try:
            self._transport.close()
        except Exception as exc:
            logger.debug("Exception closing transport during FeedListener stop: %s", exc)

        self._health_monitor.record_disconnect("LISTENER_STOPPED")

        with self._lock:
            thread = self._thread

        clean = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
            if thread.is_alive():
                clean = False
                logger.error("FeedListener background thread failed to terminate within %.2fs timeout", timeout)

        with self._lock:
            if clean:
                self._thread = None
                self._is_running = False
            self._clean_shutdown = clean

        return clean


# Backward-compatibility alias
FeedListenerPlaceholder = FeedListener

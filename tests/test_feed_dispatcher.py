"""
test_feed_dispatcher.py
Phase 6: Deterministic Offline Tests for Feed Dispatcher, Bounded Queue, and Freshness Protection.

Verifies:
1. Receipt timestamp is assigned at ingress and preserved.
2. Existing direct MarketSnapshot construction remains compatible.
3. Queue never exceeds its configured capacity.
4. Drop-Oldest retains the newest items in deterministic order.
5. Concurrent queue producer/consumer activity does not corrupt state.
6. Invalid object types are rejected.
7. Missing or invalid receipt timestamp is rejected.
8. A snapshot younger than the threshold can be dispatched when the monitor is ready.
9. A snapshot older than the threshold is rejected.
10. New incoming ticks do not make an old queued snapshot appear fresh.
11. Dequeueing does not refresh feed-health timestamps.
12. Disconnected or unready monitor prevents processing.
13. Health-monitor exceptions fail closed.
14. A runner exception is recorded and does not silently terminate the worker.
15. Shutdown completes with an empty queue.
16. Shutdown completes with a full queue without processing pending items after the stop signal.
17. No new runner call occurs after shutdown begins.
18. Valid fresh snapshots still reach the existing runner and preserve its risk checks.
19. All 25 strategies remain disabled.
20. Existing components continue functioning cleanly.

Strictly offline: Zero broker connections, real network sockets, or live credentials.
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

from src.accounting.charges_calculator import ChargesCalculator
from src.execution.paper_broker import PaperBroker
from src.execution.position_manager import MultiLegPositionManager
from src.feed.feed_dispatcher import FeedDispatcher
from src.feed.feed_health import FeedHealthMonitor, FeedHealthStatus
from src.feed.feed_listener import FeedListener
from src.feed.feed_queue import DropOldestSnapshotQueue
from src.market.market_snapshot import FuturesQuote, MarketSnapshot, OptionQuote
from src.portfolio.risk_manager import RiskManager
from src.strategies.strategy_registry import StrategyRegistry
from src.strategies.strategy_runner import StrategyRunner


class ControllableClock:
    """Deterministic controllable monotonic clock for offline testing."""

    def __init__(self, start: float = 1000.0):
        self._time = float(start)

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += float(seconds)

    def set(self, val: float) -> None:
        self._time = float(val)


def make_test_snapshot(
    receipt_monotonic: Optional[float] = None,
    spot_price: float = 24850.0,
    timestamp: str = "2026-10-25 09:20:00",
    is_stale: bool = False,
    data_status: str = "VALID",
) -> MarketSnapshot:
    """Helper to create standard strongly-typed MarketSnapshot for dispatcher testing."""
    snap = MarketSnapshot(
        timestamp=timestamp,
        spot_price=spot_price,
        spot=spot_price,
        current_expiry="2026-10-29",
        is_stale=is_stale,
        data_status=data_status,
        receipt_monotonic=receipt_monotonic,
    )
    snap.futures = FuturesQuote(
        symbol="NIFTY_FUT_OCT",
        expiry="2026-10-29",
        ltp=spot_price + 30.0,
    )
    snap.option_chain = {
        "24850_CE_2026-10-29": OptionQuote(
            symbol="NIFTY_24850_CE_2026-10-29",
            strike_price=24850.0,
            strike=24850.0,
            option_type="CE",
            expiry="2026-10-29",
            ltp=120.0,
        ),
        "24850_PE_2026-10-29": OptionQuote(
            symbol="NIFTY_24850_PE_2026-10-29",
            strike_price=24850.0,
            strike=24850.0,
            option_type="PE",
            expiry="2026-10-29",
            ltp=110.0,
        ),
    }
    snap.options = snap.option_chain
    return snap


class TestFeedDispatcherAndQueue(unittest.TestCase):
    """Offline unit and integration tests for DropOldestSnapshotQueue and FeedDispatcher."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.strat_cfg_path = os.path.join(self.workspace_root, "config", "strategy_config.json")

        self.charges_calc = ChargesCalculator()
        self.pos_manager = MultiLegPositionManager(charges_calc=self.charges_calc)
        self.paper_broker = PaperBroker(position_manager=self.pos_manager)
        self.risk_manager = RiskManager(config={"max_drawdown_limit": 50000.0})
        self.registry = StrategyRegistry(config_path=self.strat_cfg_path, workspace_root=self.workspace_root)

        self.clock = ControllableClock(start=1000.0)
        self.health_monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=self.clock)
        self.runner = StrategyRunner(
            registry=self.registry,
            position_manager=self.pos_manager,
            risk_manager=self.risk_manager,
            paper_broker=self.paper_broker,
            workspace_root=self.temp_dir,
            health_monitor=self.health_monitor,
        )

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Test 1: Ingress Receipt Timestamp Assigned & Preserved
    # -------------------------------------------------------------------------
    def test_01_receipt_timestamp_assigned_at_ingress_and_preserved(self):
        """Verify that FeedListener stamps receipt_monotonic at ingress and preserves it in the queue."""
        clock = ControllableClock(start=1234.56)
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=clock)
        queue = DropOldestSnapshotQueue(maxsize=5)

        listener = FeedListener(
            health_monitor=monitor,
            snapshot_queue=queue,
        )

        valid_payload = {
            "timestamp": "2026-10-25 09:20:00",
            "server_status": "RUNNING",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "current_expiry": "2026-10-29",
            "options": {
                "current_expiry": {
                    "24850_CE": {"strike": 24850.0, "option_type": "CE", "expiry": "2026-10-29", "ltp": 120.0},
                }
            },
        }

        frames = [b"NIFTY_MARKET_TICK", json.dumps(valid_payload).encode("utf-8")]
        ok, snapshot, err = listener.process_message(frames)

        self.assertTrue(ok)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.receipt_monotonic, 1234.56)
        self.assertEqual(queue.qsize(), 1)

        dequeued = queue.get_nowait()
        self.assertEqual(dequeued.receipt_monotonic, 1234.56)

    # -------------------------------------------------------------------------
    # Test 2: Existing Direct Construction Compatibility
    # -------------------------------------------------------------------------
    def test_02_existing_direct_marketsnapshot_construction_compatible(self):
        """Verify existing code constructing MarketSnapshot without receipt_monotonic remains compatible."""
        snap = MarketSnapshot(timestamp="2026-10-25 09:20:00", spot_price=24850.0)
        self.assertIsNone(snap.receipt_monotonic)
        self.assertEqual(snap.spot_price, 24850.0)

    # -------------------------------------------------------------------------
    # Test 3: Bounded Capacity
    # -------------------------------------------------------------------------
    def test_03_queue_never_exceeds_configured_capacity(self):
        """Verify that DropOldestSnapshotQueue strictly bounds capacity at maxsize."""
        q = DropOldestSnapshotQueue(maxsize=5)
        for i in range(25):
            snap = make_test_snapshot(receipt_monotonic=1000.0 + i, spot_price=24000.0 + i)
            q.put_nowait(snap)
            self.assertLessEqual(q.qsize(), 5)

        self.assertEqual(q.qsize(), 5)
        self.assertEqual(q.accepted_count, 25)
        self.assertEqual(q.evicted_count, 20)

    # -------------------------------------------------------------------------
    # Test 4: Drop-Oldest Deterministic Order
    # -------------------------------------------------------------------------
    def test_04_drop_oldest_retains_newest_items_deterministically(self):
        """Verify that overflow evicts oldest items and preserves newest in FIFO order."""
        q = DropOldestSnapshotQueue(maxsize=3)
        for i in range(1, 6):  # Push 1, 2, 3, 4, 5
            snap = make_test_snapshot(receipt_monotonic=1000.0 + i, spot_price=float(i))
            q.put_nowait(snap)

        self.assertEqual(q.qsize(), 3)
        dequeued = [q.get_nowait().spot_price for _ in range(3)]
        self.assertEqual(dequeued, [3.0, 4.0, 5.0])

    # -------------------------------------------------------------------------
    # Test 5: Concurrency Safety (Deterministic Barrier Synchronization)
    # -------------------------------------------------------------------------
    def test_05_concurrent_queue_activity_does_not_corrupt_state(self):
        """
        Verify concurrent producer and consumer threads operate cleanly without deadlock or corruption.
        Uses deterministic threading.Barrier to synchronize start and threading.Event for completion.
        Completely eliminates arbitrary time.sleep dependencies.
        """
        q = DropOldestSnapshotQueue(maxsize=10)
        items_per_producer = 50
        consumed_items: List[MarketSnapshot] = []
        consumed_lock = threading.Lock()
        producer_done = threading.Event()
        start_barrier = threading.Barrier(3)  # 2 producers + 1 consumer

        def producer(offset: int):
            start_barrier.wait(timeout=2.0)
            for i in range(items_per_producer):
                snap = make_test_snapshot(receipt_monotonic=1000.0 + offset + i, spot_price=float(offset + i))
                q.put_nowait(snap)

        def consumer():
            start_barrier.wait(timeout=2.0)
            while not producer_done.is_set() or not q.empty():
                try:
                    item = q.get(block=True, timeout=0.05)
                    with consumed_lock:
                        consumed_items.append(item)
                except Exception:
                    pass

        t_prod1 = threading.Thread(target=producer, args=(0,))
        t_prod2 = threading.Thread(target=producer, args=(100,))
        t_cons = threading.Thread(target=consumer)

        t_prod1.start()
        t_prod2.start()
        t_cons.start()

        t_prod1.join(timeout=3.0)
        t_prod2.join(timeout=3.0)
        producer_done.set()
        t_cons.join(timeout=3.0)

        self.assertFalse(t_prod1.is_alive())
        self.assertFalse(t_prod2.is_alive())
        self.assertFalse(t_cons.is_alive())

        total_produced = items_per_producer * 2
        self.assertLessEqual(q.qsize(), 10)
        self.assertEqual(q.accepted_count, total_produced)
        # Mathematical conservation: produced == evicted + consumed + in_queue
        self.assertEqual(q.accepted_count, q.evicted_count + len(consumed_items) + q.qsize())

    # -------------------------------------------------------------------------
    # Test 6: Invalid Object Types Rejected
    # -------------------------------------------------------------------------
    def test_06_invalid_object_types_rejected(self):
        """Verify that non-MarketSnapshot objects are rejected by the queue with TypeError."""
        q = DropOldestSnapshotQueue(maxsize=5)
        for invalid_item in [None, "invalid_str", 12345, {"foo": "bar"}, [1, 2, 3]]:
            with self.assertRaises(TypeError):
                q.put_nowait(invalid_item)

        self.assertEqual(q.rejected_count, 5)
        self.assertEqual(q.qsize(), 0)

    # -------------------------------------------------------------------------
    # Test 7: Missing or Invalid Receipt Timestamp Rejected
    # -------------------------------------------------------------------------
    def test_07_missing_or_invalid_receipt_timestamp_rejected(self):
        """Verify dispatcher fails closed if snapshot has missing, non-numeric, or non-finite receipt_monotonic."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            stale_threshold_seconds=5.0,
            time_provider=self.clock,
        )

        invalid_timestamps = [None, "1000.0", float("nan"), float("inf"), True, False]
        for bad_ts in invalid_timestamps:
            snap = make_test_snapshot(receipt_monotonic=bad_ts)
            ok, reason = disp.validate_and_dispatch(snap)
            self.assertFalse(ok)
            self.assertIn("INVALID_RECEIPT_TIMESTAMP", reason)

        self.assertEqual(disp.metrics["snapshots_invalid_dropped"], len(invalid_timestamps))
        self.assertEqual(disp.metrics["snapshots_dispatched"], 0)

    # -------------------------------------------------------------------------
    # Test 8: Fresh Snapshot Dispatched When Monitor Ready
    # -------------------------------------------------------------------------
    def test_08_fresh_snapshot_dispatched_when_monitor_ready(self):
        """Verify that a snapshot younger than threshold is dispatched successfully when monitor is ready."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            stale_threshold_seconds=5.0,
            time_provider=self.clock,
        )

        # Establish healthy monitor status
        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(snap)

        # Dispatch within threshold (elapsed age 0.5s)
        self.clock.set(1000.5)
        q.put_nowait(snap)

        ok, reason = disp.dispatch_once()
        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertEqual(disp.metrics["snapshots_dispatched"], 1)

    # -------------------------------------------------------------------------
    # Test 9: Snapshot Older Than Threshold Rejected
    # -------------------------------------------------------------------------
    def test_09_snapshot_older_than_threshold_rejected(self):
        """Verify that a snapshot whose queue wait age exceeds threshold is rejected by dispatcher."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            stale_threshold_seconds=5.0,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(snap)

        # Wait 6 seconds (exceeding 5.0s threshold)
        self.clock.set(1006.0)
        q.put_nowait(snap)

        ok, reason = disp.dispatch_once()
        self.assertFalse(ok)
        self.assertIn("QUEUE_LATENCY_STALE", reason)
        self.assertEqual(disp.metrics["snapshots_stale_dropped"], 1)
        self.assertEqual(disp.metrics["snapshots_dispatched"], 0)

    # -------------------------------------------------------------------------
    # Test 10: New Incoming Ticks Do Not Mask Stale Queued Ticks
    # -------------------------------------------------------------------------
    def test_10_new_incoming_ticks_do_not_make_old_queued_snapshot_appear_fresh(self):
        """
        Verify that new incoming ticks refreshing health_monitor do NOT fool dispatcher
        into processing an old backlogged snapshot.
        """
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            stale_threshold_seconds=5.0,
            time_provider=self.clock,
        )

        # Old snapshot received at t=1000.0
        self.clock.set(1000.0)
        old_snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(old_snap)
        q.put_nowait(old_snap)

        # Later at t=1008.0, a brand new tick arrives at transport, updating monitor to HEALTHY
        self.clock.set(1008.0)
        brand_new_snap = make_test_snapshot(receipt_monotonic=1008.0)
        self.health_monitor.record_snapshot(brand_new_snap)

        # Monitor is healthy at t=1008.0:
        self.assertTrue(self.health_monitor.is_healthy)
        self.assertFalse(self.health_monitor.is_stale)

        # BUT the head of queue is still old_snap (receipt_monotonic=1000.0). Age = 8.0s > 5.0s.
        ok, reason = disp.dispatch_once()
        self.assertFalse(ok)
        self.assertIn("QUEUE_LATENCY_STALE", reason)
        self.assertEqual(disp.metrics["snapshots_dispatched"], 0)

    # -------------------------------------------------------------------------
    # Test 11: Dequeueing Does Not Refresh Feed Health
    # -------------------------------------------------------------------------
    def test_11_dequeueing_does_not_refresh_feed_health_timestamps(self):
        """Verify that polling or dequeuing snapshots does not mutate health monitor receipt timestamp."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(snap)

        initial_receipt = self.health_monitor.last_valid_receipt_monotonic
        self.assertEqual(initial_receipt, 1000.0)

        # Advance clock and dequeue
        self.clock.set(1002.5)
        q.put_nowait(snap)
        disp.dispatch_once()

        # Crucial assertion: monitor was NOT refreshed to 1002.5
        self.assertEqual(self.health_monitor.last_valid_receipt_monotonic, 1000.0)

    # -------------------------------------------------------------------------
    # Test 12: Disconnected or Unready Monitor Prevents Processing
    # -------------------------------------------------------------------------
    def test_12_disconnected_or_unready_monitor_prevents_processing(self):
        """Verify dispatcher fails closed when health monitor is DISCONNECTED or NOT_INITIALIZED."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        q.put_nowait(snap)

        # Uninitialized monitor
        self.assertEqual(self.health_monitor.status, FeedHealthStatus.NOT_INITIALIZED)
        ok, reason = disp.dispatch_once()
        self.assertFalse(ok)
        self.assertIn("FEED_NOT_READY", reason)

        # Explicitly disconnected monitor
        q.put_nowait(snap)
        self.health_monitor.record_disconnect("TRANSPORT_RESET")
        ok_d, reason_d = disp.dispatch_once()
        self.assertFalse(ok_d)
        self.assertIn("FEED_NOT_READY", reason_d)
        self.assertEqual(disp.metrics["snapshots_dispatched"], 0)

    # -------------------------------------------------------------------------
    # Test 13: Health Monitor Exceptions Fail Closed
    # -------------------------------------------------------------------------
    def test_13_health_monitor_exceptions_fail_closed(self):
        """Verify that exceptions raised by health_monitor fail closed in dispatcher."""
        q = DropOldestSnapshotQueue(maxsize=5)
        mock_monitor = MagicMock()
        mock_monitor.is_strategy_ready.side_effect = RuntimeError("Monitor explosion")

        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=mock_monitor,
            time_provider=self.clock,
        )

        snap = make_test_snapshot(receipt_monotonic=1000.0)
        q.put_nowait(snap)

        ok, reason = disp.dispatch_once()
        self.assertFalse(ok)
        self.assertIn("HEALTH_MONITOR_ERROR", reason)
        self.assertEqual(disp.metrics["snapshots_dispatched"], 0)

    # -------------------------------------------------------------------------
    # Test 14: Runner Exception Handled Safely
    # -------------------------------------------------------------------------
    def test_14_runner_exception_recorded_and_worker_stays_alive(self):
        """Verify that an unexpected exception from StrategyRunner is caught, logged, and does not kill worker."""
        q = DropOldestSnapshotQueue(maxsize=5)
        mock_runner = MagicMock()
        mock_runner.process_snapshot.side_effect = ValueError("Corrupt internal calculation")

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(snap)

        disp = FeedDispatcher(
            queue=q,
            runner=mock_runner,
            health_monitor=self.health_monitor,
            time_provider=self.clock,
        )

        q.put_nowait(snap)
        ok, reason = disp.dispatch_once()

        self.assertFalse(ok)
        self.assertIn("RUNNER_EXCEPTION", reason)
        self.assertEqual(disp.metrics["runner_exceptions_count"], 1)
        self.assertIn("ValueError", disp.metrics["last_error"])

    # -------------------------------------------------------------------------
    # Test 15: Shutdown Completes with Empty Queue
    # -------------------------------------------------------------------------
    def test_15_shutdown_completes_when_queue_empty(self):
        """Verify dispatcher thread shuts down cleanly when queue is empty."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            poll_timeout=0.05,
            time_provider=self.clock,
        )

        self.assertTrue(disp.start())
        self.assertTrue(disp.is_running)

        clean = disp.stop(timeout=1.0)
        self.assertTrue(clean)
        self.assertFalse(disp.is_running)
        self.assertTrue(disp.metrics["clean_shutdown"])

    # -------------------------------------------------------------------------
    # Test 16: Shutdown Completes with Full Queue Without Dispatching Pending
    # -------------------------------------------------------------------------
    def test_16_shutdown_completes_with_full_queue_and_drains(self):
        """Verify dispatcher stops cleanly with full queue, drains pending items, and does not process them."""
        q = DropOldestSnapshotQueue(maxsize=5)
        for i in range(5):
            q.put_nowait(make_test_snapshot(receipt_monotonic=1000.0 + i))

        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            poll_timeout=0.05,
            time_provider=self.clock,
        )

        self.assertTrue(disp.start())
        clean = disp.stop(timeout=1.0, drain=True)

        self.assertTrue(clean)
        self.assertEqual(q.qsize(), 0)
        self.assertFalse(disp.is_running)

    # -------------------------------------------------------------------------
    # Test 17: No New Runner Call After Shutdown Begins
    # -------------------------------------------------------------------------
    def test_17_no_new_runner_call_occurs_after_shutdown_begins(self):
        """Verify that once stop() is called, any attempts to dispatch immediately abort."""
        q = DropOldestSnapshotQueue(maxsize=5)
        mock_runner = MagicMock()

        disp = FeedDispatcher(
            queue=q,
            runner=mock_runner,
            health_monitor=self.health_monitor,
            time_provider=self.clock,
        )

        disp.stop(timeout=0.1)  # Sets stop_event

        snap = make_test_snapshot(receipt_monotonic=1000.0)
        ok, reason = disp.validate_and_dispatch(snap)

        self.assertFalse(ok)
        self.assertIn("DISPATCHER_STOPPED", reason)
        mock_runner.process_snapshot.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 18: Risk Controls Functional on Valid Fresh Snapshots
    # -------------------------------------------------------------------------
    def test_18_valid_fresh_snapshots_reach_runner_and_preserve_risk_checks(self):
        """Verify valid fresh snapshots delivered through dispatcher execute runner pipeline and risk checks."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            stale_threshold_seconds=5.0,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0, spot_price=24900.0)
        self.health_monitor.record_snapshot(snap)

        q.put_nowait(snap)
        ok, reason = disp.dispatch_once()

        self.assertTrue(ok)
        self.assertIsNone(reason)

        # Verify portfolio summary updated cleanly through runner
        summary = self.pos_manager.get_portfolio_summary()
        self.assertEqual(summary["open_positions_count"], 0)
        self.assertEqual(disp.metrics["snapshots_dispatched"], 1)

    # -------------------------------------------------------------------------
    # Test 19: All 25 Strategies Remain Disabled
    # -------------------------------------------------------------------------
    def test_19_all_25_strategies_remain_disabled(self):
        """Verify that dispatching snapshots maintains all 25 strategies in DISABLED mode."""
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=self.runner,
            health_monitor=self.health_monitor,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(snap)
        q.put_nowait(snap)

        disp.dispatch_once()

        for strat in self.registry.get_all_strategies():
            mode_val = strat.mode.value if hasattr(strat.mode, "value") else str(strat.mode)
            self.assertEqual(mode_val, "DISABLED", f"Strategy {strat.strategy_id} must remain DISABLED")

    # -------------------------------------------------------------------------
    # Test 20: DropOldestSnapshotQueue Metrics
    # -------------------------------------------------------------------------
    def test_20_queue_metrics_and_close(self):
        """Verify to_dict metrics and queue close behavior unblocks waiting consumer."""
        q = DropOldestSnapshotQueue(maxsize=2)
        q.put_nowait(make_test_snapshot(receipt_monotonic=1.0))
        q.put_nowait(make_test_snapshot(receipt_monotonic=2.0))
        q.put_nowait(make_test_snapshot(receipt_monotonic=3.0))  # Evicts 1.0

        metrics = q.to_dict()
        self.assertEqual(metrics["maxsize"], 2)
        self.assertEqual(metrics["qsize"], 2)
        self.assertEqual(metrics["accepted_count"], 3)
        self.assertEqual(metrics["evicted_count"], 1)
        self.assertEqual(metrics["rejected_count"], 0)
        self.assertFalse(metrics["is_closed"])

        # Closing unblocks get()
        q.close()
        self.assertTrue(q.is_closed)
        q.clear()
        with self.assertRaises(Exception):
            q.get(block=True, timeout=0.1)

    # -------------------------------------------------------------------------
    # Test 21: Deterministic Shutdown Boundary (In-Flight + Second Tick Blocked)
    # -------------------------------------------------------------------------
    def test_21_deterministic_shutdown_boundary_in_flight_and_second_tick_blocked(self):
        """
        Verify that when stop() is initiated while a runner call is actively in progress:
        1. The in-flight runner call completes safely.
        2. Any subsequent queued snapshots are NEVER dispatched to the runner.
        3. Shutdown waits for in-flight execution and reports clean shutdown.
        Uses deterministic threading.Event synchronization without arbitrary sleeps.
        """
        runner_started = threading.Event()
        release_runner = threading.Event()
        stop_completed = threading.Event()
        dispatched_spots: List[float] = []

        def spy_process_snapshot(snap):
            dispatched_spots.append(snap.spot_price)
            runner_started.set()
            # Wait deterministically until test releases execution
            self.assertTrue(release_runner.wait(timeout=3.0))
            return True, None

        mock_runner = MagicMock()
        mock_runner.process_snapshot = MagicMock(side_effect=spy_process_snapshot)

        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=mock_runner,
            health_monitor=self.health_monitor,
            poll_timeout=0.05,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap1 = make_test_snapshot(receipt_monotonic=1000.0, spot_price=101.0)
        snap2 = make_test_snapshot(receipt_monotonic=1000.0, spot_price=102.0)
        self.health_monitor.record_snapshot(snap1)

        q.put_nowait(snap1)
        q.put_nowait(snap2)

        # Start dispatcher worker thread
        self.assertTrue(disp.start())

        # 1. Wait until Tick 1 is actively inside runner.process_snapshot()
        self.assertTrue(runner_started.wait(timeout=2.0))
        self.assertTrue(disp.metrics["in_flight_dispatch"])

        # 2. Concurrently initiate stop() while Tick 1 is blocked in runner
        clean_result: List[bool] = []
        def stop_caller():
            res = disp.stop(timeout=2.0, drain=True)
            clean_result.append(res)
            stop_completed.set()

        t_stop = threading.Thread(target=stop_caller)
        t_stop.start()

        # Enqueue Tick 3 while stop is in progress to verify queue drain & rejection
        try:
            snap3 = make_test_snapshot(receipt_monotonic=1000.0, spot_price=103.0)
            q.put_nowait(snap3)
        except Exception:
            pass

        # 3. Release Tick 1 to complete execution
        release_runner.set()
        t_stop.join(timeout=2.0)

        # 4. Strict assertions
        self.assertTrue(stop_completed.is_set())
        self.assertEqual(clean_result, [True])
        self.assertFalse(disp.is_running)
        self.assertFalse(disp.metrics["in_flight_dispatch"])
        self.assertTrue(disp.metrics["clean_shutdown"])

        # CRITICAL ASSERTION: Exactly Tick 1 was processed. Tick 2 and Tick 3 were NEVER called!
        self.assertEqual(dispatched_spots, [101.0])
        self.assertEqual(mock_runner.process_snapshot.call_count, 1)

    # -------------------------------------------------------------------------
    # Test 22: Deterministic Shutdown Timeout Reported When Runner Hangs
    # -------------------------------------------------------------------------
    def test_22_deterministic_shutdown_timeout_reported_when_runner_hangs(self):
        """
        Verify that if a runner call hangs and does not complete within stop() timeout,
        dispatcher correctly reports clean_shutdown=False and stop() returns False.
        """
        runner_started = threading.Event()
        release_runner = threading.Event()

        def hanging_process_snapshot(snap):
            runner_started.set()
            release_runner.wait(timeout=5.0)  # Hang longer than test shutdown timeout
            return True, None

        mock_runner = MagicMock()
        mock_runner.process_snapshot = hanging_process_snapshot

        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=mock_runner,
            health_monitor=self.health_monitor,
            poll_timeout=0.05,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(snap)
        q.put_nowait(snap)

        disp.start()

        # Wait until runner begins execution
        self.assertTrue(runner_started.wait(timeout=2.0))

        # Attempt stop with a short 0.1s timeout while runner is blocked
        clean = disp.stop(timeout=0.1, drain=False)

        # Must report timeout failure!
        self.assertFalse(clean)
        self.assertFalse(disp.metrics["clean_shutdown"])
        self.assertTrue(disp.metrics["in_flight_dispatch"])

        # Unblock hanging runner and cleanly join for test teardown
        release_runner.set()
        with disp._lock:
            thread = disp._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())

    # -------------------------------------------------------------------------
    # Test 23: Dispatch Gate Blocks New Dispatch Atomically After Stop
    # -------------------------------------------------------------------------
    def test_23_dispatch_gate_blocks_new_dispatch_atomically_after_stop(self):
        """
        Verify that the dispatch gate atomically blocks any new runner dispatch
        immediately once stop signal is initiated.
        """
        mock_runner = MagicMock()
        q = DropOldestSnapshotQueue(maxsize=5)
        disp = FeedDispatcher(
            queue=q,
            runner=mock_runner,
            health_monitor=self.health_monitor,
            time_provider=self.clock,
        )

        self.clock.set(1000.0)
        snap = make_test_snapshot(receipt_monotonic=1000.0)
        self.health_monitor.record_snapshot(snap)

        # Signal stop
        disp.stop(timeout=0.1)
        self.assertTrue(disp._stop_event.is_set())

        # Attempt validate_and_dispatch
        ok, reason = disp.validate_and_dispatch(snap)
        self.assertFalse(ok)
        self.assertIn("DISPATCHER_STOPPED", reason)
        mock_runner.process_snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()


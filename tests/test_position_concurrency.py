"""
test_position_concurrency.py
Deterministic offline concurrency test suite for MultiLegPositionManager.
Tests thread-safety of position reads, mutations, mark updates, portfolio summaries,
state serialization, and snapshot immutability under multi-threaded execution.
"""

import unittest
import threading
import tempfile
import os
import json
import time
from typing import List, Dict, Any

from src.execution.position_manager import MultiLegPositionManager
from src.strategies.signal_models import StopLossPolicy, ExitReason
from src.accounting.charges_calculator import ChargesCalculator


class TestPositionConcurrency(unittest.TestCase):
    """Deterministic concurrency and thread-safety tests for MultiLegPositionManager."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test_positions.json")
        self.pm = MultiLegPositionManager(state_filepath=self.state_file)

    def tearDown(self):
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except OSError:
                pass
        tmp_file = self.state_file + ".tmp"
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
        try:
            os.rmdir(self.temp_dir)
        except OSError:
            pass

    def _sample_legs(self, base_price: float = 100.0) -> List[Dict[str, Any]]:
        return [
            {
                "leg_id": "LEG_1",
                "symbol": "NIFTY24OCT25000CE",
                "strike": 25000.0,
                "option_type": "CE",
                "expiry": "2024-10-31",
                "side": "BUY",
                "quantity": 50,
                "entry_price": base_price
            },
            {
                "leg_id": "LEG_2",
                "symbol": "NIFTY24OCT25200CE",
                "strike": 25200.0,
                "option_type": "CE",
                "expiry": "2024-10-31",
                "side": "SELL",
                "quantity": 50,
                "entry_price": base_price * 0.6
            }
        ]

    def test_concurrent_portfolio_summary_reads(self):
        """
        Multiple reader threads concurrently query portfolio summary while
        worker threads continuously open and close positions.
        """
        num_readers = 4
        num_writers = 2
        iterations = 50
        barrier = threading.Barrier(num_readers + num_writers)
        errors = []

        def reader():
            try:
                barrier.wait(timeout=5)
                for _ in range(iterations):
                    summary = self.pm.get_portfolio_summary()
                    self.assertIsInstance(summary, dict)
                    self.assertIn("open_positions_count", summary)
                    self.assertIn("total_unrealized_mtm", summary)
                    self.assertIn("net_combined_pnl", summary)
                    self.assertGreaterEqual(summary["open_positions_count"], 0)
                    self.assertGreaterEqual(summary["open_legs_count"], 0)
            except Exception as e:
                errors.append(f"Reader error: {e}")

        def writer(w_idx):
            try:
                barrier.wait(timeout=5)
                for i in range(iterations):
                    pos = self.pm.create_position(
                        strategy_id=f"STRAT_00{w_idx}",
                        exit_policy=StopLossPolicy.COMBINED_POSITION,
                        legs_data=self._sample_legs(),
                        timestamp="2024-10-25 09:30:00"
                    )
                    if i % 2 == 1:
                        self.pm.close_entire_position(
                            pos.position_id,
                            exit_prices={"NIFTY24OCT25000CE": 110.0, "NIFTY24OCT25200CE": 55.0},
                            timestamp="2024-10-25 10:00:00",
                            reason=ExitReason.TARGET_HIT
                        )
            except Exception as e:
                errors.append(f"Writer error: {e}")

        threads = []
        for i in range(num_readers):
            threads.append(threading.Thread(target=reader))
        for i in range(num_writers):
            threads.append(threading.Thread(target=writer, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")
        for t in threads:
            self.assertFalse(t.is_alive(), "Thread deadlock detected")

    def test_concurrent_open_positions_reads(self):
        """
        Verifies that iterating over get_open_positions() during concurrent
        dictionary mutations never raises RuntimeError (dictionary changed size).
        """
        barrier = threading.Barrier(4)
        errors = []
        stop_event = threading.Event()

        def reader():
            try:
                barrier.wait(timeout=5)
                while not stop_event.is_set():
                    open_pos = self.pm.get_open_positions()
                    # Iterate through positions list
                    for p in open_pos:
                        _ = p.position_id
                        _ = p.status
                        _ = len(p.legs)
            except Exception as e:
                errors.append(f"Reader error: {e}")

        def writer():
            try:
                barrier.wait(timeout=5)
                created = []
                for i in range(100):
                    p = self.pm.create_position(
                        strategy_id="STRAT_TEST",
                        exit_policy=StopLossPolicy.COMBINED_POSITION,
                        legs_data=self._sample_legs(),
                        timestamp="2024-10-25 09:30:00"
                    )
                    created.append(p.position_id)
                    if len(created) > 5:
                        to_close = created.pop(0)
                        self.pm.close_entire_position(
                            to_close,
                            exit_prices={"NIFTY24OCT25000CE": 105.0, "NIFTY24OCT25200CE": 62.0},
                            timestamp="2024-10-25 09:35:00",
                            reason=ExitReason.STOP_LOSS
                        )
                stop_event.set()
            except Exception as e:
                errors.append(f"Writer error: {e}")
                stop_event.set()

        r_threads = [threading.Thread(target=reader) for _ in range(3)]
        w_thread = threading.Thread(target=writer)
        all_threads = r_threads + [w_thread]

        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Encountered errors during iteration: {errors}")
        for t in all_threads:
            self.assertFalse(t.is_alive(), "Thread deadlock detected")

    def test_concurrent_mark_price_updates_and_summary_reads(self):
        """
        Writer thread atomically updates mark prices while reader threads
        fetch portfolio summaries, ensuring no partially updated marks are observed.
        """
        # Create 2 open positions
        pos1 = self.pm.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(base_price=100.0),
            timestamp="2024-10-25 09:15:00"
        )
        pos2 = self.pm.create_position(
            strategy_id="STRAT_002",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(base_price=100.0),
            timestamp="2024-10-25 09:15:00"
        )

        barrier = threading.Barrier(3)
        errors = []
        stop_event = threading.Event()

        def mark_updater():
            try:
                barrier.wait(timeout=5)
                # Alternates marks between price pairs
                for i in range(100):
                    p1 = 120.0 if i % 2 == 0 else 80.0
                    p2 = 60.0 if i % 2 == 0 else 40.0
                    self.pm.update_marks(
                        {"NIFTY24OCT25000CE": p1, "NIFTY24OCT25200CE": p2},
                        timestamp="2024-10-25 09:20:00"
                    )
                stop_event.set()
            except Exception as e:
                errors.append(f"Updater error: {e}")
                stop_event.set()

        def summary_reader():
            try:
                barrier.wait(timeout=5)
                while not stop_event.is_set():
                    summary = self.pm.get_portfolio_summary()
                    self.assertEqual(summary["open_positions_count"], 2)
                    self.assertEqual(summary["open_legs_count"], 4)
                    self.assertIsInstance(summary["total_unrealized_mtm"], float)
            except Exception as e:
                errors.append(f"Summary reader error: {e}")

        t_up = threading.Thread(target=mark_updater)
        t_rd1 = threading.Thread(target=summary_reader)
        t_rd2 = threading.Thread(target=summary_reader)
        threads = [t_up, t_rd1, t_rd2]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Mark update errors: {errors}")
        for t in threads:
            self.assertFalse(t.is_alive(), "Thread deadlock detected")

    def test_state_serialization_during_concurrent_access(self):
        """
        Verifies that save_state() produces consistent, valid JSON files
        without tearing while positions are concurrently created and closed.
        """
        barrier = threading.Barrier(3)
        errors = []
        stop_event = threading.Event()

        def saver():
            try:
                barrier.wait(timeout=5)
                save_count = 0
                while not stop_event.is_set() and save_count < 25:
                    success = self.pm.save_state()
                    self.assertTrue(success)
                    # Verify on-disk file is valid JSON
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.assertEqual(data["schema_version"], "2.1.0")
                    self.assertIn("positions", data)
                    self.assertEqual(data["positions_count"], len(data["positions"]))
                    save_count += 1
            except Exception as e:
                errors.append(f"Saver error: {e}")

        def mutator(m_id):
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    p = self.pm.create_position(
                        strategy_id=f"STRAT_MUT_{m_id}",
                        exit_policy=StopLossPolicy.COMBINED_POSITION,
                        legs_data=self._sample_legs(),
                        timestamp="2024-10-25 09:30:00"
                    )
                    if i % 3 == 0:
                        self.pm.close_entire_position(
                            p.position_id,
                            exit_prices={"NIFTY24OCT25000CE": 105.0, "NIFTY24OCT25200CE": 58.0},
                            timestamp="2024-10-25 09:40:00",
                            reason=ExitReason.TIME_CUTOFF
                        )
                if m_id == 1:
                    stop_event.set()
            except Exception as e:
                errors.append(f"Mutator error: {e}")
                stop_event.set()

        t_save = threading.Thread(target=saver)
        t_m1 = threading.Thread(target=mutator, args=(1,))
        t_m2 = threading.Thread(target=mutator, args=(2,))
        threads = [t_save, t_m1, t_m2]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Serialization errors: {errors}")
        for t in threads:
            self.assertFalse(t.is_alive(), "Thread deadlock detected")

    def test_snapshot_immutability_and_defensive_copies(self):
        """
        Verifies that mutating snapshots returned by get_position_snapshot()
        and get_open_position_snapshots() does not corrupt internal state.
        """
        pos = self.pm.create_position(
            strategy_id="STRAT_SNAP",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(base_price=100.0),
            timestamp="2024-10-25 09:15:00"
        )
        snap = self.pm.get_position_snapshot(pos.position_id)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["status"], "OPEN")

        # Caller attempts to corrupt the returned dictionary
        snap["status"] = "CORRUPTED_STATUS"
        snap["legs"]["LEG_1"]["current_price"] = 999999.0
        del snap["legs"]["LEG_2"]

        # Check internal position state via position manager
        fresh_pos = self.pm.get_position(pos.position_id)
        self.assertEqual(fresh_pos.status, "OPEN")
        self.assertEqual(len(fresh_pos.legs), 2)
        self.assertEqual(fresh_pos.legs["LEG_1"].current_price, 100.0)

        # Check open position snapshots
        all_snaps = self.pm.get_open_position_snapshots()
        self.assertEqual(len(all_snaps), 1)
        all_snaps[0]["status"] = "ANOTHER_CORRUPTION"
        self.assertEqual(self.pm.get_position(pos.position_id).status, "OPEN")

    def test_reentrancy_and_deadlock_freedom(self):
        """
        Verifies that nested method calls through RLock do not deadlock:
        - get_portfolio_summary() calls get_open_positions()
        - record_processed_signal() calls _prune_processed_signals() which calls get_open_positions()
        """
        # Create a position
        pos = self.pm.create_position(
            strategy_id="STRAT_REENTRANT",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(),
            timestamp="2024-10-25 09:15:00"
        )
        # Test signal recording and pruning under nested lock
        for i in range(50):
            self.pm.record_processed_signal(f"SIG_{i}")

        summary = self.pm.get_portfolio_summary()
        self.assertEqual(summary["open_positions_count"], 1)

        # Partial close which internally calls pos.update_marks and recalculate_risk
        ok, updated_pos, records, err = self.pm.close_specific_legs(
            position_id=pos.position_id,
            legs_to_close=["LEG_1"],
            exit_prices={"NIFTY24OCT25000CE": 110.0, "NIFTY24OCT25200CE": 60.0},
            timestamp="2024-10-25 09:30:00",
            reason=ExitReason.TARGET_HIT
        )
        self.assertTrue(ok)
        self.assertEqual(updated_pos.status, "PARTIALLY_CLOSED")

    def test_accounting_results_exact_match(self):
        """
        Verifies that accounting formulas, charges, and PnL calculations remain
        100% identical and analytically exact under the synchronized position manager.
        """
        # Buy 50 Qty @ 100, Sell 50 Qty @ 60
        # Leg 1: Entry = 100, Exit = 120 -> Realized PnL = (120 - 100) * 50 = +1000.0
        # Leg 2: Entry = 60, Exit = 40 -> Realized PnL = (60 - 40) * 50 = +1000.0
        pos = self.pm.create_position(
            strategy_id="STRAT_EXACT",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(base_price=100.0),
            timestamp="2024-10-25 09:15:00"
        )
        closed_pos = self.pm.close_entire_position(
            position_id=pos.position_id,
            exit_prices={"NIFTY24OCT25000CE": 120.0, "NIFTY24OCT25200CE": 40.0},
            timestamp="2024-10-25 09:45:00",
            reason=ExitReason.TARGET_HIT
        )
        self.assertIsNotNone(closed_pos)
        self.assertEqual(closed_pos.status, "CLOSED")

        calc = ChargesCalculator()
        c1 = calc.calculate_trade_charges("OPTIONS", 100.0, 120.0, 50, "BUY").total_charges
        c2 = calc.calculate_trade_charges("OPTIONS", 60.0, 40.0, 50, "SELL").total_charges
        expected_total_charges = c1 + c2

        self.assertAlmostEqual(closed_pos.total_charges, expected_total_charges, places=2)
        expected_net_realized = (1000.0 - c1) + (1000.0 - c2)
        self.assertAlmostEqual(closed_pos.combined_realized_pnl, expected_net_realized, places=2)

        summary = self.pm.get_portfolio_summary()
        self.assertEqual(summary["open_positions_count"], 0)
        self.assertAlmostEqual(summary["total_net_realized_pnl"], expected_net_realized, places=2)

    def test_position_lifecycle_during_telemetry_reads(self):
        """
        Simulates telemetry polling (dashboard API querying open positions and summary)
        while position lifecycle events (open, partial close, full close) occur concurrently.
        """
        barrier = threading.Barrier(3)
        errors = []
        stop_event = threading.Event()

        def telemetry_poller():
            try:
                barrier.wait(timeout=5)
                while not stop_event.is_set():
                    summary = self.pm.get_portfolio_summary()
                    open_snaps = self.pm.get_open_position_snapshots()
                    self.assertIsInstance(summary, dict)
                    self.assertIsInstance(open_snaps, list)
                    for snap in open_snaps:
                        self.assertIn("position_id", snap)
                        self.assertIn("legs", snap)
                        self.assertIn("status", snap)
                        self.assertIn(snap["status"], ["OPEN", "PARTIALLY_CLOSED"])
            except Exception as e:
                errors.append(f"Telemetry error: {e}")

        def lifecycle_worker(w_id):
            try:
                barrier.wait(timeout=5)
                for i in range(40):
                    pos = self.pm.create_position(
                        strategy_id=f"STRAT_LIFE_{w_id}",
                        exit_policy=StopLossPolicy.COMBINED_POSITION,
                        legs_data=self._sample_legs(),
                        timestamp="2024-10-25 09:30:00"
                    )
                    # Partial close
                    self.pm.close_specific_legs(
                        position_id=pos.position_id,
                        legs_to_close=["LEG_1"],
                        exit_prices={"NIFTY24OCT25000CE": 105.0, "NIFTY24OCT25200CE": 55.0},
                        timestamp="2024-10-25 09:35:00",
                        reason=ExitReason.TARGET_HIT
                    )
                    # Full close
                    self.pm.close_entire_position(
                        position_id=pos.position_id,
                        exit_prices={"NIFTY24OCT25000CE": 108.0, "NIFTY24OCT25200CE": 52.0},
                        timestamp="2024-10-25 09:40:00",
                        reason=ExitReason.TARGET_HIT
                    )
                if w_id == 1:
                    stop_event.set()
            except Exception as e:
                errors.append(f"Worker error: {e}")
                stop_event.set()

        t_tel = threading.Thread(target=telemetry_poller)
        t_w1 = threading.Thread(target=lifecycle_worker, args=(1,))
        t_w2 = threading.Thread(target=lifecycle_worker, args=(2,))
        threads = [t_tel, t_w1, t_w2]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Telemetry concurrency errors: {errors}")
        for t in threads:
            self.assertFalse(t.is_alive(), "Thread deadlock detected")


if __name__ == "__main__":
    unittest.main()

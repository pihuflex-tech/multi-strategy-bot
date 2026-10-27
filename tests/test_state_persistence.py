"""
test_state_persistence.py
Deterministic offline tests for MultiLegPositionManager state persistence hardening (F-10).
Verifies:
1. Unique temporary file naming per save_state invocation (no collision).
2. Concurrent save_state calls produce valid state files without corrupting JSON or crashing.
3. Temporary files are cleaned up after successful writes and handled failures.
4. Independent cleanup: failure in one writer does not delete another writer's temporary file.
5. State file schema version '2.1.0' and data integrity preserved under concurrent loads.
"""

import unittest
import threading
import tempfile
import os
import json
import time
import uuid
from unittest.mock import patch

from src.execution.position_manager import MultiLegPositionManager
from src.strategies.signal_models import StopLossPolicy


class TestStatePersistence(unittest.TestCase):
    """Test suite for position manager state persistence hardening."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test_positions.json")
        self.pm = MultiLegPositionManager(state_filepath=self.state_file)

    def tearDown(self):
        # Clean up temp files
        if os.path.exists(self.temp_dir):
            for fname in os.listdir(self.temp_dir):
                fpath = os.path.join(self.temp_dir, fname)
                try:
                    os.remove(fpath)
                except OSError:
                    pass
            try:
                os.rmdir(self.temp_dir)
            except OSError:
                pass

    def _sample_legs(self, base_price: float = 100.0):
        return [
            {
                "leg_id": "LEG_A",
                "symbol": "NIFTY24OCT25000CE",
                "strike": 25000.0,
                "option_type": "CE",
                "expiry": "2024-10-31",
                "side": "BUY",
                "quantity": 50,
                "entry_price": base_price
            },
            {
                "leg_id": "LEG_B",
                "symbol": "NIFTY24OCT25200CE",
                "strike": 25200.0,
                "option_type": "CE",
                "expiry": "2024-10-31",
                "side": "SELL",
                "quantity": 50,
                "entry_price": base_price * 0.5
            }
        ]

    def test_unique_temp_file_naming_per_invocation(self):
        """Verify that each save_state invocation generates a unique temporary filename."""
        self.pm.create_position(
            strategy_id="STRAT_01",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(100.0),
            timestamp="2024-10-25 10:00:00"
        )
        
        created_temp_files = []
        original_replace = os.replace

        def mock_replace(src, dst):
            created_temp_files.append(src)
            original_replace(src, dst)

        with patch("os.replace", side_effect=mock_replace):
            self.assertTrue(self.pm.save_state())
            self.assertTrue(self.pm.save_state())
            self.assertTrue(self.pm.save_state())

        self.assertEqual(len(created_temp_files), 3)
        # All temp file names must be distinct
        self.assertEqual(len(set(created_temp_files)), 3)
        for temp_name in created_temp_files:
            self.assertTrue(temp_name.startswith(self.state_file + ".tmp."))

    def test_concurrent_save_state_calls(self):
        """
        Verify that multiple concurrent threads calling save_state() do not collide,
        all temporary files are cleanly removed, and the resulting state file is valid JSON
        with schema version '2.1.0'.
        """
        # Create several positions
        for i in range(5):
            self.pm.create_position(
                strategy_id=f"STRAT_0{i}",
                exit_policy=StopLossPolicy.COMBINED_POSITION,
                legs_data=self._sample_legs(100.0 + i),
                timestamp="2024-10-25 10:00:00"
            )

        num_threads = 8
        iterations_per_thread = 15
        barrier = threading.Barrier(num_threads)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                for _ in range(iterations_per_thread):
                    success = self.pm.save_state()
                    if not success:
                        errors.append(RuntimeError("save_state() returned False"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Concurrent save errors: {errors}")
        self.assertTrue(os.path.exists(self.state_file))

        # Check no dangling .tmp.* files remain
        remaining_files = os.listdir(self.temp_dir)
        temp_files = [f for f in remaining_files if ".tmp." in f]
        self.assertEqual(temp_files, [], f"Found orphaned temp files: {temp_files}")

        # Verify state file content
        with open(self.state_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        self.assertEqual(state.get("schema_version"), "2.1.0")
        self.assertIn("positions", state)
        self.assertEqual(len(state["positions"]), 5)

    def test_temp_file_cleanup_on_failure(self):
        """Verify that if writing fails, the unique temporary file is deleted and error is recorded."""
        self.pm.create_position(
            strategy_id="STRAT_01",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(100.0),
            timestamp="2024-10-25 10:00:00"
        )

        # Cause json.dump to raise an error
        with patch("json.dump", side_effect=IOError("Simulated disk error")):
            success = self.pm.save_state()
            self.assertFalse(success)
            self.assertIsNotNone(self.pm.last_save_error)
            self.assertIn("Simulated disk error", self.pm.last_save_error)

        # Check that no temporary files remain
        remaining_files = os.listdir(self.temp_dir)
        temp_files = [f for f in remaining_files if ".tmp." in f]
        self.assertEqual(temp_files, [])

    def test_writer_failure_does_not_delete_other_writer_temp_file(self):
        """
        Verify that each writer cleans up only its own temporary file without
        touching another writer's temporary file.
        """
        # Create a mock temporary file simulating a concurrent writer's file
        other_temp_file = f"{self.state_file}.tmp.{uuid.uuid4().hex}"
        with open(other_temp_file, "w") as f:
            f.write("other writer active")

        self.assertTrue(os.path.exists(other_temp_file))

        # Cause current save_state to fail
        with patch("json.dump", side_effect=ValueError("Serialization error")):
            success = self.pm.save_state()
            self.assertFalse(success)
            self.assertIsNotNone(self.pm.last_save_error)
            self.assertIn("ValueError", self.pm.last_save_error)

        # Verify that other_temp_file is still intact and not accidentally deleted
        self.assertTrue(os.path.exists(other_temp_file), "Other writer's temp file was erroneously deleted!")
        
        # Clean up manual temp file
        os.remove(other_temp_file)

    def test_state_recovery_from_saved_state(self):
        """Verify that state saved under the new logic recovers identically."""
        pos1 = self.pm.create_position(
            strategy_id="STRAT_01",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=self._sample_legs(100.0),
            timestamp="2024-10-25 10:00:00"
        )
        self.pm.update_marks(
            price_map={"NIFTY24OCT25000CE": 120.0, "NIFTY24OCT25200CE": 45.0},
            timestamp="2024-10-25 10:05:00"
        )
        saved = self.pm.save_state()
        self.assertTrue(saved)

        # Load into a new PositionManager instance
        new_pm = MultiLegPositionManager(state_filepath=self.state_file)
        success, err = new_pm.load_state()
        self.assertTrue(success)
        self.assertIsNone(err)

        recovered_pos = new_pm.get_position(pos1.position_id)
        self.assertIsNotNone(recovered_pos)
        self.assertEqual(recovered_pos.strategy_id, "STRAT_01")
        self.assertEqual(len(recovered_pos.legs), 2)
        self.assertAlmostEqual(recovered_pos.legs["LEG_A"].current_price, 120.0)
        self.assertAlmostEqual(recovered_pos.legs["LEG_B"].current_price, 45.0)


if __name__ == "__main__":
    unittest.main()

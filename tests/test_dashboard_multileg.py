"""
test_dashboard_multileg.py
Deterministic offline integration tests for multi-leg position telemetry,
API sanitization, and defensive snapshot safety.
"""

import math
import os
import tempfile
import unittest
from typing import Any, Dict, List

from main import ApplicationTelemetryProvider
from src.dashboard.api import sanitize_telemetry, _sanitize_leg
from src.dashboard.app import create_app
from src.execution.position_manager import MultiLegPositionManager
from src.strategies.signal_models import StopLossPolicy


class TestDashboardMultiLegIntegration(unittest.TestCase):
    """Integration tests verifying multi-leg position flow through telemetry and API."""

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
        try:
            os.rmdir(self.temp_dir)
        except OSError:
            pass

    def _create_bull_call_spread(self) -> str:
        """Helper creating a 2-leg Bull Call Spread position."""
        pos = self.pm.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=[
                {
                    "leg_id": "LEG_BUY",
                    "symbol": "NIFTY24OCT25000CE",
                    "strike": 25000.0,
                    "option_type": "CE",
                    "expiry": "2024-10-31",
                    "side": "BUY",
                    "quantity": 50,
                    "entry_price": 120.0
                },
                {
                    "leg_id": "LEG_SELL",
                    "symbol": "NIFTY24OCT25200CE",
                    "strike": 25200.0,
                    "option_type": "CE",
                    "expiry": "2024-10-31",
                    "side": "SELL",
                    "quantity": 50,
                    "entry_price": 45.0
                }
            ],
            timestamp="2024-10-25 09:20:00"
        )
        return pos.position_id

    def test_single_open_multileg_position_through_api(self):
        """
        Verify that a multi-leg position flows from PositionManager through
        ApplicationTelemetryProvider to /api/state with sanitized legs.
        """
        pos_id = self._create_bull_call_spread()
        self.pm.update_marks(
            {"NIFTY24OCT25000CE": 135.0, "NIFTY24OCT25200CE": 40.0},
            timestamp="2024-10-25 09:25:00"
        )

        provider = ApplicationTelemetryProvider(position_manager=self.pm)
        app = create_app(telemetry_provider=provider)
        client = app.test_client()

        res = client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertIn("open_positions", data)
        self.assertEqual(len(data["open_positions"]), 1)

        pos_data = data["open_positions"][0]
        self.assertEqual(pos_data["position_id"], pos_id)
        self.assertEqual(pos_data["strategy_id"], "STRAT_001")
        self.assertEqual(pos_data["status"], "OPEN")
        self.assertEqual(pos_data["legs_count"], 2)

        # Verify combined MTM matches: (135 - 120)*50 + (45 - 40)*50 = 750 + 250 = 1000.0
        self.assertAlmostEqual(pos_data["combined_unrealized_pnl"], 1000.0, places=2)

        # Verify legs array is preserved and properly formatted
        self.assertIn("legs", pos_data)
        self.assertIsInstance(pos_data["legs"], list)
        self.assertEqual(len(pos_data["legs"]), 2)

        leg_map = {l["leg_id"]: l for l in pos_data["legs"]}
        self.assertIn("LEG_BUY", leg_map)
        self.assertIn("LEG_SELL", leg_map)

        buy_leg = leg_map["LEG_BUY"]
        self.assertEqual(buy_leg["symbol"], "NIFTY24OCT25000CE")
        self.assertEqual(buy_leg["side"], "BUY")
        self.assertEqual(buy_leg["remaining_quantity"], 50)
        self.assertEqual(buy_leg["entry_price"], 120.0)
        self.assertEqual(buy_leg["current_price"], 135.0)
        self.assertAlmostEqual(buy_leg["unrealized_pnl"], 750.0, places=2)

        sell_leg = leg_map["LEG_SELL"]
        self.assertEqual(sell_leg["symbol"], "NIFTY24OCT25200CE")
        self.assertEqual(sell_leg["side"], "SELL")
        self.assertEqual(sell_leg["remaining_quantity"], 50)
        self.assertEqual(sell_leg["entry_price"], 45.0)
        self.assertEqual(sell_leg["current_price"], 40.0)
        self.assertAlmostEqual(sell_leg["unrealized_pnl"], 250.0, places=2)

    def test_multileg_with_multiple_legs_different_quantities(self):
        """
        Verify multi-leg structures with different strikes, asymmetric quantities,
        and mixed options/futures quotes serialize cleanly.
        """
        self.pm.create_position(
            strategy_id="STRAT_ASYM",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=[
                {
                    "leg_id": "L1",
                    "symbol": "NIFTY24OCT24800PE",
                    "strike": 24800.0,
                    "option_type": "PE",
                    "side": "BUY",
                    "quantity": 100,
                    "entry_price": 50.0
                },
                {
                    "leg_id": "L2",
                    "symbol": "NIFTY24OCT25000PE",
                    "strike": 25000.0,
                    "option_type": "PE",
                    "side": "SELL",
                    "quantity": 50,
                    "entry_price": 95.0
                },
                {
                    "leg_id": "L3",
                    "symbol": "NIFTY_FUT_OCT",
                    "strike": 0.0,
                    "option_type": "FUT",
                    "side": "BUY",
                    "quantity": 25,
                    "entry_price": 25050.0
                }
            ],
            timestamp="2024-10-25 09:30:00"
        )
        provider = ApplicationTelemetryProvider(position_manager=self.pm)
        state = provider.get_state()
        self.assertEqual(len(state["open_positions"]), 1)
        self.assertEqual(len(state["open_positions"][0]["legs"]), 3)

        app = create_app(telemetry_provider=provider)
        client = app.test_client()
        res = client.get("/api/state")
        data = res.get_json()

        legs = data["open_positions"][0]["legs"]
        self.assertEqual(len(legs), 3)
        self.assertEqual(legs[0]["remaining_quantity"], 100)
        self.assertEqual(legs[1]["remaining_quantity"], 50)
        self.assertEqual(legs[2]["remaining_quantity"], 25)

    def test_empty_positions_returns_empty_list(self):
        """Verify that zero open positions serialize safely as empty list without errors."""
        provider = ApplicationTelemetryProvider(position_manager=self.pm)
        app = create_app(telemetry_provider=provider)
        client = app.test_client()

        res = client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["open_positions"], [])
        self.assertEqual(data["portfolio"]["open_positions_count"], 0)

    def test_missing_optional_fields_and_non_finite_values(self):
        """
        Verify that NaN, Inf, None, and corrupted data are safely filtered without crashing.
        """
        raw_malformed = {
            "open_positions": [
                {
                    "position_id": "POS_CORRUPT",
                    "strategy_id": "STRAT_BAD",
                    "combined_unrealized_pnl": float("nan"),
                    "combined_realized_pnl": float("inf"),
                    "api_key": "LEAKED_KEY_123",
                    "legs": [
                        {
                            "leg_id": "LEG_CORRUPT",
                            "symbol": "NIFTY24OCTCE",
                            "entry_price": float("nan"),
                            "current_price": None,
                            "unrealized_pnl": float("-inf"),
                            "secret_token": "SUPER_SECRET",
                        },
                        "NOT_A_DICT"
                    ]
                }
            ]
        }
        clean = sanitize_telemetry(raw_malformed)
        pos = clean["open_positions"][0]

        # NaN/Inf converted to None
        self.assertIsNone(pos["combined_unrealized_pnl"])
        self.assertIsNone(pos["combined_realized_pnl"])
        self.assertNotIn("api_key", pos)

        # Invalid leg item discarded
        self.assertEqual(len(pos["legs"]), 1)
        leg = pos["legs"][0]
        self.assertIsNone(leg["entry_price"])
        self.assertIsNone(leg["current_price"])
        self.assertIsNone(leg["unrealized_pnl"])
        self.assertNotIn("secret_token", leg)

    def test_defensive_snapshots_cannot_mutate_position_manager(self):
        """
        Verify that mutating dictionaries returned by telemetry provider
        does not corrupt internal PositionManager state (F-09).
        """
        pos_id = self._create_bull_call_spread()
        provider = ApplicationTelemetryProvider(position_manager=self.pm)

        state = provider.get_state()
        self.assertEqual(len(state["open_positions"]), 1)

        # Attempt to mutate returned snapshot
        snap = state["open_positions"][0]
        snap["status"] = "CORRUPTED"
        if isinstance(snap["legs"], dict):
            snap["legs"]["LEG_BUY"]["current_price"] = 999999.0
        elif isinstance(snap["legs"], list):
            snap["legs"][0]["current_price"] = 999999.0

        # Check internal position manager
        internal_pos = self.pm.get_position(pos_id)
        self.assertEqual(internal_pos.status, "OPEN")
        self.assertEqual(internal_pos.legs["LEG_BUY"].current_price, 120.0)


if __name__ == "__main__":
    unittest.main()

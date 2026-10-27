"""
test_paper_execution.py
Offline unit tests for simulated paper execution and multi-leg position lifecycle.
Validates:
- PositionManager leg creation, MTM mark updates, and portfolio aggregate summary.
- Clean partial exits without double-counting (cost basis preservation).
- Full position closure and statutory charges deduction.
- Orphan short leg exposure detection (safety rule).
- PaperBroker simulated spread crossing and slippage modeling.
"""

import unittest
from datetime import datetime
from src.accounting.charges_calculator import ChargesCalculator
from src.execution.position_manager import MultiLegPositionManager, LegPosition, MultiLegPosition
from src.execution.paper_broker import PaperBroker
from src.strategies.signal_models import (
    StopLossPolicy, ExitReason, StrategySignal, LegOrder, SignalDirection, StrategyMode
)
from src.market.market_snapshot import MarketSnapshot, OptionContract, FuturesContract


class TestPaperExecutionAndPositionManager(unittest.TestCase):
    """Test suite for position management and paper execution."""

    def setUp(self):
        self.tax_calc = ChargesCalculator(brokerage_per_order=20.0)
        self.pm = MultiLegPositionManager(charges_calc=self.tax_calc)
        self.broker = PaperBroker(self.pm, simulated_slippage_points=0.5)

    def test_single_leg_creation_and_mark_update(self):
        """Test creating a single leg position and updating mark prices."""
        legs_data = [{
            "symbol": "NIFTY26OCT24800CE",
            "strike": 24800.0,
            "option_type": "CE",
            "side": "BUY",
            "quantity": 75,
            "entry_price": 100.0
        }]
        pos = self.pm.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=legs_data,
            timestamp="2026-10-25T10:00:00Z"
        )
        self.assertEqual(pos.status, "OPEN")
        self.assertEqual(len(pos.legs), 1)

        # Update mark price to 120 (+20 points = +1500 gross)
        price_map = {"NIFTY26OCT24800CE": 120.0}
        pos.update_marks(price_map, "2026-10-25T10:05:00Z")
        self.assertEqual(pos.combined_unrealized_pnl, 1500.0)

    def test_partial_exit_accounting_no_double_counting(self):
        """Test partial exit: closes portion, preserves cost basis, avoids double counting."""
        leg = LegPosition(
            leg_id="leg_001",
            symbol="NIFTY26OCT24800CE",
            strike=24800.0,
            option_type="CE",
            expiry="2026-10-29",
            side="BUY",
            initial_quantity=150,  # 2 lots
            remaining_quantity=150,
            entry_price=100.0,
            entry_time="2026-10-25T10:00:00Z",
            current_price=100.0
        )
        # Close 1 lot (75 qty) @ 120 (+20 pts = +1500 PnL, 10 charges)
        pnl_closed = leg.partial_close(qty=75, exit_price=120.0, timestamp="2026-10-25T10:15:00Z", charges=10.0)
        self.assertEqual(pnl_closed, 1490.0)
        self.assertEqual(leg.remaining_quantity, 75)
        self.assertEqual(leg.status, "OPEN")
        self.assertEqual(leg.realized_pnl, 1490.0)

        # Update mark on remaining 75 qty to 130 (+30 pts = +2250 unrealized)
        leg.update_mark_price(130.0, "2026-10-25T10:20:00Z")
        self.assertEqual(leg.unrealized_pnl, 2250.0)

        # Close the remaining 75 qty @ 130
        pnl_final = leg.partial_close(qty=75, exit_price=130.0, timestamp="2026-10-25T10:30:00Z", charges=10.0)
        self.assertEqual(pnl_final, 2240.0)
        self.assertEqual(leg.remaining_quantity, 0)
        self.assertEqual(leg.status, "CLOSED")
        self.assertEqual(leg.realized_pnl, 1490.0 + 2240.0)
        self.assertEqual(leg.unrealized_pnl, 0.0)

    def test_orphan_short_exposure_detection(self):
        """Test safety check alerting when short option legs remain open with zero wing hedges."""
        legs_data = [
            {"symbol": "NIFTY_SHORT_CE", "strike": 24900, "option_type": "CE", "side": "SELL", "quantity": 75, "entry_price": 60},
            {"symbol": "NIFTY_LONG_CE", "strike": 25100, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 20}
        ]
        pos = self.pm.create_position(
            strategy_id="STRAT_017",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=legs_data,
            timestamp="2026-10-25T10:00:00Z"
        )
        # Initially, 1 short and 1 long leg -> no orphan risk
        self.assertIsNone(pos.check_orphan_risk())

        # Now suppose long wing was accidentally closed
        long_leg = next(l for l in pos.legs.values() if l.side == "BUY")
        long_leg.close(exit_price=15.0, timestamp="2026-10-25T10:30:00Z")

        # Now short leg is unprotected naked short -> must flag risk
        orphan_alert = pos.check_orphan_risk()
        self.assertIsNotNone(orphan_alert)
        self.assertIn("ORPHAN_SHORT_EXPOSURE", orphan_alert)

    def test_full_position_closure_and_portfolio_summary(self):
        """Test closing entire position and calculating aggregate portfolio metrics."""
        legs_data = [{
            "symbol": "NIFTY26OCT24800CE",
            "strike": 24800.0,
            "option_type": "CE",
            "side": "BUY",
            "quantity": 75,
            "entry_price": 100.0
        }]
        pos = self.pm.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=legs_data,
            timestamp="2026-10-25T10:00:00Z"
        )

        summary_before = self.pm.get_portfolio_summary()
        self.assertEqual(summary_before["open_positions_count"], 1)
        self.assertEqual(summary_before["open_legs_count"], 1)

        # Close position @ 140
        self.pm.close_entire_position(
            position_id=pos.position_id,
            exit_prices={"NIFTY26OCT24800CE": 140.0},
            timestamp="2026-10-25T11:00:00Z",
            reason=ExitReason.TARGET_HIT
        )
        self.assertEqual(pos.status, "CLOSED")

        summary_after = self.pm.get_portfolio_summary()
        self.assertEqual(summary_after["open_positions_count"], 0)
        self.assertEqual(summary_after["open_legs_count"], 0)
        self.assertGreater(summary_after["total_net_realized_pnl"], 2800.0)  # 3000 gross minus charges
        self.assertGreater(summary_after["total_charges"], 40.0)

    def test_paper_broker_execution_with_slippage(self):
        """Test paper broker execution fills leg with simulated slippage and spread crossing."""
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T10:00:00Z",
            spot=24800.0,
            options={
                "24800_CE": OptionContract(
                    strike=24800.0, option_type="CE", ltp=120.0, bid=119.5, ask=120.5, oi=100000, volume=50000
                )
            }
        )

        signal = StrategySignal(
            strategy_id="STRAT_001",
            strategy_version="1.0.0",
            timestamp="2026-10-25T10:00:00Z",
            direction=SignalDirection.BULLISH,
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs=[
                LegOrder(
                    symbol="NIFTY26OCT24800CE",
                    strike=24800.0,
                    option_type="CE",
                    side="BUY",
                    quantity=75,
                    requested_price=120.0
                )
            ],
            initial_sl_value=80.0
        )

        success, pos, err = self.broker.execute_entry_signal(signal, snapshot)
        self.assertTrue(success)
        self.assertIsNotNone(pos)
        self.assertIsNone(err)

        # Long BUY fill price should cross ask (120.5) + slippage (0.5) = 121.0
        leg = list(pos.legs.values())[0]
        self.assertEqual(leg.entry_price, 121.0)


if __name__ == "__main__":
    unittest.main()

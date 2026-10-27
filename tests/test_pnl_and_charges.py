"""
test_pnl_and_charges.py
Unit tests for Indian F&O statutory tax engine and multi-leg P&L accounting.
Validates:
- Budget 2024 regulatory charges (STT 0.1% on options sell, 0.02% on futures sell, GST 18%, Stamp Duty, SEBI, Brokerage).
- Leg-level and combined multi-leg P&L calculation: sum(q_i * (M_i - E_i)) with signed quantities.
- Defined risk, max profit, and max loss boundaries for vertical spreads.
"""

import unittest
from src.accounting.charges_calculator import ChargesCalculator, StatutoryCharges
from src.accounting.pnl_calculator import (
    calculate_leg_pnl,
    calculate_combined_position_pnl,
    calculate_spread_defined_risk
)


class TestPnLAndCharges(unittest.TestCase):
    """Test suite for PnL calculation and statutory tax modeling."""

    def setUp(self):
        self.tax_calc = ChargesCalculator(
            brokerage_per_order=20.0,
            stt_options_sell_pct=0.10,     # Post-Budget 2024: 0.1% on sell premium
            stt_futures_sell_pct=0.02,     # Post-Budget 2024: 0.02% on sell futures turnover
            exchange_options_pct=0.05,
            exchange_futures_pct=0.00173,
            stamp_duty_options_buy_pct=0.003,
            stamp_duty_futures_buy_pct=0.002,
            sebi_per_crore=10.0,
            gst_pct=18.0
        )

    def test_single_leg_pnl_buy_and_sell(self):
        """Test gross P&L for long and short single legs."""
        # Long Call: bought 75 @ 100, current price 140 -> +3000
        long_pnl = calculate_leg_pnl(entry_price=100.0, current_price=140.0, quantity=75, side="BUY")
        self.assertEqual(long_pnl, 3000.0)

        # Long Call in loss: bought 75 @ 100, current price 80 -> -1500
        long_loss = calculate_leg_pnl(entry_price=100.0, current_price=80.0, quantity=75, side="BUY")
        self.assertEqual(long_loss, -1500.0)

        # Short Call: sold 75 @ 120, current price 90 -> +2250
        short_pnl = calculate_leg_pnl(entry_price=120.0, current_price=90.0, quantity=75, side="SELL")
        self.assertEqual(short_pnl, 2250.0)

        # Short Call in loss: sold 75 @ 120, current price 150 -> -2250
        short_loss = calculate_leg_pnl(entry_price=120.0, current_price=150.0, quantity=75, side="SELL")
        self.assertEqual(short_loss, -2250.0)

    def test_combined_position_pnl_long_straddle(self):
        """Test combined multi-leg P&L: sum(q_i * (M_i - E_i)) for Long Straddle."""
        # Buy 1 lot 24800 CE @ 120, Buy 1 lot 24800 PE @ 110
        # If CE rises to 180 (+60) and PE drops to 60 (-50)
        # Net P&L = 75*(180-120) + 75*(60-110) = 4500 - 3750 = +750
        legs = [
            {"side": "BUY", "entry_price": 120.0, "current_price": 180.0, "quantity": 75},
            {"side": "BUY", "entry_price": 110.0, "current_price": 60.0, "quantity": 75}
        ]
        result = calculate_combined_position_pnl(legs)
        self.assertEqual(result["gross_unrealized_pnl"], 750.0)
        self.assertEqual(result["entry_cost"], (120 * 75) + (110 * 75))

    def test_combined_position_pnl_short_straddle(self):
        """Test combined multi-leg P&L for Short Straddle with signed quantities."""
        # Sell 1 lot CE @ 120, Sell 1 lot PE @ 110
        # If market decays: CE at 80, PE at 70
        # CE PnL = -75*(80-120) = +3000
        # PE PnL = -75*(70-110) = +3000
        # Total = +6000
        legs = [
            {"side": "SELL", "entry_price": 120.0, "current_price": 80.0, "quantity": 75},
            {"side": "SELL", "entry_price": 110.0, "current_price": 70.0, "quantity": 75}
        ]
        result = calculate_combined_position_pnl(legs)
        self.assertEqual(result["gross_unrealized_pnl"], 6000.0)

    def test_spread_defined_risk(self):
        """Test defined max loss and max profit boundaries for Bull Call Spread."""
        # NIFTY 24800 / 24900 Bull Call Spread
        # Buy 24800 CE @ 140, Sell 24900 CE @ 90. Net debit = 50. Width = 100.
        # Max Loss = 50 * 75 = 3750. Max Profit = (100 - 50) * 75 = 3750.
        spread = calculate_spread_defined_risk(
            spread_type="BULL_CALL_SPREAD",
            buy_strike=24800.0,
            sell_strike=24900.0,
            buy_premium=140.0,
            sell_premium=90.0,
            lot_size=75
        )
        self.assertEqual(spread["max_loss"], 3750.0)
        self.assertEqual(spread["max_profit"], 3750.0)
        self.assertEqual(spread["risk_reward_ratio"], 1.0)

    def test_spread_defined_risk_default_lot_size_65(self):
        """Test defined max loss and max profit boundaries using default lot size 65."""
        # NIFTY 24800 / 24900 Bull Call Spread
        # Buy 24800 CE @ 140, Sell 24900 CE @ 90. Net debit = 50. Width = 100.
        # Max Loss = 50 * 65 = 3250. Max Profit = (100 - 50) * 65 = 3250.
        spread = calculate_spread_defined_risk(
            spread_type="BULL_CALL_SPREAD",
            buy_strike=24800.0,
            sell_strike=24900.0,
            buy_premium=140.0,
            sell_premium=90.0
            # Omits lot_size to verify default lot_size == 65
        )
        self.assertEqual(spread["max_loss"], 3250.0)
        self.assertEqual(spread["max_profit"], 3250.0)
        self.assertEqual(spread["risk_reward_ratio"], 1.0)

    def test_options_statutory_charges_budget_2024(self):
        """Test post-Budget 2024 options statutory charges."""
        # Buy 1 lot (75 qty) @ 100, Sell @ 150
        # Buy turnover = 7500, Sell turnover = 11250, Total = 18750
        charges = self.tax_calc.calculate_trade_charges(
            instrument_type="OPTIONS",
            entry_price=100.0,
            exit_price=150.0,
            quantity=75,
            side="BUY"
        )
        # Brokerage = 2 orders * 20 = 40.0
        self.assertEqual(charges.brokerage, 40.0)

        # STT = 0.1% on SELL turnover (11250 * 0.001) = 11.25
        self.assertEqual(charges.stt, 11.25)

        # Stamp duty = 0.003% on BUY turnover (7500 * 0.00003) = 0.225 -> 0.23
        self.assertEqual(charges.stamp_duty, 0.23)

        # Total charges must be positive and greater than brokerage
        self.assertGreater(charges.total_charges, 40.0)
        self.assertGreater(charges.gst, 0.0)

    def test_futures_statutory_charges_budget_2024(self):
        """Test post-Budget 2024 futures statutory charges."""
        # Buy 1 lot futures (75 qty) @ 24800, Sell @ 24900
        # Buy turnover = 1,860,000, Sell turnover = 1,867,500
        charges = self.tax_calc.calculate_trade_charges(
            instrument_type="FUTURES",
            entry_price=24800.0,
            exit_price=24900.0,
            quantity=75,
            side="BUY"
        )
        # STT = 0.02% on SELL turnover (1,867,500 * 0.0002) = 373.5
        self.assertEqual(charges.stt, 373.5)
        self.assertEqual(charges.brokerage, 40.0)
        self.assertGreater(charges.total_charges, 400.0)


if __name__ == "__main__":
    unittest.main()

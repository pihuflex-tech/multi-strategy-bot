"""
test_risk_manager.py
Offline unit tests for independent portfolio risk governor.
Validates:
- Stale tick entry blocking.
- Strategy cooldown timers and reactivation.
- Maximum open positions per strategy (duplicate position prevention).
- Portfolio max open positions limit.
- Daily realized loss circuit breaker and emergency square-off trigger.
- Signal leg quantity sanity checks.
"""

import time
import unittest
from src.portfolio.risk_manager import RiskManager
from src.strategies.signal_models import (
    StrategySignal, LegOrder, SignalDirection, StopLossPolicy
)


class TestRiskManager(unittest.TestCase):
    """Test suite for portfolio risk controls and circuit breakers."""

    def setUp(self):
        self.rm = RiskManager(config={
            "portfolio_max_daily_loss": 50000.0,
            "max_open_positions_total": 5,
            "max_open_positions_per_strategy": 1,
            "risk_checks_enabled": True
        })

        self.signal = StrategySignal(
            strategy_id="STRAT_001",
            strategy_version="1.0.0",
            timestamp="2026-10-25T10:00:00Z",
            direction=SignalDirection.BULLISH,
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs=[
                LegOrder(
                    symbol="NIFTY24800CE",
                    strike=24800.0,
                    option_type="CE",
                    side="BUY",
                    quantity=75,
                    requested_price=120.0
                )
            ]
        )

    def test_valid_signal_passes_risk_checks(self):
        """A valid signal with low portfolio exposure must pass risk checks."""
        summary = {"open_positions_count": 0, "total_net_realized_pnl": 0.0, "total_unrealized_mtm": 0.0}
        passed, reason = self.rm.validate_signal(
            self.signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=0,
            is_market_data_stale=False
        )
        self.assertTrue(passed)
        self.assertIsNone(reason)

    def test_stale_market_data_blocks_entry(self):
        """Signals generated with stale ticks must be blocked immediately."""
        summary = {"open_positions_count": 0, "total_net_realized_pnl": 0.0, "total_unrealized_mtm": 0.0}
        passed, reason = self.rm.validate_signal(
            self.signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=0,
            is_market_data_stale=True
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "STALE_MARKET_DATA_ENTRY_BLOCKED")

    def test_cooldown_blocks_reentry(self):
        """A strategy in cooldown must be blocked until cooldown expires."""
        self.rm.set_cooldown("STRAT_001", duration_seconds=60.0)
        self.assertTrue(self.rm.is_in_cooldown("STRAT_001"))

        summary = {"open_positions_count": 0, "total_net_realized_pnl": 0.0, "total_unrealized_mtm": 0.0}
        passed, reason = self.rm.validate_signal(
            self.signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=0,
            is_market_data_stale=False
        )
        self.assertFalse(passed)
        self.assertIn("STRATEGY_IN_COOLDOWN", reason)

    def test_duplicate_position_prevention(self):
        """Cannot open more than max_open_positions_per_strategy (default 1)."""
        summary = {"open_positions_count": 1, "total_net_realized_pnl": 0.0, "total_unrealized_mtm": 0.0}
        passed, reason = self.rm.validate_signal(
            self.signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=1,
            is_market_data_stale=False
        )
        self.assertFalse(passed)
        self.assertIn("MAX_POSITIONS_PER_STRATEGY_REACHED", reason)

    def test_portfolio_max_positions_limit(self):
        """Cannot open a new position if portfolio max open positions reached."""
        summary = {"open_positions_count": 5, "total_net_realized_pnl": 0.0, "total_unrealized_mtm": 0.0}
        passed, reason = self.rm.validate_signal(
            self.signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=0,
            is_market_data_stale=False
        )
        self.assertFalse(passed)
        self.assertIn("PORTFOLIO_MAX_OPEN_POSITIONS_EXCEEDED", reason)

    def test_daily_loss_circuit_breaker(self):
        """If net daily loss exceeds threshold, all new entry signals are blocked."""
        summary = {"open_positions_count": 0, "total_net_realized_pnl": -52000.0, "total_unrealized_mtm": 0.0}
        passed, reason = self.rm.validate_signal(
            self.signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=0,
            is_market_data_stale=False
        )
        self.assertFalse(passed)
        self.assertIn("DAILY_LOSS_CIRCUIT_BREAKER_TRIGGERED", reason)

        # Emergency square-off halt test
        emergency, halt_msg = self.rm.check_portfolio_emergency_halt(summary)
        self.assertTrue(emergency)
        self.assertIn("EMERGENCY_DAILY_LOSS_BREACH", halt_msg)

    def test_invalid_leg_quantity(self):
        """Zero or negative quantity legs must be rejected."""
        bad_signal = StrategySignal(
            strategy_id="STRAT_001",
            strategy_version="1.0.0",
            timestamp="2026-10-25T10:00:00Z",
            direction=SignalDirection.BULLISH,
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs=[
                LegOrder(symbol="NIFTY24800CE", strike=24800.0, option_type="CE", side="BUY", quantity=0, requested_price=120.0)
            ]
        )
        summary = {"open_positions_count": 0, "total_net_realized_pnl": 0.0, "total_unrealized_mtm": 0.0}
        passed, reason = self.rm.validate_signal(
            bad_signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=0,
            is_market_data_stale=False
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "INVALID_LEG_QUANTITY_0")


if __name__ == "__main__":
    unittest.main()

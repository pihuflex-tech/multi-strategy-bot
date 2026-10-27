"""
calendar_spread.py - STRAT_023
Independent implementation of Multi-Expiry Calendar Spread Strategy.
Sells near-expiry ATM option and Buys far-expiry ATM option to capture accelerated theta decay.
"""

from typing import Optional, Dict, Any
from src.strategies.strategy_base import StrategyBase
from src.strategies.signal_models import (
    StrategySignal,
    ExitSignal,
    SignalDirection,
    ExitReason,
    LegOrder,
    StopLossPolicy
)
from src.market.market_snapshot import MarketSnapshot
from src.execution.position_manager import MultiLegPosition
from src.calculations.atm_calculator import get_atm_strike

class CalendarSpreadStrategy(StrategyBase):
    """
    STRAT_023: Calendar Spread Strategy (Multi-Expiry)
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_023",
            name="Calendar Spread",
            cluster="spreads",
            config=config or {},
            workspace_root=workspace_root
        )
        self.combined_sl_pct = float(self.config.get("combined_sl_pct", 25.0))
        self.target_pct = float(self.config.get("target_pct", 40.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price)
        current_expiry = snapshot.current_expiry
        next_expiry = snapshot.next_expiry

        # Require verified distinct expiries - never substitute or fall back
        if not current_expiry or not next_expiry or current_expiry == next_expiry:
            return None

        near_ce = snapshot.get_option(atm_strike, "CE", expiry=current_expiry)
        far_ce = snapshot.get_option(atm_strike, "CE", expiry=next_expiry)

        # Data-blocked if either required expiry quote is missing or non-positive
        if not near_ce or not far_ce or near_ce.ltp <= 0 or far_ce.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_near = LegOrder("LEG_SELL_NEAR_CE", near_ce.symbol, atm_strike, "CE", current_expiry, "SELL", qty, near_ce.ltp)
        leg_far = LegOrder("LEG_BUY_FAR_CE", far_ce.symbol, atm_strike, "CE", next_expiry, "BUY", qty, far_ce.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.BUY,
            timestamp=snapshot.timestamp,
            legs=[leg_near, leg_far],
            entry_reason=f"Calendar Spread: Sell Near {atm_strike} CE ({current_expiry}) @ {near_ce.ltp}, Buy Far {atm_strike} CE ({next_expiry}) @ {far_ce.ltp}",
            initial_sl_value=self.combined_sl_pct,
            initial_sl_type="COMBINED",
            target_value=self.target_pct,
            exit_policy=StopLossPolicy.COMBINED_POSITION
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        total_entry_cost = position.entry_debit_or_credit
        if total_entry_cost <= 0:
            return None

        unrealized = position.combined_unrealized_pnl
        loss_pct = (-unrealized / total_entry_cost) * 100.0 if unrealized < 0 else 0.0
        profit_pct = (unrealized / total_entry_cost) * 100.0 if unrealized > 0 else 0.0

        if loss_pct >= self.combined_sl_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Calendar Spread SL hit: loss {round(loss_pct, 2)}% >= {self.combined_sl_pct}%"
            )

        if profit_pct >= self.target_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Calendar Spread target reached: profit {round(profit_pct, 2)}% >= {self.target_pct}%"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

"""
long_straddle.py - STRAT_003
Independent implementation of Long Straddle Strategy (2-leg buying: ATM CE + ATM PE).
Operates under COMBINED_POSITION exit policy.
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

class LongStraddleStrategy(StrategyBase):
    """
    STRAT_003: Long Straddle Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_003",
            name="Long Straddle",
            cluster="straddle",
            config=config or {},
            workspace_root=workspace_root
        )
        self.combined_sl_pct = float(self.config.get("combined_sl_pct", 25.0))
        self.combined_target_pct = float(self.config.get("combined_target_pct", 45.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price)
        ce = snapshot.get_option(atm_strike, "CE")
        pe = snapshot.get_option(atm_strike, "PE")
        if not ce or not pe or ce.ltp <= 0 or pe.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_ce = LegOrder("LEG_BUY_CE", ce.symbol, atm_strike, "CE", ce.expiry, "BUY", qty, ce.ltp)
        leg_pe = LegOrder("LEG_BUY_PE", pe.symbol, atm_strike, "PE", pe.expiry, "BUY", qty, pe.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.BUY,
            timestamp=snapshot.timestamp,
            legs=[leg_ce, leg_pe],
            entry_reason=f"Volatility expansion setup, ATM {atm_strike} combined premium {ce.ltp + pe.ltp}",
            initial_sl_value=self.combined_sl_pct,
            initial_sl_type="COMBINED",
            target_value=self.combined_target_pct,
            exit_policy=StopLossPolicy.COMBINED_POSITION
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        # Evaluated on combined position P&L relative to initial debit
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
                details=f"Combined SL breached: loss {round(loss_pct, 2)}% >= {self.combined_sl_pct}%"
            )

        if profit_pct >= self.combined_target_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Combined target reached: profit {round(profit_pct, 2)}% >= {self.combined_target_pct}%"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

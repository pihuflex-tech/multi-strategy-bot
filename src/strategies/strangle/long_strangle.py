"""
long_strangle.py - STRAT_004
Independent implementation of Long Strangle Strategy (2-leg buying: OTM CE + OTM PE).
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

class LongStrangleStrategy(StrategyBase):
    """
    STRAT_004: Long Strangle Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_004",
            name="Long Strangle",
            cluster="strangle",
            config=config or {},
            workspace_root=workspace_root
        )
        self.strike_offset = int(self.config.get("strike_offset", 100))
        self.combined_sl_pct = float(self.config.get("combined_sl_pct", 30.0))
        self.combined_target_pct = float(self.config.get("combined_target_pct", 60.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price)
        ce_strike = atm_strike + self.strike_offset
        pe_strike = atm_strike - self.strike_offset

        ce = snapshot.get_option(ce_strike, "CE")
        pe = snapshot.get_option(pe_strike, "PE")
        if not ce or not pe or ce.ltp <= 0 or pe.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_ce = LegOrder("LEG_BUY_OTM_CE", ce.symbol, ce_strike, "CE", ce.expiry, "BUY", qty, ce.ltp)
        leg_pe = LegOrder("LEG_BUY_OTM_PE", pe.symbol, pe_strike, "PE", pe.expiry, "BUY", qty, pe.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.BUY,
            timestamp=snapshot.timestamp,
            legs=[leg_ce, leg_pe],
            entry_reason=f"Volatility expansion setup, OTM strikes {ce_strike}/{pe_strike}",
            initial_sl_value=self.combined_sl_pct,
            initial_sl_type="COMBINED",
            target_value=self.combined_target_pct,
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

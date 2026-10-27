"""
iron_condor.py - STRAT_007
Independent implementation of 4-leg Iron Condor Strategy.
Instantiates and validates all 4 legs: Long Wing PE, Short OTM PE, Short OTM CE, Long Wing CE.
Mathematical defined risk bounds maximum potential loss.
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

class IronCondorStrategy(StrategyBase):
    """
    STRAT_007: Iron Condor Strategy (4-Leg Defined Risk)
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_007",
            name="Iron Condor",
            cluster="iron_condor",
            config=config or {},
            workspace_root=workspace_root
        )
        self.short_offset = int(self.config.get("short_offset", 150))
        self.wing_offset = int(self.config.get("wing_offset", 350))
        self.combined_sl_pct = float(self.config.get("combined_sl_pct", 35.0))
        self.target_pct = float(self.config.get("target_pct", 60.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price)

        short_pe_strike = atm_strike - self.short_offset
        long_pe_strike = atm_strike - self.wing_offset

        short_ce_strike = atm_strike + self.short_offset
        long_ce_strike = atm_strike + self.wing_offset

        # Validate strike ordering: long_pe < short_pe < short_ce < long_ce
        if not (long_pe_strike < short_pe_strike < short_ce_strike < long_ce_strike):
            return None

        long_pe = snapshot.get_option(long_pe_strike, "PE")
        short_pe = snapshot.get_option(short_pe_strike, "PE")
        short_ce = snapshot.get_option(short_ce_strike, "CE")
        long_ce = snapshot.get_option(long_ce_strike, "CE")

        if not (long_pe and short_pe and short_ce and long_ce):
            return None
        if any(opt.ltp <= 0 for opt in [long_pe, short_pe, short_ce, long_ce]):
            return None

        qty = self.lot_size * self.max_lots
        leg_lpe = LegOrder("LEG_BUY_WING_PE", long_pe.symbol, long_pe_strike, "PE", long_pe.expiry, "BUY", qty, long_pe.ltp)
        leg_spe = LegOrder("LEG_SELL_SHORT_PE", short_pe.symbol, short_pe_strike, "PE", short_pe.expiry, "SELL", qty, short_pe.ltp)
        leg_sce = LegOrder("LEG_SELL_SHORT_CE", short_ce.symbol, short_ce_strike, "CE", short_ce.expiry, "SELL", qty, short_ce.ltp)
        leg_lce = LegOrder("LEG_BUY_WING_CE", long_ce.symbol, long_ce_strike, "CE", long_ce.expiry, "BUY", qty, long_ce.ltp)

        # Net credit = (short_pe.ltp + short_ce.ltp) - (long_pe.ltp + long_ce.ltp)
        net_credit_pts = (short_pe.ltp + short_ce.ltp) - (long_pe.ltp + long_ce.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.SELL,
            timestamp=snapshot.timestamp,
            legs=[leg_lpe, leg_spe, leg_sce, leg_lce],
            entry_reason=f"Defined risk 4-leg Condor: {long_pe_strike}/{short_pe_strike}/{short_ce_strike}/{long_ce_strike}, Net Credit {round(net_credit_pts, 2)} pts",
            initial_sl_value=self.combined_sl_pct,
            initial_sl_type="COMBINED",
            target_value=self.target_pct,
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            metadata={"net_credit_pts": net_credit_pts}
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        total_credit = abs(position.entry_debit_or_credit)
        if total_credit <= 0:
            return None

        unrealized = position.combined_unrealized_pnl
        loss_pct = (-unrealized / total_credit) * 100.0 if unrealized < 0 else 0.0
        profit_pct = (unrealized / total_credit) * 100.0 if unrealized > 0 else 0.0

        if loss_pct >= self.combined_sl_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Condor combined SL breached: loss {round(loss_pct, 2)}% >= {self.combined_sl_pct}%"
            )

        if profit_pct >= self.target_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Condor target reached: profit {round(profit_pct, 2)}% >= {self.target_pct}%"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

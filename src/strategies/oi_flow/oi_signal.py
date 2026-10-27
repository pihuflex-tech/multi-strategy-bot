"""
oi_signal.py - STRAT_014
Independent implementation of Open Interest (OI) Flow Strategy.
Uses Put-Call Ratio (PCR) and strike-level OI accumulation to establish directional option positions.
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
from src.calculations.market_metrics import calculate_pcr

class OIBasedStrategy(StrategyBase):
    """
    STRAT_014: OI-Based Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_014",
            name="OI-Based Strategy",
            cluster="oi_flow",
            config=config or {},
            workspace_root=workspace_root
        )
        self.pcr_extreme_threshold = float(self.config.get("pcr_extreme_threshold", 1.25))
        self.sl_pct = float(self.config.get("sl_pct", 20.0))
        self.target_pct = float(self.config.get("target_pct", 40.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        if not snapshot.option_chain:
            return None

        pcr_metrics = calculate_pcr(snapshot.option_chain)
        pcr_oi = pcr_metrics["pcr_oi"]

        atm_strike = get_atm_strike(snapshot.spot_price)
        qty = self.lot_size * self.max_lots

        # PCR > threshold -> Bullish Put writing dominance -> Buy Call
        if pcr_oi >= self.pcr_extreme_threshold:
            opt = snapshot.get_option(atm_strike, "CE")
            if not opt or opt.ltp <= 0:
                return None
            leg = LegOrder("LEG_OI_BUY_CE", opt.symbol, atm_strike, "CE", opt.expiry, "BUY", qty, opt.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Bullish OI Flow: PCR {pcr_oi} >= {self.pcr_extreme_threshold}",
                initial_sl_value=self.sl_pct,
                initial_sl_type="PERCENTAGE",
                target_value=self.target_pct,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )
        # PCR < 0.80 -> Bearish Call writing dominance -> Buy Put
        elif pcr_oi <= 0.80:
            opt = snapshot.get_option(atm_strike, "PE")
            if not opt or opt.ltp <= 0:
                return None
            leg = LegOrder("LEG_OI_BUY_PE", opt.symbol, atm_strike, "PE", opt.expiry, "BUY", qty, opt.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Bearish OI Flow: PCR {pcr_oi} <= 0.80",
                initial_sl_value=self.sl_pct,
                initial_sl_type="PERCENTAGE",
                target_value=self.target_pct,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        return None

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        open_legs = [l for l in position.legs.values() if l.status == "OPEN"]
        if not open_legs:
            return None

        leg = open_legs[0]
        current_opt = snapshot.get_option(leg.strike, leg.option_type)
        curr_price = current_opt.ltp if current_opt and current_opt.ltp > 0 else leg.current_price

        # SL check
        sl_price = leg.entry_price * (1.0 - (self.sl_pct / 100.0))
        if curr_price <= sl_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"OI Strategy SL breached: {curr_price} <= {round(sl_price, 2)}"
            )

        # Target check
        target_price = leg.entry_price * (1.0 + (self.target_pct / 100.0))
        if curr_price >= target_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"OI Strategy target reached: {curr_price} >= {round(target_price, 2)}"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

"""
adaptive_hedging.py - STRAT_015
Independent implementation of Adaptive Hedging Strategy.
Monitors directional delta imbalance and triggers rule-based hedging positions to protect capital.
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

class AdaptiveHedgingStrategy(StrategyBase):
    """
    STRAT_015: Adaptive Hedging Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_015",
            name="Adaptive Hedging",
            cluster="hedging",
            config=config or {},
            workspace_root=workspace_root
        )
        self.delta_trigger_threshold = float(self.config.get("delta_trigger_threshold", 0.35))
        self.rebalance_cooldown_seconds = float(self.config.get("rebalance_cooldown_seconds", 600.0))
        self.last_hedge_time: float = 0.0

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        # Evaluates underlying momentum as a proxy for portfolio delta exposure
        fut = snapshot.futures
        if not fut or fut.ltp <= 0:
            return None

        atm_strike = get_atm_strike(snapshot.spot_price)
        atm_ce = snapshot.get_option(atm_strike, "CE")
        atm_pe = snapshot.get_option(atm_strike, "PE")
        if not atm_ce or not atm_pe:
            return None

        # Directional disparity check
        call_delta = atm_ce.delta if atm_ce.delta > 0 else 0.50
        put_delta = abs(atm_pe.delta) if atm_pe.delta != 0 else 0.50
        delta_skew = call_delta - put_delta

        qty = self.lot_size * self.max_lots

        # Excessive upside exposure -> Buy Put hedge
        if delta_skew >= self.delta_trigger_threshold:
            leg = LegOrder("LEG_HEDGE_PE", atm_pe.symbol, atm_strike, "PE", atm_pe.expiry, "BUY", qty, atm_pe.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Adaptive Delta Hedge: Upside delta skew {round(delta_skew, 2)} >= {self.delta_trigger_threshold}",
                initial_sl_value=25.0,
                initial_sl_type="PERCENTAGE",
                target_value=40.0,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )
        # Excessive downside exposure -> Buy Call hedge
        elif delta_skew <= -self.delta_trigger_threshold:
            leg = LegOrder("LEG_HEDGE_CE", atm_ce.symbol, atm_strike, "CE", atm_ce.expiry, "BUY", qty, atm_ce.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Adaptive Delta Hedge: Downside delta skew {round(delta_skew, 2)} <= -{self.delta_trigger_threshold}",
                initial_sl_value=25.0,
                initial_sl_type="PERCENTAGE",
                target_value=40.0,
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

        # SL check: 25%
        sl_price = leg.entry_price * 0.75
        if curr_price <= sl_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Adaptive Hedge SL hit: {curr_price} <= {round(sl_price, 2)}"
            )

        # Target check: 40%
        target_price = leg.entry_price * 1.40
        if curr_price >= target_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Adaptive Hedge target reached: {curr_price} >= {round(target_price, 2)}"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

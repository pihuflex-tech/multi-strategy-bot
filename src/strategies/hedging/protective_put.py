"""
protective_put.py - STRAT_010
Independent implementation of Futures + Protective Put Strategy (2-leg synthetic call).
Combines Long Futures with a Long OTM Put to limit maximum downside disaster risk.
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

class ProtectivePutStrategy(StrategyBase):
    """
    STRAT_010: Futures + Protective Put Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_010",
            name="Futures + Protective Put",
            cluster="hedging",
            config=config or {},
            workspace_root=workspace_root
        )
        self.put_offset = int(self.config.get("put_offset", 100))
        self.futures_sl_points = float(self.config.get("futures_sl_points", 60.0))
        self.target_points = float(self.config.get("target_points", 120.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        fut = snapshot.futures
        if not fut or fut.ltp <= 0:
            return None

        atm_strike = get_atm_strike(snapshot.spot_price)
        put_strike = atm_strike - self.put_offset
        put_opt = snapshot.get_option(put_strike, "PE")
        if not put_opt or put_opt.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_fut = LegOrder("LEG_BUY_FUT", fut.symbol, 0.0, "FUT", fut.expiry, "BUY", qty, fut.ltp)
        leg_put = LegOrder("LEG_BUY_PROTECTIVE_PUT", put_opt.symbol, put_strike, "PE", put_opt.expiry, "BUY", qty, put_opt.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.BUY,
            timestamp=snapshot.timestamp,
            legs=[leg_fut, leg_put],
            entry_reason=f"Futures + Put: Long Fut @ {fut.ltp}, Long {put_strike} PE @ {put_opt.ltp}",
            initial_sl_value=self.futures_sl_points,
            initial_sl_type="POINTS",
            target_value=self.target_points,
            exit_policy=StopLossPolicy.COMBINED_POSITION
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        fut_leg = position.legs.get("LEG_BUY_FUT")
        put_leg = position.legs.get("LEG_BUY_PROTECTIVE_PUT")
        if not fut_leg or fut_leg.status != "OPEN":
            return None

        fut = snapshot.futures
        curr_fut_p = fut.ltp if fut and fut.ltp > 0 else fut_leg.current_price
        fut_points_pnl = curr_fut_p - fut_leg.entry_price

        # Check futures stop loss
        if fut_points_pnl <= -self.futures_sl_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Futures downside SL hit: {fut_points_pnl} pts <= -{self.futures_sl_points} pts"
            )

        # Check target
        if fut_points_pnl >= self.target_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Futures upside target hit: {fut_points_pnl} pts >= {self.target_points} pts"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

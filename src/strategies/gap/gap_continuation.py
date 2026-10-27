"""
gap_continuation.py - STRAT_012
Independent implementation of Gap Continuation Strategy.
Trades in the direction of large opening gap breakouts when momentum confirms.
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

class GapContinuationStrategy(StrategyBase):
    """
    STRAT_012: Gap Continuation Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_012",
            name="Gap Continuation",
            cluster="gap",
            config=config or {},
            workspace_root=workspace_root
        )
        self.min_gap_pct = float(self.config.get("min_gap_pct", 0.40))
        self.sl_points = float(self.config.get("sl_points", 35.0))
        self.target_points = float(self.config.get("target_points", 70.0))
        self.reference_open: Optional[float] = None
        self.previous_close: float = float(self.config.get("previous_close", 24800.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        if self.reference_open is None and snapshot.spot_price > 0:
            self.reference_open = snapshot.spot_price

        if not self.reference_open or self.previous_close <= 0:
            return None

        gap_pct = ((self.reference_open - self.previous_close) / self.previous_close) * 100.0

        fut = snapshot.futures
        if not fut or fut.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots

        # Large Gap Up -> Trade Continuation Long
        if gap_pct >= self.min_gap_pct and snapshot.spot_price >= self.reference_open:
            leg = LegOrder("LEG_FUTURES_CONT", fut.symbol, 0.0, "FUT", fut.expiry, "BUY", qty, fut.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Gap Continuation Long: Gap Up +{round(gap_pct, 2)}% >= {self.min_gap_pct}%",
                initial_sl_value=self.sl_points,
                initial_sl_type="POINTS",
                target_value=self.target_points,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )
        # Large Gap Down -> Trade Continuation Short
        elif gap_pct <= -self.min_gap_pct and snapshot.spot_price <= self.reference_open:
            leg = LegOrder("LEG_FUTURES_CONT", fut.symbol, 0.0, "FUT", fut.expiry, "SELL", qty, fut.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.SELL,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Gap Continuation Short: Gap Down {round(gap_pct, 2)}% <= -{self.min_gap_pct}%",
                initial_sl_value=self.sl_points,
                initial_sl_type="POINTS",
                target_value=self.target_points,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        return None

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        leg = position.legs.get("LEG_FUTURES_CONT")
        if not leg or leg.status != "OPEN":
            return None

        fut = snapshot.futures
        curr_price = fut.ltp if fut and fut.ltp > 0 else leg.current_price
        points_pnl = (curr_price - leg.entry_price) if leg.side == "BUY" else (leg.entry_price - curr_price)

        if points_pnl <= -self.sl_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Gap Continuation SL hit: {points_pnl} pts <= -{self.sl_points} pts"
            )

        if points_pnl >= self.target_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Gap Continuation target reached: {points_pnl} pts >= {self.target_points} pts"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

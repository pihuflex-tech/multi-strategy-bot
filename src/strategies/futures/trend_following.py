"""
trend_following.py - STRAT_009
Independent implementation of Futures Trend Following Strategy.
Generates long or short futures signals based on trend direction, with initial SL, trailing SL, and target points.
"""

from typing import Optional, Dict, Any, List
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
from src.calculations.trend_calculator import calculate_ema

class FuturesTrendFollowingStrategy(StrategyBase):
    """
    STRAT_009: Futures Trend Following Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_009",
            name="Futures Trend Following",
            cluster="futures",
            config=config or {},
            workspace_root=workspace_root
        )
        self.initial_sl_points = float(self.config.get("initial_sl_points", 45.0))
        self.trailing_sl_points = float(self.config.get("trailing_sl_points", 25.0))
        self.target_points = float(self.config.get("target_points", 90.0))
        self.price_history: List[float] = []

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        fut = snapshot.futures
        if not fut or fut.ltp <= 0:
            return None

        self.price_history.append(fut.ltp)
        if len(self.price_history) > 50:
            self.price_history.pop(0)

        if len(self.price_history) < 10:
            return None

        ema_fast = calculate_ema(self.price_history, 9)
        ema_slow = calculate_ema(self.price_history, 21)

        side = "BUY" if ema_fast >= ema_slow else "SELL"
        direction = SignalDirection.BUY if side == "BUY" else SignalDirection.SELL

        qty = self.lot_size * self.max_lots
        leg = LegOrder(
            leg_id="LEG_FUTURES",
            symbol=fut.symbol,
            strike=0.0,
            option_type="FUT",
            expiry=fut.expiry,
            side=side,
            quantity=qty,
            requested_price=fut.ltp
        )

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=direction,
            timestamp=snapshot.timestamp,
            legs=[leg],
            entry_reason=f"Futures Trend {side}: Fast EMA {ema_fast} vs Slow EMA {ema_slow}",
            initial_sl_value=self.initial_sl_points,
            initial_sl_type="POINTS",
            trailing_sl_value=self.trailing_sl_points,
            target_value=self.target_points,
            exit_policy=StopLossPolicy.INDIVIDUAL_LEG
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        leg = position.legs.get("LEG_FUTURES")
        if not leg or leg.status != "OPEN":
            return None

        fut = snapshot.futures
        curr_price = fut.ltp if fut and fut.ltp > 0 else leg.current_price

        # Points P&L
        points_pnl = (curr_price - leg.entry_price) if leg.side == "BUY" else (leg.entry_price - curr_price)

        # Initial SL check
        if points_pnl <= -self.initial_sl_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Futures SL hit: {points_pnl} pts <= -{self.initial_sl_points} pts"
            )

        # Target check
        if points_pnl >= self.target_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Futures target hit: {points_pnl} pts >= {self.target_points} pts"
            )

        # Trailing SL check
        active_tsl = position.trailing_sl_value
        if active_tsl > 0:
            if leg.side == "BUY" and curr_price <= active_tsl:
                return ExitSignal(
                    position_id=position.position_id,
                    strategy_id=self.strategy_id,
                    timestamp=snapshot.timestamp,
                    reason=ExitReason.TRAILING_STOP_LOSS,
                    details=f"Futures Long TSL hit: {curr_price} <= {active_tsl}"
                )
            elif leg.side == "SELL" and curr_price >= active_tsl:
                return ExitSignal(
                    position_id=position.position_id,
                    strategy_id=self.strategy_id,
                    timestamp=snapshot.timestamp,
                    reason=ExitReason.TRAILING_STOP_LOSS,
                    details=f"Futures Short TSL hit: {curr_price} >= {active_tsl}"
                )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        leg = position.legs.get("LEG_FUTURES")
        if not leg or leg.status != "OPEN":
            return

        fut = snapshot.futures
        curr_price = fut.ltp if fut and fut.ltp > 0 else leg.current_price
        points_pnl = (curr_price - leg.entry_price) if leg.side == "BUY" else (leg.entry_price - curr_price)

        # Activate trailing once profit reaches +30 points
        if points_pnl >= 30.0:
            if leg.side == "BUY":
                new_tsl = curr_price - self.trailing_sl_points
                if new_tsl > position.trailing_sl_value:
                    position.trailing_sl_value = round(new_tsl, 2)
            else:
                new_tsl = curr_price + self.trailing_sl_points
                if position.trailing_sl_value == 0 or new_tsl < position.trailing_sl_value:
                    position.trailing_sl_value = round(new_tsl, 2)

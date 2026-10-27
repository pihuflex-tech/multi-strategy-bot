"""
ema_crossover.py - STRAT_013
Independent implementation of EMA Crossover Strategy (Fast 9 EMA vs Slow 21 EMA).
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

class EMACrossoverStrategy(StrategyBase):
    """
    STRAT_013: EMA Crossover Strategy (9 / 21 EMA)
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_013",
            name="EMA Crossover",
            cluster="trend",
            config=config or {},
            workspace_root=workspace_root
        )
        self.fast_period = int(self.config.get("fast_period", 9))
        self.slow_period = int(self.config.get("slow_period", 21))
        self.sl_points = float(self.config.get("sl_points", 40.0))
        self.trailing_sl_points = float(self.config.get("trailing_sl_points", 20.0))
        self.target_points = float(self.config.get("target_points", 80.0))
        self.price_history: List[float] = []

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        fut = snapshot.futures
        if not fut or fut.ltp <= 0:
            return None

        self.price_history.append(fut.ltp)
        if len(self.price_history) > 60:
            self.price_history.pop(0)

        if len(self.price_history) < self.slow_period:
            return None

        ema_fast = calculate_ema(self.price_history, self.fast_period)
        ema_slow = calculate_ema(self.price_history, self.slow_period)

        side = "BUY" if ema_fast >= ema_slow else "SELL"
        direction = SignalDirection.BUY if side == "BUY" else SignalDirection.SELL

        qty = self.lot_size * self.max_lots
        leg = LegOrder("LEG_EMA_FUT", fut.symbol, 0.0, "FUT", fut.expiry, side, qty, fut.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=direction,
            timestamp=snapshot.timestamp,
            legs=[leg],
            entry_reason=f"EMA Cross: Fast {ema_fast} vs Slow {ema_slow}",
            initial_sl_value=self.sl_points,
            initial_sl_type="POINTS",
            trailing_sl_value=self.trailing_sl_points,
            target_value=self.target_points,
            exit_policy=StopLossPolicy.INDIVIDUAL_LEG
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        leg = position.legs.get("LEG_EMA_FUT")
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
                details=f"EMA Cross SL hit: {points_pnl} pts <= -{self.sl_points} pts"
            )

        if points_pnl >= self.target_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"EMA Cross target hit: {points_pnl} pts >= {self.target_points} pts"
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
                    details=f"EMA Cross Long TSL hit: {curr_price} <= {active_tsl}"
                )
            elif leg.side == "SELL" and curr_price >= active_tsl:
                return ExitSignal(
                    position_id=position.position_id,
                    strategy_id=self.strategy_id,
                    timestamp=snapshot.timestamp,
                    reason=ExitReason.TRAILING_STOP_LOSS,
                    details=f"EMA Cross Short TSL hit: {curr_price} >= {active_tsl}"
                )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        leg = position.legs.get("LEG_EMA_FUT")
        if not leg or leg.status != "OPEN":
            return

        fut = snapshot.futures
        curr_price = fut.ltp if fut and fut.ltp > 0 else leg.current_price
        points_pnl = (curr_price - leg.entry_price) if leg.side == "BUY" else (leg.entry_price - curr_price)

        if points_pnl >= 30.0:
            if leg.side == "BUY":
                new_tsl = curr_price - self.trailing_sl_points
                if new_tsl > position.trailing_sl_value:
                    position.trailing_sl_value = round(new_tsl, 2)
            else:
                new_tsl = curr_price + self.trailing_sl_points
                if position.trailing_sl_value == 0 or new_tsl < position.trailing_sl_value:
                    position.trailing_sl_value = round(new_tsl, 2)

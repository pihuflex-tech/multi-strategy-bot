"""
supertrend_trend.py - STRAT_025
Independent implementation of Supertrend Futures Trend Following Strategy.
Uses 10-period ATR with 3.0 multiplier to trade index futures with trailing stops.
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
from src.calculations.trend_calculator import calculate_supertrend

class SupertrendFuturesStrategy(StrategyBase):
    """
    STRAT_025: Supertrend Futures Trend Following Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_025",
            name="Supertrend Futures Trend Following",
            cluster="futures",
            config=config or {},
            workspace_root=workspace_root
        )
        self.supertrend_period = int(self.config.get("supertrend_period", 10))
        self.supertrend_multiplier = float(self.config.get("supertrend_multiplier", 3.0))
        self.sl_points = float(self.config.get("sl_points", 40.0))
        self.trailing_sl_points = float(self.config.get("trailing_sl_points", 20.0))
        self.target_points = float(self.config.get("target_points", 80.0))
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.closes: List[float] = []

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        fut = snapshot.futures
        if not fut or fut.ltp <= 0:
            return None

        self.highs.append(fut.ltp)
        self.lows.append(fut.ltp)
        self.closes.append(fut.ltp)
        if len(self.closes) > 50:
            self.highs.pop(0)
            self.lows.pop(0)
            self.closes.pop(0)

        if len(self.closes) < self.supertrend_period:
            return None

        st_result = calculate_supertrend(
            self.highs, self.lows, self.closes, self.supertrend_period, self.supertrend_multiplier
        )
        direction = st_result["direction"]  # 1 for Bullish, -1 for Bearish
        side = "BUY" if direction == 1 else "SELL"
        sig_direction = SignalDirection.BUY if side == "BUY" else SignalDirection.SELL

        qty = self.lot_size * self.max_lots
        leg = LegOrder("LEG_ST_FUT", fut.symbol, 0.0, "FUT", fut.expiry, side, qty, fut.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=sig_direction,
            timestamp=snapshot.timestamp,
            legs=[leg],
            entry_reason=f"Supertrend Direction {direction} ({side}) @ {fut.ltp}",
            initial_sl_value=self.sl_points,
            initial_sl_type="POINTS",
            trailing_sl_value=self.trailing_sl_points,
            target_value=self.target_points,
            exit_policy=StopLossPolicy.INDIVIDUAL_LEG
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        leg = position.legs.get("LEG_ST_FUT")
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
                details=f"Supertrend SL hit: {points_pnl} pts <= -{self.sl_points} pts"
            )

        if points_pnl >= self.target_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Supertrend target hit: {points_pnl} pts >= {self.target_points} pts"
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
                    details=f"Supertrend Long TSL hit: {curr_price} <= {active_tsl}"
                )
            elif leg.side == "SELL" and curr_price >= active_tsl:
                return ExitSignal(
                    position_id=position.position_id,
                    strategy_id=self.strategy_id,
                    timestamp=snapshot.timestamp,
                    reason=ExitReason.TRAILING_STOP_LOSS,
                    details=f"Supertrend Short TSL hit: {curr_price} >= {active_tsl}"
                )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        leg = position.legs.get("LEG_ST_FUT")
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

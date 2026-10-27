"""
donchian_breakout.py - STRAT_022
Independent implementation of Donchian Channel Breakout Strategy.
Enters Long on break of 20-period high, Short on break of 20-period low on Index Futures.
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
from src.calculations.trend_calculator import calculate_donchian_channel

class DonchianBreakoutStrategy(StrategyBase):
    """
    STRAT_022: Donchian Channel Breakout Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_022",
            name="Donchian Channel Breakout",
            cluster="trend",
            config=config or {},
            workspace_root=workspace_root
        )
        self.channel_period = int(self.config.get("channel_period", 20))
        self.sl_points = float(self.config.get("sl_points", 45.0))
        self.trailing_sl_points = float(self.config.get("trailing_sl_points", 25.0))
        self.target_points = float(self.config.get("target_points", 90.0))
        self.highs: List[float] = []
        self.lows: List[float] = []

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        fut = snapshot.futures
        if not fut or fut.ltp <= 0:
            return None

        self.highs.append(fut.ltp)
        self.lows.append(fut.ltp)
        if len(self.highs) > 50:
            self.highs.pop(0)
            self.lows.pop(0)

        if len(self.highs) < self.channel_period:
            return None

        # Calculate channel excluding current candle
        donchian = calculate_donchian_channel(self.highs[:-1], self.lows[:-1], self.channel_period)
        qty = self.lot_size * self.max_lots

        # Breakout Above Upper Channel -> Long Futures
        if fut.ltp > donchian["upper"]:
            leg = LegOrder("LEG_DONCHIAN_FUT", fut.symbol, 0.0, "FUT", fut.expiry, "BUY", qty, fut.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Donchian Breakout Long: Fut {fut.ltp} > Upper {donchian['upper']}",
                initial_sl_value=self.sl_points,
                initial_sl_type="POINTS",
                trailing_sl_value=self.trailing_sl_points,
                target_value=self.target_points,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        # Breakdown Below Lower Channel -> Short Futures
        if fut.ltp < donchian["lower"]:
            leg = LegOrder("LEG_DONCHIAN_FUT", fut.symbol, 0.0, "FUT", fut.expiry, "SELL", qty, fut.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.SELL,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Donchian Breakdown Short: Fut {fut.ltp} < Lower {donchian['lower']}",
                initial_sl_value=self.sl_points,
                initial_sl_type="POINTS",
                trailing_sl_value=self.trailing_sl_points,
                target_value=self.target_points,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        return None

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        leg = position.legs.get("LEG_DONCHIAN_FUT")
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
                details=f"Donchian SL hit: {points_pnl} pts <= -{self.sl_points} pts"
            )

        if points_pnl >= self.target_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Donchian target reached: {points_pnl} pts >= {self.target_points} pts"
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
                    details=f"Donchian Long TSL hit: {curr_price} <= {active_tsl}"
                )
            elif leg.side == "SELL" and curr_price >= active_tsl:
                return ExitSignal(
                    position_id=position.position_id,
                    strategy_id=self.strategy_id,
                    timestamp=snapshot.timestamp,
                    reason=ExitReason.TRAILING_STOP_LOSS,
                    details=f"Donchian Short TSL hit: {curr_price} >= {active_tsl}"
                )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        leg = position.legs.get("LEG_DONCHIAN_FUT")
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

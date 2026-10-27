"""
vwap_reclaim.py - STRAT_020
Independent implementation of VWAP Reclaim/Loss Strategy on Index Futures.
Generates Long signal upon VWAP reclaim from below, Short signal upon VWAP breakdown.
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
from src.calculations.market_metrics import VWAPTracker

class VWAPReclaimStrategy(StrategyBase):
    """
    STRAT_020: VWAP Reclaim/Loss Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_020",
            name="VWAP Reclaim/Loss",
            cluster="trend",
            config=config or {},
            workspace_root=workspace_root
        )
        self.sl_points = float(self.config.get("sl_points", 35.0))
        self.target_points = float(self.config.get("target_points", 70.0))
        self.vwap_tracker = VWAPTracker()
        self.prev_spot: Optional[float] = None

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        fut = snapshot.futures
        if not fut or fut.ltp <= 0 or snapshot.spot_price <= 0:
            return None

        # Update VWAP with volume
        current_vwap = self.vwap_tracker.update(snapshot.spot_price, fut.volume)

        if self.prev_spot is None:
            self.prev_spot = snapshot.spot_price
            return None

        qty = self.lot_size * self.max_lots

        # VWAP Reclaim: Previous Spot < VWAP and Current Spot >= VWAP -> Long
        if self.prev_spot < current_vwap and snapshot.spot_price >= current_vwap:
            self.prev_spot = snapshot.spot_price
            leg = LegOrder("LEG_VWAP_FUT", fut.symbol, 0.0, "FUT", fut.expiry, "BUY", qty, fut.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"VWAP Reclaim Long: Spot {snapshot.spot_price} crossed above VWAP {current_vwap}",
                initial_sl_value=self.sl_points,
                initial_sl_type="POINTS",
                target_value=self.target_points,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        # VWAP Loss: Previous Spot > VWAP and Current Spot <= VWAP -> Short
        if self.prev_spot > current_vwap and snapshot.spot_price <= current_vwap:
            self.prev_spot = snapshot.spot_price
            leg = LegOrder("LEG_VWAP_FUT", fut.symbol, 0.0, "FUT", fut.expiry, "SELL", qty, fut.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.SELL,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"VWAP Loss Short: Spot {snapshot.spot_price} crossed below VWAP {current_vwap}",
                initial_sl_value=self.sl_points,
                initial_sl_type="POINTS",
                target_value=self.target_points,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        self.prev_spot = snapshot.spot_price
        return None

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        leg = position.legs.get("LEG_VWAP_FUT")
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
                details=f"VWAP Reclaim SL hit: {points_pnl} pts <= -{self.sl_points} pts"
            )

        if points_pnl >= self.target_points:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"VWAP Reclaim target reached: {points_pnl} pts >= {self.target_points} pts"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

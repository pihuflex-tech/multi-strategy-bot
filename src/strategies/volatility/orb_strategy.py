"""
orb_strategy.py - STRAT_019
Independent implementation of Opening Range Breakout (ORB) Strategy.
Evaluates the initial 15-minute auction range and enters momentum options upon confirmed high/low breakout.
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
from src.calculations.atm_calculator import get_atm_strike

class OpeningRangeBreakoutStrategy(StrategyBase):
    """
    STRAT_019: Opening Range Breakout Strategy (15M ORB)
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_019",
            name="Opening Range Breakout",
            cluster="volatility",
            config=config or {},
            workspace_root=workspace_root
        )
        self.range_minutes = int(self.config.get("range_minutes", 15))
        self.sl_pct = float(self.config.get("sl_pct", 20.0))
        self.target_pct = float(self.config.get("target_pct", 50.0))

        # Range boundaries
        self.opening_high: Optional[float] = None
        self.opening_low: Optional[float] = None
        self.is_range_formed: bool = False

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        cur_time = snapshot.timestamp.split(" ")[-1] if " " in snapshot.timestamp else snapshot.timestamp
        spot = snapshot.spot_price
        if spot <= 0:
            return None

        # Build range between 09:15:00 and 09:30:00
        if cur_time < "09:30:00":
            if self.opening_high is None or spot > self.opening_high:
                self.opening_high = spot
            if self.opening_low is None or spot < self.opening_low:
                self.opening_low = spot
            return None

        self.is_range_formed = True
        if not self.opening_high or not self.opening_low:
            return None

        atm_strike = get_atm_strike(spot)
        qty = self.lot_size * self.max_lots

        # Breakout Above Opening High -> Buy ATM Call
        if spot > self.opening_high:
            opt = snapshot.get_option(atm_strike, "CE")
            if not opt or opt.ltp <= 0:
                return None
            leg = LegOrder("LEG_ORB_BUY_CE", opt.symbol, atm_strike, "CE", opt.expiry, "BUY", qty, opt.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"ORB Bullish Breakout: Spot {spot} > 15M High {self.opening_high}",
                initial_sl_value=self.sl_pct,
                initial_sl_type="PERCENTAGE",
                target_value=self.target_pct,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        # Breakdown Below Opening Low -> Buy ATM Put
        if spot < self.opening_low:
            opt = snapshot.get_option(atm_strike, "PE")
            if not opt or opt.ltp <= 0:
                return None
            leg = LegOrder("LEG_ORB_BUY_PE", opt.symbol, atm_strike, "PE", opt.expiry, "BUY", qty, opt.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"ORB Bearish Breakdown: Spot {spot} < 15M Low {self.opening_low}",
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
                details=f"ORB SL hit: {curr_price} <= {round(sl_price, 2)}"
            )

        # Target check
        target_price = leg.entry_price * (1.0 + (self.target_pct / 100.0))
        if curr_price >= target_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"ORB target reached: {curr_price} >= {round(target_price, 2)}"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

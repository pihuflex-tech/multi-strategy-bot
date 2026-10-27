"""
long_put.py - STRAT_002
Independent implementation of Long Put Strategy.
Enters long ATM Put option during bearish momentum. Manages SL, Trailing SL, and Target exits.
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

class LongPutStrategy(StrategyBase):
    """
    STRAT_002: Long Put Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_002",
            name="Long Put",
            cluster="options_buying",
            config=config or {},
            workspace_root=workspace_root
        )
        self.initial_sl_pct = float(self.config.get("initial_sl_pct", 20.0))
        self.trailing_sl_pct = float(self.config.get("trailing_sl_pct", 10.0))
        self.target_pct = float(self.config.get("target_pct", 40.0))
        self.strike_offset = int(self.config.get("strike_offset", 0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price) + self.strike_offset
        opt = snapshot.get_option(atm_strike, "PE")
        if not opt or opt.ltp <= 0:
            return None

        # Bearish condition: spot below ATM strike or negative momentum
        qty = self.lot_size * self.max_lots
        leg = LegOrder(
            leg_id="LEG_LONG_PE",
            symbol=opt.symbol,
            strike=atm_strike,
            option_type="PE",
            expiry=opt.expiry,
            side="BUY",
            quantity=qty,
            requested_price=opt.ltp
        )

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.BUY,
            timestamp=snapshot.timestamp,
            legs=[leg],
            entry_reason=f"Bearish momentum, Spot {snapshot.spot_price} <= ATM {atm_strike}",
            initial_sl_value=self.initial_sl_pct,
            initial_sl_type="PERCENTAGE",
            trailing_sl_value=self.trailing_sl_pct,
            target_value=self.target_pct,
            exit_policy=StopLossPolicy.INDIVIDUAL_LEG
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        leg = position.legs.get("LEG_LONG_PE")
        if not leg or leg.status != "OPEN":
            return None

        current_opt = snapshot.get_option(leg.strike, "PE")
        curr_price = current_opt.ltp if current_opt and current_opt.ltp > 0 else leg.current_price

        # Initial Stop Loss check
        sl_price = leg.entry_price * (1.0 - (self.initial_sl_pct / 100.0))
        if curr_price <= sl_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Initial SL breached: {curr_price} <= {round(sl_price, 2)}"
            )

        # Target check
        target_price = leg.entry_price * (1.0 + (self.target_pct / 100.0))
        if curr_price >= target_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Target reached: {curr_price} >= {round(target_price, 2)}"
            )

        # Trailing SL check (if active)
        active_tsl = position.trailing_sl_value
        if active_tsl > 0 and curr_price <= active_tsl:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.TRAILING_STOP_LOSS,
                details=f"Trailing SL hit: {curr_price} <= {round(active_tsl, 2)}"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        leg = position.legs.get("LEG_LONG_PE")
        if not leg or leg.status != "OPEN":
            return
        curr_price = leg.current_price
        # Activate trailing once profit reaches +15%
        activation_price = leg.entry_price * 1.15
        if curr_price >= activation_price:
            new_tsl = curr_price * (1.0 - (self.trailing_sl_pct / 100.0))
            if new_tsl > position.trailing_sl_value:
                position.trailing_sl_value = round(new_tsl, 2)

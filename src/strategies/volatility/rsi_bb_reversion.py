"""
rsi_bb_reversion.py - STRAT_021
Independent implementation of RSI + Bollinger Band Mean Reversion Strategy.
Enters counter-trend option positions when statistical price extremes coincide with momentum exhaustion.
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
from src.calculations.trend_calculator import calculate_rsi, calculate_bollinger_bands

class RSIBBMeanReversionStrategy(StrategyBase):
    """
    STRAT_021: RSI + Bollinger Band Mean Reversion Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_021",
            name="RSI + Bollinger Band Mean Reversion",
            cluster="volatility",
            config=config or {},
            workspace_root=workspace_root
        )
        self.rsi_period = int(self.config.get("rsi_period", 14))
        self.bb_period = int(self.config.get("bb_period", 20))
        self.bb_std = float(self.config.get("bb_std", 2.0))
        self.sl_pct = float(self.config.get("sl_pct", 25.0))
        self.target_pct = float(self.config.get("target_pct", 45.0))
        self.price_history: List[float] = []

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        spot = snapshot.spot_price
        if spot <= 0:
            return None

        self.price_history.append(spot)
        if len(self.price_history) > 60:
            self.price_history.pop(0)

        if len(self.price_history) < max(self.rsi_period, self.bb_period):
            return None

        rsi = calculate_rsi(self.price_history, self.rsi_period)
        bb = calculate_bollinger_bands(self.price_history, self.bb_period, self.bb_std)

        atm_strike = get_atm_strike(spot)
        qty = self.lot_size * self.max_lots

        # Oversold Reversion: RSI < 30 and Spot <= Lower Band -> Buy Call
        if rsi < 30.0 and spot <= bb["lower"]:
            opt = snapshot.get_option(atm_strike, "CE")
            if not opt or opt.ltp <= 0:
                return None
            leg = LegOrder("LEG_MR_BUY_CE", opt.symbol, atm_strike, "CE", opt.expiry, "BUY", qty, opt.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Mean Reversion Oversold: RSI {rsi} < 30, Spot {spot} <= Lower BB {bb['lower']}",
                initial_sl_value=self.sl_pct,
                initial_sl_type="PERCENTAGE",
                target_value=self.target_pct,
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG
            )

        # Overbought Reversion: RSI > 70 and Spot >= Upper Band -> Buy Put
        if rsi > 70.0 and spot >= bb["upper"]:
            opt = snapshot.get_option(atm_strike, "PE")
            if not opt or opt.ltp <= 0:
                return None
            leg = LegOrder("LEG_MR_BUY_PE", opt.symbol, atm_strike, "PE", opt.expiry, "BUY", qty, opt.ltp)
            return StrategySignal(
                strategy_id=self.strategy_id,
                direction=SignalDirection.BUY,
                timestamp=snapshot.timestamp,
                legs=[leg],
                entry_reason=f"Mean Reversion Overbought: RSI {rsi} > 70, Spot {spot} >= Upper BB {bb['upper']}",
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
                details=f"RSI-BB SL hit: {curr_price} <= {round(sl_price, 2)}"
            )

        # Target check
        target_price = leg.entry_price * (1.0 + (self.target_pct / 100.0))
        if curr_price >= target_price:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"RSI-BB target reached: {curr_price} >= {round(target_price, 2)}"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

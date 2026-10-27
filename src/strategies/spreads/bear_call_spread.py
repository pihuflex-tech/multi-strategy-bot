"""
bear_call_spread.py - STRAT_017
Independent implementation of 2-leg Bear Call Credit Spread Strategy.
Sells OTM Call, Buys Outer Wing Call for defined tail risk protection.
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

class BearCallCreditSpreadStrategy(StrategyBase):
    """
    STRAT_017: Bear Call Credit Spread Strategy (Defined Risk Credit Spread)
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_017",
            name="Bear Call Credit Spread",
            cluster="spreads",
            config=config or {},
            workspace_root=workspace_root
        )
        self.short_offset = int(self.config.get("short_offset", 100))
        self.wing_offset = int(self.config.get("wing_offset", 200))
        self.combined_sl_pct = float(self.config.get("combined_sl_pct", 35.0))
        self.target_pct = float(self.config.get("target_pct", 60.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price)
        short_strike = atm_strike + self.short_offset
        wing_strike = atm_strike + self.wing_offset

        short_ce = snapshot.get_option(short_strike, "CE")
        wing_ce = snapshot.get_option(wing_strike, "CE")
        if not short_ce or not wing_ce or short_ce.ltp <= 0 or wing_ce.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_short = LegOrder("LEG_SELL_SHORT_CE", short_ce.symbol, short_strike, "CE", short_ce.expiry, "SELL", qty, short_ce.ltp)
        leg_wing = LegOrder("LEG_BUY_WING_CE", wing_ce.symbol, wing_strike, "CE", wing_ce.expiry, "BUY", qty, wing_ce.ltp)

        net_credit_pts = short_ce.ltp - wing_ce.ltp
        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.SELL,
            timestamp=snapshot.timestamp,
            legs=[leg_short, leg_wing],
            entry_reason=f"Bear Call Credit Spread: Sell {short_strike} CE @ {short_ce.ltp}, Buy {wing_strike} CE @ {wing_ce.ltp}",
            initial_sl_value=self.combined_sl_pct,
            initial_sl_type="COMBINED",
            target_value=self.target_pct,
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            metadata={"net_credit_pts": net_credit_pts}
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        total_credit = abs(position.entry_debit_or_credit)
        if total_credit <= 0:
            return None

        unrealized = position.combined_unrealized_pnl
        loss_pct = (-unrealized / total_credit) * 100.0 if unrealized < 0 else 0.0
        profit_pct = (unrealized / total_credit) * 100.0 if unrealized > 0 else 0.0

        if loss_pct >= self.combined_sl_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Credit spread SL hit: loss {round(loss_pct, 2)}% >= {self.combined_sl_pct}%"
            )

        if profit_pct >= self.target_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Credit spread target reached: profit {round(profit_pct, 2)}% >= {self.target_pct}%"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

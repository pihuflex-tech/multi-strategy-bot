"""
bull_put_spread.py - STRAT_018
Independent implementation of 2-leg Bull Put Credit Spread Strategy.
Sells OTM Put, Buys Outer Wing Put for defined tail risk protection.
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

class BullPutCreditSpreadStrategy(StrategyBase):
    """
    STRAT_018: Bull Put Credit Spread Strategy (Defined Risk Credit Spread)
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_018",
            name="Bull Put Credit Spread",
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
        short_strike = atm_strike - self.short_offset
        wing_strike = atm_strike - self.wing_offset

        short_pe = snapshot.get_option(short_strike, "PE")
        wing_pe = snapshot.get_option(wing_strike, "PE")
        if not short_pe or not wing_pe or short_pe.ltp <= 0 or wing_pe.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_short = LegOrder("LEG_SELL_SHORT_PE", short_pe.symbol, short_strike, "PE", short_pe.expiry, "SELL", qty, short_pe.ltp)
        leg_wing = LegOrder("LEG_BUY_WING_PE", wing_pe.symbol, wing_strike, "PE", wing_pe.expiry, "BUY", qty, wing_pe.ltp)

        net_credit_pts = short_pe.ltp - wing_pe.ltp
        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.SELL,
            timestamp=snapshot.timestamp,
            legs=[leg_short, leg_wing],
            entry_reason=f"Bull Put Credit Spread: Sell {short_strike} PE @ {short_pe.ltp}, Buy {wing_strike} PE @ {wing_pe.ltp}",
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

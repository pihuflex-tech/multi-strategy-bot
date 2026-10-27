"""
bear_put_spread.py - STRAT_016
Independent implementation of 2-leg Bear Put Spread Strategy (Debit Spread).
Buys ATM Put, Sells OTM Put (-spread_width).
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

class BearPutSpreadStrategy(StrategyBase):
    """
    STRAT_016: Bear Put Spread Strategy (Debit Spread)
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_016",
            name="Bear Put Spread",
            cluster="spreads",
            config=config or {},
            workspace_root=workspace_root
        )
        self.spread_width = int(self.config.get("spread_width", 100))
        self.combined_sl_pct = float(self.config.get("combined_sl_pct", 30.0))
        self.target_pct = float(self.config.get("target_pct", 50.0))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price)
        otm_strike = atm_strike - self.spread_width

        buy_pe = snapshot.get_option(atm_strike, "PE")
        sell_pe = snapshot.get_option(otm_strike, "PE")
        if not buy_pe or not sell_pe or buy_pe.ltp <= 0 or sell_pe.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_buy = LegOrder("LEG_BUY_ATM_PE", buy_pe.symbol, atm_strike, "PE", buy_pe.expiry, "BUY", qty, buy_pe.ltp)
        leg_sell = LegOrder("LEG_SELL_OTM_PE", sell_pe.symbol, otm_strike, "PE", sell_pe.expiry, "SELL", qty, sell_pe.ltp)

        net_debit_pts = buy_pe.ltp - sell_pe.ltp
        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.BUY,
            timestamp=snapshot.timestamp,
            legs=[leg_buy, leg_sell],
            entry_reason=f"Bear Put Spread: Buy {atm_strike} PE @ {buy_pe.ltp}, Sell {otm_strike} PE @ {sell_pe.ltp}",
            initial_sl_value=self.combined_sl_pct,
            initial_sl_type="COMBINED",
            target_value=self.target_pct,
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            metadata={"net_debit_pts": net_debit_pts}
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        total_entry_cost = position.entry_debit_or_credit
        if total_entry_cost <= 0:
            return None

        unrealized = position.combined_unrealized_pnl
        loss_pct = (-unrealized / total_entry_cost) * 100.0 if unrealized < 0 else 0.0
        profit_pct = (unrealized / total_entry_cost) * 100.0 if unrealized > 0 else 0.0

        if loss_pct >= self.combined_sl_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS,
                details=f"Debit spread SL hit: loss {round(loss_pct, 2)}% >= {self.combined_sl_pct}%"
            )

        if profit_pct >= self.target_pct:
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Debit spread target reached: profit {round(profit_pct, 2)}% >= {self.target_pct}%"
            )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        pass

"""
short_straddle.py - STRAT_005
Independent implementation of Short Straddle Strategy (2-leg selling: ATM CE + ATM PE).
Features individual leg stop-loss with shift-to-cost on first leg SL, and combined target exit.
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

class ShortStraddleStrategy(StrategyBase):
    """
    STRAT_005: Short Straddle Strategy
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None, workspace_root: str = "."):
        super().__init__(
            strategy_id="STRAT_005",
            name="Short Straddle",
            cluster="straddle",
            config=config or {},
            workspace_root=workspace_root
        )
        self.leg_sl_pct = float(self.config.get("leg_sl_pct", 25.0))
        self.target_pct = float(self.config.get("target_pct", 50.0))
        self.shift_to_cost_on_first_sl = bool(self.config.get("shift_to_cost_on_first_sl", True))

    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        atm_strike = get_atm_strike(snapshot.spot_price)
        ce = snapshot.get_option(atm_strike, "CE")
        pe = snapshot.get_option(atm_strike, "PE")
        if not ce or not pe or ce.ltp <= 0 or pe.ltp <= 0:
            return None

        qty = self.lot_size * self.max_lots
        leg_ce = LegOrder("LEG_SELL_CE", ce.symbol, atm_strike, "CE", ce.expiry, "SELL", qty, ce.ltp)
        leg_pe = LegOrder("LEG_SELL_PE", pe.symbol, atm_strike, "PE", pe.expiry, "SELL", qty, pe.ltp)

        signal = StrategySignal(
            strategy_id=self.strategy_id,
            direction=SignalDirection.SELL,
            timestamp=snapshot.timestamp,
            legs=[leg_ce, leg_pe],
            entry_reason=f"Range-bound premium collection, ATM {atm_strike} combined credit {ce.ltp + pe.ltp}",
            initial_sl_value=self.leg_sl_pct,
            initial_sl_type="PERCENTAGE",
            target_value=self.target_pct,
            exit_policy=StopLossPolicy.INDIVIDUAL_LEG_WITH_SHIFT_TO_COST
        )
        return signal

    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        open_legs = [l for l in position.legs.values() if l.status == "OPEN"]
        closed_legs = [l for l in position.legs.values() if l.status == "CLOSED"]

        # Combined Target Check (50% decay of total initial credit)
        total_credit = abs(position.entry_debit_or_credit)
        if total_credit > 0 and position.combined_unrealized_pnl >= (total_credit * (self.target_pct / 100.0)):
            return ExitSignal(
                position_id=position.position_id,
                strategy_id=self.strategy_id,
                timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET,
                details=f"Combined 50% target reached: profit {position.combined_unrealized_pnl} >= {total_credit * 0.5}"
            )

        # Individual Leg Stop Loss Check
        for leg in open_legs:
            current_opt = snapshot.get_option(leg.strike, leg.option_type)
            curr_price = current_opt.ltp if current_opt and current_opt.ltp > 0 else leg.current_price

            # Determine stop-loss price
            is_shifted_to_cost = position.metadata.get(f"{leg.leg_id}_shifted_to_cost", False)
            if is_shifted_to_cost:
                sl_price = leg.entry_price  # Shifted to cost
            else:
                sl_price = leg.entry_price * (1.0 + (self.leg_sl_pct / 100.0))

            if curr_price >= sl_price:
                # Close this specific leg
                return ExitSignal(
                    position_id=position.position_id,
                    strategy_id=self.strategy_id,
                    timestamp=snapshot.timestamp,
                    reason=ExitReason.STOP_LOSS,
                    legs_to_close=[leg.leg_id],
                    details=f"Leg {leg.leg_id} SL hit: {curr_price} >= {round(sl_price, 2)}"
                )

        return None

    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        # Shift-to-cost handler: if 1 leg is closed due to SL, shift surviving open leg SL to cost
        closed_legs = [l for l in position.legs.values() if l.status == "CLOSED"]
        open_legs = [l for l in position.legs.values() if l.status == "OPEN"]

        if self.shift_to_cost_on_first_sl and len(closed_legs) == 1 and len(open_legs) == 1:
            surviving_leg = open_legs[0]
            flag_key = f"{surviving_leg.leg_id}_shifted_to_cost"
            if not position.metadata.get(flag_key, False):
                position.metadata[flag_key] = True
                self.log_event("SHIFT_TO_COST_APPLIED", {
                    "position_id": position.position_id,
                    "surviving_leg": surviving_leg.leg_id,
                    "new_sl_price": surviving_leg.entry_price
                })

"""
pnl_calculator.py
Accurate P&L engine supporting single-leg and multi-leg options/futures accounting.
Implements the exact formula: Unrealized P&L = sum(q_i * (M_i - E_i)) with signed quantities.
"""

from typing import List, Dict, Any, Optional

def calculate_leg_pnl(
    entry_price: float,
    current_price: float,
    quantity: int,
    side: str
) -> float:
    """
    Computes unrealized or realized gross P&L for a single leg.
    side: 'BUY' or 'SELL'.
    """
    if quantity <= 0:
        return 0.0
    if side.upper() == "BUY":
        return round((current_price - entry_price) * quantity, 2)
    else:
        return round((entry_price - current_price) * quantity, 2)

def calculate_combined_position_pnl(legs: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Computes combined multi-leg P&L across all active or closed legs.
    Each leg dict must contain:
      - 'side': 'BUY' or 'SELL'
      - 'entry_price': float
      - 'current_price': float
      - 'quantity': int
    Returns:
      {
        'gross_unrealized_pnl': float,
        'entry_cost': float,
        'current_value': float,
        'net_debit_or_credit': float
      }
    """
    total_unrealized = 0.0
    entry_cost = 0.0
    current_value = 0.0
    net_debit_credit = 0.0

    for leg in legs:
        side = leg["side"].upper()
        entry_p = float(leg["entry_price"])
        curr_p = float(leg["current_price"])
        qty = int(leg["quantity"])

        # Signed quantity convention: q_i > 0 for BUY, q_i < 0 for SELL
        signed_qty = qty if side == "BUY" else -qty

        leg_unrealized = signed_qty * (curr_p - entry_p)
        total_unrealized += leg_unrealized

        entry_cost += (entry_p * qty)
        current_value += (curr_p * qty)

        # Debit (cost out of pocket) if net buy; Credit (cash collected) if net sell
        net_debit_credit += (signed_qty * entry_p)

    return {
        "gross_unrealized_pnl": round(total_unrealized, 2),
        "entry_cost": round(entry_cost, 2),
        "current_value": round(current_value, 2),
        "net_debit_or_credit": round(net_debit_credit, 2)
    }

def calculate_spread_defined_risk(
    spread_type: str,                 # "BULL_CALL_SPREAD", "BEAR_PUT_SPREAD", "CREDIT_SPREAD", "IRON_CONDOR"
    buy_strike: float,
    sell_strike: float,
    buy_premium: float,
    sell_premium: float,
    lot_size: int = 65
) -> Dict[str, float]:
    """
    Computes theoretical maximum profit, maximum loss, and breakeven points for 2-leg spreads.
    """
    strike_diff = abs(buy_strike - sell_strike)
    net_debit = buy_premium - sell_premium

    if "BULL_CALL" in spread_type or "BEAR_PUT" in spread_type:
        # Debit spread
        max_loss = max(0.0, net_debit) * lot_size
        max_profit = max(0.0, strike_diff - net_debit) * lot_size
    else:
        # Credit spread
        net_credit = sell_premium - buy_premium
        max_profit = max(0.0, net_credit) * lot_size
        max_loss = max(0.0, strike_diff - net_credit) * lot_size

    return {
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "risk_reward_ratio": round(max_profit / max(1.0, max_loss), 2)
    }

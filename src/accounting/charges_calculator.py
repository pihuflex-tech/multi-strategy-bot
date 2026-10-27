"""
charges_calculator.py
Accurate Indian Statutory Taxation & Transaction Charges Engine for NSE F&O derivatives.
Compliant with post-Budget 2024 Indian taxation structure.
"""

from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class StatutoryCharges:
    """Itemized breakdown of Indian F&O regulatory taxes and charges."""
    brokerage: float
    stt: float
    exchange_charges: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    total_charges: float

class ChargesCalculator:
    """
    Computes exact Indian statutory taxes, exchange fees, and brokerage per trade.
    """
    def __init__(
        self,
        brokerage_per_order: float = 20.0,
        stt_options_sell_pct: float = 0.10,
        stt_futures_sell_pct: float = 0.02,
        exchange_options_pct: float = 0.050,
        exchange_futures_pct: float = 0.00173,
        stamp_duty_options_buy_pct: float = 0.003,
        stamp_duty_futures_buy_pct: float = 0.002,
        sebi_per_crore: float = 10.0,
        gst_pct: float = 18.0
    ):
        self.brokerage_per_order = brokerage_per_order
        self.stt_options_sell_pct = stt_options_sell_pct / 100.0
        self.stt_futures_sell_pct = stt_futures_sell_pct / 100.0
        self.exchange_options_pct = exchange_options_pct / 100.0
        self.exchange_futures_pct = exchange_futures_pct / 100.0
        self.stamp_duty_options_buy_pct = stamp_duty_options_buy_pct / 100.0
        self.stamp_duty_futures_buy_pct = stamp_duty_futures_buy_pct / 100.0
        self.sebi_rate = sebi_per_crore / 10000000.0
        self.gst_rate = gst_pct / 100.0

    def calculate_trade_charges(
        self,
        instrument_type: str,         # "OPTIONS" or "FUTURES"
        entry_price: float,
        exit_price: float,
        quantity: int,
        side: str = "BUY"             # Direction of opening trade: "BUY" or "SELL"
    ) -> StatutoryCharges:
        """
        Calculates complete round-trip statutory charges for a single trade leg.
        """
        if quantity <= 0:
            return StatutoryCharges(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        entry_turnover = entry_price * quantity
        exit_turnover = exit_price * quantity
        total_turnover = entry_turnover + exit_turnover

        # Determine buy and sell turnovers
        if side.upper() == "BUY":
            buy_turnover = entry_turnover
            sell_turnover = exit_turnover
        else:
            buy_turnover = exit_turnover
            sell_turnover = entry_turnover

        # 1. Brokerage (2 orders: 1 entry, 1 exit)
        brokerage = 2.0 * self.brokerage_per_order

        # 2. STT (Securities Transaction Tax - strictly on SELL side)
        is_options = "OPTION" in instrument_type.upper()
        if is_options:
            stt = sell_turnover * self.stt_options_sell_pct
            exchange_charges = total_turnover * self.exchange_options_pct
            stamp_duty = buy_turnover * self.stamp_duty_options_buy_pct
        else:
            stt = sell_turnover * self.stt_futures_sell_pct
            exchange_charges = total_turnover * self.exchange_futures_pct
            stamp_duty = buy_turnover * self.stamp_duty_futures_buy_pct

        # 3. SEBI Turnover Charges
        sebi_charges = total_turnover * self.sebi_rate

        # 4. GST (18% on Brokerage + Exchange Charges + SEBI Charges)
        gst = (brokerage + exchange_charges + sebi_charges) * self.gst_rate

        total = brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst

        return StatutoryCharges(
            brokerage=round(brokerage, 2),
            stt=round(stt, 2),
            exchange_charges=round(exchange_charges, 2),
            sebi_charges=round(sebi_charges, 4),
            stamp_duty=round(stamp_duty, 2),
            gst=round(gst, 2),
            total_charges=round(total, 2)
        )

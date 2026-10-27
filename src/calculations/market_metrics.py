"""
market_metrics.py
Calculates VWAP, Put-Call Ratio (PCR), and aggregate market metrics in native Python.
"""

from typing import Dict, List, Optional
from src.market.market_snapshot import MarketSnapshot, OptionQuote

class VWAPTracker:
    """Tracks running volume-weighted average price across intraday bars."""
    def __init__(self):
        self.cumulative_pv: float = 0.0
        self.cumulative_volume: float = 0.0

    def update(self, price: float, volume: float) -> float:
        if volume > 0:
            self.cumulative_pv += (price * volume)
            self.cumulative_volume += volume
        return self.get_vwap(fallback_price=price)

    def get_vwap(self, fallback_price: float = 0.0) -> float:
        if self.cumulative_volume <= 0:
            return fallback_price
        return round(self.cumulative_pv / self.cumulative_volume, 2)

    def reset(self):
        self.cumulative_pv = 0.0
        self.cumulative_volume = 0.0

def calculate_pcr(option_chain: Dict[str, OptionQuote]) -> Dict[str, float]:
    """
    Computes Put-Call Ratio by Open Interest and Volume across the option chain.
    """
    total_pe_oi = 0
    total_ce_oi = 0
    total_pe_vol = 0
    total_ce_vol = 0

    for quote in option_chain.values():
        if quote.option_type.upper() == "CE":
            total_ce_oi += quote.open_interest
            total_ce_vol += quote.volume
        elif quote.option_type.upper() == "PE":
            total_pe_oi += quote.open_interest
            total_pe_vol += quote.volume

    pcr_oi = (total_pe_oi / total_ce_oi) if total_ce_oi > 0 else 1.0
    pcr_vol = (total_pe_vol / total_ce_vol) if total_ce_vol > 0 else 1.0

    return {
        "pcr_oi": round(pcr_oi, 4),
        "pcr_volume": round(pcr_vol, 4),
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi
    }

class MarketMetrics:
    """Convenience class providing static calculation methods for market metrics."""
    @staticmethod
    def calculate_vwap(prices: List[float], volumes: List[float]) -> float:
        tracker = VWAPTracker()
        for p, v in zip(prices, volumes):
            tracker.update(p, v)
        return tracker.get_vwap()

    @staticmethod
    def calculate_pcr(put_oi: int, call_oi: int) -> float:
        return round(put_oi / call_oi, 4) if call_oi > 0 else 1.0

"""
trend_calculator.py
Native Python implementation of technical trend, momentum, and volatility indicators.
Includes EMA, RSI, Bollinger Bands, Donchian Channel, and Supertrend.
Uses collections.deque or standard lists without Pandas or NumPy.
"""

from typing import List, Tuple, Dict, Optional, Any
import math

def calculate_ema(values: List[float], period: int, previous_ema: Optional[float] = None) -> float:
    """
    Calculates Exponential Moving Average (EMA).
    If previous_ema is provided, computes incremental EMA:
        EMA_t = (Price_t * k) + (EMA_{t-1} * (1 - k))
    where k = 2 / (period + 1).
    """
    if not values:
        return 0.0
    k = 2.0 / (period + 1.0)
    if previous_ema is not None and previous_ema > 0:
        return (values[-1] * k) + (previous_ema * (1.0 - k))

    # Initialize from simple moving average if previous_ema is None
    if len(values) < period:
        return sum(values) / len(values)
    sma = sum(values[:period]) / period
    ema = sma
    for val in values[period:]:
        ema = (val * k) + (ema * (1.0 - k))
    return round(ema, 2)

def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculates Relative Strength Index (RSI).
    """
    if len(prices) <= period:
        return 50.0  # Neutral default

    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    # Initial average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed averages (Wilder's smoothing)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)

def calculate_bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Dict[str, float]:
    """
    Calculates Bollinger Bands: Middle Band (SMA), Upper Band, Lower Band, Bandwidth.
    """
    if len(prices) < period:
        avg = sum(prices) / max(1, len(prices))
        return {"middle": avg, "upper": avg, "lower": avg, "bandwidth": 0.0}

    subset = prices[-period:]
    mean = sum(subset) / period
    variance = sum((p - mean) ** 2 for p in subset) / period
    std_dev = math.sqrt(variance)

    upper = mean + (num_std * std_dev)
    lower = mean - (num_std * std_dev)
    bandwidth = (upper - lower) / mean if mean > 0 else 0.0

    return {
        "middle": round(mean, 2),
        "upper": round(upper, 2),
        "lower": round(lower, 2),
        "bandwidth": round(bandwidth, 4)
    }

def calculate_donchian_channel(highs: List[float], lows: List[float], period: int = 20) -> Dict[str, float]:
    """
    Calculates Donchian Channel: Upper Channel (max high of period),
    Lower Channel (min low of period), Middle Channel (average of upper & lower).
    """
    if not highs or not lows:
        return {"upper": 0.0, "lower": 0.0, "middle": 0.0}

    sub_highs = highs[-period:]
    sub_lows = lows[-period:]
    upper = max(sub_highs)
    lower = min(sub_lows)
    middle = (upper + lower) / 2.0

    return {
        "upper": round(upper, 2),
        "lower": round(lower, 2),
        "middle": round(middle, 2)
    }

def calculate_supertrend(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 10,
    multiplier: float = 3.0
) -> Dict[str, Any]:
    """
    Calculates Supertrend indicator in native Python.
    Returns: {'supertrend': float, 'direction': 1 (Bullish) or -1 (Bearish)}
    """
    n = len(closes)
    if n < period:
        c = closes[-1] if closes else 0.0
        return {"supertrend": c, "direction": 1}

    # True Range calculation
    tr_list = []
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

    # ATR
    if len(tr_list) < period:
        atr = sum(tr_list) / max(1, len(tr_list))
    else:
        atr = sum(tr_list[:period]) / period
        for tr in tr_list[period:]:
            atr = (atr * (period - 1) + tr) / period

    curr_high = highs[-1]
    curr_low = lows[-1]
    curr_close = closes[-1]
    hl2 = (curr_high + curr_low) / 2.0

    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    # Simplified instantaneous state
    if curr_close > basic_upper:
        direction = 1
        st = basic_lower
    elif curr_close < basic_lower:
        direction = -1
        st = basic_upper
    else:
        direction = 1 if curr_close >= hl2 else -1
        st = basic_lower if direction == 1 else basic_upper

    return {
        "supertrend": round(st, 2),
        "direction": direction,
        "atr": round(atr, 2)
    }

class TrendCalculator:
    """Convenience class providing static methods for trend & volatility indicators."""
    @staticmethod
    def ema(values: List[float], period: int, previous_ema: Optional[float] = None) -> float:
        return calculate_ema(values, period, previous_ema)

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> float:
        return calculate_rsi(prices, period)

    @staticmethod
    def bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Tuple[float, float, float]:
        bb = calculate_bollinger_bands(prices, period, num_std)
        return bb["upper"], bb["middle"], bb["lower"]

    @staticmethod
    def donchian_channel(highs: List[float], lows: List[float], period: int = 20) -> Tuple[float, float, float]:
        dc = calculate_donchian_channel(highs, lows, period)
        return dc["upper"], dc["lower"], dc["middle"]

    @staticmethod
    def supertrend(highs: List[float], lows: List[float], closes: List[float], period: int = 7, multiplier: float = 3.0) -> Tuple[float, int]:
        st = calculate_supertrend(highs, lows, closes, period, multiplier)
        return st["supertrend"], st["direction"]

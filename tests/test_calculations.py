"""
test_calculations.py
Deterministic offline unit tests for native quantitative calculators:
- ATMCalculator
- GreeksCalculator (pure Python Black-Scholes)
- IVCalculator (Newton-Raphson & bisection)
- TrendCalculator (EMA, RSI, Bollinger Bands, Donchian, Supertrend)
- FuturesBasisCalculator
- MarketMetrics (VWAP & PCR)
"""

import math
import unittest
from src.calculations.atm_calculator import ATMCalculator
from src.calculations.greeks_calculator import GreeksCalculator
from src.calculations.iv_calculator import IVCalculator
from src.calculations.trend_calculator import TrendCalculator
from src.calculations.futures_basis import FuturesBasisCalculator
from src.calculations.market_metrics import MarketMetrics


class TestQuantitativeCalculators(unittest.TestCase):
    """Offline unit tests for pure Python quantitative calculators."""

    def test_atm_calculator(self):
        """Test ATM strike rounding and strike ladder generation."""
        # NIFTY spot 24823 -> ATM 24800 (interval 50)
        self.assertEqual(ATMCalculator.calculate_atm(24823.5, 50), 24800)
        self.assertEqual(ATMCalculator.calculate_atm(24830.0, 50), 24850)
        self.assertEqual(ATMCalculator.calculate_atm(24800.0, 50), 24800)

        ladder = ATMCalculator.strike_ladder(24800, 50, count=2)
        expected = [24700, 24750, 24800, 24850, 24900]
        self.assertEqual(ladder, expected)

    def test_greeks_calculator_call_and_put(self):
        """Test Black-Scholes Greeks bounds and behavior."""
        spot = 24800.0
        strike = 24800.0
        dte_years = 7.0 / 365.0  # 7 days to expiry
        sigma = 0.15  # 15% IV
        rate = 0.065  # 6.5% risk free

        # Call Greeks
        call_greeks = GreeksCalculator.calculate(spot, strike, dte_years, rate, sigma, "CE")
        self.assertGreater(call_greeks.price, 0.0)
        self.assertGreater(call_greeks.delta, 0.45)
        self.assertLess(call_greeks.delta, 0.60)
        self.assertGreater(call_greeks.gamma, 0.0)
        self.assertGreater(call_greeks.vega, 0.0)
        self.assertLess(call_greeks.theta, 0.0)

        # Put Greeks
        put_greeks = GreeksCalculator.calculate(spot, strike, dte_years, rate, sigma, "PE")
        self.assertGreater(put_greeks.price, 0.0)
        self.assertGreater(put_greeks.delta, -0.60)
        self.assertLess(put_greeks.delta, -0.40)
        self.assertGreater(put_greeks.gamma, 0.0)
        self.assertGreater(put_greeks.vega, 0.0)
        self.assertLess(put_greeks.theta, 0.0)

        # Put-Call Parity Check: C - P approx S - K*e^(-r*T)
        discount = math.exp(-rate * dte_years)
        parity_diff = (call_greeks.price - put_greeks.price) - (spot - strike * discount)
        self.assertAlmostEqual(parity_diff, 0.0, delta=1.0)

    def test_iv_calculator(self):
        """Test Implied Volatility solver inversion."""
        spot = 24800.0
        strike = 24800.0
        dte_years = 14.0 / 365.0
        known_sigma = 0.18
        rate = 0.065

        # Calculate theoretical price
        ref_call = GreeksCalculator.calculate(spot, strike, dte_years, rate, known_sigma, "CE")
        solved_iv = IVCalculator.calculate_iv(ref_call.price, spot, strike, dte_years, rate, "CE")

        self.assertIsNotNone(solved_iv)
        self.assertAlmostEqual(solved_iv, known_sigma, places=3)

    def test_trend_calculator_indicators(self):
        """Test pure Python EMA, RSI, Bollinger Bands, and Supertrend."""
        prices = [100.0, 102.0, 101.5, 103.0, 104.5, 104.0, 106.0, 107.5, 107.0, 109.0, 
                  110.5, 110.0, 112.0, 113.5, 115.0, 116.0, 115.5, 117.0, 118.5, 120.0]

        # EMA
        ema_val = TrendCalculator.ema(prices, period=5)
        self.assertIsNotNone(ema_val)
        self.assertGreater(ema_val, 110.0)

        # RSI
        rsi_val = TrendCalculator.rsi(prices, period=14)
        self.assertIsNotNone(rsi_val)
        self.assertGreaterEqual(rsi_val, 0.0)
        self.assertLessEqual(rsi_val, 100.0)
        self.assertGreater(rsi_val, 70.0)  # strongly rising series

        # Bollinger Bands
        upper, mid, lower = TrendCalculator.bollinger_bands(prices, period=10, num_std=2.0)
        self.assertIsNotNone(upper)
        self.assertGreater(upper, mid)
        self.assertGreater(mid, lower)

        # Donchian Channel
        highs = [p + 1.0 for p in prices]
        lows = [p - 1.0 for p in prices]
        donch_upper, donch_lower, donch_mid = TrendCalculator.donchian_channel(highs, lows, period=10)
        self.assertEqual(donch_upper, max(highs[-10:]))
        self.assertEqual(donch_lower, min(lows[-10:]))
        self.assertEqual(donch_mid, (donch_upper + donch_lower) / 2.0)

        # Supertrend
        closes = prices
        st_val, st_dir = TrendCalculator.supertrend(highs, lows, closes, period=7, multiplier=3.0)
        self.assertIn(st_dir, [1, -1])
        self.assertIsNotNone(st_val)

    def test_futures_basis_calculator(self):
        """Test futures basis and basis percentage calculation."""
        spot = 24800.0
        futures = 24880.0

        basis = FuturesBasisCalculator.calculate_basis(futures, spot)
        basis_pct = FuturesBasisCalculator.calculate_basis_pct(futures, spot)

        self.assertEqual(basis, 80.0)
        self.assertAlmostEqual(basis_pct, (80.0 / 24800.0) * 100.0, places=4)

    def test_market_metrics_vwap_and_pcr(self):
        """Test VWAP calculation and Put-Call Ratio."""
        # VWAP
        ticks = [
            (24800.0, 100),
            (24810.0, 200),
            (24820.0, 300)
        ]
        prices = [t[0] for t in ticks]
        volumes = [t[1] for t in ticks]
        expected_vwap = (24800*100 + 24810*200 + 24820*300) / (100 + 200 + 300)
        calc_vwap = MarketMetrics.calculate_vwap(prices, volumes)
        self.assertAlmostEqual(calc_vwap, expected_vwap, places=2)

        # PCR
        pcr = MarketMetrics.calculate_pcr(put_oi=1500000, call_oi=1000000)
        self.assertEqual(pcr, 1.5)


if __name__ == "__main__":
    unittest.main()

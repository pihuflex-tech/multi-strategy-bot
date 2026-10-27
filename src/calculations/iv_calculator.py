"""
iv_calculator.py
Pure native Python Implied Volatility (IV) solver using Newton-Raphson with bisection fallback.
Fully independent of SciPy or external C-libraries.
"""

from src.calculations.greeks_calculator import calculate_black_scholes_price, norm_pdf
import math

def calculate_implied_volatility(
    price: float,
    spot: float,
    strike: float,
    tte_years: float,
    rate: float = 0.07,
    option_type: str = "CE",
    max_iterations: int = 50,
    tolerance: float = 1e-4
) -> float:
    """
    Computes Black-Scholes implied volatility for an option quote.
    Returns annualized IV as a decimal (e.g. 0.155 for 15.5%).
    Returns 0.0 if intrinsic value exceeds price or solver fails to converge.
    """
    if price <= 0 or spot <= 0 or strike <= 0 or tte_years <= 0:
        return 0.0

    # Intrinsic value check
    intrinsic = max(0.0, spot - strike) if option_type.upper() == "CE" else max(0.0, strike - spot)
    if price < intrinsic:
        return 0.0

    # Initial guess using Brenner-Subrahmanyam approximation
    sigma = math.sqrt(2.0 * math.pi / tte_years) * (price / spot)
    sigma = max(0.05, min(sigma, 2.5))  # Clamp initial guess to [5%, 250%]

    # Newton-Raphson iterations
    for _ in range(max_iterations):
        model_price = calculate_black_scholes_price(spot, strike, tte_years, sigma, rate, option_type)
        diff = model_price - price
        if abs(diff) < tolerance:
            return round(sigma, 4)

        # Vega calculation
        sigma_sqrt_t = sigma * math.sqrt(tte_years)
        if sigma_sqrt_t <= 0:
            break
        d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * tte_years) / sigma_sqrt_t
        vega = spot * norm_pdf(d1) * math.sqrt(tte_years)

        if vega < 1e-5:
            break  # Vega too small for Newton-Raphson

        sigma_new = sigma - diff / vega
        if sigma_new <= 0.01 or sigma_new > 5.0:
            break  # Stepped out of bounds
        sigma = sigma_new

    # Bisection fallback
    low_sigma = 0.01
    high_sigma = 3.0
    for _ in range(max_iterations):
        mid_sigma = 0.5 * (low_sigma + high_sigma)
        mid_price = calculate_black_scholes_price(spot, strike, tte_years, mid_sigma, rate, option_type)
        diff = mid_price - price
        if abs(diff) < tolerance:
            return round(mid_sigma, 4)
        if diff > 0:
            high_sigma = mid_sigma
        else:
            low_sigma = mid_sigma

    return round(mid_sigma, 4)

class IVCalculator:
    """Convenience class for implied volatility calculations."""
    @staticmethod
    def calculate_iv(
        price: float,
        spot: float,
        strike: float,
        tte_years: float,
        rate: float = 0.07,
        option_type: str = "CE"
    ) -> float:
        return calculate_implied_volatility(price, spot, strike, tte_years, rate, option_type)

"""
greeks_calculator.py
Pure native Python Black-Scholes Greeks calculator using standard library math.
Zero dependency on SciPy, NumPy, or C-extensions. Fully compatible with Android Termux.
"""

import math
from typing import Dict

def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def calculate_black_scholes_price(
    spot: float,
    strike: float,
    tte_years: float,
    volatility: float,
    rate: float = 0.07,
    option_type: str = "CE"
) -> float:
    """
    Computes theoretical Black-Scholes option price.
    """
    if spot <= 0 or strike <= 0 or volatility <= 0 or tte_years <= 0:
        # At expiration or boundary conditions
        if option_type.upper() == "CE":
            return max(0.0, spot - strike)
        else:
            return max(0.0, strike - spot)

    sigma_sqrt_t = volatility * math.sqrt(tte_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * tte_years) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t

    discount = math.exp(-rate * tte_years)
    if option_type.upper() == "CE":
        return spot * norm_cdf(d1) - strike * discount * norm_cdf(d2)
    else:
        return strike * discount * norm_cdf(-d2) - spot * norm_cdf(-d1)

def calculate_greeks(
    spot: float,
    strike: float,
    tte_years: float,
    volatility: float,
    rate: float = 0.07,
    option_type: str = "CE"
) -> Dict[str, float]:
    """
    Computes Black-Scholes Greeks: Delta, Gamma, Theta (per calendar day), and Vega (per 1% IV).
    Returns dict: {'delta': ..., 'gamma': ..., 'theta': ..., 'vega': ...}
    """
    greeks = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    if spot <= 0 or strike <= 0 or volatility <= 0 or tte_years <= 0:
        if option_type.upper() == "CE":
            greeks["delta"] = 1.0 if spot > strike else 0.0
        else:
            greeks["delta"] = -1.0 if spot < strike else 0.0
        return greeks

    sigma_sqrt_t = volatility * math.sqrt(tte_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * tte_years) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t

    pdf_d1 = norm_pdf(d1)
    discount = math.exp(-rate * tte_years)

    # Gamma (identical for Call and Put)
    gamma = pdf_d1 / (spot * sigma_sqrt_t)

    # Vega (per 1% change in IV: dPrice / dSigma * 0.01)
    vega = (spot * pdf_d1 * math.sqrt(tte_years)) * 0.01

    if option_type.upper() == "CE":
        delta = norm_cdf(d1)
        # Annualized Theta converted to 1-calendar-day decay
        theta_annual = -(spot * pdf_d1 * volatility) / (2.0 * math.sqrt(tte_years)) - rate * strike * discount * norm_cdf(d2)
        theta_day = theta_annual / 365.0
    else:
        delta = norm_cdf(d1) - 1.0
        theta_annual = -(spot * pdf_d1 * volatility) / (2.0 * math.sqrt(tte_years)) + rate * strike * discount * norm_cdf(-d2)
        theta_day = theta_annual / 365.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta_day, 4),
        "vega": round(vega, 4)
    }

from dataclasses import dataclass

@dataclass
class GreeksResult:
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float

class GreeksCalculator:
    """Convenience class providing unified pricing and Greeks calculation."""
    @staticmethod
    def calculate(
        spot: float,
        strike: float,
        dte_years: float,
        rate: float,
        sigma: float,
        option_type: str = "CE"
    ) -> GreeksResult:
        price = calculate_black_scholes_price(spot, strike, dte_years, sigma, rate, option_type)
        g = calculate_greeks(spot, strike, dte_years, sigma, rate, option_type)
        return GreeksResult(
            price=round(price, 2),
            delta=g["delta"],
            gamma=g["gamma"],
            theta=g["theta"],
            vega=g["vega"]
        )

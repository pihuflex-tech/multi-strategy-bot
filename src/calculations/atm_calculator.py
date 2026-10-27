"""
atm_calculator.py
Pure native Python ATM strike calculation and strike ladder utilities.
"""

def get_atm_strike(spot_price: float, strike_step: int = 50) -> int:
    """
    Computes the nearest ATM strike price for a given spot price and strike interval.
    e.g., spot=24824.50, step=50 -> 24800; spot=24826.00, step=50 -> 24850.
    """
    if spot_price <= 0:
        return 0
    return int(round(spot_price / float(strike_step))) * strike_step

def get_strike_ladder(atm_strike: int, num_strikes: int = 5, strike_step: int = 50):
    """
    Returns a symmetric ladder of strikes around the ATM strike.
    """
    ladder = []
    for i in range(-num_strikes, num_strikes + 1):
        ladder.append(atm_strike + (i * strike_step))
    return ladder

class ATMCalculator:
    """Utility class for ATM strike and ladder computations."""
    @staticmethod
    def calculate_atm(spot_price: float, strike_step: int = 50) -> int:
        return get_atm_strike(spot_price, strike_step)

    @staticmethod
    def strike_ladder(atm_strike: int, strike_step: int = 50, count: int = 5):
        return get_strike_ladder(atm_strike, num_strikes=count, strike_step=strike_step)

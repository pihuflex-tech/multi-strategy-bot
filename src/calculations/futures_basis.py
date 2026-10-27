"""
futures_basis.py
Computes index futures fair value and spot basis spread.
"""

from typing import Dict

def calculate_futures_basis(futures_price: float, spot_price: float) -> Dict[str, float]:
    """
    Computes absolute basis spread and percentage basis spread between futures and spot.
    Returns: {'basis': float, 'basis_pct': float}
    """
    if spot_price <= 0:
        return {"basis": 0.0, "basis_pct": 0.0}
    basis = futures_price - spot_price
    basis_pct = (basis / spot_price) * 100.0
    return {
        "basis": round(basis, 2),
        "basis_pct": round(basis_pct, 4)
    }

class FuturesBasisCalculator:
    """Convenience class for futures basis calculations."""
    @staticmethod
    def calculate_basis(futures_price: float, spot_price: float) -> float:
        return calculate_futures_basis(futures_price, spot_price)["basis"]

    @staticmethod
    def calculate_basis_pct(futures_price: float, spot_price: float) -> float:
        return calculate_futures_basis(futures_price, spot_price)["basis_pct"]

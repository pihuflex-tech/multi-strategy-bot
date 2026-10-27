"""
market_snapshot.py
Strongly-typed data structures for market quotes, option chains, and full market snapshots.
Fully native Python implementation without external quantitative dependencies.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import time

@dataclass
class OptionQuote:
    """Represents a single option contract quote."""
    token: str = ""
    symbol: str = ""
    strike_price: float = 0.0
    option_type: str = "CE"           # "CE" or "PE"
    expiry: str = ""                  # e.g. "2026-10-29"
    ltp: float = 0.0                  # Last Traded Price
    bid: float = 0.0                  # Best Bid
    ask: float = 0.0                  # Best Ask
    volume: int = 0                   # Cumulative Day Volume
    open_interest: int = 0            # Current Open Interest
    oi_change: int = 0                # Change in OI
    delta: float = 0.0                # Black-Scholes Delta
    gamma: float = 0.0                # Gamma
    theta: float = 0.0                # Theta
    vega: float = 0.0                 # Vega
    iv: float = 0.0                   # Implied Volatility
    timestamp: float = field(default_factory=time.time)
    strike: float = 0.0
    oi: int = 0

    def __post_init__(self):
        if self.strike > 0 and self.strike_price == 0:
            self.strike_price = self.strike
        elif self.strike_price > 0 and self.strike == 0:
            self.strike = self.strike_price
        if self.oi > 0 and self.open_interest == 0:
            self.open_interest = self.oi
        elif self.open_interest > 0 and self.oi == 0:
            self.oi = self.open_interest

@dataclass
class FuturesQuote:
    """Represents a futures contract quote."""
    token: str = ""
    symbol: str = ""
    expiry: str = ""
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open_interest: int = 0
    oi: int = 0
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.oi > 0 and self.open_interest == 0:
            self.open_interest = self.oi
        elif self.open_interest > 0 and self.oi == 0:
            self.oi = self.open_interest

@dataclass
class MarketSnapshot:
    """Normalized snapshot of market state at a specific timestamp."""
    timestamp: str = ""                   # "YYYY-MM-DD HH:MM:SS" or ISO string
    spot_price: float = 0.0               # NIFTY Spot LTP
    futures: Optional[FuturesQuote] = None
    india_vix: float = 0.0
    option_chain: Dict[str, OptionQuote] = field(default_factory=dict) # Key: e.g. "25000_CE", "25000_PE"
    current_expiry: str = ""
    next_expiry: str = ""
    monthly_expiry: str = ""
    is_stale: bool = False
    data_status: str = "VALID"        # "VALID", "DEGRADED", "STALE", "DISCONNECTED"
    spot: float = 0.0
    options: Optional[Dict[str, OptionQuote]] = None
    receipt_monotonic: Optional[float] = None

    def __post_init__(self):
        if self.spot > 0 and self.spot_price == 0:
            self.spot_price = self.spot
        elif self.spot_price > 0 and self.spot == 0:
            self.spot = self.spot_price

        if self.options and not self.option_chain:
            self.option_chain = self.options
        elif self.option_chain and not self.options:
            self.options = self.option_chain

    def add_option(self, quote: OptionQuote, underlying: str = "NIFTY"):
        """Adds an option contract quote using an expiry-safe composite key."""
        s_int = int(round(quote.strike if quote.strike > 0 else quote.strike_price))
        opt_type = quote.option_type.upper()
        if quote.expiry:
            key = f"{s_int}_{opt_type}_{quote.expiry}"
        else:
            key = f"{s_int}_{opt_type}"
        self.option_chain[key] = quote
        if self.options is not None:
            self.options[key] = quote

    def get_option(self, strike: float, option_type: str, expiry: Optional[str] = None) -> Optional[OptionQuote]:
        """
        Retrieve an option quote by strike, type, and exact expiry.
        Prevents cross-expiry overwrites and contract identity ambiguity.
        Never silently substitutes contracts from different expiries.
        """
        s_int = int(round(strike))
        opt_type = option_type.upper()

        if expiry:
            # 1. Direct compound key lookup
            compound_key = f"{s_int}_{opt_type}_{expiry}"
            if compound_key in self.option_chain:
                return self.option_chain[compound_key]

            # 2. Linear scan for matching attributes
            for k, q in self.option_chain.items():
                q_strike = int(round(q.strike if q.strike > 0 else q.strike_price))
                if q_strike == s_int and q.option_type.upper() == opt_type and q.expiry == expiry:
                    return q

            # Exact expiry requested but not found -> NEVER substitute!
            return None

        # Expiry is None:
        # Check if current_expiry is set on the snapshot
        if self.current_expiry:
            compound_key = f"{s_int}_{opt_type}_{self.current_expiry}"
            if compound_key in self.option_chain:
                return self.option_chain[compound_key]
            for k, q in self.option_chain.items():
                q_strike = int(round(q.strike if q.strike > 0 else q.strike_price))
                if q_strike == s_int and q.option_type.upper() == opt_type and q.expiry == self.current_expiry:
                    return q

        # Fallback to legacy single-expiry key if present
        legacy_key = f"{s_int}_{opt_type}"
        if legacy_key in self.option_chain:
            return self.option_chain[legacy_key]

        # Scan for matches in chain: only return if unambiguous (exactly 1 match)
        matching = []
        for k, q in self.option_chain.items():
            q_strike = int(round(q.strike if q.strike > 0 else q.strike_price))
            if q_strike == s_int and q.option_type.upper() == opt_type:
                matching.append(q)

        if len(matching) == 1:
            return matching[0]

        # If multiple expiries exist and none was specified -> ambiguous, return None
        return None

    def get_atm_strike(self, step: int = 50) -> int:
        """Calculate the nearest ATM strike."""
        if self.spot_price <= 0:
            return 0
        return int(round(self.spot_price / float(step))) * step

    def get_combined_straddle_premium(self, strike: Optional[float] = None) -> float:
        """Calculate combined ATM straddle premium (CE LTP + PE LTP)."""
        atm = strike if strike is not None else self.get_atm_strike()
        ce = self.get_option(atm, "CE")
        pe = self.get_option(atm, "PE")
        ce_price = ce.ltp if ce else 0.0
        pe_price = pe.ltp if pe else 0.0
        return ce_price + pe_price

# Type Aliases for contract structures
OptionContract = OptionQuote
FuturesContract = FuturesQuote

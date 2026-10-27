"""
data_validator.py
Validates schema integrity, data types, finite numbers, positive prices,
and valid option/futures contract attributes on parsed market-feed payloads.
Strictly decoupled: does not fabricate default Greeks, prices, or expiries.
Does not make timestamp freshness decisions (Phase 2 responsibility).
"""

import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union


def _is_valid_number(val: Any) -> bool:
    """Checks if value is a numeric int or float (excluding bool) and is finite."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    return math.isfinite(val)


def _is_valid_integer_count(val: Any) -> bool:
    """Checks if value is a non-negative integer count (excluding bool)."""
    if isinstance(val, bool) or not isinstance(val, int):
        return False
    return val >= 0


_ISO_EXPIRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NSE_EXPIRY_RE = re.compile(r"^\d{2}[A-Za-z]{3}\d{4}$")


def _is_valid_expiry(val: Any) -> bool:
    """
    Validates that expiry is a string matching either documented format:
    1. ISO format: 'YYYY-MM-DD' (e.g. '2026-10-29')
    2. NSE exchange format: 'DDMMMYYYY' (e.g. '28OCT2026')
    Rejects malformed dates, whitespace, non-strings, and ambiguous numeric date strings (e.g. DD/MM/YYYY).
    """
    if not isinstance(val, str) or not val:
        return False
    if _ISO_EXPIRY_RE.match(val):
        try:
            datetime.strptime(val, "%Y-%m-%d")
            return True
        except ValueError:
            return False
    if _NSE_EXPIRY_RE.match(val):
        try:
            datetime.strptime(val.upper(), "%d%b%Y")
            return True
        except ValueError:
            return False
    return False


class FeedDataValidator:
    """
    Validator for parsed market data payloads against the expected NIFTY feed schema.
    Enforces positive prices, finite numbers, valid strikes/option types, non-negative integer counts,
    documented expiry formats, and valid bid-ask relationships.
    """

    def __init__(self, require_expiry: bool = True, allow_empty_options: bool = True):
        """
        Initializes validator with policy flags.

        Parameters:
            require_expiry: If True, option contracts must have non-empty expiry information.
            allow_empty_options: If True, an empty options dictionary is accepted as a valid empty collection.
        """
        self.require_expiry = require_expiry
        self.allow_empty_options = allow_empty_options

    def validate(self, payload: Any) -> Tuple[bool, Optional[str]]:
        """
        Validates the parsed payload against schema requirements.

        Returns:
            Tuple of (is_valid: bool, error_message: Optional[str])
        """
        # 1. Root structure check
        if not isinstance(payload, dict):
            return False, f"INVALID_PAYLOAD_TYPE: Expected dict, got {type(payload).__name__}"

        # 2. Required top-level fields
        if "timestamp" not in payload:
            return False, "MISSING_FIELD: timestamp"
        ts = payload["timestamp"]
        if not isinstance(ts, str) or not ts.strip():
            return False, f"INVALID_TYPE: timestamp must be non-empty string, got {type(ts).__name__}"

        if "spot" not in payload:
            return False, "MISSING_FIELD: spot"
        spot = payload["spot"]
        if not isinstance(spot, dict):
            return False, f"INVALID_TYPE: spot must be dict, got {type(spot).__name__}"

        if "options" not in payload:
            return False, "MISSING_FIELD: options"
        options = payload["options"]
        if not isinstance(options, dict):
            return False, f"INVALID_TYPE: options must be dict, got {type(options).__name__}"

        # Validate top-level expiries if present
        for top_exp_key in ["current_expiry", "next_expiry", "monthly_expiry"]:
            if top_exp_key in payload and payload[top_exp_key] is not None:
                top_exp_val = payload[top_exp_key]
                if not isinstance(top_exp_val, str) or not _is_valid_expiry(top_exp_val):
                    return False, f"INVALID_EXPIRY_FORMAT: {top_exp_key} {top_exp_val!r} does not match documented ISO (YYYY-MM-DD) or NSE (DDMMMYYYY) format"

        # 3. Spot fields validation
        if "ltp" not in spot:
            return False, "MISSING_FIELD: spot.ltp"
        spot_ltp = spot["ltp"]
        if not _is_valid_number(spot_ltp):
            return False, f"INVALID_PRICE: spot.ltp must be a finite number, got {spot_ltp!r}"
        if spot_ltp <= 0.0:
            return False, f"INVALID_PRICE: spot.ltp must be positive (>0), got {spot_ltp}"

        if "symbol" in spot and not isinstance(spot["symbol"], str):
            return False, f"INVALID_TYPE: spot.symbol must be string, got {type(spot['symbol']).__name__}"

        for price_field in ["open", "high", "low", "close", "net_change"]:
            if price_field in spot and spot[price_field] is not None:
                if not _is_valid_number(spot[price_field]):
                    return False, f"NON_FINITE_VALUE: spot.{price_field} must be finite number, got {spot[price_field]!r}"

        for count_field in ["volume", "open_interest", "oi"]:
            if count_field in spot and spot[count_field] is not None:
                c_val = spot[count_field]
                if not _is_valid_integer_count(c_val):
                    return False, f"INVALID_COUNT: spot.{count_field} must be a non-negative integer, got {c_val!r}"

        # 4. Futures validation (if present)
        futures = payload.get("futures")
        if futures is not None:
            if not isinstance(futures, dict):
                return False, f"INVALID_TYPE: futures must be dict, got {type(futures).__name__}"

            near_fut = futures.get("near") if isinstance(futures.get("near"), dict) else None

            # Check LTP on futures root or near
            if "ltp" in futures and futures["ltp"] is not None:
                fut_ltp = futures["ltp"]
                if not _is_valid_number(fut_ltp):
                    return False, f"INVALID_PRICE: futures.ltp must be finite number, got {fut_ltp!r}"
                if fut_ltp <= 0.0:
                    return False, f"INVALID_PRICE: futures.ltp must be positive (>0), got {fut_ltp}"
            elif near_fut and "ltp" in near_fut and near_fut["ltp"] is not None:
                fut_ltp = near_fut["ltp"]
                if not _is_valid_number(fut_ltp):
                    return False, f"INVALID_PRICE: futures.near.ltp must be finite number, got {fut_ltp!r}"
                if fut_ltp <= 0.0:
                    return False, f"INVALID_PRICE: futures.near.ltp must be positive (>0), got {fut_ltp}"

            # Bid/ask, volume, oi, expiry checks across root and near
            for container, c_label in [(futures, "futures"), (near_fut, "futures.near")]:
                if container is None:
                    continue
                for fut_price in ["bid", "ask"]:
                    if fut_price in container and container[fut_price] is not None:
                        f_val = container[fut_price]
                        if not _is_valid_number(f_val) or f_val < 0.0:
                            return False, f"INVALID_PRICE: {c_label}.{fut_price} must be non-negative finite number, got {f_val!r}"

                fut_bid = container.get("bid")
                fut_ask = container.get("ask")
                if fut_bid is not None and fut_ask is not None:
                    if _is_valid_number(fut_bid) and _is_valid_number(fut_ask):
                        if fut_bid > 0.0 and fut_ask > 0.0 and fut_bid > fut_ask:
                            return False, f"INVALID_PRICE: Inverted futures bid-ask spread (bid {fut_bid} > ask {fut_ask})"

                for count_field in ["volume", "open_interest", "oi"]:
                    if count_field in container and container[count_field] is not None:
                        c_val = container[count_field]
                        if not _is_valid_integer_count(c_val):
                            return False, f"INVALID_COUNT: {c_label}.{count_field} must be a non-negative integer, got {c_val!r}"

                if "expiry" in container and container["expiry"] is not None and container["expiry"] != "":
                    fut_exp = container["expiry"]
                    if not isinstance(fut_exp, str) or not _is_valid_expiry(fut_exp):
                        return False, f"INVALID_EXPIRY_FORMAT: {c_label}.expiry {fut_exp!r} does not match documented ISO (YYYY-MM-DD) or NSE (DDMMMYYYY) format"

        # 5. Indices validation (if present)
        indices = payload.get("indices")
        if indices is not None:
            if not isinstance(indices, dict):
                return False, f"INVALID_TYPE: indices must be dict, got {type(indices).__name__}"
            vix = indices.get("india_vix")
            if vix is not None and isinstance(vix, dict) and "ltp" in vix:
                vix_ltp = vix["ltp"]
                if not _is_valid_number(vix_ltp) or vix_ltp <= 0.0:
                    return False, f"INVALID_PRICE: indices.india_vix.ltp must be positive finite number, got {vix_ltp!r}"

        # 6. Options collection validation
        if not options and not self.allow_empty_options:
            return False, "EMPTY_OPTIONS_COLLECTION: options container has no contracts"

        # Inspect contracts (supports both bucketed: {"current_expiry": {"24800_CE": {...}}} and flat: {"24800_CE": {...}})
        contracts_to_validate: List[Tuple[str, Dict[str, Any], Optional[str]]] = []
        for key, val in options.items():
            if not isinstance(val, dict):
                return False, f"INVALID_TYPE: options entry '{key}' must be dict, got {type(val).__name__}"

            # Check if val is a contract quote or a bucket of contracts
            if "strike" in val or "strike_price" in val or "option_type" in val:
                contracts_to_validate.append((key, val, payload.get("current_expiry")))
            else:
                # Sub-bucket (e.g. current_expiry, wings, next_expiry, monthly_expiry, or explicit date key)
                bucket_expiry = payload.get(key) if key in payload else (key if ("-" in key or "/" in key) else None)
                if not bucket_expiry and key == "wings":
                    bucket_expiry = payload.get("current_expiry")
                for ckey, cval in val.items():
                    if not isinstance(cval, dict):
                        return False, f"INVALID_TYPE: Contract '{ckey}' in bucket '{key}' must be dict, got {type(cval).__name__}"
                    contracts_to_validate.append((ckey, cval, bucket_expiry))

        top_current_expiry = payload.get("current_expiry")

        for ckey, contract, parent_expiry in contracts_to_validate:
            # Strike validation
            strike = contract.get("strike", contract.get("strike_price"))
            if strike is None:
                return False, f"MISSING_FIELD: Contract '{ckey}' missing strike/strike_price"
            if not _is_valid_number(strike):
                return False, f"INVALID_STRIKE: Contract '{ckey}' strike must be finite number, got {strike!r}"
            if strike <= 0.0:
                return False, f"INVALID_STRIKE: Contract '{ckey}' strike must be positive (>0), got {strike}"

            # Option type validation
            opt_type = contract.get("option_type")
            if opt_type is None:
                return False, f"MISSING_FIELD: Contract '{ckey}' missing option_type"
            if not isinstance(opt_type, str):
                return False, f"INVALID_OPTION_TYPE: Contract '{ckey}' option_type must be string, got {type(opt_type).__name__}"
            if opt_type.upper() not in ["CE", "PE"]:
                return False, f"INVALID_OPTION_TYPE: Contract '{ckey}' option_type must be 'CE' or 'PE', got {opt_type!r}"

            # LTP validation
            if "ltp" not in contract:
                return False, f"MISSING_FIELD: Contract '{ckey}' missing ltp"
            opt_ltp = contract["ltp"]
            if not _is_valid_number(opt_ltp):
                return False, f"INVALID_PRICE: Contract '{ckey}' ltp must be finite number, got {opt_ltp!r}"
            if opt_ltp <= 0.0:
                return False, f"INVALID_PRICE: Contract '{ckey}' ltp must be positive (>0), got {opt_ltp}"

            # Expiry validation
            raw_expiry = contract.get("expiry")
            if raw_expiry is not None:
                if not isinstance(raw_expiry, str) or not _is_valid_expiry(raw_expiry):
                    return False, f"INVALID_EXPIRY_FORMAT: Contract '{ckey}' expiry {raw_expiry!r} does not match documented ISO (YYYY-MM-DD) or NSE (DDMMMYYYY) format"
                resolved_expiry = raw_expiry
            else:
                inherited = parent_expiry or top_current_expiry
                if inherited is not None:
                    if not isinstance(inherited, str) or not _is_valid_expiry(inherited):
                        return False, f"INVALID_EXPIRY_FORMAT: Contract '{ckey}' inherited expiry {inherited!r} does not match documented ISO (YYYY-MM-DD) or NSE (DDMMMYYYY) format"
                    resolved_expiry = inherited
                else:
                    resolved_expiry = None

            if self.require_expiry and not resolved_expiry:
                return False, f"MISSING_EXPIRY: Contract '{ckey}' lacks valid expiry information"

            # Optional bid/ask checks
            for quote_field in ["bid", "ask"]:
                if quote_field in contract and contract[quote_field] is not None:
                    q_val = contract[quote_field]
                    if not _is_valid_number(q_val) or q_val < 0.0:
                        return False, f"INVALID_PRICE: Contract '{ckey}' {quote_field} must be non-negative finite number, got {q_val!r}"

            c_bid = contract.get("bid")
            c_ask = contract.get("ask")
            if c_bid is not None and c_ask is not None:
                if _is_valid_number(c_bid) and _is_valid_number(c_ask):
                    if c_bid > 0.0 and c_ask > 0.0 and c_bid > c_ask:
                        return False, f"INVALID_PRICE: Inverted option bid-ask spread for '{ckey}' (bid {c_bid} > ask {c_ask})"

            # Optional volume and open interest checks
            for count_field in ["volume", "open_interest", "oi"]:
                if count_field in contract and contract[count_field] is not None:
                    c_val = contract[count_field]
                    if not _is_valid_integer_count(c_val):
                        return False, f"INVALID_COUNT: Contract '{ckey}' {count_field} must be a non-negative integer, got {c_val!r}"

            # Optional Greeks checks
            greeks = contract.get("greeks")
            if greeks is not None:
                if not isinstance(greeks, dict):
                    return False, f"INVALID_TYPE: Contract '{ckey}' greeks must be dict, got {type(greeks).__name__}"
                for g_name, g_val in greeks.items():
                    if g_val is not None and not _is_valid_number(g_val):
                        return False, f"NON_FINITE_VALUE: Contract '{ckey}' greek '{g_name}' must be finite number, got {g_val!r}"

        return True, None

    def is_valid(self, payload: Any) -> bool:
        """Convenience method returning True if valid, False otherwise."""
        valid, _ = self.validate(payload)
        return valid


# Backward-compatibility alias
DataValidatorPlaceholder = FeedDataValidator


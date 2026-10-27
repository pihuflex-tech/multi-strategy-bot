"""
data_normalizer.py
Phase 2A: Safe, deterministic normalization boundary converting validated raw
market-data dictionaries into strongly-typed market models (MarketSnapshot, OptionQuote, FuturesQuote).
Enforces ISO expiry normalization, canonical contract keys, explicit OI alias validation,
and duplicate contract rejection.
Strictly decoupled: does not connect to ZeroMQ, execute trades, or mutate input payloads.
"""

from datetime import datetime
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from src.market.market_snapshot import FuturesQuote, MarketSnapshot, OptionQuote


_ISO_EXPIRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NSE_EXPIRY_RE = re.compile(r"^\d{2}[A-Za-z]{3}\d{4}$")


def normalize_expiry_date(expiry_raw: Any) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Normalizes an expiry string to ISO 'YYYY-MM-DD'.

    Supported formats:
        - ISO format: 'YYYY-MM-DD' (e.g. '2026-10-29')
        - NSE exchange format: 'DDMMMYYYY' (e.g. '28OCT2026')

    Rejections:
        - Impossible dates (e.g. '2026-02-30', '32OCT2026')
        - Ambiguous dates (e.g. '01/02/2026')
        - Leading/trailing whitespace or non-string types

    Returns:
        (success: bool, normalized_iso: Optional[str], error: Optional[str])
    """
    if not isinstance(expiry_raw, str):
        return False, None, f"INVALID_EXPIRY_TYPE: Expected str, got {type(expiry_raw).__name__}"

    if expiry_raw != expiry_raw.strip() or not expiry_raw.strip():
        return False, None, f"INVALID_EXPIRY_WHITESPACE: Expiry string contains leading/trailing whitespace or is empty: {expiry_raw!r}"

    if _ISO_EXPIRY_RE.match(expiry_raw):
        try:
            dt = datetime.strptime(expiry_raw, "%Y-%m-%d")
            return True, dt.strftime("%Y-%m-%d"), None
        except ValueError as e:
            return False, None, f"IMPOSSIBLE_DATE: Invalid ISO calendar date {expiry_raw!r}: {e}"

    if _NSE_EXPIRY_RE.match(expiry_raw):
        try:
            dt = datetime.strptime(expiry_raw.upper(), "%d%b%Y")
            return True, dt.strftime("%Y-%m-%d"), None
        except ValueError as e:
            return False, None, f"IMPOSSIBLE_DATE: Invalid NSE calendar date {expiry_raw!r}: {e}"

    return False, None, f"UNSUPPORTED_EXPIRY_FORMAT: Expiry {expiry_raw!r} does not match documented ISO (YYYY-MM-DD) or NSE (DDMMMYYYY) format"


def _extract_expiry_from_bucket(bucket: Any) -> Optional[str]:
    """
    Safely extracts and normalizes an expiry date from a nested bucket.
    The bucket may either:
        1. Directly contain an 'expiry' field: {'expiry': '29OCT2026', ...}
        2. Be a dictionary of contracts: {'24850_CE': {'expiry': '29OCT2026', ...}}
    Returns normalized ISO expiry string (YYYY-MM-DD) if found and valid, otherwise None.
    """
    if not isinstance(bucket, dict):
        return None

    if "expiry" in bucket and isinstance(bucket["expiry"], str) and bucket["expiry"].strip():
        ok, norm, _ = normalize_expiry_date(bucket["expiry"])
        if ok and norm:
            return norm

    for _, item in bucket.items():
        if isinstance(item, dict) and "expiry" in item:
            exp = item["expiry"]
            if isinstance(exp, str) and exp.strip():
                ok, norm, _ = normalize_expiry_date(exp)
                if ok and norm:
                    return norm

    return None


def resolve_open_interest(container: Dict[str, Any], context_label: str) -> Tuple[bool, int, Optional[str]]:
    """
    Explicitly resolves open_interest and oi aliases.

    Rules:
        - If only open_interest is present, preserve its value.
        - If only oi is present, preserve its value.
        - If both are present and equal, accept the value.
        - If both are present and conflict, reject with CONFLICTING_OI_ALIASES.
        - Preserve explicit zero.
        - If neither is present, return 0 (matching OptionQuote/FuturesQuote default).

    Returns:
        (success: bool, resolved_oi: int, error: Optional[str])
    """
    has_oi = "oi" in container and container["oi"] is not None
    has_open_interest = "open_interest" in container and container["open_interest"] is not None

    if has_oi and has_open_interest:
        val_oi = container["oi"]
        val_open_interest = container["open_interest"]

        if isinstance(val_oi, bool) or not isinstance(val_oi, int) or val_oi < 0:
            return False, 0, f"INVALID_COUNT: {context_label} oi must be a non-negative integer, got {val_oi!r}"
        if isinstance(val_open_interest, bool) or not isinstance(val_open_interest, int) or val_open_interest < 0:
            return False, 0, f"INVALID_COUNT: {context_label} open_interest must be a non-negative integer, got {val_open_interest!r}"

        if val_oi != val_open_interest:
            return False, 0, f"CONFLICTING_OI_ALIASES: {context_label} has conflicting oi={val_oi} and open_interest={val_open_interest}"
        return True, val_oi, None

    if has_open_interest:
        val = container["open_interest"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return False, 0, f"INVALID_COUNT: {context_label} open_interest must be a non-negative integer, got {val!r}"
        return True, val, None

    if has_oi:
        val = container["oi"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return False, 0, f"INVALID_COUNT: {context_label} oi must be a non-negative integer, got {val!r}"
        return True, val, None

    return True, 0, None


def _is_finite_number(val: Any) -> bool:
    """Checks if value is a numeric int or float (excluding bool) and is finite."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    return math.isfinite(val)


class FeedDataNormalizer:
    """
    Converts raw, validated market-data payloads into strongly-typed MarketSnapshot instances.
    Enforces ISO expiry normalization, canonical contract keys, explicit OI alias validation,
    and duplicate contract rejection.
    """

    def __init__(self, allow_empty_options: bool = True, strict_flat_expiry: bool = False):
        self.allow_empty_options = allow_empty_options
        self.strict_flat_expiry = strict_flat_expiry

    def normalize(self, payload: Any) -> Tuple[bool, Optional[MarketSnapshot], Optional[str]]:
        """
        Normalizes a validated raw market data dictionary into a typed MarketSnapshot.

        Parameters:
            payload: Validated raw market tick dictionary.

        Returns:
            Tuple of (success: bool, snapshot: Optional[MarketSnapshot], error: Optional[str])
            On failure, snapshot is strictly None (no partial construction).
        """
        if not isinstance(payload, dict):
            return False, None, f"INVALID_PAYLOAD_TYPE: Expected dict, got {type(payload).__name__}"

        # 1. Timestamp validation
        raw_ts = payload.get("timestamp")
        if not isinstance(raw_ts, str) or not raw_ts.strip():
            return False, None, f"INVALID_TIMESTAMP: Expected non-empty string, got {raw_ts!r}"
        timestamp_str = raw_ts.strip()

        # 2. Spot price validation
        raw_spot = payload.get("spot")
        if not isinstance(raw_spot, dict):
            return False, None, f"INVALID_SPOT: Expected dict, got {type(raw_spot).__name__}"
        raw_spot_ltp = raw_spot.get("ltp")
        if not _is_finite_number(raw_spot_ltp) or raw_spot_ltp <= 0.0:
            return False, None, f"INVALID_PRICE: spot.ltp must be positive finite number, got {raw_spot_ltp!r}"
        spot_price = float(raw_spot_ltp)

        # 3. Expiries normalization (root or nested options buckets)
        raw_options_dict = payload.get("options") if isinstance(payload.get("options"), dict) else {}

        normalized_current_expiry = ""
        if payload.get("current_expiry") is not None:
            ok_cur, norm_cur, err_cur = normalize_expiry_date(payload["current_expiry"])
            if not ok_cur:
                return False, None, f"INVALID_CURRENT_EXPIRY: {err_cur}"
            normalized_current_expiry = norm_cur
        elif raw_options_dict:
            extracted_cur = _extract_expiry_from_bucket(raw_options_dict.get("current_expiry"))
            if not extracted_cur:
                extracted_cur = _extract_expiry_from_bucket(raw_options_dict.get("wings"))
            if extracted_cur:
                normalized_current_expiry = extracted_cur

        normalized_next_expiry = ""
        if payload.get("next_expiry") is not None:
            ok_nxt, norm_nxt, err_nxt = normalize_expiry_date(payload["next_expiry"])
            if not ok_nxt:
                return False, None, f"INVALID_NEXT_EXPIRY: {err_nxt}"
            normalized_next_expiry = norm_nxt
        elif raw_options_dict:
            extracted_nxt = _extract_expiry_from_bucket(raw_options_dict.get("next_expiry"))
            if extracted_nxt:
                normalized_next_expiry = extracted_nxt

        normalized_monthly_expiry = ""
        if payload.get("monthly_expiry") is not None:
            ok_mth, norm_mth, err_mth = normalize_expiry_date(payload["monthly_expiry"])
            if not ok_mth:
                return False, None, f"INVALID_MONTHLY_EXPIRY: {err_mth}"
            normalized_monthly_expiry = norm_mth
        elif raw_options_dict:
            extracted_mth = _extract_expiry_from_bucket(raw_options_dict.get("monthly_expiry"))
            if extracted_mth:
                normalized_monthly_expiry = extracted_mth

        # 4. Futures normalization (optional)
        futures_quote: Optional[FuturesQuote] = None
        raw_futures = payload.get("futures")
        if raw_futures is not None:
            if not isinstance(raw_futures, dict):
                return False, None, f"INVALID_FUTURES: Expected dict, got {type(raw_futures).__name__}"

            near_dict = raw_futures.get("near") if isinstance(raw_futures.get("near"), dict) else {}

            # Determine LTP: prioritize positive near contract LTP, then root raw_futures LTP
            fut_ltp_raw = None
            if _is_finite_number(near_dict.get("ltp")) and float(near_dict.get("ltp")) > 0:
                fut_ltp_raw = near_dict.get("ltp")
            elif _is_finite_number(raw_futures.get("ltp")) and float(raw_futures.get("ltp")) > 0:
                fut_ltp_raw = raw_futures.get("ltp")
            elif "ltp" in near_dict:
                fut_ltp_raw = near_dict.get("ltp")
            elif "ltp" in raw_futures:
                fut_ltp_raw = raw_futures.get("ltp")

            # Validate LTP or handle safe missing/incomplete near
            if fut_ltp_raw is not None:
                if not _is_finite_number(fut_ltp_raw) or fut_ltp_raw < 0.0:
                    return False, None, f"INVALID_PRICE: futures.ltp must be positive finite number, got {fut_ltp_raw!r}"
                if fut_ltp_raw == 0.0:
                    # Incomplete / uninitialized futures contract handled safely as None
                    futures_quote = None
                else:
                    fut_bid = near_dict.get("bid") if near_dict.get("bid") is not None else raw_futures.get("bid")
                    if fut_bid is not None:
                        if not _is_finite_number(fut_bid) or fut_bid < 0.0:
                            return False, None, f"INVALID_PRICE: futures.bid must be non-negative finite number, got {fut_bid!r}"
                        fut_bid_val = float(fut_bid)
                    else:
                        fut_bid_val = 0.0

                    fut_ask = near_dict.get("ask") if near_dict.get("ask") is not None else raw_futures.get("ask")
                    if fut_ask is not None:
                        if not _is_finite_number(fut_ask) or fut_ask < 0.0:
                            return False, None, f"INVALID_PRICE: futures.ask must be non-negative finite number, got {fut_ask!r}"
                        fut_ask_val = float(fut_ask)
                    else:
                        fut_ask_val = 0.0

                    if fut_bid_val > 0.0 and fut_ask_val > 0.0 and fut_bid_val > fut_ask_val:
                        return False, None, f"INVALID_PRICE: Inverted futures bid-ask spread (bid {fut_bid_val} > ask {fut_ask_val})"

                    # Resolve futures OI aliases (check near_dict first, fallback to raw_futures)
                    if "oi" in near_dict or "open_interest" in near_dict:
                        ok_fut_oi, fut_oi_val, err_fut_oi = resolve_open_interest(near_dict, "futures.near")
                    else:
                        ok_fut_oi, fut_oi_val, err_fut_oi = resolve_open_interest(raw_futures, "futures")
                    if not ok_fut_oi:
                        return False, None, err_fut_oi

                    # Futures volume
                    fut_vol = near_dict.get("volume") if near_dict.get("volume") is not None else raw_futures.get("volume")
                    if fut_vol is not None:
                        if isinstance(fut_vol, bool) or not isinstance(fut_vol, int) or fut_vol < 0:
                            return False, None, f"INVALID_COUNT: futures.volume must be non-negative integer, got {fut_vol!r}"
                        fut_vol_val = fut_vol
                    else:
                        fut_vol_val = 0

                    # Futures expiry
                    fut_expiry_raw = near_dict.get("expiry") if near_dict.get("expiry") is not None else raw_futures.get("expiry")
                    fut_norm_expiry = ""
                    if fut_expiry_raw is not None and str(fut_expiry_raw).strip():
                        ok_fe, fe_norm, err_fe = normalize_expiry_date(fut_expiry_raw)
                        if not ok_fe:
                            return False, None, f"INVALID_FUTURES_EXPIRY: {err_fe}"
                        fut_norm_expiry = fe_norm

                    # Futures token & symbol
                    fut_token = str(near_dict.get("token") or raw_futures.get("token") or "")
                    fut_symbol = str(near_dict.get("symbol") or raw_futures.get("symbol") or "")

                    futures_quote = FuturesQuote(
                        token=fut_token,
                        symbol=fut_symbol,
                        expiry=fut_norm_expiry,
                        ltp=float(fut_ltp_raw),
                        bid=fut_bid_val,
                        ask=fut_ask_val,
                        volume=fut_vol_val,
                        open_interest=fut_oi_val,
                        oi=fut_oi_val,
                        timestamp=time.time()
                    )
            else:
                # Neither near nor root had ltp: handle missing/incomplete near safely
                futures_quote = None

        # 5. India VIX normalization
        india_vix = 0.0
        raw_indices = payload.get("indices")
        if isinstance(raw_indices, dict):
            vix_entry = raw_indices.get("india_vix")
            if isinstance(vix_entry, dict):
                v_ltp = vix_entry.get("ltp")
                if _is_finite_number(v_ltp) and v_ltp > 0.0:
                    india_vix = float(v_ltp)

        # 6. Options collection normalization
        raw_options = payload.get("options")
        if not isinstance(raw_options, dict):
            return False, None, f"INVALID_OPTIONS: Expected dict, got {type(raw_options).__name__}"

        if not raw_options and not self.allow_empty_options:
            return False, None, "EMPTY_OPTIONS_COLLECTION: options container has no contracts"

        # Disassemble flat or bucketed layout into flat candidate list:
        # Tuple of (ckey, contract_dict, enclosing_bucket_expiry, bucket_name)
        candidates: List[Tuple[str, Dict[str, Any], Optional[str], Optional[str]]] = []

        for key, val in raw_options.items():
            if not isinstance(val, dict):
                return False, None, f"MALFORMED_CONTRACT: options entry '{key}' must be a dictionary, got {type(val).__name__}"

            # Check if val is a single contract or a bucket of contracts
            if "strike" in val or "strike_price" in val or "option_type" in val:
                # Flat contract: enclosing_exp is strictly None to prevent blind fallback
                candidates.append((key, val, None, None))
            else:
                # Sub-bucket (e.g. "current_expiry", "next_expiry", "monthly_expiry", or "2026-10-29")
                bucket_name = key
                enclosing_exp: Optional[str] = None

                if bucket_name == "current_expiry":
                    enclosing_exp = normalized_current_expiry or None
                elif bucket_name == "next_expiry":
                    enclosing_exp = normalized_next_expiry or None
                elif bucket_name == "monthly_expiry":
                    enclosing_exp = normalized_monthly_expiry or None
                elif bucket_name == "wings":
                    enclosing_exp = normalized_current_expiry or None
                else:
                    # Check if bucket_name itself is an ISO or NSE date string
                    ok_bd, bd_norm, _ = normalize_expiry_date(bucket_name)
                    if ok_bd:
                        enclosing_exp = bd_norm

                for ckey, cval in val.items():
                    if not isinstance(cval, dict):
                        return False, None, f"MALFORMED_CONTRACT: Contract '{ckey}' in bucket '{bucket_name}' must be a dictionary, got {type(cval).__name__}"
                    candidates.append((ckey, cval, enclosing_exp, bucket_name))

        # Pre-scan flat contracts to detect multi-expiry or ambiguous contexts
        flat_strike_type_counts: Dict[Tuple[int, str], int] = {}
        flat_has_non_current_expiries = False

        for ckey, contract, enclosing_exp, bucket_name in candidates:
            if bucket_name is None:
                # Flat contract
                s_raw = contract.get("strike", contract.get("strike_price"))
                ot_raw = contract.get("option_type")
                if _is_finite_number(s_raw) and isinstance(ot_raw, str):
                    st_key = (int(round(float(s_raw))), ot_raw.upper().strip())
                    flat_strike_type_counts[st_key] = flat_strike_type_counts.get(st_key, 0) + 1

                exp_raw = contract.get("expiry")
                if exp_raw is not None:
                    ok_e, norm_e, _ = normalize_expiry_date(exp_raw)
                    if ok_e and norm_e and normalized_current_expiry and norm_e != normalized_current_expiry:
                        flat_has_non_current_expiries = True

        duplicate_flat_strike_types = {k for k, count in flat_strike_type_counts.items() if count > 1}
        is_multi_expiry_payload = bool(normalized_next_expiry) or bool(normalized_monthly_expiry) or flat_has_non_current_expiries

        # Normalized option chain
        normalized_option_chain: Dict[str, OptionQuote] = {}
        all_resolved_expiries: List[str] = []

        for ckey, contract, enclosing_exp, bucket_name in candidates:
            # A. Strike
            strike_raw = contract.get("strike", contract.get("strike_price"))
            if strike_raw is None:
                return False, None, f"MALFORMED_CONTRACT: Contract '{ckey}' missing strike/strike_price"
            if not _is_finite_number(strike_raw) or strike_raw <= 0.0:
                return False, None, f"INVALID_STRIKE: Contract '{ckey}' strike must be positive finite number, got {strike_raw!r}"
            strike_val = float(strike_raw)
            strike_int = int(round(strike_val))

            # B. Option Type
            opt_type_raw = contract.get("option_type")
            if not isinstance(opt_type_raw, str):
                return False, None, f"MALFORMED_CONTRACT: Contract '{ckey}' option_type must be string, got {type(opt_type_raw).__name__}"
            opt_type = opt_type_raw.upper().strip()
            if opt_type not in ["CE", "PE"]:
                return False, None, f"INVALID_OPTION_TYPE: Contract '{ckey}' option_type must be 'CE' or 'PE', got {opt_type_raw!r}"

            # C. LTP
            ltp_raw = contract.get("ltp")
            if ltp_raw is None:
                return False, None, f"MALFORMED_CONTRACT: Contract '{ckey}' missing ltp"
            if not _is_finite_number(ltp_raw) or ltp_raw <= 0.0:
                return False, None, f"INVALID_PRICE: Contract '{ckey}' ltp must be positive finite number, got {ltp_raw!r}"
            ltp_val = float(ltp_raw)

            # D. Expiry Resolution & Conflict Detection
            contract_exp_raw = contract.get("expiry")
            norm_contract_exp: Optional[str] = None

            if contract_exp_raw is not None:
                ok_ce, ce_norm, err_ce = normalize_expiry_date(contract_exp_raw)
                if not ok_ce:
                    return False, None, f"INVALID_CONTRACT_EXPIRY: Contract '{ckey}' {err_ce}"
                norm_contract_exp = ce_norm

            # Detect conflict between enclosing bucket expiry and contract-level expiry
            if enclosing_exp is not None and norm_contract_exp is not None:
                if enclosing_exp != norm_contract_exp:
                    return False, None, (
                        f"EXPIRY_CONFLICT: Contract '{ckey}' contract expiry {norm_contract_exp!r} "
                        f"conflicts with enclosing bucket '{bucket_name}' expiry {enclosing_exp!r}"
                    )

            if bucket_name is not None:
                # Bucketed layout: bucket establishes unambiguous expiry context
                resolved_exp = norm_contract_exp or enclosing_exp
                if not resolved_exp:
                    return False, None, f"MISSING_EXPIRY: Contract '{ckey}' in bucket '{bucket_name}' has no contract expiry and no enclosing bucket expiry"
            else:
                # Flat layout: fail-closed resolution policy
                if norm_contract_exp is not None:
                    resolved_exp = norm_contract_exp
                else:
                    # Contract has no explicit expiry
                    if self.strict_flat_expiry:
                        return False, None, f"AMBIGUOUS_EXPIRY_IN_FLAT_LAYOUT: Flat contract '{ckey}' missing explicit expiry field"

                    if is_multi_expiry_payload:
                        return False, None, f"AMBIGUOUS_EXPIRY_IN_FLAT_LAYOUT: Flat contract '{ckey}' missing explicit expiry in multi-expiry payload"

                    if (strike_int, opt_type) in duplicate_flat_strike_types:
                        return False, None, f"AMBIGUOUS_EXPIRY_IN_FLAT_LAYOUT: Flat contract '{ckey}' missing explicit expiry cannot be safely resolved due to duplicate strike/type contracts"

                    if not normalized_current_expiry:
                        return False, None, f"MISSING_EXPIRY: Contract '{ckey}' has no contract expiry and payload has no current_expiry"

                    resolved_exp = normalized_current_expiry

            all_resolved_expiries.append(resolved_exp)

            # E. Bid/Ask
            bid_raw = contract.get("bid")
            if bid_raw is not None:
                if not _is_finite_number(bid_raw) or bid_raw < 0.0:
                    return False, None, f"INVALID_PRICE: Contract '{ckey}' bid must be non-negative finite number, got {bid_raw!r}"
                bid_val = float(bid_raw)
            else:
                bid_val = 0.0

            ask_raw = contract.get("ask")
            if ask_raw is not None:
                if not _is_finite_number(ask_raw) or ask_raw < 0.0:
                    return False, None, f"INVALID_PRICE: Contract '{ckey}' ask must be non-negative finite number, got {ask_raw!r}"
                ask_val = float(ask_raw)
            else:
                ask_val = 0.0

            if bid_val > 0.0 and ask_val > 0.0 and bid_val > ask_val:
                return False, None, f"INVALID_PRICE: Inverted option bid-ask spread for '{ckey}' (bid {bid_val} > ask {ask_val})"

            # F. Volume
            vol_raw = contract.get("volume")
            if vol_raw is not None:
                if isinstance(vol_raw, bool) or not isinstance(vol_raw, int) or vol_raw < 0:
                    return False, None, f"INVALID_COUNT: Contract '{ckey}' volume must be non-negative integer, got {vol_raw!r}"
                vol_val = vol_raw
            else:
                vol_val = 0

            # G. Open Interest resolution
            ok_oi, oi_val, err_oi = resolve_open_interest(contract, f"Contract '{ckey}'")
            if not ok_oi:
                return False, None, err_oi

            # H. Greeks extraction
            greeks_dict = contract.get("greeks") if isinstance(contract.get("greeks"), dict) else {}
            delta_val = float(greeks_dict.get("delta", contract.get("delta", 0.0)) or 0.0)
            gamma_val = float(greeks_dict.get("gamma", contract.get("gamma", 0.0)) or 0.0)
            theta_val = float(greeks_dict.get("theta", contract.get("theta", 0.0)) or 0.0)
            vega_val = float(greeks_dict.get("vega", contract.get("vega", 0.0)) or 0.0)
            iv_val = float(greeks_dict.get("iv", contract.get("iv", 0.0)) or 0.0)

            for g_name, g_val in [("delta", delta_val), ("gamma", gamma_val), ("theta", theta_val), ("vega", vega_val), ("iv", iv_val)]:
                if not _is_finite_number(g_val):
                    return False, None, f"NON_FINITE_VALUE: Contract '{ckey}' greek '{g_name}' must be finite number, got {g_val!r}"

            # I. Canonical key construction & duplicate detection
            canonical_key = f"{strike_int}_{opt_type}_{resolved_exp}"

            if canonical_key in normalized_option_chain:
                existing = normalized_option_chain[canonical_key]
                # Check if quotes are conflicting
                if (existing.ltp != ltp_val or existing.bid != bid_val or existing.ask != ask_val
                        or existing.volume != vol_val or existing.open_interest != oi_val):
                    return False, None, (
                        f"DUPLICATE_CONTRACT_CONFLICT: Duplicate canonical contract key '{canonical_key}' "
                        f"with conflicting quote data (existing ltp={existing.ltp}, new ltp={ltp_val})"
                    )
                return False, None, (
                    f"DUPLICATE_CONTRACT: Canonical contract key '{canonical_key}' appears multiple times in option chain"
                )

            # J. Construct typed OptionQuote
            symbol_val = str(contract.get("symbol", f"NIFTY_{strike_int}_{opt_type}_{resolved_exp}"))
            token_val = str(contract.get("token", ""))

            quote = OptionQuote(
                token=token_val,
                symbol=symbol_val,
                strike_price=strike_val,
                strike=strike_val,
                option_type=opt_type,
                expiry=resolved_exp,
                ltp=ltp_val,
                bid=bid_val,
                ask=ask_val,
                volume=vol_val,
                open_interest=oi_val,
                oi=oi_val,
                oi_change=int(contract.get("oi_change", 0) or 0),
                delta=delta_val,
                gamma=gamma_val,
                theta=theta_val,
                vega=vega_val,
                iv=iv_val,
                timestamp=time.time()
            )
            normalized_option_chain[canonical_key] = quote

        # If top current_expiry was empty but contracts were normalized, deduce current_expiry
        final_current_expiry = normalized_current_expiry
        if not final_current_expiry and all_resolved_expiries:
            final_current_expiry = sorted(all_resolved_expiries)[0]

        snapshot = MarketSnapshot(
            timestamp=timestamp_str,
            spot_price=spot_price,
            spot=spot_price,
            futures=futures_quote,
            india_vix=india_vix,
            option_chain=normalized_option_chain,
            current_expiry=final_current_expiry,
            next_expiry=normalized_next_expiry,
            monthly_expiry=normalized_monthly_expiry,
            is_stale=False,
            data_status="VALID"
        )
        # Ensure snapshot.options references option_chain as per MarketSnapshot.__post_init__
        snapshot.options = normalized_option_chain

        return True, snapshot, None


# Backward-compatibility alias
DataNormalizerPlaceholder = FeedDataNormalizer

"""
api.py
REST API endpoints serving sanitized, read-only telemetry data to web clients.

Phase 3A: Minimal, safe, read-only REST API endpoints:
- GET /api/health: Dashboard service health probe
- GET /api/state: Sanitized system telemetry snapshot

Strict Safety Guarantees:
- Read-only endpoints only; no control or trade execution routes.
- Sanitizes all inputs, stripping sensitive keys (credentials, secrets, tokens).
- Does not fabricate live prices, ticks, positions, or PnL when missing.
- Safe fallbacks (UNKNOWN, NOT_CONNECTED, NOT_AVAILABLE).
- Raw exception messages and tracebacks are never exposed to HTTP clients.
- Dashboard service health is decoupled from and does not verify market-feed health.
"""

import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from flask import Blueprint, current_app, jsonify, Response

logger = logging.getLogger("DashboardAPI")

api_bp = Blueprint("dashboard_api", __name__)

SENSITIVE_KEY_SUBSTRINGS = (
    "secret",
    "password",
    "token",
    "credential",
    "totp",
    "api_key",
    "apikey",
    "private_key",
    "client_secret",
)

EXACT_SENSITIVE_KEYS = {
    "secret",
    "api_secret",
    "password",
    "pass",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "pin",
    "totp",
    "totp_secret",
    "private_key",
    "client_secret",
    "api_key",
    "apikey",
}


def _is_sensitive_key(key: Any) -> bool:
    """Return True if the key represents a sensitive or credential field."""
    norm = str(key).strip().lower()
    if norm in EXACT_SENSITIVE_KEYS:
        return True
    for substring in SENSITIVE_KEY_SUBSTRINGS:
        if substring in norm:
            return True
    return False


def _sanitize_float(val: Any) -> Optional[float]:
    """Sanitize float value. Returns None if invalid or NaN/Inf; never fabricates."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 2)
    except (ValueError, TypeError):
        return None


def _sanitize_float_default(val: Any, default: float = 0.0) -> float:
    """Sanitize float with numeric default; fallback if None or invalid."""
    res = _sanitize_float(val)
    return res if res is not None else default


def _sanitize_int(val: Any, default: int = 0) -> int:
    """Sanitize integer value; returns default if invalid."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _sanitize_int_nullable(val: Any) -> Optional[int]:
    """Sanitize integer value; returns None if invalid or None."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _sanitize_error_code(raw_msg: Any, default: str = "NONE") -> str:
    """Sanitize error messages to safe diagnostic codes without leaking internals."""
    if not raw_msg or not isinstance(raw_msg, str):
        return default
    msg = raw_msg.strip()
    if not msg:
        return default
    # Redact potential tracebacks, file paths, or sensitive terms
    if any(pattern in msg for pattern in ("Traceback", "File \"", "\n", "\\", "/")):
        return "INTERNAL_ERROR"
    if any(s in msg.lower() for s in SENSITIVE_KEY_SUBSTRINGS):
        return "REDACTED_ERROR"
    return msg[:80]


def _sanitize_leg(raw_leg: Any) -> Optional[Dict[str, Any]]:
    """Sanitize individual leg dictionary within a multi-leg position."""
    if not isinstance(raw_leg, dict):
        return None

    clean_leg: Dict[str, Any] = {}
    for str_k in ("leg_id", "symbol", "option_type", "expiry", "side", "status", "entry_time", "exit_time"):
        val = raw_leg.get(str_k)
        if val is not None and not _is_sensitive_key(str_k):
            clean_leg[str_k] = str(val)[:80]
        else:
            clean_leg[str_k] = None

    clean_leg["strike"] = _sanitize_float(raw_leg.get("strike"))
    clean_leg["initial_quantity"] = _sanitize_int_nullable(raw_leg.get("initial_quantity"))
    clean_leg["remaining_quantity"] = _sanitize_int_nullable(
        raw_leg.get("remaining_quantity", raw_leg.get("quantity", raw_leg.get("qty")))
    )
    clean_leg["entry_price"] = _sanitize_float(raw_leg.get("entry_price"))
    clean_leg["current_price"] = _sanitize_float(raw_leg.get("current_price"))
    clean_leg["exit_price"] = _sanitize_float(raw_leg.get("exit_price"))
    clean_leg["realized_pnl"] = _sanitize_float(raw_leg.get("realized_pnl"))
    clean_leg["unrealized_pnl"] = _sanitize_float(raw_leg.get("unrealized_pnl"))
    clean_leg["charges"] = _sanitize_float(raw_leg.get("charges"))
    return clean_leg


def _extract_telemetry_payload(provider: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Extract raw telemetry dictionary from an injected provider.
    
    Supported provider types:
    - Callable returning Dict or None
    - Object with get_state() or to_dict()
    - Dict
    - File path (str or Path) to JSON file (read-only)
    - None (telemetry unavailable)
    
    Returns (raw_dict_or_none, extraction_status_code).
    """
    if provider is None:
        return None, "TELEMETRY_UNAVAILABLE"

    try:
        raw_data = None
        if callable(provider):
            raw_data = provider()
        elif hasattr(provider, "get_state") and callable(getattr(provider, "get_state")):
            raw_data = provider.get_state()
        elif hasattr(provider, "to_dict") and callable(getattr(provider, "to_dict")):
            raw_data = provider.to_dict()
        elif isinstance(provider, (str, Path)):
            path = Path(provider)
            if not path.is_file():
                return None, "TELEMETRY_SOURCE_NOT_FOUND"
            with open(path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        elif isinstance(provider, dict):
            raw_data = provider
        else:
            return None, "INVALID_PROVIDER_TYPE"

        if raw_data is None:
            return None, "TELEMETRY_UNAVAILABLE"
        if not isinstance(raw_data, dict):
            return None, "INVALID_TELEMETRY_FORMAT"
        return raw_data, "OK"
    except Exception as exc:
        logger.warning("Telemetry provider access failed safely: %s", type(exc).__name__)
        return None, "TELEMETRY_PROVIDER_ERROR"


def sanitize_telemetry(raw: Optional[Dict[str, Any]], extraction_status: str = "OK") -> Dict[str, Any]:
    """
    Convert raw or missing telemetry data into a sanitized, schema-compliant snapshot.
    
    Guarantees:
    - Missing or invalid fields map to explicit fallbacks (UNKNOWN, NOT_CONNECTED, NOT_AVAILABLE).
    - Strips sensitive credentials and secrets.
    - No fabricated prices, ticks, or PnL.
    """
    if not isinstance(raw, dict):
        raw = {}

    # 1. bot_status
    bot_status_candidates = [raw.get("bot_status"), raw.get("engine_status"), raw.get("status")]
    bot_status = "UNKNOWN"
    for candidate in bot_status_candidates:
        if isinstance(candidate, str) and candidate.strip():
            bot_status = candidate.strip().upper()
            break

    # 1b. app_lifecycle
    app_lifecycle = "STANDBY"
    raw_lifecycle = raw.get("app_lifecycle")
    if isinstance(raw_lifecycle, str) and raw_lifecycle.strip():
        app_lifecycle = raw_lifecycle.strip().upper()

    # 1c. strategy_engine
    strategy_engine = "NOT_AVAILABLE"
    raw_strategy_engine = raw.get("strategy_engine")
    if isinstance(raw_strategy_engine, str) and raw_strategy_engine.strip():
        strategy_engine = raw_strategy_engine.strip()

    # 1d. paper_execution
    paper_execution = "STANDBY"
    raw_paper_exec = raw.get("paper_execution")
    if isinstance(raw_paper_exec, str) and raw_paper_exec.strip():
        paper_execution = raw_paper_exec.strip().upper()

    # 2. feed_connection
    feed_conn = "NOT_CONNECTED"
    raw_feed_conn = raw.get("feed_connection")
    if isinstance(raw_feed_conn, str) and raw_feed_conn.strip():
        feed_conn = raw_feed_conn.strip().upper()

    # 3. market_data_status
    market_data_status = "NOT_AVAILABLE"
    raw_market_status = raw.get("market_data_status", raw.get("market_status"))
    if isinstance(raw_market_status, str) and raw_market_status.strip():
        market_data_status = raw_market_status.strip().upper()

    # 4. last_tick
    last_tick_raw = raw.get("last_tick")
    if isinstance(last_tick_raw, dict):
        last_tick = {
            "timestamp": str(last_tick_raw.get("timestamp", "NOT_AVAILABLE")),
            "spot": _sanitize_float(last_tick_raw.get("spot", last_tick_raw.get("spot_price"))),
            "futures": _sanitize_float(last_tick_raw.get("futures", last_tick_raw.get("futures_price"))),
            "symbol": str(last_tick_raw.get("symbol", "NIFTY")),
        }
    elif raw.get("spot_price") is not None or raw.get("futures_price") is not None or raw.get("timestamp"):
        last_tick = {
            "timestamp": str(raw.get("timestamp", "NOT_AVAILABLE")),
            "spot": _sanitize_float(raw.get("spot_price")),
            "futures": _sanitize_float(raw.get("futures_price")),
            "symbol": "NIFTY",
        }
    else:
        last_tick = {
            "timestamp": "NOT_AVAILABLE",
            "spot": None,
            "futures": None,
            "symbol": "NIFTY",
        }

    # 5. strategies
    raw_strategies = raw.get("strategies")
    strategies: List[Dict[str, Any]] = []
    if isinstance(raw_strategies, list):
        for item in raw_strategies:
            if isinstance(item, dict):
                clean_strat = {}
                for k, v in item.items():
                    if not _is_sensitive_key(k):
                        if isinstance(v, (int, float, str, bool)) or v is None:
                            clean_strat[str(k)] = v
                strategies.append(clean_strat)

    # 6. open_positions
    raw_positions = raw.get("open_positions")
    open_positions: List[Dict[str, Any]] = []
    if isinstance(raw_positions, list):
        for pos in raw_positions:
            if isinstance(pos, dict):
                clean_pos: Dict[str, Any] = {}
                # Parent position metadata
                for k in ("position_id", "strategy_id", "status", "entry_time", "close_time", "exit_policy"):
                    val = pos.get(k)
                    if val is not None and not _is_sensitive_key(k):
                        clean_pos[k] = str(val)[:80]
                    else:
                        clean_pos[k] = None

                # Numeric parent position metrics
                clean_pos["combined_unrealized_pnl"] = _sanitize_float(
                    pos.get("combined_unrealized_pnl", pos.get("unrealized_pnl"))
                )
                clean_pos["combined_realized_pnl"] = _sanitize_float(
                    pos.get("combined_realized_pnl", pos.get("realized_pnl"))
                )
                clean_pos["total_charges"] = _sanitize_float(pos.get("total_charges"))
                clean_pos["entry_debit_or_credit"] = _sanitize_float(pos.get("entry_debit_or_credit"))
                clean_pos["target_value"] = _sanitize_float(pos.get("target_value"))
                clean_pos["initial_sl_value"] = _sanitize_float(pos.get("initial_sl_value"))
                clean_pos["trailing_sl_value"] = _sanitize_float(pos.get("trailing_sl_value"))

                # Flat legacy fields if present (for single-leg backward compatibility)
                if pos.get("symbol") is not None and not _is_sensitive_key("symbol"):
                    clean_pos["symbol"] = str(pos["symbol"])[:80]
                if pos.get("side") is not None and not _is_sensitive_key("side"):
                    clean_pos["side"] = str(pos["side"])[:10].upper()
                if pos.get("quantity") is not None or pos.get("qty") is not None:
                    clean_pos["quantity"] = _sanitize_int_nullable(pos.get("quantity", pos.get("qty")))
                if pos.get("entry_price") is not None:
                    clean_pos["entry_price"] = _sanitize_float(pos.get("entry_price"))
                if pos.get("current_price") is not None:
                    clean_pos["current_price"] = _sanitize_float(pos.get("current_price"))
                if pos.get("unrealized_pnl") is not None:
                    clean_pos["unrealized_pnl"] = _sanitize_float(pos.get("unrealized_pnl"))

                # Nested legs extraction and sanitization
                clean_legs: List[Dict[str, Any]] = []
                raw_legs = pos.get("legs")
                leg_candidates: List[Any] = []
                if isinstance(raw_legs, dict):
                    leg_candidates = list(raw_legs.values())
                elif isinstance(raw_legs, list):
                    leg_candidates = raw_legs

                for candidate in leg_candidates:
                    sanitized_leg = _sanitize_leg(candidate)
                    if sanitized_leg is not None:
                        clean_legs.append(sanitized_leg)

                clean_pos["legs"] = clean_legs
                clean_pos["legs_count"] = len(clean_legs)
                open_positions.append(clean_pos)

    # 7. portfolio
    raw_portfolio = raw.get("portfolio")
    if isinstance(raw_portfolio, dict):
        portfolio = {
            "open_positions_count": _sanitize_int_nullable(raw_portfolio.get("open_positions_count")),
            "open_legs_count": _sanitize_int_nullable(raw_portfolio.get("open_legs_count")),
            "total_unrealized_mtm": _sanitize_float(
                raw_portfolio.get("total_unrealized_mtm", raw_portfolio.get("unrealized_pnl"))
            ),
            "total_realized_gross_pnl": _sanitize_float(
                raw_portfolio.get("total_realized_gross_pnl", raw_portfolio.get("realized_pnl"))
            ),
            "total_charges": _sanitize_float(raw_portfolio.get("total_charges")),
            "total_net_realized_pnl": _sanitize_float(raw_portfolio.get("total_net_realized_pnl")),
            "net_combined_pnl": _sanitize_float(raw_portfolio.get("net_combined_pnl")),
            "currency": str(raw_portfolio.get("currency", "INR")),
        }
    else:
        portfolio = {
            "open_positions_count": None,
            "open_legs_count": None,
            "total_unrealized_mtm": None,
            "total_realized_gross_pnl": None,
            "total_charges": None,
            "total_net_realized_pnl": None,
            "net_combined_pnl": None,
            "currency": "INR",
        }

    # 8. errors
    raw_errors = raw.get("errors")
    system_error = extraction_status if extraction_status != "OK" else "NONE"
    errors = {
        "last_feed_error": "NONE",
        "last_system_error": system_error,
    }
    if isinstance(raw_errors, dict):
        raw_feed_err = raw_errors.get("last_feed_error")
        if raw_feed_err:
            errors["last_feed_error"] = _sanitize_error_code(raw_feed_err, "NONE")
        raw_sys_err = raw_errors.get("last_system_error")
        if raw_sys_err and system_error == "NONE":
            errors["last_system_error"] = _sanitize_error_code(raw_sys_err, "NONE")

    return {
        "app_lifecycle": app_lifecycle,
        "bot_status": bot_status,
        "feed_connection": feed_conn,
        "market_data_status": market_data_status,
        "strategy_engine": strategy_engine,
        "paper_execution": paper_execution,
        "last_tick": last_tick,
        "strategies": strategies,
        "open_positions": open_positions,
        "portfolio": portfolio,
        "errors": errors,
    }


@api_bp.route("/health", methods=["GET"])
def get_health() -> Response:
    """
    Service health probe endpoint.
    
    Explicit semantic guarantee:
    Dashboard service health proves only that the Flask API service is active.
    It does not prove or verify market-feed health or connectivity.
    """
    response = {
        "status": "ONLINE",
        "service": "dashboard_api",
        "market_feed_verified": False,
        "note": "Dashboard service health does not prove market-feed health.",
    }
    return jsonify(response)


@api_bp.route("/state", methods=["GET"])
def get_state() -> Response:
    """
    Sanitized read-only telemetry snapshot endpoint.
    
    Reads from the injected telemetry provider in application configuration.
    """
    provider = current_app.config.get("TELEMETRY_PROVIDER")
    raw_payload, extraction_status = _extract_telemetry_payload(provider)
    sanitized_state = sanitize_telemetry(raw_payload, extraction_status)
    return jsonify(sanitized_state)


class ApiPlaceholder:
    """Backward compatibility alias for api."""
    blueprint = api_bp

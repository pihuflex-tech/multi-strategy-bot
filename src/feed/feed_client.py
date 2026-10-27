"""
feed_client.py
Synchronous, injectable, offline-testable market data feed client.

Orchestrates the market feed ingestion pipeline:
FeedTransport -> FeedMessageParser -> FeedDataValidator -> FeedDataNormalizer -> FeedHealthMonitor

Strictly decoupled:
- Pure Python standard library with no external dependencies.
- No network sockets, ZeroMQ connections, or broker APIs.
- No background threads, async loops, or automatic reconnections.
- No strategy evaluation or order placement.
"""

from enum import Enum
import math
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple, Union, runtime_checkable

from src.feed.data_normalizer import FeedDataNormalizer
from src.feed.data_validator import FeedDataValidator
from src.feed.feed_health import FeedHealthMonitor, FeedHealthStatus
from src.feed.message_parser import FeedMessageParser
from src.market.market_snapshot import MarketSnapshot


class FeedClientState(str, Enum):
    """Lifecycle states of the FeedClient."""
    UNCONNECTED = "UNCONNECTED"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    CLOSED = "CLOSED"


@runtime_checkable
class FeedTransport(Protocol):
    """
    Protocol for receiving raw multipart wire frames and closing transport.
    Decoupled interface permitting offline mocking and in-memory test fixtures.
    """

    def recv_frames(self, timeout_ms: int) -> Optional[List[bytes]]:
        """
        Receives multipart wire frames within timeout_ms.

        Parameters:
            timeout_ms: Maximum wait duration in milliseconds.

        Returns:
            List of bytes frames if available, or None on timeout.
        """
        ...

    def close(self) -> None:
        """Closes the underlying transport channel and releases resources."""
        ...


class FeedClient:
    """
    Synchronous, injectable, offline-testable market data client.
    Orchestrates FeedTransport -> FeedMessageParser -> FeedDataValidator -> FeedDataNormalizer -> FeedHealthMonitor.
    """

    DEFAULT_POLL_TIMEOUT_MS: int = 500
    DEFAULT_TOPIC: bytes = b"NIFTY_MARKET_TICK"

    _SAFE_DISCONNECT_REASONS: Set[str] = {
        "CLIENT_DISCONNECTED",
        "CLIENT_CLOSED",
        "CLIENT_STOPPED",
        "TIMEOUT: RECEIVE_TIMEOUT",
        "TRANSPORT_ERROR: CONNECTION_RESET",
        "TRANSPORT_ERROR: CONNECTION_REFUSED",
        "TRANSPORT_ERROR: BROKEN_PIPE",
        "TRANSPORT_ERROR: IO_FAULT",
        "TRANSPORT_ERROR: CHANNEL_UNAVAILABLE",
        "TRANSPORT_ERROR: CLEANUP_FAILED",
    }

    def __init__(
        self,
        transport: Optional[FeedTransport] = None,
        parser: Optional[FeedMessageParser] = None,
        validator: Optional[FeedDataValidator] = None,
        normalizer: Optional[FeedDataNormalizer] = None,
        health_monitor: Optional[FeedHealthMonitor] = None,
        poll_timeout_ms: int = DEFAULT_POLL_TIMEOUT_MS,
        expected_topic: bytes = DEFAULT_TOPIC,
    ):
        """
        Initializes FeedClient with injected pipeline components.

        Parameters:
            transport: Optional initial FeedTransport instance.
            parser: Optional FeedMessageParser instance.
            validator: Optional FeedDataValidator instance.
            normalizer: Optional FeedDataNormalizer instance.
            health_monitor: Optional FeedHealthMonitor instance.
            poll_timeout_ms: Positive integer timeout in milliseconds for polling.
            expected_topic: Expected bytes topic for parser.
        """
        if (
            isinstance(poll_timeout_ms, bool)
            or not isinstance(poll_timeout_ms, int)
            or poll_timeout_ms <= 0
        ):
            raise ValueError(f"poll_timeout_ms must be a positive integer, got {poll_timeout_ms!r}")

        self._poll_timeout_ms: int = poll_timeout_ms
        self._expected_topic: bytes = bytes(expected_topic)

        self._transport: Optional[FeedTransport] = transport
        self._parser: FeedMessageParser = (
            parser if parser is not None else FeedMessageParser(expected_topic=self._expected_topic)
        )
        self._validator: FeedDataValidator = (
            validator if validator is not None else FeedDataValidator(require_expiry=True, allow_empty_options=True)
        )
        self._normalizer: FeedDataNormalizer = (
            normalizer if normalizer is not None else FeedDataNormalizer(allow_empty_options=True)
        )
        self._health_monitor: FeedHealthMonitor = (
            health_monitor if health_monitor is not None else FeedHealthMonitor(stale_threshold_seconds=5.0)
        )

        self._state: FeedClientState = FeedClientState.UNCONNECTED
        self._last_valid_snapshot: Optional[MarketSnapshot] = None
        self._last_poll_error: Optional[str] = None
        self._closed_transports: Set[int] = set()

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------
    @property
    def state(self) -> FeedClientState:
        """Returns the current lifecycle state of the client."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Returns True if client is actively connected."""
        return self._state == FeedClientState.CONNECTED

    @property
    def last_valid_snapshot(self) -> Optional[MarketSnapshot]:
        """
        Returns the most recent valid MarketSnapshot accepted by the pipeline.
        Retained across errors, timeouts, or disconnects as historical reference.
        Does not imply current feed freshness.
        """
        return self._last_valid_snapshot

    @property
    def last_poll_error(self) -> Optional[str]:
        """Returns the most recent public error code encountered during polling, if any."""
        return self._last_poll_error

    @property
    def health_monitor(self) -> FeedHealthMonitor:
        """Direct access to the underlying FeedHealthMonitor."""
        return self._health_monitor

    @property
    def health_status(self) -> FeedHealthStatus:
        """
        Evaluates elapsed time and returns active FeedHealthStatus.
        Accurately preserves INVALID_DATA even if initial tick was never valid.
        """
        if (
            self._health_monitor.status == FeedHealthStatus.INVALID_DATA
            and self._health_monitor.last_valid_receipt_monotonic is None
        ):
            return FeedHealthStatus.INVALID_DATA
        return self._health_monitor.check_health()

    def is_strategy_ready(
        self,
        min_options: int = 1,
        require_futures: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluates quantitative strategy readiness.
        Delegates to the health monitor: requires healthy status, freshness, and instrument availability.
        """
        return self._health_monitor.is_strategy_ready(
            min_options=min_options,
            require_futures=require_futures
        )

    # -------------------------------------------------------------------------
    # Lifecycle Management
    # -------------------------------------------------------------------------
    def connect(self, transport: Optional[FeedTransport] = None) -> None:
        """
        Connects the client to an injected transport.

        Parameters:
            transport: Optional fresh FeedTransport instance. If None, uses transport provided in __init__.

        Raises:
            RuntimeError: If client is in terminal CLOSED state or replacement cleanup fails.
            ValueError: If transport is missing, invalid, or already closed.
        """
        if self._state == FeedClientState.CLOSED:
            raise RuntimeError("CLIENT_PERMANENTLY_CLOSED: Cannot connect a closed FeedClient.")

        target = transport if transport is not None else self._transport
        if target is None:
            raise ValueError("TRANSPORT_INVALID: No transport provided.")

        if not hasattr(target, "recv_frames") or not callable(getattr(target, "recv_frames")):
            raise ValueError("TRANSPORT_INVALID: Transport must implement recv_frames().")
        if not hasattr(target, "close") or not callable(getattr(target, "close")):
            raise ValueError("TRANSPORT_INVALID: Transport must implement close().")

        if id(target) in self._closed_transports or getattr(target, "is_closed", False):
            raise ValueError("TRANSPORT_ALREADY_CLOSED: Cannot connect an already-closed transport.")

        # Idempotent no-op if connecting same active transport
        if self._state == FeedClientState.CONNECTED and self._transport is target:
            return

        # Replacing active transport
        if self._state == FeedClientState.CONNECTED and self._transport is not None:
            old_transport = self._transport
            cleanup_failed = False
            if id(old_transport) not in self._closed_transports:
                self._closed_transports.add(id(old_transport))
                try:
                    old_transport.close()
                except Exception:
                    cleanup_failed = True

            if cleanup_failed:
                self._transport = None
                self._state = FeedClientState.DISCONNECTED
                self._last_poll_error = "TRANSPORT_ERROR: CLEANUP_FAILED"
                if self._health_monitor is not None:
                    self._health_monitor.record_disconnect("TRANSPORT_ERROR: CLEANUP_FAILED")
                raise RuntimeError("TRANSPORT_ERROR: CLEANUP_FAILED")

        self._transport = target
        self._state = FeedClientState.CONNECTED
        self._last_poll_error = None

    def disconnect(self, reason: str = "CLIENT_DISCONNECTED") -> None:
        """
        Disconnects the active transport and transitions client to DISCONNECTED state.
        Safe and idempotent: closes transport at most once per client-owned instance.
        Suppresses transport cleanup exceptions to guarantee entering safe DISCONNECTED state.
        """
        if self._state == FeedClientState.CLOSED:
            return

        self._state = FeedClientState.DISCONNECTED
        self._last_poll_error = "CLIENT_STOPPED"

        old_transport = self._transport
        if old_transport is not None and id(old_transport) not in self._closed_transports:
            self._closed_transports.add(id(old_transport))
            try:
                old_transport.close()
            except Exception:
                pass

        safe_reason = self._sanitize_disconnect_reason(reason)
        if self._health_monitor is not None:
            self._health_monitor.record_disconnect(safe_reason)

    def close(self) -> None:
        """
        Permanently shuts down the FeedClient.
        Terminal transition to CLOSED state. Subsequent connect() calls will fail.
        Safe and idempotent: closes transport at most once per client-owned instance.
        """
        if self._state == FeedClientState.CLOSED:
            return

        self._state = FeedClientState.CLOSED
        self._last_poll_error = "CLIENT_STOPPED"

        old_transport = self._transport
        if old_transport is not None and id(old_transport) not in self._closed_transports:
            self._closed_transports.add(id(old_transport))
            try:
                old_transport.close()
            except Exception:
                pass

        if self._health_monitor is not None:
            self._health_monitor.record_disconnect("CLIENT_CLOSED")

    # -------------------------------------------------------------------------
    # Polling & Pipeline Orchestration
    # -------------------------------------------------------------------------
    def poll_snapshot(
        self,
        timeout_ms: Optional[int] = None
    ) -> Tuple[bool, Optional[MarketSnapshot], Optional[str]]:
        """
        Polls a single multipart message from the transport and processes it through the pipeline:
        Transport -> MessageParser -> DataValidator -> DataNormalizer -> HealthMonitor

        Parameters:
            timeout_ms: Optional timeout override in milliseconds. Defaults to poll_timeout_ms.

        Returns:
            Tuple of (success: bool, snapshot: Optional[MarketSnapshot], error: Optional[str])
        """
        # 1. State short-circuits
        if self._state == FeedClientState.CLOSED:
            return False, None, "CLIENT_STOPPED"
        if self._state == FeedClientState.DISCONNECTED:
            return False, None, "CLIENT_STOPPED"
        if self._state == FeedClientState.UNCONNECTED:
            return False, None, "CLIENT_NOT_CONNECTED"

        if self._transport is None:
            return False, None, "CLIENT_NOT_CONNECTED"

        effective_timeout = timeout_ms if timeout_ms is not None else self._poll_timeout_ms
        if (
            isinstance(effective_timeout, bool)
            or not isinstance(effective_timeout, int)
            or effective_timeout < 0
        ):
            return False, None, "TIMEOUT: INVALID_PARAMETER"

        # 2. Receive from transport
        try:
            frames = self._transport.recv_frames(effective_timeout)
        except (TypeError, KeyError, IndexError, AttributeError, AssertionError):
            # Programming bugs: trigger cleanup and re-raise
            self.disconnect("TRANSPORT_ERROR: CHANNEL_UNAVAILABLE")
            raise
        except Exception as exc:
            err_code = self._classify_transport_exception(exc)
            if err_code == "TIMEOUT: RECEIVE_TIMEOUT":
                self._health_monitor.check_health()
                self._last_poll_error = err_code
                return False, None, err_code
            else:
                self.disconnect(err_code)
                self._last_poll_error = err_code
                return False, None, err_code

        # 3. Handle timeout (None frames)
        if frames is None:
            err_code = "TIMEOUT: RECEIVE_TIMEOUT"
            self._health_monitor.check_health()
            self._last_poll_error = err_code
            return False, None, err_code

        # 4. Pipeline Stage 1: Parser
        try:
            ok_p, payload, err_p = self._parser.parse_multipart(frames)
        except (TypeError, KeyError, IndexError, AttributeError, AssertionError):
            raise
        except Exception:
            ok_p = False
            payload = None
            err_p = "PARSER_EXCEPTION"

        if not ok_p:
            pub_err = self._map_parser_error(err_p)
            self._health_monitor.record_invalid_tick(pub_err)
            self._last_poll_error = pub_err
            return False, None, pub_err

        # 5. Pipeline Stage 2: Validator
        try:
            ok_v, err_v = self._validator.validate(payload)
        except (TypeError, KeyError, IndexError, AttributeError, AssertionError):
            raise
        except Exception:
            ok_v = False
            err_v = "VALIDATOR_EXCEPTION"

        if not ok_v:
            pub_err = self._map_validator_error(err_v)
            self._health_monitor.record_invalid_tick(pub_err)
            self._last_poll_error = pub_err
            return False, None, pub_err

        # 6. Pipeline Stage 3: Normalizer
        try:
            ok_n, snapshot, err_n = self._normalizer.normalize(payload)
        except (TypeError, KeyError, IndexError, AttributeError, AssertionError):
            raise
        except Exception:
            ok_n = False
            snapshot = None
            err_n = "NORMALIZATION_EXCEPTION"

        if not ok_n:
            pub_err = self._map_normalizer_error(err_n)
            self._health_monitor.record_invalid_tick(pub_err)
            self._last_poll_error = pub_err
            return False, None, pub_err

        # 7. Pipeline Stage 4: Health Monitor
        self._health_monitor.record_snapshot(snapshot)
        self._last_valid_snapshot = snapshot
        self._last_poll_error = None
        return True, snapshot, None

    # -------------------------------------------------------------------------
    # Sanitization & Error Code Mappings
    # -------------------------------------------------------------------------
    def _sanitize_disconnect_reason(self, reason: str) -> str:
        """Sanitizes arbitrary disconnect reason strings to fixed public codes."""
        if isinstance(reason, str) and reason in self._SAFE_DISCONNECT_REASONS:
            return reason
        return "CLIENT_DISCONNECTED"

    @staticmethod
    def _classify_transport_exception(exc: Exception) -> str:
        """Maps transport exceptions to stable, safe public error codes without exposing details."""
        if isinstance(exc, TimeoutError):
            return "TIMEOUT: RECEIVE_TIMEOUT"
        if isinstance(exc, ConnectionResetError):
            return "TRANSPORT_ERROR: CONNECTION_RESET"
        if isinstance(exc, ConnectionRefusedError):
            return "TRANSPORT_ERROR: CONNECTION_REFUSED"
        if isinstance(exc, BrokenPipeError):
            return "TRANSPORT_ERROR: BROKEN_PIPE"
        if isinstance(exc, (OSError, IOError)):
            return "TRANSPORT_ERROR: IO_FAULT"
        return "TRANSPORT_ERROR: CHANNEL_UNAVAILABLE"

    @staticmethod
    def _map_parser_error(err: Optional[str]) -> str:
        """Maps internal parser errors to fixed public error codes."""
        if not err or not isinstance(err, str):
            return "PARSER_ERROR: INVALID_PAYLOAD"
        if err.startswith("INVALID_FRAME_STRUCTURE") or err.startswith("INVALID_FRAME_COUNT"):
            return "PARSER_ERROR: INVALID_FRAME"
        if err.startswith("INVALID_TOPIC"):
            return "PARSER_ERROR: INVALID_TOPIC"
        if err.startswith("PAYLOAD_TOO_LARGE"):
            return "PARSER_ERROR: PAYLOAD_TOO_LARGE"
        if err.startswith("INVALID_ENCODING"):
            return "PARSER_ERROR: INVALID_ENCODING"
        if err.startswith("MALFORMED_JSON") or err.startswith("NON_STANDARD_CONSTANT_REJECTED"):
            return "PARSER_ERROR: MALFORMED_JSON"
        if err.startswith("INVALID_PAYLOAD_TYPE"):
            return "PARSER_ERROR: INVALID_PAYLOAD"
        return "PARSER_ERROR: INVALID_PAYLOAD"

    @staticmethod
    def _map_validator_error(err: Optional[str]) -> str:
        """Maps internal validator errors to fixed public error codes."""
        if not err or not isinstance(err, str):
            return "VALIDATOR_ERROR: SCHEMA_VIOLATION"
        if err.startswith("MISSING_FIELD"):
            return "VALIDATOR_ERROR: MISSING_FIELD"
        if err.startswith("INVALID_PRICE") or err.startswith("INVALID_BID_ASK"):
            return "VALIDATOR_ERROR: INVALID_PRICE"
        if err.startswith("INVALID_STRIKE") or err.startswith("INCONSISTENT_STRIKE"):
            return "VALIDATOR_ERROR: INVALID_STRIKE"
        if err.startswith("INVALID_OPTION_TYPE"):
            return "VALIDATOR_ERROR: INVALID_OPTION_TYPE"
        if err.startswith("INVALID_EXPIRY"):
            return "VALIDATOR_ERROR: INVALID_EXPIRY"
        if err.startswith("INVALID_COUNT"):
            return "VALIDATOR_ERROR: INVALID_COUNT"
        if err.startswith("INVALID_TYPE") or err.startswith("INVALID_PAYLOAD_TYPE"):
            return "VALIDATOR_ERROR: SCHEMA_VIOLATION"
        return "VALIDATOR_ERROR: SCHEMA_VIOLATION"

    @staticmethod
    def _map_normalizer_error(err: Optional[str]) -> str:
        """Maps internal normalizer errors to fixed public error codes."""
        if not err or not isinstance(err, str):
            return "NORMALIZER_ERROR: NORMALIZATION_FAILED"
        if err.startswith("AMBIGUOUS"):
            return "NORMALIZER_ERROR: AMBIGUOUS_EXPIRY"
        if err.startswith("EXPIRY_CONFLICT") or err.startswith("EXPIRY_MISMATCH"):
            return "NORMALIZER_ERROR: EXPIRY_CONFLICT"
        if err.startswith("DUPLICATE_CONTRACT"):
            return "NORMALIZER_ERROR: DUPLICATE_CONTRACT"
        if err.startswith("CONFLICTING_OI"):
            return "NORMALIZER_ERROR: CONFLICTING_OI"
        if (
            err.startswith("INVALID_EXPIRY")
            or err.startswith("IMPOSSIBLE_DATE")
            or err.startswith("UNSUPPORTED_EXPIRY_FORMAT")
            or err.startswith("INVALID_CONTRACT_EXPIRY")
            or err.startswith("INVALID_FUTURES_EXPIRY")
            or err.startswith("MISSING_EXPIRY")
        ):
            return "NORMALIZER_ERROR: INVALID_EXPIRY"
        if err.startswith("INVALID_PRICE"):
            return "NORMALIZER_ERROR: INVALID_PRICE"
        return "NORMALIZER_ERROR: NORMALIZATION_FAILED"


# Backward-compatibility alias
FeedClientPlaceholder = FeedClient

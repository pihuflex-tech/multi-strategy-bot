"""
feed_health.py
Phase 2B: Offline Feed Health Monitor.
Tracks market feed heartbeat, monotonic freshness, stale data transitions,
connection status, and strategy readiness boundaries without network or ZeroMQ dependencies.
Strictly decoupled: does not connect to sockets, mutate historical positions, or execute orders.
"""

from enum import Enum
import math
import time
from typing import Any, Callable, Dict, Optional, Tuple, Union

from src.market.market_snapshot import MarketSnapshot


class FeedHealthStatus(str, Enum):
    """
    Explicit feed health states consistent with the multi-strategy bot architecture.
    """
    NOT_INITIALIZED = "NOT_INITIALIZED"  # No valid data has established health
    HEALTHY = "HEALTHY"                  # Valid data received within freshness threshold
    STALE = "STALE"                      # Most recent valid data is older than threshold
    INVALID_DATA = "INVALID_DATA"        # Latest attempted update was invalid (history retained)
    DISCONNECTED = "DISCONNECTED"        # Caller explicitly reported connection unavailable


class FeedHealthMonitor:
    """
    Monitors market feed heartbeat, monotonic elapsed-time freshness, and data readiness.
    Permits deterministic offline testing via dependency-injected monotonic time providers.
    Separates feed freshness from quantitative strategy readiness.
    """

    DEFAULT_STALE_THRESHOLD_SECONDS: float = 5.0

    def __init__(
        self,
        stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD_SECONDS,
        time_provider: Optional[Callable[[], float]] = None
    ):
        """
        Initializes the feed health monitor.

        Parameters:
            stale_threshold_seconds: Positive finite seconds after which valid data is considered stale.
            time_provider: Callable returning monotonic time in seconds (defaults to time.monotonic).
        """
        if (isinstance(stale_threshold_seconds, bool)
                or not isinstance(stale_threshold_seconds, (int, float))
                or not math.isfinite(stale_threshold_seconds)
                or stale_threshold_seconds <= 0.0):
            raise ValueError(
                f"stale_threshold_seconds must be a positive finite number, got {stale_threshold_seconds!r}"
            )

        self.stale_threshold_seconds: float = float(stale_threshold_seconds)
        self.time_provider: Callable[[], float] = time_provider if time_provider is not None else time.monotonic

        # Health state attributes
        self.status: FeedHealthStatus = FeedHealthStatus.NOT_INITIALIZED
        self.last_valid_receipt_monotonic: Optional[float] = None
        self.last_valid_exchange_timestamp: Optional[str] = None
        self.last_error: Optional[str] = None

        # Content availability tracking (separated from health)
        self.last_option_chain_len: int = 0
        self.last_has_futures: bool = False

        # Diagnostic counters
        self.valid_ticks_count: int = 0
        self.invalid_ticks_count: int = 0
        self.clock_faults_count: int = 0
        self._max_observed_monotonic: Optional[float] = None

    def _sample_monotonic_time(self) -> Tuple[Optional[float], Optional[str]]:
        """
        Safely samples the injected monotonic time provider.
        Enforces exception safety, non-boolean numeric type, finite numeric bounds,
        and non-decreasing monotonic progression.

        Returns:
            Tuple of (valid_timestamp: Optional[float], error_reason: Optional[str])
        """
        try:
            raw_time = self.time_provider()
        except Exception as exc:
            return None, f"CLOCK_EXCEPTION: {type(exc).__name__}: {str(exc)}"

        if isinstance(raw_time, bool) or not isinstance(raw_time, (int, float)):
            return None, f"CLOCK_INVALID_TYPE: Expected finite float/int, got {type(raw_time).__name__}"

        if not math.isfinite(raw_time):
            return None, f"CLOCK_NON_FINITE: Clock returned non-finite value {raw_time!r}"

        now = float(raw_time)

        # Monotonicity check: time must not move backwards relative to previously observed time
        if self._max_observed_monotonic is not None and now < self._max_observed_monotonic:
            return None, (
                f"CLOCK_REGRESSION: Monotonic time moved backwards "
                f"({now} < max_observed {self._max_observed_monotonic})"
            )

        self._max_observed_monotonic = now
        return now, None

    def record_valid_tick(
        self,
        timestamp: Optional[str] = None,
        option_chain_len: int = 0,
        has_futures: bool = False
    ) -> FeedHealthStatus:
        """
        Records the successful receipt and normalization of a valid market tick.
        Refreshes monotonic arrival time and transitions status to HEALTHY.

        Parameters:
            timestamp: Exchange timestamp string from tick payload (if present).
            option_chain_len: Number of option contracts present in tick.
            has_futures: Whether a futures quote is present in tick.

        Returns:
            Updated FeedHealthStatus (HEALTHY, or STALE/current if clock fault occurred).
        """
        now, clock_err = self._sample_monotonic_time()
        if clock_err is not None:
            self.clock_faults_count += 1
            self.last_error = f"CLOCK_FAULT: {clock_err}"
            # If currently healthy, clock failure means freshness cannot be established
            if self.status == FeedHealthStatus.HEALTHY:
                self.status = FeedHealthStatus.STALE
            # Note: does NOT increment invalid_ticks_count (market data payload was valid)
            # Note: does NOT refresh last_valid_receipt_monotonic or last_valid_exchange_timestamp
            return self.status

        self.last_valid_receipt_monotonic = now

        # Record exchange timestamp safely without raising on missing or non-string
        if isinstance(timestamp, str) and timestamp.strip():
            self.last_valid_exchange_timestamp = timestamp.strip()
        elif timestamp is None:
            self.last_valid_exchange_timestamp = None
        else:
            # Preserve string representation of unusual timestamp values safely
            self.last_valid_exchange_timestamp = str(timestamp)

        self.last_option_chain_len = max(0, int(option_chain_len) if isinstance(option_chain_len, int) else 0)
        self.last_has_futures = bool(has_futures)
        self.valid_ticks_count += 1
        self.last_error = None
        self.status = FeedHealthStatus.HEALTHY
        return self.status

    def record_snapshot(self, snapshot: MarketSnapshot) -> FeedHealthStatus:
        """
        Convenience method to record a valid tick directly from a normalized MarketSnapshot.
        """
        if not isinstance(snapshot, MarketSnapshot):
            return self.record_invalid_tick(f"INVALID_SNAPSHOT_TYPE: Expected MarketSnapshot, got {type(snapshot).__name__}")

        opt_len = len(snapshot.option_chain) if snapshot.option_chain is not None else 0
        has_fut = snapshot.futures is not None
        return self.record_valid_tick(
            timestamp=snapshot.timestamp,
            option_chain_len=opt_len,
            has_futures=has_fut
        )

    def record_invalid_tick(self, error_reason: str) -> FeedHealthStatus:
        """
        Records an invalid or unparseable tick payload.
        Crucially: DOES NOT refresh last_valid_receipt_monotonic or last_valid_exchange_timestamp.
        Transitions state to INVALID_DATA while preserving historical last-valid markers.
        If already DISCONNECTED, preserves DISCONNECTED.

        Parameters:
            error_reason: Documented error code or description of parser/validator failure.

        Returns:
            Updated FeedHealthStatus.
        """
        self.invalid_ticks_count += 1
        self.last_error = str(error_reason) if error_reason else "UNKNOWN_INVALID_DATA"
        if self.status != FeedHealthStatus.DISCONNECTED:
            self.status = FeedHealthStatus.INVALID_DATA
        return self.status

    def record_disconnect(self, reason: str = "CONNECTION_UNAVAILABLE") -> FeedHealthStatus:
        """
        Explicitly records that the feed connection or network channel is unavailable.
        Does not refresh last_valid_receipt_monotonic.
        Transitions state to DISCONNECTED.

        Parameters:
            reason: Reason code for disconnection.

        Returns:
            Updated FeedHealthStatus (DISCONNECTED).
        """
        self.last_error = str(reason)
        self.status = FeedHealthStatus.DISCONNECTED
        return self.status

    def check_health(self) -> FeedHealthStatus:
        """
        Evaluates elapsed monotonic time since last valid tick and returns active status.
        Idempotent: does not mutate historical markers or position states.

        Returns:
            Active FeedHealthStatus.
        """
        # Disconnected remains disconnected until a new valid tick is received
        if self.status == FeedHealthStatus.DISCONNECTED:
            return FeedHealthStatus.DISCONNECTED

        # Not initialized remains uninitialized until valid tick is received
        if self.status == FeedHealthStatus.NOT_INITIALIZED or self.last_valid_receipt_monotonic is None:
            return FeedHealthStatus.NOT_INITIALIZED

        # If already STALE, clock passage or recovery alone cannot restore HEALTHY
        if self.status == FeedHealthStatus.STALE:
            return FeedHealthStatus.STALE

        now, clock_err = self._sample_monotonic_time()
        if clock_err is not None:
            self.clock_faults_count += 1
            self.last_error = f"CLOCK_FAULT: {clock_err}"
            if self.status == FeedHealthStatus.HEALTHY:
                self.status = FeedHealthStatus.STALE
            return self.status

        elapsed = now - self.last_valid_receipt_monotonic

        # If elapsed time exceeds threshold, mark as STALE
        if elapsed > self.stale_threshold_seconds:
            self.status = FeedHealthStatus.STALE
            return FeedHealthStatus.STALE

        # If within threshold and not invalid, status is HEALTHY
        if self.status == FeedHealthStatus.INVALID_DATA:
            # Remains INVALID_DATA to reflect that the latest message was corrupt,
            # though last valid data is still within freshness threshold
            return FeedHealthStatus.INVALID_DATA

        self.status = FeedHealthStatus.HEALTHY
        return FeedHealthStatus.HEALTHY

    @property
    def is_healthy(self) -> bool:
        """
        Returns True iff current status is HEALTHY and elapsed time is within threshold.
        """
        current_status = self.check_health()
        return current_status == FeedHealthStatus.HEALTHY

    @property
    def is_stale(self) -> bool:
        """
        Returns True if feed data is stale, uninitialized, disconnected, or older than threshold.
        """
        if self.last_valid_receipt_monotonic is None:
            return True
        if self.status in [FeedHealthStatus.NOT_INITIALIZED, FeedHealthStatus.DISCONNECTED, FeedHealthStatus.STALE]:
            return True
        now, clock_err = self._sample_monotonic_time()
        if clock_err is not None:
            return True
        elapsed = now - self.last_valid_receipt_monotonic
        return elapsed > self.stale_threshold_seconds

    def is_strategy_ready(
        self,
        min_options: int = 1,
        require_futures: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Separation of Feed Health from Strategy Readiness.
        Evaluates whether current market data is sufficient for strategy evaluation.
        Fail-closed: requires healthy feed, fresh data, and non-empty required instruments.

        Parameters:
            min_options: Minimum number of option contracts required (default 1).
            require_futures: If True, requires futures quote in latest valid tick.

        Returns:
            Tuple of (is_ready: bool, block_reason: Optional[str])
        """
        # 1. Feed Health Check
        current_status = self.check_health()
        if current_status != FeedHealthStatus.HEALTHY:
            return False, f"FEED_NOT_HEALTHY: Feed status is {current_status.value}"

        if self.is_stale:
            return False, "FEED_STALE: Market data elapsed time exceeds freshness threshold"

        # 2. Options Availability Check (Empty options does not imply readiness)
        if self.last_option_chain_len < max(0, min_options):
            return False, (
                f"INSUFFICIENT_OPTIONS: Option chain contains {self.last_option_chain_len} contracts; "
                f"minimum required is {min_options}"
            )

        # 3. Futures Availability Check
        if require_futures and not self.last_has_futures:
            return False, "MISSING_FUTURES: Required futures contract is missing from latest market tick"

        return True, None

    def reset(self) -> None:
        """Resets monitor to initial uninitialized state."""
        self.status = FeedHealthStatus.NOT_INITIALIZED
        self.last_valid_receipt_monotonic = None
        self.last_valid_exchange_timestamp = None
        self.last_error = None
        self.last_option_chain_len = 0
        self.last_has_futures = False
        self.valid_ticks_count = 0
        self.invalid_ticks_count = 0
        self.clock_faults_count = 0
        self._max_observed_monotonic = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializes health telemetry state for logging or live_state.json monitoring.
        """
        now, clock_err = self._sample_monotonic_time()
        elapsed = None
        if now is not None and self.last_valid_receipt_monotonic is not None:
            elapsed = round(now - self.last_valid_receipt_monotonic, 4)
        return {
            "status": self.status.value,
            "is_healthy": self.is_healthy,
            "is_stale": self.is_stale,
            "stale_threshold_seconds": self.stale_threshold_seconds,
            "elapsed_seconds": elapsed,
            "last_valid_exchange_timestamp": self.last_valid_exchange_timestamp,
            "valid_ticks_count": self.valid_ticks_count,
            "invalid_ticks_count": self.invalid_ticks_count,
            "clock_faults_count": self.clock_faults_count,
            "last_error": self.last_error,
            "last_option_chain_len": self.last_option_chain_len,
            "last_has_futures": self.last_has_futures
        }


# Backward-compatibility alias
FeedHealthPlaceholder = FeedHealthMonitor

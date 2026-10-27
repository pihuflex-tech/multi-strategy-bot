"""
test_feed_health.py
Comprehensive offline unit tests for FeedHealthMonitor (Phase 2B).
Covers all 15+ specifications from user prompt:
1. Initial state is not healthy.
2. A valid update establishes healthy status.
3. Freshness below the threshold remains healthy.
4. Freshness beyond the threshold becomes stale.
5. Invalid input does not refresh the last-valid-data timestamp.
6. A valid update can recover a stale feed.
7. Explicit disconnection is represented correctly.
8. Recovery after disconnection requires valid fresh data.
9. Invalid or missing timestamps are handled safely.
10. Negative, zero, non-finite, and invalid freshness thresholds rejected with ValueError.
11. Clock injection permits deterministic tests without sleeping.
12. Empty option data does not imply readiness.
13. Missing futures remain unavailable.
14. Health and strategy readiness remain separate.
15. Repeated status checks do not mutate historical position or execution data.
16. Record from typed MarketSnapshot directly.
17. Telemetry serialization contains all expected keys.
"""

import math
import os
import unittest

from src.feed.feed_health import FeedHealthMonitor, FeedHealthStatus
from src.market.market_snapshot import FuturesQuote, MarketSnapshot, OptionQuote


class TestFeedHealthMonitor(unittest.TestCase):
    """Offline unit test suite for FeedHealthMonitor."""

    def setUp(self):
        # Monotonic mock clock provider
        self.current_time = 1000.0
        self.time_provider = lambda: self.current_time
        self.monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=self.time_provider)

    def test_01_initial_state_is_not_healthy(self):
        """1. Initial state is not healthy: NOT_INITIALIZED, is_healthy=False, is_stale=True."""
        self.assertEqual(self.monitor.status, FeedHealthStatus.NOT_INITIALIZED)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)
        self.assertIsNone(self.monitor.last_valid_receipt_monotonic)
        self.assertIsNone(self.monitor.last_valid_exchange_timestamp)
        self.assertEqual(self.monitor.valid_ticks_count, 0)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)

    def test_02_valid_update_establishes_healthy_status(self):
        """2. A valid update establishes healthy status."""
        status = self.monitor.record_valid_tick(
            timestamp="2026-10-25 09:20:00.000",
            option_chain_len=50,
            has_futures=True
        )
        self.assertEqual(status, FeedHealthStatus.HEALTHY)
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")
        self.assertEqual(self.monitor.valid_ticks_count, 1)
        self.assertEqual(self.monitor.last_option_chain_len, 50)
        self.assertTrue(self.monitor.last_has_futures)

    def test_03_freshness_below_threshold_remains_healthy(self):
        """3. Freshness below the threshold remains healthy."""
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000")

        # Advance clock by 2.5 seconds (stale threshold is 5.0)
        self.current_time += 2.5
        status = self.monitor.check_health()
        self.assertEqual(status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)

        # Advance to 4.99 seconds (still within threshold)
        self.current_time = 1000.0 + 4.99
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)

    def test_04_freshness_beyond_threshold_becomes_stale(self):
        """4. Freshness beyond the threshold becomes stale."""
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000")

        # Advance clock to 5.01 seconds (beyond 5.0s threshold)
        self.current_time = 1000.0 + 5.01
        status = self.monitor.check_health()
        self.assertEqual(status, FeedHealthStatus.STALE)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)

        # Further advance to 30.0 seconds
        self.current_time = 1000.0 + 30.0
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.STALE)
        self.assertTrue(self.monitor.is_stale)

    def test_05_invalid_input_does_not_refresh_last_valid_timestamp(self):
        """5. Invalid input does not refresh the last-valid-data timestamp."""
        # 1. First valid tick at t=1000.0
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000")
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)

        # 2. Advance time to t=1003.0 and record invalid tick
        self.current_time = 1003.0
        status = self.monitor.record_invalid_tick("MALFORMED_JSON: line 1 col 5")

        self.assertEqual(status, FeedHealthStatus.INVALID_DATA)
        self.assertEqual(self.monitor.status, FeedHealthStatus.INVALID_DATA)
        self.assertEqual(self.monitor.invalid_ticks_count, 1)
        self.assertEqual(self.monitor.last_error, "MALFORMED_JSON: line 1 col 5")
        # Monotonic last valid time MUST NOT be updated
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # 3. Advance to t=1006.0: since last valid was at t=1000.0, elapsed is 6.0 > 5.0 -> STALE
        self.current_time = 1006.0
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.STALE)

    def test_06_valid_update_can_recover_stale_feed(self):
        """6. A valid update can recover a stale feed."""
        # Valid tick at t=1000
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000")

        # Becomes stale at t=1010
        self.current_time = 1010.0
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.STALE)
        self.assertTrue(self.monitor.is_stale)

        # New valid tick arrives at t=1011
        self.current_time = 1011.0
        rec_status = self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:11.000")
        self.assertEqual(rec_status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1011.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:11.000")

    def test_07_explicit_disconnection_is_represented_correctly(self):
        """7. Explicit disconnection is represented correctly (status=DISCONNECTED, is_healthy=False, is_stale=True)."""
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000")
        self.assertTrue(self.monitor.is_healthy)

        # Disconnect signal
        disc_status = self.monitor.record_disconnect(reason="ZMQ_SUB_ENDPOINT_UNREACHABLE")
        self.assertEqual(disc_status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)
        self.assertEqual(self.monitor.last_error, "ZMQ_SUB_ENDPOINT_UNREACHABLE")

        # check_health() must remain DISCONNECTED even if within threshold
        self.current_time = 1001.0
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.DISCONNECTED)

    def test_08_recovery_after_disconnection_requires_valid_fresh_data(self):
        """8. Recovery after disconnection requires valid fresh data."""
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000")
        self.monitor.record_disconnect(reason="DISCONNECTED")

        # Invalid tick arriving during disconnect does NOT restore healthy status and preserves DISCONNECTED
        self.monitor.record_invalid_tick("CORRUPTED_FRAME")
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.invalid_ticks_count, 1)
        self.assertEqual(self.monitor.last_error, "CORRUPTED_FRAME")
        self.assertFalse(self.monitor.is_healthy)

        # Only a fresh valid tick restores HEALTHY
        self.current_time = 1020.0
        status = self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:20.000")
        self.assertEqual(status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)

    def test_09_invalid_or_missing_timestamps_handled_safely(self):
        """9. Invalid, missing, whitespace, or non-string timestamps handled safely without raising."""
        for ts_val in [None, "", "   ", 12345, 123.45, True, ["ts"]]:
            monitor = FeedHealthMonitor(time_provider=lambda: 1000.0)
            status = monitor.record_valid_tick(timestamp=ts_val, option_chain_len=10)
            self.assertEqual(status, FeedHealthStatus.HEALTHY)
            self.assertTrue(monitor.is_healthy)

    def test_10_invalid_freshness_thresholds_rejected_with_value_error(self):
        """10. Negative, zero, non-finite, boolean, and non-numeric thresholds raise ValueError."""
        invalid_thresholds = [
            0, 0.0, -1, -5.0,
            True, False,
            float("nan"), float("inf"), float("-inf"),
            "5.0", None, []
        ]
        for bad_thresh in invalid_thresholds:
            with self.assertRaises(ValueError, msg=f"Should reject threshold={bad_thresh!r}"):
                FeedHealthMonitor(stale_threshold_seconds=bad_thresh)

    def test_11_clock_injection_permits_deterministic_tests_without_sleeping(self):
        """11. Clock injection permits deterministic tests without sleeping."""
        clock_ticks = [100.0, 102.0, 106.0, 110.0]
        clock_iter = iter(clock_ticks)
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=lambda: next(clock_iter))

        # t=100: valid tick
        monitor.record_valid_tick()
        self.assertEqual(monitor.last_valid_receipt_monotonic, 100.0)

        # t=102: elapsed=2 <= 5 -> HEALTHY
        self.assertEqual(monitor.check_health(), FeedHealthStatus.HEALTHY)

        # t=106: elapsed=6 > 5 -> STALE
        self.assertEqual(monitor.check_health(), FeedHealthStatus.STALE)

    def test_12_empty_option_data_does_not_imply_readiness(self):
        """12. Empty option data does not imply readiness even when feed is healthy."""
        # Feed receives tick with 0 options (empty collection)
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000", option_chain_len=0)
        self.assertTrue(self.monitor.is_healthy, "Feed should be healthy at transport level")

        # But strategy readiness fails closed
        is_ready, reason = self.monitor.is_strategy_ready(min_options=1)
        self.assertFalse(is_ready)
        self.assertIn("INSUFFICIENT_OPTIONS", reason)
        self.assertIn("Option chain contains 0 contracts", reason)

    def test_13_missing_futures_remain_unavailable(self):
        """13. Missing futures remain unavailable and block strategies requiring futures."""
        # Tick has options but no futures
        self.monitor.record_valid_tick(
            timestamp="2026-10-25 09:20:00.000",
            option_chain_len=100,
            has_futures=False
        )
        self.assertTrue(self.monitor.is_healthy)

        # Option strategies ready
        ready_opt, _ = self.monitor.is_strategy_ready(min_options=10, require_futures=False)
        self.assertTrue(ready_opt)

        # Futures strategies fail closed
        ready_fut, reason_fut = self.monitor.is_strategy_ready(min_options=10, require_futures=True)
        self.assertFalse(ready_fut)
        self.assertIn("MISSING_FUTURES", reason_fut)

    def test_14_health_and_strategy_readiness_remain_separate(self):
        """14. Health and strategy readiness are distinct orthogonal concepts."""
        # Case A: Healthy feed, but empty options -> Health=True, Readiness=False
        self.monitor.record_valid_tick(option_chain_len=0)
        self.assertTrue(self.monitor.is_healthy)
        ready_a, _ = self.monitor.is_strategy_ready(min_options=5)
        self.assertFalse(ready_a)

        # Case B: Stale feed, but previously had 100 options -> Health=False, Readiness=False
        self.monitor.record_valid_tick(option_chain_len=100)
        self.current_time += 10.0  # Becomes stale
        self.assertFalse(self.monitor.is_healthy)
        ready_b, reason_b = self.monitor.is_strategy_ready(min_options=5)
        self.assertFalse(ready_b)
        self.assertIn("FEED_NOT_HEALTHY", reason_b)

        # Case C: Disconnected feed -> Readiness=False
        self.monitor.record_disconnect()
        ready_c, reason_c = self.monitor.is_strategy_ready()
        self.assertFalse(ready_c)
        self.assertIn("FEED_NOT_HEALTHY", reason_c)

    def test_15_repeated_status_checks_do_not_mutate_historical_data(self):
        """15. Repeated status checks do not mutate state markers or file data."""
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000", option_chain_len=20)
        initial_dict = self.monitor.to_dict()

        # Call check_health() 100 times without advancing clock
        for _ in range(100):
            status = self.monitor.check_health()
            self.assertEqual(status, FeedHealthStatus.HEALTHY)

        subsequent_dict = self.monitor.to_dict()
        self.assertEqual(initial_dict, subsequent_dict)
        self.assertEqual(self.monitor.valid_ticks_count, 1)

    def test_16_record_snapshot_integration(self):
        """16. Direct integration with normalized MarketSnapshot."""
        snap = MarketSnapshot(
            timestamp="2026-10-25 09:20:00.000",
            spot_price=24850.0,
            futures=FuturesQuote(symbol="NIFTY_FUT", ltp=24880.0),
            option_chain={"24850_CE_2026-10-29": OptionQuote(strike=24850.0, ltp=120.0)},
            current_expiry="2026-10-29"
        )
        status = self.monitor.record_snapshot(snap)
        self.assertEqual(status, FeedHealthStatus.HEALTHY)
        self.assertEqual(self.monitor.last_option_chain_len, 1)
        self.assertTrue(self.monitor.last_has_futures)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # Invalid snapshot object
        bad_status = self.monitor.record_snapshot("not_a_snapshot")
        self.assertEqual(bad_status, FeedHealthStatus.INVALID_DATA)

    def test_17_to_dict_telemetry_keys(self):
        """17. to_dict produces comprehensive diagnostic telemetry."""
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000", option_chain_len=40, has_futures=True)
        telemetry = self.monitor.to_dict()
        expected_keys = {
            "status", "is_healthy", "is_stale", "stale_threshold_seconds",
            "elapsed_seconds", "last_valid_exchange_timestamp",
            "valid_ticks_count", "invalid_ticks_count", "clock_faults_count", "last_error",
            "last_option_chain_len", "last_has_futures"
        }
        self.assertTrue(expected_keys.issubset(telemetry.keys()))
        self.assertEqual(telemetry["status"], "HEALTHY")
        self.assertTrue(telemetry["is_healthy"])
        self.assertFalse(telemetry["is_stale"])
        self.assertEqual(telemetry["last_option_chain_len"], 40)
        self.assertTrue(telemetry["last_has_futures"])

    def test_18_clock_returns_nan_transitions_to_stale_and_blocks_readiness(self):
        """18. Monotonic clock returning NaN transitions HEALTHY -> STALE and blocks readiness."""
        self.monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000", option_chain_len=10)
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)

        # Inject NaN into clock provider
        self.current_time = float("nan")
        status = self.monitor.check_health()
        self.assertEqual(status, FeedHealthStatus.STALE)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)
        self.assertIn("CLOCK_NON_FINITE", self.monitor.last_error)
        self.assertEqual(self.monitor.clock_faults_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)

        ready, reason = self.monitor.is_strategy_ready()
        self.assertFalse(ready)
        self.assertIn("FEED_NOT_HEALTHY", reason)

    def test_19_clock_returns_infinities_rejected_fail_closed(self):
        """19. Monotonic clock returning +inf or -inf fails closed safely."""
        for inf_val in [float("inf"), float("-inf")]:
            self.monitor.reset()
            self.current_time = 1000.0
            self.monitor.record_valid_tick(option_chain_len=5)
            self.current_time = inf_val

            status = self.monitor.check_health()
            self.assertEqual(status, FeedHealthStatus.STALE)
            self.assertFalse(self.monitor.is_healthy)
            self.assertTrue(self.monitor.is_stale)
            self.assertEqual(self.monitor.invalid_ticks_count, 0)
            self.assertGreater(self.monitor.clock_faults_count, 0)

    def test_20_clock_returns_non_numeric_handled_safely_without_crash(self):
        """20. Monotonic clock returning non-numeric types does not crash with TypeError."""
        for bad_clock in [None, "1005.0", [1000.0], {"t": 1000.0}, True]:
            monitor = FeedHealthMonitor(time_provider=lambda: 1000.0)
            monitor.record_valid_tick(option_chain_len=10)

            # Switch provider to return bad non-numeric type
            monitor.time_provider = lambda b=bad_clock: b
            status = monitor.check_health()
            self.assertEqual(status, FeedHealthStatus.STALE)
            self.assertFalse(monitor.is_healthy)
            self.assertTrue(monitor.is_stale)
            self.assertIn("CLOCK_INVALID_TYPE", monitor.last_error)
            self.assertEqual(monitor.clock_faults_count, 1)
            self.assertEqual(monitor.invalid_ticks_count, 0)

            # to_dict does not raise TypeError
            telem = monitor.to_dict()
            self.assertIsNone(telem["elapsed_seconds"])

    def test_21_clock_provider_exception_handled_safely(self):
        """21. Clock provider raising exception transitions to STALE without crashing caller."""
        self.monitor.record_valid_tick(option_chain_len=10)
        def broken_clock():
            raise RuntimeError("Hardware timer failed")
        self.monitor.time_provider = broken_clock

        status = self.monitor.check_health()
        self.assertEqual(status, FeedHealthStatus.STALE)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)
        self.assertIn("CLOCK_EXCEPTION", self.monitor.last_error)
        self.assertEqual(self.monitor.clock_faults_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)

    def test_22_clock_regression_backwards_movement_triggers_stale_immediately(self):
        """22. Backwards-moving clock triggers CLOCK_REGRESSION and cannot suppress stale status."""
        self.current_time = 1000.0
        self.monitor.record_valid_tick(option_chain_len=10)

        # Clock jumps backwards
        self.current_time = 950.0
        status = self.monitor.check_health()
        self.assertEqual(status, FeedHealthStatus.STALE)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)
        self.assertIn("CLOCK_REGRESSION", self.monitor.last_error)
        self.assertEqual(self.monitor.clock_faults_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)

    def test_23_clock_anomaly_during_record_valid_tick_distinguished_from_market_data_error(self):
        """23. Clock fault during record_valid_tick preserves market data counters and original timestamp."""
        self.current_time = 1000.0
        self.monitor.record_valid_tick(timestamp="TS_01", option_chain_len=10)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.valid_ticks_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)

        # Next tick arrives when clock returns NaN
        self.current_time = float("nan")
        status = self.monitor.record_valid_tick(timestamp="TS_02", option_chain_len=10)
        self.assertEqual(status, FeedHealthStatus.STALE)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)  # Preserved
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "TS_01")  # Preserved
        self.assertEqual(self.monitor.valid_ticks_count, 1)  # Not incremented
        self.assertEqual(self.monitor.invalid_ticks_count, 0)  # Market data was not invalid
        self.assertEqual(self.monitor.clock_faults_count, 1)  # Distinct clock counter incremented
        self.assertIn("CLOCK_FAULT", self.monitor.last_error)

    def test_24_clock_becomes_invalid_after_healthy_immediately_locks_down(self):
        """24. Clock becoming invalid after HEALTHY transitions to STALE and locks down readiness."""
        self.current_time = 1000.0
        self.monitor.record_valid_tick(option_chain_len=10)
        ready_before, _ = self.monitor.is_strategy_ready()
        self.assertTrue(ready_before)

        # Clock becomes invalid
        self.current_time = float("nan")
        ready_after, reason = self.monitor.is_strategy_ready()
        self.assertFalse(ready_after)
        self.assertIn("FEED_NOT_HEALTHY", reason)

    def test_25_clock_recovery_alone_does_not_restore_healthy_without_new_tick(self):
        """25. Clock recovery alone cannot restore HEALTHY; only an explicit valid tick can recover."""
        self.current_time = 1000.0
        self.monitor.record_valid_tick(option_chain_len=10)
        self.assertTrue(self.monitor.is_healthy)

        # Feed goes stale due to elapsed time
        self.current_time = 1010.0
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.STALE)

        # Clock continues ticking forward normally (e.g. 1012.0, 1015.0)
        self.current_time = 1012.0
        status_recovery_attempt = self.monitor.check_health()
        self.assertEqual(status_recovery_attempt, FeedHealthStatus.STALE)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)

        # Strategy readiness remains blocked despite valid clock
        ready, _ = self.monitor.is_strategy_ready()
        self.assertFalse(ready)

        # Only a NEW valid tick restores HEALTHY
        self.current_time = 1016.0
        recovered_status = self.monitor.record_valid_tick(option_chain_len=10)
        self.assertEqual(recovered_status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)

    def test_26_disconnected_status_preserved_during_clock_fault(self):
        """26. Disconnected status is preserved unconditionally during clock faults."""
        self.monitor.record_disconnect("NETWORK_FAILURE")
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)

        # Clock faults occur while disconnected
        self.current_time = float("nan")
        status = self.monitor.check_health()
        self.assertEqual(status, FeedHealthStatus.DISCONNECTED)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)

        # Calling record_valid_tick during clock fault still cannot establish health
        status_tick = self.monitor.record_valid_tick(option_chain_len=10)
        self.assertEqual(status_tick, FeedHealthStatus.DISCONNECTED)
        self.assertFalse(self.monitor.is_healthy)
        self.assertEqual(self.monitor.clock_faults_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)

    def test_27_repeated_health_checks_during_clock_anomaly_are_idempotent(self):
        """27. Repeated health checks during clock anomaly remain idempotent without mutating counters."""
        self.current_time = 1000.0
        self.monitor.record_valid_tick(option_chain_len=10)
        self.current_time = float("nan")

        for _ in range(50):
            status = self.monitor.check_health()
            self.assertEqual(status, FeedHealthStatus.STALE)
            self.assertFalse(self.monitor.is_healthy)

        self.assertEqual(self.monitor.valid_ticks_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)
        self.assertEqual(self.monitor.clock_faults_count, 1)

    def test_28_disconnected_preserved_during_invalid_tick_and_recovers_on_valid_tick(self):
        """28. DISCONNECTED state is preserved during invalid ticks, timestamps are untouched, and recovers on valid tick."""
        # 1. Establish initial healthy state at t=1000.0
        self.current_time = 1000.0
        self.monitor.record_valid_tick(
            timestamp="2026-10-25 09:20:00.000",
            option_chain_len=20,
            has_futures=True
        )
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # 2. Feed drops connection
        disc_status = self.monitor.record_disconnect(reason="TCP_RESET_BY_PEER")
        self.assertEqual(disc_status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)
        self.assertFalse(self.monitor.is_healthy)
        ready_disc, reason_disc = self.monitor.is_strategy_ready()
        self.assertFalse(ready_disc)
        self.assertEqual(reason_disc, "FEED_NOT_HEALTHY: Feed status is DISCONNECTED")

        # 3. Invalid tick arrives while DISCONNECTED at t=1005.0
        self.current_time = 1005.0
        inv_status = self.monitor.record_invalid_tick("MALFORMED_JSON_PAYLOAD")

        # Preserves DISCONNECTED, increments invalid_ticks_count, sets last_error
        self.assertEqual(inv_status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.invalid_ticks_count, 1)
        self.assertEqual(self.monitor.last_error, "MALFORMED_JSON_PAYLOAD")

        # Crucially: last valid arrival timestamps MUST NOT be refreshed!
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # Clock faults are isolated and not confused with data errors
        self.assertEqual(self.monitor.clock_faults_count, 0)

        # 4. A second invalid tick arrives at t=1007.0
        self.current_time = 1007.0
        inv_status_2 = self.monitor.record_invalid_tick("INVALID_OPTION_PRICE")
        self.assertEqual(inv_status_2, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.invalid_ticks_count, 2)
        self.assertEqual(self.monitor.last_error, "INVALID_OPTION_PRICE")
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)

        # 5. check_health() preserves DISCONNECTED
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.DISCONNECTED)

        # 6. Clock fault occurs while DISCONNECTED -> status remains DISCONNECTED, clock fault counted separately
        self.current_time = float("nan")
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.clock_faults_count, 0)  # check_health returns DISCONNECTED before clock check
        self.assertEqual(self.monitor.invalid_ticks_count, 2)

        # 7. Recovery: Clock restored and valid tick arrives at t=1020.0
        self.current_time = 1020.0
        rec_status = self.monitor.record_valid_tick(
            timestamp="2026-10-25 09:20:20.000",
            option_chain_len=25,
            has_futures=True
        )
        self.assertEqual(rec_status, FeedHealthStatus.HEALTHY)
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        ready_rec, reason_rec = self.monitor.is_strategy_ready()
        self.assertTrue(ready_rec)
        self.assertIsNone(reason_rec)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1020.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:20.000")
        self.assertEqual(self.monitor.valid_ticks_count, 2)
        self.assertEqual(self.monitor.invalid_ticks_count, 2)  # Historical counter preserved
        self.assertIsNone(self.monitor.last_error)


if __name__ == "__main__":
    unittest.main()


"""
test_feed_pipeline.py
Phase 2C: Offline Feed Pipeline Integration Tests.

Tests the real public interfaces of the complete feed pipeline in order:
FeedMessageParser -> FeedDataValidator -> FeedDataNormalizer -> FeedHealthMonitor

Uses synthetic byte frames, JSON fixtures, standard-library unittest, and deterministic injected clocks.
Strictly offline: zero network connections, ZeroMQ sockets, or live market data.
"""

import copy
import json
import math
import unittest
from typing import Any, Dict, List, Optional, Tuple, Union

from src.feed.data_normalizer import FeedDataNormalizer
from src.feed.data_validator import FeedDataValidator
from src.feed.feed_health import FeedHealthMonitor, FeedHealthStatus
from src.feed.message_parser import FeedMessageParser
from src.market.market_snapshot import MarketSnapshot


class TestFeedPipelineIntegration(unittest.TestCase):
    """
    Offline integration test suite verifying the end-to-end feed pipeline:
    FeedMessageParser -> FeedDataValidator -> FeedDataNormalizer -> FeedHealthMonitor
    """

    DEFAULT_TOPIC: bytes = b"NIFTY_MARKET_TICK"

    def setUp(self):
        """Initializes deterministic pipeline components with mock monotonic time."""
        self.current_time: float = 1000.0
        self.time_provider = lambda: self.current_time

        self.parser = FeedMessageParser(expected_topic=self.DEFAULT_TOPIC)
        self.validator = FeedDataValidator(require_expiry=True, allow_empty_options=True)
        self.normalizer = FeedDataNormalizer(allow_empty_options=True)
        self.monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=self.time_provider)

    # -------------------------------------------------------------------------
    # Test-Only Helper: Pipeline Orchestration & Stage Verification
    # -------------------------------------------------------------------------
    def _process_pipeline(self, frames: Any) -> Dict[str, Any]:
        """
        Test-only orchestration helper that passes wire frames through the 4 components.
        Tracks exactly which stage fails and guarantees later stages are not invoked on failure.

        Returns:
            Dict containing:
                success: bool
                failed_stage: Optional[str] ("PARSER", "VALIDATOR", "NORMALIZER", None)
                error: Optional[str]
                snapshot: Optional[MarketSnapshot]
                status: Optional[FeedHealthStatus]
        """
        # Stage 1: Parser
        ok_p, payload, err_p = self.parser.parse_multipart(frames)
        if not ok_p:
            self.monitor.record_invalid_tick(err_p)
            return {
                "success": False,
                "failed_stage": "PARSER",
                "error": err_p,
                "snapshot": None,
                "status": self.monitor.status
            }

        # Stage 2: Validator
        ok_v, err_v = self.validator.validate(payload)
        if not ok_v:
            self.monitor.record_invalid_tick(err_v)
            return {
                "success": False,
                "failed_stage": "VALIDATOR",
                "error": err_v,
                "snapshot": None,
                "status": self.monitor.status
            }

        # Stage 3: Normalizer
        ok_n, snapshot, err_n = self.normalizer.normalize(payload)
        if not ok_n:
            self.monitor.record_invalid_tick(err_n)
            return {
                "success": False,
                "failed_stage": "NORMALIZER",
                "error": err_n,
                "snapshot": None,
                "status": self.monitor.status
            }

        # Stage 4: Feed Health Monitor
        status = self.monitor.record_snapshot(snapshot)
        return {
            "success": True,
            "failed_stage": None,
            "error": None,
            "snapshot": snapshot,
            "status": status
        }

    # -------------------------------------------------------------------------
    # Fixture Generators
    # -------------------------------------------------------------------------
    def _create_valid_payload_dict(self) -> Dict[str, Any]:
        """Constructs a standard, fully-formed synthetic market tick dictionary."""
        return {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {
                "symbol": "NIFTY 50",
                "ltp": 24850.0,
                "open": 24800.0,
                "high": 24890.0,
                "low": 24780.0,
                "close": 24820.0,
                "net_change": 30.0,
                "volume": 1500000
            },
            "indices": {
                "india_vix": {
                    "ltp": 14.5,
                    "net_change": -0.2
                }
            },
            "futures": {
                "symbol": "NIFTY_FUT",
                "token": "FUT_NIFTY_OCT",
                "ltp": 24880.0,
                "bid": 24878.0,
                "ask": 24882.0,
                "volume": 500000,
                "open_interest": 10000000,
                "expiry": "2026-10-29"
            },
            "current_expiry": "2026-10-29",
            "options": {
                "24800_CE": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "expiry": "2026-10-29",
                    "ltp": 125.0,
                    "bid": 124.0,
                    "ask": 126.0,
                    "volume": 85000,
                    "open_interest": 2500000,
                    "greeks": {"iv": 14.2, "delta": 0.54, "gamma": 0.0012, "theta": -11.0, "vega": 8.5}
                },
                "24800_PE": {
                    "strike": 24800.0,
                    "option_type": "PE",
                    "expiry": "2026-10-29",
                    "ltp": 80.0,
                    "bid": 79.5,
                    "ask": 80.5,
                    "volume": 90000,
                    "open_interest": 2200000,
                    "greeks": {"iv": 14.0, "delta": -0.46, "gamma": 0.0012, "theta": -10.5, "vega": 8.5}
                },
                "24900_CE": {
                    "strike": 24900.0,
                    "option_type": "CE",
                    "expiry": "2026-10-29",
                    "ltp": 70.0,
                    "bid": 69.5,
                    "ask": 70.5,
                    "volume": 60000,
                    "open_interest": 1800000
                },
                "24700_PE": {
                    "strike": 24700.0,
                    "option_type": "PE",
                    "expiry": "2026-10-29",
                    "ltp": 45.0,
                    "bid": 44.5,
                    "ask": 45.5,
                    "volume": 55000,
                    "open_interest": 1500000
                }
            }
        }

    def _create_wire_frames(
        self,
        payload_data: Union[Dict[str, Any], str, bytes],
        topic: bytes = DEFAULT_TOPIC
    ) -> List[Any]:
        """Encodes payload into multipart ZeroMQ byte frames [topic_bytes, payload_bytes]."""
        if isinstance(payload_data, dict):
            payload_bytes = json.dumps(payload_data).encode("utf-8")
        elif isinstance(payload_data, str):
            payload_bytes = payload_data.encode("utf-8")
        elif isinstance(payload_data, (bytes, bytearray)):
            payload_bytes = bytes(payload_data)
        else:
            payload_bytes = payload_data  # allow passing non-bytes to test parser type checks
        return [topic, payload_bytes]

    # -------------------------------------------------------------------------
    # Scenario 1: Valid End-to-End Pipeline & Strategy Readiness
    # -------------------------------------------------------------------------
    def test_01_valid_end_to_end_pipeline_and_strategy_readiness(self):
        """1. Valid wire frames pass through all 4 stages and establish strategy readiness."""
        payload_dict = self._create_valid_payload_dict()
        frames = self._create_wire_frames(payload_dict)

        result = self._process_pipeline(frames)

        self.assertTrue(result["success"])
        self.assertIsNone(result["failed_stage"])
        self.assertIsNone(result["error"])

        snapshot = result["snapshot"]
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertEqual(snapshot.spot_price, 24850.0)
        self.assertIsNotNone(snapshot.futures)
        self.assertEqual(snapshot.futures.ltp, 24880.0)
        self.assertEqual(len(snapshot.option_chain), 4)

        # Health Monitor State
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)
        self.assertEqual(self.monitor.valid_ticks_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)
        self.assertEqual(self.monitor.clock_faults_count, 0)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # Strategy Readiness Gate
        ready, block_reason = self.monitor.is_strategy_ready(min_options=4, require_futures=True)
        self.assertTrue(ready)
        self.assertIsNone(block_reason)

    # -------------------------------------------------------------------------
    # Scenario 2: Wire, Framing, & Serialization Faults (Parser Stage Rejection)
    # -------------------------------------------------------------------------
    def test_02_wire_framing_and_parser_rejections(self):
        """2. Malformed framing, bad encodings, invalid JSON, and oversized payloads reject at Parser."""
        invalid_frames_cases = [
            ("single_frame", [b"NIFTY_MARKET_TICK"], "INVALID_FRAME_COUNT"),
            ("three_frames", [b"T1", b"T2", b"T3"], "INVALID_FRAME_COUNT"),
            ("non_bytes_topic", ["NOT_BYTES", b"{}"], "INVALID_TOPIC_TYPE"),
            ("non_bytes_payload", [b"NIFTY_MARKET_TICK", 12345], "INVALID_PAYLOAD_TYPE"),
            ("wrong_topic", [b"BANKNIFTY_MARKET_TICK", b"{}"], "INVALID_TOPIC"),
            ("invalid_utf8", [b"NIFTY_MARKET_TICK", b"\x80\xff\xfe"], "INVALID_ENCODING"),
            ("malformed_json_syntax", [b"NIFTY_MARKET_TICK", b'{"spot": {'], "MALFORMED_JSON"),
            ("json_scalar_string", [b"NIFTY_MARKET_TICK", b'"plain string"'], "INVALID_PAYLOAD_TYPE"),
            ("json_list", [b"NIFTY_MARKET_TICK", b'[{"spot": 24000}]'], "INVALID_PAYLOAD_TYPE"),
            ("json_nan_constant", [b"NIFTY_MARKET_TICK", b'{"spot": {"ltp": NaN}}'], "MALFORMED_JSON"),
            ("oversized_payload", [b"NIFTY_MARKET_TICK", b"x" * (5 * 1024 * 1024 + 1)], "PAYLOAD_TOO_LARGE"),
        ]

        for case_name, bad_frames, expected_err_sub in invalid_frames_cases:
            with self.subTest(case=case_name):
                result = self._process_pipeline(bad_frames)
                self.assertFalse(result["success"])
                self.assertEqual(result["failed_stage"], "PARSER")
                self.assertIn(expected_err_sub, result["error"])
                self.assertIsNone(result["snapshot"])
                self.assertEqual(self.monitor.status, FeedHealthStatus.INVALID_DATA)
                self.assertFalse(self.monitor.is_healthy)

                # Readiness must be blocked
                ready, reason = self.monitor.is_strategy_ready()
                self.assertFalse(ready)
                self.assertIn("FEED_NOT_HEALTHY", reason)

        # Counter check: exactly 11 parser failures recorded
        self.assertEqual(self.monitor.invalid_ticks_count, len(invalid_frames_cases))
        self.assertEqual(self.monitor.valid_ticks_count, 0)
        self.assertIsNone(self.monitor.last_valid_receipt_monotonic)

    # -------------------------------------------------------------------------
    # Scenario 3: Schema & Financial Domain Rejections (Validator Stage Rejection)
    # -------------------------------------------------------------------------
    def test_03_validator_domain_and_schema_rejections(self):
        """3. Domain violations (negative prices, invalid strikes, bad expiries) reject at Validator."""
        valid_base = self._create_valid_payload_dict()

        def make_mutated_payload(mutator):
            payload = copy.deepcopy(valid_base)
            mutator(payload)
            return payload

        validator_test_cases = [
            ("missing_spot", lambda p: p.pop("spot"), "MISSING_FIELD"),
            ("negative_spot_ltp", lambda p: p["spot"].__setitem__("ltp", -100.0), "INVALID_PRICE"),
            ("zero_spot_ltp", lambda p: p["spot"].__setitem__("ltp", 0.0), "INVALID_PRICE"),
            ("non_finite_spot_price", lambda p: p["spot"].__setitem__("open", "NaN_STRING"), "NON_FINITE_VALUE"),
            ("negative_option_strike", lambda p: p["options"]["24800_CE"].__setitem__("strike", -24800.0), "INVALID_STRIKE"),
            ("zero_option_strike", lambda p: p["options"]["24800_CE"].__setitem__("strike", 0.0), "INVALID_STRIKE"),
            ("invalid_option_type", lambda p: p["options"]["24800_CE"].__setitem__("option_type", "CALL"), "INVALID_OPTION_TYPE"),
            ("impossible_calendar_expiry", lambda p: p["options"]["24800_CE"].__setitem__("expiry", "2026-02-30"), "INVALID_EXPIRY_FORMAT"),
            ("ambiguous_date_format", lambda p: p["options"]["24800_CE"].__setitem__("expiry", "29/10/2026"), "INVALID_EXPIRY_FORMAT"),
            ("crossed_futures_spread", lambda p: (p["futures"].__setitem__("bid", 24890.0), p["futures"].__setitem__("ask", 24880.0)), "INVALID_PRICE"),
            ("crossed_option_spread", lambda p: (p["options"]["24800_CE"].__setitem__("bid", 130.0), p["options"]["24800_CE"].__setitem__("ask", 120.0)), "INVALID_PRICE"),
            ("negative_volume_count", lambda p: p["futures"].__setitem__("volume", -50), "INVALID_COUNT"),
            ("boolean_oi_count", lambda p: p["futures"].__setitem__("open_interest", True), "INVALID_COUNT"),
        ]

        for case_name, mutator, expected_err_sub in validator_test_cases:
            with self.subTest(case=case_name):
                bad_dict = make_mutated_payload(mutator)
                frames = self._create_wire_frames(bad_dict)
                result = self._process_pipeline(frames)

                self.assertFalse(result["success"])
                self.assertEqual(result["failed_stage"], "VALIDATOR")
                self.assertIn(expected_err_sub, result["error"])
                self.assertIsNone(result["snapshot"])
                self.assertEqual(self.monitor.status, FeedHealthStatus.INVALID_DATA)

        self.assertEqual(self.monitor.invalid_ticks_count, len(validator_test_cases))
        self.assertEqual(self.monitor.valid_ticks_count, 0)

    # -------------------------------------------------------------------------
    # Scenario 4: Normalizer Rejections & Contract Conflicts
    # -------------------------------------------------------------------------
    def test_04_normalizer_rejections_and_contract_conflicts(self):
        """4. Conflicting OI aliases, expiry conflicts, and duplicate contracts reject at Normalizer."""
        valid_base = self._create_valid_payload_dict()

        # Case A: Conflicting OI aliases on an option contract
        conflicting_oi = copy.deepcopy(valid_base)
        conflicting_oi["options"]["24800_CE"]["oi"] = 10000
        conflicting_oi["options"]["24800_CE"]["open_interest"] = 20000

        res_a = self._process_pipeline(self._create_wire_frames(conflicting_oi))
        self.assertFalse(res_a["success"])
        self.assertEqual(res_a["failed_stage"], "NORMALIZER")
        self.assertIn("CONFLICTING_OI_ALIASES", res_a["error"])
        self.assertIsNone(res_a["snapshot"])

        # Case B: Expiry conflict between enclosing bucket and contract expiry
        expiry_conflict = copy.deepcopy(valid_base)
        expiry_conflict["options"] = {
            "current_expiry": {
                "24800_CE": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "expiry": "2026-11-05",  # Conflicts with top-level current_expiry 2026-10-29
                    "ltp": 125.0
                }
            }
        }
        res_b = self._process_pipeline(self._create_wire_frames(expiry_conflict))
        self.assertFalse(res_b["success"])
        self.assertEqual(res_b["failed_stage"], "NORMALIZER")
        self.assertIn("EXPIRY_CONFLICT", res_b["error"])
        self.assertIsNone(res_b["snapshot"])

        # Case C: Duplicate canonical contract key in option chain
        duplicate_contract = copy.deepcopy(valid_base)
        duplicate_contract["options"] = {
            "opt_1": {"strike": 24800.0, "option_type": "CE", "expiry": "2026-10-29", "ltp": 125.0},
            "opt_2": {"strike": 24800.0, "option_type": "CE", "expiry": "2026-10-29", "ltp": 125.0}
        }
        res_c = self._process_pipeline(self._create_wire_frames(duplicate_contract))
        self.assertFalse(res_c["success"])
        self.assertEqual(res_c["failed_stage"], "NORMALIZER")
        self.assertIn("DUPLICATE_CONTRACT", res_c["error"])
        self.assertIsNone(res_c["snapshot"])

        self.assertEqual(self.monitor.invalid_ticks_count, 3)

    # -------------------------------------------------------------------------
    # Scenario 5: Empty Options & Missing Futures Readiness Separation
    # -------------------------------------------------------------------------
    def test_05_empty_options_and_missing_futures_readiness_separation(self):
        """5. Transport link health remains strictly distinct from quantitative strategy readiness."""
        valid_base = self._create_valid_payload_dict()

        # Case A: Empty options container
        empty_options_dict = copy.deepcopy(valid_base)
        empty_options_dict["options"] = {}

        res_a = self._process_pipeline(self._create_wire_frames(empty_options_dict))
        self.assertTrue(res_a["success"])
        self.assertIsNotNone(res_a["snapshot"])
        self.assertEqual(len(res_a["snapshot"].option_chain), 0)

        # Feed transport is fresh and healthy!
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)

        # Strategy requiring options must be blocked fail-closed
        ready_opt, reason_opt = self.monitor.is_strategy_ready(min_options=1)
        self.assertFalse(ready_opt)
        self.assertIn("INSUFFICIENT_OPTIONS", reason_opt)

        # Pure spot strategy requiring zero options passes
        ready_spot, _ = self.monitor.is_strategy_ready(min_options=0, require_futures=False)
        self.assertTrue(ready_spot)

        # Case B: Missing futures quote
        self.monitor.reset()
        missing_futures_dict = copy.deepcopy(valid_base)
        missing_futures_dict["futures"] = None

        res_b = self._process_pipeline(self._create_wire_frames(missing_futures_dict))
        self.assertTrue(res_b["success"])
        self.assertIsNone(res_b["snapshot"].futures)
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)

        # Strategy requiring futures must be blocked
        ready_fut, reason_fut = self.monitor.is_strategy_ready(min_options=1, require_futures=True)
        self.assertFalse(ready_fut)
        self.assertIn("MISSING_FUTURES", reason_fut)

        # Strategy not requiring futures passes
        ready_no_fut, _ = self.monitor.is_strategy_ready(min_options=1, require_futures=False)
        self.assertTrue(ready_no_fut)

    # -------------------------------------------------------------------------
    # Scenario 6: Timestamp Poisoning Immunity
    # -------------------------------------------------------------------------
    def test_06_rejected_messages_never_refresh_timestamps(self):
        """6. Upstream parser, validator, or normalizer failures never update valid arrival timestamps."""
        # 1. Successful initial valid tick at t=1000.0
        self.current_time = 1000.0
        valid_dict = self._create_valid_payload_dict()
        res_initial = self._process_pipeline(self._create_wire_frames(valid_dict))
        self.assertTrue(res_initial["success"])
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")
        self.assertEqual(self.monitor.valid_ticks_count, 1)

        # 2. Corrupt frame arrives at t=1002.0 (fails Parser)
        self.current_time = 1002.0
        self._process_pipeline([b"NIFTY_MARKET_TICK", b"INVALID_JSON_CORRUPT"])
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # 3. Bad domain frame arrives at t=1003.0 with new exchange timestamp (fails Validator)
        self.current_time = 1003.0
        bad_spot = copy.deepcopy(valid_dict)
        bad_spot["timestamp"] = "2026-10-25 09:20:03.000"
        bad_spot["spot"]["ltp"] = -999.0
        self._process_pipeline(self._create_wire_frames(bad_spot))
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # 4. Conflicting contract frame arrives at t=1004.0 (fails Normalizer)
        self.current_time = 1004.0
        bad_oi = copy.deepcopy(valid_dict)
        bad_oi["timestamp"] = "2026-10-25 09:20:04.000"
        bad_oi["options"]["24800_CE"]["oi"] = 1111
        bad_oi["options"]["24800_CE"]["open_interest"] = 2222
        self._process_pipeline(self._create_wire_frames(bad_oi))
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")

        # Valid ticks count unchanged, exactly 3 invalid ticks counted
        self.assertEqual(self.monitor.valid_ticks_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 3)

    # -------------------------------------------------------------------------
    # Scenario 7: Clock Anomalies Fail Closed Without Corrupting Market Data Count
    # -------------------------------------------------------------------------
    def test_07_clock_anomalies_fail_closed_without_misclassifying_market_data(self):
        """7. Local clock faults increment clock_faults_count without misclassifying market data."""
        self.current_time = 1000.0
        valid_dict = self._create_valid_payload_dict()
        self._process_pipeline(self._create_wire_frames(valid_dict))
        self.assertEqual(self.monitor.valid_ticks_count, 1)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)
        self.assertEqual(self.monitor.clock_faults_count, 0)

        # Inject NaN into mock monotonic clock
        self.current_time = float("nan")
        valid_dict_2 = self._create_valid_payload_dict()
        valid_dict_2["timestamp"] = "2026-10-25 09:20:05.000"

        # The pipeline parses, validates, and normalizes successfully
        res = self._process_pipeline(self._create_wire_frames(valid_dict_2))
        self.assertTrue(res["success"])
        self.assertIsNotNone(res["snapshot"])

        # But FeedHealthMonitor detects clock fault during arrival timestamping
        self.assertEqual(self.monitor.status, FeedHealthStatus.STALE)
        self.assertFalse(self.monitor.is_healthy)
        self.assertTrue(self.monitor.is_stale)

        # CRITICAL FAULT ISOLATION:
        # invalid_ticks_count must NOT increment (market data payload was valid!)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)
        # clock_faults_count MUST increment
        self.assertEqual(self.monitor.clock_faults_count, 1)
        self.assertIn("CLOCK_FAULT", self.monitor.last_error)

        # Strategy readiness must fail-closed
        ready, reason = self.monitor.is_strategy_ready()
        self.assertFalse(ready)
        self.assertIn("FEED_NOT_HEALTHY", reason)

    # -------------------------------------------------------------------------
    # Scenario 8: Stale & Disconnected Recovery Lifecycle
    # -------------------------------------------------------------------------
    def test_08_stale_and_disconnected_recovery_lifecycle(self):
        """8. Stale/disconnected states cannot recover via clock passage; require new valid wire tick."""
        valid_dict = self._create_valid_payload_dict()

        # Step 1: Normal initial tick
        self.current_time = 1000.0
        self._process_pipeline(self._create_wire_frames(valid_dict))
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)

        # Step 2: Elapsed time exceeds 5.0s threshold -> transitions to STALE
        self.current_time = 1010.0
        status_stale = self.monitor.check_health()
        self.assertEqual(status_stale, FeedHealthStatus.STALE)
        self.assertTrue(self.monitor.is_stale)

        # Step 3: Explicit disconnect recorded
        self.monitor.record_disconnect("SOCKET_DROPPED")
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)

        # Step 4: Invalid frame arrives while disconnected -> rejected
        self._process_pipeline([b"NIFTY_MARKET_TICK", b"BAD_JSON"])
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)

        # Step 5: Disconnect re-recorded
        self.monitor.record_disconnect("RECONNECT_FAILED")
        self.assertEqual(self.monitor.status, FeedHealthStatus.DISCONNECTED)

        # Step 6: Clock fault occurs while disconnected -> DISCONNECTED remains preserved
        self.current_time = float("nan")
        self.assertEqual(self.monitor.check_health(), FeedHealthStatus.DISCONNECTED)

        # Step 7: Clock recovers and fresh valid wire tick arrives -> Full recovery!
        self.current_time = 1025.0
        valid_dict_recovered = self._create_valid_payload_dict()
        valid_dict_recovered["timestamp"] = "2026-10-25 09:20:25.000"

        res_rec = self._process_pipeline(self._create_wire_frames(valid_dict_recovered))
        self.assertTrue(res_rec["success"])
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)
        self.assertTrue(self.monitor.is_healthy)
        self.assertFalse(self.monitor.is_stale)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1025.0)

        # Readiness restored
        ready, _ = self.monitor.is_strategy_ready()
        self.assertTrue(ready)

    # -------------------------------------------------------------------------
    # Scenario 9: Credential Shielding in Error Messages & Telemetry
    # -------------------------------------------------------------------------
    def test_09_credential_shielding_in_pipeline_errors(self):
        """9. Injected credential keys or secrets are quarantined and never leak in error messages."""
        sensitive_key = "SECRET_API_TOKEN_999888"
        sensitive_pass = "CONFIDENTIAL_PASSWORD_XYZ"

        malicious_payload = self._create_valid_payload_dict()
        malicious_payload["api_key"] = sensitive_key
        malicious_payload["broker_password"] = sensitive_pass
        malicious_payload["spot"]["ltp"] = -50.0  # Force validation failure

        frames = self._create_wire_frames(malicious_payload)
        res = self._process_pipeline(frames)

        self.assertFalse(res["success"])
        self.assertEqual(res["failed_stage"], "VALIDATOR")

        # Assert secrets are NOT leaked in pipeline error message
        self.assertNotIn(sensitive_key, res["error"])
        self.assertNotIn(sensitive_pass, res["error"])

        # Assert secrets are NOT leaked in health monitor last_error
        self.assertNotIn(sensitive_key, str(self.monitor.last_error))
        self.assertNotIn(sensitive_pass, str(self.monitor.last_error))

        # Assert secrets are NOT leaked in health monitor telemetry serialization
        telemetry = self.monitor.to_dict()
        telemetry_str = json.dumps(telemetry)
        self.assertNotIn(sensitive_key, telemetry_str)
        self.assertNotIn(sensitive_pass, telemetry_str)

    # -------------------------------------------------------------------------
    # Scenario 10: Multi-Tick Pipeline Determinism & Snapshot Isolation
    # -------------------------------------------------------------------------
    def test_10_repeated_tick_determinism_and_snapshot_isolation(self):
        """10. Sequential processing of 20 ticks maintains snapshot immutability and accurate counters."""
        valid_dict = self._create_valid_payload_dict()
        frames = self._create_wire_frames(valid_dict)

        snapshots: List[MarketSnapshot] = []

        # Process 20 identical frames across advancing monotonic clock ticks
        for i in range(20):
            self.current_time += 0.5  # 500ms intervals (fresh, well within 5.0s threshold)
            res = self._process_pipeline(frames)
            self.assertTrue(res["success"])
            snapshots.append(res["snapshot"])

        # Verify all 20 snapshots have matching spot prices and contract sets
        self.assertEqual(len(snapshots), 20)
        for snap in snapshots:
            self.assertEqual(snap.spot_price, 24850.0)
            self.assertEqual(len(snap.option_chain), 4)

        # Verify snapshot isolation: mutating snapshot 0 must NOT mutate snapshot 1..19
        snap_0 = snapshots[0]
        snap_1 = snapshots[1]
        self.assertIsNot(snap_0, snap_1)
        self.assertIsNot(snap_0.option_chain, snap_1.option_chain)

        # Mutate quote in snapshot 0
        target_contract = "24800_CE_2026-10-29"
        snap_0.option_chain[target_contract].ltp = 99999.0

        # Snapshot 1 remains pristine
        self.assertEqual(snap_1.option_chain[target_contract].ltp, 125.0)

        # Verify final diagnostic counters
        self.assertEqual(self.monitor.valid_ticks_count, 20)
        self.assertEqual(self.monitor.invalid_ticks_count, 0)
        self.assertEqual(self.monitor.clock_faults_count, 0)
        self.assertEqual(self.monitor.status, FeedHealthStatus.HEALTHY)


if __name__ == "__main__":
    unittest.main()

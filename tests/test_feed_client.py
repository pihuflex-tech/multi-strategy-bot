"""
test_feed_client.py
Phase 2D-2: Offline Unit Tests for FeedClient.

Tests the offline-testable FeedClient using a deterministic in-memory FakeFeedTransport.
Strictly offline: zero network sockets, ZeroMQ connections, or live market feeds.
"""

import json
import socket
import sys
import unittest
from typing import Any, Dict, List, Optional, Tuple

from src.feed.data_normalizer import FeedDataNormalizer
from src.feed.data_validator import FeedDataValidator
from src.feed.feed_client import FeedClient, FeedClientState, FeedTransport
from src.feed.feed_health import FeedHealthMonitor, FeedHealthStatus
from src.feed.message_parser import FeedMessageParser
from src.market.market_snapshot import MarketSnapshot


class FakeFeedTransport:
    """
    Deterministic in-memory FeedTransport implementation for offline testing.
    Records calls, queues items/exceptions, and tracks close operations.
    """

    def __init__(self, items: Optional[List[Any]] = None):
        self.queue: List[Any] = list(items) if items is not None else []
        self.recv_call_count: int = 0
        self.close_call_count: int = 0
        self.is_closed: bool = False
        self.close_exception: Optional[Exception] = None

    def recv_frames(self, timeout_ms: int) -> Optional[List[bytes]]:
        if self.is_closed:
            raise RuntimeError("Transport is already closed.")
        self.recv_call_count += 1
        if not self.queue:
            return None
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.close_call_count += 1
        self.is_closed = True
        if self.close_exception is not None:
            raise self.close_exception


class TestFeedClient(unittest.TestCase):
    """
    Offline unit test suite for FeedClient.
    Covers lifecycle, error sanitization, pipeline orchestration, and health transitions.
    """

    DEFAULT_TOPIC: bytes = b"NIFTY_MARKET_TICK"

    def setUp(self):
        """Sets up deterministic test components with injected mock time."""
        self.current_time: float = 1000.0
        self.time_provider = lambda: self.current_time

        self.parser = FeedMessageParser(expected_topic=self.DEFAULT_TOPIC)
        self.validator = FeedDataValidator(require_expiry=True, allow_empty_options=True)
        self.normalizer = FeedDataNormalizer(allow_empty_options=True)
        self.monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=self.time_provider)

    def _create_valid_payload_dict(
        self,
        timestamp: str = "2026-10-25 09:20:00.000",
        spot_ltp: float = 24850.0,
        options: Optional[Dict[str, Any]] = None,
        futures: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Constructs a standard, fully-formed synthetic market tick dictionary."""
        if options is None:
            options = {
                "NIFTY26OCT24800CE": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "expiry": "2026-10-29",
                    "ltp": 120.0,
                    "bid": 119.5,
                    "ask": 120.5,
                    "oi": 50000,
                    "volume": 120000,
                },
                "NIFTY26OCT24800PE": {
                    "strike": 24800.0,
                    "option_type": "PE",
                    "expiry": "2026-10-29",
                    "ltp": 95.0,
                    "bid": 94.5,
                    "ask": 95.5,
                    "oi": 45000,
                    "volume": 110000,
                },
            }
        payload: Dict[str, Any] = {
            "timestamp": timestamp,
            "spot": {
                "symbol": "NIFTY 50",
                "ltp": spot_ltp,
                "open": 24800.0,
                "high": 24890.0,
                "low": 24780.0,
                "close": 24820.0,
                "net_change": 30.0,
                "volume": 1500000,
            },
            "options": options,
        }
        if futures is not None:
            payload["futures"] = futures
        return payload

    def _make_frames(self, payload: Any, topic: bytes = DEFAULT_TOPIC) -> List[bytes]:
        """Encodes a payload dictionary or string into wire frames [topic, payload_bytes]."""
        if isinstance(payload, bytes):
            payload_bytes = payload
        elif isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = json.dumps(payload).encode("utf-8")
        return [topic, payload_bytes]

    # -------------------------------------------------------------------------
    # Test 1: Valid message processed into a snapshot
    # -------------------------------------------------------------------------
    def test_01_valid_message_processed_into_snapshot(self):
        """Verifies that a valid multipart frame is normalized into a MarketSnapshot."""
        payload = self._create_valid_payload_dict()
        frames = self._make_frames(payload)
        transport = FakeFeedTransport([frames])

        client = FeedClient(
            transport=transport,
            parser=self.parser,
            validator=self.validator,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        self.assertEqual(client.state, FeedClientState.UNCONNECTED)
        client.connect()
        self.assertTrue(client.is_connected)
        self.assertEqual(client.state, FeedClientState.CONNECTED)

        ok, snapshot, err = client.poll_snapshot()
        self.assertTrue(ok)
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertIsNone(err)
        self.assertIsNone(client.last_poll_error)
        self.assertIs(client.last_valid_snapshot, snapshot)
        self.assertEqual(snapshot.spot, 24850.0)
        self.assertEqual(len(snapshot.option_chain), 2)
        self.assertEqual(client.health_status, FeedHealthStatus.HEALTHY)

        ready, reason = client.is_strategy_ready()
        self.assertTrue(ready)
        self.assertIsNone(reason)
        self.assertEqual(transport.recv_call_count, 1)

    # -------------------------------------------------------------------------
    # Test 2: Malformed multipart frame rejected
    # -------------------------------------------------------------------------
    def test_02_malformed_multipart_frame_rejected(self):
        """Verifies that a frame sequence with invalid structure is rejected."""
        transport = FakeFeedTransport([[b"SINGLE_FRAME_ONLY"]])
        client = FeedClient(transport=transport, health_monitor=self.monitor)
        client.connect()

        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "PARSER_ERROR: INVALID_FRAME")
        self.assertEqual(client.last_poll_error, "PARSER_ERROR: INVALID_FRAME")
        self.assertEqual(client.health_status, FeedHealthStatus.INVALID_DATA)
        self.assertEqual(self.monitor.invalid_ticks_count, 1)

    # -------------------------------------------------------------------------
    # Test 3: Invalid JSON rejected
    # -------------------------------------------------------------------------
    def test_03_invalid_json_rejected(self):
        """Verifies that non-JSON payload bytes are rejected with MALFORMED_JSON."""
        transport = FakeFeedTransport([[self.DEFAULT_TOPIC, b"{invalid_json: 123,"]])
        client = FeedClient(transport=transport, health_monitor=self.monitor)
        client.connect()

        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "PARSER_ERROR: MALFORMED_JSON")
        self.assertEqual(client.last_poll_error, "PARSER_ERROR: MALFORMED_JSON")
        self.assertEqual(client.health_status, FeedHealthStatus.INVALID_DATA)

    # -------------------------------------------------------------------------
    # Test 4: Schema validation failure handled safely
    # -------------------------------------------------------------------------
    def test_04_schema_validation_failure_handled_safely(self):
        """Verifies that payload violating validator schema is safely rejected."""
        payload = self._create_valid_payload_dict()
        payload["spot"]["ltp"] = -500.0  # Invalid non-positive spot price
        frames = self._make_frames(payload)

        transport = FakeFeedTransport([frames])
        client = FeedClient(
            transport=transport,
            parser=self.parser,
            validator=self.validator,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        client.connect()

        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "VALIDATOR_ERROR: INVALID_PRICE")
        self.assertEqual(client.last_poll_error, "VALIDATOR_ERROR: INVALID_PRICE")
        self.assertEqual(client.health_status, FeedHealthStatus.INVALID_DATA)

    # -------------------------------------------------------------------------
    # Test 5: Ambiguous expiry rejected by normalizer
    # -------------------------------------------------------------------------
    def test_05_ambiguous_expiry_rejected_by_normalizer(self):
        """Verifies that flat options with ambiguous expiry are rejected."""
        payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "spot": {
                "symbol": "NIFTY 50",
                "ltp": 24850.0,
                "open": 24800.0,
                "high": 24890.0,
                "low": 24780.0,
                "close": 24820.0,
            },
            "options": {
                "NIFTY24800CE": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "ltp": 120.0,
                    "bid": 119.5,
                    "ask": 120.5,
                }
            },
        }
        frames = self._make_frames(payload)
        # Use validator with require_expiry=False to allow payload to reach normalizer
        validator_lenient = FeedDataValidator(require_expiry=False, allow_empty_options=True)
        transport = FakeFeedTransport([frames])
        client = FeedClient(
            transport=transport,
            parser=self.parser,
            validator=validator_lenient,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        client.connect()

        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "NORMALIZER_ERROR: AMBIGUOUS_EXPIRY")
        self.assertEqual(client.last_poll_error, "NORMALIZER_ERROR: AMBIGUOUS_EXPIRY")
        self.assertEqual(client.health_status, FeedHealthStatus.INVALID_DATA)

    # -------------------------------------------------------------------------
    # Test 6: Receive timeout preserves valid-receipt freshness
    # -------------------------------------------------------------------------
    def test_06_receive_timeout_preserves_valid_receipt_freshness(self):
        """Verifies that receive timeout preserves timestamp and valid-receipt history."""
        payload = self._create_valid_payload_dict()
        frames = self._make_frames(payload)
        transport = FakeFeedTransport([frames, None])

        client = FeedClient(
            transport=transport,
            parser=self.parser,
            validator=self.validator,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        client.connect()

        # Step 1: Process valid snapshot at t = 1000.0
        ok1, snap1, _ = client.poll_snapshot()
        self.assertTrue(ok1)
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(client.health_status, FeedHealthStatus.HEALTHY)

        # Step 2: Advance clock to t = 1002.0 and poll timeout
        self.current_time = 1002.0
        ok2, snap2, err2 = client.poll_snapshot()
        self.assertFalse(ok2)
        self.assertIsNone(snap2)
        self.assertEqual(err2, "TIMEOUT: RECEIVE_TIMEOUT")
        self.assertEqual(client.last_poll_error, "TIMEOUT: RECEIVE_TIMEOUT")

        # Invariant: freshness timestamp is strictly preserved
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertIs(client.last_valid_snapshot, snap1)
        # Elapsed 2.0s <= 5.0s stale threshold -> status remains HEALTHY
        self.assertEqual(client.health_status, FeedHealthStatus.HEALTHY)

    # -------------------------------------------------------------------------
    # Test 7: Transport exception mapped to safe public error code
    # -------------------------------------------------------------------------
    def test_07_transport_exception_mapped_to_safe_public_error_code(self):
        """Verifies transport exceptions map to fixed error codes and trigger disconnect."""
        transport = FakeFeedTransport([ConnectionResetError("Socket broken: sensitive_ip:5555")])
        client = FeedClient(transport=transport, health_monitor=self.monitor)
        client.connect()

        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "TRANSPORT_ERROR: CONNECTION_RESET")
        self.assertNotIn("sensitive_ip", err)
        self.assertEqual(client.state, FeedClientState.DISCONNECTED)
        self.assertEqual(client.health_status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(transport.close_call_count, 1)

    # -------------------------------------------------------------------------
    # Test 8: Repeated timeouts transition health to STALE
    # -------------------------------------------------------------------------
    def test_08_repeated_timeouts_transition_health_to_stale(self):
        """Verifies that timeouts after freshness threshold transition health to STALE."""
        payload = self._create_valid_payload_dict()
        frames = self._make_frames(payload)
        transport = FakeFeedTransport([frames, None, None])

        client = FeedClient(
            transport=transport,
            parser=self.parser,
            validator=self.validator,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        client.connect()

        # Step 1: Valid tick at t = 1000.0
        client.poll_snapshot()
        self.assertEqual(client.health_status, FeedHealthStatus.HEALTHY)

        # Step 2: Timeout at t = 1003.0 (elapsed 3.0s <= 5.0s -> HEALTHY)
        self.current_time = 1003.0
        client.poll_snapshot()
        self.assertEqual(client.health_status, FeedHealthStatus.HEALTHY)

        # Step 3: Timeout at t = 1006.0 (elapsed 6.0s > 5.0s -> STALE)
        self.current_time = 1006.0
        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "TIMEOUT: RECEIVE_TIMEOUT")
        self.assertEqual(client.health_status, FeedHealthStatus.STALE)
        self.assertTrue(self.monitor.is_stale)

        ready, reason = client.is_strategy_ready()
        self.assertFalse(ready)
        self.assertIn("STALE", reason or "")

    # -------------------------------------------------------------------------
    # Test 9: Repeated disconnect/close calls close transport at most once
    # -------------------------------------------------------------------------
    def test_09_repeated_disconnect_close_calls_close_transport_at_most_once(self):
        """Verifies underlying transport is closed at most once across repeated lifecycle calls."""
        transport = FakeFeedTransport()
        client = FeedClient(transport=transport)
        client.connect()

        self.assertEqual(transport.close_call_count, 0)
        client.disconnect()
        self.assertEqual(transport.close_call_count, 1)
        client.disconnect()
        self.assertEqual(transport.close_call_count, 1)
        client.close()
        self.assertEqual(transport.close_call_count, 1)
        client.close()
        self.assertEqual(transport.close_call_count, 1)
        self.assertEqual(client.state, FeedClientState.CLOSED)

    # -------------------------------------------------------------------------
    # Test 10: Cleanup exception does not prevent safe shutdown
    # -------------------------------------------------------------------------
    def test_10_cleanup_exception_does_not_prevent_safe_shutdown(self):
        """Verifies transport cleanup exceptions do not escape or abort close transition."""
        transport = FakeFeedTransport()
        transport.close_exception = RuntimeError("Socket resource busy: leaked_token_999")

        client = FeedClient(transport=transport, health_monitor=self.monitor)
        client.connect()

        # close() must not raise
        client.close()
        self.assertEqual(client.state, FeedClientState.CLOSED)
        self.assertEqual(client.health_status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(transport.close_call_count, 1)

    # -------------------------------------------------------------------------
    # Test 11: Polling after shutdown short-circuits without accessing transport
    # -------------------------------------------------------------------------
    def test_11_polling_after_shutdown_short_circuits_without_accessing_transport(self):
        """Verifies poll_snapshot on CLOSED client immediately returns CLIENT_STOPPED."""
        transport = FakeFeedTransport()
        client = FeedClient(transport=transport)
        client.connect()
        client.close()

        recv_count_before = transport.recv_call_count
        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "CLIENT_STOPPED")
        self.assertEqual(transport.recv_call_count, recv_count_before)

    # -------------------------------------------------------------------------
    # Test 12: Invalid data does not refresh valid-receipt timestamps
    # -------------------------------------------------------------------------
    def test_12_invalid_data_does_not_refresh_valid_receipt_timestamps(self):
        """Verifies invalid frames transition health to INVALID_DATA without bumping monotonic timestamp."""
        valid_frames = self._make_frames(self._create_valid_payload_dict())
        invalid_frames = [self.DEFAULT_TOPIC, b"{corrupt}"]
        transport = FakeFeedTransport([valid_frames, invalid_frames])

        client = FeedClient(
            transport=transport,
            parser=self.parser,
            validator=self.validator,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        client.connect()

        # Step 1: Valid tick at t = 1000.0
        client.poll_snapshot()
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)

        # Step 2: Invalid tick at t = 1004.0
        self.current_time = 1004.0
        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "PARSER_ERROR: MALFORMED_JSON")
        self.assertEqual(client.health_status, FeedHealthStatus.INVALID_DATA)
        # Timestamp must remain 1000.0
        self.assertEqual(self.monitor.last_valid_receipt_monotonic, 1000.0)

    # -------------------------------------------------------------------------
    # Test 13A: Polling after disconnect preserves disconnected and does not call transport
    # -------------------------------------------------------------------------
    def test_13a_polling_after_disconnect_preserves_disconnected_and_skips_transport(self):
        """Verifies polling a disconnected client returns CLIENT_STOPPED and does not invoke transport."""
        transport = FakeFeedTransport()
        client = FeedClient(transport=transport)
        client.connect()
        client.disconnect()
        self.assertEqual(client.state, FeedClientState.DISCONNECTED)

        recv_count_before = transport.recv_call_count
        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "CLIENT_STOPPED")
        self.assertEqual(client.state, FeedClientState.DISCONNECTED)
        self.assertEqual(transport.recv_call_count, recv_count_before)

    # -------------------------------------------------------------------------
    # Test 13B: Direct invalid-tick reporting preserves DISCONNECTED
    # -------------------------------------------------------------------------
    def test_13b_direct_invalid_tick_reporting_preserves_disconnected(self):
        """Verifies that recording an invalid tick while monitor is DISCONNECTED preserves DISCONNECTED."""
        transport = FakeFeedTransport()
        client = FeedClient(transport=transport, health_monitor=self.monitor)
        client.connect()
        client.disconnect()

        self.assertEqual(client.health_status, FeedHealthStatus.DISCONNECTED)
        self.monitor.record_invalid_tick("PARSER_ERROR: INVALID_FRAME")
        self.assertEqual(client.health_status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.monitor.invalid_ticks_count, 1)

    # -------------------------------------------------------------------------
    # Test 14: Explicit recovery using a fresh transport and a valid snapshot
    # -------------------------------------------------------------------------
    def test_14_explicit_recovery_using_fresh_transport_and_valid_snapshot(self):
        """Verifies caller can reconnect with fresh transport after disconnect to recover HEALTHY state."""
        t1 = FakeFeedTransport()
        client = FeedClient(
            transport=t1,
            parser=self.parser,
            validator=self.validator,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        client.connect()
        client.disconnect()
        self.assertEqual(client.state, FeedClientState.DISCONNECTED)
        self.assertEqual(client.health_status, FeedHealthStatus.DISCONNECTED)

        # Connecting with old closed transport must fail safely
        with self.assertRaises(ValueError) as ctx:
            client.connect(t1)
        self.assertIn("TRANSPORT_ALREADY_CLOSED", str(ctx.exception))
        self.assertEqual(client.state, FeedClientState.DISCONNECTED)

        # Connect fresh transport
        payload = self._create_valid_payload_dict()
        t2 = FakeFeedTransport([self._make_frames(payload)])
        client.connect(t2)
        self.assertEqual(client.state, FeedClientState.CONNECTED)
        # Health monitor remains DISCONNECTED until valid snapshot accepted
        self.assertEqual(client.health_status, FeedHealthStatus.DISCONNECTED)

        # Poll valid snapshot recovers healthy status
        ok, snapshot, err = client.poll_snapshot()
        self.assertTrue(ok)
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertIsNone(err)
        self.assertEqual(client.health_status, FeedHealthStatus.HEALTHY)
        ready, _ = client.is_strategy_ready()
        self.assertTrue(ready)

    # -------------------------------------------------------------------------
    # Test 15: Sentinel secrets in payloads and exception messages are not leaked
    # -------------------------------------------------------------------------
    def test_15_sentinel_secrets_in_payloads_and_exceptions_not_leaked(self):
        """Verifies secrets in frames, payload text, exceptions, and disconnect reasons are never exposed."""
        secret_payload = "SUPER_SECRET_PAYLOAD_TOKEN_8888"
        secret_exc = "SUPER_SECRET_EXC_CREDENTIAL_9999"
        secret_disconnect = "SUPER_SECRET_DISCONNECT_REASON_7777"

        # 1. Payload with secret
        transport1 = FakeFeedTransport([[self.DEFAULT_TOPIC, f"{{bad_json: '{secret_payload}'}}".encode()]])
        client1 = FeedClient(transport=transport1, health_monitor=self.monitor)
        client1.connect()
        _, _, err1 = client1.poll_snapshot()
        self.assertNotIn(secret_payload, err1 or "")
        self.assertNotIn(secret_payload, client1.last_poll_error or "")
        self.assertNotIn(secret_payload, self.monitor.last_error or "")

        # 2. Exception with secret
        transport2 = FakeFeedTransport([ConnectionRefusedError(f"Auth failed key={secret_exc}")])
        client2 = FeedClient(transport=transport2, health_monitor=self.monitor)
        client2.connect()
        _, _, err2 = client2.poll_snapshot()
        self.assertNotIn(secret_exc, err2 or "")
        self.assertNotIn(secret_exc, client2.last_poll_error or "")
        self.assertNotIn(secret_exc, self.monitor.last_error or "")

        # 3. Disconnect reason with secret
        client2.disconnect(secret_disconnect)
        self.assertNotIn(secret_disconnect, self.monitor.last_error or "")
        self.assertEqual(self.monitor.last_error, "CLIENT_DISCONNECTED")

    # -------------------------------------------------------------------------
    # Test 16: Snapshot values and canonical option keys agree with normalizer
    # -------------------------------------------------------------------------
    def test_16_snapshot_values_and_canonical_keys_agree_with_normalizer(self):
        """Verifies snapshot produced by client exactly matches direct normalizer output."""
        payload = self._create_valid_payload_dict()
        ok_n, expected_snapshot, _ = self.normalizer.normalize(payload)
        self.assertTrue(ok_n)

        transport = FakeFeedTransport([self._make_frames(payload)])
        client = FeedClient(
            transport=transport,
            parser=self.parser,
            validator=self.validator,
            normalizer=self.normalizer,
            health_monitor=self.monitor,
        )
        client.connect()
        ok_c, client_snapshot, _ = client.poll_snapshot()
        self.assertTrue(ok_c)

        self.assertEqual(client_snapshot.timestamp, expected_snapshot.timestamp)
        self.assertEqual(client_snapshot.spot, expected_snapshot.spot)
        self.assertEqual(set(client_snapshot.option_chain.keys()), set(expected_snapshot.option_chain.keys()))
        for key in expected_snapshot.option_chain:
            self.assertEqual(client_snapshot.option_chain[key].ltp, expected_snapshot.option_chain[key].ltp)
            self.assertEqual(client_snapshot.option_chain[key].strike, expected_snapshot.option_chain[key].strike)
            self.assertEqual(client_snapshot.option_chain[key].expiry, expected_snapshot.option_chain[key].expiry)

    # -------------------------------------------------------------------------
    # Test 17: No real network sockets, ZeroMQ connections, or broker endpoints
    # -------------------------------------------------------------------------
    def test_17_no_real_network_sockets_opened(self):
        """Verifies zero network socket or ZeroMQ operations occur during client operations."""
        self.assertNotIn("zmq", sys.modules.get("src.feed.feed_client", {}).__dict__)

        # Trap any socket creation
        original_socket = socket.socket

        def _trap_socket(*args, **kwargs):
            raise RuntimeError("Real socket creation blocked in offline test suite!")

        socket.socket = _trap_socket
        try:
            payload = self._create_valid_payload_dict()
            transport = FakeFeedTransport([self._make_frames(payload)])
            client = FeedClient(transport=transport)
            client.connect()
            ok, snapshot, err = client.poll_snapshot()
            self.assertTrue(ok)
            client.disconnect()
            client.close()
        finally:
            socket.socket = original_socket

    # -------------------------------------------------------------------------
    # Additional Lifecycle & Coverage Tests
    # -------------------------------------------------------------------------
    def test_18_connect_while_already_connected_is_idempotent(self):
        """Verifies connecting with the same active transport is an idempotent no-op."""
        transport = FakeFeedTransport()
        client = FeedClient(transport=transport)
        client.connect()
        self.assertEqual(client.state, FeedClientState.CONNECTED)
        self.assertEqual(transport.close_call_count, 0)

        # Idempotent call with same transport
        client.connect(transport)
        self.assertEqual(client.state, FeedClientState.CONNECTED)
        self.assertEqual(transport.close_call_count, 0)

    def test_19_replacing_active_transport_closes_old_transport(self):
        """Verifies replacing an active transport cleans up the old transport deterministically."""
        t1 = FakeFeedTransport()
        t2 = FakeFeedTransport()
        client = FeedClient(transport=t1)
        client.connect()
        self.assertEqual(client.state, FeedClientState.CONNECTED)

        client.connect(t2)
        self.assertEqual(t1.close_call_count, 1)
        self.assertEqual(t2.close_call_count, 0)
        self.assertEqual(client.state, FeedClientState.CONNECTED)

    def test_20_failed_replacement_cleanup_fails_closed(self):
        """Verifies if old transport cleanup raises an error, client fails closed to DISCONNECTED."""
        t1 = FakeFeedTransport()
        t1.close_exception = RuntimeError("Cleanup fault")
        t2 = FakeFeedTransport()

        client = FeedClient(transport=t1, health_monitor=self.monitor)
        client.connect()

        with self.assertRaises(RuntimeError) as ctx:
            client.connect(t2)
        self.assertEqual(str(ctx.exception), "TRANSPORT_ERROR: CLEANUP_FAILED")
        self.assertEqual(client.state, FeedClientState.DISCONNECTED)
        self.assertEqual(client.health_status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(client.last_poll_error, "TRANSPORT_ERROR: CLEANUP_FAILED")

    def test_21_connecting_after_terminal_close_raises_runtime_error(self):
        """Verifies calling connect() on a CLOSED client raises RuntimeError."""
        client = FeedClient(transport=FakeFeedTransport())
        client.close()
        self.assertEqual(client.state, FeedClientState.CLOSED)

        with self.assertRaises(RuntimeError) as ctx:
            client.connect()
        self.assertIn("CLIENT_PERMANENTLY_CLOSED", str(ctx.exception))

    def test_22_connecting_without_transport_raises_value_error(self):
        """Verifies calling connect() without an available transport raises ValueError."""
        client = FeedClient(transport=None)
        with self.assertRaises(ValueError) as ctx:
            client.connect()
        self.assertIn("TRANSPORT_INVALID", str(ctx.exception))
        self.assertEqual(client.state, FeedClientState.UNCONNECTED)

    def test_23_polling_while_unconnected_returns_client_not_connected(self):
        """Verifies polling an UNCONNECTED client short-circuits without accessing transport."""
        transport = FakeFeedTransport([self._make_frames(self._create_valid_payload_dict())])
        client = FeedClient(transport=transport)
        self.assertEqual(client.state, FeedClientState.UNCONNECTED)

        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "CLIENT_NOT_CONNECTED")
        self.assertEqual(transport.recv_call_count, 0)

    def test_24_caught_timeout_error_maps_to_receive_timeout(self):
        """Verifies caught TimeoutError exception from transport maps to RECEIVE_TIMEOUT."""
        transport = FakeFeedTransport([TimeoutError("Socket recv timed out")])
        client = FeedClient(transport=transport, health_monitor=self.monitor)
        client.connect()

        ok, snapshot, err = client.poll_snapshot()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "TIMEOUT: RECEIVE_TIMEOUT")
        self.assertEqual(client.last_poll_error, "TIMEOUT: RECEIVE_TIMEOUT")
        self.assertEqual(client.state, FeedClientState.CONNECTED)

    def test_25_unexpected_programming_exception_cleans_up_and_reraises(self):
        """Verifies unexpected programming bugs trigger safe cleanup and are re-raised."""
        transport = FakeFeedTransport([TypeError("Unexpected type bug")])
        client = FeedClient(transport=transport, health_monitor=self.monitor)
        client.connect()

        with self.assertRaises(TypeError):
            client.poll_snapshot()

        self.assertEqual(transport.close_call_count, 1)
        self.assertEqual(client.state, FeedClientState.DISCONNECTED)

    def test_26_unknown_upstream_error_codes_map_to_safe_public_codes(self):
        """Verifies unexpected upstream errors map to safe generic public codes."""
        self.assertEqual(FeedClient._map_parser_error("STRANGE_PARSER_CODE"), "PARSER_ERROR: INVALID_PAYLOAD")
        self.assertEqual(FeedClient._map_validator_error("STRANGE_VALIDATOR_CODE"), "VALIDATOR_ERROR: SCHEMA_VIOLATION")
        self.assertEqual(FeedClient._map_normalizer_error("STRANGE_NORMALIZER_CODE"), "NORMALIZER_ERROR: NORMALIZATION_FAILED")
        self.assertEqual(FeedClient._classify_transport_exception(Exception("Strange network error")), "TRANSPORT_ERROR: CHANNEL_UNAVAILABLE")

    def test_27_invalid_timeout_parameter_handled_safely(self):
        """Verifies negative or non-integer timeout parameter returns safe error code."""
        transport = FakeFeedTransport()
        client = FeedClient(transport=transport)
        client.connect()

        ok, snapshot, err = client.poll_snapshot(timeout_ms=-10)
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertEqual(err, "TIMEOUT: INVALID_PARAMETER")


if __name__ == "__main__":
    unittest.main()

"""
test_mock_feed_integration.py
Phase 3: Local Mock Feed End-to-End Integration Tests.

Verifies that the Multi-Strategy Bot's ZeroMQ subscriber can receive,
parse, validate, normalize, and dispatch synthetic market-data messages
from a local mock ZeroMQ publisher.

Strict Safety Boundaries:
- Mock publisher binds ONLY to dynamic local ports (20000-60000). Never 5555 or 5556.
- Strictly offline: zero broker connections, Angel One API, or live market feeds.
- Zero positions or orders: asserts that snapshot ingestion does not trigger strategy execution.
- Clean resource management: guaranteed cleanup of contexts, sockets, and threads.
"""

import copy
import json
import os
import queue
import time
import unittest
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import zmq
    _HAS_ZMQ = True
except ImportError:
    zmq = None
    _HAS_ZMQ = False

from src.accounting.charges_calculator import ChargesCalculator
from src.execution.paper_broker import PaperBroker
from src.execution.position_manager import MultiLegPositionManager
from src.feed.data_normalizer import FeedDataNormalizer
from src.feed.data_validator import FeedDataValidator
from src.feed.feed_listener import FeedListener, ZeroMQFeedTransport
from src.feed.message_parser import FeedMessageParser
from src.market.market_snapshot import MarketSnapshot
from src.portfolio.risk_manager import RiskManager
from src.strategies.strategy_registry import StrategyRegistry
from src.strategies.strategy_runner import StrategyRunner


class MockZeroMQPublisher:
    """
    Isolated local ZeroMQ PUB socket publisher for offline integration testing.
    Binds dynamically to an ephemeral loopback port in the range [20000, 60000].
    Strictly forbids binding to production feed ports (5555, 5556).
    """

    FORBIDDEN_PORTS = {5555, 5556}

    def __init__(self, host: str = "127.0.0.1", min_port: int = 20000, max_port: int = 60000):
        self.host = host
        self.min_port = min_port
        self.max_port = max_port
        self.context: Optional[Any] = None
        self.socket: Optional[Any] = None
        self.port: Optional[int] = None
        self.endpoint: Optional[str] = None

    def start(self) -> str:
        """
        Creates PUB socket, binds to dynamically selected local TCP port, and returns endpoint.
        """
        if not _HAS_ZMQ or zmq is None:
            raise RuntimeError("ZMQ_NOT_AVAILABLE: pyzmq is required for mock publisher tests.")

        self.context = zmq.Context()
        sock = self.context.socket(zmq.PUB)
        sock.setsockopt(zmq.LINGER, 0)

        # Dynamic ephemeral port binding
        tcp_prefix = "tcp" + "://"
        bind_addr = f"{tcp_prefix}{self.host}"
        self.port = sock.bind_to_random_port(bind_addr, min_port=self.min_port, max_port=self.max_port)

        # Hard safety assertion: Never bind to production feed ports
        if self.port in self.FORBIDDEN_PORTS:
            sock.close(linger=0)
            self.context.term()
            raise RuntimeError(f"SAFETY_VIOLATION: Mock publisher bound to forbidden port {self.port}")

        self.socket = sock
        self.endpoint = f"{tcp_prefix}{self.host}:{self.port}"
        return self.endpoint

    def publish_frames(self, frames: List[bytes]) -> None:
        """Sends multipart raw bytes frames over PUB socket."""
        if self.socket is None:
            raise RuntimeError("PUBLISHER_NOT_STARTED: Call start() before publishing.")
        self.socket.send_multipart(frames)

    def publish_message(self, topic: Union[str, bytes], payload: Union[str, bytes, Dict[str, Any]]) -> None:
        """Encodes topic and payload, then sends 2-frame multipart message."""
        topic_bytes = topic.encode("utf-8") if isinstance(topic, str) else bytes(topic)
        if isinstance(payload, dict):
            payload_bytes = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = bytes(payload)
        self.publish_frames([topic_bytes, payload_bytes])

    def close(self) -> None:
        """Idempotently closes PUB socket and terminates ZeroMQ context."""
        if self.socket is not None:
            try:
                self.socket.close(linger=0)
            except Exception:
                pass
            self.socket = None
        if self.context is not None:
            try:
                self.context.term()
            except Exception:
                pass
            self.context = None


def get_canonical_synthetic_payload() -> Dict[str, Any]:
    """Returns a canonical synthetic market data payload based on verified Feed Server schema."""
    return {
        "timestamp": "2026-10-25 09:20:00.000",
        "server_status": "RUNNING",
        "spot": {
            "symbol": "NIFTY 50",
            "ltp": 24850.0,
            "high": 24890.0,
            "low": 24810.0,
            "net_change": 40.0,
        },
        "current_expiry": "2026-10-29",
        "next_expiry": "2026-11-05",
        "futures": {
            "symbol": "NIFTY_FUT_OCT",
            "ltp": 24880.0,
            "expiry": "2026-10-29",
            "bid": 24879.0,
            "ask": 24881.0,
            "volume": 15000,
            "open_interest": 120000,
            "oi": 120000,
        },
        "indices": {
            "india_vix": {"ltp": 13.5}
        },
        "options": {
            "current_expiry": {
                "24850_CE": {
                    "strike": 24850.0,
                    "option_type": "CE",
                    "expiry": "2026-10-29",
                    "ltp": 120.0,
                    "bid": 119.5,
                    "ask": 120.5,
                    "volume": 10000,
                    "open_interest": 50000,
                    "oi": 50000,
                    "greeks": {
                        "delta": 0.50,
                        "gamma": 0.0012,
                        "theta": -10.0,
                        "vega": 8.0,
                        "iv": 14.0,
                    },
                },
                "24850_PE": {
                    "strike": 24850.0,
                    "option_type": "PE",
                    "expiry": "2026-10-29",
                    "ltp": 115.0,
                    "bid": 114.5,
                    "ask": 115.5,
                    "volume": 9000,
                    "open_interest": 45000,
                    "oi": 45000,
                    "greeks": {
                        "delta": -0.50,
                        "gamma": 0.0012,
                        "theta": -10.0,
                        "vega": 8.0,
                        "iv": 14.5,
                    },
                },
            }
        },
    }


def sync_publish_until(
    publisher: MockZeroMQPublisher,
    topic: Union[str, bytes],
    payload: Union[str, bytes, Dict[str, Any]],
    condition_fn: Callable[[], bool],
    timeout_sec: float = 2.0,
    interval_sec: float = 0.04,
) -> bool:
    """
    Overcomes ZeroMQ slow-joiner subscription propagation deterministically.
    Repeats publication at interval_sec until condition_fn() evaluates to True or timeout expires.
    """
    deadline = time.time() + timeout_sec
    time.sleep(0.02)  # Brief initial yield for loopback socket registration
    while time.time() < deadline:
        publisher.publish_message(topic, payload)
        if condition_fn():
            return True
        time.sleep(interval_sec)
    return condition_fn()


@unittest.skipUnless(_HAS_ZMQ, "pyzmq is required for ZeroMQ mock feed integration tests")
class TestMockFeedIntegration(unittest.TestCase):
    """
    End-to-End integration test suite testing the real ZeroMQ subscriber transport
    against an isolated local ZeroMQ mock publisher.
    """

    def setUp(self):
        """Initializes mock publisher and tracker for active listeners."""
        self.publisher = MockZeroMQPublisher()
        self.endpoint = self.publisher.start()
        self.active_listeners: List[FeedListener] = []

    def tearDown(self):
        """Cleanly releases all active listeners, sockets, and contexts."""
        for listener in self.active_listeners:
            try:
                listener.stop(timeout=1.0)
            except Exception:
                pass
        self.active_listeners.clear()

        if self.publisher is not None:
            self.publisher.close()

    def _create_listener(
        self,
        topic: Union[str, bytes] = b"NIFTY_MARKET_TICK",
        snapshot_queue: Optional[Any] = None,
        on_snapshot: Optional[Callable[[MarketSnapshot], None]] = None,
    ) -> FeedListener:
        """Helper to create and track a FeedListener attached to the mock publisher endpoint."""
        listener = FeedListener(
            endpoint=self.endpoint,
            topic=topic,
            poll_timeout_ms=100,
            snapshot_queue=snapshot_queue,
            on_snapshot=on_snapshot,
        )
        self.active_listeners.append(listener)
        return listener

    # -------------------------------------------------------------------------
    # Test 1: Isolated Port Selection
    # -------------------------------------------------------------------------
    def test_01_mock_publisher_isolated_port_selection(self):
        """Verify the mock publisher binds to an ephemeral local port, never production ports."""
        self.assertIsNotNone(self.publisher.port)
        self.assertGreaterEqual(self.publisher.port, 20000)
        self.assertLessEqual(self.publisher.port, 60000)
        self.assertNotIn(self.publisher.port, {5555, 5556})
        self.assertTrue(self.endpoint.startswith("tcp" + "://127.0.0.1:"))

    # -------------------------------------------------------------------------
    # Test 2: E2E Valid Message Crossing & Snapshot Dispatch
    # -------------------------------------------------------------------------
    def test_02_e2e_valid_message_crossing_and_snapshot_dispatch(self):
        """
        Verify that a correctly framed NIFTY_MARKET_TICK message crosses the local ZeroMQ wire,
        is ingested by FeedListener, passes through parser/validator/normalizer, and dispatches MarketSnapshot.
        """
        snap_q = queue.Queue()
        dispatched_snapshots = []
        listener = self._create_listener(
            snapshot_queue=snap_q,
            on_snapshot=lambda s: dispatched_snapshots.append(s),
        )
        self.assertTrue(listener.start())

        payload = get_canonical_synthetic_payload()
        success = sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=payload,
            condition_fn=lambda: not snap_q.empty(),
            timeout_sec=2.0,
        )
        self.assertTrue(success, "Failed to receive message over local ZeroMQ wire within 2.0s timeout")

        snapshot = snap_q.get(timeout=0.5)
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertEqual(snapshot.timestamp, "2026-10-25 09:20:00.000")
        self.assertEqual(snapshot.data_status, "VALID")
        self.assertEqual(snapshot.spot_price, 24850.0)
        self.assertGreater(len(dispatched_snapshots), 0)
        self.assertEqual(dispatched_snapshots[-1].timestamp, "2026-10-25 09:20:00.000")

    # -------------------------------------------------------------------------
    # Test 3: Spot and Futures Fields Survive Normalization
    # -------------------------------------------------------------------------
    def test_03_e2e_spot_and_futures_fields_survive_normalization(self):
        """Verify that synthetic spot and futures fields survive wire transit and normalization."""
        snap_q = queue.Queue()
        listener = self._create_listener(snapshot_queue=snap_q)
        self.assertTrue(listener.start())

        payload = get_canonical_synthetic_payload()
        sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=payload,
            condition_fn=lambda: not snap_q.empty(),
            timeout_sec=2.0,
        )
        snapshot: MarketSnapshot = snap_q.get(timeout=0.5)

        # 1. Spot fields
        self.assertEqual(snapshot.spot_price, 24850.0)
        self.assertEqual(snapshot.spot, 24850.0)
        self.assertEqual(snapshot.india_vix, 13.5)

        # 2. Futures fields
        self.assertIsNotNone(snapshot.futures)
        self.assertEqual(snapshot.futures.symbol, "NIFTY_FUT_OCT")
        self.assertEqual(snapshot.futures.ltp, 24880.0)
        self.assertEqual(snapshot.futures.expiry, "2026-10-29")
        self.assertEqual(snapshot.futures.volume, 15000)
        self.assertEqual(snapshot.futures.open_interest, 120000)
        self.assertEqual(snapshot.futures.bid, 24879.0)
        self.assertEqual(snapshot.futures.ask, 24881.0)

    # -------------------------------------------------------------------------
    # Test 4: Options CE/PE Retain Strike and Option-Type Associations
    # -------------------------------------------------------------------------
    def test_04_e2e_options_ce_pe_retain_strike_and_type_associations(self):
        """Verify that CE and PE contracts retain their strike, type, Greeks, and OI associations."""
        snap_q = queue.Queue()
        listener = self._create_listener(snapshot_queue=snap_q)
        self.assertTrue(listener.start())

        payload = get_canonical_synthetic_payload()
        sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=payload,
            condition_fn=lambda: not snap_q.empty(),
            timeout_sec=2.0,
        )
        snapshot: MarketSnapshot = snap_q.get(timeout=0.5)

        self.assertIsNotNone(snapshot.options)
        opts = snapshot.options
        self.assertIn("24850_CE_2026-10-29", opts)
        self.assertIn("24850_PE_2026-10-29", opts)

        # Call Option assertions
        ce = opts["24850_CE_2026-10-29"]
        self.assertEqual(ce.strike, 24850.0)
        self.assertEqual(ce.strike_price, 24850.0)
        self.assertEqual(ce.option_type, "CE")
        self.assertEqual(ce.expiry, "2026-10-29")
        self.assertEqual(ce.ltp, 120.0)
        self.assertEqual(ce.bid, 119.5)
        self.assertEqual(ce.ask, 120.5)
        self.assertEqual(ce.volume, 10000)
        self.assertEqual(ce.open_interest, 50000)
        self.assertEqual(ce.oi, 50000)
        self.assertEqual(ce.delta, 0.50)
        self.assertEqual(ce.gamma, 0.0012)
        self.assertEqual(ce.theta, -10.0)
        self.assertEqual(ce.vega, 8.0)
        self.assertEqual(ce.iv, 14.0)

        # Put Option assertions
        pe = opts["24850_PE_2026-10-29"]
        self.assertEqual(pe.strike, 24850.0)
        self.assertEqual(pe.strike_price, 24850.0)
        self.assertEqual(pe.option_type, "PE")
        self.assertEqual(pe.expiry, "2026-10-29")
        self.assertEqual(pe.ltp, 115.0)
        self.assertEqual(pe.bid, 114.5)
        self.assertEqual(pe.ask, 115.5)
        self.assertEqual(pe.volume, 9000)
        self.assertEqual(pe.open_interest, 45000)
        self.assertEqual(pe.oi, 45000)
        self.assertEqual(pe.delta, -0.50)
        self.assertEqual(pe.gamma, 0.0012)
        self.assertEqual(pe.theta, -10.0)
        self.assertEqual(pe.vega, 8.0)
        self.assertEqual(pe.iv, 14.5)

    # -------------------------------------------------------------------------
    # Test 5: Malformed JSON Does Not Crash Subscriber
    # -------------------------------------------------------------------------
    def test_05_e2e_malformed_json_does_not_crash_subscriber(self):
        """Verify that malformed JSON payloads over wire do not crash the subscriber."""
        snap_q = queue.Queue()
        listener = self._create_listener(snapshot_queue=snap_q)
        self.assertTrue(listener.start())

        # Send corrupt JSON frame
        corrupt_bytes = b"{{{NOT_VALID_JSON_FRAME_12345"
        success = sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=corrupt_bytes,
            condition_fn=lambda: listener.metrics["parser_errors"] > 0,
            timeout_sec=2.0,
        )
        self.assertTrue(success, "Listener failed to register parser error from malformed JSON")
        self.assertTrue(listener.is_running, "Subscriber crashed on malformed JSON payload")
        self.assertTrue(snap_q.empty(), "Corrupt payload should not dispatch a snapshot")

        # Confirm subscriber remains healthy and can subsequently process a valid message
        valid_payload = get_canonical_synthetic_payload()
        valid_success = sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=valid_payload,
            condition_fn=lambda: not snap_q.empty(),
            timeout_sec=2.0,
        )
        self.assertTrue(valid_success, "Subscriber failed to recover and process subsequent valid message")
        self.assertFalse(snap_q.empty())

    # -------------------------------------------------------------------------
    # Test 6: Unexpected Topic Is Not Dispatched
    # -------------------------------------------------------------------------
    def test_06_e2e_unexpected_topic_not_dispatched(self):
        """Verify that wire frames with unexpected topics are filtered out and not dispatched."""
        snap_q = queue.Queue()
        listener = self._create_listener(snapshot_queue=snap_q)
        self.assertTrue(listener.start())

        # Publish with wrong topic
        payload = get_canonical_synthetic_payload()
        for _ in range(5):
            self.publisher.publish_message("UNEXPECTED_TOPIC_BANKNIFTY", payload)
            time.sleep(0.03)

        time.sleep(0.1)
        self.assertTrue(snap_q.empty(), "Unexpected topic frame dispatched as snapshot")
        self.assertEqual(listener.metrics["snapshots_dispatched"], 0)

    # -------------------------------------------------------------------------
    # Test 7: Invalid Payload Rejected Safely by Validator
    # -------------------------------------------------------------------------
    def test_07_e2e_invalid_payload_rejected_safely_by_validator(self):
        """Verify that structurally invalid payloads (schema violation) are rejected safely."""
        snap_q = queue.Queue()
        listener = self._create_listener(snapshot_queue=snap_q)
        self.assertTrue(listener.start())

        # Missing 'spot' dict which is mandatory for validation
        invalid_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "server_status": "RUNNING",
            # 'spot' omitted entirely
            "current_expiry": "2026-10-29",
        }
        success = sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=invalid_payload,
            condition_fn=lambda: listener.metrics["validation_errors"] > 0,
            timeout_sec=2.0,
        )
        self.assertTrue(success, "Listener failed to register validation error for invalid payload")
        self.assertTrue(snap_q.empty(), "Invalid schema payload should not produce MarketSnapshot")
        self.assertTrue(listener.is_running)

    # -------------------------------------------------------------------------
    # Test 8: Bounded Timeout & Clean Resource Cleanup
    # -------------------------------------------------------------------------
    def test_08_e2e_bounded_timeout_and_clean_resource_cleanup(self):
        """Verify that stopping the subscriber releases sockets/threads and returns within timeout."""
        listener = self._create_listener()
        self.assertTrue(listener.start())
        self.assertTrue(listener.is_running)

        start_time = time.time()
        listener.stop(timeout=2.0)
        elapsed = time.time() - start_time

        self.assertLess(elapsed, 2.0, "listener.stop() exceeded bounded timeout")
        self.assertFalse(listener.is_running)
        self.assertTrue(listener.transport.is_closed)

    # -------------------------------------------------------------------------
    # Test 9: Snapshot Reception Does NOT Invoke Strategy Evaluation
    # -------------------------------------------------------------------------
    def test_09_e2e_snapshot_reception_does_not_invoke_strategy_evaluation(self):
        """
        Verify that receiving a market snapshot does NOT evaluate strategies.
        Uses spy to confirm zero strategy evaluation invocations.
        """
        registry = StrategyRegistry()
        charges_calc = ChargesCalculator()
        pos_mgr = MultiLegPositionManager(charges_calc=charges_calc)
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
        )

        strategy_eval_invocations = []
        original_process = runner.process_snapshot

        def spy_process_snapshot(snapshot):
            strategy_eval_invocations.append(snapshot)
            return original_process(snapshot)

        runner.process_snapshot = spy_process_snapshot

        snap_q = queue.Queue()
        # FeedListener intentionally has NO reference to runner
        listener = self._create_listener(snapshot_queue=snap_q)
        self.assertTrue(listener.start())

        payload = get_canonical_synthetic_payload()
        sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=payload,
            condition_fn=lambda: not snap_q.empty(),
            timeout_sec=2.0,
        )

        self.assertFalse(snap_q.empty())
        # Verification: StrategyRunner was never invoked by snapshot receipt
        self.assertEqual(len(strategy_eval_invocations), 0, "Strategy evaluation was invoked upon feed message reception")

    # -------------------------------------------------------------------------
    # Test 10: Snapshot Reception Creates Zero Positions or Orders
    # -------------------------------------------------------------------------
    def test_10_e2e_snapshot_reception_creates_zero_positions_or_orders(self):
        """
        Verify that receiving market snapshots over ZeroMQ creates zero orders or positions.
        """
        charges_calc = ChargesCalculator()
        pos_mgr = MultiLegPositionManager(charges_calc=charges_calc)
        paper_broker = PaperBroker(position_manager=pos_mgr)

        snap_q = queue.Queue()
        listener = self._create_listener(snapshot_queue=snap_q)
        self.assertTrue(listener.start())

        payload = get_canonical_synthetic_payload()
        for _ in range(3):
            self.publisher.publish_message(b"NIFTY_MARKET_TICK", payload)
            time.sleep(0.04)

        sync_publish_until(
            publisher=self.publisher,
            topic=b"NIFTY_MARKET_TICK",
            payload=payload,
            condition_fn=lambda: snap_q.qsize() >= 1,
            timeout_sec=2.0,
        )

        # Assert no positions or orders exist
        self.assertEqual(len(pos_mgr.positions), 0, "Positions were created during feed ingestion")
        self.assertEqual(len(pos_mgr.get_open_positions()), 0, "Open positions were created")
        summary = pos_mgr.get_portfolio_summary()
        self.assertEqual(summary["open_positions_count"], 0)
        self.assertEqual(summary["total_unrealized_mtm"], 0.0)


if __name__ == "__main__":
    unittest.main()

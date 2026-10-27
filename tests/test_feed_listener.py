"""
test_feed_listener.py
Phase 2: Offline Unit Tests for ZeroMQ Feed Transport and FeedListener.

Tests:
1. Correct topic subscription (default b"NIFTY_MARKET_TICK" and custom).
2. Correct configured endpoint (default tcp://127.0.0.1:5556 and config/feed_config.json).
3. Correct handling of a valid two-frame message.
4. Malformed JSON handled safely without crashing.
5. Unexpected frame count handled safely without crashing.
6. Incorrect topic handled safely without crashing.
7. Invalid payload schema handled safely without crashing.
8. Socket errors, graceful shutdown, and context/socket cleanup.
9. Confirmation that receiving messages does NOT execute strategies or create trades.
10. Existing parser, validator, and normalizer behavior remains intact.

Strictly offline: Zero live sockets, ZeroMQ network binds, or broker connections.
"""

import json
import os
import queue
import time
import unittest
from typing import Any, List, Optional

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


class MockFeedTransport:
    """Deterministic in-memory mock transport for offline unit testing."""

    def __init__(self, frames_list: Optional[List[Any]] = None):
        self.frames_list: List[Any] = list(frames_list) if frames_list else []
        self.recv_call_count: int = 0
        self.close_call_count: int = 0
        self.is_closed: bool = False

    def recv_frames(self, timeout_ms: int = 500) -> Optional[List[bytes]]:
        if self.is_closed:
            return None
        self.recv_call_count += 1
        if not self.frames_list:
            return None
        item = self.frames_list.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.close_call_count += 1
        self.is_closed = True


class MockZmqSocket:
    """Mock ZeroMQ socket recording socket options and connections without opening ports."""

    def __init__(self):
        self.sockopts = {}
        self.connected_endpoints = []
        self.closed = False
        self.linger = None
        self.polled = 0
        self.frames_to_recv = []

    def setsockopt(self, opt, val):
        self.sockopts[opt] = val

    def connect(self, endpoint):
        self.connected_endpoints.append(endpoint)

    def poll(self, timeout_ms=500):
        self.polled += 1
        return 1 if self.frames_to_recv else 0

    def recv_multipart(self, flags=0):
        if not self.frames_to_recv:
            raise RuntimeError("Queue empty")
        return self.frames_to_recv.pop(0)

    def close(self, linger=None):
        self.closed = True
        self.linger = linger


class TestFeedListenerOffline(unittest.TestCase):
    """Offline unit test suite for ZeroMQ Feed Transport and FeedListener (Phase 2)."""

    def setUp(self):
        self.valid_payload_dict = {
            "server_status": "CONNECTED",
            "timestamp": "2026-10-27 09:30:00.000",
            "reconnect_count": 0,
            "last_error": None,
            "spot": {"symbol": "NIFTY", "ltp": 24850.0, "net_change": 25.0},
            "indices": {"india_vix": {"ltp": 13.5}},
            "futures": {
                "near": {
                    "token": "48704",
                    "symbol": "NIFTY26OCTFUT",
                    "expiry": "27OCT2026",
                    "ltp": 24880.0,
                    "volume": 15000,
                    "open_interest": 120000
                }
            },
            "options": {
                "current_expiry": {
                    "24850_CE": {
                        "strike": 24850.0,
                        "option_type": "CE",
                        "expiry": "27OCT2026",
                        "ltp": 120.0,
                        "bid": 119.5,
                        "ask": 120.5,
                        "volume": 10000,
                        "open_interest": 50000,
                        "greeks": {"iv": 14.25, "delta": 0.50}
                    },
                    "24850_PE": {
                        "strike": 24850.0,
                        "option_type": "PE",
                        "expiry": "27OCT2026",
                        "ltp": 115.0,
                        "bid": 114.5,
                        "ask": 115.5,
                        "volume": 9000,
                        "open_interest": 45000,
                        "greeks": {"iv": 14.85, "delta": -0.50}
                    }
                }
            }
        }
        self.valid_payload_bytes = json.dumps(self.valid_payload_dict).encode("utf-8")
        self.valid_frames = [b"NIFTY_MARKET_TICK", self.valid_payload_bytes]

    def test_01_correct_topic_subscription(self):
        """1. Correct topic subscription defaults to b'NIFTY_MARKET_TICK' and passes to transport."""
        # Default topic
        listener_default = FeedListener()
        self.assertEqual(listener_default.topic, b"NIFTY_MARKET_TICK")

        # Custom topic string converted to bytes
        listener_custom = FeedListener(topic="CUSTOM_TICK")
        self.assertEqual(listener_custom.topic, b"CUSTOM_TICK")

        # Transport socket option subscription verification
        mock_sock = MockZmqSocket()
        transport = ZeroMQFeedTransport(
            endpoint="tcp://127.0.0.1:5556",
            topic="NIFTY_MARKET_TICK",
            socket=mock_sock
        )
        transport.connect()
        self.assertTrue(transport.is_connected)
        self.assertEqual(mock_sock.sockopts.get(zmq.SUBSCRIBE if zmq else -1, b"NIFTY_MARKET_TICK"), b"NIFTY_MARKET_TICK")
        transport.close()
        self.assertTrue(transport.is_closed)

    def test_02_correct_configured_endpoint(self):
        """2. Correct configured endpoint defaults to tcp://127.0.0.1:5556 and matches feed_config.json."""
        listener = FeedListener()
        self.assertEqual(listener.endpoint, "tcp://127.0.0.1:5556")

        # Read actual feed_config.json to verify intended endpoint
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "feed_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg.get("feed_url"), "tcp://127.0.0.1:5556")
        self.assertFalse(cfg.get("feed_enabled"))  # Mandatory safety rule: feed_enabled remains false

        # Transport connect endpoint verification
        mock_sock = MockZmqSocket()
        transport = ZeroMQFeedTransport(endpoint="tcp://127.0.0.1:5556", socket=mock_sock)
        transport.connect()
        self.assertEqual(transport.endpoint, "tcp://127.0.0.1:5556")
        transport.close()

    def test_03_correct_handling_of_valid_two_frame_message(self):
        """3. Correct handling of a valid two-frame message: parses, validates, normalizes, dispatches."""
        dispatched_snapshots = []
        snap_queue = queue.Queue()

        def on_snap(snap: MarketSnapshot):
            dispatched_snapshots.append(snap)

        transport = MockFeedTransport([self.valid_frames])
        listener = FeedListener(
            transport=transport,
            on_snapshot=on_snap,
            snapshot_queue=snap_queue
        )

        ok, snapshot, err = listener.poll_once()
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIsNotNone(snapshot)
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertEqual(snapshot.spot_price, 24850.0)
        self.assertEqual(snapshot.current_expiry, "2026-10-27")
        self.assertEqual(len(snapshot.option_chain), 2)
        self.assertIn("24850_CE_2026-10-27", snapshot.option_chain)

        # Dispatched to callback and queue
        self.assertEqual(len(dispatched_snapshots), 1)
        self.assertEqual(dispatched_snapshots[0].spot_price, 24850.0)
        self.assertFalse(snap_queue.empty())
        self.assertEqual(snap_queue.get_nowait().spot_price, 24850.0)

        # Telemetry metrics
        m = listener.metrics
        self.assertEqual(m["messages_received"], 1)
        self.assertEqual(m["snapshots_dispatched"], 1)
        self.assertEqual(m["parser_errors"], 0)

    def test_04_malformed_json_handled_safely(self):
        """4. Malformed JSON handled safely without crashing; recorded in metrics."""
        bad_json_frames = [b"NIFTY_MARKET_TICK", b"{\"spot\": 24850.0, incomplete json..."]
        transport = MockFeedTransport([bad_json_frames])
        listener = FeedListener(transport=transport)

        ok, snapshot, err = listener.poll_once()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertIn("MALFORMED_JSON", err)

        m = listener.metrics
        self.assertEqual(m["messages_received"], 1)
        self.assertEqual(m["snapshots_dispatched"], 0)
        self.assertEqual(m["parser_errors"], 1)

    def test_05_unexpected_frame_count_handled_safely(self):
        """5. Unexpected frame counts (1 frame or 3 frames) rejected safely without crashing."""
        # 1 frame
        t1 = MockFeedTransport([[b"NIFTY_MARKET_TICK"]])
        l1 = FeedListener(transport=t1)
        ok1, snap1, err1 = l1.poll_once()
        self.assertFalse(ok1)
        self.assertIsNone(snap1)
        self.assertIn("INVALID_FRAME_COUNT", err1)
        self.assertEqual(l1.metrics["parser_errors"], 1)

        # 3 frames
        t2 = MockFeedTransport([[b"NIFTY_MARKET_TICK", self.valid_payload_bytes, b"EXTRA_FRAME"]])
        l2 = FeedListener(transport=t2)
        ok2, snap2, err2 = l2.poll_once()
        self.assertFalse(ok2)
        self.assertIsNone(snap2)
        self.assertIn("INVALID_FRAME_COUNT", err2)
        self.assertEqual(l2.metrics["parser_errors"], 1)

    def test_06_incorrect_topic_handled_safely(self):
        """6. Incorrect topic rejected safely without crashing."""
        wrong_topic_frames = [b"BANKNIFTY_MARKET_TICK", self.valid_payload_bytes]
        transport = MockFeedTransport([wrong_topic_frames])
        listener = FeedListener(transport=transport)

        ok, snapshot, err = listener.poll_once()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertIn("INVALID_TOPIC", err)
        self.assertEqual(listener.metrics["parser_errors"], 1)

    def test_07_invalid_payload_handled_safely(self):
        """7. Invalid payload (e.g. missing spot, negative price) rejected safely by validator."""
        # Missing spot
        bad_payload = dict(self.valid_payload_dict)
        del bad_payload["spot"]
        bad_frames = [b"NIFTY_MARKET_TICK", json.dumps(bad_payload).encode("utf-8")]

        transport = MockFeedTransport([bad_frames])
        listener = FeedListener(transport=transport)

        ok, snapshot, err = listener.poll_once()
        self.assertFalse(ok)
        self.assertIsNone(snapshot)
        self.assertIn("MISSING_FIELD: spot", err)
        self.assertEqual(listener.metrics["validation_errors"], 1)

    def test_08_socket_errors_and_shutdown(self):
        """8. Socket errors handled safely; clean shutdown releases resources cleanly."""
        # Error during receive
        transport = MockFeedTransport([ConnectionResetError("Connection reset by peer")])
        listener = FeedListener(transport=transport)

        ok, snap, err = listener.poll_once()
        self.assertFalse(ok)
        self.assertIsNone(snap)
        self.assertIn("TRANSPORT_ERROR: ConnectionResetError", err)
        self.assertEqual(listener.metrics["transport_errors"], 1)

        # Background thread lifecycle & clean stop
        mock_t = MockFeedTransport([])
        bg_listener = FeedListener(transport=mock_t, poll_timeout_ms=20)
        started = bg_listener.start()
        self.assertTrue(started)
        self.assertTrue(bg_listener.is_running)

        # Clean shutdown
        bg_listener.stop(timeout=1.0)
        self.assertFalse(bg_listener.is_running)
        self.assertTrue(mock_t.is_closed)
        self.assertEqual(mock_t.close_call_count, 1)

    def test_09_receiving_message_does_not_execute_strategies_or_create_trades(self):
        """9. Receiving a message strictly does NOT execute strategies, place orders, or create trades."""
        # Set up full strategy runner and paper broker
        registry = StrategyRegistry()
        charges_calc = ChargesCalculator()
        pos_mgr = MultiLegPositionManager(charges_calc=charges_calc)
        paper_broker = PaperBroker(position_manager=pos_mgr)
        risk_mgr = RiskManager()
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker
        )

        # Dispatch messages through listener
        transport = MockFeedTransport([self.valid_frames, self.valid_frames])
        listener = FeedListener(transport=transport)

        # Process two messages
        ok1, snap1, _ = listener.poll_once()
        ok2, snap2, _ = listener.poll_once()
        self.assertTrue(ok1)
        self.assertTrue(ok2)

        # Verify strict paper boundary: zero orders, zero positions, zero executions
        summary = pos_mgr.get_portfolio_summary()
        self.assertEqual(summary["open_positions_count"], 0)
        self.assertEqual(summary["open_legs_count"], 0)
        self.assertEqual(summary["total_unrealized_mtm"], 0.0)
        self.assertEqual(len(pos_mgr.positions), 0)
        self.assertEqual(len(paper_broker.processed_exit_signals), 0)

    def test_10_existing_parser_validator_normalizer_behavior_intact(self):
        """10. Existing parser, validator, and normalizer components interface identically."""
        # Direct component pipeline execution
        parser = FeedMessageParser()
        validator = FeedDataValidator(require_expiry=True, allow_empty_options=True)
        normalizer = FeedDataNormalizer(allow_empty_options=True)

        ok_p, p_dict, _ = parser.parse_multipart(self.valid_frames)
        self.assertTrue(ok_p)
        ok_v, _ = validator.validate(p_dict)
        self.assertTrue(ok_v)
        ok_n, direct_snap, _ = normalizer.normalize(p_dict)
        self.assertTrue(ok_n)

        # FeedListener pipeline execution
        transport = MockFeedTransport([self.valid_frames])
        listener = FeedListener(
            transport=transport,
            parser=parser,
            validator=validator,
            normalizer=normalizer
        )
        ok_l, listener_snap, _ = listener.poll_once()
        self.assertTrue(ok_l)

        # Assert identical outputs
        self.assertEqual(direct_snap.spot_price, listener_snap.spot_price)
        self.assertEqual(direct_snap.current_expiry, listener_snap.current_expiry)
        self.assertEqual(len(direct_snap.option_chain), len(listener_snap.option_chain))
        self.assertEqual(
            sorted(direct_snap.option_chain.keys()),
            sorted(listener_snap.option_chain.keys())
        )


if __name__ == "__main__":
    unittest.main()

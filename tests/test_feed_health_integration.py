"""
test_feed_health_integration.py
Phase 5A: Offline Unit and Integration Tests for Feed Health & Stale-Data Safety.

Verifies:
1. A valid normalized snapshot updates the health monitor exactly once.
2. An invalid payload does not refresh the last-valid-receipt time.
3. A stale snapshot is rejected at the strategy-processing boundary.
4. An unhealthy or stale feed prevents strategy evaluation.
5. A healthy feed with a valid snapshot follows the expected existing processing path,
   without enabling disabled strategies.
6. Health-monitor exceptions fail closed and do not cause strategy evaluation.
7. Existing parser, validator, normalizer, listener, and health behavior remains compatible.

Strictly offline: Zero broker connections, real Feed Server sockets, or order execution.
"""

import copy
import json
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from src.accounting.charges_calculator import ChargesCalculator
from src.execution.paper_broker import PaperBroker
from src.execution.position_manager import MultiLegPositionManager
from src.strategies.signal_models import StopLossPolicy
from src.feed.data_normalizer import FeedDataNormalizer
from src.feed.data_validator import FeedDataValidator
from src.feed.feed_health import FeedHealthMonitor, FeedHealthStatus
from src.feed.feed_listener import FeedListener
from src.feed.message_parser import FeedMessageParser
from src.market.market_snapshot import FuturesQuote, MarketSnapshot, OptionQuote
from src.portfolio.risk_manager import RiskManager
from src.strategies.strategy_registry import StrategyRegistry
from src.strategies.strategy_runner import StrategyRunner
import main


class MockTransport:
    """Deterministic in-memory transport for feed health testing."""

    def __init__(self, frames_list: Optional[List[Any]] = None):
        self.frames_list: List[Any] = list(frames_list) if frames_list else []
        self.recv_call_count: int = 0
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
        self.is_closed = True


def make_valid_payload_dict() -> Dict[str, Any]:
    """Generates standard valid synthetic market tick dictionary."""
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
        "indices": {"india_vix": {"ltp": 13.5}},
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
                    "greeks": {"delta": 0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0, "iv": 14.0},
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
                    "greeks": {"delta": -0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0, "iv": 14.5},
                },
            }
        },
    }


def make_valid_snapshot(is_stale: bool = False, data_status: str = "VALID") -> MarketSnapshot:
    """Creates a strongly-typed valid MarketSnapshot for StrategyRunner tests."""
    normalizer = FeedDataNormalizer(allow_empty_options=True)
    _, snapshot, _ = normalizer.normalize(make_valid_payload_dict())
    assert snapshot is not None
    snapshot.is_stale = is_stale
    snapshot.data_status = data_status
    return snapshot


class TestFeedHealthIntegration(unittest.TestCase):
    """Offline unit test suite for Feed Health Integration and Stale-Data Safety."""

    DEFAULT_TOPIC: bytes = b"NIFTY_MARKET_TICK"

    def setUp(self):
        """Initializes deterministic clock and mock components."""
        self.mock_time: float = 1000.0
        self.time_provider = lambda: self.mock_time
        self.health_monitor = FeedHealthMonitor(
            stale_threshold_seconds=5.0,
            time_provider=self.time_provider
        )

    # -------------------------------------------------------------------------
    # Test 1: Valid Snapshot Updates Health Monitor Exactly Once
    # -------------------------------------------------------------------------
    def test_01_valid_normalized_snapshot_updates_health_monitor_once(self):
        """Verifies that a successfully normalized snapshot updates health monitor exactly once."""
        payload = make_valid_payload_dict()
        frames = [self.DEFAULT_TOPIC, json.dumps(payload).encode("utf-8")]
        transport = MockTransport([frames])

        listener = FeedListener(
            transport=transport,
            health_monitor=self.health_monitor,
        )

        self.assertEqual(listener.health_monitor.status, FeedHealthStatus.NOT_INITIALIZED)
        self.assertEqual(listener.health_monitor.valid_ticks_count, 0)
        self.assertIsNone(listener.health_monitor.last_valid_receipt_monotonic)

        ok, snapshot, err = listener.poll_once()
        self.assertTrue(ok)
        self.assertIsNotNone(snapshot)
        self.assertIsNone(err)

        # Health monitor assertions
        self.assertEqual(listener.health_monitor.status, FeedHealthStatus.HEALTHY)
        self.assertEqual(listener.health_monitor.valid_ticks_count, 1)
        self.assertEqual(listener.health_monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(listener.health_monitor.last_valid_exchange_timestamp, "2026-10-25 09:20:00.000")
        self.assertTrue(listener.health_monitor.is_healthy)
        self.assertFalse(listener.health_monitor.is_stale)

        # Metrics reflects updated health
        metrics = listener.metrics
        self.assertEqual(metrics["health_status"], "HEALTHY")
        self.assertTrue(metrics["is_healthy"])
        self.assertFalse(metrics["is_stale"])

    # -------------------------------------------------------------------------
    # Test 2: Invalid Payload Does Not Refresh Last-Valid-Receipt Time
    # -------------------------------------------------------------------------
    def test_02_invalid_payload_does_not_refresh_last_valid_receipt_time(self):
        """Verifies that malformed JSON or schema errors record invalid tick without refreshing timestamp."""
        payload = make_valid_payload_dict()
        frames_valid = [self.DEFAULT_TOPIC, json.dumps(payload).encode("utf-8")]
        frames_corrupt_json = [self.DEFAULT_TOPIC, b"{invalid_corrupt_json:"]
        frames_invalid_schema = [self.DEFAULT_TOPIC, json.dumps({"timestamp": "2026-10-25"}).encode("utf-8")]

        transport = MockTransport([frames_valid, frames_corrupt_json, frames_invalid_schema])
        listener = FeedListener(
            transport=transport,
            health_monitor=self.health_monitor,
        )

        # Step 1: Ingest valid tick at t = 1000.0
        listener.poll_once()
        self.assertEqual(self.health_monitor.status, FeedHealthStatus.HEALTHY)
        initial_receipt_time = self.health_monitor.last_valid_receipt_monotonic
        self.assertEqual(initial_receipt_time, 1000.0)

        # Step 2: Advance clock to t = 1002.0 and ingest corrupt JSON
        self.mock_time = 1002.0
        ok_corrupt, snap_corrupt, err_corrupt = listener.poll_once()
        self.assertFalse(ok_corrupt)
        self.assertIsNone(snap_corrupt)
        self.assertIn("MALFORMED_JSON", err_corrupt)
        self.assertIn("PARSER_ERROR", self.health_monitor.last_error)

        # Assert: status changed to INVALID_DATA, but last_valid_receipt_monotonic did NOT refresh to 1002.0
        self.assertEqual(self.health_monitor.status, FeedHealthStatus.INVALID_DATA)
        self.assertEqual(self.health_monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.health_monitor.invalid_ticks_count, 1)

        # Step 3: Advance clock to t = 1004.0 and ingest schema violation (missing spot)
        self.mock_time = 1004.0
        ok_schema, snap_schema, err_schema = listener.poll_once()
        self.assertFalse(ok_schema)
        self.assertIsNone(snap_schema)

        # Assert: receipt time still remains 1000.0
        self.assertEqual(self.health_monitor.last_valid_receipt_monotonic, 1000.0)
        self.assertEqual(self.health_monitor.invalid_ticks_count, 2)

    # -------------------------------------------------------------------------
    # Test 3: Stale Snapshot Rejected at Strategy-Processing Boundary
    # -------------------------------------------------------------------------
    def test_03_explicitly_stale_snapshot_rejected_at_strategy_boundary(self):
        """Verifies that an explicitly stale snapshot skips strategy evaluation."""
        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=None,
        )

        strategy_invocations = []
        for strat in registry.get_all_strategies():
            orig_on_tick = strat.on_tick

            def make_spy(s):
                return lambda snap, pos: strategy_invocations.append(s.strategy_id) or (None, None)

            strat.on_tick = make_spy(strat)

        # Case A: snapshot.is_stale == True
        stale_snap = make_valid_snapshot(is_stale=True)
        ok, reason = runner.process_snapshot(stale_snap)

        self.assertFalse(ok)
        self.assertIsNotNone(reason)
        self.assertIn("SNAPSHOT_STALE", reason)
        self.assertEqual(runner.last_rejection_reason, reason)
        self.assertEqual(len(strategy_invocations), 0, "Strategies were evaluated on stale snapshot")

        # Case B: snapshot.data_status == "STALE" (with is_stale=False)
        stale_status_snap = make_valid_snapshot(is_stale=False, data_status="STALE")
        ok_status, reason_status = runner.process_snapshot(stale_status_snap)

        self.assertFalse(ok_status)
        self.assertIn("SNAPSHOT_STATUS_STALE", reason_status)
        self.assertEqual(len(strategy_invocations), 0)

    # -------------------------------------------------------------------------
    # Test 4: Unhealthy or Stale Feed Prevents Strategy Evaluation
    # -------------------------------------------------------------------------
    def test_04_unhealthy_or_stale_feed_monitor_prevents_strategy_evaluation(self):
        """
        Verifies that even if snapshot.is_stale is False, a stale or unhealthy
        FeedHealthMonitor blocks strategy evaluation.
        """
        # Monitor initialized at t = 1000.0, threshold = 5.0
        self.health_monitor.record_valid_tick(
            timestamp="2026-10-25 09:20:00.000",
            option_chain_len=10,
            has_futures=True,
        )
        self.assertTrue(self.health_monitor.is_healthy)

        # Advance clock by 10 seconds (exceeding 5.0s threshold)
        self.mock_time = 1010.0
        self.assertTrue(self.health_monitor.is_stale)

        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=self.health_monitor,
        )

        strategy_invocations = []
        for strat in registry.get_all_strategies():
            strat.on_tick = lambda snap, pos: strategy_invocations.append(1) or (None, None)

        # Snapshot itself has is_stale=False
        snapshot = make_valid_snapshot(is_stale=False, data_status="VALID")
        ok, reason = runner.process_snapshot(snapshot)

        self.assertFalse(ok)
        self.assertIsNotNone(reason)
        self.assertIn("STALE", reason)
        self.assertIn("FEED_NOT_READY", reason)
        self.assertEqual(runner.last_rejection_reason, reason)
        self.assertEqual(len(strategy_invocations), 0, "Strategies were evaluated when feed monitor was stale")

    # -------------------------------------------------------------------------
    # Test 5: Healthy Feed With Valid Snapshot Follows Expected Path
    # -------------------------------------------------------------------------
    def test_05_healthy_feed_with_valid_snapshot_follows_expected_path(self):
        """
        Verifies that a healthy feed with a fresh valid snapshot evaluates normally,
        while strictly keeping all strategies in their configured DISABLED state.
        """
        self.health_monitor.record_valid_tick(
            timestamp="2026-10-25 09:20:00.000",
            option_chain_len=10,
            has_futures=True,
        )
        self.assertTrue(self.health_monitor.is_healthy)

        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=self.health_monitor,
        )

        evaluated_strategies = []
        for strat in registry.get_all_strategies():
            orig_tick = strat.on_tick

            def make_evaluator(s, orig):
                def evaluator(snap, pos):
                    evaluated_strategies.append(s.strategy_id)
                    return orig(snap, pos)
                return evaluator

            strat.on_tick = make_evaluator(strat, orig_tick)

        snapshot = make_valid_snapshot(is_stale=False, data_status="VALID")
        ok, reason = runner.process_snapshot(snapshot)

        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertIsNone(runner.last_rejection_reason)
        # All 25 strategies were evaluated
        self.assertEqual(len(evaluated_strategies), 25)
        # Confirmed: All strategies remain strictly DISABLED
        for strat in registry.get_all_strategies():
            mode_str = strat.mode.value if hasattr(strat.mode, "value") else str(strat.mode)
            self.assertEqual(mode_str, "DISABLED")
        # Confirmed: Zero positions or orders created
        self.assertEqual(len(pos_mgr.positions), 0)

    # -------------------------------------------------------------------------
    # Test 6: Health-Monitor Exceptions Fail Closed
    # -------------------------------------------------------------------------
    def test_06_health_monitor_exception_fails_closed_and_blocks_evaluation(self):
        """Verifies that if health_monitor raises an exception, StrategyRunner fails closed."""
        faulty_monitor = MagicMock()
        faulty_monitor.is_stale = False
        faulty_monitor.is_strategy_ready.side_effect = RuntimeError("Simulated internal monitor corruption")

        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=faulty_monitor,
        )

        strategy_invocations = []
        for strat in registry.get_all_strategies():
            strat.on_tick = lambda snap, pos: strategy_invocations.append(1) or (None, None)

        snapshot = make_valid_snapshot(is_stale=False, data_status="VALID")
        ok, reason = runner.process_snapshot(snapshot)

        self.assertFalse(ok)
        self.assertIsNotNone(reason)
        self.assertIn("HEALTH_MONITOR_ERROR: RuntimeError", reason)
        self.assertEqual(len(strategy_invocations), 0, "Strategies evaluated despite health monitor exception")

    # -------------------------------------------------------------------------
    # Test 7: Poll Timeout Does NOT Mark Transport Disconnected
    # -------------------------------------------------------------------------
    def test_07_poll_timeout_does_not_mark_disconnected(self):
        """
        Verifies that an ordinary poll timeout does not record a transport disconnection,
        while an actual transport exception does record DISCONNECTED.
        """
        # Empty transport simulating poll timeout
        timeout_transport = MockTransport([])
        listener = FeedListener(
            transport=timeout_transport,
            health_monitor=self.health_monitor,
        )

        # Baseline: record a valid tick so monitor is HEALTHY
        self.health_monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000")
        self.assertEqual(self.health_monitor.status, FeedHealthStatus.HEALTHY)

        # Poll once with timeout
        ok_timeout, snap_timeout, err_timeout = listener.poll_once()
        self.assertFalse(ok_timeout)
        self.assertIsNone(snap_timeout)
        self.assertEqual(err_timeout, "TIMEOUT")

        # Crucial check: status is NOT DISCONNECTED! It remains HEALTHY
        self.assertNotEqual(self.health_monitor.status, FeedHealthStatus.DISCONNECTED)
        self.assertEqual(self.health_monitor.status, FeedHealthStatus.HEALTHY)

        # Now simulate actual transport socket failure
        failing_transport = MockTransport([ConnectionResetError("Socket connection reset")])
        listener_failing = FeedListener(
            transport=failing_transport,
            health_monitor=self.health_monitor,
        )

        ok_err, snap_err, err_err = listener_failing.poll_once()
        self.assertFalse(ok_err)
        self.assertIn("TRANSPORT_ERROR", err_err)

        # Assert: Transport exception explicitly records DISCONNECTED
        self.assertEqual(self.health_monitor.status, FeedHealthStatus.DISCONNECTED)

    # -------------------------------------------------------------------------
    # Test 8: Runner Without Health Monitor Rejects Default-Valid Snapshot
    # -------------------------------------------------------------------------
    def test_08_runner_without_health_monitor_rejects_default_valid_snapshot(self):
        """
        Verifies that StrategyRunner fails closed by default when self.health_monitor is None,
        even if the snapshot has is_stale=False and data_status='VALID'.
        """
        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=None,
        )

        strategy_invocations = []
        for strat in registry.get_all_strategies():
            strat.on_tick = lambda snap, pos: strategy_invocations.append(1) or (None, None)

        snapshot = make_valid_snapshot(is_stale=False, data_status="VALID")
        ok, reason = runner.process_snapshot(snapshot)

        self.assertFalse(ok)
        self.assertIsNotNone(reason)
        self.assertIn("NO_HEALTH_MONITOR", reason)
        self.assertEqual(runner.last_rejection_reason, reason)
        self.assertEqual(len(strategy_invocations), 0, "Strategies evaluated when health monitor was missing")

    # -------------------------------------------------------------------------
    # Test 9: Stale Snapshot Does NOT Update Existing Position Marks
    # -------------------------------------------------------------------------
    def test_09_stale_snapshot_does_not_update_existing_position_marks(self):
        """
        Verifies that when a stale snapshot is processed, existing position mark prices
        and unrealized P&L remain completely unaltered.
        """
        self.health_monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000", option_chain_len=10)
        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=self.health_monitor,
        )

        stale_snapshot = make_valid_snapshot(is_stale=True)
        opt = stale_snapshot.get_option(24850, "CE")
        self.assertIsNotNone(opt)

        # Create an open position with entry price 100.0 matching the contract symbol
        pos = pos_mgr.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=[{
                "leg_id": "L1",
                "symbol": opt.symbol,
                "strike": opt.strike,
                "option_type": opt.option_type,
                "side": "BUY",
                "quantity": 65,
                "entry_price": 100.0,
                "current_price": 100.0,
            }],
            timestamp="2026-10-25 09:20:00"
        )
        leg = pos.legs["L1"]
        self.assertEqual(leg.current_price, 100.0)
        self.assertEqual(pos.combined_unrealized_pnl, 0.0)

        # Change option LTP in the stale snapshot to 250.0
        opt.ltp = 250.0

        ok, reason = runner.process_snapshot(stale_snapshot)

        self.assertFalse(ok)
        self.assertIn("SNAPSHOT_STALE", reason)
        # Position mark must remain at 100.0 (NOT updated to 250.0)
        self.assertEqual(leg.current_price, 100.0)
        self.assertEqual(pos.combined_unrealized_pnl, 0.0)

    # -------------------------------------------------------------------------
    # Test 10: Unhealthy Feed Does NOT Cause Untrusted Prices to Update MTM
    # -------------------------------------------------------------------------
    def test_10_unhealthy_feed_does_not_update_portfolio_mtm(self):
        """
        Verifies that when feed health is degraded (e.g. DISCONNECTED), incoming untrusted
        prices do not alter portfolio unrealized MTM.
        """
        self.health_monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000", option_chain_len=10)
        # Now mark disconnected
        self.health_monitor.record_disconnect("TRANSPORT_LOST")
        self.assertEqual(self.health_monitor.status, FeedHealthStatus.DISCONNECTED)

        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=self.health_monitor,
        )

        snapshot = make_valid_snapshot(is_stale=False, data_status="VALID")
        opt = snapshot.get_option(24850, "CE")
        self.assertIsNotNone(opt)

        pos_mgr.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=[{
                "leg_id": "L1",
                "symbol": opt.symbol,
                "strike": opt.strike,
                "option_type": opt.option_type,
                "side": "BUY",
                "quantity": 65,
                "entry_price": 100.0,
                "current_price": 100.0,
            }],
            timestamp="2026-10-25 09:20:00"
        )
        initial_summary = pos_mgr.get_portfolio_summary()
        self.assertEqual(initial_summary["total_unrealized_mtm"], 0.0)

        # Snapshot with untrusted price (180.0)
        opt.ltp = 180.0

        ok, reason = runner.process_snapshot(snapshot)

        self.assertFalse(ok)
        self.assertIn("FEED_NOT_READY", reason)
        # Portfolio MTM must remain unchanged at 0.0
        post_summary = pos_mgr.get_portfolio_summary()
        self.assertEqual(post_summary["total_unrealized_mtm"], 0.0)

    # -------------------------------------------------------------------------
    # Test 11: Untrusted Snapshot Does NOT Trigger Emergency Position Closure
    # -------------------------------------------------------------------------
    def test_11_untrusted_snapshot_does_not_trigger_emergency_close(self):
        """
        Verifies that an untrusted or stale snapshot containing crash prices
        does not cause emergency portfolio liquidation.
        """
        self.health_monitor.record_valid_tick(timestamp="2026-10-25 09:20:00.000", option_chain_len=10)
        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        # Set a tight emergency drawdown limit of 1,000 INR
        risk_mgr = RiskManager(config={
            "max_drawdown_limit_inr": 1000.0,
            "max_portfolio_loss_pct": 5.0,
        })
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=self.health_monitor,
        )

        crash_snapshot = make_valid_snapshot(is_stale=True)
        opt = crash_snapshot.get_option(24850, "CE")
        self.assertIsNotNone(opt)

        # Open position: 65 units at 120.0
        pos = pos_mgr.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=[{
                "leg_id": "L1",
                "symbol": opt.symbol,
                "strike": opt.strike,
                "option_type": opt.option_type,
                "side": "BUY",
                "quantity": 65,
                "entry_price": 120.0,
                "current_price": 120.0,
            }],
            timestamp="2026-10-25 09:20:00"
        )
        self.assertEqual(pos.status, "OPEN")

        # Create an explicitly stale snapshot with a flash crash to 1.0
        # If marks were updated, this would imply an artificial loss of (120 - 1) * 65 = 7735 INR,
        # which would breach the 1,000 INR emergency drawdown limit.
        opt.ltp = 1.0

        ok, reason = runner.process_snapshot(crash_snapshot)

        self.assertFalse(ok)
        self.assertIn("SNAPSHOT_STALE", reason)
        # Position MUST remain OPEN, never closed due to an untrusted tick
        self.assertEqual(pos.status, "OPEN")
        self.assertEqual(len(pos_mgr.get_open_positions()), 1)
        self.assertEqual(len(paper_broker.processed_exit_signals), 0)

    # -------------------------------------------------------------------------
    # Test 12: Monitor Exception Prevents Valuation Change and Evaluation
    # -------------------------------------------------------------------------
    def test_12_monitor_exception_prevents_valuation_change_and_evaluation(self):
        """
        Verifies that if health_monitor raises an exception, the freshness guard
        fails closed BEFORE mark-to-market updates, preserving existing valuation.
        """
        faulty_monitor = MagicMock()
        faulty_monitor.is_stale = False
        faulty_monitor.is_strategy_ready.side_effect = RuntimeError("Simulated monitor crash")

        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=faulty_monitor,
        )

        snapshot = make_valid_snapshot(is_stale=False, data_status="VALID")
        opt = snapshot.get_option(24850, "CE")
        self.assertIsNotNone(opt)

        pos = pos_mgr.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=[{
                "leg_id": "L1",
                "symbol": opt.symbol,
                "strike": opt.strike,
                "option_type": opt.option_type,
                "side": "BUY",
                "quantity": 65,
                "entry_price": 100.0,
                "current_price": 100.0,
            }],
            timestamp="2026-10-25 09:20:00"
        )

        opt.ltp = 300.0

        strategy_invocations = []
        for strat in registry.get_all_strategies():
            strat.on_tick = lambda snap, p: strategy_invocations.append(1) or (None, None)

        ok, reason = runner.process_snapshot(snapshot)

        self.assertFalse(ok)
        self.assertIn("HEALTH_MONITOR_ERROR: RuntimeError", reason)
        self.assertEqual(len(strategy_invocations), 0)
        # Position mark must remain at 100.0, MTM at 0.0
        self.assertEqual(pos.legs["L1"].current_price, 100.0)
        self.assertEqual(pos_mgr.get_portfolio_summary()["total_unrealized_mtm"], 0.0)

    # -------------------------------------------------------------------------
    # Test 13: Shared Monitor Propagates Transitions Between Listener and Runner
    # -------------------------------------------------------------------------
    def test_13_shared_monitor_propagates_transitions_between_listener_and_runner(self):
        """
        Verifies that when FeedListener and StrategyRunner share a FeedHealthMonitor instance,
        listener transitions (e.g. transport failure) are immediately visible to the runner.
        """
        shared_monitor = FeedHealthMonitor(
            stale_threshold_seconds=5.0,
            time_provider=self.time_provider,
        )

        payload = make_valid_payload_dict()
        frames_valid = [self.DEFAULT_TOPIC, json.dumps(payload).encode("utf-8")]
        transport = MockTransport([frames_valid])

        listener = FeedListener(
            transport=transport,
            health_monitor=shared_monitor,
        )
        registry = StrategyRegistry()
        pos_mgr = MultiLegPositionManager(charges_calc=ChargesCalculator())
        risk_mgr = RiskManager()
        paper_broker = PaperBroker(position_manager=pos_mgr)
        runner = StrategyRunner(
            registry=registry,
            position_manager=pos_mgr,
            risk_manager=risk_mgr,
            paper_broker=paper_broker,
            health_monitor=shared_monitor,
        )

        # Step 1: Listener ingests valid frame
        ok_l, snapshot, err_l = listener.poll_once()
        self.assertTrue(ok_l)
        self.assertTrue(shared_monitor.is_healthy)

        # Runner evaluates snapshot -> succeeds
        ok_r, reason_r = runner.process_snapshot(snapshot)
        self.assertTrue(ok_r)
        self.assertIsNone(reason_r)

        # Step 2: Transport error occurs on listener
        failing_transport = MockTransport([ConnectionResetError("Socket broken")])
        listener._transport = failing_transport
        ok_fail, _, _ = listener.poll_once()
        self.assertFalse(ok_fail)
        self.assertEqual(shared_monitor.status, FeedHealthStatus.DISCONNECTED)

        # Runner attempts to process subsequent snapshot -> rejected immediately
        subsequent_snapshot = make_valid_snapshot(is_stale=False, data_status="VALID")
        ok_sub, reason_sub = runner.process_snapshot(subsequent_snapshot)
        self.assertFalse(ok_sub)
        self.assertIn("FEED_NOT_READY", reason_sub)

    # -------------------------------------------------------------------------
    # Test 14: main.initialize_subsystems Injects Shared Health Monitor
    # -------------------------------------------------------------------------
    def test_14_main_subsystems_injects_shared_health_monitor(self):
        """
        Verifies that main.initialize_subsystems() creates a shared FeedHealthMonitor
        and injects it into StrategyRunner.
        """
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        self.assertIn("health_monitor", subsystems)
        health_monitor = subsystems["health_monitor"]
        self.assertIsInstance(health_monitor, FeedHealthMonitor)
        self.assertIs(subsystems["runner"].health_monitor, health_monitor)


if __name__ == "__main__":
    unittest.main()


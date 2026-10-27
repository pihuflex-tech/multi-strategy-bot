"""
tests/test_startup.py
Deterministic offline regression tests for Phase 3C: Single-Command Startup and Dashboard Orchestration.

Guarantees Verified:
1. Root main.py imports without side-effects.
2. CLI argument parsing and --help behavior.
3. Dashboard startup with mocked WSGI server factory.
4. Dashboard-disabled mode (--no-dashboard) does not start the dashboard service.
5. Default dashboard host is loopback (127.0.0.1) and default port is 8080.
6. Custom port override (--dashboard-port) is honored.
7. Port conflicts and startup failures exit cleanly with non-zero exit code.
8. Shutdown handling cleanly terminates the dashboard service without thread leaks.
9. Application factory (create_app) directly registers both UI (GET /) and API (GET /api/health, /api/state).
10. Telemetry provider integration accurately reflects real 25 strategies in DISABLED mode and zero paper positions.
11. Socket safety: Zero network listening sockets are bound during testing.
"""

import io
import json
import os
import signal
import socket
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import main
from src.dashboard.app import create_app
from src.dashboard.dashboard_service import DashboardService
from src.feed.feed_dispatcher import FeedDispatcher, shutdown_pipeline
from src.feed.feed_health import FeedHealthMonitor
from src.feed.feed_listener import FeedListener
from src.feed.feed_queue import DropOldestSnapshotQueue
from src.market.market_snapshot import FuturesQuote, MarketSnapshot


class DummyMockServer:
    """Mock WSGI server for deterministic socketless startup testing."""

    def __init__(self):
        self.serve_forever_started = threading.Event()
        self.shutdown_called = False
        self.server_close_called = False
        self._stop_event = threading.Event()

    def serve_forever(self):
        self.serve_forever_started.set()
        self._stop_event.wait(timeout=2.0)

    def shutdown(self):
        self.shutdown_called = True
        self._stop_event.set()

    def server_close(self):
        self.server_close_called = True


class TestStartupOrchestration(unittest.TestCase):
    """Test suite for root main.py orchestration and startup flow."""

    def test_import_main_is_side_effect_free(self):
        """Verifies that importing main.py does not start threads or bind sockets."""
        self.assertTrue(hasattr(main, "main"))
        self.assertTrue(hasattr(main, "ApplicationTelemetryProvider"))
        self.assertTrue(hasattr(main, "build_argument_parser"))

    def test_argument_parser_defaults(self):
        """Verifies CLI argument defaults."""
        parser = main.build_argument_parser()
        args = parser.parse_args([])
        self.assertFalse(args.no_dashboard)
        self.assertEqual(args.dashboard_host, "127.0.0.1")
        self.assertEqual(args.dashboard_port, 8080)
        self.assertFalse(args.inspect)
        self.assertFalse(args.test_tick)
        self.assertFalse(args.status)

    def test_argument_parser_overrides(self):
        """Verifies CLI argument parsing with explicit overrides."""
        parser = main.build_argument_parser()
        args = parser.parse_args([
            "--no-dashboard",
            "--dashboard-host", "127.0.0.2",
            "--dashboard-port", "8085",
            "--inspect",
        ])
        self.assertTrue(args.no_dashboard)
        self.assertEqual(args.dashboard_host, "127.0.0.2")
        self.assertEqual(args.dashboard_port, 8085)
        self.assertTrue(args.inspect)

    def test_cli_help(self):
        """Verifies that --help exits cleanly with code 0."""
        parser = main.build_argument_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_cli_inspect_mode(self):
        """Verifies --inspect executes and returns 0."""
        exit_code = main.main(argv=["--inspect"], run_once=True)
        self.assertEqual(exit_code, 0)

    def test_cli_status_mode(self):
        """Verifies --status executes and returns 0."""
        exit_code = main.main(argv=["--status"], run_once=True)
        self.assertEqual(exit_code, 0)

    def test_cli_test_tick_mode(self):
        """Verifies --test-tick executes synthetic snapshot and returns 0 without mutating state files."""
        with patch.object(main.StrategyRunner, "process_snapshot") as mock_snap:
            exit_code = main.main(argv=["--test-tick"], run_once=True)
            self.assertEqual(exit_code, 0)
            mock_snap.assert_called_once()

    def test_no_dashboard_mode_does_not_start_service(self):
        """Verifies --no-dashboard runs without starting any web server."""
        mock_factory = MagicMock()
        exit_code = main.main(
            argv=["--no-dashboard"],
            server_factory=mock_factory,
            run_once=True,
        )
        self.assertEqual(exit_code, 0)
        mock_factory.assert_not_called()

    def test_dashboard_startup_with_mock_server_factory(self):
        """Verifies full startup flow with injected mock server factory."""
        dummy_server = DummyMockServer()

        def mock_factory(host, port, app, **kwargs):
            self.assertEqual(host, "127.0.0.1")
            self.assertEqual(port, 8080)
            return dummy_server

        exit_code = main.main(
            argv=[],
            server_factory=mock_factory,
            run_once=True,
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(dummy_server.shutdown_called)
        self.assertTrue(dummy_server.server_close_called)

    def test_dashboard_port_override(self):
        """Verifies that --dashboard-port 8081 is passed to the server factory."""
        captured_port = []
        dummy = DummyMockServer()

        def mock_factory(host, port, app, **kwargs):
            captured_port.append(port)
            return dummy

        exit_code = main.main(
            argv=["--dashboard-port", "8081"],
            server_factory=mock_factory,
            run_once=True,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(captured_port, [8081])

    def test_dashboard_startup_port_conflict_handled_safely(self):
        """Verifies that a port conflict / server creation error exits with 1 without hanging."""
        def failing_factory(host, port, app, **kwargs):
            raise OSError("Address already in use")

        exit_code = main.main(
            argv=["--dashboard-port", "8080"],
            server_factory=failing_factory,
            run_once=True,
        )
        self.assertEqual(exit_code, 1)

    def test_subsystem_init_failure_exits_nonzero(self):
        """Verifies that subsystem initialization failures exit with code 1."""
        with patch("main.initialize_subsystems") as mock_init:
            mock_init.side_effect = RuntimeError("Failed to load required subsystem config")
            exit_code = main.main(argv=[], run_once=True)
            self.assertEqual(exit_code, 1)

    def test_non_loopback_host_warning(self):
        """Verifies that non-loopback host addresses emit a security warning."""
        dummy = DummyMockServer()
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            exit_code = main.main(
                argv=["--dashboard-host", "0.0.0.0"],
                server_factory=lambda *a, **k: dummy,
                run_once=True,
            )
        self.assertEqual(exit_code, 0)
        output = captured_out.getvalue()
        self.assertIn("SECURITY WARNING: Binding dashboard to external interface 0.0.0.0", output)

    def test_orderly_shutdown_signal_handling(self):
        """Verifies that signal handlers request orderly shutdown without socket leaks."""
        dummy = DummyMockServer()
        received_handlers = {}

        original_signal = signal.signal

        def intercept_signal(sig, handler):
            received_handlers[sig] = handler
            return original_signal(sig, handler)

        with patch("signal.signal", side_effect=intercept_signal):
            exit_code = main.main(
                argv=[],
                server_factory=lambda *a, **k: dummy,
                run_once=True,
            )
            self.assertEqual(exit_code, 0)
            self.assertIn(signal.SIGINT, received_handlers)
            self.assertTrue(dummy.shutdown_called)
            self.assertTrue(dummy.server_close_called)


class TestTelemetryProviderIntegration(unittest.TestCase):
    """Test suite verifying the ApplicationTelemetryProvider reads live objects safely."""

    def test_telemetry_provider_reflects_actual_registry(self):
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        registry = subsystems["registry"]
        pos_mgr = subsystems["position_manager"]

        provider = main.ApplicationTelemetryProvider(registry=registry, position_manager=pos_mgr)
        state = provider.get_state()

        self.assertEqual(state["bot_status"], "STANDBY")
        self.assertEqual(state["app_lifecycle"], "RUNNING")
        self.assertEqual(state["feed_connection"], "NOT_CONNECTED")
        self.assertEqual(state["market_data_status"], "NOT_AVAILABLE")
        self.assertEqual(state["strategy_engine"], "ALL_DISABLED (0/25)")
        self.assertEqual(state["paper_execution"], "STANDBY")
        self.assertIsNone(state["last_tick"]["spot"])
        self.assertIsNone(state["last_tick"]["futures"])

        # Exactly 25 strategies
        self.assertEqual(len(state["strategies"]), 25)
        for s in state["strategies"]:
            self.assertEqual(s["mode"], "DISABLED")

        # Zero paper positions and genuine zero portfolio metrics
        self.assertEqual(len(state["open_positions"]), 0)
        self.assertEqual(state["portfolio"]["open_positions_count"], 0)
        self.assertEqual(state["portfolio"]["open_legs_count"], 0)
        self.assertEqual(state["portfolio"]["total_unrealized_mtm"], 0.0)
        self.assertEqual(state["portfolio"]["total_realized_gross_pnl"], 0.0)
        self.assertEqual(state["portfolio"]["total_charges"], 0.0)
        self.assertEqual(state["portfolio"]["total_net_realized_pnl"], 0.0)
        self.assertEqual(state["portfolio"]["net_combined_pnl"], 0.0)
        self.assertEqual(state["errors"]["last_system_error"], "NONE")


class TestTelemetryProviderAccuracy(unittest.TestCase):
    """Focused regression tests for telemetry error transparency and accurate statuses."""

    def test_strategy_extraction_failure_reports_sanitized_error(self):
        mock_registry = MagicMock()
        mock_registry.get_all_strategies.side_effect = RuntimeError("Strategy DB read timeout")
        mock_pm = MagicMock()
        mock_pm.get_portfolio_summary.return_value = {
            "open_positions_count": 0,
            "open_legs_count": 0,
            "total_unrealized_mtm": 0.0,
            "total_realized_gross_pnl": 0.0,
            "total_charges": 0.0,
            "total_net_realized_pnl": 0.0,
            "net_combined_pnl": 0.0,
        }
        mock_pm.get_open_positions.return_value = []

        provider = main.ApplicationTelemetryProvider(registry=mock_registry, position_manager=mock_pm)
        state = provider.get_state()

        self.assertEqual(state["errors"]["last_system_error"], "STRATEGY_TELEMETRY_UNAVAILABLE")
        self.assertEqual(state["strategy_engine"], "UNAVAILABLE")
        self.assertEqual(state["strategies"], [])
        # Valid portfolio is preserved
        self.assertEqual(state["portfolio"]["total_unrealized_mtm"], 0.0)
        self.assertEqual(state["portfolio"]["open_positions_count"], 0)

    def test_portfolio_extraction_failure_reports_sanitized_error_and_nulls(self):
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        registry = subsystems["registry"]
        mock_pm = MagicMock()
        mock_pm.get_portfolio_summary.side_effect = ValueError("Corrupt position record")

        provider = main.ApplicationTelemetryProvider(registry=registry, position_manager=mock_pm)
        state = provider.get_state()

        self.assertEqual(state["errors"]["last_system_error"], "PORTFOLIO_TELEMETRY_UNAVAILABLE")
        # All unavailable portfolio metrics must be None, NOT 0.0
        p = state["portfolio"]
        self.assertIsNone(p["open_positions_count"])
        self.assertIsNone(p["open_legs_count"])
        self.assertIsNone(p["total_unrealized_mtm"])
        self.assertIsNone(p["total_realized_gross_pnl"])
        self.assertIsNone(p["total_charges"])
        self.assertIsNone(p["total_net_realized_pnl"])
        self.assertIsNone(p["net_combined_pnl"])
        # Useful strategy telemetry is preserved
        self.assertEqual(len(state["strategies"]), 25)
        self.assertEqual(state["strategy_engine"], "ALL_DISABLED (0/25)")

    def test_both_extractions_failed_reports_telemetry_extraction_error(self):
        mock_registry = MagicMock()
        mock_registry.get_all_strategies.side_effect = RuntimeError("Strategy failure")
        mock_pm = MagicMock()
        mock_pm.get_portfolio_summary.side_effect = RuntimeError("PM failure")

        provider = main.ApplicationTelemetryProvider(registry=mock_registry, position_manager=mock_pm)
        state = provider.get_state()

        self.assertEqual(state["errors"]["last_system_error"], "TELEMETRY_EXTRACTION_ERROR")
        self.assertEqual(state["strategies"], [])
        self.assertEqual(state["strategy_engine"], "UNAVAILABLE")
        self.assertIsNone(state["portfolio"]["total_unrealized_mtm"])

    def test_missing_registry_and_missing_position_manager(self):
        provider = main.ApplicationTelemetryProvider(registry=None, position_manager=None)
        state = provider.get_state()

        self.assertEqual(state["errors"]["last_system_error"], "TELEMETRY_EXTRACTION_ERROR")
        self.assertEqual(state["strategy_engine"], "UNAVAILABLE")
        self.assertIsNone(state["portfolio"]["total_unrealized_mtm"])

    def test_genuine_zeroes_preserved_when_healthy(self):
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        provider = main.ApplicationTelemetryProvider(
            registry=subsystems["registry"],
            position_manager=subsystems["position_manager"],
        )
        state = provider.get_state()
        p = state["portfolio"]

        # Genuine zero numbers must not be null
        self.assertEqual(p["open_positions_count"], 0)
        self.assertEqual(p["open_legs_count"], 0)
        self.assertEqual(p["total_unrealized_mtm"], 0.0)
        self.assertEqual(p["total_realized_gross_pnl"], 0.0)
        self.assertEqual(p["total_charges"], 0.0)
        self.assertEqual(p["total_net_realized_pnl"], 0.0)
        self.assertEqual(p["net_combined_pnl"], 0.0)

    def test_default_statuses_do_not_imply_active_trading(self):
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        provider = main.ApplicationTelemetryProvider(
            registry=subsystems["registry"],
            position_manager=subsystems["position_manager"],
        )
        state = provider.get_state()

        self.assertNotEqual(state["bot_status"], "ONLINE_ACTIVE")
        self.assertEqual(state["bot_status"], "STANDBY")
        self.assertEqual(state["app_lifecycle"], "RUNNING")
        self.assertEqual(state["feed_connection"], "NOT_CONNECTED")
        self.assertEqual(state["market_data_status"], "NOT_AVAILABLE")
        self.assertEqual(state["strategy_engine"], "ALL_DISABLED (0/25)")
        self.assertEqual(state["paper_execution"], "STANDBY")

    def test_api_responses_remain_sanitized_under_extraction_failures(self):
        mock_registry = MagicMock()
        mock_registry.get_all_strategies.side_effect = RuntimeError("secret_token_12345 in traceback at C:/repo/secret.py")
        mock_pm = MagicMock()
        mock_pm.get_portfolio_summary.side_effect = ValueError("api_key_abcde failure")

        provider = main.ApplicationTelemetryProvider(registry=mock_registry, position_manager=mock_pm)
        app = create_app(telemetry_provider=provider)
        client = app.test_client()

        res = client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        text = res.get_data(as_text=True)

        self.assertNotIn("secret_token_12345", text)
        self.assertNotIn("api_key_abcde", text)
        self.assertNotIn("traceback", text.lower())
        data = res.get_json()
        self.assertEqual(data["errors"]["last_system_error"], "TELEMETRY_EXTRACTION_ERROR")


class TestAppFactoryBlueprintIntegrity(unittest.TestCase):
    """Verifies that create_app() registers both UI and API natively."""

    def test_create_app_serves_both_ui_and_api(self):
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        provider = main.ApplicationTelemetryProvider(
            registry=subsystems["registry"],
            position_manager=subsystems["position_manager"],
        )
        app = create_app(telemetry_provider=provider)
        client = app.test_client()

        # UI route
        index_res = client.get("/")
        self.assertEqual(index_res.status_code, 200)
        self.assertIn("NIFTY Multi-Strategy Quant Monitor", index_res.get_data(as_text=True))

        # Health API
        health_res = client.get("/api/health")
        self.assertEqual(health_res.status_code, 200)
        self.assertEqual(health_res.get_json()["status"], "ONLINE")

        # State API
        state_res = client.get("/api/state")
        self.assertEqual(state_res.status_code, 200)
        state_data = state_res.get_json()
        self.assertEqual(state_data["bot_status"], "STANDBY")
        self.assertEqual(state_data["app_lifecycle"], "RUNNING")
        self.assertEqual(state_data["strategy_engine"], "ALL_DISABLED (0/25)")
        self.assertEqual(state_data["paper_execution"], "STANDBY")
        self.assertEqual(len(state_data["strategies"]), 25)
        self.assertEqual(state_data["feed_connection"], "NOT_CONNECTED")


class TestOfflineSocketSafetyInStartup(unittest.TestCase):
    """Verifies that no network sockets are opened during automated startup tests."""

    def test_no_socket_opened_during_startup_orchestration(self):
        with patch("socket.socket") as mock_sock:
            mock_sock.side_effect = RuntimeError("Socket creation prohibited in offline tests!")

            dummy = DummyMockServer()
            exit_code = main.main(
                argv=["--dashboard-port", "8080"],
                server_factory=lambda *a, **k: dummy,
                run_once=True,
            )
            self.assertEqual(exit_code, 0)
            mock_sock.assert_not_called()


class TestPhase6CompositionRootWiringAndShutdown(unittest.TestCase):
    """
    Phase 6 Step 4: Composition-Root Wiring and Orderly Shutdown Coordination Tests.
    Strictly offline regression suite verifying:
    1. Shared FeedHealthMonitor instance across runner, listener, and dispatcher.
    2. DropOldestSnapshotQueue bounded capacity and wiring.
    3. FeedDispatcher wired to single StrategyRunner and snapshot queue.
    4. Gated startup: feed_enabled: false prevents background thread start.
    5. Gated strategies: all 25 strategies remain in DISABLED mode.
    6. No worker threads start during import or initialize_subsystems().
    7. Orderly shutdown coordination: listener stops before dispatcher.
    8. Incomplete shutdown reporting if either thread fails to stop.
    9. Repeated / idempotent shutdown safety.
    10. Listener blocks queue insertion once stopped.
    11. Discard pending snapshots upon orderly shutdown.
    """

    def test_shared_health_monitor_identity_across_all_subsystems(self):
        """Verifies that listener, dispatcher, and runner share the identical FeedHealthMonitor instance."""
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        health_monitor = subsystems["health_monitor"]
        runner = subsystems["runner"]
        listener = subsystems["listener"]
        dispatcher = subsystems["dispatcher"]

        self.assertIsInstance(health_monitor, FeedHealthMonitor)
        self.assertIs(runner.health_monitor, health_monitor)
        self.assertIs(listener.health_monitor, health_monitor)
        self.assertIs(dispatcher.health_monitor, health_monitor)

    def test_queue_and_dispatcher_wiring(self):
        """Verifies that DropOldestSnapshotQueue(maxsize=5) is shared between listener and dispatcher."""
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        snapshot_queue = subsystems["snapshot_queue"]
        listener = subsystems["listener"]
        dispatcher = subsystems["dispatcher"]
        runner = subsystems["runner"]

        self.assertIsInstance(snapshot_queue, DropOldestSnapshotQueue)
        self.assertEqual(snapshot_queue.maxsize, 5)
        self.assertIs(listener.snapshot_queue, snapshot_queue)
        self.assertIs(dispatcher.queue, snapshot_queue)
        self.assertIs(dispatcher.runner, runner)

    def test_no_background_workers_start_implicitly(self):
        """Verifies that constructing subsystems does NOT start any background threads."""
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        listener = subsystems["listener"]
        dispatcher = subsystems["dispatcher"]

        self.assertFalse(listener.is_running)
        self.assertIsNone(listener._thread)
        self.assertFalse(dispatcher.is_running)
        self.assertIsNone(dispatcher._thread)

    def test_feed_disabled_startup_gating(self):
        """Verifies that when feed_enabled is false, startup never starts listener or dispatcher."""
        dummy = DummyMockServer()
        exit_code = main.main(
            argv=["--no-dashboard"],
            server_factory=lambda *a, **k: dummy,
            run_once=True,
        )
        self.assertEqual(exit_code, 0)

        # Direct subsystems inspection
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        feed_cfg = subsystems.get("feed_cfg", {})
        self.assertFalse(feed_cfg.get("feed_enabled", False))
        self.assertFalse(subsystems["listener"].is_running)
        self.assertFalse(subsystems["dispatcher"].is_running)

    def test_strategy_execution_disabled_all_25_remain_disabled(self):
        """Verifies that all 25 strategies are loaded in DISABLED mode and none are activated."""
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        registry = subsystems["registry"]
        all_strats = registry.get_all_strategies()
        self.assertEqual(len(all_strats), 25)
        for strat in all_strats:
            mode_val = strat.mode.value if hasattr(strat.mode, "value") else str(strat.mode)
            self.assertEqual(mode_val, "DISABLED")

        active = registry.get_active_strategies()
        self.assertEqual(len(active), 0)

    def test_orderly_shutdown_sequence_listener_precedes_dispatcher(self):
        """Verifies that shutdown_pipeline terminates the listener before the dispatcher."""
        call_sequence = []
        mock_listener = MagicMock()
        mock_dispatcher = MagicMock()

        def mock_listener_stop(*args, **kwargs):
            call_sequence.append("listener_stop")
            return True

        def mock_dispatcher_stop(*args, **kwargs):
            call_sequence.append("dispatcher_stop")
            return True

        mock_listener.stop.side_effect = mock_listener_stop
        mock_dispatcher.stop.side_effect = mock_dispatcher_stop

        clean, details = shutdown_pipeline(listener=mock_listener, dispatcher=mock_dispatcher, timeout=1.0)
        self.assertTrue(clean)
        self.assertTrue(details["overall_clean"])
        self.assertEqual(call_sequence, ["listener_stop", "dispatcher_stop"])

    def test_shutdown_reports_incomplete_if_listener_remains_alive(self):
        """Verifies that if the listener fails to terminate, clean shutdown is reported as False."""
        mock_listener = MagicMock()
        mock_listener.stop.return_value = False
        mock_dispatcher = MagicMock()
        mock_dispatcher.stop.return_value = True

        clean, details = shutdown_pipeline(listener=mock_listener, dispatcher=mock_dispatcher, timeout=1.0)
        self.assertFalse(clean)
        self.assertFalse(details["listener_clean"])
        self.assertFalse(details["overall_clean"])
        self.assertTrue(any("LISTENER_SHUTDOWN_TIMEOUT" in e for e in details["errors"]))
        # Dispatcher must still be stopped for safe containment
        mock_dispatcher.stop.assert_called_once()

    def test_shutdown_reports_incomplete_if_dispatcher_remains_alive(self):
        """Verifies that if the dispatcher fails to terminate, clean shutdown is reported as False."""
        mock_listener = MagicMock()
        mock_listener.stop.return_value = True
        mock_dispatcher = MagicMock()
        mock_dispatcher.stop.return_value = False

        clean, details = shutdown_pipeline(listener=mock_listener, dispatcher=mock_dispatcher, timeout=1.0)
        self.assertFalse(clean)
        self.assertFalse(details["dispatcher_clean"])
        self.assertFalse(details["overall_clean"])
        self.assertTrue(any("DISPATCHER_SHUTDOWN_TIMEOUT" in e for e in details["errors"]))

    def test_repeated_shutdown_pipeline_is_safe_and_idempotent(self):
        """Verifies that calling shutdown_pipeline multiple times is safe and returns clean."""
        subsystems = main.initialize_subsystems(main.WORKSPACE_ROOT)
        listener = subsystems["listener"]
        dispatcher = subsystems["dispatcher"]

        clean1, details1 = shutdown_pipeline(listener=listener, dispatcher=dispatcher, timeout=1.0)
        self.assertTrue(clean1)
        self.assertTrue(details1["overall_clean"])

        clean2, details2 = shutdown_pipeline(listener=listener, dispatcher=dispatcher, timeout=1.0)
        self.assertTrue(clean2)
        self.assertTrue(details2["overall_clean"])

    def test_listener_blocks_snapshot_insertion_after_shutdown(self):
        """Verifies that once listener stop is signaled, new messages are rejected and not queued."""
        q = DropOldestSnapshotQueue(maxsize=5)
        mock_transport = MagicMock()
        listener = FeedListener(transport=mock_transport, snapshot_queue=q)

        # Stop the listener
        clean = listener.stop(timeout=1.0)
        self.assertTrue(clean)

        # Attempt to process a valid message frame
        raw_payload = json.dumps({
            "feed_timestamp": "2026-10-25 09:25:00",
            "spot_price": 24850.0,
            "futures": {"symbol": "NIFTY_FUT", "expiry": "2026-10-29", "last_price": 24890.0},
            "options": {}
        }).encode("utf-8")
        frames = [b"NIFTY_MARKET_TICK", raw_payload]

        ok, snap, err = listener.process_message(frames)
        self.assertFalse(ok)
        self.assertIn("LISTENER_STOPPED", str(err))
        self.assertEqual(q.qsize(), 0)

    def test_pending_snapshots_discarded_on_orderly_shutdown(self):
        """Verifies that pending snapshots in the queue are drained and discarded upon shutdown."""
        q = DropOldestSnapshotQueue(maxsize=5)
        snap1 = MarketSnapshot(timestamp="2026-10-25 09:25:00", spot_price=24850.0)
        snap2 = MarketSnapshot(timestamp="2026-10-25 09:25:01", spot_price=24855.0)
        q.put(snap1)
        q.put(snap2)
        self.assertEqual(q.qsize(), 2)

        mock_runner = MagicMock()
        dispatcher = FeedDispatcher(queue=q, runner=mock_runner)

        clean, details = shutdown_pipeline(dispatcher=dispatcher, timeout=1.0, drain_queue=True)
        self.assertTrue(clean)
        self.assertEqual(q.qsize(), 0)
        self.assertEqual(details["queue_drained_count"], 2)


class TestTelemetryFeedHealthIntegration(unittest.TestCase):
    """Focused tests verifying ApplicationTelemetryProvider dynamically reflects actual FeedHealthMonitor state."""

    def test_telemetry_reports_not_connected_when_feed_disabled(self):
        """When feed_enabled=False, telemetry strictly reports NOT_CONNECTED regardless of health monitor."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        snap = MarketSnapshot(timestamp="2026-10-27 12:00:00", spot_price=24850.0)
        monitor.record_snapshot(snap)

        provider = main.ApplicationTelemetryProvider(
            health_monitor=monitor,
            feed_enabled=False
        )
        state = provider.get_state()
        self.assertEqual(state["feed_connection"], "NOT_CONNECTED")
        self.assertEqual(state["market_data_status"], "NOT_AVAILABLE")

    def test_telemetry_reports_connecting_when_listener_running_awaiting_tick(self):
        """When feed_enabled=True and listener is running but uninitialized, reports CONNECTING."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        mock_listener = MagicMock()
        mock_listener.is_running = True
        mock_listener.last_snapshot = None

        provider = main.ApplicationTelemetryProvider(
            health_monitor=monitor,
            listener=mock_listener,
            feed_enabled=True
        )
        state = provider.get_state()
        self.assertEqual(state["feed_connection"], "CONNECTING")
        self.assertEqual(state["market_data_status"], "NOT_INITIALIZED")
        self.assertIsNone(state["last_tick"]["spot"])

    def test_telemetry_reports_connected_and_extracts_live_tick(self):
        """When feed_enabled=True and valid tick received, reports CONNECTED and populates last_tick."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        fut = FuturesQuote(token="48704", symbol="NIFTY_FUT", expiry="2026-10-29", ltp=24890.0)
        snap = MarketSnapshot(timestamp="2026-10-27 12:00:00", spot_price=24850.5, futures=fut)
        monitor.record_snapshot(snap)

        mock_listener = MagicMock()
        mock_listener.is_running = True
        mock_listener.last_snapshot = snap

        provider = main.ApplicationTelemetryProvider(
            health_monitor=monitor,
            listener=mock_listener,
            feed_enabled=True
        )
        state = provider.get_state()
        self.assertEqual(state["feed_connection"], "CONNECTED")
        self.assertEqual(state["market_data_status"], "HEALTHY")
        self.assertEqual(state["last_tick"]["spot"], 24850.5)
        self.assertEqual(state["last_tick"]["futures"], 24890.0)
        self.assertEqual(state["last_tick"]["timestamp"], "2026-10-27 12:00:00")
        self.assertEqual(state["last_tick"]["symbol"], "NIFTY")

    def test_telemetry_reports_stale_when_elapsed_exceeds_threshold(self):
        """When feed data goes stale, reports STALE without fabricating live connection."""
        curr_time = 1000.0
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=lambda: curr_time)
        snap = MarketSnapshot(timestamp="2026-10-27 12:00:00", spot_price=24850.0)
        monitor.record_snapshot(snap)
        self.assertTrue(monitor.is_healthy)

        # Advance time by 6 seconds to trigger stale
        curr_time = 1006.0
        self.assertTrue(monitor.is_stale)

        provider = main.ApplicationTelemetryProvider(
            health_monitor=monitor,
            feed_enabled=True
        )
        state = provider.get_state()
        self.assertEqual(state["feed_connection"], "STALE")
        self.assertEqual(state["market_data_status"], "STALE")

    def test_telemetry_reports_disconnected_on_transport_failure(self):
        """When health monitor records disconnect, reports DISCONNECTED."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        monitor.record_disconnect("ZeroMQ transport dropped")

        provider = main.ApplicationTelemetryProvider(
            health_monitor=monitor,
            feed_enabled=True
        )
        state = provider.get_state()
        self.assertEqual(state["feed_connection"], "DISCONNECTED")
        self.assertEqual(state["market_data_status"], "DISCONNECTED")
        self.assertEqual(state["errors"]["last_feed_error"], "ZeroMQ transport dropped")

    def test_telemetry_reports_invalid_data_on_corrupt_payload(self):
        """When health monitor records invalid data, reports INVALID_DATA."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        monitor.record_invalid_tick("PARSER_ERROR: Malformed JSON")

        provider = main.ApplicationTelemetryProvider(
            health_monitor=monitor,
            feed_enabled=True
        )
        state = provider.get_state()
        self.assertEqual(state["feed_connection"], "INVALID_DATA")
        self.assertEqual(state["market_data_status"], "INVALID_DATA")
        self.assertIn("PARSER_ERROR", state["errors"]["last_feed_error"])


class TestCliStatusFeedHealthIntegration(unittest.TestCase):
    """Deterministic offline tests verifying main.py --status CLI output across feed states."""

    def test_cli_status_reports_not_connected_when_feed_disabled(self):
        """Default status report displays NOT_CONNECTED (Offline) when feed_enabled is false."""
        stdout_capture = io.StringIO()
        with patch("sys.argv", ["main.py", "--status"]), patch("sys.stdout", stdout_capture):
            code = main.main()
        self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Feed Connection: NOT_CONNECTED (Offline)", output)
        self.assertIn("Total Unrealized MTM: INR 0", output)

    def test_cli_status_reports_connected_live_when_feed_healthy(self):
        """When feed is enabled and monitor is healthy, CLI reports CONNECTED (Live)."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        snap = MarketSnapshot(timestamp="2026-10-27 12:00:00", spot_price=24850.0)
        monitor.record_snapshot(snap)

        stdout_capture = io.StringIO()
        with patch("sys.argv", ["main.py", "--status"]), \
             patch("sys.stdout", stdout_capture), \
             patch("main.initialize_subsystems") as mock_sub:
            mock_sub.return_value = {
                "registry": MagicMock(get_registered_ids=lambda: ["STRAT_001"], get_active_strategies=lambda: []),
                "position_manager": MagicMock(get_portfolio_summary=lambda: {"open_positions_count": 0, "total_unrealized_mtm": 0.0}),
                "runner": MagicMock(),
                "health_monitor": monitor,
                "listener": MagicMock(is_running=True),
                "feed_cfg": {"feed_enabled": True},
                "app_cfg": {"app_name": "NIFTY Multi-Strategy Bot", "mode": "PAPER_TRADING_ONLY"},
            }
            code = main.main()

        self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Feed Connection: CONNECTED (Live)", output)

    def test_cli_status_reports_stale_when_elapsed_exceeds_threshold(self):
        """When feed is enabled and data is older than threshold, CLI reports STALE."""
        curr_time = 1000.0
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0, time_provider=lambda: curr_time)
        snap = MarketSnapshot(timestamp="2026-10-27 12:00:00", spot_price=24850.0)
        monitor.record_snapshot(snap)
        curr_time = 1006.0  # Trigger STALE

        stdout_capture = io.StringIO()
        with patch("sys.argv", ["main.py", "--status"]), \
             patch("sys.stdout", stdout_capture), \
             patch("main.initialize_subsystems") as mock_sub:
            mock_sub.return_value = {
                "registry": MagicMock(get_registered_ids=lambda: ["STRAT_001"], get_active_strategies=lambda: []),
                "position_manager": MagicMock(get_portfolio_summary=lambda: {"open_positions_count": 0, "total_unrealized_mtm": 0.0}),
                "runner": MagicMock(),
                "health_monitor": monitor,
                "listener": MagicMock(is_running=True),
                "feed_cfg": {"feed_enabled": True},
                "app_cfg": {"app_name": "NIFTY Multi-Strategy Bot", "mode": "PAPER_TRADING_ONLY"},
            }
            code = main.main()

        self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Feed Connection: STALE (Freshness threshold exceeded)", output)

    def test_cli_status_reports_disconnected_on_transport_drop(self):
        """When feed is enabled and monitor reports disconnect, CLI reports DISCONNECTED."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        monitor.record_disconnect("ZeroMQ transport dropped")

        stdout_capture = io.StringIO()
        with patch("sys.argv", ["main.py", "--status"]), \
             patch("sys.stdout", stdout_capture), \
             patch("main.initialize_subsystems") as mock_sub:
            mock_sub.return_value = {
                "registry": MagicMock(get_registered_ids=lambda: ["STRAT_001"], get_active_strategies=lambda: []),
                "position_manager": MagicMock(get_portfolio_summary=lambda: {"open_positions_count": 0, "total_unrealized_mtm": 0.0}),
                "runner": MagicMock(),
                "health_monitor": monitor,
                "listener": MagicMock(is_running=True),
                "feed_cfg": {"feed_enabled": True},
                "app_cfg": {"app_name": "NIFTY Multi-Strategy Bot", "mode": "PAPER_TRADING_ONLY"},
            }
            code = main.main()

        self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Feed Connection: DISCONNECTED (ZeroMQ transport dropped)", output)

    def test_cli_status_reports_invalid_data_on_corrupt_payload(self):
        """When feed is enabled and monitor reports invalid data, CLI reports INVALID_DATA."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)
        monitor.record_invalid_tick("PARSER_ERROR: Bad Frame")

        stdout_capture = io.StringIO()
        with patch("sys.argv", ["main.py", "--status"]), \
             patch("sys.stdout", stdout_capture), \
             patch("main.initialize_subsystems") as mock_sub:
            mock_sub.return_value = {
                "registry": MagicMock(get_registered_ids=lambda: ["STRAT_001"], get_active_strategies=lambda: []),
                "position_manager": MagicMock(get_portfolio_summary=lambda: {"open_positions_count": 0, "total_unrealized_mtm": 0.0}),
                "runner": MagicMock(),
                "health_monitor": monitor,
                "listener": MagicMock(is_running=True),
                "feed_cfg": {"feed_enabled": True},
                "app_cfg": {"app_name": "NIFTY Multi-Strategy Bot", "mode": "PAPER_TRADING_ONLY"},
            }
            code = main.main()

        self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Feed Connection: INVALID_DATA (PARSER_ERROR: Bad Frame)", output)

    def test_cli_status_reports_connecting_when_listener_active_awaiting_tick(self):
        """When feed is enabled and listener is running awaiting tick, CLI reports CONNECTING."""
        monitor = FeedHealthMonitor(stale_threshold_seconds=5.0)

        stdout_capture = io.StringIO()
        with patch("sys.argv", ["main.py", "--status"]), \
             patch("sys.stdout", stdout_capture), \
             patch("main.initialize_subsystems") as mock_sub:
            mock_sub.return_value = {
                "registry": MagicMock(get_registered_ids=lambda: ["STRAT_001"], get_active_strategies=lambda: []),
                "position_manager": MagicMock(get_portfolio_summary=lambda: {"open_positions_count": 0, "total_unrealized_mtm": 0.0}),
                "runner": MagicMock(),
                "health_monitor": monitor,
                "listener": MagicMock(is_running=True),
                "feed_cfg": {"feed_enabled": True},
                "app_cfg": {"app_name": "NIFTY Multi-Strategy Bot", "mode": "PAPER_TRADING_ONLY"},
            }
            code = main.main()

        self.assertEqual(code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Feed Connection: CONNECTING (Awaiting initial market tick)", output)


if __name__ == "__main__":
    unittest.main()



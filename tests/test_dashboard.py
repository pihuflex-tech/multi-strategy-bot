"""
tests/test_dashboard.py
Comprehensive deterministic offline tests for Phase 3A: Flask Dashboard Service & REST API.

Guarantees Verified:
- create_app factory behavior and offline configuration.
- GET /api/health response format, decoupling from market feed.
- GET /api/state with valid mock telemetry.
- Safe fallbacks when telemetry is missing or unavailable (UNKNOWN, NOT_CONNECTED, NOT_AVAILABLE).
- Sanitization of credentials, secrets, tokens, NaN/Inf, and errors.
- Exception containment: Provider exceptions never expose tracebacks or secrets.
- DashboardService lifecycle with mocked WSGI server (start, is_running, idempotent shutdown).
- Offline safety: No real sockets or listening ports are bound.
"""

import math
import socket
import threading
import unittest
from unittest.mock import MagicMock, patch

from src.dashboard.api import (
    api_bp,
    sanitize_telemetry,
    _is_sensitive_key,
    _sanitize_error_code,
    ApiPlaceholder,
)
from src.dashboard.routes import ui_bp, attach_ui, RoutesPlaceholder
from src.dashboard.app import create_app, AppPlaceholder
from src.dashboard.dashboard_service import DashboardService, DashboardServicePlaceholder


class DummyMockServer:
    """Mock WSGI server for deterministic socketless lifecycle testing."""

    def __init__(self):
        self.serve_forever_started = threading.Event()
        self.shutdown_called = False
        self.server_close_called = False
        self._stop_event = threading.Event()

    def serve_forever(self):
        self.serve_forever_started.set()
        self._stop_event.wait(timeout=3.0)

    def shutdown(self):
        self.shutdown_called = True
        self._stop_event.set()

    def server_close(self):
        self.server_close_called = True


class TestDashboardAppFactory(unittest.TestCase):
    """Tests for Flask application factory configuration."""

    def test_create_app_defaults(self):
        app = create_app()
        self.assertFalse(app.config["DEBUG"])
        self.assertFalse(app.config["TESTING"])
        self.assertIsNone(app.config["TELEMETRY_PROVIDER"])
        self.assertIn("dashboard_api", app.blueprints)

    def test_create_app_custom_config(self):
        app = create_app(
            telemetry_provider={"status": "CUSTOM"},
            config={"TESTING": True, "CUSTOM_KEY": 1234},
        )
        self.assertTrue(app.config["TESTING"])
        self.assertEqual(app.config["CUSTOM_KEY"], 1234)
        self.assertEqual(app.config["TELEMETRY_PROVIDER"], {"status": "CUSTOM"})

    def test_app_placeholder_alias(self):
        app = AppPlaceholder.create_app()
        self.assertIsNotNone(app)
        self.assertIn("dashboard_api", app.blueprints)


class TestDashboardHealthEndpoint(unittest.TestCase):
    """Tests for the GET /api/health endpoint."""

    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def test_get_health_contract(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        data = response.get_json()

        self.assertEqual(data.get("status"), "ONLINE")
        self.assertEqual(data.get("service"), "dashboard_api")
        self.assertFalse(data.get("market_feed_verified"))
        self.assertIn("Dashboard service health does not prove market-feed health.", data.get("note", ""))

    def test_post_health_disallowed(self):
        response = self.client.post("/api/health", json={"test": 1})
        self.assertEqual(response.status_code, 405)


class TestDashboardStateEndpoint(unittest.TestCase):
    """Tests for the GET /api/state endpoint and telemetry sanitization."""

    def test_get_state_with_valid_mock_telemetry(self):
        mock_data = {
            "bot_status": "RUNNING",
            "feed_connection": "CONNECTED",
            "market_data_status": "ACTIVE",
            "last_tick": {
                "timestamp": "2026-10-25 09:20:00",
                "spot": 24850.50,
                "futures": 24890.25,
                "symbol": "NIFTY",
            },
            "strategies": [
                {"strategy_id": "STRAT_001", "name": "Long Call", "mode": "DISABLED", "entries_today": 0}
            ],
            "open_positions": [],
            "portfolio": {
                "open_positions_count": 0,
                "open_legs_count": 0,
                "total_unrealized_mtm": 0.0,
                "total_realized_gross_pnl": 1500.0,
                "total_charges": 45.2,
                "total_net_realized_pnl": 1454.8,
                "net_combined_pnl": 1454.8,
                "currency": "INR",
            },
            "errors": {
                "last_feed_error": "NONE",
                "last_system_error": "NONE",
            },
        }

        app = create_app(telemetry_provider=lambda: mock_data)
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["bot_status"], "RUNNING")
        self.assertEqual(data["feed_connection"], "CONNECTED")
        self.assertEqual(data["market_data_status"], "ACTIVE")
        self.assertEqual(data["last_tick"]["spot"], 24850.50)
        self.assertEqual(data["last_tick"]["futures"], 24890.25)
        self.assertEqual(len(data["strategies"]), 1)
        self.assertEqual(data["strategies"][0]["strategy_id"], "STRAT_001")
        self.assertEqual(data["portfolio"]["total_realized_gross_pnl"], 1500.0)
        self.assertEqual(data["errors"]["last_system_error"], "NONE")

    def test_get_state_with_none_provider(self):
        app = create_app(telemetry_provider=None)
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["bot_status"], "UNKNOWN")
        self.assertEqual(data["feed_connection"], "NOT_CONNECTED")
        self.assertEqual(data["market_data_status"], "NOT_AVAILABLE")
        self.assertIsNone(data["last_tick"]["spot"])
        self.assertIsNone(data["last_tick"]["futures"])
        self.assertEqual(data["last_tick"]["timestamp"], "NOT_AVAILABLE")
        self.assertEqual(data["strategies"], [])
        self.assertEqual(data["open_positions"], [])
        self.assertIsNone(data["portfolio"]["open_positions_count"])
        self.assertIsNone(data["portfolio"]["total_unrealized_mtm"])
        self.assertEqual(data["errors"]["last_system_error"], "TELEMETRY_UNAVAILABLE")

    def test_get_state_provider_raises_exception_safe_fallback(self):
        def faulty_provider():
            raise RuntimeError("Database connection string postgresql://admin:secret_pass_123@db:5432 failed")

        app = create_app(telemetry_provider=faulty_provider)
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["bot_status"], "UNKNOWN")
        self.assertEqual(data["errors"]["last_system_error"], "TELEMETRY_PROVIDER_ERROR")
        # Ensure secret or exception text does not appear anywhere in the JSON response
        response_text = response.get_data(as_text=True)
        self.assertNotIn("secret_pass_123", response_text)
        self.assertNotIn("RuntimeError", response_text)
        self.assertNotIn("postgresql://", response_text)

    def test_get_state_malformed_provider_return(self):
        app = create_app(telemetry_provider=lambda: ["not", "a", "dict"])
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["bot_status"], "UNKNOWN")
        self.assertEqual(data["errors"]["last_system_error"], "INVALID_TELEMETRY_FORMAT")

    def test_get_state_with_object_protocols(self):
        class StateObject:
            def get_state(self):
                return {"bot_status": "ONLINE_ACTIVE", "spot_price": 24900.0}

        app1 = create_app(telemetry_provider=StateObject())
        client1 = app1.test_client()
        res1 = client1.get("/api/state").get_json()
        self.assertEqual(res1["bot_status"], "ONLINE_ACTIVE")
        self.assertEqual(res1["last_tick"]["spot"], 24900.0)

        class DictObject:
            def to_dict(self):
                return {"bot_status": "PAUSED"}

        app2 = create_app(telemetry_provider=DictObject())
        client2 = app2.test_client()
        res2 = client2.get("/api/state").get_json()
        self.assertEqual(res2["bot_status"], "PAUSED")

    def test_sensitive_credentials_stripping(self):
        dirty_payload = {
            "bot_status": "RUNNING",
            "api_key": "LIVE_API_KEY_9999",
            "api_secret": "LIVE_SECRET_ABCD",
            "token": "JWT_BEARER_TOKEN",
            "totp_secret": "MY_TOTP_KEY",
            "password": "my_master_password",
            "strategies": [
                {
                    "strategy_id": "STRAT_001",
                    "name": "Long Call",
                    "secret_strat_token": "SECRET_LEAK",
                    "mode": "DISABLED",
                }
            ],
            "open_positions": [
                {
                    "symbol": "NIFTY26OCT24800CE",
                    "client_secret": "POS_SECRET",
                    "qty": 50,
                }
            ],
            "errors": {
                "last_feed_error": "Auth failed with token=SECRET_TOKEN_XYZ",
                "last_system_error": "Traceback at File \"/secret/path/main.py\", line 10",
            },
        }

        app = create_app(telemetry_provider=lambda: dirty_payload)
        client = app.test_client()

        response = client.get("/api/state")
        data = response.get_json()
        raw_text = response.get_data(as_text=True)

        # None of the sensitive values should be in the response
        self.assertNotIn("LIVE_API_KEY_9999", raw_text)
        self.assertNotIn("LIVE_SECRET_ABCD", raw_text)
        self.assertNotIn("JWT_BEARER_TOKEN", raw_text)
        self.assertNotIn("MY_TOTP_KEY", raw_text)
        self.assertNotIn("my_master_password", raw_text)
        self.assertNotIn("SECRET_LEAK", raw_text)
        self.assertNotIn("POS_SECRET", raw_text)

        # Sensitive keys must be stripped
        self.assertNotIn("api_key", data)
        self.assertNotIn("api_secret", data)
        self.assertNotIn("token", data)
        self.assertNotIn("secret_strat_token", data["strategies"][0])
        self.assertNotIn("client_secret", data["open_positions"][0])

        # Redacted error messages
        self.assertEqual(data["errors"]["last_feed_error"], "REDACTED_ERROR")
        self.assertEqual(data["errors"]["last_system_error"], "INTERNAL_ERROR")

    def test_nan_and_inf_handling(self):
        payload_with_nan = {
            "spot_price": float("nan"),
            "futures_price": float("inf"),
            "portfolio": {
                "total_unrealized_mtm": float("nan"),
                "total_realized_gross_pnl": float("-inf"),
            },
        }
        app = create_app(telemetry_provider=payload_with_nan)
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertIsNone(data["last_tick"]["spot"])
        self.assertIsNone(data["last_tick"]["futures"])
        self.assertIsNone(data["portfolio"]["total_unrealized_mtm"])
        self.assertIsNone(data["portfolio"]["total_realized_gross_pnl"])

    def test_post_state_disallowed(self):
        app = create_app()
        client = app.test_client()
        response = client.post("/api/state", json={"command": "START"})
        self.assertEqual(response.status_code, 405)

    def test_get_state_unavailable_portfolio_returns_null_fields(self):
        """Verifies that missing or null portfolio data results in null metrics without fabricating 0.0."""
        payload_no_portfolio = {
            "bot_status": "STANDBY",
            "portfolio": None,
        }
        app = create_app(telemetry_provider=payload_no_portfolio)
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        p = data["portfolio"]
        self.assertIsNone(p["open_positions_count"])
        self.assertIsNone(p["open_legs_count"])
        self.assertIsNone(p["total_unrealized_mtm"])
        self.assertIsNone(p["total_realized_gross_pnl"])
        self.assertIsNone(p["total_charges"])
        self.assertIsNone(p["total_net_realized_pnl"])
        self.assertIsNone(p["net_combined_pnl"])

    def test_get_state_genuine_zeroes_preserved(self):
        """Verifies that genuine zero portfolio metrics remain 0.0 and are not converted to null."""
        payload_zeros = {
            "bot_status": "STANDBY",
            "portfolio": {
                "open_positions_count": 0,
                "open_legs_count": 0,
                "total_unrealized_mtm": 0.0,
                "total_realized_gross_pnl": 0.0,
                "total_charges": 0.0,
                "total_net_realized_pnl": 0.0,
                "net_combined_pnl": 0.0,
            },
        }
        app = create_app(telemetry_provider=payload_zeros)
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        p = data["portfolio"]
        self.assertEqual(p["open_positions_count"], 0)
        self.assertEqual(p["open_legs_count"], 0)
        self.assertEqual(p["total_unrealized_mtm"], 0.0)
        self.assertEqual(p["total_realized_gross_pnl"], 0.0)
        self.assertEqual(p["total_charges"], 0.0)
        self.assertEqual(p["total_net_realized_pnl"], 0.0)
        self.assertEqual(p["net_combined_pnl"], 0.0)

    def test_get_state_independent_statuses(self):
        """Verifies that independent statuses are extracted, sanitized, and passed through."""
        payload = {
            "app_lifecycle": "RUNNING",
            "bot_status": "STANDBY",
            "feed_connection": "NOT_CONNECTED",
            "market_data_status": "NOT_AVAILABLE",
            "strategy_engine": "ALL_DISABLED (0/25)",
            "paper_execution": "STANDBY",
        }
        app = create_app(telemetry_provider=payload)
        client = app.test_client()

        response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["app_lifecycle"], "RUNNING")
        self.assertEqual(data["bot_status"], "STANDBY")
        self.assertEqual(data["feed_connection"], "NOT_CONNECTED")
        self.assertEqual(data["market_data_status"], "NOT_AVAILABLE")
        self.assertEqual(data["strategy_engine"], "ALL_DISABLED (0/25)")
        self.assertEqual(data["paper_execution"], "STANDBY")


class TestDashboardServiceLifecycle(unittest.TestCase):
    """Tests for DashboardService lifecycle management using mock servers."""

    def test_service_initialization_defaults(self):
        service = DashboardService(host="127.0.0.1", port=8080)
        self.assertEqual(service.host, "127.0.0.1")
        self.assertEqual(service.port, 8080)
        self.assertFalse(service.is_running)
        self.assertIsNone(service.last_error)

    def test_service_start_and_shutdown_with_mock_server(self):
        dummy_server = DummyMockServer()

        def mock_factory(host, port, app, **kwargs):
            return dummy_server

        service = DashboardService(
            host="127.0.0.1",
            port=9999,
            server_factory=mock_factory,
        )

        started = service.start(daemon=True)
        self.assertTrue(started)
        self.assertTrue(dummy_server.serve_forever_started.wait(timeout=1.0))
        self.assertTrue(service.is_running)

        # Idempotent start call
        second_start = service.start()
        self.assertTrue(second_start)

        # Shutdown
        stopped = service.shutdown(timeout=1.0)
        self.assertTrue(stopped)
        self.assertTrue(dummy_server.shutdown_called)
        self.assertTrue(dummy_server.server_close_called)
        self.assertFalse(service.is_running)

        # Repeated shutdown calls are safe and idempotent
        repeat_shutdown = service.shutdown(timeout=1.0)
        self.assertTrue(repeat_shutdown)
        self.assertFalse(service.is_running)

    def test_service_server_factory_failure(self):
        def failing_factory(host, port, app, **kwargs):
            raise OSError("Address already in use")

        service = DashboardService(server_factory=failing_factory)
        started = service.start()
        self.assertFalse(started)
        self.assertFalse(service.is_running)
        self.assertIsNotNone(service.last_error)
        self.assertTrue(service.last_error.startswith("SERVER_CREATION_FAILED:OSError"))

    def test_dashboard_service_placeholder_alias(self):
        dummy = DummyMockServer()
        service = DashboardServicePlaceholder(server_factory=lambda *a, **k: dummy)
        self.assertIsInstance(service, DashboardService)
        service.start()
        self.assertTrue(service.is_running)
        service.shutdown()
        self.assertFalse(service.is_running)


class TestOfflineSocketSafety(unittest.TestCase):
    """
    Test verifying that neither the test client nor the mocked DashboardService
    opens or binds any network socket during testing.
    """

    def test_socket_prohibition_during_offline_testing(self):
        # Patch socket.socket so any attempt to create a network socket immediately fails
        with patch("socket.socket") as mock_sock:
            mock_sock.side_effect = RuntimeError("Network socket creation strictly prohibited in offline tests!")

            # 1. Test client requests to Flask API
            app = create_app(telemetry_provider=lambda: {"bot_status": "RUNNING"})
            client = app.test_client()

            health_res = client.get("/api/health")
            self.assertEqual(health_res.status_code, 200)

            state_res = client.get("/api/state")
            self.assertEqual(state_res.status_code, 200)

            # 2. DashboardService lifecycle with mock server
            dummy_server = DummyMockServer()
            service = DashboardService(server_factory=lambda *a, **k: dummy_server)
            self.assertTrue(service.start())
            self.assertTrue(service.is_running)
            self.assertTrue(service.shutdown())
            self.assertFalse(service.is_running)

            # If any socket had been created, RuntimeError would have been raised.
            mock_sock.assert_not_called()


class TestDashboardUIRoutes(unittest.TestCase):
    """Deterministic offline unit tests for Phase 3B UI dashboard rendering."""

    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def test_get_index_renders_html_successfully(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        text = response.get_data(as_text=True)

        self.assertIn("<!DOCTYPE html>", text)
        self.assertIn("NIFTY Multi-Strategy Quant Monitor", text)

    def test_index_references_api_state_and_polling(self):
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        self.assertIn("/api/state", text)
        self.assertIn("fetchDashboardState", text)
        self.assertIn("POLL_INTERVAL_MS", text)

    def test_index_contains_required_sections(self):
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        # Section A: System Overview
        self.assertIn("Bot Status", text)
        self.assertIn("Feed Connection", text)
        self.assertIn("Market Health", text)
        self.assertIn("Last Market Tick", text)
        self.assertIn("Last Dashboard Sync", text)

        # Section B: Strategy Monitor
        self.assertIn("Configured Strategy Inventory", text)
        self.assertIn("Strategy telemetry not available", text)

        # Section C: Open Paper Positions
        self.assertIn("Open Paper Trading Positions", text)
        self.assertIn("NO OPEN PAPER POSITIONS", text)

        # Section D: Portfolio Summary
        self.assertIn("Portfolio Accounting Summary", text)
        self.assertIn("Unrealized MTM", text)
        self.assertIn("Realized Gross P&L", text)
        self.assertIn("Total Charges", text)
        self.assertIn("Net Combined P&L", text)

        # Section E: Telemetry & Error Audit
        self.assertIn("Telemetry Diagnostics & Error Audit", text)
        self.assertIn("Last Feed Error Code", text)
        self.assertIn("Last System Error Code", text)

    def test_index_safety_boundary_banner_present(self):
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        self.assertIn("Architectural Safety Boundary", text)
        self.assertIn("Dashboard web service health confirms only local HTTP telemetry serving", text)
        self.assertIn("DASHBOARD DATA UNAVAILABLE", text)

    def test_index_has_no_remote_cdns_or_scripts(self):
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        # No external script tags or remote stylesheets
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("cdn.", text)
        self.assertNotIn("unpkg.com", text)
        self.assertNotIn("cdnjs.cloudflare.com", text)
        self.assertNotIn("fonts.googleapis.com", text)

    def test_post_index_method_not_allowed(self):
        response = self.client.post("/", data={"test": 1})
        self.assertEqual(response.status_code, 405)

    def test_routes_placeholder_alias(self):
        self.assertEqual(RoutesPlaceholder.blueprint, ui_bp)

    def test_empty_data_fallbacks_present(self):
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        self.assertIn("NO OPEN PAPER POSITIONS", text)
        self.assertIn("Strategy telemetry not available", text)
        self.assertIn("NOT AVAILABLE", text)
        self.assertIn("NOT_CONNECTED", text)

    def test_xss_prevention_static_inspection(self):
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        # Confirm script contains safe textContent and createElement patterns
        self.assertIn("textContent", text)
        self.assertIn("document.createElement", text)
        self.assertIn("createCell", text)
        # Confirm no unsanitized string concatenation directly assigned to innerHTML from API variables
        self.assertNotIn("innerHTML = pos.", text)
        self.assertNotIn("innerHTML = s.", text)
        self.assertNotIn("innerHTML = data.", text)

    def test_index_handles_missing_portfolio_metrics_without_false_zeroes(self):
        """Verifies frontend script guards against rendering null/undefined metrics as ₹0.00."""
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        # Check formatCurrency returns "NOT AVAILABLE" for null or undefined
        self.assertIn("if (val === null || val === undefined || isNaN(val))", text)
        self.assertIn('return "NOT AVAILABLE";', text)

        # Check that null MTM doesn't receive false positive text-green class
        self.assertIn("if (mtm !== null && mtm !== undefined && !isNaN(mtm))", text)
        self.assertIn('mtmEl.className = "card-value mono text-muted"', text)

    def test_index_contains_independent_status_indicators(self):
        """Verifies that Strategy Engine and Paper Execution indicators exist in the UI."""
        response = self.client.get("/")
        text = response.get_data(as_text=True)

        self.assertIn("Strategy Engine", text)
        self.assertIn("Paper Execution", text)
        self.assertIn("stratEngineBadge", text)
        self.assertIn("paperExecBadge", text)


if __name__ == "__main__":
    unittest.main()


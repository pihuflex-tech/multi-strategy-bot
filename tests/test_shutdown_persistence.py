"""
test_shutdown_persistence.py
Deterministic offline tests for graceful shutdown position persistence hardening (F-11).
Verifies:
1. Position state is explicitly flushed via save_state() during graceful shutdown.
2. Persistence failure is logged accurately and does not hide errors.
3. Dashboard shutdown is still performed even if state persistence fails or raises.
4. Shutdown handles cases where position_manager is None (early startup failure) safely.
"""

import threading
import unittest
from unittest.mock import MagicMock, patch

import main
from src.dashboard.dashboard_service import DashboardService


class DummyMockServer:
    """Mock WSGI server for deterministic socketless shutdown testing."""

    def __init__(self):
        self.shutdown_called = False
        self.server_close_called = False
        self._stop_event = threading.Event()

    def serve_forever(self):
        self._stop_event.wait(timeout=2.0)

    def shutdown(self):
        self.shutdown_called = True
        self._stop_event.set()

    def server_close(self):
        self.server_close_called = True


class TestShutdownPersistence(unittest.TestCase):
    """Test suite for graceful shutdown persistence behaviors in main.py."""

    def test_normal_shutdown_flushes_position_state(self):
        """Verifies that normal graceful shutdown calls position_manager.save_state()."""
        dummy_server = DummyMockServer()

        with patch("main.initialize_subsystems") as mock_init:
            mock_pm = MagicMock()
            mock_pm.is_healthy.return_value = True
            mock_pm.save_state.return_value = True

            mock_runner = MagicMock()
            mock_runner.is_healthy.return_value = True

            mock_registry = MagicMock()
            mock_registry.list_strategies.return_value = []

            mock_init.return_value = {
                "app_cfg": {"dashboard_enabled": True},
                "charges_calc": MagicMock(),
                "position_manager": mock_pm,
                "risk_manager": MagicMock(),
                "paper_broker": MagicMock(),
                "registry": mock_registry,
                "runner": mock_runner,
            }

            exit_code = main.main(
                argv=[],
                server_factory=lambda *a, **k: dummy_server,
                run_once=True
            )

            self.assertEqual(exit_code, 0)
            mock_pm.save_state.assert_called_once()
            self.assertTrue(dummy_server.shutdown_called)

    def test_persistence_failure_does_not_skip_dashboard_shutdown(self):
        """
        Verifies that if position_manager.save_state() raises an exception during shutdown,
        dashboard_service.shutdown() is still executed.
        """
        dummy_server = DummyMockServer()

        with patch("main.initialize_subsystems") as mock_init:
            mock_pm = MagicMock()
            mock_pm.is_healthy.return_value = True
            mock_pm.save_state.side_effect = RuntimeError("Disk full during shutdown flush")

            mock_runner = MagicMock()
            mock_runner.is_healthy.return_value = True

            mock_registry = MagicMock()
            mock_registry.list_strategies.return_value = []

            mock_init.return_value = {
                "app_cfg": {"dashboard_enabled": True},
                "charges_calc": MagicMock(),
                "position_manager": mock_pm,
                "risk_manager": MagicMock(),
                "paper_broker": MagicMock(),
                "registry": mock_registry,
                "runner": mock_runner,
            }

            with self.assertLogs("MainOrchestrator", level="ERROR") as log_capture:
                exit_code = main.main(
                    argv=[],
                    server_factory=lambda *a, **k: dummy_server,
                    run_once=True
                )

            self.assertEqual(exit_code, 0)
            # Verify save_state was attempted
            mock_pm.save_state.assert_called_once()
            # Verify dashboard shutdown was still executed
            self.assertTrue(dummy_server.shutdown_called)
            # Verify the error was logged accurately
            self.assertTrue(any("Disk full during shutdown flush" in msg for msg in log_capture.output))

    def test_persistence_returning_false_is_logged(self):
        """Verifies that save_state() returning False is logged with details."""
        dummy_server = DummyMockServer()

        with patch("main.initialize_subsystems") as mock_init:
            mock_pm = MagicMock()
            mock_pm.is_healthy.return_value = True
            mock_pm.save_state.return_value = False
            mock_pm.last_save_error = "Mocked write error"

            mock_runner = MagicMock()
            mock_runner.is_healthy.return_value = True

            mock_registry = MagicMock()
            mock_registry.list_strategies.return_value = []

            mock_init.return_value = {
                "app_cfg": {"dashboard_enabled": True},
                "charges_calc": MagicMock(),
                "position_manager": mock_pm,
                "risk_manager": MagicMock(),
                "paper_broker": MagicMock(),
                "registry": mock_registry,
                "runner": mock_runner,
            }

            with self.assertLogs("MainOrchestrator", level="ERROR") as log_capture:
                exit_code = main.main(
                    argv=[],
                    server_factory=lambda *a, **k: dummy_server,
                    run_once=True
                )

            self.assertEqual(exit_code, 0)
            mock_pm.save_state.assert_called_once()
            self.assertTrue(dummy_server.shutdown_called)
            self.assertTrue(any("Mocked write error" in msg for msg in log_capture.output))

    def test_startup_failure_before_position_manager_available(self):
        """Verifies that an error in initialize_subsystems exits cleanly without AttributeError on pm."""
        with patch("main.initialize_subsystems") as mock_init:
            mock_init.side_effect = ValueError("Corrupt configuration at startup")

            exit_code = main.main(argv=[], run_once=True)
            self.assertEqual(exit_code, 1)

    def test_runtime_error_preserves_error_code_and_persists_state(self):
        """
        Verifies that if a runtime error occurs during execution, position_manager state
        is still flushed in finally, and the original error exit code (1) is preserved.
        """
        with patch("main.initialize_subsystems") as mock_init:
            mock_pm = MagicMock()
            mock_pm.is_healthy.return_value = True
            mock_pm.save_state.return_value = True

            mock_runner = MagicMock()
            mock_runner.is_healthy.return_value = True

            mock_registry = MagicMock()
            mock_registry.list_strategies.return_value = []

            mock_init.return_value = {
                "app_cfg": {"dashboard_enabled": True},
                "charges_calc": MagicMock(),
                "position_manager": mock_pm,
                "risk_manager": MagicMock(),
                "paper_broker": MagicMock(),
                "registry": mock_registry,
                "runner": mock_runner,
            }

            # Inject error in DashboardService.start
            with patch.object(DashboardService, "start", side_effect=RuntimeError("Dashboard bind error")):
                exit_code = main.main(argv=[], run_once=True)

            self.assertEqual(exit_code, 1)
            # save_state must still be called in finally block
            mock_pm.save_state.assert_called_once()


if __name__ == "__main__":
    unittest.main()

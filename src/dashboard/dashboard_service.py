"""
dashboard_service.py
Controllable WSGI background service runner for web monitoring.

Phase 3A: Minimal, controllable background service runner:
- Uses Werkzeug's make_server for precise lifecycle control.
- Allows injecting server_factory to prevent socket binding during offline testing.
- Supports start() and idempotent shutdown().
- Bounds thread join timeouts; does not hang or use reloader.
- Safe for repeated shutdown calls.
"""

import logging
import threading
from typing import Any, Callable, Dict, Optional
from werkzeug.serving import make_server

from src.dashboard.app import create_app

logger = logging.getLogger("DashboardService")


class DashboardService:
    """
    Background lifecycle runner for the Flask dashboard web service.
    
    Provides explicit, controllable start() and shutdown() routines
    suitable for headless background execution and deterministic unit testing.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        telemetry_provider: Any = None,
        app: Optional[Any] = None,
        server_factory: Optional[Callable[..., Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the DashboardService.
        
        Args:
            host: IP address or hostname to bind.
            port: Port number for the HTTP server.
            telemetry_provider: Optional telemetry source provider.
            app: Optional pre-configured Flask app. If None, create_app is used.
            server_factory: Factory function returning a WSGI server. Defaults to Werkzeug make_server.
            config: Optional configuration dictionary.
        """
        self.host = host
        self.port = port
        self.telemetry_provider = telemetry_provider
        self.config = config or {}
        self.server_factory = server_factory if server_factory is not None else make_server

        if app is not None:
            self.app = app
        else:
            self.app = create_app(telemetry_provider=self.telemetry_provider, config=self.config)

        self._server: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._is_running = False
        self._last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Return True if the service is currently active and thread is alive."""
        with self._lock:
            return self._is_running and self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> Optional[str]:
        """Return the last recorded error code or message."""
        with self._lock:
            return self._last_error

    def start(self, daemon: bool = True) -> bool:
        """
        Start the WSGI server on a background thread.
        
        Args:
            daemon: If True, the worker thread will run as a daemon thread.
            
        Returns:
            bool: True if started or already running; False on error.
        """
        with self._lock:
            if self._is_running and self._thread is not None and self._thread.is_alive():
                logger.info("DashboardService already running.")
                return True

            try:
                self._server = self.server_factory(self.host, self.port, self.app, threaded=True)
            except Exception as exc:
                self._last_error = f"SERVER_CREATION_FAILED:{type(exc).__name__}"
                self._is_running = False
                self._server = None
                logger.error("Failed to instantiate dashboard server: %s", self._last_error)
                return False

            server_instance = self._server

            def _run_server() -> None:
                try:
                    server_instance.serve_forever()
                except Exception as exc:
                    with self._lock:
                        self._last_error = f"SERVER_RUNTIME_ERROR:{type(exc).__name__}"
                        self._is_running = False
                    logger.error("Dashboard server terminated with error: %s", type(exc).__name__)

            self._thread = threading.Thread(
                target=_run_server,
                name="FlaskDashboardServiceThread",
                daemon=daemon,
            )
            self._thread.start()
            self._is_running = True
            self._last_error = None
            return True

    def shutdown(self, timeout: float = 2.0) -> bool:
        """
        Stop the WSGI server and join the background worker thread.
        
        Repeated calls are safe and idempotent.
        
        Args:
            timeout: Maximum seconds to wait for thread termination.
            
        Returns:
            bool: True if cleanly shut down or already stopped; False on timeout/error.
        """
        with self._lock:
            if not self._is_running and (self._thread is None or not self._thread.is_alive()):
                return True

            server = self._server
            thread = self._thread
            self._is_running = False

        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                with self._lock:
                    self._last_error = f"SERVER_SHUTDOWN_ERROR:{type(exc).__name__}"
                logger.warning("Error during server shutdown: %s", type(exc).__name__)

            if hasattr(server, "server_close") and callable(getattr(server, "server_close")):
                try:
                    server.server_close()
                except Exception:
                    pass

        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, timeout))
            if thread.is_alive():
                with self._lock:
                    self._last_error = "SERVER_THREAD_JOIN_TIMEOUT"
                logger.error("Dashboard service worker thread failed to terminate within timeout.")
                return False

        with self._lock:
            self._server = None
            self._thread = None
            self._is_running = False
            return True


class DashboardServicePlaceholder(DashboardService):
    """Backward compatibility alias for dashboard_service."""
    pass

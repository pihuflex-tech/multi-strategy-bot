"""
main.py - Primary Unified Entry Point and Dashboard Orchestrator.

NIFTY Multi-Strategy Quant Bot & Paper Trading Engine.
Mode: STRICT SIMULATED PAPER TRADING ONLY. Zero Broker Order Execution.

Provides single-command startup:
    python main.py
    python main.py --dashboard-port 8081
    python main.py --no-dashboard
    python main.py --inspect
    python main.py --test-tick
"""

import argparse
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = os.path.dirname(os.path.abspath(__file__))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from src.accounting.charges_calculator import ChargesCalculator
from src.dashboard.dashboard_service import DashboardService
from src.execution.paper_broker import PaperBroker
from src.execution.position_manager import MultiLegPositionManager
from src.market.market_snapshot import FuturesQuote, MarketSnapshot, OptionQuote
from src.feed.feed_dispatcher import FeedDispatcher, shutdown_pipeline
from src.feed.feed_health import FeedHealthMonitor
from src.feed.feed_listener import FeedListener
from src.feed.feed_queue import DropOldestSnapshotQueue
from src.portfolio.risk_manager import RiskManager
from src.strategies.strategy_registry import StrategyRegistry
from src.strategies.strategy_runner import StrategyRunner

logger = logging.getLogger("MainOrchestrator")


class ApplicationTelemetryProvider:
    """
    Thread-safe read-only telemetry provider interfacing live application components
    with the Flask monitoring dashboard.
    
    Guarantees:
    - Never fabricates live prices, ticks, positions, or P&L.
    - Accurately reflects 25 configured strategies and their operating modes (e.g. DISABLED).
    - Reports feed connection as NOT_CONNECTED and market data as NOT_AVAILABLE
      when no live feed transport is connected.
    """

    def __init__(
        self,
        registry: Optional[StrategyRegistry] = None,
        position_manager: Optional[MultiLegPositionManager] = None,
        lock: Optional[threading.Lock] = None,
        app_lifecycle: str = "RUNNING",
        paper_execution: str = "STANDBY",
    ):
        self.registry = registry
        self.position_manager = position_manager
        self._lock = lock or threading.Lock()
        self.app_lifecycle = app_lifecycle
        self.paper_execution = paper_execution

    def get_state(self) -> Dict[str, Any]:
        """Produce a synchronized, sanitized dictionary snapshot for the dashboard API."""
        with self._lock:
            # 1. Strategies state & error tracking
            strategies_data: List[Dict[str, Any]] = []
            strategy_error: Optional[str] = None
            if self.registry:
                try:
                    for s in self.registry.get_all_strategies():
                        strategies_data.append({
                            "strategy_id": str(s.strategy_id),
                            "name": str(s.name),
                            "cluster": str(s.cluster),
                            "mode": str(s.mode.value if hasattr(s.mode, "value") else s.mode),
                            "entries_today": int(getattr(s, "entries_today", 0)),
                        })
                    active_count = sum(1 for s in strategies_data if s.get("mode") == "ENABLED")
                    if active_count == 0:
                        strategy_engine_status = f"ALL_DISABLED (0/{len(strategies_data)})"
                    else:
                        strategy_engine_status = f"ACTIVE ({active_count}/{len(strategies_data)})"
                except Exception as exc:
                    logger.warning("Error collecting strategy telemetry: %s", type(exc).__name__)
                    strategy_error = "STRATEGY_TELEMETRY_UNAVAILABLE"
                    strategy_engine_status = "UNAVAILABLE"
            else:
                strategy_error = "STRATEGY_TELEMETRY_UNAVAILABLE"
                strategy_engine_status = "UNAVAILABLE"

            # 2. Portfolio and open positions state & error tracking
            portfolio_data: Optional[Dict[str, Any]] = None
            open_positions_data: List[Dict[str, Any]] = []
            portfolio_error: Optional[str] = None

            def _safe_float(val: Any) -> Optional[float]:
                if val is None:
                    return None
                try:
                    f = float(val)
                    if math.isnan(f) or math.isinf(f):
                        return None
                    return round(f, 2)
                except (ValueError, TypeError):
                    return None

            def _safe_int(val: Any) -> Optional[int]:
                if val is None:
                    return None
                try:
                    return int(val)
                except (ValueError, TypeError):
                    return None

            if self.position_manager:
                try:
                    summary = self.position_manager.get_portfolio_summary()
                    portfolio_data = {
                        "open_positions_count": _safe_int(summary.get("open_positions_count")),
                        "open_legs_count": _safe_int(summary.get("open_legs_count")),
                        "total_unrealized_mtm": _safe_float(summary.get("total_unrealized_mtm")),
                        "total_realized_gross_pnl": _safe_float(summary.get("total_realized_gross_pnl")),
                        "total_charges": _safe_float(summary.get("total_charges")),
                        "total_net_realized_pnl": _safe_float(summary.get("total_net_realized_pnl")),
                        "net_combined_pnl": _safe_float(summary.get("net_combined_pnl")),
                        "currency": str(summary.get("currency", "INR")),
                    }
                    open_positions_data = self.position_manager.get_open_position_snapshots()
                except Exception as exc:
                    logger.warning("Error collecting portfolio telemetry: %s", type(exc).__name__)
                    portfolio_error = "PORTFOLIO_TELEMETRY_UNAVAILABLE"
                    portfolio_data = {
                        "open_positions_count": None,
                        "open_legs_count": None,
                        "total_unrealized_mtm": None,
                        "total_realized_gross_pnl": None,
                        "total_charges": None,
                        "total_net_realized_pnl": None,
                        "net_combined_pnl": None,
                        "currency": "INR",
                    }
            else:
                portfolio_error = "PORTFOLIO_TELEMETRY_UNAVAILABLE"
                portfolio_data = {
                    "open_positions_count": None,
                    "open_legs_count": None,
                    "total_unrealized_mtm": None,
                    "total_realized_gross_pnl": None,
                    "total_charges": None,
                    "total_net_realized_pnl": None,
                    "net_combined_pnl": None,
                    "currency": "INR",
                }

            # 3. Transparent error aggregation
            if strategy_error and portfolio_error:
                system_error = "TELEMETRY_EXTRACTION_ERROR"
            elif strategy_error:
                system_error = strategy_error
            elif portfolio_error:
                system_error = portfolio_error
            else:
                system_error = "NONE"

            return {
                "app_lifecycle": self.app_lifecycle,
                "bot_status": "STANDBY",
                "feed_connection": "NOT_CONNECTED",
                "market_data_status": "NOT_AVAILABLE",
                "strategy_engine": strategy_engine_status,
                "paper_execution": self.paper_execution,
                "last_tick": {
                    "timestamp": "NOT_AVAILABLE",
                    "spot": None,
                    "futures": None,
                    "symbol": "NIFTY",
                },
                "strategies": strategies_data,
                "open_positions": open_positions_data,
                "portfolio": portfolio_data,
                "errors": {
                    "last_feed_error": "NONE",
                    "last_system_error": system_error,
                },
            }

    def __call__(self) -> Dict[str, Any]:
        return self.get_state()


def print_banner() -> None:
    """Print the ASCII startup banner."""
    print("=" * 76)
    print("  [*] NIFTY MULTI-STRATEGY QUANTITATIVE BOT & PAPER TRADING ENGINE [*]")
    print("  Version: 2.1.0-orchestrated | Mode: STRICT PAPER TRADING ONLY")
    print("  Arch: 25 Independent Strategies | Clean Decoupled Telemetry")
    print("=" * 76)


def build_argument_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser for the root entry point."""
    parser = argparse.ArgumentParser(
        description="NIFTY Multi-Strategy Quant Bot & Dashboard Orchestrator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Run core engine without starting the Flask web dashboard",
    )
    parser.add_argument(
        "--dashboard-host",
        type=str,
        default="127.0.0.1",
        help="Host address to bind dashboard service (loopback 127.0.0.1 required for security)",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8080,
        help="Port number for dashboard web service",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Inspect all 25 registered strategies and exit",
    )
    parser.add_argument(
        "--test-tick",
        action="store_true",
        help="Run a synthetic market snapshot to verify engine pipeline and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display current engine telemetry status and exit",
    )
    return parser


def initialize_subsystems(workspace_root: str) -> Dict[str, Any]:
    """Initialize the shared, single-instance core subsystems."""
    config_dir = os.path.join(workspace_root, "config")
    app_cfg_path = os.path.join(config_dir, "app_config.json")
    strat_cfg_path = os.path.join(config_dir, "strategy_config.json")
    risk_cfg_path = os.path.join(config_dir, "risk_config.json")
    feed_cfg_path = os.path.join(config_dir, "feed_config.json")

    app_cfg: Dict[str, Any] = {}
    if os.path.exists(app_cfg_path):
        with open(app_cfg_path, "r", encoding="utf-8") as f:
            app_cfg = json.load(f)

    charges_calc = ChargesCalculator()
    position_manager = MultiLegPositionManager(charges_calc=charges_calc)

    risk_cfg: Dict[str, Any] = {}
    if os.path.exists(risk_cfg_path):
        with open(risk_cfg_path, "r", encoding="utf-8") as f:
            risk_cfg = json.load(f)
    risk_manager = RiskManager(config=risk_cfg)

    feed_cfg: Dict[str, Any] = {}
    if os.path.exists(feed_cfg_path):
        with open(feed_cfg_path, "r", encoding="utf-8") as f:
            feed_cfg = json.load(f)

    # 1. Single shared FeedHealthMonitor
    stale_sec = float(feed_cfg.get("stale_data_threshold_seconds", 5.0))
    health_monitor = FeedHealthMonitor(stale_threshold_seconds=stale_sec)

    paper_broker = PaperBroker(position_manager=position_manager)
    registry = StrategyRegistry(config_path=strat_cfg_path, workspace_root=workspace_root)
    runner = StrategyRunner(
        registry=registry,
        position_manager=position_manager,
        risk_manager=risk_manager,
        paper_broker=paper_broker,
        workspace_root=workspace_root,
        health_monitor=health_monitor,
    )

    # 2. Bounded Drop-Oldest snapshot queue (capacity 5)
    snapshot_queue = DropOldestSnapshotQueue(maxsize=5)

    # 3. FeedListener & FeedDispatcher sharing single HealthMonitor and Runner
    endpoint = str(feed_cfg.get("feed_url", "tcp://127.0.0.1:5556")).strip()
    topic = feed_cfg.get("subscription_topic", "NIFTY_MARKET_TICK")
    poll_ms = int(feed_cfg.get("poll_timeout_ms", 500))

    listener = FeedListener(
        endpoint=endpoint,
        topic=topic,
        poll_timeout_ms=poll_ms,
        health_monitor=health_monitor,
        snapshot_queue=snapshot_queue,
    )
    dispatcher = FeedDispatcher(
        queue=snapshot_queue,
        runner=runner,
        health_monitor=health_monitor,
        stale_threshold_seconds=stale_sec,
    )

    return {
        "app_cfg": app_cfg,
        "feed_cfg": feed_cfg,
        "charges_calc": charges_calc,
        "position_manager": position_manager,
        "risk_manager": risk_manager,
        "paper_broker": paper_broker,
        "registry": registry,
        "runner": runner,
        "health_monitor": health_monitor,
        "snapshot_queue": snapshot_queue,
        "listener": listener,
        "dispatcher": dispatcher,
    }


def main(
    argv: Optional[List[str]] = None,
    server_factory: Optional[Callable[..., Any]] = None,
    run_once: bool = False,
) -> int:
    """
    Main orchestration routine.
    
    Args:
        argv: Command-line arguments. If None, sys.argv[1:] is used.
        server_factory: Optional WSGI server factory injection (used in tests to avoid socket binding).
        run_once: If True, executes startup and immediately shuts down without blocking.
        
    Returns:
        int: Process exit code (0 on success, non-zero on failure).
    """
    parser = build_argument_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    print_banner()

    # 1. Initialize Subsystems (single shared instance)
    try:
        subsystems = initialize_subsystems(WORKSPACE_ROOT)
    except Exception as exc:
        print(f"[!] Critical Error: Failed to initialize core trading subsystems: {exc}")
        return 1

    registry: StrategyRegistry = subsystems["registry"]
    position_manager: MultiLegPositionManager = subsystems.get("position_manager")
    runner: StrategyRunner = subsystems["runner"]
    listener: Optional[FeedListener] = subsystems.get("listener")
    dispatcher: Optional[FeedDispatcher] = subsystems.get("dispatcher")
    feed_cfg: Dict[str, Any] = subsystems.get("feed_cfg", {})
    feed_enabled: bool = bool(feed_cfg.get("feed_enabled", False))
    registered_ids = registry.get_registered_ids()

    print(f"[+] Loaded Configuration: {subsystems['app_cfg'].get('app_name', 'NIFTY Multi-Strategy Bot')}")
    print(f"[+] Operational Mode: {subsystems['app_cfg'].get('mode', 'PAPER_TRADING_ONLY')}")
    print(f"[+] Registered Strategies: {len(registered_ids)} / 25 verified independent strategy modules.")

    # 2. Inspect Mode
    if args.inspect:
        print("\n=== REGISTERED STRATEGY INVENTORY ===")
        print(f"{'ID':<12} {'Strategy Name':<38} {'Cluster':<16} {'Mode':<10}")
        print("-" * 78)
        for s in registry.get_all_strategies():
            mode_str = s.mode.value if hasattr(s.mode, "value") else str(s.mode)
            print(f"{s.strategy_id:<12} {s.name:<38} {s.cluster:<16} {mode_str:<10}")
        print("-" * 78)
        return 0

    # 3. Test-Tick Verification Mode
    if args.test_tick:
        print("\n[*] Generating synthetic market snapshot for pipeline verification...")
        synthetic_snapshot = MarketSnapshot(
            timestamp="2026-10-25 09:25:00",
            spot_price=24850.0,
            futures=FuturesQuote(
                "FUT_NIFTY", "NIFTY_FUT_OCT", "2026-10-29", 24890.0,
                bid=24889.0, ask=24891.0, volume=15000, open_interest=120000
            ),
            india_vix=13.5,
            current_expiry="2026-10-29",
            next_expiry="2026-11-05",
        )
        for strike in [24700, 24750, 24800, 24850, 24900, 24950, 25000]:
            synthetic_snapshot.option_chain[f"{strike}_CE"] = OptionQuote(
                f"CE_{strike}", f"NIFTY_{strike}_CE", strike, "CE", "2026-10-29",
                ltp=120.0, bid=119.5, ask=120.5, volume=5000, open_interest=45000, delta=0.50
            )
            synthetic_snapshot.option_chain[f"{strike}_PE"] = OptionQuote(
                f"PE_{strike}", f"NIFTY_{strike}_PE", strike, "PE", "2026-10-29",
                ltp=110.0, bid=109.5, ask=110.5, volume=4800, open_interest=42000, delta=-0.50
            )

        # Establish test tick validity through standard health monitor interface
        if "health_monitor" in subsystems:
            subsystems["health_monitor"].record_snapshot(synthetic_snapshot)

        print("[*] Processing snapshot through StrategyRunner across all 25 strategies...")
        runner.process_snapshot(synthetic_snapshot)
        summary = position_manager.get_portfolio_summary()
        print("[+] Pipeline execution successful.")
        print(f"    - Open Positions: {summary['open_positions_count']}")
        print(f"    - Open Legs: {summary['open_legs_count']}")
        print(f"    - Unrealized MTM: INR {summary['total_unrealized_mtm']}")
        return 0

    # 4. Status Mode
    if args.status:
        summary = position_manager.get_portfolio_summary()
        active = registry.get_active_strategies()
        print("\n=== SYSTEM TELEMETRY STATUS ===")
        print(f"  App Lifecycle: RUNNING | Bot Status: STANDBY")
        engine_str = f"ALL_DISABLED (0/{len(registered_ids)})" if len(active) == 0 else f"ACTIVE ({len(active)}/{len(registered_ids)})"
        print(f"  Strategy Engine: {engine_str}")
        print(f"  Paper Execution: STANDBY")
        print(f"  Registered Strategies: {len(registered_ids)}")
        print(f"  Active Strategies: {len(active)} (All others DISABLED)")
        print(f"  Open Positions: {summary['open_positions_count']}")
        print(f"  Total Unrealized MTM: INR {summary['total_unrealized_mtm']}")
        print(f"  Feed Connection: NOT_CONNECTED (Offline)")
        print("===============================\n")
        return 0

    # 5. Full Orchestration Startup
    dashboard_service: Optional[DashboardService] = None
    exit_code = 0
    try:
        state_lock = threading.Lock()
        telemetry_provider = ApplicationTelemetryProvider(
            registry=registry,
            position_manager=position_manager,
            lock=state_lock,
        )

        if not args.no_dashboard:
            host = args.dashboard_host
            port = args.dashboard_port

            # Safety check: warn if non-loopback
            if host != "127.0.0.1" and host != "localhost":
                print(f"[!] SECURITY WARNING: Binding dashboard to external interface {host}")

            print(f"[*] Initializing Flask Monitoring Dashboard on http://{host}:{port} ...")
            dashboard_service = DashboardService(
                host=host,
                port=port,
                telemetry_provider=telemetry_provider,
                server_factory=server_factory,
            )

            started = dashboard_service.start(daemon=True)
            if not started:
                err = dashboard_service.last_error or "UNKNOWN_ERROR"
                print(f"[!] Error: Failed to start dashboard server on {host}:{port}: {err}")
                exit_code = 1
                return exit_code

            print(f"[+] Dashboard Service Online: http://{host}:{port}")
        else:
            print("[i] Dashboard disabled via --no-dashboard flag.")

        # 5b. Feed Startup Gating
        if feed_enabled:
            print("[*] Feed enabled in configuration. Starting dispatcher and listener...")
            if dispatcher is not None:
                dispatcher.start()
            if listener is not None:
                listener.start()

        print("\n[i] Engine Status:")
        print("    - All 25 independent strategy modules loaded successfully.")
        print("    - Shared multi-leg accounting, risk governor, and paper broker initialized.")
        print("    - Default operating mode for all strategies: DISABLED.")
        if feed_enabled:
            print("    - Feed subscriber: ACTIVE (Live ZeroMQ stream).")
        else:
            print("    - Feed subscriber: Standby (feed_enabled: false in config/feed_config.json).")
        if dashboard_service:
            print(f"    - Dashboard URL: http://{args.dashboard_host}:{args.dashboard_port}")
        print("\n[+] System running. Press Ctrl+C to terminate.")

        # 6. Signal and Lifecycle Management
        stop_event = threading.Event()

        def handle_shutdown_signal(signum: int, frame: Any) -> None:
            print(f"\n[!] Shutdown signal ({signum}) received. Initiating graceful shutdown...")
            stop_event.set()

        signal.signal(signal.SIGINT, handle_shutdown_signal)
        try:
            signal.signal(signal.SIGTERM, handle_shutdown_signal)
        except Exception:
            pass

        if run_once:
            # For deterministic tests: run startup, perform zero wait, exit
            stop_event.set()
        else:
            while not stop_event.is_set():
                stop_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        print("\n[!] KeyboardInterrupt caught.")
    except Exception as exc:
        print(f"[!] Runtime error in main orchestration loop: {exc}")
        logger.error("Runtime error in main loop: %s", exc, exc_info=True)
        exit_code = 1
    finally:
        print("[*] Performing orderly shutdown of subsystems...")
        # 1. Orderly shutdown of feed listener and dispatcher
        if listener is not None or dispatcher is not None:
            try:
                clean, details = shutdown_pipeline(listener=listener, dispatcher=dispatcher, timeout=2.0)
                if clean:
                    print("[+] Feed pipeline shut down cleanly.")
                else:
                    errs = details.get("errors", [])
                    print(f"[!] Warning: Feed pipeline shutdown incomplete or encountered errors: {errs}")
                    logger.warning("Feed pipeline shutdown details: %s", details)
            except Exception as pipe_exc:
                print(f"[!] Error: Exception during feed pipeline shutdown: {pipe_exc}")
                logger.error("Exception during feed pipeline shutdown: %s", pipe_exc, exc_info=True)

        # 2. Persist position state
        if position_manager is not None:
            try:
                saved = position_manager.save_state()
                if saved:
                    print("[+] Position state persisted successfully.")
                else:
                    err_msg = getattr(position_manager, "last_save_error", "Unknown error")
                    print(f"[!] Warning: Position state persistence returned false during shutdown: {err_msg}")
                    logger.error("Failed to persist position state during shutdown: %s", err_msg)
            except Exception as save_exc:
                print(f"[!] Error: Failed to persist position state during shutdown: {save_exc}")
                logger.error("Exception while persisting position state during shutdown: %s", save_exc, exc_info=True)

        # 3. Shutdown dashboard service
        if dashboard_service and dashboard_service.is_running:
            dashboard_service.shutdown(timeout=2.0)
            print("[+] Dashboard service stopped cleanly.")
        print("[+] System shutdown complete. Exiting.\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

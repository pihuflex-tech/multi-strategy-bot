"""
strategy_runner.py
Master Strategy Runner orchestrating the evaluation of all 25 independent strategies.
Connects the StrategyRegistry, MultiLegPositionManager, RiskManager, and PaperBroker.
"""

from typing import Dict, List, Optional, Any, Tuple
import os
import json
import time
import csv
import copy

from src.strategies.strategy_registry import StrategyRegistry
from src.strategies.strategy_base import StrategyBase
from src.strategies.signal_models import StrategyMode, StrategySignal, ExitSignal
from src.market.market_snapshot import MarketSnapshot
from src.execution.position_manager import MultiLegPositionManager
from src.execution.paper_broker import PaperBroker
from src.feed.feed_health import FeedHealthMonitor
from src.portfolio.risk_manager import RiskManager

class StrategyRunner:
    """
    Orchestrates tick processing, signal validation, paper execution, and state telemetry.
    """
    def __init__(
        self,
        registry: StrategyRegistry,
        position_manager: MultiLegPositionManager,
        risk_manager: RiskManager,
        paper_broker: PaperBroker,
        workspace_root: str = ".",
        health_monitor: Optional[FeedHealthMonitor] = None,
    ):
        self.registry = registry
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.paper_broker = paper_broker
        self.workspace_root = workspace_root
        self.health_monitor: Optional[FeedHealthMonitor] = health_monitor
        self.last_rejection_reason: Optional[str] = None

        self.live_state_path = os.path.join(self.workspace_root, "logs", "live_state.json")
        self.runtime_state_path = os.path.join(self.workspace_root, "runtime", "state.json")
        os.makedirs(os.path.dirname(self.live_state_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.runtime_state_path), exist_ok=True)

        self.last_state_flush: float = 0.0

        # Configure state_filepath on position manager if not set
        if not self.position_manager.state_filepath:
            self.position_manager.state_filepath = self.runtime_state_path

        # Attempt recovery of persisted positions prior to evaluations
        if os.path.exists(self.runtime_state_path):
            success, err = self.position_manager.load_state(self.runtime_state_path)
            if not success:
                for strat in self.registry.get_all_strategies():
                    strat.log_error(f"CRITICAL: Failed to recover state from {self.runtime_state_path}: {err}")
            else:
                self.paper_broker.sync_from_positions()
                # Reconcile any staged pending execution logs against strategy CSV audit logs
                reconciled, rec_err = self.reconcile_pending_execution_logs()
                if not reconciled:
                    self.position_manager.recovery_failed = True
                    for strat in self.registry.get_all_strategies():
                        strat.log_error(f"CRITICAL: Execution log reconciliation failed: {rec_err}")

    def _is_snapshot_fresh(self, snapshot: MarketSnapshot) -> Tuple[bool, Optional[str]]:
        """
        Evaluates fail-closed freshness and strategy readiness.
        Guarantees:
        - Explicitly stale snapshot is rejected.
        - Data status STALE/DISCONNECTED/INVALID is rejected.
        - If health monitor is None, fails closed (NO_HEALTH_MONITOR).
        - Injected health monitor readiness is enforced.
        - Health monitor exceptions fail closed.
        - Never assumes freshness solely from snapshot.is_stale defaulting to False.
        """
        # 1. Explicit snapshot staleness flag
        if getattr(snapshot, "is_stale", False) is True:
            return False, "SNAPSHOT_STALE: MarketSnapshot explicitly flagged as stale"

        # 2. Snapshot data status check
        data_status = getattr(snapshot, "data_status", "VALID")
        if data_status in ("STALE", "DISCONNECTED", "INVALID"):
            return False, f"SNAPSHOT_STATUS_{data_status}: MarketSnapshot data_status is {data_status}"

        # 3. Fail-closed if health monitor is missing
        if self.health_monitor is None:
            return False, "NO_HEALTH_MONITOR: Health monitor is required to establish snapshot freshness"

        # 4. Injected FeedHealthMonitor check
        try:
            ready, reason = self.health_monitor.is_strategy_ready()
            if not ready:
                return False, f"FEED_NOT_READY: {reason or 'Feed health monitor reported unready'}"

            if self.health_monitor.is_stale:
                return False, "FEED_STALE: FeedHealthMonitor elapsed time exceeds freshness threshold"
        except Exception as exc:
            # Fail-closed on health monitor inspection exceptions
            return False, f"HEALTH_MONITOR_ERROR: {type(exc).__name__}"

        return True, None

    def process_snapshot(self, snapshot: MarketSnapshot) -> Tuple[bool, Optional[str]]:
        """
        Processes a single market tick across all 25 strategies.
        Guarantees:
        - Freshness/readiness guard runs FIRST (fail-closed boundary).
        - Stale, unverified, unready, or invalid snapshots are rejected immediately:
          they NEVER overwrite position marks, NEVER alter portfolio MTM, and
          NEVER trigger emergency portfolio liquidations.
        - Position mark prices and risk accounting updated only on verified fresh snapshots.
        - Never enables strategies or alters disabled modes.
        
        Returns:
            Tuple of (evaluated: bool, reason: Optional[str])
        """
        # ---------------------------------------------------------------------
        # 1. Freshness / Readiness Guard (Scope B & C: Fail-closed boundary)
        # ---------------------------------------------------------------------
        is_fresh, stale_reason = self._is_snapshot_fresh(snapshot)
        if not is_fresh:
            self.last_rejection_reason = stale_reason
            return False, stale_reason

        self.last_rejection_reason = None

        # ---------------------------------------------------------------------
        # 2. Mark Prices & Risk Evaluation (Only executed for trusted snapshots)
        # ---------------------------------------------------------------------
        price_map = {}
        for opt in snapshot.option_chain.values():
            price_map[opt.symbol] = opt.ltp
        if snapshot.futures:
            price_map[snapshot.futures.symbol] = snapshot.futures.ltp

        self.position_manager.update_marks(price_map, snapshot.timestamp)

        for pos in self.position_manager.get_open_positions():
            # Check for orphan risk
            orphan_warn = pos.check_orphan_risk()
            if orphan_warn:
                strat = self.registry.get_strategy(pos.strategy_id)
                if strat:
                    strat.log_error(f"RISK ALERT: {orphan_warn}")

        portfolio_summary = self.position_manager.get_portfolio_summary()

        # Check emergency portfolio circuit breaker
        is_emergency, emergency_reason = self.risk_manager.check_portfolio_emergency_halt(portfolio_summary)
        if is_emergency:
            # Emergency square-off
            for pos in self.position_manager.get_open_positions():
                strat = self.registry.get_strategy(pos.strategy_id)
                if strat:
                    strat.log_error(f"EMERGENCY SHUTDOWN: {emergency_reason}")
                self.paper_broker.close_entire_position(
                    pos.position_id, price_map, snapshot.timestamp, reason=emergency_reason
                )
            self._save_atomic_state(snapshot, portfolio_summary)
            return False, f"EMERGENCY_HALT: {emergency_reason}"

        # 2. Evaluate all 25 strategies independently
        for strat in self.registry.get_all_strategies():
            open_positions = self.position_manager.get_open_positions(strat.strategy_id)
            active_pos = open_positions[0] if open_positions else None

            # Execute on_tick (protected by StrategyBase try-except)
            entry_sig, exit_sig = strat.on_tick(snapshot, active_pos)

            # 3. Handle Exit Signal
            if exit_sig and active_pos:
                success, updated_pos, closed_legs, err = self.paper_broker.execute_exit_signal(exit_sig, snapshot)
                if success and updated_pos:
                    if updated_pos.status == "CLOSED":
                        strat.log_trade(updated_pos, exit_sig.reason.value, closed_legs=closed_legs)
                        strat.log_event("POSITION_CLOSED", {
                            "position_id": updated_pos.position_id,
                            "reason": exit_sig.reason.value,
                            "realized_pnl": updated_pos.combined_realized_pnl,
                            "charges": updated_pos.total_charges
                        })
                        # Set cooldown if closed via SL
                        if "STOP_LOSS" in exit_sig.reason.value:
                            self.risk_manager.set_cooldown(strat.strategy_id, 300.0)
                    else:
                        strat.log_partial_exit(updated_pos, closed_legs, exit_sig.reason.value)
                        strat.log_event("PARTIAL_LEG_EXIT", {
                            "position_id": updated_pos.position_id,
                            "closed_legs": [l["leg_id"] for l in closed_legs],
                            "reason": exit_sig.reason.value,
                            "realized_pnl": sum(l["realized_pnl"] for l in closed_legs),
                            "charges": sum(l["charges"] for l in closed_legs)
                        })

                    # Atomically persist updated position state including post-logging metadata
                    pending_backup = copy.deepcopy(updated_pos.metadata.get("pending_execution_logs", []))
                    if not self.position_manager.save_state(self.runtime_state_path):
                        # Restore pending logs if state saving fails so events are not lost
                        updated_pos.metadata["pending_execution_logs"] = pending_backup
                        strat.log_error(f"Failed to persist position state after exit logging for {updated_pos.position_id}")

            # 4. Handle Entry Signal
            if entry_sig and not active_pos:
                if strat.mode == StrategyMode.MONITOR_ONLY:
                    strat.log_signal(entry_sig, accepted=False, rejection_reason="MONITOR_ONLY_MODE")
                elif strat.mode == StrategyMode.PAPER:
                    is_valid, reject_reason = self.risk_manager.validate_signal(
                        entry_sig, portfolio_summary, len(open_positions), snapshot.is_stale
                    )
                    if not is_valid:
                        strat.log_signal(entry_sig, accepted=False, rejection_reason=reject_reason)
                    else:
                        success, pos, err = self.paper_broker.execute_entry_signal(entry_sig, snapshot)
                        if success and pos:
                            strat.entries_today_count += 1
                            strat.log_signal(entry_sig, accepted=True)
                            strat.log_event("POSITION_OPENED", {
                                "position_id": pos.position_id,
                                "legs_count": len(pos.legs),
                                "entry_debit_or_credit": pos.entry_debit_or_credit
                            })
                            # Atomically persist newly opened position
                            if not self.position_manager.save_state(self.runtime_state_path):
                                strat.log_error(f"Failed to persist position state after entry for {pos.position_id}")
                        else:
                            strat.log_signal(entry_sig, accepted=False, rejection_reason=err)

        # 5. Persist state snapshot (throttled to 1s)
        now = time.time()
        if now - self.last_state_flush >= 1.0:
            self._save_atomic_state(snapshot, portfolio_summary)
            self.last_state_flush = now

        return True, None

    def _save_atomic_state(self, snapshot: MarketSnapshot, portfolio_summary: Dict[str, Any]):
        """Persists live telemetry snapshot atomically."""
        active_strats = [
            {
                "strategy_id": s.strategy_id,
                "name": s.name,
                "cluster": s.cluster,
                "mode": s.mode.value,
                "entries_today": s.entries_today_count
            }
            for s in self.registry.get_all_strategies()
        ]

        open_pos_list = [
            {
                "position_id": p.position_id,
                "strategy_id": p.strategy_id,
                "entry_time": p.entry_time,
                "unrealized_mtm": p.combined_unrealized_pnl,
                "legs": [
                    {
                        "leg_id": l.leg_id,
                        "symbol": l.symbol,
                        "side": l.side,
                        "qty": l.remaining_quantity,
                        "entry": l.entry_price,
                        "current": l.current_price
                    }
                    for l in p.legs.values() if l.status == "OPEN"
                ]
            }
            for p in self.position_manager.get_open_positions()
        ]

        state = {
            "timestamp": snapshot.timestamp,
            "engine_status": "ONLINE_ACTIVE",
            "spot_price": snapshot.spot_price,
            "futures_price": snapshot.futures.ltp if snapshot.futures else 0.0,
            "india_vix": snapshot.india_vix,
            "portfolio": portfolio_summary,
            "open_positions": open_pos_list,
            "registered_strategies_count": len(self.registry.get_registered_ids()),
            "strategies": active_strats
        }

        # Write to temporary file then rename for atomic safety
        temp_path = self.live_state_path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            if os.path.exists(self.live_state_path):
                os.remove(self.live_state_path)
            os.rename(temp_path, self.live_state_path)
        except Exception:
            pass

        # Persist full active positions state
        if self.position_manager.is_healthy():
            self.position_manager.save_state(self.runtime_state_path)

    def reconcile_pending_execution_logs(self) -> Tuple[bool, Optional[str]]:
        """
        Inspects all recovered positions for staged pending execution logs,
        idempotently reconciles them against strategy CSV audit files,
        and durably updates state.json.
        Fails closed on corrupted or unresolvable pending events.
        """
        all_positions = list(self.position_manager.positions.values())
        any_modified = False

        for pos in all_positions:
            pending_events = pos.metadata.get("pending_execution_logs")
            if not pending_events:
                continue

            strat = self.registry.get_strategy(pos.strategy_id)
            if not strat:
                return False, f"UNKNOWN_STRATEGY: Strategy {pos.strategy_id} for position {pos.position_id} is not registered"

            reconciled_events = []
            for event in pending_events:
                # 1. Validation pass: verify event data integrity
                if not isinstance(event, dict):
                    return False, f"CORRUPT_PENDING_EVENT: Expected dict, got {type(event).__name__} in position {pos.position_id}"

                event_type = event.get("event_type")
                if event_type not in ["LEG_EXIT", "TRADE_SUMMARY"]:
                    return False, f"CORRUPT_PENDING_EVENT: Unknown or missing event_type '{event_type}' in position {pos.position_id}"

                if event_type == "LEG_EXIT":
                    required_fields = ["exec_id", "leg_id", "symbol", "side", "closed_quantity", "entry_price", "exit_price", "exit_time"]
                    missing = [f for f in required_fields if f not in event]
                    if missing:
                        return False, f"CORRUPT_PENDING_EVENT: Missing required fields {missing} in position {pos.position_id}"

                    exec_id = event["exec_id"]
                    logged_keys = pos.metadata.setdefault("_logged_execution_keys", [])
                    logged_closed_qty = pos.metadata.setdefault("_logged_closed_qty", {})

                    # Check idempotence: is this record already in legs.csv or logged_keys?
                    already_in_csv = self._is_leg_execution_in_csv(strat.legs_log_path, event)
                    if not already_in_csv:
                        # Append to legs.csv
                        try:
                            with open(strat.legs_log_path, "a", newline="", encoding="utf-8") as f:
                                writer = csv.writer(f)
                                writer.writerow([
                                    event["leg_id"], pos.position_id, pos.strategy_id,
                                    event["symbol"], event["side"], event["closed_quantity"],
                                    event["entry_price"], event["exit_price"], event.get("entry_time", pos.entry_time),
                                    event["exit_time"], round(float(event.get("realized_pnl", 0.0)), 2),
                                    round(float(event.get("charges", 0.0)), 2)
                                ])
                        except Exception as e:
                            return False, f"CSV_APPEND_FAILED: Failed to append to {strat.legs_log_path}: {e}"

                    if exec_id not in logged_keys:
                        logged_keys.append(exec_id)
                        logged_closed_qty[event["leg_id"]] = logged_closed_qty.get(event["leg_id"], 0) + event["closed_quantity"]

                    reconciled_events.append(event)

                elif event_type == "TRADE_SUMMARY":
                    required_fields = ["trade_id", "entry_time", "close_time", "gross_pnl", "total_charges", "net_pnl", "reason"]
                    missing = [f for f in required_fields if f not in event]
                    if missing:
                        return False, f"CORRUPT_PENDING_EVENT: Missing required trade fields {missing} in position {pos.position_id}"

                    trade_id = event["trade_id"]
                    logged_trades = pos.metadata.setdefault("_logged_trade_ids", [])

                    already_in_csv = self._is_trade_summary_in_csv(strat.trades_log_path, trade_id)
                    if not already_in_csv:
                        try:
                            with open(strat.trades_log_path, "a", newline="", encoding="utf-8") as f:
                                writer = csv.writer(f)
                                writer.writerow([
                                    trade_id, pos.position_id, pos.strategy_id,
                                    event["entry_time"], event["close_time"],
                                    round(float(event["gross_pnl"]), 2), round(float(event["total_charges"]), 2),
                                    round(float(event["net_pnl"]), 2), event["reason"],
                                    event.get("legs_count", len(pos.legs))
                                ])
                        except Exception as e:
                            return False, f"CSV_APPEND_FAILED: Failed to append to {strat.trades_log_path}: {e}"

                    if trade_id not in logged_trades:
                        logged_trades.append(trade_id)

                    reconciled_events.append(event)

            # Remove only the safely reconciled events from pending_execution_logs
            if reconciled_events:
                pos.metadata["pending_execution_logs"] = [
                    e for e in pos.metadata["pending_execution_logs"] if e not in reconciled_events
                ]
                any_modified = True

        if any_modified:
            # Durably commit the cleaned state to disk
            persisted = self.position_manager.save_state(self.runtime_state_path)
            if not persisted:
                return False, f"PERSISTENCE_FAILED_AFTER_RECONCILIATION: Failed to commit cleaned state to {self.runtime_state_path}"

        return True, None

    @staticmethod
    def _is_leg_execution_in_csv(csv_path: str, event: Dict[str, Any]) -> bool:
        """Checks if a leg execution event has already been recorded in legs.csv."""
        if not os.path.exists(csv_path):
            return False
        try:
            with open(csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if len(row) >= 10:
                        # row: leg_id, position_id, strategy_id, symbol, side, quantity, entry_price, exit_price, entry_time, exit_time
                        if (row[0] == str(event["leg_id"]) and
                            row[1] == str(event["position_id"]) and
                            int(float(row[5])) == int(event["closed_quantity"]) and
                            row[9] == str(event["exit_time"])):
                            return True
        except Exception:
            pass
        return False

    @staticmethod
    def _is_trade_summary_in_csv(csv_path: str, trade_id: str) -> bool:
        """Checks if a trade summary has already been recorded in trades.csv."""
        if not os.path.exists(csv_path):
            return False
        try:
            with open(csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if len(row) >= 1 and row[0] == str(trade_id):
                        return True
        except Exception:
            pass
        return False

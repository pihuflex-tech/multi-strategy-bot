"""
strategy_base.py
Abstract Base Class for all 25 Independent Strategies.
Provides isolated execution, individual file logging, independent state management,
and fault-tolerant lifecycle hooks. An exception in one strategy cannot crash the application.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple
import os
import csv
import json
import time
import traceback

from src.market.market_snapshot import MarketSnapshot
from src.strategies.signal_models import (
    StrategyMode,
    SignalDirection,
    StrategySignal,
    ExitSignal,
    ExitReason,
    StopLossPolicy
)
from src.execution.position_manager import MultiLegPosition

class StrategyBase(ABC):
    """
    Abstract Base Class for an independent quantitative trading strategy.
    """
    def __init__(
        self,
        strategy_id: str,
        name: str,
        cluster: str,
        config: Optional[Dict[str, Any]] = None,
        workspace_root: str = "."
    ):
        self.strategy_id = strategy_id
        self.name = name
        self.cluster = cluster
        self.config = config or {}
        self.workspace_root = workspace_root

        # Operating mode: DISABLED by default
        mode_str = self.config.get("mode", "DISABLED").upper()
        self.mode = StrategyMode[mode_str] if mode_str in StrategyMode.__members__ else StrategyMode.DISABLED

        # Strategy-specific timing and risk parameters
        self.entry_time = self.config.get("entry_time", "09:20:00")
        self.exit_time = self.config.get("exit_time", "15:15:00")
        self.max_entries_per_day = int(self.config.get("max_entries_per_day", 1))
        self.entries_today_count = 0
        self.lot_size = int(self.config.get("lot_size", 65))
        self.max_lots = int(self.config.get("max_lots", 1))

        # Internal state
        self.is_active_today = True
        self.last_evaluated_timestamp = ""
        self.active_position_id: Optional[str] = None

        # Setup individual logging directories
        self.log_dir = os.path.join(self.workspace_root, "logs", "strategies", self.strategy_id)
        self._init_logging()

    def _init_logging(self):
        """Initializes strategy-specific log files and headers."""
        os.makedirs(self.log_dir, exist_ok=True)
        self.signals_log_path = os.path.join(self.log_dir, "signals.jsonl")
        self.trades_log_path = os.path.join(self.log_dir, "trades.csv")
        self.legs_log_path = os.path.join(self.log_dir, "legs.csv")
        self.events_log_path = os.path.join(self.log_dir, "events.jsonl")
        self.errors_log_path = os.path.join(self.log_dir, "errors.log")

        # Initialize trades.csv header if not present
        if not os.path.exists(self.trades_log_path):
            with open(self.trades_log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "trade_id", "position_id", "strategy_id", "entry_time", "exit_time",
                    "gross_pnl", "total_charges", "net_pnl", "exit_reason", "legs_count"
                ])

        # Initialize legs.csv header if not present
        if not os.path.exists(self.legs_log_path):
            with open(self.legs_log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "leg_id", "position_id", "strategy_id", "symbol", "side",
                    "quantity", "entry_price", "exit_price", "entry_time", "exit_time",
                    "realized_pnl", "charges"
                ])

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Appends an event to events.jsonl."""
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "strategy_id": self.strategy_id,
            "event_type": event_type,
            "data": data
        }
        try:
            with open(self.events_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def log_signal(self, signal: StrategySignal, accepted: bool, rejection_reason: Optional[str] = None):
        """Appends a signal decision to signals.jsonl."""
        record = {
            "timestamp": signal.timestamp,
            "signal_id": signal.signal_id,
            "strategy_id": self.strategy_id,
            "direction": signal.direction.value,
            "accepted": accepted,
            "rejection_reason": rejection_reason,
            "entry_reason": signal.entry_reason,
            "legs": [
                {
                    "leg_id": l.leg_id,
                    "symbol": l.symbol,
                    "side": l.side,
                    "quantity": l.quantity,
                    "requested_price": l.requested_price
                }
                for l in signal.legs
            ]
        }
        try:
            with open(self.signals_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def log_partial_exit(self, position: MultiLegPosition, closed_legs: List[Dict[str, Any]], reason: str):
        """Logs partial leg exit events to legs.csv and events.jsonl without duplicating."""
        logged_keys = position.metadata.setdefault("_logged_execution_keys", [])
        logged_closed_qty = position.metadata.setdefault("_logged_closed_qty", {})
        try:
            with open(self.legs_log_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for leg in closed_legs:
                    leg_id = leg["leg_id"]
                    closed_qty = leg["closed_quantity"]
                    rem_qty = leg.get("remaining_quantity", 0)
                    exit_time = leg.get("exit_time", "")
                    exit_price = leg.get("exit_price", 0.0)
                    exec_key = f"{position.position_id}_{leg_id}_rem_{rem_qty}_qty_{closed_qty}_exit_{exit_time}_{exit_price}"

                    if exec_key in logged_keys:
                        continue

                    writer.writerow([
                        leg_id, position.position_id, self.strategy_id,
                        leg["symbol"], leg["side"], closed_qty,
                        leg["entry_price"], exit_price, leg["entry_time"],
                        exit_time, round(leg["realized_pnl"], 2), round(leg["charges"], 2)
                    ])
                    logged_keys.append(exec_key)
                    logged_closed_qty[leg_id] = logged_closed_qty.get(leg_id, 0) + closed_qty

            self.log_event("PARTIAL_EXIT", {
                "position_id": position.position_id,
                "reason": reason,
                "closed_legs": [l["leg_id"] for l in closed_legs],
                "realized_pnl": sum(l["realized_pnl"] for l in closed_legs),
                "charges": sum(l["charges"] for l in closed_legs)
            })

            # Prune successfully written events from staged pending logs
            if "pending_execution_logs" in position.metadata:
                position.metadata["pending_execution_logs"] = [
                    e for e in position.metadata["pending_execution_logs"]
                    if e.get("exec_id") not in logged_keys
                ]
        except Exception as e:
            self.log_error(f"Failed to log partial exit: {e}")

    def log_trade(self, position: MultiLegPosition, reason: str, closed_legs: Optional[List[Dict[str, Any]]] = None):
        """Logs completed trade and individual leg exit executions to CSV without duplicating."""
        trade_id = f"TRD_{position.position_id}"
        gross_pnl = position.combined_realized_pnl + position.total_charges
        net_pnl = position.combined_realized_pnl
        logged_trades = position.metadata.setdefault("_logged_trade_ids", [])
        logged_keys = position.metadata.setdefault("_logged_execution_keys", [])
        logged_closed_qty = position.metadata.setdefault("_logged_closed_qty", {})

        try:
            # 1. Log trade-level summary row (only once per trade)
            if trade_id not in logged_trades:
                with open(self.trades_log_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        trade_id, position.position_id, self.strategy_id,
                        position.entry_time, position.close_time,
                        round(gross_pnl, 2), round(position.total_charges, 2),
                        round(net_pnl, 2), reason, len(position.legs)
                    ])
                logged_trades.append(trade_id)

            # 2. Log leg-level execution records
            with open(self.legs_log_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)

                # If closed_legs passed from broker execution, log those specific execution events
                if closed_legs:
                    for leg in closed_legs:
                        leg_id = leg["leg_id"]
                        closed_qty = leg["closed_quantity"]
                        rem_qty = leg.get("remaining_quantity", 0)
                        exit_time = leg.get("exit_time", "")
                        exit_price = leg.get("exit_price", 0.0)
                        exec_key = f"{position.position_id}_{leg_id}_rem_{rem_qty}_qty_{closed_qty}_exit_{exit_time}_{exit_price}"

                        if exec_key in logged_keys:
                            continue

                        writer.writerow([
                            leg_id, position.position_id, self.strategy_id,
                            leg["symbol"], leg["side"], closed_qty,
                            leg["entry_price"], exit_price, leg["entry_time"],
                            exit_time, round(leg["realized_pnl"], 2), round(leg["charges"], 2)
                        ])
                        logged_keys.append(exec_key)
                        logged_closed_qty[leg_id] = logged_closed_qty.get(leg_id, 0) + closed_qty

                # Also verify if any leg has unlogged remaining closed quantity
                for leg in position.legs.values():
                    already_logged = logged_closed_qty.get(leg.leg_id, 0)
                    unlogged_qty = leg.initial_quantity - already_logged
                    if unlogged_qty > 0 and leg.status == "CLOSED":
                        exec_key = f"{position.position_id}_{leg.leg_id}_final_{unlogged_qty}_{leg.exit_time}_{leg.exit_price}"
                        if exec_key not in logged_keys:
                            writer.writerow([
                                leg.leg_id, position.position_id, self.strategy_id,
                                leg.symbol, leg.side, unlogged_qty,
                                leg.entry_price, leg.exit_price, leg.entry_time,
                                leg.exit_time, round(leg.realized_pnl, 2), round(leg.charges, 2)
                            ])
                            logged_keys.append(exec_key)
                            logged_closed_qty[leg.leg_id] = already_logged + unlogged_qty

            # Prune successfully written events from staged pending logs
            if "pending_execution_logs" in position.metadata:
                position.metadata["pending_execution_logs"] = [
                    e for e in position.metadata["pending_execution_logs"]
                    if (e.get("event_type") == "LEG_EXIT" and e.get("exec_id") not in logged_keys)
                    or (e.get("event_type") == "TRADE_SUMMARY" and e.get("trade_id") not in logged_trades)
                ]
        except Exception as e:
            self.log_error(f"Failed to log trade to CSV: {e}")

    def log_error(self, message: str, exc: Optional[Exception] = None):
        """Logs an isolated error to errors.log without crashing application."""
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts}] [{self.strategy_id}] ERROR: {message}\n"
        if exc:
            entry += traceback.format_exc() + "\n"
        try:
            with open(self.errors_log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        open_position: Optional[MultiLegPosition] = None
    ) -> Tuple[Optional[StrategySignal], Optional[ExitSignal]]:
        """
        Primary isolated entry point called on every market snapshot.
        Wrapped in comprehensive try-except to ensure fault isolation.
        Returns: (entry_signal, exit_signal)
        """
        if self.mode == StrategyMode.DISABLED:
            return None, None

        try:
            self.last_evaluated_timestamp = snapshot.timestamp
            cur_time = snapshot.timestamp.split(" ")[-1] if " " in snapshot.timestamp else snapshot.timestamp

            # 1. Evaluate Exit if an open position exists
            if open_position and open_position.status in ["OPEN", "PARTIALLY_CLOSED"]:
                # Force exit at cutoff time
                if cur_time >= self.exit_time:
                    exit_sig = ExitSignal(
                        position_id=open_position.position_id,
                        strategy_id=self.strategy_id,
                        timestamp=snapshot.timestamp,
                        reason=ExitReason.TIME_CUTOFF,
                        details=f"Market session exit time {self.exit_time} reached."
                    )
                    return None, exit_sig

                # Update Trailing Stop Loss
                self.update_trailing_sl(snapshot, open_position)

                # Strategy-specific exit evaluation
                exit_sig = self.evaluate_exit(snapshot, open_position)
                if exit_sig:
                    return None, exit_sig
                return None, None

            # 2. Evaluate Entry if no open position and entry quota remains
            if not open_position:
                if self.entries_today_count >= self.max_entries_per_day:
                    return None, None

                # Check entry timing window
                if cur_time < self.entry_time:
                    return None, None

                entry_sig = self.evaluate_entry(snapshot)
                return entry_sig, None

        except Exception as e:
            self.log_error(f"Unhandled exception in on_tick: {e}", exc=e)
            return None, None

        return None, None

    @abstractmethod
    def evaluate_entry(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        """Strategy-specific entry signal generator."""
        pass

    @abstractmethod
    def evaluate_exit(self, snapshot: MarketSnapshot, position: MultiLegPosition) -> Optional[ExitSignal]:
        """Strategy-specific exit evaluator."""
        pass

    @abstractmethod
    def update_trailing_sl(self, snapshot: MarketSnapshot, position: MultiLegPosition):
        """Strategy-specific trailing stop loss logic."""
        pass

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        open_position: Optional[MultiLegPosition] = None
    ) -> Tuple[Optional[StrategySignal], Optional[ExitSignal]]:
        """Public alias for on_tick."""
        return self.on_tick(snapshot, open_position)

    def get_rulebook(self) -> Dict[str, Any]:
        """Returns the full rulebook for this strategy."""
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "cluster": self.cluster,
            "mode": self.mode.value,
            "entry": {
                "entry_time": self.entry_time,
                "max_entries_per_day": self.max_entries_per_day,
                "lot_size": self.lot_size,
                "max_lots": self.max_lots,
                "conditions": self.config.get("entry_conditions", {})
            },
            "initial_sl": {
                "policy": self.config.get("exit_policy", "INDIVIDUAL_LEG"),
                "sl_pct": self.config.get("sl_pct", 20.0),
                "sl_pts": self.config.get("sl_pts", 0.0)
            },
            "trailing_sl": {
                "activation_pts": self.config.get("trailing_activation_pts", 30.0),
                "trail_by_pts": self.config.get("trailing_step_pts", 15.0)
            },
            "exit": {
                "exit_time": self.exit_time,
                "target_pct": self.config.get("target_pct", 40.0),
                "target_pts": self.config.get("target_pts", 0.0)
            }
        }

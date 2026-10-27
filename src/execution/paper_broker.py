"""
paper_broker.py
Simulated Paper Execution Broker.
Fills simulated orders taking into account bid-ask crossing, slippage, and execution uncertainty.
Under no circumstances submits real broker orders.
"""

from typing import Dict, List, Optional, Any, Tuple, Set
import time
import uuid

from src.strategies.signal_models import StrategySignal, ExitSignal, LegOrder, ExitReason
from src.market.market_snapshot import MarketSnapshot
from src.execution.position_manager import MultiLegPositionManager, MultiLegPosition

class PaperBroker:
    """
    Simulated Paper Broker executing signals against live market snapshots.
    Enforces bid-ask spread crossing, slippage modeling, price validity checks,
    and duplicate exit prevention.
    """
    def __init__(
        self,
        position_manager: MultiLegPositionManager,
        simulated_slippage_points: float = 0.4,
        symmetric_slippage: bool = True
    ):
        self.position_manager = position_manager
        self.slippage_points = simulated_slippage_points
        self.symmetric_slippage = symmetric_slippage
        self.processed_exit_signals: Set[str] = set()
        self.sync_from_positions()

    def sync_from_positions(self):
        """Synchronizes processed exit signal IDs from the position manager and recovered positions."""
        if self.position_manager:
            self.processed_exit_signals.update(self.position_manager.processed_exit_signals)
            for pos in self.position_manager.positions.values():
                for sig in pos.metadata.get("processed_exit_signals", []):
                    self.processed_exit_signals.add(sig)

    def is_signal_processed(self, signal_id: Optional[str], pos: Optional[MultiLegPosition] = None) -> bool:
        """Checks if an exit signal ID has already been executed."""
        if not signal_id:
            return False
        if signal_id in self.processed_exit_signals:
            return True
        if self.position_manager and signal_id in self.position_manager.processed_exit_signals:
            return True
        if pos and signal_id in pos.metadata.get("processed_exit_signals", []):
            return True
        return False

    def execute_entry_signal(
        self,
        signal: StrategySignal,
        snapshot: MarketSnapshot
    ) -> Tuple[bool, Optional[MultiLegPosition], Optional[str]]:
        """
        Executes a strategy entry signal in paper trading mode.
        Simulates individual leg fills with bid-ask spread crossing and slippage.
        """
        if not self.position_manager.is_healthy():
            return False, None, "RECOVERY_FAILED_FAIL_CLOSED"

        if snapshot.is_stale or snapshot.data_status in ["STALE", "DISCONNECTED"]:
            return False, None, "REJECTED_STALE_MARKET_DATA"

        legs_data = []

        for leg in signal.legs:
            # Check market availability with expiry-safe matching
            if leg.option_type in ["CE", "PE"]:
                opt = snapshot.get_option(leg.strike, leg.option_type, expiry=leg.expiry if leg.expiry else None)
                if not opt or opt.ltp <= 0:
                    return False, None, f"REJECTED_ILLIQUID: Missing quote for {leg.strike} {leg.option_type}"
                market_price = opt.ltp
                bid = opt.bid if opt.bid > 0 else market_price - self.slippage_points
                ask = opt.ask if opt.ask > 0 else market_price + self.slippage_points
            else:
                fut = snapshot.futures
                if not fut or fut.ltp <= 0:
                    return False, None, "REJECTED_ILLIQUID: Missing futures quote"
                market_price = fut.ltp
                bid = fut.bid if fut.bid > 0 else market_price - self.slippage_points
                ask = fut.ask if fut.ask > 0 else market_price + self.slippage_points

            # Simulated fill price with spread crossing and slippage
            if leg.side == "BUY":
                fill_price = ask + (self.slippage_points if self.symmetric_slippage else 0.0)
            else:
                fill_price = bid - (self.slippage_points if self.symmetric_slippage else 0.0)

            fill_price = round(max(0.05, fill_price), 2)
            leg.assumed_fill_price = fill_price
            leg.status = "FILLED"

            legs_data.append({
                "leg_id": leg.leg_id,
                "symbol": leg.symbol,
                "strike": leg.strike,
                "option_type": leg.option_type,
                "expiry": leg.expiry,
                "side": leg.side,
                "quantity": leg.quantity,
                "entry_price": fill_price
            })

        # Create multi-leg position
        pos = self.position_manager.create_position(
            strategy_id=signal.strategy_id,
            exit_policy=signal.exit_policy,
            legs_data=legs_data,
            timestamp=snapshot.timestamp,
            initial_sl=signal.initial_sl_value,
            trailing_sl=signal.trailing_sl_value,
            target=signal.target_value,
            metadata=signal.metadata
        )

        return True, pos, None

    def execute_exit_signal(
        self,
        exit_sig: ExitSignal,
        snapshot: MarketSnapshot
    ) -> Tuple[bool, Optional[MultiLegPosition], List[Dict[str, Any]], Optional[str]]:
        """
        Executes a position exit signal in paper trading mode.
        Supports closing selected legs, partial quantities, or full structures.
        Rejects stale/invalid prices and duplicate exit executions.
        Returns: (success: bool, updated_pos: Optional[MultiLegPosition], closed_legs: List[Dict], error: Optional[str])
        """
        # 0. Fail-closed recovery check
        if not self.position_manager.is_healthy():
            return False, None, [], "RECOVERY_FAILED_FAIL_CLOSED"

        # 1. Duplicate exit signal prevention
        if exit_sig.signal_id and self.is_signal_processed(exit_sig.signal_id):
            return False, None, [], f"DUPLICATE_EXIT_SIGNAL: {exit_sig.signal_id} has already executed"

        # 2. Check position existence and status
        pos = self.position_manager.positions.get(exit_sig.position_id)
        if not pos:
            return False, None, [], f"POSITION_NOT_FOUND: {exit_sig.position_id}"
        if pos.status == "CLOSED":
            return False, None, [], f"POSITION_ALREADY_CLOSED: {exit_sig.position_id}"

        # Check position-specific exit signals
        if exit_sig.signal_id and self.is_signal_processed(exit_sig.signal_id, pos):
            return False, None, [], f"DUPLICATE_EXIT_SIGNAL: {exit_sig.signal_id} has already executed"

        # 3. Market data freshness check
        if snapshot.is_stale or snapshot.data_status in ["STALE", "DISCONNECTED"]:
            return False, None, [], "REJECTED_STALE_MARKET_DATA"

        # 4. Identify target legs to close
        target_leg_ids = exit_sig.legs_to_close if exit_sig.legs_to_close else [
            lid for lid, l in pos.legs.items() if l.status == "OPEN"
        ]

        if not target_leg_ids:
            return False, None, [], "NO_OPEN_LEGS_TO_CLOSE"

        # Check if all target legs are already closed
        for lid in target_leg_ids:
            if lid in pos.legs and pos.legs[lid].status == "CLOSED":
                return False, None, [], f"LEG_ALREADY_CLOSED: Leg {lid} is already closed"

        # 5. Build and validate exit prices for target legs
        exit_prices = {}
        for leg_id in target_leg_ids:
            if leg_id not in pos.legs:
                return False, None, [], f"INVALID_LEG_ID: {leg_id} not in position"
            leg = pos.legs[leg_id]
            if leg.status == "OPEN":
                if leg.option_type in ["CE", "PE"]:
                    opt = snapshot.get_option(leg.strike, leg.option_type, expiry=leg.expiry if leg.expiry else None)
                    if not opt or opt.ltp <= 0:
                        return False, None, [], f"REJECTED_INVALID_PRICE: Missing quote for {leg.symbol}"
                    if opt.bid < 0 or opt.ask < 0:
                        return False, None, [], f"REJECTED_INVALID_PRICE: Negative bid/ask for {leg.symbol}"
                    mkt_p = opt.ltp
                    bid = opt.bid if opt.bid > 0 else mkt_p - self.slippage_points
                    ask = opt.ask if opt.ask > 0 else mkt_p + self.slippage_points
                else:
                    fut = snapshot.futures
                    if not fut or fut.ltp <= 0:
                        return False, None, [], f"REJECTED_INVALID_PRICE: Missing futures quote for {leg.symbol}"
                    mkt_p = fut.ltp
                    bid = fut.bid if fut.bid > 0 else mkt_p - self.slippage_points
                    ask = fut.ask if fut.ask > 0 else mkt_p + self.slippage_points

                # Closing trade: BUY exits at bid; SELL exits at ask
                if leg.side == "BUY":
                    exit_p = bid - self.slippage_points
                else:
                    exit_p = ask + self.slippage_points

                exit_prices[leg.symbol] = round(max(0.05, exit_p), 2)

        # 6. Snapshot position before any exit mutation for atomic in-memory rollback
        pos_snapshot = pos.snapshot()

        # Execute exit via MultiLegPositionManager
        success, updated_pos, closed_records, err = self.position_manager.close_specific_legs(
            position_id=pos.position_id,
            legs_to_close=target_leg_ids,
            exit_prices=exit_prices,
            timestamp=snapshot.timestamp,
            reason=exit_sig.reason,
            leg_quantities=exit_sig.leg_quantities
        )

        if not success:
            return False, None, [], err

        # Mark signal processed in-memory
        if exit_sig.signal_id:
            if updated_pos:
                sig_list = updated_pos.metadata.setdefault("processed_exit_signals", [])
                if exit_sig.signal_id not in sig_list:
                    sig_list.append(exit_sig.signal_id)
            self.processed_exit_signals.add(exit_sig.signal_id)
            if self.position_manager:
                self.position_manager.record_processed_signal(exit_sig.signal_id)

        # Stage durable pending execution logs in metadata prior to persistence
        if updated_pos and closed_records:
            pending_logs = updated_pos.metadata.setdefault("pending_execution_logs", [])
            reason_str = exit_sig.reason.value if hasattr(exit_sig.reason, "value") else str(exit_sig.reason)
            for rec in closed_records:
                leg_id = rec["leg_id"]
                closed_qty = rec["closed_quantity"]
                rem_qty = rec.get("remaining_quantity", 0)
                exit_time = rec.get("exit_time", "")
                exit_price = rec.get("exit_price", 0.0)
                exec_key = f"{updated_pos.position_id}_{leg_id}_rem_{rem_qty}_qty_{closed_qty}_exit_{exit_time}_{exit_price}"
                if not any(e.get("exec_id") == exec_key for e in pending_logs):
                    pending_logs.append({
                        "event_type": "LEG_EXIT",
                        "exec_id": exec_key,
                        "strategy_id": updated_pos.strategy_id,
                        "position_id": updated_pos.position_id,
                        "leg_id": leg_id,
                        "symbol": rec["symbol"],
                        "side": rec["side"],
                        "closed_quantity": closed_qty,
                        "remaining_quantity": rem_qty,
                        "entry_price": rec["entry_price"],
                        "exit_price": exit_price,
                        "entry_time": rec["entry_time"],
                        "exit_time": exit_time,
                        "realized_pnl": rec["realized_pnl"],
                        "charges": rec["charges"],
                        "reason": reason_str
                    })

            if updated_pos.status == "CLOSED":
                trade_id = f"TRD_{updated_pos.position_id}"
                gross_pnl = round(updated_pos.combined_realized_pnl + updated_pos.total_charges, 2)
                if not any(e.get("exec_id") == trade_id for e in pending_logs):
                    pending_logs.append({
                        "event_type": "TRADE_SUMMARY",
                        "exec_id": trade_id,
                        "strategy_id": updated_pos.strategy_id,
                        "position_id": updated_pos.position_id,
                        "trade_id": trade_id,
                        "entry_time": updated_pos.entry_time,
                        "close_time": updated_pos.close_time,
                        "gross_pnl": gross_pnl,
                        "total_charges": round(updated_pos.total_charges, 2),
                        "net_pnl": round(updated_pos.combined_realized_pnl, 2),
                        "reason": reason_str,
                        "legs_count": len(updated_pos.legs)
                    })

        # 7. If durable state filepath is configured, enforce persistence before claiming success
        if self.position_manager and self.position_manager.state_filepath:
            persisted = False
            try:
                persisted = self.position_manager.save_state()
            except Exception:
                persisted = False

            if not persisted:
                # Rollback signal processing if persistence failed
                if exit_sig.signal_id:
                    self.processed_exit_signals.discard(exit_sig.signal_id)
                    if self.position_manager:
                        self.position_manager.processed_exit_signals.discard(exit_sig.signal_id)
                    if updated_pos and "processed_exit_signals" in updated_pos.metadata:
                        try:
                            updated_pos.metadata["processed_exit_signals"].remove(exit_sig.signal_id)
                        except (ValueError, AttributeError):
                            pass

                # In-place restoration of mutated position to exact pre-exit state
                try:
                    pos.restore_from_snapshot(pos_snapshot)
                except Exception as rollback_exc:
                    if self.position_manager:
                        self.position_manager.recovery_failed = True
                    return False, None, [], f"ROLLBACK_FAILED_FAIL_CLOSED: {rollback_exc}"

                return False, None, [], "PERSISTENCE_FAILED_EXIT_ROLLED_BACK"

        return True, updated_pos, closed_records, None

    def close_entire_position(
        self,
        position_id: str,
        exit_prices: Dict[str, float],
        timestamp: str,
        reason: Any
    ) -> Optional[MultiLegPosition]:
        """
        Closes entire composite position across all open legs and persists state if configured.
        """
        if not self.position_manager:
            return None
        pos = self.position_manager.positions.get(position_id)
        if not pos or pos.status == "CLOSED":
            return None
        pos_snapshot = pos.snapshot()
        exit_reason = reason if isinstance(reason, ExitReason) else ExitReason.MANUAL
        closed_pos = self.position_manager.close_entire_position(position_id, exit_prices, timestamp, exit_reason)
        if closed_pos and self.position_manager.state_filepath:
            persisted = False
            try:
                persisted = self.position_manager.save_state()
            except Exception:
                persisted = False
            if not persisted:
                try:
                    pos.restore_from_snapshot(pos_snapshot)
                except Exception:
                    self.position_manager.recovery_failed = True
                return None
        return closed_pos


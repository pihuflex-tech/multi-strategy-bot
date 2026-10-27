"""
position_manager.py
Advanced multi-leg position tracker and lifecycle manager for all 25 strategies.
Tracks leg-level and combined-position metrics, enforces orphan leg safety,
handles partial and selected leg exits cleanly without double-counting,
and provides atomic state persistence and fail-closed restart recovery.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Set
import os
import json
import time
import uuid
import copy
import threading
import logging

logger = logging.getLogger(__name__)

from src.accounting.pnl_calculator import calculate_leg_pnl, calculate_combined_position_pnl
from src.accounting.charges_calculator import ChargesCalculator, StatutoryCharges
from src.strategies.signal_models import StopLossPolicy, ExitReason

@dataclass
class LegPosition:
    """Individual leg within a position."""
    leg_id: str
    symbol: str
    strike: float
    option_type: str                  # "CE", "PE", "FUT"
    expiry: str
    side: str                         # "BUY", "SELL"
    initial_quantity: int
    remaining_quantity: int
    entry_price: float
    entry_time: str
    current_price: float
    current_time: str = ""
    exit_price: float = 0.0
    exit_time: str = ""
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    charges: float = 0.0
    status: str = "OPEN"              # "OPEN", "CLOSED"

    def update_mark_price(self, new_price: float, timestamp: str):
        if self.status == "OPEN" and new_price > 0:
            self.current_price = new_price
            self.current_time = timestamp
            self.unrealized_pnl = calculate_leg_pnl(
                self.entry_price, self.current_price, self.remaining_quantity, self.side
            )

    def close(self, exit_price: float, timestamp: str, charges: float = 0.0):
        if self.status == "OPEN":
            self.exit_price = exit_price
            self.exit_time = timestamp
            self.charges += charges
            closed_pnl = calculate_leg_pnl(
                self.entry_price, self.exit_price, self.remaining_quantity, self.side
            ) - charges
            self.realized_pnl += closed_pnl
            self.unrealized_pnl = 0.0
            self.remaining_quantity = 0
            self.status = "CLOSED"

    def partial_close(self, qty: int, exit_price: float, timestamp: str, charges: float = 0.0) -> float:
        """Closes a partial quantity, locking in realized P&L while preserving remaining cost basis."""
        if qty <= 0 or qty > self.remaining_quantity or self.status != "OPEN":
            return 0.0
        closed_pnl = calculate_leg_pnl(self.entry_price, exit_price, qty, self.side) - charges
        self.realized_pnl += closed_pnl
        self.remaining_quantity -= qty
        self.charges += charges
        if self.remaining_quantity == 0:
            self.status = "CLOSED"
            self.exit_price = exit_price
            self.exit_time = timestamp
            self.unrealized_pnl = 0.0
        else:
            self.unrealized_pnl = calculate_leg_pnl(
                self.entry_price, self.current_price, self.remaining_quantity, self.side
            )
        return closed_pnl

    def to_dict(self) -> Dict[str, Any]:
        """Serializes leg state to dictionary."""
        return {
            "leg_id": self.leg_id,
            "symbol": self.symbol,
            "strike": self.strike,
            "option_type": self.option_type,
            "expiry": self.expiry,
            "side": self.side,
            "initial_quantity": self.initial_quantity,
            "remaining_quantity": self.remaining_quantity,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
            "current_price": self.current_price,
            "current_time": self.current_time,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time,
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "charges": round(self.charges, 2),
            "status": self.status
        }

    def snapshot(self) -> Dict[str, Any]:
        """Creates an in-memory snapshot of mutable leg fields for atomic rollback."""
        return {
            "remaining_quantity": self.remaining_quantity,
            "current_price": self.current_price,
            "current_time": self.current_time,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "charges": self.charges,
            "status": self.status
        }

    def restore_from_snapshot(self, snap: Dict[str, Any]):
        """Restores mutable leg fields in-place on the exact same instance."""
        self.remaining_quantity = snap["remaining_quantity"]
        self.current_price = snap["current_price"]
        self.current_time = snap["current_time"]
        self.exit_price = snap["exit_price"]
        self.exit_time = snap["exit_time"]
        self.realized_pnl = snap["realized_pnl"]
        self.unrealized_pnl = snap["unrealized_pnl"]
        self.charges = snap["charges"]
        self.status = snap["status"]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LegPosition":
        """Reconstructs leg position from serialized dictionary with validation."""
        required = ["leg_id", "symbol", "side", "initial_quantity", "remaining_quantity", "entry_price", "status"]
        for r in required:
            if r not in d:
                raise ValueError(f"Missing required LegPosition field: {r}")
        return cls(
            leg_id=str(d["leg_id"]),
            symbol=str(d["symbol"]),
            strike=float(d.get("strike", 0.0)),
            option_type=str(d.get("option_type", "CE")),
            expiry=str(d.get("expiry", "")),
            side=str(d["side"]),
            initial_quantity=int(d["initial_quantity"]),
            remaining_quantity=int(d["remaining_quantity"]),
            entry_price=float(d["entry_price"]),
            entry_time=str(d.get("entry_time", "")),
            current_price=float(d.get("current_price", d["entry_price"])),
            current_time=str(d.get("current_time", "")),
            exit_price=float(d.get("exit_price", 0.0)),
            exit_time=str(d.get("exit_time", "")),
            realized_pnl=float(d.get("realized_pnl", 0.0)),
            unrealized_pnl=float(d.get("unrealized_pnl", 0.0)),
            charges=float(d.get("charges", 0.0)),
            status=str(d["status"])
        )


@dataclass
class MultiLegPosition:
    """Composite position container managing 1 to 4 legs."""
    position_id: str
    strategy_id: str
    exit_policy: StopLossPolicy
    legs: Dict[str, LegPosition] = field(default_factory=dict)
    entry_time: str = ""
    close_time: str = ""
    status: str = "OPEN"              # "OPEN", "PARTIALLY_CLOSED", "CLOSED"
    initial_sl_value: float = 0.0
    trailing_sl_value: float = 0.0
    target_value: float = 0.0
    peak_pnl: float = 0.0             # High-water mark for trailing stop
    entry_debit_or_credit: float = 0.0
    combined_unrealized_pnl: float = 0.0
    combined_realized_pnl: float = 0.0
    total_charges: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_fully_closed(self) -> bool:
        return all(leg.status == "CLOSED" for leg in self.legs.values())

    def update_marks(self, price_map: Dict[str, float], timestamp: str):
        """Updates mark prices for all active legs and recomputes combined P&L."""
        if self.status == "CLOSED":
            return

        for leg_id, leg in self.legs.items():
            if leg.symbol in price_map and price_map[leg.symbol] > 0:
                leg.update_mark_price(price_map[leg.symbol], timestamp)

        # Recompute combined unrealized MTM
        leg_dicts = [
            {
                "side": l.side,
                "entry_price": l.entry_price,
                "current_price": l.current_price,
                "quantity": l.remaining_quantity
            }
            for l in self.legs.values() if l.status == "OPEN"
        ]
        res = calculate_combined_position_pnl(leg_dicts)
        self.combined_unrealized_pnl = res["gross_unrealized_pnl"]

        # Track high-water mark for trailing stop
        current_total = self.combined_unrealized_pnl + self.combined_realized_pnl
        if current_total > self.peak_pnl:
            self.peak_pnl = current_total

    def check_orphan_risk(self) -> Optional[str]:
        """
        Safety check: Detects if any long wing/protection leg was closed leaving
        a short option leg naked. Returns descriptive warning if orphaned.
        """
        short_legs = [l for l in self.legs.values() if l.status == "OPEN" and l.side == "SELL"]
        long_legs = [l for l in self.legs.values() if l.status == "OPEN" and l.side == "BUY"]

        # For multi-leg structures with short legs, having open shorts with 0 open longs is high risk
        if len(short_legs) > 0 and len(long_legs) == 0 and len(self.legs) > 1:
            return f"ORPHAN_SHORT_EXPOSURE: {len(short_legs)} short legs open with zero wing hedges!"
        return None

    def recalculate_risk(self) -> Optional[str]:
        """
        Recalculates risk after any leg state change.
        Detects orphaned short legs and updates hedging status in metadata.
        Never silently claims that a position is hedged when protective wings are closed.
        """
        orphan = self.check_orphan_risk()
        if orphan:
            self.metadata["orphan_risk"] = orphan
            self.metadata["is_hedged"] = False
        else:
            self.metadata["orphan_risk"] = None
            short_legs = [l for l in self.legs.values() if l.status == "OPEN" and l.side == "SELL"]
            long_legs = [l for l in self.legs.values() if l.status == "OPEN" and l.side == "BUY"]
            if len(short_legs) > 0 and len(long_legs) == 0:
                self.metadata["is_hedged"] = False
            elif len(short_legs) > 0 and len(long_legs) > 0:
                self.metadata["is_hedged"] = True
            else:
                self.metadata["is_hedged"] = False
        return orphan

    def snapshot(self) -> Dict[str, Any]:
        """Creates an in-memory snapshot of composite position fields for atomic rollback."""
        return {
            "status": self.status,
            "close_time": self.close_time,
            "peak_pnl": self.peak_pnl,
            "entry_debit_or_credit": self.entry_debit_or_credit,
            "combined_unrealized_pnl": self.combined_unrealized_pnl,
            "combined_realized_pnl": self.combined_realized_pnl,
            "total_charges": self.total_charges,
            "initial_sl_value": self.initial_sl_value,
            "trailing_sl_value": self.trailing_sl_value,
            "target_value": self.target_value,
            "metadata": copy.deepcopy(self.metadata),
            "legs": {lid: leg.snapshot() for lid, leg in self.legs.items()}
        }

    def restore_from_snapshot(self, snap: Dict[str, Any]):
        """Restores composite position fields in-place on the exact same instance."""
        self.status = snap["status"]
        self.close_time = snap["close_time"]
        self.peak_pnl = snap["peak_pnl"]
        self.entry_debit_or_credit = snap.get("entry_debit_or_credit", self.entry_debit_or_credit)
        self.combined_unrealized_pnl = snap["combined_unrealized_pnl"]
        self.combined_realized_pnl = snap["combined_realized_pnl"]
        self.total_charges = snap["total_charges"]
        self.initial_sl_value = snap.get("initial_sl_value", self.initial_sl_value)
        self.trailing_sl_value = snap.get("trailing_sl_value", self.trailing_sl_value)
        self.target_value = snap.get("target_value", self.target_value)
        self.metadata = copy.deepcopy(snap["metadata"])
        for lid, lsnap in snap["legs"].items():
            if lid in self.legs:
                self.legs[lid].restore_from_snapshot(lsnap)
        self.recalculate_risk()

    def to_dict(self) -> Dict[str, Any]:
        """Serializes multi-leg position to dictionary."""
        clean_metadata = {}
        for k, v in self.metadata.items():
            if isinstance(v, set):
                clean_metadata[k] = list(v)
            else:
                clean_metadata[k] = v

        return {
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "exit_policy": self.exit_policy.value if hasattr(self.exit_policy, "value") else str(self.exit_policy),
            "entry_time": self.entry_time,
            "close_time": self.close_time,
            "status": self.status,
            "initial_sl_value": self.initial_sl_value,
            "trailing_sl_value": self.trailing_sl_value,
            "target_value": self.target_value,
            "peak_pnl": round(self.peak_pnl, 2),
            "entry_debit_or_credit": round(self.entry_debit_or_credit, 2),
            "combined_unrealized_pnl": round(self.combined_unrealized_pnl, 2),
            "combined_realized_pnl": round(self.combined_realized_pnl, 2),
            "total_charges": round(self.total_charges, 2),
            "metadata": clean_metadata,
            "legs": {lid: leg.to_dict() for lid, leg in self.legs.items()}
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MultiLegPosition":
        """Reconstructs MultiLegPosition from dictionary with strict schema validation."""
        required = ["position_id", "strategy_id", "status", "legs"]
        for r in required:
            if r not in d:
                raise ValueError(f"Missing required MultiLegPosition field: {r}")

        raw_policy = d.get("exit_policy", "COMBINED_POSITION")
        try:
            policy = StopLossPolicy(raw_policy)
        except Exception:
            policy = StopLossPolicy.COMBINED_POSITION

        legs = {}
        for lid, ldata in d.get("legs", {}).items():
            legs[lid] = LegPosition.from_dict(ldata)

        pos = cls(
            position_id=str(d["position_id"]),
            strategy_id=str(d["strategy_id"]),
            exit_policy=policy,
            entry_time=str(d.get("entry_time", "")),
            close_time=str(d.get("close_time", "")),
            status=str(d["status"]),
            initial_sl_value=float(d.get("initial_sl_value", 0.0)),
            trailing_sl_value=float(d.get("trailing_sl_value", 0.0)),
            target_value=float(d.get("target_value", 0.0)),
            peak_pnl=float(d.get("peak_pnl", 0.0)),
            entry_debit_or_credit=float(d.get("entry_debit_or_credit", 0.0)),
            combined_unrealized_pnl=float(d.get("combined_unrealized_pnl", 0.0)),
            combined_realized_pnl=float(d.get("combined_realized_pnl", 0.0)),
            total_charges=float(d.get("total_charges", 0.0)),
            metadata=d.get("metadata", {}),
            legs=legs
        )
        pos.recalculate_risk()
        return pos


class MultiLegPositionManager:
    """
    Central repository managing open and historical multi-leg positions across all 25 strategies.
    Provides atomic persistence and fail-closed restart recovery.
    """
    SCHEMA_VERSION = "2.1.0"

    def __init__(
        self,
        charges_calc: Optional[ChargesCalculator] = None,
        state_filepath: Optional[str] = None
    ):
        self._lock = threading.RLock()
        self.positions: Dict[str, MultiLegPosition] = {}
        self.charges_calc = charges_calc or ChargesCalculator()
        self.state_filepath = state_filepath
        self.recovery_failed: bool = False
        self.recovery_error: Optional[str] = None
        self.is_recovered: bool = False
        self.processed_exit_signals: Set[str] = set()
        self.last_save_error: Optional[str] = None

    def record_processed_signal(self, signal_id: str):
        """Records a processed exit signal ID and prunes stale records safely."""
        if not signal_id:
            return
        with self._lock:
            self.processed_exit_signals.add(signal_id)
            self._prune_processed_signals()

    def _prune_processed_signals(self, max_retained: int = 1000):
        """
        Prunes historical exit signal IDs to prevent unbounded growth.
        Guarantees that all signal IDs for active/open positions are NEVER pruned.
        """
        with self._lock:
            active_signals: Set[str] = set()
            for pos in self.get_open_positions():
                for sig in pos.metadata.get("processed_exit_signals", []):
                    active_signals.add(sig)

            if len(self.processed_exit_signals) > max_retained + len(active_signals):
                excess = len(self.processed_exit_signals) - (max_retained + len(active_signals))
                to_remove = set()
                for sig in self.processed_exit_signals:
                    if sig not in active_signals:
                        to_remove.add(sig)
                        if len(to_remove) >= excess:
                            break
                self.processed_exit_signals -= to_remove

    def is_healthy(self) -> bool:
        """Returns True if the position manager is operational and has not suffered recovery failure."""
        with self._lock:
            return not self.recovery_failed

    def create_position(
        self,
        strategy_id: str,
        exit_policy: StopLossPolicy,
        legs_data: List[Dict[str, Any]],
        timestamp: str,
        initial_sl: float = 0.0,
        trailing_sl: float = 0.0,
        target: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> MultiLegPosition:
        """Instantiates a new MultiLegPosition with leg records."""
        with self._lock:
            pos_id = f"POS_{strategy_id}_{str(uuid.uuid4())[:8]}"
            pos = MultiLegPosition(
                position_id=pos_id,
                strategy_id=strategy_id,
                exit_policy=exit_policy,
                entry_time=timestamp,
                initial_sl_value=initial_sl,
                trailing_sl_value=trailing_sl,
                target_value=target,
                metadata=metadata or {}
            )

            for ldata in legs_data:
                leg_id = ldata.get("leg_id", str(uuid.uuid4())[:6])
                leg = LegPosition(
                    leg_id=leg_id,
                    symbol=ldata["symbol"],
                    strike=float(ldata.get("strike", 0.0)),
                    option_type=ldata.get("option_type", "CE"),
                    expiry=ldata.get("expiry", ""),
                    side=ldata["side"].upper(),
                    initial_quantity=int(ldata["quantity"]),
                    remaining_quantity=int(ldata["quantity"]),
                    entry_price=float(ldata["entry_price"]),
                    entry_time=timestamp,
                    current_price=float(ldata["entry_price"]),
                    current_time=timestamp
                )
                pos.legs[leg_id] = leg

            # Initial debit/credit calculation
            leg_dicts = [
                {"side": l.side, "entry_price": l.entry_price, "current_price": l.entry_price, "quantity": l.initial_quantity}
                for l in pos.legs.values()
            ]
            calc = calculate_combined_position_pnl(leg_dicts)
            pos.entry_debit_or_credit = calc["net_debit_or_credit"]
            pos.recalculate_risk()

            self.positions[pos_id] = pos
            return pos

    def close_specific_legs(
        self,
        position_id: str,
        legs_to_close: List[str],
        exit_prices: Dict[str, float],
        timestamp: str,
        reason: ExitReason,
        leg_quantities: Optional[Dict[str, int]] = None
    ) -> Tuple[bool, Optional[MultiLegPosition], List[Dict[str, Any]], Optional[str]]:
        """
        Closes specified legs or partial quantities within a position.
        Validates leg ownership, quantities, price validity, and prevents duplicate exits.
        Recalculates risk and flags orphan short risk if protective wings are closed.
        """
        with self._lock:
            pos = self.positions.get(position_id)
            if not pos:
                return False, None, [], f"POSITION_NOT_FOUND: {position_id}"
            if pos.status == "CLOSED":
                return False, None, [], f"POSITION_ALREADY_CLOSED: {position_id}"
            if not legs_to_close:
                return False, None, [], "NO_LEGS_SPECIFIED"

            # 1. Validation pass: verify all requested legs belong to position and have valid quantity and price
            leg_quantities = leg_quantities or {}
            for leg_id in legs_to_close:
                if leg_id not in pos.legs:
                    return False, None, [], f"INVALID_LEG_ID: {leg_id} does not belong to position {position_id}"
                leg = pos.legs[leg_id]
                if leg.status == "CLOSED" or leg.remaining_quantity <= 0:
                    return False, None, [], f"LEG_ALREADY_CLOSED: Leg {leg_id} is already closed"

                requested_qty = leg_quantities.get(leg_id, leg.remaining_quantity)
                if requested_qty <= 0 or requested_qty > leg.remaining_quantity:
                    return False, None, [], (
                        f"INVALID_QUANTITY: Requested {requested_qty} for leg {leg_id}, "
                        f"remaining open is {leg.remaining_quantity}"
                    )

                exit_p = exit_prices.get(leg.symbol, 0.0)
                if exit_p <= 0.0:
                    return False, None, [], f"INVALID_PRICE: Exit price for {leg.symbol} must be > 0, got {exit_p}"

            # 2. Execution pass: close or partial close requested legs
            closed_records = []
            for leg_id in legs_to_close:
                leg = pos.legs[leg_id]
                requested_qty = leg_quantities.get(leg_id, leg.remaining_quantity)
                exit_p = exit_prices[leg.symbol]
                inst_type = "OPTIONS" if leg.option_type in ["CE", "PE"] else "FUTURES"

                charges = self.charges_calc.calculate_trade_charges(
                    inst_type, leg.entry_price, exit_p, requested_qty, leg.side
                )
                total_charge = charges.total_charges

                if requested_qty == leg.remaining_quantity:
                    # Full leg close
                    prev_realized = leg.realized_pnl
                    leg.close(exit_p, timestamp, total_charge)
                    pnl_delta = leg.realized_pnl - prev_realized
                else:
                    # Partial leg close
                    pnl_delta = leg.partial_close(requested_qty, exit_p, timestamp, total_charge)

                pos.combined_realized_pnl += pnl_delta
                pos.total_charges += total_charge

                closed_records.append({
                    "leg_id": leg.leg_id,
                    "symbol": leg.symbol,
                    "side": leg.side,
                    "closed_quantity": requested_qty,
                    "remaining_quantity": leg.remaining_quantity,
                    "entry_price": leg.entry_price,
                    "exit_price": exit_p,
                    "entry_time": leg.entry_time,
                    "exit_time": timestamp,
                    "realized_pnl": pnl_delta,
                    "charges": total_charge
                })

            # 3. Update structure state and remaining risk
            if pos.is_fully_closed():
                pos.status = "CLOSED"
                pos.close_time = timestamp
                pos.combined_unrealized_pnl = 0.0
                pos.metadata["exit_reason"] = reason.value if hasattr(reason, "value") else str(reason)
            else:
                pos.status = "PARTIALLY_CLOSED"
                # Recompute marks for remaining open legs
                pos.update_marks(exit_prices, timestamp)

            # Recalculate risk on remaining structure
            pos.recalculate_risk()

            return True, pos, closed_records, None

    def close_entire_position(
        self,
        position_id: str,
        exit_prices: Dict[str, float],
        timestamp: str,
        reason: ExitReason
    ) -> Optional[MultiLegPosition]:
        """Closes all open legs in a position and records realized P&L and charges."""
        with self._lock:
            pos = self.positions.get(position_id)
            if not pos or pos.status == "CLOSED":
                return None

            # Price validation: all open legs must have valid positive prices
            for leg in pos.legs.values():
                if leg.status == "OPEN":
                    exit_p = exit_prices.get(leg.symbol, leg.current_price)
                    if exit_p <= 0.0:
                        return None

            total_pos_charges = 0.0
            total_realized_delta = 0.0
            closed_records = []

            for leg_id, leg in pos.legs.items():
                if leg.status == "OPEN":
                    exit_p = exit_prices.get(leg.symbol, leg.current_price)
                    inst_type = "OPTIONS" if leg.option_type in ["CE", "PE"] else "FUTURES"
                    charges = self.charges_calc.calculate_trade_charges(
                        inst_type, leg.entry_price, exit_p, leg.remaining_quantity, leg.side
                    )
                    prev_realized = leg.realized_pnl
                    closed_qty = leg.remaining_quantity
                    leg.close(exit_p, timestamp, charges.total_charges)
                    total_pos_charges += charges.total_charges
                    pnl_delta = leg.realized_pnl - prev_realized
                    total_realized_delta += pnl_delta
                    closed_records.append({
                        "leg_id": leg.leg_id,
                        "symbol": leg.symbol,
                        "side": leg.side,
                        "closed_quantity": closed_qty,
                        "entry_price": leg.entry_price,
                        "exit_price": exit_p,
                        "entry_time": leg.entry_time,
                        "exit_time": timestamp,
                        "realized_pnl": pnl_delta,
                        "charges": charges.total_charges
                    })

            pos.combined_realized_pnl += total_realized_delta
            pos.combined_unrealized_pnl = 0.0
            pos.total_charges += total_pos_charges
            pos.close_time = timestamp
            pos.status = "CLOSED"
            reason_str = reason.value if hasattr(reason, "value") else str(reason)
            pos.metadata["exit_reason"] = reason_str

            # Stage durable pending execution logs in metadata prior to persistence
            pending_logs = pos.metadata.setdefault("pending_execution_logs", [])
            for rec in closed_records:
                exec_key = f"{pos.position_id}_{rec['leg_id']}_rem_0_qty_{rec['closed_quantity']}_exit_{rec['exit_time']}_{rec['exit_price']}"
                if not any(e.get("exec_id") == exec_key for e in pending_logs):
                    pending_logs.append({
                        "event_type": "LEG_EXIT",
                        "exec_id": exec_key,
                        "strategy_id": pos.strategy_id,
                        "position_id": pos.position_id,
                        "leg_id": rec["leg_id"],
                        "symbol": rec["symbol"],
                        "side": rec["side"],
                        "closed_quantity": rec["closed_quantity"],
                        "remaining_quantity": 0,
                        "entry_price": rec["entry_price"],
                        "exit_price": rec["exit_price"],
                        "entry_time": rec["entry_time"],
                        "exit_time": rec["exit_time"],
                        "realized_pnl": rec["realized_pnl"],
                        "charges": rec["charges"],
                        "reason": reason_str
                    })

            trade_id = f"TRD_{pos.position_id}"
            gross_pnl = round(pos.combined_realized_pnl + pos.total_charges, 2)
            if not any(e.get("exec_id") == trade_id for e in pending_logs):
                pending_logs.append({
                    "event_type": "TRADE_SUMMARY",
                    "exec_id": trade_id,
                    "strategy_id": pos.strategy_id,
                    "position_id": pos.position_id,
                    "trade_id": trade_id,
                    "entry_time": pos.entry_time,
                    "close_time": pos.close_time,
                    "gross_pnl": gross_pnl,
                    "total_charges": round(pos.total_charges, 2),
                    "net_pnl": round(pos.combined_realized_pnl, 2),
                    "reason": reason_str,
                    "legs_count": len(pos.legs)
                })

            pos.recalculate_risk()
            return pos

    def get_position(self, position_id: str) -> Optional[MultiLegPosition]:
        """Returns a position by its unique position ID if present."""
        with self._lock:
            return self.positions.get(position_id)

    def get_open_positions(self, strategy_id: Optional[str] = None) -> List[MultiLegPosition]:
        """Returns active open positions optionally filtered by strategy ID."""
        with self._lock:
            open_pos = [p for p in self.positions.values() if p.status in ["OPEN", "PARTIALLY_CLOSED"]]
            if strategy_id:
                return [p for p in open_pos if p.strategy_id == strategy_id]
            return open_pos

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Calculates portfolio-wide MTM, total open legs, realized P&L, and recovery health."""
        with self._lock:
            open_positions = self.get_open_positions()
            total_open_legs = sum(
                sum(1 for l in p.legs.values() if l.status == "OPEN") for p in open_positions
            )
            total_unrealized = sum(p.combined_unrealized_pnl for p in open_positions)
            total_realized = sum(p.combined_realized_pnl for p in self.positions.values())
            total_charges = sum(p.total_charges for p in self.positions.values())

            return {
                "open_positions_count": len(open_positions),
                "open_legs_count": total_open_legs,
                "total_unrealized_mtm": round(total_unrealized, 2),
                "total_realized_gross_pnl": round(total_realized + total_charges, 2),
                "total_charges": round(total_charges, 2),
                "total_net_realized_pnl": round(total_realized, 2),
                "net_combined_pnl": round(total_unrealized + total_realized, 2),
                "recovery_failed": self.recovery_failed,
                "is_healthy": self.is_healthy()
            }

    def update_marks(self, price_map: Dict[str, float], timestamp: str) -> None:
        """
        Atomically updates current mark prices and recalculates unrealized P&L
        for all active open positions under self._lock.
        """
        with self._lock:
            for pos in self.positions.values():
                if pos.status in ["OPEN", "PARTIALLY_CLOSED"]:
                    pos.update_marks(price_map, timestamp)

    def get_position_snapshot(self, position_id: str) -> Optional[Dict[str, Any]]:
        """Returns a thread-safe defensive dictionary snapshot of a position by ID."""
        with self._lock:
            pos = self.positions.get(position_id)
            return pos.to_dict() if pos else None

    def get_open_position_snapshots(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns thread-safe defensive dictionary snapshots of all open positions."""
        with self._lock:
            return [p.to_dict() for p in self.get_open_positions(strategy_id)]

    def save_state(self, filepath: Optional[str] = None) -> bool:
        """
        Atomically persists full multi-leg positions state with versioned schema.
        Fail-closed guard: NEVER replaces or overwrites if recovery failed!
        Takes an in-memory snapshot under self._lock and performs disk I/O outside the lock.
        """
        target_path = filepath or self.state_filepath
        if not target_path:
            return False

        with self._lock:
            if self.recovery_failed:
                return False

            payload = {
                "schema_version": self.SCHEMA_VERSION,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "positions_count": len(self.positions),
                "positions": {pid: pos.to_dict() for pid, pos in self.positions.items()},
                "processed_exit_signals": list(self.processed_exit_signals)
            }

        temp_path = f"{target_path}.tmp.{uuid.uuid4().hex}"
        try:
            parent_dir = os.path.dirname(target_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

            # Safe atomic replacement across platforms (POSIX and Windows)
            # On Windows, concurrent replace operations on the same destination can cause
            # transient WinError 5 (Access Denied); retry with minimal backoff.
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    os.replace(temp_path, target_path)
                    break
                except PermissionError:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(0.01 * (attempt + 1))

            self.last_save_error = None
            return True
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            self.last_save_error = f"{type(e).__name__}: {str(e)}"
            logger.error("Failed to save state to %s: %s", target_path, e, exc_info=True)
            return False

    def load_state(self, filepath: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """
        Restores full position state before strategy evaluations.
        Detects corrupt, incomplete, or incompatible state and fails closed.
        Never replaces unreadable state with an empty position list.
        """
        target_path = filepath or self.state_filepath
        if not target_path or not os.path.exists(target_path):
            with self._lock:
                self.recovery_failed = False
                self.is_recovered = True
            return True, None

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                with self._lock:
                    self.recovery_failed = True
                    self.recovery_error = "CORRUPTED_STATE: File is empty"
                return False, self.recovery_error

            data = json.loads(content)
            if not isinstance(data, dict):
                with self._lock:
                    self.recovery_failed = True
                    self.recovery_error = "CORRUPTED_STATE: Root element is not a JSON object"
                return False, self.recovery_error

            version = data.get("schema_version")
            if not version or not str(version).startswith("2."):
                with self._lock:
                    self.recovery_failed = True
                    self.recovery_error = f"INCOMPATIBLE_SCHEMA_VERSION: Found {version}, expected 2.x"
                return False, self.recovery_error

            positions_data = data.get("positions")
            if positions_data is None or not isinstance(positions_data, dict):
                with self._lock:
                    self.recovery_failed = True
                    self.recovery_error = "CORRUPTED_STATE: Missing or invalid 'positions' container"
                return False, self.recovery_error

            recovered_positions = {}
            for pid, pdata in positions_data.items():
                pos = MultiLegPosition.from_dict(pdata)
                recovered_positions[pid] = pos

            processed_signals = set(data.get("processed_exit_signals", []))
            for pos in recovered_positions.values():
                for sig in pos.metadata.get("processed_exit_signals", []):
                    processed_signals.add(sig)

            with self._lock:
                self.positions = recovered_positions
                self.processed_exit_signals = processed_signals
                self.recovery_failed = False
                self.recovery_error = None
                self.is_recovered = True
            return True, None

        except Exception as e:
            with self._lock:
                self.recovery_failed = True
                self.recovery_error = f"RECOVERY_FAILURE: {str(e)}"
            return False, self.recovery_error

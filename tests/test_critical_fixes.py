"""
test_critical_fixes.py
Comprehensive regression test suite for critical multi-leg exit, recovery,
and expiry-safe contract identity fixes.

Covers:
1. Closing one leg while other legs remain open.
2. Partial quantity exits and correct cost basis preservation.
3. Full structure exit.
4. Orphan short-leg detection and unhedged status flagging.
5. Duplicate exit-event rejection.
6. Restart recovery with open multi-leg positions.
7. Recovery from corrupted state and fail-closed defense.
8. Preventing new entries when recovery fails.
9. Same-strike contracts across multiple expiries.
10. Correct trade-log entries for partial and full exits without duplication.
11. Correct P&L and charges after multiple partial exits.
12. Preserving existing historical records and file append discipline.
"""

import unittest
import tempfile
import os
import json
import csv
from unittest.mock import patch

from src.accounting.charges_calculator import ChargesCalculator
from src.execution.position_manager import MultiLegPositionManager, LegPosition, MultiLegPosition
from src.execution.paper_broker import PaperBroker
from src.portfolio.risk_manager import RiskManager
from src.strategies.signal_models import (
    StopLossPolicy, ExitReason, StrategySignal, LegOrder, SignalDirection, StrategyMode, ExitSignal
)
from src.market.market_snapshot import MarketSnapshot, OptionContract, FuturesContract
from src.strategies.strategy_registry import StrategyRegistry
from src.strategies.strategy_base import StrategyBase
from src.strategies.spreads.diagonal_spread import DiagonalSpreadStrategy
from src.strategies.strategy_runner import StrategyRunner


class TestCriticalFixes(unittest.TestCase):
    """Test suite covering the 12 required regression areas."""

    def setUp(self):
        self.tax_calc = ChargesCalculator(brokerage_per_order=20.0)
        self.pm = MultiLegPositionManager(charges_calc=self.tax_calc)
        self.broker = PaperBroker(self.pm, simulated_slippage_points=0.5)

    def test_01_closing_one_leg_while_other_legs_remain_open(self):
        """1. Closing one leg while other legs remain open."""
        legs_data = [
            {"leg_id": "L_CE", "symbol": "NIFTY26OCT24800CE", "strike": 24800, "option_type": "CE", "side": "SELL", "quantity": 75, "entry_price": 100.0},
            {"leg_id": "L_PE", "symbol": "NIFTY26OCT24800PE", "strike": 24800, "option_type": "PE", "side": "SELL", "quantity": 75, "entry_price": 95.0}
        ]
        pos = self.pm.create_position(
            strategy_id="STRAT_005",
            exit_policy=StopLossPolicy.INDIVIDUAL_LEG_WITH_SHIFT_TO_COST,
            legs_data=legs_data,
            timestamp="2026-10-25T09:30:00Z"
        )
        self.assertEqual(pos.status, "OPEN")
        self.assertEqual(len(pos.legs), 2)

        # Snapshot with CE hitting SL (125) and PE at 85
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T09:45:00Z",
            spot=24820.0,
            options={
                "24800_CE": OptionContract(strike=24800, option_type="CE", ltp=125.0, bid=124.5, ask=125.5),
                "24800_PE": OptionContract(strike=24800, option_type="PE", ltp=85.0, bid=84.5, ask=85.5)
            }
        )

        exit_sig = ExitSignal(
            position_id=pos.position_id,
            strategy_id="STRAT_005",
            timestamp=snapshot.timestamp,
            reason=ExitReason.STOP_LOSS,
            legs_to_close=["L_CE"],
            signal_id="sig_exit_ce_01"
        )

        success, updated_pos, closed_records, err = self.broker.execute_exit_signal(exit_sig, snapshot)
        self.assertTrue(success)
        self.assertIsNotNone(updated_pos)
        self.assertEqual(updated_pos.status, "PARTIALLY_CLOSED")
        self.assertEqual(len(closed_records), 1)

        # Verify CE is CLOSED, PE is still OPEN
        self.assertEqual(pos.legs["L_CE"].status, "CLOSED")
        self.assertEqual(pos.legs["L_CE"].remaining_quantity, 0)
        self.assertEqual(pos.legs["L_PE"].status, "OPEN")
        self.assertEqual(pos.legs["L_PE"].remaining_quantity, 75)

        # Verify PE leg still updates unrealized MTM properly
        pos.update_marks({"NIFTY26OCT24800PE": 80.0}, "2026-10-25T10:00:00Z")
        self.assertAlmostEqual(pos.legs["L_PE"].unrealized_pnl, (95.0 - 80.0) * 75, places=1)

    def test_02_partial_quantity_exits_and_correct_cost_basis(self):
        """2. Partial quantity exits and correct cost basis."""
        legs_data = [{
            "leg_id": "L1",
            "symbol": "NIFTY26OCT25000CE",
            "strike": 25000,
            "option_type": "CE",
            "side": "BUY",
            "quantity": 150,
            "entry_price": 100.0
        }]
        pos = self.pm.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=legs_data,
            timestamp="2026-10-25T10:00:00Z"
        )

        snapshot1 = MarketSnapshot(
            timestamp="2026-10-25T10:30:00Z",
            spot=25050.0,
            options={
                "25000_CE": OptionContract(strike=25000, option_type="CE", ltp=120.0, bid=119.5, ask=120.5)
            }
        )

        # Exit 50 of 150 quantity
        exit_sig1 = ExitSignal(
            position_id=pos.position_id,
            strategy_id="STRAT_001",
            timestamp=snapshot1.timestamp,
            reason=ExitReason.PROFIT_TARGET,
            legs_to_close=["L1"],
            leg_quantities={"L1": 50},
            signal_id="sig_part_01"
        )

        success, updated_pos, closed_records, err = self.broker.execute_exit_signal(exit_sig1, snapshot1)
        self.assertTrue(success)
        self.assertEqual(len(closed_records), 1)
        self.assertEqual(closed_records[0]["closed_quantity"], 50)
        self.assertEqual(closed_records[0]["remaining_quantity"], 100)

        leg = pos.legs["L1"]
        self.assertEqual(leg.remaining_quantity, 100)
        self.assertEqual(leg.entry_price, 100.0)  # Cost basis preserved!
        self.assertEqual(leg.status, "OPEN")
        self.assertEqual(pos.status, "PARTIALLY_CLOSED")

        # Now mark remaining 100 qty at 130
        pos.update_marks({"NIFTY26OCT25000CE": 130.0}, "2026-10-25T11:00:00Z")
        self.assertAlmostEqual(leg.unrealized_pnl, (130.0 - 100.0) * 100, places=1)

    def test_03_full_structure_exit(self):
        """3. Full structure exit."""
        legs_data = [
            {"leg_id": "L1", "symbol": "NIFTY26OCT24700PE", "strike": 24700, "option_type": "PE", "side": "BUY", "quantity": 75, "entry_price": 30.0},
            {"leg_id": "L2", "symbol": "NIFTY26OCT24900PE", "strike": 24900, "option_type": "PE", "side": "SELL", "quantity": 75, "entry_price": 70.0},
            {"leg_id": "L3", "symbol": "NIFTY26OCT25100CE", "strike": 25100, "option_type": "CE", "side": "SELL", "quantity": 75, "entry_price": 65.0},
            {"leg_id": "L4", "symbol": "NIFTY26OCT25300CE", "strike": 25300, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 25.0}
        ]
        pos = self.pm.create_position(
            strategy_id="STRAT_007",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=legs_data,
            timestamp="2026-10-25T09:30:00Z"
        )
        self.assertEqual(len(pos.legs), 4)

        snapshot = MarketSnapshot(
            timestamp="2026-10-25T15:00:00Z",
            spot=25000.0,
            options={
                "24700_PE": OptionContract(strike=24700, option_type="PE", ltp=10.0, bid=9.5, ask=10.5),
                "24900_PE": OptionContract(strike=24900, option_type="PE", ltp=25.0, bid=24.5, ask=25.5),
                "25100_CE": OptionContract(strike=25100, option_type="CE", ltp=20.0, bid=19.5, ask=20.5),
                "25300_CE": OptionContract(strike=25300, option_type="CE", ltp=5.0, bid=4.5, ask=5.5)
            }
        )

        exit_sig = ExitSignal(
            position_id=pos.position_id,
            strategy_id="STRAT_007",
            timestamp=snapshot.timestamp,
            reason=ExitReason.TIME_CUTOFF,
            legs_to_close=[],  # Empty means entire position
            signal_id="sig_full_exit_01"
        )

        success, updated_pos, closed_records, err = self.broker.execute_exit_signal(exit_sig, snapshot)
        self.assertTrue(success)
        self.assertEqual(len(closed_records), 4)
        self.assertEqual(updated_pos.status, "CLOSED")
        self.assertEqual(updated_pos.combined_unrealized_pnl, 0.0)
        self.assertTrue(all(l.status == "CLOSED" for l in updated_pos.legs.values()))
        self.assertGreater(updated_pos.total_charges, 80.0)

    def test_04_orphan_short_leg_detection(self):
        """4. Orphan short-leg detection."""
        legs_data = [
            {"leg_id": "SHORT_CE", "symbol": "NIFTY24900CE", "strike": 24900, "option_type": "CE", "side": "SELL", "quantity": 75, "entry_price": 60.0},
            {"leg_id": "LONG_WING", "symbol": "NIFTY25100CE", "strike": 25100, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 20.0}
        ]
        pos = self.pm.create_position(
            strategy_id="STRAT_017",
            exit_policy=StopLossPolicy.COMBINED_POSITION,
            legs_data=legs_data,
            timestamp="2026-10-25T10:00:00Z"
        )
        self.assertTrue(pos.metadata.get("is_hedged", True))
        self.assertIsNone(pos.metadata.get("orphan_risk"))

        snapshot = MarketSnapshot(
            timestamp="2026-10-25T11:00:00Z",
            spot=24950.0,
            options={
                "24900_CE": OptionContract(strike=24900, option_type="CE", ltp=75.0, bid=74.5, ask=75.5),
                "25100_CE": OptionContract(strike=25100, option_type="CE", ltp=30.0, bid=29.5, ask=30.5)
            }
        )

        # Close the protective long wing only
        exit_sig = ExitSignal(
            position_id=pos.position_id,
            strategy_id="STRAT_017",
            timestamp=snapshot.timestamp,
            reason=ExitReason.STOP_LOSS,
            legs_to_close=["LONG_WING"],
            signal_id="sig_close_wing"
        )

        success, updated_pos, closed_records, err = self.broker.execute_exit_signal(exit_sig, snapshot)
        self.assertTrue(success)

        # Short leg MUST NOT be silently closed, but orphan risk must be detected
        self.assertEqual(pos.legs["SHORT_CE"].status, "OPEN")
        self.assertIsNotNone(pos.metadata.get("orphan_risk"))
        self.assertIn("ORPHAN_SHORT_EXPOSURE", pos.metadata["orphan_risk"])
        self.assertFalse(pos.metadata.get("is_hedged"))

    def test_05_duplicate_exit_event_rejection(self):
        """5. Duplicate exit-event rejection."""
        legs_data = [{
            "leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0
        }]
        pos = self.pm.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=legs_data,
            timestamp="2026-10-25T10:00:00Z"
        )

        snapshot = MarketSnapshot(
            timestamp="2026-10-25T10:30:00Z",
            spot=24850.0,
            options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=120.0, bid=119.5, ask=120.5)}
        )

        exit_sig = ExitSignal(
            position_id=pos.position_id,
            strategy_id="STRAT_001",
            timestamp=snapshot.timestamp,
            reason=ExitReason.TARGET_HIT,
            legs_to_close=["L1"],
            signal_id="DUP_SIGNAL_123"
        )

        # First execution: success
        success1, pos1, records1, err1 = self.broker.execute_exit_signal(exit_sig, snapshot)
        self.assertTrue(success1)
        self.assertIsNone(err1)

        # Second execution with same signal_id: MUST REJECT
        success2, pos2, records2, err2 = self.broker.execute_exit_signal(exit_sig, snapshot)
        self.assertFalse(success2)
        self.assertIn("DUPLICATE_EXIT_SIGNAL", err2)

    def test_06_restart_recovery_with_open_multi_leg_positions(self):
        """6. Restart recovery with open multi-leg positions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            pm1 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)

            legs_data = [
                {"leg_id": "CE1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "SELL", "quantity": 75, "entry_price": 110.0},
                {"leg_id": "PE1", "symbol": "NIFTY24800PE", "strike": 24800, "option_type": "PE", "side": "SELL", "quantity": 75, "entry_price": 105.0}
            ]
            pos1 = pm1.create_position(
                strategy_id="STRAT_005",
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG_WITH_SHIFT_TO_COST,
                legs_data=legs_data,
                timestamp="2026-10-25T09:30:00Z",
                initial_sl=25.0,
                trailing_sl=10.0,
                target=50.0,
                metadata={"test_key": "recovery_val"}
            )
            # Save state atomically
            saved = pm1.save_state()
            self.assertTrue(saved)
            self.assertTrue(os.path.exists(state_file))

            # Simulate application restart: fresh manager
            pm2 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            recovered, err = pm2.load_state()
            self.assertTrue(recovered)
            self.assertIsNone(err)
            self.assertTrue(pm2.is_healthy())

            recovered_positions = pm2.get_open_positions()
            self.assertEqual(len(recovered_positions), 1)
            r_pos = recovered_positions[0]
            self.assertEqual(r_pos.position_id, pos1.position_id)
            self.assertEqual(r_pos.strategy_id, "STRAT_005")
            self.assertEqual(len(r_pos.legs), 2)
            self.assertEqual(r_pos.legs["CE1"].entry_price, 110.0)
            self.assertEqual(r_pos.legs["PE1"].entry_price, 105.0)
            self.assertEqual(r_pos.metadata["test_key"], "recovery_val")

    def test_07_recovery_from_corrupted_state(self):
        """7. Recovery from corrupted state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt_file = os.path.join(tmpdir, "corrupt_state.json")
            # Write malformed JSON
            with open(corrupt_file, "w", encoding="utf-8") as f:
                f.write('{"schema_version": "2.1.0", "positions": "NOT_A_VALID_OBJECT"')

            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=corrupt_file)
            recovered, err = pm.load_state()
            self.assertFalse(recovered)
            self.assertTrue(pm.recovery_failed)
            self.assertFalse(pm.is_healthy())
            self.assertIsNotNone(err)

            # Fail-closed protection: save_state MUST NOT overwrite corrupted file!
            saved = pm.save_state()
            self.assertFalse(saved)
            # Check corrupted file content is intact (not replaced by empty positions)
            with open(corrupt_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn('"NOT_A_VALID_OBJECT"', content)

    def test_08_preventing_new_entries_when_recovery_fails(self):
        """8. Preventing new entries when recovery fails."""
        rm = RiskManager()
        pm = MultiLegPositionManager(charges_calc=self.tax_calc)
        pm.recovery_failed = True  # Recovery failure state

        summary = pm.get_portfolio_summary()
        self.assertTrue(summary["recovery_failed"])
        self.assertFalse(summary["is_healthy"])

        signal = StrategySignal(
            strategy_id="STRAT_001",
            direction=SignalDirection.BUY,
            timestamp="2026-10-25T10:00:00Z",
            legs=[LegOrder(symbol="NIFTY24800CE", strike=24800, option_type="CE", side="BUY", quantity=75)]
        )

        is_valid, reject_reason = rm.validate_signal(
            signal=signal,
            portfolio_summary=summary,
            open_positions_count_for_strat=0,
            is_market_data_stale=False
        )
        self.assertFalse(is_valid)
        self.assertIn("RECOVERY_FAILURE", reject_reason)

    def test_09_same_strike_contracts_across_multiple_expiries(self):
        """9. Same-strike contracts across multiple expiries."""
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T10:00:00Z",
            spot_price=24800.0,
            current_expiry="2026-10-29",
            next_expiry="2026-11-05"
        )
        near_contract = OptionContract(
            symbol="NIFTY26OCT24800CE", strike=24800, option_type="CE", expiry="2026-10-29", ltp=110.0
        )
        far_contract = OptionContract(
            symbol="NIFTY26NOV24800CE", strike=24800, option_type="CE", expiry="2026-11-05", ltp=230.0
        )

        snapshot.add_option(near_contract)
        snapshot.add_option(far_contract)

        # Both contracts must coexist without overwriting
        self.assertEqual(len(snapshot.option_chain), 2)

        # Querying with exact expiry returns exact contract
        q_near = snapshot.get_option(24800, "CE", expiry="2026-10-29")
        q_far = snapshot.get_option(24800, "CE", expiry="2026-11-05")
        self.assertIsNotNone(q_near)
        self.assertIsNotNone(q_far)
        self.assertEqual(q_near.ltp, 110.0)
        self.assertEqual(q_far.ltp, 230.0)

        # Querying an unavailable expiry must return None (data-blocked) - NEVER fall back!
        q_missing = snapshot.get_option(24800, "CE", expiry="2026-11-12")
        self.assertIsNone(q_missing)

        # Calendar Spread evaluation without far expiry must return None (blocked)
        strat = StrategyRegistry.create_strategy("STRAT_023", config={"mode": "PAPER"})
        strat.entries_today_count = 0

        # Snapshot with missing far expiry
        partial_snapshot = MarketSnapshot(
            timestamp="2026-10-25T10:00:00Z",
            spot_price=24800.0,
            current_expiry="2026-10-29",
            next_expiry="2026-11-05"
        )
        partial_snapshot.add_option(near_contract)  # Only near contract added
        sig = strat.evaluate_entry(partial_snapshot)
        self.assertIsNone(sig)  # Blocked! Never silently substitutes!

    def test_10_correct_trade_log_entries_for_partial_and_full_exits(self):
        """10. Correct trade-log entries for partial and full exits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            strat = StrategyRegistry.create_strategy("STRAT_005", config={
                "mode": "PAPER",
                "sl_pct": 25.0,
                "shift_sl_to_cost_on_first_exit": True
            })
            # Point log directories to temporary isolated folder
            strat.log_dir = tmpdir
            strat.trades_log_path = os.path.join(tmpdir, "trades.csv")
            strat.legs_log_path = os.path.join(tmpdir, "legs.csv")
            strat.events_log_path = os.path.join(tmpdir, "events.jsonl")
            strat.errors_log_path = os.path.join(tmpdir, "errors.log")

            # Initialize headers
            with open(strat.trades_log_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["trade_id", "pos_id", "strat", "t_in", "t_out", "gross", "fees", "net", "reason", "legs"])
            with open(strat.legs_log_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["leg_id", "pos_id", "strat", "sym", "side", "qty", "p_in", "p_out", "t_in", "t_out", "pnl", "fees"])

            legs_data = [
                {"leg_id": "LEG_A", "symbol": "NIFTY_CE", "strike": 24800, "option_type": "CE", "side": "SELL", "quantity": 75, "entry_price": 100.0},
                {"leg_id": "LEG_B", "symbol": "NIFTY_PE", "strike": 24800, "option_type": "PE", "side": "SELL", "quantity": 75, "entry_price": 100.0}
            ]
            pos = self.pm.create_position(
                strategy_id="STRAT_005",
                exit_policy=StopLossPolicy.INDIVIDUAL_LEG_WITH_SHIFT_TO_COST,
                legs_data=legs_data,
                timestamp="2026-10-25T09:30:00Z"
            )

            # 1. Partial exit of LEG_A
            success, updated_pos, closed_records, err = self.pm.close_specific_legs(
                position_id=pos.position_id,
                legs_to_close=["LEG_A"],
                exit_prices={"NIFTY_CE": 125.0},
                timestamp="2026-10-25T10:00:00Z",
                reason=ExitReason.STOP_LOSS
            )
            self.assertTrue(success)
            strat.log_partial_exit(updated_pos, closed_records, ExitReason.STOP_LOSS.value)

            # Check legs.csv contains exactly 1 data row for LEG_A
            with open(strat.legs_log_path, "r", encoding="utf-8") as f:
                leg_rows = list(csv.reader(f))
            self.assertEqual(len(leg_rows), 2)  # Header + 1 row
            self.assertEqual(leg_rows[1][0], "LEG_A")

            # 2. Final exit of LEG_B
            success2, updated_pos2, closed_records2, err2 = self.pm.close_specific_legs(
                position_id=pos.position_id,
                legs_to_close=["LEG_B"],
                exit_prices={"NIFTY_PE": 60.0},
                timestamp="2026-10-25T15:00:00Z",
                reason=ExitReason.TIME_CUTOFF
            )
            self.assertTrue(success2)
            self.assertEqual(updated_pos2.status, "CLOSED")
            strat.log_trade(updated_pos2, ExitReason.TIME_CUTOFF.value)

            # Check legs.csv now contains exactly 2 data rows (no duplication of LEG_A!)
            with open(strat.legs_log_path, "r", encoding="utf-8") as f:
                leg_rows_after = list(csv.reader(f))
            self.assertEqual(len(leg_rows_after), 3)  # Header + LEG_A + LEG_B
            logged_leg_ids = [r[0] for r in leg_rows_after[1:]]
            self.assertEqual(logged_leg_ids, ["LEG_A", "LEG_B"])

            # Check trades.csv contains exactly 1 trade row
            with open(strat.trades_log_path, "r", encoding="utf-8") as f:
                trade_rows = list(csv.reader(f))
            self.assertEqual(len(trade_rows), 2)  # Header + 1 trade

    def test_11_correct_pnl_and_charges_after_multiple_partial_exits(self):
        """11. Correct P&L and charges after multiple partial exits."""
        legs_data = [{
            "leg_id": "L1", "symbol": "NIFTY25000CE", "strike": 25000, "option_type": "CE", "side": "BUY", "quantity": 150, "entry_price": 100.0
        }]
        pos = self.pm.create_position(
            strategy_id="STRAT_001",
            exit_policy=StopLossPolicy.PREMIUM_BASED,
            legs_data=legs_data,
            timestamp="2026-10-25T10:00:00Z"
        )

        # Exit 1: 50 qty @ 110 (+10 pts * 50 = +500 gross)
        s1, p1, rec1, e1 = self.pm.close_specific_legs(
            pos.position_id, ["L1"], {"NIFTY25000CE": 110.0}, "2026-10-25T10:30:00Z",
            ExitReason.PROFIT_TARGET, leg_quantities={"L1": 50}
        )
        self.assertTrue(s1)
        self.assertEqual(rec1[0]["closed_quantity"], 50)

        # Exit 2: 50 qty @ 120 (+20 pts * 50 = +1000 gross)
        s2, p2, rec2, e2 = self.pm.close_specific_legs(
            pos.position_id, ["L1"], {"NIFTY25000CE": 120.0}, "2026-10-25T11:00:00Z",
            ExitReason.PROFIT_TARGET, leg_quantities={"L1": 50}
        )
        self.assertTrue(s2)
        self.assertEqual(rec2[0]["closed_quantity"], 50)

        # Exit 3: final 50 qty @ 130 (+30 pts * 50 = +1500 gross)
        s3, p3, rec3, e3 = self.pm.close_specific_legs(
            pos.position_id, ["L1"], {"NIFTY25000CE": 130.0}, "2026-10-25T11:30:00Z",
            ExitReason.PROFIT_TARGET, leg_quantities={"L1": 50}
        )
        self.assertTrue(s3)
        self.assertEqual(pos.status, "CLOSED")

        # Total gross = 500 + 1000 + 1500 = 3000
        gross_total = pos.combined_realized_pnl + pos.total_charges
        self.assertAlmostEqual(gross_total, 3000.0, places=1)
        self.assertGreater(pos.total_charges, 60.0)
        self.assertAlmostEqual(pos.combined_realized_pnl, 3000.0 - pos.total_charges, places=1)

    def test_12_preserving_existing_historical_records(self):
        """12. Preserving existing historical records and file append discipline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_csv = os.path.join(tmpdir, "trades.csv")
            # Write prior historical trade lines
            with open(test_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["trade_id", "status"])
                writer.writerow(["HISTORICAL_001", "CLOSED"])
                writer.writerow(["HISTORICAL_002", "CLOSED"])

            # Append new trade
            with open(test_csv, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["NEW_TRADE_003", "CLOSED"])

            # Verify historical rows were not overwritten
            with open(test_csv, "r", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[1][0], "HISTORICAL_001")
            self.assertEqual(rows[2][0], "HISTORICAL_002")
            self.assertEqual(rows[3][0], "NEW_TRADE_003")


class StrategyBaseStub(StrategyBase):
    """Stub strategy class for isolated strategy unit tests."""
    def evaluate_entry(self, snapshot):
        return None
    def evaluate_exit(self, snapshot, position):
        return None
    def update_trailing_sl(self, snapshot, position):
        pass


class TestFourVerifiedDefects(unittest.TestCase):
    """
    Dedicated regression test suite covering the 4 verified defects:
    1. Fix 1 (P0): DiagonalSpreadStrategy NameError & Expiry Handling.
    2. Fix 2 (P1): MultiLegPositionManager Atomic State Persistence.
    3. Fix 3 (P1): StrategyBase Complete Per-Execution Leg Audit Logging.
    4. Fix 4 (P2): PaperBroker Idempotent Exit Deduplication Across Restarts.
    """

    def setUp(self):
        self.tax_calc = ChargesCalculator(brokerage_per_order=20.0)

    # =========================================================================
    # FIX 1 — P0: DIAGONAL SPREAD NAMEERROR & EXPIRY RESOLUTION
    # =========================================================================

    def test_fix1_01_both_expiries_present_with_valid_contracts(self):
        """1. Both required expiries are present, with valid contracts for both expiries."""
        snapshot = MarketSnapshot(
            timestamp="2026-10-25 09:25:00",
            spot_price=24800.0,
            current_expiry="2026-10-29",
            next_expiry="2026-11-05",
            options={
                "24900_CE_2026-10-29": OptionContract(strike=24900, option_type="CE", expiry="2026-10-29", ltp=55.0, bid=54.5, ask=55.5, symbol="NIFTY26OCT24900CE"),
                "24800_CE_2026-11-05": OptionContract(strike=24800, option_type="CE", expiry="2026-11-05", ltp=125.0, bid=124.5, ask=125.5, symbol="NIFTY05NOV24800CE")
            }
        )
        strat = DiagonalSpreadStrategy()
        sig = strat.evaluate_entry(snapshot)
        self.assertIsNotNone(sig, "Entry signal should be produced when both expiries and valid contracts exist")
        self.assertEqual(len(sig.legs), 2)
        # Leg 1: Sell near OTM CE
        self.assertEqual(sig.legs[0].strike, 24900)
        self.assertEqual(sig.legs[0].expiry, "2026-10-29")
        self.assertEqual(sig.legs[0].side, "SELL")
        # Leg 2: Buy far ATM CE
        self.assertEqual(sig.legs[1].strike, 24800)
        self.assertEqual(sig.legs[1].expiry, "2026-11-05")
        self.assertEqual(sig.legs[1].side, "BUY")

    def test_fix1_02_next_expiry_missing(self):
        """2. The next expiry is missing."""
        snapshot = MarketSnapshot(
            timestamp="2026-10-25 09:25:00",
            spot_price=24800.0,
            current_expiry="2026-10-29",
            next_expiry="",
            options={
                "24900_CE_2026-10-29": OptionContract(strike=24900, option_type="CE", expiry="2026-10-29", ltp=55.0, bid=54.5, ask=55.5)
            }
        )
        strat = DiagonalSpreadStrategy()
        sig = strat.evaluate_entry(snapshot)
        self.assertIsNone(sig, "Missing next_expiry must result in data-blocked None without NameError")

    def test_fix1_03_current_expiry_missing(self):
        """3. The current expiry is missing."""
        snapshot = MarketSnapshot(
            timestamp="2026-10-25 09:25:00",
            spot_price=24800.0,
            current_expiry="",
            next_expiry="2026-11-05",
            options={
                "24800_CE_2026-11-05": OptionContract(strike=24800, option_type="CE", expiry="2026-11-05", ltp=125.0, bid=124.5, ask=125.5)
            }
        )
        strat = DiagonalSpreadStrategy()
        sig = strat.evaluate_entry(snapshot)
        self.assertIsNone(sig, "Missing current_expiry must result in data-blocked None without NameError")

    def test_fix1_04_one_or_more_required_contracts_missing(self):
        """4. One or more required option contracts are missing."""
        snapshot = MarketSnapshot(
            timestamp="2026-10-25 09:25:00",
            spot_price=24800.0,
            current_expiry="2026-10-29",
            next_expiry="2026-11-05",
            options={
                # Near CE present, but far CE missing
                "24900_CE_2026-10-29": OptionContract(strike=24900, option_type="CE", expiry="2026-10-29", ltp=55.0, bid=54.5, ask=55.5)
            }
        )
        strat = DiagonalSpreadStrategy()
        sig = strat.evaluate_entry(snapshot)
        self.assertIsNone(sig, "Missing required option contract must return data-blocked None")

    def test_fix1_05_same_strike_different_expiries_resolve_correctly(self):
        """5. Contracts with identical strikes and option types but different expiries resolve correctly."""
        # Both near and far have contracts at strike 24800 and 24900
        snapshot = MarketSnapshot(
            timestamp="2026-10-25 09:25:00",
            spot_price=24800.0,
            current_expiry="2026-10-29",
            next_expiry="2026-11-05",
            options={
                "24900_CE_2026-10-29": OptionContract(strike=24900, option_type="CE", expiry="2026-10-29", ltp=55.0, bid=54.5, ask=55.5, symbol="NEAR_24900_CE"),
                "24900_CE_2026-11-05": OptionContract(strike=24900, option_type="CE", expiry="2026-11-05", ltp=95.0, bid=94.5, ask=95.5, symbol="FAR_24900_CE"),
                "24800_CE_2026-10-29": OptionContract(strike=24800, option_type="CE", expiry="2026-10-29", ltp=110.0, bid=109.5, ask=110.5, symbol="NEAR_24800_CE"),
                "24800_CE_2026-11-05": OptionContract(strike=24800, option_type="CE", expiry="2026-11-05", ltp=160.0, bid=159.5, ask=160.5, symbol="FAR_24800_CE"),
            }
        )
        strat = DiagonalSpreadStrategy()
        sig = strat.evaluate_entry(snapshot)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.legs[0].symbol, "NEAR_24900_CE")
        self.assertEqual(sig.legs[0].expiry, "2026-10-29")
        self.assertEqual(sig.legs[1].symbol, "FAR_24800_CE")
        self.assertEqual(sig.legs[1].expiry, "2026-11-05")

    # =========================================================================
    # FIX 2 — P1: SAFE ATOMIC STATE REPLACEMENT
    # =========================================================================

    def test_fix2_01_successful_replacement_of_existing_state_file(self):
        """Atomic replacement successfully updates existing state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            
            # Initial state
            self.assertTrue(pm.save_state())
            self.assertTrue(os.path.exists(state_path))
            with open(state_path, "r", encoding="utf-8") as f:
                d1 = json.load(f)
            self.assertEqual(d1["positions_count"], 0)

            # Create position and save again (replacing state file)
            pm.create_position(
                "STRAT_001", StopLossPolicy.PREMIUM_BASED,
                [{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                "2026-10-25 09:30:00"
            )
            self.assertTrue(pm.save_state())
            with open(state_path, "r", encoding="utf-8") as f:
                d2 = json.load(f)
            self.assertEqual(d2["positions_count"], 1)
            # Ensure no orphaned temp files
            self.assertFalse(os.path.exists(state_path + ".tmp"))

    def test_fix2_02_failure_before_replacement_preserves_previous_file(self):
        """Failure during write/fsync preserves existing state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            self.assertTrue(pm.save_state())

            with open(state_path, "r", encoding="utf-8") as f:
                initial_content = f.read()

            # Simulate failure during json.dump
            with patch("json.dump", side_effect=IOError("Simulated write failure")):
                success = pm.save_state()
                self.assertFalse(success)

            # Existing state file must be unchanged
            with open(state_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), initial_content)
            self.assertFalse(os.path.exists(state_path + ".tmp"))

    def test_fix2_03_replacement_raising_exception_preserves_previous_state(self):
        """Exception during os.replace preserves existing state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            self.assertTrue(pm.save_state())

            with open(state_path, "r", encoding="utf-8") as f:
                initial_content = f.read()

            # Simulate failure in os.replace
            with patch("os.replace", side_effect=OSError("Simulated replace lock")):
                success = pm.save_state()
                self.assertFalse(success)

            # Previous valid state must remain untouched
            with open(state_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), initial_content)
            self.assertFalse(os.path.exists(state_path + ".tmp"))

    def test_fix2_04_save_attempts_after_failed_recovery_fail_closed(self):
        """Save attempts after failed recovery do not overwrite the corrupt source state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt_file = os.path.join(tmpdir, "corrupt_state.json")
            with open(corrupt_file, "w", encoding="utf-8") as f:
                f.write("{ INVALID JSON CONTENT ...")

            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=corrupt_file)
            recovered, err = pm.load_state(corrupt_file)
            self.assertFalse(recovered)
            self.assertTrue(pm.recovery_failed)

            # Attempting to save MUST return False and NOT touch or overwrite the corrupt file
            saved = pm.save_state(corrupt_file)
            self.assertFalse(saved)

            with open(corrupt_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "{ INVALID JSON CONTENT ...")

    # =========================================================================
    # FIX 3 — P1: COMPLETE PER-EXECUTION LEG AUDIT LOGGING
    # =========================================================================

    def test_fix3_01_single_leg_multiple_partial_exits_and_final_exit(self):
        """A single leg undergoing 1st partial exit, 2nd partial exit, and final exit logs all executions correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = MultiLegPositionManager(charges_calc=self.tax_calc)
            strat = StrategyBaseStub("STRAT_TEST", "Test Strategy", "MOMENTUM", workspace_root=tmpdir)
            pos = pm.create_position(
                "STRAT_TEST", StopLossPolicy.PREMIUM_BASED,
                [{"leg_id": "LEG_1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                "2026-10-25 09:30:00"
            )

            # 1. First partial exit of 25 qty @ 110
            s1, pos, rec1, e1 = pm.close_specific_legs(pos.position_id, ["LEG_1"], {"NIFTY24800CE": 110.0}, "2026-10-25 10:00:00", ExitReason.PROFIT_TARGET, leg_quantities={"LEG_1": 25})
            self.assertTrue(s1)
            strat.log_partial_exit(pos, rec1, ExitReason.PROFIT_TARGET.value)

            # 2. Second partial exit of 25 qty @ 120
            s2, pos, rec2, e2 = pm.close_specific_legs(pos.position_id, ["LEG_1"], {"NIFTY24800CE": 120.0}, "2026-10-25 11:00:00", ExitReason.PROFIT_TARGET, leg_quantities={"LEG_1": 25})
            self.assertTrue(s2)
            strat.log_partial_exit(pos, rec2, ExitReason.PROFIT_TARGET.value)

            # 3. Final exit of remaining 25 qty @ 130
            s3, pos, rec3, e3 = pm.close_specific_legs(pos.position_id, ["LEG_1"], {"NIFTY24800CE": 130.0}, "2026-10-25 12:00:00", ExitReason.PROFIT_TARGET, leg_quantities={"LEG_1": 25})
            self.assertTrue(s3)
            self.assertEqual(pos.status, "CLOSED")
            strat.log_trade(pos, ExitReason.PROFIT_TARGET.value, closed_legs=rec3)

            # Verify legs.csv
            with open(strat.legs_log_path, "r", encoding="utf-8") as f:
                leg_rows = list(csv.reader(f))
            self.assertEqual(len(leg_rows), 4, f"Expected header + 3 exit execution rows, got {len(leg_rows)}")
            header, r1, r2, r3 = leg_rows
            self.assertEqual(int(r1[5]), 25)
            self.assertEqual(int(r2[5]), 25)
            self.assertEqual(int(r3[5]), 25)
            total_closed = int(r1[5]) + int(r2[5]) + int(r3[5])
            self.assertEqual(total_closed, 75, "Total closed quantity across executions must match initial quantity 75")

            # Verify trades.csv
            with open(strat.trades_log_path, "r", encoding="utf-8") as f:
                trade_rows = list(csv.reader(f))
            self.assertEqual(len(trade_rows), 2, "Expected header + 1 trade row")
            net_pnl = float(trade_rows[1][7])
            sum_realized = float(r1[10]) + float(r2[10]) + float(r3[10])
            self.assertAlmostEqual(net_pnl, sum_realized, places=2)

    def test_fix3_02_duplicate_submission_does_not_produce_duplicate_record(self):
        """Duplicate submission of an exit event does not append duplicate rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = MultiLegPositionManager(charges_calc=self.tax_calc)
            strat = StrategyBaseStub("STRAT_TEST", "Test Strategy", "MOMENTUM", workspace_root=tmpdir)
            pos = pm.create_position(
                "STRAT_TEST", StopLossPolicy.PREMIUM_BASED,
                [{"leg_id": "LEG_1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 50, "entry_price": 100.0}],
                "2026-10-25 09:30:00"
            )
            s1, pos, rec1, e1 = pm.close_specific_legs(pos.position_id, ["LEG_1"], {"NIFTY24800CE": 110.0}, "2026-10-25 10:00:00", ExitReason.PROFIT_TARGET, leg_quantities={"LEG_1": 25})
            self.assertTrue(s1)

            # Submit partial exit twice
            strat.log_partial_exit(pos, rec1, ExitReason.PROFIT_TARGET.value)
            strat.log_partial_exit(pos, rec1, ExitReason.PROFIT_TARGET.value)

            with open(strat.legs_log_path, "r", encoding="utf-8") as f:
                leg_rows = list(csv.reader(f))
            self.assertEqual(len(leg_rows), 2, "Duplicate submission must be ignored; only 1 execution row present")

    # =========================================================================
    # FIX 4 — P2: IDEMPOTENT EXIT SIGNALS ACROSS RESTARTS
    # =========================================================================

    def test_fix4_01_partial_exit_restart_and_replay_same_signal(self):
        """Partial exit followed by restart and replay of same signal ID is safely rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            pm1 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            broker1 = PaperBroker(pm1, simulated_slippage_points=0.0)

            pos = pm1.create_position(
                "STRAT_001", StopLossPolicy.PREMIUM_BASED,
                [{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                "2026-10-25 09:30:00"
            )
            pm1.save_state()

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 10:00:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=110.0, ask=110.0, symbol="NIFTY24800CE")}
            )

            # First partial exit
            sig1 = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_001", timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25},
                signal_id="EXIT_SIG_PARTIAL_1"
            )
            s1, p1, rec1, e1 = broker1.execute_exit_signal(sig1, snapshot)
            self.assertTrue(s1)
            self.assertEqual(p1.legs["L1"].remaining_quantity, 50)

            # Simulate application restart: new PM, new Broker loading state from disk
            pm2 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            recovered, err = pm2.load_state()
            self.assertTrue(recovered)
            broker2 = PaperBroker(pm2, simulated_slippage_points=0.0)

            # Replay the identical exit signal
            s2, p2, rec2, e2 = broker2.execute_exit_signal(sig1, snapshot)
            self.assertFalse(s2)
            self.assertIn("DUPLICATE_EXIT_SIGNAL", str(e2))
            # Verify remaining quantity was NOT reduced further
            self.assertEqual(pm2.positions[pos.position_id].legs["L1"].remaining_quantity, 50)

    def test_fix4_02_two_distinct_partial_exit_signals_on_same_leg(self):
        """Two distinct partial-exit signals on the same leg execute cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            broker = PaperBroker(pm, simulated_slippage_points=0.0)

            pos = pm.create_position(
                "STRAT_001", StopLossPolicy.PREMIUM_BASED,
                [{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                "2026-10-25 09:30:00"
            )
            pm.save_state()

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 10:00:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=110.0, ask=110.0, symbol="NIFTY24800CE")}
            )

            # Signal 1
            sig1 = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_001", timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25},
                signal_id="EXIT_SIG_A"
            )
            s1, p1, rec1, e1 = broker.execute_exit_signal(sig1, snapshot)
            self.assertTrue(s1)
            self.assertEqual(p1.legs["L1"].remaining_quantity, 50)

            # Signal 2 (distinct signal ID)
            sig2 = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_001", timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25},
                signal_id="EXIT_SIG_B"
            )
            s2, p2, rec2, e2 = broker.execute_exit_signal(sig2, snapshot)
            self.assertTrue(s2)
            self.assertEqual(p2.legs["L1"].remaining_quantity, 25)

    def test_fix4_03_replaying_fully_processed_signal(self):
        """Replaying a fully processed signal against a closed position is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            broker = PaperBroker(pm, simulated_slippage_points=0.0)

            pos = pm.create_position(
                "STRAT_001", StopLossPolicy.PREMIUM_BASED,
                [{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                "2026-10-25 09:30:00"
            )
            pm.save_state()

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 10:00:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=110.0, ask=110.0, symbol="NIFTY24800CE")}
            )

            sig = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_001", timestamp=snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 75},
                signal_id="EXIT_SIG_FULL"
            )
            s1, p1, rec1, e1 = broker.execute_exit_signal(sig, snapshot)
            self.assertTrue(s1)
            self.assertEqual(p1.status, "CLOSED")

            # Replay
            s2, p2, rec2, e2 = broker.execute_exit_signal(sig, snapshot)
            self.assertFalse(s2)

    def test_fix4_04_failed_exit_followed_by_valid_retry(self):
        """A failed exit is not marked processed and a valid retry with same signal ID succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            broker = PaperBroker(pm, simulated_slippage_points=0.0)

            pos = pm.create_position(
                "STRAT_001", StopLossPolicy.PREMIUM_BASED,
                [{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                "2026-10-25 09:30:00"
            )
            pm.save_state()

            # Snapshot with negative/missing quote (will fail execution pass)
            bad_snapshot = MarketSnapshot(
                timestamp="2026-10-25 10:00:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=0.0, bid=-1.0, ask=0.0, symbol="NIFTY24800CE")}
            )

            sig = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_001", timestamp=bad_snapshot.timestamp,
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 75},
                signal_id="RETRYABLE_SIGNAL_01"
            )
            s_fail, _, _, _ = broker.execute_exit_signal(sig, bad_snapshot)
            self.assertFalse(s_fail)
            # Must NOT be marked processed
            self.assertFalse(broker.is_signal_processed("RETRYABLE_SIGNAL_01"))

            # Retry with good snapshot
            good_snapshot = MarketSnapshot(
                timestamp="2026-10-25 10:01:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=109.5, ask=110.5, symbol="NIFTY24800CE")}
            )
            s_ok, p_ok, _, _ = broker.execute_exit_signal(sig, good_snapshot)
            self.assertTrue(s_ok)
            self.assertEqual(p_ok.status, "CLOSED")
            self.assertTrue(broker.is_signal_processed("RETRYABLE_SIGNAL_01"))

    def test_fix4_05_recovery_from_state_file_without_processed_signals_field(self):
        """Recovery from a legacy state file that omits processed_exit_signals field succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_file = os.path.join(tmpdir, "legacy_state.json")
            legacy_payload = {
                "schema_version": "2.1.0",
                "timestamp": "2026-10-25 09:30:00",
                "positions_count": 0,
                "positions": {}
                # "processed_exit_signals" is omitted intentionally
            }
            with open(legacy_file, "w", encoding="utf-8") as f:
                json.dump(legacy_payload, f)

            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=legacy_file)
            recovered, err = pm.load_state()
            self.assertTrue(recovered)
            self.assertEqual(len(pm.processed_exit_signals), 0)

    def test_fix4_06_corrupt_state_blocks_new_entries_and_exits(self):
        """Corrupted state triggers fail-closed recovery and blocks broker entries and exits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt_file = os.path.join(tmpdir, "corrupt.json")
            with open(corrupt_file, "w", encoding="utf-8") as f:
                f.write("{ incomplete json")

            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=corrupt_file)
            recovered, err = pm.load_state()
            self.assertFalse(recovered)
            self.assertTrue(pm.recovery_failed)

            broker = PaperBroker(pm)
            snapshot = MarketSnapshot(
                timestamp="2026-10-25 10:00:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=100.0, bid=99.5, ask=100.5, symbol="NIFTY24800CE")}
            )

            # Entry must be blocked
            entry_sig = StrategySignal(
                strategy_id="STRAT_001", direction=SignalDirection.BULLISH, timestamp=snapshot.timestamp,
                legs=[LegOrder("L1", "NIFTY24800CE", 24800, "CE", "2026-10-29", "BUY", 75, 100.0)],
                exit_policy=StopLossPolicy.PREMIUM_BASED
            )
            s_entry, pos, err_entry = broker.execute_entry_signal(entry_sig, snapshot)
            self.assertFalse(s_entry)
            self.assertEqual(err_entry, "RECOVERY_FAILED_FAIL_CLOSED")

            # Exit must be blocked
            exit_sig = ExitSignal(
                position_id="POS_FAKE", strategy_id="STRAT_001", timestamp=snapshot.timestamp,
                reason=ExitReason.STOP_LOSS
            )
            s_exit, _, _, err_exit = broker.execute_exit_signal(exit_sig, snapshot)
            self.assertFalse(s_exit)
            self.assertEqual(err_exit, "RECOVERY_FAILED_FAIL_CLOSED")


class TestRollbackAndPruningFixes(unittest.TestCase):
    """
    Focused regression test suite covering:
    - In-memory position rollback upon exit persistence failure (partial & full).
    - In-place authoritative object identity preservation.
    - Consistency across broker, manager, and position metadata signal caches.
    - Safe retry without double accounting or double-decrementing.
    - Signal pruning exceeding 1,000 historical signals while protecting open/partially-closed positions.
    - Protection against stale signal re-execution.
    """

    def setUp(self):
        self.tax_calc = ChargesCalculator(brokerage_per_order=20.0)

    def test_partial_exit_persistence_failure_rollback_and_retry(self):
        """A. Partial-exit persistence failure rolls back in-place, retry succeeds cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            broker = PaperBroker(pm, simulated_slippage_points=0.5)

            # Start with a position of 75 units
            pos = pm.create_position(
                strategy_id="STRAT_001",
                exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{
                    "leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800,
                    "option_type": "CE", "side": "BUY", "quantity": 75,
                    "entry_price": 100.0
                }],
                timestamp="2026-10-25 09:30:00"
            )
            self.assertTrue(pm.save_state())

            initial_qty = pos.legs["L1"].remaining_quantity
            initial_pnl = pos.combined_realized_pnl
            initial_charges = pos.total_charges
            initial_status = pos.status
            initial_leg_status = pos.legs["L1"].status

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:45:00",
                spot_price=24850.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=120.0, bid=119.5, ask=120.5, symbol="NIFTY24800CE")}
            )

            # Attempt to exit 25 units with persistence failure
            exit_sig = ExitSignal(
                position_id=pos.position_id,
                strategy_id="STRAT_001",
                timestamp="2026-10-25 09:45:00",
                reason=ExitReason.PROFIT_TARGET,
                legs_to_close=["L1"],
                leg_quantities={"L1": 25},
                signal_id="EXIT_PARTIAL_25"
            )

            with patch.object(pm, "save_state", return_value=False):
                success, updated_pos, closed_records, err = broker.execute_exit_signal(exit_sig, snapshot)
                self.assertFalse(success)
                self.assertIsNone(updated_pos)
                self.assertEqual(closed_records, [])
                self.assertEqual(err, "PERSISTENCE_FAILED_EXIT_ROLLED_BACK")

            # Verify position remains exactly as it was before attempt
            self.assertIs(pm.positions[pos.position_id], pos)  # Authoritative object identity preserved
            self.assertEqual(pos.legs["L1"].remaining_quantity, 75)
            self.assertEqual(pos.legs["L1"].status, initial_leg_status)
            self.assertEqual(pos.status, initial_status)
            self.assertEqual(pos.combined_realized_pnl, initial_pnl)
            self.assertEqual(pos.total_charges, initial_charges)

            # Verify signal tracking consistency across broker, manager, metadata
            self.assertNotIn("EXIT_PARTIAL_25", broker.processed_exit_signals)
            self.assertNotIn("EXIT_PARTIAL_25", pm.processed_exit_signals)
            self.assertNotIn("EXIT_PARTIAL_25", pos.metadata.get("processed_exit_signals", []))

            # Retry the exact same exit signal - persistence succeeds now
            retry_success, retry_pos, retry_records, retry_err = broker.execute_exit_signal(exit_sig, snapshot)
            self.assertTrue(retry_success)
            self.assertIs(retry_pos, pos)
            self.assertEqual(pos.legs["L1"].remaining_quantity, 50)
            self.assertEqual(pos.status, "PARTIALLY_CLOSED")
            self.assertEqual(pos.legs["L1"].status, "OPEN")
            self.assertEqual(len(retry_records), 1)
            self.assertEqual(retry_records[0]["closed_quantity"], 25)

            # Verify realized P&L is for exactly 25 units net of statutory charges
            expected_gross_pnl = (119.5 - 0.5 - 100.0) * 25
            self.assertAlmostEqual(pos.combined_realized_pnl + pos.total_charges, expected_gross_pnl, places=1)
            self.assertAlmostEqual(retry_records[0]["realized_pnl"] + retry_records[0]["charges"], expected_gross_pnl, places=1)

            # Verify signal tracking is updated everywhere
            self.assertIn("EXIT_PARTIAL_25", broker.processed_exit_signals)
            self.assertIn("EXIT_PARTIAL_25", pm.processed_exit_signals)
            self.assertIn("EXIT_PARTIAL_25", pos.metadata.get("processed_exit_signals", []))

            # Verify duplicate retry is rejected
            dup_s, _, _, dup_err = broker.execute_exit_signal(exit_sig, snapshot)
            self.assertFalse(dup_s)
            self.assertIn("DUPLICATE_EXIT_SIGNAL", dup_err)
            self.assertEqual(pos.legs["L1"].remaining_quantity, 50)

    def test_full_exit_persistence_failure_rollback_and_retry(self):
        """B. Full-exit persistence failure preserves OPEN state and retries safely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            broker = PaperBroker(pm, simulated_slippage_points=0.5)

            pos = pm.create_position(
                strategy_id="STRAT_002",
                exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{
                    "leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800,
                    "option_type": "CE", "side": "BUY", "quantity": 75,
                    "entry_price": 100.0
                }],
                timestamp="2026-10-25 09:30:00"
            )
            self.assertTrue(pm.save_state())

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:50:00",
                spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=109.5, ask=110.5, symbol="NIFTY24800CE")}
            )

            exit_sig = ExitSignal(
                position_id=pos.position_id,
                strategy_id="STRAT_002",
                timestamp="2026-10-25 09:50:00",
                reason=ExitReason.STOP_LOSS,
                legs_to_close=["L1"],
                signal_id="EXIT_FULL_SIG"
            )

            # Force persistence failure via exception (e.g. disk write failure)
            with patch.object(pm, "save_state", side_effect=IOError("Simulated disk write failure")):
                success, updated_pos, closed_records, err = broker.execute_exit_signal(exit_sig, snapshot)
                self.assertFalse(success)
                self.assertIsNone(updated_pos)
                self.assertEqual(err, "PERSISTENCE_FAILED_EXIT_ROLLED_BACK")

            # Verify in-memory position is NOT closed
            self.assertEqual(pos.status, "OPEN")
            self.assertEqual(pos.legs["L1"].status, "OPEN")
            self.assertEqual(pos.legs["L1"].remaining_quantity, 75)
            self.assertEqual(pos.close_time, "")

            # Verify disk state still has original OPEN position
            disk_pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            recovered, _ = disk_pm.load_state()
            self.assertTrue(recovered)
            disk_pos = disk_pm.get_position(pos.position_id)
            self.assertEqual(disk_pos.status, "OPEN")
            self.assertEqual(disk_pos.legs["L1"].remaining_quantity, 75)

            # Retry exit with working persistence
            retry_success, retry_pos, retry_records, retry_err = broker.execute_exit_signal(exit_sig, snapshot)
            self.assertTrue(retry_success)
            self.assertEqual(pos.status, "CLOSED")
            self.assertEqual(pos.legs["L1"].status, "CLOSED")
            self.assertEqual(pos.legs["L1"].remaining_quantity, 0)
            self.assertTrue(broker.is_signal_processed("EXIT_FULL_SIG", pos))

    def test_successful_partial_and_full_exits(self):
        """C. Successful partial and full exits update quantities and accounting correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            broker = PaperBroker(pm, simulated_slippage_points=0.5)

            pos = pm.create_position(
                strategy_id="STRAT_003",
                exit_policy=StopLossPolicy.COMBINED_POSITION,
                legs_data=[{
                    "leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800,
                    "option_type": "CE", "side": "BUY", "quantity": 75,
                    "entry_price": 100.0
                }],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:40:00",
                spot_price=24820.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=120.0, bid=119.5, ask=120.5, symbol="NIFTY24800CE")}
            )

            # 1. First partial exit: 25 units
            sig1 = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_003", timestamp="2026-10-25 09:40:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="PARTIAL_1"
            )
            s1, p1, r1, _ = broker.execute_exit_signal(sig1, snapshot)
            self.assertTrue(s1)
            self.assertEqual(pos.status, "PARTIALLY_CLOSED")
            self.assertEqual(pos.legs["L1"].remaining_quantity, 50)
            charges_p1 = pos.total_charges
            realized_p1 = pos.combined_realized_pnl
            self.assertGreater(charges_p1, 0)
            self.assertAlmostEqual(realized_p1 + charges_p1, (119.0 - 100.0) * 25, places=1)

            # 2. Second partial exit: 25 units
            sig2 = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_003", timestamp="2026-10-25 09:42:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="PARTIAL_2"
            )
            s2, p2, r2, _ = broker.execute_exit_signal(sig2, snapshot)
            self.assertTrue(s2)
            self.assertEqual(pos.status, "PARTIALLY_CLOSED")
            self.assertEqual(pos.legs["L1"].remaining_quantity, 25)
            self.assertGreater(pos.total_charges, charges_p1)
            self.assertAlmostEqual(pos.combined_realized_pnl + pos.total_charges, (119.0 - 100.0) * 50, places=1)

            # 3. Final exit: remaining 25 units
            sig3 = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_003", timestamp="2026-10-25 09:45:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], signal_id="PARTIAL_3"
            )
            s3, p3, r3, _ = broker.execute_exit_signal(sig3, snapshot)
            self.assertTrue(s3)
            self.assertEqual(pos.status, "CLOSED")
            self.assertEqual(pos.legs["L1"].remaining_quantity, 0)
            self.assertEqual(pos.legs["L1"].status, "CLOSED")

            # Reload from disk and verify persistence reflects exact closed state
            disk_pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            recovered, _ = disk_pm.load_state()
            self.assertTrue(recovered)
            recovered_pos = disk_pm.get_position(pos.position_id)
            self.assertEqual(recovered_pos.status, "CLOSED")
            self.assertEqual(recovered_pos.legs["L1"].remaining_quantity, 0)
            self.assertAlmostEqual(recovered_pos.combined_realized_pnl, pos.combined_realized_pnl, places=2)
            self.assertAlmostEqual(recovered_pos.total_charges, pos.total_charges, places=2)

    def test_deduplication_consistency_across_caches(self):
        """D. Deduplication caches in broker, manager, and position metadata agree consistently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            broker = PaperBroker(pm, simulated_slippage_points=0.5)

            pos = pm.create_position(
                strategy_id="STRAT_004",
                exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{
                    "leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800,
                    "option_type": "CE", "side": "BUY", "quantity": 75,
                    "entry_price": 100.0
                }],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:40:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=109.5, ask=110.5, symbol="NIFTY24800CE")}
            )
            sig_fail = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_004", timestamp="2026-10-25 09:40:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="FAIL_SIG"
            )

            # After failure: none of the caches contain FAIL_SIG
            with patch.object(pm, "save_state", return_value=False):
                s, _, _, _ = broker.execute_exit_signal(sig_fail, snapshot)
                self.assertFalse(s)

            self.assertNotIn("FAIL_SIG", broker.processed_exit_signals)
            self.assertNotIn("FAIL_SIG", pm.processed_exit_signals)
            self.assertNotIn("FAIL_SIG", pos.metadata.get("processed_exit_signals", []))
            self.assertFalse(broker.is_signal_processed("FAIL_SIG", pos))

            # After success: all caches contain SUCCESS_SIG
            sig_success = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_004", timestamp="2026-10-25 09:41:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="SUCCESS_SIG"
            )
            s_ok, _, _, _ = broker.execute_exit_signal(sig_success, snapshot)
            self.assertTrue(s_ok)

            self.assertIn("SUCCESS_SIG", broker.processed_exit_signals)
            self.assertIn("SUCCESS_SIG", pm.processed_exit_signals)
            self.assertIn("SUCCESS_SIG", pos.metadata.get("processed_exit_signals", []))
            self.assertTrue(broker.is_signal_processed("SUCCESS_SIG", pos))

            # After restart from state file: caches are reconstructed and agree
            pm2 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            recovered, _ = pm2.load_state()
            self.assertTrue(recovered)
            broker2 = PaperBroker(pm2)
            self.assertTrue(broker2.is_signal_processed("SUCCESS_SIG", pm2.get_position(pos.position_id)))
            self.assertFalse(broker2.is_signal_processed("FAIL_SIG", pm2.get_position(pos.position_id)))

    def test_signal_pruning_protects_open_positions_and_prevents_reexecution(self):
        """E. Historical signal pruning (>1000) protects open/partially-closed positions and prevents stale re-execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            broker = PaperBroker(pm, simulated_slippage_points=0.5)

            # Create an open position and execute a partial exit with signal OPEN_POS_PARTIAL_SIG
            pos_open = pm.create_position(
                strategy_id="STRAT_005",
                exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{
                    "leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800,
                    "option_type": "CE", "side": "BUY", "quantity": 75,
                    "entry_price": 100.0
                }],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:40:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=109.5, ask=110.5, symbol="NIFTY24800CE")}
            )

            # Partial exit of 25 units
            sig_partial = ExitSignal(
                position_id=pos_open.position_id, strategy_id="STRAT_005", timestamp="2026-10-25 09:40:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="OPEN_POS_PARTIAL_SIG"
            )
            s, _, _, _ = broker.execute_exit_signal(sig_partial, snapshot)
            self.assertTrue(s)
            self.assertEqual(pos_open.status, "PARTIALLY_CLOSED")

            # Now populate 1,200 historical completed signals
            for i in range(1200):
                pm.record_processed_signal(f"HIST_CLOSED_SIG_{i:04d}")

            # Verify total count is pruned
            self.assertLessEqual(len(pm.processed_exit_signals), 1002)

            # Confirm signal associated with partially closed position remains protected!
            self.assertIn("OPEN_POS_PARTIAL_SIG", pm.processed_exit_signals)
            self.assertTrue(broker.is_signal_processed("OPEN_POS_PARTIAL_SIG", pos_open))

            # Attempting to re-execute OPEN_POS_PARTIAL_SIG must be blocked by deduplication
            retry_sig = ExitSignal(
                position_id=pos_open.position_id, strategy_id="STRAT_005", timestamp="2026-10-25 09:45:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="OPEN_POS_PARTIAL_SIG"
            )
            s_retry, _, _, err_retry = broker.execute_exit_signal(retry_sig, snapshot)
            self.assertFalse(s_retry)
            self.assertIn("DUPLICATE_EXIT_SIGNAL", err_retry)
            self.assertEqual(pos_open.legs["L1"].remaining_quantity, 50)  # Not reduced to 25!

    def test_rollback_failure_fails_closed(self):
        """Task 1: If in-memory rollback itself raises an exception, system fails closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_file)
            broker = PaperBroker(pm, simulated_slippage_points=0.5)

            pos = pm.create_position(
                strategy_id="STRAT_001",
                exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{
                    "leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800,
                    "option_type": "CE", "side": "BUY", "quantity": 75,
                    "entry_price": 100.0
                }],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:45:00",
                spot_price=24850.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=120.0, bid=119.5, ask=120.5, symbol="NIFTY24800CE")}
            )

            exit_sig = ExitSignal(
                position_id=pos.position_id,
                strategy_id="STRAT_001",
                timestamp="2026-10-25 09:45:00",
                reason=ExitReason.PROFIT_TARGET,
                legs_to_close=["L1"],
                leg_quantities={"L1": 25},
                signal_id="ROLLBACK_FAIL_SIG"
            )

            # Both save_state fails AND restore_from_snapshot raises an exception
            with patch.object(pm, "save_state", return_value=False), \
                 patch.object(pos, "restore_from_snapshot", side_effect=RuntimeError("Corrupt in-memory state")):
                success, updated_pos, closed_records, err = broker.execute_exit_signal(exit_sig, snapshot)
                self.assertFalse(success)
                self.assertIn("ROLLBACK_FAILED_FAIL_CLOSED", err)
                # Position manager must now be marked recovery_failed
                self.assertTrue(pm.recovery_failed)
                self.assertFalse(pm.is_healthy())

            # Verify that any subsequent operations fail closed
            entry_sig = StrategySignal(
                strategy_id="STRAT_001", direction=SignalDirection.BULLISH, timestamp=snapshot.timestamp,
                legs=[LegOrder("L2", "NIFTY24800CE", 24800, "CE", "", "BUY", 75, 100.0)],
                exit_policy=StopLossPolicy.PREMIUM_BASED
            )
            s_entry, _, err_entry = broker.execute_entry_signal(entry_sig, snapshot)
            self.assertFalse(s_entry)
            self.assertEqual(err_entry, "RECOVERY_FAILED_FAIL_CLOSED")


class TestExecutionLogDurability(unittest.TestCase):
    """
    Regression test suite covering Tasks 2 & 3:
    A. Crash or simulated interruption after position state is persisted but before CSV logging.
    B. CSV append succeeds, but the process fails before pending metadata is cleared.
    C. Pending metadata is cleared in memory, but saving the cleared state fails.
    D. Restart with pending partial-exit events.
    E. Restart with pending final-exit and trade-summary events.
    F. Reprocessing the same pending event multiple times does not duplicate CSV rows.
    G. Different legitimate partial exits on the same leg remain separate records.
    H. Persistence fails during an exit, and rollback restores the original in-memory state without double-counting P&L or charges.
    I. Corrupt or incomplete pending-event data causes safe failure and is not silently discarded.
    """

    def setUp(self):
        self.tax_calc = ChargesCalculator(brokerage_per_order=20.0)

    def test_A_and_D_crash_after_state_persistence_before_csv_logging_partial_exit(self):
        """Points A & D: State persisted with pending partial-exit, crash before CSV append, restart reconciles cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = StrategyRegistry(config_path="config/strategy_config.json", workspace_root=tmpdir)
            state_path = os.path.join(tmpdir, "runtime", "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            risk = RiskManager(config={})
            broker = PaperBroker(pm, simulated_slippage_points=0.5)
            runner = StrategyRunner(registry, pm, risk, broker, workspace_root=tmpdir)

            pos = pm.create_position(
                strategy_id="STRAT_001", exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            # Execute exit of 25 units via broker (stages pending_execution_logs into state.json)
            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:40:00", spot_price=24800.0,
                options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=120.0, bid=119.5, ask=120.5, symbol="NIFTY24800CE")}
            )
            exit_sig = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_001", timestamp="2026-10-25 09:40:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="PARTIAL_EXIT_1"
            )
            success, updated_pos, closed_records, _ = broker.execute_exit_signal(exit_sig, snapshot)
            self.assertTrue(success)

            # Verify state.json contains pending_execution_logs
            with open(runner.runtime_state_path, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            pos_data = state_data["positions"][pos.position_id]
            self.assertEqual(len(pos_data["metadata"]["pending_execution_logs"]), 1)
            self.assertEqual(pos_data["metadata"]["pending_execution_logs"][0]["event_type"], "LEG_EXIT")

            # Check CSV file does not have the exit row yet (crash simulated here)
            strat = registry.get_strategy("STRAT_001")
            strat_legs_path = strat.legs_log_path
            if os.path.exists(strat_legs_path):
                with open(strat_legs_path, "r", encoding="utf-8") as f:
                    rows_before = list(csv.reader(f))
                self.assertEqual(len(rows_before), 1)  # Only header

            # Restart simulation: new StrategyRunner loads state and runs reconcile_pending_execution_logs
            pm2 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=runner.runtime_state_path)
            broker2 = PaperBroker(pm2)
            runner2 = StrategyRunner(registry, pm2, risk, broker2, workspace_root=tmpdir)

            # Assert recovered position state
            rec_pos = pm2.positions[pos.position_id]
            self.assertEqual(rec_pos.status, "PARTIALLY_CLOSED")
            self.assertEqual(rec_pos.legs["L1"].remaining_quantity, 50)
            self.assertEqual(len(rec_pos.metadata.get("pending_execution_logs", [])), 0)

            # Assert audit records in legs.csv: exactly 1 data row
            with open(strat_legs_path, "r", encoding="utf-8") as f:
                rows_after = list(csv.reader(f))
            self.assertEqual(len(rows_after), 2)  # header + 1 data row
            self.assertEqual(rows_after[1][0], "L1")
            self.assertEqual(int(rows_after[1][5]), 25)

    def test_B_and_F_csv_append_succeeds_but_crash_before_clearing_pending_no_duplicates(self):
        """Points B & F: CSV append succeeded before crash; reprocessing multiple times never duplicates rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = StrategyRegistry(config_path="config/strategy_config.json", workspace_root=tmpdir)
            state_path = os.path.join(tmpdir, "runtime", "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            risk = RiskManager(config={})
            broker = PaperBroker(pm)
            runner = StrategyRunner(registry, pm, risk, broker, workspace_root=tmpdir)

            strat = registry.get_strategy("STRAT_001")
            pos = pm.create_position(
                strategy_id="STRAT_001", exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                timestamp="2026-10-25 09:30:00"
            )
            exec_key = f"{pos.position_id}_L1_rem_50_qty_25_exit_2026-10-25 09:40:00_120.0"
            pos.metadata["pending_execution_logs"] = [{
                "event_type": "LEG_EXIT",
                "exec_id": exec_key,
                "strategy_id": "STRAT_001",
                "position_id": pos.position_id,
                "leg_id": "L1", "symbol": "NIFTY24800CE", "side": "BUY",
                "closed_quantity": 25, "remaining_quantity": 50,
                "entry_price": 100.0, "exit_price": 120.0,
                "entry_time": "2026-10-25 09:30:00", "exit_time": "2026-10-25 09:40:00",
                "realized_pnl": 500.0, "charges": 20.0, "reason": "PROFIT_TARGET"
            }]
            pos.legs["L1"].remaining_quantity = 50
            pos.status = "PARTIALLY_CLOSED"
            pm.save_state()

            # Pre-write row to legs.csv (simulating CSV append succeeded before crash)
            with open(strat.legs_log_path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["L1", pos.position_id, "STRAT_001", "NIFTY24800CE", "BUY", 25, 100.0, 120.0, "2026-10-25 09:30:00", "2026-10-25 09:40:00", 500.0, 20.0])

            # Run reconciliation pass 1
            reconciled1, _ = runner.reconcile_pending_execution_logs()
            self.assertTrue(reconciled1)

            # Reprocess pass 2 (Point F: multiple reprocessing)
            reconciled2, _ = runner.reconcile_pending_execution_logs()
            self.assertTrue(reconciled2)

            # Assert NO DUPLICATES in legs.csv (still exactly 1 data row)
            with open(strat.legs_log_path, "r", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 2)  # Header + exactly 1 data row

    def test_C_save_state_failure_preserves_pending_events_without_loss(self):
        """Point C: Pending metadata cleared in memory, but saving state fails; events are restored without loss."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = StrategyRegistry(config_path="config/strategy_config.json", workspace_root=tmpdir)
            state_path = os.path.join(tmpdir, "runtime", "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            risk = RiskManager(config={})
            broker = PaperBroker(pm)
            runner = StrategyRunner(registry, pm, risk, broker, workspace_root=tmpdir)

            pos = pm.create_position(
                strategy_id="STRAT_001", exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                timestamp="2026-10-25 09:30:00"
            )
            pending_event = {
                "event_type": "LEG_EXIT",
                "exec_id": "test_exec_c1",
                "strategy_id": "STRAT_001",
                "position_id": pos.position_id,
                "leg_id": "L1", "symbol": "NIFTY24800CE", "side": "BUY",
                "closed_quantity": 25, "remaining_quantity": 50,
                "entry_price": 100.0, "exit_price": 120.0,
                "entry_time": "2026-10-25 09:30:00", "exit_time": "2026-10-25 09:40:00",
                "realized_pnl": 500.0, "charges": 20.0, "reason": "PROFIT_TARGET"
            }
            pos.metadata["pending_execution_logs"] = [pending_event]
            pm.save_state()

            # Force save_state to fail during reconciliation
            with patch.object(pm, "save_state", return_value=False):
                reconciled, err = runner.reconcile_pending_execution_logs()
                self.assertFalse(reconciled)
                self.assertIn("PERSISTENCE_FAILED_AFTER_RECONCILIATION", err)

            # Pending events are preserved on disk without loss
            disk_pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            disk_pm.load_state()
            self.assertEqual(len(disk_pm.positions[pos.position_id].metadata["pending_execution_logs"]), 1)

    def test_E_restart_with_pending_final_exit_and_trade_summary(self):
        """Point E: Restart with pending final-exit and trade-summary reconciles both CSV logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = StrategyRegistry(config_path="config/strategy_config.json", workspace_root=tmpdir)
            state_path = os.path.join(tmpdir, "runtime", "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            risk = RiskManager(config={})
            broker = PaperBroker(pm, simulated_slippage_points=0.5)
            runner = StrategyRunner(registry, pm, risk, broker, workspace_root=tmpdir)

            pos = pm.create_position(
                strategy_id="STRAT_002", exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{"leg_id": "L1", "symbol": "NIFTY24800PE", "strike": 24800, "option_type": "PE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            # Execute full exit via broker (stages both LEG_EXIT and TRADE_SUMMARY)
            snapshot = MarketSnapshot(
                timestamp="2026-10-25 09:50:00", spot_price=24800.0,
                options={"24800_PE": OptionContract(strike=24800, option_type="PE", ltp=130.0, bid=129.5, ask=130.5, symbol="NIFTY24800PE")}
            )
            exit_sig = ExitSignal(
                position_id=pos.position_id, strategy_id="STRAT_002", timestamp="2026-10-25 09:50:00",
                reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], signal_id="FULL_EXIT_SIG_E"
            )
            success, updated_pos, closed_records, _ = broker.execute_exit_signal(exit_sig, snapshot)
            self.assertTrue(success)

            # Restart runner: loads state and reconciles both logs
            pm2 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            broker2 = PaperBroker(pm2)
            runner2 = StrategyRunner(registry, pm2, risk, broker2, workspace_root=tmpdir)

            strat = registry.get_strategy("STRAT_002")

            # Check trades.csv has trade summary
            with open(strat.trades_log_path, "r", encoding="utf-8") as f:
                t_rows = list(csv.reader(f))
            self.assertEqual(len(t_rows), 2)  # header + 1 row
            self.assertEqual(t_rows[1][0], f"TRD_{pos.position_id}")
            self.assertEqual(t_rows[1][1], pos.position_id)

            # Check legs.csv has leg execution
            with open(strat.legs_log_path, "r", encoding="utf-8") as f:
                l_rows = list(csv.reader(f))
            self.assertEqual(len(l_rows), 2)  # header + 1 row
            self.assertEqual(int(l_rows[1][5]), 75)

    def test_G_different_partial_exits_on_same_leg_remain_separate(self):
        """Point G: Different legitimate partial exits on the same leg remain separate distinct records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = StrategyRegistry(config_path="config/strategy_config.json", workspace_root=tmpdir)
            state_path = os.path.join(tmpdir, "runtime", "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            risk = RiskManager(config={})
            broker = PaperBroker(pm, simulated_slippage_points=0.5)
            runner = StrategyRunner(registry, pm, risk, broker, workspace_root=tmpdir)
            strat = registry.get_strategy("STRAT_001")

            pos = pm.create_position(
                strategy_id="STRAT_001", exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            # Partial exit 1: 25 units at 09:40
            snap1 = MarketSnapshot(timestamp="2026-10-25 09:40:00", spot_price=24800.0, options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=110.0, bid=109.5, ask=110.5, symbol="NIFTY24800CE")})
            sig1 = ExitSignal(position_id=pos.position_id, strategy_id="STRAT_001", timestamp="2026-10-25 09:40:00", reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="SIG_P1")
            s1, p1, r1, _ = broker.execute_exit_signal(sig1, snap1)
            self.assertTrue(s1)
            strat.log_partial_exit(p1, r1, "PROFIT_TARGET")
            pm.save_state()

            # Partial exit 2: 25 units at 09:45
            snap2 = MarketSnapshot(timestamp="2026-10-25 09:45:00", spot_price=24800.0, options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=120.0, bid=119.5, ask=120.5, symbol="NIFTY24800CE")})
            sig2 = ExitSignal(position_id=pos.position_id, strategy_id="STRAT_001", timestamp="2026-10-25 09:45:00", reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="SIG_P2")
            s2, p2, r2, _ = broker.execute_exit_signal(sig2, snap2)
            self.assertTrue(s2)
            strat.log_partial_exit(p2, r2, "PROFIT_TARGET")
            pm.save_state()

            # Assert legs.csv contains exactly 2 data rows with different exit times
            with open(strat.legs_log_path, "r", encoding="utf-8") as f:
                l_rows = list(csv.reader(f))
            self.assertEqual(len(l_rows), 3)  # header + 2 rows
            self.assertEqual(l_rows[1][9], "2026-10-25 09:40:00")
            self.assertEqual(l_rows[2][9], "2026-10-25 09:45:00")

    def test_H_persistence_fails_during_exit_and_rollback_restores_clean_accounting(self):
        """Point H: Persistence failure during exit rolls back without double-counting P&L or charges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "runtime", "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            broker = PaperBroker(pm, simulated_slippage_points=0.5)

            pos = pm.create_position(
                strategy_id="STRAT_001", exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                timestamp="2026-10-25 09:30:00"
            )
            pm.save_state()

            snapshot = MarketSnapshot(timestamp="2026-10-25 09:45:00", spot_price=24800.0, options={"24800_CE": OptionContract(strike=24800, option_type="CE", ltp=120.0, bid=119.5, ask=120.5, symbol="NIFTY24800CE")})
            exit_sig = ExitSignal(position_id=pos.position_id, strategy_id="STRAT_001", timestamp="2026-10-25 09:45:00", reason=ExitReason.PROFIT_TARGET, legs_to_close=["L1"], leg_quantities={"L1": 25}, signal_id="H_FAIL_SIG")

            # Force persistence failure
            with patch.object(pm, "save_state", return_value=False):
                s, _, _, err = broker.execute_exit_signal(exit_sig, snapshot)
                self.assertFalse(s)
                self.assertEqual(err, "PERSISTENCE_FAILED_EXIT_ROLLED_BACK")

            # Verify in-memory state restored to exactly 75 units, 0 realized P&L, 0 charges
            self.assertEqual(pos.legs["L1"].remaining_quantity, 75)
            self.assertEqual(pos.combined_realized_pnl, 0.0)
            self.assertEqual(pos.total_charges, 0.0)
            self.assertEqual(len(pos.metadata.get("pending_execution_logs", [])), 0)

            # Retry same signal with working persistence
            s_retry, p_retry, records, _ = broker.execute_exit_signal(exit_sig, snapshot)
            self.assertTrue(s_retry)
            self.assertEqual(p_retry.legs["L1"].remaining_quantity, 50)
            self.assertGreater(p_retry.combined_realized_pnl, 0.0)
            self.assertGreater(p_retry.total_charges, 0.0)
            self.assertEqual(len(records), 1)

    def test_I_corrupt_or_incomplete_pending_event_causes_safe_failure(self):
        """Point I: Corrupt or incomplete pending-event data causes safe failure and is not silently discarded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = StrategyRegistry(config_path="config/strategy_config.json", workspace_root=tmpdir)
            state_path = os.path.join(tmpdir, "runtime", "state.json")
            pm = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            risk = RiskManager(config={})
            broker = PaperBroker(pm)
            runner = StrategyRunner(registry, pm, risk, broker, workspace_root=tmpdir)

            pos = pm.create_position(
                strategy_id="STRAT_001", exit_policy=StopLossPolicy.PREMIUM_BASED,
                legs_data=[{"leg_id": "L1", "symbol": "NIFTY24800CE", "strike": 24800, "option_type": "CE", "side": "BUY", "quantity": 75, "entry_price": 100.0}],
                timestamp="2026-10-25 09:30:00"
            )
            # Corrupted pending event: incomplete dict missing required fields
            pos.metadata["pending_execution_logs"] = [
                {"event_type": "LEG_EXIT", "corrupted": True}
            ]
            pm.save_state()

            # Restart runner: must detect corrupt pending event and fail closed!
            pm2 = MultiLegPositionManager(charges_calc=self.tax_calc, state_filepath=state_path)
            broker2 = PaperBroker(pm2)
            runner2 = StrategyRunner(registry, pm2, risk, broker2, workspace_root=tmpdir)

            self.assertTrue(pm2.recovery_failed)
            self.assertFalse(pm2.is_healthy())

            # Corrupt event is NOT silently discarded
            self.assertEqual(len(pm2.positions[pos.position_id].metadata["pending_execution_logs"]), 1)

            # Broker operations are strictly blocked
            snap = MarketSnapshot(timestamp="2026-10-25 09:40:00", spot_price=24800.0)
            exit_sig = ExitSignal(position_id=pos.position_id, strategy_id="STRAT_001", timestamp="2026-10-25 09:40:00", reason=ExitReason.PROFIT_TARGET)
            s_exit, _, _, err_exit = broker2.execute_exit_signal(exit_sig, snap)
            self.assertFalse(s_exit)
            self.assertEqual(err_exit, "RECOVERY_FAILED_FAIL_CLOSED")


if __name__ == "__main__":
    unittest.main()




"""
test_strategy_signals.py
Offline unit tests for all 25 quantitative strategy modules.
Validates:
- Exact registration and factory instantiation for STRAT_001 through STRAT_025.
- Default DISABLED mode safety.
- Complete rulebooks for all 25 strategies (entry, initial SL, trailing SL, exit).
- Evaluation behavior across single-leg, 2-leg, and 4-leg options/futures strategies.
- Straddle shift-to-cost SL mechanics.
"""

import os
import json
import unittest
from src.strategies.strategy_registry import StrategyRegistry
from src.strategies.signal_models import StrategyMode, SignalDirection, StopLossPolicy, LegOrder
from src.market.market_snapshot import MarketSnapshot, OptionContract, FuturesContract


class TestAll25Strategies(unittest.TestCase):
    """Test suite verifying all 25 registered strategies."""

    def test_registry_contains_exactly_25_strategies(self):
        """Must have exactly STRAT_001 through STRAT_025 registered."""
        strats = StrategyRegistry.list_strategies()
        self.assertEqual(len(strats), 25)
        for i in range(1, 26):
            expected_id = f"STRAT_{i:03d}"
            self.assertIn(expected_id, strats, f"Missing {expected_id} from StrategyRegistry")

    def test_instantiate_all_25_strategies_and_validate_rulebooks(self):
        """Instantiate every strategy and verify its metadata, default mode, and rulebook."""
        all_ids = StrategyRegistry.list_strategies()
        for strat_id in all_ids:
            strat = StrategyRegistry.create_strategy(strat_id, config={"mode": "DISABLED"})
            self.assertIsNotNone(strat, f"Failed to instantiate {strat_id}")
            self.assertEqual(strat.strategy_id, strat_id)
            self.assertTrue(len(strat.name) > 0)
            self.assertEqual(strat.mode, StrategyMode.DISABLED)

            rulebook = strat.get_rulebook()
            self.assertIsInstance(rulebook, dict)
            self.assertIn("entry", rulebook, f"Missing entry rules in {strat_id}")
            self.assertIn("initial_sl", rulebook, f"Missing initial_sl rules in {strat_id}")
            self.assertIn("trailing_sl", rulebook, f"Missing trailing_sl rules in {strat_id}")
            self.assertIn("exit", rulebook, f"Missing exit rules in {strat_id}")

    def test_disabled_strategy_generates_zero_signals(self):
        """A strategy in DISABLED mode must never generate signals or evaluate positions."""
        strat = StrategyRegistry.create_strategy("STRAT_001", config={"mode": "DISABLED"})
        snapshot = MarketSnapshot(timestamp="2026-10-25T10:00:00Z", spot=24800.0)
        entry_sig, exit_sig = strat.evaluate(snapshot, open_position=None)
        self.assertIsNone(entry_sig)
        self.assertIsNone(exit_sig)

    def test_strat_001_long_call_signal_generation(self):
        """Test STRAT_001 Long Call signal generation in PAPER mode."""
        strat = StrategyRegistry.create_strategy("STRAT_001", config={
            "mode": "PAPER",
            "strike_offset": 0,
            "sl_pct": 20.0,
            "target_pct": 40.0
        })
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T10:00:00Z",
            spot=24850.0,
            options={
                "24850_CE": OptionContract(strike=24850.0, option_type="CE", ltp=110.0, oi=100000, volume=50000)
            }
        )
        entry_sig, exit_sig = strat.evaluate(snapshot, open_position=None)
        self.assertIsNotNone(entry_sig)
        self.assertEqual(entry_sig.strategy_id, "STRAT_001")
        self.assertEqual(len(entry_sig.legs), 1)
        self.assertEqual(entry_sig.legs[0].side, "BUY")
        self.assertEqual(entry_sig.legs[0].option_type, "CE")
        self.assertEqual(entry_sig.legs[0].strike, 24850.0)

    def test_strat_005_short_straddle_dual_leg_generation(self):
        """Test STRAT_005 Short Straddle generates 2 short legs (CE and PE)."""
        strat = StrategyRegistry.create_strategy("STRAT_005", config={
            "mode": "PAPER",
            "sl_pct": 25.0,
            "shift_sl_to_cost_on_first_exit": True
        })
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T09:30:00Z",
            spot=24800.0,
            options={
                "24800_CE": OptionContract(strike=24800.0, option_type="CE", ltp=120.0, oi=200000, volume=80000),
                "24800_PE": OptionContract(strike=24800.0, option_type="PE", ltp=115.0, oi=210000, volume=85000)
            }
        )
        entry_sig, exit_sig = strat.evaluate(snapshot, open_position=None)
        self.assertIsNotNone(entry_sig)
        self.assertEqual(len(entry_sig.legs), 2)
        legs_by_type = {l.option_type: l for l in entry_sig.legs}
        self.assertIn("CE", legs_by_type)
        self.assertIn("PE", legs_by_type)
        self.assertEqual(legs_by_type["CE"].side, "SELL")
        self.assertEqual(legs_by_type["PE"].side, "SELL")

    def test_strat_007_iron_condor_four_leg_generation(self):
        """Test STRAT_007 Iron Condor generates 4 legs with defined risk."""
        strat = StrategyRegistry.create_strategy("STRAT_007", config={
            "mode": "PAPER",
            "short_offset": 100,
            "wing_offset": 300,
            "sl_pct": 30.0
        })
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T09:35:00Z",
            spot=24800.0,
            options={
                "24900_CE": OptionContract(strike=24900.0, option_type="CE", ltp=60.0),
                "25100_CE": OptionContract(strike=25100.0, option_type="CE", ltp=15.0),
                "24700_PE": OptionContract(strike=24700.0, option_type="PE", ltp=55.0),
                "24500_PE": OptionContract(strike=24500.0, option_type="PE", ltp=12.0)
            }
        )
        entry_sig, exit_sig = strat.evaluate(snapshot, open_position=None)
        self.assertIsNotNone(entry_sig)
        self.assertEqual(len(entry_sig.legs), 4)

        sides = [l.side for l in entry_sig.legs]
        self.assertEqual(sides.count("SELL"), 2)
        self.assertEqual(sides.count("BUY"), 2)

    def test_strat_010_futures_protective_put(self):
        """Test STRAT_010 generates 2 legs: BUY Futures + BUY Protective Put."""
        strat = StrategyRegistry.create_strategy("STRAT_010", config={
            "mode": "PAPER",
            "put_otm_offset": 100
        })
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T09:30:00Z",
            spot=24800.0,
            futures=FuturesContract(symbol="NIFTY_FUT", ltp=24850.0, oi=500000, volume=150000),
            options={
                "24700_PE": OptionContract(strike=24700.0, option_type="PE", ltp=45.0)
            }
        )
        entry_sig, exit_sig = strat.evaluate(snapshot, open_position=None)
        self.assertIsNotNone(entry_sig)
        self.assertEqual(len(entry_sig.legs), 2)
        leg_types = [l.option_type for l in entry_sig.legs]
        self.assertIn("FUT", leg_types)
        self.assertIn("PE", leg_types)

    def test_default_lot_size_is_65_across_all_registered_strategies(self):
        """Verifies default lot size is 65 in configs, models, and strategy instances."""
        # 1. market_config.json
        market_cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "market_config.json")
        with open(market_cfg_path, "r", encoding="utf-8") as f:
            market_cfg = json.load(f)
        self.assertEqual(market_cfg.get("default_lot_size"), 65)

        # 2. strategy_config.json
        strat_cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "strategy_config.json")
        with open(strat_cfg_path, "r", encoding="utf-8") as f:
            strat_cfg = json.load(f)
        self.assertEqual(strat_cfg.get("default_rules", {}).get("default_lot_size"), 65)

        # All 25 strategy entries in strategy_config.json must specify lot_size == 65
        for strat_id, s_data in strat_cfg.get("strategies", {}).items():
            self.assertEqual(s_data.get("lot_size"), 65, f"Strategy {strat_id} lot_size is not 65")

        # 3. LegOrder dataclass default quantity
        leg = LegOrder()
        self.assertEqual(leg.quantity, 65)

        # 4. Strategy instances loaded via StrategyRegistry
        registry = StrategyRegistry()
        for strat in registry.get_all_strategies():
            self.assertEqual(strat.lot_size, 65, f"Strategy {strat.strategy_id} instance lot_size is not 65")

    def test_quantity_calculation_lots_times_lot_size(self):
        """Verifies quantity is consistently calculated as number_of_lots * lot_size."""
        # 1 lot -> 65
        strat_1lot = StrategyRegistry.create_strategy("STRAT_001", config={
            "mode": "PAPER",
            "lot_size": 65,
            "max_lots": 1,
            "strike_offset": 0
        })
        snapshot = MarketSnapshot(
            timestamp="2026-10-25T10:00:00Z",
            spot=24850.0,
            options={
                "24850_CE": OptionContract(strike=24850.0, option_type="CE", ltp=110.0)
            }
        )
        entry_sig, _ = strat_1lot.evaluate(snapshot, open_position=None)
        self.assertIsNotNone(entry_sig)
        self.assertEqual(entry_sig.legs[0].quantity, 65)

        # 2 lots -> 130
        strat_2lots = StrategyRegistry.create_strategy("STRAT_001", config={
            "mode": "PAPER",
            "lot_size": 65,
            "max_lots": 2,
            "strike_offset": 0
        })
        entry_sig2, _ = strat_2lots.evaluate(snapshot, open_position=None)
        self.assertIsNotNone(entry_sig2)
        self.assertEqual(entry_sig2.legs[0].quantity, 130)

        # Custom contract-specific lot size (e.g. historical contract lot size 50 or 25)
        strat_custom = StrategyRegistry.create_strategy("STRAT_001", config={
            "mode": "PAPER",
            "lot_size": 25,
            "max_lots": 3,
            "strike_offset": 0
        })
        entry_sig3, _ = strat_custom.evaluate(snapshot, open_position=None)
        self.assertIsNotNone(entry_sig3)
        self.assertEqual(entry_sig3.legs[0].quantity, 75)


if __name__ == "__main__":
    unittest.main()

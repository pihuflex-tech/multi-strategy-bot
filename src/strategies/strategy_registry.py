"""
strategy_registry.py
Central Registry & Discovery Factory for all 25 Independent Quantitative Strategies.
Enforces that exactly 25 strategies are registered (STRAT_001 through STRAT_025).
"""

from typing import Dict, List, Optional, Type, Any
import os
import json

from src.strategies.strategy_base import StrategyBase
from src.strategies.options_buying.long_call import LongCallStrategy
from src.strategies.options_buying.long_put import LongPutStrategy
from src.strategies.straddle.long_straddle import LongStraddleStrategy
from src.strategies.strangle.long_strangle import LongStrangleStrategy
from src.strategies.straddle.short_straddle import ShortStraddleStrategy
from src.strategies.strangle.short_strangle import ShortStrangleStrategy
from src.strategies.iron_condor.iron_condor import IronCondorStrategy
from src.strategies.spreads.bull_call_spread import BullCallSpreadStrategy
from src.strategies.futures.trend_following import FuturesTrendFollowingStrategy
from src.strategies.hedging.protective_put import ProtectivePutStrategy
from src.strategies.gap.gap_fade import GapFadeStrategy
from src.strategies.gap.gap_continuation import GapContinuationStrategy
from src.strategies.trend.ema_crossover import EMACrossoverStrategy
from src.strategies.oi_flow.oi_signal import OIBasedStrategy
from src.strategies.hedging.adaptive_hedging import AdaptiveHedgingStrategy
from src.strategies.spreads.bear_put_spread import BearPutSpreadStrategy
from src.strategies.spreads.bear_call_spread import BearCallCreditSpreadStrategy
from src.strategies.spreads.bull_put_spread import BullPutCreditSpreadStrategy
from src.strategies.volatility.orb_strategy import OpeningRangeBreakoutStrategy
from src.strategies.trend.vwap_reclaim import VWAPReclaimStrategy
from src.strategies.volatility.rsi_bb_reversion import RSIBBMeanReversionStrategy
from src.strategies.trend.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.spreads.calendar_spread import CalendarSpreadStrategy
from src.strategies.spreads.diagonal_spread import DiagonalSpreadStrategy
from src.strategies.futures.supertrend_trend import SupertrendFuturesStrategy

class StrategyRegistry:
    """
    Registry for the 25 independent quantitative strategies.
    """
    # Strict 1-to-1 Mapping of Strategy IDs to Concrete Classes
    STRATEGY_MAP: Dict[str, Type[StrategyBase]] = {
        "STRAT_001": LongCallStrategy,
        "STRAT_002": LongPutStrategy,
        "STRAT_003": LongStraddleStrategy,
        "STRAT_004": LongStrangleStrategy,
        "STRAT_005": ShortStraddleStrategy,
        "STRAT_006": ShortStrangleStrategy,
        "STRAT_007": IronCondorStrategy,
        "STRAT_008": BullCallSpreadStrategy,
        "STRAT_009": FuturesTrendFollowingStrategy,
        "STRAT_010": ProtectivePutStrategy,
        "STRAT_011": GapFadeStrategy,
        "STRAT_012": GapContinuationStrategy,
        "STRAT_013": EMACrossoverStrategy,
        "STRAT_014": OIBasedStrategy,
        "STRAT_015": AdaptiveHedgingStrategy,
        "STRAT_016": BearPutSpreadStrategy,
        "STRAT_017": BearCallCreditSpreadStrategy,
        "STRAT_018": BullPutCreditSpreadStrategy,
        "STRAT_019": OpeningRangeBreakoutStrategy,
        "STRAT_020": VWAPReclaimStrategy,
        "STRAT_021": RSIBBMeanReversionStrategy,
        "STRAT_022": DonchianBreakoutStrategy,
        "STRAT_023": CalendarSpreadStrategy,
        "STRAT_024": DiagonalSpreadStrategy,
        "STRAT_025": SupertrendFuturesStrategy,
    }

    def __init__(self, config_path: Optional[str] = None, workspace_root: str = "."):
        self.workspace_root = workspace_root
        self.config_path = config_path or os.path.join(workspace_root, "config", "strategy_config.json")
        self.strategies: Dict[str, StrategyBase] = {}
        self._load_and_instantiate()

    def _load_and_instantiate(self):
        """Loads strategy configuration and instantiates all 25 strategies."""
        config_data = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception as e:
                print(f"[!] Error reading strategy config: {e}")

        strat_configs = config_data.get("strategies", {})

        for strat_id, cls in self.STRATEGY_MAP.items():
            cfg = strat_configs.get(strat_id, {})
            # Instantiate strategy
            instance = cls(config=cfg, workspace_root=self.workspace_root)
            self.strategies[strat_id] = instance

    def get_strategy(self, strategy_id: str) -> Optional[StrategyBase]:
        """Returns strategy instance by exact ID."""
        return self.strategies.get(strategy_id)

    def get_all_strategies(self) -> List[StrategyBase]:
        """Returns all 25 instantiated strategy instances."""
        return list(self.strategies.values())

    def get_registered_ids(self) -> List[str]:
        """Returns list of registered strategy IDs."""
        return sorted(list(self.strategies.keys()))

    def get_active_strategies(self) -> List[StrategyBase]:
        """Returns strategies that are NOT in DISABLED mode."""
        return [s for s in self.strategies.values() if s.mode.value != "DISABLED"]

    @classmethod
    def list_strategies(cls) -> List[str]:
        """Returns sorted list of registered strategy IDs."""
        return sorted(list(cls.STRATEGY_MAP.keys()))

    @classmethod
    def create_strategy(
        cls,
        strategy_id: str,
        config: Optional[Dict[str, Any]] = None,
        workspace_root: str = "."
    ) -> Optional[StrategyBase]:
        """Factory method to instantiate a strategy by ID."""
        strat_cls = cls.STRATEGY_MAP.get(strategy_id)
        if strat_cls:
            return strat_cls(config=config or {}, workspace_root=workspace_root)
        return None

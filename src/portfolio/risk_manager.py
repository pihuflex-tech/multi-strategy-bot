"""
risk_manager.py
Central Shared Portfolio Risk Manager.
Enforces position limits, daily loss circuit breakers, stale data rejection,
and cooldown rules independently from strategy signal generation.
"""

from typing import Dict, Any, Optional, Tuple
import time

from src.strategies.signal_models import StrategySignal

class RiskManager:
    """
    Independent Risk Governor checking all simulated entries and portfolio exposure limits.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.portfolio_max_daily_loss = float(cfg.get("portfolio_max_daily_loss", 50000.0))
        self.portfolio_max_drawdown_pct = float(cfg.get("portfolio_max_drawdown_pct", 5.0))
        self.max_open_positions_total = int(cfg.get("max_open_positions_total", 25))
        self.max_open_positions_per_strategy = int(cfg.get("max_open_positions_per_strategy", 1))
        self.max_capital_per_strategy = float(cfg.get("max_capital_per_strategy", 200000.0))
        self.stale_data_threshold_seconds = float(cfg.get("stale_data_threshold_seconds", 5.0))
        self.emergency_square_off_enabled = bool(cfg.get("emergency_square_off_enabled", True))
        self.risk_checks_enabled = bool(cfg.get("risk_checks_enabled", True))

        # Cooldown trackers: strategy_id -> cooldown_expiry_timestamp
        self.cooldowns: Dict[str, float] = {}

    def set_cooldown(self, strategy_id: str, duration_seconds: float = 300.0):
        """Sets a cooldown window for a specific strategy after stop loss or error."""
        self.cooldowns[strategy_id] = time.time() + duration_seconds

    def is_in_cooldown(self, strategy_id: str) -> bool:
        """Checks if a strategy is currently in cooldown."""
        exp = self.cooldowns.get(strategy_id, 0.0)
        return time.time() < exp

    def validate_signal(
        self,
        signal: StrategySignal,
        portfolio_summary: Dict[str, Any],
        open_positions_count_for_strat: int,
        is_market_data_stale: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates whether a strategy signal passes all portfolio risk rules.
        Returns: (is_valid: bool, rejection_reason: Optional[str])
        """
        if not self.risk_checks_enabled:
            return True, None

        # Rule 0: State recovery failure check (Fail-closed principle)
        if portfolio_summary.get("recovery_failed", False) or not portfolio_summary.get("is_healthy", True):
            return False, "RECOVERY_FAILURE_ENGINE_FAIL_CLOSED"

        # Rule 1: Stale data blocking
        if is_market_data_stale:
            return False, "STALE_MARKET_DATA_ENTRY_BLOCKED"

        # Rule 2: Strategy Cooldown check
        if self.is_in_cooldown(signal.strategy_id):
            remaining = int(self.cooldowns[signal.strategy_id] - time.time())
            return False, f"STRATEGY_IN_COOLDOWN_{remaining}S_REMAINING"

        # Rule 3: Max open positions per strategy (duplicate position protection)
        if open_positions_count_for_strat >= self.max_open_positions_per_strategy:
            return False, f"MAX_POSITIONS_PER_STRATEGY_REACHED_{self.max_open_positions_per_strategy}"

        # Rule 4: Total portfolio open positions limit
        current_open = portfolio_summary.get("open_positions_count", 0)
        if current_open >= self.max_open_positions_total:
            return False, f"PORTFOLIO_MAX_OPEN_POSITIONS_EXCEEDED_{self.max_open_positions_total}"

        # Rule 5: Daily Realized Loss Circuit Breaker
        net_realized = portfolio_summary.get("total_net_realized_pnl", 0.0)
        unrealized = portfolio_summary.get("total_unrealized_mtm", 0.0)
        combined_today = net_realized + unrealized

        if combined_today <= -self.portfolio_max_daily_loss:
            return False, f"DAILY_LOSS_CIRCUIT_BREAKER_TRIGGERED_{combined_today}_LE_MINUS_{self.portfolio_max_daily_loss}"

        # Rule 6: Validate leg quantities
        if not signal.legs:
            return False, "EMPTY_SIGNAL_LEGS"

        for leg in signal.legs:
            if leg.quantity <= 0:
                return False, f"INVALID_LEG_QUANTITY_{leg.quantity}"

        return True, None

    def check_portfolio_emergency_halt(self, portfolio_summary: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Checks if the entire portfolio has breached emergency loss thresholds.
        """
        net_realized = portfolio_summary.get("total_net_realized_pnl", 0.0)
        unrealized = portfolio_summary.get("total_unrealized_mtm", 0.0)
        combined_today = net_realized + unrealized

        if combined_today <= -self.portfolio_max_daily_loss:
            return True, f"EMERGENCY_DAILY_LOSS_BREACH: Net PnL {combined_today} <= -{self.portfolio_max_daily_loss}"

        return False, None

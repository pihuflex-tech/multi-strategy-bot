# Strategy Variant Schema Specification

**Document ID:** `SCHEMA-SPEC-2026-001`  
**Location:** `docs/strategy_research_design/strategy_variant_schema.md`  
**Classification:** Quantitative Data Model & Strategy Configuration Schema  
**Status:** PROPOSED TECHNICAL SPECIFICATION  

---

## 1. Overview & Architecture Role

The **Strategy Variant Model** defines a standardized, strongly typed contract for defining any quantitative strategy variant in the NIFTY Multi-Strategy Bot.

In the modular v2.0 architecture, every trading strategy is a parameterized instance of a base strategy class. To ensure deterministic execution, automated backtesting, and systematic comparison, every variant must be declared according to this formal schema.

### Quad-Tier Classification Standard
To maintain strict intellectual and engineering discipline, every field in the schema is tagged with its formal epistemic status:
1. `[OWNER_APPROVED]`: Hard business or regulatory requirements explicitly mandated by the project owner (e.g., paper trading boundary, statutory tax accounting, terminal bootstrap).
2. `[RESEARCH_HYPOTHESIS]`: Theoretical financial premise under investigation (e.g., "Buying calls during confirmed momentum produces positive expectancy").
3. `[EMPIRICALLY_VALIDATED_RULE]`: Specific quantitative rules or parameter thresholds that must be rigorously verified against out-of-sample data before production paper trading.
4. `[UNRESOLVED_DECISION]`: Open architectural or strategic choices requiring owner sign-off.

---

## 2. Complete JSON Schema Specification

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "StrategyVariantDefinition",
  "description": "Standardized schema for all quantitative strategy variants in the Multi-Strategy Bot.",
  "type": "object",
  "required": [
    "variant_id",
    "strategy_id",
    "family_id",
    "version",
    "hypothesis",
    "instrument_type",
    "timeframe",
    "data_requirements",
    "regime_eligibility",
    "temporal_rules",
    "entry_rules",
    "exit_rules",
    "multi_leg_structure",
    "risk_management",
    "cost_profile",
    "lifecycle_governance"
  ],
  "properties": {
    "variant_id": {
      "type": "string",
      "pattern": "^VAR_[A-Z0-9_]+$",
      "description": "Globally unique variant identifier (e.g. VAR_STRADDLE_0920_SL25_TSL5)."
    },
    "strategy_id": {
      "type": "string",
      "pattern": "^CORE_[0-9]{3}$|^EXP_[A-Z0-9_]+$",
      "description": "Parent strategy archetype ID (e.g. CORE_005)."
    },
    "family_id": {
      "type": "string",
      "description": "Parent cluster family (e.g. FAM_STRADDLE)."
    },
    "version": {
      "type": "string",
      "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$",
      "description": "Semantic configuration version."
    },
    "hypothesis": {
      "type": "object",
      "required": ["statement", "expected_edge", "epistemic_status"],
      "properties": {
        "statement": { "type": "string" },
        "expected_edge": { "type": "string" },
        "epistemic_status": { "type": "string", "enum": ["RESEARCH_HYPOTHESIS"] }
      }
    },
    "instrument_type": {
      "type": "string",
      "enum": ["INDEX_OPTIONS", "INDEX_FUTURES", "FUTURES_PLUS_OPTIONS", "MULTI_LEG_SPREAD"]
    },
    "timeframe": {
      "type": "object",
      "required": ["bar_interval", "holding_horizon"],
      "properties": {
        "bar_interval": { "type": "string", "enum": ["1M", "3M", "5M", "15M", "TICK_BY_TICK"] },
        "holding_horizon": { 
          "type": "string", 
          "enum": ["STRICT_INTRADAY", "MULTI_DAY_POSITIONAL"],
          "description": "[UNRESOLVED_DECISION] Strict Intraday vs Positional holding."
        }
      }
    },
    "data_requirements": {
      "type": "object",
      "required": ["underlying_token", "requires_option_chain", "required_features"],
      "properties": {
        "underlying_token": { "type": "string", "default": "NIFTY_SPOT" },
        "requires_option_chain": { "type": "boolean" },
        "required_features": {
          "type": "array",
          "items": { "type": "string" },
          "description": "e.g. ['ATM_STRIKE', 'GREEKS_DELTA', 'GREEKS_GAMMA', 'IV_SKEW', 'VWAP', 'FUTURES_BASIS']"
        }
      }
    },
    "regime_eligibility": {
      "type": "object",
      "required": ["eligible_regimes", "prohibited_regimes", "min_confidence_score"],
      "properties": {
        "eligible_regimes": {
          "type": "array",
          "items": { "type": "string" },
          "description": "List of market regimes under which this variant is active."
        },
        "prohibited_regimes": {
          "type": "array",
          "items": { "type": "string" }
        },
        "min_confidence_score": {
          "type": "number",
          "minimum": 0.0,
          "maximum": 1.0,
          "default": 0.60
        }
      }
    },
    "temporal_rules": {
      "type": "object",
      "required": ["allowed_entry_start", "allowed_entry_end", "force_exit_time", "allowed_days_of_week"],
      "properties": {
        "allowed_entry_start": { "type": "string", "pattern": "^(09|10|11|12|13|14):[0-5][0-9]:00$" },
        "allowed_entry_end": { "type": "string", "pattern": "^(09|10|11|12|13|14|15):[0-5][0-9]:00$" },
        "force_exit_time": { "type": "string", "default": "15:15:00" },
        "allowed_days_of_week": {
          "type": "array",
          "items": { "type": "string", "enum": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"] }
        },
        "expiry_day_only": { "type": "boolean", "default": false }
      }
    },
    "entry_rules": {
      "type": "object",
      "required": ["trigger_type", "primary_condition", "filters"],
      "properties": {
        "trigger_type": { "type": "string", "enum": ["TIME_CLOCK", "PRICE_BREAKOUT", "INDICATOR_CROSS", "VOLATILITY_EXPANSION", "OI_UNWIND"] },
        "primary_condition": { "type": "string", "description": "Logical expression evaluated by engine." },
        "filters": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },
    "exit_rules": {
      "type": "object",
      "required": ["initial_stop_loss", "profit_target", "trailing_stop_loss", "time_stop"],
      "properties": {
        "initial_stop_loss": {
          "type": "object",
          "required": ["type", "value"],
          "properties": {
            "type": { "type": "string", "enum": ["PERCENTAGE_ENTRY_LTP", "FIXED_POINTS", "ATR_MULTIPLE", "COMBINED_PREMIUM_PCT"] },
            "value": { "type": "number" }
          }
        },
        "profit_target": {
          "type": "object",
          "required": ["type", "value"],
          "properties": {
            "type": { "type": "string", "enum": ["PERCENTAGE_ENTRY_LTP", "FIXED_POINTS", "RISK_REWARD_RATIO", "NONE"] },
            "value": { "type": "number" }
          }
        },
        "trailing_stop_loss": {
          "type": "object",
          "required": ["enabled", "activation_threshold", "trail_step"],
          "properties": {
            "enabled": { "type": "boolean" },
            "type": { "type": "string", "enum": ["HIGH_WATER_MARK_PCT", "STEP_POINTS", "ATR_TRAIL", "SHIFT_TO_COST"] },
            "activation_threshold": { "type": "number" },
            "trail_step": { "type": "number" }
          }
        },
        "time_stop": {
          "type": "string",
          "default": "15:15:00"
        }
      }
    },
    "multi_leg_structure": {
      "type": "object",
      "required": ["leg_count", "legs", "execution_mode"],
      "properties": {
        "leg_count": { "type": "integer", "minimum": 1, "maximum": 4 },
        "execution_mode": { 
          "type": "string", 
          "enum": ["ATOMIC_ALL_OR_NONE", "SEQUENTIAL_HEDGE_FIRST"],
          "description": "[UNRESOLVED_DECISION] Multi-leg fill synchronization."
        },
        "legs": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["leg_id", "side", "instrument", "strike_selection_rule", "quantity_ratio"],
            "properties": {
              "leg_id": { "type": "string" },
              "side": { "type": "string", "enum": ["BUY", "SELL"] },
              "instrument": { "type": "string", "enum": ["CE", "PE", "FUT"] },
              "strike_selection_rule": {
                "type": "object",
                "required": ["method"],
                "properties": {
                  "method": { "type": "string", "enum": ["ATM_ROUND_50", "DELTA_TARGET", "POINTS_OFFSET", "IV_EXTREME"] },
                  "offset_value": { "type": "number" }
                }
              },
              "quantity_ratio": { "type": "integer", "default": 1 }
            }
          }
        }
      }
    },
    "risk_management": {
      "type": "object",
      "required": ["max_capital_allocation", "lot_size", "max_loss_per_day", "cooldown_seconds_after_sl"],
      "properties": {
        "max_capital_allocation": { "type": "number", "description": "Max virtual margin in INR." },
        "lot_size": { "type": "integer", "default": 75 },
        "max_loss_per_day": { "type": "number" },
        "cooldown_seconds_after_sl": { "type": "integer", "default": 300 }
      }
    },
    "cost_profile": {
      "type": "object",
      "required": ["tax_model", "slippage_model"],
      "properties": {
        "tax_model": { "type": "string", "enum": ["INDIAN_FO_STATUTORY_2026"], "default": "INDIAN_FO_STATUTORY_2026" },
        "slippage_model": { "type": "string", "enum": ["FIXED_POINTS", "PERCENTAGE_SPREAD", "VOLATILITY_ADAPTIVE"] }
      }
    },
    "lifecycle_governance": {
      "type": "object",
      "required": ["max_entries_per_day", "allow_reentry_same_direction"],
      "properties": {
        "max_entries_per_day": { "type": "integer", "default": 1 },
        "allow_reentry_same_direction": { "type": "boolean", "default": false }
      }
    }
  }
}
```

---

## 3. Python DataClass Representation

```python
"""
strategy_variant_schema.py
Strongly-typed schema dataclasses for Multi-Strategy Bot variants.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum

class EpistemicStatus(Enum):
    OWNER_APPROVED = "OWNER_APPROVED"
    RESEARCH_HYPOTHESIS = "RESEARCH_HYPOTHESIS"
    EMPIRICALLY_VALIDATED_RULE = "EMPIRICALLY_VALIDATED_RULE"
    UNRESOLVED_DECISION = "UNRESOLVED_DECISION"

class HoldingHorizon(Enum):
    STRICT_INTRADAY = "STRICT_INTRADAY"
    MULTI_DAY_POSITIONAL = "MULTI_DAY_POSITIONAL"

class LegSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OptionType(Enum):
    CE = "CE"
    PE = "PE"
    FUT = "FUT"

class StrikeMethod(Enum):
    ATM_ROUND_50 = "ATM_ROUND_50"
    DELTA_TARGET = "DELTA_TARGET"
    POINTS_OFFSET = "POINTS_OFFSET"
    IV_EXTREME = "IV_EXTREME"

@dataclass(frozen=True)
class LegDefinition:
    leg_id: str
    side: LegSide
    instrument: OptionType
    strike_method: StrikeMethod
    offset_value: float = 0.0
    quantity_ratio: int = 1

@dataclass(frozen=True)
class StopLossConfig:
    sl_type: str  # PERCENTAGE_ENTRY_LTP | FIXED_POINTS | ATR_MULTIPLE
    sl_value: float
    tsl_enabled: bool = False
    tsl_type: str = "NONE"  # HIGH_WATER_MARK_PCT | STEP_POINTS | SHIFT_TO_COST
    tsl_trigger: float = 0.0
    tsl_step: float = 0.0

@dataclass(frozen=True)
class StrategyVariant:
    variant_id: str
    strategy_id: str
    family_id: str
    version: str
    hypothesis_statement: str
    instrument_type: str
    holding_horizon: HoldingHorizon
    eligible_regimes: List[str]
    entry_start_time: str
    entry_end_time: str
    force_exit_time: str
    legs: List[LegDefinition]
    stop_loss: StopLossConfig
    lot_size: int = 75
    max_entries_per_day: int = 1
    cooldown_seconds: int = 300
    epistemic_flags: Dict[str, EpistemicStatus] = field(default_factory=dict)
```

---

## 4. Example Variant Instance: `VAR_STRADDLE_0920_SL25_TSL5`

Below is an instantiated research variant demonstrating how a concrete strategy is parameterized without hardcoded engine logic:

```json
{
  "variant_id": "VAR_STRADDLE_0920_SL25_TSL5",
  "strategy_id": "CORE_005",
  "family_id": "FAM_STRADDLE",
  "version": "2.0.0",
  "hypothesis": {
    "statement": "Selling ATM straddles at 09:20 IST under range-bound, low-volatility regimes captures rapid early-morning theta decay while a 25% leg SL combined with a 5% step trailing SL caps adverse gamma breakouts.",
    "expected_edge": "Theta decay capture with dynamic risk locking",
    "epistemic_status": "RESEARCH_HYPOTHESIS"
  },
  "instrument_type": "INDEX_OPTIONS",
  "timeframe": {
    "bar_interval": "1M",
    "holding_horizon": "STRICT_INTRADAY"
  },
  "data_requirements": {
    "underlying_token": "NIFTY_SPOT",
    "requires_option_chain": true,
    "required_features": ["ATM_STRIKE", "COMBINED_STRADDLE_PREMIUM", "INDIA_VIX", "INTRADAY_VWAP"]
  },
  "regime_eligibility": {
    "eligible_regimes": ["SIDEWAYS_RANGE_BOUND", "LOW_VOLATILITY"],
    "prohibited_regimes": ["HIGH_VOLATILITY", "OPENING_GAP_UP", "OPENING_GAP_DOWN"],
    "min_confidence_score": 0.65
  },
  "temporal_rules": {
    "allowed_entry_start": "09:20:00",
    "allowed_entry_end": "09:25:00",
    "force_exit_time": "15:15:00",
    "allowed_days_of_week": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
    "expiry_day_only": false
  },
  "entry_rules": {
    "trigger_type": "TIME_CLOCK",
    "primary_condition": "cur_time >= '09:20:00' and cur_time <= '09:25:00'",
    "filters": [
      "regime in ['SIDEWAYS_RANGE_BOUND', 'LOW_VOLATILITY']",
      "abs(spot - vwap) / vwap < 0.002"
    ]
  },
  "exit_rules": {
    "initial_stop_loss": {
      "type": "PERCENTAGE_ENTRY_LTP",
      "value": 25.0
    },
    "profit_target": {
      "type": "PERCENTAGE_ENTRY_LTP",
      "value": 50.0
    },
    "trailing_stop_loss": {
      "enabled": true,
      "type": "STEP_POINTS",
      "activation_threshold": 15.0,
      "trail_step": 5.0
    },
    "time_stop": "15:15:00"
  },
  "multi_leg_structure": {
    "leg_count": 2,
    "execution_mode": "ATOMIC_ALL_OR_NONE",
    "legs": [
      {
        "leg_id": "LEG_1_CE",
        "side": "SELL",
        "instrument": "CE",
        "strike_selection_rule": { "method": "ATM_ROUND_50", "offset_value": 0 },
        "quantity_ratio": 1
      },
      {
        "leg_id": "LEG_2_PE",
        "side": "SELL",
        "instrument": "PE",
        "strike_selection_rule": { "method": "ATM_ROUND_50", "offset_value": 0 },
        "quantity_ratio": 1
      }
    ]
  },
  "risk_management": {
    "max_capital_allocation": 250000.0,
    "lot_size": 75,
    "max_loss_per_day": 7500.0,
    "cooldown_seconds_after_sl": 600
  },
  "cost_profile": {
    "tax_model": "INDIAN_FO_STATUTORY_2026",
    "slippage_model": "PERCENTAGE_SPREAD"
  },
  "lifecycle_governance": {
    "max_entries_per_day": 1,
    "allow_reentry_same_direction": false
  }
}
```

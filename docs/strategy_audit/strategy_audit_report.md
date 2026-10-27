> [!NOTE]
> **HISTORICAL DOCUMENTATION — RETIRED V1 MODEL AUDIT**
> This audit report documents the behavioral, mathematical, and architectural state of the legacy single-file quantitative model (v1.0) prior to its complete retirement.
> The legacy files audited herein (`strategy_engine.py`, `generate_strategies.py`, `strategies_config.json`, and `dashboard.py`) have been permanently retired and moved to `archive/legacy_v1/`.
> This report serves as the empirical justification and forensic baseline for designing the new strategy system from scratch.

# Quantitative Strategy Audit & Migration Specification Report (100 Strategies)

**Repository:** `C:\Users\ASUS\Desktop\multi-strategy-bot`  
**Phase:** Phase 2A — Comprehensive Strategy Audit and Architectural Reset  
**Auditor:** Lead Quantitative Strategy Engineer & Trading Systems Auditor  
**Scope:** Strict Forensic Audit of Legacy Model (External Feed Server untouched)  

---

## 1. Executive Summary & Audit Methodology

A rigorous, line-by-line quantitative audit was conducted across all 100 strategies configured in [strategies_config.json](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategies_config.json) and evaluated within [strategy_engine.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategy_engine.py). 

### Primary Findings
1. **Wide Discrepancy Between Configuration and Execution**:
   - Of the 100 configured strategies, **only 30 have fully operational handlers** in the legacy engine.
   - **30 strategies have zero lines of code** in the engine and never enter trades.
   - **20 strategies enter blindly on time**, completely ignoring configured technical indicators (Greeks, Volume, Open Interest).
   - **20 strategies have severe structural defects**: Wing Condors lack wing hedges (naked strangles), and Basis Arbitrage inverts long/short logic.
2. **Pre-Market & Post-Market Guard Bug**:
   - Line 208 of `strategy_engine.py` allows instantaneous entry of 24 strategies when fed any tick outside `09:15:00` to `15:30:00` IST.
3. **Absence of Trailing Stop-Loss Mechanics**:
   - Zero strategies currently feature programmatic trailing stop-loss logic. Only Smart Straddles implement a basic shift-to-cost rule upon the first leg hitting its stop-loss.

---

## 2. Strategy Count & Implementation Status Breakdown

Every strategy has been inspected against the legacy engine and classified into one of five rigorous engineering statuses:
- **`IMPLEMENTED`**: Handler logic exists and executes matching the core rule concept.
- **`PARTIAL`**: Handler exists, but key indicators, filters, or parameters are ignored.
- **`DEFECTIVE`**: Handler contains material logic contradictions, inverted actions, or missing protective hedges.
- **`MISSING`**: No handler exists in the engine; evaluates to no-op.
- **`UNVERIFIED`**: Cannot be verified from available code or fixtures.

### Quantitative Summary by Strategy Family

| # | Strategy Family (Cluster) | Strategy IDs | Configured Rule Type | Count | Verified Implementation Status | Recommended Holding Mode | Primary Deficit / Observation |
| :-: | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| **1** | **Smart Straddles** | `STRAT_001` - `010` | `TIME_STRADDLE` | 10 | **IMPLEMENTED** | **INTRADAY** | Pre/post-market entry bug; shift-to-cost only triggers on SL hit, ignoring target exits. |
| **2** | **Dynamic Delta** | `STRAT_011` - `020` | `GREEKS_DELTA` | 10 | **IMPLEMENTED** | **INTRADAY** | Evaluates closest delta; falls back to arbitrary strike if delta is missing; unhedged leg risk. |
| **3** | **Gamma Velocity** | `STRAT_021` - `030` | `GAMMA_BUY` | 10 | **PARTIAL** | **INTRADAY** | Enters purely on clock; **completely ignores `min_gamma` (0.0011)** and volume velocity. |
| **4** | **OI Order Flow** | `STRAT_031` - `040` | `OI_FLOW` | 10 | **PARTIAL** | **INTRADAY** | Enters purely on clock; **completely ignores open interest and volume metrics**. |
| **5** | **Vega Skew** | `STRAT_041` - `050` | `VEGA_SKEW` | 10 | **IMPLEMENTED** | **INTRADAY** | Correctly evaluates IV skew spread; target calculation overrides config with hardcoded `*0.65`. |
| **6** | **Heavyweight Sniper** | `STRAT_051` - `060` | `HEAVYWEIGHT_SYNC` | 10 | **MISSING** | **INTRADAY** | **Zero handler code in engine.** Declares `min_sync: 3/4`, but 0 trades are ever entered. |
| **7** | **Basis Arbitrage** | `STRAT_061` - `070` | `BASIS_ARB` | 10 | **DEFECTIVE** | **CONDITIONAL** | **Inverted logic**: Sells futures at discount, buys at premium. Trades naked single futures leg. |
| **8** | **Wing Condors** | `STRAT_071` - `080` | `WING_CONDOR` | 10 | **DEFECTIVE** | **CONDITIONAL** | **Unhedged**: Sells 2 short legs; zero long wing hedges bought. Hardcodes SL/target overrides. |
| **9** | **Theta Calendar** | `STRAT_081` - `090` | `CALENDAR_SPREAD` | 10 | **MISSING** | **CONDITIONAL** | **Zero handler code in engine.** Multi-expiry calendar spread logic absent. |
| **10** | **Afternoon 0-DTE** | `STRAT_091` - `100` | `AFTERNOON_0DTE` | 10 | **MISSING** | **INTRADAY** | **Zero handler code in engine.** Afternoon gamma breakout/decay logic absent. |
| **TOTAL** | **10 Clusters** | **100 Strategies** | | **100** | **30 Implemented<br>20 Partial<br>20 Defective<br>30 Missing** | **70 Intraday<br>30 Conditional** | |

---

## 3. High-Priority Defects & Architectural Vulnerabilities

### Priority 1: Market Session Timing Inversion
- **File:** [strategy_engine.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategy_engine.py#L208-L209)
- **Code:** `if "09:15:00" <= cur_time <= "15:30:00" and cur_time < strat["entry_time"]: continue`
- **Impact:** When a tick arrives outside market hours (e.g. pre-market 08:00:00 or replay 16:00:00), the condition evaluates to `False`, bypassing the guard and causing all eligible strategies to enter instantly.

### Priority 2: Unhedged Tail Risk in Wing Condors
- **File:** [strategy_engine.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategy_engine.py#L280-L290)
- **Code:** Checks `and wings_opts:`, but creates only 2 short positions:
  `Position(s_id, ..., atm+off, "CE", ..., "SELL", ...)`
  `Position(s_id, ..., atm-off, "PE", ..., "SELL", ...)`
- **Impact:** Leaves the trader with unlimited black-swan loss exposure under the false label of a "Wing-Hedged Iron Condor".

### Priority 3: Directionally Inverted Basis Arbitrage
- **File:** [strategy_engine.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategy_engine.py#L272)
- **Code:** `act = "SELL" if fut_ltp < spot else "BUY"`
- **Impact:** Sells the underpriced asset (discounted futures) and buys the overpriced asset (premium futures), guaranteeing losses upon contract expiry convergence.

### Priority 4: Disconnected Web Dashboard Telemetry
- **File:** [dashboard.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/dashboard.py#L10-L11)
- **Code:** Queries `http://127.0.0.1:5555/api/market_data` and attempts to read `/tmp/market_state.json`.
- **Impact:** Never reads [logs/live_state.json](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/logs/live_state.json). The dashboard displays `CACHE_DEGRADED` and ₹0.00 MTM indefinitely.

---

## 4. Intraday vs. Positional Analysis

All 100 strategies currently declare an `exit_time` between `15:10:00` and `15:15:00` IST.
- **70 Strategies are Strictly Recommended as INTRADAY**:
  - All short options (Straddles, Delta Strangles, Vega Skew) carry unacceptable overnight gap risks that would devastate unhedged accounts.
  - All long options (Gamma Buyers, OI Flow, Heavyweight Sniper, Afternoon 0-DTE) suffer rapid theta decay and should never be carried overnight.
- **30 Strategies are Recommended as CONDITIONAL**:
  - **Basis Arbitrage (`STRAT_061`-`070`)**: Fair value convergence rarely completes in intraday sessions.
  - **Wing Condors (`STRAT_071`-`080`)**: Once 4-leg hedges are restored, defined-risk condors can safely be held multi-day.
  - **Theta Calendars (`STRAT_081`-`090`)**: Inter-expiry theta harvesting requires multiple trading days to capture front-month decay.
- **Holding Mode Rule**: Unless disk-backed state persistence (`src/storage/position_store.py`) and explicit owner approval are in place, the engine will enforce mandatory intraday square-off at 15:15:00 IST.

---

## 5. Trailing Stop-Loss Design Review

No trailing stop-loss is implemented in the legacy codebase. The review proposes candidate trailing designs across strategy types:

1. **Short Premium Decaying Strategies (Straddles & Strangles)**:
   - **Method**: Break-Even Movement followed by Premium Percentage Trailing.
   - **Trigger**: Once premium decays by 20% from entry, shift SL to Entry Price (Break-Even).
   - **Ratchet**: For every further 10% premium decay, trail SL down by 10%.
2. **Long Premium Momentum Buyers (Gamma, OI Flow, 0-DTE)**:
   - **Method**: R-Multiple (Risk Multiple) Trailing.
   - **Trigger**: Once position reaches +1R (+15% to +25%), lock break-even.
   - **Ratchet**: Trail at 0.5R distance once profit exceeds +1.5R.
3. **Defined-Risk Condors**:
   - **Method**: Atomic Net Credit Trailing.
   - **Trigger**: When combined 4-leg Condor value decays by 50% of initial credit received.

---

## 6. Migration Mapping to Modular Architecture

Every legacy strategy is mapped 1:1 to the modular components created in Phase 1:

| Cluster | Legacy Code Location | New Target Module | Analytical Dependencies | Execution Model |
| :--- | :--- | :--- | :--- | :--- |
| **Smart Straddles** | `strategy_engine.py:TIME_STRADDLE` | `src/strategies/straddle/short_straddle.py` | `atm_calculator`, `strike_calculator` | Dual-Leg Synchronous Sell |
| **Dynamic Delta** | `strategy_engine.py:GREEKS_DELTA` | `src/strategies/strangle/short_strangle.py` | `greeks_calculator.delta` | Dual-Leg Synchronous Sell |
| **Gamma Velocity** | `strategy_engine.py:GAMMA_BUY` | `src/strategies/options_buying/long_call.py` / `long_put.py` | `greeks_calculator.gamma`, `volume_calculator` | Single-Leg Market Buy |
| **OI Order Flow** | `strategy_engine.py:OI_FLOW` | `src/strategies/oi_flow/oi_signal.py` | `oi_calculator`, `volume_calculator` | Single-Leg Market Buy |
| **Vega Skew** | `strategy_engine.py:VEGA_SKEW` | `src/strategies/volatility/volatility_contraction.py` | `iv_calculator`, `volatility_calculator` | Single-Leg Short |
| **Heavyweight Sniper** | *(None)* | `src/strategies/trend/ema_crossover.py` *(or dedicated)* | `trend_calculator.market_breadth` | Single-Leg Option Buy |
| **Basis Arbitrage** | `strategy_engine.py:BASIS_ARB` | `src/strategies/futures/trend_following.py` *(or dedicated)* | `futures_basis.calculate_spread` | Inverted Action Reversal |
| **Wing Condors** | `strategy_engine.py:WING_CONDOR` | `src/strategies/iron_condor/iron_condor.py` | `strike_calculator.wing_strikes` | Atomic 4-Leg Execution |
| **Theta Calendar** | *(None)* | `src/strategies/spreads/bear_call_spread.py` *(or dedicated)* | `expiry_manager.get_calendar_pair` | Inter-Expiry 2-Leg Pair |
| **Afternoon 0-DTE** | *(None)* | `src/strategies/volatility/volatility_expansion.py` | `greeks_calculator.gamma`, `atm_calculator` | Rapid Momentum Execution |

---

## 7. Audit Artifacts Produced

The following 7 artifacts were generated in `docs/strategy_audit/`:

1. [strategy_inventory.csv](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/docs/strategy_audit/strategy_inventory.csv) — 100 rows detailing verified facts, structure, entry/exit rules, and gaps.
2. [strategy_classification.csv](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/docs/strategy_audit/strategy_classification.csv) — 100 rows detailing intraday vs positional classification and gap risk.
3. [strategy_migration_map.csv](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/docs/strategy_audit/strategy_migration_map.csv) — 100 rows mapping legacy code to target modules, calculations, and tests.
4. [trailing_sl_review.csv](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/docs/strategy_audit/trailing_sl_review.csv) — 100 rows evaluating trailing SL suitability and design proposals.
5. [strategy_audit_report.md](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/docs/strategy_audit/strategy_audit_report.md) — This comprehensive technical evaluation report.
6. [owner_decisions.md](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/docs/strategy_audit/owner_decisions.md) — Catalog of unresolved questions requiring project-owner approval.
7. [csv_audit_schema.md](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/docs/strategy_audit/csv_audit_schema.md) — Formal specifications for `strategy_audit.csv` and `trade_event_audit.csv`.

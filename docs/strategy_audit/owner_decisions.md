> [!NOTE]
> **HISTORICAL DOCUMENTATION — STRATEGY MODEL RETIREMENT RATIONALE**
> This decision catalog was compiled during the Phase 2A audit. The subsequent decision was taken to **completely retire the legacy strategy model** and build a clean strategy system from scratch rather than patch or migrate the legacy implementation. These questions remain preserved as historical context and reference points for designing the new strategy system.

# Project Owner Architectural Decisions & Strategic Approvals

**Location:** `docs/strategy_audit/owner_decisions.md`  
**Purpose:** Formal catalog of architectural decisions and forensic trade-offs from the Phase 2A audit.  
**Standard:** Strict compliance with [AGENTS.md](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/AGENTS.md) and quantitative engineering governance.

---

## 1. Holding Mode Decisions: Intraday vs. Positional

### Decision 1.1: Scope of Positional Holding in Mobile/Termux Environment
- **Context**: 70 of the 100 strategies are short options or long momentum scalps. However, **Basis Arbitrage (`STRAT_061`-`070`)** and **Theta Calendars (`STRAT_081`-`090`)** require multiple days or weeks for basis convergence and inter-expiry theta decay to materialize.
- **Problem**: Running multi-day positional holding on an Android Termux mobile node or a personal Windows laptop introduces severe battery sleep, process killing, power loss, and overnight gap risks.
- **Options for Owner**:
  - **Option A (Strict Intraday Only - Recommended)**: Force all 100 strategies to close at 15:15:00 IST every day. Eliminate overnight gap risk and avoid multi-day state synchronization across restarts.
  - **Option B (Selective Positional with Disk Persistence)**: Allow Calendar Spreads and Basis Arbitrage to carry overnight positions with disk-backed state (`src/storage/position_store.py`), but mandate strict margin caps and overnight stop-losses.
  - **Option C (Separate Profiles)**: Deploy an "Intraday Profile" for Termux/edge nodes and an optional "Positional Profile" for dedicated servers.

---

## 2. Multi-Leg Strategies: Iron Condors & Hedging Fidelity

### Decision 2.1: Restoring 4-Leg Wing Condors vs. Accepting Naked Strangles
- **Context**: In `strategies_config.json`, `STRAT_071` through `STRAT_080` are designated as "Wing-Hedged Iron Condors". However, the legacy engine in [strategy_engine.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategy_engine.py#L280-L290) only sells the short ATM CE and PE, leaving the position as a 100% naked short strangle with zero wing protection.
- **Options for Owner**:
  - **Option A (Restore 4-Leg Defined-Risk Condor - Recommended)**: Update the modular strategy engine to execute all 4 legs atomically:
    - Leg 1: SELL `ATM + short_offset` CE
    - Leg 2: BUY `ATM + 600` CE (from `wings_opts`)
    - Leg 3: SELL `ATM - short_offset` PE
    - Leg 4: BUY `ATM - 600` PE (from `wings_opts`)
  - **Option B (Rename and Acknowledge as Strangle)**: Reclassify Cluster 8 as "Wide Short Strangles" and accept unlimited tail risk.

### Decision 2.2: Multi-Leg Exit Atomicity vs. Independent Leg Exits
- **Context**: In the legacy engine, each leg of a multi-leg strategy is evaluated and closed independently. If Leg 1 hits SL, Leg 2 remains open and unhedged.
- **Options for Owner**:
  - **Option A (Atomic Strategy-Level Exit - Recommended)**: When strategy-level stop loss or target is triggered, square off all open legs of that trade ID simultaneously.
  - **Option B (Independent Leg Exits with Shift-to-Cost)**: Allow individual leg SL exits, but immediately move remaining open legs to break-even.

---

## 3. Futures-Spot Basis Arbitrage Remediation

### Decision 3.1: Correction of Inverted Arbitrage Direction
- **Context**: In [strategy_engine.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategy_engine.py#L272), the legacy code executes:
  `act = "SELL" if fut_ltp < spot else "BUY"`
  When Futures trades at a discount to Spot (`fut < spot`), it sells futures; when Futures trades at a premium (`fut > spot`), it buys futures. This is directionally inverted and incurs structural losses on convergence.
- **Options for Owner**:
  - **Option A (Correct to True Arbitrage - Recommended)**: Reverse the logic:
    - When `fut < spot` (Discount): BUY Futures (convergence to spot).
    - When `fut > spot` (Premium): SELL Futures (convergence to spot).
  - **Option B (Trend Momentum Interpretation)**: If the original intention was momentum breakout rather than arbitrage, rename Cluster 7 to "Futures Basis Momentum" and re-document rules.

### Decision 3.2: Single-Leg Futures vs. Dual-Leg Cash-Futures Pair
- **Context**: True basis arbitrage requires holding the offsetting Spot (or synthetic option) basket. Trading only NIFTY_FUT leaves directional exposure to market moves.
- **Options for Owner**:
  - **Option A (Synthetic Futures-Option Arbitrage)**: Offset the futures leg using synthetic ATM options (Long Futures + Long Put + Short Call).
  - **Option B (Single-Leg Futures Basis Scalping)**: Acknowledge that the bot only trades NIFTY_FUT, treating basis divergence as a statistical mean-reversion signal with strict point-based stops.

---

## 4. Technical Indicator & Quantitative Filter Calibration

### Decision 4.1: Gamma Velocity Trigger Rules (`STRAT_021`-`030`)
- **Context**: In [generate_strategies.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/generate_strategies.py#L60), strategies declare `"min_gamma": 0.0011`. The legacy engine completely ignores this field and enters unconditionally at 09:30:00.
- **Options for Owner**:
  - **Option A (Strict Gamma Filter - Recommended)**: Enforce `c.get("greeks", {}).get("gamma", 0) >= 0.0011` AND require volume > 5-tick moving average before entry.
  - **Option B (Time-Based Entry Only)**: Formally delete `min_gamma` from configuration if gamma filtering is not desired.

### Decision 4.2: Open Interest Order Flow Rules (`STRAT_031`-`040`)
- **Context**: In `strategies_config.json`, strategies declare `"mode": "BUY_CE"` or `"BUY_PE"`. The legacy engine completely ignores open interest and enters unconditionally at 09:35:00.
- **Options for Owner**:
  - **Option A (True OI Unwinding / Build-up Signals - Recommended)**: Implement real order-flow logic:
    - BUY CE: Require Call OI unwinding (Call writers covering) AND Put OI build-up.
    - BUY PE: Require Put OI unwinding (Put writers covering) AND Call OI build-up.
  - **Option B (Simple Offset Buying)**: If order flow is not required, reclassify as standard momentum options buying.

### Decision 4.3: Heavyweight Synchronization Rules (`STRAT_051`-`060`)
- **Context**: `HEAVYWEIGHT_SYNC` has zero code in the engine. It declares `"min_sync": 3` or `4` across RELIANCE, HDFC Bank, ICICI Bank, and Infosys.
- **Options for Owner**:
  - **Option A (Implement Constituent Directional Sniper - Recommended)**: Ingest `feed["heavyweights"]`. When `>= min_sync` stocks have positive net change, buy CE; when negative, buy PE.
  - **Option B (Decommission Cluster 6)**: Remove or substitute with an alternate quantitative strategy cluster if constituent tracking is not wanted.

---

## 5. Trailing Stop-Loss Activation & Calculation Approvals

### Decision 5.1: Strategy-Specific Trailing Methods
- **Context**: No trailing stop-loss is implemented in the legacy engine.
- **Options for Owner**:
  - **For Short Straddles/Strangles**: Approve Break-Even ratchet when premium decays by 20%, followed by trailing 10% steps.
  - **For Long Option Buyers**: Approve 1R profit lock (move SL to entry price once +1R is achieved; trail at 0.5R distance).
  - **For Calendar Spreads / Basis Arb**: Do not apply trailing SL; use fixed target convergence exits.

---

## 6. Position Sizing, Lot Sizes, & Capital Allocation

### Decision 6.1: Dynamic Lot Size vs. Fixed Hardcoded 75
- **Context**: `lot_size` is hardcoded to 75 across the legacy codebase. NSE NIFTY contract lot sizes change periodically under regulatory mandates.
- **Options for Owner**:
  - **Option A (Dynamic Lot Size - Recommended)**: Resolve lot size dynamically from contract metadata or [market_config.json](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/config/market_config.json).
  - **Option B (Maintain 75)**: Continue fixed 75 lot size across all strategies.

### Decision 6.2: Virtual Capital Allocation Model
- **Context**: All 100 strategies currently trade simultaneously without capital limits, which would require massive margin in live markets.
- **Options for Owner**:
  - **Option A (Fixed Lot per Strategy - Recommended for Research)**: Allow every strategy to simulate 1 fixed lot to maintain comparable statistical baselines.
  - **Option B (Portfolio Margin Cap)**: Enforce a global virtual capital limit (e.g. ₹10,00,000) where strategies queue or skip signals if available margin is exhausted.

---

## 7. Statutory Tax Rates & Cost Accounting

### Decision 7.1: Adoption of Current Union Budget F&O Tax Rates
- **Context**: The legacy `compute_taxes()` function uses outdated Futures STT (`0.000125` vs current `0.0002` / 0.02%) and applies STT and stamp duty to inverted buy/sell sides.
- **Options for Owner**:
  - **Option A (Adopt Authoritative Current Regulatory Tax Model - Recommended)**:
    - Options STT: 0.1% on SELL turnover only.
    - Futures STT: 0.02% on SELL turnover only.
    - Stamp Duty: 0.003% (options) and 0.002% (futures) on BUY turnover only.
    - GST: 18% on (Brokerage + Exchange Charges).
    - Exchange Turnover: NSE active schedules.
  - **Option B (Retain Legacy Flat Estimates)**: Keep flat legacy tax formulas for consistency with prior trade CSVs.

# Project Owner Architectural Decisions Required (Phase 2B)

**Document ID:** `DECISION-SPEC-2026-001`  
**Location:** `docs/strategy_research_design/owner_decisions_required.md`  
**Classification:** Strategic Architectural Trade-Offs & Owner Decision Registry  
**Status:** AWAITING PROJECT OWNER DETERMINATION BEFORE PHASE 3  

---

## 1. Purpose & Governance Protocol

In strict adherence to the project instructions (`AGENTS.md`) and the Lead Software Architect mandate, this document records all unresolved architectural decisions, risk trade-offs, and strategic preferences.

AI agents and developers must **never guess or invent arbitrary answers** to strategic business and risk questions. The following 7 decisions require explicit owner determination before beginning Phase 3 (Analytical Calculations & Market Engine implementation).

---

## 2. Decision Catalog

### Decision 1: Holding Horizon Discipline — Strict Intraday vs. Positional Multi-Day

- **Context**: 
  Several strategy concepts (such as Basis Arbitrage `CORE_009/010`, Calendar Spreads, and Positional Iron Condors) fundamentally rely on multi-day convergence or inter-expiry theta decay.
- **Problem**:
  Carrying multi-day positional risk on an **Android Termux mobile device** or a **personal Windows 11 workstation** introduces severe operational risks:
  - Mobile OS background battery optimization sleeping or killing Python processes.
  - WiFi drops, power outages, and laptop sleep modes.
  - Large overnight index gap risk (e.g. $\pm 300$ points) bypassing intraday stop-loss algorithms.
  - State serialization complexity across overnight machine restarts.
- **Architectural Options for Owner**:
  - **Option 1.A (Strict Intraday Only — Strongly Recommended)**:
    Force all 15 core strategies and experimental families to square off automatically at `15:15:00` IST. Zero overnight exposure. Completely eliminates overnight gap risk and ensures seamless mobile node stability.
  - **Option 1.B (Dual-Track Architecture)**:
    Create an explicit configuration flag `allow_positional_holding: true/false`. Only strategies explicitly tagged `MULTI_DAY_POSITIONAL` are allowed to carry overnight positions, requiring a hardened SQLite state persistence database and morning reconciliation routines.
- **Default Recommendation**: **Option 1.A (Strict Intraday Only)**.

---

### Decision 2: Multi-Leg Execution Atomicity — Synchronized All-or-Nothing vs. Sequential Hedge-First

- **Context**:
  Multi-leg structures (Straddles, Strangles, Spreads, Iron Condors) require executing 2 to 4 distinct option strikes. In live and paper simulation, market liquidity varies by strike.
- **Problem**:
  If a strategy attempts to sell an ATM CE and ATM PE, and one leg fills while the other leg faces a liquidity freeze or wide spread, the position becomes an accidental naked directional bet.
- **Architectural Options for Owner**:
  - **Option 2.A (Sequential Hedge-First with Auto-Orphan Square-off — Recommended)**:
    For hedged structures (Condors/Spreads), submit long protection wings first; only submit short legs once wings are filled. For neutral straddles, submit both legs concurrently; if leg 2 fails to fill within 2.0 seconds, immediately execute an emergency market square-off of leg 1.
  - **Option 2.B (Synthetic Atomic All-or-None)**:
    Simulate fills strictly as a single composite instrument (e.g. "Straddle Premium = 230.00"). Both legs fill at the identical tick or neither fills. Simplifies simulation but slightly understates real-world leg slippage.
- **Default Recommendation**: **Option 2.A**.

---

### Decision 3: Programmatic Trailing Stop-Loss (TSL) Governance Across Strategy Families

- **Context**:
  The Phase 2A audit revealed that the legacy engine had zero programmatic trailing stop-loss mechanics (only a rudimentary shift-to-cost rule on straddles).
- **Problem**:
  Different strategy families require fundamentally different trailing algorithms. A high-water-mark percentage works for trending single-leg options, while discrete step trailing is required for short straddles.
- **Architectural Options for Owner**:
  - **Option 3.A (Uniform High-Water Mark Trailing)**:
    Enforce a single trailing algorithm across all strategies: once profit reaches $+X\%$, trail peak profit by $Y\%$.
  - **Option 3.B (Family-Specific Modular TSL Models — Recommended)**:
    Support three standardized TSL models declared in `strategy_variant_schema.md`:
    1. Short Straddles/Strangles: **Shift-to-Cost on first leg SL** + **Discrete Step Trailing** on surviving leg.
    2. Momentum & Option Buying: **Continuous High-Water Mark Trailing (HWM)**.
    3. Futures Trend Following: **ATR-Based Dynamic Trailing**.
- **Default Recommendation**: **Option 3.B**.

---

### Decision 4: Strategy Activation Scope for Phase 4 Implementation

- **Context**:
  The research system defines 15 Core Strategies (`CORE_001` to `CORE_015`) and 14 Additional Extensible Families (`FAM_MOM`, `FAM_ORB`, `FAM_GAMMA_BLAST`, etc.).
- **Problem**:
  Implementing all 15 core strategies plus 14 additional families simultaneously creates a massive surface area of 100+ variants before baseline plumbing is battle-tested.
- **Architectural Options for Owner**:
  - **Option 4.A (Phased Two-Tier Rollout — Recommended)**:
    - *Tier 1 (Immediate Phase 4 Focus)*: Implement and validate the **15 Core Strategies** first (covering options buying, straddles, strangles, spreads, iron condors, futures trend, and gap strategies).
    - *Tier 2 (Subsequent Expansion)*: Add the additional extensible families (Gamma Blast, ORB, VWAP, Event-Day) once Tier 1 core execution, tax accounting, and CSV telemetry are proven rock-solid.
  - **Option 4.B (All-At-Once Implementation)**:
    Implement all 29 strategy archetypes and their parameterized variants in a single phase.
- **Default Recommendation**: **Option 4.A (Phased Two-Tier Rollout)**.

---

### Decision 5: Historical Data Ingestion Format for Offline Backtesting

- **Context**:
  The bot currently ingests real-time market data over ZeroMQ (`tcp://127.0.0.1:5555`). For offline backtesting without network sockets, historical data must be read from disk.
- **Problem**:
  What file format and directory layout should the offline replay engine standardize upon?
- **Architectural Options for Owner**:
  - **Option 5.A (Compressed JSON Lines Snapshot Files — Recommended for Native Python)**:
    Store historical tick snapshots as daily `.jsonl.gz` files under `data/historical/ticks/YYYY-MM-DD.jsonl.gz`. Requires zero external C-dependencies (native `gzip` and `json` libraries), perfectly preserving full 92-token option chain snapshots.
  - **Option 5.B (Parquet / Arrow Format)**:
    Store historical data in Apache Parquet. Offers higher compression and read speed, but introduces `pyarrow` or `fastparquet` dependencies which can be difficult to compile on Android Termux.
- **Default Recommendation**: **Option 5.A (Compressed JSONL)**.

---

### Decision 6: Portfolio Capital Allocation & Circuit Breaker Thresholds

- **Context**:
  When running multiple strategy variants simultaneously, the Portfolio Risk Manager must enforce capital caps and drawdown circuit breakers.
- **Problem**:
  What baseline virtual capital and maximum daily drawdown limit should be configured in `config/risk_config.json`?
- **Architectural Options for Owner**:
  - **Option 6.A (Conservative Retail Sizing — Recommended)**:
    - Base Virtual Capital: **₹1,000,000 (10 Lakhs)**.
    - Max Capital Allocation per Strategy Variant: **₹150,000 (1.5 Lakhs)**.
    - Max Portfolio Daily Loss Circuit Breaker: **₹25,000 (2.5% of capital)**.
    - Max Open Position Legs Across All Strategies: **16 Legs**.
  - **Option 6.B (Institutional / Prop Desk Sizing)**:
    - Base Virtual Capital: **₹5,000,000 (50 Lakhs)**.
    - Max Portfolio Daily Loss: **₹100,000 (2.0% of capital)**.
    - Max Open Position Legs: **40 Legs**.
- **Default Recommendation**: **Option 6.A (Conservative Retail Sizing)**.

---

### Decision 7: NIFTY Lot Size Evolution & Multi-Contract Expiry Governance

- **Context**:
  The National Stock Exchange of India (NSE) periodically revises derivative contract lot sizes:
  - Historically, NIFTY 50 lot size was 50, then revised to 75, and recently revised to 25.
  - Furthermore, weekly contracts expire every Thursday, while monthly contracts trade simultaneously.
- **Problem**:
  How should contract lot sizing and expiry resolution be handled in the calculations and execution layers?
- **Architectural Options for Owner**:
  - **Option 7.A (Dynamic Date-Mapped Instrument Master — Recommended)**:
    Store an internal date-bounded lookup mapping:
    - Dates prior to April 2024: Lot Size = 50
    - April 2024 to November 2024: Lot Size = 75
    - November 2024 onwards: Lot Size = 25 (or current active NSE standard)
    - Default to active configuration key `lot_size` in `config/market_config.json`.
  - **Option 7.B (Static Config Key)**:
    Use a single fixed lot size integer in configuration (`"lot_size": 75`) across all backtests and paper trading runs.
- **Default Recommendation**: **Option 7.A (Dynamic Date-Mapped Master with Config Fallback)**.

---

## 3. Decision Sign-Off Summary Table

| Decision # | Core Question | Recommended Option | Alternative Option | Status |
| :---: | :--- | :--- | :--- | :---: |
| **D-1** | Holding Horizon Discipline | **Option 1.A (Strict Intraday Only, 15:15 Square-off)** | Option 1.B (Dual-Track Positional) | `PENDING OWNER APPROVAL` |
| **D-2** | Multi-Leg Execution Atomicity | **Option 2.A (Hedge-First with Auto-Orphan Square-off)** | Option 2.B (Synthetic Atomic Fill) | `PENDING OWNER APPROVAL` |
| **D-3** | Trailing Stop-Loss Governance | **Option 3.B (Modular Model per Family)** | Option 3.A (Uniform Percentage HWM) | `PENDING OWNER APPROVAL` |
| **D-4** | Strategy Activation Scope | **Option 4.A (Phased 15 Core Strategies First)** | Option 4.B (All 29 Archetypes at Once) | `PENDING OWNER APPROVAL` |
| **D-5** | Historical Backtest Data Format | **Option 5.A (Native JSONL.gz Snapshots)** | Option 5.B (Parquet / Arrow) | `PENDING OWNER APPROVAL` |
| **D-6** | Portfolio Capital & Risk Caps | **Option 6.A (₹10 Lakh Capital, ₹25k Max Daily Loss)** | Option 6.B (₹50 Lakh Capital) | `PENDING OWNER APPROVAL` |
| **D-7** | NIFTY Lot Size & Expiry Rules | **Option 7.A (Dynamic Date-Mapped Master)** | Option 7.B (Static Config Integer) | `PENDING OWNER APPROVAL` |

# Risk Management & Statutory Accounting Specification

**Document ID:** `RISK-ACCT-SPEC-2026-001`  
**Location:** `docs/strategy_research_design/risk_and_accounting_specification.md`  
**Classification:** Quantitative Risk Controls & Indian Statutory Tax Modeling  
**Status:** PROPOSED TECHNICAL SPECIFICATION  

---

## 1. Architectural Mission

A robust quantitative algorithmic platform must maintain dual-layered risk governance and rigorous transactional accounting:
1. **Multi-Tier Risk Governance**: Protecting virtual capital through granular position-level rules, variant-level controls, and overarching portfolio circuit breakers.
2. **Statutory Indian F&O Tax Modeling**: Eliminating the illusion of "paper profits" by computing every trade's true net yield after applying exact Indian regulatory taxes and exchange fees (effective FY 2024–2026).

---

## 2. Multi-Tier Risk Architecture

Risk is governed across three hierarchical levels:

```
[ LEVEL 1: PORTFOLIO RISK CONTROLLER ]
  - Max Aggregate Portfolio Daily Loss Limit (e.g. ₹25,000)
  - Max Aggregate Open Position Legs Limit (e.g. 20 legs)
  - Portfolio Drawdown Circuit Breaker (e.g. -10% of total capital)
  - Global Market Volatility Lock (VIX > 25.0 freeze)
                 │
                 ▼
[ LEVEL 2: STRATEGY VARIANT RISK CONTROLLER ]
  - Max Capital Allocation per Variant (e.g. ₹200,000)
  - Max Daily Loss per Variant (e.g. ₹5,000)
  - Max Entries per Day (e.g. 1 entry)
  - Cooldown Window after Stop-Loss (e.g. 300 seconds)
                 │
                 ▼
[ LEVEL 3: POSITION & MULTI-LEG LIFECYCLE CONTROLLER ]
  - Initial Stop-Loss (Fixed points / % entry LTP / ATR multiple)
  - Trailing Stop-Loss (High-water mark / Step trailing)
  - Shift-to-Cost Rule on 2-leg straddles
  - Orphan Leg Fail-Safe (Immediate square-off of naked exposure)
```

---

## 3. Position Lifecycle & Multi-Leg Safety Algorithms

### 3.1 Multi-Leg Atomic Execution & Orphaned Leg Protection
In multi-leg structures (Straddles, Strangles, Iron Condors), asymmetric fills can create catastrophic unhedged exposures.
- **Execution Synchronization**:
  1. For Debit Spreads & Iron Condors: Long wing protection legs must be submitted and confirmed **first** before short credit legs are executed (`SEQUENTIAL_HEDGE_FIRST`).
  2. For Neutral Straddles/Strangles: Both legs are dispatched in a single synchronized burst (`ATOMIC_ALL_OR_NONE`).
- **Orphan Leg Fail-Safe**:
  If Leg 1 fills but Leg 2 fails or is rejected within $\Delta t = 2.0\text{ seconds}$, the position controller triggers an immediate emergency market order to liquidate Leg 1. Naked single-leg exposure is never permitted to float unmonitored.

### 3.2 Shift-to-Cost Logic
For two-leg short straddles and strangles:
- **Trigger**: When Leg 1 hits its initial stop-loss (+25% of entry price) and is closed, the position controller immediately inspects Leg 2.
- **Action**: Leg 2's stop-loss price is automatically modified to its **original entry LTP** (cost price).
- **Target Exception**: If Leg 1 exited due to hitting its **profit target** rather than a stop-loss, Leg 2 retains its standard trailing SL and is NOT shifted to cost.

### 3.3 Programmatic Trailing Stop-Loss (TSL) Algorithms
The engine supports three distinct, unambiguous mathematical models for trailing stops:

#### Model A: High-Water Mark Trailing (HWM)
Trails favorable peak profit continuously:
- For Short Option Position: Peak favorable price is the lowest recorded LTP ($P_{\min}$).
  $$\text{TSL Price}(t) = P_{\min}(t) \times (1.0 + \text{TrailPct})$$
- For Long Option / Futures: Peak favorable price is the highest recorded LTP ($P_{\max}$).
  $$\text{TSL Price}(t) = P_{\max}(t) \times (1.0 - \text{TrailPct})$$

#### Model B: Discrete Step Trailing
Advances the stop price in discrete increments once a profit hurdle is crossed:
- **Condition**: Once unrealized profit reaches $X$ points, lock in $Y$ points.
- For every additional $S$ points of favorable movement, step the stop price forward by $S$ points.

#### Model C: ATR-Based Dynamic Trailing
Trails price using a multiple of current 1-minute Average True Range (ATR):
$$\text{TSL Price}(t) = \text{LTP}(t) \pm (k \times \text{ATR}_{14})$$
Where $k \in [1.5, 3.0]$ is a configurable volatility multiplier.

---

## 4. Statutory Indian F&O Taxation & Transaction Cost Accounting

To reflect exact real-world net P&L, the **Tax & Accounting Engine (`src/accounting/tax_engine.py`)** applies the post-Budget 2024 statutory tax structure for Indian National Stock Exchange (NSE) equity derivatives:

```
[ GROSS P&L ] = (Exit Price - Entry Price) * Quantity * Side_Multiplier
      │
      ├── Less: Brokerage Fees (Flat ₹20 per executed order)
      ├── Less: Securities Transaction Tax (STT - Sell Side Only)
      ├── Less: Exchange Transaction Charges (NSE Fee)
      ├── Less: Goods & Services Tax (GST - 18% on Brokerage + Exch Fee)
      ├── Less: SEBI Turnover Charges
      ├── Less: Stamp Duty (Buy Side Only)
      ▼
[ NET P&L ] = Gross P&L - Total Statutory Deductions
```

### 4.1 Exact Mathematical Formulas & Statutory Rates

| Statutory Fee Component | Regulatory Governing Body | Options Contracts Formula & Rate | Futures Contracts Formula & Rate | Applicable Side |
| :--- | :--- | :--- | :--- | :--- |
| **Brokerage** | Discount Broker Standard | ₹20.00 flat per executed order | ₹20.00 flat per executed order | Both Buy & Sell (Round-trip = ₹40) |
| **Securities Transaction Tax (STT)** | Ministry of Finance (Budget 2024) | **0.10% (0.0010)** on Premium Turnover | **0.02% (0.0002)** on Notional Turnover | **SELL Side Only** |
| **Exchange Transaction Charges** | National Stock Exchange (NSE) | **0.050% (0.00050)** on Premium Turnover | **0.00173% (0.0000173)** on Notional Turnover | Both Buy & Sell |
| **SEBI Turnover Charges** | SEBI | **₹10 per Crore (0.000001)** on Turnover | **₹10 per Crore (0.000001)** on Turnover | Both Buy & Sell |
| **Stamp Duty** | Indian Stamp Act (State Govt) | **0.003% (0.00003)** on Premium Turnover | **0.002% (0.00002)** on Notional Turnover | **BUY Side Only** |
| **Goods & Services Tax (GST)** | Central & State GST (18%) | **18%** on $(\text{Brokerage} + \text{Exchange Charges} + \text{SEBI Fee})$ | **18%** on $(\text{Brokerage} + \text{Exchange Charges} + \text{SEBI Fee})$ | Both Buy & Sell |

### 4.2 Comprehensive Calculation Example (NIFTY Short Straddle)

Consider 1 lot (75 quantity) NIFTY short straddle:
- **Entry**: Sell 24800 CE @ 120.00, Sell 24800 PE @ 110.00 (Total Premium = 230.00)
- **Exit**: Buy back 24800 CE @ 80.00, Buy back 24800 PE @ 75.00 (Total Premium = 155.00)
- **Gross Profit**: $(230.00 - 155.00) \times 75 = \mathbf{+₹5,625.00}$

**Statutory Deductions Breakdown:**
1. **Turnover**:
   - CE Sell Turnover: $120.00 \times 75 = ₹9,000$
   - CE Buy Turnover: $80.00 \times 75 = ₹6,000$
   - PE Sell Turnover: $110.00 \times 75 = ₹8,250$
   - PE Buy Turnover: $75.00 \times 75 = ₹5,625$
   - Total Premium Turnover: $₹28,875.00$
   - Total Sell Turnover: $9,000 + 8,250 = ₹17,250.00$
   - Total Buy Turnover: $6,000 + 5,625 = ₹11,625.00$
2. **Brokerage**: 4 orders $\times ₹20.00 = \mathbf{₹80.00}$
3. **STT (Sell Side Only)**: $0.1\% \times ₹17,250.00 = \mathbf{₹17.25}$
4. **Exchange Transaction Charges**: $0.05\% \times ₹28,875.00 = \mathbf{₹14.44}$
5. **SEBI Charges**: $0.000001 \times ₹28,875.00 = \mathbf{₹0.03}$
6. **Stamp Duty (Buy Side Only)**: $0.003\% \times ₹11,625.00 = \mathbf{₹0.35}$
7. **GST (18%)**: $18\% \times (80.00 + 14.44 + 0.03) = 18\% \times 94.47 = \mathbf{₹17.00}$
8. **Total Taxes & Charges**: $80.00 + 17.25 + 14.44 + 0.03 + 0.35 + 17.00 = \mathbf{₹129.07}$
9. **Net Realized Profit**: $5,625.00 - 129.07 = \mathbf{+₹5,495.93}$ (Friction Drag: $2.29\%$)

---

## 5. Portfolio Circuit Breakers & Emergency Shutdown

The Portfolio Risk Manager enforces deterministic circuit breakers across all concurrently running strategies:

```python
class PortfolioRiskGovernor:
    MAX_DAILY_LOSS_LIMIT = 25000.0       # INR
    MAX_OPEN_LEGS_LIMIT = 20
    MAX_PORTFOLIO_DRAWDOWN_PCT = 10.0   # Percent of peak equity
    VIX_EXTREME_THRESHOLD = 26.0

    def evaluate_portfolio_health(self, current_state):
        # Rule 1: Daily Loss Circuit Breaker
        if current_state.realized_pnl + current_state.unrealized_mtm <= -self.MAX_DAILY_LOSS_LIMIT:
            return self.trigger_emergency_shutdown("DAILY_LOSS_LIMIT_BREACHED")

        # Rule 2: Open Exposure Cap
        if current_state.open_legs_count > self.MAX_OPEN_LEGS_LIMIT:
            return self.suppress_new_entries("MAX_OPEN_LEGS_EXCEEDED")

        # Rule 3: Extreme Volatility Freeze
        if current_state.india_vix >= self.VIX_EXTREME_THRESHOLD:
            return self.suppress_new_entries("VIX_EXTREME_VOLATILITY_FREEZE")
            
        return "HEALTHY"
```

In the event of an emergency shutdown:
1. `strategy_execution_enabled` is immediately flipped to `False` in memory.
2. All pending simulated limit orders are canceled.
3. Market square-off orders are dispatched sequentially for all open positions.
4. An emergency alert event is logged to `logs/audit/circuit_breaker.log`.

# Quantitative Performance Evaluation & Attribution Metrics

**Document ID:** `METRICS-SPEC-2026-001`  
**Location:** `docs/strategy_research_design/performance_metrics.md`  
**Classification:** Quantitative Statistics & Performance Attribution Framework  
**Status:** PROPOSED TECHNICAL SPECIFICATION  

---

## 1. Architectural Mission

The **Performance Analytics Engine (`src/monitoring/performance.py`)** provides rigorous, unbiased quantitative evaluation across all backtests, simulated paper trading runs, and prospective live telemetry.

### Core Evaluation Principles
1. **Net Realized Basis**: No strategy is judged on gross P&L. Every metric is computed strictly **net of Indian statutory taxes, exchange fees, and execution slippage**.
2. **Multi-Dimensional Attribution**: Performance is decomposed across market regimes, weekdays, session periods (morning, mid-day, afternoon), and contract expiries to detect conditional fragility.
3. **Overfitting & Robustness Governance**: Incorporates statistical penalty metrics (Deflated Sharpe Ratio, Probability of Backtest Overfitting) to prevent selecting lucky variants.

---

## 2. Mathematical Metric Definitions & Formulas

Let $N$ denote the total number of completed trades for a strategy variant, with individual net trade P&Ls denoted by $x_1, x_2, \dots, x_N$.
Let $W = \{x_i \mid x_i > 0\}$ be the set of winning trades ($N_W = |W|$), and $L = \{x_i \mid x_i \le 0\}$ be the set of losing trades ($N_L = |L|$).

```
                             [ PERFORMANCE TAXONOMY ]
                                        │
         ┌──────────────────────┬───────┴──────────────┬─────────────────────┐
         ▼                      ▼                      ▼                     ▼
 [ RETURN METRICS ]     [ RISK & DRAWDOWN ]    [ EXPECTANCY RATIOS ]  [ FRICTION DRAG ]
 - Net Cumulative P&L   - Maximum Drawdown     - Profit Factor        - Tax Drag %
 - Return on Capital    - Max DD Duration      - Payoff Ratio         - Slippage Drag
 - Annualized Return    - Calmar / Sharpe      - Math Expectancy      - Net / Gross Yield
```

### 2.1 Return & Yield Metrics

#### 1. Net Realized Cumulative P&L ($P_{\text{net}}$)
$$P_{\text{net}} = \sum_{i=1}^{N} x_i = P_{\text{gross}} - \sum_{i=1}^{N} \text{Taxes}_i - \sum_{i=1}^{N} \text{Slippage}_i$$

#### 2. Return on Capital (ROC)
$$\text{ROC} = \frac{P_{\text{net}}}{\text{Capital}_{\text{allocated}}} \times 100\%$$

#### 3. Annualized Compound Return (CAGR)
$$\text{CAGR} = \left( 1.0 + \frac{P_{\text{net}}}{\text{Initial Capital}} \right)^{\frac{252}{T_{\text{days}}}} - 1.0$$

---

### 2.2 Trade Distribution & Expectancy Metrics

#### 4. Win Rate ($W\%$) & Loss Rate ($L\%$)
$$W\% = \frac{N_W}{N} \times 100\%, \quad L\% = \frac{N_L}{N} \times 100\%$$

#### 5. Average Win ($\bar{W}$) & Average Loss ($\bar{L}$)
$$\bar{W} = \frac{1}{N_W} \sum_{x \in W} x, \quad \bar{L} = \frac{1}{N_L} \sum_{x \in L} |x|$$

#### 6. Payoff Ratio (Win / Loss Ratio)
$$R_{\text{payoff}} = \frac{\bar{W}}{\bar{L}}$$

#### 7. Profit Factor ($PF$)
$$PF = \frac{\sum_{x \in W} x}{\sum_{x \in L} |x|}$$
- **Hurdle**: A strategy must achieve a net $PF \ge 1.35$ after all statutory taxes and slippage to be eligible for production paper trading.

#### 8. Mathematical Expectancy ($E$) per Trade
$$E = (W\% \times \bar{W}) - (L\% \times \bar{L})$$
- Represents the average expected rupee profit generated per trade.
- Must be strictly positive ($E > 0$) across out-of-sample test windows.

---

### 2.3 Risk & Drawdown Metrics

#### 9. Maximum Drawdown (MDD)
Let $C_t$ be the cumulative equity curve at trade $t$:
$$C_t = \sum_{i=1}^{t} x_i, \quad H_t = \max_{0 \le s \le t} C_s$$
$$\text{DD}_t = H_t - C_t, \quad \text{MDD} = \max_{1 \le t \le N} \text{DD}_t$$
$$\text{MDD}_{\%} = \max_{1 \le t \le N} \left( \frac{H_t - C_t}{\text{Peak Capital}_t} \right) \times 100\%$$

#### 10. Maximum Drawdown Duration ($T_{\text{MDD}}$)
The maximum number of trading sessions elapsed from an equity peak to a new equity high.

#### 11. Sharpe Ratio ($SR$)
$$SR = \frac{\bar{r}_d - r_f}{\sigma_d} \times \sqrt{252}$$
Where $\bar{r}_d$ is mean daily net return, $r_f$ is daily risk-free rate (6.5% Indian 91-day T-bill annualized), and $\sigma_d$ is standard deviation of daily returns.

#### 12. Sortino Ratio
Measures return relative solely to downside volatility:
$$\text{Sortino} = \frac{\bar{r}_d - r_f}{\sigma_{\text{downside}}} \times \sqrt{252}, \quad \sigma_{\text{downside}} = \sqrt{\frac{1}{N_d} \sum_{r_i < r_f} (r_i - r_f)^2}$$

#### 13. Calmar Ratio
$$\text{Calmar} = \frac{\text{CAGR}}{\text{MDD}_{\%}}$$

---

### 2.4 Transaction Friction & Efficiency Metrics

#### 14. Statutory Friction Drag ($\text{Drag}_{\%}$)
Measures the percentage of gross profits consumed by Indian regulatory taxes, exchange fees, and brokerage:
$$\text{Drag}_{\%} = \frac{\sum_{i=1}^{N} \text{Taxes}_i + \sum_{i=1}^{N} \text{Brokerage}_i}{P_{\text{gross}}} \times 100\%$$
- High-frequency options scalping strategies that display large gross profits but suffer $\text{Drag}_{\%} > 35\%$ are classified as **economically unviable**.

#### 15. Slippage Erosion Rate
$$\text{Slippage Erosion}_{\%} = \frac{\sum_{i=1}^{N} \text{Slippage Cost}_i}{P_{\text{gross}}} \times 100\%$$

---

## 3. Dimensional Performance Attribution Slices

To uncover hidden vulnerabilities, every strategy variant's performance is automatically sliced across four distinct dimensions:

### 3.1 Regime Attribution Matrix
Evaluates whether strategy alpha is concentrated in specific regimes:
- Net P&L in `STRONG_BULLISH_TREND` vs `STRONG_BEARISH_TREND`
- Net P&L in `SIDEWAYS_RANGE_BOUND`
- Net P&L in `HIGH_VOLATILITY` vs `LOW_VOLATILITY`
- Net P&L in `OPENING_GAP` sessions

### 3.2 Day-of-Week Attribution
Measures edge consistency across Monday, Tuesday, Wednesday, Thursday, and Friday.
- Highlights whether strategies rely on specific expiry decay days (e.g. Thursday NIFTY expiry or Tuesday FINNIFTY expiry).

### 3.3 Intraday Session Phase Attribution
- **Morning Open (09:15–10:30 IST)**: High volatility, opening discovery, high slippage.
- **Mid-Day Consolidation (10:30–13:30 IST)**: Institutional decay, range-bound chop.
- **Afternoon Expiry / Trend (13:30–15:15 IST)**: Gamma expansion, trend re-acceleration, institutional unwinding.

### 3.4 Holding Duration & Capital Utilization
- Average holding time per trade (in minutes).
- Percentage of trading session capital is actively committed vs idle cash.

---

## 4. Minimum Statistical Validation Hurdles (Pass/Fail)

Before any strategy variant can graduate from **Research Prototype** to **Active Paper Trading**, it must satisfy all 8 empirical criteria across out-of-sample data:

| Metric | Minimum Passing Hurdle | Rationale |
| :--- | :--- | :--- |
| **Total Out-of-Sample Trades** | $N \ge 100$ closed trades | Statistical power ($p < 0.05$ significance). |
| **Net Profit Factor** | $PF_{\text{net}} \ge 1.35$ | Buffer against adverse market regime shifts. |
| **Mathematical Expectancy** | $E \ge ₹350.00$ per lot | Ensures edge survives real-world slippage. |
| **Maximum Drawdown** | $\text{MDD}_{\%} \le 15.0\%$ | Preserves capital during drawdown streaks. |
| **Calmar Ratio** | $\text{Calmar} \ge 1.20$ | Ensures returns justify downside risk. |
| **Statutory Friction Drag** | $\text{Drag}_{\%} \le 25.0\%$ | Prevents broker/tax erosion of profits. |
| **Deflated Sharpe Ratio** | $\text{DSR} \ge 0.95$ | Corrects for multiple testing / data mining. |
| **Monte Carlo 95th Percentile DD** | Observed $\text{MDD} \le \text{MC}_{95}$ | Validates drawdown is not an outlier fluke. |

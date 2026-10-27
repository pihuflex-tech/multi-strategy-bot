---
name: paper-trading-engineer
description: >-
  Specialist skill for paper-trading execution, statutory tax modeling, slippage simulation,
  position management, trade logging, and telemetry integration in the NIFTY Multi-Strategy Bot.
---

# Paper Trading Engineer (Specialist Skill)

The `paper-trading-engineer` skill governs the simulation fidelity, statutory tax accuracy, trade execution mechanics, risk accounting, and telemetry pipeline of the `multi-strategy-bot`.

---

## Core Responsibilities

1. **Simulation Fidelity & Order Fill Modeling**:
   - Model execution price fills realistically using LTP plus/minus simulated bid-ask slippage.
   - Apply slippage symmetrically (deducted on entries as well as exits).
   - Ensure simulated fills do not assume unrealistic liquidity or zero-impact fills on illiquid far-OTM strikes.
2. **Statutory Tax & Transaction Cost Engine**:
   - Audit and maintain statutory F&O transaction charges in `compute_taxes()`:
     - **Securities Transaction Tax (STT)**: Options 0.1% of turnover on **SELL side only**; Futures 0.02% of turnover on **SELL side only**.
     - **Exchange Turnover Charges**: NSE F&O rates on total turnover.
     - **Goods & Services Tax (GST)**: 18% on (Brokerage + Exchange Charges).
     - **Stamp Duty**: Options 0.003% on **BUY side only**; Futures 0.002% on **BUY side only**.
     - **Brokerage**: Flat ₹40 per round trip (₹20 per order).
   - Verify applicable statutory rates against authoritative regulatory sources before changing formulas.
3. **Position Sizing & Lot Size Integrity**:
   - Avoid hardcoding stale lot sizes. Support dynamic contract lot sizes aligned with exchange regulatory specifications.
   - Ensure positions maintain isolated accounting per strategy ID (`self.positions[s_id]`).
4. **Telemetry, State Persistence, & Dashboard**:
   - Manage real-time state serialization (`logs/live_state.json`) via atomic temporary writes.
   - Guard against file-locking collisions on Windows (`PermissionError` handling with retries).
   - Align telemetry JSON schemas with the web dashboard ([dashboard.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/dashboard.py)) to ensure live metrics reflect reality.
   - Ensure trade records are logged asynchronously via `queue.Queue` to `logs/trades/trades_*.csv` without blocking snapshot processing.
5. **Absolute Paper-Trading Guard**:
   - Never implement, enable, or import live broker trading APIs. All order fills must remain simulated.

---

## Verification & Audit Checklist

When auditing paper-trading components, verify:
- [ ] Are trade entry prices subject to realistic slippage?
- [ ] Is STT charged exclusively on the sell leg (entry for short options, exit for long options)?
- [ ] Is Stamp Duty charged exclusively on the buy leg (entry for long options, exit for short options)?
- [ ] Does gross P&L correctly distinguish between BUY (`(exit - entry) * qty`) and SELL (`(entry - exit) * qty`)?
- [ ] Does `logs/live_state.json` serialize atomically without crashing on Windows?
- [ ] Does `dashboard.py` consume `logs/live_state.json` without failing or searching invalid paths?
- [ ] Are trade logs written with all 19 standardized columns matching `CSV_HEADER`?

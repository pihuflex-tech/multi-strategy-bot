---
name: trading-system-tester
description: >-
  Specialist skill for deterministic offline testing, test fixture design, and regression
  verification in the NIFTY Multi-Strategy Bot. Use to construct synthetic tick snapshots,
  validate strategy boundaries, verify tax math, and produce empirical test evidence.
---

# Trading System Tester (Specialist Skill)

The `trading-system-tester` skill is responsible for the design, execution, and reporting of deterministic, offline automated regression tests for the `multi-strategy-bot` codebase.

---

## Core Responsibilities

1. **Deterministic Offline Test Suites**:
   - Construct robust unit and regression tests using `pytest` and standard Python testing tools.
   - Design static, deterministic market snapshot fixtures representing realistic tick payloads (Spot, Futures, VIX, Heavyweights, Options chain with Greeks).
   - Ensure tests execute strictly offline without requiring a live market feed, broker credentials, or active ZeroMQ socket connections.
2. **Boundary & Edge Case Verification**:
   - **Market Session Boundaries**: Test entry behavior at pre-market (`08:00:00`), market open (`09:15:00`), intermediate entry times, and post-market (`16:00:00`). Verify no trades enter outside market hours.
   - **Malformed & Degenerate Ticks**: Verify engine resilience against empty snapshots, missing strikes, zero LTPs, missing keys, and corrupted payloads.
   - **Stale Data Ingestion**: Verify that snapshots with stale timestamps or disconnected feed statuses are detected and rejected.
   - **Exit Mechanics**: Verify that stop-loss, target, and EOD time exits trigger at mathematically exact thresholds.
   - **Tax Math Accuracy**: Verify that `compute_taxes()` matches exact statutory formulas for both BUY-first and SELL-first trades.
3. **Rigorous Evidence Reporting**:
   - Categorize every test item strictly into the standard taxonomy:
     - **`PASSED`**: Directly executed and confirmed with command output.
     - **`FAILED`**: Executed and encountered an error or assertion mismatch.
     - **`SKIPPED`**: Inapplicable to the current scope.
     - **`UNTESTED`**: Applicable but unexecuted due to environmental constraints.
   - Never report a test as passed unless it was genuinely executed with verifiable output.

---

## Synthetic Snapshot Fixture Standard

Deterministic test fixtures should follow this standard in-memory snapshot structure:

```python
MOCK_SNAPSHOT = {
    "server_status": "RUNNING",
    "timestamp": "2026-10-25 09:20:00.000",
    "reconnect_count": 0,
    "last_error": None,
    "spot": {"symbol": "NIFTY 50", "ltp": 22800.0, "high": 22850.0, "low": 22750.0, "net_change": 50.0},
    "indices": {"india_vix": {"ltp": 15.0, "net_change": 0.0}},
    "heavyweights": {
        "RELIANCE-EQ": {"ltp": 2900.0, "net_change": 15.0, "volume": 100000},
        "HDFCBANK-EQ": {"ltp": 1500.0, "net_change": 10.0, "volume": 150000},
        "ICICIBANK-EQ": {"ltp": 1100.0, "net_change": 8.0, "volume": 120000},
        "INFY-EQ": {"ltp": 1450.0, "net_change": 5.0, "volume": 90000}
    },
    "futures": {"ltp": 22820.0, "open_interest": 10000000, "volume": 500000, "net_change": 45.0},
    "options": {
        "current_expiry": {
            "22800_CE": {"strike": 22800.0, "option_type": "CE", "expiry": "28OCT2026", "ltp": 120.0, "greeks": {"iv": 14.0, "delta": 0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0}},
            "22800_PE": {"strike": 22800.0, "option_type": "PE", "expiry": "28OCT2026", "ltp": 115.0, "greeks": {"iv": 14.5, "delta": -0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0}}
        },
        "wings": {
            "23400_CE": {"strike": 23400.0, "option_type": "CE", "expiry": "28OCT2026", "ltp": 8.0},
            "22200_PE": {"strike": 22200.0, "option_type": "PE", "expiry": "28OCT2026", "ltp": 9.0}
        }
    }
}
```

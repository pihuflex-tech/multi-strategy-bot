# CSV Audit Engine: Schema Specification & Data Integrity Standard

**Location:** `docs/strategy_audit/csv_audit_schema.md`  
**Purpose:** Formal specification for strategy audit logs and future real paper-trading event streams.  
**Standard:** 100% auditable, replayable, deterministic, and idempotent.

---

## 1. Overview and Architecture

The audit subsystem ensures every strategy decision, entry trigger, stop-loss adjustment, trailing ratchet, exit fill, and risk event is captured deterministically.

To maintain dual-platform efficiency (Windows development and Android Termux mobile nodes), audit logging is decoupled into two primary schemas:
1. **`strategy_audit.csv`**: Point-in-time and session-level strategy configuration, lifecycle status, and operational verification audits.
2. **`trade_event_audit.csv`**: Granular, append-only, sequential trade event streams capturing simulated execution receipts, state mutations, and telemetry context.

---

## 2. Specification: `strategy_audit.csv`

Records the operational audit record of every registered strategy across versions and market sessions.

### Schema Table

| Column Name | Data Type | Nullable | Description / Allowed Values | Example |
| :--- | :--- | :---: | :--- | :--- |
| `audit_timestamp` | ISO8601 String | No | Timestamp when the audit was generated (`YYYY-MM-DD HH:MM:SS.fff`). | `2026-10-25 15:00:00.000` |
| `audit_version` | String | No | Version of the strategy audit specification. | `v2.0-phase2a` |
| `strategy_id` | String | No | Unique strategy identifier (`STRAT_001` - `STRAT_100`). | `STRAT_071` |
| `strategy_name` | String | No | Descriptive strategy name from configuration. | `Condor_0925_Off0` |
| `family` | String | No | Strategy cluster/family name. | `Wing_Condors` |
| `rule_type` | String | No | Strategy engine dispatcher rule identifier. | `WING_CONDOR` |
| `implementation_status` | Enum | No | `IMPLEMENTED`, `PARTIAL`, `DEFECTIVE`, `MISSING`, `UNVERIFIED`. | `DEFECTIVE` |
| `recommended_mode` | Enum | No | `INTRADAY`, `POSITIONAL`, `CONDITIONAL`, `UNCLASSIFIED`. | `CONDITIONAL` |
| `structure_legs` | Integer | No | Number of legs required by the strategy definition. | `4` |
| `is_hedged` | Enum | No | `YES`, `NO`, `PARTIAL`. | `NO` |
| `entry_time` | String | No | Daily scheduled entry trigger time (`HH:MM:SS`). | `09:25:00` |
| `exit_time` | String | No | Daily scheduled mandatory exit time (`HH:MM:SS`). | `15:15:00` |
| `initial_sl_pct` | Float | Yes | Initial stop-loss percentage from entry premium. | `30.0` |
| `target_pct` | Float | Yes | Initial profit target percentage from entry premium. | `45.0` |
| `trailing_sl_status` | Enum | No | `ENABLED`, `DISABLED`, `BREAK_EVEN_ONLY`, `PROPOSED`. | `PROPOSED` |
| `missing_components` | String | Yes | Specific missing code, handlers, or hedge legs. | `Missing 2 long wing legs` |
| `risk_observations` | String | Yes | Risk notes (e.g. unhedged tail risk, gap risk). | `Naked short strangle risk` |
| `evidence_reference` | String | No | Source file and line reference proving status. | `strategy_engine.py:L280` |
| `owner_decision_pending` | String | Yes | Pending owner architectural decision. | `Approve 4-leg condor model` |

---

## 3. Specification: `trade_event_audit.csv`

Records every state mutation, order submission, fill execution, trailing SL movement, and exit event in real paper-trading execution.

### Schema Table

| Column Name | Data Type | Nullable | Description / Allowed Values | Example |
| :--- | :--- | :---: | :--- | :--- |
| `event_id` | String | No | Monotonically increasing unique event UUID or sequence ID. | `EVT-20261025-00042` |
| `timestamp` | ISO8601 String | No | Host execution timestamp with millisecond resolution. | `2026-10-25 09:25:01.124` |
| `sequence_num` | Integer | No | Strictly incrementing integer per trading session. | `42` |
| `strategy_id` | String | No | Unique strategy identifier. | `STRAT_001` |
| `trade_id` | String | No | Unique identifier grouping legs of a specific trade lifecycle. | `TRD-STRAT001-20261025-01` |
| `event_type` | Enum | No | `SIGNAL_GENERATED`, `ORDER_SUBMITTED`, `ORDER_FILLED`, `SL_INITIALIZED`, `SL_SHIFT_COST`, `TRAILING_SL_UPDATED`, `TARGET_HIT`, `SL_HIT`, `EOD_SQUAREOFF`, `EMERGENCY_EXIT`, `ORDER_REJECTED`. | `TRAILING_SL_UPDATED` |
| `instrument_symbol` | String | No | Exchange trading symbol or synthetic key. | `NIFTY26OCT22800CE` |
| `underlying` | String | No | Underlying index or asset name. | `NIFTY` |
| `strike` | Float | Yes | Strike price (0.0 for futures). | `22800.0` |
| `opt_type` | Enum | No | `CE`, `PE`, `FUT`. | `CE` |
| `expiry` | String | No | Contract expiry string. | `28OCT2026` |
| `direction` | Enum | No | `BUY`, `SELL`. | `SELL` |
| `quantity` | Integer | No | Traded contract quantity (shares). | `75` |
| `market_ltp` | Float | No | Last traded price at the instant of event. | `110.50` |
| `execution_price` | Float | Yes | Simulated fill price including slippage. | `110.90` |
| `slippage_points` | Float | No | Applied slippage penalty in index points. | `0.40` |
| `previous_sl_price` | Float | Yes | Stop-loss price prior to this event. | `144.00` |
| `new_sl_price` | Float | Yes | Stop-loss price resulting from this event. | `120.00` |
| `trailing_method` | Enum | Yes | `NONE`, `SHIFT_TO_COST`, `PREMIUM_PCT`, `R_MULTIPLE`, `ATR`. | `SHIFT_TO_COST` |
| `trailing_trigger_value` | Float | Yes | Indicator or price value that triggered the trail. | `120.00` |
| `exit_reason` | Enum | Yes | `SL_HIT`, `TARGET_HIT`, `EOD_TIME`, `EMERGENCY_RISK`, `DESYNC`. | `SL_HIT` |
| `gross_pnl` | Float | Yes | Gross realized P&L for this fill (Rupees). | `+712.50` |
| `brokerage` | Float | Yes | Brokerage fee (₹20 per executed leg). | `20.00` |
| `stt` | Float | Yes | Statutory Securities Transaction Tax (Rupees). | `8.29` |
| `exchange_charges` | Float | Yes | Exchange turnover fees (Rupees). | `4.15` |
| `gst` | Float | Yes | GST at 18% on (Brokerage + Exchange Charges). | `4.35` |
| `stamp_duty` | Float | Yes | Statutory Stamp Duty (Rupees). | `0.25` |
| `total_charges` | Float | Yes | Total statutory transaction costs (Rupees). | `37.04` |
| `net_pnl` | Float | Yes | Net realized P&L after all charges (Rupees). | `+675.46` |
| `feed_status` | Enum | No | `RUNNING`, `STALE_TICK`, `RECONNECTING`, `DEGRADED`. | `RUNNING` |
| `tick_latency_ms` | Float | Yes | Difference between tick timestamp and event timestamp. | `12.4` |
| `error_code` | String | Yes | Specific error or rejection code if applicable. | `NONE` |
| `rule_source` | String | No | Source file and rule condition triggering event. | `short_straddle.py:shift_to_cost` |

---

## 4. Duplicate Prevention & Event Idempotency Mechanics

To prevent duplicate events, ghost trades, and race conditions during high-frequency tick processing:

1. **State Machine Transitions (Idempotency)**:
   - Every position maintains a strict state machine:
     `IDLE` ➔ `PENDING_ENTRY` ➔ `OPEN` ➔ `SL_PENDING` ➔ `CLOSED`.
   - Once a position enters `CLOSED`, no further events (SL changes, targets, EOD exits) can be emitted for that `trade_id`.
2. **Single Daily Entry Lock**:
   - Every strategy has an in-memory and persistent lock: `entered_today[strategy_id] = True`.
   - The lock is acquired atomically before the `ORDER_SUBMITTED` event is queued.
3. **Monotonic Sequence Counter**:
   - Each event receives a strictly incrementing integer `sequence_num` governed by an atomic counter.
   - Sinks (CSV logger, state store, dashboard) reject any incoming event with `sequence_num <= last_processed_sequence`.
4. **Asynchronous Threaded Queue with Flusher**:
   - All events are dispatched through a thread-safe `queue.Queue`.
   - A dedicated daemon thread consumes events and appends them to disk in batches, preventing file I/O latency from stalling the tick ingestion loop.

---

## 5. File Retention, Rotation, and Crash Safety

- **Storage Location**: `logs/audit/trade_event_audit_YYYY-MM-DD.csv`.
- **Daily Rotation**: New audit files are created automatically at 00:00:00 IST every day.
- **Atomic Writes**: State files (`logs/live_state.json`) use temporary files with retry wrappers to eliminate Windows file-locking collisions.

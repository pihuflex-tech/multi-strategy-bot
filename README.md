# NIFTY Multi-Strategy Quant Bot & Paper Trading Engine

> **Operating Mode**: `STRICT SIMULATED PAPER TRADING ONLY`  
> **Architecture**: v2.1.0-orchestrated (Modular Standard-Library Python)  
> **Live Broker Execution**: **PERMANENTLY DISABLED** — No broker endpoints, trading credentials, or real money transactions are supported.

---

## 1. Project Overview & Purpose

The **NIFTY Multi-Strategy Quant Bot** is an institutional-grade algorithmic quantitative trading and paper-trading engine developed specifically for the **NIFTY 50** Indian derivatives (F&O) market.

The system is designed to consume tick data from a decoupled ZeroMQ market feed server, evaluate 25 quantitative strategy modules in parallel, simulate paper orders with modeled execution slippage, calculate exact Indian statutory charges under Budget 2024 tax rules (STT, NSE turnover fees, SEBI charges, stamp duty, GST), track multi-leg option positions with orphan wing risk detection, and present real-time telemetry on an integrated, local web dashboard.

### Core Philosophy
- **Lightweight & Dependency-Free**: Built entirely with native Python standard libraries (`json`, `time`, `threading`, `math`, `os`, `pathlib`, `dataclasses`, `argparse`). Heavy quantitative C-dependencies (`pandas`, `numpy`, `scipy`) are strictly excluded to ensure lightning-fast startup and minimal resource consumption.
- **Cross-Platform Parity**: Runs smoothly on both **Windows 11 Workstations** and **Android Termux mobile environments**.
- **Decoupled Architecture**: Strictly an independent consumer of market data (`tcp://127.0.0.1:5555`). It never embeds broker authentication, live order routing, or market feed generation inside the bot process.
- **Safety by Default**: All 25 strategies are configured in `DISABLED` mode out of the box. The feed subscriber is offline by default (`feed_enabled: false`).

---

## 2. Current Implementation Status & Explicit Limitations

| Subsystem | Current Status | Notes & Limitations |
|---|---|---|
| **Core Architecture** | **Operational** (`v2.1.0`) | Unified single-command orchestrator in root `main.py`. |
| **Strategy Engine** | **Initialized (All Disabled)** | All 25 strategies registered in `src/strategies/`. All set to `DISABLED` mode by default. |
| **Position Management** | **Operational** | `MultiLegPositionManager` tracks legs, combined MTM, orphan risk, and atomic persistence. |
| **Statutory Tax Engine** | **Operational** | Models Budget 2024 STT (0.1% on options sell, 0.02% on futures sell), GST (18%), stamp duty, and turnover charges. |
| **Web Monitoring Dashboard** | **Operational** | Read-only Flask dashboard on `http://127.0.0.1:8080`. Fully decoupled telemetry provider. |
| **Market Feed Subscriber** | **Standby / Offline** | Client abstraction, parsers, and health monitors implemented. Live ZeroMQ transport connection is disabled by default (`feed_enabled: false`). |
| **Broker Order Placement** | **Excluded by Design** | Strict simulation only. No exchange or broker API connections exist or are authorized. |
| **Automated Test Suite** | **Verified (371/371 Passing)** | 100% deterministic offline regression tests. No network sockets or external dependencies. |

---

## 3. Architecture Overview & Data Flow

The bot operates as a unidirectional, event-driven paper execution engine:

```text
 ┌────────────────────────────────────────────────────────┐
 │        External NIFTY Feed Server (Port 5555)          │
 │  Emits ZeroMQ b"NIFTY_MARKET_TICK" Frames (Read-Only)  │
 └───────────────────────────┬────────────────────────────┘
                             │  tcp://127.0.0.1:5555 (Standby)
                             ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                       Root Orchestrator (main.py)                       │
 │  - Initializes single-instance shared subsystems                        │
 │  - Coordinates lifecycle, signal traps (SIGINT/SIGTERM), graceful exit  │
 └───────┬───────────────────┬───────────────────┬───────────────────┬─────┘
         │                   │                   │                   │
         ▼                   ▼                   ▼                   ▼
 ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
 │   Strategy    │   │  Multi-Leg    │   │   Statutory   │   │  Paper Broker │
 │   Registry    │   │   Position    │   │    Tax &      │   │  & Execution  │
 │  (25 Modules  │   │    Manager    │   │  Accounting   │   │  (Symmetric   │
 │   DISABLED)   │   │ (RLock State) │   │ (Budget 2024) │   │   Slippage)   │
 └───────┬───────┘   └───────┬───────┘   └───────────────┘   └───────────────┘
         │                   │
         └─────────┬─────────┘
                   │ Defensive Snapshots (under Lock)
                   ▼
 ┌────────────────────────────────────────────────────────┐
 │        ApplicationTelemetryProvider (main.py)          │
 │  - Sanitizes telemetry; rejects sensitive credentials   │
 │  - Replaces unavailable metrics with explicit nulls    │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │           Flask Web Dashboard (Port 8080)              │
 │  - GET /            -> Dark-Themed Single-Page UI      │
 │  - GET /api/health  -> Heartbeat & Safety Disclaimer    │
 │  - GET /api/state   -> Real-Time Sanitized Telemetry   │
 └────────────────────────────────────────────────────────┘
```

---

## 4. Repository Directory Structure

```text
multi-strategy-bot/
├── AGENTS.md                  # Operational rules, boundaries, and safety invariants
├── README.md                  # Canonical project documentation and operator runbook
├── requirements.txt           # Minimal dependencies (pyzmq, flask)
├── .env.example               # Environment variables configuration template
├── .gitignore                 # Git ignore rules for virtual environments, caches, logs
├── main.py                    # Unified single-command orchestrator & telemetry provider
│
├── config/                    # Modular JSON configurations
│   ├── app_config.json        # Application lifecycle & operational mode
│   ├── feed_config.json       # ZeroMQ subscriber endpoints & health parameters
│   ├── strategy_config.json   # 25-strategy definitions, parameters, and modes
│   ├── risk_config.json       # Portfolio loss limits & drawdown controls
│   ├── paper_trading_config.json # Capital allocation, tax rates, & slippage model
│   └── dashboard_config.json  # Web dashboard host & port settings
│
├── src/                       # Production source packages
│   ├── accounting/            # Statutory charges calculator, P&L math, trade logger
│   ├── calculations/          # Black-Scholes Greeks, IV, ATM rounding, basis spreads
│   ├── core/                  # Application lifecycle and shutdown manager
│   ├── dashboard/             # Flask dashboard backend, REST API, templates, static UI
│   ├── execution/             # Paper broker, order simulation, position manager
│   ├── feed/                  # Feed client abstraction, message parser, validator, health
│   ├── market/                # Market snapshot models, option chain, session tracker
│   ├── portfolio/             # Risk governor, exposure controls, capital allocator
│   ├── storage/               # Atomic state writer and persistent state manager
│   └── strategies/            # Strategy base class, registry, runner, 25 strategy modules
│
├── tests/                     # 278 deterministic offline unit & integration tests
├── logs/                      # Audit logs, trade CSV records, and state persistence
│   ├── live_state.json        # Atomic position snapshot state cache (Schema 2.1.0)
│   └── trades/                # 19-column auditable trade logs
└── archive/                   # Permanently retired legacy v1 files (for traceability)
```

---

## 5. Prerequisites & Installation (Windows 10 / 11)

### Requirements
- **Operating System**: Windows 10 or 11 (64-bit).
- **Python**: Python 3.10, 3.11, 3.12, 3.13, or 3.14 (standard distribution from [python.org](https://www.python.org/downloads/)).
- **Git**: Git for Windows (recommended for cloning).
- **Zero Heavy Compilers**: No MSVC build tools or C++ compilers required.

### Installation Steps
1. **Clone or Open the Repository**:
   ```powershell
   cd C:\Users\<YourUsername>\Desktop\multi-strategy-bot
   ```

2. **Create a Dedicated Virtual Environment**:
   ```powershell
   python -m venv venv
   ```

3. **Activate the Virtual Environment**:
   ```powershell
   .\venv\Scripts\activate
   ```

4. **Install Dependencies**:
   ```powershell
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
   *Note: Only `pyzmq` and `flask` will be installed.*

---

## 6. Starting the Application

The bot is started using the primary root entry point:

```powershell
python main.py
```

### What Happens on Startup
1. Subsystems are initialized (Charges Calculator, Position Manager, Risk Governor, Paper Broker, Strategy Registry, Strategy Runner).
2. Configuration is validated from `config/`.
3. The 25 independent strategy modules are loaded (all starting in `DISABLED` mode).
4. The read-only Flask web dashboard starts in a background thread on `http://127.0.0.1:8080`.
5. The terminal displays engine status and waits for user termination.

### Graceful Termination
To stop the bot, press **`Ctrl+C`** in the terminal:
- The orchestrator traps the `SIGINT` / `SIGTERM` signal.
- The latest in-memory position state is atomically flushed to `logs/live_state.json`.
- The Flask WSGI server is shut down cleanly without leaking sockets or worker threads.

---

## 7. Command-Line Options & Flags

Root `main.py` provides built-in CLI options for operational inspection and headless deployment:

```powershell
python main.py [OPTIONS]
```

| Flag | Argument | Default | Description |
|---|---|---|---|
| `-h`, `--help` | None | — | Show help message and exit. |
| `--no-dashboard` | Flag | `False` | Run the core engine in headless terminal mode without starting the Flask web server. |
| `--dashboard-host` | String | `127.0.0.1` | Host interface to bind web dashboard (loopback required for local security). |
| `--dashboard-port` | Integer | `8080` | Port number for web dashboard service. |
| `--inspect` | Flag | `False` | Print formatted inventory of all 25 strategies, clusters, and modes, then exit cleanly. |
| `--status` | Flag | `False` | Display current engine telemetry summary, strategy counts, and position metrics, then exit. |
| `--test-tick` | Flag | `False` | Run a synthetic market snapshot through the full pipeline to verify calculations, then exit. |

### Operational CLI Examples

```powershell
# Run with a custom dashboard port
python main.py --dashboard-port 8085

# Run in headless mode without a web dashboard
python main.py --no-dashboard

# Inspect registered strategies inventory
python main.py --inspect

# Check current telemetry status
python main.py --status

# Execute a deterministic pipeline test with a synthetic tick
python main.py --test-tick
```

---

## 8. Running the Offline Test Suite

The project includes an extensive suite of **371 deterministic offline regression tests**. Tests require zero network connections, open no listening sockets, and mock all persistence paths to isolated temporary directories.

To execute the entire test suite:

```powershell
python -m unittest discover tests -p "test_*.py" -v
```

### Focused Test Suites
You can run specific test categories individually:

```powershell
# Run multi-leg dashboard integration tests (F-07, F-09)
python -m unittest tests/test_dashboard_multileg.py -v

# Run atomic state persistence and concurrency tests (F-10)
python -m unittest tests/test_state_persistence.py -v

# Run graceful shutdown persistence tests (F-11)
python -m unittest tests/test_shutdown_persistence.py -v

# Run startup orchestration and CLI argument tests
python -m unittest tests/test_startup.py -v

# Run web dashboard API & UI tests
python -m unittest tests/test_dashboard.py -v

# Run position concurrency and RLock thread-safety tests
python -m unittest tests/test_position_concurrency.py -v
```

---

## 9. Configuration Files Explained

All operational behavior is controlled through modular JSON files in the `config/` directory:

| Configuration File | Governing Parameters | Safety Invariants |
|---|---|---|
| [`config/app_config.json`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/config/app_config.json) | Application name, logging levels, operational mode. | Mode must remain `"PAPER_TRADING_ONLY"`. |
| [`config/feed_config.json`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/config/feed_config.json) | ZeroMQ subscriber URL (`tcp://127.0.0.1:5555`), topic (`NIFTY_MARKET_TICK`), poll timeout (500ms). | `feed_enabled` defaults to `false`. |
| [`config/strategy_config.json`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/config/strategy_config.json) | Strike selection, default entry/exit times, stop loss, target percentages, lot sizes. | All 25 strategies default to `"mode": "DISABLED"`. |
| [`config/risk_config.json`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/config/risk_config.json) | Daily maximum loss limit, maximum position limits, drawdown circuit breakers. | Enforces portfolio-level kill switches. |
| [`config/paper_trading_config.json`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/config/paper_trading_config.json) | Initial virtual capital (₹1,000,000), modeled slippage ticks, statutory tax schedule. | Simulation parameters only. |
| [`config/dashboard_config.json`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/config/dashboard_config.json) | Web dashboard port, host binding, polling refresh rate (1500ms). | Defaults to loopback `127.0.0.1`. |

---

## 10. Inventory of All 25 Strategies

The bot houses 25 independent quantitative strategies spanning 9 market regimes. All strategies inherit from `StrategyBase` and are instantiated in `DISABLED` mode:

| Strategy ID | Strategy Name | Cluster | Primary Instrument | Description |
|---|---|---|---|---|
| `STRAT_001` | Long Call | `options_buying` | NIFTY CE | Directional call buying on bullish momentum breakouts. |
| `STRAT_002` | Long Put | `options_buying` | NIFTY PE | Directional put buying on bearish momentum breakdowns. |
| `STRAT_003` | Long Straddle | `straddle` | Multi-Leg (CE+PE) | Buys ATM CE + ATM PE expecting large volatility expansion. |
| `STRAT_004` | Long Strangle | `strangle` | Multi-Leg (CE+PE) | Buys OTM CE + OTM PE expecting sharp directional moves. |
| `STRAT_005` | Short Straddle | `straddle` | Multi-Leg (CE+PE) | Sells ATM CE + ATM PE to harvest intraday theta decay. |
| `STRAT_006` | Short Strangle | `strangle` | Multi-Leg (CE+PE) | Sells OTM CE + OTM PE targeting sideways range bound days. |
| `STRAT_007` | Iron Condor | `iron_condor` | Multi-Leg (4 Legs) | Defined-risk range trading with short strangle + long wings. |
| `STRAT_008` | Bull Call Spread | `spreads` | Multi-Leg (2 Legs) | Moderately bullish vertical spread buying lower strike & selling higher strike. |
| `STRAT_009` | Futures Trend Following | `futures` | NIFTY FUT | EMA/ADX directional trend follower on front-month futures. |
| `STRAT_010` | Futures + Protective Put | `hedging` | Synthetic Multi-Leg | Long futures hedged with protective OTM put option. |
| `STRAT_011` | Gap Fade | `gap` | Options / Futures | Mean reversion fading opening price gaps outside prior day range. |
| `STRAT_012` | Gap Continuation | `gap` | Options / Futures | Momentum breakout following large opening volume gaps. |
| `STRAT_013` | EMA Crossover | `trend` | Options / Futures | Fast/slow EMA crossover system with trend filter. |
| `STRAT_014` | OI-Based Strategy | `oi_flow` | Options | Option chain Open Interest build-up & unwinding signals. |
| `STRAT_015` | Adaptive Hedging | `hedging` | Multi-Leg | Dynamic delta-neutral hedging adjusting short leg exposures. |
| `STRAT_016` | Bear Put Spread | `spreads` | Multi-Leg (2 Legs) | Moderately bearish debit spread buying higher put & selling lower put. |
| `STRAT_017` | Bear Call Credit Spread | `spreads` | Multi-Leg (2 Legs) | Bearish credit spread collecting premium above resistance. |
| `STRAT_018` | Bull Put Credit Spread | `spreads` | Multi-Leg (2 Legs) | Bullish credit spread collecting premium below support. |
| `STRAT_019` | Opening Range Breakout | `volatility` | Options / Futures | Breakout trading on the high/low of initial market opening range. |
| `STRAT_020` | VWAP Reclaim/Loss | `trend` | Options / Futures | Intraday institutional benchmark mean reversion and trend continuation. |
| `STRAT_021` | RSI + Bollinger Band | `volatility` | Options | Mean reversion capitalizing on extreme statistical band deviations. |
| `STRAT_022` | Donchian Channel Breakout | `trend` | Futures | Long-term Turtle trading breakout channel on futures. |
| `STRAT_023` | Calendar Spread | `spreads` | Multi-Leg | Exploits term-structure implied volatility differentials across weekly/monthly expiries. |
| `STRAT_024` | Diagonal Spread | `spreads` | Multi-Leg | Combines horizontal time decay with vertical strike offsets. |
| `STRAT_025` | Supertrend Trend Following| `futures` | NIFTY FUT | Volatility-adjusted ATR trailing trend system on futures. |

---

## 11. Feed Status & Market Ingestion Pipeline

- **Default State**: Inactive (`feed_enabled: false`).
- **Transport Standard**: ZeroMQ subscriber socket (`ZMQ_SUB`) connecting to `tcp://127.0.0.1:5555` subscribing to `b"NIFTY_MARKET_TICK"`.
- **Implemented Pipeline Modules**:
  - [`MessageParser`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/src/feed/message_parser.py): Validates multi-part ZeroMQ frame headers and unpacks JSON tick payloads.
  - [`DataValidator`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/src/feed/data_validator.py): Rejects malformed prices, inverted option chains, negative quantities, or corrupted records.
  - [`DataNormalizer`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/src/feed/data_normalizer.py): Standardizes quotes into strongly-typed `MarketSnapshot`, `OptionQuote`, and `FuturesQuote` records.
  - [`FeedHealthTracker`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/src/feed/feed_health.py): Tracks sequence gaps, message throughput, and flags stale data if ticks stop arriving.
- **Phase 4 Integration**: Live background socket streaming and thread synchronization will be activated in Phase 4.

---

## 12. Dashboard Endpoints & Telemetry API

The integrated Flask web server provides read-only monitoring. All state mutation methods (`POST`, `PUT`, `DELETE`) return `405 Method Not Allowed`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Responsive dark-mode single-page monitoring dashboard. |
| `GET` | `/api/health` | Service health status (`{"status": "ONLINE", "feed_disclaimer": "..."}`). |
| `GET` | `/api/state` | Real-time sanitized telemetry payload. |

### Telemetry Payload Structure (`/api/state`)
The endpoint returns clean JSON where missing metrics map to `null` and non-finite floats (`NaN`, `Inf`) are sanitized:

```json
{
  "bot_status": "STANDBY",
  "app_lifecycle": "RUNNING",
  "strategy_engine": "ALL_DISABLED (0/25)",
  "paper_execution": "STANDBY",
  "feed_connection": "NOT_CONNECTED",
  "market_data_status": "NOT_AVAILABLE",
  "last_tick": {
    "timestamp": "NOT_AVAILABLE",
    "spot": null,
    "futures": null,
    "symbol": "NIFTY"
  },
  "strategies": [
    {
      "strategy_id": "STRAT_001",
      "name": "Long Call",
      "cluster": "options_buying",
      "mode": "DISABLED",
      "entries_today": 0
    }
  ],
  "open_positions": [],
  "portfolio": {
    "open_positions_count": 0,
    "open_legs_count": 0,
    "total_unrealized_mtm": 0.0,
    "total_realized_gross_pnl": 0.0,
    "total_charges": 0.0,
    "total_net_realized_pnl": 0.0,
    "net_combined_pnl": 0.0,
    "currency": "INR"
  },
  "errors": {
    "last_feed_error": "NONE",
    "last_system_error": "NONE"
  }
}
```

---

## 13. Multi-Leg Position Tracking & State Persistence

### Position Lifecycle
`MultiLegPositionManager` manages complex options combinations (Straddles, Iron Condors, Vertical Spreads):
- **Leg-Level Tracking**: Each leg tracks `symbol`, `strike`, `side`, `initial_quantity`, `remaining_quantity`, `entry_price`, `current_price`, and leg-level realized/unrealized P&L.
- **Composite Metrics**: Aggregates combined unrealized MTM, entry debit/credit, peak high-water mark, and total modeled taxes.
- **Orphan Wing Risk**: Flags an alert if protective long legs are closed while naked short option legs remain exposed.
- **Defensive Snapshots**: `get_open_position_snapshots()` constructs clean, decoupled dictionaries under an internal `threading.RLock`. Telemetry callers cannot mutate internal position objects.

### Atomic State Persistence (Fix F-10)
Positions are saved to `logs/live_state.json` (Schema `2.1.0`):
1. An in-memory snapshot is extracted under `self._lock`.
2. The payload is written to a unique temporary file (`logs/live_state.json.tmp.<UUID>`) outside the lock.
3. Disk buffers are flushed via `os.fsync()`.
4. The temporary file replaces the destination atomically via `os.replace`.
5. On Windows, transient `PermissionError` file-locking contention is automatically retried up to 5 times.
6. If an error occurs, only the writer's unique temporary file is deleted, ensuring concurrent writes never collide or corrupt state.

---

## 14. Permanent Paper-Trading Boundary

> [!CAUTION]
> **NO REAL TRADING AUTHORIZATION**  
> Under no circumstances does this repository execute live orders with real capital.

- **Simulation Only**: All execution is simulated in-memory and logged to `logs/live_state.json` and `logs/trades/`.
- **Zero Credentials**: Never store or input broker API keys, trading passwords, MPINs, or TOTP secrets in this codebase.
- **Slippage Modeling**: Orders fill against simulated market prices with realistic configurable tick slippage.

---

## 15. Security & Operational Best Practices

1. **Local Loopback Binding**: Always bind the dashboard to `127.0.0.1` (the default). If bound to `0.0.0.0`, the application prints a security warning because the dashboard has no authentication barrier.
2. **Environment Variables**: Use `.env` only for local overrides. Never commit `.env` to version control.
3. **Audit Hashes**: Historical CSV files (`data/` and `logs/`) are baseline protected:
   - `logs/live_state.json` SHA-256: `50c761757e7759ee1c5aada7bcdd9200859eca204683fddce1fddd0dc3338d14`
   - 156 Historical CSV files combined SHA-256: `979f03268164fe74350b44aa9a9ae4bedb52286917f43695fcae53aa6accf7af`

---

## 16. Troubleshooting Common Issues

### Issue 1: Port 8080 Already in Use
**Error**: `OSError: [WinError 10048] Only one usage of each socket address is normally permitted`  
**Solution**: Specify an alternative port:
```powershell
python main.py --dashboard-port 8085
```

### Issue 2: ModuleNotFoundError: No module named 'flask' or 'zmq'
**Error**: Python cannot locate installed packages.  
**Solution**: Ensure your virtual environment is active:
```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Issue 3: Feed Reports NOT_CONNECTED
**Observation**: Dashboard displays red `NOT_CONNECTED` badge.  
**Explanation**: This is expected default behavior. `config/feed_config.json` has `feed_enabled: false` to keep the bot completely offline until live feed testing is explicitly scheduled in Phase 4.

### Issue 4: Portfolio Metrics Display NOT AVAILABLE
**Observation**: Dashboard displays `NOT AVAILABLE` instead of ₹0.00.  
**Explanation**: When the position manager is uninitialized or in standby before any trades have occurred, metrics explicitly display `NOT AVAILABLE` to avoid falsely misleading operators with break-even ₹0.00 performance.

---

## 17. Android Termux Operations (Unverified Compatibility Notice)

The codebase is engineered strictly with POSIX-compliant standard library Python and zero C-dependencies, specifically to allow running on Android mobile nodes via Termux.

Detailed Termux installation and operations guides are provided in:
- [`TERMUX_SETUP.md`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/TERMUX_SETUP.md) — Package installation and environment setup.
- [`TERMUX_RUN.md`](file:///C:/Users/ASUS/Desktop/multi-strategy-bot/TERMUX_RUN.md) — Operational runbook, `tmux` background management, and wake-locks.

> [!NOTE]
> **Compatibility Status**: While all code is designed and syntax-tested for Linux/POSIX compliance, **live execution on physical Android Termux devices remains unverified in this workstation engineering phase**.

---

## 18. Current Known Limitations & Next Steps

1. **Feed Subscriber Transport (Phase 4)**: The live ZeroMQ consumer loop must be connected to the existing `FeedClient` protocol with non-blocking queue semantics.
2. **Dual Entry Points (Finding F-08)**: Legacy terminal runner `src/main.py` coexists alongside root orchestrator `main.py`. These will be unified into `src/core/application.py`.
3. **Strategy Activation**: All 25 strategies are currently disabled. Activation requires calibrated risk budgets and live paper dry-runs.
4. **Browser Testing**: Web UI is verified via unit tests and Flask client simulation; real browser E2E testing will follow.

---

## 19. Historical Documentation Notice

The previous Hindi document `READ.md` described the retired single-file v1 architecture (`strategy_engine.py`, 100 legacy strategies). All legacy v1 source files have been retired to `archive/legacy_v1/`. This canonical English `README.md` is now the single source of truth for the project.

# Android Termux Operations & Runbook

This guide covers running, monitoring, controlling, and troubleshooting the **NIFTY Multi-Strategy Quant Bot** within Android Termux.

---

## 1. Operating Modes & Prerequisites

The bot supports three strategy operating modes:
- `DISABLED` (Default): Strategy is dormant. No processing, no signals.
- `MONITOR_ONLY`: Evaluates live ticks, calculates indicators, records signals to `signals.jsonl`, but takes zero positions.
- `PAPER`: Simulates paper trades, tracks multi-leg options positions, calculates MTM P&L and Budget 2024 taxes, and logs executions. **Never submits broker orders.**

Before running:
1. Ensure Termux wake-lock is acquired:
   ```bash
   termux-wake-lock
   ```
2. Verify configuration in `config/strategy_config.json` or `.env`.

> [!NOTE]
> **Compatibility Status**: While all code is designed and syntax-tested for Linux/POSIX compliance with lightweight native standard libraries, **live execution on physical Android Termux devices remains unverified in this workstation engineering phase**.

---

## 2. Network Configuration for External Feed (Standby / Offline)

The bot consumes live market ticks via a decoupled ZeroMQ subscriber from `nifty-feed-server`:
- By default, the feed connection is **offline/disabled** (`feed_enabled: false` in `config/feed_config.json`), and live ZeroMQ transport integration is deferred to Phase 4.
- When configured for future live paper trading, `FEED_ZMQ_URL` targets `tcp://127.0.0.1:5555` (or LAN IP such as `tcp://192.168.1.100:5555`).
- If running Termux on a mobile device on the same local Wi-Fi as your Windows feed server:
  ```bash
  export FEED_ZMQ_URL="tcp://192.168.1.100:5555"
  ```
  or configure in `.env`:
  ```ini
  FEED_ZMQ_URL=tcp://192.168.1.100:5555
  FEED_TOPIC=NIFTY_MARKET_TICK
  ```

> [!NOTE]
> **Safe Disconnected State**: The feed subscriber starts offline by default. If the network feed is unreachable or disabled, the subscriber waits gracefully with non-blocking timeouts (`FEED_TIMEOUT_MS=500`). The bot does NOT crash, fabricate prices, or place unauthorized orders.

---

## 3. Running the Bot

### Method A: Foreground Interactive (Recommended for Initial Testing)

Run in headless mode directly from the repository root:
```bash
python main.py --no-dashboard
```
*(To run with the optional local mobile web dashboard, omit `--no-dashboard`: `python main.py`)*

To stop gracefully: Press `Ctrl+C`. The bot catches `SIGINT`, flushes all open file handles, persists position state, and shuts down cleanly.

### Method B: Background Service using `tmux` (Recommended for Trading Sessions)

Using `tmux` ensures your session survives accidental app minimization or terminal disconnections:

1. Install `tmux`:
   ```bash
   pkg install -y tmux
   ```
2. Start a new session:
   ```bash
   tmux new -s quantbot
   ```
3. Inside the tmux session, start the bot:
   ```bash
   python main.py --no-dashboard
   ```
4. Detach from tmux session: Press `Ctrl+B`, then release and press `D`.
5. Reattach anytime:
   ```bash
   tmux attach -t quantbot
   ```

### Method C: Background Service using `nohup`

```bash
nohup python main.py --no-dashboard > logs/bot_stdout.log 2>&1 &
echo $! > runtime/bot.pid
```

To stop a background process:
```bash
kill -SIGINT $(cat runtime/bot.pid)
```

---

## 4. Monitoring & Telemetry

### Inspect Strategy Status
```bash
python main.py --inspect
```
Displays registered strategies, enabled modes, lot sizes, and SL policies.

### Check Telemetry Status
```bash
python main.py --status
```
Displays current engine lifecycle, strategy counts, open positions, and offline feed status.

### Run Synthetic Offline Pipeline
```bash
python main.py --test-tick
```
Simulates a full cycle of calculations, risk checks, and state serialization without network dependency.

### Tail Live Strategy Trades & Events
Every strategy has its own isolated log directory under `logs/strategies/STRAT_XXX/`:

- **Signals**:
  ```bash
  tail -f logs/strategies/STRAT_001/signals.jsonl
  ```
- **Trades Ledger**:
  ```bash
  tail -f logs/strategies/STRAT_001/trades.csv
  ```
- **Individual Leg Events**:
  ```bash
  tail -f logs/strategies/STRAT_001/legs.csv
  ```
- **Execution & Stop Events**:
  ```bash
  tail -f logs/strategies/STRAT_001/events.jsonl
  ```

### Live Aggregate State
Check the atomic portfolio snapshot at:
```bash
cat logs/live_state.json | grep -A 10 '"portfolio"'
```
*(Or inspect the complete formatted state via `python -m json.tool logs/live_state.json`)*

---

## 5. State Recovery After Restart

If Termux is killed by Android or closed:
1. `logs/live_state.json` preserves the last known state.
2. Individual strategy event logs (`events.jsonl`, `trades.csv`, `legs.csv`) are append-only and immutable.
3. Upon restart, the `MultiLegPositionManager` loads existing open positions from `logs/live_state.json` without duplicating trades or reopening closed positions.

---

## 6. Troubleshooting & Diagnostics

| Issue | Cause | Resolution |
|---|---|---|
| `UnicodeEncodeError` | Console encoding issue on Android | System uses clean ASCII CLI output (`[+]`, `[-]`, `INR`). Ensure `LANG=en_US.UTF-8` in Termux. |
| Process killed when screen locks | Battery optimization active | Run `termux-wake-lock` and set Termux battery settings to **Unrestricted**. |
| `zmq.error.ZMQError: Address already in use` | Previous process still running | Run `pkill -f "python main.py"` and restart. |
| Cannot reach Windows feed | Firewall blocking port 5555 | Open inbound port 5555 in Windows Defender Firewall on the host PC. |
| Read-only file system error | Permission issue | Run `termux-setup-storage` and ensure working in `~/multi-strategy-bot`. |

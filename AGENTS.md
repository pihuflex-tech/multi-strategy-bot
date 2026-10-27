# Multi-Strategy Bot — Workspace Guidelines and Operational Rules

Welcome to the **NIFTY Multi-Strategy Quant Bot** workspace. All AI agents and contributors operating within this repository must adhere strictly to these project-specific engineering rules.

---

## 1. Architectural Integrity and Code Inspection
- **Inspect Before Modifying**: Always thoroughly inspect existing source code, configuration files, and directory layouts before proposing or applying changes.
- **Preserve Existing Architecture**: Respect the existing decoupled architecture. This application is strictly an independent consumer of market data via ZeroMQ (`tcp://127.0.0.1:5555`, topic `b"NIFTY_MARKET_TICK"`).
- **Feed Isolation**: Do not embed, rewrite, or merge a separate market feed server or broker authentication pipeline into this bot repository.
- **Protect Existing Work**: Do not overwrite or modify active configuration (`config/strategy_config.json`, `config/app_config.json`) or data without explicit user alignment and technical justification. Legacy configuration (`strategies_config.json`) has been permanently retired to `archive/legacy_v1/`.

---

## 2. Permanent Paper-Trading Boundary
- **Simulation Only**: All execution activity must remain strictly simulated (paper trading) within in-memory position trackers (`self.positions`), telemetry state (`logs/live_state.json`), and trade audit logs (`logs/trades/`).
- **No Broker Order Execution**: Under no circumstances should live broker order execution, exchange trading endpoints, or API order submission routines be introduced, armed, or enabled.
- **Zero Live Credentials**: Never store or solicit live trading API keys, trading PINs, or TOTP secrets within this repository.

---

## 3. Lightweight Python & Cross-Platform Discipline
- **Minimal Dependencies**: Keep dependencies minimal and native. Rely on Python's standard library (`json`, `time`, `queue`, `threading`, `datetime`, `math`, `os`, `pathlib`).
- **No Heavy Quantitative Frameworks**: Do not introduce heavy C-dependencies (`pandas`, `numpy`, `scipy`) unless explicitly authorized. The core engine must run smoothly in resource-constrained environments.
- **Dual-Platform Parity**: Maintain full compatibility across:
  - **Local Workstation Development**: Windows 11 (handle path separators and file-locking mechanics cleanly).
  - **Mobile / Edge Node Deployment**: Android Termux (ensure POSIX compliance, single-core CPU efficiency, and minimal battery overhead).
- **Cross-Platform Paths**: Always use `os.path.join` or `pathlib.Path` instead of hardcoding platform-specific paths (such as `/tmp/`).

---

## 4. Deterministic Offline Testing & Empirical Evidence
- **Offline Test Requirement**: Any logic change must be validated using deterministic, offline regression tests with static snapshot fixtures.
- **No Live Data Dependency in Tests**: Automated tests must never depend on a live market feed, network sockets, or external services.
- **Evidence-Based Reporting**: Never claim a test passed, a bug was fixed, or live compatibility was achieved without empirical proof. Report exact command invocations, outputs, and status classifications:
  - `PASSED`: Verified directly with executed command output.
  - `FAILED`: Executed and failed with error/stack trace.
  - `SKIPPED`: Inapplicable to the current scope.
  - `UNTESTED`: Applicable but unexecuted due to environmental limits.

---

## 5. Transparent Accounting & Change Discipline
- **Scope Discipline**: Restrict modifications strictly to the explicitly requested task.
- **Closing Accounting**: Conclude every task with an accurate summary of:
  - Exact files created, modified, or inspected.
  - Empirical verification results.
  - Unverified assumptions and technical limitations.
  - Pending open questions or required follow-ups.

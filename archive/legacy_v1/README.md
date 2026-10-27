# Legacy Architecture (v1.0) — Retired Artifacts Archive

> [!WARNING]
> **RETIRED HISTORICAL ARTIFACTS — DO NOT EXECUTE OR IMPORT**
> The files in this directory belong to the legacy single-file quantitative model (v1.0).
> They have been permanently retired as part of the deliberate architectural reset.
> Do NOT import, execute, or re-introduce these files into the active `src/` modular runtime.

---

## 📅 Archival Metadata
- **Archival Date**: 2026-10-25
- **Reason for Retirement**: Complete architectural reset following the Phase 2A quantitative strategy audit.
- **Architectural Shift**: Replaced by the Phase 1 clean modular architecture (`src/` packages, `config/` schemas, and dedicated offline test suites).

---

## 🗄️ Archived Files
The following legacy files were preserved for historical traceability and forensic audit prior to their removal from the active root:

1. `strategy_engine.py` (17,533 bytes)
   - *Legacy Role*: Monolithic multi-strategy runner, ZeroMQ subscriber, tax calculator, and trade logger.
   - *Audit Status*: Contained severe market entry guard inversions (`cur_time < strat["entry_time"]`), inverted STT/Stamp Duty tax calculations, and 70/100 missing or defective strategy implementations.
2. `generate_strategies.py` (7,068 bytes)
   - *Legacy Role*: Script used to generate synthetic configurations for 100 strategies.
   - *Audit Status*: Populated unused parameters (such as `min_gamma`) that the legacy engine ignored.
3. `strategies_config.json` (31,352 bytes)
   - *Legacy Role*: Flat JSON dictionary containing configuration blocks for 100 legacy strategies (`STRAT_001` through `STRAT_100`).
   - *Note*: Distinct from active configuration `config/strategy_config.json`.
4. `dashboard.py` (7,939 bytes)
   - *Legacy Role*: Standalone Flask web UI.
   - *Audit Status*: Hardcoded to read legacy `/tmp/market_state.json` and legacy HTTP endpoints; replaced by `src/dashboard/` in the modular architecture.
5. `archive_manifest.json`
   - Contains exact cryptographic SHA-256 hashes and byte lengths at time of archival.

---

## 🛡️ Preservation Boundary
Historical trade records in `logs/trades/` and runtime snapshots in `logs/live_state.json` remain preserved in their respective directories as auditable historical telemetry.

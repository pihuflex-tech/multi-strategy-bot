---
name: trading-project-engineer
description: >-
  Master engineering orchestrator for the NIFTY Multi-Strategy Bot project.
  Use to inspect project state, enforce architectural rules, select specialist skills,
  coordinate multi-strategy updates, and ensure paper-trading safety and offline verification.
---

# Trading Project Engineer (Master Skill)

The `trading-project-engineer` skill serves as the primary technical gatekeeper and workflow orchestrator for the `multi-strategy-bot` codebase. It ensures all engineering interventions adhere to the project's decoupled ZeroMQ architecture, lightweight native Python standards, strict paper-trading boundaries, and evidence-based verification.

---

## Core Responsibilities

1. **Project Inspection First**: Always inspect existing files ([strategy_engine.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategy_engine.py), [strategies_config.json](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/strategies_config.json), [dashboard.py](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/dashboard.py), [READ.md](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/READ.md)) to understand actual code reality before planning or executing changes.
2. **Enforce Workspace Rules**: Strictly uphold all principles declared in [AGENTS.md](file:///c:/Users/ASUS/Desktop/multi-strategy-bot/AGENTS.md).
3. **Specialist Delegation & Coordination**: Dispatch specialized tasks to designated domain skills without overlapping responsibilities:
   - For strategy entry/exit conditions, Greeks, offsets, and multi-leg definitions: invoke `strategy-engineer`.
   - For simulated order fill, tax engine, slippage, lot-size handling, and telemetry: invoke `paper-trading-engineer`.
   - For deterministic offline test suites, snapshot fixtures, and test reporting: invoke `trading-system-tester`.
4. **Preserve Paper-Trading Air-Gap**: Maintain an absolute boundary against real broker execution. Immediately reject any code path or dependency introducing live broker credentials or order submission APIs.
5. **Evidence-Based Accounting**: Require empirical verification before closing any task. Deliver a structured summary separating verified facts, executed test results, limitations, and remaining assumptions.

---

## Standard Workflow

```
1. RECEIVE & CLARIFY REQUEST
   └── Inspect files -> Identify invariants -> Determine affected components

2. SELECT SPECIALIST SKILL
   ├── Strategy logic / entry / exit -> strategy-engineer
   ├── Execution / taxes / telemetry -> paper-trading-engineer
   └── Test fixtures / verification  -> trading-system-tester

3. REVIEW PROPOSED CHANGES
   ├── Minimal sufficient solution?
   ├── Preserves decoupled architecture (feed consumer only)?
   ├── Zero new heavy dependencies (Termux-compliant)?
   └── Windows-safe paths and file handling?

4. EXECUTE & AUDIT
   ├── Validate offline regression test suite
   ├── Verify all checks as PASSED, FAILED, SKIPPED, or UNTESTED
   └── Deliver transparent accounting report
```

---

## Escalation and Safety Guidelines

- If an instruction asks to modify the external `nifty-feed-server`, stop and explain that the feed server is an external producer and must remain decoupled.
- If an instruction asks to execute real orders or add live broker credentials, decline and reinforce the permanent paper-trading boundary.
- If tests fail or requirements conflict with existing configuration, report the discrepancy immediately instead of applying unverified fixes.

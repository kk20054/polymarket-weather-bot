# WeatherBot Agent Rules

This is the short operating guide for Codex and other coding agents. Read detailed rules from `docs/AGENTS_DETAIL_CN.md` only when the current task needs them.

## Mission

```text
real data -> leakage-free probabilities -> realistic paper execution
-> production dashboard -> 14-30 day validation -> small live canary
```

WeatherBot is usable for observation and controlled simulation. Do not claim stable profitability or unlock unattended live trading until validation gates prove an edge.

## Three Default Documents

- `AGENTS.md`: agent workflow, safety boundaries, and standard commands.
- `docs/CURRENT_STATE.md`: the only default turn-start context; keep it under 40 lines.
- `README.md`: installation, startup, operator workflow, strategy meaning, and limitations.

Indexed references, not default context:

- `docs/IMPLEMENTATION_LOGIC_CN.md`: stable layers and data flow.
- `docs/DATA_STORAGE_CN.md`: logical and physical data paths.
- `docs/AGENTS_DETAIL_CN.md`: PolyWX, UI, data, algorithm, naming, and Git details.
- `docs/SOURCE_REGISTER.csv`: reusable external evidence and refresh rules.
- Git history: the project ledger and release history.
- `audits/`: ignored temporary evidence; never scan it by default.

## Turn Start Protocol

1. Read only `docs/CURRENT_STATE.md`.
2. Run `git status --short --branch`.
3. Confirm the named layer, scope, and blockers; read only the relevant indexed reference.
4. Reuse `docs/SOURCE_REGISTER.csv` and existing evidence before browsing.
5. Do not recursively scan `data/`, `audits/`, `backups/`, `.venv/`, generated output, or archives.

For substantial work, establish this compact task contract in the plan or commentary, not another Markdown file:

```text
goal | deliverable | in scope | out of scope | authoritative input | checks | commit
```

## Turn End Protocol

1. Run `scripts/check.ps1` with the smallest sufficient scope.
2. Update `docs/CURRENT_STATE.md` only when durable facts, blockers, or the single next task changed.
3. Do not create per-turn ledgers, recap documents, or audit indexes unless explicitly requested.
4. Keep temporary evidence under ignored `audits/`; keep formal history in Git.
5. Report changed files, checks, usability, and remaining blocker.

## Build Order

- Layer 0: reference corpus and schema evidence.
- Layer 1: station registry and settlement contracts.
- Layer 2: METAR and supplementary observations.
- Layer 3: forecast runs and ensemble members.
- Layer 4: hourly consensus and daily-max distributions.
- Layer 5: strictly matched market buckets and executable quotes.
- Layer 6: signal decisions, evidence, and risk gates.
- Layer 7: persisted-data dashboard.
- Layer 8: paper execution and risk controls.
- Layer 9: 14-30 day validation and replay.
- Layer 10: live canary only after gates pass.

One turn should touch at most one layer plus its direct consumer. For Layers 0-6, plan before code unless the user already supplied an approved implementation plan.

## Canonical Code Paths

- `weatherbot_v3/`: production business modules.
- `dashboard_server.py`: FastAPI and adapter layer only.
- `frontend/src/`: the only editable production frontend.
- `dashboard/`: compatibility static UI; do not add features here.
- `legacy/`: read-only history.
- `sites-dashboard/`: ignored experiment, never a production dependency.
- `tests/`: regression and contract tests.
- `scripts/`: the only supported operator/developer entry commands.

Runtime data stays behind the project `data/` Junction to `D:\WeatherBot\data`. Never replace it with scattered absolute paths or delete its physical target.

## Safety Red Lines

- Backend startup stays lightweight; no automatic legacy scan or simulation resume.
- Do not start `weatherbet.py`.
- `LIVE_TRADING=false` remains the default.
- The first possible live action is BUY YES limit-only canary with idempotency, balance, tick, minimum-size, freshness, duplicate, spread, depth, and daily-limit checks.
- Do not commit `audits/`, `data/`, `.env`, `config.json`, `.venv/`, generated builds, dependencies, backups, or secrets.
- Do not weaken data, calibration, liquidity, or risk gates merely to create more trades.

## Standard Commands

```powershell
# Start backend, frontend, scheduler, and browser through the canonical launcher.
.\scripts\dev.ps1

# Default code-and-UI gate. Other scopes: docs, backend, frontend, full.
.\scripts\check.ps1
.\scripts\check.ps1 -Scope backend
```

Use Git commits/tags as the only code release history. Do not create copied “latest/final/v2” source trees. A build or local launcher is generated from the tracked mainline and is not a second editable version.

# WeatherBot Agent Rules

This file is the short operating guide for Codex and any other coding agent working on WeatherBot. Detailed PolyWX, dashboard, data, algorithm, naming, and workflow rules live in `docs/AGENTS_DETAIL_CN.md`.

## Mission

WeatherBot is a production-oriented Polymarket weather trading platform:

```text
real data foundation -> leakage-free probability model -> realistic paper execution -> production dashboard -> 14-30 day validation -> small live canary
```

Do not claim stable profitability until paper-trading and validation gates prove it. Current status remains usable for observation and controlled simulation, not unattended live trading.

## Progress Files

- Start every turn from `docs/CURRENT_STATE.md`.
- Active ledger: `PROJECT_PROGRESS_CN.md`.
- Older history: `docs/PROGRESS_ARCHIVE_CN.md`.
- Detailed project rules: `docs/AGENTS_DETAIL_CN.md`.
- Local evidence under `audits/` is not committed and is not a default Turn Start dependency.

## Turn Start Protocol

1. Read only `docs/CURRENT_STATE.md`; do not read archives unless the task explicitly involves historical decisions.
2. Run `git status --short --branch` before editing.
3. If the task names a layer, confirm the current layer and blockers from `docs/CURRENT_STATE.md`.
4. Reuse existing evidence; do not run Firecrawl unless the user explicitly asks or current evidence is insufficient for the named task.
5. If the dashboard appears stuck, check `/api/dashboard` latency and live runtime fields before deeper browser debugging.

## Turn End Protocol

1. Append a concise ledger entry to `PROJECT_PROGRESS_CN.md` for every non-trivial turn.
2. Update `docs/CURRENT_STATE.md` so the next turn can start without reading long archives.
3. Record changed files, checks run, usability conclusion, blockers, and next step.
4. If Firecrawl was used, record ids, URLs, coverage, failures, and whether the result changed architecture or code.
5. If a commit is made, record the final commit hash.

## Build Order

Build from the data floor upward; one turn should touch at most one layer plus its immediate consumer.

- Layer 0: PolyWX reference corpus and schema evidence under local `audits/`.
- Layer 1: `stations` registry with ICAO/WMO ids, timezone, and settlement rule text.
- Layer 2: `metar_reports` and `mesonet_observations` ingestion with parser tests.
- Layer 3: `forecast_runs` and `forecast_members` for raw and archived model inputs.
- Layer 4: `hourly_consensus` plus DEB daily max `(mu, sigma)` production.
- Layer 5: `market_buckets` with strict matching, token, quote, tick, and order size metadata.
- Layer 6: `signal_decisions` with model distribution, market edge, gates, and evidence links.
- Layer 7: PolyWX-shaped dashboard reading only persisted layer data.
- Layer 8: Paper execution and risk gates.
- Layer 9: 14-30 day validation and replay quality checks.
- Layer 10: Live canary only after validation gates pass.

## Detail Index

See `docs/AGENTS_DETAIL_CN.md` for PolyWX Reference Workflow, Reference Fusion Architecture, Dashboard Rules, Theme Contract, Data And Algorithm Rules, directory boundaries, naming, and Git discipline.

## Execution Safety Red Lines

- Backend startup must be lightweight: no automatic weather fetch, no automatic simulation resume, and no legacy infinite scan by default.
- Do not start `weatherbet.py` legacy loops unless the user explicitly asks.
- `LIVE_TRADING=false` remains the default; live behavior stays behind dry-run checks, risk gates, and canary sizing.
- First allowed live behavior is BUY YES limit-only canary with strict idempotency, balance, tick size, `orderMinSize`, stale-book, duplicate-order, spread, and daily-limit checks.
- Do not commit `audits/`, `data/`, `.env`, `config.json`, `.venv/`, `frontend/dist/`, `node_modules/`, or secrets.

## Required Checks

For backend/core work:

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m unittest tests.test_v3_core
.\.venv\Scripts\python.exe -m weatherbot_v3.cli project-verify --verification-mode observation
```

`project-verify` is read-only and exits `2` when the requested readiness stage is blocked. Use `--deep-verification` only for deliberate full SQLite integrity audits; the default quick scope is the per-turn gate.

For PolyWX/dashboard contract work:

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m unittest tests.test_polywx_contract tests.test_v3_core
```

For frontend work:

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run build
```

For docs-only work:

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
git diff --check
git status --short --branch
```

Runtime dashboard smoke check when relevant:

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
Measure-Command { Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/dashboard' | Out-Null } | Select-Object TotalMilliseconds
$d=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/dashboard'
[pscustomobject]@{
  scanner_status=$d.stats.scanner_status
  is_running=$d.stats.is_running
  auto_simulation_enabled=$d.auto_simulation.enabled
  production_running=$d.production_refresh.running
  auto_refresh_running=$d.production_refresh.auto_refresh_running
} | ConvertTo-Json
```

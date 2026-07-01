# WeatherBot Agent Rules

This file is the project-level operating guide for Codex and any other coding agent working on WeatherBot.

## Mission

WeatherBot is a production-oriented Polymarket weather trading platform. The goal is:

```text
real data foundation -> leakage-free probability model -> realistic paper execution -> production dashboard -> 14-30 day validation -> small live canary
```

Do not claim or imply that the bot can reliably make money until paper-trading and validation gates prove it. Current status is still Phase 1.5 to Phase 2 transition: usable for observation and controlled simulation, not for unattended live trading.

## Where Progress Lives

- `PROJECT_PROGRESS_CN.md` is the authoritative project ledger. Every non-trivial development, research, Firecrawl, UI, data, algorithm, test, deployment, or debugging turn must append an entry before the final response.
- `audits/polywx-firecrawl-<YYYY-MM-DD>/MANIFEST.json` is the authoritative PolyWX corpus index. Never infer corpus quality from directory names alone.
- `audits/` contains local research evidence only. It can explain decisions, but it must not be committed.
- Final answers after non-trivial work must say: current usable status, what changed, verification run, blockers, next step, and where the record was written.
- When the user asks "where are we", "can I use it", "does backtest have value", or "is data guaranteed", answer from `PROJECT_PROGRESS_CN.md` plus current runtime evidence, not from memory or chat summaries.

## Turn Start Protocol

Before editing anything in this repository:

1. Read `PROJECT_PROGRESS_CN.md` enough to know the current phase, latest completed layer, last verification, and blockers.
2. Check git state with `git status --short --branch`.
3. If the task involves PolyWX, Firecrawl, dashboard schema, or visual matching, open the latest `audits/polywx-firecrawl-*/MANIFEST.json` and `SCHEMA_MAP_CN.md` first.
4. If a fresh crawl is not required by a new question, reuse existing evidence instead of repeating Firecrawl.
5. If the dashboard appears stuck, first check `/api/dashboard` latency and live runtime fields before deeper browser debugging.

## Turn End Protocol

At the end of every non-trivial turn:

1. Append a new entry to `PROJECT_PROGRESS_CN.md`.
2. Include the Build Order layer, exact changed files, verification output, current usability conclusion, remaining blockers, and next step.
3. If Firecrawl was used, record job ids or scrape ids, URLs, what succeeded, what failed, and whether the result changed architecture or code.
4. If no code was changed, say so explicitly and run only the checks relevant to the documentation or research change.
5. If a commit is made, update the ledger with the final commit hash.
6. Do not let the only record of work live in the chat transcript, context summary, or `audits/`.

## Build Order

WeatherBot must be built from the data floor upward. Do not work on a higher layer until the lower layer has persisted rows, a regression test, and a `PROJECT_PROGRESS_CN.md` ledger entry.

- Layer 0: PolyWX reference corpus using Firecrawl. Store under `audits/polywx-firecrawl-<YYYY-MM-DD>/` with `MANIFEST.json`, rendered DOM snapshots, XHR responses, screenshots, and `SCHEMA_MAP_CN.md`.
- Layer 1: `stations` table for target cities, including ICAO/WMO ids, timezone, and settlement rule text.
- Layer 2: `metar_reports` and `mesonet_observations` ingestion with parser unit tests.
- Layer 3: `forecast_runs` and `forecast_members` for ECMWF, GFS, HRRR, Open-Meteo, DEB, and related inputs.
- Layer 4: `hourly_consensus` view feeding charts and signal engine.
- Layer 5: `market_buckets` with strict bucket matching, tick size, `orderMinSize`, `negRisk`, token id, and orderbook metadata.
- Layer 6: `signal_decisions` with distribution, model-market edge, execution gate, evidence links, AI review, and paper/live decision.
- Layer 7: PolyWX-shaped dashboard. It reads only from Layers 1-6 and must not create visual-only mock data paths.
- Layer 8: Paper execution and risk gates.
- Layer 9: 14-30 day validation.
- Layer 10: Live canary.

One turn should not modify more than one Build Order layer plus its immediate consumer. Split larger work and record each step in the ledger.

## Product Direction

- Learn PolyWX's information architecture: city-first dashboard, city index, recommendation focus, refresh time, date switching, forecast, METAR, historical observations, bias statistics, fetch logs, hourly rows, and clear empty states.
- Do not copy PolyWX branding, membership flows, voting widgets, non-trading prompts, or visual identity.
- Preserve WeatherBot's own edge: Polymarket event links, YES token data, real orderbooks, paper/live gates, truth versions, simulated fills, live dry-run checks, and risk controls.
- Use `suislanchez/polymarket-kalshi-weather-bot` only as a reference for FastAPI + SQLite + React dashboard structure and simulation ergonomics. Do not reintroduce BTC modules, Kalshi-first assumptions, or simple `edge > 8%` auto-trading logic.
- Treat `alteregoeth-ai/weatherbot` as the airport-station and multi-source weather baseline, not as a production trading architecture.

## PolyWX Reference Workflow

Before any dashboard, schema, or "align with PolyWX" task, verify a fresh PolyWX reference corpus exists. If it does not, produce it with Firecrawl in this order:

1. `firecrawl_map` on `https://polywx.xyz` to enumerate reachable routes and query-parameter permutations. Save raw output.
2. `firecrawl_search` for module-level keywords: `Forecast`, `METAR`, `Historical`, `Diff Stats`, `Fetch Log`, `Hourly Temperature`, `Daily Max Prediction`, `Probability buckets`.
3. `firecrawl_scrape` with JavaScript rendering for each of the five tabs and the hourly chart. Capture rendered DOM, visible network calls, and inline JSON when available.
4. For at least 3 cities x 3 dates, scrape city/date URLs and store outputs under `audits/polywx-firecrawl-<YYYY-MM-DD>/<city>/<date>/`.
5. Write `MANIFEST.json` with crawl start/end time, Firecrawl ids, sha256 file list, discovered API endpoints, JS-rendered/static status, unresolved gaps, and per-tab coverage counts.
6. Write `SCHEMA_MAP_CN.md` mapping each PolyWX field to a WeatherBot SQLite column and dashboard component.

Corpus validity requires `MANIFEST.json` less than 14 days old, all five tabs plus hourly chart, and at least representative XHR response body evidence. A static markdown-only scrape is not valid because PolyWX is a JS-rendered SPA.

Never say PolyWX has been "fully crawled" unless the manifest proves it. Current accepted wording for partial evidence is "rendered evidence corpus" or "representative XHR corpus", not "full source clone".

## Reference Fusion Architecture

Use external repositories as design inputs, not code to copy blindly. Every borrowed idea must be mapped into WeatherBot's data, audit, paper-trading, and risk-control model and pinned to a Build Order layer.

- `punkpeye/awesome-mcp-servers`: discovery index for research and data-acquisition tools. MCPs are supporting adapters, not core trading dependencies. Firecrawl is allowed for PolyWX/GitHub/source research; production decisions must still use typed collectors and persisted database rows. Layer 0 only.
- `python-metar/python-metar`: METAR/SPECI decoding model. Store raw report plus decoded temperature, dew point, wind, gust, visibility, cloud layers, altimeter/pressure, precipitation, sea-level pressure, peak wind, station id, report time, source URL, parser version, and parse warnings. Layer 2.
- `Polymarket/*`: official market and CLOB references define execution boundaries. Keep order creation behind a `PolymarketExecutor` adapter with token id, tick size, `negRisk`, `orderMinSize`, best bid/ask, book timestamp, allowance/balance state, idempotency key, and exact API response. Layer 5 boundary plus Layer 8 execution.
- `yangyuan-zhen/PolyWeather`: borrow the city terminal shape, observation-driven chart updates, aviation METAR/TAF, nearby official network layers, DEB/hourly consensus, full bucket distribution, strict market-bucket matching, SSE/event replay, health endpoints, and public/private trading boundary. Layers 4-7.
- PolyWX corpus: dashboard visual evidence and signal evidence surface. Layers 6-7.

## Target Data Foundation

- `stations`: city key, display name, ICAO/WMO/provider station ids, timezone, settlement rule text, primary settlement source, nearby observation networks, and confidence.
- `metar_reports`: raw METAR/SPECI text, decoded fields, parser version, report time, station id, source URL, fetch time, parse status, and parse warnings.
- `mesonet_observations`: non-METAR official/local networks such as JMA AMeDAS, HKO, CWA, AMOS, NWS/NOAA, airport runway sensors, and other rule-relevant station feeds. Label as observation evidence, not settlement truth by default.
- `forecast_runs` and `forecast_members`: ECMWF/GFS/HRRR/Open-Meteo/DEB inputs with run time, valid time, horizon, member values, and source quality.
- `hourly_consensus`: one city/date/hour path for chart and signal engine, separating observations, forecast consensus, timing markers, cloud/humidity, and residuals.
- `market_buckets`: all Polymarket outcomes for a city/date event, with exact/range/or-higher/or-lower direction, token id, quote, tick size, `orderMinSize`, and strict matching status.
- `signal_decisions`: distribution, model-market edge, execution gate, AI review, paper/live decision, skip reason, and source evidence links.

## Dashboard Generation Rules

- Generate the dashboard from the same city/date evidence payload that powers signals.
- Each city page should show one primary hourly chart, one probability/market bucket module, then five tabbed evidence tables: Forecast, METAR, Historical, Diff Stats, Fetch Log.
- Observation lines are solid; model/DEB forecasts are dashed; residuals are red/blue bars; humidity/cloud can be secondary bars only when real data exists.
- The METAR tab must show raw report, decoded temperature/dew point/wind/cloud/pressure/precipitation, report age, parser warnings, and station-local time.
- The Historical tab must distinguish settlement truth, METAR history, official nearby-network history, and Open-Meteo fallback. Fallback rows must not unlock live gates.
- The Diff Stats tab must compute observed minus forecast, MAE/bias/Pearson R, overlap count, source coverage, and whether the sample is independent by settlement day.
- The Fetch Log tab must use structured backend rows: source, stage, status, duration, message, and details.
- Recommendation/focus areas should highlight cities with fresh observations, market liquidity, clean bucket matching, high evidence coverage, and actionable but gated paper signals.

## Technology Stack

- Backend: Python, FastAPI, SQLite, `weatherbot_v3`.
- Frontend: Vite, React, TypeScript, Tailwind CSS, Recharts, lucide-react.
- Local backend: `http://127.0.0.1:8765`.
- Local frontend: `http://127.0.0.1:5173`.
- Main backend entrypoint: `dashboard_server.py`.
- Main frontend app: `frontend/src/App.tsx`.
- Main city dashboard component: `frontend/src/components/WeatherPanel.tsx`.

## Directory Boundaries

- `weatherbot_v3/`: production modules for config, DB, truth, forecast archives, distributions, qualification, execution, AI review, notifications, and CLI utilities.
- `dashboard_server.py`: API and adapter layer only. Move business logic into `weatherbot_v3/` when practical.
- `frontend/src/components/`: dashboard UI components.
- `tests/`: core regression tests, especially `tests/test_v3_core.py`.
- `legacy/`: read-only historical snapshot unless the user explicitly asks for legacy work.
- `data/`, `.env`, `config.json`, `.venv/`, `frontend/dist/`, `node_modules/`, `backups/`, and `audits/`: local state or generated artifacts; do not commit.

## Agent Guardrails

- Plan before code for Layers 0-6. For those layers, output a numbered plan and wait for user confirmation unless the user already provided an explicit implementation plan.
- No fabricated data. If a PolyWX field, API endpoint, unit, or schema is unknown, inspect the corpus or run Firecrawl. If evidence is unavailable, stop and ask.
- Scope lock: files outside the current layer are read-only unless the user names them. The right-column trading workbench and `legacy/` are always read-only unless the task title mentions them.
- Ask before deleting or rewriting existing collectors, tables, or components.
- Ask before broad refactors, renames, or moves outside the current layer.
- Verification is mandatory. A turn without a ledger entry and verification note is incomplete.

## UI Rules

- The dashboard is a trading workbench, not an explanation wall.
- Left column: city index, search, recommendation focus, station and signal summaries only.
- Center column: one city and one date. Show forecast, METAR, historical observations, bias statistics, fetch logs, market signals, and expandable details.
- Right column: paper account, one-click simulation, signal queue, trade records, and controlled execution actions.
- System readiness, data gates, truth health, model dataset audit, equity curve, and long explanations belong in folded system/review/risk areas, not the first viewport.
- Long text belongs in details, tooltips, row expansion, or secondary tabs.
- Avoid duplicate city headings. Keep selected city, station, date, signal state, and data freshness visible when the center panel scrolls.
- Empty states should show what is missing and which manual action can refresh it. Do not trigger automatic scans because a panel is empty.
- Desktop and mobile layouts must avoid horizontal overflow. Left, center, and right columns should scroll independently on desktop.

## PolyWX Theme Contract

- Support both PolyWX-style light and dark modes. Persist the user's theme choice locally.
- Light style: `#FFFFFF` page/panel backgrounds, `#111827` primary text, `border-gray-200` borders, restrained gray secondary text.
- Dark style: `#161A22` page background, `#1B212C` panels, `#222A37` raised/input surfaces, `#2C3445` borders, `#CBD2DC` primary text, `#7D8694` secondary text, `#2563EB` accent.
- Avoid decorative gradients, heavy chrome, and high-saturation panels unless a specific data state requires them.
- Use straight edges. Containers, buttons, tabs, inputs, tables, and cards should be `rounded-none` or equivalent.
- Top filter bar exposes city switching, continent filtering, and previous/next/today date controls.
- City workbench tabs are exactly: `预报`, `METAR`, `历史`, `偏差统计`, `抓取日志`.
- Hourly chart target: Recharts `ResponsiveContainer`, 24-hour chart from `00:00` to `23:00`, METAR/real observation solid line, model forecast dashed line, residual bars near the bottom.
- Tables use standard HTML tables with horizontal scrolling for wide schemas.
- Do not copy PolyWX membership, voting, feedback, branding, or non-trading prompts. WeatherBot's right side remains the controlled execution workbench.

## Data And Algorithm Rules

- Settlement truth is the foundation. Production calibration must use station-level or official/paid truth where possible.
- Open-Meteo archive is low-confidence fallback only. It cannot unlock live trading.
- METAR hourly observations are useful for D+0 reasoning but are not automatically final daily settlement truth.
- Probability must be stored and displayed as an auditable distribution, not just a single bucket EV.
- Every signal must preserve enough evidence to reconstruct the decision: market rule, station, date, forecast run, truth version, orderbook snapshot, distribution, risk gate, and paper/live decision.
- Independent settlement days matter more than repeated snapshots. Do not treat many snapshots from one market day as many independent samples.
- Low-price tail buckets, thin orderbooks, stale books, high spread, missing tick/orderMinSize, missing station truth, and short calibration history must be gated hard.
- Strategy changes must prove that allowed groups outperform blocked groups in paper/backtest before live canary expansion.

## Execution Safety

- Backend startup must be lightweight: do not auto-fetch weather, resume auto simulation, or start legacy infinite scans by default.
- Data refresh should be triggered by the dashboard action or explicit environment variables such as `WEATHERBOT_AUTO_REFRESH=true`.
- Do not start `weatherbet.py` legacy loops unless the user explicitly asks.
- Live trading defaults remain off. Live execution stays behind `LIVE_TRADING=false`, dry-run checks, risk gates, and canary sizing.
- First live behavior, when allowed, is BUY YES limit-only canary with strict idempotency, balance, tick size, `orderMinSize`, stale-book, duplicate-order, spread, and daily-limit checks.

## Naming And Code Style

- Python functions, variables, and DB helpers use `snake_case`.
- React components use `PascalCase`.
- Frontend local state and TypeScript props may use camelCase; backend payload fields generally remain snake_case unless an adapter maps them.
- New database tables, risk events, policy names, and log stages must use explicit, auditable names. Avoid vague abbreviations.
- Keep changes scoped. Do not refactor unrelated modules while fixing UI, data, or execution behavior.
- Use UTF-8 for Chinese docs and dashboard copy.

## Tools And Workflow

- Use `rg` / `rg --files` first for repo search.
- Use `apply_patch` for manual tracked-file edits.
- For UI/product work, use Product Design and data-visualization skills when requested or relevant.
- For PolyWX research, follow the PolyWX Reference Workflow. Avoid long broad crawls when the existing manifest already answers the question.
- Browser verification should use the in-app browser when available.
- Before spending time on browser debugging, check `/api/dashboard` latency and runtime state.
- Figma is a design baseline and communication tool. It does not replace code, runtime, or browser acceptance checks.
- For GitHub work, keep local git state and remote branch aligned. Stage only intended files.

## Required Checks

Run the checks that match the change. For most dashboard or backend work, run all of these:

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m unittest tests.test_v3_core
```

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run build
```

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

For Firecrawl / Layer 0 turns, additionally verify:

- `audits/polywx-firecrawl-<YYYY-MM-DD>/MANIFEST.json` exists.
- Manifest covers all five tabs plus the hourly chart.
- At least 3 cities x 3 dates were scraped with JS rendering when the task requires a full representative corpus.
- At least representative XHR response body evidence is captured and recorded.
- `SCHEMA_MAP_CN.md` maps captured fields to WeatherBot columns and dashboard components.

For frontend changes, also verify in a browser:

- No long-lived `正在连接本地看板 API`.
- No console errors or warnings introduced by the change.
- No horizontal overflow on the main dashboard.
- Left, center, and right columns remain independently scrollable on desktop.
- Live trading remains locked unless the task explicitly implements canary validation.

For docs-only turns, run at least:

```powershell
git diff --check
git status --short --branch
```

## Git Discipline

- Check status before editing and before committing.
- Do not stage unrelated user changes.
- Do not commit `audits/`, `data/`, `.env`, `config.json`, `.venv/`, `frontend/dist/`, `node_modules/`, or secrets.
- Prefer explicit staging:

```powershell
git -c safe.directory=C:/Users/Administrator/Documents/polymarket/weatherbot add -- AGENTS.md PROJECT_PROGRESS_CN.md
```

- Before push, confirm the diff contains only intended files.

## Next Goal Template

Use this as the next Codex goal when starting the production validation and dashboard pass:

```text
Continue WeatherBot v6 production validation and dashboard remediation. Follow AGENTS.md. Start by reading PROJECT_PROGRESS_CN.md and the latest PolyWX MANIFEST instead of repeating prior crawls. The project goal is real data foundation -> leakage-free probability model -> realistic paper execution -> production dashboard -> 14-30 day validation -> small live canary. Continue one Build Order layer at a time. Keep backend startup lightweight: no auto fetch, no auto simulation resume, no legacy scan. After each change, update PROJECT_PROGRESS_CN.md with usability, verification, blockers, and next step. Before commit, confirm no data/config/.env/.venv/audits artifacts are staged.
```

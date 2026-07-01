# WeatherBot Agent Rules

This file is the project-level operating guide for Codex and any other coding agent working on WeatherBot.

## Mission

WeatherBot is a production-oriented Polymarket weather trading platform. The goal is:

```text
real data foundation -> leakage-free probability model -> realistic paper execution -> production dashboard -> 14-30 day validation -> small live canary
```

Do not claim or imply that the bot can reliably make money until the paper-trading and validation gates prove it. Current status is still Phase 1.5 to Phase 2 transition: usable for simulation and observation, not for unattended live trading.

## Product Direction

- Learn PolyWX's information architecture: city-first dashboard, city index, recommendation focus, refresh time, date switching, forecast, METAR, historical observations, bias statistics, fetch logs, hourly rows, and clear empty states.
- Do not copy PolyWX branding, membership flows, voting widgets, non-trading prompts, or visual identity.
- Preserve WeatherBot's own edge: Polymarket event links, YES token data, real orderbooks, paper/live gates, truth versions, simulated fills, live dry-run checks, and risk controls.
- Use `suislanchez/polymarket-kalshi-weather-bot` only as a reference for FastAPI + SQLite + React dashboard structure and simulation ergonomics. Do not reintroduce BTC modules, Kalshi-first assumptions, or simple `edge > 8%` auto-trading logic.
- Treat `alteregoeth-ai/weatherbot` as the airport-station and multi-source weather baseline, not as a production trading architecture.

## Reference Fusion Architecture

Use these external repositories as design inputs, not as code to copy blindly. Every borrowed idea must be mapped into WeatherBot's data, audit, paper-trading, and risk-control model.

- `punkpeye/awesome-mcp-servers`: treat as a discovery index for research and data-acquisition tools. MCPs are supporting adapters, not core trading dependencies. Record whether each tool is local or cloud, whether it needs credentials, whether it can mutate state, and what evidence it produced. Firecrawl is allowed for PolyWX/GitHub/source research; production weather and trading decisions must still use typed WeatherBot collectors and persisted database rows.
- `python-metar/python-metar`: use as the METAR/SPECI decoding model. WeatherBot needs a first-class METAR ingestion layer that stores the raw report plus decoded temperature, dew point, wind, gust, visibility, cloud layers, altimeter/pressure, precipitation, sea-level pressure, peak wind, station id, report time, source URL, parser version, and parse warnings. NOAA current station TXT and 24-hour cycle files are valid collection targets. METAR supports D+0 and intraday evidence; it is not automatically final settlement truth unless the market rule says the airport METAR station is the settlement source.
- `Polymarket/*`: official market and CLOB references define execution boundaries. Prefer the current official unified SDK or documented CLOB flow for new execution work, and keep legacy client quirks isolated behind a `PolymarketExecutor` adapter. Every order path must fetch or persist token id, tick size, negRisk, orderMinSize, best bid/ask, book timestamp, allowance/balance state, idempotency key, and exact API response. First production shape remains BUY YES limit-only GTC/dry-run/canary; market orders, FOK/FAK, automated size escalation, and deposit-wallet auth workarounds are forbidden until explicitly validated.
- `yangyuan-zhen/PolyWeather`: borrow the production weather-intelligence shape: city terminal, observation-driven chart updates, aviation METAR/TAF, official nearby-network layers, DEB/hourly consensus, full bucket distribution, strict market-bucket matching, SSE/event replay, health/metrics endpoints, and a clear public/private trading boundary. Do not copy subscription/payment/growth code or private strategy thresholds. The useful product idea is a city/date evidence terminal where observations, forecasts, settlement station, probability buckets, and market quotes are generated from one auditable core.

### WeatherBot Target Data Foundation

The next architecture pass should make these data layers explicit in SQLite and API payloads:

- `stations`: city key, display name, ICAO/WMO/provider station ids, timezone, settlement rule text, primary settlement source, nearby observation networks, and confidence.
- `metar_reports`: raw METAR/SPECI text, decoded fields, parser version, report time, station id, source URL, fetch time, parse status, and parse warnings.
- `mesonet_observations`: non-METAR official/local networks such as JMA AMeDAS, HKO, CWA, AMOS, NWS/NOAA, airport runway sensors, and other rule-relevant station feeds. These rows must be labeled as observation evidence, not settlement truth by default.
- `forecast_runs` and `forecast_members`: ECMWF/GFS/HRRR/Open-Meteo/DEB inputs with run time, valid time, horizon, member values, and source quality.
- `hourly_consensus`: one city/date/hour path used by the chart and signal engine; it must separate real observations, forecast consensus, TAF timing markers, cloud/humidity, and residuals.
- `market_buckets`: all Polymarket outcomes for a city/date event, with exact/range/or-higher/or-lower direction, token id, quote, tick size, orderMinSize, and strict matching status.
- `signal_decisions`: distribution, model-market edge, execution gate, AI review, paper/live decision, skip reason, and source evidence links.

### PolyWX / PolyWeather Dashboard Generation Rules

- The dashboard should be generated from the same city/date evidence payload that powers signals. Do not build separate visual-only mock data paths.
- Each city page should show one primary hourly chart, one probability/market bucket module, then five tabbed evidence tables: Forecast, METAR, Historical, Diff Stats, Fetch Log.
- The hourly chart must make observation provenance clear: Real METAR or official observation lines are solid; model/DEB forecasts are dashed; residuals are red/blue bars; humidity/cloud can be secondary bars only when real data exists.
- The METAR tab must show raw report, decoded temperature/dew point/wind/cloud/pressure/precipitation, report age, parser warnings, and station-local time.
- The Historical tab must distinguish settlement truth, METAR history, official nearby-network history, and Open-Meteo fallback. Fallback rows must not unlock live gates.
- The Diff Stats tab must compute observed minus forecast, MAE/bias/Pearson R, overlap count, source coverage, and whether the sample is independent by settlement day.
- The Fetch Log tab must use structured backend rows (`source`, `stage`, `status`, `duration`, `message`, `details`) rather than guessing from raw UI events.
- The recommendation/focus area should highlight cities with fresh observations, market liquidity, clean bucket matching, high evidence coverage, and actionable but still gated paper signals.

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
- `dashboard_server.py`: API and adapter layer only. Keep business logic moving into `weatherbot_v3/` when practical.
- `frontend/src/components/`: dashboard UI components.
- `tests/`: core regression tests, especially `tests/test_v3_core.py`.
- `legacy/`: read-only historical snapshot unless the user explicitly asks for legacy work.
- `data/`, `.env`, `config.json`, `.venv/`, `frontend/dist/`, `node_modules/`, `backups/`, and `audits/`: local state or generated artifacts; do not commit.

## UI Rules

- The dashboard is a trading workbench, not an explanation wall.
- Left column: city index, search, recommendation focus, station and signal summaries only.
- Center column: one city and one date. Show forecast, METAR, historical observations, bias statistics, fetch logs, market signals, and expandable details.
- Right column: paper account, one-click simulation, signal queue, trade records, and controlled execution actions.
- System readiness, data gates, truth health, model dataset audit, equity curve, and long explanations belong in folded "system / review / risk" areas, not the first viewport.
- Long text must go into `details`, tooltips, row expansion, or a secondary tab.
- Avoid duplicate city headings. Keep the selected city, station, date, signal state, and data freshness visible when the center panel scrolls.
- Empty states must be useful and calm: show what is missing and which manual action can refresh it. Do not trigger automatic scans just because a panel is empty.
- Desktop and mobile layouts must avoid horizontal overflow. Left, center, and right columns should scroll independently on desktop.

## PolyWX Workbench Theme Contract

When the task is to align with PolyWX, use the current PolyWX page as the product baseline, but preserve WeatherBot trading controls and auditability.

- Theme support: WeatherBot must support both PolyWX-style light and dark modes. Do not hard-code the page into one theme. Persist the user's theme choice locally.
- Light visual style: use `#FFFFFF` page/panel backgrounds, `#111827` primary text, `border-gray-200` borders, and restrained gray secondary text.
- Dark visual style: align with Firecrawl-extracted PolyWX branding: `#161A22` page background, `#1B212C` panels, `#222A37` raised/input surfaces, `#2C3445` borders, `#CBD2DC` primary text, `#7D8694` secondary text, and `#2563EB` primary accent.
- Avoid decorative gradients, heavy dashboard chrome, and high-saturation panels unless a specific data state requires them.
- Corners: use straight edges everywhere. Containers, buttons, tabs, inputs, tables, and cards must be `rounded-none` or equivalent.
- Top filter bar: must expose City switching, Continent filtering, and a date switcher with previous/next/today behavior. Keep these controls in the primary path, not hidden in details.
- Tabs: the city workbench must include exactly these five primary data tabs, localized in Chinese when the surrounding UI is Chinese: `预报`, `METAR`, `历史`, `偏差统计`, `抓取日志`. Tabs are straight-edged segmented controls, not pill buttons.
- Hourly Temperature chart: use Recharts `ResponsiveContainer` and a 24-hour chart from `00:00` to `23:00`. The production target is a dual-axis line chart: Real METAR as a solid black line `#000000`, Model Forecast as a gray dashed line `#6B7280` with `strokeDasharray="4 4"`, and Diff residuals as red/blue bars near the bottom.
- Tables: tab interiors should use standard HTML tables. Header cells are bold with `#F9FAFB` background; rows use fixed `py-2 px-4` spacing and `hover:bg-gray-50`. Preserve horizontal scrolling for wide schemas instead of compressing text into unreadable cells.
- PolyWX evidence to preserve: product/version placement, language toggle, light-mode indicator, manual refresh action, city list by continent, date controls, `Forecast / METAR / Historical / Diff Stats / Fetch Log` information architecture, hourly chart, forecast table schema, METAR/historical observation schemas, Diff Stats, and Fetch Log.
- Do not copy PolyWX membership, voting, feedback, branding, or non-trading prompts. WeatherBot's right side remains the controlled paper/live execution workbench.

## Data And Algorithm Rules

- Settlement truth is the foundation. Production calibration must use station-level or official/paid truth where possible.
- Open-Meteo archive is only a low-confidence fallback. It cannot unlock live trading.
- METAR hourly observations are useful for D+0 reasoning but are not automatically final daily settlement truth.
- Probability must be stored and displayed as an auditable distribution, not just a single bucket EV.
- Every signal should preserve enough evidence to reconstruct the decision: market rule, station, date, forecast run, truth version, orderbook snapshot, distribution, risk gate, and paper/live decision.
- Independent settlement days matter more than repeated snapshots. Do not treat many snapshots from one market day as many independent samples.
- Low-price tail buckets, thin orderbooks, stale books, high spread, missing tick/orderMinSize, missing station truth, and short calibration history must be gated hard.
- Strategy changes must prove that allowed groups outperform blocked groups in paper/backtest before any live canary expansion.

## Execution Safety

- Backend startup must be lightweight: do not auto-fetch weather, do not resume auto simulation, and do not start legacy infinite scans by default.
- Data refresh should be triggered by the dashboard "manual fetch" action or by explicit environment variables such as `WEATHERBOT_AUTO_REFRESH=true`.
- Do not start `weatherbet.py` legacy loops unless the user explicitly asks.
- Live trading defaults remain off. Live execution must stay behind `LIVE_TRADING=false` by default, dry-run checks, risk gates, and canary sizing.
- First live behavior, when allowed in the future, is BUY YES limit-only canary with strict idempotency, balance, tick size, orderMinSize, stale-book, duplicate-order, spread, and daily-limit checks.

## Naming And Code Style

- Python functions, variables, and DB helpers use `snake_case`.
- React components use `PascalCase`.
- Frontend local state and TypeScript props may use camelCase; backend payload fields generally remain snake_case unless an existing adapter maps them.
- New database tables, risk events, policy names, and log stages must use explicit, auditable names. Avoid vague abbreviations.
- Keep changes scoped. Do not refactor unrelated modules while fixing UI, data, or execution behavior.
- Use UTF-8 for Chinese docs and dashboard copy. Avoid introducing garbled text; read Chinese files with UTF-8 in PowerShell.

## Tools And Workflow

- Use `rg` / `rg --files` first for repo search.
- Use `apply_patch` for manual tracked-file edits.
- For UI/product work, use the Product Design and data-visualization skills when requested or relevant.
- For PolyWX research, use Firecrawl in this order: `firecrawl_map` or `firecrawl_search` first, then a schema-scoped `firecrawl_scrape` for specific modules/fields. Avoid long `firecrawl_interact`, broad markdown scrapes, or feedback calls in the critical path when they are not necessary for the current edit.
- Browser verification should use the in-app browser when available. Before spending time on browser debugging, quickly check `/api/dashboard` latency and runtime state.
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
} | ConvertTo-Json
```

For frontend changes, also verify in a browser:

- No long-lived "正在连接本地看板 API".
- No console errors or warnings introduced by the change.
- No horizontal overflow on the main dashboard.
- Left, center, and right columns remain independently scrollable on desktop.
- Live trading remains locked unless the task explicitly implements canary validation.

## Git Discipline

- Check status before editing and before committing:

```powershell
git -c safe.directory=C:/Users/Administrator/Documents/polymarket/weatherbot status --short --branch
```

- Do not stage unrelated user changes.
- Do not commit `audits/`, `data/`, `.env`, `config.json`, `.venv/`, `frontend/dist/`, `node_modules/`, or secrets.
- Prefer explicit staging:

```powershell
git -c safe.directory=C:/Users/Administrator/Documents/polymarket/weatherbot add -- AGENTS.md
```

- Before push, confirm the diff contains only intended files.

## Next Goal Template

Use this as the next Codex goal when starting the production validation and dashboard pass:

```text
Continue WeatherBot v6 production validation and dashboard remediation. Follow AGENTS.md. The project goal is real data foundation -> leakage-free probability model -> realistic paper execution -> production dashboard -> 14-30 day validation -> small live canary. Continue optimizing the UI around a PolyWX-style city workbench: left side only city index, recommendation focus, and search; center city page shows forecast, METAR, historical observations, bias statistics, fetch logs, market signals, and expandable details; right side only paper account, signal queue, trade records, and controlled actions. Do not put data gates, long explanations, equity curves, or system audits in the first viewport; place them in folded system/review/risk areas. Keep backend startup lightweight: no auto fetch, no auto simulation resume, no legacy scan. After each change, run Python unittest, frontend build, /api/dashboard latency/runtime checks, and browser UI verification. Before commit, confirm no data/config/.env/.venv/audits artifacts are staged.
```

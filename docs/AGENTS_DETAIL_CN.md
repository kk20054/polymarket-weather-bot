# WeatherBot Agent Detail Rules CN

本文件承接从 `AGENTS.md` 精简出的详细规则。日常开工只读 `docs/CURRENT_STATE.md`；只有任务涉及 UI/PolyWX/算法/目录边界/历史设计决策时，再按需阅读本文件的相关章节。

---

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
3. Use a schema-scoped `firecrawl_scrape` with JavaScript rendering for each of the five tabs and the hourly chart. Capture rendered DOM, visible network calls, and inline JSON when available.
4. For at least 3 cities x 3 dates, scrape city/date URLs and store outputs under `audits/polywx-firecrawl-<YYYY-MM-DD>/<city>/<date>/`.
5. Write `MANIFEST.json` with crawl start/end time, Firecrawl ids, sha256 file list, discovered API endpoints, JS-rendered/static status, unresolved gaps, and per-tab coverage counts.
6. Write `SCHEMA_MAP_CN.md` mapping each PolyWX field to a WeatherBot SQLite column and dashboard component.

Corpus validity requires `MANIFEST.json` less than 14 days old, all five tabs plus hourly chart, and at least representative XHR response body evidence. A static markdown-only scrape is not valid because PolyWX is a JS-rendered SPA.

Never say PolyWX has been "fully crawled" unless the manifest proves it. Current accepted wording for partial evidence is "rendered evidence corpus" or "representative XHR corpus", not "full source clone".

## Reference Fusion Architecture

Use external repositories as design inputs, not code to copy blindly. Every borrowed idea must be mapped into WeatherBot's data, audit, paper-trading, and risk-control model and pinned to a Build Order layer.

- `punkpeye/awesome-mcp-servers`: discovery index for research and data-acquisition tools. MCPs are supporting adapters, not core trading dependencies. Firecrawl is allowed for PolyWX/GitHub/source research; production decisions must still use typed collectors and persisted database rows. Layer 0 only.
- `python-metar/python-metar`: METAR/SPECI decoding model. Store raw report plus decoded temperature, dew point, wind, gust, visibility, cloud layers, altimeter/pressure, precipitation, sea-level pressure, peak wind, station id, report time, source URL, parser version, and parse warnings. Layer 2.
- `Polymarket/*`: official market and CLOB references define execution boundaries. Keep order creation behind a `PolymarketExecutor` adapter with token id, tick size, `negRisk`, `orderMinSize`, best bid/ask, book timestamp, allowance/balance state, idempotency key, and exact API response. First production shape remains BUY YES limit-only GTC/dry-run/canary. Layer 5 boundary plus Layer 8 execution.
- `yangyuan-zhen/PolyWeather`: borrow the city terminal shape, observation-driven chart updates, aviation METAR/TAF, nearby official network layers, DEB/hourly consensus, full bucket distribution, strict market-bucket matching, SSE/event replay, health endpoints, and public/private trading boundary. Layers 4-7.
- PolyWX corpus: dashboard visual evidence and signal evidence surface. Layers 6-7.

## Target Data Foundation

- `stations`: city key, display name, ICAO/WMO/provider station ids, timezone, settlement rule text, primary settlement source, nearby observation networks, confidence, `display_enabled`, collector `enabled`, and `city_scope`. Display visibility never grants collector or trading permission.
- `metar_reports`: raw METAR/SPECI text, decoded fields, parser version, report time, station id, source URL, fetch time, parse status, and parse warnings.
- `mesonet_observations`: non-METAR official/local networks such as JMA AMeDAS, HKO, CWA, AMOS, NWS/NOAA, airport runway sensors, and other rule-relevant station feeds. Label as observation evidence, not settlement truth by default.
- `forecast_runs` and `forecast_members`: ECMWF/GFS/HRRR/Open-Meteo/DEB inputs with run time, valid time, horizon, member values, and source quality.
- `hourly_consensus`: one city/date/hour path for chart and signal engine, separating observations, forecast consensus, timing markers, cloud/humidity, and residuals.
- `daily_max_predictions`: city_key, target_date, issued_at, mu, sigma, model_weights, member_count, components, source_run_ids, sigma_floor, and mu_observed_floor_applied.
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
- Keep two recommendation contracts separate. `weather_focus` mirrors the public PolyWX card shape (city, local date, current temperature, predicted maximum) and is never a trade claim. `trade_candidate` comes only from Layer 6 market buckets, model probability, edge, and explicit paper/live gates.

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

## PolyWX Workbench Theme Contract

- Support both PolyWX-style light and dark modes. Persist the user's theme choice locally.
- Light style: `#FFFFFF` page/panel backgrounds, `#111827` primary text, `border-gray-200` borders, restrained gray secondary text.
- Dark style: align with Firecrawl-extracted PolyWX branding: `#161A22` page background, `#1B212C` panels, `#222A37` raised/input surfaces, `#2C3445` borders, `#CBD2DC` primary text, `#7D8694` secondary text, `#2563EB` accent.
- Avoid decorative gradients, heavy chrome, and high-saturation panels unless a specific data state requires them.
- Use straight edges. Containers, buttons, tabs, inputs, tables, and cards should be `rounded-none` or equivalent.
- Top filter bar exposes City switching, Continent filtering, and a date switcher with previous/next/today controls.
- City workbench tabs are exactly: `预报`, `METAR`, `历史`, `偏差统计`, `抓取日志`.
- Hourly chart target: Recharts `ResponsiveContainer`, 24-hour chart from `00:00` to `23:00`, METAR/real observation solid line, model forecast dashed line, residual bars near the bottom.
- Tables use standard HTML tables with horizontal scrolling for wide schemas.
- Do not copy PolyWX membership, voting, feedback, branding, or non-trading prompts. WeatherBot's right side remains the controlled execution workbench.

## Data And Algorithm Rules

- Settlement truth is the foundation. Production calibration must use station-level or official/paid truth where possible.
- Open-Meteo archive is low-confidence fallback only. It cannot unlock live trading.
- METAR hourly observations are useful for D+0 reasoning but are not automatically final daily settlement truth.
- Probability must be stored and displayed as an auditable distribution, not just a single bucket EV.
- Probability distributions must be persisted as `(mu, sigma)` parameters or an empirical distribution. Never store only one bucket EV as the model evidence.
- `sigma` must have a floor, defaulting to `0.5°C` or the unit-equivalent value, and should decay with remaining intraday uncertainty when observations constrain the day.
- `mu` must be floored by the highest observed temperature so far for the target station/day before any order decision can use it.
- Every signal must preserve enough evidence to reconstruct the decision: market rule, station, date, forecast run, truth version, orderbook snapshot, distribution, risk gate, and paper/live decision.
- Independent settlement days matter more than repeated snapshots. Do not treat many snapshots from one market day as many independent samples.
- Low-price tail buckets, thin orderbooks, stale books, high spread, missing tick/orderMinSize, missing station truth, and short calibration history must be gated hard.
- Strategy changes must prove that allowed groups outperform blocked groups in paper/backtest before live canary expansion.

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

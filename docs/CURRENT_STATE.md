# WeatherBot Current State

## Current Layer
- Date: 2026-08-16. Phase 3/6: forward simulation and production hardening.
- Production DB: `D:\WeatherBot\data\weatherbot_v3.db` (about 60GB).
- Local backend and frontend run on ports `8765/5173`; the scheduler is explicitly user-controlled and is currently stopped.
- One selected strategy revision drives signal generation and both order adapters; simulated and live execution do not use separate models or thresholds.
- `LIVE_TRADING=false` and the live order adapter is not production-ready, so real submission remains unavailable internally without cluttering the dashboard.

## Latest Evidence
- Core coverage is complete for 49 enabled cities: observations, forecasts, DEB, market buckets, and decisions are present.
- Old run `paper-20260727T010646Z-0a8f6729` is stopped and immutable: 4 orders, 2 resolved losses, realized PnL `-$2.31`.
- Active simulation run is `paper-20260809T165308Z-bbfb1434`, using bankroll `$40`, max `$2/trade`, and `$10/day`.
- Active revision `spr_13639230b1b3e97631aec4cf3f811749` uses edge `5%`, Kelly multiplier `0.25`, and bankroll cap `12.5%`; the user-entered trade cap remains authoritative.
- Model weighting now defaults to dynamic prior plus leakage-free inverse-MAE shrinkage from the first paired result; Model Analysis also supports a persisted manual override.
- Strategy settings are applied atomically to `signal_generation`, `paper_default`, and `live_default`; only the final order adapter differs.
- New decisions are writing against the active revision. Current rejects are explainable market/data gates such as spread, missing book sides, D+0 timing, and insufficient effective edge.
- Frozen forward validation remains negative: mean CLV `-3.28pp` (95% CI `[-5.47pp, -1.10pp]`), so there is no evidence supporting live deployment.
- WU truth covers 48/49 cities but is immature; PWS remains unavailable without entitlement.
- The Vite frontend is deployed to Vercel project `weatherbot-frontend`; GitHub auto-deploy is connected to `kk20054/polymarket-weather-bot`.
- Vercel serves the frontend at `https://www.polywxx.org`; its same-origin `/api/*` gateway proxies read-only requests to the local FastAPI backend through `api.polywxx.org`.
- `api.polywxx.org` is carried by a named Cloudflare Tunnel installed as an automatic Windows service. The origin requires a server-side token, and the public gateway rejects write methods with HTTP `405`.
- The public dashboard is operational but intentionally read-only. Local `http://127.0.0.1:5173` remains the writable operator interface; the public API depends on this laptop and FastAPI being online.
- The dashboard removed forward-validation, public/live-lock, strategy-backup, and advanced-diagnostic chrome; developer settings now contain only data connections and strategy controls.
- City grouping is timezone-first; New Zealand and Australia are shown under Oceania.

## Production Blockers
- Real Polymarket submission is incomplete: the legacy executor lacks production idempotency, aggregate risk reservation, and revision-bound routing.
- Strategy profitability is unproven; current forward CLV and settled results are negative.
- Truth maturity, source entitlement, SQLite size, and single-writer contention remain operational risks.

## Next Task
- Keep GitHub `main` as the Vercel frontend source of truth; runtime data, tunnel credentials, and API secrets remain local and ignored.
- Keep the laptop and FastAPI backend online when the public dashboard must show live data; Cloudflare Tunnel now starts automatically with Windows.
- Let the active simulation run collect new decisions and settlements under the explicit bankroll settings; do not create another queue.
- Review simulation fills, CLV, and settled PnL by strategy revision before changing strategy parameters again.
- Implement and independently verify the real CLOB execution path before making the live option selectable.

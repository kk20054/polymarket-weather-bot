# WeatherBot Current State

## Current Layer
- Date: 2026-08-16. Phase 3/6: forward simulation and production hardening.
- Production DB: `D:\WeatherBot\data\weatherbot_v3.db` (about 60GB).
- Backend, frontend, and scheduler were stopped cleanly before GitHub publication; ports `8765` and `5173` are offline.
- One strategy engine serves two execution modes; there is no separate exploration queue.
- `LIVE_TRADING=false` and the live executor is not production-ready, so live remains unavailable.

## Latest Evidence
- Core coverage is complete for 49 enabled cities: observations, forecasts, DEB, market buckets, and decisions are present.
- Old run `paper-20260727T010646Z-0a8f6729` is stopped and immutable: 4 orders, 2 resolved losses, realized PnL `-$2.31`.
- Active simulation run is `paper-20260809T165308Z-bbfb1434`, using bankroll `$40`, max `$2/trade`, and `$10/day`.
- Active revision `spr_13639230b1b3e97631aec4cf3f811749` uses simulation edge `5%`, Kelly multiplier `0.25`, and bankroll cap `12.5%`; the user-entered trade cap remains authoritative.
- Live evaluation is independent of simulation evaluation, but remains blocked by `LIVE_TRADING=false` and `live_execution_not_ready`; it never falls back to a simulated order.
- New decisions are writing against the active revision. Current rejects are explainable market/data gates such as spread, missing book sides, D+0 timing, and insufficient effective edge.
- Frozen forward validation remains negative: mean CLV `-3.28pp` (95% CI `[-5.47pp, -1.10pp]`), so there is no evidence supporting live deployment.
- WU truth covers 48/49 cities but is immature; PWS remains unavailable without entitlement.
- The Vite frontend is deployed to Vercel project `weatherbot-frontend`; GitHub auto-deploy is connected to `kk20054/polymarket-weather-bot`.
- Vercel serves the frontend at `https://weatherbot-frontend-8c43w4lvj-max-janel.vercel.app`; `polywxx.org` is assigned but awaits Cloudflare A records.

## Production Blockers
- Real Polymarket submission is incomplete: the legacy executor lacks production idempotency, aggregate risk reservation, and revision-bound routing.
- Strategy profitability is unproven; current forward CLV and settled results are negative.
- Truth maturity, source entitlement, SQLite size, and single-writer contention remain operational risks.

## Next Task
- Keep GitHub `main` as the Vercel frontend source of truth; runtime data and secrets remain local and ignored.
- Complete Cloudflare DNS for `polywxx.org` and `www.polywxx.org`, then verify HTTPS and the honest backend-offline state.
- Let the active simulation run collect new decisions and settlements under the explicit bankroll settings; do not create another queue.
- Review simulation fills, CLV, and settled PnL by strategy revision before changing strategy parameters again.
- Implement and independently verify the real CLOB execution path before making the live option selectable.

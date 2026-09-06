# WeatherBot Current State

## Current Layer
- Date: 2026-09-06. Scope: connectivity/Shanghai September 7 quotes, presentation and local-only live canary infrastructure. No risk/model/protocol changes.
- Production DB: `D:\WeatherBot\data\weatherbot_v3.db` (~60GB), through repository `data` Junction. Do not scan or delete broadly.
- Local backend/frontend: `8765/5173`; public Vercel frontend uses Cloudflare Tunnel `api.polywxx.org` to the laptop. Public access stays read-only.

## Incident Evidence
- At ~17:00 +08 both local ports were closed and no Python process existed. Cloudflared was running; public API returned 502. Service exit cause is not established; no evidence attributes it to a US proxy or Polymarket maintenance.
- Desktop launcher restored backend PID `16752` at 17:01 (uvicorn ready 17:01:47), frontend, and scheduler (started `2026-09-06T17:02:46.097153+08:00`). Local/public health returned 200.
- Shanghai September 7: all 11 books last fetched successfully at 16:50, then aged while services stopped. At 17:08 all 11 refreshed: 6 two-sided, 5 genuinely lacking bids. Live /book and Gamma returned 200; 30C market active/accepting orders, bid/ask initially 52/54c and later 53/54c. Future market date does not imply a fresh quote.
- Backend geoblock read returned `blocked=false,country=HK`: request egress, not user location or eligibility. Global CLOB status operational. Polymarket US uses separate API hosts; US maintenance not verified.
- At 17:28 existing run `paper-20260905T153105Z-89acc10b` was ACTIVE with 5 orders/5 open/0 settled/0 exits. No account was started or changed by this turn. Scheduler executes collection and existing-run ticks.

## Implementation And Verification
- UI now says `报价未刷新` with timestamp help, not expired market. Current book prices precede prediction-snapshot prices; a missing current side never borrows an old quote or Gamma indicative price.
- Live: revision-bound BUY YES GTC canary, explicit credentials, geography/YES/neg-risk checks, authenticated balance/allowance reads, canonical sizing/effective edge, existing quote/liquidity gates, transactional pre-POST reservation, persisted signed hash, single POST, unknown status, cancel and identity-checked reconciliation.
- Optional `requirements-live.txt` pins official `py-clob-client-v2==1.1.0`. Isolated install/pip check passed, 48 transport tests passed. Production environment was not modified; optional SDK not installed there.
- Local checks: 13 service/API tests + 43 transport tests passed; 5 SDK tests skipped in production venv (passed isolated). Existing architectural-lock regression passed separately. Frontend build/whitespace check passed; known large-bundle/caniuse warnings remain.
- Browser verified Shanghai September 7 forecast/DEB/all 11 buckets, scheduler running and current 5-position account, without trading actions.
- `LIVE_TRADING=false`, `LIVE_DRY_RUN=true`, `LIVE_EXECUTION_PRODUCTION_READY=false`. Zero real live orders at audit. No production credentials or real order/allowance/wallet operations used.
- IMPORTANT: final backend restart was blocked by tool policy before any stop ran. PID 16752 still serves old import-time backend; new live routes NOT loaded. Frontend HMR reflects quote fix. Manual operator restart required; do not claim backend deployment complete.

## Retained Strategy Facts
- Calibration scoring uses `runtime-bias-walk-forward-v2`; fitted MAE is not predictive accuracy. Legacy scores do not drive weights/residual spread. Prior-only training boundaries/frozen protocols unchanged.
- Dynamic weighting uses station/model/location-version/lead inverse-MAE shrinkage; primary prior excludes GEM/JMA, retained diagnostically. Manual override remains. Paper/live share models but distinct maturity/sizing gates remain.
- Earlier ended cohort `paper-20260809T165308Z-bbfb1434`: 13 orders, 11 settled (2 wins/9 losses), realized -$4.9111, 2 rejected. Do not mix it with the new active cohort or claim profitability.

## Remaining Blockers And Next Task
- Canary is not a full automated portfolio: no automatic SELL, chain settlement/marked-equity reconciliation or full lifecycle qualification. Matched/unknown/partially cancelled orders conservatively retain exposure. Keep production-ready false.
- Operator must restart local backend through desktop launcher, then check `/api/live/status` stays disabled and the same paper run survives. Do not create another run or change credentials/settings.
- No historical model-selection/PnL reruns. Laptop uptime, store size, truth entitlement and prior npm advisories remain separate issues.

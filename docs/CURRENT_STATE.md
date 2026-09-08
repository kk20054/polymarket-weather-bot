# WeatherBot Current State
## Current Layer
- Date: 2026-09-08. Scope: rule-aligned temperature buckets and direct probability/exit/calibration consumers. No risk threshold, weight, frozen protocol or cohort-parameter changes.
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
- Release: source `c43d082` pushed to main and feature branch; Vercel `dpl_4vi8CGh6DyqHR1xfdzWd4N2AaVkr` READY at 17:33, both custom domains assigned. Public JS `index-C7__FmBH.js` matches the local build; public health 200.
- `LIVE_TRADING=false`, `LIVE_DRY_RUN=true`, `LIVE_EXECUTION_PRODUCTION_READY=false`. Zero real live orders at audit. No production credentials or real order/allowance/wallet operations used.
- September 8 restart completed through the desktop launcher at 10:10 +08: backend PID 6916/8765, frontend PID 10132/5173, scheduler started 10:12:02. `/api/live/status` now returns 200, `live-execution-v2-canary`, enabled=false and production_ready=false. The same active 5-position paper run survived.
- Startup UI replaces the square spinner with WeatherBot/weather branding and a thin loading track; saved theme/language, reduced motion and reconnect supported. Frontend scoped check/build passed; dark desktop, light 390px mobile, injected disconnect/reconnect and order-tab rendering checked. Local only; public frontend still uses the September 6 release above.
- Read smoke checks: health/dashboard/live/scheduler/orders/predictions/buckets/probabilities/decisions/hourly APIs returned 200. Shanghai September 8: 11/11 strict-matched buckets, 25 prediction records and 9 decision records available. This confirms readable data, not complete freshness or execution qualification. Orders response took ~10.5s.
- NOAA WRH official display uses Math.round in C/F modes: exact k means [k-0.5,k+0.5). New `gaussian-cdf-rule-aligned-v3` shares boundaries with ensemble and observed-breach/exit checks. Source comes from explicit primary URL or primary-rule paragraph, never a conditional fallback. Unknown legacy rules are unchanged.
- September 8 bounded check: 49 cities/539 buckets, 49 normalized positive distributions, all 47 NOAA events have gap-free intervals (3 require primary-rule text). WU/IEM rows are excluded from NEW training on explicitly NOAA-owned dates; NOAA orders cannot promote them to exact truth. No historical orders/settlements rewritten.
- Local restart: backend PID 20340 started 10:55:29 +08; scheduler 10:56:03. Health/frontend HTTP 200, Shanghai API carries new contract; one rebuilt decision persisted NOAA bounds [28.5,29.5), sum=1, blocked by orderbook_fetch_failed. 37 targeted tests passed; no full suite/frontend rebuild/history backtest this turn.

## Retained Strategy Facts
- Calibration scoring uses `runtime-bias-walk-forward-v2`; fitted MAE is not predictive accuracy. Legacy scores do not drive weights/residual spread. Prior-only training boundaries/frozen protocols unchanged.
- Dynamic weighting uses station/model/location-version/lead inverse-MAE shrinkage; primary prior excludes GEM/JMA, retained diagnostically. Manual override remains. Paper/live share models but distinct maturity/sizing gates remain.
- Earlier ended cohort `paper-20260809T165308Z-bbfb1434`: 13 orders, 11 settled (2 wins/9 losses), realized -$4.9111, 2 rejected. Do not mix it with the new active cohort or claim profitability.

## Remaining Blockers And Next Task
- Canary is not a full automated portfolio: no automatic SELL, chain settlement/marked-equity reconciliation or full lifecycle qualification. Matched/unknown/partially cancelled orders conservatively retain exposure. Keep production-ready false.
- Runtime warnings at 10:20 +08: second orderbook poll timed out after 30s to CLOB (0/539 refreshed; first poll succeeded). First derive batch reported 49 failed cities; Shanghai log shows 24 hourly rows and one signal result for today, zero new daily predictions, and no tomorrow buckets. Child reasons are omitted from this summary, so do not equate the warning with all data being absent or claim its full cause established. Investigate model freshness/child reasons separately without weakening gates.
- Current cohort `paper-20260905T153105Z-89acc10b`: its first 5 September 7 orders Gamma-resolved at ~10:22 September 8, 2 wins/3 losses, realized +$6.459048. All 5 lack exact temperature and are calibration-ineligible. This is not evidence of a durable edge; no account reset/manual order.
- Next: authorized NOAA WRH historical truth with dated station/rule/revision provenance. Existing WU calibration artifacts are not invalidated by this patch and may still inform NOAA predictions; source-regime-specific bias/MAE artifact selection remains open. Never infer continuous truth from winner buckets or use a local fetch failure to authorize WU fallback.
- No historical model-selection/PnL reruns. Laptop uptime, store size, truth entitlement and prior npm advisories remain separate issues.

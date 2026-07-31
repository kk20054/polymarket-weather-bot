# WeatherBot Current State

## Current Layer
- Date: 2026-08-01. Phase 3/6: frozen forward observation and operator-access hardening.
- Backend, frontend, and the 12-poller scheduler are running against `D:\WeatherBot\data\weatherbot_v3.db`.
- `LIVE_TRADING=false`; no profitability claim has been established.
- Frozen v2 validation remains unchanged: model-side `0.20<=ask<0.40`, `edge>=8%`, target `N=338`.

## Latest Evidence
- Sites version 2 is deployed privately at `https://weatherbot-polymarket-v1.kl28398052.chatgpt.site`.
- Hosted reads use a protected Cloudflare origin tunnel while the laptop is online and fall back to the immutable snapshot when it is offline.
- Hosted writes are owner-only and allowlisted. API settings, live/canary execution, secrets, and legacy actions remain local-only.
- The tunnel origin requires `WEATHERBOT_ORIGIN_TOKEN`; localhost remains usable without the token.
- Local `/api/healthz` is immediate, `/api/dashboard` returned 200 in 8.8s with a 1.09MB payload, and `/api/source-health` exceeded 30s.
- The production SQLite database is 38.62GB. A fast audit estimates that duplicate raw payloads older than 30 days account for about 0.94GB.
- `storage-archive` now supports checksum-verified gzip archival before clearing duplicate `raw_json`; `storage-restore` reverses it.
- No storage archive or VACUUM has been applied to the production database in this turn.

## Production Blockers
- The current Cloudflare Quick Tunnel URL is temporary and changes after restart; stable remote operation needs a named tunnel/domain.
- Dashboard/source-health aggregation is still too slow for a consistently responsive public operator experience.
- Forward validation has not reached the frozen `N=338` stop rule; strategy quality and profitability remain unproven.

## Next Task
- Sign in with the owner account and verify the deployed private Sites dashboard in live and laptop-offline modes.
- Replace Quick Tunnel with a named Cloudflare Tunnel before treating remote writes as production-stable.
- Schedule downtime, run `storage-archive --apply`, verify archives, then run an offline SQLite compaction separately.
- Profile and cache `/api/source-health` and reduce the 1.09MB dashboard payload without changing strategy semantics.

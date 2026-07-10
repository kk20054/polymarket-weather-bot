# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-10. Phase 2 data-source health stabilization; Layer 1 consistency fix and Layer 2/3/5/6 health observability are implemented.
- Usable for source observation, controlled paper research, and scheduler soak testing. It is not production-ready for unattended paper settlement or live trading.
- `LIVE_TRADING=false`; no canary is permitted before 14-30 days of validated paper results.
- Station verification inversion is repaired from persisted Gamma probe evidence: Atlanta/Chicago/Dallas/NYC/Shanghai/Tokyo are `verified`; Hong Kong is `settlement_mismatch` (VHHH observation vs HKO settlement).
- Source health is available through `python -m weatherbot_v3.cli source-health`, `GET /api/source-health`, and the compact `GET /api/scheduler/status.source_health` summary.
- The scheduler was explicitly started at `2026-07-10T09:31:32Z` on backend `127.0.0.1:8765` for a 24-hour soak; startup remains opt-in.

## Latest Ledger Summaries
- 2026-07-10 / Layer 1: restored seven overwritten settlement-rule records from `data_fetch_logs`, repaired six verified statuses and one Hong Kong mismatch, and protected verified timestamps during future registry sync.
- 2026-07-10 / Layer 2-6 observability: added a 13-row source health matrix covering contracts, METAR, Open-Meteo, Weather.com, China Live, PWS, WU hourly/daily, IEM, HKO, orderbook, consensus, and decisions.
- Code commit: `d58ae12`.
- Fetch-log secret redaction commit: `8eda988`; 24 local log rows containing a plaintext weather key were scrubbed, with zero matches remaining.
- 2026-07-10 / Scheduler soak: first cycle restored Open-Meteo, orderbook, and hourly consensus; China Live reported one failed city; Weather.com and PWS coverage remain incomplete.
- 2026-07-09 / WU truth: 10 cities x 7 days of WU daily truth and 1,609 hourly rows were persisted; 30-day coverage remains pending.
- 2026-07-09 / Forecast alignment: Weather.com v3 is wired into scheduler/DEB but has only Shanghai smoke coverage before this soak.

## Production Blockers
- Settlement contracts cover 7/14 enabled cities; Hong Kong remains paper-only because settlement station and observation station differ.
- WU daily/hourly history is 7 days for 10 cities, not the required 30 days; HKO daily truth has only one day.
- Weather.com v3 coverage is incomplete; the current key returns PWS v2 HTTP 401 and a dedicated WU PWS API key is required. Shanghai China Live returned HTTP 502 while Hong Kong HKO succeeded.
- Saved PolyWX benchmarks still show material Forecast/Cloud/DEB differences that require offline reconstruction after source coverage is stable.
- Paper orders have no complete settlement lifecycle, realized PnL, win rate, or Brier-score validation.
- The current database is large and scheduler first-cycle pollers can run for a long time; the 24-hour soak must establish duration, failures, and freshness before widening scope.
- UI subtraction and component splitting are deliberately deferred until data and paper lifecycle evidence are trustworthy.

## Next Step
- Let the scheduler soak continue; sample `/api/scheduler/status` and `/api/source-health`, diagnose the China Live failure and long-running pollers, then extend WU/HKO truth to 30 days.

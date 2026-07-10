# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2 data-source stabilization; Layer 1 verification consistency and Layer 2/3/5/6 scheduler observability are implemented.
- The system is usable for monitored data collection and controlled paper research. It is not ready for unattended paper settlement or live trading.
- `LIVE_TRADING=false`; no canary is permitted before a complete paper settlement lifecycle and 14-30 validated days.
- Settlement evidence is consistent for the current seven audited cities: six verified contracts and one Hong Kong settlement mismatch.
- Scheduler startup remains explicit. The current soak started at 2026-07-10 23:50 Asia/Shanghai; six uninterrupted hours is the preliminary acceptance gate, while 24 hours remains the stronger pre-paper stability check.

## Latest Ledger Summaries
- 2026-07-11 / Truth audit: `truth_delta_audit` now persists `delta_hko_minus_iem`, maps Hong Kong HKO settlement truth to VHHH observations, and carries the city key. IEM truth fetch now correctly uses physical `station_id` rather than the HKO settlement authority. Commits `cc90a90`, `649e2d8`.
- 2026-07-10 / Layer 1 + source health: restored six verified contracts plus the Hong Kong mismatch and added a 13-source health matrix. Commits `d58ae12`, `8eda988`.
- 2026-07-10 / Scheduler capacity: batched SQLite/CLOB operations, bounded city work, and removed duplicated derive market refresh. Commit `9f1a04f`.
- 2026-07-10 / Quote resilience: Gamma refresh now updates the decision tables every five minutes; AWC retries transient reads; optional PWS 401 responses cool down. Commits `d4c90f7`, `dc1cd8b`.
- Runtime benchmarks: METAR 14/14 in 89.8s; Forecast 14/14 in 362.9s; Gamma 30 events and 330 books in 9.6-14.1s; isolated Derive 14/14 in 572.1s.
- Verification: 184 `tests.test_v3_core` tests passed after the truth audit changes; `git diff --check` passed.

## Production Blockers
- The new scheduler needs an uninterrupted six-hour preliminary soak from commit `dc1cd8b`; 24 hours remains required before paper-validation evidence can be trusted.
- Settlement contracts cover only 7/14 enabled cities; Hong Kong remains paper-only because VHHH observations do not equal HKO settlement truth.
- WU daily/hourly history is 7 days for 10 cities, not the required 30 days; HKO daily truth has only one day.
- Weather.com v3 coverage is incomplete; the current key returns PWS v2 HTTP 401, so a dedicated WU PWS API permission/key is still required.
- Saved PolyWX benchmarks still show material Forecast/Cloud/DEB differences.
- Paper orders do not yet have a complete settlement lifecycle, realized PnL, win rate, or Brier-score validation.
- Live dry-run, balance, duplicate-order, and canary gates are not accepted for production use.

## Next Step
- Let the committed scheduler reach its six-hour preliminary gate and inspect per-poller continuity, failures, and freshness.
- Extend WU truth to 30 days and HKO truth to 30 days, then rebuild WU-IEM and HKO-VHHH deltas using `cc90a90`.
- Expand Weather.com coverage and resolve the WU PWS permission gap.
- Rebuild saved PolyWX Forecast/Cloud/DEB benchmarks only after source coverage is stable.
- Implement the paper settlement and scoring lifecycle before any live canary discussion.

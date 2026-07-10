# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-10. Phase 2 data-source stabilization; Layer 1 verification consistency and Layer 2/3/5/6 scheduler observability are implemented.
- The system is usable for monitored data collection and controlled paper research. It is not ready for unattended paper settlement or live trading.
- `LIVE_TRADING=false`; no canary is permitted before a complete paper settlement lifecycle and 14-30 validated days.
- Settlement evidence is consistent for the current seven audited cities: six verified contracts and one Hong Kong settlement mismatch.
- Scheduler startup remains explicit. The committed scheduler now uses bounded city tasks, staggered pollers, batch CLOB requests, batch SQLite transactions, and a dedicated five-minute refresh for the exact orderbook tables consumed by signal decisions.

## Latest Ledger Summaries
- 2026-07-10 / Layer 1: restored overwritten Gamma settlement evidence and reconciled six verified cities plus the Hong Kong mismatch. Commit `d58ae12`.
- 2026-07-10 / Source health: added a 13-row source health matrix through CLI and API; fetch-log secret redaction removed 24 leaked local URL values. Commits `d58ae12`, `8eda988`.
- 2026-07-10 / Scheduler capacity: fixed repeated 3 GB schema initialization, CLOB `/book` fan-out, unbounded city work, and duplicated derive market refreshes. Commit `9f1a04f`.
- 2026-07-10 / Quote freshness: moved active `market_buckets/orderbooks` refresh into the five-minute Gamma poller so long Derive runs cannot make decision quotes stale. Commit `d4c90f7`.
- 2026-07-10 / Scheduler resilience: classify unlisted Seoul CLOB tokens as auditable book gaps, retry transient AWC reads, and cool down rejected PWS credentials for one hour. Commit `dc1cd8b`.
- Runtime benchmarks: METAR 14/14 in 89.8s; Forecast 14/14 in 362.9s; Gamma 30 events and 330 books in 9.6-14.1s; isolated Derive 14/14 in 572.1s.
- Verification: 205 backend/contract tests passed; frontend production build passed; `git diff --check` passed.

## Production Blockers
- The new scheduler implementation still needs an uninterrupted 24-hour soak from commit `dc1cd8b`; the prior one-hour run proved poller continuity but found one AWC timeout, one benign Seoul book-gap batch, and an over-noisy PWS authorization failure.
- Settlement contracts cover only 7/14 enabled cities; Hong Kong remains paper-only because VHHH observations do not equal HKO settlement truth.
- WU daily/hourly history is 7 days for 10 cities, not the required 30 days; HKO daily truth has only one day.
- Weather.com v3 coverage is incomplete; the current key returns PWS v2 HTTP 401, so a dedicated WU PWS API permission/key is still required.
- Saved PolyWX benchmarks still show material Forecast/Cloud/DEB differences.
- Paper orders do not yet have a complete settlement lifecycle, realized PnL, win rate, or Brier-score validation.
- Live dry-run, balance, duplicate-order, and canary gates are not accepted for production use.

## Next Step
- Run the committed scheduler for 24 hours and sample source health, poller durations, failures, and freshness.
- Extend WU truth to 30 days and HKO truth to 30 days, then compute WU-IEM and HKO-VHHH deltas.
- Expand Weather.com coverage and resolve the WU PWS permission gap.
- Rebuild saved PolyWX Forecast/Cloud/DEB benchmarks only after source coverage is stable.
- Implement the paper settlement and scoring lifecycle before any live canary discussion.

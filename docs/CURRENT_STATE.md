# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2 source-health verification is active; a new 24-hour scheduler soak started at `2026-07-11T08:17:11Z` before the remaining PolyWX benchmark and Layer 7 acceptance work.
- All 14 enabled cities have verified settlement coverage; Hong Kong intentionally remains HKO/VHHH mismatch and paper-only.
- The scheduler is running. Its first new cycle completed METAR 14/14 and China Live 2/2; paper validation remains explicitly inactive.
- The system supports audited collection, truth deltas, decisions, controlled paper execution, and authoritative Gamma settlement. It has not proved unattended profitability.
- `LIVE_TRADING=false`; no live or canary execution is permitted.

## Latest Ledger Summaries
- 2026-07-11 / Source health v2: station verification invariants are enforced in both directions; `/api/source-health` now exposes 14 city rows across 13 sources. Scheduler soak started at `08:17:11Z`. Commit `25d2396`.
- 2026-07-11 / Layer 6 probability repair: exact integer-C market buckets now use the same canonical truncation boundaries as Gaussian CDF buckets. Shanghai distribution sums to 1.0 instead of 0.0. Commit `e978b73`.
- 2026-07-11 / Layer 7 reduction: removed duplicate city filters, inactive cities, internal rule badges, repeated forecast controls, and legacy one-click simulation. The right rail now reads the inactive paper cohort status. Commit `110bfc2`.
- 2026-07-11 / Paper cohort: inactive-by-default 14-30 day validation enforces $40 bankroll, $2/trade, $10/day, five open positions/orders, and fresh post-start decisions. Commit `d1876b5`.
- 2026-07-11 / Paper settlement: new v1 orders only realize PnL after authoritative closed Gamma outcomes and record win rate and Brier scores. Commit `4245bf4`.

## Production Blockers
- Layer 7 is materially cleaner; the Hourly chart is now isolated and has passed desktop/768px QA, but `WeatherPanel.tsx` still needs DEB/table extraction and operator review.
- PWS v2 returns HTTP 401 with the current entitlement, so PWS peak-lock is unavailable.
- Saved PolyWX replay still shows material Cloud, Forecast, and Chicago 07-04 DEB differences.
- Scheduler status is in-memory; source-health v2 now provides a persistent-data-derived 14-city/source matrix even after process restart.
- Derive can exceed its 15-minute interval under load.
- No new cohort orders have resolved, so production PnL, win rate, and Brier evidence remain empty.
- Live dry-run, balance, duplicate-order, and canary gates are not accepted.

## Next Step
- Complete the 24-hour source-health soak; stop and diagnose any material core-source regression.
- Then rebuild the saved PolyWX Forecast/Cloud/DEB dates before the remaining Layer 7 UI audit.
- Obtain operator acceptance, then explicitly start the inactive Layer 9 cohort; do not start it beforehand.
- Run 14-30 days using only new `paper-execution-v1` orders and authoritative settlements.
- Resolve or explicitly disable PWS; quantify Cloud/Forecast/DEB differences through paper scoring.

# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2 data-source stabilization is complete enough for monitored use; Layer 7 operator-dashboard acceptance is in progress before Layer 9 paper validation starts.
- All 14 enabled cities have verified settlement coverage; Hong Kong intentionally remains HKO/VHHH mismatch and paper-only.
- The scheduler passed an approximately nine-hour preliminary soak, but is currently stopped. The paper cohort is implemented and explicitly inactive.
- The system supports audited collection, truth deltas, decisions, controlled paper execution, and authoritative Gamma settlement. It has not proved unattended profitability.
- `LIVE_TRADING=false`; no live or canary execution is permitted.

## Latest Ledger Summaries
- 2026-07-11 / Layer 6 probability repair: exact integer-C market buckets now use the same canonical truncation boundaries as Gaussian CDF buckets. Shanghai distribution sums to 1.0 instead of 0.0. Commit `e978b73`.
- 2026-07-11 / Layer 7 reduction: removed duplicate city filters, inactive cities, internal rule badges, repeated forecast controls, and legacy one-click simulation. The right rail now reads the inactive paper cohort status. Commit `110bfc2`.
- 2026-07-11 / Paper cohort: inactive-by-default 14-30 day validation enforces $40 bankroll, $2/trade, $10/day, five open positions/orders, and fresh post-start decisions. Commit `d1876b5`.
- 2026-07-11 / Paper settlement: new v1 orders only realize PnL after authoritative closed Gamma outcomes and record win rate and Brier scores. Commit `4245bf4`.
- 2026-07-11 / Data foundation: 14-city settlement verification, 30-day WU/IEM/HKO truth, 69 mature bias rows, and leakage-safe PolyWX replay are complete. Commits `1e90c5c`, `26d887f`, `937a203`, `02ee52e`.

## Production Blockers
- Layer 7 is materially cleaner; the Hourly chart is now isolated and has passed desktop/768px QA, but `WeatherPanel.tsx` still needs DEB/table extraction and operator review.
- PWS v2 returns HTTP 401 with the current entitlement, so PWS peak-lock is unavailable.
- Saved PolyWX replay still shows material Cloud, Forecast, and Chicago 07-04 DEB differences.
- Scheduler status is in-memory; after a backend restart badges honestly show “未运行” instead of persisted source freshness.
- Derive can exceed its 15-minute interval under load.
- No new cohort orders have resolved, so production PnL, win rate, and Brier evidence remain empty.
- Live dry-run, balance, duplicate-order, and canary gates are not accepted.

## Next Step
- Continue Layer 7: split DEB/table components, then run the final production-workbench audit with Product Design, data-visualization, and browser evidence.
- Obtain operator acceptance, then explicitly start the inactive Layer 9 cohort; do not start it beforehand.
- Run 14-30 days using only new `paper-execution-v1` orders and authoritative settlements.
- Resolve or explicitly disable PWS; quantify Cloud/Forecast/DEB differences through paper scoring.

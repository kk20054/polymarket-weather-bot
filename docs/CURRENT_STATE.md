# WeatherBot Current State

## Current Layer
- Date: 2026-07-24. Phase 3/6: leakage-safe calibration, real ensemble distributions and controlled paper validation.
- Observation and revision-bound paper operation are available. `LIVE_TRADING=false`; no profitability claim.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`.

## Latest Evidence
- Model calibration is now expanding-window walk-forward: each date is scored with bias fitted only on earlier independent settlement dates.
- Open-Meteo Previous Runs are fixed-lead slices across multiple initializations, not one archived run. They remain diagnostic-only and are excluded from trading calibration.
- V3 is accumulating normally. Current independent pairs include Shanghai 10, Chicago 8, Tokyo 6 and Hong Kong 0; all remain below the 20-pair mature-weight threshold.
- A provisional DEB may display prior weights when every source is immature, but `core_modal_v1` blocks paper entry when calibrated-weight coverage is below 80%.
- Real GFS ensemble snapshots persist 31 members. Full local days are training-eligible; truncated edge days are marked partial and excluded. Bucket probabilities preserve empirical GFS member shape while deterministic families use calibrated kernels.
- Orderbook replay now selects the last quote with `quote_timestamp <= decision_time`, rejects future/stale books, walks ask levels for executable depth, and no longer treats total depth as best-ask liquidity.
- Full verification passed: 444 Python tests, frontend production build, whitespace check and observation readiness.

## Production Blockers
- Only 11 authoritative settlements are available; realized PnL is `-$4.17`.
- Model Brier `0.062806` is worse than market Brier `0.049140`; edge is not proven.
- 35 enabled cities still lack sufficient WU/HKO truth coverage and 179 weighted components lack mature seven-day calibration.

## Next Task
- Keep paper validation only. Accumulate exact run snapshots and authoritative truth, then evaluate calibration by city, model family and decision lead-time before changing strategy thresholds.

# WeatherBot Current State

## Current Layer
- Date: 2026-07-25. Phase 3/6: leakage-safe calibration, real ensemble distributions and controlled paper validation.
- Paper simulation uses immediate dynamic model weighting. Calibration maturity limits live trading only.
- `LIVE_TRADING=false`; profitability is not yet proven.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`.

## Latest Evidence
- Every configured model with a prior weight participates from its first valid forecast.
- Dynamic weight blends the prior with inverse-MAE performance as leakage-free pairs accumulate; the performance share ramps continuously with sample count.
- Models without a settled MAE retain a nonzero prior-only weight instead of being silently removed.
- Shanghai currently gives V3 about 42.9% at `n=10`, while JMA still receives about 7.1% before its first valid MAE.
- Wide model disagreement is a paper caution, not a paper blocker. It remains a live-maturity blocker.
- Active paper run: `paper-20260725T045941Z-2059361a`, profile revision `spr_e3462ea2aacb622e7335b2acab6b2c30`, $40 bankroll, `model_guarded` exit.
- Real GFS ensemble snapshots persist 31 members. Orderbook replay remains point-in-time and rejects future or stale quotes.
- Focused strategy tests and the frontend production build passed.

## Production Blockers
- Executable paper orders still require valid bid/ask, acceptable spread, sufficient depth/order size, fresh quotes and positive edge.
- Sparse calibration increases uncertainty; it no longer suppresses paper discovery but still blocks live approval.
- Several cities and model families do not yet have enough settled truth to support a live-trading claim.

## Next Task
- Keep the scheduler running and classify every skipped candidate as no-edge, quote/spread, order-minimum or stale-data.
- Evaluate calibration, fill quality, ROI and drawdown before changing any live threshold.

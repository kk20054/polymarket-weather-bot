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
- Shanghai D+0 currently predicts `34.50 +/- 1.20 C`; D+1 predicts `32.68 +/- 2.19 C`.
- Shanghai currently gives V3 about 42.9% at `n=10`; JMA's 8 real pairs now move it from the 7.3% prior to about 8.3%.
- Wide model disagreement is a paper caution, not a paper blocker. It remains a live-maturity blocker.
- Active paper run: `paper-20260725T045941Z-2059361a`, profile revision `spr_e3462ea2aacb622e7335b2acab6b2c30`, $40 bankroll, `model_guarded` exit.
- Real GFS ensemble snapshots persist 31 members. Orderbook replay remains point-in-time and rejects future or stale quotes.
- The latest paper candidate passed model edge checks but was skipped when execution revalidation found a stale/widened orderbook.
- The global minimum executable edge defaults to 8% and is editable in Strategy Settings; execution revalidation uses the same threshold.
- Bias correction now begins at 10 leakage-safe pairs with zero-prior shrinkage `n/(n+10)` and a +/-2.5 C cap before bucket probabilities are calculated.
- Visual Crossing Pro is ready as an optional paper/history calibration provider with masked settings and payload validation; it cannot unlock live truth.
- Focused strategy, settings, dynamic-weight and walk-forward calibration tests passed.

## Production Blockers
- Executable paper orders still require valid bid/ask, acceptable spread, sufficient depth/order size, fresh quotes and positive edge.
- Sparse calibration increases uncertainty; it no longer suppresses paper discovery but still blocks live approval.
- Several cities and model families do not yet have enough settled truth to support a live-trading claim.

## Next Task
- Keep the scheduler running and classify every skipped candidate as no-edge, quote/spread, order-minimum or stale-data.
- Next calibration upgrade: segment residuals by D+0/D+1/D+2 lead time and validate with walk-forward replay before changing the live path.

# WeatherBot Current State

## Current Layer
- Date: 2026-07-26. Phase 3/6: leakage-safe calibration, real ensemble distributions and controlled paper validation.
- Paper simulation uses immediate dynamic model weighting. Calibration maturity limits live trading only.
- `LIVE_TRADING=false`; profitability is not yet proven.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`.

## Latest Evidence
- Signal funnel audit covers 49 enabled cities x 3 target dates: 147/147 have DEB and components, 146/147 have at least four model families, all 147 have a computable model spread, and 1,617 buckets strict-match.
- Deterministic runs now count as participating model families when they carry a positive weight and forecast evidence. `member_count` is reserved for distribution/sigma evidence.
- All default collector, truth, market and decision coverage is driven by `stations.enabled`; legacy five-city/default-limit paths were removed.
- During the latest three target dates, 7 cities produced 8 executable paper candidates: Amsterdam, Chicago, Karachi, Moscow, Singapore, Wellington and Wuhan.
- The latest round for those dates currently has zero paper candidates. Every rejection is classified as data gap, strict-match failure, untradeable book/order minimum, or genuine no-edge; there is no unknown bucket.
- The current future operational window has one visible paper candidate (Amsterdam 2026-07-27). The dashboard now consumes strategy candidates instead of hiding them behind weather-focus cards.
- Tail strategy's 20-day independent-settlement requirement is a live-maturity rule only. It does not suppress paper discovery.
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
- Bias training now covers every enabled registry city, including Wellington and Chongqing; their five available model families each have 6 independent settled pairs instead of 0.
- Visual Crossing Pro is ready as an optional paper/history calibration provider with masked settings and payload validation; it cannot unlock live truth.
- Focused strategy, settings, dynamic-weight and walk-forward calibration tests passed.
- Live CLOB audit sampled 15 CoreModal Top-1 buckets across 15 cities: 15/15 requests returned HTTP 200 and genuinely had no YES bids, while DB and live asks both equaled 0.001.
- Orderbook state now distinguishes `two_sided`, `side_absent`, `book_absent` and `fetch_failed`; failed fetches preserve quote timestamps and cannot masquerade as fresh quotes.

## Production Blockers
- Executable paper orders still require valid bid/ask, acceptable spread, sufficient depth/order size, fresh quotes and positive edge. These thresholds were not lowered.
- The latest runtime verification is blocked by stale `hourly_consensus`, decisions older than 30 minutes and one stale/future-dated orderbook candidate.
- Current dominant decision reasons are invalid bid/ask, wide spread, stale book, exchange minimum above the risk budget, and genuine edge below the configured minimum.
- CoreModal can still rank a physically eliminated bucket after the observed maximum has passed it; this is the next strategy defect to fix without lowering risk thresholds.
- Sparse calibration increases uncertainty; it no longer suppresses paper discovery but still blocks live approval.
- Several cities and model families do not yet have enough settled truth to support a live-trading claim.

## Next Task
- Exclude physically eliminated buckets before CoreModal ranking, then refresh decisions and rerun the funnel without changing risk thresholds.
- Next calibration upgrade: segment residuals by D+0/D+1/D+2 lead time and validate with walk-forward replay before changing the live path.

# WeatherBot Current State

## Current Layer
- Date: 2026-07-26. Phase 3/6: leakage-safe calibration, real ensemble distributions and controlled paper validation.
- Paper simulation is entering a forward-only validation phase. Historical P&L is no longer the primary iteration metric; CLV and proper probability scores are collected alongside final P&L.
- `LIVE_TRADING=false`; profitability is not yet proven.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`.

## Latest Evidence
- Signal funnel audit covers 49 enabled cities x 3 target dates: 147/147 have DEB and components, 146/147 have at least four model families, all 147 have a computable model spread, and 1,617 buckets strict-match.
- During the latest three target dates, 7 cities produced 8 executable paper candidates: Amsterdam, Chicago, Karachi, Moscow, Singapore, Wellington and Wuhan.
- Production bucket probabilities now use one normalized clean-Gaussian contract with a nonzero numerical floor; observed highs block impossible trades without rewriting the model distribution.
- Before station-by-lead calibration matures, V3 is the production cold-start baseline. ECMWF/GFS/ICON remain diagnostic inputs; GEM/JMA have zero production prior after the benchmark rejected their fixed weights.
- Bias correction is segmented by station and forecast lead when those leakage-safe records are available.
- D+0 candidates are generated only in the local peak-minus-2-to-3-hour window; D+1 and historical replay keep their existing timing contract.
- Active paper run: `paper-20260725T045941Z-2059361a`, profile revision `spr_e3462ea2aacb622e7335b2acab6b2c30`, $40 bankroll, `model_guarded` exit.
- Real GFS ensemble snapshots persist 31 members. Orderbook replay remains point-in-time and rejects future or stale quotes.
- Bias correction now begins at 10 leakage-safe pairs with zero-prior shrinkage `n/(n+10)` and a +/-2.5 C cap before bucket probabilities are calculated.
- Live CLOB audit sampled 15 CoreModal Top-1 buckets across 15 cities: 15/15 requests returned HTTP 200 and genuinely had no YES bids, while DB and live asks both equaled 0.001.
- Orderbook state now distinguishes `two_sided`, `side_absent`, `book_absent` and `fetch_failed`; failed fetches preserve quote timestamps and cannot masquerade as fresh quotes.
- New forward candidates persist immutable decision ask/quote age and append pre-close quotes keyed to Gamma market close time, enabling future CLV measurement.
- Corrected V3-only historical P&L: at edge>=8% and 5-10c ask, ROI was +57.73% with a non-informative 95% CI of [-100.00%, +227.57%]. No N>=25 condition subset beat the market on Brier.
- Detecting a 20% ROI in that ask band needs about 2,426 trades, or roughly 1,120 days at the observed rate; this is why the next stage is prospective CLV/probability validation rather than another historical P&L loop.

## Production Blockers
- Executable paper orders still require valid bid/ask, acceptable spread, sufficient depth/order size, fresh quotes and positive edge. These thresholds were not lowered.
- The latest runtime verification is blocked by stale `hourly_consensus`, decisions older than 30 minutes and one stale/future-dated orderbook candidate.
- Current dominant decision reasons are invalid bid/ask, wide spread, stale book, exchange minimum above the risk budget, and genuine edge below the configured minimum.
- Historical rows before this migration do not contain immutable decision-time quote and labeled pre-close quote evidence, so they cannot support rigorous CLV.
- Sparse calibration increases uncertainty; it no longer suppresses paper discovery but still blocks live approval.
- Several cities and model families do not yet have enough settled truth to support a live-trading claim.

## Next Task
- Restart the normal scheduler after deployment and begin forward paper observation; do not reopen historical model-selection loops.
- Report CLV, multiclass Brier/log loss and realized paper P&L once new candidates have both immutable decision quotes and pre-close snapshots.

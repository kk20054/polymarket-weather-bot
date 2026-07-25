# WeatherBot Current State

## Current Layer
- Date: 2026-07-25. Phase 3/6: leakage-safe calibration, real ensemble distributions and controlled paper validation.
- Observation and revision-bound paper operation are available. `LIVE_TRADING=false`; no profitability claim.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`.

## Latest Evidence
- `core_modal_v1` now separates paper and live maturity: 10-19 independent settlements are provisional paper candidates at 0.5x size; 20 remains the mature/live threshold.
- Component MAE and dynamic weighting begin at 10 leakage-free pairs; additive bias correction and live maturity still require 20.
- Active paper run: `paper-20260725T030230Z-6828a63a`, profile revision `spr_6404882c2553f33d03ef38da029b0106`, $40 bankroll, `hold_to_settlement`, live locked.
- Shanghai V3 is active at `n=10` with 7-day MAE about `1.21°C`; the model dialog now prioritizes ranking, sample count, forecast high, weight and real MAE without repeated internal-status labels.
- Real GFS ensemble snapshots persist 31 members. Orderbook replay remains point-in-time and rejects future or stale quotes.
- Targeted strategy/exit/UI contract tests (31) and the frontend production build passed. Browser checks found no console error or horizontal page overflow.

## Production Blockers
- Profitability is still unproven; provisional paper permission is not a live-trading approval.
- Authoritative truth, model agreement, market depth, spread, edge and order-size gates still apply after the 10-sample threshold.
- Several cities and model families remain below mature calibration coverage; V3 has not yet produced a leakage-safe MAE for every city.

## Next Task
- Let the active paper run collect decisions and settlements, then compare calibration, fill quality, ROI and drawdown by city, model family and lead time before changing live thresholds.

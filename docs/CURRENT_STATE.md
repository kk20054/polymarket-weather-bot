# WeatherBot Current State

## Current Layer
- Date: 2026-07-25. Phase 3/6: leakage-safe calibration, real ensemble distributions and controlled paper validation.
- Observation and revision-bound paper operation are available. `LIVE_TRADING=false`; no profitability claim.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`.

## Latest Evidence
- `core_modal_v1` now separates paper and live maturity: 10-19 independent settlements are provisional paper candidates at 0.5x size; 20 remains the mature/live threshold.
- Component calibration uses the same split: paper may use a provisional component from 10 samples, while live still requires 20 and non-imputed MAE.
- Active paper run: `paper-20260724T165032Z-c5158ac8`, profile revision `spr_34fca82e96190f1888ff4f0124cc232a`, $50 bankroll, live locked.
- Shanghai V3 is present and accumulating (`n=10` on 2026-07-25). The DEB model dialog now exposes real revision paths, current disagreement, weights and sample counts.
- Real GFS ensemble snapshots persist 31 members. Orderbook replay remains point-in-time and rejects future or stale quotes.
- Targeted strategy tests (30) and the frontend production build passed. Browser checks found no console error or horizontal page overflow.

## Production Blockers
- Profitability is still unproven; provisional paper permission is not a live-trading approval.
- Authoritative truth, model agreement, market depth, spread, edge and order-size gates still apply after the 10-sample threshold.
- Several cities and model families remain below mature calibration coverage; V3 has not yet produced a leakage-safe MAE for every city.

## Next Task
- Let the active paper run collect decisions and settlements, then compare calibration, fill quality, ROI and drawdown by city, model family and lead time before changing live thresholds.

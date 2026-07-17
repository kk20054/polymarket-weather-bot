# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-17. Phase 2/3 data-source and probability-contract validation.
- Observation and controlled rebuild tooling are usable. Unattended paper validation and live trading are not ready.
- D+0 `forecast-snapshot-selection-v2` now admits a late snapshot only for points whose `valid_at >= snapshot available_at`; elapsed-hour revisions remain excluded. The verifier requires this point-level contract and `temporal_no_leak=pass`.
- Current Shanghai: model center `36.306C`, sigma `2.097C`, observed max `39C`; PolyWX center `36.826C`, sigma `2.183C`, observed max `39C`. Model-center delta is `-0.520C`.
- Current Chicago: model center `32.712C`, sigma `1.391C`, observed max `26.7C`; PolyWX center `32.588C`, sigma `1.713C`, observed max `26.7C`. Model-center delta is `+0.124C`.
- Same-date 24-hour parity is not yet acceptable: Shanghai forecast MAE `1.125C` and cloud MAE `36.79pp`; Chicago forecast MAE `0.417F` and cloud MAE `12.63pp`. Missing intra-day Weather.com snapshots explain much of the elapsed-hour gap but do not excuse it for production.
- WU Historical is present and correctly bucketed by station-local hour. The dashboard now draws native minute-frequency points but computes delta/correlation/overlap from aligned hourly rows; Chicago `:51` observations therefore contribute `7` paired hours instead of `0`.
- Scheduler is stopped, auto simulation is off, paper cohort is inactive, settlements are `0`, and `LIVE_TRADING=false`.

## Production Blockers
- Current-day source continuity is incomplete: China Live returned HTTP 502, PWS remains unavailable, and elapsed Weather.com snapshots are sparse because the scheduler was not continuously collecting revisions.
- Layer 5/6 is stale and lacks an atomic decision-round contract: no current matched market/orderbook round, decisions do not yet bind one exact DEB revision to one immutable quote snapshot, and 16 high-weight components lack authoritative seven-day calibration.
- Trading performance is unproven: 49 historical paper fills have no settlement rows or realized PnL; the required 14-day/30-settlement cohort has not started.

## Next Task
- Add forecast-snapshot continuity gating and an immutable revision-bound market/decision round. Do not start the paper cohort until same-date field parity, authoritative calibration, and current decision lineage pass.

# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-17. Phase 2/3 data-source alignment with fail-closed Layer 4/6 contracts.
- Observation, saved PolyWX comparison, and controlled simulation tooling are usable; unattended paper validation and live trading are not ready.
- D+0 forecast selection now uses `forecast-snapshot-selection-v2`: for each model member and UTC valid hour it chooses the latest snapshot available at one fixed cutoff, then aggregates the city-local day.
- D+1/D+2 continue to use one latest model run. Snapshot availability, peak provenance, coverage, selection hash, and contributing run IDs are persisted for audit.
- Future snapshots are excluded, partial later runs fill only their available hours, and DST duplicate local hours are retained by canonical UTC valid time.
- Shanghai 2026-07-15 rebuilt row 2636: model `36.598C +/- 1.715C` versus saved PolyWX `37.094C +/- 1.671C`; center delta `-0.496C`, sigma delta `+0.044C`. The observed-floor trading center remains separately auditable at `38.5C`.
- Chicago 2026-07-15 rebuilt row 2637: model `35.452C +/- 1.072C` versus saved PolyWX `35.793C +/- 1.075C`; center delta `-0.341C`, sigma delta `-0.003C`.
- Both saved same-date benchmarks now pass the current `0.5C` center target without copying PolyWX values.
- Legacy rows without a same-as-of cohort contract remain hidden from DEB, buckets, signals, and execution.
- Scheduler is stopped, auto simulation is off, paper validation is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Runtime sources, order books, derived decisions, and dashboard are currently stale or stopped; project verification therefore remains `code_only`.
- Sixteen high-weight model components still lack seven-day bias samples backed by authoritative WU/HKO truth.
- No active 14-day paper cohort or 30 authoritative resolved paper positions exist, so ROI and Brier edge are unproven.
- Layer 8 live execution still lacks final atomic idempotency reservation, aggregate risk budgeting, and revision-bound routing.

## Next Task
- Run one controlled current-day collection and PolyWX parity benchmark through snapshot-selection-v2; only after fresh source, market, decision, and dashboard checks pass may the 14-day paper-validation cohort begin.

# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-15. Phase 2/3 data-source alignment with Layer 7/8 fail-closed hardening.
- Observation, PolyWX comparison, and controlled simulation are usable; unattended paper validation and live trading are not ready.
- Shanghai 2026-07-14 currently builds Weather.com revision-aware forecast, native WU Historical, DEB `35.50C +/- 1.62C`, and a continuous Gaussian distribution.
- Scheduler is stopped, auto simulation is off, paper validation is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Forecast inputs are not yet guaranteed to share a fresh as-of cutoff; missing historical revisions cannot be reconstructed after the fact.
- The core/CLI paper path can still bypass a required cohort identity, and authoritative WU/HKO settlement evidence is insufficient for ROI claims.
- Live execution still lacks final idempotency reservation and aggregate risk budgeting; PWS entitlement also remains unavailable.

## Next Task
- Finish the Shanghai forecast/DEB discrepancy pass: enforce source-age and unit contracts, fix tail-bucket API serialization/loading state, then compare the same city/date against the saved PolyWX evidence. Do not start a soak or live path.

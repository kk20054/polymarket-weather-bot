# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-15. Phase 2/3 data-source alignment with Layer 7/8 fail-closed hardening.
- Observation, PolyWX comparison, and controlled simulation are usable; unattended paper validation and live trading are not ready.
- Forecast/DEB rows now require an auditable same-as-of source cohort; legacy rows without that contract are hidden from DEB, buckets, signals, and execution.
- Shanghai 2026-07-14 still has Weather.com forecast and native WU Historical evidence, but its old DEB and 28 derived decisions are correctly suppressed pending a fresh rebuild.
- Scheduler is stopped, auto simulation is off, paper validation is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- A fresh collector-to-DEB rebuild must prove the same-as-of cohort contract; missing historical revisions cannot be reconstructed after the fact.
- Authoritative WU/HKO settlement evidence remains insufficient for ROI claims, and PWS entitlement is unavailable.
- Live execution still lacks final idempotency reservation and aggregate risk budgeting.

## Next Task
- Run one fresh Shanghai/Chicago collection and rebuild, compare the same city/date against saved PolyWX evidence, then admit only valid rows to a controlled paper cohort. Do not start a soak or live path.

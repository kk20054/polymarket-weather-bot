# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-15. Phase 2/3 data-source alignment with Layer 7/8 fail-closed hardening.
- Observation, PolyWX comparison, and controlled simulation are usable; unattended paper validation and live trading are not ready.
- Forecast/DEB rows now require an auditable same-as-of source cohort; legacy rows without that contract are hidden from DEB, buckets, signals, and execution.
- Shanghai/Chicago 2026-07-15 were freshly collected and rebuilt with an accepted Weather.com v3 + Open-Meteo cohort; Shanghai DEB is visible at `38.50°C ± 1.71°C`.
- Shanghai 2026-07-14 still has forecast/history evidence, but its old DEB remains correctly suppressed because the historical same-as-of cohort cannot be reconstructed honestly.
- D+0 bucket probabilities are conditioned on the observed daily maximum: after Shanghai reached 39°C, all 32–38°C settlement buckets are zero and the remaining mass is normalized to 1.
- The legacy `/api/bot/start` scanner path is retired with HTTP 410; it can no longer bypass v3 audit, strategy, and execution controls.
- Current auditable decisions are visible but all are blocked/skip; no paper or live order was created.
- Scheduler is stopped, auto simulation is off, paper validation is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Fresh-cohort DEB now works, but same-date numeric parity against independent PolyWX evidence still needs to be measured before paper admission.
- Authoritative WU/HKO settlement evidence remains insufficient for ROI claims, and PWS entitlement is unavailable.
- Layer 8 still needs an atomic single-order transaction, active-cohort start locking, final idempotency reservation, and aggregate risk budgeting.

## Next Task
- Capture same-date Shanghai/Chicago PolyWX evidence and run the field-level Forecast/METAR/Historical/DEB/bucket benchmark. Do not start a soak, paper cohort, or live path until it passes.

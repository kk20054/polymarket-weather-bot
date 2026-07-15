# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-15. Phase 2/3 data-source alignment with Layer 7/8 fail-closed hardening.
- Observation, PolyWX comparison, and controlled simulation are usable; unattended paper validation and live trading are not ready.
- Forecast/DEB rows now require an auditable same-as-of source cohort; legacy rows without that contract are hidden from DEB, buckets, signals, and execution.
- Shanghai/Chicago 2026-07-15 were freshly collected and rebuilt with an accepted Weather.com v3 + Open-Meteo cohort. The dashboard now separates raw model center from the observed-floor trading center.
- Shanghai displays model `36.60°C ± 1.71°C` versus PolyWX `37.09°C ± 1.67°C`; its trading center is separately shown as `38.50°C` after the observed 39°C floor.
- Chicago model center is `35.18°C` versus PolyWX `35.79°C` (delta `-0.62°C`), so the same-date DEB parity gate is not fully passed.
- Shanghai 2026-07-14 still has forecast/history evidence, but its old DEB remains correctly suppressed because the historical same-as-of cohort cannot be reconstructed honestly.
- D+0 bucket probabilities are now read from the authoritative backend contract and conditioned on the observed daily maximum: after Shanghai reached 39°C, all 32–38°C settlement buckets are zero and the remaining mass is normalized to 1.
- Legacy 2026-07-14 rows now render as `该日期预测不可审计`, not as a transient DEB read failure.
- The legacy `/api/bot/start` scanner path is retired with HTTP 410; it can no longer bypass v3 audit, strategy, and execution controls.
- Current auditable decisions are visible but all are blocked/skip; no paper or live order was created.
- Scheduler is stopped, auto simulation is off, paper validation is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Same-date DEB parity is acceptable for Shanghai but Chicago remains `0.62°C` outside the `0.5°C` model-center target.
- Shanghai D+0 Open-Meteo components are close to the 18-hour cohort age limit; a fresh full-local-day archive/stitching contract is still needed.
- Authoritative WU/HKO settlement evidence remains insufficient for ROI claims, and PWS entitlement is unavailable.
- Layer 8 still needs an atomic single-order transaction, active-cohort start locking, final idempotency reservation, and aggregate risk budgeting.

## Next Task
- Build an audited D+0 full-local-day Open-Meteo archive/stitching path, then rerun Shanghai/Chicago DEB parity. Do not start a soak, paper cohort, or live path until both cities pass.

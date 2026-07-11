# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2 data-source stabilization; settlement-rule verification and Layer 2 truth coverage are complete for all 14 enabled cities, with Hong Kong intentionally retained as a mismatch.
- The scheduler completed about nine uninterrupted hours and passed the preliminary development soak. This is not a 14-30 day paper-production proof.
- The system is usable for monitored collection, truth-delta research, and controlled paper analysis. It is not ready for unattended paper settlement or live trading.
- `LIVE_TRADING=false`; the scheduler is stopped after the soak.

## Latest Ledger Summaries
- 2026-07-11 / Bias audit: the trainer now uses HKO/WU exact truth before IEM, enforces a pre-local-day forecast cutoff, and records real corrected MAE/RMSE. Across 14 cities, no model has the required 20 independent forecast dates, so runtime bias remains disabled. Commit `bbe104b`.
- 2026-07-11 / Settlement verification: seven provisional Asian cities were verified against active Gamma rules. All matched their configured stations; Singapore WSSS parsing was fixed and incomplete contracts can no longer become verified. Commit `1e90c5c`.
- 2026-07-11 / Truth backfill: WU 13 cities x 30 days, IEM 14 cities x 30 days, and HKO 30 days all completed without missing days. WU-IEM overlap is 390 days; HKO-VHHH overlap is 30 days. Commit `26d887f`.
- 2026-07-11 / Scheduler soak: about nine hours completed. METAR 102 cycles, forecast 8, Gamma 90, derive 20; transient single-city warnings recovered. PWS remains HTTP 401 due API entitlement.
- 2026-07-11 / Truth audit: HKO-IEM deltas and Hong Kong VHHH observation mapping were corrected. Commits `cc90a90`, `649e2d8`.

## Production Blockers
- Settlement coverage is 14/14, but Hong Kong remains a deliberate HKO/VHHH settlement mismatch and must stay paper-only.
- PWS v2 returns HTTP 401 with the current key, so peak-lock PWS coverage is not production-ready.
- Saved PolyWX benchmarks still show material Forecast/Cloud/DEB differences.
- The 30-day truth window is complete, but no city/model has 20 leakage-free independent forecast dates; Open-Meteo Previous Runs backfill is required before bias can affect DEB.
- Derive cycles can exceed the 15-minute interval; the last soak cycle took about 17.7 minutes.
- Paper orders still lack a complete automatic settlement, realized PnL, win-rate, Brier-score, and replay-validation lifecycle.
- Live dry-run, balance, duplicate-order, and canary gates are not accepted for production use.

## Next Step
- Backfill at least 20 independent Open-Meteo Previous Runs dates per enabled city/model, then retrain the audited bias table.
- Resolve or explicitly disable the unavailable PWS entitlement path.
- Rebuild saved PolyWX Forecast/Cloud/DEB benchmarks after source provenance is stable.
- Implement paper settlement/scoring, then run 14-30 validated days before any live canary.

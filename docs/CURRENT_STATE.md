# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2 data-source stabilization; Layer 2 truth coverage is now complete for the fixed 30-day validation window.
- The scheduler completed about nine uninterrupted hours and passed the preliminary development soak. This is not a 14-30 day paper-production proof.
- The system is usable for monitored collection, truth-delta research, and controlled paper analysis. It is not ready for unattended paper settlement or live trading.
- `LIVE_TRADING=false`; the scheduler is stopped after the soak.

## Latest Ledger Summaries
- 2026-07-11 / Truth backfill: WU 13 cities x 30 days, IEM 14 cities x 30 days, and HKO 30 days all completed without missing days. WU-IEM overlap is 390 days; HKO-VHHH overlap is 30 days. Commit `26d887f`.
- 2026-07-11 / Scheduler soak: about nine hours completed. METAR 102 cycles, forecast 8, Gamma 90, derive 20; transient single-city warnings recovered. PWS remains HTTP 401 due API entitlement.
- 2026-07-11 / Truth audit: HKO-IEM deltas and Hong Kong VHHH observation mapping were corrected. Commits `cc90a90`, `649e2d8`.
- 2026-07-10 / Source health: settlement verification and the 13-source health matrix were restored. Commits `d58ae12`, `8eda988`.
- 2026-07-10 / Scheduler capacity: SQLite/CLOB work was batched and duplicate derive refresh removed. Commit `9f1a04f`.

## Production Blockers
- Settlement contracts cover 7/14 enabled cities; seven Asian cities remain provisional, while Hong Kong remains a deliberate settlement mismatch/paper-only case.
- PWS v2 returns HTTP 401 with the current key, so peak-lock PWS coverage is not production-ready.
- Saved PolyWX benchmarks still show material Forecast/Cloud/DEB differences.
- Derive cycles can exceed the 15-minute interval; the last soak cycle took about 17.7 minutes.
- Paper orders still lack a complete automatic settlement, realized PnL, win-rate, Brier-score, and replay-validation lifecycle.
- Live dry-run, balance, duplicate-order, and canary gates are not accepted for production use.

## Next Step
- Verify settlement contracts for the seven provisional cities without auto-correcting station mappings.
- Use the completed WU/HKO/IEM truth window to train and audit forecast bias by city/model.
- Resolve or explicitly disable the unavailable PWS entitlement path.
- Rebuild saved PolyWX Forecast/Cloud/DEB benchmarks after source provenance is stable.
- Implement paper settlement/scoring, then run 14-30 validated days before any live canary.

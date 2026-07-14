# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-14. Phase 2/3 data-source alignment and Phase 7/8 fail-closed hardening are active.
- A controlled 14-city refresh rebuilt D+0/D+1 markets, forecasts, hourly evidence, DEB predictions and revision-bound decisions without starting the scheduler.
- D+0 and D+1 each have 14 active weather events and 154 strictly matched buckets with CLOB snapshots; 28 DEBs and 341 latest-build decisions were generated.
- A production-only SQLite mismatch was fixed: `forecast_runs(snapshot_key)` now has a full unique index compatible with `ON CONFLICT(snapshot_key)`.
- China Live now isolates per-city failures: Hong Kong succeeded while Shanghai's upstream HTTP 502 was recorded without aborting the batch.
- Paper execution rejects missing, stale or materially future quote timestamps. The legacy live executor is structurally blocked even if configuration is accidentally enabled.
- Layer 7 now refuses cross-date/cross-city fallback, does not label historical fallback as WU Historical, and hides edge/action styling for stale or invalid quotes.
- All 341 Python tests and the frontend production build pass. The scheduler/backend remain stopped, paper validation is inactive and `LIVE_TRADING=false`.
- Runtime METAR/orderbooks became stale after the controlled refresh because continuous scheduling stayed off; readiness therefore remains `code_only`.
- Profitability is not proven. No gate may be relaxed to manufacture recommendations or paper orders.

## Latest Ledger Summaries
- 2026-07-14 / Controlled Layer 2-6 refresh: 14-city D+0/D+1 data and decision chains rebuilt; current data later became stale with scheduler intentionally stopped.
- 2026-07-14 / Execution and verifier hardening: quote time fails closed, live submit is structurally locked, CLOB epoch timestamps and terminal prices are verified correctly.
- 2026-07-14 / Layer 7 truthfulness: exact-date rendering, real WU Historical provenance and stale quote display now fail closed.
- 2026-07-14 / Layer 3/4 forecast time contract: availability validation, immutable snapshots, source-bound DEBs and quarantine migration are complete.
- 2026-07-13 / Layer 2/4 storage keys: canonical METAR and hourly-consensus identities removed duplicates and reject ambiguous writes.

## Production Blockers
- METAR and Polymarket orderbooks are stale while the scheduler/backend are stopped.
- Sixteen high-weight forecast components still lack seven independent days of eligible calibration evidence.
- Wunderground PWS entitlement is missing; PWS and peak-lock remain explicitly unavailable.
- Shanghai China Live upstream returned HTTP 502 during the controlled refresh and needs a later source retry/fallback review.
- Authoritative WU/HKO truth and revision-bound resolved paper outcomes remain insufficient for ROI/Brier claims.
- No 14-30 day paper cohort is active; the existing 60 paper records are legacy and not valid evidence.
- The live executor remains intentionally non-production and still needs idempotency reservation plus aggregate risk budgeting before canary review.
- Browser acceptance for Shanghai/Chicago, both themes, console state and narrow-width overflow is still pending.
- Twenty-five display-only cities still require source smoke tests and settlement-contract admission before collection or paper use.
- Information-edge exits remain disabled until SELL fills and historical orderbook replay exist.

## Next Step
- Commit and push this controlled refresh, safety and Layer 7 truthfulness batch.
- Start backend/Vite only for an operator browser acceptance pass; do not start the scheduler automatically.
- After UI acceptance, run one short controlled fresh-data cycle and rerun observation/paper verification immediately.
- Start a 14-30 day revision-bound paper cohort only after fresh executable-book checks pass.
- Keep live locked until authoritative paper evidence and the live-executor rewrite both pass.

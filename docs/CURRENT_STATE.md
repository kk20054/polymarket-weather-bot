# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-14. Phase 2/3 data-source alignment is active; Layer 3/4 temporal integrity is now fail-closed.
- Online forecasts use actual `retrieved_at`; only trusted archives may use model `run_at`. Same-hour snapshots and DEB cutoffs are immutable and minute-accurate.
- Production history remains intact: 44,523 forecast rows are preserved, 5,405 temporally invalid rows are quarantined, and 1,133 of 1,776 legacy DEBs are invalidated rather than deleted.
- The current valid set is 643 DEBs; all 14 enabled cities retain a latest valid prediction. The verifier reports `prediction_math=pass` and `temporal_no_leak=pass`.
- The dashboard backend and scheduler are stopped, paper validation is inactive, and `LIVE_TRADING=false`. Runtime sources and orderbooks are stale, so the system remains `code_only`, not paper-ready or live-ready.
- Profitability is not proven. No gate may be relaxed to manufacture recommendations or paper orders.

## Latest Ledger Summaries
- 2026-07-14 / Layer 3/4 forecast time contract: centralized availability validation, immutable snapshots, source-bound DEBs, quarantine migration and test-database isolation are complete.
- 2026-07-13 / Layer 2/4 storage keys: canonical METAR and hourly-consensus identities removed 40/2 duplicates and now reject ambiguous writes.
- 2026-07-13 / Cross-layer verifier: observation, paper, evidence and live-canary readiness became machine gates; live canary remains blocked.
- 2026-07-13 / Settings and API UX: the dashboard settings drawer supports masked local credentials and provider-level connection tests without exposing secrets.
- 2026-07-13 / Layer 6/8 controls: strategy revisions, bankroll-aware Kelly sizing and revision-bound paper batches are implemented; no fresh eligible cohort is active.

## Production Blockers
- METAR, Open-Meteo, Weather.com v3, orderbook, consensus and signal decisions are stale while the scheduler/backend are stopped.
- Sixteen high-weight forecast components still lack seven independent days of eligible calibration evidence.
- All 22 currently matched market buckets fail fresh executable-orderbook checks.
- The active strategy revision has no fresh 14-city decision batch and no current paper candidate.
- Wunderground PWS entitlement is missing; PWS and peak-lock remain explicitly unavailable.
- Authoritative WU/HKO truth and revision-bound resolved paper outcomes are insufficient for ROI/Brier claims.
- The legacy live executor lacks pre-submit idempotency reservation, aggregate risk budgeting and revision-bound routing.
- Layer 7 still needs fail-closed stale-quote/probability/date rendering fixes and an operator browser acceptance pass.
- Twenty-five display-only cities still require source smoke tests and settlement-contract admission before collection or paper use.
- Information-edge exits remain disabled until SELL fills and historical orderbook replay exist.

## Next Step
- Run one controlled upstream refresh after the backend is deliberately started; do not auto-start the scheduler.
- Rebuild current D+0/D+1 DEB, buckets and revision-bound decisions for all 14 enabled cities, then rerun the observation/paper verifier.
- Fix the remaining Layer 7 fail-open rendering issues and complete Shanghai/Chicago browser QA.
- Start a 14-30 day paper cohort only after operator acceptance and fresh executable-book checks pass.
- Keep live locked until authoritative paper evidence and the live-executor rewrite both pass.

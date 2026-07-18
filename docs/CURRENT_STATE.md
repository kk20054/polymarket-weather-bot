# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-18. Phase 2/3 data-source and probability-contract validation.
- Polymarket temperature cards are real matched event buckets. Each card carries market/token identity, the latest bid/ask, model settlement probability, decision gates, and a direct event link.
- The bucket probability path no longer applies the observed-high floor twice. Intraday ensemble sigma now decays from the prediction issue time; Shanghai 2026-07-18 moved from an implausibly broad tail to 37C 87.8%, 38C 11.6%, 39C 0.6%.
- Fresh quotes may become paper candidates; stale quotes remain visible as `old book / old gap` and cannot become executable candidates. Complete-set cost is shown separately from directional model edge.
- Scheduler is stopped, auto simulation is off, the paper cohort is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Core runtime data is stale while the scheduler is stopped: METAR, CLOB orderbooks, hourly consensus, and most signal decisions need a controlled refresh before paper execution.
- High-weight Weather.com/NWP components still lack sufficient no-leak WU/HKO truth calibration; Shanghai currently has only 3 independent settlement days and PWS is unavailable.
- Paper evidence is not established: no revision-bound v2 paper orders, no authoritative settled cohort, and the live executor is not production-ready.

## Next Task
- Restore fresh orderbooks and source observations under the controlled scheduler, then accumulate calibrated WU/HKO settlements before starting the 14-30 day paper cohort. Do not unlock live trading.

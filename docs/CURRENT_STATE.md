# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-18. Phase 2/3 data-source and probability-contract validation.
- Physical runtime data has moved to `D:\WeatherBot\data`; the project `data/` path is a Junction, so existing code paths remain valid.
- All 51 registered cities have current METAR and forecast inputs. Nine China/Hong Kong cities have China Live; 49 cities have active Polymarket events, valid DEB rows, strictly matched market buckets, and persisted signal decisions.
- Jakarta and Lagos currently have no active Polymarket temperature event, so they remain observation-only. PWS remains unavailable without an entitled WU PWS key.
- Scheduler is stopped, auto simulation is off, the paper cohort is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Current candidate buckets are blocked mainly by insufficient independent truth samples and thin low-price orderbooks; gates must not be relaxed merely to display signals.
- Denver has a KDEN/KBKF settlement mismatch and Hong Kong uses HKO rather than VHHH; live remains blocked for both. Istanbul uses NOAA rules rather than WU history.
- Paper evidence is not established: no authoritative 14-30 day settled cohort, and the live executor is not production-ready.

## Next Task
- Run a controlled scheduler smoke and begin the 14-30 day paper cohort only after source freshness remains stable; do not unlock live trading.

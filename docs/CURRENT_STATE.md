# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-12. Phase 2/3 data-source alignment and Layer 7 operator review are active.
- Weather.com v3 now drives the dashboard Forecast/Cloud series; Open-Meteo models remain separate DEB inputs.
- Shanghai China Live uses Pudong station `101020600`; WU same-day Historical is incrementally collectable.
- Scheduler validation was explicitly cancelled and the scheduler is stopped. Paper validation remains inactive.
- `LIVE_TRADING=false`; the system is research/paper infrastructure, not a proven profitable production bot.

## Latest Ledger Summaries
- 2026-07-12 / Layer 7 hierarchy and honest freshness: the left city index and recommendation strip were compacted to the PolyWX hierarchy; the middle panel now owns one native source-status row, date controls and five tabs. Tooltip time is a forced `date + HH:mm`, the adaptive Y axis is verified, source ages come from source rows rather than fetch logs, and advanced diagnostics are lazy-loaded.
- 2026-07-12 / Active-market refresh cadence: Forecast and WU Historical now run on a fixed 10-minute start-to-start cadence for active markets; non-active enabled cities retain a 30-minute baseline. A controlled WU cycle completed 14/14 cities in 61.4 seconds with no failures; continuous scheduler remains stopped.
- 2026-07-12 / Forecast-DEB field closure: Weather.com condition, precipitation probability, revision count and retrieval time now survive Layer 4 into the dashboard. Shanghai DEB includes v3/GFS/JMA/ECMWF/ICON/GEM and benchmarks at `30.10+/-1.51C` versus PolyWX `29.88+/-1.62C` at the sampled time.
- 2026-07-12 / Three-city live benchmark: Shanghai and Chicago Forecast/Cloud are close to PolyWX, Tokyo past-hour Forecast is about 2C low while METAR/Historical agree. Chart hover labels now show `YYYY/MM/DD HH:mm` instead of raw minute indexes.
- 2026-07-12 / Tokyo station-location repair: RJTT coordinates were corrected from the Narita-area `35.7647,140.3864` to AWC RJTT/HND `35.553,139.781`. Wrong-location forecast snapshots and bias rows are now excluded; Tokyo DEB moved to `28.97+/-1.39C` versus PolyWX `28.90+/-1.37C`.
- 2026-07-12 / Honest evidence badges: city-page Forecast/METAR/Historical status reads native source series rather than stale aggregate cards. DEB observed floor now displays the actual METAR high and METAR sample count.
- 2026-07-11 / Source-role repair (`84ab4f0`): split Weather.com, NWP, WU Historical, METAR, China Live and PWS pollers. PWS now requires an independent WU key and no longer floods 401 with the forecast key.
- 2026-07-11 / Native-series dashboard: `/api/hourly-consensus` exposes native-frequency source series. Shanghai smoke returned Forecast 24, METAR 36, WU Historical 36, and Pudong China Live rows.
- 2026-07-11 / Nonblocking scheduler (`446cc22`, `a4b8385`, `f7de6c1`): source-health, registry reads and fetch-log writes run off the event loop; the erroneous 40-second v3 timeout was removed.
- 2026-07-11 / Derived batch repair (`1fe8a79`): readiness is refreshed once per 14-city batch instead of once per stage/date/city. A real D+0/D+1 batch completed 14/14 in 324.5 seconds with no timeout.
- 2026-07-11 / DEB v3 repair: default mode is `polywx_aligned`; latest Shanghai build includes v3 and five available families, with traceable weights and truth basis.
- 2026-07-11 / Layer 7 cleanup: the Hourly chart labels and provenance are explicit; Forecast cloud comes only from the same Weather.com v3 snapshot.
- 2026-07-11 / Historical workbench repair: all 14 enabled cities now have a WU Historical display path; Shanghai has 44 rows, Chicago 9, and Hong Kong 46 VHHH display-only rows. METAR and WU tables consume native-frequency rows.
- 2026-07-11 / Time-axis and diff repair: the main chart uses local minutes 0-1439 instead of a categorical axis, and Diff Stats now renders residual bars plus cumulative mean for METAR or Historical.
- 2026-07-11 / Prior foundation: settlement verification, source-health-v2, paper cohort controls and authoritative Gamma settlement remain intact.

## Production Blockers
- Independent WU PWS entitlement is missing; PWS series and peak-lock are unavailable.
- Two-hour/six-hour scheduler validation is intentionally deferred until operator UI and numeric benchmark acceptance.
- Shanghai same-date Forecast/Cloud/DEB benchmark is within the current target tolerance; Chicago still needs same-local-day Historical and final numeric comparison after its local day begins.
- Tokyo pre-fix past-hour forecasts remain intentionally blank because their archived snapshots used the wrong location; correct history will accumulate from the repair onward.
- The 10-minute Forecast/WU cadence has unit and single-cycle evidence but still needs operator-controlled runtime observation after the dashboard is accepted.
- WU/HKO truth coverage and resolved paper outcomes are insufficient for profitability claims.
- China Live has no retrospective weather.com.cn minute archive; points before collector activation remain honestly absent.
- Layer 7 still requires operator visual acceptance before starting the 14-30 day cohort.
- The live PolyWX sample and local benchmark still disagree on Shanghai peak hour (`14:00` vs `16:00`); this needs a computation/source audit, not a cosmetic override.
- Live dry-run/canary gates remain unaccepted and intentionally locked.

## Next Step
- Complete remaining Forecast/Cloud/DEB numeric benchmark rows against saved/current PolyWX evidence.
- Obtain an entitled WU PWS key or keep PWS explicitly disabled.
- After operator acceptance, start the explicit 14-30 day paper cohort; keep live locked.

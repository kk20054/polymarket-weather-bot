# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2/3 data-source alignment and Layer 7 operator review are active.
- Weather.com v3 now drives the dashboard Forecast/Cloud series; Open-Meteo models remain separate DEB inputs.
- Shanghai China Live uses Pudong station `101020600`; WU same-day Historical is incrementally collectable.
- Scheduler validation was explicitly cancelled and the scheduler is stopped. Paper validation remains inactive.
- `LIVE_TRADING=false`; the system is research/paper infrastructure, not a proven profitable production bot.

## Latest Ledger Summaries
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
- PolyWX numeric benchmark still needs same-date Shanghai/Chicago Forecast, Cloud, Historical and DEB comparison.
- WU/HKO truth coverage and resolved paper outcomes are insufficient for profitability claims.
- China Live has no retrospective weather.com.cn minute archive; points before collector activation remain honestly absent.
- Layer 7 still requires operator visual acceptance before starting the 14-30 day cohort.
- Live dry-run/canary gates remain unaccepted and intentionally locked.

## Next Step
- Complete remaining Forecast/Cloud/DEB numeric benchmark rows against saved/current PolyWX evidence.
- Obtain an entitled WU PWS key or keep PWS explicitly disabled.
- After operator acceptance, start the explicit 14-30 day paper cohort; keep live locked.

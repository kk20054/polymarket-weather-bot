# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2/3 data-source alignment and Layer 7 operator review are active.
- Weather.com v3 now drives the dashboard Forecast/Cloud series; Open-Meteo models remain separate DEB inputs.
- Shanghai China Live uses Pudong station `101020600`; WU same-day Historical is incrementally collectable.
- Scheduler is stopped after the implementation smoke test. Paper validation remains inactive.
- `LIVE_TRADING=false`; the system is research/paper infrastructure, not a proven profitable production bot.

## Latest Ledger Summaries
- 2026-07-11 / Source-role repair (`84ab4f0`): split Weather.com, NWP, WU Historical, METAR, China Live and PWS pollers. PWS now requires an independent WU key and no longer floods 401 with the forecast key.
- 2026-07-11 / Native-series dashboard: `/api/hourly-consensus` exposes native-frequency source series. Shanghai smoke returned Forecast 24, METAR 36, WU Historical 36, and Pudong China Live rows.
- 2026-07-11 / Nonblocking status (`446cc22`): source-health scans now run off the FastAPI event loop; scheduler status is an in-memory read and the original soak clock was reset.
- 2026-07-11 / DEB v3 repair: default mode is `polywx_aligned`; latest Shanghai build includes v3 and five available families, with traceable weights and truth basis.
- 2026-07-11 / Layer 7 cleanup: the Hourly chart labels and provenance are explicit; Forecast cloud comes only from the same Weather.com v3 snapshot.
- 2026-07-11 / Prior foundation: settlement verification, source-health-v2, paper cohort controls and authoritative Gamma settlement remain intact.

## Production Blockers
- Independent WU PWS entitlement is missing; PWS series and peak-lock are unavailable.
- The required two-hour smoke and six-hour scheduler stability run have not yet been completed.
- PolyWX numeric benchmark still needs same-date Shanghai/Chicago Forecast, Cloud, Historical and DEB comparison.
- WU/HKO truth coverage and resolved paper outcomes are insufficient for profitability claims.
- Derive duration under full-city load needs remeasurement after the poller split.
- Layer 7 still requires operator visual acceptance before starting the 14-30 day cohort.
- Live dry-run/canary gates remain unaccepted and intentionally locked.

## Next Step
- Run a controlled scheduler smoke and verify each split poller, source freshness and no repeated PWS 401.
- Complete browser QA for Shanghai and Chicago against saved/current PolyWX evidence.
- After operator acceptance, start the explicit 14-30 day paper cohort; keep live locked.

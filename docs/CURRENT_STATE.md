# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-23. Phase 3/6 leakage-free calibration and controlled paper validation; observation and paper operation are available, live trading is not.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`; `LIVE_TRADING=false`. A native desktop launcher at `D:\WeatherBot\Launcher\WeatherBotLauncher.exe` now performs idempotent backend/frontend health checks, explicitly starts the scheduler, and opens the dashboard; backend startup itself remains lightweight.
- Active revision `spr_2c5694b368eb394cf07d1bdc67dcd35b` runs only `core_modal_v1`: top-two modal buckets, 8% minimum effective edge, 15% fractional Kelly, 5% bankroll cap, authoritative truth, model agreement, liquidity and order-minimum gates.
- A stale 31-member GFS run could previously shadow a fresh deterministic GFS run and leave US cities below the four-family gate. Source selection now keeps candidates in the newest 12-hour cohort before preferring ensemble members; fresh Chicago/Shanghai DEB rows again use GFS.
- Leakage-safe D0/D+1 replay across 13 cohort cities and 2026-07-18..22 produced 113 valid cases from 130 requests: top-1 accuracy 27.43%, top-2 45.13%, multiclass Brier 0.6968, and zero historically executable trades. D0 top-2 was 50.82% versus D+1 38.46%; this supports continued paper study, not a profitability claim.
- Weather.com v3 raw forecasts were current, but the persisted bias table had stopped at 2026-07-20 because no scheduler path retrained it. The historical poller now refreshes the leakage-free bias artifact roughly daily; a real 2026-07-23 retrain advanced supported-city v3 pairs through 2026-07-22. V3 remains below the 20-pair runtime threshold (4-9 pairs per city), so it is collecting rather than receiving mature dynamic weight.
- Paper entry strategies are now single-select. Core modal and low-price tail can overlap at 10-15 cents, while single-bucket and ladder policies can target the same token; combination mode remains blocked until a tested portfolio-level precedence and exposure allocator exists.
- A paper-only `model_guarded_take_profit` exit mode is available for the next cohort. It sells only at a fresh executable best bid with full depth after a 15-minute hold and concurrent 5%, $0.05 and one-tick profit thresholds; model and observed-breach protection remain active. The current cohort keeps its immutable prior exit policy.
- Active paper cohort `paper-20260721T094730Z-7705f78f` has `$40` bankroll, `$2` trade cap, `$6` daily cap, three orders/day and five open positions. Project-level revision-bound evidence currently contains 11 v2 paper orders and 10 authoritative settlements with realized PnL `-$8.43`; model Brier `0.043725` is worse than market Brier `0.003645`, so profitability is not demonstrated.
- Dashboard city browsing now groups correctly by continent, timezone or alphabet. Forecast/observation tables use compact cloud, precipitation and wind marks; sticky headers now contain those marks during table scrolling in both themes. METAR/WU pressure is normalized to hPa, bias pairing follows the PolyWX nearest-local-hour contract, and fetch logs are scoped and sorted for the selected city.

## Production Blockers
- Evidence: the replay has only four independent dates and no executable historical trades; positive net ROI/CLV and calibrated probability have not been demonstrated.
- Paper outcome: current realized evidence is negative and the model has not beaten market probability calibration; strategy thresholds must not be relaxed merely to increase order count.
- Inputs: Weather.com v3 now refreshes calibration automatically but remains below 20 leakage-free pairs per city and stays zero-weight while collecting; WU/HKO coverage and settlement contracts still block live qualification in part of the registry.
- Execution quality: current decisions are mainly rejected for cross-model spread, market spread, order minimum or insufficient edge. These gates must not be weakened solely to manufacture trades.
- Frontend quality: the production bundle is about 895 KB before gzip and should later be split, but this is a performance follow-up rather than a signal-quality blocker.

## Next Task
- Keep the scheduler and current cohort running. Start a separate future cohort with `model_guarded_take_profit` only after the current cohort is deliberately stopped, then compare realized PnL, missed settlement value and drawdown against hold/model-guarded cohorts. Do not enable live trading.

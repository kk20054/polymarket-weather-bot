# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-22. Phase 3/6 leakage-free calibration and controlled paper validation; observation and paper operation are available, live trading is not.
- Runtime data remains under the project `data/` Junction to `D:\WeatherBot\data`. Backend `8765`, frontend `5173`, and the scheduler are running; `LIVE_TRADING=false`.
- Active revision `spr_2c5694b368eb394cf07d1bdc67dcd35b` runs only `core_modal_v1`: top-two modal buckets, 8% minimum effective edge, 15% fractional Kelly, 5% bankroll cap, authoritative truth, model agreement, liquidity and order-minimum gates.
- A stale 31-member GFS run could previously shadow a fresh deterministic GFS run and leave US cities below the four-family gate. Source selection now keeps candidates in the newest 12-hour cohort before preferring ensemble members; fresh Chicago/Shanghai DEB rows again use GFS.
- Leakage-safe D0/D+1 replay across 13 cohort cities and 2026-07-18..21 produced 95 valid cases from 104 requests: top-1 accuracy 26.32%, top-2 47.37%, multiclass Brier 0.6771, and zero historically executable trades. D0 top-2 was 51.92% versus D+1 41.86%; this supports continued paper study, not a profitability claim.
- Active paper cohort `paper-20260721T094730Z-7705f78f` has `$40` bankroll, `$2` trade cap, `$6` daily cap, three orders/day and five open positions. Its apply-mode tick is healthy and currently has no fresh executable candidate.
- Dashboard payload now keeps full time-series detail only for the selected city, reducing the response from about 2.8 MB / 6-9 seconds to about 0.7 MB / 1-4 seconds under scheduler load. Browser QA shows no connection spinner or visible error; five evidence tabs and the paper workbench render together without the former notification pile-up.

## Production Blockers
- Evidence: the replay has only four independent dates and no executable historical trades; positive net ROI/CLV and calibrated probability have not been demonstrated.
- Inputs: Weather.com v3 remains below 20 leakage-free pairs per city and stays zero-weight while collecting; WU/HKO coverage and settlement contracts still block live qualification in part of the registry.
- Execution quality: current decisions are mainly rejected for cross-model spread, market spread, order minimum or insufficient edge. These gates must not be weakened solely to manufacture trades.
- Frontend quality: the production bundle is about 895 KB before gzip and should later be split, but this is a performance follow-up rather than a signal-quality blocker.

## Next Task
- Keep the scheduler and current cohort running; after several independent settlements, review ROI, Brier, CLV and gate counterfactuals by city before changing thresholds. Do not enable live trading.

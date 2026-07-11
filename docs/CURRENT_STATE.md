# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-11. Phase 2 data-source stabilization; settlement-rule verification and Layer 2 truth coverage are complete for all 14 enabled cities, with Hong Kong intentionally retained as a mismatch.
- The scheduler completed about nine uninterrupted hours and passed the preliminary development soak. This is not a 14-30 day paper-production proof.
- The system is usable for monitored collection, truth-delta research, controlled paper execution, and authoritative paper settlement. It is not yet validated for unattended paper trading or live trading.
- `LIVE_TRADING=false`; the scheduler is stopped after the soak.

## Latest Ledger Summaries
- 2026-07-11 / Paper settlement: new v1 paper orders can move from provisional WU/HKO truth to authoritative closed Gamma outcomes, atomically realizing PnL and recording win rate, model/market Brier scores. The 60 legacy rows remain explicitly unscorable. Commit `4245bf4`.
- 2026-07-11 / PolyWX replay: leakage-safe replay now cuts off future truth and forecast snapshots. It also fixed a P0 where display-only historical data could raise the DEB observed floor. Chicago 07-02 DEB mu is now within 0.13F of saved PolyWX. Commits `02ee52e`, `193422d`.
- 2026-07-11 / Previous Runs calibration: 14 cities received 30 fixed T+24 archive dates for their regional primary models plus ICON/GEM coverage. Bias retraining now has 69 runtime-eligible city/model rows, and DEB consumes the weighted correction. Commit `937a203`.
- 2026-07-11 / Bias audit baseline: the trainer now uses HKO/WU exact truth before IEM, enforces a pre-local-day forecast cutoff, and records real corrected MAE/RMSE. This pre-backfill baseline was later superseded by the 69 mature rows above. Commit `bbe104b`.
- 2026-07-11 / Settlement verification: seven provisional Asian cities were verified against active Gamma rules. All matched their configured stations; Singapore WSSS parsing was fixed and incomplete contracts can no longer become verified. Commit `1e90c5c`.
- 2026-07-11 / Truth backfill: WU 13 cities x 30 days, IEM 14 cities x 30 days, and HKO 30 days all completed without missing days. WU-IEM overlap is 390 days; HKO-VHHH overlap is 30 days. Commit `26d887f`.
- 2026-07-11 / Scheduler soak: about nine hours completed. METAR 102 cycles, forecast 8, Gamma 90, derive 20; transient single-city warnings recovered. PWS remains HTTP 401 due API entitlement.
- 2026-07-11 / Truth audit: HKO-IEM deltas and Hong Kong VHHH observation mapping were corrected. Commits `cc90a90`, `649e2d8`.

## Production Blockers
- Settlement coverage is 14/14, but Hong Kong remains a deliberate HKO/VHHH settlement mismatch and must stay paper-only.
- PWS v2 returns HTTP 401 with the current key, so peak-lock PWS coverage is not production-ready.
- Leakage-safe saved PolyWX replay still shows material Cloud differences (20.67-46.36pp), Forecast MAE of 1.61-1.88F for Chicago and 2.11C for Shanghai, and a 3.09F Chicago 07-04 DEB mu gap.
- The 30-day truth and primary Previous Runs windows are complete; 69 city/model rows are mature. Weather.com v3 has no historical archive and NBM remains below the 20-day calibration gate.
- Derive cycles can exceed the 15-minute interval; the last soak cycle took about 17.7 minutes.
- The settlement/scoring code is complete, but the 60 historical legacy paper rows have no decision/token/city/date identity chain and cannot be scored; no new v1 orders have resolved yet, so production PnL evidence is still empty.
- Live dry-run, balance, duplicate-order, and canary gates are not accepted for production use.

## Next Step
- Start a controlled Layer 9 paper cohort using only new `paper-execution-v1` orders, keep scheduler and auto-simulation explicitly controlled, and collect 14-30 days of authoritative settlements.
- Resolve or explicitly disable the unavailable PWS entitlement path.
- Revisit Cloud/DEB source parity while paper scoring quantifies whether the differences help or hurt calibration; do not discuss a live canary before the validation gate passes.

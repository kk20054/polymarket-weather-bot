# WeatherBot Current State

## Current Layer
- Date: 2026-09-05. Scope: Layer 8 execution integrity and its Layer 7 dashboard consumer.
- Production DB: `D:\WeatherBot\data\weatherbot_v3.db` (about 60GB), exposed through the repository `data` Junction.
- Local backend/frontend are running on `8765/5173`; backend restarted at `2026-09-05T19:35:45+08:00`, worker PID `3156`. Scheduler remains stopped by explicit operator control.
- Latest persisted decision is `2026-08-16T23:34:58.935698+00:00`; the current display is historical, not a fresh September trading session.
- `LIVE_TRADING=false`; the real order adapter is incomplete. No account or trade was created, and no risk threshold, model weight, or frozen protocol was changed in this audit. The existing expiry check marked the elapsed cohort completed.

## Latest Evidence
- Latest cohort `paper-20260809T165308Z-bbfb1434` ended on `2026-08-23T16:53:08.702407+00:00`; it is completed, not active.
- That cohort has 13 orders: 6 settled (1 win, 5 losses; realized PnL `-$2.2857`), 5 open (3 filled, 2 partial), and 2 rejected. Open-order marks are stale and are not final results.
- From August 14 through the last available August 17 decision: 49 cities, 1,712 decisions, 52 gate-passing rows across 14 cities. Repeated candidate rows are not distinct fills.
- Primary blocks: spread 566, D+0 window 473, absent bid 276, insufficient edge 214, absent ask 70, risk budget below exchange minimum 27, no qualified top bucket 14, minimum size over trade cap 12, observed-high elimination 5, tail bucket 3.
- Selected revision `spr_13639230b1b3e97631aec4cf3f811749`: paper edge/effective edge 5%, ask minimum 0.05, stale book 300s, spread 500bps, Kelly 0.25, bankroll fraction cap 12.5%; the ended account also capped trades at $2 and daily spend at $10.
- Dynamic weighting uses first-pair inverse-MAE shrinkage with station/model/location-version/lead calibration. Primary prior excludes GEM/JMA; they remain diagnostic sources. Manual override remains available.
- IMPORTANT: shared revision/data/model does not mean identical eligibility. `base.py` and `core_modal.py` still have separate live maturity, sizing and spread policy. Earlier documentation claiming identical thresholds was inaccurate; this audit did not weaken either path.

## Repairs And Checks
- Ended/stopped accounts retain their run ID and order history in the API/UI without resuming execution or creating a new account.
- An explicit YES-token lookup cannot fall back to another outcome's book. Single-order and fill persistence is transactional, including rollback and retry.
- Exit logic uses the latest snapshot even when its bid is missing, rejects invalid/future quotes, and counts distinct decision-linked predictions rather than unrelated forecast refreshes. Old exit-version counters do not carry into the corrected confirmation rule.
- Dashboard now supports raw decimal weight editing, actionable buy failure messages, bought/pending states, order-state filtering, unfilled-order semantics and elapsed-time quote freshness. Stale/expired edge values are suppressed.
- Verification: 28 focused execution/history tests plus 14 exit tests passed; frontend TypeScript/Vite build passed. Edge browser checks covered 13 historical orders, 5 open/2 unfilled filters, decimal input, mobile modal, failed buy and bought state. No test order was written to production.
- Public frontend remains Vercel `https://www.polywxx.org`, with a read-only gateway to local FastAPI via `api.polywxx.org`. This turn changed local code only; the public deployment was not updated or independently revalidated.

## Remaining Blockers
- Profitability is not established: this cohort's settled result is negative. Recorded frozen forward CLV was also negative; no historical PnL/model-selection experiment was rerun.
- Five expired-market positions remain open in the recorded ledger and need authoritative settlement reconciliation before any account-performance conclusion.
- No fresh scheduler cycle or new real-data fill was observed in this audit; UI interaction used isolated execution responses, not production writes.
- Truth entitlement/maturity, the 60GB SQLite store, the laptop-dependent public API and incomplete live execution remain operational limitations. No data was deleted.

## Next Task
- Reconcile the five pending positions against authoritative settlement, then explicitly choose and start the next account/scheduler session. Measure current execution reasons and fills on the shared selected revision; do not silently resume an ended cohort or reopen historical model-selection experiments.

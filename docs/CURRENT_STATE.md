# WeatherBot Current State

## Current Layer
- Date: 2026-09-05. Scope: predictive-error field contract and its runtime/UI consumers; dashboard transport and navigation performance.
- Production DB: `D:\WeatherBot\data\weatherbot_v3.db` (about 60GB), exposed through the repository `data` Junction.
- Local backend/frontend are running on `8765/5173`; backend restarted at `2026-09-05T20:59:49+08:00`, worker PID `11136`. Scheduler remains stopped by explicit operator control.
- Latest persisted decision is `2026-08-16T23:34:58.935698+00:00`; the current display is historical, not a fresh September trading session.
- `LIVE_TRADING=false`; the real order adapter is incomplete. No account or trade was created, and no risk threshold, model weight, or frozen protocol was changed in this audit. The existing expiry check marked the elapsed cohort completed.

## Latest Evidence
- Latest cohort `paper-20260809T165308Z-bbfb1434` ended on `2026-08-23T16:53:08.702407+00:00`; it is completed, not active.
- Latest ledger has 13 orders: 11 settled (2 wins, 9 losses; realized PnL `-$4.9111`), zero open, and 2 rejected. Five settlement rows were updated at `2026-09-05T20:04:46..49+08:00`, before this turn's changes; do not reuse the prior 6-settled partial result.
- From August 14 through the last available August 17 decision: 49 cities, 1,712 decisions, 52 gate-passing rows across 14 cities. Repeated candidate rows are not distinct fills.
- Primary blocks: spread 566, D+0 window 473, absent bid 276, insufficient edge 214, absent ask 70, risk budget below exchange minimum 27, no qualified top bucket 14, minimum size over trade cap 12, observed-high elimination 5, tail bucket 3.
- Selected revision `spr_13639230b1b3e97631aec4cf3f811749`: paper edge/effective edge 5%, ask minimum 0.05, stale book 300s, spread 500bps, Kelly 0.25, bankroll fraction cap 12.5%; the ended account also capped trades at $2 and daily spend at $10.
- Dynamic weighting uses first-pair inverse-MAE shrinkage with station/model/location-version/lead calibration. Primary prior excludes GEM/JMA; they remain diagnostic sources. Manual override remains available.
- Audit found 75 primary-family single-pair calibration cells with zero fitted MAE, 67 with nonzero bias. Fitted zero is not predictive accuracy. Scoring now shares runtime shrinkage/capping, scores the first pair with zero prior correction, and uses only priors available before forecast issuance.
- Predictive MAE requires `runtime-bias-walk-forward-v2` evidence; legacy scores no longer drive weights or residual spread. Bias-table regeneration was not run. The next scheduled calibration refresh creates the new contract; old snapshots remain unchanged. Truth availability conservatively uses stored `updated_at` and local-day completion, not a reconstructed publication history.
- IMPORTANT: shared revision/data/model does not mean identical eligibility. `base.py` and `core_modal.py` still have separate live maturity, sizing and spread policy. Earlier documentation claiming identical thresholds was inaccurate; this audit did not weaken either path.

## Repairs And Checks
- Ended/stopped accounts retain their run ID and order history in the API/UI without resuming execution or creating a new account.
- An explicit YES-token lookup cannot fall back to another outcome's book. Single-order and fill persistence is transactional, including rollback and retry.
- Exit logic uses the latest snapshot even when its bid is missing, rejects invalid/future quotes, and counts distinct decision-linked predictions rather than unrelated forecast refreshes. Old exit-version counters do not carry into the corrected confirmation rule.
- Dashboard now supports raw decimal weight editing, actionable buy failure messages, bought/pending states, order-state filtering, unfilled-order semantics and elapsed-time quote freshness. Stale/expired edge values are suppressed.
- Dashboard order JSON is 153,874 bytes instead of 2,407,770 (93.6% less), retaining 13 orders, PnL and 120 equity points. Full evidence remains available without `compact=true`; SQLite reads leave the API event loop. Settings load on demand; idle polling slows; leaving a city cancels its read requests.
- UI preserves navigation/account during city loading, provides a mobile city selector, and shows settlement win rate separately from realized PnL. Model Analysis distinguishes historical versus forward MAE with count/method tooltips.
- Verification: 8 API tests and 16 calibration/weight/integration tests passed; frontend build passed. Edge checks at 1600x1000 and 390x844 covered compact orders, uninterrupted city switching, request cancellation, lazy settings and mobile selection; no production order was written.
- Public frontend remains Vercel `https://www.polywxx.org`, with a read-only gateway to local FastAPI via `api.polywxx.org`. This turn changed local code only; the public deployment was not updated or independently revalidated.

## Remaining Blockers
- Profitability is not established: this cohort's settled result is negative. Recorded frozen forward CLV was also negative; no historical PnL/model-selection experiment was rerun.
- More pairs may improve bias estimation; they do not prove improved resolution, win rate, or profit. Inverse-MAE blending remains a heuristic, not direct optimization against the market.
- No fresh scheduler cycle or new real-data fill was observed in this audit; UI interaction used isolated execution responses, not production writes.
- Truth entitlement/maturity, the 60GB SQLite store, the laptop-dependent public API and incomplete live execution remain operational limitations. No data was deleted.

## Next Task
- Explicitly choose and start the next account/scheduler session, confirm its calibration refresh emits the versioned predictive errors, then assess forward probability quality and execution results separately. Do not silently resume an ended cohort or reopen historical model-selection experiments.

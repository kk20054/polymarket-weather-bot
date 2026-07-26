# WeatherBot Current State

## Current Layer
- Date: 2026-07-27. Phase 3/6: frozen forward observation.
- Backend and scheduler are running against `D:\WeatherBot\data\weatherbot_v3.db`.
- Paper validation was stopped at `2026-07-26T17:58:32Z`; the v2 statistics cohort continues to observe without placing orders.
- `LIVE_TRADING=false` by default; profitability is not proven.

## Latest Evidence
- Production fix commit `7c47df4` is deployed.
- Preregistration v1 is void: 88.5% of pre-close quotes were already at the two probability endpoints, so its "CLV" had degenerated into unit P&L.
- Frozen v2 uses `decision + 6h`, capped at predicted peak minus 1h. Historical availability is 76.2%, ask-change SD is 0.2216, and the fixed target is N=338.
- The v2 primary cohort is model-side `0.20<=ask<0.40` and `edge>=8%`; `paper_allowed` is an immutable analysis stratum, not an enrollment gate.
- Historical first-candidate rate is 28.17/day model-side versus 0.50/day gate-pass. Expected model-side evaluation date is 2026-08-12; a gate-pass-only route would extend to about 2028-12-30.
- Runtime has enrolled 16 immutable v2 candidates: 0 paper-allowed and 16 blocked.
- The first valid anchor is São Paulo 2026-07-26: entry ask `0.38`, anchor ask `0.34`, observed CLV `-0.04`. This is one observation, not an evaluation.
- Current blocker counts are `d0_outside_peak_decision_window=12`, `core_no_qualified_top_bucket=3`, and `spread_too_wide=1`.
- Targeted forward-validation/scheduler tests passed (5/5), the frontend build passed, and no risk threshold or model weight changed.

## Production Blockers
- CLV N is only 1 versus the frozen target N=338.
- The current executable stratum remains empty; this is tracked separately and does not suppress the model-quality cohort.
- The selected anchor is outcome-independent but was earlier than the realized daily peak in only 61.0% of historical auditable cases; this frozen limitation must remain visible in the final interpretation.

## Next Task
- Pure observation only until the fixed checkpoint at `2026-07-28T15:30:00Z`.
- At the checkpoint, report model-side candidates, ask/lead strata, `paper_allowed` count, rate change, and all gate blockers.
- Stop at N=338 and evaluate once. Do not edit v2, create v3, or reopen historical model-selection/P&L loops.

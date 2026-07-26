# WeatherBot Current State

## Current Layer
- Date: 2026-07-26. Phase 3/6: forward-only paper validation.
- Scheduler and dashboard are running; protocol start is `2026-07-26T15:30:00Z`.
- Runtime DB is `D:\WeatherBot\data\weatherbot_v3.db`.
- `LIVE_TRADING=false`; profitability is not proven.

## Latest Evidence
- Commits `047884f` and `a0dc50e` are deployed.
- Twenty newly reconstructed events all have 11 positive bucket probabilities summing to 1.
- Since protocol start, 196/196 predictions use V3 as the only positive cold-start weight; GEM/JMA remain diagnostic at zero weight.
- 105 decisions were written; 98 contain decision-time ask and all 105 contain quote age.
- Twelve pre-close quote rows were written and all include `market_close_at`.
- Fifteen rows meet the model-side `20-40c + edge>=8%` filter, but zero currently pass all existing paper execution gates.
- Main blockers are genuine wide spread, absent bid/ask side, D+0 timing window and insufficient positive edge; thresholds were not lowered.
- D+0 scheduling coverage is 49/49 cities: 15-minute derive gives four ticks per one-hour peak window; cross-midnight Asian windows are fixed.
- Frozen preregistration: `docs/FORWARD_VALIDATION_PREREGISTRATION_CN.md`.
- Primary cohort: V3, `0.20<=ask<0.40`, `edge>=8%`, existing `paper_allowed`; target N=73, expected evaluation 2026-08-03.

## Production Blockers
- The preregistered cohort has zero executable candidates so far; this is not yet a 48-hour rate result.
- Historical rows before deployment cannot provide rigorous immutable decision-to-preclose CLV.
- Several markets are genuinely one-sided or too wide for the unchanged execution gates.

## Next Task
- At `2026-07-28T15:30:00Z`, report the real 48-hour candidate count, ask/lead distribution and rate change.
- Continue to N=73, then evaluate frozen CLV, Brier/log loss, paper P&L and H-A/H-B exactly once.
- Do not reopen historical model-selection/P&L loops or alter the preregistered main hypothesis.

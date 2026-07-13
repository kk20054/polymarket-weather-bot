# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-13. Phase 2/3 data-source alignment, Layer 7 operator review and Layer 8 paper validation are active.
- The dashboard has 51 display profiles; 14 collector-enabled cities retain independent Forecast, METAR, WU Historical, China Live and optional PWS roles.
- Paper simulation now uses the operator cohort bankroll for Kelly sizing, one explicit cap chain, current stored orderbook freshness and authoritative Polymarket settlement.
- Every new decision, paper cohort and paper order is bound to an immutable strategy revision; the active conservative profile is revision 2.
- The normal dashboard now opens developer settings as a right-side drawer with the same PolyWX light/dark theme. The drawer groups overview, strategy/risk, immutable revisions and read-only system status; `/developer` remains a deep-link fallback.
- Developer settings now includes a read-only data-source page backed by source-health-v2: 13 source roles, 14-city coverage, freshness, required blockers and credential-presence booleans are visible without exposing secrets.
- Layer 6 batch rebuilding now selects all enabled stations and the newest overlapping prediction/market dates; dry-run no longer writes readiness state.
- Scheduler is stopped and paper validation is inactive after verification. `LIVE_TRADING=false`; profitability is not proven.

## Latest Ledger Summaries
- 2026-07-13 / Layer 7/8 settings UX: replaced the isolated developer form with a themed settings drawer; publishing and activation are separate, activation requires confirmation, and live remains read-only locked.
- 2026-07-13 / Layer 6 and data-source controls: fixed oldest-first/legacy-five-city signal targeting and added a safe source-health page to developer settings.
- 2026-07-13 / Layer 8 sizing and strategy audit: cohort bankroll now recomputes Kelly; global `MAX_BET` no longer silently truncates a higher cohort limit; ladder groups reserve three order/position slots.
- 2026-07-13 / Immutable strategy profiles: append-only revisions and activation events bind signal generation and paper defaults; decisions/orders persist parameter and sizing snapshots.
- 2026-07-13 / Developer boundary: `/developer` publishes confirmed local revisions and shows read-only system state; normal UI hides maintenance diagnostics and exposes no secret/live toggle.
- 2026-07-12 / Scheduler containment: heavy pollers are serialized/staggered; controlled collector cycles stayed below 87.3MB RSS and scheduler remains stopped after the test (`1d63438`).

## Production Blockers
- Twenty-five display-only cities still need source smoke tests and settlement-contract probes before collector or paper admission.
- Exact PolyWX private recommendation logic is not public; local weather focus is an auditable approximation, not a copied buy signal.
- Independent WU PWS entitlement is missing; PWS series and peak-lock remain unavailable.
- WU/HKO truth coverage and resolved paper outcomes are insufficient for profitability claims.
- Current Shanghai revision-2 decisions are all paper-blocked by actual gates; do not relax gates merely to manufacture orders.
- Revision 2 currently covers only the last Shanghai build; weather, orderbook and derived sources are stale while the scheduler is stopped, so a fresh 14-city rebuild has intentionally not been run yet.
- Information-edge exits remain disabled until SELL fills and historical orderbook replay are implemented.
- Correct-location Tokyo forecast history and same-local-day Chicago Historical benchmark still need additional archive density.
- Collector-level timeout/residual-worker reporting and a controlled runtime observation remain before unattended scheduling.
- Live dry-run/canary gates remain unaccepted and intentionally locked.

## Next Step
- Run a controlled upstream refresh, then rebuild revision-2 signal decisions across all 14 enabled cities and publish a blocked-reason/candidate report.
- Run a browser/operator acceptance pass on normal and `/developer` pages, then start the explicit 14-30 day paper cohort only if accepted.
- Admit new cities one at a time through source and settlement probes; keep catalog-only cities disabled meanwhile.
- Continue PolyWX numeric benchmarks and obtain an entitled PWS key or keep PWS explicitly disabled.

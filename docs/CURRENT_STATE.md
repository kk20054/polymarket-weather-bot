# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-13. Phase 2/3 data-source alignment, Layer 7 operator review and Layer 8 paper validation are active.
- The dashboard has 51 display profiles; 14 collector-enabled cities retain independent Forecast, METAR, WU Historical, China Live and optional PWS roles.
- Paper simulation now uses the operator cohort bankroll for Kelly sizing, one explicit cap chain, current stored orderbook freshness and authoritative Polymarket settlement.
- Every new decision, paper cohort and paper order is bound to an immutable strategy revision; the active conservative profile is revision 2.
- `/developer` owns strategy thresholds and read-only system status. The normal dashboard only shows bankroll, strategy selection, orders, market links and the active revision.
- Scheduler is stopped and paper validation is inactive after verification. `LIVE_TRADING=false`; profitability is not proven.

## Latest Ledger Summaries
- 2026-07-13 / Layer 8 sizing and strategy audit: cohort bankroll now recomputes Kelly; global `MAX_BET` no longer silently truncates a higher cohort limit; ladder groups reserve three order/position slots.
- 2026-07-13 / Immutable strategy profiles: append-only revisions and activation events bind signal generation and paper defaults; decisions/orders persist parameter and sizing snapshots.
- 2026-07-13 / Developer boundary: `/developer` publishes confirmed local revisions and shows read-only system state; normal UI hides maintenance diagnostics and exposes no secret/live toggle.
- 2026-07-12 / Scheduler containment: heavy pollers are serialized/staggered; controlled collector cycles stayed below 87.3MB RSS and scheduler remains stopped after the test (`1d63438`).
- 2026-07-12 / City and recommendation contracts: 51-city display catalog is separate from the 14-city collector watchlist; weather focus cards remain separate from auditable trade candidates (`ac968a2`).

## Production Blockers
- Twenty-five display-only cities still need source smoke tests and settlement-contract probes before collector or paper admission.
- Exact PolyWX private recommendation logic is not public; local weather focus is an auditable approximation, not a copied buy signal.
- Independent WU PWS entitlement is missing; PWS series and peak-lock remain unavailable.
- WU/HKO truth coverage and resolved paper outcomes are insufficient for profitability claims.
- Current Shanghai revision-2 decisions are all paper-blocked by actual gates; do not relax gates merely to manufacture orders.
- Information-edge exits remain disabled until SELL fills and historical orderbook replay are implemented.
- Correct-location Tokyo forecast history and same-local-day Chicago Historical benchmark still need additional archive density.
- Collector-level timeout/residual-worker reporting and a controlled runtime observation remain before unattended scheduling.
- Live dry-run/canary gates remain unaccepted and intentionally locked.

## Next Step
- Rebuild revision-2 signal decisions across all 14 enabled cities and publish a blocked-reason/candidate report.
- Run a browser/operator acceptance pass on normal and `/developer` pages, then start the explicit 14-30 day paper cohort only if accepted.
- Admit new cities one at a time through source and settlement probes; keep catalog-only cities disabled meanwhile.
- Continue PolyWX numeric benchmarks and obtain an entitled PWS key or keep PWS explicitly disabled.

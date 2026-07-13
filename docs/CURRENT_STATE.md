# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-13. Phase 2/3 data-source alignment, Layer 7 operator review and Layer 8 paper validation are active.
- The dashboard has 51 display profiles; 14 collector-enabled cities retain independent Forecast, METAR, WU Historical, China Live and optional PWS roles.
- Paper simulation now uses the operator cohort bankroll for Kelly sizing, one explicit cap chain, current stored orderbook freshness and authoritative Polymarket settlement.
- Every new decision, paper cohort and paper order is bound to an immutable strategy revision; the active conservative profile is revision 2.
- The normal dashboard now opens developer settings as a right-side drawer with the same PolyWX light/dark theme. The drawer groups overview, strategy/risk, immutable revisions and read-only system status; `/developer` remains a deep-link fallback.
- Developer settings now includes a Chinese-first API configuration page: Weather.com, Wunderground PWS and optional MiniMax/Visual Crossing/Feishu credentials can be saved locally, masked and connection-tested; source-health-v2 remains available under a collapsed advanced diagnostic section.
- A read-only five-agent verifier now reports separate `observation / paper / paper_evidence / live_canary` readiness and exits nonzero when the requested stage is blocked. The quick gate is `python -m weatherbot_v3.cli project-verify --verification-mode observation`; `--deep-verification` adds full SQLite integrity scanning.
- The canary endpoint now forces dry-run regardless of environment flags. Bulk paper execution requires the exact strategy revision and decision `issued_at` batch shown in the workbench; out-of-range Kelly probabilities return zero sizing.
- Layer 6 batch rebuilding now selects all enabled stations and the newest overlapping prediction/market dates; dry-run no longer writes readiness state.
- Scheduler is stopped and paper validation is inactive after verification. `LIVE_TRADING=false`; profitability is not proven.

## Latest Ledger Summaries
- 2026-07-13 / Layer 7/8 settings UX: replaced the isolated developer form with a themed settings drawer; publishing and activation are separate, activation requires confirmation, and live remains read-only locked.
- 2026-07-13 / API settings UX: replaced credential-presence labels with local editable masked inputs, provider-specific connection tests and Chinese purpose/error text; Weather.com real connectivity passed and Wunderground PWS remains honestly unconfigured.
- 2026-07-13 / Cross-layer verification: data/model, decision/risk, paper/settlement and operator-surface agents became a machine gate; the real database is currently `code_only`, not observation-ready.
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
- The verifier found 40 duplicate historical METAR keys, 2 duplicate hourly-consensus keys, 3,285 training runs failing lead-time/no-leak checks, and 959 DEB rows with invalid source timing/identity. These must be repaired before evidence can train production probabilities.
- All 176 current/future `matched` bucket rows fail fresh executable-book checks; no paper execution may use them until CLOB refresh and revalidation.
- Live executor remains explicitly `live-execution-v1-legacy`: aggregate risk budgets, revision-bound live routing and pre-submit idempotency reservation are not implemented, so live canary stays blocked even after paper evidence improves.
- Information-edge exits remain disabled until SELL fills and historical orderbook replay are implemented.
- Correct-location Tokyo forecast history and same-local-day Chicago Historical benchmark still need additional archive density.
- Collector-level timeout/residual-worker reporting and a controlled runtime observation remain before unattended scheduling.
- Live dry-run/canary gates remain unaccepted and intentionally locked.

## Next Step
- First repair temporal leakage/duplicate storage keys and add migration regression tests; do not train or build evidence from contaminated history.
- Then run a controlled upstream refresh, rebuild revision-2 decisions across all 14 enabled cities and rerun the observation/paper verifier before starting a cohort.
- Run a browser/operator acceptance pass on normal and `/developer` pages, then start the explicit 14-30 day paper cohort only if accepted.
- Admit new cities one at a time through source and settlement probes; keep catalog-only cities disabled meanwhile.
- Continue PolyWX numeric benchmarks and obtain an entitled PWS key or keep PWS explicitly disabled.

# WeatherBot Current State

## Current Layer
- Date: 2026-08-09. Phase 3/6: forward paper validation and production hardening.
- Production DB: `D:\WeatherBot\data\weatherbot_v3.db` (about 50GB).
- Backend and scheduler are running; dashboard is available locally on port 5173.
- `LIVE_TRADING=false`; current strategy is not approved for live or canary use.

## Latest Evidence
- Core coverage is complete for 49 enabled cities: METAR, Open-Meteo, Weather.com v3, hourly consensus, market buckets, and signal decisions are present.
- Three-day funnel (Aug 7-9): 147/147 city-date predictions have components, >=4 model families, and model spread; 1,617 buckets are strict matched.
- Latest decisions have zero `paper_allowed`; six eligible decisions appeared during the window.
- Current blocks are quote-side absence/staleness, D+0 timing, spread, price/effective-edge, and exchange minimums. Risk thresholds were not relaxed.
- Active paper run has 4 order records, 2 resolved losses, 0 wins, realized PnL `-$2.31`, and cash `$37.69`.
- Frozen forward validation has 338 enrolled / 212 anchored. Mean CLV is `-3.28pp`, 95% CI `[-5.47pp, -1.10pp]`.
- D+0 CLV is `-5.41pp` (N=124); D+1 is `-0.30pp` (N=88). Neither supports live trading.
- WU daily/hourly truth covers 48/49 cities but is generally below 30 days; Istanbul is missing. IEM daily coverage is 27.1%.
- PWS remains optional and unavailable for production use: one stale city and 48 missing due to entitlement/configuration.
- A production orderbook query bug excluded current markets after old history exceeded the row limit. The repaired cycle refreshed 1,067 books with 0 missing and restored 49/49 orderbook health.
- Paper candidate loading now filters in SQL by freshness, run cities, strategy revision, and `paper_allowed`; scheduler logs retain exact skip reasons and timing.
- Dashboard forward-validation data now refreshes per request instead of serving a stale cached result.

## Production Blockers
- Current strategy has negative forward CLV and 0/2 settled paper results; profitability requirement fails.
- WU truth history is not mature across all cities, IEM coverage is partial, and PWS is not entitled.
- Recent collector errors still mark aggregate source health blocked even though current orderbooks are healthy.
- SQLite size and single-writer contention remain operational risks.

## Next Task
- Let the current cohort expire on 2026-08-10; do not start another unchanged cohort.
- Keep thresholds frozen. Diagnose probability resolution and pre-register any D+1-only strategy revision before testing it.
- Move execution quotes to Polymarket batch/WebSocket market data before any live canary.
- Use `audits/strategy-health-2026-08-09/README.md` for the full evidence and repair record.

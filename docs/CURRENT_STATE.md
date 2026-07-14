# WeatherBot Current State

## Current Phase And Usability
- Date: 2026-07-14. The project remains in Phase 2/3 data-source alignment with Layer 7/8 fail-closed hardening.
- Recent-date Wunderground Historical is repaired: Shanghai 2026-07-14 has 46 native rows and Chicago has 9; both the chart and statistics now consume the native WU series.
- Weather.com v3 DEB reconstruction now keeps forecast revisions and takes the latest snapshot per local valid hour. Shanghai v3 daily high changed from the invalid partial-run value 30.56C to 35.0C.
- Shanghai DEB is now 35.50C +/- 1.62C with a 36.0C observed floor. PolyWX showed 35.18C +/- 1.54C; the remaining mean difference is explainable by WeatherBot's observed-floor safety contract.
- The diagnostic Gaussian chart uses 18 fixed-width bins. Layer 6 market buckets for `polywx_aligned_deb_v1` now use Gaussian CDF integration instead of treating six model-family means as empirical ensemble members.
- Shanghai's 11 market buckets now form a continuous distribution with a 24.2% central peak rather than model-weight spikes.
- Python regression tests (267) and the frontend production build pass. Shanghai and Chicago browser QA show native Historical data, continuous Gaussian bars, no console errors and no page-level horizontal overflow.
- Scheduler is stopped, auto simulation is off, paper validation is inactive and `LIVE_TRADING=false`. Profitability and production readiness are not proven.

## Latest Ledger Summaries
- 2026-07-14 / Layers 2/3/4/6/7: repaired WU Historical writes and native display, v3 revision-aware daily reconstruction, Gaussian visualization and Layer 6 bucket math.
- 2026-07-14 / Layer 8: bound every non-dry-run paper order to an active cohort, immutable strategy revision, fresh executable quote and shared Kelly/risk ledger.
- 2026-07-14 / Layers 2-6: completed a controlled 14-city D+0/D+1 rebuild while keeping scheduler, paper cohort and live execution off.
- 2026-07-14 / Layers 3/4: enforced leakage-safe forecast availability and source contracts, quarantining invalid historical predictions without deleting raw rows.
- 2026-07-13 / Layer 7/8: moved developer parameters into the main dashboard settings drawer and separated save from activation.

## Production Blockers
- METAR and Polymarket orderbooks are stale while the scheduler is stopped.
- Forecast revision history that was never captured cannot be recreated after the fact; future comparisons require continuous collection.
- The PolyWX-style per-hour forecast revision popup is not implemented yet; WeatherBot currently exposes only revision counts while using revision history in calculations.
- Sixteen high-weight components still lack seven independent eligible calibration days.
- Wunderground PWS entitlement is missing; PWS and peak-lock remain unavailable.
- Authoritative WU/HKO resolved truth and revision-bound paper outcomes remain insufficient for ROI/Brier claims.
- No 14-30 day paper cohort is active; legacy paper records are not production evidence.
- The live executor still needs idempotency reservation and aggregate risk budgeting before any canary review.

## Next Step
- Refresh current METAR, model runs and executable orderbooks in one controlled cycle, then verify the same decision batch immediately.
- Add a read-only PolyWX-style forecast revision detail view without changing model math or execution gates.
- Start a 14-30 day revision-bound paper cohort only after fresh-source and fresh-book verification passes.
- Keep live locked until authoritative paper evidence and the live-executor rewrite both pass.

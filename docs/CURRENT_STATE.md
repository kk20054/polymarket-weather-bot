# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-18. Phase 2/3 data-source and probability-contract validation.
- Wellington is enabled and settlement-verified. Its local weather-focus card (`15.0C` now / `13.6C` model center / `1.36C` sigma) reproduces the current PolyWX focus (`15.0C` / `13.5C` / `1.39C`) without treating it as a buy signal.
- The dashboard refreshes its presentation cache and focus cards on API reads. The scheduler is running; auto simulation is off, the paper cohort is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Wellington WU hourly history returns HTTP 400, so its Historical series is not yet at PolyWX parity.
- Shanghai weather.com.cn is unavailable; China Live now falls back to explicitly labeled Weather.com v3 current conditions, which restores freshness but is not source-identical to PolyWX.
- PWS remains unauthorized and trading performance is unproven; Wellington decisions are correctly paper-blocked by insufficient bias samples and wide spreads.

## Next Task
- Repair WU Historical coverage for newly enabled cities, then bind current DEB and immutable orderbook snapshots before starting the paper cohort.

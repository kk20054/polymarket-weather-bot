# WeatherBot Current State

## Phase And Usability
- Date: 2026-07-18. Phase 2/3 data-source and probability-contract validation.
- Wellington is enabled and settlement-verified. Its local weather-focus card (`15.0C` now / `13.6C` model center / `1.36C` sigma) reproduces the current PolyWX focus (`15.0C` / `13.5C` / `1.39C`) without treating it as a buy signal.
- Hourly bias statistics now use the verified PolyWX contract: exact `HH:00` forecast/observation pairs for delta and Pearson R, plus native-cadence Historical/METAR overlap. Remaining small differences reflect forecast snapshot timing, not a second formula.
- The dashboard refreshes its presentation cache and focus cards on API reads. The scheduler is running; auto simulation is off, the paper cohort is inactive, and `LIVE_TRADING=false`.

## Production Blockers
- Wellington WU hourly history returns HTTP 400, so its Historical series is not yet at PolyWX parity.
- Shanghai China Live now reads the PolyWX-aligned Pudong `101020600` feed through a browser-fingerprint HTTP client. Weather.com/ZSPD integer fallback rows are isolated from the red series; correct 5-minute history accumulates from 2026-07-18 15:05 local because earlier fallback snapshots cannot be reconstructed.
- PWS remains unauthorized and trading performance is unproven; Wellington decisions are correctly paper-blocked by insufficient bias samples and wide spreads.

## Next Task
- Repair WU Historical coverage for newly enabled cities, then bind current DEB and immutable orderbook snapshots before starting the paper cohort.

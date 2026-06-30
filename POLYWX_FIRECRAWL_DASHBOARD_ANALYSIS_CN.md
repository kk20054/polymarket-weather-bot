# PolyWX Firecrawl Dashboard Analysis

Date: 2026-06-30

This note records the PolyWX dashboard evidence collected through Firecrawl and turns it into WeatherBot implementation guidance. It is not a claim that PolyWX's trading logic is profitable; it is a product and data-workbench reference for WeatherBot.

## Firecrawl Evidence

Source URL:

- `https://www.polywx.xyz/?city=chicago-kord&date=2026-06-29`

Firecrawl tools used:

- `firecrawl_map`: returned the canonical PolyWX home page and public metadata.
- `firecrawl_scrape` with structured JSON schema: returned dashboard information architecture. The result marked `rendered=false`, so it should be treated as page metadata plus partial static/rendered extraction, not a complete browser render.
- `firecrawl_scrape` with `branding`: returned visual and component baseline.

Key metadata extracted:

- Title: `Weather Monitor - Hourly Forecast, METAR & Temperature Accuracy`.
- Description: tracks hourly weather forecasts, METAR aviation observations, historical temperature data, forecast accuracy, diff statistics, and daily maximum temperature predictions.
- Keywords include weather forecast, hourly temperature, METAR, weather monitor, temperature accuracy, weather dashboard, forecast comparison, daily max temperature, 天气预报, 逐小时气温, 气温监测.

## Product Structure To Borrow

PolyWX is organized as a city/date weather evidence workbench, not as a trading cockpit. The useful structure is:

- Top controls: city, continent, timezone/date context, sort or A-Z city browsing.
- Primary tabs: Forecast, METAR, Historical, Diff Stats, Fetch Log.
- Hourly Temperature module: 24-hour evidence timeline that compares forecast and observation.
- Daily Max Prediction module: daily high-temperature prediction view, useful for Polymarket weather buckets.
- Forecast detail table: time, temperature, cloud, precipitation, wind, condition, pressure, dew point, fetch timestamps.
- METAR observation table: aviation observation records, local time alignment, current and max-so-far signals.
- Historical table: previous observed highs and station history.
- Diff Stats: forecast error, average delta, correlation/accuracy indicators, overlap between historical and METAR.
- Fetch Log: source, status, duration, message, and acquisition audit trail.

WeatherBot should preserve this structure but add trading-specific layers:

- Polymarket event URL and market slug.
- YES token and orderbook snapshot.
- Bucket distribution and signal decision chain.
- Paper/live executor result.
- Risk gate and go-live readiness.

## Visual Structure To Borrow

Firecrawl branding extracted PolyWX as a compact weather monitor:

- Small typography: roughly 13px body and 15px section headings.
- Dense tables and low whitespace.
- System font stack.
- Minimal component decoration.
- Strong distinction between primary action, secondary tabs, and data tables.

The raw branding extraction saw the original page as dark themed, with colors around `#161A22`, `#1B212C`, `#222A37`, border `#2C3445`, secondary text `#7D8694`, and blue accent `#2563EB`. WeatherBot's active design constraint is now the project-level dual-theme contract in `AGENTS.md`:

- Light mode keeps white backgrounds, `#111827` primary text, and `border-gray-200` borders.
- Dark mode follows PolyWX's extracted dark palette: `#161A22` page, `#1B212C` panels, `#222A37` raised/input surfaces, `#2C3445` borders, `#CBD2DC` primary text, and `#7D8694` secondary text.
- Straight edges / `rounded-none`.
- No decorative gradients or dashboard chrome.

Therefore, WeatherBot should borrow PolyWX's density, information architecture, and dark-mode palette while preserving a clean light mode for daily use.

## WeatherBot Implementation Contract

The current WeatherBot dashboard should converge on this city page shape:

1. Left column
   - City search.
   - Continent filter.
   - City list with station, latest temperature, and signal count.
   - Recommendation/focus state.

2. Center column
   - Sticky city/date header.
   - Top city/date controls.
   - Hourly Temperature chart with 24 fixed hours.
   - Forecast, METAR, Historical, Diff Stats, Fetch Log tabs.
   - Daily Max Prediction / bucket distribution module.
   - Market signals and expandable evidence details below weather evidence.

3. Right column
   - Paper account state.
   - One-click simulation control.
   - Signal queue.
   - Paper/live trade records.
   - Live trading must stay locked unless canary gates are explicitly passed.

## Data Contract

Every city/date page should be backed by normalized evidence rows:

- `forecast_hourly`: city, station, target date, local hour, model, model run time, temperature, cloud, precipitation, wind, pressure, dew point, condition, fetched_at.
- `metar_hourly`: city, station, target date, local hour, observed temperature, observation timestamp, max_so_far, raw METAR, fetched_at.
- `historical_daily`: city, station, target date, actual high, provider, truth confidence, calibration tier, fetched_at.
- `forecast_diff`: city, station, target date, local hour or daily row, forecast, observed, delta, absolute error, provider pair, eligibility.
- `fetch_log`: source, city, station, target date, status, duration_ms, message, fetched_at.
- `market_bucket_distribution`: city, target date, bucket, probability, ask, bid, spread, edge, gate result.

The center chart should not infer missing hours by stretching existing data. It should show a fixed 24-hour skeleton and render missing data as gaps.

## Algorithm Implications

PolyWX's strongest lesson for WeatherBot is not UI styling; it is that weather trading needs a visible evidence chain:

- Forecast and observation must be aligned by station and local hour.
- Daily high prediction should be derived from the hourly curve plus station truth, not only a single model mean.
- Bias statistics need overlap counts, average delta, and correlation, otherwise they are decorative.
- Fetch logs are part of the trading system because stale or missing evidence must block signals.
- METAR is strong for D+0 current/high-so-far reasoning, but final production calibration still needs settlement-grade station truth.

## Next Engineering Steps

Near term:

- Ensure `/api/dashboard` always returns `weather_city_series` with 24-hour forecast/METAR skeleton rows per selected date.
- Add explicit `fetch_log` rows to the API instead of synthesizing them only from generic dashboard events.
- Split `WeatherPanel.tsx` into smaller components:
  - `CityWorkbenchHeader`.
  - `HourlyTemperatureChart`.
  - `WeatherEvidenceTabs`.
  - `DailyMaxPredictionPanel`.
  - `WeatherFetchLogTable`.
  - `TradingSignalEvidencePanel`.
- Keep paper/live execution controls in the right column; do not mix them into weather evidence tabs.

Mid term:

- Backfill station-level truth and forecast archives.
- Add forecast-vs-observed diff materialization in SQLite.
- Build bucket distributions from the complete daily high distribution.
- Make every BUY/SKIP decision expandable into evidence: forecast curve, METAR curve, historical bias, distribution, orderbook, and risk gate.

Done criteria for this layer:

- The page can answer: "why did the bot like or reject this bucket?" without opening logs.
- Missing evidence is visible and blocks action.
- The dashboard remains readable without explanation walls.
- The backend still starts lightweight and does not auto-run scans on page load.

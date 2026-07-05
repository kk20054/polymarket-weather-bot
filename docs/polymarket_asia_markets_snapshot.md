# Polymarket Asian Weather Markets Snapshot

- Generated at: `2026-07-05T09:47:57.979498+00:00`
- Scope: Gamma active event probe for confirmed Asian highest-temperature markets, Wunderground/HKO source reachability, WU-vs-METAR feasibility check, Open-Meteo source check, unit/timezone contract notes, and settlement/execution implications.
- CSV: `docs/polymarket_asia_markets_snapshot.csv`
- Open-Meteo sample JSON: `docs/open_meteo_asia_samples.json`

## 1. Asian active market inventory

Confirmed city set used: Shanghai/ZSPD, Beijing/ZBAA, Hong Kong/HKO rule with VHHH airport observation mismatch, Tokyo/RJTT, Seoul/RKSI, Taipei/RCSS, Wuhan/ZHHH, Qingdao/ZSQD, Shenzhen/ZGSZ, Singapore/WSSS. Taipei is RCSS Songshan, not RCTP Taoyuan.

|city|event_id|slug|date|station|source_type|volume24hr|open_interest|markets|source_url|
|---|---|---|---|---|---|---|---|---|---|
|Shanghai|661252|highest-temperature-in-shanghai-on-july-5-2026|2026-07-05|ZSPD|wunderground_airport_history|246841.578|122622.25|11|https://www.wunderground.com/history/daily/cn/shanghai/ZSPD|
|Shanghai|664853|highest-temperature-in-shanghai-on-july-6-2026|2026-07-06|ZSPD|wunderground_airport_history|21455.7|12827.098|11|https://www.wunderground.com/history/daily/cn/shanghai/ZSPD|
|Shanghai|668424|highest-temperature-in-shanghai-on-july-7-2026|2026-07-07|ZSPD|wunderground_airport_history|3510.95|3074.138|11|https://www.wunderground.com/history/daily/cn/shanghai/ZSPD|
|Beijing|661259|highest-temperature-in-beijing-on-july-5-2026|2026-07-05|ZBAA|wunderground_airport_history|111713.676|63426.59|11|https://www.wunderground.com/history/daily/cn/beijing/ZBAA|
|Beijing|664864|highest-temperature-in-beijing-on-july-6-2026|2026-07-06|ZBAA|wunderground_airport_history|13642.27|8304.479|11|https://www.wunderground.com/history/daily/cn/beijing/ZBAA|
|Beijing|668431|highest-temperature-in-beijing-on-july-7-2026|2026-07-07|ZBAA|wunderground_airport_history|2500.974|2195.177|11|https://www.wunderground.com/history/daily/cn/beijing/ZBAA|
|Hong Kong|657477|highest-temperature-in-hong-kong-on-july-4-2026|2026-07-04|HKO|hong_kong_observatory_daily_extract|23863.983|75398.459|11||
|Hong Kong|661251|highest-temperature-in-hong-kong-on-july-5-2026|2026-07-05|HKO|hong_kong_observatory_daily_extract|160753.575|57446.415|11||
|Hong Kong|664852|highest-temperature-in-hong-kong-on-july-6-2026|2026-07-06|HKO|hong_kong_observatory_daily_extract|26417.872|12637.806|11||
|Hong Kong|668423|highest-temperature-in-hong-kong-on-july-7-2026|2026-07-07|HKO|hong_kong_observatory_daily_extract|2811.852|2471.57|11||
|Tokyo|661250|highest-temperature-in-tokyo-on-july-5-2026|2026-07-05|RJTT|wunderground_airport_history|113359.493|68138.831|11|https://www.wunderground.com/history/daily/jp/tokyo/RJTT|
|Tokyo|664851|highest-temperature-in-tokyo-on-july-6-2026|2026-07-06|RJTT|wunderground_airport_history|31908.414|22806.705|11|https://www.wunderground.com/history/daily/jp/tokyo/RJTT|
|Tokyo|668422|highest-temperature-in-tokyo-on-july-7-2026|2026-07-07|RJTT|wunderground_airport_history|1606.083|1558.057|11|https://www.wunderground.com/history/daily/jp/tokyo/RJTT|
|Seoul|661244|highest-temperature-in-seoul-on-july-5-2026|2026-07-05|RKSI|wunderground_airport_history|142961.389|56569.327|11|https://www.wunderground.com/history/daily/kr/incheon/RKSI|
|Seoul|664849|highest-temperature-in-seoul-on-july-6-2026|2026-07-06|RKSI|wunderground_airport_history|12148.67|9597.871|11|https://www.wunderground.com/history/daily/kr/incheon/RKSI|
|Seoul|668417|highest-temperature-in-seoul-on-july-7-2026|2026-07-07|RKSI|wunderground_airport_history|3724.267|3197.724|11|https://www.wunderground.com/history/daily/kr/incheon/RKSI|
|Taipei|661257|highest-temperature-in-taipei-on-july-5-2026|2026-07-05|RCSS|wunderground_airport_history|67162.878|47068.587|11|https://www.wunderground.com/history/daily/tw/taipei/RCSS|
|Taipei|664860|highest-temperature-in-taipei-on-july-6-2026|2026-07-06|RCSS|wunderground_airport_history|15268.277|12865.585|11|https://www.wunderground.com/history/daily/tw/taipei/RCSS|
|Taipei|668429|highest-temperature-in-taipei-on-july-7-2026|2026-07-07|RCSS|wunderground_airport_history|12465.305|4657.672|11|https://www.wunderground.com/history/daily/tw/taipei/RCSS|
|Wuhan|661260|highest-temperature-in-wuhan-on-july-5-2026|2026-07-05|ZHHH|wunderground_airport_history|64849.255|36956.983|11|https://www.wunderground.com/history/daily/cn/wuhan/ZHHH|
|Wuhan|664865|highest-temperature-in-wuhan-on-july-6-2026|2026-07-06|ZHHH|wunderground_airport_history|19669.848|10251.648|11|https://www.wunderground.com/history/daily/cn/wuhan/ZHHH|
|Wuhan|668432|highest-temperature-in-wuhan-on-july-7-2026|2026-07-07|ZHHH|wunderground_airport_history|1438.458|1426.954|11|https://www.wunderground.com/history/daily/cn/wuhan/ZHHH|
|Qingdao|661272|highest-temperature-in-qingdao-on-july-5-2026|2026-07-05|ZSQD|wunderground_airport_history|38915.417|28912.78|11|https://www.wunderground.com/history/daily/cn/qingdao/ZSQD|
|Qingdao|664871|highest-temperature-in-qingdao-on-july-6-2026|2026-07-06|ZSQD|wunderground_airport_history|3686.777|3399.881|11|https://www.wunderground.com/history/daily/cn/qingdao/ZSQD|
|Qingdao|668443|highest-temperature-in-qingdao-on-july-7-2026|2026-07-07|ZSQD|wunderground_airport_history|2199.929|1992.788|11|https://www.wunderground.com/history/daily/cn/qingdao/ZSQD|
|Shenzhen|661262|highest-temperature-in-shenzhen-on-july-5-2026|2026-07-05|ZGSZ|wunderground_airport_history|66654.959|49713.519|11|https://www.wunderground.com/history/daily/cn/shenzhen/ZGSZ|
|Shenzhen|664867|highest-temperature-in-shenzhen-on-july-6-2026|2026-07-06|ZGSZ|wunderground_airport_history|21203.083|13060.775|11|https://www.wunderground.com/history/daily/cn/shenzhen/ZGSZ|
|Shenzhen|668434|highest-temperature-in-shenzhen-on-july-7-2026|2026-07-07|ZGSZ|wunderground_airport_history|1929.739|1398.055|11|https://www.wunderground.com/history/daily/cn/shenzhen/ZGSZ|
|Singapore|661253|highest-temperature-in-singapore-on-july-5-2026|2026-07-05|WSSS|wunderground_airport_history|68245.259|43696.008|11|https://www.wunderground.com/history/daily/sg/singapore/WSSS|
|Singapore|664854|highest-temperature-in-singapore-on-july-6-2026|2026-07-06|WSSS|wunderground_airport_history|8434.224|6430.506|11|https://www.wunderground.com/history/daily/sg/singapore/WSSS|
|Singapore|668425|highest-temperature-in-singapore-on-july-7-2026|2026-07-07|WSSS|wunderground_airport_history|1312.283|1235.929|11|https://www.wunderground.com/history/daily/sg/singapore/WSSS|

Bucket-level CSV rows: `341` rows. One row equals one event outcome bucket/market, with event and CLOB metadata repeated for auditability.

### Resolution URL reachability

|city|station|status|reachable|url|
|---|---|---|---|---|
|Beijing|ZBAA|200|True|https://www.wunderground.com/history/daily/cn/beijing/ZBAA|
|Qingdao|ZSQD|200|True|https://www.wunderground.com/history/daily/cn/qingdao/ZSQD|
|Seoul|RKSI|200|True|https://www.wunderground.com/history/daily/kr/incheon/RKSI|
|Shanghai|ZSPD|200|True|https://www.wunderground.com/history/daily/cn/shanghai/ZSPD|
|Shenzhen|ZGSZ|200|True|https://www.wunderground.com/history/daily/cn/shenzhen/ZGSZ|
|Singapore|WSSS|200|True|https://www.wunderground.com/history/daily/sg/singapore/WSSS|
|Taipei|RCSS|200|True|https://www.wunderground.com/history/daily/tw/taipei/RCSS|
|Tokyo|RJTT|200|True|https://www.wunderground.com/history/daily/jp/tokyo/RJTT|
|Wuhan|ZHHH|200|True|https://www.wunderground.com/history/daily/cn/wuhan/ZHHH|

Notes: Most Asian markets use Wunderground airport station history URLs. Hong Kong is different: Gamma rules point to the Hong Kong Observatory Daily Extract, not Wunderground, and should not be forced through WU airport history.

## 2. Wunderground vs AWC METAR / IEM ASOS feasibility: ZBAA 2026-06-15..2026-06-30

Result: Wunderground pages are reachable, but the static HTML for dated ZBAA pages exposes `Daily Observations` with `No Data Recorded`; Weather.com PWS history API returns empty arrays for stationId=ZBAA. The public AviationWeather `date=YYYYMMDD` probe returns a single report, not a full day sequence, so it is not sufficient to compute daily max. IEM ASOS can return the full station hourly/routine series and is the practical free METAR-sequence fallback.

|date|wu_page_status|wu_static_observation_state|wu_pws_daily_obs_count|wu_daily_high_c|awc_date_api_report_count|awc_date_api_max_temp_c|iem_asos_routine_count|iem_asos_local_day_max_c|delta_wu_minus_iem_c|
|---|---|---|---|---|---|---|---|---|---|
|2026-06-15|200|no_data_recorded|0|--|1|22|24|26.0|--|
|2026-06-16|200|no_data_recorded|0|--|1|22|24|25.0|--|
|2026-06-17|200|no_data_recorded|0|--|1|23|24|32.0|--|
|2026-06-18|200|no_data_recorded|0|--|1|25|24|29.0|--|
|2026-06-19|200|no_data_recorded|0|--|1|26|24|29.0|--|
|2026-06-20|200|no_data_recorded|0|--|1|22|24|30.0|--|
|2026-06-21|200|no_data_recorded|0|--|1|23|24|30.0|--|
|2026-06-22|200|no_data_recorded|0|--|1|23|24|31.0|--|
|2026-06-23|200|no_data_recorded|0|--|1|21|24|27.0|--|
|2026-06-24|200|no_data_recorded|0|--|1|23|24|30.0|--|
|2026-06-25|200|no_data_recorded|0|--|1|26|24|32.0|--|
|2026-06-26|200|no_data_recorded|0|--|1|26|24|34.0|--|
|2026-06-27|200|no_data_recorded|0|--|1|26|24|35.0|--|
|2026-06-28|200|no_data_recorded|0|--|1|27|24|34.0|--|
|2026-06-29|200|no_data_recorded|0|--|1|22|24|27.0|--|
|2026-06-30|200|no_data_recorded|--|--|1|22|24|30.0|--|

Conclusion: a dedicated Wunderground scraper is still needed if exact Polymarket settlement replication is required. AWC/IEM METAR max is useful for approximation and model calibration, but it should remain flagged `not_exact_wunderground_settlement_source` until daily WU max is captured. Preferred implementation path: try a browser-rendered WU scraper or licensed Weather.com/WU API; keep IEM ASOS as station truth fallback and compare deltas once WU daily max is available.

## 3. Open-Meteo source availability

|city|station|preferred_model|forecast_endpoint_ok|ensemble_member_fields|historical_forecast_2025_07_04_ok|previous_runs_recent_ok|timezone|
|---|---|---|---|---|---|---|---|
|Shanghai|ZSPD|cma_grapes_global|True|30|True|True|Asia/Shanghai|
|Beijing|ZBAA|cma_grapes_global|True|30|True|True|Asia/Shanghai|
|Hong Kong|VHHH|cma_grapes_global|True|30|True|True|Asia/Hong_Kong|
|Tokyo|RJTT|jma_seamless|True|30|True|True|Asia/Tokyo|
|Seoul|RKSI|jma_seamless|True|30|True|True|Asia/Seoul|
|Taipei|RCSS|jma_seamless|True|30|True|True|Asia/Taipei|
|Chicago|KORD|gfs_seamless|True|30|True|True|America/Chicago|

Operational read: `cma_grapes_global` works for China/Hong Kong, `jma_seamless` works for Japan/Korea/Taipei, `gfs_seamless` ensemble returns member fields, and Historical Forecast responds for 2025-07-04. Recent Previous Runs endpoint also responds, but lead-time walk-forward evaluation should explicitly persist `run_at` and valid-time horizons before using it for training.

Minimal sample JSON is saved in `docs/open_meteo_asia_samples.json`; it includes request URLs, hourly units, first rows, and ensemble member field count.

## 4. Unit / timezone / precision contract

- US markets: Fahrenheit primary, 1deg F bucket, local-day station max; use station timezone, not UTC calendar day.
- Asian markets: Celsius primary, integer deg C bucket in market labels; current WeatherBot C bucket test uses truncation-style `[N, N+1)` for exact integer buckets. Hong Kong rules mention HKO 0.1deg C source precision, so HKO bucket interpretation needs a dedicated contract parser check before live.
- Backend should persist SI-ish native values with explicit `unit` and `timezone`; UI must render by `city_config`/market rule, not infer from city name or label.

### Hardcode assumption scan

|file|assumption|unit|tz|
|---|---|---|---|
|dashboard_server.py|Many legacy display helpers default `market.get("unit") or "F"`; must not be used for Asian C markets without station/market unit.|F/C display conversion|UTC plus station timezone fallback|
|weatherbot_v3/distribution.py|Converts native buckets to F for older distribution path; Layer 6 C buckets now need truncation contract tests.|F internal conversion in legacy path|n/a|
|weatherbot_v3/deb.py / hourly.py|Daily max must aggregate by city local day, not UTC day.|city profile unit|city settlement timezone|
|weatherbot_v3/metar.py|AWC METAR temp comes in C; IEM tmpf/dwpf are F and converted to C in parser.|C from AWC, F from IEM|report UTC -> city local|
|weatherbot_v3/china_weather.py|China live sources emit C/Beijing-HK local time; display-only until settlement source proven.|C/hPa/kph-ish source strings|Asia/Shanghai or Asia/Hong_Kong|
|frontend/src/components/WeatherPanel.tsx|Frontend renders unit from city/market payload; should not infer F/C from label alone.|payload unit|local timestamp labels|

## 5. Settlement and execution flow constraints

- Polymarket highest-temperature markets use UMA/MOOV2-style optimistic resolution. The bot cannot propose settlement unless it controls a whitelisted proposer address; this project should not implement propose logic.
- Execution Workbench must model delayed realized PnL: market technical close -> proposer submits outcome -> liveness window (commonly around 2h per user-supplied MOOV2 note) -> finalization/payout. Until finalization, positions should remain pending/unrealized.
- Live gate implication: `resolved`/`settled` status must be separated from `market closed`; otherwise daily PnL will be overstated or marked too early.

## 6. Strategy implications and Asian priority

- Priority proposal for research/paper: Shanghai > Wuhan > Beijing >> Hong Kong > Tokyo > Seoul.
- Rationale: Asian markets are newer/less efficient, Chinese Wunderground airport markets have clear ICAO URLs, Open-Meteo CMA GRAPES is available, and user-supplied external PnL notes identify Shanghai as historically positive while Seoul was a large loss city.
- Seoul should be `monitor_only` until paper validation shows city-specific positive ROI; Hong Kong should be observation/paper only until HKO Daily Extract truth is implemented and station mismatch is resolved.
- Tail bucket strategy remains research-only: cheap buckets `<$0.10` can create fat-tail wins, but current WeatherBot should require source agreement, WU/HKO truth coverage, market depth, and replay-tested ROI before any canary.

## 7. Action items for WeatherBot

1. Add Asian city registry rows for Beijing/ZBAA, Taipei/RCSS, Wuhan/ZHHH, Qingdao/ZSQD, Shenzhen/ZGSZ, Singapore/WSSS if not already present, with `settlement_unit=C` and local timezone.
2. Extend Gamma market sync from current 5-7 cities to the full Asian city set; preserve Hong Kong HKO as `settlement_mismatch` until HKO truth collector exists.
3. Add Wunderground daily max capture as a separate truth-provider candidate; do not overwrite METAR/IEM rows.
4. Prefer CMA GRAPES for mainland China, JMA seamless for Japan/Korea/Taipei, and ensemble + historical forecast runs for calibration.
5. Add settlement-delay state to paper/live accounting before measuring realized PnL.

## Fetch errors

```json
[]
```

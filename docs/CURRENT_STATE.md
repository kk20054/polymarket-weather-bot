# WeatherBot Current State

## Latest Clean Snapshot 2026-07-09
- Phase: Phase 2 data-source alignment. Usable for observation, source benchmarking, and controlled paper research; live trading remains locked.
- This turn landed WU/weather.com hourly history as `truth_wunderground_hourly`, exposed it through `wunderground-hourly-fetch`, and feeds it into `hourly_consensus_points` as the Historical line.
- Weather.com v3 forecast is wired into `weathercom-fetch`, production refresh, scheduler forecast poller, and `DEB_WEIGHT_MODE=polywx_aligned` DEB components as `weathercom_v3`.
- WU daily settlement truth now derives from WU/weather.com hourly history using each city local day, then stores `truth_wunderground_daily`; existing rows are skipped unless `--force-rebuild` is used.
- Real batch backfill succeeded for 10 cities x 7 days (2026-07-01..07): 70 daily truth rows and 1609 hourly rows; no skipped city-date.
- Real smoke succeeded: ZSPD WU hourly 2026-07-06 returned 48 rows, high 36.0C, low 26.0C; hourly consensus rebuilt 24 Historical points.
- Real smoke succeeded: Shanghai Weather.com v3 forecast persisted 2 runs / 2 members, then DEB built `polywx_aligned_deb_v1` with `has_weathercom_v3=True`.
- Checks passed: `python -m unittest tests.test_v3_core`, targeted WU/weathercom tests, and `git diff --check` (only Windows line-ending warnings).
- Remaining blockers: only 7 days are backfilled so far; HKO truth still separate; PolyWX numeric benchmark still needs batch comparison.
- Next: extend WU daily/hourly backfill to 30-90 days, rebuild hourly consensus + DEB, and compare against PolyWX saved benchmarks.

## Latest Clean Snapshot 2026-07-07
- Phase: Phase 1.5 -> Phase 2. Usable for observation, controlled simulation, and paper research; live trading remains locked.
- This turn aligned WeatherBot source roles with the PolyWX / weather.com / WU / METAR / PWS model without importing PolyWX display values as truth.
- Implemented `weathercom_v3_forecast`; the current `.env` key returns HTTP 401 for weather.com v3 forecast, so DEB records `missing_weathercom_v3` and re-normalizes remaining model weights.
- Wunderground PWS current works with the current key and remains display-only trend / peak-lock evidence; WU daily history still fails for airport truth with the current key.
- DEB can run in `DEB_WEIGHT_MODE=polywx_aligned`, storing `role`, `weight_prior`, `weight_after_mae`, `mae_7d`, `truth_basis`, and PWS peak-lock evidence per component.
- Dashboard DEB card now includes a PolyWX-style source-weight table next to the Gaussian bucket chart; this does not unlock live execution.
- Checks passed: `python -m unittest tests.test_v3_core tests.test_polywx_contract tests.test_ensemble_vs_market`, `npm run build`, `git diff --check`.
- Next: decide whether to obtain weather.com forecast / WU daily history permissions, or keep Open-Meteo as the honest WeatherBot forecast source and label UI accordingly.

## 当前 Phase 与可用性结论

- 当前处于 Phase 1.5 -> Phase 2：可观察、可模拟、可继续生产化验证，但不能宣称自动实盘盈利。
- 本轮修复调度器与云量口径：调度器 poller 完成后前端会主动刷新 dashboard/market/signal/DEB 查询；PWS 失败不再让核心 METAR poller 退避。
- Hourly 主图云量改用 `forecast_cloud_cover`，METAR 表仍保留 METAR 云层解析出的 `cloud_cover`，避免把观测云层和预报云量混在同一条面积图里。
- 上轮继续做 PolyWX 对齐减法：中间主板删除 Delta Audit 入口、内部 `METAR --`/`证据 F/H`/模块计数和顶部“刷新当前城市”；推荐关注移到城市横条上方，只显示城市、现在温度、预计最高温。
- 后端没有新增 endpoint、collector 或实盘路径；`LIVE_TRADING=false`，实盘仍锁定。
- 修正了 Hourly 图表口径：云量不再用湿度兜底；无真实 China Live/PWS 数据不画点；日期前后按钮按日历日切换。
- 修正了 METAR 派生字段链路：`visibility`、`condition/wx`、`cloud_cover` 从 METAR raw/解析产物进入 hourly consensus 与 UI。
- 本轮用 Firecrawl 抓取 PolyWX Shanghai 2026-07-06 与 Chicago 2026-07-04 的 24 小时 Forecast/Cloud benchmark，并生成 `audits/polywx-source-alignment-2026-07-07/README.md`；结论是 Cloud 百分比刻度已统一为 0-100，差异主要来自数据源/模型处理不同，不是 UI 百分比错误。
- 已补齐 Shanghai 2026-07-06 的 Open-Meteo Historical display-only 小时数据：写入 24 行 `mesonet_observations.network=open_meteo_historical` 并重建 hourly consensus；该数据不是 settlement truth，不解锁 live。
- 当前验证通过：`python -m unittest tests.test_polywx_contract tests.test_scheduler`、`python -m unittest tests.test_v3_core`、`npm run build`、`git diff --check`；浏览器打开 Shanghai 2026-07-06 无 console error。

## 最近 5 条 ledger 摘要

- 2026-07-07 / Layer 0/2/4 / Firecrawl PolyWX benchmark + Shanghai historical 补数：Cloud 差异确认为 data_source，不是百分比单位；Shanghai historical 从 0/24 补到 24/24；下一步决定 UI 是否引入 `polywx_forecast` display-only 兜底或继续明确展示 WeatherBot 自有预报。
- 2026-07-07 / Layer 2/4/7 / 调度器刷新链路和云量口径修正：poller 完成会刷新前端查询，PWS 失败不阻断 METAR，主图云量走 forecast cloud；下一步跑 10-30 分钟 scheduler 观察实时更新。
- 2026-07-06 / Layer 2/4/7 / PolyWX 对齐减法 + METAR 派生字段修正完成；下一步重启前后端后人工核验 Shanghai/Chicago 页面。
- 2026-07-06 / Layer 2/4/7 / 强制减法与 DEB/hourly 口径修正完成；下一步用真实 PolyWX benchmark 继续核对曲线和 DEB 数值。
- 2026-07-06 / Layer 7 / Round 5 UI 已完成城市状态、i18n、动态桶表、Delta/Alpha 展示；下一步继续减掉非主路径说明。
- 2026-07-05 / Round 4 / Ensemble DEB 和 Previous Runs 入口已落地；Beijing 34C sanity 暴露模型校准仍不可靠。
- 2026-07-05 / Round 3 / Truth Layer 三源协议、Gamma 结构化持久化、亚洲城市 registry 完成；IEM 仍只能作 approximation。

## 生产阻塞项清单

- PolyWX 对标已完成 Forecast/Cloud 第一轮字段级 benchmark；Cloud 百分比刻度无误，但 Forecast/Cloud 来源不同导致 Shanghai cloud MAE 46.36pp、Chicago cloud MAE 20.67pp。China Live 仍缺 5 分钟历史源，DEB 数值仍需继续 benchmark。
- 结算 truth 独立样本不足，IEM/AWC/METAR 不能替代 Wunderground/HKO 直接解锁 live。
- Open-Meteo Previous Runs 与市场高置信桶存在明显偏差，模型校准还不能用于真实资金。
- 推荐卡可读性已改善，但推荐数量仍可能因 gate 严格而为 0，需要用诊断脚本解释原因。
- Orderbook replay、滑点、成交失败、退出流动性、结算延迟还未达到生产验收。
- 14-30 天 paper validation、dry-run、canary gate 均未完成。

## 未来 5 分钟内的下一步

- 若核对 UI，当前后端 `8765` 与 Vite `5173` 已启动；打开 `http://127.0.0.1:5173/?city=shanghai-zspd&date=2026-07-06`。
- 若核对数据，先看 `/api/dashboard` 的 `weather_city_series`，再看 `/api/hourly-consensus?city=shanghai&target_date=2026-07-06`。
- 若继续整改，优先决定 Forecast/Cloud 对齐路线：A) 将 PolyWX forecast 作为 `polywx_forecast` display-only/fallback 用于 UI parity；B) 保留 Open-Meteo/WeatherBot 自有预报并在 UI 明确标注来源差异。China Live 若要对齐 PolyWX 5 分钟历史，需要另找可回放的中国站点历史 feed。
- 若提交代码，确认不 stage `audits/`、`data/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`、`node_modules/`。

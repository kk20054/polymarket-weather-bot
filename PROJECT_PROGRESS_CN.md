# WeatherBot 项目进度台账

最后更新：2026-07-02

这个文件是 WeatherBot 的事实台账。每轮开发、研究、验证或修复结束前，都要在这里追加一条记录，避免进度散落在聊天记录、上下文摘要和本地 `audits/` 目录里。

## 怎么看当前项目推进

- 当前能不能日常用：看 `当前可用性结论`。
- 最近做了什么：看 `近期进度记录`。
- 为什么还不能实盘自动赚钱：看 `生产阻塞项`。
- PolyWX/Firecrawl 到底抓了什么：看 `PolyWX 研究状态`。
- 下一轮该做什么：看 `下一步优先级`。

## 当前可用性结论

当前状态：**Phase 1.5 到 Phase 2 过渡**。

可以用来做：

- 本地打开看板，按城市和日期观察天气证据页。
- 手动触发受控抓取，查看预报、METAR、历史观测、偏差统计、抓取日志和交易信号。
- 小额策略研发前的 paper/simulation 验证。
- 检查 Polymarket 链接、盘口、信号、模拟记录和数据链路是否完整。

现在不能声称可以做：

- 无人值守自动实盘赚钱。
- 直接用当前 EV 信号加仓。
- 仅凭当前本地回测证明策略有稳定 edge。
- 用 Open-Meteo fallback 或少量 truth 样本解锁实盘。

一句话判断：**现在是可观察、可模拟、可继续生产化验证的天气交易平台雏形；还不是可放心实盘自动赚钱的机器人。**

## 数据和回测价值判断

当前已有价值：

- SQLite 已经成为主要状态库，逐步沉淀 forecast、METAR、hourly consensus、orderbook、signals、paper orders、fetch logs 等结构化数据。
- 看板已能按 PolyWX 方式把同一城市/日期的预报、METAR、历史、偏差和日志放到一个证据页。
- paper executor 和 live/dry-run 架构已经有雏形，实盘默认锁定是正确状态。
- 回测和模拟可以用于发现明显坏策略、城市误差、盘口 spread 成本和低价尾桶失真。

当前还不够：

- 结算 truth 覆盖不足，很多城市仍没有足够官方站点/独立结算日样本。
- 回测还不是完整盘口回放，不能证明实际成交、滑点和退出流动性。
- 策略组还没有证明 allowed 组长期 ROI 为正且显著优于 blocked 组。
- PolyWX 参考目前仍是摘要和代表页结构研究，不是完整源码或完整 API 归档。

## 生产阻塞项

1. **truth 样本不足**：城市/站点独立结算日数量未达到生产门槛。
2. **概率校准未闭环**：需要无泄漏 forecast archive、station truth、bucket distribution、walk-forward 验证。
3. **盘口级回放不足**：需要保存并回放 orderbook/best bid/ask/tick/orderMinSize/staleness。
4. **策略收益未证明**：当前不能用局部 UI 或单次模拟盈亏判断可盈利。
5. **实盘验收未完成**：dry-run、重复订单保护、最小订单、余额、熔断和 14-30 天 paper gate 仍需持续验证。

## PolyWX 研究状态

已确认事实：

- PolyWX 是 query 参数驱动的 SPA，核心 URL 形态是 `https://www.polywx.xyz/?city={city-station}&date={yyyy-mm-dd}`。
- Firecrawl `map` 能发现少量公开入口，例如 Chicago/Tokyo 和 `?lang=zh`，但不能自动枚举全城市/全日期。
- 本地目录 `audits/polywx-firecrawl-reference-2026-07-01/` 和 `audits/polywx-full-reference-2026-07-01/` 目前只包含摘要 `README_CN.md`，不是完整语料库。
- 已借鉴到 WeatherBot 的关键模块：城市单页、推荐关注、日期切换、预报/METAR/历史观测/偏差统计/抓取日志五 tab、逐小时气温图、当日最高温预测、概率分桶、抓取日志。

需要补齐：

- 固定城市/日期样本矩阵。
- 渲染 DOM snapshot。
- 页面截图。
- 静态资源和前端 bundle 线索。
- 可见网络/API 响应。
- `MANIFEST.json` 记录抓取 URL、时间、文件数、工具、失败原因。

## 近期进度记录

### 2026-07-02：Layer 4 hourly_consensus 逐小时共识层生产化

- 目标：按 `AGENTS.md` Build Order 继续 Layer 4，只补 `hourly_consensus` 统一小时证据层的 schema、collector、API、readiness 和测试；不触碰 market bucket、signal decision、右侧执行台、自动抓取或实盘交易。
- Build Order layer：Layer 4 — `hourly_consensus` view feeding charts and signal engine。
- Layer 0 前置核验：
  - 复核 `audits/polywx-firecrawl-2026-07-01/MANIFEST.json`：`generated_at=2026-07-01T20:51:44.375748+08:00`、当前约 `0.16` 天龄、`files=17`、`five_tabs=true`、`hourly_chart=true`、`xhr_response_bodies=true`、`api_endpoints=true`。
  - 结论：manifest 少于 14 天且有代表性 XHR body，本轮复用现有 PolyWX corpus，不重复 Firecrawl。
- 改动：
  - `weatherbot_v3/db.py` 扩展 `hourly_consensus`，新增 `precipitation`、`wind_speed`、`wind_direction`、`pressure`、`dew_point`、`forecast_source`、`forecast_sources_json`、`observation_sources_json`、`source_mix_json`、`consensus_version`、`build_status`、`build_warnings`，并让 `upsert_hourly_consensus()` 持久化这些字段。
  - `weatherbot_v3/hourly.py` 新增 `build_hourly_consensus()`：读取 Layer 3 `forecast_runs/forecast_members` 和 Layer 2 `metar_reports/mesonet_observations`，按城市/日期/本地小时合成 forecast、observed、residual、source mix、build status。
  - `weatherbot_v3/hourly.py` 新增 `hourly_consensus_summary()`，并扩展 `hourly_consensus_points()` 输出更多天气字段和审计字段。
  - `dashboard_server.py` 新增只读接口 `GET /api/hourly-consensus?city=...&target_date=...`，不会触发抓取、扫描或自动模拟。
  - `weatherbot_v3/qualification.py` 新增 `hourly_consensus` readiness stage，暴露 rows、cities、dates、rows_with_forecast、rows_with_observed、rows_with_residual、partial_rows，并在 next actions 中提示显式执行 `hourly-consensus-build`。
  - `weatherbot_v3/cli.py` 新增 `hourly-consensus-build` 命令；`weatherbot_v3/production_actions.py` 的 `build_hourly_consensus` action 从旧 METAR-only builder 切换到新的 Layer 4 builder。
  - `tests/test_v3_core.py` 增加 Layer 4 测试：schema 字段、forecast+METAR+PWS 合成 residual、只读 API、CLI runner、production action、readiness gate。
- 验证：
  - Targeted Layer 4 tests 通过：8 tests OK。
  - `python -m unittest tests.test_v3_core` 通过：98 tests OK；仍有既有 sqlite `ResourceWarning: unclosed database` 噪声。
  - `python -m unittest tests.test_polywx_contract` 通过：7 tests OK。
  - `npm run build` 通过；仍有既有 Browserslist 过期和 Vite chunk size warning。
  - 当前 8765 `/api/dashboard` 运行态：约 `243.3ms` 返回，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`、`last_refresh_was_auto=false`。
  - 临时启动 8766 验证新代码：`/api/dashboard` OK，未启动扫描/自动刷新；`/api/hourly-consensus?city=chicago` OK，当前库返回 `hourly_rows=2`；验证后已关闭临时进程。
  - `git diff --check` 通过；仅有 Windows LF/CRLF 提示，没有 whitespace error。
- 当前可用性结论：
  - Layer 4 现在有一条明确的统一小时证据路径：后续小时图、偏差统计和信号层可以读取同一张 `hourly_consensus`，而不是各自临时拼 forecast/METAR/PWS。
  - 该层可以帮助判断“预报与观测偏离多少、哪些小时有 residual、数据源混合情况”，但它仍是数据基座层，不证明策略有 edge，也不解锁自动实盘。
- 剩余阻塞：
  - 当前共识行质量依赖 Layer 2/3 输入覆盖；如果某城市没有 METAR/PWS 或 forecast runs，Layer 4 只能生成 partial/forecast-only 行。
  - residual 只在同小时 forecast 与 observed 同时存在时产生；还需要更系统的历史 truth 和 station-local 独立样本来做校准。
  - sqlite ResourceWarning 仍需后续单独治理。
  - production-refresh 仍不会自动构建 hourly consensus，需显式运行 `hourly-consensus-build`，符合“不自动抓取/不自动扫描”的启动约束。
- 下一步：
  - 进入 Layer 5：`market_buckets`，把 Polymarket outcome/token/orderbook metadata 与城市/日期/温度桶严格匹配，记录 tick size、orderMinSize、negRisk、token id、bucket boundary 和 strict matching status。
- 相关提交：`96fe9e6 Add hourly consensus data layer`；随后 ledger-hash 回填提交记录本行。

### 2026-07-01：Layer 3 forecast_runs / forecast_members 预报层生产化

- 目标：按 `AGENTS.md` Build Order 继续 Layer 3，只补预报运行与成员层的数据合约、PolyWX forecast 行解析、只读 API 和测试；不触碰概率策略、信号决策、右侧执行台、自动抓取或实盘交易。
- Build Order layer：Layer 3 — `forecast_runs` and `forecast_members` for ECMWF, GFS, HRRR, Open-Meteo, DEB, and related inputs。
- Layer 0 前置核验：
  - 复核 `audits/polywx-firecrawl-2026-07-01/MANIFEST.json`：`generated_at=2026-07-01T20:51:44.375748+08:00`、`files=17`、`five_tabs=true`、`hourly_chart=true`、`xhr_response_bodies=true`、`api_endpoints=true`。
  - 结论：本轮复用现有 PolyWX XHR 证据，不重复 Firecrawl；`/api/forecast` 已知字段包括 `hour`、`temperature_c`、`fetched_at`、`cloud_cover_pct`、`precip_chance_pct`、`wind_dir_deg`、`wind_kph`、`pressure_hpa`、`dew_point_c`、`condition_phrase` 等。
- 改动：
  - `weatherbot_v3/db.py` 扩展 `forecast_runs`，新增 `parser_version`、`parse_status`、`parse_warnings`、`source_unit`，并让 `insert_forecast_run()` 在 upsert 时持久化这些解析审计字段。
  - `weatherbot_v3/db.py` 新增 `forecast_summary()`，提供按 city/date 查询 forecast run、member、source 统计和最近 run 的只读摘要。
  - 新增 `weatherbot_v3/forecast.py`，提供 `forecast_run_from_polywx_rows()` 与 `ingest_polywx_forecasts()`：把 PolyWX `/api/forecast` 风格小时行解析为一个 deterministic forecast run + member，并保留 source URL、raw response hash、单位转换、解析状态和 warnings。
  - `dashboard_server.py` 新增只读接口 `GET /api/forecasts?city=...&target_date=...`，不会触发抓取、扫描或自动模拟。
  - `tests/test_v3_core.py` 增加 Layer 3 合约测试：forecast schema 字段、PolyWX forecast 行解析与落库、单位 C->F 转换、hourly member 聚合、`/api/forecasts` 不触发刷新。
- 重要取舍：
  - PolyWX forecast XHR 没有披露底层模型和真实 run time，本轮明确标记 `source=polywx_forecast`、`model_version=undisclosed`、`training_eligible=false`、`ineligibility_reason=polywx_model_source_and_run_time_not_disclosed`，不伪装成 ECMWF/GFS/HRRR，也不解锁训练或实盘 gate。
- 验证：
  - Targeted Layer 3 tests 通过：schema、forecast store、PolyWX forecast parser、forecast API、hourly points 共 5 tests OK。
  - Data readiness/model dataset 相关窄测试通过：3 tests OK。
  - `python -m unittest tests.test_v3_core` 通过：95 tests OK；仍有既有 sqlite `ResourceWarning: unclosed database` 噪声。
  - `python -m unittest tests.test_polywx_contract` 通过：7 tests OK。
  - `npm run build` 通过；仍有既有 Browserslist 过期和 Vite chunk size warning。
  - 当前 8765 `/api/dashboard` 运行态：约 `220.5ms` 返回，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`、`last_refresh_was_auto=false`。
  - 临时启动 8766 验证新代码：`/api/dashboard` OK，未启动扫描/自动刷新；`/api/forecasts?city=chicago` OK，当前库返回 `forecast_runs=874`、`forecast_members=9462`；验证后已关闭临时进程。
  - `git diff --check` 通过；仅有 Windows LF/CRLF 提示，没有 whitespace error。
- 当前可用性结论：
  - Layer 3 现在具备“把预报输入作为可审计 run/member 存储和读取”的基础能力，后续 PolyWX 风格 Forecast tab、小时图、偏差统计、模型训练和信号引擎可以从同一套 forecast run/member 表取数。
  - 这提升的是数据基座和可复盘能力，不代表策略已经有 edge，也不代表可以自动实盘。
- 剩余阻塞：
  - 当前 PolyWX forecast 只能作为参考输入，不能作为无泄漏训练样本，因为底层模型和 run time 不透明。
  - 生产级 ECMWF/GFS/HRRR/Open-Meteo collector 仍需继续补齐 typed collector 和真实 provider metadata。
  - forecast_members 虽可存 hourly_json，但成员级 ensemble source、run id、issued_at、provider license 还需要更细的源适配。
  - sqlite ResourceWarning 仍需后续单独治理，减少测试噪声。
- 下一步：
  - 进入 Layer 4：`hourly_consensus`，把 Layer 2 观测和 Layer 3 预报汇成城市/日期/小时的统一证据路径，供 PolyWX 风格小时图和后续信号引擎读取。
- 相关提交：`f7249b4 Add forecast data foundation layer`；随后 ledger-hash 回填提交记录本行。

### 2026-07-01：Layer 2 METAR/mesonet 观测层生产化

- 目标：按 `AGENTS.md` Build Order 继续 Layer 2，只补 METAR/SPECI 与 mesonet/PWS 观测层的数据合约、解析、API 和测试；不改右侧执行台、不启动自动抓取、不触碰实盘交易。
- Build Order layer：Layer 2 — `METAR/SPECI + mesonet_observations`。
- Layer 0 前置核验：
  - 复核 `audits/polywx-firecrawl-2026-07-01/MANIFEST.json`：`generated_at=2026-07-01T20:51:44.375748+08:00`、`files=17`、`five_tabs=true`、`hourly_chart=true`、`xhr_response_bodies=true`、`api_endpoints=true`。
  - 结论：本轮复用既有 PolyWX 证据，不重复 Firecrawl。
- 改动：
  - `weatherbot_v3/db.py` 扩展 `mesonet_observations`，新增 `parser_version`、`parse_status`、`parse_warnings`、`raw_unit`，并让 upsert 持久化这些解析审计字段。
  - 新增 `weatherbot_v3/mesonet.py`，提供 PWS/mesonet 行解析和批量 ingest：支持 `temperature_c`/`temperature_f`、站点 id、观测时间、湿度、露点、质量标记、source URL、parser version 和 parse warnings。
  - `dashboard_server.py` 新增只读接口 `GET /api/observations?city=...&target_date=...`，直接返回 `weather_evidence_summary`，不会触发抓取或扫描。
  - `weatherbot_v3/qualification.py` 新增 `observations` readiness stage：检查 METAR 是否存在、城市覆盖是否完整、主结算站点是否缺口、METAR parse failure 是否为 0；同时把 mesonet 作为可选辅助观测指标暴露。
  - `tests/test_v3_core.py` 增加 Layer 2 合约测试：mesonet schema 字段、PWS 行解析与落库、观测 API 不触发刷新、data readiness 对 METAR 缺口的阻塞提示。
- 验证：
  - `python -m unittest tests.test_v3_core` 通过：93 tests OK；仍有既有 sqlite `ResourceWarning: unclosed database` 噪声，需要后续单独治理。
  - `python -m unittest tests.test_polywx_contract` 通过：7 tests OK。
  - `npm run build` 通过；仍有既有 Browserslist 过期和 Vite chunk size warning。
  - 当前 8765 `/api/dashboard` 运行态：约 `293.6ms` 返回，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`、`last_refresh_was_auto=false`。
  - 临时启动 8766 验证新代码：`/api/dashboard` OK，`/api/observations?city=chicago` OK，返回 `metar_reports=2`、`mesonet_observations=0`；验证后已关闭临时进程。
  - `git diff --check` 通过；仅有 Windows LF/CRLF 提示，没有 whitespace error。
- 当前可用性结论：
  - Layer 2 现在具备“结构化保存站点观测证据”的基础能力：METAR 和 mesonet 观测可以带原始来源、解析版本、解析状态和警告进入 SQLite，并能通过 API 读出来。
  - 这让后续 D+0 最高温判断、PolyWX 风格的 METAR/实时观测模块、偏差统计和策略 gate 有了更稳的数据落点。
  - 当前仍不能用于自动实盘赚钱；它只是把观测层证据链打稳了一格。
- 剩余阻塞：
  - 当前库里 Chicago 有少量 METAR 证据，但 mesonet/PWS 仍是通用 ingest/parser，没有完整区域网络 collector。
  - METAR 城市全覆盖、主站点全覆盖、parse failure 清理仍未达生产 gate。
  - WMO id 映射仍未补齐。
  - 现有完整测试仍有 sqlite ResourceWarning，需要后续做连接关闭治理，减少噪声。
- 下一步：
  - 进入 Layer 3：`forecast_runs` 与 `forecast_members`，把预报数据从“展示/快照”升级成可追踪 run、member、source、issued_at、valid_time 的无泄漏训练/推理基座。
- 相关提交：`9320b60 Add observations data foundation layer`；随后 ledger-hash 回填提交记录本行。

### 2026-07-01：Layer 1 stations 站点基座落库

- 目标：按 `AGENTS.md` Build Order 进入 Layer 1，只补 `stations` 站点基座：SQLite schema、registry collector、测试和 API surface；不触碰右侧执行台、不启动自动抓取、不做实盘。
- Build Order layer：Layer 1 — `stations` table for target cities。
- Layer 0 前置核验：
  - 复核 `audits/polywx-firecrawl-2026-07-01/MANIFEST.json`：`five_tabs=true`、`hourly_chart=true`、`xhr_response_bodies=true`、`api_endpoints=true`、`files=17`、XHR scrape id `019f1db5-1419-77d7-b55c-297ac1227be9`。
  - 结论：本轮无需重复 Firecrawl，可复用现有代表性 PolyWX rendered/XHR corpus。
- 改动：
  - `weatherbot_v3/db.py` 新增 `stations` 表，字段包括 `city_key`、`city_name`、`station_id`、`icao_id`、`wmo_id`、provider ids、station name、timezone、unit、lat/lon、region、settlement rule text、primary settlement source、nearby networks、confidence、verification status、registry version、raw JSON、updated_at。
  - 新增 `weatherbot_v3/stations.py`，作为 Layer 1 collector：把 `SETTLEMENT_REGISTRY` 同步进 SQLite，提供 `sync_station_registry()`、`list_stations()`、`get_station()` 和 `station_row_from_profile()`。
  - `weatherbot_v3/qualification.py` 新增 `stations` readiness stage；当前 20 个站点行、ICAO/timezone/unit/station_id 完整时该 stage ready；`wmo_id_missing=20` 作为 metrics 暴露，不伪造 WMO 号。
  - `weatherbot_v3/cli.py` 新增 `stations-sync`、`stations-list`；`weatherbot_v3/README_CN.md` 初始化流程加入 `stations-sync`。
  - `dashboard_server.py` 新增 `GET /api/stations`，支持 `city`、`region` 和 `sync_registry` 参数。
  - `tests/test_v3_core.py` 新增 Layer 1 测试：站点同步落库、station row parser、`/api/stations` API、data readiness stations stage。
  - `AGENTS.md` 保留原 PolyWX contract 测试关键词，避免文档结构整理导致合约测试无意义失败。
- 验证：
  - Targeted tests：4 个新增/相关 `tests.test_v3_core` 测试通过；2 个 `tests.test_polywx_contract` 文档合约测试通过。
  - `python -m unittest tests.test_v3_core` 通过：91 tests OK；仍有既有 sqlite `ResourceWarning: unclosed database` 噪音。
  - `python -m unittest tests.test_polywx_contract` 通过：7 tests OK。
  - `npm run build` 通过；仍有既有 Browserslist 过期和 chunk size warning。
  - `/api/dashboard` runtime check：当前 8765 约 `243ms` 返回；`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`、`last_refresh_was_auto=false`。
  - 当前 8765 `/api/stations` 返回 404，因为正在运行的是旧后端进程，尚未重启加载新路由。
  - 临时启动新后端 `127.0.0.1:8766` 验证新代码：`/api/dashboard` OK，未启动扫描/自动刷新；`/api/stations?city=chicago` 返回 `KORD`、`America/Chicago`、`sync_synced=20`，验证后已关闭临时进程。
  - CLI 验证：`python -m weatherbot_v3.cli stations-sync` 返回 `synced=20`、`total=20`，`stations` stage `ready`，regions 分布为 `asia=6`、`ca=1`、`eu=4`、`oc=1`、`sa=2`、`us=6`。
- 当前可用性结论：
  - Layer 1 站点基座已可用：站点注册表现在有 SQLite 主表、CLI 同步和 API 读取面。
  - 这提升了后续 METAR、mesonet、forecast、truth、market bucket 的统一站点来源，减少 UI/算法各自猜站点的问题。
  - 当前仍不能证明策略可赚钱，也不能解锁实盘；它只是把数据基座第一层钉稳。
- 剩余阻塞：
  - WMO id 尚未补权威映射，当前不伪造，作为 metrics 暴露。
  - Layer 2 `metar_reports` / `mesonet_observations` 虽已有部分表和函数，但还需要按 Build Order 做 parser/collector/source URL/parse warnings 的完整生产验收。
  - 当前 8765 需要手动重启后端才能暴露 `/api/stations` 新路由。
- 下一步：
  - 进入 Layer 2：补 METAR/SPECI 与 mesonet observations 的 parser/collector 测试和 API surface，优先保证 raw report、decoded fields、source URL、parser version、parse warnings 可复盘。
- 相关提交：`cfc85ae Add stations data foundation layer`；随后 ledger-hash 回填提交记录本行。

### 2026-07-01：进度治理修复与 Layer 0 证据状态校准

- 目标：回应“每轮工作没有稳定记录、Firecrawl 重复抓取、项目推进不透明”的问题，把记录规则写死到 `AGENTS.md`，并校准当前 PolyWX corpus 的真实状态。
- Build Order layer：项目治理 / Layer 0 状态校准；未进入 Layer 1+，未修改交易逻辑、算法、看板组件或执行工作台。
- 改动：
  - 重写整理 `AGENTS.md` 的 Markdown 结构，修复使命代码块未闭合、标题和命令块被打散的问题。
  - 新增并强化 `Where Progress Lives`、`Turn Start Protocol`、`Turn End Protocol`：后续每轮必须先读 `PROJECT_PROGRESS_CN.md`，再看 git 状态；涉及 PolyWX 时先核验最新 `MANIFEST.json` 和 `SCHEMA_MAP_CN.md`，不能因为上下文压缩就重复 Firecrawl。
  - 明确最终回复必须说明：当前能不能用、改了什么、验证结果、剩余阻塞、下一步、记录写在哪里。
  - 明确 `audits/` 是本地研究证据，不提交；`PROJECT_PROGRESS_CN.md` 才是人类可读事实台账。
  - 复核 `audits/polywx-firecrawl-2026-07-01/MANIFEST.json`：当前 `xhr_response_bodies=true`、`api_endpoints=true`、`five_tabs=true`、`hourly_chart=true`。
  - 复核本地 XHR 证据文件：`audits/polywx-firecrawl-2026-07-01/network/chicago-kord/2026-07-01/xhr_capture.json`，scrape id 为 `019f1db5-1419-77d7-b55c-297ac1227be9`，捕获了 Forecast、METAR、Historical、PWS、Fetch Log、Diff Stats、Accuracy、Historical-METAR Match、Peak Marker、Prediction、Recommendations 等响应摘要。
- 验证：
  - `git diff --check` 通过；仅提示 Windows 工作区会把 `AGENTS.md`、`PROJECT_PROGRESS_CN.md` 的 LF 转为 CRLF，没有 whitespace error。
  - `python -m unittest tests.test_v3_core` 通过：88 tests OK；仍有既有 sqlite `ResourceWarning: unclosed database` 噪音。
  - `npm run build` 通过；仍有既有 Browserslist 过期和 chunk size warning。
  - `/api/dashboard` runtime check：约 `214ms` 返回；`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`、`last_refresh_was_auto=false`。
  - Layer 0 manifest check：`five_tabs=true`、`hourly_chart=true`、`xhr_response_bodies=true`、`api_endpoints=true`、`files=17`、XHR scrape id `019f1db5-1419-77d7-b55c-297ac1227be9`。
- 当前可用性结论：
  - 项目当前仍是“可观察、可模拟、可继续生产化验证”的阶段，不是可无人值守实盘自动赚钱的机器人。
  - PolyWX Layer 0 已经比旧台账更完整：有代表性 XHR response body 证据；但仍不是 PolyWX 完整源码克隆，也不是所有城市/日期的完整 API 归档。
  - 回测/模拟当前有研发价值，可用于发现坏策略、数据缺口、盘口成本和低价尾桶问题；还不能证明稳定 edge。
- 剩余阻塞：
  - 结算 truth 覆盖和独立 settlement day 样本仍不足。
  - 盘口级 orderbook replay、成交/退出流动性回放仍不足。
  - 策略 allowed 组尚未证明长期 ROI 为正且优于 blocked 组。
  - PolyWX corpus 的长响应多数保存为 `bodyPrefix + textLength + keys`，不是全量原文归档。
- 下一步：
  - 进入下一轮生产验证前，先从本台账和最新 manifest 继续；不重复 Firecrawl，除非新问题需要新的证据。
  - 优先推进 Layer 1 `stations` 和 Layer 2 METAR/mesonet truth 数据基座，再继续 UI 像素级对齐或策略扩展。
- 相关提交：`70bcee2 Record project progress protocol`；随后 ledger-hash 回填提交记录本行。

### 2026-07-01：Layer 0 PolyWX Firecrawl corpus 重新生成

- 目标：按 AGENTS.md 的 Build Order 先补 Layer 0，确认 `audits/polywx-firecrawl-2026-07-01/` 是否存在；不存在则先用 Firecrawl 生成语料，停止在 Layer 0，不触碰上层 schema/API/UI。
- Build Order layer：Layer 0 — PolyWX reference corpus (Firecrawl)。
- 改动：
  - 新增本地研究目录 `audits/polywx-firecrawl-2026-07-01/`（按规则不提交 GitHub）。
  - Firecrawl `map`：`https://polywx.xyz`，发现 6 个公开入口，确认 PolyWX 是 query-param SPA。
  - Firecrawl `search`：关键词 `Forecast / METAR / Historical / Diff Stats / Fetch Log / Hourly Temperature / Daily Max Prediction / Probability buckets`，只返回首页；feedback 调用失败，Firecrawl 返回 `INVALID_BODY`。
  - Firecrawl `scrape`：完成 3 城市 × 3 日期样本矩阵：`chicago-kord`、`tokyo-rjtt`、`atlanta-katl` × `2026-07-01`、`2026-06-30`、`2026-06-24`。
  - 生成 `MANIFEST.json`、`SCHEMA_MAP_CN.md`、`firecrawl_map_raw.json`、`firecrawl_search_raw.json` 和每页 `structure.json`。
  - 下载 2 张 Firecrawl screenshot 到本地；其余页面为 JSON-only，因为 full screenshot scrape 单页耗时最高超过 10 分钟。
- 验证：
  - Manifest check：`captured_pages=9`、`js_rendered_pages=9`、`five_tabs=true`、`hourly_chart=true`、`schema_map_exists=true`、`pages_with_screenshot=2`、`xhr_response_bodies=false`。
  - `python -m unittest tests.test_v3_core` 通过；仍有既有 sqlite `ResourceWarning` 噪音。
  - `npm run build` 通过；仍有既有 Browserslist 和 chunk size warning。
  - 重启本地后端后 `/api/dashboard` 约 `177ms`；`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`、`last_refresh_was_auto=false`。
- 当前可用性结论：Layer 0 现在有一个可审计的 PolyWX 信息架构参考 corpus，可用于后续讨论字段映射；但它不是 AGENTS 定义的完全有效 corpus，因为缺少每个 tab 至少一个 XHR response body，不能作为继续 Layer 1+ 的完全解锁依据。
- 剩余阻塞：
  - Firecrawl MCP `scrape` 没有直接返回 XHR response body。
  - full-page screenshot/html 抓取非常慢，不适合逐页串行重跑。
  - Firecrawl search feedback 返回 `INVALID_BODY`，未成功提交反馈。
- 下一步：先补 Layer 0 的网络响应捕获方案，可用 Firecrawl `interact` 或浏览器网络记录作辅助证据；补齐后再进入 Layer 1 `stations`。
- 相关提交：`969a106 Record PolyWX Firecrawl corpus status`。

### 2026-07-01：market bucket 执行摘要接入

- 目标：把“概率分桶看起来有 edge”进一步落到“盘口桶是否严格匹配、paper/live 为什么允许或阻塞”的城市/日期 evidence 摘要，减少只看 EV 或柱状图的误判。
- 改动：
  - 后端新增 `market_summary`，挂在 `city_evidence.dates[].modules.market_buckets` 下。
  - 摘要统计匹配桶、低价尾桶、开放尾桶、缺价、价差问题、过期盘口线索、paper 允许数、live 允许数、阻塞原因和代表样例。
  - 前端 `TemperatureDistributionPanel` 增加“盘口 / 执行摘要”，显示匹配桶、Paper OK、低价尾桶、盘口问题、主要阻塞原因，以及可执行/被阻塞样例。
  - TypeScript 增加 `CityEvidenceMarketBucketSummary`、`CityEvidenceMarketSignal` 和 `CityEvidenceMarketReason`。
  - 合约测试要求后端和前端持续暴露 `market_summary`，防止后续 UI 重构把交易审计能力删掉。
- 验证：
  - `python -m unittest tests.test_v3_core` 通过；仍有既有 sqlite `ResourceWarning` 噪音。
  - `python -m unittest tests.test_polywx_contract` 通过。
  - `npm run build` 通过；仍有既有 Browserslist 和 chunk size warning。
  - 本地 `/api/dashboard` 快速返回；`scanner_status=stopped`、`production_refresh.running=false`、`signal_count=0`，说明后端未误开自动抓取或自动模拟。
- 结论：看板现在能更直接回答“为什么这个信号不能买/只能 paper/被 live gate 阻塞”，但当前本地运行态没有新信号样本，真实策略收益仍需后续盘口回放和 paper 样本验证。
- 下一步：补 orderbook replay/成交可复现链路，让 paper buy/skip 不只看当前字段，而能按历史盘口快照重放。
- 相关提交：`b1022d3 Add market bucket evidence summary`。

### 2026-07-01：概率分桶 evidence summary 接入

- 目标：把 PolyWX 的“当日最高温预测 / 概率分桶”从单个信号的前端图表，推进为城市/日期 evidence payload 的可复盘摘要。
- 改动：
  - 后端 `city/date evidence` 新增 `probability_summary`，包含信号数、分桶数、归一化分布数、可操作信号数、最高概率桶、最高概率、top buckets 和严格匹配标记。
  - `daily_max_prediction`、`probability_buckets`、`market_buckets` 三个模块都带同一份概率摘要，便于 UI、信号和审计共享。
  - 前端 `TemperatureDistributionPanel` 接入 `selectedDateEvidence.modules.probability_buckets.probability_summary`，展示 evidence 级最高概率、分布覆盖、可操作信号和 top buckets。
  - TypeScript 增加 `CityEvidenceProbabilitySummary` 和 `CityEvidenceProbabilityBucket`。
  - 测试补充概率摘要 contract，防止退回只有行数没有分布摘要。
- 验证：
  - `python -m unittest tests.test_v3_core` 通过；仍有既有 sqlite `ResourceWarning` 噪声。
  - `python -m unittest tests.test_polywx_contract` 通过。
  - `npm run build` 通过；仍有既有 Browserslist/chunk size warning。
- 结论：概率桶现在更接近 PolyWX 的“城市/日期证据模块”，但仍需要更多真实分布样本和盘口回放来证明策略收益。
- 下一步：补 market bucket 严格匹配和盘口回放，让 probability summary 不只是展示概率，还能解释“为什么可以买/为什么不能买”。
- 相关提交：`2314323 Surface probability bucket evidence summary`。

### 2026-07-01：建立进度台账和每轮记录规则

- 原因：用户指出多轮 Firecrawl 和 UI 修改缺少统一进度记录，导致上下文压缩后容易重复造轮子。
- 本轮处理：
  - 新增 `PROJECT_PROGRESS_CN.md` 作为项目事实台账。
  - 明确当前可用性：可观察、可模拟、不可无人值守实盘。
  - 明确 PolyWX 参考目录不是完整语料库。
  - 明确后续每轮要更新台账。
- 验证：文档落盘，后续会在 `AGENTS.md` 中强制引用。

### 2026-07-01：PolyWX 风格城市工作台 UI 对齐

- 提交：`91ae5db Align dashboard workbench with PolyWX layout`
- 改动：
  - 中间工作台改成 PolyWX 风格：单日期控件，五个 tab。
  - 顶部文案改为“天气量化交易平台”。
  - 只保留顶部一个“自动抓取”入口。
  - 左侧顶部固定为“推荐关注”。
  - 逐小时图表改为暗色：METAR 亮色实线、预报蓝色虚线、云量/湿度柱、残差柱。
  - 更新 PolyWX 合约测试。
- 验证：
  - `npm run build` 通过。
  - `python -m unittest tests.test_polywx_contract` 通过。
  - `python -m unittest tests.test_v3_core` 通过，但仍有既有 `ResourceWarning: unclosed database` 噪声。
  - 浏览器确认无“正在连接”，1 个自动抓取按钮，1 个日期输入，五个 tab 存在。

### 2026-07-01：记录 PolyWX 本地参考状态

- 提交：`44798a7 Document PolyWX local reference state`
- 改动：
  - 在 `AGENTS.md` 记录 PolyWX 本地目录当前只是摘要。
  - 明确上下文压缩后必须重新核验文件内容，不能把目录名当完成证据。
- 验证：
  - `AGENTS.md` 已推送。
  - `audits/` 仍按规则不提交。

### 2026-07-01：城市证据、METAR、fetch log 和 diff stats 基座

相关提交：

- `5d2c3c8 Build METAR hourly consensus rows`
- `4b06af8 Surface hourly consensus in city evidence`
- `8ce0299 Persist structured weather fetch logs`
- `1283114 Add PolyWX-style diff stats summary`
- `1b6f53b Surface evidence diff summary in dashboard`

已完成：

- 新增/强化 `metar_reports`、`mesonet_observations`、`hourly_consensus`、`data_fetch_logs` 等数据基座。
- 城市证据 payload 开始包含逐小时 consensus、fetch log、diff summary。
- 看板 diff tab 能显示平均差、MAE/Pearson/overlap 等 PolyWX 式指标。

仍不足：

- 还需要真实 METAR raw report 解码字段更完整地展示。
- 还需要固定来源 truth 和独立 settlement day 统计进入策略 gate。
- 还需要更完整的 probability bucket evidence summary 和 market bucket 严格匹配。

## 下一步优先级

1. **数据基座优先**：补齐 station truth、METAR raw/decoded、mesonet/PWS、forecast archive、market buckets 的可复盘闭环。
2. **回放优先**：从“模拟买入记录”升级为盘口驱动 replay，包含 best bid/ask、orderMinSize、tick、staleness、成交失败和退出流动性。
3. **策略验证优先**：按城市、站点、数据源、时间窗口、价格桶、spread、低价尾桶分组，证明 allowed 组优于 blocked 组。
4. **看板服务策略**：继续像 PolyWX 一样展示证据，但 WeatherBot 的核心仍是交易审计、paper/live gate 和风险控制。
5. **实盘保持锁定**：直到连续 paper 验证、truth coverage、dry-run 和 canary gate 全部过关。

## 每轮更新模板

```text
### YYYY-MM-DD：本轮标题

- 目标：
- 改动：
- 验证：
- 结论：
- 下一步：
- 相关提交：
```

### 2026-07-02：Layer 3.5 Open-Meteo 多模型 collector
- 目标：在 Layer 2.5 五城市 METAR 历史回填完成后，补强 Layer 3 真实一手预报输入；新增 Open-Meteo typed collector，让 `forecast_runs` / `forecast_members` 不再只依赖 `polywx_forecast` 这类二手展示值。本轮不新增 Build Order 层，不改 Layer 4/5/6 schema，不改前端，不接入 `production_actions` 自动流程，不触发实盘或自动模拟。
- 改动：新增 `weatherbot_v3/openmeteo.py`，包含官方 Forecast API 与 Ensemble API 请求、城市模型 allowlist、CONUS HRRR/NBM/JMA 探测、按城市本地日窗口聚合 daily high、run/member 转换、同小时幂等 run_key、`raw_response_hash` 审计、`meta.inferred_run_at`、fetch log 与 dry-run；`weatherbot_v3/cli.py` 新增显式命令 `openmeteo-fetch`，支持 `--city`、`--limit-cities`、`--all-cities`、`--dry-run`、`--ensemble`、`--forecast-days`；`tests/test_v3_core.py` 新增 5 条 Open-Meteo 回归测试。
- 模型探测结论：官方 Forecast API 实测可用 `ecmwf_ifs025`、`ecmwf_aifs025_single`、`gfs_seamless`、`ncep_hrrr_conus`、`ncep_nbm_conus`、`icon_seamless`、`gem_seamless`、`jma_seamless`；旧候选 `gfs_nbm` 失败，实际 NBM 名称为 `ncep_nbm_conus`。Ensemble API 实测 ECMWF/GFS/ICON 有成员；HRRR/NBM/GEM/AIFS 在 ensemble endpoint 只返回 deterministic 字段，没有 member keys，因此 `--ensemble` 仅抓 `ecmwf_ifs025`、`gfs_seamless`、`icon_seamless`，不伪造成员。
- 数据填补：先跑 Chicago deterministic 小冒烟，成功写入 51 个 Open-Meteo run/member；随后跑 5 城市 deterministic：Chicago、Tokyo、Atlanta、NYC、Dallas，成功写入 252 个 fresh training runs/members，失败 0；再跑 Chicago ensemble，成功写入 24 个 ensemble runs 和 952 个 members，失败 0。SQLite 复核：Open-Meteo runs=276、members=1,204；5 城市覆盖为 Atlanta 51、Chicago 75、Dallas 51、NYC 51、Tokyo 48；Chicago `GET /api/forecasts?city=chicago&target_date=2026-07-02` 返回 runs=10、members=126。
- 本地日聚合：collector 请求 `timezone=UTC` 和 hourly fields，不使用 Open-Meteo daily max；在代码里按城市 timezone 切分本地日并计算 high，避免 UTC-day max 与 Polymarket 本地结算日错位。Open-Meteo 原始温度以 C 保存在 raw/meta/source_unit，`forecast_runs.unit` 与 `mean_high` / member high / hourly `temperature_2m` 按城市交易单位写入，保持现有 DEB 与 market bucket 的 F/C 对齐。
- 一次性对照限制：ClaudeCode 计划里的 “Chicago 2026-07-01 12:00 local” 对照不能严格完成，因为 Open-Meteo 免费 forecast endpoint 是前向预报，不提供该历史 run archive；当前 IEM METAR 回填窗口与新 forecast 可用窗口也没有 12:00 local 的同小时重叠。本轮改为记录 Open-Meteo source/readiness/API 快照；后续需要先刷新当日 METAR 并重建 hourly_consensus，再做同小时 METAR / Open-Meteo / PolyWX 横向对照。
- 验证：新增 Open-Meteo targeted tests 5 OK；`python -m unittest tests.test_v3_core` 115 OK（仍有既有 sqlite ResourceWarning 噪声）；`python -m unittest tests.test_polywx_contract` 7 OK；`npm run build` 通过（仍有既有 Browserslist/chunk warning）；`/api/dashboard` 约 395.9ms，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`；`git diff --check` 仅 LF/CRLF warning。
- 当前可用性结论：Layer 3.5 已把 5 个主力城市升级为多模型、一手、可审计、training_eligible 的 Open-Meteo 输入源，能支撑后续 DEB 权重、模型分歧、bias/residual 校准与 signal decision；这仍是数据基座，不证明策略已有 edge，也不会自动买入。
- 剩余阻塞：`forecast_runs` readiness 仍因其余 15 城市缺 fresh forecast 而 blocked；Open-Meteo 免费 API 不是历史 forecast archive，不能替代真正的 historical NWP replay；`meta.inferred_run_at` 是文档频率推断而非 provider 暴露的真实 run time；sqlite ResourceWarning 仍需单独治理。
- 下一步：基于 5 城市 METAR + Open-Meteo 多模型输入，重建/刷新 Layer 4 `hourly_consensus` 与 `daily_max_predictions`，让 DEB 使用新 source 进行残差和模型分歧校准；之后再进入 Layer 6 完整 `signal_decisions`。
- 相关提交：未提交；提交时只 stage `weatherbot_v3/openmeteo.py`、`weatherbot_v3/cli.py`、`tests/test_v3_core.py`、`PROJECT_PROGRESS_CN.md`，不要 stage `data/`、`audits/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`、`.tmp-tests/`。
### 2026-07-02：Layer 5 market_buckets 市场桶数据层
- 目标：按 `AGENTS.md` Build Order 继续 Layer 5，只补 `market_buckets` 的 SQLite schema、解析/collector、只读 API、readiness stage 和测试；不触碰 Layer 6 信号决策、不改右侧执行工作台、不启用自动抓取或实盘。
- Build Order layer：Layer 5 — `market_buckets` with strict bucket matching, tick size, `orderMinSize`, `negRisk`, token id, and orderbook metadata。
- Layer 0 前置核验：复核 `audits/polywx-firecrawl-2026-07-01/MANIFEST.json`：`generated_at=2026-07-01T20:51:44.375748+08:00`，本轮约 `0.52` 天龄，`files=17`，`five_tabs=true`，`hourly_chart=true`，`xhr_response_bodies=true`，`api_endpoints=true`。结论：本轮复用现有 PolyWX corpus，不重复 Firecrawl。
- 改动：新增 `market_buckets` 表和读写摘要函数；新增 `weatherbot_v3/market_buckets.py` 解析 Polymarket Gamma-like payload；新增只读 `GET /api/market-buckets`；readiness 增加 `market_buckets` stage；CLI 增加 `market-buckets-sync`；production action 增加 `sync_market_buckets`；补 7 个 Layer 5 回归测试。
- 验证：Targeted Layer 5 tests 7 OK；`python -m unittest tests.test_v3_core` 105 OK；`python -m unittest tests.test_polywx_contract` 7 OK；`npm run build` 通过；8765 `/api/dashboard` 约 `194.6ms`，`scanner_status=stopped`、`is_running=false`、`production_running=false`，`/api/market-buckets` 返回 `ok=true`；`git diff --check` 仅 LF/CRLF 提示。
- 当前可用性结论：Layer 5 已具备把 Polymarket 天气 outcome/token/盘口约束沉淀成可审计市场桶的基础能力；这仍是数据基座层，不证明策略有 edge，不解锁自动实盘。
- 剩余阻塞：collector 当前只同步本地已落库 Gamma-like payload；后续仍需显式市场发现/详情抓取 action。历史盘口 replay、真实成交/退出流动性和 Layer 6 `signal_decisions` 尚未完成。sqlite ResourceWarning 仍需单独治理。
- 下一步：进入 Layer 6 `signal_decisions`，把 Layer 4 小时证据、Layer 5 market bucket、distribution、model-market edge、execution gate 和 skip/buy 原因链沉淀为可复盘决策表。
- 相关提交：`4b8d60a Add market buckets data layer`；`41fc301 Record market buckets layer hash`。

### 2026-07-02：Layer 2.5 METAR 历史批量回填能力（probe 完成，未回填）
- 目标：补强既有 Layer 2 `metar_reports` 数据密度；新增 IEM ASOS 历史批量回填能力，但本轮只做到代码、测试、站点 probe，不执行 30 天真实回填，不新增 Build Order 层。
- 改动：`weatherbot_v3/metar.py` 新增 IEM ASOS collector、CSV parser、F->C 转换、`iem-asos-csv-v1` parser_version、`station_id + report_time` 幂等 key、partial/failed/skip 处理、raw CSV 落盘、hour coverage 与 Chicago 字段可用率统计、结构化 `data_fetch_logs`；`weatherbot_v3/cli.py` 新增 `metar-backfill --probe-stations/--all-cities/--dry-run/--limit-cities`；`tests/test_v3_core.py` 新增 5 个 Layer 2.5 回归测试。
- 数据源结论：IEM ASOS 官方页面显示当前归档有延迟，probe 使用 6 小时滞后窗口；探测请求使用 `nometa=yes + data=all + report_type=3/4`，实测能返回无表头数据行。慢速 probe 已写入 `data/iem_asos_raw/probe_report.json`（gitignore），结果：Chicago=KORD、Tokyo=RJTT、Atlanta=KATL、NYC=KLGA、Dallas=KDAL，全部 ready。
- 验证：`python -m unittest tests.test_v3_core` 110 OK（仍有既有 sqlite ResourceWarning 噪声）；`python -m unittest tests.test_polywx_contract` 7 OK；`npm run build` 通过（仍有既有 Browserslist/chunk warning）；`/api/dashboard` 约 244.7ms，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`；`git diff --check` 仅 LF/CRLF warning。
- 当前可用性结论：Layer 2.5 现在具备可手动触发的 IEM ASOS 历史回填能力，且 probe 已确认 5 个主力城市站点可取数；但真实 30 天回填尚未执行，需要用户批准后再跑，不影响实盘锁定状态。
- 剩余阻塞：尚未回填 30 天数据，Chicago routine 字段可用率阈值尚未用真实 30 天样本验收；Tokyo 等非美国站点虽 probe 可取数，但后续仍需确认长期字段完整度；sqlite ResourceWarning 仍待单独治理。
- 下一步：用户批准后运行 `python -m weatherbot_v3.cli metar-backfill --city chicago --days 3` 做真实小冒烟，再决定是否执行 5 城市 30 天回填；回填完成后记录 coverage_pct 与字段低于阈值原因。
- 相关提交：未提交；本轮不要 stage `data/iem_asos_raw/`、`audits/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`。

### 2026-07-02：Layer 2.5 METAR 30 天历史回填完成（5 城市）
- 目标：补齐上一轮未完成的 Layer 2.5 数据密度 A 段；先把 METAR 历史样本从 rows=0 变成可用于 Layer 4 residual / Layer 6 校准的真实观测基座，再进入 Open-Meteo Layer 3.5。
- 改动：本轮只修正 `weatherbot_v3/metar.py` parser 兼容性，IEM `metar` 字段可能直接以 `KORD 282151Z ...` 开头而不带 `METAR ` 前缀；同时修正字段可用率统计读取嵌套 `raw_json.payload` 的逻辑。未改前端、未改 Layer 3/4/5/6 schema、未触发 legacy loop、未打开 live/auto simulation。
- 数据填补：先跑 `metar-backfill --city chicago --days 3` 冒烟，修复 parser 后 Chicago 3 天 77 条全部 parsed；随后执行 5 城市 30 天回填，IEM 原始 CSV 落在 `data/iem_asos_raw/`（gitignore）。SQLite 复核：IEM ASOS rows=4,744，`metar_reports` total=4,746，IEM parse_status=`parsed` 4,744；城市分布为 Atlanta/KATL 795、Chicago/KORD 855、Dallas/KDAL 875、NYC/KLGA 779、Tokyo/RJTT 1,440，时间窗约 `2026-06-01T21:30Z` 到 `2026-07-01T21:00Z`。
- 覆盖验收：CLI 回填摘要显示 5 城市 hour coverage 均约 99%（Chicago 99.17%、Tokyo 99.31%、Atlanta 99.17%、NYC 99.03%、Dallas 99.03%）；Chicago routine 字段可用率全部达标，temperature/dew_point/wind_speed/visibility/raw_text/cloud_coverage=100%，wind_direction=99.41%，未出现低于阈值字段。
- 验证：进程检查确认无 `metar-backfill` 残留，仅有 dashboard uvicorn；`python -m unittest tests.test_v3_core` 110 OK（仍有既有 sqlite ResourceWarning 噪声）；`python -m unittest tests.test_polywx_contract` 7 OK；`npm run build` 通过（仍有既有 Browserslist/chunk warning）；`/api/dashboard` 约 335.5ms，`scanner_status=stopped`、`is_running=false`。
- 当前可用性结论：Layer 2.5 对 5 个主力城市已从“结构就绪但样本稀薄”升级为“真实历史观测可用”；这能支撑后续 DEB residual 和 bias 校准的第一批样本，但还不能证明交易策略有 edge，也不会自动买入。
- 剩余阻塞：`data-readiness` 的 observations stage 仍会因为其余 15 个城市未回填而标 blocked；生产级全市场覆盖后续还需要扩到全部目标城市。5 城市回填命令耗时较长，shell 返回过 timeout 124，但 JSON 输出和 DB 复核均确认已完成。
- 下一步：进入 Layer 3.5 Open-Meteo 多模型 collector；计划需纳入本轮前置结论：按城市本地日窗口聚合 daily max，CONUS 优先探测 HRRR/NBM 模型名，幂等键按 city/source/target_date/model/retrieved_hour，raw_response_hash 仅作审计，`inferred_run_at` 写入 meta。
- 相关提交：未提交；不要 stage `data/iem_asos_raw/`、`audits/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`、`.tmp-tests/`。
### 2026-07-02：Layer 4 DEB 日最高温预测扩展 + Layer 6 Gaussian 概率骨架
- 目标：新增 PolyWX 风格 Daily Max Prediction / Probability buckets 底座：持久化日最高温 `(mu, sigma)`，并用 Gaussian CDF 对 Layer 5 市场桶积分，作为后续 `signal_decisions` 的可审计概率输入；本轮不改前端、不触发 paper/live 执行。
- Build Order layer：Layer 4 扩展（`daily_max_predictions` 与 `weatherbot-deb-v1` 生成器）+ Layer 6 直接消费者骨架（`signal_decisions` distribution JSON 字段与 bucket probability 函数）。
- Layer 0 前置核验：`audits/polywx-firecrawl-2026-07-01/MANIFEST.json` 显示 `generated_at=2026-07-01T20:51:44.375748+08:00`、`five_tabs=true`、`hourly_chart=true`、`probability_buckets=true`、`xhr_response_bodies=true`、`api_endpoints=true`、`files=17`。`/api/prediction` 证据暴露 `generated_at/mu/sigma/method/buckets/observed`，但未暴露内部权重、sigma 公式、完整更新频率；因此本轮实现标记为 WeatherBot 自主 `weatherbot-deb-v1`，不冒充 PolyWX DEB-v1。
- 改动：`AGENTS.md` 补充 daily max 与概率规则；`weatherbot_v3/db.py` 新增 `daily_max_predictions` 与 `signal_decisions` 概率 JSON 字段写入；`weatherbot_v3/deb.py` 新增 DEB 生成器、Gaussian CDF 桶积分、开口桶处理、单位转换、观测最高温下限与 decision skeleton；`dashboard_server.py` 新增 `GET /api/daily-max-predictions`、`POST /api/daily-max-predictions/build`、`GET /api/bucket-probabilities`；`tests/test_deb_gaussian.py` 新增 5 条回归。
- 3 城市 x 3 日期一次性对照：Chicago 2026-07-01 WeatherBot `97.12F / sigma 0.90F` vs PolyWX `94.94F`，差异约 +2.2F；Chicago 2026-06-30 `95.57F / 0.90F`；Chicago 2026-06-24 `70.73F / 1.56F`；Tokyo 2026-07-01 `27.16C / 0.50C`；Tokyo 2026-06-30 `26.10C / 0.50C`；Tokyo 2026-06-24 未生成，原因 `missing_forecast_runs`；Atlanta 2026-07-01 `95.72F / 1.17F`；Atlanta 2026-06-30 `92.33F / 3.02F`；Atlanta 2026-06-24 `83.62F / 1.26F`。除 Chicago 2026-07-01 外，现有 PolyWX 语料未捕获可比 DEB 展示值。
- 验证：`python -m unittest tests.test_deb_gaussian` 5 OK；`python -m unittest tests.test_v3_core` 105 OK（仍有既有 sqlite ResourceWarning 噪声）；`npm run build` 通过（仍有既有 Browserslist/chunk warning）；`/api/dashboard` 约 `249.6ms`，`scanner_status=stopped`，`is_running=false`，`production_running=false`，`auto_refresh_running=false`；API route snapshot 确认三条新路由已注册；`git diff --check` 仅 LF/CRLF warning。
- 当前可用性结论：系统现在有了可持久化的 `(mu, sigma)` 与市场桶概率积分底座，可用于后续 Layer 6 edge 审计；但该能力仍是概率层骨架，不会自动买入，也不能证明策略已有 edge。
- 剩余阻塞：PolyWX 精确 DEB 内部公式未知；WeatherBot 当前 DEB 权重/残差校准仍简单，Chicago 对照已有约 2.2F 差异；Tokyo 2026-06-24 缺 forecast_runs；历史 truth 与独立 settlement day 样本仍不足；sqlite ResourceWarning 仍需单独治理。
- 下一步：继续 Layer 6，把 `daily_max_predictions + market_buckets + orderbook constraints` 写成完整 `signal_decisions` 决策链：model probability、market implied probability、edge、gate reasons、paper/live blocked reason，仍不触发自动执行。
- 相关提交：未提交；待验收后只 stage `AGENTS.md`、`PROJECT_PROGRESS_CN.md`、`dashboard_server.py`、`weatherbot_v3/db.py`、`weatherbot_v3/deb.py`、`tests/test_deb_gaussian.py`。
### 2026-07-02：Layer 4 v2 Open-Meteo 多模型小时共识 + DEB v2 重建
- 目标：在 Layer 2.5 五城市 METAR 历史回填和 Layer 3.5 Open-Meteo 多模型 collector 就位后，刷新 Layer 4 `hourly_consensus` 与 `daily_max_predictions`，让后续 Layer 6 可以读取可审计的小时残差、模型分歧和日最高温 `(mu, sigma)`。本轮不新增 Build Order 层，不改前端，不触发 paper/live 执行，不启用 legacy loop、auto simulation 或自动刷新。
- 改动：`weatherbot_v3/hourly.py` 将 Open-Meteo typed sources 作为 primary，`polywx_forecast` 只作 explicit fallback；小时共识改为 primary model median，并写入 `forecast_spread`、`forecast_member_count`、`consensus_method`；`weatherbot_v3/db.py` 增加批量 upsert，避免逐行 SQLite 写入导致长时间运行；`weatherbot_v3/cli.py` 修正 `hourly-consensus-build --days`，现在会按城市限定目标日期集合，而不是全库扫描；`weatherbot_v3/deb.py` 升级为 `weatherbot-deb-v2`，加入 CONUS/Tokyo/global 初始权重、成员日最高温、spread/history sigma 分解、同小时幂等 issued_at、观测最高温下限和 bias 样本门槛；`tests/test_v3_core.py` 增加 Open-Meteo primary、PolyWX fallback、station-local day、观测 floor、幂等与 CLI 回归测试。
- 关键修复：排查到此前长时间运行的两个原因：一是 `--days` 没有真正限制目标日期，二是 `hourly_consensus` 逐行打开 SQLite 写入；均已修复。另发现 IEM ASOS `metar_reports.temperature/dew_point` 是摄氏度，而 Chicago/Atlanta/NYC/Dallas 交易单位是华氏度，旧逻辑会把 25C 当 25F 造成约 -50F 假残差；本轮已在 hourly/DEB 中按站点与交易单位转换，并让 DEB bias 只使用 `openmeteo_multi_model` 且 residual 合理的样本。
- 数据刷新：重新构建 5 城市 30 天目标窗口，`hourly-consensus-build --limit-cities 5 --days 30` 完成：`target_pairs=150`、`forecast_points=1680`、`observation_points=2712`、`rows_built=3552`、`rows_upserted=3552`。readiness 仍为 blocked，原因是历史 forecast 覆盖不完整：`hourly_forecast_city_date_gap=112`、`hourly_partial_rows=3063`。重新构建 DEB v2：请求 75 个 city/date，成功存储 75，失败 0。
- 验证：`python -m unittest tests.test_v3_core` 通过，121 tests OK；`python -m unittest tests.test_polywx_contract` 通过，7 tests OK；`python -m unittest tests.test_deb_gaussian` 通过，5 tests OK；`npm run build` 通过，仍有既有 Browserslist 过期与 chunk size warning；`git diff --check` 通过，仅 Windows LF/CRLF 提示；临时 8766 最新后端验证 `/api/hourly-consensus?city=chicago&target_date=2026-07-02` 返回 24 行，首行 `forecast_source=openmeteo_multi_model` 且有 `forecast_spread`；`/api/daily-max-predictions?city=chicago&target_date=2026-07-08` 返回 `weatherbot-deb-v2`，`mu=86.972F`、`sigma=3.8648F`、`member_count=4`。
- 质量快照：DEB v2 五城市均已生成，Chicago/Atlanta/Dallas/NYC 使用 F，Tokyo 使用 C；`bias_sample_count` 当前全部为 0，这是预期结果，因为 Open-Meteo 是前向免费 forecast，不提供足够历史 NWP replay，尚未积累同源 forecast+observed 独立样本。残差 sanity check 已清除单位错误：当前 `abs(residual)>25` 行数为 0；按城市 residual 范围约 Atlanta `[-11.20,12.16]F`、Chicago `[-13.36,15.26]F`、Dallas `[-14.18,9.24]F`、NYC `[-11.28,12.06]F`、Tokyo `[-9.00,7.20]C`。
- 当前可用性结论：Layer 4 v2 已可作为后续信号层的数据输入，能展示/审计小时预报、观测、模型分歧和 DEB 高斯参数；但它仍是数据与概率基座，不证明策略有 edge，不解锁自动实盘。
- 剩余阻塞：历史 forecast archive 不完整，导致许多 city/date 仍 partial；DEB bias 还没有真实同源样本，短期只能使用模型权重、spread 和保守 sigma；sqlite `ResourceWarning: unclosed database` 仍是既有噪声，需后续专项清理；当前 8765 存在旧 dashboard 进程重复，验收最新代码时优先用临时端口或重启后端。
- 下一步：进入 Layer 6 `signal_decisions`，把 `daily_max_predictions + market_buckets + orderbook constraints` 合成可复盘决策链：model bucket probability、market implied probability、edge、gate reasons、paper/live blocked reasons。仍不触发任何执行动作。
- 相关提交：未提交；提交时只 stage 本轮代码/测试/进度文件，不提交 `data/`、`audits/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`、`.tmp-tests/`。

### 2026-07-02：Layer 4 v2 收尾复核与 Layer 6 前置检查
- 目标：复核 ClaudeCode 基于 `AGENTS.md` 和本进度台账给出的 Layer 6 前置建议，按本地真实状态判断哪些已完成、哪些需要修补；本轮不进入 Layer 6 编码，不改前端，不触发 paper/live 执行。
- 复核结论：Layer 4 v2 本体已完成并可作为 Layer 6 输入；ClaudeCode 提到的 3 个前置项中，`Chicago v1 vs v2 sanity` 确实缺记录，本轮已补；8765 旧进程问题已清理并重新核验；“必须立刻 commit”暂不执行，因为当前工作区混有 Layer 2.5、Layer 3.5、Layer 4、Layer 5 的未提交改动和新文件，贸然只 stage Layer 4 v2 容易漏掉依赖，后续应单独做一次提交拆分/备份轮。
- Chicago sanity：SQLite 中仍保留 `chicago / 2026-07-01` 的 v1 与 v2 行，无需回滚旧代码。对照 PolyWX 展示值 `94.94F`：v1 为 `97.1156F / sigma 0.90F`，绝对误差 `2.1756F`；v2 为 `95.0000F / sigma 1.5640F`，绝对误差 `0.0600F`。结论：v2 在唯一可比 PolyWX 展示点上明显优于 v1，不阻塞进入 Layer 6。
- 后端进程：已停止旧的 8765 uvicorn 进程并用当前代码重启。Windows 进程表显示两个 Python PID：`26500` 为父进程，`4960` 为子进程；`Get-NetTCPConnection -LocalPort 8765` 确认只有 `4960` 在监听 `127.0.0.1:8765`，因此当前不是两个后端抢端口。后续验收应以“单监听端口/单 uvicorn 进程树”为准，而不是只数 Python PID。
- 运行态验证：`/api/dashboard` 约 `1222.5ms` 返回，`stats.scanner_status=stopped`、`stats.is_running=false`、`production_refresh.running=false`、`production_refresh.auto_refresh_running=false`；`/api/hourly-consensus?city=chicago&target_date=2026-07-02` 返回 24 行，首行 `forecast_source=openmeteo_multi_model`、`consensus_method=median_primary_v1`、有 `forecast_spread`；`/api/daily-max-predictions?city=chicago&target_date=2026-07-01` 返回 2 行，latest 为 `weatherbot-deb-v2`。
- 当前可用性结论：可以进入 Layer 6 设计/实现，但 Layer 6 的正确目标是“记录可复盘决策链和 gate reasons”，不是放开交易。由于 `bias_sample_count=0` 仍是诚实状态，Layer 6 里 live 应默认 blocked，paper 可以作为观察/模拟候选。
- 剩余阻塞：工作区尚未拆分提交；historical NWP replay 仍缺失，DEB bias 还不能真实校准；8765 dashboard 已刷新为当前代码，但浏览器若仍缓存旧页面，需手动刷新前端；sqlite `ResourceWarning` 仍待专项清理。
- 下一步：先做一次 Git 提交拆分/备份轮，或在用户确认后进入 Layer 6 `signal_decisions`。Layer 6 开始前必须先输出计划，重点覆盖 schema、decision builder、gate reason、read-only API、CLI、readiness 和测试。
- 相关提交：未提交。
### 2026-07-02：Layer 6 signal_decisions 决策链落库
- 改动：完成 Layer 6 `signal_decisions` 决策链的后端落地。`weatherbot_v3/db.py` 扩展完整 decision schema、幂等 `decision_id`、可选 `path` 的 market bucket 写入/读取、`upsert_signal_decision_record()` 与 `list_signal_decisions()`；新增/完善 `weatherbot_v3/signals.py`，把 `daily_max_predictions + market_buckets` 合成 model probability、market implied probability、edge、orderbook snapshot、paper/live gate reasons、evidence links；`dashboard_server.py` 新增 `GET /api/signal-decisions`、`GET /api/signal-decisions/{decision_id}`、`POST /api/signal-decisions/build`；`weatherbot_v3/qualification.py` 新增 Layer 6 readiness stage 与 next action；`weatherbot_v3/cli.py` 已有 `signal-decisions-build` 命令并接入 readiness。
- 验证：`python -m unittest tests.test_v3_core` 126 OK（仍有既有 sqlite ResourceWarning 噪声）；`python -m unittest tests.test_polywx_contract` 7 OK；`python -m unittest tests.test_deb_gaussian` 5 OK；`npm run build` 通过（仍有既有 Browserslist/chunk warning）；`git diff --check` 仅 LF/CRLF warning。临时 8766 后端验证：`/api/dashboard` 约 362.4ms，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`；`/api/signal-decisions?city=chicago&target_date=2026-07-02` 返回 OK、count=0；`/api/data-readiness` 能看到 `signal_decisions` stage。
- 结论：Layer 6 代码与测试已可用，但它仍是“只读决策证据层”，不会触发 paper/live 执行。设计上 `bias_sample_count < 7` 只阻塞 live，不阻塞 paper 观察候选；`LIVE_TRADING=false` 仍保持锁定。当前真实库 `signal-decision-v1` 行数为 0，不是 Layer 6 代码失败，而是没有可用的 Layer 5 市场桶输入。
- 阻塞：本地真实库 `daily_max_predictions` 已有 83 行，但 `market_buckets` 只有从旧 signals/markets 解析出的 48 行，`matched=0`，主要缺 `tick_size`、`order_min_size`、`best_ask/price`，且日期集中在 2026-06-16 到 2026-06-20，与当前 Open-Meteo/DEB 的 2026-07 预测窗口不重合。因此真实库暂时无法生成可交易的 Layer 6 decisions。历史 skeleton `signal_decisions` 行已在 readiness 中过滤，只统计 `decision_version=signal-decision-v1`。
- 下一步：先补 Layer 5.5：用当前 Polymarket Gamma/CLOB 活跃天气市场重新同步 `market_buckets`，必须带 YES token、event_url、bestBid/bestAsk、tick size、orderMinSize、quote timestamp、strict_match_status；然后重新运行 `daily-max-build` 与 `signal-decisions-build --limit-cities 5 --days 7`，生成真实可审计 decisions。前端 Layer 7 再读取这些决策展示，不应提前接执行按钮。

### 2026-07-02：Layer 5.5 active Polymarket weather market_buckets 同步 + Layer 6 决策重建
- 目标：复核 ClaudeCode 基于 `AGENTS.md` 与进度文件给出的 Layer 6 建议，并按本地真实状态补齐缺口。本地审计结论是：Layer 6 代码已完成，真正阻塞是 Layer 5 `market_buckets` 仍是旧窗口/未 matched 数据，无法给当前 DEB 预测窗口生成真实决策。因此本轮归属为 Layer 5 补强 + 直接消费者 Layer 6 重建；不改前端、不触发 paper/live 执行、不启用 legacy loop、LIVE_TRADING 仍保持 false。
- 改动：`weatherbot_v3/market_buckets.py` 新增 active Polymarket weather sync，使用 Gamma event slug 路径读取活跃最高温事件，并用 CLOB `/book` 补齐 YES token、bestBid/bestAsk、spread、tick size、orderMinSize、orderbook snapshot；修复 Gamma 问题文本中 `Â°F`/度数符号解析。`weatherbot_v3/cli.py` 扩展 `market-buckets-sync --active-weather`，并修复 `signal-decisions-build` 默认目标选择，优先选择 `daily_max_predictions` 与 `market_buckets` 实际重叠的 city/date，避免误选未来无市场桶日期。`dashboard_server.py` 新增手动 `POST /api/market-buckets/sync-active`，不会在后端启动时自动运行。`weatherbot_v3/polymarket.py` 修复 CLOB quote spread，优先用真实 bestAsk-bestBid。`weatherbot_v3/db.py` 收紧 `dump_json(..., allow_nan=False)`，将 NaN/Infinity 标准化为 null，修复 `/api/signal-decisions` 因开口桶 Infinity 返回 500 的问题。`tests/test_v3_core.py` 增加 active Gamma/CLOB ingest、dry-run、不重复写入、度数编码解析、CLOB spread、Layer 6 target overlap 等回归。
- 真实数据：Gamma `search=` 对天气市场不可靠，本轮确认可用路径是 `https://gamma-api.polymarket.com/events/slug/highest-temperature-in-{city}-on-{month}-{day}-{year}`。已同步 5 城市（chicago/tokyo/atlanta/nyc/dallas）x 2 天（2026-07-02/2026-07-03）：requested_events=10，events_found=10，markets_seen=110，stored=110，strict matched=110，orderbook_ok=110。当前 `market_buckets` 总数 158，其中当前窗口 110 条且 matched=110。随后重建 Layer 6：requested=10，stored=110，decision_count=110，failed=0。
- 决策快照：当前 110 条真实窗口 `signal_decisions` 中，`paper_allowed/buy=1`，`skip=edge_below_min=10`，`paper_blocked=99`，`live_decision=blocked=110`。主要阻塞为 spread_too_wide=81、low_price_tail_bucket=18、edge_below_min=10、insufficient_bias_samples=1。唯一 paper buy 候选为 Dallas 2026-07-02，96-97F 桶，ask=0.23，model_probability≈0.2604，edge≈0.0304；live 仍因 insufficient_bias_samples 与 live_trading_disabled 被锁住。
- 验证：`python -m unittest tests.test_v3_core` 130 OK；`python -m unittest tests.test_polywx_contract tests.test_deb_gaussian` 12 OK；`npm run build` 通过，仍有既有 Browserslist 过期与 Vite chunk size warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。临时 8766 最新后端验证：`/api/dashboard` 快速返回，`/api/market-buckets?city=chicago&target_date=2026-07-02` 可读，`/api/signal-decisions?city=chicago&target_date=2026-07-02` 返回 200 且 count=5（limit=5）；临时进程已关闭。完整测试中仍有既有 `weatherbot_v3/hourly.py` sqlite ResourceWarning 噪声，未影响通过。
- 当前可用性结论：市场桶缺失这个关键 blocker 已解除，WeatherBot 现在能把真实 Polymarket 活跃天气市场、CLOB 盘口、DEB 高斯概率和 Layer 6 gate reasons 接起来，形成可审计的模拟/观察信号链。它仍不是可自动实盘赚钱版本：live 全部保持 blocked；bias_sample_count 仍不足；高 spread 和低价尾部桶过滤会大量拦截；策略 edge 仍需通过 paper settlement 验证。
- 剩余阻塞与下一步：下一轮优先把 Layer 7 看板接入这些真实 `market_buckets` 与 `signal_decisions`，让 UI 能展示真实 event_url/token/orderbook/edge/gate reasons；或者进入 Layer 8 paper executor，把这 1 条 paper buy 候选走完整模拟订单生命周期。另一个必要技术债是清理 `hourly.py` 的 sqlite ResourceWarning。提交时不要 stage `data/`、`audits/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`、`.tmp-tests/`。

### 2026-07-02：Layer 7 前 7-commit stack 落地
- 目标：进入 Layer 7 之前，先把当前 Layer 2.5/3.5/4/5.5/6 相关未提交工作拆成 7 个本地 commit，避免继续把数据基座、概率层、市场桶、决策层和共享 API/CLI 改动堆在脏工作区里。本轮只做 Git 落盘与验证，不改前端 UI，不启动 legacy loop，不触发 live trading。
- Build Order 范围：提交整理轮，不新增功能层；覆盖已完成但未落地的 Layer 2.5 METAR backfill、Layer 3.5 Open-Meteo collector、Layer 4 DEB probability、Layer 5.5 active market buckets、Layer 6 signal decisions 及其共享接口/测试。
- 7 个 commit：`032879a Update agent rules for probability layers`；`7c93855 Add METAR history backfill collector`；`5332298 Add Open-Meteo multi-model collector`；`3c9d5cd Add DEB daily max probability layer`；`fe54634 Sync active Polymarket weather buckets`；`10ae655 Add signal decision engine`；第 7 个为本 ledger 所在的 integration commit（amend 后最终 hash 以 `git log -7` 与本轮最终回复为准）。
- 验证：`python -m unittest tests.test_v3_core` 130 OK；`python -m unittest tests.test_polywx_contract tests.test_deb_gaussian` 12 OK；`npm run build` 通过，仍有既有 Browserslist 过期与 Vite chunk size warning；`git diff --check` 通过。PolyWX corpus 复核：`audits/polywx-firecrawl-2026-07-01/MANIFEST.json` generated_at=`2026-07-01T20:51:44.375748+08:00`，five_tabs/hourly_chart/probability_buckets/xhr_response_bodies/api_endpoints 均为 true。临时 8766 后端验证：`/api/dashboard` 约 76.3ms，scanner_status=stopped，is_running=false，production_running=false，auto_refresh_running=false；`/api/market-buckets?city=chicago&target_date=2026-07-02` 可读；`/api/signal-decisions?city=chicago&target_date=2026-07-02&limit=5` 返回 200，count=5，首条 gate_status=paper_blocked。
- 当前可用性结论：Layer 7 前的底层代码和接口已经有本地 commit 栈可回滚/备份；当前系统仍是可观察、可模拟、可审计的生产化雏形，不是自动实盘赚钱版本。live trading 仍锁定，auto refresh/legacy scan 未开启。
- 剩余阻塞与下一步：工作区仍保留未跟踪的本地 `audits/` 与 `.tmp-tests/` 研究/临时文件，按规则不提交。进入 Layer 7 前可选择先 push 当前分支到 GitHub；Layer 7 的下一步是只读接入 Layers 1-6 的真实 API，把 market buckets、signal decisions、gate reasons、event_url/token/orderbook 展示到 PolyWX-shaped dashboard。

### 2026-07-02：Layer 7 PolyWX-shaped dashboard 只读接入 Layer 5/6 决策数据
- 目标：进入 Build Order Layer 7，只改 PolyWX-shaped dashboard 的只读数据接入与展示，不新增抓取层、不触发 paper/live 执行、不改右侧执行工作台；复用 `audits/polywx-firecrawl-2026-07-01/` 作为新鲜 PolyWX corpus，不重复 Firecrawl。
- Build Order layer：Layer 7 — PolyWX-shaped dashboard reads only from Layers 1-6。Layer 0 前置复核通过：`MANIFEST.json` generated_at=`2026-07-01T20:51:44.375748+08:00`，`five_tabs/hourly_chart/probability_buckets/xhr_response_bodies/api_endpoints=true`，`captured_pages=9`，`js_rendered_pages=9`。
- 改动：`frontend/src/api.ts` 新增 `fetchMarketBuckets()`、`fetchSignalDecisions()`、`fetchDailyMaxPredictions()`；`frontend/src/types.ts` 新增 `MarketBucketSummary`、`SignalDecisionSummary`、`DailyMaxPredictionSummary` 等 Layer 5/6/DEB 类型；`frontend/src/App.tsx` 为选中 city/date 增加只读 React Query，并把真实 market buckets、signal decisions、daily max prediction 传入 `WeatherPanel`；`frontend/src/components/WeatherPanel.tsx` 将首屏判断条和“当日最高温预测（DEB）/Probability buckets”模块优先接入真实 Layer 5/6 数据，展示 μ/σ、market bucket、YES token、Polymarket 链接、best bid/ask、spread、orderMinSize、tick size、depth、gate reasons，并保留旧 `city_evidence`/legacy signal fallback；`tests/test_polywx_contract.py` 更新为检查新的 Layer 7 合约。
- 验证：`python -m unittest tests.test_v3_core` 通过，130 tests OK，仍有既有 `weatherbot_v3/hourly.py` sqlite `ResourceWarning` 噪声；`python -m unittest tests.test_polywx_contract tests.test_deb_gaussian` 通过，12 tests OK；`npm run build` 通过，仍有既有 Browserslist 过期和 Vite chunk size warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。临时 8766 后端运行态：`/api/dashboard` 约 `98ms`，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`；`/api/market-buckets?city=chicago&target_date=2026-07-02` 返回 `bucket_count=11`、`matched=11`；`/api/signal-decisions?...limit=5` 返回 `count=5`；`/api/daily-max-predictions` 返回 `mu=95.648`、`sigma=2.1687686598620917`。浏览器冒烟使用临时 8766 + 5175 与本机 Chrome：页面 HTTP 200，无 console/page error，无“正在连接”，页面包含“预报/METAR/当日最高温预测/Polymarket/观察或 gate 状态”。
- 当前可用性结论：Layer 7 已能把真实 Polymarket market bucket、CLOB 盘口、DEB 高斯概率、Layer 6 gate reasons 展示进中间 PolyWX 风格城市页；这提升的是观察、审计和模拟前判断能力，不解锁实盘自动交易。右侧执行台保持原有受控状态，live trading 仍默认锁定。
- 剩余阻塞：当前 8765 可能仍有旧 uvicorn 进程，验收最新代码时建议重启后端或使用临时端口；`weather_signals` 旧摘要仍可能为 0，但 Layer 7 中间页现在可通过新 `signal_decisions` 展示真实决策；历史 NWP replay 与 DEB bias 样本仍不足，不能证明策略有稳定 edge；sqlite `ResourceWarning` 仍需后续专项清理。
- 下一步：进入 Layer 8 之前，先决定是否 push 当前本地分支到 GitHub；下一层应做 Paper execution，把 Layer 6 的 paper-allowed candidate 走完整模拟订单生命周期，同时保持 live/canary 关闭。
- 相关提交：代码提交 `349db87 Connect dashboard to market decisions`；本 ledger 条目将单独提交，避免工作记录只存在聊天里。

### 2026-07-02：Layer 8 Paper execution 生命周期与风控门禁
- 目标：进入 Build Order Layer 8，只补 paper execution 和 risk gate，不触发 live/canary，不启动 legacy loop，不开启 auto simulation，不修改右侧执行工作台 UI。Layer 0 前置复核通过：`audits/polywx-firecrawl-2026-07-01/MANIFEST.json` generated_at=`2026-07-01T20:51:44.375748+08:00`，`five_tabs/hourly_chart/probability_buckets/xhr_response_bodies/api_endpoints=true`，captured_pages=9，js_rendered_pages=9。
- 改动：`weatherbot_v3/db.py` 扩展 `paper_orders` 与 `fills` 字段，新增 Layer 8 订单/成交读写、幂等查询、open token 重复保护、`paper_execution_summary()`；新增 `weatherbot_v3/paper.py`，从 Layer 6 `signal_decisions` 生成 BUY YES paper order，检查 paper gate、token、tick、orderMinSize、price、spread、ask depth、stale book、重复 token，并记录 filled/partial/rejected、fill row、mark-to-bid unrealized PnL；`weatherbot_v3/cli.py` 新增 `paper-execute`，默认 dry-run，只有 `--apply` 写库；`dashboard_server.py` 新增 `GET /api/paper-orders` 与 `POST /api/paper-orders/execute`；`weatherbot_v3/qualification.py` 新增 `paper_execution` readiness stage，并排除旧版 legacy paper orders；`tests/test_v3_core.py` 增加 schema、fill、idempotency、blocked rejection、API/CLI 控制测试。
- 验证：`python -m unittest tests.test_v3_core` 134 OK，仍有既有 `weatherbot_v3/hourly.py` sqlite `ResourceWarning` 噪声；`python -m unittest tests.test_polywx_contract tests.test_deb_gaussian` 12 OK；`npm run build` 通过，仍有既有 Browserslist 过期与 Vite chunk size warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。8765 仍是旧进程，`/api/paper-orders` 返回 404；临时 8766 用当前代码验证：`/api/dashboard` 约 320.8ms，scanner_status=stopped，is_running=false，production_running=false，auto_refresh_running=false；`/api/paper-orders` 返回 200；Dallas 真实 paper candidate dry-run 返回 `paper_filled`，$2.00、8.695652 shares、mark-to-bid unrealized PnL `-0.086957`；`/api/data-readiness` 的 `paper_execution` stage 仍 blocked，原因 `paper_orders_missing`，并记录 `legacy_paper_orders_excluded=60`。
- 当前可用性结论：Layer 8 代码链路可用，已经能把 Layer 6 的 paper-allowed decision 变成可审计的本地模拟订单与成交记录；dry-run 证明 spread 会作为即时浮亏进入 unrealized PnL。当前真实库尚未写入新的 `paper-execution-v1` 订单，因此 paper validation 还没有正式开始。live trading 仍全锁，canary 未开放。
- 剩余阻塞：真实库中 60 条旧版 legacy paper orders 没有 decision/fill 链接，本轮只在 readiness 中排除，不做迁移；8765 需要重启才能加载新 API；策略 edge 仍未经过 14-30 天 paper settlement 验证；历史 NWP replay、truth 样本、sqlite ResourceWarning 仍是后续债务。
- 下一步：重启本地后端到当前代码后，可先用 `python -m weatherbot_v3.cli paper-execute --decision-id <id> --amount 2 --apply` 对单条 paper candidate 做受控写库，再进入 Layer 9 的 14-30 天 paper validation 统计；如果继续 UI，可把新 `/api/paper-orders` 接入右侧交易记录，但需单独一轮且保持 live locked。
- 相关提交：Layer 8 代码提交 `2fbf958 Add paper execution lifecycle`；本 ledger 条目将单独提交。

### 2026-07-02：Layer 7 PolyWX 视觉优先级收敛
- 目标：结合 ClaudeCode 建议与本地实际 UI 体验，对 Layer 7 看板做视觉/信息架构收敛；只改前端只读展示与合约测试，不改后端 schema/API，不触发抓取、paper/live 执行，不修改右侧 Execution Workbench。复用 `audits/polywx-firecrawl-2026-07-01/MANIFEST.json` 与 `SCHEMA_MAP_CN.md`，确认 corpus 覆盖 `five_tabs=true`、`hourly_chart=true`、`probability_buckets=true`、`xhr_response_bodies=true`。
- 改动：`frontend/src/App.tsx` 将左侧“推荐关注”从绿色交易提示卡改为中性 `evidence-only` 证据卡，突出城市、站点、数据年龄和当前温度，避免把推荐误读为买入信号；`frontend/src/components/WeatherPanel.tsx` 移除首屏三张 SourcePulse 大卡与重复 KPI 栅格，把预测/METAR/历史状态压到 workbench header 徽章；Forecast tab 首屏现在直接进入 Hourly Temperature 图；tab 副标删除，保留 `预报 / METAR / 历史观测 / 偏差统计 / 抓取日志` 五 tab；DEB 模块改为紧凑主数字 μ + σ，method/normalized/observed 收进 `DEB metadata`；Probability buckets 标题常驻，空态改为中性 `No probability buckets for this date.`；桶标签改为 PolyWX 风格 `95–96°F`、`96°F or above`、`88°F or below`；Polymarket 链接、YES token、盘口和 gate reasons 下沉到 `WeatherBot 附加：盘口 / token / gate` 折叠区；`tests/test_polywx_contract.py` 增加 Layer 7 视觉优先级合约断言。
- 验证：`npm run build` 通过，仍有既有 Browserslist 过期与 Vite chunk size warning；`python -m unittest tests.test_polywx_contract tests.test_deb_gaussian` 13 OK；`python -m unittest tests.test_v3_core` 134 OK，仍有既有 sqlite `ResourceWarning` 噪声；`/api/dashboard` 约 `229.4ms`，`scanner_status=stopped`、`is_running=false`、`production_running=false`、`auto_refresh_running=false`；浏览器验收 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-02`：加载后无“正在连接”长停、无后端错误、无 console error、无横向溢出，页面包含 `Daily Max Prediction (DEB)`、`Probability buckets (Gaussian)`、`evidence-only`，旧 tab 副标已消失。`git diff --check` 通过，仅 Windows LF/CRLF warning。
- 当前可用性结论：看板比上一版更接近 PolyWX 的“先看城市证据、再展开细节”路径，首屏噪声减少，概率分布和交易附加信息的边界更清楚。当前仍是观察/模拟平台，不解锁实盘，不证明策略稳定盈利。
- 剩余阻塞：当前只是视觉优先级收敛，尚未做到像素级复刻；若某城市/日期没有 Layer 5/6 桶数据，`WeatherBot 附加`不会出现，这是诚实空态而不是 UI 缺失；sqlite ResourceWarning、truth 样本不足、14-30 天 paper validation 仍未完成。
- 下一步：若继续 Layer 7，应做一次更细的截图级 UI QA（桌面 + 窄屏），再决定是否把 `/api/paper-orders` 接入右侧交易记录；若进入策略验证，则开始 Layer 9 paper validation，不打开 live/canary。
- 相关提交：未提交。

### 2026-07-03：Layer 7 PolyWX UI 二次生产化修复
- 目标：结合用户截图反馈与 ClaudeCode UI 建议，对 Layer 7 城市工作台继续做生产可用收敛；只改前端只读 UI 与合约测试，不改后端 schema/API，不触发抓取、paper/live 执行，不修改右侧 Execution Workbench。复用 `audits/polywx-firecrawl-2026-07-01/` corpus，`MANIFEST.json` 显示 `five_tabs/hourly_chart/probability_buckets/xhr_response_bodies/api_endpoints=true`，`SCHEMA_MAP_CN.md` 已覆盖 forecast/METAR/historical/fetchlog/diffstats/prediction/recommendations 字段。
- 改动：`frontend/src/App.tsx` 将左侧“推荐关注”从上一轮过弱的 `evidence-only` 卡改为 PolyWX 风格暖色重点卡，展示城市、站点、`现在` 与 `预计最高` 两个核心数字，数据年龄移到小字；城市列表默认只展示状态点、城市、站点和最新温度，F/M/H/RH/信号细节放入 hover title，减少侧栏噪声。`frontend/src/components/WeatherPanel.tsx` 将 workbench header 改为预报/METAR/历史观测三枚数据源脉搏徽章，直接展示 freshness、duration 与失败提示；移除 `PolyWX-style city workbench` 说明行；Forecast tab 的 Hourly Temperature 改为全宽组合图，温度轴跟随城市交易单位，增加图例、右侧云量百分比轴、粉色峰值标记和底部三枚紧凑 KPI；METAR/Historical/Fetch Log tab 改为全宽表格，旧右侧卡列移入折叠快照或删除；抓取日志隐藏 `0ms` 占位耗时；偏差条最大宽度收敛到 60%；DEB 卡改为 `μ ± σ` 双单位展示，Probability buckets 在 DEB μ/σ 存在但市场桶缺失时可用 Gaussian fallback，且 fallback 只允许基于 `dailyMaxPrediction.latest`，避免旧 signal distribution 画出 0°F 伪分布；尾桶标签规范为 `87°F or below/above`。`tests/test_polywx_contract.py` 更新合约断言，约束暖色推荐卡、全宽小时图、峰值标记、fetch tab 不混信号卡、Gaussian fallback 与加载态。
- 验证：`npm run build` 通过 3 次，仍有既有 Browserslist 过期与 Vite chunk size warning；`python -m unittest tests.test_polywx_contract tests.test_deb_gaussian` 13 OK；`python -m unittest tests.test_v3_core` 134 OK，仍有既有 sqlite `ResourceWarning` 噪声；`/api/dashboard` 当前 8765 约 `242.7ms`，`scanner_status=stopped`、`is_running=false`、`production_running=null`、`auto_refresh_running=null`、`fetch_log_count=50`；`git diff --check` 通过，仅 Windows LF/CRLF warning。浏览器验收 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-02`：无“正在连接”长停、无横向溢出；推荐关注/预计最高存在；旧 `PolyWX-style city workbench` 不存在；DEB 加载完成后显示 `35.36°C / 95.65°F ± 1.20°C / 2.17°F`，概率桶显示 `87°F or below`、`88°F–89°F` 等规范标签。
- 当前可用性结论：中间城市页已更接近 PolyWX 的证据工作台：首屏先看推荐关注、数据源健康、小时图、DEB 和概率桶，交易附加信息继续收进折叠区；该层提升的是观察、审计和模拟前判断，不解锁 live，不证明策略已有稳定 edge。
- 剩余阻塞：DEB/Probability 在 React Query 初始加载的前几秒仍会先显示加载态，数据返回后才出现 μ/σ 和桶；如果某 city/date 没有 daily max prediction 或 market buckets，会诚实空态；像素级完全复刻、移动端细节、右侧 `/api/paper-orders` 接入仍未做；sqlite ResourceWarning、truth 样本不足、14-30 天 paper validation 仍是后续生产验证阻塞。
- 下一步：建议继续 Layer 7 QA，把当前页面和 PolyWX 在桌面/窄屏做截图级对照，并决定是否接 `/api/paper-orders` 到右侧交易记录；或者进入 Layer 9 paper validation，开始连续模拟验证胜率/ROI/回撤，仍保持 live/canary 关闭。
- 相关提交：未提交；本轮仅修改 `frontend/src/App.tsx`、`frontend/src/components/WeatherPanel.tsx`、`tests/test_polywx_contract.py` 与本 ledger。

### 2026-07-03：Layer 7 今日日期刷新与图表布局修复
- 目标：修复用户反馈的三个可用性问题：选不到今天数据、Hourly Temperature 图底部红/蓝 residual bars 遮住横坐标、Probability buckets 图下方留白过大；同时核对“自动抓取”真实链路，避免按钮名和实际数据刷新范围不一致。
- 根因：`/api/hourly-consensus`、`/api/market-buckets`、`/api/signal-decisions`、`/api/daily-max-predictions?city=chicago&target_date=2026-07-03` 已有今日数据，但前端会把用户选择的日期强制回退到 `dashboard.citySeries` 中已有的 `availableDates`；而 `city-evidence`/dashboard cache 可能尚未包含今天，导致 UI 看起来“换不到今天”。另一个根因是 `/api/production-refresh` 仍是 v1 半链路，只跑 contracts、legacy forecast snapshot、signal migration、orderbook/readiness，没有重建 Open-Meteo、METAR recent refresh、hourly_consensus、DEB、market_buckets、signal_decisions。
- 改动：`frontend/src/components/WeatherPanel.tsx` 停止在 `selectedDate` 不属于旧 `availableDates` 时自动回退，并让缺少图表行的选中日期使用轻量 fallback；Hourly residual bars 从图表内部 absolute overlay 移到图表下方独立条带；Probability buckets 降低 chart 高度、X 轴 label 高度和底部 margin。`frontend/src/App.tsx` 与 `frontend/src/api.ts` 让所有“自动抓取”入口传入当前 `startDate/endDate`，刷新成功后同时 invalidate market buckets、signal decisions、daily max predictions。`weatherbot_v3/cli.py` 将 `run_production_refresh()` 升级为 `production-refresh-v2`：contracts_sync -> forecast_backfill -> openmeteo_fetch -> metar_refresh -> hourly_consensus -> daily_max_build -> market_buckets_sync(active weather) -> signal_decisions_build -> signal_scan/skip+migration -> orderbook_backfill。仍然是手动触发，不启动 legacy loop，不启用 auto simulation，不打开 live/canary。
- 验证：`python -m unittest tests.test_v3_core.V3CoreTests.test_production_refresh_summarizes_pipeline_without_signal_scan` 通过；`npm run build` 通过，仍有既有 Browserslist 过期和 Vite chunk size warning；`python -m unittest tests.test_v3_core tests.test_polywx_contract tests.test_deb_gaussian` 147 OK，仍有既有 sqlite `ResourceWarning` 噪声。测试同步确认 production refresh 新阶段顺序和 selected target date 传递。
- 当前可用性结论：本轮修复后，前端不会再因为 dashboard/cache 暂无今日行而把日期切回旧日期；点击“自动抓取”会针对当前城市/日期重建更完整的数据链路。需要重启正在运行的后端进程才能加载 `production-refresh-v2` 后端代码；Vite 前端通常可由 dev server HMR 自动加载。
- 剩余阻塞：运行中的 8765 如果未重启仍是旧后端；`city-evidence` cache 本身仍可能晚于底层 endpoints；sqlite ResourceWarning 和部分历史中文乱码仍是后续债务。本轮不改变 live trading 锁定状态，也不证明策略已经有稳定 edge。
- 下一步：重启后端后在 UI 选择今天，点击“自动抓取”，检查 production refresh stages 是否出现 `openmeteo_fetch/metar_refresh/hourly_consensus/daily_max_build/market_buckets_sync/signal_decisions_build`，再继续做截图级 UI QA 或 Layer 9 paper validation。

### 2026-07-03：Layer 7 自动抓取可观测性与主题一致性修复
- 目标：复核用户反馈“自动抓取后按钮恢复、最新数据看不出来、浅色主题中间仍发黑、高斯桶图下方留白”的真实原因，并把抓取读写链路从黑盒改成可观察状态。本轮只改后端状态返回、前端状态展示和 Layer 7 布局/主题；不触发 live/canary，不启动 legacy loop，不开启 auto simulation。
- 根因：数据实际已经写入，`data/production-refresh.json` 显示 `production-refresh-v2` 对 `chicago / 2026-07-03` 已完成 11 个阶段；但两个问题让 UI 误导用户：其一，`signal_decisions_build` 的 open-tail bucket 在 stage payload 中带 `-inf/inf`，FastAPI JSON 响应不接受非有限浮点，导致 `/api/dashboard` 一度 500；其二，用户页面仍停在 `date=2026-06-29`，而新抓取目标是 `2026-07-03`。此外 `/api/dashboard` 缓存会残留 `production_refresh.running=true`，需要用最新 status 覆盖。
- 改动：`dashboard_server.py` 增加 `_json_safe()`，将 NaN/Infinity 递归转为 null，并用于 state 文件和 `/api/dashboard` 返回；新增 `GET /api/production-refresh/status`，并在 `/api/dashboard` 返回前用最新 `production-refresh.json` 覆盖缓存中的 refresh 状态；`weatherbot_v3/cli.py` 为 `run_production_refresh()` 增加 `progress_callback`，每个阶段开始/结束都写入 running stage；`frontend/src/api.ts`、`frontend/src/types.ts`、`frontend/src/App.tsx` 增加 3 秒轮询的抓取状态条，按钮在后台运行期间持续显示“抓取中 当前阶段”，完成后显示目标日期；浅色主题同步设置 html/body 背景，并补齐 arbitrary Tailwind 色值覆盖；`WeatherPanel.tsx` 将 Probability buckets 图改为全宽优先，指标和桶表下移，减少宽屏空白。
- 真实验证：浏览器打开 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-03` 后，点击“自动抓取”会显示 `抓取中 0/1 · contracts_sync`，随后依次经过 `openmeteo_fetch/metar_refresh/hourly_consensus/daily_max_build/market_buckets_sync/signal_decisions_build/signal_migration/orderbook_backfill`，最终 `running=false`、11/11 OK。页面最终显示 `抓取 完成 · 2026-07-03`，DEB 为 `μ 89.60°F / σ 4.16°F`，并展示 11 个 Gaussian buckets。浅色主题下 `html/body/root` 背景均为白色，中间卡片不再保持黑底。
- 验证命令：`npm run build` 通过，仍有既有 Browserslist 过期与 Vite chunk size warning；`python -m unittest tests.test_v3_core tests.test_polywx_contract tests.test_deb_gaussian` 147 OK，仍有既有 sqlite `ResourceWarning` 噪声；补跑 `python -m unittest tests.test_v3_core.V3CoreTests.test_dashboard_production_refresh_endpoint_persists_result tests.test_v3_core.V3CoreTests.test_dashboard_production_refresh_rejects_concurrent_run` 2 OK；`git diff --check` 通过，仅 Windows LF/CRLF warning。重启 8765 后 `/api/dashboard` 约 `195.1ms`，`running=false`、`ok=true`、`target_date=2026-07-03`、`stage_count=11`；`/api/production-refresh/status` 约 `26.8ms`，状态一致。
- 当前可用性结论：自动抓取现在是可观察的受控手动流水线，不再是按钮闪一下的黑盒；如果用户仍停在旧 URL 日期，顶部会显示抓取目标日期，城市页切到对应日期后能读到最新 DEB/桶数据。当前仍是观察/模拟验证平台，不解锁实盘，也不证明策略稳定盈利。
- 剩余阻塞：`WeatherPanel.tsx` 仍偏大，后续应拆分；`hourly.py` sqlite `ResourceWarning` 仍需专项清理；部分旧日志中文在 PowerShell 渲染中仍有 mojibake；Layer 9 paper validation 与右侧 `/api/paper-orders` UI 接入尚未完成。
- 下一步：可以进入 Layer 7 截图级 QA 收尾，或进入 Layer 9 paper validation，开始连续模拟评估胜率、ROI、回撤，继续保持 `LIVE_TRADING=false`。

### 2026-07-03：Layer 7 小时聚合与看板验证收口
- 目标：继续验证用户截图与 ClaudeCode 建议里提到的“最新数据不明显、Hourly 图为空或错位、推荐关注位置不合理、抓取日志看不出生产阶段、浏览器 console 警告”等问题；本轮只改 Layer 4 直接消费者与 Layer 7 只读看板，不触发抓取、不启动 legacy loop、不打开 auto simulation、不改变 live/canary 锁定。
- 根因：`hourly_consensus` 重建时会优先选择最新 Open-Meteo primary runs，但这些 run 对本地日期可能只覆盖部分小时；同时旧逻辑直接按 `valid_at` 原始时间分桶，UTC 与 station-local timestamp 混在一起，导致 Chicago 2026-07-02 这类本地日页面在 UI 上像“缺小时/空图”。另外，推荐关注仍在左侧单卡，抓取日志没有把 `production_refresh.stages` 合并展示，Probability bucket 列表在缺少 `signal_id` 时会产生重复 React key warning。
- 改动：`weatherbot_v3/hourly.py` 增加 station-local 时间归一化，按 `(city, target_date, local_hour)` 聚合 forecast rows；当精确 Open-Meteo primary runs 存在但覆盖不完整时，保留少量 legacy/model supplemental runs，避免重建时抹掉完整历史小时。`frontend/src/App.tsx` 将“推荐关注”移动到中间主板顶部，左侧专注城市导航；`frontend/src/components/WeatherPanel.tsx` 把 `productionRefresh.stages` 接入抓取日志 tab，移除 Hourly 图内 residual bars 以免遮挡横轴，降低 cloud/RH 柱透明度，并修复 probability bucket、reason、executable/blocked signal 列表 key；`tests/test_v3_core.py` 和 `tests/test_polywx_contract.py` 更新对应回归与合约断言。
- 验证命令：`python -W ignore::ResourceWarning -m unittest tests.test_v3_core tests.test_polywx_contract tests.test_deb_gaussian` 148 OK；`npm run build` 通过，仍只有既有 Browserslist 过期与 Vite chunk size warning；`/api/dashboard` 返回 `scanner_status=stopped`、`is_running=false`、`production_refresh_running=false`，本次测得约 `3251ms`；`/api/hourly-consensus?city=chicago&target_date=2026-07-02` 返回 `rows=24`、23 个 METAR 行、24 个 forecast 行；`/api/hourly-consensus?city=chicago&target_date=2026-07-03` 返回 `rows=24`、forecast-only 24 行，符合当前本地日还未产生 METAR 观测的状态。
- 浏览器验证：in-app browser 打开 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-03`，页面不再显示“正在连接”，有“推荐关注 / 自动抓取 / Hourly Temperature / Daily Max Prediction / Probability buckets / 抓取日志”，旧 `PolyWX-style city workbench` 文案不存在；浅色主题下 `body` 与 `root` 均为白底，无横向溢出；本次刷新后的 console 无新的 warn/error。
- 当前可用性结论：Layer 7 主看板现在能稳定读取 2026-07-03 的最新 forecast/DEB/Gaussian bucket 数据，2026-07-02 的历史小时图也有完整 24 小时聚合；抓取状态与生产阶段可在日志 tab 里审计。当前仍是观察与模拟验证平台，不证明策略稳定盈利，不允许自动实盘。
- 剩余阻塞：`/api/dashboard` 本轮一次返回约 3.25 秒，仍需后续继续压缩首页查询成本；`WeatherPanel.tsx` 仍偏大，后续应拆分；Chicago 2026-07-03 当前 METAR 为空是时区/日期进度导致的诚实状态，不应伪造观测；Layer 9 paper validation 与右侧 `/api/paper-orders` UI 接入尚未完成。
- 下一步：先 push 当前修复，之后建议进入 Layer 7 截图级 QA 或 Layer 9 paper validation；继续保持 `LIVE_TRADING=false`、canary/live locked。

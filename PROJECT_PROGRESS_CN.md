# WeatherBot 项目进度台账

最后更新：2026-07-01

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

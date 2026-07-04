# WeatherBot 项目进度台账

最后更新：2026-07-04

> 更早记录见 `docs/PROGRESS_ARCHIVE_CN.md`。日常 Turn Start 只读 `docs/CURRENT_STATE.md`，不要通读本文件或归档，除非任务明确涉及历史决策。

## 当前可用性结论

当前状态：**Phase 1.5 到 Phase 2 过渡**。

- 可以本地打开看板，按城市/日期观察天气证据页、手动或受控自动抓取、查看预报/METAR/历史观测/偏差统计/抓取日志和 gated 信号。
- 已接入默认关闭的 server-side scheduler：可手动启动/停止，按 enabled 城市分频率刷新 METAR、forecast 和派生层，并在看板顶部显示 PolyWX 风格状态徽章。
- 已新增 Polymarket Gamma 结算源核验：7 个重点城市均找到活跃天气市场，其中 6 个与本地观测站一致；Hong Kong 市场按 HKO 天文台结算，与 VHHH 观测站不一致，live gate 已阻塞但 paper/观察保留。
- 已接入 China Weather Live 展示实况：Hong Kong 使用 HKO 官方 rhrread API，Shanghai 使用 weather.com.cn `sk_2d/101020100` JSONP；数据只写 `mesonet_observations`，仅作为 observation evidence，不解锁 live gate。
- 可以继续做 paper/simulation 验证、数据链路排查、UI 生产化和策略研究。
- 不能声称无人值守自动实盘赚钱；不能直接用当前 EV 信号加仓；不能用局部回测证明稳定 edge。
- 一句话：**现在是可观察、可模拟、可继续生产化验证的天气交易平台雏形；还不是可放心实盘自动赚钱的机器人。**

## 生产阻塞项

1. truth 独立结算日和站点覆盖仍不足，部分城市只能 paper。
2. 仍有未核验城市 `settlement_rule_unverified`；Hong Kong 存在 `settlement_mismatch`，需要 HKO truth collector 后才可讨论 live。
3. forecast archive 与 station truth 的 walk-forward 校准仍需持续扩样。
4. orderbook/best bid/ask/tick/orderMinSize/staleness 的盘口级回放还未完成生产验收。
5. allowed 策略组尚未证明长期 ROI 为正且显著优于 blocked 组。
6. live dry-run、重复订单保护、余额、熔断、14-30 天 paper gate 和 canary gate 仍未完成。
7. Layer 7 看板仍需继续 browser QA，避免空态、主题不一致和刷新状态误导。
8. scheduler 真实长跑还未完成 60 分钟 forecast 周期观测；当前只完成 2 个 METAR 周期冒烟。
9. `dashboard_server.py` 仍有测试期 SQLite connection ResourceWarning，后续非文档轮次应修。

## 最近 4 条完整 ledger

### 2026-07-04：Git 落盘与推荐 gate 根因诊断

- 目标：本轮不加新功能，只把前四轮改动按 Layer 落成原子 commit，并新增只读诊断脚本定位“推荐关注=0”的根因；重点判断 `metar_stale_or_missing` 是否是 D+1/D+2 决策被误套 D+0 METAR 新鲜度。
- 改动：新增 `tools/diagnose_recommendation_gate.py`，对 chicago/tokyo/atlanta/nyc/dallas/shanghai/hong-kong 输出最新 METAR `report_time`、age、fresh 状态、最新 `signal_decisions` 的 `target_date/issued_at`、stored gate reasons Top3 与 recommendation filter reasons Top3；诊断结果写入 `audits/recommendation-gate-diagnosis-2026-07-04/`，不提交 audits。根据最终诊断触发限定修复：`dashboard_server.py` 的 `_recommendations_payload()` 拆成 `today_observation` 与 `forecast_lead` 两类，D+0 要求 METAR 报文 <30 分钟，D+1/D+2 只要求 forecast run <90 分钟，不再被 stale METAR 误杀。
- 诊断结论：第一次诊断时 7 城 METAR age 约 95-134 分钟，verdict=`collector_stale_or_missing`；启动 scheduler 后 METAR 先恢复 fresh，推荐短暂出现 1-2 个候选，但 30 分钟末尾又因为 METAR 报文真实发布时间落在上一小时 `:51/:52/:53` 而重新出现 `metar_stale_or_missing`。最终诊断出现 `horizon_mismatch_candidates=3`，说明确有少量 D+1/D+2 候选被 D+0 METAR freshness 误杀；修复后直接调用新源码 `_recommendations_payload(scheduler_status={'running': True})` 返回 3 个 `forecast_lead` 候选。
- 验证：`git diff --check` 通过，仅 Windows LF/CRLF warning；误用系统 `python` 的一次测试因缺少 `requests` 失败，随后使用 `.venv\Scripts\python.exe` 跑完整套件通过 176 tests OK，仍有既有 SQLite ResourceWarning；`npm run build` 通过，仍有既有 Browserslist/chunk warning；新增回归 `test_dashboard_recommendations_use_forecast_freshness_for_lead_dates` 覆盖 D+1 新 forecast + stale METAR 的场景。
- 30 分钟 scheduler 采样：手动 `POST /api/scheduler/start`，起点 `2026-07-04T11:06:31Z`；第一轮后 METAR poller `last_run_at=2026-07-04T11:07:39Z`，dashboard skipped 变为 `paper_gate_blocked=154`、`older_decision_round=660`；约 25 分钟时推荐为 1；满 30 分钟前后 `metar_last=2026-07-04T11:35:22Z`、`forecast_last=2026-07-04T11:11:14Z`、`derive_last=2026-07-04T11:34:47Z`、`metar_runs=12`、`forecast_runs=2`、`derive_runs=4`，旧后端 payload 推荐为 0 且 skipped=`metar_stale_or_missing=132`、`older_decision_round=792`、`paper_gate_blocked=22`；随后停止 scheduler 成功。
- 相关提交：settlement `08f6008`，China/scheduler `b86dd51`，Layer7 charts `ba334a4`，recommendation/diagnostics/docs 为本轮最终提交。

### 2026-07-04：Layer 6/7 调度器驱动推荐关注闭环

- 目标：把第 1-4 轮成果串起来，让顶部“推荐关注”不再依赖手动单城市抓取或 legacy `weather_signals.actionable`，改为从调度器持续更新后的全城市 `signal_decisions` 最新一轮自动生成；保持 live 全锁，不新增 paper 执行按钮。
- 改动：`dashboard_server.py` 新增 `_recommendations_payload()`，按 `METAR age < 30min`、`stations.settlement_rule_verified_at` 非空、`market_buckets.strict_match_status=matched`、`paper_allowed=true` 或仅 `spread_too_wide` 阻塞筛选交易候选；同一城市只展示当前最佳候选，并返回城市、站点、当前观测、DEB `mu±sigma`、最优桶、edge、阻塞原因与 Polymarket 链接。上海/香港若 `verification_status=no_active_market` 则返回 `observation_only`，只展示 DEB 与 China Weather Live，不展示 bucket/token/edge/gate。`/api/dashboard` 每次请求都会附带最新 `recommendations`，不等待 dashboard cache 重建。`frontend/src/App.tsx` 将推荐卡改为 `DashboardRecommendationItem` 渲染，显示 `METAR age` 与 `verified` 状态；空态区分 `scheduler_stopped` 与 gate 后无候选，并展示 skip 计数。补充 `DashboardRecommendations` 类型和推荐筛选/无市场分支测试。
- 验证：`python -m unittest tests.test_v3_core -k recommendations` 通过 3 tests；`python -m unittest tests.test_polywx_contract` 通过 12 tests；完整 `python -m unittest tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled tests.test_polywx_contract` 通过 175 tests；`npm run build` 通过；`git diff --check` 通过，仅 Windows LF/CRLF warning；浏览器打开 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-03`，推荐关注区域可见，console error=0，无横向溢出。
- 30 分钟 scheduler 验证：手动启动 scheduler 后每 5 分钟采样 7 次并自动停止。METAR poller 从第 1 个完整周期开始连续成功 `ok_cities=7 / failed_cities=0`，`fails_last_hour=0`，最后一次 `metar_last_run_at=2026-07-04T09:40:10Z`、poller age 约 90s；推荐数量从 sample0 的 5 个、sample1 的 2 个，在 derive_poller 于 `2026-07-04T09:17:56Z` 重建后降为 0，并在最后 sample6 仍为 0，`empty_reason=no_recommendations_after_gates`，主要 skip 为 `metar_stale_or_missing=88`、`paper_gate_blocked=66`、`older_decision_round=638`。
- 结论：推荐关注的“数据链路/自动刷新/UI 展示”已打通，但当前策略 gate 下不能保证推荐列表稳定出现 ≥2 城市；这是最新 `signal_decisions` 与盘口/新鲜度/纸面交易门槛筛选后的真实结果，不是前端不刷新。当前系统可用于观察实时推荐空态、skip 计数和候选卡，但仍不能进入自动 paper 或 live。
- 下一步：优先查 derive 后 `signal_decisions` 为何大量落入 `metar_stale_or_missing` 与 `paper_gate_blocked`，需要把最新 METAR refresh 与 decision target 的 target_date/local-day 对齐，并做 Layer 9 paper validation 前的 gate 分布报表；若要稳定出现候选，不能放宽 live/paper 安全门槛，只能改善数据新鲜度、市场桶匹配和盘口可用性。
- 相关提交：本轮最终提交。

### 2026-07-04：Layer 7 PolyWX 高保真小时图与高斯桶前端整改

- 目标：本轮只改 Layer 7 前端，不改后端 schema、不触发抓取；以 PolyWX `chicago-kord` 工作台和本地 audits corpus 为验收标准，重点修正 Hourly Temperature 与 Probability buckets 的视觉编码、读图顺序和空白问题。
- 改动：`frontend/src/components/WeatherPanel.tsx` 将小时图升级为 Recharts `ComposedChart` 多系列：METAR 橙色实线圆点、Historical 绿色实线圆点、China Weather Live 红色方块点、PWS 紫色三角点、Forecast 蓝色虚线空心圆；Cloud Cover % 改为右轴半透明 Area；X 轴固定 00:00-23:00；峰值改为 `ReferenceLine` + 粉底白字 `peak HH:00`；图下增加 `AVG Δ (OBS−FC)`、`ACCURACY (PEARSON R)`、`HIST↔METAR OVERLAP` 三行徽章与诚实空态。高斯桶图固定 0-25% Y 轴，最高 1-2 个桶用 `#2563EB`，其余 `#4B5563`，移除卖一折线，bucket 表紧贴图表下方，指标卡后置。
- Recharts 属性表：

| 模块 | 系列/元素 | 样式 |
| --- | --- | --- |
| Hourly | METAR | `stroke="#F97316"`、`strokeWidth={2}`、`dot={{ r: 3 }}`、`activeDot={{ r: 5 }}` |
| Hourly | Historical | `stroke="#22C55E"`、实心圆点 |
| Hourly | China Weather Live | `stroke="#EF4444"`、`SquareDot` |
| Hourly | PWS | `stroke="#A855F7"`、`TriangleDot` |
| Hourly | Forecast | `stroke="#3B82F6"`、`strokeDasharray="4 4"`、`HollowCircleDot` |
| Hourly | Cloud Cover % | right axis 0-100、`Area fill="#94A3B8" fillOpacity={0.25}` |
| Hourly | Peak | `ReferenceLine stroke="#EC4899"`、label `peak HH:00` |
| Buckets | top buckets | `Cell fill="#2563EB"` |
| Buckets | other buckets | `Cell fill="#4B5563"`、Y axis 0-25% |

- 验证：`npm run build` 通过；`python -m unittest tests.test_polywx_contract` 通过 12 tests；`python -m unittest tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled tests.test_polywx_contract` 通过 172 tests OK，仍有既有 SQLite ResourceWarning 噪声；浏览器打开 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-03` 和 `http://127.0.0.1:5173/?city=shanghai-zspd&date=2026-07-04`，无 console error、无横向溢出。截图：`audits/layer7-polywx-visual-2026-07-04/chicago-2026-07-03-wait8.png`、`audits/layer7-polywx-visual-2026-07-04/shanghai-2026-07-04-wait8.png`、`audits/layer7-polywx-visual-2026-07-04/chicago-2026-07-03-buckets-final.png`。
- 结论：Layer 7 核心图表已经按 PolyWX 风格完成一轮高保真整改；Chicago 页面可完整展示小时图和高斯桶，Shanghai 2026-07-04 可展示中国城市路径。Shanghai 2026-07-03 没有可绘制小时字段时会显示诚实空态，不属于前端渲染错误。
- 下一步：继续做浏览器 QA 与组件拆分，尤其将 `WeatherPanel.tsx` 拆出 HourlyChart、ProbabilityBuckets、EvidenceBadges；若继续数据问题，优先查 scheduler/current date 写入与 `/api/hourly-consensus` 数据完整性。
- 相关提交：`ba334a4`。

### 2026-07-04：Layer 2 China Weather Live mesonet_observations

- 目标：参考 PolyWX 图例中的 “China Weather Live” 红色系列，为中国城市补充国内/本地实况源；严格作为 `mesonet_observations` observation evidence，不写入 `metar_reports`，不作为 settlement truth，也不解锁 live gate。
- 改动：新增 `weatherbot_v3/china_weather.py` collector，支持 `python -m weatherbot_v3.cli china-weather-fetch --city shanghai --city hongkong`；Hong Kong 使用 HKO 官方开放数据 `https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en`，读取 Hong Kong Observatory 的 temperature/humidity recordTime；Shanghai 使用 `http://d1.weather.com.cn/sk_2d/101020100.html?_=<ts>` 的 `var dataSK={...}` JSONP，读取 temp/SD/qy/wde/wse/time/date；`mesonet_observations` 增加 `raw_response`、`raw_response_hash`、`fetched_at`，并让缺失数值保留 NULL；scheduler 新增 `china_live_poller`，默认随 scheduler 手动启动后每 5 分钟只拉 shanghai/hong-kong；`hourly.py` 保存 `source_temperatures.china_live`，让 China Live 不覆盖 METAR diff；前端 `WeatherPanel.tsx` 新增红色 “China Weather Live” 折线和状态条新增 China Live 徽章。
- 实际跑数：真实运行 `china-weather-fetch --city shanghai --city hongkong`，写入 2 行 `mesonet_observations`：Shanghai `station_id=101020100`、temperature 31.9C、humidity 69%；Hong Kong `station_id=HKO`、temperature 30.0C、humidity 80%。随后运行 `hourly-consensus-build --city shanghai --city hong-kong --target-date 2026-07-04`，两城 10:00 local 均可在 `hourly_consensus_points` 读到 `china_live` 红线值，`metar` 未被伪装。
- 已知限制：HKO rhrread 为官方 JSON、免 key，分钟级/近实时；weather.com.cn `sk_2d` 是公开 JSONP，需要 `User-Agent` 与 `Referer`，字段为城市级/中国天气网口径，不是 ZSPD 机场结算 truth；HTML fallback 只用于显式失败/降级，不静默造数；两源均标记 `display_only`、`not_settlement_truth`。
- 验证：`python -m unittest tests.test_polywx_contract tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled` 通过，172 tests OK；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning；测试仍打印既有 SQLite ResourceWarning。
- 结论：Layer 2 中国实况展示源已可用，PolyWX 风格红线数据链路打通；这增强看板证据密度，但不改变 live gate，尤其 Hong Kong 仍需单独 HKO settlement truth collector 才能讨论实盘。
- 下一步：做浏览器 QA 确认 Shanghai/Hong Kong 页面红线展示、toast 与 `china_live_poller` 状态一致；后续如要把 Hong Kong HKO 升级为 truth，必须另起 Layer 2/5 truth collector 轮次并绑定结算规则。
- 相关提交：`b86dd51`。

### 2026-07-04：Layer 1/5 Polymarket 结算源 Gamma 核验

- 目标：按 alteregoeth-ai/weatherbot 的核心教训，先钉死每个 Polymarket 天气市场的真实结算站点、来源、单位和时区；尤其确认上海/香港是否能作为 observation station 直接进入 live gate，避免把市中心、错误机场或二手展示源当成结算 truth。
- 改动：新增 `weatherbot_v3/polymarket_probe.py` 与 CLI `python -m weatherbot_v3.cli polymarket-market-probe --city <key>`，按 D0-D3 探测 Gamma active event 并解析 market description/source URL；`stations` 扩展并持久化 `settlement_station_id`、`settlement_station_name`、`settlement_timezone`、`settlement_unit`、`settlement_time_basis`、`settlement_rule_verified_at`；`sync_station_registry()` 保留已核验结果；`qualification.py` 与 `signals.py` 将 `settlement_rule_unverified`/`settlement_mismatch` 纳入 live gate，但不阻塞 paper；看板城市头部新增 Settlement station、Rule verified、Timezone、Truth source 与 Non-truth source 标签；补充 Gamma probe、resolution parser、mismatch live gate 和 dashboard 合约测试。
- 核验结果：

| 城市 | 活跃市场 | 观测站 | 结算站 | 来源机构 | 单位 | 时区 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shanghai | yes | ZSPD | ZSPD | wunderground | C | Asia/Shanghai | verified |
| hong-kong | yes | VHHH | HKO | hong_kong_observatory | C | Asia/Hong_Kong | settlement_mismatch |
| chicago | yes | KORD | KORD | wunderground | F | America/Chicago | verified |
| tokyo | yes | RJTT | RJTT | wunderground | C | Asia/Tokyo | verified |
| atlanta | yes | KATL | KATL | wunderground | F | America/New_York | verified |
| nyc | yes | KLGA | KLGA | wunderground | F | America/New_York | verified |
| dallas | yes | KDAL | KDAL | wunderground | F | America/Chicago | verified |

- 验证：真实运行 `polymarket-market-probe` 覆盖 shanghai/hong-kong/chicago/tokyo/atlanta/nyc/dallas，active=7、verified=6、mismatches=1；`python -m unittest tests.test_polywx_contract tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled` 通过；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。
- 结论：上海可按 ZSPD/Wunderground 进入已核验 observation truth 链路；香港市场结算为 HKO 天文台 Daily Extract，不能用 VHHH METAR 解锁 live，当前必须 `settlement_mismatch` 阻塞实盘，只保留 paper/观察；其余 5 个主力城市与现有 station_id 一致。
- 下一步：为 Hong Kong 接 HKO Daily Extract truth collector，或继续批量核验剩余城市；在此之前不要扩大香港 live 自动交易。
- 相关提交：`08f6008`。

## 下一步优先级

1. 下一轮开工只读 `docs/CURRENT_STATE.md`，避免重复读取长归档。
2. 若继续 UI：优先 Layer 7 browser QA，确认 Shanghai/Hong Kong 红色 China Weather Live 曲线、scheduler China Live 徽章、toast 与主题一致。
3. 若继续结算源：优先补 Hong Kong HKO truth collector 或批量核验剩余城市的 settlement rule。
4. 若继续数据：跑 60 分钟 forecast scheduler 长跑，确认 forecast_poller 真实完成并写入状态。
5. 若继续策略：进入 Layer 9 paper validation，评估样本、盘口回放和 allowed/blocked ROI。
6. 实盘继续锁定，直到 paper validation 和 canary dry-run gate 通过。

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

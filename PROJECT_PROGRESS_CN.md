# WeatherBot 项目进度台账

最后更新：2026-07-04

> 更早记录见 `docs/PROGRESS_ARCHIVE_CN.md`。日常 Turn Start 只读 `docs/CURRENT_STATE.md`，不要通读本文件或归档，除非任务明确涉及历史决策。

## 当前可用性结论

当前状态：**Phase 1.5 到 Phase 2 过渡**。

- 可以本地打开看板，按城市/日期观察天气证据页、手动或受控自动抓取、查看预报/METAR/历史观测/偏差统计/抓取日志和 gated 信号。
- 已接入默认关闭的 server-side scheduler：可手动启动/停止，按 enabled 城市分频率刷新 METAR、forecast 和派生层，并在看板顶部显示 PolyWX 风格状态徽章。
- 已新增 Polymarket Gamma 结算源核验：7 个重点城市均找到活跃天气市场，其中 6 个与本地观测站一致；Hong Kong 市场按 HKO 天文台结算，与 VHHH 观测站不一致，live gate 已阻塞但 paper/观察保留。
- 已接入 China Weather Live 展示实况：Hong Kong 使用 HKO 官方 rhrread API，Shanghai 使用 weather.com.cn `sk_2d/101020100` JSONP；数据只写 `mesonet_observations`，仅作为 observation evidence，不解锁 live gate。
- 已接入 Wunderground/Weather.com PWS collector 骨架：写 `mesonet_observations.network=wunderground_pws`，display-only/not-settlement-truth，随 METAR poller 同频；当前本机未配置 API key，真实 5 城命令已审计为 skipped 不造数。
- DEB 峰值小时已改为 hourly_consensus 混合曲线 argmax：过去小时用观测覆盖 forecast，并列取最晚；Chicago 2026-07-02 已从 forecast-only 15:00 修正为 mixed 16:00。
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

## 最近 5 条完整 ledger

### 2026-07-04：Layer 2/7 Historical display-only 回填入口与 PolyWX benchmark 工具

- 目标：结合本地实现与 PolyWX 对照建议，不把 PolyWX 展示值导入生产 truth，而是补齐 WeatherBot 自己的历史小时密度入口、诚实区分 METAR/Historical/PWS 系列，并提供 audit-only 的 PolyWX benchmark 与 scheduler 长跑采样工具。
- 改动：`weatherbot_v3/history.py` 新增 Open-Meteo Historical collector，使用 `archive-api.open-meteo.com/v1/archive` 拉取 hourly temperature、humidity、dew point、cloud、wind、pressure、precipitation 等字段，写入 `mesonet_observations.network=open_meteo_historical`，并标记 `display_only/research_truth/not_settlement_truth/open_meteo_archive_grid`；CLI 新增 `history-backfill`，必须显式手动运行，不接入启动流程。`weatherbot_v3/hourly.py` 改为把 `open_meteo_historical`、`wunderground_pws` 与 `china_live` 作为独立系列输出，且不再把非 METAR mesonet 数据冒充成 METAR。前端 `WeatherPanel.tsx` 的 METAR/Historical 表格补齐 humidity、cloud、weather、visibility、wind、pressure、dew、fetched 等列，Historical tab 新增小时级历史表。新增 `tools/scheduler_longrun_probe.py` 用于采样 `/api/scheduler/status` 与 `/api/dashboard`，新增 `tools/polywx_benchmark_snapshot.py` 仅把 PolyWX 页面/API 证据保存到 `audits/`，脚本内明确禁止把 PolyWX 值导入 truth/mesonet/hourly/forecast/trading 表。
- 验证：`.venv\Scripts\python.exe -m unittest tests.test_v3_core tests.test_polywx_contract` 通过 174 tests OK，仍有既有 SQLite connection ResourceWarning；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。测试覆盖 Open-Meteo Historical display-only 入库、Historical/PWS 不冒充 METAR、PolyWX contract 中的 history-backfill/benchmark disclaimer/scheduler probe 关键词。
- 结论：本轮提高了历史/展示数据密度和对照工具质量，但没有把 Open-Meteo Historical 或 PolyWX 当成 settlement truth，也没有解锁 live。若用户看到折线图没有 METAR 白线，现在系统会更诚实地区分：METAR 缺失就是缺失，Historical/PWS/China Live 会作为独立系列出现，不能填充 METAR gate。
- 下一步：如需真实补历史密度，手动运行 `python -m weatherbot_v3.cli history-backfill --city chicago --days 30` 或限定 7 城批量回填，再重建 `hourly-consensus`；如需验证 scheduler 稳定性，运行 `python tools/scheduler_longrun_probe.py --duration-minutes 60 --sample-seconds 300 --start-scheduler --stop-scheduler`。PolyWX 只能用 `tools/polywx_benchmark_snapshot.py` 做 audit 对照，不得导入生产库。
- 相关提交：本轮最终提交。

### 2026-07-04：Layer 6/8 策略复用层、Kelly 仓位与 Ladder Paper 原子执行

- 目标：只改 Layer 6/8，不改前端、不解锁 live；把原本唯一的单桶 EV 策略扩展为可组合策略层，并加入 Kelly 仓位建议。新增三类策略：`single_bucket_ev`、`ladder_grid`、`tail_buying`。
- 改动：新增 `weatherbot_v3/sizing.py`，按二元 YES 合约计算 Kelly fraction，并以 `kelly_multiplier=0.15`、`min(bankroll*0.05, max_per_trade_usd)` 做硬上限；`config.py` 增加 `bankroll_usd`、`kelly_multiplier`、`max_per_trade_usd`，兼容现有 `config.json.balance/max_bet`。新增 `weatherbot_v3/strategies/`：`StrategyBase.evaluate()`、`SingleBucketEVStrategy`（MIN_EDGE 0.05）、`LadderGridStrategy`（μ 附近 3 桶、每桶 edge >=3%、总仓位为中心桶 Kelly 的 0.6 并按 CDF 权重分配）、`TailBuyingStrategy`（ask <=0.15、edge >=10%、独立结算日 >=20、单笔 cap $50、日候选 cap 5）。`signals.py` 改为统一调用策略层，保留 single bucket 旧 decision_id 稳定性；`signal_decisions` 自动迁移并持久化 `strategy_name`、`kelly_fraction`、`position_size_usd`、`ladder_group_id`。`paper.py` 支持 ladder group 原子执行：同组 3 桶全组预检查，任一盘口/风控失败则不写任何订单。`dashboard_server.py` 后端推荐 payload 增加策略标签、Kelly/仓位和 ladder `sub_buckets`，但本轮未改 React 前端。
- 验证：新增 `tests/test_kelly_sizing.py` 与 `tests/test_strategies.py`；`.venv\Scripts\python.exe -m unittest tests.test_kelly_sizing tests.test_strategies` 通过 8 tests OK；`.venv\Scripts\python.exe -m unittest tests.test_v3_core` 通过 160 tests OK，仍有既有 SQLite ResourceWarning；`.venv\Scripts\python.exe -m unittest tests.test_polywx_contract tests.test_kelly_sizing tests.test_strategies` 通过 20 tests OK。未运行 `npm run build`，因为本轮没有改前端文件。
- 真实跑数：执行 `signal-decisions-build --city chicago --city nyc --city atlanta --target-date 2026-07-04 --limit 200`，写入 33 条最新决策（每城 11）；最新 issued_at 均为 `2026-07-04T11:00:00+00:00`。最新轮次按策略统计：`single_bucket_ev=33`、`paper_allowed=0`、平均 edge `-0.009212`、平均 Kelly `0.024041`；`ladder_grid=0`，原因是没有 3 桶同时满足 edge/sizing；`tail_buying=0`，原因是未同时满足 ask、edge、历史样本 gate。审计报告写入 `audits/strategy-multiplex-report-2026-07-04.md`，不提交。
- 结论：策略复用层、Kelly sizing 和 ladder paper 原子执行已落地并有回归测试；真实当日 Chicago/NYC/Atlanta 仍无可执行 paper 候选，主要阻塞是 `insufficient_bias_samples`、`settlement_rule_unverified`、`spread_too_wide`，说明当前不能自动加仓，更不能 live。`LIVE_TRADING=false` 未改变。
- 下一步：继续补 settlement verification/truth 样本与盘口 replay；若要让 ladder/tail 真正参与 paper validation，需要先改善样本、价差和市场桶匹配，不应通过放宽 gate 伪造候选。
- 相关提交：`dbd3459`。

### 2026-07-04：Layer 2/4/6 PWS、DEB peak 与摄氏桶口径修正

- 目标：落实第二轮修改建议：导出 Chicago 2026-07-02 峰值差异审计；DEB `peak_hour` 改为混合曲线 argmax；新增 Wunderground PWS 聚合 collector 写入 `mesonet_observations`；核对并测试摄氏度市场桶按截断口径积分；本轮结束提交。
- 改动：新增 `audits/peak-diff-chicago-2026-07-02.md`，导出 24 行 `hourly_consensus` 并逐源标注 forecast/observed/mixed 最大值。`weatherbot_v3/deb.py` 新增 `mixed_curve_argmax_v1`：以 build `issued_at` 为分界，已发生小时优先 `observed_temp`，未来或缺观测小时用 `forecast_temp`，并列最大值取最新本地小时；`daily_max_predictions` 增加 `peak_hour`、`peak_temp`、`peak_source` 持久化字段。新增 `weatherbot_v3/pws.py`，通过 Weather.com/Wunderground PWS API 发现/拉取邻近 PWS，聚合为 `wunderground_pws`，并标记 `display_only`、`not_settlement_truth`；CLI 增加 `pws-fetch`，scheduler 的 `metar_poller` 同频调用但 PWS 缺 key 不拖垮 METAR。`bucket_probabilities()` 的摄氏度整数桶改为截断口径，例如 `23°C` 为 `[23,24)`，不是 `23±0.5`。
- 审计结论：WeatherBot 本地 DB 中 Chicago 2026-07-02 forecast-only max 为 `94.50°F @15:00`，METAR observed max 为 `93.92°F @13:00/14:00/15:00/16:00`，mixed tie policy 选择 `16:00`；本地 PolyWX corpus 未捕获 2026-07-02 peak-marker XHR，因此外部 `17:00` 说法仍需 fresh capture 后才能作为源证据。重新跑 `daily-max-build --city chicago --target-date 2026-07-02` 后落库 `peak_hour=16:00`、`peak_source=metar`。
- PWS 跑数：真实执行 `pws-fetch --city chicago --city nyc --city dallas --city atlanta --city miami --station-limit 5`，5 城均返回 `missing_wunderground_api_key`，`rows_upserted=0`、`skipped=5`、`failed=0`；取证写入 `audits/pws-wunderground-fetch-2026-07-04.md`。这是预期行为：未配置 `WUNDERGROUND_API_KEY` 或 `WEATHER_COM_API_KEY` 时不抓不造假数据。
- 验证：`.venv\Scripts\python.exe -m unittest tests.test_v3_core tests.test_scheduler` 通过 164 tests OK，仍有既有 SQLite ResourceWarning；`.venv\Scripts\python.exe -m unittest tests.test_polywx_contract` 通过 12 tests OK；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。
- 结论：峰值标记口径、PWS display-only 数据入口和 C 桶概率边界已补齐；PWS 真实入库仍阻塞于本机未配置 Wunderground/Weather.com API key。该轮不改变 live gate，实盘继续锁定。
- 下一步：若要让 PWS 紫色三角真实出现，需要配置 `WUNDERGROUND_API_KEY` 或 `WEATHER_COM_API_KEY` 并重跑 `pws-fetch`/scheduler；若要核对 PolyWX 17:00，需要重新捕获 2026-07-02 Chicago 的 PolyWX peak-marker XHR。
- 相关提交：本轮最终提交。

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

### 2026-07-05：README 运行手册与本地启动烟测

- 目标：把根目录 README 从旧版 WeatherBet 单体说明更新为当前 WeatherBot v6 的 GitHub 风格运行手册，并实际启动本地项目供人工核验。
- 改动：重写 `README.md`，说明当前实现路径、目录结构、后端/前端启动方法、端口占用处理、看板操作、常用 CLI、人工核验命令、测试命令、配置变量、当前限制与风险声明；明确 `weatherbet.py`/legacy 不是 v6 主运行入口。
- 验证：已启动 FastAPI 后端 `http://127.0.0.1:8765` 与 Vite 前端 `http://127.0.0.1:5173/`；`/api/dashboard` 返回成功，当前运行态为 `scanner_status=stopped`、`is_running=false`、`stats.auto_simulation.enabled=false`、`production_refresh.running=false`、`scheduler_status.running=false`；前端 HTTP 200。
- 结论：项目已在本机跑起，可打开 `http://127.0.0.1:5173/` 人工核验；本轮没有启动 scheduler、没有开启实盘、没有修改交易逻辑。
- 下一步：人工检查 README 的启动/核验步骤是否符合你的操作习惯；如要继续验证数据刷新，再在看板点击“启动调度器”并观察顶部 poller 徽章。
- 相关提交：未提交。
### 2026-07-05: Asian Polymarket weather market source snapshot
- Target: complete Asian weather-market research addendum A-F without changing trading code or unlocking live execution.
- Changes: added `docs/polymarket_asia_markets_snapshot.md`, `docs/polymarket_asia_markets_snapshot.csv`, and `docs/open_meteo_asia_samples.json`. The snapshot covers active Gamma Asian highest-temperature events, per-bucket CSV rows, Wunderground/HKO source reachability, ZBAA Wunderground vs AWC/IEM feasibility, Open-Meteo CMA/JMA/GFS ensemble/historical/previous-runs availability, unit/timezone contract notes, Asian city strategy priority, and UMA MOOV2 settlement-delay constraints.
- Verification: Gamma exact slug probes found 31 active Asian events and 341 bucket rows; Wunderground base URLs are reachable for 9 non-HK Asian airport markets; Hong Kong uses HKO Daily Extract rather than Wunderground. Open-Meteo forecast, ensemble, historical forecast, and previous-runs probes succeeded for Shanghai, Beijing, Hong Kong, Tokyo, Seoul, Taipei, and Chicago.
- Conclusion: Asian markets are confirmed and worth integrating, but exact settlement replication still needs a dedicated Wunderground daily-history path; IEM/AWC METAR max should remain an approximation, not live-unlocking settlement truth.
- Next: wire Asian station registry/model preferences in a separate implementation turn; prioritize Shanghai/Wuhan/Beijing, keep Hong Kong truth-gated until HKO collector is production-ready, and keep Seoul monitor-only unless paper evidence improves.
- Commit: not committed in this research turn; workspace already had unrelated dirty files.
## 2026-07-05：Round 3 Truth Layer 三源协议 + Gamma 结构化持久化

- 改动：将旧 `weatherbot_v3/truth.py` 迁移为 `weatherbot_v3/truth/__init__.py`，新增 `truth/iem_asos.py`、`truth/hko.py`、`truth/wunderground.py`、`truth/delta.py`；新增 Gamma 结构化模块 `weatherbot_v3/polymarket_gamma.py`；SQLite 新增 `truth_iem_daily`、`truth_iem_hourly`、`truth_wunderground_daily`、`truth_hko_daily`、`truth_delta_audit`、`polymarket_events`、`polymarket_markets`、`polymarket_orderbook`。
- 改动：补齐亚洲城市 registry/stations：Beijing ZBAA、Wuhan ZHHH、Qingdao ZSQD、Shenzhen ZGSZ、Taipei RCSS，并确保 Seoul/Singapore 本地旧库可选；Hong Kong 观测站保留 VHHH，但 settlement truth 指向 HKO Daily Extract；受控 scheduler 新增 `gamma_orderbook_poller`，默认停用，手动启动 scheduler 后每 300 秒刷新亚洲活跃事件与 orderbook。
- 验证：`python -m unittest tests.test_v3_core` 167 tests OK；`python -m unittest tests.test_polywx_contract` 12 tests OK；`python -m unittest tests.test_scheduler` 6 tests OK；`git diff --check` OK（仅 CRLF warning）。新增测试覆盖 IEM F->C、HKO Daily Extract、Wunderground skip reason、Gamma 三表持久化、11/6 桶动态边界、`or higher/or lower` 尾桶和 scheduler status shape。
- 真实冒烟：IEM ZBAA 2026-06-27 返回 high_c=35.0、obs_count=24；HKO 2026-07-04 官方 Daily Extract 当前只发布到 7/2，返回 `date_not_found_in_hko_daily_extract`，HKO 2026-07-02 成功 high_c=32.2；Wunderground ZBAA 2026-06-27 无 key/未授权返回 `http_401` skip；Shanghai 2026-07-06 Gamma 同步 1 event、11 markets、11 orderbooks，尾桶 `37°C or higher` 正确解析为 `[37,+inf)`。
- 结论：Round 3 数据结构和 collector 路径可用；IEM 是可靠近似 truth，HKO 是 Hong Kong P0 truth，Wunderground 是可选 truth 且失败不阻塞。当前仍不能用这些数据直接解锁 live，需后续 Round 4/5 把 truth_delta、Gamma orderbook refresh 和策略/看板消费链路接入。

### 2026-07-05: Round 4 Ensemble DEB + bucket calibration foundation
- Target: replace single Gaussian-only DEB consumption with an ensemble-backed probability path, add initial market sanity calibration, wire model reprice events, and keep live trading locked.
- Changes: added `weatherbot_v3/forecasts/ensemble.py`, `weatherbot_v3/bias.py`, and `scripts/train_bias.py`; extended `daily_max_predictions`, `signal_decisions.forecast_algo`, and `model_reprice_events`; `signals.py` now consumes ensemble sample distributions when available and falls back to Gaussian CDF otherwise. `openmeteo.py` now includes CMA GRAPES for China/HK deterministic fetches; `config.py` stores Asian city priority modes.
- Verification: ran 195 tests OK with `python -m unittest tests.test_ensemble_vs_market tests.test_deb_gaussian tests.test_v3_core tests.test_scheduler tests.test_polywx_contract`; `git diff --check` passed with only Windows line-ending warnings. Real smoke generated `data/bias_table.json`, fetched Shanghai Open-Meteo ensemble data, built `ensemble_v1` daily max for Shanghai 2026-07-06, rebuilt Shanghai signal decisions, and stored `model_reprice_events`. The earlier SQLite ResourceWarning noise was resolved in the 2026-07-05 Previous Runs follow-up.
- Conclusion: Round 4 foundation is usable for paper/research probability comparisons. Live remains locked. The 341-bucket calibration is currently a snapshot sanity baseline because the local DB does not yet contain full archived ensemble runs for every historical bucket; this must become a Previous Runs walk-forward before profitability claims.
- Next: collect archived Open-Meteo previous-runs for the 341 bucket set, expand ensemble coverage beyond the smoke city/date, then rerun calibration with real historical lead-time alignment.
- Commit: not committed in this turn.

### 2026-07-06：Round 5 Layer 7 UI 大清理 + i18n + 城市状态可视化

- 目标：只做 Layer 7 UI 与只读展示支撑，不改交易算法、不解锁 live；把 10 个亚洲城市的 fully_active / paper_only / monitor_only / observation_only 状态显性化，并补齐 i18n、动态 bucket 表、Delta Audit 和 alpha candidate 标记。
- 改动：新增 `frontend/src/i18n/zh-CN.json`、`frontend/src/i18n/en.json`、`frontend/src/i18n/useT.ts`；顶栏新增分组 City dropdown 与语言 dropdown；中间主板新增 HK paper-only 黄色横幅、Seoul monitor-only 红色横幅、fully_active 市场信息条；新增 `DeltaAuditPanel`，读取 `truth_delta_audit`；概率桶明细从表格改为 6-12 桶动态 grid，展示 model_prob / market_mid / edge / bid-ask，edge 绝对值 >8pp 高亮；alpha candidate 用 `model_reprice_events` 显示 ⚡ tooltip。
- 后端支撑：`dashboard_server.py` 新增只读 `/api/truth-delta-audit`、`/api/model-reprice-events`，dashboard payload 暴露 `city_statuses`；`weatherbot_v3/db.py` 新增 truth delta 与 model reprice summary/list 函数。
- 验证：`npm run build` 通过；`.venv\Scripts\python.exe -m unittest tests.test_polywx_contract tests.test_v3_core` 通过，181 tests OK；`git diff --check` 通过，仅 Windows LF/CRLF warning。
- 结论：Round 5 UI 框架可用，城市状态与风险提示更清晰；Delta/Alpha 页面依赖 Round 3/4 表内真实数据，没数据时显示诚实空态。live 仍锁定。
- 下一步：浏览器人工 QA 10 城城市切换、HK/Seoul 横幅、6/12 桶动态表；之后进入 Round 6 前应先补 Previous Runs/Truth Delta 数据密度与 paper validation。
- 相关提交：待提交。

### 2026-07-05: Previous Runs walk-forward entry and SQLite warning cleanup

- Target: close the main leftover from Round 4 by replacing fake market-normalized "calibration" with a real Open-Meteo Previous Runs ingestion path, then remove noisy SQLite ResourceWarnings from the test run.
- Changes: extended `weatherbot_v3/openmeteo.py` with `fetch_openmeteo_previous_runs`, per-region model selection, local-day-to-UTC request windows, previous_dayN parsing, and explicit data-fetch logging; added `openmeteo-previous-runs` CLI; added `previous_run_samples` and `previous_run_distribution_for_buckets` to `weatherbot_v3/forecasts/ensemble.py`; updated `tests/test_ensemble_vs_market.py` so the 341-bucket snapshot is labeled as a market baseline rather than model probability. Fixed legacy `dashboard_db.py` by making `_connect()` return a closing sqlite connection.
- Verification: real smoke fetched Beijing 2026-07-05 previous-day 1/2/3 archived runs for ECMWF/GFS/CMA and wrote 9 forecast runs plus 9 members; report written to `audits/previous-runs-beijing-2026-07-05.md`. `python -m unittest tests.test_ensemble_vs_market tests.test_deb_gaussian tests.test_v3_core tests.test_scheduler tests.test_polywx_contract` passed, 198 tests OK. `git diff --check` passed with only Windows line-ending warnings.
- Conclusion: the walk-forward data path now exists and is auditable, but it exposed a real blocker: Beijing 2026-07-05 34C bucket had market mid 0.9965 while archived Previous Runs sample probability was 0.0. That means the old `model_prob >= 0.85` sanity cannot be honestly claimed yet; it is a calibration/model-source mismatch to solve before production paper scoring.
- Next: run previous-runs ingestion across the full 341-bucket set, compare by city/model/lead-time, then decide whether CMA/JMA/GFS ensemble weighting, station truth, or bucket/date alignment is causing the large Beijing mismatch. Keep scheduler and live trading off unless explicitly requested.
- Commit: not committed in this turn.

### 2026-07-06：强制减法 + DEB/hourly 口径审计修正

- 目标：停止加功能，只做减法、bug fix、审计和逻辑落纸；不新增 UI 组件、后端 endpoint 或 collector；继续以 PolyWX 为 benchmark，承认当前看板仍不能用于真实自动赚钱。
- 改动：`frontend/src/App.tsx` 将顶部状态条收缩为 Forecast / METAR / Historical / Last refresh 四个 pill，并把 production refresh 进度隐藏到 `?debug=1`；推荐关注卡压缩为城市状态、当前温度到 DEB、bucket/edge、Polymarket 链接四行，gate 细节进 tooltip；移除顶部 auto paper/live locked 等噪声。`frontend/src/components/WeatherPanel.tsx` 删除 Forecast snapshot cards、Schema notes、DEB metadata、WeatherBot 盘口/token/gate 附加说明、额外指标说明和四个白色 metric boxes；Bucket grid 改为两行核心信息，bid/ask 进 tooltip。
- 改动：`weatherbot_v3/metar.py`、`weatherbot_v3/scheduler.py`、`weatherbot_v3/cli.py` 把 recent METAR 默认窗口从 6 小时改成 24 小时，避免打开当天页面时只有少数白线点。`weatherbot_v3/hourly.py` 改为每小时按 `report_time` 选择最新观测，保留 METAR / China Live / PWS / Historical 独立来源，不再用小时内最大值冒充当前观测；无 METAR 但有 mesonet 时 primary source 标记为 `mesonet_other`，不插值、不伪造 METAR。
- 改动：`weatherbot_v3/deb.py` 的 DEB μ 下限改为 `observed_max_so_far - 0.5°C`，同时覆盖 ensemble 和 fallback 路径；新增 Tokyo observed-floor 回归测试，并调整 Chicago 回归预期。新增 `docs/IMPLEMENTATION_LOGIC_CN.md` 落纸当前 L0-L9 实现路径、数据边界、算法口径、执行边界和未完成项。
- 审计：本地新增 `audits/deb_audit_2026-07-05.md` 与 `audits/hourly_gaps_audit_2026-07-05.md`（不提交），记录 DEB 数据流、Tokyo 2026-07-05 Gamma/Polymarket resolved bucket 26C 对照、当前 DB 无足够本地 replay 样本、以及 hourly/METAR 缺口与修复方案。
- 验证：`python -m unittest tests.test_polywx_contract` 通过 13 tests OK；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`python -m unittest discover tests -v` 通过 212 tests OK；`git diff --check` 待提交前复跑。
- 结论：本轮显著减少了看板肥胖和内部解释噪声，也修正了 METAR 窗口、小时观测聚合和 DEB observed floor 这三个会直接影响预测读数的问题。但当前模型仍未证明盈利，PolyWX 字段级对齐和 DEB 数值 benchmark 仍未完成，live 继续锁定。
- 下一步：用 PolyWX Chicago/Tokyo/Shanghai 的真实截图/DOM/XHR 做字段级 benchmark，优先核对 Hourly 曲线、DEB μ/σ、peak hour、METAR 派生字段和 Probability buckets；不要再扩新功能，先把现有链路跑准。
- 相关提交：待提交。

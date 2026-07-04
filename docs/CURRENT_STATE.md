# WeatherBot Current State

## 当前 Phase 与可用性结论

- 当前处于 **Phase 1.5 -> Phase 2**：可观察、可模拟、可继续生产化验证。
- 看板已接入城市/日期证据、小时图、DEB 高斯桶、市场桶、信号决策、抓取日志和 paper 工作台。
- Layer 7 核心图表已完成一轮 PolyWX 对齐：小时图多系列点线、Cloud Area、peak 标记、三行统计徽章、高斯桶蓝/灰高亮和紧凑 bucket 表。
- 后端启动应保持轻量；抓取只能由按钮、CLI、显式环境变量或手动启动的 scheduler 触发。
- 受控 scheduler 已接入：默认停止，可手动启动/停止，刷新 enabled 城市并通过三徽章显示 Forecast/METAR/Historical 状态。
- 推荐关注已改为读取最新 `signal_decisions`：后端返回 `recommendations`，前端 10-30 秒轮询；最新诊断确认推荐 gate 已拆成 `today_observation` 与 `forecast_lead`，D+1/D+2 不再被 stale METAR 误杀，剩余主因是 `paper_gate_blocked`/样本不足/settlement gate。
- Polymarket Gamma 结算源核验已接入：7 个重点城市均找到活跃市场，6 个 station match；Hong Kong 市场按 HKO 天文台结算，与 VHHH 观测站不一致，live gate 阻塞、paper 保留。
- China Weather Live 已接入：Hong Kong 用 HKO rhrread，Shanghai 用 weather.com.cn `sk_2d/101020100`；只写 `mesonet_observations` 做展示/证据，不解锁 live gate。
- Wunderground/Weather.com PWS collector 已接入为 display-only mesonet：`pws-fetch` 与 METAR poller 同频路径可用；当前未配置 API key，5 个美国城市真实命令返回 skipped，不造假数据。
- DEB `peak_hour` 已改用 hourly_consensus 混合曲线：过去小时用观测覆盖 forecast，并列取最晚；Chicago 2026-07-02 已验证为 mixed `16:00`。
- `LIVE_TRADING=false`，实盘仍锁定；当前不能承诺自动赚钱或无人值守实盘。
- 本轮文档治理复核后，开工只读本文件；历史细节按需看 `PROJECT_PROGRESS_CN.md` 或 `docs/PROGRESS_ARCHIVE_CN.md`。

## 最近 5 条 ledger 摘要

- 2026-07-04 / Layer 2/4/6 PWS、DEB peak 与 C 桶口径 / 结论：Chicago 2026-07-02 审计确认 forecast-only 15:00 与 observed tie 13-16 的差异，DEB 已改 mixed curve argmax 并落 `peak_hour=16:00`；新增 `wunderground_pws` collector/CLI/scheduler 接入，当前缺 API key 所以真实 5 城 skipped；C 桶概率改为截断 `[23,24)`。
- 2026-07-04 / Git 落盘 + 推荐 gate 诊断 / 结论：四个 Layer commit 已落盘；诊断脚本确认推荐=0 不是前端问题，且发现少量 D+1/D+2 被 D+0 METAR freshness 误杀，已拆成 `today_observation` 与 `forecast_lead` gate。
- 2026-07-04 / Layer 6/7 推荐关注闭环 / 结论：推荐卡改由最新 `signal_decisions` 生成，METAR age、verified、DEB、bucket、edge、Polymarket 链接和无市场观察分支已接入；30 分钟 scheduler 实测 METAR 7/7 OK，但 derive 后推荐从 5/2 个降为 0，下一步查 `paper_gate_blocked` 和 `settlement_rule_unverified`。
- 2026-07-04 / Layer 7 PolyWX 图表整改 / 结论：Hourly Temperature 与 Probability buckets 已按 PolyWX 风格重做，Chicago 与 Shanghai 浏览器截图通过，无 console error/横向溢出；下一步：继续组件拆分或查 current date 数据完整性。
- 2026-07-04 / Layer 2 China Weather Live mesonet / 结论：HKO rhrread 与 weather.com.cn sk_2d 已写入 mesonet，小时图输出 `china_live` 红线值；display only，不覆盖 METAR，不解锁 live；下一步：browser QA 或 HKO truth collector 单独轮次。

## 生产阻塞项清单

- truth 独立结算日样本不足，不能解锁实盘校准。
- 仍有未核验城市 `settlement_rule_unverified`；Hong Kong 为 HKO 结算但当前观测站是 VHHH，必须补 HKO truth collector 后才可讨论 live。
- China Weather Live 当前是 `display_only/not_settlement_truth`，不能作为解锁 live 的 truth 证据。
- Wunderground PWS 当前缺 `WUNDERGROUND_API_KEY` 或 `WEATHER_COM_API_KEY`，只能保持 collector/CLI/scheduler 路径就绪，不能产生 PWS 实况点。
- 多模型 forecast archive 与 station truth 的 walk-forward 验证还不够。
- orderbook 级 replay、滑点、退出流动性和成交失败模拟仍需补强。
- 推荐关注链路可用，但最新验证未稳定保留 ≥2 个候选；D+1/D+2 已改用 forecast freshness，D+0 仍可能因 METAR 报文真实发布节奏超过 30 分钟而被拦截。
- allowed 策略组未证明长期 ROI 为正且优于 blocked 组。
- 14-30 天 paper validation、dry-run、重复订单保护和 canary gate 未完成。
- 部分城市/日期小时图仍可能因数据缺口或聚合口径显示诚实空态，例如 Shanghai 2026-07-03。
- scheduler 已完成 30 分钟 METAR 长跑，METAR poller 7/7 OK、0 fail/hr；forecast poller 仍需要 60 分钟长跑观测。
- `WeatherPanel.tsx` 仍偏大，后续 UI 迭代应拆出 HourlyChart、ProbabilityBuckets、EvidenceBadges。
- `dashboard_server.py` 存在测试期 SQLite connection ResourceWarning，后续非文档轮次应修。
- 实盘按钮必须保持锁定，除非任务明确进入 canary 验收。

## 未来 5 分钟内的下一步

- 先运行 `python -m weatherbot_v3.cli state-print` 快速确认本文件摘要。
- 若查结算源，先用 `python -m weatherbot_v3.cli polymarket-market-probe --city <key>` 核验 Gamma 规则，不要凭城市名猜 station。
- 若查中国实况，先用 `python -m weatherbot_v3.cli china-weather-fetch --city shanghai --city hongkong`，再看 `mesonet_observations.network=china_live`。
- 若查 PWS，先确认 `WUNDERGROUND_API_KEY` 或 `WEATHER_COM_API_KEY`，再跑 `python -m weatherbot_v3.cli pws-fetch --city chicago --dry-run`。
- 若查推荐为空，先跑 `python tools/diagnose_recommendation_gate.py`，再看 `/api/dashboard.recommendations.skipped`；重点区分 scheduler 停跑导致的 stale 与 signal decision 自身 gate。
- 若查刷新问题，先看 `/api/scheduler/status` 是否 running、last_run_at、fails_last_hour 和 next_run_at。
- 若改 UI，先验证 `/api/dashboard` 与浏览器实际状态；本轮截图在 `audits/layer7-polywx-visual-2026-07-04/`。
- 若改数据/策略，按 Build Order 一次只动一个 layer 加直接消费者。
- 不跑 Firecrawl，除非任务明确要求或现有 evidence 不足。
- 收尾必须更新 `PROJECT_PROGRESS_CN.md` 和本文件。

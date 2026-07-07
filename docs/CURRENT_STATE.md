# WeatherBot Current State

## 当前 Phase 与可用性结论

- 当前处于 Phase 1.5 -> Phase 2：可观察、可模拟、可继续生产化验证，但不能宣称自动实盘盈利。
- 本轮修复调度器与云量口径：调度器 poller 完成后前端会主动刷新 dashboard/market/signal/DEB 查询；PWS 失败不再让核心 METAR poller 退避。
- Hourly 主图云量改用 `forecast_cloud_cover`，METAR 表仍保留 METAR 云层解析出的 `cloud_cover`，避免把观测云层和预报云量混在同一条面积图里。
- 上轮继续做 PolyWX 对齐减法：中间主板删除 Delta Audit 入口、内部 `METAR --`/`证据 F/H`/模块计数和顶部“刷新当前城市”；推荐关注移到城市横条上方，只显示城市、现在温度、预计最高温。
- 后端没有新增 endpoint、collector 或实盘路径；`LIVE_TRADING=false`，实盘仍锁定。
- 修正了 Hourly 图表口径：云量不再用湿度兜底；无真实 China Live/PWS 数据不画点；日期前后按钮按日历日切换。
- 修正了 METAR 派生字段链路：`visibility`、`condition/wx`、`cloud_cover` 从 METAR raw/解析产物进入 hourly consensus 与 UI。
- 当前验证通过：`python -m unittest tests.test_polywx_contract tests.test_scheduler`、`python -m unittest tests.test_v3_core`、`npm run build`、`git diff --check`；浏览器打开 Shanghai 2026-07-06 无 console error。

## 最近 5 条 ledger 摘要

- 2026-07-07 / Layer 2/4/7 / 调度器刷新链路和云量口径修正：poller 完成会刷新前端查询，PWS 失败不阻断 METAR，主图云量走 forecast cloud；下一步跑 10-30 分钟 scheduler 观察实时更新。
- 2026-07-06 / Layer 2/4/7 / PolyWX 对齐减法 + METAR 派生字段修正完成；下一步重启前后端后人工核验 Shanghai/Chicago 页面。
- 2026-07-06 / Layer 2/4/7 / 强制减法与 DEB/hourly 口径修正完成；下一步用真实 PolyWX benchmark 继续核对曲线和 DEB 数值。
- 2026-07-06 / Layer 7 / Round 5 UI 已完成城市状态、i18n、动态桶表、Delta/Alpha 展示；下一步继续减掉非主路径说明。
- 2026-07-05 / Round 4 / Ensemble DEB 和 Previous Runs 入口已落地；Beijing 34C sanity 暴露模型校准仍不可靠。
- 2026-07-05 / Round 3 / Truth Layer 三源协议、Gamma 结构化持久化、亚洲城市 registry 完成；IEM 仍只能作 approximation。

## 生产阻塞项清单

- PolyWX 对标还未做到字段级一致；云量主图口径已改为 forecast cloud，但 Forecast/China Live/DEB 数值仍需继续 benchmark。
- 结算 truth 独立样本不足，IEM/AWC/METAR 不能替代 Wunderground/HKO 直接解锁 live。
- Open-Meteo Previous Runs 与市场高置信桶存在明显偏差，模型校准还不能用于真实资金。
- 推荐卡可读性已改善，但推荐数量仍可能因 gate 严格而为 0，需要用诊断脚本解释原因。
- Orderbook replay、滑点、成交失败、退出流动性、结算延迟还未达到生产验收。
- 14-30 天 paper validation、dry-run、canary gate 均未完成。

## 未来 5 分钟内的下一步

- 若核对 UI，当前后端 `8765` 与 Vite `5173` 已启动；打开 `http://127.0.0.1:5173/?city=shanghai-zspd&date=2026-07-06`。
- 若核对数据，先看 `/api/dashboard` 的 `weather_city_series`，再看 `/api/hourly-consensus?city=shanghai&target_date=2026-07-06`。
- 若继续整改，优先启动调度器跑 10-30 分钟，观察顶部 poller age 和 `/api/hourly-consensus` 是否随 completed poller 刷新；然后再处理 Forecast/China Live 数值差异。
- 若提交代码，确认不 stage `audits/`、`data/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`、`node_modules/`。

# WeatherBot Current State

## 当前 Phase 与可用性结论

- 当前处于 Phase 1.5 -> Phase 2：可观察、可模拟、可继续生产化验证，但不能宣称自动实盘盈利。
- 本轮按“强制减法 + 算法审计”收缩 UI：顶部只保留 Forecast / METAR / Historical / Last refresh 四个状态，推荐卡和桶表只显示交易判断必需字段。
- 后端没有新增 endpoint、collector 或实盘路径；`LIVE_TRADING=false`，实盘仍锁定。
- 修正了两条数据口径：METAR 增量抓取默认拉最近 24 小时；hourly consensus 按小时取最新观测而不是最大值。
- DEB μ floor 改为 `observed_max_so_far - 0.5°C`，避免模型明显低于日内实况仍参与判断。
- 当前完整测试通过：`python -m unittest discover tests -v` 共 212 tests OK；前端 `npm run build` 通过。

## 最近 5 条 ledger 摘要

- 2026-07-06 / Layer 2/4/7 / 强制减法与 DEB/hourly 口径修正完成；下一步用真实 PolyWX benchmark 继续核对曲线和 DEB 数值。
- 2026-07-06 / Layer 7 / Round 5 UI 已完成城市状态、i18n、动态桶表、Delta/Alpha 展示；下一步继续减掉非主路径说明。
- 2026-07-05 / Round 4 / Ensemble DEB 和 Previous Runs 入口已落地；Beijing 34C sanity 暴露模型校准仍不可靠。
- 2026-07-05 / Round 3 / Truth Layer 三源协议、Gamma 结构化持久化、亚洲城市 registry 完成；IEM 仍只能作 approximation。
- 2026-07-04 / Layer 6/8 / 三策略和 Kelly 仓位已接入；真实候选仍被样本、settlement、spread gate 阻塞。

## 生产阻塞项清单

- PolyWX 对标还未做到字段级一致，尤其 DEB 数值、Hourly 曲线、METAR 表派生字段仍需继续 benchmark。
- 结算 truth 独立样本不足，IEM/AWC/METAR 不能替代 Wunderground/HKO 直接解锁 live。
- Open-Meteo Previous Runs 与市场高置信桶存在明显偏差，模型校准还不能用于真实资金。
- 推荐卡可读性已改善，但推荐数量仍可能因 gate 严格而为 0，需要用诊断脚本解释原因。
- Orderbook replay、滑点、成交失败、退出流动性、结算延迟还未达到生产验收。
- 14-30 天 paper validation、dry-run、canary gate 均未完成。

## 未来 5 分钟内的下一步

- 若继续本轮，先看 `audits/deb_audit_2026-07-05.md` 和 `audits/hourly_gaps_audit_2026-07-05.md` 的结论。
- 若核对 UI，打开 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-04`，只检查四状态、Hourly、DEB、Bucket 和推荐卡。
- 若核对数据，先跑 `/api/hourly-consensus` 与 `/api/daily-max-predictions`，再和 PolyWX 截图/DOM 对照。
- 若提交代码，确认不 stage `audits/`、`data/`、`.env`、`config.json`、`.venv/`、`frontend/dist/`、`node_modules/`。

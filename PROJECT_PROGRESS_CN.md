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

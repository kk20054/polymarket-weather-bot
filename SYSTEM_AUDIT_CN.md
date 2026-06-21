# WeatherBot v2 -> v3 系统审计

审计日期：2026-06-21

## 当前 v2 状态

- 代码形态：`bot_v2.py` 是扫描、预测、信号、模拟持仓、止损、结算的单体脚本。
- 看板形态：`dashboard_server.py` 提供 FastAPI API，`frontend/` 是 React/Tailwind 看板。
- 数据形态：旧信号在 `data/weatherbot.db`，市场/持仓主要在 `data/markets/*.json`。
- 模拟盘：旧模拟盘可以记录信号和本地持仓，但原先不是严格按订单生命周期建模。
- 实盘：旧版没有真正的 Polymarket CLOB 执行器。
- AI/飞书：旧版没有 AI 审核和飞书通知链路。

## 已知问题

- 单体脚本职责过重，继续迭代实盘会难以审计。
- 旧日志和部分前端文案存在历史乱码。
- 旧看板进程检测只识别看板自身启动的扫描器。
- 旧 JSON 市场文件和 SQLite 信号库并存，状态来源不统一。
- 早期信号曾把确定性预测当作高置信概率，导致 EV 被放大。
- 实盘关键能力缺失：订单最小量、tick size、余额、重复下单保护、熔断、通知。

## v3 本次落地内容

- 新增 `weatherbot_v3` 包，拆出配置、数据库、Polymarket 行情校验、模拟执行器、实盘执行器、AI 审核、飞书通知。
- 新增 v3 SQLite schema：markets、forecasts、orderbooks、signals、ai_reviews、paper_orders、live_orders、fills、settlements、risk_events、notifications。
- 旧扫描器生成信号时同步写入 v3 signals。
- 看板启动时初始化 v3 数据库并迁移旧信号。
- 旧“模拟买入”接口接入 v3 `PaperExecutor`，先校验实时盘口、最小订单和 tick size。
- 新增 `/api/v3/live-order`：默认被 `LIVE_TRADING=false` 保护，不会实盘下单。
- 新增 `/api/v3/status` 和 `/api/v3/notify-daily`。
- 前端 `$` 按钮改为调用 v3 live-order；新增 Daily 按钮触发飞书日结摘要。

## 实盘前仍需完成

- 安装并验证 Polymarket CLOB SDK 和真实钱包凭据。
- 增加账户余额查询、每日额度统计、最大持仓数统计、总回撤统计。
- 将云端 worker 和本机看板拆开部署。
- 用至少 7 天模拟结果验证策略稳定性。


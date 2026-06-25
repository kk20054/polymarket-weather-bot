# WeatherBot v3 说明

v3 是生产化骨架：模拟盘、AI 审核、飞书通知和小额实盘执行器都从旧扫描器里拆出来。

默认状态是安全的：

- `LIVE_TRADING=false`
- `LIVE_DRY_RUN=true`
- `AI_REVIEW_ENABLED=false`
- `AI_REQUIRED_FOR_LIVE=false`

## 初始化

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m weatherbot_v3.cli init-db
.\.venv\Scripts\python.exe -m weatherbot_v3.cli migrate
.\.venv\Scripts\python.exe -m weatherbot_v3.cli data-readiness
.\.venv\Scripts\python.exe -m weatherbot_v3.cli forecast-backfill --days 4
.\.venv\Scripts\python.exe -m weatherbot_v3.cli orderbook-backfill --limit 20
```

`forecast-backfill` 只抓取并保存预测运行，不生成信号、不修改模拟持仓。它按每个机场的当地日期保存 ECMWF、GFS ensemble、GFS seamless 短临和 METAR 观测。

`orderbook-backfill` 刷新近期信号对应的真实 CLOB 多档盘口，不下单。模拟成交只消耗限价以内的真实 ask 深度，未成交金额保留为现金。

## 可选环境变量

```powershell
$env:AI_REVIEW_ENABLED="true"
$env:AI_PROVIDER="minimax"
$env:MINIMAX_API_KEY="你的 MiniMax Key"
$env:MINIMAX_BASE_URL="https://api.minimax.io/v1"
$env:MINIMAX_MODEL="MiniMax-M3"

$env:FEISHU_WEBHOOK_URL="你的飞书机器人 webhook"

$env:LIVE_TRADING="false"
$env:LIVE_DRY_RUN="true"
$env:LIVE_MAX_ORDER_USD="2"
$env:LIVE_DAILY_MAX_USD="10"
```

实盘需要额外配置 Polymarket CLOB 私钥和 API 凭证。第一版默认 dry-run，不会真实下单。

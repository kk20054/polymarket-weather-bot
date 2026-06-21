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
```

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


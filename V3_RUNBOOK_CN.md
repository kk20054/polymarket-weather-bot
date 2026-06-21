# WeatherBot v3 运行手册

## 初始化

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m weatherbot_v3.cli init-db
.\.venv\Scripts\python.exe -m weatherbot_v3.cli migrate
```

## 启动看板

```powershell
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173/
```

## 安全默认值

默认配置不会实盘下单：

```powershell
$env:LIVE_TRADING="false"
$env:LIVE_DRY_RUN="true"
```

前端信号表里的 `$` 按钮会调用 v3 实盘接口，但在默认配置下只会记录被保护/阻断的订单。

## MiniMax AI 审核

```powershell
$env:AI_REVIEW_ENABLED="true"
$env:AI_PROVIDER="minimax"
$env:MINIMAX_API_KEY="你的 MiniMax API Key"
$env:MINIMAX_BASE_URL="https://api.minimax.io/v1"
$env:MINIMAX_MODEL="MiniMax-M3"
$env:AI_REQUIRED_FOR_LIVE="true"
```

AI 关闭时系统照常运行。AI 开启且 `AI_REQUIRED_FOR_LIVE=true` 时，AI 拒绝、超时或输出非法 JSON 都会阻止实盘。

## 飞书通知

```powershell
$env:FEISHU_WEBHOOK_URL="你的飞书自定义机器人 webhook"
```

看板 `Daily` 按钮会发送日度摘要；未配置 webhook 时只写入本地 notifications 记录。

## 实盘前检查

实盘前必须满足：

- 模拟盘连续运行至少 7 天。
- 至少 30 个模拟仓位已结算。
- `/api/v3/status` 无异常风险事件。
- 飞书通知可达。
- CLOB dry-run 成功。
- `LIVE_MAX_ORDER_USD`、`LIVE_DAILY_MAX_USD`、`LIVE_MAX_OPEN_POSITIONS` 已按你的风险承受能力设置。


# WeatherBot v4 生产化升级说明

本版本把核心风控从“只看 EV 信号”推进到“结算源 truth + 整场概率分布 + 盘口约束 + 回测准备度”。默认仍然是模拟优先，实盘自动买入会被严格阻塞，直到样本和 truth 覆盖率达标。

## 新增能力

- 结算源 truth：系统会记录 `actual_provider`、`actual_station`、`actual_confidence`、`actual_calibration_eligible`，Open-Meteo archive 只作为低置信 fallback。
- 整场分布：信号展开后会显示同一事件所有温度桶的归一化概率，避免单个尾部桶被模型过度放大。
- truth 健康面板：看板会显示每个城市的站点、可校准样本数、fallback 数量和阻塞原因。
- 实盘准备度：只要 truth 样本不足、存在 Open-Meteo fallback 或旧数据来源未知，自动实盘会保持阻塞。
- canary dry-run：新增 `/api/executor/canary-dry-run`，用于验证 `$1-$2` 小额实盘订单会被怎样处理，但默认不会真实下单。

## 建议配置

复制 `config.example.json` 为 `config.json` 后，重点检查：

```json
{
  "TRUTH_PROVIDER_MODE": "official_paid",
  "VISUAL_CROSSING_KEY": "YOUR_VISUAL_CROSSING_KEY",
  "OPEN_METEO_ACTUAL_ALLOWED_FOR_PAPER": true,
  "OPEN_METEO_ACTUAL_ALLOWED_FOR_LIVE": false,
  "MIN_INDEPENDENT_SETTLEMENT_DAYS": 20,
  "LIVE_TRADING": false,
  "LIVE_DRY_RUN": true,
  "CANARY_MAX_ORDER_USD": 2
}
```

`VISUAL_CROSSING_KEY` 不是必须，但没有它时很多非美国/历史站点 truth 只能停留在低置信状态。实盘前建议配置。

## 使用方式

后端：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

前端：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

扫描器：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe weatherbet.py
```

## API

- `GET /api/truth/coverage`：查看本地 truth 覆盖率和 DB 覆盖率。
- `GET /api/markets/{market_id}/distribution`：查看某个市场对应事件的整场温度桶分布。
- `GET /api/signals/{signal_id}/decision`：查看该信号被允许、观察或阻塞的结构化原因。
- `GET /api/backtest/policies`：查看策略候选和实盘准备度。
- `POST /api/executor/canary-dry-run`：用 live executor 做 canary dry-run。

## 实盘前硬门槛

- 连续模拟稳定运行至少 14 天。
- 至少 30 个已结算独立 paper 仓位。
- 可允许策略组 ROI 为正，并明显优于 blocked 组。
- truth coverage 达到 90% 以上。
- 不允许 Open-Meteo-only actual 作为实盘校准样本。
- 第一笔真实订单只能是 `$1-$2` canary。

当前版本仍不能保证盈利；它的目标是先把“为什么买、为什么不买、买了是否真有 edge”变成可审计数据。

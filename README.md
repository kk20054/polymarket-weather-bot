# WeatherBot v6

本项目是一个本地运行的 Polymarket 天气量化交易平台，目标路径是：

```text
真实数据基座 -> 可审计概率模型 -> 真实盘口模拟 -> 生产看板 -> 14-30 天 paper 验证 -> 小额实盘 canary
```

当前版本适合人工核验、数据复盘和受控模拟，不适合无人值守实盘。`LIVE_TRADING=false` 是默认红线。

## 当前能力

- 城市/结算站注册表：按 Polymarket 最高温市场的结算站点维护 ICAO、时区、单位和规则文本。
- 天气数据层：METAR、Open-Meteo 多模型预报、Open-Meteo historical display-only、China Weather Live、PWS display-only。
- 预测层：小时 consensus、DEB 日最高温 `mu/sigma`、高斯概率桶。
- 市场层：Polymarket Gamma/CLOB 市场桶、YES token、best bid/ask、spread、tick size、orderMinSize。
- 策略层：`single_bucket_ev`、`ladder_grid`、`tail_buying`，带 fractional Kelly sizing。
- 执行层：paper executor 与 live executor 分离；live 保持锁定，只允许后续 canary 验收后开启。
- 看板：PolyWX 风格城市工作台，包含 hourly temperature、概率桶、推荐关注、scheduler 状态、模拟账户和交易记录。

## 目录实现路径

| 路径 | 作用 |
|---|---|
| `dashboard_server.py` | FastAPI 后端入口，只做 API 和适配层。 |
| `weatherbot_v3/` | 生产化核心模块：数据采集、DB、DEB、市场桶、信号、策略、paper/live executor、scheduler。 |
| `frontend/` | Vite + React + TypeScript + Tailwind + Recharts 看板。 |
| `data/weatherbot_v3.db` | v3 主 SQLite 状态库，本地文件，不提交 Git。 |
| `data/weatherbot.db` | legacy `weatherbet.py` 旧库/旧状态，不是当前主路径。 |
| `audits/` | 本地审计和验证报告，不提交 Git。 |
| `docs/CURRENT_STATE.md` | 当前进度摘要，开发前优先阅读。 |
| `AGENTS.md` | 本项目开发规则和安全红线。 |
| `legacy/`、`weatherbet.py` | 旧版单体 bot，仅作参考，不作为 v6 主运行入口。 |

## 本地启动

在 PowerShell 中进入项目根目录：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
```

启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

另开一个 PowerShell 启动前端：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

打开看板：

```text
http://127.0.0.1:5173/
```

后端 API：

```text
http://127.0.0.1:8765/api/dashboard
```

## 端口占用处理

查看并停止 8765 后端端口：

```powershell
$pids = (Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess | Sort-Object -Unique
$pids | ForEach-Object { Stop-Process -Id $_ -Force }
```

查看并停止 5173 前端端口：

```powershell
$pids = (Get-NetTCPConnection -LocalPort 5173 -State Listen).OwningProcess | Sort-Object -Unique
$pids | ForEach-Object { Stop-Process -Id $_ -Force }
```

如果只想一键重启旧 dashboard 进程，可尝试：

```powershell
.\scripts\restart_dashboard.ps1
```

## 看板操作

1. 左侧选择城市，例如 `Chicago · KORD`。
2. 中间看 hourly temperature、METAR、Historical、PWS/China Live、DEB 高斯桶和市场桶。
3. 顶部 `刷新当前城市` 会跑一次受控 production refresh，不会启动常驻 scheduler。
4. 顶部 `启动调度器` 会启动 server-side poller，按频率刷新 enabled 城市：
   - METAR：约每 5 分钟
   - China Live：约每 5 分钟
   - derive：约每 15 分钟
   - forecast：约每 60 分钟
5. 右侧 `一键模拟` 只影响 paper/simulation，不代表实盘下单。
6. `实盘锁定` 是正常状态；没有完成 canary 验收前不要解锁 live。

## 常用 CLI

初始化/同步基础表：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli init-db
.\.venv\Scripts\python.exe -m weatherbot_v3.cli stations-sync
```

拉取单城市 Open-Meteo 多模型预报：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli openmeteo-fetch --city chicago --forecast-days 7
```

回填 Chicago 30 天 historical display-only 小时数据：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli history-backfill --city chicago --days 30
```

构建小时 consensus、DEB 和信号：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli hourly-consensus-build --city chicago --target-date 2026-07-05
.\.venv\Scripts\python.exe -m weatherbot_v3.cli daily-max-build --city chicago --target-date 2026-07-05
.\.venv\Scripts\python.exe -m weatherbot_v3.cli market-buckets-sync --active-weather --cities chicago --target-date 2026-07-05
.\.venv\Scripts\python.exe -m weatherbot_v3.cli signal-decisions-build --city chicago --target-date 2026-07-05
```

诊断推荐为空：

```powershell
.\.venv\Scripts\python.exe tools\diagnose_recommendation_gate.py
```

## 人工核验清单

启动后先确认后端健康：

```powershell
Measure-Command { Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/dashboard' | Out-Null } | Select-Object TotalMilliseconds
$d = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/dashboard'
[pscustomobject]@{
  scanner_status = $d.stats.scanner_status
  is_running = $d.stats.is_running
  auto_simulation_enabled = $d.stats.auto_simulation.enabled
  production_running = $d.production_refresh.running
  scheduler_running = $d.scheduler_status.running
  recommendations = $d.recommendations.count
} | ConvertTo-Json
```

正常轻量启动应看到：

- `scanner_status=stopped`
- `is_running=false`
- `auto_simulation_enabled=false`
- `production_running=false`
- `scheduler_running=false`

点击 `启动调度器` 后，再看：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/scheduler/status' | ConvertTo-Json -Depth 6
```

重点看每个 poller 的 `last_run_at`、`last_duration_ms`、`fails_last_hour`、`next_run_at`。

## 测试

后端核心测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_v3_core
```

PolyWX/dashboard contract：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_polywx_contract tests.test_v3_core
```

前端构建：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run build
```

## 配置与密钥

本地配置文件：

- `config.json`
- `.env`

这些文件不要提交 Git。

常见可选环境变量：

| 变量 | 默认 | 说明 |
|---|---:|---|
| `LIVE_TRADING` | `false` | 实盘总开关，默认关闭。 |
| `V3_DB_PATH` | `data/weatherbot_v3.db` | v3 SQLite 主库路径。 |
| `WEATHERBOT_SCHEDULER` | `false` | 是否后端启动时自动启动 scheduler；默认不要开。 |
| `MINIMAX_API_KEY` | 空 | 可选 AI 审核。 |
| `FEISHU_WEBHOOK_URL` | 空 | 可选飞书通知。 |
| `WUNDERGROUND_API_KEY` / `WEATHER_COM_API_KEY` | 空 | 可选 PWS 数据。 |

## 当前限制

- 不能承诺稳定赚钱；必须先完成 14-30 天 paper validation。
- Open-Meteo Historical、China Live、PWS 当前多数是 display-only 或 research evidence，不直接解锁 live gate。
- 部分城市 settlement truth 和 Polymarket 规则仍需持续核验。
- 推荐为空不一定是抓取失败，常见原因是 gate、settlement verification、paper gate 或 decision round 过滤。
- legacy `weatherbet.py` 可以运行旧流程，但不代表 v6 生产化平台主路径。

## 风险声明

Polymarket 天气市场是高风险预测市场。天气桶通常很窄，1-2F 的站点、口径或时间窗口偏差就可能导致全亏。本项目当前用于研究、人工核验和受控模拟；实盘前必须先通过 paper 验证、dry-run、重复订单保护和 canary 验收。

# WeatherBot v6

WeatherBot 是一个本地运行的 Polymarket 城市日最高温研究与模拟交易平台。当前主线目标是：

```text
真实数据采集 -> 可审计概率模型 -> 实时盘口匹配 -> 模拟成交与结算 -> 14-30 天验证 -> 小额实盘 canary
```

当前版本可用于数据观察、信号研究和受控模拟交易。它尚未证明具有稳定盈利能力，`LIVE_TRADING=false` 必须保持关闭。

## 当前可用性

截至 2026-07-20：

- 后端、前端、调度器、天气采集、Polymarket Gamma/CLOB 盘口、模拟成交和模拟结算链路可运行。
- 已注册 51 个城市，其中 49 个城市有活跃天气市场映射。
- 当前模拟 cohort 为 `paper-20260719T080517Z-63d973b3`，本金 `$40`，单笔上限 `$2`，最多同时持仓 5 笔。
- 当前 cohort 已产生 6 笔模拟订单：1 笔已结算亏损 `$1.26`，5 笔仍持仓或等待市场结算。
- 当前 cohort 使用 `single_bucket_ev + ladder_grid`，退出方式固定为 `hold_to_settlement`。
- `model_guarded` 模型失效退出已实现，但只对新建 cohort 生效，不会改写当前实验。
- PWS 仍缺具备 Weather Underground PWS 产品权限的 API key，因此保持禁用。
- 实盘执行器、资金授权和 canary 验收尚未完成，不能自动实盘。

这份状态会变化。实时状态以看板和下面的命令为准：

```powershell
./.venv/Scripts/python.exe -m weatherbot_v3.cli source-health
./.venv/Scripts/python.exe -m weatherbot_v3.cli paper-cohort-status
```

## 系统结构

```text
天气源 / 观测源
  |-- Weather.com v3 hourly forecast
  |-- Open-Meteo NWP models
  |-- AWC / IEM METAR
  |-- WU hourly / daily history
  |-- China Weather Live / HKO
  `-- PWS (有授权时)
          |
          v
hourly_consensus -> Daily Max Prediction (DEB) -> 概率桶
          |                                      |
          `------------------+-------------------'
                             v
Polymarket Gamma/CLOB -> market buckets -> signal decisions
                                                |
                                                v
                                  paper executor / settlement
                                                |
                                                v
                                       React 交易看板
```

| 路径 | 作用 |
|---|---|
| `dashboard_server.py` | FastAPI API 和前端适配层 |
| `weatherbot_v3/` | 采集、DEB、市场桶、策略、模拟执行、结算、调度器 |
| `weatherbot_v3/strategies/` | 三类策略实现 |
| `frontend/` | Vite + React + TypeScript + Tailwind + Recharts 看板 |
| `data/weatherbot_v3.db` | SQLite 主状态库；逻辑路径实际指向 `D:\WeatherBot\data` |
| `data/weatherbot.db` | legacy 旧库，不是 v6 主库 |
| `docs/CURRENT_STATE.md` | 当前开发阶段和阻塞项 |
| `docs/IMPLEMENTATION_LOGIC_CN.md` | Layer 0-9 数据流和边界 |
| `docs/DATA_STORAGE_CN.md` | D 盘数据迁移与恢复说明 |
| `AGENTS.md` | 项目开发、安全和验证规则 |
| `legacy/`、`weatherbet.py` | 旧版参考，不要与 v6 同时运行 |

## 数据源职责

数据源不能混用。看板上的多条曲线代表不同角色，并不保证数值完全一致。

| 数据源 | 当前用途 | 是否结算 truth |
|---|---|---|
| Weather.com v3 | 主小时预报、云量、天气状态，并作为 DEB 的 `v3` 分量 | 否 |
| Open-Meteo | ECMWF、GFS、ICON、GEM、JMA 等独立 NWP 输入 | 否 |
| AWC / IEM METAR | 机场实况、当日最高温下限、模型失效判断 | 否，通常只是近似观测 |
| WU hourly / daily | 历史观测与非香港市场 truth 候选 | 规则指向 WU 时是首选 |
| HKO Daily Extract | 香港市场 truth 候选 | 是，取决于市场规则 |
| China Weather Live | 中国城市分钟级实况辅助 | 否 |
| WU PWS | 邻近个人站趋势和拐点辅助 | 否 |
| Gamma API | 市场、事件、结算状态 | 市场胜负权威来源 |
| CLOB | token、bid/ask、深度、tick、最小订单 | 交易执行依据 |

重要边界：PWS 和 China Live 可以帮助判断温度是否见顶，但不能替代市场规则指定的结算站。

## 首次安装

在 PowerShell 中进入项目：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
```

创建 Python 环境并安装依赖：

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

安装前端依赖：

```powershell
cd frontend
npm install
cd ..
```

密钥放在项目根目录 `.env`，不要提交 Git。可在看板“设置”中查看掩码状态和测试 API。

## 本地启动

需要两个 PowerShell 窗口。

窗口 1，启动后端：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
./.venv/Scripts/python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

窗口 2，启动前端：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173/
```

后端启动后默认不会自动启动调度器。可在顶栏点击“启动调度器”，或执行：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/scheduler/start
```

查看状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/scheduler/status
Invoke-RestMethod http://127.0.0.1:8765/api/source-health
```

停止调度器：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/scheduler/stop
```

## 日常操作

1. 启动后端和前端，确认页面没有长期显示“正在连接”。
2. 启动调度器，等待数据源徽章转为绿色。
3. 从左侧按洲、时区或字母选择城市，再选择当地日期。
4. 在“预报”页检查 Forecast、METAR、历史观测和 China Live 是否新鲜。
5. 查看 DEB 的预测最高温、标准差、模型来源和权重。
6. 查看温度桶中的模型概率、市场 ask、优势和交易状态。
7. 右侧“策略队列”只展示通过策略计算的候选；“天气关注”只是值得观察，不等于买入。
8. 启动模拟 cohort 后，系统按固定策略版本和 Kelly 风控自动处理新信号。
9. 在“模拟订单”查看入场时间、桶、价格、份额、当前 bid 估值、浮动盈亏和结算结果。
10. 实验结束后导出或记录 cohort 的 ROI、Brier score、回撤和逐笔证据，再决定下一轮参数。

关闭电脑会停止后端和调度器。再次开机后需要重新启动两个服务和调度器；数据库中的历史、订单和 cohort 不会丢失。

## 看懂天气图与 DEB

### 小时图

- Forecast：当天每小时预测温度。
- METAR：结算机场附近的航空观测，是当日温度下限和走势证据。
- Historical：WU 历史/当日观测，适合对照结算口径。
- China Live：国内实况站辅助线，不是结算温度。
- PWS：邻近个人站高频趋势；未授权时不会画线。
- Cloud：与主 forecast 同一快照的云量，不用湿度冒充。
- Peak：混合曲线的预测峰值小时，不是已确定结算值。

### DEB

DEB 将 `v3 / GFS / ECMWF / ICON / GEM / JMA` 等模型去偏后加权，输出：

- `mu`：当日最高温中心预测。
- `sigma`：预测不确定度；越大表示分布越分散。
- observed floor：当日已经观测到的最高温，模型不能再预测低于这个值。
- 每源权重：先验权重结合近 7 日误差调整；缺源时重新归一化。

DEB 不是“最可能温度就是必胜温度”。对于 1°C 或 1°F 窄桶，哪怕中心预测只偏 1 度，也可能完全结算到相邻桶。

## 看懂温度桶

每个桶对应一个 Polymarket YES 市场。

| 字段 | 含义 |
|---|---|
| 模型概率 | DEB 认为当日最高温落入该桶的概率 |
| 市场 ask | 立即买入 YES 需要支付的价格 |
| 市场 bid | 立即卖出 YES 大致能收到的价格 |
| 优势/edge | `模型概率 - ask`，以百分点表示 |
| 毛 EV/份 | 忽略费用时，每份的期望收益近似为 `模型概率 - ask` |
| 浮动盈亏 | 按可卖出的 best bid 估值，不按页面中间价 |

例：模型概率 30%，ask 10¢，优势为 `+20 个百分点`。这只是候选，不是自动买入理由。它还必须通过：

- 市场与温度桶严格匹配。
- 站点、单位、日期和结算规则已核验。
- forecast、DEB、盘口没有过期。
- spread、深度、tick size、最小订单满足要求。
- 模型校准样本和策略阈值满足要求。
- Kelly 仓位大于零且不突破 cohort 风控。

买入后立刻出现小幅浮亏通常是 bid/ask spread，不代表天气判断已错；但宽 spread 会显著侵蚀 edge。

## 当前策略

### 1. Single Bucket EV

实现：`weatherbot_v3/strategies/single_bucket_ev.py`

- 对每个桶独立计算 `model_probability - best_ask`。
- 当前最低 edge 为 5 个百分点。
- 通过完整数据、盘口和风险 gate 后，按 fractional Kelly 分配仓位。
- 优点：简单、可审计，适合建立基线。
- 风险：窄桶对 1 度误差极敏感，尾部概率失准时会连续买错。

### 2. Ladder Grid

实现：`weatherbot_v3/strategies/ladder_grid.py`

- 选择最接近 `mu` 的中心桶和左右各一个相邻桶。
- 三个桶每个都必须至少有 3 个百分点 edge。
- 整组仓位为中心桶 Kelly 仓位的 60%，再按模型概率分配。
- 三个桶原子执行：一个桶盘口不合格，整组跳过。
- 优点：降低最高温刚好落在相邻桶导致全损的风险。
- 风险：多个 ask 的总成本可能过高，分散不等于正期望。

### 3. Tail Buying

实现：`weatherbot_v3/strategies/tail_buying.py`

- 只看 ask 不高于 15¢ 的低价桶。
- 模型概率必须比 ask 高至少 10 个百分点。
- 城市至少需要 20 个独立结算日，避免用少量数据夸大尾部概率。
- 每日最多 5 个候选。
- 当前模拟 cohort 没有启用该策略。
- 风险最高。便宜不等于低风险，低价尾部可以连续归零。

## Kelly 仓位

二元 YES 合约的全 Kelly 比例可写为：

```text
b = 1 / ask - 1
kelly = (p * b - (1 - p)) / b
```

本项目使用 15% fractional Kelly，并额外限制：

- 单笔不超过 bankroll 的 5%。
- 单笔不超过 cohort 的 `max_per_trade_usd`。
- 不超过当日剩余额度、现金和最大同时持仓数。

Kelly 只是在“概率 p 已校准”的前提下控制仓位。若概率本身偏差很大，Kelly 会把模型错误放大，因此 paper 验证优先于提高金额。

## 退出方式

### Hold To Settlement

持有到 Polymarket 结算。优点是实验口径清楚，不会把短时 spread 当成止损信号；缺点是错误仓位会承受全部损失。

### Model Guarded

只对新 cohort 可选：

- 观测最高温已经物理越过目标桶时，尝试按新鲜 best bid 退出。
- 最新模型概率持续跌破阈值，并经过两次独立确认时退出。
- best bid 过旧、深度不足或价格不合法时拒绝退出。

当前 cohort 仍固定为 `hold_to_settlement`。不要在同一 cohort 中途换退出规则，否则无法比较策略。

普通的“价格跌 20% 就止损”不适合薄盘口天气桶：买入 ask 后按 bid 估值会天然亏 spread，容易把噪声固化成真实亏损。

## 市面常见玩法

这些是天气预测市场常见研究方向，不代表本项目都已实现，更不代表必然盈利。

1. 概率差交易：用集合预报生成每桶概率，与可成交 ask 比较。WeatherBot 当前主要属于这一类。
2. 近结算锁峰：D+0 使用 METAR、PWS、云量和剩余升温空间判断日高温是否已形成。项目已有观测 floor 和 guarded exit，完整 peak-lock 入场仍需验证。
3. 相邻桶组合：买中心桶和邻桶，降低边界误差。项目的 Ladder Grid 已实现模拟版本。
4. 模型更新时间差：ECMWF/GFS 新 run 发布后，模型概率先变化、盘口稍后重定价。项目记录 model timing，但不自动追价。
5. 低价尾部：买市场低估的小概率桶。项目有 Tail Buying，但它对概率校准要求最高，当前不应作为主策略。
6. 全套桶套利：若互斥桶的可成交总成本加费用低于 1 美元，理论上存在锁定空间；必须同时核对深度、费用和是否真能全部成交。项目暂未实现自动套利。
7. Maker 挂单：用被动限价减少 spread 和 taker fee，但存在不成交或只成交最差一腿的风险。项目 paper 目前按可成交 ask 模拟，不等于 maker 回测。
8. 跨平台价差：比较 Polymarket、Kalshi 等同口径市场。结算站、单位和规则往往不同，项目尚未接入跨平台执行。

Polymarket 显示价格通常是 midpoint，但实际买入支付 ask、卖出收到 bid；天气市场还可能启用 taker fee。因此任何策略都应以可成交价格、深度和费用计算，而不是只看页面概率。

## 模拟交易建议

推荐先运行 14-30 天，并至少积累 30 个已结算独立仓位。每轮实验只改一个变量。

建议顺序：

1. Cohort A：`single_bucket_ev`，`hold_to_settlement`，作为基线。
2. Cohort B：相同入场策略，改用 `model_guarded`，比较是否降低损失而不过早卖出赢家。
3. Cohort C：加入 `ladder_grid`，比较组合桶的 ROI 和最大回撤。
4. 只有独立结算样本足够后，再单独测试 `tail_buying`。

至少记录：

- 已结算数量、胜率、ROI、最大回撤。
- Brier score 和按城市/lead time/价格桶的校准。
- 买入 ask、退出 bid、spread、深度和费用估计。
- 信号产生到模拟成交的延迟与拒单原因。
- guarded exit 的触发数、成功退出数和 exit regret。

不要只看胜率。买入 5¢ 的合约即使胜率低也可能盈利，买入 40¢ 的合约即使胜率较高也可能亏损；核心是长期校准后的期望收益。

## 当前限制

- 尚无 14-30 天、30+ 独立已结算仓位证明正 ROI。
- 部分城市 WU 历史接口不稳定；Istanbul 当前有明确缺口。
- PWS key 无产品权限，PWS 线保持禁用。
- 部分城市 settlement source 仍有 mismatch 或样本不足，只允许观察/paper。
- 盘口薄、spread 宽时，模型 edge 可能无法兑现。
- paper fill 是基于盘口快照的模拟，不等于真实排队、部分成交和网络延迟。
- 天气市场可能启用 taker fee；实验必须逐步加入费用后的净 PnL。
- live executor、钱包授权、余额检查、撤单与 canary 尚未完成生产验收。
- MiniMax AI 审核和飞书通知不是当前核心盈利依据，默认可以关闭。

## 实盘开放条件

在同时满足以下条件前，不应解除 `LIVE_TRADING=false`：

- 连续 14-30 天调度稳定。
- 至少 30 个独立已结算 paper 仓位。
- 允许策略组费用后 ROI 为正，并优于 blocked/观察组。
- truth coverage、站点和结算规则达到验收阈值。
- 最大回撤、日亏损和重复订单保护通过测试。
- CLOB tick、min order、余额、深度、过期盘口和 idempotency 测试通过。
- 第一笔只允许 `$1-$2` canary，且需要人工确认。

## 常用命令

项目健康检查：

```powershell
./.venv/Scripts/python.exe -m weatherbot_v3.cli state-print
./.venv/Scripts/python.exe -m weatherbot_v3.cli source-health
./.venv/Scripts/python.exe -m weatherbot_v3.cli project-verify --verification-mode paper
```

城市与站点：

```powershell
./.venv/Scripts/python.exe -m weatherbot_v3.cli stations-list
./.venv/Scripts/python.exe -m weatherbot_v3.cli stations-enable --city chicago
./.venv/Scripts/python.exe -m weatherbot_v3.cli stations-disable --city chicago
```

单城市手动刷新：

```powershell
./.venv/Scripts/python.exe -m weatherbot_v3.cli metar-refresh --city chicago --recent-hours 6
./.venv/Scripts/python.exe -m weatherbot_v3.cli weathercom-fetch --city chicago
./.venv/Scripts/python.exe -m weatherbot_v3.cli openmeteo-fetch --city chicago
./.venv/Scripts/python.exe -m weatherbot_v3.cli wunderground-hourly-fetch --city chicago --days 1
```

模拟 cohort：

```powershell
./.venv/Scripts/python.exe -m weatherbot_v3.cli paper-cohort-status
./.venv/Scripts/python.exe -m weatherbot_v3.cli paper-cohort-tick
```

完整 CLI：

```powershell
./.venv/Scripts/python.exe -m weatherbot_v3.cli --help
```

## 测试

后端：

```powershell
./.venv/Scripts/python.exe -m unittest discover tests
```

前端：

```powershell
cd frontend
npm run build
```

API：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/dashboard
Invoke-RestMethod http://127.0.0.1:8765/api/source-health
Invoke-RestMethod http://127.0.0.1:8765/api/paper-validation/status
```

## 故障排查

### 端口被占用

查看监听进程：

```powershell
Get-NetTCPConnection -LocalPort 8765,5173 -State Listen |
  Select-Object LocalPort,OwningProcess
```

停止指定端口的旧进程：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

Get-NetTCPConnection -LocalPort 5173 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

### 页面一直显示正在连接

1. 确认 `http://127.0.0.1:8765/api/dashboard` 能返回 JSON。
2. 确认前端实际端口是 5173，而不是 Vite 自动切换后的 5174。
3. 查看 `.tmp/dashboard_server.stderr.log` 和 `.tmp/vite.stderr.log`。
4. 更新代码后重启后端，旧 uvicorn 不会自动加载新逻辑。

### 数据不更新

先看调度器：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/scheduler/status
```

若 `running=false`，手动启动。关闭电脑、结束后端或重启系统都会停止内存中的 scheduler，但不会删除数据库。

### 没有买入信号

这是允许的正常状态。常见原因包括：

- 当地日期或市场已经过期。
- 只有“天气关注”，没有满足交易 gate。
- model edge 不足。
- spread 太宽、盘口过旧或深度不足。
- 结算站、市场桶或 token 没有严格匹配。
- 策略样本不足，只允许观察。
- 达到每日额度或最大持仓数。

### 订单一买入就浮亏

买入按 ask，持仓按可卖出的 bid 估值。`ask - bid` 会立刻表现为浮亏。应同时检查 spread、深度和费用，而不是用页面 midpoint 估值。

## 安全与数据

- `.env`、`config.json`、API key、钱包私钥、`data/` 不提交 Git。
- 物理数据目录是 `D:\WeatherBot\data`，项目内 `data/` 是 junction。
- 不要同时运行 legacy `weatherbet.py` 和 v6 scheduler。
- 所有实盘按钮默认锁定；不要仅因看板出现正 edge 就手动提高金额。

## 参考资料

- [Polymarket Market Data Overview](https://docs.polymarket.com/market-data/overview)
- [Polymarket Prices and Orderbook](https://docs.polymarket.com/concepts/prices-orderbook)
- [Polymarket Orderbook API](https://docs.polymarket.com/trading/orderbook)
- [Polymarket Order Overview](https://docs.polymarket.com/trading/orders/overview)
- [Polymarket Fees](https://docs.polymarket.com/trading/fees)
- [alteregoeth-ai/weatherbot](https://github.com/alteregoeth-ai/weatherbot)
- [suislanchez/polymarket-kalshi-weather-bot](https://github.com/suislanchez/polymarket-kalshi-weather-bot)
- [yangyuan-zhen/PolyWeather](https://github.com/yangyuan-zhen/PolyWeather)

## 免责声明

本项目是研究和模拟工具，不构成投资建议，不承诺盈利。天气市场同时存在预测误差、结算源差异、盘口流动性、费用、滑点、接口故障和规则变更风险。只使用可以全部损失的资金，并在完整 paper 证据成立后再考虑极小额 canary。

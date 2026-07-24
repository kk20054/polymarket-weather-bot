# WeatherBot

面向 Polymarket 日最高温市场的本地天气量化研究与模拟交易平台。系统将机场观测、历史观测、多模型预报、市场温度桶和真实盘口放进同一条可审计链路，并提供受控模拟账户验证策略。

> 当前状态：可采集、可分析、可模拟；实盘保持锁定。历史证据尚未证明正收益，不应把“天气关注”或正概率差直接当成买入建议。

## 项目入口

本项目只维护三份默认入口，避免每轮重新阅读全部历史：

| 文件 | 职责 |
| --- | --- |
| `AGENTS.md` | Codex/开发代理规则、安全边界和标准命令 |
| `docs/CURRENT_STATE.md` | 当前 Phase、阻塞项和唯一下一步 |
| `README.md` | 安装、启动、操作、策略含义和局限 |

外部资料先查 `docs/SOURCE_REGISTER.csv`；架构、存储和详细 UI/算法规则按需读取 `docs/` 下的索引文档。Git 是唯一代码版本历史，不再复制“最新版/最终版”源码目录。

## 当前能力

- 51 个工作台城市的机场站点、时区和结算规则注册表；尚未完成采集的城市会明确标为待接入。
- METAR、中国实况、Wunderground 历史观测、Weather.com v3 与 Open-Meteo 多模型预报。
- PolyWX 风格的城市工作台：预报、METAR、历史观测、偏差统计、抓取日志。
- DEB 日最高温分布、市场温度桶严格匹配、盘口价格与概率优势计算。
- `$40` 等自定义本金的受控模拟账户、Kelly 仓位、订单生命周期、模型保护/可成交止盈、资金曲线。
- SQLite 审计链：每次预报、观测、决策、订单、估值和结算均保留来源与时间。

## 重要结论

当前活跃策略 `core_modal_v1` 只观察模型概率最高的两个温度桶，并要求：

- 模型概率至少 `25%`；
- 扣除价差缓冲后的有效优势至少 `8%`；
- 至少 4 个独立模型家族，模型最高温分歧不超过 `1.5°C`；
- 校准覆盖率至少 `80%`，且有足够独立结算日；
- 盘口、tick、最小订单、深度、新鲜度和结算规则全部有效；
- 仓位使用 `15% fractional Kelly`，单笔最多本金的 `5%`。

2026-07-18 至 2026-07-22 的无泄漏回放得到 113 个有效案例：Top-1 命中率 `27.43%`、Top-2 `45.13%`、多分类 Brier `0.6968`，可执行历史交易为 `0`。这说明系统已经能诚实拒绝低质量交易，但还没有盈利证据。

Weather.com v3 原始预报会随调度器持续保存；Wunderground 前一日 truth 更新后，历史 poller 约每日自动重训一次无泄漏校准表。2026-07-23 实测各已接入城市的 v3 配对样本已推进到 `2026-07-22`，但数量仅 `4–9` 天，尚未达到 `20` 天动态权重门槛，因此 v3 会继续积累但暂不参与成熟权重。

同日项目级模拟证据共有 `11` 笔 revision-bound v2 订单、`10` 笔权威结算，已实现 PnL 为 `-$8.43`；模型 Brier `0.043725`，市场 Brier `0.003645`。样本仍小，但结果明确说明当前模型尚未优于市场，不能据此开启实盘。

## 技术架构

```text
天气/市场采集器
  -> SQLite 原始事实与快照
  -> hourly consensus / DEB / bucket probabilities
  -> signal decisions + 风控闸门
  -> paper executor / settlement / equity curve
  -> FastAPI
  -> React + TypeScript + Tailwind + Recharts
```

主要目录：

| 路径 | 用途 |
| --- | --- |
| `weatherbot_v3/` | 生产化采集、派生、策略、执行和风控 |
| `dashboard_server.py` | FastAPI 与看板适配层 |
| `frontend/src/` | 唯一可编辑的 React 生产看板 |
| `dashboard/` | FastAPI 兼容静态页，不再新增功能 |
| `tests/` | 单元、契约和集成测试 |
| `scripts/dev.ps1` | 唯一标准启动命令 |
| `scripts/check.ps1` | 唯一标准检查命令 |
| `tools/` | 回放与只读诊断工具 |
| `data/` | 本地运行数据 Junction，实际指向 `D:\WeatherBot\data` |
| `legacy/` | 旧版，只读参考 |

## 首次安装

在 PowerShell 中：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

cd frontend
npm install
```

密钥只放项目根目录 `.env`，不要写入 README、`config.json` 或 Git：

```dotenv
LIVE_TRADING=false
WEATHER_COM_API_KEY=***
WUNDERGROUND_API_KEY=***
MINIMAX_API_KEY=***
FEISHU_WEBHOOK_URL=***
```

没有某个可选密钥时，对应来源会明确降级或禁用，不会伪造数据。

## 启动项目

标准命令：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\scripts\dev.ps1
```

该命令调用同一个桌面启动器，因此命令行和桌面快捷方式不会形成两套启动逻辑。

### 推荐：桌面一键启动

本机已经安装启动器后，双击桌面的 **WeatherBot 看板** 即可：

1. 校验端口 `8765/5173` 上是否已是本项目，避免重复启动或误开旧版本；
2. 缺少服务时隐藏启动 FastAPI 后端和 Vite 前端，并等待健康检查通过；
3. 显式启动数据调度器；
4. 用默认浏览器打开 <http://127.0.0.1:5173/>。

重复点击不会再开第二套服务，也不会让 Vite 自动漂移到 `5174`。启动失败时会弹出明确提示；日志在：

```text
D:\WeatherBot\logs\launcher.log
D:\WeatherBot\logs\backend.log
D:\WeatherBot\logs\frontend.log
```

首次安装或重新生成启动器：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
powershell -ExecutionPolicy Bypass -File .\scripts\install_weatherbot_launcher.ps1
```

启动器本体位于 `D:\WeatherBot\Launcher\WeatherBotLauncher.exe`，桌面只是快捷方式。它复用现有 `.venv`、`frontend/node_modules` 和 D 盘数据，不会把数据库或密钥打进 EXE。

### 手动启动

#### 1. 后端

打开第一个 PowerShell：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

#### 2. 前端

打开第二个 PowerShell：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：<http://127.0.0.1:5173/>

#### 3. 启动调度器

后端默认不会自动抓取。可在看板顶部点击“启动调度器”，或执行：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/scheduler/start
```

检查状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/scheduler/status
Invoke-RestMethod http://127.0.0.1:8765/api/source-health
```

停止调度器：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8765/api/scheduler/stop
```

## 日常操作

### 左侧城市栏

- 搜索城市名或 ICAO 机场代码。
- 分组下拉支持按洲、时区、字母浏览；每组有独立标题与城市数。
- 城市温度是最近可用观测，不等于该日最终结算高温。

### 中间天气工作台

1. **预报**：独立显示中国实况、PWS、METAR、历史观测、本系统预报和云量。没有真实值的系列不会补零。
2. **METAR**：机场原始报文和解析字段。它是日内实况证据，不自动等同于 Wunderground 结算值。
3. **历史观测**：Wunderground/weather.com 历史序列；香港采用 HKO 规则。缺失时诚实显示空态。
4. **偏差统计**：`观测 - 预报`。METAR 按最近整点匹配；历史观测按最近整点匹配并去重，避免同一小时重复计数。
5. **抓取日志**：只显示当前城市的最近记录，检查来源、状态、耗时和错误；天气、观测、盘口、信号按消息语义归类。

各观测表的气压统一显示为 `hPa`；温度、风速和能见度按城市显示习惯转换。

DEB 的 `μ ± σ` 表示日最高温预测中心和不确定度。概率桶是模型分布，不是收益保证。只有市场桶严格匹配且真实 ask、价差、流动性和风控全部通过时，才会成为模拟候选。

### 右侧模拟交易台

1. 展开“自动模拟设置”。
2. 输入模拟本金和单笔上限。
3. 单选一个入场策略并选择退出方式。
4. 确认顶部调度器正在运行。
5. 点击“一键模拟”。

当前建议只使用 `核心高概率桶`。旧的单桶 EV、相邻网格和低价尾部策略仍保留用于研究，但默认关闭，因为历史证据不足。每批模拟只允许一个入场策略：动态核心与低价尾部会在 `10–15¢` 区间重叠，单桶和相邻网格也可能命中同一 token；在组合级去重和风险分配尚未验证前，不允许把它们自由叠加。

模拟账户启动后会固定策略版本，避免测试期间参数漂移。策略队列展示当前城市的决策；模拟订单页展示：

- 入场时间、城市、日期、温度桶和策略；
- 买入价、份额、成本、当前买一价；
- 未实现/已实现 PnL 与状态；
- Polymarket 对应市场链接；
- 资金曲线。

没有通过全部闸门的候选时显示“暂无模拟订单”，这是有效结果，不是故障。

## 退出与结算

### 持有至结算

忽略盘中噪声，等待 Polymarket 官方结果。适合先验证概率质量。

### 模型保护退出

仅用于模拟盘：

- 已观测最高温使目标桶不可能时，尝试按真实买一价退出；
- 仅模型转弱时，需要连续两次概率低于 `8%`；
- 还要满足最短持有时间、盘口新鲜度、深度和卖价不差于模型公允价。

这不是传统固定百分比止损，避免薄盘口的短期价差把正常波动固化成损失。

### 盈利止盈 + 模型保护

仅用于新启动的模拟批次，旧批次参数保持冻结：

- 只按真实可成交的 YES `best bid` 计算，不使用中间价、最新成交价或页面估值；
- 至少持有 `15` 分钟；
- 可成交利润同时达到入场成本的 `5%`、`$0.05`，且卖价至少比入场价高一个 tick；
- 买一档深度必须覆盖全部份额，盘口时间不得超过 `300` 秒；
- 未达到止盈时，实况穿桶和模型连续失效保护仍然生效。

该模式用于检验盘中信息差是否可兑现，不代表它一定优于持有至结算。应分别比较两个模拟 cohort 的成交率、已实现 PnL、错失结算收益和最大回撤。

## 如何读“概率优势”

```text
原始优势 = 模型概率 - 当前 YES ask
有效优势 = 原始优势 - max(tick, spread / 2)
```

正数只表示模型比市场更乐观，不代表可以买。真正的候选还必须满足模型排名、校准、结算 truth、模型分歧、盘口、最小订单和 Kelly 大小等条件。

## 数据源职责

| 来源 | 角色 | 是否直接解锁实盘 |
| --- | --- | --- |
| AWC/IEM METAR | 机场实况、日内最高温下限 | 否 |
| Wunderground daily/hourly | 非香港市场历史/结算 truth 候选 | 需覆盖与核验 |
| HKO Daily Extract | 香港结算 truth | 需规则匹配 |
| Weather.com v3 | 主展示预报与 DEB 模型之一 | 否 |
| Open-Meteo ECMWF/GFS/ICON/GEM/JMA | 独立 NWP 与 DEB | 否 |
| 中国天气实况 | 中国城市短临辅助 | 否 |
| PWS | 温度走势与峰值拐点辅助 | 否 |
| Polymarket Gamma/CLOB | 市场、token、盘口、结算 | 交易必需 |

## 与参考项目的取舍

- [alteregoeth-ai/weatherbot](https://github.com/alteregoeth-ai/weatherbot)：借鉴机场站点、EV、Kelly、模拟和价差过滤；不沿用 JSON 单体状态和未经验证的概率校准。
- [suislanchez/polymarket-kalshi-weather-bot](https://github.com/suislanchez/polymarket-kalshi-weather-bot)：借鉴 31-member ensemble、8% edge、15% fractional Kelly、Brier 和三栏看板；不混入 BTC 策略。
- [PolyWeather](https://github.com/yangyuan-zhen/PolyWeather)：借鉴 settlement-oriented 观测、DEB、严格桶匹配、EMOS shadow 和事件驱动展示；不把未公开的生产阈值当作已验证事实。
- [Polymarket 官方订单文档](https://docs.polymarket.com/trading/orders/create)：实盘必须遵守限价、tick、minimum size、余额、订单状态和重复订单约束。

## 当前局限

- 回放只有少量独立结算日，城市级命中率波动很大。
- 当前回放没有历史可执行订单，无法计算真实 ROI 或证明 edge。
- 当前项目级模拟证据已实现 `-$8.43`，且模型 Brier 暂时弱于市场；这是继续校准和对照实验的依据，不是盈利验证。
- 多数候选被模型分歧、最小订单和盘口价差阻塞。
- Weather.com v3 校准已自动日更，但各城市目前只有 `4–9` 个无泄漏配对样本，未满 `20` 天前权重仍为零。
- PWS 取决于独立 API entitlement；无权限时保持禁用。
- 历史买一价只能近似持仓退出价值，薄盘口会造成明显浮亏。

因此当前正确路径是继续采集和受控模拟，按城市统计 Brier、Top-2、CLV、成交率、ROI 和最大回撤，再决定是否调整策略。不要为了产生订单而放宽风控。

## 验证命令

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m unittest tests.test_v3_core
.\.venv\Scripts\python.exe -m unittest tests.test_polywx_contract

cd frontend
npm run build
```

无泄漏策略回放示例：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe tools\backtest_core_modal_strategy.py `
  --cities chicago shanghai tokyo singapore `
  --start 2026-07-18 --end 2026-07-22 `
  --output audits\core-modal-review.json
```

## 常见问题

### 8765 或 5173 端口被占用

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess
Stop-Process -Id <OwningProcess> -Force
```

只停止确认属于 WeatherBot 的 PID。Vite 若发现 5173 被占用会自动切到 5174，应优先清理旧进程，避免打开错版本。

### 看板数据不更新

依次检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/scheduler/status
Invoke-RestMethod http://127.0.0.1:8765/api/source-health
Invoke-RestMethod http://127.0.0.1:8765/api/dashboard
```

然后打开当前城市的“抓取日志”。不要只看顶部“刷新成功”，要确认对应 source 的最新时间和状态。

### 实盘

`LIVE_TRADING=false` 是默认且必须保持的状态。当前版本尚未达到实盘验收门槛，也不承诺稳定盈利。

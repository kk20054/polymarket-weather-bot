# WeatherBot 看板使用手册

这个看板使用 `suislanchez/polymarket-kalshi-weather-bot` 的 React/Tailwind 交易终端框架；天气信号仍由本项目的 `bot_v2.py` 生成。

当前版本默认是模拟盘和半自动记录：看板不会替你向 Polymarket 真实下单。

## 1. 启动看板

第一个 PowerShell：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

第二个 PowerShell：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173
```

## 2. 启动扫描器

可以在看板点“启动扫描”，也可以在第三个 PowerShell 手动运行：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe weatherbet.py
```

扫描器会持续刷新天气、盘口和本地模拟仓位。看板上的“刷新”只刷新页面数据，不等于重新扫描市场。

## 3. 术语

- `P`：模型认为 YES 会赢的概率。
- `市场P`：当前买入价近似代表的市场隐含概率，例如 24c 约等于 24%。
- `概率差`：`模型P - 市场P`，这是判断有没有优势的核心指标。
- `EV收益`：`模型P / 买入价 - 1`，低价合约会把这个数字放大，因此首页主要展示更直观的“模型概率差”。
- `WX`：weather，天气信号。
- `ensemble 成员`：同一模型运行中的多个可能天气轨迹，用于生成概率分布；详细成员信息放在分析页，不作为首页操作指标。

## 4. 模拟流程

1. 在“模拟账户”输入本金，例如 `40`，点“应用”。
2. 勾选“同时清除标记”可以开始一轮全新的模拟。
3. 等扫描器产生信号后，在右侧“信号”里查看 `模型P / 市场P / 概率差 / EV收益`。
4. 点“一键模拟”会启动后台自动模拟。启动后每 5 分钟检查一次新信号，并持续运行，直到点击“停止自动模拟”。
5. 自动模拟状态保存在本地；浏览器关闭后仍会运行，后端服务重启后也会恢复。
6. 自动模拟和单条“模拟买入”都只写入模拟账户，不会真实下单。
7. 点外链可以打开 Polymarket 页面人工核对。
8. “模拟 / 实盘”用于切换操作模式；实盘未连接或策略门槛未通过时，实盘按钮会保持锁定。
9. 市场结算后点“检查结算”，看板会尝试从 Polymarket 读取结果并更新胜率、结算率和 PnL。

## 5. 关键配置

配置文件：

```text
C:\Users\Administrator\Documents\polymarket\weatherbot\config.json
```

常用字段：

- `balance`：模拟本金。
- `max_bet`：单笔模拟最大下注金额。
- `min_ev`：最低 EV 收益率。
- `min_prob_edge`：最低概率差，默认 `0.08` 表示模型概率至少比价格高 8 个百分点。
- `min_model_prob`：最低模型胜率，避免买入模型概率太低的尾部票。
- `max_price`：最高买入价。
- `min_volume`：最低市场成交量。
- `max_slippage`：最大 bid/ask 价差。
- `use_gfs_ensemble`：是否优先用 GFS ensemble 概率。
- `ensemble_min_members`：ensemble 至少需要多少成员才启用。
- `scan_interval`：扫描间隔秒数。

## 6. 复盘判断

先至少模拟 3 到 7 天，不建议现在直接接实盘。重点看：

- 结算率是否足够高，否则胜率没有意义。
- `概率差` 高的信号是否真的更准。
- 不同城市、不同来源、不同 EV 桶的结果是否稳定。
- 是否经常差一个温度档，如果经常差一档，说明模型概率仍过度自信。

当前样本还少，而且历史样本混有旧版单点概率信号；先用看板观察，不要只按 EV 自动买。

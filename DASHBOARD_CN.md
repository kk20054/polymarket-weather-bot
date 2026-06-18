# WeatherBot 看板使用手册

这个看板使用 `suislanchez/polymarket-kalshi-weather-bot` 的 React/Tailwind 交易终端框架；天气信号仍然来自当前 `alteregoeth-ai/weatherbot` 的 `bot_v2.py` 逻辑。

当前版本默认是半自动和模拟记录：看板不会替你向 Polymarket 下单。

## 1. 启动看板

在项目目录打开第一个 PowerShell：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

再打开第二个 PowerShell：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173
```

## 2. 启动 WeatherBot 扫描

第三个 PowerShell 运行：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
.\.venv\Scripts\python.exe weatherbet.py
```

看板右上角“刷新”只是刷新本地看板数据，不会启动扫描器。真正持续扫描的是这个 `weatherbet.py` 窗口。

## 3. 手动模拟一笔信号

在右侧“信号”表里：

1. 找到你想跟踪的天气信号。
2. 看 `EV`、`限价`、`金额`。金额输入框默认填机器人建议金额，你可以改成自己的模拟金额，例如 `1.00`、`2.00`、`5.00`。
3. 点外链按钮打开 Polymarket，只用于核对市场页面。
4. 点绿色勾号，表示“模拟买入”。这只写入本地数据库，不会真钱下单。
5. 点 `$`，表示你已经在 Polymarket 手动实盘买入，用于本地标记。
6. 点 `X`，表示跳过这个信号。

点“模拟买入”或“实盘标记”后，右下角“模拟/交易记录”会出现一条待定记录。

## 4. 本地数据保存在哪里

SQLite 数据库：

```text
C:\Users\Administrator\Documents\polymarket\weatherbot\data\weatherbot.db
```

里面会保存：

- 信号题目、Polymarket URL、YES token
- 限价、买入价差、建议金额、模拟金额
- 信号状态：`signal`、`simulated`、`bought`、`skipped`
- 看板系统日志

## 5. 建议你先怎么跑几天

先不要再实盘扩大仓位。建议连续 3 到 7 天只做模拟：

1. 每天让 `weatherbet.py` 持续跑。
2. 每次出现信号，在看板填模拟金额并点绿色勾号。
3. 第二天 Polymarket 结算后，把结果对照记录下来。
4. 重点看是否经常差 1 个温度桶、是否某些城市连续错、D+0 和 D+1 哪个更可靠。

昨天全亏说明当前信号不能直接按 EV 当成确定优势。后面要优化实盘策略，优先方向应该是：降低单笔金额、提高 `min_ev`、限制只做模型一致性更高的城市/日期、记录真实结算结果后再校准。

## 6. 安全提醒

这个版本不会自动下单。真正下单仍然需要你打开 Polymarket 页面，人工确认市场、方向、价格和金额后提交。

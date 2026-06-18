# WeatherBot Dashboard

这个 dashboard 使用 `suislanchez/polymarket-kalshi-weather-bot` 的 React/Tailwind 三栏交易终端框架：

- 顶部状态栏
- 左侧 equity / terminal
- 中间 globe / weather / EV distribution
- 右侧 signals / trades

信号来源仍然是当前 `alteregoeth-ai/weatherbot` 的 `bot_v2.py` 逻辑。

当前实现不会改变 `bot_v2.py` 的信号策略，也不会真实下单。真实交易仍然需要你打开 Polymarket 页面手动确认。

## 启动

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动 dashboard API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

另开一个 PowerShell，启动 React 前端：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

打开：

```text
http://127.0.0.1:5173
```

## 数据库

SQLite 数据库位置：

```text
data/weatherbot.db
```

保存内容：

- bot 产生的手动下单信号
- Polymarket URL
- YES token id
- 建议限价、金额、份额
- EV、Kelly、预测来源
- 人工状态标记：signal / bought / skipped

## 注意

Dashboard 的 `Start` / `Scan` 按钮只会记录提示，不会真正启动后台扫描。实际扫描仍然用：

```powershell
.\.venv\Scripts\python.exe weatherbet.py
```

真实下单仍然需要你点击信号里的 Open 打开 Polymarket 页面，人工核对后手动下单。

## 本地数据与 GitHub

`config.json`、`data/`、`.venv/` 都不会提交到 GitHub。请用 `config.example.json` 作为配置模板，然后在本地创建自己的 `config.json`。

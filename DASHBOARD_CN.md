# WeatherBot Dashboard

这个 dashboard 借鉴了 `suislanchez/polymarket-kalshi-weather-bot` 的思路：用本地 API、数据库和页面集中查看信号、模拟仓位与手动下单卡片。

当前实现不会改变 `bot_v2.py` 的信号策略，也不会真实下单。真实交易仍然需要你打开 Polymarket 页面手动确认。

## 启动

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动 dashboard：

```powershell
.\.venv\Scripts\python.exe -m uvicorn dashboard_server:app --host 127.0.0.1 --port 8765
```

打开：

```text
http://127.0.0.1:8765
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

## 本地数据与 GitHub

`config.json`、`data/`、`.venv/` 都不会提交到 GitHub。请用 `config.example.json` 作为配置模板，然后在本地创建自己的 `config.json`。

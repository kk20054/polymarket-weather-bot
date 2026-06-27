# 历史 Forecast Archive 导入说明

这个入口用于补齐 Phase 2 需要的无泄漏历史预测样本。它导入的是“当时真实可见的 forecast run/member”，不是事后拼接出来的历史天气。

## 运行方式

先生成待补 archive 清单：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli forecast-archive-manifest --output-path data\forecast_archive\historical_forecasts.template.jsonl
```

这个文件是模板，不是可直接导入的真实数据。需要用真实 ECMWF/GFS/HRRR 历史模型运行结果补齐 `run_at`、`valid_at`、`lead_hours`、`model_version` 和 `members` 后，再作为正式 archive 导入。

先 dry-run：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli forecast-archive-import --archive-path data\forecast_archive\historical_forecasts.jsonl
```

确认 `valid / skipped / errors` 后再写入 SQLite：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli forecast-archive-import --archive-path data\forecast_archive\historical_forecasts.jsonl --apply
```

导入后重新看 Phase 2 样本缺口：

```powershell
.\.venv\Scripts\python.exe -m weatherbot_v3.cli model-dataset-audit --limit 50
```

## 文件格式

支持 `.jsonl`、JSON 数组，或 `{"runs": [...]}`。

最小样例：

```json
{
  "city": "nyc",
  "target_date": "2026-06-23",
  "source": "ecmwf",
  "provider": "ecmwf_archive",
  "model": "ecmwf_ifs",
  "model_version": "archive-2026-06",
  "run_at": "2026-06-22T12:00:00+00:00",
  "retrieved_at": "2026-06-22T12:10:00+00:00",
  "valid_at": "2026-06-23T18:00:00+00:00",
  "lead_hours": 30,
  "members": [
    {"member_id": "m01", "high_temp": 80.0},
    {"member_id": "m02", "high_temp": 82.0}
  ]
}
```

必须字段：

- `city`
- `target_date`
- `source`
- `model`
- `model_version`
- `run_at`
- `valid_at`
- `members[].member_id`
- `members[].high_temp`，或可从 `members[].hourly` 计算出的目标当地日最高温

## 无泄漏规则

- D+1 / D+2：`run_at` 必须早于目标城市本地结算日开始。
- D+0：`run_at` 必须早于目标城市本地结算日结束。
- `valid_at` 必须落在目标城市本地结算日内。
- `city` 必须存在于本地机场结算 registry。
- 如果提供 `station_id`，必须匹配 registry。例如 Dallas 使用 `KDAL`，不能用 `KDFW`。
- 如果提供 `unit`，必须匹配 registry。美国城市多为 `F`，欧洲/亚洲等多为 `C`。
- 缺 `members` 的记录不导入，因为 Phase 2 需要成员级分布，而不是只要均值。

如果记录来自 Open-Meteo historical/continuous 产品，系统会加 `historical_continuous_product_review_required` 标记；这种数据不能直接当成高置信生产训练样本。

## 生产建议

优先导入真实 archive：

- ECMWF 历史 run
- GFS/GEFS 历史 run
- HRRR 或短临模型历史 run

每条记录都应能回答三个问题：

1. 这次模型运行在当时是否已经可见？
2. 它对应哪个机场站点和目标当地日期？
3. 成员级日最高温是如何由小时路径算出来的？

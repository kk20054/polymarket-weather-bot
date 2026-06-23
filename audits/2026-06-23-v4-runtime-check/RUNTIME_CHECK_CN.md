# WeatherBot v4 运行验收记录

## 已验证

- 后端已重启并加载当前代码：`http://127.0.0.1:8765/api/dashboard` 返回新字段。
- Dashboard payload：
  - `weather_signals`: 16
  - `weather_city_series`: 20
  - 首个城市 `history_count`: 1
  - 首个城市 `forecast_count`: 80
- 温度拟合接口：
  - `records`: 196
  - `markets`: 18
  - `eligible_markets`: 1
  - `tier_counts`: `research_truth=166`, `live_truth=30`
- 历史天气补全接口已跑通：
  - `POST /api/weather/backfill-history`
  - 测试参数：`days=1`, `cities=["nyc"]`
  - 返回：`fetched=1`, `errors=0`

## 截图验收限制

- Playwright 库可用，但默认 Chromium 浏览器二进制未安装。
- 改用本机 Chrome 时被系统权限拦截：`spawn EPERM`。
- 当前会话没有暴露 in-app Browser/Chrome 控制工具，因此本轮无法生成可靠页面截图。

## 当前结论

- “温度拟合为空”的数据层问题已解决，接口有记录且区分研究级/实盘级样本。
- “看不到城市历史/预测天气曲线”的数据层问题已解决，接口返回每城历史点和预测点。
- 前端已通过 TypeScript/Vite 构建，说明组件字段和接口类型对齐。
- 仍建议在可控浏览器工具可用时补一次视觉截图 QA。


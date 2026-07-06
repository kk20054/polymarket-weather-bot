# WeatherBot 当前实现逻辑

## L0 参考语料

PolyWX 只作为视觉和数据密度基准，不作为运行时数据源。Firecrawl / benchmark 产物只能进入 `audits/`，不能写入交易表。

## L1 城市与结算规则

城市信息来自 `weatherbot_v3/stations.py` 与 DB `stations` 表。实盘 gate 只认可已核验 settlement rule 的城市。HK/Seoul 等特殊状态只影响 paper/live gate，不改变观测采集。

## L2 观测层

主要表：

- `metar_reports`: 机场 METAR/IEM/AWC 观测。
- `mesonet_observations`: China Live、PWS、Open-Meteo historical 等 display-only 或补充观测。

关键代码：

- `weatherbot_v3/metar.py`
- `weatherbot_v3/china_weather.py`
- `weatherbot_v3/pws.py`
- `weatherbot_v3/hourly.py`

当前规则：

- scheduler 的 METAR 增量窗口为最近 24 小时。
- 同小时观测取最新报文，不取最高温。
- 非 METAR 观测不能冒充 settlement truth。

## L3 预报层

主要表：

- `forecast_runs`
- `forecast_members`

关键代码：

- `weatherbot_v3/openmeteo.py`
- `weatherbot_v3/forecast_archive.py`

Open-Meteo 多模型是一手 forecast 输入；PolyWX forecast 只能用于人工对照或 UI fallback，不应训练交易 edge。

## L4 派生层

主要表：

- `hourly_consensus`
- `daily_max_predictions`

关键代码：

- `weatherbot_v3/hourly.py`
- `weatherbot_v3/deb.py`
- `weatherbot_v3/forecasts/ensemble.py`

DEB 当前规则：

- local-day hourly forecast/member max 生成日最高温分布。
- ensemble 优先，fallback 为 point forecast + sigma。
- μ 必须不低于 `observed_max_so_far - 0.5°C`。
- peak hour 用混合曲线：过去小时用观测，未来小时用 forecast，并列取最晚。

## L5 市场桶

主要表：

- `market_buckets`
- `polymarket_events`
- `polymarket_markets`
- `polymarket_orderbook`

关键代码：

- `weatherbot_v3/markets.py`
- `weatherbot_v3/polymarket_gamma.py`

桶边界必须从 Gamma/market slug 动态解析，不能 hardcode。

## L6 策略与 gate

主要表：

- `signal_decisions`

关键代码：

- `weatherbot_v3/signals.py`
- `weatherbot_v3/strategies/`
- `weatherbot_v3/sizing.py`

当前支持 single bucket EV、ladder grid、tail buying。live gate 默认锁定，paper 也必须通过盘口/tick/orderMinSize 等检查。

## L7 Dashboard

技术栈：

- Vite + React + TypeScript
- Tailwind
- Recharts

主文件：

- `frontend/src/App.tsx`
- `frontend/src/components/WeatherPanel.tsx`

当前 UI 原则：

- 对标 PolyWX，主路径只展示 Forecast / METAR / Historical / Last refresh 四个状态。
- 推荐卡只保留 4 行：城市状态、当前到 DEB、桶与 edge、Polymarket 链接。
- 盘口、token、gate、schema notes、DEB metadata 等调试信息不得压在首屏。

## L8 执行层

主要代码：

- `weatherbot_v3/executor.py`
- `weatherbot_v3/paper.py`
- `weatherbot_v3/live.py`

当前状态：live locked。任何真实下单都必须另起 canary 验收轮。

## L9 验证层

必跑：

- `python -m unittest tests.test_v3_core`
- `python -m unittest tests.test_polywx_contract`
- `npm run build`

生产前仍缺：

- 连续 14-30 天 paper validation。
- PolyWX benchmark 对同 city/date 的 peak hour、DEB μ/σ、bucket probability 对齐。
- Wunderground / HKO / IEM truth delta 的长期统计。


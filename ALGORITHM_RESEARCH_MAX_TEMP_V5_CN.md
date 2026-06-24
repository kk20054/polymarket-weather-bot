# WeatherBot v5 最高温预测研究方案

> 本文是下一阶段算法设计，不在本轮改动中启用，也不改变当前模拟或实盘信号。

## 1. 当前问题判断

现有系统已经具备多模型、站点映射、整场 bucket 分布和盘口风控，但最高温命中率仍受四个问题限制：

1. **预测对象不够精确**：交易结算的是指定机场/站点在指定本地日的最终最高温，不是城市中心温度，也不是模型单个时次的 2 米温度。
2. **日最高温生成过于简化**：直接使用模型给出的日高温或单个最佳值，会漏掉逐小时升温轨迹、云量、风向、露点、降水和海风锋等条件变化。
3. **校准样本重复且来源层级混杂**：同一天大量扫描快照不能当作独立样本；Open-Meteo 历史适合研究拟合，但不能代替市场规则对应的结算 truth。
4. **窄温度桶放大误差**：1-2°F 的桶对站点偏差、观测取整和预报时效非常敏感。均值预测看似准确，也可能持续买错相邻桶。

当前拟合页已经改为按城市/日期去重，并选取最接近赛前 24 小时的预测作为独立天气日。原始快照仍保留用于分时分析。

## 2. 建议的生产级预测链路

### 2.1 先严格定义结算目标

每个市场必须生成不可变的 `SettlementTarget`：

- 机场 ICAO/WMO 站点
- 市场使用的时区和本地日期边界
- 温度单位
- 结算页面和规则原文
- 温度取整方式
- 桶边界是否包含端点
- 最终 truth provider 与 fallback 层级

美国机场优先使用 NWS/NOAA 站点产品和 NCEI Local Climatological Data；METAR 只提供逐时/特殊观测，不能默认等同最终日最高温。

### 2.2 从逐小时轨迹推导日最高温

不要只接收一个 `daily_max`。每个模型保存本地日逐小时数据：

- 2 米温度
- 露点和相对湿度
- 总云量/低云量
- 10 米风速、风向和阵风
- 降水概率与降水量
- 边界层高度
- 短波辐射或太阳辐射代理

对每个成员计算：

`member_daily_max = max(hourly_temperature within settlement local day)`

最终概率分布由所有成员的 `member_daily_max` 构成。这样可以保留峰值出现时间和非对称尾部，而不是假设一个固定高斯分布。

### 2.3 按预报时效分开建模

- **D+2 / D+1**：ECMWF ENS、GEFS/GFS、其他可用 ensemble，使用成员级日最高温。
- **D+0 早晨**：加入 HRRR/HREF 或区域短临模型，对机场站点做局地偏差修正。
- **D+0 临近结算**：使用站点 `max-so-far`，只预测剩余升温空间。

D+0 剩余最高温建议建模为：

`final_max = max(max_so_far, max_future_hourly_temperature)`

若最新观测已超过某个桶的上限，该桶立即归零；若太阳辐射时段结束、云层稳定且所有短临成员均无法触及桶下限，则应触发模型失效退出，而不是等待盘口止损。

### 2.4 校准采用分层 EMOS/MOS

参考 NOAA MOS/NBM 和 EMOS 思路，建立三层校准：

1. **全局层**：按模型、预报时效、季节学习基础误差。
2. **气候区层**：内陆、沿海、热带、高海拔等共享参数。
3. **站点层**：机场专属 bias、MAE、日变化和风向条件偏差。

站点样本少时向气候区和全局参数收缩，避免“少于 20 天就完全不能使用”，也避免用 3 个天气日拟合出极端站点修正。实盘解锁仍必须依赖足够的高置信 settlement truth。

第一版可实现滚动 MOS：

- 训练窗口：最近 60-120 个独立天气日
- 特征：模型日最高温、预报时效、月份、云量、露点、风向/风速、降水、站点
- 输出：校准后均值和残差尺度
- 模型：正则化线性回归/Huber 回归

第二版再实现 EMOS：

- 均值由多模型均值线性组合
- 方差由 ensemble spread 和历史残差共同决定
- 目标函数使用 CRPS，而非只优化 MAE

### 2.5 桶概率必须包含观测与取整不确定性

概率计算应同时模拟：

- 校准后的天气分布
- 站点观测误差
- 结算页面取整规则
- 单位转换

推荐用 Monte Carlo 直接把每个样本映射到实际市场桶，最后对同一事件所有桶归一化。尾部桶不得仅依赖高斯公式外推。

## 3. 市场决策层改进

天气准确率和交易盈利能力需要分开评估。

信号至少经过以下顺序：

1. 站点与规则确定
2. 预测分布校准通过
3. 全部互斥桶概率归一
4. 与市场价格比较
5. 扣除 spread、滑点和最小订单影响
6. 低价尾部额外门槛
7. Paper Executor 可成交检查

建议新增：

- `top_bucket_probability`
- `second_bucket_probability`
- `top2_margin`
- `market_consensus_distance`
- `forecast_run_stability`
- `max_so_far_distance_to_bucket`
- `expected_spread_cost`

当模型和市场相差很大时，不应自动把差异视为 edge。先检查站点、日期边界、单位、模型运行时间和最新观测是否错位。

## 4. 验证指标

不要只看胜率：

- 日最高温 MAE/RMSE
- Bias
- CRPS
- PIT / rank histogram
- 每个桶的 Brier score
- Reliability diagram
- Top-1 bucket accuracy
- Top-2 bucket coverage
- 按城市、站点、时效、月份、价格段分组的 paper ROI
- 扣除 spread 后的 realized edge

生产门槛应同时满足“天气概率校准”和“可执行交易 ROI”，不能只满足其中一个。

## 5. 推荐实施顺序

### Phase A：数据闭环

- 为所有城市核验站点、时区、日期边界和取整。
- 引入 NCEI/NWS/官方站点日最高温 truth。
- 保存逐小时模型成员和逐时观测。

### Phase B：独立日回放

- 按 D+2、D+1、D+0 分开生成基线。
- 对比当前算法、原始 ensemble、滚动 MOS。
- 找出每个城市的系统偏差和最优提前量。

### Phase C：概率校准

- 实现分层滚动 MOS。
- 实现成员日最高温的 empirical distribution。
- 增加取整和观测误差模拟。

### Phase D：近结算策略

- 维护 `max_so_far`。
- 学习不同月份、云量和风向条件下的剩余升温分布。
- 用模型失效条件替代短时价格止损。

### Phase E：小额验收

- 至少 30 个高置信独立结算日。
- 概率校准优于市场前的原始模型基线。
- Paper ROI 在扣 spread 后仍为正。
- 再开放 $1-$2 canary。

## 6. 参考资料

- NOAA Model Output Statistics: https://www.weather.gov/mdl/mos_getbull
- NOAA National Blend of Models: https://vlab.noaa.gov/web/mdl/nbm
- NOAA HRRR: https://rapidrefresh.noaa.gov/hrrr/
- Aviation Weather Center Data API / METAR: https://aviationweather.gov/data/api/
- NOAA NCEI Local Climatological Data: https://www.ncei.noaa.gov/products/land-based-station/local-climatological-data
- ECMWF forecast documentation: https://www.ecmwf.int/en/forecasts/documentation-and-support
- Gneiting et al., Ensemble Model Output Statistics and CRPS: https://journals.ametsoc.org/view/journals/mwre/133/5/mwr2904.1.xml


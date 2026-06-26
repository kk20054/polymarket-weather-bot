# WeatherBot v6 Phase 1 合同与 Truth 审计

审计日期：2026-06-26

## 结论

本阶段把结算规则、机场 truth 和数据资格门禁推进到可复盘状态。系统仍不允许实盘，当前阻塞是正确的：事件级结算合同还没有人工核验，各城市独立结算 truth 样本也未达到生产门槛。

## 已完成

### 事件级结算合同

- 新增 `settlement_contracts`，按 Polymarket event slug 保存一份结算合同。
- `contracts-sync` 从 `data/markets/*.json` 重建 market rules 和 settlement contracts。
- 合同记录包含城市、机场站点、时区、单位、目标本地日期、规则文本、来源 URL、provider 优先级、自动核验证据和人工核验时间。
- 自动核验只作为证据标签，不等于实盘许可；`manual_verified_at` 仍需人工确认。

当前数据：

- market files: 211
- bucket market rules: 2321
- settlement contracts: 211
- auto verified contracts: 60
- manual verified contracts: 0

### Bucket 规则主键修复

发现部分历史市场文件中，同一事件下多个温度 bucket 会复用相同 `market_id`。旧表以 `market_id` 为主键，会把多个 bucket 折叠成一条，影响 distribution、规则审计和复盘。

已修复：

- `market_rules.market_id` 作为规则主键。
- 如果同批规则出现重复 `market_id`，生成稳定 `rule:<hash>` 主键。
- 原始 Polymarket/Gamma id 保存在 `exchange_market_id`。
- `contracts-sync` 会按当前市场文件裁掉旧的派生规则，避免历史错误主键残留。

当前数据：

- synthetic rule keys: 825
- exchange id preserved: 825
- bucket rules after prune: 2321

### Truth 观测版本化

新增 `truth_observation_versions`，truth 更新采用 append-only 版本链。

已修复：

- AviationWeather epoch 秒/毫秒时间解析。
- `truth-audit` 会识别官方观测日期与目标市场日期不一致的记录，并追加无效化版本，不直接删除历史。
- `truth_observations` 只作为 latest materialized row。

当前 truth 门禁：

- eligible independent city-days: 38
- total city-days: 50
- NWS eligible: 31
- Visual Crossing eligible: 6
- AviationWeather eligible: 1
- legacy unknown excluded: 17
- truth stage: ready

### 数据资格门禁

刷新后当前门禁：

- settlement contracts: blocked
- truth: ready
- forecast runs: ready
- orderbooks: ready
- overall score: 0.75
- live allowed: false

这说明系统现在不是因为预测或盘口过期而假阻塞，而是被正确挡在“人工核验和城市级样本数”之前。

## 当前阻塞

- 211 个事件级结算合同尚未人工核验。
- 20 个城市的独立 eligible truth days 均未达到 `MIN_INDEPENDENT_SETTLEMENT_DAYS=20`。
- 非美国城市仍缺少足够官方/付费 station truth，历史 legacy truth 不允许进入生产校准。

## 验证

- `python -m weatherbot_v3.cli contracts-sync`：通过，2321 bucket rules / 211 contracts。
- `python -m weatherbot_v3.cli truth-audit`：通过，未发现新的时间错配。
- `python -m weatherbot_v3.cli forecast-backfill --days 3`：20/20 城市成功，forecast stage ready。
- `python -m weatherbot_v3.cli orderbook-backfill --limit 30`：12 个真实 CLOB depth，orderbook stage ready。
- `python -m weatherbot_v3.cli data-readiness`：overall blocked，score 0.75，仅剩 settlement contract 全局阻塞。
- `python -m unittest tests.test_v3_core`：35 tests passed。
- `npm run build`：通过。

## 下一步

1. 增加合同人工核验工作流：按 event 展开规则文本、Wunderground/官方 URL、站点、时区、单位，确认后写入 `manual_verified_at`。
2. 对非美国城市接入更稳定的 station truth 历史源，优先 Visual Crossing station mode 或同等 paid official archive。
3. 建立训练集 manifest，把 forecast run、truth version、market event、orderbook snapshot 锁定到不可变版本。
4. 在模型训练前，禁止使用低置信 legacy/open-meteo truth 作为 production calibration truth。

# WeatherBot v6 Phase 1 预测与盘口数据审计

审计日期：2026-06-25

## 本批目标

建立可追溯、无事后泄漏标记的预测运行档案，并让模拟成交使用真实 CLOB 多档盘口。

## 已完成

### 预测运行档案

- 20/20 城市完成真实预测抓取。
- 保存 ECMWF、GFS ensemble、GFS seamless short range 和 METAR。
- 原 `HRRR` 字段实际请求 `gfs_seamless`，新数据层已明确标记为 `gfs_seamless_short_range`；旧字段仅保留兼容。
- 每次运行保存 provider、model、版本、抓取时间、有效时间、lead hours、机场、时区、单位、来源 URL、许可和原始响应 SHA-256。
- GFS ensemble 保存成员级当地日最高温，以及温度、湿度、露点、云量、风、降水和短波辐射逐小时路径。
- 使用内容 hash 去重；重复响应不会产生相同 run key。
- 加入 `training_eligible` 与 `ineligibility_reason`，目标当地日结束后取得的数据禁止进入训练。
- Windows 已安装 `tzdata 2026.2`；扫描目标日期改为机场当地日期，不再按 UTC 日期生成 D+0。

当前真实数据：

- forecast runs：362
- forecast members：5,222
- 新门禁后可训练 runs：178
- fresh city coverage：20/20
- forecast data gate：ready

### 真实 CLOB 盘口

- 接入 `GET https://clob.polymarket.com/book?token_id=...`。
- 保存 bids、asks、交易所时间戳、book hash、tick size、min order size 和深度。
- best bid 使用最高买价，best ask 使用最低卖价。
- 盘口新鲜度按交易所时间戳判断，不按本地写库时间伪装。
- Gamma 只允许作为 fallback 元数据；模拟和实盘订单必须取得真实 CLOB book。
- 模拟成交按限价以内的 ask 深度逐档计算。
- 部分成交只扣实际成交金额，剩余金额留在现金账户。
- scanner 最终信号会在接受前重新抓取真实 CLOB，并重新检查 spread、价格、最小份额、盘口年龄与可成交深度。
- 后端持续自动模拟开启时，每 5 分钟先刷新当前信号的 CLOB 深度，再进行买入/跳过判断。

当前真实数据：

- orderbook snapshots：141
- fresh CLOB snapshots：11
- orderbook data gate：ready

## 当前仍阻塞实盘

- 1,621 条结算规则尚未人工核验。
- 70 条历史规则时区仍与城市注册表不一致。
- 高置信独立 truth 只有 2 天，最低门槛为 20 天。
- 17 条旧 truth 来源未知。
- 策略允许组历史样本、ROI、胜率仍未达标。

## 验证

- Python tests：29 项通过。
- TypeScript/Vite production build：通过。
- 真实 forecast backfill：20/20 城市成功。
- 真实 orderbook backfill：近期 20 个信号中 11 个取得 CLOB，9 个已关闭/不可用市场降级为 Gamma fallback，未被当作可执行盘口。
- 浏览器看板：预测运行档案和盘口快照均显示“可用”，实盘仍保持锁定。

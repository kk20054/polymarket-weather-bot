# WeatherBot v6 Phase 0 数据基座审计

审计日期：2026-06-25

## 结论

当前系统继续保持模拟盘，实盘必须阻塞。代码已经具备机场站点映射、官方 truth provider、盘口快照和模拟执行器，但历史数据尚未形成生产级证据链。

## 当前基线

- 城市/机场注册表：20 个城市，统一站点、时区、单位和坐标。
- 市场规则：1,621 条。
- 高置信独立结算日：2 天。
- 旧版未知 truth：17 条。
- 版本化 forecast runs：0。
- forecast members：0。
- 盘口快照：120 条，但当前均已过期。
- 旧版代码回滚标签：`v2-legacy-before-prod`。
- 当前策略基线：`legacy_champion`，只用于对照，不具备实盘资格。

## Phase 0 已落实

1. 新增统一城市/机场结算注册表 `weatherbot_v3/registry.py`。
2. 扩展 `market_rules`，加入合同 ID、目标本地日期、边界、舍入规则、provider 优先级、规则版本、注册表版本、解析与人工核验时间。
3. 新增 `data_qualification_audits`，保存版本化数据资格审计。
4. 新增 `/api/data-readiness`，输出结算合同、truth、预测档案、盘口四类硬门禁。
5. 数据资格未通过时强制加入实盘阻塞原因。
6. 看板新增紧凑“数据资格”模块；详细说明通过标签与悬停查看。

## 当前硬阻塞

- 所有历史市场规则尚未人工核验。
- 历史规则仍有时区不一致记录。
- 高置信独立结算日距离最低 20 天门槛仍差 18 天。
- 旧版 truth 来源不明，不能用于生产校准。
- scanner 尚未把每次模型运行和 ensemble member 写入 v3 forecast store。
- 当前盘口快照已过期。

## 下一批实施顺序

1. scanner 写入版本化 forecast runs、成员级数据和原始响应 hash。
2. 规则核验工作流：按事件而非按 bucket 批量确认站点、日期、单位、结算 URL。
3. 官方机场 truth 历史回填，研究数据与实盘资格数据分层。
4. 建立训练数据 manifest，并冻结 `legacy_champion` 对照结果。


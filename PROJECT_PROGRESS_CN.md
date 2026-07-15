# WeatherBot 项目进度台账

### 2026-07-15：Layer 3/7 逐小时预报修订审计与 PolyWX 弹窗
- Layer：只扩展 Layer 3 已持久化 Weather.com v3 快照的只读审计能力及其直接 Layer 7 展示消费者；没有改模型概率、策略、模拟执行或实盘路径。
- 改动：新增逐小时 forecast revision history 计算与 `/api/forecast-history` 只读接口，按站点本地 valid hour 汇总全部已保存快照；同时区分 `snapshot_count`、整数显示口径下的真实 `revision_count` 与 `distinct_count`，相同值快照折叠。预报明细仅在真实修订数大于零时显示历史入口，无修订小时显示 `--`；新增按需加载的 PolyWX 风格弹窗，展示 UTC/本地抓取时间、温度和相对前值，并支持关闭按钮与 Escape。
- 验证：生产库 Shanghai 2026-07-14 15:00 为 12 次快照、1 次有效修订、2 个整数温度值；保留行是 34°C 与 35°C，未把 `34.0 -> 34.444` 的精度/解析器变化误算成业务修订。`python -m unittest tests.test_polywx_contract tests.test_v3_core` 243/243 通过，`npm run build` 通过，`git diff --check` 通过。内置浏览器在 1280x720 验证弹窗、关闭/Escape、零修订空态、console error/warn=0 和页面无横向溢出；设计证据见 `design-qa.md` 与本地忽略目录 `audits/revision-dialog-2026-07-15/`。
- 结论：PolyWX 的“变更”会反映到该小时的最新预报值；WeatherBot 现在既在计算链使用每小时最新快照，也能审计具体修订。但本地只从持续采集开始拥有 12 个快照，不能伪造 PolyWX 的 150 个历史快照。三名定向审计代理同时确认：forecast revision 数据链已闭合；实盘仍结构性锁定；架构没有新增 P0 数据损坏。
- 阻塞/下一步：审计发现 CLI/core paper executor 对空 cohort 的约束仍弱于 dashboard 路径，这是启动 14-30 天 cohort 前的首要安全修复；`.env` 的 UTF-8 BOM 还会导致 `WUNDERGROUND_API_KEY` 识别失败。其后才做一次受控新鲜 METAR/模型/盘口刷新并启动 revision-bound paper cohort。scheduler、auto simulation、paper validation 均保持关闭，`LIVE_TRADING=false`。
- 相关提交：本条所在提交。

### 2026-07-14：WU Historical、V3 预报修订与高斯桶概率口径修复
- Layer：修复 Layer 2/3/4/6 数据链及其直接 Layer 7 展示消费者；未启动 scheduler、paper cohort 或 live。
- 改动：WU Historical 调度写入改为单写者并避免每城重复同步 station registry；主图、统计和历史观测表统一读取 exact-date 原生 WU series。Weather.com v3 不再把最新的晚间残缺 run 当作全天预报，而是按本地 valid hour 选择最新已知快照并用旧快照补齐已过去小时，保留 revision run ids、快照数和覆盖小时数。高斯诊断图改为摄氏 0.5°C/华氏 1°F 的 18 个固定桶，不再动态放大桶宽。`polywx_aligned_deb_v1` 的市场桶概率改用 μ/σ 高斯 CDF 积分，不再把六个模型家族均值误当作独立 ensemble members。
- 验证：Shanghai 2026-07-14 原生 WU Historical 46 行、Chicago 9 行；上海 v3 全天最高由错误的 30.56°C 修复为 35.0°C，DEB σ 由约 2.55°C 收敛到 1.624°C。上海 11 个实际市场桶概率变为连续的 `0.0/0.2/1.3/4.6/11.6/20.1/24.2/20.1/11.6/4.6/1.6%`，不再出现模型权重尖峰。`python -m unittest tests.test_v3_core tests.test_scheduler tests.test_polywx_contract` 267/267 通过，`npm run build` 通过；内置浏览器实测 Shanghai/Chicago 历史曲线、统计和高斯图，console error=0、无页面级横向溢出。
- 结论：用户观察到的两项偏差都是真问题，而非读图错误。PolyWX 的“变更”是某个有效小时的历史预报快照，主表/主图使用该小时最新值；WeatherBot 现在已在计算链复现这一“latest snapshot per local hour”口径，但尚缺详细修订弹窗。上海本地当前 `35.50±1.62°C` 与 PolyWX 截图 `35.18±1.54°C` 的剩余均值差主要来自本地 36.0°C 已观测最高温 floor，不允许模型均值低于已经发生的实况。
- 阻塞/下一步：此前未持续保存的历史 forecast snapshot 无法事后补造；当前 scheduler 停止且 orderbook/METAR 已陈旧，市场价格栏仍为空或受 gate 阻塞。下一步先做一次受控新鲜数据刷新，再补只读 forecast revision 明细；14-30 天 cohort 与 live 均不在本轮开启。
- 相关提交：本条所在提交。

### 2026-07-14：Layer 8 模拟订单强制绑定 cohort 与内置浏览器验收
- Layer：仅修复 Layer 8 paper executor、paper validation 及其直接 Layer 7 工作台/设置消费者；未启动 scheduler、未创建 paper cohort、未触碰 live 下单。
- 改动：手动单笔、批量与自动 tick 统一走 active cohort 的 run/revision/decision-batch 约束，共享同一现金、日额度、持仓与策略 cap 台账；执行前重新读取本地最新盘口，以新 ask 重算 edge 与 Kelly，并重新检查盘口年龄、spread、尾部价格和零 Kelly。缺 cohort 的非 dry-run 请求返回 409，错误 run 被拒绝。设置抽屉同步修正“已配置/已连接”、实盘执行器就绪状态、校准日和每轮候选上限等中文语义；工作台移除可绕过 Kelly 的手填金额，并在模拟账户未启动时禁用检查/买入。
- 验证：`python -m unittest tests.test_v3_core tests.test_polywx_contract tests.test_paper_validation tests.test_execution_workbench_contract tests.test_project_verification` 共 264 项通过；`npm run build`、`python -m py_compile` 与 `git diff --check` 通过。内置浏览器实际打开 Chicago 页面，逐项验证设置抽屉、API 星号状态、策略页、候选展开、Polymarket 链接与账户停用状态；console error/warn=0，1280x720 无页面级横向溢出。
- 结论：右侧模拟交易台不再存在“单笔/批量按钮绕过 cohort 风控”的 P0 路径，用户输入的模拟本金只能通过 Kelly 和统一风控分配；实盘仍结构性锁定。本轮浏览器同时确认近期日期的 `历史观测` 仍为空，这是独立的 Layer 2/3 WU 日期/查询链阻塞，不能用 UI 通过掩盖。
- 阻塞/下一步：先修上海与 Chicago 近期 WU Historical 的采集、持久化和 exact-date 查询，完成同日期浏览器对照后再做一次短受控刷新；只有新鲜盘口、真实历史观测和 verifier 同时通过，才可启动首轮 14-30 天 revision-bound paper cohort。
- 相关提交：`180ac6f`。

### 2026-07-14：14 城受控刷新、执行安全与 Layer 7 诚实展示
- Layer：补强 Layer 2-6 的当前批次数据链，并修复 Layer 7/8 的 fail-closed 边界；未启动 scheduler、paper cohort 或 live。
- 改动：对 14 个 enabled 城市显式刷新 METAR、Open-Meteo、Weather.com v3、Gamma/CLOB，并重建 D+0/D+1 hourly consensus、DEB 与 signal decisions。修复生产库 `forecast_runs(snapshot_key)` 部分唯一索引无法满足 `ON CONFLICT` 的问题；China Live 改为单城市失败隔离。Paper executor 不再用 decision issued_at 冒充盘口时间，并拒绝缺失/未来盘口；legacy live executor 即使误配 `LIVE_TRADING=true` 也被架构常量锁死。核验器支持 CLOB 毫秒时间戳，并不再把已跳过的 ask=1 终局盘口误判为 paper 决策违规。前端取消跨日期/跨城市数据回退、WU 缺失时不再把 fallback 画成历史观测、失效盘口不再显示 edge 或信号高亮。
- 验证：D+0 与 D+1 各找到 14/14 事件、154/154 严格匹配桶和 154/154 盘口；生成 28 个 DEB 与最新构建 341 条决策，`live_allowed=0`。生产迁移已应用且无数据删除。全套 `python -m unittest discover tests` 为 341/341 通过；`npm run build`、`git diff --check` 通过。刷新结束后因 scheduler 保持关闭，METAR/盘口按预期转为 stale，project verifier 仍返回 `code_only`；paper validation inactive、`LIVE_TRADING=false`。
- 结论：当前批次证明 14 城数据链可以完整重建，并关闭了生产库写入、模拟盘口时间、误开 live 和 Layer 7 误导展示四类高风险缺陷；它不等于连续运行稳定或策略盈利证明。上海 China Live 本轮上游 HTTP 502 已被隔离并诚实记录，香港成功。
- 阻塞/下一步：先提交本轮代码，再启动后端/Vite 做 Shanghai/Chicago 双主题与窄屏人工验收；验收后紧邻一次短周期受控刷新重跑 observation/paper verifier。只有新鲜可执行盘口通过后才启动 14-30 天 revision-bound paper cohort；live 继续锁定。
- 相关提交：本条所在提交。

### 2026-07-14：Layer 3/4 预测可用时间契约与历史隔离

- Layer：Layer 3 forecast snapshot、Layer 4 DEB/bias/hourly 及其只读 verifier 直接消费者；未修改前端功能、paper 执行或 live 路径。
- 改动：新增统一 `forecast_time` 契约。在线源只以实际 `retrieved_at` 作为可用时刻，受信任归档才可用模型 `run_at`；有效 lead 同时检查持久化值和 `valid_at-available_at`。Open-Meteo、Weather.com、archive、ensemble、DEB、bias、hourly 和 model-dataset 全部复用同一 fail-closed 校验。snapshot key 包含精确可用时刻与内容哈希，同一小时的不同抓取不再互相覆盖；DEB `issued_at` 不再向下取整。
- 数据迁移：版本化迁移 `20260714_01_forecast_availability_contract` 保留全部 44,523 条 forecast，隔离 5,405 条时序无效记录，并失效 1,130 条受污染 DEB；补充迁移 `20260714_02_prediction_source_contract` 又识别出 3 条引用 training-ineligible/METAR 伪 forecast 的旧 DEB。最终 1,776 条历史预测中 643 条有效、1,133 条保留但无效。迁移前后均未删除 forecast 或 prediction 原始行。
- 测试隔离：首次完整测试暴露旧发现模式会继承本机生产 DB 路径；迁移已在新增冷备前触发，但 7 月 13 日已有同尺寸备份，且本轮迁移不删历史。随后为每个测试入口增加进程级临时数据库护栏；再次完整运行后，生产库大小、修改时间及关键计数前后完全一致。新增备份为 `data/weatherbot_v3.db.bak-forecast-time-20260714`（gitignored）。
- 验证：最终完整 unittest 335/335 通过，前端 `npm run build` 通过。生产只读 verifier 当前 `prediction_math=pass`、`temporal_no_leak=pass`，14 个 enabled 城市均保留最新有效 DEB；系统仍因实时源/盘口过期、16 个高权重组件校准不足而保持 `code_only`。scheduler stopped、paper validation inactive、`LIVE_TRADING=false`。
- 结论：此前 3,285/5,405 负 lead 与 959/1,133 污染 DEB 不再能进入训练、概率、信号或证据链；历史仍可审计。下一步必须用新鲜上游重建当前批次，不能恢复旧无效预测或放宽 gate。
- 下一步：显式启动后端后做一次受控上游刷新，重建 14 城 D+0/D+1 DEB、market buckets 与激活 revision decisions，再运行 observation/paper verifier；随后处理 Layer 7 fail-open 表达和操作员浏览器验收。
- 相关提交：本条所在提交。

### 2026-07-13: Layer 7/8 开发者设置抽屉与主看板统一
- 改动：将孤立、平铺的 `/developer` 表单重构为主看板右侧设置抽屉，并保留 `/developer` 深链接。入口同时位于顶栏设置图标与模拟交易台；设置按“概览 / 策略与风控 / 版本与审计 / 系统状态”分组，继承 PolyWX 风格浅色/深色主题。
- 安全：创建参数版本与激活作用域彻底分离；新版本默认 `activate_scopes=[]`，切换信号生成或模拟默认前必须二次确认。实盘状态只读锁定，不提供密钥、live 或 webhook 开关。
- 参考：采用 ClawX 成熟设置模式中的左侧分组、右侧 Sheet、显式保存/激活分离、危险操作确认；未复制其品牌或业务结构。
- 验证：`npm run build` 通过；`tests.test_execution_workbench_contract` 3/3；`tests.test_polywx_contract tests.test_v3_core` 230/230；浏览器验证深/浅主题、390px 响应式、`/developer` 深链接、版本确认取消、console error/warn=0、无横向溢出。
- 结论：开发者参数不再挤占日常交易工作台；普通操作员保留上下文，开发者可在受控抽屉中调整草稿并审计不可变版本。scheduler stopped、paper cohort inactive、`LIVE_TRADING=false`。
- 下一步：在启动 14-30 天 paper cohort 前完成右侧交易台与设置抽屉的人工验收；不因 UI 改造放宽任何 gate。

### 2026-07-13: Layer 8 cohort Kelly、不可变策略版本与开发者模式

- 改动：新增 append-only `strategy_profile_revisions` 与激活事件；`signal-decision-v3`、`paper-validation-v2`、`paper-execution-v2` 从信号到 cohort 到订单统一钉住 revision/hash/参数快照。模拟 tick 按 cohort 当前可用资金重算 Kelly，单笔本金比例、cohort 单笔、策略、日额度和现金只走一次显式 cap 链；ladder 预留 3 个订单/持仓槽位。executor 使用最新本地 orderbook 快照并动态计算 age，不再信任信号生成时冻结的 age；毫秒 epoch 时间戳已支持。
- UI：普通右栏保留 `$40` 本金、策略组合、一键模拟、订单、结算和 Polymarket 链接，只读显示策略 revision；维护诊断从普通页隐藏。新增 `/developer` 策略实验室，可在本机确认后创建不可变 revision，并分别激活到信号生成与 paper 默认；不暴露密钥、私钥、webhook 或 live 开关。
- 验证：`tests.test_v3_core` 215/215；`tests.test_polywx_contract` 15/15；Layer 8/策略 profile 定向测试 22/22；`npm run build` 通过。浏览器普通页无系统诊断/横向溢出，开发者页可见 revision、阈值和只读系统状态，console error/warn=0。真实库已激活保守 revision 2，并重建上海 11 条 revision-2 decisions；它们均被真实 spread/bias 等 gate 阻塞，没有制造模拟订单。
- 结论：用户输入模拟本金现在真正决定 Kelly 金额；全局 `$2` 二次截断和参数不可追溯两项 P0 已关闭。策略组合仍只定义入场，退出继续限定 `hold_to_settlement`；信息差退出在 SELL fill 与历史盘口回放完成前保持禁用。scheduler stopped、paper cohort inactive、`LIVE_TRADING=false`。
- 下一步：按 revision 2 重建 14 个 enabled 城市的最新决策并汇总 blocked reason；经操作员 UI 验收后启动首轮 14-30 天 paper cohort，不放宽 gate，不开启 live。

### 2026-07-12: 调度器冷启动资源回归修复

- 改动：为所有重型 poller 增加全局单槽限流和每次启动错峰；collector 批次不再逐城市执行全库 readiness；保留结果改为有界摘要；长任务错过周期后合并 tick，不再每秒追赶。`build_data_readiness()` 改为只查询资格审计所需列，订单簿深度在 SQL 中计算，不再把 raw/orderbook JSON 装入 Python；readiness 历史限制为最近 200 条，derive 热路径改为显式审计模式。
- 验证：修复前实机 RSS 在约 80MB 至 2.6GB 间波动；修复后真实跑完 METAR 14/14、China Live 2/2、Weather.com Forecast 14/14，19 个样本 RSS 为 80.6-87.3MB，scheduler status 最大延迟 59ms，随后已停止。真实 5.6GB 数据库 readiness 耗时约 10.5s、峰值 109.6MB，审计历史由 11,594 条收敛到 200 条。
- 测试：`tests.test_v3_core` 215/215；`tests.test_polywx_contract + tests.test_scheduler` 41/41；`git diff --check` 通过。后端已重启加载新代码，scheduler stopped、paper validation inactive、`LIVE_TRADING=false`。
- 结论：此前“启动调度器后后端失联”的主回归已关闭，自动模拟所依赖的数据采集不再因 readiness 全库装载立即耗尽内存。剩余风险是 `asyncio.to_thread` 外层超时不能杀死底层线程，后续需审计 collector 自身 HTTP timeout 和残留 worker 报告。
- 下一步：修复 Layer 8 模拟本金与 Kelly 的口径闭环，消除 cohort `max_per_trade` 与全局 `MAX_BET` 双重上限，并把策略阈值固化为不可变 strategy-profile revision；之后才启动第一轮 14-30 天模拟 cohort。
- 相关提交：`1d63438`。

### 2026-07-12: Layer 1/7 城市目录扩容与推荐契约拆分

- 目标：在不扩大调度器配额、不放宽 paper/live 闸门的前提下，对齐 PolyWX 城市观察目录，并把公开的“推荐关注”卡片与 WeatherBot 的交易候选彻底分开。
- 取证：Firecrawl 渲染 `https://www.polywx.xyz/?city=singapore-wsss&date=2026-07-12`，页面列出 50 个 city/station；公开推荐卡只暴露城市、日期、当前温度和预计最高温，没有 edge、买卖方向、合约或选择理由。渲染 scrape id `019f5546-c5ef-70ec-ae2b-3205ed24a878`，interaction session `019f5547-5f74-70dd-ac9c-848d1cecc4ae`；直接访问 recommendations/cities API 均为 HTTP 403（ids `019f5548-a9a2-73f9-bc9a-546c4ad9842b`、`019f5548-b83c-7360-93f4-eb4a687034c1`）。完整记录在本地 `audits/polywx-city-recommendation-2026-07-12/`，不提交。
- 改动：注册表补 25 个 PolyWX 城市，结合原 26 城形成 51 城目录；新增 `stations.display_enabled` 与 `city_scope`，保留 `stations.enabled` 专门控制 collector。新增城市全部 `observation_only`、display=true、enabled=false。左栏显示完整目录并诚实标记“待接入/未采集”。推荐 API 升级为 `recommendations-v2-separated-contracts`：`focus_items` 只用新鲜观测与 D+0 DEB 生成 near-peak 天气关注，`items` 继续保留 Layer 6 trade candidates；前端顶部只消费 focus cards，右侧交易台仍消费策略决策。
- 真实结果：数据库 51 城全部可显示、collector-enabled 仍为 14；Manila 为 RPLL/display=true/enabled=false/observation_only。当前 focus set 为 Beijing、Singapore、Shanghai、Tokyo；Singapore 为 `29.0C -> 29.9C`，与本轮 PolyWX 可见推荐城市重合。浏览器验证 51 城、搜索 Guangzhou、Manila 空态、Singapore focus tooltip，无 console error；独立 QA 后端 scheduler=false。
- 验证：临时 SQLite 下 `python -m unittest tests.test_v3_core` 通过 214 tests；定向 PolyWX/watchlist/focus tests 通过 19 tests；`npm run build` 通过；`git diff --check` 仅既有 CRLF warning。真实 5.4GB DB 上全套测试受运行中调度器锁/I/O 影响在 5 分钟超时，因此改用隔离 DB 完成确定性验证。
- 结论/阻塞：城市目录与采集 watchlist 已解耦，新增城市不会自动抓取或获得交易资格。PolyWX 私有推荐策略仍不可从公开字段证明，当前只复现公开关注卡片的产品层；下一步应对 Manila/Guangzhou 做显式数据源与 Gamma 结算准入，再决定是否启用 collector。
- 相关提交：`ac968a2`。

### 2026-07-11: PolyWX leakage-safe replay and observed-floor source contract

- 目标：用本地保存的 PolyWX Chicago 2026-07-02/07-04 与 Shanghai 2026-07-06 证据重建 Forecast/Cloud/DEB/peak 对标，同时禁止目标日及之后 truth、晚于 capture 的 forecast snapshot 污染历史 replay。
- 改动：Bias trainer 新增 `as_of_date_exclusive` 与非持久化模式；ensemble 按 `issued_at` 过滤 forecast run；DEB 支持显式 walk-forward bias。修复 P0：`observed_floor` 不再读取 `hourly_consensus` 或 display-only mesonet fallback，只接受截至 issued_at 的 METAR，香港额外允许与结算机构一致的 HKO current；PWS/历史格点/上海城市级 China Live 仍为趋势或展示证据。
- 对标结果：Chicago 07-02 Forecast MAE 1.88F、Cloud MAE 22.53pp、DEB mu 差 0.13F、peak 17:00 vs 16:00；Chicago 07-04 为 1.61F、20.67pp、3.09F、14:00 vs 12:00；Shanghai 07-06 为 2.11C、46.36pp、1.37C。PolyWX US API 的 `temperature_c` 实际存 F、`pressure_hpa` 实际呈 inHg 尺度，工具现按城市单位契约读取，不再相信误导字段名。
- 验证：`python -m unittest tests.test_v3_core tests.test_ensemble_vs_market` 通过 206 tests；benchmark 输出在 `audits/polywx-replay-2026-07-11/`；`git diff --check` 通过。调度器、live 与 auto simulation 均未启动。
- 结论：Cloud 大差异是数据源差异；Chicago 07-04 与 Shanghai DEB 仍有模型融合/硬 floor 契约差异，不能通过抄 PolyWX 数值修饰。下一步应先闭合 paper settlement/PnL/Brier，才能判断差异是否具备交易价值。
- 相关提交：`02ee52e`、`193422d`。

### 2026-07-11: Layer 3/4 Previous Runs 30-day calibration archive

- 目标：在不降低 20 个独立结算日门槛的前提下，为 14 个 enabled 城市补齐固定 T+24 的 Open-Meteo Previous Runs，并让成熟 bias 真正进入 DEB。
- 改动：Previous Runs collector 改为每个 city/model 一次日期范围请求，而不是逐日请求；按目标城市本地日拆分持久化，保留幂等 run key、原始响应哈希和结构化范围日志。Bias trainer 固定优先 `previous_day1`，避免混入更晚的 current snapshot；DEB 顶层 `bias_correction` 改为真实的分量加权 bias。
- 真实数据：区域主模型完成 41 次请求、写入 1,230 runs 和 1,230 members；ICON/GEM 补充完成 28 次请求、写入 840 runs 和 818 members，HTTP failure=0。重训生成 87 个 city/model 行，其中 69 行达到 20+ 独立日期并可在 runtime 使用。
- 质量样例：Chicago corrected MAE 为 ECMWF 1.0633C、GFS 1.1067C、ICON 0.7467C、GEM 1.3033C、HRRR 1.1067C；Shanghai 为 ECMWF 1.21C、GFS 1.06C、ICON 1.3133C、GEM 1.4286C、CMA 1.6733C。Chicago/Shanghai DEB 冒烟均读取 30 个样本并应用非零加权 bias。
- 验证：`python -m unittest tests.test_v3_core tests.test_ensemble_vs_market` 通过 203 tests；`git diff --check` 通过。Weather.com v3 尚无历史 archive，NBM 仍低于样本门槛；PWS 401 未改变；live 与 auto simulation 保持关闭。
- 结论：此前“没有模型达到 20 个独立日期”的阻塞已解除，下一步可以使用成熟 bias 重建保存日期的 PolyWX Forecast/Cloud/DEB benchmark；这仍不等于策略盈利或 live readiness。
- 相关提交：`937a203`。

最后更新：2026-07-07

> 更早记录见 `docs/PROGRESS_ARCHIVE_CN.md`。日常 Turn Start 只读 `docs/CURRENT_STATE.md`，不要通读本文件或归档，除非任务明确涉及历史决策。

## 当前可用性结论

当前状态：**Phase 1.5 到 Phase 2 过渡**。

- 可以本地打开看板，按城市/日期观察天气证据页、手动或受控自动抓取、查看预报/METAR/历史观测/偏差统计/抓取日志和 gated 信号。
- 已接入默认关闭的 server-side scheduler：可手动启动/停止，按 enabled 城市分频率刷新 METAR、forecast 和派生层，并在看板顶部显示 PolyWX 风格状态徽章。
- 已新增 Polymarket Gamma 结算源核验：7 个重点城市均找到活跃天气市场，其中 6 个与本地观测站一致；Hong Kong 市场按 HKO 天文台结算，与 VHHH 观测站不一致，live gate 已阻塞但 paper/观察保留。
- 已接入 China Weather Live 展示实况：Hong Kong 使用 HKO 官方 rhrread API，Shanghai 使用 weather.com.cn `sk_2d/101020100` JSONP；数据只写 `mesonet_observations`，仅作为 observation evidence，不解锁 live gate。
- 已接入 Wunderground/Weather.com PWS collector 骨架：写 `mesonet_observations.network=wunderground_pws`，display-only/not-settlement-truth，随 METAR poller 同频；当前本机未配置 API key，真实 5 城命令已审计为 skipped 不造数。
- DEB 峰值小时已改为 hourly_consensus 混合曲线 argmax：过去小时用观测覆盖 forecast，并列取最晚；Chicago 2026-07-02 已从 forecast-only 15:00 修正为 mixed 16:00。
- 可以继续做 paper/simulation 验证、数据链路排查、UI 生产化和策略研究。
- 不能声称无人值守自动实盘赚钱；不能直接用当前 EV 信号加仓；不能用局部回测证明稳定 edge。
- 一句话：**现在是可观察、可模拟、可继续生产化验证的天气交易平台雏形；还不是可放心实盘自动赚钱的机器人。**

## 生产阻塞项

1. truth 独立结算日和站点覆盖仍不足，部分城市只能 paper。
2. 仍有未核验城市 `settlement_rule_unverified`；Hong Kong 存在 `settlement_mismatch`，需要 HKO truth collector 后才可讨论 live。
3. forecast archive 与 station truth 的 walk-forward 校准仍需持续扩样。
4. orderbook/best bid/ask/tick/orderMinSize/staleness 的盘口级回放还未完成生产验收。
5. allowed 策略组尚未证明长期 ROI 为正且显著优于 blocked 组。
6. live dry-run、重复订单保护、余额、熔断、14-30 天 paper gate 和 canary gate 仍未完成。
7. Layer 7 看板仍需继续 browser QA，避免空态、主题不一致和刷新状态误导。
8. scheduler 真实长跑还未完成 60 分钟 forecast 周期观测；当前只完成 2 个 METAR 周期冒烟。
9. `dashboard_server.py` 仍有测试期 SQLite connection ResourceWarning，后续非文档轮次应修。

## 最近 5 条完整 ledger

### 2026-07-07：Layer 0/2/4 PolyWX Forecast/China Live/Cloud benchmark 与历史补数

- 目标：用 Firecrawl 抓取 PolyWX 作为 benchmark，横向核对 WeatherBot 的 Forecast、China Live 与 Cloud 百分比口径；只把可由自有 collector 补齐的历史缺口写入数据库，不把 PolyWX 展示值导入 truth/交易表。
- 改动/数据：Firecrawl 抽取 `https://www.polywx.xyz/?city=shanghai-zspd&date=2026-07-06` 与 `https://www.polywx.xyz/?city=chicago-kord&date=2026-07-04`，得到 24 小时 Forecast 与 Cloud benchmark。生成审计目录 `audits/polywx-source-alignment-2026-07-07/`，包含 `polywx-firecrawl-compact.json`、`local-after-backfill.json` 与 `README.md`。先备份 `data/weatherbot_v3.db` 到 `data/weatherbot_v3.db.bak-source-align-20260707-103827`，随后运行 `history-backfill --city shanghai --start-date 2026-07-06 --end-date 2026-07-06`，写入 24 行 `mesonet_observations.network=open_meteo_historical`，再运行 `hourly-consensus-build --city shanghai --target-date 2026-07-06`。
- 验证/结果：Shanghai historical coverage 从 0/24 补到 24/24；Shanghai Forecast MAE vs PolyWX 为 2.11C，Cloud MAE 为 46.36 个百分点；Chicago Forecast MAE 为 1.61F，Cloud MAE 为 20.67 个百分点。Cloud 双方均为 0-100 百分比，不是 0-1 刻度错误；主要差异是 `data_source`，WeatherBot 走 Open-Meteo 多模型/forecast archive，PolyWX 走其自有 forecast feed/处理。Firecrawl 没稳定抽出 China Live 完整 5 分钟序列，用户截图显示 PolyWX 有更密集中国实况；本地 weather.com.cn 当前源只能提供当前快照，不能回填历史 5 分钟序列。
- 结论：本轮安全补齐了 Shanghai 历史线，但没有、也不应把 PolyWX 值写入生产表。Forecast/Cloud 若要“完全像 PolyWX”，下一步需要明确选择：把 `polywx_forecast` 作为 display-only/fallback 用于 UI parity，或继续保留 WeatherBot 自有预报并在看板上诚实标注来源差异。China Live 要达到 PolyWX 5 分钟历史密度，需要另找可回放的中国站点历史 feed。
- 下一步：优先做 Forecast/Cloud 路线决策和实现；若选择 UI parity，扩展现有 `polywx_forecast` display-only ingestion，并禁止参与 training/live gate；若选择独立模型路线，改 UI 标签与说明，避免暗示与 PolyWX 同源。

### 2026-07-04：Layer 2/7 Historical display-only 回填入口与 PolyWX benchmark 工具

- 目标：结合本地实现与 PolyWX 对照建议，不把 PolyWX 展示值导入生产 truth，而是补齐 WeatherBot 自己的历史小时密度入口、诚实区分 METAR/Historical/PWS 系列，并提供 audit-only 的 PolyWX benchmark 与 scheduler 长跑采样工具。
- 改动：`weatherbot_v3/history.py` 新增 Open-Meteo Historical collector，使用 `archive-api.open-meteo.com/v1/archive` 拉取 hourly temperature、humidity、dew point、cloud、wind、pressure、precipitation 等字段，写入 `mesonet_observations.network=open_meteo_historical`，并标记 `display_only/research_truth/not_settlement_truth/open_meteo_archive_grid`；CLI 新增 `history-backfill`，必须显式手动运行，不接入启动流程。`weatherbot_v3/hourly.py` 改为把 `open_meteo_historical`、`wunderground_pws` 与 `china_live` 作为独立系列输出，且不再把非 METAR mesonet 数据冒充成 METAR。前端 `WeatherPanel.tsx` 的 METAR/Historical 表格补齐 humidity、cloud、weather、visibility、wind、pressure、dew、fetched 等列，Historical tab 新增小时级历史表。新增 `tools/scheduler_longrun_probe.py` 用于采样 `/api/scheduler/status` 与 `/api/dashboard`，新增 `tools/polywx_benchmark_snapshot.py` 仅把 PolyWX 页面/API 证据保存到 `audits/`，脚本内明确禁止把 PolyWX 值导入 truth/mesonet/hourly/forecast/trading 表。
- 验证：`.venv\Scripts\python.exe -m unittest tests.test_v3_core tests.test_polywx_contract` 通过 174 tests OK，仍有既有 SQLite connection ResourceWarning；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。测试覆盖 Open-Meteo Historical display-only 入库、Historical/PWS 不冒充 METAR、PolyWX contract 中的 history-backfill/benchmark disclaimer/scheduler probe 关键词。
- 结论：本轮提高了历史/展示数据密度和对照工具质量，但没有把 Open-Meteo Historical 或 PolyWX 当成 settlement truth，也没有解锁 live。若用户看到折线图没有 METAR 白线，现在系统会更诚实地区分：METAR 缺失就是缺失，Historical/PWS/China Live 会作为独立系列出现，不能填充 METAR gate。
- 下一步：如需真实补历史密度，手动运行 `python -m weatherbot_v3.cli history-backfill --city chicago --days 30` 或限定 7 城批量回填，再重建 `hourly-consensus`；如需验证 scheduler 稳定性，运行 `python tools/scheduler_longrun_probe.py --duration-minutes 60 --sample-seconds 300 --start-scheduler --stop-scheduler`。PolyWX 只能用 `tools/polywx_benchmark_snapshot.py` 做 audit 对照，不得导入生产库。
- 相关提交：本轮最终提交。

### 2026-07-04：Layer 6/8 策略复用层、Kelly 仓位与 Ladder Paper 原子执行

- 目标：只改 Layer 6/8，不改前端、不解锁 live；把原本唯一的单桶 EV 策略扩展为可组合策略层，并加入 Kelly 仓位建议。新增三类策略：`single_bucket_ev`、`ladder_grid`、`tail_buying`。
- 改动：新增 `weatherbot_v3/sizing.py`，按二元 YES 合约计算 Kelly fraction，并以 `kelly_multiplier=0.15`、`min(bankroll*0.05, max_per_trade_usd)` 做硬上限；`config.py` 增加 `bankroll_usd`、`kelly_multiplier`、`max_per_trade_usd`，兼容现有 `config.json.balance/max_bet`。新增 `weatherbot_v3/strategies/`：`StrategyBase.evaluate()`、`SingleBucketEVStrategy`（MIN_EDGE 0.05）、`LadderGridStrategy`（μ 附近 3 桶、每桶 edge >=3%、总仓位为中心桶 Kelly 的 0.6 并按 CDF 权重分配）、`TailBuyingStrategy`（ask <=0.15、edge >=10%、独立结算日 >=20、单笔 cap $50、日候选 cap 5）。`signals.py` 改为统一调用策略层，保留 single bucket 旧 decision_id 稳定性；`signal_decisions` 自动迁移并持久化 `strategy_name`、`kelly_fraction`、`position_size_usd`、`ladder_group_id`。`paper.py` 支持 ladder group 原子执行：同组 3 桶全组预检查，任一盘口/风控失败则不写任何订单。`dashboard_server.py` 后端推荐 payload 增加策略标签、Kelly/仓位和 ladder `sub_buckets`，但本轮未改 React 前端。
- 验证：新增 `tests/test_kelly_sizing.py` 与 `tests/test_strategies.py`；`.venv\Scripts\python.exe -m unittest tests.test_kelly_sizing tests.test_strategies` 通过 8 tests OK；`.venv\Scripts\python.exe -m unittest tests.test_v3_core` 通过 160 tests OK，仍有既有 SQLite ResourceWarning；`.venv\Scripts\python.exe -m unittest tests.test_polywx_contract tests.test_kelly_sizing tests.test_strategies` 通过 20 tests OK。未运行 `npm run build`，因为本轮没有改前端文件。
- 真实跑数：执行 `signal-decisions-build --city chicago --city nyc --city atlanta --target-date 2026-07-04 --limit 200`，写入 33 条最新决策（每城 11）；最新 issued_at 均为 `2026-07-04T11:00:00+00:00`。最新轮次按策略统计：`single_bucket_ev=33`、`paper_allowed=0`、平均 edge `-0.009212`、平均 Kelly `0.024041`；`ladder_grid=0`，原因是没有 3 桶同时满足 edge/sizing；`tail_buying=0`，原因是未同时满足 ask、edge、历史样本 gate。审计报告写入 `audits/strategy-multiplex-report-2026-07-04.md`，不提交。
- 结论：策略复用层、Kelly sizing 和 ladder paper 原子执行已落地并有回归测试；真实当日 Chicago/NYC/Atlanta 仍无可执行 paper 候选，主要阻塞是 `insufficient_bias_samples`、`settlement_rule_unverified`、`spread_too_wide`，说明当前不能自动加仓，更不能 live。`LIVE_TRADING=false` 未改变。
- 下一步：继续补 settlement verification/truth 样本与盘口 replay；若要让 ladder/tail 真正参与 paper validation，需要先改善样本、价差和市场桶匹配，不应通过放宽 gate 伪造候选。
- 相关提交：`dbd3459`。

### 2026-07-04：Layer 2/4/6 PWS、DEB peak 与摄氏桶口径修正

- 目标：落实第二轮修改建议：导出 Chicago 2026-07-02 峰值差异审计；DEB `peak_hour` 改为混合曲线 argmax；新增 Wunderground PWS 聚合 collector 写入 `mesonet_observations`；核对并测试摄氏度市场桶按截断口径积分；本轮结束提交。
- 改动：新增 `audits/peak-diff-chicago-2026-07-02.md`，导出 24 行 `hourly_consensus` 并逐源标注 forecast/observed/mixed 最大值。`weatherbot_v3/deb.py` 新增 `mixed_curve_argmax_v1`：以 build `issued_at` 为分界，已发生小时优先 `observed_temp`，未来或缺观测小时用 `forecast_temp`，并列最大值取最新本地小时；`daily_max_predictions` 增加 `peak_hour`、`peak_temp`、`peak_source` 持久化字段。新增 `weatherbot_v3/pws.py`，通过 Weather.com/Wunderground PWS API 发现/拉取邻近 PWS，聚合为 `wunderground_pws`，并标记 `display_only`、`not_settlement_truth`；CLI 增加 `pws-fetch`，scheduler 的 `metar_poller` 同频调用但 PWS 缺 key 不拖垮 METAR。`bucket_probabilities()` 的摄氏度整数桶改为截断口径，例如 `23°C` 为 `[23,24)`，不是 `23±0.5`。
- 审计结论：WeatherBot 本地 DB 中 Chicago 2026-07-02 forecast-only max 为 `94.50°F @15:00`，METAR observed max 为 `93.92°F @13:00/14:00/15:00/16:00`，mixed tie policy 选择 `16:00`；本地 PolyWX corpus 未捕获 2026-07-02 peak-marker XHR，因此外部 `17:00` 说法仍需 fresh capture 后才能作为源证据。重新跑 `daily-max-build --city chicago --target-date 2026-07-02` 后落库 `peak_hour=16:00`、`peak_source=metar`。
- PWS 跑数：真实执行 `pws-fetch --city chicago --city nyc --city dallas --city atlanta --city miami --station-limit 5`，5 城均返回 `missing_wunderground_api_key`，`rows_upserted=0`、`skipped=5`、`failed=0`；取证写入 `audits/pws-wunderground-fetch-2026-07-04.md`。这是预期行为：未配置 `WUNDERGROUND_API_KEY` 或 `WEATHER_COM_API_KEY` 时不抓不造假数据。
- 验证：`.venv\Scripts\python.exe -m unittest tests.test_v3_core tests.test_scheduler` 通过 164 tests OK，仍有既有 SQLite ResourceWarning；`.venv\Scripts\python.exe -m unittest tests.test_polywx_contract` 通过 12 tests OK；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。
- 结论：峰值标记口径、PWS display-only 数据入口和 C 桶概率边界已补齐；PWS 真实入库仍阻塞于本机未配置 Wunderground/Weather.com API key。该轮不改变 live gate，实盘继续锁定。
- 下一步：若要让 PWS 紫色三角真实出现，需要配置 `WUNDERGROUND_API_KEY` 或 `WEATHER_COM_API_KEY` 并重跑 `pws-fetch`/scheduler；若要核对 PolyWX 17:00，需要重新捕获 2026-07-02 Chicago 的 PolyWX peak-marker XHR。
- 相关提交：本轮最终提交。

### 2026-07-04：Git 落盘与推荐 gate 根因诊断

- 目标：本轮不加新功能，只把前四轮改动按 Layer 落成原子 commit，并新增只读诊断脚本定位“推荐关注=0”的根因；重点判断 `metar_stale_or_missing` 是否是 D+1/D+2 决策被误套 D+0 METAR 新鲜度。
- 改动：新增 `tools/diagnose_recommendation_gate.py`，对 chicago/tokyo/atlanta/nyc/dallas/shanghai/hong-kong 输出最新 METAR `report_time`、age、fresh 状态、最新 `signal_decisions` 的 `target_date/issued_at`、stored gate reasons Top3 与 recommendation filter reasons Top3；诊断结果写入 `audits/recommendation-gate-diagnosis-2026-07-04/`，不提交 audits。根据最终诊断触发限定修复：`dashboard_server.py` 的 `_recommendations_payload()` 拆成 `today_observation` 与 `forecast_lead` 两类，D+0 要求 METAR 报文 <30 分钟，D+1/D+2 只要求 forecast run <90 分钟，不再被 stale METAR 误杀。
- 诊断结论：第一次诊断时 7 城 METAR age 约 95-134 分钟，verdict=`collector_stale_or_missing`；启动 scheduler 后 METAR 先恢复 fresh，推荐短暂出现 1-2 个候选，但 30 分钟末尾又因为 METAR 报文真实发布时间落在上一小时 `:51/:52/:53` 而重新出现 `metar_stale_or_missing`。最终诊断出现 `horizon_mismatch_candidates=3`，说明确有少量 D+1/D+2 候选被 D+0 METAR freshness 误杀；修复后直接调用新源码 `_recommendations_payload(scheduler_status={'running': True})` 返回 3 个 `forecast_lead` 候选。
- 验证：`git diff --check` 通过，仅 Windows LF/CRLF warning；误用系统 `python` 的一次测试因缺少 `requests` 失败，随后使用 `.venv\Scripts\python.exe` 跑完整套件通过 176 tests OK，仍有既有 SQLite ResourceWarning；`npm run build` 通过，仍有既有 Browserslist/chunk warning；新增回归 `test_dashboard_recommendations_use_forecast_freshness_for_lead_dates` 覆盖 D+1 新 forecast + stale METAR 的场景。
- 30 分钟 scheduler 采样：手动 `POST /api/scheduler/start`，起点 `2026-07-04T11:06:31Z`；第一轮后 METAR poller `last_run_at=2026-07-04T11:07:39Z`，dashboard skipped 变为 `paper_gate_blocked=154`、`older_decision_round=660`；约 25 分钟时推荐为 1；满 30 分钟前后 `metar_last=2026-07-04T11:35:22Z`、`forecast_last=2026-07-04T11:11:14Z`、`derive_last=2026-07-04T11:34:47Z`、`metar_runs=12`、`forecast_runs=2`、`derive_runs=4`，旧后端 payload 推荐为 0 且 skipped=`metar_stale_or_missing=132`、`older_decision_round=792`、`paper_gate_blocked=22`；随后停止 scheduler 成功。
- 相关提交：settlement `08f6008`，China/scheduler `b86dd51`，Layer7 charts `ba334a4`，recommendation/diagnostics/docs 为本轮最终提交。

### 2026-07-04：Layer 6/7 调度器驱动推荐关注闭环

- 目标：把第 1-4 轮成果串起来，让顶部“推荐关注”不再依赖手动单城市抓取或 legacy `weather_signals.actionable`，改为从调度器持续更新后的全城市 `signal_decisions` 最新一轮自动生成；保持 live 全锁，不新增 paper 执行按钮。
- 改动：`dashboard_server.py` 新增 `_recommendations_payload()`，按 `METAR age < 30min`、`stations.settlement_rule_verified_at` 非空、`market_buckets.strict_match_status=matched`、`paper_allowed=true` 或仅 `spread_too_wide` 阻塞筛选交易候选；同一城市只展示当前最佳候选，并返回城市、站点、当前观测、DEB `mu±sigma`、最优桶、edge、阻塞原因与 Polymarket 链接。上海/香港若 `verification_status=no_active_market` 则返回 `observation_only`，只展示 DEB 与 China Weather Live，不展示 bucket/token/edge/gate。`/api/dashboard` 每次请求都会附带最新 `recommendations`，不等待 dashboard cache 重建。`frontend/src/App.tsx` 将推荐卡改为 `DashboardRecommendationItem` 渲染，显示 `METAR age` 与 `verified` 状态；空态区分 `scheduler_stopped` 与 gate 后无候选，并展示 skip 计数。补充 `DashboardRecommendations` 类型和推荐筛选/无市场分支测试。
- 验证：`python -m unittest tests.test_v3_core -k recommendations` 通过 3 tests；`python -m unittest tests.test_polywx_contract` 通过 12 tests；完整 `python -m unittest tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled tests.test_polywx_contract` 通过 175 tests；`npm run build` 通过；`git diff --check` 通过，仅 Windows LF/CRLF warning；浏览器打开 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-03`，推荐关注区域可见，console error=0，无横向溢出。
- 30 分钟 scheduler 验证：手动启动 scheduler 后每 5 分钟采样 7 次并自动停止。METAR poller 从第 1 个完整周期开始连续成功 `ok_cities=7 / failed_cities=0`，`fails_last_hour=0`，最后一次 `metar_last_run_at=2026-07-04T09:40:10Z`、poller age 约 90s；推荐数量从 sample0 的 5 个、sample1 的 2 个，在 derive_poller 于 `2026-07-04T09:17:56Z` 重建后降为 0，并在最后 sample6 仍为 0，`empty_reason=no_recommendations_after_gates`，主要 skip 为 `metar_stale_or_missing=88`、`paper_gate_blocked=66`、`older_decision_round=638`。
- 结论：推荐关注的“数据链路/自动刷新/UI 展示”已打通，但当前策略 gate 下不能保证推荐列表稳定出现 ≥2 城市；这是最新 `signal_decisions` 与盘口/新鲜度/纸面交易门槛筛选后的真实结果，不是前端不刷新。当前系统可用于观察实时推荐空态、skip 计数和候选卡，但仍不能进入自动 paper 或 live。
- 下一步：优先查 derive 后 `signal_decisions` 为何大量落入 `metar_stale_or_missing` 与 `paper_gate_blocked`，需要把最新 METAR refresh 与 decision target 的 target_date/local-day 对齐，并做 Layer 9 paper validation 前的 gate 分布报表；若要稳定出现候选，不能放宽 live/paper 安全门槛，只能改善数据新鲜度、市场桶匹配和盘口可用性。
- 相关提交：本轮最终提交。

### 2026-07-04：Layer 7 PolyWX 高保真小时图与高斯桶前端整改

- 目标：本轮只改 Layer 7 前端，不改后端 schema、不触发抓取；以 PolyWX `chicago-kord` 工作台和本地 audits corpus 为验收标准，重点修正 Hourly Temperature 与 Probability buckets 的视觉编码、读图顺序和空白问题。
- 改动：`frontend/src/components/WeatherPanel.tsx` 将小时图升级为 Recharts `ComposedChart` 多系列：METAR 橙色实线圆点、Historical 绿色实线圆点、China Weather Live 红色方块点、PWS 紫色三角点、Forecast 蓝色虚线空心圆；Cloud Cover % 改为右轴半透明 Area；X 轴固定 00:00-23:00；峰值改为 `ReferenceLine` + 粉底白字 `peak HH:00`；图下增加 `AVG Δ (OBS−FC)`、`ACCURACY (PEARSON R)`、`HIST↔METAR OVERLAP` 三行徽章与诚实空态。高斯桶图固定 0-25% Y 轴，最高 1-2 个桶用 `#2563EB`，其余 `#4B5563`，移除卖一折线，bucket 表紧贴图表下方，指标卡后置。
- Recharts 属性表：

| 模块 | 系列/元素 | 样式 |
| --- | --- | --- |
| Hourly | METAR | `stroke="#F97316"`、`strokeWidth={2}`、`dot={{ r: 3 }}`、`activeDot={{ r: 5 }}` |
| Hourly | Historical | `stroke="#22C55E"`、实心圆点 |
| Hourly | China Weather Live | `stroke="#EF4444"`、`SquareDot` |
| Hourly | PWS | `stroke="#A855F7"`、`TriangleDot` |
| Hourly | Forecast | `stroke="#3B82F6"`、`strokeDasharray="4 4"`、`HollowCircleDot` |
| Hourly | Cloud Cover % | right axis 0-100、`Area fill="#94A3B8" fillOpacity={0.25}` |
| Hourly | Peak | `ReferenceLine stroke="#EC4899"`、label `peak HH:00` |
| Buckets | top buckets | `Cell fill="#2563EB"` |
| Buckets | other buckets | `Cell fill="#4B5563"`、Y axis 0-25% |

- 验证：`npm run build` 通过；`python -m unittest tests.test_polywx_contract` 通过 12 tests；`python -m unittest tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled tests.test_polywx_contract` 通过 172 tests OK，仍有既有 SQLite ResourceWarning 噪声；浏览器打开 `http://127.0.0.1:5173/?city=chicago-kord&date=2026-07-03` 和 `http://127.0.0.1:5173/?city=shanghai-zspd&date=2026-07-04`，无 console error、无横向溢出。截图：`audits/layer7-polywx-visual-2026-07-04/chicago-2026-07-03-wait8.png`、`audits/layer7-polywx-visual-2026-07-04/shanghai-2026-07-04-wait8.png`、`audits/layer7-polywx-visual-2026-07-04/chicago-2026-07-03-buckets-final.png`。
- 结论：Layer 7 核心图表已经按 PolyWX 风格完成一轮高保真整改；Chicago 页面可完整展示小时图和高斯桶，Shanghai 2026-07-04 可展示中国城市路径。Shanghai 2026-07-03 没有可绘制小时字段时会显示诚实空态，不属于前端渲染错误。
- 下一步：继续做浏览器 QA 与组件拆分，尤其将 `WeatherPanel.tsx` 拆出 HourlyChart、ProbabilityBuckets、EvidenceBadges；若继续数据问题，优先查 scheduler/current date 写入与 `/api/hourly-consensus` 数据完整性。
- 相关提交：`ba334a4`。

### 2026-07-04：Layer 2 China Weather Live mesonet_observations

- 目标：参考 PolyWX 图例中的 “China Weather Live” 红色系列，为中国城市补充国内/本地实况源；严格作为 `mesonet_observations` observation evidence，不写入 `metar_reports`，不作为 settlement truth，也不解锁 live gate。
- 改动：新增 `weatherbot_v3/china_weather.py` collector，支持 `python -m weatherbot_v3.cli china-weather-fetch --city shanghai --city hongkong`；Hong Kong 使用 HKO 官方开放数据 `https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en`，读取 Hong Kong Observatory 的 temperature/humidity recordTime；Shanghai 使用 `http://d1.weather.com.cn/sk_2d/101020100.html?_=<ts>` 的 `var dataSK={...}` JSONP，读取 temp/SD/qy/wde/wse/time/date；`mesonet_observations` 增加 `raw_response`、`raw_response_hash`、`fetched_at`，并让缺失数值保留 NULL；scheduler 新增 `china_live_poller`，默认随 scheduler 手动启动后每 5 分钟只拉 shanghai/hong-kong；`hourly.py` 保存 `source_temperatures.china_live`，让 China Live 不覆盖 METAR diff；前端 `WeatherPanel.tsx` 新增红色 “China Weather Live” 折线和状态条新增 China Live 徽章。
- 实际跑数：真实运行 `china-weather-fetch --city shanghai --city hongkong`，写入 2 行 `mesonet_observations`：Shanghai `station_id=101020100`、temperature 31.9C、humidity 69%；Hong Kong `station_id=HKO`、temperature 30.0C、humidity 80%。随后运行 `hourly-consensus-build --city shanghai --city hong-kong --target-date 2026-07-04`，两城 10:00 local 均可在 `hourly_consensus_points` 读到 `china_live` 红线值，`metar` 未被伪装。
- 已知限制：HKO rhrread 为官方 JSON、免 key，分钟级/近实时；weather.com.cn `sk_2d` 是公开 JSONP，需要 `User-Agent` 与 `Referer`，字段为城市级/中国天气网口径，不是 ZSPD 机场结算 truth；HTML fallback 只用于显式失败/降级，不静默造数；两源均标记 `display_only`、`not_settlement_truth`。
- 验证：`python -m unittest tests.test_polywx_contract tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled` 通过，172 tests OK；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning；测试仍打印既有 SQLite ResourceWarning。
- 结论：Layer 2 中国实况展示源已可用，PolyWX 风格红线数据链路打通；这增强看板证据密度，但不改变 live gate，尤其 Hong Kong 仍需单独 HKO settlement truth collector 才能讨论实盘。
- 下一步：做浏览器 QA 确认 Shanghai/Hong Kong 页面红线展示、toast 与 `china_live_poller` 状态一致；后续如要把 Hong Kong HKO 升级为 truth，必须另起 Layer 2/5 truth collector 轮次并绑定结算规则。
- 相关提交：`b86dd51`。

### 2026-07-04：Layer 1/5 Polymarket 结算源 Gamma 核验

- 目标：按 alteregoeth-ai/weatherbot 的核心教训，先钉死每个 Polymarket 天气市场的真实结算站点、来源、单位和时区；尤其确认上海/香港是否能作为 observation station 直接进入 live gate，避免把市中心、错误机场或二手展示源当成结算 truth。
- 改动：新增 `weatherbot_v3/polymarket_probe.py` 与 CLI `python -m weatherbot_v3.cli polymarket-market-probe --city <key>`，按 D0-D3 探测 Gamma active event 并解析 market description/source URL；`stations` 扩展并持久化 `settlement_station_id`、`settlement_station_name`、`settlement_timezone`、`settlement_unit`、`settlement_time_basis`、`settlement_rule_verified_at`；`sync_station_registry()` 保留已核验结果；`qualification.py` 与 `signals.py` 将 `settlement_rule_unverified`/`settlement_mismatch` 纳入 live gate，但不阻塞 paper；看板城市头部新增 Settlement station、Rule verified、Timezone、Truth source 与 Non-truth source 标签；补充 Gamma probe、resolution parser、mismatch live gate 和 dashboard 合约测试。
- 核验结果：

| 城市 | 活跃市场 | 观测站 | 结算站 | 来源机构 | 单位 | 时区 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shanghai | yes | ZSPD | ZSPD | wunderground | C | Asia/Shanghai | verified |
| hong-kong | yes | VHHH | HKO | hong_kong_observatory | C | Asia/Hong_Kong | settlement_mismatch |
| chicago | yes | KORD | KORD | wunderground | F | America/Chicago | verified |
| tokyo | yes | RJTT | RJTT | wunderground | C | Asia/Tokyo | verified |
| atlanta | yes | KATL | KATL | wunderground | F | America/New_York | verified |
| nyc | yes | KLGA | KLGA | wunderground | F | America/New_York | verified |
| dallas | yes | KDAL | KDAL | wunderground | F | America/Chicago | verified |

- 验证：真实运行 `polymarket-market-probe` 覆盖 shanghai/hong-kong/chicago/tokyo/atlanta/nyc/dallas，active=7、verified=6、mismatches=1；`python -m unittest tests.test_polywx_contract tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled` 通过；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`git diff --check` 通过，仅 Windows LF/CRLF warning。
- 结论：上海可按 ZSPD/Wunderground 进入已核验 observation truth 链路；香港市场结算为 HKO 天文台 Daily Extract，不能用 VHHH METAR 解锁 live，当前必须 `settlement_mismatch` 阻塞实盘，只保留 paper/观察；其余 5 个主力城市与现有 station_id 一致。
- 下一步：为 Hong Kong 接 HKO Daily Extract truth collector，或继续批量核验剩余城市；在此之前不要扩大香港 live 自动交易。
- 相关提交：`08f6008`。

## 下一步优先级

1. 下一轮开工只读 `docs/CURRENT_STATE.md`，避免重复读取长归档。
2. 若继续 UI：优先 Layer 7 browser QA，确认 Shanghai/Hong Kong 红色 China Weather Live 曲线、scheduler China Live 徽章、toast 与主题一致。
3. 若继续结算源：优先补 Hong Kong HKO truth collector 或批量核验剩余城市的 settlement rule。
4. 若继续数据：跑 60 分钟 forecast scheduler 长跑，确认 forecast_poller 真实完成并写入状态。
5. 若继续策略：进入 Layer 9 paper validation，评估样本、盘口回放和 allowed/blocked ROI。
6. 实盘继续锁定，直到 paper validation 和 canary dry-run gate 通过。

## 每轮更新模板

```text
### YYYY-MM-DD：本轮标题

- 目标：
- 改动：
- 验证：
- 结论：
- 下一步：
- 相关提交：
```

### 2026-07-05：README 运行手册与本地启动烟测

- 目标：把根目录 README 从旧版 WeatherBet 单体说明更新为当前 WeatherBot v6 的 GitHub 风格运行手册，并实际启动本地项目供人工核验。
- 改动：重写 `README.md`，说明当前实现路径、目录结构、后端/前端启动方法、端口占用处理、看板操作、常用 CLI、人工核验命令、测试命令、配置变量、当前限制与风险声明；明确 `weatherbet.py`/legacy 不是 v6 主运行入口。
- 验证：已启动 FastAPI 后端 `http://127.0.0.1:8765` 与 Vite 前端 `http://127.0.0.1:5173/`；`/api/dashboard` 返回成功，当前运行态为 `scanner_status=stopped`、`is_running=false`、`stats.auto_simulation.enabled=false`、`production_refresh.running=false`、`scheduler_status.running=false`；前端 HTTP 200。
- 结论：项目已在本机跑起，可打开 `http://127.0.0.1:5173/` 人工核验；本轮没有启动 scheduler、没有开启实盘、没有修改交易逻辑。
- 下一步：人工检查 README 的启动/核验步骤是否符合你的操作习惯；如要继续验证数据刷新，再在看板点击“启动调度器”并观察顶部 poller 徽章。
- 相关提交：未提交。
### 2026-07-05: Asian Polymarket weather market source snapshot
- Target: complete Asian weather-market research addendum A-F without changing trading code or unlocking live execution.
- Changes: added `docs/polymarket_asia_markets_snapshot.md`, `docs/polymarket_asia_markets_snapshot.csv`, and `docs/open_meteo_asia_samples.json`. The snapshot covers active Gamma Asian highest-temperature events, per-bucket CSV rows, Wunderground/HKO source reachability, ZBAA Wunderground vs AWC/IEM feasibility, Open-Meteo CMA/JMA/GFS ensemble/historical/previous-runs availability, unit/timezone contract notes, Asian city strategy priority, and UMA MOOV2 settlement-delay constraints.
- Verification: Gamma exact slug probes found 31 active Asian events and 341 bucket rows; Wunderground base URLs are reachable for 9 non-HK Asian airport markets; Hong Kong uses HKO Daily Extract rather than Wunderground. Open-Meteo forecast, ensemble, historical forecast, and previous-runs probes succeeded for Shanghai, Beijing, Hong Kong, Tokyo, Seoul, Taipei, and Chicago.
- Conclusion: Asian markets are confirmed and worth integrating, but exact settlement replication still needs a dedicated Wunderground daily-history path; IEM/AWC METAR max should remain an approximation, not live-unlocking settlement truth.

### 2026-07-11：Layer 2 Truth 30 天回填与调度器初验

- 目标：在不解锁实盘的前提下，用 6 小时以上调度器连续运行作为开发初验，并补齐 WU、HKO、IEM 的固定 30 天 truth 数据基座。
- 改动：IEM 改为每站一次区间请求、每站一个 SQLite 事务和每区间一条结构化日志；HKO 改为每月一次请求并只持久化目标日紧凑原文；CLI 返回紧凑结果；`log_data_fetch` 支持显式测试数据库路径。
- 验证：调度器连续运行约 9 小时后停止，METAR/forecast/Gamma/derive 分别完成 102/8/90/20 个周期，瞬时单城 WARN 可恢复，PWS 仍因权限返回 401。固定窗口 2026-06-08 至 2026-07-07：WU 13 城 390/390 天、IEM 14 城 420/420 天、HKO 30/30 天；WU-IEM overlap=390，HKO-VHHH overlap=30。全量 `tests.test_v3_core` 187 tests 通过，`git diff --check` 通过。
- 结论：6 小时门槛足够继续开发，且实际已取得约 9 小时证据；但它只证明调度器具有初步连续性，不证明策略盈利或无人值守生产稳定。三类 truth 历史源现均为 healthy，实时源因调度器停止而 stale 属预期。
- 阻塞：7 个亚洲城市 settlement contract 仍 provisional；Hong Kong 保持 settlement mismatch；PWS key 无 v2 权限；derive 周期可能超过 15 分钟；paper settlement/PnL/Brier 生命周期未闭合。
- 下一步：先核验 7 个 provisional settlement contracts，再用 30 天 truth 做逐城市/模型 bias 训练与 PolyWX benchmark；之后实现 paper settlement/scoring，实盘继续锁定。
- 相关提交：`26d887f`。

### 2026-07-11：Layer 1/5 剩余亚洲城市结算规则核验

- 目标：修复 `verification_status` 与 `settlement_rule_verified_at` 的一致性，并把 7 个 provisional 亚洲城市按当前活跃 Polymarket 市场规则真实核验，不自动篡改站点。
- 改动：Gamma probe 对北京、青岛、首尔、深圳、新加坡、台北、武汉逐城取证。修复 ICAO 解析只接受部分首字母、遗漏 `WSSS` 的问题；`verified` 现在必须同时具备规则原文、结算站、来源、单位、时区和本地日口径，缺字段时保持 `unverified` 且不写 verified timestamp。
- 验证：7/7 城均找到 2026-07-11 活跃市场，结算站分别为 ZBAA/ZSQD/RKSI/ZGSZ/WSSS/RCSS/ZHHH，与本地 observation station 全部一致；13 城状态为 verified，Hong Kong 保持 HKO/VHHH settlement_mismatch。reconciliation 检查 26 行，repaired=0、inconsistent=0；source health settlement coverage 从 50% 升为 100%；`tests.test_v3_core` 189 tests 全通过，`git diff --check` 通过。
- 结论：站点核验状态倒挂已闭环，空站点或半截 Gamma 规则不能再误解锁 live gate。source health 仍 blocked 是因为香港真实 mismatch，以及调度器停止后实时 METAR/orderbook/derive 数据 stale，并非结算覆盖缺失。
- 下一步：用 30 天 WU/HKO/IEM truth 训练逐城市/模型 bias，并单独处理 PWS 401 entitlement；随后重建保存日期的 PolyWX Forecast/Cloud/DEB benchmark。
- 相关提交：`1e90c5c`。

### 2026-07-11：Layer 3/4 无泄漏模型 Bias 校准审计

- 目标：使用已经补齐的 30 天 WU/HKO/IEM truth 训练逐城市、逐模型 bias，同时证明训练不会读取目标日之后的 forecast 快照。
- 改动：`bias.py` 改为 Hong Kong 优先 HKO、其他城市优先 WU、IEM 仅 fallback；每个 city/model/date 只选 target local-day 开始前的最新 forecast as-of，Previous Runs 使用其归档 run_at。bias 表新增 raw MAE、去偏后 MAE/RMSE、最近 7 日 MAE、truth basis、独立日期、泄漏排除数和 runtime eligibility；少于 20 个独立日期时 bias 和 MAE 均不得改变 DEB。输出采用临时文件原子替换。
- 验证：真实生成 14 城、87 个 city-model 行；runtime_eligible_rows=0。Chicago/Atlanta/Dallas 的最大独立样本为 13/12/13，Shanghai/Tokyo 为 9/11，多数新增亚洲城仅 1-7；被 as-of 规则排除的目标日后快照数量已逐模型记录。`tests.test_v3_core` 192 tests 通过，`tests.test_ensemble_vs_market` 7 tests 通过，`git diff --check` 通过。
- 结论：此前 trainer 错把 IEM 放在 WU 前、用绝对 bias 冒充 MAE，并可能选择目标日后快照；三项问题均已修复。当前没有足够 forecast archive，系统继续安全地不应用 runtime bias，不能通过降低 20 日门槛伪造校准成熟度。
- 下一步：按城市和模型批量补 Open-Meteo Previous Runs，目标至少 20 个独立日期；重训后再重建保存日期的 PolyWX Forecast/Cloud/DEB benchmark。
- 相关提交：`bbe104b`。
- Next: wire Asian station registry/model preferences in a separate implementation turn; prioritize Shanghai/Wuhan/Beijing, keep Hong Kong truth-gated until HKO collector is production-ready, and keep Seoul monitor-only unless paper evidence improves.
- Commit: not committed in this research turn; workspace already had unrelated dirty files.
## 2026-07-05：Round 3 Truth Layer 三源协议 + Gamma 结构化持久化

- 改动：将旧 `weatherbot_v3/truth.py` 迁移为 `weatherbot_v3/truth/__init__.py`，新增 `truth/iem_asos.py`、`truth/hko.py`、`truth/wunderground.py`、`truth/delta.py`；新增 Gamma 结构化模块 `weatherbot_v3/polymarket_gamma.py`；SQLite 新增 `truth_iem_daily`、`truth_iem_hourly`、`truth_wunderground_daily`、`truth_hko_daily`、`truth_delta_audit`、`polymarket_events`、`polymarket_markets`、`polymarket_orderbook`。
- 改动：补齐亚洲城市 registry/stations：Beijing ZBAA、Wuhan ZHHH、Qingdao ZSQD、Shenzhen ZGSZ、Taipei RCSS，并确保 Seoul/Singapore 本地旧库可选；Hong Kong 观测站保留 VHHH，但 settlement truth 指向 HKO Daily Extract；受控 scheduler 新增 `gamma_orderbook_poller`，默认停用，手动启动 scheduler 后每 300 秒刷新亚洲活跃事件与 orderbook。
- 验证：`python -m unittest tests.test_v3_core` 167 tests OK；`python -m unittest tests.test_polywx_contract` 12 tests OK；`python -m unittest tests.test_scheduler` 6 tests OK；`git diff --check` OK（仅 CRLF warning）。新增测试覆盖 IEM F->C、HKO Daily Extract、Wunderground skip reason、Gamma 三表持久化、11/6 桶动态边界、`or higher/or lower` 尾桶和 scheduler status shape。
- 真实冒烟：IEM ZBAA 2026-06-27 返回 high_c=35.0、obs_count=24；HKO 2026-07-04 官方 Daily Extract 当前只发布到 7/2，返回 `date_not_found_in_hko_daily_extract`，HKO 2026-07-02 成功 high_c=32.2；Wunderground ZBAA 2026-06-27 无 key/未授权返回 `http_401` skip；Shanghai 2026-07-06 Gamma 同步 1 event、11 markets、11 orderbooks，尾桶 `37°C or higher` 正确解析为 `[37,+inf)`。
- 结论：Round 3 数据结构和 collector 路径可用；IEM 是可靠近似 truth，HKO 是 Hong Kong P0 truth，Wunderground 是可选 truth 且失败不阻塞。当前仍不能用这些数据直接解锁 live，需后续 Round 4/5 把 truth_delta、Gamma orderbook refresh 和策略/看板消费链路接入。

### 2026-07-05: Round 4 Ensemble DEB + bucket calibration foundation
- Target: replace single Gaussian-only DEB consumption with an ensemble-backed probability path, add initial market sanity calibration, wire model reprice events, and keep live trading locked.
- Changes: added `weatherbot_v3/forecasts/ensemble.py`, `weatherbot_v3/bias.py`, and `scripts/train_bias.py`; extended `daily_max_predictions`, `signal_decisions.forecast_algo`, and `model_reprice_events`; `signals.py` now consumes ensemble sample distributions when available and falls back to Gaussian CDF otherwise. `openmeteo.py` now includes CMA GRAPES for China/HK deterministic fetches; `config.py` stores Asian city priority modes.
- Verification: ran 195 tests OK with `python -m unittest tests.test_ensemble_vs_market tests.test_deb_gaussian tests.test_v3_core tests.test_scheduler tests.test_polywx_contract`; `git diff --check` passed with only Windows line-ending warnings. Real smoke generated `data/bias_table.json`, fetched Shanghai Open-Meteo ensemble data, built `ensemble_v1` daily max for Shanghai 2026-07-06, rebuilt Shanghai signal decisions, and stored `model_reprice_events`. The earlier SQLite ResourceWarning noise was resolved in the 2026-07-05 Previous Runs follow-up.
- Conclusion: Round 4 foundation is usable for paper/research probability comparisons. Live remains locked. The 341-bucket calibration is currently a snapshot sanity baseline because the local DB does not yet contain full archived ensemble runs for every historical bucket; this must become a Previous Runs walk-forward before profitability claims.
- Next: collect archived Open-Meteo previous-runs for the 341 bucket set, expand ensemble coverage beyond the smoke city/date, then rerun calibration with real historical lead-time alignment.
- Commit: not committed in this turn.

### 2026-07-06：Round 5 Layer 7 UI 大清理 + i18n + 城市状态可视化

- 目标：只做 Layer 7 UI 与只读展示支撑，不改交易算法、不解锁 live；把 10 个亚洲城市的 fully_active / paper_only / monitor_only / observation_only 状态显性化，并补齐 i18n、动态 bucket 表、Delta Audit 和 alpha candidate 标记。
- 改动：新增 `frontend/src/i18n/zh-CN.json`、`frontend/src/i18n/en.json`、`frontend/src/i18n/useT.ts`；顶栏新增分组 City dropdown 与语言 dropdown；中间主板新增 HK paper-only 黄色横幅、Seoul monitor-only 红色横幅、fully_active 市场信息条；新增 `DeltaAuditPanel`，读取 `truth_delta_audit`；概率桶明细从表格改为 6-12 桶动态 grid，展示 model_prob / market_mid / edge / bid-ask，edge 绝对值 >8pp 高亮；alpha candidate 用 `model_reprice_events` 显示 ⚡ tooltip。
- 后端支撑：`dashboard_server.py` 新增只读 `/api/truth-delta-audit`、`/api/model-reprice-events`，dashboard payload 暴露 `city_statuses`；`weatherbot_v3/db.py` 新增 truth delta 与 model reprice summary/list 函数。
- 验证：`npm run build` 通过；`.venv\Scripts\python.exe -m unittest tests.test_polywx_contract tests.test_v3_core` 通过，181 tests OK；`git diff --check` 通过，仅 Windows LF/CRLF warning。
- 结论：Round 5 UI 框架可用，城市状态与风险提示更清晰；Delta/Alpha 页面依赖 Round 3/4 表内真实数据，没数据时显示诚实空态。live 仍锁定。
- 下一步：浏览器人工 QA 10 城城市切换、HK/Seoul 横幅、6/12 桶动态表；之后进入 Round 6 前应先补 Previous Runs/Truth Delta 数据密度与 paper validation。
- 相关提交：本轮提交已完成，最终 hash 见本轮回复。

### 2026-07-05: Previous Runs walk-forward entry and SQLite warning cleanup

- Target: close the main leftover from Round 4 by replacing fake market-normalized "calibration" with a real Open-Meteo Previous Runs ingestion path, then remove noisy SQLite ResourceWarnings from the test run.
- Changes: extended `weatherbot_v3/openmeteo.py` with `fetch_openmeteo_previous_runs`, per-region model selection, local-day-to-UTC request windows, previous_dayN parsing, and explicit data-fetch logging; added `openmeteo-previous-runs` CLI; added `previous_run_samples` and `previous_run_distribution_for_buckets` to `weatherbot_v3/forecasts/ensemble.py`; updated `tests/test_ensemble_vs_market.py` so the 341-bucket snapshot is labeled as a market baseline rather than model probability. Fixed legacy `dashboard_db.py` by making `_connect()` return a closing sqlite connection.
- Verification: real smoke fetched Beijing 2026-07-05 previous-day 1/2/3 archived runs for ECMWF/GFS/CMA and wrote 9 forecast runs plus 9 members; report written to `audits/previous-runs-beijing-2026-07-05.md`. `python -m unittest tests.test_ensemble_vs_market tests.test_deb_gaussian tests.test_v3_core tests.test_scheduler tests.test_polywx_contract` passed, 198 tests OK. `git diff --check` passed with only Windows line-ending warnings.
- Conclusion: the walk-forward data path now exists and is auditable, but it exposed a real blocker: Beijing 2026-07-05 34C bucket had market mid 0.9965 while archived Previous Runs sample probability was 0.0. That means the old `model_prob >= 0.85` sanity cannot be honestly claimed yet; it is a calibration/model-source mismatch to solve before production paper scoring.
- Next: run previous-runs ingestion across the full 341-bucket set, compare by city/model/lead-time, then decide whether CMA/JMA/GFS ensemble weighting, station truth, or bucket/date alignment is causing the large Beijing mismatch. Keep scheduler and live trading off unless explicitly requested.
- Commit: not committed in this turn.

### 2026-07-06：强制减法 + DEB/hourly 口径审计修正

- 目标：停止加功能，只做减法、bug fix、审计和逻辑落纸；不新增 UI 组件、后端 endpoint 或 collector；继续以 PolyWX 为 benchmark，承认当前看板仍不能用于真实自动赚钱。
- 改动：`frontend/src/App.tsx` 将顶部状态条收缩为 Forecast / METAR / Historical / Last refresh 四个 pill，并把 production refresh 进度隐藏到 `?debug=1`；推荐关注卡压缩为城市状态、当前温度到 DEB、bucket/edge、Polymarket 链接四行，gate 细节进 tooltip；移除顶部 auto paper/live locked 等噪声。`frontend/src/components/WeatherPanel.tsx` 删除 Forecast snapshot cards、Schema notes、DEB metadata、WeatherBot 盘口/token/gate 附加说明、额外指标说明和四个白色 metric boxes；Bucket grid 改为两行核心信息，bid/ask 进 tooltip。
- 改动：`weatherbot_v3/metar.py`、`weatherbot_v3/scheduler.py`、`weatherbot_v3/cli.py` 把 recent METAR 默认窗口从 6 小时改成 24 小时，避免打开当天页面时只有少数白线点。`weatherbot_v3/hourly.py` 改为每小时按 `report_time` 选择最新观测，保留 METAR / China Live / PWS / Historical 独立来源，不再用小时内最大值冒充当前观测；无 METAR 但有 mesonet 时 primary source 标记为 `mesonet_other`，不插值、不伪造 METAR。
- 改动：`weatherbot_v3/deb.py` 的 DEB μ 下限改为 `observed_max_so_far - 0.5°C`，同时覆盖 ensemble 和 fallback 路径；新增 Tokyo observed-floor 回归测试，并调整 Chicago 回归预期。新增 `docs/IMPLEMENTATION_LOGIC_CN.md` 落纸当前 L0-L9 实现路径、数据边界、算法口径、执行边界和未完成项。
- 审计：本地新增 `audits/deb_audit_2026-07-05.md` 与 `audits/hourly_gaps_audit_2026-07-05.md`（不提交），记录 DEB 数据流、Tokyo 2026-07-05 Gamma/Polymarket resolved bucket 26C 对照、当前 DB 无足够本地 replay 样本、以及 hourly/METAR 缺口与修复方案。
- 验证：`python -m unittest tests.test_polywx_contract` 通过 13 tests OK；`npm run build` 通过，仍有既有 Browserslist/chunk warning；`python -m unittest discover tests -v` 通过 212 tests OK；`git diff --check` 待提交前复跑。
- 结论：本轮显著减少了看板肥胖和内部解释噪声，也修正了 METAR 窗口、小时观测聚合和 DEB observed floor 这三个会直接影响预测读数的问题。但当前模型仍未证明盈利，PolyWX 字段级对齐和 DEB 数值 benchmark 仍未完成，live 继续锁定。
- 下一步：用 PolyWX Chicago/Tokyo/Shanghai 的真实截图/DOM/XHR 做字段级 benchmark，优先核对 Hourly 曲线、DEB μ/σ、peak hour、METAR 派生字段和 Probability buckets；不要再扩新功能，先把现有链路跑准。
- 相关提交：待提交。

### 2026-07-06：PolyWX 对齐减法 + METAR 派生字段修正

- 目标：继续执行 PolyWX 对齐整改方案，优先删掉日常看板里的调试噪声，并修复 raw METAR 已正确但 UI `vis / wx / cloud` 显示错误的问题；不新增 endpoint、collector 或交易路径。
- 改动：`frontend/src/App.tsx` 删除中间主板 `Delta Audit` 入口，删除顶部“刷新当前城市”，推荐关注移到城市标题上方并压缩为城市/现在温度/预计最高温；城市标题下删除 `METAR --`、`证据 F/H`、模块计数、信号计数；顶部 poller 状态改中文短标签，失败次数只保留在 tooltip。
- 改动：`frontend/src/components/WeatherPanel.tsx` 修复前一天/后一天/今天按日历日切换，Hourly 图例改为中文 PolyWX 风格；云量只使用真实 `cloud_cover` 字段，不再用 humidity 兜底；无真实 China Live/PWS 数据时不画浮点；Forecast/METAR/Historical/China Live/PWS 继续独立成多条 series。
- 改动：`weatherbot_v3/hourly.py` 将 METAR `visibility`、天气现象 token（如 `-RA`）、云层覆盖百分比从 `metar_reports.raw_text/cloud_layers_json` 映射进 observation payload 和 `hourly_consensus_points`，让 METAR 表和 Hourly 图读解析产物而不是空值/JSON 字符串。
- 验证：`python -m unittest tests.test_polywx_contract -v` 13 tests OK；`python -m unittest tests.test_v3_core -v` 168 tests OK；`npm run build` 通过；`git diff --check` 通过，仅 Windows LF/CRLF warning。`/api/dashboard` 返回 `weather_city_series=26`、`recommendations=0`、`scheduler=true`。
- Firecrawl 对照：`firecrawl_interact` 成功读取 PolyWX Shanghai 2026-07-06 动态页面，确认其主路径为推荐关注、Forecast/METAR/Historical/China Weather Live 状态徽章、日期控制、Forecast/METAR/Historical/Diff Stats/Fetch Log 五 tab、Hourly chart、DEB 和 Probability buckets。本轮只对齐主路径，不照搬会员/反馈等非交易模块。
- 结论：本轮解决了“看板肥胖”和“raw METAR 对但派生字段错”的一部分核心问题；数据层仍需继续核对 Forecast/China Live/Cloud 与 PolyWX 的真实源差异，推荐为 0 仍可能由 gate 和市场匹配导致。live 仍锁定。
- 下一步：重启后端和 Vite 后人工核验 `shanghai-zspd&date=2026-07-06` 与 `chicago-kord` 页面；下一轮只做 Forecast/China Live 数值对齐和推荐 gate 诊断，不再加新模块。
- 相关提交：待提交。

### 2026-07-07：调度器刷新链路 + 云量口径修正

- 目标：修复用户实测“启动调度器后数据停留在旧小时”和“云量与 PolyWX 不一致”的两个问题；不新增 collector、不解锁 live、不改交易执行路径。
- 改动：`weatherbot_v3/scheduler.py` 将 METAR poller 的成功判定改为核心 METAR 增量抓取成功即可，PWS 失败只记录 `optional_warnings`，不再触发核心 poller 失败退避；`frontend/src/App.tsx` 在 scheduler poller 出现新的 `last_run_at` 后主动 invalidate dashboard、market buckets、signal decisions、daily max predictions 与 model reprice 查询，避免前端缓存停留在上一轮数据。
- 改动：`weatherbot_v3/hourly.py` 在 `hourly_consensus_points` 中新增 `forecast_cloud_cover`，从 `raw_json.forecast.cloud_cover` 读取；`frontend/src/components/WeatherPanel.tsx` 的 Hourly 主图云量面积图改用 `forecast_cloud_cover`，而 METAR 表继续使用 METAR 云层解析后的 `cloud_cover`；历史 diff fallback 不再把 `humidity_mean` 塞进 cloud 字段。
- 验证：`python -m unittest tests.test_polywx_contract tests.test_scheduler` 20 tests OK；`python -m unittest tests.test_v3_core` 168 tests OK；`npm run build` 通过；`git diff --check` 通过（仅 Windows LF/CRLF warning）。`/api/hourly-consensus?city=shanghai&target_date=2026-07-06` 已返回 `forecast_cloud_cover`；浏览器打开 `http://127.0.0.1:5173/?city=shanghai-zspd&date=2026-07-06` 无 console error，页面无 Delta Audit/刷新当前城市，调度器按钮可启动并已手动停止。
- 结论：本轮解决的是“调度完成后前端不刷新”和“主图云量取错合成字段”的工程问题。昨天日志显示 scheduler 实际跑到北京时间约 17:20，因此旧页面停在旧数据更可能由前端缓存与部分小时共识未刷新造成；若后续仍停在某小时，需要继续按 city/date 查 collector 入库与 derive_poller 输出。
- 下一步：人工启动调度器跑 10-30 分钟，观察顶部 poller age、`/api/scheduler/status`、`/api/hourly-consensus` 是否同步更新；随后继续对齐 Forecast/China Live 与 PolyWX 的数值来源差异。实盘继续锁定。
- 相关提交：本轮提交已完成，最终 hash 见本轮回复。

### 2026-07-10: Layer 1 verification reconciliation + source health matrix

- 目标：先修复 `verification_status` 与 `settlement_rule_verified_at` 倒挂，再建立全数据源实时健康矩阵；不改 UI、不解锁 live。
- 改动：`stations.py` 在 registry upsert 前后执行证据一致性修复，`verified_at` 会保护已核验规则；若旧 sync 覆盖了规则，则从 `data_fetch_logs.stage=settlement_rule_probe` 恢复规则原文、结算站、来源和 probe 快照。新增 `source_health.py`，覆盖 settlement contracts、METAR、Open-Meteo、Weather.com v3、China Live、PWS、WU hourly/daily、IEM、HKO、orderbook、hourly consensus、signal decisions；接入 CLI `source-health`、`GET /api/source-health` 和 scheduler compact status。
- 真实修复：Atlanta/Chicago/Dallas/NYC/Shanghai/Tokyo 从 provisional 恢复为 verified；Hong Kong 恢复为 settlement_mismatch（VHHH observation vs HKO settlement）；7 条规则原文和来源均从 probe 日志恢复，无不一致残留。
- 验证：`tests.test_v3_core` 177/177 通过；`tests.test_scheduler tests.test_polywx_contract` 20/20 通过；定向 scheduler health 契约通过；`git diff --check` 仅有 Windows 换行提示。
- 运行态：后端 `127.0.0.1:8765` 已显式启动 scheduler 做 24 小时 soak。首轮已恢复 Open-Meteo、orderbook、hourly consensus；China Live 有 1 城失败；Weather.com/PWS/truth coverage 仍不完整。`LIVE_TRADING=false` 未变。
- Soak 发现：Hong Kong HKO 成功，Shanghai China Live 返回 HTTP 502；当前 Weather.com key 调 PWS v2 返回 HTTP 401，说明后续解除 US-only 前需要有效 WU PWS API 权限。异常 URL 曾把 apiKey 带入本地 fetch logs，已新增递归脱敏并清理 24 条历史记录，复查明文命中为 0。
- 下一步：持续采样健康矩阵并诊断 China Live/长 poller；随后扩 WU/HKO 至 30 天并计算 WU-IEM、HKO-VHHH delta。
- 相关提交：`d58ae12`、`8eda988`。

### 2026-07-07: PolyWX-aligned source role contract + weather.com/WU probe

- Target: align WeatherBot data-source roles with the PolyWX/weather.com/WU/METAR/PWS interpretation, without importing PolyWX display values as trading truth or unlocking live execution.
- Changes: added `.env` fallback reader `weatherbot_v3/env_utils.py`; added `weatherbot_v3/weathercom.py` and `weathercom-fetch` CLI for `forecast_runs.source=weathercom_v3_forecast`; wired optional weather.com v3 forecast into scheduler forecast poller; added `WEATHER_COM_FORECAST_ENABLED`, `PWS_PEAK_LOCK_ENABLED`, and `DEB_WEIGHT_MODE`; extended ensemble DEB with `polywx_aligned_deb_v1`, source `role`, `weight_prior`, `weight_after_mae`, `mae_7d`, `truth_basis`, and `missing_weathercom_v3` warnings; added PWS peak-lock evidence to DEB as display/trend evidence only; added PolyWX-style DEB per-source weight table to the dashboard.
- Probe results: current `.env` key can fetch Wunderground PWS current observations, but weather.com v3 forecast returns HTTP 401 and WU/weather.com daily history for airport ICAO still fails (`no_daily_high_in_payload` / HTTP 401). WU truth dry-run attempts are now redacted (`apiKey=***`).
- Verification: `python -m unittest tests.test_v3_core tests.test_polywx_contract tests.test_ensemble_vs_market` passed 192 tests; `npm run build` passed; `git diff --check` passed with only Windows line-ending warnings. Audit updated at `audits/polywx-source-alignment-2026-07-07/README.md`.
- Conclusion: source-role plumbing is in place, PWS current is usable for peak-lock evidence, but weather.com v3 forecast and WU daily truth still require proper API permission. Live remains locked.
- Next: either obtain weather.com forecast / WU daily-history permission, or keep Open-Meteo as the honest WeatherBot forecast source and label the UI accordingly.

### 2026-07-09: WU hourly history 入库 + Weather.com v3 forecast 接入 scheduler/DEB

- 目标：先落地两个 PolyWX 数据源对齐任务：WU/weather.com hourly history 进入本地库并驱动 Historical 线；Weather.com v3 forecast 进入 scheduler/production-refresh 与 DEB 权重组件。
- 改动：新增 `truth_wunderground_hourly` 表与索引；新增 `wunderground-hourly-fetch` CLI；`truth/wunderground.py` 增加 hourly history fetch/parse/persist；`hourly.py` 优先使用 `wunderground_history` 作为 Historical；`production-refresh-v2` 增加 `weathercom_fetch` 阶段，scheduler forecast poller 继续写入 `weathercom_v3_forecast`；测试覆盖 WU hourly -> Historical 线与 production refresh 阶段。
- 验证：Weather.com v3 Shanghai dry-run `planned_runs=2`；WU ZSPD 2026-07-06 hourly `row_count=48/high_c=36.0/low_c=26.0`；本地入库后 `historical_points=24`；`DEB_WEIGHT_MODE=polywx_aligned` 下 DEB `has_weathercom_v3=True`；`python -m unittest tests.test_v3_core` 173/173 OK；`git diff --check` OK（仅 CRLF warning）。
- 结论：WU hourly history 已能入库并驱动 Historical 线；Weather.com v3 forecast 已能由 scheduler/production-refresh 写入并参与 PolyWX-aligned DEB。实盘仍锁定。
- 阻塞：WU/HKO truth 覆盖仍不足；Weather.com/WU key 权限与稳定性需要持续监控；尚未做 10 城批量 WU hourly/daily backfill 和 PolyWX 数值回归。
- 下一步：批量回填 10 城 30-90 天 WU hourly/daily，重建 hourly consensus/DEB，并做 PolyWX benchmark diff。
- 相关提交：e400d25。

### 2026-07-09: WU daily settlement truth 本地日聚合 + 10 城 7 天批量回填

- 目标：继续补齐 WU daily settlement truth，不再依赖单独 daily endpoint；优先复用已跑通的 WU/weather.com hourly history，按每个城市本地日历日聚合 daily high/low，并保留小时明细供审计。
- 改动：`fetch_wunderground_daily_result()` 新增 `timezone_name` 与 hourly fallback；`weather_com_location_historical_json` 成功时优先按 local-day hourly rows 计算 `truth_wunderground_daily`，并附带 `hourly_result` 供 CLI 同步写入 `truth_wunderground_hourly`；`wunderground-truth-fetch` 默认跳过已有 daily truth，除非显式 `--force-rebuild`，避免批量回填重复请求；CLI 输出改为摘要，不再打印 raw observations 巨型 JSON。
- 跑数：先备份 DB 到 `data/weatherbot_v3.db.bak-wu-daily-20260709-174049`；单日 Shanghai/ZSPD 2026-07-06 dry-run 返回 `high_c=36.0/low_c=26.0/hourly_row_count=48`，接口耗时约 1.66s、整条 CLI 约 3.63s；10 城 x 3 天首次入库 `stored=30/hourly_rows_upserted=1186/skipped=0`，重复同窗口全 cached 约 2.01s。
- 跑数：扩展 10 城 x 7 天（2026-07-01..07），结果 `stored=70/hourly_rows_upserted=1609/skipped=0`，总耗时 113.12s；DB 汇总确认 KORD/RCSS/RJTT/RKSI/WSSS/ZBAA/ZGSZ/ZHHH/ZSPD/ZSQD 均有 7 天 daily truth，hourly rows 按站点 168-351 行不等。
- 验证：新增本地日过滤回归测试，确认 UTC 上一日本地非目标日的极端温度不会污染 target_date；`python -m unittest tests.test_v3_core` 174/174 OK；`git diff --check` OK（仅 Windows CRLF warning）。
- 结论：WU daily truth 已从“接口探测可用”进入“可批量入库、可断点续跑、可本地日聚合”的状态；当前仅完成 7 天样本，仍不足以解锁 live，只能支撑后续 bias/DEB/PolyWX benchmark。
- 下一步：扩展到 30-90 天；重建 hourly consensus 和 daily_max_predictions；对 PolyWX 保存基准做字段级 diff；Hong Kong 仍走 HKO truth，不应纳入 WU 批量。
- 相关提交：790714f。

### 2026-07-10: Scheduler bounded execution and batch persistence

- Goal: finish the interrupted scheduler stabilization work before extending truth coverage. The short soak had shown multi-hour pollers caused by repeated schema initialization on a roughly 3 GB SQLite database and hundreds of sequential CLOB orderbook calls.
- Changes: added shared-transaction batch writes for METAR, forecast runs/members, market buckets, generic orderbooks, Gamma events/markets/orderbooks; switched CLOB orderbooks to official `POST /books` batches of at most 500 tokens; added city timeouts and explicit poller-specific logs; staggered first runs; preserved an explicitly empty watchlist; changed Derive to refresh all cities once per target date before per-city hourly/DEB/decision work.
- Runtime evidence: Chicago METAR fell from about 59s to 2.2s in isolation. Full METAR completed 14/14 in 89.8s. Full Forecast completed 14/14 in 362.9s. Gamma persisted 30 events, 330 markets, and 330 books in 9.6s (scheduler 11.8-14.1s). Isolated Derive completed 14/14 in 572.1s, below its 900s interval. One Seoul AWC read timed out during the second METAR cycle and was recorded without blocking other cities.
- Verification: `python -m unittest tests.test_v3_core tests.test_scheduler tests.test_watchlist_enabled tests.test_polywx_contract` passed 205 tests; `npm run build` passed; `git diff --check` passed. Live and auto simulation remained disabled.
- Conclusion: the previous indefinite-running symptom is fixed at the known request/write bottlenecks, and each core poller now has bounded behavior and measured capacity. A real 24-hour uninterrupted soak is still required; this turn does not claim production readiness.
- Next: run the committed scheduler for 24 hours, then extend WU/HKO truth to 30 days and compute source deltas. Do not start UI work or live canary work first.
- Commit: `9f1a04f`.

### 2026-07-10: Keep signal-decision orderbooks fresh during long Derive runs

- Finding: the first complete Derive cycle finished 14/14 cities in 741.6s, but source health still marked `polymarket_orderbook` stale. This was real, not a cache error: Derive fetched quotes at its start, then spent over 12 minutes computing, while the five-minute Gamma poller updated only `polymarket_orderbook` and not the `market_buckets/orderbooks` tables read by signal decisions.
- Fix: `gamma_orderbook_poller` now refreshes both structured Gamma audit tables and all enabled-city D+0/D+1 active market buckets/orderbooks every five minutes. Derive no longer fetches markets; it consumes the pre-refreshed rows and validates that each decision target has buckets.
- Runtime evidence: a real combined Gamma run completed in 41.4s and persisted 30 events, 330 structured books, 308 active market buckets, and 308 active orderbooks with zero failures. Source health immediately changed orderbook coverage to 100% healthy with roughly 26-36s quote age.
- Verification: 206 backend/scheduler/watchlist/dashboard-contract tests passed; `npm run build` passed; `git diff --check` passed. Live and auto simulation remained disabled.
- Conclusion: the previous soak is not accepted because decisions could see stale quotes. The 24-hour clock must restart from this commit; remaining blockers are settlement verification, WU/HKO history depth, transient METAR errors, and optional PWS coverage.
- Commit: `d4c90f7`.

### 2026-07-10: Scheduler resilience after first one-hour soak sample

- Observation: the one-hour run completed eight METAR cycles, twelve China Live cycles, one Forecast cycle, eight Gamma cycles, and two Derive cycles. Forecast completed 14/14 in 317.4s. Derive completed 14/14 twice in 837.0s and 905.2s; the latter narrowly exceeded the 900s target under concurrent load.
- Findings: one Singapore AWC request timed out at 20s and recovered on later cycles. One Gamma structured sync reported ten Seoul `clob_batch_book_missing` rows even though active decision orderbooks were refreshed. PWS returned repeated HTTP 401 responses for the configured Weather.com key, which is a permission gap rather than a network outage.
- Changes: AWC current-METAR reads now retry once with bounded backoff. Gamma classifies missing individual structured books as `book_gaps`, preserves them for audit, and keeps the poller successful when active bucket refresh succeeds. Scheduler applies an in-memory one-hour PWS auth cooldown after a 401/unauthorized response so optional PWS no longer floods fetch logs.
- Verification: 209 backend/scheduler/watchlist/dashboard-contract tests passed; `git diff --check` passed. No execution behavior changed and live remains locked.
- Conclusion: restart the 24-hour soak from this commit. PWS still requires a dedicated Wunderground PWS entitlement before it can become a usable source; Seoul book gaps remain correctly non-tradable.
- Commit: `dc1cd8b`.

### 2026-07-11: Truth delta audit repair and six-hour scheduler gate

- 目标：不打断正在运行的 scheduler，先修正 HKO settlement truth 与 VHHH observation station 无法对账的 audit 缺口，并把 scheduler 验收拆为六小时预验收与二十四小时稳定性证据。
- 改动：`truth_delta_audit` 新增 `delta_hko_minus_iem`；delta rebuild 通过 `stations.station_id -> city_key -> settlement_station_id` 识别香港，而不再错误要求 IEM ICAO 为 `HKO`。审计行现写入 city key，summary 提供 HKO-vs-IEM histogram。`wunderground-truth-fetch --force-rebuild` 现正确透传。
- 验证：新增 VHHH=31.0°C / HKO=32.2°C 的回归，得出 `delta_hko_minus_iem=+1.2°C`；CLI force rebuild 回归通过；`python -m unittest tests.test_v3_core` 183/183 OK；新旧 DB schema 均可迁移。
- 结论：HKO-VHHH delta 已具备正确持久化与可复算能力，但当前生产 DB 的 delta 尚未重建，且 WU/HKO 仍未达到 30 天；没有运行外网回填、没有重启 scheduler、`LIVE_TRADING=false` 未变。
- 下一步：scheduler 连续运行至六小时后先读取 source health；通过后以限速、可断点批次补齐 WU/HKO/IEM 真值，再运行 `truth-delta-build`。
- 相关提交：`cc90a90`。

### 2026-07-11: IEM observation-station selection for Hong Kong truth delta

- 目标：为后续 HKO−VHHH 30 天 delta 回填修正 IEM truth collector 的站点口径，不触碰正在运行的 scheduler。
- 改动：`iem-asos-fetch` 现在始终用 `stations.station_id` 和 observation timezone；香港因此请求机场观测站 `VHHH`，而不会误取结算机构 `HKO` 后静默跳过。其他城市的 station/settlement station 一致，行为不变。
- 验证：新增香港 CLI 回归，确认请求参数为 `VHHH / Asia/Hong_Kong`；`python -m unittest tests.test_v3_core` 184/184 OK；`git diff --check` 通过。
- 结论：回填前置条件已齐备。仍需等 scheduler 六小时预验收结束后，再显式运行 WU/HKO/IEM 日期范围回填和 `truth-delta-build`。
- 下一步：六小时审计结果通过后，先做 3 天 Chicago/Hong Kong 冒烟，再做 30 天可缓存批次。
- 相关提交：`649e2d8`。
### 2026-07-11：Layer 8/9 模拟结算与评分生命周期

- 目标：把新一代模拟订单从“已成交/浮盈亏”推进到可审计的权威结算、已实现 PnL、胜率和 Brier score，实盘继续锁定。
- 改动：新增 `weatherbot_v3/paper_settlement.py` 与 `paper-settle` CLI；扩展 `settlements` 表、模拟账户摘要和受控 scheduler poller。WU/HKO/IEM 只形成 provisional truth，只有 Gamma 市场 `closed=true` 且 YES/NO 为终态 1/0 才关闭订单并兑现 PnL；重复执行按订单 settlement key 幂等。
- 验证：定向 settlement/scheduler 16 tests 通过；核心、scheduler、PolyWX contract、ensemble 合计 235 tests 全通过；真实库 dry-run 扫描 60 笔旧单，`candidates=0`、`legacy_skipped=60`、`resolved_total=0`，没有写入伪造盈亏；`git diff --check` 通过。
- 结论：今后完整身份链的 `paper-execution-v1` 订单已经能自动等待权威结算并产出 realized PnL、win rate、model/market Brier；旧 60 笔记录缺少 decision/token/city/date，明确保留但不可评分。当前仍没有已结算的新样本，因此不能声称策略盈利。
- 阻塞：PWS 权限仍为 HTTP 401；derive 周期偏长；Layer 9 尚需 14-30 天新订单和权威结算样本；live 保持锁定。
- 下一步：启动受控的新 v1 paper cohort，先验证订单生成、模拟成交、provisional truth、Gamma finalization 的连续链路，再进入 14-30 天统计验证。
- 相关提交：`4245bf4`。
### 2026-07-11：Layer 9 受控模拟验证 cohort 底座

- 目标：在 UI 验收前补齐 14-30 天内测所需的可审计运行容器，但默认不启动、不创建模拟订单。
- 改动：新增 `paper_validation_runs` 与 `weatherbot_v3/paper_validation.py`；cohort 仅消费启动后且不超过 30 分钟的新决策，默认只允许 `single_bucket_ev`，排除 monitor-only 城市，并限制 `$40` 本金、单笔 `$2`、每日 `$10`、最多 5 个未结算仓位和每日 5 单。订单新增 `cohort_run_id`；scheduler 新增 inactive-by-default 的 paper execution poller；CLI 支持 start/status/stop/tick，start/stop 必须显式 `--apply`。
- 验证：paper cohort/settlement/scheduler 19 tests 通过；core/PolyWX/ensemble 219 tests 通过；真实库 `paper-cohort-status` 返回 `inactive`；`git diff --check` 通过。一次合并测试因后台 derive 与 4.5GB SQLite 竞争超过 300 秒，停止 scheduler 后拆分复跑全部通过，不是测试失败。
- 结论：长期内测的风险预算、身份链、显式启停和结算消费者已经就绪，但遵照人工验收顺序没有启动 cohort，也没有产生新模拟仓位。旧版 auto simulation 继续关闭，live 继续锁定。
- 下一步：使用 Product Design audit、数据可视化和浏览器截图证据完成 Layer 7 UI 减法、组件拆分和 PolyWX 工作台对齐；人工验收通过后再启动 14-30 天 cohort。
- 相关提交：`d1876b5`。

### 2026-07-11：Layer 6 概率批次修复与 Layer 7 看板第一轮减法

- 目标：在启动 14-30 天模拟内测前，先修复上海整数摄氏度桶显示为全 0 的 P0 问题，并按 PolyWX 城市工作台做看板减法；不启动 cohort、不解锁 live。
- 改动：整数摄氏度 exact bucket 改为复用统一 truncation 边界，上海最新 ensemble 分布由 `sum_probability=0.0` 修复为 `1.0`；前端只消费最新 `issued_at` 决策批次，旧批次不再覆盖新概率。看板移除重复城市筛选、未启用城市、首屏内部规则标签、重复 Forecast Options 和旧版一键模拟，共净减 322 行；右侧新增只读 `paper-validation-v1` 状态卡，旧模拟入口不再误导操作员。`/api/dashboard` 不再每次同步重扫 4.5GB SQLite 计算推荐，热请求从约 2.3-4.8 秒降至约 0.6 秒。
- 验证：全套 `python -m unittest discover tests` 257/257 通过；`npm run build` 通过；`git diff --check` 通过。浏览器验证上海最新桶出现 `model 20.0% / 80.0%`，前一天/今天同步 URL，无横向溢出，浅色中间面板不再发黑，新标签页 console error=0；截图见 `audits/ui-audit-2026-07-11/`。
- 结论：Layer 7 已从“功能堆叠”进入可读的生产工作台方向，概率展示链已闭合；但 `WeatherPanel.tsx` 仍需继续拆分，DEB 下半区、表格密度和窄屏还未完成人工验收。cohort 仍为 `inactive`，scheduler 停止，`LIVE_TRADING=false`。
- 阻塞：PWS entitlement 仍为 HTTP 401；重启后 scheduler badge 只反映本进程状态而非持久化 source freshness；Cloud/Forecast/DEB 与保存的 PolyWX benchmark 仍有差异；尚无新 cohort 权威结算样本。
- 下一步：完成 Hourly/DEB/table 组件拆分和浏览器窄屏 QA，人工确认看板后才显式启动 14-30 天 cohort。
- 相关提交：概率修复 `e978b73`；看板减法 `110bfc2`。

### 2026-07-11：Layer 7 Hourly 图组件拆分与响应式验收

- 目标：继续收敛生产看板代码边界，先把 Hourly Temperature 图从超大的 `WeatherPanel.tsx` 中拆出，并完成桌面与窄屏浏览器验收；不启动 scheduler、paper cohort 或 live。
- 改动：新增 `frontend/src/components/HourlyTemperatureChart.tsx`，集中管理多源折线、云量、峰值线、24 小时刻度和三行差异统计；`WeatherPanel.tsx` 只保留数据标准化与面板编排；PolyWX 契约测试同步覆盖拆分后的组件。
- 验证：`python -m unittest discover tests` 257/257 通过；`npm run build` 通过；桌面 1280px 与窄屏 768px 均无横向溢出，浏览器 console error=0，Hourly 图、峰值线、刻度与统计行未发生遮挡。
- 结论：Hourly 展示已形成可独立维护的组件边界，且拆分没有改变数据、策略或执行语义。`WeatherPanel.tsx` 仍然偏大，DEB 概率分布和下方明细表仍需继续拆分并由操作员最终验收。
- 阻塞：PWS entitlement 仍为 HTTP 401；Cloud/Forecast/DEB 与保存的 PolyWX benchmark 仍有差异；14-30 天 paper cohort 仍为 inactive，`LIVE_TRADING=false`。
- 下一步：先完成剩余 Layer 连接和数据口径整改，再用 Product Design、数据可视化和浏览器证据完成 DEB/table 与整个工作台 UI 收尾；只有人工验收后才启动 14-30 天模拟内测。

### 2026-07-11：Layer 1 核验状态不变量与 source-health-v2

- 目标：回到生产化顺序的第 1、2 步，确保 `verification_status` 与 `settlement_rule_verified_at` 双向一致，并把按源汇总扩展成 14 城逐源健康矩阵。
- 改动：reconciliation 现在会清理无有效合约证据的 verified timestamp、降级缺 timestamp 的 terminal status，并可从真实 probe 日志恢复 timestamp；`source-health-v2` 新增 `city_matrix`、逐城样本数、age、history days、live eligibility 与 Hong Kong paper-only 标记。
- 验证：生产库 26 站检查 `repaired=0/inconsistent=0`；14 个启用城市为 13 verified + Hong Kong settlement_mismatch。Core/scheduler/dashboard contract 共 226 tests 通过，`git diff --check` 通过。
- 运行证据：后端重启后 `/api/source-health` 返回 v2 与 14 个 city rows；scheduler 于 `2026-07-11T08:17:11.679287Z` 启动，首轮 METAR 14/14（56.989s）、China Live 2/2（44.249s）成功，Forecast 与 Orderbook 首轮正在运行。
- 结论：状态倒挂不再只被报告而会被确定性修复；每个城市缺哪个源、是 missing/stale/degraded/healthy 现可直接读取。PWS 仍是可选 entitlement 缺口，Hong Kong 仍正确 paper-only，live 与 paper cohort 均未开启。
- 下一步：连续观察 24 小时并按 v2 矩阵验收各 poller；通过后进入保存 PolyWX 日期的 Forecast/Cloud/DEB 重建与字段级差异分析。
- 相关提交：`25d2396`。

### 2026-07-11：Layer 2 PWS 亚洲范围解锁与真实权限探测

- 目标：核对“亚洲 PWS 缺失”究竟是代码范围限制还是 API 权限问题，同时不重启正在进行的 24 小时 scheduler soak。
- 改动：移除 `pws.py` 的 `profile.region != us` 跳过和 US-only selector；所有 registry 城市均可用坐标执行 PWS discovery。Discovery 404 现在诚实记录为可选源无覆盖，不再制造 hard failure；401 仍保留为 entitlement 错误。
- 验证：fixture 覆盖上海 station discovery、current observation 解析和 404 no-coverage；3 项定向测试通过。真实 dry-run 显示 Tokyo/Seoul/Taipei/Hong Kong/Singapore 均发现具体 PWS ID，但 current endpoint 返回 401；Shanghai/Beijing discovery 返回 404。
- 结论：US-only 技术阻塞已解除。亚洲 PWS 的剩余阻塞是外部 API entitlement；中国大陆坐标还存在公开 PWS discovery 无覆盖。此源继续保持 display/peak-lock only，不影响 METAR、truth 或 live gate。
- 下一步：24 小时 soak 结束并重启后端后启用新代码；若取得支持 PWS current 的 Weather.com/WU key，可直接复跑亚洲 dry-run，无需再改 collector。
- 相关提交：`eb1f21f`。
### 2026-07-11：PolyWX 数据源职责闭环与原始多频率看板

- 改动：上海 China Live 切到浦东 `101020600`；调度器拆为 METAR、China Live、Weather.com v3、Open-Meteo NWP、WU Historical、PWS 与 derive 独立 poller；PWS 只接受独立 `WUNDERGROUND_API_KEY`。Forecast/Cloud 统一读取同一 v3 快照，WU/METAR/China Live/PWS 以原始频率独立返回；DEB 默认 `polywx_aligned` 并修复整点截断遗漏最新 v3 的问题。
- 展示：上海与 Chicago 均显示 24 行 v3 Forecast、同快照 Cloud/天气状况/修订次数、当日 WU Historical 和含 v3 的 DEB；主图六个图例支持点击隐藏/恢复曲线，PWS 无权限时显示诚实禁用态。
- 验证：真实上海冒烟得到 Forecast 24、METAR 41、WU Historical 38、浦东 China Live 7、PWS 0；Chicago v3 Forecast 24 行。浏览器两城无 console error、无横向溢出；全套 `python -m unittest discover tests` 263/263 OK；`npm run build` OK；`git diff --check` 仅 Windows 换行提示。
- 结论：采集、派生与主图展示职责已经闭合，PWS 仍因独立产品 entitlement 缺失不可用；2 小时与 6 小时连续调度验收尚未完成，不能据此宣称生产稳定或盈利。
- 下一步：基于提交 `84ab4f0` 启动 2 小时 scheduler smoke，检查全部启用城市的源新鲜度、WU 当日增量、无重复 PWS 401；通过后再做 6 小时稳定性验证与 PolyWX 数值 benchmark。
- 启动前修复：首次 scheduler 启动暴露 `/api/scheduler/status` 同步扫描 4GB SQLite、阻塞事件循环超过 30 秒的 P0。提交 `446cc22` 将 status 改为纯内存读取，`/api/source-health` 改在线程中计算并回填缓存；并发实测 source-health 运行时 status 仍可在约 182ms 返回，热请求约 6ms。
- 继续修复：提交 `a4b8385` 将 scheduler 的 registry 读取和 per-city/overall fetch log 写入全部移出事件循环；提交 `f7de6c1` 删除 Weather.com v3 内层错误的 40 秒硬超时。首轮取证显示 WU Historical 13/13、NWP 14/14，PWS 无 key 时无 401；旧 forecast 3 个 timeout 由该 40 秒限制造成。
- 当前验证：唯一有效的连续 scheduler 起点为 `2026-07-11T12:56:56.701552+00:00`（北京时间 20:56:56）。启动 20 秒后 status 在 forecast 运行中仍于 49ms 返回；此前所有起点均作废。

### 2026-07-11：Layer 4/6 派生批次性能修复

- 改动：`run_hourly_consensus_build`、`run_daily_max_build` 与 `run_signal_decisions_build` 的人工 CLI 默认行为不变；scheduler 批量派生时传入 `refresh_readiness=false`，只在 14 城批次结束后统一刷新一次 readiness。
- 验证：旧真实批次约 23 分钟，并在后续轮次触发单城市 300 秒超时。修复后真实 14 城 D+0/D+1 派生 `14/14` 成功，耗时 `324.52s`、无超时；Chicago 单日期从约 `60.7s` 降至 `14.6s`。全套测试 `264/264` 通过，`npm run build` 与 `git diff --check` 通过。
- 结论：根因是针对 4.9GB SQLite 的 readiness 全库扫描被按阶段/日期/城市重复执行。派生批次现已回到单周期内可控完成，但必须加载新后端后重启 2 小时与 6 小时验证时钟。
- 阻塞：独立 PWS entitlement、PolyWX 数值 benchmark、操作员 UI 验收、14-30 天权威模拟结算样本仍未完成；live 保持锁定，paper cohort 保持 inactive。
- 下一步：重启后端，启动新的受控 scheduler，确认 Forecast/NWP/Historical/Derived 首轮闭环，再进入 2 小时与 6 小时门槛。
- 相关提交：`1fe8a79`。
- 新验证起点：后端加载修复后，scheduler 于 `2026-07-11T13:36:49.608789+00:00`（北京时间 21:36:49）启动；2 小时与 6 小时门槛只从此时间计算，heartbeat 已同步更新。

### 2026-07-11：Layer 7 原始历史观测、偏差图与分钟时间轴修复

- 改动：撤销尚未到阶段的 2/6 小时长跑并停止 scheduler；`历史观测` Tab 改为直接消费 `truth_wunderground_hourly` 原生 30/60 分钟序列，METAR Tab 直接消费原始报文序列，不再用 24 行 hourly consensus 冒充。主图 X 轴改为本地日分钟 `0-1439`，中国实况 5-10 分钟点、WU 30 分钟点、METAR 与整点预报按真实时间定位；温度 Y 轴按可见温度范围自适应并保留最小边距，云量继续使用独立 0-100% 右轴。偏差统计新增正负残差柱和累计均值线，可切换 METAR/历史观测。
- 数据修复：WU parser 补 `dewPt`；AWC `6+SM` 按城市制式显示；美国 WU 的 km/kph/hPa 在 API 展示层转换为 mi/mph/inHg。香港小时历史改用 VHHH + HK country code 作为 display-only evidence，HKO Daily Extract 仍是唯一结算 truth。
- 验证：显式增量抓取得到 Shanghai WU 44 行、Chicago 9 行、Hong Kong 46 行；上海 WU 首行 `28°C / 75% / 9.0km / 1006hPa / dew 27°C` 与保存的 PolyWX 样本一致；Chicago 首行换算为约 `68°F / 75% / 10mi / 7mph / 29inHg / dew 62.6°F`。全套测试 267/267、前端 build 通过；浏览器温度左轴自适应为 26-32°C、云量右轴保持 0-100%，console error/warn 为 0。
- 结论：历史观测“空模块”和历史曲线缺失已修复；中国实况此前挤在右侧同时包含分类轴错误与真实留档从 18:20 才开始两层原因。时间轴错误已修，缺失的早间 China Live 不用 WU/Forecast 伪造回填。
- 阻塞/下一步：PWS entitlement 仍缺；Forecast/Cloud/DEB 仍需继续做同日字段级 PolyWX benchmark。完成 UI 人工验收前不恢复 scheduler 长跑、不启动 paper cohort，`LIVE_TRADING=false`。

### 2026-07-12：Forecast 字段闭环、JMA 入模与看板证据状态修复

- 改动：`weatherbot_v3/hourly.py` 将 Weather.com v3 的天气现象、降水概率、修订次数和抓取时间完整保留到 Layer 4 raw payload 与 `/api/hourly-consensus`；`weatherbot_v3/openmeteo.py` 将 `jma_seamless` 纳入中国城市模型 allowlist；`WeatherPanel.tsx` 改为用当前日期原生 Forecast/METAR/WU Historical series 判断证据状态，并用 DEB `observed_floor` + 实际 METAR 行数展示当日已观测最高温。
- 验证：真实刷新 Shanghai/Chicago Weather.com、METAR 和 WU Historical；Shanghai 得到 Forecast 24、METAR 8、WU Historical 7、China Live 1。Shanghai DEB 为 `30.10+/-1.51C`，包含 v3/GFS/JMA/ECMWF/ICON/GEM 六家；同时间 PolyWX 样本约 `29.88+/-1.62C`，差 `0.22C/0.11C`。Forecast 前三小时温度 `27/27/27C`、云量 `100/99/99%`，字段来源均为同一 Weather.com v3 snapshot。
- UI：浏览器确认 China Live `03:40` 按分钟定位，没有挤到右端；温度 Y 轴自适应约 `26-31C`；METAR/WU 徽章按真实 series 变绿；DEB 显示 `实测 27.00C (metar, 8 样本)`；Forecast 表显示 condition、precip chance、revision 与 fetched time；console error/warn 为 0。
- 结论：上海 Forecast/Cloud/DEB 已达到本轮 PolyWX 数值对照目标，且不复制 PolyWX 运行值。Chicago 7 月 12 日 WU 请求的 HTTP 400 是当地仍处 7 月 11 日、目标日尚未成为历史日，不是美洲数据源断开。scheduler 保持停止，paper cohort inactive，live 继续锁定。
- 阻塞/下一步：PWS entitlement 仍缺；Chicago 需在当地 7 月 12 日开始后完成 WU Historical 与数值 benchmark；随后做运营者 UI 验收，再决定是否启动 14-30 天模拟 cohort。

### 2026-07-12：三城 PolyWX 横向对照与悬浮日期修复

- 改动：对上海 ZSPD、东京 RJTT、Chicago KORD 做当前动态 PolyWX 与本地同日字段级对照；`HourlyTemperatureChart.tsx` 新增 tooltip 日期格式化，混合频率数字分钟轴不再向用户显示 `660/1203`，而是显示 `YYYY/MM/DD HH:mm`。
- 验证：三城 Weather.com v3 与 AWC METAR 受控刷新成功；WU 当日历史在上海、东京成功，Chicago 因当地目标日尚未开始诚实返回 HTTP 400。浏览器悬浮实测显示 `2026/07/12 12:00`，无 console error。
- 结论：上海和 Chicago 的 Forecast/Cloud 与 PolyWX 接近；东京过去小时 Forecast 仍比 PolyWX 低约 2C，但 METAR/Historical 抽样一致，因此数值口径尚未完全闭环。WeatherBot Forecast/Historical 默认 30 分钟，PolyWX 抽样约 10-13 分钟，刷新速度尚未等价。
- 阻塞：需追踪东京 forecast archive 选取逻辑；PWS entitlement 仍缺；活动城市是否改为分层 10 分钟轮询需先评估 API 配额与写放大。scheduler 保持停止，paper cohort inactive，`LIVE_TRADING=false`。
- 下一步：先修东京过去小时 Forecast 快照选择，再决定只对活跃市场城市缩短 Forecast/WU Historical 周期，避免 14 城无差别高频写入。

### 2026-07-12：Tokyo/RJTT 坐标、预测位置契约与 DEB 冷启动修复

- 改动：依据 AWC stationinfo 将 Tokyo/RJTT 从错误坐标 `35.7647,140.3864` 修正为羽田 `35.553,139.781`；注册表新增 `location_version`。Hourly、ensemble DEB 与 bias 训练会排除 `source_url` 明示 geocode/latitude/longitude 与当前结算站不一致的 forecast run。
- 数据安全：旧 Tokyo 预测行保留审计但不再进入展示/DEB；旧 bias 表属于 location v1，不再应用到 location v2。成熟残差暂缺时 σ 使用 `1.2C` 保守冷启动误差并记录 `uncalibrated_sigma_default`，避免样本不足时虚假收窄。
- 验证：重新抓取羽田坐标 Weather.com v3 与六个 Open-Meteo 模型。修复后 Tokyo DEB `28.97+/-1.39C`，同时间 PolyWX `28.90+/-1.37C`；μ 差 `0.07C`、σ 差 `0.02C`。错误地点的过去小时不再显示为 24C，而是诚实留空。
- 结论：Tokyo 的 2C 分裂根因是 station ID 正确但坐标错误，并叠加旧坐标 bias。该 P0 已闭环；从修复时点起正确 forecast archive 会逐小时积累。
- 验收：全套 `270/270` Python tests、前端 production build 与 `git diff --check` 通过；新后端加载后，浏览器确认 Tokyo 错误 24C 过去时段消失、正确序列从 09:00 `27.8C` 开始、DEB `28.97+/-1.39C`，console 无 error。
- 下一步：评估仅对活跃市场城市启用 10 分钟 Forecast/WU Historical 调度，继续保持 scheduler 停止、paper cohort inactive、live 锁定。

### 2026-07-12：活跃市场 10 分钟固定周期刷新

- 改动：Forecast 与 WU Historical 默认周期由 30 分钟收敛为 10 分钟，并从“任务完成后再等待一个周期”改为固定 start-to-start 周期，避免 14 城 Forecast 的 4 分钟级执行耗时被重复叠加。新增分层刷新：有活跃市场的启用城市每轮刷新，其他启用城市每 3 轮刷新一次；每轮结果和 scheduler status 持久化 `refresh_scope`、活跃城市数、延后城市与 baseline 周期。
- 验证：新增周期、选城和状态时间测试；`tests.test_scheduler` 21/21 通过。真实受控 WU Historical 单轮完成 14/14 城、0 失败、耗时 `61.364s`，本轮 14 城均有 active market，因此无延后城市。全套 Python tests `275/275`、前端 production build 与 `git diff --check` 通过。
- 结论：Forecast/WU Historical 的调度目标已与 PolyWX 抽样观察到的约 10-13 分钟新鲜度对齐，且 future observation-only 城市不会被无差别高频抓取。常驻 scheduler 仍保持停止；本轮仅执行一次独立 Historical run，没有启动长跑。
- 阻塞：PWS entitlement 仍缺；正确地点的 Tokyo forecast archive 需从修复时点继续积累；Layer 7 仍需操作员 UI 验收和剩余数据字段对照；14-30 天 paper cohort 尚未启动，live 保持锁定。
- 下一步：重启后端加载新 scheduler 代码，在操作员确认看板后做短时受控运行观察；随后继续完成左/中 PolyWX 工作台细节和右侧策略模拟闭环。

### 2026-07-12：Layer 7 PolyWX 层级、悬浮时间与诚实新鲜度

- 改动：左侧城市列表压缩为站点索引，推荐关注改为横向紧凑卡；中间工作台收敛为单一数据源状态行、日期和五个 Tab，删除重复 scheduler 徽章、旧手动抓取提示和中间交易 gate。逐小时图 tooltip 使用专用组件强制显示 `日期 + HH:mm`，温度轴继续按有效温度自适应；高级诊断只在展开后请求。`/api/dashboard?city=` 只保留选中城市重证据，其他城市仅返回摘要。
- 性能：dashboard payload 由约 1.78MB 降到约 0.61MB；逐小时证据优先加载，DEB/桶/决策在 Hourly 完成后再加载。修复模拟开关同步等待完整 dashboard 重建导致测试和操作卡死的问题，改为原子更新缓存状态。
- 验证：全套 Python `280/280` 通过；前端 production build 通过；上海 1440x900 截图无横向溢出，中国实况按本地分钟落位，Y 轴为约 `25-31C`。上海本地 DEB `30.03+/-1.47C` 对 PolyWX `29.71+/-1.58C`；数据新鲜度因 scheduler 停止仍明显落后，UI 已如实显示 1.9-2.9 小时而非伪装成数分钟前。截图与字段表见 `audits/ui-qa-2026-07-12/three-city-benchmark.md`。
- 结论：本轮闭合了用户指出的内部分钟标签、纵轴挤压、China Live 横轴和错误新鲜度文案。上海 peak hour 本地 `16:00`、PolyWX 样本 `14:00` 仍不一致，需下一轮审计峰值计算；PWS entitlement 仍缺。scheduler 保持停止，paper cohort inactive，`LIVE_TRADING=false`。
- 下一步：先审计 peak-hour 的 forecast/observed blending 与 PolyWX 差异，再完成右侧策略模拟工作台的订单生命周期可视化；运营者验收后才启动短时调度和 14-30 天 cohort。
### 2026-07-12: PolyWX forecast-revision peak marker and Weather.com precision

- Layer: Layer 3/4 forecast archive with its direct Layer 7 chart consumer. No execution or live-trading behavior changed.
- Changes: reverse-engineered PolyWX `/api/peak-marker` and `/api/forecast-history`; added a separate 72-hour Weather.com revision peak marker with latest-hour tie handling; passed it through `/api/hourly-consensus` to the chart without changing DEB's mixed observed/forecast peak semantics. Weather.com now requests imperial payloads to preserve 1 F precision and normalizes temperature, dew point, wind, pressure and precipitation at ingestion. Added forecast lookup indexes to remove full-table scans.
- Verification: real browser hover shows `2026/07/12 13:00` instead of raw minute coordinates; Shanghai/Tokyo/Chicago were compared against live PolyWX APIs. Local hourly latency fell from 3.7-4.4s to 1.7-2.5s. `python -m unittest tests.test_v3_core tests.test_polywx_contract` passed 226/226; `npm run build` and `git diff --check` passed. Detailed untracked evidence is in `audits/polywx-alignment-2026-07-12/three-city-forecast-marker.md`.
- Conclusion: UI time formatting and marker computation contracts are closed. Real values are not yet fully equal because WeatherBot has fewer/staler persisted revisions: Shanghai peak 13:00 vs PolyWX 14:00, Tokyo 15:00 vs 16:00, Chicago 18:00/86F vs 17:00/87F. Scheduler remained stopped, paper validation inactive and `LIVE_TRADING=false`.
- Next: accumulate correct-location Tokyo revisions and repair source freshness before another controlled benchmark; then move to the right-side strategy simulation workbench as the next layer consumer.

### 2026-07-12: Layer 8 策略模拟交易台与原子 ladder 成交

- Layer：Layer 8 paper executor 及其直接的右侧工作台消费者；未修改 live 下单路径，\`LIVE_TRADING=false\`。
- 改动：右侧工作台不再用旧 \`/signals/{id}/status\` 伪装模拟买入，改为读取最新一批 \`signal_decisions\`，通过 \`/api/paper-orders/execute\` 做成交检查和模拟买入，并从 \`/api/paper-orders\` 展示真实订单、浮动盈亏和结算。策略队列支持 \`single_bucket_ev / ladder_grid / tail_buying\` 标签与 ladder 三腿合并展示。
- 执行安全：\`paper_orders\` 持久化 \`strategy_name/ladder_group_id\`；ladder 三腿必须全部满足深度与风控，三条订单和 fills 在同一个 SQLite 事务中落库，任一腿不可完整成交时整组零写入。实盘按钮与调用未进入新工作台。
- 验证：\`tests.test_execution_workbench_contract + tests.test_v3_core + tests.test_polywx_contract\` 共 230 tests 全部通过；\`npm run build\` 通过；浏览器上海页面显示最新批次 11 条策略、0 条可模拟、0 订单，阻塞策略按钮不可点击，订单页诚实空态，console error/warn 为 0。调度器保持 stopped，paper cohort inactive。
- 结论：右侧“看起来模拟但只改标签”的断链已关闭。当前上海没有通过 paper gate 的策略，因此没有制造演示订单；下一步应审计多城市 blocked reason 分布并做证据驱动的阈值评估，随后由操作员决定是否启动 14-30 天 cohort。

### 2026-07-12：Layer 8 操作员模拟控制与市场链接恢复

- 改动：右侧交易台恢复可见的模拟本金、单笔上限、入场策略组合与“一键模拟/停止”入口；该入口创建真实 `paper_validation_runs`，后续由 scheduler tick 按 Kelly 建议、日额度和持仓上限自动执行，并由既有 paper settlement poller 读取 Polymarket Gamma 结果结算。支持 `single_bucket_ev / ladder_grid / tail_buying` 组合；退出方式当前只开放 `hold_to_settlement`，信息差退出因尚无 SELL 成交与历史盘口回放而诚实锁定。
- 链路：`/api/paper-validation/start|stop|tick` 已接入；手动批量模拟会按选定策略过滤；`signal_decisions` 对旧记录动态补齐 `market_buckets.event_url`，Atlanta 抽样 120/120 条决策均有 Polymarket 事件链接。
- 图表：Atlanta 预报为本地整点，METAR/WU 为实际 `:52` 分钟；温度曲线改为逐点 `linear` 连接，避免 `monotone` 插值造成视觉时间偏移，Cloud 继续独立使用 0-100% 右轴。
- 验证：`tests.test_paper_validation + tests.test_polywx_contract` 19/19、`tests.test_v3_core` 214/214、前端 production build、`git diff --check` 全部通过。浏览器确认 40 美元/2 美元设置、三策略选择、实盘锁定、无横向溢出；展开 Atlanta 决策得到正确 Polymarket URL。
- 阻塞：重启后同时启动全部 poller 时复现约 1.6GB RSS 并失去 8765 监听的运行态回归；为避免掩盖问题，当前后端已稳定重启且 scheduler 保持 stopped，paper cohort inactive，`LIVE_TRADING=false`。下一步先修 scheduler 冷启动 fan-out，再做自动模拟闭环验收。

### 2026-07-13：Layer 6 批量目标修复与开发者数据源面板

- 改动：修复 `signal-decisions-build` 批量日期按升序选择旧日期的问题，改为优先最新预测/市场交集；未指定城市时按 `stations.enabled=1` 读取全部 14 城，不再静默退回旧 5 城；`--dry-run` 不再写 readiness。开发者设置新增只读“数据源”页，展示 source-health-v2 的 13 类来源、14 城覆盖、新鲜度和必需阻塞，并只返回 Weather.com/WU PWS 凭据是否配置的布尔值。
- 验证：新增 4 项 Layer 6 回归；`tests.test_v3_core tests.test_polywx_contract` 233/233 通过，前端 `npm run build` 通过，`git diff --check` 通过。浏览器确认 Weather.com 已配置、PWS 功能允许但凭据未配置、13 类链路可滚动查看，无密钥内容进入前端。
- 结论：revision 2 只覆盖上海 11 条的主要工程原因已定位并修复；当前 500 条 gate 样本主要为 `insufficient_bias_samples`，上海 3 个正 edge 候选又被真实 spread gate 阻塞。批量重建现在具备正确目标选择，但调度器停止后天气、盘口和派生数据约 12 小时过期，因此本轮没有用旧输入制造新决策。
- 阻塞：独立 PWS entitlement 仍未配置；revision 2 尚未在新鲜输入上覆盖 14 城；独立结算样本不足、价差过宽和 live 锁定仍是事实闸门。
- 下一步：先受控刷新上游，再按最新 D+0/D+1 交集重建 14 城 revision-2 决策并输出候选/gate 报告；操作员验收后才启动 14-30 天 paper cohort。
- 相关提交：`6b14f17`。

### 2026-07-13：开发者设置 API 配置闭环

- 改动：将只读的凭据状态页改为中文优先的 API 配置页。Weather.com、Wunderground PWS 作为主要天气服务展示，MiniMax、Visual Crossing、飞书收进“更多可选服务”；每项支持本地保存、星号隐藏、清空和供应商级连通性测试。高级数据源健康矩阵折叠保留，不再占据主路径。
- 安全：浏览器永不读取完整已保存密钥，后端只返回 `configured` 与固定星号；写入仅允许本机请求和显式确认，通过原子方式更新 gitignored `.env`。飞书测试因会发送消息而要求二次确认；Polymarket 私钥继续不进入页面，`LIVE_TRADING=false`。
- 验证：Weather.com 使用当前本地密钥真实读取逐小时预报成功；Wunderground PWS 当前为空配置并如实显示。API 单测、核心/PolyWX/执行工作台回归共 242 项通过，前端 production build 通过，浏览器浅色主题下保存/测试/折叠交互无 console error。
- 结论：开发者不再需要理解内部环境变量名，也不需要手工打开 `.env` 才能配置普通数据/通知 API；密钥权限不足、未配置和连接失败均有中文结果。实盘与 paper 验证状态未改变。
- 下一步：由操作者在“开发者设置 → API 配置”中补入具备 PWS 产品权限的独立 Wunderground key 并点击测试；通过后再恢复受控数据刷新与策略验证。

### 2026-07-13：跨层定向验证代理与执行安全边界

- 改动：新增只读 `project-verify`，由数据基座、概率模型、信号风控、模拟执行/结算、操作面五类代理分别输出 `observation / paper / paper_evidence / live_canary` 门禁；默认 quick，`--deep-verification` 才做完整 SQLite integrity scan。API 密钥响应同时纳入星号边界验证。
- 执行安全：`/api/executor/canary-dry-run` 无论 `LIVE_TRADING/LIVE_DRY_RUN` 环境组合都强制 `force_dry_run=true`，默认金额为 $1 且受 canary/live 双上限约束；右侧批量模拟请求必须携带当前显示的 `strategy_revision_id + decision_batch_issued_at`，后端缺任一字段直接拒绝。Kelly 对越界概率返回零仓位。
- 验证：定向 11 项测试通过；`tests.test_v3_core tests.test_polywx_contract tests.test_project_verification tests.test_execution_workbench_contract` 共 244 项通过；前端 `npm run build` 通过。真实只读 deep 报告保存于 gitignored `audits/project-verification-2026-07-13/`，结果为 `code_only`，四个 readiness stage 均 blocked。
- 真实阻塞：核心源因 scheduler stopped 全部过期；历史库存在 40 组 METAR 重复、2 组 consensus 重复；3,285 个 training run 不满足 lead/no-leak，959 个 DEB 来源晚于 issued_at 或身份不匹配；16 个高权重组件校准少于 7 日；176/176 matched 市场盘口不满足新鲜/可执行检查；尚无 14 日/30 笔权威 paper 证据。live executor 仍是 legacy v1，缺聚合风险预算、revision-bound 路由和 CLOB 提交前幂等保留。
- 结论：代码边界更安全且验证从“报告”升级为机器门禁，但系统当前不具备 observation/paper/live 使用资格，不能通过放宽 edge/gate 制造信号。下一步优先修时序泄漏和重复键，再刷新上游并重建 14 城 revision-2 决策；之后才启动 14-30 日 cohort。
- 相关提交：`0bab475`。

### 2026-07-13：设置页减法与 API 可配置闭环

- Layer：Layer 7 操作面与既有本机 API 配置接口；未修改 collector、策略、paper/live 执行逻辑。
- 改动：将五段式“开发者设置”收敛为 `连接服务 / 模拟策略 / 高级设置`，默认进入连接服务；Weather.com、Wunderground PWS、Visual Crossing、MiniMax、飞书五项全部直接展示，支持填写、更新、清除和真实连接验证。已配置值只以固定星号返回，内部版本、source health 与生产阻塞收进高级折叠区，交易台入口统一改名为“设置”。
- 验证：`tests.test_polywx_contract tests.test_v3_core tests.test_api_settings tests.test_execution_workbench_contract` 共 243 项通过；前端 production build 通过；浏览器浅色主题下无 console error/warn。使用本机已保存且未回传明文的 Weather.com key，真实连接验证成功并读取逐小时预报，耗时约 1622ms。
- 结论：普通用户不再需要理解环境变量名或打开 `.env`，所有已支持 API 均可在同一页配置和验证；密钥明文仍只保存在本机。Polymarket 钱包私钥继续不进入浏览器，`LIVE_TRADING=false` 与 paper 状态未改变。
- 下一步：由操作员在“设置 -> 连接服务”补入具备 PWS 产品权限的独立 Wunderground key 并点击“验证连接”；通过后再恢复受控数据刷新与 14-30 日模拟验证。

### 2026-07-13：四路定向代理验证与天气自然键迁移

- Layer：Layer 2 METAR、Layer 4 hourly consensus 及其只读跨层验证；未启动 scheduler、paper 或 live。
- 验证代理：分别审计数据时序、Layer 5-10 执行、Layer 7 PolyWX 契约和架构边界。报告固化于 `audits/targeted-agent-verification-2026-07-13/README.md`；共同确认重复键为当前最先可闭环的 P0，并识别 forecast 时序泄漏、legacy live 提交、paper 证据口径和 UI fail-open 为后续 P0。
- 改动：新增版本化迁移 `20260713_01_canonical_weather_keys`；METAR 强制 `(station_id, report_time)`，consensus 强制 `(city, target_date, local_hour)`；调用方自定义 key 被 canonical key 替代，身份不完整时拒绝写入。迁移按解析质量、更新时间、ID 选择胜出行并记录删除计数。
- 真实数据：迁移前备份 `data/weatherbot_v3.db.bak-canonical-keys-20260713-213347`；生产库删除 40 条重复 METAR 和 2 条重复 consensus，迁移后两类重复组均为 0，唯一索引存在，verifier `storage_integrity=pass`。
- 验证：4 项定向迁移/幂等测试通过；完整 `python -m unittest discover tests` 共 327 项全部通过；系统仍为 `code_only`，`LIVE_TRADING=false`、dry-run 开启、scheduler 停止、paper validation inactive。
- 下一步：建立统一 forecast availability/as-of validator 与不可变 snapshot key，隔离 3,285 个负 lead run，并重建 1,130 个受影响 DEB；随后才修 Layer 7 fail-open 表达和 Layer 8/10 证据/实盘安全边界。

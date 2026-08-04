# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> For user-friendly release highlights, see the [GitHub Releases](https://github.com/ZhuLinsen/daily_stock_analysis/releases) page.

## [Unreleased]

<!-- 新条目格式：- [类型] 描述（类型取值：新功能/改进/修复/文档/测试/chore）-->
<!-- 每条独立一行追加到本段末尾，无需分类标题，合并时冲突最小 -->

- [新功能] Journal“仓位复盘”新增 Moomoo LIVE/US 期权当前持仓只读双采样、短期服务端 preview、显式 future-only confirm 与独立 append-only 快照账本；完整空仓可保存，查询失败或边界变化不会伪装为空仓，且全链路不解锁或执行交易。
- [修复] 当前持仓证据保存两轮规范化边界并重算 hash/差异，逐字段交叉验证采集与 summary 计数，只接受规范 Decimal 字符串，并拒绝未来偏差超过 2 分钟、采集超过 5 分钟或完成后超过 30 分钟的陈旧首次确认，防止伪造稳定性或把旧 payload 当作当前锚点。
- [改进] 当前持仓确认冻结并事务内重验最新 Journal refresh publication、账户 binding 与连续性 hash；读取确认记录时重算表头、provenance、member/instrument key 和成员集合，并保持当前状态与历史 opening boundary、证据窗口末数量及券商成本上下文严格隔离。
- [测试] 2026-07-30 真实 OpenD 零写检查得到 7 个活动美股期权持仓行、7 个完整合约规格、0 个 validation issue，并过滤当次返回的 19 条 `qty=0` 非活动缓存行；两次边界一致，正式数据库文件未变化且没有交易动作；另补双读篡改、陈旧/未来时间、publication 锚定、存储篡改、幂等与历史 Episode 零写回归。
- [文档] 新增 `New-docs/phase1/11_CURRENT_POSITION_SNAPSHOTS.md`，记录 Moomoo 当前持仓 API 语义、双采样证据、新鲜度门禁、显式确认、append-only 存储、真实验收及未来 Episode boundary 所需的成交连续性 fence；专题细节未重复写入根 README。
- [修复] 当前持仓 latest 状态改为在同一 SQLite 读事务中核对最新 Journal publication、账户 binding、publication anchor、30 分钟 freshness 与 2 分钟未来时钟偏差；过期、时钟异常、旧账户或被新刷新取代的确认记录明确降为“历史快照”，不再永久显示为当前仓位或当前空仓，同时仍允许为正确账户重新只读检查。
- [修复] 当前持仓早期 SQLite 表会按当前模型补齐缺少的 additive nullable 列，应用数据库初始化即安装三张快照表的 UPDATE/DELETE 拒绝触发器，避免热重载旧 schema 或全局 `create_all` 留下兼容与不可变保护缺口。
- [新功能] Journal 当前持仓新增零写 Episode continuity-fence readiness：以保守的采集完成时点为 boundary，核对同账户后续 refresh/publication 链、canonical 来源、守护区成交、aggregate-only 时间歧义、跨界普通单/组合单及期权身份；只输出 `no_snapshot / awaiting_refresh / blocked / ready`，不会生成或激活 Episode。
- [改进] “仓位复盘”把未来边界流程收敛为等待快照、等待后续刷新、门禁阻断和连续性已证明四个状态；历史快照不因超过 30 分钟而失去边界候选资格，同时明确 `ready` 只允许进入下一步零写 build preview，不表示收益或构建已经验证。
- [测试] 新增 continuity-fence 只读连接、缺快照/缺后续刷新、覆盖缺口、守护区成交、汇总成交变化、跨边界 fill set/组合执行组、未发布来源、期权身份与稳定 fence key 回归，并覆盖 Web 深层 camelCase 与矛盾状态 fail-closed。
- [文档] 更新 `New-docs/phase1/11_CURRENT_POSITION_SNAPSHOTS.md`，记录 continuity fence 的边界时间、证据链门禁、页面状态、零写保证和接入新版 Episode build 前的剩余条件；专题细节未重复写入根 README。
- [新功能] Journal 新增 fence-bound 未来 Episode 零写构建预览：服务端在同一只读 SQLite 事务中重算 continuity fence、严格重放 snapshot/publication/canonical 证据并仅投影 boundary 后的美股期权事件，返回确定性 build/evidence hash、生命周期计数与费用守恒，但不保存构建、不改变默认 activation 且不执行交易。
- [修复] Canonical 严格读取补验 set key、root/stored provenance、reader/counts、source batch keys/kinds，并强制 in-session continuity helper 使用同一 SQLite bind、显式事务与 `query_only=1`；fence v1.1 额外冻结 target source cutoff，blocked 状态不再暴露 full projection，预览预算覆盖 order/fill/group/leg/fill-link。
- [测试] 新增 stale fence、可变/零填充 OCC 身份、父 detail order、snapshot 成本隔离、left-censored P&L、费用守恒、只读事务误用、canonical set key/provenance 篡改与零业务写入回归；API 另校验生命周期计数和全部 no-write/no-trade 标志。
- [文档] 扩展 `New-docs/phase1/11_CURRENT_POSITION_SNAPSHOTS.md`，记录 future Episode preview 的唯一 fence 输入、严格 boundary 规则、摘要响应、left-censored 限制、零写合同及正式 append-only 构建前的剩余步骤；根 README 未重复专题实现细节。
- [新功能] Journal 只读证据链新增 append-only Moomoo execution-group/leg/fill-link/group-fee 模型与 canonical provenance；完整、终态、腿数量平衡且严格位于 CSV 基线后的组合单可进入单腿 PositionEpisode，父组合不再伪装成普通订单。
- [改进] Moomoo 只读刷新为所有实际成交的期权合约批量冻结 market snapshot 合约规格，不再只读取组合腿；已成交合约缺 multiplier 继续 fail closed，未成交失败单不会因无执行乘数阻断仓位构建。
- [修复] 组合费用按币种仅在 execution-group scope 精确保留，不猜测分摊到腿；受影响回合的 Fee/Net 保持空值并从 Headline 排除，生成与激活均要求独立 `accept_group_fee_scope` 确认。
- [改进] Journal“交易证据”和“仓位复盘”页面分开展示普通单、组合组/腿/成交关联/组费/合约规格、逐币种费用守恒及受影响回合，并用明确文案解释腿级净收益为何不可用。
- [测试] 真实 OpenD 零写验收通过 759 orders / 1,649 fills / 740 fees / 255 contract specs、1 group / 2 legs / 4 fill links；临时正式库副本构建 1,615 个回合，USD 174,531.05 普通费用与 8.08 组费守恒，正式库保持未发布、未构建、未激活。
- [文档] 更新 Journal 每日只读刷新与 canonical Episode 专题，记录组合增量尾部门禁、组级费用语义、显式确认、真实副本验收和仍保持 fail-closed 的 CSV overlap/腿级费用限制；细节未重复写入根 README。
- [修复] Journal 每日只读刷新与状态水位统一截止到最近已完成的 XNYS session close：盘前/盘中不再把当天尚未结算的成交和缺失费用混入上一日正式批次，周末、节假日与半日市使用交易所真实 session；日历不可解析时 preview fail closed。
- [改进] Journal 刷新按钮改为“检查上一完整交易日”，并明确当前盘中、盘后和夜盘不会进入正式日终证据预览。
- [新功能] 新增默认关闭的 `MOOMOO_PREMARKET_PREFETCH_ENABLED` 影子证据生产器：按 XNYS `open-22m` 冻结 SPY + 持久化 Top 5 的共同 `target_as_of`，最迟 `open-21m` 启动并在 `open-19m` 硬终止；Moomoo 只在可取消的 spawn 子进程中读取，父进程以 DB lease、owner/attempt fencing 和单向 Pipe 管理，正常完成后原子追加 `ready / partial / unavailable` 审计 bundle。正式 Regime、排名、UI 与 API 当前不消费该证据。
- [新功能] 新增 `regime_premarket_prefetch_runs` 可恢复协调表与 append-only `regime_premarket_artifact_bundles`：bundle 保存统一日期/session/universe/as-of/freshness、规范 payload 与 SHA-256，SQLite 拒绝 UPDATE/DELETE；正式 selector 预留但 fail closed，真实影子窗口验收前不得接入 consumer。
- [测试] 新增盘前影子预取的纯证据合同、跨 worker DB claim/heartbeat/lease recovery、append+settle 原子性、不可变触发器、因果 selector、真实 multiprocessing spawn 成功/卡死回收、调度窗口、FastAPI 生命周期及正式 Regime 零消费回归。
- [文档] 更新服务端盘前编排专题，记录影子预取的默认关闭开关、09:08–09:11 ET 窗口、独立进程/数据库边界、正常 partial 与 timeout/crash 语义，以及真实 shadow 验收和正式接入门槛；专题细节未重复写入根 README。
- [新功能] 盘前影子证据新增 append-only `regime_premarket_artifact_ingestions` 一对一数据库回执：SQLite 先原子提交 bundle + succeeded run，再在第二事务用 `STRFTIME` 数据库时钟生成毫秒级 UTC `ingested_at`，确保回执不早于 artifact 首次 commit；调用方不能传入或回填，崩溃恢复与旧 bundle 只按恢复/迁移当下数据库时间登记。
- [修复] 正式 artifact selector 改用可信入库回执证明因果可见性，要求 `ingested_at + 1ms <= consumer as_of`，并重验 fetched/claimed 不晚于回执容差上界且回执不越过 hard deadline；截止后才恢复的回执会保留审计记录并将 run 明确置为 failed，当前正式回执发布与选择在非 SQLite 方言上整体 fail closed。
- [改进] 盘前证据 schema 初始化完成后，普通读路径只做轻量只读 readiness 检查，不再为每次 selector/status 查询争抢 SQLite writer lock；正式 Regime、排名、UI 与 API 仍未消费影子数据，真实 09:08–09:11 ET 窗口仍待验收。
- [测试] 新增 after-commit 数据库回执生成与不可变、两阶段崩溃后无回执 fail-closed/重放恢复、截止后恢复转 failed、幂等保留原回执、legacy 迁移使用当下 DB 时钟、事后 artifact 对历史 consumer 不可见、时间顺序/deadline fail-closed 及 schema readiness 读路径回归。
- [文档] 更新服务端盘前编排专题，将影子持久化修正为 run、bundle、ingestion receipt 三类记录，记录数据库时钟、迁移边界、selector 因果门禁、读锁优化及正式 consumer/真实窗口的剩余退出条件；专题细节未重复写入根 README。
- [修复] 盘前影子 artifact 入库改为严格重放 schema、交叉核对 run/quality/coverage、规范化等价 payload 后再哈希，并拒绝晚于 fetch 的证据、非五分钟 policy 与非有限 JSON；正式 selector 同时按 consumer 视角检查每条实际证据 freshness，截止后 failed/timed_out 重放不再改写终态。
- [修复] FastAPI lifespan 分别隔离各 scheduler 的 stop 异常，单一关闭失败不再跳过其余 scheduler 与 app state 清理。
- [测试] 固化影子 producer 默认关闭零启动、真实 XNYS DST/early-close/非交易日、语义等价 payload 幂等、伪造 coverage、未来证据、NaN 腐坏 fail-closed、截止后终态只读重放与 teardown 隔离回归。
- [新功能] Opportunity Snapshot 新增 `opportunity_qualification_v2` append-only assessment：将数据库证明的发布状态、analysis quality、逐候选 causal/observation 与“标的路径 / 日线选股 / 完整研究”三条 `qualified / excluded / unverified` 决策分离保存；canonical 发布同事务写入，显式历史 backfill 只追加新 policy，不改写旧事实。v2 将 retrospective raw path 明确排除，并令完整研究在 assessment 缺失或读取异常时 fail closed。
- [改进] `/regime` 官方盘前卡继续分开展示发布状态、数据质量和统计入样；有 qualification 时第三轴显示“标的路径 X/Y、日线选股 X/Y、完整研究 X/Y”且不暴露技术 track key，旧 API 缺字段时保持既有聚合 UI。候选列使用“研究用途”，5D/20D 使用“已回填 / 等待目标日”，零入样显示“尚无可统计样本”。
- [改进] 结果自动维护 policy 升级为 `xnys-close-qualified-raw-path-v2`：逐候选只回填标的路径 track 为 `qualified` 且 `prospective` 的 5D/20D outcome，不再按整批 `validation_eligible` 跳过 degraded canonical；Snapshot API 与页面分别展示 raw-path 审计进度和完整研究进度，学习摘要仍只接纳完整研究 track 同时 `qualified + prospective` 的 outcome，raw path 不得填充命中率。
- [测试] 新增 qualification 三轨纯分类、append-only/幂等/冲突、canonical 同事务回滚、显式 backfill、API 深层 camelCase、ready/degraded/legacy UI，以及 maintenance v2 raw-path 与完整研究隔离回归。
- [文档] 更新每日机会、服务端盘前编排与自动结果维护专题，记录 qualification 三轨、analysis quality/causal window 分离、degraded canonical 的 raw-path 回填边界和 v2 maintenance slot 语义；专题细节未重复写入根 README。
- [修复] Regime 计算为盘前 provider 冻结同一个 timezone-aware UTC `as_of` 并拒绝 naive 时间；带 `date/Date` 或 `DatetimeIndex` 的 SPY/VIX/板块日线在指标计算前过滤 `target_date` 之后的 future rows，避免未来数据污染当日快照。
- [文档] 明确 Moomoo 盘前适配器虽已只读验收但尚未接入正式 Regime；约 32 秒的同步冷调用不得进入 canonical 关键路径，下一步必须先实现可终止独立进程与带日期/as-of/hash/质量证明的 append-only artifact，缺失或过期继续降级。
- [修复] `/regime` Top 5 机会榜不再与 0–45 DTE 期权墙并发调用独立 `/option-context`：`option-wall/1.1` 复用同批动态合约快照返回最近到期 ATM Call IV，使默认五标冷加载保持每标最多两个链窗口、合计最多 10 次 `get_option_chain`，避免第 11 次调用触发 Moomoo 10 次/30 秒限频并令末位标的部分覆盖。
- [测试] 新增同一墙快照最近到期/最近执行价 ATM Call IV、精确 ATM 缺 IV fail-closed、endpoint 不调用独立 ATM 读取、Top 5 selected 零 `/option-context` 请求，以及切换墙 DTE 范围不改变固定 0–45 ATM 语义的回归。
- [文档] 更新每日机会与期权数据配置专题，记录 `option-wall/1.1` 的 ATM IV 选择合同、单页 10 次链查询预算、独立 endpoint 兼容边界及多标签页仍可能竞争供应商额度的剩余风险。
- [修复] Web 顶栏 Moomoo 状态改为共享监控器：同标签请求/定时器去重，跨标签以 Web Locks、localStorage 与 BroadcastChannel 协调为每 60 秒最多一次健康探测，隐藏页暂停，失败按 15/30/60/120 秒退避并显示“状态未知”，网络恢复或页面重新可见时主动恢复且成功状态最多保留 75 秒。
- [测试] 新增 Moomoo 状态多组件单请求/单时钟、跨标签新鲜结果复用、隐藏页暂停与 online 恢复回归测试。
- [改进] 今日机会研究新增“基础榜 → 期权概览 → Top 5 墙”三阶段状态与请求日、基础证据日期和 ET 生成时间；刷新期间保留并标明上一成功结果，可选增强失败或期权墙部分覆盖时明确降级，Regime 首次加载不再阻塞机会榜。
- [新功能] Canonical Premarket Research Cycle v1 新增服务端有序研究池版本、唯一 cycle slot、append-only attempt/stage/lease、跨 worker 单 owner claim 与重启状态恢复；本地低噪声 scheduler 在 XNYS 09:12 ET 首次生成、09:17 ET 恢复，09:18 后拒绝新 leader、09:20 硬截止。2026-07-24 已完成首轮真实窗口验收；完整 draft/finalizer、pool 生效交易日、服务端 last-good、外部进程监管与期权增强冻结仍待完成。
- [修复] 官方盘前 snapshot、候选、`persist_snapshot=completed` 与 `attempt_finished=published` 改为同一数据库事务；cycle slot 锁内以权威时间生成全部 provenance，并在构造后再次检查 09:20 deadline 与 lease，跨截止或终态失败会整包回滚。
- [修复] 官方盘前发布契约将保序研究池与 Snapshot canonical universe 按规范化 symbols 集合核对，修复真实非字母顺序研究池在发布阶段被误判为 scope 不一致的问题。
- [修复] FastAPI lifespan 关闭盘前 scheduler 时会等待正在执行的只读 tick 完整退出；有限等待超时时保留 live thread 引用并拒绝同一 host 重启，避免 reload 后遗留 provider thread 与新 scheduler 重叠。
- [改进] 今日机会页以“官方盘前研究 / 官方版本 / 只读预览”展示 scheduler、研究池版本和最近 attempt；页面加载不再自动 run 或设置窗口 timer，只有明确点击才发送 `manual=true`，运行中只短期轮询只读 status 并禁止并发普通扫描。
- [新功能] Watchlist 新增本地列表与官方盘前研究池差异核对及显式保存；正式池仅接受按顺序去重的前 20 个美股期权标的，`STOCK_LIST` 与 TradingView 导入都不会被后台静默启用。
- [测试] 新增研究池幂等/版本、append-only trigger、租约竞争/过期接管/旧 owner 拒绝、两次 attempt 上限、重启恢复、缺池零 provider、09:12 手动门禁、scheduler due/retry/lifespan 与 Web 只读加载回归。
- [文档] 新增 `New-docs/phase1/08_SERVER_OWNED_PREMARKET_ORCHESTRATION.md`，记录服务端研究池、09:12/09:17 时序、持久化 attempt/lease、低噪声 scheduler、安全边界、真实窗口验收缺口与回滚。
- [测试] 2026-07-24 真实盘前窗口验证 09:12 自动 attempt 失败整包回滚、09:17 自动恢复发布及 09:20 后幂等；新增非字母顺序研究池的原子发布回归测试，并记录辅助 Regime 权限降级不等同于发布故障。
- [修复] Finnhub 第三方 HTTP 失败诊断移除完整 query string，并脱敏 `token/api_key/key` 与当前配置 key，只保留异常类型、状态和不含查询参数的路径；现存旧日志不会被自动改写，仍按既有轮转与保留策略处置。
- [改进] 今日机会研究收敛为 Watchlist Top 5 主清单：Top 5 自动批量加载并直出 0–45 DTE Call/Put 执行价墙，其余候选、排名外增强与 5D/20D 学习口径默认折叠。
- [修复] 研究用途改由新鲜完整日线、方向结构和独立量能/成交额确认共同决定；降级或缺失 Regime 不再把所有候选统一标为等待交易触发，页面同步展示“基础门禁通过 / 基础候选·非信号 / 仅作背景 / 精确数据阻断”及研究确认、失效观察。
- [新功能] 本地 Watchlist 支持合并导入 TradingView 官方 TXT 导出；机会扫描使用前 20 只自选并显示来源，避免把 TradingView 的 broker/Charting Library API 误称为个人账户 Watchlist 同步接口。
- [新功能] 新增 Moomoo History CSV 的 loss-aware parser、去标识化只读对账 CLI 和可信证据导入预览：保留重复表头的位置语义、逐笔 fill、订单汇总、九项费用与 ET 时间证据，并将批次分为 `exact` / `partial` / `blocked`。
- [新功能] 新增 append-only `journal_v2_*` 证据账本、预览/确认导入/data-health API 与 Journal 导入交互；相同源文件幂等，partial 必须显式确认，旧 Journal/FIFO 表不被重建或混用，Data Health 明确区分整批范围与局部 API 对账窗口。
- [新功能] 新增不可变 reconciliation attestation：API 对账可以晚于 CSV 批次追加并记录 export hash，不修改既有 ImportBatch；SQLite trigger 拒绝全部 `journal_v2_*` 表的 UPDATE/DELETE，强化 append-only 契约。
- [改进] passed reconciliation attestation 必须携带只读 export SHA-256，并验证窗口订单数、statement identity 与当前 batch observation 一一绑定；SQLite 连接启用 `recursive_triggers`，防止 `INSERT OR REPLACE` 绕过 append-only DELETE trigger。
- [修复] Moomoo 导入不再静默丢失只有 `Filled@Avg Price` 的早期已成交订单，不再合并字节相同但合法的同秒 fill，并补纳 `Consolidated Audit Trail Fees`；缺少逐笔证据的订单保留为 `aggregate_only`，绝不合成 fill；旧 `/journal/import` 固定返回 410，关闭不完整 FIFO 重建旁路。
- [修复] CSV/OpenAPI 对账门禁现在同时检查 CSV 内部一致性、孤立记录、API order/deal/fee 引用与两端费用总额；任何异常都会阻止 `analysis_ready`，不再出现局部逐单相等但整体证据异常的假阳性。
- [修复] parser 升级为 `moomoo-statement-v2`：取消/失败订单的 `0@0.00` 保持 `not_filled`，真实部分成交取消仍保留 fill；对账不再以 API 单边 `dealt_qty=0` 跳过 CSV 成交证据。正式库用新 batch 表达升级，旧 v1 仅保留审计。
- [测试] 用正确真实 CSV 与只读 OpenAPI 稳定窗口逐单核对 1,089 个订单、2,107 条 fill、VWAP 和九项费用分量均为零差异，并新增 parser、reconciliation、append-only repository、API 与 Web 导入状态测试。
- [文档] 新增 `New-docs/phase1/01_MOOMOO_EVIDENCE_LEDGER.md`，记录真实数据覆盖、证据等级、正式本地批次、分析限制及 PositionEpisode/StrategyEpisode 后续退出条件。
- [新功能] 新增 PositionEpisode builder v1.1.0、append-only EpisodeBuild/StrategyEpisode/PositionEpisode 持久化与列表/详情 API：latest accepted `moomoo-statement-v2` batch 的 5,400 条真实 fill 和 574 条 aggregate order event 生成 1,441 个生命周期，aggregate-only 证据不会被伪造成 fill。
- [新功能] Journal 默认进入“仓位复盘”并展示最新 immutable build；legacy Overview/Trades/Reality/Analysis 页面仅作隔离存档，不再作为当前交易事实源。
- [改进] Episode builder 由 broker amount 证明股票/期权乘数 1/100（金额残差容差 USD 0.005），验证 USD 151,750.75 source/allocated 费用守恒；当前缺少已验证期初持仓快照，1,441 个 Episode 全部以 `assumed_flat_unverified` 排除 headline，条件性 closed P&L 仅作辅助读数。
- [改进] SQLite 应用连接同时启用 `foreign_keys` 与 `recursive_triggers`，Episode build 继续受 `journal_v2_*` UPDATE/DELETE 拒绝 trigger 保护；build 1 写入前保留 `data/backups/stock_analysis-pre-episodes-20260721.db` 回滚备份。
- [文档] 新增 `New-docs/phase1/02_POSITION_EPISODES.md`，记录首个正式 Episode build、条件性 P&L 边界、合约乘数证据、费用守恒、API、legacy 隔离与回滚方式。
- [文档] 新增 `New-docs/architecture/05_PRODUCT_CHARTER_AND_ROADMAP.md`，将产品目标收敛为以 Moomoo 成交事实、期权策略生命周期、证据化复盘和个人 Playbook 为核心的 Trading OS，并定义运行止血、数据对账、复盘工作台、Edge Explorer、Playbook 与只读行情决策台的阶段验收标准；旧 v4 的 LEAP/频率目标降级为待验证假设。
- [修复] 系统配置响应 Schema 补齐已注册的 `phase0` 分类，修复 `/api/v1/system/config?include_schema=true` 因 Pydantic 分类校验失败返回 500 的问题。
- [修复] Moomoo 真实账户只读成交同步将应用层 `LIVE` 正确映射为 SDK 10.x 的 `TrdEnv.REAL`，修复因访问不存在的 `TrdEnv.LIVE` 而无法查询真实交割记录的问题，并明确查询过程不解锁交易。
- [新功能] 新增 `scripts/probe_moomoo_readonly.py` 安全入口：不写 Journal 数据库，显式通过 `get_acc_list` 选择稳定账户，按最多 7 天窗口分块查询订单/成交并按 `order_id`/`deal_id` 去重，费用查询限制为每批 400 单；stdout 仅输出去标识化汇总，显式 `--output` 才导出移除账户标识后的分析记录。
- [改进] Moomoo 只读探测将同步 Trade Context 隔离到带总截止时间的可终止子进程，并新增 `analysis_ready`：只有订单/成交代码、方向、数量、成交加权均价和费用覆盖全部对账通过才允许进入分析；程序化时间窗口统一转换到市场时区并限制单次最长 366 天。
- [修复] Moomoo SDK 进程级配置改为线程安全的一次性初始化，关闭 console 重连洪泛并将 SDK 文件日志 best-effort 限到 WARNING/3 份备份；可选 SDK 初始化异常会安全 shelve 到 fallback；Breakout context 仅在 handler 与订阅全部成功后发布，失败路径会关闭资源且未订阅状态不再误报健康。
- [改进] 旧 Moomoo live Journal writer 继续暂停：旧 CLI 固定返回 `LegacySyncPaused`，`POST /api/v1/journal/sync-live` 固定返回 409；CSV 已改由隔离的 append-only 可信证据导入器入账，在 API observation、canonical reader、opening snapshot 与策略意图确认达到退出条件前不恢复旧 writer。
- [改进] Journal 用户界面移除 `v2` 迁移代号，统一使用“仓位复盘”“交易证据”和“可信证据账本”；内部 `/journal/v2/...` API、`journal_v2_*` 表名与 parser 版本保持兼容。
- [改进] Web 顶栏将 Moomoo 状态明确为“可达 · 只读”，使用可访问 Tooltip 说明 TCP 可达不等于交割单已同步；Journal 移除固定 50% 盈亏平衡与 0DTE 30% 危险阈值，并修复重复字体声明导致的 404/解码错误。
- [测试] 新增 Moomoo 永久只读 AST 边界、总超时与完整对账、旧 writer 暂停、SDK 并发初始化/资源释放、LaunchAgent 日志轮转、Web 字体装载与 Journal 文案回归测试。
- [改进] macOS 三个 LaunchAgent 统一通过日志轮转包装器启动，分别限制 stdout/stderr 单文件为 10 MiB 并保留 3 个备份，同时保持原日志路径与子进程退出码、信号转发语义，避免 OpenD 离线重连等异常导致日志无限增长。
- [改进] `scripts/install_launchagents.sh` 新增 `--only uvicorn` 精确选择，可单独恢复只读 Web/API 而不加载旧 Moomoo writer 或 breakout；状态命令同时展示 Web-only 与全量恢复入口。
- [修复] LaunchAgent 状态脚本改用真实的 `GET /api/health` 探针，不再因请求不存在的 `/health` 或使用不受支持的 HEAD 方法而把健康 Web 服务误报为不可达。
- [新功能] 新增严格的去标识化 Moomoo OpenAPI export parser、`/journal/v2/openapi-imports/preview` 与 Web“OpenAPI 只读 JSON”预览；展示真实范围、order/fill/fee、费用与对账状态，但明确不写 Journal 数据库。
- [新功能] 新增 identity-link-aware 纯 canonical reader scaffold：显式 order/deal link 仅在可证明时使用；逐笔无法安全配对但订单级 count/quantity/VWAP 已 attested 时，由 authoritative API fill set 覆盖 CSV weak-fill set 并保留集合级 provenance，禁止伪造 broker deal link；当前尚未生成/持久化 links/attestations，也未接 ORM 或正式 Episode source。
- [修复] OpenAPI parser 的数字规范化不再依赖 JSON 调用方选择 `float` 或 `Decimal` loader，同一只读文件在两条路径下生成相同 observations、source/evidence hash 与 batch key；兼容 Moomoo 四位小数秒时间。
- [测试] 真实重叠窗口 canonical 内存合同通过：2,178 order observations 去重为 1,089，4,214 fill observations 去重为 2,107，并保留 2,107 条 CSV weak-fill shadow provenance；`analysis_ready=true`、0 issues、未写正式数据库。
- [修复] Data Health 与 PositionEpisode repository 在 canonical persistence 接通前继续显式选择 CSV source，防止较新的局部 OpenAPI window 取代完整 CSV 分母或悄悄成为 Episode 输入。
- [文档] 新增 `New-docs/phase1/03_OPENAPI_CANONICAL.md`，记录 OpenAPI 预览、canonical identity/冲突规则、真实 1,089 orders / 2,107 fills 验收和正式写库前的剩余边界。
- [chore] 保留早期 Moomoo OpenAPI Phase B live Journal writer 原型作为迁移背景和测试对象；其 CLI/API 当前均固定暂停，不作为可执行同步入口，`LIVE` 仅表示读取真实账户历史事实，不表示开放交易能力。
- [新功能] Moomoo OpenAPI Phase C 期权链：新建 `data_provider/moomoo_options.py`，调 `get_option_chain` / `get_option_expiration_date`，IV 直接使用 Moomoo 服务端值（无需 BS 反推）。`src/options/iv_rank.py::compute_atm_iv` 在 `MOOMOO_OPEND_ENABLED=true` 时优先走 Moomoo，失败回落 yfinance。
- [新功能] Moomoo OpenAPI Phase D 实时突破检测：新建 `src/breakout/live_runner.py`（KLine_1M 订阅 + 60 bar 环形缓冲 + range_high 突破触发 + Q1-Q5 过滤）+ `scripts/run_breakout_live.py` 长进程 CLI（JSON 行输出，可接 Telegram bot 等下游）。
- [修复] `/regime` watchlist 现在合并 `useUserWatchlistStore` 的本地自选（regime snapshot 里原本没有 watchlist key，导致该区块永远为空）；每行带 source dot（accent=本地自选 / grey=regime snapshot）。
- [修复] `/regime` Macro / Prev Day / Sector / Premarket 维度在数据源未配置或无数据时显示 `—` 并加 caption，区分「评分 0」与「没数据」；`ContributionList` 新增 `status: 'no_data' | 'computed'` prop。
- [改进] `/stocks/:ticker` 对无效 ticker（K 线 + 新闻 + digest 同时为空且都加载完）展示带拼写建议的引导卡片；`中文总结` tab 条件门修正，`DigestView` 在 `newsCount === 0` 时给出明确说明，不再互斥遮蔽。
- [新功能] `/journal` 新增 Analysis / Framework / Ask AI 三个 tab：Analysis 拉 `/api/v1/journal/stats-by-style` 渲染 `StyleBreakdown` 堆叠条 + 表 + `PnLByDte` 柱图 + Best/Worst 3；Framework 把用户的交易大前提保存到 `localStorage`（`dsa-journal-framework` key）；Ask AI 新建 `AskJournalChat` 聊天组件，对接 `POST /api/v1/journal/qa` 单轮 LLM，chat history 持久化。
- [新功能] 后端新增 `GET /api/v1/journal/stats-by-style`（按 `trade_style` + DTE 分桶汇总 PnL + worst/best）和 `POST /api/v1/journal/qa`（用户 framework + 最近 N 天 closed trades → LLM 单轮中文 Markdown 分析）；抽出 `stats_by_style` 到 `src/journal/analytics.py`；新建 `src/services/journal_qa_service.py`。
- [新功能] dsa-web `/stocks/:ticker` 详情页接入真实 K 线与可跳转新闻：合成数据替换为 `/api/v1/stocks/{code}/history` 与新增的 `/api/v1/stocks/{code}/news`，MA overlay 改为 8 / 13 / 144 / 169，新增 5m/15m/1h/1D/1W/1M 六档时间线切换（pills Tabs）。
- [新功能] `/api/v1/stocks/{code}/history` 支持 weekly / monthly（基于日线 `resample('W-FRI' / 'ME')` 聚合）与 1m/5m/15m/30m/60m/90m/1h 分钟级周期（仅美股，走 `YfinanceFetcher.fetch_intraday`，受 yfinance 官方上限约束）。
- [新功能] 新增 `GET /api/v1/stocks/{code}/news`：按 ticker 检索新闻，复用 `SearchService.search_stock_news`（SerpAPI / Tavily / Brave / Bocha / Anspire / MiniMax / SearXNG），返回 `StockNewsItem(title, snippet, url, source, published_at)`，过滤掉非 http(s) 链接，未配置 provider 时返回空列表不报错。
- [文档] 文档中心重组：把所有 `docs/*.md`（除 `docs/CHANGELOG.md`）+ 根目录 `PROJECT_VISION.md` / `review.md` 全部迁移到 `New-docs/`，按 `architecture/` `modules/` `design/` `user-guide/` `configuration/` `deployment/` `contributing/` `integrations/bots/` `phase0/` `archive/` 分类；`New-docs/00_INDEX.md` → `New-docs/README.md` 重写为总索引；同步更新 `README.md` / `AGENTS.md` / `.github/instructions/{client,governance}.instructions.md` 中对应路径；`Daily_workflow_guide.md` 新增 "新版 Web 界面速查" 段，映射每日动作到 `/regime` / `/watchlist` / `/stocks/:ticker` / ⌘K CommandMenu。`docs/CHANGELOG.md` 路径不动（AGENTS.md §1 硬规则 + 自动化依赖）。
- [改进] dsa-web UI 全面迁移到 `New-docs/design/Design_system.md` v1.0（Linear-Dark 终端风：violet `#7170ff` accent、Geist Sans/Mono、muted green/red 语义色、4 级 bg / 3 级 border / 4 级 text token、`rounded-ds-md` 最大半径、无阴影 elevation）；新增 `src/styles/tokens.css` + `styles/globals.css` + 新版 `tailwind.config.ts`（与遗留 token 并存，待 v1.1 清理）。
- [新功能] dsa-web 新增 `/watchlist` 路由（列选择器 + CSV 导出 + 过滤 + 跳转股票详情）与 `/stocks/:ticker` 详情页（lightweight-charts 蜡烛图 + MA3/5/13 overlay + TradePlan 表 + News/Events/History/Trace tabs + Run analysis 按钮）。
- [新功能] dsa-web 全局 `⌘K` / `Ctrl+K` Command Menu（cmdk）：支持 ticker 跳转、页面跳转、Recompute regime、最近 5 个查看过的 ticker。
- [改进] dsa-web Regime 页按 `Design_system.md §11.2` 重绘：`RegimeScore` 替换 250×250 gauge（+sparkline 趋势），`ContributionList` 替换 radar + bar 双重展示，`DataSourceStatus` 取代独立的 Data Sources Health 卡，watchlist premarket 改为 `DataTable`，history chart 改 200px line + 阈值虚线 + state 颜色 dot strip；删除 `🔎` / `📖` emoji 标题。
- [改进] dsa-web 侧边栏重写为 56 px 图标模式（`NavSidebar` 替换 `SidebarNav`），首页 `/` 重定向到 `/regime`；移除 `/`(HomePage)、`/chat`(ChatPage)、`/portfolio`(PortfolioPage) 三个路由（chat 功能并入 `⌘K`，portfolio 合并为 watchlist 过滤）。
- [chore] dsa-web 删除 light mode：卸载 `next-themes`，删除 `components/theme/ThemeProvider.tsx` + `ThemeToggle.tsx`，`index.html` 固化 `dark` class；依赖新增 `@tanstack/react-table` / `lightweight-charts` / `cmdk` / `sonner` / `react-hotkeys-hook` / `@fontsource/geist-sans` / `@fontsource/geist-mono`。
- [chore] dsa-web 删除 radar/gauge anti-patterns：`regime/DimensionRadar.tsx` / `ScoreGauge.tsx` / `ContributionsBar.tsx` / `DimensionBreakdown.tsx` / `DataSourcesHealth.tsx` / `MarketSnapshot.tsx` / `MacroAgenda.tsx` / `WatchlistHeatmap.tsx` / `MacroEventsPanel.tsx` / `RegimeScoreCard.tsx` + `common/ScoreGauge.tsx` + `common/ParticleBackground` 保留但标记待删 + 所有被 HomePage/ChatPage 独占的测试文件。
- [新功能] dsa-web 新增 `/design-lab` dev-only 页面（`Button` / `IconButton` / `Input` / `Tabs` / `Badge` / `EmptyState` / `Skeleton` / `Toast` / `DataTable` / `PriceCell` / `ChangeCell` / `Sparkline` / `StatBar` / `MASlopeCell` 一次性预览）；v1.1 cleanup 时移除。
- [chore] phase0 stage 0: 新增 `src/journal/`、`src/regime/`、`src/breakout/`、`src/options/`、`src/lab/` 五个模块骨架与对应 tests 目录；`src/core/config_registry.py` 注册 `phase0` 分类与 Journey/Regime/Breakout/Lab/Options 默认字段；`.env.example` 同步 Phase 0 注释；新增 `docs/phase0/README.md` 作为分 stage 索引。为 v4 改造后续 stage 1-12 铺路，不触发业务行为变化。
- [新功能] phase0 stage 1 (Options 基础层)：新增 `src/options/occ_parser.py`（OCC 变长 strike 解析 + 标准 8 位零填充兼容）、`src/options/black_scholes.py`（call/put/Greeks/IV 反推，日历日 theta，Hull 教材值对照）、`src/options/iv_rank.py`（yfinance ATM IV + HV fallback 百分位）、`data_provider/options_chain.py`（期权链抓取 + 内存 TTL + 可选 SQLite 持久化 + LEAP 候选筛选）、`src/options/storage.py`（`option_chains_cache` / `iv_snapshots` 两张缓存表幂等建表）。共 41 个单元测试，yfinance mock + scipy 真值对照。
- [新功能] phase0 stage 2 (Journal 核心)：新增 `src/journal/models.py`（7 张 `journal_*` 表：imports / orders / trades / shadow_trades / health_checks / monthly_reviews / phase_state，完全独立，不触碰原有 `portfolio_trades`）、`src/journal/brokers/moomoo_us.py`（Moomoo US 主行+fill-only 合并、ET 时区解析、OCC symbol 识别、external_id 幂等哈希）、`src/journal/storage.py`（幂等建表 + 三层去重 + 全量重配）、`src/journal/matcher.py`（FIFO 配对：分组 key 支持期权/股票、乘数 100/1、过度平仓翻方向、fee 分摊、DTE 分桶）、`src/journal/analytics.py`（Reality Test 灵魂指标 / DTE 分布 / 桶胜率 / 日度 Health Check）、4 个 CLI 脚本（init_journal_schema / import_csv / rebuild_trades / reality_test）。共 35 个单元测试（含端到端 CSV→FIFO→Reality Test smoke）。
- [新功能] phase0 stage 3 (Regime 核心)：新增 `data_provider/alpaca_fetcher.py`（Alpaca bars/news/premarket REST，无 key graceful fallback）、`data_provider/finnhub_fetcher.py`（经济日历/财报日历/分析师评级）、`src/regime/scorers.py`（6 维度纯函数：市场方向 / 波动率 / 宏观惩罚 / 板块 / 前日结构 / 盘前，范围固定）、`src/regime/classifier.py`（四档分类 aggressive/standard/cautious/no_trade + 入库）、`src/regime/fetchers.py`（Alpaca+yfinance+Finnhub 聚合，每 getter defensive）、`src/regime/storage.py`（`regime_scores` 表 upsert）、`src/regime/cli.py` + `src/regime/backfill.py`（单次 / 回补 90 天）。共 35 个单元测试。
- [新功能] phase0 stage 4 (Regime 晨报 + cron)：新增 `templates/regime_morning_brief.md.j2`（六维度 breakdown + 宏观事件 + warnings）、`src/regime/morning_brief.py`（format_brief / send_brief / CLI，复用 `TelegramSender`，未配置时 fallback 到 stdout）、`.github/workflows/regime_brief.yml`（双 cron 13/14 UTC + ET hour guard 防双推，workflow_dispatch 支持 `date` / `dry_run`）。共 6 个模板/推送单测。
- [新功能] phase0 stage 5 (Breakout Detector + 四层过滤)：新增 `src/breakout/detector.py`（range_high/range_low + 破前日高/低检测，数据源无关）、`src/breakout/volume_check.py`（Q3：量能倍数）、`src/breakout/timeframe_check.py`（Q4：多周期 price-vs-MA 对齐）、`src/breakout/rs_check.py`（Q5：相对 SPY 的 RS）、`src/breakout/filter.py`（Q1-Q5 短路决策树，各 Q 失败标 `rejected_at`）。共 16 个单元测试。
- [新功能] phase0 stage 6 (Breakout 历史回填)：新增 `src/breakout/retest_tracker.py`（real / fake / retest-continuation 窗口分析）、`src/breakout/backfill_trade_style.py`（纯规则把历史 trades 批量打 `breakout_chase/retest/pullback_buy/...` 标签）、`src/breakout/backfill_fake_breakout.py`（outcome-based 代理：短持 + 大幅亏损 = fake）。共 12 个单元测试。
- [新功能] phase0 stage 7 (Journal UI + API)：新增 `api/v1/schemas/journal.py` + `api/v1/endpoints/journal.py` 7 个端点（reality-test / trades 列表+详情+patch / health-check / stats / import），并注册到 `api/v1/router.py`；前端 `apps/dsa-web/src/types/journal.ts` + `api/journal.ts` + `stores/journalStore.ts` + 4 个 journal 组件（RealityTestCard / DTEDistribution / TradeTable / JournalImport）+ `pages/JournalPage.tsx` 四 tab（Overview / Trades / Reality / Import），`HomePage.tsx` 顶部集成 RealityTestCard，`App.tsx` 加 `/journal` 路由。7 个 FastAPI TestClient 单测；npm run lint + build 全绿。
- [新功能] phase0 stage 8 (Regime/Breakout UI + Today 首页)：后端新增 `api/v1/endpoints/regime.py`（today / history / recompute）+ `api/v1/endpoints/breakout.py`（signals 含 fake-only 过滤）+ `api/v1/schemas/regime.py`，router 注册；前端 `types/regime.ts` + `api/regime.ts` + `stores/regimeStore.ts` + `components/regime/RegimeScoreCard.tsx` + `components/regime/RegimeHistoryChart.tsx` + `components/breakout/BreakoutSignalsList.tsx` + `components/charts/TradingViewWidget.tsx`（tv.js 动态加载，`useId` 生成 container id）+ `pages/RegimePage.tsx`（路由 `/regime`）；`HomePage.tsx` 首屏双卡并排（RealityTest + Regime）。7 个新后端契约测试。
- [新功能] phase0 stage 9 (AI 月度复盘)：新增 `src/agent/prompts/monthly_retrospective.py`（5 段中文 prompt + 严禁 emoji/加油话的 system message）、`templates/daily_health_check.md.j2` + `weekly_reality_test.md.j2` + `monthly_retrospective.md.j2`、`src/journal/monthly_review.py`（compute_monthly_stats / generate_review / run 幂等 upsert 到 journal_monthly_reviews）、`scripts/generate_monthly_review.py`、`.github/workflows/monthly_review.yml`（每月 1 号 cron + workflow_dispatch）、3 个新 `/api/v1/journal/reviews/*` 端点、前端 `MonthlyReviewPanel` 组件 + Journal 页 reviews tab（react-markdown + remark-gfm 渲染）。4 个新测试，累计 171 backend passed。
- [新功能] phase0 stage 10 (Agent Skills)：新增 `strategies/option_trader.yaml` + `leap_explorer.yaml` + `trend_follower.yaml`（带硬规则 / 禁用词 / 字段契约）、`.claude/skills/option_trader/SKILL.md` + `leap_explorer/SKILL.md` + `trend_follower/SKILL.md`（Claude skill bundles，已进入索引）、`templates/option_decision.md.j2` + `leap_proposal.md.j2` + `trend_plan.md.j2`、4 个新 Agent tools（`get_regime_score_tool` / `check_breakout_tool` / `get_option_chain_tool` + `find_leap_candidates_tool` / `get_journal_snapshot_tool`）。8 个单测，累计 179 backend passed。
- [新功能] phase0 stage 11 (Folder watcher + bot 命令)：新增 `src/journal/folder_watcher.py`（watchdog 监听 `~/Daily-Stock-Inbox/`，CSV 前缀白名单 + sha256 去重 + Telegram 通知 + 启动 sweep）、`scripts/run_journal_watcher.py` 常驻入口、`bot/commands/journal_cmd.py` + `regime_cmd.py` + `phase_cmd.py` 三个命令并在 `bot/commands/__init__.py` 自动注册；`requirements.txt` 加 `watchdog>=3.0`。11 个新单测，累计 190 backend passed。
- [修复] phase0 audit 跨层 bug 修复：(1) `apps/dsa-web/src/api/journal.ts::importJournalCsv` 显式 Content-Type 会破坏 multipart boundary → 删除 header；(2) `src/journal/brokers/moomoo_us.py::compute_external_id` DST 切换会产生不同 id → 新增 `_canonical_ts` 转 UTC 再哈希；(3) `/api/v1/journal/import` 加 50 MB 限制 + 文件名 `Path.name` sanitise；(4) `/api/v1/regime/recompute` 加 60s 进程内冷却（429）；(5) `TradeTable::fmtDate` 对 naive UTC 字符串补 `Z` 避免本地时区误解释；(6) `TradingViewWidget` 用 `studies.join('|')` 稳定化 useEffect 依赖避免重挂；(7) SidebarNav 补 `/journal` + `/regime` 导航入口；(8) `regime/fetchers.py::get_prev_day_structure` 回溯窗口 6 天 → 14 天覆盖 3-day 假期。5 个 regression tests，累计 195 backend passed。
- [新功能] phase0 stage 12 (整合 + 退出评估)：README 顶加 Phase 0 banner 说明 Mirror 层已完工；`AGENTS.md` §3 补 Phase 0 新模块索引；`scripts/ci_gate.sh::syntax_check` 扩展覆盖新模块；新增 `docs/phase0/STAGE_12_INTEGRATION.md` + `PHASE_0_EXIT_REVIEW.md`（交付清单 / 硬性指标 / 问题修复列表 / 用户签字 checklist）。Phase 0 代码层面全部完成，待用户真实数据跑通后打 `v0.phase0` tag。
- [修复] 大盘复盘链路接入 `REPORT_LANGUAGE`：`REPORT_LANGUAGE=en` 时，A 股/合并复盘的 Prompt、章节标题、模板兜底文案与通知包装标题统一改为英文，避免出现英文正文外包中文标题的问题。
- [修复] `AGENT_MAX_STEPS` 在 orchestrator 多 Agent 模式下统一明确为“默认作为各子 Agent 的步数上限而非硬覆盖；TechnicalAgent 等高默认值 Agent 会被封顶、低默认值 Agent 保持原值；当用户主动调高（>10）时，再统一覆盖所有子 Agent 采用全局值”，同时修复用户设置 12 但 TechnicalAgent 仍以默认 6 步运行并报 "Agent exceeded max steps" 的问题（fixes #1026）
- [修复] Specialist（Skill）Agent 失败不再中断整个分析管线，改为与 intel/risk 相同的优雅降级策略
- [改进] Agent 超步数错误信息增加 AGENT_MAX_STEPS 调整提示，帮助用户自助排查
- [修复] **MiniMax-M2.7 模型连接测试支持** — 修复 LLM 通道连接测试在 MiniMax-M2.7 模型下返回 "Empty response" 的问题；增加了 `max_tokens` 上限（8→256）以容纳 MiniMax 思考过程，并添加 `content_blocks` 格式解析逻辑统一处理 MiniMax 响应格式差异。
- [修复] 移除 `HistoryItem` 与 `ReportSummary` 响应 Schema 中 `sentiment_score` 的 `ge=0/le=100` 约束（fixes #942）——历史库中存储的超范围负值或大于 100 的情绪评分不再触发 Pydantic ValidationError，历史列表与详情接口恢复正常返回。
- [改进] 后端股票名称解析改为优先复用前端 `stocks.index.json` 全量索引并懒加载缓存；纯后端/缺失静态资源场景静默降级回 `STOCK_NAME_MAP` 与原有数据源回退链路。
- [改进] Agent IntelAgent 新增公司公告搜索维度（上交所/深交所/cninfo）与主力资金流工具（get_capital_flow），修复 Agent 模式下公告和资金流数据经常缺失的问题
- [修复] webui_frontend.py 在 static/index.html 存在但 static/assets/ 缺失时发出明确警告，避免用户因 CSS/JS 资源缺失导致页面元素异常变大却无从排查
- [修复] `StockAnalysisPipeline` 搜索服务与社交舆情服务改为可选降级初始化：任一服务初始化异常时记录 warning 并以禁用状态继续运行，避免外部依赖抖动阻塞主分析链路与 SSE 进度回调。
- [文档] DEPLOY.md 和 deploy-webui-cloud.md 新增"UI 元素异常变大/布局错乱"排查步骤（重建 Docker 镜像或手动执行 npm run build）
- [文档] 补充飞书 Webhook 配置说明：强调 `FEISHU_WEBHOOK_URL` 是群通知必填项、`FEISHU_WEBHOOK_SECRET` 与飞书机器人「签名校验」必须两端同时启用或同时关闭、`FEISHU_APP_SECRET` 仅用于应用/Stream Bot 模式不可替代 Webhook；同步完善英文指南并在 `.env.example` 为相关配置项补充内联说明注释

- [新功能] 集成 Longbridge OpenAPI 作为美股/港股可选数据源；配置 `LONGBRIDGE_*` 后优先使用长桥获取日线与实时行情，YFinance / AkShare 兜底；未配置时行为与此前一致。长桥联调请使用 `tests/longbridge_live_smoke.py`（手动脚本，不参与 pytest 收集）。
- [文档] 澄清 README（中/英/繁）中长桥「首选 / 兜底 / 未配置不调用」的边界；`docs/README_EN.md` / `docs/README_CHT.md` 顶部导航与完整指南链接改为 `./` 相对路径，避免在文档子目录下解析错误；`LONGBRIDGE_PRINT_QUOTE_PACKAGES` 与代码及 `.env.example` 对齐为未设置时默认关闭。
- [修复] 港股名称获取失败问题 — 修复当主数据源字段缺失时无法正确回退到备用字段获取港股名称的问题（fixes #940）
- [修复] SSE 任务流断开时 CancelledError 被静默吞掉问题 — 修复 SSE 流中断时异常未向上抛出导致故障无日志可查的问题，现在正确 re-raise CancelledError（fixes #967）
- [修复] Agent SSE 流清理阶段静默吞掉后台执行器异常 — 流结束时后台任务异常现在正确记录并上报，避免错误无法感知（fixes #969）
- [文档] FAQ 补充 Ollama `OllamaException / APIConnectionError` 连接失败排障条目（Q12c），覆盖服务未启动、URL 配置错误、模型前缀缺失、模型未下载、远程防火墙等 5 个检查点
- [修复] 技能加载异常被静默吞没问题 — 在 ask.py、skills/aggregator.py、skills/router.py 的静默 except 块补充 logger.warning 日志，确保技能列表为空时有日志可查（fixes #970）
- [修复] SQLite 主写入链路现在对 `stock_daily(code,date)` 使用批量原子 upsert，并在文件型 SQLite 连接上默认启用 `WAL`、`busy_timeout` 与有限写入重试，降低批量分析和并发回写场景下的锁竞争与吞吐抖动，返回值中的“新增数”改为按本次真正插入窗口计算（并发场景不再把并行写入行误算入当前调用）。
- [修复] 优化多 Agent 与单 Agent 的预算护栏语义：当后续阶段/步骤剩余预算低于最小阈值（首阶段除外）时会主动跳过并进行降级处理；若当前已完成阶段可支持构建降级报告，则返回 `success=True` 并携带非空内容；否则返回 `success=False`、`content=""`；`run_agent_loop` 预算过低时当前仍返回失败降级语义（`success=False`、`content=""`），`AgentExecutor` 保持统一下游契约。


- [新功能] tushare支持港股查询 — 配置了tushare凭证的用户会调用hk_daily接口获取数据，如果用户tushare权限不够依然会出现数据查询异常的情况，和改造前直接抛出不支持的异常流程相同。
- [改进] TushareFetcher `get_chip_distribution` 对港股直接返回 `None`，不调用 `cyq_chips`（港股暂不支持筹码分布）。
- [测试] 补充 `get_chip_distribution` 获取筹码分布的单元测试。
- [改进] TushareFetcher `_normalize_data` 对港股（`hk_daily`）不再对 `vol`/`amount` 做 A 股手→股、千元→元 的缩放，与 Tushare 港股字段语义一致。
- [测试] 补充 `TushareFetcher._normalize_data` 港股与 A 股/ETF 单位处理的单元测试。
- [新功能] 集成 Anspire Search 作为可选语义搜索后端; 配置 `ANSPIRE_*` 可使用Anspire Search获取实时行情及新闻资讯，未配置时行为与此前一致。Anspire Search请使用 `tests/test_anspire_search.py`（手动脚本）。
- [修复] GitHub Actions `daily_analysis.yml` 未注入 `REPORT_LANGUAGE` 环境变量，导致用户在 Secrets/Variables 中配置后不生效（fixes #1013）
- [修复] `GET /api/v1/analysis/status/{task_id}` 从数据库回填已完成任务时缺少 `current_price` / `change_pct`，导致首页报告股票名旁不显示实时价格（fixes #983）
- [新功能] 新增 Moomoo OpenAPI DB-aware `plan` / `confirm` 与 Web 确认交互：plan 零证据行写入并展示覆盖率、identity provenance、canonical 影响和 blockers；partial window 必须显式确认，stale preview 固定拒绝（服务首次初始化可能创建缺失 schema）。
- [改进] OpenAPI canonical persistence 只使用目标账户最新 accepted CSV batch，旧 CSV 仅保留审计且不参与本次输入；order/fill/fee observations、order/deal links、fill-set attestations 和 canonical set 以单事务 append-only 追加，相同 export 重复确认幂等，且不写 legacy Journal、不触发 Episode rebuild、不执行交易动作。
- [改进] OpenAPI plan 与 confirm 统一累积最新 CSV 基线下的全部 accepted API batches；不同窗口可连续追加，重叠窗口复用既有 order/deal identity proofs，避免重复 link。
- [测试] 正确数据的零证据行写入 plan 与隔离库幂等验收覆盖 1,089 / 3,542 个订单、窗口外 2,453 单；首次计划新增 1,089 order、2,107 fill、1,058 fee、1,089 order links、1,136 deal links、244 fill-set attestations，canonical 输出 3,542 orders / 5,400 fills，重复确认新增计数为 0；另覆盖连续不相交窗口与重叠窗口。上述结果不代表正式 Journal 数据库已导入。
- [文档] 将 Phase 1.3 专题和 Moomoo 路线图更新到 1.3B，记录只读 plan/confirm、最新 CSV 基线、原子幂等边界、局部覆盖结果，以及 OpenAPI confirm 与 Episode rebuild 的隔离关系。
- [新功能] 新增冻结 canonical set 到 PositionEpisode 的显式 preview/confirm：重放已保存 members 而不重跑身份推断，以 append-only、幂等方式生成不自动替换默认 CSV build 的对比构建；`assumed_flat_unverified` 仍需独立确认，aggregate submitted amount 不作为执行现金流。
- [文档] 新增 `New-docs/phase1/04_CANONICAL_EPISODE_BUILD.md` 并同步 README、产品路线图和 Moomoo 路线图，记录 574 aggregate + 5,400 fill events、1,441 个 signed-position lifecycle 与 USD 151,750.75 费用守恒基线，明确该算法不是 FIFO 且项目永久只读。
- [新功能] 新增 `/journal/review/:episodeId` 单合约复盘工作台：保留 `build_id` 与仓位列表筛选/页码上下文，将底层 K 线与 execution evidence 联动，明确区分真实 fill、订单时间代理和无法映射的行情缺口；期权 premium 不混入底层价格轴。
- [改进] 股票历史行情响应追加可选 `source`、`coverage_start`、`coverage_end` 与 `last_bar_at`；单合约复盘仅在最近 60 天且 5 分钟 bars 覆盖全部 evidence 时使用分钟视图，否则明确降级日线并禁止精确入场、MFE/MAE 结论，空行情不伪造 provenance。
- [修复] Moomoo 分钟线将无 offset 的 `time_key` 按 US 美东时间或 HK/中国内地北京时间补入含夏令时的 ISO 时区，并升级 Web 会话缓存版本，避免浏览器复用旧时间语义而把真实成交证据错误降级为日线。
- [文档] 新增 `New-docs/phase1/05_SINGLE_POSITION_REVIEW_WORKSPACE.md`，记录单合约复盘路由、构建/返回上下文、K 线/evidence 联动、行情覆盖与降级、`assumed_flat_unverified` 和永久只读边界；本次细节未重复写入根 README。
- [新功能] PositionEpisode 列表新增只读 `case_focus=top_profit|top_loss|largest_fee|longest_hold`：缺省仍按开仓时间最新优先，盈亏聚焦仅纳入已闭合且净损益已知的 Episode，并与现有 build、标的、状态、完整度和分页条件取交集，不构建或改写交易事实。
- [新功能] 股票 history 新增显式派生的 `period=2m`：底层读取带时区 1 分钟 K 线，按市场本地日期聚合连续 2 分钟 OHLCV，响应通过 `derived_from_period=1m` 与 `aggregation_method=time_bucket_2m_ohlcv` 声明来源，空数据也不伪装为原生周期。
- [测试] 新增 PositionEpisode 聚焦排序、筛选交集与 OpenAPI schema 契约，以及 2 分钟行情 OHLCV、缺分钟/时段缺口、DST、空响应和 API 映射回归测试。
- [修复] Moomoo 分钟历史改为沿 `page_req_key` 安全读取全部分页，合并后按 `time_key` 去重排序；后续页错误、重复 continuation key、异常空页或超过 128 页均拒绝返回部分覆盖，同时每页保持美股盘前盘后参数。
- [新功能] 单合约复盘新增 1m/2m/5m/1h/日线显式切换与按需 AI 辅助复盘；AI 固定绑定当前 Episode/build，只接收去标识化执行与市场事实，返回事实/推断/未知边界、SPY/Regime 上下文和 provenance，生成文本不持久化且 Moomoo 永久只读。
- [修复] 复盘 K 线买卖 marker 改为依据 `allocated_cash_flow` 正负，而非用开仓/平仓角色猜测方向，避免空头回合方向反转；手动周期无 bars 或未覆盖全部 evidence 时明确显示不可用，不静默换周期或重新贴标签。
- [测试] 新增案例精选路由上下文、多周期控件与 1m/2m 显式请求、5m→日线自动降级、2 分钟派生行情、现金流买卖 marker、显式 build AI 端点、去标识化 prompt、行情/Regime 缺口与 LLM 不可用降级回归测试。
- [文档] 扩充 `New-docs/phase1/05_SINGLE_POSITION_REVIEW_WORKSPACE.md` 并同步文档索引和产品路线图，记录案例精选、多周期 provenance、现金流买卖语义、AI 输入/端点/失败边界、条件 P&L 与单笔 edge 护栏；专题细节不重复写入根 README。
- [修复] Moomoo 分钟行情按 `page_req_key` 完整读取请求窗口并对重叠 bar 去重排序；后续页失败、重复 continuation key 或超安全页数时拒绝返回部分数据，Web 同步延长只读行情等待时间并失效旧的单页缓存。
- [修复] 按需 AI 复盘把 LiteLLM 适配器的失败文本或缺少必需章节的截断输出识别为不可用状态，不再把内部连接异常/残缺文本冒充模型分析或回传给页面；同时将 LiteLLM 第三方日志降至 WARNING，避免完整复盘 prompt 写入 DEBUG 日志造成日志洪泛。
- [新功能] 单合约复盘 K 线新增原生 `15m` / `30m` 显式切换，并在七档周期上叠加由底层 close 计算的 EMA8 / EMA13；EMA 以首个完整周期 SMA 为 seed 后按标准系数递推，bars 不足时不补值，期权 premium 仍不进入底层价格轴。
- [改进] 按需 AI 复盘并行读取底层与 SPY、跳过无关名称/实时报价补取，并为成功非空行情增加 180 秒、最多 32 项的只读短缓存；每个标的的模型输入收敛到最多 24 根 OHLCV，LLM fallback 共用 30 秒路由预算且单模型最多 15 秒，并用 35 秒服务端墙钟硬截止兜住不遵守 timeout 的供应商 SDK；LiteLLM 默认使用本地 cost map，超时或章节不完整继续返回确定性市场上下文，不缓存或持久化 AI 文本。
- [文档] 更新 `New-docs/phase1/05_SINGLE_POSITION_REVIEW_WORKSPACE.md`，记录七档原生/派生周期、EMA seed 与递推口径、AI 延迟预算/缓存边界，以及 Moomoo 永久只读、不下单和条件 P&L 护栏；专题细节未重复写入根 README。
- [修复] 单合约复盘分钟图默认按纽约常规时段 09:30–16:00 ET 过滤，并可显式切换含盘前盘后的 04:00–20:00 ET；EMA8/13 基于当前时段重算，修复扩展时段混算导致与常见行情软件口径明显不一致的问题。
- [修复] 复盘图表横轴与十字光标不再把 Unix 时间按 UTC 展示，统一使用 `America/New_York` 并自动处理夏令时；历史最终 bar 的 close/EMA 明确不得冒充成交瞬间已知信号。
- [改进] 复盘助手拆分为无需外部模型的本地证据复盘与可选模型增强；模型失败时返回完整的事实结论、成交结构、相对 SPY、证据缺口和补充问题，模型等待预算收敛到单模型 10 秒、路由 16 秒、服务端墙钟 20 秒。
- [新功能] 新增 `JOURNAL_AI_MODEL` 与 `JOURNAL_AI_FALLBACK_MODELS` 复盘专用模型链，支持继承 Agent 链、接续去重和显式空 fallback；OpenAI/GPT 仍要求服务端 API Key，不复用 ChatGPT/Codex 登录态。
- [文档] 同步根 README、产品章程与 Phase 1.5 工作台说明，记录 EMA 时段/ET 口径、真实 Episode 数值核对、证据复盘/模型增强边界和后续 ReviewAnnotation 方向。
- [新功能] 单回合复盘新增六字段交易逻辑草稿，严格分开进场前计划与事后记录；草稿按 build/episode 仅存浏览器，本次复盘只发送非空字段，后端将其标记为未经独立验证的用户自述且不写 Moomoo 或证据账本。
- [修复] 分钟级复盘收益代理不再读取成交所在未完成 bar 的最终 close，改用成交前最近已完成且仍新鲜的 bar；模型行情切片增加 finalized/availability 边界，Regime 缺少生成 as-of 时明确保持开仓可见性未知。
- [改进] 复盘 API 始终返回不可被模型覆盖的 `evidence_markdown`，模型成功时另返独立 `model_analysis_markdown`；用户补录明确标为事后回忆并限制总计 6,000 字，默认行情读取增加每路 8 秒截止与全局有界降级。
- [新功能] `/regime` 新增“今日机会研究”确定性候选榜与 `POST /api/v1/opportunities/daily`：扫描本地自选或服务端 `STOCK_LIST`，使用上一完整交易日量价、EMA8/13 和已保存 Regime，按研究状态、证据支持与完整度稳定排序；逐项展示来源、时间、反证、未知项和 readiness，不调用 LLM、不输出伪精确总分、不执行交易。
- [修复] Moomoo 期权链按官方语义改为先取静态合约 code，再以每批最多 400 个 code 合并动态 market snapshot；bid/ask、成交量、OI、IV 和 Delta 不再从静态链行读取或被伪装为全 0 实时行情。
- [改进] 每日机会扫描改为单请求复用行情 manager、最多四路并发、30 秒服务端 TTL 与 single-flight；前端增加 5 分钟缓存和最新请求保护，减少重复初始化、日志洪泛和旧 watchlist 响应覆盖；未配置的可选 Tushare 数据源降为 debug 能力状态，行情 fallback 只保留一次最终 warning，并抑制 yfinance 已由应用汇总的重复 ERROR。
- [修复] 每日机会固定只使用美股 T-1 或更早完整日线，非美股候选明确 blocked，过期日线/no_trade 降为仅背景；成交额代理不再重复抬高支持证据，UI 分开显示基础量价覆盖与总体数据域覆盖。
- [修复] Moomoo 动态期权快照拒绝 NaN/inf、无效 `option_valid` 与缺字段合约，ATM IV 改为精确单合约快照并在不完整时 fail closed；DTE 统一按纽约市场日期，查询与 context 生命周期使用同一锁。
- [文档] 新增 `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 并同步产品路线图，记录 Moomoo v10.9 异常期权事件、本机 10.4 能力差距、FINRA 延迟暗池背景、可选 TRF 数据源、TradePlan 与 5d/20d 结果闭环顺序；专题细节不重复写入根 README。
- [新功能] 每日机会榜新增独立 `POST /api/v1/opportunities/option-context` 渐进增强：基础候选先显示，再为前三个合格美股 underlying 读取 Moomoo 最近到期 ATM Call 单点 IV；未启用、缺数据或请求失败均逐标降级且不影响基础榜，并明确不是 IV Rank、异常期权流或买卖信号。
- [改进] 期权上下文按标的、Moomoo 启用状态和 ET 日期使用 30 秒服务端 TTL/single-flight，重叠批次复用已读标的；前端增加 5 分钟缓存、30 秒独立超时、手动刷新和旧响应保护，未进入首批的候选明确标记为未扫描。
- [修复] Moomoo QuoteContext 在异步 READY 后强制设置 5 秒同步查询连接等待上限，SDK 缺少或拒绝该安全设置时关闭 context 并 fail closed，避免 OpenD 刚断线时同步查询无限重连占住服务端线程。
- [测试] 新增 ATM IV 上下文启用/禁用、百分比换算、逐标降级、重叠批次缓存、混合市场过滤、前端加载/刷新/竞态与 Moomoo 同步连接截止回归；机会/Moomoo 后端相关测试 70 项通过，前端组件定向测试 8 项通过。
- [文档] 更新每日机会专题与产品路线图，记录 ATM IV 已交付范围、缓存/超时边界、不得从单点 volume/OI 推断大单，以及后续流动性上下文和 Moomoo 10.9 真异常流接入顺序；专题细节未重复写入根 README。
- [新功能] 每日机会研究新增只读 `POST /api/v1/opportunities/option-walls`：默认按 0–45 DTE 标准合约返回 Top 3 Call/Put OI、当日累计 Volume 与 unsigned gross gamma concentration 墙，并携带覆盖率、市场日期、抓取时间、排除项和逐标降级状态；墙数据不参与基础候选排名。
- [改进] 期权墙以 `abs(gamma) × OI × contract_multiplier × spot² × 0.01` 计算标的变动 1% 时的近似美元 Delta 对冲名义变化；明确不称 Dealer GEX、不推断做市商方向、不计算 gamma flip，UI 使用表格加详情展示可复现口径、as-of 和“策略有效性未验证”。
- [文档] 更新每日机会专题，记录 Moomoo 10.4 已可支持基础期权墙、10.9 `get_option_event` 仍是下一步只读验收，以及当前尚未持久化墙历史或完成结果回测；专题细节不重复写入根 README。
- [改进] Web 全局视觉从近纯黑与高饱和紫色收敛为 charcoal 层级、单一研究蓝与更清晰的文字/边框对比；cyan/purple 仅保留为多序列图表色，并同步设计系统真源。
- [修复] 期权墙将任一静态链日期窗口失败计入覆盖失败，避免剩余窗口的快照 100% 被误报为整段 DTE 完整；机会表同时将混合指标列更名为“数据完整度”并移除成交额倍率的美元符号误导。
- [测试] 机会榜/Moomoo 只读相关后端定向测试 89 项、前端机会组件测试 12 项通过，并完成 Web lint、正式构建和真实 OpenD 期权墙浏览器交互验收。
- [新功能] 每日机会研究新增只读 `POST /api/v1/opportunities/option-events`：在 Moomoo OpenD 10.9.6918 / Python SDK 10.9.6908 上按 underlying 读取最近异常期权成交，逐标返回成交权利金、盘口、IV/Delta 与 Moomoo 方向/情绪/成交类型分类；页面在当前候选详情中渐进展示，失败不阻断基础榜，全链路不解锁或调用交易接口。
- [改进] 异常期权成交按标的使用 30 秒服务端 TTL/single-flight、请求数量上限与前端旧响应保护，并通过独立只读 QuoteContext lane 避免被大型期权墙扫描阻塞；明确供应商分类不能证明开平仓、真实主动买卖方、参与者目的或 dealer 定位，事件暂不进入候选排名、不持久化且无历史回测。
- [文档] 更新每日机会专题，记录 Moomoo 10.9 只读升级验收、`option-events` 字段与时效口径、官方限频和 UI 用途，并将异常成交的证据边界与后续样本外验证顺序固化；专题细节未重复写入根 README。
- [修复] Moomoo 环境变量示例、订阅说明与旧 LaunchAgent 注释不再建议解锁或模拟/真实交易，也不将 `LIVE` 当作执行开关；`LIVE` 仅表示只读真实账户历史，旧 writer 仍固定暂停。
- [新功能] 每日机会研究新增显式不可变快照与 5/20 XNYS 交易日结果 API：`snapshots/freeze`、`snapshots`、`snapshots/{snapshot_key}/evaluate` 和 `learning-summary` 分别冻结当前基础榜、只读列出进度、追加已到目标日结果与返回受门槛保护的描述统计；普通 `/daily` 预览继续零写入。
- [新功能] 新增 `opportunity_snapshot_runs`、`opportunity_snapshot_candidates`、`opportunity_candidate_outcomes` 三张隔离的 append-only 表和 UPDATE/DELETE 拒绝 trigger；run/candidate 原子写入、相同重试幂等、冲突快照拒绝，partial 与 complete 结果只追加不原地修订，本地升级前数据库备份继续由 `data/` ignore 规则排除于仓库。
- [改进] 结果评估以 `exchange-calendars` XNYS 日历固定 `S close < freeze < E open` 因果窗口，分别保存冻结 close 与 next-open proxy 的 5D/20D 标的收益、MFE/MAE 和相对 SPY；5D 使用 ±0.5%、20D 使用 ±1.0% 的方向上下文阈值，mixed/unknown 不生成 signed 指标，参考复权或来源连续性无法核对的样本排除于学习。
- [改进] 学习摘要严格隔离 signal/playbook/universe/结构 setup/Regime/direction/horizon cohort：须同时满足 20 个已回填方向样本和 20 个独立 signal sessions 才显示描述命中率并允许人工调查；不足门槛不返回 hit/miss/neutral 细分，不生成 TP/FP、missed opportunity 或 regime mismatch，不把 underlying proxy 冒充期权收益，且任何样本量都不会自动调整排名权重。
- [测试] 新增机会快照日槽幂等/冲突与 SQLite 不可变 trigger、S-close/E-open 门禁、XNYS 节假日、5D/20D 目标日状态、close/next-open/MFE/MAE/SPY、mixed/unknown 空 signed 字段、来源/复权缺口、API 显式写入边界和学习样本门槛回归测试。
- [测试] 离线门禁不再受本地 ignored `.env`、固定历史日期或手工联网诊断脚本影响，并补齐 requirements 已声明的 JSON repair 本地测试依赖；真实行情、LLM 与通知诊断只在 `network` 测试中执行。
- [文档] 扩充每日机会专题，记录三表/API、因果冻结窗口、5/20 结果公式、严格 cohort、样本门槛和永久不自动调权边界；专题细节未重复写入根 README。
- [新功能] 每日机会研究接入 Moomoo Python SDK 10.9 的只读 `get_option_underlying_overview`：通过 `POST /api/v1/opportunities/option-overview` 为全部合格候选批量展示供应商 IV、IV Rank/Percentile、HV，以及 Call/Put Volume 与 OI 概览；任一标的失败均独立降级，全链路不解锁、不下单。
- [新功能] 新增 `/regime/opportunity/:ticker` 专业单票研究页，集中展示 1m/2m/5m/15m/30m/1h/1D underlying K 线与 EMA8/13、上一完整交易日结构、IV/HV、0–45 DTE 墙和 Moomoo 异常成交，并保留来源、抓取时间和不可推断边界。
- [改进] 每日清单改用 `snapshots/ensure` 在合法 XNYS 盘前窗口幂等保存当日第一份研究版本，周末、休市、盘中和盘后只更新页面而不生成事后样本；UI 移除需要用户理解的“冻结今日研究”主操作。
- [改进] 机会榜和单票详情按数据域明确时钟：Volume 是当前交易时段累计，OI 是上一清算日 T-1，IV 只表达隐含波动幅度而不表达方向，unsigned Gross Gamma 仅是公开 OI 的集中度代理、不是 Dealer GEX 或 gamma flip。
- [文档] 更新每日机会专题，记录全候选期权概览、专业单票研究页、盘前自动保存、七档 K 线/EMA8/13，以及 OI、Volume、IV、Gross Gamma 和异常成交的时点与解释限制；细节未重复写入根 README。
- [新功能] 单票机会页新增首屏专业摘要与 1/5/20 交易日 IV 模型终值区间，以简化 lognormal 假设分别展示约 68%/95% 理论覆盖区间；明确它们不是方向预测、真实胜率、目标价或盘中触及概率。
- [改进] 单票机会页压缩重复内容，将波动率、期权墙和异常成交归并为按需切换的标签页；首屏只保留 setup、关键价位、主要反证、未知项和图表，完整 provenance 与限制留在对应明细域。
- [文档] 更新每日机会专题，固化专业摘要的信息层级、IV 模型区间公式与缺失值降级，以及 68%/95% 终值区间不得被解释为策略胜率或路径概率的边界；专题细节未重复写入根 README。
- [修复] 单票机会页的美股分钟 K 线默认仅显示纽约常规时段 09:30–16:00 ET，并可切换含盘前盘后的 04:00–20:00 ET；EMA8/13 始终基于当前可见 bars 重算，数据时点同步取当前可见最后一根，日线隐藏时段切换。
- [修复] 每日机会扫描在已启用 Moomoo OpenD 时优先读取本地美股日线，再回退 Yfinance/Longbridge；基础请求收敛为 35 秒独立预算，手动刷新同时绕过前后端已完成缓存，不再因远端限流长期停在“扫描中”。
- [改进] 今日机会研究将基础扫描、市场背景与排名外增强分层：`unknown` 不再当失败门禁，Playbook/异常期权成交/期权墙/暗池背景不再重复冒充核心证据缺口；行情阻断显示具体原因，刷新失败时保留上一份可用列表。
- [修复] Regime 新增 `regime-quality-v1` 快照质量合同：空 Sector/昨日结构/盘前不再产生 −5/−2/+3 伪分，SPY/VIX 核心缺失时 fail closed 且不展示成真实 no_trade，API/Web 明确 degraded、缺失域与非权威边界；今日日期统一为纽约市场日。
- [改进] Regime 重算改为 Moomoo-first 有界取数：SPY/11 个板块/昨日结构复用本地只读日线，VIX 缺少 Moomoo 覆盖时使用 3 秒 Cboe 官方 CSV fallback；Finnhub 合并为两个 7 日区间请求并识别 403/timeout，Alpaca SPY 失败停止逐股扩散，避免远端串行请求令页面超时或把权限失败伪装成零事件。
- [修复] Regime 仪表盘色带与后端 35/55/75 分档阈值重新对齐，degraded 状态改为 `CONTEXT ONLY · PROVISIONAL` 且不再给出执行指令；重算同时刷新 UTC 证据时间，Web 始终按纽约时区显示，修复本地时间误标为 ET。
- [新功能] 单合约复盘新增服务端 ReviewAnnotation v1 与 Review Queue：用户可显式保存六字段自述、标签和错误类型，按绑定 immutable build + PositionEpisode 的 append-only revision 查看历史，并从未开始/进行中/已完成计数、筛选和行状态继续复盘；相同最新内容幂等，AI 不自动写入，annotation 不修改券商证据、canonical、Episode 经济字段或 P&L。
- [修复] DatabaseManager 冷启动改为并发安全初始化，避免首批并发请求取得缺少 `_engine` 的半初始化单例；Journal v2 首次建表与 append-only trigger 安装同步串行化。
- [新功能] 新增服务端 5D/20D 机会结果自动维护：按精确 XNYS 收盘后 30 分钟首次检查、收盘后 4.5 小时最多重试一次，使用数据库租约和两次 attempt 上限支持多 worker、重启及周末接续，并避开 08:45–09:30 ET 官方盘前研究窗口。
- [改进] 结果维护先用本地 XNYS 日历筛出真正到期或 partial 的冻结快照，未到期时零行情请求；同轮跨快照共享 ticker/SPY 历史缓存，冻结快照和结果继续 append-only，运行租约只作为可恢复的可变协调状态。
- [修复] Alpaca 盘前涨跌改用纽约 04:00–09:30 内已完成且新鲜的 1 分钟 bar，并以精确上一 XNYS session 收盘价为分母；未来日期、缺前收、前收日期错误、未完成分钟或过期行情全部降级，不再把未知编码为 0%。
- [修复] Alpaca 股票 bars 保持空列表兼容的同时记录请求状态，Regime 能区分 401/403 权限、429 限频、timeout 与真实空分钟，不再把供应商拒绝误报为 `no_completed_premarket_bar`。
- [新功能] 新增只读 Moomoo 专用盘前适配器：固定读取未复权的 04:00–09:30 ET 已完成 1 分钟 bar，以精确上一 XNYS session 的 `last_close` 为基准；真实 SPY 冷调用字段验证通过，但约 32 秒且同步 SDK 不可安全取消，因此暂不接入正式 Regime fallback。
- [改进] Regime 同轮 Alpaca SPY/watchlist 盘前请求冻结同一 `as_of`，并持久化 `_attempted_sources`、`_reason` 与具体 provider reason；Moomoo 整篮子 fallback 延后到可终止独立进程生成 append-only 盘前 artifact 后启用，不能用跨交易日 last-good 或共享线程 worker 替代。
- [测试] 新增 Alpaca 403 状态与 Regime reason 透传、Moomoo 未复权/扩展时段/未完成分钟/5 分钟 freshness/`last_close` 证据回归，并完成本机只读 SPY 字段和延迟冒烟。
- [修复] Finnhub 经济日历与财报日历分别记录 readiness；一个子域无权限或失败时只屏蔽对应证据，另一个成功子域继续参与 Regime，旧 degraded 载荷仍保守不计分。
- [修复] 单合约复盘证据 marker 不再把首根 K 线之前的成交前推到未来 candle；分钟图只映射到同一纽约交易日、同一盘段且不晚于证据时间的 bar，并覆盖开盘前与收盘边界回归。
- [改进] 设置页与 `.env.example` 补齐 Alpaca、Finnhub 和结果维护开关；学习面板展示服务端自动回填状态，手动操作收敛为“重试到期缺口”。
- [文档] 新增期权研究数据/API 配置指南和自动结果维护合同，区分已接入、免费增强、候选未接入及付费来源，记录 Moomoo/Alpaca/Finnhub/OpenAI 权限、冒烟测试、统计门槛、运维与回滚。
- [测试] 新增结果维护并发租约、失败恢复、早收盘、保护窗口、到期前零 provider、跨快照缓存、20 日统计门槛、Alpaca/Finnhub partial readiness 与复盘 marker 会话边界回归。
- [改进] Top 5 期权墙由逐标串行改为最多五条彼此隔离、可复用且逐次独占的 Moomoo QuoteContext lane 并发读取，继续保留逐标 30 秒 TTL/single-flight、请求顺序、真实 coverage 与失败降级；本机同组冷缓存从 30.47 秒降至 9.64 秒且 10,374/10,374 张合约完整返回。
- [测试] 新增 Top 5 墙位五路重叠与响应顺序、QuoteContext lane 独占/复用回归；期权 Moomoo 快照与机会 API 定向测试 68 项通过。
- [文档] 每日机会与期权数据接入指南补充墙位并发边界、Moomoo 单次 400 代码/30 秒 60 次快照额度、真实计时证据及避免多页面重试风暴的操作说明。
- [新功能] Journal 新增默认关闭的服务端 OpenD 只读刷新：从最新可信水位自动选择重叠窗口，将去标识化结果冻结为短期 server-owned artifact，网页先预览 overlap/tail/阻断再显式发布；preview 零业务证据写入，所有响应固定声明零交易动作。
- [修复] Moomoo 对账区分历史 overlap 冲突与严格晚于旧 cutoff 的 authoritative incremental tail；完整查询但零成交现在也能推进 broker 查询水位，同秒新增记录依赖 broker 稳定 ID 去重，账户 HMAC 与窗口连续性不成立时 fail closed。
- [新功能] Canonical PositionEpisode build 新增 append-only 显式激活：构建成功不自动切换默认复盘，激活以 build key 和当前 activation/build compare-and-swap，重复请求幂等、A→B→A 回退保留完整历史，并再次要求确认 `assumed_flat_unverified` 边界。
- [改进] Journal“交易证据”首屏改为每日只读刷新状态机，分开展示券商查询水位、最新成交、证据发布与当前复盘版本；CSV/JSON 收入首次导入高级区，刷新确认后联动更新 Data Health 和仓位复盘。
- [测试] 补齐只读空窗口、账户 binding、合法 incremental tail、overlap 冲突、窗口缺口、server-owned preview/confirm/幂等发布、构建激活/CAS/append-only 与 Web 刷新交互回归。
- [文档] 新增 `New-docs/phase1/10_JOURNAL_READONLY_REFRESH.md`，记录每日只读刷新配置、四阶段状态机、连续性门禁、水位、激活与剩余 contract multiplier/opening snapshot 限制；专题细节未重复写入根 README。
- [修复] Moomoo 只读成交导入在稳定订单/声明组合腿精确关联且父单一秒内完成更新时，允许带审计告警的亚秒级成交/订单时间反序；保留原始时间，超出护栏或其他证据不一致仍保持阻断。
- [改进] Journal 只读刷新保留并结构化识别 Moomoo `strategy_type/combo_legs`，且要求非空 OpenD 订单响应证明字段能力；组合父单按逐腿代码、方向和数量比例核对，并按 CSV 基线前、重叠区和增量尾部分别给出执行组投影阻断，不再误报为普通单腿代码、方向、数量与 VWAP 冲突或静默伪装成股票。
- [改进] Journal 只读预览将缺逐笔成交、孤立成交、数量/标的/方向/均价差异与缺券商费用等对账 warning 映射为面向交易复盘的中文原因，不再直接展示内部字段名。
- [修复] Episode continuity fence 改为在显式 SQLite `mode=ro` / `query_only` 读事务中统一读取全部证据，严格重验 snapshot artifact/provenance/member、OpenD refresh artifact/accepted batch/publication 与 frozen canonical root/member/source/group/multiplier 投影，并按 OCC semantic identity 判断边界，避免并发混读、无效刷新、raw observation 旁路及 strike 零填充造成假 `ready`。
- [文档] 明确 continuity fence 的“零写”只承诺零业务表与零 Schema 写入；SQLite 在 WAL 模式下仍可能为读取协调创建或维护 `-wal` / `-shm` sidecar，不承诺数据库目录在文件系统层面完全无字节变化。
- [文档] 新增 `New-docs/HANDOFF.md` 总交接手册，统一记录产品目标、证据架构、正式库与运行快照、Moomoo 永久只读边界、Web/API 日常流程、Git/GitHub 同步真相、验证矩阵、风险、故障排查和分阶段验收路线；专题细节未重复写入根 README。
- [修复] 前端图表 EMA 统一为共享 SMA-seeded 实现 `apps/dsa-web/src/utils/ema.ts`：机会详情页原从首 close 递推的 overlay 改为与 Journal 复盘 overlay 及后端 `_ema_last` 同一语义（前 period 根 close 均值作 seed 落在第 period 根 bar，样本不足 fail closed），消除同一标的两处图表 EMA 起始段可见差异。
- [测试] 新增 `utils/__tests__/ema.test.ts`：手算小样本、后端 `_ema_last` 同输入 parity fixture、seed 位置与不足样本/非法 period fail-closed 回归；机会榜测试补数据时点提示断言。
- [修复] 每日机会榜数据时点提示不再把基础相对量列总括为“Volume＝本交易日累计”：现明确“相对量能＝上一完整交易日（相对之前 20 个 session）”，仅期权 Call/Put Volume 标注为当前交易日累计。
- [新功能] 新增只读端点 `GET /api/v1/opportunities/snapshots/{snapshot_key}`（`opportunity-snapshot-detail/1.0`）：返回单个不可变机会快照 summary 与冻结 run payload 原文；key 格式不合法或不存在返回 404，存储异常返回 503，均不触碰冻结证据。
- [新功能] 机会详情页与官方榜单实现同版本证据绑定：canonical 榜单跳详情携带 `?snapshotKey=`，详情页优先读取冻结候选与冻结信号日并显示“官方快照 … · 冻结于 …”；快照缺失或不含该标的时显式提示并回退即时扫描，无绑定时明确标注“即时扫描 · 未绑定官方快照”，preview 榜单不伪装官方绑定。
- [测试] 后端补 snapshot detail 端点 200/404/非法 key fail-closed 测试；前端补 canonical 深链携带 snapshotKey、详情页官方绑定不再触发即时扫描、快照不可用回退提示与无绑定标注共 4 个回归。
- [修复] `test_iv_rank.py` 补 autouse fixture 禁用 Moomoo IV 优先路径：此前套件中任一测试触发 `get_config()` 加载 `.env` 后，本机 `MOOMOO_OPEND_ENABLED=true` 会让 `compute_atm_iv` 单元测试打到真实 OpenD 实时期权报价（盘中间歇性失败且违反 `-m "not network"` 封闭性）；现在 yfinance fallback 被确定性隔离测试。
- [新功能] 应用日志新增跨日期 retention：`LOG_RETENTION_DAYS`（保留天数）与 `LOG_RETENTION_MAX_TOTAL_MB`（同前缀总体积上限）在 `setup_logging` 时清理按日期命名的历史日志；默认 0＝关闭不改变现有行为，只匹配本前缀 `*_{YYYYMMDD}.log` 及轮转备份，当天文件与 `logs/archive/` 归档目录永不受影响，删除失败仅告警不中断启动。
- [测试] 新增 `tests/test_log_retention.py` 7 项安全边界回归：默认关闭零删除、只删同前缀过期日期文件、不递归 archive 子目录、体积上限从最旧删起且不动当天、unlink 失败继续、环境变量畸形值按关闭处理。
- [改进] 机会详情页官方绑定时新增「冻结与当前差异」条：显示冻结基准 close（信号日收盘）与当前 spot（Moomoo 时点）的百分比变化并注明不改变冻结榜单结论；任一数值缺失整条隐藏，即时扫描页不显示，补 2 个前端回归。
- [新功能] 新增 `GET /api/v1/system/health-layers` 分层健康端点：api 进程、OpenD TCP、Moomoo SDK、Journal 刷新配置就绪度、盘前官方发布水位、outcome 维护水位六层独立只读探测；单层失败只降级该层不 500，secret 永不回显，补 4 项端点回归。
- [新功能] TopBar 新增「健康」分层弹层：点击按需拉取 health-layers 并按 ok/降级/不可用/未启用着色展示各层与说明；接口失败显示有界错误文案，后端新增未知层按原名展示；补 3 项组件回归。
- [文档] 新增 `New-docs/phase1/12_FORMAL_FUTURE_BUILD_CONTRACT.md`：阶段 B 正式 future build 的切片划分与切片 1 写合同设计冻结稿（端点、CAS 重验序列、snapshot-fence link 表、preview in-session 重构、T1-T9 测试合同、activation 扩展的已知 schema 约束）；实现尚未开始。
- [新功能] Journal 新增正式 future Episode build confirm 写路径 `POST /api/v1/journal/v2/episode-builds/position-snapshot`：在同一 `BEGIN IMMEDIATE` 写事务内重跑 fence-bound preview 核心，CAS 对比 fence key / planned build key / evidence hash，强制费用守恒、complete-snapshot 边界与 left-censored / group-fee 显式接受后，append-only 追加 EpisodeBuild 及新 `journal_v2_episode_build_snapshot_fence_sources` link 行（UPDATE/DELETE deny triggers 同批安装）；build_key 幂等且 duplicate 必须核对存量 link 身份，默认 activation 与默认视图不变，零交易动作。
- [改进] fence-bound future preview 核心拆出 in-session 版本供 confirm 写事务复用，公开 `preview_fenced_position_episodes` 签名与零写语义不变；`planned_build_key` 派生按写合同补齐 snapshot key、target canonical set key、target publication key 与 projection 身份；CSV fallback 默认读显式排除 snapshot-fence build，episode summary 读回正确标注 `position_snapshot_fenced_canonical` 来源。
- [测试] 新增正式 future build 的 T1-T9 回归：happy path 追加 build+link、同参数重放 duplicate 零新行、真实 detail fill 证据行与费用守恒、stale fence / hash 不匹配 / 缺 acceptance 全部 409 且表计数与内容摘要零变化、link 表 UPDATE/DELETE 被拒、同 build_key 但 link 身份不符拒绝、API 层请求校验 / 409 映射 / 读回一致性守卫。
- [测试] 补 fence build 与 CSV-backed build 并存时的默认视图回归：CSV 基线批次不破坏 continuity fence，且 fence build 更新时默认读取仍是 CSV fallback（对抗性验证发现的覆盖缺口）。
- [新功能] 仓位复盘案例精选新增「证据最不完整」（`weakest_evidence`）：按 `completeness_score` 升序把证据最差的仓位回合排在前面，帮助优先复核缺证据案例；不过滤生命周期状态，其他筛选仍为交集；前后端枚举、URL 白名单、OpenAPI 合同测试同步更新。
- [修复] `position_snapshot_models` 显式注册 `refresh_models` 到共享 metadata：修复单独运行 episode repository 测试时 snapshot artifact 表对 `journal_v2_refresh_publications` 的外键在 `create_all` 中解析失败（全量套件因导入顺序掩盖的隔离缺陷）。
- [文档] HANDOFF 排障章节新增「官方发布每天 degraded」根因诊断：Finnhub 经济日历 403（免费档无权限）导致 events 降级、无 Alpaca key 导致 premarket 域不可用；两者均为配置/权限缺口而非代码缺陷，附解决选项与影响边界（full_research track 持续为 0 的原因之一）。
- [新功能] Web「仓位复盘」未来回合预览新增「写入正式 Future Build」显式确认块：展示计划 build key / 证据指纹 / 计划回合数，left-censored 与组费仅组级精确按需强制勾选后才可写入；成功与幂等重放均明确提示「已构建 ≠ 已生效：默认复盘视图不变（activation 尚未支持 future build）」，409 冲突时作废旧预览并要求重新运行零写预览后再确认，全程不自动触发写入。
- [测试] 前端补 future build confirm 客户端与卡片回归：POST 请求体与三个 64-hex key 的本地校验（畸形 key 不发请求）、回显 build/fence 身份漂移与 activation 越权 fail-closed、无预览时确认块隐藏、acceptance 勾选门禁、写入中禁用、成功 / duplicate / 409 清空预览路径。
- [修复] Playwright E2E 后端改为隔离启动：独立端口 8765、独立 `ENV_FILE` 与一次性空白 SQLite（`apps/dsa-web/e2e/.artifacts/`，已 gitignore），认证与全部 Moomoo / 盘前 / 回填调度器显式关闭，绝不复用本机常驻正式后端或真实 `.env` / `data/stock_analysis.db`；vite dev 代理目标支持 `DSA_WEB_API_PROXY_TARGET` 覆盖，chromium 项目改用本机 Chrome（`channel: 'chrome'`）以避免依赖 bundled 浏览器下载。
- [测试] 重写 `apps/dsa-web/e2e/smoke.spec.ts` 为当前 UI 语义（HANDOFF §14.3）：断言 `/` 与 `/login` 在无认证时落到 `/regime`、官方盘前研究未发布空态与“数据时点”口径行、分层健康弹层 OpenD / Journal / 盘前发布 / 结果回填全为“未启用”、`/journal` 仓位复盘与交易证据空态无未预期 console/page error、`/regime/opportunity/AAPL` 深链呈现“即时扫描 · 未绑定官方快照”；旧 `report-markdown.spec.ts` 因依赖已下线 UI 显式 `test.describe.skip` 并标注 TODO。
- [新功能] Regime 宏观事件域改用零成本官方年度日程（`src/regime/official_schedule.py` + `src/regime/data/official_economic_schedule_2026.json`）：FOMC 决议日（两日会议第二天）、CPI、非农发布日直接取自 federalreserve.gov / bls.gov 官方页面并随仓库版本化，含 per-series `source_url` / `retrieved_at` / coverage；`target_date + 7 天`窗口超出 coverage 时 fail closed 为 `unavailable`，过期日程不会伪装成“今天没有事件”。
- [改进] `get_macro_events` 不再调用 Finnhub 付费 `/calendar/economic`（免费档每日 403 导致 events 域恒为 degraded）：经济序列以官方日程为主源，Finnhub 只保留 earnings 日历；官方日程覆盖窗口内经济序列 readiness=ready，events 域在 earnings 同时可用时恢复 ready，消除每日 `regime_supporting_events_degraded`。`_readiness`/`_status` 合同与 scorer 字段名保持不变，仅追加 `_economic_calendar` 可观测元数据。
- [测试] 新增官方日程 provider 单测（决议日/发布日标志、两日会议第一天不标记、非事件日、7 天 agenda 窗口、coverage 边界 fail-closed、缺 series/坏文件 fail-closed、多年度文件合并）与 `get_macro_events` 集成回归（无 Finnhub 时经济序列仍 ready、earnings 正常时 events 整域 ready、超出 coverage 降级、不再调用经济日历端点）。
- [文档] HANDOFF「官方发布每天 degraded」排障条目更新：events 侧已由官方年度日程解决，premarket 侧仍需 Alpaca key；补充年度运维步骤——来年官方日程发布后需刷新 `src/regime/data/official_economic_schedule_*.json`，否则 coverage 到期前一周起 events 会诚实地重新降级。
- [新功能] 期权墙逐层合同增量（`option-wall/1.2`）：每个墙位 level additive 新增 `side`、`metric_basis`（OI＝T-1 清算 / Volume＝当日累计 / Gamma＝模型值）、按贡献排序的 top 3 到期日 `expiry_breakdown`（expiry/dte/metric_value/share_of_level_percent/contract_count + other 汇总桶）与逐到期 quote 上下文（单行快照支撑时附 IV 与 quote_as_of），缺失字段保持 null 并以 `quote_evidence`（observed/partial/unavailable）显式标缺；旧字段与端点签名不变，旧 payload 仍可通过 schema 校验。
- [改进] Web 期权墙逐层可展开到期分布：`/regime/opportunity/:ticker` 期权墙 tab 与今日机会候选详情共用 `WallLevelExpiryBreakdown`，逐到期显示占比、DTE、IV 与「Bid/Ask/Mark 标缺（快照未含盘口报价）」等显式标缺文案，保留「集中度区域，非 dealer GEX / gamma flip」诚实框架；期权墙前后端缓存 key 升为 v1.2。
- [测试] 期权墙逐层合同回归：builder 多到期 fixture 的 top 3 + other 分桶、share 总和 ≤ 100、多合约单元不归属报价、缺 IV 行显式 unavailable、全报价行 observed、三种 metric_basis 语义与 payload 无任何 dealer-sign 字段；API 端点断言逐层 breakdown 与 legacy level 兼容校验；前端补墙位 tab 到期分布与标缺文案渲染断言。
- [文档] HANDOFF §9.2 更新为 `option-wall/1.2` 已落地逐层字段清单与对照目标合同仍缺项（逐层 bid/ask/mark 需扩展 Moomoo 墙快照 adapter；dealer sign 红线不变）。
- [新功能] Episode build activation 资格扩展到 snapshot-fence 正式 future build（阶段 B 切片 2）：不改 activation 表 schema，fence build 激活时把 fence link 冻结的目标事实集身份写入 `canonical_set_id/sha256`（其回合正是该冻结集在快照边界后的窗口重放）；目标集行缺失或指纹不一致时激活与后续默认读取均 fail closed，无任一 source link 的 CSV build 仍不可激活，零激活时 CSV fallback 语义不变。
- [新功能] 激活新增 additive `accept_left_censored_openings` 确认（默认 false）：目标 build 报告含 left-censored 回合（snapshot 继承仓位、无券商成本）时必须显式勾选，否则 409 拒绝且零写；activation key 仅在该 flag 置真时参与派生，历史激活记录的幂等重放不受影响。
- [改进] data-health（journal refresh status）对激活的 fence build 按其冻结目标事实集对齐：目标集即最新 canonical set 时不再要求重复 activate 最新 canonical build，其余水位与阶段判定不变；激活响应消息按 build 来源标注 canonical / snapshot-fence future。
- [改进] Web「仓位复盘」显式查看 canonical / snapshot-fence build 时新增独立「设为默认复盘构建」卡片：按目标 build 实际口径强制 assumed-flat / 组费 / left-censored 勾选，确认区明示「激活后默认视图切换，可再激活其他构建切回，但无法回到零激活的 CSV 默认状态」；future build 写入成功提示同步改为「激活前默认复盘视图不变，需显式打开该构建后单独激活」。
- [测试] 新增 fence build 激活回归：happy path 默认读取切换与幂等重放、缺 left-censored 确认零写拒绝、陈旧 CAS 拒绝、fence link 指纹篡改在激活与默认读取双向 fail closed、激活前 CSV fallback 不变、data-health 三阶段对齐；API 补新 flag 透传与 409 映射；前端补两处激活卡片的勾选门禁、请求载荷与文案回归。

### 发布亮点

- 📊 **回测页新增"次日验证"视图** — 可按股票与日期范围查看 AI 预测 vs 次日实际涨跌，复用历史分析与 1 日回测结果，快速验证分析准确率。
- 🔧 **LLM 接入体验简化** — 用户侧文案统一收口为"主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有配置键保持兼容。
- 🐳 **Docker / WebUI 运行时稳态补强** — 修复系统设置保存后配置不生效、启动早期日志缺失、预构建静态资源复用等问题，降低容器化部署的运维摩擦。
- 🔒 **安全与并发稳定性同步增强** — Discord 入站 Webhook 补齐 Ed25519 验签，修复并发执行时共享状态未加锁、单股推送模式通知并发复用等问题。
- 🖥️ **桌面端与定时任务细节打磨** — Windows 安装器支持自选安装目录，内置定时调度器感知运行中 SCHEDULE_TIME 变更，断点续传改按市场时区判断。

### 新功能

- 📊 **回测页新增"次日验证 / 1 日窗口"视图** — 可按股票代码与分析日期范围查看 AI 预测、次日实际涨跌及筛选区间准确率，复用历史分析与 1 日回测结果实现。
- 🏷️ **Web 设置页新增版本信息卡片** — `apps/dsa-web` 现在会在构建时注入前端包版本与构建时间，系统设置页新增只读"版本信息"区块，展示 `WebUI 版本 / 构建标识 / 构建时间`；当 `package.json` 仍为占位版本 `0.0.0` 时，会自动回退为构建标识，方便 Docker 重建后快速确认当前静态资源是否已经生效。
- 🪟 **Windows 桌面安装器支持自选安装目录** — 安装器改为支持在安装向导中自定义安装目录，安装到非默认盘符后仍沿用现有打包态目录逻辑在安装目录旁读写 `.env`、`data/stock_analysis.db` 和 `logs/desktop.log`，同时保留 `win-unpacked` 免安装分发方式。安装器仅支持当前用户安装、已禁用管理员提权（`allowElevation: false`），并通过 NSIS `.onVerifyInstDir` 阻止选择系统保护目录。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；现在仅对极少数高位且摘要明显不足的结果，在更短超时预算内做延迟补抓，并优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案已统一收口为"主模型 / Agent 主模型 / 备选模型 / 模型渠道 / 高级模型路由配置"；Web 设置页、配置元数据、校验提示与中英文文档不再把 LiteLLM 当作普通用户默认必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键仍保持兼容。

### 修复

- 🚀 **启动早期失败时暴露真实根因** — `python main.py` 现在通过 stderr 暴露真实根因，bootstrap 阶段不再向硬编码 `logs/` 目录写入文件日志，文件日志推迟到 `config.log_dir` 可用后创建，避免健康启动在非预期路径残留日志文件。
- 🐳 **Docker WebUI 运行时优先复用预构建静态资源** — `prepare_webui_frontend_assets()` 现在会先检查镜像内已有的 `static/index.html` 是否可直接复用；当容器运行时不包含 `apps/dsa-web` 源码目录且未安装 `npm` 时，也不会误报"未找到前端项目，无法自动构建"，从而恢复 Docker 部署后的 WebUI 打开能力。
- 🐳 **Docker WebUI 系统设置保存后配置生效** — Docker 场景下 WebUI 保存 `STOCK_LIST`、`SCHEDULE_ENABLED`、`SCHEDULE_TIME`、`SCHEDULE_RUN_IMMEDIATELY`、`RUN_IMMEDIATELY` 后，`Config` 会优先读取持久化 `.env` 中的新值，避免被容器创建时注入的旧环境变量覆盖。
- 📈 **市场复盘 LLM max_tokens 提升** — 市场复盘生成链路将 LLM `max_tokens` 从 `2048` 提升到 `8192`，降低长复盘输出因 `MAX_TOKENS` 提前截断导致内容未完成的概率。
- ⏰ **内置定时调度器感知 SCHEDULE_TIME 运行时变更** — 调度器现在会在运行中感知 WebUI 保存后的 `SCHEDULE_TIME` 变化，并在下一轮检查时重绑 daily job。
- 🪟 **Windows Release 渠道编辑器保留 MiniMax 模型前缀** — 渠道模式下填写 `minimax/<模型名>` 时，后端归一化与 Web 设置页运行时模型列表都会保留该值原样，不再误改写成 `openai/minimax/<模型名>`。
- 🤖 **Discord 入站 Webhook 补齐 Ed25519 验签** — `DiscordPlatform` 现在会基于 `X-Signature-Ed25519`、`X-Signature-Timestamp` 和原始请求体校验 Discord Interaction 签名；缺失签名头、公钥格式非法或签名不匹配时直接拒绝请求，同时对 timestamp 做 ±5 分钟时效窗口校验以防御重放攻击。
- ⚙️ **STOCK_GROUP_N / EMAIL_GROUP_N 配置关系明确化** — 明确与 `STOCK_LIST` 的关系，并在配置校验中对超出 `STOCK_LIST` 的邮件分组给出 warning。
- 🗓️ **断点续传改按市场时区和交易日历判断**（fixes #880）— 股票数据存在性检查不再直接使用服务器自然日，而是按 A 股 / 港股 / 美股各自市场时区解析"最新可复用交易日"。
- 📨 **单股推送模式不再并发复用共享通知实例** — `StockAnalysisPipeline.run()` 现在会保留个股分析并发，但把 `SINGLE_STOCK_NOTIFY=true` 下的即时通知挪到结果收集侧串行发送。
- 🔇 **实时行情降级提示收口为单次告警** — 分析主流程获取股票名称时不再提前触发一次实时行情查询，只有在全部数据源都不可用时才提示已降级为历史收盘价继续分析。
- 🔍 **A 股中文资讯搜索恢复中文优先** — `search_stock_news()` 现在会在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面。
- 🔒 **并发执行时共享状态补齐统一加锁** — 修复并发执行时共享状态缺少统一加锁的问题，避免多线程场景下的数据竞争。

### 测试

- 🧪 **补充设置页版本信息回归测试** — 新增 Web 设置页版本信息渲染断言，并覆盖占位版本 `0.0.0` 自动回退为构建标识的逻辑。
- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

- [修复] 🐳 **Docker WebUI 运行时优先复用预构建静态资源** — `prepare_webui_frontend_assets()` 现在会先检查镜像内已有的 `static/index.html` 是否可直接复用；当容器运行时不包含 `apps/dsa-web` 源码目录且未安装 `npm` 时，也不会误报“未找到前端项目，无法自动构建”，从而恢复 Docker 部署后的 WebUI 打开能力。
- [改进] 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；现在仅对极少数高位且摘要明显不足的结果，在更短超时预算内做延迟补抓，并优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- [修复] A 股和中文股票名称场景下的相关资讯搜索恢复中文优先策略：`search_stock_news()` 现在会在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面；同时非美股查询不再默认沿用 Brave 的 `en/US` 区域语言偏好，避免更新后被英文新闻结果占满。
- [修复] 飞书群机器人通知现在支持 `FEISHU_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_KEYWORD`，并在 Web 设置与文档中明确区分 Webhook 推送和 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 应用模式，降低误配导致的推送失败。
- [改进] 🤖 **普通分析链路支持 LiteLLM 流式生成与更细任务进度** — 常规股票分析在 LLM 阶段会优先尝试 `stream=True` 并在服务端累积 chunk，首页任务 SSE 新增 `task_progress` 事件与更细的 `message/progress` 更新；仅在最终 JSON 解析成功后才持久化历史报告，不支持流式的 provider 会在首个 chunk 前自动回退到原非流式调用。
- [新功能] Web AI 模型配置支持按渠道调用 `/models` 获取可用模型，并在渠道编辑器中以多选方式写回 `LLM_{CHANNEL}_MODELS`，获取失败时仍保留手动输入作为降级路径。
- [新功能] Journal 新增零写模式观察聚合 `GET /api/v1/journal/v2/review-insights`（Playbook 合同切片 C-1）：按最新复盘标注的标签/错误类型 × 方向 × 边界口径分桶；胜率/均值等比率仅在 ≥10 笔且 ≥5 个独立交易日的已验证 P&L 样本上显示，条件性 P&L（假设平仓/左截断/组费影响）单独计数且永不进入统计，未标注回合只汇总为单一未复盘计数。
- [新功能] Journal“仓位复盘”页新增「模式观察」面板：展示分桶计数、样本不足与条件性 P&L 的 fail-closed 文案，并注明“观察到的模式 ≠ 已验证规则；晋升到 Playbook 需要显式操作（后续切片）”。
- [改进] Episode P&L 可统计口径（`pnl_summary_eligible` 排除原因）收敛为仓储层单一实现 `position_episode_pnl_exclusion_reasons`，API 投影与模式观察聚合共用同一定义，避免口径漂移。
- [测试] 新增 review-insights 仓储与 API 合同回归：分桶键不混轨、10 笔/5 交易日阈值边界、条件性 P&L 超阈值仍被排除、latest-revision 聚合、未复盘/未构建空态与零写断言；Web 端补「模式观察」面板计数/统计/条件标注/空态渲染测试。
- [文档] `New-docs/phase1/13_PLAYBOOK_PROMOTION_CONTRACT.md` 切片 C-1 标记为已实现并补实现锚点；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 同步 C-1 状态。
- [新功能] Journal Playbook 切片 C-2：新增 append-only `journal_v2_playbook_candidates`（L2 候选）与 `journal_v2_playbook_rules`（L3 规则版本链）两张表，均纳入 SQLite UPDATE/DELETE 拒绝触发器；创建候选与晋升规则时在同一会话内重跑 C-1 聚合冻结证据快照（build 身份、成员回合 id ≤200、最新标注修订、含条件性分桶的计数、阈值与 as-of），桶或默认构建不存在时 fail closed 拒绝写入。
- [新功能] 新增 Playbook 端点：`GET /api/v1/journal/v2/playbook`、`POST .../playbook/candidates`、`POST .../playbook/candidates/{candidate_key}/promote`、`POST .../playbook/rules/{lineage_key}/retire`；晋升/退役全部为显式用户动作 + CAS（重放幂等、陈旧期望与已退役 lineage 的再晋升缺显式 new-version 意图时返回 409），退役追加 `retired` 新版本并逐字复制晋升时冻结的快照，规则永不反写任何评分、榜单或 AI prompt。
- [新功能] Journal“仓位复盘”页「模式观察」每个分桶新增「保存为候选」内联表单（标题 + 规则描述，空内容禁用提交），并在其下方新增 Playbook 面板：候选可显式「晋升为规则」、规则显示版本/状态并支持显式「退役」，两者均带确认步骤与“规则不会影响系统评分或榜单，仅是你的决策清单”文案。
- [测试] 新增 Playbook 仓储回归（快照冻结内容、幂等重放、桶缺失/无构建 fail-closed 零写、CAS 陈旧拒绝、退役复制快照、重晋升需显式意图、两表 deny trigger、列表排序）、API 合同（round-trip + 409/422）与 Web 组件测试（表单 gating、晋升/退役确认流、诚实文案断言）。
- [文档] `New-docs/phase1/13_PLAYBOOK_PROMOTION_CONTRACT.md` 切片 C-2 标记为已实现并补实现锚点；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 同步 C-2 状态。
- [改进] `/regime/opportunity/:ticker` 摘要同证据束（D-4）：官方快照绑定时，「交易研究结论」摘要句中织入的增强数据数值（option-overview 的 IV Rank）强制带「（当前增强数据 as-of ET，非冻结榜单证据）」内联标注，模型终值区间的 IV 输入同步加注（价格基准 as-of 已由 modelBasisLabel 携带）；冻结 bundle 数值（20 日区间 / EMA / 量能比率）不加注，即时扫描视图不加注。
- [测试] 详情页文案审计回归：官方绑定下增强数值必须带非冻结标注、冻结数值不得被标注、即时扫描无任何增强标注；审计按「指标名+数字」词面模式扫描结论侧栏段落，防止新增无标注增强数值（局限：不识别未命名裸数字）。
- [文档] `New-docs/HANDOFF.md` §9.3 摘要同证据束项标记已完成并记录标注方式；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` D-4 状态更新为已验收。
- [新功能] Journal Playbook 切片 C-3：新增零写反向链接端点 `GET /api/v1/journal/v2/position-episodes/{episode_id}/playbook-links?build_id=...`，返回冻结证据快照中引用该回合的候选与规则（每条 lineage 只取最新版本）；只匹配快照 `build_id` 与给定构建一致的引用，快照样本被截断（冻结时成员 >200）而无法确认成员关系时，仅在冻结桶回显与回合当前标签/方向/边界口径一致时以独立 `possible_truncated` 条目返回，绝不伪装为已确认；未知回合/构建与 review-annotation 读路径一致返回 404。
- [新功能] 单笔复盘页（`/journal/review/:episodeId`）新增「Playbook 关联」面板：按已确认规则（`规则 v{n} · 生效中/已退役`）→ 已确认候选 → 「可能相关（无法确认）」排序展示，均带冻结桶回显（标签/错误类型 · 值 · 方向 · 边界口径）；截断条目标注「证据快照抽样截断，无法确认该回合是否在样本内」，空态为「该回合未被任何 Playbook 候选或规则引用」。
- [测试] 新增 C-3 仓储回归（确认链接排序与桶回显、lineage 最新版本含退役状态、构建不匹配排除、截断样本 possible 标注与桶不匹配省略、零写断言、未知 scope fail-closed）、API 合同（链接契约 + 空链接 + 404/422）与 Web 组件测试（规则/候选标签、截断不确定性标注、空态与错误态渲染）。
- [文档] `New-docs/phase1/13_PLAYBOOK_PROMOTION_CONTRACT.md` 切片 C-3 标记为已实现并补实现锚点；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 同步 C-3 状态。
- [文档] 新增 `New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md`（F-2 短期 artifact GC 设计冻结稿）：核实过期 refresh / position-snapshot preview artifact 表全部处于 SQLite deny-trigger 保护内（原“非 append-only 保护范围”假设不成立），冻结删除谓词（过期 ≥7 天且从未确认且无 publication/snapshot 引用且非账户最新）、单事务受控删除 + append-only 回执 + 备份前置的执行模型与 T1-T10 零误删测试合同；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` F-2 状态同步为设计已冻结。
- [测试] 概率展示校准边界（D-5）自动化断言：`/regime/opportunity/:ticker` 详情页测试固定模型终值区间块必须携带「IV 模型终值分布 · 不是历史真实胜率、盘中触及概率或方向预测」声明与「方向概率尚未校准」提示，并对整页（含期限切换与各研究 tab）扫描禁止出现「上涨概率 / 胜率+数字」伪概率文案（校准提示中的否定引用为唯一豁免）。
- [新功能] 分层健康端点 `GET /api/v1/system/health-layers` 新增第 7 层 `economic_schedule_coverage`（E-5 年度日程续期预警）：读取官方 Fed/BLS 日程数据文件的 coverage_through，余量 >30 天为 ok、≤30 天 degraded（提示在到期前放入下一年度数据文件）、超出覆盖或数据文件不可用为 down；`src/regime/official_schedule.py` 增加公共缓存访问器 `get_cached_official_schedule`，前端健康弹层补「经济日程覆盖」层名，三态与不可用态均有回归测试。
- [测试] Moomoo env 测试隔离系统化（Q-2 / HANDOFF §15 P1.9）：新增仓库根 `conftest.py` autouse fixture，非 network 测试统一把 `MOOMOO_OPEND_ENABLED` 等 5 个 live-integration 开关强制为 false（network 标记与单测试 `monkeypatch.setenv` 仍可 opt-in），杜绝 `get_config()` 载入宿主 `.env` 后 `-m "not network"` 套件打到真实 OpenD/调度器；新增 `tests/test_env_isolation_conftest.py` 回归证明，`test_iv_rank.py` 局部隔离 fixture 保留作纵深防御。
- [新功能] 短期 artifact GC 切片 F-2a（合同 `New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md`）：新增 `scripts/artifact_gc.py` 显式 CLI（默认零写 dry-run，以 SQLite `mode=ro` 只读打开正式库）与 `src/journal/ledger/artifact_gc.py` 单事务执行器，仅回收「过期超 7 天宽限 + 从未确认（无 publication / confirmed snapshot 引用）+ 非该账户最新一条」的 `journal_v2_refresh_artifacts` / `journal_v2_position_snapshot_artifacts` 预览行；apply 必须提供当日经 `integrity_check` 验证的备份，先在事务内重算谓词、只摘 DELETE 触发器、有界删除并断言 rowcount，再从属主模块共享 DDL 常量重建触发器并断言在位，任何失败整体回滚；每次运行（含空集与 dry-run `--receipt`）追加不可变回执到新表 `journal_v2_artifact_gc_receipts`（入 `_APPEND_ONLY_TABLE_NAMES` 保护），无任何后台/启动/API 清扫路径，`regime_premarket_*`、`opportunity_*` 与全部证据表明确禁区。
- [测试] artifact GC 合同 T1-T10 全绿（`src/journal/tests/test_artifact_gc.py`）：死行删除与回执一致、被引用行永不删除、宽限期/未过期保留、每账户最新一条保留且 `get_journal_refresh_status` 逐字段不变、dry-run 零写且候选集与 apply 将删集合一致、幂等二次 apply 删 0 行、触发器在位/外部写仍被 ABORT/rowcount 注入失败回滚、禁区表 count+content hash 不变、无备份或备份损坏或非 SQLite fail closed、回执表 append-only 且 `deleted_ids_sha256` 可由 `deleted_ids_json` 复算。
- [文档] `New-docs/phase1/14_ARTIFACT_GC_CONTRACT.md` 状态行标记 F-2a 已实现（正式库 apply 仍待用户确认 + 当日备份）；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` F-2 行同步；`New-docs/HANDOFF.md` §5.2 / §15 P3.7 过期清理技术债措辞更新为已实现，可恢复异步 job 与持久化 last failure 仍为技术债。
- [文档] F-4 clean-clone 演练完成并更新 `New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` F-4 行为 ✅（2026-08-01，clone bc74bc6）：干净克隆 pushed 分支后 `pip install -r requirements.txt`（含 scipy）、`pip install flake8 pytest` + `./scripts/ci_gate.sh`（2636 passed / 6 network deselected）、`npm ci && npm run build`、`main.py --serve-only`（隔离空库 + 全开关关闭，/api/health 与 /api/v1/system/health-layers 均 200）全部通过，零依赖原工作树/.env/数据库；README 待改小缺口仅记录未修：方式二把 `cp .env.example .env` 呈现为必需步骤（serve-only 冒烟无 .env 可跑）、启动方式自动构建写 `npm install` 而贡献节写 `npm ci` 不一致、未演练无 Node 环境下 `WEBUI_AUTO_BUILD` 默认自动构建的失败路径。
- [改进] G-3 Journal 信息架构重构：`/journal?tab=positions` 收敛为纯复盘工作台（新增「复盘工作台」头部条：默认构建标识 + Review Queue 计数 + 「继续复盘下一笔」CTA——优先续上进行中、其次按 top_loss 案例精选取亏损最大的未复盘已平仓回合、无命中退回最近未开始；回合列表/筛选、模式观察与 Playbook 保留），全部数据管线迁入更名后的「数据与构建」tab（原「交易证据」，`?tab=import` 深链不变）并分组为 每日刷新 / 历史导入 / 当前持仓快照与未来构建 / 构建与默认视图管理；canonical 构建预览、assumed-flat 构建与激活管理拆出为 `EpisodeBuildManagementPanel`，所有既有按钮与确认流保留，vitest + Playwright smoke 断言同步更新。
- [新功能] G-2 盘中跟踪面板：新增 `POST /api/v1/opportunities/intraday-tracking`（最多 5 个美股期权 underlying，复用期权墙同款 Moomoo Quote-only `get_market_snapshot` 机制 + 共享 30s TTL/single-flight，ATR/量能中位等日线派生输入按 symbol+ET 交易日另行 15 分钟记忆）与 `/regime` 机会看板下方的 `IntradayTrackingPanel`：对照冻结盘前计划展示 现价（as-of）/距确认位/距失效位（$ 与 ATR 倍数）/VWAP 上下方/量能节奏/盘段状态，确认与失效价直接取冻结候选结构化证据（前 20 日高低、EMA13），不解析展示字符串；手动刷新 + 仅页面可见（document.visibilityState）且盘段为盘前/盘中时 60 秒自动轮询；不重新排序、不生成买卖信号，Moomoo 未启用/不可用逐标的显式 not_configured/unavailable，绝不以 0 冒充实时数据。
- [新功能] G-4 首批专业指标（`src/opportunities/intraday.py`，纯函数 + 显式标缺）：ATR14（已完成日线 Wilder 平滑，公式在代码内文档化）、VWAP 近似（当日累计成交额 ÷ 累计成交量，basis 标签 `session_turnover_over_volume`，输入缺失给显式 reason 绝不回填）、量能节奏（当日累计 vs 前 20 交易日全日成交量中位数，明示未按盘中时点折算）、盘段状态（America/New_York 时钟，未接入交易所假日日历）；相对强度 vs SPY 留待后续切片。
- [测试] 盘中跟踪确定性测试：`src/opportunities/tests/test_intraday.py`（VWAP 有/无、ATR14 种子与平滑步手工核对、量能中位与比值、盘段时钟边界含周末/UTC 换算/naive 拒绝）；`api/v1/tests/test_intraday_tracking_endpoint.py`（disabled/unavailable fail-closed 不吐零值、ready 全指标合同、缺量 VWAP 标缺、盘段时钟 monkeypatch、TTL 缓存 + refresh 旁路 + 日线记忆、single-flight 并发共享、symbols 边界校验）；前端 `IntradayTrackingPanel.test.tsx`（冻结锚点推导与 ATR 距离符号、as-of 列渲染、VWAP 标缺、not_configured 不伪造数字、fake timers 验证 60 秒轮询仅在页面可见且盘前/盘中、手动刷新带 refresh 旁路、非美股候选不渲染）。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.7 盘中跟踪（语义与诚实边界：量能对比为全日中位未按时点折算、VWAP 为近似口径、无买卖信号），`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` G-2/G-4 行同步为代码落地、盘中真实验证待服务重启后进行。
- [改进] 复盘流 v2（G-5）：单笔复盘页 `/journal/review/:id` 重构为队列化逐笔复盘——顶部 sticky 快捷操作条（回合标识 + Net + 「跳过，下一笔 →」/「保存草稿并下一笔」/「完成复盘并下一笔」，保存失败停留当前页并显示错误）；「下一笔」与复盘工作台 CTA 收敛为单一实现 `apps/dsa-web/src/components/journal/review/nextReviewEpisode.ts`（进行中 → top_loss 未开始 → 最近未开始，排除当前回合，队列清空如实提示「全部回合已完成复盘」并提供返回列表），导航保持 `/journal/review/:id` push history 且查询上下文保留；≥1280px 双列布局（左 = K 线与成交证据时间线，右 = sticky 交易逻辑草稿，窄屏工作单先行，容器加宽至 1720px）；工作单默认快速复盘模式（错误类型/交易风格预设 chip 一键写入既有 tags/error_types 并去重、与自由文本共用一份数据，默认仅进场逻辑回忆与复盘反思两栏，「展开完整工作单」显示其余四项），保存校验、字符上限与 annotation 语义不变；证据窗口/窗口末投影与 Matching/Completeness/Provenance 折叠进默认收起的「证据明细」。
- [新功能] 复盘页订单级分批成交合并（display-only，`evidenceConsolidation.ts`）：同一 broker order 的多笔 fill 在成交证据时间线合并为一行（方向、总数量、现金流加权均价 = ∑|现金流| ÷ (∑数量 × 合约乘数)，BigInt 十进制精确、half-up 保留 4 位、成交时间范围、合计费用、「分 N 笔成交」徽标），费用证据不全时显式标注「费用不完整」而非部分和；可展开逐笔审阅全部原始 fill（底层 evidence 不变）；K 线默认每单一个合并 marker（点击选中合并行），同单 fill 相距超过一根 K 线时保留逐笔 marker，全部未映射时不伪造位置；单笔订单与订单时间代理证据保持原样且代理标注保留。
- [测试] 复盘流 v2 前端回归：新增 `nextReviewEpisode` 单测（三档优先级、排除当前回合、耗尽返回 null）与 `evidenceConsolidation` 单测（加权均价精确与 half-up、费用完整性、单笔透传、代理标注保留、marker 合并含相邻 bar 合并/跨 bar 拆分/日线不同 bar 拆分/未映射跳过、混合方向降级 unknown）；`JournalEpisodeReviewPage` 测试更新并新增（操作条渲染、双列结构断言、快速 chip 写入与取消、compact/展开工作单、保存并下一笔导航、跳过不保存、保存失败停留、队列耗尽提示、证据明细默认折叠、合并行聚合值与逐笔展开、合并 marker 点击选中）；`ReviewWorkbenchHeader` 断言同步共享 helper；`npm run lint` + vitest 全量（73 文件 / 629 测试）+ `npm run build` + `npx playwright test e2e/smoke.spec.ts`（5/5）全绿。
- [文档] `New-docs/phase1/05_SINGLE_POSITION_REVIEW_WORKSPACE.md` 新增 §1.1 复盘流 v2 与 §3.1 订单级分批成交合并口径并更新状态行；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 新增 G-5 行（复盘流 v2，✅ 2026-08-01）。
- [新功能] G-6 日内/周内看板分离：新增 `POST /api/v1/opportunities/intraday-top` 盘中滚动 Top 5（复用 G-2 Moomoo 会话快照 + 共享日线加载器的 ATR14/量能中位/上一日结构 + 逐标的有界异动页；缺口/量能节奏/VWAP 位置/波幅扩张/异动活跃五项 v1 启发式阈值做确定性证据计数排名，`signal_version=intraday_session_evidence_v1`，60 秒 TTL + single-flight）；响应固定 `statistics_track=none_intraday_v1_unscored`，不冻结、不写快照/qualification/5D/20D 结果，休市时段仍可读但显式标注「最近一个交易时段」并把缺口分母切换为快照自带前收；缺可用现价快照的标的 fail-closed 为「数据不足」。
- [新功能] 新增 `GET /api/v1/opportunities/intraday-pulse` 市场脉搏（SPY/QQQ/VIX 快照现价与当日涨跌，分母为快照自带前收）；VIX 与 SPY/QQQ 隔离请求，供应商不可得时逐代码显式标缺，绝不以 0 或旧值冒充。
- [新功能] Web 新增 `/intraday` 日内工作台（导航首位「日内」）：sticky 市场脉搏条（读数 + 盘段 + ET 时点 + 刷新指示）、今日计划（复用盘中跟踪面板对照冻结盘前 Top 5，无冻结计划时诚实空态）、可点列头排序的日内扫描表（行点击进入即时扫描详情页）与跨标的期权异动 feed（≤20 条按时间倒序，固定标注 Moomoo 分类不证明开平仓方向）；页头明示「盘中滚动研究 · 不是信号 · 不进入统计」，自动刷新 60 秒且仅页面可见、盘段为盘前/盘中时运行。
- [改进] `/regime` 周内机会榜副标题如实标注「基于上一完整交易日日线结构 · 数日至数周研究周期」，并新增「进入日内工作台 →」入口；既有官方盘前冻结与统计行为不变。
- [测试] 日内 Top 确定性测试：`src/opportunities/tests/test_intraday_top.py`（五项阈值双向、缺快照 fail-closed、Moomoo sentiment 聚合诚实性——中性多数记中性、平票记 mixed、无分类记 unknown、绝不发明方向，休市缺口口径切换、排序确定性、异动 feed 时间倒序）；`api/v1/tests/test_intraday_top_endpoint.py`（响应合同与 statistics_track、未启用零 Moomoo 调用、60 秒 TTL/refresh 旁路/single-flight、逐标的异动失败隔离、STOCK_LIST 回退与请求边界、pulse 未配置与 VIX 降级）；前端 `IntradayPage.test.tsx`（区块渲染与 as-of、客户端排序三态、轮询门控、休市停表与标注、手动刷新 refresh 旁路、失败诚实降级）+ Shell 导航首位断言 + Playwright smoke 新增 `/intraday` 诚实空态用例。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.8 日内工作台与日内 Top（日内/周内语义分界、v1 阈值表、诚实合同）；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 新增 G-6 行；`New-docs/HANDOFF.md` §7.1 路由表新增 `/intraday`。
- [新功能] G-7 波段爆发（momentum burst）成为日内扫描主排序信号：新增纯函数模块 `src/opportunities/intraday_bursts.py`（5m K 线滚动 15 分钟窗口，`burst_score = |收−开|/当日 5m 波幅中位 × 窗口量比`，当日不足 6 根 K 线时中位数回退上一交易时段并显式标注）；`intraday-top` 升 `signal_version=intraday_session_evidence_v2`，盘中 `ranking_method=burst_score_first_then_evidence_count`（当前爆发分优先、证据计数次之），休市退回证据计数排序但候选仍附最近一个交易时段的波段列表；候选新增 `session_bursts`（当前窗口 + ≤4 个相隔 ≥30 分钟的独立波段）与「波段爆发」证据项（当前窗口 ≥6.0 记 supports）。阈值按 2026-07-31 用户标注行情校准（MU 09:45 跳水 35.5、AMZN 09:30 开盘波 18.7、NVDA 09:40/15:15 两波 9.3/8.6 全部 ≥ LEG_MIN_SCORE 8.0；GOOGL——v1 全时段聚合误排第一的失败样本——最佳窗口仅 15.2）。
- [新功能] intraday-top 执行器逐标的服务端读取 5m K 线（复用 `/stocks/{code}/history?period=5m` 的同一 StockService 加载器，仅当前 + 上一交易时段、有界线程池并发、逐标的 60 秒 TTL 缓存）；单标的 5m 读取失败只把该标的的波段爆发显式 `unavailable`，绝不阻塞缺口/量能/波幅/异动等聚合证据，也不影响响应可用性。
- [改进] `/intraday` 日内扫描表新增首个数据列「当前爆发」（爆发分 + 方向箭头 + 15 分钟推力%）与「今日波段」列（如「2 波：09:40↓ · 15:15↑」，悬停展示每波明细；休市显示最近一个交易时段），默认排序＝服务端排名（盘中爆发分优先），页脚公式行与页头排名标注同步 v2；期权异动 feed 不变。
- [测试] 波段爆发校准回归与单元测试：真实 2026-07-31（用户标注日）常规时段 5m K 线固化为永久 fixture `src/opportunities/tests/fixtures/intraday_5m_2026-07-31_regular.json`（记录来源与 captured_at），`src/opportunities/tests/test_intraday_bursts.py` 断言 MU 09:40-09:50 向下波段、AMZN 09:30 开盘波、NVDA ≥2 波且含 15:00 后一波、GOOGL 最佳波段分 < MU 跳水分（v1 失败样本永久钉住），另含归一化数学手算核对、开盘初段中位数回退、独立波段合并、休市时段归属、无 K 线 fail-closed；`test_intraday_top.py` 新增爆发证据阈值双向/盘中爆发优先排序/休市 v1 排序附波段/缺失不阻塞聚合断言；`test_intraday_top_endpoint.py` 新增 5m 失败隔离与逐标的爆发缓存跨 refresh 命中断言；前端 `IntradayPage.test.tsx` 同步新列渲染、标缺诚实与爆发列排序断言。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` §2.8 更新为 v2：波段爆发语义、阈值表新增波段爆发行、2026-07-31 校准记录（标注人＝用户）与休市排序退回说明；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 新增 G-7 行。
- [新功能] G-8 临期合约面板：新增 `POST /api/v1/opportunities/near-expiry-contracts`（单个美股期权 underlying，`max_dte` 默认 3、上限 7、含 0DTE），补「选中标的 → 选中合约」缺口的只读合约级数据——近价窗口（±5% ∪ 现价上下各 8 档）内 Call/Put 合约的 bid/ask/mid/点差%/最新价/当日量/T-1 OI/供应商 IV/delta 与逐合约 as-of，按到期日中性分组；显式非推荐引擎：不打分、不偏好排序、不生成买卖建议，响应固定携带 OI 结算口径、点差随时变化、IV 供应商模型值、不构成推荐、以券商实时盘口为准的 limitations。
- [新功能] 临期合约数据路径复用既有 Moomoo 链机制并直读同批 `get_market_snapshot` 的期权 `bid_price/ask_price`（单到期 fetch_chain 一直在读，期权墙只是未保留该字段）：1 次链日期窗口 + 1 个快照批次，独占 wall QuoteContext lane，30 秒 TTL + single-flight，`refresh` 只绕过已完成 TTL；bid/ask 任一缺失时 mid 与点差显式 null + reason 绝不 0 回填，快照缺行保留静态行逐字段标缺，逐到期隔离，窗口内无到期日返回诚实 `empty`。
- [新功能] Web 新增 `NearExpiryContractPanel`：`/intraday` 日内扫描表行尾「临期合约」按钮内联展开（整行点击仍是既有详情页导航、同一时刻只展开一行以保持表格可用），`/regime/opportunity/:ticker` 期权墙标签底部「查看临期合约（0–3 DTE）」按需展开；ATM 行高亮为位置标记、点差 >15% 标「流动性差」（v1 启发式展示阈值，未经交易结果验证）、缺失字段显示「标缺」，页头固定「合约选择参考 · 不构成推荐 · 以券商实时盘口为准」。
- [测试] 临期合约确定性测试：`src/opportunities/tests/test_near_expiry_contracts.py`（18：近价窗口 ±5%/稀疏链扩到 8 档/边界含入、点差数学与缺 bid/ask null-not-zero/交叉盘口标缺、逐到期隔离、ATM 双边标记、失败批次降级）；`api/v1/tests/test_near_expiry_contracts_endpoint.py`（17：未启用零 Moomoo 调用、TTL 复用/refresh 旁路/single-flight、max_dte 隔离缓存 key、非法 symbol 与 max_dte 422、诚实空态与 unavailable 降级）；前端 `NearExpiryContractPanel.test.tsx` + `IntradayScanTable.test.tsx`（分组与 as-of + 诚实页头、流动性差阈值严格大于、标缺渲染、按钮展开不触发行导航、单行展开）。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.9 临期合约面板（bid/ask 来源结论、额度成本、近价窗口、诚实边界与交互选择）；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 新增 G-8 行。
- [新功能] Moomoo History CSV parser 升级到 `moomoo-statement-v3`：识别组合单（多腿价差）父单行的 `Nunit(s)` 组合 unit 数量、`2unit(s)@7.00` unit 成交摘要与 `MU260731P745/760` 型价差符号（保留原始符号并在无歧义时解析 underlying/到期/方向/行权价文本，不做 OCC 单行权价伪解码），父单后的腿展示行与其成交续行作为腿证据保留在父单上；含组合父单的 CSV 不再整体解析失败，父单永不伪装成普通单腿订单。
- [改进] CSV 组合父单入账为 audit-only `BrokerExecutionGroupObservation`（组合 unit 语义）+ 组级费用观测：不分摊费用到腿、不推导合约乘数或腿级数量、不写腿/成交观测；canonical 选择将 CSV 组合父单显式排除（provenance `canonical_scope=excluded_csv_combo_parent`），腿级真相仍由 OpenAPI execution group 提供；preview 新增 `combo_parent_orders` 等 additive 计数并把含组合父单的 CSV 至少判为 `partial`，旧版 CSV↔readonly 对账将窗口内组合父单显式排除并输出警示。
- [测试] 新增真实导出行文本的组合父单解析回归（unit 数量/成交摘要/组级费用尾列/价差符号/两条腿展示行、邻近普通单不受影响）、对账排除警示、账本组合父单 audit-only 入账与 canonical fail-closed 排除、preview/import API 组合计数与部分级别断言；既有 parser 与账本测试全部保持通过。
- [文档] `New-docs/phase1/01_MOOMOO_EVIDENCE_LEDGER.md` 新增 §4.3 组合单父单解析与 canonical 排除语义；`New-docs/HANDOFF.md` §4.2 更新 parser 条目；根 README 未涉及该专题细节，故未改动。
- [新功能] 日内扫描噪音过滤 v3（`intraday_session_evidence_v3`）：把用户自身交易纪律（Playbook 候选 R1/R3，1,653 笔已平仓交易统计核验）编码为四类诚实上下文标注——时段上下文（ET 时钟九段 + 硬编码 v1 纪律提示，pulse 与 intraday-top 双响应携带、脉搏条首醒目展示）、财报临近（Finnhub 一次区间调用覆盖全 universe，≤3 天醒目「财报 N 天内 · 期权贵」）、大盘对齐（SPY 并入同批快照，会话 VWAP 位置 vs 候选爆发方向 → 顺势/逆势/标缺）、速度分级（相邻 15 分钟窗口爆发分之差 → 加速/减速/持平/标缺）。设计规则固定「系统标注，用户过滤」：全部信号只加标签，不自动过滤行、不隐藏候选、不参与排序或证据计数。
- [改进] 日内扫描表新增「速度」「大盘」「财报」三列（财报列可按距财报天数排序、标缺行恒排最后）并扩展 footer 公式行（含「减速=你的离场信号（R1）」与设计规则）；市场脉搏条新增时段标签（可访问 Tooltip 说明其为用户历史统计的硬编码文案）与 SPY/QQQ 会话 VWAP 位置。
- [修复] v3 上下文全部 fail-closed：财报日历不可得时 `within_blackout=null` 显式标缺（绝不以「无财报」冒充安全，成功但窗口内无财报才是诚实 `false`）、SPY 或爆发方向任一侧缺失时大盘对齐标缺、速度窗口不足显式 unknown；日历失败只短缓存 10 分钟且不阻断其余证据。
- [测试] 新增时段 ET 时钟边界（含 09:30/10:00/13:00/15:00 等 18 个断言点与周末/UTC 换算）、速度分级（加速/减速/持平/单窗口与缺分数 unknown）、财报临近（0/3/4 天回避窗边界、窗口外排除、unavailable 诚实）、大盘对齐矩阵（up/above=顺势等四象限 + flat/缺失标缺）、日历单次区间调用跨 refresh 与 universe 的按日缓存、SPY 并入同批快照零新增请求，以及前端列/badge/时段标签渲染与 E2E 时段标签断言；周日休市 TestClient 连本机 OpenD + 真实 Finnhub 验收（closed 诚实标注 + 最近交易时段速度状态 + MU 逆势样本）。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` §2.8 新增 v3 上下文信号小节（四类信号口径、财报额度成本、设计规则）；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 新增 G-9 行；根 README 不承载日内列级细节，故未改动。
- [新功能] 日内扫描形态相似度 styleMatch v1（`intraday_session_evidence_v4`）：纯函数 `src/opportunities/intraday_setups.py` 把当前时段几何形状与用户自己的三个 Playbook setup 做形状对比——S1 十五分钟低点抬高突破（5m 按 09:30 ET 栅格聚合 15m，尾部连续抬高 swing low ≥2 + 突破结构高点）、S2 跳空高开托举（向上跳空达缺口证据同阈值 0.75×ATR/1.5% + 缺口未回补 + 现价 ≥ 会话 VWAP）、S3 高开遇阻回落（跳空 + 现价跌破开盘/VWAP + SPY 处于会话 VWAP 下方，做空 setup）；每 setup 恰好一个状态 matched/partial/not_matched/unavailable（附中文理由与证据行），可多 setup 同时相似，`matched_setups`/`partial_setups` 为顶层摘要。复用波段爆发通道同一批 5m K 线与 G-2 快照派生字段，零新增供应商请求；纯标注，不参与排序、不隐藏行、不是买卖信号。
- [改进] 日内扫描表新增「形态」列：matched 实底徽标 / partial 描边徽标加「· 似」（tooltip 展示理由、证据行与只读 Playbook 对应「对应 Playbook: S1（候选/已晋升）」）、无相似「—」、输入不足「标缺」；footer 固定附「形态相似度为 v1 几何检测（5m近似），不含你的进场确认帧（2m/1m 回踩8/13EMA），不是信号」。服务端只读读取 journal_v2 Playbook 候选（标题 S1/S2/S3 前缀，5 分钟缓存），无任何晋升或写入逻辑，读取失败仅缺 Playbook 标注。
- [修复] styleMatch 全程 fail-closed：S1 不足 3 根 15m K 线、缺口输入缺失、托举/回落输入缺失、SPY 状态标缺分别给显式 reason（SPY 标缺时 S3 最多 partial，绝不冒充「大盘走弱」）；休市按最近一个交易时段评估并以 `session_date_et` + `quote_session_scope=latest_prior_session` 如实标注；缺口阈值单一真源迁至 `intraday_setups`（`intraday_top` 原名别名导入，数值语义不变）。
- [测试] 新增 `src/opportunities/tests/test_intraday_setups.py`（25：S1 抬高+突破/未突破 partial/低点走低/K 线不足/平坦无 swing low、S2 阈值双向与同源断言/单边托举 partial/回补 not_matched/输入标缺、S3 大盘走弱 matched/大盘强 partial/标缺 partial、多 setup 同时相似、休市 as-of、Playbook 只读透传、15m 栅格聚合）；`test_intraday_top.py` +4（setup_match 携带且不改证据计数、S1 K 线接线、Playbook refs 注入、休市 scope）；endpoint 合同断言 v4 + setup_match 状态矩阵 + Playbook 只读标注 + 5m 失败仅 S1 标缺；前端形态列徽标/tooltip/—/标缺渲染测试。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.10 形态相似度（三 setup v1 规则表、显式不检查项、Playbook 只读对应、诚实边界）；`New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` 新增 G-10 行；根 README 不承载日内列级细节，故未改动。
- [新功能] 临期合约面板（G-8）页头接入 v3 财报临近警示：`near-expiry-contracts` 响应新增 additive `earnings_proximity` 字段（与日内扫描表候选同形状，复用同一份逐 ET 日 Finnhub 日历缓存与 `compute_earnings_proximity`，零新增抓取路径、零新增常量），回避窗内（≤3 天）面板页头醒目标注「财报 N 天内 · 期权贵 · 你的回避规则」（0 天为「今日财报…」），ready 且窗外不加任何标注；该字段与面板自身 state 正交（Moomoo 未启用/失败照常返回）。
- [修复] 临期面板财报字段 fail-closed：日历不可得时 `state=unavailable`、`within_blackout=null`，前端小字「财报日历标缺 · 未知≠安全」；旧会话缓存缺该字段的载荷同样按标缺处理，绝不以缺失冒充安全。
- [测试] `test_near_expiry_contracts_endpoint.py` +5（回避窗内 3/5/basis 断言、窗外 ready 不标注且不串其他标的、日历 unavailable 诚实、Moomoo 禁用仍带字段、扫描车道已加载日历时临期面板零第二次区间调用）+ autouse Finnhub 日历桩（既有用例绝不打真实 Finnhub）；前端面板 +5（回避窗徽标、今日财报文案、窗外零标注、标缺小字、旧载荷缺字段按标缺）；真实 TestClient 验收：SNDK 财报 2026-08-05（3 天内 → within_blackout=true）、AAPL 窗口内无财报（false），两标的共享一次日历区间调用。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` §2.9 前端小节新增财报临近一行（字段形状、复用口径、三种渲染状态）；根 README 不承载面板级细节，故未改动。
- [文档] `New-docs/HANDOFF.md` §1「一屏结论」与 §1.1 同步 2026-08-02 真实状态（分支已推送 + PR #3、canonical set #2 与 build #3 未激活、日内工作台 G-1..G-10、周内榜 21:12 自动发布就绪、moomoo-sync 旧 LaunchAgent 待 F-1 清理），§1.2 浏览器核对清单新增 `/intraday` 交易日主屏。
- [新功能] 日内扫描新增 watchlist v1 两层 universe（`INTRADAY_WATCHLIST` + `INTRADAY_DEEP_LANE_MAX`，默认不配置＝行为与现状逐字节一致）：宽层每 60 秒仅 1 次 Moomoo 批量快照覆盖全清单（≤200 档，官方单次上限 400），异动闸门按 |当日涨跌幅|→成交额 晋升前 K 档（默认 12，1..20）进入既有 v4 深度管线，当日冻结盘前计划标的始终占深度位不占 K 名额；响应新增 additive `universe_scan` 块与逐候选 `scan_tier`/`deep_lane_reason`，宽层行只有快照字段、深度读数缺席即缺席。
- [改进] 深度层 5m K 线额度按 Moomoo `request_history_kline` 真实配额语义设计（30 天滚动窗口去重标的数、账户档位 100 起、同标的重复请求不扣额）：晋升去重上界＝清单长度，另设每 ET 日新晋升去重标的数护栏 30 档（内部常量），触顶如实标注 `day_promotion_cap_reached`；前端页头/footer 改为「全清单 N 檔快照 · 深度分析前 K 檔 · 其余仅快照」，表下新增「仅快照 · 未做深度分析」紧凑列表（含快照未解析点名与截断警示），深度行附「计划钉选/异动 #n」徽标。
- [测试] 新增 `api/v1/tests/test_intraday_top_two_tier.py`（8：未配置清单回归锁定、显式 symbols 绕过两层、单快照批次+闸门排序+宽层无深度字段、计划钉选去重不占 K、日晋升护栏、Moomoo 禁用诚实降级、清单显式截断、K 值钳制）；既有空 symbols 回归用例固定清单未配置；前端 IntradayScanTable +4（两层页头/footer/徽标、仅快照列表与未解析点名、无 universeScan 单层渲染回归、日护栏警示行）。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.11 两层扫描（启用条件与回滚＝取消 `INTRADAY_WATCHLIST`、闸门口径、K 线配额语义、每周期请求预算、additive 响应合同）；`.env.example` 新增两键中文说明与用户实际 69 档清单示例值；根 README 不承载日内 universe 细节，故未改动。
- [修复] 日内两层扫描盘前时段（ET 04:00–09:30）改按真实盘前口径晋升与排序（`gate_basis=premarket_pre_price_change_then_pre_turnover_v1`：|pre_change_rate|（盘前价 vs 上一常规收盘，可为负）→ pre_turnover）：Moomoo 常规快照字段在盘前仍指向上一常规时段，原口径会把昨天的异动复现成今晨深度榜；缺盘前字段的行不可晋升且宽层恒排最后，整批无盘前字段时显式回退常规口径并在 `universe_scan.gate_warnings` 携带 `premarket_fields_unavailable_ranking_reflects_prior_session`；宽层行 additive 携带 `pre_change_percent`/`pre_turnover`（缺列＝null 绝不 0 回填）；前端盘前口径下页头/footer 附「盘前异动排序（盘前价 vs 前收 · 盘前成交额次序）」、仅快照行展示「盘前 ±x%」与盘前成交额（缺盘前字段显式「盘前标缺」）、回退时显示「盘前字段不可用 · 当前排序反映上一常规时段」警示。
- [测试] 盘前闸门契约测试 +3（盘前口径按 |盘前涨跌|→盘前成交额 晋升且上一时段最大异动因缺盘前字段绝不晋升、整批无盘前字段显式回退+警示、常规时段无视盘前字段且口径与警示逐字节回归）+ Moomoo 快照解析器盘前字段单测（负 pre_change_rate 合法、pre_price 零/负与非法值/缺列一律 None）；前端 IntradayScanTable +3（盘前口径页头/footer 标注、盘前读数 chip 与盘前标缺行诚实渲染、回退警示 chip、常规时段无盘前标注回归）。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` §2.11 新增盘前口径小节（常规快照字段盘前指向上一常规时段的语义、pre_change_rate 相对上一常规收盘口径、回退警示诚实边界与前端标注）；根 README 不承载日内闸门细节，故未改动。
- [改进] 日内扫描表改为两级布局：默认网格收敛为 9 列交易关键读数（排名/标的/涨跌%/当前爆发/今日波段/速度/形态/波段vs大盘/详情），深度位与财报警示徽标并入标的格次行（日历标缺显式「财报标缺 · 未知≠安全」），「大盘」列改名「波段vs大盘」并加列头 tooltip 澄清「当前波段方向 vs SPY VWAP 位置、非个股涨跌方向」；量能节奏/缺口/波幅扩张/VWAP/期权异动/财报全文/研究状态移入行内展开的「研究读数」网格（临期合约面板上方），仅快照列表默认只显示前 5 檔可一键展开，footer 收敛为一行口径说明 +「完整口径」切换（完整公式墙原文逐字保留）——重排可见性不删除任何读数，标缺语义与共享 Tooltip 无障碍标注不变。
- [新功能] 日内扫描「今日波段」列接入 signal v5 波段分级：burst leg 类型新增 additive `grade` 字段（strong＝爆发分 ≥8 暴动 / medium＝≥2.5 持续推升），前端以「09:45↓ 强」实底警示 chip 与「10:00↑ 中」描边 chip 呈现，每波分级并入 aria-label 波段明细；缺 grade 的旧载荷渲染无分级 chip，绝不发明分级。
- [测试] IntradayScanTable 测试更新至两级布局（20 例全过）：新增 9 列默认网格与次要指标不入首屏、强/中/无分级波段 chip 与空态/标缺、仅快照列表前 5 檔折叠与展开切换、展开行研究读数网格、footer 短行 + 完整口径切换、按当前爆发排序标缺行恒最后且服务端排名不改写等回归。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` §2.8 扫描表列清单更新为 9 列默认网格并注明两级布局与 v5 波段分级 chips；根 README 不承载日内列级细节，故未改动。
- [新功能] 日内波段记录分级（signal v5）：强波段 ≥8（2026-07-31 暴动样本校准，阈值不变）之外新增中波段 ≥2.5（2026-08-03 NVDA 上午 09:55→10:25 持续推升实时校准，峰值 5.03/持续段 2.5-3.1/无波时段 <1.1），持续推升型可交易波不再漏记；波段携带 grade（strong/medium）additive 字段，强波段贪心优先占据 ≤4 名额。
- [修复] 日内两层扫描异动闸门升级 v2（`gate_basis=momentum15m_then_day_change_v2`，2026-08-03 首个实盘日校准：普涨跳空日 |当日涨跌| 单口径让 +5%~+11% 隔夜跳空标的挤满深度层，用户唯一认定可交易的 NVDA +2.5% 稳步爬升整日未晋升）：非盘前时段 K 名额拆两档——ceil(2K/3) 按最近 15 分钟动量 |mom15|（宽层批量快照喂养的进程内滚动历史，取 12–18 分钟窗内最老样本，零新增请求）、其余按 |当日涨跌幅| 兜底，两侧成交额次序、动量先占位后去重；冷启动（重启/开盘/样本过期）显式回退 v1 并在 `gate_warnings` 携带 `momentum_history_warming_up_ranking_by_day_change`，绝不静默；动量历史 ET 日切换即清空，盘前口径（pre_*）完全不变。
- [新功能] 新增 `INTRADAY_PINNED_TICKERS` 用户钉选（仅两层模式生效，留空＝现状不变）：钉选标的与当日冻结盘前计划同权——始终深扫、不占 K 名额、并入同一批快照、计入当日额度去重，`deep_lane_reason.promoted_by` 新增 `user_pinned`（兼具计划身份时计划钉选优先标注），`universe_scan` 新增 `user_pinned` 字段；前端深度行附「钉选」徽标、footer 如实报告钉选占位。
- [新功能] 日内扫描响应新增「今日曾深扫」账本（`universe_scan.day_ledger` + `day_ledger_basis`，additive）：当日曾晋升深度层、被 movers 轮换出的标的以最后一次深扫摘要 as-of 保留（分级波段/形态匹配/最后涨跌/`state=rotated_out`），绝不与当前深度层重复；进程内展示缓存——不写数据库、重启清空并以 basis 字段如实声明「重启后从当前时刻累计」；前端主表下方渲染「今日曾深扫 · 波段保留」紧凑行（空账本不渲染）。
- [改进] /intraday 命名与主次整理：扫描表标题改「实时扫描 · 现在谁在动」（两层模式附「深度层实时排名，下方为今日曾深扫账本」副标注），原「盘中跟踪」面板改名「今日计划跟踪 · 盘前冻结计划走到哪了」，页面顺序调整为 市场脉搏 → 实时扫描（主表）→ 今日计划跟踪 → 期权事件流；v2 口径页头/footer 附「15分动量优先（谁现在在动）· 当日涨跌兜底」，预热回退显式「动量样本预热中 · 暂按当日涨跌排序」警示 chip。
- [测试] 两层扫描契约测试 +6（钉选去重/不占 K/并入同批快照、清单未配置时钉选零影响回归、mom15 12–18 分钟窗数学（假时钟）、ET 日切换清空动量历史、冷启动回退警示 → 15 分钟后 v2 双子额度排名（动量位不给横盘标的）、账本轮换保留最后深扫载荷且不与当前深度层重复）；常规时段回归用例更新为断言冷启动警示；前端 IntradayScanTable +6（改名页头与副标注、「钉选」徽标与钉选计数、v2 口径标注、预热警示 chip、账本行渲染与空账本不渲染）、IntradayPage 组合顺序与 9 列布局断言更新、IntradayTrackingPanel 改名断言更新。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` §2.11 更新（闸门 v2 规则与 2026-08-03 NVDA/跳空日校准背景、用户钉选、今日曾深扫账本、前端命名与主次整理、additive 响应合同）；`.env.example` 新增 `INTRADAY_PINNED_TICKERS` 中文说明并补充闸门 v2 注释；根 README 不承载日内闸门细节，故未改动。
- [新功能] 个人画像回灌（personal-edge）：新增只读 `GET /api/v1/journal/v2/personal-edge`（`src/journal/personal_edge.py` 零写聚合），对 Journal 当前默认 episode build（复用既有 `_resolve_effective_episode_build` 解析，激活新 build 自动跟随）的已平仓回合做描述统计——按标的（仅 n≥5）`{n, net, win_rate, fees}`、持仓时长桶（<10m/10-30m/30-60m/1-3h/3-6h/6h-1d/>1d，含 avg_win/avg_loss）、进场 DTE 桶（0/1-3/4-7/8-30/>30，DTE 缺失单独报 `dte_unknown`）与月度 `{n, net, fees, win_rate}`；响应固定携带 build_id + 日期范围 + computed_at（as-of 诚实），服务端进程内缓存约 10 分钟；月度按 ET≈UTC−4 近似换算并以 `month_basis` 与 limitations（内生性 caveat + 时区注记）原文声明。
- [新功能] 实时扫描表新增第 10 列「你的战绩」（深度行 + 今日曾深扫账本行）：该标的的个人净盈亏（紧凑 $ 格式）· 胜率 · 笔数，净亏损且 n≥20 加警示 tint + tooltip「你的历史亏钱标的 · n 笔 · 净 −$X · 胜率 Y%」，n<5 显式「样本不足」，端点失败或 Journal 未构建显式「标缺」——系统标注，用户过滤：不隐藏行、不改排序、不是信号；前端经 `usePersonalEdge` hook 读取（fetch 层 10 分钟 sessionCache，每会话最多一次请求）。
- [新功能] 临期合约面板头部下方新增个人 DTE 提示行「你的 DTE 战绩：0DTE ±$…(x%) · 1-3DTE … · 4-7DTE … · 样本 YYYY-MM→YYYY-MM · 描述非因果」：数值全部来自 personal-edge 端点实时重算（绝不硬编码），空档位显式「无样本」、端点缺席显式「标缺」，tooltip 原文携带内生性 caveat——在 0–7 DTE 合约选择的决策瞬间给出用户自己的 DTE 分层历史。
- [新功能] 经既有 Playbook 候选路径显式创建「R4 · 持仓时间纪律（30分钟-3小时是你的盈利区）」候选（free-form 证据快照、幂等）：rule_text 内嵌创建时 personal-edge 端点返回的持仓时长分层数字、进场质量读数（<10 分钟单极低胜率对应「追高进场/速度不足强做」）与内生性提醒，定位为数据描述供本人复核，不构成建议。
- [测试] personal-edge 单元测试（无 build → None、持仓/DTE/标的/月度桶数学、n≥5 门槛、开仓中与缺净盈亏回合只计数不入统计、UTC−4 月度边界、非法阈值拒绝）+ 端点契约测试（not_built 诚实空态、ready 合同、10 分钟缓存命中与重置、非法 account_key 422）；前端 IntradayScanTable +6（盈利普通展示、亏损 n≥20 警示 tooltip、亏损 n<20 不警示、样本不足、端点缺席/not_built 标缺、账本行同语义标注）、NearExpiryContractPanel +4（DTE 提示行数值与内生性 tooltip、空档位无样本、端点缺席与 not_built 显式标缺）、既有 10 列布局断言更新。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.12「个人画像回灌」（端点合同、build 解析与 as-of 语义、三个消费面、诚实边界）；根 README 不承载日内列级细节，故未改动。
- [新功能] 日内候选新增「近 30 分钟位移」纯函数读数 `recent_displacement`（`src/opportunities/intraday_bursts.py::compute_recent_displacement`，复用波段爆发通道已取回的同一批 5m K 线，零新增请求）：取当前时段（休市取最近一个交易时段）最后 6 根 5m K 线，以窗口首根开盘价为「30 分钟前价格」参考点，输出 ATR 归一化的 `net_move / high_excursion / low_excursion / abs_range`；ATR 标尺优先日线 ATR14（`atr14_daily`），缺失时回退取证分析同款盘中代理「最近 20 根 5m 波幅均值 ×3」（`intraday_20bar_proxy_x3`）并显式标注基准，两者都不可得显式 `unavailable`，K 线不足 6 根显式 `insufficient_bars` + 实际根数，绝不 0 回填。
- [新功能] `POST /api/v1/opportunities/intraday-top` 候选行新增 additive `recent_displacement` 字段（`IntradayRecentDisplacement`），`signal_version` 升至 `intraday_session_evidence_v6`，并新增一条位移口径 limitations：它是标注，不进证据计数、不参与排序、不隐藏行。
- [新功能] 实时扫描表默认网格新增「近30分位移」列（紧跟「速度」，交易关键顺序＝涨跌/爆发/波段/速度/位移/形态）：`≥ +0.5` 或 `≤ −0.5 ATR` 用涨跌色强调并给出区间高低偏移，介于 ±0.5 之间弱化显示 +「未达 0.5」，K 线不足或 ATR 标尺不可得显式「标缺」；tooltip 原文携带 0.5 ATR 经验线的来源与实际使用的 ATR 基准；为保持默认 10 列，「波段vs大盘」下沉到展开行「研究读数」区（口径与 tooltip 原样保留）。
- [新功能] 经既有 Playbook 候选路径显式创建「R5 · 进场要求「已经在动」（近30分钟位移 ≥0.5 ATR）」候选（free-form 证据快照、幂等）：rule_text 内嵌 766 笔回合取证数字（进场几何无预测力、速死单 18.3% vs 赢家 71.2% 达到 ≥0.5 ATR、MFE/MAE 中位、84% 合约 ≤1DTE 零容忍）与全部 caveat（样本恰为最差两个月、按持仓时长分组的循环性、非官方 5m K 线、描述统计非建议）。
- [测试] 位移纯函数测试 +9（恰好 6 根 K 线边界窗口数学、窗口只取最后 6 根不吃早盘涨幅、负向/横盘保号、ATR14 与盘中代理基准选择及标注、非正 ATR14 回退、不足 6 根/零 K 线显式 insufficient、零波幅无 ATR14 显式 unavailable、休市按最近交易时段且与同日盘中口径一致）、builder 接线测试 +5（ATR14 标尺读数、代理回退、无 K 线不足、休市时段、run limitations 携带口径）、端点契约测试补 v6 版本与 `recent_displacement` 全字段断言；前端 IntradayScanTable +5（越线强调 up/down、未达 0.5 弱化、三类标缺、tooltip 基准原文、「波段vs大盘」下沉后仍在研究读数区可达）与默认 10 列断言更新，IntradayPage 列表断言同步。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.13「近 30 分钟位移」（取证证据表、窗口与 ATR 标尺口径、additive 响应合同、前端列与降列决策、R5 候选、诚实边界）；根 README 不承载日内列级细节，故未改动。
- [新功能] 规模与频率监控：`GET /api/v1/journal/v2/personal-edge` 响应新增 additive `discipline` 区块（既有字段一字不改，复用同一默认 build 与同一 10 分钟缓存），按月与「build 内实际存在的最后 20 个交易日」窗口输出 `trades_per_day`（笔数 ÷ 有入场的 ET 自然日）、`median/total_premium_at_risk`（`|opening_cash_flow|`）、`pnl_per_dollar_risked`（Σ净盈亏 ÷ Σ风险金额）、`median_episode_pnl`、`body_pnl`（去掉当月最好/最差各 5 笔，仅 n≥15）、`zero_dte_share`、`exact_fill_share`；取证背景为本人 1,554 笔期权回合全样本研究——进场几何（χ² p=0.105）、行情跟随（Kruskal p=0.337）、止损纪律三者皆平，真正变化的是每美元回报塌约 24 倍（5.83%→0.24%）、中位仓位 2.4 倍、日均笔数 +44%、盈亏集中到尾部赢家。
- [新功能] `/intraday` 脉搏条下方新增「规模与频率」一行 chip（`IntradayDisciplineStrip`）：近 20 个交易日的每美元回报 / 日均笔数 / 中位仓位 / 本体盈亏，注脚为端点返回的最早可得月份同口径基线（前端不硬编码任何数字），行尾恒显示「基于已发布证据 build #N · 截至 YYYY-MM-DD」；中性排版、无涨跌色、无建议措辞（镜子不是警报），tooltip 原文携带端点 limitations，缺分母显式「标缺」。
- [新功能] Journal「模式观察」下方新增同层兄弟区块「规模与频率」（`DisciplineMonthlyPanel`）：月度表（笔数/日均/中位仓位/风险金额合计/每美元回报/中位盈亏/本体盈亏/0DTE 占比/精确成交占比）+ 当前窗口脚注，`exact_fill_share<1` 的月份显式标注「成交明细为重建，时点仅供参考」，缺分母的读数显示「标缺」并把原因挂在 title 上。
- [改进] personal-edge `limitations` 追加两条诚实声明：重建成交明细（`has_exact_fill_times=0`，集中在 2026-04 前半段）月份的时点与每美元回报仅供参考；规模与频率的派生比率一律 fail-closed（分母为 0、无可用开仓现金流、本体盈亏样本 <15 笔时返回 null + 原因）。
- [新功能] 经既有 Playbook 候选路径显式创建「R6 · 规模与频率纪律（每美元回报才是真账）」候选（free-form 证据快照、幂等，id=9）：rule_text 内嵌 24 倍每美元衰减与三个统计上持平的对照、仓位 2.4 倍、频率 +44%、本体/尾部拆解、4 月重建成交明细 caveat（精确成交口径 4 月 0.72% / 5 月 0.95% / 6 月 0.16% / 7 月 0.24%）以及工作台按自身口径重算 build #3 的对照值，定位为数据描述供本人复核，不构成建议。
- [测试] 规模与频率服务单元测试 +5（ET≈UTC−4 自然日去重的日均笔数与 UTC 跨日边界、每美元回报与中位仓位数学、开仓现金流缺失只计缺席、本体盈亏 n≥15 门槛与 <15 的 null+原因、近 20 交易日窗口选取与起止日期、无现金流/合计为 0/无已知 DTE 三类零分母各自的原因区分、limitations 追加项）；端点契约测试 +2（`discipline` 为 additive 且既有字段逐字不变、窗口读数与重建成交标志）；前端 `IntradayDisciplineStrip` +5、`DisciplineMonthlyPanel` +5（数值与基线注脚、标缺与原因、limitations 原文 tooltip、重建成交行标注、未构建空态）。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.12.1「规模与频率」（三个对照皆平的取证表、additive 端点合同、前端两个消费面、R6 候选、重建成交与 fail-closed 诚实边界、与研究样本的口径差异）；根 README 不承载日内面板级细节，故未改动。
- [新功能] 日内实时扫描新增「哑火形态」标注（`fizzle_flag`，`signal_version` 升至 `intraday_session_evidence_v7`）：当前 15 分钟窗口同时满足 intraday 分层（`median_basis` 非上一时段回退）、窗口效率 ≥0.9（|收−开| ÷ 窗口高低差，几乎无回撤）与量比 <2.0 时命中；9,173 次爆发起点回放（21 标的 × 123 个交易时段，2026-02-05→08-03，用户自己的 5m K 线逐根重放、无未来函数）实测该形态 30 分钟内达到 ≥0.5 ATR 有利位移仅 26.8%（样本内 n=291）/ 25.4%（样本外 n=177），基准 47.8%/50.5%，方向一致性 20/20 标的、6/6 月、三把 ATR 标尺同号。additive 标注：不进 supports 计数、不参与排序、不隐藏行，不是卖出信号。
- [改进] 前端在既有「当前爆发」单元格内渲染 warning-muted 小徽标「哑火形态」（**不新增列**），tooltip 携带样本内外频率、基准、样本描述与固定诚实边界「这是形态描述与历史频率，不是卖出信号；方向本身在样本中约 53%，与掷硬币无实质差别」；未命中与标缺一律不渲染——缺席不是结论。
- [修复] 近 30 分钟位移的 ATR 回退标尺可比性缺陷：旧盘中代理（最近 20 根 5m 波幅均值 ×3）与真实日线 ATR14 之比在盘中 0.28→0.42→0.20 漂移（10:30–11:00 低估约 18–20pp、15:00–15:30 高估约 10–15pp）。回退顺序改为「日线 ATR14 →『上一批交易时段真实波幅均值』（≤3 个已结束时段、当日内恒定，`prior_sessions_true_range_mean`）→ 旧代理仅在无上一时段时兜底」，并新增 `atr_scale_comparability` / `atr_prior_session_count` 逐行标注该读数能否与 ATR14 口径横向比较；**日线 ATR14 主路径数值口径逐字未变**（有/无上一时段 K 线时结果完全相同，测试断言）。
- [改进] 波段爆发 `limitations` 追加两条实测性质：起速那一刻**方向不可预测**（P(方向)=50.6%、19 个候选因子方向 AUC 0.48–0.52、MFE/|MAE| 中位 1.026，因此系统不提供延续概率或真假速度评分）；中波段阈值 2.5 平均每个「有爆发的标的-交易日」触发 3.74 个窗口、其中 62.8% 落在开盘 stratum（回退基准让开盘 K 线天然显得超常）——**不改阈值**，只如实声明该口径产物。同时记录本版**刻意不做**「起速幅度分档」列（只描述波动幅度、无方向含义，表格已过密）。
- [新功能] 经既有 Playbook 候选路径显式创建「R7 · 起速那一刻分不出方向（哑火形态是唯一稳的回避）」候选（free-form 证据快照、幂等）：rule_text 内嵌方向 AUC 0.48–0.52、P(方向) 50.6%、MFE/|MAE| 1.026、哑火规则与样本内外频率、**已证伪**的「突破前 15 分钟区间」假设（AUC 0.496，三把标尺一致，记录以免重复尝试），以及「约 200 次比较，Bonferroni 下无一存活；按方向一致性与样本外稳定性判断」，定位为数据描述供本人复核，不构成建议。
- [测试] 哑火形态真值表 +12（命中、三个条件各自否定与多条件同时未满足、开盘分层永不命中、窗口高低相等即标缺、缺 median_basis/vol_norm/窗口 K 线不足、profile 只描述当前窗口、fail-closed profile 携带标缺读数）；ATR 标尺 +5（ATR14 主路径逐字不变、上一时段回退口径与跳空 true range、时点伪影消除对照、旧代理降为兜底并标不可比）；候选接线 +4（verbatim 复制、不改 supports/研究状态、缺字段与缺 profile 各自标缺）；端点契约 v7 + additive-only + limitations 断言；前端标记 +5（命中渲染且不新增列、tooltip 数字与诚实边界、未命中/标缺/旧载荷三种不渲染）。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` 新增 §2.14「哑火形态与 ATR 标尺可比性修正」：把回放研究的**否定性结论**（起速那一刻方向不可预测）放在最前，列明本版刻意**不做**的四件事（延续概率/真假速度评分、起速幅度分档列、改阈值、重试已证伪的前 15 分钟区间突破假设）、哑火规则与样本内外证据、ATR 回退与可比性标签、阈值触发密度的已知性质与后续跟进判断、R7 候选与诚实边界；§2.13 加 v7 回退层变更指引。根 README 不承载日内面板级细节，故未改动。
- [修复] 盘中 5m K 线时间戳口径：Moomoo `request_history_kline` 的 `time_key` 按 K 线**结束**时间打标，此前被当成开始时间使用，`09:30 ≤ 标签 < 16:00` 实际取到的是 **09:25–15:55**——混进一根盘前 K 线、又丢掉收盘集合竞价那根。2026-08-04 用只读 OpenD 独立复核确认（官方日线开盘价 == 标签 09:35 那根的开盘价、官方日线收盘价 == 标签 16:00 那根的收盘价，**25/25 个标的-交易日逐分钱吻合**，START 口径 0/25；标签 09:35 中位量是标签 09:30 的 15–25 倍；延伸时段端点为 04:05 与 20:00）。新增 `normalize_bar_label_convention(bars, source=...)` 只对已知按结束时间打标的源（`BAR_LABEL_END_SOURCES`）整体前移一个 K 线周期，未知/缺失 source 一律不猜测；调用点为 5m K 线进入研究管线的唯一入口 `_fetch_intraday_5m_bars`，波段爆发 / 近30分位移 / v4 形态对比一次性修正，`intraday_bursts` 保持纯函数不为供应商分叉；`/stocks/{code}/history` 公开载荷未改动。
- [改进] 因上条为**数值口径变更**，`INTRADAY_TOP_SIGNAL_VERSION` 升至 `intraday_session_evidence_v8`（响应 Literal、TS 类型与前端注脚同步）：所有窗口、中位数、波段标签、速度与位移读数整体前移一根 K 线并换掉两侧边界样本，历史截图与本版不可逐值对照。真实端点抽样（TestClient，2026-08-03 时段，ATR14 标尺）当前窗口分 NVDA 3.49→10.02、MU 0.80→4.04（方向 up→down）、AMZN 0.80→9.44（方向 up→down），近30分位移 net NVDA −0.228→−0.155、MU +0.111→+0.032、AMZN +0.074→−0.062（变号）。
- [改进] 波段爆发 `limitations` 追加两条：K 线时间戳口径修正本身（含复核证据与「历史读数会变」的显式声明）；以及修正后**收盘集合竞价的口径性质**——最后一个 15 分钟窗口（15:45–16:00）现在真正含收盘竞价那根，其成交量通常是当日中位 K 线的 5–20 倍，量比被机械放大，几乎每个标的每天都会在 15:45 出现高分窗口。**不改阈值**，只如实声明该口径产物。
- [修复] 近 30 分位移的**循环性更正**：0.5 这条线源自按**持仓时长**分组的分层，而持仓时长由结果决定——该分层按构造即循环。2m 回放研究（3,492 次 EMA8/13 回踩持稳进场）量化：循环记分下「速度未死 vs 已死」P(>0) 差 **37.6 个百分点**（54.4% vs 16.8%），改为只从第 15 分钟检查点**向前**计分后只剩 **0.1 个百分点**（49.9% vs 49.8%），且样本内微弱效应**样本外反号**（+0.122 → −0.128）。保留读数与 0.5 参考刻度（它仍是对「有没有在动」的诚实描述），但**删除全部存活框架**：弱化态文案 `未达 0.5` → `不足 0.5 ATR`，列头与逐行 tooltip 以更正**开头**，完整口径文本在取证数字紧后接整段更正，简短口径行同步；后端 `DISPLACEMENT_LIMITATION_LINE`、`IntradayRecentDisplacement` docstring 与模块常量注释同步。
- [新功能] 经既有 Playbook 候选路径显式创建「R8 · 进场择时没有边际（两个时间框架、13,136 次事件）」候选（free-form 证据快照、幂等，**id=11**）：rule_text 内嵌回踩持稳 P(方向)=49.9% vs 时间匹配随机对照 49.8%（n=3,492 / 对照 10,476，MFE/|MAE|=0.994、95% CI [48.2, 51.5] 含 50%）、用户自己写下的 S1 形状 `hold_8`=47.6%**比随机还低**、唯一为正的 `hold_13`=55.6%（17/20 标的、5/6 个月）却是 S1 的**反面**且只值约 6.4 bps 正股位移（小于 0-3DTE 来回价差）、**99.97%** 的冲量 40 分钟内都会回到 2m EMA8 区域（「等回调」不是过滤器）、1 分钟减速离场不改善均值（−0.008 vs +0.005）但砍掉 38% 标准差（0.753→0.463，故 R1 的价值是波动与时间价值成本管理而非预测），以及上条循环性更正；标注样本仅 6 个月、以正股位移而非期权 P&L 度量、Bonferroni 无一存活，定位为数据描述供本人复核，不构成建议。
- [改进] `PlaybookPanel` 候选与规则正文补 `whitespace-pre-line`：`rule_text` 是 append-only 冻结文本、分段靠换行，此前 R5–R8 这类多段取证记录会塌成一坨。只改渲染，不动数据。
- [测试] 时段边界回归 +2（四个标的逐一断言常规时段恰 78 根、首末为 09:30/15:55、**收盘集合竞价那根被保留**且 OHLCV 等于供应商标签 16:00 那根、**盘前那根被排除**、开盘那根等于供应商标签 09:35；归一函数只对已知源生效、不就地改写入参、未知源与不可解析时间戳原样返回）；校准回归重跑（fixture 重采为供应商原始时间戳并放宽到 09:00–16:30，因此现在真正在测这条链路）——阈值**无需重新校准**，仅 AMZN 开盘波断言改为「仍是强 up 窗口」（score 18.72→8.56，仍 ≥8.0，是被 30 分钟去重规则挤出 legs，不是检测失败）；前端位移文案断言改写 +12（更正必须打头、37.6→0.1pp 与 n=3,492 原文、`不足 0.5 ATR` 取代 `未达 0.5` 且旧文案不得残留、完整口径携带两种记分数字与样本外反号）；`PlaybookPanel` 换行保留 +1。
- [文档] `New-docs/phase1/06_DAILY_OPPORTUNITY_BOARD.md` §2.13 新增 v8 两节（循环性更正的双记分对照表与文案变更清单；K 线时间戳口径修正的三条独立复核证据、修正方式与调用点、真实端点前后抽样表、校准重跑结论与 AMZN 唯一变化项、收盘竞价的新口径性质）并在旧 tooltip 原文处加作废指引；新增 §2.15 记录 R8 候选与 append-only 不可改写的处置方式。根 README 不承载日内面板级细节，故未改动。

## [3.11.0] - 2026-03-27

### 发布亮点

- 🎨 **Web 工作台完成一轮 UI 统一与双主题升级** — 首页、问股、回测、持仓和设置页进一步收口到统一设计 token、输入表面和状态表达；新增完整浅色主题，并支持浅色 / 深色一键切换与持久化保存。
- 🤖 **Bot / Agent 能力重新补回主分支** — 恢复 `/history`、`/strategies`、`/research` 等命令，`/ask` 继续支持多股对比与组合视角；Deep Research、事件监控与 schedule 轮询链路重新接回主线能力。
- 🔒 **安全性与运行稳态同步补强** — 修复 `X-Forwarded-For` 限流绕过风险，恢复 LiteLLM 官方 PyPI 安装路径，Tushare 初始化不再依赖本地 SDK，降低 Docker、桌面打包和环境重建时的脆弱点。
- 🖥️ **日常使用细节继续打磨** — 修复首页港股自动补全提交、登录页首屏主题闪烁、历史长股票名重叠，以及 Telegram Markdown 解析失败时整条通知发送中断等问题。

### 新功能

- 🎨 **全新浅色主题与双主题切换上线** — Web 工作台新增完整浅色主题，并支持在侧边栏中一键切换浅色 / 深色模式；主题选择会持久化保存，刷新页面后仍保持当前偏好。此次升级不是局部配色微调，而是对卡片层级、边界对比、输入表面、状态提示和页面背景做了一整套 light theme 重绘。
- 🤖 **补回主分支缺失的 Agent / Bot 能力** — `#648` / `#649` 已重新补回 `main`：Bot 恢复 `/history`、`/strategies`、`/research`，`/ask` 保留多股对比与组合视角；Deep Research 与 Event Monitor 的配置重新在 Web 设置页可见并可编辑，schedule 模式也重新接入事件告警轮询。

### 改进

- 🖥️ **核心页面统一到同一套工作台视觉语言** — `Home / Chat / Backtest / Portfolio / Settings` 进一步收口到共享设计 token、`input-surface` 输入体系、空态/错误态表达和抽屉遮罩语义，减少页面之间的视觉割裂与局部私有样式漂移。
- 💬 **问股交互可达性与反馈增强** — 问股页补强了会话导出、通知发送、消息复制、历史删除与追问上下文提示；AI 回复操作不再过度依赖 hover，触屏设备和小屏场景下也能直接触达关键按钮。
- 📊 **回测与持仓页表面和状态表达继续标准化** — 回测页筛选控件、布尔状态、结果表格与汇总卡片统一到共享输入/状态原语；持仓页的导入反馈、汇率刷新提示、空态与警示信息进一步归口到共享组件，减少页面级重复实现。
- 🧭 **导航与页面壳层协同优化** — 侧边栏主题切换、问股完成角标、移动端抽屉遮罩和主内容滚动契约进一步统一，首页、问股和回测在桌面端与移动端的切页体验更稳定。

### 测试

- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

### 修复

- 🌗 **Web 首屏默认主题预设为深色** — `apps/dsa-web/index.html` 现在会在 React 挂载前读取本地保存的主题偏好；若没有已保存值，则立即给 `<html>` 预设 `dark` 并同步 `color-scheme`，避免首页和登录页首屏先闪出浅色主题。
- 🔐 **登录页独立主题层收口** — 登录页输入框、标签、切换按钮和按钮文案现在使用独立的 `--login-*` 视觉 token，不再继承全局浅/深主题文字色；即使浏览器缓存了浅色主题，登录页仍保持稳定的深色视觉与青色密码输入表现，避免密码圆点和文案落成黑色。
- 🖥️ **首页港股代码输入修复** — Web 首页分析输入框现在可正确接受港股代码与自动完成选中的港股项，补齐 `00700.HK` / `HK00700` 等格式识别，避免提交时误报“请输入有效的股票代码或股票名称”。

- 🔒 **认证限流 X-Forwarded-For 取值修复（CWE-345）**（#841 / #842）— `get_client_ip()` 从取 `X-Forwarded-For` 最左值改为最右值，防止攻击者通过伪造首部旋转限流桶绕过暴力破解保护；仅影响 `TRUST_X_FORWARDED_FOR=true` 且单层可信反向代理的部署场景，多级代理环境需按部署文档评估配置。
- 📦 **恢复 LiteLLM 官方 PyPI 安装并锁定安全上限** — `requirements.txt` 重新使用 `pip install litellm` 的官方 PyPI 安装路径，并在保留历史最低要求 `>=1.80.10` 的同时增加 `<1.82.7` 的安全上限，避免误装已被移除的 `1.82.7` / `1.82.8` 风险版本；Windows 桌面打包脚本也同步回退到标准 `pip install -r requirements.txt` 链路，减少特殊下载分支带来的维护成本。
- 📨 **Telegram Markdown 解析失败回退纯文本**（fixes #850）— `src/notification_sender/telegram_sender.py` 现在会在 Telegram 返回 `HTTP 400` 且包含 `can't parse entities` / Markdown 解析错误时，自动去掉 `parse_mode` 后重试纯文本发送，避免 `*ST` 等正文内容直接导致整条通知失败。
- 🔢 **A 股同码实时行情保留交易所提示**（fixes #852）— `DataFetcherManager` 与 `TushareFetcher` 现在会保留 `SZ000001` / `000001.SZ` 这类显式沪深提示，旧版 Tushare 实时行情降级分支不再把深市 `000001` 误判成 `sh000001` 上证指数。
- 🎯 **多 Agent 次优买点不再盲目复制理想买点**（fixes #851）— 当多智能体结果缺少独立 `secondary_buy` 时，仪表盘现在优先展示 `N/A` 而不是把 fallback 值硬拷贝成与 `ideal_buy` 完全相同，减少误导性的双买点展示。
- 🧩 **Tushare 初始化不再强依赖本地 SDK 包** — `TushareFetcher` 现在直接使用内置 HTTP client 访问 Tushare Pro，不再在启动阶段先 `import tushare` 才能初始化；修复了 Docker、桌面打包或环境重建后因缺少 `tushare` 包而提前报 `No module named 'tushare'` 的问题，并补充对应回归测试。
- ⚙️ **`daily_analysis` 工作流补齐 `DEEPSEEK_API_KEY` 映射** — GitHub Actions 每日分析工作流现在会正确透传 `DEEPSEEK_API_KEY`，避免云端任务配置了密钥却在运行时拿不到对应环境变量。
- 🖥️ **历史列表过长股票名称截断与悬停展示**（fixes #815）— 历史列表中过长的股票名称, 现在会按字符类型自动截断（英文15/中文8/混合10字符），默认显示截断结果，悬停时展示完整名称；解决 1920x1080 分辨率下股票名称与右侧状态标签文字重叠的问题。新增 `stockName.ts` 工具函数并补充对应测试。

### 文档

- 🧾 **README 捐赠入口更新为小红书二维码** — README 及中英文说明中的赞助入口更新为小红书二维码素材，保持展示口径一致。

## [3.10.1] - 2026-03-24

### 新功能

- 🔔 **Web 端分析推送通知开关**（#808）— 首页分析按钮旁新增「推送通知」复选框，默认勾选；取消勾选时本次分析不发送 Telegram/企业微信等推送。API `POST /api/v1/analysis/analyze` 新增 `notify` 字段（`bool`，默认 `true`），不传时行为与修改前一致，Bot 和定时任务不受影响。

### 改进

- 🖥️ **问股 / 回测页面布局与壳层协同优化** — 统一 Chat / Backtest 页面容器、共享 UI 状态和跟随问答交互路径，移除部分硬编码高度限制，让导航框架内的填充与滚动行为更连贯。
- 🎨 **全局视觉与共享组件继续收敛** — Light theme 引入动态 HSL 阴影体系，统一侧边栏激活态、告警组件对比度和聊天气泡样式，并把部分零散内联样式收口为语义化 CSS 变量，提升一致性与可维护性。

### 修复

- 🖼️ **系统设置智能导入文件选择恢复** — 修复了“系统设置 > 基础设置 > 智能导入”模块中 “选择图片 / 选择文件” 两个按钮点击无响应的问题。
- 🖥️ **移动端滚动与交互层级修复** — 解决主题切换菜单在移动端被主内容遮挡的 z-index 冲突，并恢复首页长报告场景下的正常纵向滚动，不影响其他页面现有滚动行为。
- 🧾 **Markdown 纯文本复制清洗增强** — 改进纯文本导出算法，复制分析报告时会更稳定地清除表格分隔符等 Markdown 痕迹，提升分享和归档内容的纯净度。
- 🧠 **Trading philosophy injection 覆盖 legacy + Agent 全链路**（#810）— `GeminiAnalyzer`、单 Agent 模式和 skill-aware Prompt 现在共享同一套策略注入状态；只有隐式回落到内置默认 `bull_trend` 时才保留旧的趋势型提示，显式策略选择或自定义默认 skill 不再被偷偷叠加 `MA5>MA10>MA20` 多头基线。
- 🛠️ **后端 CI 依赖安装链路稳态化**（#835）— 拆分 backend gate 阶段、为依赖安装增加重试，并把 CI 用的 `litellm` 安装来源调整为更稳定的 GitHub 源，降低依赖解析抖动导致的 backend gate 偶发失败。
- 🪟 **Windows 桌面发版构建恢复 LiteLLM 安装兼容性** — `scripts/build-backend.ps1` 现在会先过滤 `requirements.txt` 中的 LiteLLM GitHub 源包，再下载对应 tag 的 zipball 到本地移除上游可选 `enterprise/` 目录后安装，绕过 Windows runner 上 Poetry 构建 wheel 时把目录误当文件打包导致的失败；同时补上 `pip install` 退出码检查，避免依赖安装失败后只在后续 `python-multipart` 校验阶段才暴露成次生报错。

### 测试

- 🧪 **问股 / 回测 / 智能导入回归覆盖补齐** — 同步更新 E2E 冒烟期望，补充 `DashboardStateBlock`、Chat 页、智能导入文件选择与相关交互回归断言，确保近期 UI 调整后的关键路径仍可稳定通过。

## [3.10.0] - 2026-03-24

### 发布亮点

- 🔎 **自动补全与索引工具扩展到三市场** — 补全索引生成链路现在同时覆盖 A 股、港股、美股，配套新增 Tushare 股票列表抓取工具与更完整的静态索引数据，让首页搜索入口从“能用”走向“更全、更稳”。
- 🖥️ **Dashboard 与报告查看体验继续收口** — 首页 Dashboard 面板、状态边界、字体层级和完整报告表格密度完成一轮统一；报告详情也补齐了 Markdown/纯文本复制与更可靠的按钮交互，减少历史报告查看与分享时的摩擦。
- 🤖 **Agent skill 与市场语义边界更清晰** — skill bundle、默认策略、回测汇总语义和兼容接口进一步收敛；同时分析 Prompt 不再默认写死 A 股上下文，美股和港股分析也能按各自市场规则生成更贴切的内容。
- ⏰ **定时与桌面配置能力更贴近真实使用场景** — 桌面端支持 `.env` 导入导出；`python main.py --schedule --stocks ...` 也不再把启动时股票快照错误带入后续计划执行，定时任务会跟随最新保存的 `STOCK_LIST`。
### 新功能

- 💾 **桌面端 `.env` 备份/恢复入口**（#754）— 桌面模式下的系统设置页新增 `导出 .env` / `导入 .env` 按钮，可直接备份当前已保存配置，或把备份文件中的键值合并恢复到当前桌面端 `.env`；导入沿用现有 `config_version` 冲突保护与运行时重载链路，不改变现有桌面端便携模式路径。
- 📊 **Tushare 股票列表获取工具** — 新增 `scripts/fetch_tushare_stock_list.py`，支持从 Tushare Pro 获取 A股、港股、美股列表信息并保存为 CSV，配有分页读取、智能限流、错误处理和进度提示；新增对应使用文档 `docs/TUSHARE_STOCK_LIST_GUIDE.md`。
- 🔎 **索引生成脚本多市场支持** — `generate_index_from_csv.py` 重构为支持 Tushare 和 AkShare 双数据源，同时覆盖 A股、港股、美股三个市场；新增按市场分类的别名映射（A股、港股常见别名，美股常用股票英文缩写）；添加 `--source` 参数切换数据源、`--test` 参数验证模式；严格过滤美股 DUMMY 记录。
- 🔎 **索引生成脚本增强** — `generate_stock_index.py` 新增 `--test`/`-t` 测试模式和 `--verbose`/`-v` 详细输出模式，添加市场分布统计，优化 JSON 输出格式。
- 📋 **首页完整报告支持双模式复制** — 历史报告详情头部新增“复制 Markdown 源码”和“复制纯文本”工具按钮；前者保留原始 Markdown 结构，后者去除常见 Markdown 格式符号，方便分享、归档和跨报告比对。复制按钮文案会跟随 `REPORT_LANGUAGE` 保持中英文一致，避免英文报告页出现中文固定文案。
- 🧩 **个股分析页补齐关联板块展示**（#669）— A 股分析写路径现在会把 `belong_boards` 一次性写入 `fundamental_context` / `fundamental_snapshot`，结构化报告详情同步新增 `belong_boards` 与 `sector_rankings` 字段，Web 个股分析页首屏可直接展示所属板块及其是否命中当日板块涨跌榜；无数据时保持 fail-open 隐藏，不影响现有分析主流程。

### 改进

- 🖥️ **Dashboard 面板统一化（PR7-2）** — 新增 `DashboardPanelHeader` 和 `DashboardStateBlock` 作为历史、报告、资讯、任务和透明度等面板的通用组件；统一了各面板标题层级、加载/空态/错误态和 CSS 变量 token。
- 🖥️ **HomePage 状态边界收口（PR7-2）** — 引入 `useHomeDashboardState` hook，集中 `stockPoolStore` 状态选取逻辑，移除 `HomePage` 中重复的本地状态派生和回调定义。
- 🧭 **Agent skill 统一到单一配置语义** — Multi-Agent runtime、API、Web chat 和配置元数据统一围绕 `skill` 概念收敛；`/api/v1/agent/skills` 成为主发现入口，`AGENT_SKILL_*` 成为主配置面，内置 skill 元数据也开始声明默认启用、排序优先级、market regime tag 等信息，减少默认策略散落在代码里的隐式耦合。
- 🔎 **自动补全索引数据更新** — 重新生成 `stocks.index.json`，涵盖 A股、港股、美股三个市场，提升自动补全覆盖率。
- 🧾 **Dashboard 字体与完整报告表格密度微调** — 收敛首页侧栏、空状态、历史操作区的字体层级，并将完整 Markdown 报告表格 `th/td` 的内边距调整到更紧凑的 4-6px 区间，让信息密度与现有 Dashboard 视觉节奏更一致。

### 修复

- ⏰ **定时模式不再锁定启动时 CLI 股票快照** — `python main.py --schedule --stocks ...` 现在不会让后续计划执行沿用启动时的旧股票列表；定时任务每次触发前都会重新读取最新保存的 `STOCK_LIST`，确保 WebUI 或 `.env` 更新后的自选股配置能参与后续推送。
- 🌍 **LLM Prompt 按股票市场动态注入上下文** — 分析链路不再把市场规则写死成 A 股；系统 Prompt 会根据股票代码识别 A 股、港股或美股，并注入对应的角色描述与交易规则提示，减少跨市场分析出现口径错位或结论失真的问题。
- 🔎 **美股自动补全复用 ticker 去重** — `generate_index_from_csv.py` 在导入 Tushare `us_basic` CSV 时会先按 `ts_code` 折叠复用的美股 ticker，优先保留更可能仍在使用的记录，避免 `stocks.index.json` 出现重复 `canonicalCode` 后让 Web 自动补全展示历史名称或提交歧义代码。
- 🧾 **Web 报告详情复制交互稳定性修复**（#749）— `ReportDetails` 中“原始分析结果 / 分析快照”的复制按钮补齐可点击层级，避免被下方 JSON 内容覆盖；两个面板的复制提示也改为各自独立，不再出现复制一个后两个按钮同时显示“已复制”的误导反馈。
- 📊 **Agent skill 回测与兼容接口语义收敛** — `get_skill_backtest_summary` 现在要求显式传入 `skill_id`，缺失时返回明确校验提示；仓库尚未持久化真实 skill 级汇总时会返回明确的 unsupported/info 响应，并保留 `normalized` 与 `*_pct` 兼容字段，避免沿用 overall 指标误导 Agent 或用户。
- 🔧 **Skill 默认选择与兼容层行为加固** — `allowed-tools` 会继续仅作为 `SKILL.md` bundle 元数据保留，不再泄露到运行时工具选择；`/api/v1/agent/strategies` 恢复旧 payload 形状；显式传入 `skills: []` 时会清空陈旧上下文；当用户明确选择策略 skill 时不再偷偷叠加默认 bull-trend，而在 `AGENT_SKILLS` 为空时则统一只回落到单一主默认 skill。

### 测试

- 🧪 **Dashboard 组件测试覆盖率扩展（PR7-2）** — 新增 `ReportNews` 和 `TaskPanel` 测试；对 `HistoryList`、`ReportDetails`、`HomePage`、`useDashboardLifecycle` 和 `stockPoolStore` 增强了断言覆盖，包括删除回退、移动端抽屉和任务生命周期等场景。
- 🧪 **多市场索引生成测试补齐** — 新增 `tests/test_generate_index_from_csv.py`，覆盖 Tushare/AkShare 双数据源解析、多市场判断、美股 DUMMY 过滤与重复 ticker 去重等核心路径。
- 🧪 **关联板块写入与 API 契约回归** — 新增 `tests/test_pipeline_related_boards.py`，并补充分析历史与分析接口契约测试，确保 `belong_boards` / `sector_rankings` 只做增量扩展且保持 fail-open。
- 🧪 **定时模式股票列表语义回归测试** — 新增 `tests/test_main_schedule_mode.py`，覆盖定时模式忽略启动时 `--stocks` 快照、单次运行仍保留 CLI 股票覆盖的边界场景。

### 文档

- 📘 **新增 Tushare 股票列表工具文档** — 新增 `docs/TUSHARE_STOCK_LIST_GUIDE.md`，说明股票列表抓取工具的使用方法、数据格式和常见问题。
- 🌍 **补齐定时模式与关联板块的双语说明** — `docs/full-guide.md` / `docs/full-guide_EN.md` 现在明确说明 scheduled mode 会在每次执行前重新读取 `STOCK_LIST`，并同步补充个股关联板块展示能力说明，减少配置预期偏差。
- 🧭 **调整 Agent 术语兼容文案** — README、双语文档、设置页与问股界面继续以“策略”作为用户入口主称呼，同时补充 `skill` 作为内部统一命名，降低迁移期理解成本。

## [3.9.0] - 2026-03-20

### 发布亮点

- 🤖 **模型链路与报告语言更灵活** — Agent 现在可以通过 `AGENT_LITELLM_MODEL` 独立选择模型链路，普通分析与 Agent 报告也可通过 `REPORT_LANGUAGE=zh|en` 输出统一语言，减少“英文内容 + 中文壳子”这类混排问题，并允许团队分别权衡主分析与 Agent 的成本、速度和能力。
- 🔎 **首页分析体验完成一轮闭环优化** — 首页新增 A 股自动补全，支持代码、中文名、拼音和别名检索；同时 Dashboard 状态收口到统一 store，历史、报告、新闻与 Markdown 抽屉的交互更稳定，“Ask AI” 追问也会优先携带当前报告上下文。
- 💬 **通知与检索能力继续外扩** — 新增 Slack 一等通知渠道；SearXNG 在未配置自建实例时可以自动发现公共实例并按受控轮询降级；Tavily 时效新闻链路修复后，严格时效过滤不再错误丢光有效结果。
- 💼 **持仓与市场复盘链路更稳** — A 股 market review 可选接入 TickFlow 强化指数与涨跌统计；持仓账本写入改为串行化以缩小并发超卖窗口；汇率刷新入口和禁用态提示也更加清晰，减少用户误判。

### 新功能

- 🔎 **Web 股票自动补全 MVP** — 首页分析输入框新增本地索引驱动的自动补全，支持股票代码、中文名、拼音和别名匹配；选中候选后会提交 canonical code，并透传 `stock_name`、`original_query`、`selection_source` 到分析请求、任务状态和 SSE 事件；索引加载失败时自动退回旧输入模式，不阻断原有提交流程。同步补充了静态索引加载器、索引生成脚本和前后端契约测试。分阶段进行开发，第一阶段仅支持 A 股。
- 💬 **Slack 一等通知渠道** — 新增 Slack 原生通知支持，同时支持 Bot Token 和 Incoming Webhook 两种接入方式；同时配置时优先使用 Bot API，确保文本与图片发送到同一频道；Bot Token 模式支持图片上传（raw body POST，不使用 multipart）；新增 `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID`、`SLACK_WEBHOOK_URL` 配置项，GitHub Actions 工作流同步补齐对应 Secrets 传递。
- 🌍 **报告输出语言可配置**（Issue #758）— 新增 `REPORT_LANGUAGE=zh|en`，默认 `zh`；语言设置会同步注入普通分析与 Agent Prompt，并覆盖 Markdown/Jinja 模板、通知 fallback、历史/API `report_language` 元数据及 Web 报告页固定文案，避免“英文内容 + 中文壳子”的混合输出。
- 🚀 **Agent 与普通分析模型解耦**（Issue #692）— 新增 `AGENT_LITELLM_MODEL`（留空继承 `LITELLM_MODEL`，无前缀按 `openai/<model>` 归一）；Agent 执行链路与 `/api/v1/agent/models` 的 `is_primary/is_fallback` 标记改为基于 Agent 实际模型链路；系统配置与启动期校验补齐 `AGENT_LITELLM_MODEL` 的 `unknown_model/missing_runtime_source` 检查；Web 设置页新增 Agent 主模型选择并与渠道模式运行时配置同步。
- 🔎 **SearXNG 公共实例自动发现与受控轮询**（#752）— 新增 `SEARXNG_PUBLIC_INSTANCES_ENABLED`，在未配置 `SEARXNG_BASE_URLS` 时默认从 `searx.space` 拉取公共实例列表，并按受控轮询顺序选择实例；同次请求内遇到超时、连接错误、HTTP 非 200 或无效 JSON 会自动切换到下一个实例。已配置自建实例的用户保持原有优先级与语义不变；`daily_analysis` GitHub Actions 工作流也已支持显式透传该开关并在启动日志中展示当前状态。
- 📈 **TickFlow market review enhancement** (#632) — 新增可选 `TICKFLOW_API_KEY`；配置后，A 股大盘复盘的主要指数行情优先尝试 TickFlow；若当前 TickFlow 套餐支持标的池查询，市场涨跌统计也会优先尝试 TickFlow。失败或权限不足时立即回退到现有 `AkShare / Tushare / efinance` 链路；板块涨跌榜回退顺序保持不变。接入层同时适配了真实 SDK 契约：主指数查询按单次请求上限分批拉取，并将 TickFlow 返回的比例型 `change_pct` / `amplitude` 统一转换为项目内部的百分比口径。

### 改进

- **Dashboard state slice and workspace closure** — moved Home / Dashboard state into `stockPoolStore`, consolidated history selection, report loading, task syncing, polling refresh, and markdown drawer handling under a single state slice.
- **Dashboard panel standardization** — kept the current dashboard layout contract stable while unifying history, report, news, and markdown presentation with shared tokens, standardized states, and bounded in-panel scrolling for the history list.
- **Dashboard-to-chat follow-up bridge** — routed “Ask AI” follow-ups through report-context hydration instead of direct cross-page state coupling, while keeping chat sends usable when enriched history context is still loading.
- 💼 **持仓账本并发写入串行化**（#742）— 持仓源事件写入/删除现在会在 SQLite 下先获取串行化写锁，减少并发卖出把超售流水写入账本的窗口；直接持仓写接口在锁竞争时返回 `409 portfolio_busy`，CSV 导入保持逐条提交并把 busy 计入 `failed_count`。
- 💱 **持仓页汇率手动刷新入口补齐**（#748）— Web `/portfolio` 页面现在会在“汇率状态”卡片中展示“刷新汇率”按钮，直接调用现有 `POST /api/v1/portfolio/fx/refresh` 接口；刷新后会仅重载快照与风险数据，并以内联摘要反馈“已更新 / 仍 stale / 刷新失败”的结果，减少用户对 `fxStale` 长时间停留的误解。

### 修复

- 🔎 **Web 自动补全 Enter 提交语义修正** — 股票自动补全在搜索命中候选时不再默认高亮第一项；候选列表展开但用户尚未用方向键或鼠标明确选中时，按 Enter 会继续提交原始输入，避免手动输入被第一条候选静默覆盖。
- 🌍 **补齐 `REPORT_LANGUAGE` 启动解析与历史展示本地化边界** — `Config` 在启动时继续遵循“真实环境变量优先、`.env` 兜底”的既有语义，并在两者冲突时输出显式告警，减少 `REPORT_LANGUAGE` 来源不清带来的误判；同时 `/api/v1/history/{id}` 英文详情响应会同步本地化 `sentiment_label`，历史 Markdown 也会正确识别英文 `bias_status` 的风险等级 emoji，避免出现 `乐观` 或 `🚨Safe` 这类中英混排/误报展示。
- 📰 **Tavily 时效新闻检索发布时间映射修复**（#782）— Tavily 在股票新闻和严格时效的情报维度中现在会显式使用 `topic="news"`，并兼容 `published_date` / `publishedDate` 两种发布时间字段；修复了 Tavily 明明返回结果却在后续硬过滤阶段被全部记为 `drop_unknown` 丢弃的问题，同时将机构分析、业绩预期、行业分析等分析型维度恢复为宽源搜索，不再被统一压缩成新闻模式。
- 💱 **持仓页汇率刷新禁用语义修正**（#772）— 当 `PORTFOLIO_FX_UPDATE_ENABLED=false` 时，`POST /api/v1/portfolio/fx/refresh` 现在会返回显式 `refresh_enabled=false` 与 `disabled_reason`，Web `/portfolio` 页面会明确提示“汇率在线刷新已被禁用”，不再误报“当前范围无可刷新的汇率对”。
- 🤖 **Agent timeout and config hardening** — `AGENT_ORCHESTRATOR_TIMEOUT_S` now also protects the legacy single-agent ReAct loop, parallel tool batches stop waiting once the remaining budget is exhausted, and invalid numeric `.env` values fall back to safe defaults with warnings instead of crashing startup.
- 🌐 **CORS wildcard + credentials compatibility** — `CORS_ALLOW_ALL=true` no longer combines `allow_origins=["*"]` with credentialed requests, avoiding browser-side cross-origin failures in demo/development setups.
- 🧭 **Unavailable Agent settings hidden from Web UI** — Deep Research / Event Monitor controls are now treated as compatibility-only metadata in the current branch and are removed from the Settings page to avoid exposing non-functional toggles.

### 文档

- 新增 Ollama 本地模型配置说明，同步更新 `README.md` 与 `docs/README_EN.md`（Fixes #690）
- 完善 Ollama 配置说明：`docs/full-guide.md` / `docs/full-guide_EN.md` 环境变量表与 Note 补充 `OLLAMA_API_BASE`，避免英文用户误以为 Ollama 不能作为独立配置入口；合并重复的 `OLLAMA_API_BASE` 条目为单一条目
- 明确文档同步治理边界：补充 `README.md`、专题文档、双语文档与交付说明之间的默认同步规则，减少后续文档漂移

## [3.8.0] - 2026-03-17

### 发布亮点

- 🎨 **Web 界面完成一轮骨架升级** — 新的 App Shell、侧边导航、主题能力、登录与系统设置流程已经串成统一体验，桌面端加载背景也完成对齐。
- 📈 **分析上下文继续补强** — 美股新增社交舆情情报，A 股补齐财报与分红结构化上下文，Tushare 新接入筹码分布和行业板块涨跌数据。
- 🔒 **运行稳定性与配置兼容性提升** — 退出登录会立即让旧会话失效，定时启动兼容旧配置，运行中的 `MAX_WORKERS` 调整和新闻时效窗口反馈更清晰。
- 💼 **持仓纠错链路更完整** — 超售会被前置拦截，错误交易/资金流水/公司行为可以直接删除回滚，便于修复脏数据。

### 新功能

- 📱 **美股社交舆情情报** — 新增 Reddit / X / Polymarket 社交媒体情绪数据源，为美股分析提供实时社交热度、情绪评分和提及量等补充指标；完全可选，仅在配置 `SOCIAL_SENTIMENT_API_KEY` 后对美股生效。
- 📊 **A 股财报与分红结构化增强**（Issue #710）— `fundamental_context.earnings.data` 新增 `financial_report` 与 `dividend` 字段；分红统一按“仅现金分红、税前口径”计算，并补充 `ttm_cash_dividend_per_share` 与 `ttm_dividend_yield_pct`；分析/历史 API 的 `details` 追加 `financial_report`、`dividend_metrics` 可选字段，保持 fail-open 与向后兼容。
- 🔍 **接入 Tushare 筹码与行业板块接口** — 新增筹码分布、行业板块涨跌数据获取能力，并统一纳入配置化数据源优先级；默认按上海时间区分盘中/盘后交易日取数，优先使用 Tushare 同花顺接口，必要时降级到东财。
- 🧱 **Web UI 基础骨架升级** — 重建共享设计令牌与通用组件，新增 App Shell、Theme Provider、侧边导航，并同步调整 Electron 加载背景，为 Web / Desktop 的统一体验打底。
- 🔐 **登录与系统设置流程重做** — 重构 Login、Settings 与 Auth 管理流程，补上显式的认证 setup-state 处理，并让 Web 端与运行时认证配置 API 行为对齐。
- 🧪 **前端回归与冒烟覆盖补强** — 新增并扩展登录、首页、聊天、移动端 Shell、设置页、回测入口等关键路径的组件测试与 Playwright smoke coverage。

### 变更

- 🧭 **页面接入新 Shell 布局契约** — Home、Chat、Settings、Backtest 已统一接入新的页面容器、抽屉和滚动约定，降低 UI 迁移期间的页面行为不一致。
- 💾 **设置页状态同步更稳** — 优化草稿保留、直接保存同步与冲突处理，减少模块级保存后前后端配置状态不一致的问题。
- 🎭 **登录页视觉基线回归** — 登录页恢复到既有 `006` 分支的视觉基线，同时保留新的认证状态逻辑和统一表单交互模型。
- 🏛️ **AI 协作治理资产加固** — 收敛并加强 `AGENTS.md`、`CLAUDE.md`、Copilot 指令和校验脚本的一致性约束，降低治理资产长期漂移风险。

### Added

- **Web UI foundation refresh** — rebuilt shared design tokens and common primitives, introduced the app shell, theme provider, sidebar navigation, and Electron loading background alignment for the upgraded desktop/web experience
- **Settings and auth workflow overhaul** — rebuilt the Login, Settings, and Auth management flows, added explicit auth setup-state handling, and aligned the Web UI with the runtime auth configuration APIs
- **UI regression coverage and smoke checks** — expanded targeted frontend tests and added Playwright smoke coverage for login, home, chat, mobile shell, settings, and backtest entry flows

### Changed

- **Shell-driven page integration** — aligned Home, Chat, Settings, and Backtest with the new shell layout contract so routing, drawer behavior, and page-level scrolling are consistent during the UI migration
- **Settings state consistency** — refined draft preservation, direct-save synchronization, and conflict handling so module-level saves no longer leave the page out of sync with backend config state
- **Login visual baseline** — restored the login page visual treatment to the established `006` branch baseline while keeping the newer auth-state logic and unified form interaction model

### 修复

- ⏰ **定时启动立即执行兼容旧配置**（Issue #726）— `SCHEDULE_RUN_IMMEDIATELY` 未设置时会回退读取 `RUN_IMMEDIATELY`，修复升级后旧 `.env` 在定时模式下的兼容性问题；同时澄清 `.env.example` / README 中两个配置项的适用范围，并注明 Outlook / Exchange 强制 OAuth2 暂不支持。
- 🧵 **运行期 `MAX_WORKERS` 配置生效与可解释性增强**（#633）— 修复异步分析队列未按 `MAX_WORKERS` 同步的问题；新增任务队列并发 in-place 同步机制（空闲即时生效、繁忙延后），并在设置保存反馈与运行日志中明确输出 `profile/max/effective`，减少“参数未生效”误解。
- 🔐 **退出登录立即失效现有会话** — `POST /api/v1/auth/logout` 现在会轮换 session secret，避免旧 cookie 在退出后仍可继续访问受保护接口；同浏览器标签页和并发页面会被同步登出。认证开启时，该接口也不再属于匿名白名单，未登录请求会返回 `401`，避免匿名请求触发全局 session 失效。
- 🧮 **Tushare 板块/筹码调用限流与跨日缓存修复** — 新增的 `trade_cal`、行业板块排行、筹码分布链路统一接入 `_check_rate_limit()`；交易日历缓存改为按自然日刷新，避免服务跨天运行后继续沿用旧交易日判断取数日期。
- 💼 **持仓超售拦截与错误流水恢复**（#718）— `POST /api/v1/portfolio/trades` 现在会在写入前校验可卖数量，超售返回 `409 portfolio_oversell`；持仓页新增交易 / 资金流水 / 公司行为删除能力，删除后会同步失效仓位缓存与未来快照，便于从错误流水中直接恢复。
- 📧 **邮件中文发件人名编码**（#708）— 邮件通知现在会对包含中文的 `EMAIL_SENDER_NAME` 自动做 RFC 2047 编码，并在异常路径补充 SMTP 连接清理，修复 GitHub Actions / QQ SMTP 下 `'ascii' codec can't encode characters` 导致的发送失败。
- 🐛 **港股 Agent 实时行情去重与快速路由** — 统一 `HK01810` / `1810.HK` / `01810` 等港股代码归一规则；港股实时行情改为直接走单次 `akshare_hk` 路径，避免按 A 股 source priority 重复触发同一失败接口；Agent 运行期对显式 `retriable=false` 的工具失败增加短路缓存，减少同轮分析中的重复失败调用。
- 📰 **新闻时效硬过滤与策略分窗**（#697）— 新增 `NEWS_STRATEGY_PROFILE`（`ultra_short/short/medium/long`）并与 `NEWS_MAX_AGE_DAYS` 统一计算有效窗口；搜索结果在返回后执行发布时间硬过滤（时间未知剔除、超窗剔除、未来仅容忍 1 天），并在历史 fallback 链路追加相同约束，避免旧闻再次进入“最新动态/风险警报”。

### 文档

- ☁️ **新增云服务器 Web 界面部署与访问教程**（Fixes #686）— 补充从云端部署到外部访问的落地说明，降低远程自托管门槛。
- 🌍 **补齐英文文档索引与协作文档** — 新增英文文档索引、贡献指南、Bot 命令文档，并补充中英双语 issue / PR 模板，方便中英文协作与外部贡献者理解项目入口。
- 🏷️ **本地化 README 补充 Trendshift badge** — 在多语言 README 中同步补上新版能力入口标识，减少中英文说明面不一致。

## [3.7.0] - 2026-03-15

### 新功能

- 💼 **持仓管理 P0 全功能上线**（#677，对应 Issue #627）
  - **核心账本与快照闭环**：新增账户、交易、现金流水、企业行为、持仓缓存、每日快照等核心数据模型与 API 端点；支持 FIFO / AVG 双成本法回放；同日事件顺序固定为 `现金 → 企业行为 → 交易`；持仓快照写入采用原子事务。
  - **券商 CSV 导入**：支持华泰 / 中信 / 招商首批适配，含列名别名兼容；两阶段接口（解析预览 + 确认提交）；`trade_uid` 优先、key-field hash 兜底的幂等去重；前导零股票代码完整保留。
  - **组合风险报告**：集中度风险（Top Positions + A 股板块口径）、历史回撤监控（支持回填缺失快照）、止损接近预警；多币种统一换算 CNY 口径；汲取失败时回退最近成功汇率并标记 stale。
  - **Web 持仓页**（`/portfolio`）：组合总览、持仓明细、集中度饼图、风险摘要、全组合 / 单账户切换；手工录入交易 / 资金流水 / 企业行为；内嵌账户创建入口；CSV 解析 + 提交闭环与券商选择器。
  - **Agent 持仓工具**：新增 `get_portfolio_snapshot` 数据工具，默认紧凑摘要，可选持仓明细与风险数据。
  - **事件查询 API**：新增 `GET /portfolio/trades`、`GET /portfolio/cash-ledger`、`GET /portfolio/corporate-actions`，支持日期过滤与分页。
  - **可扩展 Parser Registry**：应用级共享注册，支持运行时注册新券商；新增 `GET /portfolio/imports/csv/brokers` 发现接口。

- 🎨 **前端设计系统与原子组件库**（#662）
  - 引入渐进式双主题架构（HSL 变量化设计令牌），清理历史 Legacy CSS；重构 Button / Card / Badge / Collapsible / Input / Select 等 20+ 核心组件；新增 `clsx` + `tailwind-merge` 类名合并工具；提升历史记录、LLM 配置等页面可读性。

- ⚡ **分析 API 异步契约与启动优化**（#656）
  - 规范 `POST /api/v1/analysis/analyze` 异步请求的返回契约；优化服务启动辅助逻辑；修复前端报告类型联合定义与后端响应对齐问题。

### 修复

- 🔔 **Discord 环境变量向后兼容**（#659）：运行时新增 `DISCORD_CHANNEL_ID` → `DISCORD_MAIN_CHANNEL_ID` 的 fallback 读取；历史配置用户无需修改即可恢复 Discord Bot 通知；全部相关文档与 `.env.example` 对齐。
- 🔧 **GitHub Actions Node 24 升级**（#665）：将所有 GitHub 官方 actions 升级至 Node 24 兼容版本，消除 CI 日志中的 Node.js 20 deprecation warning（影响 2026-06-02 强制升级窗口）。
- 📅 **持仓页默认日期本地化**：手工录入表单默认日期改用本地时间（`getFullYear/Month/Date`），修复 UTC-N 时区用户在当天晚间出现日期偏移的问题。
- 🔁 **CSV 导入去重逻辑加固**：dedup hash 纳入行序号作为区分因子，确保同字段合法分笔成交不被误折叠；同时在 `trade_uid` 存在时也持久化 hash，防止混合来源重复写入。

### 变更

- `POST /api/v1/portfolio/trades` 在同账户内 `trade_uid` 冲突时返回 `409`。
- 持仓风险响应新增 `sector_concentration` 字段（增量扩展），原有 `concentration` 字段保持不变。
- 分析 API `analyze` 接口异步行为契约文档化；前端报告类型联合更新。

### 测试

- 新增持仓核心服务测试（FIFO / AVG 部分卖出、同日事件顺序、重复 `trade_uid` 返回 409、快照 API 契约）。
- 新增 CSV 导入幂等性、合法分笔成交不误去重、去重边界、风险阈值边界、汇率降级行为测试。
- 新增 Agent `get_portfolio_snapshot` 工具调用测试。
- 新增分析 API 异步契约回归测试。

## [3.6.0] - 2026-03-14

### Added
- 📊 **Web UI Design System** — implemented dual-theme architecture and terminal-inspired atomic UI components
- 📊 **UI Components Refactoring** — integrated `clsx` and `tailwind-merge` for robust class composition across Web UI

- 🗑️ **History batch deletion** — Web UI now supports multi-selection and batch deletion of analysis history; added `POST /api/v1/history/batch-delete` endpoint and `ConfirmDialog` component.
- 🔐 **Auth settings API** — new `POST /api/v1/auth/settings` endpoint to enable or disable Web authentication at runtime and set the initial admin password when needed
- openclaw Skill 集成指南 — 新增 [New-docs/integrations/openclaw-skill-integration.md](../New-docs/integrations/openclaw-skill-integration.md)（原 `docs/openclaw-skill-integration.md`），说明如何通过 openclaw Skill 调用 DSA API
- ⚙️ **LLM channel protocol/test UX** — `.env` and Web settings now share the same channel shape (`LLM_CHANNELS` + `LLM_<NAME>_PROTOCOL/BASE_URL/API_KEY/MODELS/ENABLED`); settings page adds per-channel connection testing, primary/fallback/vision model selection, and protocol-aware model prefixing
- 🤖 **Agent architecture Phase 0+1** — shared protocols (`AgentContext`, `AgentOpinion`, `StageResult`), extracted `run_agent_loop()` runner, `AGENT_ARCH` switch (`single`/`multi`), config registry entries
- 🔍 **Bot NL routing** — two-layer natural-language routing: cheap regex pre-filter (stock codes + finance keywords) → lightweight LLM intent parsing; controlled by `AGENT_NL_ROUTING=true`; supports multi-stock and strategy extraction
- 💬 **`/ask` multi-stock analysis** — comma or `vs` separated codes (max 5), parallel thread execution with 150s timeout (preserves partial results), Markdown comparison summary table at top
- 📋 **`/history` command** — per-user session isolation via `{platform}_{user_id}:{scope}` format (colon delimiter prevents prefix collision); lists both `/chat` and `/ask` sessions; view detail or clear
- 📊 **`/strategies` command** — lists available strategy YAML files grouped by category (趋势/形态/反转/框架) with ✅/⬜ activation status
- 🔧 **Backtest summary tools** — `get_strategy_backtest_summary` and `get_stock_backtest_summary` registered as read-only Agent tools
- ⚙️ **Agent auto-detection** — `is_agent_available()` auto-detects from `LITELLM_MODEL`; explicit `AGENT_MODE=true/false` takes full precedence
- 🏗️ **Multi-Agent orchestrator (Phase 2)** — `AgentOrchestrator` with 4 modes (`quick`/`standard`/`full`/`strategy`); drop-in replacement for `AgentExecutor` via `AGENT_ARCH=multi`; `BaseAgent` ABC with tool subset filtering, cached data injection, and structured `AgentOpinion` output
- 🧩 **Specialised agents (Phase 2-4)** — `TechnicalAgent` (8 tools, trend/MA/MACD/volume/pattern analysis), `IntelAgent` (news & sentiment, risk flag propagation), `DecisionAgent` (synthesis into Decision Dashboard JSON), `RiskAgent` (7 risk categories, two-level severity with soft/hard override)
- 📈 **Strategy system (Phase 3)** — `StrategyAgent` (per-strategy evaluation from YAML skills), `StrategyRouter` (rule-based regime detection → strategy selection), `StrategyAggregator` (weighted consensus with backtest performance factor)
- 🔬 **Deep Research agent (Phase 5)** — `ResearchAgent` with 3-phase approach (decompose → research sub-questions → synthesise report); token budget tracking; new `/research` bot command with aliases (`/深研`, `/deepsearch`)
- 🧠 **Memory & calibration (Phase 6)** — `AgentMemory` with prediction accuracy tracking, confidence calibration (activates after minimum sample threshold), strategy auto-weighting based on historical win rate
- 📊 **Portfolio Agent (Phase 7)** — `PortfolioAgent` for multi-stock portfolio analysis (position sizing, sector concentration, correlation risk, cross-market linkage, rebalance suggestions)
- 🔔 **Event-driven alerts (Phase 7)** — `EventMonitor` with `PriceAlert`, `VolumeAlert`, `SentimentAlert` rules; async checking, callback notifications, serializable persistence
- ⚙️ **New config entries** — `AGENT_ORCHESTRATOR_MODE`, `AGENT_RISK_OVERRIDE`, `AGENT_DEEP_RESEARCH_BUDGET`, `AGENT_MEMORY_ENABLED`, `AGENT_STRATEGY_AUTOWEIGHT`, `AGENT_STRATEGY_ROUTING` — all registered in `config.py` + `config_registry.py` (WebUI-configurable)

### Changed
- 🔐 **Auth password state semantics** — stored password existence is now tracked independently from auth enablement; when auth is disabled, `/api/v1/auth/status` returns `passwordSet=false` while preserving the saved password for future re-enable
- 🔐 **Auth settings re-enable hardening** — re-enabling auth with a stored password now requires `currentPassword`, and failed session creation rolls back the auth toggle to avoid lockout
- ♻️ **AgentExecutor refactored** — `_run_loop` delegates to shared `runner.run_agent_loop()`; removed duplicated serialization/parsing/thinking-label code
- ♻️ **Unified agent switch** — Bot, API, and Pipeline all use `config.is_agent_available()` instead of divergent `config.agent_mode` checks
- 📖 **README.md** — expanded Bot commands section (ask/chat/strategies/history), added NL routing note, updated agent mode description
- 📖 **.env.example** — added `AGENT_ARCH` and `AGENT_NL_ROUTING` configuration documentation
- 🔌 **Analysis API async contract** — `POST /api/v1/analysis/analyze` now documents distinct async `202` payloads for single-stock vs batch requests, and `report_type=full` is treated consistently with the existing full-report behavior

### Fixed
- 🐛 **Analysis API blank-code guardrails** — `POST /api/v1/analysis/analyze` now drops whitespace-only entries before batch enqueue and returns `400` when no valid stock code remains
- 🐛 **Bare `/api` SPA fallback** — unknown API paths now return JSON `404` consistently for both `/api/...` and the exact `/api` path
- 🎮 **Discord channel env compatibility** — runtime now accepts legacy `DISCORD_CHANNEL_ID` as a fallback for `DISCORD_MAIN_CHANNEL_ID`, and the docs/examples now use the same variable name as the actual workflow/config implementation
- 🐛 **Session secret rotation on Windows** — use atomic replace so auth toggles invalidate existing sessions even when `.session_secret` already exists
- 🐛 **Auth toggle atomicity** — persist `ADMIN_AUTH_ENABLED` before rotating session secret; on rotation failure, roll back to the previous auth state
- 🔧 **LLM runtime selection guardrails** — YAML 模式下渠道编辑器不再覆盖 `LITELLM_MODEL` / fallback / Vision；系统配置校验补上全部渠道禁用后的运行时来源检查，并修复 `vertexai/...` 这类协议别名模型被重复加前缀的问题
- 🐛 **Multi-stock `/ask` follow-up regressions** — portfolio overlay now shares the same timeout budget as the per-stock phase and is skipped on timeout instead of blocking the bot reply; `/history` now stores the readable per-stock summary instead of raw dashboard JSON; condensed multi-stock output now renders numeric `sniper_points` values
- 🐛 **Decision dashboard enum compatibility** — multi-agent `DecisionAgent` now keeps `decision_type` within the legacy `buy|hold|sell` contract and normalizes stray `strong_*` outputs before risk override, pipeline conversion, and downstream统计/通知汇总
- 🛟 **Multi-Agent partial-result fallback** — `IntelAgent` now caches parsed intel for downstream reuse, shared JSON parsing tolerates lightly malformed model output, and the orchestrator preserves/synthesizes a minimal dashboard on timeout or mid-pipeline parse failure instead of always collapsing to `50/观望/未知`
- 🐛 **Shared LiteLLM routing restored** — bot NL intent parsing and `ResearchAgent` planning/synthesis now reuse the same LiteLLM adapter / Router / fallback / `api_base` injection path as the main Agent flow, so `LLM_CHANNELS` / `LITELLM_CONFIG` / OpenAI-compatible deployments behave consistently
- 🐛 **Bot chat session backward compatibility** — `/chat` now keeps using the legacy `{platform}_{user_id}` session id when old history already exists, and `/history` can still list / view / clear those pre-migration sessions alongside the new `{platform}_{user_id}:chat` format
- 🐛 **EventMonitor unsupported rule rejection** — config validation/runtime loading now reject or skip alert types the monitor cannot actually evaluate yet, so schedule mode no longer silently accepts permanent no-op rules
- 🐛 **P0 基本面聚合稳定性修复** (#614) — 修复 `get_stock_info` 板块语义回归（新增 `belong_boards` 并保留 `boards` 兼容别名）、引入基本面上下文精简返回以控制 token、为基本面缓存增加最大条目淘汰，并补齐 ETF 总体状态聚合与 NaN 板块字段过滤，保证 fail-open 与最小入侵。
- 🔧 **GitHub Actions 搜索引擎环境变量补充** — 工作流新增 `MINIMAX_API_KEYS`、`BRAVE_API_KEYS`、`SEARXNG_BASE_URLS` 环境变量映射，使 GitHub Actions 用户可配置 MiniMax、Brave、SearXNG 搜索服务（此前 v3.5.0 已添加 provider 实现但缺少工作流配置）
- 🤖 **Multi-Agent runtime consistency** — `AGENT_MAX_STEPS` now propagates to each orchestrated sub-agent; added cooperative `AGENT_ORCHESTRATOR_TIMEOUT_S` budget to stop overlong pipelines before they cascade further
- 🔌 **Multi-Agent feature wiring** — `AGENT_RISK_OVERRIDE` now actively downgrades final dashboards on hard risk findings; `AGENT_MEMORY_ENABLED` now injects recent analysis memory + confidence calibration into specialised agents; multi-stock `/ask` now runs `PortfolioAgent` to add portfolio-level allocation and concentration guidance
- 🔔 **EventMonitor runtime wiring** — schedule mode can now load alert rules from `AGENT_EVENT_ALERT_RULES_JSON`, poll them at `AGENT_EVENT_MONITOR_INTERVAL_MINUTES`, and send triggered alerts through the existing notification service
- 🛠️ **Follow-up stability fixes** — multi-stock `/ask` now falls back to usable text output when dashboard JSON parsing fails; EventMonitor skips semantically invalid rules instead of aborting schedule startup; background alert polling now runs independently of the main scheduled analysis loop
- 🧪 **Multi-Agent regression coverage** — added orchestrator execution tests for `run()`, `chat()`, critical-stage failure, graceful degradation, and timeout handling
- 🧹 **PortfolioAgent cleanup** — `post_process()` now reuses shared JSON parsing and removed stale unused imports
- 🚦 **Bot async dispatch** — `CommandDispatcher` now exposes `dispatch_async()`; NL intent parsing and default command execution are offloaded from the event loop, DingTalk stream awaits async handlers directly, and Feishu stream processing is moved off the SDK callback thread
- 🌐 **Async webhook handler** — new `handle_webhook_async()` function in `bot/handler.py` for use from async contexts (e.g. FastAPI); calls `dispatch_async()` directly without thread bridging
- 🧵 **Feishu stream ThreadPoolExecutor** — replaced unbounded per-message `Thread` spawning with a capped `ThreadPoolExecutor(max_workers=8)` to prevent thread explosion under message bursts
- 🔒 **EventMonitor safety** — `_check_volume()` now safely handles `get_daily_data` returning `None` (no tuple-unpacking crash); `on_trigger` callbacks support both sync and async callables via `asyncio.to_thread`/`await`
- 🧹 **ResearchAgent dedup** — `_filtered_registry()` now delegates to `BaseAgent._filtered_registry()` instead of duplicating the filtering logic
- 🧹 **Bot trailing whitespace cleanup** — removed W291/W293 whitespace issues across `bot/handler.py`, `bot/dispatcher.py`, `bot/commands/base.py`, `bot/platforms/feishu_stream.py`, `bot/platforms/dingtalk_stream.py`
- 🐛 **Dispatcher `_parse_intent_via_llm` safety** — replaced fragile `'raw' in dir()` with `'raw' in locals()` for undefined-variable guard in `JSONDecodeError` handler
- 🐛 **筹码结构 LLM 未填写时兜底补全** (#589) — DeepSeek 等模型未正确填写 `chip_structure` 时，自动用数据源已获取的筹码数据补全，保证各模型展示一致；普通分析与 Agent 模式均生效
- 🐛 **历史报告狙击点位显示原始文本** (#452) — 历史详情页现优先展示 `raw_result.dashboard.battle_plan.sniper_points` 中的原始字符串，避免 `analysis_history` 数值列把区间、说明文字或复杂点位压缩成单个数字；保留原有数值列作为回退
- 🐛 **Session prefix collision** — user ID `123` could see sessions of user `1234` via `startswith`; fixed with colon delimiter in session_id format
- 🐛 **NL pre-filter false positives** — `re.IGNORECASE` caused `[A-Z]{2,5}` to match common English words like "hello"; removed global flag, use inline `(?i:...)` only for English finance keywords
- 🐛 **Dotted ticker in strategy args** — `_get_strategy_args()` didn't recognize `BRK.B` as a stock code, leaving it in strategy text; now accepts `TICKER.CLASS` format
- ⏱️ **efinance 长调用挂起修复** (#660) — 为所有 efinance API 调用引入 `_ef_call_with_timeout()` 包装（默认 30 秒，可通过 `EFINANCE_CALL_TIMEOUT` 配置）；使用 `executor.shutdown(wait=False)` 确保超时后不再阻塞主线程，彻底消除 81 分钟挂起问题
- 🛡️ **类型安全内容完整性检查** (#660) — `check_content_integrity()` 现在将非字符串类型的 `operation_advice` / `analysis_summary` 视为缺失字段，避免下游 `get_emoji()` 因 `dict.strip()` 崩溃
- 📄 **报告保存与通知解耦** (#660) — `_save_local_report()` 不再依赖 `send_notification` 标志触发，`--no-notify` 模式下本地报告照常保存
- 🔄 **operation_advice 字典归一化** (#660) — Pipeline 和 BacktestEngine 现在将 LLM 返回的 `dict` 格式 `operation_advice` 通过 `decision_type`（不区分大小写）映射为标准字符串，防止因模型输出格式变化导致崩溃
- 🛡️ **runner.py usage None 防护** (#660) — `response.usage` 为 `None` 时不再抛出 `AttributeError`，回退为 0 token 计数
- 📋 **orchestrator 静默失败改为日志警告** (#660) — `IntelAgent` / `RiskAgent` 阶段失败现在记录 `WARNING` 而非静默跳过，便于诊断

### Notes
- ⚠️ **Multi-worker auth toggles** — runtime auth updates are process-local; multi-worker deployments must restart/roll workers to keep auth state consistent

## [3.5.0] - 2026-03-12

### Added
- 📊 **Web UI full report drawer** (Fixes #214) — history page adds "Full Report" button to display the complete Markdown analysis report in a side drawer; new `GET /api/v1/history/{record_id}/markdown` endpoint
- 📊 **LLM cost tracking** — all LLM calls (analysis, agent, market review) recorded in `llm_usage` table; new `GET /api/v1/usage/summary?period=today|month|all` endpoint returns aggregated token usage by call type and model
- 🔍 **SearXNG search provider** (Fixes #550) — quota-free self-hosted search fallback; priority: Bocha > Tavily > Brave > SerpAPI > MiniMax > SearXNG
- 🔍 **MiniMax web search provider** — `MiniMaxSearchProvider` with circuit breaker (3 failures → 300s cooldown) and dual time-filtering; configured via `MINIMAX_API_KEYS`
- 🤖 **Agent models discovery API** — `GET /api/v1/agent/models` returns available model deployments (primary/fallback/source/api_base) for Web UI model selector
- 🤖 **Agent chat export & send** (#495) — export conversation to .md file; send to configured notification channels; new `POST /api/v1/agent/chat/send`
- 🤖 **Agent background execution** (#495) — analysis continues when switching pages; badge notification on completion; auto-cancel in-progress stream on session switch
- 📝 **Report Engine P0** — Pydantic schema validation for LLM JSON; Jinja2 templates (markdown/wechat/brief) with legacy fallback; content integrity checks with retry; brief mode (`REPORT_TYPE=brief`); history signal comparison
- 📦 **Smart import** — multi-source import from image/CSV/Excel/clipboard; Vision LLM extracts code+name+confidence; name→code resolver (local map + pinyin + AkShare); confidence-tiered confirmation
- ⚙️ **GitHub Actions LiteLLM config** — workflow supports `LITELLM_CONFIG`/`LITELLM_CONFIG_YAML` for flexible AI provider configuration
- ⚙️ **Config engine refactor & system API** (#602) — unified config registry, validation and API exposure
- 📖 **LLM configuration guide** — new `docs/LLM_CONFIG_GUIDE.md` covering 3-tier config, quick start, Vision/Agent/troubleshooting

### Fixed
- 🐛 **analyze_trend always reports No historical data** (#600) — now fetches from DB/DataFetcher instead of broken `get_analysis_context`
- 🐛 **Chip structure fallback when LLM omits it** (#589) — auto-fills from data source chip data for consistent display across models
- 🐛 **History sniper points show raw text** (#452) — prioritizes original strings over compressed numeric values
- 🐛 **GitHub Actions ENABLE_CHIP_DISTRIBUTION configurable** (#617) — no longer hardcoded, supports vars/secrets override
- 🐛 **`.env` save preserves comments and blank lines** — Web settings no longer destroys `.env` formatting
- 🐛 **Agent model discovery fixes** — legacy mode includes LiteLLM-native providers; source detection aligned with runtime; fallback deployments no longer expanded per-key
- 🐛 **Stooq US stock previous close semantics** — no longer misuses open price as previous close
- 🐛 **Stock name prefetch regression** — prioritizes local `STOCK_NAME_MAP` before remote queries
- 🐛 **AkShare limit-up/down calculation** (#555) — fixed market analysis statistics
- 🐛 **AkShare Tencent source field index & ETF quote mapping** (#579)
- 🐛 **Pytdx stock name cache pagination** (#573) — prevents cache overflow
- 🐛 **PushPlus oversized report chunking** (#489) — auto-segments long content
- 🐛 **Agent chat cancel & switch** (#495) — cancel no longer misreports as failure; fast switch no longer overwrites stream state
- 🐛 **MiniMax search status in `/status` command** (#587)
- 🐛 **config_registry duplicate BOCHA_API_KEYS** — removed duplicate dict entry that silently overwrote config

### Changed
- 🔎 **Fetcher failure observability** — logs record start/success/failure with elapsed time, failover transitions; Efinance/Akshare include upstream endpoint and classified failure categories
- ♻️ **Data source resilience & cleanup** (#602) — fallback chain optimization
- ♻️ **Image extract API response extension** — new `items` field (code/name/confidence); `codes` preserved for backward compatibility
- ♻️ **Import parse error messages** — specific failure reasons for Excel/CSV; improved logging with file type and size

### Docs
- 📖 LLM config guide refactored for clarity (#583)
- 📖 `image-extract-prompt.md` with full prompt documentation
- 📖 AkShare fallback cache TTL documentation
## [3.4.10] - 2026-03-07

### Fixed
- 🐛 **EfinanceFetcher ETF OHLCV data** (#541, #527) — switch `_fetch_etf_data` from `ef.fund.get_quote_history` (NAV-only, no OHLCV, no `beg`/`end` params) to `ef.stock.get_quote_history`; ETFs now return proper open/high/low/close/volume/amount instead of zeros; remove obsolete NAV column mappings from `_normalize_data`
- 🐛 **tiktoken 0.12.0 `Unknown encoding cl100k_base`** (#537) — pin `tiktoken>=0.8.0,<0.12.0` in requirements.txt to avoid plugin-registration regression introduced in 0.12.0
- 🐛 **Web UI API error classification** (#540) — frontend no longer treats every HTTP 400 as the same "server/network" failure; now distinguishes Agent disabled / missing params / model-tool incompatibility / upstream LLM errors / local connection failures
- 🐛 **北交所代码识别失败** (#491, #533) — 8/4/92 开头的 6 位代码现正确识别为北交所；Tushare/Akshare/Yfinance 等数据源支持 .BJ 或 bj 前缀；Baostock/Pytdx 对北交所代码显式切换数据源；避免误判上海 B 股 900xxx
- 🐛 **狙击点位解析错误** (#488, #532) — 理想买入/二次买入等字段在无「元」字时误提取括号内技术指标数字；现先截去第一个括号后内容再提取

### Added
- **Markdown-to-image for dashboard report** (#455, #535) — 个股日报汇总支持 markdown 转图片推送（Telegram、WeChat、Custom、Email），与大盘复盘行为一致
- **markdown-to-file engine** (#455) — `MD2IMG_ENGINE=markdown-to-file` 可选，对 emoji 支持更好，需 `npm i -g markdown-to-file`
- **PREFETCH_REALTIME_QUOTES** (#455) — 设为 `false` 可禁用实时行情预取，避免 efinance/akshare_em 全市场拉取
- **Stock name prefetch** (#455) — 分析前预取股票名称，减少报告中「股票xxxxx」占位符
- 📊 **分析报告模型标记** (#528, #534) — 在分析报告 meta、报告末尾、推送内容中展示 `model_used`（完整 LLM 模型名）；Agent 多轮调用时记录并展示每轮实际使用的模型（支持 fallback 切换）

### Changed
- **Enhanced markdown-to-image failure warning** (#455) — 转图失败时提示具体依赖（wkhtmltopdf 或 m2f）
- **WeChat-only image routing optimization** (#455) — 仅配置企业微信图片时，不再对完整报告做冗余转图，避免误导性失败日志
- **Stock name prefetch lightweight mode** (#455) — 名称预取阶段跳过 realtime quote 查询，减少额外网络开销

## [3.4.9] - 2026-03-06

### Added
- 🧠 **Structured config validation** — `ConfigIssue` dataclass and `validate_structured()` with severity-aware logging; `CONFIG_VALIDATE_MODE=strict` aborts startup on errors
- 🖼️ **Vision model config** — `VISION_MODEL` and `VISION_PROVIDER_PRIORITY` for image stock extraction; provider fallback (Gemini → Anthropic → OpenAI → DeepSeek) when primary fails
- 🚀 **CLI init wizard** — `python -m dsa init` 3-step interactive bootstrap (model → data source → notification), 9 provider presets, incremental merge by default
- 🔧 **Multi-channel LLM support** with visual channel editor (#494)

### Changed
- ♻️ **Vision extraction** — migrated from gemini-3 hardcode to `litellm.completion()` with configurable model and provider fallback; `OPENAI_VISION_MODEL` deprecated in favor of `VISION_MODEL`
- ♻️ **Market analyzer** — uses `Analyzer.generate_text()` for LLM calls; fixes bypass and Anthropic `AttributeError` when using non-Router path
- ♻️ **Config validation refinements** — test_env output format syncs with `validate_structured` (severity-aware ✓/✗/⚠/·); Vision key warning when `VISION_MODEL` set but no provider API key; market_analyzer test covers `generate_market_review` fallback when `generate_text` returns None
- ⚙️ **Auto-tag workflow defaults to NO tag** — only tags when commit message explicitly contains `#patch`, `#minor`, or `#major`
- ♻️ **Formatter and notification refactor** (#516)

### Fixed
- 🐛 **STOCK_LIST not refreshed on scheduled runs** — `.env` or WebUI changes to `STOCK_LIST` now hot-reload before each scheduled analysis (#529)
- 🐛 **WebUI fails to load with MIME type error** — SPA fallback route now resolves correct `Content-Type` for JS/CSS files (#520)
- 🐛 **AstrBot sender docstring misplaced** — `import time` placed before docstring in `_send_astrbot`, causing it to become dead code
- 🐛 **Telegram Markdown link escaping** — `_convert_to_telegram_markdown` escaped `[]()` characters, breaking all Markdown links in reports
- 🐛 **Duplicate `discord_bot_status` field** in Config dataclass — second declaration silently shadowed the first
- 🧹 **Unused imports** — removed `shutil`/`subprocess` from `main.py`
- 🔧 **Config validation and Vision key check** (#525)

### Docs
- 📝 Clarified GitHub Actions non-trading-day manual run controls (`TRADING_DAY_CHECK_ENABLED` + `force_run`) for Issue #461 / PR #466

## [3.4.8] - 2026-03-02

### Fixed
- 🐛 **Desktop exe crashes on startup with `FileNotFoundError`** — PyInstaller build was missing litellm's JSON data files (e.g. `model_prices_and_context_window_backup.json`). Added `--collect-data litellm` to both Windows and macOS build scripts so the files are correctly bundled in the executable.

### CI
- 🔧 Cache Electron binaries on macOS CI runners to prevent intermittent EOF download failures when fetching `electron-vX.Y.Z-darwin-*.zip` from GitHub CDN
- 🔧 Fix macOS DMG `hdiutil Resource busy` error during desktop packaging

### Docs
- 📝 Clarify non-trading-day manual run controls for GitHub Actions (`TRADING_DAY_CHECK_ENABLED` + `force_run`) (#474)

## [3.4.7] - 2026-02-28

### Added
- 🧠 **CN/US Market Strategy Blueprint System** (#395) — market review prompt injects region-specific strategy blueprints with position sizing and risk trigger recommendations

### Fixed
- 🐛 **`TRADING_DAY_CHECK_ENABLED` env var and `--force-run` for GitHub Actions** (#466)
- 🐛 **Agent pipeline preserved resolved stock names** (#464) — placeholder names no longer leak into reports
- 🐛 **Code cleanup** (#462, Fixes #422)
- 🐛 **WebUI auto-build on startup** (#460)
- 🐛 **ARCH_ARGS unbound variable** (#458)
- 🐛 **Time zone inconsistency & right panel flash** (#439)

### Docs
- 📝 Clarify potential ambiguities in code (#343)
- 📝 ENABLE_EASTMONEY_PATCH guidance for Issue #453 (#456)

## [3.4.0] - 2026-02-27

### Added
- 📡 **LiteLLM Direct Integration + Multi API Key Support** (#454, Fixes #421 #428)
  - Removed native SDKs (google-generativeai, google-genai, anthropic); unified through `litellm>=1.80.10`
  - New config: `LITELLM_MODEL`, `LITELLM_FALLBACK_MODELS`, `GEMINI_API_KEYS`, `ANTHROPIC_API_KEYS`, `OPENAI_API_KEYS`
  - Multi-key auto-builds LiteLLM Router (simple-shuffle) with 429 cooldown
  - **Breaking**: `.env` `GEMINI_MODEL` (no prefix) only for fallback; explicit config must include provider prefix

### Changed
- ♻️ **Notification Refactoring** (#435) — extracted 10 sender classes into `src/notification_sender/`

### Fixed
- 🐛 LLM NoneType crash, history API 422, sniper points extraction
- 🐛 Auto-build frontend on WebUI startup — `WEBUI_AUTO_BUILD` env var (default `true`)
- 🐛 Docker explicit project name (#448)
- 🐛 Bocha search SSL retry (#445, #446) — transient errors retry up to 3 times
- 🐛 Gemini google-genai SDK migration (Fixes #440, #444)
- 🐛 Mobile home page scrolling (Fixes #419, #433)
- 🐛 History list scroll reset (#431)
- 🐛 Settings save button false positive (fixes #417, #430)

## [3.3.22] - 2026-02-26

### Added
- 💬 **Chat History Persistence** (Fixes #400, #414) — `/chat` page survives refresh, sidebar session list
- 🎨 Project VI Assets — logo icon set, PSD, vector, banner (#425)
- 🚀 Desktop CI Auto-Release (#426) — Windows + macOS parallel builds

### Fixed
- 🐛 Agent Reasoning 400 & LiteLLM Proxy (fixes #409, #427)
- 🐛 Discord chunked sending (#413) — `DISCORD_MAX_WORDS` config
- 🐛 yfinance shared DataFrame (#412)
- 🐛 sniper_points parsing (#408)
- 🐛 Agent framework category missing (#406)
- 🐛 Date inconsistency & query id (fixes #322, #363)

## [3.3.12] - 2026-02-24

### Added
- 📈 **Intraday Realtime Technical Indicators** (Issue #234, #397) — MA calculated from realtime price, config: `ENABLE_REALTIME_TECHNICAL_INDICATORS`
- 🤖 **Agent Strategy Chat** (#367) — full ReAct pipeline, 11 YAML strategies, SSE streaming, multi-turn chat
- 📢 PushPlus Group Push — `PUSHPLUS_TOPIC` (#402)
- 📅 Trading Day Check (Issue #373, #375) — `TRADING_DAY_CHECK_ENABLED`, `--force-run`

### Fixed
- 🐛 DeepSeek reasoning mode (Issue #379, #386)
- 🐛 Agent news intel persistence (Fixes #396, #405)
- 🐛 Bare except clauses replaced with `except Exception` (#398)
- 🐛 UUID fallback for HTTP non-secure context (fixes #377, #381)
- 🐛 Docker DNS resolution (Fixes #372, #374)
- 🐛 Agent session/strategy bugs — multiple follow-up fixes for #367
- 🐛 yfinance parallel download data filtering

### Changed
- Market review strategy consistency — unified cn/us template
- Agent test assertions updated (`6 -> 11`)


## [3.2.11] - 2026-02-23

### 修复（#patch）
- 🐛 **StockTrendAnalyzer 从未执行** (Issue #357)
  - 根因：`get_analysis_context` 仅返回 2 天数据且无 `raw_data`，pipeline 中 `raw_data in context` 始终为 False
  - 修复：Step 3 直接调用 `get_data_range` 获取 90 日历天（约 60 交易日）历史数据用于趋势分析
  - 改善：趋势分析失败时用 `logger.warning(..., exc_info=True)` 记录完整 traceback

## [3.2.10] - 2026-02-22

### 新增
- ⚙️ 支持 `RUN_IMMEDIATELY` 配置项，设为 `true` 时定时任务触发后立即执行一次分析，无需等待首个定时点

### 修复
- 🐛 修复 Web UI 页面居中问题
- 🐛 修复 Settings 返回 500 错误

## [3.2.9] - 2026-02-22

### 修复
- 🐛 **ETF 分析仅关注指数走势**（Issue #274）
  - 美股/港股 ETF（如 VOO、QQQ）与 A 股 ETF 不再纳入基金公司层面风险（诉讼、声誉等）
  - 搜索维度：ETF/指数专用 risk_check、earnings、industry 查询，避免命中基金管理人新闻
  - AI 提示：指数型标的分析约束，`risk_alerts` 不得出现基金管理人公司经营风险

## [3.2.8] - 2026-02-21

### 修复
- 🐛 **BOT 与 WEB UI 股票代码大小写统一**（Issue #355）
  - BOT `/analyze` 与 WEB UI 触发分析的股票代码统一为大写（如 `aapl` → `AAPL`）
  - 新增 `canonical_stock_code()`，在 BOT、API、Config、CLI、task_queue 入口处规范化
  - 历史记录与任务去重逻辑可正确识别同一股票（大小写不再影响）

## [3.2.7] - 2026-02-20

### 新增
- 🔐 **Web 页面密码验证**（Issue #320, #349）
  - 支持 `ADMIN_AUTH_ENABLED=true` 启用 Web 登录保护
  - 首次访问在网页设置初始密码；支持「系统设置 > 修改密码」和 CLI `python -m src.auth reset_password` 重置

## [3.2.6] - 2026-02-20
### ⚠️ 破坏性变更（Breaking Changes）

- **历史记录 API 变更 (Issue #322)**
  - 路由变更：`GET /api/v1/history/{query_id}` → `GET /api/v1/history/{record_id}`
  - 参数变更：`query_id` (字符串) → `record_id` (整数)
  - 新闻接口变更：`GET /api/v1/history/{query_id}/news` → `GET /api/v1/history/{record_id}/news`
  - 原因：`query_id` 在批量分析时可能重复，无法唯一标识单条历史记录。改用数据库主键 `id` 确保唯一性
  - 影响范围：使用旧版历史详情 API 的所有客户端需同步更新

### 修复
- 修复美股（如 ADBE）技术指标矛盾：akshare 美股复权数据异常，统一美股历史数据源为 YFinance（Issue #311）
- 🐛 **历史记录查询和显示问题 (Issue #322)**
  - 修复历史记录列表查询中日期不一致问题：使用明天作为 endDate，确保包含今天全天的数据
  - 修复服务器 UI 报告选择问题：原因是多条记录共享同一 `query_id`，导致总是显示第一条。现改用 `analysis_history.id` 作为唯一标识
  - 历史详情、新闻接口及前端组件已全面适配 `record_id`
  - 新增后台轮询（每 30s）与页面可见性变更时静默刷新历史列表，确保 CLI 发起的分析完成后前端能及时同步，使用 `silent` 模式避免触发 loading 状态
- 🐛 **美股指数实时行情与日线数据** (Issue #273)
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 ^GSPC）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源
  - 消除重复的美股识别逻辑，统一使用 `is_us_stock_code()` 函数

### 优化
- 🎨 **首页输入栏与 Market Sentiment 布局对齐优化**
  - 股票代码输入框左缘与历史记录 glass-card 框左对齐
  - 分析按钮右缘与 Market Sentiment 外框右对齐
  - Market Sentiment 卡片向下拉伸填满格子，消除与 STRATEGY POINTS 之间的空隙
  - 窄屏时输入栏填满宽度，响应式对齐保持一致

## [3.2.5] - 2026-02-19

### 新增
- 🌍 **大盘复盘可选区域**（Issue #299）
  - 支持 `MARKET_REVIEW_REGION` 环境变量：`cn`（A股）、`us`（美股）、`both`（两者）
  - us 模式使用 SPX/纳斯达克/道指/VIX 等指数；both 模式可同时复盘 A 股与美股
  - 默认 `cn`，保持向后兼容

## [3.2.4] - 2026-02-18

### 修复
- 🐛 **统一美股数据源为 YFinance**（Issue #311）
  - akshare 美股复权数据异常，统一美股历史数据源为 YFinance
  - 修复 ADBE 等美股股票技术指标矛盾问题

## [3.2.3] - 2026-02-18

### 修复
- 🐛 **标普500实时数据缺失**（Issue #273）
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 `^GSPC`）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源

## [3.2.2] - 2026-02-16

### 新增
- 📊 **PE 指标支持**（Issue #296）
  - AI System Prompt 增加 PE 估值关注
- 📰 **新闻时效性筛查**（Issue #296）
  - `NEWS_MAX_AGE_DAYS`：新闻最大时效（天），默认 3，避免使用过时信息
- 📈 **强势趋势股乖离率放宽**（Issue #296）
  - `BIAS_THRESHOLD`：乖离率阈值（%），默认 5.0，可配置
  - 强势趋势股（多头排列且趋势强度 ≥70）自动放宽乖离率到 1.5 倍

## [3.2.1] - 2026-02-16

### 新增
- 🔧 **东财接口补丁可配置开关**
  - 支持 `EFINANCE_PATCH_ENABLED` 环境变量开关东财接口补丁（默认 `true`）
  - 补丁不可用时可降级关闭，避免影响主流程

## [3.2.0] - 2026-02-15

### 新增
- 🔒 **CI 门禁统一（P0）**
  - 新增 `scripts/ci_gate.sh` 作为后端门禁单一入口
  - 主 CI 改为 `backend-gate`、`docker-build`、`web-gate` 三段式
  - CI 触发改为所有 PR，避免 Required Checks 因路径过滤缺失而卡住合并
  - `web-gate` 支持前端路径变更按需触发
  - 新增 `network-smoke` 工作流承载非阻断网络场景回归
- 📦 **发布链路收敛（P0）**
  - `docker-publish` 调整为 tag 主触发，并增加发布前门禁校验
  - 手动发布增加 `release_tag` 输入与 semver/changelog 强校验
  - 发布前新增 Docker smoke（关键模块导入）
- 📝 **PR 模板升级（P0）**
  - 增加背景、范围、验证命令与结果、回滚方案、Issue 关联等必填项
- 🤖 **AI 审查覆盖增强（P0）**
  - `pr-review` 纳入 `.github/workflows/**` 范围
  - 新增 `AI_REVIEW_STRICT` 开关，可选将 AI 审查失败升级为阻断

## [3.1.13] - 2026-02-15

### 新增
- 📊 **仅分析结果摘要**（Issue #262）
  - 支持 `REPORT_SUMMARY_ONLY` 环境变量，设为 `true` 时只推送汇总，不含个股详情
  - 默认 `false`，多股时适合快速浏览

## [3.1.12] - 2026-02-15

### 新增
- 📧 **个股与大盘复盘合并推送**（Issue #190）
  - 支持 `MERGE_EMAIL_NOTIFICATION` 环境变量，设为 `true` 时将个股分析与大盘复盘合并为一次推送
  - 默认 `false`，减少邮件数量、降低被识别为垃圾邮件的风险

## [3.1.11] - 2026-02-15

### 新增
- 🤖 **Anthropic Claude API 支持**（Issue #257）
  - 支持 `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`、`ANTHROPIC_TEMPERATURE`、`ANTHROPIC_MAX_TOKENS`
  - AI 分析优先级：Gemini > Anthropic > OpenAI
- 📷 **从图片识别股票代码**（Issue #257）
  - 上传自选股截图，通过 Vision LLM 自动提取股票代码
  - API: `POST /api/v1/stocks/extract-from-image`；支持 JPEG/PNG/WebP/GIF，最大 5MB
  - 支持 `OPENAI_VISION_MODEL` 单独配置图片识别模型
- ⚙️ **通达信数据源手动配置**（Issue #257）
  - 支持 `PYTDX_HOST`、`PYTDX_PORT` 或 `PYTDX_SERVERS` 配置自建通达信服务器

## [3.1.10] - 2026-02-15

### 新增
- ⚙️ **立即运行配置**（Issue #332）
  - 支持 `RUN_IMMEDIATELY` 环境变量，`true` 时定时任务启动后立即执行一次
- 🐛 修复 Docker 构建问题

## [3.1.9] - 2026-02-14

### 新增
- 🔌 **东财接口补丁机制**
  - 新增 `patch/eastmoney_patch.py` 修复 efinance 上游接口变更
  - 不影响其他数据源的正常运行

## [3.1.8] - 2026-02-14

### 新增
- 🔐 **Webhook 证书校验开关**（Issue #265）
  - 支持 `WEBHOOK_VERIFY_SSL` 环境变量，可关闭 HTTPS 证书校验以支持自签名证书
  - 默认保持校验，关闭存在 MITM 风险，仅建议在可信内网使用

## [3.1.7] - 2026-02-14

### 修复
- 🐛 修复包导入错误（package import error）

## [3.1.6] - 2026-02-13

### 修复
- 🐛 修复 `news_intel` 中 `query_id` 不一致问题

## [3.1.5] - 2026-02-13

### 新增
- 📷 **Markdown 转图片通知**（Issue #289）
  - 支持 `MARKDOWN_TO_IMAGE_CHANNELS` 配置，对 Telegram、企业微信、自定义 Webhook（Discord）、邮件发送图片格式报告
  - 邮件为内联附件，增强对不支持 HTML 客户端的兼容性
  - 需安装 `wkhtmltopdf` 和 `imgkit`

## [3.1.4] - 2026-02-12

### 新增
- 📧 **股票分组发往不同邮箱**（Issue #268）
  - 支持 `STOCK_GROUP_N` + `EMAIL_GROUP_N` 配置，不同股票组报告发送到对应邮箱
  - 大盘复盘发往所有配置的邮箱

## [3.1.3] - 2026-02-12

### 修复
- 🐛 修复 Docker 内运行时通过页面修改配置报错 `[Errno 16] Device or resource busy` 的问题

## [3.1.2] - 2026-02-11

### 修复
- 🐛 修复 Docker 一致性问题，解决关键批次处理与通知 Bug

## [3.1.1] - 2026-02-11

### 变更
- ♻️ `API_HOST` → `WEBUI_HOST`：Docker Compose 配置项统一

## [3.1.0] - 2026-02-11

### 新增
- 📊 **ETF 支持增强与代码规范化**
  - 统一各数据源 ETF 代码处理逻辑
  - 新增 `canonical_stock_code()` 统一代码格式，确保数据源路由正确

## [3.0.5] - 2026-02-08

### 修复
- 🐛 修复信号 emoji 与建议不一致的问题（复合建议如"卖出/观望"未正确映射）
- 🐛 修复 `*ST` 股票名在微信/Dashboard 中 markdown 转义问题
- 🐛 修复 `idx.amount` 为 None 时大盘复盘 TypeError
- 🐛 修复分析 API 返回 `report=None` 及 ReportStrategy 类型不一致问题
- 🐛 修复 Tushare 返回类型错误（dict → UnifiedRealtimeQuote）及 API 端点指向

### 新增
- 📊 大盘复盘报告注入结构化数据（涨跌统计、指数表格、板块排名）
- 🔍 搜索结果 TTL 缓存（500 条上限，FIFO 淘汰）
- 🔧 Tushare Token 存在时自动注入实时行情优先级
- 📰 新闻摘要截断长度 50→200 字

### 优化
- ⚡ 补充行情字段请求限制为最多 1 次，减少无效请求

## [3.0.4] - 2026-02-07

### 新增
- 📈 **回测引擎** (PR #269)
  - 新增基于历史分析记录的回测系统，支持收益率、胜率、最大回撤等指标评估
  - WebUI 集成回测结果展示

## [3.0.3] - 2026-02-07

### 修复
- 🐛 修复狙击点位数据解析错误问题 (PR #271)

## [3.0.2] - 2026-02-06

### 新增
- ✉️ 可配置邮件发送者名称 (PR #272)
- 🌐 外国股票支持英文关键词搜索

## [3.0.1] - 2026-02-06

### 修复
- 🐛 修复 ETF 实时行情获取、市场数据回退、企业微信消息分块问题
- 🔧 CI 流程简化

## [3.0.0] - 2026-02-06

### 移除
- 🗑️ **移除旧版 WebUI**
  - 删除基于 `http.server.ThreadingHTTPServer` 的旧版 WebUI（`web/` 包）
  - 旧版 WebUI 的功能已完全被 FastAPI（`api/`）+ React 前端替代
  - `--webui` / `--webui-only` 命令行参数标记为弃用，自动重定向到 `--serve` / `--serve-only`
  - `WEBUI_ENABLED` / `WEBUI_HOST` / `WEBUI_PORT` 环境变量保持兼容，自动转发到 FastAPI 服务
  - `webui.py` 保留为兼容入口，启动时直接调用 FastAPI 后端
  - Docker Compose 中移除 `webui` 服务定义，统一使用 `server` 服务

### 变更
- ♻️ **服务层重构**
  - 将 `web/services.py` 中的异步任务服务迁移至 `src/services/task_service.py`
  - Bot 分析命令（`bot/commands/analyze.py`）改为使用 `src.services.task_service`
  - Docker 环境变量 `WEBUI_HOST`/`WEBUI_PORT` 更名为 `API_HOST`/`API_PORT`（旧名仍兼容）

## [2.3.0] - 2026-02-01

### 新增
- 🇺🇸 **增强美股支持** (Issue #153)
  - 实现基于 Akshare 的美股历史数据获取 (`ak.stock_us_daily()`)
  - 实现基于 Yfinance 的美股实时行情获取（优先策略）
  - 增加对不支持数据源（Tushare/Baostock/Pytdx/Efinance）的美股代码过滤和快速降级

### 修复
- 🐛 修复 AMD 等美股代码被误识别为 A 股的问题 (Issue #153)

## [2.2.5] - 2026-02-01

### 新增
- 🤖 **AstrBot 消息推送** (PR #217)
  - 新增 AstrBot 通知渠道，支持推送到 QQ 和微信
  - 支持 HMAC SHA256 签名验证，确保通信安全
  - 通过 `ASTRBOT_URL` 和 `ASTRBOT_TOKEN` 配置

## [2.2.4] - 2026-02-01

### 新增
- ⚙️ **可配置数据源优先级** (PR #215)
  - 支持通过环境变量（如 `YFINANCE_PRIORITY=0`）动态调整数据源优先级
  - 无需修改代码即可优先使用特定数据源（如 Yahoo Finance）

## [2.2.3] - 2026-01-31

### 修复
- 📦 更新 requirements.txt，增加 `lxml_html_clean` 依赖以解决兼容性问题

## [2.2.2] - 2026-01-31

### 修复
- 🐛 修复代理配置区分大小写问题 (fixes #211)

## [2.2.1] - 2026-01-31

### 修复
- 🐛 **YFinance 兼容性修复** (PR #210, fixes #209)
  - 修复新版 yfinance 返回 MultiIndex 列名导致的数据解析错误

## [2.2.0] - 2026-01-31

### 新增
- 🔄 **多源回退策略增强**
  - 实现了更健壮的数据获取回退机制 (feat: multi-source fallback strategy)
  - 优化了数据源故障时的自动切换逻辑

### 修复
- 🐛 修复 analyzer 运行后无法通过改 .env 文件的 stock_list 内容调整跟踪的股票

## [2.1.14] - 2026-01-31

### 文档
- 📝 更新 README 和优化 auto-tag 规则

## [2.1.13] - 2026-01-31

### 修复
- 🐛 **Tushare 优先级与实时行情** (Fixed #185)
  - 修复 Tushare 数据源优先级设置问题
  - 修复 Tushare 实时行情获取功能

## [2.1.12] - 2026-01-30

### 修复
- 🌐 修复代理配置在某些情况下的区分大小写问题
- 🌐 修复本地环境禁用代理的逻辑

## [2.1.11] - 2026-01-30

### 优化
- 🚀 **飞书消息流优化** (PR #192)
  - 优化飞书 Stream 模式的消息类型处理
  - 修改 Stream 消息模式默认为关闭，防止配置错误运行时报错

## [2.1.10] - 2026-01-30

### 合并
- 📦 合并 PR #154 贡献

## [2.1.9] - 2026-01-30

### 新增
- 💬 **微信文本消息支持** (PR #137)
  - 新增微信推送的纯文本消息类型支持
  - 添加 `WECHAT_MSG_TYPE` 配置项

## [2.1.8] - 2026-01-30

### 修复
- 🐛 修正日志中 API 提供商显示错误 (PR #197)

## [2.1.7] - 2026-01-30

### 修复
- 🌐 禁用本地环境的代理设置，避免网络连接问题

## [2.1.6] - 2026-01-29

### 新增
- 📡 **Pytdx 数据源 (Priority 2)**
  - 新增通达信数据源，免费无需注册
  - 多服务器自动切换
  - 支持实时行情和历史数据
- 🏷️ **多源股票名称解析**
  - DataFetcherManager 新增 `get_stock_name()` 方法
  - 新增 `batch_get_stock_names()` 批量查询
  - 自动在多数据源间回退
  - Tushare 和 Baostock 新增股票名称/列表方法
- 🔍 **增强搜索回退**
  - 新增 `search_stock_price_fallback()` 用于数据源全部失败时
  - 新增搜索维度：市场分析、行业分析
  - 最大搜索次数从 3 增加到 5
  - 改进搜索结果格式（每维度 4 条结果）

### 改进
- 更新搜索查询模板以提高相关性
- 增强 `format_intel_report()` 输出结构

## [2.1.5] - 2026-01-29

### 新增
- 📡 新增 Pytdx 数据源和多源股票名称解析功能

## [2.1.4] - 2026-01-29

### 文档
- 📝 更新赞助商信息

## [2.1.3] - 2026-01-28

### 文档
- 📝 重构 README 布局
- 🌐 新增繁体中文翻译 (README_CHT.md)

### 修复
- 🐛 修复 WebUI 无法输入美股代码问题
  - 输入框逻辑改成所有字母都转换成大写
  - 支持 `.` 的输入（如 `BRK.B`）

## [2.1.2] - 2026-01-27

### 修复
- 🐛 修复个股分析推送失败和报告路径问题 (fixes #166)
- 🐛 修改 CR 错误，确保微信消息最大字节配置生效

## [2.1.1] - 2026-01-26

### 新增
- 🔧 添加 GitHub Actions auto-tag 工作流
- 📡 添加 yfinance 兜底数据源及数据缺失警告

### 修复
- 🐳 修复 docker-compose 路径和文档命令
- 🐳 Dockerfile 补充 copy src 文件夹 (fixes #145)

## [2.1.0] - 2026-01-25

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 📈 **MACD 和 RSI 技术指标**
  - MACD：趋势确认、金叉死叉信号（零轴上金叉⭐、金叉✅、死叉❌）
  - RSI：超买超卖判断（超卖⭐、强势✅、超买⚠️）
  - 指标信号纳入综合评分系统
- 🎮 **Discord 推送支持** (PR #124, #125, #144)
  - 支持 Discord Webhook 和 Bot API 两种方式
  - 通过 `DISCORD_WEBHOOK_URL` 或 `DISCORD_BOT_TOKEN` + `DISCORD_MAIN_CHANNEL_ID` 配置
- 🤖 **机器人命令交互**
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
- 🌡️ **AI 温度参数可配置** (PR #142)
  - 支持自定义 AI 模型温度参数
- 🐳 **Zeabur 部署支持**
  - 添加 Zeabur 镜像部署工作流
  - 支持 commit hash 和 latest 双标签

### 重构
- 🏗️ **项目结构优化**
  - 核心代码移至 `src/` 目录，根目录更清爽
  - 文档移至 `docs/` 目录
  - Docker 配置移至 `docker/` 目录
  - 修复所有 import 路径，保持向后兼容
- 🔄 **数据源架构升级**
  - 新增数据源熔断机制，单数据源连续失败自动切换
  - 实时行情缓存优化，批量预取减少 API 调用
  - 网络代理智能分流，国内接口自动直连
- 🤖 Discord 机器人重构为平台适配器架构

### 修复
- 🌐 **网络稳定性增强**
  - 自动检测代理配置，对国内行情接口强制直连
  - 修复 EfinanceFetcher 偶发的 `ProtocolError`
  - 增加对底层网络错误的捕获和重试机制
- 📧 **邮件渲染优化**
  - 修复邮件中表格不渲染问题 (#134)
  - 优化邮件排版，更紧凑美观
- 📢 **企业微信推送修复**
  - 修复大盘复盘推送不完整问题
  - 增强消息分割逻辑，支持更多标题格式
  - 增加分批发送间隔，避免限流丢失
- 👷 **CI/CD 修复**
  - 修复 GitHub Actions 中路径引用的错误

## [2.0.0] - 2026-01-24

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 🤖 **机器人命令交互** (PR #113)
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
  - 支持选择精简报告或完整报告
- 🎮 **Discord 推送支持** (PR #124)
  - 支持 Discord Webhook 推送
  - 添加 Discord 环境变量到工作流

### 修复
- 🐳 修复 WebUI 在 Docker 中绑定 0.0.0.0 (fixed #118)
- 🔔 修复飞书长连接通知问题
- 🐛 修复 `analysis_delay` 未定义错误
- 🔧 启动时 config.py 检测通知渠道，修复已配置自定义渠道情况下仍然提示未配置问题

### 改进
- 🔧 优化 Tushare 优先级判断逻辑，提升封装性
- 🔧 修复 Tushare 优先级提升后仍排在 Efinance 之后的问题
- ⚙️ 配置 TUSHARE_TOKEN 时自动提升 Tushare 数据源优先级
- ⚙️ 实现 4 个用户反馈 issue (#112, #128, #38, #119)

## [1.6.0] - 2026-01-19

### 新增
- 🖥️ WebUI 管理界面及 API 支持（PR #72）
  - 全新 Web 架构：分层设计（Server/Router/Handler/Service）
  - 核心 API：支持 `/analysis` (触发分析), `/tasks` (查询进度), `/health` (健康检查)
  - 交互界面：支持页面直接输入代码并触发分析，实时展示进度
  - 运行模式：新增 `--webui-only` 模式，仅启动 Web 服务
  - 解决了 [#70](https://github.com/ZhuLinsen/daily_stock_analysis/issues/70) 的核心需求（提供触发分析的接口）
- ⚙️ GitHub Actions 配置灵活性增强（[#79](https://github.com/ZhuLinsen/daily_stock_analysis/issues/79)）
  - 支持从 Repository Variables 读取非敏感配置（如 STOCK_LIST, GEMINI_MODEL）
  - 保持对 Secrets 的向下兼容

### 修复
- 🐛 修复企业微信/飞书报告截断问题（[#73](https://github.com/ZhuLinsen/daily_stock_analysis/issues/73)）
  - 移除 notification.py 中不必要的长度硬截断逻辑
  - 依赖底层自动分片机制处理长消息
- 🐛 修复 GitHub Workflow 环境变量缺失（[#80](https://github.com/ZhuLinsen/daily_stock_analysis/issues/80)）
  - 修复 `CUSTOM_WEBHOOK_BEARER_TOKEN` 未正确传递到 Runner 的问题

## [1.5.0] - 2026-01-17

### 新增
- 📲 单股推送模式（[#55](https://github.com/ZhuLinsen/daily_stock_analysis/issues/55)）
  - 每分析完一只股票立即推送，不用等全部分析完
  - 命令行参数：`--single-notify`
  - 环境变量：`SINGLE_STOCK_NOTIFY=true`
- 🔐 自定义 Webhook Bearer Token 认证（[#51](https://github.com/ZhuLinsen/daily_stock_analysis/issues/51)）
  - 支持需要 Token 认证的 Webhook 端点
  - 环境变量：`CUSTOM_WEBHOOK_BEARER_TOKEN`

## [1.4.0] - 2026-01-17

### 新增
- 📱 Pushover 推送支持（PR #26）
  - 支持 iOS/Android 跨平台推送
  - 通过 `PUSHOVER_USER_KEY` 和 `PUSHOVER_API_TOKEN` 配置
- 🔍 博查搜索 API 集成（PR #27）
  - 中文搜索优化，支持 AI 摘要
  - 通过 `BOCHA_API_KEYS` 配置
- 📊 Efinance 数据源支持（PR #59）
  - 新增 efinance 作为数据源选项
- 🇭🇰 港股支持（PR #17）
  - 支持 5 位代码或 HK 前缀（如 `hk00700`、`hk1810`）

### 修复
- 🔧 飞书 Markdown 渲染优化（PR #34）
  - 使用交互卡片和格式化器修复渲染问题
- ♻️ 股票列表热重载（PR #42 修复）
  - 分析前自动重载 `STOCK_LIST` 配置
- 🐛 钉钉 Webhook 20KB 限制处理
  - 长消息自动分块发送，避免被截断
- 🔄 AkShare API 重试机制增强
  - 添加失败缓存，避免重复请求失败接口

### 改进
- 📝 README 精简优化
  - 高级配置移至 `docs/full-guide.md`


## [1.3.0] - 2026-01-12

### 新增
- 🔗 自定义 Webhook 支持
  - 支持任意 POST JSON 的 Webhook 端点
  - 自动识别钉钉、Discord、Slack、Bark 等常见服务格式
  - 支持配置多个 Webhook（逗号分隔）
  - 通过 `CUSTOM_WEBHOOK_URLS` 环境变量配置

### 修复
- 📝 企业微信长消息分批发送
  - 解决自选股过多时内容超过 4096 字符限制导致推送失败的问题
  - 智能按股票分析块分割，每批添加分页标记（如 1/3, 2/3）
  - 批次间隔 1 秒，避免触发频率限制

## [1.2.0] - 2026-01-11

### 新增
- 📢 多渠道推送支持
  - 企业微信 Webhook
  - 飞书 Webhook（新增）
  - 邮件 SMTP（新增）
  - 自动识别渠道类型，配置更简单

### 改进
- 统一使用 `NOTIFICATION_URL` 配置，兼容旧的 `WECHAT_WEBHOOK_URL`
- 邮件支持 Markdown 转 HTML 渲染

## [1.1.0] - 2026-01-11

### 新增
- 🤖 OpenAI 兼容 API 支持
  - 支持 DeepSeek、通义千问、Moonshot、智谱 GLM 等
  - Gemini 和 OpenAI 格式二选一
  - 自动降级重试机制

## [1.0.0] - 2026-01-10

### 新增
- 🎯 AI 决策仪表盘分析
  - 一句话核心结论
  - 精确买入/止损/目标点位
  - 检查清单（✅⚠️❌）
  - 分持仓建议（空仓者 vs 持仓者）
- 📊 大盘复盘功能
  - 主要指数行情
  - 涨跌统计
  - 板块涨跌榜
  - AI 生成复盘报告
- 🔍 多数据源支持
  - AkShare（主数据源，免费）
  - Tushare Pro
  - Baostock
  - YFinance
- 📰 新闻搜索服务
  - Tavily API
  - SerpAPI
- 💬 企业微信机器人推送
- ⏰ 定时任务调度
- 🐳 Docker 部署支持
- 🚀 GitHub Actions 零成本部署

### 技术特性
- Gemini AI 模型（gemini-3-flash-preview）
- 429 限流自动重试 + 模型切换
- 请求间延时防封禁
- 多 API Key 负载均衡
- SQLite 本地数据存储

---

[Unreleased]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.11.0...HEAD
[3.11.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.10...v3.5.0
[3.4.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.9...v3.4.10
[3.4.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.8...v3.4.9
[3.4.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.7...v3.4.8
[3.4.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.0...v3.4.7
[3.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.22...v3.4.0
[3.3.22]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.12...v3.3.22
[3.3.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.11...v3.3.12
[3.2.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.10...v3.2.11
[2.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.5...v2.3.0
[2.2.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.4...v2.2.5
[2.2.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.3...v2.2.4
[2.2.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.14...v2.2.0
[2.1.14]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.13...v2.1.14
[2.1.13]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.12...v2.1.13
[2.1.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.11...v2.1.12
[2.1.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.10...v2.1.11
[2.1.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.9...v2.1.10
[2.1.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.8...v2.1.9
[2.1.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.7...v2.1.8
[2.1.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.6...v2.1.7
[2.1.6]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.5...v2.1.6
[2.1.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.4...v2.1.5
[2.1.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.2...v2.1.3
[2.1.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.6.0...v2.0.0
[1.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v1.0.0

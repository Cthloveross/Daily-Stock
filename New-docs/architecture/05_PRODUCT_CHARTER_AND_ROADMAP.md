# 05 · Options Trading OS 产品章程与路线图

> 状态：当前产品规划与交付顺序的真源（Working Source of Truth）
>
> 建立日期：2026-07-20
>
> 适用范围：Moomoo 数据接入、期权交易复盘、行情分析、个人交易系统、Web 交互
>
> 说明：本文替代旧路线图中的实施优先级；`01_PROJECT_VISION_v4.md` 保留为历史背景，其中关于 LEAP、交易频率和仓位比例的结论降级为待验证假设。

---

## 1. 一句话目标

本项目是一个面向单一交易者、以 **Moomoo 成交事实** 为起点的期权交易复盘与行情决策系统：它把订单和成交重建为完整的持仓/策略生命周期，把每次进出场与当时的 K 线、市场环境、期权状态和个人规则对齐，帮助用户识别可重复的优势、真实亏损来源与执行偏差，并把结论沉淀为可执行、可验证的个人 Playbook。

它不是自动下单机器人，也不是替用户给出确定性买卖答案的“AI 水晶球”。

## 2. 产品要闭合的循环

系统的价值不在于多展示几个指标，而在于闭合下面的学习循环：

```text
Moomoo 原始数据
  -> 可核对的订单 / 成交 / 费用
  -> 持仓与多腿策略生命周期
  -> 同步当时的市场与期权上下文
  -> 计划 vs 实际、盈利 vs 亏损的证据化复盘
  -> 形成或修订 Playbook 规则
  -> 下一次交易前检查
  -> 新数据再次验证规则
```

每一个首页指标都必须能下钻到具体交易；每一条 AI 结论都必须能回到数据、图表或用户记录；每一条规则都必须能在未来交易中被评估。

## 3. 核心产品原则

### 3.1 数据可信优先于 AI

- 原始文件和原始成交记录不可变，解析、配对、分组都保留版本和来源。
- 导入后必须能与 Moomoo 的成交数量、费用和已实现盈亏对账。
- 数据缺失时明确显示“未知”，不让 AI 补猜。
- 同一份数据重复导入必须幂等，不得制造重复交易。

### 3.2 分析单位是策略生命周期，不是扁平的一行成交

当前 `journal_trades` 适合单腿 FIFO 往返，但不足以表达：

- 分批建仓、减仓和加仓；
- 同一观点下的多腿价差；
- roll 到新执行价或新到期日；
- 到期、行权、指派和提前平仓；
- 同一策略跨多个合约的总费用、最大风险与最终结果。

新的核心对象应是 `PositionEpisode` 和 `StrategyEpisode`。单腿交易只是它们的简单情况。

### 3.3 相关性不是因果

- “某指标出现时更赚钱”只能先记为候选规律。
- 所有分组统计显示样本数；样本过少时不给强结论。
- 优先做同类交易的匹配比较，例如相同 DTE、方向、时段和 Regime 下比较不同进场方式。
- 显示置信区间、稳定性和样本外结果，而不只显示胜率。

### 3.4 复盘优先，盘前辅助随后

先把历史交易还原正确、把单笔复盘工作台做顺，再建设实时盘前/盘中决策台。否则实时界面只会放大未经验证的指标和规则。

### 3.5 券商连接永久只读

- `LIVE` 只表示读取真实账户历史事实，不表示开放交易执行；所有环境永久只读。
- 不实现自动下单、改单、撤单或交易解锁。
- Moomoo/OpenD 离线时，系统应清楚降级，不能无限高频重连和刷日志。

### 3.6 UI 围绕任务，而不是围绕模块

用户想完成的是“今天是否可交易”“这笔为什么赚/亏”“哪种做法可重复”“下一笔要遵守什么”，而不是分别参观 Journal、Regime、Backtest 等代码模块。

## 4. 当前基线与缺口（更新至 2026-07-23）

| 能力 | 当前事实 | 结论 |
|---|---|---|
| Moomoo SDK / OpenD | 只读探测已采用唯一/显式稳定账户、7 日分块、稳定 ID、费用分批、完整对账和进程级总超时；正确 CSV 与 API 稳定窗口已全量对账；去标识化 JSON 已完成严格 parser、plan/confirm、append-only persistence 与 canonical set；当前期权持仓已具备独立双采样、预览/确认、30 分钟 currentness 和 append-only 快照 | live writer 继续暂停；过期、账户变化或 publication 被取代的确认记录只作为历史证据，当前持仓仍只证明本次采集区间，可作未来锚点，但尚未通过成交连续性 fence 成为 Episode opening boundary |
| 常驻进程 | 三个旧 LaunchAgent 已卸载；SDK 连接快速失败、资源释放和日志轮转已有确定性测试 | 仍需 24 小时在线/离线观察后再决定只恢复哪些服务；旧 Journal sync 不恢复 |
| 日志 | 项目与 Moomoo SDK 历史轮转日志已归档压缩，约释放 5.3 GB；SDK 控制台关闭、文件日志降到 WARNING 并保留 3 份 | 仍需 24 小时测量实际增长量与重复 incident 抑制 |
| Journal 数据 | 当前 CSV 基线的 build 1 与 canonical set 1 的冻结 replay 都得到 5,974 个执行事件（574 aggregate + 5,400 fill）、1,441 个 PositionEpisode，USD 151,750.75 费用守恒；OpenAPI batch 3 与 canonical set 1 已正式追加；当前持仓快照存放在独立账本 | canonical 对比构建需显式 confirm 且不自动替换 build 1；历史期初边界仍为 `assumed_flat_unverified`，当前快照不会倒推旧窗口，headline 严格为空 |
| Web | `/regime`、`/watchlist`、`/stocks/:ticker`、`/journal` 与 `/journal/review/:episodeId` 等路由可见；仓位列表可按大赚/大亏/高费用/长持有精选案例，并按待复盘/进行中/已完成进入 Review Queue | 单合约事实核对、案例定位、底层行情与可继续的复盘队列已形成第一条连续路径；完整导航和策略级工作流仍未完成 |
| Journal UI | 默认进入“仓位复盘”，读取 CSV immutable build；canonical Episode 另走显式 preview/confirm 与 `build_id` 对比；单合约工作台提供 1m/2m/5m/15m/30m/1h/日线、常规/扩展时段、ET 横轴、EMA8/13、现金流 marker、fill/order-time-proxy 区分、行情 provenance、本地证据复盘、六字段草稿、用户标签/错误类型、append-only ReviewAnnotation v1 与按需模型增强 | 证据事实层不依赖 LLM 且不能被模型替换；未提交草稿仍只在浏览器，显式保存才追加绑定 immutable build + PositionEpisode 的用户自述版本，AI 不自动写入。尚无经用户确认的多腿 StrategyEpisode、期权历史行情/Greeks、MFE/MAE 和完整 Strategy Review |
| Moomoo UI | badge、证据覆盖率、局部对账状态、OpenAPI 写入计划/确认、canonical 构建计划和 PositionEpisode 下钻可见；旧 `/journal/sync-live` 固定拒绝 | 仍缺失败恢复和完整 Strategy Review |
| 设置 API | `phase0` Schema 错误已修复，真实浏览器可加载全部配置 | Phase0 仍缺用户友好名称与交易任务分层 |
| 自动交易 | 生产目录未发现解锁/下单/改单/撤单引用，并有 AST 边界测试持续扫描 | 永久保持这一安全边界 |

因此下一步不是继续加新指标，而是先得到一个稳定、可信、能完成完整任务的最小闭环。

### 4.1 真实只读样本基线（去标识化）

2026-07-20 已通过 Moomoo OpenAPI 对真实账户做只读 smoke，不写 Journal 数据库：

- 最近 7 天返回 362 笔历史订单，其中 352 笔全部成交；对应 718 条逐笔成交，订单与成交数量、代码和均价核对无差异；
- 截至上一交易日的最近 30 天聚合到 1,058 笔全部成交订单、2,107 条逐笔成交和 1,058 条费用记录，其中 2,097 条成交属于期权，说明后续模型与 UI 必须以期权为主而不是股票 Journal 的附属功能；
- 该稳定窗口的订单、成交代码、方向、数量、成交加权均价和费用覆盖全部对账通过；盘中新成交曾短暂缺 1 条费用，探测器正确返回 `analysis_ready=false`，不会把查询成功误报成可分析；
- 90 天单次请求在本机超时，而约 7 天窗口稳定返回，因此正式读取采用分块、重试、稳定 ID 去重与断点状态；
- 这些数字只证明读取和基础对账链路可用，不等于已经完成策略配对、净 P&L 或入场原因分析。

所有对外日志与文档只保留去标识化汇总，不记录账户号、卡号或完整成交明细。

### 4.1.1 正确 CSV 与双来源验收（2026-07-21）

当前 accepted CSV 是完整历史基线：其来源共 6,094 条数据行，重建为 3,542 个父订单和 5,400 条真实逐笔成交；其中 574 个较早的 Filled 订单只有数量、均价和费用汇总。稳定窗口内 CSV 与只读 API 的 1,089 个订单、2,107 条 fill、状态、数量、VWAP、费用总额和九项费用分项全部一致。CSV 与 OpenAPI 已以 append-only 方式写入可信证据账本，并生成 3,542 orders / 5,400 fills、0 blockers 的 canonical set；同源旧 parser 批次只保留审计，legacy Journal 不参与当前分析。冻结 canonical replay 产生 574 个 aggregate order events + 5,400 个 fill events、1,441 个 PositionEpisode（1,437 closed / 4 open），并验证 USD 151,750.75 费用守恒。Aggregate order 的 submitted amount 不作为执行现金流，避免把券商委托/汇总语义混进 P&L。写入前后均生成 SQLite 备份并通过完整性检查，旧 Journal 表与备份逐行一致。

### 4.2 真实浏览器审计结论

2026-07-20 用真实本机后端与浏览器走查 `/regime`、`/journal`、交易抽屉和
`/settings`，得到以下直接结论：

- 配置端点和只读 Moomoo badge 已可用；字体 404/解码错误已经修复，复验为 0 个 console error；
- Journal 的 8 个平级 tab 没有形成“找到交易 → 核对事实 → 看图 → 标注 → 保存规则”的连续任务流；
- 当时 Trades 仍按 FIFO 碎片行展示，详情抽屉也不能承担复盘工作台；后续已新增单合约 K 线与成交证据工作台，但 legacy Trades 仍只作隔离存档；
- 旧 Journal 的 `Reality Test`、DTE 图和时间显示没有 provenance，且与新的只读事实集不是同一数据口径；时区与 DTE 映射必须在继续美化前修正；
- 胜率与 0DTE 占比原先使用固定“盈亏平衡/危险”阈值，这些误导文案已改为描述性提示；后续必须结合赔率、费用、P&L 分布和风险判断。

Data Health、“仓位复盘”、canonical 对比构建、案例精选、七档 K 线/evidence 联动、本地证据复盘、按需模型增强、ReviewAnnotation v1 和 Review Queue 已经落地。
前端下一步不是继续给 legacy 页面加卡片，而是利用案例精选和该工作台抽查复杂生命周期，
再补用户确认的 StrategyEpisode 分组、TradePlan/RuleEvaluation 和期权/市场快照。

## 5. 目标用户与核心任务

### 5.1 主用户

当前只服务一个用户：项目所有者本人，美股期权为主。A 股、港股和通用股票分析能力保留，但不主导产品信息架构。

### 5.2 用户每天真正要完成的任务

1. 确认数据和 Moomoo 连接是否健康、最新成交是否已同步。
2. 判断当天市场环境、事件风险和允许使用的策略范围。
3. 查看关注标的与候选机会，并记录交易前假设、失效条件和风险预算。
4. 交易后快速找到对应策略生命周期，核对成交和费用。
5. 在同步图表上查看进出场、MFE/MAE、IV/Greeks 和市场背景。
6. 标注计划遵守度、错误类型、情绪和新的证据。
7. 对比同类盈利/亏损交易，决定保留、修改或废弃哪条规则。
8. 周/月度检查这些规则是否真的改善了结果。

## 6. 目标信息模型

| 对象 | 职责 | 当前映射 / 缺口 |
|---|---|---|
| `ImportBatch` | 文件/API 同步批次、哈希、来源、解析版本、状态 | 可信账本已实现 CSV 与 OpenAPI 批次、parser 版本、完整度和对账结果 |
| `BrokerOrder` | 委托层事实 | `BrokerOrderObservation` 已实现；CSV 使用带碰撞检测的派生身份，API 稳定 `order_id` 已通过显式 link 入账 |
| `BrokerFill` | 每一笔实际成交、时间、价格、数量、费用 | `BrokerFillObservation` 已实现；不会为 aggregate-only 老订单合成 fill，双来源重叠由 canonical provenance 去重 |
| `Instrument` | 股票或 OCC 合约的标准身份 | 现有字段分散在订单和交易中 |
| `PositionEpisode` | 单一合约/标的从开到关的经济持仓过程 | signed-position lifecycle builder、CSV/canonical append-only 构建、列表/详情 API 和 evidence allocation 已实现；它不是 FIFO，opening boundary 尚未核验 |
| `StrategyEpisode` | 同一交易观点下的多腿、调整、roll、行权/指派 | 当前仅 1:1 `single_position_unclassified` 容器；不推断 spread/roll，候选与人工确认流程待实现 |
| `MarketSnapshot` | 进场/调整/出场时的底层行情、Regime、VIX、板块与事件 | 单合约工作台已按 ET 将 evidence 对齐到七档底层 K 线，并区分常规/扩展时段；证据复盘和模型增强可读取同期 SPY 与邻近 Regime 并返回 provenance，但这些仍是动态读取，不是冻结的不可变快照 |
| `OptionSnapshot` | bid/ask、成交量、OI、IV、Greeks、DTE、曲面位置 | 期权链能力存在，但未和每笔交易冻结关联 |
| `TradePlan` | 入场前假设、触发条件、失效条件、目标、最大风险 | 缺失 |
| `RuleEvaluation` | 某笔交易对每条 Playbook 规则的遵守结果 | 缺失 |
| `ReviewAnnotation` | 用户显式保存的事后自述、标签与错误类型 | v1 已实现：绑定 immutable build + PositionEpisode，按 revision append-only 追加并保留 content hash/前序链接；没有 annotation 派生为待复盘，最新版本决定进行中/已完成。AI、本地证据复盘与模型增强不会自动写入，且 annotation 不修改 broker evidence、canonical、Episode 经济字段或 P&L |
| `PlaybookRule` | 可版本化的个人策略与纪律规则 | 当前 Framework 是一段 localStorage 文本，不可审计 |
| `Experiment` | 一条候选规律的样本、假设、开始/停止条件和样本外结果 | 缺失 |

实现时优先追加新表和兼容层，保留原始数据，不对现有用户数据做破坏性重写。

## 7. 复盘指标体系

### 7.1 结果层

- 净 P&L、R multiple、expectancy、Profit Factor；
- 最大回撤、连续亏损、盈利集中度；
- MFE、MAE、利润捕获率、退出效率；
- 持有时间、资金占用、费用与滑点占比；
- 单腿和整个 StrategyEpisode 两种口径。

### 7.2 期权上下文

- DTE、moneyness、delta/gamma/theta/vega；
- 入场 IV、IV percentile/rank、IV 变化；
- bid-ask spread、成交相对 mid 的滑点、流动性；
- 财报/FOMC/CPI 等事件距离；
- 多腿的净 Greeks、最大损失、盈亏平衡和调整轨迹。

### 7.3 行情与进场上下文

- 底层标的多周期结构、趋势和波动；
- SPY/QQQ、行业相对强弱、VIX 与 Regime；
- 时段、开盘后分钟数、gap、VWAP/EMA/关键价位距离；
- breakout、retest、pullback 等标签，但标签定义必须版本化；
- 进场前后固定窗口的价格路径，而不是只看最终盈亏。

### 7.4 执行与行为

- 是否有交易前计划、是否满足触发、是否越过失效条件；
- 追价、过早止盈、扛亏、加仓摊平、报复交易、过度交易；
- 计划风险与实际风险偏差；
- 情绪只作为用户输入，不由模型猜测。

### 7.5 统计护栏

- 每个结论显示样本数、时间范围、缺失率和数据源；
- 小样本默认显示“观察中”，不显示“已验证优势”；
- 同时看均值、中位数、分布和尾部风险；
- 指标和规则版本变化后分段评估，避免把不同定义混在一起；
- AI 只生成解释和待验证假设，最终统计由确定性代码计算。

## 8. 前端信息架构

### 8.1 Today / Cockpit

首屏只回答：系统健康吗、数据新吗、今天是什么环境、我现在有什么待办。

- 顶部固定 Data Health：OpenD、最后同步时间、未对账数量、行情 freshness；
- Market Context：Regime、VIX、事件、关键指数/板块；
- Review Queue：待复盘、缺标签、缺计划、需要确认的多腿分组；
- Active Plans：尚未失效的交易假设和风险预算；
- 不展示无法下钻或没有行动含义的装饰性 KPI。

### 8.2 Trades

- 默认按 `StrategyEpisode` 展示，而不是按 FIFO 行；
- 支持时间、标的、策略、方向、DTE、时段、Regime、规则遵守、盈亏和数据完整度筛选；
- 任一聚合结果都能一键查看构成交易；
- 支持保存筛选视图和并排比较两个 cohort。

### 8.3 Review Workspace

这是整个产品最重要的页面。

第一条可用纵向切片已落地：仓位列表可按大赚、大亏、高费用和长持有定位案例，并用 Review Queue 区分待复盘、进行中和已完成；`/journal/review/:episodeId` 保留 `build_id` 与列表筛选上下文，支持 1m/2m/5m/15m/30m/1h/日线并联动不可变 execution evidence。分钟线默认常规时段，可显式切换含盘前盘后，EMA8/13 根据当前口径重算，横轴与十字光标固定显示 ET。2 分钟线明确声明由带时区 1 分钟线派生；marker 以分配现金流区分买卖，真实 fill 和订单时间代理采用不同视觉语义；首次 5 分钟行情覆盖不足时明确降级日线。用户可在六字段本机草稿中分开记录进场前计划和事后反思，并添加用户标签/错误类型；显式保存会为当前 immutable build + PositionEpisode 追加一版 ReviewAnnotation，非重复编辑不会覆盖旧版，相同最新内容幂等。去标识化本地证据复盘和专用模型链增强继续独立运行，模型失败不影响证据审阅，也不会自动写入 annotation。当前仍是 PositionEpisode 单合约视图，不含策略分组、期权历史行情、MFE/MAE、TradePlan/RuleEvaluation 或 Playbook。

```text
┌────────────────────────────────────────────────────────────┐
│ Strategy header · P&L · risk · completeness · prev/next   │
├────────────────────────────────┬───────────────────────────┤
│ 底层 K 线 + 入/出/调整标记       │ 计划 vs 实际              │
│ 多周期切换、画线、事件、Regime   │ Playbook checklist        │
│ 可切期权价格 / IV / Greeks       │ 错误、情绪、证据与 AI 假设 │
├────────────────────────────────┴───────────────────────────┤
│ order/fill/leg/roll 时间线 · MFE/MAE · 同类交易对照       │
└────────────────────────────────────────────────────────────┘
```

关键交互：

- 图表、成交时间线和明细表保持同步选中；
- 从任一 entry/exit 标记回到原始 fill；
- 多周期切换必须展示真实 period/source/coverage，派生周期必须声明来源；
- AI 只使用去标识化事实，条件 P&L 不升级，单笔案例不证明 edge；
- 一键对比相同 setup 的盈利和亏损交易；
- AI 回答必须附引用：交易字段、图表区间、规则版本或行情快照；
- 显式保存用户自述后更新 Review Queue；规则统计要等 RuleEvaluation 落地，不能由 annotation 或 AI 文本自动生成。

### 8.4 Analytics / Edge Explorer

- Overview：expectancy、PF、drawdown、集中度、Reality Test；
- Entry：时段、形态、触发方式、多周期结构；
- Options：DTE、delta、IV、流动性、策略结构；
- Execution：MFE/MAE、滑点、利润捕获率、规则遵守度；
- Compare：赢家/输家或两个自定义 cohort 的同维度比较；
- 每张图都可下钻，不单独制造“AI 洞察卡片”。

### 8.5 Playbook

- Setup 定义、适用市场环境、入场触发、失效条件、退出规则和风险上限；
- 每条规则有版本、启用日期、适用范围和证据链接；
- 规则状态：`draft -> testing -> validated / rejected / retired`；
- 展示遵守与违反该规则后的结果差异，但不把相关性包装为因果。

### 8.6 Data & Settings

- Moomoo 连接、CSV/API 同步、范围预览、dry-run、导入历史和对账；
- 数据源覆盖率、freshness、失败原因与降级状态；
- 用户看到的是聚合后的 incident，不是不断滚动的原始日志；
- 高级设置与普通交易工作流分离。

## 9. 日志与可观测性标准

当前 3 GB 日志是 P0 故障，不是正常现象。后续必须满足：

- 所有常驻进程单实例运行，有明确 PID/owner/heartbeat；
- 重连采用指数退避、抖动和相同错误抑制；
- stdout/stderr 与业务事件分离，结构化记录 component、event、severity、attempt；
- 文件日志可轮转、可配置保留期和总量上限；
- 默认不记录账户号、token、完整原始成交或用户 Framework；
- Finnhub 等第三方 HTTP 失败摘要必须移除完整 query string 与 `token/api_key/key` 值，只保留异常类型、状态和不含查询参数的路径等诊断信息；本次修复只保护后续新日志，现存旧日志可能仍含历史值且不会被程序自动改写；
- UI 只展示“发生了什么、影响什么、如何恢复、最后一次时间”；
- 验收时测量空闲、OpenD 离线和数据源故障三种情况下的日志增长。

建议默认验收阈值：同一离线错误最多每分钟出现一次摘要；正常空闲 24 小时日志增长低于 10 MB。具体轮转参数通过配置提供，不写死环境差异。

## 10. 分阶段路线图

时间是相对估算，只有上一阶段的退出条件通过后才进入下一阶段。

### Phase 0：运行时止血与可信基线（1–3 天）

交付：

- 识别并安全停掉重复/失控的旧进程，保留必要故障证据后再归档大日志；
- 一个受控启动入口，同时管理 Web、同步和 breakout 服务；
- OpenD 未启动时快速降级，不出现高频重连；
- 修复 `phase0` 配置 Schema 500；
- Web、Journal、Moomoo status、config 关键 API smoke；
- 加入日志轮转、错误去重和连接退避的确定性测试。

退出条件：

- 连续运行 24 小时无重复进程、无失控日志；
- OpenD 在线/离线两种状态均有明确且可恢复的 UI；
- `main.py --serve-only` 的关键 API 和前端构建通过；
- 保持 Moomoo 永久只读，无任何解锁、下单、改单或撤单路径。

### Phase 1：Moomoo 数据可信与策略生命周期（1–2 周）

**Phase 1.1–1.5 当前进度**：CSV loss-aware parser、双来源稳定窗口对账、append-only ImportBatch/Order/Fill、CSV preview/import/data-health API 已完成。signed-position lifecycle builder 已把当前 CSV 基线的 5,974 个执行事件构建为 1,441 个 PositionEpisode，并用 1:1 unclassified StrategyEpisode 保留后续分组空间；费用 USD 151,750.75 全量守恒。Phase 1.3B 已落地严格 OpenAPI parser、DB-aware plan/confirm、显式 identity links、fill-set attestations 与版本化 canonical persistence；当前正式本地账本已保存 1,089 order、2,107 fill、1,058 fee observations，canonical 输出 3,542 orders / 5,400 fills、0 blockers。Phase 1.4 新增冻结 canonical set replay、显式 preview/confirm、append-only source binding 与 `build_id` 对比读取。Phase 1.5 的单合约复盘切片现已支持大赚/大亏/高费用/长持有案例精选，保留构建/筛选返回上下文，以分配现金流确定买卖 marker，并提供 1m/2m/5m/15m/30m/1h/日线切换；分钟线默认使用 09:30–16:00 ET，可显式切换 04:00–20:00 ET，EMA8/13 基于当前时段重算，图表横轴与十字光标固定显示纽约时间。2 分钟线声明由带时区 1 分钟线派生，首次 5 分钟覆盖不完整时才明确降级日线。复盘助手先返回不依赖 LLM 的证据复盘，再按需调用复盘专用模型链；模型失败时仍返回完整本地复盘且不写入账本。六字段浏览器草稿会严格分开进场前计划和事后记录，只把非空字段作为未经验证的用户自述附在本次请求。ReviewAnnotation v1 现允许用户显式把六字段、标签和错误类型保存为绑定 immutable build + PositionEpisode 的 append-only revision；相同最新内容幂等，AI 不自动写入，最新状态驱动待复盘/进行中/已完成队列。分钟级收益代理只使用成交前最近已完成且仍新鲜的 bar，Regime 缺少生成 as-of 时保持开仓可见性未知。annotation 与复盘助手都不重新计算账户收益，不把 aggregate proxy、未完成 bar 的最终 close、日线位置或单笔案例描述成当时已知信号、精确入场或已验证 edge。当前期权持仓已能以独立双采样快照保存，但它只证明采集区间内的当前状态，尚未通过成交连续性 fence 成为任何历史构建的 opening boundary；因此构建仍为 `assumed_flat_unverified`，所有 Episode 排除在 headline 之外。未来边界接线、真实多腿意图确认和 20 个复杂生命周期人工抽查仍未完成，因此 Phase 1 尚未退出。

交付：

- 用用户真实样本覆盖 CSV 与 OpenAPI 两条导入路径；
- 独立保存 fill，建立可追溯的 order -> fill -> leg -> position -> strategy 链；
- 支持分批、部分平仓、多腿、roll、到期/行权/指派的明确状态；
- 导入预览、dry-run、重复检测、失败行下载和对账报告；
- 为迁移和重配提供版本号与可逆策略。

退出条件：

- 选定月份的成交数量、费用、已实现盈亏与 Moomoo 对账；
- 同一数据重复导入结果不变；
- 用户抽查至少 20 个策略生命周期，其中包含其真实使用的复杂场景；
- 所有差异可定位到原始行或明确的券商口径。

### Phase 2：单笔复盘工作台与新导航（2–3 周）

**当前进度**：单合约 PositionEpisode 的案例精选、七档 K 线/evidence、常规/扩展时段、ET 时间轴、现金流买卖点、六字段本机未提交草稿、本地证据复盘、按需模型增强、ReviewAnnotation v1 和 Review Queue 已作为第一条纵向切片落地。用户可显式保存绑定 immutable build + PositionEpisode 的事后自述 revision；AI/确定性复盘文本不自动持久化，annotation 也不把条件 P&L、用户自述或单笔相关性升级为账户事实/edge。完整 StrategyEpisode header、TradePlan/RuleEvaluation、期权状态、MFE/MAE、同类 cohort 对照和新导航仍待完成。

交付：

- 用 Today / Trades / Review / Analytics / Playbook / Data 重组主导航；
- 建立 Review Workspace：图表、成交、期权状态、计划、规则、注释同步；
- 进出场标记、MFE/MAE、费用/滑点、市场与事件快照；
- Review Queue 和数据完整度提示；
- 关键流程用 Playwright 做真实浏览器验收。

退出条件：

- 用户可在一个页面完成一笔交易从事实核对到保存结论；
- 抽查 10 笔交易，成交标记与行情时间一致；
- 导入、筛选、打开、标注、保存、下钻、恢复错误等主流程通过；
- 页面不依赖阅读原始日志来判断失败原因。

### Phase 3：Edge Explorer 与盈亏归因（2–3 周）

交付：

- 赢家/输家、自定义 cohort 和匹配 cohort 比较；
- DTE、时段、Regime、setup、IV/Greeks、流动性、规则遵守等维度；
- MFE/MAE、利润捕获率、滑点、错误成本；
- 样本量、置信区间、缺失率和样本外追踪；
- 可复现 notebook 先验证，再把稳定分析生产化。

退出条件：

- 每条洞察可下钻到构成交易和原始证据；
- 小样本不会显示强结论；
- 至少形成 3 条可证伪的候选规律，并定义后续验证窗口；
- 指标计算有确定性测试和人工抽样核对。

### Phase 4：个人 Playbook 与执行纪律（约 2 周）

交付：

- 结构化规则、版本、适用条件、checklist 和失效标准；
- 交易前计划与交易后 RuleEvaluation；
- mistake cost、rule adherence、计划风险 vs 实际风险；
- 规则从 draft 到 validated/rejected 的实验流程。

退出条件：

- 每个常用 setup 至少有一个可执行版本；
- 新交易可在 60 秒内完成交易前计划；
- 周报能区分“策略本身失败”和“没有按策略执行”；
- 规则修改前后可分段比较。

### Phase 5：行情与盘前/盘中决策台（约 3 周）

**当前进度**：已提前交付四层只读纵向切片：`/regime` 顶部按本地自选或服务端研究池请求确定性每日候选，使用上一完整交易日、EMA8/13、量价结构和当日已保存 Regime，逐项展示来源、时间、支持/反证、未知项和 readiness；基础榜先完成，再按需渐进读取 Moomoo underlying IV/IV Rank/HV 概览、最近到期 ATM Call 单点 IV、0–45 DTE 可观测 OI/Volume/无符号 Gamma 集中墙，以及最近交易时段异常期权成交。四层都不调用 LLM、不写交易事实、不输出伪精确总分；期权墙不冒充 Dealer GEX，Moomoo 的方向/情绪/事件标签也不证明开平仓、真实主动方或 dealer 仓位。Canonical 盘前研究已具备服务端研究池、持久化 attempt/stage/lease 与 09:12/09:17 ET scheduler；冻结候选的 5D/20D underlying 路径由独立收盘后任务按 XNYS 日历自动成熟，只有同一 cohort 满 20 个方向样本和 20 个独立交易日才显示描述命中率，且永不自动调权。场外/TRF print、用户确认 Playbook、TradePlan、期权增强冻结、真实期权 P&L 和 TriggerSpec 尚未完成。详细边界见 [`phase1/06_DAILY_OPPORTUNITY_BOARD.md`](../phase1/06_DAILY_OPPORTUNITY_BOARD.md) 与 [`phase1/09_AUTOMATIC_OUTCOME_MAINTENANCE.md`](../phase1/09_AUTOMATIC_OUTCOME_MAINTENANCE.md)。

**下一段实施顺序（foundation 已开始接线；未完成项和待验收项不得描述成当前页面能力）**：

1. **Canonical Premarket Research Cycle**：v1 已接入服务端有序研究池版本、append-only attempt/stage、SQLite lease、重启状态重建和 snapshot/publication/lease 同事务；本地 scheduler 在 XNYS `open-18m`（常规日 09:12 ET）首次生成，失败时在 `open-13m`（09:17）恢复，`open-12m` 禁止新 leader、`open-10m` 硬截止。Web 加载只读 status，不再自动 run；只有用户显式操作才发送 `manual=true`，且不能绕过同一时点和两次 attempt 上限。它仍不是完整 canonical session：当前成功 run 会立即冻结，尚无 append-only draft/finalizer、pool 生效交易日、服务端 last-good、外部进程监管或期权增强冻结，且尚未经过真实 `09:12–09:20 ET` 盘前验收。详细合同见 [`phase1/07_CANONICAL_PREMARKET_RESEARCH_CYCLE.md`](../phase1/07_CANONICAL_PREMARKET_RESEARCH_CYCLE.md) 与 [`phase1/08_SERVER_OWNED_PREMARKET_ORCHESTRATION.md`](../phase1/08_SERVER_OWNED_PREMARKET_ORCHESTRATION.md)。
2. **全宽 Top 5 / near-spot wall**：canonical session 就绪后，再把主页面收敛为全宽 Top 5，优先展示现价附近可执行价位的 Call/Put OI、Volume 与无符号 Gamma 集中墙；完整链与排名外增强按需展开，墙继续不推断 dealer 方向或 gamma flip。
3. **ATM contract shortlist**：最后才从已通过数据门禁的候选中列出具体近 ATM 合约，至少同时显示到期日/DTE、执行价、Call/Put、bid/ask、spread、delta、OI、volume 与各字段 as-of。缺少盘口或时间证据时不生成“可交易”短名单，短名单也只是研究候选，不是方向预测、胜率或订单指令。

这个顺序先固定“哪一份盘前事实”作为同日真源，再确定重点标的和 near-spot 结构，最后才选择具体合约，避免在候选与墙仍漂移时制造看似精确的合约建议。全链路继续永久只读。

交付：

- 数据 freshness 清楚的市场环境、事件和 watchlist；
- 只显示与当前 Playbook 匹配的 setup 候选；
- 期权链、流动性、IV/Greeks 和情景 P&L；
- 保存假设、失效条件、风险预算，生成 TradePlan；
- 仍然不执行券商下单。

退出条件：

- 候选信号能说明来源、数据时间和匹配了哪条规则；
- OpenD/行情降级不会产生“看似实时”的过期数据；
- 用户能从候选到保存计划完成完整只读流程。

### Phase 6：Replay、Shadow、Backtest 与 AI Coach（持续迭代）

交付：

- bar replay 和决策点回放；
- shadow trades 与真实交易对比；
- 规则版本化回测和样本外追踪；
- 周/月度 AI Coach 只引用确定性统计和证据；
- 针对已验证问题逐步加入新分析，而不是批量堆指标。

## 11. 成功指标

### 11.1 产品可信度

- 导入/同步对账率；
- 重复导入为零、未解释差异数量；
- 交易上下文完整率和数据 freshness；
- 用户完成一笔复盘所需时间；
- 待复盘队列完成率。

### 11.2 系统稳定性

- 单实例运行率、API 可用率、关键请求延迟；
- Moomoo 离线时的恢复时间和错误摘要频率；
- 日志日增长量与未处理 incident 数；
- fallback 是否清楚标注而不是静默伪装。

### 11.3 交易过程质量

- Playbook 覆盖率、交易前计划覆盖率、rule adherence；
- 违反规则造成的 mistake cost；
- 实际风险相对计划风险的偏差；
- 过度交易、追价、扛亏等用户确认行为的频率。

### 11.4 交易结果

- expectancy、Profit Factor、最大回撤和尾部损失；
- 盈利集中度、Reality Test、不同 setup 的稳定性；
- 费用/滑点占毛利比例；
- 结果指标用于验证系统和规则，不作为项目承诺的收益目标。

胜率、0DTE 占比、LEAP 占比都可以观察，但不再预设“某个比例必然更好”。只有经用户确认的风险目标和真实数据才能把它们升级为约束。

## 12. 参考产品：借什么，不抄什么

- [TraderSync Features](https://tradersync.com/features/)：借鉴导入、图表上的进出场、风险/止损跟踪、标签和下钻；不照搬其大量独立报表页面。
- [Edgewonk Features](https://edgewonk.com/features)：借鉴 setup/时段/入场分析、规则遵守和错误成本；不让主观评分替代原始证据。
- [TradeZella Help](https://help.tradezella.com/en/articles/5801077-welcome-to-tradezella)：借鉴 Playbook、复盘和 replay 的闭环；不把社交/教育内容引入私人终端。
- [Tradervue Reports](https://app.tradervue.com/help/reports)：借鉴 tag、risk 和 exit analysis；统一到一个可下钻的 Edge Explorer。
- [OptionStrat Features](https://optionstrat.com/features)：借鉴多腿情景 P&L、IV sensitivity、净 Greeks 和可拖动结构；定价输入以真实 Moomoo 合约和盘口为准。
- [TradingView Supercharts](https://www.tradingview.com/support/solutions/43000746464-getting-started-with-supercharts/)：借鉴同步图表、画线、时间周期和 replay 心智模型；不自建完整图表引擎。

## 13. 已确认的产品决定（2026-07-20）

1. **数据来源**：允许项目连接用户本机 Moomoo OpenD，读取真实账户的订单、逐笔成交和费用，用于复盘分析。
2. **第一优先级**：先完成交易后复盘闭环，再建设实时盘前/盘中 cockpit。
3. **券商边界**：长期保持只读；不提供交易密码，不调用解锁、下单、改单或撤单接口。
4. **策略复杂度**：首轮真实数据表明最近 30 天已成交委托几乎全部为期权。数据模型从一开始支持单腿、多腿、分批和 roll；具体 UI 优先级由成交事实自动归类后决定，不再要求用户凭印象选择。

只读不是一句约定，而是实现约束：运行入口采用白名单式查询能力，代码与测试持续扫描交易动作，OpenD 保持锁定。导出、探测和 preview 不追加业务事实；CSV/OpenAPI 证据与 canonical EpisodeBuild 只有经过各自显式 confirm 才能 append-only 写入，并且永不触碰 legacy Journal。内部 `/journal/v2/...` 路径仅为兼容性技术标识，产品名称不显示 `v2`。

## 14. 每项功能的完成定义

任何功能只有同时满足以下条件才算完成：

- 有明确用户任务和退出条件；
- 数据来源、时间和降级状态可见；
- 正常、空数据、加载、错误、离线和恢复状态都可用；
- 后端确定性测试 + 受影响前端构建/交互验证通过；
- 用真实或脱敏 Moomoo 样本人工核对；
- 用户可见行为同步到专题文档和 `docs/CHANGELOG.md`；
- 说明未验证项、风险与回滚方式。

---

当前 Phase 0 的代码止血项已完成，24 小时在线/离线运行观察仍未签字；Phase 1.1–1.2 已建立从 broker evidence 到 PositionEpisode 的第一条可信链路，Phase 1.3B 已完成 OpenAPI 严格预览、DB-aware plan/confirm、identity-link-aware canonical persistence，Phase 1.4 已补齐冻结 canonical set 到显式、可对比 EpisodeBuild 的接线，Phase 1.5 已把案例精选、单个 Episode、七档底层 K 线、常规/扩展时段、ET 时间轴、execution evidence、本地证据复盘、按需模型增强、ReviewAnnotation v1 与 Review Queue 接入同一工作台。当前 accepted CSV 与只读 OpenAPI 稳定窗口逐单核对 1,089 / 3,542 个订单，scope 为 `partial_window`；正式本地账本已追加 OpenAPI batch 3（1,089 orders / 2,107 fills / 1,058 fees）、1,089 order links、1,136 deal links、244 fill-set attestations，并生成 3,542 orders / 5,400 fills、0 blockers 的 canonical set。CSV build 1 与 canonical preview 均得到 5,400 条 fill + 574 条 aggregate order events、1,441 个 PositionEpisode（1,437 closed、4 open / 44 contracts）和 USD 151,750.75 费用守恒；后者必须显式 confirm，且不自动替换默认 build 1。工作台保留 `build_id` 与案例上下文，以现金流方向显示买卖点，区分真实 fill/订单时间代理，显示行情 provenance，并在手动周期、annotation 服务或模型不可用时显式降级；用户自述 revision 与 broker evidence/canonical/P&L 隔离，不会改变 `assumed_flat_unverified`、headline 资格或 Moomoo 永久只读边界。下一步是用 Review Queue 和四类案例完成复杂生命周期抽查，核验 opening snapshot，并补多腿/roll 人工确认、TradePlan/RuleEvaluation 和期权/市场快照；盘前研究已具备服务端版本化 pool、持久化恢复、publication/lease 同事务和 09:12/09:17 本地 scheduler，且于 2026-07-24 完成首轮真实自动触发、原子回滚、恢复发布与幂等验收；5D/20D underlying 结果也具备收盘后自动成熟与严格 20 日统计门槛。下一步仍需重复窗口/睡眠唤醒观察、draft/finalizer、server last-good、pool 生效日、分层 eligibility、TriggerSpec 与期权增强冻结，之后才进入可验证的 near-spot wall 与 ATM contract shortlist。旧 live writer 在这些退出条件完成前保持禁用。未经用户明确确认，不执行 git commit/push；本项目不引入自动下单能力。

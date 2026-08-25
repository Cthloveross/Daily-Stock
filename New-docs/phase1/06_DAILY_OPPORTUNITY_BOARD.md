# Phase 1.6 · 每日机会研究看板

> 状态：Watchlist Top 5 确定性基础榜、Top 5 直出 Moomoo 0–45 DTE 期权墙、全候选期权概览、单票专业研究页、Moomoo 最近交易时段异常期权成交，以及旧 v1 盘前机会快照与 5/20 XNYS 交易日结果学习已接线；Canonical Premarket Research Cycle 已具备服务端研究池、持久化 attempt/stage/lease、XNYS 09:12/09:17 低噪声 scheduler，以及 append-only 三轨 qualification，并于 2026-07-24 真实窗口完成自动触发、原子回滚、自动恢复、冻结发布和幂等验收。5D/20D 标的结果由 `xnys-close-qualified-raw-path-v2` 在目标交易日收盘后自动回填，完整研究命中率另受更严格 track 门禁。draft/finalizer、服务端 last-good 与期权增强冻结仍未完成；当天 Finnhub/Alpaca 权限导致辅助 Regime 数据降级，场外/TRF 大额成交仍未配置，策略 edge 仍未验证。
>
> 安全边界：只读行情研究，不解锁、不下单、不改单、不撤单。

## 1. 用户任务

每天先得到一个有限的候选清单，再决定哪些标的值得进入交易计划。清单必须回答：

- 为什么它今天值得观察；
- 哪些证据支持，哪些证据反对；
- 每项证据是什么时间、什么来源、是否延迟；
- 哪些关键数据仍未知；
- 当前只是研究候选，还是已经满足用户确认过的 Playbook 门禁。

初版不会输出一个伪精确“机会总分”，也不会把候选描述成买卖指令。排序先按 `research_ready / watch_only / context_only / blocked`、风格匹配状态、支持证据数、数据完整度和稳定 ticker 次序完成。

## 2. 当前已交付

- `POST /api/v1/opportunities/daily`：接收最多 20 个自选标的；空列表使用服务端 `STOCK_LIST`。页面把排序后的前 5 个作为主研究清单，其余候选默认折叠，不把十几只股票同时铺在首屏。
- `POST /api/v1/opportunities/option-overview`：最多批量读取 15 个美股 underlying 的 Moomoo 只读 Quote 概览；机会榜中的全部合格候选都可显示供应商 IV、IV Rank、IV Percentile、前值 IV、30/60/90/120/365 日 HV，以及 Call/Put 的当日累计 Volume 与上一清算日 OI。每个标的独立返回 `ready / not_configured / unavailable`，不再把榜单第四名以后永久留在“首批未扫描”。
- `POST /api/v1/opportunities/option-context`：兼容保留的独立最近到期、最接近现价 Call 单合约 IV 接口，供单票详情或其他消费者按需使用；它与 underlying overview 的供应商统计口径分开，失败或前端超时不影响基础候选。`/regime` 主机会榜不再调用它。
- `POST /api/v1/opportunities/option-walls`：单次最多批量读取 5 个标的的 Moomoo 期权链和动态快照；默认只纳入 0–45 DTE 标准合约，分别返回 Top 3 Call/Put OI 墙、当日累计 Volume 墙、unsigned gross gamma concentration 墙，以及由同一动态快照派生的最近到期 ATM Call IV。页面在基础清单完成后自动为 Top 5 渐进加载墙位并直接显示 Call/Put 摘要；选中票的 ATM IV 复用该响应，不再发起第 11 次期权链调用。墙失败或覆盖不足不会阻断基础候选，也不参与基础排名。
- `POST /api/v1/opportunities/option-events`：最多接收三个合格美股 underlying，使用 Moomoo `get_option_event` 的 `OWNER_LIST` 服务端过滤逐标读取最近异常期权成交；每标默认返回 5 条、最多 10 条，并独立返回 `ready / empty / not_configured / unavailable`，任一标的失败不会阻断基础榜或其他标的。
- `POST /api/v1/opportunities/snapshots/ensure`：这是保留的旧 `premarket_prior_close_xnys_v1 / web_daily_opportunity` 结果跟踪路径。它只验证上一完整 XNYS 日线收盘后、对应下一 XNYS 交易日开盘前的因果窗口，不是新的 `08:45–09:20 ET` canonical session，也不再是当前机会页默认写入路径。旧快照、快照列表、到期结果评估和学习摘要继续保留审计，不会被新 scope 覆盖或删除。
- `OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED=true`：启用服务端 5D/20D 结果到期回填任务。任务在精确 XNYS 收盘后 30 分钟首次检查，数据缺口在收盘后 4.5 小时最多再试一次；启动/周末恢复会接续最近已到期交易日。`xnys-close-qualified-raw-path-v2` 只扫描标的路径 track 已 `qualified` 且因果窗口为 `prospective` 的候选，未到期时不触碰行情源；Web 只读展示最近状态，手动按钮仅用于重试已到期缺口。完整运行合同见 [`09_AUTOMATIC_OUTCOME_MAINTENANCE.md`](09_AUTOMATIC_OUTCOME_MAINTENANCE.md)。
- `GET/PUT /api/v1/opportunities/premarket/universe` 与 `POST /premarket/status|run`：Canonical Premarket Research Cycle v1 已升级为服务端版本化研究池、持久化 attempt/stage/lease 与本地低噪声 scheduler。页面只读 status，不再自动 run；scheduler 在 `09:12 ET` 首次生成、必要时 `09:17 ET` 恢复，`09:18 ET` 后拒绝新 leader，`09:20 ET` 硬截止。手动运行必须显式 `manual=true` 且不能绕过同一门禁。当前成功 run 仍立即冻结，尚无 draft/finalizer、服务端 last-good、pool 生效交易日或期权增强冻结，详细合同见 [`07_CANONICAL_PREMARKET_RESEARCH_CYCLE.md`](07_CANONICAL_PREMARKET_RESEARCH_CYCLE.md) 与 [`08_SERVER_OWNED_PREMARKET_ORCHESTRATION.md`](08_SERVER_OWNED_PREMARKET_ORCHESTRATION.md)。
- Opportunity Snapshot 以可选 `qualification` 返回一个 snapshot-level assessment 和三条逐候选分析轨道；官方发布时 qualification 与 snapshot/候选/终态在同一事务追加，旧快照可通过显式 dry-run/apply 回填新 policy assessment，任何路径都不改写已有事实。前端只显示“标的路径 / 日线选股 / 完整研究”的 qualified 计数，不暴露内部 track key。
- `/regime` 顶部“今日机会研究”：主表只展示 Top 5，并直接列出结构、量能、IV/Rank、Call/Put 墙、研究状态以及可复现的确认/失效观察；其余候选、排名外增强和 5D/20D 学习口径默认折叠。页面明确显示本次来自本地 Watchlist 还是服务端默认池，并提供“管理 / 导入自选”入口。
- `/regime/opportunity/:ticker` 单票详情：首屏先给出压缩后的专业摘要、1/5/20 交易日 IV 模型终值区间、关键价位和 underlying K 线；供应商 IV/HV、0–45 DTE 墙与异常成交收进分域标签页，避免同一指标在多个卡片反复出现。详情用于形成 setup、trigger、invalidation、流动性与风险检查，不把任何单项指标包装成自动买卖信号。
- 研究用途由基础证据直接分层：新鲜完整日线同时具备一致方向结构与至少一项独立量能/成交额确认时为 `research_ready`（基础门禁通过）；只有其中一侧成立或方向证据冲突时为 `watch_only`（基础候选·非信号）；结构混合且无量能支持、日线过期或权威 Regime 为 `no_trade` 时为 `context_only`（仅作背景）；核心日线不足或标的不支持才是 `blocked`（精确数据阻断）。缺失或降级 Regime 不再把所有标的统一压成同一种用途；`research_ready` 也只是研究资格，不是入场信号。
- 基础排名只使用上一完整交易日的 OHLCV/成交额、EMA8/13、相对量能、近期结构和已保存的当日 Regime；不调用 LLM。期权概览、墙和异常成交目前是并列研究上下文，不会悄悄改变基础排序，也不会被事后混入官方盘前版本。窗口内发布只写隔离的 `opportunity_*` 研究快照，不写 Journal 或交易事实表。
- 第一阶段 universe 只支持美股期权 underlying；A 股、港股或其他不支持的符号按候选 `blocked`，不会套用纽约收盘时间或 SPY Regime。
- 同一请求复用一套行情 manager，最多四路日线并发；服务端使用 30 秒 TTL + single-flight，前端使用 5 分钟缓存和最新请求保护，减少重复初始化、日志和旧响应覆盖。
- Moomoo 期权概览使用单次只读 Quote batch 覆盖全部合格候选，并按“标的集合 + Moomoo 启用状态 + ET 市场日期”缓存和 single-flight；Top 5 期权墙使用最多五条彼此隔离、可复用且逐次独占的 QuoteContext lane 并行读取，每个标的内部仍按官方 400 合约上限分批，响应按请求顺序重组。主机会榜的 ATM Call IV 与墙共用动态快照；兼容保留的独立 IV 接口、墙和异常事件仍各自缓存与降级，某一重型扫描不会阻断基础清单。
- UI 将 `available / expected` 明确称为“数据完整度”，并另列数据域覆盖；这个计数包含基础日线/技术/Regime 指标，不再误称为纯“量价证据”，期权流、暗池和 Playbook 缺失时也不会显示成“证据完整”。
- 候选排名合同中的 `unusual_options_flow` 与 `off_exchange_prints` 仍不以 0 参与排序；前者虽已有独立实时研究面板，但在完成历史保存、延迟测量和结果验证前不会悄悄改写基础榜权重。
- 个人风格在结构化 Playbook 尚未经用户确认前保持 `unknown`，不从单笔盈利或旧 heuristic 标签伪造“高度匹配”。

### 2.1 期权墙计算合同

期权墙必须固定到期范围后才有可复现含义。当前接口默认 `0–45 DTE`、`standard_contracts_only=true`，在同一标的、同一快照口径内分别按行权价聚合 Call 与 Put；页面返回 Top 3 而非只给一个“神奇价位”，并同时显示现价距离、覆盖率、数据日期、抓取时间、排除合约和局限。当前聚合口径为：

- `CallOI(K) = Σ OI(call, K, expiry)`，Put 同理；OI 墙为聚合值最大的前三个行权价。
- `CallVolume(K) = Σ session_volume(call, K, expiry)`，Put 同理；Volume 是当前市场日累计成交量，不是单笔大单，也不表示开仓量。
- `GrossGamma1Pct(i) = abs(gamma_i) × open_interest_i × contract_multiplier_i × spot² × 0.01`；再按行权价与 Call/Put 分侧求和并取前三名。

`GrossGamma1Pct` 的单位是“标的价格变动 1% 时，对应的近似美元 Delta 对冲名义变化”。它是 unsigned gross gamma concentration，只描述公开 OI 在当前 Gamma 下的集中程度。接口不把 Call 人为设为正、Put 人为设为负，因此不称 `Dealer GEX`，不推断做市商净持仓，也不计算 gamma flip。实际 Dealer Gamma 的方向需要参与者持仓、买卖及开平仓身份，而公开 OI、Volume 和当前快照并不提供这些事实。

响应必须保留 `market_date_et`、`generated_at`、OI/Volume/quote 的 as-of 说明、请求/有效/排除合约数和 coverage ratio。OI 是交易所清算后按日更新的数据，Volume 是当日累计，Gamma/spot 是当前快照；这些时间口径不得被合并描述成同一时点的实时仓位。覆盖不足、任一链日期窗口/快照批次失败、权限缺失、OpenD 不可达或必要字段非法时逐标降级，不能用 0 填补缺失合约或把部分窗口误报为完整覆盖。

Top 5 不再逐标串行等待完整链。服务端为每个同时扫描的 underlying 独占一条可复用 QuoteContext lane，避免同一个 SDK context 被多线程交叉使用；每条动态快照请求仍最多 400 个代码，单标失败只降级该标，30 秒 TTL 与逐标 single-flight 继续阻止相同请求重复放大。2026-07-24 本机同一组 TSLA/NVDA/AAPL/META/COIN、0–45 DTE、服务重启后冷缓存验收从 `30.47s` 降至 `9.64s`，10,374/10,374 张请求合约均返回且 0 个失败批次；该数值是当次环境证据，不是跨机器 SLA。实现仍受 Moomoo `get_market_snapshot` 每 30 秒 60 次的官方额度约束，多个页面或不同标的集合并发刷新可能触发逐标部分覆盖，不能以重试风暴掩盖。

默认 `0–45 DTE` 每标最多拆成两个 30 日 `get_option_chain` 日期窗口，Top 5 冷加载最多占用 10 次链查询，正好等于 Moomoo 当前环境观测到的 `10 次 / 30 秒` 上限。`option-wall/1.1` 因此在每个 item 内新增 `atm_call_iv`：先固定墙快照的最近到期日，再选最接近 spot 的 Call，并只读取该合约在同批动态快照中的 IV；精确 ATM 合约缺 IV 时 fail closed，不改选其他执行价或到期日。主机会榜不得再并发调用 `/option-context`，否则第 11 次链查询可能令最后一个标的部分覆盖。多个标签页同时对不同集合强制冷刷新仍可能竞争供应商额度，当前缓解不等于全局跨进程限流器。

`option-wall/1.2`（2026-08-01）在每个墙位 level 上 additive 补齐逐层字段：`side`（call / put / call_put_aggregate）、`metric_basis` 结算口径（OI＝T-1 清算存量、Volume＝当日累计、Gamma＝模型值）、按贡献排序的 top 3 到期日 `expiry_breakdown`（expiry / dte / metric_value / share_of_level_percent / contract_count + `other` 汇总桶），以及逐到期 quote 上下文——仅当该 strike×expiry×right 单元由恰好一行快照支撑时附该行的 `iv_percent` 与 `quote_as_of`，多行聚合不归属报价。Moomoo 墙快照行当前不携带 bid/ask/mark，这些字段保持显式 null 并通过 `quote_evidence`（observed / partial / unavailable）标缺，不做零值回填；旧字段与端点签名不变。

期权墙 UI 使用主表格承载可比较字段，逐层可展开到期分布与报价证据（缺失按「标缺」显示），详情区解释公式、来源、coverage/as-of 与不能推断的内容，并显式显示“计算可复现、策略有效性未验证”。墙位目前只是独立研究上下文，不进入确定性基础榜排序，也未持久化为历史序列或完成交易结果回测。

### 2.2 异常期权成交合同

本机已将 Moomoo OpenD 从 `10.4.6408` 升级到 `10.9.6918`，Python SDK 升级到 `10.9.6908`，并通过只读 Quote Context 验证官方 [`get_option_event`](https://openapi.moomoo.com/moomoo-api-doc/hk/quote/get-option-event.html) 可用。该升级没有解锁交易，也没有调用下单、改单、撤单或交易解锁接口；项目安全边界仍是行情研究只读。版本能力与变更可对照 [Moomoo OpenAPI changelog](https://openapi.moomoo.com/moomoo-api-doc/en/changelog/changelog.html)。

接口为每个 underlying 使用 `OWNER_LIST` 精确过滤，响应固定 `schema_version=option-event/1.0`，并保留 `source=moomoo_openapi`、`fetched_at`、最近事件的 `event_as_of`、供应商返回总数与本次返回数。`not_configured / unavailable` 没有伪造 as-of 或总数，成功但无事件时明确返回 `empty` 与 `all_count=0`。单条事件提供稳定事件标识和以下可审计字段：

- 合约与时间：`option_code`、underlying、`fill_time`、Call/Put、行权价、到期日与 DTE；
- 成交事实：成交价、合约数、成交权利金，以及成交时 underlying、bid/ask；
- 期权上下文：`iv_percent`、当日累计 `total_volume`、`total_open_interest`、`vo_ratio_percent` 与 `delta`；
- Moomoo 分类：`ticker_type`、`sentiment`、`order_types` 与 `strategy_type`。

页面只在当前选中候选的详情区异步展示最近 5 条，并称为“最近交易时段异常成交”：盘前或休市期间返回上一交易时段事件时，不冒充“今天刚发生”。UI 逐行展示事件发生时间，并在区块底部显示来源和事件截至时间；响应中的抓取时间用于空结果 provenance 与降级兜底。加载、空数据、未配置与不可用分别呈现，不用 0 或演示数据填补。

缓存和限流按逐标设计。服务端以“schema version + 标的 + Moomoo 启用状态 + ET 市场日期 + 每标条数”为键使用 30 秒 TTL 与 single-flight；同一标的、同一条数的重叠请求复用在途或已完成结果。异常事件使用与重型期权墙扫描分离的只读 QuoteContext lane，不会在应用层因 0–45 DTE 合约快照仍在分批读取而等待同一把锁。前端也使用 30 秒缓存、in-flight 复用与最新请求保护，切换候选不会让旧响应覆盖新选择。每次页面请求最多三个标的、每标最多十条，正常路径每标只读一个首页，低于 Moomoo 官方 `get_option_event` 首页查询的 30 秒 60 次限频；接口不自动展开全量分页。

这些标签必须按供应商口径理解，而不是项目自行证明的交易意图：

- `ticker_type=BUY / SELL / NEUTRAL` 是 Moomoo 对成交方向的分类，不证明某个已识别参与者真实主动买入或卖出；
- `sentiment=BULLISH / BEARISH / NEUTRAL` 是 Moomoo 标签，不是本项目模型结论或收益预测；
- `NORMAL / SWEEP / CROSS / FLOOR` 与 `SINGLE_LEG / MULTI_LEG` 是供应商的成交/策略分类，不能单独证明开仓、平仓、滚仓、对冲目的或最终净方向；
- 公开事件不提供参与者身份和 dealer inventory，因此不能推断 dealer 多空、净 Gamma、真实主动买卖方，也不能把单笔成交直接称为机构押注。

异常期权成交当前只作为独立研究证据，不进入确定性候选排名，不写入 OpportunityRun 或交易事实账本，也没有历史回测。事件是否对用户 Playbook 有增量价值，需先保存不可变历史快照，再按 setup、Regime、DTE 与 5d/20d 结果做样本外验证。

### 2.3 单票详情与数据时钟

机会榜只承担横向筛选，单票详情承担纵向研究。2026-07-31 起榜单与详情实现第一层同版本绑定：官方 canonical 榜单跳转详情时 URL 携带 `?snapshotKey=ops_…`，详情页经 `GET /api/v1/opportunities/snapshots/{snapshot_key}` 优先读取同一份冻结 run 的候选证据（数据时点栏显示「官方快照 … · 冻结于 …」），不再对该标的重跑即时扫描；preview 榜单或直接输入 URL 时显示「即时扫描 · 未绑定官方快照」，快照缺失/不含该标的时显式提示并回退即时扫描。期权 overview/context/walls/events 与 K 线仍是实时增强，各自携带 as-of，不进入冻结榜单证据。官方绑定页另显示「冻结与当前差异」条（冻结基准 close vs 当前 Moomoo spot 的百分比变化，注明不改变冻结榜单结论；任一数值缺失时整条隐藏）。详情页按“先结论、再图表、最后证据明细”组织，默认首屏只保留决策最相关的信息：一句话专业摘要、方向背景与 setup、上一完整交易日高低点、IV 所处状态、1/5/20 交易日模型区间、最近的关键墙位、反面证据和仍未知的数据。摘要只能压缩下方已经存在的结构化证据，不允许由单一异常成交、OI 墙或高 IV 生成伪确定性的涨跌结论、胜率或交易指令。

详情页提供 `1m / 2m / 5m / 15m / 30m / 1h / 1D` 七档 underlying K 线，并在当前可见时段的 close 上计算 EMA8 / EMA13。EMA 使用共享的 SMA-seeded 实现（`apps/dsa-web/src/utils/ema.ts`，2026-07-31 起与 Journal 复盘 overlay 及后端 `_ema_last` 同一语义）：前 period 根 close 的均值作为 seed 落在第 period 根 bar，样本不足一个完整 seed 时该线为空，不用首 close 递推或补零。美股分钟图默认只显示纽约常规时段 `09:30–16:00 ET`，可显式切换到含盘前盘后的 `04:00–20:00 ET`；筛选按 `America/New_York` 自动处理夏令时，切换后 EMA8 / EMA13 立即基于新的可见 bars 重算，顶部 K 线时点也同步显示当前可见最后一根，避免常规时段图误报 `20:00 ET`。日线不显示交易时段切换。分钟图用于观察执行环境，日线用于结构背景；最终 bar、EMA 或墙位都不是已验证的入场触发器，页面不能事后把它们描述成成交时已经可见的信号。首屏之外的信息按用途收进四个标签页：

- “波动与情景”集中展示 IV、IV Rank/Percentile、前值 IV、HV 与模型终值区间的假设和限制；
- “期权墙”集中展示 Call/Put OI、当日 Volume 与 unsigned gross gamma concentration、覆盖率和 as-of；
- “异常成交”集中展示供应商事件标签、成交时盘口和发生时间，不把事件分类改写为机构意图。
- “数据说明”集中展示各数据域时钟、概率模型边界、执行门禁与数据源返回限制。

摘要中的数值只出现一次；标签页承担完整字段、provenance 和方法解释，页面底部不再重复同一组 IV、OI、Volume 和限制文案。窄屏下优先保留摘要、图表和关键价位，重型明细通过标签切换查看，避免为一次研究强迫用户连续滚动多个重复面板。

页面按数据域显示时钟，禁止用一个笼统的“实时”标签覆盖不同口径：

- 基础候选的 OHLCV、EMA 与结构证据来自上一完整 XNYS 交易日；
- Option Overview 的 Call/Put Volume 是当前交易时段累计，不是单笔大单或开仓量；盘前通常仍对应尚未开始或供应商最近可用时段，必须连同 `session_volume_date` 展示；
- Option Overview 与期权墙中的 OI 是上一清算交易日（T-1）事实，不是盘中实时持仓；OI 多寡本身不提供多空方向；
- IV、IV Rank、IV Percentile、HV 和 Greeks 是 Moomoo Quote 统计/模型快照。IV 表达市场隐含的波动幅度，不表达上涨或下跌方向；Greeks 是理论敏感度，不保证实际价格变化；
- 异常成交保留供应商 `fill_time` 与本地 `fetched_at`，供应商 BUY/SELL、情绪和订单类型标签不等于项目证明的参与者意图；
- FINRA OTC/ATS 公共统计属于延迟背景，不能与当天盘中事件放在同一时钟或用于声称“今日暗池买入”。

详情页的专业研究顺序固定为：先看 underlying 结构和相对量能，再看可执行性（合约 bid/ask、spread、成交量和 OI 的时点），再看 IV 水平/期限/偏斜，最后把墙和异常成交作为上下文。当前接口尚不能完整提供合约级 spread、期限结构与 skew，因此这些字段应显示为待补数据，而不是由 overview 聚合值推断。任何研究结论都应写清 setup、预期触发、失效条件、风险预算和时间止损；本阶段页面只辅助检查，不替用户生成订单。

### 2.4 IV 模型终值区间合同

专业摘要使用 underlying 现价 `S0`、供应商当前年化 IV `σ` 和 `N = 1 / 5 / 20` 个交易日生成模型终值区间。当前采用零远期价格漂移、每年 252 个交易日的简化 lognormal 近似：

- `v = σ × sqrt(N / 252)`
- `Lower(N, z) = S0 × exp(-0.5 × v² - z × v)`
- `Upper(N, z) = S0 × exp(-0.5 × v² + z × v)`
- `z = 1` 标记为约 68.27% 模型区间，`z = 2` 标记为约 95.45% 模型区间。`-0.5 × v²` 是零远期漂移近似下的对数收益均值修正，使模型中的期望终值保持在现价附近。

这里的 68%/95% 只是“在该简化模型及当前 IV 不变的前提下，N 日后终值落入区间”的理论覆盖率。它不是历史样本校准后的真实概率，不是策略胜率、上涨/下跌概率、盘中触及上下界的概率，也不表示价格会沿某条路径运动。页面必须称其为“IV 模型终值区间”，不得简称“目标价”“预测价”或“有 68% 概率不会突破”。

该近似没有纳入期限结构、volatility skew/smile、利率、股息、财报或宏观事件跳跃、波动率随时间变化和 fat tails；underlying overview 的聚合 IV 也不等同于某个准备交易的具体合约 IV。缺少有效现价或 IV 时区间整体留空，不以 HV、ATM 单合约或旧值静默替代。后续只有经过不可变快照、同口径历史覆盖率检验和按 Regime/setup 分层校准后，页面才可以额外展示“历史实现覆盖率”；即使完成校准，也必须与策略胜率和触及概率分开。

### 2.5 扫描可用性与状态分层

基础日线的美股个股路由固定为：已启用且健康的 `MoomooFetcher` 优先，其后才按配置回退 Yfinance / Longbridge；美股指数仍保留独立远端路由。这样本地 OpenD 已可读时，不再先等待易受限流影响的远端源。一次请求继续复用同一 manager 与每个 fetcher 的调用锁，避免多个 worker 并发操作同一 QuoteContext。

基础候选请求的 Web 墙钟预算为 35 秒；期权概览、ATM IV、墙和异常事件分别独立加载，不能延长“扫描中”状态。手动“重新扫描”同时绕过浏览器缓存与服务端 30 秒已完成结果 TTL，但仍复用相同 key 的在途请求，避免连续点击放大数据源流量。刷新期间保留上一份可用列表；如果本次只得到临时行情阻断，不用十个 `blocked` 候选覆盖 last-known-good，而是明确提示并允许稍后重试。

Alpaca 盘前涨跌只接受纽约时间 04:00–09:30 内已经完成且不超过 5 分钟的 1 分钟 bar，并以 `exchange-calendars` 解析出的精确上一 XNYS session 收盘价为分母。未来日期、盘前未开始、缺少或日期错误的前收、未完成分钟和过期数据全部 fail closed，不再用最近几根分钟线的开盘价近似，也不把未知编码成 0%。

Finnhub 的经济日历与自选股财报日历分别记录 readiness。一个子域 403、超时或权限不足时，只屏蔽该子域对应的宏观分数，不再把另一个成功子域一起清零；旧 degraded 载荷没有子域 readiness 时继续保守不计分。失败诊断会移除 HTTP query string，并脱敏 `token/api_key/key` 及当前配置的 key；仍保留异常类型、HTTP 状态和不含查询参数的路径。

Web 将一次研究周期明确展示为“基础榜 → 期权概览 → Top 5 墙”三个阶段，并分别标注等待、读取、可用、增强降级或失败。状态栏同时展示本次扫描请求日、候选基础证据日期（混合日期时显示范围）和 ET 生成时间；阶段进行中禁用重复刷新，但继续展示上一成功结果及其生成时间。期权概览等可选请求失败只标记增强降级，不把基础榜误报为整体失败；执行价墙只覆盖部分 Top 5 时显示实际覆盖数量/比例，不把部分结果冒充完整。Regime 首次请求的 loading skeleton 与机会榜独立挂载，不再阻塞基础扫描开始。

这三个 Web 加载阶段不等同于 canonical backend 的 `resolve_window / compute_regime / scan_completed_bars / quality_gate / persist_snapshot`。页面必须以服务端 `published + snapshot` 为冻结证据；展示阶段本身不能证明 canonical session 已运行或已冻结。页面加载只读 status，不设置自动 run timer；`running` 时最多短期轮询只读 status，且不并发启动 `/daily`。只有用户明确点击时才发送 `manual=true`，服务端仍执行全部时点、pool、attempt、lease 与质量门禁。

官方盘前状态卡把三个彼此独立的轴明确分开，不能再压缩成一个“可用/不可用”标签：

- **发布状态**：回答今天的官方版本是待发布、生成中、已发布还是未发布；只有 `published + snapshot` 证明不可变版本存在。
- **数据质量**：回答本批输入是完整、支持证据降级还是阻断。`published + degraded` 是合法状态，表示官方版本已经冻结、可以用于今日研究，但辅助证据不完整。
- **统计入样**：新快照有 `qualification` 时分别显示“标的路径 X/Y、日线选股 X/Y、完整研究 X/Y”；`X` 是该 track 的 `qualified_count`，`Y` 是 `qualified + excluded + unverified`，不是命中数。旧 API 缺少 qualification 时继续显示“已纳入 N/M / 未纳入 N/M / 尚未产生”，不能把缺字段默认成三轨通过。`published + degraded` 可以出现“标的路径 5/5、完整研究 0/5”：它表示基础价格路径仍可前瞻回填，但不能进入完整研究命中率，不是发布失败。

候选研究内容另按以下三层组织：

- **基础扫描**：美股支持范围、至少 21 根新鲜完整日线、来源与 as-of；缺失时显示具体的“行情源暂不可用 / 日线不足 / 日线已过期 / 标的不支持”，不再统一称为“证据不足”。
- **市场背景**：Regime 可用于风险环境解释；缺失或降级时仍可浏览股票量价结构，但不得把它冒充已通过的市场门禁。
- **排名外增强**：异常期权成交、期权墙、完整逐笔期权流/NBBO、FINRA ATS/TRF 背景与未确认 Playbook。它们影响后续研究深度或个人风格匹配，当前缺失不阻断基础扫描，也不在每只股票上重复显示成待补核心证据。

`hard_gates.status=unknown` 不等于失败；只有 `failed` 才进入基础门禁失败摘要。`no_trade` 在输入质量合格时表示“市场风险关闭”，不是权限或证据错误。候选列称为“研究用途”而不是交易触发：`research_ready / watch_only / context_only / blocked` 分别显示“基础门禁通过 / 基础候选·非信号 / 仅作背景 / 精确阻断原因”。页面保留完整 readiness/provenance 明细，但将可选增强收进折叠说明，首屏优先回答候选是否可扫描、数据来自哪里、为什么值得观察。

### 2.6 Watchlist 来源与 TradingView 边界

普通 `/daily` 预览优先使用 Web 本地 Watchlist，按用户顺序最多扫描前 20 只，再从确定性基础排名中展示 Top 5；没有本地自选时才使用服务端 `STOCK_LIST`。官方盘前版本只使用 `/watchlist` 上显式保存的服务端研究池；页面显示本地列表与正式版本是否一致、版本时间与来源，不会静默同步。TradingView TXT 导入继续只合并到本地列表，用户确认后才保存为正式池。

TradingView 面向个人网站账户没有公开的自选列表 REST API；其公开 REST API 是给 broker 集成使用，Charting Library 的 Watchlist API 只管理嵌入式 Trading Platform widget 的 Watchlist，不能当作用户在 tradingview.com 上的个人列表同步接口。因此当前不保存 TradingView 登录信息、不抓取私有网页，也不声称实时双向同步。用户在 TradingView 导出 TXT 后导入，是官方支持、可审计且不依赖浏览器会话的边界：

- [TradingView：导入或导出 Watchlist](https://www.tradingview.com/support/solutions/43000487233-how-to-import-or-export-a-watchlist/)
- [TradingView：个人数据 API 说明](https://www.tradingview.com/support/solutions/43000474413-i-need-access-to-your-api-in-order-to-get-data-or-indicator-values/)
- [TradingView Charting Library `IWatchListApi`](https://www.tradingview.com/charting-library-docs/latest/api/interfaces/Charting_Library.IWatchListApi/)

5D/20D 区块是前瞻结果跟踪，不是今日候选筛选门禁。UI 用“已回填 / 等待目标日 / 已到目标日但缺行情”描述结果生命周期；`mature_count` 只表示目标窗口已经到达并保存了结果，不表示策略“成熟”，`pending_count` 也不表示交易机会“待观察”。没有任何统计入样时只显示“尚无可统计样本”，完整样本门槛与 cohort 口径放在折叠区。

### 2.7 盘中跟踪（G-2 / G-4 首批指标）

盘前计划一旦生成即视为冻结基准；「盘中跟踪」区块（`IntradayTrackingPanel`，位于机会看板下方）只把当日实时行情对照这份冻结计划，**不重新排序、不生成买卖信号、不改变盘前排名**，页头明示「跟踪冻结的盘前计划 · 不是信号 · 不改变盘前排名」并全程带数据时点。

数据由 `POST /api/v1/opportunities/intraday-tracking`（最多 5 个美股期权 underlying）返回，实时字段复用与期权墙相同的 Moomoo `get_market_snapshot` Quote-only 机制；服务端 30 秒 TTL + single-flight 与兄弟接口一致，日线派生输入（ATR14、20 日量能中位）按 (symbol, ET market date) 另行 15 分钟记忆，避免 60 秒轮询反复触发日线数据源。每标的列：现价（as-of）、距确认位、距失效位、VWAP 上/下方、量能节奏、盘段状态。

指标定义与诚实边界（G-4 首批，相对强度 vs SPY 后续交付）：

- **确认/失效价**：直接取冻结候选结构化证据的同一数值锚点（`prior_20d_range_position` 的前 20 日高/低，退化时用 `ema8_ema13_alignment` 的 EMA13），不解析展示字符串；方向混合的候选没有单一触发价，显式标缺而不是猜测。
- **ATR 标准化距离**：美元距离 ÷ ATR14；ATR14 用与每日榜相同的已完成日线加载器，Wilder 平滑（TR 取 max(H−L, |H−C₋₁|, |L−C₋₁|)，前 14 根 TR 简单均值做种子，其后 `ATR = (ATR₋₁×13 + TR)/14`），不含当日未完成 K 线。符号约定：距确认位 > 0 表示尚未触发，距失效位 > 0 表示仍有缓冲。
- **VWAP**：当日累计成交额 ÷ 累计成交量的近似值（basis 标签 `session_turnover_over_volume`），不是逐笔加权官方 VWAP；两个输入缺一即显式标缺原因，绝不回填。
- **量能节奏**：当日累计成交量 ÷ 前 20 个交易日**全日**成交量中位数（basis 标签明示 full-day median），**未按盘中时点折算**——开盘初段比值偏低属正常，这是 v1 的诚实口径而不是 bug。
- **盘段状态**：premarket/regular/afterhours/closed 由 America/New_York 时钟判定（basis `america_new_york_clock_v1`），未接入交易所假日日历。

刷新策略：手动刷新按钮 + 可选 60 秒自动刷新；自动刷新只在页面可见（`document.visibilityState`）且盘段为盘前/盘中时运行，其余情况彻底停表。Moomoo 未启用或快照缺失时逐标的显式 `not_configured` / `unavailable`，实时字段保持 null，从不显示伪造的 0。

### 2.8 日内工作台与日内 Top（G-6：日内 / 周内看板分离）

2026-08-02 起，原先单一的机会看板显式拆成两种互不替代的研究口径：

- **周内 Top 5 · 盘前冻结**（`/regime`，本文档 §2.1–§2.7 的既有看板）：基于上一完整交易日日线结构，研究周期数日至数周；官方版本在盘前窗口冻结，进入 qualification 与 5D/20D 结果统计。页头副标题 2026-08-02 起如实标注「基于上一完整交易日日线结构 · 数日至数周研究周期」，并提供「进入日内工作台 →」入口。
- **日内工作台**（`/intraday`，导航首位）：盘中交易时段的单屏主界面，盘中滚动、不冻结、不入统计。

`/intraday` 页面自上而下：

1. **市场脉搏**（sticky 顶栏）：**时段上下文标签**（v3，条首醒目位，见下）+ SPY / QQQ / VIX 快照现价与当日涨跌（分母为快照自带前收，`change_basis=moomoo_snapshot_prev_close`）+ SPY/QQQ 会话 VWAP 位置（v3，累计额/量近似）、盘段状态、ET 数据时点与自动刷新指示。数据来自 `GET /api/v1/opportunities/intraday-pulse`（60 秒 TTL + single-flight）；VIX 与 SPY/QQQ 隔离请求，供应商快照不可得时逐代码显式标缺，不用其他来源或旧值冒充。
2. **今日计划**：冻结盘前 Top 5 的对照跟踪，直接复用 §2.7 的 `IntradayTrackingPanel`（该组件同时保留在 `/regime` 官方看板下方，行为不变；本页是它的主要使用场景）。今日没有已发布官方版本时显示诚实空态，不用预览榜冒充冻结计划。
3. **日内扫描表**（核心）：`POST /api/v1/opportunities/intraday-top` 的盘中滚动 Top 5。2026-08-03 起改为**两级布局**：默认网格只保留交易关键读数，次要读数移入行内展开区，一键可达——重排可见性，不删除任何读数，标缺语义不变。默认 11 列（2026-08-15 现状，「你的战绩」按用户要求收尾）：**排名**（服务端排名，客户端列头排序不改写）/ 标的（次行小字合并深度位徽标与财报警示徽标：回避窗内「财报 N 天内 · 期权贵」实底警示、日历标缺显式「财报标缺 · 未知≠安全」）/ 涨跌%（现价与 as-of 为次行；**盘前时段**主行改示真实盘前变动「盘前 +2.4%」、标缺显式「盘前标缺」，副行如实标「昨日 +5.1%」——G-12：`session_change_percent` 此刻仍指向上一常规时段；列头排序同口径按盘前涨跌）/ **当前爆发**（爆发分 + 方向箭头 + 15 分钟推力% + 量比；v7 哑火徽标命中才渲染）/ **今日波段**（signal v5 分级波段 chips：「09:45↓ 强」实底警示＝爆发分 ≥8 暴动、「10:00↑ 中」描边＝≥2.5 持续推升，缺 grade 的旧载荷渲染无分级 chip；空＝「—」、不可得＝标缺；每波窗口/推力/爆发分/分级明细以可访问 aria-label 附带；休市显示最近一个交易时段）/ **速度**（v3：加速↑/减速↓/持平/标缺）/ **近30分位移**（v6，见 §2.13）/ **形态**（v4 styleMatch：S1/S2/S3 相似度徽标，见 §2.10）/ **期权墙**（2026-08-15：深度层每檔上一清算时段 OI 的未平仓分布事实，紧凑两行「C 210 · P 195」+「γ205 · 中心202.4」——C/P＝两侧最大 OI 行权价、γ＝1% 波动下 gross gamma 集中行权价、中心＝Σ(行权价×OI)÷Σ(OI) 描述性重心；单元格 tooltip 逐位给 OI 张数/占该侧%/距现价%/主力到期与 as-of；**不渲染任何「最可能收在 X」**——pinning/max pain 假说在用户数据上尚未验证（逐日快照 2026-08-04 起积累），该边界逐字挂列头与单元格 tooltip；数据经共享 hook `useDeepLaneOptionWalls` 与盘前期权异常面板共用同一次 option-walls 读取与缓存，深度层外/读取失败显式标缺）/ 详情按钮 / **你的战绩**（个人画像回灌，见 §2.12；2026-08-14 按用户要求移到最后一列——它是上下文标注，不是盘中交易关键读数，语义与列头 tooltip 不变）。次要读数（波段vs大盘 / 量能节奏 / 缺口 / 波幅扩张(ATR) / VWAP / 期权异动 / 财报全文 / 研究状态+证据计数）在行尾「详情」展开的「研究读数」网格中（临期合约面板上方）；仅快照次级列表默认只显示前 5 檔（「展开全部 N 檔」切换）；footer 收敛为一行排序与口径说明（含 v5 波段分级图例，2026-08-15 起可见行只留数据口径）+「完整口径」切换展开完整公式墙与「不是信号/非预测」类披露句（原文逐字保留，隐藏 ≠ 删除，见 §16 界面披露策略）。默认排序＝服务端排名（盘中爆发分优先），涨跌%/当前爆发列头可点做客户端排序（第三次点击回到服务端排名），行点击进入 `/regime/opportunity/:ticker` 即时扫描详情（不绑定 snapshotKey）。
4. **盘前期权异常面板**（侧栏，2026-08-14 起，见 §15）：深度层标的上一时段的 call/put 偏斜与大单事实——盘前 + 开盘 30 分钟自动展开，其后收起为次要参考。原**期权异动 feed**（跨自选池、按时间倒序的最近异动成交，≤20 条）同日移入 Journal 仓位复盘面（`JournalOptionEventReview` 包装组件，只读一次不轮询）：用户定位「期权异动是复盘证据，不是盘中决策输入」；feed 组件本体与「分类不证明开平仓方向」脚注原样保留。

`POST /api/v1/opportunities/intraday-top` 合同（`schema_version=intraday-top/1.0`，`signal_version=intraday_session_evidence_v4`）：

- 请求：`symbols`（≤20，空数组回退服务端 `STOCK_LIST`）、`limit`（默认 5，1–10）、`refresh`。非美股期权 underlying 不参与扫描并在 `unsupported_symbols` 中如实列出。
- 装配全部复用 G-2 机制：会话快照来自与期权墙相同的 Moomoo Quote-only `get_market_snapshot`；ATR14 / 20 日量能中位 / 上一日结构（前收、前 20 日高低、EMA8/13）来自与每日榜相同的完成日线加载器（15 分钟逐标的记忆）；期权异动逐标的复用 `option-events` 的 30 秒缓存 key（每标最近一页 ≤10 条，失败只降级该标的）。波段爆发的 5m K 线走与 `/stocks/{code}/history?period=5m` 相同的服务端加载器，逐标的只取当前 + 上一交易时段常规时段（有界线程池并发 + 逐标的 60 秒 TTL 缓存；单标的失败只把该标的的波段爆发显式 `unavailable`，绝不阻塞聚合证据）。响应级 60 秒 TTL + single-flight，`refresh` 只绕过已完成 TTL。
- 每个候选带与每日榜同构的 evidence 数组（source / observed_at / fetched_at / observation_window / quality_state / limitations）。`ranking_method` 盘中为 `burst_score_first_then_evidence_count`（当前爆发分优先，其次证据计数，再次研究状态），休市退回 `rule_based_evidence_count`（但候选仍附最近一个交易时段的波段列表，晚间复盘可见「今日走了几波」）。

**波段爆发（v2 主排序信号，`src/opportunities/intraday_bursts.py`）**：对当日常规时段（09:30–16:00 ET）的 5m K 线取滚动 3 根（15 分钟）窗口，`thrust_norm = |窗口末收 − 窗口首开| ÷ 当日 5m 波幅（high−low）中位数`，`vol_norm = 窗口成交量 ÷ (3 × 当日 5m 量中位数)`，`burst_score = thrust_norm × vol_norm`，方向取推力符号。当日不足 6 根 K 线时中位数基准回退上一交易时段并显式标注 `median_basis=prior_session_fallback`。**当前爆发**＝最新窗口；**今日波段**＝`burst_score ≥ LEG_MIN_SCORE(8.0)` 且窗口起点相隔 ≥30 分钟的 top 独立窗口（≤4 个，按时间正序）。证据项「波段爆发」在当前窗口 `burst_score ≥ BURST_SUPPORT_MIN(6.0)` 时记 supports。

校准记录（2026-07-31 周五常规时段，机会由用户标注；K 线固化为永久回归 fixture `src/opportunities/tests/fixtures/intraday_5m_2026-07-31_regular.json`）：MU ~09:45 跳水（−5.2%/15min @4.6× 量）爆发分 35.5、AMZN 09:30 开盘波 18.7、NVDA 09:40 波 9.3、NVDA 15:15 波 8.6——`LEG_MIN_SCORE=8.0` 是四个标注波段全部通过的最大整数档（约束项＝NVDA 15:15 的 8.6）；`BURST_SUPPORT_MIN=6.0` 低一档，让发展中的爆发提前一根 K 线点亮（NVDA 09:35 前奏窗口 6.3）。GOOGL 是 v1 失败样本：全时段聚合曾把它排第一，但其当日最佳窗口仅 15.2，远低于 MU 跳水的 35.5——该修复由校准回归测试永久钉住。阈值均为 v2 启发式，不是验证过的 edge；改动必须重新校准并升 `signal_version`。

v2 聚合证据阈值（保留 v1 语义，退居次要排序因子；代码内常量注释文档化；修改必须升 `signal_version`）：

| 证据 | 记为 supports 的条件 | 口径说明 |
|---|---|---|
| 波段爆发 | 当前 15 分钟窗口 burst_score ≥ 6.0 | 推力×量比，中位数归一化；阈值按 2026-07-31 标注样本校准 |
| 量能节奏 | ≥ 1.5× | 当日累计 ÷ 前 20 交易日全日中位（未按时点折算，同 G-2） |
| 缺口 | \|开盘−参考前收\| ≥ 0.75×ATR14，或 \|缺口%\| ≥ 1.5% | 参考前收＝日线加载器上一完整日收盘；休市时段切换为快照自带前收并显式改 `gap_basis` |
| 波幅扩张 | (session high − session low) ÷ ATR14 ≥ 1.0 | ATR14＝已完成日线 Wilder 平滑 |
| 期权异动 | 最近一页事件 ≥ 3 条且 Moomoo sentiment 多数方向非中性 | 只统计供应商分类；平票记 `mixed`、无分类记 `unknown`，绝不发明方向 |
| VWAP 位置 | 与缺口方向一致（缺口向上且价在 VWAP 上方，或反之） | VWAP＝累计额/量近似 |

研究状态（`research_state`）：`active`（盘中活跃，≥2 项独立支持）/ `watch`（观察）/ `insufficient`（数据不足——缺可用现价快照时 fail-closed，无论其他证据如何）。上一日结构（前 20 日区间位置、EMA 排列）只作 `research_context` 证据，永不参与盘中排序计数。

**v3 上下文信号（2026-08-02，`intraday_session_evidence_v3`）**：把用户自己的交易纪律（Playbook 候选 R1/R3，按其 1,653 笔已平仓交易统计数据核验）编码为四类诚实**上下文标注**。设计规则固定为「**系统标注，用户过滤**」：这些信号只加标签，绝不自动过滤行、绝不隐藏候选、绝不阻断任何操作，也不参与排序键或 supporting_evidence_count。

1. **时段上下文**（`session_phase`，pulse 与 intraday-top 响应均携带）：常规时段按 ET 时钟再切分为 `opening_probe`（09:30–10:00）/ `prime`（10:00–11:00）/ `midday`（11:00–13:00）/ `noise`（13:00–14:00）/ `afternoon`（14:00–15:00）/ `power_hour`（15:00–16:00），盘前/盘后/休市沿用原盘段。每个 phase 带中文标签 + 用户历史统计提示（`session_phase_hint_basis=user_trading_history_hardcoded_v1`，硬编码 v1 文案）：开盘试错「仅轻仓 S2 · 你的历史此时段净亏」、主战场「你的历史最大净盈利时段」、午间震荡「你的历史净亏损时段 · 默认观望」、尾盘趋势「你的历史最高单笔均值时段」。标签在市场脉搏条首醒目展示；时钟口径与盘段相同（`america_new_york_clock_v1`，未接假日日历）。
2. **财报临近标记**（`earnings_proximity`，逐候选）：Finnhub 财报日历一次**区间调用**（当日 → +5 天）覆盖整个 universe，按 ET 日期缓存（成功 1 小时 / 失败 10 分钟），从中取每个候选窗口内最近的财报日（`days_to_earnings`，0=今日）。`days_to_earnings ≤ 3`（`EARNINGS_BLACKOUT_DAYS=3`，用户「财报日及临近数日不交易、权利金过贵」规则的 v1 启发式）时前端醒目标注「财报 N 天内 · 期权贵」。日历不可得时 `state=unavailable`、`within_blackout=null`——显式标缺，绝不以「无财报」冒充安全；日历成功但窗口内无财报才是诚实的 `within_blackout=false`。
3. **大盘对齐**（`market_alignment`，逐候选 + 响应级 `market_context`）：用户 setup 显式依赖大盘情绪，因此 SPY 并入 intraday-top 的同一批快照（不新增请求次数），由累计成交额 ÷ 累计成交量近似出 SPY 会话 VWAP 位置；候选**当前爆发方向** vs SPY VWAP 位置得出 `aligned`（顺势：up+above 或 down+below）/ `against`（逆势）/ `unknown`（任一侧 flat/缺失，附 reason）。列「大盘」显示 顺势/逆势/标缺；VWAP 近似口径显式标注（`session_turnover_over_volume`），不是逐笔官方 VWAP。
4. **速度分级**（`session_bursts.speed`，逐候选）：用户纪律 R1「日内只交易加速；2 分钟级速度的波在 1 分钟速度熄火时离场」。本仓库 K 线为 5m 粒度，诚实 v1 只能给出 5m 窗口级近似：相邻两个滚动 15 分钟窗口的爆发分之差（`consecutive_rolling_15m_window_burst_score_delta_5m_bars`）→ `accelerating` / `decelerating` / `flat`；窗口不足或缺分数显式 `unknown`。列「速度」显示 加速/减速/持平/标缺，表格 footer 附「减速=你的离场信号（R1）」——这是用户自己的离场提示，不是系统信号。

前端：扫描表新增「速度」（当前爆发旁）「大盘」（VWAP 旁）「财报」（期权异动前，按距财报天数可排序，标缺行恒排最后）三列，footer 公式行同步扩展并写明设计规则；市场脉搏条首展示时段标签（tooltip 说明其为用户历史统计的硬编码文案）。全部四类信号的口径与「硬编码用户历史提示、不是市场统计、不是信号」限制随响应 `limitations` 固定携带。

诚实合同（响应 `limitations` 固定携带，页头同句展示）：盘中滚动、不冻结、不写快照/qualification/5D/20D 结果（`statistics_track=none_intraday_v1_unscored`）、期权异动不证明方向、盘中排序＝爆发分优先 + 证据计数次之（均为确定性研究度量而非胜率模型，不是买卖信号）。休市时段接口仍可用，但 `quote_session_scope=latest_prior_session`、证据口径标注「最近一个交易时段」、排序退回证据计数。前端自动刷新与 §2.7 相同：60 秒、仅页面可见且盘段为盘前/盘中。

### 2.9 临期合约面板（G-8：0–3 DTE 合约选择支持）

从「选中标的」到「选中合约」之间此前是空白：系统研究 underlying，但不给任何合约级 bid/ask、点差、流动性或逐行权价 IV。临期合约面板（2026-08-02）补的就是这一层，并且**只是合约选择支持，不是推荐引擎**：不给合约打分、不做偏好排序（只有按 expiry / strike / right 的中性排序）、不生成买卖建议；页头固定「合约选择参考 · 不构成推荐 · 以券商实时盘口为准」。

`POST /api/v1/opportunities/near-expiry-contracts` 合同（`schema_version=near-expiry-contracts/1.0`）：

- 请求：`symbol`（单个美股期权 underlying，`US.` 前缀规范化移除，非法 422）、`max_dte`（默认 3、上限 7、含 0DTE）、`refresh`。
- 数据路径复用既有 Moomoo 链机制：1 次 `get_option_expiration_date` + 1 次 `get_option_chain` 日期窗口（`max_dte ≤ 7 < 30` 天恒为单窗口，占 §2.1 记录的 10 次/30 秒链额度中的 1 次）+ 1 次 underlying 快照（含 `update_time` 作 spot as-of）+ 近价窗口合约的 `get_market_snapshot`（典型规模远小于单批 400 上限，即 1 个快照批次）。整个读取走与期权墙相同的独占 QuoteContext lane，不与墙扫描交叉使用同一 SDK context。
- **bid/ask 来源**：`get_market_snapshot` 对期权 code 本就返回 `bid_price` / `ask_price`（单到期 `fetch_chain_via_moomoo` 一直在读；期权墙适配器只是没保留这两个字段）。本面板直接从同一批快照读取，不需要额外的 per-expiry 链调用。
- 近价窗口：`abs(strike/spot−1) ≤ 5%` 与现价上下各最近 8 档行权价的并集（`src/opportunities/near_expiry_contracts.py`，边界含 1e-9 浮点容差）。窗口只约束供应商请求规模，不是价值判断。
- 逐合约字段：`code / right / strike / dte / expiry / bid / ask / mid / spread_percent / last_price / session_volume / open_interest / iv_percent / delta / quote_as_of`。`spread_percent = (ask−bid)/mid`；bid/ask 任一缺失时 mid 与 spread 显式 null + reason（`bid_or_ask_unavailable`），**绝不 0 回填**；交叉盘口（ask < bid）保留 mid、点差标缺（`crossed_quote`）。快照缺行或 `option_valid` 无效的合约保留静态行并逐字段标缺（`quote_state=unavailable` + `snapshot_missing/snapshot_invalid`），逐到期隔离：一个到期日的快照失败不影响另一个到期日（组内 `ready / partial / unavailable`）。
- 时间口径：OI 显式标 T-1 清算口径（`open_interest_as_of` 用 XNYS 上一 session）；volume 为当日累计；IV 为供应商百分数模型值；spot 与逐合约 `quote_as_of` 来自快照自带 `update_time`。与期权墙同一标准合约边界：显式 `NON_STANDARD` 排除、缺 `option_standard_type` 单独计数排除。
- 额度安全：30 秒完成态 TTL + single-flight，key =（symbol, max_dte, Moomoo 启用状态, ET 日期）；`refresh` 只绕过已完成 TTL、仍复用在途请求。窗口内没有到期日（只有周五到期的标的在周末查 0–3 天窗口时会出现）返回诚实 `empty` 状态，不是失败。
- 响应固定携带 limitations：OI 结算口径、点差与流动性随时变化、IV 为供应商模型值、面板不构成合约推荐或买卖建议、执行前以券商实时盘口为准。

前端（`NearExpiryContractPanel`）：

- `/intraday` 日内扫描表交互取「行点击不变 + 每行显式按钮」：整行点击仍是既有的即时扫描详情页导航，行尾「临期合约」按钮在该行下方展开内联面板（同一时刻只展开一行，保持表格可用）。选这个方案而不是改行点击语义，是为了不破坏既有肌肉记忆、也不牺牲表格扫读。
- 面板按到期日分组（`0 DTE / 1 DTE / …` 小节），列：行权价（ATM 行高亮 + 位置标记 badge）/ Call|Put / Bid×Ask（点差%）/ 最新价 / 当日量 / OI（T-1）/ IV / as-of。点差 > 15% 标「流动性差」——这是 v1 启发式展示阈值（`SPREAD_ILLIQUID_THRESHOLD_PERCENT`，未经交易结果验证），只提示点差成本显著；缺 bid/ask 显示「标缺」。
- `/regime/opportunity/:ticker` 详情页「期权墙」标签底部提供「查看临期合约（0–3 DTE）」按钮，按需展开同一面板组件；只有显式点击才发起读取。
- 客户端 30 秒缓存 + 在途去重，与服务端 TTL 对齐，不用长缓存冻结盘中读数。
- 财报临近（2026-08-02）：响应新增 additive `earnings_proximity` 字段（与 §2.8 扫描表候选同形状，复用同一份逐 ET 日 Finnhub 日历缓存，零新增抓取路径）；面板页头在回避窗内（≤3 天）醒目标注「财报 N 天内 · 期权贵 · 你的回避规则」（0 天为「今日财报…」），ready 且窗外不加任何标注，日历不可得时小字「财报日历标缺 · 未知≠安全」——未知绝不冒充安全。

真实验收（2026-08-02 周日休市，TestClient 连本机 OpenD）：MU `max_dte=3` 返回 2026-08-03 / 2026-08-05 两个到期日、64/64 张全部观测报价（MU/NVDA 现有周一/周三/周五到期，周末 3 天窗口并不为空）；NVDA `max_dte=7` 三个到期日 96/96；0 失败批次、0 非标准合约排除。spot as-of 与逐合约报价 as-of 如实显示上一时段（周五 15:59 / 盘后 20:01 ET）、`open_interest_as_of=2026-07-31`。样本行同时覆盖窄点差（NVDA 08-07 ATM Call 3.1%）与将被标「流动性差」的宽点差深度 ITM/OTM 行（NVDA 08-03 P185000 33.3%）。该数值是当次环境证据，不是 SLA。

诚实边界：本面板是快照读数，不是逐笔 NBBO；不提供期限结构、skew、bid/ask size 或可成交滑点；「流动性差」阈值与 ATM 标记都是展示辅助，不是合约质量结论；任何字段都不进入候选排名或统计。

### 2.10 形态相似度 styleMatch v1（G-10：你的 setup 形状标注）

用户的核心问题是「我的交易能根据你的推荐来操作吗」。诚实的回答：本系统不做推荐，但可以标注**当前时段的几何形状**与用户自己 Playbook 里三个 setup 的相似度——把「这波像不像我自己的打法」从主观扫盘变成显式标注。2026-08-02 起 `intraday-top` 升 `intraday_session_evidence_v4`，每个候选新增 `setup_match`（纯函数 `src/opportunities/intraday_setups.py`，`style_match_version=style_match_v1`）。设计规则与 v3 相同：**系统标注，用户过滤**——形态标签不参与排序、不隐藏行、不改变研究状态，也不是买卖信号。

检测输入全部复用已有数据（**零新增请求**）：波段爆发通道已取回的同一批 5m K 线（逐标的 60 秒缓存现同时缓存原始 K 线）、G-2 会话快照派生字段（开/最低/现价/参考前收/VWAP 近似）、缺口证据的同一对阈值常量（0.75×ATR / 1.5%，单一真源迁至 `intraday_setups`，`intraday_top` 以原名别名导入）与 v3 大盘上下文的 SPY 会话 VWAP 位置。休市时段按最近一个交易时段的 K 线评估并以 `session_date_et` + `quote_session_scope=latest_prior_session` 如实标注（与波段爆发同口径）。

三个 setup 的 v1 几何规则（每个恰好一个状态 `matched` / `partial` / `not_matched` / `unavailable`，一个候选可同时相似多个，全部状态都暴露；`matched_setups` / `partial_setups` 为顶层摘要）：

| Setup | v1 检测（basis） | matched | partial | 显式不检查（写入 limitations） |
|---|---|---|---|---|
| S1 十五分钟低点抬高突破 | 5m 按 09:30 ET 栅格聚合 15m；swing low=低点严格低于左右邻居；尾部连续抬高的 swing low ≥2 个；结构高点=抬高区间内 15m 最高价 | 低点抬高 + 现价或其后 15m 收盘突破结构高点（证据：低点序列时间+价位、结构高点、突破窗口） | 低点抬高但未突破 | 5m/2m 回踩 8/13 EMA 企稳追进、2 分钟级加速度；<3 根 15m K 线时 unavailable |
| S2 跳空高开托举 | 向上跳空达缺口证据同阈值（≥0.75×ATR 或 ≥1.5%）+ 最低价 > 参考前收（缺口未回补）+ 现价 ≥ 会话 VWAP | 三项全部成立 | 跳空成立但托举只有一半（另一半不成立或标缺，reason 写明是哪一半） | 第一根 5m/2m 下探托举、8/13 EMA 不破、二次确认轻仓/加仓纪律 |
| S3 高开遇阻回落（做空 setup） | 向上跳空同阈值 + 现价跌破开盘价或 VWAP + 大盘走弱（SPY 处于会话 VWAP 下方） | 三项全部成立 | 形态成立但「形态似 S3 但大盘未走弱」（SPY 在 VWAP 上方/持平），或 SPY 状态标缺无法确认 | 阻力位识别、2m/1m 速度降级离场时机 |

Playbook 只读对应：服务端从 journal_v2 Playbook 候选表读取标题以 `S1`/`S2`/`S3` 开头的最新候选（`_load_intraday_playbook_refs`，本地 SQLite 读取 + 5 分钟缓存），把 `candidate_key` + 状态（候选/已晋升）附在对应 setup 上，供徽标 tooltip 显示「对应 Playbook: S1（候选）」。严格只读：无任何写入或晋升逻辑，读取失败只让徽标缺少 Playbook 标注；Playbook 规则依旧不反哺任何评分、排序或提示词（与 C-2 合同一致）。

前端：扫描表「今日波段」后新增「形态」列——matched 实底徽标（S1 低点抬高 / S2 跳空托举 / S3 高开遇阻）、partial 描边徽标加「· 似」后缀，tooltip（title + aria-label）展示原因、证据行（如「低点序列 10:15 745.20 → 10:45 747.80；突破 11:00 收 750.10 > 749.60」）与 Playbook 对应；无相似形态显示「—」，K 线/快照输入不足显示「标缺」。footer 固定附「形态相似度为 v1 几何检测（5m近似），不含你的进场确认帧（2m/1m 回踩8/13EMA），不是信号」。

诚实边界（响应 `limitations` 固定携带）：5m 聚合到 15m 是近似帧，不是用户实际使用的 2m/1m 确认帧；三个 setup 都只检查形状几何，完全不含进场时机、托举细节与离场纪律；形态相似 ≠ 可交易；阈值与几何规则改动必须升 `signal_version`。

### 2.11 watchlist v1 两层扫描（宽层快照 → 异动闸门 → 深度层）

用户的完整 TradingView 清单约 69 档美股（equities/ETF），远超日内扫描原有 ≤20 档单层 universe；把 69 档全部塞进深度管线会同时打爆 5m K 线额度与 60 秒轮询节奏。2026-08 起采用专业扫描器的标准分层：**宽而便宜的快照层 → 异动闸门 → 窄而昂贵的深度层**。

**启用条件（回滚 = 取消设置 `INTRADAY_WATCHLIST`）**：仅当服务端配置了 `INTRADAY_WATCHLIST`（逗号分隔清单，服务端上限 200 档，超出显式截断并标注 `watchlist_truncated`）且客户端 `symbols` 为空时启用；显式 `symbols`（≤20）或未配置清单时行为与既有单层扫描逐字节一致（`universe_scan` 恒为 null，候选无 `scan_tier` / `deep_lane_reason`——由回归测试锁定）。`INTRADAY_DEEP_LANE_MAX`（默认 12，1..20 双端钳制）只约束异动晋升名额。

**宽层（tier-1）**：整个清单 + 当日冻结盘前计划标的 + SPY 并入**每 60 秒周期仅 1 次** Moomoo `get_market_snapshot` 批量快照（官方单次上限 400 个代码；清单有界 200，恒为单请求，无需分片）。宽层只产出快照可得字段：现价 / 当日涨跌%（快照前收口径）/ 当日高低 / 量 / 额 / as-of。宽层行**没有**爆发、形态、速度、异动、财报字段——缺席即缺席，不以 null 占位冒充「已分析」。

**异动闸门 v2（2026-08-04 起，非盘前时段默认，`gate_basis=momentum15m_then_day_change_v2`）**：K 个异动名额拆成两个子额度——**ceil(2K/3) 档按 `|mom15|`（最近 15 分钟动量，「谁现在在动」）降序**，其余名额按 `|当日涨跌幅|`（「谁今天最大」）降序兜底；动量侧先占位再去重，两侧各以成交额次序、代码字典序兜底（确定性）。`mom15 = (last / price_15min_ago − 1)×100`，由宽层每轮批量快照喂养的**进程内**价格滚动历史推导（零新增供应商请求）：取 12–18 分钟回看窗内「最老」样本作基准；窗内无足龄样本（服务重启 / 开盘冷启动 / 样本过期）时该标的 `mom15=None`，只能走当日涨跌子额度。整批皆无 `mom15` 时闸门**显式回退** v1 当日涨跌口径，并在 `universe_scan.gate_warnings` 携带 `momentum_history_warming_up_ranking_by_day_change`——绝不静默假装在按动量排序。动量历史仅常规时段喂养、ET 日期切换即清空（绝不跨日比价）、重启即冷启动。

**v2 校准背景（2026-08-03 首个实盘日）**：当日为普涨跳空日，v1 闸门按 `|当日涨跌幅|` 排序，深度层被 +5%~+11% 的隔夜跳空标的（RBLX/CRWV/TEAM…）长期占满；用户当日唯一认定「真正能交易」的 NVDA（+2.5% 稳步爬升，10:00–10:15 中波段爆发分 5.06）整日没能晋升深度层。结论：隔夜跳空后横盘的标的当日涨跌幅恒定居高，却没有任何盘中动量——「谁今天涨得多」≠「谁现在在动」。v2 把多数名额交给 15 分钟动量（用户的「可交易」定义），保留少数名额给当日涨跌（跳空标的仍可见），两类视角互为补充而非互斥。

**v1 口径（回退与休市路径，`gate_basis=abs_change_percent_then_turnover_v1`）**：按 `|当日涨跌幅|` 主序、成交额次序、代码字典序兜底晋升前 K 档。缺涨跌幅（快照未解析/缺前收）的行不可晋升——闸门绝不以 0 涨跌冒充平静。**计划钉选**：当日已冻结盘前计划的标的始终占深度位、不占 K 名额（不在清单里也会并入同一批快照）；计划标的不重复参与异动排名。**用户钉选（`INTRADAY_PINNED_TICKERS`，2026-08-04 起）**：用户显式钉选的标的与计划钉选同权——始终深扫、不占 K 名额、并入同一批快照、计入当日额度去重集合；`deep_lane_reason.promoted_by=user_pinned`（同一标的兼具计划身份时计划钉选优先标注）；未配置＝无钉选（现状不变）。闸门不是信号：晋升只决定「谁被深度分析」，不代表方向或质量结论。

**今日曾深扫账本（`universe_scan.day_ledger`，display truth，2026-08-04 起）**：今天曾进入深度层、当前被 movers 轮换出去的标的**不得无声消失**（2026-08-03 实盘：ORCL 09:40 记录强波段后被挤出可见深度层，用户回看时波段整段不见）。服务端进程内按 ET 日保留每个曾深扫标的**最后一次深扫**的候选摘要，轮换出后以 `{ticker, last_seen_at, session_bursts_legs（分级波段）, setup_matched_setups, last_change_percent, state:"rotated_out"}` 行返回——as-of 数据、不实时刷新、绝不与当前深度层重复。纯展示缓存：不写数据库，重启即清空，`day_ledger_basis=in_process_since_service_start_resets_on_restart` 如实声明「重启后从当前时刻累计」。

**盘前口径（ET 04:00–09:30 工作日，`gate_basis=premarket_pre_price_change_then_pre_turnover_v1`）**：盘前时段 Moomoo 常规快照字段（现价/涨跌幅/量/额）仍指向**上一常规时段**——若照常按 `change_percent` 排序，深度榜会复现昨天的异动而不是今晨盘前的真实异动。因此盘前闸门与宽层剩余行排序改用 pre_* 字段：`|pre_change_rate|`（盘前价相对上一常规收盘的百分比，可为负）主序、`pre_turnover`（盘前成交额）次序；缺盘前字段的行不可按盘前异动晋升、在宽层恒排最后——语义与常规口径一致，绝不以 0 冒充「平静」。宽层行 additive 携带 `pre_change_percent` / `pre_turnover`（快照缺列＝null，绝不 0 回填）。若整批快照都无盘前字段（如快照权限差异），闸门**显式回退**常规口径并在 `universe_scan.gate_warnings` 携带 `premarket_fields_unavailable_ranking_reflects_prior_session`——如实声明「当前排序反映上一常规时段」，绝不静默假装在按盘前排序。前端：盘前口径下页头与 footer 附「盘前异动排序（盘前价 vs 前收 · 盘前成交额次序）」，仅快照行展示「盘前 ±x%」与盘前成交额、缺盘前字段的行显式「盘前标缺」；回退时页头与 footer 显示警示「盘前字段不可用 · 当前排序反映上一常规时段」。

**深度层（tier-2）**：完全复用 §2.8–§2.10 的既有 v4 管线（会话快照字段直接复用宽层同一批快照，零新增快照请求；5m 波段爆发 / styleMatch / 速度 / 期权异动 / 财报 / 临期合约资格照旧）。深度层名单已由闸门有界（K + 计划钉选），因此候选**全部返回**、不再按 `limit` 二次截断（`include_all_candidates`，否则「已深度分析却无声消失」）；`requested_limit` 仍如实回显。

**K 线额度语义（本设计的正确性支点，实测自 Moomoo 官方 API Limits）**：深度层 5m K 线经 `StockService.get_history_data` → `DataFetcherManager.get_intraday_data` 优先命中 Moomoo `request_history_kline`（失败回退 yfinance）。该接口的配额是 **30 天滚动窗口内的去重标的数**（账户档位 100/300/1000/2000；同一标的 30 天内重复请求不再扣额；同一标的的日线/5m 等不同周期只记 1 个额度）。因此真实约束是「30 天内晋升过的去重标的数」，其上界＝清单长度（69 档清单 < 最低档位 100）；为防单日 movers 高频换血，另设**每 ET 日新晋升去重标的数上限 30**（内部常量 `_INTRADAY_DEEP_DAILY_DISTINCT_CAP`，非环境变量）。触顶后新标的当日只保留宽层快照行，响应显式标注 `day_promotion_cap_reached`，前端给出警示行——绝不静默丢弃。财报日历仍是一次 Finnhub 区间调用覆盖任意大小 universe（逐标的匹配为字典查找）。

**每周期请求预算（两层模式，缓存全冷）**：1 次批量快照（≤清单+计划+SPY ≤ 206 codes，单请求）+ 深度层每档 1 次 5m K 线（60 秒逐标的 TTL，≤4 并发）+ 深度层每档 1 次有界异动页（30 秒 TTL 与 option-events 端点共用）+ 日线派生每档 900 秒记忆 + 财报 1 次/小时。宽层非晋升标的零 K 线、零日线加载。

**响应合同（additive）**：`universe` 仍为 `list[str]`（= 宽层实际扫描的全部标的，类型不变——设计说明：原要求把 mode 等放进 `universe` 字段，但该字段既有类型为列表，改型即破坏合同，故新增 `universe_scan` 块）。`universe_scan`：`mode` / `gate_basis`（v2 动量口径 / v1 回退 / 盘前口径三值）/ `gate_warnings[]`（闸门降级警示：盘前字段不可用、动量历史预热中）/ `watchlist_total` / `watchlist_truncated` / `scanned_total` / `deep_lane_count` / `deep_lane_max` / `deep_lane[]`（含晋升原因与异动名次，深度层名单本身绝不无声截断）/ `plan_always_include` / `user_pinned`（用户钉选生效清单）/ `gated_out_count` / `snapshot_unresolved_symbols`（供应商无返回行的标的，如实点名）/ `day_promotion_cap(_reached)` / `day_ledger[]` + `day_ledger_basis`（今日曾深扫账本，见上）/ `snapshot_only[]`（宽层行，按闸门同口径降序、标缺行恒最后；盘前口径下 additive 携带 `pre_change_percent` / `pre_turnover`）。每个深度候选带 `scan_tier="deep"` + `deep_lane_reason`（计划钉选 / 用户钉选 / 异动 #n）。

**前端（2026-08-04 命名与主次整理）**：/intraday 页顺序为 市场脉搏 → **实时扫描 · 现在谁在动**（主表，副标注「深度层实时排名，下方为今日曾深扫账本」）→ **今日计划跟踪 · 盘前冻结计划走到哪了**（原「盘中跟踪」改名，消除与扫描表的混淆）→ 期权事件流（2026-08-14 起移入 Journal 仓位复盘，侧栏换为盘前期权异常面板，见 §15）。页头计数「全清单 N 檔快照 · 深度分析 K 檔」；v2 口径附「15分动量优先（谁现在在动）· 当日涨跌兜底」，预热回退时页头/footer 显式「动量样本预热中 · 暂按当日涨跌排序」警示 chip；深度行标的格附「计划钉选」/「钉选」/「异动 #n」徽标；主表下方依次为「今日曾深扫 · 波段保留（N 檔）」账本行（ticker + as-of ET + 最后涨跌 + 分级波段 chips + 形态 + 「已轮换出」，标注「不实时刷新 · 重启后从当前时刻累计」，空账本不渲染）与「仅快照 · 未做深度分析（N 檔）」紧凑列表（ticker + 涨跌% + 成交额，快照未解析与日上限触顶各有显式警示行）；footer 固定附「全清单 N 檔快照 · 深度分析前 K 檔（口径随 gate_basis 如实切换）· 其余仅快照」。单层模式渲染完全不变。

**诚实边界（响应 `limitations` 固定携带）**：两层扫描只有晋升标的做深度分析，其余仅快照、深度字段一律缺席；闸门为启发式（v2：15 分钟动量子额度 + 当日涨跌兜底；冷启动显式回退并警示），不是信号，晋升不代表方向或质量结论；今日曾深扫账本为进程内展示缓存（as-of、不刷新、重启清空、无 DB 写入）；K 线额度为 30 天滚动去重标的数配额，日晋升护栏触顶如实标注。周内看板、市场脉搏与既有单层扫描不受任何影响。

### 2.12 个人画像回灌（personal-edge：你的战绩标注，2026-08-04 起）

盘面上第一次出现**用户自己的历史**：`GET /api/v1/journal/v2/personal-edge` 把 Journal **当前默认 episode build**（与其他 journal 读取完全同一套 `_resolve_effective_episode_build` 解析——有激活用激活，无激活回退最新 CSV build；激活新 build 后此端点自动跟随）的已平仓回合做零写描述统计：(a) 按标的 `{n, net, win_rate, fees}`（仅 n≥5，样本不足只报计数）；(b) 持仓时长桶 `<10m/10-30m/30-60m/1-3h/3-6h/6h-1d/>1d` `{n, net, win_rate, avg_win, avg_loss}`；(c) 进场 DTE 桶 `0/1-3/4-7/8-30/>30`（DTE 缺失单独报 `dte_unknown`，不折进 0DTE）；(d) 月度 `{n, net, fees, win_rate}`。响应固定携带 `build_id` + 日期范围（`first_opened_at`/`last_closed_at`）+ `computed_at`（as-of 诚实），服务端进程内缓存约 10 分钟（回合按 build 追加不可变，缓存安全），前端 fetch 层同口径 10 分钟 sessionCache——每会话最多一次网络请求。月度口径按 **ET≈UTC−4 近似**换算 UTC `opened_at` 并以 `month_basis` 如实声明（3 月初 EST 为 UTC−5，边界样本可能偏移 ±1 小时，limitations 原文携带）。

**消费面（标注不过滤——系统标注，用户过滤）**：

- **实时扫描表「你的战绩」列**（深度行 + 今日曾深扫账本行）：该标的的个人 `净盈亏 · 胜率 · 笔数`；净亏损且 n≥20 加警示 tint + tooltip「你的历史亏钱标的 · n 笔 · 净 −$X · 胜率 Y%」；n<5 显式「样本不足」；端点失败或 Journal 未构建显式「标缺」。绝不隐藏行、不改排序、不是信号。
- **临期合约面板 DTE 提示行**：头部下方一行「你的 DTE 战绩：0DTE +$…(x%) · 1-3DTE −$…(x%) · 4-7DTE +$…(x%) · 样本 YYYY-MM→YYYY-MM · 描述非因果」，数值全部来自端点实时重算（绝不硬编码），空档位显式「无样本」；tooltip 原文携带内生性 caveat。面板服务 0–7 DTE 合约选择，这一行在决策瞬间给出用户自己的 DTE 分层真相。
- **Playbook R4 候选**（经既有 `POST /v2/playbook/candidates` 路径显式创建，free-form 证据快照、幂等）：「R4 · 持仓时间纪律（30分钟-3小时是你的盈利区）」，rule_text 内嵌创建时端点返回的持仓时长分层数字 + 进场质量读数（<10 分钟单极低胜率对应「追高进场/速度不足强做」）+ 内生性提醒，定位为数据描述供本人复核，不是建议。

**诚实边界**：持仓时长/DTE 与结果存在内生性（止损单天然短），全部为描述统计、非因果结论、不构成建议（`limitations` 原文携带，前端 tooltip 透出）；开仓中与缺净盈亏的回合只计数不入统计；缺失字段显式标缺，不以 0 冒充。

#### 2.12.1 规模与频率（discipline：每美元回报才是真账，2026-08-04 起）

**为什么加这一块**：对用户自己 **1,554 笔期权回合**（2026-03-04→07-31，Journal build #3，5m K 线取自本人 Moomoo 数据）做全样本取证后，4→7 月盈亏衰减的三个常见嫌疑人**统计上全是平的**：

| 常见解释 | 4 月 → 7 月 | 检验 | 结论 |
| --- | --- | --- | --- |
| 进场几何变差 | 追高比例 52.0% → 56.6% | 月×类别 χ² p=0.105 | 无可检出衰减 |
| 行情跟随变差 | 前瞻 30 分钟 MFE 中位 0.370 → 0.341 | 五个月 Kruskal p=0.337 | 月份彼此不可区分 |
| 止损纪律变差 | 亏损中位 −22.6% → −24.4% 权利金 | — | 稳定 |

真正变化的是**规模、频率与每美元回报**：每美元风险回报塌约 **24 倍**（5.83% → 0.24%，4 月后台阶式下移）；单笔中位风险金额 **2.4 倍**（$4,097 → $9,695），月度权利金流水 $1.81M → $5.89M；日均笔数 **+44%**（16.5 → 23.7）；0DTE 占比 26.7% → 46.7%，单笔中位合约数 15 → 32；盈亏集中到尾部赢家——剔除每月最好/最差各 5 笔后 4 月 +$51,709、5 月 −$23,918、6 月 −$49,517、7 月 −$99,456，单笔中位盈亏 −$385 → −$1,297。**5 月本可发现这件事的指标，正是工作台此前完全不显示的「每美元回报 + 仓位 + 频率」。**

**端点合同（additive，既有字段一字不改）**：`personal-edge` 响应新增 `discipline` 区块，与其余读数同一个默认 build、同一次 10 分钟缓存：

- `discipline.monthly[]`：月度行（月度口径与 §2.12 一致，ET≈UTC−4 近似），字段 `trades_per_day`（笔数 ÷ 有入场的 ET 自然日数）、`median_premium_at_risk` / `total_premium_at_risk`（`|opening_cash_flow|` 的中位与合计）、`pnl_per_dollar_risked`（Σ`realized_pnl_net` ÷ Σ`|opening_cash_flow|`）、`median_episode_pnl`、`body_pnl`（去掉当月最好/最差各 5 笔后的合计，仅 n≥15 成立）、`zero_dte_share`（分母为已知 DTE 的回合数）、`exact_fill_share`（`has_exact_fill_times` 均值）。
- `discipline.current_window`：**build 内实际存在的最后 20 个交易日**同口径读数 + `start_date`/`end_date`，让用户看到「现在」相对自己历史的位置。

**消费面**：`/intraday` 脉搏条下方一行**规模与频率条**（`IntradayDisciplineStrip`）：「每美元回报 / 日均笔数 / 中位仓位 / 本体盈亏」四个 chip 取当前窗口值，注脚是**端点返回的最早一个可得月份**同口径基线（如「4月 +4.85%」，前端不硬编码任何数字），行尾恒显示「基于已发布证据 build #N · 截至 YYYY-MM-DD」。中性排版、无涨跌色、无建议措辞——**镜子，不是警报**；不隐藏任何数据、不进入排序或信号。Journal 页「模式观察」下方新增同层兄弟区块**规模与频率**（`DisciplineMonthlyPanel`）：月度表 + 当前窗口脚注，`exact_fill_share<1` 的月份行内显式标注「成交明细为重建，时点仅供参考」。

**Playbook R6 候选**（经既有 `POST /v2/playbook/candidates` 路径显式创建，free-form 证据快照、幂等）：「R6 · 规模与频率纪律（每美元回报才是真账）」，`rule_text` 内嵌上述全部取证数字（24 倍每美元衰减 + 三个平的对照、仓位 2.4 倍、频率 +44%、本体/尾部拆解）、重建成交明细的 caveat，以及工作台按自身口径重算 build #3 的对照值，定位为数据描述供本人复核，不是建议。

**诚实边界**：4 月的漂亮数字主要落在 **4/1–4/24，那段成交明细是重建的**（`has_exact_fill_times=0`）；只看精确成交回合，每美元回报为 4 月 0.72% / 5 月 0.95% / 6 月 0.16% / 7 月 0.24%——因此 `exact_fill_share` 逐行下发，<1 的月份 UI 必须标注。所有派生比率 **fail-closed**：分母为 0、无可用 `opening_cash_flow`、或样本 <15 笔（本体盈亏）时返回 `null` + `*_reason`，前端显示「标缺」并把原因挂在 tooltip/title 上，绝不以 0 或截断样本冒充。口径提示：本区块的样本 = 该 build 全部**已平仓且有净盈亏**的回合（含少量正股回合），与上述只取期权回合的研究样本不同，绝对值会有差异、方向一致。

### 2.13 近 30 分钟位移（recent displacement：它「已经」在不在动，2026-08-04 起）

**为什么加这一列**：对用户自己的 766 笔期权回合（样本窗口 2026-06-08→2026-07-31，Journal build #3）做取证分析后得到一个反直觉的结论——**进场几何（追高 vs 回调）对结果没有预测力**，而**进场之后的位移**把结果分得很开：

| 分组 | 进场后 30 分钟前向 MFE 中位 | MAE 中位 | 达到 ≥0.5 ATR 有利位移 |
| --- | --- | --- | --- |
| 速死亏损单（持仓 <30 分钟） | 0.18 ATR | −0.49 ATR | **18.3%** |
| 走出来的赢家（持仓 30 分钟–3 小时） | 0.69 ATR | −0.13 ATR | **71.2%** |

该样本 **84% 的合约 ≤1DTE**：时间价值对「不动」零容忍，进场后 30 分钟还不动的仓位在结构上已经死了。因此工作台按标的直接回答「它**现在**到底有没有在动」，并且用与那份取证分析**同一把 ATR 标尺**度量，把 **0.5 ATR** 画成用户自己的经验「活下来」线。

**口径**（`src/opportunities/intraday_bursts.py` 的 `compute_recent_displacement`，纯函数、零新增请求——复用波段爆发通道已取回的同一批 5m K 线）：取当前时段（休市取最近一个交易时段，沿用既有 as-of 标注）最后 `DISPLACEMENT_WINDOW_BARS = 6` 根常规时段 5m K 线，以**窗口首根 K 线开盘价**作为「30 分钟前的价格」参考点：

- `high_excursion_atr = (窗口最高价 − 30 分钟前价格) / atr_unit`
- `low_excursion_atr = (窗口最低价 − 30 分钟前价格) / atr_unit`
- `net_move_atr = (窗口末收盘 − 30 分钟前价格) / atr_unit`
- `abs_range_atr = (窗口最高价 − 窗口最低价) / atr_unit`

`atr_unit` 优先取共享日线加载器已注入的 **ATR14**（`atr_basis = atr14_daily`，与取证分析同源）；缺失或非正时回退取证分析用过的**盘中代理**（最近 20 根 5m K 线波幅均值 × 3，`atr_basis = intraday_20bar_proxy_x3`）并显式标注用了哪一种；两者都不可得时 `state = unavailable` + 原因。K 线不足 6 根（时段起点）时 `state = insufficient_bars` 并带上实际 `bar_count`——**绝不 0 回填冒充「没动」**。

> **v7（2026-08-04）修正**：上述**回退层**已改动——盘中代理被降为最后兜底，中间插入「上一批交易时段真实波幅均值」，并新增 `atr_scale_comparability` 标签；日线 ATR14 主路径数值口径**逐字未变**。原因与新口径见下方 §2.14。

**响应合同**（additive，`signal_version` 升到 `intraday_session_evidence_v6`）：候选行新增 `recent_displacement`（`IntradayRecentDisplacement`：`state / window_minutes / net_move_atr / high_excursion_atr / low_excursion_atr / abs_range_atr / atr_basis / survival_line_atr / bar_count / unavailable_reason`）。它是**标注**：不进 `supporting_evidence_count`、不参与排序、不隐藏行、不产生 evidence 条目。

**前端**：实时扫描表默认网格新增第 7 列「近30分位移」（紧跟「速度」，交易关键顺序＝涨跌/爆发/波段/速度/位移/形态）；为保持默认网格 10 列，「波段vs大盘」下沉到展开行「研究读数」区（重排可见性 ≠ 删除，口径与 tooltip 原样保留）。单元格：带符号 ATR 净位移（如 `+0.72 ATR`）；`≥ +0.5` 或 `≤ −0.5` 用涨跌色强调并给出区间「高 +0.91 ATR · 低 −0.14 ATR」；介于 ±0.5 之间用弱化色 +「未达 0.5」；不足/不可得显式「标缺」。tooltip 原文：「近 30 分钟（6×5m）净位移 ÷ ATR。0.5 ATR 是你自己 766 笔样本里「活下来」的经验线（描述统计，非预测、非信号）；基准：ATR14 日线 / 盘中代理」并附**实际使用的基准**与区间读数。

> **v8（2026-08-04）更正**：上一段的弱化态文案与 tooltip 原文**已作废**——`未达 0.5` 改为 `不足 0.5 ATR`，tooltip 改为以循环性更正开头，不再出现「活下来」的存活框架。现行文案见下方 v8 小节。

**Playbook R5 候选**（经既有 `POST /v2/playbook/candidates` 路径显式创建，free-form 证据快照、幂等）：「R5 · 进场要求「已经在动」（近30分钟位移 ≥0.5 ATR）」，`rule_text` 内嵌上表全部取证数字（18.3% vs 71.2%、MFE/MAE 中位、84% ≤1DTE 零容忍、进场几何无预测力）与全部 caveat，定位为数据描述供本人复核，不是建议。

**诚实边界**：这是对**过去 30 分钟已经发生的事**的描述统计——不是预测、不是买卖信号；0.5 ATR 来自用户自己**最差两个月**的回溯样本，且分组本身按持仓时长定义（止损单天然短、赢家天然长，与结果存在循环性，不是因果证明）；K 线为非官方 5m 聚合；ATR 基准逐行如实标注，缺失显式标缺。

#### v8（2026-08-04）循环性更正：那道鸿沟几乎全是循环性

上面「18.3% vs 71.2%」那张表的分组变量是**持仓时长**，而持仓时长本身由结果决定——**这个分层按构造就是循环的**（旧文案只写了一句「与结果存在循环性」的脚注，等于承认了却没量化，读者仍会把 0.5 读成一条「越过就活」的门槛）。2026-08 的 2m 回放研究用**同一套机器**把这份循环性量化了出来（3,492 次 EMA8/13 回踩持稳进场，21 标的 × 123 个交易时段，2026-02-05→08-03）：

| 记分方式 | 速度未死 | 速度已死 | 差距 |
| --- | --- | --- | --- |
| **循环记分**（用整段 30 分钟窗口内速度是否存活，去判**这同一段**窗口的结果） | 均值 +0.071 / P(>0) **54.4%** | −0.476 / **16.8%** | **37.6 个百分点** |
| **诚实记分**（只从第 15 分钟检查点**向前**计分，即进场当时真能知道的信息） | +0.005 / **49.9%** | −0.013 / **49.8%** | **0.1 个百分点** |

而且样本内那点微弱的检查点效应**样本外直接反号**：样本内「第 5 分钟已走 ≥0.3 ATR」→ 其后 +0.122；样本外同一条件 → **−0.128**。

**结论**：肉眼可见的那道鸿沟**几乎全部是循环性**。位移读数本身仍然是**诚实的描述**（用户要的就是「它现在到底有没有在动」），所以**这一列保留、数字不删**；不诚实的是让 0.5 这条线暗示「越过它就能活下来」。因此 v8 **删掉全部存活框架**：

- 弱化态文案：`未达 0.5` → **`不足 0.5 ATR`**（「未达」隐含「本该达到」）；
- 列头 tooltip 与逐行 tooltip **以这条更正开头**（`【更正】…那个分层按构造就是循环的…37.6 个百分点塌到 0.1 个百分点（49.9% vs 49.8%，n=3,492）…这一列只描述过去 30 分钟已经发生的事，不含任何前向信息，0.5 只是参考刻度、不是「越过就能活下来」`）；
- 完整口径文本在取证数字**紧后**接上整段更正（含循环/诚实两种记分的全部数字与样本外反号）；
- 默认简短口径行同步改写；后端 `DISPLACEMENT_LIMITATION_LINE`、`IntradayRecentDisplacement` docstring、模块常量注释同步。

同一条更正也进入 **Playbook 候选 R8**（见 §2.15）——R4/R5 的 `rule_text` 是 append-only 冻结文本，**不可改写**，因此更正以**新候选**形式追加，而不是修改历史记录。

#### v8（2026-08-04）K 线时间戳口径修正：Moomoo 按 K 线**结束**时间打标

**发现**：`src/opportunities/intraday_bursts.py` 全模块按「时间戳 = 这根 K 线的**开始**时间」计算（`filter_regular_session_bars` 取 `09:30 ≤ start < 16:00`，`_window_end_label` 用「末根 start + 5 分钟」推末端标签），yfinance 正是这个口径；但 **Moomoo `request_history_kline` 的 `time_key` 是这根 K 线的结束时间**。

**独立复核**（2026-08-04，只读 OpenD，NVDA/AAPL/MU/AMZN/GOOGL，2026-07-27→08-03，三条互相独立的证据）：

1. **官方日线对齐（决定性）**：日线开盘价 == 标签 `09:35` 那根的开盘价，日线收盘价 == 标签 `16:00` 那根的收盘价，**25/25 个标的-交易日逐分钱吻合**；按开始时间解释（09:30 / 15:55）则 **0/25** 吻合。
2. **成交量剖面**：标签 `09:35` 中位量是标签 `09:30` 的 **15–25 倍**（开盘集合竞价落在 09:35），标签 `16:00` 是全天最大（收盘集合竞价），标签 `09:30` 只有几十万股——典型盘前量。
3. **延伸时段端点**：首/末标签是 `04:05` 与 `20:00`，而非 `04:00` 与 `19:55`。

**后果**：`09:30 ≤ 标签 < 16:00` 在结束时间口径下实际取的是 **09:25–15:55**——**混进一根盘前 K 线、又丢掉收盘集合竞价那根**。

**修正方式**：新增 `normalize_bar_label_convention(bars, source=...)`，只对已知按结束时间打标的源（`BAR_LABEL_END_SOURCES = {"MoomooFetcher"}`）把时间戳整体前移一个 K 线周期；**未知/缺失 source 一律视为已是 start 口径，不猜测**。调用点是 `api/v1/endpoints/opportunities.py::_fetch_intraday_5m_bars`——盘中 5m K 线进入研究管线（波段爆发 / 近 30 分位移 / v4 形态对比）的**唯一入口**，因此三个消费者一次性修正，而 `intraday_bursts` 保持纯函数、内部不为供应商分叉。**没有改动 `/stocks/{code}/history` 的公开载荷**（K 线图口径不在本次范围内）。

**这是数值口径变更**，`INTRADAY_TOP_SIGNAL_VERSION` 因此从 `v7` 升到 **`v8`**：所有窗口、中位数、波段标签、速度与位移读数整体前移一根 K 线并换掉两侧边界样本，历史截图与本版**不可逐值对照**。真实端点抽样（TestClient，2026-08-03 时段，日线 ATR14 标尺）：

| 标的 | 当前窗口分（前 → 后） | 近30分位移 net（前 → 后） |
| --- | --- | --- |
| NVDA | 3.49 → **10.02** | −0.228 → **−0.155** |
| MU | 0.80 → **4.04**（方向 up → down） | +0.111 → **+0.032** |
| AMZN | 0.80 → **9.44**（方向 up → down） | +0.074 → **−0.062**（**变号**） |

**校准重跑**（2026-07-31 用户标注样本，fixture 已重采为供应商原始时间戳并放宽到 09:00–16:30，因此现在真正在测这条链路）：**阈值无需重新校准**——MU 跳水 09:45→09:40（35.51→35.44，−5.18% @4.61×，与用户标注的「−5.2% / 4.6×」反而更贴）、NVDA 两波 09:40→09:35 与 15:15→15:10、GOOGL 反例仍远弱于 MU 跳水。唯一变化的是 **AMZN 开盘波**：修正前那个标签 09:30 的窗口真实覆盖 09:25–09:40（含一根盘前 K 线、且恰好收在日内尖顶），score **18.72**；修正后真正的 09:30–09:45 窗口 score **8.56**，**仍 ≥ 强波段阈值 8.0**，只是被 15 分钟后分更高的窗口（11.05）按 30 分钟去重规则挤出 legs。测试因此改为断言「开盘窗口仍是强 up 窗口」，避免把一个**选择规则**的结果误记成**检测能力**的丧失。

**新增的口径性质（如实声明、不改阈值）**：修正后最后一个 15 分钟窗口（15:45–16:00）真正含收盘集合竞价那根，而它的成交量通常是当日中位 K 线的 **5–20 倍**，量比因此被机械放大——几乎每个标的每天都会在 15:45 出现一个高分窗口。这是**口径产物**，只说明收盘竞价本身是全天最大的成交事件，不代表尾盘更值得交易。

### 2.15 Playbook 候选 R8：进场择时没有边际（2026-08-04）

经既有 `POST /api/v1/journal/v2/playbook/candidates` 路径显式创建（free-form 证据快照、幂等），**候选 id=11**，与 R5(id=8) / R6(id=9) / R7(id=10) 同形。标题：**「R8 · 进场择时没有边际（两个时间框架、13,136 次事件）」**。

样本：两个时间框架合计 **13,136 次冲量事件**（2m 起点 7,126 + 生产 5m 起点 6,010），21 个标的 × 123 个交易时段（2026-02-05→08-03），逐根重放、无未来函数；主口径＝2m 起点、回踩 8/13 EMA 持稳进场、30 分钟持有（**n=3,492**）。`rule_text` 内嵌：

1. **进场择时没有边际**：回踩持稳 P(方向)=**49.9%** vs 时间匹配随机对照 **49.8%**（n=10,476），MFE/|MAE|=**0.994**；symbol-day 分块自助 95% CI [48.2, 51.5] 含 50%。
2. **用户自己写下的 S1 形状反而更差**：`hold_8`（浅回踩、不破 EMA8，正是 S1 描述的形状）=**47.6%**，**比随机还低**；唯一为正的桶 `hold_13`（先跌破 EMA8 再收回）=**55.6%**（17/20 标的、5/6 个月方向一致），但它恰好是 S1 的**反面**，且只值约 **6.4 bps**（中位；均值约 8.0 bps）正股位移——**小于 0-3DTE 合约的来回价差**。
3. **「等一个回调」不是过滤器**：**99.97%** 的冲量在 40 分钟内都会回到 2m EMA8 区域（7,126 次里只有 2 次没回）。
4. **离场测试**：1 分钟减速离场**不改善均值**（−0.008 vs 持有到期 +0.005），但把标准差砍掉 **38%**（0.753→0.463）——**R1 的真实价值是波动与时间价值成本管理，不是预测**。
5. **循环性更正**（同上 §2.13 v8）：37.6pp → 0.1pp，样本外反号。

边界：数据描述供本人复核，不构成建议；样本仅 6 个月单一波动率环境；全部以**正股位移**度量，**不是期权 P&L**（未计价差、滑点、theta、IV 变化）；K 线为非官方聚合；约 200 次比较下 Bonferroni 无一存活，保留的结论按方向一致性与样本外稳定性判断、不是按 p 值。

> `rule_text` 是 append-only 冻结文本（DB 触发器 + 无 PATCH 路由 + `candidate_key` 内容哈希三重锁），因此本次更正一律**新增候选**，不改写 R4/R5。前端 `PlaybookPanel` 同步补 `whitespace-pre-line`——R5–R8 这类多段取证记录此前会塌成一坨（只改渲染，不动数据）。

### 2.14 哑火形态与 ATR 标尺可比性修正（2026-08-04 起）

#### 首要结论是否定的：起速那一刻分不出方向

对**用户自己的 Moomoo 5m K 线**做了一次全样本回放研究：**9,173 次爆发起点**，21 个标的 × 123 个交易时段（2026-02-05→2026-08-03），逐根 K 线重放、**无未来函数**，窗口口径直接复用本仓库 `src/opportunities/intraday_bursts` 的同一套数学。结论先说最重要的那条——

| 问题 | 实测 |
| --- | --- |
| 30 分钟后净位移仍在爆发方向的概率 | **50.6%**（n=9,173） |
| 19 个候选判别因子对「方向」的 AUC | **全部落在 0.48–0.52** |
| 全样本 MFE / \|MAE\| 中位数 | **1.026**（有利与不利**同幅**放大） |

也就是说：**看起来像「延续概率」的东西，实质是波动率读数穿了方向的外衣**——它能告诉你「接下来会不会动得厉害」，但对「往哪边动」一无所知。

**因此本版明确不做**（记录在案，避免重复尝试）：

- ❌ **不做**任何「延续概率」「真/假速度」评分或方向判断；
- ❌ **不做**研究另行提出的「起速幅度分档」列——它只描述波动幅度、不含方向信息（同一档内 P(方向) ≈ 53%），而表格已经过密（用户已明确抱怨列太多）。**刻意不加列**；
- ❌ **不改**生产阈值 `LEG_MEDIUM_MIN_SCORE = 2.5`（见下方「阈值的已知性质」）；
- ❌ **不再重试**「突破前 15 分钟区间」假设：方向 AUC **0.496**，且在三把标尺下同样无效——**已记录为证伪**。

经**时间有序**的样本外验证（Feb–May 拟合、Jun–Aug 检验）活下来的只有两件事，本版只落这两件。

#### 落地一：「哑火形态」标记（唯一稳的回避型发现）

**规则**（`compute_fizzle_flag`，纯函数）——当前 15 分钟窗口同时满足：

| 条件 | 含义 |
| --- | --- |
| `stratum == "intraday"` | 复用既有 `median_basis` 字段：`prior_session_fallback` → `open`，否则 `intraday`（**不另立时钟规则**） |
| `efficiency ≥ 0.9` | \|窗口末收 − 窗口首开\| ÷ (窗口最高 − 窗口最低)，即**几乎没有回撤的单向推升**；高低相等 → 效率未定义 → **标缺**，绝不判为命中 |
| `vol_norm < 2.0` | 既有窗口量比字段，即**量能平平** |

**证据**（fresh onsets、时间有序切分）：

| | 命中该形态 | 同期基准 |
| --- | --- | --- |
| 样本内 Feb–May | **26.8%**（n=291） | 47.8% |
| 样本外 Jun–Aug | **25.4%**（n=177） | 50.5% |

（「命中率」＝30 分钟内达到 **≥0.5 ATR 有利位移**的比例。）方向一致性：**20/20 个标的**、**6/6 个月**；且在三把标尺（生产盘中代理、重建日线 ATR14、原始百分比）下**同号**。

**读法**：*一段几乎不回撤、量能却平平的干净盘中推升——最像「真速度」的形态，恰恰是这份样本里最常哑火的形态。*

**响应合同**（additive，`signal_version` 升到 `intraday_session_evidence_v7`）：候选行新增 `fizzle_flag`（`IntradayFizzleFlag`：`state`(flagged/not_flagged/unavailable) / `efficiency` / `vol_norm` / `stratum` / `reason` / `basis` / `reference`），`session_bursts.fizzle_flag` 是同一读数的单一真源。`reference` 把上表数字（样本、样本内外命中率与基准、n）**随行下发**，前端不硬编码任何数字。它是**标注**：不进 `supporting_evidence_count`、不参与排序、不隐藏行、不产生 evidence 条目。

**前端**：**不新增列**。命中时在既有「当前爆发」单元格内、爆发分下方渲染一枚 warning-muted 小徽标「哑火形态」，tooltip 给出口径、样本内外频率与基准、样本描述，以及固定的诚实边界原文：「这是形态描述与历史频率，不是卖出信号；方向本身在样本中约 53%，与掷硬币无实质差别。」`not_flagged` **什么都不渲染**（不制造视觉噪音）；`unavailable` 同样**什么都不渲染**——**缺席不是结论**。

#### 落地二：ATR 标尺可比性修正

同一研究量化了既有回退代理的标定缺陷：`intraday_20bar_proxy_x3`（最近 20 根 5m 波幅均值 ×3）与真实日线 ATR14 之比在盘中**从 0.28（开盘）→ 0.42（10:30–11:00）→ 0.20（14:00–15:30）漂移**——它是一把**随时点伸缩的尺子**。换算成同口径比较时，10:30–11:00 的读数被**低估约 18–20pp**、15:00–15:30 被**高估约 10–15pp**（以连续率表示）。后果有两层：同一标的跨时点不可比；走代理的行与走 ATR14 的行更不可比，而 `0.5 ATR` 这条经验线对两者是**同一条线**。

**修正**（least invasive，两手都做）：

1. **改回退顺序**：日线 ATR14 → **上一批交易时段真实波幅均值**（`prior_sessions_true_range_mean`：最多 3 个已结束时段，每个时段 TR = `max(高−低, |高−前收|, |低−前收|)`，能拿到再前一个时段收盘时才含跳空项）→ 旧盘中代理**仅在连一个上一时段都没有时兜底**。新回退层由**已经结束的**时段算出，**当日内恒定**，因此结构上不可能制造时点效应；数据来自同一批已取回的 5m K 线，**零新增请求**。
2. **显式标注可比性**：新增 `atr_scale_comparability` = `daily_atr14`（可比）/ `daily_scale_prior_sessions_approximate`（日线量级，但仅 ≤3 个时段、噪声大于 ATR14）/ `intraday_scale_not_comparable`（盘中量级，**不可**与 ATR14 行或跨时点比较），配套 `atr_prior_session_count`。UI 的位移 tooltip 逐行照实说出这句话。

**日线 ATR14 主路径（生产上有 ATR14 的标的走的正是这条）数值口径逐字未变**——测试直接断言「有/无上一时段 K 线时该路径的每个数值完全相同」。字段语义变化随 `signal_version` v7 一起声明，并写入 limitations。

#### 阈值的已知性质（如实声明，不改动）

同一份回放实测：`LEG_MEDIUM_MIN_SCORE = 2.5` 平均每个「有爆发的标的-交易日」触发 **3.74 个窗口**，其中 **62.8%** 落在**开盘 stratum**（当日不足 6 根 K 线、中位数基准回退上一时段的时点，约 09:55 前）——因为回退基准让开盘 K 线天然显得超常。**本版不改阈值**，只把这条性质写进 `session_bursts.limitations`：早段爆发占多数是**口径产物**，不代表早段更值得交易。

**是否值得后续跟进**：值得，但**不急**，且不应以「调阈值」的形式做。真正该做的是让开盘段与盘中段各自可比（例如开盘段用上一时段同时段中位数而非全时段中位数做分母），那是一次口径变更 + 重新校准 + 升版本的独立任务，需要先把「用户是否希望早段少报」这个产品判断问清楚；在此之前，如实声明比悄悄改阈值诚实。

**Playbook R7 候选**（经既有 `POST /v2/playbook/candidates` 路径显式创建，free-form 证据快照、幂等）：「R7 · 起速那一刻分不出方向（哑火形态是唯一稳的回避）」，`rule_text` 内嵌方向 AUC 0.48–0.52、P(方向) 50.6%、MFE/\|MAE\| 1.026、哑火规则与其样本内外频率、**已证伪**的「突破前 15 分钟区间」假设（AUC 0.496，三把标尺一致），以及「约 200 次比较，Bonferroni 下无一存活；按方向一致性与样本外稳定性判断」。定位为**数据描述供本人复核，不构成建议**。

**诚实边界**：哑火形态是**形态描述 + 历史频率**，不是卖出信号、不是方向判断；样本是用户自己 21 个标的、6 个月的 5m K 线（非官方聚合），「有利位移」按 ATR 归一化定义，命中率随标尺换算而变、但方向在三把标尺下一致；约 200 次比较下 **Bonferroni 无一存活**，该规则是按**方向一致性与样本外稳定性**保留的，不是按 p 值；`unavailable` 一律不渲染，**缺席不是「没命中」的结论**。

## 3. 数据语义修正

Moomoo 官方明确说明 [`get_option_chain`](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html) 只返回静态合约资料。动态 bid/ask、成交量、OI、IV 和 Greeks 必须用合约 code 再调用 [`get_market_snapshot`](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html)。当前适配器已改为分批（每批最多 400 个 code）合并快照；严格检查 `option_valid` 和有限数，缺任一必要动态字段就省略该合约，不再把静态行或缺失值伪装成全 0 实时行情。最近到期 ATM Call IV 仍先用静态链与 spot 锁定单一合约再读取快照；它不是 IV Rank/Percentile，也不代表异常期权大单或买卖方向。

Python SDK `10.9.6908` 的只读 Quote Context 已接入 [`get_option_underlying_overview`](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-underlying-overview.html)。该接口直接返回 underlying 维度的 Call/Put Volume、T-1 OI、IV、IV Rank、IV Percentile、前值 IV，以及多个窗口的 HV/Percentile；因此机会页现在显示的 `IV Rank` 是供应商字段，不再使用 `hv_fallback` 冒充。它仍只是供应商统计快照：页面保留来源、抓取时间、Volume session date 和 OI as-of，不把 `IV Rank` 高低直接翻译成买入或卖出期权。

所有 QuoteContext 均先走 TCP 探测和异步 READY 截止，并设置 SDK 公开的 5 秒同步查询连接等待上限；连接在查询前断开时不会进入无限重连。页面仍保留 30 秒独立超时和失败降级。当前切片不显示单合约 volume/OI 为“大单”：volume 是当日累计，OI 的更新时间和 bid/ask 的时效还需要显式 provenance 后才能作为流动性上下文展示。

当前 OpenD `10.9.6918` 与 Python SDK `10.9.6908` 已完成 [`get_option_event`](https://openapi.moomoo.com/moomoo-api-doc/hk/quote/get-option-event.html) 只读连通验收，可返回成交权利金、成交时盘口、IV/Delta、成交/策略分类等字段。官方没有给出适用于当前账户和行情权限的统一端到端延迟 SLA，因此接口保留源 `fill_time` 与本地 `fetched_at`，不能仅凭“接口成功”把数据称为无延迟实时信号。当前验收只证明数据可读取，不证明标签正确率或交易 edge。

现有 `src/options/iv_rank.py` 的 `hv_fallback` 仍只是“当前 IV 相对历史已实现波动率”的代理，不是真实同口径 IV Rank；只有 Moomoo underlying overview 成功返回的 `iv_rank` 字段才在机会页标为 `IV Rank`，overview 不可用时必须留空或明确降级。

## 4. 暗池与场外成交边界

[FINRA OTC Transparency](https://www.finra.org/filing-reporting/otc-transparency) 是延迟发布的周/月度汇总。Tier 1 NMS 初次发布约延迟两周，Tier 2/OTC 约四周，所以它只能作为慢速背景，不能支持“今天出现暗池买入”。

当日能力应称为“场外/TRF 大额成交”，并从含 exchange、condition、TRF id、participant/SIP timestamp 和 correction 的逐笔供应商读取。单笔 TRF print 也不能证明具体 ATS、主动方向或机构观点。可选供应商阶段优先评估 [Massive 股票逐笔](https://massive.com/docs/rest/stocks/trades-quotes/trades)；如果 Moomoo v10.9 异常期权事件不能满足账户权限或稳定性，再评估 [Intrinio unusual options activity](https://data.intrinio.com/documentation/web_api/get_unusual_activity_v2)。任何付费源都保持可插拔，未配置不影响基础候选榜。

## 5. 不可变机会快照与结果学习

### 5.1 写入边界与 API

一次普通扫描生成内存中的 `OpportunityRun`，每个候选带稳定 `candidate_id`。`POST /api/v1/opportunities/daily` 本身仍是零写入预览；当前 Web 先读 canonical status，在允许窗口触发受控 run，成功时使用新 scope 冻结结果，其他状态才显示普通 `/daily` 预览。旧 v1 `snapshots/ensure` API 与历史快照继续保留兼容和结果审计，但当前机会页不再主动调用。

当前持久化使用与 Journal 隔离的三张表：

- `opportunity_snapshot_runs`：冻结 market date、universe、limit、signal/schema/freeze/playbook 版本、排序方法、as-of、完整 payload 与内容哈希；
- `opportunity_snapshot_candidates`：冻结名次、方向上下文、setup/evidence/readiness、上一完整交易日参考价与数据来源；
- `opportunity_candidate_outcomes`：只追加已经到达目标 XNYS 收盘的 5/20 交易日标的结果、SPY 对照、输入 bar 哈希与 provenance。

三张表都安装 SQLite `UPDATE / DELETE` 拒绝 trigger；run 与其 candidates 在同一事务写入。结果以“candidate + horizon + evaluator version + complete/partial state”为不可变槽位：完全相同重试幂等，冲突内容拒绝；缺部分路径或 SPY 附件但已有目标收盘时可以先追加 `partial`，后续完整来源只能追加新的 `complete` 行，不能原地修订。

Qualification 另使用两张同样与 Journal 隔离的 append-only 表：

- `opportunity_snapshot_qualification_assessments`：按 `snapshot + qualification policy` 冻结发布证明、analysis quality、原因、事实哈希与 assessment 哈希；
- `opportunity_candidate_track_assessments`：为快照中的每个候选恰好追加“标的路径 / 日线选股 / 完整研究”三条决策，分别保存 causal window、observation、`qualified / excluded / unverified`、原因与事实哈希。

两表同样拒绝 `UPDATE / DELETE`。同一 policy、同一内容重试幂等，同一槽位出现不同内容时拒绝冲突；新 policy 只能追加新 assessment，不能把旧判断原地改名。Canonical 发布通过 terminal cycle event、attempt、snapshot key 与冻结时间形成 publication proof，qualification 写入失败会令 snapshot 与发布终态一起回滚。历史快照的 qualification 回填必须先生成不写库的确定性计划，再显式 apply；它不会重新抓行情，也不会重写旧 snapshot、candidate 或 outcome。

保存、只读与评估接口如下：

- `POST /api/v1/opportunities/snapshots/ensure`：保留的旧 v1 兼容入口；在旧因果窗口幂等保存一次，其他时间只返回状态，永远不解锁或调用交易接口；
- `POST /api/v1/opportunities/snapshots/freeze`：保留给显式受控流程的低层入口；它创建的是审计快照，是否具备结果学习资格仍由 XNYS 因果窗口判定，不能用来盘后补造正式样本；
- `GET /api/v1/opportunities/snapshots?limit=...`：只读列出冻结时间、qualification 三轨计数、旧聚合统计数，以及 5/20 日“已回填 / 等待目标日 / 已到目标日但缺行情”进度；`underlying_path_candidate_count / underlying_path_progress` 以加法字段单列 v2 标的路径审计范围，`full_research_candidate_count / full_research_progress` 单列严格完整研究统计范围；
- `POST /api/v1/opportunities/snapshots/{snapshot_key}/evaluate`：只为已经到达目标交易日的窗口追加结果；尚未到目标日、日历异常或关键数据缺失时不写伪结果；
- `GET /api/v1/opportunities/learning-summary`：只读返回分 cohort 的描述统计、自动维护状态和门槛状态，固定 `auto_adjustment=false`。

Snapshot 读取合同以向后兼容方式保留 `validation_eligible / eligibility_reasons`，并追加可选 `analysis_quality_eligible / analysis_quality_reasons` 与 `qualification`。`qualification` 返回 assessment identity、policy、发布状态、analysis quality 和三轨汇总；前端把 snake_case 深层映射为 camelCase。严格 5D/20D 面板只读取 `full_research_progress`，若 raw-path 已开始回填但完整研究尚未入样，则另行显示“标的路径审计”，不得把它混成完整研究命中率。旧快照或旧服务响应可能省略新字段，消费者必须回退旧聚合 UI 或显示“历史质量口径未知”，不能把缺字段默认为 `true`。旧 boolean 只作为兼容字段，不再被描述为 publication、analysis quality、causal window 与 downstream analysis 四个问题的唯一答案。

旧 v1 历史版本与当前 canonical foundation run 都只冻结确定性基础榜；页面异步取得的 underlying overview、ATM IV、期权墙和异常成交尚未进入任一冻结 payload，也不会被事后拼接到旧快照。`run_type` 仍固定 `morning_prior_close`；未来 `open_refresh / intraday_refresh` 必须使用新合同，不能把上一完整交易日 OHLCV、T-1 OI 或延迟数据显示成盘中刚发生的信号。

### 5.2 三轨 Qualification 与独立证据轴

`opportunity_qualification_v2` 将原先混在 `validation_eligible` 内的事实拆开：

- snapshot 级 `publication_state`：`canonical_published / audit_frozen / legacy_unverified`，只有数据库 terminal event 与 attempt/snapshot identity 一致才能证明 canonical；
- snapshot 级 `analysis_quality_state`：`ready / degraded / blocked / unassessed`，只回答完整研究输入质量；
- candidate 级 `causal_window_state`：`prospective / retrospective / premature / unverified`，只回答冻结发生在信号收盘与下一常规开盘的哪个位置；
- candidate 级 `observation_state`：`ready / partial / unavailable`，只回答 underlying 参考 session、close 与 source 是否可审计。

每个候选在相同事实之上生成三条用途不同、互不替代的决策：

1. **标的路径**（内部 key `raw_underlying_path_v1`）：只判断是否存在足以保存 underlying 价格路径的参考观察；它不声称 setup、Regime、方向或完整研究已通过。自动维护还会额外要求该 track 为 `qualified` 且 causal window 为 `prospective`。
2. **日线选股**（内部 key `underlying_daily_selection_v1`）：要求可证明的官方/前瞻日线选择、可用 underlying 观察、候选未 blocked，且 analysis quality 不是 blocked/unassessed；支持证据 degraded 不会自动抹掉已经冻结的日线选择事实。
3. **完整研究**（内部 key `canonical_full_research_v1`）：在日线选股之上继续要求 analysis quality `ready`、`research_ready`、数据完整、方向可操作且 SPY benchmark anchor 可审计；只有该 track 的 qualified prospective outcome 才可进入完整研究 cohort 与描述性命中率。

因此 `canonical_published + analysis_quality=degraded` 不是自相矛盾：只要 underlying 观察与因果窗口成立，标的路径仍可 qualified prospective 并在 5D/20D 到期后追加；完整研究 track 会以 `analysis_quality_degraded` 明确 excluded，这些 raw-path outcome 永远不会被学习摘要当成完整研究命中/失败。`qualified` 只对指定 track 有意义，不得跨 track 传播。

Qualification v2 进一步令方向性标的路径与实际回填合同一致：`retrospective` 冻结版本在 raw-path track 中也明确 `excluded`，不能因为价格数据可见就被描述成前瞻合格。严格完整研究 track 在 assessment 缺失、policy 未回填或读取异常时一律 fail closed，不再回退旧 `validation_eligible`；旧布尔兼容只保留给已知旧快照的 prospective raw-path 读取。

### 5.3 XNYS 因果时间与结果口径

冻结候选的参考交易日记为 `S`，下一 XNYS 常规交易日记为 `E`。`S close <= freeze < E open` 形成 `causal_window_state=prospective`；早于 `S close` 为 `premature`，到达或晚于 `E open` 为 `retrospective`，日历/证据无法证明时为 `unverified`。`validation_eligible` 继续保留给旧消费者，但 v2 结果维护逐候选选择“标的路径 `qualified` 且 `prospective`”，完整研究摘要再选择“完整研究 `qualified` 且 `prospective`”，不再用一个整批 boolean 同时回答两种分析用途。没有 qualification 的旧快照才按旧 candidate eligibility 做兼容读取，且不能因此伪造三轨 assessment。

交易日、节假日、提前收盘和常规开收盘时刻全部由 `exchange-calendars` 的 `XNYS` 日历解析，日历不可用时 fail closed，不以工作日加减近似。依赖已在 `requirements.txt` 中，本机验收版本为 `exchange-calendars 4.13.2`。

5D/20D 的 `D` 是 XNYS session，不是自然日；`E` 是路径第 1 日。每个合格候选保留两套标的收益代理：

- `S close → 第 5/20 个 session close`：用冻结的参考 close 作分母，并重新抓取同一 `S close` 仅作复权/修订一致性审计，绝不静默替换冻结值；
- `E next-open proxy → 第 5/20 个 session close`：把 `E` 常规开盘价作为统一、不可声称可成交的入场代理，并从它计算路径 MFE/MAE；
- 同窗口保存 SPY 的 close 与 next-open proxy 收益，只有标的和 SPY session 严格对齐且来源连续时才计算相对 SPY；
- LONG/SHORT 分别按方向签名。5D signed close return `≥ +0.5% / ≤ -0.5%` 记 `CONTEXT_HIT / CONTEXT_MISS`，中间为 `NEUTRAL`；20D 对应阈值为 `±1.0%`。

这些都是 underlying 的观测结果，不是期权收益，不包含 strike、到期、IV、Delta、spread、成交滑点或合约乘数，也不证明用户实际看见、触发或执行了交易。`E open` 只是统一代理，不是 broker fill；结果表不写 Journal，也不与真实仓位 P&L 混用。

方向为 `mixed / unknown` 时仍可保存原始 close/entry proxy 路径，但 `signed_*`、MFE、MAE 和 signed relative-SPY 必须为空，只使用 `NON_DIRECTIONAL / DIRECTION_UNKNOWN` 标签，不进入方向命中率分母。参考 close 发生复权/修订不一致、冻结与评估的数据源不连续、重复/缺失 session、目标 close 缺失或 SPY 无法严格对齐时，系统 fail closed：结果保持 pending/data gap，或把有目标 close 的 partial 结果单列；关键质量缺口从学习摘要排除，不跨来源补值。

### 5.4 Cohort 与学习护栏

5D 与 20D 分开统计，并且学习摘要先排除不属于 `canonical_full_research_v1 = qualified + prospective` 的 outcome。标的路径回填只保存“后来怎么走”，不会自动成为完整研究策略样本。通过该 track 后，不同 `signal_version`、`playbook_version`、universe、requested limit、ranking/freeze policy/scope，以及不同结构 setup signature、Regime、direction 的记录仍生成不同 cohort，绝不为了凑样本混算；同 ticker、同信号交易日、同 horizon、同 cohort 也只计一次。

门槛固定为：

- 同一 cohort、同一 horizon 至少有 20 个 LONG/SHORT 已回填方向样本，且覆盖 20 个不同 signal sessions，才显示描述性 `CONTEXT_HIT` 比例；不足时只显示 collecting 和方向样本数，不返回 hit/miss/neutral 细分；
- 同一门槛达到后才标记 `investigation_ready`，含义仅是可以人工调查；
- 任意样本量都不会自动调整排名或证据权重。若人工形成新规则，必须另起版本、做 walk-forward 验证并由用户确认，不能回写旧快照。

本阶段有意不生成 `TRUE_POSITIVE / FALSE_POSITIVE`、`missed opportunity` 或 `regime mismatch`：前两者会把“方向上下文”误装成完整预测，missed opportunity 需要冻结的 `trade_taken=false` 与预定义触发条件，regime mismatch 需要预先定义的失效规则与因果证据；当前快照都不具备这些事实。这里只使用 `CONTEXT_HIT / CONTEXT_MISS / NEUTRAL / NON_DIRECTIONAL / DIRECTION_UNKNOWN` 的描述标签，不做交易归因。

正式数据库改动前保留了 `data/backups/stock_analysis-pre-opportunity-outcomes-20260722.db` 本地回滚备份；`data/` 已由 `.gitignore` 排除，该数据库及其 WAL/SHM 不进入仓库。

## 6. 后续交付顺序

1. **期权可交易性第二层**：当前已交付全部合格候选的 Moomoo underlying overview、当前选中标的的最近到期 ATM Call 单点 IV，以及默认 0–45 DTE 的 OI/当日 Volume/unsigned gross gamma concentration 墙；下一步建立逐字段 nullable 的 `ATMContractContext`，补 bid/ask、size、mid、spread%、delta、各自 as-of/质量和权限，只称“合约流动性/结构上下文”。
2. **波动率结构与异常成交合格化**：underlying IV/IV Rank/HV 概览和 `get_option_event` 只读接入已完成；下一步补期限结构、skew 与预期波动区间，并保存不可变事件快照、测量源时间到抓取时间的实际延迟、处理重复/修正与权限变化，再以样本外结果决定是否进入 ranking。screen 接口可另行验收，不能由 overview 或事件接口成功推定其可用。
3. **TradePlan / Playbook**：用户确认 setup、DTE、delta、时段、事件和风险规则后，才把 `style_match` 从 `unknown` 升级。
4. **盘中场外成交（可选付费源）**：接入带 TRF/condition/correction 的逐笔数据，不把 print 单独解释为方向。
5. **结果合同扩展**：不可变基础榜、5/20 XNYS 交易日标的路径、MFE/MAE 与相对 SPY 已交付；下一步先冻结预定义 trigger/invalidation 和 `trade_taken` 事实，再评估是否建立“触发/未触发”与真实交易关联，未冻结前不做事后归因。
6. **学习验证**：当前须同时满足 20 个已回填方向样本与 20 个独立 signal sessions 才显示描述命中率并允许人工调查；结果回填后仍需 walk-forward 和用户确认的新版本，绝不自动调权。

## 7. 当前限制

- 初版是研究清单，不是全市场扫描器；扫描本地自选前 20 只或 `STOCK_LIST`，首屏只展示 Top 5。TradingView 当前通过官方 TXT 导入合并，不是账户实时同步。
- 初版只对美股期权 underlying 生成研究候选；其他市场保留 blocked 状态等待独立交易日历、Regime 和期权数据合同。
- 普通 OpportunityRun 请求本身仍不保存；Web 先读 canonical status，页面加载不再自动调用 `/premarket/run`。窗口外 `/daily` 结果只是只读预览，旧 v1 `snapshots/ensure` 也不再由该页面默认调用；服务端 scheduler 只在 09:12/09:17 ET 使用正式研究池，历史上未保存的预览不能事后重建成因果样本。
- 没有经用户确认的结构化 Playbook，个人风格匹配保持未知。
- Moomoo OpenD 10.9.6918 / Python SDK 10.9.6908 已可读取官方 underlying overview 与异常期权事件，但尚未持久化期权域逐日快照、测量账户级延迟或完成历史回测；overview 的 IV Rank 是供应商统计字段，不是本项目验证出的择时 edge。
- 期权墙尚未保存为逐日历史序列，也未通过用户交易样本验证其对触墙、穿越、钉仓或后续收益的预测增量；当前只可作为研究上下文。
- 异常成交事件的 Moomoo 方向、情绪和订单类型均为供应商分类；缺少开平仓、参与者身份、真实主动方与 dealer inventory，当前不得据此推断 dealer 定位或生成买卖指令。
- 当前结果学习只评估标的价格上下文，不包含期权收益、真实成交、trigger 是否触发或 trade_taken；异步 overview、ATM IV、期权墙和异常成交也尚未冻结进样本。
- 自动结果任务只追加真正到达目标交易日的 underlying 结果；当前没有 `TriggerSpec`、期权合约收益或 `trade_taken` 事实，不能据此生成“错过机会”或真实交易胜率。
- 单票详情尚未提供完整合约级 bid/ask size、spread、期限结构、skew、可成交滑点和用户风险预算；这些缺口不会由聚合 Volume/OI、IV 或 Gross Gamma 补推。
- 结果摘要是严格 cohort 内的小样本描述，不是策略胜率；达到人工调查门槛也不会自动修改排名。
- FINRA 公共数据不具备当日时效；第三方 TRF 源尚未配置。

## 8. 规模与频率口径订正：断开显示 + 费用门槛 + 剔尾读数（2026-08-04 晚）

> 本节自足，可独立阅读；订正对象是 §2.12.1 的 `discipline` 区块。若与 §2.12.1 中「每美元回报塌了约 24 倍」的旧表述冲突，**以本节为准**——那条趋势线本身约 75% 是口径假象。

### 8.1 为什么订正：被证伪的头条

工作台此前把月度 `pnl_per_dollar_risked` 画成一条连续趋势线（默认 build #1：4 月 4.85% → 7 月 1.80%；build #3：4 月 4.85% → 7 月 −0.19%），读起来像「边际在衰减」。后续取证表明**这条曲线约 75% 是测量口径造成的，把它当趋势线发布是主动误导**：

| 证据 | 数值 |
| --- | --- |
| build #3 中由**汇总 ORDER 行**构建的回合（`evidence_summary_json.fill_allocations == 0`，无明细成交） | 245 笔，恰好是 3 月 + 4/1–4/20 |
| 断点位置 | 4/20–21 ≈ CSV 导出日（2026-07-21）前约 90 天＝券商明细成交保留窗口，**不是**行情或行为边界 |
| 汇总口径 vs 明细口径毛每美元（全样本） | 9.64% vs 1.77%（5.4 倍） |
| **同在 4 月内**的对照（同一人、同一月、同一行情） | **9.53%（汇总） vs 2.42%（明细）** |
| 分母被低估的典型特征 | 汇总行 4.08% 的回报 >+200%（明细行 1.84%）；配平笔数中位 2 vs 3 |
| 管线自身的标记 | canonical 投影已写明 `aggregate_order_amount_policy = "audit_only_not_execution_cash_flow"`（仅供审计，不是执行现金流） |

在**可比窗口**（4/20–7/31，1,410 笔）上，毛每美元为 2.42 / 2.58 / 2.00 / 1.22%，两两置换检验**全部 p ≥ 0.756**，Kruskal p=0.887，日度边际对日历时间 rho=+0.126 p=0.295——**没有可检出的边际衰减**；按笔等权，7 月反而是第二好的月份。

真正成立的两件事：**单笔风险 +68%**（$6,760 → $11,340）而单笔美元盈亏基本持平（$163 → $138）；**费用门槛恒定**在约 119–138 bp 权利金（每张合约往返 $3.29–3.31），在约 1.2% 毛边际下把 7 月**净**边际压成 −0.19%。以及尾部事实：可比窗口内最好的 **5 笔（共 1,410 笔）贡献了 80% 的毛盈亏**，剔除每月最好 5 笔后四个月每美元边际**全为负**（4 月 −3.53%、5 月 −0.91%、6 月 −0.86%、7 月 −1.77%）。

### 8.2 口径来源判据订正（correctness）

`personal_edge.py` 此前用 `has_exact_fill_times` 计算 `exact_fill_share`。**因果上正确的判据是「该回合是否由明细成交构建」**，即 `evidence_summary_json.fill_allocations > 0`——决定风险金额分母是否可信的是构建来源，不是时点是否精确。两者**会不一致**：build #3 有 **3 笔 4 月回合**由明细成交构建但 `has_exact_fill_times=0`（4 月 `fill_detailed_share` 0.4238 vs `exact_fill_share` 0.4155）。

- 新增 `fill_detailed_share` —— **口径可比性以它为准**；`exact_fill_share` 保留供历史连续性，两者差异不隐藏（`discipline.fill_detailed_governs` 原文下发这条规则，`limitations` 亦携带）。
- 判据解析 **fail closed**：`NULL` / 空串 / 非法 JSON / 非对象 / 缺 `fill_allocations` 键（旧行与既有 fixture 写的就是 `{}`）/ 布尔值（`True` 是 `int` 子类，绝不能读成「1 笔明细成交」）/ 非整数 / 负数 一律记为**来源不可判定**，计入 `fill_provenance_unknown_count`，**绝不当作全明细**。额外键（build #3 有 2 行带 `group_fee_unallocated`）照常解析。
- 任一区间只要不是 100% 明细成交，即置位 `basis_break: true` + `basis_break_reason`（写明笔数、成因与 `audit_only_not_execution_cash_flow`）。

### 8.3 端点合同（additive，既有字段一字不改）

`discipline.monthly[]` 与 `discipline.current_window` 每行新增：

| 字段 | 含义 |
| --- | --- |
| `fill_detailed_count` / `aggregate_only_count` / `fill_provenance_unknown_count` | 口径来源三分计数 |
| `fill_detailed_share` | 由明细成交构建的占比（分母＝区间全部回合；不可判定会拉低它） |
| `basis_break` / `basis_break_reason` | 机器可读的断裂标记与原因 |
| `fees_total` / `fees_missing_count` | 费用合计与缺席计数 |
| `fee_pct_of_premium_at_risk` (+`_reason`) | **恒定过路费**＝Σ`total_fee` ÷ Σ`\|opening_cash_flow\|` |
| `gross_pct_of_premium_at_risk` (+`_reason`) | 毛口径＝(净盈亏＋费用) ÷ 同一分母；恒等式 **毛 = 净 + 费用** 在同一行严格成立 |
| `pnl_per_dollar_excluding_top_n` / `gross_pct_excluding_top_n` | 剔除最好 N 笔后的每美元回报（净／毛），N＝`exclude_top_n`（＝`body_trim_count`＝5） |
| `excluding_top_n_count` / `excluding_top_n_reason` | 剩余笔数与不成立原因 |

区块级新增 `exclude_top_n` 与 `fill_detailed_governs`。**剔尾口径**：分子分母取同一子集（风险金额已知的回合），按净盈亏排序去掉最好的 N 笔，门槛与 `body_pnl` 同为 **n≥15**（按「风险金额已知」的样本数判定）。**fail-closed**：分母为 0、无可用 `opening_cash_flow`、**任一回合缺 `total_fee`**（过路费绝不以 0 冒充）或样本不足时返回 `null` + `*_reason`。

### 8.4 消费面

- **Journal「规模与频率」表**（`DisciplineMonthlyPanel`）：`basis_break` 月份单独成组、行内标注「口径断裂 · 不可与后续月份比较」（悬停给出笔数与成因），两组之间插入一条**显式断口行**，并在表上方给出说明段——写明分母来自管线自己标记 `audit_only_not_execution_cash_flow` 的汇总 ORDER 行，以及**同在 4 月内 9.53% vs 2.42%** 的对照。新增「毛每美元 / 费用门槛 / 剔除最好 5 笔 / 明细成交占比」四列；毛口径 ≤ 门槛的月份加「不及门槛」标记（净口径必为负）。
- **`/intraday` 规模与频率条**（`IntradayDisciplineStrip`）：新增「毛每美元」「费用门槛」两个 chip；**基线选择拒绝被污染的月份**——`baseline()` 跳过所有 `basis_break` 月份，只取**最早一个全明细成交月份**并在脚注写明取的是哪个月（「基线取最早的全明细成交月份（N 月）」）；一个干净月份都没有时**宁可不显示基线**（「基线不显示 · 早期月份均由汇总 ORDER 行构建」）。当前窗口自身断裂时显式警示，不静默比较。

### 8.5 Playbook 候选 R9

经既有 `POST /api/v1/journal/v2/playbook/candidates` 路径显式创建（append-only、幂等、未晋升）：**「R9 · 没有「好做的行情」，也没有边际衰减（衰减 75% 是口径假象）」**（candidate id=12，接在 R8 id=11 之后）。`rule_text` 内嵌：regime 零结果（132 项检验仅 5 项名义显著 < 随机期望 6.6；BH q<0.20 无一存活；族向置换 p=0.12；样本外相关号一致率 45.3%；岭回归 R²_out=−3.85；日度边际 lag-1 自相关 −0.053、游程检验 p=0.94；并纠正「4 月是回调后磨」的叙事前提——4 月实为近乎不间断上涨 +9.68%、最大回撤 −0.85%）、75% 口径假象拆解与 4 月内 9.53% vs 2.42% 对照、可比窗口无衰减及其 p 值、真实变化（单笔风险 +68% 而美元盈亏持平、恒定约 125bp 门槛、7 月净 −0.19%）、尾部事实（top 5/1,410 ＝ 80% 毛盈亏；剔尾后四个月全负）。定位为**数据描述供本人复核，非建议**，并携带 n=92 交易日 / 单一 regime / 单一交易者的边界。

### 8.6 诚实边界

样本为单一交易者、单一 regime、92 个交易日的描述统计，**不是因果结论、不构成建议**。`basis_break` 区间的每美元读数**只能自比，不可跨组比较**，也不得进入任何排序、信号或过滤。月度口径仍为 ET≈UTC−4 近似（3 月初 EST 为 UTC−5，边界样本可能偏移 ±1 小时）。本区块的样本＝该 build 全部**已平仓且有净盈亏**的回合（含少量正股回合），与上述只取期权回合的研究样本不同，绝对值会有差异、方向一致。

## 9. 车道检查清单 + 规则遵守度统计（规则 v2 的前向证伪，2026-08-04）

用户从自身干净口径历史推出并采纳了一套**两车道规则**（Playbook 候选「V2-0」…「V2-D」，id 13–17，append-only 未晋升）。本节记录把这套规则落到盘面与账本上的两件东西：**开仓前对照清单**（`/intraday`）与**规则遵守度记账**（Journal）。两者都不下单、不推荐、不含概率；系统只读。

### 9.1 干净口径与依据数字

样本＝用户自身 build #3 的回合，满足：已平仓、`realized_pnl_net` 与 `opening_cash_flow` 均已知、由**明细成交**构建（`evidence_summary_json.fill_allocations > 0`）、ET 入场日 ≥ **2026-04-21**，共 **n=1,407**。`gross = realized_pnl_net + total_fee`，`risk = ABS(opening_cash_flow)`。4/20–21 的边界≈券商明细成交保留窗口（CSV 导出日前约 90 天），不是行情或行为边界；更早的回合由汇总 ORDER 行构建、分母被管线自身标记 `audit_only_not_execution_cash_flow`，混入会系统性抬高每美元读数，故整段排除（见 §8）。

| 事实 | 数字 |
| --- | --- |
| 同一 4-7DTE 合约：**隔夜持有** | 毛 **+34.13%**（n=65，胜率 61.5%，过路费 1.55%，剔除最好 3 笔仍 +22.27%、最好 5 笔仍 **+19.13%**）——全样本**唯一**非尾部驱动的组合 |
| 同一 4-7DTE 合约：**当日平掉** | **−4.04%**（n=57，胜率 22.8%，剔除最好 5 笔 −10.28%） |
| 0DTE 日内 | +3.44%（n=556，过路费 1.76%），剔除最好 5 笔仅 **+0.41%** → 低于过路费，尾部驱动 |
| 0DTE 按 ET 小时 | 9 点 +10.6%（n=160）、10 点 +2.7%、11 点 +4.9%；**12:00 之后 −8.19%**（剔除最好 5 笔 −15.54%） |
| 1-3DTE | 当日平 −2.94%（n=469，剔尾 −6.45%）；隔夜 +6.98% 但剔尾 **−4.03%**（纯尾部驱动）——两头都不占，整段排除 |
| 隔夜进场时段 | ET 11:00–12:00 **−1.76%**、13:00–14:00 **−2.99%**；其余时段 +21%…+36% |
| 仓位纪律 | 单笔风险 p25–p90 为 5,180–15,750（最大 70,375）；70 个交易日中 51% 为亏损日；最差单日 −82,130 恰好发生在投入最大的一天（498,743）；峰值累计 +205,619 后最大回撤 −163,621 |

**每一个消费面都必须原文携带的边界**：样本仅 **2026-04→07 一个市场状态**（SPY 上行，隔夜多头天然占优，下跌市可能完全不同）；星级桶 **n=65 偏小**；**隔夜跳空风险在该窗口内未被充分体现**；描述统计，不是因果结论，不构成建议。

### 9.2 车道判定（纯机械，`classify_rule_lane`）

判定只读四个已存字段：`dte_at_entry`、`opened_at` 的 ET 小时、`opened_at`/`closed_at` 的 ET **自然日**、`ABS(opening_cash_flow)`。它**无从知道进场当时的意图**，因此也不声称知道——`bought_time_unused` 是结果标签，不是读心。ET 换算沿用既有 ET≈UTC−4 近似（与 `month_basis` 同一口径）。

判定顺序固定且是全函数（任何输入恰好落入一条车道）：

| 车道 | 条件 | 判定 | 规则 |
| --- | --- | --- | --- |
| `intraday_0dte` | `dte == 0` 且 ET 小时 < 12 | 合规 | V2-A |
| `overnight_4_7` | `4 ≤ dte ≤ 7` 且平仓 ET 自然日 **晚于**开仓日 | 合规 | V2-B |
| `dte_1_3` | `1 ≤ dte ≤ 3`（任何时段、任何持有时长） | 违规 | V2-C① |
| `bought_time_unused` | `dte ≥ 4` 且平仓 ET 自然日 **不晚于**开仓日 | 违规 | V2-C② |
| `late_0dte` | `dte == 0` 且 ET 小时 ≥ 12 | 违规 | V2-C③ |
| `other` | `dte ≥ 8` 且跨自然日 | 规则未覆盖 | V2-0 |
| `unknown` | 缺 DTE / 负 DTE / 缺 ET 小时 / 缺开仓或平仓自然日 | 不可判定 | V2-0 |

边界语义：DTE 0 → 日内，1 与 3 → 违规，4 与 7 → 过夜，8 → 未覆盖；ET 11 时仍合规、**12 时整**即违规；当日/隔夜按 **ET 自然日**判定而非持仓时长——同日开平的 20 小时持仓仍是当日平。`unknown` **绝不并入 `other`**：缺席即缺席。

### 9.3 端点合同（additive，既有字段一字不改）

`GET /api/v1/journal/v2/personal-edge` 新增 `rule_compliance` 区块，并新增可选 query `since=YYYY-MM-DD`（默认＝规则采纳日 `RULE_SET_V2_ADOPTED_AT = "2026-08-05"`，用户的下一个交易日 ET）。`since` **只**移动前向切片，不影响全历史切片与任何既有字段；进程内 TTL 缓存的键相应改为 `(account_key, since, 解析后的默认 build id)`，不同 `since` 不共用条目，且 build 激活会因 build id 变化自然使旧缓存失效（rules-evidence 同理）。`data_state = not_built` 时该区块为 `null`。

| 字段 | 含义 |
| --- | --- |
| `rule_set_id` / `adopted_at` / `clean_basis_start` / `clean_basis_reason` | 规则集标识、采纳日、干净口径起点与其**原文理由** |
| `population_n` | 进入统计的回合数 |
| `excluded_before_clean_basis_count` / `excluded_aggregate_or_unknown_basis_count` / `excluded_missing_premium_count` | 三类排除各自计数（沉默过滤是不允许的） |
| `exclude_top_n` / `exclude_top_n_min_episode_count` | 剔尾的 N（=5）与门槛（n≥15），与规模与频率同一实现 |
| `intraday_lane_dte` / `intraday_lane_et_cutoff_hour` / `overnight_lane_min_dte` / `overnight_lane_max_dte` / `overnight_lane_weak_entry_et_hours` | 车道阈值真源（前端不硬编码数值） |
| `all_history` / `since_adoption` | 两个切片，结构相同 |
| ~~`daily_budget`~~ | **已于 2026-08 移除**（V2-D 额度读数）：Journal 永远不含「今天」，该读数的 as-of 恒落在过去、恒为过期；当日额度由前端手动计数承载（见 §11.6 修正 4） |
| `limitations` | 单一 regime / n=65 / 跳空 / 非建议 / 只读不下单，逐条原文 |

每个切片：`state`（`ready` / `no_episodes` / `no_episodes_since_adoption`）+ `state_reason` + `start_date` + `n` + `lanes[]` + `verdicts[]`。`lanes[]` 逐条携带 `verdict` 与 `rule_id`——**不下发以车道 id 为键的字典**，因为 Web 层的深层 camelCase 会改写字典键，规则 id 必须只以「值」的形式过网。每个桶给出 `n / risk / net / gross / gross_pct / toll_pct / win_rate / gross_pct_excluding_top_n / excluding_top_n_count`，任何分母不足的比率为 `null` + `ratio_reason` / `excluding_top_n_reason`（空桶是「无样本」，**不是 0%**）。`verdicts[]` 是同一套算法在 `compliant / violation / uncovered / unknown` 上的汇总，因此**合规单 vs 违规单**可直接对比。

`daily_budget` 区块已移除（2026-08）：它按 build 内最后一个交易日给出 V2-D 额度读数，但 Journal 只有导入的历史成交、永远不含今天，该读数因此恒为过期——一个永远过期的读数等于没有。当日「日内单 x/6 / 过夜持仓 y/3」由前端手动计数（`useIntradayManualBudgetStore`，按 ET 日作用域）承载，见 §11.6 修正 4。

**剔尾机制复用**：`_exclude_top_n_readings` 由规模与频率与车道遵守度共用——分子分母取同一子集（风险金额已知的回合），按净盈亏排序去掉最好的 5 笔，样本 <15 笔时返回 `null` + 原因；剩余回合中任一缺 `total_fee` 时净口径仍成立而毛口径缺席（过路费绝不以 0 冒充）。

### 9.4 消费面一：`/intraday` 开仓前车道检查清单

`LaneChecklistPanel`（判定层在 `laneChecklist.ts`，组件只渲染）位于规模与频率条之下。输入：车道（日内 / 过夜）+ 选填 DTE + 选填标的 + 「打算今天就平掉」勾选。每条检查只有 `pass` / `fail` / **标缺** 三态，附一行**引用规则编号**的原因。

- **日内车道**：① DTE 必须为 0（V2-A）；② ET 时钟必须 < 12:00（V2-A / V2-C③）——时钟取**市场脉搏端点的 `generatedAt`**，与脉搏条「数据时点」同一口径，**不另起时钟源**；③ 标的不在财报回避窗——复用日内扫描**深度层候选上已有的 `earningsProximity`**，标的不在深度层或日历不可得时**标缺**（未知≠安全，绝不以「没查到」冒充合规）。
- **过夜车道**：① DTE 必须在 4-7（V2-B）；② ET 小时不得为 11 或 13（V2-B 的两个偏弱时段）；③ 提醒行「**本车道不适用速度衰竭离场（V2-B）**」+「进场时必须能说清『为什么今天不一定走完』」。V2-B 不含财报条款，因此过夜车道**不渲染**财报检查——不凭空发明规则。
- **硬禁止（V2-C，与所选车道无关，命中即列出）**：① 1-3DTE 一律不开新仓；② 4-7DTE 且用户勾选「打算今天就平掉」；③ DTE 0 且 ET ≥ 12:00。
- **额度读数**：「今日日内单 x/6」「当前过夜持仓 y/3」为**用户手动计数**（`useIntradayManualBudgetStore`，按 ET 日作用域，明标「手动维护 · 按 ET 交易日自动归零」）。原 `rule_compliance.daily_budget` 读数因 Journal 永远不含今天而恒为过期，已于 2026-08 移除（见 §11.6 修正 4）。

面板正文写明：这是对照**你自己那套规则**的检查清单，不是推荐、不含概率；本系统只读，**不会下单**；并原文携带单一 regime / n=65 / 跳空风险的边界。

### 9.5 消费面二：Journal「规则遵守度」

`RuleCompliancePanel`（规模与频率的同层兄弟）按两个切片摊开车道表，并把**合规单 vs 违规单的毛每美元**作为最醒目的读数——那就是会证伪或确认这套规则的数字。

顺序上「采纳后（前向验证）」在前、「全历史（规则从这里推出来）」在后，并各自写明定位：全历史那一列**因为规则由它推出，所以必然好看**。采纳后尚无样本时显示一行「**尚无采纳后样本**」+ 端点原文原因，**绝不渲染成一张全 0 的表**。区块另外说明车道判定的机械性、干净口径三类排除的计数，并逐条原文列出 `limitations`。

### 9.6 真实读数（2026-08-04 核对）

正式默认 build 为 **#1**（n=1,191 进入统计）：合规单 +9.06%（n=464）vs 违规单 −1.58%（n=638）。对 **build #3** 的只读交叉核对逐项复现了 §9.1 的依据数字：`population_n=1,407`；`overnight_4_7` n=65 / 毛 **+34.13%** / 过路费 1.55% / 胜率 61.5% / 剔尾 **+19.13%**；`late_0dte` n=89 / **−8.19%** / 剔尾 **−15.54%**；`intraday_0dte` n=467 / +5.74%；`dte_1_3` n=602 / −1.37%；`bought_time_unused` n=81 / −3.72%；`other` n=86 / +1.57%；`unknown` n=17。汇总：**合规单 +8.49%（n=532）vs 违规单 −2.48%（n=772）**，剔尾后 +5.14% vs −4.81%。两个 build 的 `since_adoption` 均为 `no_episodes_since_adoption`（采纳日尚未到来），额度读数带 as-of 日 2026-07-20 / 2026-07-31，因此在盘面上一律显示标缺。

### 9.7 诚实边界

样本为**单一交易者、单一市场状态（2026-04→07，SPY 上行）**的描述统计，**不是因果结论、不构成建议**；过夜车道 n=65 偏小，隔夜跳空风险在该窗口内未被充分体现。车道判定是机械的结果标签，不代表进场意图。这两个面板不排序、不打分、不产生信号、不进入任何过滤，也**不下任何单**——系统全程只读。真正的检验只有一个：采纳后那一列。

## 10. 今日车道可用性（day-type，V2-E，2026-08-04）

### 10.1 为什么做：星期效应其实是合约可用性

对用户自身**干净口径**历史（build #3，ET 入场日 ≥ 2026-04-21、仅 `fill_allocations > 0` 的明细成交回合，**n=1,407**；`gross = realized_pnl_net + total_fee`，`risk = ABS(opening_cash_flow)`，口径同 §9.1）的取证得到一个反直觉结论：**用户全部的「星期效应」都是合约可用性造成的合约选择问题。**

| 星期 | 毛每美元 | n | 其中 0DTE | 其中 1-3DTE |
| --- | --- | --- | --- | --- |
| 周一 | **+5.61%** | 263 | 141 | — |
| 周三 | **+7.99%** | 259 | 118 | — |
| 周五 | **+3.29%** | 284 | 209 | — |
| **周二** | **−2.61%** | 271 | **仅 36** | **206** |
| **周四** | **−3.74%** | 313 | **52** | **213** |

成因：用户主要标的（NVDA / TSLA / MU / AAPL 等）为**周一/三/五到期**，多数中小盘**仅周五到期**；周二/周四没有 0DTE 可用，于是**退而买 1-3DTE**。而逐 DTE 拆开后，1-3DTE 恰恰是全样本最差的一段：

| 桶 | 毛每美元 | n | 剔除最好 3 笔 | 胜率 |
| --- | --- | --- | --- | --- |
| 0DTE 当日平 | +3.44% | 556 | +1.49% | — |
| **1DTE 当日平** | **−4.97%** | 313 | **−8.01%** | **25.2%** |
| 2DTE 当日平 | +5.73% | 90 | −3.15% | — |
| 3DTE 当日平 | −5.19% | 66 | −9.88% | — |
| 4DTE 隔夜 | **+38.88%** | 30 | +16.33% | 60.0% |
| 7DTE 隔夜 | **+33.35%** | 21 | +21.63% | 66.7% |

另外两个事实：周二/周四真做的 0DTE 中 **QQQ 占 43/88 笔**（毛 −0.45%），而 QQQ 属用户历史负边际标的（见 Playbook R3）；**行为缺口**——恰恰在过夜车道是唯一好选择的两天，用户几乎没用它：周二 1 笔、周四 11 笔 4-7DTE 隔夜单。

以上已写入 Playbook 候选 **「V2-E · 按合约可用性决定今天做不做日内（周二/周四＝过夜日）」**（id 18，append-only 未晋升）。本节记录把 V2-E 落到盘面上的读数层。

### 10.2 判定规则：从当日真实到期日推导，不含任何星期逻辑

**星期规则是错的实现方式**：假日、节前特殊到期、标的新增周二/周四到期都会让「周二＝过夜日」失效。因此判定输入一律是**当日真实期权到期日元数据**。

逐标的（`build_ticker_availability`，`src/opportunities/lane_availability.py`）：

| 输入 | `state` | `has_zero_dte` | 说明 |
| --- | --- | --- | --- |
| 0..7 DTE 内有到期日且含 0DTE | `ready` | `true` | `available_dte_list` 去重升序 |
| 0..7 DTE 内有到期日但无 0DTE | `ready` | `false` | 确证没有 |
| 窗口内一个到期日都没有 | `ready` | `false` | 诚实空态，不是失败 |
| 链读不到 | `unavailable` | **`null`** | + `unavailable_reason`；**未知 ≠「今天没有 0DTE」** |

聚合 `day_type`（`derive_day_type`），顺序即优先级：

1. **任一标的确证有 0DTE** → `intraday_available`。正向证据不因别的标的读不到而作废（读不到只可能再**增加** 0DTE）。
2. 没有任何 0DTE **且全部标的都读到了链** → `overnight_only`。这就是 V2-E 的「过夜日」：日内车道关闭，只剩过夜车道（V2-B）或不做。
3. 其余（一个都没查，或没查到 0DTE 但存在读不到的标的）→ `unknown` + 原因。**fail closed**：绝不以「读不到」冒充「今天没有 0DTE」。

### 10.3 数据路径与额度护栏（零新增抓取路径）

回答「今天有没有 0DTE」只需要到期日，不需要任何一张合约的报价。因此新增的
`fetch_expiry_availability_moomoo`（`data_provider/moomoo_options.py`）**复用临期合约链读取路径的第一步**——同一个独占 wall QuoteContext lane + 同一个 `get_option_expiration_date`——拿到到期日元数据后就停下：**不发 `get_option_chain` 日期窗口、不取 underlying 快照、不发 `get_market_snapshot` 批次**，比 §2.9 的整条链读取便宜一个量级。

额度护栏（对齐 §2.1 记录的 10 次链查询 / 30 秒；常量见 `api/v1/endpoints/opportunities.py` 的 `_LANE_AVAILABILITY_*`）：

- **逐标的按 ET 交易日缓存 1 小时**（到期日一天最多变一次）——同一交易日内的 60 秒轮询稳态命中缓存、**零供应商请求**；失败只短缓存 5 分钟，避免一次抖动把一整天钉死在 `unknown`。
- **单轮新增读取上限 8**（60 秒轮询周期内 ≤8 次 < 10 次/30 秒）；超出的标的本轮显式记为 `unavailable` + `deferred_provider_quota_budget` 并列入 `deferred_tickers`，**下一轮补齐**——绝不为了凑齐结论而把「没查」说成「没有 0DTE」。
- **单轮参与判定的标的上限 12**（＝ `INTRADAY_DEEP_LANE_MAX` 默认值），**只覆盖深度层，绝不向宽层全清单扇出**。

### 10.4 端点合同（additive，既有字段一字不改）

**`POST /api/v1/opportunities/intraday-top`** 新增顶层 `lane_availability`（仅 **watchlist 两层模式**；单层/显式 symbols 路径恒为 `null`，行为逐字节不变）：

| 字段 | 含义 |
| --- | --- |
| `day_type` / `day_type_reason` | `intraday_available` / `overnight_only` / `unknown` + 一行原因 |
| `basis` | `per_ticker_option_expiry_metadata_within_0_7_dte_v1`（判定依据回显，不是星期规则） |
| `checked_scope` / `checked_count` / `readable_count` / `unavailable_count` | 判定范围与可读性计数 |
| `zero_dte_tickers` | **确证**有 0DTE 的标的（用户直接看到「今天是哪几个」） |
| `deferred_tickers` | 本轮因额度预算或名单上限未查的标的 |
| `tickers[]` | 逐标的 `state / has_zero_dte / available_dte_list / expiries[] / unavailable_reason` |
| `formula_version` / `market_date_et` / `max_dte` / `limitations` | 口径与逐条边界 |

**`POST /api/v1/opportunities/near-expiry-contracts`**（§2.9）新增三个 additive 字段，由**已在手的到期日分组**推导，**零额外抓取**：`has_zero_dte`、`available_dte_list`、`availability_unavailable_reason`。链读不到（`not_configured` / `unavailable`）时 `has_zero_dte` 显式 `null` + `near_expiry_chain_unavailable`；`state=empty`（链可读、窗口内无到期日）时为 `false`。

### 10.5 消费面：车道检查清单的第一行

`LaneChecklistPanel`（§9.4）新增**面板第一行**的车道日类型，它决定「今天这条车道到底开不开」，必须在用户选车道之前就看到。四种呈现：

| 状态 | 文案 |
| --- | --- |
| `intraday_available` | 「今日：日内车道可用（NVDA/TSLA/MU 有 0DTE）」——直接点名是**哪几个**标的 |
| `overnight_only` | 「今日：过夜日 · 无 0DTE · 日内车道关闭（V2-E）」 |
| `blacklist_only` | 「今日仅黑名单标的有 0DTE（QQQ）· 日内车道实际关闭（V2-E）」 |
| `unknown` | 「今日：车道可用性标缺 · …」+ 原因（区块缺席 / 链读不到 / 本轮未查） |

tooltip 一律以 **`规则出处：V2-E · 按合约可用性决定今天做不做日内（周二/周四＝过夜日）`** 起头，与既有 V2-A/B/C/D 的引用模式一致。

**硬阻断**：选中**日内**车道且 `day_type = overnight_only` 时给出 `V2-E` 硬阻断，一行写清证据——「绝不退而买 1-3DTE（周二/周四历史 −2.61%/−3.74%，1DTE 当日 −4.97%，n=313，胜率 25.2%）」。选中**过夜**车道不阻断——它正是今天该走的车道。

**黑名单叠加**：仓库内此前没有黑名单配置项，故在 `laneChecklist.ts` 定义为**有出处的常量** `BLACKLIST_TICKERS = ['PLTR', 'AMD', 'QQQ', 'SMCI']`，出处即用户自己的规则文本——Playbook R3「标的黑名单」（PLTR −$5.3 万、QQQ −$4.1 万）、V2-E 附带禁令（周二/周四 0DTE 中 QQQ 占 43/88 笔，毛 −0.45%）、个人画像回灌 G-16 的「漏斗」标的。**黑名单是用户自己的规则，不是市场事实**，因此只在前端叠加：服务端 `day_type` 只回答「今天有没有 0DTE」。若确证有 0DTE 但**全部**落在黑名单上，面板显示 `blacklist_only` 并同样阻断日内车道（V2-E 附带禁令：不得因「今天只有它有 0DTE」而交易黑名单标的）；若同时存在非黑名单标的，则照常 `intraday_available`，黑名单标的在 tooltip 里如实提示、不静默。

### 10.6 真实读数（2026-08-04 周二，TestClient 连本机 OpenD）

第一轮轮询：深度层 16 个标的 → 参与判定 12 个（上限）、本轮读取 8 个（额度预算），`day_type=unknown`，4 个标的显式 `deferred_provider_quota_budget`。第二轮起全部 12 个可读、缓存命中零新增请求，**`day_type=overnight_only`**（「12 个深度层标的今日均无 0DTE 到期，日内车道关闭（V2-E）」）：

| 标的 | `available_dte_list` | `has_zero_dte` |
| --- | --- | --- |
| NVDA / TSLA / MU / AAPL | `[1, 3, 6]`（08-05 / 08-07 / 08-10） | `false` |
| AAOI / PLTR / LITE / MRVL / GLW / ALAB / AMKR / ASTS | `[3]`（08-07） | `false` |

与用户手动核对完全一致：主力名（NVDA/TSLA/MU/AAPL）今天只有 1/3/6DTE，中小盘只有周五（3DTE）——**2026-08-04 是一个标准的「过夜日」**。同日单独探测 QQQ 得 `[0, 1, 2, 3, 6, 7]`（确有 0DTE），但 QQQ 今日不在深度层，故不影响聚合；若它进入深度层，前端会呈现 `blacklist_only` 而不是放行。

### 10.7 诚实边界

依据数字来自**单一交易者、单一市场状态（2026-04→07，SPY 上行）**的描述统计，4DTE / 7DTE 隔夜样本各仅 30 / 21 笔，隔夜跳空风险未充分体现——**不是因果结论，不构成建议**。车道可用性只回答「今天有没有 0DTE 可用」：不评价标的、不预测方向、不排序、不打分、不进入任何统计，也**不下任何单**。判定范围是今日深度层中有界的前若干个标的，**不代表全部 universe 今日没有 0DTE**；DTE 以 America/New_York 交易日按自然日计算，未接入交易所假日日历。链读不到时一律显式标缺——**未知不等于安全，也不等于「今天没有 0DTE」**。

---

## 11. /intraday 重构：盘中计划在上、实时扫描在下（2026-08-05）

### 11.1 为什么改

用户在盘中看到的车道清单是这样的：六行读数里五行「标缺」（合约期限、财报回避、今日日内单、当前过夜持仓，加上顶部的车道可用性）。原文反馈：「警示性不够」「本系统只读、不会下单不需要你说」。问题不在数据，在**把「系统已经知道的要求」和「查过了拿不到的读数」混成了同一个词**，又在最重要的一件事（今天没有 0DTE）上只给了一行小字。

### 11.2 页面顺序

脉搏条 → 纪律条（规模与频率）→ 车道清单 → **盘中计划** → **实时扫描（滚动）** → 今日计划跟踪 → 期权事件流（2026-08-14 起侧栏换为盘前期权异常面板，期权异动 feed 移入 Journal 仓位复盘，见 §15）。

盘中计划是干活的地方，实时扫描退居其下作为**提升来源**：扫描表每行有「加入盘中计划」，已提升的行显示「已在计划」。扫描表原有的深度层行为（8 行、排序、展开、账本、仅快照列表）一字未动。

### 11.3 盘中计划清单（客户端，按 ET 交易日作用域）

`apps/dsa-web/src/stores/intradayPlanStore.ts`：`useIntradayPlanStore` 持久化在 localStorage（`dsa-intraday-plan`）但状态里带 `marketDate`，读侧选择器 `selectPlanTickers` 判定「不是今天就是空清单」——换一个交易日自动为空，无需用户记得清理；「清空」随时可用。上限 8，与服务端 `focus_symbols` 一致。

⚠️ **它绝不复制 `useUserWatchlistStore` 的坑**：那个 store 一旦非空，页面就会向 `/opportunities/intraday-top` 显式传 `symbols`，而服务端只在「空 `symbols` + 已配置 `INTRADAY_WATCHLIST`」时走两层扫描——于是两层模式被整个关掉。盘中计划的标的走的是 additive 的 `focusSymbols`，**不进 `symbols`**，两层扫描原样保留（页面级测试锁定该回归）。

### 11.4 `focus_symbols` 契约（additive）

`IntradayTopRequest.focus_symbols`：`list[str]`，`max_length=8`，与 `symbols` 共用 `_normalized_symbols`（去空白/大写/去重，非法代码 422）。**不参与 universe 解析**，只在两层模式下并入深度层，语义与既有 `INTRADAY_PINNED_TICKERS` 完全一致：

- 与 `plan_always_include` / `user_pinned` 去重（同一标的只占一个深度位，标注取先出现的身份）；
- **不占异动闸门额度**（`deep_lane_max` 全额留给 movers），但计入当日晋升去重集合；
- 并入**同一批** `get_market_snapshot`（仍是一次请求），不新增任何取数路径；
- 深度位标注 `deep_lane_reason.promoted_by = "user_focus"`，`universe_scan.user_focus` 回显生效名单；
- 进入服务端扫描缓存 key（不同 focus 名单＝不同深度层名单，绝不共用旧结果）；
- 单层路径（显式 `symbols` 或未配置清单）完全不受影响。

真实读数（TestClient，清单 `[AAA, BBB]`、`deep_lane_max=1`、`focus_symbols=["mu"]`）：

```
deep_lane  [{"ticker":"MU","promoted_by":"user_focus","mover_rank":null},
            {"ticker":"BBB","promoted_by":"mover_rank","mover_rank":1}]
user_focus ['MU']   deep_lane_max 1   deep_lane_count 2
```

MU 不在清单里也被并入同一批快照并深扫，且没有挤掉异动名额。

### 11.5 逐标的卡片

`IntradayPlanPanel.tsx` + 纯判定层 `intradayPlanChecks.ts`。**车道规则与 ET 时钟直接复用 `laneChecklist.ts` 的同一套判定**（`evaluateLaneChecklist` + `earningsCheck`），不另写一份，避免两处规则漂移。每张卡片：

1. **今日车道**（V2-E）：日内可用 / 过夜日 · 日内关闭 / 标缺 / 读取中；
2. **逐条状态**（四态 `pass` / `fail` / `missing` / `requirement`）：合约期限（V2-A/B）、开仓时点（V2-A/C③ 或 V2-B）、财报回避、近 30 分位移 vs 0.5 ATR、速度、哑火形态、形态匹配 S1/S2/S3、波段 vs 大盘、今日波段；标的不在今日深度层时扫描读数**逐条标缺**并明说「未知≠没动」，绝不静默放行；
3. **合约候选**：按今日车道推出的 DTE 区间读一次 `near-expiry-contracts`（日内 `max_dte=0`；过夜 `max_dte=7` 且客户端只看 ≥4DTE 的到期组），按用户自己的价格分档标注——$2-8 甜蜜区高亮（$2-4 净 +3.12%，n=544；$4-8 净 +3.44%，n=208）、**<$1 灰显警示**（净 −14.25%，n=72，费用吃掉权利金 5.56%）、$1-2（净 −1.28%，n=365）、>$8（净 −0.96%，n=218）；点差 >15% 沿用既有「流动性差」；
4. **仓位与张数**：标准仓位是**纯本地输入**（默认 $3,000，不落盘、不猜账户规模）→ 可买张数 `⌊仓位 ÷ (参考价×100)⌋`、手续费 `张数 × $3.29`（实测每张往返，区间 $3.29–3.31）、费/本金%（本金＝张数×参考价×100，与历史「费用占权利金」同口径）。$0.50 的合约买满 $3,000 是 60 张 → $197.40 手续费 → 6.58%；$4.00 是 7 张 → $23.03 → 0.82%。**便宜合约的陷阱在选择的那一刻就可见**；
5. **失效位提示**：中性一行「进场前先写下失效位；说不清就不开（结构止损）」+ 当日**已有观测值**（今日最高/最低、最近一段上/下行波段的时间窗）。**不计算任何进出场价位**。

### 11.6 车道清单的六处修复

| # | 问题 | 修复 |
| --- | --- | --- |
| 1 | 未填 DTE ⇒「标缺」 | 新增第四态 `requirement`：直接写出**今天的要求**（「今日只能 4-7DTE（今日无可用 0DTE）」/「今日只能 0DTE」），填了 DTE 才翻成 pass/fail。开仓时点同理，直接给出实时 ET 时钟 + 约束窗口 |
| 2 | 今日无 0DTE 只是清单里一行小字 | 整块警示：`text-h3` 大字「今日无 0DTE · 日内车道关闭」+ 一行证据（周二/周四 −2.61%/−3.74%，1DTE 当日 −4.97%，n=313）。同时**日内车道按钮置为不可选 + 原因**，默认落在今天真正成立的过夜车道 |
| 3 | 无标的的「财报回避」恒为标缺 | 从车道清单**删除**该行，下沉到盘中计划的逐标的卡片（那里一定有标的） |
| 4 | 额度计数恒为标缺 | Journal 只有导入的历史成交（截至 2026-07-20），永远不含今天。改为**手动计数** `useIntradayManualBudgetStore`（按 ET 日作用域）：「今日日内单 2/6 [+1][−1]」「当前过夜持仓 1/3 [+1][−1]」，到达上限转警示样式，超过上限如实显示（不夹回上限），明标「手动维护 · 按 ET 交易日自动归零」。移除面板对 `personal-edge.rule_compliance.daily_budget` 的依赖 |
| 5 | 满屏样板话 | 删除两个面板正文里全部的「本系统只读 / 不会下单」，只保留一行：**「按你自己的规则机械核对，不是买卖建议」**。长口径 caveat（样本仅 2026-04→07 一个市场状态、过夜 n=65 偏小、in-sample、前向验证见 /journal）内容一字未删，改挂 tooltip/aria-label |
| 6 | 冷启动 ⇒「标缺」 | 新增 `loading` 日型：首轮扫描在飞时显示「今日车道可用性读取中…」「ET 时钟读取中…」。**标缺＝查过了拿不到；读取中＝还没查完**，两者在 UI 上分开 |

### 11.7 诚实边界

引用的每个数字都带样本量与口径（build #3 干净口径，n=1,407，2026-04-21→07-31）。样本窗口仅 2026-04→07 一个市场状态（SPY 上行），规则由同一份样本内推出（in-sample），前向验证见 `/journal` 的规则遵守度。盘中计划只做**机械核对**：不排序、不打分、不给概率或胜率、不生成进出场价位，也不下任何单。

## 12. 16 分钟悬挂缺陷链修复：单飞截止时间 + Moomoo 断路器（2026-08-07）

2026-08 实盘复现的一次 16 分钟前端「转圈」由一整条缺陷链构成，经对抗性验证逐条确认（file:line 为修复前坐标），本节记录修复后的并发合同。

### 12.1 单飞（single-flight）新合同（`_get_or_compute_scan`）

| # | 缺陷（修复前） | 修复后合同 |
| --- | --- | --- |
| 1 | leader 无截止时间（`opportunities.py:617`）：45 秒租约只 504 跟随者，leader 自己可悬挂 16 分钟 | 工厂在共享有界线程池（16 workers）执行；**发起方与跟随方一样只等到租约到点**，随后抛 `OpportunityScanTimeoutError` → 可重试 504，孤儿计算继续在后台跑 |
| 2 | 迟到结果被丢弃（`opportunities.py:630`）：超租约完成的扫描被扔掉再抛 504——慢供应商下「永远算、永远丢」的活锁 | **迟到的成功结果一律发布**进完成态缓存并清账（`_finish_scan_flight`），下一次轮询直接命中；成功算出的扫描绝不丢弃 |
| 3 | 重复 leader（`opportunities.py:575`）：过期 flight 被逐出并接纳新 leader，旧工厂仍在跑，重复扫描自我放大 | **每个 key 同时至多一个工厂**：在途计算存续期间（哪怕租约已过期）后到请求只会有界等待或 504，绝不启动第二个工厂；在途计算完成后为所有人发布 |

配套：完成态缓存上限 32 → 128（逐标的 key 在 20 标的清单下互相挤兑）；此前不含 504 翻译的期权端点（overview/context/walls/daily-snapshot/events）补上统一的 `opportunity_scan_timeout` 504。

### 12.2 Moomoo 共享断路器（`src/services/moomoo_runtime.py`）

一台「卡死」的 OpenD（TCP 可连、RPC 悬挂）会让每次调用烧满 SDK 内部 12–20 秒上限。`MOOMOO_RPC_BREAKER`（进程级，无新增环境变量）：连续 **3** 次传输类失败（RPC 异常、连接/握手失败、错误详情含超时/断连标记）后打开，**60 秒**冷却期内所有热路径快速失败（快照 / 异动 / 到期日 / wall lane 获取 / history kline），冷却结束放行一个探针（探针自带 30 秒租约防悬挂半开态），成功即闭合。业务型拒绝（如 Unknown stock）不计入失败。调用方既有的 unavailable + reason fail-closed 语义原样保留。

### 12.3 车道可用性的 lane 获取短等待（G-5）

到期日读取只发生在日内榜单飞 leader 内，旧实现每个标的可在 wall lane 上排 30 秒队（8 × 30s 独占整个租约）。现在 lane 获取等待收紧为 2.5 秒；lane 正忙抛 `MoomooWallLaneBusyError` → 该标的以 `deferred_wall_lane_busy` 推迟到下一轮（**不缓存失败**，`deferred_tickers` 机制照常如实呈现），结论保持 unknown。

### 12.4 Tier-1 批量快照的未知代码恢复（G-6，LIVE 复现）

清单里 1 个 `Unknown stock` 会让整批（实测 69 个）代码全部失败。现在识别到未知代码型拒绝时：错误详情点名代码就过滤后重试，没点名就二分重试；额外调用 ≤3 次硬顶，预算内未解析的代码如实缺席——端点的 `snapshot_unresolved_symbols` 既有口径自动披露，绝不把单个坏代码的失败扩大成整批失败。传输类失败仍整批 fail closed（语义不变）。

### 12.5 其余修复（G-7a–e）

- **7a** 当日晋升账本只在总数硬顶裁剪后提交「真正深扫」的标的；被 `trimmed_by_total_cap` 挤掉的标的不再消耗当日/30 天配额记录。
- **7b** mom15 动量历史改为**时间制保留**（回看上限 + 2 分钟缓冲；240 条硬性兜底），且单层/两层路径的每轮 tier-1 快照都喂养同一份历史——工厂每分钟跑 >2 次时不再动量饥饿、闸门不再静默退回 v1。
- **7c** 扫描完成态缓存上限 32 → 128（见 §12.1）。
- **7d** `StockService` 历史/实时读取改用进程级共享 `DataFetcherManager`（按类身份缓存，测试 monkeypatch 自动重建）；5m 波段爆发车道不再每标的每轮新开 OpenD 连接。
- **7e** `MoomooFetcher` 的每笔 RPC 与健康检查 `close()` 持同一把生命周期锁，close 不再能在 RPC 执行中途拆连接。

以上并发行为均有确定性测试（慢工厂 + 短租约、假时钟断路器、混合有效/未知代码批次），见 `api/v1/tests/test_opportunities_endpoint.py`、`api/v1/tests/test_intraday_top_two_tier.py`、`tests/test_moomoo_runtime.py`、`tests/test_moomoo_options_snapshots.py`、`tests/test_moomoo_intraday_pagination.py`。

## 13. 盘中预热循环 + 扫描延迟诊断（2026-08-14）

### 13.1 问题

盘中工作台是纯请求驱动：单飞 leader 已被 45 秒租约约束（§12.1），但**冷路径**
（缓存空窗后的完整两层装配）实测 12.5 秒（冷）vs 0.01 秒（暖）。页面关过一阵、
TTL 过期、服务重启、显式刷新——任何缓存空窗都会让用户的下一次轮询吃满冷路径，
实盘时体感就是「页面卡了」。

### 13.2 服务端预热循环（`src/services/intraday_warm_cache_scheduler.py`）

`IntradayWarmCacheScheduler`：与 premarket/outcome host 同一模式的 daemon 线程
（start 幂等、stop 无界 join、quiet-unless-incident）。语义：

- **时窗闸门**：完全复用 `market_session_state`（`america_new_york_clock_v1`，
  不新增时钟逻辑）——工作日 04:00–20:00 ET（盘前→盘后）内预热，之外 idle
  零请求（周末/夜间每 tick 只是一次纯时钟判定）。
- **预热目标**：`warm_default_intraday_top_scan()` 以「无显式 symbols、无
  focus、默认 limit=5」解析出与页面默认轮询**完全相同**的 key/工厂
  （`_intraday_top_key_and_factory`，端点与预热共用，无平行实现），并以
  `bypass_cache=True` 走 `_get_or_compute_scan`——与用户点「刷新」同语义：
  绕过已完成 TTL 条目、仍加入同 key 在途请求。**join-not-duplicate**：用户
  在 leader 时预热只加入等待，绝不并发第二个工厂（有慢工厂确定性测试钉住）。
- **节奏**：`INTRADAY_REFRESH_INTERVAL_SECONDS`（默认 45，低于 30——基础
  单飞租约——钳制到 30）。装配超时（>45s 租约）折叠为 failed tick，孤儿计算
  照常迟到发布（§12.1 合同不变）。
- **断路器兼容**：`MOOMOO_RPC_BREAKER` 打开时工厂快速失败，预热 tick 折叠为
  `failed:<error_type>` 并保持节奏；host 只在状态迁移时各写一行 INFO
  （idle→warming、warming→failed、恢复），绝不逐 tick 刷屏、绝不刷 error。
- **不可区分性**：预热结果与请求驱动结果同缓存、同 TTL、同诚实字段——载荷
  本身零特殊化；唯一痕迹是完成态缓存条目的元数据（发起方 + 生成耗时）。

配置：`INTRADAY_REFRESH_SCHEDULER_ENABLED`（默认 false ＝行为零变化）+
`INTRADAY_REFRESH_INTERVAL_SECONDS`，由 Web/API lifespan 托管（与另三个
scheduler 同一 start/stop/异常隔离模式），修改后需重启进程。回滚＝去掉
env 开关（或置 false）重启。

### 13.3 扫描延迟诊断（additive）

`/opportunities/intraday-top` 响应新增两个 additive 字段（旧载荷缺席即缺席）：

- `generated_in_seconds`：该结果的**工厂墙钟耗时**（秒）。命中完成态缓存的
  响应报告**原始**生成耗时，不是本次请求耗时。
- `served_from ∈ {fresh, cache, warm_cache}`：fresh＝本次等到了一次工厂运行
  （leader 或加入在途计算）；cache＝命中请求驱动的完成态缓存；warm_cache＝
  命中预热循环写入的完成态缓存。

前端实时扫描表脚注渲染一行小字（如「本轮扫描耗时 12.4s · 预热缓存命中」，
`data-testid="scan-latency-footer"`）——之后任何「页面变慢」都能从截图直接
定位是生成慢还是缓存没接住。

## 14. 盘中机会提示器：Telegram 事实推送（2026-08-14）

### 14.1 诉求与架构

用户诉求：「盘前盘中的时候知道什么票有机会，比如高开的、放量的」——人不必
盯盘，手机收提示。实现为**预热载荷的观察者**（`src/services/
intraday_opportunity_alerter.py`）：§13 的预热循环每 tick 经 single-flight
算出两层扫描载荷后，把该独立深拷贝交给提示器做纯内存检测（
`warm_default_intraday_top_scan(payload_observer=...)`），**零新增供应商
请求、零新增时钟逻辑**。因此提示器**依赖预热循环**：
`INTRADAY_REFRESH_SCHEDULER_ENABLED=false` 时没有载荷可观察，提示器零检测。
检测窗口即预热窗口（工作日 09:00–16:15 ET）。发送复用既有
`TelegramSender`（`src/notification_sender/telegram_sender.py`），不新建
发送通道。

### 14.2 六条检测规则（v1 启发式，全部只读载荷已有字段）

阈值沿用扫描面板已校准的常量，**全部是 v1 启发式，不是验证过的交易边际**；
数字与口径原样透传，不重算（唯一乘法是位移美元换算，见规则 4）：

1. **高开/盘前异动**（仅 `session_state="premarket"`，预热窗口内即
   09:00–09:30 ET）：`|pre_change_percent| ≥ 2.0` →
   「{T} 盘前 {+x.x}%」。盘前异动最强的标的恰好会被闸门晋升出宽层，
   两层扫描因此把同一批快照的 `pre_change_percent` 原样带到深度候选上
   （additive 字段，2026-08-14 起在 `IntradayTopCandidate` 声明并随响应
   序列化：仅盘前时段有值——快照的 pre_* 列开盘后仍残留当日早间读数，
   非盘前一律置 None，防止陈旧读数冒充现时；前端扫描表盘前时段以它作
   涨跌% 主行）。
2. **放量**（仅 `session_state="regular"`）：候选当前 15 分钟窗口
   `vol_norm ≥ 2.0` 且爆发分 `score ≥ LEG_MEDIUM_MIN_SCORE(2.5)` →
   「{T} 放量 {vol_norm:.1f}× · 15分推力 {thrust:+.1f}% · 爆发分 {score:.1f}」。
3. **强波段入账**（仅 `quote_session_scope="current_session"`）：候选
   `legs` 中**新出现**的 `grade="strong"` 波段（进程内按
   (ticker, start_et) 记忆）→「{T} 强波段 {start_et} 分 {score:.0f} {方向}」。
4. **位移达标**（仅 current_session）：`recent_displacement.net_move_atr`
   首次达到 ±0.5 ATR 参考线（优先读载荷自带 `survival_line_atr`；每标的
   每方向每日一次）→「{T} 近30分位移 {±$X.XX}（{±0.xx} ATR）」。美元换算
   沿用前端扫描表同一口径：仅 `atr_basis="atr14_daily"` 时 `net × atr14`，
   其余标尺不可横向比较，只报 ATR 值。
5. **日型提醒**（每日一次）：`lane_availability.day_type` 一旦不再
   `unknown` →「今日有 0DTE：日内车道可用」或「今日无 0DTE：过夜日 ·
   日内关闭」。未知≠没有，unknown 期间不提醒。
6. **盘前期权大单**（仅 premarket；每标的每日一次，2026-08-14 起）：
   候选 `option_activity.max_single_turnover ≥ $1M`
   （`PREMARKET_LARGE_PRINT_MIN_TURNOVER`，与 §15 面板同值的 v1 圆整
   启发式）→「{T} 期权大单 $x.xM（上一时段异动页最大单笔）」。盘前
   读到的异动页承载的是上一时段成交，文案如实声明口径；供应商
   dominant_sentiment 是整页多数、不是该笔的分类，因此**不随大单转述**。
   注意：预热载荷只带「偏斜/大单」这对事实里的**大单**一半——call/put
   比例在 option-walls 端点上、不在预热扫描载荷里，本模块不为它新增
   取数路径，偏斜没有对应提示（有意跳过，见 §15 面板）。

黑名单标的（PLTR/AMD/QQQ/SMCI，后端单一出处
`src/opportunities/blacklist.py`，与前端 `laneChecklist.ts` 的
`BLACKLIST_TICKERS` 同源同值需保持同步）**不压制**，只附加
「⚠️ 你的历史亏钱标的」标注——系统标注，用户过滤。

### 14.3 防骚扰与诚实合同

- （标的 × 规则）× ET 日去重，进程内、ET 日期翻转清零；**中途重启后最坏
  情况会重复提示**（接受的代价，不落库）。
- 单 tick 多条提示合并为**一条** Telegram 消息（头行「【盘中提示 ·
  HH:MM ET】」取载荷 `as_of` 的 ET 时刻）。
- 全局日上限 `INTRADAY_ALERTS_MAX_PER_DAY`（默认 20，最小 1）：触顶后在
  同一条消息内补「今日提示已达上限 N 条」，当日不再发送。
- 每条消息末尾固定一行「事实描述，非买卖信号」；全文永不出现概率、建议、
  目标价或买卖措辞。
- 发送在独立 daemon 线程消化有界队列（满则丢弃本 tick 消息并按状态变化记
  一行日志）；Telegram 失败/恢复同样只在状态变化时各记一行
  （quiet-unless-incident），任何异常都不上抛、不阻塞预热节奏。

配置：`INTRADAY_ALERTS_ENABLED`（默认 false ＝行为零变化）+
`INTRADAY_ALERTS_MAX_PER_DAY`，由 Web/API lifespan 在预热调度器分支内托管，
需要 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`，修改后需重启进程。
回滚＝去掉 `INTRADAY_ALERTS_ENABLED`（或置 false）重启。

## 15. 盘前期权异常面板：昨日 call/put 偏斜与大单事实（2026-08-14）

### 15.1 诉求与定位

用户诉求（逐字）：「盘前的涨跌呢？」「期权异动我不要盘中的，这个是作为
复盘的……开盘前我要知道有什么票的期权不对劲，比如特别大单或 call/put
比例不一样，参考专业人士的」。专业盘前简报把**上一时段**的异常期权活动
（大单 prints、P/C 偏斜）当作盘前事实陈列——本面板照此定位：全部是
**事实描述**，不是信号。同日三处联动：

- `/intraday` 侧栏原**期权异动 feed** 移入 Journal 仓位复盘面
  （`apps/dsa-web/src/components/journal/JournalOptionEventReview.tsx`
  包装既有 `IntradayOptionEventFeed`，进页签只读一次、不轮询；组件本体
  与「分类不证明开平仓方向」脚注原样保留）；
- 侧栏换成**盘前期权异常**面板
  （`apps/dsa-web/src/components/opportunities/PremarketOptionAnomalyPanel.tsx`）；
- 扫描表「涨跌%」列盘前时段改示真实盘前变动（见 §2.8 与 §14.2 规则 1 的
  `pre_change_percent` additive 契约）。

### 15.2 数据路径（零新增取数路径，逐檔 fail-closed）

面板只覆盖深度层名单（`universe_scan.deep_lane`，≤8 檔；单层模式退回
候选列表），绝不向宽层全清单扇出：

- call/put 成交量比与 OI 比：既有 `POST /opportunities/option-walls`
  （G-24 比例，0–45 DTE 窗口），每批 ≤5 檔——2026-08-15 起墙读取抽到共享
  hook `apps/dsa-web/src/hooks/useDeepLaneOptionWalls.ts`，与扫描表
  「期权墙」列共用**同一次**请求与缓存（名单派生/分批/在途去重只发生一次）；
- 最大单笔成交：既有 `POST /opportunities/option-events`，每批 ≤3 檔、
  每檔最近一页 ≤10 条，取页内 turnover 最大者。

8 檔名单合计最多 2+3 次请求；API 层既有 sessionCache（墙 5 分钟 /
异动 30 秒）+ 在途去重，深度层名单变化才重取。收起态零请求。任何一路
失败该檔显式「量比标缺 / OI比标缺 / 大单标缺」，绝不用 0 冒充；比例
value=null 时逐字带服务端 reason（分母为 0 等），不折算成 0 或无穷大。

### 15.3 展示节奏与阈值（v1 启发式，未经验证）

- **盘前 + 开盘 30 分钟**（`session_phase` ∈ premarket / opening_probe）
  自动展开（此时它是简报）；其后默认收起为次要参考（昨日旧事实），可
  手动展开/收起。
- 「偏斜」章：量比或 OI 比 `≥3` 或 `≤0.33`（≈1/3 对称；
  `OPTION_SKEW_RATIO_HIGH/LOW`）。圆整数只求可辩护的保守取值，不是
  统计校准产物。
- 「大单」章：单笔金额 `≥ $1M`（`LARGE_PRINT_MIN_TURNOVER_USD`，与
  §14.2 规则 6 同值）。2026-08-15 依用户反馈（「把大单的改成 CRWV大单
  Call $7.9M，明显一点」）章面把**方向写进标题**：「大单 Call $7.9M」
  （方向缺失时如实只给金额）；「最大单」一行给全明细（strike/到期/DTE/
  张数/金额与**供应商分类**——偏多/偏空/中性＝Moomoo 标签，绝不改写为
  方向结论），不再另起「大单明细」行重复金额。
- 未命中阈值的比例与最大单照常陈列（<$1M 只是不盖章），无异常时显式
  「无异常标记」。
- 版式（2026-08-15）：一行一个事实、标签列对齐（量比 C/P / OI比 C/P /
  最大单）；比例无定义时可见行只留紧凑短语——服务端
  `该窗口内无有效合约，无法计算比例` → **「近月窗口无合约」**、分母为 0
  → 「分母为 0」、其余 → 「无法计算」——服务端原因逐字连同「为什么」
  （比例窗口只扫 0–45 DTE 近月；CRWV 这类大单落在数百 DTE 的远月 LEAP
  上时窗口内没有任何有效合约，比例无定义、如实标缺，不折算成 0 或
  无穷大）挂在该短语的 ⓘ tooltip。

### 15.4 诚实边界（2026-08-15 起收进标题行 ⓘ tooltip，原文逐字）

以下三条 + 「事实描述，非信号」定位句合并为
`PREMARKET_PANEL_HONESTY_NOTE`，逐字收进面板标题行的 ⓘ（`InfoHint`）
tooltip/aria-label——隐藏 ≠ 删除（见 §16 界面披露策略）；可见界面只保留
逐读数的标缺原因与 as-of：

- OI＝上一清算时段结算；成交量与大单＝供应商快照/异动页的会话累计——
  盘前绝大多数期权尚无成交，此刻读到的即上一时段事实，缺失显式标缺；
  大单取自最近一页异动（≤10 条/檔），**不是全时段全量最大**。
- 阈值全部是 v1 启发式，未经验证。
- 「分类来自供应商，不构成方向证明；比例异常≠会涨会跌——该假说在你的
  数据上尚未检验（期权墙逐日快照正在积累样本）」。

## 16. 界面披露策略（2026-08-15）

用户反馈（逐字）：「这种话你加在界面里干什么：按你自己的规则机械核对，
不是买卖建议（样本与口径边界 ⓘ）——不要急着撇清关系」。

策略：**诚实文本一字不删，但不占可见 chrome**。每个面板最多保留一枚小
ⓘ（`apps/dsa-web/src/components/common/InfoHint.tsx`，包共享 Tooltip），
挂在标题/角落，tooltip 与 aria-label 逐字携带完整诚实边界原文；常驻的
披露整句（「不是买卖建议」「机械核对」「本系统只读」「事实描述，非信号」
之类）从可见正文/footer 移除。**保留可见**的例外：as-of 时点、标缺原因、
逐读数的口径标注（它们是数据标签，不是免责声明），以及后端 Telegram
消息尾行（后端未动）。本轮落地面：

- `LaneChecklistPanel`：footer 定位句 → 标题行 ⓘ（`LANE_CHECKLIST_HONESTY_NOTE`）；
- `IntradayPlanPanel`：header 定位句 → 标题行 ⓘ（`PLAN_HONESTY_NOTE`）;
- `PremarketOptionAnomalyPanel`：header「事实描述，非信号」+ footer 三条
  → 标题行 ⓘ（`PREMARKET_PANEL_HONESTY_NOTE`，见 §15.4）；
- `RuleCompliancePanel`：底部 limitations 常驻列表 → 标题旁 ⓘ（端点原文
  逐字）；车道判定与样本口径两段保留可见（表内数字的基准标注）；
- `IntradayScanTable` footer：可见行只留数据口径，「不是信号/非预测」句
  并入「完整口径」切换（`RELOCATED_SIGNAL_DISCLAIMER`，展开逐字可见）；
- `RulesPage` 横幅：整条警示横幅 → ⓘ + 「打开规则遵守度面板 →」链接
  （横幅文案仍由后端下发、逐字进 tooltip）；
- `IntradayDisciplineStrip`：本就 tooltip-only，未改。

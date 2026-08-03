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
3. **日内扫描表**（核心）：`POST /api/v1/opportunities/intraday-top` 的盘中滚动 Top 5。列：标的 / **当前爆发**（首个数据列：爆发分 + 方向箭头 + 15 分钟推力%）/ **速度**（v3：加速/减速/持平/标缺）/ **今日波段**（如「2 波：09:40↓ · 15:15↑」，每波窗口/推力/爆发分明细以可访问 aria-label 附带；休市显示最近一个交易时段）/ **形态**（v4 styleMatch：S1/S2/S3 相似度徽标，见 §2.10）/ 现价+当日%（as-of）/ 缺口 / 量能节奏 / VWAP 位置 / **大盘**（v3：顺势/逆势/标缺）/ 波幅扩张(ATR) / **财报**（v3：回避窗内醒目「财报 N 天内 · 期权贵」badge）/ 期权异动（N 笔·偏向·最大单）/ 研究状态；默认排序＝服务端排名（盘中爆发分优先），列头可点做客户端排序（第三次点击回到服务端排名），行点击进入 `/regime/opportunity/:ticker` 即时扫描详情（不绑定 snapshotKey）。
4. **期权异动 feed**：跨自选池、按时间倒序的最近异动成交（接口响应内有界 ≤20 条）：时间 · 标的 · Call/Put · 行权价/到期 · 金额 · Moomoo 情绪分类，底部固定标注「分类不证明开平仓方向」。

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

**异动闸门（documented v1 heuristic，`gate_basis=abs_change_percent_then_turnover_v1`）**：按 `|当日涨跌幅|` 主序、成交额次序、代码字典序兜底晋升前 K 档进入深度层。缺涨跌幅（快照未解析/缺前收）的行不可晋升——闸门绝不以 0 涨跌冒充平静。**计划钉选**：当日已冻结盘前计划的标的始终占深度位、不占 K 名额（不在清单里也会并入同一批快照）；计划标的不重复参与异动排名。闸门不是信号：晋升只决定「谁被深度分析」，不代表方向或质量结论。

**盘前口径（ET 04:00–09:30 工作日，`gate_basis=premarket_pre_price_change_then_pre_turnover_v1`）**：盘前时段 Moomoo 常规快照字段（现价/涨跌幅/量/额）仍指向**上一常规时段**——若照常按 `change_percent` 排序，深度榜会复现昨天的异动而不是今晨盘前的真实异动。因此盘前闸门与宽层剩余行排序改用 pre_* 字段：`|pre_change_rate|`（盘前价相对上一常规收盘的百分比，可为负）主序、`pre_turnover`（盘前成交额）次序；缺盘前字段的行不可按盘前异动晋升、在宽层恒排最后——语义与常规口径一致，绝不以 0 冒充「平静」。宽层行 additive 携带 `pre_change_percent` / `pre_turnover`（快照缺列＝null，绝不 0 回填）。若整批快照都无盘前字段（如快照权限差异），闸门**显式回退**常规口径并在 `universe_scan.gate_warnings` 携带 `premarket_fields_unavailable_ranking_reflects_prior_session`——如实声明「当前排序反映上一常规时段」，绝不静默假装在按盘前排序。前端：盘前口径下页头与 footer 附「盘前异动排序（盘前价 vs 前收 · 盘前成交额次序）」，仅快照行展示「盘前 ±x%」与盘前成交额、缺盘前字段的行显式「盘前标缺」；回退时页头与 footer 显示警示「盘前字段不可用 · 当前排序反映上一常规时段」。

**深度层（tier-2）**：完全复用 §2.8–§2.10 的既有 v4 管线（会话快照字段直接复用宽层同一批快照，零新增快照请求；5m 波段爆发 / styleMatch / 速度 / 期权异动 / 财报 / 临期合约资格照旧）。深度层名单已由闸门有界（K + 计划钉选），因此候选**全部返回**、不再按 `limit` 二次截断（`include_all_candidates`，否则「已深度分析却无声消失」）；`requested_limit` 仍如实回显。

**K 线额度语义（本设计的正确性支点，实测自 Moomoo 官方 API Limits）**：深度层 5m K 线经 `StockService.get_history_data` → `DataFetcherManager.get_intraday_data` 优先命中 Moomoo `request_history_kline`（失败回退 yfinance）。该接口的配额是 **30 天滚动窗口内的去重标的数**（账户档位 100/300/1000/2000；同一标的 30 天内重复请求不再扣额；同一标的的日线/5m 等不同周期只记 1 个额度）。因此真实约束是「30 天内晋升过的去重标的数」，其上界＝清单长度（69 档清单 < 最低档位 100）；为防单日 movers 高频换血，另设**每 ET 日新晋升去重标的数上限 30**（内部常量 `_INTRADAY_DEEP_DAILY_DISTINCT_CAP`，非环境变量）。触顶后新标的当日只保留宽层快照行，响应显式标注 `day_promotion_cap_reached`，前端给出警示行——绝不静默丢弃。财报日历仍是一次 Finnhub 区间调用覆盖任意大小 universe（逐标的匹配为字典查找）。

**每周期请求预算（两层模式，缓存全冷）**：1 次批量快照（≤清单+计划+SPY ≤ 206 codes，单请求）+ 深度层每档 1 次 5m K 线（60 秒逐标的 TTL，≤4 并发）+ 深度层每档 1 次有界异动页（30 秒 TTL 与 option-events 端点共用）+ 日线派生每档 900 秒记忆 + 财报 1 次/小时。宽层非晋升标的零 K 线、零日线加载。

**响应合同（additive）**：`universe` 仍为 `list[str]`（= 宽层实际扫描的全部标的，类型不变——设计说明：原要求把 mode 等放进 `universe` 字段，但该字段既有类型为列表，改型即破坏合同，故新增 `universe_scan` 块）。`universe_scan`：`mode` / `gate_basis` / `gate_warnings[]`（闸门降级警示，见盘前口径小节）/ `watchlist_total` / `watchlist_truncated` / `scanned_total` / `deep_lane_count` / `deep_lane_max` / `deep_lane[]`（含晋升原因与异动名次，深度层名单本身绝不无声截断）/ `plan_always_include` / `gated_out_count` / `snapshot_unresolved_symbols`（供应商无返回行的标的，如实点名）/ `day_promotion_cap(_reached)` / `snapshot_only[]`（宽层行，按闸门同口径降序、标缺行恒最后；盘前口径下 additive 携带 `pre_change_percent` / `pre_turnover`）。每个深度候选带 `scan_tier="deep"` + `deep_lane_reason`（计划钉选 or 异动 #n）。

**前端**：页头计数改为「全清单 N 檔快照 · 深度分析 K 檔」；深度行标的格附「计划钉选」/「异动 #n」徽标；表格下方新增「仅快照 · 未做深度分析（N 檔）」紧凑列表（ticker + 涨跌% + 成交额，快照未解析与日上限触顶各有显式警示行）；footer 固定附「全清单 N 檔快照 · 深度分析前 K 檔（|涨跌|→成交额）· 其余仅快照」。单层模式渲染完全不变。

**诚实边界（响应 `limitations` 固定携带）**：两层扫描只有晋升标的做深度分析，其余仅快照、深度字段一律缺席；闸门为 v1 启发式（|涨跌幅|→成交额），不是信号，晋升不代表方向或质量结论；K 线额度为 30 天滚动去重标的数配额，日晋升护栏触顶如实标注。周内看板、市场脉搏与既有单层扫描不受任何影响。

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

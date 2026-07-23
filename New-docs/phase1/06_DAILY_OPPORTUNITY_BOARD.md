# Phase 1.6 · 每日机会研究看板

> 状态：Watchlist Top 5 确定性基础榜、Top 5 直出 Moomoo 0–45 DTE 期权墙、全候选期权概览、单票专业研究页、Moomoo 最近交易时段异常期权成交，以及盘前自动保存的不可变机会快照与 5/20 XNYS 交易日结果学习已接线；场外/TRF 大额成交仍未配置，策略 edge 仍未验证。
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
- `POST /api/v1/opportunities/option-context`：保留最近到期、最接近现价的 Call 单合约 IV，用于当前选中标的的合约上下文；它与 underlying overview 的供应商统计口径分开，失败或前端超时不影响基础候选。
- `POST /api/v1/opportunities/option-walls`：单次最多批量读取 5 个标的的 Moomoo 期权链和动态快照；默认只纳入 0–45 DTE 标准合约，分别返回 Top 3 Call/Put OI 墙、当日累计 Volume 墙和 unsigned gross gamma concentration 墙。页面在基础清单完成后自动为 Top 5 渐进加载墙位并直接显示 Call/Put 摘要，不再要求逐票点击；墙失败或覆盖不足不会阻断基础候选，也不参与基础排名。
- `POST /api/v1/opportunities/option-events`：最多接收三个合格美股 underlying，使用 Moomoo `get_option_event` 的 `OWNER_LIST` 服务端过滤逐标读取最近异常期权成交；每标默认返回 5 条、最多 10 条，并独立返回 `ready / empty / not_configured / unavailable`，任一标的失败不会阻断基础榜或其他标的。
- `POST /api/v1/opportunities/snapshots/ensure`：页面加载每日清单后安全、幂等地检查是否应保存“今日研究版本”；只在上一完整 XNYS 日线收盘后、对应下一 XNYS 交易日开盘前写入，周末、休市日、盘中和盘后都跳过，刷新不会重复写入。`snapshots/freeze` 仍作为显式 API 保留，快照列表、成熟结果评估和学习摘要接口保持不变。
- `/regime` 顶部“今日机会研究”：主表只展示 Top 5，并直接列出结构、量能、IV/Rank、Call/Put 墙、研究状态以及可复现的确认/失效观察；其余候选、排名外增强和 5D/20D 学习口径默认折叠。页面明确显示本次来自本地 Watchlist 还是服务端默认池，并提供“管理 / 导入自选”入口。
- `/regime/opportunity/:ticker` 单票详情：首屏先给出压缩后的专业摘要、1/5/20 交易日 IV 模型终值区间、关键价位和 underlying K 线；供应商 IV/HV、0–45 DTE 墙与异常成交收进分域标签页，避免同一指标在多个卡片反复出现。详情用于形成 setup、trigger、invalidation、流动性与风险检查，不把任何单项指标包装成自动买卖信号。
- 研究状态由基础证据直接分层：新鲜完整日线同时具备一致方向结构与至少一项独立量能/成交额确认时为 `research_ready`（重点研究）；只有其中一侧成立或方向证据冲突时为 `watch_only`（等待确认）；结构混合且无量能支持、日线过期或权威 Regime 为 `no_trade` 时为 `context_only`（背景观察）；核心日线不足或标的不支持才是 `blocked`。缺失或降级 Regime 不再把所有标的统一压成等待状态；`research_ready` 也只是研究资格，不是入场信号。
- 基础排名只使用上一完整交易日的 OHLCV/成交额、EMA8/13、相对量能、近期结构和已保存的当日 Regime；不调用 LLM。期权概览、墙和异常成交目前是并列研究上下文，不会悄悄改变基础排序。自动保存只写隔离的 `opportunity_*` 研究快照，不写 Journal 或交易事实表。
- 第一阶段 universe 只支持美股期权 underlying；A 股、港股或其他不支持的符号按候选 `blocked`，不会套用纽约收盘时间或 SPY Regime。
- 同一请求复用一套行情 manager，最多四路日线并发；服务端使用 30 秒 TTL + single-flight，前端使用 5 分钟缓存和最新请求保护，减少重复初始化、日志和旧响应覆盖。
- Moomoo 期权概览使用单次只读 Quote batch 覆盖全部合格候选，并按“标的集合 + Moomoo 启用状态 + ET 市场日期”缓存和 single-flight；单合约 IV、墙和异常事件仍各自独立限流、缓存与降级，某一重型扫描不会阻断基础清单。
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

期权墙 UI 使用主表格承载可比较字段，详情区解释公式、来源、coverage/as-of 与不能推断的内容，并显式显示“计算可复现、策略有效性未验证”。墙位目前只是独立研究上下文，不进入确定性基础榜排序，也未持久化为历史序列或完成交易结果回测。

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

机会榜只承担横向筛选，单票详情承担纵向研究。详情页按“先结论、再图表、最后证据明细”组织，默认首屏只保留决策最相关的信息：一句话专业摘要、方向背景与 setup、上一完整交易日高低点、IV 所处状态、1/5/20 交易日模型区间、最近的关键墙位、反面证据和仍未知的数据。摘要只能压缩下方已经存在的结构化证据，不允许由单一异常成交、OI 墙或高 IV 生成伪确定性的涨跌结论、胜率或交易指令。

详情页提供 `1m / 2m / 5m / 15m / 30m / 1h / 1D` 七档 underlying K 线，并在当前可见时段的 close 上计算 EMA8 / EMA13。美股分钟图默认只显示纽约常规时段 `09:30–16:00 ET`，可显式切换到含盘前盘后的 `04:00–20:00 ET`；筛选按 `America/New_York` 自动处理夏令时，切换后 EMA8 / EMA13 立即基于新的可见 bars 重算，顶部 K 线时点也同步显示当前可见最后一根，避免常规时段图误报 `20:00 ET`。日线不显示交易时段切换。分钟图用于观察执行环境，日线用于结构背景；最终 bar、EMA 或墙位都不是已验证的入场触发器，页面不能事后把它们描述成成交时已经可见的信号。首屏之外的信息按用途收进四个标签页：

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

页面状态分为三层：

- **基础扫描**：美股支持范围、至少 21 根新鲜完整日线、来源与 as-of；缺失时显示具体的“行情源暂不可用 / 日线不足 / 日线已过期 / 标的不支持”，不再统一称为“证据不足”。
- **市场背景**：Regime 可用于风险环境解释；缺失或降级时仍可浏览股票量价结构，但不得把它冒充已通过的市场门禁。
- **排名外增强**：异常期权成交、期权墙、完整逐笔期权流/NBBO、FINRA ATS/TRF 背景与未确认 Playbook。它们影响后续研究深度或个人风格匹配，当前缺失不阻断基础扫描，也不在每只股票上重复显示成待补核心证据。

`hard_gates.status=unknown` 不等于失败；只有 `failed` 才进入基础门禁失败摘要。`no_trade` 在输入质量合格时表示“市场风险关闭”，不是权限或证据错误。页面保留完整 readiness/provenance 明细，但将可选增强收进折叠说明，首屏优先回答候选是否可扫描、数据来自哪里、为什么值得观察。

### 2.6 Watchlist 来源与 TradingView 边界

机会清单优先使用 Web 本地 Watchlist，按用户顺序最多扫描前 20 只，再从确定性基础排名中展示 Top 5；没有本地自选时才使用服务端 `STOCK_LIST`。`/watchlist` 支持合并导入 TradingView Advanced View 官方导出的 TXT：解析逗号或换行分隔的 `EXCHANGE:TICKER`，忽略分组标题，并把受支持的美股 ticker 去重加入现有自选。

TradingView 面向个人网站账户没有公开的自选列表 REST API；其公开 REST API 是给 broker 集成使用，Charting Library 的 Watchlist API 只管理嵌入式 Trading Platform widget 的 Watchlist，不能当作用户在 tradingview.com 上的个人列表同步接口。因此当前不保存 TradingView 登录信息、不抓取私有网页，也不声称实时双向同步。用户在 TradingView 导出 TXT 后导入，是官方支持、可审计且不依赖浏览器会话的边界：

- [TradingView：导入或导出 Watchlist](https://www.tradingview.com/support/solutions/43000487233-how-to-import-or-export-a-watchlist/)
- [TradingView：个人数据 API 说明](https://www.tradingview.com/support/solutions/43000474413-i-need-access-to-your-api-in-order-to-get-data-or-indicator-values/)
- [TradingView Charting Library `IWatchListApi`](https://www.tradingview.com/charting-library-docs/latest/api/interfaces/Charting_Library.IWatchListApi/)

5D/20D 区块是前瞻结果跟踪，不是今日候选筛选门禁。新系统刚开始积累不可变盘前快照时，成熟样本为 0 属于正常状态；主页面只显示一行进度，完整样本门槛与 cohort 口径放在折叠区，避免把尚未成熟的研究统计误当作当天不可用。

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

一次普通扫描生成内存中的 `OpportunityRun`，每个候选带稳定 `candidate_id`。`POST /api/v1/opportunities/daily` 本身仍是零写入预览；Web 在取得清单后另行调用 `POST /api/v1/opportunities/snapshots/ensure`。`ensure` 只有在 run 的 `market_date_et` 恰好等于候选下一 XNYS entry session，且请求严格早于该 session 常规开盘时，才保存该日第一个正式研究版本。周末、节假日、盘中与盘后返回 `outside_window` 而不写库；相同日槽再次加载返回 `existing`，不覆盖既有不可变版本。

当前持久化使用与 Journal 隔离的三张表：

- `opportunity_snapshot_runs`：冻结 market date、universe、limit、signal/schema/freeze/playbook 版本、排序方法、as-of、完整 payload 与内容哈希；
- `opportunity_snapshot_candidates`：冻结名次、方向上下文、setup/evidence/readiness、上一完整交易日参考价与数据来源；
- `opportunity_candidate_outcomes`：只追加已经到达目标 XNYS 收盘的 5/20 交易日标的结果、SPY 对照、输入 bar 哈希与 provenance。

三张表都安装 SQLite `UPDATE / DELETE` 拒绝 trigger；run 与其 candidates 在同一事务写入。结果以“candidate + horizon + evaluator version + complete/partial state”为不可变槽位：完全相同重试幂等，冲突内容拒绝；缺部分路径或 SPY 附件但已有目标收盘时可以先追加 `partial`，后续完整来源只能追加新的 `complete` 行，不能原地修订。

保存、只读与评估接口如下：

- `POST /api/v1/opportunities/snapshots/ensure`：Web 默认入口；合法盘前窗口自动保存一次，其他时间只返回状态，永远不解锁或调用交易接口；
- `POST /api/v1/opportunities/snapshots/freeze`：保留给显式受控流程的低层入口；它创建的是审计快照，是否具备结果学习资格仍由 XNYS 因果窗口判定，不能用来盘后补造正式样本；
- `GET /api/v1/opportunities/snapshots?limit=...`：只读列出冻结时间、合格样本数和 5/20 日成熟/待定/缺口进度；
- `POST /api/v1/opportunities/snapshots/{snapshot_key}/evaluate`：只为已成熟目标交易日追加结果；未成熟、日历异常或关键数据缺失不写伪结果；
- `GET /api/v1/opportunities/learning-summary`：只读返回分 cohort 的描述统计和门槛状态，固定 `auto_adjustment=false`。

当前自动保存版本只包含确定性基础榜；页面异步取得的 underlying overview、ATM IV、期权墙和异常成交尚未进入冻结 payload，也不会被事后拼接到旧快照。`run_type` 仍固定 `morning_prior_close`；未来 `open_refresh / intraday_refresh` 必须使用新合同，不能把上一完整交易日 OHLCV、T-1 OI 或延迟数据显示成盘中刚发生的信号。

### 5.2 XNYS 因果时间与结果口径

冻结候选的参考交易日记为 `S`，下一 XNYS 常规交易日记为 `E`。只有 `S` 正式收盘后、且严格早于 `E` 常规开盘的 freeze 才具有 `validation_eligible=true` 并进入正式学习样本。早于 `S close` 无法证明输入是完整日线；到达或晚于 `E open` 才冻结（包括盘中或盘后补冻）已经看到入场交易日信息，因此快照仍为审计记录，但永久排除于结果样本。

交易日、节假日、提前收盘和常规开收盘时刻全部由 `exchange-calendars` 的 `XNYS` 日历解析，日历不可用时 fail closed，不以工作日加减近似。依赖已在 `requirements.txt` 中，本机验收版本为 `exchange-calendars 4.13.2`。

5D/20D 的 `D` 是 XNYS session，不是自然日；`E` 是路径第 1 日。每个合格候选保留两套标的收益代理：

- `S close → 第 5/20 个 session close`：用冻结的参考 close 作分母，并重新抓取同一 `S close` 仅作复权/修订一致性审计，绝不静默替换冻结值；
- `E next-open proxy → 第 5/20 个 session close`：把 `E` 常规开盘价作为统一、不可声称可成交的入场代理，并从它计算路径 MFE/MAE；
- 同窗口保存 SPY 的 close 与 next-open proxy 收益，只有标的和 SPY session 严格对齐且来源连续时才计算相对 SPY；
- LONG/SHORT 分别按方向签名。5D signed close return `≥ +0.5% / ≤ -0.5%` 记 `CONTEXT_HIT / CONTEXT_MISS`，中间为 `NEUTRAL`；20D 对应阈值为 `±1.0%`。

这些都是 underlying 的观测结果，不是期权收益，不包含 strike、到期、IV、Delta、spread、成交滑点或合约乘数，也不证明用户实际看见、触发或执行了交易。`E open` 只是统一代理，不是 broker fill；结果表不写 Journal，也不与真实仓位 P&L 混用。

方向为 `mixed / unknown` 时仍可保存原始 close/entry proxy 路径，但 `signed_*`、MFE、MAE 和 signed relative-SPY 必须为空，只使用 `NON_DIRECTIONAL / DIRECTION_UNKNOWN` 标签，不进入方向命中率分母。参考 close 发生复权/修订不一致、冻结与评估的数据源不连续、重复/缺失 session、目标 close 缺失或 SPY 无法严格对齐时，系统 fail closed：结果保持 pending/data gap，或把有目标 close 的 partial 结果单列；关键质量缺口从学习摘要排除，不跨来源补值。

### 5.3 Cohort 与学习护栏

5D 与 20D 分开统计。不同 `signal_version`、`playbook_version`、universe、requested limit、ranking/freeze policy/scope，以及不同结构 setup signature、Regime、direction 的记录生成不同 cohort，绝不为了凑样本混算；同 ticker、同信号交易日、同 horizon、同 cohort 也只计一次。

门槛固定为：

- 同一 cohort、同一 horizon 至少有 10 个 LONG/SHORT 成熟方向样本，才显示描述性 `CONTEXT_HIT` 比例；不足时只显示 collecting 和样本数；
- 至少 20 个成熟方向样本，且来自 20 个不同 signal sessions，才标记 `investigation_ready`，含义仅是可以人工调查；
- 任意样本量都不会自动调整排名或证据权重。若人工形成新规则，必须另起版本、做 walk-forward 验证并由用户确认，不能回写旧快照。

本阶段有意不生成 `TRUE_POSITIVE / FALSE_POSITIVE`、`missed opportunity` 或 `regime mismatch`：前两者会把“方向上下文”误装成完整预测，missed opportunity 需要冻结的 `trade_taken=false` 与预定义触发条件，regime mismatch 需要预先定义的失效规则与因果证据；当前快照都不具备这些事实。这里只使用 `CONTEXT_HIT / CONTEXT_MISS / NEUTRAL / NON_DIRECTIONAL / DIRECTION_UNKNOWN` 的描述标签，不做交易归因。

正式数据库改动前保留了 `data/backups/stock_analysis-pre-opportunity-outcomes-20260722.db` 本地回滚备份；`data/` 已由 `.gitignore` 排除，该数据库及其 WAL/SHM 不进入仓库。

## 6. 后续交付顺序

1. **期权可交易性第二层**：当前已交付全部合格候选的 Moomoo underlying overview、当前选中标的的最近到期 ATM Call 单点 IV，以及默认 0–45 DTE 的 OI/当日 Volume/unsigned gross gamma concentration 墙；下一步建立逐字段 nullable 的 `ATMContractContext`，补 bid/ask、size、mid、spread%、delta、各自 as-of/质量和权限，只称“合约流动性/结构上下文”。
2. **波动率结构与异常成交合格化**：underlying IV/IV Rank/HV 概览和 `get_option_event` 只读接入已完成；下一步补期限结构、skew 与预期波动区间，并保存不可变事件快照、测量源时间到抓取时间的实际延迟、处理重复/修正与权限变化，再以样本外结果决定是否进入 ranking。screen 接口可另行验收，不能由 overview 或事件接口成功推定其可用。
3. **TradePlan / Playbook**：用户确认 setup、DTE、delta、时段、事件和风险规则后，才把 `style_match` 从 `unknown` 升级。
4. **盘中场外成交（可选付费源）**：接入带 TRF/condition/correction 的逐笔数据，不把 print 单独解释为方向。
5. **结果合同扩展**：不可变基础榜、5/20 XNYS 交易日标的路径、MFE/MAE 与相对 SPY 已交付；下一步先冻结预定义 trigger/invalidation 和 `trade_taken` 事实，再评估是否建立“触发/未触发”与真实交易关联，未冻结前不做事后归因。
6. **学习验证**：当前 10 样本才显示描述命中率、20 样本且 20 独立 session 才允许人工调查；样本成熟后仍需 walk-forward 和用户确认的新版本，绝不自动调权。

## 7. 当前限制

- 初版是研究清单，不是全市场扫描器；扫描本地自选前 20 只或 `STOCK_LIST`，首屏只展示 Top 5。TradingView 当前通过官方 TXT 导入合并，不是账户实时同步。
- 初版只对美股期权 underlying 生成研究候选；其他市场保留 blocked 状态等待独立交易日历、Regime 和期权数据合同。
- 普通 OpportunityRun 请求本身仍不保存；Web 只在合法 XNYS 盘前窗口通过 `snapshots/ensure` 自动保存该日第一份研究版本，周末、休市、盘中和盘后跳过。历史上未保存的预览不能事后重建成因果样本。
- 没有经用户确认的结构化 Playbook，个人风格匹配保持未知。
- Moomoo OpenD 10.9.6918 / Python SDK 10.9.6908 已可读取官方 underlying overview 与异常期权事件，但尚未持久化期权域逐日快照、测量账户级延迟或完成历史回测；overview 的 IV Rank 是供应商统计字段，不是本项目验证出的择时 edge。
- 期权墙尚未保存为逐日历史序列，也未通过用户交易样本验证其对触墙、穿越、钉仓或后续收益的预测增量；当前只可作为研究上下文。
- 异常成交事件的 Moomoo 方向、情绪和订单类型均为供应商分类；缺少开平仓、参与者身份、真实主动方与 dealer inventory，当前不得据此推断 dealer 定位或生成买卖指令。
- 当前结果学习只评估标的价格上下文，不包含期权收益、真实成交、trigger 是否触发或 trade_taken；异步 overview、ATM IV、期权墙和异常成交也尚未冻结进样本。
- 单票详情尚未提供完整合约级 bid/ask size、spread、期限结构、skew、可成交滑点和用户风险预算；这些缺口不会由聚合 Volume/OI、IV 或 Gross Gamma 补推。
- 结果摘要是严格 cohort 内的小样本描述，不是策略胜率；达到人工调查门槛也不会自动修改排名。
- FINRA 公共数据不具备当日时效；第三方 TRF 源尚未配置。

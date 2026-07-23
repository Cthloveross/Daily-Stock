# Phase 1.5 · 单合约复盘工作台

> 状态：单个 PositionEpisode 的案例精选、七档 K 线、常规/扩展时段、ET 横轴、EMA8/EMA13、成交证据联动、证据复盘与按需模型增强已落地（2026-07-22）。
>
> 产品边界：页面只读取不可变 Episode evidence 与底层行情，不写交易账本，不读取交易密码，不解锁交易，不下单、改单或撤单。
>
> 口径边界：这是单合约仓位生命周期的复盘页，不是已经完成多腿归组的 StrategyEpisode，也不把未验证边界下的条件性结果升级为正式收益。

## 1. 用户路径与路由

在“仓位复盘”中打开一个仓位回合的详情后，点击“打开完整复盘 · K 线与成交证据”，进入：

```text
/journal/review/:episodeId
```

页面通过内部兼容 API `GET /api/v1/journal/v2/position-episodes/{episode_id}` 读取不可变 Episode、构建信息和 allocation evidence。若当前正在查看 canonical 对比构建，URL 会保留 `build_id`，详情请求也会携带相同 `build_id`，不能静默回到默认 CSV build。

进入工作台时还会保留仓位列表的以下查询上下文：

- `build_id`：显式选择的构建；
- `symbol`：底层标的筛选；
- `status`：生命周期状态筛选；
- `completeness`：证据完整度筛选；
- `case`：案例精选排序；
- `page`：列表页码。

“返回仓位复盘”会回到 `/journal?tab=positions` 并恢复上述参数。未携带 `build_id` 时，进入和返回都继续使用默认 CSV build。

## 2. 页面回答什么

仓位列表提供“案例精选”，帮助用户从 1,441 个 PositionEpisode 中先找到值得复盘的回合。页面查询参数使用 `case`，读取 API 时映射为只读的 `case_focus`：

| 界面选项 | `case_focus` | 确定性规则 |
|---|---|---|
| 盈利最多（大赚） | `top_profit` | 只纳入已闭合且净 P&L 已知的 Episode，按净 P&L 降序 |
| 亏损最多（大亏） | `top_loss` | 只纳入已闭合且净 P&L 已知的 Episode，按净 P&L 升序 |
| 费用最高（高费用） | `largest_fee` | 按已持久化总费用降序，空值排后 |
| 持有最长（长持有） | `longest_hold` | 按已持久化持有秒数降序，空值排后 |

案例精选与 `build_id`、标的、状态、完整度和分页条件取交集；未选择时仍按开仓时间最近优先。它只改变后端排序和结果集合，不重建 Episode、不改写交易事实，也不把条件性 P&L 升级为已验证收益。打开案例后，`case` 会随返回上下文保留。

工作台把已经可信的执行事实与底层行情放到同一个时间轴：

```text
PositionEpisode + immutable allocation evidence
              -> 证据发生时间（ET）
              -> 默认对齐底层 5 分钟 K 线；不满足覆盖门禁时降级日线
              -> 可手动切换 1m / 2m / 5m / 15m / 30m / 1h / 日线
              -> 分钟线默认筛选常规时段，可显式切换含盘前盘后
              -> 基于当前时段的底层 close 重算 EMA8 / EMA13
              -> 横轴、十字光标与证据时间统一显示纽约时间 ET
              -> 图表 marker <-> evidence timeline 联动
```

顶部显示：

- OCC/raw contract、底层标的、方向和生命周期状态；
- 开仓/平仓时间、期权开平仓均价、费用和净结果；
- 当前 `EpisodeBuild` ID，保证用户知道正在看哪个事实版本；
- `Moomoo 只读 · 不下单` 边界；
- 条件性结果、期初边界未验证和窗口内未归零的显式警告。

期权成交价格只出现在摘要和证据时间线。图表纵轴只使用底层股票价格，绝不把期权 premium 画到底层价轴上。

## 3. Fill、订单时间代理与买卖方向

页面延续 Episode evidence 的时间精度，不制造不存在的逐笔成交：

| 证据 | 图表语义 | 时间线语义 |
|---|---|---|
| 真实 fill observation | 负分配现金流使用向上“买” marker，正分配现金流使用向下“卖” marker | 标记“真实逐笔成交”，显示 fill observation ID |
| `aggregate_only` order 或 `timing_precision=order_time_proxy` | 使用方形、警示色 marker，并按现金流显示“代理·买/卖” | 标记“订单时间代理”，显示 order observation ID |

买卖点以 `allocated_cash_flow` 的经济方向为准，而不是用 `open/add/reduce/close` 角色猜测：现金流 `< 0` 是买入，`> 0` 是卖出，零值或缺失则明确显示“方向未知”。生命周期角色仍独立显示为开仓、加仓、减仓或平仓，因此空头开仓/回补不会被多头的开平仓视觉规则反转。

订单时间代理只说明券商订单汇总证据出现的时间，不是伪造的 fill time，不能用于分钟级入场、滑点、MFE 或 MAE 结论。无法对齐任何 bar 的 evidence 会显示“行情缺口”，其原始时间不会被移动到邻近 K 线。

当前联动行为：

- 点击 evidence 会在图表上聚焦对应 bar，并高亮 marker；
- 点击带事件的 bar/marker 会高亮该时间点对应的 evidence；
- 多条 evidence 落在同一 bar 时，时间线仍是逐条事实的权威视图，图表点击默认聚焦该 bar 的首条匹配证据。

## 4. 行情来源与覆盖范围

工作台复用：

```text
GET /api/v1/stocks/{stock_code}/history?period=...&days=...
```

股票历史响应在原有 `stock_code / stock_name / period / data` 外新增可选字段：

| 字段 | 含义 |
|---|---|
| `source` | `DataFetcherManager` 最终成功返回 K 线的数据源名称 |
| `coverage_start` | 最终返回 bars 的最早日期或时间 |
| `coverage_end` | 最终返回 bars 的最晚日期或时间 |
| `last_bar_at` | 最终返回的最新一根 bar 日期或时间 |
| `derived_from_period` | 派生周期所使用的原始周期；原生周期为 `null` |
| `aggregation_method` | 派生 K 线的聚合方法；原生周期为 `null` |

日/周/月线时间使用 `YYYY-MM-DD`；分钟线使用带时区 ISO datetime。Moomoo `time_key` 原始值不带 offset，服务端会按市场代码补入美东时间或北京时间，并保留美东夏令时差异；浏览器不得按查看者本地时区猜测。覆盖范围从最终返回的数据计算，包括 resample 与 tail 之后的实际结果，不用请求参数伪造。空行情时四个 provenance 字段均为 `null`。

Moomoo 分钟历史会沿 `page_req_key` 读取全部分页，再按 `time_key` 去重和升序；每一页保留相同的盘前盘后参数。重复 continuation key、后续页错误、空页仍要求续页或超过 128 页时整次请求失败并交给数据源 fallback，不把已取到的部分分页伪装成完整覆盖。

`period=2m` 不是 Moomoo 原生周期。服务端固定读取带时区的 1 分钟 K 线，按市场本地日期对齐连续 2 分钟 bucket，使用 first/max/min/last 聚合 OHLC、求和 volume/amount，并按聚合后的 close 重算涨跌幅；不会跨交易日或交易时段缺口拼接。响应继续返回 `period=2m`，同时明确返回 `derived_from_period=1m` 与 `aggregation_method=time_bucket_2m_ohlcv`，即使源数据为空也不伪装成原生 2 分钟行情。

`period=15m` 与 `period=30m` 是独立的原生请求周期。Moomoo 分别映射到 `K_15M` 与 `K_30M`，继续遵守同一套完整分页、去重、升序和市场时区规则；成功返回时 `derived_from_period` 与 `aggregation_method` 均为 `null`，页面不会用前端重采样后的 K 线冒充券商原生周期。

工作台会显示实际周期、`source`、覆盖范围和最后一根 bar。`source` 缺失时显示“未知数据源”，不会猜测供应商。

### 4.1 手动周期切换

K 线右上角提供 `1m / 2m / 5m / 15m / 30m / 1h / 1D` 七档手动切换。当前工作台请求上限分别为：1/2/5/15/30 分钟 60 天、1 小时 730 天、日线 1,000 天；Moomoo 分钟数据会完整读取 continuation pages，其他 fallback 数据源可能有更短的周期限制，因此实际可用范围仍以响应中的 `coverage_start / coverage_end` 为准。

手动选择是显式请求：页面只展示响应中真实返回的周期，不把其他周期重新贴标签。所选周期没有 bars 或无法覆盖全部 execution evidence 时，页面显示该周期不可用并继续保留证据时间线，不会静默换成另一档。首次进入页面、用户尚未手动选择时，才使用下一节的 5 分钟到日线自动降级。

### 4.2 EMA8 与 EMA13

工作台在每个可用周期的底层 K 线上同时显示 EMA8 与 EMA13。两条线只读取当前图表真实返回并经当前时段筛选后的底层 `close`，不读取期权成交价、premium、现金流或模型输出；切换周期或交易时段后都会重新计算，因此 1 分钟 EMA8、日线 EMA8、常规时段 EMA8 与扩展时段 EMA8 都是不同口径，不能混用。

计算采用常规、可复现的 EMA：先用首个完整的 8 根或 13 根 close 的简单平均值作为 seed，再按 `alpha = 2 / (period + 1)` 递推。第一条 EMA 数据点分别从第 8 根或第 13 根 bar 开始；bars 不足一个完整周期时对应线为空，不补齐、不外推，也不伪造早期值。EMA 仅帮助观察底层价格节奏，不证明期权交易逻辑、策略 edge 或因果关系。

分钟视图默认使用美股常规时段 `09:30–16:00 ET`，用户可显式切换到含盘前盘后的 `04:00–20:00 ET`。筛选按 `America/New_York` 计算，自动处理夏令时；bar 结束边界不纳入下一时段。页面同时显示当前 EMA 口径与参与计算的 K 线数量，所选时段外的 execution evidence 保留在时间线并标记“所选时段外”，不会被移动到别的 bar。

真实 Episode #1117 的只读核对说明了为什么必须显示这一口径：在 2026-07-01 09:54:36 ET 附近，5 分钟常规时段 EMA8/EMA13 为 `375.626437 / 374.647118`，包含盘前盘后时为 `377.992471 / 378.428857`；15 分钟分别为 `374.158756 / 373.104667` 与 `379.083327 / 378.991694`；30 分钟分别为 `373.169722 / 372.468989` 与 `379.417412 / 378.485151`。公式没有变化，差异来自输入时段。

此前 lightweight-charts 默认把 Unix 时间按 UTC 标在横轴，09:54 ET 会显示成约 13:54。工作台现对分钟图显式设置纽约时区：横轴与十字光标把 `2026-07-01T13:54:00Z` 显示为 `2026-07-01 09:54 ET`，冬夏令时分别由 IANA 时区处理。历史 bar 展示的是最终 OHLC；成交发生在 bar 结束前时，该 bar 的最终 close 与包含它的 EMA 在成交瞬间尚不可知，复盘不得把它写成当时已知信号。

## 5. 首次视图的 5 分钟到日线降级

降级由工作台的首次自动视图负责，股票历史 API 本身不会把失败的 5 分钟请求伪装成日线响应；用户手动选择周期后也不会触发这条自动降级。

当前规则：

1. 以 Episode 开仓时间和全部 evidence time 中的最早值判断回合年龄；
2. 回合在最近 60 天内时，先请求底层 5 分钟 K 线；
3. 只有返回非空 bars，且每条 execution evidence 都能映射到对应 5 分钟 bar，才使用 5 分钟视图；
4. 5 分钟请求失败、返回空数据或未覆盖全部 evidence 时，明确说明原因并请求日线；
5. 回合早于 60 天时直接请求日线，并说明分钟行情已超出当前可靠窗口；
6. 日线也为空时不绘制 K 线，但仍保留可审阅的证据时间线。

日线降级会固定显示：“日线只能帮助观察大方向，不能用于精确判断入场、MFE 或 MAE。”日线 marker 按 evidence 的纽约市场日历日期对齐，不把日线上的位置描述成分钟级进场点。

## 6. 证据复盘与模型增强

复盘助手把“确定性复盘”和“外部模型增强”拆成两步。点击“生成证据复盘”时调用：

```text
POST /api/v1/journal/v2/position-episodes/{episode_id}/ai-review?build_id=...&enhance=false
```

这条路径不调用任何外部 LLM，而是立即用本地证据引擎生成事实结论、交易结果与成交结构、标的相对 SPY、执行观察、证据缺口和下一次必须补充的问题。响应为 `analysis_mode=deterministic`、`analysis_source=local_evidence_engine`；主动选择本地模式时 `data_state=ready`，不会用“AI 不可用”冒充失败。

点击“尝试模型增强”时同一端点使用 `enhance=true`。响应始终把确定性事实层放在 `evidence_markdown`；只有模型成功时才另外返回 `model_analysis_markdown`，界面将其放在独立“模型推断层”，不能替换事实层。模型成功时返回 `analysis_mode=model_enhanced`、`analysis_source=configured_llm`；模型失败时不丢弃结果，而是保留 `evidence_markdown`、令模型字段为空，并标记 `data_state=llm_unavailable`。旧 `analysis_markdown` 暂时保留原语义供旧客户端兼容，并已标记 deprecated。模型等待期间，页面继续显示已经生成的证据复盘。

页面在复盘助手前提供六项“交易逻辑草稿”：策略假设、进场触发、失效点与止损、仓位/合约理由、实际出场原因和事后反思。前四项只能被视为对进场前计划的事后回忆，后两项属于结果已知后的记录；界面和模型指令都禁止把事后补录倒推成当时已经写下或已知的 setup。草稿只在当前浏览器的 `localStorage` 持久化，键同时包含 `build_id` 与 `episode_id`，不会写入 Moomoo、append-only 证据账本或 Episode。清空草稿只删除对应浏览器键。点击本地证据复盘时，非空草稿会发送给本机服务但不调用外部模型；点击模型增强时，同一批非空文字还会发送给用户已经配置的第三方模型供应商。

生成复盘时，只有非空字段会作为本次请求的可选 body 发送：

```json
{
  "user_context": {
    "setup_thesis": "...",
    "entry_trigger": "...",
    "invalidation_plan": "...",
    "position_rationale": "...",
    "exit_reason": "...",
    "post_trade_reflection": "..."
  }
}
```

每项最多 2,000 字符，六项去空白后的合计最多 6,000 字符；浏览器和服务端双重限制，服务端还会拒绝额外字段。上下文在事实包中固定标记为 `self_report_not_independently_verified`、`recorded_in_system_before_entry=false` 和 `post_trade_recall_of_pre_trade_plan`；确定性复盘只会逐项展示并列出缺失内容，模型增强才可检查计划与执行的一致性，而且“无法验证”不能写成“不一致”。没有 body 的旧调用完全兼容。

两条请求都固定绑定当前 `episode_id` 和页面实际展示的 `build_id`。服务端读取不可变 Episode/evidence、底层标的与 SPY 的同期行情，以及开仓日附近七天内可用的 Regime；生成文本不写回 Journal、annotation、canonical set 或任何交易状态，重新生成也不会改变既有事实。

发送给模型的是有界、去标识化的事实包：合约与生命周期经济字段、分配后的成交时间/数量/现金流/费用、条件 P&L 标记、每个标的最多 24 根紧邻回合的底层与 SPY OHLCV、行情来源/计算口径、可用 Regime、明确 warnings，以及本次请求中实际提供的用户自述。行情窗口较长时保留前 12 根和后 12 根候选 bar，避免紧凑输入只留下进场却丢失出场上下文；分钟 bar 同时携带 `finalized_at` 与相对开仓/平仓的可用性标记。账户 key、账户号、卡号、token、源文件路径、broker observation ID 和完整原始账单不会进入模型输入；输入 JSON 只被视为数据，不能作为指令。

分钟级标的/SPY 收益代理只读取开仓与平仓各自发生前最近一根已经完成、且距离事件不超过一个周期的 bar close。假设 5 分钟 bar 的时间戳表示开始时间，09:54 ET 的成交不能读取 09:50–09:55 bar 的最终 close，只能使用 09:50 前已经完成的 bar；没有新鲜的已完成 bar 时结果保持未知。日线仍明确标成整日/跨日背景代理，不能当作精确持仓窗口或进场时已知信号。Regime 当前只有日期而没有生成 as-of 时间，因此即使找到快照，也必须标记“开仓时可见性未知”。

为降低交互等待，底层与 SPY 两路独立行情会并行读取；复盘专用的默认行情 loader 跳过无关的股票名称和实时报价补取。默认真实 loader 每路最多等待 8 秒，并使用全局 4 槽有界 daemon；超时或容量占满会立即降级为“该行情未知”，挂起 worker 在真正结束前继续占槽，避免无限积累。成功且非空的行情才进入进程内只读短缓存：key 为 `(symbol, period, days)`，TTL 180 秒，最多 32 项，并以深拷贝读写；自定义 loader、空结果与失败结果不缓存，复盘文本也不缓存或持久化。模型指令要求 700–1,000 个中文字符，保留六个完整章节门禁；单个已配置模型最多等待 10 秒，全部模型 fallback 共用 16 秒路由预算，结构不完整也只能在该预算内尝试下一模型。服务另有覆盖 LiteLLM 首次导入、初始化和模型调用的 20 秒墙钟硬截止；超时 provider 只能在有界 daemon worker 槽位中收尾，不会继续阻塞页面或无限累积。LiteLLM 默认读取随包附带的本地 cost map，避免首次复盘为刷新价格表额外访问网络；显式环境覆盖仍会被尊重。

复盘模型链可用 `JOURNAL_AI_MODEL` 与 `JOURNAL_AI_FALLBACK_MODELS` 独立配置，不再必须继承全局 Gemini/Agent 路由。两项都不存在时完全继承当前 Agent 模型链；只设置复盘主模型且未声明专用 fallback 时，会去重接续 Agent 模型链；显式保存空 fallback 表示不继续尝试其他模型。选择 `openai/<model>` 时仍复用现有 OpenAI provider credential，不复制或写死 Key。

本地网站不能直接把当前 ChatGPT/Codex 登录或订阅当成后台模型连接。要在网页按钮中自动调用 GPT，必须在服务端配置 OpenAI API Key，并显式选择可用的复盘模型；只添加 OpenAI Key 而保留自动 Gemini 主模型时，复盘仍可能先走 Gemini。另一条路线是把本项目暴露为 ChatGPT App/MCP 工具并在 ChatGPT 内调用，但那是不同的产品入口，不等于本地网页获得免费的会话后端。

输出必须把三类信息分开：

- **事实**：输入中可直接核验的执行、费用、持有时间、P&L 边界、带 provenance 的行情与 Regime；
- **推断**：对价格行为、相对表现和可能交易逻辑的解释，必须标置信度和证据，相关性不能写成因果；
- **未知**：用户原始意图、交易计划、止损理由、心理、缺失行情，以及没有提供的 IV、Greeks、bid/ask 和仓位快照。

没有 Regime 证据时不能归因为 regime mismatch；单笔案例不能证明策略存在 edge，也不能据此调整系统权重。AI 输出不是投资建议或下单指令。若本回合 P&L 是 `assumed_flat_unverified` 下的条件值，模型和界面都必须保留该边界，不能称为已验证账户收益或升级 headline 资格。

失败采用可审计降级：单个底层/SPY 行情读取失败时，对应收益代理保持未知并写入 warning；Regime 不可用时明确禁止相关归因；LLM 未配置、超时、报错、结构不完整或返回空文本时，响应为 `data_state=llm_unavailable`，但 `analysis_markdown` 仍是完整的本地证据复盘，不再是空泛的“稍后重试”。未知 Episode/构建返回 404，无效开仓时间返回 422。K 线、原始 evidence 和本地证据复盘始终不依赖模型供应商。

## 7. 边界与 P&L

`assumed_flat_unverified` 不会因为进入工作台而改变：

- 页面显示“条件性结果 · 不进 Headline”；
- 期初没有券商持仓快照时，用户操作不能把结果改成 Verified；
- 页面只展示后端已经持久化的费用和条件性 P&L，不在浏览器重新计算账户收益；
- `lifecycle_status=open` 只表示导入证据回放到窗口末端尚未归零，不代表当前仍持仓，因为当前没有经过验证的期末 position snapshot。

行情 K 线与 evidence 对齐用于复盘上下文，不会改变 Episode、allocation、canonical set 或默认构建。

## 8. 错误与空状态

- 无效 Episode ID、构建不匹配或详情读取失败时展示可重试错误，并保留返回仓位复盘入口；
- 行情读取失败时展示行情错误，证据详情仍可独立审阅；
- 没有 bars 时明确说明不会移动 evidence，也不会用邻近日线伪装精确入场；
- 没有 execution evidence 时不生成 marker，并明确显示没有可展示的执行证据；
- 模型不可用时返回完整本地证据复盘和确定性市场上下文，不把供应商失败扩大成整页失败；
- 图表按需加载单个 Episode，不把 1,441 个生命周期的全部证据一次载入浏览器。

## 9. 本次不包含

- 不进行 StrategyEpisode 多腿、spread、roll、行权或指派分组；
- 不导入 opening/closing position snapshot，不提升 headline P&L 资格；
- 不提供期权合约自身的历史价格、IV、Greeks、bid/ask 或 OI 曲线；
- 不计算 MFE、MAE、滑点、利润捕获率或进场形态结论；
- 不保存 ReviewAnnotation、交易计划、情绪、错误标签或 Playbook 规则；
- 不把本地或模型复盘文本持久化为交易事实，不将相关性包装成确定性盈亏原因或已验证 edge；
- 不提供实时信号、交易解锁或任何订单执行能力。

下一步应先用“大赚/大亏/高费用/长持有”案例精选抽查真实复杂生命周期、现金流买卖方向和多周期时间对齐，再加入经用户确认的 StrategyEpisode 分组、持久化复盘注释，以及有明确 provenance 的期权/市场快照。Moomoo 边界永久保持只读。

# Phase 1.5 · 单合约复盘工作台

> 状态：单个 PositionEpisode 的案例精选、七档 K 线、常规/扩展时段、ET 横轴、EMA8/EMA13、成交证据联动、证据复盘、按需模型增强、ReviewAnnotation v1 与 Review Queue 已落地（2026-07-23）。复盘流 v2（队列化操作条、双列布局、快速复盘模式、订单级分批成交合并）已落地（2026-08-01，见 §1.1）。
>
> 产品边界：页面只读不可变 Episode evidence 与底层行情；只有用户显式保存时才会向隔离的 ReviewAnnotation 追加一版自述，不修改券商证据账本、canonical、Episode 经济字段或 P&L，不读取交易密码，不解锁交易，不下单、改单或撤单。
>
> 口径边界：这是单合约仓位生命周期的复盘页，不是已经完成多腿归组的 StrategyEpisode，也不把未验证边界下的条件性结果升级为正式收益。

## 1. 用户路径与路由

G-3（2026-08-01）起，`/journal?tab=positions` 是纯复盘工作台：顶部「复盘工作台」条显示默认构建标识、Review Queue 计数（未开始/进行中/已完成）与主 CTA「继续复盘下一笔」，其下依次是回合列表（含筛选与案例精选）、模式观察与 Playbook。当前持仓快照、canonical 构建预览/生成与激活（设为默认）管理全部移至「数据与构建」tab（原「交易证据」，`?tab=import` 深链不变），复盘时不再穿插数据管线卡片。

「继续复盘下一笔」的确定性优先级（只读，不新增后端排序口径）：

1. 先续上「进行中」的回合（最近开仓优先），避免半途而废；
2. 否则按 `top_loss` 案例精选取第一笔「未开始」回合——亏损最大的已平仓回合优先复盘，这是职业复盘的默认优先级（`top_loss` 只覆盖已平仓且净额已知的回合）；
3. `top_loss` 无命中时退回「未开始」的最近开仓回合；三步都为空时如实提示“全部回合已完成复盘”，不伪造下一笔。

CTA 命中后直接进入下述单合约复盘工作台，并保留当前 `build_id` 等查询上下文。

### 1.1 复盘流 v2：队列化单笔复盘（2026-08-01）

单笔复盘页按「逐笔清队列」重构，解决“工作单埋在页面中部、每笔复盘完都要回列表”的问题：

- **sticky 快捷操作条**（页面顶部常驻）：回合标识（合约、生命周期、条件性标注）+ Net（未归零回合如实显示“窗口末未归零”）+ 「跳过，下一笔 →」+ 「保存草稿并下一笔」+ 主按钮「完成复盘并下一笔」。保存失败（校验或网络）时停留当前页并显式展示错误，不吞草稿；工作单内原有「保存进行中 / 标记复盘完成」按钮全部保留。
- **「下一笔」单一实现**：工作台头部 CTA 与本页共用 `apps/dsa-web/src/components/journal/review/nextReviewEpisode.ts` 的 `findNextReviewEpisode`（只读调用既有列表 API，小页拉取）：进行中（最近开仓优先）→ 未开始按 `top_loss` → 未开始最近开仓；解析时排除当前回合；三档皆空时如实提示「全部回合已完成复盘」并提供返回列表入口，不伪造下一笔。导航后 URL 仍为 `/journal/review/:id`（push history，查询上下文保留）。
- **双列布局（≥1280px）**：左列 = 回合摘要 + 底层 K 线与事件 + 成交证据时间线；右列 = 交易逻辑草稿工作单（sticky、可独立滚动）。窄屏保持单列且工作单紧跟操作条之后。页面容器加宽到 1720px。「复盘助手」「Playbook 关联」在双列之下；「证据窗口与窗口末投影」与 Matching/Completeness/Provenance 技术来源合并折叠进「证据明细」（默认收起，内容完整保留）。
- **快速复盘模式**（默认）：工作单只显示「进场逻辑回忆」（对应 `setup_thesis`）与「复盘反思」（对应 `post_trade_reflection`）两栏，加两组一键 chip——错误类型（追高进场 / 逆势抗单 / 仓位过大 / 无进场计划 / 提前止盈 / 止损未执行 / 情绪交易 / 过度交易）与交易风格标签（突破 / 回调 / 趋势跟随 / 事件驱动 / 剥头皮 / 波段）。chip 点选直接写入既有 `tags` / `error_types` 字段（与自由文本共用一份数据、自动去重，再点取消）；「展开完整工作单」显示其余四项（进场触发、失效点与止损、仓位理由、实际出场原因）。这只是展示层收纳：字段语义、字符上限、本机草稿与「完成复盘至少一项自述」校验完全不变。
- **订单级分批成交合并**（display-only，见 §3）：同一 broker order 的多笔 fill 在时间线合并为一行（方向、总数量、现金流加权均价、成交时间范围、合计费用、「分 N 笔成交」），可展开逐笔审阅；K 线默认每单一个 marker，成员相距超过一根 K 线时保留逐笔 marker。

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

### 3.1 订单级分批成交合并（2026-08-01，display-only）

很多交易是一笔决策、分批成交。`evidenceKind=fill` 且带 `broker_order_observation_id` 的证据按订单合并为一行展示（实现：`apps/dsa-web/src/components/journal/review/evidenceConsolidation.ts`）：

- 合并行显示 方向、回合角色集合、总数量、**现金流加权均价**（= ∑|分配现金流| ÷ (∑数量 × 合约乘数)，BigInt 十进制精确运算，half-up 保留 4 位；任一成员缺数量/现金流或乘数不可用时显示不可得，不估算）、成交时间范围（首笔 → 末笔 ET）、合计费用与「分 N 笔成交」徽标；
- **费用诚实**：只有每条成员 fill 都有费用证据时才求和，否则显示「费用不完整」，不把部分和冒充总费用；
- 合并只发生在展示层：展开「N 笔逐笔成交」可看到每条 fill 的原始数量、现金流、费用与 observation ID，底层 evidence 不变、完整可审计；单笔成交的订单与订单时间代理证据保持原样展示；任一成员是订单时间代理时合并行保留「订单时间代理」标注；
- **marker 规则**：合并组默认每单一个 marker，锚定首笔已映射 fill 的 K 线（点击选中合并行）；同一订单的 fill 在当前周期上相距**超过一根 K 线**（分钟线按 bar 间隔，日线及以上按是否同一根 bar）时保留逐笔 marker，不隐藏真实的时间分散执行；全部成员都无法映射时不画 marker，不伪造位置。

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

页面在复盘助手前提供六项“交易逻辑草稿”：策略假设、进场触发、失效点与止损、仓位/合约理由、实际出场原因和事后反思，并可填写复盘标签与错误类型。前四项只能被视为对进场前计划的事后回忆，后两项属于结果已知后的记录；界面和模型指令都禁止把事后补录倒推成当时已经写下或已知的 setup。未提交更改会按 `build_id + episode_id` 自动保存在当前浏览器的 `localStorage`；进入页面时若同时存在服务器版本和本机未提交草稿，优先保留本机草稿并提示服务器最新 revision，避免静默覆盖。清空草稿只删除对应浏览器键，不删除已保存版本。点击本地证据复盘时，非空的六字段自述会发送给本机服务但不调用外部模型；点击模型增强时，同一批非空文字还会发送给用户已经配置的第三方模型供应商。

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

每项最多 2,000 字符，六项去空白后的合计最多 6,000 字符；标签和错误类型各最多 20 项、每项最多 64 字符，并做去空白、去重和稳定排序。浏览器和服务端双重限制，服务端还会拒绝额外字段。上下文在事实包中固定标记为 `self_report_not_independently_verified`、`recorded_in_system_before_entry=false` 和 `post_trade_recall_of_pre_trade_plan`；确定性复盘只会逐项展示并列出缺失内容，模型增强才可检查计划与执行的一致性，而且“无法验证”不能写成“不一致”。没有 body 的旧调用完全兼容。

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

## 7. ReviewAnnotation v1 与 Review Queue

ReviewAnnotation 是用户对一笔既有交易的**事后自述**，不是券商事实、交易前计划证明或模型结论。页面只有在用户点击“保存进行中”或“标记复盘完成”后才调用服务器；自动保存到浏览器、生成本地证据复盘和请求模型增强都不会创建 annotation。

当前提供三个接口：

```text
GET  /api/v1/journal/v2/position-episodes/{episode_id}/review-annotations/latest?build_id=...
GET  /api/v1/journal/v2/position-episodes/{episode_id}/review-annotations?build_id=...
POST /api/v1/journal/v2/position-episodes/{episode_id}/review-annotations
```

POST body 显式携带 `build_id`、`review_status`、六个自述字段、`tags` 和 `error_types`。接口只接受用户提交的这些字段，不接受或隐式复制 `evidence_markdown`、`model_analysis_markdown` 等 AI 输出。

状态口径固定为：

| Review Queue | 持久化状态 | 含义 |
|---|---|---|
| 待复盘 | `not_started`（派生） | 当前 immutable build + PositionEpisode 没有任何 annotation；数据库不为“空状态”写占位行 |
| 进行中 | `in_progress` | 最新 revision 被用户保存为仍需继续 |
| 已完成 | `completed` | 最新 revision 被用户标记完成；至少要有一个六字段自述，只有标签或错误类型不能完成复盘 |

持久化与审计边界：

- annotation 同时绑定 `account_key + episode_build_id + position_episode_id`；构建不匹配返回 404，不能把一个 build 的自述静默挪到另一个 build；
- 每次非重复保存只会追加下一 revision，并通过 `previous_annotation_id` 串联历史；历史按最新 revision 优先返回，不提供覆盖更新或删除路径；
- 规范化内容的 `content_sha256` 相同且仍是最新版本时，重复提交幂等返回原 revision，不制造重复版本；
- annotation 表继续受 append-only UPDATE/DELETE 拒绝保护；它与 broker order/fill/fee evidence、canonical set、Episode allocation、经济字段、P&L 和 Headline eligibility 隔离；
- annotation 只代表 `user self-report · not independently verified`。AI 可以在一次复盘请求中读取用户当前提供的文字，但不会自动创建、完成、修改或删除 annotation。

PositionEpisode 列表响应的 `review_queue` 在当前 build 与标的、生命周期、完整度、案例精选等非复盘筛选范围内返回 `pending / in_progress / completed / total`。可选 `review_status=not_started|in_progress|completed` 只筛列表项目，不改变这组队列基数；每行同时返回最新状态、revision 和更新时间。这样用户可以从“待复盘 → 进行中 → 已完成”继续工作，同时始终知道状态属于哪一个不可变事实版本。

## 8. 边界与 P&L

`assumed_flat_unverified` 不会因为进入工作台而改变：

- 页面显示“条件性结果 · 不进 Headline”；
- 期初没有券商持仓快照时，用户操作不能把结果改成 Verified；
- 页面只展示后端已经持久化的费用和条件性 P&L，不在浏览器重新计算账户收益；
- `lifecycle_status=open` 只表示导入证据回放到窗口末端尚未归零，不代表当前仍持仓，因为当前没有经过验证的期末 position snapshot。

行情 K 线与 evidence 对齐用于复盘上下文，不会改变 Episode、allocation、canonical set 或默认构建。

## 9. 错误与空状态

- 无效 Episode ID、构建不匹配或详情读取失败时展示可重试错误，并保留返回仓位复盘入口；
- 行情读取失败时展示行情错误，证据详情仍可独立审阅；
- 没有 bars 时明确说明不会移动 evidence，也不会用邻近日线伪装精确入场；
- 没有 execution evidence 时不生成 marker，并明确显示没有可展示的执行证据；
- 模型不可用时返回完整本地证据复盘和确定性市场上下文，不把供应商失败扩大成整页失败；
- 服务器 annotation 读取失败时保留本机草稿继续编辑；保存失败时不清除草稿，用户可重试；
- 没有已保存 annotation 时显示“待复盘”，版本历史为空而不是报错；
- 图表按需加载单个 Episode，不把 1,441 个生命周期的全部证据一次载入浏览器。

## 10. 本次不包含

- 不进行 StrategyEpisode 多腿、spread、roll、行权或指派分组；
- 不导入 opening/closing position snapshot，不提升 headline P&L 资格；
- 不提供期权合约自身的历史价格、IV、Greeks、bid/ask 或 OI 曲线；
- 不计算 MFE、MAE、滑点、利润捕获率或进场形态结论；
- 不把 ReviewAnnotation 冒充预先存在的 TradePlan，也不保存情绪、RuleEvaluation 或 Playbook 规则；
- 不自动保存本地证据复盘或模型增强文本，不把它们写入 annotation 或交易事实，不将相关性包装成确定性盈亏原因或已验证 edge；
- 不提供实时信号、交易解锁或任何订单执行能力。

下一步应先用 Review Queue 与“大赚/大亏/高费用/长持有”案例精选抽查真实复杂生命周期、现金流买卖方向和多周期时间对齐，再加入经用户确认的 StrategyEpisode 分组、TradePlan/RuleEvaluation，以及有明确 provenance 的期权/市场快照。Moomoo 边界永久保持只读。

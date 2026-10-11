# Phase 1.11 · Moomoo 当前期权持仓快照

> 状态：LIVE / US / option-only 的只读双采样探针、短期服务端预览、显式确认、append-only 当前持仓证据、连续性 fence 与 fence-bound 零写 Episode 构建预览已落地；它只证明本次采集区间内的当前状态，不反向证明任何历史 Episode 的期初仓位。
>
> 安全边界：本链路没有交易解锁、下单、改单或撤单调用。`LIVE` 只表示读取真实账户的当前持仓。

## 1. 为什么单独建立当前持仓证据

成交账本可以从已知成交重放“证据窗口末仓位”，但不能自动回答两个不同问题：

1. 账单覆盖开始前，账户是否已经持有同一合约；
2. 账单证据截止后，券商此刻实际还持有什么。

把第二个问题的答案直接写回第一个问题，会把今天的状态伪装成数月前的历史事实。当前持仓快照因此使用独立表、独立 preview / confirm 和独立页面卡片；它不会修改 canonical set、Episode build、默认构建或旧 Journal。

Moomoo 的 [`position_list_query`](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html) 返回指定账户的当前持仓，期权数量单位为合约；返回结构包含方向、代码、数量、可卖数量和成本上下文字段。接口没有提供可当作历史时点使用的 broker `as_of`，因此系统只记录本地括住的采集区间，并明确保存 `broker_as_of=null`。字段含义以 Moomoo 的 [Trade Position 定义](https://openapi.moomoo.com/moomoo-api-doc/trade/trade.html) 为准。

## 2. 只读双采样合同

一次 preview 固定执行：

```text
选择唯一或显式指定的 LIVE + US 稳定 acc_id
  -> position_list_query(refresh_cache=true, show_option_strategy_view=false)
  -> 再次执行同一完整查询
  -> 仅比较 instrument + LONG/SHORT + signed contract quantity
  -> 读取每个活动期权的 broker contract spec
  -> 生成去标识、确定性排序和哈希的服务端 artifact
```

- 两次读取都要求服务端刷新；任何一轮失败都不是“空仓”。
- 两轮都保留按合约排序的规范化边界行；账本重新计算每轮 hash，并从边界行独立推导新增、减少、方向或张数变化。调用方提供的 `stable` 与差异摘要不能替代底层证据；任一不一致都不能确认。
- `qty=0` 是 Moomoo 可能返回的非活动缓存行；它会计数后过滤，不进入持仓边界。负数、NaN、无穷或无法解析的数量继续阻断。
- 股票和非美股行只计入过滤统计；活动记录只接受可解析的美股期权、`LONG / SHORT` 和 `USD`。
- 期权乘数必须由同次 market snapshot 的 `lot_size / option_contract_size / option_contract_multiplier` 三字段一致性证明；缺失或冲突时 fail closed。
- 行情、市值、浮盈亏、原始账户号、`position_id` 和名称不进入 payload。
- `cost_price / average_cost / diluted_cost` 只作为“券商当前成本上下文”展示，不用于补造历史现金流、费用、已实现 P&L 或胜率。
- 数量、执行价、成本与乘数在证据合同中只接受规范 Decimal 字符串；`"2"` 与非规范的 `"2.00"` 不再形成语义相同却 source hash 不同的两份证据。
- summary 与首读/次读的总行数、识别持仓数和各类过滤数逐字段交叉验证；分类数不能超过原始总行数，账户选择与时间语义也必须一致。

完整空仓是一项有效证据：两次完整查询都没有活动期权行且不存在校验问题时，快照保存为 `empty`。查询失败、部分读取、边界变化或规格不完整则保持不可确认，绝不降级为零持仓。

## 3. 时间与账户连续性

系统分别保留：

| 字段 | 含义 |
|---|---|
| `observed_from` | 第一次持仓读取开始时间 |
| `observed_through` | 第二次持仓读取完成时间 |
| `operation_completed_at` | 合约规格读取和规范化全部完成时间 |
| `broker_as_of` | Moomoo 未提供，固定为 `null` |

稳定的双采样只能说明区间两端的净持仓边界一致，不能排除区间内先开后平的净零成交，也不能被描述成某个精确历史时点的券商 statement。

为防止旧 payload 或未来时钟被重新包装成“当前”证据，首版固定要求：本地未来偏差不超过 2 分钟、双读采集区间不超过 5 分钟、采集完成到 artifact 保存及首次确认不超过 30 分钟。三项 temporal policy 会进入 scope、artifact、preview 与确认 provenance 的哈希；已经成功确认的同一 artifact 在过期后只允许幂等读取原回执，不会新建第二份证据。

确认不等于永久“当前”。每次读取 latest 都以服务器评估时刻重新计算 `operation_completed_at` 的 age 和未来时钟偏差，并同时核对快照的 account binding 和被冻结的 refresh publication 是否仍是当前最新锚点。只有 age 不超过 30 分钟、未来偏差不超过 2 分钟、binding 匹配且 publication anchor 仍为 latest 时，API 才返回 `is_current=true`；过期、本地时钟异常、账户变化或锚点被新刷新取代的记录仍是不可变历史证据，但页面必须标为“历史快照”，不能继续叫当前仓位或当前空仓。

确认还要求账户连续性。首版以最近一笔已确认的 Journal read-only refresh publication 为锚点：它已经通过 CSV overlap 把本机 OpenD 账户与可信交易证据绑定。持仓 artifact 使用相同 HMAC domain 和 secret 计算不可逆 account binding，并冻结 `refresh_publication_id`、`refresh_publication_key` 与连续性 hash；确认事务会重新读取该账户最新 publication，不能由调用者只传一个相同 hash 绕过。没有该锚点时仍可只读 preview，但不能把结果发布为可信持仓证据。

## 4. Preview、确认与不可变存储

网页使用：

```text
GET  /api/v1/journal/v2/position-snapshots/latest
POST /api/v1/journal/v2/position-snapshots/preview
POST /api/v1/journal/v2/position-snapshots/{artifact_id}/confirm
```

Preview 保存短期、server-owned artifact；浏览器只拿到规范化持仓、范围、状态、warnings、hash 和过期时间，不能在确认请求中替换 payload。确认必须：

- 引用仍未过期的 artifact 与 preview key；
- 再次解析并重算全部 hash；
- 再次核对最新账户 binding 和被冻结的 refresh publication；
- 显式提交 `acknowledge_future_only=true`；
- 通过完整读取、稳定边界、USD option-only、合约规格和成员集合守恒门禁。

确认后只追加：

- `journal_v2_position_snapshot_artifacts`：短期服务端预览；
- `journal_v2_position_snapshots`：确认回执与采集边界；
- `journal_v2_position_snapshot_members`：规范化活动期权持仓。

三张表都由 SQLite trigger 拒绝 UPDATE / DELETE；应用数据库初始化和 snapshot repository 初始化都会确保触发器存在，避免 `Base.metadata.create_all` 先建表时留下短暂无保护窗口。早期本地表使用只增不删的 schema 检查补齐当前模型缺少的 nullable 列，已确认记录仍必须通过完整 provenance 才能读取。相同 artifact 重试幂等返回原 snapshot；不同采集时刻即使持仓内容相同，也仍是新的观察，而不是覆盖旧记录。

读取已确认快照时也不是“查到行就信任”：系统会重算 snapshot key、确认 provenance、成员 provenance、member/instrument key 和成员集合 hash，并与原 artifact 的 scope/source/evidence/snapshot/stability、账户 binding 及 publication 外键交叉核对。latest snapshot、latest Journal publication 和 currentness 在同一个 SQLite 读事务中求值，避免账户锚点切换时把两次不同时间的读取拼成一次“当前”响应。任何存储层不一致都会 fail closed。

## 5. 页面口径

快照卡片自 G-3（2026-08-01）起挂在「数据与构建」tab 的「当前持仓快照与未来构建」分组（此前位于“仓位复盘”页顶部）；Journal 页面仍把三类信息分开：

1. Episode 的历史成交证据窗口；
2. 由该窗口内成交重放得到的“证据窗口末数量”；
3. 新卡片中的“当前期权持仓快照”。

当前快照展示采集区间、距评估时刻的 age、30 分钟 currentness 门槛、账户/锚点状态、完整空仓或活动合约数、LONG / SHORT 数量、可卖数量、可证明乘数和可折叠的券商成本上下文。过期、旧账户或被新 publication 取代的确认记录会醒目标为“历史快照”，同时仍允许对当前正确账户发起新的只读 preview；页面不会把旧记录静默当成当前，也不会把快照成员并入旧 Episode 表或把“证据窗口末未归零”改名为实时持仓。

## 6. 未来 Episode 连续性门禁

系统现在提供零业务写入、只读的连续性评估：

```text
GET /api/v1/journal/v2/position-snapshots/episode-boundary-readiness
```

它不会生成或激活 Episode，而是把最新已确认 snapshot 与其后最新已发布的 Journal refresh / canonical 事实集放进同一个保守门禁。结果只有四种：`no_snapshot`、`awaiting_refresh`、`blocked`、`ready`。`ready` 仅表示可以进入下一步的零业务写入 Episode build preview，不表示已有新构建、旧构建被改写或收益已验证。

评估打开独立的 SQLite `mode=ro` 连接，启用 `query_only` 和写操作拒绝器，并在读取任何 snapshot、publication 或 canonical 证据前显式执行 `BEGIN`；整个门禁因此固定在同一个 SQLite 读事务 / WAL snapshot 中，不会把并发刷新前后的行拼成一次判断。该入口不调用 schema 初始化，也不写 artifact、业务账本、Episode、activation 或任何业务表 / Schema。这里的“零写”专指零业务表和零 Schema 写入；SQLite 仍可能为 WAL 模式下的读取协调创建或维护 `-wal` / `-shm` sidecar，因此不承诺数据库目录在文件系统层面完全没有字节变化。

快照也不是“查到确认行就信任”。门禁通过 snapshot repository 的严格读取器重新解析被冻结的 artifact payload，重验 schema、parser name/version、temporal policy、scope/source/evidence/stability hash、账户与 publication binding，并重算 snapshot provenance、member provenance、member/instrument key 和成员集合。任一 artifact、provenance 或 member 漂移都会 fail closed，不能仅凭格式正确的 64 位 hash 进入 `ready`。

anchor 与后续每一笔 refresh publication 也在同一事务中重新解析其 server-owned OpenD payload，并交叉核对 artifact key、source/evidence hash、完整查询窗口、accepted OpenAPI import batch、实际 order/fill 行数、账户 binding、canonical batch membership、canonical cutoff 与 publication key。结构无效或 provenance 漂移的 refresh 不能仅凭已存在的 publication 行充当连续覆盖。

anchor 与 target canonical set 都通过 Episode builder 共用的 frozen replay verifier：重新核对 canonical root hash、root/member payload、selected source 与 batch provenance、execution-group / leg / fill-link 成员关系及 multiplier 证据，然后只从冻结 payload 投影订单、成交、组合和经济字段。门禁不会绕过该投影直接把 selected raw observation 当作 canonical 事实。快照与后续成交的合约匹配使用 `asset_type + underlying + expiry + Decimal strike + right + currency` 的 OCC semantic identity；Moomoo 变长 strike 和标准 8 位零填充 symbol 只要语义相同就视为同一合约，raw symbol 仅用于审计，乘数仍单独核对。

门禁不使用 30 分钟 currentness 作为历史边界条件：snapshot 在取得后续 refresh 时必然会成为“历史快照”，但只要其不可变证据和后续覆盖仍能验证，它仍可作为未来边界候选。

门禁固定以 `operation_completed_at` 作为保守 boundary，并把 `query_started_at` 到 `operation_completed_at` 整段作为 acquisition guard。要进入 `ready`，至少需同时证明：

- 后续成交证据从该 snapshot 的边界开始连续覆盖，没有静默时间缺口；
- snapshot artifact、provenance、members 与成交证据属于同一 account binding、US option scope 和兼容 parser / policy；
- acquisition guard 内没有会改变仓位的精确成交；
- 只有订单时间、没有精确成交时间的 aggregate-only 记录没有在边界前新增或改变；
- 同一普通订单 fill set 或组合执行组没有跨越 boundary；
- boundary 后的 order / fill 均来自经过 frozen canonical root/member/source/group 重验的投影，并属于 anchor 之后已发布的同账户 refresh batch 链；
- snapshot 成员与后续 OCC semantic identity / contract multiplier 兼容，不因 strike 是否使用 8 位零填充而误判；
- fence key 的 `position-snapshot-continuity-fence/1.1` 合同冻结 snapshot、publication chain、canonical ID/hash/source cutoff、时间 policy 与全部门禁计数；即使同步重算合法 canonical set key，cutoff 漂移也必须产生新的 fence。

页面只展示必要状态、下一步和可折叠阻断依据：没有 snapshot 时提示完成当前持仓确认；已有 snapshot 但没有后续发布时提示下一完整交易日后在「每日刷新」确认一次只读刷新；存在竞争成交、跨界经济单元、来源缺口或身份冲突时保持 `blocked`；证据充分时显示“连续性已证明”，同时明确“尚未生成或激活 Episode”。

即便未来用非零 snapshot 验证了新窗口的期初数量，先于该 snapshot 的开仓成本、费用和现金流仍未知；相关生命周期继续是 left-censored，不能补造历史已实现收益。

## 7. Fence-bound 零写 Episode 构建预览

连续性状态为 `ready` 后，页面可自动读取一份内存构建摘要：

```text
GET /api/v1/journal/v2/episode-builds/position-snapshot/preview
    ?expected_fence_key=<64 位小写 SHA-256>
```

浏览器只能提交最近 readiness 返回的 `expected_fence_key`，不能选择 snapshot、publication、canonical set、boundary 或 source batch。服务端在新的 `mode=ro + query_only + explicit BEGIN` 事务里重新读取并验证最新连续性；只有 fence 仍完全相同才会在同一 SQLite WAL snapshot 中投影并调用 builder。状态已变化、fence 过期、canonical/root/member/provenance 漂移、账户不一致或门禁不再 `ready` 时统一返回冲突，不沿用旧预览。

预览固定使用以下构建边界：

- opening snapshot 视为完整的已验证数量边界，`opening_snapshot_complete=true` 且 `assume_flat_if_missing=false`；
- snapshot 的券商 cost / average / diluted cost 仍只作展示，不传入 builder，也不推导历史现金流；
- 逐笔 fill 仅接受 `filled_at > operation_completed_at`，aggregate-only order 仅接受 `ordered_at > operation_completed_at`，恰好等于 boundary 的事件不进入；
- fill-detail 父订单即使早于 boundary，也仅作为边界后 fill 的来源和费用守恒辅助，不凭订单时间制造一条执行；
- 只消费同账户、`LIVE / US / option` 且属于已发布 refresh batch 链的 canonical 事实；股票及其他资产明确排除，不能把期权快照当作股票空仓证明；
- Moomoo 变长 OCC symbol 与标准零填充 symbol 先按 underlying、expiry、Decimal strike、right、currency 归一，再单独证明 contract multiplier；
- 跨 boundary 的普通订单 fill set 或 execution group 继续 fail closed；组合费用保留在 group scope，不猜测分配到单腿。

响应只返回 snapshot → canonical 身份、boundary/cutoff、构建与证据 hash、持仓/事件/计划 Episode 计数、费用守恒和 warnings，不返回整批 Episode 明细。页面把结果标为“只读 · 尚未构建”，并再次核对 fence、snapshot ID/key、target publication 与 canonical ID/hash；任一身份变化都会清空旧摘要。

数量边界可以验证，boundary 之前的开仓成本和费用仍未知。因此由 snapshot 带入的 lifecycle 保持 left-censored；headline 只允许使用非 left-censored、已关闭、完整且费用没有 group-scope 歧义的 Episode。没有合格 Episode 时 P&L 返回空值，而不是零或估算值。

这条 API 不初始化 schema、不保存 artifact、不插入 EpisodeBuild/PositionEpisode、不修改默认 activation，也不调用交易接口。响应显式固定：

```text
preview_only=true
business_data_written=false
episode_build_written=false
activation_changed=false
evidence_written=false
trading_action_performed=false
```

未来若增加正式构建，必须使用新的显式确认流程，将 snapshot、fence、target canonical set、boundary policy、builder config 与 evidence hash 固化到 append-only build provenance，并创建独立 activation；不得把当前 preview 隐式转成写操作，也不得修改旧 build、旧 activation 或旧 P&L。

## 8. 真实只读验收与剩余风险

2026-07-30 的真实 OpenD 零写检查：

- 两次服务端读取的 7 行活动美股期权持仓边界一致；
- 对应 7 行合约规格全部完成，0 validation issues；
- Moomoo 同时返回的 19 条 `qty=0` 非活动缓存行被明确过滤；该数量可能随券商缓存状态变化，不是账户持仓数；
- `analysis_ready=true`、`position_snapshot_complete=true`；
- 当次生产数据库主文件大小与修改时间前后不变，`journal_database_written=false`、`trading_actions_performed=false`；这是该次验收结果，不扩展为 SQLite 永不产生 WAL/SHM sidecar 的承诺。

仍需完成：

- 在「数据与构建」的「每日刷新」正式确认一笔 account-bound refresh publication 后，走通生产库 snapshot preview / confirm；
- 真实完整空仓、多账户歧义、持仓在双采样之间变化、OpenD 离线和合约规格权限缺失验收；
- 在生产库形成“refresh A → snapshot → refresh B”后，以真实成交验证 continuity fence 的 `ready / blocked` 分类；
- 在真实 `ready` fence 上验收零写 Episode 预览的计数、费用守恒、延迟和页面身份失效保护；
- 将 anchor 的严格 canonical replay 拆为共享验证与 boundary-only 物化；当前结果正确，但大型 canonical set 仍会为 anchor 构造不参与 builder 的中间对象，需在真实 `ready` 场景记录延迟和内存后优化；
- 为正式写入增加独立的显式确认、append-only build 与 activation 流程；当前零写预览不能自动生成或激活构建；
- 收盘后自动采集前先补统一账户级 lease、持久化 pending preview / attempt 和恢复 UI；不能把手工 future-only / refresh confirm 或账户连续性门禁静默移除。

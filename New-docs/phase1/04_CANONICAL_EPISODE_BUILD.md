# Phase 1.4 · Canonical 证据到仓位生命周期对比构建

> 状态：冻结 canonical set 的 preview/confirm、append-only 构建、组合执行组重放、显式读取与 append-only 激活能力已落地；构建成功不会自动替换当前 build，仍需独立确认激活。
>
> 产品边界：Moomoo/OpenD 永久只读。本链路不读取交易密码，不解锁交易，不下单、改单或撤单。
>
> 命名边界：用户界面使用“仓位复盘”“交易证据”“可信证据账本”等产品名称；`/journal/v2/...`、`journal_v2_*` 与 parser 版本仅是兼容性技术标识。

## 1. 为什么要有独立的 canonical 构建

Phase 1.3B 已把最新 accepted CSV baseline 与只读 OpenAPI 观察合并为版本化 canonical set。保存 canonical set 和生成仓位生命周期是两次独立、都需要显式确认的动作：

```text
CSV + OpenAPI observations
        -> identity links / attestations
        -> frozen canonical set
        -> preview（不追加 Episode 行）
        -> confirm（追加新的可对比 EpisodeBuild）
        -> 通过 build_id 显式查看
        -> append-only 激活后才成为默认复盘版本
```

这样可以同时保留：

- 原 CSV build 作为初始默认、可回查的稳定基线；
- canonical build 作为新的对比版本；
- canonical set、投影版本、来源批次和 Episode build 之间的不可变绑定；
- 新规则上线后重跑所产生的新版本，而不覆盖历史结果。

Canonical confirm 不会“激活”新构建，也不会让“仓位复盘”悄悄换口径。没有 activation 时默认读取继续回退到 CSV build；只有携带明确 `build_id` 才查看未激活的对比构建。完成独立激活后，默认读取才解析到对应 canonical build。

## 2. 冻结事实重放，而不是重新猜身份

构建器重放 canonical set 创建时冻结的 record、members、selected observation references 与 source batches。它不会在构建时重新运行当前版本的 identity linking 或 canonicalization，也不会因为后来的规则变化而重新选择来源。

重放前会验证：

- canonical set ID、SHA-256、账户、状态与 source batches；
- canonical root/member payload 与已保存哈希一致；
- 每个 member 指向的 selected observation、批次和经济字段仍与冻结记录一致；
- 期权证据具有可证明的合约乘数；
- fill 到 order 的关系能落到同一冻结 canonical 身份。
- execution group、声明腿、每笔 fill-link 与 group-fee companion records 完整且仍绑定同一冻结父单；
- 每笔组合 fill 只绑定一条腿且不携带伪造的普通订单或腿级费用。

任一结构、哈希或绑定不一致都阻止构建，不能用“当前看起来相同”的记录替代被冻结的证据。

## 3. 当前真实基线

2026-07-21 的 canonical set 1 由正式本地 CSV batch 2 与只读 OpenAPI batch 3 组成，冻结结果为 3,542 orders / 5,400 fills、0 blocking issues。将它投影到 builder 后的基线是：

| 项目 | 结果 |
|---|---:|
| Aggregate order events | 574 |
| Detailed fill events | 5,400 |
| 执行事件总数 | 5,974 |
| PositionEpisode | 1,441 |
| 已归零 | 1,437 |
| 未归零 | 4 |
| Source known fees | USD 151,750.75 |
| Allocated known fees | USD 151,750.75 |
| 费用守恒 | 通过 |

这组数字与当前 CSV build 的生命周期数量一致，是“去重后的 canonical 证据没有重复计算成交或费用”的回归基线，不代表两个 build 的 provenance 相同，也不等于已经取得期初持仓事实。

### 3.1 Aggregate submitted amount 不是执行现金流

Canonical order observation 中的 `amount` 可能表达委托/汇总口径；真实样本中部分值与 `filled_qty × avg_price × contract_multiplier` 不一致。它不能直接作为已执行现金流喂给 builder，否则会把未成交或券商汇总语义混进 P&L。

Canonical 投影因此不传递 aggregate order 的 submitted amount。对于只有订单汇总的已成交证据，builder 只根据已证明的成交数量、均价和合约乘数推导执行现金流，同时保留原 observation 供审计。逐笔 fill 仍以冻结的真实 fill 为准。

## 4. 生命周期语义：不是 FIFO

当前算法是 `signed_position_episode_builder`：按单一 instrument 的有符号持仓变化构建 `0 -> 非 0 -> 0` 生命周期。它支持分批建仓、减仓、归零和反手；反手会把同一事件按数量、费用和现金流拆到旧生命周期的平仓部分与新生命周期的开仓部分。

因此它是 **signed-position lifecycle**，不是 FIFO lot matcher。文档和 UI 不应把新的 PositionEpisode 称为“FIFO 交易”；FIFO 仅属于隔离保留的 legacy Journal 页面。

## 5. Preview / confirm 契约

内部兼容 API 路径保留 `v2`，产品文案不显示该迁移代号：

| 方法 | 内部路径 | 行为 |
|---|---|---|
| `GET` | `/api/v1/journal/v2/episode-builds/canonical/preview` | 选择指定或最新 analysis-ready canonical set，返回冻结 hash、build key、来源批次、事件/Episode 数量、费用守恒、默认 build 对比和警告；不追加 Episode 或 source-link 行 |
| `POST` | `/api/v1/journal/v2/episode-builds/canonical` | 携带 preview 返回的 `canonical_set_id`、`canonical_set_sha256`、`build_key`；重新验证后原子追加或幂等返回对比构建 |
| `GET` | `/api/v1/journal/v2/position-episodes?build_id=...` | 显式读取指定构建；省略 `build_id` 时仍读取当前 CSV 默认 build |
| `GET` | `/api/v1/journal/v2/position-episodes/{episode_id}?build_id=...` | 在指定构建内下钻 Episode 与 allocation evidence；省略 `build_id` 时仍只在默认 CSV build 中查找 |
| `GET` | `/api/v1/journal/v2/episode-builds/activation` | 读取当前默认版本、选择来源与上一 activation；没有 activation 时显示 CSV fallback |
| `POST` | `/api/v1/journal/v2/episode-builds/{build_id}/activate` | 以预期 build key 和当前 activation/build ID 做 compare-and-swap，显式切换默认复盘版本 |

“仓位复盘”顶部的“可信事实集构建预览”会显示事实集、执行事件、计划回合、费用守恒以及相对默认构建的差异，并明确提示“不会替换当前默认视图”。需要期初为空假设时，构建和激活各自要求独立确认。构建完成后可以先“查看这个构建”；只有点击“启用为当前复盘”并通过 stale-state 校验，列表和详情的默认读取才会切换。

Preview 可能初始化缺失的本地 schema/append-only trigger，但不会追加 canonical、Episode、allocation 或 source-binding 事实行。`confirm_allowed=true` 还要求 canonical set 可分析、无未解决证据且费用守恒。

Confirm 会拒绝 stale canonical hash 或 build key；SQLite 写入使用即时写锁，并把 EpisodeBuild、StrategyEpisode、PositionEpisode、evidence allocation 与 canonical source link 放在同一事务中。稳定 build key 和 source link 使同一计划重复确认返回既有构建，不制造重复 Episode 或费用。

### 5.1 组合执行组与费用口径

Canonical execution group 保留券商父组合、声明腿、稳定成交关联与整组费用，但 PositionEpisode 仍按真实单腿 instrument 构建。父组合 `qty / dealt_qty / net price` 只用于证明组合包与平衡腿，不会被当成普通合约数量、腿 VWAP 或执行现金流。

当券商只返回整组费用时，权威守恒按币种记录：ordinary source、ordinary allocated、retained execution-group、source known 与 accounted。组费不会猜测分摊到腿；任何包含 group fill 的回合均设置 `group_fee_unallocated`，`total_fee / realized_pnl_net` 为空，并从严格 Headline 排除。Preview 展示组数、受影响回合数、保留组费和逐币种守恒；confirm 与 activate 分别要求 `accept_group_fee_scope=true`。

2026-07-30 的真实库副本验证包含 1 个 MU vertical spread、2 条腿、4 笔 fill 与 USD 8.08 组费。完整 canonical build 生成 1,615 个回合；USD ordinary 174,531.05 + retained group 8.08 = source/accounted 174,539.13，2 个受影响腿回合的 Fee/Net 均为空。该验证没有写入或激活正式数据库。

## 6. `assumed_flat_unverified` 必须单独确认

Canonical set 解决的是“双来源重叠怎样去重”，不自动解决覆盖窗口开始前的真实持仓。系统现已能冻结并确认“采集当下”的券商持仓快照，但它不能倒推当前 canonical 历史窗口的期初状态；因此现有 preview 仍会返回：

- `opening_boundary_policy=assumed_flat_unverified`；
- `requires_assumed_flat_acceptance=true`；
- headline P&L 继续为空；
- 条件性 P&L 只能在同时显示边界警告时使用。

Confirm 必须独立提交 `accept_assumed_flat=true`。确认 canonical hash/build key 不等于接受期初为空，这两个决定不能合并或代替。

组合费用确认同样独立：`accept_group_fee_scope=true` 只接受“费用精确到组、腿级 Net 不可用”的展示口径，不代表接受期初边界，也不授权把整组费用平均或按数量分摊。

## 7. Append-only、可比与回滚

每个 canonical build 通过 `journal_v2_episode_build_canonical_sources` 绑定 canonical set ID/hash、投影名称/版本、canonical ingest cutoff 和完整 `source_batch_ids`。Episode 的事件覆盖窗口与 canonical ingest cutoff 分开保存，避免把“事实发生到何时”和“这版事实何时冻结”混为一谈。默认版本选择另存为 `journal_v2_episode_build_activations` 追加事件，不能 UPDATE/DELETE build 或把选择状态塞回不可变事实行。

历史 build 不更新、不删除。产品视图回滚到旧 canonical build 时会追加新的 activation（A → B → A），完整保留谁替代了谁；显式 `build_id` 仍可用于只读比较。若必须回滚本地数据库，应停止服务、先保存当前库，再整体恢复已验证备份，不能在 append-only 表上执行 DELETE。

## 8. 仍未完成的边界

- 尚未取得与本构建历史左边界同一时点、同一账户且成交连续的 opening-position snapshot，不能把条件性 P&L 升为 headline；当前时点快照只可作为未来锚点；
- StrategyEpisode 仍是 1:1 `single_position_unclassified`；execution group 证明同一次券商组合执行，但尚未替用户确认跨执行的策略生命周期、roll、行权或指派分组；
- 仍需人工抽查至少 20 个复杂生命周期；
- canonical 对比构建只改进事实来源与可审计性，不自动生成进场理由、K 线归因或 Playbook 结论；
- 旧 `scripts/sync_moomoo_live.py` 与 `/journal/sync-live` 继续固定暂停。

显式选中的生命周期现已接入[单合约复盘工作台](./05_SINGLE_POSITION_REVIEW_WORKSPACE.md)，可联动底层 K 线与 execution evidence，但不会自动生成归因结论。当前期权持仓的只读双采样已经形成独立、面向未来的快照；下一步是在保持默认基线可回查的前提下证明成交连续性 fence，再把合格快照接成新版本 opening boundary，并继续完成 StrategyEpisode 人工确认与可追溯的期权/市场快照。

每日增量查询、账户 binding、刷新水位与证据发布的完整合同见 [Journal 每日只读刷新专题](./10_JOURNAL_READONLY_REFRESH.md)。

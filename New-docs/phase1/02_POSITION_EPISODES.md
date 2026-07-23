# Phase 1.2 · PositionEpisode 生命周期账本

> 状态：builder v1.1.0 与首个 append-only build 已落地（2026-07-21）
>
> 事实源：只读取 latest accepted `moomoo-statement-v2` batch；旧 parser 审计批次和 legacy Journal 均不参与 Episode 构建。
>
> 边界：当前没有已验证的期初持仓快照，因此所有汇总都必须保留 `assumed_flat_unverified` 标记。

## 1. 这一步解决什么

Phase 1.1 保存了不可变的 Moomoo 订单、逐笔成交和费用证据。Phase 1.2 在这些证据之上生成单一合约的持仓生命周期：

```text
latest accepted `moomoo-statement-v2` batch
  └─ 5,974 execution events
       ├─ 5,400 real fill events
       └─ 574 aggregate order events
            ↓ builder v1.1.0
       1,441 PositionEpisodes
            ↓ 1:1, no intent inference
       1,441 unclassified StrategyEpisodes
```

574 条较早的成交只有订单级数量、均价、金额和费用。它们以 `aggregate order event` 参与持仓与现金流重建，不会被伪造成逐笔 fill。因此，这部分证据仍不能支持分钟级入场归因、滑点或 MFE/MAE。

## 2. 首个正式 build 的结果

当前正式数据库中的 build 1 是 immutable、append-only 的版本化构建：

| 项目 | 结果 |
|---|---:|
| Builder | `signed_position_episode_builder` v1.1.0 |
| 输入事件 | 5,974 |
| 真实逐笔 fill event | 5,400 |
| aggregate order event | 574 |
| PositionEpisode | 1,441 |
| 已归零 Episode | 1,437 |
| 未归零 Episode | 4，共 44 张合约 |
| StrategyEpisode | 1,441，和 PositionEpisode 1:1 |
| Strategy 类型 | `single_position_unclassified` |
| Spread / roll 推断 | 均关闭 |

每个 Episode 都能下钻到不可变的 order/fill allocation evidence。builder 配置、输入证据集合和算法版本分别保存哈希；相同输入和配置再次构建时返回相同 build，不覆盖 build 1。

这里的“未归零”只表示导入账单的证据回放到窗口末端时没有回到零；由于还没有期末 position snapshot，它不等于经过券商验证的当前持仓。

## 3. 为什么 headline P&L 现在必须为空

CSV 覆盖窗口开始时没有经过券商验证的 opening-position snapshot。builder 只能在用户显式传入 `accept_assumed_flat=true` 后，以 `assumed_flat_unverified` 构建生命周期。这是一个可审计的计算假设，不是已知账户事实。

因此当前规则是：

- 1,441 个 Episode 全部排除在 headline 指标之外；
- headline 的 eligible closed count 为 0，gross / fee / net 保持空值；
- UI 可以展示条件性结果，但必须同时显示 `assumed_flat_unverified`，不得把它改写成正式账户收益。

在“窗口开始前持仓为 0”这一未验证假设下，1,437 个已归零 Episode 的条件性结果是：

| 条件性 closed 指标 | USD |
|---|---:|
| Gross realized P&L | 372,980.48508 |
| Fee | 151,667.10 |
| Net realized P&L | 221,313.38508 |

4 个未归零 Episode 的费用仍属于完整费用守恒的一部分，但不进入上表的 closed P&L。只有导入并核验覆盖窗口起点的真实持仓快照，或取得足够早且可证明从零开始的历史后，才能重新构建并评估 headline 资格。

## 4. 金额、乘数与费用守恒

合约乘数不是按 symbol 猜测。repository 用 broker `amount / (quantity × price)` 证明乘数：

- 股票证据只接受乘数 1；
- 期权证据只接受乘数 100；
- 由于券商金额按分舍入，允许的绝对金额残差最多为 USD 0.005；
- 无法从执行证据证明乘数，或同一合约的证据相互矛盾时，build 失败，不静默套默认值。

费用以订单级事实为准：有逐笔 fill 的订单按成交数量分配，并把舍入余数放入最后一条 allocation；aggregate-only 订单直接保留订单费用。首个 build 的守恒结果为：

```text
source known fee total    USD 151,750.75
allocated known fee total USD 151,750.75
difference                USD       0.00
```

builder 在费用不守恒时拒绝持久化。

## 5. 生命周期与策略边界

PositionEpisode 以合约的有符号持仓变化构建，支持分批建仓、减仓、归零和反手；反手事件会在原 Episode 的平仓部分与新 Episode 的开仓部分之间分配数量、费用和现金流。

当前 StrategyEpisode 只是一个明确的 1:1 容器：

- grouping policy 为 `one_to_one_no_strategy_inference`；
- strategy type 为 `single_position_unclassified`；
- 不根据同一时间、同一标的或相邻到期日自动声称 spread、组合腿或 roll；
- 后续多腿/roll 分组必须先提供候选，再由用户确认交易意图。

这避免把事后看起来相关的订单包装成用户当时实际执行的策略。

## 6. API 与当前界面

| 方法 | 路径 | 语义 |
|---|---|---|
| `POST` | `/api/v1/journal/v2/episode-builds?accept_assumed_flat=true` | 对最新 accepted batch 追加或幂等返回版本化 EpisodeBuild；没有显式接受边界假设时拒绝 |
| `GET` | `/api/v1/journal/v2/position-episodes` | 读取最新 build 的分页列表、build 元数据、质量门禁、条件性 P&L 与对账覆盖率；可选 `case_focus=top_profit|top_loss|largest_fee|longest_hold` 只改变只读筛选/排序 |
| `GET` | `/api/v1/journal/v2/position-episodes/{episode_id}` | 读取单个 Episode、instrument、matching/completeness/provenance 和 order/fill allocation evidence |

Journal 默认进入“仓位复盘”，展示 latest build 的 PositionEpisode。旧 Overview、Trades、Reality、Analysis 等 FIFO 页面仅作为隔离的 legacy 存档，不是当前交易事实源，也不得与可信账本指标混算。

`case_focus` 省略时仍按 `opened_at` 最新优先。`top_profit` / `top_loss` 只纳入已闭合且 `realized_pnl_net` 已知的 Episode，分别按净损益降序/升序；`largest_fee` 按 `total_fee` 降序，`longest_hold` 按 `hold_seconds` 降序。它与 `underlying`、生命周期、完整度、`build_id` 和分页条件取交集，不覆盖原筛选，也不生成或修改 Episode。

当前 OpenAPI attestation 只覆盖最新 `moomoo-statement-v2` batch 的 1,089 / 3,542 个订单，scope 为 `partial_window`。Episode API 必须原样显示这个范围，不能把局部双来源对账写成整批已核验。

## 7. 存储与回滚

- EpisodeBuild、StrategyEpisode、PositionEpisode 和 evidence allocation 全部只追加；
- 应用 SQLite 连接同时启用 `foreign_keys` 与 `recursive_triggers`；
- `journal_v2_*` 表的 UPDATE/DELETE trigger 阻止覆盖或删除历史 build，也阻止 `INSERT OR REPLACE` 借 DELETE 绕过约束；
- build 1 写入前备份为 `data/backups/stock_analysis-pre-episodes-20260721.db`；`data/` 由 Git 忽略。

若需回滚本次本地数据写入，应先停止服务并保存当前数据库，再用该备份替换数据库；这会整体回到 Episode build 之前，而不是在 append-only 表上执行 DELETE。源码回滚与数据库回滚是两件独立的事。

## 8. 下一步退出条件

1. 导入经过去标识化的只读 OpenAPI observations，并建立跨批次 canonical reader；
2. 获取并验证覆盖窗口起点的真实持仓快照，消除 `assumed_flat_unverified`；
3. 人工抽查至少 20 个包含分批、反手、aggregate-only 和复杂期权生命周期的 Episode；
4. 建立多腿/roll 候选与用户确认流程，不自动推断交易意图；
5. 在 Review Workspace 中把图表、Episode、原始 evidence 和用户复盘结论同步联动。

在第 2 项完成前，条件性 P&L 可以用于核查算法与发现数据问题，但不能升级为产品 headline、策略胜率或个人 Playbook 证据。

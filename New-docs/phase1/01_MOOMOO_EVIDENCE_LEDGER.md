# Phase 1.1 · Moomoo 证据账本与双来源对账

> 状态：Phase 1.1 已落地（2026-07-21）；后续 Episode 构建见 [Phase 1.2 · PositionEpisode 生命周期账本](./02_POSITION_EPISODES.md)。
> 边界：账户与行情只读；本地账本只追加；不解锁、不下单、不改单、不撤单。

## 1. 这一步解决什么

旧 Journal 把多笔 fill 合并成订单均价，再用 FIFO 全量重建 `journal_trades`。这会丢失逐笔成交证据、破坏稳定 ID，也无法诚实表达券商只保留订单汇总而不再提供 fill 明细的情况。

Phase 1.1 新增可信证据账本（内部表族名为 `journal_v2_*`），先保存不可变的 broker evidence。Phase 1.2 已用版本化 EpisodeBuild 生成 PositionEpisode / StrategyEpisode；旧 `journal_imports`、`journal_orders`、`journal_trades` 和用户备注不参与新导入、Episode 构建或重建。

## 2. 真实数据验收结果

正确的 Moomoo CSV 包含：

- 6,094 条数据行、3,542 个父订单；
- 3,422 Filled、50 Cancelled、70 Failed；
- 5,400 条逐笔 fill；
- 2,848 个 Filled 订单有逐笔 fill；
- 574 个较早的 Filled 订单只有 `数量@均价` 汇总和完整费用，不能伪造成逐笔 fill；
- Filled 费用合计 USD 151,750.75，其中 aggregate-only 订单费用 USD 13,827.99；
- 新版 CSV 的 `Consolidated Audit Trail Fees` 合计也纳入费用分项和 `Total`。

CSV 与只读 OpenAPI 稳定窗口（2026-06-19 23:59:59 至 2026-07-19 23:59:59 ET）对账结果：

- 两边都是 1,089 个订单、1,058 个 Filled、2,107 条 fill；
- 1,089 / 1,089 父订单一一匹配，无 missing、extra 或复合键碰撞；
- 状态、每单 fill 数、成交数量、VWAP、费用总额和九项费用分项均无差异；
- 两边费用都是 USD 56,081.90；
- API `create_time` 与 CSV `Fill Time` 并非同一个时钟：126 条相差 1–5 秒，因此两个来源时间分别保留，不互相覆盖。

这证明稳定窗口可用于经济结果分析。574 个 aggregate-only 订单可用于持仓、现金流和费用复盘，但不能用于精确入场时刻、滑点、MFE/MAE 或分钟 K 线归因。

## 3. 新数据模型

新表全部位于 `src/journal/ledger/models.py`：

| 表 | 作用 |
|---|---|
| `journal_v2_import_batches` | 来源哈希、parser 版本、覆盖窗口、完整度与对账结果；不保存原始路径或文件名 |
| `journal_v2_reconciliation_attestations` | 可晚于 CSV 追加的独立对账证明，保存 API export 哈希、窗口与不可变结果，不修改原批次 |
| `journal_v2_broker_order_observations` | 某批次内不可变的订单观察，允许 `fill_detail` / `aggregate_only` |
| `journal_v2_broker_fill_observations` | 真实逐笔成交；不会为缺失明细的老订单合成 fill |
| `journal_v2_episode_builds` | 输入证据集合与构建算法的不可变版本 |
| `journal_v2_strategy_episodes` | 整个交易观点的策略生命周期 |
| `journal_v2_position_episodes` | 单一合约从建仓到归零的经济持仓生命周期 |
| `journal_v2_position_episode_evidence` | order/fill 到 episode 的数量、费用和现金流分配证据 |

金额、价格与数量使用 `Numeric/Decimal`。相同源文件和 parser 版本生成相同 batch key；重复导入直接返回已有批次。算法或 parser 升级通过新 batch / build 表达，不覆盖旧证据。

SQLite 为全部 `journal_v2_*` 表安装 UPDATE/DELETE 拒绝 trigger；应用连接同时启用 `foreign_keys` 和 `recursive_triggers`，append-only 不再只依赖 repository 约定，`INSERT OR REPLACE` 也不能借隐式 DELETE 绕过保护。跨来源对账可以在相同 CSV 重导时追加为新的 attestation，不会因为 batch 已存在而丢失后来获得的 API 证明。

## 4. 解析、对账和 API

### 4.1 只读 CLI

```bash
python scripts/reconcile_moomoo_history.py \
  --csv /path/to/History-Margin.csv \
  --readonly-export /path/to/moomoo-readonly.json
```

退出码：

- `0`：`analysis_ready=true`；
- `2`：读取成功但对账未通过或费用仍在延迟；
- `1`：输入或格式错误。

stdout 只包含汇总，不包含路径、文件名、order/deal ID 或逐笔记录，也不打开 Journal 数据库。

### 4.2 Web API

| 方法 | 路径 | 语义 |
|---|---|---|
| `POST` | `/api/v1/journal/v2/imports/preview` | 只读预览；返回 exact / partial / blocked、覆盖区间和费用 |
| `POST` | `/api/v1/journal/v2/imports` | 明确确认后追加可信证据；partial 必须传 `allow_partial=true` |
| `GET` | `/api/v1/journal/v2/data-health` | 最近已接受批次的覆盖率、对账与 observation 数量 |
| `POST` | `/api/v1/journal/v2/episode-builds` | 显式接受期初边界假设后，追加或幂等返回 EpisodeBuild |
| `GET` | `/api/v1/journal/v2/position-episodes` | 最新 build 的 PositionEpisode 列表、质量门禁与对账范围 |
| `GET` | `/api/v1/journal/v2/position-episodes/{episode_id}` | 单个 Episode 及其 order/fill allocation evidence |

旧 `/api/v1/journal/import` 对 Moomoo CSV 固定返回 410，避免任何旧客户端绕过可信账本、静默丢失 aggregate-only 订单后重建 FIFO。Web `/journal?tab=import` 已改为预览优先，并明确显示“数据库写入：否”；只有用户点击保存按钮才追加可信证据。

Data Health 会分别显示整个 batch 的覆盖范围和 API reconciliation 的覆盖窗口。当前正确 CSV 共 3,542 个订单，只有稳定窗口内 1,089 个订单经过 OpenAPI 逐单核对，因此 UI 显示“部分窗口已通过”，不会再误报整批已通过。

Episode API 的边界假设、条件性 P&L、费用守恒和当前构建结果见 [Phase 1.2 专题](./02_POSITION_EPISODES.md)。

## 5. 当前本地数据状态

真实 CSV 已在写入前通过 SQLite 在线备份和 `PRAGMA integrity_check`。当前分析的唯一事实源是 latest accepted `moomoo-statement-v2` batch；`moomoo-statement-v1` 只是同源 parser 升级审计，legacy Journal 和较早错误账单均不参与当前分析。为保持 parser 升级可重现，数据库不覆盖旧版本，而是保留旧 parser 审计批次并新增当前权威批次：

- 2 个同源、不同 parser 版本的 immutable batch（`moomoo-statement-v1` 审计 + 当前 `moomoo-statement-v2`）；
- 每个版本各有 1 条独立稳定窗口 reconciliation attestation；
- 当前可信账本分析视图为 3,542 条 order observations、5,400 条 fill observations、574 条 aggregate-only；
- 物理表共 7,084 条 order observations 与 10,800 条 fill observations，属于版本化证据，不得跨 batch 相加；
- latest accepted `moomoo-statement-v2` batch 已生成 append-only build 1：5,974 个执行事件生成 1,441 个 PositionEpisode，其中 1,437 个已归零、4 个未归零（共 44 张合约）；
- build 1 使用 builder v1.1.0，费用 USD 151,750.75 完整守恒；当前期初持仓边界为 `assumed_flat_unverified`，全部 Episode 都排除在 headline P&L 之外；
- legacy Journal 所有表的行数和逐行集合与备份一致。

原始 CSV 没有加入 Git；`data/` 与数据库备份仍由 `.gitignore` 排除。

Data Health 只选最新 `moomoo-statement-v2`：整个 batch 为 3,542 个订单，OpenAPI 已核对其中稳定窗口的 1,089 个订单，scope 为 `partial_window`，不是整批通过。较早错误 CSV 从未进入可信账本。

## 6. 分析与反馈护栏

- 先分 `exact`、`aggregate`、`partial`，再计算指标；不得把缺失明细当成零成交。
- 券商费用是 order-level 原始事实；向 fill/episode 分摊属于派生规则，必须记录版本并验证费用守恒。
- 没有交易前信号或计划证据时，只能描述盈亏与行为，不能事后贴 `TRUE_POSITIVE/FALSE_POSITIVE` 标签。
- 统计结论显示样本数、时间范围、缺失率和数据来源；少于 20 个信号样本不调整策略权重。
- CSV 复合键只用于带碰撞检测的对账；正式 API observation 以 broker `order_id/deal_id` 为主身份。

## 7. 下一刀

PositionEpisode builder、费用守恒和 1:1 unclassified StrategyEpisode 已在 Phase 1.2 落地。下一步是：

1. 导入 API observation，并提供跨批次 canonical reader；
2. 取得并核验覆盖窗口起点的真实持仓快照，消除 `assumed_flat_unverified`；
3. 人工抽查至少 20 个复杂生命周期；
4. 为多腿和 roll 生成可解释候选，并由用户确认交易意图；
5. 用 Review Workspace 联动图表、PositionEpisode、原始 evidence 和复盘结论。

Journal 默认使用“仓位复盘”。旧 Overview、Trades、Reality、Analysis 等 FIFO 页面仅作隔离存档，不是当前事实源，也不与可信账本口径混合。

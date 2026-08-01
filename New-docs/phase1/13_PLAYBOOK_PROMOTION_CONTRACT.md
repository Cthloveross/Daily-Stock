# 个人 Playbook 晋升链路合同（阶段 C · 设计冻结稿）

> 状态：设计合同（2026-08-01）；切片 C-1 已实现（2026-08-01），C-2 / C-3 未开始
>
> 真源顺序：可执行代码 > 本合同 > HANDOFF §16 阶段 C 概述
>
> 目标：把「observed pattern（观察到的模式）」与「validated rule（已验证规则）」
> 严格分离，让复盘 annotation 可聚合、可晋升、可版本化，但绝不自动改变
> 系统权重或伪造统计置信。

## 0. 输入现实（已核实）

- `ReviewAnnotation`（append-only revision 链）已含结构化字段：
  `setup_thesis`、`entry_trigger`、`invalidation_plan`、`position_rationale`、
  `exit_reason`、`post_trade_reflection`、`tags`、`error_types`、`review_status`；
- Episode 侧已有 lifecycle/completeness/realized_pnl_net/hold_seconds/费用等
  持久化维度与案例精选排序；
- 当前 annotation 总数为个位数（用户刚开始复盘）——**任何统计展示都必须
  fail closed 于样本门槛之下**。

## 1. 分层模型

| 层 | 实体 | 可变性 | 语义 |
|---|---|---|---|
| L0 | ReviewAnnotation | append-only revision | 用户对单笔的自述与标签 |
| L1 | PatternObservation（视图，不落库） | 派生 | 按 tag/setup 分组的聚合观察：样本数、独立交易日数、条件性 P&L 分布、error_types 频次 |
| L2 | PlaybookCandidate | append-only | 用户从 L1 显式创建的候选规律（文字化规则 + 绑定的证据 episode 集） |
| L3 | PlaybookRule | append-only version 链 | 用户显式晋升的正式规则；含 version、生效说明、晋升时引用的样本快照 |

## 2. 硬边界（红线延伸）

1. L1 聚合**只读派生**，不落新表；样本数 < 门槛（默认 10 笔且 ≥5 个独立
   交易日）时只显示计数，不显示胜率/期望值等任何比率；
2. 条件性 P&L（`assumed_flat_unverified` / left-censored / 组费影响）在聚合中
   必须单独分桶，绝不与 verified 样本合并成单一数字；
3. L2→L3 晋升是**用户显式动作**（CAS + 显式按钮），系统永不自动晋升；
4. PlaybookRule 不反写任何评分权重、榜单排序或 AI prompt——它只是用户
   自己的决策清单；将来若要影响系统行为，另立合同；
5. 晋升时冻结证据快照（episode ids + build id + annotation revisions +
   聚合数字 + as-of），后续数据变化不追溯修改已晋升规则的依据记录；
6. 规则退役 = append 新 version 标记 retired，不删除。

## 3. 最小实现切片

| 切片 | 内容 | 写面 | 状态 |
|---|---|---|---|
| C-1 | L1 聚合端点 `GET /journal/v2/review-insights`（按 tag/setup 分组，门槛 fail-closed）+ 复盘页「模式观察」面板 | 零写 | 已实现 |
| C-2 | L2/L3 表 + append-only 仓储 + 晋升/退役端点 + Playbook 页面 | 新增 2 张 append-only 表 | 未开始 |
| C-3 | 单笔复盘页反向链接（该 episode 命中的 rule/candidate） | 零写 | 未开始 |

### C-1 实现锚点（2026-08-01）

- 聚合仓储：`src/journal/ledger/review_insights.py`
  `get_latest_review_insights()`——单一只读会话内解析默认构建（activation 优先、
  CSV fallback），复用 review 队列的 latest-revision-per-episode 子查询把回合
  与最新标注 join 后按 `{group_kind ∈ tag|error_type, group_value, direction
  (LONG/SHORT), boundary_policy ∈ verified|assumed_or_censored}` 分桶；纯
  SELECT，不落任何新表。
- 口径统一：`position_episode_pnl_exclusion_reasons()`
  （`src/journal/ledger/episode_repository.py`）是 verified/conditional P&L 的
  唯一定义，API 投影（`pnl_summary_eligible`）与聚合共用；条件性成员单独计
  入 `conditional_episode_count`，超过阈值也不进入 stats。
- 门槛 fail-closed：verified 样本 `≥10 笔且 ≥5 个独立 ET 交易日` 才返回
  `stats`（win_rate/avg_pnl/sum_pnl/胜负计数）；否则仅计数并返回机器可读
  `stats_gate.reason ∈ below_sample_threshold | no_verified_pnl_episodes`。
  无任何标注的回合只汇总为单一 `unreviewed` 计数，永不出现盈亏统计。
- 端点：`GET /api/v1/journal/v2/review-insights`
  （`api/v1/endpoints/journal_reviews.py`；schema
  `journal-review-insights/1.0`，含 build_id/build_key/source_kind、
  generated_at、thresholds 回显、按成员数降序的 buckets，见
  `api/v1/schemas/journal_reviews.py`）。
- 页面：`apps/dsa-web/src/components/journal/ReviewInsightsPanel.tsx`
  （「模式观察」面板，挂载于 `/journal` 仓位复盘 tab，`fetchReviewInsights`
  见 `apps/dsa-web/src/api/journal.ts`），含「样本不足仅显示计数」「条件性
  P&L 不参与统计」与「观察到的模式 ≠ 已验证规则」文案。
- 回归：`src/journal/tests/test_review_insights.py`（分桶不混轨、10/5 阈值边
  界、条件排除、latest-revision、零写断言）、
  `api/v1/tests/test_journal_reviews_endpoint.py`（合同 + not_built 空态）、
  `apps/dsa-web/src/components/journal/__tests__/ReviewInsightsPanel.test.tsx`。

## 4. 验收（对照 HANDOFF §16 阶段 C）

- annotation 与真实 schema 字段一一对应，无新自由文本旁路；
- 同类样本 dashboard 不混 policy/regime/direction（分桶键至少含
  direction + boundary policy）；
- 未达门槛不显示 edge；晋升快照可重放；
- 全量 gate + Web gate + 真实页面验证。

# 个人 Playbook 晋升链路合同（阶段 C · 设计冻结稿）

> 状态：设计合同（2026-08-01）；切片 C-1 / C-2 已实现（2026-08-01），C-3 未开始
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
| C-2 | L2/L3 表 + append-only 仓储 + 晋升/退役端点 + Playbook 页面 | 新增 2 张 append-only 表 | 已实现 |
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

### C-2 实现锚点（2026-08-01）

- 表：`src/journal/ledger/playbook_models.py` ——
  `journal_v2_playbook_candidates`（L2：`candidate_key` sha256 UNIQUE、
  `source_bucket_json` 对自由候选可空、`evidence_snapshot_json` +
  `evidence_snapshot_sha256` 创建时冻结）与 `journal_v2_playbook_rules`
  （L3：`UNIQUE(lineage_key, version)`、`UNIQUE(previous_rule_id)`、chain
  CHECK `version=1 无 previous 且 active / version>1 必有 previous`、
  `status ∈ active|retired`）；两表均加入
  `src/journal/ledger/repository.py` 的 `_APPEND_ONLY_TABLE_NAMES`，
  SQLite UPDATE/DELETE 拒绝触发器随 `init_ledger_schema()` 安装。
- 仓储：`src/journal/ledger/playbook_repository.py` ——
  `create_playbook_candidate`（非空/限长校验；同一会话内经
  `collect_review_insight_bucket_evidence`（`review_insights.py` 内与 C-1
  聚合共用 `_aggregate`）重跑聚合冻结快照：build_id/build_key/source_kind、
  成员 episode_ids（有序 ≤200 + 截断标记）、latest annotation revision ids、
  含条件性分桶的计数、10/5 阈值、`generated_at`；桶或构建缺失 →
  `PlaybookConflictError` 零写 fail closed）、`promote_candidate_to_rule`
  （CAS：candidate_key 定位 + active 重放幂等 + retired lineage 需显式
  `allow_new_version` + `expected_current_version`；晋升时重新冻结快照）、
  `retire_playbook_rule`（CAS on 当前版本；追加 `retired` 新版本并逐字复制
  晋升快照）、`list_playbook_candidates` / `list_playbook_rules`（只读，
  newest first，附 `promoted` / `is_latest_version` 投影）。所有失败路径
  零写；幂等以自然键（内容 hash）判定 `duplicate=True`。
- 端点：`GET /api/v1/journal/v2/playbook`、
  `POST /api/v1/journal/v2/playbook/candidates`、
  `POST /api/v1/journal/v2/playbook/candidates/{candidate_key}/promote`、
  `POST /api/v1/journal/v2/playbook/rules/{lineage_key}/retire`
  （`api/v1/endpoints/journal_reviews.py`；schema `journal-playbook/1.0`
  于 `api/v1/schemas/journal_reviews.py`；冲突/陈旧 CAS → 409，校验 →
  422；响应回显冻结快照）。
- 页面：`ReviewInsightsPanel.tsx` 每桶「保存为候选」内联表单（标题 + 规则
  描述，空内容禁用提交）+ `apps/dsa-web/src/components/journal/PlaybookPanel.tsx`
  （候选「晋升为规则」/ 规则版本链「退役」，均带确认步骤与「规则不会影响
  系统评分或榜单，仅是你的决策清单」文案），挂载于 `/journal` 仓位复盘 tab
  「模式观察」下方；API client 见 `apps/dsa-web/src/api/journal.ts`。
- 回归：`src/journal/tests/test_playbook_repository.py`（快照冻结内容、
  幂等重放、fail-closed 零写、CAS、deny triggers、退役快照复制、重晋升
  意图门禁）、`api/v1/tests/test_journal_playbook_endpoint.py`
  （round-trip + 409/422）、
  `apps/dsa-web/src/components/journal/__tests__/PlaybookPanel.test.tsx` 与
  `ReviewInsightsPanel.test.tsx`（表单 gating、确认流、诚实文案）。

## 4. 验收（对照 HANDOFF §16 阶段 C）

- annotation 与真实 schema 字段一一对应，无新自由文本旁路；
- 同类样本 dashboard 不混 policy/regime/direction（分桶键至少含
  direction + boundary policy）；
- 未达门槛不显示 edge；晋升快照可重放；
- 全量 gate + Web gate + 真实页面验证。

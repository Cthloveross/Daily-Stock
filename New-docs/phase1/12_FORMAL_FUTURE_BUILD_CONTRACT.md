# 正式 Future Episode Build 写合同（阶段 B · 切片 1 / 2 / 3 已实现）

> 状态：切片 1（confirm 写路径 + snapshot-fence link 表）已于 2026-07-31 实现并通过
> T1-T9 回归与后端全量 gate；切片 3（Web UI：preview → 正式 build 显式确认）已于
> 2026-08-01 实现并通过前端 lint / vitest / build；切片 2（activation 扩展）已于
> 2026-08-01 实现（无 schema 变更，见 §6 决策记录）：fence build 可显式激活为
> 默认复盘视图，激活前默认视图不变（UI 明示「已构建 ≠ 已生效：激活前默认视图不变」）。
> 实现落点：`src/journal/ledger/position_snapshot_episode_build.py`（confirm 写路径）、
> `journal_v2_episode_build_snapshot_fence_sources`（link 表，`models.py`）、
> `POST /api/v1/journal/v2/episode-builds/position-snapshot`（`journal_positions.py`）；
> Web 侧 `apps/dsa-web/src/api/journalPositions.ts`（`confirmFuturePositionEpisodeBuild`，
> 三个 64-hex key 本地校验 + 回显身份 fail-closed）与
> `apps/dsa-web/src/components/journal/FutureEpisodePreviewSummary.tsx`
> （acceptance 勾选门禁、幂等重放提示、409 后作废预览并要求重新零写预览）。
> 注：为守住 §5「默认视图不变」红线，切片 1 已同步把 `_latest_csv_build` 的
> CSV fallback 显式排除 snapshot-fence build（§6 中原列为切片 2 事项）。
>
> 真源顺序：可执行代码 > 本合同 > HANDOFF §16 阶段 B 概述
>
> 目标：把 fence-bound 零写 preview 升级为显式、append-only 的正式 build，
> 不改变默认视图（activation 为独立切片），零交易动作。

## 0. 切片划分

| 切片 | 内容 | 是否改 schema | 状态 |
|---|---|---|---|
| 1 | 正式 future-build confirm 写路径 + snapshot-fence link 表 | 新增 1 张 link 表（append-only + deny triggers） | 已实现（2026-07-31） |
| 2 | activation 资格扩展到 future build | 否（决策：不改 `EpisodeBuildActivation`，见 §6 决策记录） | 已实现（2026-08-01） |
| 3 | Web UI（preview → 正式 build 按钮 + 显示） | 否 | 已实现（2026-08-01） |

本合同冻结切片 1；切片 2 的约束与最终决策记录在 §6。

## 1. 现状事实（探索结论，file:line 以当前工作树为准）

- `preview_fenced_position_episodes(account_key, expected_fence_key)`
  （`src/journal/ledger/position_snapshot_episode_preview.py:626`）在单一
  `mode=ro + query_only + deny-writes authorizer` 事务内：重算 continuity
  fence 并 CAS 对比、验证 snapshot/canonical 冻结身份、窗口化
  `(boundary_at, source_cutoff_at]` 内 LIVE/US options 事实、OCC 语义身份
  + multiplier 证明、`build_position_episodes(opening_snapshot_complete=True,
  assume_flat_if_missing=False)`、费用守恒校验；返回内存
  `FuturePositionEpisodeBuildPreview`，含 `planned_build_key`、
  `builder_config_sha256`、`evidence_set_sha256`、fence/snapshot/target
  publication/target canonical 全部身份与 episodes 元组。
- canonical 正式 build 的写路径样板：`append_canonical_position_episode_build`
  （`episode_repository.py:3542`）——`BEGIN IMMEDIATE` → 在写事务内**重跑完整
  plan** → CAS 对比 expected sha/build_key → 显式 acceptance 门禁 →
  `_append_prepared_build`（build_key 幂等，duplicate=True 不重写）。
- per-episode 持久化由 `_append_prepared_build` 通用承担（EpisodeBuild 行 +
  StrategyEpisode/PositionEpisode/PositionEpisodeEvidence 行），来源标注靠
  link 表（canonical 用 `EpisodeBuildCanonicalSource`，唯一约束
  `uq_jv2_episode_canonical_link_key`）。
- activation（`activation_repository.py:204-268`）当前硬性只接受
  canonical-linked build；`EpisodeBuildActivation.canonical_set_id/sha256`
  NOT NULL；`_latest_csv_build` 用「无 canonical link」定义 CSV fallback。
- 已知不变量风险：snapshot 开仓边界只体现在 episode 级 provenance，不产生
  PositionEpisodeEvidence 行（evidence 行 CHECK 强制绑定 order XOR fill 观察）。

## 2. 切片 1 写合同

### 2.1 端点

`POST /api/v1/journal/v2/episode-builds/position-snapshot`
（放在 `api/v1/endpoints/journal_positions.py`，沿用该文件的 blanket-409 约定）

请求体（全部 echo 自 GET position-snapshot/preview 的响应）：

| 字段 | 约束 | 语义 |
|---|---|---|
| `expected_fence_key` | `^[0-9a-f]{64}$`（小写，与 preview GET 一致） | 冻结 continuity fence |
| `expected_build_key` | 64-hex（服务端 `.lower()` 归一） | preview 的 `planned_build_key` |
| `expected_evidence_set_sha256` | 64-hex | preview 的 `evidence_set_sha256` |
| `accept_left_censored_openings` | bool，默认 false | 显式接受 snapshot 继承仓位 left-censored、无 broker 成本 |
| `accept_group_fee_scope` | bool，默认 false | 组费只在组级精确时必须显式接受 |

响应沿用 `EpisodeBuildResponse` 形状（duplicate 幂等语义相同），并附
snapshot/fence 身份块。

### 2.2 服务端重验序列（写事务内，全部 fail closed）

1. `BEGIN IMMEDIATE`；
2. 调用 **in-session 版 preview 核心**（见 §3 重构）重算完整
   `FuturePositionEpisodeBuildPreview`；
3. CAS：重算 `fence_key == expected_fence_key`、
   `planned_build_key == expected_build_key`、
   `evidence_set_sha256 == expected_evidence_set_sha256`，任一不等 → 409
   `future build plan changed; request a new preview`；
4. `fee_conserved == True` 且 `unresolved_evidence_count == 0`，否则拒绝；
5. `opening_boundary_policy == 'complete_snapshot'` 且所有 episode
   `left_boundary_verified`（preview 已保证，写路径复验）；
6. snapshot 继承的 left-censored episodes 存在时必须
   `accept_left_censored_openings=True`；
7. `group_fee_affected_episode_count > 0` 且腿级费用不完整时必须
   `accept_group_fee_scope=True`；
8. `_append_prepared_build` 复用追加（build_key 幂等；duplicate 校验必须
   核对 link 表身份，不能只信 build_key 相等——见 §4 测试 T7）；
9. 追加 snapshot-fence link 行（§2.3）；
10. 读回 `get_episode_summary(build_id)` 一致，否则 500。

### 2.3 新 link 表 `journal_v2_episode_build_snapshot_fence_sources`

镜像 `EpisodeBuildCanonicalSource` 模式，UPDATE/DELETE deny triggers 同批安装：

- `link_key`＝sha256_json({episode_build_key, snapshot_id, snapshot_key,
  fence_key, target_canonical_set_id, target_canonical_set_sha256,
  projection_name, projection_version})，UNIQUE；
- `episode_build_id` FK UNIQUE（一 build 一来源）；
- `snapshot_id`/`snapshot_key`、`fence_key`、`continuity_policy_version`；
- `target_publication_id`/`target_publication_key`；
- `target_canonical_set_id` FK、`target_canonical_set_sha256`、
  `source_cutoff_at`、`boundary_at`；
- `projection_name='position_snapshot_fenced_canonical_projection'`、
  `projection_version='1.0.0'`（与 preview 常量同源引用，不硬编码字面量）。

### 2.4 build_key 覆盖性要求

`planned_build_key` 的派生必须覆盖：source_kind、snapshot_id/key、fence_key、
target canonical id/key/sha256/source_cutoff、boundary_at、builder
name/version、builder_config_sha256、projection name/version。实现时先核对
preview 现有派生是否已全覆盖；缺任何一项须在 preview 内补齐（会改变已发布
preview 的 planned_build_key——当前无已持久化消费方，允许）。

## 3. 必要重构：preview 核心 in-session 化

`preview_fenced_position_episodes` 拆为：

- `preview_fenced_position_episodes_in_session(connection, session, account_key,
  expected_fence_key)`：纯计算核心（复用现有
  `assess_position_snapshot_continuity_in_session` 模式）；
- 原函数保持签名不变，内部开 ro 连接后调用 in-session 核心——**现有零写语义
  与全部现有测试不得改变**；
- confirm 写路径在 `BEGIN IMMEDIATE` 事务内调用 in-session 核心。
  注意：写事务内 authorizer/deny-writes 不适用，靠「核心函数只读不写」由
  测试证明（T8 零业务写对照）。

## 4. 测试合同（最低集）

- T1 happy path：ready fence → confirm → build + link 行追加，counts/fees
  与 preview 一致；
- T2 幂等：同参数重放 duplicate=True，零新行；
- T3 stale fence：confirm 前出现新 publication → 409，零写；
- T4 build_key/evidence hash 不匹配 → 409，零写；
- T5 缺 acceptance（left-censored / group fee）→ 409，零写；
- T6 deny triggers：link 表 UPDATE/DELETE 被拒；
- T7 duplicate 身份核对：同 build_key 但 link 身份不符 → 拒绝而非静默 duplicate；
- T8 confirm 失败路径全程零业务写（表 count + content hash 对照）；
- T9 API 层：请求校验、409 映射、读回一致性。

## 5. 不做什么（切片 1 红线）

- 不改 activation 资格、不写 activation 行——build 追加后默认视图不变；
- 不把 GET preview 改成写、不复用 canonical POST 端点；
- 不回填 snapshot 成本到 builder（`snapshot_cost_context_excluded_left_censored`
  政策不变）；
- 不在 UI 宣称「已构建=已生效」。

## 6. 切片 2（activation 扩展）约束与决策记录（2026-08-01 已实现）

原评审前已知约束：

- `EpisodeBuildActivation.canonical_set_id/sha256` NOT NULL → 曾列出 nullable +
  kind 判别，或并行 provenance link 两个方案；append-only 保护表的 schema
  变更须走 HANDOFF §18.2 备份流程；
- `_eligible_canonical_target` 三元组返回值被 activate 与默认读 resolver 双消费；
- `_latest_csv_build` 以「无 canonical link」定义 fallback，新 link 种类会改变
  该语义，须显式排除（切片 1 已提前落实）；
- data-health（refresh_repository）与 dsa-web journalCanonical 客户端均对
  activation 形状有断言。

**决策记录：不改 schema，以目标事实集身份复用 NOT NULL 列。**

- `EpisodeBuildActivation.canonical_set_id/sha256` 保持 NOT NULL 不变：激活
  fence build 时写入其 `EpisodeBuildSnapshotFenceSource` link 冻结的
  `target_canonical_set_id / target_canonical_set_sha256`。语义上诚实——
  fence build 的回合正是该冻结目标事实集在快照边界后窗口的严格重放；
  不引入 nullable 列、不建并行 activation 表、不重建受保护表。
- `_eligible_canonical_target` 重构为 `_eligible_activation_target`：canonical
  link 或 snapshot-fence link 之一即可激活；fence 分支 join
  `CanonicalEvidenceSetRecord` 校验目标集行存在、指纹与 link 一致、账户/
  broker 匹配且 `analysis_ready`，任一不符 fail closed。两种 link 均无的
  build 保留原错误文案（`only canonical-linked Episode builds can be
  activated`），CSV fallback 语义不变。返回值带 `source_kind` 判别，
  activate 与默认读 resolver 双消费；默认读交叉核对 activation 身份与
  link（canonical 或 fence target）一致。
- 新增 additive 激活确认 `accept_left_censored_openings`（默认 false）：目标
  build 报告 `left_censored_episode_count > 0` 时必须置真（报告缺该字段时
  从 build 的 episode 行派生计数兜底）；activation key 仅在 flag 置真时
  参与哈希派生，历史激活 key 与幂等重放不受影响。canonical build 的
  `accept_assumed_flat` / `accept_group_fee_scope` 语义不变（fence build
  为 `complete_snapshot`，assumed-flat 门禁天然通过）。
- data-health：激活的 fence build 目标集即最新 canonical set 时，
  `get_journal_refresh_status` 视为 `current/none`，不再要求重复 activate
  最新 canonical build；无 canonical build 时 pending 仍诚实为 `build`。
- Web：显式查看 canonical / fence build 时提供独立激活卡片（按目标 build
  实际口径强制勾选 assumed-flat / 组费 / left-censored），确认区明示
  「激活后默认视图切换，可再激活其他构建切回，但无法回到零激活的 CSV
  默认状态」；CSV build 无激活入口。
- 实现落点：`activation_repository.py`（eligibility/acceptance/key 派生）、
  `refresh_repository.py`（data-health 对齐）、`api/v1` activation 请求
  schema 与 409 映射、`apps/dsa-web` activation 客户端 + `PositionEpisodesPanel`
  + `FutureEpisodePreviewSummary` 文案；回归见
  `src/journal/tests/test_position_snapshot_episode_build.py`（fence 激活
  五连测）与 `api/v1/tests/test_journal_endpoints.py`。

## 7. 验收（切片 1）

- 后端全量 gate 通过且新增 T1–T9 全绿；
- 正式库上仅做只读验证（当前 0 confirmed snapshot，无法真实 confirm——真实
  演练要等用户完成一次 snapshot confirm + 后续 refresh publication）；
- HANDOFF §5.3「当前缺口」与 §16 阶段 B、`docs/CHANGELOG.md` 同步；
- 未经用户明确确认不 commit/push。

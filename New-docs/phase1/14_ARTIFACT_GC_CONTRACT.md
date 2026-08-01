# 短期 Artifact GC 合同（阶段 F · F-2 · 设计冻结稿）

> 状态：设计合同（2026-08-01 冻结）；F-2a / F-2b 均未实现
>
> 真源顺序：可执行代码 > 本合同 > HANDOFF §5.2 / §15 P3.7 概述
>
> 目标：为短期 refresh / position-snapshot preview artifact 提供**显式、有界、
> 留回执、可回滚**的过期清理；绝不触碰任何已确认证据行、任何 publication /
> canonical / episode / snapshot 事实，绝不引入静默后台删除。
>
> 背景：HANDOFF §5.2（L384）与 §15 P3.7（L1179）记录“短期 refresh/snapshot
> artifact 的过期清理尚未实现”为运维技术债；
> `New-docs/architecture/06_MATURITY_EXECUTION_PLAN.md` F-2 行要求
> “短期表非 append-only 保护范围需先核实”。本合同即核实结论 + 冻结设计。

## 0. 探索结论（事实基线，file:line 与 2026-08-01 只读 DB 查询）

### 0.1 保护现状核实：F-2 原假设不成立

**核实结论：所有 artifact payload 表都在 SQLite UPDATE/DELETE deny-trigger
保护范围内。**“短期表不在 append-only 保护范围”这一原假设为假。

| 表 | expires_at | deny trigger（代码声明） | deny trigger（正式库实测 2026-08-01） | 属于 `repository.py` `_APPEND_ONLY_TABLE_NAMES`（L89-120）？ |
|---|---|---|---|---|
| `journal_v2_refresh_artifacts` | 有，NOT NULL（`refresh_models.py:54`；CHECK `expires_at > recorded_at` L63-66） | 有：`refresh_repository.py:72-75` `_IMMUTABLE_TABLES` + `init_refresh_schema` L183-198 安装 | **存在**（`trg_journal_v2_refresh_artifacts_delete_immutable` / `_update_immutable`） | 否（独立模块自管） |
| `journal_v2_refresh_publications` | 无 | 同上（同一 tuple） | **存在** | 否（独立模块自管） |
| `journal_v2_position_snapshot_artifacts` | 有，NOT NULL（`position_snapshot_models.py:107`；CHECK L127-130） | 有：模型层 `after_create` DDL hook（`position_snapshot_models.py:399-418`）+ metadata 修复（L421-459）+ 仓储 `_IMMUTABLE_TABLES`（`position_snapshot_repository.py:85-89`，安装 L1324-1330） | **存在** | 否（独立模块自管） |
| `journal_v2_position_snapshots` / `_members` | 无 | 同上 | **存在** | 否（独立模块自管） |
| `regime_premarket_artifact_bundles` | 有（`premarket_models.py:157`；CHECK `target_as_of <= expires_at` L190-193） | 有：`premarket_repository.py:60-63` `_APPEND_ONLY_TABLES` + guards L464-476 | **未安装**（feature 默认关闭，`src/config.py:739`；`init_premarket_evidence_schema` 从未在正式库运行；首次运行即补装，ready 检查 L426-441 要求 trigger 存在） | 否（独立模块自管） |
| `regime_premarket_artifact_ingestions` | 无（DB 时间戳回执） | 同上 | 未安装（同上） | 否 |
| `regime_premarket_prefetch_runs` | 仅 `lease_expires_at`（租约，非 artifact TTL） | **无**——设计上可变协调状态（`premarket_models.py:3-8, 41-42`；不在 `_APPEND_ONLY_TABLES` L60-63） | 无 | 否 |

因此对任务 (a) 的回答：**没有任何一张 artifact 表“可以直接 DELETE”**。
唯一无保护的 `regime_premarket_prefetch_runs` 不是 payload 存储（是 slot
租约/审计行，且是 `regime_premarket_artifact_bundles.slot_key` 的 FK 父表，
`premarket_models.py:138-142`），本合同明确不触碰它。GC 必须采用 §3 的
“单事务受控删除”机制，而非普通 DELETE。

### 0.2 过期语义现状（读侧如何对待 stale artifact）

- refresh confirm 在过期时 fail closed：`refresh_repository.py:772-773`
  （`refresh preview expired; run a new read-only refresh`）；TTL 默认 30
  分钟、上限 240 分钟（L69、L599-600），`expires_at` 写入于 L654。
- position snapshot confirm 同样 fail closed：`position_snapshot_repository.py:2236-2238`
  （docstring L2149 “Atomically confirm one non-expired artifact”）；TTL 默认
  30 分钟、上限 240 分钟（L62、L1542-1543），`expires_at` 写入于 L1626。
- premarket formal 选择跳过对 target 已过期的 bundle：`premarket_repository.py:1771`
  （`if bundle.expires_at < target: continue`）——注意这是**相对 target 的
  过期**：历史 target 的时间旅行读仍可选中旧 bundle，故 bundle 不是纯缓存。
- 即：**过期 artifact 已经在所有写路径上被拒绝，只是行本身从不回收**。

### 0.3 引用关系（谁会永远需要一个 artifact 行）

- `journal_v2_refresh_publications.refresh_artifact_id` → FK 指向
  `journal_v2_refresh_artifacts.id`（`refresh_models.py:90-94`；一 artifact
  至多一 publication，UNIQUE L119-122）。continuity 严格重放**只经由
  publication 到达 artifact**（`load_verified_refresh_publication_in_session`，
  `refresh_repository.py:268-274`；HANDOFF §5.3 L417）。
- `journal_v2_position_snapshots.artifact_id` → FK 指向
  `journal_v2_position_snapshot_artifacts.id`（`position_snapshot_models.py:183-187`；
  UNIQUE L227-230）。members 只引用 snapshot（L305-309），不直接引用 artifact。
- `PositionSnapshotArtifact.refresh_publication_id` / `ConfirmedPositionSnapshot.refresh_publication_id`
  引用的是 **publication**（`position_snapshot_models.py:70-73, 194-197`），
  不构成对未确认 refresh artifact 的反向引用。
- 全库 `PRAGMA foreign_keys=ON`（`src/storage.py:761`）：误删被引用行会直接
  触发 FK 约束失败并回滚——这是谓词之外的第二道保险。
- 状态读路径：`get_journal_refresh_status` 只读取**每账户最新一条** artifact
  （`refresh_repository.py:951-962`，排序 `recorded_at DESC, id DESC`），其
  `evidence_blocked / pending_stage` 派生依赖该行（L1066-1101）。按 id 读取
  artifact 的唯一非测试调用方是 confirm 流程本身（`refresh_repository.py:769`）。
  → 删除“最新一条”会改变用户可见状态，谓词必须保留它（§2）。

### 0.4 正式库现状（`data/stock_analysis.db`，只读 `mode=ro`，2026-08-01 08:17 UTC）

| 事实 | 数值 |
|---|---|
| `journal_v2_refresh_artifacts` 总数 | 5（**全部已过期**） |
| 其中无 publication 引用 | 4（账户最新行 id=5 恰为被 publication 引用的那条） |
| 满足谓词 (2)+(3)（过期 + 无引用 + 非最新） | 4，payload 共 3,938,098 bytes |
| 再叠加谓词 (1) GRACE_DAYS=7 后的当日候选 | 0（4 条的 `expires_at` 分别为 07-30 与 08-01，均未过期满 7 天；至 2026-08-08 全部进入候选） |
| `journal_v2_refresh_publications` | 1 |
| refresh artifact payload 总量 | 5,043,439 bytes（约 1 MB/条） |
| `journal_v2_position_snapshot_artifacts` / `_snapshots` / `_members` | 0 / 0 / 0 |
| `regime_premarket_prefetch_runs` / `_artifact_bundles` / `_ingestions` | 0 / 0 / 0 |
| 全库大小 | 43,731 页 × 4,096 = 约 179 MB |

规模判断要诚实：当前可回收约 3.9 MB（占全库约 2%）。F-2 的价值是**封顶
未来增长**（每次 preview 约 1 MB，正常使用每日 1-N 次），不是立刻省空间。

## 1. 逐表裁决

| 表 | 裁决 | 依据 |
|---|---|---|
| `journal_v2_refresh_artifacts` | **GC 范围内**（§2 谓词 + §3 机制） | 过期后无任何合法未来用途（0.2）；未被 publication 引用即不在任何重放链上（0.3） |
| `journal_v2_position_snapshot_artifacts` | **GC 范围内**（同机制；当前 0 行，机制与测试先行） | 同上；未被 confirmed snapshot 引用即死行 |
| `journal_v2_refresh_publications` | **永不删除** | append-only 发布回执；continuity/重放的锚点（HANDOFF §5.3） |
| `journal_v2_position_snapshots` / `_members` | **永不删除** | 已确认证据；HANDOFF §18.3“Append-only build 不删除” |
| `regime_premarket_artifact_bundles` / `_ingestions` | **不进 GC 范围** | 非纯缓存（历史 target 时间旅行读，0.2）；feature 默认关、0 行；若未来需要 retention，另立合同——**BLOCKED-needs-decision**（见 §8） |
| `regime_premarket_prefetch_runs` | **不进 GC 范围** | 可变协调 + slot 审计 + bundles 的 FK 父表；无 payload 负担 |
| `opportunity_premarket_cycle_slots/attempts/events`、`opportunity_outcome_maintenance_runs` | **不进 GC 范围** | 研究周期证据/租约（`src/opportunities/cycle_models.py`、`maintenance_models.py`），其 `lease_expires_at` 是租约不是 artifact TTL；正式库有 deny triggers（实测存在） |
| 其余全部 `journal_v2_*` 证据表（`repository.py:89-120` tuple） | **永不触碰** | 账本红线 |

## 2. 删除谓词（predicate_version = `artifact_gc_predicate_v1`）

三个条件**同时**成立才是候选，并且逐表拼出真实列名：

### 2.1 `journal_v2_refresh_artifacts`

```sql
SELECT a.id
FROM journal_v2_refresh_artifacts AS a
WHERE
  -- (1) 已过期且超出宽限期（GRACE_DAYS = 7；比较在 SQLAlchemy 层以 UTC
  --     datetime 绑定参数完成，不用 datetime('now') 文本比较）
  a.expires_at < :now_utc_minus_grace
  -- (2) 从未确认：无 publication 引用（refresh_models.py:90-94）
  AND NOT EXISTS (
    SELECT 1 FROM journal_v2_refresh_publications AS p
    WHERE p.refresh_artifact_id = a.id
  )
  -- (3) 非该账户最新一条（保持 get_journal_refresh_status 输出不变，
  --     排序与 refresh_repository.py:957-961 完全一致）
  AND a.id <> (
    SELECT a2.id FROM journal_v2_refresh_artifacts AS a2
    WHERE a2.broker = a.broker AND a2.account_key = a.account_key
    ORDER BY a2.recorded_at DESC, a2.id DESC LIMIT 1
  )
ORDER BY a.recorded_at ASC, a.id ASC
LIMIT :batch_limit;   -- 默认 500
```

### 2.2 `journal_v2_position_snapshot_artifacts`

```sql
SELECT a.id
FROM journal_v2_position_snapshot_artifacts AS a
WHERE
  a.expires_at < :now_utc_minus_grace
  -- 从未确认：无 confirmed snapshot 引用（position_snapshot_models.py:183-187）
  AND NOT EXISTS (
    SELECT 1 FROM journal_v2_position_snapshots AS s
    WHERE s.artifact_id = a.id
  )
  AND a.id <> (
    SELECT a2.id FROM journal_v2_position_snapshot_artifacts AS a2
    WHERE a2.broker = a.broker AND a2.account_key = a.account_key
    ORDER BY a2.recorded_at DESC, a2.id DESC LIMIT 1
  )
ORDER BY a.recorded_at ASC, a.id ASC
LIMIT :batch_limit;
```

谓词注记：

- **GRACE_DAYS=7**：TTL 上限只有 240 分钟，7 天宽限完全覆盖排障/取证窗口，
  同时保证“今天预览、今天被清”的竞态在设计上不存在；
- 条件 (3) 的代价是每账户永久保留至多 1 条过期 artifact（约 1 MB）——这是
  为了 `get_journal_refresh_status` 的 `evidence_blocked` / `pending_stage`
  语义逐字节不变（0.3），显式接受；
- 候选集在**写事务内重算**（§3 步骤 3），事务外的预扫描只用于 dry-run 展示；
- 删除不会自动收缩 DB 文件（空闲页复用）；`VACUUM` 明确不在本合同范围（§7）。

## 3. 执行模型（apply 模式，单事务受控删除）

普通 DELETE 会被 deny trigger ABORT（0.1），因此 apply 采用**单事务内
“摘 DELETE 触发器 → 按谓词删除 → 原样重建触发器”**。SQLite 的 DDL 是事务性
的：未提交的 DROP TRIGGER 对其他连接不可见，且 `BEGIN IMMEDIATE` 已排他写；
任何一步失败 → ROLLBACK → 触发器与行原样恢复。

每张表一次 apply 的固定序列：

1. 事务外预检：仅支持 SQLite（非 SQLite fail closed）；`init_refresh_schema()` /
   snapshot schema 已就绪；备份前置已满足（§4）；
2. `BEGIN IMMEDIATE`；
3. 在事务内**重算**候选集（§2 SQL，含 LIMIT）；空集 → 直接写 0 计数回执并提交;
4. 记录候选 `COUNT(*)`、`SUM(LENGTH(payload_json))`、`MIN/MAX(id)`、
   有序 id 列表的 sha256；
5. `DROP TRIGGER trg_<table>_delete_immutable`（**只摘 DELETE 触发器**；
   UPDATE 触发器全程在位）；
6. `DELETE FROM <table> WHERE id IN (<候选>)`；断言 rowcount == 候选数，
   不等 → ROLLBACK；被引用行如混入会先被 `PRAGMA foreign_keys=ON`
   （`storage.py:761`）以 FK 约束失败拦下并整体回滚；
7. 以**触发器属主模块的同一 DDL 常量**重建触发器（refresh 表引用
   `refresh_repository.py:190-198` 的 DDL 来源；snapshot 表引用
   `position_snapshot_models.py:404-418` 的 DDL 来源；实现时抽成共享常量，
   禁止在 GC 内手抄第二份触发器文本，避免语义漂移）；
8. 事务内查询 `sqlite_master` 断言该表 UPDATE + DELETE 两个触发器都在；
9. 追加一条 GC 回执行（§3.1）；
10. `COMMIT`；提交后再次断言触发器在位，并输出结构化日志
    （表名、候选数、删除数、回收字节、耗时、backup 标识）。

### 3.1 回执表 `journal_v2_artifact_gc_receipts`（新增，append-only）

删除来自证据毗邻存储的任何行都必须留下不可变回执（与 ingestion /
publication 回执同一文化）。新表加入 `repository.py` 的
`_APPEND_ONLY_TABLE_NAMES`（L89-120，沿用 Playbook C-2 的入表模式），
UPDATE/DELETE deny triggers 随 `init_ledger_schema()` 安装：

| 列 | 语义 |
|---|---|
| `receipt_key` sha256 UNIQUE | 内容派生键（table_name + 有序删除 id 列表 sha256 + started_at） |
| `run_kind` | `dry_run` \| `apply`（CHECK：dry_run → deleted_count = 0） |
| `table_name` | 本次目标表（CHECK 只允许 §1 范围内两张表） |
| `predicate_version` / `grace_days` / `batch_limit` | 谓词参数快照 |
| `candidate_count` / `deleted_count` / `payload_bytes_reclaimed` | 计数 |
| `deleted_ids_json` + `deleted_ids_sha256` | 有序 id 列表（≤ batch_limit）与指纹 |
| `backup_path` + `backup_sha256` | apply 必填；dry_run 可空 |
| `started_at` / `completed_at` / `recorded_at` | UTC |

### 3.2 入口：F-2a 仅 CLI，无 API、无启动清扫、无后台任务

- 入口固定为 `scripts/artifact_gc.py`（薄壳，核心在
  `src/journal/ledger/artifact_gc.py`）；
- **默认 dry-run**：打印候选 id / 计数 / 字节，零写（回执亦可 `--receipt`
  显式落 dry_run 行，默认不落）；
- `--apply` 必须与 `--backup-path <file>` 同时给出（§4）；可选
  `--limit N`（默认 500）、`--grace-days N`（默认 7，只允许 ≥1）、
  `--table`（默认两张全跑）；
- **不做** startup sweep、**不挂** LaunchAgent、**不加** API 端点——仓库
  护栏是“显式 + 有日志 + 有界”，任何用户可见数据的静默后台删除都被禁止；
  调度化是 F-2b 的独立决策（§8）。

## 4. 零风险顺序（备份前置，对照 HANDOFF §18.2）

候选行按 §2 谓词是**可证明的死行**（过期 → confirm 永拒；无引用 → 不在任何
重放链；非最新 → 无状态读者），即“纯缓存”论证成立。**但机制本身触碰受保护
表的触发器**，这正是 §18.2 所防护的操作类别，因此备份前置不可豁免：

1. apply 前必须存在一份**当日、经 SQLite backup API 创建并通过
   integrity/count 验证**的备份（流程同 HANDOFF §12 2026-07-31 基线备份，
   L460）；CLI 以 `--backup-path` 接收，校验文件存在、可读、`PRAGMA
   integrity_check=ok`，并把路径 + sha256 写入回执；
2. 首次演练顺序（§18.2 第 4 步的类比）：先在**备份副本**上 dry-run → apply
   → 跑 §5 全部断言；副本演练通过后，才在用户明确确认下对正式库 apply；
3. 正式库 apply 前后各留一次 `get_journal_refresh_status` 输出对照
   （必须逐字段一致）；
4. 回滚方式：单事务内任何失败自动 ROLLBACK；已提交的误删（理论上被
   谓词 + FK + rowcount 断言三重排除）按 §18.3“错误证据 batch”incident
   流程从已验证备份恢复——这也是备份前置存在的原因。

## 5. 测试合同（最低集，全部离线）

| # | 断言 |
|---|---|
| T1 | 过期(>grace) + 无引用 + 非最新 的 refresh artifact 被 apply 删除；回执 candidate/deleted/bytes 与实际一致 |
| T2 | 被 publication 引用的 artifact **永不**删除（即使过期）；同样断言 snapshot 侧被 `journal_v2_position_snapshots.artifact_id` 引用的 artifact 不删 |
| T3 | 未过期、或过期但在 grace 内的 artifact 不删 |
| T4 | 每账户最新一条 artifact 即使过期+无引用也保留；GC 前后 `get_journal_refresh_status` 全字段相等 |
| T5 | dry-run 零写：两张目标表 + 回执表 count 与 content hash 前后一致，但报告的候选集与 apply 将删集合完全相同 |
| T6 | 幂等：同参数第二次 apply 删除 0 行，新回执 deleted_count=0 |
| T7 | 触发器不变量：apply 成功后两表 UPDATE/DELETE 触发器在 `sqlite_master` 中在位且外部 DELETE/UPDATE 仍被 ABORT；DELETE 步骤注入失败（rowcount 不符）→ ROLLBACK 后行与触发器原样 |
| T8 | 禁区零触碰：`journal_v2_refresh_publications`、`journal_v2_position_snapshots/_members`、全部 `_APPEND_ONLY_TABLE_NAMES` 证据表、`regime_premarket_*`、`opportunity_*` 在 apply 前后 count + content hash 不变 |
| T9 | fail closed：无 `--backup-path` 的 `--apply` 拒绝执行；备份文件缺失/integrity 失败拒绝；非 SQLite 引擎拒绝 |
| T10 | 回执表自身 append-only：UPDATE/DELETE 被 deny trigger 拒绝；回执 `deleted_ids_sha256` 可由 `deleted_ids_json` 复算 |

## 6. 明确不在范围（红线）

- `logs/`——已有独立机制 `LOG_RETENTION_DAYS`（`src/logging_config.py:199-204`，
  F-3 项，等用户配置），GC 不碰文件系统日志；
- `data/backups/`——备份留档人工管理，任何自动删除备份都是事故；
- 任何 `journal_v2_*` 证据行：observations / canonical / episodes / builds /
  activations / annotations / playbook / publications / confirmed snapshots
  与 members；
- `regime_premarket_*` 三表与 `opportunity_*` 周期/结局表（§1 裁决）；
- 旧 Phase 0 Mirror 表（`journal_trades` / `journal_orders` /
  `journal_shadow_trades`——其 `expires_at`/`expiry` 是期权合约到期，不是
  artifact TTL）；
- `VACUUM` / 文件收缩、前端构建产物（`src/webui_frontend.py`）、
  `apps/dsa-web/e2e/.artifacts`（已 gitignore，HANDOFF L1098）。

## 7. 被否决的替代机制（记录，防止重提）

- **条件化触发器**（把无条件 ABORT 改成“过期+无引用可删”）：把删除能力
  永久下放给所有写入方，扩大 ambient authority；且触发器 DDL 在三处属主
  （`refresh_repository.py`、`position_snapshot_models.py` hook、
  `position_snapshot_repository.py` 修复路径）需同步改语义，漂移风险高。否决。
- **status 标记 / payload 置空**：UPDATE 同样被 deny trigger 拒绝，且
  `payload_json` NOT NULL + hash CHECK 不允许挖空。不可行。
- **分区/影子表轮转**：为 ~1 MB/天 的负担引入双表读路径，违背“稳定性优先、
  克制基础设施迁移”。否决。

## 8. 切片计划与验收

### F-2a（实现切片，本合同冻结的全部强制内容）

改动面：

- `src/journal/ledger/artifact_gc.py`（谓词 + 单事务执行器 + 回执写入）；
- `src/journal/ledger/artifact_gc_models.py`（回执表模型）+
  `repository.py` `_APPEND_ONLY_TABLE_NAMES` 追加回执表；
- 触发器 DDL 抽成属主模块共享常量（refresh / snapshot 两处，行为不变）；
- `scripts/artifact_gc.py` CLI（默认 dry-run）；
- 测试 T1-T10；
- 文档：HANDOFF §5.2 L384 / §15 P3.7 技术债措辞更新、
  `docs/CHANGELOG.md`、本合同状态行。

验收：

- 全量后端 gate + T1-T10 全绿；
- 备份副本演练：dry-run 报告的候选集必须与执行当日按 §2 谓词的只读 SQL
  复算逐 id 一致（2026-08-01 基线：忽略 grace 为 4 条 / 3,938,098 bytes；
  含 GRACE_DAYS=7 当日为 0 条，2026-08-08 起 4 条全部进入候选），apply 后
  T4/T7/T8 断言在副本实测通过；
- 正式库 apply 仅在用户明确确认 + 当日验证备份存在时执行，并留回执；
- 无新增 env 配置（F-2a 零配置）；未经确认不 commit/push。

### F-2b（可选调度切片，**默认不做**）

- 内容：env-gated 定期 dry-run→apply（如 `ARTIFACT_GC_ENABLED`，默认 false，
  新增配置须同步 `.env.example` 与文档）；
- **BLOCKED-needs-decision（两项，未决前 F-2b 不开工）**：
  1. 调度模式下 §4 备份前置如何满足（无人值守 apply 与“备份先行”天然冲突；
     可能的解是调度只 dry-run + 提醒，apply 永远人工）；
  2. `regime_premarket_artifact_bundles` 是否需要 retention（0.1/§1：时间
     旅行读语义使其非纯缓存；feature 默认关、0 行，无现实压力）。

## 9. 交付说明模板（对照 AGENTS.md §9）

F-2a 交付时必须写明：改了什么（上述文件清单）、为什么（本合同）、验证情况
（gate + T1-T10 + 副本演练数字）、未验证项（正式库 apply 是否执行）、风险点
（触发器窗口、误删——由 T7/T8/备份覆盖）、回滚方式（§4 第 4 条）。

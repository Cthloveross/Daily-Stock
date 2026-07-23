# Phase 1.3B · OpenAPI 只读证据与 canonical persistence

> 状态：严格解析、Web/API plan/confirm、identity projection、append-only persistence 与 canonical set 版本化已落地（2026-07-21）。
>
> 数据口径：每次 plan 只选择目标账户**最新一份 accepted CSV batch**作为完整基线；更早 CSV batch 继续保留审计，但不参与 identity projection、canonical 统计或 Episode 输入选择。
>
> 产品边界：项目永久只读，不读取交易密码，不解锁交易，不下单、改单或撤单。

本文中的 1,089 / 3,542 等数字已同时经过零证据行写入 plan、隔离数据库幂等验收和正式本地 confirm 复核。服务首次初始化时可以创建缺失表和 append-only trigger；正式证据状态以 confirm 响应、ImportBatch 和 canonical set 记录为准。

本地正式验收（2026-07-21）：正确 CSV baseline 为 batch 2，旧 batch 1 仅保留审计；OpenAPI 已作为 batch 3 追加 1,089 orders / 2,107 fills / 1,058 fees，并保存 1,089 order links、1,136 deal links、244 fill-set attestations。canonical set 为 3,542 orders / 5,400 fills、0 blockers；同一 export 再确认时所有新增计数均为 0。该确认没有触发 Episode rebuild 或任何交易动作。

## 1. 本阶段解决什么

Phase 1.3A 已完成去标识化 OpenAPI export 的严格解析和纯 canonical reader。Phase 1.3B 补齐从“可预览”到“可审计保存”的受控链路：

1. plan 不追加任何 Journal 证据行，读取最新 accepted CSV 基线，执行窗口对账、order/deal identity projection、fill-set attestation 和 canonical impact 计算；
2. confirm 重新计算同一计划，拒绝 stale `preview_key`，并要求用户对局部窗口显式确认；
3. 一次事务追加 OpenAPI order/fill/fee observations、identity links、fill-set attestations 和版本化 canonical set；任一步失败都不留下半套证据；
4. 同一 export 重复确认返回 `already_present`，追加计数全部为 0，不重复计算成交或费用；
5. OpenAPI confirm 不写 legacy Journal，也不触发 Episode rebuild。现有“仓位复盘”在后续 canonical → Episode 接线完成前仍展示原 immutable Episode build。

## 2. API 与 Web 交互

| 方法 | 路径 | 语义 |
|---|---|---|
| `POST` | `/api/v1/journal/v2/openapi-imports/preview` | 仅校验去标识化只读 JSON；不打开或写入 Journal 数据库 |
| `POST` | `/api/v1/journal/v2/openapi-imports/plan` | 不追加证据行；读取最新 accepted CSV 基线并返回覆盖率、计划写入量、canonical 影响与 blockers；服务初始化可能创建缺失 schema |
| `POST` | `/api/v1/journal/v2/openapi-imports/confirm` | 携带精确 `preview_key`；局部窗口还必须传 `acknowledge_partial_window=true`，随后原子追加整套不可变证据 |

Journal 的“交易证据”页会先展示覆盖范围、窗口外未验证数量、计划写入量、canonical 输出与 issues。只有 `confirm_allowed=true` 且用户勾选局部范围确认后才能保存。

confirm 响应固定声明：

- `legacy_journal_written=false`；
- `episode_build_triggered=false`；
- `trading_action_performed=false`。

因此“证据已经保存”和“PositionEpisode 已重建”是两件独立的事，界面不能把前者表述为后者。

## 3. Identity 与 canonical 规则

### 基线选择

- 只选择 `broker=moomoo`、目标 `account_key`、`source_kind=csv`、`status=accepted` 中最新记录；
- 旧 CSV batch 不参与本次 canonical 输入，也不会因 OpenAPI 导入而被更新或删除；
- OpenAPI 局部窗口不能替换 3,542 单的完整 CSV 分母；窗口外 CSV 事实保留为 `selected_batch_stable`，但仍标记为尚未由该 OpenAPI 窗口交叉验证。

### 显式身份投影

- order 只有经过对账的一对一关系才创建 broker order identity link；
- deal 只有逐笔证据可证明一对一且时间信息不冲突时才创建 broker deal identity link；
- 逐笔无法安全配对、但订单级 count/quantity/VWAP 等价时，API fill set 作为 authoritative，CSV weak fill set 通过 attestation 作为 shadow provenance 保留；
- reader 不根据“看起来相似”做模糊配对，也不删除原始 observation。

### 原子与幂等

confirm 会重新 plan 并校验 `preview_key` 和预期 canonical hash。OpenAPI batch、费用、links、attestations、canonical members/issues/provenance 在同一事务中追加，并受 append-only UPDATE/DELETE 拒绝 trigger 保护。稳定 batch key、link key、attestation key 与 canonical set hash 共同保证重复确认幂等。后续不同窗口会在最新 CSV 基线上累积所有 accepted OpenAPI batches；重叠窗口重复出现的同一 broker order/deal 会复用既有身份凭证，不重复建立 link。

## 4. 正确数据的零证据行写入计划结果

对 2026-06-19 23:59:59 至 2026-07-19 23:59:59 ET 的去标识化只读窗口，与最新 accepted CSV 基线对账：

| 覆盖项目 | 结果 |
|---|---:|
| OpenAPI orders / CSV baseline orders | 1,089 / 3,542 |
| Coverage ratio | 30.75% |
| Window outside unverified orders | 2,453 |
| OpenAPI fills | 2,107 |
| OpenAPI fee observations | 1,058 |
| Reconciliation differences | 全部为 0 |
| Scope | `partial_window` |
| `confirm_allowed` | `true`（confirm 仍需显式确认局部范围） |

首次 confirm 的计划追加量为：

| 不可变记录 | 计划新增 |
|---|---:|
| OpenAPI order observations | 1,089 |
| OpenAPI fill observations | 2,107 |
| OpenAPI fee observations | 1,058 |
| Order identity links | 1,089 |
| Deal identity links | 1,136 |
| Order fill-set attestations | 244 |
| Canonical sets | 1 |

1,136 条逐笔成交可建立 broker deal link；其余 971 条 CSV weak fills 通过 244 个订单级 fill-set attestations 保留为 shadow provenance。加入 2,453 个窗口外 latest-batch facts 后，canonical 输出仍为完整基线口径：

| Canonical 项目 | 结果 |
|---|---:|
| Canonical orders | 3,542 |
| Canonical fills | 5,400 |
| Blocking issues | 0 |
| `analysis_ready` | `true` |

正式本地账本已通过 confirm 原子追加上述记录，并生成 canonical set 1：3,542 orders / 5,400 fills、0 blockers。再次使用同一 export confirm 返回 `already_present`，沿用相同 canonical hash，所有追加计数为 0。导入前后均保留 SQLite 在线备份并通过完整性检查。

## 5. 当前分析边界

- 局部 OpenAPI 窗口只交叉验证 1,089 / 3,542 个订单；窗口外 2,453 个订单不能写成“OpenAPI 已覆盖”；
- confirm 不自动创建新的 EpisodeBuild，“仓位复盘”不会因保存 OpenAPI 证据而悄悄切换口径；
- opening-position snapshot 尚未验证，现有 Episode 继续保留 `assumed_flat_unverified`，条件性 P&L 不能进入 headline；
- StrategyEpisode 仍是待用户确认的分组问题，不能仅凭成交相似度自动推断 spread 或 roll；
- 旧 `scripts/sync_moomoo_live.py` 与 `/journal/sync-live` 继续暂停，不能绕过受控 plan/confirm 链路。

## 6. 下一步退出条件

1. 新增 canonical set → EpisodeBuild 的显式、版本化 repository 接线，保留旧 build 可回滚；
2. 对 canonical Episode 执行成交、费用、持仓和 P&L 守恒回归，不因双来源重叠而重复计数；
3. 取得并验证覆盖窗口起点的 opening-position snapshot，再决定是否解除 headline P&L 限制；
4. 为 StrategyEpisode 候选分组提供用户确认流程，并人工抽查至少 20 个复杂生命周期。

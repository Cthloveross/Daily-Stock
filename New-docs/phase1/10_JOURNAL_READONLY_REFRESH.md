# Phase 1.10 · Journal 每日只读刷新与版本发布

> 状态：服务端持有的 OpenD 只读预览、显式证据发布、canonical 构建和 append-only 激活闭环已落地；完整、终态且位于可证明增量尾部的组合期权现已支持 execution-group/leg 投影，其他不完整边界继续 fail closed。
>
> 安全边界：`LIVE` 只表示读取真实账户历史。本流程不读取交易密码，不解锁交易，也没有下单、改单或撤单接口。

## 1. 目标

CSV 仍是首次建立长历史基线的入口。日常更新不再要求反复下载完整账单，而是在网页“交易证据”页完成：

```text
已确认 CSV 基线
  -> 从最近可信水位向前重叠 7 日，只读查询 OpenD
  -> 服务端冻结 preview artifact（零业务证据写入）
  -> 用户核对范围、差异与阻断项
  -> 显式发布同一份 artifact 到 append-only 证据账本
  -> 生成新的 canonical PositionEpisode build
  -> 显式激活该 build，默认仓位复盘才切换版本
```

查询、发布、构建、激活是四个独立阶段。任一阶段失败都不会自动越过下一阶段，也不会触碰 legacy Journal。

## 2. 配置

`.env.example` 提供以下独立配置：

```dotenv
MOOMOO_OPEND_ENABLED=true
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111

MOOMOO_JOURNAL_REFRESH_ENABLED=true
MOOMOO_JOURNAL_ENV=LIVE
MOOMOO_JOURNAL_ACCOUNT_ID=
MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET=<至少 32 字符的本地随机值>
MOOMOO_JOURNAL_QUERY_TIMEOUT_SECONDS=15
MOOMOO_JOURNAL_REFRESH_TIMEOUT_SECONDS=180
```

- 默认关闭；没有配置时页面只展示状态和修复提示，刷新按钮保持禁用。
- `MOOMOO_JOURNAL_ACCOUNT_ID` 可留空。只有一个符合 `LIVE + US` 的账户时自动选择；存在多个候选时必须明确填写，不能猜账户。
- account binding secret 只用于对稳定账户 ID 做 HMAC。数据库与响应只保存不可逆 binding，不保存原始账户号；该 secret 必须在后续刷新中保持稳定。
- Journal 使用独立的 `MOOMOO_JOURNAL_ENV=LIVE`，不复用旧 writer 的 `MOOMOO_TRADE_ENV`；这不会授予任何交易动作。
- 单项同步查询和整个隔离子进程都有硬超时。OpenD 离线、SDK 阻塞或上下文无法安全关闭时，本次 preview 失败且不发布证据。

## 3. 服务端持有的 preview

网页调用：

```text
POST /api/v1/journal/v2/refreshes/preview
```

服务端自行计算查询窗口、运行可终止的只读探测、解析结果并生成 import plan。原始逐笔 payload 不回传浏览器，而是保存为短期 artifact；响应只包含：

- artifact ID、不可变 key 与过期时间；
- `retrieval_complete`、`coverage_complete` 和 `has_activity`；
- 券商查询完整水位与最新成交时间；
- overlap 匹配、窗口内冲突、新增尾部订单及 canonical 影响；
- 是否允许发布、阻断项和 warnings；
- `evidence_written=false`、`trading_action_performed=false`。

“查询完整水位”和“最新成交时间”必须分开：某日没有成交时，完整查询仍会推进水位；最新成交时间不会被伪造为当天。

每日刷新固定截止到**最近一个已经完整收盘的 XNYS session close**，而不是点击按钮时的墙钟时间。交易日盘前和盘中只查询上一完整交易日；正常收盘后才可包含当天，半日市使用交易所日历给出的真实提前收盘时间，周末和节假日回退到上一 session。当前盘中、盘后和夜盘不会混入正式日终批次，因此尚未稳定的当日费用不会阻断昨天本已完整的证据。交易所日历无法解析时 preview fail closed，不用工作日或固定 `16:00` 猜测证据截止。

artifact 默认 30 分钟过期。确认只能引用服务端保存的 artifact ID 与 preview key；服务端会重新解析、重新计划并核对 key，浏览器不能提交替换后的逐笔 payload。

## 4. 连续性与新增成交规则

每次窗口以“最新 accepted CSV 的覆盖终点”和“最近已发布 OpenD 查询水位”中较新者为锚点，再向前重叠 7 日，并以最近已完成的 XNYS session close 为右边界。窗口最长 366 天；缺口更大时要求先导入更新的 CSV 基线。

保存 preview 和发布回执时都重新验证：

- 账户 binding 必须与上一笔已确认刷新一致；
- 查询窗口必须覆盖最新可信锚点，不能留下静默时间缺口；
- `LIVE + US`、完整 retrieval/coverage、严格 schema 与证据 hash 必须成立；
- overlap 内 API-only、CSV-only 或身份歧义继续阻断；
- 严格晚于旧证据 cutoff、具有 broker 稳定 ID 的 API-only 订单属于合法 incremental tail，不再误报为历史对账冲突；
- 每个实际产生成交证据的期权合约都通过 Moomoo market snapshot 冻结 `lot_size / option_contract_size / option_contract_multiplier`；读取不到时 planner fail closed，不能仅凭 OCC 外形假定为 100。未成交失败单不会因为没有执行乘数而阻断 Episode，但也不会进入现金流。

Moomoo 的 [order `create_time/updated_time`](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-list.html) 与 [deal `create_time`](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-fill-list.html) 来自两个独立记录。真实只读样本中 1,649 笔成交仅有 1 笔出现 0.374 秒反序。parser 不改写原始时间；只有 stable ID 精确关联、成交匹配父单或声明组合腿、状态为 `OK`、父单有成交且在成交后 1 秒内更新时才接受，并追加 `broker_fill_precedes_order_create_within_1s=N` 审计 warning。超过 1 秒或任一身份/状态条件不符仍硬拒绝。

历史订单的 `strategy_type` 与 `combo_legs` 会以去标识白名单保留。[Moomoo 组合单合同](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-combo-order.html)定义父单 `qty` 为组合包数量，而逐笔成交是各腿数量，父单代码、方向和净价也不能按普通单腿规则核对。非空 OpenD 订单响应必须证明这两个能力字段同时存在；SDK 对没有 `strategyType` protobuf 字段的普通订单返回 `N/A`，仅在独立的 `combo_legs` 同时明确为空时规范为 `NONE`。旧 v1 文件仍可读取，但未证明字段能力时不能发布。

组合投影只放行同时满足以下条件的事实：父单是完整终态、每笔 fill 精确落到声明的 `(code, side)` 腿、各腿成交量满足 `proved_group_qty × qty_ratio`、所有腿都有 broker-stated multiplier、整组费用完整，并且整组严格位于 CSV baseline 之后的 incremental tail。父单不写成普通单标的；父单净价只保留作审计，不参与腿 VWAP 或现金流。原始 group、leg、fill-link 和 group-fee 分别写入 append-only companion 表，并在 canonical set 中冻结相同关系与 provenance。

早于 CSV 基线、落在 CSV overlap、字段能力未知、部分成交、腿不平衡、缺费用或缺合约规格的组合继续原子阻断，不能用同秒或同标的启发式合并。整组费用只在 execution-group scope 精确保留；系统不会猜测腿级分摊，因此受影响腿的 `total_fee / realized_pnl_net` 保持空值并从严格 Headline 排除。

## 5. 显式发布与水位

网页确认调用：

```text
POST /api/v1/journal/v2/refreshes/{artifact_id}/confirm
```

确认会原子追加 OpenAPI batch、identity links、attestations 与 canonical set；成功后再追加 refresh publication receipt。重复确认同一 artifact 幂等返回同一发布回执。若进程在证据事务成功后、回执落库前崩溃，重试只补回执，不重复经济事实。

状态接口：

```text
GET /api/v1/journal/v2/refresh-status
```

它分开展示四类水位：

| 水位 | 含义 |
|---|---|
| `broker_queried_through` | OpenD 已完整查询到何时；即使零成交也可推进 |
| `latest_fill_at` | 最近实际成交，仅表示活动时间 |
| `evidence_published_through` | 已写入可信账本的来源 cutoff |
| `active_source_through` | 当前默认复盘 build 实际覆盖到何时 |

状态机为：`never_synced -> stale/evidence_blocked -> evidence_current -> build_ready -> current`；`pending_stage` 明确下一步是 `refresh / confirm / build / activate / none`。过期数据不能只因接口成功而显示“当前”。

## 6. 构建与激活

证据发布不会自动重建或切换复盘。先使用 canonical preview/confirm 生成不可变 build，再调用：

```text
GET  /api/v1/journal/v2/episode-builds/activation
POST /api/v1/journal/v2/episode-builds/{build_id}/activate
```

激活请求必须携带预期 build key、当前 activation ID、当前 build ID，并在使用 `assumed_flat_unverified` 时再次明确接受该边界。若 build 含未分摊的 execution-group fee，生成与激活还必须分别提交 `accept_group_fee_scope=true`，确认使用者理解受影响腿没有费用/净收益且不会进入 Headline。服务端以 compare-and-swap 拒绝 stale 页面并把每次切换追加成不可变事件；重复请求幂等，A → B → A 回退也保留完整历史。

只有激活成功后，省略 `build_id` 的 PositionEpisode 列表与详情才切换到新 build。激活只改变本地复盘读取指针，不改变券商持仓，不执行交易。

## 7. 当前限制与下一步

- 当前 preview 是一个有 180 秒墙钟上限的同步 HTTP 请求；它不会无限挂住，但尚未升级为可跨进程恢复的后台任务。
- 失败状态目前随响应返回，尚未持久化最近一次 OpenD 错误；刷新页面后只能看到最后成功/已保存水位。
- artifact 会过期并拒绝确认，但尚未实现自动清理任务。
- 组合期权当前只支持可证明的完整 incremental tail；CSV baseline 前或 overlap 内没有同一组合父标识时继续不可证明，不能用同秒启发式自动合并。
- Moomoo 只提供整组费用时，系统不会臆测腿级分摊。相关腿可用于成交路径复盘，但腿级 Net 与净收益排行保持不可用；未来若券商提供可靠的腿级费用事实，再以新投影版本追加支持。
- 新出现且无法从冻结 market snapshot 或既有金额证据证明 multiplier 的已成交期权仍会 fail closed；行情权限缺失不会触发“默认 100”降级。
- 当前期权持仓的只读双采样、preview/confirm 与 append-only 存储已经落地，但它只证明采集区间内的当前状态，不能倒推现有历史窗口期初；成交连续性 fence 与未来 Episode boundary 尚未接通，因此激活不会把旧 `assumed_flat_unverified` 条件性 P&L 升级为 headline 收益。详见 [`11_CURRENT_POSITION_SNAPSHOTS.md`](./11_CURRENT_POSITION_SNAPSHOTS.md)。
- 仍需在真实 OpenD 在线、上一完整交易日零成交、上一完整交易日有新增合约、OpenD 离线和多账户五种情形下持续验收；盘中成交只在对应 session 完整收盘后的后续刷新中进入候选证据。

2026-07-30 的真实只读验收在生产库副本上完成：759 orders / 1,649 fills / 740 fees、255 份已成交期权 contract specs、1 execution group / 2 legs / 4 fill links / 1 group fee；zero-write plan 为 `incremental_tail`、0 blocking issues。临时副本确认后生成 1,615 个 PositionEpisode，USD 普通费用 174,531.05 与组费 8.08 逐币种守恒；2 个受影响腿回合的 Fee/Net 均为空并以 `group_fee_unallocated` 从 Headline 排除。正式数据库未发布、未构建、未激活。

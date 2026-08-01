# Phase 1.7 · Canonical Premarket Research Cycle v1 Foundation

> 状态：**v1 foundation 与 server-owned orchestration 已接线；2026-07-24 已完成真实 `09:12–09:20 ET` 首轮自动触发、失败回滚、恢复发布与幂等验收。完整 draft/finalizer cycle 尚未完成，辅助 Regime 数据权限与多层 eligibility 仍需继续完善。**
>
> 当前实现：纯 XNYS 窗口解析、服务端版本化研究池、持久化 attempt/stage/lease、09:12/09:17 ET 低噪声 scheduler、服务端 run/status、Regime 因果与核心质量门禁、上一完整 XNYS 日线 freshness、独立 freeze policy/scope、不可变基础榜快照。
>
> 明确未实现：append-only draft/finalizer、服务端 last-good、pool 生效交易日、外部进程监管、期权增强冻结，以及 snapshot publication 与 lease terminal 的单事务提交。
>
> 服务端编排的当前合同、API、限制与回滚见 [`08_SERVER_OWNED_PREMARKET_ORCHESTRATION.md`](08_SERVER_OWNED_PREMARKET_ORCHESTRATION.md)。
>
> 安全边界：行情研究永久只读；不解锁、不下单、不改单、不撤单。

## 1. 为什么需要独立的 canonical cycle

旧机会快照只验证“上一交易日已经收盘且下一交易日尚未开盘”。这个因果窗口足以阻止盘后补造样本，却不足以决定**当天哪一次盘前研究是正式版本**。页面在纽约凌晨打开时也可能先写入当日 slot；后续即使 Regime 或行情证据更完整，首份不可变快照也不会被替换。

本地审计曾观察到同一 XNYS 市场日的机会快照在 `00:52 ET` 写入，而同日 Regime 在 `01:23 ET` 才生成。这个例子不代表数据被伪造，但证明“合法 pre-open”不等于“合适的正式研究时点”。

新 foundation 因此使用新的：

- `cycle_version=canonical_premarket_cycle_v1`
- `freeze_policy_version=canonical_premarket_xnys_v2`
- `scope_key=canonical_premarket_research`

旧 `premarket_prior_close_xnys_v1 / web_daily_opportunity` 快照继续保留作历史审计和既有 5D/20D 结果跟踪；它们不会被新 status/run 当成 canonical v1 快照，也不会被迁移、覆盖或删除。

## 2. XNYS 市场日与窗口合同

Foundation 使用 `exchange-calendars` 的 `XNYS` session/open，不使用服务器本地日期、普通工作日或固定 UTC 时刻推断交易日。

对于纽约日期 `E`：

- `E` 必须是 XNYS session；
- `S = previous_session(E)` 是唯一允许进入基础榜的完整日线参考 session；
- canonical 窗口是 `[open(E)-45m, open(E)-10m)`；
- 常规开盘日对应 `08:45:00–09:19:59 ET`；
- 新 provider 工作的最晚启动点 `latest_start_at=open(E)-12m`，常规开盘日为 `09:18 ET`；达到该时刻后返回 `window_closed / latest_start_passed`，为发布保留最后两分钟；
- `open(E)-10m`（常规开盘日 `09:20 ET`）是写入硬截止，完成过晚的研究不发布；
- 窗口按 XNYS open 计算，自动跟随美东夏令时；
- 周末、节假日或 XNYS 非 session 返回 `non_session`，不写快照；
- 早于窗口返回 `waiting_window`；尚无已发布/运行结果时，达到 latest start 后即返回 `window_closed`，硬截止后同样不允许事后补写。

`market_date_et` 是研究面向的 XNYS session，`previous_session` 是基础日线事实日期。两者不能合并成一个含糊的“今天数据”标签。

## 3. 当前 API

| 方法 | 路由 | 当前语义 |
|---|---|---|
| `GET` | `/api/v1/opportunities/premarket/universe` | 读取服务端正式研究池；未配置时 `STOCK_LIST` 只能作为未启用建议 |
| `PUT` | `/api/v1/opportunities/premarket/universe` | 显式追加一版有序、不可变的服务端研究池 |
| `POST` | `/api/v1/opportunities/premarket/status` | 只读解析 XNYS 日期、调度边界、持久化 attempt/stage/lease 与已保存快照；不启动 provider |
| `POST` | `/api/v1/opportunities/premarket/run` | 只有显式 `manual=true` 且服务端 due/lease 门禁通过时才运行；否则等价于 status |

运行与状态请求继续使用 `PremarketCycleRequest`；其中浏览器 `symbols` 不再是官方 universe 真源：

- 正式 symbols/limit 始终来自已显式保存的服务端 pool；
- `manual`：默认 `false`；只有明确人工操作才设为 `true`；
- 没有 pool 时返回 `research_pool_missing`，且零 provider work。

响应显式返回 `window_start_at / latest_start_at / window_end_at`，客户端不自行用本地时区猜测三个边界。

当前 `cycle_key` 只由市场日与 cycle/freeze policy/scope 决定，形成“每个 XNYS 市场日一个官方 slot”。第一次持久化 attempt 会锁定 pool version、保序 universe 与 limit；同日 pool 后续变化不影响该 cycle。唯一持久化 cycle slot 配合 SQLite `BEGIN IMMEDIATE` 或非 SQLite 行锁，让多个 worker 中只有一个写 owner；状态从数据库事件重建，不再依赖浏览器或进程内 `_cycle_statuses`。墙钟时间只用于审计，当前 pool/attempt 由数据库追加顺序判定。

基础日线扫描另有 30 秒 process-local lease 和 follower wait；迟到 generation 不能把结果写回已经被替换的 cache flight。Canonical 并发调用不会在请求内等待 provider leader，而是立即返回持久化 `running` 状态，客户端只做有界的只读 status 轮询。这里的 bounded flight 只限制单进程并发与等待，不等于安全取消 provider leader；跨进程所有权与崩溃恢复由持久化 cycle slot、attempt 和 lease 决定。

## 4. Session 状态、质量与阶段

### 4.1 顶层 `state`

| 状态 | 含义 |
|---|---|
| `non_session` | 当前纽约日期不是 XNYS session |
| `waiting_window` | 是 XNYS session，但尚未到 `open-45m` |
| `ready_to_run` | 已进入窗口，尚未启动当日官方 slot |
| `running` | 相同 key 的服务端 leader 正在运行 |
| `published` | 新 scope 的不可变快照已存在或本次成功写入 |
| `blocked` | Regime 核心门禁或 T-1 基础证据门禁未通过，没有写入 |
| `window_closed` | 已到 `open-12m` 最晚启动点、已到 `open-10m` 发布硬截止，或研究完成时越过硬截止，禁止启动/补写 |
| `failed` | 当前阶段发生被脱敏记录的运行异常，没有写入 |

### 4.2 独立 `quality`

- `ready`：Regime 六域和可用候选达到 foundation 的完整门禁；
- `degraded`：SPY/VIX 核心 Regime 可用、至少一个 T-1 候选可信，但辅助 Regime 域或部分候选不可用；
- `blocked`：Regime 核心/因果时间、市场日、T-1 reference 或全部候选基础事实不可证明；
- `unknown`：尚未运行或没有可读取质量结论。

`quality` 不是策略胜率，也不是候选的 `research_ready / watch_only / context_only / blocked`。后者继续描述单个标的研究资格。

### 4.3 当前阶段

响应中的 `stages` 当前可能包含：

1. `resolve_window`
2. `compute_regime`
3. `scan_completed_bars`
4. `quality_gate`
5. `persist_snapshot`

阶段状态保留 `pending / running / completed / degraded / blocked / failed / skipped`。错误只返回稳定 `error_code` 和异常类型，不把供应商响应、账号或原始敏感文本写入 API/日志。

Web 页面另有“基础榜 → 期权概览 → Top 5 墙”三个**展示加载阶段**。它们用于解释页面渐进加载和增强降级，不等同于上述 canonical backend stages，也不表示期权增强已经冻结。

## 5. Regime 与 T-1 门禁

### 5.1 Regime

`/premarket/run` 当前先显式计算同一 `market_date_et` 的 Regime，再固定 `cycle_as_of` 并把同一份 Regime 值传入基础扫描。门禁要求：

- Regime 日期等于 `market_date_et`；
- `generated_at` 必须是带时区时间且不晚于 `cycle_as_of`；
- SPY 与 VIX 两个核心域都为 `ready`；
- events、sectors、prev_day、premarket 任一辅助域不完整时允许 `quality=degraded`，但必须逐域保留状态。

Regime 当日存储仍可被后续重算更新；canonical 快照保存的是当次 run payload、Regime 质量、生成时间和版本化 metadata。历史解释必须读取冻结 payload，不能只按日期重新 JOIN 当前 mutable Regime 行。

### 5.2 完整日线

- 每个非 blocked 候选的 `reference_session_date` 必须严格等于 `previous_session`；
- 至少一个候选通过该 freshness 门禁才允许发布；
- 没有候选、市场日不一致、全部候选 blocked 或 reference 错位时返回 `blocked`；
- 部分标的行情不可用时可以发布 `degraded`，但不可用标的和数量必须保留。

基础榜仍只使用上一完整日线的 OHLCV/成交额、EMA8/13、20 日结构、相对量能和本次冻结 Regime。期权概览、墙、异常成交和 AI 不参与该门禁或排序。

## 6. 当前冻结与幂等边界

当前 foundation 在一次 `/premarket/run` 通过质量与窗口复检后**立即**调用不可变 snapshot repository。它会在 payload 内保存：

- cycle/freeze policy/scope 版本；
- XNYS market date、previous session、regular open、窗口边界和 latest start；
- started/cycle-as-of/published 时间；
- quality、quality reasons 与 Regime 六域状态；
- 已脱敏的 Regime manifest 及其 SHA-256，避免后续 mutable Regime 行改变历史解释；
- fresh/unavailable candidate 数量；
- universe hash、signal version 和已完成阶段。

相同新 scope slot 再次调用返回既有不可变快照并标记 `idempotent_replay=true`。新 policy/scope 使它不会命中旧 v1 快照。

发布前会先完成 benchmark 上下文预取，再在 append 前复查 `open-10m` deadline；snapshot repository 在实际 INSERT 前还会用注入时钟复查一次。`quality=degraded` 可以保存作审计，但固定 `validation_eligible=false / learning_eligible=false`。

这只是 foundation 幂等，不是最终设计中的“多次 draft → 固定 finalizer → 唯一正式版本”。当前当日第一次成功的 `/run` 会立即占用官方 slot，并把该请求实际使用的 universe/limit 固定下来；因此在服务端 Watchlist 版本与 append-only draft/finalizer 落地前，不能把它描述为完整 canonical 调度系统。

## 7. 前端已具备的可见状态

当前 `/regime` 机会研究已增加：

- 页面加载只调用只读 `/premarket/status`，不会自动调用 `/premarket/run`；
- `published` 直接使用冻结 run；非 session、窗口前/后、阻断或接口失败时才回退普通 `/daily` 只读预览；
- 手动生成必须显式点击，且仍受 09:12 primary、09:17 retry、两次 attempt 上限与 09:18 latest-start 约束；
- `running` 仅短期轮询只读 status；不设置自动 run timer，不并发普通 `/daily`；
- Watchlist 页显式核对并保存服务端正式研究池；
- 状态卡显示 scheduler、pool version、attempt trigger/时间/lease 与下一次计划；
- 相同 status/run 请求在浏览器侧复用 in-flight Promise；`running` 无 payload 或 run 超时后二次 status 仍为 `running` 时不并发启动 `/daily`；
- 用户界面称“官方盘前研究 / 官方版本 / 只读预览”，内部版本、scope 与 API 合同继续使用 canonical 命名；
- 请求市场日、候选基础证据日期/日期范围、ET 生成时间；
- “基础榜 → 期权概览 → Top 5 墙”阶段状态；
- 基础榜可用但期权增强失败时的“增强降级”；
- 执行价墙部分 coverage 的有效/请求合约数与比例；
- 刷新期间继续显示上一份前端可用结果及其生成时间；
- Regime 首次加载与机会榜解耦，避免 Regime skeleton 阻止基础榜开始请求。

这些改进解决了“页面看起来一直扫描”“部分墙冒充完整”“旧结果没有时间”的交互问题，并已把页面主流程接到 canonical status/run。旧 `snapshots/ensure` API 仍保留给旧 v1 审计/结果合同，但对应自动 effect 已删除，不再是当前机会页的默认写入路径。服务端 last-good 仍未实现；普通 `/daily` fallback 只是当次只读预览，不能冒充 canonical 快照。

## 8. 尚未实现：不得误报为完成

以下六项是完整 Canonical Premarket Research Cycle 的必要后续或已知可靠性边界，不属于本次 foundation：

### 8.1 Append-only draft 与 finalizer

- 尚无窗口内多次 draft revision；
- 尚无建议的 `08:45 / 09:00 / 09:12 / 09:18 ET` 尝试序列；
- 尚无 `09:18` 选择最新仍满足 freshness 的 draft、`09:20` 硬截止 finalizer；
- 当前成功 `/run` 会立即冻结，而不是等待统一 finalization。

### 8.2 Attempt 持久化与 restart recovery（基础版已实现）

- universe version、attempt root 与 stage/terminal events 均 append-only；
- 服务重启后可重建 running/blocked/failed、lease 与 next retry；
- 持久化 cycle slot 与数据库锁支持多 worker single owner 与 lease-expiry takeover；
- provider 无安全取消，极端情况下仍可能重复只读请求；但 snapshot、候选、完成阶段与 `published` 终态在同一事务提交，并在构造前后两次检查 deadline/lease。

### 8.3 服务端 last-good

- 尚无按 scope/version/universe 兼容性选择的持久化 last-good；
- 当前前端只在当前 tab/cache 生命周期内保留上一成功结果；
- 不能保证浏览器重开或服务重启后仍保留同样 fallback；
- 后续必须显示原始 display session、as-of、stale age 和 fallback reason，绝不能把上一交易日标成今天。

### 8.4 Local scheduler 与 server-owned Watchlist（本地服务版已实现）

- 已按 XNYS `open-18m / open-13m` 触发，idle tick 不写日志；
- 正式 pool 版本由 Watchlist 页显式保存；`STOCK_LIST` 只作未启用建议；
- FastAPI 进程必须保持运行；尚无 LaunchAgent/容器 one-shot 等外部 24 小时监管；
- pool 尚无 `effective_from_market_date`，但当日第一次 attempt 已锁定版本。

### 8.5 期权增强冻结

- option overview、ATM IV、0–45 DTE wall 和异常成交仍是异步页面增强；
- 它们没有写入 canonical payload，也没有共享一个完整 as-of；
- 当前 wall/事件缺失不影响基础榜，但也不能用于重放当日完整期权研究；
- near-spot wall 和 ATM contract shortlist 必须在独立 provenance、coverage、盘口时点与不可变快照合同完成后再接入。

### 8.6 跨进程唯一性与截止事务

- 当前“一市场日一官方 slot”由进程内 single-flight 和读取既有快照实现，数据库尚无 `market_date + scope + freeze_policy` 的唯一约束；
- 两个独立服务进程同时首写仍存在理论竞态，不能把进程内幂等描述成分布式 exactly-once；
- append 前 deadline guard 与 SQLite commit 不是相对交易所时钟的原子事务，极端边界仍需持久化 finalizer/lease 合同；
- provider leader 目前不能被安全取消；基础 `/daily` follower 等待有界，canonical 并发请求立即返回持久化 `running`，都不代表底层 provider 工作已停止。迟到 owner 即使完成只读工作，也无法绕过 slot、lease 与原子发布门禁。

## 9. 证据与学习护栏

- 缺失数据保持 `null / unknown / unavailable`，不能用 `0`、旧值、HV、ATM 单点或模型文本补齐；
- 每个观测值保留 source 与 observation/published/fetched 时间；无法证明时间时不显示伪 as-of；
- 派生结构只能引用同一 frozen run 中已有的确定性证据；
- Moomoo OI/Volume/Gamma、BUY/SELL 和 sentiment 保留供应商原义，不推断开平仓、机构意图或 Dealer GEX；
- AI 不参与 canonical 门禁、排序、hard gate 或事实写入；
- `published` 只说明已保存一版研究，不说明策略有效；
- 完整学习资格后续必须拆分为 causal-window、analysis-quality 与 official-finalization 三层；旧 `validation_eligible` 不能单独证明完整 canonical 质量；
- `quality=degraded` 的 foundation 快照在完整 cohort 合同完成前不得混入“已验证 edge”。

## 10. 验收状态与下一步

当前 Foundation 的确定性测试已覆盖：

- XNYS 普通日、周末、`[open-45m, open-10m)` 与 `open-12m` latest-start 边界；
- Regime 日期、generated-at 因果顺序、SPY/VIX 核心域和辅助域降级；
- T-1 reference session、全部 blocked、部分 unavailable；
- status 只读、窗口外零写入、同日 key 并发 join、旧 scope 隔离，以及不同 symbols/limit 请求仍回放首份官方 slot；
- leader/follower 超时后不允许迟到结果污染新的 flight。

在产品状态可从“foundation”升级前，还必须完成：

1. 补齐 DST、节假日/提前收盘边界，并在真实 XNYS 盘前窗口在线验证 provider、数据 freshness、耗时、日志量和失败恢复；
2. 落地 append-only draft/finalizer；
3. 补 pool 生效交易日、服务端 last-good 与外部进程监管；
4. 把 snapshot publication、官方 slot 与 lease fence 收拢到同一事务；
5. 冻结 near-spot option wall 后，再进入 ATM contract shortlist。

## 11. 回滚

- 新 foundation 使用独立 freeze policy/scope，停用新 `/premarket/run` 调用即可停止新增 canonical v1 foundation 快照；
- 旧机会快照、5D/20D outcome 和普通 `/daily` 预览不需要删除或改写；
- 已写入的新 scope snapshot 保持 append-only，不执行 UPDATE/DELETE；
- 回滚 Web 状态展示不改变任何快照或 Journal 事实；
- 全链路始终保持 Moomoo 只读边界。

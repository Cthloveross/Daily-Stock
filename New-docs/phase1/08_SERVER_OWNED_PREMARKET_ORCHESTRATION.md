# Phase 1.8 · Server-owned Premarket Orchestration

> 状态：**本地服务端编排 v1 与 append-only 三轨 qualification 已实现，并于 2026-07-24 真实 `09:12–09:20 ET` 窗口完成自动触发、失败回滚、自动恢复、冻结发布与幂等验收。当天辅助 Regime 数据因供应商权限降级；当前口径允许合格且前瞻的标的路径继续回填，但完整研究 track 明确排除，不能进入命中率。默认关闭的 Moomoo 盘前影子证据预取 v1 也已实现，但尚未经过真实影子窗口验收，正式 Regime、排名与产品界面均不消费其产物。**
>
> 安全边界：全链路只读取行情、Regime 与期权研究数据；不导入交易执行上下文，不解锁、不下单、不改单、不撤单。

## 1. 本切片解决什么

上一版必须依赖浏览器在盘前窗口打开页面，运行状态也只存在于当前 Python 进程。现在正式研究由服务端持有：

- 浏览器 Watchlist 与“官方盘前研究池”分离；研究池必须显式保存为不可变版本；
- `STOCK_LIST` 只作为未启用的导入建议，不会被后台静默当成正式 universe；
- 每次运行 attempt、阶段、租约和终态写入 SQLite，服务重启后仍可读取；
- 持久化 cycle slot、SQLite `BEGIN IMMEDIATE` / 非 SQLite 行锁和租约 fencing 防止多个 worker 同时成为写入 owner；
- FastAPI lifespan 内的低噪声 scheduler 按 XNYS 开盘相对时间唤醒，不复用服务器本地墙钟任务；
- Web 页面先只读 status；只有用户明确点击时才发送 `manual=true`。页面加载不会启动官方 run；若服务端确认没有运行中的官方 attempt，页面仍可读取普通 `/daily` 预览及其行情 provider。

它仍不是完整 draft/finalizer 系统。当前正式版本是一次成功即冻结，因此自动时点刻意放到靠近开盘、但仍留有恢复预算的位置。

## 2. XNYS 时序

所有时点从 `exchange-calendars` 的 XNYS `regular_open_at` 推导，自动跟随交易日、节假日与夏令时：

| 时点 | 常规交易日 | 行为 |
|---|---:|---|
| `open-45m` | 08:45 ET | canonical 因果窗口开始；只能显示 `/daily` 预览 |
| `open-18m` | 09:12 ET | 第一次官方运行到期 |
| `open-13m` | 09:17 ET | 第一次失败、阻断或租约过期时的最后恢复机会 |
| `open-12m` | 09:18 ET | 禁止 claim 新 attempt |
| `open-10m` | 09:20 ET | snapshot 写入硬截止 |

最多两个 attempt。09:12 前的手动请求不能提前占用正式 slot；人工操作也不能绕过 09:17 retry anchor、attempt budget 或 09:18 latest-start。

### 2.1 Regime `as_of` 单点冻结与未来日线防护

一次 `compute_regime` 在阶段开始时只捕获一个 timezone-aware UTC `as_of`。`compute_regime_score(..., as_of=...)` 不会在不同盘前标的之间重新读取墙钟，而是把同一时点传给 SPY 和最多五只 Watchlist 的盘前适配器，并在 snapshot 保存 `_requested_as_of`；naive datetime 会在创建 provider 前直接拒绝。Regime 完成后 coordinator 再捕获 `cycle_as_of`，用于证明 `regime.generated_at <= cycle_as_of`，并把同一 `cycle_as_of` 传给后续 T-1 候选扫描。两者是先后有序的因果边界，不能把完成后的时间倒灌进已经开始的 Regime 请求。

SPY、VIX、板块与昨日结构使用的日线在计算 close、MA 和涨跌前先过滤 provider 返回的未来行：有 `date/Date` 列或 `DatetimeIndex` 时，只保留 observation date `<= target_date`，无法解析日期的有日期列记录也不进入计算。这样即使 provider 或测试 fixture 意外混入下一交易日行，也不会污染当日 Regime。没有任何日期字段的 legacy frame 为兼容保留原行为，因为系统无法从中证明未来泄漏；它不能被引用为“已通过日期因果校验”的更强证据。

## 3. 服务端研究池

API：

| 方法 | 路由 | 语义 |
|---|---|---|
| `GET` | `/api/v1/opportunities/premarket/universe` | 读取当前正式版本；未配置时可返回 `STOCK_LIST` 建议，但 `configured=false` |
| `PUT` | `/api/v1/opportunities/premarket/universe` | 显式追加一版有序、去重、最多 20 只的美股期权研究池 |

顺序是研究池版本与候选输入语义的一部分，不会排序后再保存。相同内容紧邻重复提交幂等；切换到另一版本后再切回原内容会产生新的审计版本。Snapshot 表为稳定 identity 会把 universe 保存为 canonical 排序；发布契约因此核对同一 symbols 集合和 attempt 上的原始版本/顺序，而不能把两种持久化表示按位置直接比较。

当前限制：

- 尚无 `effective_from_market_date`；当日第一次 attempt 创建后会锁定其 universe，后续 pool 变更不影响该 cycle；
- 在第一次 attempt 之前保存的新版本会成为当前正式研究池；
- Web 只保存支持的美股期权 underlying，并明确显示被忽略或超过 20 只的项目。

## 4. 持久化 attempt 与租约

新增四张独立表：

- `opportunity_research_universe_versions`
- `opportunity_premarket_cycle_slots`
- `opportunity_premarket_cycle_attempts`
- `opportunity_premarket_cycle_events`

四表均安装 SQLite `UPDATE/DELETE` 拒绝触发器。变更只能追加新版本、cycle slot、attempt 或事件。Cycle slot 为每个 `cycle_key` 提供唯一、可锁的持久化竞争点；墙钟时间只作审计，当前版本与当前 attempt 按数据库追加顺序判定。

Attempt root 固定：

- cycle/date/policy/scope；
- universe version、保序 symbols 与 limit；
- `manual` 或 `scheduler` trigger；
- owner、开始时间、hard deadline 和恢复来源。

Event stream 记录：

- attempt start/finish；
- stage start/finish；
- lease expiry；
- allowlisted error code 与脱敏的小型 payload。

租约由每次合法阶段事件续期并封顶到 09:20。旧 owner、已被新 attempt 接管的 owner，以及过期 owner 的任何非终态写入都会被拒绝；为留下崩溃审计，仍是最新 owner 的过期 attempt 只允许追加一次非 `published` 失败终态。Provider 调用本身不能安全取消；极端情况下可能产生重复只读请求，但只有持有最新数据库租约的 owner 有资格继续发布。

正式发布时先锁定 cycle slot，再取得权威 `published_at`。Snapshot、候选、`persist_snapshot=completed` 与 `attempt_finished=published` 在同一数据库事务内提交；snapshot 构造完成后还会在写终态前再次检查 09:20 hard deadline 和 lease。若锁等待或构造过程跨过截止，整组写入回滚，不会出现“已有快照但 attempt 报失败”的分裂状态。

正式发布事务现在还会追加两张 qualification 审计表：

- `opportunity_snapshot_qualification_assessments`：保存 policy、数据库验证的 publication proof、独立 analysis quality、原因与哈希；
- `opportunity_candidate_track_assessments`：为每个候选保存标的路径、日线选股、完整研究三条 track 的 causal window、observation 与 `qualified / excluded / unverified`。

同一 snapshot/policy 只有一个不可变 assessment 槽位，每个候选必须恰好拥有全部三条 track；两表安装 `UPDATE/DELETE` 拒绝 trigger。qualification 分类或追加失败会使 snapshot、候选和 terminal published event 一起回滚。历史 snapshot 只允许通过显式 dry-run/apply 追加新 policy assessment，不会修改原快照或旧 policy 结果。

## 5. Scheduler 宿主与日志

配置：

```env
PREMARKET_RESEARCH_SCHEDULER_ENABLED=true
```

该开关在 FastAPI lifespan 启动时读取，修改后需重启 Web 服务。启用后：

- daemon thread 每 30 秒执行一次只读 tick；
- FastAPI lifespan 关闭时等待当前 tick 退出；诊断调用若使用有限等待并超时，会保留 live thread 引用并拒绝同一 host 再启动第二条线程；
- 没有正式研究池、非 XNYS session、未到时点、已发布或窗口关闭时均不启动 provider；
- tick 先读数据库状态，再由同一个 claim/run coordinator 决定是否执行；
- idle tick 不写日志；
- 只记录 scheduler 启停、一次 attempt 的终态或去敏后的异常类型；
- 多个 worker 即使各有 scheduler thread，持久化 cycle slot 与数据库锁仍只允许一个运行 owner。

这是当前本地/桌面部署的宿主，不是跨机器分布式 scheduler。服务进程关闭期间不会唤醒；未来如需 24 小时无人值守，应让外部进程管理器调用同一 one-shot tick，而不是复制业务逻辑。

## 6. API 与 Web 行为

`POST /api/v1/opportunities/premarket/status` 始终只读，返回：

- scheduler 是否启用；
- 09:12 / 09:17 / 09:18 / 09:20 ET 边界；
- 正式 pool version/source/symbols；
- latest attempt key、trigger、started/lease/completed；
- recoverable 与 next scheduled time；
- 持久化 stages、quality、error code；
- 已发布 snapshot/run（如存在）。

`POST /api/v1/opportunities/premarket/run` 只有请求显式包含 `manual=true` 才尝试运行；否则等价于 status。即使 `manual=true`，服务端仍会执行全部 due-time、pool、attempt budget、lease、Regime、T-1 freshness 和 deadline 门禁。

Web 页面：

- 加载时先读取 status，再决定显示官方版本或普通预览；
- 不再因为 `ready_to_run` 自动 POST run，也不再设置窗口开始 timer；
- `running` 时只短期轮询 status，不并发 `/daily`；
- Watchlist 页显示本地列表与正式池是否一致，并由用户点击显式保存；
- UI 显示 scheduler、研究池版本和 attempt，而不把“扫描中”作为无限 loading；
- 首屏把**发布状态、数据质量、统计入样**拆成三个正交轴。有 qualification 时第三轴显示“标的路径 X/Y、日线选股 X/Y、完整研究 X/Y”，其中 X 是该 track 的 qualified 数、Y 是该 track 的全部候选数；技术 track key 不直接露给用户。旧 API 缺字段时继续显示既有聚合 `已纳入/未纳入 N/M`，不猜测三轨通过；
- 5D/20D 结果生命周期显示“已回填 / 等待目标日 / 已到目标日但缺行情”；后端 `mature_count` 只是已到目标窗口并保存结果的计数，不再在 UI 中称为“策略成熟”，零入样时显示“尚无可统计样本”。

Snapshot 只读响应以加法方式暴露可选 `analysis_quality_eligible / analysis_quality_reasons` 和 `qualification`，既有 `validation_eligible / eligibility_reasons` 继续保留。Qualification 将 publication、analysis quality、candidate causal window、underlying observation 和 downstream track decision 分开保存；旧快照缺少新字段时必须当作“历史口径未知”并回退旧 UI，不能默认通过。

三条 track 的用途不同：标的路径只回答 underlying 后续价格能否被保存；日线选股回答官方前瞻选择事实能否研究；完整研究还要求 analysis quality ready、`research_ready`、数据完整、方向可操作与 SPY anchor。`published + degraded` 因此可以同时出现“标的路径 qualified、完整研究 excluded”。`xnys-close-qualified-raw-path-v2` 可为前者追加到期路径，但学习摘要只接受完整研究 track 同时 `qualified + prospective` 的 outcome，不能用 raw path 数量填充命中率。

## 7. 仍未完成的可靠性边界

- 尚无窗口内多 draft 与 09:18 finalizer；不能启用 08:45/09:00 多次正式运行；
- 尚无 pool 生效交易日、服务端 last-good 或外部 LaunchAgent/容器级 one-shot 监管；
- 期权概览、墙和异常成交仍是页面增强，没有冻结到官方 snapshot；
- 2026-07-24 的真实窗口已经验证本地 LaunchAgent 持续运行、scheduler 唤醒、T-1 Moomoo 日线、两次 attempt、事务回滚、恢复发布与截止后幂等；尚未覆盖机器睡眠后唤醒、进程在整个窗口离线、跨机器部署或长期日志增长；
- 当天 Finnhub economic calendar 与 Alpaca SPY 盘前分钟线均返回 `403`，因此 `events/premarket` 为 degraded。当前多轨口径不会丢弃可验证的 underlying 路径，但会令完整研究 track fail closed；raw-path outcome 可用于审计“后来怎么走”，不能为了显示命中率而跨 track 放宽。

### 7.1 Moomoo 盘前影子证据预取 v1

`MoomooFetcher.get_premarket` 已能只读取得 `extended_time=True`、`AuType.NONE` 的纽约 `04:00–09:30` 已完成 1 分钟 bar，并核对精确上一 XNYS session 的 `last_close`。由于 2026-07-24 本机 SPY 冷调用约 32 秒，同步 SDK 调用不能进入正式 Regime 的短工作预算；当前实现因此是与 canonical run 完全隔离的**影子 producer**，不是正式 fallback。

该 producer 默认关闭：

```env
MOOMOO_PREMARKET_PREFETCH_ENABLED=false
```

只有显式改为 `true` 并重启 FastAPI 服务，lifespan 才会启动低噪声影子 scheduler。这个开关只控制审计证据生产，不会改变正式 Regime、Top 5 排名或页面数据源。

#### 7.1.1 XNYS 影子窗口

全部边界从当日 XNYS `regular_open_at` 推导，不读取服务器本地固定墙钟：

| 边界 | 常规交易日 | 语义 |
|---|---:|---|
| `open-22m` | 09:08 ET | `target_as_of`；影子预取到期并可 claim |
| `open-21m` | 09:09 ET | latest-start 边界；到达后不再启动新的子进程 |
| `open-19m` | 09:11 ET | hard deadline；父进程必须终止并回收未完成子进程 |
| `target+5m` | 09:13 ET | artifact `expires_at`；即使未来接入 consumer 也不能越过该时效 |

每次请求的 universe 是**显式持久化研究池的有序 Top 5，再强制加入 SPY 基准**；没有正式研究池时保持 idle，不会静默采用 `STOCK_LIST` 建议。市场日、精确上一 session、研究池版本和 symbols 都进入持久化 identity，不能跨交易日或跨 universe 复用。

#### 7.1.2 可终止进程与数据库所有权

- 父进程先 claim `regime_premarket_prefetch_runs` 的数据库 lease，并在等待期间 heartbeat；多 worker 由数据库决定唯一 owner。
- Moomoo SDK 与 QuoteContext 只存在于 `spawn` 子进程。子进程不连接数据库，只经 `duplex=False` 的单向 Pipe 把逐标的 observation 和完成信号送回父进程。
- 达到 hard deadline、scheduler 停止或子进程不退出时，父进程先 `terminate()`、短暂 `join()`，仍存活再 `kill()` 并回收，避免共享线程中留下不可取消的 SDK 调用。
- 只有仍持有当前 attempt lease 的父进程可以落库；旧 owner 或失去 lease 的结果不能写 artifact。

#### 7.1.3 三类持久化记录、可信入库回执与失败语义

影子预取使用三张职责不同的表：

- `regime_premarket_prefetch_runs` 是**可变协调状态**，保存 deterministic slot、attempt、owner、lease、终态和去敏错误码；
- `regime_premarket_artifact_bundles` 是**append-only 证据包**，保存 schema/policy/adapter、market date、previous session、universe、target/fetch/expiry、coverage、quality、canonical payload 与哈希，并安装 `UPDATE/DELETE` 拒绝 trigger；
- `regime_premarket_artifact_ingestions` 是与 bundle 一对一的**append-only 数据库入库回执**。SQLite 先原子提交 bundle 与匹配的 `succeeded` run，再在第二个事务中使用数据库表达式 `STRFTIME('%Y-%m-%d %H:%M:%f', 'NOW')` 生成毫秒级 UTC `ingested_at`；因此该时间确定不早于 artifact 首次提交，而不是 artifact 事务内尚未 commit 的时间。应用调用方不能传入或回填这个时间，回执记录本身也拒绝 `UPDATE/DELETE`。

两阶段之间若进程崩溃，artifact 与成功 run 可以保留，但由于没有回执，正式 selector 完全看不见；初始化或相同请求重放只会在确认匹配的成功 run 已提交后，以**恢复执行当下的数据库时间**补回执。若该恢复回执已经越过 hard deadline，回执仍作为审计事实保留，但协调 run 会改成 `failed`、解除 bundle 关联并记录 `artifact_receipt_causal_order_invalid`，避免长期出现“状态成功、正式结果不可用”的矛盾。SQLite 升级时已有 bundle 若缺回执，也遵守同一规则，绝不复制历史 `created_at`、`fetch_started_at` 或 `fetched_at` 来伪装它在过去已经可见。该迁移是保守的：旧数据因此只能证明“不晚于迁移/恢复后存在”，不能恢复其原始提交时点。当前可信回执的正式发布与选择只支持 SQLite；其他数据库方言不猜测时间、精度或会话时区并直接 fail closed。

正式 selector 通过回执表内连接取得可信时钟，除既有日期、session、policy、universe、hash、quality 与 observation freshness 门禁外，还要求 `ingested_at + 1ms <= consumer as_of`，并重新验证 `fetched_at` 与 `claimed_at` 均不晚于回执容差上界、该上界不晚于 hard deadline。调用方提供的 `created_at` 只保留审计用途，不再作为“当时已经入库”的因果证明。相同 bundle 的幂等重放沿用原回执，不生成更早或更新的可用时点。

Schema 初始化仍负责建表、安装不可变 trigger 与 SQLite legacy 回填；初始化成功后，普通读路径只执行轻量、只读的 schema-readiness 检查，不为每次 selector/status 读取再次申请 `BEGIN IMMEDIATE` writer lock。这样既保留启动/迁移时的完整校验，也避免高频只读查询与影子 producer 争夺写锁。

子进程若按协议正常结束，父进程会为请求全集构造一份 coherent bundle：全覆盖是 `ready`，部分标的缺证据是 `partial`，全部缺证据是 `unavailable`。后两种不是伪造成功，而是把每个缺口及其 allowlisted reason 固化为可审计事实；bundle 与 run 的 `succeeded` 终态原子提交，正式可消费性则必须再等 after-commit 回执成功提交。

入库前会严格重放 artifact schema，并将 symbols、observations 和可容差的小数表示规范化后再计算 payload hash；语义相同但输入顺序不同的重试因此落到同一 bundle，而不是制造冲突。Quality 与 coverage 由 observation 重新推导并和 run 的日期、session、target、universe 逐项核对；`evidence_as_of` 晚于 `fetched_at`、非五分钟 policy、非有限 JSON 数值或不可复现涨跌幅都会拒绝。

进程超时、崩溃、协议错误、被停止或完成时已经越过 hard deadline 时，**只更新 run 的 `timed_out` / `failed` 终态，不写 artifact**。系统不会把未完成 Pipe 内容拼成半包，也不会把操作故障伪装成市场数据 unavailable。

#### 7.1.4 严格影子隔离与启用门槛

当前正式 `compute_regime`、T-1 候选扫描、Top 5 排名、Snapshot qualification、Web UI 和对外 API **均不查询或消费**这三张影子表；正式链路仍按原 provider/fallback 语义运行。仓库虽然提供 fail-closed artifact selector，用于校验数据库入库回执、日期、session、policy、universe、hash、因果时点、允许的 quality，以及每条实际 observation 相对 consumer `as_of` 的五分钟 freshness，但尚未把 selector 注入任何正式 consumer。

截至本文更新，确定性测试已经覆盖默认关闭零启动、DST/early-close/非交易日、调度边界、lease/recovery、单向子进程协议、terminate/kill、ready/partial/unavailable bundle、timeout/crash 只落 run、bundle/回执 append-only、数据库时钟因果 selector、legacy 迁移与多 scheduler teardown 隔离；真实 macOS spawn 的成功、超时和取消路径也确认子进程已回收。但**尚未在真实 `09:08–09:11 ET` 影子窗口完成端到端验收**，正式 consumer 也仍未接入 selector。在验证真实 Moomoo 延迟、完整分钟边界、进程回收、重启恢复、coverage、长期日志，以及正式 consumer 的 fail-closed 降级行为之前，不得开启正式消费，也不得在 UI 或分析结论中把该影子 artifact 表述为已补齐的 Moomoo 盘前证据。

可信数据库入库时钟已经补齐“事后写入不能伪装成当时已存在”的门禁，但它不等于影子链路已经可用于正式决策。下一接入阶段仍须在真实窗口验收后，以显式 feature gate 将 selector 注入单一 consumer，并证明缺回执、回执晚于 consumer `as_of`、过期 observation、质量不足或读取异常都会继续沿既有 degraded/fail-closed 路径运行。

这些限制必须继续在 UI 和产品文档中可见，不能把 v1 描述为完整自动交易或已验证策略。

## 8. 验证与回滚

确定性测试覆盖 pool 幂等/版本、墙钟回拨、append-only trigger、跨连接单 owner claim、两次 attempt 上限、租约过期接管、旧 owner 拒绝、重启状态重建、09:12 前零 provider、缺 pool 零 provider、原子发布、qualification 同事务回滚/幂等/冲突、三轨分类与显式历史回填、09:19:59→09:20 跨界整包回滚、API manual gate、scheduler idle/due/retry 与 FastAPI lifespan start/stop。

### 8.1 2026-07-24 真实窗口验收

- `09:12:18 ET` scheduler 自动创建第一次 attempt。Regime 为 degraded、T-1 日线扫描完成；发布时暴露了真实研究池保序表示与 Snapshot canonical 排序的顺序敏感比较缺陷。
- 第一次 attempt 以 `persist_snapshot_failed` 结束；事务内曾构造的 5 个候选和 snapshot 全部回滚，数据库中没有半成品。
- 修正为 canonical symbols 集合比较并重启宿主后，`09:17:01 ET` scheduler 自动创建第二次 attempt，并在 `09:17:13 ET` 成功发布同一市场日的不可变快照。
- 截至 `09:20:39 ET`，数据库保持 1 个 cycle slot、2 个 attempts、26 个 append-only events、1 份 snapshot、5 个 candidates；重复读取返回相同 attempt/snapshot/frozen time 且 `idempotent_replay=true`，没有第三次 attempt 或重复快照。
- Top 5 为 GOOGL、TSLA、AMD、AMZN、NFLX；基础行情全部来自 Moomoo、reference date 均为 `2026-07-23`，没有 T-1 锚点或冻结时间错位。
- Snapshot `quality=degraded`、旧聚合 `eligible=0/5` 是辅助域权限降级后的结果，不是 scheduler 或发布失败。三轨 qualification 会独立判断每个候选：满足 observation 与 prospective 因果门禁的标的路径仍可回填，完整研究因 analysis quality degraded 而 excluded；两者都不证明策略有效。

回滚：

1. 设 `PREMARKET_RESEARCH_SCHEDULER_ENABLED=false` 并重启服务；
2. 如曾启用影子 producer，同时设 `MOOMOO_PREMARKET_PREFETCH_ENABLED=false` 并重启服务；
3. Web 仍可读取历史 pool、attempt 和 snapshot；
4. 普通 `/daily` 预览、Journal 和既有 5D/20D outcome 不受影响；
5. append-only 行不删除、不覆盖。

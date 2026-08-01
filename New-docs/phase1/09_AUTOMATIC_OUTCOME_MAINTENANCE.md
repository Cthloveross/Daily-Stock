# Phase 1.9 · 5D/20D 结果自动回填与维护

> 状态：**本地服务端自动维护 v2 已实现；policy 为 `xnys-close-qualified-raw-path-v2`，默认关闭，需显式启用并重启服务。**
>
> 安全边界：本流程只读取历史行情并追加冻结候选的标的价格路径；不解锁、不下单、不改单、不撤单，不写 Journal 成交事实，也不自动修改候选排序或策略权重。

## 1. 本切片解决什么

旧的 5D/20D 结果只能由页面对单个快照手动调用评估接口。页面没有打开时，已经到达目标 XNYS 收盘的候选不会自动回填；直接每天遍历并抓取所有历史行情，又会在结果尚未到期时制造无效请求和重复日志。

现在结果维护由 FastAPI 服务端持有：

- 以最近已完成的 XNYS 交易日为键，领取一个按维护策略版本隔离的 slot；
- 精确按 XNYS 实际收盘时间调度，提前收盘日不使用固定墙钟；
- 自动路径先用本地交易日历证明存在已到期或 `partial` 的 5D/20D 缺口，未到期时不请求行情；
- 同一次维护中的相同 ticker 与 SPY 历史只读取一次，再复用于多个快照；
- 多 worker 通过数据库 slot、锁和有界租约竞争单一 owner；
- 结果证据继续 append-only，调度租约则使用独立的可变运行状态；
- 维护对象按候选选择 qualification 中“标的路径 `qualified` 且 causal window 为 `prospective`”的集合，不再用整批 `validation_eligible` 同时决定所有下游用途；
- 结果面板只读展示自动维护开关、最近 slot、尝试次数、新增结果与数据缺口。

本流程负责“让已经到期的证据被记录”，不负责证明策略有效，也不是自动交易系统。

## 2. 证据与运行状态的边界

### 2.1 不可变研究证据

以下事实分别保存在不可变机会、qualification 与 outcome 表中：

- 冻结研究版本与候选；
- snapshot qualification assessment，以及逐候选三轨的 causal/observation/qualification 决策；
- 5D/20D 标的路径；
- SPY 对照、输入 bar 哈希、来源和评估时间；
- `complete` 或可后续补全的 `partial` 结果。

自动任务调用的仍是既有 append-only outcome repository。完全相同的重试幂等；已有 `complete` 结果不会被覆盖；自动维护不能修改冻结快照、候选、历史结果或 Journal。

### 2.2 可变运行协调状态

`opportunity_outcome_maintenance_runs` 只保存调度协调信息，不是市场证据。每行对应：

```text
session_date_et + policy_version=xnys-close-qualified-raw-path-v2
```

该表可以更新 `pending / running / completed / degraded / failed`、owner、45 分钟租约、尝试次数、下次重试时间、脱敏错误类型和结果摘要。可变是为了让崩溃 worker 的租约能够恢复，不代表市场事实被修订。

数据库约束保证：

- 同一 XNYS session 与 policy 只有一个 slot；
- SQLite 使用 `BEGIN IMMEDIATE`，其他数据库使用行锁；
- 同一时刻只有一个 live owner；
- 过期租约可由新 owner 接管；
- 旧 owner、错误 owner 或租约已过期的 owner 不能提交终态；
- `attempt_count` 最大为 2；
- `completed` 为该 slot 的终态。

运行结果同时保存 canonical JSON 与 SHA-256，供状态审计使用；它仍只是运行摘要，不进入命中率样本。

### 2.3 Qualification 是输入门禁，不是维护结果

Maintenance 不重新分类、补写或修改 qualification。它只读取当前 policy 已保存的三轨 assessment：

- 自动和单快照评估选择 `raw_underlying_path_v1` 为 `qualified` 且 `causal_window_state=prospective` 的候选；
- retrospective 的 raw path 在 `opportunity_qualification_v2` 中直接 `excluded`，不进入自动回填，避免把已看到下一交易日信息的冻结版本伪装成前瞻样本；
- `excluded / unverified` 不请求 provider，也不会被 0、旧值或猜测补成路径；
- 没有 qualification 的旧快照保留 legacy candidate eligibility 兼容读取，但不会因此生成伪造的三轨 assessment。

`analysis_quality=degraded` 不会自动抹掉 underlying observation。若 raw-path track 仍是 qualified prospective，结果可以 append-only 回填；它是否能进入“完整研究命中率”由另一条 `canonical_full_research_v1` track 独立决定。

## 3. XNYS 调度合同

Scheduler 每 60 秒进行一次低成本 tick，时点均由 `exchange-calendars` 的 XNYS session close 推导：

| 边界 | 行为 |
|---|---|
| XNYS 实际收盘前 | `waiting_settlement`，不创建 provider 工作 |
| `close + 30m` | 当日 slot 的第一次自动检查 |
| `close + 4h30m` | 第一次为 `degraded / failed` 时的唯一计划重试 |
| `08:45–09:30 ET` | 保护正式盘前研究窗口，结果维护不运行 |

提前收盘日同样从该日真实 close 加 30 分钟和 4.5 小时，不使用固定的 `16:00 ET`。

如果第一次尝试开始时已经晚于计划重试时点，其失败或降级后的 `next_retry_at` 使用：

```text
max(close + 4h30m, 当前时间 + 5m)
```

因此不会在一次失败后立即紧密重试。每个日 slot 最多两次；第二次仍有缺口时不再为该 slot 安排第三次。后续 XNYS 交易日会形成新的维护 slot，并可再次发现仍然到期的缺口；操作人员也可显式使用单快照手动接口。

### 3.1 启动与周末接续

服务启动后第一次 tick 会从当前纽约日期向前回看 14 天，寻找“最近一个已经到达 `close + 30m` 的 XNYS session”：

- 当天收盘后启动，会接续当天 slot；
- 周末启动，会接续最近的周五 slot；
- 节假日不会被当成交易 session；
- 服务离线期间错过多个墙钟时点时，最近 slot 的评估仍会扫描所有当前已到期快照，而不是只处理该交易日生成的快照。

这是一种有界 catch-up，不是无限历史 cron 回放。一次自动维护最多读取最近 500 个冻结快照。

### 3.2 与盘前正式研究隔离

`08:45–09:30 ET` 内 scheduler 直接返回 `premarket_protected`。该保护范围覆盖 09:12 首次正式盘前研究、09:17 恢复、09:18 最晚启动和 09:20 发布截止，并额外保留到 09:30，避免 outcome 历史请求与正式候选研究争抢 provider。

结果维护与 `PREMARKET_RESEARCH_SCHEDULER_ENABLED` 是两个独立开关、两个独立 daemon 和两套数据库 slot；启用其中一个不会隐式启用另一个。

## 4. 自动评估流程

一次取得租约的自动维护按以下顺序执行：

1. 最多列出 500 个冻结快照，逐候选选择标的路径 track 为 `qualified + prospective` 的集合；不再因为 snapshot 的旧聚合 `validation_eligible=false` 就跳过整批；
2. 仅对该集合使用冻结日期、已有结果和本地 XNYS 日历计算 `underlying_path_progress` 的 5D/20D 进度；
3. 只有出现“目标日已到但缺结果”的 `data_gap_count`，或已有 `partial` 需要补全时，才加入 due 集合；
4. due 集合为空时以 `completed` 结束，provider 调用数为 0；
5. due 集合非空时，为最早参考日到当前时间建立一次共享历史读取范围；
6. 同一 ticker 或 SPY 在本次维护中第一次请求后写入请求级 cache，后续快照复用相同结果或相同失败；
7. 每个快照独立评估；一只股票或一个快照失败不会阻断其他快照；
8. 只为 qualified prospective raw-path 候选追加已经达到目标 XNYS 收盘且满足结果合同的 `complete / partial`；`pending / data_gap / ineligible` 不伪造结果；
9. 任一快照失败或仍有到期数据缺口时，slot 记为 `degraded`；未捕获异常记为 `failed`；其余记为 `completed`。

第 2 步是自动路径的关键门禁：5D 已回填而 20D 尚未到期时，后者不会让 scheduler 每天重复下载历史数据。Degraded canonical 若 raw path 合格，可以在这里被评估；这不会改变其完整研究 track 的 excluded 状态。

## 5. 配置、API 与页面状态

### 5.1 启用

`.env.example` 默认保持关闭：

```env
OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED=false
```

本地需要自动维护时改为：

```env
OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED=true
```

配置在 FastAPI lifespan 启动时读取，修改后必须重启 Web 服务。关闭开关不会删除已保存快照、结果或维护状态。

### 5.2 只读状态

`GET /api/v1/opportunities/learning-summary` 返回：

- `automatic_maintenance_enabled`
- `maintenance_policy_version`
- `latest_maintenance.session_date_et`
- `state / attempt_count / completed_at / next_retry_at`
- `due_snapshot_count / inserted_outcomes / data_gap_horizons`
- `last_error_code`

其中 `maintenance_policy_version` 必须返回 `xnys-close-qualified-raw-path-v2`。Policy 是 slot identity 的一部分，因此旧 `xnys-close-due-outcomes-v1` 运行行继续保留审计；v2 为同一 session 创建独立 slot，不覆盖或复用旧语义的终态。

`latest_maintenance=null` 在首次符合 `close + 30m` 的 tick 之前是正常状态，不表示系统卡住。页面“研究结果跟踪”折叠区展示相同摘要；idle tick 不持续写日志。

### 5.3 手动重试

单快照故障兜底接口仍保留：

```http
POST /api/v1/opportunities/snapshots/{snapshot_key}/evaluate
```

服务端同样只选择 qualified prospective raw-path 候选，并只追加已到目标日的结果；pending 或数据缺口不会被写成成功结果。页面“手动重试到期缺口”只是显式恢复入口，按钮状态或前端筛选不能重新定义后端 qualification。

需要注意：**单快照手动接口没有自动批处理的“due 集合为空即零 provider”保证。** 它会为目标快照准备缺失 horizon 所需的历史数据，再由纯评估器拒绝尚未到期的结果。因此它应作为已到期缺口的人工恢复入口，不应替代每日自动预检。

其他只读接口：

```http
GET /api/v1/opportunities/snapshots?limit=10
GET /api/v1/opportunities/learning-summary
```

Snapshot 响应以加法字段分别返回 `underlying_path_candidate_count / underlying_path_progress` 与 `full_research_candidate_count / full_research_progress`。页面的手动重试按前者选择可恢复的 raw-path 缺口；严格 5D/20D 研究统计只按后者展示。两者不一致时，raw-path 结果只能显示为独立“标的路径审计”，不能进入完整研究命中率。

## 6. 学习摘要门槛

结果回填不等于可以展示“胜率”。Learning summary 先要求 outcome 对应候选在 `canonical_full_research_v1` track 中同时为 `qualified` 且 `prospective`；raw-path track 只合格的 outcome 保留审计，但不进入 hit/miss/neutral 分母。通过完整研究门禁后，5D 与 20D 仍永不混算，摘要只选择当前策略基线下同一 cohort：

- 相同 `signal / freeze policy / playbook / scope` 版本；
- 相同 ranking method、universe 与 requested limit；
- 相同结构 setup signature；
- 相同冻结 Regime；
- 相同方向。

同 ticker、同信号交易日、同 horizon、同 cohort 只计一次。参考价格修订不一致、来源连续性不成立或目标 close 质量缺失的记录会排除，并单列 `excluded_quality_count`。

当前显示门槛固定为：

- 至少 **20 个方向样本**；方向样本为 `CONTEXT_HIT / CONTEXT_MISS / NEUTRAL`，`NON_DIRECTIONAL / DIRECTION_UNKNOWN` 不进入分母；
- 同时来自至少 **20 个不同的 signal sessions**；
- 两个条件必须在同一 cohort、同一 horizon 内同时满足。

未达门槛时，API 将 hit/miss/neutral/non-directional 计数和命中率返回为 `null`，页面只显示方向样本数和独立交易日进度。达到 20/20 后只进入 `investigation_ready`，含义是可以人工调查，不代表统计独立性、可交易 edge 或未来收益。

`auto_adjustment` 永久为 `false`。无论样本多少，本流程都不会修改排名、证据权重或 Playbook。若人工形成新规则，必须创建新版本、做 walk-forward 验证并由用户确认。

## 7. 当前限制

- **只验证 underlying 路径。** 结果不是期权 P&L，不包含 strike、expiry、DTE、IV、Greeks、spread、滑点、合约乘数或真实 fill。
- **Raw path 与完整研究严格分轨。** Degraded canonical 可以回填 qualified prospective underlying 路径，但 analysis quality degraded 会排除完整研究 track；不能用这些 outcome 填充策略命中率。
- **没有冻结 TriggerSpec。** 当前没有预定义 entry trigger、invalidation、`trade_taken` 或未触发事实，因此不能生成“错过机会”、真实阳性/假阳性或执行质量结论。
- **到期前必须保持 0 已回填结果。** 新系统开始积累时，5D/20D 显示 0 是正确状态；只有目标 XNYS session 真正收盘后才允许追加，不能为了让页面“有数据”提前回填。
- **数据源连续性 fail closed。** 冻结源与评估源不一致、参考价无法核验、session 缺失或 SPY 无法严格对齐时，保持缺口或 `partial`，不跨源用 0、旧值或推测补齐。
- **服务进程必须运行。** 当前 scheduler 宿主是 FastAPI daemon；没有外部进程管理器时，电脑休眠或服务停止期间不会主动唤醒，但下次启动可做最近 session 的有界接续。
- **最多扫描 500 个快照。** 更长历史需要显式归档/分批策略，不能假设当前 tick 覆盖无限记录。
- **自动维护不补造旧快照或 qualification。** 对已有 assessment 的快照，它只评估被证明为 qualified prospective 的 raw path；只有已知旧 v1 快照在 assessment 缺失时可为 prospective raw path 保留 candidate eligibility 兼容读取。完整研究 track、canonical 快照缺 assessment 以及任何 qualification 读取异常全部 fail closed，不会把历史普通预览事后转换成 canonical publication 或完整研究样本。
- **没有自动交易或自动调权。** 本模块不导入 broker trade context，也不调用任何交易接口。

## 8. 运维与排障

### 8.1 最小验收

1. 设置 `OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED=true`；
2. 重启 FastAPI 服务；
3. 请求 `GET /api/v1/opportunities/learning-summary`；
4. 确认 `automatic_maintenance_enabled=true`；
5. 在尚未到达任何维护时点时，允许 `latest_maintenance=null`；
6. 到达某个 XNYS `close + 30m` 后，确认出现最近 session、尝试次数和结果摘要；
7. 对仍未到期的快照，确认 `inserted_outcomes=0` 且没有因 scheduler 产生历史行情请求。

维护日志刻意保持低噪声：记录 daemon 启停、实际 `evaluated` 终态，以及发生变化的异常类型；`waiting_settlement`、`premarket_protected`、已有终态和其他 idle tick 不逐分钟打印。

### 8.2 常见状态

| 现象 | 含义与处理 |
|---|---|
| `automatic_maintenance_enabled=false` | 开关未启用，或改 `.env` 后没有重启服务 |
| `latest_maintenance=null` | 尚未到第一个 `close + 30m`；先核对 XNYS 日期与服务启动时间 |
| `completed` 且新增为 0 | 该次预检没有到期缺口，属于正常幂等结果 |
| `degraded / outcome_data_gap` | 已到期但来源连续性、目标 bar 或对照数据不完整；查看 `next_retry_at` |
| `failed` | 本次发生运行异常；API 只暴露异常类型，不返回供应商敏感原文 |
| `running` | 某 worker 持有 45 分钟租约；不要直接修改数据库 |
| `running` 超过租约 | 新 worker 可在尝试预算内接管；旧 owner 随后提交会被拒绝 |
| `attempt_count=2` 且仍 degraded/failed | 当前日 slot 已耗尽；等待后续 XNYS session 自动再检，或对明确到期快照使用手动接口 |
| 5D/20D 已回填为 0 | 先核对 raw-path track 是否 qualified prospective，以及目标 XNYS session 是否真的收盘；未到期时不应手工制造结果 |
| Raw path 已回填但没有命中率 | 先核对完整研究 track；degraded analysis quality、非 `research_ready`、数据不完整、方向不可操作或 SPY anchor 缺失都会排除，之后同一 cohort 还须满足 20 个方向样本和 20 个不同信号交易日 |

排障时不要直接删除或改写 `opportunity_outcome_maintenance_runs`。租约、尝试次数和错误摘要是判断并发、恢复与 provider 稳定性的运行证据；快照和 outcome 表更不得修改。

## 9. 验证覆盖

后端确定性测试覆盖：

- 精确 `close + 30m` 前零执行；
- 提前收盘日；
- 第一次降级后等待 `close + 4h30m`；
- 每个 slot 最多两次尝试；
- 两连接只允许一个 owner；
- 租约过期接管和旧 owner 拒绝；
- `08:45–09:30 ET` 保护窗口；
- daemon 停止与 idle 低噪声；
- pending-only 自动预检零 provider；
- qualified prospective raw-path 选择、degraded canonical 路径回填，以及 retrospective/unverified 零 provider；
- 完整研究 track 排除 degraded raw-path outcome，不污染命中率；
- 多快照 ticker/SPY 共享历史 cache；
- 20 方向样本 + 20 独立 signal sessions 门槛；
- API 维护状态与 FastAPI lifespan 启停。

相关检查可运行：

```bash
python -m pytest \
  src/opportunities/tests/test_maintenance_repository.py \
  tests/test_opportunity_outcome_scheduler.py \
  tests/test_opportunity_snapshot_service.py \
  tests/test_premarket_scheduler.py \
  api/v1/tests/test_opportunities_endpoint.py

cd apps/dsa-web
npm test -- src/api/__tests__/opportunities.test.ts \
  src/components/opportunities/__tests__/DailyOpportunityList.test.tsx
npm run lint
npm run build
```

网络供应商在线验收仍需在真实到期 session 后完成；离线测试证明时间、幂等、租约和 fail-closed 合同，不证明外部数据源永远可用。

## 10. 回滚

1. 设置 `OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED=false`；
2. 重启 FastAPI 服务；
3. 确认 `learning-summary.automatic_maintenance_enabled=false`；
4. 保留已有维护行、冻结快照和 outcome，不删除、不覆盖；
5. 必要时继续通过单快照接口人工补充真正到期的结果。

回滚只停止未来自动唤醒。机会研究页面、普通扫描、Journal、正式盘前研究 scheduler、历史 qualification、快照和已回填结果均不应受到影响；旧 v1 与新 v2 maintenance slot 都保留审计。

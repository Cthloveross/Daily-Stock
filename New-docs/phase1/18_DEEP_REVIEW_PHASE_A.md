# 18. 深度复盘 Phase A 实现注记（蓝图 17 §三(f) 落地）

> as-of: 2026-08-25 ｜ 真源关系：设计以 `17_PRO_PRACTICE_BLUEPRINT.md` 为准；本文只记录 Phase A 的实现事实、契约细节与偏差清单。取证结论仍以文档 16 为准。

## 0. 交付范围

蓝图 Phase A 两个可独立交付/回滚的部件：

- **A1 引导式日终复盘流**：`/journal/review/daily`（S0-S4 步进器，盲评优先）＋ `GET/POST /api/v1/journal/review-flow/daily` ＋ append-only 会话修订链。
- **A2 既有数据上的 MAE/MFE（诚实用法）**：`src/journal/excursions.py` 纯函数 ＋ `journal_v2_episode_excursions` / `market_5m_bars` ＋ `scripts/backfill_excursions.py` ＋ 单笔复盘页四区改造与只读偏移诊断。

硬红线（测试锁定）：只读不下单；无概率无预测；日终流密封并自愿揭示前**载荷级**无任何盈亏字段；标缺绝不冒充；禁用指标清单（SQN/Zella 类合成分、hold-time 分桶 edge、MAE 推导止损）进代码常量且新页面有渲染负断言。

## 1. 新表 DDL（均由 `init_ledger_schema` 创建）

模型文件：`src/journal/ledger/review_flow_models.py`；前两张已加入
`_APPEND_ONLY_TABLE_NAMES`（SQLite UPDATE/DELETE deny trigger，同 journal_v2 全家）。

### 1.1 `journal_v2_daily_review_sessions`（append-only 修订链）

| 列 | 说明 |
|---|---|
| `account_key, et_date, session_kind('trading_day'/'rest_day'), revision` | `(account_key, et_date, revision)` 唯一；revision ≥ 1 |
| `previous_session_id` | 自引用、唯一（链不可分叉） |
| `episode_build_id` | 会话锚定的激活 build |
| `started_at / sealed_at / revealed_at / revealed_after_seal` | 盲评顺序数据；CHECK：`revealed_after_seal=0 OR sealed_at IS NOT NULL` |
| `steps_json / process_scores_json / violation_acks_json / note / content_sha256` | 内容冻结与幂等去重（canonical JSON 的 sha256） |

仓库层（`daily_review_repository.py`）额外强制：密封后内容逐字节冻结，唯一合法追加是「揭示」修订；揭示必须晚于已密封修订（同一笔写入禁止既密封又揭示）；揭示不可撤销；未密封请求揭示报错。

### 1.2 `journal_v2_episode_excursions`（append-only，幂等重算）

`(episode_build_id, position_episode_id, code_version, attempt)` 唯一；当前
`code_version = "underlying-5m/1.1"`（公式或窗口口径变更必须递增，旧行永不改写；1.0→1.1 变更见 §8）。列：status（`ready/partial/missing_bars/not_applicable` + CHECK）、status_reason、exposure(±1)、u0/u0_at/u0_flag、mfe/mae_underlying_pct（**小数**，非百分点）、mfe_at/mae_at/mae_before_mfe、atr14/mfe_atr/mae_atr、bars_used、coverage_start/end、missing_sessions_json、computed_at、attempt（≥1）。

时间戳自 1.1 起统一 UTC 入库（读取按 UTC 贴标原样返回）；1.0 历史行的时间戳是 ET wall-clock 存储的，保留为历史记录，默认读路径（当前 code_version）不触及。

`attempt` 语义（§8 修复 5）：默认重算幂等返回既有最优行、零写；仅回填脚本以 `allow_retry=True` 且新结果**严格更好**（status 等级 ready>partial>missing_bars>not_applicable，或同级且 bars_used 更多）时，以 attempt+1 追加新行。读取（单条与列表）一律取最优/最新 attempt。

### 1.3 `market_5m_bars`（市场数据持久层，非账本）

`(symbol, bar_start_at)` 唯一（bar **开始**时间口径，UTC 存储）＋ `session_date_et` 索引。只插入已完结（start+5min ≤ now）的常规时段 bar，冲突跳过（先到先得）；**不装 deny trigger**（供应商修正需保留人工重建通道），但仓库层从不 UPDATE。存在意义：yfinance 5m 仅回溯 ~60 天，本地持久化让未来回合永不撞保留墙。

## 2. 端点契约（`api/v1/endpoints/journal_review_flow.py`）

- `GET /api/v1/journal/review-flow/daily?date=`：装配 `episodes_today[]`（车道/规则编号/verdict/needs_ack，**无盈亏字段**）、`open_positions[]`（出场预登记状态）、`process_metrics[7]`、会话与休息连续计数。`reveal` 仅当最新会话 `sealed_at≠null 且 revealed_after_seal` 时非空——盲评是载荷级契约，端点测试对整个 JSON 做 `pnl/profit/盈亏/realized` 负断言。
- `POST /api/v1/journal/review-flow/daily`：追加会话修订。服务端强制：休息日确认只在当日无活动时合法（反向谎报 422）；密封要求当日每条 violation 有非空一句话确认（合并既有 acks）；手填只接受 `manual_allowed` 指标（1/4）且值域 是/否/标缺；`exit_plans` 写**既有** review annotation 修订链（仅替换 `invalidation_plan`、附 `exit_plan_registration` tag，其余字段原样保留——新增用途，不新增机制），且只在全部校验通过后落笔（被拒请求零写，§8 修复 8），写后重算流再冻结自动指标。
- **揭示契约（§8 修复 1）**：`reveal=true` 是**最小请求**（`et_date` + 可选 `expected_revision` 链位校验），内容字段一律忽略、不写出场预登记；服务端把已密封修订的内容**逐字重放**成新修订，只翻揭示位。保持：揭示要求此前已有密封修订（否则 422）、密封与揭示不能同一修订、揭示不可撤销。客户端漂移（页面重载丢手填分、封卷后注解写入、指标 3 因会话行出现翻面）不再能卡死揭示。
- `GET /api/v1/journal/episodes/{id}/excursion`：偏移记录或标缺原因 ＋ 机械判定块（车道/四象限/两个反事实）。永久免责随载荷携带。
- `GET /api/v1/journal/review-flow/excursions`：散点数据（|MAE|×R，按持有结构 intraday/overnight 分层；R 或 MAE 缺失的点带原因返回、前端不画成 0）。

四象限（`classify_decision_quadrant`）：合规∧盈=应得的赢；合规∧亏=坏运气；违规∧盈=**侥幸**（UI 标红）；违规∧亏=应得的输；uncovered/unknown/盈亏缺失或为 0 → 不判定。

## 3. 七项过程指标的 Phase A 可得性（实现口径）

| # | 指标 | Phase A 实现 | basis |
|---|---|---|---|
| 1 | 推送依从率 | push ledger 未建 | **标缺**（可手填自评，不冒充自动值） |
| 2 | 结构依从率 | 今日新开回合按 \|opening_cash_flow\| 加权的合规风险占比；uncovered/unknown 不进分母、缺现金流逐笔报数 | 自动 |
| 3 | 出场规则依从率 | Phase A ＝「平仓前是否已有在册出场计划」比率；预登记机制启用前标缺；规则 vs 情绪机判待 Phase B 结构化 exit_plan | 自动/标缺 |
| 4 | 有效点差支付 | 需下单时 mid 快照 | **标缺**（可手填自评） |
| 5 | 费用占比 | 今日平仓回合 Σfee/Σ权利金 对照 1.25% 基线（F4）；缺费用/现金流逐笔报数 | 自动 |
| 6 | 决策日志完整率 | 今日新开回合有「当日、进场侧字段非空」annotation 的比例。计数规则（§8 修复 9）：只认 `setup_thesis/entry_trigger/position_rationale` 至少一项非空；`invalidation_plan` 不计（S3 出场预登记只写该字段，出场计划在册率由指标 3 负责，不重复计） | 自动 |
| 7 | 休息纪律 | 当日零活动判定 ＋ 已密封休息日连续计数 | 自动 |

## 4. MAE/MFE 口径（与蓝图逐条对齐）

- exposure：long call / short put / long equity → +1；long put / short call / short equity → −1；组合腿或不明 → not_applicable（不猜）。资产类型词汇以账本实际输出为准：正股是 `"equity"`（`ledger/episodes.py`），`"stock"` 保留为别名（§8 修复 2）。
- U0 = opened_at 之后第一根 5m bar 的 open；盘前开仓 → 当日首根 RTH bar（flag `premarket_open_first_rth_bar`）；盘外开仓（时钟事实）→ 次一时段首根 bar（flag `entry_outside_rth_next_session_bar`）；RTH 内开仓但入场日整日缺 bar（数据事实）→ flag `entry_day_bars_missing_next_session_bar`，不谎称盘外开仓（§8 修复 7）。
- 短于一根 5m bar 的持有（窗口内不含任何理论 bar 开始时刻）→ `not_applicable`，status_reason 以 `sub_bar_hold_below_5m_granularity` 开头——这是 5m 粒度限制，不是数据缺失，不再写 `missing_bars`（§8 修复 4）。窗口理应含 bar 开始时刻而 bar 缺失的仍是 `missing_bars`。
- 出场 bar 溢出（如实声明，进 `EXCURSION_LIMITATIONS` 与前端文案）：窗口按 bar 开始时间截取，平仓时刻所在的整根 bar 计入，因此平仓之后至多约 5 分钟的行情也会被计入（§8 修复 8）。
- 逐 bar favorable/adverse 用 high/low 按 exposure 取向；`mfe = max(0, max exposure×(fav/U0−1))`、`mae = min(0, min exposure×(adv/U0−1))`；记 `mfe_at/mae_at/mae_before_mfe`。RTH only，隔夜跳空由次日首根 bar 体现。
- ATR 标尺：日线 Wilder ATR14（入场日之前的完结日线，不足 15 根标缺）；`x_atr = x×U0/atr14`。
- 覆盖判定：持有窗口内预期交易日（周一至周五，开仓晚于 15:55 顺延、平仓早于 09:30 前移）vs 实际 bar 覆盖；缺日 → `partial` 并逐日列出（原因文案注明可能含休市日——无交易日历，不冒充判断）；全无 → `missing_bars`。
- **不做** BS/delta 折算；免责文案（含「持仓分组存在内生性」）由前端组件 `ExcursionDiagnostics` 永久渲染并被测试逐字锁定。

## 5. 回填与每日持久化（`scripts/backfill_excursions.py`）

- 全量：`python -m scripts.backfill_excursions`（近 ~60 天窗口；顺序抓取 + 0.6s 间隔，走 `StockService.get_history_data` 同一 loader/缓存/fallback，Moomoo 口径先过 `normalize_bar_label_convention`）。
- 每日增量（**同一脚本兼任每日 5m 持久化任务**）：`python -m scripts.backfill_excursions --daily`，只处理近 5 日活跃 underlying。调度：收盘后（建议 ET 16:30 之后）用 launchd 模板 `scripts/launchagents/com.dailystock.excursion-daily.plist` 或 cron；**不新增守护进程，仓库也不代为加载**（加载是用户动作，见 §8 修复 10）。写入面仅 `market_5m_bars`（插入-跳过）与 excursion 行（append-only 幂等），失败单标的不拖垮整批。
- 重试语义（§8 修复 5）：既有最优行 status ∈ {missing_bars, partial} 且回合仍在保留窗口内的，重新进入待算集；重算结果**严格更好**才以 attempt+1 追加（无新 bar 重跑零写），瞬时抓取失败不再被首次写入永久冻结。
- 2026-08-25 真实执行（build 1，`underlying-5m/1.0`，历史记录；1.1 重跑见 §8）：closed 1,437 全部落行——**ready 355 / partial 9 / missing_bars 1,051 / not_applicable 22**；missing_bars 绝大多数为平仓早于 2026-06-27（保留窗口起点）的**永久标缺**，原因逐行写明。5m bars 持久化 **94,020 根 / 30 个 underlying**（2026-06-29→2026-08-25，各 41 个交易日）；SKHYV 抓取失败（ValueError，退市类符号）如实记录。

## 6. 前端落点

- `pages/JournalDailyReviewPage.tsx`（S0-S4 步进器；来源徽章 自动/手填/标缺；打分卡无任何盈亏字段与按盈亏着色；密封→揭示两次独立 POST）。
- `pages/JournalEpisodeReviewPage.tsx` 四区强制顺序：区① `DecisionSnapshotPanel`（regime/推送标缺徽章走蓝图 E-4/Phase B 口径）；区② 折叠揭示（揭示前无 Net、不发行情请求、保存/完成按钮禁用）；区③ 四象限 chip（侥幸红）＋ 双轨词表（mistake 词表 chips 写入既有 error_types labels）；区④ `EpisodeWhatIfPanel`（两个反事实，gate 反事实在 E-4 回填前标缺）。
- `components/journal/review/ExcursionDiagnostics.tsx`（单笔读数 + |MAE|×R 分层散点 + 永久免责）；`reviewHardLines.ts`（免责/禁用词/mistake 词表常量，与后端同源）；`DailyReviewEntryCard`（Journal 首页入口：未开始/进行中/已密封/休息日）。

## 7. 与蓝图的偏差（如实列出）

1. **violation 确认的落库位置**：蓝图 §三(d) 写「violation 确认直接写 annotation 链」，§三(e)-2 又在会话表里列了 `violation_acks_json`。实现取 (e)-2：确认存会话链（含 lane/rule_id/一句话），出场预登记存 annotation 链——避免同一事实两处真源。
2. **响应字段名**：蓝图 (f) 的 `open_positions_needing_exit_plan` 实现为 `open_positions[]`（带 `has_exit_plan` 标志），S3 前端据此筛选；多返回已在册计划文本供指标 3 与区① 复用。
3. **ATR 列**：蓝图 (e)-1 表未列 ATR 列；按任务要求补充 `atr14/mfe_atr/mae_atr`（可空、缺即标缺），属 additive。
4. **单笔页 AI 面板位置**：AI 分析整体位于揭示后（区②语义），但 DOM 顺序在区③ 工作单之后——蓝图的「过程段/结果段拆分生成」属 AI prompt 改造，未在 Phase A 动 AI 链路。
5. **`npm run build` 未执行**：编排方约定由部署侧执行；本地验证为 tsc + vitest + lint（Node 22）。
6. **ET 换算口径分道（2026-08-25 起）**：review-flow 调用路径改用真实 `America/New_York`（DST 感知，§8 修复 6）；`personal_edge` 自身保持其文档化的 UTC−4 近似不动（月度口径的既有已证结论不重算）。两套口径仅在 EST 时段（11 月—3 月）的入场小时与 04:00–05:00 UTC 的自然日边界上有差。

## 8. 复核修复（2026-08-25 深度复核 Phase A）

对抗性复核确认了以下缺陷并全部修复；涉及存储语义的（修复 2/3/4）以 `EXCURSION_CODE_VERSION = underlying-5m/1.1` 追加新行落地（1.0 旧行保留），并对真实库重跑回填（结果见下）。

1. **HIGH 揭示被内容漂移卡死**（`api/v1/endpoints/journal_review_flow.py`）：原实现在揭示 POST 里按**当前**流状态重建 process_scores/session_kind/acks，再撞冻结校验——页面重载丢手填分、封卷后写注解、指标 3 因会话行出现翻面，都会让揭示永久 422。修复：揭示改为服务端**逐字重放**已密封修订（新增 `_post_reveal_revision`），请求最小化（`et_date` + 可选 `expected_revision`）。保持：须先有密封修订、密封≠揭示同修订、揭示不可撤销。回归：仓库级三场景测试 + scratchpad 对抗repro 全过。
2. **MED-HIGH exposure 词汇错位**（`src/journal/excursions.py`）：判定表只认 `"stock"`，账本实际写 `"equity"`（`ledger/episodes.py:611`）——22 笔正股回合被假 `not_applicable`。修复：接受 `"equity"`（保留 `"stock"` 别名），单测改钉真实词汇。
3. **MED 时间戳 ET-naive 入库、按 UTC 读出**（`src/journal/ledger/excursion_repository.py`）：aware ET datetime 经 SQLite 存成 ET wall-clock，读出时被贴 UTC 标签——所有 1.0 行的 u0_at/mfe_at/mae_at/coverage 偏移 4 小时。修复：插入前统一 `_utc()` 转换；读取按 UTC 贴标即「存什么返回什么」。round-trip 测试锁定（ET 进 → API 出同一时刻）。
4. **MED 亚 bar 持有谎称 missing_bars**：持有短于一根 5m bar 且窗口不含任何理论 bar 开始时刻，是**粒度限制**不是数据缺失。修复：判 `not_applicable`，status_reason 以 `sub_bar_hold_below_5m_granularity` 开头；窗口理应含 bar 开始时刻而缺 bar 的仍是 `missing_bars`。
5. **MED 瞬时失败被永久冻结**：`(build, episode, code_version)` 幂等键让抓取失败时写下的 `missing_bars` 行永不重试。修复：唯一键扩为 `(…, attempt)`，回填对保留窗口内 status ∈ {missing_bars, partial} 的行重新计算，**严格更好**才以 attempt+1 追加（无新 bar 零写＝真幂等）；读取取最优/最新 attempt。**迁移如实记录**：表仅 1 天历史，采用 DROP 触发器 → RENAME 旧表 → create_all 建新表 → 整行复制（attempt=1，id 保留，按 id 去重可续传）→ DROP 旧表 → 重装触发器；RENAME 不改索引名，故先 DROP 旧 `ix_jv2_excursion_scope` 再由迁移显式重建。append-only 语义全程保持（无任何行内容被改写）。
6. **LOW-MED EST 时段 ET 换算**：review-flow 原用 personal_edge 的 UTC−4 近似；冬令时会把 11:30 EST 的 0DTE 入场算成 12 点后而误判 `late_0dte`。修复：review_flow 内建 `America/New_York` 真实换算（`_et_hour/_et_day/today_et_date`），personal_edge 内部不动；EST 用例（2026-12-01 16:30 UTC = 11:30 EST → `intraday_0dte`）入测试。
7. **LOW u0_flag 把入场日数据缺口标成盘外开仓**（live episode 1064：12:48 ET RTH 内开仓、入场日无 bar）：时钟事实与数据事实分开——新 flag `entry_day_bars_missing_next_session_bar`，前端文案「入场日无 5m bar（数据缺口，非盘外开仓）」。
8. **出场 bar 溢出未声明 + POST 校验顺序**：(a) `EXCURSION_LIMITATIONS` 增加「出场 bar 溢出：平仓所在整根 bar 计入，平仓后至多约 5 分钟行情被计入」，前端同文案 + tooltip；(b) POST 原先在校验之前写出场预登记——重排为全部校验（session_kind、ack 目标、密封覆盖、手填目标、exit-plan 目标、内容尺寸预检、密封冻结守卫）通过后才落笔，被拒请求零写（回归测试锁定）。
9. **指标 6 被 S3 写入抬高**：出场预登记修订的 `invalidation_plan` 被当作进场决策日志。修复（规则文档化）：指标 6 只认 `setup_thesis/entry_trigger/position_rationale` 至少一项非空（字段集判定，对历史行同样成立）；S3 写入另附 `exit_plan_registration` tag 留痕。出场计划在册率由指标 3 负责，不重复计。
10. **`--daily` 调度诚实化**：不加守护进程；新增 launchd 模板 `scripts/launchagents/com.dailystock.excursion-daily.plist`（仿既有三个模板，StartCalendarInterval 本地 16:45，需按机器时区调整到 ET 收盘后）。**仓库与脚本均不代为加载**——加载是用户动作（F-1 同款门槛）；`--daily` 运行结束打印加载命令：`launchctl load ~/Library/LaunchAgents/com.dailystock.excursion-daily.plist`（先按 `install_launchagents.sh` 的 sed 模式替换 `__PROJECT_DIR__`/`__PYTHON__`）。
11. **免责常量双语言漂移风险**：后端测试 `test_disclaimer_matches_frontend_constant_byte_for_byte` 直接读前端 `reviewHardLines.ts`，断言 `EXCURSION_DISCLAIMER` 逐字节一致——任一侧单边改动都会在后端测试报警。
12. **禁用词负断言只覆盖 S0**：日终页测试扩展为走到 S2（七项指标卡全部渲染）后再扫一遍禁用词。

**1.1 真实重跑（2026-08-25，build 1，1,437 笔已平仓回合，全部以新版本追加）**：**ready 356 / partial 9 / missing_bars 1,039 / not_applicable 33**（1.0 对照：355 / 9 / 1,051 / 22，两版共 2,874 行、1.0 全数保留）。逐项验证：22 笔正股回合按 bar 可得性落 1 ready + 21 missing_bars（此前全是假 not_applicable）；33 笔亚 bar 持有（含保留窗口内那 7 笔）全部拿到 `sub_bar_hold_below_5m_granularity` 的诚实 `not_applicable`（跨 bar 开始时刻而缺 bar 的仍是 missing_bars）；episode 1064 的 u0_flag 改为 `entry_day_bars_missing_next_session_bar`；抽样确认时间戳 UTC 入库（u0_at 与 opened_at 同为 UTC wall-clock）。5m 持久层增至 **94,860 根 / 30 underlying**（2026-06-29→08-25）；SKHYV 抓取仍失败（ValueError，退市类符号）如实记录，其保留窗口内回合保持 missing_bars 且在未来运行中可被重试。

## 9. 回滚

全部为新增文件/新表 + 单页改造：删除新增文件、`git checkout` 三个被改文件（`api/v1/router.py`、`App.tsx`、`JournalPage.tsx`、`JournalEpisodeReviewPage.tsx`、`TradeLogicDraftPanel.tsx`、`repository.py` 的注册行）即回滚；新表为 additive，留存不影响任何既有读写路径；扫描与推送链路零接触。

§8 复核修复的回滚补充：代码层回退各文件即可；数据层 `attempt` 列迁移**不需要**反向迁移——旧代码对宽唯一键与 `attempt`（server_default=1）完全兼容，`underlying-5m/1.1` 行在旧代码下只是不被默认读取（旧常量指向 1.0）；append-only 原则下不删除任何已追加行。launchd 模板文件删除即回滚（本仓库从未加载过它）。

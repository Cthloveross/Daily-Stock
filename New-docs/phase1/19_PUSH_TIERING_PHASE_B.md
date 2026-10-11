# 19. 推送分级 Phase B 实现注记（`INTRA-0-7/v1`）

> **历史版本说明（被 2026-09-10 合同覆盖）**：本文的旧 5m P0 七条件、
> `≤1/日、≤3/周`、09:45–15:15 窗口，以及必须先有合约/Regime 证据才成立的
> P0 语义已经退役。现行 `p0_2m_signal_v1` 四条件、P0-X、2/日与 7/周预算、
> 先发 underlying 后异步补期权及上线门禁，以
> [`22_P0_2M_SIGNAL_AND_CALIBRATION.md`](22_P0_2M_SIGNAL_AND_CALIBRATION.md) 为准。
> 下文保留用于解释历史台账字段和 Phase B 的迁移来源。

当前运行补充：strict-2m/P1-O 使用独立分钟快时钟，不等待慢页面或期权预热；
开盘启用时后台慢预热在 09:30–09:36 让路。`history_loaded_count` 不代表当前同钟点
60 日 ready，固定前 60 日遇到半日市不能借第 61 日补齐下午 RTH。
正式 P0 仍需完整历史留出集与现场 shadow 全部门禁通过，不是等满 20 天自动启用。

> as-of: 2026-09-08 ｜ 本文记录截至该日的历史实现事实。旧蓝图 17 的 V2-B/4-7DTE
> 隔夜设计已冻结为历史。该版本当时的交易车道是
> `INTRA-0-7/v1`：0-7DTE、同一 ET 日平仓。P1-O 顺序开盘发现窗从
> 09:32 ET 最早双确认到 09:35 ET 原生 5 分钟最终确认（旧版最终画像最迟只接受到
> 09:44:59）；P0 确认候选窗为 09:45-15:15 ET；
> 正常交易日 0DTE 于 14:30 ET 提前截止、15:30 ET 检查未平仓、15:45 ET
> 为退出期限；半日市按 XNYS 真实收盘分别平移为 close−90/30/15 分钟。
> 系统只读，不下单。

## 0. 交付范围

- **B0 顺序开盘发现层（P1-O，旧版）**：09:31-09:34 每分钟只使用已经完成的 1m K；
  连续两个相邻 checkpoint 同向通过严格门槛才响铃，最早 09:32。若此前未确认，
  09:35 用供应商原生完成 5m K 作一次最终确认。盘中计划、钉选与 `STOCK_LIST`
  核心标的合并后最多 20 票，最多 3 个候选/日。第一条不等待期权链，后续合规
  0-7DTE 合约只以静默补充发送。
- **B1 四级推送协议（P0-P3 tier engine）**：`src/services/alert_tiering.py`（纯判定层）＋ `src/services/intraday_opportunity_alerter.py` 重构（原六条 v1 规则直推 Telegram → 四级出口）。
- **B2 持久化推送台账**：`journal_v2_push_events`（append-only，deny trigger）＋ 重启去重重建 ＋ 过程指标 1「推送依从率」由标缺转为机械关联可算 ＋ `GET /api/v1/journal/push-ledger/daily`。
- **B3 强证据小件**：① 当日财报拦截（计划面板 + 推送侧）；② XNYS
  收盘前 30 分钟同日平仓检查提醒；③「聪明钱/主力/大资金」措辞禁令 + 负向断言测试。

硬红线（测试锁定）：不确定一律降级、绝不升级；无概率无预测；**每条被抑制/降级的决定都落台账并带原因（无静默丢弃）**；系统通知（watchdog）不占任何预算；盲评日终流除指标 1 接线外不动。

## 1. 四级出口（蓝图 §2.2 的实现映射）

| 级别 | 通道 | 硬预算 | 触发（实现现状） |
|---|---|---|---|
| 旧 P0 行动级 | Telegram 响铃 | ≤1/日、≤3/周（历史预算） | 已退役七条件全过（见 §2）∧ 财报窗确证清白 ∧ R6 未触线 |
| P1-O 开盘发现 | Telegram **响铃** | ≤3 个候选/日（独立于 P0/P1 近失预算） | `opening_impulse_v2` 在 09:32-09:34 获得相邻同向双确认，或 09:35 用五根完整 1m 累计结果最终确认；仅表示 setup 已出现，不表示入场确认 |
| P1 预备级 | Telegram **静默**（`disable_notification=true`，`TelegramSender.send_to_telegram` additive 参数） | 机会近失 ≤3/日；退出/R6 风险提醒不占机会预算 | (a) 七条件恰差一条（缺哪条写明）；(b) P1-O 的期权盘口补充；(c) XNYS 收盘前 30 分钟同日平仓检查提醒；(d) R6 触线通告 |
| P2 观察级 | 仅 `/intraday` 「今日提示流」（台账读端） | 不限 | 原六条 v1 规则的全部事实（**不再直推 Telegram**）＋ 被财报/R6 降级的候选 |
| P2 strict-2m 研究提醒 | 显式开启后 Telegram 响铃 | 独立 RTH 2/日、7/周；盘外每盘段 1/日、合计 5/周 | 真实四条件命中，但未验证收益；不占正式 P0/P0-X 指标，不发期权补充 |
| P3 日志级 | 仅台账 | — | 被抑制候选、双窗在动但差 ≥2 条的评估记录、持仓快照标缺时的跳过记录 |
| 系统通道 | Telegram 响铃 | 不占预算 | `notify_system`（watchdog）原语义，另落 tier=system 台账行 |

行为变化（用户可见）：放量/强波段/位移/盘前异动/大单/日型六类宽松事实提示自本次起**只出现在页面「今日提示流」**；手机端保留 P1-O 开盘发现、P0、P1 风险/近失与系统出口。P1-O 虽记为 P1 家族，但是否响铃以台账的 `delivery_channel` 为真源，页面不再把所有 P1 统称为“静默”。

去重与合并：同 tick 的 P0 各成一条响铃消息行、普通 P1 合并为一条静默消息；P1-O 按复合异常工程分数排序后最多取 3 票合并成一条响铃消息。v1/v2 共用规范键 `("opening_setup_v2", ticker, "09:30")`，同一票早期已响后 09:35 不重复；它与 `("p0", ticker)`、`("p1_near_miss", ticker, 差额条件号)` 分离。服务重启从 decision 行恢复事实去重。预算恢复同时区分三种结果：成功 delivery 占用；明确 `telegram_ok=false` 且无成功结果时释放；已有 Telegram decision 但没有 delivery 行时保守占用，防止进程恰在外部发送成功、追加 delivery 前退出后重启超发。全局 `INTRADAY_ALERTS_MAX_PER_DAY`（默认 20）仍是机会类 Telegram 行数外层护栏。`same_day_flatten_reminder` 与 `r6_fuse_notice` 是风险控制提醒，绕过 P1/全局机会预算。

### 1.1 P1-O 顺序开盘发现合同

`src/opportunities/opening_impulse_sequential.py` 在 09:31-09:34 只聚合当前已经完成、
以起始时刻标记的 1m K；每个 checkpoint 都和目标日前恰好 20 个 XNYS 交易日的
相同累计开盘区间比较。早期必须相邻两次严格通过且方向一致，最早 09:32 响铃；
09:35 的现行实现改用 09:30 起五根完整 1m 累计画像作一次最终确认，不再依赖
供应商原生 5m。严格门槛全部满足才算通过：累计波幅 ≥0.40 ATR、实体 ≥0.30 ATR、
实体效率 ≥60%、顺势收盘位置 ≥68%、同槽量比 ≥1.0、同槽成交量分位 ≥80%、
同槽波幅分位 ≥90%。它是保守的工程筛选，不是论文证明的最优参数。

第一条消息包含方向、checkpoint 累计 OHLC、成交量/按累计收盘价估算的名义额、
ATR 标准化实体与波幅、历史同进度量比和分位、缺口及是否收复前收，并把累计
高/低/中位列为后续确认与失效观察锚；09:35 最终确认字段来自五根已完成 1m 的累计结果。
为满足
分钟 checkpoint 的时效，第一条明确写“期权盘口待补”，不等待较慢的 0-7DTE 链；完整 warm
扫描取得满足 bid、ask、delta、点差和新鲜度门槛的合约后，只补一条静默消息，且仍
声明它不是突破确认。调度器在 09:31-09:35 每分钟边界后 5 秒探测；计划/钉选/核心
标的的日线与精确 20 日 × 5 根开盘 1m 基准从 09:00 ET 起预热；凑足 20 个有效同槽才缓存
12 小时，不足样本只负缓存 5 分钟，避免把早到的不完整长历史锁死到当天结束。
若数据商在任一关键 checkpoint 后 5 秒仍未定稿，完整 Top/期权扫描先让路，调度器
每 2 秒补抓一次、最多 4 次；仅供应商滞后才重试，格式错误或缺历史直接 fail closed。
早期画像必须在其 checkpoint 的墙钟分钟内被 worker 观察；排队跨分钟即过期并抑制。
09:35 最终画像截止 09:36；09:36 起只留页面记录，不补发过时手机铃声。

该分层依据与 TSLA 2026-08-31 无前视回放见
[`21_OPENING_5M_ALERT_RESEARCH.md`](21_OPENING_5M_ALERT_RESEARCH.md)。一篇 SSRN
ORB 工作论文采用“首根形成候选、随后 high/low 突破才入场”的历史规则；它只
启发这里的分层设计。该版本 P0 曾是独立双窗规则，首根 high/low 仅作人工观察锚；
现行 P0 为严格 2m 四条件。

## 2. 已退役的 P0 七条件：历史实现与可评估性

> 本节不再描述当前 P0。旧 5m `burst_score`、Regime、期权摩擦、预算和时段门
> 不得再组合成 P0；当前四项 underlying 门槛见 phase1/22 §3。

单一真源：`src/services/alert_tiering.py`（模块 docstring 有同一张表）。

| # | 条件 | 评估来源 | 现状 |
|---|---|---|---|
| 1 | 盘前短名单在册 | `universe_scan.plan_always_include`（今日冻结盘前计划） | **自动**；短名单化（改进 #11，Phase C）落地前以计划钉选为代理，代理语义写入条件 detail |
| 2 | 映射 `INTRA-0-7` 纯日内结构 | `lane_availability.tickers[].available_dte_list` ∩ [0,7] | **自动**；要求 `same_et_day_only`，读数 deferred/unavailable → 标缺→降级 |
| 3 | 双窗确认 | 当前短窗（`session_bursts.current` 达 strong 线）∧ 长窗（`recent_displacement` 达 ±存活线）∧ **连续两个新的 5m 证据窗同向成立**（结束时间严格相差 5 分钟；同一/倒退 K 线不计且不能覆盖已有进度；向前跳格或变向会重置；重启后重新数，宁严不宽） | **自动** |
| 4 | regime gate 对该期限非红 | `gate_state` / `red_lanes` | **自动、fail closed**：0-1DTE 要求显式 allow/绿灯；gate 缺失或红灯均不得发 P0。2-7DTE 不受 0-1DTE 专属 gate 误关，但全车道红灯仍阻断。 |
| 5 | 同合约摩擦核算 | 服务端 warm 副本的 `candidate.option_contract_friction`；`spread% + 2×1.25% < \|delta\| × 已发生标的位移 / option_mid × 100` | **自动、fail closed**：方向向上选 Call、向下选 Put；合约池为 0-7DTE，硬要求 bid>0、`0.35 ≤ \|Delta\| ≤ 0.65`，再校验点差与报价新鲜度，并按点差、\|Delta−0.5\|、成交量、OI、DTE、ATM 距离稳定排序。0DTE 要求点差≤8%、报价龄≤30秒；1-7DTE 要求点差≤10%、报价龄≤90秒；任一证据不可核验即标缺降级。 |
| 6 | 预算未耗尽（≤1/日、≤3/周） | 台账重建的计数 | **自动** |
| 7 | ET 时段与同日退出合规 | 载荷 `as_of` + worker 实际观察时刻 + XNYS 当日官方 open/close | **自动、fail closed**：正常日 P0 在 09:45 前或 15:15 后不产生新候选，0DTE 在 14:30 后关闭；半日市分别使用 close−45 与 close−90 分钟的新仓截止，close−30 分钟检查未平仓，close−15 分钟退出。假日、真实收盘后或日历不可得时机会类 Telegram 统一降为 web-only；P1-O 早期画像仅限各自 09:32-09:34 墙钟分钟，09:35 最终画像接受至 09:44:59。 |

叠加在七条件之上的两道闸（不计入「恰差一条」计数）：

- **财报拦截（纯日内口径）**：`days_to_earnings == 0`（财报发生在同一
  ET 日）→ P0/P1 降 P2；未来 1-5 天不再因旧隔夜窗口被拦截，因为持仓必须
  当日结束。`within_blackout=true` 但缺少可核验的 `days_to_earnings` 时判
  `unknown`，P0 降 P1；字段契约漂移同样按未知处理，未知绝不冒充安全。
- **R6 保险丝（改进 #9，D2 修正后当日锁存）**：注入的当日已实现盈亏 ≤ −$12,000（`R6_FUSE_DAILY_LOSS_USD`，doc16 §5.2 宽线）→ 当日 P0/P1 全部降 P2（`r6_fuse_active`）＋ 一条 P1 静默触线通告。**触线即锁存（`resolve_fuse_state`）**：之后盈亏回升或数据断供都不解锁；ET 日翻转后复位，重启后从台账 `r6_fuse_notice` decision 行重新锁存。已锁存＋缺失维持 triggered；未锁存＋缺失对 P1 按未触线处理，但 P0 要求正面确证未触线，因此降 P1。旧跨周末提示已取消，不再产生 `weekend_notice` 决定。

P0 自动收紧（蓝图：passed 率滚动 20 条 >70% → 收紧）：**只记账与展示，不自改**。`compute_p0_pass_rate`（`src/journal/push_metrics.py`）给出机械代理（已投递 P0 无同日跟随回合＝passed），feed 与页面带「机械代理，非人工清账」标注；实际自动收紧闸在常量 `P0_AUTO_TIGHTEN_ENABLED = False` 之后，翻开需人工决定与文档记录。

## 3. 台账 DDL 与重启重建（B2）

`journal_v2_push_events`（`src/journal/ledger/push_models.py`；已入 `_APPEND_ONLY_TABLE_NAMES`，SQLite UPDATE/DELETE deny trigger）：

| 列 | 说明 |
|---|---|
| `push_id`(unique) / `record_kind('decision'/'delivery')` | 决定行与发送结果行两类；发送结果晚产生，以第二行追加，绝不回写 |
| `et_date / ts / tier('P0'..'P3','system') / rule / ticker / text` | 决定的完整事实 |
| `dedupe_key_json / conditions_json` | 去重键（重启重建用）＋ 七条件逐条评估 JSON |
| `lane_rule_id / playbook_ref / option_mid / option_spread_pct / gate_state` | 蓝图 §三(e)-4 字段；near-P0 候选有通过验证的具体合约时写入 Mid/点差，报价标缺时保持空值，不以 0 冒充 |
| `delivery_channel('telegram'/'telegram_silent'/'web_only'/'ledger_only'/'none')` | 四级出口 |
| `suppressed / suppressed_reason` | CHECK：被抑制必带原因（无静默丢弃，仓库层 + 表级双保险） |
| `message_key / telegram_ok` | 同一 Telegram 消息的分组键；delivery 行记 ok/fail |

写入时序：strict-2m 在返回 accepted 及标记窗口已处理之前，原子提交 decision 与可恢复
`telegram_delivery_outbox` 正文；首次外部发送也必须在持久化之后。数据库写入失败或队列满
返回拒绝，回滚本 tick 预留/去重，允许后续 tick 重检，不能先吞掉 crossing。其他普通消息
也在首次发送前持久化；发送后追加 delivery，不回写 decision。opening 有独立硬截止，
不使用跨 checkpoint 的延迟重放。

重启重建（修复原「中途重启最坏重复提示」注明缺陷）：每 ET 日第一个 tick 做一次有界读取——当日 decision 行的去重键全量并入 `seen`；P1-O、P0/P0-X、普通 P1、全局与跨日周预算把成功 delivery 和“已有 Telegram decision、尚无 delivery 结果”的不确定窗口都保守计为已占用。只有明确 `telegram_ok=false` 且没有成功结果时才释放预算。退出/R6 风险提醒只恢复去重、本就不占机会预算。台账不可读 → 拒绝本次观察并在后续 tick 重试，不回退到无持久证据的内存去重。旧双窗确认的最后证据 K 线标识**不**重建；strict-2m 则由版本化 `window_end` 与 30 分钟 episode 去重恢复。

上下文快照（gate/持仓/当日盈亏）：独立 context worker 按 TTL（300s）刷新 `TierContext`，Telegram sender 不再被慢 provider 阻塞。providers 在 `src/services/intraday_alert_context.py`，并由 `api/app.py` 全部接线。持仓/盈亏来自 Journal EpisodeBuild，**不是实时券商状态**；只有 build 的创建时刻与 source cutoff 都属于目标 ET 日才可使用，否则返回标缺，不能把旧 build 的空仓/零盈亏当成今日确证。gate 复用 Journal edge panel 的 `compute_gate` 与 30 分钟日线缓存；行情失败返回明确的 fail-closed 红灯。

## 4. 过程指标 1 与已知限度

下文“P2 仅页面”指普通观察级；显式研究响铃虽使用 Telegram，仍排除在正式 P0/P1
过程指标外。成功回执只证明 Telegram 接收，不证明手机已响或用户已读。

`src/journal/push_metrics.py::compute_push_compliance`，`review_flow.py` 指标 1 消费：

```
推送依从率 = 今日新开回合中「开仓前同日同标的存在已投递 P0/P1 推送」的比例
           = pushed_episodes / total_episodes
```

**「已投递」的按层定义（D8 修正）**：P0/P1 都是 Telegram 层，一条 decision 行只有在同 `message_key` 存在 `telegram_ok=true` 的 delivery 行时才算已投递——decision 行证明「决定发」，delivery 行才证明「送达」；发送失败、delivery 行缺失或无 message_key 的决定一律不计（宁少不虚）。时间关联使用该消息**第一次成功 delivery 的时刻**，不能用更早的 decision 时刻制造“交易前已收到”的假象。P2 是 web-only 观察级，本就不属于本指标。reason 同时携带蓝图口径的反向计数（今日送达确证的 P0/P1 推送 N 条中 M 条同日同标的有跟随新开回合），且 `pushes_followed` 按**回合侧去重**——一笔回合至多满足一条推送（推送按时刻先到先配），同标的 P0+P1 不会因同一笔回合各计一次。标缺规则：台账当日**零 decision 行**（提示器未运行/未启用）、目标日没有可用 EpisodeBuild，或 build 的 source cutoff 尚未覆盖目标日 → 标缺；今日无新开回合也标缺（带推送计数），不再把缺少回合证据算成 P0 100% passed。历史日期 API 把所请求 ET 日期作为 `as_of` 上界，未来的 build/delivery 不得回填过去可见性。P0 passed 率的滚动窗口取数为有界分批回扫（keyset 翻页，最多 8 批），窗口填满或历史扫尽为止；历史不足目标窗口时 reason 如实报告实际窗口条数。**诚实限度（`PUSH_JOIN_LIMITATIONS`，测试锁定）**：机械关联只证明行动相关、不证明因果——无法区分「看到推送后进场」与「本来就要进场」，也不识别跨标的启发；P0 passed 率是机械代理，非 S1 人工 acted/passed/missed 清账（S1 左栏人工清账 + `journal_v2_push_outcomes` 是后续交付）。

同日平仓提醒（P1-b）实现注记：从 XNYS 当日真实 close−30 分钟起，持仓快照确证仍有未平仓时发
一条 P1 静默提醒，正文明确 `same_day_flatten_reminder` 与 close−15 分钟退出期限（正常日 15:30/15:45，13:00 半日市 12:30/12:45）；
快照不可得则以独立的 `same_day_flatten_context_missing` 键落 P3 标缺，不把未知冒充空仓，也不提供“继续持有到收盘/隔夜”
的方向判断；它不会提前消耗真正的 `same_day_flatten_reminder` 键，因此同一进程内上下文稍后恢复且仍有仓位时仍能发送。该提醒不占 P1 机会预算或全局机会提示上限，防止早盘候选把
尾盘风险提醒挤掉。

## 5. API 与前端

- `GET /api/v1/journal/push-ledger/daily?date=`（`api/v1/endpoints/journal_push.py`）：当日全部台账行、正式/研究独立预算、P0 passed 率及 limitations。兼容追加 `delivery_status`，按 `message_key` 关联既有回执/outbox，保留原始 `telegram_ok`。
- `/intraday`「今日提示流」：decision 最新在前，研究明确标“未验证收益”；显示等待、Telegram 已接收、失败或结果未确认。关联失败回执合并到决定，仅无对应决定的失败单独显示。“已受理”或 Telegram 接收不等于手机已响/已读。
- 计划面板（`IntradayPlanPanel` / `intradayPlanChecks.ts`）：B3-① `earningsHardBlock`——只有 `days_to_earnings == 0`（今日财报）时渲染红框硬性警示（`EARNINGS_PREMIUM_HARD_BLOCK_TEXT`，de Silva 引用；只警示不禁止；未来 1–5 天不按旧隔夜逻辑阻断，标缺≠安全）。
- B3-③：`IntradayScanTable` 期权墙 tooltip「主力到期」→「首要到期」（该位 OI 占比最大的到期日，语义不变）；前后端各有负向断言测试（`alertCopyBan.test.ts` / `test_alert_pipeline_copy_never_uses_smart_money_attribution`）锁定「聪明钱/主力/大资金」不回潜（用户自建 Playbook 标题等用户数据不在禁令范围）。

## 6. 偏差与后续（对照蓝图逐条）

- 蓝图表结构建议 `journal_v2_push_events + journal_v2_push_outcomes`：本次只建前者；outcomes（S1 左栏 acted/passed/missed 人工清账）与疲劳仪表的人工 acted 率是后续交付，当前 passed 率为机械代理并如实标注。
- 旧条件⑤摩擦与 `option_mid/option_spread_pct` 列已接通：`IntradayOptionFrictionService` 只在默认两层 warm 扫描完成后、提示器 observe 之前运行；原始链按 ticker + ET 日期做 30s TTL/single-flight，方向性 Call/Put 每次从同一原始链重选，不会串缓存。wall lane 只等待 2.5s，忙时记 `deferred_wall_lane_busy` 且不负缓存，下一 tick 重试。增强只改独立深拷贝，不污染 60s 页面扫描缓存，不延长核心扫描 lease；异常时提示器收到原始副本并按标缺降级。该段记录的是旧 P0 先核验合约才成立的文案；现行严格 2m P0 先成立 underlying 事实，再异步附加期权快照。
- **当前 P0 可达边界**：strict-2m 由独立快时钟扫描完整自选，不受页面 Top 前几名限制；DTE、Regime 和期权摩擦不决定 underlying 信号是否成立。旧两层 warm 的七条件路径已退役。
- 旧 gate 状态接入（历史改进 #10，不是当前 P0 门槛）：`red_lanes` 钩子、`gate_state` 记账和默认
  `default_gate_state_provider` 已接通。0-1DTE 的 P0 必须拿到 Journal edge
  panel 同源 gate 的显式 allow；日线取数失败会得到 fail-closed 红灯，不会
  永久停在“provider 缺失”。2-7DTE 不受 0-1DTE 专属红灯误关，但全车道
  红灯仍会阻断。
- **财报 `clear` 依赖单一生产方的布尔契约（D9）**：`earnings_veto` 的 clear 判定建立在 `intraday_top` 财报临近区块对 `within_blackout` 的布尔承诺上；已加 isinstance 防御——非布尔＝契约漂移 → unknown（未知≠安全）。
- **R6 当前边界**：触线或状态标缺不删除 strict-2m underlying 异常事实；去掉行动性措辞并停止期权补充。§2 的降级规则仅说明已退役七条件实现。
- **Telegram 可靠性边界**：普通消息具备首次发送前持久化的 outbox，明确失败可在最多 6 分钟内跨重启重试，过期终止且补发标延迟。read-timeout 等未知送达不自动重发。opening 不跨 checkpoint 补发，09:35 截止 09:36。确定失败有失败回执；terminal 或过期无回执只能标结果未确认，不能推断手机是否收到。
- **开盘基准重启边界**：精确 20 日开盘 1m 基准目前是单进程内存缓存；异常重启后会立即重建，但不会补发已经错过的 checkpoint。关键窗 09:31-09:35 不应发布或重启，且 09:31 前必须确认健康层 `opening_v2_1m_provider=ok`。如要承诺异常重启后仍不漏第一声，需再加持久化基准。
- **部署边界**：当前调度、进程内去重和快重试按单一 scheduler 进程设计；本机 LaunchAgent 使用单个 uvicorn worker。若以后部署多 worker，必须先加分布式 lease/唯一投递约束，不能直接复制 scheduler。
- **台账 deny trigger 随建表就位（D3）**：`push_models` 注册表级 + metadata 级 after_create 钩子（模式同 `position_snapshot_models`），`Base.metadata.create_all` 与 `init_ledger_schema` 两条建表路径都不留「建表后、首次 repo 调用前」的无保护窗口；对既有无触发器数据库，任一路径的幂等 `CREATE TRIGGER IF NOT EXISTS` 会就地补装。
- 60 分钟同 ticker 同方向合并：per-day 去重已覆盖绝大多数重复；线程式事件族合并未做。
- 配置零新增（`.env.example` 已更新为 `INTRA-0-7/v1` 语义）：预算、期限、
  时窗、流动性阈值与保险丝线均为文档化代码常量，不增加平行开关矩阵。

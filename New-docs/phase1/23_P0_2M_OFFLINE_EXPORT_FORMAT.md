# P0 2m 离线校准原始导出格式

## 1. 用途与边界

`scripts/calibrate_p0_2m.py --raw-export-dir ...` 可以直接从每只股票的
1 分钟和日线 CSV 重建严格 2 分钟校准输入，不需要先手工计算 `coverage`、
`events`、`atr_only_events` 或 `shadow`。此离线模式不会联网拉行情、不会修改配置，
也不会启用 Telegram。

`selection_end_session` 是第 180 个交易日、也就是 holdout 结束并冻结规则的
t0。只有 t0 之前的历史 K 线时，正式 `shadow_sessions` / `shadow` 保持空；脚本
不会从 t0 之前倒切 20 日冒充前瞻数据。若另有 t0 后 20 日历史数据，可以通过
`offline_replay_end_session` 生成 `offline_static_replay` 诊断，但它存放在
provenance 中且 `activation_evidence = false`。即使手工补一个延迟数值，离线重放
仍不能通过 `forward_shadow_20_sessions`。

### 原始采集与显式前瞻登记

同一脚本提供独立入口，采集、校准、登记不会隐式串联；路径示例均位于忽略跟踪的 `data/`。

```bash
python scripts/calibrate_p0_2m.py --preflight-moomoo --raw-export-dir data/p0-2m/run/raw
python scripts/calibrate_p0_2m.py --collect-moomoo --raw-export-dir data/p0-2m/run/raw
python scripts/calibrate_p0_2m.py --raw-export-dir data/p0-2m/run/raw --derived-input-output data/p0-2m/run/input.json --output data/p0-2m/run/calibration.json
python scripts/calibrate_p0_2m.py --start-shadow --input data/p0-2m/run/input.json --restart-reason "修复后重新登记；保留旧轮覆盖失败证据"
```

预检只读额度并实际请求三类标的首末日期的六份 1m 样本，不创建文件或写库，但供应商
历史额度仍可能按请求标的占用。采集首次固定最近已完成 XNYS session，也可显式传
`--selection-end-session YYYY-MM-DD`；续取不能改变终点、清单或规则版本。按最多 20 个
交易日区块串行下载，逐分页检查 04:00–20:00 ET 交易日扫描窗，进入活动窗即停止，不争抢
实时数据。每票完成后原子保存两份 CSV 并登记 hash，重启验证完成票后跳过；不留分片堆积。

采集记录 QFQ、采集时间、分钟/日线价格口径检查和供应商公司行动证据。有 CSV 不等于
可上线。`--recheck-adjustment` 只与离线 `--raw-export-dir` 派生入口一起使用，先校验
原始 manifest 和 CSV hash，再核验保存的因子、分钟/日线价格和成交量口径及计算等价性。
纯共同正乘数（A>0、B=0）在证据完整时可以通过；非零加项、缺因子或无法证明口径仍失败。
跨 180 日成交量基线的拆并股/未知股份单位变化仍保持 shadow；价格尺度不变性不能代替
RVOL 股份单位证明，也不只在离线重写成交量而让实时与回放分叉。
复核保留原检查及版本化 review，不改原始 manifest/CSV；输出使用新文件名，不覆盖旧失败
产物。该证明只针对尺度不变的信号与收益，不等于逐时点绝对价格重建。其依据来自
[Moomoo 历史分页](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html)
和 [复权因子接口](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-rehab.html)，不将调整后的
价格天然视为无未来信息。

逐票明确的复权核验失败属于证据不合格，不是文件结构损坏：该票保留在冻结 69 只及
全部日期分母内，正式 `complete_minutes` 为 0，`observed_complete_minutes` 保留实际
信号口径的完整分钟数；其 strict 与 ATR-only 事件同时退出正式统计，原始数据和排除原因
保留。不得删除失败票改善覆盖率，也不额外要求 69/69 全部通过。缺少逐票核验记录、
身份不一致或文件 hash 损坏仍是全局输入错误。

`--start-shadow` 会写现有 SQLite：仅接受来源、文件 hash、冻结范围及调整证据均完整的
派生输入，使用输入文件 SHA256 作为幂等请求 id，非 RTH 显式登记一次。它不启用 Telegram
或改 action。旧运行保留，不自动重选观察日期；历史结果应先审阅，缺口不能补写。
输入变化后不能借用旧 hash 登记：同日期窗口的显式替代仅在原轮首个开盘前且没有任何
观察时允许，必须给出原因并保留 supersedes 关联。相同请求幂等，并发请求串行形成
关联链而非分叉；已开始或已有观察的同窗运行不得改写。

## 2. 目录布局

```text
raw-p0-export/
├── manifest.json
├── AAPL_1m.csv
├── AAPL_daily.csv
├── TSLA_1m.csv
├── TSLA_daily.csv
└── ... 当前冻结清单中的其余股票
```

文件名可以在 manifest 的 `files` 中逐票覆盖。路径必须留在导出目录内。

## 3. manifest.json

```json
{
  "schema_version": "p0_2m_raw_export_v1",
  "selection_end_session": "2026-08-12",
  "offline_replay_end_session": "2026-09-10",
  "source": "MoomooFetcher",
  "minute_bar_label": "end",
  "minute_timezone": "America/New_York",
  "universe": ["FXI", "TQQQ", "...冻结的全部 69 只..."],
  "files": {
    "TSLA": {
      "minute": "TSLA_1m.csv",
      "daily": "TSLA_daily.csv"
    }
  }
}
```

- `selection_end_session` 必须是 XNYS 交易日。脚本以它为终点锁定最近 180
  个 XNYS session，按 `60 baseline + 80 development + 40 holdout` 切分。
  这个日期就是预注册边界 t0，选择不依赖回测结果。
- `offline_replay_end_session` 可省略。若提供，它必须恰好是 t0 后第 20 个
  XNYS session。脚本会生成每票/每日的行情覆盖和 would-notify 诊断，但只放在
  `producer_metadata.offline_shadow_replay`，绝不写入正式 shadow 输入。
- Moomoo 1m 时间戳是分钟结束标签，所以 `source = MoomooFetcher` 必须配
  `minute_bar_label = end`。其他 start-labelled 导出必须使用非 Moomoo 的
  明确 source，并配 `minute_bar_label = start`。冲突时直接拒绝处理。
- `minute_timezone` 当前只接受 `America/New_York`。CSV 可带 UTC offset；
  不带 offset 时严格按这个 manifest 时区解释。
- 不写 `universe` 时使用代码中冻结的 69 只清单。正式校准不要用子集；子集
  只适合检查导出格式，`fixed_universe_69` 门禁会失败。
- 采集器追加 `frozen_at`、`signal_version`、`universe_hash`、固定交易日清单及
  `collection.symbols` 完成状态；每票 `files` 追加 `minute_sha256` / `daily_sha256`，读取时校验。
- `adjustment` 包含 `autype=QFQ`、`snapshot_at`、`consistency_verified` 和
  `verification_basis`。缺失或未验证仍可生成研究诊断，但 `input_integrity` 不能通过。
- 派生输入分开保留 `development_events` / `development_atr_only_events` 与原有的
  40 日 `events` / `atr_only_events`；开发段不计入上线事件数。最终 artifact 的
  `development` 是诊断，`validated_session_scopes=["regular"]` 仅覆盖 RTH，
  `economic_edge_verified=false`，不宣称已选案例是独立未见样本。

## 4. CSV 列

1m 文件必需列：

```text
date,open,high,low,close,volume
```

可选列：`amount`、`_source_time_key_count`。Moomoo 分页层提供的
`_source_time_key_count` 应保留；值不为 1 的源分钟会使对应 2m 桶失效。
重复、缺失、非连续、非法 OHLC、跨盘段或未完成分钟都不会被补值。

日线文件必需列：

```text
date,high,low,close
```

`date` 使用 `YYYY-MM-DD`。重复或非法日线不参与 ATR。每个事件只使用事件日
之前、最近 140 个日历日内连续完成日线计算 production 同口径的 Wilder
ATR14；缺口会截断连续序列，不会跨缺口拼接。

## 5. 派生口径

- 2m：调用 production 的 `session_anchored_exact_2m_v2` 聚合器。
- baseline coverage：合格票 `complete_minutes = 可用严格 RTH 2m bar 数 × 2`；一对
  源分钟中任一个坏掉，或聚合量不满足生产信号要求，整对都不计完整；复权不合格票正式
  完整量为 0，原始观测完整量单独保留。
- strict event：只输出完整 18m 当前窗、目标日前恰好 60 个 XNYS 交易日内的
  60 个同钟 8m 基线、Wilder ATR14 都可得且四项固定阈值全过的 RTH 事件。
  若这 60 日内的半日市没有目标钟点，基线会少于 60 并 fail closed，不能向
  更早的第 61 个交易日补齐；该口径与线上 `p0_2m_signal_v1` 完全相同。
- ATR-only comparator：同一 18m 窗只应用 `displacement_atr >= 0.5`，不借用
  8m percentile 或 RVOL。
- 20m 方向调整收益：以信号窗结束时 close 为起点，以准确 `E + 20m` 结束的
  2m bar close 为终点；该未来 bar 缺失时保持 `null`，不前填。超过真实收盘时为
  `session_censored`，仍计入事件、频次和预算，不能用次日/盘后价格补齐。
  正常截尾、真实缺数和待成熟分开报告，strict 与 ATR-only 使用同样口径。
- 可选离线 replay availability：只描述每个 symbol/session 的严格 RTH 2m bar
  acquisition coverage，不证明真实通知链路表现，也不参与上线门禁。

## 6. 命令

先保存派生输入，便于审阅每一个事件和缺口：

```bash
python scripts/calibrate_p0_2m.py \
  --raw-export-dir /absolute/path/raw-p0-export \
  --derived-input-output /absolute/path/p0-derived-input.json \
  --output /absolute/path/p0-calibration-artifact.json

# 对已有原始证据做离线复权复核，保留上面的原始产物
python scripts/calibrate_p0_2m.py \
  --raw-export-dir /absolute/path/raw-p0-export --recheck-adjustment \
  --derived-input-output /absolute/path/p0-reviewed-input.json \
  --output /absolute/path/p0-reviewed-calibration.json
```

仅有 bars-only 离线导出时，命令以退出码 `1` 表示“产物生成成功，但上线门禁
未全过”，这不是脚本崩溃。格式或 manifest 错误返回 `2`。真实 20 日前瞻
shadow 和 Telegram 入队证据必须在 t0 后来自运行台账，再以
`shadow_observation_mode = live_forward_shadow` 进入最终校准；禁止把离线重放
改名后当作线上证据。

## 7. 合并真实 live-forward shadow

`src.journal.ledger.p0_2m_shadow_repository.export_p0_2m_live_shadow_rows`
输出的 JSON 使用 `p0_2m_live_shadow_export_v1`。保存该 JSON 后，将它与 t0
冻结的派生输入合并：

通过本脚本登记的新轮以冻结输入文件 SHA256 作为 `run_request_id`。后续合并必须使用
原始 `--input` 文件，并核对登记 hash；换一份历史输入或直接重建 raw 导出不能冒充同一轮。
合并记录保留 `base_input_sha256`，校准时再次核对；旧的非 hash 运行标识维持兼容。
新 hash 运行不能把已合并文件单独作为 `--input` 再次校准，亦不能在已合并文件上套用
另一次导出；必须保留冻结原件和独立现场导出，防止改动历史数据却保留旧 hash 声明。

真实 enrollment 由常驻 monitor 首次登记或上述显式新轮入口现场建立：必须在首个 shadow 日 09:30:00 ET 前
完成；首次尝试若发生在 RTH 会被拒绝并继续等待，收盘后成功建立时从下一个 XNYS
交易日开始。若数据库已有身份匹配的冻结记录，盘中重启会恢复原窗口而不是重新
enrollment。交易日 20:00 ET 起及周末/休市日只会把历史基线
预热目标切到下一个 XNYS 交易日，不会回填或移动 enrollment。每个 RTH endpoint 的现场
写入硬截止为结束后 +75 秒，过期缺口必须原样保留，离线导出不能补成前瞻证据。

```bash
python scripts/calibrate_p0_2m.py \
  --input /absolute/path/p0-derived-input-at-t0.json \
  --live-shadow-input /absolute/path/p0-live-shadow-20d.json \
  --derived-input-output /absolute/path/p0-derived-with-live-shadow.json \
  --output /absolute/path/p0-final-calibration-artifact.json
```

也可以同时使用 `--raw-export-dir` 和 `--live-shadow-input`，但复现正式结果时
应优先保留 t0 当天生成且内容哈希固定的 derived input，避免 20 日后重新导出
历史数据造成输入漂移。

CLI 在覆盖正式 shadow 字段前会拒绝以下情况：

- schema 不是 `p0_2m_live_shadow_export_v1`；
- 缺少 exporter 顶层 `enrollment`，或其中的 observation mode、signal
  version、69 票 universe/hash、固定 sessions 与信封不一致；
- enrollment 的 `enrolled_at` / `created_at` 不是带时区时间、晚于首个
  shadow 开盘，或 `enrolled_at` 没有严格晚于 holdout 收盘；
- 顶层或任一行不是 `live_forward_shadow`；
- signal version 不是当前严格 2m 版本；
- universe 的顺序、成员或 universe hash 与 t0 输入不完全一致；
- shadow 首日不晚于 holdout 末日，或不是由真实 enrollment 时刻唯一确定、内部无跳日的
  20 个连续 XNYS session；shadow 可以晚于 holdout 后第一个交易日开始，但不能任意延后；
- 任一行的 `enrollment_key`、symbol、session、session scope、signal version 或 universe hash
  与导出信封不一致。

覆盖率不足、缺 endpoint、缺 Telegram receipt 或延迟超标不会在合并阶段被
“修好”；这些行原样进入 calibration gate 并使对应门禁失败。live exporter 会把完整
导出 SHA-256 和 exact `enrollment_key` 追加到同一 SQLite 的 export-attestation 表，合并
阶段同时重算摘要、查验 receipt，并重读实际 enrollment 数据库行及其时间边界。事故重跑
必须使用稳定的 `run_request_id`、非空 `restart_reason` 和
`supersedes_enrollment_key`；旧失败运行保持不可变，新旧运行的 observation、导出和
attestation 禁止混合。仅在 JSON 中自报新 key 或伪造一个盘中 enrollment 不能通过。

每个 symbol/session aggregate 还必须显式携带 `telegram_enqueue_evidence_mode`，
受信值只有 `counterfactual_no_send_same_queue` 与 `actual_telegram_queue_receipt`。
前者走真实有界队列但不外发；后者来自研究手机或正式出口的真实队列接受记录。
单一 mode 对应最慢已接受样本，可选 `telegram_enqueue_evidence_modes` 审计全部模式；
混合这两种模式不再被误拒绝，但数据库 enrollment、来源、摘要与回执关联校验保持不变。
字段缺失、未知、离线回放、未接受队列或缺真实时间戳均不能证明延迟门禁，
单独补一个看似合理的秒数不会通过。exporter 本身会写 digest receipt，不是只读诊断。

现有观察表追加可空的 `window_close`；只有完整评价才能记录准确的最终 2m 收盘价，
无须再次触发信号，价格口径保存在 `data_quality.window_close_basis`。旧记录保留 NULL，
不通过迁移补行情。逐票逐日的 `p0_2m_shadow_outcomes_v1` 诊断纳入同一导出摘要及合并绑定：
按同轮、同标的、同 RTH 日准确 t+20m 配对，要求正数有限价格且口径一致。
全体事件按预算前合格信号、方向、30 分钟首次 crossing 去重；`would_notify_events`
单列应通知子集，`notification_events` 仅列有真实 Telegram 队列接受记录的子集，
不把 counterfactual no-send 当实际通知，也不把入队等同于手机送达。
尾盘截尾、待成熟、缺价格分别列明；这些收益仅为诊断，`activation_evidence=false`，
不成为绕开原联合门槛的第二条上线通道。

全部门禁通过后，校准 CLI 还会在同一 SQLite 追加最终 artifact receipt，绑定 exact
`artifact_sha256`、原 export receipt/hash 和 shadow binding；运行时必须查到 exact digest，
所以改写 gate/metrics 后重算 JSON 自哈希仍会失败。校准 CLI、运行时和 exporter 必须使用
同一持久 `DATABASE_PATH`，正式签字时要一起保留 SQLite 与 artifact。该 DB receipt 防止
只篡改 JSON，不是独立密码学签名；具备数据库任意 INSERT 或进程代码执行权限的主体仍在
部署信任边界内。

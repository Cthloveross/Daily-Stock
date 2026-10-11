# 22. 严格 2 分钟 P0 / P0-X 信号与上线校准

> as-of: 2026-10-10 ｜ 本文是当前 P0 的规则真源。`p0_2m_signal_v1`
> 已彻底取代旧 5m `burst_score` 对 P0 的分级、排序和手机发送作用。
> P0 研究子开关默认开启，但实际 monitor 还要求盘中 scheduler 已启用且自选非空；
> 正式 RTH P0 手机出口默认关闭，盘外仅保留研究出口；运行中的研究结果进入 shadow：
> `INTRADAY_P0_2M_ACTION_ENABLED=false`。校准完成前可显式开启独立的
> `INTRADAY_P0_2M_SHADOW_MOBILE_ENABLED`，把严格四条件真实命中以
> “尚未校准、不可据此行动”的 P2 研究观察响铃发送；它不伪装正式 P0，
> 不发期权补充，也不进入正式 P0/P0-X 预算或 passed 率。

## 1. 规则定位与证据边界

这套规则检测的是“相对该股票自身、同一盘段、同一钟点异常大的已实现价格推进，
并且同时有相对成交量和较长窗口位移确认”。它是异常检测合同，不是前瞻收益模型，
也不等于已经证明可以盈利。

旧 `burst_score = 15m 价格推进强度 × 15m 成交量倍数` 是项目内部启发式；乘法
结构及阈值 `8` 均没有论文或机构标准依据。旧字段只为 API 兼容和历史取证保留，
不得再参与 P0 判定、排序、分级或提醒。

文献支持的范围更窄：高频波动与成交量具有明显日内季节性，因此同钟点标准化比
全天统一阈值更合理；同时，更短采样会放大市场微观结构噪音，不能因为改成 2 分钟
就声称自动“过滤了噪音”。参见 [Andersen 与 Bollerslev 1997](https://doi.org/10.1016/S0927-5398%2897%29000004-2)、
[Brownlees、Cipollini 与 Gallo 2011](https://doi.org/10.1093/jjfinec/nbq024)、
[Lee 与 Mykland 2012](https://doi.org/10.1016/j.jeconom.2012.03.001)。这些研究支持
特征族与数据处理方向，不提供本项目的 P95、1.5 倍或 0.5 ATR 阈值；这些阈值是
冻结后等待样本外检验的工程规格。P95 是历史异常分位，不是未来同向运动概率 95%。
[Brownlees 等人的作者稿](https://www.tse-fr.eu/sites/default/files/medias/doc/conf/fbf/gallo.pdf)
研究成交量预测和 VWAP 执行，并不验证本项目的方向收益；
[Gao 等人的日内动量研究](https://www.sciencedirect.com/science/article/pii/S0304405X18301351)
研究开盘半小时与收盘半小时的关系，也不能外推为单票 8m 信号后的 20m 收益。
本项目没有实现 Lee–Mykland 跳跃检验，不属于上述论文策略的复现。

### 1.1 成熟系统的边界

成熟系统需要分别证明检测规则、收益证据和送达可靠性。上述日内季节性研究支持
同钟点基线，但不能证明本项目 P95、1.5 RVOL 或 0.5 ATR 能产生可交易利润。
研究提醒可以报告异常事实；正式 P0 不能因为通知太少而反复调整阈值。
当前系统是日内异常发现器加人工期权复核，不自动下单，也没有已经验证的完整入场、
止损、止盈、仓位和期权净收益模型。0–7DTE 指合约剩余到期天数，不是持仓天数。
[FINRA 15-09](https://www.finra.org/rules-guidance/notices/15-09)支持独立检查、变更记录、
受控试运行、故障监控和快速关闭的工程实践，不为本项目阈值或收益背书。

策略挑选会使回测偏乐观，普通 holdout 本身也不能消除反复试验带来的选择偏差。
[Bailey 等人的回测过拟合研究](https://doi.org/10.21314/JCF.2016.322)因此提示：应保留
候选试验轨迹、冻结阈值并做新的前瞻检验，而不是把历史胜率包装成机构认可。
当前校准只检验标的后续 20m 位移，不证明包含价差、手续费和执行误差的期权收益。
本轮 60/80/40 输出分别包括开发诊断与冻结历史审计；TSLA/META 等已参与挑选的案例
不构成完全未见样本。artifact 显式写入 `economic_edge_verified=false`、
`historical_independence=not_established_preselected_cases` 和
`validated_session_scopes=["regular"]`。通过只表示固定规则和 RTH 检测/投递验收通过；
盘外 P0-X 事实保留，但只能走研究提醒，即使 RTH action 已启用也不能升级。
资产类别后续 20m 中位数非正时保持 shadow，不进入正式手机标的集合。

运行可靠性必须从数据到消息逐段核验，不能从进程存活推断。
[Google SRE 的监控实践](https://sre.google/sre-book/monitoring-distributed-systems/)
强调用户可见症状、低噪音告警及延迟分布。本项目据此将快信号与慢页面任务分开、
先持久化再确认接受、过期停止发送、短暂订阅抖动不响铃，并区分 Telegram 受理
与手机实际响铃。99% 可用率、75 秒硬期限及 60/80/40+20 日切分均是项目验收值，
不是论文或机构通用标准，也不能单独排除回测过拟合。

## 2. 严格 1m → 2m 数据合同

公共历史入口只能先读取原生 1m，再按 `session_anchored_exact_2m_v2` 聚合：

1. 所有时间先转换到 `America/New_York`，输出以 2m K 的起始时刻标记。
2. Moomoo 的 1m 结束标签先减一分钟；已经用起始时刻标记的数据源不移动。
3. 每个 XNYS 交易日分三段独立锚定：盘前 04:00、RTH 官方开盘、盘后官方收盘。
   半日市使用真实收盘；任何 2m 桶不得跨盘段边界。
4. 一根 2m K 必须恰好含两根唯一、连续、合法且已经完成的 1m K。缺分钟、重复、
   非法 OHLCV、尚未完成、跨盘段或交易所日历不可确认时全部 fail closed；不插值，
   不静默去重，也不假设所有交易日都在 16:00 收盘。
5. 计算时只使用 `as_of` 之前已经完成的桶。`window_end` 是右开区间终点，也是手机
   消息显示的“数据时间”；未来行和当前正在形成的分钟不能进入信号或基线。

## 3. 固定公式与四项门槛

对同一盘段内最近九根完整 2m K 计算：

```text
impulse_8m = log(第四根 2m 收盘 / 第一根 2m 开盘)

price_percentile
  = count(过去 60 个匹配交易日的 |impulse_8m| <= 当前 |impulse_8m|) / 60

RVOL_8m
  = 当前四根 2m 总成交量 / 过去 60 个匹配交易日同一 8m 槽总量的中位数

displacement_18m
  = |第九根 2m 收盘 - 第一根 2m 开盘| / 前一交易日 ATR14
```

“匹配交易日”必须是目标日前最近 60 个 XNYS 交易日，并与当前窗口属于相同
`session_scope` 和相同 ET 钟点。单票基线不能借用其他股票，新加入标的在自身 60 日
基线完整前只能是 `baseline_warming/shadow`。

P0/P0-X 只看以下四项：

| 条件 | 通过标准 |
| --- | --- |
| 1. 数据完整 | 严格 2m 当前 18m 窗口完整；恰有 60 个严格早于目标日的同盘段同钟点 8m 基线；版本、来源与数据质量可核验 |
| 2. 8m 价格异常 | `price_percentile >= 0.95` |
| 3. 8m 相对量 | `RVOL_8m >= 1.50` |
| 4. 18m 同向位移 | 8m 与 18m 方向一致，且 `displacement_18m >= 0.50 ATR14` |

四项全过才是 `qualified`。前三项全过、方向一致且 18m 位移在
`[0.30, 0.50) ATR` 时为 `forming`：只显示在网页，不响手机。路径效率、VWAP、
市场残差、Regime、财报、DTE、合约摩擦和旧固定时段均不是这四项中的硬门；这些
研究字段在被样本外验证前不能偷偷升级成条件。

当前研究字段固定放在 `research_fields` 下，逐项携带 `state/value/basis/reason`：
`path_efficiency_8m` 描述 8m 净推进相对逐段路径长度，
`vwap_distance_8m_percent` 仅在同一 8m 窗口有可核验成交额时计算，
`market_residual_8m` 在尚未冻结市场基准与残差模型前固定为 `unavailable`。它们只供
后续研究，不进入四条件真值、预算、分级或排序。

## 4. 盘段、分级与独立开盘分支

- `session_scope=regular` 的严格四条件命中记为 `P0`。
- `session_scope=premarket/afterhours` 的命中记为 `P0X`，手机标题为
  “P0-X 盘外异动”。它只报告正股事实，不声称此时个股期权可执行，也不计入 RTH
  P0 频次。
- 旧 `09:45–15:15` P0 窗口和 0DTE 截止不再决定 underlying 信号是否成立。
  同日平仓仍是用户的交易纪律，但不属于异常检测数学。
- `opening_impulse_v2` 是独立的 P1-O 发现分支：09:31 起逐分钟只读完整 1m，
  09:32–09:34 可以经相邻同向确认提前响铃；此前未确认时，09:35 也只用从 09:30
  累计的五根完整 1m 做最终确认，不再等待或依赖供应商原生 5m。它不会因为出现得
  早而自动升级为 P0。

严格 2m 扫描器每分钟运行一次，但只在出现新的完整 2m `window_end` 时评估；这使
供应商延迟有重试机会，同时不会重复计算同一根未变化的 K。
该快调度在分钟结束后约 5 秒运行，与慢 Top/期权页面预热分开；开盘 09:30–09:36
暂缓慢预热，避免它占用开盘数据链。分离调度不能消除供应商本身的缺数或延迟。

## 5. 发送、去重与期权补充

信号事实与发送预算分开：预算耗尽只把本次发送抑制为网页/台账记录，不得把已成立
的信号改写成“不成立”。

| 出口 | 手机预算 |
| --- | --- |
| RTH `P0` | 最多 2 次/日、7 次/周 |
| 未校准 RTH 研究观察（P2） | 独立最多 2 次/日、7 次/周；不占正式 P0 预算 |
| 盘外 P0X 事实的研究观察（P2） | 独立每盘段最多 1 次/日、盘前盘后合计 5 次/周；没有正式盘外发送出口 |

同盘段、相同标的、相同方向的 30 分钟 episode 只发送第一次 crossing；反向独立记账，
不重置原方向锚点，恰好 30 分钟可形成新 episode。盘前/RTH/盘后各自隔离，盘外研究事件
不得阻挡 RTH 资格，重启恢复使用相同维度。周预算按 ET 当日及前六个自然日计算；
完整自然周仅用于历史频次中位数。去重和发送预算由 append-only 推送台账恢复：明确
`telegram_ok=false` 才释放预算；已持久化 decision 但缺 delivery 的崩溃窗口保守
按已占用处理，避免 Telegram 实际成功后重启超发。decision 本身落库失败时禁止外部
发送。台账重建失败时拒绝本次观察并在后续扫描重试，不回退进程内去重。
全局通知上限仍是最外层降噪护栏，但不属于信号条件。

真实 Telegram 文本先走普通域名请求；发送器在启动时缓存由 DNS 返回的 IPv4，只有明确的
DNS、建连或 connect-timeout 失败才用该地址直连，并继续强制校验 `api.telegram.org` 的
SNI、证书主机名和 HTTP Host。普通 HTTP/API 错误不会走该回退；read-timeout 等可能已被
Telegram 接收的结果记为不确定并停止自动重试，避免重复响铃；过往 DNS 错误的隐式
exception context 不能把本次 read-timeout 误判为可重试。严格 2m 在确认 dispatch
接受前，将 decision 与普通消息 outbox 同事务持久化；提交失败或队列满时拒绝接受，
恢复本 tick 去重/预算，monitor 不推进已完成窗口。worker 复用已提交结果，不重复写入。
其余非 opening 消息也在首次外发前与 decision 同事务写入
`telegram_delivery_outbox`；任一落库失败都禁止外发。明确发送失败后按 15/45/120/120 秒退避且最晚
6 分钟终止；服务在硬截止前重启会恢复原始完整正文和固定截止时间。期间保留预算
reservation，成功后完成，耗尽才释放。延迟补发必须在正文首行注明延迟并保留原数据时间。
即使尚未首次发送，排队超过原始 6 分钟硬期限也终止，不能靠重启或内部请求重试延长。
09:32–09:35 的逐分钟 opening 消息不进入延迟队列，不能跨分钟冒充实时提醒；
09:35 最终确认也必须在 09:36 前开始发送。append-only
delivery 只记录最终成功或确定的终止失败；pending 尝试不提前写 false，不确定结果也不写
false，预算按保守已占用处理。成功回执若短暂写库失败，会从 succeeded outbox 在重启时补齐；
确定失败回执写库成功后才把 outbox 置为 terminal。电脑休眠、关机或整机断网仍不能保证
实时送达。

网页依据 outbox 与回执区分“等待重试”“Telegram 已受理”“已过期/结果未确认”，
后来的成功回执覆盖早先失败展示；受理不等于手机已响或用户已读。订阅不可用或健康
未知立即 fail closed，但连续 60 秒才通知断开；已通知后连续确认健康 30 秒才通知恢复。
短暂抖动不产生断开/恢复通知对，健康计时遇到新故障重新开始。

未校准研究观察沿用相同的标的/方向 30 分钟 episode 去重，并以
`tier=P2`、`rule=p0_2m_shadow_research` 单独写入 decision/delivery。其独立日/周预算
同样从 append-only 台账恢复；正式 P0/P0-X 计数和 `p0_2m_candidate` passed-rate
查询不会读取这些行。

RTH P0 第一条只发 underlying，正文包含方向、触发价、8m/18m 变化、同钟分位、
8m RVOL、前一交易日 ATR14、距常规收盘时间与 `window_end` 数据时间。期权链在
首条入队后异步查询：最多补充三个方向匹配的 0–7DTE 候选，每个到期日最多一个。
无合约、报价陈旧、点差宽、Delta/盘口不可核验或补充任务失败都只显示“暂无报价合格
期权候选”，不能撤销 underlying P0，也不能拖慢第一条。P0-X 不补充盘外期权执行
建议。

报价合格候选不等于可以立即交易。手动执行政策独立于 P0 数学：09:45 开始，普通候选
截至实际收盘前 45 分钟，0DTE 截至收盘前 90 分钟，退出参考为收盘前 15 分钟。
后端以同一 XNYS 日历返回 `execution_policy`，包括实际收盘、各截止时间和准入原因；
前端只展示该结论，缺失、未知或过期均标为不可验证，不自行假设 16:00 收盘。
策略有效期不超过 120 秒且不跨越下一个边界；暂停刷新也不能永久保留旧的“通过”。
R6 未知或触发时，包括开盘分支在内的消息均仅保留标的事实，不展示合约或行动建议。

## 6. 版本化载荷与安全上下文

`p0_signal_evidence_v2` 至少携带：

- `signal_version`、`universe_hash`、`session_scope`、`window_end`；
- `price_percentile`、`rvol_8m`、`displacement_atr`、`baseline_count`；
- `source`、`data_quality`、`option_candidates`；
- 以及方向、触发价、8m/18m 百分比、前一交易日 ATR14、基线日期范围、固定阈值、
  失败条件与限制说明。

财报与 Regime 只作为警告上下文，不改变四项信号事实。R6 风险保险丝同样不删除
信号：触发或状态标缺时，已经成立的 underlying P0/P0-X 仍发送事实型手机消息，
但必须去掉行动性措辞，并且不查询、不发送期权候选；R6 警告随消息展示。R6 只限制
消息可采取的动作语义，不得把四条件命中改写成未命中。

## 7. 固定 69 只与上线门禁

当前清单冻结为 69 只，集合哈希为：

```text
sha256:9c2d3e717a105b7044b7db26a465eda6b9182d1ab69db5c6771c8497518ea59f
```

校准至少需要 180 个有效 XNYS 交易日，固定取最近 180 日并按时间切成前 60 日基线、随后
80 日开发集、最后 40 日 locked holdout；holdout 后另做 20 个交易日前瞻 shadow。
股票、普通 ETF、杠杆/反向 ETF 分层出报告。分类未知属于全局输入错误，会使 artifact
失败；单票基线不足只限制该票，不要求类别内每票全过。类别的成熟 holdout 表现或 20 日 shadow 不完整时，该类别
保持 `state=shadow`，不会连带否定证据完整的类别。artifact 显式输出
`mobile_eligible_symbols`，只有基线就绪且所属类别为 `validated` 的标的可以进入手机
出口；其余标的继续只进网页/台账。

只有全部门禁显式通过，`activation_ready` 才为 `true`：

- 输入、版本、标的集合和无未来函数证据完整；固定 69 只与 `60/80/40` 切分一致。
- 至少 56/69 只各自具有完整 60 日基线，合格分钟完整率不低于 98%。
- 逐票复权核验失败保留在 69 只分母内并退出正式事件及 ATR-only 对照；明确失败不会额外
  变成 69/69 全通过硬门，缺失核验记录或文件 hash 损坏仍阻断输入。
- holdout 至少 30 个去重 RTH 事件、覆盖至少 20 个独立 RTH 交易日。
- 预算前完整周事件数中位数在 3–7；预算抑制率不超过 20%；单票占比不超过 20%。
- 可评价 holdout 事件的 20m 方向调整收益中位数大于 0；收盘不足 20m 的事件明确为
  `session_censored`，保留事件、频次和预算统计，不借次日价格补齐，不禁止尾盘信号。
  正常截尾与真正缺行情、尚未到评价时间分开计数，strict 与 ATR-only 使用相同口径。
- 相对“仅 0.5 ATR”比较器的按日聚类 bootstrap 90% 区间下界不低于 0，并覆盖
  至少 20 个聚类交易日。
- 后置 shadow 恰有 20 个交易日且每天有 RTH 观察；重复通知和未完成 K 线触发均为
  0；数据可用率不低于 99%。
- 每个应通知 shadow 事件都有延迟样本，延迟非负，完整 2m 结束到 Telegram 入队的
  最大值不超过 75 秒。
- 所有标的资产类别已知；每类都显式报告 `validated` 或 `shadow`，手机范围必须严格
  等于 artifact 的 `mobile_eligible_symbols`，不得把 shadow 类别混入。

任一门禁失败或证据缺失时，旧 5m P0 仍保持退役，新 2m 结果只进网页/台账；不能
为了达到每周五次而自动放宽 0.5 ATR、改 holdout 或事后挑阈值。

## 8. 离线校准与启用

校准器既可接收已经派生的 JSON envelope / 带 `kind` 的 JSONL，也可用
`--raw-export-dir` 从逐票 1m 与日线 CSV 生成审计输入；输入 schema 为
`p0_2m_calibration_input_v1`，核心集合包括 `coverage`、`events`、
`atr_only_events`、`shadow`，以及 `historical_sessions`、`shadow_sessions`、
冻结 universe 和可选资产类别覆盖。raw 模式把 `selection_end_session` 固定为
第 180 日（holdout 末日 t0）；之后已有历史只能记为 `offline_static_replay` 诊断，
正式 shadow 必须来自 t0 后 20 日、明确标记为 `live_forward_shadow` 的运行遥测。
命令做离线统计，不抓行情、不修改 `.env`、不打开 Telegram；成功时会追加数据库
activation receipt，因此不是只读诊断，即使未指定 artifact 输出路径也不应当作只读探针：

```bash
python scripts/calibrate_p0_2m.py \
  --input /absolute/path/to/p0_2m_export.json \
  --output /absolute/path/to/p0_2m_activation.json

# 或先从原始 CSV 生成可审计的派生输入
python scripts/calibrate_p0_2m.py \
  --raw-export-dir /absolute/path/to/raw_export \
  --derived-input-output /absolute/path/to/p0_2m_input.json \
  --output /absolute/path/to/p0_2m_activation.json
```

原始 manifest、CSV 字段和 t0 语义见
`New-docs/phase1/23_P0_2M_OFFLINE_EXPORT_FORMAT.md`。

退出码 `0` 表示全部门禁通过，`1` 表示 artifact 已生成但仍有失败门禁，`2` 表示
输入或写入错误。先人工审阅 `gate_results`、分层报告和 `artifact_sha256`；只有通过
的 artifact 才能配置：

```env
INTRADAY_P0_2M_ENABLED=true
INTRADAY_P0_2M_ACTION_ENABLED=true
INTRADAY_P0_2M_ACTIVATION_ARTIFACT=/absolute/path/to/p0_2m_activation.json
INTRADAY_P0_2M_SHADOW_MOBILE_ENABLED=false
INTRADAY_REFRESH_INTERVAL_SECONDS=60
```

运行时会再次核对 schema、`signal_version`、当前清单 `universe_hash`、69 只内容、
全部全局 gate、资产分层状态、`mobile_eligible_symbols` 和 `activation_ready=true`。
此外，live exporter 会在同一 SQLite 中追加 export digest receipt；全部 gate 通过时，
校准 CLI 会再追加绑定 exact `artifact_sha256`、export receipt 与 shadow binding 的最终
artifact receipt。运行时必须在同一持久 `DATABASE_PATH` 找到并逐字段匹配该记录。因而
手改 JSON 后重算自哈希、只复制 artifact 不复制数据库、单独打开 action 开关、替换清单或
提供失败/不可读 artifact 都不会绕过门禁。回退时只需将
`INTRADAY_P0_2M_ACTION_ENABLED=false` 并重启；研究扫描和不可变台账仍可保留。

在正式 artifact 尚未通过、但希望手机收到严格四条件研究事实时，可保持 action 为
`false` 并单独设 `INTRADAY_P0_2M_SHADOW_MOBILE_ENABLED=true`。该出口要求 Telegram
凭据；健康层会明确区分“未校准研究响铃已启用”和“正式 P0 仍为 shadow”。

截至本文 as-of，已取得冻结 69 只、2026-01-23–2026-10-09 共 180 个交易日的原始导出，
保存在忽略跟踪的 `data/p0-2m/2026-10-09/`，仍无通过 artifact。原 `input.json` /
`calibration.json` 保留，纠错后另存 `input-reviewed.json` / `calibration-reviewed.json`。
原基线资格 32/69，复核后为 58/69，已达到 56/69 门槛，未删除任何失败标的：BITO 的
非零加项、NVDA/MSFT/NOW 的分钟与日线价格不一致、DELL/RBLX/FNGS 的非法分钟量价，
以及四只新标的早期缺数仍列出原因并保持 shadow。

纠错后的冻结历史审计：开发段 2026-04-21–2026-08-13 有 700 个去重事件，预算预计
发送 112、抑制率 84%，完整周中位数 41，683 个可评价 20m 收益中位数 -0.05348%，
另 17 个为正常收盘截尾。后 40 日 2026-08-14–2026-10-09 有 183 个事件、36 个独立
交易日，预算预计发送 56、抑制 127（69.40%），完整周事件中位数 21.5；178 个可评价
20m 方向调整收益中位数 -0.001070%，另 5 个正常收盘截尾，真正缺行情/待成熟均为 0。
相对 ATR-only 的按日聚类 90% 区间下界 -0.14058 个百分点（4,000 次，seed 42）。
股票类 159 事件、中位数 -0.03730%；普通 ETF 20 事件、+0.11059%；杠杆/反向 ETF
4 事件、+0.29130%，均未通过完整前瞻，不以较小类别的正结果掩盖整体失败。
原结果为 106 事件、周中位数 12、抑制率 50.94%、收益中位数 -0.0229%；本次差异来自
可核验标的恢复和评价口径纠错，没有调整 P95、1.5 RVOL 或 0.5 ATR。覆盖率改善不等于
盈利证据：频次、预算、正收益及比较区间门槛仍未通过，不能只等 20 日就启用。
原始 TSLA/META CSV 三例各 195 个 RTH 端点实时/离线一致：TSLA 8/31 首次 09:52，
META 9/1 无严格命中，META 9/10 首次 09:52 向下；TSLA 的 09:48 旧结论不成立。

旧前瞻 2026-09-14–2026-10-09 共 20 个交易日已经结束：应有 269,100 个逐票 RTH endpoint，
实际观察 192,372 个（71.5%），完整评估 164,536 个，数据可用率上界只有 61.1%，
不可能达到 99%。这是遥测覆盖诊断，不是收益回测；缺失数据不能推断成“没有机会”。
电脑休眠造成实际扫描中断。旧运行保留为失败证据，2026-10-10 ET 已在首个开盘前、
零观察条件下显式替代同日期新轮 2026-10-12–2026-11-06，保留原登记及 supersedes 关联。
新轮绑定 `input-reviewed.json` 的 SHA256
`fdf63b70c5b3587ace5cccd3ffe444809b2ee9ede98ee6d81c6b5f0637d823f8`；
旧 `input.json` 的 `49d698…` 登记不能用于这次输入，不能回填旧分钟或混用新旧数据。
新轮用于实际采集及发送验收，即使链路通过也不能消除上述历史门槛失败。因此生产配置必须继续
保持 `INTRADAY_P0_2M_ACTION_ENABLED=false`；单元测试 fixture 或历史重放结果不能代替
这份前瞻上线证据。

## 9. 真实前瞻 shadow 遥测

FastAPI 启动 strict-2m monitor 时，先在独立表
`journal_v2_p0_2m_shadow_enrollments` 冻结完整 69 只、`universe_hash`、`signal_version` 与从
启动边界开始的连续 20 个 XNYS session。首次 enrollment 在开盘前完成时可纳入当天；若
首次尝试发生在 RTH，仓库会拒绝写入，monitor 只在后续扫描重试，直到收盘后才冻结以下一
个 XNYS 交易日为首日的完整窗口，不能把半天运行事后标成完整前瞻。已有且身份匹配的冻结
enrollment 则可在盘中重启时直接恢复，不会另建窗口。`enrolled_at` 与 `created_at` 必须
接近真实写入时刻，且必须在首个 shadow 日 09:30:00 ET 之前完成，之后不能
UPDATE/DELETE。

供应商故障等事故使整轮 shadow 不可用时，不删除、不修补旧 enrollment 和 observations。
运维必须在下一个可用交易日开盘前显式调用
`start_new_p0_2m_live_shadow_run`，提供稳定的 `restart_request_id` 和可审计的
`restart_reason`；新 enrollment 会通过 `supersedes_enrollment_key` 指向上一轮。
`enrollment_key` 是实际运行身份，并进入每条 observation 的唯一键、导出信封、逐行数据和
SQLite export attestation；默认 monitor 只续写最新一轮，校准器拒绝混合运行、伪造运行身份
或与登记时间不一致的延迟 20 日窗口。旧 schema 迁移时会保留
`*_pre_run_identity` 原表及其 UPDATE/DELETE deny trigger，并在逐行内容核对通过后才启用
新表，因而事故记录仍可审计，但不能占用新运行的 endpoint 唯一键。

历史基线预热与 enrollment 是两件事：交易日 20:00 ET 起，以及周末/休市日，monitor 会把
预热目标切到下一个 XNYS 交易日，避免下次开盘前重新下载完整 69 只历史；这不会回填遥测，
也不会改变已经冻结的 20 日窗口。线上预热只服务“前 60 个 XNYS 交易日”的信号基线，
因此按交易所日历动态计算最小分钟下载区间；离线校准所需的 60/80/40 共 180 日不在每次
服务重启时重复下载。所有 Moomoo 历史 K 线 RPC 在进程内单并发执行：首分页至少相隔
0.6 秒、同票续页至少相隔 0.05 秒。OpenD TCP 不可达或共享断路器打开时，当前批次立即
停止，已 ready 标的保留，剩余标的 fail closed 并在供应商恢复后的受控重试中继续。
健康字段 `history_loaded_count` 仅表示历史缓存已载入；`ready_count` 要求当前钟点拥有
最近 60 个 XNYS 交易日的完整 8m 基线并且实时订阅可用。缺失部分槽时保留已有合法缓存，
同票同目标日最多下载 3 次，重试至少间隔 60 秒；耗尽记入
`baseline_retry_exhausted_count`。半日市使某些下午槽只有 59 个合法样本时，保持不可用，
不借第 61 日、不用别票基线。18m 尚未形成的开盘阶段保持 structural pending。
`scan_window_active=false` 表示明确休市或 04:00–20:00 ET 窗口外，此时当前槽
`ready_count=0` 是预期，不等于历史下载失败；缓存仍可预热下一交易日。
日历无法确认时该字段为 `null`，不能假装休市。各盘段前 18 分钟基线就绪看首个
合法 18m endpoint（如 04:18、09:48、正常日盘后 16:18），信号本身仍是 pending；
跨盘段前两分钟尚无新完成桶时，健康核验上一已完成槽。
健康另检查快信号 scheduler 的运行状态，以及最近完成扫描的龄期；活动窗超过 120 秒
报异常，首次扫描有 120 秒宽限，非活动窗不误报陈旧。75 秒通知入队线不因此放宽。
前瞻运行暴露起止日、waiting/collecting/completed/unknown、登记错误及最近记录成功/失败；
读登记失败清除旧就绪结论，已结束运行停止越界写入，最后收盘 endpoint 的合法写入宽限保留。
这些遥测错误不阻断研究消息，但必须在健康面板可见。新轮只能显式登记，保留旧轮失败证据。
开盘 1m/readiness/预热与全天 monitor 复用同一冻结自选；缓存容纳完整清单，取消旧 20 只截断。
盘前预热 20 日开盘基线和日线，开盘关键窗只读订阅缓存，不逐票临时查询历史行情。
09:30–09:36 普通页面的旧 5m 路径也只读已有缓存；缺失时显示未就绪，不临时发起历史请求。
单只股票读取或计算失败只降级该票，其余自选继续扫描；09:36 起恢复非关键窗的正常取数。
57,600 根原生 1m 行的严格聚合使用 positional iterator 和 ISO 时间快路径，避免全量
DataFrame record-dict 物化占用 GIL；常驻参考索引只保存信号实际消费的 7 个字段，扫描时
才恢复当前四个同钟槽的最多 240 行。该优化不改变重复、缺分钟、OHLC、完成时间或盘段
合同，也不减少任意钟点所需的 60 日基线。

每个 RTH endpoint 只在结束后 0–75 秒（含）的现场窗口内采集；超过 +75 秒就是硬截止，
不会事后补写。现场证据分三类追加到
`journal_v2_p0_2m_shadow_observations`：`endpoint_observation` 保存最初现场状态，
`completed_evaluation` 保存随后可用的完整评估，`notification_attempt` 按独立 message id
保存每次队列尝试。前两类各有稳定唯一身份，attempt 身份额外包含 message id；因此 +5 秒
缺数、+65 秒数据完整时可以新增评估而不改写首次缺数，重启重试的第二次入队也不会被
endpoint 去重吞掉。两张表都有 SQLite UPDATE/DELETE deny trigger；写入失败只记日志，
不能阻塞信号链。

数据可用性不只看 `observed_at >= window_end`。遥测另存 `max_component_end`，要求最新
component 确实结束于目标 endpoint 且不晚于扫描开始；开盘前 16 分钟的 structural
pending 还必须从 XNYS open 到目标 endpoint 每根严格 2m 都完整、唯一、合法。缺分钟、
重复、非法 OHLC 或 future component 都不能计为 available。收盘 endpoint 最多允许
+5/+65 秒两次现场探测，75 秒后盘后扫描不会继续制造 16:00 RTH 记录。

action 与研究手机出口均关闭时，qualified 首次 crossing 会按真实 P0 正文走同一消息装配函数，并作为
`counterfactual_no_send_same_queue` 放入同一个有界 dispatch queue。worker 识别 probe 后
绝不调用 Telegram sender、绝不写 delivery、绝不占真实 P0/全局预算；underlying 的
web-only decision 仍正常写 push ledger，便于页面与重启去重。研究手机出口或正式 action
启用时，真实接受的 Telegram 工作使用 `actual_telegram_queue_receipt`。校准器明确接受
这两种受信模式；aggregate 的单一 mode 对应最慢已接受样本，并可通过
`telegram_enqueue_evidence_modes` 审计混合集合。布尔 observer、通用网页队列或任意第三种 mode 都不能
证明 Telegram 入队 SLA。

`duplicate_notification_count` 只从 append-only `notification_attempt` 中同标的、同方向、
30 分钟 episode 内超过首次的**实际已接受入队**计算，不从 endpoint 或常量推断。qualified
评估缺逐票 decision、attempt 与评估无法关联，或 `would_notify` 没有已接受回执时，相关
字段/延迟保持缺失并让校准失败。

在恰好 20 个指定 XNYS 日期采集完成后，可由导出函数生成校准器需要的 `shadow`
rows，并在数据库追加 export digest receipt；它不是只读诊断：

```python
from src.journal.ledger.p0_2m_shadow_repository import (
    export_p0_2m_live_shadow_rows,
)

payload = export_p0_2m_live_shadow_rows(
    session_dates=twenty_xnys_dates,
    symbols=frozen_69_symbols,
    universe_hash=frozen_universe_hash,
)
```

函数拒绝调用方另选日期，`session_dates` 和 `symbols` 必须逐项等于冻结 enrollment；导出
顶层保留完整 `enrollment` 对象、`enrollment_started_at`、`expected_first_session`、
`exported_at` 与 `shadow_observation_mode`。它按交易所真实开收盘生成期望 endpoint：正常日
195 个、半日市 105 个；69×20 的
每个 symbol/day 都输出一行。完全缺失的股票或日期、单个缺窗口、数据不可用、评估不可用、
未完成 K 线触发、缺分级回执和缺延迟分别保留完整列表/计数，不能被均值掩盖。该导出只读
现场账本并固定声明 `live_forward_shadow`；离线 CSV、历史 replay、回测时钟或事后补算
均没有写入该账本的入口，不能冒充这 20 日证据。

## 10. 60 日最终规格回放锁

最终回归固定使用本文件的 60 日同钟基线、log 8m impulse、P95、RVOL 1.5 与
同向 18m/0.5 ATR，不混用早期 20 日探索结果：

| 样本 | 最终规格结果 | 关键读数 |
| --- | --- | --- |
| TSLA 2026-08-31 09:48 | **该窗口不合格** | 价格分位 P91.67、RVOL 1.643、位移 0.836 ATR；只差 P95。早期“09:48 命中”来自 20 日基线，不能当作 60 日结果 |
| TSLA 2026-08-31 全天 | **09:52 首次 P0 向上合格** | 价格分位 P96.67、RVOL 2.135、位移 0.545 ATR；因此正确结论不是“全天无命中”，而是比旧口径晚 4 分钟 |
| META 2026-09-01 | **全天无严格命中** | RTH 最大绝对位移 0.481 ATR，未到 0.5 |
| META 2026-09-10 09:52 | **P0 向下合格** | 价格分位 P96.67、RVOL 2.343、位移 0.754 ATR |

这些案例由 `tests/fixtures/p0_2m_60d_regression_cases.json` 固定。该锁只证明实现
没有悄悄改变口径，不证明阈值有前瞻收益；也不能用改短基线的方式让 TSLA 样本
事后“重新通过”。

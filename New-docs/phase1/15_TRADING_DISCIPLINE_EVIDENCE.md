# 交易纪律证据页与期权墙逐日快照（2026-08-04）

> 状态：已实现（2026-08-04）
>
> 真源顺序：可执行代码 > 本文 > 对话结论
>
> 目标：把散落在对话里的取证数字变成**后端可复算的读数**，并为两个当前
> 无法检验的期权假设开始积累样本。

本文覆盖三件互相关联的事：

1. `/rules`「交易纪律」页 —— 每条纪律和它的证据放在同一处；
2. 期权墙的 call/put 比例 —— 事实描述，**不是**方向信号；
3. `option_wall_daily_snapshots` —— 让「高 call 比例是不是意味着上涨」
   这类问题在几个月后**可以被回答**。

---

## 1. 为什么需要这一页

用户手上有一批只在对话里出现过的取证结论：合约价格甜蜜区、DTE × 持有方式、
时段、星期 × 0DTE 可用性、手续费门槛。规则写进了 Playbook，但**支撑规则的
数字没有固定落点**——每次都要重新口算，而一旦被前端硬编码就会与口径漂移。

`/rules` 把两个来源合到一个页面：

| 来源 | 端点 | 内容 |
|---|---|---|
| Playbook | `GET /api/v1/journal/v2/playbook` | 规则原文 + 候选/已采纳状态（本页只读，晋升与退役仍在 `/journal`） |
| 证据表 | `GET /api/v1/journal/v2/rules-evidence` | 支撑这些规则的全部数字 |

**前端不做任何统计**。分档边界、剔尾 N、严重度分位数定义、费用计算器的默认
锚点全部由响应下发；页面里唯一的算术是费用计算器，它只把后端给的费率乘以
用户当场输入的两个数。

---

## 2. 口径：与车道遵守度完全同一份定义

`src/journal/rules_evidence.py` 复用 `personal_edge` 的干净口径常量与
`classify_rule_lane`，不新增平行实现。样本 ＝ 同时满足：

- `lifecycle_status == 'closed'` 且 `realized_pnl_net` 已知；
- `evidence_summary_json.fill_allocations > 0`（由明细成交构建）；
- `opening_cash_flow` 已知且非 0，风险金额 ＝ `ABS(opening_cash_flow)`；
- ET 入场日 ≥ `RULE_COMPLIANCE_CLEAN_BASIS_START`（2026-04-21）。

**毛口径 ＝ `realized_pnl_net + total_fee`**。每美元读数一律为
「合计金额 / 合计风险金额」的**金额加权**，不是逐笔平均——两者会显著不同，
混用会得出互相矛盾的结论。

### 2.1 build 解析：一个必须显式的现实

默认解析走 `_latest_build`（与其余 journal 读数完全相同）。但本仓库**当前
没有任何 activation 记录**，该解析因此回落到最新的 **CSV build #1**，而用户
手上那批取证数字出自 **canonical build #3**——两者样本量与区间都不同：

| build | 来源 | 样本 n | 区间 |
|---|---|---|---|
| #1 | CSV | 1,191 | 2026-04-21 → 07-20 |
| #3 | canonical | 1,407 | 2026-04-21 → 07-31 |

因此端点接受显式 `build_id`，响应**始终回显** `build_id`/`build_key`，
页面把「这页在读哪个 build」显示出来。**绝不静默替换**。

---

## 3. 证据表与真实读数（build #3，n=1,407）

### 3.1 合约价格甜蜜区（`average_entry_price`，边界 `[lower, upper)`）

| 分档 | n | 费用占权利金 | 毛口径 | 净口径 |
|---|---|---|---|---|
| <$1 | 72 | 5.56% | −8.69% | **−14.25%** |
| $1-2 | 365 | 2.20% | +0.93% | **−1.28%** |
| $2-4 | 544 | 1.23% | +4.35% | +3.12% |
| $4-8 | 208 | 0.61% | +4.05% | +3.44% |
| >$8 | 218 | 0.18% | −0.77% | −0.96% |

结论：**同样金额买便宜合约＝张数多＝手续费按张收**。$1-2 档毛口径本来是
正的（+0.93%），是费用把它翻成了负的（−1.28%）；$1 以下净 −14%。

### 3.2 DTE × 持有方式（毛口径）

持有方式按 ET 交易日判定：平仓日 == 开仓日为「当日平」，否则「过夜」。

| 车道 | n | 毛口径 | 胜率 | 剔除最好 5 笔 |
|---|---|---|---|---|
| 0DTE 当日平 | 556 | +3.44% | 32.6% | +0.41% |
| 1-3DTE 当日平 | 469 | −2.94% | 25.6% | −6.45% |
| 4-7DTE 当日平 | 57 | −4.04% | 22.8% | −10.22% |
| **4-7DTE 过夜** | 65 | **+34.13%** | **61.5%** | **+19.13%** |

两条车道：0DTE 当日平、4-7DTE 过夜。**买了时间却当日平掉**（4-7DTE 当日平
−4.04%）是最贵的一种做法。

### 3.3 时段（ET 小时，毛口径 vs 过路费）

| ET | n | 毛口径 | 过路费 |
|---|---|---|---|
| 09:00 | 321 | +0.14% | 1.32% |
| 10:00 | 434 | +2.68% | 1.43% |
| 11:00 | 254 | −1.05% | 1.18% |
| 12:00 | 165 | +4.71% | 1.33% |
| 13:00 | 130 | −4.08% | 1.01% |
| 14:00 | 75 | +9.15% | 0.98% |
| 15:00 | 28 | +25.52% | 0.55% |

### 3.4 星期 × 0DTE 可用性（毛口径；**排除缺 DTE 的回合**）

缺 DTE 无法判定车道，混入会让「0DTE 可用性」这个因读错。

| 星期 | n | 毛口径 | 0DTE 笔数 | 1-3DTE 笔数 |
|---|---|---|---|---|
| 周一 | 263 | **+5.61%** | 141 | 50 |
| 周二 | 271 | **−2.61%** | 36 | 206 |
| 周三 | 259 | **+7.99%** | 118 | 114 |
| 周四 | 313 | **−3.74%** | 52 | 213 |
| 周五 | 284 | **+3.29%** | 209 | 19 |

这与 `src/opportunities/lane_availability.py` 的判定层同源：亏损的两天正是
**没有 0DTE 可用**的两天（周二 36 笔 vs 1-3DTE 206 笔）。

### 3.5 手续费门槛

全样本恒定费率 **1.27%** 占权利金（n=1,407）。页面用一个两输入计算器
（单笔金额、每天笔数）把它换算成用户自己的月度过路费。**输入只在组件
state 里**，不落库、不上报；系统既不知道也不猜账户规模。

### 3.6 仓位与回撤：「你选择的参数 + 它们的含义」

回显用户自己选定的参数（$3,000/笔、−$6,000 日熔断、最多 2 个并发），
**不是推荐值**。派生算术：

| 亏损档 | 定义 | 单笔亏损 | 触发熔断所需笔数 |
|---|---|---|---|
| 归零 | 亏掉全部权利金 | −100% | 2.0 笔 |
| 严重亏损 | 全样本单笔净回报 p5 | −61.86% | 3.2 笔 |
| 平庸亏损 | 全样本单笔净回报 p25 | −33.55% | 6.0 笔 |

- **样本内触发频率**：70 个交易日中 22 天会触发（约每 3.2 个交易日 1 次）。
  这个频率**绑定的是历史下单节奏**（每日中位 20 笔）；「最多 2 个并发」会
  大幅降低每日笔数，因此该数字不能直接搬到新规则下。
- **累计最大回撤**：本样本这一条真实历史路径、按 10% 比例仓位复利为
  **−92.12%**。它是**一条已实现路径**，不是模拟分布的中位数，也不是对未来
  回撤的估计。排序按平仓时刻（权益真正变化的时点）并以开仓时刻与标的做
  确定性 tie-break——乘法路径的最大回撤对顺序敏感。
- 页面明确写出：**每日熔断与累计回撤是两件不同的事**。日熔断限制的是单日
  已实现亏损，累计回撤是多日连续下滑的叠加。

### 3.7 相关性提醒

合规车道（`intraday_0dte` + `overnight_4_7`，n=532）内的高频标的：

TSLA(109) / NVDA(62) / MU(57) / MSFT(55) / QQQ(46) / AMZN(42)

它们同属一个高相关簇。**同时持有 2 个 ≈ 一个双倍仓位**，日熔断额度会被一波
行情同时吃掉。

### 3.8 过夜跳空

每日熔断保护不了过夜仓位：熔断在盘中按已实现亏损触发，而跳空发生在开盘
竞价，止损单无法在跳空之间成交。

---

## 4. call/put 比例：事实，不是信号

`POST /opportunities/option-walls` 升到 **option-wall/1.3**，additive 新增：

| 字段 | 含义 |
|---|---|
| `totals` | `call_oi` / `put_oi` / `call_volume` / `put_volume` 窗口合计 |
| `ratios.call_put_oi_ratio` | OI 比例；`metric_basis = settled_open_interest_prior_session` |
| `ratios.call_put_volume_ratio` | 成交量比例；`metric_basis = current_session_cumulative_volume` |
| `ratios.caveat` | 标准告警文案，前端逐字渲染 |
| `oi_weighted_center` | `Σ(strike × OI) / Σ(OI)`，**不是** max pain |

**分母为 0 时 `value` 为 `null` 并附 `reason`**，绝不以 0、1 或无穷大冒充一个
「有定义」的比例；两侧合计仍如实给出，读的人能看出为什么无定义。

### 4.1 诚实要求（这是本次改动最重要的部分）

用户问了两个问题：「近期的高 call 比例是不是意味着上涨？」与「一个票今天
最可能向哪个点靠近？」。

**本仓库没有任何历史 OI 序列**，因此 call/put 比例预测方向、以及最大痛点 /
gamma 钉仓有引力，这两个假设在用户自己的数据上**一次都没有被检验过**。
所以：

- 比例与墙位一律作为**事实**呈现（现在盘口上摆着什么），绝不作为方向信号；
- 标准告警随响应下发并逐字渲染，同时进入 `limitations`：

  > call/put 比例与墙位是当前持仓与成交的事实描述；本仓库尚无历史 OI 序列，
  > 因此「高 call 比例＝会涨」「价格会向最大痛点靠拢」这两个说法在你的数据上
  > 都**尚未被检验**。系统正在逐日记录，样本足够后会给出回溯频率。

- **不计算、不展示 max pain 预测**。唯一相关的读数被严格标注为
  「未平仓分布的加权中心（描述，未验证是否有引力）」，并带机器可读的
  `validated_as_price_magnet: false`。

前端 `OptionWallRatioPanel` 同时挂在 `/regime` 的机会列表与
`/regime/opportunity/:ticker` 的「期权墙」标签页上。

---

## 5. 逐日快照：让问题在几个月后可被回答

### 5.1 表

`option_wall_daily_snapshots`，键 `(market_date_et, ticker)`，
定义在 `src/opportunities/wall_snapshot_models.py`。

```sql
CREATE TABLE option_wall_daily_snapshots (
    id INTEGER NOT NULL,
    snapshot_key VARCHAR(128) NOT NULL,
    market_date_et DATE NOT NULL,
    ticker VARCHAR(32) NOT NULL,
    spot FLOAT NOT NULL,
    call_oi_total FLOAT NOT NULL,
    put_oi_total FLOAT NOT NULL,
    call_volume_total FLOAT NOT NULL,
    put_volume_total FLOAT NOT NULL,
    call_put_oi_ratio FLOAT,
    call_put_oi_ratio_reason VARCHAR(255),
    call_put_volume_ratio FLOAT,
    call_put_volume_ratio_reason VARCHAR(255),
    top_call_wall_strike FLOAT,
    top_call_wall_oi FLOAT,
    top_put_wall_strike FLOAT,
    top_put_wall_oi FLOAT,
    gross_gamma_concentration_strike FLOAT,
    oi_weighted_center_strike FLOAT,
    coverage_percent FLOAT NOT NULL,
    source VARCHAR(64) NOT NULL,
    formula_version VARCHAR(64) NOT NULL,
    quote_as_of VARCHAR(64),
    fetched_at DATETIME NOT NULL,
    recorded_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_option_wall_daily_snapshot_key UNIQUE (snapshot_key),
    CONSTRAINT uq_option_wall_daily_snapshot_slot UNIQUE (market_date_et, ticker),
    CONSTRAINT ck_option_wall_daily_snapshot_spot CHECK (spot > 0),
    CONSTRAINT ck_option_wall_daily_snapshot_coverage
        CHECK (coverage_percent >= 0 AND coverage_percent <= 100),
    CONSTRAINT ck_option_wall_daily_snapshot_totals
        CHECK (call_oi_total >= 0 AND put_oi_total >= 0
               AND call_volume_total >= 0 AND put_volume_total >= 0),
    -- 比例要么有值、要么有原因，不允许「两个都空」这种静默缺失
    CONSTRAINT ck_option_wall_daily_snapshot_oi_ratio_explained
        CHECK ((call_put_oi_ratio IS NOT NULL) OR (call_put_oi_ratio_reason IS NOT NULL)),
    CONSTRAINT ck_option_wall_daily_snapshot_volume_ratio_explained
        CHECK ((call_put_volume_ratio IS NOT NULL) OR (call_put_volume_ratio_reason IS NOT NULL))
);
```

**append-only**，与 journal_v2 / opportunity_* 家族同一套 deny trigger：

```
trg_option_wall_daily_snapshots_update_immutable
trg_option_wall_daily_snapshots_delete_immutable
-- BEFORE UPDATE/DELETE ... BEGIN SELECT RAISE(ABORT,
--   'option wall snapshot rows are append-only'); END
```

对齐 `opportunity_snapshot_runs` 的约定：`quote_as_of`（行情观测时刻）与
`fetched_at`（抓取时刻）**分开记**，`source` + `formula_version` 随行落库。

### 5.2 写入器

`src/opportunities/wall_snapshot_repository.record_wall_snapshots()`：

- **零新增取数路径**：入参是已经算好的 option-walls 响应体，本模块自己
  **不调用任何 provider**；
- **有界**：`tickers` 参数限定为调用方的日内深度层，另有
  `MAX_SNAPSHOT_TICKERS = 20` 兜底钳制，**绝不扇出到全部 69 个标的**；
- **幂等**：`snapshot_key = owds_<sha256(market_date_et|ticker)>`，刻意不含
  wall-clock，同日重复调用返回 `duplicate=True` 且不覆盖；
- **fail closed**：`state` 不是 `ready`/`partial`、或 spot 不可用时
  **什么都不写**（不是写 0）；部分覆盖如实写入并带自己的 `coverage_percent`。

### 5.3 触发方式（显式端点，不是常驻守护进程）

```
POST /api/v1/opportunities/option-walls/daily-snapshot
{"symbols": ["NVDA", "TSLA"], "dte_min": 0, "dte_max": 45}
```

本仓库目前**没有**一个适合挂载「盘中每日跑一次」的通用调度点（盘前
orchestration 只覆盖 premarket 周期），因此按约定暴露为显式端点：由用户、
cron 或外部调度每个 ET 交易日调用一次即可，重复调用幂等。**没有新增任何
always-on daemon。**

它复用既有墙位车道与缓存——同一 `(symbol, date, dte)` 在缓存 TTL 内不产生
新的 provider 请求；symbols 上限与墙位端点一致（≤5）。

### 5.4 2026-08-04 首条真实记录（NVDA）

| 字段 | 值 |
|---|---|
| spot | 206.64 |
| call_oi_total / put_oi_total | 2,608,001 / 2,063,349 |
| call_put_oi_ratio | **1.264** |
| call_volume_total / put_volume_total | 1,082,883 / 674,790 |
| call_put_volume_ratio | **1.605** |
| top call wall / top put wall | 210.0（OI 208,789）/ 170.0（OI 196,987） |
| gross gamma 集中位 | 210.0 |
| 未平仓加权中心 | 202.50 |
| coverage_percent | 100.0 |
| quote_as_of / fetched_at | 2026-08-03 15:59:59 / 2026-08-04 13:12:00Z |

重复调用返回 `written=false, duplicate=true`，行数不变。

---

## 6. 诚实边界

- `/rules` 全部为**描述统计**，不是建议、不是预测、不构成投资意见；
- 样本只覆盖 **2026-04→07 单一 regime**，换一段行情结论可能不成立；
- 规则由这段样本推出，属**样本内拟合**；前向验证以规则采纳日
  2026-08-05 之后的回合为准，落在 `/journal` 规则遵守度面板；
- 每张表都带样本量；分母为 0、样本缺失一律返回 `null` + 原因，**绝不以 0
  冒充**；
- 仓位与回撤块是「你选择的参数 + 它们的含义」，不对未来频率或回撤作任何承诺；
- call/put 比例与墙位只是事实描述；两个方向性假设**尚未被检验**，
  逐日快照正在积累样本。

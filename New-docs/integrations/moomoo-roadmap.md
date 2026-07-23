# Moomoo OpenAPI 接入路线图

> 目标：用 Moomoo OpenD daemon 增强当前以 yfinance + 手动 CSV 导入为主的数据链路，逐步形成“实时行情 + 只读交割单证据”的闭环。
>
> 操作风险按 phase 递增；**默认全部关闭**（`MOOMOO_OPEND_ENABLED=false`）。每个 phase 都能独立验收、独立回滚。
>
> **2026-07-21 安全更新**：项目已确认永久只读。Phase B 的旧 live writer 因缺少完整 provenance，CLI 和 `POST /journal/sync-live` 均保持暂停；只读 OpenAPI 入口仍是 `scripts/probe_moomoo_readonly.py`。CSV 已有独立的证据预览与 append-only 可信账本，当前 accepted CSV 也已生成版本化 PositionEpisode build。OpenAPI 已新增 DB-aware plan/confirm：plan 只读取最新 accepted CSV baseline，confirm 经局部范围显式确认后原子、幂等地追加不可变证据与 canonical set；冻结 canonical set 另有显式 preview/confirm，可生成不自动替换 CSV 默认视图的对比 EpisodeBuild。单合约复盘工作台已把所选 build 的 Episode evidence 与底层 K 线联动，明确区分 fill、订单时间代理和行情覆盖降级。没有已验证的期初持仓快照时，Episode 必须保留 `assumed_flat_unverified`。以下旧设计仅保留迁移背景，不代表可执行步骤。

---

## 现状（无 moomoo 时）

| 数据 | 来源 | 缺陷 |
|---|---|---|
| 美股 K 线 | yfinance | 日线 OK；intraday 配额紧（1m=7d / 5m=60d）；偶尔抽风 |
| 美股实时报价 | yfinance + 长桥兜底 | 延迟 15 分钟；非交易时段空 |
| 期权链 / IV | `data_provider/options_chain.py`（yfinance） | 行权价偶有缺失；greeks 自己算 |
| 交割单 | 手动 export Moomoo CSV → `/api/v1/journal/v2/imports/preview` → 明确确认后追加可信证据 → PositionEpisode build | 仍需手动导出；StrategyEpisode 当前仅为 1:1 unclassified 容器，尚无用户确认的多腿/roll 分组；legacy `/journal/import` 固定返回 410 |
| 仓位 | 不存在 | UI 上看不到当前持仓 |
| 板块 / 经纪席位 | 无 | regime 第 4 维只能用 ETF 代理 |

---

## Phase 配额表

| Phase | 范围 | 风险 | 状态 |
|---|---|---|---|
| **A · 行情读取** | K 线 + 报价走 OpenD | 低（只读） | ✅ 完成 |
| **B · 交割单事实** | 只读拉订单/成交/费用；CSV 可信证据、OpenAPI/canonical Episode plan/confirm 与单合约复盘 | 中（read-only 账户） | 🟡 1.5 已落地；opening snapshot 与策略分组待完成，live writer 暂停 |
| **C · 期权链 + IV** | 期权链替换 yfinance | 中 | ✅ 完成 |
| **D · 实时订阅 + 突破检测** | KLine_1M push 喂 breakout detector | 中 | ✅ 完成 |
| **E · 交易执行** | 解锁、下单、改单、撤单 | 禁止 | ❌ 永久不在本仓库实现 |

---

## ✅ Phase A · 行情读取（已实现）

### 落地

- [data_provider/moomoo_fetcher.py](data_provider/moomoo_fetcher.py) — 新建 `MoomooFetcher(BaseFetcher)`
  - `_fetch_raw_data` 走 `request_history_kline(K_DAY)` + 标准化为 `STANDARD_COLUMNS`
  - `fetch_intraday(stock_code, interval, days)` 同 `YfinanceFetcher` 签名，覆盖 `1m / 5m / 15m / 30m / 60m / 1h`
  - `get_realtime_quote(stock_code)` 走 `get_market_snapshot([code])`，返回 `UnifiedRealtimeQuote`（`source=RealtimeSource.MOOMOO`）
  - SDK 懒加载：未装 `moomoo-api` 时静默 shelve（priority=99），不影响其他 fetcher
  - OpenQuoteContext 用 RLock + 单例，多线程安全
- [data_provider/base.py](data_provider/base.py)
  - `DataFetcherManager.__init__` 把 `MoomooFetcher()` 加入 `_fetchers`
  - `get_intraday_data` 路由现在是 **Moomoo 优先 → yfinance 兜底**（仅当 `MoomooFetcher.priority < 99` 即 SDK + ENABLED 同时满足）
- [data_provider/realtime_types.py](data_provider/realtime_types.py) — `RealtimeSource` 枚举加 `MOOMOO = "moomoo"`
- [.env.example](.env.example) + [requirements.txt](requirements.txt) — 新增配置入口（默认全注释，opt-in）

### 启用步骤

```bash
# 1) 装 OpenD（macOS）— 让 install-moomoo-opend skill 跑，或手动从 https://openapi.moomoo.com/ 下
# 2) 装 Python SDK
pip install moomoo-api>=10.9.6908

# 3) 启动 OpenD app，登录你的 Moomoo 账号

# 4) 改 .env
echo 'MOOMOO_OPEND_ENABLED=true'   >> .env
echo 'MOOMOO_OPEND_HOST=127.0.0.1' >> .env
echo 'MOOMOO_OPEND_PORT=11111'     >> .env

# 5) 重启 uvicorn / 重启分析器
# 日志里应看到：[MoomooFetcher] enabled OpenD=127.0.0.1:11111 priority=2
# 这之后 /api/v1/stocks/AMZN/history?period=5m 会自动走 Moomoo（实时），
# 失败再 fallback 到 yfinance。
```

### 验收

```bash
# 后端：
curl 'http://localhost:8000/api/v1/stocks/AMZN/history?period=daily&days=10'   # 看 stock_name 是否 "Amazon.com Inc"
curl 'http://localhost:8000/api/v1/stocks/AMZN/history?period=5m&days=2'       # intraday，检查 date 是否带时区

# 服务日志应看到 [Moomoo] history_kline daily/intraday code=US.AMZN ...
# 关掉 OpenD，重启后端 → fetcher shelve，自动用 yfinance；功能不掉
```

### 回滚

```bash
# .env 改 MOOMOO_OPEND_ENABLED=false 或删行；下次重启自动回到 yfinance-only
```

---

## 🟡 Phase B · 交割单事实与策略生命周期

**目标**：把 CSV 与 OpenAPI 分别保存为不可变 broker observations，再通过 canonical reader 建立可追溯的 order → fill → position → strategy 链。新事实不再直接喂给 legacy FIFO 全量重建。

### 正式同步前的只读探测入口

正式写入 Journal 前，先用独立入口核对真实账户的订单、成交与费用：

```bash
# 默认查询唯一的真实美股账户，回看 30 天；stdout 仅输出去标识化汇总
python scripts/probe_moomoo_readonly.py --env LIVE

# 显式导出白名单字段，供后续做生命周期和费用分析
python scripts/probe_moomoo_readonly.py --env LIVE \
  --output ./data/moomoo-readonly-export.json
```

该入口不会调用 Journal storage，也不会写项目数据库。它会先执行短连接探测，再通过
`get_acc_list` 确认目标账户；只有目标环境和市场下存在唯一账户时才自动选择，否则要求
显式传入 `--acc-id`。历史查询按最多 7 天分块并重试，以稳定的 `order_id` / `deal_id`
去重；订单费用每批最多查询 400 个。完整记录仅写入显式指定的 `--output` 文件，且账户
ID、卡号等账户标识不会进入导出内容或 stdout。`SIMULATE` 模式只查询历史订单，不请求
该环境不支持的逐笔成交和订单费用接口。同步 SDK 边界运行在有总截止时间的子进程中；
查询完成只表示 `ok=true`，代码、方向、数量、成交加权均价和费用覆盖全部通过后才会返回
`analysis_ready=true`。

### CSV 证据账本（已落地）

Web `/journal?tab=import` 会先调用 `POST /api/v1/journal/v2/imports/preview`，此时不写数据库；确认后才调用 `POST /api/v1/journal/v2/imports`。逐笔成交写入 `journal_v2_broker_fill_observations`，只有券商订单汇总的历史记录只写 order observation，不补造 fill。相同源文件与 parser 版本幂等，`partial` 批次必须显式传入 `allow_partial=true`。

当前 accepted CSV 与 2026-06-19 至 2026-07-19 ET 的只读 OpenAPI 稳定窗口已完成逐单对账：1,089 / 3,542 个订单、2,107 条 fill、VWAP 和九项费用分量均无差异，scope 为 `partial_window`。完整证据验收见 [Phase 1.1 Moomoo 证据账本](../phase1/01_MOOMOO_EVIDENCE_LEDGER.md)，Episode 口径见 [Phase 1.2 PositionEpisode 生命周期账本](../phase1/02_POSITION_EPISODES.md)。

### OpenAPI DB-aware plan/confirm（已落地）

`POST /api/v1/journal/v2/openapi-imports/plan` 是零证据行写入计划：它只读取目标账户最新的 accepted CSV batch，更早 CSV 继续保留审计但不参与本次 canonical 输入。服务首次初始化可能创建缺失表和不可变 trigger；计划本身不追加订单、成交、费用、link、attestation 或 canonical 记录。计划展示覆盖率、窗口外未验证订单、order/deal identity links、fill-set attestations、canonical 影响与 blockers。

`POST /api/v1/journal/v2/openapi-imports/confirm` 会重新生成计划、拒绝 stale `preview_key`，并要求 `partial_window` 显式确认。通过后，OpenAPI order/fill/fee observations、identity links、fill-set attestations 和 canonical set 在同一事务中追加；重复 export 返回 `already_present`，不重复新增事实或费用。confirm 固定不写 legacy Journal、不触发 Episode rebuild，也不执行任何交易动作。

正确数据的 plan 覆盖 1,089 / 3,542 个订单，窗口外 2,453 个订单仍未由该 API 窗口验证。正式本地 confirm 已追加 1,089 order、2,107 fill、1,058 fee、1,089 order links、1,136 deal links 和 244 fill-set attestations；去重后的完整 latest-batch canonical 结果为 3,542 orders / 5,400 fills，0 blocking issues。重复 confirm 返回 `already_present` 且新增计数全部为 0。详见 [Phase 1.3B OpenAPI canonical persistence](../phase1/03_OPENAPI_CANONICAL.md)。

### Canonical → Episode 对比构建（已落地）

Canonical set 保存后不会自动重建或切换“仓位复盘”。`GET /api/v1/journal/v2/episode-builds/canonical/preview` 重放冻结的 canonical record/members/selected observations，返回 canonical hash、build key、来源批次、计划 Episode 数量和费用守恒；`POST /api/v1/journal/v2/episode-builds/canonical` 携带上述精确标识，在重新验证后原子、幂等地追加对比 build。省略 `build_id` 的读取仍返回 CSV 默认 build，canonical build 必须显式选择。

2026-07-21 canonical set 1 的真实 preview 为 574 个 aggregate order events + 5,400 个 fill events，生成 1,441 个 PositionEpisode（1,437 closed / 4 open），USD 151,750.75 source/allocated 费用守恒。Aggregate order observation 的 submitted amount 不作为执行现金流；builder 只根据可证明的 filled quantity、average price 与 contract multiplier 推导金额。该 builder 按有符号持仓 `0 -> 非 0 -> 0` 构建生命周期，不是 FIFO。完整契约见 [Phase 1.4 canonical 对比构建](../phase1/04_CANONICAL_EPISODE_BUILD.md)。

### 单合约 K 线与成交证据复盘（已落地）

`/journal/review/:episodeId` 保留所选 `build_id` 以及标的、状态、完整度和页码上下文，将不可变 fill/order evidence 对齐到底层 K 线。真实 fill 与 `order_time_proxy` 使用不同 marker；期权成交价只在摘要和时间线展示，不混入底层价格轴。最近 60 天先尝试 5 分钟行情，只有 bars 非空且覆盖全部 execution evidence 才采用；否则明确降级日线并禁止精确入场、MFE/MAE 结论。行情接口返回实际 `source / coverage_start / coverage_end / last_bar_at`，空数据不伪造 provenance。详见 [Phase 1.5 单合约复盘工作台](../phase1/05_SINGLE_POSITION_REVIEW_WORKSPACE.md)。

### 已落地与待完成

已落地：

- `src/journal/brokers/moomoo_statement.py`：loss-aware CSV parser；
- `scripts/reconcile_moomoo_history.py`：CSV/OpenAPI 去标识化对账；
- `src/journal/ledger/models.py` 与 `repository.py`：append-only batch/order/fill 证据账本；
- `src/journal/ledger/episodes.py` 与 `episode_repository.py`：builder v1.1.0、append-only EpisodeBuild、费用守恒与 evidence allocation；
- `src/journal/brokers/moomoo_openapi_export.py`：去标识化只读 export 的严格纯解析；
- `src/journal/ledger/identity.py` 与 `canonical.py`：只对已对账事实生成显式 order/deal links 或 order-level fill-set attestations，并按稳定身份去重、保留 shadow provenance、阻断经济字段冲突；
- `src/journal/ledger/openapi_repository.py` 与 `repository.py`：DB-aware 零证据行写入 plan、stale/partial confirm 门禁，以及 OpenAPI observations、links、attestations、canonical set 的单事务 append-only persistence；
- `src/journal/ledger/episode_repository.py` 与 `models.py`：冻结 canonical replay、stale hash/build-key 门禁、append-only canonical source binding 和不激活默认视图的对比构建；
- `src/services/stock_service.py` 与股票历史 API：在最终返回 bars 上追加行情 source、coverage 和 last-bar provenance；
- `/journal/v2/imports/preview`、`/journal/v2/imports`、`/journal/v2/data-health`；
- `/journal/v2/openapi-imports/preview`：只读 JSON 范围、数量、费用和对账预览，不写数据库；
- `/journal/v2/openapi-imports/plan`、`/journal/v2/openapi-imports/confirm`：最新 accepted CSV 基线上的零证据行写入计划与显式原子确认；
- `/journal/v2/episode-builds/canonical/preview`、`/journal/v2/episode-builds/canonical`：冻结 canonical set 的零 Episode 行写入预览与显式原子确认；
- `/journal/v2/episode-builds`、`/journal/v2/position-episodes` 与 Episode 详情 API；
- `/journal/review/:episodeId`：保留构建/筛选返回上下文的单合约底层 K 线与成交证据工作台；
- Web 导入预览、明确确认、Data Health、默认“仓位复盘”，以及“可信事实集构建预览”中的 assumed-flat 勾选、显式查看/返回默认构建。

当前 signed-position lifecycle builder 已用 5,400 条真实 fill 与 574 条 aggregate order event 构建 1,441 个 PositionEpisode，并验证 USD 151,750.75 source/allocated 费用守恒。aggregate-only 证据不会被伪造成 fill，submitted amount 也不会被当成执行现金流；StrategyEpisode 当前保持 1:1 `single_position_unclassified`，不推断 spread 或 roll。

待完成：经验证的 opening-position snapshot、StrategyEpisode 候选分组与人工确认，以及至少 20 个复杂生命周期的人工抽查。旧 `scripts/sync_moomoo_live.py` 和 `/journal/sync-live` 在这些退出条件完成前固定拒绝。

**幂等与身份**：CSV 批次以账户键、源内容哈希和 parser 版本组成稳定 batch key；CSV order identity 使用代码、方向、数量和订单秒级时间构造并检测碰撞。OpenAPI observation 以 broker `order_id/deal_id` 为稳定身份；不能证明逐笔一对一时使用订单级 fill-set attestation，不能通过删除“看起来相同”的行来去重。

### 风险与缓解

| 风险 | 缓解 |
|---|---|
| 把 `LIVE` 误解为可交易 | `LIVE` 仅表示读取真实历史；OpenD 保持交易锁定，代码护栏扫描所有交易动作 |
| OpenD 断线丢同步 | 新 importer 必须保存范围与批次状态，恢复后重新覆盖边界窗口并按稳定 ID 去重 |
| 同步 Context 构造时 OpenD 掉线 | 整个只读探测隔离到可终止子进程，并设置总超时；不读取或保存交易密码 |
| 较早订单没有逐笔 fill | 保留为 `aggregate_only`，只允许持仓、现金流和费用级分析，禁止分钟级入场、滑点和 MFE/MAE 归因 |
| Aggregate submitted amount 被误当执行现金流 | canonical 投影不传该金额；只由已证明的成交数量、均价和合约乘数推导执行现金流 |
| 历史分钟行情无法覆盖 Episode | 最近 60 天也必须覆盖全部 evidence 才使用 5 分钟视图，否则明确降级日线并禁止精确入场/MFE/MAE 结论 |
| 把期权 premium 画到底层价格轴 | 图表纵轴只接收底层 OHLC；期权均价仅在摘要和 evidence 时间线显示 |
| 覆盖窗口起点没有持仓快照 | build 标记 `assumed_flat_unverified`，全部 Episode 排除 headline；条件性 P&L 不代表正式账户收益 |
| 同秒同价的独立成交被误删 | 不按行内容盲目去重；以来源身份、行证据与数量守恒判断 |
| CSV Fill Time 与 API create_time 不同 | 两个来源时间分别保存，不互相覆盖 |

### 验收

- **已完成**：稳定窗口 1,089 个订单、2,107 条 fill、成交数量、VWAP 与九项费用分量零差异；
- **已完成**：CSV 重复导入幂等，`partial` 必须明确确认，`blocked` 不落库；盘中费用不全时 OpenAPI 返回 `analysis_ready=false`；
- **已完成**：signed-position lifecycle builder、1,441 个 PositionEpisode、1:1 unclassified StrategyEpisode、aggregate-only 不造 fill 与 USD 151,750.75 费用守恒；
- **已完成**：OpenAPI export 严格只读 preview，以及以最新 accepted CSV 为唯一基线的 DB-aware plan/confirm；局部窗口需显式确认，整套 observations/identity provenance/canonical set 单事务追加且重复确认幂等；
- **已完成**：正确数据 plan 覆盖 1,089 / 3,542 个订单，正式本地 confirm 追加 1,089 order、2,107 fill、1,058 fee、1,089 order links、1,136 deal links、244 fill-set attestations，canonical 输出 3,542 orders / 5,400 fills；
- **已完成**：冻结 canonical replay、显式 preview/confirm、append-only/idempotent 对比构建和默认 CSV build 隔离；真实 preview 为 574 aggregate + 5,400 fill events、1,441 个 Episode，USD 151,750.75 费用守恒；
- **已完成**：单合约复盘保留 `build_id` 与列表上下文，K 线/evidence 可联动，fill 与订单时间代理分开，行情 source/coverage 可见，5 分钟覆盖不足时明确降级日线；
- **待完成**：opening snapshot、用户确认的 StrategyEpisode 分组与至少 20 个复杂生命周期人工抽查；
- 旧 CLI 持续返回 `LegacySyncPaused`，旧 API 持续返回 409。

---

## ⏳ Phase C · 期权链 + IV 替换

**目标**：当前 [data_provider/options_chain.py](../data_provider/options_chain.py) 走 yfinance 拉期权链，IV 自己算（Black-Scholes 反推）。Moomoo 给的链更全 + 自带 IV/Delta/Gamma/Theta/Vega。

### 设计

- 给 `OptionsChainFetcher` 加一个 `_source: 'yfinance' | 'moomoo'` 属性
- `MOOMOO_OPEND_ENABLED=true` 时优先 moomoo，回落 yfinance
- moomoo: `OpenQuoteContext.get_option_chain(code, start, end)` + `get_option_expiration_date(code)`
- moomoo greeks 直接用，避免我们的 BS 反推数值漂移

### 影响面

- [src/options/iv_rank.py](../src/options/iv_rank.py) 减少对 BS 反推的依赖
- [src/lab/](../src/lab/) LEAP explorer 拿到更稳定的 IV 历史

---

## ⏳ Phase D · 实时订阅 + 突破检测

**目标**：用 KLine_1M / Ticker 实时推送，喂 [src/breakout/detector.py](../src/breakout/detector.py)，把"事后回填的 trade_style"升级为"盘中实时打标"。

### 设计

```
src/breakout/live_runner.py             ← 新建
    init_subscriptions(watchlist_tickers)
    on_kline_push(handler) → run_filter(Q1..Q5) → 命中 → push 到 bot/Telegram

scripts/run_breakout_live.py            ← 常驻进程
```

**配额管理**：用户自选 50 只 ticker × KLine_1M = 50 格；够用，但要在 `unsubscribe` 旧的 ticker 时小心 1 分钟冷却。

### 影响面

- bot/commands/regime_cmd.py 加新指令 `/breakout-live` 查看当前订阅状态
- 前端 BreakoutSignalsList 增加 "live" 标签，区分历史回填 vs 实时

---

## ❌ Phase E · 下单（不规划）

本仓库永久不实现交易解锁、下单、改单或撤单。`LIVE` 只表示读取真实账户历史事实，不是交易权限。如果未来出现执行需求，应作为独立项目、独立权限与独立审计边界重新立项，不能在本仓库通过配置开关启用。

---

## 整体环境变量速查

```bash
# Phase A
MOOMOO_OPEND_ENABLED=false           # 总开关
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111
MOOMOO_PRIORITY=2                    # fetcher 优先级；默认 2，排在 yfinance(4) 前

# Phase B legacy writer（暂停，不使用）
MOOMOO_TRADE_ENABLED=false
MOOMOO_TRADE_ENV=SIMULATE

# 不提供 Phase E；项目没有 execution 配置或交易入口
```

---

## 与已有 skill 的关系

- 装在 `~/.claude/skills/install-moomoo-opend` 和 `~/.claude/skills/moomooapi`：**Claude Code 交互**用，不是 daily_stock_analysis 后端运行时依赖
- 后端依赖只有 `pip install moomoo-api`（OpenD 二进制是用户自己装在系统层级）
- `New-docs/integrations/moomoo-subscription.md` 是写给人看的速查；本文是路线图

---

_当前实施顺序以 `New-docs/architecture/05_PRODUCT_CHARTER_AND_ROADMAP.md` 为准：Phase 0 稳定性观察继续进行；Phase 1.1 证据账本、Phase 1.2 PositionEpisode builder、Phase 1.3B OpenAPI plan/confirm/canonical persistence、Phase 1.4 canonical 对比构建与 Phase 1.5 单合约复盘工作台已完成，下一步是用工作台抽查复杂生命周期、补 opening snapshot 与经用户确认的 StrategyEpisode 分组。_

# Daily-Stock — 当前架构与功能（2026-07-30 审计快照）

> 这份文档说明 fork 当前的实际形态。**和上游 `ZhuLinsen/daily_stock_analysis` 已经显著分叉**：
> - Phase 0 v4 Mirror 层（完工于 2026-04-20）—— Journal / Regime / Breakout / Options / LEAP
> - Moomoo OpenAPI 集成 Phase A/B/C/D（完工于 2026-04-29）
> - macOS LaunchAgent "always-on" 部署 + sessionStorage 缓存 + 实时突破 daemon
>
> 2026-07-20 运行审计发现旧 LaunchAgent 在 OpenD 离线时会被 SDK 同步连接阻塞并无限刷日志，三项服务因此暂停。快速失败、资源回收、日志硬上限和只读探测已有测试与真实 smoke，但仍需 24 小时观察；旧 Journal sync 因账户选择与费用口径不可信而明确禁用，不随服务恢复。2026-07-30 已新增独立的当前期权持仓双采样证据链，但它不会倒推旧 Episode 的期初仓位。产品实施顺序以 [`05_PRODUCT_CHARTER_AND_ROADMAP.md`](./05_PRODUCT_CHARTER_AND_ROADMAP.md) 为准。
>
> 旧的"配股票池 → 跑分析 → 推 Telegram"链路保留作为兼容路径，但本 fork 的核心已转向**单人交易终端 + 实时陪跑系统**。

---

## 一、产品形态

```
┌─────────────────────────────────────────────────────────────────┐
│ 浏览器 (localhost:8000)                                         │
│  ├─ /regime         六维度速度表 + 内联 TradingView             │
│  ├─ /watchlist      自选股表 + add/filter (autocomplete + 校验) │
│  ├─ /stocks/:ticker K线 (Moomoo) + 新闻 + LLM 中文摘要          │
│  ├─ /journal        交割单 + Reality Test + AI 月度复盘 + Q&A    │
│  ├─ /backtest       AI 预测 vs 次日实际                          │
│  └─ /settings       LLM / 数据源 / 通知配置                     │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTP (FastAPI on 8000)
┌──────────────────────┴──────────────────────────────────────────┐
│ uvicorn 后端（当前手动受控启动；LaunchAgent 暂停验收）          │
│  ├─ Phase 0 modules: journal / regime / breakout / options/lab │
│  ├─ Moomoo OpenAPI 集成 Phase A-D                              │
│  ├─ Search providers: Tavily / Brave / SerpAPI / SearXNG ...   │
│  └─ LLM router: Gemini / Claude / DeepSeek / Qwen 等           │
└──────┬─────────────────────────┬────────────────────────────────┘
       │                         │
       │ Moomoo OpenAPI          │ HTTPS (yfinance / Tavily / ...)
       │ 11111 (本机)            │
       ▼                         ▼
┌────────────────┐         ┌──────────────────┐
│ Moomoo OpenD   │         │ 互联网数据源     │
│ daemon (GUI)   │         │ (兜底)           │
│ + 用户登录     │         └──────────────────┘
└────────────────┘
```

---

## 二、关键路由 / 端点速查

### 前端路由 (`apps/dsa-web/src/pages/`)

| 路径 | 页面 | 主要功能 |
|---|---|---|
| `/` | 重定向到 `/regime` | — |
| `/regime` | RegimePage | 半圆速度表 (Fear & Greed 风) · 6 维度 Contributions + ❓悬浮说明 · Watchlist 表(TickerPicker autocomplete + did-you-mean) · 行点击 → 表下方内联 TradingView K 线 · 30/60/90 天 history |
| `/watchlist` | WatchlistPage | 全列 DataTable · 列选 / CSV export / filter / Sparkline / 行点跳详情 |
| `/stocks/:ticker` | StockDetailPage | Moomoo 实时 K 线 (1m/5m/15m/1h/1D/1W/1M) · MA 8/13/144/169 · News (5 档 sentiment 箭头) · 中文总结 tab · invalid-ticker 自动 typo 提示 |
| `/journal` | JournalPage | 默认仓位复盘、案例筛选、当前期权持仓只读快照（双采样、30 分钟时效、账户/publication currentness）、交易证据导入/刷新与隔离的 legacy 分析页 |
| `/backtest` | BacktestPage | 历史 AI 预测准确率 |
| `/settings` | SettingsPage | LLM 通道 / API 密钥 / 通知 |

### 后端 API（`/api/v1/`）

```
/health
/auth/...                     登录 / 登出 / current user
/stocks
  /{code}/quote               实时报价 (Moomoo 优先 → yfinance fallback)
  /{code}/history             K 线 (daily/weekly/monthly/1m/5m/15m/30m/60m/1h)
  /{code}/news                按 ticker 抓新闻 (Tavily / SearXNG / ...)
  /{code}/news/digest         LLM 中文摘要 + 5 档 sentiment per item
/history                      旧分析结果列表
/analysis                     启动新分析 (sync / async)
/regime
  /today, /history, /recompute
/breakout/signals             历史突破 + Q1-Q5 过滤
/journal
  /trades, /trades/{id}       交割单查询 / 编辑
  /reality-test               Reality Test 灵魂指标
  /stats, /stats-by-style     统计 + 按 trade_style 聚合
  /qa                         用户 framework + 最近交易 → LLM 中文回答
  /sync-live                  409：旧入库链路暂停，不创建 SDK Context、不写库
  /v2/position-snapshots/*   当前期权持仓双采样 preview / confirm / latest；过期或账户/锚点变化后降为历史快照，只作未来证据锚点
  /reviews/{ym}, /reviews/{y}/{m}/generate   月度 AI 复盘
  /import                     上传 CSV
/system
  /config, /config/schema     系统配置
  /moomoo-status              Moomoo OpenD 实时状态 (UI badge 用)
/backtest
/portfolio
/agent/chat                   多轮 Agent 对话（带 tool-use）
```

---

## 三、数据源链路

### 行情（K 线 / 报价）

按优先级（数字越小越优先）：

| Priority | Fetcher | 适用市场 | 状态 |
|---|---|---|---|
| 0 | EfinanceFetcher | A 股 | 默认开启 |
| 1 | AkshareFetcher | A 股 | 默认开启 |
| 2 | TushareFetcher | A 股 | 配 Token 后启用 |
| **2** | **MoomooFetcher** | **美股 / 港股** | **`MOOMOO_OPEND_ENABLED=true` + OpenD 启动后启用** |
| 3 | BaostockFetcher | A 股 | 默认开启 |
| 4 | YfinanceFetcher | 全市场兜底 | 默认开启 |
| 5 | LongbridgeFetcher | 美股 / 港股 | 配 LongPort 凭据后启用 |
| 99 | (shelved) | — | Moomoo SDK 未装时退到这里 |

**Moomoo 优先链路**（启用后）：
1. `/stocks/AMZN/history?period=5m&days=2` → `DataFetcherManager.get_intraday_data` → 优先 Moomoo（priority 2 < yfinance priority 4）
2. 失败 / OpenD 离线 → 自动 fallback 到 yfinance

### 新闻

`SearchService` 多 provider，按用户 .env 配置的 API Key 决定可用：
- Tavily（默认，每月 1000 次免费）
- SerpAPI / Brave / Bocha / Anspire / MiniMax / SearXNG

`stocks/{code}/news` 拉真实可跳转新闻；`stocks/{code}/news/digest` 调 LLM 一次产出每条 sentiment + 中文整体摘要 + 3-5 bullet。

### LLM

`LLMToolAdapter` (`src/agent/llm_adapter.py`) 通过 LiteLLM 路由，支持 Gemini / Claude / OpenAI / DeepSeek / Qwen / Moonshot 等。配 `.env` 里任一 `*_API_KEY` 自动识别。

---

## 四、Phase 0 v4 Mirror 层

**核心命题**：把"美股期权 + LEAP + 趋势流"加进来，**不侵入** A 股原链路。

```
src/journal/      ┌─ Moomoo CSV import (brokers/moomoo_us.py) + Moomoo Live API (brokers/moomoo_live.py)
                  ├─ FIFO 配对 (matcher.py) → 多腿 / 期权乘数 / fee 分摊
                  ├─ Reality Test (analytics.py) — 去掉 Top-N 看真实 PnL
                  ├─ AI 月度复盘 (monthly_review.py) — Gemini/Claude → 中文 Markdown
                  └─ Folder watcher (folder_watcher.py) — 监听 ~/Daily-Stock-Inbox/

src/regime/       ┌─ 6 维度纯函数打分 (scorers.py)
                  │   ├─ d1 Direction  (SPY MA20/50 + 5d 动能)
                  │   ├─ d2 Volatility (VIX 区间分档)
                  │   ├─ d3 Macro      (FOMC / CPI / NFP / earnings)
                  │   ├─ d4 Sector     (11 板块站上 MA20 个数)
                  │   ├─ d5 PrevDay    (昨收贴近高点 + 振幅)
                  │   └─ d6 Premarket  (SPY 盘前 + 自选涨跌人数)
                  ├─ classifier.py → aggressive / standard / cautious / no_trade
                  ├─ morning_brief.py + GitHub Actions 每日 14:00 UTC cron
                  └─ Telegram / 飞书 / Discord push

src/breakout/     ┌─ detector.py — range_high / prev_day_high 检测
                  ├─ filter.py   — Q1-Q5 短路决策树
                  │     Q1 Regime ≥ 阈值
                  │     Q2 Pattern 命中
                  │     Q3 Volume confirmation
                  │     Q4 Multi-timeframe alignment
                  │     Q5 RS vs SPY
                  ├─ live_runner.py — KLine_1M push 喂 Q1-Q5（**Phase D**）
                  └─ backfill_*.py — 历史 trade_style 回填 (rule-based)

src/options/      ┌─ occ_parser.py — OCC 变长 strike
                  ├─ black_scholes.py — call/put + Greeks + IV 反推
                  ├─ iv_rank.py — Moomoo IV 优先 → yfinance 反推 fallback（**Phase C**）
                  └─ storage.py — option_chains_cache / iv_snapshots

src/lab/          LEAP Explorer · Shadow Trades · Backtest Replayer

src/agent/        Skill bundles (option_trader / leap_explorer / trend_follower)
                  + 多轮 Chat agent + 5 个 journal/regime/breakout/options 工具
```

---

## 五、Moomoo 集成（4 phase）

| Phase | 范围 | 关键文件 | 状态 |
|---|---|---|---|
| **A · 行情** | quote / 日 K / 1m-1h intraday | [`data_provider/moomoo_fetcher.py`](../../data_provider/moomoo_fetcher.py) | ✅ |
| **B · 交割单事实读取** | get_acc_list + history order/deal + order fees → 去标识化导出 | [`src/journal/brokers/moomoo_readonly.py`](../../src/journal/brokers/moomoo_readonly.py) + [`scripts/probe_moomoo_readonly.py`](../../scripts/probe_moomoo_readonly.py) | ✅ 只读读取、分块、稳定 ID、费用和完整对账已实测；⚠️ 正式 provenance 入库未实现，旧 writer 已暂停 |
| **C · 期权 IV** | get_option_chain (服务端 IV/Greeks) | [`data_provider/moomoo_options.py`](../../data_provider/moomoo_options.py) | ✅ |
| **D · 实时突破** | KLine_1M push + Q1-Q5 过滤 + 5 min cooldown 去重 | [`src/breakout/live_runner.py`](../../src/breakout/live_runner.py) | ✅ |
| E · 下单 | 解锁 / 下单 / 改单 / 撤单 | — | ❌ 永久排除（安全边界） |

详情见 [moomoo-roadmap.md](../integrations/moomoo-roadmap.md) + [moomoo-subscription.md](../integrations/moomoo-subscription.md)。

---

## 六、macOS Always-On 部署

仓库保留 3 个 `~/Library/LaunchAgents/` 模板，但 2026-07-20 审计期间均已卸载，不能把下表理解为当前正在运行：

| LaunchAgent | 职责 | 频率 |
|---|---|---|
| `com.dailystock.uvicorn` | 后端 + 前端 static (8000) | 开机自启，崩了 30s 重启 |
| `com.dailystock.moomoo-sync` | 旧 Phase B writer（当前入口只返回暂停状态） | 每 15 分钟模板，当前未加载 |
| `com.dailystock.breakout-live` | Phase D 实时突破 daemon | 常驻 + 自动重连 OpenD |

三个 LaunchAgent 均通过 `scripts/run_with_rotating_logs.py` 分流并轮转子进程
stdout/stderr；沿用原日志路径，每个活动文件上限 10 MiB、保留 3 个备份，
LaunchAgent 自身输出指向 `/dev/null`，避免固定文件无限增长。

**装/卸/状态**（审计期间只使用状态与卸载；不要执行全量安装）：
```bash
bash scripts/launchagent_status.sh          # 看 PID + 最后日志
bash scripts/uninstall_launchagents.sh      # 全清
# bash scripts/install_launchagents.sh      # 暂停使用，需完成 24 小时验收后逐项恢复
# bash scripts/install_launchagents.sh --only uvicorn
# 上一行仅恢复 Web/API，不会加载旧同步 writer 或 breakout；仍需先完成验收
```

OpenD 本身在 macOS 系统设置 → 通用 → 登录项里勾上"Moomoo OpenD"就能开机自启 + Auto Login。

恢复 always-on 前必须先通过离线快速失败、日志轮转、单实例和 24 小时日志增长验收。当前安全启动方式是：先确认 OpenD 已登录且保持交易锁定，再手动启动受控服务；项目不读取或保存交易密码。即使其他服务恢复，旧 Moomoo Journal writer 也保持禁用，直至新 importer 完成稳定账户、完整费用、provenance 与策略生命周期对账。

---

## 七、前端关键设计决定

- **设计系统**：Linear-Dark + 终端化（4-bg / 3-border / 4-text token，单 accent #7170ff，`Geist Mono` for tabular numerics）。规则在 `New-docs/design/Design_system.md`。
- **缓存策略**：sessionStorage + 10 min TTL（[`utils/sessionCache.ts`](../../apps/dsa-web/src/utils/sessionCache.ts)）。覆盖 `regime.today` / 历史 K 线 / 新闻 / digest / journal stats-by-style — 切页不重拉。
- **本地 watchlist**：`useUserWatchlistStore` (zustand+persist) → `dsa-user-watchlist` localStorage key，刷新不丢。
- **Journal framework**：`useJournalFrameworkStore` → `dsa-journal-framework`，用户写一段文本作 AI 分析交割单的"大前提"。
- **TickerPicker autocomplete**：`useStockIndex` 加载 `public/stock-index.json`，前缀匹配 + Levenshtein fuzzy 兜底（"amaz" → "AMZN" did-you-mean）。
- **MoomooBadge** in TopBar：同一标签内由模块级监控器合并全部组件订阅；多个可见标签通过 Web Locks 竞争单次探测，并以 `localStorage` + `BroadcastChannel` 共享结果，正常状态整个浏览器 profile 最多每 60s 请求一次 `/api/v1/system/moomoo-status`。隐藏标签停止轮询，重新可见或浏览器恢复 online 时立即参与刷新；请求失败显示“状态未知”并按 15/30/60/120s 退避，成功结果超过 75s 不再继续冒充实时状态。3 个已确认状态仍为：Moomoo 可达且只读（绿）/ Moomoo offline（黄）/ yfinance（灰）。`connected` 只表示有界 TCP 探测成功，不表示交割单已经同步；端点不能为轮询创建一个会无限重连的 SDK Context。

---

## 八、未做的（已知缺）

- ❌ **多用户 SaaS**：当前是单人本地。要做多用户得给每用户跑自己的 OpenD（违 Moomoo ToS 不能搬云）+ 反向隧道方案。
- ❌ **真实下单接口**：Phase E 不规划。
- ⚠️ **HK / CN 期权**：Moomoo 链工作但不是测试重点。
- ⚠️ **Moomoo IV 盘后/无 LV2 时为 0**：自动 fallback yfinance。
- ⚠️ **Phase D 突破 push 无通知渠道**：现在仅写到 `logs/breakout-live.jsonl`；接 Telegram bot 是用户层加 hook 的事。
- 📋 **A 股 / 港股的 intraday**：Moomoo 支持但 fetcher 路由暂只优先美股。
- ⚠️ **现有 Journal 不是完整期权策略账本**：当前 FIFO 行模型不能可靠表达逐笔费用、分批、多腿、roll、行权/指派和完整证据链；不要把现有聚合 P&L 当作最终券商对账结论。

---

## 九、文档导航

- 入门 / 安装：[`README.md`](../../README.md)
- 设计系统：[`New-docs/design/Design_system.md`](../design/Design_system.md)
- Phase 0 各 stage：[`New-docs/phase0/`](../phase0/)
- Moomoo 集成：[`New-docs/integrations/moomoo-*`](../integrations/)
- 配置 / 部署：[`New-docs/configuration/`](../configuration/) + [`New-docs/deployment/`](../deployment/)
- 用户指南：[`New-docs/user-guide/`](../user-guide/)
- 历次重大改动：[`docs/CHANGELOG.md`](../../docs/CHANGELOG.md)

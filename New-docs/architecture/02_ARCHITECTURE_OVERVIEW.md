# 02 · Architecture Overview

> **目的**：标注真实 repo 里每个目录的改造点，让你知道"哪个文件改哪几行"  
> **前置**：`01_PROJECT_VISION_v4.md`

---

## 0. 真实 repo 目录树（带改造标注）

```
daily_stock_analysis/
│
├── .claude/skills/              🟢 扩展 — 新增 option_trader / leap_explorer / trend_follower
├── .github/
│   └── workflows/               🟢 扩展 — 新增 regime_brief.yml / weekly_reality_test.yml
│
├── api/
│   ├── app.py                   🟡 小改 — 挂新 router（journal / regime / lab）
│   └── routers/                 🟢 新增文件：journal.py / regime.py / lab.py / phase.py
│
├── apps/dsa-web/                🟡 扩展 — 新增路由与组件
│   ├── src/
│   │   ├── pages/
│   │   │   ├── HomePage.tsx            🟡 改造 — Reality Test 首页卡片
│   │   │   ├── PortfolioPage.tsx       🟡 扩展 — 加 Journal / Reality Test tabs
│   │   │   ├── JournalPage.tsx         🟢 新增
│   │   │   ├── LabPage.tsx             🟢 新增 — LEAP Explorer / Shadow / Backtest
│   │   │   ├── JourneyPage.tsx         🟢 新增 — Phase 进度 / Evolution Timeline
│   │   │   ├── RegimePage.tsx          🟢 新增 — Regime Score 历史图
│   │   │   ├── StockPage.tsx           🟡 扩展 — 加期权链 tab + TV Widget
│   │   │   └── BacktestPage.tsx        保留
│   │   ├── components/
│   │   │   ├── journal/                🟢 新增 — RealityTestCard / DTEDistribution /
│   │   │   │                                      DailyHealthCheck / BreakoutStatus
│   │   │   ├── lab/                    🟢 新增 — LEAPExplorer / ShadowTradeForm / ...
│   │   │   ├── regime/                 🟢 新增 — RegimeScoreCard / MorningBrief
│   │   │   └── options/                🟢 新增 — OptionChainTable / GreeksPanel
│   │   └── stores/
│   │       ├── stockPoolStore.ts       保留
│   │       ├── phaseStore.ts           🟢 新增 — 当前 Phase 状态
│   │       └── regimeStore.ts          🟢 新增
│   └── package.json                    🟡 加依赖（react-markdown 已有）
│
├── bot/
│   ├── commands/                🟡 扩展 — 加 /journal /regime /phase 命令
│   ├── telegram_bot.py          保留（主推送渠道）
│   └── ...                      其他保留
│
├── data_provider/
│   ├── __init__.py              🟡 小改 — 调整美股路由优先级
│   ├── longbridge_fetcher.py    保留
│   ├── yfinance_fetcher.py      保留
│   ├── akshare_fetcher.py       保留（A 股用，侧功能）
│   ├── tushare_fetcher.py       保留
│   ├── efinance_fetcher.py      保留（A 股用）
│   ├── alpaca_fetcher.py        🟢 新增 — 主美股数据源（Benzinga News + premarket）
│   ├── finnhub_fetcher.py       🟢 新增 — 宏观日历 + 分析师评级
│   └── options_chain.py         🟢 新增 — 期权链抓取（基于 yfinance）
│
├── docker/                      保留
├── New-docs/                    🟢 已就位 — architecture/ modules/ design/ user-guide/ deployment/ configuration/ contributing/ integrations/ phase0/ archive/ 子目录
├── docs/CHANGELOG.md            保留（AGENTS.md §1 硬规则 + 自动化依赖）
├── patch/                       保留
├── scripts/
│   ├── fetch_tushare_stock_list.py  保留（A 股用）
│   ├── generate_index_from_csv.py   保留
│   ├── init_journal_schema.py       🟢 新增
│   └── rebuild_trades.py            🟢 新增
├── sources/                     保留（图片 / banner）
│
├── src/
│   ├── agent/
│   │   ├── orchestrator/        保留 — 多 Agent 编排
│   │   ├── skills/              🟡 扩展 — 新增三个期权 skill
│   │   ├── tools/
│   │   │   ├── get_portfolio_snapshot.py   🟡 扩展 — 支持期权字段
│   │   │   ├── get_journal_snapshot.py     🟢 新增
│   │   │   ├── get_regime_score.py         🟢 新增
│   │   │   ├── get_option_chain.py         🟢 新增
│   │   │   └── ...
│   │   └── prompts/             🟡 扩展 — 加期权专用 prompt
│   │
│   ├── core/
│   │   ├── config_registry.py   🟡 扩展 — 加 CURRENT_PHASE / WATCHLIST 等新字段
│   │   ├── trading_calendar.py  保留
│   │   └── ...
│   │
│   ├── journal/                 🟢 全新目录
│   │   ├── __init__.py
│   │   ├── instruments.py        — OCC 期权代码解析
│   │   ├── csv_parser.py         — Moomoo CSV 解析
│   │   ├── matcher.py            — FIFO 配对算法
│   │   ├── analytics.py          — Reality Test / Health Check / Stats
│   │   ├── monthly_review.py     — AI 月度复盘
│   │   ├── folder_watcher.py     — ~/Daily-Stock-Inbox/ 文件夹监听
│   │   ├── storage.py            — DB CRUD
│   │   └── tests/
│   │
│   ├── regime/                  🟢 全新目录
│   │   ├── __init__.py
│   │   ├── classifier.py         — 六维度打分主函数
│   │   ├── data_fetcher.py       — 数据拉取
│   │   ├── scorers.py            — 六个打分函数
│   │   ├── morning_brief.py      — Telegram 晨报
│   │   ├── storage.py
│   │   └── tests/
│   │
│   ├── lab/                     🟢 全新目录
│   │   ├── leap_explorer.py      — LEAP 候选筛选
│   │   ├── shadow_trades.py      — 虚拟交易跟踪
│   │   ├── backtest_replayer.py  — 反事实 LEAP 回测
│   │   └── trend_scanner.py      — 周线 / 月线趋势扫描（Phase 2 用）
│   │
│   ├── breakout/                🟢 全新目录
│   │   ├── filter.py             — 四层过滤决策树
│   │   ├── volume_check.py
│   │   ├── timeframe_check.py
│   │   ├── rs_check.py
│   │   └── retest_tracker.py
│   │
│   ├── options/                 🟢 全新目录
│   │   ├── black_scholes.py      — BS 定价和 Greek
│   │   ├── chain_analyzer.py
│   │   └── iv_rank.py
│   │
│   ├── reports/                 🟡 扩展
│   │   ├── renderer.py          保留（原 Jinja 渲染引擎）
│   │   └── ...
│   │
│   ├── formatters.py            保留
│   ├── config.py                保留
│   ├── logging_config.py        保留
│   └── ...
│
├── strategies/                  🟡 扩展 — 加期权相关 YAML
│   ├── bull_trend.yaml          保留
│   ├── ma_cross.yaml            保留
│   ├── chan.yaml                保留
│   ├── option_trader.yaml       🟢 新增
│   ├── leap_explorer.yaml       🟢 新增
│   ├── trend_follower.yaml      🟢 新增
│   └── ...
│
├── templates/                   🟡 扩展 — 加期权模板
│   ├── decision_dashboard.md.j2         保留（A 股用）
│   ├── market_review.md.j2              保留
│   ├── option_decision.md.j2            🟢 新增
│   ├── daily_health_check.md.j2         🟢 新增
│   ├── weekly_reality_test.md.j2        🟢 新增
│   ├── monthly_retrospective.md.j2      🟢 新增
│   └── regime_morning_brief.md.j2       🟢 新增
│
├── tests/                       🟡 扩展 — 加单元测试
│
├── .env.example                 🟡 扩展 — 加新字段示例
├── AGENTS.md                    🟡 扩展 — 加新 skill 说明
├── CLAUDE.md                    🟡 扩展 — 加项目上下文
├── main.py                      保留（入口不动）
├── analyzer_service.py          保留
├── server.py                    保留
├── webui.py                     保留
├── README.md                    🟡 改写 — 项目定位调整（美股期权为主）
├── requirements.txt             🟡 扩展 — 加新依赖
└── review.md                    保留（原作者的自我诊断，保留作参考）
```

**颜色图例**：
- 🟢 新增
- 🟡 改造 / 扩展
- 空白 保留不动

---

## 1. 模块依赖图

```
┌────────────────────────────────────────────────────┐
│                   Frontend Layer                    │
│  apps/dsa-web/                                      │
│  HomePage • PortfolioPage • JournalPage • LabPage   │
│  JourneyPage • RegimePage • StockPage              │
└─────────────────┬──────────────────────────────────┘
                  │ HTTP /api/v1/*
┌─────────────────▼──────────────────────────────────┐
│              API Layer                              │
│  api/app.py + api/routers/*                         │
│  journal / regime / lab / phase / option            │
└─────────────────┬──────────────────────────────────┘
                  │
      ┌───────────┼───────────┬───────────┐
      │           │           │           │
┌─────▼──┐  ┌────▼───┐  ┌────▼───┐  ┌───▼────┐
│Journal │  │Regime  │  │Lab     │  │Options │
│src/    │  │src/    │  │src/    │  │src/    │
│journal/│  │regime/ │  │lab/    │  │options/│
└──┬─────┘  └───┬────┘  └───┬────┘  └───┬────┘
   │            │            │            │
   │  ┌─────────┼────────────┤            │
   │  │         │            │            │
   │  │    ┌────▼────────────▼────────────▼──┐
   │  │    │       Agent & Tools              │
   │  │    │  src/agent/skills/{3 new}        │
   │  │    │  src/agent/tools/{4 new}         │
   │  │    │  + 原有 orchestrator             │
   │  │    └─────────────┬────────────────────┘
   │  │                  │
   │  │    ┌─────────────▼─────────────────┐
   │  │    │   Data Provider Layer         │
   │  │    │  data_provider/               │
   │  │    │  alpaca / finnhub / yfinance  │
   │  │    │  / longbridge / akshare       │
   │  │    └───────────────────────────────┘
   │  │
   │  └──────────────────────┐
   ▼                         ▼
┌──────┐   ┌────────────────────────┐
│ DB   │   │   Bot Layer            │
│SQLite│   │   bot/telegram_bot.py  │
└──────┘   │   +/journal +/regime   │
           └────────────────────────┘
```

---

## 2. 改造工作量估算（按模块）

| 模块 | 改造类型 | 代码行数估算 | 工作量（按 3-5 小时/天） |
|--|--|--|--|
| `src/journal/` 新建 | 全新 | ~1500 行 | 3-4 天 |
| `src/regime/` 新建 | 全新 | ~800 行 | 2 天 |
| `src/breakout/` 新建 | 全新 | ~500 行 | 1 天 |
| `src/lab/` 新建 | 全新 | ~600 行 | 2 天 |
| `src/options/` 新建 | 全新 | ~400 行 | 1 天 |
| 三个 Agent Skill YAML | 新增 | ~300 行 | 1 天 |
| `api/routers/*` 新增 | 新增 | ~400 行 | 1 天 |
| `data_provider/alpaca_fetcher.py` | 新增 | ~300 行 | 1 天 |
| `apps/dsa-web/` 前端改造 | 扩展 | ~2000 行 TSX | 4-5 天 |
| Templates | 扩展 | ~500 行 Jinja | 1 天 |
| `config_registry.py` 扩展 | 小改 | ~100 行 | 0.5 天 |
| README + 文档 | 改写 | N/A | 1-2 天 |
| **总计** | | **~7000 行** | **约 20-25 天** |

按每周 3-4 天、每天 3-5 小时的节奏，**需要 6-8 周完成**。这和 Phase 0 的 6 周预算吻合。

---

## 3. 哪些能力是"免费获得"的（不用自己写）

来自原 repo 的现成能力（直接用，节省大量时间）：

| 能力 | 来自 | 节省时间 |
|--|--|--|
| 多 LLM 路由 + Multi-key 负载均衡 | LiteLLM + 原项目 wrapper | 1-2 天 |
| Telegram / Discord / Slack / 企微 / 飞书推送 | `bot/*` | 2-3 天 |
| Markdown → 图片转换 | `scripts/` + `markdown-to-file` 集成 | 0.5 天 |
| Jinja2 报告模板引擎 + 完整性校验 | `src/reports/` | 1-2 天 |
| FastAPI + CORS + 认证 | `api/app.py` + `server.py` | 1 天 |
| React + Tailwind + 双主题 UI 框架 | `apps/dsa-web/` | 2-3 天 |
| 数据源 fallback 链抽象 | `data_provider/` | 2 天 |
| Agent orchestrator + 多阶段编排 | `src/agent/orchestrator/` | 3-5 天 |
| 持仓 DB schema + 券商 CSV 解析器 | Portfolio 模块 | 1-2 天 |
| GitHub Actions 免费 cron | `.github/workflows/daily_analysis.yml` | 0.5 天 |
| Trading Calendar（交易日判断） | `src/core/trading_calendar.py` | 1 天 |
| 配置中心（env / web settings 双向同步） | `src/core/config_registry.py` | 1-2 天 |

**估算**：原项目直接"送"了大约 **15-25 天的工作量**。如果从零开始写所有这些，得 1.5 倍时间。

---

## 4. 数据流（典型场景）

### 场景 A：早晨 09:00 Regime 晨报

```
GitHub Actions cron (13:00 UTC = 09:00 EDT)
    │
    ▼
python -m src.regime.cli
    │
    ├─► src/regime/data_fetcher.py
    │       ├─► data_provider/alpaca_fetcher   (SPY premarket)
    │       ├─► data_provider/yfinance_fetcher (VIX / 板块 ETF)
    │       └─► data_provider/finnhub_fetcher  (宏观日历 / 财报)
    │
    ├─► src/regime/scorers.py  (六维度打分)
    │
    ├─► src/regime/storage.py  (写入 regime_scores 表)
    │
    └─► src/regime/morning_brief.py
            └─► bot/telegram_bot.py  (format + send)
                                     │
                                     ▼
                                Telegram 客户端
```

### 场景 B：盘后 CSV 导入 + 复盘

```
用户 Moomoo 导出 CSV → 拖到 ~/Daily-Stock-Inbox/
    │
    ▼
src/journal/folder_watcher.py  (watchdog 监听)
    │
    ├─► src/journal/csv_parser.py   (解析 735 行 → orders)
    ├─► src/journal/storage.py      (入库 orders)
    ├─► src/journal/matcher.py      (FIFO 配对 → trades)
    ├─► src/journal/analytics.py    (Daily Health Check)
    │
    └─► bot/telegram_bot.py         (推送"今日体检")

同时：
apps/dsa-web/ Journal 页面自动刷新（SWR）
```

### 场景 C：用户在前端问"帮我分析 NVDA"

```
User 在 Agent 聊天输入: "/ask option_trader NVDA"
    │
    ▼
apps/dsa-web → POST /api/v1/agent/chat
    │
    ▼
api/routers/agent.py
    │
    ▼
src/agent/orchestrator/
    │
    ├─► 装载 strategies/option_trader.yaml
    ├─► 调用 src/agent/tools/get_portfolio_snapshot  (用户持仓)
    ├─► 调用 src/agent/tools/get_regime_score        (今日 Regime)
    ├─► 调用 src/agent/tools/get_option_chain        (NVDA 期权链)
    ├─► 调用 src/agent/tools/analyze_technical       (K 线结构)
    ├─► 调用 src/agent/tools/news_search             (近期新闻)
    │
    ▼
LLM (Gemini / Claude 路由) → 生成 decision dashboard
    │
    ▼
templates/option_decision.md.j2 渲染
    │
    ▼
前端显示 + 可选发 Telegram
```

---

## 5. 数据库 Schema 总览

**原项目已有的表**（保留不动）：
- `portfolios`（持仓主表）
- `portfolio_events`（交易事件）
- `portfolio_snapshots`（每日快照）
- `analysis_results`（分析历史）
- `chat_sessions`（Agent 对话）
- `chat_messages`
- `backtest_results`

**新增的表**：
- `trade_imports` — CSV 导入审计
- `orders` — Moomoo 订单（每行 1 个订单，期权/正股通用）
- `instruments` — 标的元数据（OCC 期权代码解析结果）
- `trades` — 配对后的完整交易（FIFO 结果）
- `shadow_trades` — 虚拟交易
- `regime_scores` — 每日 Regime 评分
- `health_checks` — 每日体检结果
- `monthly_reviews` — 月度复盘
- `phase_state` — 当前 Phase + 进度
- `option_chains_cache` — 期权链快照缓存（避免反复调 yfinance）

**关系**：

```
instruments (1) ─┐
                  ├─ (*) orders (*) ─┐
                  │                    ├─ FIFO 配对 → (*) trades
                  └─ (*) trades       │
                                      │
portfolios (1) ─ (*) portfolio_events (同步源)
                                      │
shadow_trades (独立，不和 trades 混)  │
                                      │
regime_scores ─ 按日期关联 ─ trades   │
monthly_reviews ─ 按年月关联 ─ trades │
phase_state ─ 单行表                  │
```

详细 schema 见 `05_JOURNAL_MODULE.md`。

---

## 6. 配置项扩展（`src/core/config_registry.py`）

新增字段（都标记为 Web 设置页可编辑）：

```python
# Phase 管理
CURRENT_PHASE: int = 0                    # 0/1/2/3
WATCHLIST: list[str]                      # 沿用原有，但文档标记"美股期权 watchlist"

# Regime
REGIME_MIN_SCORE: int = 55                # 低于此不交易
REGIME_BRIEF_ENABLED: bool = True         # 晨报开关
REGIME_BRIEF_TIME_ET: str = "09:00"       # 推送时间

# Journal
INBOX_DIR: str = "~/Daily-Stock-Inbox"
PROCESSED_DIR: str = "~/Daily-Stock-Processed"
CSV_FORMAT: str = "moomoo_us"             # 将来可扩展 ibkr / alpaca

# Breakout Filter
BREAKOUT_VOLUME_MIN: float = 1.5          # 最小 volume 倍数
BREAKOUT_TIMEFRAMES: list[str] = ["2min", "5min", "15min", "1day"]
BREAKOUT_RS_MIN: float = 0.3              # RS vs SPY 最小值
BREAKOUT_RETEST_WINDOW_MIN: int = 30      # Retest 等待窗口

# Lab
LAB_LEAP_DELTA_MIN: float = 0.70
LAB_LEAP_DELTA_MAX: float = 0.85
SHADOW_TRADES_ENABLED: bool = True

# AI Prompt 加层（覆盖原 TRADING_STYLE_PROMPT 逻辑）
PERSONAL_TRADING_STYLE: str = ""          # 用户自述风格（多行）
PHASE_CONTEXT_INJECTION: bool = True      # 自动把 Phase 信息注入 Agent Prompt
```

原有字段保留不动（如 `LITELLM_MODEL` / `AGENT_MODE` / 推送渠道等）。

---

## 7. 关键设计原则

1. **Extend, don't replace** — 所有功能先看能不能扩展现有模块，不行才新建
2. **Option-first but not option-only** — `is_option` 是字段维度，不是代码分叉
3. **Phase-gated features** — 新功能按 Phase 激活，Phase 0 只上核心 Mirror 层
4. **Skill-based strategies** — 新策略走 Agent skill 机制，不独立跑
5. **Reality Test as first-class** — 首页卡片，不藏子页面
6. **A/H stock as side feature** — 保留但不主动维护，README 明说
7. **User's truth > AI's prettiness** — AI 禁止客套话，数字精确引用
8. **Config in registry** — 所有新字段走 `config_registry.py`，不散落各处
9. **Templates for rendering** — 所有 Markdown 输出走 Jinja 模板，不硬编码
10. **Tests before Phase advance** — 进下一 Phase 前核心模块必须有单元测试

---

## 8. 与原项目 PR 合并的可能性（附注）

你说"能合并吗"——这里说清楚现状：

**可以的部分**：
- `src/journal/` / `src/regime/` / `src/lab/` / `src/breakout/` / `src/options/` 是**全新目录**，对 upstream 无侵入
- 新 Agent skill YAML 也不冲突
- 新 `templates/*.md.j2` 不冲突
- `api/routers/journal.py` 等是新文件

**不太能合并的部分**：
- `HomePage.tsx` 改造（Reality Test 首页卡片）涉及产品定位分歧
- `config_registry.py` 新字段可以合，但 default 值可能不同
- `README.md` 改写（定位美股期权优先）upstream 不会接受
- `data_provider/__init__.py` 路由优先级调整需讨论

**建议**：保持 fork 但定期拉 upstream `main` 到 `upstream/main` 分支做 rebase。冲突集中在 `apps/dsa-web/src/pages/HomePage.tsx` 和 `config_registry.py`，需手动解决。其他都能 auto-merge。

**未来如果 upstream 接受**：可以先把中性的 PR 提过去（比如新 agent skill `option_trader` 作为"可选功能"）。产品定位相关的改造就别提 PR 了。

---

## 下一步

读 `03_MIGRATION_PLAN.md` 看整体路线图。

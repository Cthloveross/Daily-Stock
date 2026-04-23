# Daily-Stock v4 Phase 0 · 使用手册

> 这份文档只回答一个问题：**我现在怎么用？**
>
> 适用人群：@Cthloveross 本人 / 任何 fork 了这个 repo 想跑美股期权 Mirror 层的人。
> 代码入口总览：[New-docs/phase0/README.md](README.md)。

---

## 0. TL;DR

**最小可用路径**（不配任何 API key，也不用 Telegram）：

```bash
# 一次性初始化
pip install -r requirements.txt
python -m scripts.init_journal_schema

# 把你的 Moomoo 交易历史 CSV 放进来
cp ~/Downloads/History-*.csv tests/fixtures/journal/moomoo_real_sample.csv

# 解析 → FIFO 配对 → Reality Test
python -m scripts.import_csv --csv tests/fixtures/journal/moomoo_real_sample.csv
python -m scripts.reality_test --top-n 5
```

跑完你会看到 3 个月总净利、Top 5 占比、**"去掉 Top 5 我实际剩多少"**——这是 Phase 0 的灵魂指标。

要 UI：后端 `uvicorn server:app --reload` + 前端 `cd apps/dsa-web && npm run dev`，浏览器开 http://localhost:5173 → 侧栏 `/journal`。

要 Telegram 晨报、月度 AI 复盘，才需要配 key。往下看。

---

## 1. 前置要求

| 工具 | 版本 | 必需？ |
|--|--|--|
| Python | 3.10+ | ✅ |
| Node | 20.19+（原 repo 要求） | 仅 UI 要 |
| Git | 任何 | ✅ |
| Moomoo 账户 | US cash/margin | ✅（数据来源）|
| Alpaca paper 账户 | 免费 | 可选（Regime d6 盘前维度 + 未来新闻） |
| Finnhub 免费账户 | 免费 | 可选（Regime d3 宏观日历）|
| Telegram Bot token + chat id | 免费 | 可选（晨报/通知） |
| Gemini 或 Anthropic API key | 按量付费 | 仅月度 AI 复盘要 |

**啥都没配**：照样能跑 Journal → Reality Test 全链（这已经是 80% 价值）。

---

## 2. 一次性装机（5 分钟）

```bash
# 在 repo 根
git pull                # 确保你拉的是最新
pip install -r requirements.txt

# 初始化数据库（幂等，随时可重跑）
python -m scripts.init_journal_schema
```

**可选** — 把 API keys 写到 `.env`（仓库 `.env` 被 gitignore，不会提交）。
**完整申请 + 配置步骤看 [API_KEYS_SETUP.md](API_KEYS_SETUP.md)**。下面只列 key 名：

```bash
# .env 最小配置（全都可选）
APCA_API_KEY_ID=...             # Alpaca paper key
APCA_API_SECRET_KEY=...
FINNHUB_API_KEY=...             # https://finnhub.io 免费注册
TELEGRAM_BOT_TOKEN=...          # @BotFather 拿
TELEGRAM_CHAT_ID=...            # 私聊 bot 后去 https://api.telegram.org/bot<TOKEN>/getUpdates 取
GEMINI_API_KEY=...              # 月度复盘用
# 或
ANTHROPIC_API_KEY=...

# Phase 0 新增字段（都有默认，不用动；想改就 uncomment）
# CURRENT_PHASE=0
# PERSONAL_TRADING_STYLE="我是日内期权交易者，偏好 1-3 DTE call，..."
# REGIME_MIN_SCORE=55
# INBOX_DIR=~/Daily-Stock-Inbox
```

---

## 3. 每日工作流

**早上 09:00 ET 之前**（如果 GitHub Actions 已配 Secrets 并启用了 `regime_brief.yml`）：
→ Telegram 收到 *Regime Brief*：今天 Score 60 standard / 40 cautious / 20 no_trade。

没配自动化也行，手动跑：
```bash
python -m src.regime.cli --verbose     # 立刻打印今日 Regime
# 或
python -m src.regime.morning_brief --send   # 额外推 Telegram
```

**盘中**：用 `/regime` / `/journal today` bot 命令（如果装了 Telegram bot）查今日状态。

**盘后**：把 Moomoo CSV 倒过来，有两种姿势：

### 姿势 A — 文件夹 watcher（自动）

```bash
# 终端 1（常驻）
pip install watchdog
python -m scripts.run_journal_watcher
```

然后每天盘后把 Moomoo 导出的 `History-YYYYMMDD.csv` 拖到 `~/Daily-Stock-Inbox/`。watcher 自动：
- 解析 → 入库 → FIFO 配对 → 移到 `~/Daily-Stock-Processed/`
- Telegram 推送 "✅ Journal 导入 History-xxx.csv：新增 42 单 / 跳过 0 / 总 38 笔 trades"

**同一 CSV 丢第二次**：watcher 识别 sha256 重复，跳过（不会产生重复数据）。

### 姿势 B — 手动 CLI

```bash
python -m scripts.import_csv --csv ~/Downloads/History-20260420.csv
```

### 看今日体检
前端 http://localhost:5173/journal → Overview tab
或 Telegram：
```
/journal today
```

---

## 4. 每周工作流

周日 18:00 左右：

```bash
python -m scripts.reality_test --top-n 5 --since 2026-04-13
```

终端打印：
- 本周 closed trades 数
- 净 PnL
- Top 5 占比
- **去掉 Top 5 的净 PnL**
- 中位数单笔 PnL
- DTE 桶分布与胜率

问自己三件事（per `BREAKOUT_FILTER_PLAYBOOK.md` 原则）：
1. 去掉最赚的 5 笔，我还盈利吗？
2. 0-3DTE vs 4+DTE 胜率谁高？
3. 追突破 vs retest 谁更赚？

也可以前端 `/journal` → **Trades tab** 过滤 `style=breakout_chase` / `status=closed` 看每笔明细。

---

## 5. 每月工作流

每月 1 号 00:00 ET，GitHub Actions `monthly_review.yml` cron 自动跑上个月的复盘，结果进 `journal_monthly_reviews` 表。

手动触发（任何时候）：
```bash
python -m scripts.generate_monthly_review --month 2026-03
# --dry-run    只计算统计、不调 LLM
```

前端看：`/journal` → **Reviews tab** 选月份 → AI 复盘（800-1400 字中文，含 5 个固定章节）。

如果 `.env` 里有 `PERSONAL_TRADING_STYLE`，prompt 会对照你声明的风格指出偏离。

---

## 6. 前端 URL 速查

本地跑 `npm run dev` 后：

| URL | 内容 |
|--|--|
| `http://localhost:5173/` | Today 首页：Reality Test + Regime Score 双卡 + 股票查询 |
| `http://localhost:5173/journal` | Overview / Trades / Reality / Reviews / Import 五 tab |
| `http://localhost:5173/regime` | 今日 Regime + 30 天历史图 + Breakout 信号列表 |
| `http://localhost:5173/portfolio` | 原仓库 Portfolio 页（未动） |
| `http://localhost:5173/chat` | 原 Agent 对话页 — 打 `/ask option_trader NVDA` 触发新 skill |

生产部署：后端 `uvicorn server:app --host 0.0.0.0`，前端 `npm run build` 把产物写到 `../../static/` 由 FastAPI 托管，走同域 `/api/v1/*`。

---

## 7. Telegram bot 命令速查

如果你把 bot 接到 Telegram（用原仓库 `main.py --serve` 或 `bot/` 下的 stream handler）：

```
/journal           今日 Daily Health Check 摘要
/journal today     同上
/journal reality   今日 Reality Test（不限日期）
/regime            今日 Regime Score + 六维度 breakdown
/regime 2026-04-17 查某天
/phase             当前 Journey Phase + 在此 phase 天数
```

中文别名也可：`日志` / `今日体检` / `市场环境` / `阶段`。

---

## 8. GitHub Actions（把自动化搬上云）

如果你想让 Regime 晨报 / 月度复盘每天自动跑：

1. Repo Settings → Secrets 加：
   - `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`（可选）
   - `FINNHUB_API_KEY`（可选）
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（晨报推送必需）
   - `GEMINI_API_KEY` 或 `ANTHROPIC_API_KEY`（月度复盘必需一个）
2. Repo Settings → Variables（非 secret 的）：
   - `WATCHLIST=NVDA,TSLA,AAPL,SPY,QQQ,...`
   - `PERSONAL_TRADING_STYLE=我是日内期权交易者...`
3. Actions → `Regime Morning Brief` → **Run workflow** 手动 dispatch 一次 smoke test
4. 同样对 `Monthly Journal Retrospective` 做 dispatch

`regime_brief.yml` 自带 ET 小时 guard（双 cron for DST），不会重复推。

---

## 9. CLI 速查表

一张表，常用命令 copy-paste：

```bash
# 初始化（幂等，任何时候可重跑）
python -m scripts.init_journal_schema

# 导入 Moomoo CSV + 立刻 FIFO 重配
python -m scripts.import_csv --csv PATH [--broker moomoo_us] [--no-rebuild]

# 只重跑 FIFO 配对（不导入新 CSV）
python -m scripts.rebuild_trades

# Reality Test
python -m scripts.reality_test [--top-n 5] [--since 2026-01-01]

# Breakout 标签回填
python -m src.breakout.backfill_trade_style
python -m src.breakout.backfill_fake_breakout

# Regime 今日
python -m src.regime.cli                 # 计算 + 入库
python -m src.regime.cli --no-save       # 只看不存
python -m src.regime.cli --date 2026-04-17

# Regime 历史回补
python -m src.regime.backfill --days 90

# Regime Telegram 晨报
python -m src.regime.morning_brief --format-only    # 只打印
python -m src.regime.morning_brief --send           # 推 Telegram

# AI 月度复盘
python -m scripts.generate_monthly_review --month 2026-03
python -m scripts.generate_monthly_review --month 2026-03 --dry-run

# Folder watcher（常驻进程）
python -m scripts.run_journal_watcher

# 跑测试
python -m pytest src/options src/journal src/regime src/breakout \
                 src/agent/tools/tests api/v1/tests bot/commands/tests
```

---

## 10. REST API 速查

常用端点，都以 `/api/v1/` 前缀：

```
GET  /journal/reality-test?top_n=5&since=2026-04-01
GET  /journal/trades?symbol=NVDA&status=closed&style=breakout_chase&page=1&per_page=50
GET  /journal/trades/{id}
PATCH /journal/trades/{id}              {user_notes, emotional_state, trade_style}
GET  /journal/health-check?date=2026-04-17
GET  /journal/stats?days=90
POST /journal/import                    multipart/form-data file
GET  /journal/reviews
GET  /journal/reviews/2026/3
POST /journal/reviews/2026/3/generate   body {"dry_run": false}

GET  /regime/today
GET  /regime/history?days=30
POST /regime/recompute                  60s cooldown，会 429

GET  /breakout/signals?limit=20&only_fake=true
```

前后端都用 cookie session（登录后 axios `withCredentials: true` 自动带）；没登录 401 → 重定向 `/login`。

---

## 11. 常见问题

**Q: 我的 Moomoo CSV 导入后 orders 数字对得上，但 Reality Test 显示 "Total closed trades: 0"。**
A: FIFO 配对需要成对的 Buy + Sell。如果你只导了"本月"而未平仓的单都在，它们会进 `journal_trades` 但 `status='open'`，不计入 Reality Test。倒一份完整 90 天试试。

**Q: Telegram 没收到晨报。**
A: 先跑 `python -m src.regime.morning_brief --send` 本地测。如果本地也没收到 → `.env` 里 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 错了或没生效。用 `python -c "from src.config import get_config; c=get_config(); print(c.telegram_bot_token, c.telegram_chat_id)"` 验证。

**Q: `/journal today` 返回"今日尚未生成体检数据"。**
A: Daily Health Check 现在不是自动生成的（Phase 0 没上日终 cron）。Phase 1 会加。临时替代：用 `/journal reality` 或前端 `/journal` Overview tab。

**Q: Regime Score 全是 0。**
A: 八成是没装 yfinance / pandas。跑 `pip install -r requirements.txt` 再试。如果装了但还是 0，`python -m src.regime.cli --verbose` 打出 snapshot，看哪维度缺数据。

**Q: 前端 TradingView 图不出来。**
A: 国内网络可能卡 `s3.tradingview.com`。组件会 console.warn 但不 crash。科学上网或 Phase 1 替成另一个 widget。

**Q: 我想改 0DTE 配额 / Regime 阈值。**
A: 在 WebUI `/settings` → Phase 0 分类下改，或直接编辑 `.env` 里的字段（见 Stage 0 文档）。所有 Phase 0 字段都在 `config_registry.py` 注册，重启服务即生效。

**Q: 我想从 Phase 0 进 Phase 1。**
A: 先跑完 [PHASE_0_EXIT_REVIEW.md §八](PHASE_0_EXIT_REVIEW.md#八签字) 的 4 项签字，然后 `.env` 加 `CURRENT_PHASE=1` + `SHADOW_TRADES_ENABLED=true`。Phase 1 的 Lab 模块目前只有骨架，具体实现是下一批工作。

---

## 12. 下一步（Phase 1 启动前）

- [ ] 把真实 Moomoo CSV drop 一次，跑 `reality_test` 看数字对不对（对照 `HEALTH_CHECK_REPORT.md` ±10%）
- [ ] 至少收到一份 Telegram 晨报
- [ ] 生成一份 2026-03 月度 AI 复盘，人工 review 确认文案风格（无加油话、数据精确）
- [ ] 你主动说一句"我想试 LEAP 了" — 这是 Phase 1 的启动信号
- [ ] 打 tag `git tag -a v0.phase0 -m "..."`（手动，因为 CLAUDE.md 硬规则：git tag / push 需用户确认）

---

**一句话结语**：这个 Phase 0 的目的不是让你**马上**变成成熟交易者。是让**数据**变成你的诚实教练。跑起来，看数字，感受冲击。然后再谈 Phase 1。

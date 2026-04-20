# Setup Checklist — 开工前必做的准备

> **用途**：Phase 0 Week 1 开工前，确保所有账户、API key、环境都准备好。  
> **预估时间**：1.5-2 小时一次搞完

---

## 一、账户注册清单

### 1.1 Alpaca Paper Trading Account（免费，关键）

**为什么需要**：
- 免费 Benzinga News Feed（$177/月 Pro 版的数据）
- 免费 2min K 线（Regime Classifier 用）
- 免费股票 snapshot 和 premarket 数据

**步骤**：
1. 访问 https://alpaca.markets → Sign Up
2. 选 **Paper Trading**（不做实盘，只拿数据）
3. 填基本信息（中国身份可开 paper，不需要 SSN）
4. 邮箱验证后登录
5. Dashboard → **API Keys** → Generate new keys
6. 保存 `APCA_API_KEY_ID` 和 `APCA_API_SECRET_KEY`

**重要**：Paper 账号的数据 API 和实盘一样，完全够用。

---

### 1.2 Finnhub Account（免费）

**为什么需要**：宏观日历、分析师评级变动、股票新闻补充

**步骤**：
1. 访问 https://finnhub.io → Sign Up
2. 免费 tier 够用（60 req/min）
3. 在 Dashboard 拿到 `FINNHUB_API_KEY`

---

### 1.3 Telegram Bot（免费）

**为什么需要**：所有推送（Daily Health Check、Regime 晨报、Breakout 提示）

**步骤**：
1. Telegram 搜索 `@BotFather` → 发送 `/newbot`
2. 起个名字（如 `daily_stock_bot`）
3. 保存返回的 `TELEGRAM_BOT_TOKEN`
4. 给 bot 发一条消息（任意内容）
5. 访问 `https://api.telegram.org/bot<TOKEN>/getUpdates`，找到 `chat.id`，保存为 `TELEGRAM_CHAT_ID`

---

### 1.4 ScrapeCreators Trump Truth Social（$20/月，可选）

**为什么需要**：Trump 关税贴实时监控，盘中突发政策信号

**建议**：**Phase 0 先不订**，Phase 1 再说。$20 不多但如果用不上浪费。

如果决定订：https://scrapecreators.com → 选 Pro 套餐 → 保存 `SCRAPECREATORS_API_KEY`

---

### 1.5 其他（完全免费，无需注册）

以下都不需要 API key：
- yfinance（Python 库）
- SEC EDGAR RSS（仅需 User-Agent 带邮箱）
- Federal Reserve RSS
- Polymarket API

---

## 二、本地环境准备

### 2.1 Python 环境

```bash
# 推荐 Python 3.11
python --version  # 确认 3.11 或 3.12

# 进项目目录
cd ~/Desktop/daily_stock_analysis

# 新建 venv（如果还没有）
python -m venv .venv
source .venv/bin/activate  # Mac/Linux

# 安装核心依赖
pip install -U yfinance fastapi uvicorn \
    pydantic pandas numpy scipy \
    sqlalchemy alembic \
    python-dotenv requests \
    watchdog feedparser praw \
    python-telegram-bot \
    litellm google-generativeai
```

### 2.2 文件夹结构

```bash
mkdir -p ~/Daily-Stock-Inbox       # Moomoo CSV 放这里
mkdir -p ~/Daily-Stock-Processed   # 处理完的 CSV 移这里
mkdir -p ~/Daily-Stock-Archive     # 长期归档
```

### 2.3 `.env` 配置

在项目根目录新建 `.env`（**不要 commit**）：

```bash
# ============ LLM ============
GEMINI_API_KEY=AIzaSy...
# 可选备用
ANTHROPIC_API_KEY=sk-ant-...

# ============ 数据源 ============
APCA_API_KEY_ID=PK...
APCA_API_SECRET_KEY=...
APCA_DATA_URL=https://data.alpaca.markets

FINNHUB_API_KEY=...

# 可选，Phase 1 再加
# SCRAPECREATORS_API_KEY=...

# ============ 推送 ============
TELEGRAM_BOT_TOKEN=1234567890:AAH...
TELEGRAM_CHAT_ID=123456789

# ============ 本地路径 ============
INBOX_DIR=~/Daily-Stock-Inbox
PROCESSED_DIR=~/Daily-Stock-Processed
ARCHIVE_DIR=~/Daily-Stock-Archive

# ============ 数据库 ============
DATABASE_URL=sqlite:///./data/daily_stock.db

# ============ 交易系统参数 ============
WATCHLIST=MU,TSLA,PLTR,NVDA,QQQ,AMZN,AMD,MSFT,ORCL,META,IWM,SLV,RKLB,GOOGL,AVGO
CURRENT_PHASE=0
REGIME_MIN_SCORE=55

# ============ 个人风格（多行） ============
TRADING_STYLE_PROMPT="我的交易现状与目标（美股期权 + 正股，2026-04）：
【现状 — 诚实描述】
- 交易频率极高，日均 15 笔，99% 是期权
- 85% 是 0-3 DTE 短期期权，方向性买方
...（见 PROJECT_VISION_v3.md 第 6 节完整版）"
```

### 2.4 确认 `.gitignore` 包含

```
.env
.venv/
data/
*.sqlite
*.db
~/Daily-Stock-Inbox/*
~/Daily-Stock-Processed/*
```

---

## 三、Moomoo 端准备

### 3.1 确认你知道导出路径

在 Moomoo Desktop 或 App 里走一遍：
1. 打开 History / Trade History
2. 选择日期范围（今天）
3. 点击导出 CSV
4. 确认文件保存到 `~/Downloads/` 或指定位置

### 3.2 首次手动跑一遍

1. 导出过去 3 个月的交易记录
2. 保存到 `~/Daily-Stock-Inbox/`
3. 这份数据会作为项目的**历史基线**

---

## 四、Moomoo Desktop → Inbox 的流畅化（可选）

**目标**：把"导出 CSV" 从 10 秒压到 3 秒。

### 4.1 Mac Shortcut 方案

1. 打开 macOS **Shortcuts** 应用
2. 新建一个 Shortcut `Export Moomoo`
3. 添加步骤：
   - Open App：Moomoo
   - Delay 2 seconds
   - Run AppleScript（或 Keyboard Maestro 点击序列）
4. 放到 Menu Bar

**更简单的替代**：在 Moomoo 导出对话框里把默认路径改成 `~/Daily-Stock-Inbox/`，之后每次导出自动进目录。

### 4.2 验证 watchdog 工作

```bash
# 项目里跑
python -m src.journal.folder_watcher
# 看到 "Watching ~/Daily-Stock-Inbox/" 即可

# 另起终端，cp 一个假 CSV 进去
cp some_test.csv ~/Daily-Stock-Inbox/History-test.csv

# 第一个终端应该立刻报告 "处理中..."
```

---

## 五、Phase 0 启动检查表（全做完才开工）

打开项目 Issue 或 Notion，把这个复制进去打勾：

```
□ Alpaca Paper 账户已开通，API key 在 .env
□ Finnhub 免费账户已开通，API key 在 .env
□ Telegram Bot 已建，给 bot 发过消息
□ TELEGRAM_CHAT_ID 已拿到
□ 本地 Python 3.11 venv 建好，依赖装完
□ ~/Daily-Stock-Inbox/ 和 ~/Daily-Stock-Processed/ 已创建
□ .env 文件完整（含 WATCHLIST、TRADING_STYLE_PROMPT）
□ .gitignore 已加 .env 和 data/
□ Moomoo 3 个月历史 CSV 已导出到 Inbox
□ 看过 PROJECT_VISION_v3.md、EVOLUTION_ROADMAP.md、TRADING_SYSTEM_SPEC.md
□ 理解并接受 Phase 0 是"不改变交易方式，只看数据 6 周"
□ 愿意每天早上 09:00 ET 看 Regime 晨报（2 分钟）
□ 愿意每天盘后 5 分钟看 Daily Health Check
```

全部打勾 → 开工 → 执行 `PHASE_0_WEEK_1_SPRINT.md`

---

## 六、如果某个账户开不了

### Alpaca 开不了
- 中国身份理论上能开 paper，但有时审核严
- 备选：用 **Polygon.io 免费 tier**（只能拿 EOD，不能实时，但 Regime Classifier 够用）
- 再备选：直接用 yfinance（delay 15 分钟，晨报时效性差一点）

### Finnhub 开不了
- 直接跳过，非必需
- 宏观日历退而求其次用 `pandas_market_calendars` + 硬编码关键事件

### Telegram 在国内用不了
- 备选：企业微信机器人 / 飞书机器人
- 再备选：邮件推送（用现有的 SMTP 配置）

---

## 七、预期一次性成本

| 项 | 成本 |
|--|--|
| 时间 | 1.5-2 小时 |
| 金钱 | $0（Phase 0 全免费） |
| Phase 1 之后可能新增 | $20/月 ScrapeCreators |

---

**全部准备好 → 进入 `PHASE_0_WEEK_1_SPRINT.md`**
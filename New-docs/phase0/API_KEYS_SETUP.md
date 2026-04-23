# API Keys 配置完整清单

> 这份文档只回答：**配什么 / 怎么配 / 在哪配**。
> 所有 Phase 0 可能用到的外部 API，按"必需度"排序。每项给注册链接、具体步骤、填到哪里、怎么验证。

---

## TL;DR 清单

按重要度从上到下：

| # | API | 必需度 | 成本 | 影响 |
|--|--|--|--|--|
| 1 | **yfinance** | 🟢 必需 | $0（Python 包）| 整个 Regime 的四个维度 + Journal 行情 |
| 2 | **Alpaca Paper** | 🟡 强烈建议 | $0 | Regime d6 盘前活跃度 + 未来 Benzinga 新闻 |
| 3 | **Finnhub** | 🟡 强烈建议 | $0（60 req/min） | Regime d3 宏观事件惩罚（FOMC/CPI/NFP/财报）|
| 4 | **Telegram Bot** | 🟡 强烈建议 | $0 | 每日晨报 + 月度复盘 + watcher 通知 |
| 5 | **Gemini** *(已配)* | 🟢 LLM 至少要一个 | 按量付费（小量 ~$0/月）| AI 月度复盘 |
| 6 | **Anthropic** | ⚪ 可选（Gemini 备份） | 按量付费 | LiteLLM fallback |
| 7 | **ScrapeCreators** | ⚪ Phase 1 才用 | $20/月 | Trump Truth Social 实时抓取 |

**最小可用路径**：只配 `#1 yfinance`（装包）+ `#5 GEMINI_API_KEY`（已有）就能跑 Journal 全链 + 月度复盘。**`#2-4` 都是增强**，不配也不会阻塞。

---

## 1. yfinance（必需，已自带）

### 配什么
Python 包，不需要 API key。

### 怎么配
```bash
pip install -r requirements.txt   # yfinance 已经在里面
```

### 验证
```bash
python -c "import yfinance as yf; print(yf.Ticker('SPY').history(period='5d').tail(2))"
```
能打印最近 2 天 SPY OHLC 就 OK。

国内网络可能会超时 → 装 VPN 或用公司网络。

---

## 2. Alpaca Paper Account（强烈建议）

### 配什么
两个 key：`APCA_API_KEY_ID` + `APCA_API_SECRET_KEY`

### 注册（5 分钟）

1. 打开 https://alpaca.markets
2. 右上角 **Sign Up** → 填邮箱 / 密码（不需要 SSN / 不需要真实身份认证，因为我们只用 paper trading）
3. 登录后左侧菜单 → **Paper Trading** → Dashboard
4. 右上角 **View / Generate API Keys**
5. 点 **Generate** → 屏幕显示 key id + secret（**secret 只显示一次**，复制到安全的地方）

### 填到哪里

本地 `.env`（推荐）：
```bash
APCA_API_KEY_ID=PKXXXXXXXXXXXXX
APCA_API_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 验证
```bash
python -c "
from data_provider.alpaca_fetcher import AlpacaFetcher
a = AlpacaFetcher()
print('Configured:', a.configured)
print('SPY bars (5 min):', len(a.get_bars('SPY', '5Min', limit=5)), '根')
"
```
应该打印 `Configured: True` + 几根 bars。

### 为什么值得配
- 点亮 Regime 的 d6 维度（盘前活跃度 0-20 分）
- 未来 Phase 1 接 Benzinga News feed 也靠这个 key（免费覆盖华尔街级新闻）

---

## 3. Finnhub（强烈建议）

### 配什么
一个 key：`FINNHUB_API_KEY`

### 注册（2 分钟）

1. 打开 https://finnhub.io
2. 右上角 **Register Free** → 填邮箱 / 密码
3. 登录后跳到 Dashboard，**API key 直接显示在页面上**
4. 免费层 60 req/min，够用到天荒地老

### 填到哪里

本地 `.env`：
```bash
FINNHUB_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 验证
```bash
python -c "
from data_provider.finnhub_fetcher import FinnhubFetcher
from datetime import date
f = FinnhubFetcher()
print('Configured:', f.configured)
cal = f.get_economic_calendar(date.today(), date.today())
print(f'Today events: {len(cal)}')
"
```

### 为什么值得配
- 点亮 Regime 的 d3 维度（宏观惩罚，最高 -50 分）
- 不配的话今天就算 FOMC 日 d3 也恒为 0 → Regime 会**高估**"今天能交易"

---

## 4. Telegram Bot（强烈建议）

### 配什么
两个值：`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`

### 创建 Bot（3 分钟）

1. 在 Telegram 里搜索 **@BotFather** → 私聊
2. 发送：`/newbot`
3. BotFather 问你 bot 名字 → 填 `DailyStockPersonalBot` 或你喜欢的
4. 再问 username → 必须以 `_bot` 结尾，比如 `cthdsa_bot`
5. 它返回一段话，里面有 **API token**（`1234567890:ABCDEF...` 格式）→ 这就是 `TELEGRAM_BOT_TOKEN`

### 拿 chat_id（2 分钟）

**最简单办法**：
1. 在 Telegram 里搜你刚创建的 bot → 点 **Start**
2. 随便发条消息（"hello" 就行）
3. 浏览器打开：
   ```
   https://api.telegram.org/bot<你的TOKEN>/getUpdates
   ```
4. 返回的 JSON 里找 `"chat":{"id":123456789,...}` → 这个数字就是 `TELEGRAM_CHAT_ID`

**或者**在 Telegram 搜 `@userinfobot` → /start → 它直接告诉你你的 chat id。

### 填到哪里

本地 `.env`：
```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCDEF...
TELEGRAM_CHAT_ID=123456789
```

### 验证
```bash
python -c "
from src.config import get_config
from src.notification_sender.telegram_sender import TelegramSender
s = TelegramSender(get_config())
print('Configured:', s.is_configured())
print('Send test:', s.send_to_telegram('🧪 DSA connectivity test'))
"
```
如果 Telegram 里收到"🧪 DSA connectivity test" → 搞定。

### 为什么值得配
- 每日 09:00 ET Regime 晨报推送
- Watcher 导入 CSV 后自动通知
- bot 命令 `/journal` `/regime` `/phase` 互动

---

## 5. Gemini（你已配）

### 配什么
`GEMINI_API_KEY`（你 `.env` 第 57 行已经有了，跳过此节）

### 如果重新申请
1. https://aistudio.google.com/apikey
2. **Create API key** → 选 / 新建一个 Google Cloud project → 生成
3. 填到 `.env`：
   ```bash
   GEMINI_API_KEY=AQ.Ab8RN6IXD...
   ```

### 验证
```bash
python -m scripts.generate_monthly_review --month 2026-03 --dry-run
```
dry-run 不调 LLM 只打印 prompt；要实测 LLM 就去掉 `--dry-run`。

---

## 6. Anthropic Claude（可选，作 Gemini 备份）

### 配什么
`ANTHROPIC_API_KEY`

### 注册
1. https://console.anthropic.com → Sign up
2. 需要充值最少 $5 才能生成 API key（Anthropic 政策，不是我们设的）
3. **Settings → API Keys → Create Key** → 立刻复制（只显示一次）

### 填到哪里
```bash
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
```

### 为什么
LiteLLM 会在 Gemini 限流 / 出错时自动切 Claude。月度复盘长 Prompt 用 Claude 质量更好。

Gemini 够用就不配也行。

---

## 7. ScrapeCreators Truth Social（暂不需要）

Phase 1 新闻层才启用。要启用时：
1. https://scrapecreators.com → 订阅 $20/月
2. `.env` 加 `SCRAPECREATORS_API_KEY=xxx`
3. 但 Phase 0 的 news provider 还没实装，key 现在配了也没地方用

**现在跳过**。

---

## 在哪配（三种地方 + 优先级）

### 方式 A · 本地 `.env`（首选，开发/个人用）

路径：repo 根 `/Users/cth/Desktop/daily_stock_analysis/.env`

**已经存在**，你直接编辑追加就行：
```bash
# Regime 数据源
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
FINNHUB_API_KEY=...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# LLM（已有 Gemini）
ANTHROPIC_API_KEY=sk-ant-...    # 可选，备份
```

**重要**：`.env` 在 `.gitignore` 里，永不 commit。✅

### 方式 B · WebUI `/settings`（运行时热改）

登录后访问 http://localhost:5173/settings。原仓库的 `config_registry` 机制支持在 UI 改所有字段，**Phase 0 新增的字段**（Journal / Regime / Breakout / Lab / Options）也在 UI 里一个 "Phase 0 (v4)" 分类下能看到。

但 API keys 比较敏感，建议走 `.env`。

### 方式 C · GitHub Actions Secrets（上云自动化必需）

只有你想让 `regime_brief.yml` / `monthly_review.yml` cron 在 GitHub 上跑才需要。步骤：

1. repo 页面 → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret**，一个一个加：
   - `APCA_API_KEY_ID`
   - `APCA_API_SECRET_KEY`
   - `FINNHUB_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GEMINI_API_KEY` 或 `ANTHROPIC_API_KEY`（任一）
3. **Variables** tab（不是 secret 的）加：
   - `WATCHLIST` = `NVDA,TSLA,AAPL,SPY,QQQ,...`
   - `PERSONAL_TRADING_STYLE` = 一段描述你交易风格的文字
4. Actions → **Regime Morning Brief** → **Run workflow** 手动 dispatch 一次验证推送

---

## 配完怎么一键验证全链

把下面这段拷到终端：

```bash
cd /Users/cth/Desktop/daily_stock_analysis

python - <<'PY'
import os
from src.config import get_config
cfg = get_config()

checks = {
    'yfinance (装包)': 'PASS',  # 假定已装，报错自己会跳
    'GEMINI_API_KEY': bool(os.getenv('GEMINI_API_KEY') or cfg.gemini_api_keys),
    'APCA_API_KEY_ID': bool(os.getenv('APCA_API_KEY_ID')),
    'APCA_API_SECRET_KEY': bool(os.getenv('APCA_API_SECRET_KEY')),
    'FINNHUB_API_KEY': bool(os.getenv('FINNHUB_API_KEY')),
    'TELEGRAM_BOT_TOKEN': bool(cfg.telegram_bot_token),
    'TELEGRAM_CHAT_ID': bool(cfg.telegram_chat_id),
    'ANTHROPIC_API_KEY': bool(os.getenv('ANTHROPIC_API_KEY')),
}
for k, v in checks.items():
    mark = '✅' if v else '❌'
    print(f'{mark} {k}')
PY
```

然后跑：
```bash
# Alpaca / Finnhub 单独 smoke
python -c "from data_provider.alpaca_fetcher import AlpacaFetcher; print('Alpaca:', AlpacaFetcher().configured)"
python -c "from data_provider.finnhub_fetcher import FinnhubFetcher; print('Finnhub:', FinnhubFetcher().configured)"

# Telegram 测试消息
python -m src.regime.morning_brief --format-only       # 只打印模板
python -m src.regime.morning_brief --send              # 实推 Telegram

# Regime 完整跑一次看六维度有几个不再是 0
python -m src.regime.cli --verbose
```

配完 Alpaca + Finnhub 后你再去 http://localhost:5173/regime 看 **数据源健康** 卡片，应该从 MISSING × 2 变成 OK × 3。

---

## 配完顺序建议（你现在的最优路径）

```
已有：yfinance, GEMINI_API_KEY                    ← done

1. Alpaca paper account    (5 min)                ← 最值，免费，开 d6
2. Finnhub free            (2 min)                ← 开 d3，免费
3. Telegram bot + chat_id  (5 min)                ← 开晨报推送，免费
4. ANTHROPIC_API_KEY       (按需)                 ← Gemini 备份，要充 $5

(暂不做) ScrapeCreators                           ← Phase 1 再说
```

**一杯咖啡时间全搞定**。然后整个 Regime / 推送 / 月度复盘才算真正点亮。

配完告诉我，我把 GitHub Actions 也跟你过一遍。

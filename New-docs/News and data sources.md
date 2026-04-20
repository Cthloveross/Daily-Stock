# 新闻与数据源选型（$30/月预算版）

> **约束**：月度数据源预算 $30 USD  
> **目标**：在预算内尽可能覆盖"重量级消息"和"偏门信号"  
> **原则**：免费层优先，付费只买"免费替代不了"的

---

## 目录

- [一、$30 预算下的最终组合](#一30-预算下的最终组合)
- [二、免费层详解](#二免费层详解)
- [三、付费层详解](#三付费层详解)
- [四、每个源的接入骨架代码](#四每个源的接入骨架代码)
- [五、去重与信号聚合](#五去重与信号聚合)
- [六、信号优先级与推送策略](#六信号优先级与推送策略)
- [七、未来扩展路径](#七未来扩展路径)

---

## 一、$30 预算下的最终组合

### 1.1 推荐配置

| 层级 | 来源 | 月费 | 覆盖面 | 优先级 |
|--|--|--|--|--|
| **付费（唯一）** | ScrapeCreators Truth Social API | **$20/月** | Trump posts 实时，关税/政策第一手 | 必买 |
| 免费 | **Alpaca Market Data API** | $0 | Benzinga News Feed 聚合（重量级美股新闻） | 必用 |
| 免费 | SEC EDGAR RSS | $0 | 8-K / 10-Q / 10-K 上市公司重大事件 | 必用 |
| 免费 | Finnhub 免费 tier | $0 | 公司新闻 + 分析师评级变动 | 必用 |
| 免费 | 金十数据（试用） | $0 15 天 | 中文宏观 + Fed / CPI 抢发 | 试用期接入 |
| 免费 | Federal Reserve RSS | $0 | FOMC 声明 / 官员讲话 | 必用 |
| 免费 | yfinance news | $0 | 兜底 | 兜底 |
| 免费 | Polymarket API | $0 | 宏观事件赔率（Fed rate / 选举 / 经济事件） | 加分项 |
| 免费 | Reddit r/wallstreetbets（PRAW） | $0 | 散户情绪（过滤用） | 加分项 |

**总月费：$20**，留 $10 作为"灵活额度"应对临时需求（比如某月想试 Unusual Whales 的 7 天免费 trial）。

### 1.2 明确不买的清单

| 服务 | 月费 | 不买原因 |
|--|--|--|
| Polygon.io Starter | $29 | 吃光预算且 Alpaca 已含 Benzinga feed，重复 |
| Unusual Whales | $48 | 超预算；期权异动在 P2 功能里才需要 |
| Benzinga Pro | $177 | 远超预算 |
| Twitter/X API Basic | $200 | 远超预算；用 ScrapeCreators 拿 Musk 帖子足够 |
| Trading Economics API | $70+ | 金十和 Fed RSS 替代 |

### 1.3 为什么 Alpaca 是关键

Alpaca 为免费用户（paper trading 账户即可开通）提供 **`/v1beta1/news` REST + Websocket 端点**，数据源本身是 **Benzinga News Feed** —— 这是华尔街认可的"重量级"来源，包含：
- 盘前 / 盘中 / 盘后公司公告
- 分析师评级升降
- 财报发布快讯
- CEO 访谈摘要
- 并购重组

**零成本拿到 Benzinga 数据**，是本方案最大的杠杆点。开户只需身份证和护照（Chinese citizen 也能开 paper account，实盘需要 SSN/ITIN，但我们只用数据不用交易）。

---

## 二、免费层详解

### 2.1 Alpaca Market Data News API

**注册**：https://alpaca.markets → Sign up → 选 Paper Trading

**认证**：生成 `APCA_API_KEY_ID` + `APCA_API_SECRET_KEY`

**端点**：
```
GET https://data.alpaca.markets/v1beta1/news
  ?symbols=NVDA,AAPL,MSFT
  &start=2026-04-01T00:00:00Z
  &limit=50
Headers:
  APCA-API-KEY-ID: xxx
  APCA-API-SECRET-KEY: xxx
```

**Websocket（实时）**：
```
wss://stream.data.alpaca.markets/v1beta1/news
```
订阅后实时推送 watchlist 新闻。

**免费限制**：rate limit 200 req/min，够用有余。

**返回字段**：
```json
{
  "id": 12345,
  "headline": "NVIDIA Beats Q1 Earnings By $0.30",
  "author": "Benzinga",
  "created_at": "2026-04-15T16:05:00Z",
  "updated_at": "2026-04-15T16:05:00Z",
  "summary": "...",
  "url": "https://www.benzinga.com/...",
  "symbols": ["NVDA"],
  "source": "benzinga"
}
```

### 2.2 SEC EDGAR RSS

**零门槛**，每家上市公司都有一个 RSS：
```
https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany
  &CIK={ticker_or_cik}
  &type=&dateb=&owner=include&count=40&output=atom
```

**用法**：对 watchlist 15 只股票各订阅一个 RSS，定时拉取（每 15 分钟）。

**触发事件**：
- **8-K**：重大事件（高管变动、合同、诉讼、财报前瞻）→ **最重要**
- **10-Q / 10-K**：季报年报
- **Form 4**：内部人交易（高管买卖自家股票）
- **SC 13D / 13G**：大股东持仓变动
- **S-1**：IPO 招股书

**注意 User-Agent**：SEC 要求 User-Agent 带联系邮箱：
```python
headers = {"User-Agent": "Daily-Stock admin@example.com"}
```

### 2.3 Finnhub 免费 tier

**注册**：https://finnhub.io，免费 API key。

**免费额度**：60 req/min。

**最实用端点**：
```
GET /api/v1/company-news?symbol=NVDA&from=2026-04-10&to=2026-04-17
GET /api/v1/stock/recommendation?symbol=NVDA   # 分析师评级
GET /api/v1/calendar/earnings?from=...&to=...  # 财报日历
GET /api/v1/calendar/economic?from=...&to=...  # 宏观日历（免费限美国）
```

**与 Alpaca 的差异**：Finnhub 涵盖更多小盘 / 国际股，但实时性逊于 Alpaca（Benzinga）。**两者互补**。

### 2.4 Federal Reserve RSS

**FOMC 声明**：
```
https://www.federalreserve.gov/feeds/press_monetary.xml
```

**所有官员讲话**：
```
https://www.federalreserve.gov/feeds/speeches.xml
```

**经济数据（H.4.1 等）**：
```
https://www.federalreserve.gov/feeds/h41.xml
```

**处理**：标准 RSS，用 `feedparser` 解析即可。

### 2.5 金十数据（试用 15 天）

**申请**：https://open.jin10.com → 注册 → 申请免费试用

**核心端点（示例）**：
```
GET https://open-data-api.jin10.com/data-api/flash
Headers:
  secret-key: your_secret_key
Body:
  { "category": "market" }   # flash 分类：market / commodity / forex / futures
```

**特色**：
- **CPI / 非农 / FOMC 决议"抢发"**（自称 70% 领先全球），对 Fed 交易时刻有用
- 中文，有情绪标签
- 15 天试用后定价需询问（个人级参考 ¥100-300/月，不买也可以）

**策略**：试用期接入，评估价值。如果发现"金十抢发时刻"真能提前 10-30 秒知道数据，续费；否则砍掉。

### 2.6 yfinance news（兜底）

```python
import yfinance as yf
yf.Ticker("NVDA").news
# 返回列表，含 title / link / publisher / providerPublishTime
```

**角色**：当其他源都没数据时的保底，不作为主力。

### 2.7 Polymarket（加分项）

**端点**：https://clob.polymarket.com/markets
**免费**，无需 key。

**用法**：订阅几个关键市场：
- "Will the Fed cut rates in June 2026?"
- "Will there be a recession in 2026?"
- "Next US CPI print"

**价值**：当赔率突变时通常领先于股市反应。作为**宏观信号**而非交易信号。

### 2.8 Reddit WallStreetBets（可选）

**工具**：PRAW（Python Reddit API Wrapper），免费，需要创建 Reddit app（2 分钟搞定）。

**用法**：
```python
import praw
reddit = praw.Reddit(client_id=..., client_secret=..., user_agent=...)
for post in reddit.subreddit("wallstreetbets").hot(limit=50):
    if any(s in post.title.upper() for s in WATCHLIST):
        # 你 watchlist 中的票被 WSB 热议
        ...
```

**用途**：不是交易信号，是**情绪过滤器**。当 WSB 大肆吹捧某只你持仓的票时，注意情绪顶部。

---

## 三、付费层详解

### 3.1 ScrapeCreators Truth Social API（$20/月）

**注册**：https://scrapecreators.com

**核心端点**：
```
GET https://api.scrapecreators.com/v1/truthsocial/user-posts
  ?handle=realDonaldTrump
Headers:
  x-api-key: your_key
```

**返回**：Trump 所有最新帖子，含 `created_at` / `content` / `media` / `stats`。

**关键用法**：定时（每 2-5 分钟）轮询最新帖子，发现 `created_at` 新于上次处理时间的 → 触发"关税/贸易/政策关键词检测" → 命中则立刻推 Telegram + 标注"可能影响板块"。

**关键词模板**（初版）：
```python
TRUMP_TARIFF_KEYWORDS = [
    "tariff", "tariffs", "china", "trade deal", "trade war",
    "reciprocal", "liberation day", "deficit",
    "fentanyl", "border",
]
TRUMP_MARKET_KEYWORDS = [
    "stock market", "federal reserve", "jerome powell",
    "rate cut", "interest rate",
]
```

**历史验证**：2025 年 4 月 Liberation Day 和 10 月中国关税两次 SPY -2%+ 崩盘，都是从 Trump Truth Social 先发。该 API 能让你在这种事件第一时间知道。

**限制**：非官方 API，Truth Social 改版可能导致中断。但 ScrapeCreators 维护频繁，可接受。

**替代方案（免费）**：
- GitHub 上有开源项目 `stiles/trump-truth-social-archive`，每 4 小时更新一次，延迟太大，不可用于交易信号
- Apify 的 Truth Social Scraper 按量付费，适合低频

---

## 四、每个源的接入骨架代码

### 4.1 统一的 news_provider 抽象

```python
# src/news/providers/__init__.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass
class NewsItem:
    source: str                  # 'alpaca' / 'finnhub' / 'sec_edgar' / 'jin10' / 'trump_truth' / 'fed_rss'
    external_id: str             # 源的唯一 ID，用于去重
    published_at: datetime       # UTC
    title: str
    summary: str | None
    url: str
    symbols: list[str]           # 受影响标的（如能判断）
    severity: int                # 1-5，主观重要度
    raw: dict                    # 原始数据


class BaseProvider(ABC):
    @abstractmethod
    def fetch_latest(self, since: datetime | None = None) -> Iterable[NewsItem]:
        ...
```

### 4.2 Alpaca Provider

```python
# src/news/providers/alpaca.py

import os
from datetime import datetime, timezone
import requests
from . import BaseProvider, NewsItem


class AlpacaNewsProvider(BaseProvider):
    BASE = "https://data.alpaca.markets/v1beta1"

    def __init__(self):
        self.key = os.environ["APCA_API_KEY_ID"]
        self.secret = os.environ["APCA_API_SECRET_KEY"]

    def fetch_latest(self, symbols: list[str], since: datetime | None = None):
        params = {
            "symbols": ",".join(symbols),
            "limit": 50,
            "sort": "desc",
        }
        if since:
            params["start"] = since.isoformat()
        r = requests.get(
            f"{self.BASE}/news",
            params=params,
            headers={
                "APCA-API-KEY-ID": self.key,
                "APCA-API-SECRET-KEY": self.secret,
            },
            timeout=10,
        )
        r.raise_for_status()
        for item in r.json().get("news", []):
            yield NewsItem(
                source="alpaca",
                external_id=str(item["id"]),
                published_at=datetime.fromisoformat(
                    item["created_at"].replace("Z", "+00:00")
                ),
                title=item["headline"],
                summary=item.get("summary"),
                url=item["url"],
                symbols=item.get("symbols", []),
                severity=_severity_from_headline(item["headline"]),
                raw=item,
            )


def _severity_from_headline(headline: str) -> int:
    h = headline.lower()
    if any(kw in h for kw in ["earnings beat", "earnings miss", "guidance", "fda", "acquisition", "lawsuit"]):
        return 4
    if any(kw in h for kw in ["upgrade", "downgrade", "initiated"]):
        return 3
    return 2
```

### 4.3 SEC EDGAR Provider

```python
# src/news/providers/sec_edgar.py

import feedparser
import time
from datetime import datetime, timezone
from . import BaseProvider, NewsItem


class SecEdgarProvider(BaseProvider):
    TEMPLATE = (
        "https://www.sec.gov/cgi-bin/browse-edgar?"
        "action=getcompany&CIK={cik}&type={form_type}&"
        "dateb=&owner=include&count=10&output=atom"
    )
    SEVERITY_MAP = {"8-K": 5, "10-Q": 4, "10-K": 4, "4": 2, "SC 13D": 4, "SC 13G": 3}

    def __init__(self, cik_map: dict[str, str]):
        # cik_map: {'NVDA': '0001045810', ...}
        self.cik_map = cik_map

    def fetch_latest(self, symbols: list[str]):
        for sym in symbols:
            cik = self.cik_map.get(sym)
            if not cik:
                continue
            for form_type in ("8-K", "10-Q", "10-K", "4"):
                url = self.TEMPLATE.format(cik=cik, form_type=form_type)
                feed = feedparser.parse(
                    url,
                    request_headers={"User-Agent": "Daily-Stock admin@example.com"},
                )
                for entry in feed.entries[:5]:
                    yield NewsItem(
                        source="sec_edgar",
                        external_id=entry.id,
                        published_at=datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc),
                        title=f"[{sym}] {form_type}: {entry.title}",
                        summary=entry.summary if hasattr(entry, "summary") else None,
                        url=entry.link,
                        symbols=[sym],
                        severity=self.SEVERITY_MAP.get(form_type, 2),
                        raw=dict(entry),
                    )
            time.sleep(0.3)  # SEC 要求友好访问
```

### 4.4 Trump Truth Social Provider

```python
# src/news/providers/trump_truth.py

import os
import re
from datetime import datetime
import requests
from . import BaseProvider, NewsItem


TARIFF_KW = ["tariff", "china", "trade", "reciprocal", "liberation day", "deficit"]
MARKET_KW = ["stock", "fed", "powell", "rate cut", "interest rate", "economy"]


class TrumpTruthProvider(BaseProvider):
    BASE = "https://api.scrapecreators.com/v1"

    def __init__(self):
        self.key = os.environ["SCRAPECREATORS_API_KEY"]

    def fetch_latest(self, since: datetime | None = None):
        r = requests.get(
            f"{self.BASE}/truthsocial/user-posts",
            params={"handle": "realDonaldTrump"},
            headers={"x-api-key": self.key},
            timeout=15,
        )
        r.raise_for_status()
        posts = r.json().get("posts", [])

        for p in posts:
            pub = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
            if since and pub <= since:
                continue

            text = re.sub(r"<[^>]+>", "", p.get("content") or "")
            severity, symbols = self._classify(text)

            yield NewsItem(
                source="trump_truth",
                external_id=p["id"],
                published_at=pub,
                title=text[:120],
                summary=text,
                url=p.get("url", ""),
                symbols=symbols,
                severity=severity,
                raw=p,
            )

    def _classify(self, text: str) -> tuple[int, list[str]]:
        t = text.lower()
        if any(kw in t for kw in TARIFF_KW):
            return 5, ["SPY", "QQQ"]  # 关税/贸易影响大盘
        if any(kw in t for kw in MARKET_KW):
            return 4, ["SPY"]
        return 2, []
```

### 4.5 Jin10 Provider

```python
# src/news/providers/jin10.py

import os
import requests
from datetime import datetime
from . import BaseProvider, NewsItem


class Jin10Provider(BaseProvider):
    BASE = "https://open-data-api.jin10.com/data-api"

    def __init__(self):
        self.key = os.environ.get("JIN10_SECRET_KEY")

    def fetch_latest(self, category: str = "market"):
        if not self.key:
            return
        r = requests.get(
            f"{self.BASE}/flash",
            headers={"secret-key": self.key},
            params={"category": category},
            timeout=10,
        )
        r.raise_for_status()
        for item in r.json().get("data", []):
            yield NewsItem(
                source="jin10",
                external_id=str(item["id"]),
                published_at=datetime.fromisoformat(item["time"]),
                title=item.get("content", "")[:120],
                summary=item.get("content"),
                url=item.get("url", ""),
                symbols=[],
                severity=item.get("important", 2),  # 金十自带重要度
                raw=item,
            )
```

### 4.6 Fed RSS Provider

```python
# src/news/providers/fed_rss.py

import feedparser
from datetime import datetime, timezone
from . import BaseProvider, NewsItem


class FedRssProvider(BaseProvider):
    FEEDS = {
        "monetary": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
    }

    def fetch_latest(self):
        for kind, url in self.FEEDS.items():
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                yield NewsItem(
                    source=f"fed_{kind}",
                    external_id=entry.id,
                    published_at=datetime(*entry.published_parsed[:6], tzinfo=timezone.utc),
                    title=entry.title,
                    summary=entry.summary if hasattr(entry, "summary") else None,
                    url=entry.link,
                    symbols=["SPY", "TLT"],  # Fed 主要影响大盘和债
                    severity=5 if kind == "monetary" else 3,
                    raw=dict(entry),
                )
```

### 4.7 统一聚合器

```python
# src/news/aggregator.py

from src.news.providers.alpaca import AlpacaNewsProvider
from src.news.providers.sec_edgar import SecEdgarProvider
from src.news.providers.trump_truth import TrumpTruthProvider
from src.news.providers.jin10 import Jin10Provider
from src.news.providers.fed_rss import FedRssProvider
from src.news.storage import save_news_batch, get_last_synced_at


def run_all_providers(symbols: list[str]):
    for Provider in [
        lambda: AlpacaNewsProvider().fetch_latest(symbols, since=get_last_synced_at("alpaca")),
        lambda: SecEdgarProvider(CIK_MAP).fetch_latest(symbols),
        lambda: TrumpTruthProvider().fetch_latest(since=get_last_synced_at("trump_truth")),
        lambda: Jin10Provider().fetch_latest(),
        lambda: FedRssProvider().fetch_latest(),
    ]:
        try:
            items = list(Provider())
            save_news_batch(items)
        except Exception as exc:
            # 记录日志，不阻塞其他 provider
            import logging
            logging.exception("provider 失败")
```

---

## 五、去重与信号聚合

### 5.1 去重策略

**主键**：`(source, external_id)` 唯一约束。

**跨源去重**（可选，进阶）：
多个源可能报道同一事件（Alpaca 和 Finnhub 都有 NVDA 财报新闻）。用 **SimHash** 或 **标题前 60 字符的哈希** 做粗粒度去重：

```python
def is_duplicate_across_sources(item, recent_items):
    key = (item.symbols[0] if item.symbols else "", item.title[:60].lower())
    return key in recent_items
```

### 5.2 数据库 Schema

```sql
CREATE TABLE news_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source        TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  published_at  TIMESTAMP NOT NULL,
  title         TEXT NOT NULL,
  summary       TEXT,
  url           TEXT,
  symbols       TEXT,              -- JSON array
  severity      INTEGER NOT NULL,
  raw           TEXT,              -- JSON
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, external_id)
);

CREATE INDEX idx_news_time ON news_items(published_at DESC);
CREATE INDEX idx_news_severity ON news_items(severity DESC, published_at DESC);
```

前端按 symbol 过滤：
```sql
SELECT * FROM news_items
WHERE symbols LIKE '%"NVDA"%'
  AND published_at > datetime('now', '-1 day')
ORDER BY severity DESC, published_at DESC;
```

---

## 六、信号优先级与推送策略

### 6.1 Severity 分级标准

| 级别 | 含义 | 推送策略 | 典型事件 |
|--|--|--|--|
| 5 | 关键事件 | **立即 Telegram 推送** | FOMC 决议、Trump 关税贴、watchlist 股 8-K、CPI 公布 |
| 4 | 重要事件 | 立即推送 | 财报发布、分析师大行升降评、并购 |
| 3 | 中等事件 | 聚合到小时摘要推送 | Fed 官员讲话、小规模评级变动 |
| 2 | 一般新闻 | 只进库不推送 | 常规公司动态 |
| 1 | 噪音 | 丢弃 | 广告 / 软文 |

### 6.2 推送节流

- **单 symbol 相同主题**：1 小时内不重复推送（如 NVDA 财报，第一条 Alpaca 发了就不发 Finnhub 的同内容）
- **大盘级事件**（如 Fed 决议）：立即推，但锁定 10 分钟防止连续消息刷屏
- **Trump post 严重度 5**：立即推，附加 "可能影响板块：SPY/QQQ"

### 6.3 推送模板（Telegram）

```
🚨 [严重度 5] Trump 发布关税相关帖子
时间：2026-04-17 14:23 ET
内容：【摘要前 200 字】
可能影响：SPY, QQQ
历史参考：类似帖子过去 5 次平均引发 SPY -1.2% 盘中波动
链接：[原帖]
```

```
📊 [严重度 4] NVDA 8-K 发布
时间：2026-04-17 16:05 ET
要点：首席财务官辞职
链接：[SEC 原文]
你的持仓：100 股 NVDA @ $182.34（浮盈 $286）
```

---

## 七、未来扩展路径

### 7.1 预算放宽（$50-100/月）

| 优先新增 | 月费 | 价值 |
|--|--|--|
| Unusual Whales | $48 | 期权异动 / 聪明钱 / SPY Gamma Exposure |
| Polygon.io Starter | $29 | 更完整的新闻聚合（目前有 Alpaca 可以不急） |
| 金十正式版 | ¥150 ≈ $20 | 中文宏观抢发（如果试用期验证有价值） |

### 7.2 按需付费

- **ScrapeCreators Musk/X 账户**：如果发现 Musk 推文对 TSLA 交易价值高（查历史相关性），加订 $10-20
- **Quiver Quant Premium**：$10/月，国会议员交易（Pelosi Tracker）
- **Fintel.io**：$30/月，短卖数据

### 7.3 插针归因引擎（P2）

等预算扩到包含 Unusual Whales，启动归因引擎：
- 盘中 SPY/QQQ 异动 > 0.5%（3 分钟内）
- 回溯最近 10 分钟所有 severity ≥ 4 的新闻 + 期权异动 + Polymarket 赔率变化
- Gemini 生成"最可能原因"报告
- Telegram 实时推送

这个功能值钱，但目前预算做不了**完整版**。可以先做**无期权版**（只聚合新闻 + Polymarket），看价值是否值得未来扩预算。

---

## 八、实施顺序建议

**Week 1**：
- 注册 Alpaca paper account + 生成 API key
- 注册 Finnhub 免费账号
- 注册 ScrapeCreators + 订阅 $20/月计划
- 申请金十 15 天试用

**Week 2**：
- 实现 `BaseProvider` 接口 + 5 个 provider（Alpaca / SEC / Finnhub / Fed RSS / yfinance 兜底）
- 实现统一存储 + 去重
- 跑起基础新闻入库

**Week 3**：
- 接入 Trump Truth + Jin10
- 实现 severity 分级 + Telegram 推送
- 前端新闻流页面

**Week 4**：
- 节流 + 跨源去重优化
- Watchlist 按股过滤 UI
- 历史数据统计（每周哪个源贡献最多 severity 5 事件）

---

> **关键原则**：免费层能覆盖 80% 场景；付费只买不可替代的（Trump 实时）；预算留 1/3 buffer 应对"这个月想试一下某新源"。
# Regime Classifier 实现文档

> **文档定位**：Market Regime Classifier 的代码级完整实现。照着写就能跑。  
> **基于**：TRADING_SYSTEM_SPEC.md 第二章的六维度设计  
> **所需 API key**：Alpaca（免费）+ Finnhub（免费），都来自 SETUP_CHECKLIST.md

---

## 一、架构

```
每日 09:00 ET（cron 触发）
    │
    ▼
┌──────────────────────────────┐
│  src/regime/classifier.py    │
│                              │
│  compute_regime_score(date)  │
│    ├─ 拉 6 维度数据           │
│    ├─ 每维度打分              │
│    └─ 汇总 + 分类            │
└──────────────┬───────────────┘
               │
               ├─► 存 SQLite (regime_scores 表)
               ├─► Telegram 晨报推送
               └─► 前端 Today 页 API
```

---

## 二、项目文件结构

```
src/regime/
├── __init__.py
├── classifier.py          # 主函数 compute_regime_score
├── data_fetcher.py        # 六维度数据拉取
├── scorers.py             # 六个打分函数
├── morning_brief.py       # Telegram 晨报 formatter
├── storage.py             # DB CRUD
└── tests/
    ├── test_scorers.py
    └── fixtures/
        └── sample_market_data.json
```

---

## 三、数据库 Schema

```sql
CREATE TABLE regime_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE UNIQUE NOT NULL,
    score           INTEGER NOT NULL,           -- 0-100 + 负数可能
    label           TEXT NOT NULL,              -- 激进 / 标准 / 谨慎 / 不交易
    action          TEXT NOT NULL,
    -- 六维度拆解
    d1_direction    INTEGER,                    -- 大盘方向
    d2_volatility   INTEGER,                    -- 波动率
    d3_macro_penalty INTEGER,                   -- 宏观（负数）
    d4_sector       INTEGER,                    -- 板块轮动
    d5_prev_day     INTEGER,                    -- 前日结构
    d6_premarket    INTEGER,                    -- 盘前异动
    -- 快照数据（用于后续校准）
    snapshot_json   TEXT,                       -- 所有原始数据的 JSON
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_regime_date ON regime_scores(date);
```

---

## 四、核心实现

### 4.1 `src/regime/classifier.py`

```python
"""Regime Classifier 主函数。"""
import logging
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import Any

from .data_fetcher import MarketDataFetcher
from .scorers import (
    score_market_direction,
    score_volatility,
    score_macro_penalty,
    score_sector_rotation,
    score_prev_day_structure,
    score_premarket_activity,
)
from .storage import save_regime_score

logger = logging.getLogger(__name__)


@dataclass
class RegimeResult:
    date: date
    score: int
    label: str
    action: str
    breakdown: dict[str, int] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    top_watchlist: list[dict] = field(default_factory=list)
    
    def to_dict(self):
        d = asdict(self)
        d['date'] = self.date.isoformat()
        return d


# 分类阈值
THRESHOLD_AGGRESSIVE = 75
THRESHOLD_STANDARD   = 55
THRESHOLD_CAUTIOUS   = 35


def classify(score: int) -> tuple[str, str]:
    if score >= THRESHOLD_AGGRESSIVE:
        return (
            '高波动激进可交易',
            '可以追突破（配合第二层假突破过滤）'
        )
    if score >= THRESHOLD_STANDARD:
        return (
            '标准交易日',
            '只做回调买入和 EMA retest，不追一日新高'
        )
    if score >= THRESHOLD_CAUTIOUS:
        return (
            '谨慎交易日',
            '仅观察，不主动入场；已有仓位正常管理'
        )
    return (
        '不交易日',
        '完全观望，不开新仓（Shadow Trades 除外）'
    )


def compute_regime_score(
    target_date: date | None = None,
    watchlist: list[str] | None = None,
    save_to_db: bool = True,
) -> RegimeResult:
    """计算指定日期的 Regime Score。
    
    Args:
        target_date: 默认今天（美东日期）
        watchlist: 自选股列表（用于 d6 盘前异动）
        save_to_db: 是否保存到数据库
    """
    import os
    if target_date is None:
        target_date = _today_et()
    if watchlist is None:
        watchlist = os.environ.get('WATCHLIST', '').split(',')
    
    fetcher = MarketDataFetcher()
    
    # 拉数据
    try:
        spy_data   = fetcher.get_spy_snapshot(target_date)
        vix_data   = fetcher.get_vix(target_date)
        macro_data = fetcher.get_macro_events(target_date, watchlist)
        sector_data = fetcher.get_sector_performance(target_date, lookback_days=5)
        prev_day   = fetcher.get_prev_day_structure(target_date)
        premarket  = fetcher.get_premarket_activity(watchlist, target_date)
    except Exception as e:
        logger.exception("Regime 数据拉取失败")
        raise
    
    # 打分
    breakdown = {
        'direction':      score_market_direction(spy_data),
        'volatility':     score_volatility(vix_data),
        'macro_penalty':  score_macro_penalty(macro_data),
        'sector':         score_sector_rotation(sector_data),
        'prev_day':       score_prev_day_structure(prev_day),
        'premarket':      score_premarket_activity(premarket),
    }
    score = sum(breakdown.values())
    
    label, action = classify(score)
    
    # 挑出盘前异动 top 作为今日关注列表
    top_watchlist = sorted(
        premarket.get('symbols', []),
        key=lambda x: -abs(x.get('premarket_change_pct', 0))
    )[:5]
    
    result = RegimeResult(
        date=target_date,
        score=score,
        label=label,
        action=action,
        breakdown=breakdown,
        snapshot={
            'spy': spy_data,
            'vix': vix_data,
            'macro': macro_data,
            'sector': sector_data,
            'prev_day': prev_day,
            'premarket': premarket,
        },
        top_watchlist=top_watchlist,
    )
    
    if save_to_db:
        save_regime_score(result)
    
    return result


def _today_et() -> date:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('America/New_York')).date()
```

### 4.2 `src/regime/data_fetcher.py`

```python
"""六维度数据拉取。所有数据源免费。"""
import os
import requests
import yfinance as yf
import pandas as pd
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any


class MarketDataFetcher:
    def __init__(self):
        self.alpaca_key = os.environ.get('APCA_API_KEY_ID')
        self.alpaca_secret = os.environ.get('APCA_API_SECRET_KEY')
        self.finnhub_key = os.environ.get('FINNHUB_API_KEY')
    
    # ========== 维度 1：大盘方向 ==========
    def get_spy_snapshot(self, target_date: date) -> dict:
        """SPY 盘前走势 + 前一日 K 线 + ES 期货情况。"""
        spy = yf.Ticker('SPY')
        
        # 前一日 K 线
        hist = spy.history(
            start=target_date - timedelta(days=10),
            end=target_date,
            interval='1d',
        )
        if len(hist) == 0:
            raise ValueError(f'SPY 无数据 at {target_date}')
        prev_day = hist.iloc[-1]
        
        # 盘前走势（Alpaca 2min K 线）
        pre_change_pct = self._alpaca_premarket_change('SPY', target_date)
        
        # 前一日 K 线结构
        total_range = prev_day['High'] - prev_day['Low']
        body_size = abs(prev_day['Close'] - prev_day['Open'])
        body_ratio = body_size / total_range if total_range > 0 else 0
        
        # ES 期货 overnight change
        try:
            es = yf.Ticker('ES=F').history(period='2d', interval='1h')
            es_change = (es.iloc[-1]['Close'] - es.iloc[0]['Close']) / es.iloc[0]['Close']
        except Exception:
            es_change = None
        
        return {
            'premarket_change_pct': pre_change_pct,
            'prev_close': prev_day['Close'],
            'prev_open': prev_day['Open'],
            'prev_high': prev_day['High'],
            'prev_low': prev_day['Low'],
            'prev_body_ratio': body_ratio,
            'prev_trend_day': body_ratio > 0.7,
            'es_futures_change': es_change,
        }
    
    def _alpaca_premarket_change(self, symbol: str, target_date: date) -> float:
        """用 Alpaca 拉盘前 premarket K 线计算开盘前的涨跌幅。"""
        if not self.alpaca_key:
            # fallback：用 yfinance 1min 盘前（延迟 15min）
            return self._yfinance_premarket(symbol, target_date)
        
        et = ZoneInfo('America/New_York')
        start = datetime.combine(target_date, datetime.min.time(), tzinfo=et)
        start = start.replace(hour=4, minute=0)  # 04:00 ET 盘前开始
        end   = start.replace(hour=9, minute=29)  # 09:29 最后一根盘前 K
        
        url = 'https://data.alpaca.markets/v2/stocks/bars'
        params = {
            'symbols': symbol,
            'timeframe': '5Min',
            'start': start.isoformat(),
            'end': end.isoformat(),
            'feed': 'iex',
        }
        r = requests.get(url, params=params, headers={
            'APCA-API-KEY-ID': self.alpaca_key,
            'APCA-API-SECRET-KEY': self.alpaca_secret,
        }, timeout=10)
        r.raise_for_status()
        bars = r.json().get('bars', {}).get(symbol, [])
        if not bars:
            return 0.0
        
        first_pre = bars[0]['o']
        last_pre  = bars[-1]['c']
        # 和前一日收盘对比
        prev_close = yf.Ticker(symbol).history(period='5d')['Close'].iloc[-2]
        return (last_pre - prev_close) / prev_close * 100
    
    def _yfinance_premarket(self, symbol: str, target_date: date) -> float:
        """yfinance fallback：用 1min 数据，注意延迟。"""
        t = yf.Ticker(symbol)
        hist = t.history(period='5d', interval='1m', prepost=True)
        if len(hist) == 0:
            return 0.0
        # 取 target_date 盘前最后一根
        et = hist.index[0].tzinfo
        premarket_end = datetime.combine(target_date, datetime.min.time(), tzinfo=et)
        premarket_end = premarket_end.replace(hour=9, minute=29)
        pre_bars = hist[hist.index <= premarket_end]
        if len(pre_bars) == 0:
            return 0.0
        last_pre = pre_bars.iloc[-1]['Close']
        prev_close = t.history(period='5d', interval='1d')['Close'].iloc[-2]
        return (last_pre - prev_close) / prev_close * 100
    
    # ========== 维度 2：波动率 ==========
    def get_vix(self, target_date: date) -> dict:
        vix = yf.Ticker('^VIX')
        hist = vix.history(period='5d', interval='1d')
        if len(hist) < 2:
            return {'vix_now': 20.0, 'vix_change_pct': 0}
        prev = hist.iloc[-2]['Close']
        now  = hist.iloc[-1]['Close']
        return {
            'vix_now': now,
            'vix_prev': prev,
            'vix_change_pct': (now - prev) / prev * 100,
        }
    
    # ========== 维度 3：宏观事件 ==========
    def get_macro_events(self, target_date: date, watchlist: list[str]) -> dict:
        events = {
            'fomc_today': False,
            'cpi_today': False,
            'nfp_today': False,
            'pce_today': False,
            'ecb_today': False,
            'retail_today': False,
            'watchlist_earnings_today': [],
        }
        
        if self.finnhub_key:
            # 宏观日历
            url = 'https://finnhub.io/api/v1/calendar/economic'
            r = requests.get(url, params={
                'from': target_date.isoformat(),
                'to': target_date.isoformat(),
                'token': self.finnhub_key,
            }, timeout=10)
            if r.ok:
                for ev in r.json().get('economicCalendar', []):
                    name = ev.get('event', '').lower()
                    if 'fomc' in name or 'fed interest' in name:
                        events['fomc_today'] = True
                    elif 'cpi' in name and 'us' in ev.get('country', '').lower():
                        events['cpi_today'] = True
                    elif 'non-farm' in name or 'nonfarm' in name:
                        events['nfp_today'] = True
                    elif 'pce' in name:
                        events['pce_today'] = True
                    elif 'ecb' in name or 'europe interest' in name:
                        events['ecb_today'] = True
                    elif 'retail sales' in name:
                        events['retail_today'] = True
        
        # Watchlist 财报
        for sym in watchlist:
            try:
                cal = yf.Ticker(sym).calendar
                if cal is not None and 'Earnings Date' in cal:
                    ed = cal['Earnings Date']
                    if isinstance(ed, list):
                        ed = ed[0]
                    if ed and abs((ed - target_date).days) <= 1:
                        events['watchlist_earnings_today'].append(sym)
            except Exception:
                continue
        
        return events
    
    # ========== 维度 4：板块轮动 ==========
    def get_sector_performance(self, target_date: date, lookback_days: int = 5) -> dict:
        sector_etfs = {
            'XLK': 'Technology',
            'XLF': 'Financials',
            'XLE': 'Energy',
            'XLV': 'Healthcare',
            'XLY': 'Consumer Disc',
            'XLC': 'Communications',
            'XLI': 'Industrials',
            'XLP': 'Consumer Staples',
            'XLU': 'Utilities',
            'XLB': 'Materials',
            'XLRE': 'Real Estate',
        }
        results = {}
        for etf, sector in sector_etfs.items():
            try:
                hist = yf.Ticker(etf).history(
                    start=target_date - timedelta(days=lookback_days + 3),
                    end=target_date,
                )
                if len(hist) < 2:
                    continue
                ret = (hist.iloc[-1]['Close'] - hist.iloc[0]['Close']) / hist.iloc[0]['Close'] * 100
                results[etf] = {'sector': sector, 'return_pct': ret}
            except Exception:
                continue
        return results
    
    # ========== 维度 5：前一日市场结构 ==========
    def get_prev_day_structure(self, target_date: date) -> dict:
        spy = yf.Ticker('SPY').history(
            start=target_date - timedelta(days=40),
            end=target_date,
        )
        if len(spy) < 2:
            return {}
        prev = spy.iloc[-1]
        avg_vol_20 = spy.iloc[-21:-1]['Volume'].mean() if len(spy) >= 21 else prev['Volume']
        
        # 收盘相对高低点位置
        daily_range = prev['High'] - prev['Low']
        close_position = (prev['Close'] - prev['Low']) / daily_range if daily_range > 0 else 0.5
        
        return {
            'prev_close': prev['Close'],
            'prev_high': prev['High'],
            'prev_low': prev['Low'],
            'prev_volume': prev['Volume'],
            'prev_volume_vs_avg20': prev['Volume'] / avg_vol_20 if avg_vol_20 > 0 else 1,
            'close_position_in_range': close_position,  # 0 = 最低, 1 = 最高
        }
    
    # ========== 维度 6：盘前异动 ==========
    def get_premarket_activity(self, watchlist: list[str], target_date: date) -> dict:
        results = []
        for sym in watchlist:
            try:
                change = self._alpaca_premarket_change(sym, target_date)
                results.append({
                    'symbol': sym,
                    'premarket_change_pct': change,
                })
            except Exception:
                continue
        return {
            'symbols': results,
            'active_count_3pct': sum(1 for r in results if abs(r['premarket_change_pct']) >= 3),
        }
```

### 4.3 `src/regime/scorers.py`

```python
"""六个维度的打分函数。纯函数，易测试。"""


def score_market_direction(spy_data: dict) -> int:
    score = 0
    
    pre_pct = abs(spy_data.get('premarket_change_pct', 0))
    if pre_pct > 0.5:
        score += 15
    elif pre_pct > 0.3:
        score += 10
    elif pre_pct > 0.1:
        score += 5
    
    if spy_data.get('prev_trend_day'):
        score += 10
    elif spy_data.get('prev_body_ratio', 0) > 0.4:
        score += 5
    
    # ES 和 SPY premarket 一致性
    es_change = spy_data.get('es_futures_change')
    spy_pre = spy_data.get('premarket_change_pct', 0) / 100
    if es_change is not None:
        if (es_change > 0 and spy_pre > 0) or (es_change < 0 and spy_pre < 0):
            score += 5
        elif abs(es_change) > 0.005 and abs(spy_pre) > 0.005:
            # 方向背离
            score -= 5
    
    return score


def score_volatility(vix_data: dict) -> int:
    vix = vix_data.get('vix_now', 20)
    change = vix_data.get('vix_change_pct', 0)
    
    if 14 <= vix <= 20:
        s = 15
    elif 12 <= vix < 14:
        s = 8
    elif 20 < vix <= 25:
        s = 10
    elif vix < 12:
        s = 0
    else:  # vix > 25
        s = -10
    
    if change < -2:
        s += 5
    elif change > 5:
        s -= 5
    
    return s


def score_macro_penalty(events: dict) -> int:
    """只减分。"""
    penalty = 0
    if events.get('fomc_today'):
        penalty -= 30
    if events.get('cpi_today'):
        penalty -= 20
    if events.get('nfp_today'):
        penalty -= 20
    if events.get('pce_today'):
        penalty -= 10
    if events.get('ecb_today'):
        penalty -= 5
    if events.get('retail_today'):
        penalty -= 10
    earnings = events.get('watchlist_earnings_today', [])
    if len(earnings) >= 3:
        penalty -= 15  # 多只 watchlist 股今天财报
    elif len(earnings) >= 1:
        penalty -= 5
    return penalty


def score_sector_rotation(sectors: dict) -> int:
    """XLK 权重更高（用户偏科技）。"""
    if not sectors:
        return 0
    
    sorted_sectors = sorted(sectors.items(), key=lambda x: -x[1]['return_pct'])
    
    # 多个领涨
    leaders_count = sum(1 for _, d in sectors.items() if d['return_pct'] > 2)
    if leaders_count >= 2:
        score = 10
    elif leaders_count == 1:
        score = 5
    else:
        # 所有板块窄幅震荡
        max_ret = max(d['return_pct'] for _, d in sectors.items())
        min_ret = min(d['return_pct'] for _, d in sectors.items())
        if (max_ret - min_ret) < 2:
            score = 0
        else:
            score = 3
    
    # XLK 领涨特别加分
    xlk = sectors.get('XLK', {})
    if xlk.get('return_pct', 0) > 2 and sorted_sectors[0][0] == 'XLK':
        score += 5
    elif xlk.get('return_pct', 0) < -2:
        score -= 5
    
    # 防御板块领涨（避险情绪）减分
    xlu = sectors.get('XLU', {}).get('return_pct', 0)
    xlp = sectors.get('XLP', {}).get('return_pct', 0)
    if xlu > 1 or xlp > 1:
        # 如果是防御板块领涨
        if sorted_sectors[0][0] in ('XLU', 'XLP'):
            score -= 5
    
    return score


def score_prev_day_structure(prev_day: dict) -> int:
    if not prev_day:
        return 0
    score = 0
    
    pos = prev_day.get('close_position_in_range', 0.5)
    if pos > 0.97:
        score += 8  # 收盘在最高点附近
    elif 0.6 < pos < 0.97:
        score += 5  # V 形反弹位
    elif pos < 0.3:
        score += 0  # N 形下跌位
    
    vol_ratio = prev_day.get('prev_volume_vs_avg20', 1)
    if vol_ratio > 1.2:
        score += 5
    elif vol_ratio < 0.8:
        score -= 2
    
    return score


def score_premarket_activity(premarket: dict) -> int:
    active = premarket.get('active_count_3pct', 0)
    if active >= 3:
        return 15
    if active >= 1:
        return 8
    return 0
```

### 4.4 `src/regime/morning_brief.py`

```python
"""Telegram 晨报格式化和推送。"""
import os
import requests
from datetime import date
from .classifier import RegimeResult


def format_morning_brief(result: RegimeResult) -> str:
    lines = [
        f'🌅 可交易日评分 | {result.date} ({_weekday_cn(result.date)})',
        '',
        f'📊 综合评分：{result.score} / 100 → {result.label}',
        '',
        '建议动作：',
    ]
    
    if '激进' in result.label:
        lines += [
            '  ✅ 可以追突破（配合 Breakout Filter 第二层）',
            '  ✅ Volume 2x + 多周期 + RS 全过才入场',
            '  ⚠️ 开盘 30 分钟仍禁止',
        ]
    elif '标准' in result.label:
        lines += [
            '  ✅ 只做回调买入和 EMA retest',
            '  ❌ 不追一日新高',
            '  ❌ 不在开盘 30 分钟内入场',
        ]
    elif '谨慎' in result.label:
        lines += [
            '  ⚠️ 仅观察，不主动入场',
            '  ✅ 已有仓位正常管理',
            '  ✅ 做 Shadow Trades 练盘感',
        ]
    else:  # 不交易日
        lines += [
            '  ❌ 完全观望',
            '  ❌ 不开新仓',
            '  ✅ 可以做 Shadow Trades',
        ]
    
    lines += ['', '拆解：']
    b = result.breakdown
    lines += [
        f'  大盘方向：{b.get("direction",0):+d}',
        f'  波动率  ：{b.get("volatility",0):+d}  (VIX {result.snapshot.get("vix",{}).get("vix_now",0):.1f})',
        f'  宏观事件：{b.get("macro_penalty",0):+d}',
        f'  板块轮动：{b.get("sector",0):+d}',
        f'  前日结构：{b.get("prev_day",0):+d}',
        f'  盘前异动：{b.get("premarket",0):+d}',
    ]
    
    # 盘前异动 top
    if result.top_watchlist:
        lines += ['', '🎯 盘前异动 Top（你的 watchlist）：']
        for item in result.top_watchlist:
            sym = item['symbol']
            chg = item['premarket_change_pct']
            lines.append(f'  {sym:6s} {chg:+.2f}%')
    
    # 财报今日
    earnings_today = result.snapshot.get('macro', {}).get('watchlist_earnings_today', [])
    if earnings_today:
        lines += ['', '📅 今日财报（你的 watchlist）：']
        lines.append(f'  {", ".join(earnings_today)}')
    
    return '\n'.join(lines)


def send_morning_brief(result: RegimeResult):
    msg = format_morning_brief(result)
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        print(msg)  # fallback print
        return
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    r = requests.post(url, json={
        'chat_id': chat,
        'text': msg,
    }, timeout=10)
    r.raise_for_status()


def _weekday_cn(d: date) -> str:
    days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    return days[d.weekday()]
```

### 4.5 `src/regime/storage.py`

```python
import json
from datetime import date
from .classifier import RegimeResult
# 假设用 SQLAlchemy，简化代码用原生 sqlite
import sqlite3
import os


def save_regime_score(result: RegimeResult):
    conn = sqlite3.connect(_db_path())
    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO regime_scores
          (date, score, label, action,
           d1_direction, d2_volatility, d3_macro_penalty,
           d4_sector, d5_prev_day, d6_premarket,
           snapshot_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        result.date.isoformat(),
        result.score,
        result.label,
        result.action,
        result.breakdown.get('direction', 0),
        result.breakdown.get('volatility', 0),
        result.breakdown.get('macro_penalty', 0),
        result.breakdown.get('sector', 0),
        result.breakdown.get('prev_day', 0),
        result.breakdown.get('premarket', 0),
        json.dumps(result.snapshot, default=str),
    ))
    conn.commit()
    conn.close()


def get_regime_score(target_date: date) -> dict | None:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM regime_scores WHERE date = ?', (target_date.isoformat(),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _db_path():
    return os.environ.get('DATABASE_URL', 'sqlite:///data/daily_stock.db').replace('sqlite:///', '')
```

---

## 五、CLI 入口

```python
# src/regime/cli.py
import argparse
import logging
from datetime import date

from .classifier import compute_regime_score
from .morning_brief import send_morning_brief


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', help='YYYY-MM-DD, default today ET')
    parser.add_argument('--no-send', action='store_true', help='Skip Telegram')
    parser.add_argument('--no-save', action='store_true', help='Skip DB save')
    parser.add_argument('--backfill', nargs=2, help='Start and end date for backfill')
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    if args.backfill:
        from datetime import timedelta
        start = date.fromisoformat(args.backfill[0])
        end = date.fromisoformat(args.backfill[1])
        d = start
        while d <= end:
            if d.weekday() < 5:  # 工作日
                try:
                    r = compute_regime_score(target_date=d, save_to_db=not args.no_save)
                    print(f'{d}: {r.score} ({r.label})')
                except Exception as e:
                    print(f'{d}: FAILED {e}')
            d += timedelta(days=1)
        return
    
    target = date.fromisoformat(args.date) if args.date else None
    result = compute_regime_score(target_date=target, save_to_db=not args.no_save)
    if not args.no_send:
        send_morning_brief(result)
    else:
        from .morning_brief import format_morning_brief
        print(format_morning_brief(result))


if __name__ == '__main__':
    main()
```

**使用**：
```bash
# 每天早上跑
python -m src.regime.cli

# 指定日期
python -m src.regime.cli --date 2026-04-21

# 历史回溯（为校准准备）
python -m src.regime.cli --backfill 2026-01-15 2026-04-16

# 测试模式（不推送不存库）
python -m src.regime.cli --no-send --no-save
```

---

## 六、定时任务

### 6.1 GitHub Actions（推荐）

```yaml
# .github/workflows/regime_brief.yml
name: Daily Regime Brief

on:
  schedule:
    - cron: '0 13 * * 1-5'  # UTC 13:00 = EDT 09:00 / EST 08:00
  workflow_dispatch:

jobs:
  brief:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - env:
          APCA_API_KEY_ID: ${{ secrets.APCA_API_KEY_ID }}
          APCA_API_SECRET_KEY: ${{ secrets.APCA_API_SECRET_KEY }}
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          WATCHLIST: ${{ vars.WATCHLIST }}
        run: python -m src.regime.cli
```

### 6.2 本地 cron（Mac/Linux）

```bash
crontab -e
# 加一行
0 9 * * 1-5 cd /Users/cth/Desktop/daily_stock_analysis && source .venv/bin/activate && python -m src.regime.cli >> ~/regime.log 2>&1
```

---

## 七、测试

```python
# src/regime/tests/test_scorers.py
from src.regime.scorers import (
    score_market_direction, score_volatility, score_macro_penalty,
)


def test_volatility_ideal_vix():
    data = {'vix_now': 16, 'vix_change_pct': -3}
    assert score_volatility(data) == 20  # 15 + 5


def test_volatility_extreme_high():
    data = {'vix_now': 30, 'vix_change_pct': 10}
    assert score_volatility(data) == -15  # -10 - 5


def test_macro_fomc_day():
    events = {'fomc_today': True}
    assert score_macro_penalty(events) == -30


def test_macro_multiple_events():
    events = {'fomc_today': True, 'cpi_today': True}
    assert score_macro_penalty(events) == -50


def test_direction_strong_premarket():
    data = {
        'premarket_change_pct': 0.6,
        'prev_trend_day': True,
        'es_futures_change': 0.005,
    }
    assert score_market_direction(data) == 30  # 15 + 10 + 5
```

运行：
```bash
pip install pytest
pytest src/regime/tests/
```

---

## 八、前端 API 端点

```python
# src/api/routers/regime.py
from fastapi import APIRouter
from datetime import date

router = APIRouter(prefix='/api/v1/regime', tags=['regime'])

@router.get('/today')
async def get_today_regime():
    from src.regime.storage import get_regime_score
    import datetime as dt
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo('America/New_York')).date()
    data = get_regime_score(today)
    if not data:
        return {'status': 'not_computed_yet'}
    return data

@router.get('/history')
async def get_history(days: int = 30):
    # 返回最近 N 天的 score 时序，用于前端画趋势图
    ...

@router.post('/compute')
async def compute_on_demand():
    """手动触发计算（不发 Telegram）。"""
    from src.regime.classifier import compute_regime_score
    result = compute_regime_score(save_to_db=True)
    return result.to_dict()
```

---

## 九、校准与迭代（3 个月后）

Phase 0 跑 3 个月后，用积累的 `regime_scores` 和 `trades` 表做校准：

```sql
-- 每个分数段的实际交易胜率
SELECT 
    CASE 
        WHEN regime_score >= 75 THEN '激进(75+)'
        WHEN regime_score >= 55 THEN '标准(55-74)'
        WHEN regime_score >= 35 THEN '谨慎(35-54)'
        ELSE '不交易(<35)'
    END AS band,
    COUNT(*) AS n_trades,
    AVG(CASE WHEN pnl_net > 0 THEN 1.0 ELSE 0 END) AS win_rate,
    SUM(pnl_net) AS total_pnl
FROM trades t
JOIN regime_scores r ON DATE(t.entry_time) = r.date
WHERE t.status = 'closed'
GROUP BY band;
```

**如果数据显示**：
- "激进" 段胜率 > "标准" 段 > "谨慎" 段 → 系统有效 ✅
- 各段胜率差不多 → 权重需要调整
- "谨慎" 段胜率反而更高 → 评分模型有问题（可能用户在低分日其实更小心所以胜率反而高）

然后可以用逻辑回归重新拟合六维度的权重：
```python
from sklearn.linear_model import LogisticRegression
X = df[['d1_direction', 'd2_volatility', 'd3_macro_penalty', 
        'd4_sector', 'd5_prev_day', 'd6_premarket']].values
y = (df['pnl_net'] > 0).astype(int).values
model = LogisticRegression()
model.fit(X, y)
# model.coef_[0] 就是新的权重
```

校准后更新 `scorers.py` 里的系数，继续观察。

---

## 十、常见问题

**Q：Alpaca 的数据有延迟吗？**  
A：Paper 账户用 IEX feed，免费实时。SIP 数据要付费。IEX 覆盖所有主流股票够用。

**Q：周末没法算，怎么办？**  
A：cron 配的是 `* 1-5`，只工作日跑。周六周日不推送。

**Q：遇到长假（Thanksgiving 等）怎么办？**  
A：用 `pandas_market_calendars` 检查是否交易日，非交易日 skip。加在 `cli.py` 的开头。

**Q：yfinance 偶尔挂怎么办？**  
A：用 `tenacity` 加重试：
```python
from tenacity import retry, stop_after_attempt, wait_exponential
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def get_spy_snapshot(self, ...):
    ...
```

**Q：评分超过 100 了正常吗？**  
A：维度 1-6 最多 30+20+0+15+15+20 = 100，宏观 penalty 可以一直减到 -50+。所以 range 是 [-50, 100]。label 已经按阈值分类，不用担心数值。

**Q：我想调整阈值怎么办？**  
A：改 `classifier.py` 里 `THRESHOLD_*` 常量。建议先跑 3 个月收集数据再调，不要拍脑袋改。
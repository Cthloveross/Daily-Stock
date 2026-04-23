# 06 · Regime Classifier

> **模块**：可交易日判断器（Market Regime Classifier）  
> **位置**：`src/regime/` + 复用 `data_provider/` + 接入 `bot/` + 注册为 Agent skill  
> **前置**：`02_ARCHITECTURE_OVERVIEW.md`、`04_OPTION_SUPPORT_EXTENSION.md`  
> **使命**：**每天早晨用一个数字告诉你今天能不能交易，减少低胜率时段的操作**

---

## 0. 价值与现实

### 0.1 为什么值得做

用户真实 CSV 显示：
- 29% 订单在 09:30-10:00（开盘 30 分钟）
- 单日最多 61 笔
- Top 5 盈利笔占全部利润的 96%

这些数字共同说明一个事实：**很多订单是"冲动"的，不是"选择"的**。

Regime Classifier 的作用不是"告诉你应该买什么"，而是**在你冲动之前给一个外部参照物**："今天的环境整体评分是 42，你打算追突破？"

**光"多问这一句"就能让胜率提升几个百分点**。

### 0.2 Regime 不是算命

> **Regime Score 不是水晶球。它只是"今天这个日子的环境画像"。高分日不保证赚，低分日偶尔也会有大行情。**

正确使用方式：
- **高分日（≥75）**：放开约束，可以追突破（仍要过 Breakout Filter 四层）
- **标准日（55-74）**：只做 retest，不追新高
- **谨慎日（35-54）**：观察为主，已有仓位正常管理
- **不交易日（<35）**：真的不交易，Shadow Trades 练盘感

错误使用：
- ❌ 看分数开仓，不看 setup
- ❌ 低分日"赌一把"
- ❌ 高分日"满仓干"

---

## 1. 六维度评分架构

### 1.1 总览

```
Regime Score = Σ (6 个维度得分)，范围 [-50, 100]

D1 大盘方向        [0, 30]     SPY 盘前 + 前日结构 + ES 期货
D2 波动率          [-15, 20]   VIX 水平 + VIX 变化
D3 宏观事件        [-50, 0]    只减分：FOMC/CPI/NFP/财报日
D4 板块轮动        [-5, 15]    XLK/XLF 等 5 日表现
D5 前日市场结构    [-2, 13]    SPY 前日收盘位置 + 成交量
D6 盘前异动        [0, 20]     watchlist 盘前波动 ≥ ±3% 的只数
```

**为什么是这六个**：每一维度代表一个**不同信号来源**，互不重复：
- D1 = 方向
- D2 = 能量
- D3 = 外部冲击
- D4 = 资金流
- D5 = 延续性
- D6 = 具体机会

少一个维度评分都会失衡；多一个维度会引入噪音（我们尝试过 8 维度和 4 维度，6 是甜点）。

### 1.2 分类阈值

```python
if score >= 75:   label = '高波动激进可交易'
elif score >= 55: label = '标准交易日'
elif score >= 35: label = '谨慎交易日'
else:             label = '不交易日'
```

**为什么阈值是 75/55/35**：
- 75+ 意味着 6 维度中至少 5 个都是"好"——实际很罕见（年均 40-60 天）
- 55-74 是"中性偏正面"——绝大多数交易日（年均 120-150 天）
- 35-54 需要你动脑子判断——不多（年均 40-60 天）
- < 35 通常是 FOMC 日 / CPI 日 / 大崩盘日（年均 10-20 天）

实证校准路径（Phase 0 结束时做）：
```sql
-- Phase 0 跑 3 个月后，验证假设
SELECT 
    CASE 
        WHEN score >= 75 THEN 'aggressive'
        WHEN score >= 55 THEN 'standard'
        WHEN score >= 35 THEN 'cautious'
        ELSE 'no_trade'
    END AS band,
    COUNT(DISTINCT date) AS n_days,
    COUNT(t.id) AS n_trades,
    AVG(CASE WHEN pnl_net > 0 THEN 1.0 ELSE 0 END) AS win_rate,
    SUM(pnl_net) AS total_pnl
FROM regime_scores r
LEFT JOIN trades t ON DATE(t.entry_time) = r.date AND t.status='closed'
GROUP BY band
ORDER BY band;
```

**验证标准**：win_rate 应该随 band 从 no_trade → aggressive 单调上升。如果不是，阈值需要调整（见第 10 节校准）。

---

## 2. 在原项目基础上的落地路径

### 2.1 不要重复造轮子

原项目已经有：
- **`data_provider/`** - 多数据源抽象 + fallback 链。新加 `alpaca_fetcher.py` 和 `finnhub_fetcher.py`
- **`bot/telegram_bot.py`** - 推送能力。复用 `send_message()`
- **`src/core/trading_calendar.py`** - 交易日判断。用来 skip 非交易日
- **`src/core/config_registry.py`** - 配置注册。新字段走这里
- **`templates/` + `REPORT_RENDERER_ENABLED`** - Jinja2 模板。写 `regime_morning_brief.md.j2`
- **`src/agent/skills/` 机制** - Regime 作为 skill 让 Agent 可以主动调用

**不需要重复写**：HTTP 客户端、fallback 逻辑、模板渲染、Telegram 格式化。全部白用。

### 2.2 新建目录

```
src/regime/
├── __init__.py
├── classifier.py         # 主函数 compute_regime_score()
├── scorers.py            # 6 个打分纯函数
├── fetchers.py           # 数据拉取（调 data_provider 下的 fetcher）
├── morning_brief.py      # 格式化 + Telegram 推送
├── storage.py            # DB CRUD（regime_scores 表）
├── backfill.py           # 历史回补脚本
├── cli.py                # 命令行入口
└── tests/
    ├── test_scorers.py
    ├── test_classifier.py
    └── fixtures/
        └── sample_market_data.json

data_provider/
├── alpaca_fetcher.py     # 🟢 新增 - SPY premarket / watchlist 盘前异动
└── finnhub_fetcher.py    # 🟢 新增 - 宏观日历

strategies/
└── regime_check.yaml     # 🟢 新增 - 让 Agent 可以调 Regime Classifier

templates/
└── regime_morning_brief.md.j2  # 🟢 新增

.github/workflows/
└── regime_brief.yml      # 🟢 新增 - 每天 09:00 ET cron
```

---

## 3. 数据库 Schema

```sql
-- migration 002_regime_scores.sql
CREATE TABLE regime_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE UNIQUE NOT NULL,
    score           INTEGER NOT NULL,              -- [-50, 100]
    label           TEXT NOT NULL,                 -- aggressive/standard/cautious/no_trade
    action_hint     TEXT NOT NULL,                 -- 建议动作文本
    -- 六维度拆解
    d1_direction        INTEGER NOT NULL,
    d2_volatility       INTEGER NOT NULL,
    d3_macro_penalty    INTEGER NOT NULL,
    d4_sector           INTEGER NOT NULL,
    d5_prev_day         INTEGER NOT NULL,
    d6_premarket        INTEGER NOT NULL,
    -- 快照（用于后续校准和模型迭代）
    snapshot_json       TEXT,                      -- 所有原始数据 JSON
    -- 用户主观评价（Phase 0 训练用）
    user_perceived_quality  INTEGER,              -- 1-5，用户事后打分（可选）
    user_did_trade          BOOLEAN DEFAULT FALSE, -- 当天是否真的交易了
    user_override_reason    TEXT,                  -- 如果 score<55 但仍交易，写原因
    -- 元数据
    generated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version             TEXT DEFAULT 'v1'          -- 评分模型版本
);

CREATE INDEX idx_regime_date ON regime_scores(date DESC);
CREATE INDEX idx_regime_label ON regime_scores(label);
```

**`version` 字段的用途**：3 个月后重新校准后评分模型会变，要能区分"v1 模型分数"vs"v2 模型分数"。

---

## 4. 六个打分函数

**文件**：`src/regime/scorers.py`

```python
"""六维度打分函数。纯函数，无副作用，易测试。

所有函数签名：
    score_xxx(data: dict) -> int
    
data 的字段约定见对应 fetcher 的文档。
返回该维度得分（可能负数）。
"""
from __future__ import annotations


# ========== D1: 大盘方向 [0, 30] ==========

def score_market_direction(spy: dict) -> int:
    """
    输入：
      spy.premarket_change_pct: float  (-2.5 = -2.5%)
      spy.prev_trend_day: bool         (前一日实体 > 70% range)
      spy.prev_body_ratio: float       (实体比例 0-1)
      spy.es_futures_change: float | None  (隔夜 ES 变化，小数)
    
    规则：
      |pre|>0.5% → +15
      |pre|>0.3% → +10
      |pre|>0.1% → +5
      前一日 trend day → +10
      前一日半实体 → +5
      ES vs SPY premarket 方向一致 → +5
      ES vs SPY 背离 → -5
    """
    score = 0
    pre = abs(spy.get('premarket_change_pct', 0))
    
    if pre > 0.5:
        score += 15
    elif pre > 0.3:
        score += 10
    elif pre > 0.1:
        score += 5
    
    if spy.get('prev_trend_day'):
        score += 10
    elif spy.get('prev_body_ratio', 0) > 0.4:
        score += 5
    
    es = spy.get('es_futures_change')
    spy_pre_frac = spy.get('premarket_change_pct', 0) / 100
    if es is not None and abs(es) > 0.002 and abs(spy_pre_frac) > 0.002:
        if (es > 0) == (spy_pre_frac > 0):
            score += 5
        else:
            score -= 5
    
    return score


# ========== D2: 波动率 [-15, 20] ==========

def score_volatility(vix: dict) -> int:
    """
    VIX 区间的意义：
      14-20  理想（波动足但不极端，+15）
      12-14  偏低（+8，突破易失败）
      20-25  偏高（+10，需宽止损）
      <12    极低（0，没波动没机会）
      >25    极高（-10，插针频发）
    
    VIX 当日变化：
      下降 >2% → +5（风险偏好上升）
      上升 >5% → -5（恐慌中）
    """
    v = vix.get('vix_now', 20)
    chg = vix.get('vix_change_pct', 0)
    
    if 14 <= v <= 20:
        s = 15
    elif 12 <= v < 14:
        s = 8
    elif 20 < v <= 25:
        s = 10
    elif v < 12:
        s = 0
    else:
        s = -10
    
    if chg < -2:
        s += 5
    elif chg > 5:
        s -= 5
    
    return s


# ========== D3: 宏观事件惩罚 [-50, 0] ==========

def score_macro_penalty(events: dict) -> int:
    """只减分。
    
    events 字段：
      fomc_today: bool
      cpi_today: bool / ppi_today
      nfp_today: bool
      pce_today: bool
      ecb_today: bool
      retail_today: bool
      watchlist_earnings_today: list[str]
    """
    penalty = 0
    if events.get('fomc_today'):    penalty -= 30
    if events.get('cpi_today'):     penalty -= 20
    if events.get('ppi_today'):     penalty -= 15
    if events.get('nfp_today'):     penalty -= 20
    if events.get('pce_today'):     penalty -= 10
    if events.get('ecb_today'):     penalty -= 5
    if events.get('retail_today'):  penalty -= 10
    
    earnings = events.get('watchlist_earnings_today', [])
    if len(earnings) >= 3:
        penalty -= 15   # 多只重要财报日
    elif len(earnings) >= 1:
        penalty -= 5
    
    return max(penalty, -50)  # 下限 -50 避免无意义的极端分


# ========== D4: 板块轮动 [-5, 15] ==========

def score_sector_rotation(sectors: dict) -> int:
    """
    sectors: {'XLK': {'return_pct': 2.3}, 'XLF': {...}, ...}
    
    规则：
      ≥2 个板块涨超 2% → +10（资金有方向）
      1 个涨超 2% → +5
      全部 <2% → 0（观望）
      XLK 领涨（且用户偏科技）→ 额外 +5
      XLK 明显下跌（< -2%）→ -5
      防御板块（XLU/XLP）领涨 → -5
    """
    if not sectors:
        return 0
    
    leaders = [k for k, v in sectors.items() if v.get('return_pct', 0) > 2]
    sorted_s = sorted(sectors.items(), key=lambda kv: -kv[1].get('return_pct', 0))
    
    if len(leaders) >= 2:
        s = 10
    elif len(leaders) == 1:
        s = 5
    else:
        s = 0
    
    # XLK 特殊处理（用户偏科技）
    xlk = sectors.get('XLK', {}).get('return_pct', 0)
    if xlk > 2 and sorted_s[0][0] == 'XLK':
        s += 5
    elif xlk < -2:
        s -= 5
    
    # 防御板块领涨 → 减分
    if sorted_s[0][0] in ('XLU', 'XLP'):
        s -= 5
    
    return s


# ========== D5: 前日市场结构 [-2, 13] ==========

def score_prev_day_structure(prev_day: dict) -> int:
    """
    prev_day 字段：
      close_position_in_range: float (0=最低, 1=最高)
      prev_volume_vs_avg20: float
    
    收在高点附近 → 延续性强
    收在低点附近 → 可能反弹也可能破位，不加分
    放量 → 参与度高，信号更可信
    """
    if not prev_day:
        return 0
    
    s = 0
    pos = prev_day.get('close_position_in_range', 0.5)
    
    if pos > 0.97:
        s += 8
    elif 0.6 < pos < 0.97:
        s += 5
    # 0.3-0.6 不加不减
    # <0.3 也不加分但不减（避免漏掉大反转机会）
    
    vol_ratio = prev_day.get('prev_volume_vs_avg20', 1)
    if vol_ratio > 1.2:
        s += 5
    elif vol_ratio < 0.8:
        s -= 2
    
    return s


# ========== D6: 盘前异动 [0, 20] ==========

def score_premarket_activity(premarket: dict) -> int:
    """
    premarket: {
        'symbols': [{'symbol': 'NVDA', 'premarket_change_pct': 3.2}, ...],
        'active_count_3pct': int  # 盘前涨跌 >= 3% 的只数
    }
    
    越多异动 = 越多可选机会。
    """
    active = premarket.get('active_count_3pct', 0)
    
    if active >= 3:
        return 15
    if active >= 1:
        return 8
    return 0
```

### 4.1 测试

**文件**：`src/regime/tests/test_scorers.py`

```python
import pytest
from src.regime.scorers import (
    score_market_direction, score_volatility, score_macro_penalty,
    score_sector_rotation, score_prev_day_structure, score_premarket_activity,
)


# ========== D1 ==========
class TestDirection:
    def test_strong_premarket_plus_trend_day(self):
        data = {
            'premarket_change_pct': 0.6,
            'prev_trend_day': True,
            'prev_body_ratio': 0.75,
            'es_futures_change': 0.005,
        }
        # 15 + 10 + 5 = 30
        assert score_market_direction(data) == 30
    
    def test_flat_premarket(self):
        data = {'premarket_change_pct': 0.05, 'prev_trend_day': False}
        assert score_market_direction(data) == 0
    
    def test_es_spy_divergence_penalty(self):
        data = {
            'premarket_change_pct': 0.4,
            'es_futures_change': -0.01,  # ES 跌 1% 但 SPY 盘前涨 0.4% → 背离
        }
        # 10 (pre) + 0 (no trend day) - 5 (divergence) = 5
        assert score_market_direction(data) == 5


# ========== D2 ==========
class TestVolatility:
    def test_ideal_vix(self):
        assert score_volatility({'vix_now': 16, 'vix_change_pct': 0}) == 15
    
    def test_ideal_vix_plus_declining(self):
        assert score_volatility({'vix_now': 16, 'vix_change_pct': -3}) == 20
    
    def test_extreme_high_vix_plus_rising(self):
        assert score_volatility({'vix_now': 30, 'vix_change_pct': 10}) == -15
    
    def test_extreme_low_vix(self):
        assert score_volatility({'vix_now': 11, 'vix_change_pct': 0}) == 0


# ========== D3 ==========
class TestMacro:
    def test_no_events(self):
        assert score_macro_penalty({}) == 0
    
    def test_fomc_day(self):
        assert score_macro_penalty({'fomc_today': True}) == -30
    
    def test_fomc_plus_cpi(self):
        # FOMC -30 + CPI -20 = -50
        assert score_macro_penalty({'fomc_today': True, 'cpi_today': True}) == -50
    
    def test_lower_bound(self):
        # 即便把所有事件堆上也不会低于 -50
        all_events = {
            'fomc_today': True, 'cpi_today': True, 'ppi_today': True,
            'nfp_today': True, 'pce_today': True,
            'watchlist_earnings_today': ['NVDA', 'MSFT', 'TSLA'],
        }
        assert score_macro_penalty(all_events) == -50


# ========== D4 ==========
class TestSector:
    def test_xlk_leading_with_other_gainers(self):
        sectors = {
            'XLK': {'return_pct': 3.0},
            'XLY': {'return_pct': 2.5},
            'XLU': {'return_pct': 0.1},
        }
        # 10 (2 leaders) + 5 (XLK leading) = 15
        assert score_sector_rotation(sectors) == 15
    
    def test_defensive_leading(self):
        sectors = {
            'XLU': {'return_pct': 1.5},
            'XLP': {'return_pct': 1.2},
            'XLK': {'return_pct': -1.0},
        }
        # 0 + 0 (XLK 没跌 >2%) - 5 (XLU 领涨) = -5
        assert score_sector_rotation(sectors) == -5


# ========== D5, D6 (类似结构，略) ==========
```

---

## 5. 数据拉取（复用 data_provider）

### 5.1 新增两个 fetcher

**文件**：`data_provider/alpaca_fetcher.py`

```python
"""Alpaca 数据源。免费 paper account 可拿 IEX 实时数据。

核心用途：
1. SPY / watchlist 盘前 K 线（Regime D1/D6 需要）
2. 2min K 线（Breakout Filter 用）
3. Benzinga 新闻（事件触发用）
"""
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')


class AlpacaFetcher:
    BASE = 'https://data.alpaca.markets'
    
    def __init__(self):
        self.key = os.environ.get('APCA_API_KEY_ID')
        self.secret = os.environ.get('APCA_API_SECRET_KEY')
    
    @property
    def available(self) -> bool:
        return bool(self.key and self.secret)
    
    def _headers(self) -> dict:
        return {
            'APCA-API-KEY-ID': self.key,
            'APCA-API-SECRET-KEY': self.secret,
        }
    
    def premarket_change_pct(self, symbol: str, target_date: Optional[date] = None) -> Optional[float]:
        """用 5Min bars 计算盘前相对前日收盘的涨跌幅。"""
        if not self.available:
            return None
        
        if target_date is None:
            target_date = datetime.now(ET).date()
        
        start = datetime.combine(target_date, datetime.min.time(), tzinfo=ET)
        start = start.replace(hour=4, minute=0)   # 04:00 ET 盘前开始
        end = start.replace(hour=9, minute=29)    # 09:29 最后一根盘前
        
        try:
            r = requests.get(
                f'{self.BASE}/v2/stocks/bars',
                params={
                    'symbols': symbol,
                    'timeframe': '5Min',
                    'start': start.isoformat(),
                    'end': end.isoformat(),
                    'feed': 'iex',
                },
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            bars = r.json().get('bars', {}).get(symbol, [])
            if not bars:
                return None
            
            last_pre_close = bars[-1]['c']
            
            # 前日收盘
            prev_close = self._previous_trading_day_close(symbol, target_date)
            if prev_close is None:
                return None
            
            return (last_pre_close - prev_close) / prev_close * 100
        except Exception:
            logger.exception(f'Alpaca premarket 失败 {symbol}')
            return None
    
    def _previous_trading_day_close(self, symbol: str, ref: date) -> Optional[float]:
        start = (ref - timedelta(days=7)).isoformat()
        end = ref.isoformat()
        try:
            r = requests.get(
                f'{self.BASE}/v2/stocks/bars',
                params={
                    'symbols': symbol,
                    'timeframe': '1Day',
                    'start': start,
                    'end': end,
                    'feed': 'iex',
                },
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            bars = r.json().get('bars', {}).get(symbol, [])
            if len(bars) < 1:
                return None
            # 最后一根就是前一个交易日
            return bars[-1]['c']
        except Exception:
            return None
    
    def bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]:
        """通用 bars 接口。timeframe: '1Min' / '5Min' / '15Min' / '1Hour' / '1Day'"""
        if not self.available:
            return []
        try:
            r = requests.get(
                f'{self.BASE}/v2/stocks/bars',
                params={
                    'symbols': symbol,
                    'timeframe': timeframe,
                    'start': start.isoformat(),
                    'end': end.isoformat(),
                    'feed': 'iex',
                    'limit': 10000,
                },
                headers=self._headers(),
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get('bars', {}).get(symbol, [])
        except Exception:
            logger.exception(f'Alpaca bars 失败 {symbol}')
            return []
```

**文件**：`data_provider/finnhub_fetcher.py`

```python
"""Finnhub 免费层。宏观日历 + 分析师评级（可选）。"""
import logging
import os
from datetime import date

import requests

logger = logging.getLogger(__name__)


class FinnhubFetcher:
    BASE = 'https://finnhub.io/api/v1'
    
    def __init__(self):
        self.key = os.environ.get('FINNHUB_API_KEY')
    
    @property
    def available(self) -> bool:
        return bool(self.key)
    
    def economic_calendar(self, start: date, end: date) -> list[dict]:
        """宏观经济数据日历。"""
        if not self.available:
            return []
        try:
            r = requests.get(
                f'{self.BASE}/calendar/economic',
                params={'from': start.isoformat(), 'to': end.isoformat(), 'token': self.key},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get('economicCalendar', [])
        except Exception:
            logger.exception('Finnhub economic 失败')
            return []
    
    def earnings_calendar(self, start: date, end: date, symbols: list[str] = None) -> list[dict]:
        """财报日历。可传 symbols 过滤。"""
        if not self.available:
            return []
        try:
            r = requests.get(
                f'{self.BASE}/calendar/earnings',
                params={
                    'from': start.isoformat(),
                    'to': end.isoformat(),
                    'token': self.key,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get('earningsCalendar', [])
            if symbols:
                symbols_set = {s.upper() for s in symbols}
                data = [e for e in data if e.get('symbol', '').upper() in symbols_set]
            return data
        except Exception:
            logger.exception('Finnhub earnings 失败')
            return []
```

### 5.2 Regime 数据聚合

**文件**：`src/regime/fetchers.py`

```python
"""Regime 专用的数据聚合层。不新建 HTTP 客户端，全部调 data_provider。"""
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

from data_provider.alpaca_fetcher import AlpacaFetcher
from data_provider.finnhub_fetcher import FinnhubFetcher
from data_provider.yfinance_fetcher import YFinanceFetcher  # 原项目已有

logger = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')


class RegimeDataFetcher:
    """Regime 六维度的数据聚合器。
    
    每个 get_xxx 方法返回对应 scorer 需要的 dict。
    失败时返回部分字段或空 dict（fail-open），保证主流程不挂。
    """
    
    def __init__(self):
        self.alpaca = AlpacaFetcher()
        self.finnhub = FinnhubFetcher()
        self.yf = YFinanceFetcher()  # 原项目的 wrapper
    
    # ---------- D1 ----------
    def get_spy_snapshot(self, target_date: date) -> dict:
        spy = yf.Ticker('SPY')
        hist = spy.history(start=target_date - timedelta(days=10), end=target_date, interval='1d')
        if len(hist) == 0:
            return {}
        prev = hist.iloc[-1]
        
        pre_pct = self.alpaca.premarket_change_pct('SPY', target_date)
        if pre_pct is None:
            pre_pct = self._yfinance_premarket_fallback('SPY', target_date)
        
        # ES overnight
        try:
            es_hist = yf.Ticker('ES=F').history(period='2d', interval='1h')
            es_chg = (es_hist.iloc[-1]['Close'] - es_hist.iloc[0]['Close']) / es_hist.iloc[0]['Close']
        except Exception:
            es_chg = None
        
        total_range = prev['High'] - prev['Low']
        body = abs(prev['Close'] - prev['Open'])
        body_ratio = body / total_range if total_range > 0 else 0
        
        return {
            'premarket_change_pct': pre_pct or 0,
            'prev_close': float(prev['Close']),
            'prev_open': float(prev['Open']),
            'prev_high': float(prev['High']),
            'prev_low': float(prev['Low']),
            'prev_body_ratio': body_ratio,
            'prev_trend_day': body_ratio > 0.7,
            'es_futures_change': es_chg,
        }
    
    def _yfinance_premarket_fallback(self, symbol: str, target_date: date) -> float:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period='5d', interval='1m', prepost=True)
            if len(hist) == 0:
                return 0
            premarket_end = datetime.combine(target_date, datetime.min.time(), tzinfo=ET)
            premarket_end = premarket_end.replace(hour=9, minute=29)
            pre_bars = hist[hist.index <= premarket_end]
            if len(pre_bars) == 0:
                return 0
            last_pre = pre_bars.iloc[-1]['Close']
            daily = t.history(period='5d', interval='1d')
            prev_close = daily.iloc[-2]['Close'] if len(daily) >= 2 else daily.iloc[-1]['Close']
            return (last_pre - prev_close) / prev_close * 100
        except Exception:
            return 0
    
    # ---------- D2 ----------
    def get_vix(self, target_date: date) -> dict:
        try:
            vix = yf.Ticker('^VIX').history(period='5d', interval='1d')
            if len(vix) < 2:
                return {'vix_now': 20.0, 'vix_change_pct': 0}
            prev = vix.iloc[-2]['Close']
            now = vix.iloc[-1]['Close']
            return {
                'vix_now': float(now),
                'vix_prev': float(prev),
                'vix_change_pct': (now - prev) / prev * 100,
            }
        except Exception:
            logger.exception('VIX 拉取失败')
            return {'vix_now': 20.0, 'vix_change_pct': 0}
    
    # ---------- D3 ----------
    def get_macro_events(self, target_date: date, watchlist: list[str]) -> dict:
        events = {
            'fomc_today': False, 'cpi_today': False, 'ppi_today': False,
            'nfp_today': False, 'pce_today': False, 'ecb_today': False,
            'retail_today': False, 'watchlist_earnings_today': [],
        }
        
        # 宏观日历
        if self.finnhub.available:
            cal = self.finnhub.economic_calendar(target_date, target_date)
            for ev in cal:
                name = (ev.get('event') or '').lower()
                country = (ev.get('country') or '').lower()
                is_us = 'us' in country or 'united states' in country
                if not is_us and 'ecb' not in name:
                    continue
                if 'fomc' in name or 'fed interest' in name or 'federal funds' in name:
                    events['fomc_today'] = True
                elif 'cpi' in name or 'consumer price' in name:
                    events['cpi_today'] = True
                elif 'ppi' in name or 'producer price' in name:
                    events['ppi_today'] = True
                elif 'non-farm' in name or 'nonfarm' in name or 'nfp' in name:
                    events['nfp_today'] = True
                elif 'pce' in name:
                    events['pce_today'] = True
                elif 'ecb' in name:
                    events['ecb_today'] = True
                elif 'retail sales' in name:
                    events['retail_today'] = True
        
        # Watchlist 财报
        for sym in watchlist:
            try:
                cal = yf.Ticker(sym).calendar
                if cal is not None and 'Earnings Date' in cal:
                    ed = cal['Earnings Date']
                    if isinstance(ed, list) and len(ed) > 0:
                        ed = ed[0]
                    if ed and abs((ed - target_date).days) <= 1:
                        events['watchlist_earnings_today'].append(sym)
            except Exception:
                continue
        
        return events
    
    # ---------- D4 ----------
    def get_sector_performance(self, target_date: date, lookback_days: int = 5) -> dict:
        sector_etfs = {
            'XLK': 'Technology', 'XLF': 'Financials', 'XLE': 'Energy',
            'XLV': 'Healthcare', 'XLY': 'Consumer Disc', 'XLC': 'Communications',
            'XLI': 'Industrials', 'XLP': 'Consumer Staples',
            'XLU': 'Utilities', 'XLB': 'Materials', 'XLRE': 'Real Estate',
        }
        result = {}
        for etf, sector in sector_etfs.items():
            try:
                hist = yf.Ticker(etf).history(
                    start=target_date - timedelta(days=lookback_days + 3),
                    end=target_date,
                )
                if len(hist) < 2:
                    continue
                ret = (hist.iloc[-1]['Close'] - hist.iloc[0]['Close']) / hist.iloc[0]['Close'] * 100
                result[etf] = {'sector': sector, 'return_pct': float(ret)}
            except Exception:
                continue
        return result
    
    # ---------- D5 ----------
    def get_prev_day_structure(self, target_date: date) -> dict:
        try:
            hist = yf.Ticker('SPY').history(
                start=target_date - timedelta(days=40), end=target_date, interval='1d',
            )
            if len(hist) < 2:
                return {}
            prev = hist.iloc[-1]
            avg_vol = hist.iloc[-21:-1]['Volume'].mean() if len(hist) >= 21 else prev['Volume']
            
            day_range = prev['High'] - prev['Low']
            pos = (prev['Close'] - prev['Low']) / day_range if day_range > 0 else 0.5
            
            return {
                'prev_close': float(prev['Close']),
                'prev_high': float(prev['High']),
                'prev_low': float(prev['Low']),
                'prev_volume': int(prev['Volume']),
                'prev_volume_vs_avg20': float(prev['Volume'] / avg_vol) if avg_vol > 0 else 1,
                'close_position_in_range': float(pos),
            }
        except Exception:
            return {}
    
    # ---------- D6 ----------
    def get_premarket_activity(self, watchlist: list[str], target_date: date) -> dict:
        results = []
        for sym in watchlist:
            try:
                chg = self.alpaca.premarket_change_pct(sym, target_date)
                if chg is None:
                    chg = self._yfinance_premarket_fallback(sym, target_date)
                results.append({
                    'symbol': sym,
                    'premarket_change_pct': chg or 0,
                })
            except Exception:
                continue
        return {
            'symbols': results,
            'active_count_3pct': sum(1 for r in results if abs(r['premarket_change_pct']) >= 3),
        }
```

---

## 6. Classifier 主函数

**文件**：`src/regime/classifier.py`

```python
"""Regime Classifier 主入口。"""
import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.regime.fetchers import RegimeDataFetcher
from src.regime.scorers import (
    score_market_direction, score_volatility, score_macro_penalty,
    score_sector_rotation, score_prev_day_structure, score_premarket_activity,
)
from src.regime.storage import save_regime_score

logger = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')


THRESHOLD_AGGRESSIVE = 75
THRESHOLD_STANDARD = 55
THRESHOLD_CAUTIOUS = 35


@dataclass
class RegimeResult:
    date: date
    score: int
    label: str
    action_hint: str
    breakdown: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)
    top_watchlist: list = field(default_factory=list)
    version: str = 'v1'
    
    def to_dict(self):
        d = asdict(self)
        d['date'] = self.date.isoformat()
        return d


def classify(score: int) -> tuple[str, str]:
    if score >= THRESHOLD_AGGRESSIVE:
        return ('aggressive', '可以追突破（配合 Breakout Filter 四层过滤）')
    if score >= THRESHOLD_STANDARD:
        return ('standard', '只做回调买入和 EMA retest，不追新高')
    if score >= THRESHOLD_CAUTIOUS:
        return ('cautious', '仅观察，不主动入场；已有仓位正常管理')
    return ('no_trade', '完全观望，不开新仓（Shadow Trades 除外）')


def compute_regime_score(
    target_date: date | None = None,
    watchlist: list[str] | None = None,
    save_to_db: bool = True,
) -> RegimeResult:
    """计算指定日期的 Regime Score。"""
    if target_date is None:
        target_date = datetime.now(ET).date()
    if watchlist is None:
        watchlist = [s.strip() for s in os.environ.get('WATCHLIST', '').split(',') if s.strip()]
    
    fetcher = RegimeDataFetcher()
    
    # 拉数据（fail-open）
    snapshot = {}
    try:
        snapshot['spy'] = fetcher.get_spy_snapshot(target_date)
        snapshot['vix'] = fetcher.get_vix(target_date)
        snapshot['macro'] = fetcher.get_macro_events(target_date, watchlist)
        snapshot['sector'] = fetcher.get_sector_performance(target_date)
        snapshot['prev_day'] = fetcher.get_prev_day_structure(target_date)
        snapshot['premarket'] = fetcher.get_premarket_activity(watchlist, target_date)
    except Exception:
        logger.exception('Regime 数据拉取部分失败')
    
    # 打分
    breakdown = {
        'd1_direction': score_market_direction(snapshot.get('spy', {})),
        'd2_volatility': score_volatility(snapshot.get('vix', {})),
        'd3_macro_penalty': score_macro_penalty(snapshot.get('macro', {})),
        'd4_sector': score_sector_rotation(snapshot.get('sector', {})),
        'd5_prev_day': score_prev_day_structure(snapshot.get('prev_day', {})),
        'd6_premarket': score_premarket_activity(snapshot.get('premarket', {})),
    }
    score = sum(breakdown.values())
    
    label, action_hint = classify(score)
    
    top_watchlist = sorted(
        snapshot.get('premarket', {}).get('symbols', []),
        key=lambda x: -abs(x.get('premarket_change_pct', 0)),
    )[:5]
    
    result = RegimeResult(
        date=target_date,
        score=score,
        label=label,
        action_hint=action_hint,
        breakdown=breakdown,
        snapshot=snapshot,
        top_watchlist=top_watchlist,
    )
    
    if save_to_db:
        save_regime_score(result)
    
    return result
```

---

## 7. 晨报格式化（走原项目模板引擎）

### 7.1 Jinja 模板

**文件**：`templates/regime_morning_brief.md.j2`

```jinja
🌅 **可交易日评分 | {{ result.date }} ({{ weekday }})**

📊 综合评分：**{{ result.score }}** / 100 → _{{ label_cn }}_

### 建议动作

{% if result.label == 'aggressive' %}
✅ 可以追突破（配合 Breakout Filter 第二层过滤）
✅ Volume 2x + 多周期 + RS 全过才入场
⚠️ 开盘 30 分钟仍禁止
{% elif result.label == 'standard' %}
✅ 只做回调买入和 EMA retest
❌ 不追一日新高
❌ 不在开盘 30 分钟内入场
{% elif result.label == 'cautious' %}
⚠️ 仅观察，不主动入场
✅ 已有仓位正常管理
✅ 做 Shadow Trades 练盘感
{% else %}
❌ 完全观望
❌ 不开新仓
✅ 可以做 Shadow Trades
{% endif %}

### 维度拆解

| 维度 | 得分 | 备注 |
|------|------|------|
| 大盘方向 | {{ '%+d' | format(result.breakdown.d1_direction) }} | SPY 盘前 {{ '%+.2f%%' | format(snapshot.spy.premarket_change_pct) }} |
| 波动率 | {{ '%+d' | format(result.breakdown.d2_volatility) }} | VIX {{ '%.1f' | format(snapshot.vix.vix_now) }} ({{ '%+.1f%%' | format(snapshot.vix.vix_change_pct) }}) |
| 宏观事件 | {{ '%+d' | format(result.breakdown.d3_macro_penalty) }} | {{ macro_summary }} |
| 板块轮动 | {{ '%+d' | format(result.breakdown.d4_sector) }} | {{ sector_summary }} |
| 前日结构 | {{ '%+d' | format(result.breakdown.d5_prev_day) }} | SPY 收在 Range 的 {{ '%.0f%%' | format(snapshot.prev_day.close_position_in_range * 100) }} |
| 盘前异动 | {{ '%+d' | format(result.breakdown.d6_premarket) }} | {{ snapshot.premarket.active_count_3pct }} 只 ≥3% 波动 |

{% if result.top_watchlist %}
### 🎯 盘前异动 Top（watchlist）

{% for item in result.top_watchlist -%}
{{ "%-6s" | format(item.symbol) }} {{ '%+.2f%%' | format(item.premarket_change_pct) }}
{% endfor %}
{% endif %}

{% if snapshot.macro.watchlist_earnings_today %}
### 📅 今日财报（watchlist）

{{ snapshot.macro.watchlist_earnings_today | join(', ') }}
{% endif %}

---
_评分模型版本 {{ result.version }}_
```

### 7.2 推送逻辑

**文件**：`src/regime/morning_brief.py`

```python
"""Regime 晨报格式化 + 推送。复用 bot/ 下的推送通道。"""
import logging
import os
from datetime import date

from src.reports.renderer import render_template  # 原项目 Jinja 渲染引擎
from bot.telegram_bot import send_message as send_tg
# 如果用户禁用 Telegram 启用了邮件，可以复用 bot.email_notifier

logger = logging.getLogger(__name__)

WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
LABEL_CN = {
    'aggressive': '高波动激进可交易',
    'standard': '标准交易日',
    'cautious': '谨慎交易日',
    'no_trade': '不交易日',
}


def _build_summaries(snapshot: dict) -> dict:
    """为 Jinja 模板生成概要文本。"""
    macro = snapshot.get('macro', {})
    events = []
    if macro.get('fomc_today'): events.append('FOMC')
    if macro.get('cpi_today'): events.append('CPI')
    if macro.get('ppi_today'): events.append('PPI')
    if macro.get('nfp_today'): events.append('NFP')
    if macro.get('pce_today'): events.append('PCE')
    macro_summary = ', '.join(events) if events else '无重大事件'
    
    sector = snapshot.get('sector', {})
    if sector:
        sorted_s = sorted(sector.items(), key=lambda kv: -kv[1].get('return_pct', 0))
        top = sorted_s[0]
        sector_summary = f"{top[0]} 领涨 {top[1]['return_pct']:+.1f}%"
    else:
        sector_summary = '无数据'
    
    return {'macro_summary': macro_summary, 'sector_summary': sector_summary}


def format_brief(result) -> str:
    """使用原项目 Jinja 模板引擎渲染晨报。"""
    ctx = {
        'result': result.to_dict() if hasattr(result, 'to_dict') else result,
        'snapshot': result.snapshot,
        'weekday': WEEKDAY_CN[result.date.weekday()],
        'label_cn': LABEL_CN.get(result.label, result.label),
        **_build_summaries(result.snapshot),
    }
    return render_template('regime_morning_brief.md.j2', **ctx)


def send_brief(result) -> None:
    """推送晨报到配置好的通道。"""
    msg = format_brief(result)
    
    # 默认 Telegram。原项目 bot 已有多渠道分发逻辑，这里直接走 telegram_bot
    # 如果用户配置了其他渠道（企微/飞书），bot 模块会自动同步
    try:
        send_tg(msg, parse_mode='Markdown')
    except Exception:
        logger.exception('Telegram 推送失败')
        print(msg)  # fallback
```

---

## 8. CLI + Cron

### 8.1 CLI

**文件**：`src/regime/cli.py`

```python
"""Regime Classifier 命令行入口。

使用：
  # 今天跑 + 推送
  python -m src.regime.cli
  
  # 指定日期（不推送，仅打印）
  python -m src.regime.cli --date 2026-04-21 --no-send
  
  # 历史回补（Phase 0 用）
  python -m src.regime.cli --backfill 2026-01-15 2026-04-16
  
  # 只算不存 DB（测试用）
  python -m src.regime.cli --no-save --no-send
"""
import argparse
import logging
from datetime import date, timedelta


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD, 默认今天 ET')
    p.add_argument('--backfill', nargs=2, metavar=('START', 'END'),
                   help='回补日期范围 YYYY-MM-DD YYYY-MM-DD')
    p.add_argument('--no-send', action='store_true', help='不推送 Telegram')
    p.add_argument('--no-save', action='store_true', help='不写 DB')
    p.add_argument('--skip-holidays', action='store_true', default=True,
                   help='跳过非交易日')
    args = p.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    from src.regime.classifier import compute_regime_score
    from src.regime.morning_brief import send_brief, format_brief
    
    if args.backfill:
        from src.core.trading_calendar import is_trading_day
        start = date.fromisoformat(args.backfill[0])
        end = date.fromisoformat(args.backfill[1])
        
        d = start
        while d <= end:
            if args.skip_holidays and not is_trading_day(d, market='US'):
                d += timedelta(days=1)
                continue
            try:
                r = compute_regime_score(target_date=d, save_to_db=not args.no_save)
                print(f'{d}: score={r.score} label={r.label}')
            except Exception as e:
                print(f'{d}: FAILED {e}')
            d += timedelta(days=1)
        return
    
    target = date.fromisoformat(args.date) if args.date else None
    result = compute_regime_score(target_date=target, save_to_db=not args.no_save)
    
    if args.no_send:
        print(format_brief(result))
    else:
        send_brief(result)


if __name__ == '__main__':
    main()
```

### 8.2 GitHub Actions

**文件**：`.github/workflows/regime_brief.yml`

```yaml
name: Daily Regime Brief

on:
  schedule:
    # 美东 09:00（夏令 EDT = UTC-4，冬令 EST = UTC-5）
    # 用 UTC 13:00 = EDT 09:00；冬令会变成 EST 08:00（提前 1 小时，可接受）
    - cron: '0 13 * * 1-5'
  workflow_dispatch:
    inputs:
      target_date:
        description: 'YYYY-MM-DD (可选，默认今天)'
        required: false

jobs:
  brief:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - run: pip install -r requirements.txt
      
      - name: Compute and send regime brief
        env:
          APCA_API_KEY_ID: ${{ secrets.APCA_API_KEY_ID }}
          APCA_API_SECRET_KEY: ${{ secrets.APCA_API_SECRET_KEY }}
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          WATCHLIST: ${{ vars.WATCHLIST }}
          # 复用原项目 LiteLLM 配置（Phase 1+ 如果 Agent 需要）
          LITELLM_MODEL: ${{ secrets.LITELLM_MODEL }}
        run: |
          if [ -n "${{ github.event.inputs.target_date }}" ]; then
            python -m src.regime.cli --date ${{ github.event.inputs.target_date }}
          else
            python -m src.regime.cli
          fi
```

**关于 DB 持久化**：
- GitHub Actions 环境没有持久存储
- 两个方案：
  1. 把 SQLite DB commit 进 repo（简单，适合 Phase 0）
  2. 把 DB 放到云存储（S3 / GCS）每次下载上传（复杂但正式）

Phase 0 用方案 1（见 `.github/workflows/` 里现有 workflow 的做法，原项目大概率也是这么干的）。

---

## 9. Agent Skill 接入

让 Agent 可以在用户问"今天能不能交易"时主动调 Regime Classifier。

### 9.1 Skill YAML

**文件**：`strategies/regime_check.yaml`

```yaml
# 在 Agent 问股 /chat 场景中，让 Agent 可以主动查询今日 Regime
id: regime_check
name: 可交易日判断
category: market_analysis
enabled: true
description: |
  根据六维度模型评估今日美股市场整体环境，返回 Regime Score 和建议动作。
  用于：用户询问"今天能不能交易"/"今天适合追突破吗"/"今天应该观望吗"。

tools_required:
  - get_regime_score           # 新增工具
  - get_portfolio_snapshot     # 原有工具
  # 可选：get_journal_snapshot 看历史分数

system_prompt: |
  你是美股交易教练。用户询问今日市场环境。
  
  严格按以下规则回答：
  1. 调用 get_regime_score 拿今日分数
  2. 解释六维度拆解（不是简单给分数，要说为什么）
  3. 基于 label 给建议动作，不拍脑袋
  4. 如果用户有未平仓持仓（调 get_portfolio_snapshot），评估持仓是否需要调整
  5. 如果用户明确问"我今天该交易 X 吗"，结合 watchlist 盘前异动回答，不要空泛
  6. 不用鼓励话（加油/相信自己等禁用）
  7. 不给确定性判断（不说"今天一定会涨"）

output_format: |
  结构化，含：
  - 今日 Regime Score 数字 + label
  - 三条最关键的数据点（不是全部 6 维）
  - 具体建议（动作清单）
  - 如果低分日但用户想交易，明确"为什么系统建议不交易"

examples:
  - user: 今天能追突破吗？
    ideal: |
      今日 Regime Score 42（谨慎日）。
      关键数据：
      - CPI 今天 8:30 发布（D3 -20）
      - VIX 22.3（+4.5%）（D2 +5）
      - SPY 盘前 +0.08%（D1 +0）
      
      建议不追突破。谨慎日历史胜率低，尤其 CPI 公布前后 30 分钟容易假突破。
      可做：
      - 观察 CPI 公布后的明确方向再考虑
      - 做 Shadow Trade 记录你看中的 setup
```

### 9.2 Agent 工具

**文件**：`src/agent/tools/get_regime_score.py`

```python
"""Agent 工具：获取 Regime Score。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

TOOL_METADATA = {
    'name': 'get_regime_score',
    'description': '获取指定日期的美股 Regime Score（0-100 评分 + 分类标签 + 六维度拆解）。默认返回今日。',
    'parameters': {
        'type': 'object',
        'properties': {
            'target_date': {
                'type': 'string',
                'description': 'YYYY-MM-DD。默认今天 ET。',
            },
            'recompute': {
                'type': 'boolean',
                'description': '强制重算（忽略 DB 缓存）',
                'default': False,
            },
        },
    },
}


def execute(target_date: str = None, recompute: bool = False) -> dict:
    from src.regime.storage import get_regime_score
    from src.regime.classifier import compute_regime_score
    
    ET = ZoneInfo('America/New_York')
    if target_date:
        d = date.fromisoformat(target_date)
    else:
        d = datetime.now(ET).date()
    
    if not recompute:
        cached = get_regime_score(d)
        if cached:
            return cached
    
    result = compute_regime_score(target_date=d, save_to_db=True)
    return result.to_dict()
```

### 9.3 在哪里注册

看原项目的 skill 注册逻辑：
- `strategies/*.yaml` 会被 `src/agent/skills/` 加载器扫描
- 可能还需要在 `.claude/skills/` 下放一个 `SKILL.md` bundle（原项目 README 提到这个）

具体机制我没读到代码，开工时看实际怎么做。如果原项目有 `strategies/bull_trend.yaml` 这样的现成 skill，就参考它的结构改写。

---

## 10. Phase 0 完成后的校准

Phase 0 跑 12 周后，积累 60+ 天的 `regime_scores` 数据 + 对应的 `trades` 数据。用这份数据做两件事：

### 10.1 验证评分模型有效性

```python
# src/regime/backfill.py 里加这个

def validate_regime_model():
    """跑统计，看每个 band 的实际胜率。"""
    import sqlite3
    from src.config import DB_URL
    
    conn = sqlite3.connect(DB_URL.replace('sqlite:///', ''))
    cur = conn.cursor()
    
    cur.execute('''
        SELECT 
            CASE 
                WHEN r.score >= 75 THEN 'aggressive'
                WHEN r.score >= 55 THEN 'standard'
                WHEN r.score >= 35 THEN 'cautious'
                ELSE 'no_trade'
            END AS band,
            COUNT(DISTINCT r.date) AS n_days,
            COUNT(t.id) AS n_trades,
            AVG(CASE WHEN t.pnl_net > 0 THEN 1.0 ELSE 0 END) AS win_rate,
            SUM(t.pnl_net) AS total_pnl,
            AVG(t.pnl_net) AS avg_pnl
        FROM regime_scores r
        LEFT JOIN trades t ON DATE(t.entry_time) = r.date AND t.status='closed'
        WHERE r.date >= DATE('now', '-90 days')
        GROUP BY band
        ORDER BY 
            CASE band
                WHEN 'aggressive' THEN 1
                WHEN 'standard' THEN 2
                WHEN 'cautious' THEN 3
                WHEN 'no_trade' THEN 4
            END
    ''')
    
    rows = cur.fetchall()
    for row in rows:
        print(f'{row[0]:12s}: {row[1]:3d} 天, {row[2]:4d} 笔, 胜率 {row[3]*100:.1f}%, pnl ${row[4]:.0f}')
    
    conn.close()
```

**通过条件**：win_rate 单调递减（aggressive > standard > cautious）。

### 10.2 权重调优（可选，Phase 1 做）

如果模型没通过单调性检验，用逻辑回归重新拟合权重：

```python
def refit_weights():
    """用 Phase 0 数据重新拟合六维度权重。"""
    import numpy as np
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    import sqlite3
    
    conn = sqlite3.connect(...)
    df = pd.read_sql('''
        SELECT r.*, 
               COALESCE(SUM(CASE WHEN t.pnl_net > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(t.id), 0), 0.5) AS daily_win_rate
        FROM regime_scores r
        LEFT JOIN trades t ON DATE(t.entry_time) = r.date AND t.status='closed'
        GROUP BY r.date
        HAVING COUNT(t.id) > 0
    ''', conn)
    
    X = df[['d1_direction', 'd2_volatility', 'd3_macro_penalty',
            'd4_sector', 'd5_prev_day', 'd6_premarket']].values
    y = (df['daily_win_rate'] > 0.5).astype(int).values
    
    model = LogisticRegression(class_weight='balanced')
    model.fit(X, y)
    
    print('新权重（相对值）：', dict(zip(
        ['d1', 'd2', 'd3', 'd4', 'd5', 'd6'],
        model.coef_[0],
    )))
```

**不要在 Phase 0 就调权重**。先让初版跑够时间收集数据。盲调权重 = 过拟合。

---

## 11. 常见问题

**Q：GitHub Actions cron 精度有限（可能延迟几分钟），会影响盘前数据吗？**  
A：会延迟 5-15 分钟，但 09:00 ET 触发到 09:15 ET 推送是可以接受的（开盘前 15 分钟已足够你看完晨报）。如果要求精确，可以改本地 cron。

**Q：非交易日会浪费 Actions 额度吗？**  
A：`cron '* 1-5'` 只跑周一到周五。长假（Thanksgiving 等）会空跑一次但 fetcher 会返回空数据，CLI 检测到跳过。影响忽略。

**Q：Phase 0 第一天没有 Alpaca key 时能用吗？**  
A：能。Alpaca 失败自动 fallback 到 yfinance 盘前（延迟 15 分钟）。晨报仍然能推出来，只是盘前 change_pct 数字精度差点。

**Q：为什么 D3 最大惩罚是 -50 不是更多？**  
A：经验值。-50 已经能把"高分日"拉回"谨慎日"。再大就会让"平日 + FOMC"变成"不交易日"（score = 60 - 30 = 30），用户可能因此永远错过 FOMC 日的大行情。如果实证需要调，Phase 1 再改。

**Q：评分模型会公开给原项目吗？**  
A：Skill YAML 是通用的，可以 PR 回 upstream。但默认权重是针对用户（科技股权重高）定制的，upstream 接受的可能性低。

---

## 12. Batch 3 下一份

接下来 `07_BREAKOUT_FILTER.md` 讲四层过滤的**代码实现**（不是心法，心法在 `BREAKOUT_FILTER_PLAYBOOK.md`）+ 怎么给历史 trades 打标签 + 前端 Breakout Status 面板。

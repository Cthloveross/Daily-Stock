# 04 · Option Support Extension

> **模块**：期权通用支持（OCC 解析、Greek 计算、期权链抓取、期权 schema）  
> **位置**：`src/options/` + `data_provider/options_chain.py` + 散落字段  
> **前置**：`02_ARCHITECTURE_OVERVIEW.md`  
> **作为**：所有期权相关功能的地基（Journal、LEAP Explorer、Breakout Filter 都依赖它）

---

## 0. 问题陈述

原 repo 基本不做期权。没有 OCC 代码解析、没有 Greek、没有期权链抓取。这些是**基础设施**，必须先搭好，不然后面所有期权相关的 skill 都无从谈起。

**这一层的设计原则**：
- **通用**：OCC / Greek / 链这些是行业标准，不绑定你一个用户
- **轻量**：不接昂贵的 Polygon Options，全部免费
- **是地基不是应用**：这里只提供"能力"，不做"决策"
- **精度够用就行**：Black-Scholes 估算 Greek 误差 ±10-15%，够用；不做高精度蒙特卡洛

---

## 1. OCC 期权代码解析

### 1.1 Moomoo 的 OCC 格式（和行业标准的差异）

**标准 OCC 格式**（21 字符定长）：
```
AAPL260417C00150000
│    │      │ │
│    │      │ └── strike × 1000，padding 到 8 位 = "00150000"
│    │      └─── C/P
│    └───── expiry YYMMDD = "260417"
└────────── underlying（补空格到 6 位，如 "AAPL  "）
```

**Moomoo 实际格式**（strike 可变长，**没有 padding**）：
```
AAPL260417C150000      ← 只有 6 位 strike
SNDK260417C970000      ← 6 位，高价票
TSLA260417P382500      ← $382.50 → 382500（6 位）
SLV260130C5000         ← $5.00 → 5000（4 位！）
```

**差异**：Moomoo 去掉了 strike 的前导零。

### 1.2 实现

**文件**：`src/options/occ_parser.py`（之前计划在 `src/journal/instruments.py` 里，决定放 `src/options/` 更合理，因为期权通用）

```python
"""OCC 期权代码解析。"""
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

# 正则：字母开头 underlying + 6 位 YYMMDD + C/P + 任意位数 strike
# 注意：strike 长度可变（3-8 位），/1000 得到实际价格
OCC_PATTERN = re.compile(r'^([A-Z]+)(\d{6})([CP])(\d+)$')


@dataclass
class OptionContract:
    raw_symbol: str
    underlying: str
    expiry: date
    right: str  # 'C' or 'P'
    strike: float

    @property
    def display(self) -> str:
        """人类可读格式：'AAPL 2026-04-17 $150 C'"""
        return f"{self.underlying} {self.expiry} ${self.strike:g} {self.right}"

    @property
    def dte_at(self, ref_date: date = None) -> int:
        """距到期日天数。默认今天。"""
        if ref_date is None:
            ref_date = date.today()
        return (self.expiry - ref_date).days


@dataclass
class InstrumentInfo:
    """统一表示"标的"——正股或期权。"""
    raw_symbol: str
    is_option: bool
    underlying: str
    option: Optional[OptionContract] = None


def parse_symbol(sym: str) -> InstrumentInfo:
    """
    解析 Moomoo / 行业标准 symbol。
    正股 → InstrumentInfo(is_option=False, underlying=sym)
    期权 → InstrumentInfo(is_option=True, underlying=AAPL, option=OptionContract(...))
    """
    if not sym or not isinstance(sym, str):
        return InstrumentInfo(raw_symbol=str(sym) if sym else "", is_option=False, underlying=str(sym or ""))
    
    sym = sym.strip()
    m = OCC_PATTERN.match(sym)
    
    if not m:
        # 不是期权，当正股处理
        return InstrumentInfo(raw_symbol=sym, is_option=False, underlying=sym)
    
    u, yymmdd, right, strike_raw = m.groups()
    
    try:
        yy = int(yymmdd[:2])
        # 两位年份规则：00-49 视为 2000-2049，50-99 视为 1950-1999
        # 实际上期权不会追溯到 1999，但为了严谨
        year = 2000 + yy if yy < 50 else 1900 + yy
        mm = int(yymmdd[2:4])
        dd = int(yymmdd[4:6])
        expiry = date(year, mm, dd)
    except ValueError:
        # 日期无效，回退为正股
        return InstrumentInfo(raw_symbol=sym, is_option=False, underlying=sym)
    
    strike = int(strike_raw) / 1000.0
    
    return InstrumentInfo(
        raw_symbol=sym,
        is_option=True,
        underlying=u,
        option=OptionContract(
            raw_symbol=sym,
            underlying=u,
            expiry=expiry,
            right=right,
            strike=strike,
        ),
    )


def format_occ(underlying: str, expiry: date, right: str, strike: float,
               padded: bool = False) -> str:
    """反向生成 OCC 代码。
    
    padded=False：Moomoo 风格（strike 可变长）
    padded=True：标准 OCC（strike 8 位零填充）
    """
    yymmdd = expiry.strftime('%y%m%d')
    strike_int = int(round(strike * 1000))
    if padded:
        strike_str = f'{strike_int:08d}'
    else:
        strike_str = str(strike_int)
    return f'{underlying}{yymmdd}{right}{strike_str}'
```

### 1.3 测试用例

**文件**：`src/options/tests/test_occ_parser.py`

必须覆盖的 case（基于你真实 CSV 里的 symbol）：

```python
import pytest
from datetime import date
from src.options.occ_parser import parse_symbol, format_occ, InstrumentInfo

# ========== 期权正常情况 ==========
@pytest.mark.parametrize("sym,exp_u,exp_date,exp_r,exp_k", [
    # 来自你真实 CSV 的样本
    ('AMD260417C275000',    'AMD',  date(2026,4,17),  'C', 275.0),
    ('TSLA260417P382500',   'TSLA', date(2026,4,17),  'P', 382.5),
    ('NVDA260417C200000',   'NVDA', date(2026,4,17),  'C', 200.0),
    ('SNDK260417C920000',   'SNDK', date(2026,4,17),  'C', 920.0),
    ('SNDK260417C970000',   'SNDK', date(2026,4,17),  'C', 970.0),
    ('MU260410C425000',     'MU',   date(2026,4,10),  'C', 425.0),
    ('HOOD260123P108000',   'HOOD', date(2026,1,23),  'P', 108.0),
    # LEAP 样本（2027 到期）
    ('NVDA270115C150000',   'NVDA', date(2027,1,15),  'C', 150.0),
    # 低价 strike
    ('SLV260130C5000',      'SLV',  date(2026,1,30),  'C', 5.0),
])
def test_parse_option_success(sym, exp_u, exp_date, exp_r, exp_k):
    r = parse_symbol(sym)
    assert r.is_option is True
    assert r.underlying == exp_u
    assert r.option.expiry == exp_date
    assert r.option.right == exp_r
    assert r.option.strike == exp_k

# ========== 正股 ==========
@pytest.mark.parametrize("sym", ['AAPL', 'MSFT', 'QQQ', 'TSLA', 'BRK.B'])
def test_parse_stock(sym):
    r = parse_symbol(sym)
    assert r.is_option is False
    assert r.underlying == sym

# ========== 边界 ==========
def test_empty():
    r = parse_symbol('')
    assert r.is_option is False

def test_none_like():
    r = parse_symbol(None)
    assert r.is_option is False

def test_invalid_date():
    # 月份 99 应该 fallback 为正股
    r = parse_symbol('AAPL269917C150000')
    assert r.is_option is False

def test_no_strike():
    r = parse_symbol('AAPL260417C')
    assert r.is_option is False

# ========== 反向 ==========
def test_format_roundtrip_moomoo():
    original = 'TSLA260417P382500'
    r = parse_symbol(original)
    assert format_occ(r.underlying, r.option.expiry, r.option.right, r.option.strike,
                      padded=False) == original

def test_format_padded_standard():
    r = parse_symbol('AAPL260417C150000')
    padded = format_occ(r.underlying, r.option.expiry, r.option.right, r.option.strike,
                        padded=True)
    # 标准 OCC 是 8 位 strike
    assert padded == 'AAPL260417C00150000'
```

---

## 2. Black-Scholes 定价与 Greek

### 2.1 为什么要自己算

**免费数据源（yfinance）期权链**返回的字段：
- ✅ `bid` / `ask` / `lastPrice` / `volume` / `openInterest`
- ✅ `impliedVolatility`（IV）
- ❌ **不返回** Delta / Gamma / Theta / Vega / Rho

**我们需要这些 Greek 来做**：
- LEAP Explorer 按 Delta 0.70-0.85 筛选候选
- Journal 分析里估算每笔交易的 theta 损耗
- Backtest Replayer 反推入场时的期权价格

**所以必须自己算**。Black-Scholes 够用，误差控制在 10-15% 内（对决策影响可忽略）。

### 2.2 实现

**文件**：`src/options/black_scholes.py`

```python
"""Black-Scholes 期权定价与 Greek 估算。

参考：Hull, "Options, Futures, and Other Derivatives", 11th ed.

约定：
- S = 标的现价
- K = 行权价
- T = 距到期时间（年）；今天到期 T=0 会退化为内在价值
- r = 无风险利率（默认 0.045，可从 FRED 拉更精准值）
- iv = 隐含波动率（小数，不是百分比）
- q = 股息收益率（简化为 0，美股科技股大多无高分红）
"""
import math
from dataclasses import dataclass
from datetime import date
from scipy.stats import norm


DEFAULT_RISK_FREE_RATE = 0.045


def _d1(S: float, K: float, T: float, iv: float, r: float = DEFAULT_RISK_FREE_RATE, q: float = 0) -> float:
    if T <= 0 or iv <= 0:
        raise ValueError("T and iv must be positive")
    return (math.log(S / K) + (r - q + iv ** 2 / 2) * T) / (iv * math.sqrt(T))


def _d2(d1: float, iv: float, T: float) -> float:
    return d1 - iv * math.sqrt(T)


def call_price(S, K, T, iv, r=DEFAULT_RISK_FREE_RATE, q=0) -> float:
    """BS Call 理论价格。T=0 返回内在价值。"""
    if T <= 0:
        return max(S - K, 0)
    if iv <= 0:
        # 无波动率假设，返回折现后内在
        return max(S * math.exp(-q * T) - K * math.exp(-r * T), 0)
    d1 = _d1(S, K, T, iv, r, q)
    d2 = _d2(d1, iv, T)
    return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def put_price(S, K, T, iv, r=DEFAULT_RISK_FREE_RATE, q=0) -> float:
    if T <= 0:
        return max(K - S, 0)
    if iv <= 0:
        return max(K * math.exp(-r * T) - S * math.exp(-q * T), 0)
    d1 = _d1(S, K, T, iv, r, q)
    d2 = _d2(d1, iv, T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float      # per year
    theta_per_day: float
    vega: float       # per 1% IV change
    rho: float        # per 1% rate change


def compute_greeks(
    S: float, K: float, T: float, iv: float, right: str,
    r: float = DEFAULT_RISK_FREE_RATE, q: float = 0,
) -> Greeks:
    """
    right: 'C' or 'P'
    返回按照市场惯例的 Greek：
      - theta_per_day = theta / 365
      - vega 对 1% IV 变化
      - rho 对 1% 利率变化
    """
    if T <= 1e-6 or iv <= 1e-6:
        # 退化：到期日或无波动
        if right == 'C':
            intrinsic = max(S - K, 0)
            delta = 1.0 if S > K else 0.0
        else:
            intrinsic = max(K - S, 0)
            delta = -1.0 if S < K else 0.0
        return Greeks(delta=delta, gamma=0, theta=0, theta_per_day=0, vega=0, rho=0)

    d1 = _d1(S, K, T, iv, r, q)
    d2 = _d2(d1, iv, T)
    pdf_d1 = norm.pdf(d1)

    if right == 'C':
        delta = math.exp(-q * T) * norm.cdf(d1)
        theta = (
            -S * pdf_d1 * iv * math.exp(-q * T) / (2 * math.sqrt(T))
            - r * K * math.exp(-r * T) * norm.cdf(d2)
            + q * S * math.exp(-q * T) * norm.cdf(d1)
        )
        rho_ = K * T * math.exp(-r * T) * norm.cdf(d2) / 100
    else:  # Put
        delta = math.exp(-q * T) * (norm.cdf(d1) - 1)
        theta = (
            -S * pdf_d1 * iv * math.exp(-q * T) / (2 * math.sqrt(T))
            + r * K * math.exp(-r * T) * norm.cdf(-d2)
            - q * S * math.exp(-q * T) * norm.cdf(-d1)
        )
        rho_ = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100

    gamma = math.exp(-q * T) * pdf_d1 / (S * iv * math.sqrt(T))
    vega = S * math.exp(-q * T) * pdf_d1 * math.sqrt(T) / 100  # per 1% IV

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta,
        theta_per_day=theta / 365.0,
        vega=vega,
        rho=rho_,
    )


# ========== 辅助：从日期计算 T ==========
def time_to_expiry_years(expiry: date, from_date: date = None) -> float:
    if from_date is None:
        from_date = date.today()
    days = (expiry - from_date).days
    if days <= 0:
        return 0.0
    # 年份 = 365（不用 252 交易日，因为 Greek 是日历天衰减）
    return days / 365.0


# ========== 便利：一次算完 ==========
@dataclass
class PricingResult:
    price: float
    greeks: Greeks


def price_and_greeks(
    S: float, expiry: date, K: float, iv: float, right: str,
    r: float = DEFAULT_RISK_FREE_RATE, q: float = 0, from_date: date = None,
) -> PricingResult:
    T = time_to_expiry_years(expiry, from_date)
    if right == 'C':
        p = call_price(S, K, T, iv, r, q)
    else:
        p = put_price(S, K, T, iv, r, q)
    g = compute_greeks(S, K, T, iv, right, r, q)
    return PricingResult(price=p, greeks=g)
```

### 2.3 IV 的来源

**三个来源按优先级**：

1. **yfinance 期权链自带 `impliedVolatility`** — 首选，免费，够用
2. **从市场价反算**（二分法）— 如果 yfinance 没给，用 bid/ask 中价反求
3. **历史波动率 HV** — 最差情况兜底，计算 20 / 60 日收盘价对数收益率的年化标准差

**反求 IV 的实现**（简化版）：

```python
def implied_volatility_from_price(
    market_price: float, S: float, K: float, T: float, right: str,
    r: float = DEFAULT_RISK_FREE_RATE,
    tol: float = 1e-4, max_iter: int = 100,
) -> float:
    """用 bisection 反求 IV。"""
    if T <= 0 or market_price <= 0:
        return 0.0
    
    lo, hi = 0.001, 5.0  # IV 上限 500%
    pricer = call_price if right == 'C' else put_price
    
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        price_mid = pricer(S, K, T, mid, r)
        if abs(price_mid - market_price) < tol:
            return mid
        if price_mid < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
```

---

## 3. 期权链抓取

### 3.1 数据源选择

| 数据源 | 免费？ | 延迟 | 字段完整度 |
|--|--|--|--|
| **yfinance** | ✅ | 15 min | 有 IV，无 Greek |
| Alpaca Options | ❌ 付费 | — | — |
| Polygon Options Starter | $29/月 | 实时 | 有 Greek |
| Tradier | 要开户 | 实时 | 有 Greek |

**结论**：用 yfinance，自己补 Greek。预算范围内最优。

### 3.2 实现

**文件**：`data_provider/options_chain.py`

```python
"""期权链抓取。基于 yfinance 免费数据 + 自己算 Greek。"""
import logging
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Optional
import yfinance as yf
import pandas as pd

from src.options.black_scholes import compute_greeks, time_to_expiry_years

logger = logging.getLogger(__name__)


@dataclass
class OptionQuote:
    symbol: str           # OCC raw_symbol
    underlying: str
    expiry: date
    strike: float
    right: str            # 'C' or 'P'
    bid: float
    ask: float
    last_price: float
    volume: int
    open_interest: int
    iv: float             # implied volatility, 0.32 = 32%
    # 我们算的 Greeks
    delta: float
    gamma: float
    theta_per_day: float
    vega: float
    # 元数据
    dte: int
    moneyness: str        # 'ITM' / 'ATM' / 'OTM'
    spot: float           # 抓取时刻的标的价


class OptionsChainFetcher:
    """对一个 underlying 抓取期权链。"""
    
    def __init__(self, cache_ttl_seconds: int = 900):
        self._cache = {}   # (symbol, expiry) -> (timestamp, OptionQuote list)
        self._ttl = cache_ttl_seconds
    
    def get_expirations(self, symbol: str) -> list[str]:
        """返回所有可用到期日，格式 'YYYY-MM-DD'。"""
        t = yf.Ticker(symbol)
        try:
            return list(t.options)  # tuple of strings
        except Exception:
            logger.exception(f"获取 {symbol} expirations 失败")
            return []
    
    def get_chain(
        self,
        symbol: str,
        expiry: str,
        right: Optional[str] = None,  # 'C' / 'P' / None=both
    ) -> list[OptionQuote]:
        """抓取指定到期日的期权链。"""
        import time
        cache_key = (symbol, expiry, right)
        now = time.time()
        
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < self._ttl:
                return data
        
        t = yf.Ticker(symbol)
        try:
            chain = t.option_chain(expiry)
        except Exception:
            logger.exception(f"获取 {symbol} chain 失败")
            return []
        
        # 获取 spot
        try:
            spot = t.info.get('currentPrice') or t.info.get('regularMarketPrice')
            if spot is None:
                # fallback
                hist = t.history(period='1d')
                spot = hist.iloc[-1]['Close'] if len(hist) else 0
        except Exception:
            spot = 0
        
        expiry_date = date.fromisoformat(expiry)
        dte = (expiry_date - date.today()).days
        T = time_to_expiry_years(expiry_date)
        
        quotes = []
        dataframes = []
        if right in (None, 'C'):
            dataframes.append((chain.calls, 'C'))
        if right in (None, 'P'):
            dataframes.append((chain.puts, 'P'))
        
        for df, r in dataframes:
            for _, row in df.iterrows():
                strike = float(row['strike'])
                iv = float(row.get('impliedVolatility', 0)) or 0.3
                
                # 算 Greek
                try:
                    g = compute_greeks(
                        S=spot, K=strike, T=T, iv=iv, right=r,
                    )
                except Exception:
                    g = None
                
                # moneyness
                if r == 'C':
                    if spot > strike * 1.02:
                        moneyness = 'ITM'
                    elif spot < strike * 0.98:
                        moneyness = 'OTM'
                    else:
                        moneyness = 'ATM'
                else:
                    if spot < strike * 0.98:
                        moneyness = 'ITM'
                    elif spot > strike * 1.02:
                        moneyness = 'OTM'
                    else:
                        moneyness = 'ATM'
                
                quotes.append(OptionQuote(
                    symbol=row.get('contractSymbol', ''),
                    underlying=symbol,
                    expiry=expiry_date,
                    strike=strike,
                    right=r,
                    bid=float(row.get('bid', 0) or 0),
                    ask=float(row.get('ask', 0) or 0),
                    last_price=float(row.get('lastPrice', 0) or 0),
                    volume=int(row.get('volume', 0) or 0),
                    open_interest=int(row.get('openInterest', 0) or 0),
                    iv=iv,
                    delta=g.delta if g else 0,
                    gamma=g.gamma if g else 0,
                    theta_per_day=g.theta_per_day if g else 0,
                    vega=g.vega if g else 0,
                    dte=dte,
                    moneyness=moneyness,
                    spot=spot,
                ))
        
        self._cache[cache_key] = (now, quotes)
        return quotes
    
    def find_leap_candidates(
        self,
        symbol: str,
        delta_min: float = 0.70,
        delta_max: float = 0.85,
        min_dte: int = 270,
    ) -> list[OptionQuote]:
        """筛选 LEAP 候选：delta 在 [min, max] 范围内的 Call。
        
        默认取最远 2 个到期日。
        """
        exps = self.get_expirations(symbol)
        if not exps:
            return []
        
        today = date.today()
        leap_exps = [
            e for e in exps
            if (date.fromisoformat(e) - today).days >= min_dte
        ][:2]  # 最远两个
        
        candidates = []
        for exp in leap_exps:
            chain = self.get_chain(symbol, exp, right='C')
            for q in chain:
                if delta_min <= q.delta <= delta_max:
                    candidates.append(q)
        
        return sorted(candidates, key=lambda x: (x.dte, x.strike))


def get_leap_candidates(symbol: str, **kwargs) -> list[dict]:
    """高层便利函数。返回 dict 列表便于 API 直接用。"""
    fetcher = OptionsChainFetcher()
    results = fetcher.find_leap_candidates(symbol, **kwargs)
    return [asdict(q) for q in results]
```

### 3.3 期权链缓存（DB）

避免反复调 yfinance：

```sql
CREATE TABLE option_chains_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying      TEXT NOT NULL,
    expiry          DATE NOT NULL,
    right           TEXT NOT NULL,
    fetched_at      TIMESTAMP NOT NULL,
    spot_at_fetch   REAL,
    chain_json      TEXT NOT NULL,    -- 序列化的 OptionQuote 列表
    UNIQUE(underlying, expiry, right, fetched_at)
);

CREATE INDEX idx_chains_lookup ON option_chains_cache(underlying, expiry, right, fetched_at);
```

**缓存策略**：
- 同一 `(underlying, expiry, right)` 15 分钟内只调一次 yfinance
- DB 保留历史快照（供 Backtest Replayer 反推用）
- 每天自动清理 30 天前的缓存

---

## 4. IV Rank 计算

### 4.1 为什么要算

**IV Rank** = 当前 IV 在过去 N 天（通常 252 天）IV 区间的百分位。

- IV Rank 0-20%：IV 极低，买方有利
- IV Rank 20-50%：正常
- IV Rank 50-80%：偏高，警惕
- IV Rank 80-100%：极高（常见于财报前），买方容易被"IV crush"

**对用户的作用**：
- 期权买方：IV Rank 高时避免追入，防止财报后 IV crush 导致即便方向对也亏钱
- LEAP 选时：IV Rank 低时买 LEAP 更划算

### 4.2 实现

**文件**：`src/options/iv_rank.py`

```python
"""IV Rank 计算。"""
from datetime import date, timedelta
from dataclasses import dataclass
import yfinance as yf
import numpy as np


@dataclass
class IVRankResult:
    underlying: str
    current_iv: float           # ATM IV
    iv_rank_pct: float          # 0-100
    iv_52w_high: float
    iv_52w_low: float
    ref_expiry: str
    days_window: int            # 计算窗口


def compute_atm_iv(symbol: str, ref_date: date = None) -> tuple[float, str]:
    """
    取最接近 30 天到期的 ATM Call 的 IV 作为"当前 IV"代表值。
    返回 (iv, expiry_str)。
    """
    import yfinance as yf
    from src.options.black_scholes import time_to_expiry_years
    
    t = yf.Ticker(symbol)
    spot = t.info.get('currentPrice', 0)
    if not spot:
        return 0.0, ''
    
    exps = list(t.options)
    if not exps:
        return 0.0, ''
    
    # 找最接近 30 天的到期
    target = 30
    ref = ref_date or date.today()
    best_exp = min(exps, key=lambda e: abs((date.fromisoformat(e) - ref).days - target))
    
    chain = t.option_chain(best_exp)
    calls = chain.calls
    if len(calls) == 0:
        return 0.0, best_exp
    
    # ATM = 最接近 spot 的 strike
    calls['dist'] = (calls['strike'] - spot).abs()
    atm_row = calls.loc[calls['dist'].idxmin()]
    iv = float(atm_row.get('impliedVolatility', 0))
    
    return iv, best_exp


def compute_iv_rank(symbol: str, days_window: int = 252) -> IVRankResult:
    """
    IV Rank 需要历史 ATM IV。
    免费数据源没有历史 IV，只能近似：
    - 方法 A（简化）：用历史 HV 作为代理，HV 在窗口内的百分位
    - 方法 B（精确）：自己积累每日 ATM IV 快照
    
    这里先用方法 A（简化），DB 积累够历史数据后切方法 B。
    """
    t = yf.Ticker(symbol)
    hist = t.history(period=f'{days_window + 30}d', interval='1d')
    if len(hist) < 30:
        return IVRankResult(symbol, 0, 0, 0, 0, '', days_window)
    
    # 20 日滚动 HV 作为 IV 代理
    hist['log_ret'] = np.log(hist['Close'] / hist['Close'].shift(1))
    hist['hv_20d'] = hist['log_ret'].rolling(20).std() * np.sqrt(252)
    hv_series = hist['hv_20d'].dropna().tail(days_window)
    
    if len(hv_series) == 0:
        return IVRankResult(symbol, 0, 0, 0, 0, '', days_window)
    
    current_iv, ref_exp = compute_atm_iv(symbol)
    hv_high = hv_series.max()
    hv_low = hv_series.min()
    
    if hv_high == hv_low:
        rank = 50.0
    else:
        # 用 current IV 在 HV 分布里的位置当 IV Rank 近似
        # 注意：这是近似值，真实 IV Rank 需要 IV 时序
        rank = (current_iv - hv_low) / (hv_high - hv_low) * 100
        rank = max(0, min(100, rank))
    
    return IVRankResult(
        underlying=symbol,
        current_iv=current_iv,
        iv_rank_pct=rank,
        iv_52w_high=float(hv_high),
        iv_52w_low=float(hv_low),
        ref_expiry=ref_exp,
        days_window=days_window,
    )
```

### 4.3 长期升级

**每天存一份 ATM IV 快照**到 DB，积累 1 年后切换为"真实 IV Rank"：

```sql
CREATE TABLE iv_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying      TEXT NOT NULL,
    snapshot_date   DATE NOT NULL,
    atm_iv          REAL NOT NULL,
    ref_expiry      DATE,
    ref_dte         INTEGER,
    spot            REAL,
    UNIQUE(underlying, snapshot_date)
);
```

每天 16:30 ET cron 跑 `compute_atm_iv(sym)` for 所有 watchlist，写 DB。3 个月后就有 60+ 天真实 IV 历史，一年后有完整 IV Rank 基础。

---

## 5. Agent 工具接入

**文件**：`src/agent/tools/get_option_chain.py`

Agent 通过这个工具调用期权链：

```python
"""Agent 工具：获取期权链。被 option_trader / leap_explorer skills 使用。"""
from data_provider.options_chain import OptionsChainFetcher

TOOL_METADATA = {
    'name': 'get_option_chain',
    'description': '获取指定标的的期权链数据，含 IV、Delta、Gamma、Theta 等 Greek。',
    'parameters': {
        'type': 'object',
        'properties': {
            'symbol': {
                'type': 'string',
                'description': '正股代码，如 NVDA / TSLA',
            },
            'expiry': {
                'type': 'string',
                'description': '到期日 YYYY-MM-DD。传 "nearest" = 最近到期，"leap" = 最远 2 个到期。',
            },
            'right': {
                'type': 'string',
                'enum': ['C', 'P', 'both'],
                'default': 'both',
            },
            'strike_range_pct': {
                'type': 'number',
                'description': '相对 spot 的 strike 范围（±percent），默认 ±10%',
                'default': 10,
            },
        },
        'required': ['symbol', 'expiry'],
    },
}


def execute(symbol: str, expiry: str, right: str = 'both', strike_range_pct: float = 10) -> dict:
    fetcher = OptionsChainFetcher()
    
    # 解析 expiry 特殊值
    if expiry == 'nearest':
        exps = fetcher.get_expirations(symbol)
        expiry = exps[0] if exps else None
    elif expiry == 'leap':
        # 单独处理 LEAP
        candidates = fetcher.find_leap_candidates(symbol)
        return {
            'type': 'leap_candidates',
            'symbol': symbol,
            'candidates': [asdict(c) for c in candidates],
        }
    
    if not expiry:
        return {'error': '无有效到期日'}
    
    right_arg = None if right == 'both' else right
    chain = fetcher.get_chain(symbol, expiry, right=right_arg)
    
    # 过滤 strike 范围
    if chain:
        spot = chain[0].spot
        lo = spot * (1 - strike_range_pct / 100)
        hi = spot * (1 + strike_range_pct / 100)
        chain = [q for q in chain if lo <= q.strike <= hi]
    
    return {
        'type': 'option_chain',
        'symbol': symbol,
        'expiry': expiry,
        'spot': chain[0].spot if chain else 0,
        'quotes': [asdict(q) for q in chain],
    }
```

---

## 6. API 端点

**文件**：`api/routers/options.py`

```python
from fastapi import APIRouter, HTTPException
from datetime import date

from data_provider.options_chain import OptionsChainFetcher

router = APIRouter(prefix='/api/v1/options', tags=['options'])
_fetcher = OptionsChainFetcher()


@router.get('/{symbol}/expirations')
async def list_expirations(symbol: str):
    return {'symbol': symbol, 'expirations': _fetcher.get_expirations(symbol)}


@router.get('/{symbol}/chain')
async def get_chain(
    symbol: str,
    expiry: str,
    right: str = 'both',
):
    right_arg = None if right == 'both' else right
    chain = _fetcher.get_chain(symbol, expiry, right=right_arg)
    return {
        'symbol': symbol,
        'expiry': expiry,
        'quotes': [asdict(q) for q in chain],
    }


@router.get('/{symbol}/leap-candidates')
async def leap_candidates(
    symbol: str,
    delta_min: float = 0.70,
    delta_max: float = 0.85,
    min_dte: int = 270,
):
    """LEAP Explorer 主要入口。"""
    candidates = _fetcher.find_leap_candidates(
        symbol, delta_min=delta_min, delta_max=delta_max, min_dte=min_dte,
    )
    return {
        'symbol': symbol,
        'delta_range': [delta_min, delta_max],
        'min_dte': min_dte,
        'candidates': [asdict(c) for c in candidates],
    }


@router.get('/{symbol}/iv-rank')
async def iv_rank(symbol: str):
    from src.options.iv_rank import compute_iv_rank
    return asdict(compute_iv_rank(symbol))
```

挂到 `api/app.py`：

```python
from api.routers import options
app.include_router(options.router)
```

---

## 7. 依赖与工程细节

### 7.1 新增 requirements

```
scipy>=1.11  # 已在 repo 里，无需加
# yfinance 已有
```

Good news：`scipy` 原项目已经依赖（用于基本面计算），我们直接用。

### 7.2 性能

- BS 公式每次计算 ~1 微秒
- 算一个到期日的完整链（~40 strikes × 2 rights = 80 合约）约 0.1ms
- yfinance 拉链每次 ~2 秒（网络）
- 所以**瓶颈是网络而非计算**，缓存策略是关键

### 7.3 错误处理

```python
# 统一模式：优雅降级
try:
    greeks = compute_greeks(...)
except Exception as e:
    logger.warning(f"Greek 计算失败 {symbol}: {e}")
    greeks = Greeks(0, 0, 0, 0, 0, 0)
```

**绝不因 Greek 计算失败导致整个期权链查询失败**。宁可返回 `delta=0`（客户端看到 0 会知道是异常值），也不返回错误。

### 7.4 Ref Data 刷新

无风险利率 `r` 会变（2024 年从 5.25% 降到现在的 4.50%）。简化处理：
- 硬编码 `DEFAULT_RISK_FREE_RATE = 0.045`
- 每季度人工 review 一次，必要时调整常数
- 将来可接 FRED API（`DGS10` 10Y Treasury）自动刷新

---

## 8. 测试策略

```
src/options/tests/
  test_occ_parser.py         ← 上面第 1.3 节
  test_black_scholes.py      ← 价格对照 Hull 教材已知值
  test_iv_rank.py            ← mock yfinance，验证数学正确
  test_greeks_sanity.py      ← 性质测试（delta 范围、theta 符号等）
```

**关键 sanity test**：

```python
def test_call_delta_monotonic():
    """Call Delta 随 S 单增。"""
    from src.options.black_scholes import compute_greeks
    deltas = []
    for S in range(100, 200, 10):
        g = compute_greeks(S=S, K=150, T=0.5, iv=0.3, right='C')
        deltas.append(g.delta)
    assert all(deltas[i] < deltas[i+1] for i in range(len(deltas)-1))

def test_put_theta_negative():
    """Put theta 对买方应该为负（时间不利）。"""
    from src.options.black_scholes import compute_greeks
    g = compute_greeks(S=100, K=100, T=0.25, iv=0.3, right='P')
    assert g.theta_per_day < 0

def test_atm_call_delta_near_half():
    """ATM 短期 Call Delta 应接近 0.5。"""
    g = compute_greeks(S=100, K=100, T=0.1, iv=0.3, right='C')
    assert 0.45 < g.delta < 0.55
```

---

## 9. 接下来

- Journal 模块会用 OCC 解析器把 CSV 里的 symbol 转成结构化 `InstrumentInfo`
- LEAP Explorer 会用 `get_leap_candidates` 工具
- Breakout Filter 会用 `compute_greeks` 判断 theta 对交易的影响
- Journey 月度报告会展示用户操作的期权的 Greek 分布

下一份文档：`05_JOURNAL_MODULE.md`，这一切的交易数据基础。

# 07 · Breakout Filter

> **模块**：假突破过滤系统（代码实现版）  
> **位置**：`src/breakout/` + 扩展 `trades` 表字段 + 前端 Breakout Status 面板  
> **前置**：`BREAKOUT_FILTER_PLAYBOOK.md`（心法版）、`05_JOURNAL_MODULE.md`、`06_REGIME_CLASSIFIER.md`  
> **使命**：**把 playbook 的"问三个问题"变成系统强制流程**

---

## 0. 这份文档的定位

`BREAKOUT_FILTER_PLAYBOOK.md` 是**心法手册**——每次追突破时你打开它当副驾驶。那份文档保留不动。

**本文档是工程实现版**：
- 怎么用代码检测突破
- 怎么给 trades 打标签（过去 3 个月的历史数据要回填）
- 前端 Breakout Status 面板怎么做
- Agent 怎么在聊天里主动调 Breakout Filter
- 月度 Review 里怎么展示 chase vs retest 的差距

**一句话**：Playbook 让你"决策时慢一拍"，本系统让你"数据上可追溯"。

---

## 1. 架构

```
检测到价格突破
    │
    ▼
┌─────────────────────────────────────┐
│  src/breakout/filter.py             │
│                                     │
│  breakout_check(symbol, event) →    │
│    BreakoutFilterResult             │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ Q1: Regime pass?            │   │
│  │    src/regime/storage.py    │   │
│  └────────────┬────────────────┘   │
│               │ pass                 │
│  ┌────────────▼────────────────┐   │
│  │ Q2: Time window pass?       │   │
│  │    src/breakout/time_rules  │   │
│  └────────────┬────────────────┘   │
│               │ pass                 │
│  ┌────────────▼────────────────┐   │
│  │ Q3: Volume confirm?         │   │
│  │    src/breakout/volume_check│   │
│  └────────────┬────────────────┘   │
│               │ pass                 │
│  ┌────────────▼────────────────┐   │
│  │ Q4: Timeframe alignment?    │   │
│  │    src/breakout/timeframe_  │   │
│  └────────────┬────────────────┘   │
│               │ pass                 │
│  ┌────────────▼────────────────┐   │
│  │ Q5: RS vs SPY?              │   │
│  │    src/breakout/rs_check    │   │
│  └────────────┬────────────────┘   │
│               │ pass                 │
│  ┌────────────▼────────────────┐   │
│  │ Q6: Entry style decision    │   │
│  │    chase / split / retest   │   │
│  └─────────────────────────────┘   │
│                                     │
│  → 返回结构化建议 + 所有中间数据    │
└─────────────────────────────────────┘
         │
         ▼
 推入 Telegram Breakout Alert
         │
         ▼
 前端 Stock 页显示 Breakout Status 卡片
         │
         ▼
 用户若入场，trade 记录入库带 trade_style 字段
         │
         ▼
 Retest Tracker 追踪 → was_fake_breakout 字段回填
```

---

## 2. 检测器：怎么知道"突破"发生了

### 2.1 突破的定义（量化版）

**突破位 `P_break`**：近 N 根 K 线中价格刚刚穿透的关键阻力位。

分三类关键位：
1. **昨日高低点** — 最常用，容易判断
2. **近 N 根 K 线高低点**（N=10/20/60）
3. **自定义阻力位**（用户手动标注，Phase 2+）

### 2.2 实现

**文件**：`src/breakout/detector.py`

```python
"""突破检测。用 2min / 5min K 线实时扫描。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from data_provider.alpaca_fetcher import AlpacaFetcher


@dataclass
class BreakoutSignal:
    symbol: str
    detected_at: datetime
    direction: str               # 'up' / 'down'
    breakout_price: float        # P_break
    breakout_type: str           # 'prev_day_high' / 'intraday_high_N' / 'custom'
    reference_window: int        # N 根 K 线
    current_bar: dict            # 触发突破的 bar 数据


class BreakoutDetector:
    def __init__(self, alpaca: AlpacaFetcher = None):
        self.alpaca = alpaca or AlpacaFetcher()
    
    def scan(
        self,
        symbol: str,
        timeframe: str = '2Min',
        lookback_bars: int = 60,
        now: Optional[datetime] = None,
    ) -> Optional[BreakoutSignal]:
        """
        扫描最新一根 K 线是否构成突破。
        
        规则：
          最新 bar 的 high > 过去 N 根 bar 的最高 → upward breakout
          最新 bar 的 low < 过去 N 根 bar 的最低 → downward breakout
        """
        if now is None:
            now = datetime.now()
        
        start = now - timedelta(minutes=(lookback_bars + 5) * self._timeframe_minutes(timeframe))
        bars = self.alpaca.bars(symbol, timeframe, start, now)
        
        if len(bars) < lookback_bars + 1:
            return None
        
        df = pd.DataFrame(bars)
        df['t'] = pd.to_datetime(df['t'])
        df = df.sort_values('t')
        
        current = df.iloc[-1]
        previous_bars = df.iloc[-lookback_bars-1:-1]
        
        prev_max = previous_bars['h'].max()
        prev_min = previous_bars['l'].min()
        
        if current['h'] > prev_max:
            return BreakoutSignal(
                symbol=symbol,
                detected_at=pd.Timestamp(current['t']).to_pydatetime(),
                direction='up',
                breakout_price=prev_max,
                breakout_type=f'intraday_high_{lookback_bars}',
                reference_window=lookback_bars,
                current_bar=current.to_dict(),
            )
        
        if current['l'] < prev_min:
            return BreakoutSignal(
                symbol=symbol,
                detected_at=pd.Timestamp(current['t']).to_pydatetime(),
                direction='down',
                breakout_price=prev_min,
                breakout_type=f'intraday_low_{lookback_bars}',
                reference_window=lookback_bars,
                current_bar=current.to_dict(),
            )
        
        return None
    
    def scan_with_prev_day_levels(self, symbol: str, now: datetime = None) -> Optional[BreakoutSignal]:
        """以昨日高低点作为突破位（更稳定的信号）。"""
        from datetime import date
        if now is None:
            now = datetime.now()
        
        # 昨日日线
        today = now.date()
        start = today - timedelta(days=5)
        daily_bars = self.alpaca.bars(symbol, '1Day', 
                                        datetime.combine(start, datetime.min.time()),
                                        datetime.combine(today, datetime.min.time()))
        if len(daily_bars) < 1:
            return None
        prev_day_high = daily_bars[-1]['h']
        prev_day_low = daily_bars[-1]['l']
        
        # 当日最新 2min bar
        day_start = datetime.combine(today, datetime.min.time()).replace(hour=9, minute=30)
        recent_bars = self.alpaca.bars(symbol, '2Min', day_start, now)
        if not recent_bars:
            return None
        current = recent_bars[-1]
        
        if current['h'] > prev_day_high:
            # 找第一次突破的 bar（可能是几分钟前）
            first_break = None
            for b in recent_bars:
                if b['h'] > prev_day_high:
                    first_break = b
                    break
            return BreakoutSignal(
                symbol=symbol,
                detected_at=pd.Timestamp(first_break['t']).to_pydatetime(),
                direction='up',
                breakout_price=prev_day_high,
                breakout_type='prev_day_high',
                reference_window=1,
                current_bar=first_break,
            )
        
        if current['l'] < prev_day_low:
            first_break = None
            for b in recent_bars:
                if b['l'] < prev_day_low:
                    first_break = b
                    break
            return BreakoutSignal(
                symbol=symbol,
                detected_at=pd.Timestamp(first_break['t']).to_pydatetime(),
                direction='down',
                breakout_price=prev_day_low,
                breakout_type='prev_day_low',
                reference_window=1,
                current_bar=first_break,
            )
        
        return None
    
    @staticmethod
    def _timeframe_minutes(tf: str) -> int:
        m = {'1Min': 1, '2Min': 2, '5Min': 5, '15Min': 15, '1Hour': 60, '1Day': 1440}
        return m.get(tf, 2)
```

---

## 3. 六层过滤器

### 3.1 Filter 总函数

**文件**：`src/breakout/filter.py`

```python
"""Breakout Filter 主函数。"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from src.breakout.detector import BreakoutSignal
from src.breakout.volume_check import VolumeCheckResult, check_volume_confirmation
from src.breakout.timeframe_check import TimeframeCheckResult, check_timeframe_alignment
from src.breakout.rs_check import RSCheckResult, check_rs_vs_spy
from src.regime.storage import get_regime_score


ET = ZoneInfo('America/New_York')


@dataclass
class FilterStage:
    name: str                      # 'Q1_regime' / 'Q2_time' / ...
    passed: bool
    confidence: str                # 'strong' / 'weak' / 'reject'
    detail: dict = field(default_factory=dict)
    reason: str = ''


@dataclass
class BreakoutFilterResult:
    signal: BreakoutSignal
    stages: list[FilterStage]
    final_decision: str            # 'chase' / 'split' / 'retest' / 'skip'
    position_size_pct: float       # 建议仓位（相对原计划）
    entry_plan: dict = field(default_factory=dict)
    
    @property
    def pre_filter_pass(self) -> bool:
        """Q1 + Q2 都过了叫 pre_filter_pass。"""
        return (
            self.stages[0].passed  # Q1 Regime
            and self.stages[1].passed  # Q2 Time
        )
    
    def to_dict(self):
        d = asdict(self)
        d['stages'] = [asdict(s) for s in self.stages]
        return d


def filter_breakout(
    signal: BreakoutSignal,
    use_prev_day_high: bool = None,
) -> BreakoutFilterResult:
    """
    对一个 BreakoutSignal 执行六层过滤。
    
    返回 BreakoutFilterResult，包含：
    - 每一层的结果
    - 最终决策（chase / split / retest / skip）
    - 建议仓位比例
    - 入场计划（价格、止损、Retest 等待窗口等）
    """
    stages: list[FilterStage] = []
    
    # ========== Q1: Regime ==========
    regime_stage = _check_regime(signal.detected_at.date())
    stages.append(regime_stage)
    if not regime_stage.passed:
        return _build_skip_result(signal, stages, 'Regime Score < 55')
    
    # ========== Q2: Time window ==========
    time_stage = _check_time_window(signal.detected_at)
    stages.append(time_stage)
    if not time_stage.passed:
        return _build_skip_result(signal, stages, time_stage.reason)
    
    # ========== Q3: Volume ==========
    volume_stage = _check_volume(signal)
    stages.append(volume_stage)
    if not volume_stage.passed:
        return _build_skip_result(signal, stages, 'Volume < 1.2x')
    
    # ========== Q4: Timeframe alignment ==========
    tf_stage = _check_timeframe(signal)
    stages.append(tf_stage)
    if not tf_stage.passed:
        return _build_skip_result(signal, stages, 'Multi-timeframe not aligned')
    
    # ========== Q5: RS vs SPY ==========
    rs_stage = _check_rs(signal)
    stages.append(rs_stage)
    if not rs_stage.passed:
        return _build_skip_result(signal, stages, 'RS < 0')
    
    # ========== Q6: 决策入场方式 ==========
    strengths = [s.confidence for s in stages[2:5]]  # Q3/Q4/Q5
    all_strong = all(s == 'strong' for s in strengths)
    
    if all_strong and time_stage.confidence == 'strong':
        decision = 'split'      # 分批试仓
        size_pct = 1.0 / 3      # 先 1/3
    elif sum(1 for s in strengths if s == 'strong') >= 2:
        decision = 'retest'     # 等回踩
        size_pct = 1.0
    else:
        decision = 'shadow'     # 记录不实盘
        size_pct = 0.0
    
    entry_plan = _build_entry_plan(signal, decision, size_pct)
    
    return BreakoutFilterResult(
        signal=signal,
        stages=stages,
        final_decision=decision,
        position_size_pct=size_pct,
        entry_plan=entry_plan,
    )


# ========== Q1: Regime ==========

def _check_regime(d: date) -> FilterStage:
    regime = get_regime_score(d)
    if regime is None:
        return FilterStage(
            name='Q1_regime', passed=False, confidence='reject',
            detail={'score': None},
            reason='No regime score for today（需先跑 Regime Classifier）',
        )
    
    score = regime['score']
    if score >= 75:
        return FilterStage(
            name='Q1_regime', passed=True, confidence='strong',
            detail={'score': score, 'label': regime['label']},
        )
    if score >= 55:
        return FilterStage(
            name='Q1_regime', passed=True, confidence='weak',
            detail={'score': score, 'label': regime['label']},
        )
    return FilterStage(
        name='Q1_regime', passed=False, confidence='reject',
        detail={'score': score, 'label': regime['label']},
        reason=f'Regime Score {score} < 55',
    )


# ========== Q2: Time window ==========

HARD_BAN_WINDOWS = [
    ((9, 30), (10, 0),  '开盘乱战（09:30-10:00）'),
    ((15, 55), (16, 0), '尾盘最后 5 分钟'),
]

MIDDAY_SOFT_BAN = ((11, 30), (13, 30))   # 仅 retest 允许


def _check_time_window(dt: datetime) -> FilterStage:
    # 转换到美东
    if dt.tzinfo is None:
        dt_et = dt.replace(tzinfo=ET)
    else:
        dt_et = dt.astimezone(ET)
    
    hm = (dt_et.hour, dt_et.minute)
    
    # 硬禁区
    for (start, end, label) in HARD_BAN_WINDOWS:
        if start <= hm < end:
            return FilterStage(
                name='Q2_time', passed=False, confidence='reject',
                detail={'time_et': dt_et.strftime('%H:%M')},
                reason=f'硬禁区：{label}',
            )
    
    # TODO: 读今日 Regime snapshot 的 macro 字段
    # 如果今日有 FOMC 且现在是 13:00-14:00 → 拒绝
    # 如果今日有 CPI/PPI（08:30 ET 公布）且现在 < 10:00 → 拒绝
    # 实现略，放进 v1.1
    
    # 午盘软禁区（只允许 retest，不追）
    if MIDDAY_SOFT_BAN[0] <= hm < MIDDAY_SOFT_BAN[1]:
        return FilterStage(
            name='Q2_time', passed=True, confidence='weak',
            detail={'time_et': dt_et.strftime('%H:%M'), 'note': '午盘低流动性'},
            reason='仅 retest 允许，不追突破',
        )
    
    return FilterStage(
        name='Q2_time', passed=True, confidence='strong',
        detail={'time_et': dt_et.strftime('%H:%M')},
    )


# ========== Q3/Q4/Q5 细节在各自文件 ==========

def _check_volume(signal: BreakoutSignal) -> FilterStage:
    r = check_volume_confirmation(signal.symbol, signal.detected_at)
    return FilterStage(
        name='Q3_volume',
        passed=r.passed,
        confidence=r.confidence,
        detail={'multiple': r.multiple, 'current_vol': r.current_volume, 'avg_vol_20': r.avg_vol_20},
        reason=r.reason,
    )


def _check_timeframe(signal: BreakoutSignal) -> FilterStage:
    r = check_timeframe_alignment(signal.symbol, signal.direction, signal.detected_at)
    return FilterStage(
        name='Q4_timeframe',
        passed=r.aligned_count >= 3,
        confidence='strong' if r.aligned_count >= 4 else ('weak' if r.aligned_count >= 3 else 'reject'),
        detail={
            'aligned_count': r.aligned_count,
            'per_timeframe': r.per_timeframe,
        },
        reason=f'{r.aligned_count}/4 timeframes aligned',
    )


def _check_rs(signal: BreakoutSignal) -> FilterStage:
    r = check_rs_vs_spy(signal.symbol, signal.detected_at)
    if r.rs_30min >= 0.3:
        conf = 'strong'
    elif r.rs_30min >= 0:
        conf = 'weak'
    else:
        conf = 'reject'
    return FilterStage(
        name='Q5_rs',
        passed=r.rs_30min >= 0,
        confidence=conf,
        detail={'rs_30min': r.rs_30min, 'stock_ret': r.stock_return, 'spy_ret': r.spy_return},
        reason=f'RS = {r.rs_30min:+.2f}%',
    )


# ========== Skip / Entry Plan ==========

def _build_skip_result(signal, stages, reason) -> BreakoutFilterResult:
    return BreakoutFilterResult(
        signal=signal,
        stages=stages,
        final_decision='skip',
        position_size_pct=0,
        entry_plan={'skip_reason': reason},
    )


def _build_entry_plan(signal: BreakoutSignal, decision: str, size_pct: float) -> dict:
    P = signal.breakout_price
    if signal.direction == 'up':
        stop = P * 0.995   # 下方 0.5%
    else:
        stop = P * 1.005
    
    plan = {
        'breakout_price': P,
        'direction': signal.direction,
        'stop_loss': stop,
        'size_pct_of_plan': size_pct,
    }
    
    if decision == 'chase':
        plan['entry_method'] = '立即入场（全仓）'
    elif decision == 'split':
        plan['entry_method'] = '分批试仓'
        plan['split_schedule'] = [
            {'step': 1, 'size_pct': 1/3, 'trigger': '突破确认后'},
            {'step': 2, 'size_pct': 2/3, 'trigger': 'Retest 不破后加仓'},
        ]
    elif decision == 'retest':
        plan['entry_method'] = '等 Retest'
        plan['retest_zone'] = [P * 0.995, P * 1.005]
        plan['retest_window_min'] = 30
        plan['retest_conditions'] = [
            f'价格回踩到 {P*0.995:.2f} - {P*1.005:.2f} 范围',
            'Retest bar 的 volume > avg_20 × 1.5',
            'Retest bar 为阳线（突破方向一致）',
            '回踩期间未击穿 P_break',
        ]
    elif decision == 'shadow':
        plan['entry_method'] = 'Shadow Trade（不实盘，只记录）'
    
    return plan
```

### 3.2 Q3: Volume Check

**文件**：`src/breakout/volume_check.py`

```python
"""Volume 确认。"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from data_provider.alpaca_fetcher import AlpacaFetcher


@dataclass
class VolumeCheckResult:
    passed: bool
    confidence: str         # strong / weak / reject
    multiple: float         # 当前 bar vol / 过去 20 bar avg
    current_volume: int
    avg_vol_20: float
    reason: str


def check_volume_confirmation(symbol: str, detected_at: datetime,
                              timeframe: str = '2Min') -> VolumeCheckResult:
    """取近 21 根 2min K 线，比较最新 bar 的 volume 和过去 20 根平均。"""
    alpaca = AlpacaFetcher()
    
    end = detected_at + timedelta(minutes=2)
    start = detected_at - timedelta(minutes=2 * 25)  # 多取几根防 gap
    
    bars = alpaca.bars(symbol, timeframe, start, end)
    if len(bars) < 21:
        return VolumeCheckResult(
            passed=False, confidence='reject',
            multiple=0, current_volume=0, avg_vol_20=0,
            reason=f'数据不足（仅 {len(bars)} 根）',
        )
    
    current = bars[-1]['v']
    prev_20 = sum(b['v'] for b in bars[-21:-1]) / 20
    
    if prev_20 <= 0:
        ratio = 0
    else:
        ratio = current / prev_20
    
    if ratio < 1.2:
        conf, passed, reason = 'reject', False, f'无量 ({ratio:.2f}x)'
    elif ratio < 1.5:
        conf, passed, reason = 'weak', True, f'弱量 ({ratio:.2f}x)，降级仓位'
    elif ratio < 2.0:
        conf, passed, reason = 'weak', True, f'标准量 ({ratio:.2f}x)'
    else:
        conf, passed, reason = 'strong', True, f'强量 ({ratio:.2f}x)'
    
    return VolumeCheckResult(
        passed=passed, confidence=conf,
        multiple=ratio, current_volume=current, avg_vol_20=prev_20,
        reason=reason,
    )
```

### 3.3 Q4: Timeframe Alignment

**文件**：`src/breakout/timeframe_check.py`

```python
"""多周期一致性检查：2min / 5min / 15min / 日线 同方向。"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from data_provider.alpaca_fetcher import AlpacaFetcher


@dataclass
class TimeframeCheckResult:
    aligned_count: int                    # 0-4
    per_timeframe: dict                   # {'2Min': 'up', '5Min': 'up', ...}


def check_timeframe_alignment(symbol: str, direction: str,
                              detected_at: datetime) -> TimeframeCheckResult:
    alpaca = AlpacaFetcher()
    
    timeframes = [
        ('2Min', 20),     # 过去 20 根 2min
        ('5Min', 20),     # 过去 20 根 5min
        ('15Min', 20),    # 过去 20 根 15min
        ('1Day', 5),      # 过去 5 天日线
    ]
    
    per_tf = {}
    aligned_count = 0
    
    for tf, lookback in timeframes:
        trend = _detect_trend(alpaca, symbol, tf, detected_at, lookback)
        per_tf[tf] = trend
        if trend == direction:
            aligned_count += 1
    
    return TimeframeCheckResult(
        aligned_count=aligned_count,
        per_timeframe=per_tf,
    )


def _detect_trend(alpaca, symbol, tf, ref_time, lookback):
    """
    简化的趋势判断：
      close 的斜率 > 0 → up
      close 的斜率 < 0 → down
      slope 接近 0 → flat
    
    用线性回归简单实现。
    """
    import numpy as np
    
    tf_minutes = {'2Min': 2, '5Min': 5, '15Min': 15, '1Day': 1440}[tf]
    start = ref_time - timedelta(minutes=tf_minutes * (lookback + 5))
    end = ref_time
    
    bars = alpaca.bars(symbol, tf, start, end)
    if len(bars) < lookback:
        return 'flat'
    
    closes = [b['c'] for b in bars[-lookback:]]
    x = np.arange(len(closes))
    slope = np.polyfit(x, closes, 1)[0]
    
    # 标准化：slope 相对价格的百分比
    avg_price = sum(closes) / len(closes)
    slope_pct = slope / avg_price * 100  # 每 bar 的百分比斜率
    
    if slope_pct > 0.05:
        return 'up'
    if slope_pct < -0.05:
        return 'down'
    return 'flat'
```

### 3.4 Q5: RS vs SPY

**文件**：`src/breakout/rs_check.py`

```python
"""Relative Strength vs SPY."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from data_provider.alpaca_fetcher import AlpacaFetcher


@dataclass
class RSCheckResult:
    rs_30min: float         # stock return - SPY return，in %
    stock_return: float     # in %
    spy_return: float       # in %


def check_rs_vs_spy(symbol: str, detected_at: datetime,
                    window_minutes: int = 30) -> RSCheckResult:
    alpaca = AlpacaFetcher()
    
    start = detected_at - timedelta(minutes=window_minutes)
    end = detected_at
    
    stock_bars = alpaca.bars(symbol, '1Min', start, end)
    spy_bars = alpaca.bars('SPY', '1Min', start, end)
    
    if not stock_bars or not spy_bars:
        return RSCheckResult(rs_30min=0, stock_return=0, spy_return=0)
    
    stock_ret = (stock_bars[-1]['c'] - stock_bars[0]['o']) / stock_bars[0]['o'] * 100
    spy_ret = (spy_bars[-1]['c'] - spy_bars[0]['o']) / spy_bars[0]['o'] * 100
    
    return RSCheckResult(
        rs_30min=stock_ret - spy_ret,
        stock_return=stock_ret,
        spy_return=spy_ret,
    )
```

---

## 4. Retest Tracker：事后追踪真假突破

用户入场后 30 分钟内，系统要回答一个问题：**这是真突破还是假突破？**

**文件**：`src/breakout/retest_tracker.py`

```python
"""Retest 追踪器。监控突破后 30 分钟内的价格行为，判定真/假突破。"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from data_provider.alpaca_fetcher import AlpacaFetcher


@dataclass
class RetestOutcome:
    was_fake_breakout: bool
    max_favorable_move_pct: float    # 最大有利价格
    reversal_pct: float              # 从最高回撤多少
    reversal_time_seconds: int       # 从突破到反转用了多久
    detail: str


def track_retest(
    symbol: str,
    breakout_price: float,
    breakout_time: datetime,
    direction: str,
    window_minutes: int = 30,
) -> RetestOutcome:
    """
    突破发生 30 分钟后跑一次，回填 was_fake_breakout。
    
    假突破判定：
      30 分钟内价格击穿 P_break × 0.99（上突破）或 × 1.01（下突破），
      且未在 5 分钟内收回 → 标记为假突破。
    """
    alpaca = AlpacaFetcher()
    
    end = breakout_time + timedelta(minutes=window_minutes)
    bars = alpaca.bars(symbol, '1Min', breakout_time, end)
    
    if not bars:
        return RetestOutcome(
            was_fake_breakout=None,
            max_favorable_move_pct=0,
            reversal_pct=0,
            reversal_time_seconds=0,
            detail='无数据',
        )
    
    if direction == 'up':
        # 最大有利
        max_high = max(b['h'] for b in bars)
        max_favor_pct = (max_high - breakout_price) / breakout_price * 100
        
        # 最低点
        min_low = min(b['l'] for b in bars)
        reversal_pct = (max_high - min_low) / max_high * 100
        
        # 是否击穿 0.99 × P_break
        ko_level = breakout_price * 0.99
        was_fake = min_low < ko_level
        
        # 反转时间
        reversal_time = 0
        if was_fake:
            for b in bars:
                if b['l'] < ko_level:
                    reversal_time = int((datetime.fromisoformat(b['t'].replace('Z', '+00:00'))
                                         - breakout_time).total_seconds())
                    break
    else:  # down
        min_low = min(b['l'] for b in bars)
        max_favor_pct = (breakout_price - min_low) / breakout_price * 100
        max_high = max(b['h'] for b in bars)
        reversal_pct = (max_high - min_low) / min_low * 100
        ko_level = breakout_price * 1.01
        was_fake = max_high > ko_level
        reversal_time = 0
        if was_fake:
            for b in bars:
                if b['h'] > ko_level:
                    reversal_time = int((datetime.fromisoformat(b['t'].replace('Z', '+00:00'))
                                         - breakout_time).total_seconds())
                    break
    
    return RetestOutcome(
        was_fake_breakout=was_fake,
        max_favorable_move_pct=max_favor_pct,
        reversal_pct=reversal_pct,
        reversal_time_seconds=reversal_time,
        detail=f'Max favor {max_favor_pct:.2f}% / Reversal {reversal_pct:.2f}%',
    )
```

### 4.1 定时回填 trades 表

Cron 每小时跑一次，找 `was_fake_breakout IS NULL` 且 `entry_time < NOW() - 1 hour` 的 trades，回填：

```python
# src/breakout/backfill_fake_breakout.py

def backfill_fake_breakout_for_trades(lookback_days: int = 7):
    """扫描最近 N 天需要判定的 trades，回填字段。"""
    import sqlite3
    from src.config import DB_URL
    from src.breakout.retest_tracker import track_retest
    
    conn = sqlite3.connect(DB_URL.replace('sqlite:///', ''))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute('''
        SELECT id, underlying, avg_entry_price, entry_time, direction
        FROM trades
        WHERE was_fake_breakout IS NULL
          AND trade_style LIKE 'breakout_%'
          AND entry_time >= datetime('now', ?)
          AND entry_time <= datetime('now', '-1 hour')
        ORDER BY entry_time DESC
        LIMIT 100
    ''', (f'-{lookback_days} days',))
    
    rows = cur.fetchall()
    for row in rows:
        try:
            outcome = track_retest(
                symbol=row['underlying'],
                breakout_price=row['avg_entry_price'],
                breakout_time=datetime.fromisoformat(row['entry_time']),
                direction='up' if row['direction'] == 'long' else 'down',
            )
            cur.execute('''
                UPDATE trades SET was_fake_breakout = ?
                WHERE id = ?
            ''', (outcome.was_fake_breakout, row['id']))
        except Exception:
            pass
    
    conn.commit()
    conn.close()
```

---

## 5. 历史 trades 回填 trade_style

Phase 0 启动后，需要给过去 3 个月的 trades 打标签（trade_style / was_fake_breakout 等）。

**文件**：`src/breakout/backfill_history.py`

这个比较耗时，用 AI 辅助：

```python
"""历史 trades 回填 trade_style。"""
import litellm
from src.breakout.detector import BreakoutDetector
from src.breakout.volume_check import check_volume_confirmation


def backfill_trade_style(trade: dict) -> str:
    """
    输入一条 trade 记录，推断 trade_style。
    
    推断逻辑：
    1. 如果 entry_time 附近 5 分钟内有对应方向的突破信号 → breakout_*
       - 若 Volume > 2x → breakout_split
       - 若中间有回踩 → breakout_retest
       - 否则 → breakout_chase
    2. 否则看 RSI 位置：
       - 超卖反弹 → pullback_buy
       - 常态 → other
    3. LLM 二次确认（可选）
    """
    # 简化逻辑，实际要更复杂
    detector = BreakoutDetector()
    signal = detector.scan(trade['underlying'], timeframe='2Min', now=trade['entry_time'])
    
    if signal and signal.direction == ('up' if trade['direction'] == 'long' else 'down'):
        vol = check_volume_confirmation(trade['underlying'], trade['entry_time'])
        
        if vol.multiple > 2.0:
            return 'breakout_split'
        
        # 回踩识别：入场前 5 分钟内价格回过阻力位
        # TODO 实现
        
        return 'breakout_chase'
    
    return 'other'


def backfill_all_trades_ai(limit: int = 100):
    """用 LLM 辅助批量打标签（慢但准）。"""
    prompt = """
基于以下 trade 信息，判断 trade_style：

Symbol: {underlying}
Direction: {direction}
Entry: ${entry_price} at {entry_time}
Exit: ${exit_price} at {exit_time}
PnL: ${pnl_net}
是否期权: {is_option}
DTE: {dte_bucket}

入场前 30 分钟 2min K 线：
{pre_entry_bars}

候选 trade_style：
- breakout_chase: 看到突破立即追入
- breakout_retest: 等回踩后入场
- breakout_split: 分批试仓
- pullback_buy: 强势股回调买入
- mean_reversion: 超卖反弹 / 超买回调
- event_driven: 财报 / 新闻驱动
- other: 无法明确归类

严格返回 JSON：{{"trade_style": "...", "confidence": 0.0-1.0, "reason": "..."}}
无客套话。
"""
    # 实现略
```

**注意**：历史回填的标签**不要太认真**，错误率 20-30% 是正常的。关键是**从此开始，新入场的 trade 都有准确标签**。

---

## 6. 前端 Breakout Status 面板

### 6.1 用户体验

在 `/stock/:symbol` 页面实时显示：

```
┌────────────────────────────────────────────┐
│ 🚀 Breakout Detected  NVDA @ $182.45       │
│    14:32 ET · Up · prev_day_high           │
├────────────────────────────────────────────┤
│                                            │
│ Q1 Regime      ✅ strong   Score 68        │
│ Q2 Time        ✅ strong   14:32 ET        │
│ Q3 Volume      ✅ strong   2.35x (avg20)   │
│ Q4 Timeframes  ⚠️ weak     3/4 aligned     │
│                (2Min up / 5Min up /        │
│                 15Min up / 1Day flat)      │
│ Q5 RS vs SPY   ✅ strong   +0.58%          │
│                                            │
├────────────────────────────────────────────┤
│ 💡 Decision: RETEST                        │
│                                            │
│    Don't chase. Wait for:                 │
│    · Price retest $181.54 - $183.36       │
│    · Retest bar volume > 1.5x avg         │
│    · Bullish confirmation bar             │
│                                            │
│    Stop loss: $181.54 (-0.5%)             │
│    Window: 30 min                          │
│                                            │
│ [ Log as Shadow Trade ]  [ Notify on      │
│                             Retest ]       │
└────────────────────────────────────────────┘
```

### 6.2 API 端点

**文件**：`api/routers/breakout.py`

```python
from fastapi import APIRouter, HTTPException
from datetime import datetime

router = APIRouter(prefix='/api/v1/breakout', tags=['breakout'])


@router.get('/{symbol}/check')
async def check_breakout(symbol: str, use_prev_day: bool = False):
    """实时扫描是否有突破 + 跑 filter。"""
    from src.breakout.detector import BreakoutDetector
    from src.breakout.filter import filter_breakout
    
    detector = BreakoutDetector()
    if use_prev_day:
        signal = detector.scan_with_prev_day_levels(symbol)
    else:
        signal = detector.scan(symbol, timeframe='2Min', lookback_bars=60)
    
    if not signal:
        return {'has_breakout': False, 'symbol': symbol}
    
    result = filter_breakout(signal)
    return {
        'has_breakout': True,
        'symbol': symbol,
        'result': result.to_dict(),
    }


@router.get('/history')
async def list_breakout_history(days: int = 30):
    """返回过去 N 天所有 trade_style 为 breakout_* 的 trades + filter stages。"""
    # 实现略
    ...


@router.get('/stats/chase_vs_retest')
async def chase_vs_retest_stats(days: int = 30):
    """chase / split / retest 三种方式的胜率对比。核心 Reality Test。"""
    import sqlite3
    from src.config import DB_URL
    
    conn = sqlite3.connect(DB_URL.replace('sqlite:///', ''))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute('''
        SELECT
            trade_style,
            COUNT(*) AS n,
            SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(AVG(pnl_net), 2) AS avg_pnl,
            ROUND(SUM(pnl_net), 2) AS total_pnl,
            SUM(CASE WHEN was_fake_breakout = 1 THEN 1 ELSE 0 END) AS fake_count
        FROM trades
        WHERE status = 'closed'
          AND entry_time >= datetime('now', ?)
          AND trade_style LIKE 'breakout_%'
        GROUP BY trade_style
    ''', (f'-{days} days',))
    
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r['win_rate'] = r['wins'] / r['n'] if r['n'] else 0
        r['fake_rate'] = r['fake_count'] / r['n'] if r['n'] else 0
    
    return {'by_style': rows, 'days': days}
```

### 6.3 React 组件

**文件**：`apps/dsa-web/src/components/journal/BreakoutStatusCard.tsx`

```tsx
import { useEffect, useState } from 'react'

interface Props {
  symbol: string
}

export function BreakoutStatusCard({ symbol }: Props) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  
  useEffect(() => {
    const fetch = async () => {
      setLoading(true)
      const r = await window.fetch(`/api/v1/breakout/${symbol}/check`)
      const json = await r.json()
      setData(json)
      setLoading(false)
    }
    fetch()
    const id = setInterval(fetch, 30_000)  // 每 30s 刷新
    return () => clearInterval(id)
  }, [symbol])
  
  if (loading) return <div>Scanning...</div>
  if (!data.has_breakout) return (
    <div className="card p-4 text-gray-500">
      No breakout detected in last 60 bars
    </div>
  )
  
  const { result } = data
  const stageIcon = (s: any) => {
    if (!s.passed) return '❌'
    return s.confidence === 'strong' ? '✅' : '⚠️'
  }
  
  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-2xl">🚀</span>
        <h3 className="text-lg font-semibold">
          Breakout Detected: {symbol} @ ${result.signal.breakout_price}
        </h3>
      </div>
      
      <table className="w-full text-sm">
        <tbody>
          {result.stages.map((s: any, i: number) => (
            <tr key={i} className="border-b">
              <td className="py-2">{stageIcon(s)} {s.name}</td>
              <td className="py-2">{s.confidence}</td>
              <td className="py-2 text-gray-600">{s.reason || JSON.stringify(s.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      
      <div className={`p-3 rounded ${
        result.final_decision === 'skip' ? 'bg-red-50' :
        result.final_decision === 'retest' ? 'bg-yellow-50' : 'bg-green-50'
      }`}>
        <div className="font-bold">
          💡 Decision: {result.final_decision.toUpperCase()}
        </div>
        {result.final_decision === 'retest' && (
          <div className="text-sm mt-2">
            <p>Wait for price retest to {result.entry_plan.retest_zone[0].toFixed(2)} - {result.entry_plan.retest_zone[1].toFixed(2)}</p>
            <p>Stop loss: ${result.entry_plan.stop_loss.toFixed(2)}</p>
            <p>Window: {result.entry_plan.retest_window_min} min</p>
          </div>
        )}
      </div>
      
      <div className="flex gap-2">
        <button className="btn btn-primary">Log as Shadow Trade</button>
        <button className="btn btn-secondary">Notify on Retest</button>
      </div>
    </div>
  )
}
```

---

## 7. 月度报告新增章节

在 Monthly Review（05 里的 Jinja 模板）中新增章节：

```jinja
## 八、Breakout Filter 执行力评估

本月 {{ breakout_stats.total }} 笔突破类交易：

| 入场方式 | 笔数 | 胜率 | 总盈亏 | 假突破率 |
|---------|------|------|--------|---------|
| breakout_chase | {{ s.chase.n }} | {{ s.chase.win_rate | pct }} | ${{ s.chase.total_pnl }} | {{ s.chase.fake_rate | pct }} |
| breakout_split | {{ s.split.n }} | {{ s.split.win_rate | pct }} | ${{ s.split.total_pnl }} | {{ s.split.fake_rate | pct }} |
| breakout_retest | {{ s.retest.n }} | {{ s.retest.win_rate | pct }} | ${{ s.retest.total_pnl }} | {{ s.retest.fake_rate | pct }} |

{% if s.retest.win_rate > s.chase.win_rate + 0.15 %}
**关键发现**：Retest 胜率高 Chase 胜率 {{ (s.retest.win_rate - s.chase.win_rate) | pct }} 个百分点。
下月建议：能等 retest 就等 retest。不等的自己问"凭什么这次突破值得追"。
{% elif s.retest.n < 3 %}
本月 retest 样本太少（{{ s.retest.n }} 笔）。下月强制至少 5 笔 retest 尝试。
{% endif %}

{% if s.chase.fake_rate > 0.5 %}
警告：本月 chase 假突破率 {{ s.chase.fake_rate | pct }}（高于 50%）。
这说明你的 chase 几乎一半是在被收割。
{% endif %}
```

这段自动生成后会**以数字告诉你** retest 是不是值得。

---

## 8. Pre-filter Pass 字段回填

Phase 0 数据只有历史 trades，还没有 Breakout Filter 结果。可以回补：

```python
def backfill_pre_filter_fields(limit: int = None):
    """给历史 trades 回填 pre_filter_pass / regime_score_at_entry 等字段。"""
    import sqlite3
    
    conn = sqlite3.connect(...)
    cur = conn.cursor()
    
    # 拉所有 breakout_* 类 trades
    cur.execute('''
        SELECT id, entry_time FROM trades
        WHERE trade_style LIKE 'breakout_%'
          AND pre_filter_pass IS NULL
        ORDER BY entry_time
    ''')
    
    for row in cur.fetchall():
        from src.regime.storage import get_regime_score
        regime = get_regime_score(row['entry_time'].date())
        
        pre_pass = bool(regime and regime['score'] >= 55)
        score = regime['score'] if regime else None
        
        cur.execute('''
            UPDATE trades SET pre_filter_pass = ?, regime_score_at_entry = ?
            WHERE id = ?
        ''', (pre_pass, score, row['id']))
    
    conn.commit()
```

---

## 9. Agent 工具接入

**文件**：`src/agent/tools/check_breakout.py`

```python
TOOL_METADATA = {
    'name': 'check_breakout',
    'description': '检测标的是否正在发生突破并运行四层过滤。返回建议入场方式。',
    'parameters': {
        'type': 'object',
        'properties': {
            'symbol': {'type': 'string', 'description': '正股代码'},
            'use_prev_day_high': {
                'type': 'boolean',
                'default': False,
                'description': '用昨日高低点作为突破位（更稳定）',
            },
        },
        'required': ['symbol'],
    },
}


def execute(symbol: str, use_prev_day_high: bool = False) -> dict:
    from src.breakout.detector import BreakoutDetector
    from src.breakout.filter import filter_breakout
    
    detector = BreakoutDetector()
    signal = (
        detector.scan_with_prev_day_levels(symbol)
        if use_prev_day_high
        else detector.scan(symbol, timeframe='2Min')
    )
    
    if not signal:
        return {'has_breakout': False}
    
    return {
        'has_breakout': True,
        'signal': asdict(signal),
        'filter_result': filter_breakout(signal).to_dict(),
    }
```

这样 Agent 可以响应"NVDA 现在怎样"这种问题，主动扫描突破。

---

## 10. Phase 0 Week 3 交付清单

按 `03_MIGRATION_PLAN.md` Week 3 要求：

- [ ] `src/breakout/detector.py` + 单元测试
- [ ] `src/breakout/volume_check.py`
- [ ] `src/breakout/timeframe_check.py`
- [ ] `src/breakout/rs_check.py`
- [ ] `src/breakout/filter.py` 总函数
- [ ] `src/breakout/retest_tracker.py`
- [ ] trades 表字段 migration（`trade_style` / `pre_filter_pass` / `breakout_volume_mult` / `timeframe_alignment` / `rs_vs_spy` / `entry_was_retest` / `was_fake_breakout`）
- [ ] 历史 trades 批量回填（AI 辅助，允许 20% 错误率）
- [ ] `api/routers/breakout.py`
- [ ] 前端 `BreakoutStatusCard.tsx`（基础版，实时 30s 刷新）
- [ ] Agent 工具 `check_breakout` 注册
- [ ] Monthly Review 新章节模板

---

## 11. Batch 3 下一份

`08_AGENT_SKILL_REGISTRY.md` 讲三个新 skill 怎么写：
- `option_trader.yaml` + `SKILL.md`（短期期权操作）
- `leap_explorer.yaml` + `SKILL.md`（LEAP 候选筛选）
- `trend_follower.yaml` + `SKILL.md`（趋势流入场）

每个 skill 都是 YAML + prompt 组合，通过原项目 `strategies/` 机制接入 Agent orchestrator，用户在 `/chat` 里 `/ask option_trader NVDA` 就能触发。

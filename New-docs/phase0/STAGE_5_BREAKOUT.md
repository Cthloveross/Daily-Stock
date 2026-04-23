# Stage 5 · Breakout Detector + 四层过滤

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 3（Regime 作为 Q1 Gate）
> 产出：Detector + Q3/Q4/Q5 各自 check + 综合 filter

---

## 做了什么

### 1. Detector（`src/breakout/detector.py`）

```python
from src.breakout.detector import Bar, BreakoutDetector
det = BreakoutDetector(bars_provider=...)  # 可选；也可 scan(bars=...) 直传
sig = det.scan("NVDA", timeframe="2Min", lookback_bars=60, now=None)
sig = det.scan_with_prev_day_levels("NVDA", prev_day_high=104, prev_day_low=99)
```

- **数据源无关**：传 `bars_provider` callable，返回 `list[Bar]`。生产环境接 yfinance / Alpaca；测试时直接传 bars 列表。
- **检测类型**：`range_high` / `range_low`（滚动高/低）、`prev_day_high` / `prev_day_low`。后者供 Q2 使用"破前日"信号。
- **输出**：`BreakoutSignal(symbol, timeframe, direction, detected_at, breakout_price, reference_high, reference_low, reference_volume, current_volume, reason)`。

### 2. 三个独立 check（Q3-Q5）

| Q | 函数 | 输入 | 判定 |
|--|--|--|--|
| Q3 | `check_volume_confirmation(current_vol, ref_vol, required_multiple=1.2)` | 当前 bar 量 / 历史均量 | `multiple >= required_multiple` |
| Q4 | `check_timeframe_alignment(direction, tf_price, tf_ma, timeframes, require_aligned=3)` | 多周期 price + MA | `aligned_count >= require_aligned` |
| Q5 | `check_rs_vs_spy(direction, symbol_ret, spy_ret, rs_threshold=0.3)` | 标的/SPY 区间收益率 | up 需 `rs >= threshold`；down 需 `<= -threshold` |

各 check 独立可测，返回自己的 `*CheckResult` dataclass（`passed + 细节`）。

### 3. 综合 filter（`src/breakout/filter.py`）

```python
filter_breakout(
    signal,
    regime_score=70,
    regime_min=55,
    volume_multiple=1.2,
    tf_price={...}, tf_ma={...}, tf_require_aligned=3,  # Q4 可选
    symbol_return_pct=1.0, spy_return_pct=0.2,          # Q5 可选
    rs_threshold=0.3,
) -> BreakoutFilterResult
```

- **短路语义**：Q1 失败就返回，Q1 过后评估 Q2... 任一 Q 失败停止。这样 UI 能显示"卡在哪步"。
- **Q4/Q5 不可用时**：参数传 None → 跳过该 Q，不视为失败（因为上游数据缺失不代表信号本身有问题）
- **保守默认**：Q1 regime_score 为 None 时直接拒绝（宁可漏不错）

### 4. 11 个单测

- `test_checks.py` — volume / timeframe / RS 各边界（11 cases）
- `test_detector.py` — up/down/无信号/样本不足/破前日（5 cases）
- `test_filter.py` — 各 Q 单独失败 + 全通过（6 cases）

### 5. trades schema 字段已占位（Stage 2 ORM 里已定义）

`journal_trades` 的 `trade_style` / `pre_filter_pass` / `breakout_volume_mult` / `timeframe_alignment` / `rs_vs_spy` / `entry_was_retest` / `was_fake_breakout` 在 Stage 2 建表时就加好了。Stage 6 回填时直接 UPDATE。

---

## 怎么验证

```bash
python -m pytest src/breakout -v   # 16 tests
```

REPL smoke：
```python
from datetime import datetime, timedelta
from src.breakout.detector import Bar, BreakoutDetector
from src.breakout.filter import filter_breakout

start = datetime(2026, 4, 17, 9, 30)
bars = [Bar(start + timedelta(minutes=2*i), c, c, c, c, 1000) for i, c in enumerate([100]*10 + [105])]
sig = BreakoutDetector().scan("NVDA", bars=bars)
print(filter_breakout(sig, regime_score=70, symbol_return_pct=1.2, spy_return_pct=0.2))
```

应该输出 `passed=True, reason='all_gates_passed'`。

---

## 留了什么坑 / 显式延后

- **实时扫描**：`BreakoutDetector` 本身是纯函数，没接长驻进程/scanner。真正的实时 watcher + Telegram 推送在 Phase 1 做（Phase 0 只准备检测能力 + 批量历史回填在 Stage 6）。
- **VWAP break / round number**：detector 的 `reason` 字段已支持，但具体检测逻辑（当前价跨 VWAP / 跨 100/200/500 整数关口）留到 Phase 1。
- **Retest tracker**：30min 后回看确认是否假突破 → Stage 6。
- **MA 数据源**：`timeframe_check` 把 MA 作为输入，没自己算。接入 yfinance 多周期 MA 留 Stage 6 批量回填时顺手做。

---

## 下一步

Stage 6：历史 trades 批量标记 `trade_style` + `was_fake_breakout`，让用户第一次看到自己"追突破 vs retest"的实际胜率差距。

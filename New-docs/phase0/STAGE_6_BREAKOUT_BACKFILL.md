# Stage 6 · Breakout 历史回填 + AI 打标签

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 2（trades 表）+ Stage 5（detector + filter）
> 产出：Retest tracker + 两个 backfill 脚本 + 6 个新测试

---

## 做了什么

### 1. Retest tracker（`src/breakout/retest_tracker.py`）

```python
track_retest(
    breakout_price, direction, breakout_time,
    subsequent_bars: list[Bar],
    window_minutes=30,
    retest_tolerance_pct=0.2,
) -> RetestOutcome(
    is_fake_breakout, retest_observed, continuation_after_retest,
    window_minutes, max_close_after, min_close_after, bars_observed
)
```

- 30min 窗口内任何 close 回到突破价以下 (tol 0.2%) → `is_fake_breakout=True`
- 检测"突破后回踩到水位线附近再继续"→ `retest_observed=True + continuation_after_retest`
- 空 bars → 全 False + `bars_observed=0`（不 crash）

### 2. trade_style 批量打标签（`src/breakout/backfill_trade_style.py`）

纯规则分类（不调 LLM，避免不确定性 + 费用）：

| 场景 | label |
|--|--|
| equity + 隔夜持有 | `equity_swing` |
| equity 日内 | `mean_reversion` |
| 0DTE 快速持有 (<30min) | `breakout_chase` |
| 0DTE 长持 | `breakout_chase` / `gap_fade` |
| 1-3 DTE 短持 | `breakout_chase` |
| 1-3 DTE 长持 | `pullback_buy` |
| 4-7 DTE | `pullback_buy` |
| 其它 | `other`（待用户手动修正） |

```bash
python -m src.breakout.backfill_trade_style [--portfolio LABEL] [--overwrite]
```

默认 skip 已标签的 trade；`--overwrite` 强制重分类。

### 3. fake_breakout 批量判定（`src/breakout/backfill_fake_breakout.py`）

conservative outcome-based 代理（不依赖实时 bar 历史）：

- 标的为 `breakout_chase` / `retest` 类 trade
- `pnl_pct < -20%` AND `hold_seconds < 90min`
- → `was_fake_breakout = True`

```bash
python -m src.breakout.backfill_fake_breakout [--loss-threshold -20] [--short-hold-seconds 5400]
```

### 4. 12 个新测试

- `test_retest_tracker.py` — real / fake / retest / empty（4 cases）
- `test_backfill.py` — classify_trade 4 种、backfill_trade_style 幂等、backfill_fake_breakout 边界（8 cases）

累计 **153 passed**。

---

## 怎么验证

```bash
python -m pytest src/breakout -v   # 28 tests

# 在有真实 trades 的 DB 上：
python -m scripts.rebuild_trades
python -m src.breakout.backfill_trade_style
python -m src.breakout.backfill_fake_breakout

sqlite3 data/daily_stock.db "
SELECT trade_style, COUNT(*), ROUND(AVG(pnl_net),2), ROUND(AVG(pnl_pct),2)
FROM journal_trades
WHERE is_option=1
GROUP BY trade_style
ORDER BY COUNT(*) DESC
"

sqlite3 data/daily_stock.db "
SELECT was_fake_breakout, COUNT(*), ROUND(AVG(pnl_net),2)
FROM journal_trades
WHERE trade_style IN ('breakout_chase', 'retest')
GROUP BY was_fake_breakout
"
```

预期：用户能看到 `breakout_chase` 的假突破率远高于 `retest`，`retest` 的平均 PnL 远高于 `breakout_chase`。**这就是 Phase 0 的行为数据反馈**。

---

## 留了什么坑 / 显式延后

- **没接真实 bar 数据做 fake 判定**：靠 outcome（最终 PnL + hold 时长）做代理。Phase 1 接入 yfinance intraday bar → `retest_tracker.track_retest` 后，把 outcome-based 判定替换成 live-window 判定，准确率会高不少。
- **没上 LLM 辅助分类**：文档提过"允许 20-30% 错误率，LLM 辅助"。Stage 6 走纯规则因为：(1) 保持 CI 确定性；(2) 控费用；(3) LLM 标签易过拟合用户叙事。如果规则 coverage 不够，Stage 10 的 Agent skill 可以在对话里逐笔 refine。
- **每小时 cron**（文档原计划 `.github/workflows/breakout_backfill.yml`）**未入库**：Phase 0 CSV 入库频率低（每日盘后），再设个每小时 cron 意义不大。Stage 11 的 folder watcher 触发 rebuild_trades → 顺手触发 backfill 更自然。
- **BreakoutDetector 的实时 scan** 还没接 Alpaca：Phase 1 再做（需要长驻 scanner 进程）。
- **regime_score_at_entry 字段**：schema 已占位但 Stage 6 没填。等 Stage 9 AI 月度复盘时一次性 join regime_scores 填充，避免双重 DB round-trip。

---

## 下一步

Stage 7：前端 Mirror 核心（Journal UI + Reality Test 首页卡片）。

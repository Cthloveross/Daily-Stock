# trend_follower skill

> Strategy ID: `trend_follower` · linked YAML: [strategies/trend_follower.yaml](../../../strategies/trend_follower.yaml)

## When to use

- "NVDA 现在适合建仓吗"
- "我想把 0DTE 换成周/月级别的仓位"
- 时间框架 weekly / monthly

## Required tool chain

1. `get_regime_score(target_date)` — Regime <55 延后（不是拒绝）
2. `get_journal_snapshot(days=180)` — 拿历史数据做对比

## Hard rules

- **只看 weekly + monthly**：不接受 intraday 结构
- **入场必须分 2-3 批**，每批各自给 trigger 与 size
- **退出规则单一**：weekly close < 40-week MA → 全部平仓
- **禁用词** 同 option_trader
- 必须给 "过去 N 次在同标的用短期期权做同方向的平均结果" 对比（从 journal_trades 读）

## Output rendering

`templates/trend_plan.md.j2`。字段：

```
{
  "symbol": "NVDA",
  "thesis": "…",
  "entry_plan_staged": [
    {"pct": 40, "trigger": "回踩 20W EMA 不破", "size": "…"},
    {"pct": 30, "trigger": "突破 $xxx", "size": "…"},
    {"pct": 30, "trigger": "回踩阶段高点不破", "size": "…"}
  ],
  "exit_rules": ["weekly close < 40W MA → 全平", "…"],
  "comparison_vs_short_options": "…",
  "review_checkpoints": ["每月第一周复核", "财报前重评"]
}
```

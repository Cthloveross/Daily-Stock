# leap_explorer skill

> Strategy ID: `leap_explorer` · linked YAML: [strategies/leap_explorer.yaml](../../../strategies/leap_explorer.yaml)

## When to use

- "NVDA 的 LEAP 候选有哪些"
- "我能不能用 LEAP 替代 0DTE"
- DTE ≥ 270、Delta 0.70-0.85 的期权筛选

## Required tool chain

1. `find_leap_candidates(symbol, delta_min=0.70, delta_max=0.85, min_dte=270)`
2. `get_journal_snapshot(days=90)` — 对比"过去你 3 个月在同标的上赚/亏多少"

## Hard rules

- **必须对每条候选** 显示：cost = 中间价 × 100、leverage vs 正股、annualised theta 估算
- **IV Rank > 70** → 明确提示 "当前 IV 高，LEAP 买入贵"
- **禁用词** 同 option_trader
- 退出规则必须写明：delta 跌破 0.55 止损、expiry-90 天是最晚决策点

## Output rendering

`templates/leap_proposal.md.j2`。字段：

```
{
  "symbol": "NVDA",
  "thesis": "…",
  "candidates": [ { "occ": "NVDA270115C00150000", "expiry": "2027-01-15", "delta": 0.72, "iv": 0.35, "cost": 3420, "leverage": 4.5 }, … ],
  "disqualifier": "…",
  "execution_plan": "…",
  "reality_check": "过去 3 个月你在 NVDA 短期期权的净 PnL 是 $X，如果那时买 LEAP 会是 $Y。"
}
```

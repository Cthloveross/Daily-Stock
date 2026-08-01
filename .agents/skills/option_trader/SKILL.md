# option_trader skill

> Strategy ID: `option_trader` · linked YAML: [strategies/option_trader.yaml](../../../strategies/option_trader.yaml)

## When to use

- 用户输入是 "NVDA 今天能买 call 吗"、"SPY 0DTE put 怎么样" 等短期期权决策类问题
- DTE 范围 0-90
- 标的是美股 (US options)

## Required tool chain

1. `get_regime_score(target_date)` — 检查今天是否允许做期权
2. `check_breakout(symbol, ...)` — Q1-Q5 过滤（调用前可以先让 LLM 简单分析 K 线给出 direction / breakout_price）
3. `get_option_chain(symbol, expiry, right)` — 挑合约
4. `get_journal_snapshot(days=14)` — 看用户最近的 0DTE 配额与违规模式

## Hard rules

- **Regime < 55** → 主结论必须是"今天不做期权" / "shadow 模拟"；拒绝给实盘开仓建议
- **Q3-Q5 任一 fail** → 给出"等下一次回踩"建议，不准让用户硬追
- **禁用词**：加油 / 稳赚 / 必涨 / 相信 / emoji
- **必含章节**：counter_evidence（≥1 条）、journal_context

## Output rendering

使用 `templates/option_decision.md.j2`。字段：

```
{
  "symbol": "NVDA",
  "decision": "wait_retest | open_half | skip | shadow_only",
  "entry_plan": "…",
  "exit_plan": "…",
  "breakout_context": "Q1…Q5 逐条",
  "counter_evidence": ["…", "…"],
  "journal_context": "过去 14 天 0DTE 总数 / 违规次数",
  "risks": ["…"]
}
```

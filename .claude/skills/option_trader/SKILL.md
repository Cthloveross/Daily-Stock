---
name: option-trader
description: Review US 0-7DTE option candidates under the INTRA-0-7 same-day policy, using Regime and breakout data only as context while enforcing verifiable liquidity and exit constraints.
---

# option_trader skill

> Strategy ID: `option_trader` · linked YAML: [strategies/option_trader.yaml](../../../strategies/option_trader.yaml)

## When to use

- 用户输入是 "NVDA 今天能买 call 吗"、"SPY 0DTE put 怎么样" 等短期期权决策类问题
- DTE 范围 0-7；8+DTE 不属于本 skill
- 标的是美股 (US options)

## Required tool chain

1. `get_regime_score(target_date)` — 读取风险背景；不得把分数当 0DTE 禁令或授权
2. `check_breakout(symbol, ...)` — 把 Q1-Q5 结果作为支持/反对证据，不替代合约门禁
3. `get_option_chain(symbol, expiry, right)` — 在 0-7DTE 内核验同一张合约
4. `get_journal_snapshot(days=14)` — 读取近期 DTE 分布、盈亏与 Reality Test；不计算固定周配额

## Hard rules

- **持有合同**：所有 0-7DTE 候选必须同一 ET 日平仓；不提供隔夜路径
- **时间合同**：仅使用有效的服务端 `execution_policy`：09:45 ET 起，1-7DTE 在真实收盘前 45 分钟截止，0DTE 在真实收盘前 90 分钟截止，真实收盘前 15 分钟退出。半日市同步提前；未知、缺失或过期时钟不能猜测放行。这是手动执行参考，不限制 underlying P0/P1-O 异动提醒
- **合约合同**：bid > 0 且 `0.35 ≤ |delta| ≤ 0.65`；0DTE 点差 ≤8%、报价龄 ≤30 秒，1-7DTE 点差 ≤10%、报价龄 ≤90 秒；缺字段即标缺/拒绝
- **证据边界**：同一张合约缺可核验 `quote_as_of` 时，不能从价格或工具获取时间推出报价新鲜，不能声称报价合格或可执行。报价合格不等于执行许可；时间、财报与 R6 必须分别核对；R6 未知或触发时只报告事实，不展示合约或行动措辞
- **Regime / Breakout**：只能增加支持或反对证据，不能单独禁止 0DTE、授权开仓或绕过流动性门禁
- **无频次配额**：不使用固定每周 0DTE 次数，也不恢复旧 1-3DTE 禁令或 4-7DTE 隔夜车道
- **禁用词**：加油 / 稳赚 / 必涨 / 相信 / emoji
- **必含章节**：counter_evidence（≥1 条）、journal_context

## Output rendering

使用 `templates/option_decision.md.j2`。字段：

```
{
  "symbol": "NVDA",
  "decision": "review_candidate | wait_for_evidence | skip_due_to_friction | shadow_only",
  "entry_plan": "…",
  "exit_plan": "…",
  "breakout_context": "Q1…Q5 逐条",
  "counter_evidence": ["…", "…"],
  "journal_context": "过去 14 天 DTE 分布 / 盈亏 / Reality Test（无固定周配额）",
  "risks": ["…"]
}
```

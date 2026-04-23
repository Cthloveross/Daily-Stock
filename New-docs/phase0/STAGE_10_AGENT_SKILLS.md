# Stage 10 · Agent Skills（option_trader / leap_explorer / trend_follower）

> 状态：✅ 完成于 2026-04-20
> 前置：Stage 1（OCC + 期权链）+ Stage 3/4（Regime）+ Stage 5/6（Breakout）+ Stage 2（Journal）
> 产出：3 个 strategies YAML + 3 个 `.claude/skills/*/SKILL.md` + 3 个 Jinja 输出模板 + 4 个 agent tools + 8 个单测

---

## 做了什么

### 1. 3 个 strategy YAML（[strategies/](../../strategies/)）

- `option_trader.yaml` — 美股期权短期（0-90 DTE）。硬规则：Regime < 55 → 不开仓；Q1-Q5 fail → 标明原因；禁用词（加油 / 稳赚 / 相信 / emoji）
- `leap_explorer.yaml` — DTE ≥ 270、Delta 0.70-0.85。硬规则：必列 cost / leverage vs 正股 / annualised theta；IV Rank > 70 警告
- `trend_follower.yaml` — 周/月线级别。硬规则：必须分 2-3 批建仓；退出只看 weekly close < 40W MA

### 2. Claude Skill bundles（[.claude/skills/](../../.claude/skills/)）

每个 skill 一份 `SKILL.md`，含：
- When to use（触发场景）
- Required tool chain（调用顺序）
- Hard rules
- Output rendering（字段契约）

Claude 系统已自动把 `option_trader / leap_explorer / trend_follower` 索引为用户可调用的 skill。

### 3. 3 个 Jinja 模板（[templates/](../../templates/)）

- `option_decision.md.j2` — decision / entry-exit plan / breakout_context / counter_evidence / journal_context / risks
- `leap_proposal.md.j2` — thesis / candidates 列表 / disqualifier / execution_plan / reality_check
- `trend_plan.md.j2` — thesis / entry_plan_staged（2-3 批）/ exit_rules / comparison_vs_short_options / review_checkpoints

### 4. 4 个 Agent tools（[src/agent/tools/](../../src/agent/tools/)）

| Tool | 作用 |
|--|--|
| `get_regime_score_tool` | 读今天/指定日的 RegimeScore 行 |
| `check_breakout_tool` | 对显式 signal 跑 Q1-Q5（Phase 0 不依赖 live bars） |
| `get_option_chain_tool` | yfinance 期权链 + LEAP 候选筛选 |
| `get_journal_snapshot_tool` | 窗口 trades / win rate / 本周 0DTE 数 / phase / reality test |

每个都是 plain callable，返回 JSON-safe dict，方便 Agent orchestrator 直接塞到 LLM context。

### 5. 8 个单测（[src/agent/tools/tests/test_phase0_tools.py](../../src/agent/tools/tests/test_phase0_tools.py)）

涵盖：Regime 空/有 / Breakout Q1 拒绝 / Q1-Q5 全过 / Journal 空 + 0DTE 统计 / 期权链 list 返回 / 期权链 empty quotes。

**累计 179 backend passed**。

---

## 怎么验证

```bash
python -m pytest src/agent/tools/tests -v

# 用 Claude Skill 入口直接调（登录 Claude 或 CLI）：
# /option_trader 帮我看 NVDA 今天能买 call 吗
# /leap_explorer NVDA
# /trend_follower NVDA
```

验证要点：
- 输出应包含 Regime Score 数字、Q1-Q5 过滤链路、≥1 条 counter_evidence
- 不得出现 "加油 / 相信 / 稳赚 / emoji"
- LEAP skill 输出 Delta 在 [0.70, 0.85] 范围
- Trend skill 必须分 2-3 批

---

## 留了什么坑 / 显式延后

- **未接入原 repo 的 Agent orchestrator**：新 strategy YAML 放在 `strategies/` 下，理论上被原项目的 skill_loader 自动发现。但 Phase 0 没写"到 orchestrator 的真实桥"（比如 `src.agent.orchestrator.context_builder` 的 phase 注入），Phase 1 实际跑 Agent 对话时再补。
- **AGENT_SKILLS env** 未改：用户若希望 default-active，可在 `.env` 加 `AGENT_SKILLS=option_trader,leap_explorer,trend_follower,bull_trend`。Phase 0 暂留选择权给用户。
- **IV Rank gate** 在 LEAP prompt 写了但 tool 层没强制塞进 context。Phase 1 补一个 `get_iv_rank_tool`，或让 LLM 自己调 `compute_iv_rank`。
- **check_breakout_tool 不提供 live bars**：Phase 0 Agent 只能对用户描述的 signal 做 Q1-Q5。Phase 1 接 Alpaca premarket 之后 orchestrator 可以自动抓 bars 再跑 detector。
- **Prompt 回归测试** 用 dry-run snapshot 未实装：Phase 1 Agent 调 LLM 实际产出后再加 canned test。

---

## 下一步

Stage 11：Folder watcher + bot 命令（自动化闭环）。

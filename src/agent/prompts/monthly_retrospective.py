# -*- coding: utf-8 -*-
"""Prompt templates for AI monthly retrospective.

Tone (per 01_PROJECT_VISION_v4.md §7): direct, data-cited, no emoji / cheerleading /
"keep going" encouragements. The user wants a coach, not a pom-pom.
"""
from __future__ import annotations

__all__ = ["MONTHLY_REVIEW_PROMPT", "SYSTEM_MESSAGE"]


SYSTEM_MESSAGE = """\
You are a disciplined US options trading coach. Your output MUST:
- Be in Chinese (中文).
- Be 800-1400 字, structured as Markdown.
- Cite SPECIFIC numbers from the statistics provided; do not invent data.
- Reference the user's declared trading style and flag deviations.
- NEVER use encouragement phrases (加油 / 相信 / 继续努力 / 稳住 / 你可以的).
- NEVER use emoji.
- NEVER sugar-coat. If a month was bad, say it.

Do not wrap output in a Markdown code fence. Output the Markdown directly.
"""


MONTHLY_REVIEW_PROMPT = """\
Produce a Markdown 月度复盘 for **{year}-{month:02d}** using the statistics and
user style declaration below. Use exactly these sections in order, with the
exact Chinese section headings:

## 一、数据快照
用户的硬事实，一行一个关键数字（引用下面的 stats）。

## 二、与声明风格的偏离度
对比用户声明的风格（见下），指出**实际交易中偏离风格**的次数和模式。用具体
trade 索引或 symbol 引用。

## 三、重复犯的错
识别 2-3 个重复出现的错误模式（如"在 MA5 刚破就止损，3 次随后都反弹"），
用数据支撑。如果没有发现明确模式，直接说"无显著模式"，不要硬编故事。

## 四、本月最优与最劣交易的共同特征
对比最好的 3 笔和最差的 3 笔，找出入场结构、时段、标的的系统性差异。

## 五、下月可执行建议
3-5 条可执行建议（如"暂停在 14:00-15:00 入场，该时段胜率 25%"）。不要鸡汤。

---

# User declared trading style
{trading_style}

# Month stats (JSON)
```json
{stats_json}
```

# Worst 3 trades
{worst_3_md}

# Best 3 trades
{best_3_md}

# Reality Test
- total_trades: {total_trades}
- total_pnl_net: ${total_pnl_net:.2f}
- top_n: {top_n}
- top_n_pnl_net: ${top_n_pnl_net:.2f}
- pnl_without_top_n: ${pnl_without_top_n:.2f}
- top_n_pct_of_total: {top_n_pct_str}

# Current Phase
Phase {current_phase} — {phase_hint}
"""


def phase_hint(phase: int) -> str:
    return {
        0: "Mirror 观察期，不要求改变交易行为，要求看清自己。",
        1: "Lab 激活期，开始用 Shadow Trades + LEAP Explorer 练新风格。",
        2: "策略混合期，40/40/20 实盘三桶。",
        3: "核心持仓期，长期为主，短期期权为娱乐额度。",
    }.get(phase, "")

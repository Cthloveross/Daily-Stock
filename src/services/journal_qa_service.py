# -*- coding: utf-8 -*-
"""
Journal Q&A service
===================

Single-turn LLM Q&A over the user's recent paired trades. The "big premise"
is a user-authored trading framework passed in by the caller (stored in
localStorage on the frontend). The service aggregates the user's trade
statistics, asks the LLM to evaluate alignment/deviation from the framework,
and returns Chinese Markdown.

Deliberately NOT using the agent executor / tool-use loop — we want a fast
single LLM call that stays deterministic and cheap. No retries on LLM
failure: callers surface the error so the user knows it didn't complete.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Tuple

from src.journal.analytics import (
    dte_bucket_win_rates,
    dte_distribution,
    reality_test,
    stats_by_style,
)

logger = logging.getLogger(__name__)

_MAX_TRADES_IN_PROMPT = 25  # cap individual-trade lines sent to LLM
_MAX_FRAMEWORK_CHARS = 10000


def _framework_hash(framework: str) -> str:
    return hashlib.sha256(framework.encode("utf-8")).hexdigest()[:16]


def _compact_trade_line(t: Dict[str, Any]) -> str:
    sym = t.get("underlying") or t.get("raw_symbol") or "?"
    pnl = t.get("pnl_net") or 0.0
    pnl_pct = t.get("pnl_pct")
    hold = t.get("hold_seconds") or 0
    return (
        f"- id={t.get('id')} {sym} {t.get('direction')} "
        f"{'opt' if t.get('is_option') else 'eq'} "
        f"style={t.get('trade_style') or '—'} "
        f"dte={t.get('dte_bucket') or '—'} "
        f"hold={int(hold / 60)}min "
        f"pnl=${pnl:+.2f}"
        + (f" ({pnl_pct:+.1f}%)" if pnl_pct is not None else "")
    )


def _build_stats_block(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise trades for the LLM prompt — must be JSON-serialisable."""
    by_style = stats_by_style(trades)
    by_dte = dte_bucket_win_rates(trades)
    dte_dist = dte_distribution(trades)
    rt = reality_test(trades, top_n=5)
    wins = [t for t in trades if (t.get("pnl_net") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl_net") or 0) < 0]
    return {
        "total_closed": len(trades),
        "total_pnl_net": round(sum(t.get("pnl_net") or 0 for t in trades), 2),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "avg_win": round(sum(t["pnl_net"] for t in wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(t["pnl_net"] for t in losses) / len(losses), 2)
        if losses
        else None,
        "by_style": by_style,
        "dte_distribution": dte_dist,
        "dte_bucket_win_rates": {
            k: {
                "count": v.get("count"),
                "win_rate": round(v.get("win_rate") or 0.0, 3),
                "avg_pnl_net": round(v.get("avg_pnl_net") or 0.0, 2),
                "sum_pnl_net": round(v.get("sum_pnl_net") or 0.0, 2),
            }
            for k, v in by_dte.items()
        },
        "reality_test": {
            "total_trades": rt["total_trades"],
            "total_pnl_net": round(rt["total_pnl_net"], 2),
            "top_n": rt["top_n"],
            "top_n_pnl_net": round(rt["top_n_pnl_net"], 2),
            "pnl_without_top_n": round(rt["pnl_without_top_n"], 2),
            "top_n_ids": rt["top_n_ids"],
            "median_pnl_net": round(rt["median_pnl_net"], 2)
            if rt["median_pnl_net"] is not None
            else None,
        },
    }


_SYSTEM_MSG = (
    "你是一位严谨的美股/期权交易教练。你的任务是根据用户自行定义的【交易框架】"
    "评估用户最近的交割单。禁止使用夸奖、emoji、加油鼓劲语气；"
    "不得说『做得很棒』『很不错』『继续加油』。"
    "直接、客观、数据驱动，必要时引用具体 trade id。"
    "所有输出必须为简体中文 Markdown。"
)


_PROMPT_TEMPLATE = """【交易框架 — 用户定义的大前提】
{framework}

【最近 {n_trades} 笔已平仓交割单汇总（JSON）】
{stats_json}

【代表性交割单（最多 {sample_cap} 条，按时间降序）】
{sample_lines}

【用户的问题】
{question}

请严格按以下结构用中文 Markdown 回答：

1. **直接回答**（≤3 句，对问题给出明确结论）
2. **与框架的对齐度**（逐条引用框架里的 rule，标注"对齐/偏离"，并列出具体 trade id 作为证据）
3. **模式与根因**（从 by_style / by_dte / reality_test 的数据里挖出 1-3 个可归因的模式）
4. **下一步建议**（1-3 条可执行动作，避免空话）

禁用词：做得好、很棒、继续加油、You got this、努力、加油。
"""


def _call_llm(prompt: str, system_msg: str = _SYSTEM_MSG) -> str:
    """Thin wrapper around the project-wide LLM entry point."""
    from src.analyzer import get_analyzer

    analyzer = get_analyzer()
    # Gemini 3 Flash 在 temperature<1.0 时可能空响应；和 news_digest 保持一致。
    full_prompt = f"{system_msg}\n\n{prompt}"
    text = analyzer.generate_text(full_prompt, max_tokens=1500, temperature=1.0)
    if not text or not text.strip():
        raise RuntimeError("LLM 返回了空响应，请稍后重试")
    return text.strip()


def generate_answer(
    framework: str,
    question: str,
    trades: List[Dict[str, Any]],
) -> Tuple[str, str]:
    """Return (answer_markdown, framework_hash). Raises on any LLM failure."""
    fw = (framework or "").strip()
    if not fw:
        raise ValueError("framework 不能为空：请先到 Framework tab 填写你的交易框架")
    if len(fw) > _MAX_FRAMEWORK_CHARS:
        raise ValueError(f"framework 长度超过 {_MAX_FRAMEWORK_CHARS} 字，请精简后再试")
    q = (question or "").strip()
    if not q:
        raise ValueError("question 不能为空")

    stats_block = _build_stats_block(trades)
    sample_cap = min(_MAX_TRADES_IN_PROMPT, len(trades))
    sample_lines = "\n".join(_compact_trade_line(t) for t in trades[:sample_cap]) or "（无已平仓交割单）"

    prompt = _PROMPT_TEMPLATE.format(
        framework=fw,
        n_trades=len(trades),
        stats_json=json.dumps(stats_block, ensure_ascii=False, indent=2),
        sample_cap=sample_cap,
        sample_lines=sample_lines,
        question=q,
    )

    logger.info("journal_qa: invoking LLM with %d trades in prompt", len(trades))
    answer = _call_llm(prompt)
    return answer, _framework_hash(fw)

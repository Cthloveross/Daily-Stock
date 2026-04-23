# -*- coding: utf-8 -*-
"""AI Monthly Retrospective: stats -> LLM -> Markdown -> journal_monthly_reviews.

Usage:
    python -m scripts.generate_monthly_review --month 2026-03
    python -m scripts.generate_monthly_review --month 2026-03 --dry-run   # no LLM, prints stats only
"""
from __future__ import annotations

import calendar
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Optional

from sqlalchemy import select

from src.agent.prompts.monthly_retrospective import (
    MONTHLY_REVIEW_PROMPT,
    SYSTEM_MESSAGE,
    phase_hint,
)
from src.journal.analytics import (
    dte_bucket_win_rates,
    dte_distribution,
    reality_test,
)
from src.journal.models import (
    JournalMonthlyReview,
    JournalPhaseState,
    JournalTrade,
)
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL, init_journal_schema
from src.storage import get_db

logger = logging.getLogger(__name__)

__all__ = ["compute_monthly_stats", "generate_review", "run"]

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"


@dataclass
class MonthlyInputs:
    year: int
    month: int
    trades: list[dict]


def _collect_month_trades(year: int, month: int, portfolio: str) -> list[dict]:
    init_journal_schema()
    db = get_db()
    start = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59)
    with db.session_scope() as session:
        rows = (
            session.execute(
                select(JournalTrade)
                .where(JournalTrade.portfolio_label == portfolio)
                .where(JournalTrade.entry_time >= start)
                .where(JournalTrade.entry_time <= end)
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "underlying": r.underlying,
                "raw_symbol": r.raw_symbol,
                "is_option": bool(r.is_option),
                "direction": r.direction,
                "status": r.status,
                "entry_time": r.entry_time,
                "exit_time": r.exit_time,
                "hold_seconds": r.hold_seconds,
                "dte_at_entry": r.dte_at_entry,
                "dte_bucket": r.dte_bucket,
                "pnl_net": r.pnl_net,
                "pnl_pct": r.pnl_pct,
                "trade_style": r.trade_style,
                "was_fake_breakout": r.was_fake_breakout,
            }
            for r in rows
        ]


def compute_monthly_stats(year: int, month: int, portfolio: str = DEFAULT_PORTFOLIO_LABEL) -> dict:
    """Pre-compute the numerical block that the LLM prompt consumes."""
    trades = _collect_month_trades(year, month, portfolio)
    closed = [t for t in trades if t["status"] == "closed" and t["pnl_net"] is not None]
    wins = [t for t in closed if t["pnl_net"] > 0]
    losses = [t for t in closed if t["pnl_net"] < 0]
    best_3 = sorted(closed, key=lambda t: t["pnl_net"], reverse=True)[:3]
    worst_3 = sorted(closed, key=lambda t: t["pnl_net"])[:3]

    by_style: dict[str, list[dict]] = {}
    for t in closed:
        by_style.setdefault(t.get("trade_style") or "unknown", []).append(t)

    style_stats = {
        style: {
            "count": len(items),
            "win_rate": sum(1 for x in items if x["pnl_net"] > 0) / len(items)
            if items
            else None,
            "avg_pnl_net": fmean(x["pnl_net"] for x in items) if items else None,
            "sum_pnl_net": sum(x["pnl_net"] for x in items),
        }
        for style, items in by_style.items()
    }

    stats = {
        "year_month": f"{year:04d}-{month:02d}",
        "total_trades": len(closed),
        "total_pnl_net": sum(t["pnl_net"] for t in closed),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "avg_win": fmean(t["pnl_net"] for t in wins) if wins else None,
        "avg_loss": fmean(t["pnl_net"] for t in losses) if losses else None,
        "profit_factor": (
            sum(t["pnl_net"] for t in wins) / abs(sum(t["pnl_net"] for t in losses))
            if losses
            else None
        ),
        "dte_distribution": dte_distribution(trades),
        "dte_bucket_win_rates": dte_bucket_win_rates(trades),
        "by_style": style_stats,
        "best_3_ids": [t["id"] for t in best_3],
        "worst_3_ids": [t["id"] for t in worst_3],
        "reality_test": reality_test(trades, top_n=5),
    }
    return stats


def _format_trade_row(t: dict) -> str:
    sym = t.get("raw_symbol") or t.get("underlying")
    pnl = t.get("pnl_net") or 0.0
    hold = t.get("hold_seconds") or 0
    return (
        f"- id={t['id']} {sym} {t['direction']} "
        f"pnl=${pnl:.2f} ({t.get('pnl_pct') or 0:+.1f}%) "
        f"hold={int(hold / 60)}min style={t.get('trade_style') or '—'}"
    )


def _best_worst_markdown(trades: list[dict], descending: bool) -> str:
    closed = [t for t in trades if t["status"] == "closed" and t["pnl_net"] is not None]
    top = sorted(closed, key=lambda t: t["pnl_net"], reverse=descending)[:3]
    return "\n".join(_format_trade_row(t) for t in top) if top else "（无）"


def _current_phase() -> int:
    db = get_db()
    with db.session_scope() as session:
        row = session.get(JournalPhaseState, 1)
        return int(row.phase) if row else 0


def generate_review(
    year: int,
    month: int,
    portfolio: str = DEFAULT_PORTFOLIO_LABEL,
    trading_style: Optional[str] = None,
    current_phase: Optional[int] = None,
    dry_run: bool = False,
) -> tuple[str, dict]:
    """Compute stats, call LLM, return (markdown, stats).

    When ``dry_run=True``, skips LLM and returns a stats-only placeholder.
    """
    stats = compute_monthly_stats(year, month, portfolio)
    trades = _collect_month_trades(year, month, portfolio)
    phase = current_phase if current_phase is not None else _current_phase()
    style = trading_style or ""

    rt = stats["reality_test"]
    top_n_pct_str = (
        f"{rt['top_n_pct_of_total']:.1f}%"
        if rt.get("top_n_pct_of_total") is not None
        else "—"
    )
    prompt = MONTHLY_REVIEW_PROMPT.format(
        year=year,
        month=month,
        trading_style=style or "（用户未填 PERSONAL_TRADING_STYLE）",
        stats_json=json.dumps(stats, ensure_ascii=False, indent=2, default=str),
        worst_3_md=_best_worst_markdown(trades, descending=False),
        best_3_md=_best_worst_markdown(trades, descending=True),
        total_trades=stats["total_trades"],
        total_pnl_net=stats["total_pnl_net"],
        top_n=rt["top_n"],
        top_n_pnl_net=rt["top_n_pnl_net"],
        pnl_without_top_n=rt["pnl_without_top_n"],
        top_n_pct_str=top_n_pct_str,
        current_phase=phase,
        phase_hint=phase_hint(phase),
    )

    if dry_run:
        body = (
            f"# [DRY RUN] {year}-{month:02d}\n\n"
            f"Prompt would be {len(prompt)} chars.\n\n"
            f"Stats:\n```json\n{json.dumps(stats, ensure_ascii=False, indent=2, default=str)}\n```\n"
        )
    else:
        body = _call_llm(prompt)

    wrapper = _render_wrapper(year, month, phase, stats, body)
    return wrapper, stats


def _call_llm(prompt: str) -> str:
    """Route through the repo LLMToolAdapter (Gemini primary, Claude fallback)."""
    try:
        from src.agent.llm_adapter import LLMToolAdapter
        from src.config import get_config
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM adapter unavailable: %s", exc)
        return "# [LLM unavailable]\n\n" + str(exc)
    adapter = LLMToolAdapter(get_config())
    messages = [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": prompt},
    ]
    resp = adapter.call_text(messages, max_tokens=4000, timeout=180)
    return (resp.content or "").strip()


def _render_wrapper(
    year: int, month: int, phase: int, stats: dict, ai_body: str
) -> str:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)), trim_blocks=True, lstrip_blocks=True
    )
    tpl = env.get_template("monthly_retrospective.md.j2")
    return tpl.render(
        year=year,
        month=month,
        current_phase=phase,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        total_trades=stats.get("total_trades", 0),
        total_pnl_net=stats.get("total_pnl_net", 0.0),
        ai_body=ai_body,
        stats_json=json.dumps(stats, ensure_ascii=False, indent=2, default=str),
    )


def run(year_month: str, portfolio: str = DEFAULT_PORTFOLIO_LABEL, dry_run: bool = False) -> dict:
    """End-to-end entry: generate + upsert into journal_monthly_reviews.

    Returns the DB row as dict (ym, phase, markdown length, bool created).
    """
    year, month = map(int, year_month.split("-", 1))
    style = ""
    try:
        from src.config import get_config

        cfg = get_config()
        style = getattr(cfg, "personal_trading_style", "") or ""
    except Exception:  # noqa: BLE001
        pass

    markdown, stats = generate_review(
        year, month, portfolio=portfolio, trading_style=style, dry_run=dry_run
    )
    init_journal_schema()
    db = get_db()
    ym = f"{year:04d}-{month:02d}"
    created = False
    with db.session_scope() as session:
        existing = (
            session.execute(
                select(JournalMonthlyReview).where(
                    JournalMonthlyReview.portfolio_label == portfolio,
                    JournalMonthlyReview.year_month == ym,
                )
            )
            .scalars()
            .first()
        )
        payload = {
            "portfolio_label": portfolio,
            "year_month": ym,
            "current_phase": _current_phase(),
            "stats_json": json.dumps(stats, ensure_ascii=False, default=str),
            "review_markdown": markdown,
        }
        if existing is None:
            session.add(JournalMonthlyReview(**payload))
            created = True
        else:
            existing.stats_json = payload["stats_json"]
            existing.review_markdown = payload["review_markdown"]
            existing.current_phase = payload["current_phase"]
    return {"year_month": ym, "markdown_length": len(markdown), "created": created}

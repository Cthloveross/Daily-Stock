# -*- coding: utf-8 -*-
"""Journal analytics: Reality Test, DTE distribution, Daily Health Check.

All functions are pure: they accept plain dicts / iterables and return plain
dicts. Storage layers (API, CLI) are responsible for fetching inputs and
persisting outputs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time
from statistics import fmean, median
from typing import Iterable, List, Optional

from src.journal.matcher import dte_bucket_of

__all__ = [
    "reality_test",
    "dte_distribution",
    "dte_bucket_win_rates",
    "daily_health_check",
    "stats_by_style",
]


_DTE_BUCKET_ORDER = ["0DTE", "1-3DTE", "4-7DTE", "8-30DTE", "30+DTE"]


def reality_test(trades: Iterable[dict], top_n: int = 5) -> dict:
    """Calculate the 'remove the Top-N trades, what's left?' statistic.

    This is the Phase 0 soul metric: it exposes the concentration of lucky
    wins and tells the user what their median-case performance actually is.

    ``trades`` accepts dicts with at least ``pnl_net`` and ``status``. Only
    ``status='closed'`` rows count.
    """
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None]
    if not closed:
        return {
            "total_trades": 0,
            "total_pnl_net": 0.0,
            "top_n": top_n,
            "top_n_pnl_net": 0.0,
            "top_n_ids": [],
            "pnl_without_top_n": 0.0,
            "top_n_pct_of_total": None,
            "median_pnl_net": None,
        }
    sorted_desc = sorted(closed, key=lambda t: t["pnl_net"], reverse=True)
    top_slice = sorted_desc[: max(0, top_n)]
    total_pnl = sum(t["pnl_net"] for t in closed)
    top_pnl = sum(t["pnl_net"] for t in top_slice)
    pnl_without_top = total_pnl - top_pnl

    # Median as a robust central-tendency read alongside the sum.
    pnls_sorted = sorted(t["pnl_net"] for t in closed)
    n = len(pnls_sorted)
    if n % 2 == 1:
        median = pnls_sorted[n // 2]
    else:
        median = (pnls_sorted[n // 2 - 1] + pnls_sorted[n // 2]) / 2.0

    pct_of_total = (top_pnl / total_pnl * 100.0) if total_pnl else None

    return {
        "total_trades": len(closed),
        "total_pnl_net": float(total_pnl),
        "top_n": top_n,
        "top_n_pnl_net": float(top_pnl),
        "top_n_ids": [t.get("id") for t in top_slice if t.get("id") is not None],
        "pnl_without_top_n": float(pnl_without_top),
        "top_n_pct_of_total": pct_of_total,
        "median_pnl_net": float(median),
    }


def dte_distribution(trades: Iterable[dict]) -> dict[str, int]:
    """Bucket counts by DTE at entry. Non-option trades go to 'equity'."""
    counts = {b: 0 for b in _DTE_BUCKET_ORDER}
    counts["equity"] = 0
    for t in trades:
        if not t.get("is_option"):
            counts["equity"] += 1
            continue
        bucket = t.get("dte_bucket") or dte_bucket_of(t.get("dte_at_entry"))
        if bucket in counts:
            counts[bucket] += 1
        else:
            counts.setdefault(bucket or "unknown", 0)
            counts[bucket or "unknown"] += 1
    return counts


def dte_bucket_win_rates(trades: Iterable[dict]) -> dict[str, dict]:
    """Win-rate + avg PnL grouped by DTE bucket (closed trades only)."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t.get("status") != "closed" or t.get("pnl_net") is None:
            continue
        if t.get("is_option"):
            bucket = t.get("dte_bucket") or dte_bucket_of(t.get("dte_at_entry")) or "unknown"
        else:
            bucket = "equity"
        buckets[bucket].append(t)
    out = {}
    for bucket, rows in buckets.items():
        wins = sum(1 for r in rows if r["pnl_net"] > 0)
        out[bucket] = {
            "count": len(rows),
            "wins": wins,
            "win_rate": (wins / len(rows)) if rows else None,
            "avg_pnl_net": (sum(r["pnl_net"] for r in rows) / len(rows)) if rows else None,
            "sum_pnl_net": sum(r["pnl_net"] for r in rows),
        }
    return out


def stats_by_style(trades: Iterable[dict]) -> List[dict]:
    """Per-trade_style aggregate stats for closed trades.

    Each bucket keeps count / win_rate / avg+sum pnl_net / median hold / avg pnl%.
    Buckets returned sorted by count desc (common styles first).
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t.get("status") != "closed" or t.get("pnl_net") is None:
            continue
        style = t.get("trade_style") or "unknown"
        buckets[style].append(t)

    out: List[dict] = []
    for style, items in buckets.items():
        wins = sum(1 for r in items if r["pnl_net"] > 0)
        holds = [r.get("hold_seconds") for r in items if r.get("hold_seconds") is not None]
        pcts = [r.get("pnl_pct") for r in items if r.get("pnl_pct") is not None]
        out.append(
            {
                "style": style,
                "count": len(items),
                "win_rate": (wins / len(items)) if items else 0.0,
                "avg_pnl_net": (fmean(r["pnl_net"] for r in items)) if items else 0.0,
                "sum_pnl_net": float(sum(r["pnl_net"] for r in items)),
                "median_hold_seconds": int(median(holds)) if holds else None,
                "avg_pnl_pct": float(fmean(pcts)) if pcts else None,
            }
        )
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def daily_health_check(
    orders_of_day: Iterable[dict],
    closed_trades_of_day: Iterable[dict],
    target_date: Optional[date] = None,
) -> dict:
    """Numerical health check for one trading day.

    ``orders_of_day`` are raw order rows (dicts). ``closed_trades_of_day`` are
    FIFO-matched trades whose ``exit_time`` falls on ``target_date``. Caller
    is responsible for filtering / passing both inputs.
    """
    orders = list(orders_of_day)
    trades_closed = list(closed_trades_of_day)

    total_orders = len(orders)
    orders_0dte = 0
    orders_1_3dte = 0
    orders_opening_hour = 0
    underlying_counter: Counter[str] = Counter()

    for o in orders:
        is_opt = bool(o.get("is_option"))
        if is_opt:
            expiry = o.get("expiry")
            fill = o.get("first_fill_time") or o.get("order_time")
            if isinstance(expiry, date) and isinstance(fill, datetime):
                dte = (expiry - fill.date()).days
                if dte <= 0:
                    orders_0dte += 1
                elif dte <= 3:
                    orders_1_3dte += 1
        fill_time = o.get("first_fill_time") or o.get("order_time")
        if isinstance(fill_time, datetime):
            # "Opening hour" = first US regular-session half-hour (09:30 <= t < 10:00 ET).
            # order_time is stored as naive UTC; the two UTC windows below cover EDT
            # and EST respectively so we stay DST-agnostic.
            utc_hhmm = fill_time.time()
            if (
                time(13, 30) <= utc_hhmm < time(14, 0)
                or time(14, 30) <= utc_hhmm < time(15, 0)
            ):
                orders_opening_hour += 1
        und = o.get("underlying")
        if und:
            underlying_counter[und] += 1

    if underlying_counter:
        top_und, top_count = underlying_counter.most_common(1)[0]
        top_und_pct = (top_count / total_orders * 100.0) if total_orders else 0.0
    else:
        top_und, top_und_pct = None, None

    pnl_estimate = sum(t.get("pnl_net") or 0.0 for t in trades_closed)

    return {
        "check_date": target_date,
        "total_orders": total_orders,
        "orders_0dte": orders_0dte,
        "orders_1_3dte": orders_1_3dte,
        "orders_opening_hour": orders_opening_hour,
        "top_underlying": top_und,
        "top_underlying_pct": top_und_pct,
        "warnings_json": [],  # Stage 5 populates
        "pnl_estimate": float(pnl_estimate),
        "regime_score": None,  # Stage 4 fills from regime_scores table
    }

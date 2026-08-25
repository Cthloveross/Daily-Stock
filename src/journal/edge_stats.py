# -*- coding: utf-8 -*-
"""Edge statistics: expectancy, sample-size, DTE buckets, fee drag, Kelly.

Calibrated from the 2026H1 forensics in
``New-docs/phase1/16_EDGE_FORENSICS_AND_REGIME_GATE.md``: the user's edge is
regime-conditional, so this module exposes the statistics that reveal where
(and on how much evidence) the edge actually lives.

All functions are pure: they accept plain dicts / iterables / floats and
return plain dicts. Storage layers (API, CLI) are responsible for fetching
inputs and persisting outputs. Fail-closed: insufficient or invalid input
yields ``None`` fields, never fabricated numbers.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import fmean, median, variance
from typing import Iterable, List, Optional, Tuple

__all__ = [
    "expectancy_stats",
    "required_sample_estimate",
    "dte_bucket_expectancy",
    "fee_drag",
    "trade_frequency",
    "kelly_estimate",
    "monthly_breakdown",
]

# z constants for the default one-sided alpha=0.05 / power=0.80 test.
_Z_ALPHA = 1.645
_Z_POWER = 0.84

_DEFAULT_DTE_EDGES: Tuple[Tuple[int, Optional[int]], ...] = (
    (0, 1),
    (2, 7),
    (8, 30),
    (31, None),
)


def _clean_pnls(pnls: Iterable[float]) -> List[float]:
    """Materialize a PnL iterable, dropping ``None`` entries."""
    return [float(p) for p in pnls if p is not None]


def _entry_date(value) -> Optional[date]:
    """Extract the calendar date from a datetime or ISO 'YYYY-MM-DD...' string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def expectancy_stats(pnls: Iterable[float], n_boot: int = 4000, seed: int = 42) -> dict:
    """Core expectancy read on a series of net PnLs.

    ``avg_loss`` is negative (or None when there are no losses);
    ``payoff_ratio = avg_win / abs(avg_loss)``. The 95% CI of the mean comes
    from a seeded bootstrap (``random.Random(seed)``, sorted-percentile
    2.5 / 97.5), so results are deterministic for a fixed seed.

    Fail-closed: with n < 2 every analytic field is None (only ``n`` is real).
    """
    vals = _clean_pnls(pnls)
    n = len(vals)
    if n < 2:
        return {
            "n": n,
            "mean": None,
            "median": None,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "payoff_ratio": None,
            "ci95_low": None,
            "ci95_high": None,
        }

    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    avg_win = fmean(wins) if wins else None
    avg_loss = fmean(losses) if losses else None
    payoff_ratio = (
        avg_win / abs(avg_loss) if avg_win is not None and avg_loss is not None else None
    )

    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    if n_boot >= 1:
        rng = random.Random(seed)
        boot_means = sorted(fmean(rng.choices(vals, k=n)) for _ in range(n_boot))
        lo_idx = int(math.floor(0.025 * n_boot))
        hi_idx = min(n_boot - 1, int(math.ceil(0.975 * n_boot)) - 1)
        ci_low = boot_means[lo_idx]
        ci_high = boot_means[hi_idx]

    return {
        "n": n,
        "mean": fmean(vals),
        "median": float(median(vals)),
        "win_rate": len(wins) / n,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "ci95_low": ci_low,
        "ci95_high": ci_high,
    }


def required_sample_estimate(pnls: Iterable[float], alpha: float = 0.05, power: float = 0.8) -> dict:
    """Sample size needed to establish mean PnL > 0 (one-sided, normal approx).

    ``n_needed = ceil(((z_alpha + z_power)**2 * variance) / mean**2)`` with
    z constants fixed at 1.645 / 0.84 — these correspond to the default
    ``alpha=0.05`` / ``power=0.8``; other values are accepted for signature
    clarity but do not remap the z constants (no inverse-normal table here).

    Fail-closed: if mean <= 0 or n_current < 10, ``n_needed`` and
    ``n_remaining`` are None — no extrapolating a losing or tiny sample.
    """
    vals = _clean_pnls(pnls)
    n_current = len(vals)
    mean = fmean(vals) if n_current >= 1 else None
    if n_current < 10 or mean is None or mean <= 0:
        return {"n_current": n_current, "mean": mean, "n_needed": None, "n_remaining": None}
    var = variance(vals)  # sample variance; n_current >= 10 guarantees n >= 2
    n_needed = int(math.ceil(((_Z_ALPHA + _Z_POWER) ** 2 * var) / (mean ** 2)))
    return {
        "n_current": n_current,
        "mean": mean,
        "n_needed": n_needed,
        "n_remaining": max(0, n_needed - n_current),
    }


def _edge_label(lo: int, hi: Optional[int]) -> str:
    return f"{lo}+" if hi is None else f"{lo}-{hi}"


def dte_bucket_expectancy(
    trades: Iterable[dict],
    bucket_edges: Tuple[Tuple[int, Optional[int]], ...] = _DEFAULT_DTE_EDGES,
) -> dict:
    """Expectancy grouped by DTE-at-entry buckets, plus 'stock' for dte=None.

    NOTE: the 0-1 / 2-7 split here intentionally differs from
    ``src.journal.matcher.dte_bucket_of`` (0DTE / 1-3DTE / 4-7DTE / ...):
    the 2026H1 forensics (doc 16) found the expectancy sign flip at exactly
    the 1-vs-2 DTE boundary, so this module keeps its own edges instead of
    reusing the matcher's display buckets.

    Rows use ``dte_at_entry`` (int or None) and ``pnl_net``; rows with
    ``pnl_net`` None are skipped. Every bucket label is always present in the
    output (empty buckets get n=0, mean/win_rate None).
    """
    labels = [_edge_label(lo, hi) for lo, hi in bucket_edges]
    grouped: dict = {label: [] for label in labels}
    grouped["stock"] = []
    for t in trades:
        pnl = t.get("pnl_net")
        if pnl is None:
            continue
        dte = t.get("dte_at_entry")
        if dte is None:
            grouped["stock"].append(float(pnl))
            continue
        # First bucket whose upper edge covers dte; out-of-range (negative)
        # dte clamps into the lowest bucket rather than being invented anew.
        label = labels[-1]
        for (lo, hi), lab in zip(bucket_edges, labels):
            if hi is None or dte <= hi:
                label = lab
                break
        grouped[label].append(float(pnl))

    out: dict = {}
    for label, rows in grouped.items():
        if rows:
            wins = sum(1 for v in rows if v > 0)
            out[label] = {
                "n": len(rows),
                "total": float(sum(rows)),
                "mean": fmean(rows),
                "win_rate": wins / len(rows),
            }
        else:
            out[label] = {"n": 0, "total": 0.0, "mean": None, "win_rate": None}
    return out


def fee_drag(trades: Iterable[dict]) -> dict:
    """Aggregate fee drag: how much of the gross edge the fees eat.

    Missing / None ``total_fee`` or ``pnl_gross`` count as 0.0 in the sums.
    ``fee_to_gross_ratio`` is None when total gross is ~0 (ratio undefined).
    """
    total_fees = 0.0
    total_gross = 0.0
    for t in trades:
        total_fees += float(t.get("total_fee") or 0.0)
        total_gross += float(t.get("pnl_gross") or 0.0)
    ratio = (total_fees / abs(total_gross)) if abs(total_gross) > 1e-9 else None
    return {
        "total_fees": total_fees,
        "total_gross": total_gross,
        "total_net": total_gross - total_fees,
        "fee_to_gross_ratio": ratio,
    }


def trade_frequency(trades: Iterable[dict]) -> dict:
    """Entry-frequency profile keyed off ``entry_time`` (datetime or ISO string).

    Only the date part is used. Rows without a parseable entry date are
    skipped. ``max_day`` is the ISO date with the most entries (earliest date
    wins ties, deterministically).
    """
    day_counts: Counter = Counter()
    for t in trades:
        d = _entry_date(t.get("entry_time"))
        if d is not None:
            day_counts[d.isoformat()] += 1
    if not day_counts:
        return {"n": 0, "active_days": 0, "avg_per_day": None, "max_day": None, "max_day_count": None}
    n = sum(day_counts.values())
    active_days = len(day_counts)
    max_count = max(day_counts.values())
    max_day = min(d for d, c in day_counts.items() if c == max_count)
    return {
        "n": n,
        "active_days": active_days,
        "avg_per_day": n / active_days,
        "max_day": max_day,
        "max_day_count": max_count,
    }


def kelly_estimate(pnls: Iterable[float]) -> dict:
    """Payoff-form Kelly fraction: ``f* = p - (1-p)/b``.

    ``p`` is the empirical win rate, ``b`` the payoff ratio
    (avg_win / abs(avg_loss)).

    Caveat: this is a per-unit-risk fraction derived from the empirical
    win/loss profile, NOT an account allocation recommendation — both inputs
    are heavily regime-dependent (doc 16), so treat it as a sizing ceiling
    for discussion, not a target. Fail-closed: with n < 30, or when b is
    undefined or <= 0, every field is None — Kelly on tiny samples is
    dangerous.
    """
    none_out = {"f_star": None, "half_kelly": None, "quarter_kelly": None}
    vals = _clean_pnls(pnls)
    if len(vals) < 30:
        return none_out
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    if not wins or not losses:
        return none_out
    b = fmean(wins) / abs(fmean(losses))
    if b <= 0:
        return none_out
    p = len(wins) / len(vals)
    f_star = p - (1 - p) / b
    return {"f_star": f_star, "half_kelly": f_star / 2.0, "quarter_kelly": f_star / 4.0}


def monthly_breakdown(trades: Iterable[dict]) -> dict:
    """Per-month aggregates keyed 'YYYY-MM' (sorted ascending by month).

    Rows use ``entry_time`` (datetime or ISO string), ``pnl_net`` and
    ``total_fee``; rows without a parseable entry date or with ``pnl_net``
    None are skipped. Missing fees count as 0.0.
    """
    buckets: defaultdict = defaultdict(list)
    for t in trades:
        d = _entry_date(t.get("entry_time"))
        if d is None or t.get("pnl_net") is None:
            continue
        buckets[f"{d.year:04d}-{d.month:02d}"].append(t)

    out: dict = {}
    for month in sorted(buckets):
        rows = buckets[month]
        pnls = [float(r["pnl_net"]) for r in rows]
        wins = sum(1 for v in pnls if v > 0)
        out[month] = {
            "n": len(rows),
            "total_net": float(sum(pnls)),
            "mean": fmean(pnls),
            "win_rate": wins / len(rows),
            "total_fees": float(sum(float(r.get("total_fee") or 0.0) for r in rows)),
        }
    return out

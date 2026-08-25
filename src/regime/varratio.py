# -*- coding: utf-8 -*-
"""Variance-ratio regime shape: trending vs mean-reverting vs random walk.

Formalizes the "trending vs mean-reverting market" question via the
Lo & MacKinlay (1988) variance ratio test, and exposes the derived
position-scaling / DTE-gate rules described in
``New-docs/phase1/16_EDGE_FORENSICS_AND_REGIME_GATE.md``.

All functions are pure: they accept plain sequences / floats and return
plain dicts (or None). No storage, no network. Callers (scorers, API,
CLI) are responsible for fetching return series and persisting outputs.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Optional, Sequence

__all__ = [
    "tsmom_mm_gate",
    "variance_ratio",
    "classify_regime_shape",
    "position_scale",
    "dte_gate",
]


def variance_ratio(returns: Sequence[float], q: int) -> Optional[dict]:
    """Lo-MacKinlay VR(q) with overlapping q-period sums.

    Uses unbiased variance estimators and the Lo-MacKinlay small-sample
    bias correction for the q-period variance. Returns a dict::

        {"q": q, "n": n, "vr": vr, "z": z, "z_star": z_star}

    where ``z`` is the homoskedastic test statistic and ``z_star`` the
    heteroskedasticity-robust one, following Campbell, Lo & MacKinlay
    (1997) eq. 2.4.37 / 2.4.43: sqrt(n)*(VR-1) is asymptotically
    N(0, theta). Under a random walk vr ~ 1; vr > 1 suggests positive
    autocorrelation (trending), vr < 1 mean reversion.

    Fail closed: returns None when ``q < 2``, when the sample is too
    short (``n < max(3*q, 30)``), or when the series has zero variance.
    ``z_star`` alone may be None when its denominator degenerates.
    """
    rs = [float(r) for r in returns]
    n = len(rs)
    if q < 2 or n < max(3 * q, 30):
        return None

    mu = sum(rs) / n
    dev = [r - mu for r in rs]
    var1 = sum(d * d for d in dev) / (n - 1)
    # (Near-)constant series: VR undefined. The relative check catches
    # float residue when all returns are equal but nonzero.
    mean_sq = sum(r * r for r in rs) / n
    if var1 <= 0.0 or var1 <= 1e-15 * mean_sq:
        return None

    # Overlapping q-period sums, with Lo-MacKinlay bias correction m.
    # m already carries the factor q, so varq is a per-period variance
    # and VR = varq / var1 (not varq / (q*var1)).
    num_sums = n - q + 1
    m = q * num_sums * (1.0 - q / n)
    varq_num = 0.0
    window = sum(rs[0:q])
    for t in range(num_sums):
        if t > 0:
            window += rs[t + q - 1] - rs[t - 1]
        diff = window - q * mu
        varq_num += diff * diff
    varq = varq_num / m
    vr = varq / var1

    # Homoskedastic z (asymptotic variance phi).
    phi = 2.0 * (2 * q - 1) * (q - 1) / (3.0 * q * n)
    z = (vr - 1.0) / math.sqrt(phi)

    # Heteroskedasticity-robust z* (White-style delta_j weights):
    # delta_j = n * sum(a_t * a_{t-j}) / (sum a_t)^2, a_t = (r_t - mu)^2,
    # and sqrt(n)*(vr - 1)/sqrt(theta) ~ N(0, 1) asymptotically.
    a = [d * d for d in dev]
    sum_a = sum(a)
    theta = 0.0
    for j in range(1, q):
        cross = sum(a[t] * a[t - j] for t in range(j, n))
        delta_j = n * cross / (sum_a * sum_a)
        weight = 2.0 * (q - j) / q
        theta += weight * weight * delta_j
    z_star = math.sqrt(n) * (vr - 1.0) / math.sqrt(theta) if theta > 0.0 else None

    return {"q": q, "n": n, "vr": vr, "z": z, "z_star": z_star}


def classify_regime_shape(
    returns: Sequence[float],
    qs: Sequence[int] = (2, 5, 10),
    z_threshold: float = 1.645,
) -> dict:
    """Majority vote over VR(q) statistics into a regime-shape label.

    For each q the vote uses ``z_star`` when available, else ``z``:
    significantly above +threshold => 'trending', below -threshold =>
    'mean_reverting', otherwise 'random_walk'. Ties (no strict winner)
    resolve conservatively to 'random_walk'.

    Returns ``{"label": ..., "details": [per-q dicts], "n": n}``.
    Fail closed: with fewer than 2 usable VR results the label is
    'insufficient_data'.
    """
    n = len(returns)
    details: list[dict] = []
    votes: list[str] = []
    for q in qs:
        res = variance_ratio(returns, q)
        if res is None:
            details.append({"q": q, "n": n, "vr": None, "z": None, "z_star": None, "vote": None})
            continue
        z_val = res["z_star"] if res["z_star"] is not None else res["z"]
        if z_val > z_threshold:
            vote = "trending"
        elif z_val < -z_threshold:
            vote = "mean_reverting"
        else:
            vote = "random_walk"
        entry = dict(res)
        entry["vote"] = vote
        details.append(entry)
        votes.append(vote)

    if len(votes) < 2:
        return {"label": "insufficient_data", "details": details, "n": n}

    counts = Counter(votes).most_common()
    if len(counts) > 1 and counts[0][1] == counts[1][1]:
        label = "random_walk"  # tie: conservative default
    else:
        label = counts[0][0]
    return {"label": label, "details": details, "n": n}


def position_scale(
    recent_realized_var: float,
    baseline_realized_var: float,
    floor: float = 0.25,
    cap: float = 1.0,
) -> Optional[float]:
    """Inverse-variance position scaling, clipped to [floor, cap].

    Volatility-managed sizing per Moreira & Muir (2017, JF): scale
    exposure by ``baseline_var / recent_var`` so positions shrink when
    recent realized variance spikes above its baseline.

    Fail closed: both variances must be > 0, else None.
    """
    if recent_realized_var is None or baseline_realized_var is None:
        return None
    if recent_realized_var <= 0.0 or baseline_realized_var <= 0.0:
        return None
    scale = baseline_realized_var / recent_realized_var
    return float(min(cap, max(floor, scale)))


def dte_gate(
    shape_label: str,
    regime_score: Optional[int],
    score_threshold: int = 40,
) -> dict:
    """DTE bucket gate from regime shape + regime score.

    0-1DTE is allowed ONLY when the market shape is 'trending' AND the
    regime score is present and >= ``score_threshold``. Any missing or
    off-regime input fails closed to False; the default bucket stays
    '2-7' DTE regardless.

    The 40-point threshold is provisional and will be recalibrated once
    the 2026-08 journal data lands (doc 16 §5 E-3).
    """
    allow = (
        shape_label == "trending"
        and regime_score is not None
        and regime_score >= score_threshold
    )
    return {"allow_0_1dte": bool(allow), "default_bucket": "2-7"}


def tsmom_mm_gate(
    mom_20d_market: Optional[float],
    mom_20d_sector: Optional[float],
    mm_scale: Optional[float],
    mom_threshold: float = 0.0,
    mm_threshold: float = 0.5,
) -> dict:
    """Calibrated 0-1DTE gate: 20d time-series momentum + inverse-variance scale.

    Combination selected in the E-4 backtest (doc 16 §E-4, calibrated
    2026-08-20): train Apr-Jun 2026 kept +$82,256 / blocked -$10,232 of
    real 0-1DTE P&L; out-of-sample Jul-Aug blocked -$94,170 of -$117,162
    in losses and flagged all five worst days (incl. 2026-08-03) red.

    Components and their literature basis:
    - ``mom_20d_market`` / ``mom_20d_sector``: trailing 20-session simple
      returns of the market proxy (QQQ) and sector proxy (SOXX) — time-series
      momentum per Moskowitz, Ooi & Pedersen (2012, JFE).
    - ``mm_scale``: output of :func:`position_scale` — inverse-variance
      scaling per Moreira & Muir (2017, JF).

    The two families catch different failure modes (2026-08-03 had
    mm_scale 0.54 — passing — while sector momentum was -10.9%): both must
    pass. Any missing input fails closed. The default bucket stays '2-7'
    regardless (Coval & Shumway 2001 baseline: long options are
    unconditionally negative-EV; the gate only searches for the rare
    favourable state and is not an invitation to size up).
    """
    reasons: list[str] = []
    if mom_20d_market is None or mom_20d_market <= mom_threshold:
        reasons.append("market_momentum")
    if mom_20d_sector is None or mom_20d_sector <= mom_threshold:
        reasons.append("sector_momentum")
    if mm_scale is None or mm_scale < mm_threshold:
        reasons.append("volatility_scale")
    return {
        "allow_0_1dte": not reasons,
        "default_bucket": "2-7",
        "blocked_by": reasons,
    }

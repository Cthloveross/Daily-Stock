# -*- coding: utf-8 -*-
"""Edge-panel assembly: regime gate + edge forensics + discipline dials.

E-5 of ``New-docs/phase1/16_EDGE_FORENSICS_AND_REGIME_GATE.md``: one payload
that the ``/journal`` web card renders, combining

* ``gate``  — the calibrated 0-1DTE gate (doc 16 §5.1): QQQ / SOXX trailing
  20-session momentum (info through *yesterday*, never today's live bar)
  plus the Moreira-Muir inverse-variance scale, fed into
  :func:`src.regime.varratio.tsmom_mm_gate`;
* ``edge``  — DTE-bucket expectancy, overall expectancy CI and the H1
  evidence read (2-7DTE daily P&L t-stat vs the Harvey-Liu-Zhu t >= 3.0
  hurdle) from :mod:`src.journal.edge_stats`;
* ``discipline`` — R2 frequency cap dial and the R3 fee dashboard for the
  current calendar month.

Fail-closed rules (contract, fixed):

* if market data cannot be fetched — network failure, missing symbols, or a
  history too short to compute even the 20d momentum — the gate is
  ``allow_0_1dte=False``, ``blocked_by=["market_data_unavailable"]``,
  ``data_status="unavailable"`` and every feature is ``None``.  Features are
  never fabricated;
* if momentum is computable but the variance baseline history is short,
  ``mm_scale`` is ``None`` and :func:`tsmom_mm_gate` fails closed on the
  ``volatility_scale`` component (``data_status`` stays ``"ok"`` because the
  momentum features are real);
* journal statistics follow the fail-closed semantics of ``edge_stats``
  (``None`` fields on insufficient samples, never invented numbers).

This module performs no writes and never places orders (HANDOFF §2.3 red
line).  The only network access is the daily-close fetch, wrapped in an
in-process TTL cache (>= 30 min) so the endpoint does not hammer yfinance.

Timezone note: v1 ``journal_trades`` rows carry ET wall-clock timestamps
(``src.journal.brokers.moomoo_us._parse_trade_time``); naive datetimes are
therefore interpreted as America/New_York, aware ones are converted.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import fmean, median, stdev, variance
from typing import Callable, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from src.journal.edge_stats import (
    dte_bucket_expectancy,
    expectancy_stats,
    fee_drag,
    required_sample_estimate,
)
from src.regime.varratio import position_scale, tsmom_mm_gate

__all__ = [
    "build_edge_panel",
    "compute_gate",
]

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

#: Harvey, Liu & Zhu (2016, RFS) multiple-testing hurdle for a "new factor".
HLZ_HURDLE = 3.0
#: R2 frequency hard cap (doc 16 §4): entries per day before the dial turns red.
DAILY_CAP = 8

# A 20-trading-day simple return needs 21 closes.
_MOMENTUM_SESSIONS = 21
# Baseline realized variance = median of the rolling 20d variances over the
# trailing 250 sessions -> 250 windows x 20 returns -> 269 returns -> 270
# closes.  Shorter history fails closed (mm_scale=None), never extrapolates.
_BASELINE_WINDOWS = 250
_VAR_WINDOW = 20
_BASELINE_MIN_CLOSES = _BASELINE_WINDOWS + _VAR_WINDOW  # 270
_QQQ_FETCH_SESSIONS = 280
_SOXX_FETCH_SESSIONS = 30
# Minimum number of aggregated trading days before a t-stat is reported.
_MIN_T_STAT_DAYS = 10

_MARKET_CACHE_TTL_SECONDS = 1800.0  # >= 30 min per task contract
_market_cache: dict[tuple[str, int], tuple[float, list[float]]] = {}
_market_cache_lock = threading.Lock()


# --- market data -------------------------------------------------------------


def _fetch_closes_yfinance(symbol: str, sessions: int) -> list[float]:
    """Trailing daily closes with information through *yesterday* (ET).

    ``yfinance.history`` treats ``end`` as exclusive, so passing today's ET
    date guarantees the live (partial) bar never leaks into the features —
    the same "info through yesterday" protocol used for the E-4 calibration.
    """
    import yfinance as yf  # lazy: keep module import cheap and test-friendly

    today_et = datetime.now(ET).date()
    start = today_et - timedelta(days=int(sessions * 1.8) + 10)
    hist = yf.Ticker(symbol).history(
        start=start.isoformat(),
        end=today_et.isoformat(),
        interval="1d",
        auto_adjust=True,
    )
    closes = [float(v) for v in hist["Close"].dropna().tolist()]
    return closes[-sessions:]


def _default_fetch_closes(symbol: str, sessions: int) -> list[float]:
    """TTL-cached close fetch so the endpoint does not hammer the network."""
    key = (symbol.upper(), int(sessions))
    now = time.monotonic()
    with _market_cache_lock:
        hit = _market_cache.get(key)
        if hit is not None and now - hit[0] < _MARKET_CACHE_TTL_SECONDS:
            return list(hit[1])
    closes = _fetch_closes_yfinance(symbol, sessions)
    if closes:
        # Never cache an empty read: a transient failure must retry, not
        # pin the gate closed for the whole TTL.
        with _market_cache_lock:
            _market_cache[key] = (now, list(closes))
    return list(closes)


def _reset_market_cache() -> None:
    """Test hook: drop every cached close series."""
    with _market_cache_lock:
        _market_cache.clear()


# --- gate --------------------------------------------------------------------


def _failed_gate() -> dict:
    """Contract-mandated fail-closed gate shape."""
    return {
        "allow_0_1dte": False,
        "default_bucket": "2-7",
        "blocked_by": ["market_data_unavailable"],
        "features": {"mom_q_20d": None, "mom_s_20d": None, "mm_scale": None},
        "data_status": "unavailable",
    }


def _trailing_return(closes: Sequence[float], sessions: int = _MOMENTUM_SESSIONS) -> Optional[float]:
    """Simple trailing (sessions-1)-day return; None on short/degenerate input."""
    if len(closes) < sessions:
        return None
    base = closes[-sessions]
    if base <= 0.0:
        return None
    return closes[-1] / base - 1.0


def _mm_scale_from_closes(closes: Sequence[float]) -> Optional[float]:
    """Moreira-Muir inverse-variance scale from a daily close series.

    Recent 20d realized variance of daily returns vs the median 20d variance
    over the trailing 250 sessions, clipped to [0.25, 1.0] by
    :func:`position_scale`.  Fails closed (None) when the history is shorter
    than 270 closes or the series is degenerate.
    """
    if len(closes) < _BASELINE_MIN_CLOSES:
        return None
    returns: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev <= 0.0:
            return None
        returns.append(cur / prev - 1.0)
    n = len(returns)
    if n < _VAR_WINDOW + _BASELINE_WINDOWS - 1:
        return None
    recent = variance(returns[-_VAR_WINDOW:])
    baseline_vars = [
        variance(returns[end - _VAR_WINDOW:end])
        for end in range(n - _BASELINE_WINDOWS + 1, n + 1)
    ]
    baseline = median(baseline_vars)
    return position_scale(recent, baseline)


def compute_gate(
    fetch_closes: Optional[Callable[[str, int], List[float]]] = None,
) -> dict:
    """Assemble the 0-1DTE gate block from live (cached) market data.

    ``fetch_closes(symbol, sessions)`` must return trailing daily closes with
    info through yesterday; the default is the TTL-cached yfinance path.
    ALL market-data access is wrapped: any exception, empty series, or a
    history too short for the 20d momentum returns the fail-closed shape.
    """
    fetch = fetch_closes if fetch_closes is not None else _default_fetch_closes
    try:
        qqq = [float(v) for v in fetch("QQQ", _QQQ_FETCH_SESSIONS)]
        soxx = [float(v) for v in fetch("SOXX", _SOXX_FETCH_SESSIONS)]
    except Exception as exc:  # noqa: BLE001 — any failure fails closed
        logger.warning("edge-panel market data fetch failed: %s", exc)
        return _failed_gate()

    mom_q = _trailing_return(qqq)
    mom_s = _trailing_return(soxx)
    if mom_q is None or mom_s is None:
        # Not even the momentum features are computable -> the market data is
        # effectively unavailable; never fabricate features.
        return _failed_gate()

    mm = _mm_scale_from_closes(qqq)
    gate = tsmom_mm_gate(mom_q, mom_s, mm)
    gate["features"] = {"mom_q_20d": mom_q, "mom_s_20d": mom_s, "mm_scale": mm}
    gate["data_status"] = "ok"
    return gate


# --- journal helpers ---------------------------------------------------------


def _et_date(value) -> Optional[date]:
    """Calendar date in America/New_York; naive datetimes are assumed ET."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ET).date()
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(ET).date()
        return parsed.date()
    return None


def _h1_t_stat(day_pnls: Sequence[float]) -> Optional[float]:
    """t = mean / (stdev / sqrt(n_days)); None below 10 days or zero spread."""
    n = len(day_pnls)
    if n < _MIN_T_STAT_DAYS:
        return None
    sd = stdev(day_pnls)
    if sd <= 0.0:
        return None
    return fmean(day_pnls) / (sd / math.sqrt(n))


# --- payload assembly --------------------------------------------------------


def build_edge_panel(
    trades: Iterable[dict],
    *,
    fetch_closes: Optional[Callable[[str, int], List[float]]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Assemble the full edge-panel payload (contract of doc 16 E-5).

    ``trades`` are journal_trades rows as plain dicts (the API layer's
    ``_load_trades`` shape): ``status``, ``pnl_net``, ``pnl_gross``,
    ``total_fee``, ``dte_at_entry``, ``entry_time``.  Only closed trades
    with a known ``pnl_net`` enter any P&L statistic; ``trades_today``
    counts every entry (open or closed) dated today in ET.
    """
    if now is None:
        now_et = datetime.now(ET)
    elif now.tzinfo is not None:
        now_et = now.astimezone(ET)
    else:
        now_et = now.replace(tzinfo=ET)

    rows = list(trades)
    closed = [
        t for t in rows
        if t.get("status") == "closed" and t.get("pnl_net") is not None
    ]
    pnls = [float(t["pnl_net"]) for t in closed]

    buckets = dte_bucket_expectancy(closed)
    exp = expectancy_stats(pnls)
    expectancy = {
        "n": exp["n"],
        "mean": exp["mean"],
        "ci95_low": exp["ci95_low"],
        "ci95_high": exp["ci95_high"],
    }

    # H1 evidence: 2-7DTE trades aggregated into daily P&L sums (ET date).
    h1_trades = [
        t for t in closed
        if t.get("dte_at_entry") is not None and 2 <= t["dte_at_entry"] <= 7
    ]
    day_sums: defaultdict = defaultdict(float)
    for t in h1_trades:
        d = _et_date(t.get("entry_time"))
        if d is not None:
            day_sums[d] += float(t["pnl_net"])
    t_stat = _h1_t_stat(list(day_sums.values()))
    req = required_sample_estimate([float(t["pnl_net"]) for t in h1_trades])
    status = "significant" if (t_stat is not None and t_stat >= HLZ_HURDLE) else "insufficient"
    evidence = {
        "h1_t_stat": t_stat,
        "hlz_hurdle": HLZ_HURDLE,
        "status": status,
        "n_needed": req["n_needed"],
        "n_remaining": req["n_remaining"],
    }

    today = now_et.date()
    trades_today = sum(1 for t in rows if _et_date(t.get("entry_time")) == today)
    month_rows = []
    for t in closed:
        d = _et_date(t.get("entry_time"))
        if d is not None and d.year == today.year and d.month == today.month:
            month_rows.append(t)
    drag = fee_drag(month_rows)
    discipline = {
        "trades_today": trades_today,
        "daily_cap": DAILY_CAP,
        "month_fees": drag["total_fees"],
        "month_gross": drag["total_gross"],
        "fee_ratio": drag["fee_to_gross_ratio"],
    }

    return {
        "as_of": now_et.isoformat(),
        "gate": compute_gate(fetch_closes),
        "edge": {
            "buckets": buckets,
            "expectancy": expectancy,
            "evidence": evidence,
        },
        "discipline": discipline,
    }

# -*- coding: utf-8 -*-
"""ATM IV snapshot and IV-rank helpers.

IV Rank is the standard metric for "is current IV historically cheap or rich":
``(current_iv - min_52w) / (max_52w - min_52w)``. Without a long IV history we
fall back to historical volatility (HV) of the underlying as an approximation.

Live IV data is fetched from yfinance option chains, which expose ``impliedVolatility``
per strike. We pick the strike closest to spot for ATM IV.

References:
    - New-docs/04_OPTION_SUPPORT_EXTENSION.md §5
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

__all__ = ["IVRankResult", "compute_atm_iv", "compute_iv_rank"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IVRankResult:
    """Rank of the current IV (0 = cheap, 100 = rich)."""

    current_iv: Optional[float]
    rank_pct: Optional[float]  # 0.0-100.0
    window_days: int
    source: str  # 'hv_fallback' or 'iv_history' (future)
    sample_size: int


def _get_yfinance_ticker(symbol: str):
    """Lazy yfinance import so the package stays importable without the dep."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        logger.warning("yfinance not installed; cannot fetch IV data")
        return None
    try:
        return yf.Ticker(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance Ticker(%s) failed: %s", symbol, exc)
        return None


def compute_atm_iv(symbol: str, ref_date: Optional[date] = None) -> tuple[Optional[float], str]:
    """Return ``(atm_iv, ref_expiry_str)`` for the nearest expiry >= ref_date.

    Returns ``(None, "")`` on failure (missing data, no options, etc.).

    Resolution order:
        1. Moomoo OpenAPI (if MOOMOO_OPEND_ENABLED=true and SDK installed)
           — returns server-computed IV, no Black-Scholes reverse solve.
        2. yfinance fallback (legacy path).
    """
    # Phase C: prefer Moomoo when configured. Silent no-op when not enabled.
    try:
        from data_provider.moomoo_options import compute_atm_iv_moomoo

        iv_m, exp_m = compute_atm_iv_moomoo(symbol, ref_date)
        if iv_m and iv_m > 0:
            return iv_m, exp_m
    except Exception as exc:  # noqa: BLE001
        # Never let Moomoo's transport failures kill the IV calc — fall through to yfinance.
        logger.debug("moomoo IV path failed for %s: %s — falling back to yfinance", symbol, exc)

    tkr = _get_yfinance_ticker(symbol)
    if tkr is None:
        return None, ""

    try:
        expirations = tkr.options or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance options list failed for %s: %s", symbol, exc)
        return None, ""

    if not expirations:
        return None, ""

    target = ref_date or date.today()
    chosen: Optional[str] = None
    for exp_str in expirations:
        try:
            exp = date.fromisoformat(exp_str)
        except ValueError:
            continue
        if exp >= target:
            chosen = exp_str
            break
    if chosen is None:
        chosen = expirations[-1]

    try:
        chain = tkr.option_chain(chosen)
        calls = chain.calls
        if calls is None or calls.empty:
            return None, chosen
        info = tkr.info
        spot = info.get("regularMarketPrice") or info.get("previousClose")
        if spot is None:
            hist = tkr.history(period="5d")
            if hist is None or hist.empty:
                return None, chosen
            spot = float(hist["Close"].iloc[-1])
        calls = calls.copy()
        calls["_moneyness"] = (calls["strike"] - spot).abs()
        atm_row = calls.sort_values("_moneyness").iloc[0]
        iv = float(atm_row.get("impliedVolatility") or 0.0)
        if iv <= 0:
            return None, chosen
        return iv, chosen
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance option_chain(%s, %s) failed: %s", symbol, chosen, exc)
        return None, chosen


def _compute_hv(symbol: str, days_window: int = 252) -> tuple[Optional[float], Optional[list[float]]]:
    """Realised / historical volatility series (annualised) for fallback ranking."""
    tkr = _get_yfinance_ticker(symbol)
    if tkr is None:
        return None, None
    try:
        # Pull ~2x window_days so rolling HV has headroom.
        period = f"{max(days_window * 2, 60)}d"
        hist = tkr.history(period=period)
        if hist is None or hist.empty or "Close" not in hist:
            return None, None
        closes = hist["Close"].dropna()
        if len(closes) < 20:
            return None, None
        log_ret = (closes / closes.shift(1)).apply(lambda x: math.log(x) if x and x > 0 else None).dropna()
        # Rolling 20-day realised vol, annualised by sqrt(252).
        roll = log_ret.rolling(window=20).std() * math.sqrt(252)
        series = [float(v) for v in roll.dropna().tolist()[-days_window:]]
        if not series:
            return None, None
        return series[-1], series
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance history(%s) failed: %s", symbol, exc)
        return None, None


def compute_iv_rank(symbol: str, days_window: int = 252) -> IVRankResult:
    """Percentile rank of current IV against recent history (HV fallback)."""
    current_iv, _ = compute_atm_iv(symbol)
    hv_current, hv_series = _compute_hv(symbol, days_window=days_window)

    if not hv_series:
        return IVRankResult(
            current_iv=current_iv,
            rank_pct=None,
            window_days=days_window,
            source="hv_fallback",
            sample_size=0,
        )

    ref = current_iv if current_iv is not None else hv_current
    if ref is None:
        return IVRankResult(
            current_iv=current_iv,
            rank_pct=None,
            window_days=days_window,
            source="hv_fallback",
            sample_size=len(hv_series),
        )

    below = sum(1 for v in hv_series if v <= ref)
    rank_pct = 100.0 * below / len(hv_series) if hv_series else None

    return IVRankResult(
        current_iv=current_iv,
        rank_pct=rank_pct,
        window_days=days_window,
        source="hv_fallback",
        sample_size=len(hv_series),
    )

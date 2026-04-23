# -*- coding: utf-8 -*-
"""Black-Scholes pricing and Greek computation.

Pure-function numerics over scipy, no I/O. Used by LEAP Explorer, Breakout
Filter's theta-decay accounting, and the Agent option tools.

References:
    - Hull, "Options, Futures, and Other Derivatives" (10th ed., ch. 15/17)
    - New-docs/04_OPTION_SUPPORT_EXTENSION.md §3
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from scipy.stats import norm

__all__ = [
    "Greeks",
    "PricingResult",
    "time_to_expiry_years",
    "call_price",
    "put_price",
    "compute_greeks",
    "price_and_greeks",
    "implied_volatility_from_price",
]


_TRADING_YEAR_DAYS = 365.0  # Calendar-day convention; change to 252 for trading-day basis.


@dataclass(frozen=True)
class Greeks:
    """First-order Greeks for one contract.

    All values are "per 1 unit underlying move" (delta), "per 1% IV move"
    (vega/100), "per 1 calendar-day" (theta/365), "per 1% rate move"
    (rho/100). ``gamma`` is quoted per 1 unit underlying move.
    """

    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


@dataclass(frozen=True)
class PricingResult:
    """Theoretical price plus Greeks for a single leg."""

    price: float
    greeks: Greeks


def time_to_expiry_years(expiry: date, from_date: Optional[date] = None) -> float:
    """Calendar-day time-to-expiry expressed in years.

    If ``from_date`` is ``None`` uses UTC today. Returns 0 when expiry is in
    the past (rather than raising) so callers can still evaluate intrinsic
    value without extra guards.
    """
    ref = from_date or datetime.now(timezone.utc).date()
    days = (expiry - ref).days
    return max(days, 0) / _TRADING_YEAR_DAYS


def _d1_d2(S: float, K: float, T: float, iv: float, r: float, q: float) -> tuple[float, float]:
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        raise ValueError(
            f"Invalid BS inputs (S,K,T,iv)=({S},{K},{T},{iv}); require positive S,K,T,iv"
        )
    d1 = (math.log(S / K) + (r - q + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    return d1, d2


def call_price(S: float, K: float, T: float, iv: float, r: float = 0.045, q: float = 0.0) -> float:
    """Black-Scholes call price. Returns intrinsic when T<=0 or iv<=0."""
    if T <= 0:
        return max(S - K, 0.0)
    if iv <= 0:
        return max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    d1, d2 = _d1_d2(S, K, T, iv, r, q)
    return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def put_price(S: float, K: float, T: float, iv: float, r: float = 0.045, q: float = 0.0) -> float:
    """Black-Scholes put price via put-call parity. Intrinsic fallback when T<=0."""
    if T <= 0:
        return max(K - S, 0.0)
    if iv <= 0:
        return max(K * math.exp(-r * T) - S * math.exp(-q * T), 0.0)
    d1, d2 = _d1_d2(S, K, T, iv, r, q)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


def compute_greeks(
    S: float,
    K: float,
    T: float,
    iv: float,
    right: str,
    r: float = 0.045,
    q: float = 0.0,
) -> Greeks:
    """First-order Greeks for a call ('C') or put ('P').

    Returns zeros at expiry / iv<=0 to keep callers safe against divide-by-zero.
    """
    if right not in ("C", "P"):
        raise ValueError(f"right must be 'C' or 'P', got {right!r}")
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = _d1_d2(S, K, T, iv, r, q)
    pdf_d1 = norm.pdf(d1)
    sqrt_T = math.sqrt(T)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    if right == "C":
        delta = disc_q * norm.cdf(d1)
        theta = (
            -(S * disc_q * pdf_d1 * iv) / (2 * sqrt_T)
            - r * K * disc_r * norm.cdf(d2)
            + q * S * disc_q * norm.cdf(d1)
        )
        rho = K * T * disc_r * norm.cdf(d2)
    else:  # 'P'
        delta = disc_q * (norm.cdf(d1) - 1.0)
        theta = (
            -(S * disc_q * pdf_d1 * iv) / (2 * sqrt_T)
            + r * K * disc_r * norm.cdf(-d2)
            - q * S * disc_q * norm.cdf(-d1)
        )
        rho = -K * T * disc_r * norm.cdf(-d2)

    gamma = (disc_q * pdf_d1) / (S * iv * sqrt_T)
    # Vega scaled to "per 1% IV move" (i.e. per 0.01 absolute change in iv).
    vega = S * disc_q * pdf_d1 * sqrt_T / 100.0
    # Theta per calendar-day.
    theta_per_day = theta / _TRADING_YEAR_DAYS
    # Rho per 1% rate move.
    rho_scaled = rho / 100.0

    return Greeks(
        delta=float(delta),
        gamma=float(gamma),
        theta=float(theta_per_day),
        vega=float(vega),
        rho=float(rho_scaled),
    )


def price_and_greeks(
    S: float,
    expiry: date,
    K: float,
    iv: float,
    right: str,
    r: float = 0.045,
    q: float = 0.0,
    from_date: Optional[date] = None,
) -> PricingResult:
    """Convenience wrapper: compute T from expiry and return price + Greeks."""
    T = time_to_expiry_years(expiry, from_date=from_date)
    if right == "C":
        px = call_price(S, K, T, iv, r=r, q=q)
    elif right == "P":
        px = put_price(S, K, T, iv, r=r, q=q)
    else:
        raise ValueError(f"right must be 'C' or 'P', got {right!r}")
    g = compute_greeks(S, K, T, iv, right, r=r, q=q)
    return PricingResult(price=px, greeks=g)


def implied_volatility_from_price(
    market_price: float,
    S: float,
    K: float,
    T: float,
    right: str,
    r: float = 0.045,
    q: float = 0.0,
    tol: float = 1e-4,
    max_iter: int = 100,
    iv_init: float = 0.3,
) -> float:
    """Invert Black-Scholes via Newton-Raphson with a bisection fallback.

    Returns ``math.nan`` when inversion fails (deep out-of-the-money cases,
    negative prices, etc.).
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return float("nan")
    if right not in ("C", "P"):
        raise ValueError(f"right must be 'C' or 'P', got {right!r}")

    # Intrinsic floor check.
    intrinsic = max(S - K, 0.0) if right == "C" else max(K - S, 0.0)
    if market_price < intrinsic * math.exp(-r * T) - tol:
        return float("nan")

    iv = iv_init
    for _ in range(max_iter):
        price = call_price(S, K, T, iv, r=r, q=q) if right == "C" else put_price(S, K, T, iv, r=r, q=q)
        # Vega (pre-scaling) for Newton step.
        try:
            d1, _ = _d1_d2(S, K, T, iv, r, q)
        except ValueError:
            break
        vega_raw = S * math.exp(-q * T) * norm.pdf(d1) * math.sqrt(T)
        if vega_raw < 1e-10:
            break
        diff = price - market_price
        if abs(diff) < tol:
            return float(iv)
        iv -= diff / vega_raw
        if iv <= 0 or iv > 10:  # escape the well
            break

    # Bisection fallback over a wide band.
    lo, hi = 1e-4, 5.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        price = call_price(S, K, T, mid, r=r, q=q) if right == "C" else put_price(S, K, T, mid, r=r, q=q)
        if abs(price - market_price) < tol:
            return float(mid)
        if price < market_price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            return float(0.5 * (lo + hi))
    return float("nan")

# -*- coding: utf-8 -*-
"""Black-Scholes pricing tests against Hull's tabulated examples."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from src.options.black_scholes import (
    call_price,
    compute_greeks,
    implied_volatility_from_price,
    price_and_greeks,
    put_price,
    time_to_expiry_years,
)


class TestCallPut:
    def test_hull_textbook_call(self):
        # Hull ch.15 example: S=42, K=40, T=0.5, iv=0.2, r=0.1 -> ~4.76
        px = call_price(42, 40, 0.5, 0.2, r=0.1)
        assert math.isclose(px, 4.7594, abs_tol=0.01)

    def test_hull_textbook_put(self):
        # Put-call parity check, same inputs: put ~ 0.8086
        px = put_price(42, 40, 0.5, 0.2, r=0.1)
        assert math.isclose(px, 0.8086, abs_tol=0.01)

    def test_call_plus_put_parity(self):
        S, K, T, iv, r = 100, 100, 1.0, 0.2, 0.05
        c = call_price(S, K, T, iv, r=r)
        p = put_price(S, K, T, iv, r=r)
        # c - p == S - K*exp(-rT)
        parity = S - K * math.exp(-r * T)
        assert math.isclose(c - p, parity, abs_tol=1e-6)

    def test_intrinsic_at_expiry(self):
        assert call_price(100, 90, 0.0, 0.2) == pytest.approx(10.0)
        assert put_price(100, 110, 0.0, 0.2) == pytest.approx(10.0)
        assert call_price(100, 110, 0.0, 0.2) == pytest.approx(0.0)

    def test_zero_iv_gives_discounted_intrinsic(self):
        assert call_price(100, 90, 0.5, 0.0, r=0.05) == pytest.approx(
            max(100 - 90 * math.exp(-0.05 * 0.5), 0.0)
        )


class TestGreeks:
    def test_atm_call_delta_near_half(self):
        g = compute_greeks(100, 100, 1.0, 0.2, "C", r=0.05)
        # ATM call delta ~ 0.6368 with these inputs (Hull exercise values).
        assert 0.5 < g.delta < 0.7

    def test_atm_put_delta_negative(self):
        g = compute_greeks(100, 100, 1.0, 0.2, "P", r=0.05)
        assert -0.5 < g.delta < 0.0

    def test_delta_monotonic_in_spot(self):
        # Moving spot up should only increase call delta.
        deltas = [compute_greeks(S, 100, 0.5, 0.3, "C").delta for S in (80, 90, 100, 110, 120)]
        assert deltas == sorted(deltas)

    def test_theta_is_negative_for_long(self):
        g = compute_greeks(100, 100, 0.5, 0.3, "C")
        assert g.theta < 0  # long call bleeds theta

    def test_gamma_is_positive(self):
        g = compute_greeks(100, 100, 0.5, 0.3, "C")
        assert g.gamma > 0

    def test_bad_right_raises(self):
        with pytest.raises(ValueError):
            compute_greeks(100, 100, 0.5, 0.3, "X")

    def test_expired_returns_zeros(self):
        g = compute_greeks(100, 100, 0.0, 0.3, "C")
        assert g.delta == 0 and g.gamma == 0 and g.theta == 0


class TestPriceAndGreeks:
    def test_wrapper_composes(self):
        expiry = date.today() + timedelta(days=182)
        res = price_and_greeks(100, expiry, 100, 0.25, "C")
        assert res.price > 0
        assert -0.1 < res.greeks.theta < 0  # reasonable theta
        assert 0 < res.greeks.delta < 1


class TestIVInversion:
    def test_roundtrip(self):
        true_iv = 0.35
        px = call_price(120, 120, 0.25, true_iv, r=0.05)
        iv_back = implied_volatility_from_price(px, 120, 120, 0.25, "C", r=0.05)
        assert math.isclose(iv_back, true_iv, abs_tol=1e-3)

    def test_impossible_price_returns_nan(self):
        # Market price of 0 on an ATM call can't correspond to positive IV.
        iv = implied_volatility_from_price(0.0, 100, 100, 0.25, "C")
        assert math.isnan(iv)


class TestTimeToExpiry:
    def test_past_expiry_returns_zero(self):
        assert time_to_expiry_years(date(2020, 1, 1), from_date=date(2026, 1, 1)) == 0.0

    def test_one_year_out(self):
        from_d = date(2026, 1, 1)
        to_d = date(2027, 1, 1)
        t = time_to_expiry_years(to_d, from_date=from_d)
        assert math.isclose(t, 1.0, abs_tol=0.01)

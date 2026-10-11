# -*- coding: utf-8 -*-
"""Variance-ratio unit tests: VR(q), regime shape vote, scaling, DTE gate."""
from __future__ import annotations

import math
import random

import pytest

from src.regime.varratio import (
    classify_regime_shape,
    dte_gate,
    position_scale,
    variance_ratio,
)


def _gaussian(seed: int, n: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


def _ar1(rho: float, seed: int, n: int = 500, burn: int = 100) -> list[float]:
    """r_t = rho * r_{t-1} + eps, eps ~ N(0,1), deterministic seed."""
    rng = random.Random(seed)
    x = 0.0
    out: list[float] = []
    for i in range(n + burn):
        x = rho * x + rng.gauss(0.0, 1.0)
        if i >= burn:
            out.append(x)
    return out


class TestVarianceRatio:
    def test_fail_closed_on_short_or_bad_q(self):
        assert variance_ratio([], 2) is None
        assert variance_ratio([0.01] * 29, 2) is None  # n < 30
        assert variance_ratio([0.01] * 100, 1) is None  # q < 2
        assert variance_ratio(_gaussian(1, 29), 10) is None  # n < 3*q
        assert variance_ratio(_gaussian(1, 45), 16) is None  # n < 3*16

    def test_constant_series_is_none(self):
        # Zero variance -> VR undefined -> fail closed.
        assert variance_ratio([0.01] * 40, 2) is None

    def test_alternating_series_hand_computed(self):
        # returns = +1,-1,+1,... (n=30, q=2): every overlapping 2-sum is 0
        # and mu=0, so varq=0 and VR=0 exactly.
        #   z  = (0-1)/sqrt(phi), phi = 2*(2q-1)(q-1)/(3qn) = 6/180 = 1/30
        #      = -sqrt(30)
        #   a_t = 1 for all t => delta_1 = n*(n-1)/n^2 = 29/30, theta = 29/30
        #   z* = sqrt(30)*(0-1)/sqrt(29/30)
        r = variance_ratio([1.0, -1.0] * 15, 2)
        assert r is not None
        assert r["q"] == 2 and r["n"] == 30
        assert r["vr"] == 0.0
        assert r["z"] == pytest.approx(-math.sqrt(30.0))
        assert r["z_star"] == pytest.approx(-math.sqrt(30.0) / math.sqrt(29.0 / 30.0))

    def test_known_answer_regression(self):
        # Fixed deterministic series, values pinned once and cross-checked.
        s = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.0, 0.01, -0.015] * 3
        r = variance_ratio(s, 2)
        assert r is not None
        assert r["vr"] == pytest.approx(0.3347933513027851, rel=1e-9)
        assert r["z"] == pytest.approx(-3.6434868689387914, rel=1e-9)
        assert r["z_star"] == pytest.approx(-3.9183763078967764, rel=1e-9)

    def test_iid_gaussian_vr_near_one(self):
        g = _gaussian(42, 500)
        for q in (2, 5, 10):
            r = variance_ratio(g, q)
            assert r is not None
            assert abs(r["vr"] - 1.0) < 0.15
            assert abs(r["z"]) < 1.645
            assert abs(r["z_star"]) < 1.645

    def test_ar1_positive_rho_vr2_above_one(self):
        r = variance_ratio(_ar1(0.4, 7), 2)
        assert r is not None
        assert r["vr"] > 1.0  # theoretical VR(2) = 1 + rho = 1.4
        assert r["z_star"] > 1.645

    def test_ar1_negative_rho_vr2_below_one(self):
        r = variance_ratio(_ar1(-0.4, 7), 2)
        assert r is not None
        assert r["vr"] < 1.0  # theoretical VR(2) = 1 + rho = 0.6
        assert r["z_star"] < -1.645


class TestClassifyRegimeShape:
    def test_iid_gaussian_random_walk(self):
        out = classify_regime_shape(_gaussian(42, 500))
        assert out["label"] == "random_walk"
        assert out["n"] == 500
        assert len(out["details"]) == 3
        assert all(d["vote"] == "random_walk" for d in out["details"])

    def test_ar1_positive_trending(self):
        out = classify_regime_shape(_ar1(0.4, 7))
        assert out["label"] == "trending"

    def test_ar1_negative_mean_reverting(self):
        out = classify_regime_shape(_ar1(-0.4, 7))
        assert out["label"] == "mean_reverting"

    def test_too_short_insufficient_data(self):
        out = classify_regime_shape(_gaussian(1, 20))
        assert out["label"] == "insufficient_data"
        assert out["n"] == 20
        assert all(d["vr"] is None for d in out["details"])

    def test_single_usable_vr_still_insufficient(self):
        # n=30: only q=2 passes n >= max(3q, 30); one vote is not enough.
        out = classify_regime_shape(_gaussian(3, 30), qs=(2, 20, 25))
        assert out["label"] == "insufficient_data"

    def test_empty_input(self):
        out = classify_regime_shape([])
        assert out["label"] == "insufficient_data"
        assert out["n"] == 0


class TestPositionScale:
    def test_clip_at_floor(self):
        # Recent variance 4x the baseline -> raw 0.25 == floor exactly;
        # 100x baseline -> raw 0.01 clipped up to floor.
        assert position_scale(100.0, 1.0) == 0.25

    def test_clip_at_cap(self):
        # Calm recent regime would over-lever -> capped at 1.0.
        assert position_scale(1.0, 2.0) == 1.0

    def test_within_range(self):
        assert position_scale(1.0, 0.5) == pytest.approx(0.5)

    def test_custom_floor_cap(self):
        assert position_scale(10.0, 1.0, floor=0.5, cap=2.0) == 0.5
        assert position_scale(1.0, 10.0, floor=0.5, cap=2.0) == 2.0

    def test_zero_or_negative_variance_none(self):
        assert position_scale(0.0, 1.0) is None
        assert position_scale(1.0, 0.0) is None
        assert position_scale(-1.0, 1.0) is None
        assert position_scale(1.0, -1.0) is None


class TestDteGate:
    def test_trending_with_high_score_allows(self):
        out = dte_gate("trending", 55)
        assert out["allow_0_1dte"] is True
        assert out["default_bucket"] == "2-7"

    def test_threshold_boundary(self):
        assert dte_gate("trending", 40)["allow_0_1dte"] is True
        assert dte_gate("trending", 39)["allow_0_1dte"] is False

    def test_missing_score_fails_closed(self):
        assert dte_gate("trending", None)["allow_0_1dte"] is False

    def test_non_trending_shapes_fail_closed(self):
        for label in ("random_walk", "mean_reverting", "insufficient_data", ""):
            out = dte_gate(label, 80)
            assert out["allow_0_1dte"] is False
            assert out["default_bucket"] == "2-7"

    def test_custom_threshold(self):
        assert dte_gate("trending", 45, score_threshold=50)["allow_0_1dte"] is False
        assert dte_gate("trending", 50, score_threshold=50)["allow_0_1dte"] is True


# ---------------------------------------------------------------------------
# tsmom_mm_gate (E-4 calibrated combination, doc 16 §E-4)
# ---------------------------------------------------------------------------

from src.regime.varratio import tsmom_mm_gate


class TestTsmomMmGate:
    def test_all_pass_opens_gate(self):
        out = tsmom_mm_gate(0.05, 0.08, 0.8)
        assert out["allow_0_1dte"] is True
        assert out["blocked_by"] == []
        assert out["default_bucket"] == "2-7"

    def test_aug_3_replay_is_red(self):
        # Real features of 2026-08-03 (worst day, 0-1DTE -$84,904):
        # QQQ 20d -3.45%, SOXX 20d -10.85%, MM scale 0.54.
        out = tsmom_mm_gate(-0.0345, -0.1085, 0.5445)
        assert out["allow_0_1dte"] is False
        assert "market_momentum" in out["blocked_by"]
        assert "sector_momentum" in out["blocked_by"]
        # MM alone would have passed — documents why both families are needed.
        assert "volatility_scale" not in out["blocked_by"]

    def test_high_vol_blocks_even_with_momentum(self):
        out = tsmom_mm_gate(0.05, 0.08, 0.3)
        assert out["allow_0_1dte"] is False
        assert out["blocked_by"] == ["volatility_scale"]

    def test_missing_inputs_fail_closed(self):
        assert tsmom_mm_gate(None, 0.05, 0.8)["allow_0_1dte"] is False
        assert tsmom_mm_gate(0.05, None, 0.8)["allow_0_1dte"] is False
        assert tsmom_mm_gate(0.05, 0.05, None)["allow_0_1dte"] is False

    def test_zero_momentum_is_not_enough(self):
        out = tsmom_mm_gate(0.0, 0.05, 0.8)
        assert out["allow_0_1dte"] is False
        assert out["blocked_by"] == ["market_momentum"]

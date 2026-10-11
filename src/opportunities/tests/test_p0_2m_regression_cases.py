from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.opportunities.p0_2m_signal import (
    P0_2M_PRICE_PERCENTILE_MIN,
    P0_2M_REFERENCE_SESSIONS,
    P0_2M_RVOL_MIN,
    P0_2M_SIGNAL_VERSION,
    P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
)


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "p0_2m_60d_regression_cases.json"
)


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_real_case_snapshot_is_pinned_to_the_production_specification():
    payload = _load()
    spec = payload["specification"]

    assert payload["schema_version"] == "p0_2m_case_regression_v1"
    assert spec == {
        "signal_version": P0_2M_SIGNAL_VERSION,
        "baseline_sessions": P0_2M_REFERENCE_SESSIONS,
        "price_percentile_min": P0_2M_PRICE_PERCENTILE_MIN,
        "rvol_8m_min": P0_2M_RVOL_MIN,
        "displacement_atr_min": P0_2M_STRICT_DISPLACEMENT_ATR_MIN,
        "same_direction_required": True,
    }


def test_tsla_and_meta_real_case_outcomes_match_all_four_frozen_gates():
    cases = {
        (row["symbol"], row["market_date_et"]): row
        for row in _load()["cases"]
    }

    tsla = cases[("TSLA", "2026-08-31")]
    earlier = tsla["earlier_checkpoint"]
    assert earlier["window_end"].endswith("09:48:00-04:00")
    assert earlier["price_percentile"] == pytest.approx(0.916667)
    assert earlier["rvol_8m"] >= P0_2M_RVOL_MIN
    assert earlier["displacement_atr"] >= P0_2M_STRICT_DISPLACEMENT_ATR_MIN
    assert earlier["strict_qualified"] is False
    assert earlier["failed_checks"] == ["price_percentile_below_0.95"]

    first = tsla["first_strict_crossing"]
    assert first["window_end"].endswith("09:52:00-04:00")
    assert first["direction"] == "up"
    assert first["price_percentile"] >= P0_2M_PRICE_PERCENTILE_MIN
    assert first["rvol_8m"] >= P0_2M_RVOL_MIN
    assert first["displacement_atr"] >= P0_2M_STRICT_DISPLACEMENT_ATR_MIN
    assert first["failed_checks"] == []
    assert tsla["strict_qualified"] is True

    meta_miss = cases[("META", "2026-09-01")]
    assert meta_miss["window_end"] is None
    assert (
        meta_miss["max_abs_displacement_atr_rth"]
        < P0_2M_STRICT_DISPLACEMENT_ATR_MIN
    )
    assert meta_miss["strict_qualified"] is False

    meta_hit = cases[("META", "2026-09-10")]
    assert meta_hit["window_end"].endswith("09:52:00-04:00")
    assert meta_hit["direction"] == "down"
    assert meta_hit["price_percentile"] >= P0_2M_PRICE_PERCENTILE_MIN
    assert meta_hit["rvol_8m"] >= P0_2M_RVOL_MIN
    assert meta_hit["displacement_atr"] >= P0_2M_STRICT_DISPLACEMENT_ATR_MIN
    assert meta_hit["strict_qualified"] is True
    assert meta_hit["failed_checks"] == []

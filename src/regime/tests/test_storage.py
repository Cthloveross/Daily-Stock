# -*- coding: utf-8 -*-
"""Regime storage CRUD tests."""
from __future__ import annotations

from datetime import date, timedelta

from src.regime.classifier import RegimeResult
from src.regime.storage import (
    get_recent_scores,
    get_regime_score,
    init_regime_schema,
    save_regime_score,
)


def _mk_result(d: date, score: int = 60, label: str = "standard") -> RegimeResult:
    return RegimeResult(
        date=d,
        score=score,
        label=label,
        action_hint="",
        d1_direction=20,
        d2_volatility=10,
        d3_macro_penalty=0,
        d4_sector=10,
        d5_prev_day=10,
        d6_premarket=10,
        snapshot={"spy": {"close": 510}},
    )


def test_schema_idempotent():
    init_regime_schema()
    init_regime_schema()


def test_save_and_fetch():
    save_regime_score(_mk_result(date(2026, 4, 17)))
    row = get_regime_score(date(2026, 4, 17))
    assert row is not None
    assert row["score"] == 60
    assert row["label"] == "standard"
    assert row["snapshot"]["spy"]["close"] == 510


def test_upsert_updates_existing():
    save_regime_score(_mk_result(date(2026, 4, 17), score=60))
    save_regime_score(_mk_result(date(2026, 4, 17), score=80, label="aggressive"))
    row = get_regime_score(date(2026, 4, 17))
    assert row["score"] == 80
    assert row["label"] == "aggressive"


def test_recent_scores():
    for i in range(5):
        save_regime_score(_mk_result(date.today() - timedelta(days=i)))
    rows = get_recent_scores(days=3)
    assert len(rows) == 4  # today + 3 previous
    assert rows[0]["date"] >= rows[-1]["date"]  # descending order


def test_missing_date_returns_none():
    assert get_regime_score(date(2020, 1, 1)) is None

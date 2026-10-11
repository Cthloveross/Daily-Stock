# -*- coding: utf-8 -*-
"""Freshness guard for Journal-backed intraday safety context."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.services.intraday_alert_context import _build_is_fresh_for_et_date


def _build(*, recorded_at: datetime, source_cutoff_at: datetime):
    return SimpleNamespace(
        recorded_at=recorded_at,
        source_cutoff_at=source_cutoff_at,
    )


def test_build_freshness_requires_creation_and_source_cutoff_on_target_et_day():
    assert _build_is_fresh_for_et_date(
        _build(
            recorded_at=datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc),
            source_cutoff_at=datetime(2026, 8, 31, 14, 55, tzinfo=timezone.utc),
        ),
        "2026-08-31",
    ) is True


def test_previous_day_source_or_build_fails_closed():
    previous_source = _build(
        recorded_at=datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc),
        source_cutoff_at=datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc),
    )
    previous_build = _build(
        recorded_at=datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc),
        source_cutoff_at=datetime(2026, 8, 31, 14, 55, tzinfo=timezone.utc),
    )

    assert _build_is_fresh_for_et_date(previous_source, "2026-08-31") is False
    assert _build_is_fresh_for_et_date(previous_build, "2026-08-31") is False
    assert _build_is_fresh_for_et_date(SimpleNamespace(), "2026-08-31") is False
    assert _build_is_fresh_for_et_date(previous_source, "2026-99-99") is False

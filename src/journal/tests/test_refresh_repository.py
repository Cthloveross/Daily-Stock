# -*- coding: utf-8 -*-
"""Deterministic contracts for server-owned Journal refresh windows."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.journal.ledger.models import ImportBatch
from src.journal.ledger.refresh_repository import (
    JournalRefreshError,
    suggest_refresh_window,
)
from src.journal.ledger.repository import init_ledger_schema
from src.storage import get_db


ET = ZoneInfo("America/New_York")
ACCOUNT_KEY = "default_moomoo_us"


def _seed_csv_baseline() -> None:
    """Create the accepted CSV anchor required by ``suggest_refresh_window``."""
    init_ledger_schema()
    with get_db().session_scope() as session:
        session.add(
            ImportBatch(
                batch_key="refresh-window-csv-baseline",
                broker="moomoo",
                account_key=ACCOUNT_KEY,
                source_kind="csv",
                source_schema="moomoo-history-csv",
                source_sha256="a" * 64,
                parser_name="moomoo_statement",
                parser_version="test-v1",
                window_start=datetime(2026, 6, 1, 9, 30, tzinfo=ET),
                window_end=datetime(2026, 6, 1, 16, 0, tzinfo=ET),
                source_timezone="America/New_York",
                status="accepted",
                analysis_level="aggregate",
                analysis_ready=True,
                order_observation_count=1,
                fill_observation_count=0,
                rejected_record_count=0,
                reconciliation_status="not_run",
                reconciliation_json="{}",
                completeness_score=Decimal("1"),
                completeness_json="{}",
                warnings_json="[]",
                provenance_json="{}",
            )
        )


@pytest.mark.parametrize(
    ("now", "expected_close"),
    (
        pytest.param(
            datetime(2026, 7, 28, 8, 0, tzinfo=ET),
            datetime(2026, 7, 27, 16, 0, tzinfo=ET),
            id="regular-session-premarket-uses-prior-close",
        ),
        pytest.param(
            datetime(2026, 7, 28, 12, 0, tzinfo=ET),
            datetime(2026, 7, 27, 16, 0, tzinfo=ET),
            id="regular-session-intraday-uses-prior-close",
        ),
        pytest.param(
            datetime(2026, 7, 28, 16, 0, tzinfo=ET),
            datetime(2026, 7, 28, 16, 0, tzinfo=ET),
            id="regular-session-exact-close-is-complete",
        ),
        pytest.param(
            datetime(2026, 7, 28, 16, 0, 1, tzinfo=ET),
            datetime(2026, 7, 28, 16, 0, tzinfo=ET),
            id="regular-session-after-close-uses-same-close",
        ),
        pytest.param(
            datetime(2026, 8, 1, 12, 0, tzinfo=ET),
            datetime(2026, 7, 31, 16, 0, tzinfo=ET),
            id="weekend-uses-friday-close",
        ),
        pytest.param(
            datetime(2026, 9, 7, 12, 0, tzinfo=ET),
            datetime(2026, 9, 4, 16, 0, tzinfo=ET),
            id="labor-day-holiday-uses-prior-close",
        ),
        pytest.param(
            datetime(2026, 11, 27, 13, 0, 1, tzinfo=ET),
            datetime(2026, 11, 27, 13, 0, tzinfo=ET),
            id="half-day-after-early-close-uses-real-close",
        ),
        pytest.param(
            datetime(2026, 11, 27, 12, 30, tzinfo=ET),
            datetime(2026, 11, 25, 16, 0, tzinfo=ET),
            id="half-day-before-early-close-excludes-open-session",
        ),
    ),
)
def test_suggest_refresh_window_ends_at_most_recent_completed_xnys_close(
    now: datetime,
    expected_close: datetime,
) -> None:
    _seed_csv_baseline()

    start, end = suggest_refresh_window(ACCOUNT_KEY, now=now, overlap_days=7)

    assert end == expected_close
    assert end <= now
    assert start < end


def test_suggest_refresh_window_rejects_a_naive_refresh_clock() -> None:
    _seed_csv_baseline()

    with pytest.raises(JournalRefreshError, match="timezone"):
        suggest_refresh_window(
            ACCOUNT_KEY,
            now=datetime(2026, 7, 28, 12, 0),
        )


def test_suggest_refresh_window_fails_closed_when_xnys_calendar_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import exchange_calendars as xcals

    _seed_csv_baseline()

    def broken_calendar(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(
        xcals,
        "get_calendar",
        broken_calendar,
    )

    with pytest.raises(JournalRefreshError, match="cannot resolve"):
        suggest_refresh_window(
            ACCOUNT_KEY,
            now=datetime(2026, 7, 28, 12, 0, tzinfo=ET),
        )

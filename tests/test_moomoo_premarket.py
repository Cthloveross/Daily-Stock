from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from types import ModuleType, SimpleNamespace

import pandas as pd

from data_provider.moomoo_fetcher import MoomooFetcher


class _Context:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls: list[dict] = []

    def request_history_kline(self, **kwargs):
        self.calls.append(kwargs)
        return 0, self.frame.copy(), None


def _install_sdk(monkeypatch) -> None:
    sdk = ModuleType("moomoo")
    sdk.KLType = SimpleNamespace(K_1M="K_1M")
    sdk.AuType = SimpleNamespace(NONE="NONE")
    sdk.KL_FIELD = SimpleNamespace(ALL="ALL")
    sdk.RET_OK = 0
    monkeypatch.setitem(sys.modules, "moomoo", sdk)


def _fetcher(context: _Context) -> MoomooFetcher:
    fetcher = MoomooFetcher.__new__(MoomooFetcher)
    fetcher._get_ctx = lambda: context
    return fetcher


def _frame(*rows: tuple[str, float, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time_key": time_key,
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "last_close": last_close,
                "volume": 100,
            }
            for time_key, close, last_close in rows
        ]
    )


def test_premarket_uses_unadjusted_completed_minutes_and_last_close(
    monkeypatch,
) -> None:
    _install_sdk(monkeypatch)
    context = _Context(
        _frame(
            ("2026-04-17 03:59:00", 80.0, 100.0),
            ("2026-04-17 04:00:00", 101.0, 100.0),
            ("2026-04-17 09:11:00", 102.0, 100.0),
            # 09:12 ET is in progress at the frozen 09:12:45 observation.
            ("2026-04-17 09:12:00", 120.0, 100.0),
            ("2026-04-17 09:30:00", 130.0, 100.0),
        )
    )

    result = _fetcher(context).get_premarket(
        "spy",
        target_date=date(2026, 4, 17),
        as_of=datetime(2026, 4, 17, 13, 12, 45, tzinfo=timezone.utc),
    )

    assert result["_status"] == "ready"
    assert result["price"] == 102.0
    assert result["previous_close"] == 100.0
    assert result["previous_close_date"] == "2026-04-16"
    assert result["previous_close_field"] == "last_close"
    assert result["pct_change"] == 2.0
    assert result["as_of"] == "2026-04-17T13:12:00+00:00"
    assert len(context.calls) == 1
    request = context.calls[0]
    assert request["start"] == "2026-04-17"
    assert request["end"] == "2026-04-17"
    assert request["ktype"] == "K_1M"
    assert request["autype"] == "NONE"
    assert request["extended_time"] is True


def test_premarket_stale_bar_and_missing_last_close_fail_closed(
    monkeypatch,
) -> None:
    _install_sdk(monkeypatch)
    observed_at = datetime(
        2026,
        4,
        17,
        13,
        12,
        45,
        tzinfo=timezone.utc,
    )
    stale = _fetcher(
        _Context(_frame(("2026-04-17 08:59:00", 102.0, 100.0)))
    ).get_premarket(
        "SPY",
        target_date=date(2026, 4, 17),
        as_of=observed_at,
    )
    missing_reference = _fetcher(
        _Context(_frame(("2026-04-17 09:11:00", 102.0, 0.0)))
    ).get_premarket(
        "SPY",
        target_date=date(2026, 4, 17),
        as_of=observed_at,
    )

    assert stale["_status"] == "degraded"
    assert stale["_reason"] == "premarket_bar_stale"
    assert stale["pct_change"] is None
    assert missing_reference["_status"] == "degraded"
    assert missing_reference["_reason"] == "previous_close_last_close_missing"
    assert missing_reference["pct_change"] is None


def test_premarket_before_0400_et_makes_no_sdk_request(monkeypatch) -> None:
    _install_sdk(monkeypatch)
    context = _Context(_frame(("2026-04-17 04:00:00", 101.0, 100.0)))

    result = _fetcher(context).get_premarket(
        "SPY",
        target_date=date(2026, 4, 17),
        as_of=datetime(2026, 4, 17, 7, 59, tzinfo=timezone.utc),
    )

    assert result["_status"] == "unavailable"
    assert result["_reason"] == "premarket_not_started_or_no_completed_bar"
    assert result["pct_change"] is None
    assert context.calls == []

# -*- coding: utf-8 -*-
"""Moomoo OpenAPI read-only history adapter regression tests."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import ModuleType

import pytest

from src.journal.brokers import moomoo_live


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_moomoo(monkeypatch):
    module = ModuleType("moomoo")

    class TrdEnv:
        REAL = object()
        SIMULATE = object()

    module.TrdEnv = TrdEnv
    monkeypatch.setitem(sys.modules, "moomoo", module)
    return TrdEnv


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 2, tzinfo=timezone.utc)
    return start, end


def test_live_contract_maps_to_sdk_real(monkeypatch, fake_moomoo) -> None:
    """Regression: SDK 10.x exposes REAL, not the app-level LIVE name."""
    ctx = _FakeContext()
    captured = {}

    monkeypatch.setattr(moomoo_live, "_ctx_open", lambda **_: ctx)

    def fake_fetch(_ctx, *, start_str, end_str, env_enum):
        captured.update(start=start_str, end=end_str, env=env_enum)
        return []

    monkeypatch.setattr(moomoo_live, "_fetch_via_deals", fake_fetch)
    start, end = _window()

    result = moomoo_live.fetch_orders_as_journal(
        start=start,
        end=end,
        trd_env="LIVE",
    )

    assert result == []
    assert captured["env"] is fake_moomoo.REAL
    assert ctx.closed is True


def test_simulate_contract_maps_to_sdk_simulate(monkeypatch, fake_moomoo) -> None:
    ctx = _FakeContext()
    captured = {}

    monkeypatch.setattr(moomoo_live, "_ctx_open", lambda **_: ctx)

    def fake_fetch(_ctx, *, start_str, end_str, env_enum):
        captured.update(start=start_str, end=end_str, env=env_enum)
        return []

    monkeypatch.setattr(moomoo_live, "_fetch_via_orders", fake_fetch)
    start, end = _window()

    result = moomoo_live.fetch_orders_as_journal(
        start=start,
        end=end,
        trd_env="SIMULATE",
    )

    assert result == []
    assert captured["env"] is fake_moomoo.SIMULATE
    assert ctx.closed is True


def test_rejects_unknown_trade_environment() -> None:
    start, end = _window()

    with pytest.raises(ValueError, match="expected SIMULATE or LIVE"):
        moomoo_live.fetch_orders_as_journal(
            start=start,
            end=end,
            trd_env="REAL",
        )

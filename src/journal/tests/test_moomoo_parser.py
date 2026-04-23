# -*- coding: utf-8 -*-
"""Moomoo CSV parser unit tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.journal.brokers.moomoo_us import compute_external_id, parse


FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "journal"
INLINE_CSV = FIXTURES / "moomoo_inline_sample.csv"


def _load_inline() -> bytes:
    return INLINE_CSV.read_bytes()


class TestParse:
    def test_filled_only(self):
        orders = parse(_load_inline())
        # 6 rows in fixture but one is Cancelled, so 5 Filled orders expected
        assert len(orders) == 5

    def test_fill_merge(self):
        # The first NVDA call uses two fills (5@2.50 + 5@2.60 -> avg 2.55)
        orders = parse(_load_inline())
        first_call = next(o for o in orders if o.symbol == "NVDA260417C200000" and o.side == "Buy")
        assert first_call.filled_qty == 10
        assert len(first_call.fills) == 2
        assert abs(first_call.avg_fill_price - 2.55) < 0.001

    def test_option_symbol_parsed(self):
        orders = parse(_load_inline())
        tsla_put = next(o for o in orders if o.symbol == "TSLA260417P382500")
        assert tsla_put.instrument is not None
        assert tsla_put.instrument.is_option is True
        assert tsla_put.instrument.option.right == "P"
        assert tsla_put.instrument.option.strike == pytest.approx(382.5)

    def test_plain_equity_parsed(self):
        orders = parse(_load_inline())
        equity_buys = [o for o in orders if o.symbol == "NVDA" and o.side == "Buy"]
        assert len(equity_buys) == 1
        eq = equity_buys[0]
        assert eq.instrument is not None
        assert eq.instrument.is_option is False
        assert eq.filled_qty == 50

    def test_skips_cancelled(self):
        orders = parse(_load_inline())
        assert not any(o.status.lower() == "cancelled" for o in orders)
        assert all(o.filled_qty > 0 for o in orders)

    def test_external_id_deterministic(self):
        orders = parse(_load_inline())
        ids_first = [compute_external_id(o) for o in orders]
        ids_second = [compute_external_id(o) for o in parse(_load_inline())]
        assert ids_first == ids_second
        # Different orders produce different IDs
        assert len(set(ids_first)) == len(ids_first)

    def test_handles_empty_csv(self):
        assert parse(b"") == []

    def test_tolerates_header_only(self):
        header = b"Side,Symbol,Order Qty,Status,Fill Qty,Fill Price\n"
        assert parse(header) == []

    def test_et_timezone_applied(self):
        orders = parse(_load_inline())
        any_order = next(o for o in orders if o.first_fill_time is not None)
        assert any_order.first_fill_time.tzinfo is not None
        assert any_order.first_fill_time.utcoffset() is not None

    def test_external_id_stable_across_dst_boundary(self):
        """Regression: compute_external_id must normalise to UTC so that DST
        changes do not produce different ids for the same moment in time."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from src.journal.brokers.moomoo_us import MoomooOrder

        # Two instants that should produce the same external_id.
        # ET datetime tagged with EDT offset (-04:00).
        et = ZoneInfo("America/New_York")
        instant_et = datetime(2026, 6, 15, 10, 0, 0, tzinfo=et)
        # Same moment but represented as naive UTC (no tz). _canonical_ts
        # now tags naive times as ET, so we need to reconstruct via absolute moment.
        instant_utc = instant_et.astimezone(ZoneInfo("UTC"))

        make = lambda ft: MoomooOrder(
            side="Buy",
            symbol="NVDA260620C500000",
            name="",
            order_qty=1,
            order_price=2.0,
            order_amount=200.0,
            status="Filled",
            order_time=ft,
            order_type="Limit",
            session="Regular",
            fills=[{"qty": 1, "price": 2.0, "time": ft}],
            filled_qty=1,
            avg_fill_price=2.0,
            first_fill_time=ft,
            last_fill_time=ft,
            raw_rows=[],
        )
        from src.journal.brokers.moomoo_us import compute_external_id

        id_et = compute_external_id(make(instant_et))
        id_utc_tagged = compute_external_id(make(instant_utc))
        assert id_et == id_utc_tagged

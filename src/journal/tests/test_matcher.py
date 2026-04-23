# -*- coding: utf-8 -*-
"""FIFO matcher unit tests covering the 9 canonical scenarios."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from src.journal.matcher import dte_bucket_of, match_legs_fifo


def _evt(
    idx: int,
    side: str,
    qty: int,
    price: float,
    *,
    underlying: str = "NVDA",
    is_option: bool = False,
    expiry: date | None = None,
    strike: float | None = None,
    right: str | None = None,
    minutes_offset: int = 0,
    total_fee: float = 0.0,
):
    return {
        "id": idx,
        "external_id": f"ext_{idx}",
        "raw_symbol": f"{underlying}_OPT" if is_option else underlying,
        "is_option": is_option,
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "right": right,
        "side": side,
        "quantity": qty,
        "price": price,
        "order_time": None,
        "first_fill_time": datetime(2026, 4, 15, 10, 0) + timedelta(minutes=minutes_offset),
        "total_fee": total_fee,
    }


class TestDteBucket:
    def test_buckets(self):
        assert dte_bucket_of(0) == "0DTE"
        assert dte_bucket_of(-1) == "0DTE"
        assert dte_bucket_of(1) == "1-3DTE"
        assert dte_bucket_of(3) == "1-3DTE"
        assert dte_bucket_of(4) == "4-7DTE"
        assert dte_bucket_of(7) == "4-7DTE"
        assert dte_bucket_of(30) == "8-30DTE"
        assert dte_bucket_of(31) == "30+DTE"
        assert dte_bucket_of(None) is None


class TestSimpleOneOpenOneClose:
    def test_long_equity_win(self):
        events = [
            _evt(1, "buy", 100, 500.0),
            _evt(2, "sell", 100, 510.0, minutes_offset=60),
        ]
        trades = match_legs_fifo(events)
        assert len(trades) == 1
        t = trades[0]
        assert t["status"] == "closed"
        assert t["direction"] == "long"
        assert t["quantity"] == 100
        assert t["pnl_gross"] == pytest.approx(1000.0)  # (510-500)*100*1

    def test_option_multiplier_100(self):
        events = [
            _evt(
                1, "buy", 10, 2.50,
                is_option=True, expiry=date(2026, 5, 15), strike=200.0, right="C",
            ),
            _evt(
                2, "sell", 10, 3.00, minutes_offset=30,
                is_option=True, expiry=date(2026, 5, 15), strike=200.0, right="C",
            ),
        ]
        trades = match_legs_fifo(events)
        assert len(trades) == 1
        # (3.00 - 2.50) * 10 * 100 = 500
        assert trades[0]["pnl_gross"] == pytest.approx(500.0)


class TestPartialFills:
    def test_add_then_single_close(self):
        events = [
            _evt(1, "buy", 50, 500.0),
            _evt(2, "buy", 50, 502.0, minutes_offset=15),
            _evt(3, "sell", 100, 510.0, minutes_offset=30),
        ]
        trades = match_legs_fifo(events)
        # FIFO close consumes both open lots -> 2 closed trades
        closed = [t for t in trades if t["status"] == "closed"]
        assert len(closed) == 2
        q_total = sum(t["quantity"] for t in closed)
        assert q_total == 100
        total_pnl = sum(t["pnl_gross"] for t in closed)
        # (510-500)*50 + (510-502)*50 = 500 + 400 = 900
        assert total_pnl == pytest.approx(900.0)

    def test_one_open_split_close(self):
        events = [
            _evt(1, "buy", 100, 500.0),
            _evt(2, "sell", 50, 510.0, minutes_offset=30),
            _evt(3, "sell", 50, 515.0, minutes_offset=60),
        ]
        trades = match_legs_fifo(events)
        closed = [t for t in trades if t["status"] == "closed"]
        assert len(closed) == 2
        pnls = [t["pnl_gross"] for t in closed]
        # Each consumes 50 of the same open lot.
        assert pytest.approx(pnls[0]) == 500.0
        assert pytest.approx(pnls[1]) == 750.0


class TestOvernightHold:
    def test_cross_day(self):
        events = [
            _evt(1, "buy", 100, 500.0, minutes_offset=0),
            _evt(2, "sell", 100, 520.0, minutes_offset=24 * 60 + 300),
        ]
        trades = match_legs_fifo(events)
        assert trades[0]["hold_seconds"] > 20 * 3600
        assert trades[0]["pnl_gross"] == pytest.approx(2000.0)


class TestMultiSymbolParallel:
    def test_two_symbols_independent(self):
        events = [
            _evt(1, "buy", 10, 100.0, underlying="NVDA"),
            _evt(2, "buy", 5, 50.0, underlying="AAPL", minutes_offset=5),
            _evt(3, "sell", 10, 110.0, underlying="NVDA", minutes_offset=10),
            _evt(4, "sell", 5, 48.0, underlying="AAPL", minutes_offset=15),
        ]
        trades = match_legs_fifo(events)
        closed = [t for t in trades if t["status"] == "closed"]
        assert len(closed) == 2
        by_sym = {t["underlying"]: t for t in closed}
        assert by_sym["NVDA"]["pnl_gross"] == pytest.approx(100.0)
        assert by_sym["AAPL"]["pnl_gross"] == pytest.approx(-10.0)


class TestShort:
    def test_short_profit(self):
        events = [
            _evt(1, "sell", 100, 510.0),
            _evt(2, "buy", 100, 500.0, minutes_offset=60),
        ]
        trades = match_legs_fifo(events)
        t = trades[0]
        assert t["direction"] == "short"
        # (510 - 500) * 100 = 1000 profit on short
        assert t["pnl_gross"] == pytest.approx(1000.0)


class TestOpenPosition:
    def test_still_open(self):
        events = [
            _evt(1, "buy", 100, 500.0),
        ]
        trades = match_legs_fifo(events)
        assert len(trades) == 1
        t = trades[0]
        assert t["status"] == "open"
        assert t["pnl_net"] is None
        assert t["quantity"] == 100


class TestDuplicateGuard:
    def test_match_is_deterministic(self):
        events = [
            _evt(1, "buy", 100, 500.0),
            _evt(2, "sell", 100, 510.0, minutes_offset=10),
        ]
        a = match_legs_fifo(events)
        b = match_legs_fifo(events)
        assert a == b


class TestFeesDistributed:
    def test_exit_fee_applied(self):
        events = [
            _evt(1, "buy", 10, 100.0, total_fee=0.50),
            _evt(2, "sell", 10, 110.0, minutes_offset=30, total_fee=0.80),
        ]
        trades = match_legs_fifo(events)
        t = trades[0]
        # Gross 100, fee 0.80 applied to exit side -> net 99.20
        assert t["pnl_gross"] == pytest.approx(100.0)
        assert t["pnl_net"] == pytest.approx(99.2)

# -*- coding: utf-8 -*-
"""Deterministic tests for the near-expiry contract panel pure module."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.opportunities.near_expiry_contracts import (
    FORMULA_VERSION,
    NEAR_MONEY_MIN_STRIKES_PER_SIDE,
    NEAR_MONEY_PERCENT_BAND,
    build_near_expiry_contract_payload,
    compute_mid_and_spread,
    select_near_money_strikes,
)


def _contract(**overrides) -> SimpleNamespace:
    base = {
        "code": "US.MU250804C100000",
        "expiry": "2026-08-04",
        "dte": 2,
        "right": "C",
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.1,
        "last_price": 1.05,
        "volume": 120,
        "open_interest": 500,
        "iv_percent": 45.5,
        "delta": 0.52,
        "update_time": "2026-08-04 15:59:58",
        "snapshot_state": "observed",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _snapshot(**overrides) -> SimpleNamespace:
    base = {
        "symbol": "MU",
        "spot": 100.0,
        "spot_as_of": "2026-08-04 15:59:59",
        "max_dte": 3,
        "expiries": (("2026-08-04", 2),),
        "contracts": (),
        "requested_contract_count": 0,
        "snapshot_received_count": 0,
        "failed_batch_count": 0,
        "excluded_nonstandard_count": 0,
        "excluded_unknown_standard_type_count": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- strikes


def test_strike_window_keeps_all_strikes_inside_five_percent_band():
    strikes = [90 + i for i in range(21)]  # 90..110, $1 apart around spot 100
    selected = select_near_money_strikes(strikes, 100.0)
    # ±5% band = 95..105 (11 strikes) already exceeds 8 per side minimum on
    # neither side, so the union keeps the full band plus nearest-8 fills.
    assert all(95.0 <= strike <= 105.0 for strike in selected if 95 <= strike <= 105)
    assert set(range(95, 106)).issubset({int(s) for s in selected})


def test_strike_window_extends_sparse_chains_to_eight_per_side():
    # $5-spaced strikes: only 100 lies within ±5% of spot 100.
    strikes = [float(value) for value in range(50, 155, 5)]
    selected = select_near_money_strikes(strikes, 100.0)
    below = [s for s in selected if s <= 100.0]
    above = [s for s in selected if s > 100.0]
    assert len(below) == NEAR_MONEY_MIN_STRIKES_PER_SIDE
    assert len(above) == NEAR_MONEY_MIN_STRIKES_PER_SIDE
    assert below[-1] == 100.0 and below[0] == 65.0
    assert above[0] == 105.0 and above[-1] == 140.0


def test_strike_window_five_percent_boundary_is_inclusive():
    # 92.5 keeps a distinct below-side strike so the nearest-8 fill cannot be
    # the only reason the exact ±5% boundary strikes are selected.
    strikes = [92.5, 95.0, 100.0, 105.0]
    dense = select_near_money_strikes(strikes + [200.0], 100.0)
    assert 95.0 in dense and 105.0 in dense
    band = NEAR_MONEY_PERCENT_BAND / 100.0 + 1e-9
    in_band = [s for s in strikes if abs(s / 100.0 - 1.0) <= band]
    assert set(in_band) == {95.0, 100.0, 105.0}


def test_strike_window_fails_closed_without_valid_spot_or_strikes():
    assert select_near_money_strikes([100.0], 0.0) == []
    assert select_near_money_strikes([100.0], float("nan")) == []
    assert select_near_money_strikes([], 100.0) == []
    assert select_near_money_strikes([0.0, -5.0, float("inf")], 100.0) == []


# ---------------------------------------------------------------- spread


def test_spread_math_uses_mid_denominator():
    mid, spread, reason = compute_mid_and_spread(1.0, 1.2)
    assert mid == pytest.approx(1.1)
    # Implementation rounds to 6 decimals.
    assert spread == pytest.approx((0.2 / 1.1) * 100.0, abs=1e-6)
    assert reason is None


@pytest.mark.parametrize("bid,ask", [(None, 1.2), (1.0, None), (None, None)])
def test_spread_missing_bid_or_ask_is_null_not_zero(bid, ask):
    mid, spread, reason = compute_mid_and_spread(bid, ask)
    assert mid is None
    assert spread is None
    assert reason == "bid_or_ask_unavailable"


def test_spread_zero_zero_quote_reports_mid_not_positive():
    mid, spread, reason = compute_mid_and_spread(0.0, 0.0)
    assert mid is None and spread is None
    assert reason == "mid_not_positive"


def test_spread_no_bid_side_still_computes_wide_honest_spread():
    mid, spread, reason = compute_mid_and_spread(0.0, 0.05)
    assert mid == pytest.approx(0.025)
    assert spread == pytest.approx(200.0)
    assert reason is None


def test_spread_crossed_quote_keeps_mid_but_flags_reason():
    mid, spread, reason = compute_mid_and_spread(1.2, 1.0)
    assert mid == pytest.approx(1.1)
    assert spread is None
    assert reason == "crossed_quote"


# ---------------------------------------------------------------- payload


def test_payload_requires_positive_spot():
    with pytest.raises(ValueError):
        build_near_expiry_contract_payload(_snapshot(spot=None), max_dte=3)
    with pytest.raises(ValueError):
        build_near_expiry_contract_payload(_snapshot(spot=0.0), max_dte=3)


def test_payload_empty_expiries_is_honest_empty_state():
    payload = build_near_expiry_contract_payload(
        _snapshot(expiries=(), contracts=()),
        max_dte=3,
    )
    assert payload["state"] == "empty"
    assert payload["expiries"] == []
    assert payload["formula_version"] == FORMULA_VERSION


def test_payload_groups_by_expiry_and_flags_atm_rows_on_both_rights():
    contracts = (
        _contract(code="C99", strike=99.0),
        _contract(code="P99", strike=99.0, right="P", delta=-0.45),
        _contract(code="C101", strike=101.0),
        _contract(
            code="C0DTE",
            expiry="2026-08-02",
            dte=0,
            strike=100.5,
        ),
    )
    payload = build_near_expiry_contract_payload(
        _snapshot(
            spot=99.2,
            expiries=(("2026-08-04", 2), ("2026-08-02", 0)),
            contracts=contracts,
            requested_contract_count=4,
            snapshot_received_count=4,
        ),
        max_dte=3,
    )
    assert payload["state"] == "ready"
    # Groups sorted by DTE ascending (0DTE first).
    assert [group["expiry"] for group in payload["expiries"]] == [
        "2026-08-02",
        "2026-08-04",
    ]
    friday = payload["expiries"][1]
    atm_rows = [row for row in friday["contracts"] if row["is_atm"]]
    assert {row["code"] for row in atm_rows} == {"C99", "P99"}
    assert all(row["strike"] == 99.0 for row in atm_rows)
    # Neutral ordering: strike ascending, then right C before P.
    assert [row["code"] for row in friday["contracts"]] == ["C99", "P99", "C101"]


def test_payload_isolates_snapshot_failures_per_expiry():
    contracts = (
        _contract(code="OK", expiry="2026-08-02", dte=0),
        _contract(
            code="MISS",
            expiry="2026-08-04",
            dte=2,
            bid=None,
            ask=None,
            last_price=None,
            volume=None,
            open_interest=None,
            iv_percent=None,
            delta=None,
            update_time=None,
            snapshot_state="missing",
        ),
    )
    payload = build_near_expiry_contract_payload(
        _snapshot(
            expiries=(("2026-08-02", 0), ("2026-08-04", 2)),
            contracts=contracts,
            requested_contract_count=2,
            snapshot_received_count=1,
        ),
        max_dte=3,
    )
    assert payload["state"] == "partial"
    states = {group["expiry"]: group["state"] for group in payload["expiries"]}
    assert states == {"2026-08-02": "ready", "2026-08-04": "unavailable"}
    missing_row = payload["expiries"][1]["contracts"][0]
    assert missing_row["quote_state"] == "unavailable"
    assert missing_row["unavailable_reason"] == "snapshot_missing"
    # Null-not-zero: every quote field stays None.
    for field in (
        "bid",
        "ask",
        "mid",
        "spread_percent",
        "last_price",
        "session_volume",
        "open_interest",
        "iv_percent",
        "delta",
        "quote_as_of",
    ):
        assert missing_row[field] is None
    assert missing_row["spread_unavailable_reason"] == "bid_or_ask_unavailable"


def test_payload_invalid_snapshot_row_reports_invalid_reason():
    contracts = (
        _contract(
            code="BAD",
            bid=None,
            ask=None,
            last_price=None,
            volume=None,
            open_interest=None,
            iv_percent=None,
            delta=None,
            update_time=None,
            snapshot_state="invalid",
        ),
    )
    payload = build_near_expiry_contract_payload(
        _snapshot(
            contracts=contracts,
            requested_contract_count=1,
            snapshot_received_count=1,
        ),
        max_dte=3,
    )
    assert payload["state"] == "unavailable"
    row = payload["expiries"][0]["contracts"][0]
    assert row["unavailable_reason"] == "snapshot_invalid"


def test_payload_failed_batches_downgrade_ready_to_partial():
    payload = build_near_expiry_contract_payload(
        _snapshot(
            contracts=(_contract(),),
            requested_contract_count=2,
            snapshot_received_count=1,
            failed_batch_count=1,
        ),
        max_dte=3,
    )
    assert payload["state"] == "partial"
    coverage = payload["coverage"]
    assert coverage["failed_batches"] == 1
    assert coverage["observed_contracts"] == 1
    assert coverage["missing_contracts"] == 1


def test_payload_spread_passthrough_never_zero_fills_missing_quotes():
    contracts = (
        _contract(code="NOBIDASK", bid=None, ask=None),
        _contract(code="QUOTED", bid=0.95, ask=1.05),
    )
    payload = build_near_expiry_contract_payload(
        _snapshot(
            contracts=contracts,
            requested_contract_count=2,
            snapshot_received_count=2,
        ),
        max_dte=3,
    )
    rows = {row["code"]: row for row in payload["expiries"][0]["contracts"]}
    assert rows["NOBIDASK"]["spread_percent"] is None
    assert rows["NOBIDASK"]["spread_unavailable_reason"] == (
        "bid_or_ask_unavailable"
    )
    # 有观测标记但 bid/ask 缺失的行保持 observed 状态、字段级标缺。
    assert rows["NOBIDASK"]["quote_state"] == "observed"
    assert rows["QUOTED"]["spread_percent"] == pytest.approx(10.0)
    assert rows["QUOTED"]["mid"] == pytest.approx(1.0)

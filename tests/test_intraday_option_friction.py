# -*- coding: utf-8 -*-
"""Deterministic offline tests for intraday 0–7DTE friction snapshots."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api.v1.schemas.opportunities import IntradayOptionContractFriction
from src.services import intraday_option_friction as friction_module
from src.services.intraday_option_friction import (
    DEFAULT_WALL_LANE_WAIT_SECONDS,
    OPTION_FRICTION_SELECTION_BASIS,
    OPTION_FRICTION_SOURCE,
    IntradayOptionFrictionService,
    build_option_candidate_supplement,
    build_option_contract_friction_snapshot,
    direction_from_candidate,
)

_NOW = datetime(2026, 8, 25, 14, 30, 0, tzinfo=timezone.utc)  # 10:30 ET


def _contract(**overrides) -> SimpleNamespace:
    values = {
        "code": "US.NVDA260829C00100000",
        "expiry": "2026-08-29",
        "dte": 4,
        "right": "C",
        "strike": 100.0,
        "bid": 4.9,
        "ask": 5.1,
        "last_price": 5.0,
        "volume": 10,
        "open_interest": 100,
        "iv_percent": 40.0,
        "delta": 0.5,
        "update_time": "2026-08-25 10:29:30",  # naive Moomoo ET
        "snapshot_state": "observed",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _snapshot(*contracts, **overrides) -> SimpleNamespace:
    values = {
        "symbol": "NVDA",
        "spot": 100.0,
        "spot_as_of": "2026-08-25 10:29:31",
        "fetched_at": _NOW - timedelta(seconds=1),
        "max_dte": 7,
        "expiries": tuple(
            sorted(
                {
                    (contract.expiry, contract.dte)
                    for contract in contracts
                },
                key=lambda item: (item[1], item[0]),
            )
        ),
        "contracts": tuple(contracts),
        "requested_contract_count": len(contracts),
        "snapshot_received_count": len(contracts),
        "failed_batch_count": 0,
        "excluded_nonstandard_count": 0,
        "excluded_unknown_standard_type_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _selection_snapshot() -> SimpleNamespace:
    return _snapshot(
        _contract(
            code="C3",
            expiry="2026-08-28",
            dte=3,
            strike=100.0,
        ),
        _contract(code="C99", strike=99.0),
        _contract(code="C101", strike=101.0),
        _contract(code="P99", right="P", strike=99.0, delta=-0.45),
        _contract(code="P101", right="P", strike=101.0, delta=-0.55),
        _contract(
            code="C5",
            expiry="2026-08-30",
            dte=5,
            strike=100.0,
        ),
    )


def _build(snapshot=None, *, direction="up", now=_NOW, max_age=90.0):
    return build_option_contract_friction_snapshot(
        snapshot if snapshot is not None else _selection_snapshot(),
        ticker="nvda",
        direction=direction,
        now=now,
        quote_max_age_seconds=max_age,
    )


def test_default_fetcher_bounds_wall_lane_wait(monkeypatch):
    calls = []

    def fake_fetch(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return "snapshot"

    monkeypatch.setattr(
        "data_provider.moomoo_options.fetch_near_expiry_chain_moomoo",
        fake_fetch,
    )
    result = friction_module._default_chain_fetcher(
        "NVDA",
        max_dte=7,
        ref_date=date(2026, 8, 25),
    )
    assert result == "snapshot"
    assert calls == [
        (
            "NVDA",
            {
                "max_dte": 7,
                "ref_date": date(2026, 8, 25),
                "lane_wait_seconds": DEFAULT_WALL_LANE_WAIT_SECONDS,
            },
        )
    ]


def test_selects_from_full_pool_with_stable_dte_tiebreak():
    result = _build()

    assert result["state"] == "ready"
    assert result["contract_code"] == "C3"
    assert result["right"] == "C"
    assert result["direction"] == "up"
    assert result["dte"] == 3
    assert result["strike"] == 100.0
    assert result["underlying_spot"] == 100.0
    assert result["mid"] == pytest.approx(5.0)
    assert result["spread_percent"] == pytest.approx(4.0, abs=1e-6)
    assert result["delta"] == 0.5
    assert result["quote_age_seconds"] == 30.0
    assert result["volume"] == 10
    assert result["open_interest"] == 100
    assert result["iv_percent"] == 40.0
    assert result["source"] == OPTION_FRICTION_SOURCE
    assert result["selection_basis"] == OPTION_FRICTION_SELECTION_BASIS
    assert result["unavailable_reason"] is None


def test_down_direction_selects_put_from_same_deterministic_expiry():
    result = _build(direction="DOWN")

    assert result["state"] == "ready"
    assert result["contract_code"] == "P99"
    assert result["right"] == "P"
    assert result["delta"] == -0.45


@pytest.mark.parametrize("delta", [0.35, 0.65])
def test_absolute_delta_hard_gate_is_inclusive(delta):
    result = _build(_snapshot(_contract(delta=delta)))
    assert result["state"] == "ready"
    assert result["delta"] == delta


@pytest.mark.parametrize(
    "dte,expiry,bid,ask,expected_state",
    [
        (0, "2026-08-25", 4.8, 5.2, "ready"),   # exactly 8%
        (0, "2026-08-25", 4.79, 5.21, "unavailable"),
        (1, "2026-08-26", 4.75, 5.25, "ready"),  # exactly 10%
        (1, "2026-08-26", 4.74, 5.26, "unavailable"),
    ],
)
def test_dte_specific_displayed_spread_hard_gates(
    dte, expiry, bid, ask, expected_state
):
    result = _build(
        _snapshot(_contract(dte=dte, expiry=expiry, bid=bid, ask=ask))
    )
    assert result["state"] == expected_state
    if expected_state == "unavailable":
        assert result["unavailable_reason"] == (
            "no_eligible_contract:option_spread_too_wide"
        )


@pytest.mark.parametrize(
    "dte,expiry,quote_as_of,expected_state",
    [
        (0, "2026-08-25", "2026-08-25 10:29:30", "ready"),
        (0, "2026-08-25", "2026-08-25 10:29:29", "unavailable"),
        (1, "2026-08-26", "2026-08-25 10:28:30", "ready"),
        (1, "2026-08-26", "2026-08-25 10:28:29", "unavailable"),
        (7, "2026-09-01", "2026-08-25 10:28:30", "ready"),
    ],
)
def test_dte_specific_quote_age_hard_gates(
    dte, expiry, quote_as_of, expected_state
):
    result = _build(
        _snapshot(_contract(dte=dte, expiry=expiry, update_time=quote_as_of))
    )
    assert result["state"] == expected_state
    if expected_state == "unavailable":
        assert result["unavailable_reason"] == "no_eligible_contract:quote_stale"


def test_injected_quote_age_can_tighten_but_never_relax_hard_gate():
    quote = _snapshot(
        _contract(dte=1, expiry="2026-08-26", update_time="2026-08-25 10:28:40")
    )
    assert _build(quote, max_age=45)["state"] == "unavailable"
    assert _build(quote, max_age=999)["state"] == "ready"

    stale = _snapshot(
        _contract(dte=1, expiry="2026-08-26", update_time="2026-08-25 10:28:29")
    )
    relaxed = _build(stale, max_age=999)
    assert relaxed["state"] == "unavailable"
    assert relaxed["unavailable_reason"] == "no_eligible_contract:quote_stale"


@pytest.mark.parametrize(
    "winner,loser",
    [
        ({"bid": 4.95, "ask": 5.05}, {}),
        ({"delta": 0.5}, {"delta": 0.55}),
        ({"volume": 20}, {"volume": 10}),
        ({"open_interest": 200}, {"open_interest": 100}),
        (
            {"dte": 3, "expiry": "2026-08-28"},
            {"dte": 4, "expiry": "2026-08-29"},
        ),
        ({"strike": 100.0}, {"strike": 101.0}),
    ],
)
def test_ranking_tuple_tiebreaks_are_applied_in_documented_order(winner, loser):
    rows = (
        _contract(code="WIN", **winner),
        _contract(code="LOSE", **loser),
    )
    forward = _build(_snapshot(*rows))
    reverse = _build(_snapshot(*reversed(rows)))
    assert forward["contract_code"] == "WIN"
    assert reverse["contract_code"] == "WIN"


def test_stable_contract_code_tiebreak_is_independent_of_provider_row_order():
    rows = (_contract(code="B"), _contract(code="A"))
    assert _build(_snapshot(*rows))["contract_code"] == "A"
    assert _build(_snapshot(*reversed(rows)))["contract_code"] == "A"


def test_missing_liquidity_metrics_sort_last_but_remain_nullable_outputs():
    observed = _contract(code="OBSERVED", volume=1, open_interest=1)
    missing = _contract(code="MISSING", volume=None, open_interest=None)
    result = _build(_snapshot(missing, observed))
    assert result["contract_code"] == "OBSERVED"

    only_missing = _build(_snapshot(missing))
    assert only_missing["state"] == "ready"
    assert only_missing["volume"] is None
    assert only_missing["open_interest"] is None


def test_no_eligible_contract_reports_all_stable_rejection_reasons():
    result = _build(
        _snapshot(
            _contract(code="NO_BID", bid=0.0),
            _contract(code="BAD_DELTA", delta=0.2),
        )
    )
    assert result["state"] == "unavailable"
    assert result["unavailable_reason"] == (
        "no_eligible_contract:bid_not_positive,option_delta_out_of_range"
    )


def test_p0_supplement_returns_at_most_three_and_one_per_expiry():
    snapshot = _snapshot(
        _contract(
            code="E0_WIDE",
            dte=0,
            expiry="2026-08-25",
            strike=100,
            bid=4.8,
            ask=5.2,
        ),
        _contract(
            code="E0_TIGHT",
            dte=0,
            expiry="2026-08-25",
            strike=101,
            bid=4.95,
            ask=5.05,
        ),
        _contract(code="E1", dte=1, expiry="2026-08-26"),
        _contract(code="E2", dte=2, expiry="2026-08-27"),
        _contract(code="E3", dte=3, expiry="2026-08-28"),
    )

    result = build_option_candidate_supplement(
        snapshot,
        ticker="NVDA",
        direction="up",
        now=_NOW,
        max_candidates=3,
    )

    assert result["state"] == "ready"
    assert [row["contract_code"] for row in result["candidates"]] == [
        "E0_TIGHT",
        "E1",
        "E2",
    ]
    assert len({row["expiry"] for row in result["candidates"]}) == 3
    assert all(0 <= row["dte"] <= 7 for row in result["candidates"])
    assert all(row["right"] == "C" for row in result["candidates"])


def test_p0_supplement_failure_is_explicit_and_has_no_candidates():
    result = build_option_candidate_supplement(
        _snapshot(
            _contract(
                code="STALE",
                update_time="2026-08-25 10:27:00",
            ),
            _contract(code="WIDE", bid=4.0, ask=6.0),
        ),
        ticker="NVDA",
        direction="up",
        now=_NOW,
    )

    assert result["state"] == "unavailable"
    assert result["candidates"] == []
    assert result["unavailable_reason"] == (
        "no_executable_candidate:quote_stale,spread_too_wide"
    )


def test_fixed_result_shape_is_present_for_ready_and_unavailable_states():
    required = {
        "state",
        "unavailable_reason",
        "execution_policy",
        "execution_allowed",
        "ticker",
        "direction",
        "contract_code",
        "right",
        "underlying_spot",
        "strike",
        "expiry",
        "dte",
        "bid",
        "ask",
        "mid",
        "spread_percent",
        "delta",
        "volume",
        "open_interest",
        "iv_percent",
        "quote_as_of",
        "quote_age_seconds",
        "fetched_at",
        "source",
        "selection_basis",
    }
    assert set(_build()) == required
    assert set(_build(direction="flat")) == required


def test_api_schema_accepts_ready_and_fail_closed_future_quote_evidence():
    ready = IntradayOptionContractFriction(**_build())
    assert ready.state == "ready"
    future = IntradayOptionContractFriction(
        **_build(now=_NOW - timedelta(seconds=36))
    )
    assert future.state == "unavailable"
    assert future.quote_age_seconds is None
    assert future.unavailable_reason == (
        "no_eligible_contract:quote_as_of_in_future"
    )

    zero_dte = IntradayOptionContractFriction(
        **_build(
            _snapshot(
                _contract(dte=0, expiry="2026-08-25", iv_percent=None)
            )
        )
    )
    assert zero_dte.dte == 0
    assert zero_dte.iv_percent is None
    assert "intraday-option-friction/v2" in zero_dte.selection_basis


@pytest.mark.parametrize(
    "direction,reason",
    [("flat", "direction_unavailable"), ("", "direction_unavailable")],
)
def test_invalid_direction_fails_before_contract_selection(direction, reason):
    result = _build(direction=direction)
    assert result["state"] == "unavailable"
    assert result["unavailable_reason"] == reason
    assert result["contract_code"] is None


def test_no_eligible_expiry_and_missing_directional_contract_fail_closed():
    no_expiry = _snapshot(
        _contract(expiry="2026-09-02", dte=8),
    )
    result = _build(no_expiry)
    assert result["unavailable_reason"] == "no_eligible_0_7dte_expiry"

    calls_only = _snapshot(_contract(code="CALL", right="C"))
    result = _build(calls_only, direction="down")
    assert result["unavailable_reason"] == "matching_right_contract_unavailable"
    assert result["dte"] is None


def test_invalid_quote_is_filtered_before_ranking_another_eligible_contract():
    snapshot = _snapshot(
        _contract(
            code="ATM_MISSING",
            strike=100.0,
            bid=None,
            ask=None,
            delta=None,
            update_time=None,
            snapshot_state="missing",
        ),
        _contract(code="FAR_READY", strike=105.0),
    )
    result = _build(snapshot)

    assert result["contract_code"] == "FAR_READY"
    assert result["state"] == "ready"
    assert result["unavailable_reason"] is None


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"bid": None}, "bid_not_positive"),
        ({"ask": None}, "ask_unavailable"),
        ({"bid": 0.0, "ask": 0.0}, "bid_not_positive"),
        ({"bid": 5.2, "ask": 5.0}, "crossed_quote"),
        ({"delta": None}, "option_delta_unavailable"),
        ({"delta": 0.0}, "option_delta_unavailable"),
        ({"delta": 1.01}, "option_delta_unavailable"),
        ({"delta": float("nan")}, "option_delta_unavailable"),
        ({"delta": 0.349}, "option_delta_out_of_range"),
        ({"delta": 0.651}, "option_delta_out_of_range"),
        ({"delta": -0.5}, "option_delta_direction_mismatch"),
        ({"update_time": None}, "quote_as_of_unavailable"),
        ({"update_time": "not-a-time"}, "quote_as_of_invalid"),
    ],
)
def test_required_quote_fields_fail_closed(overrides, reason):
    result = _build(_snapshot(_contract(**overrides)))
    assert result["state"] == "unavailable"
    assert result["unavailable_reason"] == f"no_eligible_contract:{reason}"


@pytest.mark.parametrize(
    "now,quote_as_of,expected_age",
    [
        # EDT is UTC-4 in August.
        (
            datetime(2026, 8, 25, 14, 30, tzinfo=timezone.utc),
            "2026-08-25 10:29:00",
            60.0,
        ),
        # EST is UTC-5 in January.
        (
            datetime(2026, 1, 20, 15, 30, tzinfo=timezone.utc),
            "2026-01-20 10:29:00",
            60.0,
        ),
    ],
)
def test_naive_moomoo_quote_time_is_interpreted_in_new_york(
    now, quote_as_of, expected_age
):
    result = _build(
        _snapshot(_contract(update_time=quote_as_of)),
        now=now,
    )
    assert result["state"] == "ready"
    assert result["quote_age_seconds"] == expected_age


def test_stale_and_materially_future_quotes_fail_closed():
    stale = _build(
        _snapshot(_contract(update_time="2026-08-25 10:28:29")),
    )
    assert stale["quote_age_seconds"] is None
    assert stale["unavailable_reason"] == "no_eligible_contract:quote_stale"

    future = _build(
        _snapshot(_contract(update_time="2026-08-25 10:30:06")),
    )
    assert future["quote_age_seconds"] is None
    assert future["unavailable_reason"] == (
        "no_eligible_contract:quote_as_of_in_future"
    )

    small_clock_skew = _build(
        _snapshot(_contract(update_time="2026-08-25 10:30:03")),
    )
    assert small_clock_skew["state"] == "unavailable"
    assert small_clock_skew["quote_age_seconds"] is None
    assert small_clock_skew["unavailable_reason"] == (
        "no_eligible_contract:quote_as_of_in_future"
    )


def test_chain_none_and_malformed_spot_fail_closed():
    missing = build_option_contract_friction_snapshot(
        None,
        ticker="NVDA",
        direction="up",
        now=_NOW,
    )
    assert missing["unavailable_reason"] == "chain_unavailable"

    malformed = _build(_snapshot(_contract(), spot=0.0))
    assert malformed["unavailable_reason"] == "chain_payload_invalid"


def test_quote_qualified_supplement_preserves_closed_zero_dte_execution_window():
    now = datetime.fromisoformat("2026-08-25T14:31:00-04:00")
    result = build_option_candidate_supplement(
        _snapshot(
            _contract(code="C0", expiry="2026-08-25", dte=0, update_time="2026-08-25 14:30:45"),
            _contract(code="C4", update_time="2026-08-25 14:30:45"),
        ),
        ticker="NVDA", direction="up", now=now,
    )
    assert result["state"] == "ready"  # quote quality is not manual permission
    rows = {row["dte"]: row for row in result["candidates"]}
    assert rows[0]["execution_allowed"] is False
    assert rows[4]["execution_allowed"] is True
    assert result["execution_policy"]["zero_dte_entry_cutoff_et"] == "14:30"


def test_quote_qualified_snapshot_keeps_half_day_closed_time_context():
    now = datetime.fromisoformat("2026-11-27T12:50:00-05:00")
    result = _build(_snapshot(
        _contract(expiry="2026-12-01", update_time="2026-11-27 12:49:45"),
    ), now=now)
    assert result["state"] == "ready"
    assert result["execution_allowed"] is False
    assert result["execution_policy"]["exit_deadline_et"] == "12:45"
    parsed = IntradayOptionContractFriction.model_validate(result)
    assert parsed.execution_policy.close_et == "13:00"


def test_direction_from_candidate_uses_displacement_sign_only():
    candidate = {
        "recent_displacement": {"net_move_atr": -0.8},
        "session_bursts": {
            "current": {"direction": "up"},
            "legs": [{"grade": "strong", "direction": "up"}],
        },
    }
    assert direction_from_candidate(candidate) == "down"
    assert direction_from_candidate(
        {"recent_displacement": {"net_move_atr": 0.5}}
    ) == "up"
    assert direction_from_candidate(
        {"recent_displacement": {"net_move_atr": 0.0}}
    ) is None
    assert direction_from_candidate({}) is None


def test_service_passes_7dte_and_ref_date_and_reuses_raw_chain_for_directions():
    calls = []

    def fetcher(ticker, *, max_dte, ref_date):
        calls.append((ticker, max_dte, ref_date))
        return _selection_snapshot()

    service = IntradayOptionFrictionService(fetcher, clock=lambda: _NOW)
    call_result = service.get_snapshot("nvda", "up", ref_date=date(2026, 8, 25))
    put_result = service.get_snapshot("NVDA", "down", ref_date=date(2026, 8, 25))

    assert calls == [("NVDA", 7, date(2026, 8, 25))]
    assert call_result["contract_code"] == "C3"
    assert put_result["contract_code"] == "P99"


def test_completed_cache_expires_using_monotonic_clock():
    monotonic_value = [100.0]
    call_count = 0

    def fetcher(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return _selection_snapshot()

    service = IntradayOptionFrictionService(
        fetcher,
        cache_ttl_seconds=30,
        clock=lambda: _NOW,
        monotonic=lambda: monotonic_value[0],
    )
    service.get_snapshot("NVDA", "up")
    monotonic_value[0] += 29.9
    service.get_snapshot("NVDA", "up")
    assert call_count == 1
    monotonic_value[0] += 0.2
    service.get_snapshot("NVDA", "up")
    assert call_count == 2


def test_singleflight_concurrent_callers_share_one_provider_read():
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    call_count = 0
    call_lock = threading.Lock()

    def fetcher(*_args, **_kwargs):
        nonlocal call_count
        with call_lock:
            call_count += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return _selection_snapshot()

    service = IntradayOptionFrictionService(
        fetcher,
        clock=lambda: _NOW,
        singleflight_wait_seconds=2,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.get_snapshot, "NVDA", "up")
        assert fetch_started.wait(timeout=1)
        second = executor.submit(service.get_snapshot, "NVDA", "down")
        release_fetch.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert call_count == 1
    assert first_result["right"] == "C"
    assert second_result["right"] == "P"


def test_lane_busy_is_deferred_and_not_negative_cached():
    class MoomooWallLaneBusyError(RuntimeError):
        pass

    call_count = 0

    def fetcher(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise MoomooWallLaneBusyError("busy")
        return _selection_snapshot()

    service = IntradayOptionFrictionService(fetcher, clock=lambda: _NOW)
    deferred = service.get_snapshot("NVDA", "up")
    ready = service.get_snapshot("NVDA", "up")

    assert deferred["unavailable_reason"] == "deferred_wall_lane_busy"
    assert ready["state"] == "ready"
    assert call_count == 2


def test_provider_failure_is_short_negative_cached_and_never_raises():
    monotonic_value = [1.0]
    call_count = 0

    def fetcher(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("provider down")

    service = IntradayOptionFrictionService(
        fetcher,
        failure_cache_ttl_seconds=5,
        clock=lambda: _NOW,
        monotonic=lambda: monotonic_value[0],
    )
    first = service.get_snapshot("NVDA", "up")
    second = service.get_snapshot("NVDA", "up")
    assert first["unavailable_reason"] == "chain_fetch_failed"
    assert second["unavailable_reason"] == "chain_fetch_failed"
    assert call_count == 1
    monotonic_value[0] += 5.1
    service.get_snapshot("NVDA", "up")
    assert call_count == 2


def test_enrichment_returns_copies_and_only_touches_selected_tickers():
    calls = []

    def fetcher(ticker, **_kwargs):
        calls.append(ticker)
        return _selection_snapshot()

    service = IntradayOptionFrictionService(fetcher, clock=lambda: _NOW)
    nvda = {
        "ticker": "NVDA",
        "recent_displacement": {"net_move_atr": 0.8},
    }
    tsla = {
        "ticker": "TSLA",
        "recent_displacement": {"net_move_atr": -0.7},
    }
    payload = {
        "market_date_et": "2026-08-25",
        "candidates": [nvda, tsla],
        "session_state": "regular",
    }

    enriched = service.enrich_payload(payload, selected_tickers={"nvda"})

    assert enriched is not payload
    assert enriched["candidates"] is not payload["candidates"]
    assert enriched["candidates"][0] is not nvda
    assert enriched["candidates"][1] is not tsla
    assert enriched["candidates"][0]["option_contract_friction"]["state"] == "ready"
    assert "option_contract_friction" not in enriched["candidates"][1]
    assert "option_contract_friction" not in nvda
    assert "option_contract_friction" not in tsla
    assert calls == ["NVDA"]


def test_enrich_candidate_direction_failure_is_additive_and_does_not_fetch():
    call_count = 0

    def fetcher(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return _selection_snapshot()

    candidate = {"ticker": "NVDA", "recent_displacement": {"net_move_atr": 0}}
    service = IntradayOptionFrictionService(fetcher, clock=lambda: _NOW)
    result = service.enrich_candidate(candidate)

    assert result is not candidate
    assert result["option_contract_friction"]["unavailable_reason"] == (
        "direction_unavailable"
    )
    assert "option_contract_friction" not in candidate
    assert call_count == 0


def test_direction_falls_back_to_strict_opening_setup_before_30m_exists():
    candidate = {
        "ticker": "TSLA",
        "recent_displacement": {"state": "insufficient_bars"},
        "session_bursts": {
            "opening_impulse": {
                "state": "qualified",
                "alert_qualified": True,
                "direction": "down",
            }
        },
    }
    assert direction_from_candidate(candidate) == "down"
    candidate["session_bursts"]["opening_impulse"]["alert_qualified"] = False
    assert direction_from_candidate(candidate) is None

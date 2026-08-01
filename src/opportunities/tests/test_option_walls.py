from dataclasses import dataclass

import pytest

from src.opportunities.option_walls import build_option_wall_payload


@dataclass(frozen=True)
class _Contract:
    right: str
    strike: float
    open_interest: int
    volume: int
    gamma: float | None = None
    contract_size: float | None = None
    update_time: str | None = None
    expiry: str = "2026-07-24"
    implied_volatility: float | None = None
    code: str = ""
    dte: int | None = None
    bid: float | None = None
    ask: float | None = None
    mark: float | None = None


@dataclass(frozen=True)
class _Snapshot:
    spot: float
    contracts: tuple[_Contract, ...]
    expiries: tuple[str, ...] = ("2026-07-24", "2026-08-21")
    requested_contract_count: int = 4
    snapshot_received_count: int = 4
    valid_contract_count: int = 4
    failed_batch_count: int = 0
    excluded_nonstandard_count: int = 1
    excluded_unknown_standard_type_count: int = 0


def test_wall_aggregation_separates_observed_oi_volume_and_unsigned_gamma():
    snapshot = _Snapshot(
        spot=100.0,
        contracts=(
            _Contract("C", 105, 1_000, 250, 0.02, 100, "2026-07-22 10:00:00"),
            _Contract("C", 105, 500, 100, 0.01, 100, "2026-07-22 10:01:00"),
            _Contract("P", 95, 2_000, 400, 0.03, 100, "2026-07-22 10:00:30"),
            _Contract("P", 90, 250, 900, None, None, None),
        ),
    )

    payload = build_option_wall_payload(snapshot, dte_min=0, dte_max=45)

    assert payload["walls"]["call_oi"][0]["strike"] == 105
    assert payload["walls"]["call_oi"][0]["metric_value"] == 1_500
    assert payload["walls"]["put_oi"][0]["strike"] == 95
    assert payload["walls"]["put_volume"][0]["strike"] == 90
    assert payload["walls"]["gross_gamma_concentration"][0]["strike"] == 95
    assert payload["walls"]["gross_gamma_concentration"][0]["metric_value"] == 600_000
    assert payload["coverage"]["gamma_contracts"] == 3
    assert payload["quote_as_of"] == "2026-07-22 10:01:00"
    assert "dealer-position" in " ".join(payload["assumptions"])


def test_wall_levels_report_share_distance_and_tied_rank_deterministically():
    snapshot = _Snapshot(
        spot=100,
        requested_contract_count=2,
        snapshot_received_count=2,
        valid_contract_count=2,
        contracts=(
            _Contract("C", 95, 100, 1),
            _Contract("C", 105, 100, 1),
        ),
    )

    levels = build_option_wall_payload(
        snapshot,
        dte_min=0,
        dte_max=7,
    )["walls"]["call_oi"]

    assert [item["rank"] for item in levels] == [1, 1]
    assert [item["strike"] for item in levels] == [95, 105]
    assert [item["share_of_bucket_percent"] for item in levels] == [50, 50]
    assert [item["distance_from_spot_percent"] for item in levels] == [-5, 5]


def test_missing_gamma_does_not_manufacture_gamma_wall():
    snapshot = _Snapshot(
        spot=100,
        requested_contract_count=1,
        snapshot_received_count=1,
        valid_contract_count=1,
        contracts=(_Contract("P", 95, 100, 200),),
    )

    payload = build_option_wall_payload(snapshot, dte_min=0, dte_max=45)

    assert payload["walls"]["put_oi"]
    assert payload["walls"]["gross_gamma_concentration"] == []
    assert payload["coverage"]["gamma_contracts"] == 0


def test_atm_call_iv_is_selected_from_same_nearest_expiry_wall_snapshot():
    snapshot = _Snapshot(
        spot=100,
        requested_contract_count=4,
        snapshot_received_count=4,
        valid_contract_count=4,
        contracts=(
            _Contract(
                "C",
                95,
                100,
                10,
                expiry="2026-07-24",
                implied_volatility=0.41,
                code="call-95",
            ),
            _Contract(
                "C",
                101,
                100,
                10,
                expiry="2026-07-24",
                implied_volatility=0.425,
                code="call-101",
            ),
            _Contract(
                "C",
                100,
                100,
                10,
                expiry="2026-08-21",
                implied_volatility=0.99,
                code="later-call-100",
            ),
            _Contract(
                "P",
                100,
                100,
                10,
                expiry="2026-07-24",
                implied_volatility=0.88,
                code="put-100",
            ),
        ),
    )

    context = build_option_wall_payload(
        snapshot,
        dte_min=0,
        dte_max=45,
    )["atm_call_iv"]

    assert context == {
        "state": "ready",
        "expiry": "2026-07-24",
        "strike": 101.0,
        "atm_call_iv_percent": 42.5,
        "selection_method": (
            "nearest_expiry_atm_call_from_same_wall_snapshot"
        ),
    }


def test_atm_call_iv_fails_closed_when_exact_atm_snapshot_lacks_iv():
    snapshot = _Snapshot(
        spot=100,
        requested_contract_count=2,
        snapshot_received_count=2,
        valid_contract_count=2,
        contracts=(
            _Contract(
                "C",
                100,
                100,
                10,
                expiry="2026-07-24",
                implied_volatility=None,
                code="atm-without-iv",
            ),
            _Contract(
                "C",
                105,
                100,
                10,
                expiry="2026-07-24",
                implied_volatility=0.55,
                code="non-atm-with-iv",
            ),
        ),
    )

    context = build_option_wall_payload(
        snapshot,
        dte_min=0,
        dte_max=45,
    )["atm_call_iv"]

    assert context["state"] == "unavailable"
    assert context["expiry"] == "2026-07-24"
    assert context["strike"] == 100
    assert context["atm_call_iv_percent"] is None


def test_wall_levels_attach_bounded_expiry_breakdown_with_explicit_quote_markers():
    contracts = tuple(
        _Contract(
            "C",
            105,
            oi,
            10,
            expiry=expiry,
            dte=dte,
            implied_volatility=iv,
            update_time=update_time,
            code=f"call-105-{expiry}",
        )
        for expiry, dte, oi, iv, update_time in (
            ("2026-08-07", 7, 4_000, 0.42, "2026-07-31 10:00:00"),
            ("2026-08-14", 14, 3_000, None, None),
            ("2026-08-21", 21, 2_000, 0.39, "2026-07-31 10:00:01"),
            ("2026-08-28", 28, 700, 0.38, "2026-07-31 10:00:02"),
            ("2026-09-04", 35, 300, 0.37, "2026-07-31 10:00:03"),
        )
    )
    snapshot = _Snapshot(
        spot=100.0,
        requested_contract_count=5,
        snapshot_received_count=5,
        valid_contract_count=5,
        contracts=contracts,
    )

    level = build_option_wall_payload(snapshot, dte_min=0, dte_max=45)[
        "walls"
    ]["call_oi"][0]

    assert level["side"] == "call"
    assert level["metric_basis"] == "settled_open_interest_prior_session"
    breakdown = level["expiry_breakdown"]
    top = breakdown["top_expiries"]
    assert [entry["expiry"] for entry in top] == [
        "2026-08-07",
        "2026-08-14",
        "2026-08-21",
    ]
    assert [entry["dte"] for entry in top] == [7, 14, 21]
    assert [entry["metric_value"] for entry in top] == [4_000, 3_000, 2_000]
    assert [entry["contract_count"] for entry in top] == [1, 1, 1]

    # IV present -> partial (bid/ask/mark are not carried by the snapshot rows
    # and must stay explicit nulls, never zero-filled).
    assert top[0]["quote"] == {
        "iv_percent": 42.0,
        "bid": None,
        "ask": None,
        "mark": None,
        "quote_as_of": "2026-07-31 10:00:00",
    }
    assert top[0]["quote_evidence"] == "partial"
    # No quote field observed at all -> unavailable.
    assert top[1]["quote"] == {
        "iv_percent": None,
        "bid": None,
        "ask": None,
        "mark": None,
        "quote_as_of": None,
    }
    assert top[1]["quote_evidence"] == "unavailable"
    assert level["quote_evidence"] == "partial"

    other = breakdown["other"]
    assert other["expiry_count"] == 2
    assert other["metric_value"] == 1_000
    share_sum = sum(entry["share_of_level_percent"] for entry in top)
    share_sum += other["share_of_level_percent"]
    assert share_sum == pytest.approx(100.0, abs=1e-6)
    assert all(
        entry["share_of_level_percent"] <= 100.0 for entry in top
    )


def test_multi_contract_expiry_cell_never_misattributes_quotes():
    snapshot = _Snapshot(
        spot=100.0,
        requested_contract_count=2,
        snapshot_received_count=2,
        valid_contract_count=2,
        contracts=(
            _Contract(
                "C",
                105,
                1_000,
                10,
                expiry="2026-08-07",
                dte=7,
                implied_volatility=0.42,
                update_time="2026-07-31 10:00:00",
                code="call-a",
            ),
            _Contract(
                "C",
                105,
                500,
                10,
                expiry="2026-08-07",
                dte=7,
                implied_volatility=0.55,
                update_time="2026-07-31 10:00:01",
                code="call-b",
            ),
        ),
    )

    level = build_option_wall_payload(snapshot, dte_min=0, dte_max=45)[
        "walls"
    ]["call_oi"][0]

    entry = level["expiry_breakdown"]["top_expiries"][0]
    assert entry["contract_count"] == 2
    assert entry["metric_value"] == 1_500
    assert entry["quote"] == {
        "iv_percent": None,
        "bid": None,
        "ask": None,
        "mark": None,
        "quote_as_of": None,
    }
    assert entry["quote_evidence"] == "unavailable"
    assert level["quote_evidence"] == "unavailable"


def test_fully_quoted_row_reports_observed_evidence():
    snapshot = _Snapshot(
        spot=100.0,
        requested_contract_count=1,
        snapshot_received_count=1,
        valid_contract_count=1,
        contracts=(
            _Contract(
                "P",
                95,
                800,
                50,
                expiry="2026-08-07",
                dte=7,
                implied_volatility=0.5,
                update_time="2026-07-31 10:00:00",
                bid=1.2,
                ask=1.4,
                mark=1.3,
            ),
        ),
    )

    level = build_option_wall_payload(snapshot, dte_min=0, dte_max=45)[
        "walls"
    ]["put_oi"][0]

    assert level["side"] == "put"
    entry = level["expiry_breakdown"]["top_expiries"][0]
    assert entry["quote"] == {
        "iv_percent": 50.0,
        "bid": 1.2,
        "ask": 1.4,
        "mark": 1.3,
        "quote_as_of": "2026-07-31 10:00:00",
    }
    assert entry["quote_evidence"] == "observed"
    assert level["quote_evidence"] == "observed"


def test_metric_bases_follow_settlement_semantics_and_no_dealer_sign_keys():
    snapshot = _Snapshot(
        spot=100.0,
        requested_contract_count=1,
        snapshot_received_count=1,
        valid_contract_count=1,
        contracts=(
            _Contract(
                "C",
                105,
                1_000,
                250,
                0.02,
                100,
                "2026-07-31 10:00:00",
                expiry="2026-08-07",
                dte=7,
            ),
        ),
    )

    payload = build_option_wall_payload(snapshot, dte_min=0, dte_max=45)

    walls = payload["walls"]
    assert walls["call_oi"][0]["metric_basis"] == (
        "settled_open_interest_prior_session"
    )
    assert walls["call_volume"][0]["metric_basis"] == (
        "current_session_cumulative_volume"
    )
    assert walls["gross_gamma_concentration"][0]["metric_basis"] == (
        "model_from_settled_oi_and_snapshot_greeks"
    )
    assert walls["gross_gamma_concentration"][0]["side"] == "call_put_aggregate"

    forbidden = ("net_gex", "gamma_flip", "dealer")

    def _assert_no_dealer_sign_keys(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert not any(term in str(key).lower() for term in forbidden)
                _assert_no_dealer_sign_keys(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                _assert_no_dealer_sign_keys(nested)

    _assert_no_dealer_sign_keys(payload)


def test_invalid_spot_is_rejected():
    with pytest.raises(ValueError, match="positive underlying spot"):
        build_option_wall_payload(
            _Snapshot(spot=float("nan"), contracts=()),
            dte_min=0,
            dte_max=45,
        )

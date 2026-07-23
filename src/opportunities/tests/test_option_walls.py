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


def test_invalid_spot_is_rejected():
    with pytest.raises(ValueError, match="positive underlying spot"):
        build_option_wall_payload(
            _Snapshot(spot=float("nan"), contracts=()),
            dte_min=0,
            dte_max=45,
        )

# -*- coding: utf-8 -*-
"""Tests for strict CSV/OpenAPI identity and fill-set projection."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.journal.brokers.moomoo_statement import (
    StatementApiMatch,
    StatementReconciliation,
)
from src.journal.ledger.canonical import (
    FillObservationInput,
    OrderObservationInput,
    canonicalize_observations,
)
from src.journal.ledger.identity import (
    AcceptedCsvBatchSelection,
    IdentityProjectionError,
    project_reconciled_identities,
)


ET = ZoneInfo("America/New_York")
ORDER_TIME = datetime(2026, 7, 17, 9, 30, tzinfo=ET)
RECORDED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
CSV_ORDER_ID = "moomoo_csv_a1b2c3"
API_ORDER_ID = "1234567890"


def _sha(value: int) -> str:
    return f"{value:064x}"


def _reconciliation(
    *,
    analysis_ready: bool = True,
    fill_count_mismatches: int = 0,
) -> StatementReconciliation:
    return StatementReconciliation(
        analysis_ready=analysis_ready,
        window_start=ORDER_TIME - timedelta(minutes=1),
        window_end=ORDER_TIME + timedelta(minutes=1),
        statement_orders=1,
        api_orders=1,
        matched_orders=1,
        statement_only_orders=0,
        api_only_orders=0,
        matched_filled_orders=1,
        status_mismatches=0,
        fill_count_mismatches=fill_count_mismatches,
        filled_quantity_mismatches=0,
        fill_vwap_mismatches=0,
        fee_total_mismatches=0,
        fee_component_mismatches=0,
        ambiguous_identity_keys=0,
        statement_fee_total=Decimal("1.25"),
        api_fee_total=Decimal("1.25"),
        matches=(
            StatementApiMatch(
                statement_order_id=CSV_ORDER_ID,
                broker_order_id=API_ORDER_ID,
            ),
        ),
    )


def _order(
    observation_id: int,
    *,
    source_kind: str,
) -> OrderObservationInput:
    api = source_kind == "openapi"
    return OrderObservationInput(
        observation_id=observation_id,
        import_batch_id=2 if api else 1,
        batch_key="api-batch" if api else "csv-batch",
        source_kind=source_kind,
        observation_key=f"{source_kind}-order-{observation_id}",
        source_record_sha256=_sha(observation_id),
        broker="moomoo",
        account_key="margin-test",
        source_order_id=API_ORDER_ID if api else CSV_ORDER_ID,
        identity_strength="broker_stable" if api else "derived_strong",
        raw_symbol="US.AAPL" if api else "AAPL",
        asset_type="equity",
        underlying="AAPL",
        side="BUY",
        status="FILLED_ALL" if api else "FILLED",
        currency="USD",
        # Real OpenAPI rows commonly contain 1-999 ms that the CSV omits.
        ordered_at=(ORDER_TIME + timedelta(milliseconds=713)) if api else ORDER_TIME,
        order_quantity=Decimal("2"),
        evidence_level="fill_detail",
        fee_evidence_status="complete",
        recorded_at=RECORDED_AT + timedelta(minutes=observation_id),
        contract_multiplier=Decimal("1"),
        contract_multiplier_basis="asset_definition",
        source_row_number=observation_id,
        source_updated_at=ORDER_TIME + timedelta(seconds=1),
        order_price=Decimal("100"),
        order_amount=Decimal("200"),
        summary_filled_quantity=Decimal("2"),
        summary_average_fill_price=Decimal("100"),
        total_fee=Decimal("1.25"),
    )


def _fill(
    observation_id: int,
    *,
    source_kind: str,
    source_deal_id: str,
    quantity: str,
    price: str,
    second: int,
    milliseconds: int = 0,
) -> FillObservationInput:
    api = source_kind == "openapi"
    return FillObservationInput(
        observation_id=observation_id,
        import_batch_id=2 if api else 1,
        batch_key="api-batch" if api else "csv-batch",
        source_kind=source_kind,
        observation_key=f"{source_kind}-fill-{observation_id}",
        source_record_sha256=_sha(observation_id + 1000),
        broker="moomoo",
        account_key="margin-test",
        source_order_id=API_ORDER_ID if api else CSV_ORDER_ID,
        source_deal_id=source_deal_id,
        identity_strength="broker_stable" if api else "derived_weak",
        raw_symbol="US.AAPL" if api else "AAPL",
        asset_type="equity",
        underlying="AAPL",
        side="BUY",
        currency="USD",
        filled_at=ORDER_TIME + timedelta(
            seconds=second,
            milliseconds=milliseconds,
        ),
        quantity=Decimal(quantity),
        price=Decimal(price),
        recorded_at=RECORDED_AT + timedelta(minutes=observation_id),
        contract_multiplier=Decimal("1"),
        contract_multiplier_basis="asset_definition",
        source_row_number=observation_id,
        amount=Decimal(quantity) * Decimal(price),
    )


def _orders() -> list[OrderObservationInput]:
    return [_order(1, source_kind="csv"), _order(2, source_kind="openapi")]


def test_projects_order_and_unique_exact_deal_links_without_using_time() -> None:
    csv_fills = [
        _fill(
            11,
            source_kind="csv",
            source_deal_id="csv-fill-a",
            quantity="1",
            price="99",
            second=1,
        ),
        _fill(
            12,
            source_kind="csv",
            source_deal_id="csv-fill-b",
            quantity="1",
            price="101",
            second=2,
        ),
    ]
    api_fills = [
        _fill(
            21,
            source_kind="openapi",
            source_deal_id="DEAL-B",
            quantity="1",
            price="101",
            second=2,
            milliseconds=1,
        ),
        _fill(
            22,
            source_kind="openapi",
            source_deal_id="DEAL-A",
            quantity="1",
            price="99",
            second=1,
            milliseconds=999,
        ),
    ]

    result = project_reconciled_identities(
        reconciliation=_reconciliation(),
        order_observations=_orders(),
        fill_observations=[*csv_fills, *api_fills],
    )

    assert len(result.order_links) == 1
    assert result.order_links[0].statement_order_id == CSV_ORDER_ID
    assert result.order_links[0].broker_order_id == API_ORDER_ID
    assert len(result.deal_links) == 2
    assert result.fill_set_attestations == ()
    assert {
        (item.statement_deal_id, item.broker_deal_id)
        for item in result.deal_links
    } == {("csv-fill-a", "DEAL-A"), ("csv-fill-b", "DEAL-B")}

    # Source identity/provenance stays immutable; aliases are additive.
    projected_csv = [
        item for item in result.fills if item.source_kind == "csv"
    ]
    assert {item.source_deal_id for item in projected_csv} == {
        "csv-fill-a",
        "csv-fill-b",
    }
    assert {item.canonical_order_id for item in result.fills} == {API_ORDER_ID}
    assert {item.canonical_deal_id for item in result.fills} == {
        "DEAL-A",
        "DEAL-B",
    }
    assert result.deal_links[0].statement_evidence[0].source_record_sha256

    canonical = canonicalize_observations(result.orders, result.fills)
    assert canonical.analysis_ready is True
    assert len(canonical.orders) == 1
    assert len(canonical.fills) == 2


def test_unique_economics_with_a_different_second_uses_fill_set_attestation() -> None:
    csv_fill = _fill(
        11,
        source_kind="csv",
        source_deal_id="csv-fill-a",
        quantity="2",
        price="100",
        second=1,
    )
    api_fill = _fill(
        21,
        source_kind="openapi",
        source_deal_id="DEAL-A",
        quantity="2",
        price="100",
        second=2,
        milliseconds=100,
    )

    result = project_reconciled_identities(
        reconciliation=_reconciliation(),
        order_observations=_orders(),
        fill_observations=[csv_fill, api_fill],
    )

    assert result.deal_links == ()
    assert len(result.fill_set_attestations) == 1
    assert {item.source_kind: item.fill_set_role for item in result.fills} == {
        "csv": "attested_shadow",
        "openapi": "authoritative",
    }
    canonical = canonicalize_observations(result.orders, result.fills)
    assert canonical.analysis_ready is True
    assert len(canonical.fills) == 1
    assert canonical.fills[0].evidence.filled_at == api_fill.filled_at


def test_ambiguous_equal_fills_use_authoritative_and_shadow_sets() -> None:
    # Timestamps are all distinct, but equal qty/price rows are not linked by
    # proximity because time is not independent deal-identity evidence.
    csv_fills = [
        _fill(
            11,
            source_kind="csv",
            source_deal_id="csv-fill-a",
            quantity="1",
            price="100",
            second=1,
        ),
        _fill(
            12,
            source_kind="csv",
            source_deal_id="csv-fill-b",
            quantity="1",
            price="100",
            second=8,
        ),
    ]
    api_fills = [
        _fill(
            21,
            source_kind="openapi",
            source_deal_id="DEAL-A",
            quantity="1",
            price="100",
            second=1,
            milliseconds=713,
        ),
        _fill(
            22,
            source_kind="openapi",
            source_deal_id="DEAL-B",
            quantity="1",
            price="100",
            second=8,
            milliseconds=409,
        ),
    ]

    result = project_reconciled_identities(
        reconciliation=_reconciliation(),
        order_observations=_orders(),
        fill_observations=[*csv_fills, *api_fills],
    )

    assert result.deal_links == ()
    assert len(result.fill_set_attestations) == 1
    attestation = result.fill_set_attestations[0]
    assert attestation.csv_summary.count == 2
    assert attestation.api_summary.count == 2
    assert attestation.csv_summary.quantity == Decimal("2")
    assert attestation.api_summary.quantity == Decimal("2")
    assert attestation.csv_summary.vwap == Decimal("100")
    assert attestation.api_summary.vwap == Decimal("100")
    assert len(attestation.statement_evidence) == 2
    assert len(attestation.api_evidence) == 2

    roles = {
        item.source_kind: item.fill_set_role for item in result.fills
    }
    assert roles == {"csv": "attested_shadow", "openapi": "authoritative"}
    assert {
        item.fill_set_attestation_key for item in result.fills
    } == {attestation.attestation_key}
    assert all(
        item.canonical_deal_id is None
        for item in result.fills
        if item.source_kind == "csv"
    )

    canonical = canonicalize_observations(result.orders, result.fills)
    assert canonical.analysis_ready is True
    assert [item.source_deal_id for item in canonical.fills] == [
        "DEAL-A",
        "DEAL-B",
    ]
    assert canonical.provenance.shadowed_fill_observation_count == 2


def test_economically_equal_but_non_identical_rows_use_set_attestation() -> None:
    csv_fills = [
        _fill(
            11,
            source_kind="csv",
            source_deal_id="csv-fill-a",
            quantity="1",
            price="99",
            second=1,
        ),
        _fill(
            12,
            source_kind="csv",
            source_deal_id="csv-fill-b",
            quantity="1",
            price="101",
            second=2,
        ),
    ]
    api_fills = [
        _fill(
            21,
            source_kind="openapi",
            source_deal_id="DEAL-A",
            quantity="0.5",
            price="98",
            second=1,
            milliseconds=713,
        ),
        _fill(
            22,
            source_kind="openapi",
            source_deal_id="DEAL-B",
            quantity="1.5",
            price="100.6666666666666666666666667",
            second=2,
            milliseconds=409,
        ),
    ]

    result = project_reconciled_identities(
        reconciliation=_reconciliation(),
        order_observations=_orders(),
        fill_observations=[*csv_fills, *api_fills],
    )

    assert result.deal_links == ()
    assert len(result.fill_set_attestations) == 1
    assert result.fill_set_attestations[0].csv_summary.vwap == Decimal("100")
    assert result.fill_set_attestations[0].api_summary.vwap == Decimal(
        "100.000000000000000000000000"
    )


@pytest.mark.parametrize(
    ("api_fills", "message"),
    [
        (
            [
                _fill(
                    21,
                    source_kind="openapi",
                    source_deal_id="DEAL-A",
                    quantity="2",
                    price="100",
                    second=1,
                    milliseconds=713,
                )
            ],
            "count",
        ),
        (
            [
                _fill(
                    21,
                    source_kind="openapi",
                    source_deal_id="DEAL-A",
                    quantity="1",
                    price="99",
                    second=1,
                    milliseconds=713,
                ),
                _fill(
                    22,
                    source_kind="openapi",
                    source_deal_id="DEAL-B",
                    quantity="1.1",
                    price="100.9090909090909090909",
                    second=2,
                    milliseconds=409,
                ),
            ],
            "quantity",
        ),
        (
            [
                _fill(
                    21,
                    source_kind="openapi",
                    source_deal_id="DEAL-A",
                    quantity="1",
                    price="99",
                    second=1,
                    milliseconds=713,
                ),
                _fill(
                    22,
                    source_kind="openapi",
                    source_deal_id="DEAL-B",
                    quantity="1",
                    price="102",
                    second=2,
                    milliseconds=409,
                ),
            ],
            "VWAP",
        ),
    ],
)
def test_projection_refuses_unproved_fill_set(
    api_fills: list[FillObservationInput],
    message: str,
) -> None:
    csv_fills = [
        _fill(
            11,
            source_kind="csv",
            source_deal_id="csv-fill-a",
            quantity="1",
            price="99",
            second=1,
        ),
        _fill(
            12,
            source_kind="csv",
            source_deal_id="csv-fill-b",
            quantity="1",
            price="101",
            second=2,
        ),
    ]

    with pytest.raises(IdentityProjectionError, match=message):
        project_reconciled_identities(
            reconciliation=_reconciliation(),
            order_observations=_orders(),
            fill_observations=[*csv_fills, *api_fills],
        )


def test_projection_requires_passed_unambiguous_order_reconciliation() -> None:
    with pytest.raises(IdentityProjectionError, match="analysis-ready"):
        project_reconciled_identities(
            reconciliation=_reconciliation(analysis_ready=False),
            order_observations=_orders(),
            fill_observations=[],
        )

    with pytest.raises(IdentityProjectionError, match="fill_count_mismatches"):
        project_reconciled_identities(
            reconciliation=_reconciliation(fill_count_mismatches=1),
            order_observations=_orders(),
            fill_observations=[],
        )


def test_projection_keeps_incremental_tail_as_broker_stable_evidence() -> None:
    reconciliation = replace(
        _reconciliation(),
        api_orders=2,
        api_only_orders=1,
        overlap_api_only_orders=0,
        incremental_api_only_orders=1,
    )
    tail = replace(
        _order(3, source_kind="openapi"),
        source_order_id="broker-tail-order",
        observation_key="openapi-tail-order",
        source_record_sha256=_sha(303),
        ordered_at=ORDER_TIME + timedelta(hours=1),
        source_updated_at=ORDER_TIME + timedelta(hours=1),
        status="CANCELLED_ALL",
        order_quantity=Decimal("1"),
        order_amount=Decimal("0"),
        summary_filled_quantity=Decimal("0"),
        summary_average_fill_price=None,
        total_fee=Decimal("0"),
        evidence_level="not_filled",
    )

    result = project_reconciled_identities(
        reconciliation=reconciliation,
        order_observations=[*_orders(), tail],
        fill_observations=[],
    )

    projected_tail = next(
        item for item in result.orders if item.source_order_id == tail.source_order_id
    )
    assert len(result.order_links) == 1
    assert projected_tail == tail
    assert projected_tail.identity_strength == "broker_stable"


def test_projection_allows_zero_matches_only_for_empty_overlap() -> None:
    tail = replace(
        _order(3, source_kind="openapi"),
        source_order_id="broker-tail-order",
        observation_key="openapi-tail-order",
    )
    empty_overlap = replace(
        _reconciliation(),
        statement_orders=0,
        api_orders=1,
        matched_orders=0,
        api_only_orders=1,
        matched_filled_orders=0,
        statement_fee_total=Decimal("0"),
        api_fee_total=Decimal("0"),
        matches=(),
        incremental_api_only_orders=1,
    )

    result = project_reconciled_identities(
        reconciliation=empty_overlap,
        order_observations=[tail],
        fill_observations=[],
    )

    assert result.orders == (tail,)
    assert result.order_links == ()

    blocking_overlap = replace(
        empty_overlap,
        overlap_api_only_orders=1,
        incremental_api_only_orders=0,
    )
    with pytest.raises(IdentityProjectionError, match="overlap_api_only_orders"):
        project_reconciled_identities(
            reconciliation=blocking_overlap,
            order_observations=[tail],
            fill_observations=[],
        )


def test_attestation_key_excludes_local_database_ids() -> None:
    fills = [
        _fill(
            11,
            source_kind="csv",
            source_deal_id="csv-fill-a",
            quantity="1",
            price="100",
            second=1,
        ),
        _fill(
            12,
            source_kind="csv",
            source_deal_id="csv-fill-b",
            quantity="1",
            price="100",
            second=2,
        ),
        _fill(
            21,
            source_kind="openapi",
            source_deal_id="DEAL-A",
            quantity="1",
            price="100",
            second=1,
            milliseconds=713,
        ),
        _fill(
            22,
            source_kind="openapi",
            source_deal_id="DEAL-B",
            quantity="1",
            price="100",
            second=2,
            milliseconds=409,
        ),
    ]
    first = project_reconciled_identities(
        reconciliation=_reconciliation(),
        order_observations=_orders(),
        fill_observations=fills,
    )
    reloaded_orders = [
        replace(
            item,
            observation_id=item.observation_id + 1000,
            import_batch_id=item.import_batch_id + 1000,
        )
        for item in _orders()
    ]
    reloaded_fills = [
        replace(
            item,
            observation_id=item.observation_id + 1000,
            import_batch_id=item.import_batch_id + 1000,
        )
        for item in fills
    ]
    second = project_reconciled_identities(
        reconciliation=_reconciliation(),
        order_observations=reloaded_orders,
        fill_observations=reloaded_fills,
    )

    assert (
        first.fill_set_attestations[0].attestation_key
        == second.fill_set_attestations[0].attestation_key
    )


def test_selected_full_csv_batch_scopes_weak_fills_outside_api_window() -> None:
    overlap_csv = _fill(
        11,
        source_kind="csv",
        source_deal_id="csv-overlap-a",
        quantity="1",
        price="100",
        second=1,
    )
    overlap_csv_b = _fill(
        12,
        source_kind="csv",
        source_deal_id="csv-overlap-b",
        quantity="1",
        price="100",
        second=2,
    )
    overlap_api = _fill(
        21,
        source_kind="openapi",
        source_deal_id="DEAL-A",
        quantity="1",
        price="100",
        second=1,
        milliseconds=713,
    )
    overlap_api_b = _fill(
        22,
        source_kind="openapi",
        source_deal_id="DEAL-B",
        quantity="1",
        price="100",
        second=2,
        milliseconds=409,
    )
    historical_order = replace(
        _order(31, source_kind="csv"),
        source_order_id="historical-csv-order",
        observation_key="csv-order-historical",
        ordered_at=ORDER_TIME - timedelta(days=10),
    )
    historical_fill = replace(
        _fill(
            32,
            source_kind="csv",
            source_deal_id="historical-csv-fill",
            quantity="2",
            price="80",
            second=1,
        ),
        source_order_id="historical-csv-order",
        observation_key="csv-fill-historical",
        filled_at=ORDER_TIME - timedelta(days=10),
    )
    input_fills = [
        overlap_csv,
        overlap_csv_b,
        overlap_api,
        overlap_api_b,
        historical_fill,
    ]

    result = project_reconciled_identities(
        reconciliation=_reconciliation(),
        order_observations=[*_orders(), historical_order],
        fill_observations=input_fills,
        selected_csv_batch=AcceptedCsvBatchSelection(
            import_batch_id=1,
            batch_key="csv-batch",
        ),
    )

    projected_historical = next(
        item
        for item in result.fills
        if item.source_deal_id == "historical-csv-fill"
    )
    projected_overlap = next(
        item for item in result.fills if item.source_deal_id == "csv-overlap-a"
    )
    assert historical_fill.identity_strength == "derived_weak"
    assert projected_historical.identity_strength == "selected_batch_stable"
    assert projected_historical.canonical_deal_id is None
    # Overlap rows still use the broker-authoritative set contract; the local
    # full-batch strength is never substituted for broker identity evidence.
    assert projected_overlap.identity_strength == "derived_weak"
    assert projected_overlap.fill_set_role == "attested_shadow"
    assert result.selected_batch_stable_fill_count == 1


def test_selected_csv_batch_rejects_a_second_csv_import() -> None:
    second_batch_order = replace(
        _order(91, source_kind="csv"),
        import_batch_id=91,
        batch_key="another-csv-batch",
        source_order_id="another-order",
    )

    with pytest.raises(IdentityProjectionError, match="exactly the selected"):
        project_reconciled_identities(
            reconciliation=_reconciliation(),
            order_observations=[*_orders(), second_batch_order],
            fill_observations=[],
            selected_csv_batch=AcceptedCsvBatchSelection(
                import_batch_id=1,
                batch_key="csv-batch",
            ),
        )

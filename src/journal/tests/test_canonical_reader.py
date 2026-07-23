# -*- coding: utf-8 -*-
"""Tests for the pure cross-batch Journal v2 canonical reader."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.journal.ledger.canonical import (
    CanonicalizationBlockedError,
    CanonicalizationInputError,
    FillObservationInput,
    OrderObservationInput,
    canonicalize_observations,
)
from src.journal.ledger.episodes import build_position_episodes


ET = ZoneInfo("America/New_York")
ORDER_TIME = datetime(2026, 7, 20, 9, 30, tzinfo=ET)
RECORDED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


def _sha(value: int) -> str:
    return f"{value:064x}"


def _order(
    observation_id: int,
    *,
    batch: int = 1,
    source_kind: str = "csv",
    source_order_id: str = "ORDER-1",
    account_key: str = "account-a",
    identity_strength: str | None = None,
    evidence_level: str = "aggregate_only",
    side: str = "BUY",
    status: str | None = None,
    quantity: Decimal = Decimal("2"),
    average_price: Decimal = Decimal("100"),
    total_fee: Decimal | None = Decimal("1"),
) -> OrderObservationInput:
    if identity_strength is None:
        identity_strength = (
            "broker_stable" if source_kind == "openapi" else "derived_strong"
        )
    return OrderObservationInput(
        observation_id=observation_id,
        import_batch_id=batch,
        batch_key=f"batch-{batch}",
        source_kind=source_kind,
        observation_key=f"{source_kind}-order-{observation_id}",
        source_record_sha256=_sha(observation_id),
        broker="moomoo",
        account_key=account_key,
        source_order_id=source_order_id,
        identity_strength=identity_strength,
        raw_symbol="AAPL",
        asset_type="equity",
        underlying="AAPL",
        side=side,
        status=status or ("FILLED_ALL" if source_kind == "openapi" else "FILLED"),
        currency="USD",
        ordered_at=ORDER_TIME,
        order_quantity=quantity,
        evidence_level=evidence_level,
        fee_evidence_status="complete" if total_fee is not None else "missing",
        recorded_at=RECORDED_AT + timedelta(minutes=batch),
        contract_multiplier=Decimal("1"),
        contract_multiplier_basis="asset_definition",
        source_row_number=observation_id,
        source_updated_at=ORDER_TIME + timedelta(seconds=batch),
        order_price=Decimal("100"),
        order_amount=quantity * average_price,
        summary_filled_quantity=quantity,
        summary_average_fill_price=average_price,
        total_fee=total_fee,
    )


def _fill(
    observation_id: int,
    *,
    batch: int = 2,
    source_kind: str = "openapi",
    source_order_id: str | None = "ORDER-1",
    source_deal_id: str = "DEAL-1",
    account_key: str = "account-a",
    identity_strength: str | None = None,
    quantity: Decimal = Decimal("2"),
    price: Decimal = Decimal("100"),
    total_fee: Decimal | None = None,
    second: int = 1,
) -> FillObservationInput:
    if identity_strength is None:
        identity_strength = (
            "broker_stable" if source_kind == "openapi" else "derived_weak"
        )
    return FillObservationInput(
        observation_id=observation_id,
        import_batch_id=batch,
        batch_key=f"batch-{batch}",
        source_kind=source_kind,
        observation_key=f"{source_kind}-fill-{observation_id}",
        source_record_sha256=_sha(observation_id + 10_000),
        broker="moomoo",
        account_key=account_key,
        source_order_id=source_order_id,
        source_deal_id=source_deal_id,
        identity_strength=identity_strength,
        raw_symbol="AAPL",
        asset_type="equity",
        underlying="AAPL",
        side="BUY",
        currency="USD",
        filled_at=ORDER_TIME + timedelta(seconds=second),
        quantity=quantity,
        price=price,
        recorded_at=RECORDED_AT + timedelta(minutes=batch),
        contract_multiplier=Decimal("1"),
        contract_multiplier_basis="asset_definition",
        source_row_number=observation_id,
        amount=quantity * price,
        total_fee=total_fee,
    )


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_cross_batch_duplicates_are_deterministic_and_keep_all_refs() -> None:
    first_order = _order(
        1,
        batch=1,
        source_kind="openapi",
        evidence_level="fill_detail",
    )
    second_order = replace(
        first_order,
        observation_id=2,
        import_batch_id=2,
        batch_key="batch-2",
        observation_key="openapi-order-2",
        source_record_sha256=_sha(2),
        recorded_at=RECORDED_AT + timedelta(minutes=2),
    )
    first_fill = _fill(11, batch=1, total_fee=Decimal("1"))
    second_fill = replace(
        first_fill,
        observation_id=12,
        import_batch_id=2,
        batch_key="batch-2",
        observation_key="openapi-fill-12",
        source_record_sha256=_sha(10_012),
        recorded_at=RECORDED_AT + timedelta(minutes=2),
    )

    result = canonicalize_observations(
        [first_order, second_order],
        [first_fill, second_fill],
    )
    reversed_result = canonicalize_observations(
        [second_order, first_order],
        [second_fill, first_fill],
    )

    assert result.analysis_ready is True
    assert len(result.orders) == 1
    assert len(result.fills) == 1
    assert result.provenance.duplicate_order_observation_count == 1
    assert result.provenance.duplicate_fill_observation_count == 1
    assert len(result.orders[0].evidence_refs) == 2
    assert len(result.fills[0].evidence_refs) == 2
    assert len(result.evidence_refs) == 4
    assert result.orders[0].selected_ref.batch_key == "batch-2"
    assert result.fills[0].selected_ref.batch_key == "batch-2"
    assert result.episode_fills[0].quantity == Decimal("2")
    assert isinstance(result.episode_fills[0].quantity, Decimal)
    assert result.episode_fills[0].filled_at.tzinfo == timezone.utc
    assert result.canonical_set_sha256 == reversed_result.canonical_set_sha256
    assert result == reversed_result


def test_api_detail_shadows_csv_aggregate_without_double_counting() -> None:
    csv_order = _order(1, batch=1, source_kind="csv")
    api_order = _order(
        2,
        batch=2,
        source_kind="openapi",
        evidence_level="fill_detail",
    )
    fills = [
        _fill(
            11,
            source_deal_id="DEAL-1",
            quantity=Decimal("1"),
            price=Decimal("99"),
            second=1,
        ),
        _fill(
            12,
            source_deal_id="DEAL-2",
            quantity=Decimal("1"),
            price=Decimal("101"),
            second=2,
        ),
    ]

    result = canonicalize_observations([csv_order, api_order], fills)

    assert result.analysis_ready is True
    assert len(result.orders) == 1
    assert len(result.fills) == 2
    order = result.orders[0]
    assert order.evidence.evidence_level == "fill_detail"
    assert order.evidence.filled_quantity == Decimal("2")
    assert order.evidence.average_fill_price == Decimal("100")
    assert order.aggregate_shadowed is True
    assert order.selected_ref.source_kind == "openapi"
    assert result.provenance.shadowed_aggregate_order_count == 1
    assert {
        fill.evidence.broker_order_observation_id for fill in result.fills
    } == {order.evidence.observation_id}

    build = build_position_episodes(
        result.episode_orders,
        result.episode_fills,
        source_window_start=ORDER_TIME - timedelta(minutes=1),
        source_cutoff_at=ORDER_TIME + timedelta(minutes=1),
        opening_positions=(),
        opening_snapshot_complete=True,
    )
    assert build.source_event_count == 2
    assert build.detailed_fill_event_count == 2
    assert build.aggregate_order_event_count == 0


def test_explicit_identity_links_normalize_real_csv_api_shape() -> None:
    csv_order = replace(
        _order(
            1,
            source_kind="csv",
            source_order_id="csv-order-hash",
            evidence_level="aggregate_only",
        ),
        canonical_order_id="ORDER-1",
        raw_symbol="AAPL",
        ordered_at=ORDER_TIME.replace(microsecond=0),
        order_price=Decimal("100.0000000000000002"),
        summary_average_fill_price=Decimal("100.000005"),
    )
    api_order = replace(
        _order(
            2,
            source_kind="openapi",
            source_order_id="ORDER-1",
            evidence_level="fill_detail",
        ),
        raw_symbol="US.AAPL",
        ordered_at=ORDER_TIME.replace(microsecond=713000),
    )
    csv_fill = replace(
        _fill(
            11,
            source_kind="csv",
            source_order_id="csv-order-hash",
            source_deal_id="csv-fill-hash",
            identity_strength="derived_weak",
            total_fee=None,
        ),
        canonical_order_id="ORDER-1",
        canonical_deal_id="DEAL-1",
        raw_symbol="AAPL",
        filled_at=(ORDER_TIME + timedelta(seconds=1)).replace(microsecond=0),
        price=Decimal("100.0000000000000002"),
        amount=Decimal("200.0000000000000004"),
    )
    api_fill = replace(
        _fill(
            12,
            source_kind="openapi",
            source_order_id="ORDER-1",
            source_deal_id="DEAL-1",
            identity_strength="broker_stable",
            total_fee=Decimal("1"),
        ),
        raw_symbol="US.AAPL",
        filled_at=(ORDER_TIME + timedelta(seconds=1)).replace(microsecond=713000),
    )

    result = canonicalize_observations(
        [csv_order, api_order],
        [csv_fill, api_fill],
    )

    assert result.analysis_ready is True
    assert len(result.orders) == 1
    assert len(result.fills) == 1
    assert result.orders[0].identity[2] == "ORDER-1"
    assert result.fills[0].identity[2] == "DEAL-1"
    assert result.orders[0].evidence.instrument.raw_symbol == "AAPL"
    assert result.provenance.duplicate_order_observation_count == 1
    assert result.provenance.duplicate_fill_observation_count == 1
    assert len(result.fills[0].evidence_refs) == 2


def test_attested_api_fill_set_shadows_ambiguous_csv_weak_fills() -> None:
    csv_order = replace(
        _order(1, source_order_id="csv-order-hash"),
        canonical_order_id="ORDER-1",
    )
    api_order = _order(
        2,
        source_kind="openapi",
        source_order_id="ORDER-1",
        evidence_level="fill_detail",
    )
    csv_fills = [
        replace(
            _fill(
                11,
                source_kind="csv",
                source_order_id="csv-order-hash",
                source_deal_id="csv-fill-a",
                identity_strength="derived_weak",
                quantity=Decimal("1"),
                price=Decimal("99"),
                second=1,
            ),
            canonical_order_id="ORDER-1",
            fill_set_role="attested_shadow",
            fill_set_attestation_key="attestation-1",
        ),
        replace(
            _fill(
                12,
                source_kind="csv",
                source_order_id="csv-order-hash",
                source_deal_id="csv-fill-b",
                identity_strength="derived_weak",
                quantity=Decimal("1"),
                price=Decimal("101"),
                second=7,
            ),
            canonical_order_id="ORDER-1",
            fill_set_role="attested_shadow",
            fill_set_attestation_key="attestation-1",
        ),
    ]
    api_fills = [
        replace(
            _fill(
                21,
                source_order_id="ORDER-1",
                source_deal_id="DEAL-A",
                quantity=Decimal("1"),
                price=Decimal("99"),
                second=1,
            ),
            canonical_order_id="ORDER-1",
            raw_symbol="US.AAPL",
            filled_at=(ORDER_TIME + timedelta(seconds=1)).replace(
                microsecond=713000
            ),
            fill_set_role="authoritative",
            fill_set_attestation_key="attestation-1",
        ),
        replace(
            _fill(
                22,
                source_order_id="ORDER-1",
                source_deal_id="DEAL-B",
                quantity=Decimal("1"),
                price=Decimal("101"),
                second=2,
            ),
            canonical_order_id="ORDER-1",
            raw_symbol="US.AAPL",
            filled_at=(ORDER_TIME + timedelta(seconds=2)).replace(
                microsecond=409000
            ),
            fill_set_role="authoritative",
            fill_set_attestation_key="attestation-1",
        ),
    ]

    result = canonicalize_observations(
        [csv_order, api_order],
        [*csv_fills, *api_fills],
    )

    assert result.analysis_ready is True
    assert [item.source_deal_id for item in result.fills] == [
        "DEAL-A",
        "DEAL-B",
    ]
    assert result.provenance.input_fill_observation_count == 4
    assert result.provenance.canonical_fill_count == 2
    assert result.provenance.duplicate_fill_observation_count == 0
    assert result.provenance.shadowed_fill_observation_count == 2
    assert len(result.orders[0].shadowed_fill_evidence_refs) == 2
    assert "unstable_fill_identity" not in _codes(result)


def test_fill_set_shadowing_blocks_when_attested_totals_disagree() -> None:
    order = _order(
        1,
        source_kind="openapi",
        source_order_id="ORDER-1",
        evidence_level="fill_detail",
    )
    authoritative = replace(
        _fill(21, source_deal_id="DEAL-A"),
        canonical_order_id="ORDER-1",
        fill_set_role="authoritative",
        fill_set_attestation_key="attestation-1",
    )
    shadow = replace(
        _fill(
            11,
            source_kind="csv",
            source_order_id="csv-order-hash",
            source_deal_id="csv-fill-a",
            identity_strength="derived_weak",
            price=Decimal("101"),
        ),
        canonical_order_id="ORDER-1",
        fill_set_role="attested_shadow",
        fill_set_attestation_key="attestation-1",
    )

    result = canonicalize_observations([order], [authoritative, shadow])

    assert result.analysis_ready is False
    assert "fill_set_attestation_mismatch" in _codes(result)
    assert len(result.fills) == 1
    assert result.provenance.shadowed_fill_observation_count == 1


def test_uncovered_csv_aggregate_is_retained_as_order_event() -> None:
    result = canonicalize_observations([_order(1)], [])

    assert result.analysis_ready is True
    assert result.orders[0].evidence.evidence_level == "aggregate_only"
    assert result.orders[0].aggregate_shadowed is False
    assert result.episode_fills == ()

    build = build_position_episodes(
        result.episode_orders,
        result.episode_fills,
        source_window_start=ORDER_TIME - timedelta(minutes=1),
        source_cutoff_at=ORDER_TIME + timedelta(minutes=1),
        opening_positions=(),
        opening_snapshot_complete=True,
    )
    assert build.source_event_count == 1
    assert build.aggregate_order_event_count == 1
    assert build.detailed_fill_event_count == 0


def test_same_order_identity_with_conflicting_side_blocks_analysis() -> None:
    buy = _order(1, batch=1, side="BUY")
    sell = _order(2, batch=2, source_kind="openapi", side="SELL")

    result = canonicalize_observations([buy, sell], [])

    assert result.analysis_ready is False
    assert "order_economic_conflict" in _codes(result)
    issue = next(issue for issue in result.issues if issue.field_name == "side")
    assert len(issue.evidence_refs) == 2
    with pytest.raises(
        CanonicalizationBlockedError,
        match="order_economic_conflict",
    ):
        result.require_analysis_ready()


def test_same_deal_identity_with_conflicting_price_blocks_analysis() -> None:
    order = _order(1, source_kind="openapi", evidence_level="fill_detail")
    first = _fill(11, batch=1, price=Decimal("100"), total_fee=Decimal("1"))
    second = replace(
        first,
        observation_id=12,
        import_batch_id=2,
        batch_key="batch-2",
        observation_key="openapi-fill-12",
        source_record_sha256=_sha(10_012),
        price=Decimal("101"),
        amount=Decimal("202"),
    )

    result = canonicalize_observations([order], [first, second])

    assert result.analysis_ready is False
    assert "fill_economic_conflict" in _codes(result)
    assert any(issue.field_name == "price" for issue in result.issues)


def test_orphan_fill_is_preserved_for_diagnosis_but_blocks_analysis() -> None:
    result = canonicalize_observations([], [_fill(11)])

    assert result.analysis_ready is False
    assert len(result.fills) == 1
    assert result.fills[0].evidence.broker_order_observation_id is None
    assert _codes(result) == {"orphan_fill"}


def test_duplicate_order_fee_conflict_is_explicit() -> None:
    first = _order(1, batch=1, total_fee=Decimal("1"))
    second = _order(
        2,
        batch=2,
        source_kind="openapi",
        total_fee=Decimal("1.01"),
    )

    result = canonicalize_observations([first, second], [])

    assert result.analysis_ready is False
    assert "fee_total_conflict" in _codes(result)
    assert any(
        issue.entity_kind == "order" and issue.field_name == "total_fee"
        for issue in result.issues
    )


def test_fill_fee_sum_must_reconcile_to_order_total() -> None:
    order = _order(
        1,
        source_kind="openapi",
        evidence_level="fill_detail",
        total_fee=Decimal("1"),
    )
    fills = [
        _fill(
            11,
            source_deal_id="DEAL-1",
            quantity=Decimal("1"),
            total_fee=Decimal("0.40"),
        ),
        _fill(
            12,
            source_deal_id="DEAL-2",
            quantity=Decimal("1"),
            total_fee=Decimal("0.50"),
            second=2,
        ),
    ]

    result = canonicalize_observations([order], fills)

    assert result.analysis_ready is False
    assert "fee_total_mismatch" in _codes(result)


def test_partially_populated_fill_fees_block_analysis() -> None:
    order = _order(
        1,
        source_kind="openapi",
        evidence_level="fill_detail",
        total_fee=Decimal("1"),
    )
    fills = [
        _fill(
            11,
            source_deal_id="DEAL-1",
            quantity=Decimal("1"),
            total_fee=Decimal("0.50"),
        ),
        _fill(
            12,
            source_deal_id="DEAL-2",
            quantity=Decimal("1"),
            total_fee=None,
            second=2,
        ),
    ]

    result = canonicalize_observations([order], fills)

    assert result.analysis_ready is False
    assert "partial_fill_fee_evidence" in _codes(result)


def test_detail_backed_filled_order_without_fills_is_blocked() -> None:
    result = canonicalize_observations(
        [_order(1, source_kind="openapi", evidence_level="fill_detail")],
        [],
    )

    assert result.analysis_ready is False
    assert _codes(result) == {"missing_detail_fills"}


def test_weak_fill_identity_is_accepted_only_with_stable_corroboration() -> None:
    order = _order(1, source_kind="openapi", evidence_level="fill_detail")
    weak = _fill(
        11,
        batch=1,
        source_kind="csv",
        identity_strength="derived_weak",
        total_fee=Decimal("1"),
    )

    weak_only = canonicalize_observations([order], [weak])
    assert weak_only.analysis_ready is False
    assert "unstable_fill_identity" in _codes(weak_only)

    stable = replace(
        weak,
        observation_id=12,
        import_batch_id=2,
        batch_key="batch-2",
        source_kind="openapi",
        observation_key="openapi-fill-12",
        source_record_sha256=_sha(10_012),
        identity_strength="broker_stable",
    )
    corroborated = canonicalize_observations([order], [weak, stable])
    assert corroborated.analysis_ready is True
    assert corroborated.fills[0].selected_ref.source_kind == "openapi"
    assert len(corroborated.fills[0].evidence_refs) == 2


def test_detailed_fill_summary_mismatch_is_blocking() -> None:
    order = _order(
        1,
        source_kind="openapi",
        evidence_level="fill_detail",
        quantity=Decimal("2"),
    )
    fill = _fill(11, quantity=Decimal("1"), total_fee=Decimal("1"))

    result = canonicalize_observations([order], [fill])

    assert result.analysis_ready is False
    assert "fill_summary_mismatch" in _codes(result)
    assert any(
        issue.field_name == "summary_filled_quantity" for issue in result.issues
    )


def test_same_source_ids_in_different_accounts_do_not_merge() -> None:
    first = _order(1, account_key="account-a")
    second = _order(2, account_key="account-b")

    result = canonicalize_observations([second, first], [])

    assert result.analysis_ready is True
    assert len(result.orders) == 2
    assert [item.identity[1] for item in result.orders] == [
        "account-a",
        "account-b",
    ]


def test_decimal_and_timezone_contract_rejects_lossy_inputs() -> None:
    with pytest.raises(CanonicalizationInputError, match="must be Decimal"):
        replace(_order(1), order_quantity=2.0)  # type: ignore[arg-type]

    with pytest.raises(CanonicalizationInputError, match="must include a timezone"):
        replace(_order(1), ordered_at=datetime(2026, 7, 20, 9, 30))

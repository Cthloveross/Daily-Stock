# -*- coding: utf-8 -*-
"""Pure canonical-reader coverage for broker combo execution groups."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from src.journal.ledger.canonical import (
    ExecutionGroupLegObservationInput,
    ExecutionGroupObservationInput,
    FillObservationInput,
    canonicalize_observations,
)


NOW = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)


def _leg(
    *,
    observation_id: int,
    leg_key: str,
    raw_symbol: str,
    side: str,
    strike: str,
    multiplier: Decimal | None = Decimal("100"),
) -> ExecutionGroupLegObservationInput:
    return ExecutionGroupLegObservationInput(
        observation_id=observation_id,
        import_batch_id=1,
        batch_key="batch-1",
        source_kind="openapi",
        observation_key=f"leg-{leg_key}",
        source_record_sha256=(str(observation_id) * 64)[:64],
        broker="moomoo",
        account_key="account-hash",
        group_observation_id=10,
        leg_key=leg_key,
        raw_symbol=raw_symbol,
        asset_type="option",
        underlying="MU",
        side=side,
        currency="USD",
        quantity_ratio=Decimal("1"),
        recorded_at=NOW,
        expiry=date(2026, 7, 31),
        strike=Decimal(strike),
        option_right="P",
        contract_multiplier=multiplier,
        contract_multiplier_basis=(
            "broker_stated" if multiplier is not None else "unknown"
        ),
        source_updated_at=NOW,
    )


def _group(
    *,
    multiplier: Decimal | None = Decimal("100"),
    fee: Decimal | None = Decimal("8.08"),
) -> ExecutionGroupObservationInput:
    legs = (
        _leg(
            observation_id=11,
            leg_key="leg-buy-745",
            raw_symbol="US.MU260731P745000",
            side="BUY_BACK",
            strike="745",
            multiplier=multiplier,
        ),
        _leg(
            observation_id=12,
            leg_key="leg-sell-760",
            raw_symbol="US.MU260731P760000",
            side="SELL",
            strike="760",
            multiplier=multiplier,
        ),
    )
    return ExecutionGroupObservationInput(
        observation_id=10,
        import_batch_id=1,
        batch_key="batch-1",
        source_kind="openapi",
        observation_key="group-1",
        source_record_sha256="a" * 64,
        broker="moomoo",
        account_key="account-hash",
        source_group_id="combo-order-1",
        identity_strength="broker_stable",
        environment="REAL",
        raw_group_symbol="US.MU260731P745/760",
        group_side="SELL",
        strategy_type="SPREAD",
        status="FILLED_ALL",
        currency="USD",
        ordered_at=NOW,
        source_updated_at=NOW + timedelta(seconds=1),
        package_quantity=Decimal("2"),
        filled_package_quantity=Decimal("2"),
        order_net_price=Decimal("-6.7"),
        average_net_price=Decimal("-7"),
        total_fee=fee,
        fee_evidence_status="complete" if fee is not None else "missing",
        recorded_at=NOW,
        legs=legs,
        fee_components=(
            (("commission", fee),) if fee is not None else ()
        ),
        fee_observation_id=20 if fee is not None else None,
        fee_observation_key="group-fee-1" if fee is not None else None,
        fee_source_record_sha256="b" * 64 if fee is not None else None,
    )


def _fill(
    *,
    observation_id: int,
    source_deal_id: str,
    leg_key: str,
    raw_symbol: str,
    side: str,
    price: str,
    filled_at: datetime,
    quantity: str = "1",
    fill_fee: Decimal | None = None,
) -> FillObservationInput:
    strike = "745" if "745000" in raw_symbol else "760"
    return FillObservationInput(
        observation_id=observation_id,
        import_batch_id=1,
        batch_key="batch-1",
        source_kind="openapi",
        observation_key=f"fill-{source_deal_id}",
        source_record_sha256=(str(observation_id) * 64)[:64],
        broker="moomoo",
        account_key="account-hash",
        source_order_id="combo-order-1",
        source_deal_id=source_deal_id,
        identity_strength="broker_stable",
        raw_symbol=raw_symbol,
        asset_type="option",
        underlying="MU",
        side=side,
        currency="USD",
        filled_at=filled_at,
        quantity=Decimal(quantity),
        price=Decimal(price),
        recorded_at=NOW,
        expiry=date(2026, 7, 31),
        strike=Decimal(strike),
        option_right="P",
        contract_multiplier=Decimal("100"),
        contract_multiplier_basis="broker_stated",
        source_updated_at=filled_at,
        total_fee=fill_fee,
        execution_group_id="combo-order-1",
        execution_group_leg_key=leg_key,
        execution_group_fill_link_id=100 + observation_id,
        execution_group_fill_link_key=f"link-{source_deal_id}",
        execution_group_fill_link_sha256=("f" * 63) + str(observation_id)[-1],
    )


def _balanced_fills() -> tuple[FillObservationInput, ...]:
    return (
        _fill(
            observation_id=31,
            source_deal_id="deal-buy-1",
            leg_key="leg-buy-745",
            raw_symbol="US.MU260731P745000",
            side="BUY_BACK",
            price="24.3",
            filled_at=NOW + timedelta(seconds=2),
        ),
        _fill(
            observation_id=32,
            source_deal_id="deal-buy-2",
            leg_key="leg-buy-745",
            raw_symbol="US.MU260731P745000",
            side="BUY_BACK",
            price="24.3",
            filled_at=NOW + timedelta(seconds=3),
        ),
        _fill(
            observation_id=33,
            source_deal_id="deal-sell-1",
            leg_key="leg-sell-760",
            raw_symbol="US.MU260731P760000",
            side="SELL",
            price="31.3",
            filled_at=NOW + timedelta(seconds=2),
        ),
        _fill(
            observation_id=34,
            source_deal_id="deal-sell-2",
            leg_key="leg-sell-760",
            raw_symbol="US.MU260731P760000",
            side="SELL",
            price="31.3",
            filled_at=NOW + timedelta(seconds=3),
        ),
    )


def test_balanced_combo_is_analysis_ready_and_fee_remains_group_scoped() -> None:
    result = canonicalize_observations((), _balanced_fills(), (_group(),))

    assert result.analysis_ready is True
    assert result.issues == ()
    assert len(result.orders) == 0
    assert len(result.fills) == 4
    assert all(fill.source_order_id is None for fill in result.fills)
    assert all(fill.evidence.total_fee is None for fill in result.fills)
    assert all(fill.execution_group_identity is not None for fill in result.fills)

    group = result.execution_groups[0]
    assert group.total_fee == Decimal("8.08")
    assert group.fee_components == (("commission", Decimal("8.08")),)
    assert group.proved_executed_group_quantity == Decimal("2")
    assert group.broker_reported_order_net_price == Decimal("-6.7")
    assert group.broker_reported_average_net_price == Decimal("-7")
    assert {leg.filled_quantity for leg in group.legs} == {Decimal("2")}
    assert result.provenance.canonical_execution_group_count == 1
    assert result.provenance.canonical_execution_group_leg_count == 2
    assert {item.evidence_kind for item in result.evidence_refs} == {
        "execution_group",
        "execution_group_fee",
        "execution_group_fill_link",
        "execution_group_leg",
        "fill",
    }


def test_combo_hash_is_stable_under_fill_and_leg_input_order() -> None:
    group = _group()
    reversed_group = replace(group, legs=tuple(reversed(group.legs)))
    fills = _balanced_fills()

    first = canonicalize_observations((), fills, (group,))
    second = canonicalize_observations((), tuple(reversed(fills)), (reversed_group,))

    assert first.canonical_set_sha256 == second.canonical_set_sha256
    assert first.execution_groups == second.execution_groups


def test_missing_multiplier_and_unbalanced_leg_are_blocking() -> None:
    group = _group(multiplier=None)
    fills = tuple(
        replace(
            fill,
            contract_multiplier=None,
            contract_multiplier_basis="unknown",
        )
        for fill in _balanced_fills()[:-1]
    )

    result = canonicalize_observations((), fills, (group,))

    assert result.analysis_ready is False
    codes = {issue.code for issue in result.issues}
    assert "execution_group_contract_multiplier_unproved" in codes
    assert "execution_group_leg_quantity_mismatch" in codes


def test_group_fill_fee_is_rejected_and_never_copied_to_canonical_fill() -> None:
    fills = list(_balanced_fills())
    fills[0] = replace(fills[0], total_fee=Decimal("1"))

    result = canonicalize_observations((), tuple(fills), (_group(),))

    assert result.analysis_ready is False
    assert "execution_group_fill_fee_must_be_null" in {
        issue.code for issue in result.issues
    }
    assert all(fill.evidence.total_fee is None for fill in result.fills)

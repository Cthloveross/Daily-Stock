# -*- coding: utf-8 -*-
"""Tests for the pure Journal v2 PositionEpisode builder."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.journal.ledger.episodes import (
    CanonicalFillEvidence,
    CanonicalOrderEvidence,
    EpisodeBuildError,
    InstrumentIdentity,
    OpeningPosition,
    build_position_episodes,
)


UTC = timezone.utc
WINDOW_START = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)
CUTOFF = WINDOW_START + timedelta(hours=8)
INSTRUMENT = InstrumentIdentity(
    broker="moomoo",
    account_key="test_account",
    raw_symbol="EXAMPLE260821C200000",
    asset_type="option",
    underlying="EXAMPLE",
    currency="USD",
    expiry=date(2026, 8, 21),
    strike=Decimal("200"),
    option_right="C",
    contract_multiplier=None,
)


def _time(minutes: int) -> datetime:
    return WINDOW_START + timedelta(minutes=minutes)


def _order(
    observation_id: int,
    side: str,
    quantity: Decimal,
    price: Decimal,
    fee: Decimal | None,
    *,
    evidence_level: str = "fill_detail",
    amount: Decimal | None = None,
    minute: int | None = None,
) -> CanonicalOrderEvidence:
    return CanonicalOrderEvidence(
        observation_id=observation_id,
        observation_key=f"order-{observation_id}",
        instrument=INSTRUMENT,
        side=side,
        status="FILLED",
        ordered_at=_time(observation_id if minute is None else minute),
        source_sequence=observation_id,
        evidence_level=evidence_level,
        filled_quantity=quantity,
        average_fill_price=price,
        amount=amount,
        total_fee=fee,
        fee_evidence_status="complete" if fee is not None else "missing",
    )


def _fill(
    observation_id: int,
    order_id: int | None,
    side: str,
    quantity: Decimal,
    price: Decimal,
    amount: Decimal | None,
    *,
    fee: Decimal | None = None,
    minute: int | None = None,
) -> CanonicalFillEvidence:
    return CanonicalFillEvidence(
        observation_id=observation_id,
        observation_key=f"fill-{observation_id}",
        instrument=INSTRUMENT,
        side=side,
        filled_at=_time(observation_id if minute is None else minute),
        source_sequence=observation_id,
        quantity=quantity,
        price=price,
        amount=amount,
        total_fee=fee,
        broker_order_observation_id=order_id,
    )


def _build(
    orders: list[CanonicalOrderEvidence],
    fills: list[CanonicalFillEvidence],
    *,
    opening_positions: tuple[OpeningPosition, ...] = (),
    opening_snapshot_complete: bool = True,
    assume_flat_if_missing: bool = False,
):
    return build_position_episodes(
        orders,
        fills,
        source_window_start=WINDOW_START,
        source_cutoff_at=CUTOFF,
        opening_positions=opening_positions,
        opening_snapshot_complete=opening_snapshot_complete,
        assume_flat_if_missing=assume_flat_if_missing,
    )


def test_partial_fills_add_reduce_and_close_with_exact_economics() -> None:
    orders = [
        _order(1, "BUY", Decimal("2"), Decimal("2.5"), Decimal("0.30")),
        _order(2, "BUY", Decimal("1"), Decimal("4"), Decimal("0.15")),
        _order(3, "SELL", Decimal("2"), Decimal("5"), Decimal("0.20")),
        _order(4, "SELL", Decimal("1"), Decimal("6"), Decimal("0.10")),
    ]
    fills = [
        _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
        _fill(12, 1, "BUY", Decimal("1"), Decimal("3"), Decimal("300")),
        _fill(13, 2, "BUY", Decimal("1"), Decimal("4"), Decimal("400")),
        _fill(14, 3, "SELL", Decimal("2"), Decimal("5"), Decimal("1000")),
        _fill(15, 4, "SELL", Decimal("1"), Decimal("6"), Decimal("600")),
    ]

    result = _build(orders, fills)

    assert result.source_event_count == 5
    assert result.detailed_fill_event_count == 5
    assert result.aggregate_order_event_count == 0
    assert result.source_known_fee_total == Decimal("0.7500000000")
    assert result.allocated_known_fee_total == Decimal("0.7500000000")
    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode.direction == "long"
    assert episode.lifecycle_status == "closed"
    assert episode.opened_quantity == Decimal("3.0000000000")
    assert episode.max_absolute_quantity == Decimal("3.0000000000")
    assert episode.closed_quantity == Decimal("3.0000000000")
    assert episode.remaining_quantity == Decimal("0E-10")
    assert episode.average_entry_price == Decimal("3.0000000000")
    assert episode.average_exit_price == Decimal("5.3333333333")
    assert episode.opening_cash_flow == Decimal("-900.0000000000")
    assert episode.closing_cash_flow == Decimal("1600.0000000000")
    assert episode.realized_pnl_gross == Decimal("700.0000000000")
    assert episode.total_fee == Decimal("0.7500000000")
    assert episode.realized_pnl_net == Decimal("699.2500000000")
    assert episode.dte_at_entry == 32
    assert episode.completeness_status == "exact"
    assert [allocation.event_role for allocation in episode.evidence] == [
        "open",
        "add",
        "add",
        "reduce",
        "close",
    ]
    assert [item.allocated_fee for item in episode.evidence[:2]] == [
        Decimal("0.1500000000"),
        Decimal("0.1500000000"),
    ]


def test_reversal_splits_one_fill_and_conserves_fee_and_cash_flow() -> None:
    orders = [
        _order(1, "BUY", Decimal("2"), Decimal("2"), Decimal("0.2")),
        _order(2, "SELL", Decimal("3"), Decimal("3"), Decimal("0.3")),
        _order(3, "BUY_BACK", Decimal("1"), Decimal("2"), Decimal("0.1")),
    ]
    fills = [
        _fill(11, 1, "BUY", Decimal("2"), Decimal("2"), Decimal("400")),
        _fill(12, 2, "SELL", Decimal("3"), Decimal("3"), Decimal("900")),
        _fill(13, 3, "BUY_BACK", Decimal("1"), Decimal("2"), Decimal("200")),
    ]

    result = _build(orders, fills)

    assert len(result.episodes) == 2
    long_episode, short_episode = result.episodes
    assert long_episode.direction == "long"
    assert long_episode.close_reason == "reversed"
    assert long_episode.realized_pnl_gross == Decimal("200.0000000000")
    assert long_episode.total_fee == Decimal("0.4000000000")
    assert long_episode.realized_pnl_net == Decimal("199.6000000000")
    assert short_episode.direction == "short"
    assert short_episode.close_reason == "position_flattened"
    assert short_episode.realized_pnl_gross == Decimal("100.0000000000")
    assert short_episode.total_fee == Decimal("0.2000000000")
    assert short_episode.realized_pnl_net == Decimal("99.8000000000")

    long_reversal = long_episode.evidence[-1]
    short_reversal = short_episode.evidence[0]
    assert long_reversal.evidence_key == short_reversal.evidence_key == "fill-12"
    assert long_reversal.allocation_ratio == Decimal("0.6666666667")
    assert short_reversal.allocation_ratio == Decimal("0.3333333333")
    assert long_reversal.allocated_fee == Decimal("0.2000000000")
    assert short_reversal.allocated_fee == Decimal("0.1000000000")
    assert long_reversal.allocated_cash_flow == Decimal("600.0000000000")
    assert short_reversal.allocated_cash_flow == Decimal("300.0000000000")
    assert result.source_known_fee_total == Decimal("0.6000000000")
    assert result.allocated_known_fee_total == Decimal("0.6000000000")


def test_aggregate_only_orders_never_create_fill_evidence() -> None:
    orders = [
        _order(
            1,
            "BUY",
            Decimal("2"),
            Decimal("2"),
            Decimal("0.4"),
            evidence_level="aggregate_only",
            amount=Decimal("400"),
        ),
        _order(
            2,
            "SELL",
            Decimal("2"),
            Decimal("3"),
            Decimal("0.4"),
            evidence_level="aggregate_only",
            amount=Decimal("600"),
        ),
    ]

    result = _build(orders, [])

    assert result.aggregate_order_event_count == 2
    assert result.detailed_fill_event_count == 0
    episode = result.episodes[0]
    assert episode.construction_basis == "aggregate_orders"
    assert episode.has_exact_fill_times is False
    assert episode.has_exact_fill_prices is False
    assert episode.completeness_status == "partial"
    assert episode.realized_pnl_gross == Decimal("200.0000000000")
    assert episode.realized_pnl_net == Decimal("199.2000000000")
    assert all(item.evidence_kind == "order" for item in episode.evidence)
    assert all(
        item.broker_fill_observation_id is None for item in episode.evidence
    )
    assert all(
        item.timing_precision == "order_time_proxy"
        for item in episode.evidence
    )


def test_left_censored_episode_and_later_right_censored_episode() -> None:
    opening = OpeningPosition(
        instrument=INSTRUMENT,
        signed_quantity=Decimal("3"),
        observed_at=WINDOW_START,
        average_price=Decimal("1.5"),
    )
    orders = [
        _order(1, "SELL", Decimal("3"), Decimal("2"), Decimal("0.3")),
        _order(2, "BUY", Decimal("1"), Decimal("1"), Decimal("0.1")),
    ]
    fills = [
        _fill(11, 1, "SELL", Decimal("3"), Decimal("2"), Decimal("600")),
        _fill(12, 2, "BUY", Decimal("1"), Decimal("1"), Decimal("100")),
    ]

    result = _build(orders, fills, opening_positions=(opening,))

    assert len(result.episodes) == 2
    left, right = result.episodes
    assert left.is_left_censored is True
    assert left.is_right_censored is False
    assert left.average_entry_price == Decimal("1.5000000000")
    assert left.realized_pnl_gross is None
    assert left.total_fee is None
    assert left.has_complete_fees is False
    assert left.construction_basis == "opening_balance_mixed"
    assert right.is_left_censored is False
    assert right.is_right_censored is True
    assert right.lifecycle_status == "open"
    assert right.remaining_quantity == Decimal("1.0000000000")
    assert right.total_fee == Decimal("0.1000000000")
    assert right.realized_pnl_gross is None


def test_incomplete_opening_snapshot_cannot_guess_flat_position() -> None:
    order = _order(1, "BUY", Decimal("1"), Decimal("2"), Decimal("0.1"))
    fill = _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200"))

    with pytest.raises(EpisodeBuildError, match="opening snapshot is incomplete"):
        _build([order], [fill], opening_snapshot_complete=False)


def test_explicit_assumed_flat_policy_is_partial_and_preserves_economics() -> None:
    orders = [
        _order(1, "BUY", Decimal("1"), Decimal("2"), Decimal("0.1")),
        _order(2, "SELL", Decimal("1"), Decimal("3"), Decimal("0.1")),
    ]
    fills = [
        _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
        _fill(12, 2, "SELL", Decimal("1"), Decimal("3"), Decimal("300")),
    ]

    result = _build(
        orders,
        fills,
        opening_snapshot_complete=False,
        assume_flat_if_missing=True,
    )

    assert result.opening_snapshot_complete is False
    assert result.opening_boundary_policy == "assumed_flat_unverified"
    episode = result.episodes[0]
    assert episode.left_boundary_verified is False
    assert episode.opening_boundary_policy == "assumed_flat_unverified"
    assert episode.is_left_censored is False
    assert episode.construction_basis == "trade_flow_assumed_flat"
    assert episode.completeness_status == "partial"
    assert episode.realized_pnl_net == Decimal("99.8000000000")
    model_kwargs = episode.as_model_kwargs(
        episode_build_id=1,
        strategy_episode_id=1,
    )
    assert '"left_boundary_verified":false' in (
        model_kwargs["completeness_json"]
    )
    assert '"opening_boundary_policy":"assumed_flat_unverified"' in (
        model_kwargs["provenance_json"]
    )


def test_input_order_does_not_change_deterministic_build_product() -> None:
    orders = [
        _order(1, "BUY", Decimal("1"), Decimal("2"), Decimal("0.1")),
        _order(2, "SELL", Decimal("1"), Decimal("3"), Decimal("0.1")),
    ]
    fills = [
        _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
        _fill(12, 2, "SELL", Decimal("1"), Decimal("3"), Decimal("300")),
    ]

    forward = _build(orders, fills)
    reversed_input = _build(list(reversed(orders)), list(reversed(fills)))

    assert forward == reversed_input
    assert forward.episodes[0].episode_key == reversed_input.episodes[0].episode_key
    assert forward.episodes[0].lineage_key == reversed_input.episodes[0].lineage_key


def test_fee_residual_allocation_is_storage_scale_exact() -> None:
    order = _order(1, "BUY", Decimal("3"), Decimal("2"), Decimal("1"))
    fills = [
        _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
        _fill(12, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
        _fill(13, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
    ]

    result = _build([order], fills)

    fees = [item.allocated_fee for item in result.episodes[0].evidence]
    assert fees == [
        Decimal("0.3333333333"),
        Decimal("0.3333333333"),
        Decimal("0.3333333334"),
    ]
    assert sum((fee for fee in fees if fee is not None), Decimal("0")) \
        == Decimal("1.0000000000")


def test_aggregate_order_with_fill_is_rejected_instead_of_double_counted() -> None:
    order = _order(
        1,
        "BUY",
        Decimal("1"),
        Decimal("2"),
        Decimal("0.1"),
        evidence_level="aggregate_only",
        amount=Decimal("200"),
    )
    fill = _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200"))

    with pytest.raises(EpisodeBuildError, match="aggregate-only"):
        _build([order], [fill])


def test_persistence_kwargs_match_episode_and_evidence_model_contracts() -> None:
    orders = [
        _order(1, "BUY", Decimal("1"), Decimal("2"), Decimal("0.1")),
        _order(2, "SELL", Decimal("1"), Decimal("3"), Decimal("0.1")),
    ]
    fills = [
        _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
        _fill(12, 2, "SELL", Decimal("1"), Decimal("3"), Decimal("300")),
    ]
    episode = _build(orders, fills).episodes[0]

    episode_kwargs = episode.as_model_kwargs(
        episode_build_id=7,
        strategy_episode_id=9,
    )
    evidence_kwargs = episode.evidence[0].as_model_kwargs(
        episode_build_id=7,
        position_episode_id=11,
    )

    assert episode_kwargs["episode_build_id"] == 7
    assert episode_kwargs["strategy_episode_id"] == 9
    assert episode_kwargs["episode_key"] == episode.episode_key
    assert '"method":"signed_position_zero_crossing"' in (
        episode_kwargs["matching_evidence_json"]
    )
    assert evidence_kwargs["position_episode_id"] == 11
    assert evidence_kwargs["broker_fill_observation_id"] == 11
    assert evidence_kwargs["broker_order_observation_id"] is None
    assert '"timing_precision":"fill_time"' in (
        evidence_kwargs["allocation_evidence_json"]
    )


def test_evidence_derived_multiplier_has_provenance_and_validates_amount() -> None:
    derived_instrument = replace(
        INSTRUMENT,
        contract_multiplier=Decimal("100"),
        contract_multiplier_basis="evidence_derived_from_amount",
    )
    order = replace(
        _order(1, "BUY", Decimal("1"), Decimal("2"), Decimal("0.1")),
        instrument=derived_instrument,
    )
    fill = replace(
        _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), Decimal("200")),
        instrument=derived_instrument,
    )

    episode = _build([order], [fill]).episodes[0]
    model_kwargs = episode.as_model_kwargs(
        episode_build_id=1,
        strategy_episode_id=1,
    )

    assert episode.opening_cash_flow == Decimal("-200.0000000000")
    assert model_kwargs["contract_multiplier"] == Decimal("100")
    assert '"contract_multiplier_basis":"evidence_derived_from_amount"' in (
        model_kwargs["provenance_json"]
    )

    inconsistent_fill = replace(fill, amount=Decimal("201"))
    with pytest.raises(EpisodeBuildError, match="observed amount is inconsistent"):
        _build([order], [inconsistent_fill])


def test_broker_amount_rounding_within_half_cent_is_accepted() -> None:
    equity = InstrumentIdentity(
        broker="moomoo",
        account_key="test_account",
        raw_symbol="EXAMPLE",
        asset_type="equity",
        underlying="EXAMPLE",
        currency="USD",
        contract_multiplier=Decimal("1"),
        contract_multiplier_basis="evidence_derived_from_amount",
    )
    order = replace(
        _order(
            1,
            "BUY",
            Decimal("10"),
            Decimal("518.8221"),
            Decimal("0.1"),
        ),
        instrument=equity,
    )
    fill = replace(
        _fill(
            11,
            1,
            "BUY",
            Decimal("10"),
            Decimal("518.8221"),
            Decimal("5188.22"),
        ),
        instrument=equity,
    )

    episode = _build([order], [fill]).episodes[0]

    assert episode.opening_cash_flow == Decimal("-5188.2200000000")


def test_option_without_amount_or_multiplier_does_not_invent_cash_flow() -> None:
    orders = [
        _order(1, "BUY", Decimal("1"), Decimal("2"), Decimal("0.1")),
        _order(2, "SELL", Decimal("1"), Decimal("3"), Decimal("0.1")),
    ]
    fills = [
        _fill(11, 1, "BUY", Decimal("1"), Decimal("2"), None),
        _fill(12, 2, "SELL", Decimal("1"), Decimal("3"), None),
    ]

    episode = _build(orders, fills).episodes[0]

    assert episode.average_entry_price == Decimal("2.0000000000")
    assert episode.average_exit_price == Decimal("3.0000000000")
    assert episode.opening_cash_flow is None
    assert episode.closing_cash_flow is None
    assert episode.realized_pnl_gross is None
    assert episode.realized_pnl_net is None
    assert episode.completeness_status == "partial"

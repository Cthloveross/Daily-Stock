# -*- coding: utf-8 -*-
"""Focused tests for append-only PositionEpisode persistence."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.journal.brokers.moomoo_statement import (
    StatementFill,
    StatementOrder,
    StatementParseResult,
)
from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    append_latest_position_episode_build,
    get_latest_episode_summary,
    get_latest_position_episode_detail,
    get_latest_position_episode_page,
    preview_latest_position_episodes,
)
from src.journal.ledger.models import (
    EpisodeBuild,
    ImportBatch,
    PositionEpisode,
    PositionEpisodeEvidence,
    StrategyEpisode,
)
from src.journal.ledger.repository import import_statement_batch
from src.storage import get_db


ET = ZoneInfo("America/New_York")
SYMBOL = "EXAMPLE260717C200000"


def _detailed_statement(
    *,
    parser_version: str = "episode-test-v1",
    source_sha256: str = "a" * 64,
    fill_amounts: tuple[Decimal | None, Decimal | None] = (
        Decimal("250"),
        Decimal("300"),
    ),
) -> StatementParseResult:
    opened = datetime(2026, 7, 20, 9, 30, tzinfo=ET)

    def _order(
        row: int,
        side: str,
        price: Decimal,
        amount: Decimal | None,
        occurred_at: datetime,
    ) -> StatementOrder:
        fill = StatementFill(
            source_row=row,
            quantity=Decimal("1"),
            price=price,
            filled_at=occurred_at,
            amount=amount,
            market="US",
            currency="USD",
        )
        return StatementOrder(
            source_row=row,
            derived_order_id=f"detail-{row}",
            symbol=SYMBOL,
            name="Deidentified option",
            side=side,
            status="FILLED",
            order_quantity=Decimal("1"),
            # A market order proves that the repository uses fill evidence,
            # rather than requiring an order-limit price for detail rows.
            order_price=None,
            order_price_text="Market",
            order_amount=None,
            order_time=occurred_at - timedelta(seconds=2),
            order_type="Market",
            time_in_force="Day",
            session="Regular Trading Hours",
            market="US",
            currency="USD",
            summary_filled_quantity=Decimal("1"),
            summary_average_price=price,
            fills=(fill,),
            fee_components=(),
            total_fee=Decimal("0.30"),
            evidence_level="fill_detail",
        )

    orders = (
        _order(2, "BUY", Decimal("2.50"), fill_amounts[0], opened),
        _order(
            3,
            "SELL",
            Decimal("3.00"),
            fill_amounts[1],
            opened + timedelta(hours=1),
        ),
    )
    return StatementParseResult(
        source_sha256=source_sha256,
        parser_version=parser_version,
        rows_total=2,
        headers=("synthetic",),
        orders=orders,
    )


def _aggregate_statement() -> StatementParseResult:
    opened = datetime(2026, 7, 20, 9, 30, tzinfo=ET)

    def _order(
        row: int,
        side: str,
        order_price: Decimal,
        order_amount: Decimal,
        average_fill_price: Decimal,
        occurred_at: datetime,
    ) -> StatementOrder:
        return StatementOrder(
            source_row=row,
            derived_order_id=f"aggregate-{row}",
            symbol=SYMBOL,
            name="Deidentified option",
            side=side,
            status="FILLED",
            order_quantity=Decimal("2"),
            order_price=order_price,
            order_price_text=format(order_price, "f"),
            order_amount=order_amount,
            order_time=occurred_at,
            order_type="Limit",
            time_in_force="Day",
            session="Regular Trading Hours",
            market="US",
            currency="USD",
            summary_filled_quantity=Decimal("1"),
            summary_average_price=average_fill_price,
            fills=(),
            fee_components=(),
            total_fee=Decimal("0.10"),
            evidence_level="aggregate_only",
        )

    return StatementParseResult(
        source_sha256="c" * 64,
        parser_version="aggregate-test-v1",
        rows_total=2,
        headers=("synthetic",),
        orders=(
            _order(
                2,
                "BUY",
                Decimal("2.00"),
                Decimal("400"),
                Decimal("2.50"),
                opened,
            ),
            _order(
                3,
                "SELL",
                Decimal("3.00"),
                Decimal("600"),
                Decimal("3.50"),
                opened + timedelta(hours=1),
            ),
        ),
    )


def _case_focus_statement() -> StatementParseResult:
    """Four episodes with deliberately different ranking dimensions."""
    orders: list[StatementOrder] = []
    next_row = 2

    def _add_episode(
        *,
        symbol: str,
        opened_at: datetime,
        hold: timedelta | None,
        entry_price: Decimal,
        exit_price: Decimal | None,
        fee_per_fill: Decimal,
    ) -> None:
        nonlocal next_row
        legs = [("BUY", entry_price, opened_at)]
        if exit_price is not None and hold is not None:
            legs.append(("SELL", exit_price, opened_at + hold))
        for side, price, occurred_at in legs:
            row = next_row
            next_row += 1
            fill = StatementFill(
                source_row=row,
                quantity=Decimal("1"),
                price=price,
                filled_at=occurred_at,
                amount=price * Decimal("100"),
                market="US",
                currency="USD",
            )
            orders.append(
                StatementOrder(
                    source_row=row,
                    derived_order_id=f"case-focus-{row}",
                    symbol=symbol,
                    name="Case focus fixture",
                    side=side,
                    status="FILLED",
                    order_quantity=Decimal("1"),
                    order_price=None,
                    order_price_text="Market",
                    order_amount=None,
                    order_time=occurred_at - timedelta(seconds=2),
                    order_type="Market",
                    time_in_force="Day",
                    session="Regular Trading Hours",
                    market="US",
                    currency="USD",
                    summary_filled_quantity=Decimal("1"),
                    summary_average_price=price,
                    fills=(fill,),
                    fee_components=(),
                    total_fee=fee_per_fill,
                    evidence_level="fill_detail",
                )
            )

    _add_episode(
        symbol="WIN260717C200000",
        opened_at=datetime(2026, 7, 10, 9, 30, tzinfo=ET),
        hold=timedelta(hours=3),
        entry_price=Decimal("2"),
        exit_price=Decimal("5"),
        fee_per_fill=Decimal("1"),
    )
    _add_episode(
        symbol="LOSS260717C200000",
        opened_at=datetime(2026, 7, 13, 9, 30, tzinfo=ET),
        hold=timedelta(hours=1),
        entry_price=Decimal("5"),
        exit_price=Decimal("2"),
        fee_per_fill=Decimal("0.5"),
    )
    _add_episode(
        symbol="MID260717C200000",
        opened_at=datetime(2026, 7, 14, 9, 30, tzinfo=ET),
        hold=timedelta(hours=5),
        entry_price=Decimal("2"),
        exit_price=Decimal("3"),
        fee_per_fill=Decimal("10"),
    )
    _add_episode(
        symbol="OPEN260717C200000",
        opened_at=datetime(2026, 7, 15, 9, 30, tzinfo=ET),
        hold=None,
        entry_price=Decimal("1"),
        exit_price=None,
        fee_per_fill=Decimal("50"),
    )
    return StatementParseResult(
        source_sha256="9" * 64,
        parser_version="case-focus-test-v1",
        rows_total=len(orders),
        headers=("synthetic",),
        orders=tuple(orders),
    )


def test_preview_uses_latest_batch_and_requires_proved_option_multiplier():
    first = import_statement_batch(_detailed_statement())
    second_statement = _detailed_statement(
        parser_version="episode-test-v2",
        source_sha256="b" * 64,
    )
    second = import_statement_batch(second_statement)

    preview = preview_latest_position_episodes()

    assert preview is not None
    assert preview.batch_id == second.batch_id
    assert preview.batch_id != first.batch_id
    assert preview.parser_version == "episode-test-v2"
    assert preview.opening_boundary_policy == "assumed_flat_unverified"
    assert preview.reconciliation_scope == "not_run"
    assert preview.source_event_count == 2
    assert len(preview.episodes) == 1
    episode = preview.episodes[0]
    assert episode.instrument.contract_multiplier == Decimal("100.00000000")
    assert (
        episode.instrument.contract_multiplier_basis
        == "evidence_derived_from_amount"
    )
    assert episode.realized_pnl_gross == Decimal("50.0000000000")
    assert episode.realized_pnl_net == Decimal("49.4000000000")
    assert preview.headline_episode_count == 0
    assert preview.headline_realized_pnl_net is None
    assert preview.headline_exclusion_counts["boundary_unverified"] == 1
    assert preview.conditional_closed_episode_count == 1
    assert preview.conditional_realized_pnl_gross == Decimal("50.0000000000")
    assert preview.conditional_total_fee == Decimal("0.6000000000")
    assert preview.conditional_realized_pnl_net == Decimal("49.4000000000")


def test_preview_does_not_treat_newer_openapi_window_as_canonical():
    csv_result = import_statement_batch(_detailed_statement())
    db = get_db()
    with db.session_scope() as session:
        api_batch = ImportBatch(
            batch_key="openapi-batch-key",
            broker="moomoo",
            account_key="default_moomoo_us",
            source_kind="openapi",
            source_schema="dsa.moomoo.readonly-export.v1",
            source_sha256="d" * 64,
            parser_name="moomoo_openapi_export",
            parser_version="test-v1",
            window_start=datetime(2026, 7, 1, tzinfo=ET),
            window_end=datetime(2026, 7, 2, tzinfo=ET),
            source_timezone="America/New_York",
            status="accepted",
            analysis_level="exact",
            analysis_ready=True,
            order_observation_count=0,
            fill_observation_count=0,
            rejected_record_count=0,
            reconciliation_status="passed",
            reconciliation_json="{}",
            completeness_score=Decimal("1"),
            completeness_json="{}",
            warnings_json="[]",
            provenance_json="{}",
        )
        session.add(api_batch)
        session.flush()
        api_batch_id = int(api_batch.id)

    preview = preview_latest_position_episodes()

    assert preview is not None
    assert preview.batch_id == csv_result.batch_id
    assert preview.batch_id != api_batch_id
    assert preview.source_event_count == 2


def test_option_execution_without_amount_is_blocked_not_defaulted_to_one():
    import_statement_batch(
        _detailed_statement(fill_amounts=(None, Decimal("300")))
    )

    with pytest.raises(EpisodeRepositoryError, match="cannot prove"):
        preview_latest_position_episodes()


def test_equity_cent_rounding_proves_one_but_over_tolerance_is_blocked():
    base = _detailed_statement(source_sha256="e" * 64)
    orders = []
    values = (
        (Decimal("10"), Decimal("518.8221"), Decimal("5188.22")),
        (Decimal("10"), Decimal("520"), Decimal("5200")),
    )
    for order, (quantity, price, amount) in zip(base.orders, values):
        fill = replace(
            order.fills[0],
            quantity=quantity,
            price=price,
            amount=amount,
        )
        orders.append(
            replace(
                order,
                symbol="AAPL",
                order_quantity=quantity,
                summary_filled_quantity=quantity,
                summary_average_price=price,
                fills=(fill,),
            )
        )
    import_statement_batch(replace(base, orders=tuple(orders)))

    preview = preview_latest_position_episodes()

    assert preview is not None
    assert preview.episodes[0].instrument.contract_multiplier == Decimal("1")
    assert preview.multiplier_amount_tolerance == Decimal("0.005")
    assert preview.max_multiplier_proof_residual == Decimal("0.0010")


def test_amount_residual_over_half_cent_is_blocked():
    import_statement_batch(
        _detailed_statement(
            source_sha256="f" * 64,
            fill_amounts=(Decimal("249.99"), Decimal("300")),
        )
    )

    with pytest.raises(EpisodeRepositoryError, match="exceeds 0.005"):
        preview_latest_position_episodes()


def test_aggregate_order_amount_proves_identity_but_is_not_execution_cash_flow():
    import_statement_batch(_aggregate_statement(), allow_partial=True)

    preview = preview_latest_position_episodes()

    assert preview is not None
    assert preview.aggregate_order_event_count == 2
    assert preview.aggregate_only_episode_count == 1
    assert len(preview.episodes) == 1
    episode = preview.episodes[0]
    assert episode.instrument.contract_multiplier == Decimal("100.00000000")
    # Execution uses 1 @ 2.50 and 1 @ 3.50, not submitted amounts 400/600.
    assert episode.opening_cash_flow == Decimal("-250.0000000000")
    assert episode.closing_cash_flow == Decimal("350.0000000000")
    assert episode.realized_pnl_gross == Decimal("100.0000000000")


def test_append_is_explicit_idempotent_and_all_children_have_foreign_keys():
    imported = import_statement_batch(_detailed_statement())

    with pytest.raises(EpisodeRepositoryError, match="explicit acceptance"):
        append_latest_position_episode_build(accept_assumed_flat=False)

    first = append_latest_position_episode_build(accept_assumed_flat=True)
    second = append_latest_position_episode_build(accept_assumed_flat=True)

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.build_id == first.build_id
    assert first.position_episode_count == 1
    assert first.strategy_episode_count == 1
    assert first.evidence_allocation_count == 2

    db = get_db()
    with db.session_scope() as session:
        assert session.execute(select(func.count(EpisodeBuild.id))).scalar_one() == 1
        strategies = session.execute(select(StrategyEpisode)).scalars().all()
        positions = session.execute(select(PositionEpisode)).scalars().all()
        evidence = session.execute(select(PositionEpisodeEvidence)).scalars().all()
        assert len(strategies) == len(positions) == 1
        assert len(evidence) == 2
        assert strategies[0].strategy_type == "single_position_unclassified"
        assert strategies[0].roll_chain_key is None
        assert strategies[0].pnl_precision == "conditional"
        assert positions[0].strategy_episode_id == strategies[0].id
        assert all(item.episode_build_id == first.build_id for item in evidence)
        assert all(item.position_episode_id == positions[0].id for item in evidence)
        assert all(
            item.broker_fill_observation_id is not None
            and item.broker_order_observation_id is None
            for item in evidence
        )
        assert session.get(EpisodeBuild, first.build_id) is not None
        report = json.loads(
            session.get(EpisodeBuild, first.build_id).build_report_json
        )
        assert report["conditional_result_basis"] == (
            "closed_known_cash_flows_under_opening_boundary_policy"
        )
        assert report["conditional_closed_episode_count"] == 1
        assert report["conditional_realized_pnl_net"] == "49.4000000000"
        assert imported.batch_id == 1


def test_summary_page_and_detail_are_latest_build_scoped():
    import_statement_batch(_detailed_statement())
    first_build = append_latest_position_episode_build(accept_assumed_flat=True)
    first_page = get_latest_position_episode_page(per_page=10)
    first_episode_id = first_page.items[0].episode_id

    import_statement_batch(
        _detailed_statement(
            parser_version="episode-test-v2",
            source_sha256="b" * 64,
        )
    )
    latest_build = append_latest_position_episode_build(accept_assumed_flat=True)
    summary = get_latest_episode_summary()
    page = get_latest_position_episode_page(underlying="example", per_page=10)

    assert summary is not None
    assert summary.build_id == latest_build.build_id
    assert summary.build_id != first_build.build_id
    assert summary.reconciliation_scope == "not_run"
    assert summary.position_episode_count == 1
    assert summary.open_episode_count == 0
    assert summary.closed_episode_count == 1
    assert summary.boundary_unverified_episode_count == 1
    assert summary.headline_episode_count == 0
    assert summary.headline_realized_pnl_net is None
    assert summary.conditional_closed_episode_count == 1
    assert summary.conditional_realized_pnl_gross == Decimal("50.0000000000")
    assert summary.conditional_total_fee == Decimal("0.6000000000")
    assert summary.conditional_realized_pnl_net == Decimal("49.4000000000")
    assert summary.fee_conserved is True
    assert page.total == 1
    assert page.items[0].opening_boundary_policy == "assumed_flat_unverified"
    assert get_latest_position_episode_detail(first_episode_id) is None

    detail = get_latest_position_episode_detail(page.items[0].episode_id)
    assert detail is not None
    assert len(detail.evidence) == 2
    assert detail.completeness["left_boundary_verified"] is False
    assert detail.matching_evidence["opening_boundary_policy"] == (
        "assumed_flat_unverified"
    )
    assert all(item.evidence_kind == "fill" for item in detail.evidence)


def test_position_episode_case_focus_ranks_without_mutating_build() -> None:
    import_statement_batch(_case_focus_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)

    default_page = get_latest_position_episode_page(per_page=10)
    top_profit = get_latest_position_episode_page(
        case_focus="top_profit",
        per_page=10,
    )
    top_loss = get_latest_position_episode_page(
        case_focus="top_loss",
        per_page=10,
    )
    largest_fee = get_latest_position_episode_page(
        case_focus="largest_fee",
        per_page=10,
    )
    longest_hold = get_latest_position_episode_page(
        case_focus="longest_hold",
        per_page=10,
    )

    assert default_page.build_id == build.build_id
    assert [item.underlying for item in default_page.items] == [
        "OPEN",
        "MID",
        "LOSS",
        "WIN",
    ]
    assert [item.underlying for item in top_profit.items] == [
        "WIN",
        "MID",
        "LOSS",
    ]
    assert [item.underlying for item in top_loss.items] == [
        "LOSS",
        "MID",
        "WIN",
    ]
    assert top_profit.total == top_loss.total == 3
    assert all(item.lifecycle_status == "closed" for item in top_profit.items)
    assert all(item.realized_pnl_net is not None for item in top_loss.items)
    assert [item.underlying for item in largest_fee.items] == [
        "OPEN",
        "MID",
        "WIN",
        "LOSS",
    ]
    assert [item.underlying for item in longest_hold.items] == [
        "MID",
        "WIN",
        "LOSS",
        "OPEN",
    ]

    # Existing status filters remain intersections, not overrides.
    impossible = get_latest_position_episode_page(
        lifecycle_status="open",
        case_focus="top_profit",
        per_page=10,
    )
    assert impossible.total == 0
    assert impossible.items == ()


def test_position_episode_case_focus_rejects_unknown_value() -> None:
    import_statement_batch(_detailed_statement())
    append_latest_position_episode_build(accept_assumed_flat=True)

    with pytest.raises(EpisodeRepositoryError, match="case_focus"):
        get_latest_position_episode_page(case_focus="unknown")


def test_summary_read_only_fallback_for_pre_conditional_build_report(monkeypatch):
    import src.journal.ledger.episode_repository as repository

    import_statement_batch(_detailed_statement())
    append_latest_position_episode_build(accept_assumed_flat=True)
    original_parse = repository._parse_json

    def _legacy_report(value):
        parsed = original_parse(value)
        for key in (
            "conditional_closed_episode_count",
            "conditional_realized_pnl_gross",
            "conditional_total_fee",
            "conditional_realized_pnl_net",
        ):
            parsed.pop(key, None)
        return parsed

    monkeypatch.setattr(repository, "_parse_json", _legacy_report)

    summary = get_latest_episode_summary()

    assert summary is not None
    assert summary.conditional_closed_episode_count == 1
    assert summary.conditional_realized_pnl_gross == Decimal("50.0000000000")
    assert summary.conditional_total_fee == Decimal("0.6000000000")
    assert summary.conditional_realized_pnl_net == Decimal("49.4000000000")
    assert summary.headline_episode_count == 0


def test_build_key_changes_with_parser_batch_and_evidence_hash():
    import_statement_batch(_detailed_statement())
    first = preview_latest_position_episodes()
    assert first is not None

    changed = replace(
        _detailed_statement(),
        parser_version="episode-test-v2",
        source_sha256="d" * 64,
    )
    import_statement_batch(changed)
    second = preview_latest_position_episodes()

    assert second is not None
    assert second.batch_id != first.batch_id
    assert second.build_key != first.build_key
    assert second.evidence_set_sha256 == first.evidence_set_sha256
    assert second.builder_config_sha256 == first.builder_config_sha256

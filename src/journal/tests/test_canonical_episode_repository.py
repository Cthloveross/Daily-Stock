# -*- coding: utf-8 -*-
"""Contracts for explicit canonical-set-backed PositionEpisode builds."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.journal.brokers.moomoo_openapi_export import (
    OpenApiBatchMetadata,
    OpenApiExportPreview,
    OpenApiFeeObservation,
    OpenApiFillObservation,
    OpenApiOrderObservation,
    OpenApiReconciliation,
)
from src.journal.brokers.moomoo_statement import (
    StatementOrder,
    StatementParseResult,
)
from src.journal.ledger.canonical import canonicalize_observations
from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    append_canonical_position_episode_build,
    append_latest_position_episode_build,
    get_episode_summary,
    get_latest_episode_summary,
    get_latest_position_episode_page,
    get_position_episode_detail,
    get_position_episode_page,
    preview_canonical_position_episodes,
)
from src.journal.ledger.models import (
    EpisodeBuild,
    EpisodeBuildCanonicalSource,
)
from src.journal.ledger.repository import (
    append_canonical_evidence_set,
    append_openapi_batch,
    import_statement_batch,
    load_canonical_observation_inputs,
)
from src.storage import get_db


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ACCOUNT_KEY = "default_moomoo_us"
SYMBOL = "EXAMPLE260821C200000"


@pytest.fixture(autouse=True)
def _avoid_unrelated_optional_provider_import(monkeypatch):
    """Keep this focused suite independent of optional market-data packages."""
    from src.config import Config

    monkeypatch.setattr(
        Config,
        "_parse_stock_email_groups",
        classmethod(lambda _cls: []),
    )


def _aggregate_statement(
    *,
    source_sha256: str = "a" * 64,
    parser_version: str = "canonical-episode-csv-v1",
) -> StatementParseResult:
    """Two partial aggregate orders whose submitted amount is not cash flow."""
    opened_at = datetime(2026, 7, 20, 9, 30, tzinfo=ET)

    def _order(
        row: int,
        side: str,
        *,
        order_price: Decimal,
        order_amount: Decimal,
        average_fill_price: Decimal,
        ordered_at: datetime,
    ) -> StatementOrder:
        return StatementOrder(
            source_row=row,
            derived_order_id=f"aggregate-partial-{row}",
            symbol=SYMBOL,
            name="Deidentified option",
            side=side,
            status="FILLED",
            # The submitted order was for two contracts, but only one filled.
            order_quantity=Decimal("2"),
            order_price=order_price,
            order_price_text=format(order_price, "f"),
            order_amount=order_amount,
            order_time=ordered_at,
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
        source_sha256=source_sha256,
        parser_version=parser_version,
        rows_total=2,
        headers=("synthetic",),
        orders=(
            _order(
                2,
                "BUY",
                order_price=Decimal("2.00"),
                order_amount=Decimal("400"),
                average_fill_price=Decimal("2.50"),
                ordered_at=opened_at,
            ),
            _order(
                3,
                "SELL",
                order_price=Decimal("3.00"),
                order_amount=Decimal("600"),
                average_fill_price=Decimal("3.50"),
                ordered_at=opened_at + timedelta(hours=1),
            ),
        ),
    )


def _openapi_preview() -> OpenApiExportPreview:
    ordered_at = datetime(2026, 7, 20, 11, 30, tzinfo=ET)
    order_id = "broker-open-order-1"
    orders = (
        OpenApiOrderObservation(
            source_order_id=order_id,
            raw_symbol=f"US.{SYMBOL}",
            stock_name="Deidentified option",
            market="US",
            side="BUY",
            order_type="LIMIT",
            status="FILLED_ALL",
            order_quantity=Decimal("2"),
            order_price=Decimal("4.00"),
            ordered_at=ordered_at,
            source_updated_at=ordered_at,
            summary_filled_quantity=Decimal("2"),
            summary_average_fill_price=Decimal("4.00"),
            time_in_force="DAY",
            fill_outside_rth=False,
            session="RTH",
            currency="USD",
            source_record_sha256="1" * 64,
        ),
    )
    fills = tuple(
        OpenApiFillObservation(
            source_deal_id=f"broker-open-deal-{index}",
            source_order_id=order_id,
            raw_symbol=f"US.{SYMBOL}",
            stock_name="Deidentified option",
            market="US",
            side="BUY",
            quantity=Decimal("1"),
            price=Decimal("4.00"),
            filled_at=ordered_at + timedelta(seconds=index),
            source_status="OK",
            currency="USD",
            source_record_sha256=str(index + 1) * 64,
        )
        for index in (1, 2)
    )
    fees = (
        OpenApiFeeObservation(
            source_order_id=order_id,
            total_fee=Decimal("0.30"),
            fee_components=(),
            source_record_sha256="4" * 64,
        ),
    )
    reconciliation = OpenApiReconciliation(
        filled_orders_without_fills=0,
        fills_without_orders=0,
        filled_quantity_mismatches=0,
        fill_code_mismatches=0,
        fill_side_mismatches=0,
        fill_average_price_mismatches=0,
        filled_orders_without_fees=0,
    )
    metadata = OpenApiBatchMetadata(
        source_schema="dsa.moomoo.readonly-export.v1",
        source_sha256="5" * 64,
        evidence_sha256="6" * 64,
        batch_key="7" * 64,
        parser_name="moomoo_openapi_export",
        parser_version="canonical-episode-api-v1",
        generated_at=datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
        environment="LIVE",
        market="US",
        account_selection="unique_auto",
        window_start=datetime(2026, 7, 20, 9, 0, tzinfo=ET),
        window_end=datetime(2026, 7, 20, 16, 0, tzinfo=ET),
        source_timezone="America/New_York",
        window_chunks=1,
        max_chunk_days=7,
        analysis_ready=True,
        analysis_level="exact",
        reconciliation_status="passed",
        reconciliation=reconciliation,
        warnings=(),
        order_observation_count=len(orders),
        fill_observation_count=len(fills),
        fee_observation_count=len(fees),
    )
    return OpenApiExportPreview(
        metadata=metadata,
        orders=orders,
        fills=fills,
        fees=fees,
    )


@dataclass(frozen=True)
class _SeededCanonical:
    csv_batch_id: int
    api_batch_id: int
    canonical_set_id: int
    canonical_set_sha256: str
    source_batch_ids: tuple[int, ...]
    legacy_build_id: int


def _seed_cross_source_canonical() -> _SeededCanonical:
    csv_result = import_statement_batch(
        _aggregate_statement(),
        allow_partial=True,
    )
    legacy = append_latest_position_episode_build(
        accept_assumed_flat=True,
    )
    api_result = append_openapi_batch(_openapi_preview(), confirmed=True)
    inputs = load_canonical_observation_inputs(ACCOUNT_KEY)
    canonical = canonicalize_observations(inputs.orders, inputs.fills)
    assert canonical.analysis_ready is True
    assert any(
        item.evidence.instrument.contract_multiplier is None
        for item in canonical.orders
    )
    stored = append_canonical_evidence_set(
        canonical,
        account_key=ACCOUNT_KEY,
        source_cutoff_at=datetime(2026, 7, 22, tzinfo=UTC),
        confirmed=True,
    )
    return _SeededCanonical(
        csv_batch_id=csv_result.batch_id,
        api_batch_id=api_result.batch_id,
        canonical_set_id=stored.canonical_set_id,
        canonical_set_sha256=stored.canonical_set_sha256,
        source_batch_ids=inputs.source_batch_ids,
        legacy_build_id=legacy.build_id,
    )


def _canonical_preview(seed: _SeededCanonical):
    preview = preview_canonical_position_episodes(
        canonical_set_id=seed.canonical_set_id,
        account_key=ACCOUNT_KEY,
        assume_flat_if_missing=True,
    )
    assert preview is not None
    return preview


def _append_canonical(seed: _SeededCanonical, preview):
    return append_canonical_position_episode_build(
        seed.canonical_set_id,
        account_key=ACCOUNT_KEY,
        expected_canonical_set_sha256=seed.canonical_set_sha256,
        expected_build_key=preview.build_key,
        accept_assumed_flat=True,
    )


def test_preview_replays_frozen_set_and_ignores_submitted_order_amount():
    seed = _seed_cross_source_canonical()

    first = _canonical_preview(seed)

    assert first.canonical_set_id == seed.canonical_set_id
    assert first.evidence_set_sha256 == seed.canonical_set_sha256
    assert first.source_batch_ids == seed.source_batch_ids
    assert first.source_event_count == 4
    assert first.aggregate_order_event_count == 2
    assert first.detailed_fill_event_count == 2
    assert len(first.episodes) == 2
    closed = next(
        episode
        for episode in first.episodes
        if episode.lifecycle_status == "closed"
    )
    # 400/600 are submitted order amounts. Actual cash flow is based on the
    # single filled contract at 2.50/3.50 with multiplier 100.
    assert closed.opening_cash_flow == Decimal("-250.0000000000")
    assert closed.closing_cash_flow == Decimal("350.0000000000")
    assert closed.realized_pnl_gross == Decimal("100.0000000000")

    newer = import_statement_batch(
        replace(
            _aggregate_statement(),
            source_sha256="b" * 64,
            parser_version="canonical-episode-csv-v2",
        ),
        allow_partial=True,
    )
    assert newer.batch_id not in seed.source_batch_ids

    replay = _canonical_preview(seed)
    assert replay.build_key == first.build_key
    assert replay.evidence_set_sha256 == first.evidence_set_sha256
    assert replay.source_batch_ids == first.source_batch_ids
    assert tuple(replay.episodes) == tuple(first.episodes)


def test_canonical_append_requires_confirmation_and_is_idempotent():
    seed = _seed_cross_source_canonical()
    preview = _canonical_preview(seed)
    db = get_db()

    with pytest.raises(EpisodeRepositoryError, match="assumed-flat|accept"):
        append_canonical_position_episode_build(
            seed.canonical_set_id,
            account_key=ACCOUNT_KEY,
            expected_canonical_set_sha256=seed.canonical_set_sha256,
            expected_build_key=preview.build_key,
            accept_assumed_flat=False,
        )

    with db.session_scope() as session:
        assert session.execute(
            select(func.count(EpisodeBuild.id))
        ).scalar_one() == 1
        assert session.execute(
            select(func.count(EpisodeBuildCanonicalSource.id))
        ).scalar_one() == 0

    first = _append_canonical(seed, preview)
    duplicate = _append_canonical(seed, preview)

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.build_id == first.build_id
    assert first.build_key == preview.build_key
    assert first.position_episode_count == 2

    with db.session_scope() as session:
        assert session.execute(
            select(func.count(EpisodeBuild.id))
        ).scalar_one() == 2
        links = session.execute(
            select(EpisodeBuildCanonicalSource)
        ).scalars().all()
        assert len(links) == 1
        link = links[0]
        assert int(link.episode_build_id) == first.build_id
        assert int(link.canonical_set_id) == seed.canonical_set_id
        assert str(link.canonical_set_sha256) == seed.canonical_set_sha256
        assert json.loads(link.source_batch_ids_json) == list(
            seed.source_batch_ids
        )
        build = session.get(EpisodeBuild, first.build_id)
        assert build is not None
        assert json.loads(build.source_batch_ids_json) == list(
            seed.source_batch_ids
        )
        assert build.evidence_set_sha256 == seed.canonical_set_sha256


def test_canonical_append_does_not_change_default_csv_build():
    seed = _seed_cross_source_canonical()
    before = get_latest_episode_summary(ACCOUNT_KEY)
    assert before is not None
    assert before.build_id == seed.legacy_build_id

    preview = _canonical_preview(seed)
    canonical = _append_canonical(seed, preview)

    default_after = get_latest_episode_summary(ACCOUNT_KEY)
    explicit = get_episode_summary(
        build_id=canonical.build_id,
        account_key=ACCOUNT_KEY,
    )
    default_page = get_latest_position_episode_page(ACCOUNT_KEY)
    explicit_page = get_position_episode_page(
        build_id=canonical.build_id,
        account_key=ACCOUNT_KEY,
    )

    assert default_after is not None
    assert default_after.build_id == seed.legacy_build_id
    assert explicit is not None
    assert explicit.build_id == canonical.build_id
    assert explicit.source_kind == "canonical_set"
    assert explicit.canonical_set_id == seed.canonical_set_id
    assert explicit.canonical_set_sha256 == seed.canonical_set_sha256
    assert explicit.source_batch_ids == seed.source_batch_ids
    assert default_page.build_id == seed.legacy_build_id
    assert default_page.total == 1
    assert explicit_page.build_id == canonical.build_id
    assert explicit_page.total == 2
    explicit_detail = get_position_episode_detail(
        explicit_page.items[0].episode_id,
        build_id=canonical.build_id,
        account_key=ACCOUNT_KEY,
    )
    assert explicit_detail is not None
    assert explicit_detail.episode.build_id == canonical.build_id

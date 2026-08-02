# -*- coding: utf-8 -*-
"""Tests for append-only Journal v2 statement persistence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import threading
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from src.journal.brokers.moomoo_statement import (
    StatementComboLeg,
    StatementFill,
    StatementApiMatch,
    StatementOrder,
    StatementParseResult,
    StatementReconciliation,
)
from src.journal.brokers.moomoo_openapi_export import (
    OpenApiBatchMetadata,
    OpenApiComboLegObservation,
    OpenApiContractSpecObservation,
    OpenApiExportPreview,
    OpenApiFeeObservation,
    OpenApiFillObservation,
    OpenApiOrderObservation,
    OpenApiReconciliation,
)
from src.journal.ledger.canonical import canonicalize_observations
from src.journal.ledger.activation_repository import (
    EpisodeBuildActivationError,
    activate_episode_build,
)
from src.journal.ledger.episode_repository import (
    EpisodeRepositoryError,
    append_canonical_position_episode_build,
    get_episode_summary,
    preview_canonical_position_episodes,
)
from src.journal.ledger.models import (
    BrokerExecutionGroupFeeObservation,
    BrokerExecutionGroupFillLink,
    BrokerExecutionGroupLegObservation,
    BrokerExecutionGroupObservation,
    BrokerFillObservation,
    BrokerFeeObservation,
    BrokerOrderObservation,
    CanonicalEvidenceMemberRecord,
    CanonicalEvidenceProvenanceRecord,
    CanonicalEvidenceSetRecord,
    CanonicalExecutionGroupFillLinkRecord,
    CanonicalExecutionGroupLegRecord,
    CanonicalExecutionGroupMemberRecord,
    CanonicalExecutionGroupProvenanceRecord,
    DealIdentityLink,
    EpisodeBuild,
    EpisodeBuildActivation,
    ImportBatch,
    OrderFillSetAttestation,
    OrderIdentityLink,
    PositionEpisode,
    PositionEpisodeEvidence,
    ReconciliationAttestation,
)
from src.journal.ledger.openapi_repository import (
    confirm_openapi_import_plan,
    OpenApiPlanError,
    plan_openapi_import,
)
from src.journal.ledger.repository import (
    LedgerImportError,
    append_canonical_evidence_set,
    append_deal_identity_link,
    append_openapi_batch,
    append_openapi_canonical_bundle,
    append_order_fill_set_attestation,
    append_order_identity_link,
    get_latest_data_health,
    import_statement_batch,
    init_ledger_schema,
    load_canonical_observation_inputs,
)
from src.storage import DatabaseManager, get_db


ET = ZoneInfo("America/New_York")
FEE_COMPONENTS = (
    ("Commission", Decimal("0.10")),
    ("Platform Fees", Decimal("0.20")),
    ("SEC Fees", Decimal("0")),
    ("Trading Activity Fees", Decimal("0")),
    ("Options Regulatory Fees", Decimal("0")),
    ("OCC Fees", Decimal("0")),
    ("Contract Fees", Decimal("0")),
    ("Consolidated Audit Trail Fees", Decimal("0.0002")),
    ("Settlement Fees", Decimal("0")),
)


def _statement() -> StatementParseResult:
    first_time = datetime(2026, 7, 20, 9, 30, tzinfo=ET)
    fills = (
        StatementFill(
            source_row=2,
            quantity=Decimal("1"),
            price=Decimal("2.50"),
            filled_at=first_time,
            amount=Decimal("250"),
            market="US",
            currency="USD",
        ),
        StatementFill(
            source_row=3,
            quantity=Decimal("1"),
            price=Decimal("2.50"),
            filled_at=first_time,
            amount=Decimal("250"),
            market="US",
            currency="USD",
        ),
    )
    detailed = StatementOrder(
        source_row=2,
        derived_order_id="moomoo_csv_detailed",
        symbol="EXAMPLE260717C200000",
        name="Deidentified option",
        side="BUY",
        status="FILLED",
        order_quantity=Decimal("2"),
        order_price=Decimal("2.50"),
        order_price_text="2.50",
        order_amount=Decimal("500"),
        order_time=first_time,
        order_type="Limit",
        time_in_force="Day",
        session="Regular Trading Hours",
        market="US",
        currency="USD",
        summary_filled_quantity=Decimal("2"),
        summary_average_price=Decimal("2.50"),
        fills=fills,
        fee_components=FEE_COMPONENTS,
        total_fee=Decimal("0.3002"),
        evidence_level="fill_detail",
    )
    aggregate = StatementOrder(
        source_row=4,
        derived_order_id="moomoo_csv_aggregate",
        symbol="EXAMPLE260717P190000",
        name="Older deidentified option",
        side="SELL",
        status="FILLED",
        order_quantity=Decimal("1"),
        order_price=Decimal("3.10"),
        order_price_text="3.10",
        order_amount=Decimal("310"),
        order_time=datetime(2026, 7, 20, 10, 30, tzinfo=ET),
        order_type="Limit",
        time_in_force="Day",
        session="Regular Trading Hours",
        market="US",
        currency="USD",
        summary_filled_quantity=Decimal("1"),
        summary_average_price=Decimal("3.10"),
        fills=(),
        fee_components=FEE_COMPONENTS,
        total_fee=Decimal("0.3002"),
        evidence_level="aggregate_only",
    )
    return StatementParseResult(
        source_sha256="a" * 64,
        parser_version="test-parser-v1",
        rows_total=3,
        headers=("synthetic",),
        orders=(detailed, aggregate),
    )


def _openapi_preview(
    *,
    batch_suffix: str = "1",
    first_fill_price: Decimal = Decimal("2.50"),
) -> OpenApiExportPreview:
    order_time = datetime(2026, 7, 20, 9, 30, tzinfo=ET)
    first_fill_time = order_time
    orders = (
        OpenApiOrderObservation(
            source_order_id="broker-order-1",
            raw_symbol="US.EXAMPLE260717C200000",
            stock_name="Deidentified option",
            market="US",
            side="BUY",
            order_type="LIMIT",
            status="FILLED_ALL",
            order_quantity=Decimal("2"),
            order_price=Decimal("2.50"),
            ordered_at=order_time,
            source_updated_at=order_time,
            summary_filled_quantity=Decimal("2"),
            summary_average_fill_price=Decimal("2.50"),
            time_in_force="DAY",
            fill_outside_rth=False,
            session="RTH",
            currency="USD",
            source_record_sha256=("1" * 63) + batch_suffix,
        ),
    )
    fills = (
        OpenApiFillObservation(
            source_deal_id="broker-deal-1",
            source_order_id="broker-order-1",
            raw_symbol="US.EXAMPLE260717C200000",
            stock_name="Deidentified option",
            market="US",
            side="BUY",
            quantity=Decimal("1"),
            price=first_fill_price,
            filled_at=first_fill_time,
            source_status="OK",
            currency="USD",
            source_record_sha256=("2" * 63) + batch_suffix,
        ),
        OpenApiFillObservation(
            source_deal_id="broker-deal-2",
            source_order_id="broker-order-1",
            raw_symbol="US.EXAMPLE260717C200000",
            stock_name="Deidentified option",
            market="US",
            side="BUY",
            quantity=Decimal("1"),
            price=Decimal("2.50"),
            filled_at=first_fill_time,
            source_status="OK",
            currency="USD",
            source_record_sha256=("3" * 63) + batch_suffix,
        ),
    )
    fees = (
        OpenApiFeeObservation(
            source_order_id="broker-order-1",
            total_fee=Decimal("0.3002"),
            fee_components=FEE_COMPONENTS,
            source_record_sha256=("4" * 63) + batch_suffix,
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
        source_sha256=("a" * 63) + batch_suffix,
        evidence_sha256=("b" * 63) + batch_suffix,
        batch_key=("c" * 63) + batch_suffix,
        parser_name="moomoo_openapi_export",
        parser_version="test-v1",
        generated_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
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


def _openapi_payload(preview: OpenApiExportPreview) -> dict[str, object]:
    return {
        "schema": preview.metadata.source_schema,
        "mode": "read_only",
        "journal_database_written": False,
        "window": {
            "start": preview.metadata.window_start.isoformat(),
            "end": preview.metadata.window_end.isoformat(),
            "timezone": preview.metadata.source_timezone,
        },
        "summary": {"analysis_ready": preview.metadata.analysis_ready},
        "records": {
            "orders": [
                {
                    "order_id": order.source_order_id,
                    "code": order.raw_symbol,
                    "trd_side": order.side,
                    "qty": order.order_quantity,
                    "create_time": order.ordered_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_status": order.status,
                    "dealt_qty": order.summary_filled_quantity,
                    "dealt_avg_price": order.summary_average_fill_price,
                }
                for order in preview.orders
            ],
            "deals": [
                {
                    "deal_id": fill.source_deal_id,
                    "order_id": fill.source_order_id,
                    "qty": fill.quantity,
                    "price": fill.price,
                }
                for fill in preview.fills
            ],
            "fees": [
                {
                    "order_id": fee.source_order_id,
                    "fee_amount": fee.total_fee,
                    "fee_details": list(fee.fee_components),
                }
                for fee in preview.fees
            ],
        },
    }


def _openapi_preview_with_api_only_order(
    *,
    ordered_at: datetime,
) -> OpenApiExportPreview:
    preview = _openapi_preview(batch_suffix="7")
    tail_order_id = "broker-order-tail"
    tail_order = replace(
        preview.orders[0],
        source_order_id=tail_order_id,
        order_quantity=Decimal("1"),
        order_price=Decimal("2.75"),
        ordered_at=ordered_at,
        source_updated_at=ordered_at,
        summary_filled_quantity=Decimal("1"),
        summary_average_fill_price=Decimal("2.75"),
        source_record_sha256="5" * 64,
    )
    tail_fill = replace(
        preview.fills[0],
        source_deal_id="broker-deal-tail",
        source_order_id=tail_order_id,
        quantity=Decimal("1"),
        price=Decimal("2.75"),
        filled_at=ordered_at,
        source_record_sha256="6" * 64,
    )
    tail_fee = replace(
        preview.fees[0],
        source_order_id=tail_order_id,
        source_record_sha256="7" * 64,
    )
    orders = (*preview.orders, tail_order)
    fills = (*preview.fills, tail_fill)
    fees = (*preview.fees, tail_fee)
    metadata = replace(
        preview.metadata,
        source_sha256="8" * 64,
        evidence_sha256="9" * 64,
        batch_key="d" * 64,
        account_binding="e" * 64,
        window_end=ordered_at + timedelta(minutes=1),
        order_observation_count=len(orders),
        fill_observation_count=len(fills),
        fee_observation_count=len(fees),
    )
    return replace(
        preview,
        metadata=metadata,
        orders=orders,
        fills=fills,
        fees=fees,
    )


def _openapi_preview_with_combo_tail(
    *,
    ordered_at: datetime,
) -> OpenApiExportPreview:
    """Return one matched ordinary order plus one complete two-leg combo."""
    preview = _openapi_preview_with_api_only_order(ordered_at=ordered_at)
    group_id = "broker-combo-tail"
    long_symbol = "US.EXAMPLE260731C200000"
    short_symbol = "US.EXAMPLE260731C210000"
    combo_order = replace(
        preview.orders[-1],
        source_order_id=group_id,
        raw_symbol="US.EXAMPLE-COMBO",
        stock_name="Deidentified vertical spread",
        side="BUY",
        order_quantity=Decimal("2"),
        order_price=Decimal("-7.00"),
        summary_filled_quantity=Decimal("2"),
        summary_average_fill_price=Decimal("-7.00"),
        strategy_type="SPREAD",
        combo_legs=(
            OpenApiComboLegObservation(
                raw_symbol=long_symbol,
                side="BUY",
                quantity_ratio=Decimal("1"),
            ),
            OpenApiComboLegObservation(
                raw_symbol=short_symbol,
                side="SELL",
                quantity_ratio=Decimal("1"),
            ),
        ),
        source_record_sha256="5" * 64,
    )
    combo_fills = (
        replace(
            preview.fills[-1],
            source_deal_id="broker-combo-long-1",
            source_order_id=group_id,
            raw_symbol=long_symbol,
            stock_name="Deidentified long option",
            side="BUY",
            quantity=Decimal("1"),
            price=Decimal("24.30"),
            filled_at=ordered_at,
            source_record_sha256="6" * 64,
        ),
        replace(
            preview.fills[-1],
            source_deal_id="broker-combo-long-2",
            source_order_id=group_id,
            raw_symbol=long_symbol,
            stock_name="Deidentified long option",
            side="BUY",
            quantity=Decimal("1"),
            price=Decimal("24.30"),
            filled_at=ordered_at + timedelta(seconds=1),
            source_record_sha256="7" * 64,
        ),
        replace(
            preview.fills[-1],
            source_deal_id="broker-combo-short-1",
            source_order_id=group_id,
            raw_symbol=short_symbol,
            stock_name="Deidentified short option",
            side="SELL",
            quantity=Decimal("1"),
            price=Decimal("31.30"),
            filled_at=ordered_at + timedelta(seconds=2),
            source_record_sha256="8" * 64,
        ),
        replace(
            preview.fills[-1],
            source_deal_id="broker-combo-short-2",
            source_order_id=group_id,
            raw_symbol=short_symbol,
            stock_name="Deidentified short option",
            side="SELL",
            quantity=Decimal("1"),
            price=Decimal("31.30"),
            filled_at=ordered_at + timedelta(seconds=3),
            source_record_sha256="9" * 64,
        ),
    )
    combo_fee = replace(
        preview.fees[-1],
        source_order_id=group_id,
        total_fee=Decimal("8.08"),
        fee_components=(
            ("Commission", Decimal("4.00")),
            ("Platform Fees", Decimal("4.08")),
        ),
        source_record_sha256="a" * 64,
    )
    contract_specs = (
        OpenApiContractSpecObservation(
            raw_symbol=long_symbol,
            lot_size=Decimal("100"),
            option_contract_size=Decimal("100"),
            option_contract_multiplier=Decimal("100"),
            resolved_multiplier=Decimal("100"),
            source_record_sha256="b" * 64,
        ),
        OpenApiContractSpecObservation(
            raw_symbol=short_symbol,
            lot_size=Decimal("100"),
            option_contract_size=Decimal("100"),
            option_contract_multiplier=Decimal("100"),
            resolved_multiplier=Decimal("100"),
            source_record_sha256="c" * 64,
        ),
    )
    orders = (*preview.orders[:-1], combo_order)
    fills = (*preview.fills[:-1], *combo_fills)
    fees = (*preview.fees[:-1], combo_fee)
    metadata = replace(
        preview.metadata,
        source_sha256="d" * 64,
        evidence_sha256="e" * 64,
        batch_key="f" * 64,
        analysis_ready=True,
        reconciliation_status="passed",
        reconciliation=replace(
            preview.metadata.reconciliation,
            unsupported_combo_orders=1,
        ),
        warnings=("execution_group_observations=1",),
        order_observation_count=len(orders),
        fill_observation_count=len(fills),
        fee_observation_count=len(fees),
        contract_spec_observation_count=len(contract_specs),
        contract_spec_status="complete",
    )
    return replace(
        preview,
        metadata=metadata,
        orders=orders,
        fills=fills,
        fees=fees,
        contract_specs=contract_specs,
    )


def _combo_persistence_counts() -> tuple[int, ...]:
    """Snapshot all tables that a combo confirmation can append to."""
    models = (
        ImportBatch,
        BrokerOrderObservation,
        BrokerFillObservation,
        BrokerFeeObservation,
        BrokerExecutionGroupObservation,
        BrokerExecutionGroupLegObservation,
        BrokerExecutionGroupFillLink,
        BrokerExecutionGroupFeeObservation,
        CanonicalEvidenceSetRecord,
        CanonicalEvidenceMemberRecord,
        CanonicalEvidenceProvenanceRecord,
        CanonicalExecutionGroupMemberRecord,
        CanonicalExecutionGroupLegRecord,
        CanonicalExecutionGroupFillLinkRecord,
        CanonicalExecutionGroupProvenanceRecord,
    )
    db = get_db()
    with db.session_scope() as session:
        return tuple(
            int(session.execute(select(func.count(model.id))).scalar_one())
            for model in models
        )


def test_openapi_plan_and_confirm_accept_authoritative_incremental_tail():
    statement = _statement()
    statement = replace(
        statement,
        rows_total=2,
        orders=(statement.orders[0],),
    )
    import_statement_batch(statement)
    preview = _openapi_preview_with_api_only_order(
        ordered_at=datetime(2026, 7, 20, 10, 0, tzinfo=ET),
    )
    payload = _openapi_payload(preview)

    plan = plan_openapi_import(preview, payload)

    assert plan.confirm_allowed is True
    assert plan.scope_is_full_batch is True
    assert plan.coverage["scope"] == "incremental_tail"
    assert plan.coverage["matched_orders"] == 1
    assert plan.coverage["api_only_orders"] == 1
    assert plan.coverage["overlap_api_only_orders"] == 0
    assert plan.coverage["incremental_api_only_orders"] == 1
    assert plan.canonical_impact.canonical_orders == 2
    assert plan.canonical_impact.canonical_fills == 3

    result = confirm_openapi_import_plan(
        preview,
        payload,
        preview_key=plan.preview_key,
        acknowledge_partial_window=False,
    )

    assert result.status == "appended"
    assert result.scope == "incremental_tail"
    assert result.appended["orders"] == 2
    assert result.appended["fills"] == 3


def test_openapi_plan_blocks_api_only_order_inside_csv_baseline_window():
    statement = _statement()
    statement = replace(
        statement,
        rows_total=2,
        orders=(statement.orders[0],),
    )
    import_statement_batch(statement)
    preview = _openapi_preview_with_api_only_order(
        ordered_at=datetime(2026, 7, 20, 9, 29, tzinfo=ET),
    )

    payload = _openapi_payload(preview)
    plan = plan_openapi_import(preview, payload)

    assert plan.confirm_allowed is False
    assert plan.coverage["api_only_orders"] == 1
    assert plan.coverage["overlap_api_only_orders"] == 1
    assert plan.coverage["incremental_api_only_orders"] == 0
    assert "cross_source_identity_blocked" in {
        issue.code for issue in plan.issues
    }


@pytest.mark.parametrize(
    ("ordered_at", "expected_issue"),
    [
        (
            datetime(2026, 7, 20, 9, 29, tzinfo=ET),
            "combo_outside_csv_baseline_unprovable",
        ),
        (
            datetime(2026, 7, 20, 10, 0, tzinfo=ET),
            "combo_csv_overlap_unprovable",
        ),
    ],
)
def test_openapi_plan_blocks_combo_outside_provable_incremental_tail(
    ordered_at: datetime,
    expected_issue: str,
):
    import_statement_batch(_statement(), allow_partial=True)
    preview = _openapi_preview_with_combo_tail(ordered_at=ordered_at)
    payload = _openapi_payload(preview)
    before = _combo_persistence_counts()
    plan = plan_openapi_import(preview, payload)

    assert plan.confirm_allowed is False
    assert plan.scope_is_full_batch is False
    assert plan.coverage["scope"] == "partial_window"
    blocking_codes = {
        issue.code for issue in plan.issues if issue.severity == "blocking"
    }
    assert expected_issue in blocking_codes
    assert {
        code
        for code in blocking_codes
        if code.startswith("combo_") or code.startswith("execution_group_")
    } == {expected_issue}
    assert "combo_order_requires_group_projection" not in blocking_codes
    assert plan.write_plan["execution_group_observations"] == 1
    assert plan.write_plan["execution_group_leg_observations"] == 2
    assert plan.write_plan["execution_group_fill_links"] == 4
    assert plan.write_plan["execution_group_fee_observations"] == 1
    assert _combo_persistence_counts() == before
    with pytest.raises(OpenApiPlanError, match="plan_contains_blocking_issues"):
        confirm_openapi_import_plan(
            preview,
            payload,
            preview_key=plan.preview_key,
            acknowledge_partial_window=True,
        )
    assert _combo_persistence_counts() == before


def test_openapi_plan_confirms_complete_incremental_combo_and_replays_hash():
    statement = replace(
        _statement(),
        rows_total=2,
        orders=(_statement().orders[0],),
    )
    import_statement_batch(statement)
    preview = _openapi_preview_with_combo_tail(
        ordered_at=datetime(2026, 7, 20, 10, 0, tzinfo=ET),
    )
    payload = _openapi_payload(preview)

    plan = plan_openapi_import(preview, payload)

    assert plan.confirm_allowed is True
    assert plan.scope_is_full_batch is True
    assert plan.coverage["scope"] == "incremental_tail"
    assert plan.write_plan["order_observations"] == 2
    assert plan.write_plan["ordinary_order_observations"] == 1
    assert plan.write_plan["execution_group_observations"] == 1
    assert plan.write_plan["execution_group_leg_observations"] == 2
    assert plan.write_plan["execution_group_fill_links"] == 4
    assert plan.write_plan["execution_group_fee_observations"] == 1
    assert plan.canonical_impact.canonical_execution_groups == 1
    assert plan.canonical_impact.canonical_execution_group_legs == 2
    planned_hash = plan.canonical_impact.canonical_set_sha256
    assert len(planned_hash) == 64

    result = confirm_openapi_import_plan(
        preview,
        payload,
        preview_key=plan.preview_key,
        acknowledge_partial_window=False,
    )

    assert result.status == "appended"
    assert result.duplicate is False
    assert result.canonical_set_sha256 == planned_hash
    assert result.appended["ordinary_orders"] == 1
    assert result.appended["execution_groups"] == 1
    assert result.appended["execution_group_legs"] == 2
    assert result.appended["execution_group_fill_links"] == 4
    assert result.appended["execution_group_fees"] == 1
    assert result.canonical["execution_groups"] == 1
    assert result.canonical["execution_group_legs"] == 2

    db = get_db()
    with db.session_scope() as session:
        assert session.execute(
            select(func.count(BrokerOrderObservation.id)).where(
                BrokerOrderObservation.source_order_id == "broker-combo-tail"
            )
        ).scalar_one() == 0
        group_row = session.execute(
            select(BrokerExecutionGroupObservation).where(
                BrokerExecutionGroupObservation.source_execution_group_id
                == "broker-combo-tail"
            )
        ).scalar_one()
        assert session.execute(
            select(func.count(BrokerExecutionGroupLegObservation.id)).where(
                BrokerExecutionGroupLegObservation.execution_group_observation_id
                == group_row.id
            )
        ).scalar_one() == 2
        group_fills = session.execute(
            select(BrokerFillObservation)
            .where(BrokerFillObservation.source_order_id == "broker-combo-tail")
            .order_by(BrokerFillObservation.source_deal_id)
        ).scalars().all()
        assert len(group_fills) == 4
        assert all(item.broker_order_observation_id is None for item in group_fills)
        assert all(item.contract_multiplier == Decimal("100") for item in group_fills)
        assert all(item.total_fee is None for item in group_fills)
        assert all(item.fee_components_json is None for item in group_fills)
        assert all(item.fee_allocation_method is None for item in group_fills)
        group_fee = session.execute(
            select(BrokerExecutionGroupFeeObservation).where(
                BrokerExecutionGroupFeeObservation.execution_group_observation_id
                == group_row.id
            )
        ).scalar_one()
        assert group_fee.total_fee == Decimal("8.0800000000")

    replay_inputs = load_canonical_observation_inputs()
    replayed = canonicalize_observations(
        replay_inputs.orders,
        replay_inputs.fills,
        execution_group_observations=replay_inputs.execution_groups,
    )
    assert replayed.analysis_ready is True
    assert replayed.canonical_set_sha256 == planned_hash
    group_canonical_fills = [
        item
        for item in replayed.fills
        if item.execution_group_identity is not None
    ]
    assert len(group_canonical_fills) == 4
    assert all(item.evidence.total_fee is None for item in group_canonical_fills)

    episode_preview = preview_canonical_position_episodes(
        canonical_set_id=result.canonical_set_id,
    )
    assert episode_preview is not None
    group_symbols = {
        "EXAMPLE260731C200000",
        "EXAMPLE260731C210000",
    }
    group_episodes = [
        item
        for item in episode_preview.episodes
        if item.instrument.raw_symbol in group_symbols
    ]
    assert len(group_episodes) == 2
    assert sum(len(item.evidence) for item in group_episodes) == 4
    assert {item.direction for item in group_episodes} == {"long", "short"}
    assert all(item.total_fee is None for item in group_episodes)
    assert all(item.realized_pnl_net is None for item in group_episodes)
    assert all(
        allocation.allocated_fee is None
        for item in group_episodes
        for allocation in item.evidence
    )
    assert episode_preview.execution_group_count == 1
    assert episode_preview.group_fee_affected_episode_count == 2
    assert episode_preview.leg_fee_attribution_complete is False
    assert (
        episode_preview.retained_execution_group_fee_total
        == Decimal("8.0800000000")
    )
    assert episode_preview.fee_conserved is True
    assert episode_preview.fee_conservation_by_currency == {
        "USD": {
            "ordinary_source": Decimal("0.3002000000"),
            "ordinary_allocated": Decimal("0.3002000000"),
            "retained_execution_group": Decimal("8.0800000000"),
            "source_known": Decimal("8.3802000000"),
            "accounted": Decimal("8.3802000000"),
        }
    }
    assert episode_preview.headline_exclusion_counts[
        "group_fee_unallocated"
    ] == 2
    assert episode_preview.headline_episode_count == 0
    assert episode_preview.headline_total_fee is None
    assert episode_preview.headline_realized_pnl_net is None

    with pytest.raises(
        EpisodeRepositoryError,
        match="execution-group fee.*explicit acceptance",
    ):
        append_canonical_position_episode_build(
            result.canonical_set_id,
            expected_canonical_set_sha256=planned_hash,
            expected_build_key=episode_preview.build_key,
            accept_assumed_flat=True,
            accept_group_fee_scope=False,
        )
    with db.session_scope() as session:
        assert session.execute(
            select(func.count(EpisodeBuild.id))
        ).scalar_one() == 0

    episode_build = append_canonical_position_episode_build(
        result.canonical_set_id,
        expected_canonical_set_sha256=planned_hash,
        expected_build_key=episode_preview.build_key,
        accept_assumed_flat=True,
        accept_group_fee_scope=True,
    )
    assert episode_build.duplicate is False
    assert episode_build.position_episode_count == 3
    assert episode_build.evidence_allocation_count == 6
    stored_summary = get_episode_summary(episode_build.build_id)
    assert stored_summary is not None
    assert stored_summary.retained_execution_group_fee_total == Decimal(
        "8.0800000000"
    )
    assert stored_summary.group_fee_affected_episode_count == 2
    assert stored_summary.leg_fee_attribution_complete is False
    assert stored_summary.fee_conserved is True
    assert stored_summary.headline_exclusion_counts[
        "group_fee_unallocated"
    ] == 2

    with db.session_scope() as session:
        stored_group_episodes = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == episode_build.build_id,
                PositionEpisode.raw_symbol.in_(group_symbols),
            )
        ).scalars().all()
        assert len(stored_group_episodes) == 2
        assert all(item.total_fee is None for item in stored_group_episodes)
        assert all(item.realized_pnl_net is None for item in stored_group_episodes)
        assert all(
            '"group_fee_unallocated":true' in item.evidence_summary_json
            for item in stored_group_episodes
        )
        assert all(
            '"leg_fee_attribution_complete":false' in item.completeness_json
            for item in stored_group_episodes
        )
        stored_group_episode_ids = {item.id for item in stored_group_episodes}
        stored_group_allocations = session.execute(
            select(PositionEpisodeEvidence).where(
                PositionEpisodeEvidence.position_episode_id.in_(
                    stored_group_episode_ids
                )
            )
        ).scalars().all()
        assert len(stored_group_allocations) == 4
        assert all(item.allocated_fee is None for item in stored_group_allocations)

    with pytest.raises(
        EpisodeBuildActivationError,
        match="execution-group fee.*explicit acceptance",
    ):
        activate_episode_build(
            episode_build.build_id,
            episode_build.build_key,
            expected_current_activation_id=None,
            expected_current_build_id=None,
            accept_assumed_flat=True,
            accept_group_fee_scope=False,
        )
    with db.session_scope() as session:
        assert session.execute(
            select(func.count(EpisodeBuildActivation.id))
        ).scalar_one() == 0

    activation = activate_episode_build(
        episode_build.build_id,
        episode_build.build_key,
        expected_current_activation_id=None,
        expected_current_build_id=None,
        accept_assumed_flat=True,
        accept_group_fee_scope=True,
    )
    assert activation.duplicate is False
    assert activation.state.current_build_id == episode_build.build_id

    replay_plan = plan_openapi_import(preview, payload)
    assert replay_plan.confirm_allowed is True
    assert replay_plan.write_plan["already_imported"] is True
    assert replay_plan.canonical_impact.canonical_set_sha256 == planned_hash
    replay_result = confirm_openapi_import_plan(
        preview,
        payload,
        preview_key=replay_plan.preview_key,
        acknowledge_partial_window=False,
    )
    assert replay_result.status == "already_present"
    assert replay_result.duplicate is True
    assert replay_result.canonical_set_sha256 == planned_hash
    assert not any(replay_result.appended.values())


def test_openapi_plan_blocks_legacy_source_without_combo_capability_proof():
    statement = replace(
        _statement(),
        rows_total=2,
        orders=(_statement().orders[0],),
    )
    import_statement_batch(statement)
    preview = _openapi_preview(batch_suffix="6")
    preview = replace(
        preview,
        orders=(
            replace(
                preview.orders[0],
                combo_definition_available=False,
            ),
        ),
    )

    payload = _openapi_payload(preview)
    before = _combo_persistence_counts()
    plan = plan_openapi_import(preview, payload)

    assert plan.confirm_allowed is False
    assert plan.coverage["scope"] == "full_batch"
    issue_codes = {issue.code for issue in plan.issues}
    assert {
        issue.code
        for issue in plan.issues
        if issue.entity_kind == "source_capability"
    } == {
        "combo_definition_capability_unproved"
    }
    assert "combo_order_requires_group_projection" not in issue_codes
    assert plan.write_plan["unclassified_parent_observations"] == 1
    assert plan.write_plan["execution_group_observations"] == 0
    assert _combo_persistence_counts() == before
    with pytest.raises(OpenApiPlanError, match="plan_contains_blocking_issues"):
        confirm_openapi_import_plan(
            preview,
            payload,
            preview_key=plan.preview_key,
            acknowledge_partial_window=True,
        )
    assert _combo_persistence_counts() == before


def test_openapi_plan_blocks_unbound_incremental_tail():
    statement = replace(
        _statement(),
        rows_total=2,
        orders=(_statement().orders[0],),
    )
    import_statement_batch(statement)
    preview = _openapi_preview_with_api_only_order(
        ordered_at=datetime(2026, 7, 20, 10, 0, tzinfo=ET),
    )
    preview = replace(
        preview,
        metadata=replace(preview.metadata, account_binding=None),
    )

    plan = plan_openapi_import(preview, _openapi_payload(preview))

    assert plan.confirm_allowed is False
    assert "incremental_tail_account_unbound" in {
        issue.code for issue in plan.issues
    }


def test_concurrent_cold_start_never_publishes_uninitialized_database(
    monkeypatch,
):
    DatabaseManager.reset_instance()
    original_initialize = DatabaseManager._initialize
    initialize_entered = threading.Event()
    allow_initialize = threading.Event()
    first_call_lock = threading.Lock()
    first_call = True

    def delayed_initialize(self, db_url=None):
        nonlocal first_call
        with first_call_lock:
            should_delay = first_call
            first_call = False
        if should_delay:
            initialize_entered.set()
            if not allow_initialize.wait(timeout=5):
                raise RuntimeError("test did not release database initialization")
        return original_initialize(self, db_url)

    monkeypatch.setattr(
        DatabaseManager,
        "_initialize",
        delayed_initialize,
    )

    second_started = threading.Event()

    def second_get_db():
        second_started.set()
        return get_db()

    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(get_db)
        assert initialize_entered.wait(timeout=2)

        second = executor.submit(second_get_db)
        assert second_started.wait(timeout=2)
        with pytest.raises(FutureTimeout):
            second.result(timeout=0.1)

        allow_initialize.set()
        first_db = first.result(timeout=5)
        second_db = second.result(timeout=5)

        assert first_db is second_db
        assert first_db._initialized is True
        assert first_db._engine is not None

        # The exact first-request path may call the ledger schema initializer
        # concurrently after the shared DatabaseManager becomes available.
        futures = [
            executor.submit(init_ledger_schema)
            for _ in range(3)
        ]
        for future in futures:
            future.result(timeout=5)


def test_partial_statement_requires_explicit_permission(isolated_sqlite):
    with pytest.raises(LedgerImportError, match="explicitly allow"):
        import_statement_batch(_statement())

    assert not isolated_sqlite.exists()


def test_import_preserves_aggregate_order_without_synthetic_fill():
    result = import_statement_batch(_statement(), allow_partial=True)

    assert result.duplicate is False
    assert result.analysis_level == "partial"
    assert result.order_observations == 2
    assert result.fill_observations == 2
    assert result.journal_legacy_written is False

    db = get_db()
    with db.session_scope() as session:
        batch = session.get(ImportBatch, result.batch_id)
        assert batch is not None
        assert batch.analysis_level == "partial"
        assert batch.source_kind == "csv"
        assert batch.completeness_score == Decimal("0.5000")
        assert "source_path_retained\":false" in batch.provenance_json

        orders = session.execute(
            select(BrokerOrderObservation).order_by(
                BrokerOrderObservation.source_row_number
            )
        ).scalars().all()
        assert [order.evidence_level for order in orders] == [
            "fill_detail",
            "aggregate_only",
        ]
        assert orders[1].summary_filled_quantity == Decimal("1.0000000000")
        assert orders[1].summary_average_fill_price == Decimal("3.1000000000")

        fill_counts = dict(
            session.execute(
                select(
                    BrokerFillObservation.broker_order_observation_id,
                    func.count(BrokerFillObservation.id),
                ).group_by(BrokerFillObservation.broker_order_observation_id)
            ).all()
        )
        assert fill_counts == {orders[0].id: 2}
        fills = session.execute(
            select(BrokerFillObservation).order_by(
                BrokerFillObservation.source_row_number
            )
        ).scalars().all()
        assert [fill.source_row_number for fill in fills] == [2, 3]
        assert fills[0].observation_key != fills[1].observation_key


def test_reimport_is_idempotent_and_does_not_add_observations():
    first = import_statement_batch(_statement(), allow_partial=True)
    second = import_statement_batch(_statement(), allow_partial=True)

    assert second.duplicate is True
    assert second.batch_id == first.batch_id
    db = get_db()
    with db.session_scope() as session:
        assert session.execute(select(func.count(ImportBatch.id))).scalar_one() == 1
        assert session.execute(
            select(func.count(BrokerOrderObservation.id))
        ).scalar_one() == 2
        assert session.execute(
            select(func.count(BrokerFillObservation.id))
        ).scalar_one() == 2


def _combo_statement() -> StatementParseResult:
    base = _statement()
    detailed = base.orders[0]
    leg_fill_time = datetime(2026, 7, 20, 11, 0, 16, tzinfo=ET)
    combo = StatementOrder(
        source_row=6,
        derived_order_id="moomoo_csv_combo_parent",
        symbol="EXAMPLE260717P190/200",
        name="Vertical",
        side="SELL",
        status="FILLED",
        order_quantity=Decimal("2"),
        order_price=Decimal("6.70"),
        order_price_text="6.70",
        order_amount=Decimal("1340.00"),
        order_time=datetime(2026, 7, 20, 11, 0, tzinfo=ET),
        order_type="Limit",
        time_in_force="Day",
        session="Regular Trading Hours",
        market="US",
        currency="USD",
        summary_filled_quantity=Decimal("2"),
        summary_average_price=Decimal("7.00"),
        fills=(),
        fee_components=FEE_COMPONENTS,
        total_fee=Decimal("8.08"),
        evidence_level="combo_parent",
        order_kind="combo_parent",
        combo_unit_quantity=Decimal("2"),
        combo_underlying="EXAMPLE",
        combo_expiry=date(2026, 7, 17),
        combo_option_right="P",
        combo_strikes_text="190/200",
        combo_legs=(
            StatementComboLeg(
                source_row=7,
                symbol="EXAMPLE260717P190000",
                name="Deidentified leg",
                side="BUY",
                order_quantity=Decimal("2"),
                fills=(
                    StatementFill(
                        source_row=7,
                        quantity=Decimal("2"),
                        price=Decimal("24.30"),
                        filled_at=leg_fill_time,
                        amount=Decimal("4860"),
                        market="US",
                        currency="USD",
                    ),
                ),
            ),
            StatementComboLeg(
                source_row=9,
                symbol="EXAMPLE260717P200000",
                name="Deidentified leg",
                side="SELL",
                order_quantity=Decimal("2"),
                fills=(
                    StatementFill(
                        source_row=9,
                        quantity=Decimal("2"),
                        price=Decimal("31.30"),
                        filled_at=leg_fill_time,
                        amount=Decimal("6260"),
                        market="US",
                        currency="USD",
                    ),
                ),
            ),
        ),
    )
    return replace(
        base,
        source_sha256="b" * 64,
        rows_total=8,
        orders=(detailed, combo),
    )


def test_import_stores_combo_parent_as_audit_only_execution_group():
    statement = _combo_statement()
    # A combo parent alone forces the explicit partial acknowledgement.
    with pytest.raises(LedgerImportError, match="explicitly allow"):
        import_statement_batch(statement)

    result = import_statement_batch(statement, allow_partial=True)
    assert result.duplicate is False
    assert result.analysis_level == "partial"
    assert result.order_observations == 1
    assert result.fill_observations == 2
    assert result.execution_group_observations == 1

    db = get_db()
    with db.session_scope() as session:
        orders = session.execute(
            select(BrokerOrderObservation)
        ).scalars().all()
        # The combo parent is never disguised as a single-leg order row.
        assert [order.source_order_id for order in orders] == [
            "moomoo_csv_detailed"
        ]
        groups = session.execute(
            select(BrokerExecutionGroupObservation)
        ).scalars().all()
        assert len(groups) == 1
        group = groups[0]
        assert group.source_execution_group_id == "moomoo_csv_combo_parent"
        assert group.raw_parent_symbol == "EXAMPLE260717P190/200"
        assert group.strategy_type == "VERTICAL"
        assert (
            group.parent_quantity_semantics
            == "csv_combo_package_units_audit_only"
        )
        assert group.parent_price_semantics == "csv_net_price_audit_only"
        assert group.evidence_level == "combo_parent_aggregate"
        assert Decimal(group.group_order_quantity) == Decimal("2")
        assert Decimal(group.broker_reported_dealt_quantity) == Decimal("2")
        provenance = json.loads(group.provenance_json)
        assert provenance["canonical_scope"] == "excluded_csv_combo_parent"
        assert provenance["parent_economics_used_for_positions"] is False
        evidence = json.loads(group.evidence_json)
        assert evidence["combo_definition_available"] is False
        assert len(evidence["leg_display_rows"]) == 2

        # No leg-level facts are invented from CSV display rows, and the
        # group fee stays at execution-group scope.
        assert session.execute(
            select(func.count(BrokerExecutionGroupLegObservation.id))
        ).scalar_one() == 0
        group_fees = session.execute(
            select(BrokerExecutionGroupFeeObservation)
        ).scalars().all()
        assert len(group_fees) == 1
        assert Decimal(group_fees[0].total_fee) == Decimal("8.08")
        fee_provenance = json.loads(group_fees[0].provenance_json)
        assert fee_provenance["allocation_to_legs_or_fills"] is False
        fills = session.execute(
            select(BrokerFillObservation)
        ).scalars().all()
        assert all(
            fill.source_order_id != "moomoo_csv_combo_parent"
            for fill in fills
        )

    duplicate = import_statement_batch(statement, allow_partial=True)
    assert duplicate.duplicate is True
    assert duplicate.execution_group_observations == 1
    assert duplicate.order_observations == 1


def test_canonical_inputs_exclude_csv_combo_parent_fail_closed():
    import_statement_batch(_combo_statement(), allow_partial=True)

    inputs = load_canonical_observation_inputs()
    assert inputs.execution_groups == ()
    assert inputs.excluded_csv_execution_group_observations == 1

    evidence_set = canonicalize_observations(
        inputs.orders,
        inputs.fills,
        inputs.execution_groups,
    )
    # The CSV combo parent creates neither a canonical order nor a canonical
    # execution group; existing single-leg facts stay untouched.
    assert evidence_set.execution_groups == ()
    assert all(
        order.source_order_id != "moomoo_csv_combo_parent"
        for order in evidence_set.orders
    )
    assert len(evidence_set.orders) == 1


def test_sqlite_rejects_update_and_delete_on_v2_evidence():
    result = import_statement_batch(_statement(), allow_partial=True)
    db = get_db()

    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.execute(
                update(ImportBatch)
                .where(ImportBatch.id == result.batch_id)
                .values(status="mutated")
            )

    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.execute(
                delete(ImportBatch).where(ImportBatch.id == result.batch_id)
            )

    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            batch = session.get(ImportBatch, result.batch_id)
            assert batch is not None
            values = {
                column.name: getattr(batch, column.name)
                for column in ImportBatch.__table__.columns
            }
            values["status"] = "replaced"
            session.execute(
                insert(ImportBatch).values(**values).prefix_with("OR REPLACE")
            )

    with db.session_scope() as session:
        batch = session.get(ImportBatch, result.batch_id)
        assert batch is not None
        assert batch.status == "accepted"


def test_data_health_exposes_partial_reconciliation_window_and_utc_times():
    reconciliation = StatementReconciliation(
        analysis_ready=True,
        window_start=datetime(2026, 7, 20, 9, 0, tzinfo=ET),
        window_end=datetime(2026, 7, 20, 9, 59, 59, tzinfo=ET),
        statement_orders=1,
        api_orders=1,
        matched_orders=1,
        statement_only_orders=0,
        api_only_orders=0,
        matched_filled_orders=1,
        status_mismatches=0,
        fill_count_mismatches=0,
        filled_quantity_mismatches=0,
        fill_vwap_mismatches=0,
        fee_total_mismatches=0,
        fee_component_mismatches=0,
        ambiguous_identity_keys=0,
        statement_fee_total=Decimal("0.3002"),
        api_fee_total=Decimal("0.3002"),
        matches=(
            StatementApiMatch(
                statement_order_id="moomoo_csv_detailed",
                broker_order_id="broker-order-1",
            ),
        ),
    )
    first = import_statement_batch(_statement(), allow_partial=True)
    before = get_latest_data_health()
    assert before is not None
    assert before.reconciliation_status == "not_run"

    with pytest.raises(LedgerImportError, match="export SHA-256"):
        import_statement_batch(
            _statement(),
            allow_partial=True,
            reconciliation=reconciliation,
        )

    non_overlapping = replace(
        reconciliation,
        window_start=datetime(2030, 1, 1, 9, 0, tzinfo=ET),
        window_end=datetime(2030, 1, 1, 10, 0, tzinfo=ET),
    )
    with pytest.raises(LedgerImportError, match="does not overlap"):
        import_statement_batch(
            _statement(),
            allow_partial=True,
            reconciliation=non_overlapping,
            reconciliation_source_sha256="c" * 64,
        )

    second = import_statement_batch(
        _statement(),
        allow_partial=True,
        reconciliation=reconciliation,
        reconciliation_source_sha256="b" * 64,
    )
    third = import_statement_batch(
        _statement(),
        allow_partial=True,
        reconciliation=reconciliation,
        reconciliation_source_sha256="b" * 64,
    )

    health = get_latest_data_health()

    assert second.duplicate is True
    assert second.batch_id == first.batch_id
    assert third.duplicate is True
    assert health is not None
    assert health.reconciliation_status == "passed"
    assert health.reconciliation_scope == "partial_window"
    assert health.reconciled_order_observations == 1
    assert health.reconciliation_window_start == reconciliation.window_start
    assert health.reconciliation_window_end == reconciliation.window_end
    assert health.window_start is not None
    assert health.window_start.utcoffset() == timedelta(0)
    assert health.recorded_at.utcoffset() == timedelta(0)
    db = get_db()
    with db.session_scope() as session:
        attestations = session.execute(
            select(ReconciliationAttestation)
        ).scalars().all()
        assert len(attestations) == 1
        assert attestations[0].source_sha256 == "b" * 64


def test_data_health_keeps_csv_denominator_when_newer_openapi_batch_exists():
    csv_result = import_statement_batch(_statement(), allow_partial=True)
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
            window_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 7, 2, tzinfo=timezone.utc),
            source_timezone="America/New_York",
            status="accepted",
            analysis_level="exact",
            analysis_ready=True,
            order_observation_count=1,
            fill_observation_count=1,
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

    health = get_latest_data_health()

    assert health is not None
    assert health.batch_id == csv_result.batch_id
    assert health.batch_id != api_batch_id
    assert health.order_observations == 2


def test_openapi_append_requires_confirmation_and_is_idempotent(
    isolated_sqlite,
):
    preview = _openapi_preview()

    with pytest.raises(LedgerImportError, match="explicit confirmation"):
        append_openapi_batch(preview)
    assert not isolated_sqlite.exists()

    first = append_openapi_batch(preview, confirmed=True)
    second = append_openapi_batch(preview, confirmed=True)

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.batch_id == first.batch_id
    assert first.order_observations == 1
    assert first.fill_observations == 2
    assert first.fee_observations == 1
    db = get_db()
    with db.session_scope() as session:
        batch = session.get(ImportBatch, first.batch_id)
        assert batch is not None
        assert batch.source_kind == "openapi"
        assert batch.status == "accepted"
        assert session.execute(
            select(func.count(BrokerOrderObservation.id))
        ).scalar_one() == 1
        assert session.execute(
            select(func.count(BrokerFillObservation.id))
        ).scalar_one() == 2
        fee = session.execute(select(BrokerFeeObservation)).scalar_one()
        assert fee.total_fee == Decimal("0.3002000000")
        assert fee.source_order_id == "broker-order-1"


def test_openapi_append_blocks_stable_fill_and_fee_conflicts():
    append_openapi_batch(_openapi_preview(), confirmed=True)
    conflicting = _openapi_preview(
        batch_suffix="2",
        first_fill_price=Decimal("2.60"),
    )

    with pytest.raises(LedgerImportError, match="deal identity conflicts"):
        append_openapi_batch(conflicting, confirmed=True)

    db = get_db()
    with db.session_scope() as session:
        assert session.execute(select(func.count(ImportBatch.id))).scalar_one() == 1
        assert session.execute(
            select(func.count(BrokerFillObservation.id))
        ).scalar_one() == 2


def test_openapi_append_rejects_negative_total_fee_before_schema_write(
    isolated_sqlite,
):
    preview = _openapi_preview()
    preview = replace(
        preview,
        fees=(replace(preview.fees[0], total_fee=Decimal("-0.01")),),
    )

    with pytest.raises(LedgerImportError, match="cannot be negative"):
        append_openapi_batch(preview, confirmed=True)

    assert not isolated_sqlite.exists()


def _persist_cross_source_canonical_set():
    csv_result = import_statement_batch(_statement(), allow_partial=True)
    api_result = append_openapi_batch(_openapi_preview(), confirmed=True)
    db = get_db()
    with db.session_scope() as session:
        csv_order = session.execute(
            select(BrokerOrderObservation).where(
                BrokerOrderObservation.import_batch_id == csv_result.batch_id,
                BrokerOrderObservation.source_order_id == "moomoo_csv_detailed",
            )
        ).scalar_one()
        api_order = session.execute(
            select(BrokerOrderObservation).where(
                BrokerOrderObservation.import_batch_id == api_result.batch_id
            )
        ).scalar_one()
        csv_order_id = int(csv_order.id)
        api_order_id = int(api_order.id)

    link = append_order_identity_link(
        csv_order_id,
        api_order_id,
        canonical_order_id="broker-order-1",
        confirmed=True,
    )
    duplicate_link = append_order_identity_link(
        csv_order_id,
        api_order_id,
        canonical_order_id="broker-order-1",
        confirmed=True,
    )
    attestation = append_order_fill_set_attestation(
        api_order_id,
        csv_order_id,
        canonical_order_id="broker-order-1",
        confirmed=True,
    )
    duplicate_attestation = append_order_fill_set_attestation(
        api_order_id,
        csv_order_id,
        canonical_order_id="broker-order-1",
        confirmed=True,
    )
    inputs = load_canonical_observation_inputs()
    canonical = canonicalize_observations(inputs.orders, inputs.fills)
    cutoff = datetime(2026, 7, 22, tzinfo=timezone.utc)
    stored = append_canonical_evidence_set(
        canonical,
        source_cutoff_at=cutoff,
        confirmed=True,
    )
    duplicate_stored = append_canonical_evidence_set(
        canonical,
        source_cutoff_at=cutoff,
        confirmed=True,
    )
    return (
        csv_result,
        api_result,
        link,
        duplicate_link,
        attestation,
        duplicate_attestation,
        inputs,
        canonical,
        stored,
        duplicate_stored,
    )


def test_identity_fill_set_and_canonical_snapshot_are_replayable():
    (
        csv_result,
        api_result,
        link,
        duplicate_link,
        attestation,
        duplicate_attestation,
        inputs,
        canonical,
        stored,
        duplicate_stored,
    ) = _persist_cross_source_canonical_set()

    assert link.duplicate is False
    assert duplicate_link.duplicate is True
    assert attestation.duplicate is False
    assert duplicate_attestation.duplicate is True
    assert inputs.source_batch_ids == tuple(
        sorted((csv_result.batch_id, api_result.batch_id))
    )
    assert canonical.analysis_ready is True
    assert canonical.provenance.input_order_observation_count == 3
    assert canonical.provenance.input_fill_observation_count == 4
    assert canonical.provenance.shadowed_fill_observation_count == 2
    assert len(canonical.orders) == 2
    assert len(canonical.fills) == 2
    assert stored.duplicate is False
    assert duplicate_stored.duplicate is True

    db = get_db()
    with db.session_scope() as session:
        set_row = session.get(
            CanonicalEvidenceSetRecord,
            stored.canonical_set_id,
        )
        assert set_row is not None
        assert set_row.analysis_ready is True
        assert set_row.canonical_set_sha256 == canonical.canonical_set_sha256
        assert session.execute(
            select(func.count(CanonicalEvidenceMemberRecord.id))
        ).scalar_one() == 4
        members = session.execute(
            select(CanonicalEvidenceMemberRecord)
        ).scalars().all()
        assert all(
            member.selected_order_observation_id is not None
            or member.selected_fill_observation_id is not None
            for member in members
        )
        provenance = session.execute(
            select(CanonicalEvidenceProvenanceRecord)
        ).scalars().all()
        assert {row.evidence_kind for row in provenance} == {
            "order",
            "fill",
            "fee",
        }
        assert session.execute(
            select(func.count(OrderIdentityLink.id))
        ).scalar_one() == 1
        assert session.execute(
            select(func.count(OrderFillSetAttestation.id))
        ).scalar_one() == 1


def test_canonical_loader_selects_only_latest_csv_baseline():
    first = import_statement_batch(_statement(), allow_partial=True)
    newer_statement = replace(_statement(), source_sha256="e" * 64)
    second = import_statement_batch(newer_statement, allow_partial=True)

    inputs = load_canonical_observation_inputs()

    assert first.batch_id != second.batch_id
    assert inputs.csv_baseline_batch_id == second.batch_id
    assert inputs.source_batch_ids == (second.batch_id,)
    assert len(inputs.orders) == 2
    assert len(inputs.fills) == 2
    assert {item.import_batch_id for item in inputs.orders} == {second.batch_id}
    assert {item.import_batch_id for item in inputs.fills} == {second.batch_id}


def test_atomic_openapi_bundle_commits_all_layers_together():
    csv_result = import_statement_batch(_statement(), allow_partial=True)
    db = get_db()
    with db.session_scope() as session:
        csv_order_id = session.execute(
            select(BrokerOrderObservation.id).where(
                BrokerOrderObservation.import_batch_id == csv_result.batch_id,
                BrokerOrderObservation.source_order_id == "moomoo_csv_detailed",
            )
        ).scalar_one()

    result = append_openapi_canonical_bundle(
        _openapi_preview(),
        order_identity_matches=((csv_order_id, "broker-order-1"),),
        fill_set_shadow_order_observation_ids=(csv_order_id,),
        confirmed=True,
    )
    duplicate = append_openapi_canonical_bundle(
        _openapi_preview(),
        order_identity_matches=((csv_order_id, "broker-order-1"),),
        fill_set_shadow_order_observation_ids=(csv_order_id,),
        confirmed=True,
    )

    assert result.csv_baseline_batch_id == csv_result.batch_id
    assert result.source_batch_ids == tuple(
        sorted((csv_result.batch_id, result.import_result.batch_id))
    )
    assert len(result.order_identity_links) == 1
    assert not result.deal_identity_links
    assert len(result.fill_set_attestations) == 1
    assert result.canonical_set.analysis_ready is True
    assert duplicate.import_result.duplicate is True
    assert duplicate.order_identity_links[0].duplicate is True
    assert duplicate.fill_set_attestations[0].duplicate is True
    assert duplicate.canonical_set.duplicate is True
    with db.session_scope() as session:
        assert session.execute(
            select(func.count(BrokerFeeObservation.id))
        ).scalar_one() == 1
        assert session.execute(
            select(func.count(OrderIdentityLink.id))
        ).scalar_one() == 1
        assert session.execute(
            select(func.count(OrderFillSetAttestation.id))
        ).scalar_one() == 1
        assert session.execute(
            select(func.count(CanonicalEvidenceSetRecord.id))
        ).scalar_one() == 1


def test_atomic_openapi_bundle_rolls_back_every_layer_on_bad_link():
    csv_result = import_statement_batch(_statement(), allow_partial=True)
    db = get_db()
    with db.session_scope() as session:
        csv_order_id = session.execute(
            select(BrokerOrderObservation.id).where(
                BrokerOrderObservation.import_batch_id == csv_result.batch_id,
                BrokerOrderObservation.source_order_id == "moomoo_csv_detailed",
            )
        ).scalar_one()

    with pytest.raises(LedgerImportError, match="does not reference"):
        append_openapi_canonical_bundle(
            _openapi_preview(),
            order_identity_matches=((csv_order_id, "missing-order"),),
            confirmed=True,
        )

    with db.session_scope() as session:
        assert session.execute(
            select(func.count(ImportBatch.id))
        ).scalar_one() == 1
        assert session.execute(
            select(func.count(BrokerFeeObservation.id))
        ).scalar_one() == 0
        assert session.execute(
            select(func.count(OrderIdentityLink.id))
        ).scalar_one() == 0
        assert session.execute(
            select(func.count(DealIdentityLink.id))
        ).scalar_one() == 0
        assert session.execute(
            select(func.count(CanonicalEvidenceSetRecord.id))
        ).scalar_one() == 0


def test_unique_exact_deal_links_rebuild_canonical_deal_ids():
    statement = _statement()
    detailed = statement.orders[0]
    distinct_second = replace(
        detailed.fills[1],
        filled_at=detailed.fills[1].filled_at + timedelta(seconds=1),
    )
    detailed = replace(detailed, fills=(detailed.fills[0], distinct_second))
    statement = replace(statement, orders=(detailed, statement.orders[1]))
    preview = _openapi_preview()
    preview = replace(
        preview,
        fills=(
            preview.fills[0],
            replace(
                preview.fills[1],
                filled_at=preview.fills[1].filled_at + timedelta(seconds=1),
            ),
        ),
    )
    csv_result = import_statement_batch(statement, allow_partial=True)
    api_result = append_openapi_batch(preview, confirmed=True)
    db = get_db()
    with db.session_scope() as session:
        csv_order = session.execute(
            select(BrokerOrderObservation).where(
                BrokerOrderObservation.import_batch_id == csv_result.batch_id,
                BrokerOrderObservation.source_order_id == "moomoo_csv_detailed",
            )
        ).scalar_one()
        api_order = session.execute(
            select(BrokerOrderObservation).where(
                BrokerOrderObservation.import_batch_id == api_result.batch_id
            )
        ).scalar_one()
        csv_fills = session.execute(
            select(BrokerFillObservation)
            .where(
                BrokerFillObservation.broker_order_observation_id == csv_order.id
            )
            .order_by(BrokerFillObservation.filled_at)
        ).scalars().all()
        api_fills = session.execute(
            select(BrokerFillObservation)
            .where(
                BrokerFillObservation.broker_order_observation_id == api_order.id
            )
            .order_by(BrokerFillObservation.filled_at)
        ).scalars().all()
        csv_order_id = int(csv_order.id)
        api_order_id = int(api_order.id)
        csv_fill_ids = tuple(int(fill.id) for fill in csv_fills)
        api_fill_ids = tuple(int(fill.id) for fill in api_fills)

    append_order_identity_link(
        csv_order_id,
        api_order_id,
        canonical_order_id="broker-order-1",
        confirmed=True,
    )
    first = append_deal_identity_link(
        csv_fill_ids[0],
        api_fill_ids[0],
        canonical_order_id="broker-order-1",
        canonical_deal_id="broker-deal-1",
        confirmed=True,
    )
    second = append_deal_identity_link(
        csv_fill_ids[1],
        api_fill_ids[1],
        canonical_order_id="broker-order-1",
        canonical_deal_id="broker-deal-2",
        confirmed=True,
    )

    inputs = load_canonical_observation_inputs()
    canonical = canonicalize_observations(inputs.orders, inputs.fills)

    assert first.duplicate is False
    assert second.duplicate is False
    assert canonical.analysis_ready is True
    assert len(canonical.fills) == 2
    assert canonical.provenance.duplicate_fill_observation_count == 2
    with db.session_scope() as session:
        assert session.execute(
            select(func.count(DealIdentityLink.id))
        ).scalar_one() == 2


def test_every_new_phase13b_table_has_update_and_delete_guards():
    _persist_cross_source_canonical_set()
    expected_tables = {
        "journal_v2_broker_fee_observations",
        "journal_v2_order_identity_links",
        "journal_v2_deal_identity_links",
        "journal_v2_order_fill_set_attestations",
        "journal_v2_canonical_evidence_sets",
        "journal_v2_canonical_evidence_members",
        "journal_v2_canonical_evidence_issues",
        "journal_v2_canonical_evidence_provenance",
    }
    db = get_db()
    with db.session_scope() as session:
        trigger_rows = session.execute(
            select(func.count()).select_from(
                # Textual sqlite_master access is kept inside this SQLite-only
                # repository test and does not alter production schema code.
                CanonicalEvidenceSetRecord
            )
        ).scalar_one()
        assert trigger_rows == 1
        names = {
            row[0]
            for row in session.connection().exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).all()
        }
        for table_name in expected_tables:
            assert f"trg_{table_name}_update_immutable" in names
            assert f"trg_{table_name}_delete_immutable" in names

    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.execute(
                update(CanonicalEvidenceSetRecord).values(analysis_ready=False)
            )

    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.execute(delete(OrderIdentityLink))

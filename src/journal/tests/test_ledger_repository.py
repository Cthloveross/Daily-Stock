# -*- coding: utf-8 -*-
"""Tests for append-only Journal v2 statement persistence."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from src.journal.brokers.moomoo_statement import (
    StatementFill,
    StatementApiMatch,
    StatementOrder,
    StatementParseResult,
    StatementReconciliation,
)
from src.journal.brokers.moomoo_openapi_export import (
    OpenApiBatchMetadata,
    OpenApiExportPreview,
    OpenApiFeeObservation,
    OpenApiFillObservation,
    OpenApiOrderObservation,
    OpenApiReconciliation,
)
from src.journal.ledger.canonical import canonicalize_observations
from src.journal.ledger.models import (
    BrokerFillObservation,
    BrokerFeeObservation,
    BrokerOrderObservation,
    CanonicalEvidenceMemberRecord,
    CanonicalEvidenceProvenanceRecord,
    CanonicalEvidenceSetRecord,
    DealIdentityLink,
    ImportBatch,
    OrderFillSetAttestation,
    OrderIdentityLink,
    ReconciliationAttestation,
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
    load_canonical_observation_inputs,
)
from src.storage import get_db


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

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from src.journal.brokers.moomoo_position_snapshot import (
    PositionSnapshotConfig,
    run_position_snapshot_probe,
)
from src.journal.brokers.moomoo_openapi_export import parse_openapi_export
from src.journal.ledger import position_snapshot_continuity as continuity
from src.journal.ledger.episode_repository import (
    CanonicalBoundaryFill,
    CanonicalBoundaryOrder,
    VerifiedCanonicalEvidenceProjection,
    VerifiedCanonicalEpisodeEvidenceProjection,
)
from src.journal.ledger.episodes import (
    CanonicalFillEvidence,
    CanonicalOrderEvidence,
    InstrumentIdentity,
)
from src.journal.ledger.position_snapshot_episode_preview import (
    FuturePositionEpisodePreviewError,
    preview_fenced_position_episodes,
)
from src.journal.ledger.models import CanonicalEvidenceSetRecord, ImportBatch
from src.journal.ledger.position_snapshot_continuity import (
    PositionSnapshotContinuityError,
    assess_latest_position_snapshot_continuity,
    assess_position_snapshot_continuity_in_session,
)
from src.journal.ledger.position_snapshot_repository import (
    confirm_position_snapshot_artifact,
    init_position_snapshot_schema,
    plan_position_snapshot,
    save_position_snapshot_artifact,
)
from src.journal.ledger.refresh_models import (
    JournalRefreshArtifact,
    JournalRefreshPublication,
)
from src.journal.ledger.repository import init_ledger_schema
from src.storage import get_db


ACCOUNT_KEY = "primary"
ACCOUNT_SECRET = "continuity-test-secret-at-least-32-bytes"
PADDED_OCC = "US.AAPL260821C00200000"
VARIABLE_OCC = "AAPL260821C200000"


class _EnumValues:
    REAL = "REAL"
    US = "US"


SDK = SimpleNamespace(RET_OK=0, TrdEnv=_EnumValues, TrdMarket=_EnumValues)


class _TradeContext:
    def __init__(self, reads: list[list[dict[str, Any]]]) -> None:
        self.reads = reads
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def get_acc_list(self):
        return 0, [
            {
                "acc_id": "101",
                "trd_env": "REAL",
                "trdmarket_auth": ["US"],
                "acc_status": "ACTIVE",
            }
        ]

    def position_list_query(self, **kwargs):
        index = len(self.calls)
        self.calls.append(kwargs)
        return 0, list(self.reads[min(index, len(self.reads) - 1)])

    def close(self):
        self.closed = True


class _QuoteContext:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.closed = False

    def get_market_snapshot(self, codes):
        requested = set(codes)
        return 0, [row for row in self.rows if row.get("code") in requested]

    def close(self):
        self.closed = True


@dataclass(frozen=True)
class _PublicationFixture:
    publication_id: int
    publication_key: str
    batch_id: int
    canonical_set_id: int
    canonical_set_sha256: str
    watermark: datetime


@dataclass(frozen=True)
class _Scenario:
    anchor: _PublicationFixture
    target: _PublicationFixture
    snapshot: Any
    artifact: Any


def _position(symbol: str = PADDED_OCC) -> dict[str, Any]:
    return {
        "position_side": "LONG",
        "code": symbol,
        "position_market": "US",
        "qty": "2.00",
        "can_sell_qty": "1.00",
        "currency": "USD",
        "cost_price": "1.2500",
        "cost_price_valid": True,
        "average_cost": "1.2500",
        "diluted_cost": "-0.4000",
    }


def _contract_spec(symbol: str = PADDED_OCC) -> dict[str, Any]:
    return {
        "code": symbol,
        "lot_size": "100.0",
        "option_contract_size": 100,
        "option_contract_multiplier": "100.00",
    }


def _clock(base: datetime):
    values = iter(base + timedelta(milliseconds=index) for index in range(20))
    return lambda: next(values)


def _snapshot_payload(
    *,
    base: datetime,
    symbol: str = PADDED_OCC,
) -> dict[str, Any]:
    position = _position(symbol)
    trade_context = _TradeContext(
        [[position], [copy.deepcopy(position)]]
    )
    quote_context = _QuoteContext([_contract_spec(symbol)])
    result = run_position_snapshot_probe(
        PositionSnapshotConfig(
            account_binding_secret=ACCOUNT_SECRET,
            retries=0,
            retry_delay=0,
        ),
        sdk=SDK,
        context_factory=lambda _config, _sdk: trade_context,
        quote_context_factory=lambda _config, _sdk: quote_context,
        tcp_probe=lambda *_args: None,
        sleeper=lambda _seconds: None,
        clock=_clock(base),
    )
    assert trade_context.closed is True
    assert quote_context.closed is True
    return result.export_payload


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    encoded = value if isinstance(value, str) else _canonical_json(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _binding(payload: dict[str, Any]) -> str:
    return str(payload["account"]["binding"])


def _refresh_payload(
    *,
    binding: str,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    zone = ZoneInfo("America/New_York")
    start = window_start.astimezone(zone)
    end = window_end.astimezone(zone)
    window = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": "America/New_York",
        "chunks": 1,
        "max_chunk_days": 7,
    }
    reconciliation = {
        "filled_orders_without_fills": 0,
        "fills_without_orders": 0,
        "filled_quantity_mismatches": 0,
        "fill_code_mismatches": 0,
        "fill_side_mismatches": 0,
        "fill_average_price_mismatches": 0,
        "filled_orders_without_fees": 0,
    }
    return {
        "schema": "dsa.moomoo.readonly-export.v1",
        "generated_at": window_end.astimezone(timezone.utc).isoformat(),
        "mode": "read_only",
        "journal_database_written": False,
        "account": {
            "environment": "LIVE",
            "market": "US",
            "selection": "unique_auto",
            "binding": binding,
        },
        "window": window,
        "summary": {
            "ok": True,
            "retrieval_complete": True,
            "coverage_complete": True,
            "has_activity": False,
            "analysis_ready": True,
            "reconciliation_status": "passed",
            "warnings": ["no_filled_orders_in_window"],
            "mode": "moomoo_readonly_probe",
            "journal_database_written": False,
            "environment": "LIVE",
            "market": "US",
            "account_selection": "unique_auto",
            "window": copy.deepcopy(window),
            "counts": {
                "orders": 0,
                "filled_orders": 0,
                "fills": 0,
                "fees": 0,
                "unique_instruments": 0,
                "option_activity_rows": 0,
            },
            "activity_sides": {"buy": 0, "sell": 0, "other": 0},
            "fee_totals_by_currency": {},
            "activity_time_range": {"first": None, "last": None},
            "deduplicated_rows": {"orders": 0, "fills": 0, "fees": 0},
            "reconciliation": reconciliation,
            "fee_batches": 0,
        },
        "records": {"orders": [], "deals": [], "fees": []},
    }


def _init_real_schema() -> None:
    init_ledger_schema()
    init_position_snapshot_schema()


def _install_publication(
    *,
    binding: str,
    watermark: datetime,
    suffix: str,
    include_batch_ids: Iterable[int] = (),
    window_start: datetime | None = None,
    canonical_reader_name: str = "continuity_test",
    canonical_reader_version: str = "1",
    include_execution_group_contract: bool = True,
) -> _PublicationFixture:
    """Persist an FK-complete publication with a replay-valid canonical root."""
    _init_real_schema()
    resolved_window_start = window_start or watermark - timedelta(days=1)
    payload = _refresh_payload(
        binding=binding,
        window_start=resolved_window_start,
        window_end=watermark,
    )
    preview = parse_openapi_export(payload)
    metadata = preview.metadata
    preview_key = _sha256({"kind": "refresh-preview", "suffix": suffix})
    db = get_db()
    with db.session_scope() as session:
        batch = ImportBatch(
            batch_key=_sha256(
                {
                    "account_key": ACCOUNT_KEY,
                    "broker": "moomoo",
                    "parser_batch_key": metadata.batch_key,
                }
            ),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_kind="openapi",
            source_schema=metadata.source_schema,
            source_sha256=metadata.source_sha256,
            parser_name=metadata.parser_name,
            parser_version=metadata.parser_version,
            window_start=metadata.window_start.astimezone(timezone.utc),
            window_end=metadata.window_end.astimezone(timezone.utc),
            source_timezone=metadata.source_timezone,
            status="accepted",
            analysis_level="exact",
            analysis_ready=True,
            order_observation_count=0,
            fill_observation_count=0,
            rejected_record_count=0,
            reconciliation_status="passed",
            reconciliation_json=_canonical_json(
                metadata.reconciliation.as_dict()
            ),
            completeness_score=Decimal("1"),
            completeness_json="{}",
            warnings_json=_canonical_json(metadata.warnings),
            provenance_json=_canonical_json(
                {
                    "broker": "moomoo",
                    "source_kind": "openapi",
                    "source_schema": metadata.source_schema,
                    "source_sha256": metadata.source_sha256,
                    "evidence_sha256": metadata.evidence_sha256,
                    "parser_batch_key": metadata.batch_key,
                    "parser_name": metadata.parser_name,
                    "parser_version": metadata.parser_version,
                    "account_binding": binding,
                }
            ),
            recorded_at=watermark,
        )
        session.add(batch)
        session.flush()
        source_batch_ids = tuple(
            sorted({*(int(value) for value in include_batch_ids), int(batch.id)})
        )
        source_batches = [session.get(ImportBatch, value) for value in source_batch_ids]
        assert all(value is not None for value in source_batches)
        canonical_provenance = {
            "reader_name": canonical_reader_name,
            "reader_version": canonical_reader_version,
            "batch_keys": sorted(
                str(value.batch_key)
                for value in source_batches
                if value is not None
            ),
            "source_kinds": sorted(
                {
                    str(value.source_kind)
                    for value in source_batches
                    if value is not None
                }
            ),
            "input_order_observation_count": 0,
            "input_fill_observation_count": 0,
            "canonical_order_count": 0,
            "canonical_fill_count": 0,
            "duplicate_order_observation_count": 0,
            "duplicate_fill_observation_count": 0,
            "shadowed_fill_observation_count": 0,
            "shadowed_aggregate_order_count": 0,
            "input_execution_group_observation_count": 0,
            "canonical_execution_group_count": 0,
            "canonical_execution_group_leg_count": 0,
            "duplicate_execution_group_observation_count": 0,
            "blocking_issue_count": 0,
        }
        if not include_execution_group_contract:
            for field_name in (
                "input_execution_group_observation_count",
                "canonical_execution_group_count",
                "canonical_execution_group_leg_count",
                "duplicate_execution_group_observation_count",
            ):
                canonical_provenance.pop(field_name)
        root = {
            "analysis_ready": True,
            "fills": [],
            "issues": [],
            "orders": [],
            "provenance": canonical_provenance,
        }
        if include_execution_group_contract:
            root["execution_groups"] = []
        root_sha256 = _sha256(root)
        set_key = _sha256(
            {
                "account_key": ACCOUNT_KEY,
                "canonical_set_sha256": root_sha256,
                "reader_name": canonical_reader_name,
                "reader_version": canonical_reader_version,
                "source_cutoff_at": watermark.isoformat(),
            }
        )
        canonical = CanonicalEvidenceSetRecord(
            set_key=set_key,
            canonical_set_sha256=root_sha256,
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            reader_name=canonical_reader_name,
            reader_version=canonical_reader_version,
            source_cutoff_at=watermark,
            source_batch_ids_json=_canonical_json(source_batch_ids),
            analysis_ready=True,
            canonical_order_count=0,
            canonical_fill_count=0,
            blocking_issue_count=0,
            canonical_payload_json=_canonical_json(root),
            provenance_json=_canonical_json(canonical_provenance),
            recorded_at=watermark,
        )
        session.add(canonical)
        session.flush()
        artifact_key = _sha256(
            {
                "kind": "journal_refresh_artifact_v1",
                "account_key": ACCOUNT_KEY,
                "account_binding": binding,
                "source_sha256": metadata.source_sha256,
                "preview_key": preview_key,
            }
        )
        artifact = JournalRefreshArtifact(
            artifact_key=artifact_key,
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            account_binding=binding,
            environment="LIVE",
            market="US",
            window_start=metadata.window_start.astimezone(timezone.utc),
            window_end=metadata.window_end.astimezone(timezone.utc),
            generated_at=metadata.generated_at,
            source_sha256=metadata.source_sha256,
            evidence_sha256=metadata.evidence_sha256,
            preview_key=preview_key,
            confirm_allowed=True,
            payload_json=_canonical_json(payload),
            expires_at=watermark + timedelta(minutes=30),
            recorded_at=watermark,
        )
        session.add(artifact)
        session.flush()
        publication_key = _sha256(
            {
                "kind": "journal_refresh_publication_v1",
                "artifact_key": artifact_key,
                "import_batch_id": int(batch.id),
                "canonical_set_id": int(canonical.id),
                "canonical_set_sha256": root_sha256,
            }
        )
        publication = JournalRefreshPublication(
            publication_key=publication_key,
            refresh_artifact_id=int(artifact.id),
            import_batch_id=int(batch.id),
            canonical_set_id=int(canonical.id),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            account_binding=binding,
            broker_queried_through=watermark,
            latest_fill_at=None,
            evidence_published_through=watermark,
            import_duplicate=False,
            recorded_at=watermark,
        )
        session.add(publication)
        session.flush()
        return _PublicationFixture(
            publication_id=int(publication.id),
            publication_key=publication_key,
            batch_id=int(batch.id),
            canonical_set_id=int(canonical.id),
            canonical_set_sha256=root_sha256,
            watermark=watermark,
        )


def _confirm_snapshot(*, base: datetime) -> tuple[Any, Any]:
    payload = _snapshot_payload(base=base)
    plan = plan_position_snapshot(
        payload,
        account_key=ACCOUNT_KEY,
        expected_account_binding_id=_binding(payload),
    )
    artifact = save_position_snapshot_artifact(plan, payload)
    confirmation = confirm_position_snapshot_artifact(
        artifact.artifact_id,
        preview_key=artifact.preview_key,
        acknowledge_future_only=True,
        expected_account_binding_id=_binding(payload),
        account_key=ACCOUNT_KEY,
    )
    return artifact, confirmation.snapshot


def _ready_scenario(
    *,
    target_window_start: datetime | None = None,
    canonical_reader_name: str = "continuity_test",
    canonical_reader_version: str = "1",
    include_execution_group_contract: bool = True,
) -> _Scenario:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=2)
    payload = _snapshot_payload(base=base)
    binding = _binding(payload)
    anchor = _install_publication(
        binding=binding,
        watermark=base - timedelta(seconds=1),
        suffix="anchor",
        canonical_reader_name=canonical_reader_name,
        canonical_reader_version=canonical_reader_version,
        include_execution_group_contract=include_execution_group_contract,
    )
    plan = plan_position_snapshot(
        payload,
        account_key=ACCOUNT_KEY,
        expected_account_binding_id=binding,
    )
    artifact = save_position_snapshot_artifact(plan, payload)
    snapshot = confirm_position_snapshot_artifact(
        artifact.artifact_id,
        preview_key=artifact.preview_key,
        acknowledge_future_only=True,
        expected_account_binding_id=binding,
        account_key=ACCOUNT_KEY,
    ).snapshot
    target = _install_publication(
        binding=binding,
        watermark=snapshot.operation_completed_at + timedelta(seconds=1),
        suffix="target",
        include_batch_ids=(anchor.batch_id,),
        window_start=target_window_start or anchor.watermark,
        canonical_reader_name=canonical_reader_name,
        canonical_reader_version=canonical_reader_version,
        include_execution_group_contract=include_execution_group_contract,
    )
    return _Scenario(
        anchor=anchor,
        target=target,
        snapshot=snapshot,
        artifact=artifact,
    )


def _instrument(
    *,
    raw_symbol: str = VARIABLE_OCC,
    multiplier: Decimal = Decimal("100"),
) -> InstrumentIdentity:
    return InstrumentIdentity(
        broker="moomoo",
        account_key=ACCOUNT_KEY,
        raw_symbol=raw_symbol,
        asset_type="option",
        underlying="AAPL",
        currency="USD",
        expiry=date(2026, 8, 21),
        strike=Decimal("200"),
        option_right="C",
        contract_multiplier=multiplier,
        contract_multiplier_basis="evidence_derived_from_amount",
    )


def _fill(
    scenario: _Scenario,
    *,
    observation_id: int,
    filled_at: datetime,
    raw_symbol: str = VARIABLE_OCC,
    multiplier: Decimal = Decimal("100"),
    import_batch_id: int | None = None,
    linked_order_identity: tuple[str, str, str] | None = None,
    execution_group_identity: tuple[str, str, str] | None = None,
    execution_group_member_id: int | None = None,
) -> CanonicalBoundaryFill:
    return CanonicalBoundaryFill(
        member_id=observation_id + 1000,
        selected_observation_id=observation_id,
        import_batch_id=(
            scenario.target.batch_id
            if import_batch_id is None
            else import_batch_id
        ),
        identity=("moomoo", ACCOUNT_KEY, f"deal-{observation_id}"),
        linked_order_identity=linked_order_identity,
        execution_group_identity=execution_group_identity,
        execution_group_member_id=execution_group_member_id,
        instrument=_instrument(raw_symbol=raw_symbol, multiplier=multiplier),
        filled_at=filled_at,
    )


def _order(
    scenario: _Scenario,
    *,
    observation_id: int,
    ordered_at: datetime,
) -> CanonicalBoundaryOrder:
    return CanonicalBoundaryOrder(
        member_id=observation_id + 1000,
        selected_observation_id=observation_id,
        import_batch_id=scenario.target.batch_id,
        identity=("moomoo", ACCOUNT_KEY, f"order-{observation_id}"),
        instrument=_instrument(),
        ordered_at=ordered_at,
        evidence_level="aggregate_only",
        filled_quantity=Decimal("1"),
        economic_sha256=_sha256(
            {"kind": "economic-order", "observation_id": observation_id}
        ),
    )


def _patch_projections(
    monkeypatch: pytest.MonkeyPatch,
    scenario: _Scenario,
    *,
    orders: tuple[CanonicalBoundaryOrder, ...] = (),
    fills: tuple[CanonicalBoundaryFill, ...] = (),
    source_batch_ids: tuple[int, ...] | None = None,
) -> None:
    anchor_projection = VerifiedCanonicalEvidenceProjection(
        canonical_set_id=scenario.anchor.canonical_set_id,
        canonical_set_sha256=scenario.anchor.canonical_set_sha256,
        source_cutoff_at=scenario.anchor.watermark,
        source_batch_ids=(scenario.anchor.batch_id,),
        orders=(),
        fills=(),
    )
    target_projection = VerifiedCanonicalEvidenceProjection(
        canonical_set_id=scenario.target.canonical_set_id,
        canonical_set_sha256=scenario.target.canonical_set_sha256,
        source_cutoff_at=scenario.target.watermark,
        source_batch_ids=(
            source_batch_ids
            if source_batch_ids is not None
            else (scenario.anchor.batch_id, scenario.target.batch_id)
        ),
        orders=orders,
        fills=fills,
    )
    projections = {
        anchor_projection.canonical_set_id: anchor_projection,
        target_projection.canonical_set_id: target_projection,
    }

    def load_projection(_session, account_key: str, canonical_set_id: int):
        assert account_key == ACCOUNT_KEY
        return projections[canonical_set_id]

    monkeypatch.setattr(
        continuity,
        "load_verified_canonical_evidence_projection",
        load_projection,
    )


def _business_table_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in names
        }
    finally:
        connection.close()


def _tamper(path: Path, *, table: str, sql: str, parameters: tuple[Any, ...]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_update_immutable"
        )
        connection.execute(sql, parameters)
        connection.commit()
    finally:
        connection.close()


def test_no_snapshot_is_zero_business_write(isolated_sqlite: Path):
    _init_real_schema()
    before = _business_table_counts(isolated_sqlite)

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert result.status == "no_snapshot"
    assert result.ready_for_episode_build is False
    assert [reason.code for reason in result.reasons] == ["no_confirmed_snapshot"]
    assert _business_table_counts(isolated_sqlite) == before


def test_continuity_connection_denies_ddl_and_dml(isolated_sqlite: Path):
    _init_real_schema()
    connection = continuity._open_readonly_database(isolated_sqlite)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("CREATE TABLE forbidden_write (id INTEGER)")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "INSERT INTO journal_v2_position_snapshots (id) VALUES (999)"
            )
    finally:
        connection.close()


def test_in_session_continuity_rejects_mutable_database_session():
    _init_real_schema()
    db = get_db()

    with db.session_scope() as session:
        connection = session.connection()
        assert connection.in_transaction()
        assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 0
        with Session(bind=connection) as bound_session:
            with pytest.raises(
                PositionSnapshotContinuityError,
                match="query_only",
            ):
                assess_position_snapshot_continuity_in_session(
                    connection,
                    bound_session,
                    ACCOUNT_KEY,
                )


def test_readonly_session_pins_one_wal_snapshot(isolated_sqlite: Path):
    writer = sqlite3.connect(isolated_sqlite)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE continuity_read_probe (value INTEGER)")
        writer.execute("INSERT INTO continuity_read_probe VALUES (1)")
        writer.commit()
    finally:
        writer.close()

    with continuity._open_readonly_session(isolated_sqlite) as (connection, _session):
        assert connection.in_transaction()
        assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 1
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM continuity_read_probe"
        ).scalar_one() == 1

        writer = sqlite3.connect(isolated_sqlite)
        try:
            writer.execute("INSERT INTO continuity_read_probe VALUES (2)")
            writer.commit()
        finally:
            writer.close()

        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM continuity_read_probe"
        ).scalar_one() == 1

    verifier = sqlite3.connect(isolated_sqlite)
    try:
        assert verifier.execute(
            "SELECT COUNT(*) FROM continuity_read_probe"
        ).fetchone()[0] == 2
    finally:
        verifier.close()


def test_real_confirmed_snapshot_waits_for_later_publication():
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=2)
    payload = _snapshot_payload(base=base)
    anchor = _install_publication(
        binding=_binding(payload),
        watermark=base - timedelta(seconds=1),
        suffix="waiting-anchor",
    )
    artifact, snapshot = _confirm_snapshot(base=base)

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert artifact.refresh_publication_id == anchor.publication_id
    assert result.status == "awaiting_refresh"
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.boundary_at == snapshot.operation_completed_at
    assert [reason.code for reason in result.reasons] == ["no_later_publication"]


def test_real_verified_canonical_path_is_ready_stable_and_zero_write(
    isolated_sqlite: Path,
):
    scenario = _ready_scenario()
    before = _business_table_counts(isolated_sqlite)

    first = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    second = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert first.status == "ready"
    assert first.ready_for_episode_build is True
    assert first.snapshot_id == scenario.snapshot.snapshot_id
    assert first.target_canonical_set_id == scenario.target.canonical_set_id
    assert first.target_canonical_set_sha256 == scenario.target.canonical_set_sha256
    assert first.fence_key == second.fence_key
    assert first.fence_key is not None and len(first.fence_key) == 64
    assert first.policy_version == "position-snapshot-continuity-fence/1.1"
    assert first.counts.snapshot_member_count == 1
    assert _business_table_counts(isolated_sqlite) == before


def test_legacy_group_free_canonical_root_is_accepted_by_strict_continuity(
    isolated_sqlite: Path,
):
    scenario = _ready_scenario(
        canonical_reader_name="stable_broker_identity_reader",
        canonical_reader_version="1.0.0",
        include_execution_group_contract=False,
    )
    table_counts = _business_table_counts(isolated_sqlite)

    assert table_counts["journal_v2_canonical_execution_group_members"] == 0
    assert table_counts["journal_v2_canonical_execution_group_legs"] == 0

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert result.status == "ready"
    assert result.ready_for_episode_build is True
    assert result.target_canonical_set_id == scenario.target.canonical_set_id


def test_current_reader_missing_execution_group_contract_fails_closed():
    _ready_scenario(
        canonical_reader_name="stable_broker_identity_reader",
        canonical_reader_version="1.1.0",
        include_execution_group_contract=False,
    )

    with pytest.raises(
        PositionSnapshotContinuityError,
        match="canonical root execution-group collection is missing",
    ):
        assess_latest_position_snapshot_continuity(ACCOUNT_KEY)


def test_canonical_cutoff_change_rotates_the_stable_fence(
    isolated_sqlite: Path,
):
    scenario = _ready_scenario()
    first = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert first.status == "ready"
    assert first.fence_key is not None
    new_cutoff = scenario.target.watermark + timedelta(minutes=1)
    recomputed_set_key = _sha256(
        {
            "account_key": ACCOUNT_KEY,
            "canonical_set_sha256": scenario.target.canonical_set_sha256,
            "reader_name": "continuity_test",
            "reader_version": "1",
            "source_cutoff_at": new_cutoff.isoformat(),
        }
    )
    _tamper(
        isolated_sqlite,
        table="journal_v2_canonical_evidence_sets",
        sql=(
            "UPDATE journal_v2_canonical_evidence_sets "
            "SET source_cutoff_at=?, set_key=? WHERE id=?"
        ),
        parameters=(
            new_cutoff.isoformat(sep=" "),
            recomputed_set_key,
            scenario.target.canonical_set_id,
        ),
    )
    _tamper(
        isolated_sqlite,
        table="journal_v2_refresh_publications",
        sql=(
            "UPDATE journal_v2_refresh_publications "
            "SET evidence_published_through=? WHERE id=?"
        ),
        parameters=(
            new_cutoff.isoformat(sep=" "),
            scenario.target.publication_id,
        ),
    )

    second = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert second.status == "ready"
    assert second.target_canonical_set_sha256 == (
        first.target_canonical_set_sha256
    )
    assert second.counts == first.counts
    assert second.fence_key != first.fence_key


def test_canonical_root_tamper_fails_closed(isolated_sqlite: Path):
    scenario = _ready_scenario()
    _tamper(
        isolated_sqlite,
        table="journal_v2_canonical_evidence_sets",
        sql=(
            "UPDATE journal_v2_canonical_evidence_sets "
            "SET canonical_payload_json=? WHERE id=?"
        ),
        parameters=(
            _canonical_json(
                {
                    "analysis_ready": True,
                    "execution_groups": [],
                    "fills": [],
                    "issues": [{"tampered": True}],
                    "orders": [],
                    "provenance": {},
                }
            ),
            scenario.target.canonical_set_id,
        ),
    )

    with pytest.raises(
        PositionSnapshotContinuityError,
        match="canonical root payload hash verification failed",
    ):
        assess_latest_position_snapshot_continuity(ACCOUNT_KEY)


def test_canonical_set_key_tamper_fails_closed(isolated_sqlite: Path):
    scenario = _ready_scenario()
    _tamper(
        isolated_sqlite,
        table="journal_v2_canonical_evidence_sets",
        sql=(
            "UPDATE journal_v2_canonical_evidence_sets SET set_key=? "
            "WHERE id=?"
        ),
        parameters=("0" * 64, scenario.target.canonical_set_id),
    )

    with pytest.raises(
        PositionSnapshotContinuityError,
        match="set key verification failed",
    ):
        assess_latest_position_snapshot_continuity(ACCOUNT_KEY)


def test_canonical_stored_provenance_tamper_fails_closed(
    isolated_sqlite: Path,
):
    scenario = _ready_scenario()
    _tamper(
        isolated_sqlite,
        table="journal_v2_canonical_evidence_sets",
        sql=(
            "UPDATE journal_v2_canonical_evidence_sets "
            "SET provenance_json='{}' WHERE id=?"
        ),
        parameters=(scenario.target.canonical_set_id,),
    )

    with pytest.raises(
        PositionSnapshotContinuityError,
        match="root provenance",
    ):
        assess_latest_position_snapshot_continuity(ACCOUNT_KEY)


def test_refresh_artifact_payload_tamper_fails_closed(isolated_sqlite: Path):
    scenario = _ready_scenario()
    _tamper(
        isolated_sqlite,
        table="journal_v2_refresh_artifacts",
        sql=(
            "UPDATE journal_v2_refresh_artifacts SET payload_json='{}' "
            "WHERE id=(SELECT refresh_artifact_id "
            "FROM journal_v2_refresh_publications WHERE id=?)"
        ),
        parameters=(scenario.target.publication_id,),
    )

    with pytest.raises(
        PositionSnapshotContinuityError,
        match="refresh artifact payload failed strict replay",
    ):
        assess_latest_position_snapshot_continuity(ACCOUNT_KEY)


@pytest.mark.parametrize(
    ("table", "sql", "record_id"),
    [
        (
            "journal_v2_position_snapshots",
            "UPDATE journal_v2_position_snapshots SET provenance_json='{}' WHERE id=?",
            "snapshot",
        ),
        (
            "journal_v2_position_snapshot_artifacts",
            "UPDATE journal_v2_position_snapshot_artifacts SET payload_json='{}' WHERE id=?",
            "artifact",
        ),
    ],
    ids=("snapshot-provenance", "artifact-payload"),
)
def test_snapshot_provenance_and_artifact_tamper_fail_closed(
    isolated_sqlite: Path,
    table: str,
    sql: str,
    record_id: str,
):
    scenario = _ready_scenario()
    target_id = (
        scenario.snapshot.snapshot_id
        if record_id == "snapshot"
        else scenario.artifact.artifact_id
    )
    _tamper(
        isolated_sqlite,
        table=table,
        sql=sql,
        parameters=(target_id,),
    )

    with pytest.raises(PositionSnapshotContinuityError):
        assess_latest_position_snapshot_continuity(ACCOUNT_KEY)


def test_latest_snapshot_selection_uses_query_completed_at_not_insert_order():
    reference = datetime.now(timezone.utc).replace(microsecond=0)
    first_base = reference - timedelta(seconds=2)
    first_payload = _snapshot_payload(base=first_base)
    binding = _binding(first_payload)
    anchor = _install_publication(
        binding=binding,
        watermark=reference - timedelta(seconds=4),
        suffix="selection-anchor",
    )
    _first_artifact, first_snapshot = _confirm_snapshot(base=first_base)
    target = _install_publication(
        binding=binding,
        watermark=first_snapshot.operation_completed_at + timedelta(seconds=1),
        suffix="selection-target",
        include_batch_ids=(anchor.batch_id,),
        window_start=anchor.watermark,
    )
    _second_artifact, second_snapshot = _confirm_snapshot(
        base=reference - timedelta(seconds=10)
    )

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert second_snapshot.snapshot_id > first_snapshot.snapshot_id
    assert second_snapshot.query_completed_at < first_snapshot.query_completed_at
    assert second_snapshot.refresh_publication_id == target.publication_id
    assert result.status == "ready"
    assert result.snapshot_id == first_snapshot.snapshot_id


def test_variable_and_padded_occ_symbols_share_semantic_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = _ready_scenario()
    fill = _fill(
        scenario,
        observation_id=401,
        filled_at=scenario.snapshot.operation_completed_at + timedelta(milliseconds=1),
        raw_symbol=VARIABLE_OCC,
        multiplier=Decimal("100"),
    )
    _patch_projections(monkeypatch, scenario, fills=(fill,))

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert scenario.snapshot.members[0].symbol == PADDED_OCC
    assert fill.instrument.raw_symbol == VARIABLE_OCC
    assert result.status == "ready"
    assert result.counts.option_identity_mismatch_count == 0


def test_same_semantic_option_with_real_multiplier_mismatch_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = _ready_scenario()
    fill = _fill(
        scenario,
        observation_id=402,
        filled_at=scenario.snapshot.operation_completed_at + timedelta(milliseconds=1),
        raw_symbol=VARIABLE_OCC,
        multiplier=Decimal("50"),
    )
    _patch_projections(monkeypatch, scenario, fills=(fill,))

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert result.status == "blocked"
    assert result.counts.option_identity_mismatch_count == 1
    assert "option_identity_mismatch" in {reason.code for reason in result.reasons}


def test_guard_fill_and_pre_boundary_aggregate_are_both_reported(
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = _ready_scenario()
    fill = _fill(
        scenario,
        observation_id=410,
        filled_at=scenario.snapshot.query_completed_at,
    )
    order = _order(
        scenario,
        observation_id=411,
        ordered_at=scenario.snapshot.query_started_at - timedelta(milliseconds=1),
    )
    _patch_projections(monkeypatch, scenario, orders=(order,), fills=(fill,))

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    codes = {reason.code for reason in result.reasons}
    assert result.status == "blocked"
    assert result.counts.guard_fill_count == 1
    assert result.counts.aggregate_changed_pre_boundary_count == 1
    assert {"guard_fill_detected", "aggregate_changed_pre_boundary"} <= codes


def test_order_fill_set_straddling_boundary_is_not_split(
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = _ready_scenario()
    linked_order = ("moomoo", ACCOUNT_KEY, "straddling-order")
    fills = (
        _fill(
            scenario,
            observation_id=420,
            filled_at=scenario.snapshot.query_started_at - timedelta(milliseconds=1),
            linked_order_identity=linked_order,
        ),
        _fill(
            scenario,
            observation_id=421,
            filled_at=scenario.snapshot.operation_completed_at
            + timedelta(milliseconds=1),
            linked_order_identity=linked_order,
        ),
    )
    _patch_projections(monkeypatch, scenario, fills=fills)

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert result.status == "blocked"
    assert result.counts.straddling_order_count == 1
    assert "straddling_order" in {reason.code for reason in result.reasons}


def test_combo_group_straddling_boundary_is_not_split(
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = _ready_scenario()
    group_identity = ("moomoo", ACCOUNT_KEY, "combo-1")
    fills = (
        _fill(
            scenario,
            observation_id=430,
            filled_at=scenario.snapshot.query_started_at - timedelta(milliseconds=1),
            execution_group_identity=group_identity,
            execution_group_member_id=900,
        ),
        _fill(
            scenario,
            observation_id=431,
            filled_at=scenario.snapshot.operation_completed_at
            + timedelta(milliseconds=1),
            execution_group_identity=group_identity,
            execution_group_member_id=900,
        ),
    )
    _patch_projections(monkeypatch, scenario, fills=fills)

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert result.status == "blocked"
    assert result.counts.straddling_order_count == 0
    assert result.counts.straddling_group_count == 1
    assert "straddling_group" in {reason.code for reason in result.reasons}


def test_unbacked_post_boundary_evidence_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = _ready_scenario()
    fill = _fill(
        scenario,
        observation_id=440,
        filled_at=scenario.snapshot.operation_completed_at + timedelta(milliseconds=1),
        import_batch_id=999,
    )
    _patch_projections(
        monkeypatch,
        scenario,
        fills=(fill,),
        source_batch_ids=(scenario.anchor.batch_id, scenario.target.batch_id, 999),
    )

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert result.status == "blocked"
    assert result.counts.unbacked_post_boundary_count == 1
    assert "unbacked_post_boundary" in {reason.code for reason in result.reasons}


def test_refresh_window_must_cover_the_acquisition_guard():
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=2)
    payload = _snapshot_payload(base=base)
    binding = _binding(payload)
    anchor = _install_publication(
        binding=binding,
        watermark=base - timedelta(seconds=1),
        suffix="gap-anchor",
    )
    artifact, snapshot = _confirm_snapshot(base=base)
    _install_publication(
        binding=binding,
        watermark=snapshot.operation_completed_at + timedelta(seconds=1),
        suffix="gap-target",
        include_batch_ids=(anchor.batch_id,),
        window_start=snapshot.query_started_at + timedelta(milliseconds=1),
    )

    result = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)

    assert artifact.refresh_publication_id == anchor.publication_id
    assert result.status == "blocked"
    assert "publication_chain_gap" in {reason.code for reason in result.reasons}


def test_fenced_future_preview_is_stable_zero_write_and_left_censored(
    isolated_sqlite: Path,
):
    _ready_scenario()
    readiness = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert readiness.fence_key is not None
    before = _business_table_counts(isolated_sqlite)

    first = preview_fenced_position_episodes(
        ACCOUNT_KEY,
        readiness.fence_key,
    )
    second = preview_fenced_position_episodes(
        ACCOUNT_KEY,
        readiness.fence_key,
    )

    assert first.planned_build_key == second.planned_build_key
    assert first.evidence_set_sha256 == second.evidence_set_sha256
    assert first.opening_boundary_policy == "complete_snapshot"
    assert first.counts.opening_position_count == 1
    assert first.counts.opening_contract_count == Decimal("2.0000000000")
    assert first.counts.source_event_count == 0
    assert first.counts.left_censored_episode_count == 1
    assert first.episodes[0].average_entry_price is None
    assert first.episodes[0].realized_pnl_net is None
    assert first.evidence_written is False
    assert first.trading_action_performed is False
    assert _business_table_counts(isolated_sqlite) == before


def test_fenced_future_preview_rejects_stale_fence_without_writes(
    isolated_sqlite: Path,
):
    _ready_scenario()
    before = _business_table_counts(isolated_sqlite)

    with pytest.raises(
        FuturePositionEpisodePreviewError,
        match="fence changed",
    ):
        preview_fenced_position_episodes(ACCOUNT_KEY, "0" * 64)

    assert _business_table_counts(isolated_sqlite) == before


def test_fenced_future_preview_normalizes_occ_and_keeps_parent_order(
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = _ready_scenario()
    boundary = scenario.snapshot.operation_completed_at
    instrument = _instrument(raw_symbol=VARIABLE_OCC)
    order_identity = ("moomoo", ACCOUNT_KEY, "future-order")
    boundary_order = CanonicalBoundaryOrder(
        member_id=1501,
        selected_observation_id=501,
        import_batch_id=scenario.target.batch_id,
        identity=order_identity,
        instrument=instrument,
        ordered_at=boundary - timedelta(seconds=1),
        evidence_level="fill_detail",
        filled_quantity=Decimal("2"),
        economic_sha256=_sha256("future-order-economic"),
    )
    boundary_fill = CanonicalBoundaryFill(
        member_id=1601,
        selected_observation_id=601,
        import_batch_id=scenario.target.batch_id,
        identity=("moomoo", ACCOUNT_KEY, "future-deal"),
        linked_order_identity=order_identity,
        execution_group_identity=None,
        execution_group_member_id=None,
        instrument=instrument,
        filled_at=boundary + timedelta(milliseconds=1),
    )
    builder_order = CanonicalOrderEvidence(
        observation_id=501,
        observation_key="canonical-order-future",
        instrument=instrument,
        side="SELL",
        status="FILLED_ALL",
        ordered_at=boundary - timedelta(seconds=1),
        source_sequence=1,
        evidence_level="fill_detail",
        filled_quantity=Decimal("2"),
        average_fill_price=Decimal("2"),
        amount=None,
        total_fee=Decimal("1"),
        fee_evidence_status="complete",
    )
    builder_fill = CanonicalFillEvidence(
        observation_id=601,
        observation_key="canonical-fill-future",
        instrument=instrument,
        side="SELL",
        filled_at=boundary + timedelta(milliseconds=1),
        source_sequence=2,
        quantity=Decimal("2"),
        price=Decimal("2"),
        amount=Decimal("400"),
        total_fee=None,
        broker_order_observation_id=501,
    )
    full_projection = VerifiedCanonicalEpisodeEvidenceProjection(
        canonical_set_key=_sha256("future-canonical-set-key"),
        canonical_member_count=2,
        boundary=VerifiedCanonicalEvidenceProjection(
            canonical_set_id=scenario.target.canonical_set_id,
            canonical_set_sha256=scenario.target.canonical_set_sha256,
            source_cutoff_at=scenario.target.watermark,
            source_batch_ids=(
                scenario.anchor.batch_id,
                scenario.target.batch_id,
            ),
            orders=(boundary_order,),
            fills=(boundary_fill,),
        ),
        orders=(builder_order,),
        fills=(builder_fill,),
        execution_groups=(),
        max_multiplier_proof_residual=Decimal("0"),
    )

    original_boundary_loader = (
        continuity.load_verified_canonical_evidence_projection
    )

    def load_boundary(session, account_key: str, canonical_set_id: int):
        if canonical_set_id == scenario.target.canonical_set_id:
            return full_projection.boundary
        return original_boundary_loader(session, account_key, canonical_set_id)

    monkeypatch.setattr(
        continuity,
        "load_verified_canonical_evidence_projection",
        load_boundary,
    )
    monkeypatch.setattr(
        continuity,
        "load_verified_canonical_episode_evidence_projection",
        lambda *_args, **_kwargs: full_projection,
    )
    readiness = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert readiness.fence_key is not None

    preview = preview_fenced_position_episodes(
        ACCOUNT_KEY,
        readiness.fence_key,
    )

    assert preview.counts.supporting_order_count == 1
    assert preview.counts.post_boundary_fill_event_count == 1
    assert preview.counts.planned_closed_episode_count == 1
    assert preview.counts.left_censored_episode_count == 1
    assert preview.counts.headline_episode_count == 0
    assert preview.episodes[0].instrument.raw_symbol == (
        "AAPL260821C00200000"
    )
    assert preview.episodes[0].average_entry_price is None
    assert preview.episodes[0].realized_pnl_net is None

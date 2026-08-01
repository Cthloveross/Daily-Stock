from __future__ import annotations

import hashlib
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from src.journal.ledger import position_snapshot_continuity as continuity
from src.journal.ledger.activation_repository import (
    EpisodeBuildActivationError,
    activate_episode_build,
    get_episode_build_activation_state,
)
from src.journal.ledger.episode_repository import (
    CanonicalBoundaryFill,
    CanonicalBoundaryOrder,
    EpisodeRepositoryError,
    SNAPSHOT_FENCE_SOURCE_KIND,
    VerifiedCanonicalEpisodeEvidenceProjection,
    VerifiedCanonicalEvidenceProjection,
    VerifiedCanonicalExecutionGroup,
    append_latest_position_episode_build,
    get_episode_summary,
    get_latest_episode_summary,
    get_latest_position_episode_page,
)
from src.journal.ledger.episodes import (
    CanonicalFillEvidence,
    CanonicalOrderEvidence,
)
from src.journal.ledger.models import (
    BrokerFillObservation,
    BrokerOrderObservation,
    EpisodeBuild,
    EpisodeBuildActivation,
    EpisodeBuildCanonicalSource,
    ImportBatch,
)
from src.journal.ledger.refresh_repository import get_journal_refresh_status
from src.journal.ledger.position_snapshot_continuity import (
    assess_latest_position_snapshot_continuity,
)
from src.journal.ledger.position_snapshot_episode_build import (
    append_future_position_episode_build,
)
from src.journal.ledger.position_snapshot_episode_preview import (
    FuturePositionEpisodePreviewError,
    preview_fenced_position_episodes,
)
from src.journal.tests.test_position_snapshot_continuity import (
    ACCOUNT_KEY,
    _business_table_counts,
    _install_publication,
    _instrument,
    _ready_scenario,
    _sha256,
    _tamper,
)
from src.storage import get_db


_LINK_TABLE = "journal_v2_episode_build_snapshot_fence_sources"


def _business_table_digest(path: Path) -> str:
    """Order-stable content digest across every business table."""
    connection = sqlite3.connect(path)
    try:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        digest = hashlib.sha256()
        for name in names:
            digest.update(name.encode("utf-8"))
            for row in connection.execute(
                f'SELECT * FROM "{name}" ORDER BY 1'
            ):
                digest.update(repr(row).encode("utf-8"))
        return digest.hexdigest()
    finally:
        connection.close()


def _confirm(
    preview,
    *,
    accept_left_censored_openings: bool = True,
    accept_group_fee_scope: bool = False,
    expected_build_key: str | None = None,
    expected_evidence_set_sha256: str | None = None,
    expected_fence_key: str | None = None,
):
    return append_future_position_episode_build(
        ACCOUNT_KEY,
        expected_fence_key=expected_fence_key or preview.fence_key,
        expected_build_key=expected_build_key or preview.planned_build_key,
        expected_evidence_set_sha256=(
            expected_evidence_set_sha256 or preview.evidence_set_sha256
        ),
        accept_left_censored_openings=accept_left_censored_openings,
        accept_group_fee_scope=accept_group_fee_scope,
    )


def _ready_preview():
    scenario = _ready_scenario()
    readiness = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert readiness.fence_key is not None
    preview = preview_fenced_position_episodes(
        ACCOUNT_KEY,
        readiness.fence_key,
    )
    return scenario, preview


def _install_observation_rows(
    scenario,
    *,
    filled_at,
) -> tuple[int, int]:
    """Persist one real detail order/fill pair so evidence FKs resolve.

    The rows live in a standalone accepted batch that no refresh publication
    references: publication replay verification recounts the stored rows of
    each published batch, while episode evidence foreign keys only need the
    observation rows to exist.
    """
    instrument = _instrument()
    db = get_db()
    with db.session_scope() as session:
        holder = ImportBatch(
            batch_key=_sha256({"kind": "future-evidence-holder"}),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_kind="openapi",
            source_schema="dsa.moomoo.readonly-export.v1",
            source_sha256=_sha256("future-evidence-holder-source"),
            parser_name="future_evidence_holder",
            parser_version="1",
            window_start=filled_at - timedelta(hours=1),
            window_end=filled_at + timedelta(hours=1),
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
            recorded_at=filled_at,
        )
        session.add(holder)
        session.flush()
        order = BrokerOrderObservation(
            import_batch_id=int(holder.id),
            observation_key=_sha256({"kind": "future-order-obs"}),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_order_id="future-order",
            identity_strength="strong",
            source_row_number=1,
            raw_symbol=instrument.raw_symbol,
            asset_type="option",
            underlying=instrument.underlying,
            expiry=instrument.expiry,
            strike=instrument.strike,
            option_right=instrument.option_right,
            contract_multiplier=instrument.contract_multiplier,
            side="SELL",
            status="FILLED_ALL",
            currency="USD",
            ordered_at=filled_at - timedelta(seconds=1),
            order_quantity=Decimal("2"),
            order_price=Decimal("2"),
            summary_filled_quantity=Decimal("2"),
            summary_average_fill_price=Decimal("2"),
            total_fee=Decimal("1"),
            evidence_level="fill_detail",
            fee_evidence_status="complete",
            evidence_json="{}",
            completeness_score=Decimal("1"),
            completeness_json="{}",
            provenance_json="{}",
            source_record_sha256=_sha256("future-order-record"),
        )
        session.add(order)
        session.flush()
        fill = BrokerFillObservation(
            import_batch_id=int(holder.id),
            broker_order_observation_id=int(order.id),
            observation_key=_sha256({"kind": "future-fill-obs"}),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_order_id="future-order",
            source_deal_id="future-deal",
            identity_strength="strong",
            source_row_number=2,
            raw_symbol=instrument.raw_symbol,
            asset_type="option",
            underlying=instrument.underlying,
            expiry=instrument.expiry,
            strike=instrument.strike,
            option_right=instrument.option_right,
            contract_multiplier=instrument.contract_multiplier,
            side="SELL",
            filled_at=filled_at,
            quantity=Decimal("2"),
            price=Decimal("2"),
            amount=Decimal("400"),
            currency="USD",
            evidence_level="fill_detail",
            evidence_json="{}",
            completeness_score=Decimal("1"),
            completeness_json="{}",
            provenance_json="{}",
            source_record_sha256=_sha256("future-fill-record"),
        )
        session.add(fill)
        session.flush()
        return int(order.id), int(fill.id)


def _patch_full_projection(
    monkeypatch: pytest.MonkeyPatch,
    scenario,
    *,
    order_id: int,
    fill_id: int,
    filled_at,
    execution_groups: tuple[VerifiedCanonicalExecutionGroup, ...] = (),
    group_identity: tuple[str, str, str] | None = None,
    group_member_id: int | None = None,
) -> None:
    """Serve one post-boundary detail order/fill from the frozen target set."""
    instrument = _instrument()
    order_identity = ("moomoo", ACCOUNT_KEY, "future-order")
    boundary_order = CanonicalBoundaryOrder(
        member_id=order_id + 1000,
        selected_observation_id=order_id,
        import_batch_id=scenario.target.batch_id,
        identity=order_identity,
        instrument=instrument,
        ordered_at=filled_at - timedelta(seconds=1),
        evidence_level="fill_detail",
        filled_quantity=Decimal("2"),
        economic_sha256=_sha256("future-order-economic"),
    )
    boundary_fill = CanonicalBoundaryFill(
        member_id=fill_id + 1000,
        selected_observation_id=fill_id,
        import_batch_id=scenario.target.batch_id,
        identity=("moomoo", ACCOUNT_KEY, "future-deal"),
        linked_order_identity=order_identity,
        execution_group_identity=group_identity,
        execution_group_member_id=group_member_id,
        instrument=instrument,
        filled_at=filled_at,
    )
    builder_order = CanonicalOrderEvidence(
        observation_id=order_id,
        observation_key="canonical-order-future",
        instrument=instrument,
        side="SELL",
        status="FILLED_ALL",
        ordered_at=filled_at - timedelta(seconds=1),
        source_sequence=1,
        evidence_level="fill_detail",
        filled_quantity=Decimal("2"),
        average_fill_price=Decimal("2"),
        amount=None,
        total_fee=Decimal("1"),
        fee_evidence_status="complete",
    )
    builder_fill = CanonicalFillEvidence(
        observation_id=fill_id,
        observation_key="canonical-fill-future",
        instrument=instrument,
        side="SELL",
        filled_at=filled_at,
        source_sequence=2,
        quantity=Decimal("2"),
        price=Decimal("2"),
        amount=Decimal("400"),
        total_fee=None,
        broker_order_observation_id=order_id,
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
        execution_groups=execution_groups,
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


def test_future_build_confirm_appends_build_and_fence_link(
    isolated_sqlite: Path,
):
    """T1: a ready fence confirm appends one build plus one fence link."""
    _scenario, preview = _ready_preview()
    before = _business_table_counts(isolated_sqlite)

    result = _confirm(preview)

    assert result.duplicate is False
    assert result.build_key == preview.planned_build_key
    assert result.status == "partial"
    assert result.opening_boundary_policy == "complete_snapshot"
    assert result.position_episode_count == (
        preview.counts.planned_position_episode_count
    )
    assert result.evidence_allocation_count == 0
    assert result.snapshot_id == preview.snapshot_id
    assert result.snapshot_key == preview.snapshot_key
    assert result.fence_key == preview.fence_key
    assert result.target_canonical_set_id == preview.target_canonical_set_id
    assert result.target_canonical_set_sha256 == (
        preview.target_canonical_set_sha256
    )
    assert result.activation_changed is False
    assert result.trading_action_performed is False

    after = _business_table_counts(isolated_sqlite)
    assert after["journal_v2_episode_builds"] == (
        before["journal_v2_episode_builds"] + 1
    )
    assert after[_LINK_TABLE] == before.get(_LINK_TABLE, 0) + 1
    assert after["journal_v2_position_episodes"] == (
        before["journal_v2_position_episodes"] + 1
    )
    assert after["journal_v2_position_episode_evidence"] == (
        before["journal_v2_position_episode_evidence"]
    )
    assert after["journal_v2_episode_build_canonical_sources"] == (
        before["journal_v2_episode_build_canonical_sources"]
    )
    assert after["journal_v2_episode_build_activations"] == (
        before["journal_v2_episode_build_activations"]
    )

    summary = get_episode_summary(result.build_id, ACCOUNT_KEY)
    assert summary is not None
    assert summary.build_key == result.build_key
    assert summary.source_kind == SNAPSHOT_FENCE_SOURCE_KIND
    assert summary.canonical_set_id == preview.target_canonical_set_id
    assert summary.canonical_set_sha256 == (
        preview.target_canonical_set_sha256
    )
    assert summary.left_censored_episode_count == 1
    assert summary.headline_episode_count == 0

    # The default view must not change: no activation exists and the future
    # build is never a CSV fallback.
    assert get_latest_episode_summary(ACCOUNT_KEY) is None
    state = get_episode_build_activation_state(ACCOUNT_KEY)
    assert state.selection_source == "none"


def test_future_build_confirm_replay_is_duplicate_with_zero_new_rows(
    isolated_sqlite: Path,
):
    """T2: an identical replay reports duplicate=True and writes nothing."""
    _scenario, preview = _ready_preview()
    first = _confirm(preview)
    counts_after_first = _business_table_counts(isolated_sqlite)
    digest_after_first = _business_table_digest(isolated_sqlite)

    second = _confirm(preview)

    assert second.duplicate is True
    assert second.build_id == first.build_id
    assert second.build_key == first.build_key
    assert second.link_key == first.link_key
    assert _business_table_counts(isolated_sqlite) == counts_after_first
    assert _business_table_digest(isolated_sqlite) == digest_after_first


def test_future_build_confirm_persists_post_boundary_fill_evidence(
    isolated_sqlite: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A detail-backed post-boundary fill is appended with its allocations."""
    scenario = _ready_scenario()
    filled_at = scenario.snapshot.operation_completed_at + timedelta(
        milliseconds=1
    )
    order_id, fill_id = _install_observation_rows(
        scenario,
        filled_at=filled_at,
    )
    _patch_full_projection(
        monkeypatch,
        scenario,
        order_id=order_id,
        fill_id=fill_id,
        filled_at=filled_at,
    )
    readiness = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert readiness.fence_key is not None
    preview = preview_fenced_position_episodes(
        ACCOUNT_KEY,
        readiness.fence_key,
    )
    assert preview.counts.post_boundary_fill_event_count == 1
    assert preview.counts.planned_closed_episode_count == 1
    before = _business_table_counts(isolated_sqlite)

    result = _confirm(preview)

    assert result.duplicate is False
    assert result.position_episode_count == (
        preview.counts.planned_position_episode_count
    )
    assert result.evidence_allocation_count > 0
    after = _business_table_counts(isolated_sqlite)
    assert after["journal_v2_position_episode_evidence"] == (
        before["journal_v2_position_episode_evidence"]
        + result.evidence_allocation_count
    )
    summary = get_episode_summary(result.build_id, ACCOUNT_KEY)
    assert summary is not None
    assert summary.source_kind == SNAPSHOT_FENCE_SOURCE_KIND
    assert summary.fee_conserved is True
    assert summary.source_known_fee_total == preview.source_known_fee_total
    assert summary.retained_execution_group_fee_total == (
        preview.retained_execution_group_fee_total
    )


def test_future_build_confirm_rejects_stale_fence_with_zero_writes(
    isolated_sqlite: Path,
):
    """T3: a newer publication rotates the fence and the confirm fails."""
    scenario, preview = _ready_preview()
    _install_publication(
        binding=scenario.snapshot.account_binding_id,
        watermark=scenario.target.watermark + timedelta(seconds=1),
        suffix="rotated",
        include_batch_ids=(
            scenario.anchor.batch_id,
            scenario.target.batch_id,
        ),
        window_start=scenario.target.watermark,
    )
    before_counts = _business_table_counts(isolated_sqlite)
    before_digest = _business_table_digest(isolated_sqlite)

    with pytest.raises(
        FuturePositionEpisodePreviewError,
        match="fence changed",
    ):
        _confirm(preview)

    assert _business_table_counts(isolated_sqlite) == before_counts
    assert _business_table_digest(isolated_sqlite) == before_digest


def test_future_build_confirm_rejects_changed_plan_hashes(
    isolated_sqlite: Path,
):
    """T4: build-key or evidence-hash mismatches fail closed with zero writes."""
    _scenario, preview = _ready_preview()
    before_counts = _business_table_counts(isolated_sqlite)
    before_digest = _business_table_digest(isolated_sqlite)

    with pytest.raises(EpisodeRepositoryError, match="plan changed"):
        _confirm(preview, expected_build_key="0" * 64)
    with pytest.raises(EpisodeRepositoryError, match="plan changed"):
        _confirm(preview, expected_evidence_set_sha256="0" * 64)
    with pytest.raises(EpisodeRepositoryError, match="lowercase"):
        _confirm(
            preview,
            expected_fence_key=preview.fence_key.upper(),
        )

    assert _business_table_counts(isolated_sqlite) == before_counts
    assert _business_table_digest(isolated_sqlite) == before_digest


def test_future_build_confirm_requires_left_censored_acceptance(
    isolated_sqlite: Path,
):
    """T5: snapshot-inherited openings demand explicit acceptance."""
    _scenario, preview = _ready_preview()
    assert preview.counts.left_censored_episode_count == 1
    before_counts = _business_table_counts(isolated_sqlite)
    before_digest = _business_table_digest(isolated_sqlite)

    with pytest.raises(
        EpisodeRepositoryError,
        match="explicit acceptance",
    ):
        _confirm(preview, accept_left_censored_openings=False)

    assert _business_table_counts(isolated_sqlite) == before_counts
    assert _business_table_digest(isolated_sqlite) == before_digest


def test_future_build_confirm_requires_group_fee_scope_acceptance(
    isolated_sqlite: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """T5: group-scope-only fees demand explicit acceptance before append."""
    scenario = _ready_scenario()
    filled_at = scenario.snapshot.operation_completed_at + timedelta(
        milliseconds=1
    )
    group_identity = ("moomoo", ACCOUNT_KEY, "future-group")
    _patch_full_projection(
        monkeypatch,
        scenario,
        order_id=501,
        fill_id=601,
        filled_at=filled_at,
        execution_groups=(
            VerifiedCanonicalExecutionGroup(
                identity=group_identity,
                member_id=901,
                import_batch_id=scenario.target.batch_id,
                currency="USD",
                total_fee=Decimal("0.5"),
                selected_fill_ids=(601,),
            ),
        ),
        group_identity=group_identity,
        group_member_id=901,
    )
    readiness = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert readiness.fence_key is not None
    preview = preview_fenced_position_episodes(
        ACCOUNT_KEY,
        readiness.fence_key,
    )
    assert preview.counts.group_fee_affected_episode_count > 0
    before_counts = _business_table_counts(isolated_sqlite)
    before_digest = _business_table_digest(isolated_sqlite)

    with pytest.raises(
        EpisodeRepositoryError,
        match="group scope",
    ):
        _confirm(preview, accept_group_fee_scope=False)

    assert _business_table_counts(isolated_sqlite) == before_counts
    assert _business_table_digest(isolated_sqlite) == before_digest


def test_fence_link_rows_are_append_only(isolated_sqlite: Path):
    """T6: UPDATE and DELETE on the fence-link table are denied."""
    _scenario, preview = _ready_preview()
    _confirm(preview)

    connection = sqlite3.connect(isolated_sqlite)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(
                f"UPDATE {_LINK_TABLE} SET fence_key='0' || substr(fence_key, 2)"
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(f"DELETE FROM {_LINK_TABLE}")
    finally:
        connection.close()


def test_future_build_duplicate_with_mismatched_fence_link_is_rejected(
    isolated_sqlite: Path,
):
    """T7: an equal build key with a foreign fence binding must not pass."""
    _scenario, preview = _ready_preview()
    first = _confirm(preview)
    _tamper(
        isolated_sqlite,
        table=_LINK_TABLE,
        sql=(
            f"UPDATE {_LINK_TABLE} SET fence_key=? WHERE episode_build_id=?"
        ),
        parameters=("f" * 64, first.build_id),
    )
    before_counts = _business_table_counts(isolated_sqlite)

    with pytest.raises(
        EpisodeRepositoryError,
        match="invalid snapshot-fence binding",
    ):
        _confirm(preview)

    assert _business_table_counts(isolated_sqlite) == before_counts


def test_future_build_missing_fence_link_on_duplicate_is_rejected(
    isolated_sqlite: Path,
):
    """T7/T8: a same-key build without any fence link fails closed."""
    _scenario, preview = _ready_preview()
    _confirm(preview)
    # Simulate a legacy/foreign row that shares the build key but was never
    # bound to a snapshot fence.
    connection = sqlite3.connect(isolated_sqlite)
    try:
        connection.execute(
            f"DROP TRIGGER IF EXISTS trg_{_LINK_TABLE}_delete_immutable"
        )
        connection.execute(f"DELETE FROM {_LINK_TABLE}")
        connection.commit()
    finally:
        connection.close()
    before_counts = _business_table_counts(isolated_sqlite)

    with pytest.raises(
        EpisodeRepositoryError,
        match="invalid snapshot-fence binding",
    ):
        _confirm(preview)

    assert _business_table_counts(isolated_sqlite) == before_counts


def _install_csv_baseline_batch(filled_at) -> None:
    """Persist one accepted CSV batch (detail order/fill) far before the guard.

    This is the legacy CSV fallback source; it must not disturb the
    publication chain or the continuity fence.
    """
    instrument = _instrument()
    db = get_db()
    with db.session_scope() as session:
        holder = ImportBatch(
            batch_key=_sha256({"kind": "csv-fallback-holder"}),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_kind="csv",
            source_schema="dsa.moomoo.history-csv.v1",
            source_sha256=_sha256("csv-fallback-holder-source"),
            parser_name="csv_fallback_holder",
            parser_version="1",
            window_start=filled_at - timedelta(hours=1),
            window_end=filled_at + timedelta(hours=1),
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
            recorded_at=filled_at,
        )
        session.add(holder)
        session.flush()
        order = BrokerOrderObservation(
            import_batch_id=int(holder.id),
            observation_key=_sha256({"kind": "csv-fallback-order-obs"}),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_order_id="csv-fallback-order",
            identity_strength="strong",
            source_row_number=1,
            raw_symbol=instrument.raw_symbol,
            asset_type="option",
            underlying=instrument.underlying,
            expiry=instrument.expiry,
            strike=instrument.strike,
            option_right=instrument.option_right,
            contract_multiplier=instrument.contract_multiplier,
            side="BUY",
            status="FILLED_ALL",
            currency="USD",
            ordered_at=filled_at - timedelta(seconds=1),
            order_quantity=Decimal("1"),
            order_price=Decimal("1"),
            summary_filled_quantity=Decimal("1"),
            summary_average_fill_price=Decimal("1"),
            total_fee=Decimal("1"),
            evidence_level="fill_detail",
            fee_evidence_status="complete",
            evidence_json="{}",
            completeness_score=Decimal("1"),
            completeness_json="{}",
            provenance_json="{}",
            source_record_sha256=_sha256("csv-fallback-order-record"),
        )
        session.add(order)
        session.flush()
        fill = BrokerFillObservation(
            import_batch_id=int(holder.id),
            broker_order_observation_id=int(order.id),
            observation_key=_sha256({"kind": "csv-fallback-fill-obs"}),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_order_id="csv-fallback-order",
            source_deal_id="csv-fallback-deal",
            identity_strength="strong",
            source_row_number=2,
            raw_symbol=instrument.raw_symbol,
            asset_type="option",
            underlying=instrument.underlying,
            expiry=instrument.expiry,
            strike=instrument.strike,
            option_right=instrument.option_right,
            contract_multiplier=instrument.contract_multiplier,
            side="BUY",
            filled_at=filled_at,
            quantity=Decimal("1"),
            price=Decimal("1"),
            amount=Decimal("100"),
            currency="USD",
            evidence_level="fill_detail",
            evidence_json="{}",
            completeness_score=Decimal("1"),
            completeness_json="{}",
            provenance_json="{}",
            source_record_sha256=_sha256("csv-fallback-fill-record"),
        )
        session.add(fill)


def test_default_view_stays_on_csv_fallback_when_fence_build_is_newer(
    isolated_sqlite: Path,
):
    """红线回归：fence build 比 CSV-backed build 更新时，默认读取仍是 CSV fallback。"""
    scenario = _ready_scenario()
    _install_csv_baseline_batch(
        scenario.snapshot.operation_completed_at - timedelta(days=30)
    )
    csv_result = append_latest_position_episode_build(
        ACCOUNT_KEY,
        accept_assumed_flat=True,
    )

    readiness = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert readiness.fence_key is not None
    preview = preview_fenced_position_episodes(ACCOUNT_KEY, readiness.fence_key)
    result = _confirm(preview)

    assert result.duplicate is False
    assert result.build_id != csv_result.build_id
    state = get_episode_build_activation_state(ACCOUNT_KEY)
    assert state.selection_source == "csv_fallback"
    assert state.current_build_id == csv_result.build_id
    summary = get_latest_episode_summary(ACCOUNT_KEY)
    assert summary is not None
    assert summary.build_id == csv_result.build_id


def _activation_row_count() -> int:
    db = get_db()
    with db.session_scope() as session:
        return int(
            session.execute(
                select(func.count(EpisodeBuildActivation.id))
            ).scalar_one()
        )


def test_fence_build_activation_switches_default_readers_and_is_idempotent(
    isolated_sqlite: Path,
):
    """切片 2 happy path：显式激活 fence build 后默认读取切换到它。"""
    scenario = _ready_scenario()
    _install_csv_baseline_batch(
        scenario.snapshot.operation_completed_at - timedelta(days=30)
    )
    csv_result = append_latest_position_episode_build(
        ACCOUNT_KEY,
        accept_assumed_flat=True,
    )
    readiness = assess_latest_position_snapshot_continuity(ACCOUNT_KEY)
    assert readiness.fence_key is not None
    preview = preview_fenced_position_episodes(ACCOUNT_KEY, readiness.fence_key)
    fence = _confirm(preview)
    initial = get_episode_build_activation_state(ACCOUNT_KEY)
    assert initial.selection_source == "csv_fallback"
    assert initial.current_build_id == csv_result.build_id

    first = activate_episode_build(
        fence.build_id,
        fence.build_key,
        account_key=ACCOUNT_KEY,
        accept_left_censored_openings=True,
        expected_current_activation_id=initial.current_activation_id,
        expected_current_build_id=initial.current_build_id,
    )
    retry = activate_episode_build(
        fence.build_id,
        fence.build_key,
        account_key=ACCOUNT_KEY,
        accept_left_censored_openings=True,
        expected_current_activation_id=initial.current_activation_id,
        expected_current_build_id=initial.current_build_id,
    )

    assert first.duplicate is False
    assert first.target_source_kind == SNAPSHOT_FENCE_SOURCE_KIND
    assert retry.duplicate is True
    assert retry.activation_id == first.activation_id
    assert first.state.selection_source == "activation"
    assert first.state.current_build_id == fence.build_id
    assert first.state.previous_build_id == csv_result.build_id
    # The persisted activation identity is the fence link's frozen TARGET
    # canonical set: the fence build's episodes derive exactly from it.
    assert first.state.canonical_set_id == preview.target_canonical_set_id
    assert first.state.canonical_set_sha256 == (
        preview.target_canonical_set_sha256
    )

    summary = get_latest_episode_summary(ACCOUNT_KEY)
    page = get_latest_position_episode_page(ACCOUNT_KEY)
    assert summary is not None
    assert summary.build_id == fence.build_id
    assert summary.source_kind == SNAPSHOT_FENCE_SOURCE_KIND
    assert page.build_id == fence.build_id
    assert _activation_row_count() == 1


def test_fence_build_activation_requires_left_censored_acceptance(
    isolated_sqlite: Path,
):
    """缺 left-censored acceptance 时激活被拒且零业务写。"""
    _scenario, preview = _ready_preview()
    fence = _confirm(preview)
    assert preview.counts.left_censored_episode_count > 0
    before_counts = _business_table_counts(isolated_sqlite)
    before_digest = _business_table_digest(isolated_sqlite)

    with pytest.raises(
        EpisodeBuildActivationError,
        match="explicit acceptance",
    ):
        activate_episode_build(
            fence.build_id,
            fence.build_key,
            account_key=ACCOUNT_KEY,
            expected_current_activation_id=None,
            expected_current_build_id=None,
        )

    assert _business_table_counts(isolated_sqlite) == before_counts
    assert _business_table_digest(isolated_sqlite) == before_digest
    assert get_episode_build_activation_state(
        ACCOUNT_KEY
    ).selection_source == "none"


def test_fence_build_activation_cas_rejects_stale_expectations(
    isolated_sqlite: Path,
):
    """陈旧 CAS 期望不能悄悄替换更新的 fence build 选择。"""
    _scenario, preview = _ready_preview()
    fence = _confirm(preview)
    first = activate_episode_build(
        fence.build_id,
        fence.build_key,
        account_key=ACCOUNT_KEY,
        accept_left_censored_openings=True,
        expected_current_activation_id=None,
        expected_current_build_id=None,
    )

    with pytest.raises(EpisodeBuildActivationError, match="state changed"):
        activate_episode_build(
            fence.build_id,
            fence.build_key,
            account_key=ACCOUNT_KEY,
            accept_left_censored_openings=True,
            expected_current_activation_id=None,
            expected_current_build_id=1_000_000,
        )

    assert _activation_row_count() == 1
    assert get_episode_build_activation_state(
        ACCOUNT_KEY
    ).current_activation_id == first.activation_id


def test_fence_link_tamper_fails_closed_on_activation_and_default_reads(
    isolated_sqlite: Path,
):
    """篡改 fence link 的目标事实集指纹时，激活与默认读取都 fail closed。"""
    scenario, preview = _ready_preview()
    fence = _confirm(preview)

    _tamper(
        isolated_sqlite,
        table=_LINK_TABLE,
        sql=(
            f"UPDATE {_LINK_TABLE} SET target_canonical_set_sha256=? "
            "WHERE episode_build_id=?"
        ),
        parameters=("0" * 64, fence.build_id),
    )
    with pytest.raises(
        EpisodeBuildActivationError,
        match="not activation-ready",
    ):
        activate_episode_build(
            fence.build_id,
            fence.build_key,
            account_key=ACCOUNT_KEY,
            accept_left_censored_openings=True,
            expected_current_activation_id=None,
            expected_current_build_id=None,
        )
    assert _activation_row_count() == 0

    _tamper(
        isolated_sqlite,
        table=_LINK_TABLE,
        sql=(
            f"UPDATE {_LINK_TABLE} SET target_canonical_set_sha256=? "
            "WHERE episode_build_id=?"
        ),
        parameters=(scenario.target.canonical_set_sha256, fence.build_id),
    )
    activated = activate_episode_build(
        fence.build_id,
        fence.build_key,
        account_key=ACCOUNT_KEY,
        accept_left_censored_openings=True,
        expected_current_activation_id=None,
        expected_current_build_id=None,
    )
    assert activated.state.current_build_id == fence.build_id

    _tamper(
        isolated_sqlite,
        table=_LINK_TABLE,
        sql=(
            f"UPDATE {_LINK_TABLE} SET target_canonical_set_sha256=? "
            "WHERE episode_build_id=?"
        ),
        parameters=("0" * 64, fence.build_id),
    )
    with pytest.raises(
        EpisodeBuildActivationError,
        match="not activation-ready",
    ):
        get_episode_build_activation_state(ACCOUNT_KEY)
    # Default reads wrap the same fail-closed refusal in the repository error.
    with pytest.raises(
        EpisodeRepositoryError,
        match="not activation-ready",
    ):
        get_latest_episode_summary(ACCOUNT_KEY)


def _append_synthetic_canonical_build_for_target(scenario, fence_build_id: int):
    """Clone the fence build into a canonical-linked build on the target set.

    Data-health compares the latest canonical-linked build against the latest
    canonical set; this synthetic row provides that comparison partner without
    replaying a full canonical seed.
    """
    db = get_db()
    with db.session_scope() as session:
        source = session.get(EpisodeBuild, fence_build_id)
        assert source is not None
        clone = EpisodeBuild(
            build_key="c" * 64,
            broker=source.broker,
            account_key=source.account_key,
            builder_name=source.builder_name,
            builder_version=source.builder_version,
            builder_config_sha256=source.builder_config_sha256,
            evidence_set_sha256=scenario.target.canonical_set_sha256,
            source_batch_ids_json=source.source_batch_ids_json,
            source_cutoff_at=source.source_cutoff_at,
            status="succeeded",
            strategy_episode_count=source.strategy_episode_count,
            position_episode_count=source.position_episode_count,
            unresolved_evidence_count=0,
            reconciliation_status=source.reconciliation_status,
            build_report_json=source.build_report_json,
            completeness_score=source.completeness_score,
            completeness_json=source.completeness_json,
            provenance_json=source.provenance_json,
        )
        session.add(clone)
        session.flush()
        session.add(
            EpisodeBuildCanonicalSource(
                link_key="d" * 64,
                episode_build_id=int(clone.id),
                canonical_set_id=scenario.target.canonical_set_id,
                canonical_set_sha256=scenario.target.canonical_set_sha256,
                projection_name="persisted_canonical_member_projection",
                projection_version="1.1.0",
                canonical_source_cutoff_at=source.source_cutoff_at,
                source_batch_ids_json=source.source_batch_ids_json,
            )
        )
        session.flush()
        return int(clone.id)


def test_data_health_reports_active_fence_build_honestly(
    isolated_sqlite: Path,
):
    """激活的 fence build 不破坏 data-health，且按目标事实集口径对齐。"""
    scenario, preview = _ready_preview()
    fence = _confirm(preview)

    before = get_journal_refresh_status(ACCOUNT_KEY)
    assert before.active_selection_source == "none"
    assert before.active_build_id is None

    activated = activate_episode_build(
        fence.build_id,
        fence.build_key,
        account_key=ACCOUNT_KEY,
        accept_left_censored_openings=True,
        expected_current_activation_id=None,
        expected_current_build_id=None,
    )
    assert activated.duplicate is False

    active = get_journal_refresh_status(ACCOUNT_KEY)
    assert active.active_selection_source == "activation"
    assert active.active_build_id == fence.build_id
    assert active.active_canonical_set_id == preview.target_canonical_set_id
    assert active.active_source_through == preview.source_cutoff_at
    # No full canonical build exists yet, so the honest pending stage is
    # still "build" for the latest canonical set.
    assert active.pending_stage == "build"

    canonical_build_id = _append_synthetic_canonical_build_for_target(
        scenario,
        fence.build_id,
    )
    aligned = get_journal_refresh_status(ACCOUNT_KEY)
    assert aligned.latest_canonical_build_id == canonical_build_id
    # The active fence build targets exactly the latest canonical set, so
    # data-health must not demand re-activation of the canonical build.
    assert aligned.freshness_state == "current"
    assert aligned.pending_stage == "none"

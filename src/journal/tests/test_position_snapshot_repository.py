from __future__ import annotations

import copy
import hashlib
import itertools
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import DatabaseError

import src.journal.ledger.position_snapshot_repository as repository
from src.journal.brokers.moomoo_position_snapshot import (
    PositionSnapshotConfig,
    run_position_snapshot_probe,
)
from src.journal.ledger.models import (
    CanonicalEvidenceSetRecord,
    EpisodeBuild,
    EpisodeBuildActivation,
    PositionEpisode,
    StrategyEpisode,
    ImportBatch,
)
from src.journal.ledger.position_snapshot_models import (
    ConfirmedPositionSnapshot,
    PositionSnapshotArtifact,
    PositionSnapshotMember,
)
from src.journal.ledger.position_snapshot_repository import (
    MAX_POSITION_SNAPSHOT_ACQUISITION_DURATION,
    MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS,
    MAX_POSITION_SNAPSHOT_FUTURE_SKEW,
    PositionSnapshotError,
    confirm_position_snapshot_artifact,
    get_latest_position_snapshot,
    get_latest_position_snapshot_state,
    get_position_snapshot_artifact,
    get_position_snapshot_detail,
    init_position_snapshot_schema,
    list_position_snapshots,
    plan_position_snapshot,
    save_position_snapshot_artifact,
)
from src.journal.ledger.refresh_models import (
    JournalRefreshArtifact,
    JournalRefreshPublication,
)
from src.storage import get_db


ACCOUNT_KEY = "primary"
ACCOUNT_SECRET = "position-ledger-test-secret-at-least-32-bytes"
OPTION_SYMBOL = "US.AAPL260821C00200000"
_PUBLICATION_COUNTER = itertools.count(1)


class _EnumValues:
    REAL = "REAL"
    US = "US"


SDK = SimpleNamespace(
    RET_OK=0,
    TrdEnv=_EnumValues,
    TrdMarket=_EnumValues,
)


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


def _position(
    symbol: str = OPTION_SYMBOL,
    *,
    side: str = "LONG",
    qty: object = "2.00",
    currency: str = "USD",
    average_cost: object = "1.2500",
) -> dict[str, Any]:
    return {
        "position_side": side,
        "code": symbol,
        "position_market": "US",
        "qty": qty,
        "can_sell_qty": "1.00",
        "currency": currency,
        "cost_price": average_cost,
        "cost_price_valid": True,
        "average_cost": average_cost,
        "diluted_cost": "-0.4000",
    }


def _contract_spec(symbol: str = OPTION_SYMBOL) -> dict[str, Any]:
    return {
        "code": symbol,
        "lot_size": "100.0",
        "option_contract_size": 100,
        "option_contract_multiplier": "100.00",
    }


def _clock(base: datetime):
    values = iter(base + timedelta(milliseconds=index) for index in range(20))
    return lambda: next(values)


def _payload(
    *,
    reads: list[list[dict[str, Any]]] | None = None,
    quote_rows: list[dict[str, Any]] | None = None,
    base: datetime | None = None,
) -> dict[str, Any]:
    row = _position()
    trade_context = _TradeContext(reads or [[row], [copy.deepcopy(row)]])
    quote_context = _QuoteContext(
        [_contract_spec()] if quote_rows is None else quote_rows
    )
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
        clock=_clock(
            base
            or datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(seconds=1)
        ),
    )
    assert trade_context.closed is True
    return result.export_payload


def _binding(payload: dict[str, Any]) -> str:
    return str(payload["account"]["binding"])


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _install_refresh_publication(
    payload: dict[str, Any],
    *,
    recorded_at: datetime | None = None,
    suffix: str = "1",
    account_binding: str | None = None,
) -> JournalRefreshPublication:
    """Create a real, FK-complete immutable Journal continuity anchor."""
    init_position_snapshot_schema()
    now = recorded_at or datetime.now(timezone.utc) - timedelta(minutes=1)
    db = get_db()
    with db.session_scope() as session:
        batch = ImportBatch(
            batch_key=f"position-snapshot-test-batch-{suffix}",
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            source_kind="openapi",
            source_schema="test",
            source_sha256=_digest(f"source-{suffix}"),
            parser_name="test",
            parser_version="1",
            window_start=now - timedelta(days=1),
            window_end=now,
            source_timezone="UTC",
            status="succeeded",
            analysis_level="full",
            analysis_ready=True,
            order_observation_count=0,
            fill_observation_count=0,
            rejected_record_count=0,
            reconciliation_status="complete",
            reconciliation_json="{}",
            completeness_score=1,
            completeness_json="{}",
            warnings_json="[]",
            provenance_json="{}",
            recorded_at=now,
        )
        session.add(batch)
        session.flush()
        canonical = CanonicalEvidenceSetRecord(
            set_key=f"position-snapshot-test-set-{suffix}",
            canonical_set_sha256=_digest(f"canonical-{suffix}"),
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            reader_name="test",
            reader_version="1",
            source_cutoff_at=now,
            source_batch_ids_json=json.dumps([batch.id]),
            analysis_ready=True,
            canonical_order_count=0,
            canonical_fill_count=0,
            blocking_issue_count=0,
            canonical_payload_json="{}",
            provenance_json="{}",
            recorded_at=now,
        )
        session.add(canonical)
        session.flush()
        refresh_artifact = JournalRefreshArtifact(
            artifact_key=f"position-snapshot-test-refresh-artifact-{suffix}",
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            account_binding=account_binding or _binding(payload),
            environment="LIVE",
            market="US",
            window_start=now - timedelta(days=1),
            window_end=now,
            generated_at=now,
            source_sha256=_digest(f"refresh-source-{suffix}"),
            evidence_sha256=_digest(f"refresh-evidence-{suffix}"),
            preview_key=_digest(f"refresh-preview-{suffix}"),
            confirm_allowed=True,
            payload_json="{}",
            expires_at=now + timedelta(minutes=30),
            recorded_at=now,
        )
        session.add(refresh_artifact)
        session.flush()
        publication = JournalRefreshPublication(
            publication_key=_digest(f"refresh-publication-{suffix}"),
            refresh_artifact_id=refresh_artifact.id,
            import_batch_id=batch.id,
            canonical_set_id=canonical.id,
            broker="moomoo",
            account_key=ACCOUNT_KEY,
            account_binding=account_binding or _binding(payload),
            broker_queried_through=now,
            latest_fill_at=None,
            evidence_published_through=now,
            import_duplicate=False,
            recorded_at=now,
        )
        session.add(publication)
        session.flush()
        session.refresh(publication)
        session.expunge(publication)
        return publication


def _valid_plan(payload: dict[str, Any]):
    return plan_position_snapshot(
        payload,
        account_key=ACCOUNT_KEY,
        expected_account_binding_id=_binding(payload),
    )


def _save_and_confirm(payload: dict[str, Any]):
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload)
    confirmation = confirm_position_snapshot_artifact(
        artifact.artifact_id,
        preview_key=artifact.preview_key,
        acknowledge_future_only=True,
        expected_account_binding_id=_binding(payload),
        account_key=ACCOUNT_KEY,
    )
    return plan, artifact, confirmation


def _table_counts(*models: Any) -> tuple[int, ...]:
    db = get_db()
    with db.session_scope() as session:
        return tuple(
            int(session.scalar(select(func.count()).select_from(model)) or 0)
            for model in models
        )


def test_plan_is_zero_write_and_missing_binding_is_saved_but_blocked(
    isolated_sqlite,
):
    payload = _payload()

    plan = plan_position_snapshot(
        payload,
        account_key=ACCOUNT_KEY,
        expected_account_binding_id=None,
    )

    assert plan.confirm_allowed is False
    assert plan.future_anchor_candidate is False
    assert plan.blocking_reasons == ("account_continuity_not_established",)
    assert plan.position_snapshot_complete is True
    assert not isolated_sqlite.exists(), "planning must not initialize or write a DB"

    artifact = save_position_snapshot_artifact(plan, payload)
    assert artifact.confirm_allowed is False
    assert artifact.position_snapshot_complete is True
    assert _table_counts(
        PositionSnapshotArtifact,
        ConfirmedPositionSnapshot,
        PositionSnapshotMember,
    ) == (1, 0, 0)
    with pytest.raises(PositionSnapshotError, match="not eligible"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )


def test_caller_binding_cannot_override_latest_refresh_publication():
    payload = _payload()
    publication = _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    mismatched_binding = "f" * 64
    assert mismatched_binding != _binding(payload)
    plan = plan_position_snapshot(
        payload,
        account_key=ACCOUNT_KEY,
        expected_account_binding_id=mismatched_binding,
    )

    assert plan.confirm_allowed is False
    assert plan.blocking_reasons == ("account_binding_mismatch",)
    artifact = save_position_snapshot_artifact(plan, payload)
    assert artifact.confirm_allowed is True
    assert artifact.refresh_publication_id == publication.id
    assert artifact.refresh_publication_key == publication.publication_key
    with pytest.raises(PositionSnapshotError, match="latest Journal refresh"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=mismatched_binding,
            account_key=ACCOUNT_KEY,
        )


def test_stable_complete_position_confirms_without_touching_trade_evidence():
    payload = _payload()
    publication = _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload)
    protected_models = (
        CanonicalEvidenceSetRecord,
        EpisodeBuild,
        EpisodeBuildActivation,
        StrategyEpisode,
        PositionEpisode,
    )
    before = _table_counts(*protected_models)

    confirmation = confirm_position_snapshot_artifact(
        artifact.artifact_id,
        preview_key=artifact.preview_key,
        acknowledge_future_only=True,
        expected_account_binding_id=_binding(payload),
        account_key=ACCOUNT_KEY,
    )

    snapshot = confirmation.snapshot
    assert confirmation.duplicate is False
    assert snapshot.retrieval_complete is True
    assert snapshot.position_snapshot_complete is True
    assert snapshot.stability_status == "stable"
    assert snapshot.contract_spec_status == "complete"
    assert snapshot.content_state == "positions"
    assert snapshot.total_contracts == "2"
    assert snapshot.acknowledged_future_only is True
    assert snapshot.future_anchor_candidate is True
    assert snapshot.historical_opening_proven is False
    assert snapshot.broker_as_of_at is None
    assert snapshot.cost_context_only is True
    assert snapshot.refresh_publication_id == publication.id
    assert snapshot.refresh_publication_key == publication.publication_key
    assert len(snapshot.account_continuity_sha256) == 64
    assert len(snapshot.provenance_sha256) == 64
    assert plan.temporal_policy == {
        "max_future_skew_seconds": int(
            MAX_POSITION_SNAPSHOT_FUTURE_SKEW.total_seconds()
        ),
        "max_acquisition_duration_seconds": int(
            MAX_POSITION_SNAPSHOT_ACQUISITION_DURATION.total_seconds()
        ),
        "max_artifact_freshness_seconds": int(
            MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS.total_seconds()
        ),
    }
    db = get_db()
    with db.session_scope() as session:
        stored = session.get(
            ConfirmedPositionSnapshot,
            snapshot.snapshot_id,
        )
        provenance = json.loads(stored.provenance_json)
        assert provenance["temporal_policy"] == plan.temporal_policy
        assert stored.provenance_sha256 == snapshot.provenance_sha256
        assert stored.refresh_publication_id == publication.id
    foreign_keys = inspect(db._engine).get_foreign_keys(
        ConfirmedPositionSnapshot.__tablename__
    )
    assert any(
        key["referred_table"] == JournalRefreshPublication.__tablename__
        and key["constrained_columns"] == ["refresh_publication_id"]
        for key in foreign_keys
    )
    assert len(snapshot.members) == 1
    member = snapshot.members[0]
    assert member.quantity_contracts == "2"
    assert member.signed_quantity_contracts == "2"
    assert member.contract_multiplier == "100"
    assert member.cost_price == "1.25"
    assert member.average_cost == "1.25"
    assert member.diluted_cost == "-0.4"
    assert member.cost_context_semantics == "broker_display_only_not_realized_pnl"
    assert all("realized" not in key.lower() for key in asdict(member))
    assert all(
        "realized" not in column.name.lower()
        for column in PositionSnapshotMember.__table__.columns
    )
    assert _table_counts(*protected_models) == before


def test_complete_empty_snapshot_is_valid_evidence():
    payload = _payload(reads=[[], []], quote_rows=[])

    plan, _artifact, confirmation = _save_and_confirm(payload)

    assert plan.confirm_allowed is True
    assert plan.content_state == "empty"
    assert plan.contract_spec_status == "not_applicable"
    assert plan.position_count == 0
    assert plan.total_contracts == "0"
    assert confirmation.snapshot.members == ()
    assert confirmation.snapshot.position_snapshot_complete is True
    assert confirmation.snapshot.future_anchor_candidate is True
    assert _table_counts(PositionSnapshotMember) == (0,)


def test_confirmation_requires_explicit_acknowledgement():
    payload = _payload()
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload)

    with pytest.raises(PositionSnapshotError, match="acknowledgement"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=False,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )
    assert _table_counts(ConfirmedPositionSnapshot, PositionSnapshotMember) == (0, 0)


def test_preview_account_and_binding_are_rechecked_at_confirmation():
    payload = _payload()
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload)

    with pytest.raises(PositionSnapshotError, match="preview key"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key="0" * 64,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )
    with pytest.raises(PositionSnapshotError, match="does not exist"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key="different-account",
        )
    with pytest.raises(PositionSnapshotError):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id="e" * 64,
            account_key=ACCOUNT_KEY,
        )
    assert get_position_snapshot_artifact(
        artifact.artifact_id,
        account_key="different-account",
    ) is None


def test_expired_artifact_cannot_be_confirmed(monkeypatch):
    current = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(repository, "_now_utc", lambda: current[0])
    payload = _payload(base=current[0] - timedelta(seconds=1))
    _install_refresh_publication(
        payload,
        recorded_at=current[0] - timedelta(minutes=1),
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload, ttl_minutes=1)
    current[0] += timedelta(minutes=2)

    with pytest.raises(PositionSnapshotError, match="expired"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )


def test_repeated_and_concurrent_confirmation_are_idempotent():
    payload = _payload()
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload)

    def confirm():
        return confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: confirm(), range(2)))
    retry = confirm()

    assert len({item.snapshot.snapshot_id for item in results + (retry,)}) == 1
    assert sorted(item.duplicate for item in results) == [False, True]
    assert retry.duplicate is True
    assert _table_counts(
        PositionSnapshotArtifact,
        ConfirmedPositionSnapshot,
        PositionSnapshotMember,
    ) == (1, 1, 1)


def test_same_holdings_at_a_new_acquisition_are_a_new_snapshot_and_queryable():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    first_payload = _payload(
        base=now - timedelta(seconds=10)
    )
    second_payload = _payload(
        base=now - timedelta(seconds=5)
    )
    _, first_artifact, first_confirmation = _save_and_confirm(first_payload)
    _, second_artifact, second_confirmation = _save_and_confirm(second_payload)

    assert first_artifact.artifact_id != second_artifact.artifact_id
    assert first_confirmation.snapshot.snapshot_id != second_confirmation.snapshot.snapshot_id
    assert first_confirmation.snapshot.member_set_sha256 == (
        second_confirmation.snapshot.member_set_sha256
    )
    latest = get_latest_position_snapshot(ACCOUNT_KEY)
    assert latest is not None
    assert latest.snapshot_id == second_confirmation.snapshot.snapshot_id
    assert get_position_snapshot_detail(
        first_confirmation.snapshot.snapshot_id,
        account_key=ACCOUNT_KEY,
    ) == first_confirmation.snapshot
    assert get_position_snapshot_detail(
        first_confirmation.snapshot.snapshot_id,
        account_key="different-account",
    ) is None
    listed = list_position_snapshots(ACCOUNT_KEY)
    assert [item.snapshot_id for item in listed] == [
        second_confirmation.snapshot.snapshot_id,
        first_confirmation.snapshot.snapshot_id,
    ]
    assert list_position_snapshots(ACCOUNT_KEY, limit=1) == (latest,)
    state = get_latest_position_snapshot_state(ACCOUNT_KEY)
    assert state.snapshot == latest
    assert state.publication is not None
    assert state.publication.publication_id == (
        second_confirmation.snapshot.refresh_publication_id
    )
    assert state.publication.account_binding == _binding(second_payload)


def test_observed_interval_is_distinct_from_operation_completion():
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
    payload = _payload(base=base)

    plan, artifact, confirmation = _save_and_confirm(payload)

    expected_start = base + timedelta(milliseconds=1)
    expected_end = base + timedelta(milliseconds=4)
    expected_operation_end = base + timedelta(milliseconds=5)
    for record in (plan, artifact, confirmation.snapshot):
        assert record.query_started_at == expected_start
        assert record.query_completed_at == expected_end
        assert record.operation_completed_at == expected_operation_end
        assert record.broker_as_of_at is None
    assert plan.query_requested_at == base
    assert confirmation.snapshot.historical_opening_proven is False


@pytest.mark.parametrize(
    "invalid_rows",
    [
        [[_position(side="NET")], [_position(side="NET")]],
        [[_position(qty="-1")], [_position(qty="-1")]],
        [[_position(currency="EUR")], [_position(currency="EUR")]],
        [
            [_position(), _position()],
            [_position(), _position()],
        ],
    ],
    ids=("unknown-side", "negative-quantity", "non-usd", "duplicate-contract"),
)
def test_probe_validation_failures_remain_unconfirmable(invalid_rows):
    payload = _payload(reads=invalid_rows, quote_rows=[])
    assert payload["summary"]["analysis_ready"] is False
    assert payload["summary"]["validation_issues"]
    plan = plan_position_snapshot(
        payload,
        account_key=ACCOUNT_KEY,
        expected_account_binding_id=_binding(payload),
    )

    assert plan.confirm_allowed is False
    assert "position_validation_issues" in plan.blocking_reasons
    artifact = save_position_snapshot_artifact(plan, payload)
    with pytest.raises(PositionSnapshotError, match="not eligible"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )


def test_stability_is_rederived_from_persisted_boundary_rows():
    payload = _payload()
    payload["acquisition"]["first_read"]["boundary_rows"][0][
        "signed_quantity_contracts"
    ] = "1"
    payload["acquisition"]["first_read"]["boundary_sha256"] = _digest_json(
        payload["acquisition"]["first_read"]["boundary_rows"]
    )

    with pytest.raises(PositionSnapshotError, match="stability summary"):
        _valid_plan(payload)


def test_second_boundary_must_match_the_position_records():
    payload = _payload()
    for read_name in ("first_read", "second_read"):
        payload["acquisition"][read_name]["boundary_rows"][0][
            "signed_quantity_contracts"
        ] = "3"
        payload["acquisition"][read_name]["boundary_sha256"] = _digest_json(
            payload["acquisition"][read_name]["boundary_rows"]
        )

    with pytest.raises(PositionSnapshotError, match="boundary evidence"):
        _valid_plan(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["summary"].__setitem__(
                "account_selection",
                "manual_id",
            ),
            "account selection",
        ),
        (
            lambda payload: payload["acquisition"].__setitem__(
                "time_semantics",
                "broker_exact_as_of",
            ),
            "time semantics",
        ),
        (
            lambda payload: payload["summary"]["counts"].__setitem__(
                "first_total_rows",
                0,
            ),
            "summary count",
        ),
    ],
)
def test_summary_and_acquisition_metadata_are_cross_checked(mutation, message):
    payload = _payload()
    mutation(payload)

    with pytest.raises(PositionSnapshotError, match=message):
        _valid_plan(payload)


def test_classified_read_counts_cannot_exceed_total_rows():
    payload = _payload()
    payload["acquisition"]["first_read"]["total_rows"] = 0
    payload["summary"]["counts"]["first_total_rows"] = 0

    with pytest.raises(PositionSnapshotError, match="exceed total rows"):
        _valid_plan(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["records"]["positions"][0].__setitem__(
            "quantity_contracts",
            "2.0",
        ),
        lambda payload: payload["records"]["positions"][0][
            "broker_cost_context"
        ].__setitem__("average_cost", "1.250"),
        lambda payload: payload["records"]["contract_specs"][0].__setitem__(
            "resolved_multiplier",
            100,
        ),
    ],
)
def test_all_decimal_inputs_must_be_canonical_strings(mutate):
    payload = _payload()
    mutate(payload)

    with pytest.raises(PositionSnapshotError, match="decimal"):
        _valid_plan(payload)


def test_temporal_policy_rejects_long_stale_and_future_acquisitions(monkeypatch):
    current = datetime.now(timezone.utc).replace(microsecond=0)

    too_long = _payload(base=current - timedelta(seconds=1))
    completed = datetime.fromisoformat(
        too_long["acquisition"]["first_read"]["started_at"]
    ) + MAX_POSITION_SNAPSHOT_ACQUISITION_DURATION + timedelta(seconds=1)
    too_long["acquisition"]["second_read"]["completed_at"] = completed.isoformat()
    too_long["acquisition"]["completed_at"] = completed.isoformat()
    too_long["generated_at"] = completed.isoformat()
    with pytest.raises(PositionSnapshotError, match="maximum duration"):
        _valid_plan(too_long)

    fresh_payload = _payload(base=current - timedelta(seconds=1))
    fresh_plan = _valid_plan(fresh_payload)
    monkeypatch.setattr(
        repository,
        "_now_utc",
        lambda: fresh_plan.operation_completed_at
        + MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS
        + timedelta(seconds=1),
    )
    with pytest.raises(PositionSnapshotError, match="stale"):
        save_position_snapshot_artifact(fresh_plan, fresh_payload)

    monkeypatch.setattr(
        repository,
        "_now_utc",
        lambda: fresh_plan.query_requested_at
        - MAX_POSITION_SNAPSHOT_FUTURE_SKEW
        - timedelta(seconds=1),
    )
    with pytest.raises(PositionSnapshotError, match="future"):
        save_position_snapshot_artifact(fresh_plan, fresh_payload)


def test_first_confirmation_rechecks_freshness_but_expired_retry_is_idempotent(
    monkeypatch,
):
    current = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(repository, "_now_utc", lambda: current[0])
    payload = _payload(base=current[0] - timedelta(seconds=1))
    _install_refresh_publication(
        payload,
        recorded_at=current[0] - timedelta(minutes=1),
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload, ttl_minutes=1)
    first = confirm_position_snapshot_artifact(
        artifact.artifact_id,
        preview_key=artifact.preview_key,
        acknowledge_future_only=True,
        expected_account_binding_id=_binding(payload),
        account_key=ACCOUNT_KEY,
    )
    current[0] += timedelta(minutes=2)
    retry = confirm_position_snapshot_artifact(
        artifact.artifact_id,
        preview_key=artifact.preview_key,
        acknowledge_future_only=True,
        expected_account_binding_id=_binding(payload),
        account_key=ACCOUNT_KEY,
    )

    assert retry.duplicate is True
    assert retry.snapshot.snapshot_id == first.snapshot.snapshot_id


def test_confirmation_rejects_artifact_when_a_newer_publication_exists():
    payload = _payload()
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload)
    _install_refresh_publication(
        payload,
        recorded_at=datetime.now(timezone.utc),
        suffix=str(next(_PUBLICATION_COUNTER)),
    )

    with pytest.raises(PositionSnapshotError, match="latest Journal refresh"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )


def test_save_uses_database_publication_binding_not_matching_caller_hash():
    payload = _payload()
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
        account_binding="e" * 64,
    )

    with pytest.raises(PositionSnapshotError, match="latest confirmed refresh"):
        save_position_snapshot_artifact(_valid_plan(payload), payload)


def test_unexpired_artifact_can_still_be_rejected_as_stale_at_confirmation(
    monkeypatch,
):
    current = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(repository, "_now_utc", lambda: current[0])
    payload = _payload(base=current[0] - timedelta(seconds=1))
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    artifact = save_position_snapshot_artifact(
        _valid_plan(payload),
        payload,
        ttl_minutes=240,
    )
    current[0] += MAX_POSITION_SNAPSHOT_ARTIFACT_FRESHNESS + timedelta(
        seconds=1
    )

    with pytest.raises(PositionSnapshotError, match="stale"):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("artifact", "scope_sha256", "0" * 64),
        ("artifact", "account_binding_id", "0" * 64),
        ("artifact", "refresh_publication_key", "tampered"),
        ("snapshot", "snapshot_key", "0" * 64),
        ("snapshot", "source_sha256", "0" * 64),
        ("snapshot", "refresh_publication_key", "tampered"),
        ("snapshot", "provenance_json", "{}"),
        ("member", "instrument_key", "0" * 64),
        ("member", "member_key", "0" * 64),
        ("member", "provenance_json", "{}"),
    ],
)
def test_confirmed_reads_fail_closed_on_header_or_provenance_tampering(
    table,
    column,
    value,
):
    payload = _payload()
    _plan, artifact, confirmation = _save_and_confirm(payload)
    snapshot_id = confirmation.snapshot.snapshot_id
    db = get_db()
    if table == "artifact":
        table_name = PositionSnapshotArtifact.__tablename__
        row_id = artifact.artifact_id
    elif table == "snapshot":
        table_name = ConfirmedPositionSnapshot.__tablename__
        row_id = snapshot_id
    else:
        table_name = PositionSnapshotMember.__tablename__
        with db.session_scope() as session:
            row_id = int(
                session.scalar(
                    select(PositionSnapshotMember.id).where(
                        PositionSnapshotMember.snapshot_id == snapshot_id
                    )
                )
            )
    trigger = f"trg_{table_name}_update_immutable"
    with db._engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER {trigger}")
        connection.exec_driver_sql(
            f"UPDATE {table_name} SET {column} = ? WHERE id = ?",
            (value, row_id),
        )

    with pytest.raises(PositionSnapshotError):
        get_position_snapshot_detail(snapshot_id, account_key=ACCOUNT_KEY)


@pytest.mark.parametrize("target", ["payload", "hash"])
def test_tampered_server_owned_artifact_is_rejected(target):
    payload = _payload()
    _install_refresh_publication(
        payload,
        suffix=str(next(_PUBLICATION_COUNTER)),
    )
    plan = _valid_plan(payload)
    artifact = save_position_snapshot_artifact(plan, payload)
    db = get_db()
    trigger = (
        "trg_journal_v2_position_snapshot_artifacts_update_immutable"
    )
    with db._engine.begin() as connection:
        # Simulate out-of-band storage corruption.  Confirmation recreates the
        # guard before reading and must still detect the corrupted evidence.
        connection.exec_driver_sql(f"DROP TRIGGER {trigger}")
        if target == "payload":
            tampered = copy.deepcopy(payload)
            tampered["summary"]["has_positions"] = False
            connection.exec_driver_sql(
                "UPDATE journal_v2_position_snapshot_artifacts "
                "SET payload_json = ? WHERE id = ?",
                (json.dumps(tampered, sort_keys=True), artifact.artifact_id),
            )
        else:
            connection.exec_driver_sql(
                "UPDATE journal_v2_position_snapshot_artifacts "
                "SET source_sha256 = ? WHERE id = ?",
                ("0" * 64, artifact.artifact_id),
            )

    with pytest.raises(PositionSnapshotError):
        confirm_position_snapshot_artifact(
            artifact.artifact_id,
            preview_key=artifact.preview_key,
            acknowledge_future_only=True,
            expected_account_binding_id=_binding(payload),
            account_key=ACCOUNT_KEY,
        )


def test_all_snapshot_tables_reject_update_and_delete():
    payload = _payload()
    _plan, artifact, confirmation = _save_and_confirm(payload)
    snapshot_id = confirmation.snapshot.snapshot_id
    db = get_db()
    with db.session_scope() as session:
        member_id = int(
            session.scalar(
                select(PositionSnapshotMember.id).where(
                    PositionSnapshotMember.snapshot_id == snapshot_id
                )
            )
        )

    targets = (
        (PositionSnapshotArtifact.__tablename__, artifact.artifact_id),
        (ConfirmedPositionSnapshot.__tablename__, snapshot_id),
        (PositionSnapshotMember.__tablename__, member_id),
    )
    init_position_snapshot_schema()
    for table_name, row_id in targets:
        with pytest.raises(DatabaseError, match="append-only"):
            with db._engine.begin() as connection:
                connection.exec_driver_sql(
                    f"UPDATE {table_name} SET id = id WHERE id = ?",
                    (row_id,),
                )
        with pytest.raises(DatabaseError, match="append-only"):
            with db._engine.begin() as connection:
                connection.exec_driver_sql(
                    f"DELETE FROM {table_name} WHERE id = ?",
                    (row_id,),
                )

    assert _table_counts(
        PositionSnapshotArtifact,
        ConfirmedPositionSnapshot,
        PositionSnapshotMember,
    ) == (1, 1, 1)


def test_global_database_create_all_installs_snapshot_append_only_triggers():
    # Do not call ``init_position_snapshot_schema`` here: the global database
    # initialization path itself must establish the append-only boundary.
    db = get_db()
    with db._engine.connect() as connection:
        names = {
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND name LIKE "
                "'trg_journal_v2_position_snapshot%_immutable'"
            )
        }
    expected = {
        f"trg_{model.__tablename__}_{operation}_immutable"
        for model in (
            PositionSnapshotArtifact,
            ConfirmedPositionSnapshot,
            PositionSnapshotMember,
        )
        for operation in ("update", "delete")
    }
    assert names == expected


def test_global_database_create_all_repairs_existing_snapshot_tables(
    isolated_sqlite,
):
    # Existing tables do not emit table-level ``after_create`` events.  The
    # global database initialization must still close that protection window.
    with sqlite3.connect(isolated_sqlite) as connection:
        for model in (
            PositionSnapshotArtifact,
            ConfirmedPositionSnapshot,
            PositionSnapshotMember,
        ):
            connection.execute(
                f"CREATE TABLE {model.__tablename__} "
                "(id INTEGER PRIMARY KEY)"
            )

    db = get_db()
    with db._engine.connect() as connection:
        names = {
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND name LIKE "
                "'trg_journal_v2_position_snapshot%_immutable'"
            )
        }
    expected = {
        f"trg_{model.__tablename__}_{operation}_immutable"
        for model in (
            PositionSnapshotArtifact,
            ConfirmedPositionSnapshot,
            PositionSnapshotMember,
        )
        for operation in ("update", "delete")
    }
    assert names == expected


def test_additive_migration_covers_every_post_preview_snapshot_column(
    isolated_sqlite,
):
    # Simulate the short-lived schema created by an early hot reload.  These
    # tables deliberately retain their old columns and are never rebuilt.
    with sqlite3.connect(isolated_sqlite) as connection:
        connection.execute(
            "CREATE TABLE journal_v2_position_snapshot_artifacts ("
            "id INTEGER PRIMARY KEY, broker TEXT, account_key TEXT, "
            "recorded_at DATETIME, coverage_complete BOOLEAN)"
        )
        connection.execute(
            "CREATE TABLE journal_v2_position_snapshots ("
            "id INTEGER PRIMARY KEY, broker TEXT, account_key TEXT, "
            "recorded_at DATETIME)"
        )
        connection.execute(
            "CREATE TABLE journal_v2_position_snapshot_members ("
            "id INTEGER PRIMARY KEY, snapshot_id INTEGER, symbol TEXT, "
            "underlying TEXT, expiry DATE)"
        )

    db = get_db()
    init_position_snapshot_schema()
    expected_additions = {
        PositionSnapshotArtifact.__tablename__: {
            "position_snapshot_complete",
            "refresh_publication_id",
            "refresh_publication_key",
            "account_continuity_sha256",
        },
        ConfirmedPositionSnapshot.__tablename__: {
            "refresh_publication_id",
            "refresh_publication_key",
            "account_continuity_sha256",
            "stability_evidence_sha256",
            "provenance_sha256",
        },
        PositionSnapshotMember.__tablename__: {"provenance_sha256"},
    }
    db_inspector = inspect(db._engine)
    for table_name, expected_columns in expected_additions.items():
        actual_columns = {
            str(column["name"])
            for column in db_inspector.get_columns(table_name)
        }
        assert expected_columns <= actual_columns

    artifact_columns = {
        str(column["name"])
        for column in db_inspector.get_columns(
            PositionSnapshotArtifact.__tablename__
        )
    }
    assert "coverage_complete" in artifact_columns

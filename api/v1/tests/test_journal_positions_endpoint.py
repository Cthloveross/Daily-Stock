from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.v1.endpoints import journal_positions
from src.journal.brokers.moomoo_position_snapshot import (
    MoomooPositionSnapshotError,
)
from src.journal.ledger.position_snapshot_repository import PositionSnapshotError


BINDING = "a" * 64
OTHER_BINDING = "b" * 64
PREVIEW_KEY = "c" * 64
ARTIFACT_KEY = "d" * 64
SNAPSHOT_KEY = "e" * 64
HASH = "f" * 64
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def configured_readonly_env(monkeypatch):
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_OPEND_HOST", "127.0.0.1")
    monkeypatch.setenv("MOOMOO_OPEND_PORT", "11111")
    monkeypatch.setenv("MOOMOO_JOURNAL_ENV", "LIVE")
    monkeypatch.setenv("MOOMOO_JOURNAL_ACCOUNT_ID", "101")
    monkeypatch.setenv(
        "MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET",
        "journal-position-api-test-secret-long-enough",
    )
    monkeypatch.setenv("MOOMOO_JOURNAL_QUERY_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_TIMEOUT_SECONDS", "60")
    monkeypatch.setattr(
        journal_positions,
        "_now_utc",
        lambda: NOW + timedelta(minutes=10),
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(journal_positions.router, prefix="/api/v1/journal")
    return TestClient(app)


def _member(symbol: str = "US.AAPL260717C00200000") -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        asset_type="option",
        underlying="AAPL",
        expiry=date(2026, 7, 17),
        strike="200",
        option_right="C",
        currency="USD",
        position_side="LONG",
        quantity_contracts="2",
        signed_quantity_contracts="2",
        can_sell_quantity_contracts="2",
        contract_multiplier="100",
        contract_multiplier_basis=(
            "moomoo_market_snapshot_three_field_consensus"
        ),
        cost_price="1.3",
        cost_price_valid=True,
        average_cost="1.3",
        diluted_cost="-0.4",
        cost_context_semantics="broker_display_only_not_realized_pnl",
        source_position_sha256=HASH,
        source_contract_spec_sha256=HASH,
    )


def _counts(position_count: int, *, zero_rows: int = 0) -> dict[str, int]:
    return {
        "positions": position_count,
        "contract_specs": position_count,
        "first_total_rows": position_count + zero_rows,
        "second_total_rows": position_count + zero_rows,
        "first_filtered_zero_quantity_rows": zero_rows,
        "second_filtered_zero_quantity_rows": zero_rows,
        "second_filtered_non_us_rows": 0,
        "second_filtered_non_option_rows": 0,
        "validation_issues": 0,
    }


def _plan(
    *,
    members: tuple[SimpleNamespace, ...] | None = None,
    binding: str = BINDING,
    confirm_allowed: bool = True,
    blocking_reasons: tuple[str, ...] = (),
    zero_rows: int = 0,
) -> SimpleNamespace:
    rows = members if members is not None else (_member(),)
    return SimpleNamespace(
        account_binding_id=binding,
        query_started_at=NOW + timedelta(milliseconds=1),
        query_completed_at=NOW + timedelta(milliseconds=4),
        operation_completed_at=NOW + timedelta(milliseconds=5),
        broker_as_of_at=None,
        retrieval_complete=True,
        position_snapshot_complete=True,
        stability_status="stable",
        stability={
            "stable": True,
            "comparison_basis": (
                "instrument_position_side_signed_quantity_contracts"
            ),
            "added_symbols": (),
            "removed_symbols": (),
            "changed_symbols": (),
        },
        contract_spec_status=("not_applicable" if not rows else "complete"),
        analysis_ready=True,
        content_state=("empty" if not rows else "positions"),
        position_count=len(rows),
        contract_spec_count=len(rows),
        total_contracts=str(sum(int(item.quantity_contracts) for item in rows)),
        counts=_counts(len(rows), zero_rows=zero_rows),
        warnings=(),
        validation_issues=(),
        members=rows,
        artifact_key=ARTIFACT_KEY,
        preview_key=PREVIEW_KEY,
        scope_sha256=HASH,
        source_sha256=HASH,
        evidence_sha256=HASH,
        snapshot_sha256=HASH,
        stability_evidence_sha256=HASH,
        member_set_sha256=HASH,
        confirm_allowed=confirm_allowed,
        blocking_reasons=blocking_reasons,
        future_anchor_candidate=confirm_allowed,
        historical_opening_proven=False,
    )


def _artifact(*, confirm_allowed: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=7,
        artifact_key=ARTIFACT_KEY,
        preview_key=PREVIEW_KEY,
        expires_at=NOW + timedelta(minutes=30),
        confirm_allowed=confirm_allowed,
    )


def _snapshot(
    *,
    members: tuple[SimpleNamespace, ...] | None = None,
    binding: str = BINDING,
) -> SimpleNamespace:
    rows = members if members is not None else (_member(),)
    return SimpleNamespace(
        snapshot_id=11,
        snapshot_key=SNAPSHOT_KEY,
        artifact_id=7,
        account_binding_id=binding,
        refresh_publication_id=31,
        query_started_at=NOW + timedelta(milliseconds=1),
        query_completed_at=NOW + timedelta(milliseconds=4),
        operation_completed_at=NOW + timedelta(milliseconds=5),
        broker_as_of_at=None,
        scope_sha256=HASH,
        source_sha256=HASH,
        evidence_sha256=HASH,
        snapshot_sha256=HASH,
        member_set_sha256=HASH,
        acknowledged_future_only=True,
        historical_opening_proven=False,
        cost_context_only=True,
        recorded_at=NOW + timedelta(minutes=1),
        retrieval_complete=True,
        position_snapshot_complete=True,
        stability_status="stable",
        stability={
            "stable": True,
            "comparison_basis": (
                "instrument_position_side_signed_quantity_contracts"
            ),
            "added_symbols": (),
            "removed_symbols": (),
            "changed_symbols": (),
        },
        contract_spec_status=("not_applicable" if not rows else "complete"),
        analysis_ready=True,
        content_state=("empty" if not rows else "positions"),
        total_contracts=str(sum(int(item.quantity_contracts) for item in rows)),
        counts=_counts(len(rows)),
        warnings=(),
        validation_issues=(),
        members=rows,
        future_anchor_candidate=True,
    )


def _publication(binding: str = BINDING, publication_id: int = 31):
    return SimpleNamespace(
        account_binding=binding,
        publication_id=publication_id,
    )


def _latest_state(*, snapshot, publication):
    return SimpleNamespace(snapshot=snapshot, publication=publication)


def _continuity_assessment(
    *,
    status: str = "no_snapshot",
    ready: bool = False,
) -> SimpleNamespace:
    counts = SimpleNamespace(
        snapshot_member_count=0 if status == "no_snapshot" else 1,
        chain_publication_count=1 if status == "ready" else 0,
        chain_batch_count=1 if status == "ready" else 0,
        target_order_count=2 if status == "ready" else 0,
        target_fill_count=2 if status == "ready" else 0,
        guard_fill_count=0,
        pre_boundary_fill_count=0,
        post_boundary_fill_count=2 if status == "ready" else 0,
        aggregate_changed_pre_boundary_count=0,
        straddling_order_count=0,
        straddling_group_count=0,
        unbacked_post_boundary_count=0,
        option_identity_mismatch_count=0,
    )
    has_snapshot = status != "no_snapshot"
    has_target = status in {"blocked", "ready"}
    return SimpleNamespace(
        policy_version="position-snapshot-continuity-fence/1.0",
        status=status,
        ready_for_episode_build=ready,
        fence_key=(HASH if ready else None),
        account_key="default",
        snapshot_id=(11 if has_snapshot else None),
        snapshot_key=(SNAPSHOT_KEY if has_snapshot else None),
        anchor_publication_id=(31 if has_snapshot else None),
        anchor_publication_key=("anchor-31" if has_snapshot else None),
        target_publication_id=(32 if has_target else None),
        target_publication_key=("target-32" if has_target else None),
        target_canonical_set_id=(9 if has_target else None),
        target_canonical_set_sha256=(HASH if has_target else None),
        boundary_at=(NOW if has_snapshot else None),
        guard_started_at=(NOW - timedelta(seconds=2) if has_snapshot else None),
        guard_completed_at=(NOW if has_snapshot else None),
        publication_ids=((32,) if has_target else ()),
        source_batch_ids=((41,) if has_target else ()),
        counts=counts,
        reasons=(
            ()
            if ready
            else (
                SimpleNamespace(
                    code=(
                        "no_confirmed_snapshot"
                        if status == "no_snapshot"
                        else "no_later_publication"
                    ),
                    message="continuity evidence is not ready",
                    entity_kind=None,
                    entity_ids=(),
                    count=1,
                ),
            )
        ),
    )


def _probe_payload(binding: str = BINDING) -> dict:
    return {
        "account": {"binding": binding},
        "journal_database_written": False,
        "trading_actions_performed": False,
    }


def _future_episode_preview(**overrides) -> SimpleNamespace:
    counts = SimpleNamespace(
        canonical_member_count=5,
        opening_position_count=1,
        opening_contract_count=Decimal("2"),
        post_boundary_order_event_count=1,
        post_boundary_fill_event_count=2,
        supporting_order_count=1,
        excluded_non_option_event_count=0,
        execution_group_count=0,
        source_event_count=3,
        planned_position_episode_count=2,
        planned_open_episode_count=1,
        planned_closed_episode_count=1,
        left_censored_episode_count=1,
        right_censored_episode_count=1,
        headline_episode_count=1,
        headline_excluded_episode_count=1,
        group_fee_affected_episode_count=0,
    )
    values = {
        "projection_name": "position_snapshot_fenced_canonical_projection",
        "projection_version": "1.0.0",
        "continuity_policy_version": "position-snapshot-continuity-fence/1.0",
        "account_key": "default_moomoo_us",
        "scope": "moomoo_live_us_options",
        "fence_key": HASH,
        "snapshot_id": 11,
        "snapshot_key": SNAPSHOT_KEY,
        "target_publication_id": 32,
        "target_publication_key": HASH,
        "target_canonical_set_id": 9,
        "target_canonical_set_sha256": HASH,
        "canonical_source_batch_ids": (41,),
        "publication_source_batch_ids": (41,),
        "boundary_at": NOW,
        "source_cutoff_at": NOW + timedelta(days=1),
        "builder_name": "canonical_position_episode_builder",
        "builder_version": "1.0.0",
        "builder_config_sha256": HASH,
        "evidence_set_sha256": HASH,
        "planned_build_key": HASH,
        "opening_boundary_policy": "complete_snapshot",
        "max_multiplier_proof_residual": Decimal("0"),
        "source_known_fee_total": Decimal("1.20"),
        "accounted_known_fee_total": Decimal("1.20"),
        "retained_execution_group_fee_total": Decimal("0"),
        "fee_conservation_by_currency": {
            "USD": {
                "ordinary_source": Decimal("1.20"),
                "ordinary_allocated": Decimal("1.20"),
                "retained_execution_group": Decimal("0"),
                "source_known": Decimal("1.20"),
                "accounted": Decimal("1.20"),
            }
        },
        "fee_conserved": True,
        "headline_realized_pnl_gross": Decimal("50"),
        "headline_total_fee": Decimal("1.20"),
        "headline_realized_pnl_net": Decimal("48.80"),
        "counts": counts,
        "warnings": ("opening_positions_remain_left_censored",),
        "preview_only": True,
        "default_will_change": False,
        "evidence_written": False,
        "trading_action_performed": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_preview_fakes(
    monkeypatch,
    *,
    publication,
    plan,
    payload: dict | None = None,
):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        journal_positions,
        "get_latest_refresh_publication",
        lambda account_key: publication,
    )

    def probe(config):
        captured["config"] = config
        return SimpleNamespace(export_payload=payload or _probe_payload())

    def planner(export_payload, *, account_key, expected_account_binding_id):
        captured["payload"] = export_payload
        captured["account_key"] = account_key
        captured["expected_binding"] = expected_account_binding_id
        return plan

    def save(candidate, export_payload):
        captured["saved_plan"] = candidate
        captured["saved_payload"] = export_payload
        return _artifact(confirm_allowed=plan.confirm_allowed)

    monkeypatch.setattr(journal_positions, "run_position_snapshot_probe", probe)
    monkeypatch.setattr(journal_positions, "plan_position_snapshot", planner)
    monkeypatch.setattr(journal_positions, "save_position_snapshot_artifact", save)
    return captured


def test_preview_without_publication_saves_artifact_but_cannot_confirm(monkeypatch):
    plan = _plan(
        confirm_allowed=False,
        blocking_reasons=("account_continuity_not_established",),
    )
    captured = _install_preview_fakes(
        monkeypatch,
        publication=None,
        plan=plan,
    )

    response = _client().post("/api/v1/journal/v2/position-snapshots/preview", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_id"] == 7
    assert body["artifact_key"] == ARTIFACT_KEY
    assert body["preview_key"] == PREVIEW_KEY
    assert body["confirm_allowed"] is False
    assert body["blocking_reasons"] == ["account_continuity_not_established"]
    assert body["evidence_written"] is False
    assert body["trading_action_performed"] is False
    assert captured["expected_binding"] is None
    assert captured["saved_plan"] is plan
    config = captured["config"]
    assert config.env == "LIVE"
    assert config.market == "US"
    assert config.acc_id == "101"


def test_preview_rejects_publication_binding_mismatch_before_artifact_save(
    monkeypatch,
):
    plan = _plan(binding=BINDING, confirm_allowed=False)
    saved: list[bool] = []
    monkeypatch.setattr(
        journal_positions,
        "get_latest_refresh_publication",
        lambda account_key: _publication(OTHER_BINDING),
    )
    monkeypatch.setattr(
        journal_positions,
        "run_position_snapshot_probe",
        lambda config: SimpleNamespace(export_payload=_probe_payload(BINDING)),
    )
    monkeypatch.setattr(
        journal_positions,
        "plan_position_snapshot",
        lambda payload, **kwargs: plan,
    )
    monkeypatch.setattr(
        journal_positions,
        "save_position_snapshot_artifact",
        lambda *args, **kwargs: saved.append(True),
    )

    response = _client().post("/api/v1/journal/v2/position-snapshots/preview", json={})

    assert response.status_code == 409
    assert "账户" in response.json()["detail"] or "account" in response.json()["detail"]
    assert saved == []


def test_preview_projects_complete_empty_snapshot_and_zero_cache_counts(monkeypatch):
    plan = _plan(members=(), zero_rows=18)
    _install_preview_fakes(
        monkeypatch,
        publication=_publication(),
        plan=plan,
    )

    response = _client().post("/api/v1/journal/v2/position-snapshots/preview", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["confirm_allowed"] is True
    observation = body["observation"]
    assert observation["content_state"] == "empty"
    assert observation["position_count"] == 0
    assert observation["total_contracts"] == "0"
    assert observation["positions"] == []
    assert observation["position_snapshot_complete"] is True
    assert observation["future_anchor_candidate"] is True
    assert observation["historical_opening_proven"] is False
    assert observation["broker_as_of"] is None
    assert observation["filter_counts"]["filtered_zero_quantity_rows"] == 18


def test_confirm_requires_explicit_future_only_and_uses_latest_binding(monkeypatch):
    snapshot = _snapshot()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        journal_positions,
        "get_latest_refresh_publication",
        lambda account_key: _publication(),
    )

    def confirm(artifact_id, **kwargs):
        captured["artifact_id"] = artifact_id
        captured.update(kwargs)
        return SimpleNamespace(snapshot=snapshot, duplicate=False)

    monkeypatch.setattr(journal_positions, "confirm_position_snapshot_artifact", confirm)
    path = "/api/v1/journal/v2/position-snapshots/7/confirm"

    rejected = _client().post(
        path,
        json={
            "preview_key": PREVIEW_KEY,
            "acknowledge_future_only": False,
        },
    )
    assert rejected.status_code == 422
    assert captured == {}

    response = _client().post(
        path,
        json={
            "preview_key": PREVIEW_KEY,
            "acknowledge_future_only": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert captured == {
        "artifact_id": 7,
        "preview_key": PREVIEW_KEY,
        "acknowledge_future_only": True,
        "expected_account_binding_id": BINDING,
        "account_key": "default_moomoo_us",
    }
    assert body["duplicate"] is False
    assert body["trading_action_performed"] is False
    assert set(body) == {"snapshot", "duplicate", "trading_action_performed"}
    observation = body["snapshot"]["observation"]
    assert body["snapshot"]["is_current"] is True
    assert body["snapshot"]["account_binding_status"] == (
        "matches_latest_publication"
    )
    assert body["snapshot"]["publication_anchor_status"] == "latest"
    assert body["snapshot"]["freshness"] == {
        "status": "fresh",
        "age_seconds": 599,
        "future_skew_seconds": 0,
        "threshold_seconds": 1800,
        "evaluated_at": "2026-07-30T12:10:00Z",
        "age_basis": "operation_completed_at",
        "time_semantics": (
            "local_clock_age_since_operation_completed_at; "
            "locally_bracketed_acquisition_interval; "
            "broker_as_of_not_provided"
        ),
    }
    assert observation["historical_opening_proven"] is False
    position = observation["positions"][0]
    assert position["basis"] == "moomoo_market_snapshot_three_field_consensus"
    assert position["context"] == {
        "cost_price": "1.3",
        "cost_price_valid": True,
        "average_cost": "1.3",
        "diluted_cost": "-0.4",
        "semantics": "broker_display_only_not_realized_pnl",
    }
    assert "realized_pnl" not in position
    assert "unrealized_pnl" not in position


def test_latest_returns_single_nested_snapshot_source_and_continuity(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setattr(
        journal_positions,
        "get_latest_position_snapshot_state",
        lambda account_key: _latest_state(
            snapshot=snapshot,
            publication=_publication(),
        ),
    )

    response = _client().get("/api/v1/journal/v2/position-snapshots/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["configured"] is True
    assert body["continuity_ready"] is True
    assert body["continuity_reason"] is None
    assert body["latest_is_current"] is True
    assert body["latest_account_binding_matches"] is True
    assert body["latest_publication_anchor_matches"] is True
    assert body["trading_action_performed"] is False
    assert set(body) == {
        "enabled",
        "configured",
        "continuity_ready",
        "continuity_reason",
        "latest_is_current",
        "latest_account_binding_matches",
        "latest_publication_anchor_matches",
        "latest",
        "trading_action_performed",
    }
    assert body["latest"]["id"] == 11
    assert body["latest"]["observation"]["positions"][0]["symbol"].startswith("US.")


def test_episode_boundary_readiness_returns_zero_write_no_snapshot(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "assess_latest_position_snapshot_continuity",
        lambda account_key: _continuity_assessment(),
    )

    response = _client().get(
        "/api/v1/journal/v2/position-snapshots/episode-boundary-readiness"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_snapshot"
    assert body["ready_for_episode_build"] is False
    assert body["reasons"][0]["code"] == "no_confirmed_snapshot"
    assert body["evidence_written"] is False
    assert body["trading_action_performed"] is False


def test_episode_boundary_readiness_freezes_ready_evidence_identity(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "assess_latest_position_snapshot_continuity",
        lambda account_key: _continuity_assessment(status="ready", ready=True),
    )

    response = _client().get(
        "/api/v1/journal/v2/position-snapshots/episode-boundary-readiness"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["ready_for_episode_build"] is True
    assert body["fence_key"] == HASH
    assert body["target_canonical_set_id"] == 9
    assert body["target_canonical_set_sha256"] == HASH
    assert body["counts"]["post_boundary_fill_count"] == 2
    assert body["evidence_written"] is False
    assert body["trading_action_performed"] is False


def test_episode_boundary_readiness_rejects_a_contradictory_ready_flag(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "assess_latest_position_snapshot_continuity",
        lambda account_key: _continuity_assessment(
            status="blocked",
            ready=True,
        ),
    )

    response = _client().get(
        "/api/v1/journal/v2/position-snapshots/episode-boundary-readiness"
    )

    assert response.status_code == 409
    assert "inconsistent" in response.json()["detail"]


def test_future_episode_preview_returns_compact_zero_write_plan(monkeypatch):
    captured: dict[str, str] = {}

    def preview(account_key: str, expected_fence_key: str):
        captured.update(
            account_key=account_key,
            expected_fence_key=expected_fence_key,
        )
        return _future_episode_preview()

    monkeypatch.setattr(
        journal_positions,
        "preview_fenced_position_episodes",
        preview,
    )

    response = _client().get(
        "/api/v1/journal/v2/episode-builds/position-snapshot/preview",
        params={"expected_fence_key": HASH},
    )

    assert response.status_code == 200
    body = response.json()
    assert captured == {
        "account_key": "default_moomoo_us",
        "expected_fence_key": HASH,
    }
    assert body["fence_key"] == HASH
    assert body["snapshot_id"] == 11
    assert body["target_canonical_set_id"] == 9
    assert body["counts"]["opening_contract_count"] == "2"
    assert body["counts"]["planned_position_episode_count"] == 2
    assert body["counts"]["planned_open_episode_count"] == 1
    assert body["counts"]["planned_closed_episode_count"] == 1
    assert body["headline_realized_pnl_net"] == "48.80"
    assert body["preview_only"] is True
    assert body["business_data_written"] is False
    assert body["episode_build_written"] is False
    assert body["activation_changed"] is False
    assert body["evidence_written"] is False
    assert body["trading_action_performed"] is False
    assert "episodes" not in body


def test_future_episode_preview_rejects_stale_fence_without_result(monkeypatch):
    def stale(*args, **kwargs):
        raise journal_positions.FuturePositionEpisodePreviewError(
            "position continuity fence changed; request a new preview"
        )

    monkeypatch.setattr(
        journal_positions,
        "preview_fenced_position_episodes",
        stale,
    )

    response = _client().get(
        "/api/v1/journal/v2/episode-builds/position-snapshot/preview",
        params={"expected_fence_key": HASH},
    )

    assert response.status_code == 409
    assert "fence changed" in response.json()["detail"]


def test_future_episode_preview_rejects_unsafe_or_inconsistent_result(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "preview_fenced_position_episodes",
        lambda *args: _future_episode_preview(default_will_change=True),
    )
    unsafe = _client().get(
        "/api/v1/journal/v2/episode-builds/position-snapshot/preview",
        params={"expected_fence_key": HASH},
    )
    assert unsafe.status_code == 409
    assert "zero-write" in unsafe.json()["detail"]

    inconsistent_counts = _future_episode_preview().counts
    inconsistent_counts.planned_open_episode_count = 2
    monkeypatch.setattr(
        journal_positions,
        "preview_fenced_position_episodes",
        lambda *args: _future_episode_preview(counts=inconsistent_counts),
    )
    inconsistent = _client().get(
        "/api/v1/journal/v2/episode-builds/position-snapshot/preview",
        params={"expected_fence_key": HASH},
    )
    assert inconsistent.status_code == 409
    assert "lifecycle counts" in inconsistent.json()["detail"]


def test_latest_explains_missing_or_changed_account_continuity(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "get_latest_position_snapshot_state",
        lambda account_key: _latest_state(
            snapshot=None,
            publication=None,
        ),
    )
    missing = _client().get("/api/v1/journal/v2/position-snapshots/latest")
    assert missing.status_code == 200
    assert missing.json()["continuity_ready"] is False
    assert missing.json()["latest_is_current"] is False
    assert missing.json()["latest_account_binding_matches"] is None
    assert "请先完成一次刷新确认" in missing.json()["continuity_reason"]

    monkeypatch.setattr(
        journal_positions,
        "get_latest_position_snapshot_state",
        lambda account_key: _latest_state(
            snapshot=_snapshot(binding=OTHER_BINDING),
            publication=_publication(),
        ),
    )
    changed = _client().get("/api/v1/journal/v2/position-snapshots/latest")
    assert changed.status_code == 200
    assert changed.json()["continuity_ready"] is True
    assert "旧快照仅作为历史记录" in changed.json()["continuity_reason"]
    assert changed.json()["latest_is_current"] is False
    assert changed.json()["latest_account_binding_matches"] is False
    assert changed.json()["latest"]["account_binding_status"] == "mismatch"


def test_latest_confirmed_snapshot_exposes_stale_age_without_hiding_it(
    monkeypatch,
):
    monkeypatch.setattr(
        journal_positions,
        "_now_utc",
        lambda: NOW + timedelta(minutes=31),
    )
    monkeypatch.setattr(
        journal_positions,
        "get_latest_position_snapshot_state",
        lambda account_key: _latest_state(
            snapshot=_snapshot(),
            publication=_publication(),
        ),
    )

    response = _client().get("/api/v1/journal/v2/position-snapshots/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_is_current"] is False
    assert body["latest_account_binding_matches"] is True
    assert body["latest"]["is_current"] is False
    assert body["latest"]["freshness"]["status"] == "stale"
    assert body["latest"]["freshness"]["age_seconds"] == 1859
    assert body["latest"]["freshness"]["threshold_seconds"] == 1800
    assert "超过 30 分钟" in body["continuity_reason"]


def test_latest_same_binding_but_newer_publication_supersedes_snapshot(
    monkeypatch,
):
    monkeypatch.setattr(
        journal_positions,
        "get_latest_position_snapshot_state",
        lambda account_key: _latest_state(
            snapshot=_snapshot(),
            publication=_publication(publication_id=32),
        ),
    )

    response = _client().get("/api/v1/journal/v2/position-snapshots/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_account_binding_matches"] is True
    assert body["latest_publication_anchor_matches"] is False
    assert body["latest_is_current"] is False
    assert body["latest"]["account_binding_status"] == (
        "matches_latest_publication"
    )
    assert body["latest"]["publication_anchor_status"] == "superseded"
    assert body["latest"]["is_current"] is False


def test_latest_future_clock_skew_beyond_policy_is_not_current(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "_now_utc",
        lambda: NOW - timedelta(minutes=3),
    )
    monkeypatch.setattr(
        journal_positions,
        "get_latest_position_snapshot_state",
        lambda account_key: _latest_state(
            snapshot=_snapshot(),
            publication=_publication(),
        ),
    )

    response = _client().get("/api/v1/journal/v2/position-snapshots/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_account_binding_matches"] is True
    assert body["latest_publication_anchor_matches"] is True
    assert body["latest_is_current"] is False
    assert body["latest"]["is_current"] is False
    assert body["latest"]["freshness"]["status"] == "stale"
    assert body["latest"]["freshness"]["age_seconds"] == 0
    assert body["latest"]["freshness"]["future_skew_seconds"] == 180
    assert "时钟偏差超过 2 分钟" in body["continuity_reason"]


def test_latest_currentness_uses_one_repository_aggregate(monkeypatch):
    def forbidden_independent_read(_account_key):
        raise AssertionError("latest must not use independent publication reads")

    monkeypatch.setattr(
        journal_positions,
        "get_latest_refresh_publication",
        forbidden_independent_read,
    )
    monkeypatch.setattr(
        journal_positions,
        "get_latest_position_snapshot_state",
        lambda account_key: _latest_state(
            snapshot=_snapshot(binding=OTHER_BINDING),
            publication=_publication(),
        ),
    )

    response = _client().get("/api/v1/journal/v2/position-snapshots/latest")

    assert response.status_code == 200
    assert response.json()["latest_is_current"] is False
    assert response.json()["latest_account_binding_matches"] is False


def test_confirm_without_publication_is_conflict_and_missing_artifact_is_404(
    monkeypatch,
):
    path = "/api/v1/journal/v2/position-snapshots/99/confirm"
    payload = {
        "preview_key": PREVIEW_KEY,
        "acknowledge_future_only": True,
    }
    monkeypatch.setattr(
        journal_positions,
        "get_latest_refresh_publication",
        lambda account_key: None,
    )
    no_continuity = _client().post(path, json=payload)
    assert no_continuity.status_code == 409

    monkeypatch.setattr(
        journal_positions,
        "get_latest_refresh_publication",
        lambda account_key: _publication(),
    )

    def missing(*args, **kwargs):
        raise PositionSnapshotError("position snapshot artifact does not exist")

    monkeypatch.setattr(journal_positions, "confirm_position_snapshot_artifact", missing)
    not_found = _client().post(path, json=payload)
    assert not_found.status_code == 404


def test_preview_maps_configuration_to_409_and_opend_failure_to_503(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "false")
    configuration = _client().post(
        "/api/v1/journal/v2/position-snapshots/preview",
        json={},
    )
    assert configuration.status_code == 409

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setattr(
        journal_positions,
        "get_latest_refresh_publication",
        lambda account_key: _publication(),
    )

    def unavailable(_config):
        raise MoomooPositionSnapshotError("OpenD is not reachable")

    monkeypatch.setattr(journal_positions, "run_position_snapshot_probe", unavailable)
    opend = _client().post(
        "/api/v1/journal/v2/position-snapshots/preview",
        json={},
    )
    assert opend.status_code == 503
    assert "OpenD" in opend.json()["detail"]


def test_main_v1_router_registers_position_snapshot_paths():
    # Newer Starlette wraps included routers without a flat ``path``
    # attribute; assert via the mounted app's openapi schema instead.
    from fastapi import FastAPI

    from api.v1.router import router

    app = FastAPI()
    app.include_router(router)
    registered = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert (
        "GET",
        "/api/v1/journal/v2/position-snapshots/latest",
    ) in registered
    assert (
        "POST",
        "/api/v1/journal/v2/position-snapshots/preview",
    ) in registered
    assert (
        "POST",
        "/api/v1/journal/v2/position-snapshots/{artifact_id}/confirm",
    ) in registered
    assert (
        "POST",
        "/api/v1/journal/v2/episode-builds/position-snapshot",
    ) in registered


_FUTURE_SOURCE_KIND = "position_snapshot_fenced_canonical"
_FENCE = "1" * 64
_BUILD_KEY = "2" * 64
_EVIDENCE_HASH = "3" * 64
_TARGET_HASH = "4" * 64
_LINK_KEY = "5" * 64
_PUBLICATION_KEY = "6" * 64


def _future_build_result(**overrides):
    values = {
        "build_id": 21,
        "build_key": _BUILD_KEY,
        "duplicate": False,
        "status": "partial",
        "strategy_episode_count": 1,
        "position_episode_count": 1,
        "evidence_allocation_count": 0,
        "opening_boundary_policy": "complete_snapshot",
        "snapshot_id": 11,
        "snapshot_key": SNAPSHOT_KEY,
        "fence_key": _FENCE,
        "continuity_policy_version": "position-snapshot-continuity-fence/1.1",
        "target_publication_id": 5,
        "target_publication_key": _PUBLICATION_KEY,
        "target_canonical_set_id": 9,
        "target_canonical_set_sha256": _TARGET_HASH,
        "boundary_at": NOW,
        "source_cutoff_at": NOW + timedelta(minutes=5),
        "projection_name": (
            "position_snapshot_fenced_canonical_projection"
        ),
        "projection_version": "1.0.0",
        "link_id": 1,
        "link_key": _LINK_KEY,
        "activation_changed": False,
        "trading_action_performed": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _future_build_summary(**overrides):
    values = {
        "build_id": 21,
        "build_key": _BUILD_KEY,
        "source_batch_id": 3,
        "source_batch_ids": (2, 3),
        "source_kind": _FUTURE_SOURCE_KIND,
        "canonical_set_id": 9,
        "canonical_set_sha256": _TARGET_HASH,
        "account_key": "default_moomoo_us",
        "status": "partial",
        "builder_name": "signed_position_episode_builder",
        "builder_version": "1.1.0",
        "reconciliation_status": "not_run",
        "reconciliation_scope": "not_run",
        "reconciliation_window_start": None,
        "reconciliation_window_end": None,
        "reconciled_order_count": 0,
        "total_order_count": 0,
        "completeness_score": Decimal("0.5000"),
        "multiplier_amount_tolerance": Decimal("0.005"),
        "max_multiplier_proof_residual": Decimal("0"),
        "opening_boundary_policy": "complete_snapshot",
        "partial_reasons": ("opening_positions_remain_left_censored",),
        "strategy_episode_count": 1,
        "position_episode_count": 1,
        "evidence_allocation_count": 0,
        "unresolved_evidence_count": 0,
        "open_episode_count": 1,
        "closed_episode_count": 0,
        "boundary_unverified_episode_count": 0,
        "left_censored_episode_count": 1,
        "incomplete_episode_count": 1,
        "aggregate_only_episode_count": 0,
        "conditional_closed_episode_count": 0,
        "conditional_realized_pnl_gross": None,
        "conditional_total_fee": None,
        "conditional_realized_pnl_net": None,
        "headline_episode_count": 0,
        "headline_realized_pnl_gross": None,
        "headline_total_fee": None,
        "headline_realized_pnl_net": None,
        "headline_excluded_episode_count": 1,
        "headline_exclusion_counts": {"left_censored": 1},
        "source_event_count": 0,
        "aggregate_order_event_count": 0,
        "detailed_fill_event_count": 0,
        "source_known_fee_total": Decimal("0"),
        "allocated_known_fee_total": Decimal("0"),
        "retained_execution_group_fee_total": Decimal("0"),
        "fee_conservation_by_currency": {},
        "fee_conserved": True,
        "execution_group_count": 0,
        "group_fee_affected_episode_count": 0,
        "leg_fee_attribution_complete": True,
        "source_window_start": NOW,
        "source_cutoff_at": NOW + timedelta(minutes=5),
        "recorded_at": NOW + timedelta(minutes=6),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _future_confirm_request(**overrides):
    request = {
        "expected_fence_key": _FENCE,
        "expected_build_key": _BUILD_KEY,
        "expected_evidence_set_sha256": _EVIDENCE_HASH,
        "accept_left_censored_openings": True,
    }
    request.update(overrides)
    return request


def test_future_episode_build_confirm_appends_and_reads_back(monkeypatch):
    from api.v1.endpoints import journal as journal_endpoint

    # The mapped summary reports no reconcilable orders, so the shared
    # reconciliation helper must not consult live data health.
    monkeypatch.setattr(
        journal_endpoint,
        "get_latest_data_health",
        lambda _key: None,
    )
    captured: dict[str, object] = {}

    def _append(account_key, **kwargs):
        captured["account_key"] = account_key
        captured.update(kwargs)
        return _future_build_result()

    reads = []

    def _read(build_id, account_key):
        reads.append((build_id, account_key))
        return _future_build_summary()

    monkeypatch.setattr(
        journal_positions,
        "append_future_position_episode_build",
        _append,
    )
    monkeypatch.setattr(journal_positions, "get_episode_summary", _read)

    response = _client().post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=_future_confirm_request(),
    )

    assert response.status_code == 200, response.text
    assert captured == {
        "account_key": "default_moomoo_us",
        "expected_fence_key": _FENCE,
        "expected_build_key": _BUILD_KEY,
        "expected_evidence_set_sha256": _EVIDENCE_HASH,
        "accept_left_censored_openings": True,
        "accept_group_fee_scope": False,
    }
    assert reads == [(21, "default_moomoo_us")]
    body = response.json()
    assert body["data_state"] == "ready"
    assert body["duplicate"] is False
    assert body["build"]["id"] == 21
    assert body["build"]["source_kind"] == _FUTURE_SOURCE_KIND
    assert body["build"]["canonical_set_id"] == 9
    assert body["build"]["canonical_set_sha256"] == _TARGET_HASH
    assert body["build"]["opening_boundary_policy"] == "complete_snapshot"
    assert body["build"]["assumed_flat_unverified"] is False
    assert body["snapshot_fence"]["source_kind"] == _FUTURE_SOURCE_KIND
    assert body["snapshot_fence"]["snapshot_id"] == 11
    assert body["snapshot_fence"]["fence_key"] == _FENCE
    assert body["snapshot_fence"]["target_canonical_set_sha256"] == (
        _TARGET_HASH
    )
    assert body["snapshot_fence"]["link_key"] == _LINK_KEY
    assert body["summary"]["left_censored_episode_count"] == 1
    assert body["activation_changed"] is False
    assert body["trading_action_performed"] is False
    assert "default position review remains unchanged" in body["message"]


def test_future_episode_build_confirm_maps_conflicts_to_409(monkeypatch):
    from src.journal.ledger.episode_repository import EpisodeRepositoryError

    def _stale(*_args, **_kwargs):
        raise EpisodeRepositoryError(
            "future build plan changed; request a new preview"
        )

    monkeypatch.setattr(
        journal_positions,
        "append_future_position_episode_build",
        _stale,
    )
    stale = _client().post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=_future_confirm_request(),
    )
    assert stale.status_code == 409
    assert "plan changed" in stale.json()["detail"]

    def _rotated(*_args, **_kwargs):
        raise journal_positions.FuturePositionEpisodePreviewError(
            "position continuity fence changed; request a new preview"
        )

    monkeypatch.setattr(
        journal_positions,
        "append_future_position_episode_build",
        _rotated,
    )
    rotated = _client().post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=_future_confirm_request(),
    )
    assert rotated.status_code == 409
    assert "fence changed" in rotated.json()["detail"]


def test_future_episode_build_confirm_read_back_guard_is_500(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "append_future_position_episode_build",
        lambda *_args, **_kwargs: _future_build_result(),
    )
    monkeypatch.setattr(
        journal_positions,
        "get_episode_summary",
        lambda *_args, **_kwargs: None,
    )
    missing = _client().post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=_future_confirm_request(),
    )
    assert missing.status_code == 500
    assert "cannot be read back" in missing.json()["detail"]

    monkeypatch.setattr(
        journal_positions,
        "get_episode_summary",
        lambda *_args, **_kwargs: _future_build_summary(
            source_kind="csv_batch",
            canonical_set_id=None,
            canonical_set_sha256=None,
        ),
    )
    mismatched = _client().post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=_future_confirm_request(),
    )
    assert mismatched.status_code == 500
    assert "cannot be read back" in mismatched.json()["detail"]


def test_future_episode_build_confirm_validates_request_body(monkeypatch):
    monkeypatch.setattr(
        journal_positions,
        "append_future_position_episode_build",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid request bodies must never reach the append"
        ),
    )
    client = _client()

    uppercase_fence = client.post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=_future_confirm_request(expected_fence_key="A" * 64),
    )
    assert uppercase_fence.status_code == 422

    missing_key = _future_confirm_request()
    missing_key.pop("expected_build_key")
    incomplete = client.post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=missing_key,
    )
    assert incomplete.status_code == 422

    extra_field = client.post(
        "/api/v1/journal/v2/episode-builds/position-snapshot",
        json=_future_confirm_request(activate=True),
    )
    assert extra_field.status_code == 422

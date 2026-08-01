# -*- coding: utf-8 -*-
"""API contracts for the append-only Playbook (contract slice C-2)."""
from __future__ import annotations

import pytest

from src.journal.ledger.episode_repository import (
    append_latest_position_episode_build,
    get_latest_position_episode_page,
)
from src.journal.ledger.repository import import_statement_batch
from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    append_review_annotation,
)
from src.journal.tests.test_episode_repository import _case_focus_statement


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PATH", str(tmp_path / "journal_playbook_api.db")
    )
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from api.v1.endpoints import journal_reviews

    app = FastAPI()
    app.include_router(journal_reviews.router, prefix="/api/v1/journal")
    return TestClient(app)


BASE = "/api/v1/journal/v2/playbook"


def _seed_annotated_bucket() -> int:
    """Statement build with one momentum-tagged annotation."""
    import_statement_batch(_case_focus_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=10)
    episode_id = next(
        item.episode_id for item in page.items if item.underlying == "WIN"
    )
    append_review_annotation(
        ReviewAnnotationInput(
            review_status="completed",
            setup_thesis="动量复盘",
            tags=("momentum",),
        ),
        episode_build_id=build.build_id,
        position_episode_id=episode_id,
    )
    return build.build_id


_BUCKET = {
    "group_kind": "tag",
    "group_value": "momentum",
    "direction": "LONG",
    "boundary_policy": "assumed_or_censored",
}


def _create_candidate(client, **overrides):
    payload = {
        "title": "动量候选",
        "rule_text": "只在计划内的触发条件成立时进场。",
        "source_bucket": _BUCKET,
    }
    payload.update(overrides)
    return client.post(f"{BASE}/candidates", json=payload)


def test_playbook_round_trip_create_promote_retire_contract():
    build_id = _seed_annotated_bucket()
    client = _client()

    empty = client.get(BASE)
    assert empty.status_code == 200
    assert empty.json() == {
        "schema_version": "journal-playbook/1.0",
        "account_key": "default_moomoo_us",
        "candidates": [],
        "rules": [],
    }

    created = _create_candidate(client)
    assert created.status_code == 200
    body = created.json()
    assert body["created"] is True
    assert body["idempotent_replay"] is False
    candidate = body["candidate"]
    assert candidate["schema_version"] == "playbook-candidate/1.0"
    assert candidate["promoted"] is False
    assert candidate["source_bucket"] == _BUCKET
    snapshot = candidate["evidence_snapshot"]
    assert snapshot["snapshot_kind"] == "insights_bucket"
    assert snapshot["build_id"] == build_id
    assert snapshot["counts"]["episode_count"] == 1
    assert snapshot["counts"]["conditional_episode_count"] == 1
    assert len(snapshot["episode_ids"]) == 1
    assert snapshot["thresholds"] == {
        "min_episode_count": 10,
        "min_distinct_trading_day_count": 5,
    }

    replay = _create_candidate(client)
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["candidate"]["id"] == candidate["id"]

    promoted = client.post(
        f"{BASE}/candidates/{candidate['candidate_key']}/promote",
        json={},
    )
    assert promoted.status_code == 200
    rule = promoted.json()["rule"]
    assert promoted.json()["created"] is True
    assert rule["schema_version"] == "playbook-rule/1.0"
    assert rule["version"] == 1
    assert rule["status"] == "active"
    assert rule["previous_rule_id"] is None
    assert rule["is_latest_version"] is True
    assert rule["promoted_from_candidate_key"] == candidate["candidate_key"]
    assert rule["evidence_snapshot"]["captured_for"] == "rule_promotion"

    promote_replay = client.post(
        f"{BASE}/candidates/{candidate['candidate_key']}/promote",
        json={},
    )
    assert promote_replay.status_code == 200
    assert promote_replay.json()["idempotent_replay"] is True
    assert promote_replay.json()["rule"]["id"] == rule["id"]

    listed = client.get(BASE).json()
    assert len(listed["candidates"]) == 1
    assert listed["candidates"][0]["promoted"] is True
    assert len(listed["rules"]) == 1

    retired = client.post(
        f"{BASE}/rules/{rule['lineage_key']}/retire",
        json={"expected_current_version": 1},
    )
    assert retired.status_code == 200
    assert retired.json()["retired"] is True
    assert retired.json()["idempotent_replay"] is False
    retired_rule = retired.json()["rule"]
    assert retired_rule["version"] == 2
    assert retired_rule["status"] == "retired"
    assert retired_rule["previous_rule_id"] == rule["id"]
    # The retired version copies the promoted snapshot verbatim.
    assert retired_rule["evidence_snapshot"] == rule["evidence_snapshot"]

    retire_replay = client.post(
        f"{BASE}/rules/{rule['lineage_key']}/retire",
        json={"expected_current_version": 1},
    )
    assert retire_replay.status_code == 200
    assert retire_replay.json()["retired"] is False
    assert retire_replay.json()["idempotent_replay"] is True

    final = client.get(BASE).json()
    assert [item["version"] for item in final["rules"]] == [2, 1]
    assert final["rules"][0]["is_latest_version"] is True
    assert final["rules"][1]["is_latest_version"] is False


def test_candidate_validation_maps_to_422():
    _seed_annotated_bucket()
    client = _client()

    assert _create_candidate(client, title="   ").status_code == 422
    assert _create_candidate(client, rule_text="").status_code == 422
    assert _create_candidate(client, title="超" * 121).status_code == 422
    assert (
        _create_candidate(
            client,
            source_bucket={**_BUCKET, "group_kind": "setup"},
        ).status_code
        == 422
    )
    assert client.get(BASE).json()["candidates"] == []


def test_conflicts_map_to_409():
    _seed_annotated_bucket()
    client = _client()

    missing_bucket = _create_candidate(
        client,
        source_bucket={**_BUCKET, "group_value": "nonexistent"},
    )
    assert missing_bucket.status_code == 409
    assert "bucket does not exist" in missing_bucket.json()["detail"]

    unknown_promote = client.post(
        f"{BASE}/candidates/{'a' * 64}/promote",
        json={},
    )
    assert unknown_promote.status_code == 409

    candidate = _create_candidate(client).json()["candidate"]
    rule = client.post(
        f"{BASE}/candidates/{candidate['candidate_key']}/promote",
        json={},
    ).json()["rule"]

    stale_retire = client.post(
        f"{BASE}/rules/{rule['lineage_key']}/retire",
        json={"expected_current_version": 7},
    )
    assert stale_retire.status_code == 409

    client.post(
        f"{BASE}/rules/{rule['lineage_key']}/retire",
        json={"expected_current_version": 1},
    )
    repromote_without_intent = client.post(
        f"{BASE}/candidates/{candidate['candidate_key']}/promote",
        json={},
    )
    assert repromote_without_intent.status_code == 409
    assert "new-version intent" in repromote_without_intent.json()["detail"]

    reactivated = client.post(
        f"{BASE}/candidates/{candidate['candidate_key']}/promote",
        json={"allow_new_version": True, "expected_current_version": 2},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["rule"]["version"] == 3
    assert reactivated.json()["rule"]["status"] == "active"

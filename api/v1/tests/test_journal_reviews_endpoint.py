# -*- coding: utf-8 -*-
"""API contracts for append-only PositionEpisode ReviewAnnotation v1."""
from __future__ import annotations

import pytest

from src.journal.ledger.episode_repository import (
    append_latest_position_episode_build,
    get_latest_position_episode_page,
)
from src.journal.ledger.repository import import_statement_batch
from src.journal.tests.test_episode_repository import (
    _case_focus_statement,
    _detailed_statement,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal_reviews_api.db"))
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

    from api.v1.endpoints import journal, journal_reviews

    app = FastAPI()
    app.include_router(journal.router, prefix="/api/v1/journal")
    app.include_router(journal_reviews.router, prefix="/api/v1/journal")
    return TestClient(app)


def _seed_one() -> tuple[int, int]:
    import_statement_batch(_detailed_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=10)
    return build.build_id, page.items[0].episode_id


def _path(episode_id: int, suffix: str = "") -> str:
    return (
        f"/api/v1/journal/v2/position-episodes/{episode_id}"
        f"/review-annotations{suffix}"
    )


def test_latest_history_revision_and_idempotent_response_contract():
    build_id, episode_id = _seed_one()
    client = _client()

    latest_empty = client.get(
        _path(episode_id, "/latest"),
        params={"build_id": build_id},
    )
    assert latest_empty.status_code == 200
    assert latest_empty.json() == {
        "data_state": "not_started",
        "annotation": None,
    }
    history_empty = client.get(
        _path(episode_id),
        params={"build_id": build_id},
    )
    assert history_empty.status_code == 200
    assert history_empty.json() == {
        "data_state": "not_started",
        "total": 0,
        "items": [],
    }

    payload = {
        "build_id": build_id,
        "review_status": "in_progress",
        "setup_thesis": "  回踩 EMA 后恢复  ",
        "entry_trigger": "",
        "invalidation_plan": "",
        "position_rationale": "",
        "exit_reason": "",
        "post_trade_reflection": "",
        "tags": ["momentum", " A+ ", "momentum"],
        "error_types": ["late_entry"],
    }
    created = client.post(_path(episode_id), json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["created"] is True
    assert body["idempotent_replay"] is False
    assert body["annotation"]["revision"] == 1
    assert body["annotation"]["setup_thesis"] == "回踩 EMA 后恢复"
    assert body["annotation"]["tags"] == ["A+", "momentum"]

    duplicate = client.post(
        _path(episode_id),
        json={
            **payload,
            "setup_thesis": "回踩 EMA 后恢复",
            "tags": ["A+", "momentum"],
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["idempotent_replay"] is True
    assert (
        duplicate.json()["annotation"]["id"]
        == body["annotation"]["id"]
    )

    revised = client.post(
        _path(episode_id),
        json={
            **payload,
            "review_status": "completed",
            "setup_thesis": "回踩 EMA 后恢复",
            "exit_reason": "达到计划目标",
            "tags": ["A+", "momentum"],
        },
    )
    assert revised.status_code == 200
    revised_body = revised.json()
    assert revised_body["created"] is True
    assert revised_body["annotation"]["revision"] == 2
    assert (
        revised_body["annotation"]["previous_annotation_id"]
        == body["annotation"]["id"]
    )

    latest = client.get(
        _path(episode_id, "/latest"),
        params={"build_id": build_id},
    )
    assert latest.status_code == 200
    assert latest.json()["data_state"] == "ready"
    assert latest.json()["annotation"]["revision"] == 2

    history = client.get(
        _path(episode_id),
        params={"build_id": build_id},
    )
    assert history.status_code == 200
    assert history.json()["total"] == 2
    assert [item["revision"] for item in history.json()["items"]] == [2, 1]


def test_post_validates_body_limits_scope_and_rejects_model_fields():
    build_id, episode_id = _seed_one()
    client = _client()

    assert client.post(
        _path(episode_id),
        json={"review_status": "in_progress"},
    ).status_code == 422
    assert client.post(
        _path(episode_id),
        json={
            "build_id": build_id,
            "review_status": "completed",
        },
    ).status_code == 422
    assert client.post(
        _path(episode_id),
        json={
            "build_id": build_id,
            "review_status": "in_progress",
            "setup_thesis": "x" * 2_001,
        },
    ).status_code == 422
    assert client.post(
        _path(episode_id),
        json={
            "build_id": build_id,
            "review_status": "in_progress",
            "model_analysis_markdown": "AI output must not be persisted",
        },
    ).status_code == 422

    for wrong_path, wrong_build in (
        (_path(episode_id), build_id + 99),
        (_path(episode_id + 99), build_id),
    ):
        response = client.post(
            wrong_path,
            json={
                "build_id": wrong_build,
                "review_status": "in_progress",
            },
        )
        assert response.status_code == 404

    wrong_account = client.get(
        _path(episode_id, "/latest"),
        params={
            "build_id": build_id,
            "account_key": "another-account",
        },
    )
    assert wrong_account.status_code == 404


def test_review_insights_not_built_state():
    client = _client()

    resp = client.get("/api/v1/journal/v2/review-insights")

    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "journal-review-insights/1.0"
    assert body["data_state"] == "not_built"
    assert body["build_id"] is None
    assert body["build_key"] is None
    assert body["source_kind"] is None
    assert body["generated_at"] is None
    assert body["thresholds"] == {
        "min_episode_count": 10,
        "min_distinct_trading_day_count": 5,
    }
    assert body["total_episode_count"] == 0
    assert body["annotated_episode_count"] == 0
    assert body["unreviewed"] is None
    assert body["buckets"] == []


def test_review_insights_counts_only_below_threshold_contract():
    import_statement_batch(_case_focus_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=10)
    episode_ids = [item.episode_id for item in page.items]
    client = _client()

    saved = client.post(
        _path(episode_ids[1]),
        json={
            "build_id": build.build_id,
            "review_status": "completed",
            "exit_reason": "按计划退出",
            "tags": ["momentum"],
            "error_types": ["late_entry"],
        },
    )
    assert saved.status_code == 200

    resp = client.get("/api/v1/journal/v2/review-insights")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "journal-review-insights/1.0"
    assert body["data_state"] == "ready"
    assert body["build_id"] == build.build_id
    assert body["build_key"] == build.build_key
    assert body["source_kind"] == "csv_batch"
    assert body["generated_at"] is not None
    assert body["total_episode_count"] == 4
    assert body["annotated_episode_count"] == 1
    assert body["unreviewed"] == {
        "episode_count": 3,
        "distinct_trading_day_count": 3,
    }
    assert len(body["buckets"]) == 2
    for bucket in body["buckets"]:
        assert bucket["direction"] == "LONG"
        # Statement builds assume a flat opening, so the boundary bucket and
        # the conditional split both fail closed: counts only, no ratios.
        assert bucket["boundary_policy"] == "assumed_or_censored"
        assert bucket["episode_count"] == 1
        assert bucket["distinct_trading_day_count"] == 1
        assert bucket["review_completed_count"] == 1
        assert bucket["verified_episode_count"] == 0
        assert bucket["conditional_episode_count"] == 1
        assert bucket["stats"] is None
        assert bucket["stats_gate"] == {
            "eligible": False,
            "reason": "no_verified_pnl_episodes",
        }
    assert {
        (bucket["group_kind"], bucket["group_value"])
        for bucket in body["buckets"]
    } == {("tag", "momentum"), ("error_type", "late_entry")}


def test_position_episode_list_projects_queue_and_filters_before_pagination():
    import_statement_batch(_case_focus_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=10)
    episode_ids = [item.episode_id for item in page.items]
    client = _client()

    in_progress = client.post(
        _path(episode_ids[0]),
        json={
            "build_id": build.build_id,
            "review_status": "in_progress",
            "setup_thesis": "待量能确认",
        },
    )
    completed = client.post(
        _path(episode_ids[1]),
        json={
            "build_id": build.build_id,
            "review_status": "completed",
            "exit_reason": "按计划退出",
        },
    )
    assert in_progress.status_code == 200
    assert completed.status_code == 200

    listed = client.get(
        "/api/v1/journal/v2/position-episodes",
        params={"build_id": build.build_id, "per_page": 10},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["review_queue"] == {
        "pending": 2,
        "in_progress": 1,
        "completed": 1,
        "total": 4,
    }
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[episode_ids[0]]["review_status"] == "in_progress"
    assert by_id[episode_ids[0]]["review_revision"] == 1
    assert by_id[episode_ids[0]]["review_updated_at"] is not None
    assert by_id[episode_ids[1]]["review_status"] == "completed"

    filtered = client.get(
        "/api/v1/journal/v2/position-episodes",
        params={
            "build_id": build.build_id,
            "review_status": "not_started",
            "page": 1,
            "per_page": 1,
        },
    )
    assert filtered.status_code == 200
    filtered_body = filtered.json()
    assert filtered_body["total"] == 2
    assert len(filtered_body["items"]) == 1
    assert filtered_body["items"][0]["review_status"] == "not_started"
    # Counts describe the current non-review cohort, not only the selected tab.
    assert filtered_body["review_queue"] == body["review_queue"]

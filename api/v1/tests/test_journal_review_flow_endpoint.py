# -*- coding: utf-8 -*-
"""API contract walk for the guided daily review flow (blueprint 17 Phase A).

Includes the payload-level blind-first lock: before seal + reveal the daily
flow JSON must not contain any P&L field at all.
"""
from __future__ import annotations

import json

import pytest

from src.journal.excursions import (
    EXCURSION_DISCLAIMER,
    ExcursionEpisode,
    compute_excursion,
)
from src.journal.ledger.excursion_repository import append_episode_excursion
from src.journal.ledger.repository import import_statement_batch
from src.journal.ledger.episode_repository import (
    append_latest_position_episode_build,
    get_latest_position_episode_page,
)
from src.journal.tests.test_review_flow import (
    DAY,
    _synthetic_day_statement,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "review_flow_api.db"))
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

    from api.v1.endpoints import journal_review_flow

    app = FastAPI()
    app.include_router(journal_review_flow.router, prefix="/api/v1/journal")
    return TestClient(app)


def _seed() -> tuple[int, dict[str, int]]:
    import_statement_batch(_synthetic_day_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=20)
    ids = {item.raw_symbol[:3]: item.episode_id for item in page.items}
    return build.build_id, ids


BASE = "/api/v1/journal/review-flow/daily"


def _assert_blind(payload: dict) -> None:
    """No P&L of any spelling anywhere in the payload before seal+reveal.

    ``limitations`` 是描述盲评规则本身的元文本（会提到「盈亏」二字），
    不属于数据字段，剔除后再扫。
    """
    stripped = {key: payload[key] for key in payload if key != "limitations"}
    text = json.dumps(stripped, ensure_ascii=False).lower()
    for banned in ("pnl", "profit", "盈亏", "realized"):
        assert banned not in text, f"blind-first violated: {banned!r} in payload"
    assert payload["reveal"] is None


def test_full_daily_walk_s0_to_s4_with_blind_first_and_reveal_order():
    _build_id, ids = _seed()
    client = _client()

    # --- S0/S1：GET 装配，违规待确认，载荷全程无盈亏 ------------------------
    flow = client.get(BASE, params={"date": DAY})
    assert flow.status_code == 200
    body = flow.json()
    assert body["data_state"] == "ready"
    _assert_blind(body)
    violations = [
        item for item in body["episodes_today"] if item["needs_ack"]
    ]
    assert [item["episode_id"] for item in violations] == [ids["BBB"]]
    assert violations[0]["rule_id"] == "V2-C①"
    assert {metric["metric_id"] for metric in body["process_metrics"]} == {
        1, 2, 3, 4, 5, 6, 7,
    }
    assert body["open_positions"][0]["episode_id"] == ids["CCC"]
    assert body["open_positions"][0]["has_exit_plan"] is False
    assert body["mistake_vocabulary"][0] == "freelance_no_push"

    # --- 未确认违规就密封 → 422 -------------------------------------------
    refused = client.post(
        BASE, json={"et_date": DAY, "seal": True}
    )
    assert refused.status_code == 422
    assert str(ids["BBB"]) in refused.json()["detail"]

    # --- S1 违规确认 + S3 出场预登记 + S2 手填 -----------------------------
    saved = client.post(
        BASE,
        json={
            "et_date": DAY,
            "steps": {"s0": True, "s1": True, "s2": True, "s3": True},
            "violation_acks": [
                {
                    "position_episode_id": ids["BBB"],
                    "ack_text": "2DTE 是无聊单，不该开",
                }
            ],
            "exit_plans": [
                {
                    "position_episode_id": ids["CCC"],
                    "plan_text": "跌破上周低点或周四收盘前无进展即离场",
                }
            ],
            "manual_scores": [
                {"metric_id": 1, "value": "missing"},
            ],
        },
    )
    assert saved.status_code == 200
    saved_body = saved.json()
    assert saved_body["created"] is True
    assert saved_body["session"]["revision"] == 1
    assert saved_body["session"]["sealed_at"] is None
    assert saved_body["reveal"] is None
    acks = saved_body["session"]["violation_acks"]
    assert acks[0]["rule_id"] == "V2-C①"

    # 出场预登记已点亮（写入 annotation 修订链）。
    flow2 = client.get(BASE, params={"date": DAY}).json()
    _assert_blind(flow2)
    assert flow2["open_positions"][0]["has_exit_plan"] is True
    assert flow2["episodes_today"][0]["episode_id"]  # 结构完整

    # --- 手填只允许标缺指标 -------------------------------------------------
    rejected = client.post(
        BASE,
        json={
            "et_date": DAY,
            "manual_scores": [{"metric_id": 5, "value": "yes"}],
        },
    )
    assert rejected.status_code == 422

    # --- 揭示必须晚于密封 ---------------------------------------------------
    early_reveal = client.post(
        BASE, json={"et_date": DAY, "seal": True, "reveal": True,
                    "violation_acks": []},
    )
    assert early_reveal.status_code == 422

    # --- S4 封卷 ------------------------------------------------------------
    sealed = client.post(
        BASE,
        json={
            "et_date": DAY,
            "steps": {"s0": True, "s1": True, "s2": True, "s3": True,
                      "s4": True},
            "note": "按流程走完",
            "seal": True,
        },
    )
    assert sealed.status_code == 200
    assert sealed.json()["session"]["sealed_at"] is not None
    assert sealed.json()["reveal"] is None
    # 密封后 GET 仍然盲评（未揭示）。
    _assert_blind(client.get(BASE, params={"date": DAY}).json())

    # --- 自愿揭示：独立修订，顺序入库 ---------------------------------------
    revealed = client.post(
        BASE,
        json={
            "et_date": DAY,
            "steps": {"s0": True, "s1": True, "s2": True, "s3": True,
                      "s4": True},
            "note": "按流程走完",
            "seal": True,
            "reveal": True,
        },
    )
    assert revealed.status_code == 200
    revealed_body = revealed.json()
    assert revealed_body["session"]["revealed_after_seal"] is True
    assert revealed_body["session"]["revealed_at"] is not None
    reveal = revealed_body["reveal"]
    assert reveal is not None
    assert reveal["closed_episode_count"] == 2
    quadrants = {
        item["episode_id"]: item["quadrant"] for item in reveal["episodes"]
    }
    assert quadrants[ids["AAA"]] == "deserved_win"
    assert quadrants[ids["BBB"]] == "undeserved_win"
    assert reveal["total_net"] == pytest.approx(207.4)

    # 揭示后 GET 才携带 reveal。
    final = client.get(BASE, params={"date": DAY}).json()
    assert final["reveal"] is not None
    assert final["session"]["revision"] == 3


def test_reveal_replays_sealed_content_despite_client_drift():
    """复核修复 1：揭示＝服务端重放已密封修订，客户端漂移不能卡死揭示。

    覆盖三个已确认的现实场景：页面重载丢手填分（最小请求）、封卷后另开
    页签写注解（内容漂移）、一步封卷后立刻揭示（指标 3 因会话行翻面）。
    """
    build_id, ids = _seed()
    client = _client()
    steps = {"s0": True, "s1": True, "s2": True, "s3": True, "s4": True}
    sealed = client.post(BASE, json={
        "et_date": DAY,
        "steps": steps,
        "violation_acks": [
            {"position_episode_id": ids["BBB"], "ack_text": "ack"}
        ],
        "manual_scores": [{"metric_id": 1, "value": "yes"}],
        "note": "walked",
        "seal": True,
    })
    assert sealed.status_code == 200, sealed.text
    sealed_session = sealed.json()["session"]

    # 封卷后从另一处写入注解（内容漂移源）。
    from src.journal.ledger.review_repository import (
        ReviewAnnotationInput,
        append_review_annotation,
    )

    append_review_annotation(
        ReviewAnnotationInput(
            review_status="in_progress",
            setup_thesis="sealed 之后从回合页写的",
        ),
        episode_build_id=build_id,
        position_episode_id=ids["AAA"],
    )

    # 最小揭示请求：不带任何内容字段，带链位校验。
    reveal = client.post(BASE, json={
        "et_date": DAY,
        "reveal": True,
        "expected_revision": sealed_session["revision"],
    })
    assert reveal.status_code == 200, reveal.text
    body = reveal.json()
    assert body["session"]["revealed_after_seal"] is True
    assert body["reveal"] is not None
    # 内容逐字重放：手填分原样保留（不因重载丢失）。
    manual = [
        item
        for item in body["session"]["process_scores"]
        if item.get("basis") == "manual"
    ]
    assert manual and manual[0]["metric_id"] == 1
    assert body["session"]["note"] == "walked"

    # 链位不符 → 422 且零写。
    stale = client.post(BASE, json={
        "et_date": DAY, "reveal": True, "expected_revision": 1,
    })
    assert stale.status_code == 422

    # 幂等重放：再次揭示返回既有揭示修订。
    again = client.post(BASE, json={"et_date": DAY, "reveal": True})
    assert again.status_code == 200
    assert again.json()["idempotent_replay"] is True


def test_reveal_without_prior_sealed_revision_still_422():
    _seed()
    client = _client()
    # 无任何修订。
    refused = client.post(BASE, json={"et_date": DAY, "reveal": True})
    assert refused.status_code == 422
    # 有未密封修订同样拒绝（密封与揭示不能同一修订）。
    client.post(BASE, json={"et_date": DAY, "steps": {"s0": True}})
    refused = client.post(BASE, json={"et_date": DAY, "reveal": True})
    assert refused.status_code == 422


def test_rejected_post_writes_nothing():
    """复核修复 8：请求被拒（无效 ack）时出场预登记不落笔。"""
    _build_id, ids = _seed()
    client = _client()
    rejected = client.post(BASE, json={
        "et_date": DAY,
        "violation_acks": [
            # AAA 是合规回合，不需要 ack → 422。
            {"position_episode_id": ids["AAA"], "ack_text": "bogus"}
        ],
        "exit_plans": [
            {"position_episode_id": ids["CCC"], "plan_text": "该被丢弃的计划"}
        ],
    })
    assert rejected.status_code == 422
    flow = client.get(BASE, params={"date": DAY}).json()
    assert flow["open_positions"][0]["has_exit_plan"] is False


def test_rest_day_one_click_confirm():
    _seed()
    client = _client()
    rest_date = "2026-08-21"
    flow = client.get(BASE, params={"date": rest_date}).json()
    assert flow["rest_day_candidate"] is True

    # 有活动的日子谎报休息日 → 422。
    lied = client.post(
        BASE, json={"et_date": DAY, "session_kind": "rest_day"}
    )
    assert lied.status_code == 422

    confirmed = client.post(
        BASE,
        json={"et_date": rest_date, "session_kind": "rest_day", "seal": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["session"]["session_kind"] == "rest_day"

    # 休息纪律连续计数进入下一日的指标。
    next_flow = client.get(BASE, params={"date": "2026-08-24"}).json()
    assert next_flow["rest_streak"] == 1


def test_excursion_endpoint_reports_missing_reason_then_record():
    build_id, ids = _seed()
    client = _client()
    path = f"/api/v1/journal/episodes/{ids['AAA']}/excursion"

    missing = client.get(path)
    assert missing.status_code == 200
    body = missing.json()
    assert body["data_state"] == "missing"
    assert body["excursion"] is None
    assert "标缺" in body["missing_reason"] or "保留窗口" in body["missing_reason"]
    assert body["disclaimer"] == EXCURSION_DISCLAIMER
    assert body["verdict"]["rule_id"] == "V2-A"
    assert body["verdict"]["counterfactual_gate_status"] == "missing"

    # 写入一条 missing_bars 记录（超窗永久标缺）后 → ready 状态原样返回。
    result = compute_excursion(
        ExcursionEpisode(
            asset_type="option",
            direction="long",
            option_right="C",
            opened_at=None,
            closed_at=None,
        ),
        [],
    )
    append_episode_excursion(
        result,
        episode_build_id=build_id,
        position_episode_id=ids["AAA"],
    )
    ready = client.get(path).json()
    assert ready["data_state"] == "ready"
    assert ready["excursion"]["status"] == "not_applicable"

    scatter = client.get("/api/v1/journal/review-flow/excursions").json()
    assert scatter["data_state"] == "ready"
    assert scatter["disclaimer"] == EXCURSION_DISCLAIMER
    assert len(scatter["items"]) == 1

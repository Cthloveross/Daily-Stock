# -*- coding: utf-8 -*-
"""推送台账日 feed 端点契约（蓝图 17 §2.2 Phase B：今日提示流数据源）。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "push_feed_api.db"))
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

    from api.v1.endpoints import journal_push

    app = FastAPI()
    app.include_router(journal_push.router, prefix="/api/v1/journal")
    return TestClient(app)


DAY = "2026-08-25"


def _seed_rows():
    from src.journal.ledger.push_ledger_repository import (
        PushDecisionInput,
        PushDeliveryInput,
        append_push_events,
        new_push_id,
    )

    now = datetime(2026, 8, 25, 14, 31, tzinfo=timezone.utc)
    append_push_events(
        [
            PushDecisionInput(
                push_id=new_push_id(),
                et_date=DAY,
                ts=now,
                tier="P1",
                rule="p1_near_miss",
                ticker="NVDA",
                text="NVDA P0 七条件仅差一条…",
                delivery_channel="telegram_silent",
                dedupe_key=("p1_near_miss", "NVDA", "5"),
                conditions=[{"condition_id": 5, "status": "missing"}],
                option_mid=5.0,
                option_spread_pct=4.0,
                message_key="mk1",
            ),
            PushDecisionInput(
                push_id=new_push_id(),
                et_date=DAY,
                ts=now,
                tier="P3",
                rule="p0_candidate_eval",
                ticker="TSLA",
                text="TSLA P0 候选评估：差 2 条",
                delivery_channel="ledger_only",
                dedupe_key=("p0_eval", "TSLA", "1|5"),
                suppressed=True,
                suppressed_reason="p0_conditions_short:差 2 条",
            ),
            PushDecisionInput(
                push_id=new_push_id(),
                et_date=DAY,
                ts=now,
                tier="P0X",
                session_scope="premarket",
                rule="p0_2m_candidate",
                ticker="META",
                text="META P0-X 盘外异动",
                delivery_channel="telegram",
                dedupe_key=("p0_2m_episode", "META", "up", "09:18"),
                conditions=[
                    {"condition_id": "data_complete", "status": "pass"},
                    {
                        "condition_id": "p0_2m_evidence",
                        "status": "pass",
                        "evidence": {
                            "state": "qualified",
                            "alert_qualified": True,
                            "alert_tier": "P0X",
                            "signal_version": "p0_2m_signal_v1",
                            "symbol": "META",
                            "universe_hash": "sha256:test-universe",
                            "session_scope": "premarket",
                            "window_end": "2026-08-25T09:18:00-04:00",
                            "direction": "up",
                            "price_percentile": 0.983333,
                            "rvol_8m": 1.8,
                            "displacement_atr": 0.6,
                            "baseline_count": 60,
                            "source": "MoomooFetcher",
                            "data_quality": {"status": "complete", "issues": []},
                            "option_candidates": [],
                            "failed_checks": [],
                            "thresholds": {},
                            "basis": "same-clock strict 2m",
                            "limitations": [],
                        },
                    },
                ],
                message_key="mk-p0x",
            ),
        ],
        [
            PushDeliveryInput(
                push_id=new_push_id(),
                et_date=DAY,
                ts=now,
                tier="P1",
                message_key="mk1",
                telegram_ok=True,
                text="【P1 预备级 · 静默 · 10:31 ET】",
                channel="telegram_silent",
            ),
            PushDeliveryInput(
                push_id=new_push_id(),
                et_date=DAY,
                ts=now,
                tier="P0X",
                message_key="mk-p0x",
                telegram_ok=True,
                text="【P0-X 盘外异动】",
                channel="telegram",
            ),
        ],
    )


def test_daily_feed_contract_events_budgets_and_pass_rate():
    _seed_rows()
    response = _client().get(
        "/api/v1/journal/push-ledger/daily", params={"date": DAY}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["et_date"] == DAY

    kinds = [(e["record_kind"], e["tier"]) for e in body["events"]]
    assert ("decision", "P1") in kinds
    assert ("decision", "P3") in kinds
    assert ("delivery", "P1") in kinds
    assert ("decision", "P0X") in kinds
    p0x = next(
        event
        for event in body["events"]
        if event["record_kind"] == "decision" and event["tier"] == "P0X"
    )
    assert p0x["session_scope"] == "premarket"
    typed_evidence = p0x["p0_signal_evidence_v2"]
    assert typed_evidence["signal_version"] == "p0_2m_signal_v1"
    assert typed_evidence["price_percentile"] == pytest.approx(0.983333)
    assert typed_evidence["data_quality"] == {
        "status": "complete",
        "issues": [],
    }

    p1 = next(
        e
        for e in body["events"]
        if e["record_kind"] == "decision" and e["tier"] == "P1"
    )
    assert p1["option_mid"] == 5.0
    assert p1["option_spread_pct"] == 4.0
    assert p1["telegram_ok"] is None  # immutable decision is not rewritten
    assert p1["delivery_status"] == "succeeded"
    assert p0x["delivery_status"] == "succeeded"

    suppressed = next(e for e in body["events"] if e["tier"] == "P3")
    assert suppressed["suppressed"] is True
    assert suppressed["suppressed_reason"].startswith("p0_conditions_short")

    budgets = body["budgets"]
    assert budgets["p0_daily_budget"] == 2
    assert budgets["opening_daily_budget"] == 3
    assert budgets["opening_used_today"] == 0
    assert budgets["p0_weekly_budget"] == 7
    assert budgets["p1_daily_budget"] == 3
    assert budgets["p1_used_today"] == 1
    assert budgets["p0_used_today"] == 0
    assert budgets["telegram_lines_used"] == 2
    assert budgets["p0x_used_today_by_session"] == {
        "premarket": 1,
        "regular": 0,
        "afterhours": 0,
    }
    assert budgets["p0x_daily_budget_by_session"] == {
        "premarket": 1,
        "regular": 0,
        "afterhours": 1,
    }
    assert budgets["p0x_used_week"] == 1
    assert budgets["p0x_weekly_budget"] == 5
    assert budgets["research_rth_used_today"] == 0
    assert budgets["research_rth_daily_budget"] == 2
    assert budgets["research_rth_weekly_budget"] == 7
    assert budgets["research_extended_weekly_budget"] == 5

    pass_rate = body["p0_pass_rate"]
    assert pass_rate["status"] == "missing"  # 尚无已投递 P0
    assert pass_rate["auto_tighten_enabled"] is False
    assert pass_rate["threshold"] == 0.70
    assert any("机械代理" in item for item in pass_rate["limitations"])

    assert any("无静默丢弃" in item for item in body["limitations"])
    assert any("事实描述，非买卖信号" in item for item in body["limitations"])


def test_daily_feed_defaults_to_today_and_validates_date():
    client = _client()
    assert client.get("/api/v1/journal/push-ledger/daily").status_code == 200
    response = client.get(
        "/api/v1/journal/push-ledger/daily", params={"date": "bad"}
    )
    assert response.status_code == 422
    impossible = client.get(
        "/api/v1/journal/push-ledger/daily", params={"date": "2026-99-99"}
    )
    assert impossible.status_code == 422


def test_historical_daily_feed_pass_rate_excludes_later_p0():
    from src.journal.ledger.push_ledger_repository import (
        PushDecisionInput,
        PushDeliveryInput,
        append_push_events,
        new_push_id,
    )

    decisions = []
    deliveries = []
    for et_date, ticker, day in (
        ("2026-08-25", "NVDA", 25),
        ("2026-08-26", "TSLA", 26),
    ):
        message_key = new_push_id()
        decision_at = datetime(
            2026, 8, day, 14, 30, tzinfo=timezone.utc
        )
        decisions.append(
            PushDecisionInput(
                push_id=new_push_id(),
                et_date=et_date,
                ts=decision_at,
                tier="P0",
                rule="p0_2m_candidate",
                ticker=ticker,
                text=f"{ticker} P0",
                delivery_channel="telegram",
                dedupe_key=("p0", ticker),
                message_key=message_key,
            )
        )
        deliveries.append(
            PushDeliveryInput(
                push_id=new_push_id(),
                et_date=et_date,
                ts=decision_at,
                tier="P0",
                message_key=message_key,
                telegram_ok=True,
                channel="telegram",
            )
        )
    append_push_events(decisions, deliveries)

    response = _client().get(
        "/api/v1/journal/push-ledger/daily",
        params={"date": "2026-08-25"},
    )

    assert response.status_code == 200
    # 无 EpisodeBuild，率本身标缺；delivered 仍须精确反映历史上界内仅一条，
    # 不能把 8/26 的未来 P0 混入 8/25 响应。
    pass_rate = response.json()["p0_pass_rate"]
    assert pass_rate["status"] == "missing"
    assert pass_rate["delivered"] == 1
    assert "无 EpisodeBuild" in pass_rate["reason"]


def test_push_api_schema_accepts_p0x_and_canonical_session_scope():
    from pydantic import ValidationError

    from api.v1.schemas.journal_push import PushLedgerEventModel

    event = PushLedgerEventModel(
        push_id="p0x-event",
        record_kind="decision",
        et_date=DAY,
        ts="2026-08-25T08:45:00-04:00",
        tier="P0X",
        session_scope="premarket",
        rule="p0x_extended_hours",
        ticker="NVDA",
        text="盘前证据",
        delivery_channel="telegram",
        suppressed=False,
    )
    assert event.tier == "P0X"
    assert event.session_scope == "premarket"

    with pytest.raises(ValidationError):
        PushLedgerEventModel(
            push_id="bad-session",
            record_kind="decision",
            et_date=DAY,
            ts="2026-08-25T16:45:00-04:00",
            tier="P0X",
            session_scope="postmarket",
            rule="p0x_extended_hours",
            ticker="NVDA",
            text="非 canonical 盘段",
            delivery_channel="telegram",
            suppressed=False,
        )


@pytest.mark.parametrize(
    "outbox_state,expired,receipts,expected",
    [
        (None, False, [], "indeterminate"),
        ("pending", False, [], "pending"),
        ("pending", True, [], "expired_unconfirmed"),
        ("terminal", False, [], "indeterminate"),
        ("succeeded", False, [], "succeeded"),
        ("terminal", False, [False], "failed"),
        (None, False, [False], "failed"),
        (None, False, [False, True, False], "succeeded"),
        ("pending", False, [False], "pending"),
        ("pending", True, [True], "succeeded"),
    ],
)
def test_delivery_projection_joins_receipts_without_mutating_decision(
    outbox_state, expired, receipts, expected,
):
    from datetime import timedelta

    from src.journal.ledger.push_ledger_repository import (
        PushDecisionInput,
        PushDeliveryInput,
        append_push_events,
        new_push_id,
    )
    from src.journal.ledger.telegram_outbox_repository import (
        mark_telegram_outbox_success,
        mark_telegram_outbox_terminal,
        upsert_pending_telegram_outbox,
    )

    now = datetime.now(timezone.utc)
    key = "research-message"
    append_push_events(
        [PushDecisionInput(
            push_id=new_push_id(), et_date=DAY, ts=now, tier="P2",
            rule="p0_2m_shadow_research", ticker="NVDA", text="研究提醒",
            delivery_channel="telegram", message_key=key,
            session_scope="regular",
        )],
        [PushDeliveryInput(
            push_id=new_push_id(), et_date=DAY, ts=now, tier="P2",
            message_key=key, telegram_ok=receipt,
        ) for receipt in receipts],
    )
    if outbox_state is not None:
        upsert_pending_telegram_outbox(
            message_key=key, et_date=DAY, tier="P2", text="研究提醒",
            silent=False,
            expires_at=now + timedelta(minutes=-1 if expired else 1),
            next_attempt_at=now, now=now,
        )
        if outbox_state == "terminal":
            mark_telegram_outbox_terminal(key, now=now)
        elif outbox_state == "succeeded":
            mark_telegram_outbox_success(key, now=now)

    body = _client().get(
        "/api/v1/journal/push-ledger/daily", params={"date": DAY},
    ).json()
    decision = next(row for row in body["events"] if row["record_kind"] == "decision")
    assert decision["telegram_ok"] is None
    assert decision["delivery_status"] == expected
    assert len(body["events"]) == 1 + len(receipts)


def test_research_budget_is_separate_from_formal_p0_and_split_by_session():
    from src.journal.ledger.push_ledger_repository import (
        PushDecisionInput, PushDeliveryInput, append_push_events, new_push_id,
    )

    now = datetime(2026, 8, 25, 14, 31, tzinfo=timezone.utc)
    decisions = []
    deliveries = []
    for day, scope in [
        (DAY, "regular"), (DAY, "premarket"), (DAY, "afterhours"),
        ("2026-08-24", "regular"),
    ]:
        key = f"research-{day}-{scope}"
        decisions.append(PushDecisionInput(
            push_id=new_push_id(), et_date=day, ts=now, tier="P2",
            rule="p0_2m_shadow_research", ticker="NVDA", text="研究提醒",
            delivery_channel="telegram", message_key=key, session_scope=scope,
        ))
        deliveries.append(PushDeliveryInput(
            push_id=new_push_id(), et_date=day, ts=now, tier="P2",
            message_key=key, telegram_ok=True,
        ))
    append_push_events(decisions, deliveries)
    body = _client().get(
        "/api/v1/journal/push-ledger/daily", params={"date": DAY},
    ).json()
    budgets = body["budgets"]
    assert budgets["p0_used_today"] == budgets["p0_used_week"] == 0
    assert budgets["p0x_used_week"] == 0
    assert budgets["research_rth_used_today"] == 1
    assert budgets["research_rth_used_week"] == 2
    assert budgets["research_extended_used_today_by_session"] == {
        "premarket": 1, "regular": 0, "afterhours": 1,
    }
    assert budgets["research_extended_daily_budget_by_session"] == {
        "premarket": 1, "regular": 0, "afterhours": 1,
    }
    assert budgets["research_extended_used_week"] == 2
    assert body["p0_pass_rate"]["delivered"] == 0


def test_page_and_suppressed_decisions_are_not_requested():
    from src.journal.ledger.push_ledger_repository import (
        PushDecisionInput, append_push_events, new_push_id,
    )

    now = datetime(2026, 8, 25, 14, 31, tzinfo=timezone.utc)
    append_push_events([
        PushDecisionInput(
            push_id=new_push_id(), et_date=DAY, ts=now, tier="P2",
            rule="p0_2m_candidate", ticker="NVDA", text="页面候选",
            delivery_channel="web_only",
        ),
        PushDecisionInput(
            push_id=new_push_id(), et_date=DAY, ts=now, tier="P2",
            rule="p0_2m_shadow_research", ticker="TSLA", text="预算抑制",
            delivery_channel="telegram", suppressed=True,
            suppressed_reason="budget_exhausted", message_key="suppressed",
        ),
    ])
    body = _client().get(
        "/api/v1/journal/push-ledger/daily", params={"date": DAY},
    ).json()
    assert all(row["delivery_status"] == "not_requested" for row in body["events"])

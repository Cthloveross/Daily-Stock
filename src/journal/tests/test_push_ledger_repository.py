# -*- coding: utf-8 -*-
"""推送台账仓库（蓝图 17 §三(e)-4，Phase B）：append-only + 重启状态重建。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from src.journal.ledger.push_ledger_repository import (
    PushDecisionInput,
    PushDeliveryInput,
    PushLedgerError,
    P0_2M_SHADOW_RESEARCH_RULE,
    append_push_events,
    count_delivered_p0_2m_shadow_research_since,
    count_delivered_p0_since,
    count_delivered_p0x_since,
    list_push_events,
    load_push_day_state,
    new_push_id,
)
from src.journal.ledger.repository import init_ledger_schema
from src.storage import get_db

DAY = "2026-08-25"
NOW = datetime(2026, 8, 25, 14, 31, tzinfo=timezone.utc)


def _decision(**overrides) -> PushDecisionInput:
    base = dict(
        push_id=new_push_id(),
        et_date=DAY,
        ts=NOW,
        tier="P2",
        rule="volume_burst",
        ticker="NVDA",
        text="NVDA 放量 2.3×",
        delivery_channel="web_only",
        dedupe_key=("volume_burst", "NVDA"),
    )
    base.update(overrides)
    return PushDecisionInput(**base)


def _delivery(
    message_key: str,
    *,
    et_date: str = DAY,
    tier: str = "P0",
    ok: bool = True,
    channel: str = "telegram",
    ts: datetime = NOW,
) -> PushDeliveryInput:
    return PushDeliveryInput(
        push_id=new_push_id(),
        et_date=et_date,
        ts=ts,
        tier=tier,
        message_key=message_key,
        telegram_ok=ok,
        text=f"【{tier}】",
        channel=channel,
    )


def test_append_and_list_round_trip_with_conditions():
    conditions = [{"condition_id": 1, "status": "pass", "detail": "在册"}]
    append_push_events(
        [
            _decision(
                tier="P0",
                rule="p0_candidate",
                delivery_channel="telegram",
                dedupe_key=("p0", "NVDA"),
                conditions=conditions,
                gate_state="missing",
                lane_rule_id="V2-B",
                option_mid=5.0,
                option_spread_pct=4.0,
                message_key="mk1",
            )
        ],
        [
            PushDeliveryInput(
                push_id=new_push_id(),
                et_date=DAY,
                ts=NOW,
                tier="P0",
                message_key="mk1",
                telegram_ok=False,
                text="【P0 行动级】",
            )
        ],
    )
    events = list_push_events(DAY)
    assert [e.record_kind for e in events] == ["decision", "delivery"]
    decision, delivery = events
    assert decision.tier == "P0"
    assert decision.dedupe_key == ("p0", "NVDA")
    assert decision.conditions == (conditions[0],)
    assert decision.gate_state == "missing"
    assert decision.lane_rule_id == "V2-B"
    assert decision.option_mid == 5.0
    assert decision.option_spread_pct == 4.0
    assert delivery.telegram_ok is False
    assert delivery.message_key == "mk1"


def test_rows_are_append_only_update_delete_denied():
    append_push_events([_decision()])
    init_ledger_schema()
    db = get_db()
    for statement in (
        "UPDATE journal_v2_push_events SET tier = 'P0'",
        "DELETE FROM journal_v2_push_events",
    ):
        with pytest.raises(Exception) as excinfo:
            with db.session_scope() as session:
                session.execute(text(statement))
        assert "append-only" in str(excinfo.value)


def test_suppressed_decision_requires_reason():
    with pytest.raises(PushLedgerError):
        append_push_events([_decision(suppressed=True, suppressed_reason=None)])
    # 表级 CheckConstraint 双保险由模型定义；仓库层先行拒绝。
    append_push_events(
        [_decision(suppressed=True, suppressed_reason="daily_cap_reached")]
    )
    stored = list_push_events(DAY)[-1]
    assert stored.suppressed and stored.suppressed_reason == "daily_cap_reached"


def test_invalid_enum_values_rejected():
    with pytest.raises(PushLedgerError):
        append_push_events([_decision(tier="P9")])
    with pytest.raises(PushLedgerError):
        append_push_events([_decision(delivery_channel="carrier_pigeon")])
    with pytest.raises(PushLedgerError):
        append_push_events([_decision(et_date="not-a-date")])
    with pytest.raises(PushLedgerError, match="unknown session_scope"):
        append_push_events([_decision(session_scope="postmarket")])
    with pytest.raises(PushLedgerError, match="P0X decisions require"):
        append_push_events([_decision(tier="P0X", session_scope=None)])
    with pytest.raises(PushLedgerError, match="P0X decisions require"):
        append_push_events([_decision(tier="P0X", session_scope="regular")])


def test_load_push_day_state_rebuilds_dedupe_and_budgets():
    p0_key = new_push_id()
    p1_key = new_push_id()
    opening_key = new_push_id()
    opening_update_key = new_push_id()
    append_push_events(
        [
            # 已投递 P0（计预算 + 全局行计数）。
            _decision(
                tier="P0",
                rule="p0_candidate",
                delivery_channel="telegram",
                dedupe_key=("p0", "NVDA"),
                message_key=p0_key,
            ),
            # 已投递 P1 静默。
            _decision(
                tier="P1",
                rule="p1_near_miss",
                ticker="TSLA",
                delivery_channel="telegram_silent",
                dedupe_key=("p1_near_miss", "TSLA", "5"),
                message_key=p1_key,
            ),
            # P1-O 响铃使用独立预算，不挤占普通 P1。
            _decision(
                tier="P1",
                rule="opening_impulse_v1",
                ticker="AMD",
                delivery_channel="telegram",
                dedupe_key=("opening_impulse_v1", "AMD", "09:30"),
                message_key=opening_key,
            ),
            # P1-O 合约补充占全局行数，但不占普通 P1 near-miss 预算。
            _decision(
                tier="P1",
                rule="opening_option_update",
                ticker="AMD",
                delivery_channel="telegram_silent",
                dedupe_key=("opening_option_update", "AMD"),
                message_key=opening_update_key,
            ),
            # 风险提醒会送达并参与去重，但不消耗机会提示预算。
            _decision(
                tier="P1",
                rule="same_day_flatten_reminder",
                ticker=None,
                delivery_channel="telegram_silent",
                dedupe_key=("same_day_flatten_reminder",),
            ),
            _decision(
                tier="P1",
                rule="r6_fuse_notice",
                ticker=None,
                delivery_channel="telegram_silent",
                dedupe_key=("r6_fuse_notice",),
            ),
            # 被抑制的 P0：键计入 seen，但不计预算。
            _decision(
                tier="P0",
                rule="p0_candidate",
                ticker="MRVL",
                delivery_channel="telegram",
                dedupe_key=("p0", "MRVL"),
                suppressed=True,
                suppressed_reason="daily_cap_reached",
            ),
            # P2 页面行：键计入 seen，不占 telegram 行。
            _decision(dedupe_key=("volume_burst", "NVDA")),
            # 系统通知不占任何预算。
            _decision(
                tier="system",
                rule="system_notice",
                ticker=None,
                delivery_channel="telegram",
                dedupe_key=(),
            ),
        ],
        [
            _delivery(p0_key),
            _delivery(
                p1_key,
                tier="P1",
                channel="telegram_silent",
            ),
            _delivery(
                opening_key,
                tier="P1",
            ),
            _delivery(
                opening_update_key,
                tier="P1",
                channel="telegram_silent",
            ),
        ],
    )
    state = load_push_day_state(DAY)
    assert ("p0", "NVDA") in state.seen_keys
    assert ("p0", "MRVL") in state.seen_keys
    assert ("p1_near_miss", "TSLA", "5") in state.seen_keys
    assert ("opening_impulse_v1", "AMD", "09:30") in state.seen_keys
    assert ("opening_option_update", "AMD") in state.seen_keys
    assert ("opening_option_pending", "AMD") not in state.seen_keys
    assert ("same_day_flatten_reminder",) in state.seen_keys
    assert ("r6_fuse_notice",) in state.seen_keys
    assert ("volume_burst", "NVDA") in state.seen_keys
    assert state.p0_delivered == 1
    assert state.p1_delivered == 1
    assert state.opening_delivered == 1
    assert state.telegram_alert_count == 4  # P0 + P1 + P1-O + 合约补充


def test_failed_delivery_releases_budget_but_unresolved_decision_is_conservative():
    failed_p0_key = new_push_id()
    failed_p1_key = new_push_id()
    orphan_opening_key = new_push_id()
    append_push_events(
        [
            _decision(
                tier="P0",
                rule="p0_candidate",
                ticker="NVDA",
                delivery_channel="telegram",
                dedupe_key=("p0", "NVDA"),
                message_key=failed_p0_key,
            ),
            _decision(
                tier="P1",
                rule="p1_near_miss",
                ticker="TSLA",
                delivery_channel="telegram_silent",
                dedupe_key=("p1_near_miss", "TSLA", "5"),
                message_key=failed_p1_key,
            ),
            _decision(
                tier="P1",
                rule="opening_impulse_v1",
                ticker="AMD",
                delivery_channel="telegram",
                dedupe_key=("opening_impulse_v1", "AMD", "09:30"),
                message_key=orphan_opening_key,
            ),
        ],
        [
            _delivery(failed_p0_key, ok=False),
            _delivery(
                failed_p1_key,
                tier="P1",
                ok=False,
                channel="telegram_silent",
            ),
        ],
    )

    state = load_push_day_state(DAY)

    assert ("p0", "NVDA") in state.seen_keys
    assert ("p1_near_miss", "TSLA", "5") in state.seen_keys
    assert ("opening_impulse_v1", "AMD", "09:30") in state.seen_keys
    assert state.p0_delivered == 0
    assert state.p1_delivered == 0
    # 没有 delivery 行无法区分“发送前崩溃”和“发送成功后落库崩溃”；重启时
    # 必须保守占用一次，避免外部已送达却把预算重新释放。
    assert state.opening_delivered == 1
    assert state.telegram_alert_count == 1


def test_unresolved_p0_and_p0x_consume_daily_and_trailing_budgets():
    pending_p0_key = new_push_id()
    pending_p0x_key = new_push_id()
    append_push_events(
        [
            _decision(
                tier="P0",
                rule="p0_2m_candidate",
                ticker="NVDA",
                session_scope="regular",
                delivery_channel="telegram",
                dedupe_key=("p0_2m_episode", "NVDA", "up", "10:48"),
                message_key=pending_p0_key,
            ),
            _decision(
                tier="P0X",
                rule="p0_2m_candidate",
                ticker="TSLA",
                session_scope="premarket",
                delivery_channel="telegram",
                dedupe_key=("p0_2m_episode", "TSLA", "down", "08:18"),
                message_key=pending_p0x_key,
            ),
        ]
    )

    state = load_push_day_state(DAY)

    assert state.p0_delivered == 1
    assert state.p0x_delivered == 1
    assert state.p0x_delivered_by_session["premarket"] == 1
    assert state.telegram_alert_count == 2
    assert count_delivered_p0_since(DAY, days=7) == 1
    assert count_delivered_p0x_since(DAY, days=7) == 1


def test_count_delivered_p0_since_trailing_window():
    aaa_key = new_push_id()
    bbb_key = new_push_id()
    failed_key = new_push_id()
    append_push_events(
        [
            _decision(
                et_date="2026-08-20",
                tier="P0",
                delivery_channel="telegram",
                dedupe_key=("p0", "AAA"),
                message_key=aaa_key,
            ),
            _decision(
                et_date="2026-08-24",
                tier="P0",
                delivery_channel="telegram",
                dedupe_key=("p0", "BBB"),
                message_key=bbb_key,
            ),
            # 失败的 P0 保留去重事实，但不占周预算。
            _decision(
                et_date="2026-08-24",
                tier="P0",
                delivery_channel="telegram",
                dedupe_key=("p0", "DDD"),
                message_key=failed_key,
            ),
            # 抑制的 P0 不计。
            _decision(
                et_date="2026-08-24",
                tier="P0",
                delivery_channel="telegram",
                dedupe_key=("p0", "CCC"),
                suppressed=True,
                suppressed_reason="daily_cap_reached",
            ),
        ],
        [
            _delivery(aaa_key, et_date="2026-08-20"),
            _delivery(bbb_key, et_date="2026-08-24"),
            _delivery(failed_key, et_date="2026-08-24", ok=False),
        ],
    )
    # 8/19-8/25 窗口含两条；8/26 起 7 天窗仍含 8/20。
    assert count_delivered_p0_since("2026-08-25", days=7) == 2
    assert count_delivered_p0_since("2026-08-26", days=7) == 2
    assert count_delivered_p0_since("2026-08-31", days=7) == 0


def test_shadow_research_budget_is_restart_safe_and_formal_p0_isolated():
    rth_key = new_push_id()
    premarket_key = new_push_id()
    failed_key = new_push_id()
    append_push_events(
        [
            _decision(
                tier="P0",
                rule="p0_2m_candidate",
                session_scope="regular",
                delivery_channel="web_only",
                suppressed=True,
                suppressed_reason="p0_2m_shadow:action_switch_disabled",
                dedupe_key=("p0_2m_episode", "NVDA", "up", "10:48"),
            ),
            _decision(
                tier="P2",
                rule=P0_2M_SHADOW_RESEARCH_RULE,
                session_scope="regular",
                delivery_channel="telegram",
                message_key=rth_key,
                dedupe_key=(
                    P0_2M_SHADOW_RESEARCH_RULE,
                    "NVDA",
                    "up",
                    "10:48",
                ),
            ),
            # No delivery row is conservatively occupied across restart.
            _decision(
                tier="P2",
                rule=P0_2M_SHADOW_RESEARCH_RULE,
                ticker="TSLA",
                session_scope="premarket",
                delivery_channel="telegram",
                message_key=premarket_key,
                dedupe_key=(
                    P0_2M_SHADOW_RESEARCH_RULE,
                    "TSLA",
                    "down",
                    "08:18",
                ),
            ),
            _decision(
                tier="P2",
                rule=P0_2M_SHADOW_RESEARCH_RULE,
                ticker="META",
                session_scope="afterhours",
                delivery_channel="telegram",
                message_key=failed_key,
                dedupe_key=(
                    P0_2M_SHADOW_RESEARCH_RULE,
                    "META",
                    "up",
                    "16:18",
                ),
            ),
        ],
        [
            _delivery(rth_key, tier="P2"),
            _delivery(failed_key, tier="P2", ok=False),
        ],
    )

    state = load_push_day_state(DAY)

    assert state.p0_2m_shadow_research_delivered == 1
    assert state.p0x_2m_shadow_research_delivered_by_session == {
        "premarket": 1,
        "regular": 0,
        "afterhours": 0,
    }
    assert state.telegram_alert_count == 2
    assert state.p0_delivered == 0
    assert state.p0x_delivered == 0
    assert count_delivered_p0_since(DAY) == 0
    assert count_delivered_p0x_since(DAY) == 0
    assert count_delivered_p0_2m_shadow_research_since(
        DAY, session_scope="regular"
    ) == 1
    assert count_delivered_p0_2m_shadow_research_since(
        DAY, session_scope="extended"
    ) == 1
    with pytest.raises(PushLedgerError, match="session_scope"):
        count_delivered_p0_2m_shadow_research_since(
            DAY, session_scope="premarket"
        )


def test_p0x_round_trip_day_sessions_and_week_count_are_independent():
    prior_after_key = new_push_id()
    premarket_key = new_push_id()
    afterhours_key = new_push_id()
    regular_p0_key = new_push_id()
    prior_after_at = datetime(2026, 8, 24, 21, 10, tzinfo=timezone.utc)
    premarket_at = datetime(2026, 8, 25, 12, 45, tzinfo=timezone.utc)
    afterhours_at = datetime(2026, 8, 25, 21, 10, tzinfo=timezone.utc)
    append_push_events(
        [
            _decision(
                et_date="2026-08-24",
                ts=prior_after_at,
                tier="P0X",
                rule="p0x_extended_hours",
                session_scope="afterhours",
                delivery_channel="telegram",
                dedupe_key=("p0x", "afterhours", "AAPL"),
                message_key=prior_after_key,
            ),
            _decision(
                ts=premarket_at,
                tier="P0X",
                rule="p0x_extended_hours",
                session_scope="premarket",
                delivery_channel="telegram",
                dedupe_key=("p0x", "premarket", "NVDA"),
                message_key=premarket_key,
            ),
            _decision(
                ts=afterhours_at,
                tier="P0X",
                rule="p0x_extended_hours",
                session_scope="afterhours",
                delivery_channel="telegram_silent",
                dedupe_key=("p0x", "afterhours", "TSLA"),
                message_key=afterhours_key,
            ),
            _decision(
                tier="P0",
                rule="p0_candidate",
                session_scope="regular",
                delivery_channel="telegram",
                dedupe_key=("p0", "META"),
                message_key=regular_p0_key,
            ),
        ],
        [
            _delivery(
                prior_after_key,
                et_date="2026-08-24",
                tier="P0X",
                ts=prior_after_at,
            ),
            _delivery(premarket_key, tier="P0X", ts=premarket_at),
            _delivery(
                afterhours_key,
                tier="P0X",
                channel="telegram_silent",
                ts=afterhours_at,
            ),
            _delivery(regular_p0_key, tier="P0"),
        ],
    )

    today_events = list_push_events(DAY)
    premarket = next(
        event
        for event in today_events
        if event.record_kind == "decision" and event.tier == "P0X"
        and event.session_scope == "premarket"
    )
    assert premarket.dedupe_key == ("p0x", "premarket", "NVDA")

    state = load_push_day_state(DAY)
    assert state.p0x_delivered == 2
    assert state.p0x_delivered_by_session == {
        "premarket": 1,
        "regular": 0,
        "afterhours": 1,
    }
    assert state.p0_delivered == 1
    assert state.telegram_alert_count == 3
    assert count_delivered_p0x_since(DAY, days=7) == 3
    assert count_delivered_p0_since(DAY, days=7) == 1
    assert count_delivered_p0x_since(DAY, days=0) == 0


def test_push_schema_migration_preserves_rows_and_accepts_p0x(
    isolated_sqlite,
):
    """The old tier CHECK is rebuilt once without rewriting ledger facts."""
    with sqlite3.connect(isolated_sqlite) as connection:
        connection.executescript(
            """
            CREATE TABLE journal_v2_push_events (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                push_id VARCHAR(32) NOT NULL,
                record_kind VARCHAR(16) NOT NULL,
                et_date VARCHAR(10) NOT NULL,
                ts DATETIME NOT NULL,
                tier VARCHAR(8) NOT NULL,
                rule VARCHAR(64) NOT NULL,
                ticker VARCHAR(16),
                text TEXT NOT NULL,
                dedupe_key_json TEXT,
                conditions_json TEXT,
                lane_rule_id VARCHAR(16),
                playbook_ref VARCHAR(64),
                option_mid NUMERIC(18, 6),
                option_spread_pct NUMERIC(10, 4),
                gate_state VARCHAR(16),
                delivery_channel VARCHAR(24) NOT NULL,
                suppressed BOOLEAN NOT NULL,
                suppressed_reason VARCHAR(128),
                message_key VARCHAR(32),
                telegram_ok BOOLEAN,
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_jv2_push_event_push_id UNIQUE (push_id),
                CONSTRAINT ck_jv2_push_event_kind
                    CHECK (record_kind IN ('decision', 'delivery')),
                CONSTRAINT ck_jv2_push_event_tier
                    CHECK (tier IN ('P0', 'P1', 'P2', 'P3', 'system')),
                CONSTRAINT ck_jv2_push_event_channel CHECK (
                    delivery_channel IN (
                        'telegram', 'telegram_silent', 'web_only',
                        'ledger_only', 'none'
                    )
                ),
                CONSTRAINT ck_jv2_push_event_suppressed_reason
                    CHECK (suppressed = 0 OR suppressed_reason IS NOT NULL)
            );
            CREATE INDEX ix_jv2_push_events_scope ON
                journal_v2_push_events (et_date, record_kind, tier);
            CREATE INDEX ix_jv2_push_events_ticker ON
                journal_v2_push_events (et_date, ticker);
            INSERT INTO journal_v2_push_events (
                id, push_id, record_kind, et_date, ts, tier, rule, ticker,
                text, dedupe_key_json, conditions_json, lane_rule_id,
                playbook_ref, option_mid, option_spread_pct, gate_state,
                delivery_channel, suppressed, suppressed_reason, message_key,
                telegram_ok, created_at
            ) VALUES (
                7, 'legacy-push', 'decision', '2026-08-25',
                '2026-08-25 14:31:00', 'P2', 'volume_burst', 'NVDA',
                'legacy fact', '["volume_burst", "NVDA"]', NULL, NULL,
                NULL, NULL, NULL, NULL, 'web_only', 0, NULL, NULL, NULL,
                '2026-08-25 14:31:01'
            );
            """
        )

    init_ledger_schema()
    init_ledger_schema()  # no-op repair path must remain safe

    legacy = list_push_events(DAY)[0]
    assert legacy.event_id == 7
    assert legacy.push_id == "legacy-push"
    assert legacy.session_scope is None

    message_key = new_push_id()
    append_push_events(
        [
            _decision(
                tier="P0X",
                rule="p0x_extended_hours",
                session_scope="premarket",
                delivery_channel="telegram",
                message_key=message_key,
            )
        ],
        [_delivery(message_key, tier="P0X")],
    )
    assert count_delivered_p0x_since(DAY) == 1

    db = get_db()
    with db.session_scope() as session:
        table_sql = session.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='journal_v2_push_events'"
            )
        ).scalar_one()
        columns = {
            row[1]
            for row in session.execute(
                text("PRAGMA table_info(journal_v2_push_events)")
            ).all()
        }
    assert "P0X" in table_sql
    assert "session_scope" in columns
    with pytest.raises(Exception, match="p0x_session_scope"):
        with db.session_scope() as session:
            session.execute(
                text(
                    "INSERT INTO journal_v2_push_events ("
                    "push_id, record_kind, et_date, ts, tier, rule, text, "
                    "delivery_channel, suppressed, created_at"
                    ") VALUES ("
                    "'unscoped-p0x', 'decision', '2026-08-25', "
                    "'2026-08-25 12:45:00', 'P0X', 'p0x_extended_hours', "
                    "'invalid', 'telegram', 0, '2026-08-25 12:45:01'"
                    ")"
                )
            )
    with pytest.raises(Exception, match="append-only"):
        with db.session_scope() as session:
            session.execute(
                text(
                    "UPDATE journal_v2_push_events "
                    "SET text='rewritten' WHERE id=7"
                )
            )

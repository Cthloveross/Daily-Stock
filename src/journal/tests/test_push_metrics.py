# -*- coding: utf-8 -*-
"""推送台账 × 回合台账机械关联（过程指标 1 + P0 passed 率，Phase B）。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.journal.ledger.models import EpisodeBuild
from src.journal.ledger.push_ledger_repository import (
    PushDecisionInput,
    PushDeliveryInput,
    append_push_events,
    new_push_id,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.push_metrics import (
    PUSH_JOIN_LIMITATIONS,
    _recent_delivered_p0,
    compute_p0_pass_rate,
    compute_push_compliance,
)
from src.storage import get_db

DAY = "2026-08-20"


def _push(
    tier="P1",
    ticker="NVDA",
    *,
    hour=13,
    channel="telegram_silent",
    suppressed=False,
    et_date=DAY,
    message_key=None,
):
    return PushDecisionInput(
        push_id=new_push_id(),
        et_date=et_date,
        ts=datetime(2026, 8, 20, hour, 0, tzinfo=timezone.utc),
        tier=tier,
        rule="p1_near_miss" if tier == "P1" else "p0_2m_candidate",
        ticker=ticker,
        text=f"{ticker} …",
        delivery_channel=channel,
        dedupe_key=(tier.lower(), ticker),
        suppressed=suppressed,
        suppressed_reason="daily_cap_reached" if suppressed else None,
        message_key=message_key,
    )


def _delivery(
    message_key,
    *,
    tier="P1",
    ok=True,
    hour=13,
    minute=1,
    et_date=DAY,
):
    return PushDeliveryInput(
        push_id=new_push_id(),
        et_date=et_date,
        ts=datetime(2026, 8, 20, hour, minute, tzinfo=timezone.utc),
        tier=tier,
        message_key=message_key,
        telegram_ok=ok,
        text="…",
        channel="telegram_silent" if tier == "P1" else "telegram",
    )


def _delivered_push(tier="P1", ticker="NVDA", *, hour=13, et_date=DAY):
    """decision + telegram_ok=true delivery（「已投递」的完整台账证据）。"""
    message_key = new_push_id()
    channel = "telegram_silent" if tier == "P1" else "telegram"
    return (
        _push(
            tier,
            ticker,
            hour=hour,
            channel=channel,
            et_date=et_date,
            message_key=message_key,
        ),
        _delivery(message_key, tier=tier, hour=hour, et_date=et_date),
    )


def _opened(hour):
    return datetime(2026, 8, 20, hour, 0, tzinfo=timezone.utc)


def _seed_episode_build(
    *,
    source_cutoff_at=datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc),
) -> None:
    """A covered, empty episode build: zero rows means observed no entry."""

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        session.add(
            EpisodeBuild(
                build_key="push-metrics-covered-build",
                broker="moomoo",
                account_key=DEFAULT_LEDGER_ACCOUNT_KEY,
                builder_name="push-metrics-test",
                builder_version="1.0.0",
                builder_config_sha256="0" * 64,
                evidence_set_sha256="f" * 64,
                source_batch_ids_json="[1]",
                source_cutoff_at=source_cutoff_at,
                status="succeeded",
                strategy_episode_count=0,
                position_episode_count=0,
                unresolved_evidence_count=0,
                reconciliation_status="matched",
                build_report_json="{}",
                completeness_score=Decimal("1"),
                completeness_json="{}",
                provenance_json="{}",
            )
        )


def test_metric1_missing_when_ledger_has_no_rows_for_the_day():
    result = compute_push_compliance(DAY, [("NVDA", _opened(14))])
    assert result.status == "missing"
    assert "提示器未运行或未启用" in result.reason
    assert result.ratio is None


def test_metric1_ready_ratio_and_honest_join_semantics():
    nvda_decision, nvda_delivery = _delivered_push("P1", "NVDA", hour=13)
    tsla_decision, tsla_delivery = _delivered_push("P0", "TSLA", hour=15)
    # 发送失败的推送：decision 行在、delivery 行 telegram_ok=false
    # → 不算已投递（D8：decision 只证明「决定发」，不证明送达）。
    failed_key = new_push_id()
    failed_decision = _push("P1", "AVGO", hour=13, message_key=failed_key)
    failed_delivery = _delivery(failed_key, ok=False, hour=13)
    # 无 delivery 行的 decision（如台账在发送前崩溃）同样不算已投递。
    orphan_decision = _push("P1", "SMCI", hour=13, message_key=new_push_id())
    append_push_events(
        [
            nvda_decision,
            tsla_decision,
            failed_decision,
            orphan_decision,
            # 抑制行不算已投递推送。
            _push("P1", "MRVL", hour=13, suppressed=True),
            # P2 页面行不算推送，但证明提示器当日在运行。
            PushDecisionInput(
                push_id=new_push_id(),
                et_date=DAY,
                ts=datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc),
                tier="P2",
                rule="volume_burst",
                ticker="AMD",
                text="AMD 放量",
                delivery_channel="web_only",
                dedupe_key=("volume_burst", "AMD"),
            ),
        ],
        [nvda_delivery, tsla_delivery, failed_delivery],
    )
    opened = [
        ("NVDA", _opened(14)),   # 推送(13:00) 在开仓(14:00) 前 → 依从
        ("TSLA", _opened(14)),   # 推送(15:00) 在开仓(14:00) 后 → 不算依从
        ("MRVL", _opened(14)),   # 只有被抑制的推送 → freelance
        ("AMD", _opened(14)),    # 只有 P2 页面行 → freelance
        ("AVGO", _opened(14)),   # 推送发送失败 → 未送达 → freelance
        ("SMCI", _opened(14)),   # decision 无 delivery 行 → 未送达 → freelance
    ]
    result = compute_push_compliance(DAY, opened)
    assert result.status == "ready"
    assert result.total_episodes == 6
    assert result.pushed_episodes == 1
    assert result.ratio == 1 / 6
    assert result.pushes_total == 2  # 只数送达确证的 NVDA/TSLA
    # followed：NVDA 推送后同日有开仓；TSLA 开仓在推送前 → 不算 followed。
    assert result.pushes_followed == 1
    # 诚实边界原样携带（测试锁定文案）。
    assert result.limitations == PUSH_JOIN_LIMITATIONS
    assert any("不证明因果" in item for item in PUSH_JOIN_LIMITATIONS)
    assert any("telegram_ok" in item for item in PUSH_JOIN_LIMITATIONS)
    assert "机械关联" in result.reason


def test_metric1_one_episode_satisfies_at_most_one_push():
    """D8：followed 按回合侧去重——同标的 P0+P1 两条推送、一笔回合，
    只算一次跟随（先到的推送先配）。"""
    p1_decision, p1_delivery = _delivered_push("P1", "NVDA", hour=13)
    p0_decision, p0_delivery = _delivered_push("P0", "NVDA", hour=14)
    append_push_events(
        [p1_decision, p0_decision], [p1_delivery, p0_delivery]
    )
    result = compute_push_compliance(DAY, [("NVDA", _opened(15))])
    assert result.status == "ready"
    assert result.pushes_total == 2
    assert result.pushes_followed == 1  # 一笔回合至多满足一条推送
    assert result.pushed_episodes == 1 and result.total_episodes == 1


def test_metric1_uses_first_successful_delivery_time_not_decision_time():
    message_key = new_push_id()
    decision = _push(
        "P0",
        "NVDA",
        hour=13,
        channel="telegram",
        message_key=message_key,
    )
    # 决定 13:00，实际到达 15:01；14:00 的开仓发生在手机收到消息以前。
    delivery = _delivery(message_key, tier="P0", hour=15)
    append_push_events([decision], [delivery])

    result = compute_push_compliance(DAY, [("NVDA", _opened(14))])

    assert result.status == "ready"
    assert result.pushed_episodes == 0
    assert result.pushes_followed == 0
    assert result.ratio == 0.0


def test_metric1_no_episodes_reports_missing_with_push_count():
    decision, delivery = _delivered_push("P1", "NVDA", hour=13)
    append_push_events([decision], [delivery])
    result = compute_push_compliance(DAY, [])
    assert result.status == "missing"
    assert "今日无新开回合" in result.reason
    assert result.pushes_total == 1


def test_p0_pass_rate_missing_until_first_delivered_p0():
    result = compute_p0_pass_rate()
    assert result.status == "missing"
    assert result.auto_tighten_enabled is False
    assert result.threshold == 0.70


def test_p0_pass_rate_missing_without_episode_build():
    nvda_decision, nvda_delivery = _delivered_push("P0", "NVDA", hour=13)
    tsla_decision, tsla_delivery = _delivered_push("P0", "TSLA", hour=13)
    append_push_events(
        [nvda_decision, tsla_decision],
        [nvda_delivery, tsla_delivery],
    )
    # 无回合 build 无法区分“确实没开仓”与“尚未导入”，必须标缺。
    result = compute_p0_pass_rate()
    assert result.status == "missing"
    assert result.delivered == 2
    assert result.passed == 0
    assert result.rate is None
    assert "无 EpisodeBuild" in result.reason
    assert result.auto_tighten_enabled is False  # 只展示，绝不自改阈值
    assert result.limitations == PUSH_JOIN_LIMITATIONS


def test_p0_pass_rate_counts_unfollowed_with_covered_episode_build():
    nvda_decision, nvda_delivery = _delivered_push("P0", "NVDA", hour=13)
    tsla_decision, tsla_delivery = _delivered_push("P0", "TSLA", hour=13)
    append_push_events(
        [nvda_decision, tsla_decision],
        [nvda_delivery, tsla_delivery],
    )
    _seed_episode_build()

    result = compute_p0_pass_rate()

    assert result.status == "ready"
    assert result.delivered == 2
    assert result.passed == 2
    assert result.rate == 1.0
    # D8：历史不足目标窗口时如实报告实际窗口。
    assert result.reason is not None and "实际窗口 2 条" in result.reason


def test_p0_pass_rate_missing_when_build_does_not_cover_alert_et_day():
    decision, delivery = _delivered_push("P0", "NVDA", hour=13)
    append_push_events([decision], [delivery])
    # P0 到达后仅覆盖到当日 18:00 UTC，尚未覆盖 ET 当日余下时段。
    _seed_episode_build(
        source_cutoff_at=datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    )

    result = compute_p0_pass_rate()

    assert result.status == "missing"
    assert result.delivered == 1
    assert result.rate is None
    assert "source_cutoff_at 尚未覆盖" in result.reason


def test_p0_pass_rate_window_fetch_scans_past_non_qualifying_rows():
    """D8：窗口回填分批回扫——大量不入窗行（web_only P0）夹在中间时，
    旧的一次性 `limit*2` 取数会取不满窗口；新实现循环翻页直到凑满或扫尽。"""
    telegram_pairs = [
        _delivered_push("P0", ticker, hour=13)
        for ticker in ("NVDA", "TSLA", "AMD")
    ]
    web_rows = [
        PushDecisionInput(
            push_id=new_push_id(),
            et_date=DAY,
            ts=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
            tier="P0",
            rule="p0_candidate",
            ticker=f"T{index:03d}",
            text="…",
            delivery_channel="web_only",
            dedupe_key=("p0", f"T{index:03d}"),
        )
        for index in range(60)
    ]
    # telegram 行先写（id 更小＝更旧），60 条 web_only 行盖在上面。
    append_push_events(
        [decision for decision, _delivery_row in telegram_pairs],
        [delivery for _decision, delivery in telegram_pairs],
    )
    append_push_events(web_rows)
    _seed_episode_build()
    result = compute_p0_pass_rate()
    assert result.status == "ready"
    assert result.delivered == 3  # 旧实现只扫最新 40 行 → 0 条，标缺冒充
    assert result.reason is not None and "实际窗口 3 条" in result.reason


def test_p0_pass_rate_rejects_failed_and_orphan_decisions():
    failed_key = new_push_id()
    failed = _push(
        "P0",
        "NVDA",
        channel="telegram",
        message_key=failed_key,
    )
    orphan = _push(
        "P0",
        "TSLA",
        channel="telegram",
        message_key=new_push_id(),
    )
    append_push_events(
        [failed, orphan],
        [_delivery(failed_key, tier="P0", ok=False)],
    )

    result = compute_p0_pass_rate()

    assert result.status == "missing"
    assert result.delivered == 0
    assert result.rate is None


def test_p0_pass_rate_excludes_retired_legacy_p0_rows():
    strict_decision, strict_delivery = _delivered_push(
        "P0", "NVDA", hour=13
    )
    legacy_key = new_push_id()
    legacy = _push(
        "P0",
        "TSLA",
        hour=14,
        channel="telegram",
        message_key=legacy_key,
    )
    legacy = PushDecisionInput(
        **{
            **legacy.__dict__,
            "rule": "p0_candidate",
        }
    )
    append_push_events(
        [strict_decision, legacy],
        [strict_delivery, _delivery(legacy_key, tier="P0", hour=14)],
    )

    delivered = _recent_delivered_p0(20)

    assert [(row.rule, row.ticker) for row in delivered] == [
        ("p0_2m_candidate", "NVDA")
    ]


def test_recent_p0_uses_first_successful_delivery_timestamp():
    message_key = new_push_id()
    decision = _push(
        "P0",
        "NVDA",
        hour=13,
        channel="telegram",
        message_key=message_key,
    )
    append_push_events(
        [decision],
        [
            _delivery(message_key, tier="P0", ok=False, hour=13),
            _delivery(message_key, tier="P0", hour=14),
            _delivery(message_key, tier="P0", hour=15),
        ],
    )

    delivered = _recent_delivered_p0(1)

    assert len(delivered) == 1
    assert delivered[0].ts == datetime(
        2026, 8, 20, 14, 1, tzinfo=timezone.utc
    )

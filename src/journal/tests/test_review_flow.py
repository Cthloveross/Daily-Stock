# -*- coding: utf-8 -*-
"""Daily review flow domain logic (blueprint 17 §三(a), Phase A)."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.journal.brokers.moomoo_statement import (
    StatementFill,
    StatementOrder,
    StatementParseResult,
)
from src.journal.ledger.daily_review_repository import (
    DailyReviewSessionInput,
    append_daily_review_session,
)
from src.journal.ledger.episode_repository import (
    append_latest_position_episode_build,
    get_latest_position_episode_page,
)
from src.journal.ledger.repository import import_statement_batch
from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    append_review_annotation,
)
from src.journal.review_flow import (
    BANNED_REVIEW_METRICS,
    MISTAKE_VOCABULARY,
    build_reveal_summary,
    classify_decision_quadrant,
    get_daily_review_flow,
    get_episode_review_verdict,
)

ET = ZoneInfo("America/New_York")
DAY = "2026-08-20"


def _synthetic_day_statement() -> StatementParseResult:
    """Three episodes on one ET day: compliant 0DTE win, 2DTE violation win,
    and a 7DTE position still open (for exit-plan pre-registration)."""
    orders: list[StatementOrder] = []
    next_row = 2

    def _add(symbol: str, legs: list[tuple[str, Decimal, datetime]]) -> None:
        nonlocal next_row
        for side, price, occurred_at in legs:
            row = next_row
            next_row += 1
            fill = StatementFill(
                source_row=row,
                quantity=Decimal("1"),
                price=price,
                filled_at=occurred_at,
                amount=price * Decimal("100"),
                market="US",
                currency="USD",
            )
            orders.append(
                StatementOrder(
                    source_row=row,
                    derived_order_id=f"flow-{row}",
                    symbol=symbol,
                    name="Review flow fixture",
                    side=side,
                    status="FILLED",
                    order_quantity=Decimal("1"),
                    order_price=None,
                    order_price_text="Market",
                    order_amount=None,
                    order_time=occurred_at - timedelta(seconds=2),
                    order_type="Market",
                    time_in_force="Day",
                    session="Regular Trading Hours",
                    market="US",
                    currency="USD",
                    summary_filled_quantity=Decimal("1"),
                    summary_average_price=price,
                    fills=(fill,),
                    fee_components=(),
                    total_fee=Decimal("0.65"),
                    evidence_level="fill_detail",
                )
            )

    # 合规 0DTE（09:45 开，同日平，赢）。
    _add(
        "AAA260820C00100000",
        [
            ("BUY", Decimal("2.50"), datetime(2026, 8, 20, 9, 45, tzinfo=ET)),
            ("SELL", Decimal("4.00"), datetime(2026, 8, 20, 11, 0, tzinfo=ET)),
        ],
    )
    # 违规 2DTE（V2-C①），同日平且赢 → 侥幸。
    _add(
        "BBB260822C00100000",
        [
            ("BUY", Decimal("3.00"), datetime(2026, 8, 20, 10, 15, tzinfo=ET)),
            ("SELL", Decimal("3.60"), datetime(2026, 8, 20, 14, 0, tzinfo=ET)),
        ],
    )
    # 7DTE，未平 → open position。
    _add(
        "CCC260827C00100000",
        [("BUY", Decimal("1.50"), datetime(2026, 8, 20, 10, 30, tzinfo=ET))],
    )
    return StatementParseResult(
        source_sha256="f" * 64,
        parser_version="review-flow-test-v1",
        rows_total=len(orders),
        headers=("synthetic",),
        orders=orders,
    )


def _seed() -> tuple[int, dict[str, int]]:
    import_statement_batch(_synthetic_day_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=20)
    ids = {item.raw_symbol[:3]: item.episode_id for item in page.items}
    return build.build_id, ids


class TestQuadrant:
    def test_four_quadrants(self):
        assert classify_decision_quadrant("compliant", Decimal("5")) == "deserved_win"
        assert classify_decision_quadrant("compliant", Decimal("-5")) == "bad_luck"
        assert (
            classify_decision_quadrant("violation", Decimal("5"))
            == "undeserved_win"
        )
        assert (
            classify_decision_quadrant("violation", Decimal("-5"))
            == "deserved_loss"
        )

    def test_unjudgeable_stays_unjudged(self):
        assert classify_decision_quadrant("uncovered", Decimal("5")) == "not_judged"
        assert classify_decision_quadrant("unknown", Decimal("5")) == "not_judged"
        assert classify_decision_quadrant("compliant", None) == "not_judged"
        assert classify_decision_quadrant("violation", Decimal("0")) == "not_judged"


def test_daily_flow_verdicts_metrics_and_blind_payload():
    _build_id, ids = _seed()
    flow = get_daily_review_flow(et_date=DAY)
    assert flow is not None
    assert flow.et_date == DAY
    assert flow.rest_day_candidate is False

    by_id = {item.episode_id: item for item in flow.episodes_today}
    assert len(by_id) == 3
    aaa = by_id[ids["AAA"]]
    bbb = by_id[ids["BBB"]]
    ccc = by_id[ids["CCC"]]
    assert (aaa.lane, aaa.verdict, aaa.rule_id) == (
        "intraday_0dte",
        "compliant",
        "V2-A",
    )
    assert (bbb.lane, bbb.verdict, bbb.rule_id) == ("dte_1_3", "violation", "V2-C①")
    assert bbb.needs_ack is True and bbb.acked is False
    assert ccc.lifecycle_status == "open"

    # 盲评：装配结果不携带任何盈亏字段。
    for verdict in flow.episodes_today:
        assert not hasattr(verdict, "realized_pnl_net")
        assert not hasattr(verdict, "pnl_net")

    # 出场预登记：CCC 未平且无在册计划。
    assert [item.episode_id for item in flow.open_positions] == [ids["CCC"]]
    assert flow.open_positions[0].has_exit_plan is False

    metrics = {metric.metric_id: metric for metric in flow.process_metrics}
    assert set(metrics) == {1, 2, 3, 4, 5, 6, 7}
    assert metrics[1].basis == "missing" and metrics[1].manual_allowed
    assert metrics[4].basis == "missing" and metrics[4].manual_allowed
    # 结构依从率：合规风险 = AAA 250 + CCC 150（7DTE 未平仍是 overnight_4_7？
    # 不：CCC 未平 → classify 需要 closed_day，缺 → unknown，不判定）。
    # 可判定风险 = AAA 250 + BBB 300；合规占比 250/550。
    assert metrics[2].basis == "auto"
    assert metrics[2].value_ratio == pytest.approx(250 / 550)
    # 出场预登记未启用过 → 标缺。
    assert metrics[3].basis == "missing"
    # 费用占比：(0.65×4)/(250+300)。
    assert metrics[5].basis == "auto"
    assert metrics[5].value_ratio == pytest.approx(2.6 / 550)
    # 决策日志完整率：无 annotation → 0。
    assert metrics[6].basis == "auto"
    assert metrics[6].value_ratio == 0.0
    assert metrics[7].basis == "auto"


def test_exit_plan_registration_lights_up_open_position():
    build_id, ids = _seed()
    append_review_annotation(
        ReviewAnnotationInput(
            review_status="in_progress",
            invalidation_plan="跌破 8/20 低点或 8/26 收盘前无进展则退出",
        ),
        episode_build_id=build_id,
        position_episode_id=ids["CCC"],
    )
    flow = get_daily_review_flow(et_date=DAY)
    assert flow is not None
    assert flow.open_positions[0].has_exit_plan is True
    assert "退出" in flow.open_positions[0].exit_plan_text


def test_rest_day_candidate_and_streak():
    _seed()
    flow = get_daily_review_flow(et_date="2026-08-21")
    assert flow is not None
    assert flow.rest_day_candidate is True
    assert flow.episodes_today == ()
    # 密封的休息日会话进入连续计数。
    append_daily_review_session(
        DailyReviewSessionInput(
            et_date="2026-08-21", session_kind="rest_day", sealed=True
        )
    )
    next_day = get_daily_review_flow(et_date="2026-08-24")
    assert next_day is not None
    assert next_day.rest_streak == 1


def test_reveal_summary_quadrants():
    build_id, ids = _seed()
    summary = build_reveal_summary(
        "default_moomoo_us", build_id=build_id, et_date=DAY
    )
    assert summary.closed_episode_count == 2
    by_id = {item.episode_id: item for item in summary.episodes}
    assert by_id[ids["AAA"]].quadrant == "deserved_win"
    assert by_id[ids["BBB"]].quadrant == "undeserved_win"
    assert by_id[ids["BBB"]].quadrant_label == "侥幸"
    assert summary.quadrant_counts["undeserved_win"] == 1
    # (400-250-1.3) + (360-300-1.3)
    assert summary.total_net == pytest.approx(207.4)


def test_episode_verdict_and_counterfactuals():
    build_id, ids = _seed()
    verdict = get_episode_review_verdict(ids["BBB"], build_id=build_id)
    assert verdict is not None
    assert verdict.verdict == "violation"
    assert verdict.counterfactual_compliant_excluded is True
    assert "移除" in verdict.counterfactual_compliant_text
    # 反事实 B：gate 逐日记录未回填 → 标缺，绝不现算。
    assert verdict.counterfactual_gate_status == "missing"

    compliant = get_episode_review_verdict(ids["AAA"], build_id=build_id)
    assert compliant is not None
    assert compliant.counterfactual_compliant_excluded is False


def test_est_entry_hour_uses_real_eastern_time_not_utc_minus_4():
    """复核修复 6：EST（冬令时）下车道判定用真实 ET。

    2026-12-01 16:30 UTC = 11:30 EST（UTC−5）。UTC−4 近似会算成 12:30，
    把合规的 0DTE 上午单误判为 late_0dte；真实 ET 必须判 intraday_0dte。
    """
    from datetime import timezone as _tz
    from types import SimpleNamespace

    from src.journal.review_flow import _et_day, _et_hour, _lane_of

    est_open = datetime(2026, 12, 1, 16, 30, tzinfo=_tz.utc)
    assert _et_hour(est_open) == 11
    row = SimpleNamespace(
        opened_at=est_open,
        closed_at=datetime(2026, 12, 1, 20, 0, tzinfo=_tz.utc),
        dte_at_entry=0,
    )
    assert _lane_of(row) == "intraday_0dte"

    # 自然日边界：2026-12-01 04:30 UTC = 11-30 23:30 EST（UTC−4 会给 12-01）。
    assert _et_day(datetime(2026, 12, 1, 4, 30, tzinfo=_tz.utc)) == "2026-11-30"
    # 夏令时（EDT=UTC−4）行为不变。
    assert _et_hour(datetime(2026, 8, 20, 13, 45, tzinfo=_tz.utc)) == 9


def test_metric6_excludes_exit_plan_only_annotations():
    """复核修复 9：仅含出场计划的修订（S3 预登记路径）不计入指标 6。"""
    from types import SimpleNamespace

    from src.journal.review_flow import _entry_side_filled

    # 判定规则（字段集）：thesis/trigger/rationale 至少一项非空才算决策日志；
    # S3 只写 invalidation_plan → 不算。
    def _row(**overrides):
        base = dict(
            setup_thesis="",
            entry_trigger="",
            invalidation_plan="",
            position_rationale="",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    assert _entry_side_filled(_row(invalidation_plan="跌破低点离场")) is False
    assert _entry_side_filled(_row(setup_thesis="突破回踩确认")) is True
    assert _entry_side_filled(_row(entry_trigger="首根 5m 收上 VWAP")) is True
    assert _entry_side_filled(_row(position_rationale="半仓试错")) is True

    # 流级验证：S3 形状（仅出场计划）的当日修订不抬高指标 6。
    build_id, ids = _seed()
    append_review_annotation(
        ReviewAnnotationInput(
            review_status="in_progress",
            invalidation_plan="跌破入场日低点即离场",
            tags=("exit_plan_registration",),
        ),
        episode_build_id=build_id,
        position_episode_id=ids["AAA"],
    )
    flow = get_daily_review_flow(et_date=DAY)
    assert flow is not None
    metrics = {metric.metric_id: metric for metric in flow.process_metrics}
    assert metrics[6].value_ratio == 0.0


def test_hard_line_constants_are_locked():
    # 禁用指标清单（蓝图 §三(c)）：常量在，页面测试据此做负断言。
    for banned in ("sqn", "zella_score", "mae_derived_stop", "hold_time_bucket_edge"):
        assert banned in BANNED_REVIEW_METRICS
    # mistake 词表（蓝图 §三(b) 初版）逐项锁定。
    assert MISTAKE_VOCABULARY == (
        "freelance_no_push",
        "chase_after_expiry",
        "late_0dte",
        "early_exit_fear",
        "size_overrun",
        "revenge_add",
        "plan_absent",
    )

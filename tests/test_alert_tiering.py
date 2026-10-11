# -*- coding: utf-8 -*-
"""四级推送协议判定层的确定性测试（蓝图 17 §2.2，Phase B）。

覆盖：严格 2m 四项事实、开盘 1m 时序、财报风险上下文、R6 保险丝三态、
统一 XNYS 手动执行/清仓时窗，以及「聪明钱/主力/大资金」
措辞禁令的负向断言。纯函数 + 夹具，零网络零 DB。
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from src.services.alert_tiering import (
    EARNINGS_LONG_PREMIUM_BLOCK_TEXT,
    P0_AUTO_TIGHTEN_ENABLED,
    P0_DAILY_BUDGET,
    P0_PASS_RATE_TIGHTEN_THRESHOLD,
    P0_PASS_RATE_WINDOW,
    P0_WEEKLY_BUDGET,
    P1_DAILY_BUDGET,
    R6_FUSE_DAILY_LOSS_USD,
    TierContext,
    TierEngineState,
    earnings_veto,
    evaluate_p0_2m_conditions,
    intraday_execution_policy,
    fuse_state,
    opening_probe_direction,
    opening_probe_profile,
    opportunity_telegram_window,
    power_hour_window,
    same_day_exit_deadline_et,
)

# 2026-08-25 是周二；10:31 ET。
_AS_OF = "2026-08-25T14:31:00+00:00"


def _payload(**overrides):
    payload = {
        "market_date_et": "2026-08-25",
        "as_of": _AS_OF,
        "session_state": "regular",
        "quote_session_scope": "current_session",
    }
    payload.update(overrides)
    return payload


def _candidate(**overrides):
    row = {
        "ticker": "NVDA",
        "earnings_proximity": {
            "state": "ready",
            "within_blackout": False,
            "days_to_earnings": None,
        },
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# 财报拦截（B3-①后端半）/ R6 保险丝 / 时间闸门
# ---------------------------------------------------------------------------

def test_earnings_veto_three_states_and_citation():
    blackout = _candidate(
        earnings_proximity={
            "state": "ready", "within_blackout": True, "days_to_earnings": 0,
        }
    )
    verdict, detail = earnings_veto(blackout)
    assert verdict == "blackout"
    assert EARNINGS_LONG_PREMIUM_BLOCK_TEXT in detail
    assert "de Silva, Smith & So (2026, Rev. Finance)" in detail

    unknown = _candidate()
    unknown.pop("earnings_proximity")
    verdict, detail = earnings_veto(unknown)
    assert verdict == "unknown"
    assert "未知≠安全" in detail

    assert earnings_veto(_candidate())[0] == "clear"


def test_earnings_veto_same_day_policy_does_not_block_future_event():
    """同日平仓不跨未来财报；未来事件由实时 IV/点差摩擦门处理。"""
    four_days_out = _candidate(
        earnings_proximity={
            "state": "ready", "within_blackout": False, "days_to_earnings": 4,
        }
    )
    verdict, detail = earnings_veto(four_days_out)
    assert verdict == "clear"
    assert "同日平仓不跨事件" in detail
    assert earnings_veto(_candidate())[0] == "clear"


def test_earnings_veto_non_bool_within_blackout_is_unknown():
    """D9：within_blackout 是单一生产方的布尔契约；非布尔＝契约漂移 →
    unknown（未知≠安全），绝不把 truthy 杂质当命中或当清白。"""
    for weird in ("yes", 1, 0, None, [True]):
        candidate = _candidate(
            earnings_proximity={
                "state": "ready",
                "within_blackout": weird,
                "days_to_earnings": None,
            }
        )
        verdict, detail = earnings_veto(candidate)
        assert verdict == "unknown", weird
        assert "未知≠安全" in detail


def test_fuse_state_three_values_and_missing_never_triggers():
    assert fuse_state(TierContext(daily_realized_pnl=None)) == "missing"
    assert (
        fuse_state(TierContext(daily_realized_pnl=-R6_FUSE_DAILY_LOSS_USD))
        == "triggered"
    )
    assert (
        fuse_state(
            TierContext(daily_realized_pnl=-R6_FUSE_DAILY_LOSS_USD + 1)
        )
        == "not_triggered"
    )
    assert fuse_state(TierContext(daily_realized_pnl=500.0)) == "not_triggered"


def test_resolve_fuse_state_latches_for_the_day():
    """D2：触线即当日锁存——盈亏回升或数据断供都不解锁（当日不可上调，
    触线通告承诺的「明日复位」由日翻转的 state 重建兑现）。"""
    from src.services.alert_tiering import resolve_fuse_state

    state = TierEngineState()
    # 未锁存 + 缺失 → missing（保险丝绝不因缺数据触发）。
    assert resolve_fuse_state(
        TierContext(daily_realized_pnl=None), state
    ) == "missing"
    assert state.fuse_latched_today is False
    # 确证触线 → 锁存。
    assert resolve_fuse_state(
        TierContext(daily_realized_pnl=-13_000.0), state
    ) == "triggered"
    assert state.fuse_latched_today is True
    # 盈亏回升到线内：当日仍 triggered。
    assert resolve_fuse_state(
        TierContext(daily_realized_pnl=-11_000.0), state
    ) == "triggered"
    # 数据断供：当日仍 triggered。
    assert resolve_fuse_state(
        TierContext(daily_realized_pnl=None), state
    ) == "triggered"
    # 明日＝新 state → 复位。
    fresh = TierEngineState()
    assert resolve_fuse_state(
        TierContext(daily_realized_pnl=-11_000.0), fresh
    ) == "not_triggered"


def test_power_hour_window_bounds():
    assert power_hour_window({"as_of": "2026-08-25T19:30:00+00:00"}) is True
    assert power_hour_window({"as_of": "2026-08-25T19:29:00+00:00"}) is False
    assert power_hour_window({"as_of": "2026-08-25T20:00:00+00:00"}) is False


def test_half_day_exit_and_flatten_windows_from_real_close():
    half_day = {"as_of": "2026-11-27T17:30:00+00:00"}  # 12:30 ET
    assert power_hour_window(half_day) is True
    assert same_day_exit_deadline_et(half_day) == (12, 45)
    assert power_hour_window(
        {"as_of": "2026-11-27T18:00:00+00:00"}
    ) is False


@pytest.mark.parametrize(
    "as_of,expected",
    [
        ("2026-11-27T17:59:59+00:00", True),   # 12:59:59 ET
        ("2026-11-27T18:00:00+00:00", False),  # exact 13:00 close
        ("2026-11-27T18:05:00+00:00", False),  # warm grace period
        ("2026-09-07T14:00:00+00:00", False),  # XNYS holiday
        ("not-a-timestamp", False),
    ],
)
def test_opportunity_telegram_window_uses_exact_xnys_phase(as_of, expected):
    assert opportunity_telegram_window({"as_of": as_of}) is expected


def test_opportunity_window_prefers_actual_observation_time():
    assert opportunity_telegram_window(
        {
            "as_of": "2026-11-27T17:59:59+00:00",
            "alert_observed_at": "2026-11-27T18:00:00+00:00",
        }
    ) is False
    assert opportunity_telegram_window(
        {
            "as_of": "2026-11-27T17:59:59+00:00",
            "alert_observed_at": "not-a-timestamp",
        }
    ) is False


def test_opening_probe_is_independent_of_15m_30m_confirmation():
    payload = _payload(as_of="2026-08-25T13:35:05+00:00")
    final_profile = {
        "state": "qualified",
        "alert_qualified": True,
        "rule_version": "opening_impulse_v2",
        "checkpoint": 5,
        "checkpoint_label": "09:35",
        "bar_start_et": "09:30",
        "confirmation_mode": "completed_1m_cumulative",
        "confirmation_points": [5],
        "current_checkpoint_pass": True,
        "direction": "up",
    }
    candidate = _candidate(
        session_bursts={
            "state": "insufficient_bars",
            "current": None,
            "legs": [],
            "opening_progressive": final_profile,
        },
        recent_displacement={"state": "insufficient_bars"},
    )
    assert opening_probe_direction(payload, candidate) == "up"

    late_observation = copy.deepcopy(payload)
    late_observation["alert_observed_at"] = "2026-08-25T13:45:00+00:00"
    assert opening_probe_direction(late_observation, candidate) is None

    last_fresh_second = copy.deepcopy(payload)
    last_fresh_second["alert_observed_at"] = "2026-08-25T13:44:59+00:00"
    # Final 09:35 evidence expires with that wall minute; stale payloads are
    # never replayed as a later phone alert.
    assert opening_probe_direction(last_fresh_second, candidate) is None

    invalid_observation = copy.deepcopy(payload)
    invalid_observation["alert_observed_at"] = "not-a-timestamp"
    assert opening_probe_direction(invalid_observation, candidate) is None

    assert opening_probe_direction(
        _payload(as_of="2026-08-25T13:34:59+00:00"), candidate
    ) is None
    assert opening_probe_direction(
        _payload(as_of="2026-08-25T13:45:00+00:00"), candidate
    ) is None
    candidate["session_bursts"]["opening_progressive"]["state"] = "not_qualified"
    assert opening_probe_direction(payload, candidate) is None


def test_strict_2m_p0_has_exactly_four_signal_conditions():
    candidate = {
        "p0_signal_evidence_v2": {
            "signal_version": "p0_2m_signal_v1",
            "data_quality": {"status": "complete", "issues": []},
            "baseline_count": 60,
            "price_percentile": 0.95,
            "rvol_8m": 1.5,
            "displacement_atr": 0.5,
            "direction": "down",
            "impulse_8m_log": -0.01,
            "displacement_18m_percent": -1.2,
        },
        # These contexts are deliberately hostile/missing and cannot rewrite
        # the four underlying signal facts.
        "regime": {"state": "red"},
        "earnings_proximity": {"state": "ready", "days_to_earnings": 0},
        "option_contract_friction": {"state": "unavailable"},
    }
    conditions = evaluate_p0_2m_conditions(candidate)
    assert len(conditions) == 4
    assert [condition.condition_id for condition in conditions] == [1, 2, 3, 4]
    assert all(condition.status == "pass" for condition in conditions)


@pytest.mark.parametrize("updates, condition_id, status", [
    ({"signal_version": "old"}, 1, "missing"),
    ({"baseline_count": 59}, 1, "missing"),
    ({"data_quality": {"status": "incomplete", "issues": ["missing_minute"]}}, 1, "missing"),
    ({"price_percentile": 0.9499}, 2, "fail"),
    ({"price_percentile": None}, 2, "missing"),
    ({"rvol_8m": 1.4999}, 3, "fail"),
    ({"rvol_8m": None}, 3, "missing"),
    ({"displacement_atr": 0.4999}, 4, "fail"),
    ({"displacement_atr": None}, 4, "missing"),
    ({"failed_checks": ["impulse_displacement_direction_mismatch"]}, 4, "fail"),
])
def test_current_four_conditions_fail_closed_at_each_frozen_boundary(updates, condition_id, status):
    evidence = {
        "signal_version": "p0_2m_signal_v1",
        "data_quality": {"status": "complete", "issues": []},
        "baseline_count": 60,
        "price_percentile": 0.95,
        "rvol_8m": 1.5,
        "displacement_atr": 0.5,
        "direction": "up",
    }
    evidence.update(updates)
    conditions = evaluate_p0_2m_conditions({"p0_signal_evidence_v2": evidence})
    assert len(conditions) == 4
    assert {condition.condition_id: condition.status for condition in conditions} == {
        index: status if index == condition_id else "pass" for index in range(1, 5)
    }


def test_production_has_no_retired_seven_condition_gate_or_state():
    from src.services import alert_tiering

    retired = {
        "evaluate_p0_conditions", "classify_conditions", "P0_CONDITION_NAMES",
        "friction_probe_direction", "_instant_dual_window", "_dual_window_condition",
    }
    assert retired.isdisjoint(vars(alert_tiering))
    assert {"tick_index", "confirmations"}.isdisjoint(vars(TierEngineState()))
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "src/services/intraday_opportunity_alerter.py",
        "src/services/intraday_option_friction.py",
        "api/app.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        names = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree) if isinstance(node, (ast.Name, ast.Attribute))
        }
        names.update(
            alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )
        assert retired.isdisjoint(names), relative


@pytest.mark.parametrize(
    (
        "checkpoint",
        "checkpoint_label",
        "observed_at",
        "stale_at",
        "can_confirm",
    ),
    [
        (
            1,
            "09:31",
            "2026-08-25T13:31:05+00:00",
            "2026-08-25T13:32:05+00:00",
            False,
        ),
        (
            2,
            "09:32",
            "2026-08-25T13:32:05+00:00",
            "2026-08-25T13:33:05+00:00",
            True,
        ),
        (
            3,
            "09:33",
            "2026-08-25T13:33:05+00:00",
            "2026-08-25T13:34:05+00:00",
            True,
        ),
        (
            4,
            "09:34",
            "2026-08-25T13:34:05+00:00",
            "2026-08-25T13:35:05+00:00",
            True,
        ),
    ],
)
def test_progressive_opening_probe_is_fresh_only_in_its_checkpoint_minute(
    checkpoint,
    checkpoint_label,
    observed_at,
    stale_at,
    can_confirm,
):
    """09:31 cannot confirm; later checkpoints expire after their minute."""

    confirmation_points = (
        [checkpoint - 1, checkpoint] if checkpoint >= 2 else [checkpoint]
    )
    progressive = {
        "state": "qualified",
        "alert_qualified": True,
        "rule_version": "opening_impulse_v2",
        "checkpoint": checkpoint,
        "checkpoint_label": checkpoint_label,
        "confirmation_mode": "consecutive_1m",
        "confirmation_points": confirmation_points,
        "confirmation_labels": [
            f"09:{30 + point:02d}" for point in confirmation_points
        ],
        "current_checkpoint_pass": True,
        "direction": "up",
    }
    candidate = _candidate(
        session_bursts={
            "state": "opening_progressive",
            "opening_progressive": progressive,
        }
    )
    fresh = _payload(as_of=observed_at, alert_observed_at=observed_at)
    if can_confirm:
        assert opening_probe_profile(fresh, candidate) is progressive
        assert opening_probe_direction(fresh, candidate) == "up"
    else:
        assert opening_probe_profile(fresh, candidate) is None
        assert opening_probe_direction(fresh, candidate) is None

    stale = _payload(as_of=observed_at, alert_observed_at=stale_at)
    assert opening_probe_profile(stale, candidate) is None
    assert opening_probe_direction(stale, candidate) is None


# ---------------------------------------------------------------------------
# 自动收紧常量：只展示不自改（蓝图护栏进代码常量）。
# ---------------------------------------------------------------------------

def test_auto_tighten_is_gated_off_by_constant():
    assert P0_AUTO_TIGHTEN_ENABLED is False
    assert P0_PASS_RATE_WINDOW == 20
    assert P0_PASS_RATE_TIGHTEN_THRESHOLD == 0.70
    assert (P0_DAILY_BUDGET, P0_WEEKLY_BUDGET, P1_DAILY_BUDGET) == (2, 7, 3)


@pytest.mark.parametrize("as_of, ordinary, zero", [
    ("2026-08-25T09:44:59-04:00", False, False),
    ("2026-08-25T09:45:00-04:00", True, True),
    ("2026-08-25T14:29:59-04:00", True, True),
    ("2026-08-25T14:30:00-04:00", True, False),
    ("2026-08-25T15:15:00-04:00", False, False),
    ("2026-11-27T11:30:00-05:00", True, False),
    ("2026-11-27T12:15:00-05:00", False, False),
    ("2026-11-27T12:50:00-05:00", False, False),
])
def test_execution_policy_uses_exact_manual_boundaries(as_of, ordinary, zero):
    policy = intraday_execution_policy(as_of)
    assert policy["ordinary_entry_allowed"] is ordinary
    assert policy["zero_dte_entry_allowed"] is zero
    if "11-27" in as_of:
        assert policy["close_et"] == "13:00"
        assert policy["ordinary_entry_cutoff_et"] == "12:15"
        assert policy["zero_dte_entry_cutoff_et"] == "11:30"
        assert policy["exit_deadline_et"] == "12:45"
    else:
        assert policy["close_et"] == "16:00"
        assert policy["ordinary_entry_cutoff_et"] == "15:15"
        assert policy["exit_deadline_et"] == "15:45"


def test_execution_policy_expiry_does_not_cross_next_transition():
    assert intraday_execution_policy("2026-08-25T15:14:30-04:00")["expires_at"] == (
        "2026-08-25T15:15:00-04:00"
    )
    assert intraday_execution_policy("2026-08-25T14:29:59-04:00")["expires_at"] == (
        "2026-08-25T14:30:00-04:00"
    )
    assert intraday_execution_policy("2026-08-25T10:00:00-04:00")["expires_at"] == (
        "2026-08-25T10:02:00-04:00"
    )


@pytest.mark.parametrize("as_of", ["2026-10-10T10:00:00-04:00", "2026-11-26T10:00:00-05:00"])
def test_execution_policy_holiday_never_allows_entry(as_of):
    policy = intraday_execution_policy(as_of)
    assert policy["state"] == "closed"
    assert policy["ordinary_entry_allowed"] is False
    assert policy["zero_dte_entry_allowed"] is False
    assert policy["close_et"] is None


def test_execution_policy_missing_clock_cannot_guess_normal_day(monkeypatch):
    from types import SimpleNamespace
    from src.services import alert_tiering

    monkeypatch.setattr(alert_tiering, "resolve_xnys_session_clock", lambda _now: SimpleNamespace(
        state="unavailable", unavailable_reason="calendar_unavailable",
    ))
    policy = intraday_execution_policy(_AS_OF)
    assert policy["state"] == "unavailable"
    assert policy["ordinary_entry_allowed"] is None
    assert policy["zero_dte_entry_allowed"] is None
    assert policy["exit_deadline_et"] is None
    assert intraday_execution_policy("2026-08-25T10:00:00")["state"] == "unavailable"


def test_option_skill_cannot_infer_quote_age_from_fetch_time():
    root = Path(__file__).resolve().parents[1]
    for relative in ("strategies/option_trader.yaml", ".claude/skills/option_trader/SKILL.md"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "execution_policy" in source
        assert "quote_as_of" in source
        assert "报价合格" in source and "不等于" in source


# ---------------------------------------------------------------------------
# 措辞禁令（B3-③）：提示链路系统文案禁用「聪明钱/主力/大资金」归因。
# ---------------------------------------------------------------------------

def test_alert_pipeline_copy_never_uses_smart_money_attribution():
    root = Path(__file__).resolve().parents[1]
    banned = ("聪明钱", "主力", "大资金")
    for relative in (
        "src/services/alert_tiering.py",
        "src/services/intraday_opportunity_alerter.py",
        "src/services/intraday_alert_context.py",
        "api/v1/endpoints/journal_push.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        for word in banned:
            # 允许出现在「禁用声明」里：逐行检查，含禁令关键字的行豁免。
            for line in source.splitlines():
                if word in line and "禁用" not in line and "禁令" not in line:
                    pytest.fail(f"{relative} 含违禁措辞 {word!r}: {line.strip()}")

# -*- coding: utf-8 -*-
"""Deterministic tests for the intraday opportunity alerter.

覆盖：六条 v1 规则的触发/不触发与消息文案、盘前时段闸门、黑名单标注、
（标的×规则）日内去重与 ET 日期翻转清零、全局日上限与终止通知、单 tick
批量合并为一条消息、Telegram 失败的安静降级（状态变化才记日志）、有界
队列不阻塞、warm 入口的 observer 接线，以及 lifespan 开/关接线与配置解析。
全部使用夹具载荷 + mock 发送器，零网络零真实时钟依赖。
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import logging
import os
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from api.app import create_app
from src.config import Config
from src.journal.ledger.push_ledger_repository import (
    P0_2M_SHADOW_RESEARCH_RULE,
    PushDayState,
)
from src.services.intraday_opportunity_alerter import (
    ALERT_FOOTER,
    IntradayOpportunityAlerter,
    _OptionSupplementRequest,
    _OutboundMessage,
    _WorkItem,
    detect_intraday_alerts,
)
from src.services.alert_tiering import OPTION_FRICTION_SELECTION_BASIS

_DAY = "2026-08-14"
_AS_OF = "2026-08-14T14:31:00+00:00"  # 10:31 ET


class FakeLedger:
    """In-memory push ledger（测试永不触真实 DB）。"""

    def __init__(
        self,
        day_state=None,
        week_p0=0,
        week_p0x=0,
        week_shadow_p0=0,
        week_shadow_p0x=0,
        fail=False,
        fail_deliveries=False,
    ):
        self.decisions = []
        self.deliveries = []
        self._day_state = day_state or PushDayState()
        self._week_p0 = week_p0
        self._week_p0x = week_p0x
        self._week_shadow_p0 = week_shadow_p0
        self._week_shadow_p0x = week_shadow_p0x
        self.fail = fail
        self.fail_deliveries = fail_deliveries

    def append(self, decisions=(), deliveries=()):
        if self.fail or (deliveries and self.fail_deliveries):
            raise RuntimeError("ledger down")
        self.decisions.extend(decisions)
        self.deliveries.extend(deliveries)

    def load_day_state(self, et_date):
        if self.fail:
            raise RuntimeError("ledger down")
        return self._day_state

    def count_delivered_p0_week(self, et_date):
        if self.fail:
            raise RuntimeError("ledger down")
        return self._week_p0

    def count_delivered_p0x_week(self, et_date):
        if self.fail:
            raise RuntimeError("ledger down")
        return self._week_p0x

    def count_delivered_p0_2m_shadow_research_week(
        self, et_date, *, session_scope
    ):
        if self.fail:
            raise RuntimeError("ledger down")
        return (
            self._week_shadow_p0
            if session_scope == "regular"
            else self._week_shadow_p0x
        )


class FakePersistentLedger(FakeLedger):
    """Shared in-memory outbox used to exercise process-restart recovery."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outbox: dict[str, SimpleNamespace] = {}
        self.transitions: list[tuple[str, str]] = []

    def append(self, decisions=(), deliveries=()):
        super().append(decisions, deliveries)
        self.transitions.extend(
            ("decision", str(getattr(row, "message_key", "")))
            for row in decisions
        )
        self.transitions.extend(
            ("delivery", str(getattr(row, "message_key", "")))
            for row in deliveries
        )

    def upsert_retry(self, **kwargs):
        key = kwargs["message_key"]
        existing = self.outbox.get(key)
        if existing is not None and existing.state != "pending":
            return existing
        created_at = existing.created_at if existing is not None else kwargs["now"]
        row = SimpleNamespace(
            message_key=key,
            et_date=kwargs["et_date"],
            tier=kwargs["tier"],
            text=kwargs["text"],
            silent=kwargs["silent"],
            expires_at=kwargs["expires_at"],
            next_attempt_at=kwargs["next_attempt_at"],
            attempt_count=(
                existing.attempt_count if existing is not None else 0
            ),
            state="pending",
            created_at=created_at,
            updated_at=kwargs["now"],
        )
        self.outbox[key] = row
        self.transitions.append(("upsert", key))
        return row

    def reschedule_retry(self, message_key, *, next_attempt_at, now):
        row = self.outbox.get(message_key)
        if row is None or row.state != "pending":
            return False
        row.next_attempt_at = next_attempt_at
        row.attempt_count += 1
        row.updated_at = now
        self.transitions.append(("reschedule", message_key))
        return True

    def mark_retry_success(
        self, message_key, *, now, delivered_text=None
    ):
        row = self.outbox.get(message_key)
        if row is None or row.state != "pending":
            return False
        row.state = "succeeded"
        if delivered_text is not None:
            row.text = delivered_text
        row.attempt_count += 1
        row.updated_at = now
        self.transitions.append(("success", message_key))
        return True

    def mark_retry_terminal(self, message_key, *, now):
        row = self.outbox.get(message_key)
        if row is None or row.state != "pending":
            return False
        row.state = "terminal"
        row.updated_at = now
        self.transitions.append(("terminal", message_key))
        return True

    def load_pending_retries(self, *, now):
        return tuple(
            row
            for row in self.outbox.values()
            if row.state == "pending" and row.expires_at > now
        )

    def load_expired_retries(self, *, now):
        return tuple(
            row
            for row in self.outbox.values()
            if row.state == "pending" and row.expires_at <= now
        )

    def load_succeeded_retries(self):
        return tuple(
            row for row in self.outbox.values() if row.state == "succeeded"
        )

    def load_message_decisions(self, et_date, message_key):
        return tuple(
            decision
            for decision in self.decisions
            if getattr(decision, "et_date", et_date) == et_date
            and getattr(decision, "message_key", None) == message_key
        )

    def message_has_successful_delivery(self, et_date, message_key):
        return any(
            delivery.et_date == et_date
            and delivery.message_key == message_key
            and delivery.telegram_ok is True
            for delivery in self.deliveries
        )


class AtomicFakePersistentLedger(FakePersistentLedger):
    def append_with_retries(self, decisions, retries, *, now):
        self.append(decisions, ())
        return tuple(
            self.upsert_retry(**retry, now=now) for retry in retries
        )


# ---------------------------------------------------------------------------
# fixture builders（只包含检测读到的字段；缺失字段即诚实缺席）
# ---------------------------------------------------------------------------

def _current_window(*, vol_norm=2.3, score=3.1, thrust=1.2, direction="up"):
    return {
        "start_et": "10:15",
        "end_et": "10:30",
        "thrust_percent": thrust,
        "thrust_norm": 1.4,
        "vol_norm": vol_norm,
        "score": score,
        "direction": direction,
    }


def _leg(*, start_et="10:15", score=12.4, direction="up", grade="strong"):
    return {
        "start_et": start_et,
        "end_et": "10:30",
        "score": score,
        "direction": direction,
        "grade": grade,
    }


def _bursts(*, current=None, legs=(), state="ready"):
    return {"state": state, "current": current, "legs": list(legs)}


def _displacement(*, net, basis="atr14_daily", state="ready", line=0.5):
    return {
        "state": state,
        "net_move_atr": net,
        "atr_basis": basis,
        "survival_line_atr": line,
    }


def _candidate(
    ticker,
    *,
    bursts=None,
    displacement=None,
    atr14=None,
    pre_change=None,
    option_activity=None,
):
    row = {
        "ticker": ticker,
        "session_bursts": bursts if bursts is not None else _bursts(state="unavailable"),
        "recent_displacement": (
            displacement
            if displacement is not None
            else {"state": "unavailable", "net_move_atr": None}
        ),
        "atr14": atr14,
        "pre_change_percent": pre_change,
    }
    if option_activity is not None:
        row["option_activity"] = option_activity
    return row


def _payload(
    *,
    session_state="regular",
    quote_scope="current_session",
    candidates=(),
    snapshot_only=(),
    lane=None,
    market_date=_DAY,
    as_of=_AS_OF,
):
    payload = {
        "market_date_et": market_date,
        "as_of": as_of,
        "session_state": session_state,
        "quote_session_scope": quote_scope,
        "candidates": list(candidates),
        "universe_scan": {"snapshot_only": list(snapshot_only)},
    }
    if lane is not None:
        payload["lane_availability"] = lane
    return payload


class RecordingSender:
    def __init__(self, result=True):
        self.messages: list[str] = []
        self.result = result
        self.delivered = threading.Event()

    def __call__(self, text: str) -> bool:
        self.messages.append(text)
        self.delivered.set()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _sync_alerter(sender, *, ledger=None, silent_sender=None, **kwargs):
    return IntradayOpportunityAlerter(
        sender,
        send_silent_text=silent_sender,
        async_send=False,
        ledger=ledger if ledger is not None else FakeLedger(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 规则 1：高开/盘前异动
# ---------------------------------------------------------------------------

def test_premarket_move_fires_only_in_premarket_phase():
    row = {"ticker": "TSLA", "pre_change_percent": 2.4}
    premarket = _payload(session_state="premarket", snapshot_only=[row])
    alerts = detect_intraday_alerts(premarket, seen=set())
    assert [a.text for a in alerts] == ["TSLA 盘前 +2.4%"]

    # 低于 2.0% 不触发。
    weak = _payload(
        session_state="premarket",
        snapshot_only=[{"ticker": "TSLA", "pre_change_percent": 1.9}],
    )
    assert detect_intraday_alerts(weak, seen=set()) == []

    # 常规时段绝不按盘前口径提示（时段闸门）。
    regular = _payload(session_state="regular", snapshot_only=[row])
    assert detect_intraday_alerts(regular, seen=set()) == []


def test_premarket_move_reads_promoted_deep_candidates_first():
    payload = _payload(
        session_state="premarket",
        candidates=[_candidate("NVDA", pre_change=-3.1)],
        snapshot_only=[{"ticker": "TSLA", "pre_change_percent": 2.4}],
    )
    alerts = detect_intraday_alerts(payload, seen=set())
    assert [a.text for a in alerts] == ["NVDA 盘前 -3.1%", "TSLA 盘前 +2.4%"]


# ---------------------------------------------------------------------------
# 规则 6：盘前期权大单（option_activity.max_single_turnover ≥ $1M）
# ---------------------------------------------------------------------------

def _option_activity(max_single_turnover):
    return {
        "state": "ready",
        "count": 3,
        "dominant_sentiment": "bullish",
        "max_single_turnover": max_single_turnover,
    }


def test_premarket_large_print_fires_only_in_premarket():
    fires = _payload(
        session_state="premarket",
        candidates=[
            _candidate("NVDA", option_activity=_option_activity(1_200_000.0))
        ],
    )
    alerts = detect_intraday_alerts(fires, seen=set())
    assert [a.text for a in alerts] == [
        "NVDA 期权大单 $1.2M（上一时段异动页最大单笔；来源可能是对冲/库存流，不构成方向）"
    ]

    # 低于 $1M 不触发；标缺（None）绝不当 0 或当命中处理。
    for turnover in (999_999.0, None):
        quiet = _payload(
            session_state="premarket",
            candidates=[
                _candidate("NVDA", option_activity=_option_activity(turnover))
            ],
        )
        assert detect_intraday_alerts(quiet, seen=set()) == []

    # 常规时段不触发：这条规则是盘前简报，不是盘中事实流。
    regular = _payload(
        candidates=[
            _candidate("NVDA", option_activity=_option_activity(2_000_000.0))
        ],
    )
    texts = [a.text for a in detect_intraday_alerts(regular, seen=set())]
    assert not any("期权大单" in text for text in texts)


def test_premarket_large_print_dedupes_per_ticker_per_day():
    payload = _payload(
        session_state="premarket",
        candidates=[
            _candidate("NVDA", option_activity=_option_activity(1_500_000.0))
        ],
    )
    seen: set[tuple[str, ...]] = set()
    first = detect_intraday_alerts(payload, seen=seen)
    assert [a.text for a in first] == [
        "NVDA 期权大单 $1.5M（上一时段异动页最大单笔；来源可能是对冲/库存流，不构成方向）"
    ]
    # 同一事实当日不再重复；去重键随载荷重放保持稳定。
    assert detect_intraday_alerts(payload, seen=seen) == []


# ---------------------------------------------------------------------------
# 规则 2：放量
# ---------------------------------------------------------------------------

def test_volume_burst_thresholds_and_message():
    fires = _payload(
        candidates=[
            _candidate(
                "NVDA",
                bursts=_bursts(
                    current=_current_window(vol_norm=2.0, score=2.5, thrust=1.2)
                ),
            )
        ]
    )
    alerts = detect_intraday_alerts(fires, seen=set())
    assert [a.text for a in alerts] == [
        "NVDA 放量 2.0× · 15分推力 +1.2% · 爆发分 2.5"
    ]

    # 任一阈值不满足都不触发（vol_norm < 2.0 / score < 2.5）。
    for current in (
        _current_window(vol_norm=1.9, score=3.0),
        _current_window(vol_norm=2.4, score=2.4),
    ):
        payload = _payload(
            candidates=[_candidate("NVDA", bursts=_bursts(current=current))]
        )
        assert detect_intraday_alerts(payload, seen=set()) == []

    # 盘前时段没有常规时段量比可言：规则 2 只在 regular 触发。
    premarket = _payload(
        session_state="premarket",
        candidates=[
            _candidate("NVDA", bursts=_bursts(current=_current_window()))
        ],
    )
    texts = [a.text for a in detect_intraday_alerts(premarket, seen=set())]
    assert all("放量" not in text for text in texts)


# ---------------------------------------------------------------------------
# 规则 3：强波段入账
# ---------------------------------------------------------------------------

def test_strong_leg_fires_once_per_leg_start():
    seen: set = set()
    payload = _payload(
        candidates=[_candidate("NVDA", bursts=_bursts(legs=[_leg()]))]
    )
    alerts = detect_intraday_alerts(payload, seen=seen)
    assert [a.text for a in alerts] == ["NVDA 强波段 10:15 分 12 向上"]

    # 同一波段第二次观察不再触发；新波段（新的 start_et）触发。
    assert detect_intraday_alerts(payload, seen=seen) == []
    grown = _payload(
        candidates=[
            _candidate(
                "NVDA",
                bursts=_bursts(
                    legs=[
                        _leg(),
                        _leg(start_et="11:30", score=9.0, direction="down"),
                    ]
                ),
            )
        ]
    )
    alerts = detect_intraday_alerts(grown, seen=seen)
    assert [a.text for a in alerts] == ["NVDA 强波段 11:30 分 9 向下"]


def test_medium_grade_leg_never_alerts():
    payload = _payload(
        candidates=[
            _candidate("NVDA", bursts=_bursts(legs=[_leg(grade="medium")]))
        ]
    )
    assert detect_intraday_alerts(payload, seen=set()) == []


# ---------------------------------------------------------------------------
# 规则 4：位移达标
# ---------------------------------------------------------------------------

def test_displacement_dollarizes_only_on_daily_atr_basis():
    daily = _payload(
        candidates=[
            _candidate(
                "NVDA",
                displacement=_displacement(net=0.65),
                atr14=5.0,
            )
        ]
    )
    alerts = detect_intraday_alerts(daily, seen=set())
    assert [a.text for a in alerts] == ["NVDA 近30分位移 +$3.25（+0.65 ATR）"]

    # 非日线 ATR 标尺不可换算美元：只报 ATR 值，绝不硬算。
    proxy = _payload(
        candidates=[
            _candidate(
                "MRVL",
                displacement=_displacement(
                    net=-0.7, basis="intraday_20bar_proxy_x3"
                ),
                atr14=None,
            )
        ]
    )
    alerts = detect_intraday_alerts(proxy, seen=set())
    assert [a.text for a in alerts] == ["MRVL 近30分位移 -0.70 ATR"]


def test_displacement_once_per_direction_and_below_line_silent():
    seen: set = set()
    up = _payload(
        candidates=[
            _candidate("NVDA", displacement=_displacement(net=0.62), atr14=5.0)
        ]
    )
    assert len(detect_intraday_alerts(up, seen=seen)) == 1
    assert detect_intraday_alerts(up, seen=seen) == []

    below = _payload(
        candidates=[
            _candidate("NVDA", displacement=_displacement(net=0.3), atr14=5.0)
        ]
    )
    assert detect_intraday_alerts(below, seen=set()) == []

    down = _payload(
        candidates=[
            _candidate("NVDA", displacement=_displacement(net=-0.55), atr14=5.0)
        ]
    )
    assert len(detect_intraday_alerts(down, seen=seen)) == 1


def test_current_legacy_5m_fields_never_emit_or_enter_p2_tiering():
    legacy_bursts = _bursts(
        current=_current_window(vol_norm=9.0, score=99.0),
        legs=[_leg(score=99.0)],
    )
    legacy_bursts.update(
        legacy=True,
        legacy_usage="api_compatibility_and_research_display_only",
    )
    payload = _payload(
        candidates=[
            _candidate(
                "NVDA",
                bursts=legacy_bursts,
                displacement=_displacement(net=2.0),
                atr14=5.0,
            )
        ]
    )

    assert detect_intraday_alerts(payload, seen=set()) == []


# ---------------------------------------------------------------------------
# 规则 5：0-7DTE 日内池提醒
# ---------------------------------------------------------------------------

def test_intraday_policy_alert_waits_for_known_and_fires_once():
    seen: set = set()
    unknown = _payload(lane={"policy_state": "unknown"})
    assert detect_intraday_alerts(unknown, seen=seen) == []

    available = _payload(lane={"policy_state": "available"})
    alerts = detect_intraday_alerts(available, seen=seen)
    assert [a.text for a in alerts] == [
        "今日 0-7DTE 日内合约池有可用到期；所有候选必须当日平仓"
    ]
    assert detect_intraday_alerts(available, seen=seen) == []

    unavailable = _payload(lane={"policy_state": "unavailable"})
    alerts = detect_intraday_alerts(unavailable, seen=set())
    assert [a.text for a in alerts] == [
        "今日深度层标的在 0-7DTE 窗口内无可用到期"
    ]


# ---------------------------------------------------------------------------
# 黑名单：只标注，不压制
# ---------------------------------------------------------------------------

def test_blacklist_ticker_is_tagged_not_suppressed():
    payload = _payload(
        session_state="premarket",
        snapshot_only=[{"ticker": "QQQ", "pre_change_percent": 2.5}],
    )
    alerts = detect_intraday_alerts(payload, seen=set())
    assert [a.text for a in alerts] == ["QQQ 盘前 +2.5% ⚠️ 你的历史亏钱标的"]


# ---------------------------------------------------------------------------
# 四级出口（Phase B）：P2 事实只入台账 / 去重翻转与重启重建 / 全局上限
# ---------------------------------------------------------------------------

def _lane(tickers=("NVDA",), day_type="overnight_only", dte=(4,)):
    return {
        "day_type": day_type,
        "policy_state": "available" if dte else "unavailable",
        "tickers": [
            {
                "ticker": t,
                "state": "ready",
                "available_dte_list": list(dte),
                "has_zero_dte": 0 in dte,
            }
            for t in tickers
        ],
    }


def _tier_candidate(ticker="NVDA", *, friction=True, earnings="clear", net=0.8):
    """P0 七条件夹具：默认除条件③统计性外全过（friction 注入 additive 字段）。"""
    row = {
        "ticker": ticker,
        "session_bursts": _bursts(
            current=_current_window(score=9.0), legs=[_leg()]
        ),
        "recent_displacement": _displacement(net=net),
        "atr14": 5.0,
        "last_price": 100.0,
    }
    if earnings == "clear":
        row["earnings_proximity"] = {
            "state": "ready", "within_blackout": False, "days_to_earnings": None,
        }
    elif earnings == "blackout":
        row["earnings_proximity"] = {
            "state": "ready", "within_blackout": True, "days_to_earnings": 0,
        }
    # earnings == "unknown"：字段缺席（标缺）。
    if friction:
        row["option_contract_friction"] = {
            "state": "ready",
            "source": "moomoo_near_expiry_chain",
            "selection_basis": OPTION_FRICTION_SELECTION_BASIS,
            "contract_code": f"US.{ticker}260820C00100000",
            "right": "C",
            "strike": 100.0,
            "expiry": "2026-08-20",
            "dte": 6,
            "underlying_spot": 100.0,
            "bid": 4.9,
            "ask": 5.1,
            "mid": 5.0,
            "spread_percent": 4.0,
            "delta": 0.5,
            "quote_as_of": "2026-08-14T14:30:55+00:00",
            "fetched_at": _AS_OF,
            "quote_age_seconds": 5.0,
        }
    return row


def _tier_payload(ticker="NVDA", **kwargs):
    candidate = _tier_candidate(ticker, **kwargs)
    payload = _payload(candidates=[candidate], lane=_lane((ticker,)))
    payload["universe_scan"]["plan_always_include"] = [ticker]
    return payload


def _next_tier_bar(payload):
    """Return the same evidence advanced by one completed 5m bar."""

    advanced = copy.deepcopy(payload)
    advanced["as_of"] = "2026-08-14T14:36:00+00:00"
    for candidate in advanced.get("candidates") or ():
        current = (candidate.get("session_bursts") or {}).get("current")
        if isinstance(current, dict):
            current["start_et"] = "10:20"
            current["end_et"] = "10:35"
    return advanced


def _p0_2m_candidate(
    ticker="NVDA",
    *,
    direction="up",
    session_scope="regular",
    state="qualified",
    window_end=None,
    displacement_atr=0.62,
    price_percentile=0.9833,
    rvol_8m=2.1,
    **extra,
):
    if window_end is None:
        window_end = {
            "premarket": "2026-08-14T09:18:00-04:00",
            "regular": "2026-08-14T10:48:00-04:00",
            "afterhours": "2026-08-14T16:18:00-04:00",
        }[session_scope]
    qualified = state == "qualified"
    evidence = {
        "state": state,
        "alert_qualified": qualified,
        "alert_tier": (
            "P0" if session_scope == "regular" else "P0X"
        ) if qualified else None,
        "signal_version": "p0_2m_signal_v1",
        "symbol": ticker,
        "universe_hash": "sha256:test-universe",
        "session_scope": session_scope,
        "window_start": "2026-08-14T10:30:00-04:00",
        "window_end": window_end,
        "direction": direction,
        "trigger_price": 101.25,
        "impulse_8m_log": 0.012 if direction == "up" else -0.012,
        "impulse_8m_percent": 1.21 if direction == "up" else -1.21,
        "price_percentile": price_percentile,
        "rvol_8m": rvol_8m,
        "displacement_18m_percent": 1.55 if direction == "up" else -1.55,
        "displacement_atr": displacement_atr,
        "prior_daily_atr14": 2.5,
        "baseline_count": 60,
        "baseline_first_date": "2026-05-20",
        "baseline_last_date": "2026-08-13",
        "source": "MoomooFetcher",
        "data_quality": {"status": "complete", "issues": []},
        "option_candidates": [],
        "failed_checks": [],
        "unavailable_reason": None,
        "basis": "strict_test_contract",
    }
    if state == "forming":
        evidence.update(
            alert_qualified=False,
            alert_tier=None,
            displacement_atr=0.4,
            unavailable_reason="thresholds_not_met",
            failed_checks=["displacement_atr_below_0.50"],
        )
    elif state == "not_qualified":
        evidence.update(
            alert_qualified=False,
            alert_tier=None,
            unavailable_reason="thresholds_not_met",
            price_percentile=0.8,
            failed_checks=["price_percentile_below_0.95"],
        )
    row = {"ticker": ticker, "p0_signal_evidence_v2": evidence}
    row.update(extra)
    return row


def _p0_2m_payload(
    *candidates,
    mobile_enabled=True,
    mobile_reason="calibration_gates_passed",
    as_of="2026-08-14T14:48:05+00:00",
):
    selected = [
        {
            **dict(candidate),
            "p0_mobile_eligible": candidate.get(
                "p0_mobile_eligible", mobile_enabled
            ),
        }
        for candidate in (candidates or [_p0_2m_candidate()])
    ]
    return {
        "schema_version": "intraday_p0_2m_scan_v1",
        "market_date_et": _DAY,
        "as_of": as_of,
        "alert_observed_at": as_of,
        "session_state": "regular",
        "quote_session_scope": "current_session",
        "candidates": selected,
        "universe_scan": {
            "mode": "p0_2m_full_watchlist",
            "watchlist_total": len(selected),
            "watchlist_scanned": len(selected),
            "watchlist_truncated": False,
            "universe_hash": "sha256:test-universe",
        },
        "p0_mobile_delivery": {
            "enabled": mobile_enabled,
            "reason": mobile_reason,
        },
    }


def _opening_candidate(
    ticker="TSLA",
    *,
    score=4.2,
    option_contract=None,
):
    impulse = {
        "state": "qualified",
        "alert_qualified": True,
        "rule_version": "opening_impulse_v1",
        "bar_start_et": "09:30",
        "bar_end_et": "09:35",
        "open": 347.21,
        "high": 354.855,
        "low": 347.15,
        "close": 352.56,
        "volume": 1_644_899,
        "dollar_volume": 579_925_202.44,
        "direction": "up",
        "body_percent": 1.540854,
        "body_atr": 0.409962,
        "range_atr": 0.590421,
        "body_efficiency": 0.694354,
        "directional_close_location": 0.702141,
        "opening_volume_rvol_20d": 1.3903,
        "opening_range_ratio_20d": 2.172,
        "volume_percentile_20d": 0.90,
        "range_percentile_20d": 0.95,
        "reference_session_count": 20,
        "reference_median_volume": 1_183_100,
        "prior_close": 348.75,
        "gap_percent": -0.441577,
        "closed_vs_prior_percent": 1.092473,
        "reclaimed_prior_close": True,
        "opening_mid": 351.0025,
        "quality_score": score,
        "failed_checks": [],
        "basis": "same_slot",
        "limitations": [],
    }
    final_1m = copy.deepcopy(impulse)
    final_1m.update(
        rule_version="opening_impulse_v2",
        checkpoint=5,
        checkpoint_label="09:35",
        confirmation_mode="completed_1m_cumulative",
        confirmation_points=[5],
        confirmation_labels=["09:35"],
        current_checkpoint_pass=True,
        retryable_pending=False,
    )
    row = _candidate(
        ticker,
        bursts={
            "state": "insufficient_bars",
            "current": None,
            "legs": [],
            # v1 stays present only as a compatibility field; all alerting
            # reads the strict completed-1m v2 profile below.
            "opening_impulse": impulse,
            "opening_progressive": final_1m,
        },
        atr14=13.05,
    )
    if option_contract is not None:
        row["option_contract_friction"] = option_contract
    return row


def _opening_payload(*candidates):
    payload = _payload(
        market_date="2026-08-31",
        as_of="2026-08-31T13:35:05+00:00",
        candidates=list(candidates or [_opening_candidate()]),
    )
    payload["universe_scan"].update(
        plan_always_include=[],
        user_pinned=[candidate["ticker"] for candidate in payload["candidates"]],
        user_focus=[],
    )
    return payload


def _progressive_opening_candidate(
    ticker="TSLA",
    *,
    checkpoint=2,
    score=4.2,
):
    """Qualified v2 fixture: two adjacent cumulative 1m passes, same side."""

    row = _opening_candidate(ticker, score=score)
    progressive = copy.deepcopy(row["session_bursts"]["opening_impulse"])
    progressive.update(
        rule_version="opening_impulse_v2",
        checkpoint=checkpoint,
        checkpoint_label=f"09:{30 + checkpoint:02d}",
        bar_end_et=f"09:{30 + checkpoint:02d}",
        confirmation_mode="consecutive_1m",
        confirmation_points=[checkpoint - 1, checkpoint],
        confirmation_labels=[
            f"09:{29 + checkpoint:02d}",
            f"09:{30 + checkpoint:02d}",
        ],
        current_checkpoint_pass=True,
        retryable_pending=False,
    )
    row["session_bursts"] = {
        "state": "opening_progressive",
        "current": None,
        "legs": [],
        "opening_progressive": progressive,
    }
    return row


def _progressive_opening_payload(*candidates, checkpoint=2):
    minute = 30 + checkpoint
    payload = _payload(
        market_date="2026-08-31",
        as_of=f"2026-08-31T13:{minute:02d}:05+00:00",
        candidates=list(
            candidates or [_progressive_opening_candidate(checkpoint=checkpoint)]
        ),
    )
    payload["alert_observed_at"] = payload["as_of"]
    payload["opening_progressive"] = True
    payload["universe_scan"].update(
        plan_always_include=[],
        user_pinned=[candidate["ticker"] for candidate in payload["candidates"]],
        user_focus=[],
    )
    return payload


def _final_v2_opening_candidate(ticker="TSLA", *, score=4.2):
    """09:35 shape: compatibility v1 plus strict completed-1m v2."""

    row = _opening_candidate(ticker, score=score)
    native = row["session_bursts"]["opening_impulse"]
    final = copy.deepcopy(native)
    final.update(
        rule_version="opening_impulse_v2",
        checkpoint=5,
        checkpoint_label="09:35",
        confirmation_mode="completed_1m_cumulative",
        confirmation_points=[5],
        confirmation_labels=["09:35"],
        current_checkpoint_pass=True,
        retryable_pending=False,
    )
    row["session_bursts"]["opening_progressive"] = final
    return row


def _opening_contract(ticker="TSLA"):
    return {
        "state": "ready",
        "source": "moomoo_near_expiry_chain",
        "selection_basis": OPTION_FRICTION_SELECTION_BASIS,
        "contract_code": f"US.{ticker}260831C00355000",
        "right": "C",
        "strike": 355.0,
        "expiry": "2026-08-31",
        "dte": 0,
        "underlying_spot": 352.56,
        "bid": 3.80,
        "ask": 4.00,
        "mid": 3.90,
        "spread_percent": 5.128205,
        "delta": 0.48,
        "quote_as_of": "2026-08-31T13:35:08+00:00",
        "fetched_at": "2026-08-31T13:35:09+00:00",
        "quote_age_seconds": 1.0,
        "volume": 126,
        "open_interest": 940,
    }


def test_opening_impulse_rings_at_0935_with_detailed_underlying_context():
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        silent_sender=silent,
        ledger=ledger,
        daily_pnl_provider=lambda _et_date: 0.0,
    )

    alerter.observe_opening(_opening_payload())

    assert len(sender.messages) == 1
    assert silent.messages == []
    message = sender.messages[0]
    assert message.startswith(
        "【P1-O 开盘异动 · 待确认 · 响铃 · 09:35 ET】"
    )
    assert "TSLA ↑ 开盘前 5 根完整 1m 累计异常" in message
    assert "O 347.21 · H 354.86 · L 347.15 · C 352.56" in message
    assert "RVOL 1.39× / P90" in message
    assert "估算名义额 $579.93M" in message
    assert "开盘累计至 09:35 收复前收" in message
    assert "尚非 P0" in message
    assert "首报不等待期权链" in message
    assert message.count(ALERT_FOOTER) == 1
    rows = [d for d in ledger.decisions if d.rule == "opening_impulse_v2"]
    assert len(rows) == 1
    assert rows[0].tier == "P1"
    assert rows[0].delivery_channel == "telegram"
    assert rows[0].suppressed is False
    assert rows[0].message_key
    assert rows[0].message_key == ledger.deliveries[0].message_key
    assert ledger.deliveries[0].telegram_ok is True
    assert alerter._tier_state.opening_delivered_today == 1
    assert alerter._tier_state.p1_delivered_today == 0
    assert alerter._tier_state.p0_delivered_today == 0


def test_opening_r6_fuse_still_sends_fact_but_withholds_option_contract():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        daily_pnl_provider=lambda _et_date: -13_000.0,
    )

    alerter.observe_opening(
        _opening_payload(
            _opening_candidate(option_contract=_opening_contract())
        )
    )

    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert "TSLA ↑ 开盘前 5 根完整 1m 累计异常" in message
    assert "R6：当日保险丝已触发" in message
    assert "US.TSLA260831C00355000" not in message
    row = next(d for d in ledger.decisions if d.rule == "opening_impulse_v2")
    assert row.tier == "P1" and not row.suppressed
    assert row.option_mid is None and row.option_spread_pct is None


def test_opening_unknown_r6_withholds_contract_and_action_copy():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger, daily_pnl_provider=lambda _date: None)
    alerter.observe_opening(_opening_payload(
        _opening_candidate(option_contract=_opening_contract())
    ))
    assert len(sender.messages) == 1
    assert "R6：状态未知" in sender.messages[0]
    assert "US.TSLA260831C00355000" not in sender.messages[0]
    assert "只挂限价" not in sender.messages[0]
    assert "观察锚" not in sender.messages[0]
    assert "手动执行时窗" not in sender.messages[0]
    row = next(d for d in ledger.decisions if d.rule == "opening_impulse_v2")
    assert row.option_mid is None


def test_opening_quote_reference_is_not_permission_before_0945():
    sender = RecordingSender()
    alerter = _sync_alerter(sender, daily_pnl_provider=lambda _date: 0.0)
    alerter.observe_opening(_opening_payload(
        _opening_candidate(option_contract=_opening_contract())
    ))
    assert "报价合格参考（非执行许可）" in sender.messages[0]
    assert "1–7DTE 关闭 / 0DTE 关闭" in sender.messages[0]
    assert "起点 09:45 ET" in sender.messages[0]


def test_consecutive_same_direction_progressive_profile_uses_opening_observer():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)

    alerter.observe_opening(_progressive_opening_payload())

    assert len(sender.messages) == 1
    rows = [d for d in ledger.decisions if d.rule == "opening_impulse_v2"]
    assert len(rows) == 1
    assert rows[0].suppressed is False
    assert rows[0].dedupe_key == ("opening_setup_v2", "TSLA", "09:30")
    assert rows[0].conditions[0]["evidence"]["confirmation_labels"] == [
        "09:31",
        "09:32",
    ]
    message = sender.messages[0]
    assert "开盘累计至 09:32 收复前收" in message
    assert "累计中点" in message
    assert "首根收复前收" not in message
    assert alerter._tier_state.opening_delivered_today == 1
    # The dedicated opening observer must not mutate strict-2m crossing state.
    assert alerter._tier_state.p0_episode_last_window == {}


def test_progressive_v2_and_final_completed_1m_share_canonical_dedupe():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)

    alerter.observe_opening(_progressive_opening_payload())
    alerter.observe_opening(_opening_payload())

    assert len(sender.messages) == 1
    primary = [
        row
        for row in ledger.decisions
        if row.rule in {"opening_impulse_v1", "opening_impulse_v2"}
    ]
    assert [row.rule for row in primary] == ["opening_impulse_v2"]
    assert ("opening_setup_v2", "TSLA", "09:30") in alerter._seen
    assert alerter._tier_state.opening_delivered_today == 1


def test_final_v2_message_says_completed_1m_and_never_native_5m():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)

    alerter.observe_opening(
        _opening_payload(_final_v2_opening_candidate())
    )

    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert "TSLA ↑ 开盘前 5 根完整 1m 累计异常（09:30–09:35）" in message
    assert "首根 5m 尚未完成" not in message
    assert "首根5m尚未完成" not in message
    assert "未读取原生 5m" in message
    rows = [row for row in ledger.decisions if row.rule == "opening_impulse_v2"]
    assert len(rows) == 1
    assert "不读取原生 5m" in rows[0].conditions[0]["detail"]


def test_async_worker_suppresses_early_checkpoint_after_its_minute():
    sender = RecordingSender()
    ledger = FakeLedger()
    clock = {
        "now": datetime.fromisoformat("2026-08-31T13:32:05+00:00")
    }
    alerter = IntradayOpportunityAlerter(
        sender,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: clock["now"],
    )
    alerter._day = "2026-08-31"
    item = alerter._build_work_item(
        _progressive_opening_payload(),
        "2026-08-31",
        alerter._context,
        opening_only=True,
    )
    assert item is not None
    assert item.messages[0].expires_at == datetime.fromisoformat(
        "2026-08-31T09:33:00-04:00"
    )

    clock["now"] = datetime.fromisoformat("2026-08-31T13:33:00+00:00")
    alerter._process(item)

    assert sender.messages == []
    assert ledger.deliveries == []
    assert len(ledger.decisions) == 1
    assert ledger.decisions[0].suppressed is True
    assert (
        ledger.decisions[0].suppressed_reason
        == "opening_checkpoint_expired_before_send"
    )
    assert alerter._sent_count == 0
    assert alerter._tier_state.opening_delivered_today == 0


def test_opening_first_ring_does_not_wait_for_option_and_later_update_is_silent():
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        silent_sender=silent,
        ledger=ledger,
        daily_pnl_provider=lambda _et_date: 0.0,
    )
    alerter.observe_opening(_opening_payload())

    enriched = _opening_candidate(option_contract=_opening_contract())
    alerter.observe(_opening_payload(enriched))

    assert len(sender.messages) == 1
    assert len(silent.messages) == 1
    assert silent.messages[0].startswith(
        "【P1-O 合约补充 · 静默 · 09:35 ET】"
    )
    assert "US.TSLA260831C00355000" in silent.messages[0]
    assert "Bid/Ask/Mid $3.80/$4.00/$3.90" in silent.messages[0]
    assert "点差 5.13%" in silent.messages[0]
    assert "不代表 opening range 已突破" in silent.messages[0]
    assert alerter._sent_count == 2
    assert alerter._tier_state.p1_delivered_today == 0


def test_opening_option_update_obeys_global_cap_but_not_p1_budget():
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        silent_sender=silent,
        ledger=ledger,
        max_alerts_per_day=1,
        daily_pnl_provider=lambda _et_date: 0.0,
    )
    alerter.observe_opening(_opening_payload())
    alerter.observe(
        _opening_payload(_opening_candidate(option_contract=_opening_contract()))
    )

    assert len(sender.messages) == 1
    assert silent.messages == []
    suppressed = [
        row
        for row in ledger.decisions
        if row.rule == "opening_option_update"
    ]
    assert len(suppressed) == 1
    assert suppressed[0].suppressed_reason == "daily_cap_reached"
    assert alerter._tier_state.p1_delivered_today == 0


def test_opening_candidates_are_ranked_before_independent_daily_budget():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)
    payload = _opening_payload(
        _opening_candidate("AAA", score=1.0),
        _opening_candidate("BBB", score=5.0),
        _opening_candidate("CCC", score=3.0),
        _opening_candidate("DDD", score=4.0),
    )
    alerter.observe_opening(payload)

    assert len(sender.messages) == 1  # 三个候选合并一条响铃
    message = sender.messages[0]
    assert all(ticker in message for ticker in ("BBB", "DDD", "CCC"))
    assert "AAA" not in message
    suppressed = [
        d
        for d in ledger.decisions
        if d.suppressed_reason == "opening_budget_exhausted"
    ]
    assert [d.ticker for d in suppressed] == ["AAA"]


def test_opening_restart_rebuild_prevents_duplicate_ring():
    day_state = PushDayState(
        seen_keys={("opening_setup_v2", "TSLA", "09:30")},
        telegram_alert_count=1,
        opening_delivered=1,
    )
    sender = RecordingSender()
    ledger = FakeLedger(day_state=day_state)
    alerter = _sync_alerter(sender, ledger=ledger)
    alerter.observe_opening(_opening_payload())
    assert sender.messages == []
    assert ledger.decisions == []
    assert alerter._tier_state.opening_delivered_today == 1


def test_opening_restart_restores_pending_option_update():
    day_state = PushDayState(
        seen_keys={
            ("opening_setup_v2", "TSLA", "09:30"),
            ("opening_option_pending", "TSLA"),
        },
        telegram_alert_count=1,
        opening_delivered=1,
    )
    silent = RecordingSender()
    alerter = _sync_alerter(
        RecordingSender(),
        silent_sender=silent,
        ledger=FakeLedger(day_state=day_state),
        daily_pnl_provider=lambda _et_date: 0.0,
    )
    alerter.observe(
        _opening_payload(_opening_candidate(option_contract=_opening_contract()))
    )
    assert len(silent.messages) == 1
    assert "US.TSLA260831C00355000" in silent.messages[0]
    assert alerter._tier_state.p1_delivered_today == 0


def test_opening_restart_with_ready_option_does_not_duplicate_update():
    day_state = PushDayState(
        seen_keys={
            ("opening_setup_v2", "TSLA", "09:30"),
            ("opening_option_update", "TSLA"),
        },
        telegram_alert_count=1,
        opening_delivered=1,
    )
    silent = RecordingSender()
    ledger = FakeLedger(day_state=day_state)
    alerter = _sync_alerter(
        RecordingSender(),
        silent_sender=silent,
        ledger=ledger,
    )
    alerter.observe(
        _opening_payload(_opening_candidate(option_contract=_opening_contract()))
    )
    assert silent.messages == []
    assert ledger.decisions == []


def test_v1_facts_are_p2_ledger_rows_not_telegram():
    """Phase B 重构核心断言：六条 v1 规则不再直推 Telegram，而是 P2 行。"""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)
    alerter.observe(
        _payload(
            candidates=[
                _candidate("NVDA", bursts=_bursts(current=_current_window())),
                _candidate(
                    "TSLA",
                    bursts=_bursts(
                        current=_current_window(vol_norm=2.8, score=4.0, thrust=-2.1)
                    ),
                ),
            ]
        )
    )
    assert sender.messages == []  # 零 Telegram
    rows = [(d.tier, d.rule, d.ticker) for d in ledger.decisions]
    assert ("P2", "volume_burst", "NVDA") in rows
    assert ("P2", "volume_burst", "TSLA") in rows
    for decision in ledger.decisions:
        assert decision.delivery_channel == "web_only"
        assert not decision.suppressed


def test_daily_dedupe_clears_on_et_date_rollover():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)
    payload = _payload(
        candidates=[_candidate("NVDA", bursts=_bursts(current=_current_window()))]
    )
    alerter.observe(payload)
    alerter.observe(payload)
    assert len([d for d in ledger.decisions if d.rule == "volume_burst"]) == 1

    next_day = _payload(
        market_date="2026-08-17",
        candidates=[_candidate("NVDA", bursts=_bursts(current=_current_window()))],
    )
    alerter.observe(next_day)
    assert len([d for d in ledger.decisions if d.rule == "volume_burst"]) == 2


def test_restart_rebuilds_dedupe_and_budgets_from_ledger():
    """B2：重启不再重复——当日去重键与预算计数从台账重建。"""
    day_state = PushDayState(
        seen_keys={("volume_burst", "NVDA")},
        telegram_alert_count=2,
        p0_delivered=1,
        p1_delivered=1,
    )
    ledger = FakeLedger(day_state=day_state, week_p0=1)
    alerter = _sync_alerter(RecordingSender(), ledger=ledger)
    alerter.observe(
        _payload(
            candidates=[_candidate("NVDA", bursts=_bursts(current=_current_window()))]
        )
    )
    # 台账已见的事实不再产生新行（重启防重复）。
    assert all(d.rule != "volume_burst" for d in ledger.decisions)
    assert alerter._tier_state.p0_delivered_today == 1
    assert alerter._tier_state.p0_delivered_week == 1
    assert alerter._tier_state.p1_delivered_today == 1


def test_restart_rebuild_failure_rejects_until_state_can_be_restored(caplog):
    """台账不可读不得重置去重和预算；恢复后下一观察重新加载。"""
    ledger = FakeLedger(fail=True)
    alerter = _sync_alerter(RecordingSender(), ledger=ledger)
    with caplog.at_level(
        logging.WARNING, logger="src.services.intraday_opportunity_alerter"
    ):
        accepted = alerter.observe(
            _payload(
                candidates=[
                    _candidate("NVDA", bursts=_bursts(current=_current_window()))
                ]
            )
        )
    assert any("rebuild failed" in r.getMessage() for r in caplog.records)
    assert accepted is False
    assert alerter._day_state_rebuilt is False
    assert ledger.decisions == []
    ledger.fail = False
    assert alerter.observe(_p0_2m_payload()) is True
    assert alerter._day_state_rebuilt is True


def test_strict_2m_p0_rings_before_async_option_supplement():
    """标的 P0 先响铃；0–7DTE 候选之后静默补充且不改信号。"""
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()

    def option_provider(ticker, direction):
        assert ticker == "NVDA" and direction == "up"
        assert len(sender.messages) == 1  # 标的首报已经完成
        return {
            "state": "ready",
            "candidates": [
                {
                    "contract_code": "US.NVDA260814C00100000",
                    "expiry": "2026-08-14",
                    "dte": 0,
                    "right": "C",
                    "strike": 100.0,
                    "mid": 2.0,
                    "spread_percent": 4.0,
                    "delta": 0.52,
                }
            ],
        }

    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=silent,
        daily_pnl_provider=lambda et_date: 0.0,
        option_supplement_provider=option_provider,
    )
    alerter.observe(_p0_2m_payload())

    assert len(sender.messages) == 1
    assert sender.messages[0].startswith("【P0 标的信号 · 10:48 ET】")
    assert "同钟分位 P98.3" in sender.messages[0]
    assert "8m RVOL 2.10x" in sender.messages[0]
    assert "0–7DTE 期权快照异步补充中" in sender.messages[0]
    assert len(silent.messages) == 1
    assert silent.messages[0].startswith("【P0 0–7DTE 候选补充 · 静默")
    assert "US.NVDA260814C00100000" in silent.messages[0]
    assert silent.messages[0].count(ALERT_FOOTER) == 1
    assert sender.messages[0].count(ALERT_FOOTER) == 1
    deliveries = [(d.tier, d.telegram_ok) for d in ledger.deliveries]
    assert ("P0", True) in deliveries and ("P2", True) in deliveries
    primary = next(d for d in ledger.decisions if d.rule == "p0_2m_candidate")
    assert primary.tier == "P0" and primary.session_scope == "regular"
    assert len(primary.conditions) == 5  # four gates + full versioned evidence


@pytest.mark.parametrize("pnl", [None, -13_000.0])
def test_async_option_read_rechecks_r6_before_showing_contract(pnl):
    from src.services.alert_tiering import TierContext

    sender, silent, ledger = RecordingSender(), RecordingSender(), FakeLedger()
    alerter = None

    def provider(_ticker, _direction):
        alerter._context = TierContext(daily_realized_pnl=pnl)
        return {"state": "ready", "candidates": [{"contract_code": "must-not-leak"}]}

    alerter = _sync_alerter(
        sender, silent_sender=silent, ledger=ledger,
        daily_pnl_provider=lambda _date: 0.0, option_supplement_provider=provider,
    )
    alerter.observe(_p0_2m_payload())
    assert len(sender.messages) == 1
    assert silent.messages == []
    unavailable = [row for row in ledger.decisions if row.rule == "p0_2m_option_update"]
    assert len(unavailable) == 1
    assert "must-not-leak" not in unavailable[0].text
    assert "contract_context_withheld" in unavailable[0].text


def test_strict_2m_receipt_exposes_counterfactual_dedupe_outcome():
    ledger = FakeLedger()
    sender = RecordingSender()
    alerter = IntradayOpportunityAlerter(
        sender,
        ledger=ledger,
        async_send=True,
        daily_pnl_provider=lambda _et_date: 0.0,
        now_provider=lambda: datetime.fromisoformat(_AS_OF),
    )

    first = alerter.observe_p0_2m(
        _p0_2m_payload(
            mobile_enabled=False,
            mobile_reason="action_switch_disabled",
        )
    )
    duplicate = alerter.observe_p0_2m(
        _p0_2m_payload(
            _p0_2m_candidate(
                window_end="2026-08-14T10:50:00-04:00"
            ),
            mobile_enabled=False,
            mobile_reason="action_switch_disabled",
            as_of="2026-08-14T14:50:05+00:00",
        )
    )

    assert first["accepted"] is True
    first_outcome = first["candidate_outcomes"][0]
    assert first_outcome["symbol"] == "NVDA"
    assert first_outcome["window_end"] == "2026-08-14T10:48:00-04:00"
    assert first_outcome["notification_decision_available"] is True
    assert first_outcome["would_notify"] is True
    assert first_outcome["duplicate_episode_suppressed"] is False
    assert first_outcome["suppressed_reason"] == (
        "p0_2m_shadow:action_switch_disabled"
    )
    assert first_outcome["notification_attempt_id"]
    assert first_outcome["telegram_enqueue_evidence_mode"] == (
        "counterfactual_no_send_same_queue"
    )
    assert first_outcome["telegram_queue_accepted"] is True
    assert duplicate["accepted"] is True
    assert duplicate["candidate_outcomes"][0]["would_notify"] is False
    assert (
        duplicate["candidate_outcomes"][0]["duplicate_episode_suppressed"]
        is True
    )
    alerter.stop(join_timeout_seconds=None)
    assert sender.messages == []
    assert ledger.deliveries == []
    assert alerter._tier_state.p0_delivered_today == 0
    assert alerter._tier_state.p0_delivered_week == 0
    assert alerter._sent_count == 0


def test_strict_2m_shadow_research_rings_without_becoming_formal_p0():
    sender = RecordingSender()
    ledger = FakeLedger()
    option_calls = []
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _et_date: 0.0,
        option_supplement_provider=lambda *args: option_calls.append(args) or {},
    )

    alerter.observe(
        _p0_2m_payload(
            mobile_enabled=False,
            mobile_reason="action_switch_disabled",
        )
    )

    assert len(sender.messages) == 1
    assert sender.messages[0].startswith(
        "【严格 2m 研究观察 · 尚未校准 · 不可据此行动 · 响铃"
    )
    assert "尚未完成前瞻校准" in sender.messages[0]
    assert "未校准研究观察不查询或补充期权合约" in sender.messages[0]
    assert "期权快照异步补充中" not in sender.messages[0]
    assert "R6 保险丝已触发" not in sender.messages[0]
    assert "R6 状态标缺" not in sender.messages[0]
    assert option_calls == []
    formal = next(
        row for row in ledger.decisions if row.rule == "p0_2m_candidate"
    )
    research = next(
        row
        for row in ledger.decisions
        if row.rule == P0_2M_SHADOW_RESEARCH_RULE
    )
    assert formal.tier == "P0" and formal.suppressed is True
    assert formal.delivery_channel == "web_only"
    assert research.tier == "P2" and research.suppressed is False
    assert research.delivery_channel == "telegram"
    assert research.session_scope == "regular"
    assert [(row.tier, row.telegram_ok) for row in ledger.deliveries] == [
        ("P2", True)
    ]
    assert alerter._tier_state.p0_delivered_today == 0
    assert alerter._tier_state.p0_delivered_week == 0
    assert alerter._tier_state.p0_2m_shadow_research_delivered_today == 1
    assert alerter._tier_state.p0_2m_shadow_research_delivered_week == 1


def test_formal_p0_does_not_also_send_research_copy():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender, ledger=ledger, p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _et_date: 0.0,
    )
    alerter.observe(_p0_2m_payload())
    assert len(sender.messages) == 1
    assert any(row.tier == "P0" and not row.suppressed for row in ledger.decisions)
    assert not any(row.rule == P0_2M_SHADOW_RESEARCH_RULE for row in ledger.decisions)


def test_premarket_research_anchor_does_not_suppress_rth_formal_crossing():
    sender, ledger = RecordingSender(), FakeLedger()
    alerter = _sync_alerter(
        sender, ledger=ledger, p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _date: 0.0,
    )
    for scope, window_end in (
        ("premarket", "2026-08-14T09:28:00-04:00"),
        ("regular", "2026-08-14T09:48:00-04:00"),
        ("regular", "2026-08-14T09:50:00-04:00"),
    ):
        alerter.observe(_p0_2m_payload(
            _p0_2m_candidate(session_scope=scope, window_end=window_end),
            as_of=window_end,
        ))

    assert len(sender.messages) == 2  # one research ring, then one formal P0
    formal = [row for row in ledger.decisions if row.rule == "p0_2m_candidate"]
    assert [row.suppressed_reason for row in formal] == [
        "p0_2m_shadow:extended_session_not_validated", None,
        "same_symbol_direction_30m_episode",
    ]
    assert alerter._tier_state.p0_delivered_today == 1
    assert alerter._tier_state.p0_episode_last_window == {
        "premarket:NVDA:up": "2026-08-14T09:28:00-04:00",
        "regular:NVDA:up": "2026-08-14T09:48:00-04:00",
    }


def test_late_rth_anchor_does_not_suppress_afterhours_research_crossing():
    sender, ledger = RecordingSender(), FakeLedger()
    alerter = _sync_alerter(
        sender, ledger=ledger, p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _date: 0.0,
    )
    for scope, window_end in (
        ("regular", "2026-08-14T15:58:00-04:00"),
        ("afterhours", "2026-08-14T16:18:00-04:00"),
    ):
        alerter.observe(_p0_2m_payload(
            _p0_2m_candidate(session_scope=scope, window_end=window_end),
            as_of=window_end,
        ))

    assert len(sender.messages) == 2
    research = [row for row in ledger.decisions if row.rule == P0_2M_SHADOW_RESEARCH_RULE]
    assert len(research) == 1 and research[0].session_scope == "afterhours"
    assert research[0].suppressed is False
    assert alerter._tier_state.p0_delivered_today == 1
    assert alerter._tier_state.p0x_2m_shadow_research_delivered_week == 1


def test_scoped_episode_keeps_direction_and_exact_30m_semantics():
    ledger = FakeLedger()
    alerter = _sync_alerter(RecordingSender(), ledger=ledger)
    for direction, window_end in (
        ("up", "2026-08-14T10:48:00-04:00"),
        ("down", "2026-08-14T10:50:00-04:00"),
        ("up", "2026-08-14T10:52:00-04:00"),
        ("up", "2026-08-14T11:18:00-04:00"),
    ):
        alerter.observe(_p0_2m_payload(
            _p0_2m_candidate(direction=direction, window_end=window_end),
            as_of=window_end, mobile_enabled=False,
            mobile_reason="action_switch_disabled",
        ))

    assert [row.suppressed_reason for row in ledger.decisions] == [
        "p0_2m_shadow:action_switch_disabled",
        "p0_2m_shadow:action_switch_disabled",
        "same_symbol_direction_30m_episode",
        "p0_2m_shadow:action_switch_disabled",
    ]
    assert alerter._tier_state.p0_episode_last_window == {
        "regular:NVDA:up": "2026-08-14T11:18:00-04:00",
        "regular:NVDA:down": "2026-08-14T10:50:00-04:00",
    }


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("prior_scope, prior_end, current_scope, current_end, duplicate", [
    ("premarket", "09:28", "regular", "09:48", False),
    ("premarket", "09:30", "regular", "09:48", False),
    ("regular", "16:00", "afterhours", "16:18", False),
    ("regular", "15:58", "regular", "16:00", True),
])
def test_scoped_and_legacy_episode_restart_preserve_session_boundaries(
    legacy, prior_scope, prior_end, current_scope, current_end, duplicate,
):
    prior_end = f"2026-08-14T{prior_end}:00-04:00"
    current_end = f"2026-08-14T{current_end}:00-04:00"
    key = (
        ("p0_2m_episode", "NVDA", "up", prior_end) if legacy else
        ("p0_2m_episode", "NVDA", prior_scope, "up", prior_end)
    )
    ledger = FakeLedger(day_state=PushDayState(seen_keys={key}))
    alerter = _sync_alerter(RecordingSender(), ledger=ledger)
    alerter.observe(_p0_2m_payload(
        _p0_2m_candidate(session_scope=current_scope, window_end=current_end),
        as_of=current_end, mobile_enabled=False,
        mobile_reason="action_switch_disabled",
    ))

    assert len(ledger.decisions) == 1
    assert (ledger.decisions[0].suppressed_reason == "same_symbol_direction_30m_episode") is duplicate
    assert alerter._tier_state.p0_episode_last_window[f"{prior_scope}:NVDA:up"] == prior_end
    assert ("p0_2m_episode", "NVDA", prior_scope, "up", prior_end) in alerter._seen
    if not duplicate:
        assert alerter._tier_state.p0_episode_last_window[f"{current_scope}:NVDA:up"] == current_end


def test_legacy_episode_scope_restore_retries_if_calendar_is_unavailable(monkeypatch):
    from src.services import intraday_opportunity_alerter as module

    prior_end = "2026-08-14T09:28:00-04:00"
    ledger = FakeLedger(day_state=PushDayState(seen_keys={
        ("p0_2m_episode", "NVDA", "up", prior_end),
    }))
    sender = RecordingSender()
    alerter = _sync_alerter(sender, ledger=ledger)
    payload = _p0_2m_payload(
        _p0_2m_candidate(window_end="2026-08-14T09:48:00-04:00"),
        as_of="2026-08-14T09:48:00-04:00",
    )
    resolver = module.resolve_xnys_session_clock
    monkeypatch.setattr(module, "resolve_xnys_session_clock", lambda _time: SimpleNamespace(
        state="unavailable", phase=None,
    ))
    assert alerter.observe(payload) is False
    assert alerter._day_state_rebuilt is False
    assert sender.messages == [] and ledger.decisions == []

    monkeypatch.setattr(module, "resolve_xnys_session_clock", resolver)
    assert alerter.observe(payload) is True
    assert len(sender.messages) == 1
    assert alerter._tier_state.p0_episode_last_window["premarket:NVDA:up"] == prior_end


def test_strict_2m_shadow_research_receipt_prefers_actual_queue_evidence():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = IntradayOpportunityAlerter(
        sender,
        ledger=ledger,
        async_send=True,
        p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _et_date: 0.0,
        now_provider=lambda: datetime.fromisoformat(_AS_OF),
    )

    receipt = alerter.observe_p0_2m(
        _p0_2m_payload(
            mobile_enabled=False,
            mobile_reason="action_switch_disabled",
        )
    )

    outcome = receipt["candidate_outcomes"][0]
    assert receipt["accepted"] is True
    assert outcome["would_notify"] is True
    assert outcome["telegram_enqueue_evidence_mode"] == (
        "actual_telegram_queue_receipt"
    )
    assert sender.delivered.wait(timeout=2)
    alerter.stop(join_timeout_seconds=None)
    research = next(
        row
        for row in ledger.decisions
        if row.rule == P0_2M_SHADOW_RESEARCH_RULE and not row.suppressed
    )
    assert outcome["notification_attempt_id"] == research.message_key
    assert len(sender.messages) == 1


def test_strict_2m_shadow_research_uses_independent_extended_budget():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _et_date: 0.0,
    )

    alerter.observe(
        _p0_2m_payload(
            _p0_2m_candidate("GOOGL", session_scope="premarket"),
            _p0_2m_candidate(
                "META",
                session_scope="premarket",
                window_end="2026-08-14T09:20:00-04:00",
            ),
            mobile_enabled=False,
            mobile_reason="action_switch_disabled",
            as_of="2026-08-14T13:20:05+00:00",
        )
    )

    assert len(sender.messages) == 1
    assert "GOOGL" in sender.messages[0]
    assert "META" not in sender.messages[0]
    research_rows = [
        row
        for row in ledger.decisions
        if row.rule == P0_2M_SHADOW_RESEARCH_RULE
    ]
    assert len(research_rows) == 2
    assert sum(not row.suppressed for row in research_rows) == 1
    suppressed = next(row for row in research_rows if row.suppressed)
    assert suppressed.suppressed_reason == (
        "p0_2m_shadow_research_p0x_budget_exhausted"
    )
    assert alerter._tier_state.p0x_delivered_week == 0
    assert alerter._tier_state.p0x_2m_shadow_research_delivered_week == 1


def test_strict_2m_shadow_research_weekly_budget_restores_independently():
    sender = RecordingSender()
    ledger = FakeLedger(week_shadow_p0=7)
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _et_date: 0.0,
    )

    alerter.observe(
        _p0_2m_payload(
            mobile_enabled=False,
            mobile_reason="activation_artifact_not_configured",
        )
    )

    assert sender.messages == []
    research = next(
        row
        for row in ledger.decisions
        if row.rule == P0_2M_SHADOW_RESEARCH_RULE
    )
    assert research.suppressed_reason == (
        "p0_2m_shadow_research_rth_budget_exhausted"
    )
    assert alerter._tier_state.p0_delivered_week == 0


def test_strict_2m_shadow_research_preserves_r6_and_skips_options():
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    option_calls = []
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=silent,
        p0_shadow_mobile_enabled=True,
        daily_pnl_provider=lambda _et_date: -13_000.0,
        option_supplement_provider=lambda *args: option_calls.append(args) or {},
    )

    alerter.observe(
        _p0_2m_payload(
            mobile_enabled=False,
            mobile_reason="action_switch_disabled",
        )
    )

    assert len(sender.messages) == 1
    assert "R6 保险丝已触发" in sender.messages[0]
    assert "不可据此交易" in sender.messages[0]
    assert option_calls == []
    assert any("R6 保险丝触线" in message for message in silent.messages)


def test_full_option_supplement_queue_records_visible_unavailable_state():
    ledger = FakeLedger()
    alerter = IntradayOpportunityAlerter(
        RecordingSender(),
        ledger=ledger,
        async_send=True,
        option_supplement_provider=lambda *_args: {},
        now_provider=lambda: datetime.fromisoformat(_AS_OF),
    )
    alerter._option_queue = queue.Queue(maxsize=1)
    occupied = _OptionSupplementRequest(
        et_date=_DAY,
        ticker="NVDA",
        direction="up",
        window_end="2026-08-14T10:48:00-04:00",
    )
    alerter._option_queue.put_nowait(occupied)
    request = _OptionSupplementRequest(
        et_date=_DAY,
        ticker="TSLA",
        direction="down",
        window_end="2026-08-14T10:50:00-04:00",
    )

    alerter._schedule_option_supplement(request)

    assert alerter._option_queue.qsize() == 1
    row = next(
        item
        for item in ledger.decisions
        if item.rule == "p0_2m_option_update" and item.ticker == "TSLA"
    )
    assert row.tier == "P2"
    assert row.delivery_channel == "web_only"
    assert row.suppressed is False
    assert "option_supplement_queue_full" in row.text
    assert "标的 P0 不撤销" in row.text


def test_strict_2m_forming_is_web_only_even_without_silent_channel():
    """0.3–0.5 ATR 只显示“形成中”，绝不借静默通道发手机。"""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger, silent_sender=None)
    alerter.observe(
        _p0_2m_payload(_p0_2m_candidate(state="forming"))
    )
    assert sender.messages == []
    rows = [d for d in ledger.decisions if d.rule == "p0_2m_forming"]
    assert len(rows) == 1
    assert rows[0].tier == "P2"
    assert rows[0].delivery_channel == "web_only"


def test_strict_2m_mobile_scope_suppresses_shadow_stratum_per_symbol():
    """全局 artifact ready 也不能让失败分类中的股票发到手机。"""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        daily_pnl_provider=lambda et_date: 0.0,
    )
    eligible = _p0_2m_candidate("NVDA")
    ineligible = _p0_2m_candidate(
        "TQQQ", window_end="2026-08-14T10:50:00-04:00"
    )
    ineligible["p0_mobile_eligible"] = False

    alerter.observe(_p0_2m_payload(eligible, ineligible))

    assert len(sender.messages) == 1
    assert "NVDA" in sender.messages[0]
    assert "TQQQ" not in sender.messages[0]
    rows = {
        row.ticker: row
        for row in ledger.decisions
        if row.rule == "p0_2m_candidate"
    }
    assert rows["NVDA"].suppressed is False
    assert rows["TQQQ"].suppressed is True
    assert rows["TQQQ"].suppressed_reason == (
        "p0_2m_shadow:symbol_not_mobile_eligible"
    )


def test_global_cap_suppresses_telegram_tiers_with_reason():
    """全局 Telegram 行上限只压发送，不篡改两个 P0 信号事实。"""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        max_alerts_per_day=1,
        daily_pnl_provider=lambda et_date: 0.0,
    )
    alerter.observe(
        _p0_2m_payload(
            _p0_2m_candidate("NVDA"),
            _p0_2m_candidate(
                "TSLA", window_end="2026-08-14T10:50:00-04:00"
            ),
        )
    )
    assert len(sender.messages) == 1
    rows = [d for d in ledger.decisions if d.rule == "p0_2m_candidate"]
    assert len(rows) == 2 and all(d.tier == "P0" for d in rows)
    assert [row.suppressed_reason for row in rows].count("daily_cap_reached") == 1


def test_max_per_day_clamps_to_minimum_one():
    alerter = _sync_alerter(RecordingSender(), max_alerts_per_day=0)
    assert alerter.max_alerts_per_day == 1


@pytest.mark.parametrize("earnings_state", ["blackout", "unknown"])
def test_earnings_context_does_not_change_underlying_p0(earnings_state):
    """财报只可作警告上下文，不能改变严格 2m 标的信号。"""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        daily_pnl_provider=lambda et_date: 0.0,
    )
    context = (
        {"state": "ready", "within_blackout": True, "days_to_earnings": 0}
        if earnings_state == "blackout"
        else {"state": "unavailable"}
    )
    alerter.observe(
        _p0_2m_payload(
            _p0_2m_candidate(earnings_proximity=context)
        )
    )
    assert len(sender.messages) == 1
    primary = next(d for d in ledger.decisions if d.rule == "p0_2m_candidate")
    assert primary.tier == "P0" and not primary.suppressed


def test_r6_fuse_keeps_factual_p0_phone_message_and_sends_silent_notice():
    """R6 只阻止行动性措辞/合约补充；P0 事实仍发送。"""
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=silent,
        daily_pnl_provider=lambda et_date: -13_000.0,
    )
    alerter.observe(_p0_2m_payload())
    assert len(sender.messages) == 1
    assert sender.messages[0].startswith("【P0 标的信号")
    assert "R6 保险丝已触发" in sender.messages[0]
    assert "0–7DTE 期权快照异步补充中" not in sender.messages[0]
    notice_rows = [d for d in ledger.decisions if d.rule == "r6_fuse_notice"]
    assert len(notice_rows) == 1 and notice_rows[0].tier == "P1"
    assert any("R6 保险丝触线" in m for m in silent.messages)
    primary = next(d for d in ledger.decisions if d.rule == "p0_2m_candidate")
    assert primary.tier == "P0" and not primary.suppressed
    assert primary.delivery_channel == "telegram"


@pytest.mark.parametrize(
    "second_pnl", [-11_000.0, None], ids=["pnl_recovers", "provider_outage"]
)
def test_r6_fuse_latches_for_the_day(second_pnl):
    """D2：R6 触线后当日锁存——之后盈亏回升或 provider 断供都不解锁，
    后续候选照常发送事实 P0，但不附期权候选，绝无同日 R6 复播。"""
    pnl_values = iter([-13_000.0, second_pnl, second_pnl])
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=silent,
        daily_pnl_provider=lambda et_date: next(pnl_values),
    )
    alerter.observe(_p0_2m_payload(_p0_2m_candidate("NVDA")))
    alerter.observe(
        _p0_2m_payload(
            _p0_2m_candidate(
                "TSLA", window_end="2026-08-14T10:50:00-04:00"
            ),
            as_of="2026-08-14T14:50:05+00:00",
        )
    )
    assert alerter._tier_state.fuse_latched_today is True
    tsla_rows = [d for d in ledger.decisions if d.ticker == "TSLA"]
    primary = next(d for d in tsla_rows if d.rule == "p0_2m_candidate")
    assert primary.tier == "P0" and not primary.suppressed
    assert primary.delivery_channel == "telegram"
    assert "R6 保险丝已触发" in primary.text
    # 触线通告只发一次（当日去重）。
    notice_rows = [d for d in ledger.decisions if d.rule == "r6_fuse_notice"]
    assert len(notice_rows) == 1


def test_r6_fuse_relatches_from_ledger_after_restart():
    """D2：重启后从台账 re-latch——当日已有 r6_fuse_notice decision 行
    （去重键重建可见）时，即便 pnl 读数缺失也维持 triggered。"""
    day_state = PushDayState(seen_keys={("r6_fuse_notice",)})
    ledger = FakeLedger(day_state=day_state)
    alerter = _sync_alerter(
        RecordingSender(),
        ledger=ledger,
        silent_sender=RecordingSender(),
        daily_pnl_provider=lambda et_date: None,  # 重启后读不到当日盈亏
    )
    alerter.observe(_p0_2m_payload(_p0_2m_candidate("NVDA")))
    assert alerter._tier_state.fuse_latched_today is True
    primary = next(d for d in ledger.decisions if d.rule == "p0_2m_candidate")
    assert primary.tier == "P0" and not primary.suppressed
    assert "R6 保险丝已触发" in primary.text
    # 触线通告不重发（去重键已在台账）。
    assert all(d.rule != "r6_fuse_notice" for d in ledger.decisions)


def test_r6_fuse_missing_keeps_factual_p0_phone_message():
    """未锁存且 R6 数据缺失时，只去除行动性措辞/合约补充。"""
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger, silent_sender=silent)
    alerter.observe(_p0_2m_payload())
    assert len(sender.messages) == 1 and silent.messages == []
    row = next(d for d in ledger.decisions if d.rule == "p0_2m_candidate")
    assert row.tier == "P0"
    assert not row.suppressed
    assert row.delivery_channel == "telegram"
    assert "R6 状态标缺" in row.text
    assert "0–7DTE 期权快照异步补充中" not in row.text


def test_retired_weekend_strategy_never_emits_weekend_notice():
    """旧跨周末策略已退役；周五不会再生成隔夜建议或周末专属通知。"""
    from src.services.alert_tiering import OpenPositionFact

    friday = _payload(
        market_date="2026-08-28",
        as_of="2026-08-28T19:35:00+00:00",  # 周五 15:35 ET
        candidates=[],
    )
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        RecordingSender(),
        ledger=ledger,
        silent_sender=silent,
        open_positions_provider=lambda: (
            OpenPositionFact(
                raw_symbol="NVDA260901C00100000",
                underlying="NVDA",
                opened_et_date="2026-08-27",
                dte_at_entry=5,
            ),
        ),
    )
    alerter.observe(friday)
    assert len(silent.messages) == 1
    assert "同日平仓提醒" in silent.messages[0]
    assert "不允许隔夜" in silent.messages[0]
    assert "NVDA260901C00100000" in silent.messages[0]
    assert all(d.rule != "weekend_position_notice" for d in ledger.decisions)


def test_same_day_flatten_reminder_is_silent_and_missing_snapshot_is_p3():
    """15:30 后有仓位就静默提醒；Journal 快照缺失则明确 P3 标缺。"""
    from src.services.alert_tiering import OpenPositionFact

    power_hour = _payload(
        as_of="2026-08-25T19:35:00+00:00",  # 15:35 ET
        candidates=[],
    )
    ledger = FakeLedger()
    silent = RecordingSender()
    alerter = _sync_alerter(
        RecordingSender(),
        ledger=ledger,
        silent_sender=silent,
        open_positions_provider=lambda: (
            OpenPositionFact("NVDA260901C00100000", "NVDA", "2026-08-25", 5),
        ),
    )
    alerter.observe(power_hour)
    assert len(silent.messages) == 1
    assert "15:45 ET 前完成平仓" in silent.messages[0]
    rows = [d for d in ledger.decisions if d.rule == "same_day_flatten_reminder"]
    assert rows and rows[0].tier == "P1" and not rows[0].suppressed

    missing_ledger = FakeLedger()
    missing = _sync_alerter(
        RecordingSender(),
        ledger=missing_ledger,
        silent_sender=RecordingSender(),
        open_positions_provider=None,
    )
    missing.observe(power_hour)
    missing_rows = [
        d for d in missing_ledger.decisions
        if d.rule == "same_day_flatten_reminder"
    ]
    assert missing_rows and missing_rows[0].tier == "P3"
    assert missing_rows[0].suppressed_reason == "open_positions_unavailable"


def test_tail_restart_missing_context_does_not_consume_flatten_reminder_key():
    """首轮 context 标缺只记 P3；快照随后到达仍能发同日平仓提醒。"""

    from src.services.alert_tiering import OpenPositionFact

    payload = _payload(
        as_of="2026-08-25T19:35:00+00:00",  # 15:35 ET
        candidates=[],
    )
    context = {"positions": None}
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        RecordingSender(),
        ledger=ledger,
        silent_sender=silent,
        open_positions_provider=lambda: context["positions"],
    )

    alerter.observe(payload)
    assert silent.messages == []
    assert ("same_day_flatten_reminder",) not in alerter._seen
    assert ("same_day_flatten_context_missing",) in alerter._seen

    context["positions"] = (
        OpenPositionFact(
            "NVDA260901C00100000", "NVDA", "2026-08-25", 5
        ),
    )
    alerter.observe(payload)

    assert len(silent.messages) == 1
    assert "同日平仓提醒" in silent.messages[0]
    assert ("same_day_flatten_reminder",) in alerter._seen
    rows = [
        row for row in ledger.decisions
        if row.rule == "same_day_flatten_reminder"
    ]
    assert [row.tier for row in rows] == ["P3", "P1"]


def test_half_day_flatten_reminder_uses_1245_deadline():
    from src.services.alert_tiering import OpenPositionFact

    payload = _payload(
        market_date="2026-11-27",
        as_of="2026-11-27T17:30:00+00:00",  # 12:30 ET, 13:00 close
        candidates=[],
    )
    silent = RecordingSender()
    alerter = _sync_alerter(
        RecordingSender(),
        ledger=FakeLedger(),
        silent_sender=silent,
        open_positions_provider=lambda: (
            OpenPositionFact("TSLA261127C00350000", "TSLA", "2026-11-27", 0),
        ),
    )

    alerter.observe(payload)

    assert len(silent.messages) == 1
    assert "12:45 ET 前完成平仓" in silent.messages[0]
    assert "15:45" not in silent.messages[0]


def test_half_day_after_close_remains_research_despite_rth_activation():
    """RTH activation never certifies after-hours, even on a half-day."""

    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=silent,
        daily_pnl_provider=lambda et_date: 0.0,
        p0_shadow_mobile_enabled=True,
    )

    candidate = _p0_2m_candidate(
        "NVDA",
        session_scope="afterhours",
        window_end="2026-11-27T13:18:00-05:00",
    )
    payload = _p0_2m_payload(
        candidate,
        as_of="2026-11-27T18:18:05+00:00",
    )
    payload["market_date_et"] = "2026-11-27"
    payload["session_state"] = "afterhours"
    alerter.observe(payload)

    assert len(sender.messages) == 1
    assert "研究" in sender.messages[0]
    assert silent.messages == []
    rows = [row for row in ledger.decisions if row.rule == "p0_2m_candidate"]
    assert len(rows) == 1
    assert rows[0].tier == "P0X"
    assert rows[0].session_scope == "afterhours"
    assert rows[0].suppressed
    assert rows[0].suppressed_reason == "p0_2m_shadow:extended_session_not_validated"
    assert alerter._tier_state.p0_delivered_today == 0
    assert alerter._tier_state.p0x_delivered_today_by_session == {
        "premarket": 0,
        "afterhours": 0,
    }
    assert alerter._sent_count == 1


def test_half_day_close_guard_blocks_stale_opening_probe(monkeypatch):
    """P1-O cannot escape if an upstream probe wrongly survives the close."""

    from src.services import intraday_opportunity_alerter as alerter_module

    monkeypatch.setattr(
        alerter_module,
        "opening_probe_direction",
        lambda _payload, _candidate: "up",
    )
    monkeypatch.setattr(
        alerter_module,
        "opening_probe_profile",
        lambda _payload, candidate: candidate["session_bursts"]["opening_impulse"],
    )
    payload = _opening_payload()
    payload.update(
        market_date_et="2026-11-27",
        as_of="2026-11-27T18:05:00+00:00",  # 13:05 ET, after early close
    )
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger, silent_sender=silent)

    alerter.observe_opening(payload)

    assert sender.messages == []
    assert silent.messages == []
    blocked = [
        row
        for row in ledger.decisions
        if row.rule == "opening_impulse_v1"
    ]
    assert len(blocked) == 1
    assert blocked[0].tier == "P2"
    assert blocked[0].delivery_channel == "web_only"
    assert blocked[0].suppressed_reason == "xnys_opportunity_window_closed"
    assert alerter._tier_state.opening_delivered_today == 0
    assert alerter._sent_count == 0


def test_same_day_flatten_reminder_bypasses_consumed_opportunity_budgets():
    """A full opportunity budget must never suppress the same-day exit warning."""
    from src.journal.ledger.push_ledger_repository import PushDayState
    from src.services.alert_tiering import OpenPositionFact, P1_DAILY_BUDGET

    day_state = PushDayState(
        telegram_alert_count=20,
        p1_delivered=P1_DAILY_BUDGET,
    )
    ledger = FakeLedger(day_state=day_state)
    silent = RecordingSender()
    alerter = _sync_alerter(
        RecordingSender(),
        ledger=ledger,
        silent_sender=silent,
        max_alerts_per_day=1,
        open_positions_provider=lambda: (
            OpenPositionFact("NVDA260901C00100000", "NVDA", "2026-08-25", 5),
        ),
    )
    alerter.observe(_payload(
        as_of="2026-08-25T19:35:00+00:00",
        candidates=[],
    ))

    assert len(silent.messages) == 1
    assert "15:45 ET 前完成平仓" in silent.messages[0]
    rows = [d for d in ledger.decisions if d.rule == "same_day_flatten_reminder"]
    assert rows and rows[0].suppressed is False


def test_p0_daily_budget_exhaustion_suppresses_delivery_not_signal():
    from src.services.alert_tiering import P0_DAILY_BUDGET

    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        daily_pnl_provider=lambda et_date: 0.0,
    )
    tickers = ["AAA", "BBB", "CCC"]
    alerter.observe(
        _p0_2m_payload(
            *(
                _p0_2m_candidate(
                    ticker,
                    window_end=f"2026-08-14T10:{48 + index * 2:02d}:00-04:00",
                )
                for index, ticker in enumerate(tickers)
            )
        )
    )
    delivered = [
        d
        for d in ledger.decisions
        if d.rule == "p0_2m_candidate" and not d.suppressed
    ]
    suppressed = [
        d
        for d in ledger.decisions
        if d.rule == "p0_2m_candidate"
        and d.suppressed_reason == "p0_delivery_budget_exhausted"
    ]
    assert len(sender.messages) == 1  # two P0 lines are batched into one push
    assert len(delivered) == P0_DAILY_BUDGET
    assert len(suppressed) == len(tickers) - P0_DAILY_BUDGET
    assert all(row.tier == "P0" for row in delivered + suppressed)


# ---------------------------------------------------------------------------
# 发送失败安静降级 / observe 永不抛出 / 队列有界
# ---------------------------------------------------------------------------

def test_failed_p0_delivery_releases_success_budget_but_keeps_fact_dedupe():
    """最终发送失败不应占掉“已送达”预算，也不能每 tick 重复骚扰。"""

    ringing = RecordingSender(result=False)
    silent = RecordingSender(result=True)
    ledger = FakeLedger()
    alerter = _sync_alerter(
        ringing,
        silent_sender=silent,
        ledger=ledger,
        daily_pnl_provider=lambda et_date: 0.0,
    )
    payload = _p0_2m_payload()

    alerter.observe(payload)

    assert len(ringing.messages) == 1
    assert any(
        delivery.tier == "P0" and delivery.telegram_ok is False
        for delivery in ledger.deliveries
    )
    assert alerter._tier_state.p0_delivered_today == 0
    assert alerter._tier_state.p0_delivered_week == 0
    assert alerter._sent_count == 0
    assert (
        "p0_2m_episode",
        "NVDA",
        "regular",
        "up",
        "2026-08-14T10:48:00-04:00",
    ) in alerter._seen

    # 同一事实仍去重：释放的是预算，不是制造每分钟一次的重复发送。
    alerter.observe(payload)
    assert len(ringing.messages) == 1


def test_async_failed_p0_retries_then_succeeds_and_keeps_budget_reserved():
    """短暂网络失败应补发真实正文，不能在第一次失败后永久丢弃。"""

    attempts: list[str] = []
    second_attempt = threading.Event()

    def fail_once_then_succeed(text: str) -> bool:
        attempts.append(text)
        if len(attempts) == 2:
            second_attempt.set()
            return True
        return False

    ledger = FakeLedger()
    alerter = IntradayOpportunityAlerter(
        fail_once_then_succeed,
        send_silent_text=RecordingSender(),
        async_send=True,
        ledger=ledger,
        daily_pnl_provider=lambda et_date: 0.0,
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=60.0,
    )

    alerter.observe(_p0_2m_payload())
    assert second_attempt.wait(timeout=2.0)
    alerter.stop(join_timeout_seconds=None)

    assert len(attempts) == 2
    assert attempts[1].startswith("⚠️ Telegram 网络延迟补发")
    assert attempts[0] in attempts[1]
    assert "NVDA" in attempts[0]
    assert "\n" in attempts[0]
    # A pending retry has no false receipt; otherwise restart would release
    # its budget before the retry reaches a terminal outcome.
    assert [row.telegram_ok for row in ledger.deliveries] == [True]
    assert [row.text for row in ledger.deliveries] == [attempts[1]]
    assert alerter._tier_state.p0_delivered_today == 1
    assert alerter._tier_state.p0_delivered_week == 1
    assert alerter._sent_count == 1


def test_async_terminal_retry_failure_releases_budget_only_after_last_attempt():
    attempts: list[str] = []
    second_attempt = threading.Event()

    def always_fail(text: str) -> bool:
        attempts.append(text)
        if len(attempts) == 2:
            second_attempt.set()
        return False

    ledger = FakeLedger()
    alerter = IntradayOpportunityAlerter(
        always_fail,
        send_silent_text=RecordingSender(),
        async_send=True,
        ledger=ledger,
        daily_pnl_provider=lambda et_date: 0.0,
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=60.0,
    )

    alerter.observe(_p0_2m_payload())
    assert second_attempt.wait(timeout=2.0)
    alerter.stop(join_timeout_seconds=None)

    assert len(attempts) == 2
    assert [row.telegram_ok for row in ledger.deliveries] == [False]
    assert alerter._tier_state.p0_delivered_today == 0
    assert alerter._tier_state.p0_delivered_week == 0
    assert alerter._sent_count == 0


def _persistent_retry_item(
    *,
    message_key="persistent-real-alert",
    et_date="2026-09-18",
):
    message = _OutboundMessage(
        message_key=message_key,
        tier="P0",
        text="P0 标的信号\nNVDA 严格 2m 事实",
        silent=False,
    )
    decision = SimpleNamespace(
        et_date=et_date,
        message_key=message_key,
        suppressed=False,
        delivery_channel="telegram",
        tier="P0",
        rule="p0_2m_candidate",
        session_scope="regular",
    )
    return _WorkItem(
        et_date=et_date,
        decisions=(decision,),
        messages=(message,),
    )


def test_real_alert_is_persisted_before_send_and_success_finalizes_first():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = AtomicFakePersistentLedger()
    item = _persistent_retry_item()

    def sender(_text):
        row = ledger.outbox[item.messages[0].message_key]
        assert row.state == "pending"
        assert ledger.transitions[-1][0] == "upsert"
        return True

    alerter = IntradayOpportunityAlerter(
        sender,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(15.0,),
        delivery_retry_ttl_seconds=60.0,
    )

    alerter._process(item)

    key = item.messages[0].message_key
    assert ledger.outbox[key].state == "succeeded"
    assert [row.telegram_ok for row in ledger.deliveries] == [True]
    assert ledger.transitions.index(("success", key)) < ledger.transitions.index(
        ("delivery", key)
    )


def test_pending_real_alert_survives_restart_and_replays_once():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger()
    item = _persistent_retry_item()
    first_attempts: list[str] = []

    first = IntradayOpportunityAlerter(
        lambda text: first_attempts.append(text) or False,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=60.0,
    )
    # Simulate a process that exits after the first definitive failure but
    # before its in-memory retry worker runs.
    first._process(item)

    key = item.messages[0].message_key
    assert len(first_attempts) == 1
    assert ledger.outbox[key].state == "pending"
    assert ledger.deliveries == []

    replayed = threading.Event()
    replay_attempts: list[str] = []

    def replay_sender(text):
        replay_attempts.append(text)
        replayed.set()
        return True

    restarted = IntradayOpportunityAlerter(
        replay_sender,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=600.0,
    )
    restarted.start()
    assert replayed.wait(timeout=2.0)
    restarted.stop(join_timeout_seconds=None)

    assert len(replay_attempts) == 1
    assert replay_attempts[0].startswith("⚠️ Telegram 网络延迟补发")
    assert ledger.outbox[key].state == "succeeded"
    assert ledger.outbox[key].text == replay_attempts[0]
    assert [row.telegram_ok for row in ledger.deliveries] == [True]


def test_indeterminate_real_alert_terminalizes_without_false_receipt_or_retry():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger()
    item = _persistent_retry_item()
    attempts: list[str] = []
    alerter = IntradayOpportunityAlerter(
        lambda text: attempts.append(text) or None,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=60.0,
    )

    alerter._process(item)

    key = item.messages[0].message_key
    assert len(attempts) == 1
    assert ledger.outbox[key].state == "terminal"
    assert ledger.deliveries == []
    assert alerter._delivery_retry_heap == []


def test_outbox_write_failure_blocks_external_send_and_records_false_receipt():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger()

    def fail_upsert(**_kwargs):
        raise RuntimeError("outbox unavailable")

    ledger.upsert_retry = fail_upsert
    attempts: list[str] = []
    alerter = IntradayOpportunityAlerter(
        lambda text: attempts.append(text) or True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(15.0,),
        delivery_retry_ttl_seconds=60.0,
    )

    alerter._process(_persistent_retry_item())

    assert attempts == []
    assert [row.telegram_ok for row in ledger.deliveries] == [False]


def test_atomic_decision_outbox_failure_blocks_external_send():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = AtomicFakePersistentLedger()

    def fail_atomic(*_args, **_kwargs):
        raise RuntimeError("atomic storage unavailable")

    ledger.append_with_retries = fail_atomic
    attempts: list[str] = []
    alerter = IntradayOpportunityAlerter(
        lambda text: attempts.append(text) or True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(15.0,),
        delivery_retry_ttl_seconds=60.0,
    )

    alerter._process(_persistent_retry_item())

    assert attempts == []
    assert ledger.decisions == []
    assert ledger.outbox == {}


def test_strict_2m_storage_failure_rejects_then_retries_same_crossing(monkeypatch):
    """Producer acknowledgement must mean durable, not merely memory queued."""
    now = datetime.fromisoformat("2026-08-14T14:48:05+00:00")
    ledger = AtomicFakePersistentLedger()
    writer = ledger.append_with_retries
    sender = RecordingSender()
    alerter = IntradayOpportunityAlerter(
        sender,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
    )
    monkeypatch.setattr(alerter, "start", lambda: None)

    def fail_atomic(*_args, **_kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(ledger, "append_with_retries", fail_atomic)
    payload = _p0_2m_payload(as_of=now.isoformat())
    assert alerter.observe_p0_2m(payload)["accepted"] is False
    assert alerter._queue.empty()
    assert ledger.decisions == [] and ledger.outbox == {}
    assert alerter._tier_state.p0_episode_last_window == {}
    assert alerter._tier_state.p0_delivered_today == 0
    assert alerter._sent_count == 0

    monkeypatch.setattr(ledger, "append_with_retries", writer)
    assert alerter.observe_p0_2m(payload)["accepted"] is True
    item = alerter._queue.get_nowait()
    assert item.persisted_at == now
    assert ledger.outbox[item.messages[0].message_key].state == "pending"
    count = len(ledger.decisions)
    assert sender.messages == []
    alerter._process(item)
    assert len(ledger.decisions) == count  # no second decision commit
    assert len(sender.messages) == 1
    assert ledger.outbox[item.messages[0].message_key].state == "succeeded"
    alerter.observe_p0_2m(payload)
    assert len(sender.messages) == 1


def test_strict_2m_full_queue_does_not_commit_or_consume_episode(monkeypatch):
    ledger = AtomicFakePersistentLedger()
    alerter = IntradayOpportunityAlerter(
        RecordingSender(), async_send=True, queue_maxsize=1, ledger=ledger,
    )
    monkeypatch.setattr(alerter, "start", lambda: None)
    alerter._queue.put_nowait(_persistent_retry_item())
    assert alerter.observe_p0_2m(_p0_2m_payload())["accepted"] is False
    assert ledger.decisions == [] and ledger.outbox == {}
    assert alerter._tier_state.p0_episode_last_window == {}


def test_durable_2m_queue_delay_cannot_send_after_original_deadline():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    clock = {"now": now}
    ledger = AtomicFakePersistentLedger()
    sender = RecordingSender()
    alerter = IntradayOpportunityAlerter(
        sender, async_send=True, ledger=ledger, now_provider=lambda: clock["now"],
    )
    item = alerter._persist_work_item(_persistent_retry_item())
    assert item is not None
    clock["now"] = now + timedelta(minutes=6)
    alerter._process(item)
    assert sender.messages == []
    assert ledger.outbox[item.messages[0].message_key].state == "terminal"
    assert [row.telegram_ok for row in ledger.deliveries] == [False]


def test_durable_2m_delivery_passes_original_deadline_to_transport():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    deadlines = []
    ledger = AtomicFakePersistentLedger()
    alerter = IntradayOpportunityAlerter(
        lambda _text: pytest.fail("deadline-aware sender must be used"),
        send_text_with_deadline=lambda _text, expiry: deadlines.append(expiry) or True,
        async_send=True, ledger=ledger, now_provider=lambda: now,
    )
    alerter._process(_persistent_retry_item())
    assert deadlines == [now + timedelta(minutes=6)]


def test_final_opening_checkpoint_expires_at_0936_and_never_retries():
    clock = {"now": datetime.fromisoformat("2026-08-31T13:35:05+00:00")}
    ledger = FakePersistentLedger()
    sender = RecordingSender(result=False)
    alerter = IntradayOpportunityAlerter(
        sender, async_send=True, ledger=ledger,
        now_provider=lambda: clock["now"],
    )
    alerter._day = "2026-08-31"
    item = alerter._build_work_item(
        _opening_payload(_final_v2_opening_candidate()),
        "2026-08-31", alerter._context, opening_only=True,
    )
    assert item.messages[0].expires_at == datetime.fromisoformat(
        "2026-08-31T13:36:00+00:00"
    )
    alerter._process(item)
    assert len(sender.messages) == 1
    assert alerter._delivery_retry_heap == [] and ledger.outbox == {}

    # A fresh alerter receiving that work after its exact minute must not send.
    late = IntradayOpportunityAlerter(
        sender, async_send=True, ledger=FakeLedger(),
        now_provider=lambda: datetime.fromisoformat("2026-08-31T13:36:00+00:00"),
    )
    late._process(item)
    assert len(sender.messages) == 1


def test_graceful_stop_leaves_failed_real_alert_pending_for_new_process():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger()
    item = _persistent_retry_item()
    alerter = IntradayOpportunityAlerter(
        lambda _text: False,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(15.0,),
        delivery_retry_ttl_seconds=60.0,
    )
    alerter._stop_event.set()

    alerter._process(item)

    key = item.messages[0].message_key
    assert ledger.outbox[key].state == "pending"
    assert ledger.deliveries == []


def test_succeeded_outbox_repairs_missing_success_receipt_on_restart():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger(fail_deliveries=True)
    item = _persistent_retry_item()
    first = IntradayOpportunityAlerter(
        lambda _text: True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(15.0,),
        delivery_retry_ttl_seconds=60.0,
    )
    first._process(item)

    key = item.messages[0].message_key
    assert ledger.outbox[key].state == "succeeded"
    assert ledger.deliveries == []

    ledger.fail_deliveries = False
    restarted = IntradayOpportunityAlerter(
        lambda _text: True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
    )
    restarted._restore_delivery_retries()

    assert [row.telegram_ok for row in ledger.deliveries] == [True]


def test_retry_receipt_repair_uses_the_actual_delayed_phone_text():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger()
    item = _persistent_retry_item()
    first = IntradayOpportunityAlerter(
        lambda _text: False,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=60.0,
    )
    first._process(item)

    ledger.fail_deliveries = True
    delivered = threading.Event()
    phone_texts: list[str] = []

    def retry_sender(text):
        phone_texts.append(text)
        delivered.set()
        return True

    retrying = IntradayOpportunityAlerter(
        retry_sender,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=60.0,
    )
    retrying.start()
    assert delivered.wait(timeout=2.0)
    retrying.stop(join_timeout_seconds=None)

    key = item.messages[0].message_key
    assert ledger.outbox[key].state == "succeeded"
    assert ledger.outbox[key].text == phone_texts[0]
    assert ledger.deliveries == []

    ledger.fail_deliveries = False
    repairing = IntradayOpportunityAlerter(
        lambda _text: True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
    )
    repairing._restore_delivery_retries()

    assert [row.text for row in ledger.deliveries] == phone_texts


def test_failed_receipt_write_leaves_pending_row_for_restart_repair():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    clock = {"now": now}
    ledger = FakePersistentLedger(fail_deliveries=True)
    item = _persistent_retry_item()
    first = IntradayOpportunityAlerter(
        lambda _text: False,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: clock["now"],
        delivery_retry_delays_seconds=(61.0,),
        delivery_retry_ttl_seconds=60.0,
    )
    first._process(item)

    key = item.messages[0].message_key
    assert ledger.outbox[key].state == "pending"
    assert ledger.deliveries == []

    ledger.fail_deliveries = False
    clock["now"] = now + timedelta(seconds=61)
    restarted = IntradayOpportunityAlerter(
        lambda _text: True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: clock["now"],
    )
    restarted._restore_delivery_retries()

    assert ledger.outbox[key].state == "terminal"
    assert [row.telegram_ok for row in ledger.deliveries] == [False]


def test_restart_uses_persisted_deadline_not_new_ttl():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger()
    item = _persistent_retry_item()
    first = IntradayOpportunityAlerter(
        lambda _text: False,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(5.0,),
        delivery_retry_ttl_seconds=30.0,
    )
    first._process(item)
    persisted_deadline = ledger.outbox[item.messages[0].message_key].expires_at

    restarted = IntradayOpportunityAlerter(
        lambda _text: True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
        delivery_retry_delays_seconds=(5.0,),
        delivery_retry_ttl_seconds=3600.0,
    )
    restarted._restore_delivery_retries()

    assert len(restarted._delivery_retry_heap) == 1
    assert restarted._delivery_retry_heap[0].deadline == persisted_deadline


def test_expired_outbox_terminalizes_once_and_writes_one_false_receipt():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ledger = FakePersistentLedger()
    item = _persistent_retry_item()
    message = item.messages[0]
    ledger.append(item.decisions, ())
    ledger.upsert_retry(
        message_key=message.message_key,
        et_date=item.et_date,
        tier=message.tier,
        text=message.text,
        silent=message.silent,
        expires_at=now - timedelta(seconds=1),
        next_attempt_at=now - timedelta(seconds=2),
        attempt_count=1,
        now=now - timedelta(minutes=1),
    )
    alerter = IntradayOpportunityAlerter(
        lambda _text: True,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: now,
    )

    alerter._restore_delivery_retries()
    alerter._restore_delivery_retries()

    assert ledger.outbox[message.message_key].state == "terminal"
    assert [row.telegram_ok for row in ledger.deliveries] == [False]


def test_opening_failure_never_retries_at_or_after_message_expiry():
    expiry = datetime.fromisoformat("2026-08-31T13:33:00+00:00")
    clock = {
        "now": datetime.fromisoformat("2026-08-31T13:32:59+00:00")
    }
    attempts: list[str] = []

    def fail_as_minute_expires(text: str) -> bool:
        attempts.append(text)
        clock["now"] = expiry
        return False

    ledger = FakeLedger()
    alerter = IntradayOpportunityAlerter(
        fail_as_minute_expires,
        async_send=True,
        ledger=ledger,
        now_provider=lambda: clock["now"],
        delivery_retry_delays_seconds=(0.0,),
        delivery_retry_ttl_seconds=60.0,
    )
    alerter._day = "2026-08-31"
    alerter._sent_count = 1
    alerter._tier_state.opening_delivered_today = 1
    message = _OutboundMessage(
        message_key="opening-expiry",
        tier="P1",
        text="P1-O exact-minute alert",
        silent=False,
        expires_at=expiry,
    )
    decision = SimpleNamespace(
        message_key=message.message_key,
        suppressed=False,
        delivery_channel="telegram",
        tier="P1",
        rule="opening_impulse_sequential_v1",
        session_scope="regular",
    )
    item = _WorkItem(
        et_date="2026-08-31",
        decisions=(decision,),
        messages=(message,),
    )

    alerter.start()
    alerter._process(item)
    alerter.stop(join_timeout_seconds=None)

    assert attempts == [message.text]
    assert [row.telegram_ok for row in ledger.deliveries] == [False]
    assert alerter._tier_state.opening_delivered_today == 0
    assert alerter._sent_count == 0


def test_delivery_receipt_failure_keeps_in_process_p0_budget_reserved():
    """外部已发送但 delivery 落库失败时，本进程不能释放 P0 预算。"""

    ringing = RecordingSender(result=True)
    ledger = FakeLedger(fail_deliveries=True)
    alerter = _sync_alerter(
        ringing,
        silent_sender=RecordingSender(),
        ledger=ledger,
        daily_pnl_provider=lambda et_date: 0.0,
    )

    alerter.observe(_p0_2m_payload())

    assert len(ringing.messages) == 1
    assert len(ledger.decisions) == 1
    assert ledger.deliveries == []
    assert alerter._tier_state.p0_delivered_today == 1
    assert alerter._tier_state.p0_delivered_week == 1


def test_decision_persistence_failure_blocks_external_send_and_releases_budget():
    """没有 decision 去重证据时禁止发送，避免重启后重复 P0。"""

    ringing = RecordingSender(result=True)
    alerter = _sync_alerter(
        ringing,
        silent_sender=RecordingSender(),
        ledger=FakeLedger(fail=True),
        daily_pnl_provider=lambda et_date: 0.0,
    )

    alerter.observe(_p0_2m_payload())

    assert ringing.messages == []
    assert alerter._tier_state.p0_delivered_today == 0
    assert alerter._tier_state.p0_delivered_week == 0
    assert alerter._sent_count == 0


def test_cross_date_failed_delivery_rebuilds_week_budget_from_ledger():
    """旧日失败回执晚到时只重算周预算，不碰新日的日预算。"""

    ledger = FakeLedger(week_p0=1, week_p0x=2)
    alerter = _sync_alerter(RecordingSender(), ledger=ledger)
    alerter._day = "2026-08-17"
    alerter._tier_state.p0_delivered_today = 1
    alerter._tier_state.p0_delivered_week = 2
    alerter._tier_state.p0x_delivered_week = 3
    message = _OutboundMessage(
        message_key="old-message",
        tier="P0",
        text="old P0",
        silent=False,
    )
    decision = SimpleNamespace(
        message_key=message.message_key,
        suppressed=False,
        delivery_channel="telegram",
        tier="P0",
        rule="p0_2m_candidate",
        session_scope="regular",
    )
    item = _WorkItem(
        et_date="2026-08-14",
        decisions=(decision,),
        messages=(message,),
    )

    alerter._release_failed_reservations(item, message)

    assert alerter._tier_state.p0_delivered_today == 1
    assert alerter._tier_state.p0_delivered_week == 1
    assert alerter._tier_state.p0x_delivered_week == 2


def test_failed_opening_delivery_releases_its_budget():
    opening_sender = RecordingSender(result=False)
    opening = _sync_alerter(
        opening_sender,
        silent_sender=RecordingSender(),
        ledger=FakeLedger(),
    )
    opening.observe_opening(_opening_payload())
    assert opening._tier_state.opening_delivered_today == 0
    assert opening._sent_count == 0
    assert ("opening_setup_v2", "TSLA", "09:30") in opening._seen


def test_send_failure_logs_once_per_state_change(caplog):
    sender = RecordingSender(result=False)
    alerter = _sync_alerter(sender)
    with caplog.at_level(
        logging.INFO, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.notify_system("通道断开甲")
        alerter.notify_system("通道断开乙")
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "telegram send failed" in r.getMessage()
        ]
        assert len(warnings) == 1  # 同一失败态只记一行

        sender.result = True
        alerter.notify_system("通道恢复")
        infos = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "delivery healthy" in r.getMessage()
        ]
        assert len(infos) == 1  # 恢复只记一行


def test_raising_sender_never_propagates(caplog):
    sender = RecordingSender(result=ConnectionError("boom"))
    alerter = _sync_alerter(sender)
    with caplog.at_level(
        logging.WARNING, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.notify_system("通道断开")  # 不得抛出
    assert any("ConnectionError" in r.getMessage() for r in caplog.records)


def test_observe_swallows_malformed_payload():
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)
    alerter.observe({"market_date_et": _DAY, "candidates": "not-a-list"})
    alerter.observe({})  # 无 ET 日期：安静跳过
    assert sender.messages == []
    assert ledger.decisions == []


def test_bounded_queue_drops_without_blocking(monkeypatch, caplog):
    sender = RecordingSender()
    alerter = IntradayOpportunityAlerter(
        sender, queue_maxsize=1, async_send=True, ledger=FakeLedger()
    )
    monkeypatch.setattr(alerter, "start", lambda: None)  # worker 永不消费
    with caplog.at_level(
        logging.WARNING, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.observe(_burst_payload("NVDA"))
        alerter.observe(_burst_payload("TSLA"))  # 队列已满：丢弃且不阻塞
    assert alerter._queue.qsize() == 1
    assert any("queue full" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("second_entrypoint", ["start", "dispatch"])
def test_concurrent_start_restores_once_and_creates_one_worker_set(
    monkeypatch, second_entrypoint,
):
    real_thread = threading.Thread
    first_restore_entered = threading.Event()
    release_first_restore = threading.Event()
    second_at_lock_or_finished = threading.Event()
    restores = []
    workers = []
    failures = []
    alerter = IntradayOpportunityAlerter(
        RecordingSender(),
        async_send=True,
        ledger=FakeLedger(),
        gate_state_provider=lambda: None,
        option_supplement_provider=lambda *_args: {},
    )

    class ObservedRLock:
        def __init__(self):
            self.lock = threading.RLock()

        def __enter__(self):
            if threading.current_thread().name == "second-producer":
                second_at_lock_or_finished.set()
            self.lock.acquire()
            return self

        def __exit__(self, *_args):
            self.lock.release()

    class FakeWorker:
        def __init__(self, *, target, name, daemon):
            self.name = name
            self.alive = False
            workers.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

    def restore_once():
        restores.append(threading.current_thread().name)
        if len(restores) == 1:
            first_restore_entered.set()
            assert release_first_restore.wait(timeout=2.0)

    def producer(*, second=False):
        try:
            if second and second_entrypoint == "dispatch":
                assert alerter._dispatch(
                    _WorkItem(et_date=_DAY, decisions=(), messages=()),
                ) is True
            else:
                alerter.start()
        except BaseException as exc:
            failures.append(exc)
        finally:
            if second:
                second_at_lock_or_finished.set()

    monkeypatch.setattr(alerter, "_lock", ObservedRLock())
    monkeypatch.setattr(alerter, "_restore_delivery_retries", restore_once)
    monkeypatch.setattr(
        "src.services.intraday_opportunity_alerter.threading.Thread", FakeWorker,
    )
    first = real_thread(target=producer, name="first-producer")
    second = real_thread(
        target=lambda: producer(second=True), name="second-producer",
    )
    first.start()
    try:
        assert first_restore_entered.wait(timeout=2.0)
        second.start()
        assert second_at_lock_or_finished.wait(timeout=2.0)
    finally:
        release_first_restore.set()
        first.join(timeout=2.0)
        if second.ident is not None:
            second.join(timeout=2.0)

    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert restores == ["first-producer"]
    assert len(workers) == 4
    assert len({worker.name for worker in workers}) == 4
    assert alerter.running


def test_async_worker_delivers_and_stops():
    sender = RecordingSender()
    alerter = IntradayOpportunityAlerter(
        sender, async_send=True, ledger=FakeLedger()
    )
    alerter.notify_system("异步投递检查")
    assert sender.delivered.wait(timeout=5.0)
    assert len(sender.messages) == 1
    alerter.stop()
    assert not alerter.running


def test_stop_drains_work_already_accepted_by_queue():
    entered_first_send = threading.Event()
    release_first_send = threading.Event()
    messages: list[str] = []

    def blocking_sender(text: str) -> bool:
        if not messages:
            entered_first_send.set()
            release_first_send.wait(timeout=5.0)
        messages.append(text)
        return True

    alerter = IntradayOpportunityAlerter(
        blocking_sender,
        async_send=True,
        ledger=FakeLedger(),
    )
    alerter.notify_system("第一条")
    assert entered_first_send.wait(timeout=2.0)
    alerter.notify_system("第二条")
    assert alerter._queue.qsize() == 1

    # 模拟 lifespan 已发出 stop：发送 worker 必须在退出前处理第二条。
    alerter._stop_event.set()
    release_first_send.set()
    alerter.stop(join_timeout_seconds=2.0)

    assert len(messages) == 2
    assert any("第一条" in message for message in messages)
    assert any("第二条" in message for message in messages)
    assert not alerter.running


def test_slow_context_refresh_does_not_delay_queued_opening_delivery():
    sender = RecordingSender()
    provider_started = threading.Event()
    provider_release = threading.Event()

    def slow_gate_provider():
        provider_started.set()
        provider_release.wait(timeout=5.0)
        return None

    alerter = IntradayOpportunityAlerter(
        sender,
        async_send=True,
        ledger=FakeLedger(),
        gate_state_provider=slow_gate_provider,
        context_refresh_seconds=300,
    )
    alerter.start()
    assert provider_started.wait(timeout=2.0)
    alerter.notify_system("发送线程不应等待慢上下文")
    assert sender.delivered.wait(timeout=1.0)
    provider_release.set()
    alerter.stop()
    assert not alerter.running


def _burst_payload(ticker, day=_DAY):
    return _payload(
        market_date=day,
        candidates=[_candidate(ticker, bursts=_bursts(current=_current_window()))],
    )


# ---------------------------------------------------------------------------
# warm 入口的 observer 接线
# ---------------------------------------------------------------------------


def test_alert_payload_enrichment_never_quotes_legacy_near_p0_candidates():
    from api.app import _enrich_intraday_alert_payload

    payload = _tier_payload()
    payload["candidates"].append(_tier_candidate("TSLA"))
    calls = []

    class FakeFrictionService:
        def enrich_payload(self, value, *, selected_tickers):
            calls.append((value, selected_tickers))
            return {**value, "enriched": True}

    result = _enrich_intraday_alert_payload(payload, FakeFrictionService())
    assert result["enriched"] is True
    assert calls == [(payload, [])]


def test_alert_payload_enrichment_also_fetches_strict_opening_candidate():
    from api.app import _enrich_intraday_alert_payload

    payload = _opening_payload(_opening_candidate("TSLA"))
    calls = []

    class FakeFrictionService:
        def enrich_payload(self, value, *, selected_tickers):
            calls.append(selected_tickers)
            return value

    _enrich_intraday_alert_payload(payload, FakeFrictionService())
    assert calls == [["TSLA"]]


def test_warm_scan_hands_payload_copy_to_observer(monkeypatch):
    from api.v1.endpoints import opportunities as opps

    fake_payload = {
        "market_date_et": _DAY,
        "candidates": [{"ticker": "NVDA", "nested": {"value": 1}}],
    }

    def fake_get_or_compute(key, factory, **kwargs):
        kwargs["meta_out"].update(
            {
                "led_flight": True,
                "generated_in_seconds": 1.5,
                "generated_by": "warm_scheduler",
            }
        )
        return fake_payload

    monkeypatch.setattr(
        opps,
        "_intraday_top_key_and_factory",
        lambda symbols, focus, limit: (("k",), dict),
    )
    monkeypatch.setattr(opps, "_get_or_compute_scan", fake_get_or_compute)

    order = []
    observed = []

    def enrich(payload):
        order.append("enrich")
        payload["candidates"][0]["option_contract_friction"] = {
            "state": "unavailable"
        }
        return payload

    def observe(payload):
        order.append("observe")
        observed.append(payload)
        payload["candidates"][0]["nested"]["value"] = 999

    summary = opps.warm_default_intraday_top_scan(
        payload_observer=observe,
        payload_enricher=enrich,
    )
    assert order == ["enrich", "observe"]
    assert observed[0]["candidates"][0]["option_contract_friction"] == {
        "state": "unavailable"
    }
    assert fake_payload["candidates"][0] == {
        "ticker": "NVDA",
        "nested": {"value": 1},
    }
    assert summary["led_flight"] is True

    # 增强器即使先污染入参后抛错，observer 也只收到干净原始副本。
    fallback = []

    def enrich_boom(payload):
        payload["candidates"][0]["ticker"] = "CORRUPTED"
        raise RuntimeError("quote provider bug")

    summary = opps.warm_default_intraday_top_scan(
        payload_observer=fallback.append,
        payload_enricher=enrich_boom,
    )
    assert len(fallback) == 1
    observer_stamp = fallback[0].pop("alert_observed_at")
    assert datetime.fromisoformat(observer_stamp).tzinfo is not None
    assert fallback[0] == fake_payload
    assert summary["led_flight"] is True

    # observer 异常绝不打断预热小结（合同之外再兜一层）。
    def boom(_payload):
        raise RuntimeError("observer bug")

    summary = opps.warm_default_intraday_top_scan(payload_observer=boom)
    assert summary["led_flight"] is True


# ---------------------------------------------------------------------------
# lifespan 开/关接线（提示器依赖预热调度器）
# ---------------------------------------------------------------------------

class _FakeWarmScheduler:
    def __init__(
        self, tick, *, interval_seconds, channel_watch=None,
        channel_notifier=None, align_to_minute_closes=True, thread_name=None,
        defer_during_opening=False,
    ):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def test_lifespan_wires_alerter_only_with_warm_scheduler(
    monkeypatch, tmp_path: Path
):
    calls = []

    class FakeAlerter:
        def __init__(
            self,
            send_text,
            *,
            send_silent_text=None,
            send_text_with_deadline=None,
            gate_state_provider=None,
            open_positions_provider=None,
            daily_pnl_provider=None,
            option_supplement_provider=None,
            p0_shadow_mobile_enabled=False,
            max_alerts_per_day,
        ):
            calls.append(
                (
                    "init",
                    callable(send_text),
                    callable(send_silent_text),
                    callable(send_text_with_deadline),
                    callable(gate_state_provider),
                    callable(open_positions_provider),
                    callable(daily_pnl_provider),
                    max_alerts_per_day,
                    p0_shadow_mobile_enabled,
                )
            )

        def start(self):
            calls.append(("start", True))

        def stop(self):
            calls.append(("stop", True))

        def observe(self, payload):
            pass

        def notify_system(self, text):
            calls.append(("system", text))

    class FakeTelegramSender:
        def __init__(self, config):
            pass

        def send_to_telegram(
            self,
            text,
            *,
            disable_notification=False,
            delivery_deadline=None,
        ):
            return True

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            intraday_refresh_scheduler_enabled=True,
            intraday_refresh_interval_seconds=45,
            intraday_alerts_enabled=True,
            intraday_p0_2m_shadow_mobile_enabled=True,
            intraday_alerts_max_per_day=7,
            telegram_bot_token="test-token",
            telegram_chat_id="test-chat",
        ),
    )
    monkeypatch.setattr(
        "src.services.intraday_warm_cache_scheduler.IntradayWarmCacheScheduler",
        _FakeWarmScheduler,
    )
    monkeypatch.setattr(
        "src.services.intraday_opportunity_alerter.IntradayOpportunityAlerter",
        FakeAlerter,
    )
    monkeypatch.setattr(
        "src.notification_sender.telegram_sender.TelegramSender",
        FakeTelegramSender,
    )

    with TestClient(create_app(static_dir=tmp_path / "no-static")) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == [
        ("init", True, True, True, True, True, True, 7, True),
        ("start", True),
        ("stop", True),
    ]


def test_default_gate_state_provider_reuses_edge_panel_gate(monkeypatch):
    """生产 P0 gate 与 Journal 面板共用同一计算，不再永久标缺。"""

    expected = {
        "allow_0_1dte": True,
        "default_bucket": "2-7",
        "blocked_by": [],
        "data_status": "ok",
    }
    monkeypatch.setattr(
        "src.journal.edge_panel.compute_gate",
        lambda: expected,
    )

    from src.services.intraday_alert_context import default_gate_state_provider

    assert default_gate_state_provider() is expected


def test_lifespan_keeps_alerter_off_without_warm_scheduler(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(intraday_alerts_enabled=True),  # 预热未开
    )
    monkeypatch.setattr(
        "src.services.intraday_opportunity_alerter.IntradayOpportunityAlerter",
        lambda *args, **kwargs: pytest.fail(
            "alerter must stay off when the warm scheduler is disabled"
        ),
    )

    app = create_app(static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert not hasattr(app.state, "intraday_opportunity_alerter")


def test_lifespan_keeps_alerter_off_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            intraday_refresh_scheduler_enabled=True,
            intraday_refresh_interval_seconds=45,
        ),
    )
    monkeypatch.setattr(
        "src.services.intraday_warm_cache_scheduler.IntradayWarmCacheScheduler",
        _FakeWarmScheduler,
    )
    monkeypatch.setattr(
        "src.services.intraday_opportunity_alerter.IntradayOpportunityAlerter",
        lambda *args, **kwargs: pytest.fail(
            "alerter must stay off unless INTRADAY_ALERTS_ENABLED=true"
        ),
    )

    app = create_app(static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert not hasattr(app.state, "intraday_opportunity_alerter")


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------

@patch("src.config.setup_env")
@patch.object(Config, "_parse_litellm_yaml", return_value=[])
def test_config_parses_intraday_alert_settings(
    _mock_parse_litellm_yaml, _mock_setup_env
):
    try:
        # 隔离真实 .env：INTRADAY_ALERTS_ENABLED 属 env-file 优先键，
        # 本机 .env 的真实值会穿透 patch.dict —— 与 test_config_env_compat
        # 同法直接桩掉文件读取。
        with patch.object(Config, "_get_env_file_value", return_value=None):
            with patch.dict(
                os.environ,
                {
                    "STOCK_LIST": "600519",
                    "INTRADAY_ALERTS_ENABLED": "true",
                    "INTRADAY_P0_2M_SHADOW_MOBILE_ENABLED": "true",
                    "INTRADAY_ALERTS_MAX_PER_DAY": "5",
                },
                clear=True,
            ):
                config = Config._load_from_env()
            assert config.intraday_alerts_enabled is True
            assert config.intraday_p0_2m_shadow_mobile_enabled is True
            assert config.intraday_alerts_max_per_day == 5

            with patch.dict(
                os.environ,
                {
                    "STOCK_LIST": "600519",
                    "INTRADAY_ALERTS_MAX_PER_DAY": "0",
                },
                clear=True,
            ):
                config = Config._load_from_env()
            assert config.intraday_alerts_enabled is False  # 默认关闭
            assert config.intraday_p0_2m_shadow_mobile_enabled is False
            assert config.intraday_alerts_max_per_day == 1  # 最小 1 钳制
    finally:
        Config.reset_instance()


def test_intraday_policy_flip_re_alerts_with_correction_prefix():
    """日内池盘中翻转（深度层轮换）必须重新提醒并标「更正」，
    否则用户拿着过时的全天断言；同值绝不重复。"""

    seen: set[tuple[str, ...]] = set()
    unavailable = {"lane_availability": {"policy_state": "unavailable"}}
    available = {"lane_availability": {"policy_state": "available"}}

    first = detect_intraday_alerts(unavailable, seen=seen)
    assert [a.text for a in first] == ["今日深度层标的在 0-7DTE 窗口内无可用到期"]
    # 同值：不重复。
    assert detect_intraday_alerts(unavailable, seen=seen) == []
    flipped = detect_intraday_alerts(available, seen=seen)
    assert len(flipped) == 1
    assert flipped[0].text.startswith("日内池更正：今日 0-7DTE")
    # 翻转后的值也只提醒一次。
    assert detect_intraday_alerts(available, seen=seen) == []


def test_queue_full_rolls_back_dedupe_so_facts_retry_next_tick(monkeypatch):
    """对抗复核修正：队列满丢弃本 tick 消息时必须回滚去重键与计数，
    同一事实下一 tick 重检——否则一次拥塞永久吞掉当日提示。"""

    sent: list[str] = []
    alerter = IntradayOpportunityAlerter(
        lambda text: sent.append(text) or True,
        queue_maxsize=1,
        ledger=FakeLedger(),
    )
    # 显式阻止 _dispatch 的惰性自启动，避免真实 worker 与断言竞争：第一条
    # 稳定占满队列，第二条稳定触发 queue.Full。
    monkeypatch.setattr(alerter, "start", lambda: None)
    payload_a = {
        "market_date_et": "2026-08-14",
        "session_state": "premarket",
        "quote_session_scope": "current_session",
        "candidates": [{"ticker": "AAA", "pre_change_percent": 3.0}],
    }
    payload_b = {
        "market_date_et": "2026-08-14",
        "session_state": "premarket",
        "quote_session_scope": "current_session",
        "candidates": [{"ticker": "BBB", "pre_change_percent": -2.5}],
    }
    alerter.observe(payload_a)  # 入队成功，键已登记
    alerter.observe(payload_b)  # 队列满 → 丢弃 + 回滚
    assert ("premarket_move", "BBB") not in alerter._seen
    assert ("premarket_move", "AAA") in alerter._seen
    # 下一 tick 同一事实重检成功（队列腾出后可入队）。
    alerter._queue.get_nowait()
    alerter.observe(payload_b)
    assert ("premarket_move", "BBB") in alerter._seen


def test_lifespan_runs_p0_shadow_without_telegram_creds(
    monkeypatch, tmp_path: Path, caplog
):
    """Research/ledger stays live without a mobile delivery channel."""

    calls = []

    class FakeAlerter:
        def __init__(self, send_text, **kwargs):
            calls.append(("alerter_init", callable(send_text), kwargs["send_silent_text"]))

        def start(self):
            calls.append(("alerter_start",))

        def stop(self):
            calls.append(("alerter_stop",))

        def observe(self, payload):
            calls.append(("observe", payload))

        def observe_opening(self, payload):
            calls.append(("observe_opening", payload))

        def notify_system(self, text):
            calls.append(("system", text))

    class FakeMonitor:
        def __init__(self, symbols, observer, **kwargs):
            calls.append(
                (
                    "monitor_init",
                    tuple(symbols),
                    callable(observer),
                    kwargs["action_enabled"],
                    kwargs["delivery_ready"],
                    kwargs["delivery_unavailable_reason"],
                    kwargs["system_notifier"],
                )
            )

        def start(self):
            calls.append(("monitor_start",))

        def stop(self):
            calls.append(("monitor_stop",))

        def scan_once(self):
            return {"candidate_count": 0}

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            intraday_refresh_scheduler_enabled=True,
            intraday_refresh_interval_seconds=60,
            intraday_alerts_enabled=False,
            intraday_alerts_max_per_day=7,
            intraday_p0_2m_enabled=True,
            intraday_p0_2m_action_enabled=False,
            intraday_p0_2m_activation_artifact=None,
            intraday_watchlist=("TSLA", "META"),
            telegram_bot_token=None,
            telegram_chat_id=None,
        ),
    )
    monkeypatch.setattr(
        "src.services.intraday_warm_cache_scheduler.IntradayWarmCacheScheduler",
        _FakeWarmScheduler,
    )
    monkeypatch.setattr(
        "src.services.intraday_opportunity_alerter.IntradayOpportunityAlerter",
        FakeAlerter,
    )
    monkeypatch.setattr(
        "src.services.intraday_p0_2m_monitor.IntradayP02mMonitor",
        FakeMonitor,
    )

    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="api.app"):
        with TestClient(create_app(static_dir=tmp_path / "no-static")) as client:
            assert client.get("/api/health").status_code == 200

    assert ("alerter_init", True, None) in calls
    assert (
        "monitor_init",
        ("TSLA", "META"),
        True,
        False,
        False,
        "telegram_credentials_missing",
        None,
    ) in calls
    assert ("monitor_start",) in calls
    assert ("monitor_stop",) in calls
    assert ("alerter_stop",) in calls
    # action=false is an intentional shadow configuration, so missing mobile
    # credentials are not an incident and should not produce warning spam.
    assert not any("Telegram 凭据缺失" in r.getMessage() for r in caplog.records)


def test_notify_system_bypasses_budgets_and_lands_in_ledger():
    """系统通知（通道断开/恢复）不占任何预算/上限，带【系统】前缀并入台账。"""

    sent: list[str] = []
    ledger = FakeLedger()
    alerter = IntradayOpportunityAlerter(
        lambda text: sent.append(text) or True,
        max_alerts_per_day=1,
        async_send=False,
        ledger=ledger,
    )
    alerter.notify_system("行情通道断开测试")
    assert sent and sent[0].startswith("【系统】")
    system_rows = [d for d in ledger.decisions if d.tier == "system"]
    assert len(system_rows) == 1
    assert system_rows[0].rule == "system_notice"
    # 未消耗全局 Telegram 行上限。
    assert alerter._sent_count == 0


# ---------------------------------------------------------------------------
# D6：gate 红灯抑制 / red_lanes 钩子 / p0_candidate_eval P3 写入路径（端到端）
# ---------------------------------------------------------------------------

def test_gate_red_does_not_suppress_generic_zero_to_seven_availability_fact():
    """0-1DTE gate 红灯不能抹掉仍可能含 2-7DTE 的日内池可用性事实。"""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=RecordingSender(),
        gate_state_provider=lambda: {"allow_0_1dte": False},
    )
    alerter.observe(
        _payload(lane=_lane(("NVDA",), day_type="intraday_available", dte=(0,)))
    )
    rows = [d for d in ledger.decisions if d.rule == "intraday_policy"]
    assert len(rows) == 1
    row = rows[0]
    assert row.tier == "P2"
    assert not row.suppressed
    assert row.delivery_channel == "web_only"
    assert sender.messages == []


def test_regime_red_lane_does_not_change_strict_2m_p0_fact():
    """Regime may annotate context but is not one of the four P0 gates."""
    sender = RecordingSender()
    silent = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=silent,
        gate_state_provider=lambda: {
            "allow_0_1dte": True,
            "red_lanes": ["intraday_0_7dte"],
        },
        daily_pnl_provider=lambda et_date: 0.0,  # R6 确证未触线
    )
    alerter.observe(_p0_2m_payload())
    assert len(sender.messages) == 1
    p0_rows = [
        d for d in ledger.decisions if d.rule == "p0_2m_candidate"
    ]
    assert len(p0_rows) == 1
    assert p0_rows[0].tier == "P0"
    assert not p0_rows[0].suppressed


def test_p0_2m_not_qualified_observation_does_not_flood_push_ledger():
    """Four-gate misses stay in aggregate monitor health, not per-window rows."""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(
        sender,
        ledger=ledger,
        silent_sender=RecordingSender(),
    )
    first = _p0_2m_payload(_p0_2m_candidate(state="not_qualified"))
    alerter.observe(first)
    alerter.observe(first)
    second = _p0_2m_payload(
        _p0_2m_candidate(
            state="not_qualified",
            window_end="2026-08-14T10:50:00-04:00",
        ),
        as_of="2026-08-14T14:50:05+00:00",
    )
    alerter.observe(second)
    assert all(d.rule != "p0_2m_observation" for d in ledger.decisions)
    assert not any(key[0] == "p0_2m_observation" for key in alerter._seen)
    assert sender.messages == []


def test_p0_candidate_eval_not_written_when_candidate_not_moving():
    """D6 反向：双窗瞬时信号不成立（标的没在动）时，差 ≥2 条不刷台账。"""
    sender = RecordingSender()
    ledger = FakeLedger()
    alerter = _sync_alerter(sender, ledger=ledger)
    candidate = _tier_candidate("NVDA", friction=False, net=0.1)  # 位移未达线
    payload = _payload(candidates=[candidate], lane=_lane(("NVDA",)))
    payload["universe_scan"]["plan_always_include"] = []
    alerter.observe(payload)
    alerter.observe(payload)
    assert all(d.rule != "p0_candidate_eval" for d in ledger.decisions)


# ---------------------------------------------------------------------------
# D7：拥塞事件逐次记日志 / 系统通知被丢时留痕
# ---------------------------------------------------------------------------

def test_queue_full_logs_once_per_congestion_episode(monkeypatch, caplog):
    """D7：成功入队重置 queue 指纹——每次**新的**拥塞事件各记一行日志，
    而不是整个进程生命周期只记第一次。"""
    alerter = IntradayOpportunityAlerter(
        RecordingSender(), queue_maxsize=1, async_send=True, ledger=FakeLedger()
    )
    monkeypatch.setattr(alerter, "start", lambda: None)  # worker 永不消费
    with caplog.at_level(
        logging.WARNING, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.observe(_burst_payload("AAA"))  # 占满队列
        alerter.observe(_burst_payload("BBB"))  # 拥塞事件 1 → 记一行
        alerter._queue.get_nowait()             # 拥塞解除
        alerter.observe(_burst_payload("BBB"))  # 成功入队 → 指纹重置
        alerter.observe(_burst_payload("CCC"))  # 拥塞事件 2 → 再记一行
    warnings = [
        r for r in caplog.records if "queue full" in r.getMessage()
    ]
    assert len(warnings) == 2


def test_notify_system_drop_leaves_a_log_line(monkeypatch, caplog):
    """D7：系统通知无台账行可回滚——被队列满丢弃时必须留一行日志
    （文档化的唯一痕迹），逐次记录、不做指纹去重。"""
    alerter = IntradayOpportunityAlerter(
        RecordingSender(), queue_maxsize=1, async_send=True, ledger=FakeLedger()
    )
    monkeypatch.setattr(alerter, "start", lambda: None)
    with caplog.at_level(
        logging.WARNING, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.notify_system("通道断开甲")  # 占满队列
        alerter.notify_system("通道断开乙")  # 丢弃 → 留痕
        alerter.notify_system("通道断开丙")  # 再丢 → 再留痕
    drops = [
        r
        for r in caplog.records
        if "system notice dropped" in r.getMessage()
    ]
    assert len(drops) == 2
    assert any("通道断开乙" in r.getMessage() for r in drops)

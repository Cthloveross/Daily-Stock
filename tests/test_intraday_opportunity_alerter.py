# -*- coding: utf-8 -*-
"""Deterministic tests for the intraday opportunity alerter.

覆盖：五条 v1 规则的触发/不触发与消息文案、盘前时段闸门、黑名单标注、
（标的×规则）日内去重与 ET 日期翻转清零、全局日上限与终止通知、单 tick
批量合并为一条消息、Telegram 失败的安静降级（状态变化才记日志）、有界
队列不阻塞、warm 入口的 observer 接线，以及 lifespan 开/关接线与配置解析。
全部使用夹具载荷 + mock 发送器，零网络零真实时钟依赖。
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from api.app import create_app
from src.config import Config
from src.services.intraday_opportunity_alerter import (
    ALERT_FOOTER,
    IntradayOpportunityAlerter,
    detect_intraday_alerts,
)

_DAY = "2026-08-14"
_AS_OF = "2026-08-14T14:31:00+00:00"  # 10:31 ET


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
):
    return {
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


def _sync_alerter(sender, **kwargs):
    return IntradayOpportunityAlerter(sender, async_send=False, **kwargs)


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


# ---------------------------------------------------------------------------
# 规则 5：日型提醒
# ---------------------------------------------------------------------------

def test_day_type_alert_waits_for_known_and_fires_once():
    seen: set = set()
    unknown = _payload(lane={"day_type": "unknown"})
    assert detect_intraday_alerts(unknown, seen=seen) == []

    available = _payload(lane={"day_type": "intraday_available"})
    alerts = detect_intraday_alerts(available, seen=seen)
    assert [a.text for a in alerts] == ["今日有 0DTE：日内车道可用"]
    assert detect_intraday_alerts(available, seen=seen) == []

    overnight = _payload(lane={"day_type": "overnight_only"})
    alerts = detect_intraday_alerts(overnight, seen=set())
    assert [a.text for a in alerts] == ["今日无 0DTE：过夜日 · 日内关闭"]


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
# 去重翻转 / 上限 / 批量合并 / 页脚
# ---------------------------------------------------------------------------

def test_daily_dedupe_clears_on_et_date_rollover():
    sender = RecordingSender()
    alerter = _sync_alerter(sender)
    payload = _payload(
        candidates=[_candidate("NVDA", bursts=_bursts(current=_current_window()))]
    )
    alerter.observe(payload)
    alerter.observe(payload)
    assert len(sender.messages) == 1

    next_day = _payload(
        market_date="2026-08-17",
        candidates=[_candidate("NVDA", bursts=_bursts(current=_current_window()))],
    )
    alerter.observe(next_day)
    assert len(sender.messages) == 2


def test_batch_merges_one_message_with_header_and_single_footer():
    sender = RecordingSender()
    alerter = _sync_alerter(sender)
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
    assert len(sender.messages) == 1
    message = sender.messages[0]
    lines = message.split("\n")
    assert lines[0] == "【盘中提示 · 10:31 ET】"
    assert "NVDA 放量 2.3×" in message
    assert "TSLA 放量 2.8×" in message
    assert message.count(ALERT_FOOTER) == 1
    assert lines[-1] == ALERT_FOOTER


def test_global_cap_truncates_sends_final_notice_and_stops():
    sender = RecordingSender()
    alerter = _sync_alerter(sender, max_alerts_per_day=2)
    alerter.observe(
        _payload(
            candidates=[
                _candidate("NVDA", bursts=_bursts(current=_current_window())),
                _candidate("TSLA", bursts=_bursts(current=_current_window())),
                _candidate("MRVL", bursts=_bursts(current=_current_window())),
            ]
        )
    )
    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert "NVDA" in message and "TSLA" in message
    assert "MRVL" not in message
    assert "今日提示已达上限 2 条" in message

    # 触顶后当日不再发送，哪怕出现全新事实。
    alerter.observe(
        _payload(
            candidates=[
                _candidate("AVGO", bursts=_bursts(current=_current_window()))
            ]
        )
    )
    assert len(sender.messages) == 1

    # ET 日期翻转后恢复。
    alerter.observe(
        _payload(
            market_date="2026-08-17",
            candidates=[
                _candidate("AVGO", bursts=_bursts(current=_current_window()))
            ],
        )
    )
    assert len(sender.messages) == 2


def test_max_per_day_clamps_to_minimum_one():
    alerter = _sync_alerter(RecordingSender(), max_alerts_per_day=0)
    assert alerter.max_alerts_per_day == 1


# ---------------------------------------------------------------------------
# 发送失败安静降级 / observe 永不抛出 / 队列有界
# ---------------------------------------------------------------------------

def _burst_payload(ticker, day=_DAY):
    return _payload(
        market_date=day,
        candidates=[_candidate(ticker, bursts=_bursts(current=_current_window()))],
    )


def test_send_failure_logs_once_per_state_change(caplog):
    sender = RecordingSender(result=False)
    alerter = _sync_alerter(sender)
    with caplog.at_level(
        logging.INFO, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.observe(_burst_payload("NVDA"))
        alerter.observe(_burst_payload("TSLA"))
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1  # 同一失败态只记一行

        sender.result = True
        alerter.observe(_burst_payload("MRVL"))
        infos = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "healthy" in r.getMessage()
        ]
        assert len(infos) == 1  # 恢复只记一行


def test_raising_sender_never_propagates(caplog):
    sender = RecordingSender(result=ConnectionError("boom"))
    alerter = _sync_alerter(sender)
    with caplog.at_level(
        logging.WARNING, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.observe(_burst_payload("NVDA"))  # 不得抛出
    assert any("ConnectionError" in r.getMessage() for r in caplog.records)


def test_observe_swallows_malformed_payload():
    sender = RecordingSender()
    alerter = _sync_alerter(sender)
    alerter.observe({"market_date_et": _DAY, "candidates": "not-a-list"})
    alerter.observe({})  # 无 ET 日期：安静跳过
    assert sender.messages == []


def test_bounded_queue_drops_without_blocking(monkeypatch, caplog):
    sender = RecordingSender()
    alerter = IntradayOpportunityAlerter(sender, queue_maxsize=1, async_send=True)
    monkeypatch.setattr(alerter, "start", lambda: None)  # worker 永不消费
    with caplog.at_level(
        logging.WARNING, logger="src.services.intraday_opportunity_alerter"
    ):
        alerter.observe(_burst_payload("NVDA"))
        alerter.observe(_burst_payload("TSLA"))  # 队列已满：丢弃且不阻塞
    assert alerter._queue.qsize() == 1
    assert any("queue full" in r.getMessage() for r in caplog.records)


def test_async_worker_delivers_and_stops():
    sender = RecordingSender()
    alerter = IntradayOpportunityAlerter(sender, async_send=True)
    alerter.observe(_burst_payload("NVDA"))
    assert sender.delivered.wait(timeout=5.0)
    assert len(sender.messages) == 1
    alerter.stop()
    assert not alerter.running


# ---------------------------------------------------------------------------
# warm 入口的 observer 接线
# ---------------------------------------------------------------------------

def test_warm_scan_hands_payload_copy_to_observer(monkeypatch):
    from api.v1.endpoints import opportunities as opps

    fake_payload = {"market_date_et": _DAY, "candidates": []}

    def fake_get_or_compute(key, factory, **kwargs):
        kwargs["meta_out"].update(
            {
                "led_flight": True,
                "generated_in_seconds": 1.5,
                "generated_by": "warm_scheduler",
            }
        )
        return dict(fake_payload)

    monkeypatch.setattr(
        opps,
        "_intraday_top_key_and_factory",
        lambda symbols, focus, limit: (("k",), dict),
    )
    monkeypatch.setattr(opps, "_get_or_compute_scan", fake_get_or_compute)

    observed = []
    summary = opps.warm_default_intraday_top_scan(payload_observer=observed.append)
    assert observed == [fake_payload]
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
    def __init__(self, tick, *, interval_seconds):
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
        def __init__(self, send_text, *, max_alerts_per_day):
            calls.append(("init", callable(send_text), max_alerts_per_day))

        def start(self):
            calls.append(("start", True))

        def stop(self):
            calls.append(("stop", True))

        def observe(self, payload):
            pass

    class FakeTelegramSender:
        def __init__(self, config):
            pass

        def send_to_telegram(self, text):
            return True

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            intraday_refresh_scheduler_enabled=True,
            intraday_refresh_interval_seconds=45,
            intraday_alerts_enabled=True,
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
        ("init", True, 7),
        ("start", True),
        ("stop", True),
    ]


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
        with patch.dict(
            os.environ,
            {
                "STOCK_LIST": "600519",
                "INTRADAY_ALERTS_ENABLED": "true",
                "INTRADAY_ALERTS_MAX_PER_DAY": "5",
            },
            clear=True,
        ):
            config = Config._load_from_env()
        assert config.intraday_alerts_enabled is True
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
        assert config.intraday_alerts_max_per_day == 1  # 最小 1 钳制
    finally:
        Config.reset_instance()


def test_day_type_flip_re_alerts_with_correction_prefix():
    """对抗复核修正：日型盘中翻转（深度层轮换）必须重新提醒并标「更正」，
    否则用户拿着过时的全天断言；同值绝不重复。"""

    seen: set[tuple[str, ...]] = set()
    overnight = {"lane_availability": {"day_type": "overnight_only"}}
    intraday = {"lane_availability": {"day_type": "intraday_available"}}

    first = detect_intraday_alerts(overnight, seen=seen)
    assert [a.text for a in first] == ["今日无 0DTE：过夜日 · 日内关闭"]
    # 同值：不重复。
    assert detect_intraday_alerts(overnight, seen=seen) == []
    flipped = detect_intraday_alerts(intraday, seen=seen)
    assert len(flipped) == 1
    assert flipped[0].text.startswith("日型更正：今日有 0DTE")
    # 翻转后的值也只提醒一次。
    assert detect_intraday_alerts(intraday, seen=seen) == []


def test_queue_full_rolls_back_dedupe_so_facts_retry_next_tick():
    """对抗复核修正：队列满丢弃本 tick 消息时必须回滚去重键与计数，
    同一事实下一 tick 重检——否则一次拥塞永久吞掉当日提示。"""

    sent: list[str] = []
    alerter = IntradayOpportunityAlerter(
        lambda text: sent.append(text) or True,
        queue_maxsize=1,
    )
    # 不启动 worker：第一条占满队列，第二条触发 queue.Full。
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


def test_lifespan_disables_alerter_when_telegram_creds_missing(
    monkeypatch, tmp_path: Path, caplog
):
    """对抗复核修正：开了提示器但缺 Telegram 凭据 → 一次启动警告 + 干净禁用，
    绝不构造 alerter（否则 TelegramSender 会对每条消息各刷一行 WARNING）。"""

    constructed = []

    class FakeAlerter:
        def __init__(self, *a, **k):
            constructed.append(True)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(
        "src.config.get_config",
        lambda: SimpleNamespace(
            intraday_refresh_scheduler_enabled=True,
            intraday_refresh_interval_seconds=45,
            intraday_alerts_enabled=True,
            intraday_alerts_max_per_day=7,
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

    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="api.app"):
        with TestClient(create_app(static_dir=tmp_path / "no-static")) as client:
            assert client.get("/api/health").status_code == 200

    assert constructed == []
    assert any("Telegram 凭据缺失" in r.getMessage() for r in caplog.records)

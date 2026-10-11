# -*- coding: utf-8 -*-
"""Deterministic tests for styleMatch v1（形态相似度标注，纯函数）。

Fixture 策略：每个 15 分钟桶只放一根 5m K 线即可形成一根 15m 聚合 K 线，
让 S1 的 swing-low 几何完全可控；S2/S3 只依赖会话快照派生字段，可以在
bars=None 时单独驱动。每个 setup 都覆盖 matched / partial / not_matched /
unavailable 四态与诚实理由。
"""
from __future__ import annotations

import pytest

from src.opportunities.intraday_setups import (
    S1_MIN_15M_BARS,
    SETUP_GAP_ATR_MULTIPLE_MIN,
    SETUP_GAP_PERCENT_MIN,
    SETUP_MATCH_BASIS,
    STYLE_MATCH_VERSION,
    aggregate_15m_bars,
    compute_setup_match_profile,
)
from src.opportunities.intraday_bursts import filter_regular_session_bars
from src.opportunities.intraday_top import (
    GAP_ATR_MULTIPLE_SUPPORT_MIN,
    GAP_PERCENT_SUPPORT_MIN,
)


def _bar(date_text, time_text, open_, high, low, close, volume=1_000.0):
    return {
        "date": f"{date_text}T{time_text}:00-04:00",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _s1_bars(date_text="2026-07-28", *, break_close=101.2, rising=True):
    """One 5m bar per 15m bucket → six 15m bars with controlled lows/highs.

    rising=True：swing lows 99.0(09:45) → 99.5(10:15)，结构高点 100.8，
    10:45 桶收盘 break_close 决定是否突破。rising=False 交换两个低点。
    """

    low_a, low_b = (99.0, 99.5) if rising else (99.5, 99.0)
    return [
        _bar(date_text, "09:30", 100.5, 101.0, 100.0, 100.6),
        _bar(date_text, "09:45", 100.4, 100.5, low_a, low_a + 0.4),
        _bar(date_text, "10:00", 99.9, 100.8, 99.8, 100.6),
        _bar(date_text, "10:15", 100.5, 100.6, low_b, low_b + 0.4),
        _bar(date_text, "10:30", 100.0, 100.7, 100.0, 100.5),
        _bar(date_text, "10:45", 100.6, max(101.4, break_close + 0.2), 100.4, break_close),
    ]


def _profile(bars=None, **overrides):
    kwargs = dict(
        market_date_et="2026-07-28",
        quote_session_scope="current_session",
        last_price=None,
        session_open=None,
        session_low=None,
        gap_percent=None,
        gap_atr_multiple=None,
        gap_reference_close=None,
        vwap=None,
        spy_vwap_position=None,
        playbook_refs=None,
    )
    kwargs.update(overrides)
    return compute_setup_match_profile(bars, **kwargs)


def _setup(profile, key):
    return next(row for row in profile["setups"] if row["setup_key"] == key)


# S2 matched 的快照输入：跳空 +2%（1.0×ATR）、最低未回补、现价 ≥ VWAP。
_S2_QUOTE = dict(
    last_price=103.0,
    session_open=102.0,
    session_low=101.5,
    gap_percent=2.0,
    gap_atr_multiple=1.0,
    gap_reference_close=100.0,
    vwap=102.5,
)


class TestS1HigherLowsBreakout:
    def test_rising_lows_with_break_is_matched(self):
        profile = _profile(_s1_bars())
        s1 = _setup(profile, "S1")
        assert s1["state"] == "matched"
        # 证据：低点序列（时间+低点）、结构高点、突破窗口。
        assert "低点序列 09:45 99.00 → 10:15 99.50" in s1["evidence_lines"][0]
        assert "结构高点 100.80" in s1["evidence_lines"][1]
        assert "突破 11:00 收 101.20 > 100.80" in s1["evidence_lines"][2]
        assert profile["matched_setups"] == ["S1"]
        assert profile["bar_count_15m"] == 6

    def test_rising_lows_without_break_is_partial(self):
        profile = _profile(_s1_bars(break_close=100.5))
        s1 = _setup(profile, "S1")
        assert s1["state"] == "partial"
        assert "尚未突破" in s1["reason"]
        assert profile["partial_setups"] == ["S1"]

    def test_last_price_can_supply_the_break(self):
        profile = _profile(_s1_bars(break_close=100.5), last_price=101.0)
        s1 = _setup(profile, "S1")
        assert s1["state"] == "matched"
        assert "现价 101.00 > 100.80" in s1["evidence_lines"][2]

    def test_falling_lows_are_not_matched(self):
        profile = _profile(_s1_bars(rising=False))
        s1 = _setup(profile, "S1")
        assert s1["state"] == "not_matched"
        assert "未连续抬高" in s1["reason"]

    def test_fewer_than_three_15m_bars_is_unavailable_with_reason(self):
        bars = _s1_bars()[:2]  # 只有 09:30 / 09:45 两个 15m 桶。
        profile = _profile(bars)
        s1 = _setup(profile, "S1")
        assert s1["state"] == "unavailable"
        assert f"不足 {S1_MIN_15M_BARS} 根" in s1["reason"]

    def test_no_bars_is_unavailable_not_a_shape_claim(self):
        profile = _profile(None)
        s1 = _setup(profile, "S1")
        assert s1["state"] == "unavailable"
        assert "无该交易时段" in s1["reason"]

    def test_flat_session_has_no_swing_lows(self):
        bars = [
            _bar("2026-07-28", time_text, 100.0, 100.5, 99.5, 100.0)
            for time_text in ("09:30", "09:45", "10:00", "10:15", "10:30")
        ]
        s1 = _setup(_profile(bars), "S1")
        assert s1["state"] == "not_matched"
        assert "swing low 不足" in s1["reason"]


class TestS2GapUpHold:
    def test_gap_hold_above_prev_close_and_vwap_is_matched(self):
        profile = _profile(None, **_S2_QUOTE)
        s2 = _setup(profile, "S2")
        assert s2["state"] == "matched"
        assert "跳空 +2.00%（1.00×ATR）" in s2["evidence_lines"][0]
        assert "最低 101.50 > 参考前收 100.00" in s2["evidence_lines"][1]
        assert "现价 103.00 ≥ VWAP 102.50" in s2["evidence_lines"][2]
        assert "S2" in profile["matched_setups"]

    def test_gap_reuses_the_documented_gap_evidence_thresholds(self):
        # 与缺口证据同一常量：0.7×ATR 且 1.4% 两个分支都不达标 → 无跳空支持。
        below = _profile(
            None,
            **{**_S2_QUOTE, "gap_percent": 1.4, "gap_atr_multiple": 0.7},
        )
        assert _setup(below, "S2")["state"] == "not_matched"
        assert "同阈值" in _setup(below, "S2")["reason"]
        # 0.75×ATR 恰好达标（百分比分支仍 1.4 < 1.5）。
        at_atr = _profile(
            None,
            **{**_S2_QUOTE, "gap_percent": 1.4, "gap_atr_multiple": 0.75},
        )
        assert _setup(at_atr, "S2")["state"] == "matched"
        # 常量与 intraday_top 的别名严格同源。
        assert SETUP_GAP_ATR_MULTIPLE_MIN == GAP_ATR_MULTIPLE_SUPPORT_MIN == 0.75
        assert SETUP_GAP_PERCENT_MIN == GAP_PERCENT_SUPPORT_MIN == 1.5

    def test_gap_down_never_matches_s2_or_s3(self):
        profile = _profile(
            None, **{**_S2_QUOTE, "gap_percent": -2.0, "gap_atr_multiple": 1.0}
        )
        assert _setup(profile, "S2")["state"] == "not_matched"
        assert _setup(profile, "S3")["state"] == "not_matched"

    def test_one_sided_hold_is_partial_with_reason(self):
        profile = _profile(None, **{**_S2_QUOTE, "last_price": 102.0})
        s2 = _setup(profile, "S2")
        assert s2["state"] == "partial"
        assert "现价低于 VWAP" in s2["reason"]

    def test_gap_refilled_and_below_vwap_is_not_matched(self):
        profile = _profile(
            None, **{**_S2_QUOTE, "session_low": 99.5, "last_price": 101.0}
        )
        s2 = _setup(profile, "S2")
        assert s2["state"] == "not_matched"
        assert "未托住" in s2["reason"]
        assert "缺口已回补" in s2["reason"]
        assert "现价低于 VWAP" in s2["reason"]

    def test_not_matched_reason_marks_missing_vwap_instead_of_asserting_it(self):
        """缺口已回补 + VWAP 标缺：原因只陈述观测到的失败，标缺侧如实写标缺。"""
        profile = _profile(
            None,
            **{
                **_S2_QUOTE,
                "session_low": 99.5,  # 观测到：缺口已回补。
                "vwap": None,  # 未观测：VWAP 侧标缺。
            },
        )
        s2 = _setup(profile, "S2")
        assert s2["state"] == "not_matched"
        assert "缺口已回补" in s2["reason"]
        assert "现价/VWAP 标缺" in s2["reason"]
        # 绝不把没观测过的「现价低于 VWAP」写成事实。
        assert "现价低于 VWAP" not in s2["reason"]

    def test_missing_gap_inputs_are_unavailable(self):
        profile = _profile(None, last_price=103.0)
        s2 = _setup(profile, "S2")
        assert s2["state"] == "unavailable"
        assert "缺口标缺" in s2["reason"]

    def test_missing_hold_inputs_are_unavailable_not_guessed(self):
        profile = _profile(
            None, gap_percent=2.0, gap_atr_multiple=1.0
        )
        s2 = _setup(profile, "S2")
        assert s2["state"] == "unavailable"
        assert "托举输入标缺" in s2["reason"]


class TestS3GapUpRejection:
    _REJECTED = dict(
        gap_percent=2.0,
        gap_atr_multiple=1.0,
        gap_reference_close=100.0,
        session_open=102.0,
        session_low=99.5,
        last_price=101.0,
        vwap=102.5,
    )

    def test_rejection_with_weak_spy_is_matched(self):
        profile = _profile(None, **self._REJECTED, spy_vwap_position="below")
        s3 = _setup(profile, "S3")
        assert s3["state"] == "matched"
        assert "现价 101.00 < 开盘 102.00" in s3["evidence_lines"][1]
        assert any("大盘走弱" in line for line in s3["evidence_lines"])
        # 同一输入下 S2 如实 not_matched：缺口回补且现价低于 VWAP。
        assert _setup(profile, "S2")["state"] == "not_matched"

    def test_rejection_with_strong_spy_is_partial(self):
        profile = _profile(None, **self._REJECTED, spy_vwap_position="above")
        s3 = _setup(profile, "S3")
        assert s3["state"] == "partial"
        assert "形态似 S3 但大盘未走弱" in s3["reason"]

    def test_rejection_with_unknown_spy_is_partial_with_missing_reason(self):
        profile = _profile(None, **self._REJECTED, spy_vwap_position=None)
        s3 = _setup(profile, "S3")
        assert s3["state"] == "partial"
        assert "大盘状态标缺" in s3["reason"]

    def test_gap_up_still_holding_is_not_matched(self):
        profile = _profile(None, **_S2_QUOTE, spy_vwap_position="below")
        s3 = _setup(profile, "S3")
        assert s3["state"] == "not_matched"
        assert "未见回落" in s3["reason"]
        # 两侧都被观测到未跌破：原因逐侧陈述观测值。
        assert "≥ 开盘" in s3["reason"]
        assert "≥ VWAP" in s3["reason"]

    def test_not_matched_reason_marks_missing_vwap_instead_of_asserting_it(self):
        """现价未跌破开盘 + VWAP 标缺：原因只陈述观测侧，标缺侧如实写标缺。"""
        profile = _profile(
            None,
            gap_percent=2.0,
            gap_atr_multiple=1.0,
            session_open=102.0,
            last_price=103.0,  # 观测到：未跌破开盘。
            vwap=None,  # 未观测：VWAP 侧标缺。
        )
        s3 = _setup(profile, "S3")
        assert s3["state"] == "not_matched"
        assert "现价 103.00 ≥ 开盘 102.00" in s3["reason"]
        assert "VWAP 标缺" in s3["reason"]
        # 绝不把没观测过的「未跌破 VWAP」写成事实。
        assert "≥ VWAP" not in s3["reason"]

    def test_missing_last_price_is_unavailable(self):
        profile = _profile(
            None, gap_percent=2.0, gap_atr_multiple=1.0, session_open=102.0
        )
        s3 = _setup(profile, "S3")
        assert s3["state"] == "unavailable"
        assert "缺现价" in s3["reason"]


class TestProfileContract:
    def test_multi_match_exposes_all_states(self):
        profile = _profile(_s1_bars(), **_S2_QUOTE, spy_vwap_position="above")
        assert profile["matched_setups"] == ["S1", "S2"]
        assert _setup(profile, "S3")["state"] == "not_matched"
        assert profile["state"] == "ready"
        assert profile["style_match_version"] == STYLE_MATCH_VERSION
        assert profile["basis"] == SETUP_MATCH_BASIS
        assert len(profile["setups"]) == 3
        assert any("形态相似 ≠ 可交易" in text for text in profile["limitations"])
        # 诚实边界：v1 未检查用户的进场确认帧。
        assert any("8/13 EMA" in text for text in profile["limitations"])

    def test_closed_session_evaluates_latest_prior_session_with_as_of_label(self):
        # 周日休市视角：bars 属于周五 2026-07-31，如实标注该时段。
        profile = compute_setup_match_profile(
            _s1_bars("2026-07-31"),
            market_date_et="2026-08-02",
            quote_session_scope="latest_prior_session",
            last_price=None,
        )
        assert profile["quote_session_scope"] == "latest_prior_session"
        assert profile["session_date_et"] == "2026-07-31"
        assert _setup(profile, "S1")["state"] == "matched"

    def test_current_session_scope_ignores_prior_session_bars(self):
        # 盘前（今日尚无 K 线）：不得拿昨日形态冒充今日。
        profile = compute_setup_match_profile(
            _s1_bars("2026-07-27"),
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
        )
        assert profile["session_date_et"] == "2026-07-28"
        assert profile["bar_count_5m"] == 0
        assert _setup(profile, "S1")["state"] == "unavailable"

    def test_all_unavailable_profile_is_unavailable_with_reason(self):
        profile = _profile(None)
        assert profile["state"] == "unavailable"
        assert profile["unavailable_reason"] == (
            "no_usable_bars_and_missing_quote_inputs"
        )
        assert all(row["state"] == "unavailable" for row in profile["setups"])

    def test_playbook_refs_pass_through_read_only(self):
        refs = {
            "S1": {
                "setup_key": "S1",
                "candidate_key": "a" * 64,
                "status": "candidate",
                "title": "S1 · 15分钟低点抬高突破（主力打法）",
            }
        }
        profile = _profile(_s1_bars(), playbook_refs=refs)
        s1 = _setup(profile, "S1")
        assert s1["playbook"]["status"] == "candidate"
        assert s1["playbook"]["candidate_key"] == "a" * 64
        assert _setup(profile, "S2")["playbook"] is None

    def test_aggregation_uses_0930_grid_and_counts_5m_bars(self):
        bars = filter_regular_session_bars(
            [
                _bar("2026-07-28", "09:30", 100.0, 100.5, 99.5, 100.2),
                _bar("2026-07-28", "09:35", 100.2, 100.9, 100.0, 100.8),
                _bar("2026-07-28", "09:50", 100.8, 101.0, 100.4, 100.5),
            ]
        )
        rows = aggregate_15m_bars(bars)
        assert [row["start_et"] for row in rows] == ["09:30", "09:45"]
        assert rows[0]["bar_count_5m"] == 2
        assert rows[0]["high"] == pytest.approx(100.9)
        assert rows[0]["close"] == pytest.approx(100.8)
        assert rows[1]["bar_count_5m"] == 1

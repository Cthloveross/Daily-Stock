# -*- coding: utf-8 -*-
"""Deterministic tests for rolling 15-minute momentum-burst detection.

包含两类：

1. **校准回归**（永久 fixture）：2026-07-31（周五）常规时段真实 5m K 线
   （MU/AMZN/NVDA/GOOGL，来自与 /stocks/{code}/history?period=5m 相同的
   服务端加载器），当日可交易机会由用户标注：MU ~09:45 跳水、AMZN 09:30
   开盘波、NVDA 09:40 与 15:15 两波；GOOGL 是 v1 失败样本（全时段聚合把它
   排第一，但它没有可比的 15 分钟爆发）。阈值改动若打破这些断言，必须先
   重新校准并升 signal_version。
2. **单元**：归一化数学手工核对、开盘初段中位数回退、独立波段合并、
   休市时段归属、无 K 线 fail-closed。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.opportunities.intraday_bursts import (
    BURST_SUPPORT_MIN,
    LEG_MAX_COUNT,
    LEG_MEDIUM_MIN_SCORE,
    LEG_MIN_GAP_MINUTES,
    LEG_MIN_SCORE,
    MEDIAN_BASIS_CURRENT,
    MEDIAN_BASIS_PRIOR,
    SPEED_BASIS,
    compute_session_burst_profile,
    compute_speed_state,
    filter_regular_session_bars,
    select_distinct_legs,
    split_burst_session_bars,
    unavailable_burst_profile,
)

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "intraday_5m_2026-07-31_regular.json"
)


@pytest.fixture(scope="module")
def labeled_bars() -> dict:
    with _FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _profile(labeled_bars: dict, symbol: str) -> dict:
    return compute_session_burst_profile(
        labeled_bars["symbols"][symbol]["bars"],
        market_date_et="2026-07-31",
        quote_session_scope="current_session",
        source=labeled_bars["symbols"][symbol]["source"],
        fetched_at=labeled_bars["captured_at"],
    )


def _minutes(label: str) -> int:
    return int(label[:2]) * 60 + int(label[3:])


class TestCalibrationRegression:
    """用户标注的 2026-07-31 行情是阈值的永久校准基准。"""

    def test_mu_0945_plunge_detected_down(self, labeled_bars):
        profile = _profile(labeled_bars, "MU")
        assert profile["state"] == "ready"
        legs = profile["legs"]
        plunge = [
            leg
            for leg in legs
            if _minutes("09:40") <= _minutes(leg["start_et"]) <= _minutes("09:50")
        ]
        assert plunge, f"MU 09:40-09:50 plunge missing from legs: {legs}"
        leg = plunge[0]
        assert leg["direction"] == "down"
        assert leg["score"] >= LEG_MIN_SCORE
        # 用户标注 ~−5.2% / 15min @ ~4.6× 量：范围断言防口径漂移。
        assert leg["thrust_percent"] <= -4.5
        assert leg["vol_norm"] >= 4.0

    def test_amzn_opening_wave_detected(self, labeled_bars):
        profile = _profile(labeled_bars, "AMZN")
        legs = profile["legs"]
        opening = [
            leg for leg in legs if _minutes(leg["start_et"]) <= _minutes("09:35")
        ]
        assert opening, f"AMZN 09:30 opening wave missing from legs: {legs}"
        assert opening[0]["direction"] == "up"
        assert opening[0]["score"] >= LEG_MIN_SCORE

    def test_nvda_two_waves_including_late_day(self, labeled_bars):
        profile = _profile(labeled_bars, "NVDA")
        legs = profile["legs"]
        assert len(legs) >= 2, f"NVDA should yield >=2 distinct legs: {legs}"
        morning = [
            leg
            for leg in legs
            if _minutes("09:35") <= _minutes(leg["start_et"]) <= _minutes("09:45")
        ]
        late = [leg for leg in legs if _minutes(leg["start_et"]) >= _minutes("15:00")]
        assert morning and morning[0]["direction"] == "down"
        assert late, f"NVDA post-15:00 wave missing: {legs}"
        assert late[0]["direction"] == "up"

    def test_googl_best_leg_stays_below_mu_plunge(self, labeled_bars):
        """v1 失败样本保持修复：GOOGL 的最佳波段必须弱于 MU 的跳水。"""

        mu_best = max(leg["score"] for leg in _profile(labeled_bars, "MU")["legs"])
        googl = _profile(labeled_bars, "GOOGL")
        googl_best = max(
            (leg["score"] for leg in googl["legs"]), default=0.0
        )
        assert googl_best < mu_best

    def test_all_labeled_legs_clear_support_threshold_too(self, labeled_bars):
        # supports 阈值低于强波段阈值：任何强波段都必然点亮「波段爆发」；
        # v2 分级后中波段（≥2.5）合法存在于 supports 阈值之下，但每个
        # 波段都必须 ≥ 中波段下限并携带正确 grade。
        assert BURST_SUPPORT_MIN < LEG_MIN_SCORE
        assert LEG_MEDIUM_MIN_SCORE < BURST_SUPPORT_MIN
        for symbol in ("MU", "AMZN", "NVDA"):
            for leg in _profile(labeled_bars, symbol)["legs"]:
                assert leg["score"] >= LEG_MEDIUM_MIN_SCORE
                if leg["grade"] == "strong":
                    assert leg["score"] >= LEG_MIN_SCORE
                else:
                    assert leg["grade"] == "medium"
                    assert leg["score"] < LEG_MIN_SCORE


def _bar(ts: str, *, open_, high, low, close, volume):
    return {
        "date": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _flat_bar(hhmm: str, *, date="2026-07-28", volume=100.0, range_=1.0):
    return _bar(
        f"{date}T{hhmm}:00-04:00",
        open_=100.0,
        high=100.0 + range_ / 2,
        low=100.0 - range_ / 2,
        close=100.0,
        volume=volume,
    )


class TestNormalisationMath:
    def test_hand_computed_window(self):
        # 6 根平静 K（range 1.0 / vol 100）+ 3 根爆发 K（range 3.0 / vol 300）
        # → 中位数 range=1.0、vol=100；末窗推力 = 103 − 100 = 3。
        bars = [
            _flat_bar(hhmm)
            for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")
        ] + [
            _bar("2026-07-28T10:00:00-04:00", open_=100.0, high=101.5, low=99.8, close=101.0, volume=300.0),
            _bar("2026-07-28T10:05:00-04:00", open_=101.0, high=103.0, low=100.0, close=102.0, volume=300.0),
            _bar("2026-07-28T10:10:00-04:00", open_=102.0, high=104.0, low=101.0, close=103.0, volume=300.0),
        ]
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        assert profile["state"] == "ready"
        assert profile["median_bar_range"] == pytest.approx(1.0)
        assert profile["median_bar_volume"] == pytest.approx(100.0)
        assert profile["median_basis"] == MEDIAN_BASIS_CURRENT
        current = profile["current"]
        assert current["start_et"] == "10:00"
        assert current["end_et"] == "10:15"
        assert current["thrust_norm"] == pytest.approx(3.0)
        assert current["vol_norm"] == pytest.approx(3.0)
        assert current["score"] == pytest.approx(9.0)
        assert current["direction"] == "up"
        assert current["thrust_percent"] == pytest.approx(3.0)
        # 9.0 ≥ LEG_MIN_SCORE → 唯一一波。
        assert [leg["start_et"] for leg in profile["legs"]] == ["10:00"]

    def test_downward_thrust_direction(self):
        bars = [_flat_bar(hhmm) for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")] + [
            _bar("2026-07-28T10:00:00-04:00", open_=100.0, high=100.2, low=98.0, close=98.5, volume=150.0),
            _bar("2026-07-28T10:05:00-04:00", open_=98.5, high=98.6, low=97.0, close=97.2, volume=150.0),
            _bar("2026-07-28T10:10:00-04:00", open_=97.2, high=97.4, low=96.0, close=96.5, volume=150.0),
        ]
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        assert profile["current"]["direction"] == "down"
        assert profile["current"]["thrust_percent"] < 0
        # thrust_norm 用绝对值，score 永远非负。
        assert profile["current"]["score"] >= 0

    def test_window_missing_volume_fails_closed_not_zero(self):
        bars = [_flat_bar(hhmm) for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")]
        bars.append(
            _bar("2026-07-28T10:00:00-04:00", open_=100.0, high=103.0, low=100.0, close=103.0, volume=None)
        )
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        assert profile["state"] == "ready"
        # 末窗含缺量 K 线：score 显式 None，而不是把量比当 0。
        assert profile["current"]["score"] is None
        assert profile["current"]["vol_norm"] is None


class TestEarlySessionMedianFallback:
    def _prior(self):
        return [
            _flat_bar(hhmm, date="2026-07-27", volume=200.0, range_=2.0)
            for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55", "10:00")
        ]

    def test_under_six_bars_uses_prior_session_medians(self):
        bars = self._prior() + [
            _flat_bar(hhmm, date="2026-07-28", volume=100.0, range_=1.0)
            for hhmm in ("09:30", "09:35", "09:40", "09:45")
        ]
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        assert profile["median_basis"] == MEDIAN_BASIS_PRIOR
        assert profile["median_bar_range"] == pytest.approx(2.0)
        assert profile["median_bar_volume"] == pytest.approx(200.0)
        assert profile["state"] == "ready"
        assert profile["bar_count"] == 4

    def test_six_bars_switch_to_current_session_medians(self):
        bars = self._prior() + [
            _flat_bar(hhmm, date="2026-07-28", volume=100.0, range_=1.0)
            for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")
        ]
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        assert profile["median_basis"] == MEDIAN_BASIS_CURRENT
        assert profile["median_bar_range"] == pytest.approx(1.0)

    def test_no_prior_session_available_keeps_current_basis(self):
        bars = [
            _flat_bar(hhmm, date="2026-07-28")
            for hhmm in ("09:30", "09:35", "09:40")
        ]
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        assert profile["median_basis"] == MEDIAN_BASIS_CURRENT
        assert profile["state"] == "ready"


class TestDistinctLegMerging:
    def _window(self, start_minutes: int, score: float, direction: str = "up"):
        return {
            "start_et": f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
            "end_et": f"{(start_minutes + 15) // 60:02d}:{(start_minutes + 15) % 60:02d}",
            "thrust_percent": 1.0,
            "thrust_norm": 1.0,
            "vol_norm": score,
            "score": score,
            "direction": direction,
            "_start_minutes": start_minutes,
        }

    def test_windows_within_gap_merge_to_single_best_leg(self):
        rows = [
            self._window(_m, score)
            for _m, score in ((585, 30.0), (590, 34.0), (595, 28.0))  # 09:45-09:55
        ]
        legs = select_distinct_legs(rows)
        assert [leg["start_et"] for leg in legs] == ["09:50"]
        assert legs[0]["score"] == 34.0

    def test_windows_at_gap_boundary_stay_distinct(self):
        rows = [
            self._window(580, 9.3, "down"),  # 09:40
            self._window(580 + LEG_MIN_GAP_MINUTES, 8.6, "up"),
        ]
        legs = select_distinct_legs(rows)
        assert len(legs) == 2
        assert [leg["direction"] for leg in legs] == ["down", "up"]

    def test_below_threshold_and_cap(self):
        # v2 分级：强波段阈值之下、中波段阈值之上 → 记为 medium 波段；
        # 中波段阈值之下 → 不记录。
        rows = [self._window(570, LEG_MIN_SCORE - 0.01)]
        legs = select_distinct_legs(rows)
        assert [leg["grade"] for leg in legs] == ["medium"]
        assert select_distinct_legs(
            [self._window(570, LEG_MEDIUM_MIN_SCORE - 0.01)]
        ) == []
        many = [
            self._window(570 + index * LEG_MIN_GAP_MINUTES, 10.0 + index)
            for index in range(LEG_MAX_COUNT + 3)
        ]
        capped = select_distinct_legs(many)
        assert len(capped) == LEG_MAX_COUNT
        assert all(leg["grade"] == "strong" for leg in capped)

    def test_legs_are_chronological(self):
        rows = [self._window(930, 12.0), self._window(575, 20.0)]
        legs = select_distinct_legs(rows)
        assert [leg["start_et"] for leg in legs] == ["09:35", "15:30"]


class TestSessionAttachment:
    def test_closed_scope_attaches_latest_prior_session(self, labeled_bars):
        # 周六休市：market date 2026-08-01，最近一个交易时段 = 07-31。
        profile = compute_session_burst_profile(
            labeled_bars["symbols"]["NVDA"]["bars"],
            market_date_et="2026-08-01",
            quote_session_scope="latest_prior_session",
        )
        assert profile["state"] == "ready"
        assert profile["session_date_et"] == "2026-07-31"
        assert len(profile["legs"]) >= 2

    def test_current_scope_premarket_has_no_bars_yet(self, labeled_bars):
        profile = compute_session_burst_profile(
            labeled_bars["symbols"]["NVDA"]["bars"],
            market_date_et="2026-08-03",
            quote_session_scope="current_session",
        )
        assert profile["state"] == "insufficient_bars"
        assert profile["unavailable_reason"] == "no_session_bars_yet"
        assert profile["legs"] == []
        assert profile["current"] is None

    def test_split_helper_pairs_target_with_prior(self, labeled_bars):
        target, current, prior = split_burst_session_bars(
            labeled_bars["symbols"]["MU"]["bars"],
            market_date_et="2026-07-31",
            quote_session_scope="current_session",
        )
        assert target == "2026-07-31"
        assert len(current) == 78
        assert len(prior) == 78
        assert prior[0]["session_date_et"] == "2026-07-30"


class TestFailClosed:
    def test_empty_bars_are_unavailable(self):
        profile = compute_session_burst_profile(
            [], market_date_et="2026-07-28", quote_session_scope="latest_prior_session"
        )
        assert profile["state"] == "unavailable"
        assert profile["unavailable_reason"] == "no_regular_session_bars"
        assert profile["current"] is None
        assert profile["legs"] == []

    def test_unavailable_profile_shape(self):
        profile = unavailable_burst_profile("history_5m_unavailable:RuntimeError")
        assert profile["state"] == "unavailable"
        assert profile["unavailable_reason"].startswith("history_5m_unavailable")
        assert profile["legs"] == [] and profile["current"] is None

    def test_naive_and_out_of_session_bars_are_dropped(self):
        rows = filter_regular_session_bars(
            [
                _bar("2026-07-28T09:30:00", open_=1, high=2, low=0.5, close=1.5, volume=10),  # naive
                _bar("2026-07-28T09:25:00-04:00", open_=1, high=2, low=0.5, close=1.5, volume=10),  # premarket
                _bar("2026-07-28T16:00:00-04:00", open_=1, high=2, low=0.5, close=1.5, volume=10),  # afterhours
                _bar("2026-07-28T09:30:00-04:00", open_=1, high=2, low=0.5, close=1.5, volume=10),
            ]
        )
        assert len(rows) == 1
        assert rows[0]["session_date_et"] == "2026-07-28"

    def test_two_bars_only_is_insufficient(self):
        bars = [_flat_bar("09:30"), _flat_bar("09:35")]
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        assert profile["state"] == "insufficient_bars"
        assert profile["current"] is None


class TestSpeedState:
    """v3 速度分级：相邻两个滚动窗口爆发分之差；缺输入显式 unknown。"""

    @staticmethod
    def _window(score):
        return {"start_et": "10:00", "end_et": "10:15", "score": score}

    def test_accelerating_when_latest_window_score_rises(self):
        speed = compute_speed_state([self._window(4.0), self._window(9.0)])
        assert speed["state"] == "accelerating"
        assert speed["current_score"] == 9.0
        assert speed["previous_score"] == 4.0
        assert speed["delta"] == 5.0
        assert speed["basis"] == SPEED_BASIS
        assert speed["unavailable_reason"] is None

    def test_decelerating_when_latest_window_score_falls(self):
        speed = compute_speed_state(
            [self._window(2.0), self._window(9.0), self._window(3.5)]
        )
        assert speed["state"] == "decelerating"
        assert speed["delta"] == -5.5

    def test_flat_only_on_exact_equality(self):
        speed = compute_speed_state([self._window(4.0), self._window(4.0)])
        assert speed["state"] == "flat"
        assert speed["delta"] == 0.0

    def test_single_window_is_unknown_not_flat(self):
        speed = compute_speed_state([self._window(9.0)])
        assert speed["state"] == "unknown"
        assert speed["unavailable_reason"] == "fewer_than_2_windows"

    def test_missing_score_on_either_side_is_unknown(self):
        missing_current = compute_speed_state(
            [self._window(4.0), self._window(None)]
        )
        missing_previous = compute_speed_state(
            [self._window(None), self._window(4.0)]
        )
        assert missing_current["state"] == "unknown"
        assert missing_previous["state"] == "unknown"
        assert missing_current["unavailable_reason"] == "missing_window_score"

    def test_ready_profile_carries_speed_and_unavailable_profile_is_unknown(self):
        # 四根平静 K 线 + 一根放量推力 K 线：末窗必须相对前窗加速。
        bars = []
        for index, (open_, close, volume) in enumerate(
            [
                (100.0, 100.2, 1_000.0),
                (100.2, 100.0, 1_000.0),
                (100.0, 100.3, 1_000.0),
                (100.3, 100.1, 1_000.0),
                (100.1, 103.0, 4_000.0),
            ]
        ):
            minutes = 9 * 60 + 30 + index * 5
            bars.append(
                {
                    "date": f"2026-07-28T{minutes // 60:02d}:{minutes % 60:02d}:00-04:00",
                    "open": open_,
                    "high": max(open_, close) + 0.1,
                    "low": min(open_, close) - 0.1,
                    "close": close,
                    "volume": volume,
                }
            )
        profile = compute_session_burst_profile(
            bars,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
        )
        assert profile["state"] == "ready"
        assert profile["speed"]["state"] == "accelerating"
        assert profile["speed"]["current_score"] == profile["current"]["score"]

        unavailable = unavailable_burst_profile("no_regular_session_bars")
        assert unavailable["speed"]["state"] == "unknown"
        assert unavailable["speed"]["unavailable_reason"] == "fewer_than_2_windows"

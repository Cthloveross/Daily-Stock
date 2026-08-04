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
    ATR_SCALE_DAILY_ATR14,
    ATR_SCALE_DAILY_PRIOR_SESSIONS,
    ATR_SCALE_INTRADAY_NOT_COMPARABLE,
    BURST_SUPPORT_MIN,
    DISPLACEMENT_ATR_BASIS_DAILY,
    DISPLACEMENT_ATR_BASIS_PRIOR_SESSIONS,
    DISPLACEMENT_ATR_BASIS_PROXY,
    DISPLACEMENT_PROXY_MULTIPLIER,
    DISPLACEMENT_SURVIVAL_LINE_ATR,
    DISPLACEMENT_WINDOW_BARS,
    DISPLACEMENT_WINDOW_MINUTES,
    FIZZLE_BASIS,
    FIZZLE_EFFICIENCY_MIN,
    FIZZLE_REFERENCE,
    FIZZLE_VOL_NORM_MAX,
    LEG_MAX_COUNT,
    LEG_MEDIUM_MIN_SCORE,
    LEG_MIN_GAP_MINUTES,
    LEG_MIN_SCORE,
    MEDIAN_BASIS_CURRENT,
    MEDIAN_BASIS_PRIOR,
    SPEED_BASIS,
    compute_fizzle_flag,
    compute_recent_displacement,
    compute_session_burst_profile,
    compute_speed_state,
    compute_window_efficiency,
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


def _window(*, open_, close, high, low):
    """3 根 K 线的最小窗口：首根开盘 / 末根收盘 / 窗口最高 / 窗口最低可控。"""

    return [
        _bar(
            "2026-07-28T10:00:00-04:00",
            open_=open_,
            high=high,
            low=low,
            close=(open_ + close) / 2,
            volume=100.0,
        ),
        _bar(
            "2026-07-28T10:05:00-04:00",
            open_=(open_ + close) / 2,
            high=high,
            low=low,
            close=(open_ + close) / 2,
            volume=100.0,
        ),
        _bar(
            "2026-07-28T10:10:00-04:00",
            open_=(open_ + close) / 2,
            high=high,
            low=low,
            close=close,
            volume=100.0,
        ),
    ]


class TestFizzleFlagTruthTable:
    """v7 哑火形态真值表：三个条件各自的否定、开盘分层豁免、未定义即标缺。

    研究背景（见模块 docstring）：起速那一刻**方向不可预测**（P(方向)=50.6%、
    19 个因子方向 AUC 0.48–0.52），因此这里断言的只是**形态判定的确定性**，
    不是任何胜率或方向主张。
    """

    def test_efficiency_is_thrust_over_window_span(self):
        # |102 − 100| ÷ (102.5 − 99.5) = 2 / 3
        assert compute_window_efficiency(
            _window(open_=100.0, close=102.0, high=102.5, low=99.5)
        ) == pytest.approx(2 / 3, rel=1e-4)

    def test_flagged_when_intraday_clean_thrust_and_quiet_volume(self):
        flag = compute_fizzle_flag(
            # |101 − 100| ÷ (101.05 − 100.0) ≈ 0.952 ≥ 0.9
            _window(open_=100.0, close=101.0, high=101.05, low=100.0),
            median_basis=MEDIAN_BASIS_CURRENT,
            vol_norm=1.2,
        )
        assert flag["state"] == "flagged"
        assert flag["stratum"] == "intraday"
        assert flag["efficiency"] >= FIZZLE_EFFICIENCY_MIN
        assert flag["vol_norm"] < FIZZLE_VOL_NORM_MAX
        assert flag["reason"] == "intraday_clean_thrust_with_unremarkable_volume"
        assert flag["basis"] == FIZZLE_BASIS
        # 冻结的研究参考数字随读数一起下发，供 UI 原样展示。
        assert flag["reference"] == FIZZLE_REFERENCE
        assert flag["reference"]["n_in"] == 291 and flag["reference"]["n_out"] == 177

    def test_open_stratum_is_never_flagged_even_if_shape_matches(self):
        """开盘段（中位数基准回退上一时段）永不命中——研究口径就排除了它。"""

        flag = compute_fizzle_flag(
            _window(open_=100.0, close=101.0, high=101.05, low=100.0),
            median_basis=MEDIAN_BASIS_PRIOR,
            vol_norm=1.2,
        )
        assert flag["state"] == "not_flagged"
        assert flag["stratum"] == "open"
        assert flag["reason"] == "stratum_open_not_intraday"

    def test_not_flagged_when_efficiency_below_line(self):
        flag = compute_fizzle_flag(
            # |100.5 − 100| ÷ (102 − 99) ≈ 0.167
            _window(open_=100.0, close=100.5, high=102.0, low=99.0),
            median_basis=MEDIAN_BASIS_CURRENT,
            vol_norm=1.0,
        )
        assert flag["state"] == "not_flagged"
        assert flag["reason"] == f"efficiency_below_{FIZZLE_EFFICIENCY_MIN}"

    def test_not_flagged_when_volume_is_not_unremarkable(self):
        flag = compute_fizzle_flag(
            _window(open_=100.0, close=101.0, high=101.05, low=100.0),
            median_basis=MEDIAN_BASIS_CURRENT,
            vol_norm=FIZZLE_VOL_NORM_MAX,  # 边界：2.0 本身不算「量能平平」
        )
        assert flag["state"] == "not_flagged"
        assert flag["reason"] == f"vol_norm_at_or_above_{FIZZLE_VOL_NORM_MAX}"

    def test_multiple_unmet_conditions_are_all_reported(self):
        flag = compute_fizzle_flag(
            _window(open_=100.0, close=100.5, high=102.0, low=99.0),
            median_basis=MEDIAN_BASIS_PRIOR,
            vol_norm=5.0,
        )
        assert flag["state"] == "not_flagged"
        assert flag["reason"] == (
            "stratum_open_not_intraday"
            f"+efficiency_below_{FIZZLE_EFFICIENCY_MIN}"
            f"+vol_norm_at_or_above_{FIZZLE_VOL_NORM_MAX}"
        )

    def test_zero_span_window_is_unavailable_never_flagged(self):
        """窗口最高 == 最低 → 效率未定义：显式标缺，绝不当成「完美效率」。"""

        flag = compute_fizzle_flag(
            _window(open_=100.0, close=100.0, high=100.0, low=100.0),
            median_basis=MEDIAN_BASIS_CURRENT,
            vol_norm=1.0,
        )
        assert flag["state"] == "unavailable"
        assert flag["efficiency"] is None
        assert flag["reason"] == "window_high_equals_low_efficiency_undefined"

    def test_unusable_ohlc_is_a_different_reason_than_zero_span(self):
        """缺 OHLC 与「高低相等」是两回事：原因如实区分，都不判为命中。"""

        window = _window(open_=100.0, close=101.0, high=101.05, low=100.0)
        window[1]["high"] = None
        flag = compute_fizzle_flag(
            window, median_basis=MEDIAN_BASIS_CURRENT, vol_norm=1.0
        )
        assert flag["state"] == "unavailable"
        assert flag["reason"] == "window_bar_ohlc_unavailable"

    def test_missing_inputs_are_unavailable_with_reasons(self):
        no_basis = compute_fizzle_flag(
            _window(open_=100.0, close=101.0, high=101.05, low=100.0),
            median_basis=None,
            vol_norm=1.0,
        )
        assert no_basis["state"] == "unavailable"
        assert no_basis["reason"] == "median_basis_unavailable"

        no_vol = compute_fizzle_flag(
            _window(open_=100.0, close=101.0, high=101.05, low=100.0),
            median_basis=MEDIAN_BASIS_CURRENT,
            vol_norm=None,
        )
        assert no_vol["state"] == "unavailable"
        assert no_vol["reason"] == "vol_norm_unavailable"

        short_window = compute_fizzle_flag(
            _window(open_=100.0, close=101.0, high=101.05, low=100.0)[:2],
            median_basis=MEDIAN_BASIS_CURRENT,
            vol_norm=1.0,
        )
        assert short_window["state"] == "unavailable"
        assert short_window["reason"] == "fewer_than_3_window_bars"

    def test_profile_attaches_flag_for_the_current_window_only(self):
        """profile 上的哑火读数描述**当前窗口**，与更早的波段无关。"""

        # 前 6 根平静（中位数取当日）→ intraday 分层；末 3 根几乎无回撤、量比平平。
        bars = [
            _flat_bar(hhmm)
            for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")
        ] + [
            _bar("2026-07-28T10:00:00-04:00", open_=100.0, high=100.4, low=100.0, close=100.4, volume=110.0),
            _bar("2026-07-28T10:05:00-04:00", open_=100.4, high=100.8, low=100.4, close=100.8, volume=110.0),
            _bar("2026-07-28T10:10:00-04:00", open_=100.8, high=101.2, low=100.8, close=101.2, volume=110.0),
        ]
        profile = compute_session_burst_profile(
            bars, market_date_et="2026-07-28", quote_session_scope="current_session"
        )
        flag = profile["fizzle_flag"]
        assert profile["median_basis"] == MEDIAN_BASIS_CURRENT
        assert flag["state"] == "flagged"
        # 效率＝|101.2 − 100.0| ÷ (101.2 − 100.0) = 1.0（完全单向、零回撤）
        assert flag["efficiency"] == pytest.approx(1.0)
        assert flag["vol_norm"] == profile["current"]["vol_norm"]

    def test_unavailable_profile_carries_unavailable_flag(self):
        profile = unavailable_burst_profile("history_5m_unavailable:Timeout")
        assert profile["fizzle_flag"]["state"] == "unavailable"
        assert profile["fizzle_flag"]["reason"] == "burst_profile_unavailable"

    def test_early_session_profile_flag_never_claims_a_hit(self):
        """开盘不足 6 根 K 线（回退上一时段基准）→ 分层 open → 永不命中。"""

        prior = [
            _flat_bar(hhmm, date="2026-07-27")
            for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")
        ]
        today = [
            _bar("2026-07-28T09:30:00-04:00", open_=100.0, high=100.4, low=100.0, close=100.4, volume=100.0),
            _bar("2026-07-28T09:35:00-04:00", open_=100.4, high=100.8, low=100.4, close=100.8, volume=100.0),
            _bar("2026-07-28T09:40:00-04:00", open_=100.8, high=101.2, low=100.8, close=101.2, volume=100.0),
        ]
        profile = compute_session_burst_profile(
            prior + today,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
        )
        assert profile["median_basis"] == MEDIAN_BASIS_PRIOR
        assert profile["fizzle_flag"]["state"] == "not_flagged"
        assert profile["fizzle_flag"]["stratum"] == "open"


class TestRecentDisplacement:
    """v6 近 30 分钟位移：窗口数学、ATR 标尺选择与 fail-closed 三态。

    校准背景（见模块 docstring）：用户自己 766 笔回合的取证分析显示进场几何
    没有预测力，而进场后 30 分钟位移把结果分得很开——0.5 ATR 是他自己样本里
    的经验线。这里断言的是**口径的确定性**，不是任何胜率主张。
    """

    @staticmethod
    def _ramp_bars(closes, *, date="2026-07-28", start_hhmm="09:30", spread=0.1):
        """按给定收盘序列生成连续 5m K 线（open=上一根 close，高低各留 spread）。"""

        bars = []
        base_minutes = int(start_hhmm[:2]) * 60 + int(start_hhmm[3:])
        previous_close = closes[0]
        for index, close in enumerate(closes):
            open_ = previous_close
            minutes = base_minutes + index * 5
            bars.append(
                _bar(
                    f"{date}T{minutes // 60:02d}:{minutes % 60:02d}:00-04:00",
                    open_=open_,
                    high=max(open_, close) + spread,
                    low=min(open_, close) - spread,
                    close=close,
                    volume=1_000.0,
                )
            )
            previous_close = close
        return bars

    def test_exact_six_bar_window_math_with_atr14(self):
        # 6 根 K 线，窗口首根开盘 = 100.0（＝30 分钟前的价格），末收 101.0，
        # 窗口最高 101.2（100.8+0.1... 见下），最低 99.9。ATR14=2.0。
        bars = self._ramp_bars([100.0, 100.4, 100.2, 100.6, 100.8, 101.0])
        displacement = compute_recent_displacement(
            bars,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        assert displacement["state"] == "ready"
        assert displacement["bar_count"] == DISPLACEMENT_WINDOW_BARS
        assert displacement["window_minutes"] == DISPLACEMENT_WINDOW_MINUTES == 30
        assert displacement["atr_basis"] == DISPLACEMENT_ATR_BASIS_DAILY
        assert displacement["survival_line_atr"] == DISPLACEMENT_SURVIVAL_LINE_ATR
        window_high = max(bar["high"] for bar in bars)
        window_low = min(bar["low"] for bar in bars)
        assert displacement["net_move_atr"] == pytest.approx((101.0 - 100.0) / 2.0)
        assert displacement["high_excursion_atr"] == pytest.approx(
            (window_high - 100.0) / 2.0
        )
        assert displacement["low_excursion_atr"] == pytest.approx(
            (window_low - 100.0) / 2.0
        )
        assert displacement["abs_range_atr"] == pytest.approx(
            (window_high - window_low) / 2.0
        )

    def test_window_is_only_the_last_six_bars(self):
        # 前段大涨、后 30 分钟横盘：位移必须只反映最后 6 根，不吃掉早盘涨幅。
        bars = self._ramp_bars(
            [100.0, 104.0, 108.0, 110.0, 110.0, 110.0, 110.0, 110.0, 110.0]
        )
        displacement = compute_recent_displacement(
            bars,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        assert displacement["bar_count"] == 9
        # 窗口首根（第 4 根）开盘 = 108.0，末收 110.0 → 净位移 1.0 ATR。
        assert displacement["net_move_atr"] == pytest.approx((110.0 - 108.0) / 2.0)
        assert displacement["net_move_atr"] < (110.0 - 100.0) / 2.0

    def test_negative_and_flat_moves_keep_their_sign(self):
        down = compute_recent_displacement(
            self._ramp_bars([100.0, 99.0, 98.5, 98.0, 97.5, 97.0]),
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        assert down["net_move_atr"] == pytest.approx((97.0 - 100.0) / 2.0)
        assert down["net_move_atr"] < 0
        assert down["low_excursion_atr"] < 0
        assert down["abs_range_atr"] > 0

        flat = compute_recent_displacement(
            [_flat_bar(hhmm) for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")],
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        # 完全横盘：净位移恰好 0，且远低于 0.5 ATR 经验线——如实报 0，不报「动了」。
        assert flat["net_move_atr"] == pytest.approx(0.0)
        assert abs(flat["net_move_atr"]) < DISPLACEMENT_SURVIVAL_LINE_ATR

    def test_missing_atr14_falls_back_to_labeled_intraday_proxy(self):
        bars = self._ramp_bars([100.0, 100.4, 100.2, 100.6, 100.8, 101.0])
        displacement = compute_recent_displacement(
            bars,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        assert displacement["atr_basis"] == DISPLACEMENT_ATR_BASIS_PROXY
        ranges = [bar["high"] - bar["low"] for bar in bars]
        proxy = sum(ranges) / len(ranges) * DISPLACEMENT_PROXY_MULTIPLIER
        assert displacement["net_move_atr"] == pytest.approx((101.0 - 100.0) / proxy)

    def test_non_positive_atr14_falls_back_instead_of_dividing_by_zero(self):
        bars = self._ramp_bars([100.0, 100.4, 100.2, 100.6, 100.8, 101.0])
        for bad_atr in (0.0, -1.0):
            displacement = compute_recent_displacement(
                bars,
                market_date_et="2026-07-28",
                quote_session_scope="current_session",
                atr14=bad_atr,
            )
            assert displacement["state"] == "ready"
            assert displacement["atr_basis"] == DISPLACEMENT_ATR_BASIS_PROXY

    def test_fewer_than_six_bars_is_insufficient_with_the_count(self):
        bars = [_flat_bar(hhmm) for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50")]
        displacement = compute_recent_displacement(
            bars,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        assert displacement["state"] == "insufficient_bars"
        assert displacement["bar_count"] == 5
        assert displacement["unavailable_reason"] == (
            f"fewer_than_{DISPLACEMENT_WINDOW_BARS}_session_bars"
        )
        # 缺读数时四个数值全部 None——绝不 0 回填冒充「没动」。
        for key in (
            "net_move_atr",
            "high_excursion_atr",
            "low_excursion_atr",
            "abs_range_atr",
            "atr_basis",
        ):
            assert displacement[key] is None
        # 经验线常量仍然回显，方便前端一致渲染。
        assert displacement["survival_line_atr"] == DISPLACEMENT_SURVIVAL_LINE_ATR

    def test_no_bars_at_all_is_insufficient_not_zero(self):
        displacement = compute_recent_displacement(
            [],
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        assert displacement["state"] == "insufficient_bars"
        assert displacement["bar_count"] == 0
        assert displacement["net_move_atr"] is None

    def test_zero_range_bars_without_atr14_are_unavailable_not_zero_division(self):
        bars = [
            _bar(
                f"2026-07-28T09:{minute:02d}:00-04:00",
                open_=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=10.0,
            )
            for minute in (30, 35, 40, 45, 50, 55)
        ]
        displacement = compute_recent_displacement(
            bars,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        assert displacement["state"] == "unavailable"
        assert displacement["bar_count"] == 6
        assert displacement["unavailable_reason"] == (
            "no_usable_atr_unit_atr14_prior_sessions_and_intraday_proxy_all_unavailable"
        )
        assert displacement["atr_basis"] is None
        assert displacement["atr_scale_comparability"] is None

    def test_closed_session_uses_the_latest_prior_session_as_of(self, labeled_bars):
        # 周六休市：口径落在 2026-07-31 的最后 30 分钟，与既有 as-of 标注一致。
        bars = labeled_bars["symbols"]["NVDA"]["bars"]
        closed = compute_recent_displacement(
            bars,
            market_date_et="2026-08-01",
            quote_session_scope="latest_prior_session",
            atr14=3.0,
        )
        same_session = compute_recent_displacement(
            bars,
            market_date_et="2026-07-31",
            quote_session_scope="current_session",
            atr14=3.0,
        )
        assert closed["state"] == "ready"
        assert closed["bar_count"] == 78
        assert closed == same_session
        # 盘中口径下的「今天」（08-03）尚无 K 线 → 显式不足，不借用上一时段。
        premarket = compute_recent_displacement(
            bars,
            market_date_et="2026-08-03",
            quote_session_scope="current_session",
            atr14=3.0,
        )
        assert premarket["state"] == "insufficient_bars"
        assert premarket["bar_count"] == 0


def _session_bars(
    *,
    date: str,
    start_hhmm: str = "09:30",
    count: int,
    price: float = 100.0,
    range_: float = 1.0,
    step: float = 0.0,
):
    """一段连续 5m K 线：每根波幅 ``range_``、每根收盘递增 ``step``。"""

    bars = []
    base_minutes = int(start_hhmm[:2]) * 60 + int(start_hhmm[3:])
    open_ = price
    for index in range(count):
        close = open_ + step
        minutes = base_minutes + index * 5
        bars.append(
            _bar(
                f"{date}T{minutes // 60:02d}:{minutes % 60:02d}:00-04:00",
                open_=open_,
                high=max(open_, close) + range_ / 2,
                low=min(open_, close) - range_ / 2,
                close=close,
                volume=1_000.0,
            )
        )
        open_ = close
    return bars


class TestAtrScaleComparability:
    """v7 ATR 标尺可比性：日线主路径不变、日线量级回退、旧代理降为兜底并标注。

    研究实测的缺陷：旧代理（最近 20 根 5m 波幅均值 ×3）与真实日线 ATR14 之比
    在盘中 0.28 → 0.42 → 0.20 漂移，它是一把**随时点伸缩的尺子**。下面第三个
    用例直接把这个伪影演示出来，并证明新回退层没有它。
    """

    def test_daily_atr14_path_is_unchanged_and_labeled_comparable(self):
        """主路径（生产上有 ATR14 的标的走这条）数值口径逐字未变。"""

        today = _session_bars(date="2026-07-28", count=6, step=0.2, range_=0.4)
        prior = _session_bars(date="2026-07-27", count=20, range_=3.0)
        with_prior = compute_recent_displacement(
            prior + today,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        without_prior = compute_recent_displacement(
            today,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=2.0,
        )
        assert with_prior["atr_basis"] == DISPLACEMENT_ATR_BASIS_DAILY
        assert with_prior["atr_scale_comparability"] == ATR_SCALE_DAILY_ATR14
        assert with_prior["atr_prior_session_count"] is None
        # 手算：窗口首根开盘 100.0、末收 101.2 → 净位移 1.2 / 2.0 = 0.6 ATR。
        assert with_prior["net_move_atr"] == pytest.approx((101.2 - 100.0) / 2.0)
        # 上一时段 K 线的存在完全不影响 ATR14 路径的任何数值。
        for key in (
            "net_move_atr",
            "high_excursion_atr",
            "low_excursion_atr",
            "abs_range_atr",
            "atr_basis",
            "atr_scale_comparability",
        ):
            assert with_prior[key] == without_prior[key]

    def test_prior_session_true_range_is_the_first_fallback(self):
        today = _session_bars(date="2026-07-28", count=6, step=0.2, range_=0.4)
        # 上一时段：20 根、每根波幅 1.0、收盘持平 → 时段高低差 = 1.0。
        prior = _session_bars(date="2026-07-27", count=20, range_=1.0)
        displacement = compute_recent_displacement(
            prior + today,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        assert displacement["atr_basis"] == DISPLACEMENT_ATR_BASIS_PRIOR_SESSIONS
        assert (
            displacement["atr_scale_comparability"] == ATR_SCALE_DAILY_PRIOR_SESSIONS
        )
        assert displacement["atr_prior_session_count"] == 1
        # 单个上一时段、无更早收盘 → true range 退化为高−低 = 1.0。
        assert displacement["net_move_atr"] == pytest.approx((101.2 - 100.0) / 1.0)

    def test_prior_session_unit_removes_the_time_of_day_artifact(self):
        """同一段价格几何，放在早盘 vs 午后：旧代理会给出不同读数，新回退层不会。"""

        window = _session_bars(
            date="2026-07-28", start_hhmm="10:40", count=6, step=0.2, range_=0.4
        )
        early_only = _session_bars(
            date="2026-07-28", start_hhmm="09:30", count=6, step=0.2, range_=0.4
        )
        loud_morning = _session_bars(
            date="2026-07-28", start_hhmm="09:30", count=14, range_=3.0
        )
        prior = _session_bars(date="2026-07-27", count=20, range_=1.0)

        # (a) 旧代理（没有上一时段可用）：同样的 6 根窗口，读数被前面的波动改写。
        proxy_early = compute_recent_displacement(
            early_only,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        proxy_late = compute_recent_displacement(
            loud_morning + window,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        assert proxy_early["atr_basis"] == DISPLACEMENT_ATR_BASIS_PROXY
        assert proxy_late["atr_basis"] == DISPLACEMENT_ATR_BASIS_PROXY
        assert proxy_early["net_move_atr"] != proxy_late["net_move_atr"]
        # 伪影量级：同一段位移在早盘被放大到午后读数的 3 倍以上。
        assert proxy_early["net_move_atr"] > 3 * proxy_late["net_move_atr"]
        assert (
            proxy_early["atr_scale_comparability"]
            == ATR_SCALE_INTRADAY_NOT_COMPARABLE
        )

        # (b) 新回退层（有上一时段）：标尺当日内恒定，同一段几何得到同一读数。
        fixed_early = compute_recent_displacement(
            prior + early_only,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        fixed_late = compute_recent_displacement(
            prior + loud_morning + window,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        assert fixed_early["atr_basis"] == DISPLACEMENT_ATR_BASIS_PRIOR_SESSIONS
        assert fixed_late["atr_basis"] == DISPLACEMENT_ATR_BASIS_PRIOR_SESSIONS
        assert fixed_early["net_move_atr"] == pytest.approx(fixed_late["net_move_atr"])

    def test_prior_session_true_range_includes_the_gap_when_two_sessions_exist(self):
        # 07-24 收 100.0；07-27 整段在 110 附近（高低差 1.0）→ true range 含跳空。
        older = _session_bars(date="2026-07-24", count=20, range_=1.0, price=100.0)
        prior = _session_bars(date="2026-07-27", count=20, range_=1.0, price=110.0)
        today = _session_bars(date="2026-07-28", count=6, step=0.2, range_=0.4, price=110.0)
        displacement = compute_recent_displacement(
            older + prior + today,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        assert displacement["atr_prior_session_count"] == 2
        # 07-24: 高−低 = 1.0（无更早收盘）；07-27: |110.5 − 100.0| = 10.5 → 均值 5.75。
        assert displacement["net_move_atr"] == pytest.approx(
            (111.2 - 110.0) / ((1.0 + 10.5) / 2), abs=1e-6
        )

    def test_intraday_proxy_stays_the_last_resort_with_its_original_math(self):
        """只有当日 K 线时仍用旧代理（口径逐字未变），但显式标为不可比。"""

        today = _session_bars(date="2026-07-28", count=6, step=0.2, range_=0.4)
        displacement = compute_recent_displacement(
            today,
            market_date_et="2026-07-28",
            quote_session_scope="current_session",
            atr14=None,
        )
        ranges = [bar["high"] - bar["low"] for bar in today]
        proxy = sum(ranges) / len(ranges) * DISPLACEMENT_PROXY_MULTIPLIER
        assert displacement["atr_basis"] == DISPLACEMENT_ATR_BASIS_PROXY
        assert (
            displacement["atr_scale_comparability"]
            == ATR_SCALE_INTRADAY_NOT_COMPARABLE
        )
        assert displacement["atr_prior_session_count"] is None
        assert displacement["net_move_atr"] == pytest.approx((101.2 - 100.0) / proxy)

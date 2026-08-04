# -*- coding: utf-8 -*-
"""Deterministic tests for the pure intraday Top-N builder (日内 Top 5).

Focus areas: each v1 ranking threshold in both directions, fail-closed
behaviour when the core quote is missing, honest Moomoo sentiment aggregation
(no direction invention), closed-session labelling and deterministic ordering.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.opportunities.intraday_bursts import (
    BURST_SUPPORT_MIN,
    DISPLACEMENT_ATR_BASIS_DAILY,
    DISPLACEMENT_ATR_BASIS_PROXY,
    DISPLACEMENT_SURVIVAL_LINE_ATR,
)
from src.opportunities.intraday_top import (
    ACTIVE_SUPPORT_MIN,
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_WINDOW_DAYS,
    GAP_BASIS_DAILY_LOADER,
    GAP_BASIS_SNAPSHOT_PREV_CLOSE,
    INTRADAY_STATISTICS_TRACK,
    INTRADAY_TOP_SIGNAL_VERSION,
    RANKING_METHOD_BURST_FIRST,
    RANKING_METHOD_EVIDENCE_COUNT,
    IntradayDailyContext,
    IntradayQuoteInput,
    build_intraday_top_candidate,
    build_intraday_top_run,
    collect_recent_option_events,
    compute_earnings_proximity,
    compute_intraday_daily_context,
    compute_market_alignment,
    compute_market_context,
    summarise_option_events,
)

_AS_OF = datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc)


def _burst_profile(score: float | None = 9.5, *, legs: list[dict] | None = None, state: str = "ready"):
    current = None
    if score is not None:
        current = {
            "start_et": "10:15",
            "end_et": "10:30",
            "thrust_percent": 1.8,
            "thrust_norm": 3.0,
            "vol_norm": score / 3.0,
            "score": score,
            "direction": "up",
        }
    return {
        "state": state,
        "session_date_et": "2026-07-28",
        "bar_count": 13,
        "median_bar_range": 1.0,
        "median_bar_volume": 1_000.0,
        "median_basis": "current_session_bars_so_far",
        "window_minutes": 15,
        "current": current,
        "legs": legs if legs is not None else ([current] if current else []),
        "unavailable_reason": None if state == "ready" else "no_regular_session_bars",
        "source": "fixture_5m",
        "fetched_at": "2026-07-28T14:30:06+00:00",
        "basis": "rolling_15m_thrust_over_median_range_times_volume_ratio",
        "limitations": [],
    }


def _daily(**overrides) -> IntradayDailyContext:
    base = dict(
        atr14=2.0,
        atr14_last_bar_date="2026-07-27",
        atr14_unavailable_reason=None,
        prior_median_volume=1_000.0,
        median_unavailable_reason=None,
        prior_close=100.0,
        prior_close_date="2026-07-27",
        prior_high_20d=105.0,
        prior_low_20d=95.0,
        ema8=99.0,
        ema13=98.0,
        source="fixture",
    )
    base.update(overrides)
    return IntradayDailyContext(**base)


def _quote(**overrides) -> IntradayQuoteInput:
    base = dict(
        last_price=103.0,
        session_open=102.0,
        session_high=104.0,
        session_low=101.5,
        prev_close=100.0,
        session_volume=1_600.0,
        session_turnover=164_000.0,  # vwap = 102.5 < last 103 → above
        quote_as_of="2026-07-28 10:30:04",
        fetched_at=datetime(2026, 7, 28, 14, 30, 5, tzinfo=timezone.utc),
        source="moomoo_openapi",
    )
    base.update(overrides)
    return IntradayQuoteInput(**base)


def _events(sentiments: list[str | None], *, turnovers: list[float | None] | None = None):
    events = []
    for index, sentiment in enumerate(sentiments):
        events.append(
            {
                "event_id": f"evt_{index}",
                "option_code": "US.NVDA260918C100000",
                "fill_time": f"2026-07-28 10:0{index}:00",
                "sentiment": sentiment,
                "turnover": (turnovers or [None] * len(sentiments))[index],
                "order_types": ["SWEEP"],
            }
        )
    return {
        "state": "ready" if events else "empty",
        "events": events,
        "all_count": len(events),
        "event_as_of": events[-1]["fill_time"] if events else None,
        "fetched_at": "2026-07-28T14:30:05+00:00",
        "source": "moomoo_openapi",
        "limitations": [],
    }


def _candidate(**overrides):
    kwargs = dict(
        quote=_quote(),
        daily=_daily(),
        option_events=_events(["BULLISH", "BULLISH", "BULLISH", "NEUTRAL"]),
        as_of=_AS_OF,
        quote_session_scope="current_session",
        moomoo_enabled=True,
    )
    kwargs.update(overrides)
    return build_intraday_top_candidate("NVDA", **kwargs)


def _evidence_status(candidate, metric: str) -> str:
    for item in candidate["evidence"]:
        if item["metric"] == metric:
            return item["status"]
    raise AssertionError(f"missing evidence metric {metric}")


class TestRankingThresholds:
    def test_full_support_row_is_active(self):
        candidate = _candidate()
        # gap +2% (≥1.5%, 1.0×ATR), pace 1.6×, range 1.25×ATR, vwap above
        # aligned with gap-up, 3 bullish events → 5 supports.
        assert candidate["supporting_evidence_count"] == 5
        assert candidate["research_state"] == "active"
        assert candidate["supporting_evidence_count"] >= ACTIVE_SUPPORT_MIN

    def test_volume_pace_threshold_both_directions(self):
        below = _candidate(quote=_quote(session_volume=1_499.0, session_turnover=153_647.5))
        at = _candidate(quote=_quote(session_volume=1_500.0, session_turnover=153_750.0))
        assert _evidence_status(below, "session_volume_pace") == "neutral"
        assert _evidence_status(at, "session_volume_pace") == "supports"

    def test_gap_atr_threshold_both_directions(self):
        # 1.4 dollars gap on ATR 2.0 → 0.7× and 1.4% — below both branches.
        below = _candidate(quote=_quote(session_open=101.4))
        assert _evidence_status(below, "session_gap_percent") == "neutral"
        # 1.5 dollars → 0.75×ATR exactly (percent 1.5% also at threshold).
        at = _candidate(quote=_quote(session_open=101.5))
        assert _evidence_status(at, "session_gap_percent") == "supports"

    def test_gap_percent_fallback_supports_without_atr(self):
        # ATR missing: 1.6% gap still supports via the absolute-percent branch.
        candidate = _candidate(
            quote=_quote(session_open=101.6),
            daily=_daily(atr14=None, atr14_unavailable_reason="insufficient_completed_bars:3<15"),
        )
        assert _evidence_status(candidate, "session_gap_percent") == "supports"
        assert candidate["gap_atr_multiple"] is None

    def test_range_expansion_threshold_both_directions(self):
        below = _candidate(quote=_quote(session_high=102.9, session_low=101.0))
        at = _candidate(quote=_quote(session_high=103.5, session_low=101.5))
        assert _evidence_status(below, "session_range_atr_expansion") == "neutral"
        assert _evidence_status(at, "session_range_atr_expansion") == "supports"

    def test_option_events_need_count_and_directional_majority(self):
        two_bullish = _candidate(option_events=_events(["BULLISH", "BULLISH"]))
        assert _evidence_status(two_bullish, "option_event_activity") == "neutral"
        neutral_majority = _candidate(
            option_events=_events(["NEUTRAL", "NEUTRAL", "BULLISH"])
        )
        assert _evidence_status(neutral_majority, "option_event_activity") == "neutral"
        three_bearish = _candidate(
            option_events=_events(["BEARISH", "BEARISH", "BEARISH"])
        )
        assert _evidence_status(three_bearish, "option_event_activity") == "supports"

    def test_vwap_support_requires_gap_alignment(self):
        # Gap down but price above VWAP → observable, not a support.
        misaligned = _candidate(quote=_quote(session_open=98.0))
        assert _evidence_status(misaligned, "session_vwap_position") == "neutral"
        aligned = _candidate()
        assert _evidence_status(aligned, "session_vwap_position") == "supports"

    def test_prior_day_structure_stays_context_only(self):
        candidate = _candidate()
        for item in candidate["evidence"]:
            if item["metric"] == "prior_day_structure_context":
                assert item["status"] == "neutral"
                assert item["actionability"] == "research_context"
                break
        else:
            raise AssertionError("missing prior-day context evidence")
        # Even a structurally extreme prior day never adds to the count.
        extreme = _candidate(
            daily=_daily(prior_high_20d=100.5),  # last 103 > prior high
        )
        assert extreme["prior_day_context"]["range_position"] == "above_prior_20d_high"
        assert extreme["supporting_evidence_count"] == _candidate()["supporting_evidence_count"]


class TestFailClosed:
    def test_missing_quote_is_insufficient_even_with_event_support(self):
        candidate = _candidate(
            quote=None,
            option_events=_events(["BULLISH", "BULLISH", "BULLISH"]),
        )
        assert candidate["research_state"] == "insufficient"
        assert candidate["last_price"] is None
        assert candidate["gap_unavailable_reason"] == "quote_unavailable"
        assert candidate["volume_pace_unavailable_reason"] == "quote_unavailable"
        assert candidate["vwap_position"] == "unknown"

    def test_missing_last_price_is_insufficient(self):
        candidate = _candidate(quote=_quote(last_price=None))
        assert candidate["research_state"] == "insufficient"

    def test_disabled_moomoo_reason_is_explicit(self):
        candidate = _candidate(quote=None, moomoo_enabled=False)
        assert candidate["research_state"] == "insufficient"
        assert "MOOMOO_OPEND_ENABLED" in candidate["state_reason"]

    def test_missing_session_open_marks_gap_unknown(self):
        candidate = _candidate(quote=_quote(session_open=None))
        assert candidate["gap_percent"] is None
        assert candidate["gap_unavailable_reason"] == "missing_session_open"
        assert _evidence_status(candidate, "session_gap_percent") == "unknown"


class TestSentimentAggregationHonesty:
    def test_neutral_majority_stays_neutral(self):
        summary = summarise_option_events(
            _events(["NEUTRAL", "NEUTRAL", "NEUTRAL", "BULLISH"])
        )
        assert summary["dominant_sentiment"] == "neutral"
        assert summary["bullish_count"] == 1

    def test_all_unclassified_is_unknown_not_a_direction(self):
        summary = summarise_option_events(_events([None, None, ""]))
        assert summary["dominant_sentiment"] == "unknown"
        assert summary["unclassified_count"] == 3

    def test_bullish_bearish_tie_is_mixed_never_invented(self):
        summary = summarise_option_events(
            _events(["BULLISH", "BEARISH", "BULLISH", "BEARISH"])
        )
        assert summary["dominant_sentiment"] == "mixed"

    def test_max_single_turnover_and_counts(self):
        summary = summarise_option_events(
            _events(
                ["BULLISH", "BEARISH", "NEUTRAL"],
                turnovers=[10_000.0, 250_000.0, None],
            )
        )
        assert summary["max_single_turnover"] == 250_000.0
        assert summary["count"] == 3
        assert summary["bearish_count"] == 1

    def test_missing_payload_is_unavailable(self):
        summary = summarise_option_events(None)
        assert summary["state"] == "unavailable"
        assert summary["dominant_sentiment"] == "unknown"
        assert summary["count"] == 0


class TestRecentEventFeed:
    def test_newest_first_bounded_and_ticker_attached(self):
        items = {
            "NVDA": _events(["BULLISH", "BEARISH"]),
            "TSLA": _events(["NEUTRAL"]),
        }
        feed = collect_recent_option_events(items, limit=2)
        assert len(feed) == 2
        assert [row["fill_time"] for row in feed] == sorted(
            [row["fill_time"] for row in feed], reverse=True
        )
        assert all(row["ticker"] in {"NVDA", "TSLA"} for row in feed)

    def test_events_without_fill_time_are_dropped_not_sorted_blind(self):
        payload = _events(["BULLISH"])
        payload["events"][0]["fill_time"] = None
        feed = collect_recent_option_events({"NVDA": payload})
        assert feed == []


class TestRunAssembly:
    def _run(
        self,
        *,
        session_state="regular",
        quotes=None,
        symbols=("NVDA", "TSLA"),
        burst_profiles=None,
        earnings_calendar=None,
        spy_quote=None,
    ):
        return build_intraday_top_run(
            symbols=list(symbols),
            unsupported_symbols=["600519"],
            quotes=quotes if quotes is not None else {"NVDA": _quote()},
            dailies={symbol: _daily() for symbol in symbols},
            option_event_items={"NVDA": _events(["BULLISH"] * 3)},
            as_of=_AS_OF,
            market_date_et="2026-07-28",
            session_state=session_state,
            session_state_basis="america_new_york_clock_v1",
            limit=5,
            moomoo_enabled=True,
            burst_profiles=burst_profiles,
            earnings_calendar=earnings_calendar,
            spy_quote=spy_quote,
        )

    def test_run_contract_and_statistics_track(self):
        run = self._run()
        assert run["schema_version"] == "intraday-top/1.0"
        assert run["signal_version"] == INTRADAY_TOP_SIGNAL_VERSION
        assert run["signal_version"] == "intraday_session_evidence_v6"
        # 盘中（current_session scope）＝爆发分优先；休市退回证据计数。
        assert run["ranking_method"] == RANKING_METHOD_BURST_FIRST
        assert self._run(session_state="closed")["ranking_method"] == (
            RANKING_METHOD_BURST_FIRST
        )
        assert run["statistics_track"] == INTRADAY_STATISTICS_TRACK
        assert run["unsupported_symbols"] == ["600519"]
        assert any("不冻结" in text for text in run["limitations"])
        assert any("不推断开平仓" in text for text in run["limitations"])
        assert any("波段爆发" in text for text in run["limitations"])
        # v3 设计规则随响应携带：系统标注，用户过滤。
        assert any("系统标注，用户过滤" in text for text in run["limitations"])

    def test_run_session_phase_from_as_of_clock(self):
        # _AS_OF = 2026-07-28（周二）14:30 UTC = 10:30 ET → prime（主战场）。
        run = self._run()
        assert run["session_phase"] == "prime"
        assert "主战场" in run["session_phase_label"]
        assert run["session_phase_hint_basis"] == "user_trading_history_hardcoded_v1"
        # 周六休市：时段标签同样诚实为休市/复盘。
        saturday = build_intraday_top_run(
            symbols=["NVDA"],
            unsupported_symbols=[],
            quotes={},
            dailies={},
            option_event_items={},
            as_of=datetime(2026, 7, 25, 14, 30, tzinfo=timezone.utc),
            market_date_et="2026-07-25",
            session_state="closed",
            session_state_basis="america_new_york_clock_v1",
            limit=5,
            moomoo_enabled=False,
        )
        assert saturday["session_phase"] == "closed"
        assert "休市" in saturday["session_phase_label"]

    def test_run_market_context_and_candidate_alignment(self):
        spy = _quote(
            last_price=500.0,
            session_volume=1_000.0,
            session_turnover=499_000.0,  # SPY VWAP 499 < last 500 → above
        )
        run = self._run(
            burst_profiles={"NVDA": _burst_profile(9.0)},
            spy_quote=spy,
        )
        assert run["market_context"]["state"] == "ready"
        assert run["market_context"]["vwap_position"] == "above"
        nvda = next(item for item in run["candidates"] if item["ticker"] == "NVDA")
        # 爆发方向 up + SPY VWAP 上方 → 顺势；仅作标注，不改 supports 计数。
        assert nvda["market_alignment"]["state"] == "aligned"

    def test_run_without_spy_quote_keeps_alignment_unknown(self):
        run = self._run(burst_profiles={"NVDA": _burst_profile(9.0)})
        assert run["market_context"]["state"] == "unavailable"
        nvda = next(item for item in run["candidates"] if item["ticker"] == "NVDA")
        assert nvda["market_alignment"]["state"] == "unknown"
        assert nvda["market_alignment"]["unavailable_reason"] == "spy_quote_unavailable"

    def test_ordering_active_before_watch_before_insufficient(self):
        run = self._run(symbols=("TSLA", "NVDA"))
        # NVDA has a quote (active); TSLA has no quote (insufficient).
        states = [item["research_state"] for item in run["candidates"]]
        assert states == sorted(
            states, key=lambda state: {"active": 0, "watch": 1, "insufficient": 2}[state]
        )
        assert run["candidates"][0]["ticker"] == "NVDA"

    def test_closed_session_labels_evidence_and_switches_gap_basis(self):
        run = self._run(session_state="closed")
        assert run["quote_session_scope"] == "latest_prior_session"
        assert run["quote_session_label"] == "最近一个交易时段"
        nvda = next(item for item in run["candidates"] if item["ticker"] == "NVDA")
        assert nvda["gap_basis"] == GAP_BASIS_SNAPSHOT_PREV_CLOSE
        gap_evidence = next(
            item for item in nvda["evidence"] if item["metric"] == "session_gap_percent"
        )
        assert gap_evidence["observation_window"] == "latest_completed_trading_session"

    def test_live_session_uses_daily_loader_gap_basis(self):
        run = self._run()
        nvda = next(item for item in run["candidates"] if item["ticker"] == "NVDA")
        assert nvda["gap_basis"] == GAP_BASIS_DAILY_LOADER

    def test_limit_bounds(self):
        with pytest.raises(ValueError):
            self._run_with_limit(0)
        with pytest.raises(ValueError):
            self._run_with_limit(11)

    def _run_with_limit(self, limit: int):
        return build_intraday_top_run(
            symbols=["NVDA"],
            unsupported_symbols=[],
            quotes={},
            dailies={},
            option_event_items={},
            as_of=_AS_OF,
            market_date_et="2026-07-28",
            session_state="regular",
            session_state_basis="america_new_york_clock_v1",
            limit=limit,
            moomoo_enabled=False,
        )


class TestBurstEvidence:
    def test_supports_at_threshold_both_directions(self):
        below = _candidate(burst_profile=_burst_profile(BURST_SUPPORT_MIN - 0.01))
        at = _candidate(burst_profile=_burst_profile(BURST_SUPPORT_MIN))
        assert _evidence_status(below, "session_momentum_burst") == "neutral"
        assert _evidence_status(at, "session_momentum_burst") == "supports"
        assert at["supporting_evidence_count"] == below["supporting_evidence_count"] + 1

    def test_missing_profile_is_unknown_and_never_blocks_aggregates(self):
        candidate = _candidate(burst_profile=None)
        assert _evidence_status(candidate, "session_momentum_burst") == "unknown"
        assert candidate["session_bursts"]["state"] == "unavailable"
        # 聚合证据完全不受影响（缺口/量能/波幅/VWAP/异动仍是 5 项支持）。
        assert candidate["supporting_evidence_count"] == 5
        assert candidate["research_state"] == "active"

    def test_unavailable_profile_keeps_reason(self):
        candidate = _candidate(
            burst_profile=_burst_profile(None, state="unavailable")
        )
        assert candidate["session_bursts"]["state"] == "unavailable"
        assert _evidence_status(candidate, "session_momentum_burst") == "unknown"

    def test_payload_carries_current_and_legs(self):
        candidate = _candidate(burst_profile=_burst_profile(12.0))
        bursts = candidate["session_bursts"]
        assert bursts["current"]["score"] == 12.0
        assert bursts["legs"][0]["direction"] == "up"
        assert bursts["basis"].startswith("rolling_15m")


class TestBurstRanking:
    def _run(self, *, session_state="regular", burst_profiles=None):
        symbols = ("NVDA", "TSLA")
        return build_intraday_top_run(
            symbols=list(symbols),
            unsupported_symbols=[],
            quotes={symbol: _quote() for symbol in symbols},
            dailies={symbol: _daily() for symbol in symbols},
            option_event_items={
                "NVDA": _events(["BULLISH"] * 3),
                "TSLA": _events([]),
            },
            as_of=_AS_OF,
            market_date_et="2026-07-28",
            session_state=session_state,
            session_state_basis="america_new_york_clock_v1",
            limit=5,
            moomoo_enabled=True,
            burst_profiles=burst_profiles,
        )

    def test_in_session_higher_burst_outranks_higher_evidence_count(self):
        # NVDA 拿满聚合证据；TSLA 证据更少但当前爆发分更高 → TSLA 在前。
        run = self._run(
            burst_profiles={
                "NVDA": _burst_profile(6.5),
                "TSLA": _burst_profile(20.0),
            }
        )
        assert [item["ticker"] for item in run["candidates"]] == ["TSLA", "NVDA"]
        assert run["ranking_method"] == RANKING_METHOD_BURST_FIRST

    def test_in_session_missing_burst_falls_behind_scored_rows(self):
        run = self._run(
            burst_profiles={"TSLA": _burst_profile(6.5)}
        )
        assert [item["ticker"] for item in run["candidates"]] == ["TSLA", "NVDA"]

    def test_closed_session_ranks_by_strongest_session_leg(self):
        legs = [
            {
                "start_et": "09:40",
                "end_et": "09:55",
                "thrust_percent": -0.93,
                "thrust_norm": 3.17,
                "vol_norm": 2.95,
                "score": 9.33,
                "direction": "down",
            },
            {
                "start_et": "15:15",
                "end_et": "15:30",
                "thrust_percent": 0.95,
                "thrust_norm": 3.19,
                "vol_norm": 2.68,
                "score": 8.56,
                "direction": "up",
            },
        ]
        run = self._run(
            session_state="closed",
            burst_profiles={
                # 休市按最近交易时段的最强波段分排序：TSLA（50.0）在前，
                # NVDA（最强腿 9.33）在后；波段列表仍完整携带供复盘。
                "NVDA": _burst_profile(1.0, legs=legs),
                "TSLA": _burst_profile(50.0),
            },
        )
        assert run["ranking_method"] == RANKING_METHOD_BURST_FIRST
        assert [item["ticker"] for item in run["candidates"]] == ["TSLA", "NVDA"]
        nvda = run["candidates"][1]
        assert [leg["start_et"] for leg in nvda["session_bursts"]["legs"]] == [
            "09:40",
            "15:15",
        ]


class TestSetupMatchWiring:
    """v4 styleMatch：候选行携带 setup_match，纯标注、不进计数、不改排序。"""

    def test_candidate_carries_setup_match_without_changing_supports(self):
        candidate = _candidate()
        profile = candidate["setup_match"]
        assert profile["style_match_version"] == "style_match_v1"
        assert [row["setup_key"] for row in profile["setups"]] == ["S1", "S2", "S3"]
        # 默认 quote：+2% 跳空（1.0×ATR）、最低 101.5 > 前收 100、
        # 现价 103 ≥ VWAP 102.5 → S2 matched；无 5m K 线 → S1 unavailable。
        assert profile["matched_setups"] == ["S2"]
        s1 = next(row for row in profile["setups"] if row["setup_key"] == "S1")
        assert s1["state"] == "unavailable"
        # 形态标注绝不进入 supports 计数或研究状态。
        assert candidate["supporting_evidence_count"] == 5
        assert candidate["research_state"] == "active"

    def test_setup_bars_flow_into_s1_detection(self):
        bars = [
            {
                "date": f"2026-07-28T{time_text}:00-04:00",
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000.0,
            }
            for time_text, open_, high, low, close in (
                ("09:30", 100.5, 101.0, 100.0, 100.6),
                ("09:45", 100.4, 100.5, 99.0, 99.4),
                ("10:00", 99.9, 100.8, 99.8, 100.6),
                ("10:15", 100.5, 100.6, 99.5, 99.9),
                ("10:30", 100.0, 100.7, 100.0, 100.5),
                ("10:45", 100.6, 101.4, 100.4, 101.2),
            )
        ]
        candidate = _candidate(setup_bars=bars, market_date_et="2026-07-28")
        profile = candidate["setup_match"]
        s1 = next(row for row in profile["setups"] if row["setup_key"] == "S1")
        assert s1["state"] == "matched"
        assert profile["matched_setups"] == ["S1", "S2"]
        assert profile["bar_count_15m"] == 6

    def test_run_passes_playbook_refs_into_each_candidate(self):
        refs = {
            "S2": {
                "setup_key": "S2",
                "candidate_key": "b" * 64,
                "status": "candidate",
                "title": "S2 · 跳空高开托举（轻仓试错，需二次确认）",
            }
        }
        run = build_intraday_top_run(
            symbols=["NVDA"],
            unsupported_symbols=[],
            quotes={"NVDA": _quote()},
            dailies={"NVDA": _daily()},
            option_event_items={},
            as_of=_AS_OF,
            market_date_et="2026-07-28",
            session_state="regular",
            session_state_basis="america_new_york_clock_v1",
            limit=5,
            moomoo_enabled=True,
            playbook_refs=refs,
        )
        profile = run["candidates"][0]["setup_match"]
        s2 = next(row for row in profile["setups"] if row["setup_key"] == "S2")
        assert s2["state"] == "matched"
        assert s2["playbook"]["status"] == "candidate"
        s1 = next(row for row in profile["setups"] if row["setup_key"] == "S1")
        assert s1["playbook"] is None
        # v4 限制随响应携带：形态相似 ≠ 可交易，Playbook 不反哺评分。
        assert any("形态相似 ≠ 可交易" in text for text in run["limitations"])
        assert any("不反哺" in text for text in run["limitations"])

    def test_closed_session_setup_match_keeps_as_of_scope(self):
        run = build_intraday_top_run(
            symbols=["NVDA"],
            unsupported_symbols=[],
            quotes={"NVDA": _quote()},
            dailies={"NVDA": _daily()},
            option_event_items={},
            as_of=_AS_OF,
            market_date_et="2026-07-28",
            session_state="closed",
            session_state_basis="america_new_york_clock_v1",
            limit=5,
            moomoo_enabled=True,
        )
        profile = run["candidates"][0]["setup_match"]
        assert profile["quote_session_scope"] == "latest_prior_session"


class TestRecentDisplacementWiring:
    """v6 近 30 分钟位移：候选行携带 recent_displacement（additive 标注）。

    它复用 styleMatch 已注入的同一批 5m K 线（零新增请求）与日线 ATR14，
    绝不进入 supports 计数、绝不改变排序。
    """

    @staticmethod
    def _bars(closes, *, date_text="2026-07-28", start_minutes=9 * 60 + 30):
        rows = []
        previous_close = closes[0]
        for index, close in enumerate(closes):
            open_ = previous_close
            minutes = start_minutes + index * 5
            rows.append(
                {
                    "date": (
                        f"{date_text}T{minutes // 60:02d}:{minutes % 60:02d}:00-04:00"
                    ),
                    "open": open_,
                    "high": max(open_, close) + 0.1,
                    "low": min(open_, close) - 0.1,
                    "close": close,
                    "volume": 1_000.0,
                }
            )
            previous_close = close
        return rows

    def test_candidate_carries_displacement_on_the_daily_atr14_scale(self):
        # 6 根 K 线：窗口首根开盘 100.0 → 末收 102.0，ATR14=2.0 → +1.0 ATR，
        # 越过用户自己 766 笔样本的 0.5 ATR 经验线。
        candidate = _candidate(
            setup_bars=self._bars([100.0, 100.5, 101.0, 101.2, 101.6, 102.0]),
            market_date_et="2026-07-28",
        )
        displacement = candidate["recent_displacement"]
        assert displacement["state"] == "ready"
        assert displacement["atr_basis"] == DISPLACEMENT_ATR_BASIS_DAILY
        assert displacement["net_move_atr"] == pytest.approx(1.0)
        assert displacement["window_minutes"] == 30
        assert displacement["survival_line_atr"] == DISPLACEMENT_SURVIVAL_LINE_ATR
        assert displacement["bar_count"] == 6
        # 纯标注：既不加 supports 计数，也不出现在 evidence 列表里。
        assert candidate["supporting_evidence_count"] == 5
        assert all(
            item["metric"] != "recent_displacement" for item in candidate["evidence"]
        )

    def test_missing_atr14_falls_back_to_intraday_proxy_with_label(self):
        candidate = _candidate(
            daily=_daily(atr14=None, atr14_unavailable_reason="insufficient_bars"),
            setup_bars=self._bars([100.0, 100.5, 101.0, 101.2, 101.6, 102.0]),
            market_date_et="2026-07-28",
        )
        displacement = candidate["recent_displacement"]
        assert displacement["state"] == "ready"
        assert displacement["atr_basis"] == DISPLACEMENT_ATR_BASIS_PROXY
        assert displacement["net_move_atr"] is not None

    def test_without_bars_it_is_insufficient_not_zero(self):
        displacement = _candidate()["recent_displacement"]
        assert displacement["state"] == "insufficient_bars"
        assert displacement["bar_count"] == 0
        assert displacement["net_move_atr"] is None
        assert displacement["atr_basis"] is None

    def test_closed_session_evaluates_the_latest_prior_session(self):
        run = build_intraday_top_run(
            symbols=["NVDA"],
            unsupported_symbols=[],
            quotes={"NVDA": _quote()},
            dailies={"NVDA": _daily()},
            option_event_items={},
            as_of=_AS_OF,
            market_date_et="2026-08-01",  # 周六休市
            session_state="closed",
            session_state_basis="america_new_york_clock_v1",
            limit=5,
            moomoo_enabled=True,
            setup_bars={
                "NVDA": self._bars(
                    [100.0, 100.5, 101.0, 101.2, 101.6, 102.0],
                    date_text="2026-07-31",
                )
            },
        )
        displacement = run["candidates"][0]["recent_displacement"]
        assert run["quote_session_scope"] == "latest_prior_session"
        assert displacement["state"] == "ready"
        assert displacement["net_move_atr"] == pytest.approx(1.0)

    def test_run_limitations_carry_the_displacement_disclosure(self):
        run = build_intraday_top_run(
            symbols=["NVDA"],
            unsupported_symbols=[],
            quotes={"NVDA": _quote()},
            dailies={"NVDA": _daily()},
            option_event_items={},
            as_of=_AS_OF,
            market_date_et="2026-07-28",
            session_state="regular",
            session_state_basis="america_new_york_clock_v1",
            limit=5,
            moomoo_enabled=True,
        )
        joined = "".join(run["limitations"])
        assert "近 30 分钟位移" in joined
        assert "不是预测、不是买卖信号" in joined
        assert "766 笔" in joined


class TestDailyContext:
    def _bars(self, count: int):
        rows = []
        current = date(2026, 7, 27)
        dates: list[date] = []
        while len(dates) < count:
            if current.weekday() < 5:
                dates.append(current)
            current -= timedelta(days=1)
        for index, observed in enumerate(reversed(dates)):
            close = 100.0 + index
            rows.append(
                {
                    "date": observed,
                    "open": close - 0.2,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000,
                    "amount": close * 1_000,
                }
            )
        return rows

    def test_context_from_full_window(self):
        context = compute_intraday_daily_context(self._bars(25))
        assert context["prior_close"] == 124.0
        assert context["prior_close_date"] == "2026-07-27"
        assert context["prior_high_20d"] == 124.5
        assert context["prior_low_20d"] == 104.5
        assert context["ema8"] is not None and context["ema13"] is not None

    def test_short_window_leaves_range_unknown(self):
        context = compute_intraday_daily_context(self._bars(10))
        assert context["prior_high_20d"] is None
        assert context["prior_low_20d"] is None
        assert context["prior_close"] == 109.0

    def test_empty_bars_all_none(self):
        context = compute_intraday_daily_context([])
        assert all(value is None for value in context.values())


def _earnings_calendar(dates_by_symbol, *, state="ready", reason=None):
    return {
        "state": state,
        "dates_by_symbol": dates_by_symbol,
        "window_start": "2026-07-28",
        "window_end": "2026-08-02",
        "window_days": EARNINGS_WINDOW_DAYS,
        "source": "finnhub_earnings_calendar",
        "fetched_at": "2026-07-28T14:30:05+00:00",
        "unavailable_reason": reason,
    }


class TestEarningsProximity:
    """v3 财报临近：0..5 天窗口、3 天回避阈值、不可得时的诚实标缺。"""

    def test_earnings_today_is_zero_days_within_blackout(self):
        proximity = compute_earnings_proximity(
            "NVDA",
            _earnings_calendar({"NVDA": ["2026-07-28"]}),
            market_date_et="2026-07-28",
        )
        assert proximity["state"] == "ready"
        assert proximity["days_to_earnings"] == 0
        assert proximity["earnings_date"] == "2026-07-28"
        assert proximity["within_blackout"] is True
        assert proximity["blackout_days"] == EARNINGS_BLACKOUT_DAYS == 3

    def test_blackout_boundary_three_in_four_out(self):
        at_boundary = compute_earnings_proximity(
            "NVDA",
            _earnings_calendar({"NVDA": ["2026-07-31"]}),
            market_date_et="2026-07-28",
        )
        outside = compute_earnings_proximity(
            "NVDA",
            _earnings_calendar({"NVDA": ["2026-08-01"]}),
            market_date_et="2026-07-28",
        )
        assert at_boundary["days_to_earnings"] == 3
        assert at_boundary["within_blackout"] is True
        assert outside["days_to_earnings"] == 4
        assert outside["within_blackout"] is False

    def test_earliest_in_window_wins_and_beyond_window_is_excluded(self):
        proximity = compute_earnings_proximity(
            "NVDA",
            _earnings_calendar({"NVDA": ["2026-08-10", "2026-07-30", "2026-08-01"]}),
            market_date_et="2026-07-28",
        )
        assert proximity["earnings_date"] == "2026-07-30"
        assert proximity["days_to_earnings"] == 2
        # 窗口外（+5 天以后）的日期不参与；过去的日期也不参与。
        stale = compute_earnings_proximity(
            "NVDA",
            _earnings_calendar({"NVDA": ["2026-07-27", "2026-08-10"]}),
            market_date_et="2026-07-28",
        )
        assert stale["state"] == "ready"
        assert stale["earnings_date"] is None
        assert stale["within_blackout"] is False

    def test_ready_with_no_earnings_is_honest_false_not_missing(self):
        proximity = compute_earnings_proximity(
            "NVDA", _earnings_calendar({}), market_date_et="2026-07-28"
        )
        assert proximity["state"] == "ready"
        assert proximity["days_to_earnings"] is None
        assert proximity["within_blackout"] is False

    def test_unavailable_calendar_never_fakes_safe(self):
        missing = compute_earnings_proximity(
            "NVDA", None, market_date_et="2026-07-28"
        )
        failed = compute_earnings_proximity(
            "NVDA",
            _earnings_calendar(
                {}, state="unavailable", reason="finnhub_not_configured"
            ),
            market_date_et="2026-07-28",
        )
        for proximity, reason in (
            (missing, "earnings_calendar_missing"),
            (failed, "finnhub_not_configured"),
        ):
            assert proximity["state"] == "unavailable"
            # within_blackout 保持 None（未知），绝不以 False 冒充「安全」。
            assert proximity["within_blackout"] is None
            assert proximity["days_to_earnings"] is None
            assert proximity["unavailable_reason"] == reason


class TestMarketAlignment:
    """v3 大盘对齐矩阵：up/above=顺势、down/below=顺势、反向=逆势、其余标缺。"""

    def _context(self, position: str = "above"):
        return {
            "ticker": "SPY",
            "state": "ready",
            "vwap_position": position,
            "unavailable_reason": None,
        }

    def _bursts(self, direction: str = "up"):
        profile = _burst_profile(9.0)
        profile["current"]["direction"] = direction
        return profile

    @pytest.mark.parametrize(
        ("direction", "position", "expected"),
        [
            ("up", "above", "aligned"),
            ("down", "below", "aligned"),
            ("up", "below", "against"),
            ("down", "above", "against"),
        ],
    )
    def test_alignment_matrix(self, direction, position, expected):
        alignment = compute_market_alignment(
            self._bursts(direction), self._context(position)
        )
        assert alignment["state"] == expected
        assert alignment["burst_direction"] == direction
        assert alignment["spy_vwap_position"] == position

    def test_flat_sides_are_unknown_not_a_direction(self):
        flat_burst = compute_market_alignment(
            self._bursts("flat"), self._context("above")
        )
        flat_spy = compute_market_alignment(
            self._bursts("up"), self._context("flat")
        )
        assert flat_burst["state"] == "unknown"
        assert flat_spy["state"] == "unknown"
        assert flat_spy["unavailable_reason"] == "flat_side_has_no_direction"

    def test_missing_burst_or_spy_side_is_unknown_with_reason(self):
        no_burst = compute_market_alignment(None, self._context("above"))
        assert no_burst["state"] == "unknown"
        assert no_burst["unavailable_reason"] == "burst_direction_unavailable"
        unavailable_burst = compute_market_alignment(
            _burst_profile(None, state="unavailable"), self._context("above")
        )
        assert unavailable_burst["state"] == "unknown"
        no_spy = compute_market_alignment(self._bursts("up"), None)
        assert no_spy["state"] == "unknown"
        assert no_spy["unavailable_reason"] == "spy_vwap_position_unavailable"

    def test_market_context_from_quote_and_fail_closed_paths(self):
        ready = compute_market_context(
            _quote(
                last_price=500.0,
                session_volume=1_000.0,
                session_turnover=501_000.0,
            ),
            moomoo_enabled=True,
        )
        assert ready["state"] == "ready"
        assert ready["vwap"] == 501.0
        assert ready["vwap_position"] == "below"

        disabled = compute_market_context(_quote(), moomoo_enabled=False)
        assert disabled["state"] == "not_configured"
        assert disabled["unavailable_reason"] == "moomoo_not_configured"

        missing_quote = compute_market_context(None, moomoo_enabled=True)
        assert missing_quote["state"] == "unavailable"
        assert missing_quote["unavailable_reason"] == "spy_quote_unavailable"

        missing_volume = compute_market_context(
            _quote(session_volume=None), moomoo_enabled=True
        )
        assert missing_volume["state"] == "unavailable"
        assert missing_volume["vwap_position"] == "unknown"
        assert missing_volume["unavailable_reason"] == "missing_or_nonpositive_volume"

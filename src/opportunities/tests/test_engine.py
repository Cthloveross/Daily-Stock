from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from src.opportunities.engine import (
    DailyHistoryInput,
    build_daily_opportunity_run,
    completed_daily_bars,
)


def _business_dates(end: date, count: int) -> list[date]:
    out: list[date] = []
    current = end
    while len(out) < count:
        if current.weekday() < 5:
            out.append(current)
        current -= timedelta(days=1)
    return list(reversed(out))


def _bars(
    *,
    end: date = date(2026, 7, 21),
    count: int = 25,
    rising: bool = True,
    last_volume: float = 2_000.0,
) -> list[dict]:
    rows: list[dict] = []
    dates = _business_dates(end, count)
    for index, observed_date in enumerate(dates):
        close = 100.0 + index if rising else 150.0 - index
        rows.append(
            {
                "date": observed_date.isoformat(),
                "open": close - 0.25,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": last_volume if index == len(dates) - 1 else 1_000.0,
                "amount": None,
            }
        )
    return rows


def _regime(label: str = "standard") -> dict:
    return {
        "date": date(2026, 7, 22),
        "score": 65 if label == "standard" else 20,
        "label": label,
        "version": "v1",
        "generated_at": datetime(2026, 7, 22, 11, 0, tzinfo=timezone.utc),
    }


def _run(symbols, histories, regime=None, limit=10):
    return build_daily_opportunity_run(
        symbols=symbols,
        histories=histories,
        regime=regime,
        as_of=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
        limit=limit,
    )


def test_current_session_daily_bar_is_excluded_before_and_after_new_york_close():
    rows = _bars(end=date(2026, 7, 22), count=22)

    before_close = completed_daily_bars(
        rows, as_of=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
    )
    after_close = completed_daily_bars(
        rows, as_of=datetime(2026, 7, 22, 21, 30, tzinfo=timezone.utc)
    )

    assert before_close[-1]["date"] == date(2026, 7, 21)
    assert after_close[-1]["date"] == date(2026, 7, 21)
    assert all(item["date"] != date(2026, 7, 22) for item in before_close)
    assert all(item["date"] != date(2026, 7, 22) for item in after_close)


def test_engine_emits_evidence_vector_without_composite_score():
    result = _run(
        ["aapl"],
        {
            "AAPL": DailyHistoryInput(
                bars=_bars(),
                source="fixture",
                fetched_at=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
            )
        },
        _regime(),
    )

    candidate = result["candidates"][0]
    by_metric = {item["metric"]: item for item in candidate["evidence"]}
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}
    assert result["universe"] == ["AAPL"]
    assert result["ranking_method"] == "rule_based_evidence_count"
    assert result["strategy_validation_state"] == "not_validated"
    assert "不是胜率" in result["strategy_validation_message"]
    assert candidate["research_state"] == "research_ready"
    assert candidate["directional_context"] == "bullish"
    assert candidate["data_completeness"] == {
        "state": "complete",
        "available_count": 7,
        "expected_count": 7,
    }
    assert by_metric["ema8_ema13_alignment"]["status"] == "supports"
    assert by_metric["prior_20d_range_position"]["value"]["context"] == "breakout"
    assert by_metric["volume_vs_prior_20d_median"]["value"] == 2.0
    assert by_metric["dollar_volume_vs_prior_20d_median"]["quality_state"] == "derived"
    # The close*volume proxy is retained as context but must not double-count
    # the already supporting raw-volume observation.
    assert candidate["supporting_evidence_count"] == 4
    assert gates["directional_structure_confirmed"]["status"] == "passed"
    assert gates["underlying_activity_confirmed"]["status"] == "passed"
    assert gates["research_evidence_ready"]["status"] == "passed"
    assert "不是交易指令" in gates["research_evidence_ready"]["reason"]
    assert "score" not in result
    assert "score" not in candidate


def test_missing_history_is_blocked_and_missing_domains_stay_explicit():
    result = _run(
        ["NVDA"],
        {"NVDA": DailyHistoryInput(bars=(), error="provider offline")},
        _regime(),
    )

    candidate = result["candidates"][0]
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}
    readiness = {item["domain"]: item for item in candidate["readiness"]}
    assert candidate["research_state"] == "blocked"
    assert candidate["directional_context"] == "unknown"
    assert gates["completed_daily_history"]["status"] == "failed"
    assert gates["research_evidence_ready"]["status"] == "failed"
    assert readiness["daily_history"]["state"] == "unavailable"
    assert readiness["options_flow"]["state"] == "not_configured"
    assert readiness["dark_pool"]["actionability"] == "background_only"


def test_no_trade_regime_keeps_candidate_as_context_only():
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=_bars(), source="fixture")},
        _regime("no_trade"),
    )

    candidate = result["candidates"][0]
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}
    regime_evidence = next(item for item in candidate["evidence"] if item["metric"] == "stored_regime")
    assert candidate["research_state"] == "context_only"
    assert gates["regime_allows_new_risk"]["status"] == "failed"
    assert gates["research_evidence_ready"]["status"] == "failed"
    assert "no_trade" in gates["research_evidence_ready"]["reason"]
    assert regime_evidence["status"] == "contradicts"


def test_unavailable_stored_regime_does_not_turn_missing_inputs_into_risk_off():
    regime = _regime("no_trade")
    regime["snapshot"] = {
        "spy": {},
        "vix": {},
        "events": {},
        "sectors": {},
        "prev_day": {},
        "premarket": {},
    }
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=_bars(), source="fixture")},
        regime,
    )

    candidate = result["candidates"][0]
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}
    readiness = {item["domain"]: item for item in candidate["readiness"]}
    regime_evidence = next(
        item for item in candidate["evidence"] if item["metric"] == "stored_regime"
    )
    run_readiness = {item["domain"]: item for item in result["run_readiness"]}

    assert candidate["research_state"] == "research_ready"
    assert gates["regime_allows_new_risk"]["status"] == "unknown"
    assert gates["research_evidence_ready"]["status"] == "passed"
    assert readiness["regime"]["state"] == "unavailable"
    assert run_readiness["regime"]["state"] == "unavailable"
    assert regime_evidence["status"] == "unknown"
    assert regime_evidence["value"] is None
    assert candidate["data_completeness"]["available_count"] == 6


def test_degraded_regime_is_context_not_an_authoritative_no_trade_gate():
    regime = _regime("no_trade")
    regime["snapshot"] = {
        "spy": {
            "close": 630.0,
            "ma20": 625.0,
            "ma50": 610.0,
            "pct_change_5d": 1.2,
        },
        "vix": {"level": 18.0, "pct_change_5d": -2.0},
        "events": {},
        "sectors": {},
        "prev_day": {},
        "premarket": {},
    }
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=_bars(), source="fixture")},
        regime,
    )

    candidate = result["candidates"][0]
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}
    readiness = {item["domain"]: item for item in candidate["readiness"]}
    regime_evidence = next(
        item for item in candidate["evidence"] if item["metric"] == "stored_regime"
    )

    assert candidate["research_state"] == "research_ready"
    assert gates["regime_allows_new_risk"]["status"] == "unknown"
    assert gates["research_evidence_ready"]["status"] == "passed"
    assert readiness["regime"]["state"] == "partial"
    assert regime_evidence["status"] == "neutral"
    assert regime_evidence["quality_state"] == "degraded"


def test_sorting_uses_state_then_support_count_then_ticker():
    histories = {
        "HIGH": DailyHistoryInput(bars=_bars(last_volume=3_000), source="fixture"),
        "LOW": DailyHistoryInput(bars=_bars(rising=False, last_volume=500), source="fixture"),
        "BLOCKED": DailyHistoryInput(bars=(), error="missing"),
    }

    result = _run(["BLOCKED", "LOW", "HIGH"], histories, _regime())

    assert [item["ticker"] for item in result["candidates"]] == ["HIGH", "LOW", "BLOCKED"]


def test_directional_structure_without_activity_stays_watch_only():
    result = _run(
        ["AAPL"],
        {
            "AAPL": DailyHistoryInput(
                bars=_bars(last_volume=500),
                source="fixture",
            )
        },
        _regime(),
    )

    candidate = result["candidates"][0]
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}

    assert candidate["research_state"] == "watch_only"
    assert candidate["directional_context"] == "bullish"
    assert gates["directional_structure_confirmed"]["status"] == "passed"
    assert gates["underlying_activity_confirmed"]["status"] == "failed"
    assert gates["research_evidence_ready"]["status"] == "unknown"
    assert "尚未同时确认" in gates["research_evidence_ready"]["reason"]


def test_mixed_structure_without_underlying_support_is_context_only():
    rows = _bars(last_volume=500)
    for row in rows:
        row.update(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
            }
        )
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=rows, source="fixture")},
        _regime(),
    )

    candidate = result["candidates"][0]
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}

    assert candidate["research_state"] == "context_only"
    assert candidate["directional_context"] == "mixed"
    assert gates["directional_structure_confirmed"]["status"] == "failed"
    assert gates["underlying_activity_confirmed"]["status"] == "failed"
    assert gates["research_evidence_ready"]["status"] == "failed"
    assert "仅保留市场背景" in gates["research_evidence_ready"]["reason"]


def test_missing_regime_does_not_hide_complete_underlying_evidence():
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=_bars(), source="fixture")},
        regime=None,
    )

    candidate = result["candidates"][0]
    regime_evidence = next(item for item in candidate["evidence"] if item["metric"] == "stored_regime")
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}
    assert candidate["research_state"] == "research_ready"
    assert candidate["style_match"]["status"] == "unknown"
    assert regime_evidence["status"] == "unknown"
    assert gates["regime_allows_new_risk"]["status"] == "unknown"
    assert gates["research_evidence_ready"]["status"] == "passed"


def test_stale_latest_bar_cannot_become_research_ready():
    result = _run(
        ["AAPL"],
        {
            "AAPL": DailyHistoryInput(
                bars=_bars(end=date(2026, 7, 10), count=25),
                source="fixture",
            )
        },
        _regime(),
    )

    candidate = result["candidates"][0]
    gates = {item["gate_id"]: item for item in candidate["hard_gates"]}
    daily_readiness = next(item for item in candidate["readiness"] if item["domain"] == "daily_history")
    assert candidate["research_state"] == "context_only"
    assert gates["latest_completed_daily_bar_freshness"]["status"] == "failed"
    assert daily_readiness["state"] == "stale"


def test_freshness_allows_four_calendar_days_but_rejects_five():
    common = {
        "symbols": ["AAPL"],
        "regime": _regime(),
        "as_of": datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc),
        "limit": 1,
    }
    four_days = build_daily_opportunity_run(
        histories={
            "AAPL": DailyHistoryInput(
                bars=_bars(end=date(2026, 7, 2), count=25), source="fixture"
            )
        },
        **common,
    )
    five_days = build_daily_opportunity_run(
        histories={
            "AAPL": DailyHistoryInput(
                bars=_bars(end=date(2026, 7, 1), count=25), source="fixture"
            )
        },
        **common,
    )

    four_gate = next(
        item
        for item in four_days["candidates"][0]["hard_gates"]
        if item["gate_id"] == "latest_completed_daily_bar_freshness"
    )
    five_gate = next(
        item
        for item in five_days["candidates"][0]["hard_gates"]
        if item["gate_id"] == "latest_completed_daily_bar_freshness"
    )
    assert four_gate["status"] == "passed"
    assert five_gate["status"] == "failed"


def test_mixed_non_us_universe_blocks_only_unsupported_candidate():
    result = _run(
        ["AAPL", "600519"],
        {
            "AAPL": DailyHistoryInput(bars=_bars(), source="fixture"),
            "600519": DailyHistoryInput(
                bars=(), error="unsupported_non_us_options_universe"
            ),
        },
        _regime(),
    )

    by_ticker = {item["ticker"]: item for item in result["candidates"]}
    assert by_ticker["AAPL"]["research_state"] == "research_ready"
    assert by_ticker["600519"]["research_state"] == "blocked"
    unsupported_gate = next(
        item
        for item in by_ticker["600519"]["hard_gates"]
        if item["gate_id"] == "us_options_universe"
    )
    universe_readiness = next(
        item
        for item in by_ticker["600519"]["readiness"]
        if item["domain"] == "universe"
    )
    assert unsupported_gate["status"] == "failed"
    assert universe_readiness["state"] == "unavailable"


def test_yfinance_amount_is_kept_as_derived_not_observed():
    rows = _bars()
    for row in rows:
        row["amount"] = row["close"] * row["volume"]
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=rows, source="YfinanceFetcher")},
        _regime(),
    )

    evidence = next(
        item
        for item in result["candidates"][0]["evidence"]
        if item["metric"] == "dollar_volume_vs_prior_20d_median"
    )
    assert evidence["quality_state"] == "derived"
    assert evidence["status"] == "neutral"
    assert result["candidates"][0]["supporting_evidence_count"] == 4
    assert "close × volume" in evidence["limitations"][0]


def test_reported_amount_ratio_is_derived_but_can_add_independent_support():
    rows = _bars()
    for index, row in enumerate(rows):
        row["amount"] = 500_000 if index == len(rows) - 1 else 100_000
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=rows, source="MoomooFetcher")},
        _regime(),
    )

    candidate = result["candidates"][0]
    evidence = next(
        item
        for item in candidate["evidence"]
        if item["metric"] == "dollar_volume_vs_prior_20d_median"
    )
    assert evidence["quality_state"] == "derived"
    assert evidence["status"] == "supports"
    assert candidate["supporting_evidence_count"] == 5


def test_mixed_reported_and_proxy_amount_inputs_do_not_add_support():
    rows = _bars()
    for index, row in enumerate(rows):
        row["amount"] = 500_000 if index == len(rows) - 1 else None
    result = _run(
        ["AAPL"],
        {"AAPL": DailyHistoryInput(bars=rows, source="MoomooFetcher")},
        _regime(),
    )

    candidate = result["candidates"][0]
    evidence = next(
        item
        for item in candidate["evidence"]
        if item["metric"] == "dollar_volume_vs_prior_20d_median"
    )
    assert evidence["quality_state"] == "derived"
    assert evidence["status"] == "neutral"
    assert candidate["supporting_evidence_count"] == 4

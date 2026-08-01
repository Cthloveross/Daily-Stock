from __future__ import annotations

from datetime import date, datetime, timezone

from src.opportunities.premarket import (
    assess_canonical_inputs,
    assess_regime_for_cycle,
    resolve_canonical_premarket_window,
)


UTC = timezone.utc


def _regime(
    generated_at: datetime,
    *,
    supporting_state: str = "ready",
) -> dict:
    domain_states = {
        "spy": "ready",
        "vix": "ready",
        "events": supporting_state,
        "sectors": supporting_state,
        "prev_day": supporting_state,
        "premarket": supporting_state,
    }
    return {
        "date": date(2026, 7, 23),
        "generated_at": generated_at,
        "snapshot": {"quality": {"domain_states": domain_states}},
    }


def _run(reference_session: str = "2026-07-22") -> dict:
    return {
        "market_date_et": "2026-07-23",
        "candidates": [
            {
                "ticker": "AAPL",
                "research_state": "watch_only",
                "reference_session_date": reference_session,
            }
        ],
    }


def test_canonical_window_uses_exact_xnys_boundaries():
    before = resolve_canonical_premarket_window(
        datetime(2026, 7, 23, 12, 44, 59, tzinfo=UTC)
    )
    at_start = resolve_canonical_premarket_window(
        datetime(2026, 7, 23, 12, 45, tzinfo=UTC)
    )
    before_end = resolve_canonical_premarket_window(
        datetime(2026, 7, 23, 13, 19, 59, tzinfo=UTC)
    )
    at_end = resolve_canonical_premarket_window(
        datetime(2026, 7, 23, 13, 20, tzinfo=UTC)
    )

    assert before.state == "before_window"
    assert at_start.state == before_end.state == "in_window"
    assert at_end.state == "after_window"
    assert at_start.previous_session == date(2026, 7, 22)
    assert at_start.regular_open_at == datetime(
        2026, 7, 23, 13, 30, tzinfo=UTC
    )
    assert at_start.window_start_at == datetime(
        2026, 7, 23, 12, 45, tzinfo=UTC
    )
    assert at_start.window_end_at == datetime(
        2026, 7, 23, 13, 20, tzinfo=UTC
    )


def test_canonical_window_does_not_map_weekend_to_another_session():
    result = resolve_canonical_premarket_window(
        datetime(2026, 7, 25, 13, 0, tzinfo=UTC)
    )

    assert result.state == "non_session"
    assert result.is_xnys_session is False
    assert result.previous_session is None


def test_regime_gate_requires_aware_causal_timestamp_and_ready_core():
    cycle_as_of = datetime(2026, 7, 23, 12, 55, tzinfo=UTC)
    ready = assess_regime_for_cycle(
        _regime(datetime(2026, 7, 23, 12, 54, tzinfo=UTC)),
        market_date_et=date(2026, 7, 23),
        cycle_as_of=cycle_as_of,
    )
    future = assess_regime_for_cycle(
        _regime(datetime(2026, 7, 23, 12, 56, tzinfo=UTC)),
        market_date_et=date(2026, 7, 23),
        cycle_as_of=cycle_as_of,
    )
    naive = assess_regime_for_cycle(
        _regime(datetime(2026, 7, 23, 12, 54)),
        market_date_et=date(2026, 7, 23),
        cycle_as_of=cycle_as_of,
    )
    missing_core = _regime(
        datetime(2026, 7, 23, 12, 54, tzinfo=UTC)
    )
    missing_core["snapshot"]["quality"]["domain_states"]["vix"] = "unavailable"
    core = assess_regime_for_cycle(
        missing_core,
        market_date_et=date(2026, 7, 23),
        cycle_as_of=cycle_as_of,
    )

    assert ready[0] == "ready"
    assert future[0] == naive[0] == core[0] == "blocked"
    assert "regime_generated_after_cycle_as_of" in future[1]
    assert "regime_generated_at_naive" in naive[1]
    assert "regime_core_vix_not_ready" in core[1]


def test_supporting_regime_absence_degrades_but_previous_session_is_mandatory():
    window = resolve_canonical_premarket_window(
        datetime(2026, 7, 23, 12, 55, tzinfo=UTC)
    )
    regime = _regime(
        datetime(2026, 7, 23, 12, 54, tzinfo=UTC),
        supporting_state="unavailable",
    )
    degraded = assess_canonical_inputs(
        _run(),
        regime,
        window=window,
        cycle_as_of=datetime(2026, 7, 23, 12, 55, tzinfo=UTC),
    )
    stale = assess_canonical_inputs(
        _run("2026-07-21"),
        regime,
        window=window,
        cycle_as_of=datetime(2026, 7, 23, 12, 55, tzinfo=UTC),
    )

    assert degraded.state == "degraded"
    assert degraded.fresh_candidate_count == 1
    assert stale.state == "blocked"
    assert "candidate_reference_session_mismatch:AAPL" in stale.reasons
    assert "no_fresh_candidates" in stale.reasons

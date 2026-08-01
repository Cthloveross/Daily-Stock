from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.opportunities.cycle_repository import append_research_universe
from src.opportunities.premarket import CANONICAL_SCOPE_KEY
from src.opportunities.qualification import (
    TRACK_CANONICAL_FULL_RESEARCH,
    TRACK_RAW_UNDERLYING_PATH,
    TRACK_UNDERLYING_DAILY_SELECTION,
)
from src.opportunities.qualification_repository import (
    get_snapshot_qualification,
)
from src.opportunities.repository import get_snapshot, list_snapshots
from src.services import opportunity_snapshot_service as snapshot_service
from src.services import premarket_research_service as service
from src.storage import DatabaseManager


UTC = timezone.utc


@pytest.fixture(autouse=True)
def reset_cycle_state():
    service._reset_premarket_cycle_state_for_tests()
    yield
    service._reset_premarket_cycle_state_for_tests()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "premarket_service.db"))

    import src.config as config_module

    monkeypatch.setattr(
        config_module.Config,
        "_parse_stock_email_groups",
        classmethod(lambda _cls: []),
    )
    config_module.Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    yield db
    DatabaseManager.reset_instance()
    config_module.Config.reset_instance()


class SequenceClock:
    def __init__(self, *values: datetime):
        self.values = list(values)
        self.last = values[-1]

    def __call__(self) -> datetime:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def _configure_universe(db, symbols=("AAPL",), limit=1):
    return append_research_universe(
        symbols,
        limit,
        scope_key=CANONICAL_SCOPE_KEY,
        source="test",
        created_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
        db_manager=db,
    )[0]


def _regime(*, core_ready: bool = True, supporting_ready: bool = True) -> dict:
    supporting = "ready" if supporting_ready else "unavailable"
    return {
        "date": date(2026, 7, 23),
        "generated_at": datetime(2026, 7, 23, 12, 50, 30, tzinfo=UTC),
        "score": 60,
        "label": "standard",
        "d1_direction": 25,
        "d2_volatility": 15,
        "d3_macro_penalty": 0,
        "d4_sector": 10,
        "d5_prev_day": 5,
        "d6_premarket": 5,
        "snapshot": {
            "spy": {
                "close": 630.0,
                "source_url": "https://provider.invalid/bars?token=secret",
                "api_token": "secret",
            },
            "vix": {"level": 17.0},
            "quality": {
                "domain_states": {
                    "spy": "ready",
                    "vix": "ready" if core_ready else "unavailable",
                    "events": supporting,
                    "sectors": supporting,
                    "prev_day": supporting,
                    "premarket": supporting,
                }
            }
        },
        "version": "v2",
    }


def _run(
    *,
    as_of: datetime,
    reference_session: str = "2026-07-22",
) -> dict:
    return {
        "schema_version": "1.1",
        "run_id": "opr_2026-07-23_aapl",
        "run_type": "morning_prior_close",
        "market_date_et": "2026-07-23",
        "as_of": as_of.isoformat(),
        "generated_at": as_of.isoformat(),
        "signal_version": "daily_completed_bars_v1",
        "ranking_method": "rule_based_evidence_count",
        "strategy_validation_state": "not_validated",
        "strategy_validation_message": "fixture",
        "universe": ["AAPL"],
        "requested_limit": 1,
        "candidate_count": 1,
        "run_readiness": [],
        "candidates": [
            {
                "candidate_id": "opc_aapl",
                "ticker": "AAPL",
                "research_state": "watch_only",
                "directional_context": "bullish",
                "setup_tags": [],
                "last_completed_bar_at": f"{reference_session}T20:00:00+00:00",
                "reference_session_date": reference_session,
                "reference_close": 100.0,
                "reference_price_basis": "prior_completed_close",
                "source": "fixture",
                "style_match": {
                    "status": "unknown",
                    "source": "fixture",
                    "matched_rules": [],
                    "conflicting_rules": [],
                    "unknown_fields": [],
                },
                "hard_gates": [],
                "evidence": [],
                "readiness": [],
                "supporting_evidence_count": 1,
                "data_completeness": {
                    "state": "complete",
                    "available_count": 7,
                    "expected_count": 7,
                },
                "unknowns": [],
            }
        ],
    }


def _bars(symbol: str):
    close = 630.0 if symbol == "SPY" else 100.0
    return (
        [
            {
                "date": "2026-07-22",
                "open": close - 1,
                "high": close + 1,
                "low": close - 2,
                "close": close,
                "volume": 1000,
            }
        ],
        "fixture",
    )


def _mature_bars(symbol: str):
    base = 630.0 if symbol == "SPY" else 100.0
    sessions = (
        "2026-07-22",
        "2026-07-23",
        "2026-07-24",
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
        "2026-07-30",
    )
    return (
        [
            {
                "date": session,
                "open": base + index,
                "high": base + index + 1,
                "low": base + index - 1,
                "close": base + index + 0.5,
                "volume": 1000,
            }
            for index, session in enumerate(sessions)
        ],
        "fixture",
    )


def _success_clock(
    *,
    publish_at: datetime | None = None,
    after_prepare_at: datetime | None = None,
    base: datetime = datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
) -> SequenceClock:
    guard_at = after_prepare_at or base.replace(second=40)
    return SequenceClock(
        base.replace(second=5),
        base.replace(second=10),
        base.replace(second=15),
        base.replace(second=20),
        base.replace(second=25),
        base.replace(second=30),
        publish_at or base.replace(second=35),
        guard_at,
        guard_at + timedelta(seconds=5),
        guard_at + timedelta(seconds=10),
    )


def test_ready_cycle_publishes_once_and_reuses_append_only_snapshot(isolated_db):
    _configure_universe(isolated_db)
    calls = {"regime": 0, "scan": 0}

    def load_regime(_market_date, _symbols, as_of):
        calls["regime"] += 1
        assert as_of == datetime(2026, 7, 23, 13, 12, 5, tzinfo=UTC)
        return _regime()

    def scan(_symbols, _limit, *, as_of, regime):
        calls["scan"] += 1
        assert regime["date"] == date(2026, 7, 23)
        run = _run(as_of=as_of)
        run["candidates"][0]["research_state"] = "research_ready"
        return run

    first = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=scan,
        now=datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=load_regime,
        clock=_success_clock(),
        history_loader=_bars,
    )
    replay = service.run_canonical_premarket_research(
        ["MSFT", "TSLA"],
        2,
        scan_runner=scan,
        now=datetime(2026, 7, 23, 13, 0, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=load_regime,
        clock=_success_clock(),
        history_loader=_bars,
    )

    assert first["state"] == "published"
    assert first["quality"] == "ready"
    assert first["snapshot"]["validation_eligible"] is True
    assert first["snapshot"]["full_research_candidate_count"] == 1
    assert first["snapshot"]["qualification"]["publication_state"] == (
        "canonical_published"
    )
    assert first["snapshot"]["qualification"]["analysis_quality_state"] == (
        "ready"
    )
    assert {
        item["track_key"]: item["qualified_count"]
        for item in first["snapshot"]["qualification"]["tracks"]
    } == {
        TRACK_RAW_UNDERLYING_PATH: 1,
        TRACK_UNDERLYING_DAILY_SELECTION: 1,
        TRACK_CANONICAL_FULL_RESEARCH: 1,
    }
    assert first["run"]["canonical_cycle"]["previous_session"] == "2026-07-22"
    assert first["run"]["canonical_cycle"]["learning_eligible"] is True
    assert replay["state"] == "published"
    assert replay["idempotent_replay"] is True
    assert replay["snapshot"]["snapshot_key"] == first["snapshot"]["snapshot_key"]
    assert replay["universe"] == ["AAPL"]
    assert replay["requested_limit"] == 1
    assert calls == {"regime": 1, "scan": 1}

    stored = get_snapshot(
        first["snapshot"]["snapshot_key"],
        db_manager=isolated_db,
    )
    assert stored is not None
    qualification = get_snapshot_qualification(
        first["snapshot"]["snapshot_key"],
        db_manager=isolated_db,
    )
    assert qualification is not None
    assert qualification.publication_state == "canonical_published"
    assert qualification.analysis_quality_state == "ready"
    assert {
        track.track_key: track.qualification_state
        for track in qualification.candidate_tracks
    } == {
        TRACK_RAW_UNDERLYING_PATH: "qualified",
        TRACK_UNDERLYING_DAILY_SELECTION: "qualified",
        TRACK_CANONICAL_FULL_RESEARCH: "qualified",
    }
    manifest = stored.payload["canonical_cycle"]["regime_manifest"]
    assert manifest["score"] == 60
    assert manifest["dimensions"]["d1_direction"] == 25
    assert manifest["snapshot"]["domains"]["spy"]["source_url"] == "[redacted]"
    assert manifest["snapshot"]["domains"]["spy"]["api_token"] == "[redacted]"
    assert (
        stored.payload["canonical_cycle"]["regime_manifest_sha256"]
        == service.canonical_sha256(manifest)
    )

    from src.regime.classifier import RegimeResult
    from src.regime.storage import save_regime_score

    save_regime_score(
        RegimeResult(
            date=date(2026, 7, 23),
            score=1,
            label="no_trade",
            action_hint="later mutable row",
            d1_direction=1,
            d2_volatility=0,
            d3_macro_penalty=0,
            d4_sector=0,
            d5_prev_day=0,
            d6_premarket=0,
            snapshot={},
            version="v2",
            generated_at=datetime(2026, 7, 23, 13, 5, tzinfo=UTC),
        )
    )
    after_regime_upsert = get_snapshot(
        first["snapshot"]["snapshot_key"],
        db_manager=isolated_db,
    )
    assert after_regime_upsert is not None
    assert (
        after_regime_upsert.payload["canonical_cycle"]["regime_manifest"]["score"]
        == 60
    )
    assert (
        after_regime_upsert.payload["canonical_cycle"]["regime_manifest_sha256"]
        == stored.payload["canonical_cycle"]["regime_manifest_sha256"]
    )


def test_degraded_cycle_is_audited_but_excluded_from_learning(isolated_db):
    _configure_universe(isolated_db)

    def scan(_symbols, _limit, *, as_of, regime):
        run = _run(as_of=as_of)
        run["candidates"][0]["research_state"] = "research_ready"
        return run

    result = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=scan,
        now=datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: _regime(supporting_ready=False),
        clock=_success_clock(),
        history_loader=_bars,
    )
    stored = get_snapshot(
        result["snapshot"]["snapshot_key"],
        db_manager=isolated_db,
    )
    qualification = get_snapshot_qualification(
        result["snapshot"]["snapshot_key"],
        db_manager=isolated_db,
    )

    assert result["state"] == "published"
    assert result["quality"] == "degraded"
    assert result["snapshot"]["validation_eligible"] is False
    assert result["snapshot"]["eligible_candidate_count"] == 0
    assert result["snapshot"]["underlying_path_candidate_count"] == 1
    assert result["snapshot"]["full_research_candidate_count"] == 0
    assert [
        item["pending_count"]
        for item in result["snapshot"]["underlying_path_progress"]
    ] == [1, 1]
    assert result["snapshot"]["qualification"]["publication_state"] == (
        "canonical_published"
    )
    assert result["snapshot"]["qualification"]["analysis_quality_state"] == (
        "degraded"
    )
    assert {
        item["track_key"]: item["qualified_count"]
        for item in result["snapshot"]["qualification"]["tracks"]
    } == {
        TRACK_RAW_UNDERLYING_PATH: 1,
        TRACK_UNDERLYING_DAILY_SELECTION: 1,
        TRACK_CANONICAL_FULL_RESEARCH: 0,
    }
    assert result["run"]["canonical_cycle"]["learning_eligible"] is False
    assert stored is not None
    assert qualification is not None
    assert qualification.publication_state == "canonical_published"
    assert qualification.analysis_quality_state == "degraded"
    assert {
        track.track_key: track.qualification_state
        for track in qualification.candidate_tracks
    } == {
        TRACK_RAW_UNDERLYING_PATH: "qualified",
        TRACK_UNDERLYING_DAILY_SELECTION: "qualified",
        TRACK_CANONICAL_FULL_RESEARCH: "excluded",
    }
    assert stored.payload["snapshot_meta"]["learning_eligible"] is False
    assert stored.candidates[0].validation_eligible is False
    assert (
        stored.candidates[0].payload["outcome_validation"]["learning_eligible"]
        is False
    )


def test_degraded_canonical_snapshot_tracks_raw_path_without_entering_learning(
    isolated_db,
):
    _configure_universe(isolated_db)

    def scan(_symbols, _limit, *, as_of, regime):
        run = _run(as_of=as_of)
        run["candidates"][0]["research_state"] = "research_ready"
        return run

    published = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=scan,
        now=datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: _regime(supporting_ready=False),
        clock=_success_clock(),
        history_loader=_bars,
    )
    evaluated = snapshot_service.evaluate_snapshot(
        published["snapshot"]["snapshot_key"],
        db_manager=isolated_db,
        evaluated_at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
        history_loader=_mature_bars,
    )
    learning = snapshot_service.learning_summary(
        db_manager=isolated_db,
        generated_at=datetime(2026, 7, 31, 21, 1, tzinfo=UTC),
    )

    assert evaluated["tracking_candidate_count"] == 1
    assert evaluated["inserted_outcomes"] == 1
    assert evaluated["outcome_progress"][0]["mature_count"] == 0
    assert evaluated["underlying_path_progress"][0]["mature_count"] == 1
    assert evaluated["full_research_progress"][0]["mature_count"] == 0
    assert learning["horizons"][0]["mature_count"] == 0
    assert learning["horizons"][0]["directional_sample_count"] == 0


def test_before_window_is_observable_and_does_not_start_work(isolated_db):
    _configure_universe(isolated_db)
    calls = []

    result = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=lambda *_args, **_kwargs: calls.append("scan"),
        now=datetime(2026, 7, 23, 12, 44, 59, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: calls.append("regime"),
    )

    assert result["state"] == "waiting_window"
    assert calls == []


def test_latest_start_boundary_rejects_new_work_two_minutes_before_hard_end(
    isolated_db,
):
    _configure_universe(isolated_db)
    calls = []

    result = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=lambda *_args, **_kwargs: calls.append("scan"),
        now=datetime(2026, 7, 23, 13, 18, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: calls.append("regime"),
    )

    assert result["state"] == "window_closed"
    assert result["error_code"] == "latest_start_passed"
    assert result["latest_start_at"] == "2026-07-23T13:18:00+00:00"
    assert result["window_end_at"] == "2026-07-23T13:20:00+00:00"
    assert calls == []


def test_blocked_attempt_remains_visible_and_run_can_retry(isolated_db):
    _configure_universe(isolated_db)
    calls = {"regime": 0, "universes": []}

    def load_regime(_market_date, symbols, _as_of):
        calls["regime"] += 1
        calls["universes"].append(tuple(symbols))
        return _regime(core_ready=False)

    first = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=lambda *_args, **_kwargs: pytest.fail("scan must not run"),
        now=datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=load_regime,
        clock=SequenceClock(
            datetime(2026, 7, 23, 13, 12, 5, tzinfo=UTC),
            datetime(2026, 7, 23, 13, 12, 10, tzinfo=UTC),
            datetime(2026, 7, 23, 13, 12, 15, tzinfo=UTC),
        ),
    )
    append_research_universe(
        ("MSFT",),
        1,
        scope_key=CANONICAL_SCOPE_KEY,
        source="test",
        created_at=datetime(2026, 7, 23, 13, 13, tzinfo=UTC),
        db_manager=isolated_db,
    )
    service._reset_premarket_cycle_state_for_tests()
    status = service.status_canonical_premarket_research(
        ["MSFT"],
        1,
        now=datetime(2026, 7, 23, 13, 15, tzinfo=UTC),
        db_manager=isolated_db,
    )
    retry = service.run_canonical_premarket_research(
        ["MSFT"],
        1,
        scan_runner=lambda *_args, **_kwargs: pytest.fail("scan must not run"),
        now=datetime(2026, 7, 23, 13, 17, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=load_regime,
        clock=SequenceClock(
            datetime(2026, 7, 23, 13, 17, 5, tzinfo=UTC),
            datetime(2026, 7, 23, 13, 17, 10, tzinfo=UTC),
            datetime(2026, 7, 23, 13, 17, 15, tzinfo=UTC),
        ),
    )

    assert first["state"] == status["state"] == retry["state"] == "blocked"
    assert status["error_code"] == "regime_gate_blocked"
    assert calls["regime"] == 2
    assert calls["universes"] == [("AAPL",), ("AAPL",)]
    assert retry["universe"] == ["AAPL"]


def test_stale_candidate_and_late_completion_never_publish(isolated_db):
    _configure_universe(isolated_db)
    stale = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=lambda _symbols, _limit, *, as_of, regime: _run(
            as_of=as_of,
            reference_session="2026-07-21",
        ),
        now=datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: _regime(),
        clock=_success_clock(),
        history_loader=_bars,
    )

    service._reset_premarket_cycle_state_for_tests()
    late = service.run_canonical_premarket_research(
        ["MSFT"],
        1,
        scan_runner=lambda _symbols, _limit, *, as_of, regime: {
            **_run(as_of=as_of),
            "run_id": "opr_2026-07-23_msft",
            "universe": ["MSFT"],
            "candidates": [
                {
                    **_run(as_of=as_of)["candidates"][0],
                    "candidate_id": "opc_msft",
                    "ticker": "MSFT",
                }
            ],
        },
        now=datetime(2026, 7, 23, 13, 17, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: _regime(),
        clock=_success_clock(
            publish_at=datetime(2026, 7, 23, 13, 19, tzinfo=UTC),
            after_prepare_at=datetime(2026, 7, 23, 13, 20, tzinfo=UTC),
            base=datetime(2026, 7, 23, 13, 17, tzinfo=UTC),
        ),
        history_loader=_bars,
    )

    assert stale["state"] == "blocked"
    assert "candidate_reference_session_mismatch:AAPL" in stale[
        "quality_reasons"
    ]
    assert late["state"] == "window_closed"
    assert late["snapshot"] is None


def test_snapshot_factory_crossing_hard_deadline_rolls_back_publication(
    isolated_db,
):
    _configure_universe(isolated_db)

    result = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=lambda _symbols, _limit, *, as_of, regime: _run(
            as_of=as_of,
        ),
        now=datetime(2026, 7, 23, 13, 17, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: _regime(),
        clock=_success_clock(
            publish_at=datetime(2026, 7, 23, 13, 19, 50, tzinfo=UTC),
            after_prepare_at=datetime(2026, 7, 23, 13, 19, 59, tzinfo=UTC),
            base=datetime(2026, 7, 23, 13, 18, tzinfo=UTC),
        ),
        history_loader=_bars,
    )

    assert result["state"] == "window_closed"
    assert result["error_code"] == "publish_window_closed"
    assert result["snapshot"] is None
    assert list_snapshots(db_manager=isolated_db) == ()


def test_missing_persisted_pool_never_calls_providers(isolated_db):
    calls = []

    result = service.run_canonical_premarket_research(
        ["AAPL"],
        1,
        scan_runner=lambda *_args, **_kwargs: calls.append("scan"),
        now=datetime(2026, 7, 23, 13, 12, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: calls.append("regime"),
    )

    assert result["state"] == "research_pool_missing"
    assert result["universe"] == []
    assert result["error_code"] == "research_pool_missing"
    assert calls == []


def test_manual_run_cannot_start_before_primary_due_time(isolated_db):
    _configure_universe(isolated_db)
    calls = []

    result = service.run_canonical_premarket_research(
        ["MSFT"],
        5,
        scan_runner=lambda *_args, **_kwargs: calls.append("scan"),
        now=datetime(2026, 7, 23, 13, 11, 59, tzinfo=UTC),
        db_manager=isolated_db,
        regime_loader=lambda *_args: calls.append("regime"),
        trigger="manual",
    )

    assert result["state"] == "waiting_window"
    assert result["next_scheduled_at"] == "2026-07-23T13:12:00+00:00"
    assert calls == []

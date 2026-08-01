from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.opportunities.repository import (
    StoredOutcome,
    StoredSnapshot,
    StoredSnapshotCandidate,
    list_snapshots,
)
from src.services import opportunity_snapshot_service as service
from src.storage import DatabaseManager


UTC = timezone.utc


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "opportunity_service.db"))

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


def _run(symbol: str = "AAPL") -> dict:
    return {
        "schema_version": "1.1",
        "run_id": f"opr_2026-07-22_{symbol.lower()}",
        "run_type": "morning_prior_close",
        "market_date_et": "2026-07-22",
        "as_of": "2026-07-22T13:00:00+00:00",
        "generated_at": "2026-07-22T13:00:00+00:00",
        "signal_version": "daily_completed_bars_v1",
        "ranking_method": "rule_based_evidence_count",
        "strategy_validation_state": "not_validated",
        "universe": [symbol],
        "requested_limit": 1,
        "candidate_count": 1,
        "run_readiness": [],
        "candidates": [
            {
                "candidate_id": f"opc_{symbol.lower()}",
                "ticker": symbol,
                "research_state": "watch_only",
                "directional_context": "bullish",
                "setup_tags": ["close_above_ema8_above_ema13"],
                "last_completed_bar_at": "2026-07-21T16:00:00-04:00",
                "reference_session_date": "2026-07-21",
                "reference_close": 100.0,
                "reference_price_basis": "prior_completed_close",
                "source": "fixture",
                "style_match": {"status": "unknown"},
                "hard_gates": [],
                "evidence": [],
                "readiness": [],
                "supporting_evidence_count": 3,
                "data_completeness": {
                    "state": "complete",
                    "available_count": 7,
                    "expected_count": 7,
                },
                "unknowns": [],
            }
        ],
    }


def _bars(symbol: str) -> tuple[list[dict], str]:
    closes = {
        date(2026, 7, 21): 100.0 if symbol != "SPY" else 630.0,
        date(2026, 7, 22): 101.0 if symbol != "SPY" else 631.0,
        date(2026, 7, 23): 102.0 if symbol != "SPY" else 632.0,
        date(2026, 7, 24): 103.0 if symbol != "SPY" else 633.0,
        date(2026, 7, 27): 104.0 if symbol != "SPY" else 634.0,
        date(2026, 7, 28): 106.0 if symbol != "SPY" else 636.0,
    }
    return (
        [
            {
                "date": session.isoformat(),
                "open": close - 0.25,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000,
            }
            for session, close in closes.items()
        ],
        "fixture",
    )


def test_freeze_is_premarket_eligible_and_same_slot_is_idempotent(isolated_db):
    first = service.freeze_daily_snapshot(
        _run(),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 5, tzinfo=UTC),
        history_loader=_bars,
    )
    replay = service.freeze_daily_snapshot(
        _run(),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 14, 0, tzinfo=UTC),
        history_loader=_bars,
    )

    assert first["validation_eligible"] is True
    assert first["analysis_quality_eligible"] is True
    assert first["analysis_quality_reasons"] == []
    assert first["eligible_candidate_count"] == 1
    assert first["underlying_path_candidate_count"] == 1
    assert first["full_research_candidate_count"] == 0
    assert [item["eligible_count"] for item in first["full_research_progress"]] == [
        0,
        0,
    ]
    assert first["idempotent_replay"] is False
    assert replay["snapshot_key"] == first["snapshot_key"]
    assert replay["idempotent_replay"] is True


def test_snapshot_projects_analysis_quality_separately_from_causal_eligibility(
    isolated_db,
):
    result = service.freeze_daily_snapshot(
        _run("GOOGL"),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 5, tzinfo=UTC),
        history_loader=_bars,
        analysis_quality_eligible=False,
        analysis_quality_reasons=(
            "regime_supporting_events_degraded",
            "regime_supporting_premarket_degraded",
        ),
    )

    assert result["analysis_quality_eligible"] is False
    assert result["analysis_quality_reasons"] == [
        "regime_supporting_events_degraded",
        "regime_supporting_premarket_degraded",
    ]
    assert result["validation_eligible"] is False
    assert result["eligible_candidate_count"] == 0


def test_legacy_snapshot_quality_is_unknown_instead_of_assumed_ready(
    isolated_db,
):
    result = service.freeze_daily_snapshot(
        _run("META"),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 5, tzinfo=UTC),
        history_loader=_bars,
    )
    stored = list_snapshots(db_manager=isolated_db)[0]
    # Simulate the immutable payload shape produced before quality metadata
    # existed. The projection must fail closed instead of treating absence as
    # proof that the historical research bundle was complete.
    legacy_payload = dict(stored.payload)
    legacy_meta = dict(legacy_payload.get("snapshot_meta") or {})
    legacy_meta.pop("analysis_quality_eligible", None)
    legacy_meta.pop("analysis_quality_reasons", None)
    legacy_payload["snapshot_meta"] = legacy_meta
    legacy = replace(stored, payload=legacy_payload)

    item = service.snapshot_item(legacy, db_manager=isolated_db)

    assert result["analysis_quality_eligible"] is True
    assert item["analysis_quality_eligible"] is None
    assert item["analysis_quality_reasons"] == []


def test_freeze_after_entry_open_is_saved_but_excluded(isolated_db):
    result = service.freeze_daily_snapshot(
        _run("MSFT"),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 14, 0, tzinfo=UTC),
        history_loader=_bars,
    )

    assert result["validation_eligible"] is False
    assert result["eligible_candidate_count"] == 0
    assert "not_frozen_before_entry_open" in result["eligibility_reasons"]


def test_freeze_publish_guard_fails_before_append_at_hard_deadline(isolated_db):
    with pytest.raises(service.SnapshotPublishWindowClosedError):
        service.freeze_daily_snapshot(
            _run("NVDA"),
            db_manager=isolated_db,
            frozen_at=datetime(2026, 7, 22, 13, 19, tzinfo=UTC),
            benchmark_context=({}, None),
            publish_deadline=datetime(2026, 7, 22, 13, 20, tzinfo=UTC),
            publish_guard_clock=lambda: datetime(
                2026, 7, 22, 13, 20, tzinfo=UTC
            ),
        )

    assert list_snapshots(db_manager=isolated_db) == ()


def test_ensure_daily_snapshot_saves_only_official_premarket_slot(isolated_db):
    first = service.ensure_daily_snapshot(
        _run(),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 5, tzinfo=UTC),
        history_loader=_bars,
    )
    second = service.ensure_daily_snapshot(
        _run(),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 15, tzinfo=UTC),
        history_loader=_bars,
    )

    assert first["state"] == "saved"
    assert first["snapshot"]["validation_eligible"] is True
    assert second["state"] == "existing"
    assert second["snapshot"]["snapshot_key"] == first["snapshot"]["snapshot_key"]


def test_ensure_daily_snapshot_does_not_write_after_open(isolated_db):
    result = service.ensure_daily_snapshot(
        _run("MSFT"),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 14, 0, tzinfo=UTC),
        history_loader=_bars,
    )

    assert result["state"] == "outside_window"
    assert result["snapshot"] is None
    assert service.list_snapshot_items(limit=10, db_manager=isolated_db)["items"] == []


def test_ensure_daily_snapshot_does_not_create_weekend_slot(isolated_db):
    run = _run("NVDA")
    run["market_date_et"] = "2026-07-25"
    run["as_of"] = "2026-07-25T16:00:00+00:00"
    run["generated_at"] = run["as_of"]
    candidate = run["candidates"][0]
    candidate["reference_session_date"] = "2026-07-24"
    candidate["last_completed_bar_at"] = "2026-07-24T16:00:00-04:00"

    result = service.ensure_daily_snapshot(
        run,
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 25, 16, 0, tzinfo=UTC),
        history_loader=_bars,
    )

    assert result["state"] == "outside_window"
    assert service.list_snapshot_items(limit=10, db_manager=isolated_db)["items"] == []


def test_evaluation_appends_only_mature_target_close(isolated_db):
    frozen = service.freeze_daily_snapshot(
        _run(),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 5, tzinfo=UTC),
        history_loader=_bars,
    )
    pending = service.evaluate_snapshot(
        frozen["snapshot_key"],
        db_manager=isolated_db,
        evaluated_at=datetime(2026, 7, 22, 13, 10, tzinfo=UTC),
        history_loader=_bars,
    )
    mature = service.evaluate_snapshot(
        frozen["snapshot_key"],
        db_manager=isolated_db,
        evaluated_at=datetime(2026, 7, 28, 21, 0, tzinfo=UTC),
        history_loader=_bars,
    )

    assert pending["inserted_outcomes"] == 0
    assert pending["pending_horizons"] == 2
    assert mature["inserted_outcomes"] == 1
    five_day = next(
        item for item in mature["outcome_progress"] if item["horizon_sessions"] == 5
    )
    twenty_day = next(
        item for item in mature["outcome_progress"] if item["horizon_sessions"] == 20
    )
    assert five_day["mature_count"] == 1
    assert twenty_day["mature_count"] == 0


def test_due_maintenance_preflight_makes_zero_provider_calls_before_target(
    isolated_db,
):
    service.freeze_daily_snapshot(
        _run(),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 5, tzinfo=UTC),
        history_loader=_bars,
    )
    calls = []

    result = service.evaluate_due_snapshots(
        db_manager=isolated_db,
        evaluated_at=datetime(2026, 7, 23, 21, 0, tzinfo=UTC),
        history_loader=lambda symbol: calls.append(symbol) or _bars(symbol),
    )

    assert result["due_snapshot_count"] == 0
    assert result["evaluated_snapshot_count"] == 0
    assert calls == []


def test_due_maintenance_reuses_symbol_histories_across_snapshots(monkeypatch):
    snapshots = (_stored_snapshot(0), _stored_snapshot(1))
    calls: list[str] = []

    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: snapshots,
    )
    monkeypatch.setattr(
        service,
        "snapshot_item",
        lambda *_args, **_kwargs: {
            "outcome_progress": [
                {
                    "horizon_sessions": 5,
                    "data_gap_count": 1,
                    "partial_count": 0,
                }
            ]
        },
    )

    def evaluate(snapshot_key: str, *, history_loader, **_kwargs):
        history_loader("AAPL")
        history_loader("SPY")
        return {
            "snapshot_key": snapshot_key,
            "inserted_outcomes": 1,
            "already_recorded": 0,
            "pending_horizons": 1,
            "data_gap_horizons": 0,
        }

    monkeypatch.setattr(service, "evaluate_snapshot", evaluate)

    result = service.evaluate_due_snapshots(
        evaluated_at=datetime(2026, 4, 1, 21, 0, tzinfo=UTC),
        history_loader=lambda symbol: calls.append(symbol) or _bars(symbol),
    )

    assert result["evaluated_snapshot_count"] == 2
    assert result["inserted_outcomes"] == 2
    assert calls == ["AAPL", "SPY"]


def _stored_outcome(
    index: int,
    *,
    label: str = "CONTEXT_HIT",
    result_state: str = "complete",
    data_gaps: tuple[str, ...] = (),
) -> StoredOutcome:
    signal = date(2026, 1, 2) + timedelta(days=index)
    return StoredOutcome(
        id=index + 1,
        snapshot_key=f"ops_{index}",
        market_date_et=signal + timedelta(days=1),
        candidate_key=f"opc_{index}",
        ticker=f"T{index}",
        rank=1,
        directional_context="bullish",
        horizon_sessions=5,
        evaluator_version=service.FORMULA_VERSION,
        result_state=result_state,
        context_label=label,
        result_sha256=f"{index:064x}",
        evaluated_at=datetime(2026, 4, 1, tzinfo=UTC),
        result={
            "reference_session_date": signal.isoformat(),
            "provenance": {"horizon": {"data_gaps": list(data_gaps)}},
        },
    )


def _stored_snapshot(
    index: int,
    *,
    signal_version: str = "daily_completed_bars_v1",
) -> StoredSnapshot:
    signal = date(2026, 1, 2) + timedelta(days=index)
    candidate = StoredSnapshotCandidate(
        id=index + 1,
        candidate_key=f"opc_{index}",
        ticker=f"T{index}",
        rank=1,
        research_state="watch_only",
        directional_context="bullish",
        supporting_evidence_count=3,
        data_completeness_state="complete",
        reference_session_date=signal,
        reference_close=Decimal("100"),
        reference_price_basis="prior_completed_close",
        reference_source="fixture",
        validation_eligible=True,
        eligibility_reasons=(),
        payload={
            "setup_tags": ["close_above_ema8_above_ema13"],
            "evidence": [
                {
                    "metric": "stored_regime",
                    "value": {"label": "standard"},
                }
            ],
        },
    )
    return StoredSnapshot(
        id=index + 1,
        snapshot_key=f"ops_{index}",
        market_date_et=signal + timedelta(days=1),
        run_type="morning_prior_close",
        scope_key=service.SCOPE_KEY,
        signal_version=signal_version,
        schema_version="1.1",
        freeze_policy_version=service.FREEZE_POLICY_VERSION,
        playbook_version=service.PLAYBOOK_VERSION,
        source_run_id=f"opr_{index}",
        requested_limit=10,
        ranking_method="rule_based_evidence_count",
        strategy_validation_state="not_validated",
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        frozen_at=datetime(2026, 1, 2, tzinfo=UTC),
        validation_eligible=True,
        eligibility_reasons=(),
        payload_sha256=f"{index + 100:064x}",
        payload={"universe": [f"T{item}" for item in range(20)]},
        candidates=(candidate,),
    )


def _allow_all_qualification_tracks(monkeypatch):
    monkeypatch.setattr(
        service,
        "_qualified_track_candidate_keys",
        lambda snapshot, **_kwargs: {
            candidate.candidate_key for candidate in snapshot.candidates
        },
    )


def test_learning_summary_hides_small_samples_and_never_auto_adjusts(monkeypatch):
    _allow_all_qualification_tracks(monkeypatch)
    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: tuple(_stored_snapshot(index) for index in range(9)),
    )
    monkeypatch.setattr(
        service,
        "list_candidate_outcomes",
        lambda **_kwargs: tuple(_stored_outcome(index) for index in range(9)),
    )
    small = service.learning_summary(generated_at=datetime(2026, 4, 1, tzinfo=UTC))
    assert small["horizons"][0]["context_hit_rate_percent"] is None
    assert small["auto_adjustment"] is False

    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: tuple(_stored_snapshot(index) for index in range(20)),
    )
    monkeypatch.setattr(
        service,
        "list_candidate_outcomes",
        lambda **_kwargs: tuple(_stored_outcome(index) for index in range(20)),
    )
    ready = service.learning_summary(generated_at=datetime(2026, 4, 1, tzinfo=UTC))
    assert ready["horizons"][0]["context_hit_rate_percent"] == 100.0
    assert ready["horizons"][0]["investigation_ready"] is True
    assert ready["auto_adjustment"] is False


def test_learning_summary_does_not_mix_signal_version_cohorts(monkeypatch):
    _allow_all_qualification_tracks(monkeypatch)
    current_snapshots = tuple(
        _stored_snapshot(index, signal_version="signal_v2")
        for index in range(20, 40)
    )
    legacy_snapshots = tuple(
        _stored_snapshot(index, signal_version="signal_v1")
        for index in range(20)
    )
    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: (*current_snapshots, *legacy_snapshots),
    )
    monkeypatch.setattr(
        service,
        "list_candidate_outcomes",
        lambda **_kwargs: (
            *(_stored_outcome(index) for index in range(20, 40)),
            *(
                _stored_outcome(index, label="CONTEXT_MISS")
                for index in range(20)
            ),
        ),
    )

    summary = service.learning_summary(
        generated_at=datetime(2026, 4, 1, tzinfo=UTC)
    )
    five_day = summary["horizons"][0]

    assert five_day["mature_count"] == 20
    assert five_day["context_hit_count"] == 20
    assert five_day["context_miss_count"] == 0
    assert five_day["context_hit_rate_percent"] == 100.0


def test_learning_summary_excludes_critical_reference_quality_gaps(monkeypatch):
    _allow_all_qualification_tracks(monkeypatch)
    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: tuple(_stored_snapshot(index) for index in range(22)),
    )
    monkeypatch.setattr(
        service,
        "list_candidate_outcomes",
        lambda **_kwargs: (
            *(_stored_outcome(index) for index in range(20)),
            _stored_outcome(
                20,
                label="CONTEXT_MISS",
                result_state="partial",
                data_gaps=("reference_revision_mismatch",),
            ),
            _stored_outcome(
                21,
                label="CONTEXT_MISS",
                result_state="partial",
                data_gaps=("reference_close_refetch_missing",),
            ),
        ),
    )

    summary = service.learning_summary(
        generated_at=datetime(2026, 4, 1, tzinfo=UTC)
    )
    five_day = summary["horizons"][0]

    assert five_day["mature_count"] == 20
    assert five_day["excluded_quality_count"] == 2
    assert five_day["context_hit_count"] == 20
    assert five_day["context_miss_count"] == 0
    assert five_day["context_hit_rate_percent"] == 100.0


def test_learning_summary_requires_twenty_distinct_signal_sessions(monkeypatch):
    _allow_all_qualification_tracks(monkeypatch)
    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: tuple(_stored_snapshot(index) for index in range(20)),
    )
    repeated_session = "2026-01-02"
    monkeypatch.setattr(
        service,
        "list_candidate_outcomes",
        lambda **_kwargs: tuple(
            replace(
                _stored_outcome(index),
                result={
                    "reference_session_date": repeated_session,
                    "provenance": {"horizon": {"data_gaps": []}},
                },
            )
            for index in range(20)
        ),
    )

    summary = service.learning_summary(
        generated_at=datetime(2026, 4, 1, tzinfo=UTC)
    )
    five_day = summary["horizons"][0]

    assert five_day["directional_sample_count"] == 20
    assert five_day["distinct_signal_sessions"] == 1
    assert five_day["summary_visible"] is False
    assert five_day["context_hit_count"] is None
    assert five_day["context_hit_rate_percent"] is None


def test_learning_summary_fails_closed_without_full_research_qualification(
    monkeypatch,
    isolated_db,
):
    snapshots = tuple(_stored_snapshot(index) for index in range(20))
    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: snapshots,
    )
    monkeypatch.setattr(
        service,
        "list_candidate_outcomes",
        lambda **_kwargs: tuple(
            _stored_outcome(index) for index in range(20)
        ),
    )

    summary = service.learning_summary(
        db_manager=isolated_db,
        generated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )

    five_day = summary["horizons"][0]
    assert five_day["mature_count"] == 0
    assert five_day["directional_sample_count"] == 0
    assert five_day["context_hit_rate_percent"] is None


def test_qualification_lookup_error_fails_closed_for_every_track(
    monkeypatch,
):
    import src.opportunities.qualification_repository as repository

    def fail_lookup(*_args, **_kwargs):
        raise RuntimeError("qualification store unavailable")

    monkeypatch.setattr(
        repository,
        "get_snapshot_qualification",
        fail_lookup,
    )
    snapshot = _stored_snapshot(0)

    assert service._qualified_track_candidate_keys(
        snapshot,
        track_key="raw_underlying_path_v1",
    ) == set()
    assert service._qualified_track_candidate_keys(
        snapshot,
        track_key="canonical_full_research_v1",
    ) == set()


def test_due_horizon_without_outcome_is_data_gap_not_pending(isolated_db):
    def history_without_target(symbol: str):
        rows, source = _bars(symbol)
        return [row for row in rows if row["date"] != "2026-07-28"], source

    frozen = service.freeze_daily_snapshot(
        _run(),
        db_manager=isolated_db,
        frozen_at=datetime(2026, 7, 22, 13, 5, tzinfo=UTC),
        history_loader=history_without_target,
    )
    evaluated = service.evaluate_snapshot(
        frozen["snapshot_key"],
        db_manager=isolated_db,
        evaluated_at=datetime(2026, 7, 28, 21, 0, tzinfo=UTC),
        history_loader=history_without_target,
    )

    five_day = next(
        item
        for item in evaluated["outcome_progress"]
        if item["horizon_sessions"] == 5
    )
    twenty_day = next(
        item
        for item in evaluated["outcome_progress"]
        if item["horizon_sessions"] == 20
    )
    assert evaluated["inserted_outcomes"] == 0
    assert evaluated["data_gap_horizons"] == 1
    assert evaluated["pending_horizons"] == 1
    assert five_day["data_gap_count"] == 1
    assert five_day["pending_count"] == 0
    assert twenty_day["data_gap_count"] == 0
    assert twenty_day["pending_count"] == 1

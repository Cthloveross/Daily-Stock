from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.opportunities.repository import (
    StoredOutcome,
    StoredSnapshot,
    StoredSnapshotCandidate,
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
    assert first["eligible_candidate_count"] == 1
    assert first["idempotent_replay"] is False
    assert replay["snapshot_key"] == first["snapshot_key"]
    assert replay["idempotent_replay"] is True


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


def test_learning_summary_hides_small_samples_and_never_auto_adjusts(monkeypatch):
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
    current_snapshots = tuple(
        _stored_snapshot(index, signal_version="signal_v2")
        for index in range(10, 20)
    )
    legacy_snapshots = tuple(
        _stored_snapshot(index, signal_version="signal_v1")
        for index in range(10)
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
            *(_stored_outcome(index) for index in range(10, 20)),
            *(
                _stored_outcome(index, label="CONTEXT_MISS")
                for index in range(10)
            ),
        ),
    )

    summary = service.learning_summary(
        generated_at=datetime(2026, 4, 1, tzinfo=UTC)
    )
    five_day = summary["horizons"][0]

    assert five_day["mature_count"] == 10
    assert five_day["context_hit_count"] == 10
    assert five_day["context_miss_count"] == 0
    assert five_day["context_hit_rate_percent"] == 100.0


def test_learning_summary_excludes_critical_reference_quality_gaps(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_snapshots",
        lambda **_kwargs: tuple(_stored_snapshot(index) for index in range(12)),
    )
    monkeypatch.setattr(
        service,
        "list_candidate_outcomes",
        lambda **_kwargs: (
            *(_stored_outcome(index) for index in range(10)),
            _stored_outcome(
                10,
                label="CONTEXT_MISS",
                result_state="partial",
                data_gaps=("reference_revision_mismatch",),
            ),
            _stored_outcome(
                11,
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

    assert five_day["mature_count"] == 10
    assert five_day["excluded_quality_count"] == 2
    assert five_day["context_hit_count"] == 10
    assert five_day["context_miss_count"] == 0
    assert five_day["context_hit_rate_percent"] == 100.0


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

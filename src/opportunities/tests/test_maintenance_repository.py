from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest

from src.opportunities.maintenance_repository import (
    OUTCOME_MAINTENANCE_POLICY_VERSION,
    OutcomeMaintenanceLeaseError,
    claim_outcome_maintenance,
    get_latest_outcome_maintenance,
    settle_outcome_maintenance,
)
from src.storage import DatabaseManager


UTC = timezone.utc
SESSION = date(2026, 7, 23)
NOW = datetime(2026, 7, 23, 20, 30, tzinfo=UTC)


def test_policy_version_tracks_prospective_qualified_raw_paths():
    assert (
        OUTCOME_MAINTENANCE_POLICY_VERSION
        == "xnys-close-qualified-raw-path-v2"
    )


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PATH",
        str(tmp_path / "outcome_maintenance.db"),
    )

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


def test_claim_is_single_owner_and_completed_slot_is_terminal(isolated_db):
    first = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-a",
        now=NOW,
        db_manager=isolated_db,
    )
    contender = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-b",
        now=NOW,
        db_manager=isolated_db,
    )

    assert first.claimed is True
    assert contender.claimed is False
    assert contender.run.owner_key == "worker-a"

    completed = settle_outcome_maintenance(
        first.run.maintenance_key,
        owner_key="worker-a",
        state="completed",
        result={"due_snapshot_count": 0, "inserted_outcomes": 0},
        now=NOW + timedelta(minutes=1),
        db_manager=isolated_db,
    )
    replay = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-b",
        now=NOW + timedelta(hours=1),
        db_manager=isolated_db,
    )

    assert completed.state == "completed"
    assert replay.claimed is False
    assert replay.run.attempt_count == 1
    assert get_latest_outcome_maintenance(
        db_manager=isolated_db,
    ).maintenance_key == first.run.maintenance_key


def test_completed_v1_slot_does_not_block_default_v2_policy(isolated_db):
    legacy = claim_outcome_maintenance(
        SESSION,
        owner_key="legacy-worker",
        now=NOW,
        policy_version="xnys-close-due-outcomes-v1",
        db_manager=isolated_db,
    )
    settle_outcome_maintenance(
        legacy.run.maintenance_key,
        owner_key="legacy-worker",
        state="completed",
        result={"due_snapshot_count": 0, "inserted_outcomes": 0},
        now=NOW + timedelta(minutes=1),
        db_manager=isolated_db,
    )

    current = claim_outcome_maintenance(
        SESSION,
        owner_key="current-worker",
        now=NOW + timedelta(minutes=2),
        db_manager=isolated_db,
    )

    assert current.claimed is True
    assert current.run.policy_version == OUTCOME_MAINTENANCE_POLICY_VERSION
    assert current.run.maintenance_key != legacy.run.maintenance_key
    assert get_latest_outcome_maintenance(
        db_manager=isolated_db,
    ).maintenance_key == current.run.maintenance_key
    assert get_latest_outcome_maintenance(
        policy_version="xnys-close-due-outcomes-v1",
        db_manager=isolated_db,
    ).maintenance_key == legacy.run.maintenance_key


def test_degraded_slot_retries_once_at_persisted_time(isolated_db):
    first = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-a",
        now=NOW,
        db_manager=isolated_db,
    )
    retry_at = NOW + timedelta(hours=4)
    degraded = settle_outcome_maintenance(
        first.run.maintenance_key,
        owner_key="worker-a",
        state="degraded",
        result={"data_gap_horizons": 1},
        now=NOW + timedelta(minutes=1),
        next_retry_at=retry_at,
        error_code="outcome_data_gap",
        db_manager=isolated_db,
    )

    early = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-b",
        now=retry_at - timedelta(seconds=1),
        db_manager=isolated_db,
    )
    second = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-b",
        now=retry_at,
        db_manager=isolated_db,
    )
    final = settle_outcome_maintenance(
        second.run.maintenance_key,
        owner_key="worker-b",
        state="degraded",
        result={"data_gap_horizons": 1},
        now=retry_at + timedelta(minutes=1),
        db_manager=isolated_db,
    )
    exhausted = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-c",
        now=retry_at + timedelta(days=1),
        db_manager=isolated_db,
    )

    assert degraded.next_retry_at == retry_at
    assert early.claimed is False
    assert second.claimed is True
    assert second.run.attempt_count == 2
    assert final.next_retry_at is None
    assert exhausted.claimed is False


def test_expired_lease_can_be_recovered_but_stale_owner_cannot_settle(isolated_db):
    first = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-a",
        now=NOW,
        lease_seconds=60,
        db_manager=isolated_db,
    )
    recovered = claim_outcome_maintenance(
        SESSION,
        owner_key="worker-b",
        now=NOW + timedelta(seconds=61),
        db_manager=isolated_db,
    )

    assert recovered.claimed is True
    assert recovered.run.attempt_count == 2
    with pytest.raises(OutcomeMaintenanceLeaseError):
        settle_outcome_maintenance(
            first.run.maintenance_key,
            owner_key="worker-a",
            state="completed",
            result={},
            now=NOW + timedelta(seconds=62),
            db_manager=isolated_db,
        )


def test_two_connections_claim_only_one_owner(isolated_db):
    def claim(owner_key: str):
        return claim_outcome_maintenance(
            SESSION,
            owner_key=owner_key,
            now=NOW,
            db_manager=isolated_db,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))

    assert sorted(result.claimed for result in results) == [False, True]

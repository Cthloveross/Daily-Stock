# -*- coding: utf-8 -*-
"""Append-only ReviewAnnotation repository and review-queue coverage."""
from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
import pytest

from src.journal.ledger.episode_repository import (
    append_latest_position_episode_build,
    get_latest_position_episode_detail,
    get_latest_position_episode_page,
)
from src.journal.ledger.models import (
    EpisodeBuild,
    PositionEpisode,
    ReviewAnnotation,
)
from src.journal.ledger.repository import import_statement_batch
from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    ReviewAnnotationRepositoryError,
    ReviewAnnotationScopeNotFoundError,
    append_review_annotation,
    get_latest_review_annotation,
    list_review_annotation_history,
)
from src.journal.tests.test_episode_repository import (
    _case_focus_statement,
    _detailed_statement,
)
from src.storage import DatabaseManager, get_db


def _seed_one() -> tuple[int, int]:
    import_statement_batch(_detailed_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=10)
    return build.build_id, page.items[0].episode_id


def test_review_revisions_are_idempotent_append_only_and_restart_readable():
    build_id, episode_id = _seed_one()
    first = append_review_annotation(
        ReviewAnnotationInput(
            review_status="in_progress",
            setup_thesis="  开盘回踩 EMA  ",
            tags=("momentum", " A+ ", "momentum"),
            error_types=("late entry", "late entry"),
        ),
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )

    assert first.duplicate is False
    assert first.annotation.revision == 1
    assert first.annotation.setup_thesis == "开盘回踩 EMA"
    assert first.annotation.tags == ("A+", "momentum")
    assert first.annotation.error_types == ("late entry",)

    duplicate = append_review_annotation(
        ReviewAnnotationInput(
            review_status="in_progress",
            setup_thesis="开盘回踩 EMA",
            tags=("momentum", "A+"),
            error_types=("late entry",),
        ),
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )
    assert duplicate.duplicate is True
    assert duplicate.annotation.annotation_id == first.annotation.annotation_id
    assert duplicate.annotation.revision == 1

    second = append_review_annotation(
        ReviewAnnotationInput(
            review_status="completed",
            setup_thesis="开盘回踩 EMA",
            exit_reason="触及计划目标",
            tags=("A+", "momentum"),
            error_types=("late entry",),
        ),
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )
    assert second.duplicate is False
    assert second.annotation.revision == 2
    assert (
        second.annotation.previous_annotation_id
        == first.annotation.annotation_id
    )

    history = list_review_annotation_history(
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )
    assert [item.revision for item in history] == [2, 1]

    # Recreate the DatabaseManager to prove the value is persisted rather than
    # surviving only in a process-local cache.
    DatabaseManager.reset_instance()
    latest = get_latest_review_annotation(
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )
    assert latest is not None
    assert latest.annotation_id == second.annotation.annotation_id
    assert latest.review_status == "completed"


def test_review_validation_and_exact_scope_binding():
    build_id, episode_id = _seed_one()

    empty_progress = append_review_annotation(
        ReviewAnnotationInput(review_status="in_progress"),
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )
    assert empty_progress.annotation.revision == 1

    with pytest.raises(
        ReviewAnnotationRepositoryError,
        match="completed review requires",
    ):
        append_review_annotation(
            ReviewAnnotationInput(review_status="completed"),
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )

    with pytest.raises(
        ReviewAnnotationRepositoryError,
        match="at most 2000",
    ):
        append_review_annotation(
            ReviewAnnotationInput(
                review_status="in_progress",
                setup_thesis="测" * 2_001,
            ),
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )

    with pytest.raises(
        ReviewAnnotationRepositoryError,
        match="at most 6000",
    ):
        append_review_annotation(
            ReviewAnnotationInput(
                review_status="in_progress",
                setup_thesis="一" * 2_000,
                entry_trigger="二" * 2_000,
                invalidation_plan="三" * 2_000,
                position_rationale="四",
            ),
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )

    with pytest.raises(
        ReviewAnnotationRepositoryError,
        match="at most 20 items",
    ):
        append_review_annotation(
            ReviewAnnotationInput(
                review_status="in_progress",
                tags=tuple(f"tag-{index}" for index in range(21)),
            ),
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )

    with pytest.raises(
        ReviewAnnotationRepositoryError,
        match="at most 64 characters",
    ):
        append_review_annotation(
            ReviewAnnotationInput(
                review_status="in_progress",
                error_types=("x" * 65,),
            ),
            episode_build_id=build_id,
            position_episode_id=episode_id,
        )

    for wrong_scope in (
        {"episode_build_id": build_id + 1, "position_episode_id": episode_id},
        {"episode_build_id": build_id, "position_episode_id": episode_id + 99},
        {
            "episode_build_id": build_id,
            "position_episode_id": episode_id,
            "account_key": "another-account",
        },
    ):
        with pytest.raises(
            ReviewAnnotationScopeNotFoundError,
            match="selected build and account",
        ):
            get_latest_review_annotation(**wrong_scope)


def test_review_trigger_rejects_mutation_without_changing_episode_economics():
    build_id, episode_id = _seed_one()
    db = get_db()
    with db.session_scope() as session:
        before_position = session.execute(
            select(
                PositionEpisode.realized_pnl_gross,
                PositionEpisode.total_fee,
                PositionEpisode.realized_pnl_net,
            ).where(PositionEpisode.id == episode_id)
        ).one()
        before_build = session.execute(
            select(
                EpisodeBuild.build_report_json,
                EpisodeBuild.position_episode_count,
            ).where(EpisodeBuild.id == build_id)
        ).one()

    saved = append_review_annotation(
        ReviewAnnotationInput(
            review_status="completed",
            post_trade_reflection="按计划退出",
        ),
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )

    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.execute(
                update(ReviewAnnotation)
                .where(ReviewAnnotation.id == saved.annotation.annotation_id)
                .values(review_status="in_progress")
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with db.session_scope() as session:
            session.execute(
                delete(ReviewAnnotation).where(
                    ReviewAnnotation.id == saved.annotation.annotation_id
                )
            )

    with db.session_scope() as session:
        after_position = session.execute(
            select(
                PositionEpisode.realized_pnl_gross,
                PositionEpisode.total_fee,
                PositionEpisode.realized_pnl_net,
            ).where(PositionEpisode.id == episode_id)
        ).one()
        after_build = session.execute(
            select(
                EpisodeBuild.build_report_json,
                EpisodeBuild.position_episode_count,
            ).where(EpisodeBuild.id == build_id)
        ).one()
        assert session.execute(
            select(func.count(ReviewAnnotation.id))
        ).scalar_one() == 1

    assert after_position == before_position
    assert after_build == before_build


def test_review_queue_counts_and_filters_apply_before_pagination():
    import_statement_batch(_case_focus_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    initial = get_latest_position_episode_page(per_page=10)
    episode_ids = [item.episode_id for item in initial.items]

    assert initial.total == 4
    assert initial.review_queue.pending == 4
    assert initial.review_queue.total == 4
    assert {item.review_status for item in initial.items} == {"not_started"}

    append_review_annotation(
        ReviewAnnotationInput(
            review_status="in_progress",
            setup_thesis="等待确认",
        ),
        episode_build_id=build.build_id,
        position_episode_id=episode_ids[0],
    )
    append_review_annotation(
        ReviewAnnotationInput(
            review_status="completed",
            exit_reason="按计划退出",
        ),
        episode_build_id=build.build_id,
        position_episode_id=episode_ids[1],
    )

    completed = get_latest_position_episode_page(
        review_status="completed",
        page=1,
        per_page=1,
    )
    assert completed.total == 1
    assert len(completed.items) == 1
    assert completed.items[0].review_status == "completed"
    assert completed.items[0].review_revision == 1
    assert completed.items[0].review_updated_at is not None
    assert completed.review_queue.pending == 2
    assert completed.review_queue.in_progress == 1
    assert completed.review_queue.completed == 1
    assert completed.review_queue.total == 4

    pending = get_latest_position_episode_page(
        review_status="not_started",
        page=1,
        per_page=1,
    )
    assert pending.total == 2
    assert len(pending.items) == 1
    assert pending.items[0].review_status == "not_started"

    detail = get_latest_position_episode_detail(episode_ids[1])
    assert detail is not None
    assert detail.episode.review_status == "completed"
    assert detail.episode.review_revision == 1

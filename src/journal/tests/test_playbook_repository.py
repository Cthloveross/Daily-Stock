# -*- coding: utf-8 -*-
"""Append-only Playbook candidates and rule versions (contract slice C-2)."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import src.journal.ledger.playbook_repository as playbook_repository
from src.journal.ledger.playbook_repository import (
    PlaybookConflictError,
    PlaybookRepositoryError,
    PlaybookSourceBucket,
    create_playbook_candidate,
    list_playbook_candidates,
    list_playbook_links_for_episode,
    list_playbook_rules,
    promote_candidate_to_rule,
    retire_playbook_rule,
)
from src.journal.ledger.repository import init_ledger_schema
from src.journal.ledger.review_repository import (
    ReviewAnnotationScopeNotFoundError,
)
from src.journal.tests.test_review_insights import (
    _annotate,
    _seed_statement_build,
    _seed_synthetic_build,
    _verified_spec,
)
from src.storage import get_db


ET = ZoneInfo("America/New_York")

_CANDIDATE_TABLE = "journal_v2_playbook_candidates"
_RULE_TABLE = "journal_v2_playbook_rules"


def _business_table_digest(path: Path) -> str:
    """Order-stable content digest across every business table."""
    connection = sqlite3.connect(path)
    try:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        digest = hashlib.sha256()
        for name in names:
            digest.update(name.encode("utf-8"))
            for row in connection.execute(
                f'SELECT * FROM "{name}" ORDER BY 1'
            ):
                digest.update(repr(row).encode("utf-8"))
        return digest.hexdigest()
    finally:
        connection.close()


def _table_count(table: str) -> int:
    db = get_db()
    with db.session_scope() as session:
        return int(
            session.connection()
            .exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"')
            .scalar()
        )


def _bucket(**overrides) -> PlaybookSourceBucket:
    values = {
        "group_kind": "tag",
        "group_value": "momentum",
        "direction": "LONG",
        "boundary_policy": "assumed_or_censored",
    }
    values.update(overrides)
    return PlaybookSourceBucket(**values)


def _seed_annotated_statement_build() -> tuple[int, dict[str, int]]:
    """Statement build with one completed momentum annotation on WIN."""
    build_id, by_underlying = _seed_statement_build()
    _annotate(
        build_id,
        by_underlying["WIN"],
        review_status="completed",
        tags=("momentum",),
    )
    return build_id, by_underlying


def test_create_candidate_freezes_bucket_evidence_snapshot():
    build_id, by_underlying = _seed_annotated_statement_build()

    result = create_playbook_candidate(
        title="动量突破只在验证边界做",
        rule_text="只在 regime 分数 >= 60 且突破当日成交量放大时进场。",
        source_bucket=_bucket(),
    )

    assert result.duplicate is False
    candidate = result.candidate
    assert len(candidate.candidate_key) == 64
    assert candidate.title == "动量突破只在验证边界做"
    assert candidate.promoted is False
    assert candidate.source_bucket == _bucket()
    snapshot = candidate.evidence_snapshot
    assert snapshot["schema_version"] == "playbook-evidence-snapshot/1.0"
    assert snapshot["snapshot_kind"] == "insights_bucket"
    assert snapshot["captured_for"] == "candidate_creation"
    assert snapshot["build_id"] == build_id
    assert snapshot["source_kind"] == "csv_batch"
    assert snapshot["bucket"] == {
        "group_kind": "tag",
        "group_value": "momentum",
        "direction": "LONG",
        "boundary_policy": "assumed_or_censored",
    }
    assert snapshot["counts"] == {
        "episode_count": 1,
        "distinct_trading_day_count": 1,
        "review_completed_count": 1,
        "verified_episode_count": 0,
        "verified_distinct_trading_day_count": 0,
        "conditional_episode_count": 1,
    }
    assert snapshot["episode_ids"] == [by_underlying["WIN"]]
    assert snapshot["episode_id_sample_truncated"] is False
    assert snapshot["annotation_revisions"] == [
        {
            "position_episode_id": by_underlying["WIN"],
            "annotation_id": snapshot["annotation_revisions"][0][
                "annotation_id"
            ],
            "revision": 1,
        }
    ]
    assert snapshot["thresholds"] == {
        "min_episode_count": 10,
        "min_distinct_trading_day_count": 5,
    }
    # The as-of moment is frozen alongside the counts.
    datetime.fromisoformat(snapshot["generated_at"])


def test_create_candidate_replay_is_idempotent_and_keeps_frozen_snapshot(
    isolated_sqlite: Path,
):
    _build_id, by_underlying = _seed_annotated_statement_build()
    first = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    )
    # The underlying bucket changes after creation; the frozen snapshot
    # must not follow it on an idempotent replay.
    _annotate(_build_id, by_underlying["LOSS"], tags=("momentum",))
    digest_before_replay = _business_table_digest(isolated_sqlite)

    replay = create_playbook_candidate(
        title=" 动量候选 ",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    )

    assert replay.duplicate is True
    assert replay.candidate.candidate_key == first.candidate.candidate_key
    assert replay.candidate.evidence_snapshot == (
        first.candidate.evidence_snapshot
    )
    assert replay.candidate.evidence_snapshot["counts"]["episode_count"] == 1
    assert _business_table_digest(isolated_sqlite) == digest_before_replay


def test_create_candidate_fails_closed_when_bucket_missing(
    isolated_sqlite: Path,
):
    _seed_annotated_statement_build()
    digest_before = _business_table_digest(isolated_sqlite)

    with pytest.raises(PlaybookConflictError, match="bucket does not exist"):
        create_playbook_candidate(
            title="不存在的桶",
            rule_text="规则文本。",
            source_bucket=_bucket(group_value="nonexistent"),
        )

    assert _business_table_digest(isolated_sqlite) == digest_before
    assert _table_count(_CANDIDATE_TABLE) == 0


def test_create_candidate_fails_closed_without_any_build(
    isolated_sqlite: Path,
):
    # Materialize the empty schema first so the digest only tracks rows.
    init_ledger_schema()
    digest_before = _business_table_digest(isolated_sqlite)

    with pytest.raises(PlaybookConflictError, match="bucket does not exist"):
        create_playbook_candidate(
            title="没有构建",
            rule_text="规则文本。",
            source_bucket=_bucket(),
        )

    assert _business_table_digest(isolated_sqlite) == digest_before


def test_create_candidate_validation_rejections_are_zero_write(
    isolated_sqlite: Path,
):
    _seed_annotated_statement_build()
    digest_before = _business_table_digest(isolated_sqlite)

    with pytest.raises(PlaybookRepositoryError, match="title cannot be empty"):
        create_playbook_candidate(
            title="   ",
            rule_text="规则文本。",
            source_bucket=_bucket(),
        )
    with pytest.raises(
        PlaybookRepositoryError, match="rule_text cannot be empty"
    ):
        create_playbook_candidate(
            title="标题",
            rule_text="",
            source_bucket=_bucket(),
        )
    with pytest.raises(PlaybookRepositoryError, match="at most 120"):
        create_playbook_candidate(
            title="超" * 121,
            rule_text="规则文本。",
            source_bucket=_bucket(),
        )
    with pytest.raises(PlaybookRepositoryError, match="at most 2000"):
        create_playbook_candidate(
            title="标题",
            rule_text="超" * 2001,
            source_bucket=_bucket(),
        )
    with pytest.raises(
        PlaybookRepositoryError, match="direction must be LONG or SHORT"
    ):
        create_playbook_candidate(
            title="标题",
            rule_text="规则文本。",
            source_bucket=_bucket(direction="SIDEWAYS"),
        )
    with pytest.raises(
        PlaybookRepositoryError, match="group_kind must be tag or error_type"
    ):
        create_playbook_candidate(
            title="标题",
            rule_text="规则文本。",
            source_bucket=_bucket(group_kind="setup"),
        )

    assert _business_table_digest(isolated_sqlite) == digest_before


def test_free_form_candidate_freezes_no_fabricated_evidence():
    result = create_playbook_candidate(
        title="自由候选",
        rule_text="不绑定任何观察桶的自由规则。",
    )

    assert result.duplicate is False
    assert result.candidate.source_bucket is None
    snapshot = result.candidate.evidence_snapshot
    assert snapshot["snapshot_kind"] == "free_form"
    assert "episode_ids" not in snapshot
    assert "counts" not in snapshot


def test_promote_appends_version_one_and_refreezes_evidence():
    build_id, by_underlying = _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    # The bucket grows and WIN gains a second revision before promotion:
    # the rule snapshot must freeze the state at promotion time.
    _annotate(build_id, by_underlying["LOSS"], tags=("momentum",))
    _annotate(
        build_id,
        by_underlying["WIN"],
        review_status="completed",
        tags=("momentum",),
        setup_thesis="修订后的复盘",
    )

    result = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key
    )

    assert result.duplicate is False
    rule = result.rule
    assert rule.version == 1
    assert rule.status == "active"
    assert rule.previous_rule_id is None
    assert rule.is_latest_version is True
    assert len(rule.rule_key) == 64
    assert len(rule.lineage_key) == 64
    assert rule.promoted_from_candidate_id == candidate.candidate_id
    assert rule.promoted_from_candidate_key == candidate.candidate_key
    assert rule.title == candidate.title
    assert rule.rule_text == candidate.rule_text
    snapshot = rule.evidence_snapshot
    assert snapshot["captured_for"] == "rule_promotion"
    assert snapshot["counts"]["episode_count"] == 2
    assert snapshot["episode_ids"] == sorted(
        [by_underlying["WIN"], by_underlying["LOSS"]]
    )
    revisions = {
        entry["position_episode_id"]: entry["revision"]
        for entry in snapshot["annotation_revisions"]
    }
    assert revisions[by_underlying["WIN"]] == 2
    assert revisions[by_underlying["LOSS"]] == 1
    # The candidate's own frozen snapshot is untouched by the promotion.
    stored_candidate = list_playbook_candidates()[0]
    assert stored_candidate.evidence_snapshot["counts"]["episode_count"] == 1
    assert stored_candidate.promoted is True


def test_promote_replay_is_duplicate_with_zero_new_rows(
    isolated_sqlite: Path,
):
    _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    first = promote_candidate_to_rule(candidate_key=candidate.candidate_key)
    digest_after_first = _business_table_digest(isolated_sqlite)

    replay = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key
    )

    assert replay.duplicate is True
    assert replay.rule.rule_id == first.rule.rule_id
    assert replay.rule.evidence_snapshot == first.rule.evidence_snapshot
    assert _business_table_digest(isolated_sqlite) == digest_after_first
    assert _table_count(_RULE_TABLE) == 1


def test_promote_unknown_candidate_fails_closed(isolated_sqlite: Path):
    _seed_annotated_statement_build()
    digest_before = _business_table_digest(isolated_sqlite)

    with pytest.raises(PlaybookConflictError, match="does not exist"):
        promote_candidate_to_rule(candidate_key="a" * 64)

    assert _business_table_digest(isolated_sqlite) == digest_before


def test_promote_fails_closed_when_bucket_disappears(isolated_sqlite: Path):
    """A newer default build without the bucket blocks promotion honestly."""
    _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    # A newer build becomes the default and has no annotations at all.
    _seed_synthetic_build(
        [
            {
                "underlying": "SYN",
                "direction": "long",
                "opened_at": datetime(2026, 7, 27, 10, 0, tzinfo=ET),
                "realized_pnl_net": None,
                "verified": True,
                "lifecycle_status": "open",
            }
        ]
    )
    digest_before = _business_table_digest(isolated_sqlite)

    with pytest.raises(PlaybookConflictError, match="bucket does not exist"):
        promote_candidate_to_rule(candidate_key=candidate.candidate_key)

    assert _business_table_digest(isolated_sqlite) == digest_before
    assert _table_count(_RULE_TABLE) == 0


def test_retire_appends_retired_version_copying_the_frozen_snapshot():
    _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    promoted = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key
    ).rule

    result = retire_playbook_rule(
        lineage_key=promoted.lineage_key,
        expected_current_version=1,
    )

    assert result.duplicate is False
    retired = result.rule
    assert retired.version == 2
    assert retired.status == "retired"
    assert retired.previous_rule_id == promoted.rule_id
    assert retired.lineage_key == promoted.lineage_key
    assert retired.title == promoted.title
    assert retired.rule_text == promoted.rule_text
    # Retirement freezes nothing new: the promoted snapshot is copied.
    assert retired.evidence_snapshot == promoted.evidence_snapshot
    assert retired.evidence_snapshot_sha256 == (
        promoted.evidence_snapshot_sha256
    )
    rules = list_playbook_rules()
    assert [rule.version for rule in rules] == [2, 1]
    assert rules[0].is_latest_version is True
    assert rules[1].is_latest_version is False


def test_retire_replay_and_stale_cas(isolated_sqlite: Path):
    _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    promoted = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key
    ).rule
    retire_playbook_rule(
        lineage_key=promoted.lineage_key,
        expected_current_version=1,
    )
    digest_after_retire = _business_table_digest(isolated_sqlite)

    replay = retire_playbook_rule(
        lineage_key=promoted.lineage_key,
        expected_current_version=1,
    )
    assert replay.duplicate is True
    assert replay.rule.version == 2
    assert _business_table_digest(isolated_sqlite) == digest_after_retire

    with pytest.raises(PlaybookConflictError, match="version changed"):
        retire_playbook_rule(
            lineage_key=promoted.lineage_key,
            expected_current_version=5,
        )
    with pytest.raises(PlaybookConflictError, match="does not exist"):
        retire_playbook_rule(
            lineage_key="b" * 64,
            expected_current_version=1,
        )
    assert _business_table_digest(isolated_sqlite) == digest_after_retire


def test_promotion_after_retirement_requires_explicit_new_version_intent(
    isolated_sqlite: Path,
):
    _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    promoted = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key
    ).rule
    retire_playbook_rule(
        lineage_key=promoted.lineage_key,
        expected_current_version=1,
    )
    digest_after_retire = _business_table_digest(isolated_sqlite)

    with pytest.raises(PlaybookConflictError, match="new-version intent"):
        promote_candidate_to_rule(candidate_key=candidate.candidate_key)
    with pytest.raises(PlaybookConflictError, match="version changed"):
        promote_candidate_to_rule(
            candidate_key=candidate.candidate_key,
            allow_new_version=True,
            expected_current_version=1,
        )
    assert _business_table_digest(isolated_sqlite) == digest_after_retire

    reactivated = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key,
        allow_new_version=True,
        expected_current_version=2,
    )
    assert reactivated.duplicate is False
    assert reactivated.rule.version == 3
    assert reactivated.rule.status == "active"
    assert reactivated.rule.previous_rule_id is not None


def test_playbook_tables_reject_update_and_delete(isolated_sqlite: Path):
    _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    promote_candidate_to_rule(candidate_key=candidate.candidate_key)

    connection = sqlite3.connect(isolated_sqlite)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(
                f"UPDATE {_CANDIDATE_TABLE} SET title='改写'"
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(f"DELETE FROM {_CANDIDATE_TABLE}")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(
                f"UPDATE {_RULE_TABLE} SET status='retired'"
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(f"DELETE FROM {_RULE_TABLE}")
    finally:
        connection.close()


def test_list_candidates_and_rules_return_newest_first():
    _seed_annotated_statement_build()
    first = create_playbook_candidate(
        title="第一个候选",
        rule_text="规则一。",
        source_bucket=_bucket(),
    ).candidate
    second = create_playbook_candidate(
        title="第二个候选",
        rule_text="规则二。",
    ).candidate
    promote_candidate_to_rule(candidate_key=first.candidate_key)

    candidates = list_playbook_candidates()
    assert [item.candidate_id for item in candidates] == [
        second.candidate_id,
        first.candidate_id,
    ]
    assert candidates[0].promoted is False
    assert candidates[1].promoted is True

    rules = list_playbook_rules()
    assert len(rules) == 1
    assert rules[0].promoted_from_candidate_key == first.candidate_key
    assert rules[0].is_latest_version is True


# --- slice C-3: zero-write reverse links from one episode --------------------


def test_episode_links_confirmed_rules_before_candidates():
    build_id, by_underlying = _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    promoted = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key
    ).rule

    result = list_playbook_links_for_episode(
        episode_id=by_underlying["WIN"],
        build_id=build_id,
    )

    assert result.build_id == build_id
    assert result.episode_id == by_underlying["WIN"]
    assert [
        (link.kind, link.link_state) for link in result.links
    ] == [("rule", "confirmed"), ("candidate", "confirmed")]
    rule_link, candidate_link = result.links
    assert rule_link.title == "动量候选"
    assert rule_link.lineage_key == promoted.lineage_key
    assert rule_link.version == 1
    assert rule_link.status == "active"
    assert rule_link.candidate_key is None
    assert rule_link.snapshot_build_id == build_id
    assert rule_link.snapshot_generated_at is not None
    # The bucket echo comes from the frozen snapshot, not a live re-derive.
    assert rule_link.bucket == _bucket()
    assert candidate_link.candidate_key == candidate.candidate_key
    assert candidate_link.promoted is True
    assert candidate_link.lineage_key is None
    assert candidate_link.bucket == _bucket()

    # An episode outside every frozen sample has no links at all.
    unreferenced = list_playbook_links_for_episode(
        episode_id=by_underlying["LOSS"],
        build_id=build_id,
    )
    assert unreferenced.links == ()


def test_episode_links_show_only_latest_rule_version_with_retired_status():
    build_id, by_underlying = _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    promoted = promote_candidate_to_rule(
        candidate_key=candidate.candidate_key
    ).rule
    retire_playbook_rule(
        lineage_key=promoted.lineage_key,
        expected_current_version=1,
    )

    result = list_playbook_links_for_episode(
        episode_id=by_underlying["WIN"],
        build_id=build_id,
    )

    rule_links = [link for link in result.links if link.kind == "rule"]
    assert len(rule_links) == 1
    assert rule_links[0].version == 2
    assert rule_links[0].status == "retired"
    assert rule_links[0].link_state == "confirmed"


def test_episode_links_exclude_snapshots_frozen_for_another_build():
    build_a, by_underlying = _seed_annotated_statement_build()
    create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    )
    # A newer default build gains an episode whose identity matches the
    # frozen bucket echo exactly — the build identity check must still win.
    build_b, episode_ids = _seed_synthetic_build(
        [
            {
                "underlying": "SYN",
                "direction": "long",
                "opened_at": datetime(2026, 7, 27, 10, 0, tzinfo=ET),
                "realized_pnl_net": None,
                "verified": False,
            }
        ]
    )
    _annotate(build_b, episode_ids[0], tags=("momentum",))

    mismatched = list_playbook_links_for_episode(
        episode_id=episode_ids[0],
        build_id=build_b,
    )
    assert mismatched.links == ()

    # The episode of the snapshot's own build keeps its confirmed link even
    # though that build is no longer the default one.
    original = list_playbook_links_for_episode(
        episode_id=by_underlying["WIN"],
        build_id=build_a,
    )
    assert [link.link_state for link in original.links] == ["confirmed"]


def test_episode_links_truncated_sample_is_possible_only_on_bucket_match(
    monkeypatch,
):
    # Freeze with a tiny sample bound so the snapshot truncates honestly.
    monkeypatch.setattr(playbook_repository, "_MAX_EVIDENCE_EPISODE_IDS", 2)
    specs = [
        _verified_spec(day=6, pnl="10"),
        _verified_spec(day=7, pnl="10"),
        _verified_spec(day=8, pnl="10"),
        _verified_spec(day=9, pnl="10"),  # tagged "other"
        _verified_spec(day=10, pnl="10", direction="short"),
    ]
    build_id, episode_ids = _seed_synthetic_build(specs)
    for episode_id in episode_ids[:3]:
        _annotate(build_id, episode_id, tags=("breakout",))
    _annotate(build_id, episode_ids[3], tags=("other",))
    _annotate(build_id, episode_ids[4], tags=("breakout",))
    candidate = create_playbook_candidate(
        title="突破候选",
        rule_text="规则文本。",
        source_bucket=_bucket(
            group_value="breakout", boundary_policy="verified"
        ),
    ).candidate
    snapshot = candidate.evidence_snapshot
    assert snapshot["episode_id_sample_truncated"] is True
    assert snapshot["episode_ids"] == sorted(episode_ids[:3])[:2]

    # Sampled member: confirmed.
    sampled = list_playbook_links_for_episode(
        episode_id=episode_ids[0],
        build_id=build_id,
    )
    assert [link.link_state for link in sampled.links] == ["confirmed"]

    # Outside the truncated sample with a matching bucket echo: unknowable,
    # surfaced as a separate possible entry — never as a confirmed link.
    truncated_member = list_playbook_links_for_episode(
        episode_id=episode_ids[2],
        build_id=build_id,
    )
    assert [
        (link.kind, link.link_state) for link in truncated_member.links
    ] == [("candidate", "possible_truncated")]

    # Outside the sample with a different tag: omitted entirely.
    other_tag = list_playbook_links_for_episode(
        episode_id=episode_ids[3],
        build_id=build_id,
    )
    assert other_tag.links == ()

    # Same tag but the opposite direction: omitted entirely.
    other_direction = list_playbook_links_for_episode(
        episode_id=episode_ids[4],
        build_id=build_id,
    )
    assert other_direction.links == ()


def test_episode_links_are_zero_write_and_fail_closed_on_unknown_scope(
    isolated_sqlite: Path,
):
    build_id, by_underlying = _seed_annotated_statement_build()
    candidate = create_playbook_candidate(
        title="动量候选",
        rule_text="规则文本。",
        source_bucket=_bucket(),
    ).candidate
    promote_candidate_to_rule(candidate_key=candidate.candidate_key)
    digest_before = _business_table_digest(isolated_sqlite)

    list_playbook_links_for_episode(
        episode_id=by_underlying["WIN"],
        build_id=build_id,
    )
    with pytest.raises(
        ReviewAnnotationScopeNotFoundError, match="not found"
    ):
        list_playbook_links_for_episode(
            episode_id=999_999,
            build_id=build_id,
        )
    with pytest.raises(PlaybookRepositoryError, match="must be positive"):
        list_playbook_links_for_episode(episode_id=0, build_id=build_id)

    assert _business_table_digest(isolated_sqlite) == digest_before

# -*- coding: utf-8 -*-
"""Zero-write review-insights aggregation (Playbook contract slice C-1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from src.journal.ledger.episode_repository import (
    append_latest_position_episode_build,
    get_latest_position_episode_page,
)
from src.journal.ledger.models import (
    EpisodeBuild,
    PositionEpisode,
    StrategyEpisode,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    import_statement_batch,
    init_ledger_schema,
)
from src.journal.ledger.review_insights import (
    BOUNDARY_POLICY_ASSUMED_OR_CENSORED,
    BOUNDARY_POLICY_VERIFIED,
    STATS_GATE_BELOW_SAMPLE_THRESHOLD,
    STATS_GATE_NO_VERIFIED_PNL,
    get_latest_review_insights,
)
from src.journal.ledger.review_repository import (
    ReviewAnnotationInput,
    append_review_annotation,
)
from src.journal.tests.test_episode_repository import _case_focus_statement
from src.storage import get_db


ET = ZoneInfo("America/New_York")


def _seed_statement_build() -> tuple[int, dict[str, int]]:
    """Real CSV path: four assumed-flat episodes on four distinct ET days."""
    import_statement_batch(_case_focus_statement())
    build = append_latest_position_episode_build(accept_assumed_flat=True)
    page = get_latest_position_episode_page(per_page=10)
    by_underlying = {item.underlying: item.episode_id for item in page.items}
    return build.build_id, by_underlying


def _annotate(
    build_id: int,
    episode_id: int,
    *,
    review_status: str = "in_progress",
    tags: tuple[str, ...] = (),
    error_types: tuple[str, ...] = (),
    setup_thesis: str = "复盘备注",
) -> None:
    append_review_annotation(
        ReviewAnnotationInput(
            review_status=review_status,
            setup_thesis=setup_thesis,
            tags=tags,
            error_types=error_types,
        ),
        episode_build_id=build_id,
        position_episode_id=episode_id,
    )


def _seed_synthetic_build(
    specs: list[dict],
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> tuple[int, list[int]]:
    """Append-only INSERTs shaped like stored builder output.

    Statement builds can only produce assumed-flat boundaries, so verified
    P&L scenarios are seeded directly with the same stored JSON shape the
    real builder writes; nothing is mutated afterwards.
    """
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = EpisodeBuild(
            build_key=f"synthetic-insights-{account_key}-{len(specs)}",
            broker="moomoo",
            account_key=account_key,
            builder_name="synthetic-insights-test",
            builder_version="1.0.0",
            builder_config_sha256="0" * 64,
            evidence_set_sha256="f" * 64,
            source_batch_ids_json="[1]",
            source_cutoff_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            status="succeeded",
            strategy_episode_count=len(specs),
            position_episode_count=len(specs),
            unresolved_evidence_count=0,
            reconciliation_status="matched",
            build_report_json="{}",
            completeness_score=Decimal("1"),
            completeness_json="{}",
            provenance_json="{}",
        )
        session.add(build)
        session.flush()
        episode_ids: list[int] = []
        for index, spec in enumerate(specs):
            verified = bool(spec.get("verified", True))
            lifecycle_status = spec.get("lifecycle_status", "closed")
            opened_at = spec["opened_at"].astimezone(timezone.utc)
            closed_at = (
                opened_at + timedelta(hours=2)
                if lifecycle_status == "closed"
                else None
            )
            pnl = spec.get("realized_pnl_net")
            matching: dict[str, object] = {
                "method": "signed_position_zero_crossing",
                "opening_boundary_policy": (
                    "verified_trade_flow"
                    if verified
                    else "assumed_flat_unverified"
                ),
                "contract_multiplier_basis": "amount_quantity_price_proof",
            }
            if spec.get("group_fee_unallocated"):
                matching["execution_group_fee_scope"] = (
                    "retained_at_group_scope_not_leg_allocated"
                )
            strategy = StrategyEpisode(
                episode_build_id=build.id,
                episode_key=f"strategy-{index}",
                lineage_key=f"lineage-{index}",
                broker="moomoo",
                account_key=account_key,
                underlying=spec.get("underlying", "SYN"),
                strategy_type="single_position_unclassified",
                direction=spec.get("direction", "long"),
                lifecycle_status=lifecycle_status,
                grouping_method="one_to_one_no_strategy_inference",
                grouping_confidence=Decimal("1"),
                currency="USD",
                opened_at=opened_at,
                closed_at=closed_at,
                position_count=1,
                leg_count=1,
                realized_pnl_net=pnl,
                pnl_precision="exact",
                grouping_evidence_json="{}",
                evidence_summary_json="{}",
                completeness_score=Decimal("1"),
                completeness_status="exact",
                completeness_json="{}",
                provenance_json="{}",
            )
            session.add(strategy)
            session.flush()
            episode = PositionEpisode(
                episode_build_id=build.id,
                strategy_episode_id=strategy.id,
                episode_key=f"position-{index}",
                lineage_key=f"lineage-{index}",
                broker="moomoo",
                account_key=account_key,
                raw_symbol=spec.get("underlying", "SYN") + "260918C100000",
                asset_type="option",
                underlying=spec.get("underlying", "SYN"),
                direction=spec.get("direction", "long"),
                lifecycle_status=lifecycle_status,
                currency="USD",
                opened_at=opened_at,
                closed_at=closed_at,
                hold_seconds=7200 if closed_at is not None else None,
                opened_quantity=Decimal("1"),
                max_absolute_quantity=Decimal("1"),
                closed_quantity=(
                    Decimal("1")
                    if lifecycle_status == "closed"
                    else Decimal("0")
                ),
                remaining_quantity=(
                    Decimal("0")
                    if lifecycle_status == "closed"
                    else Decimal("1")
                ),
                realized_pnl_gross=pnl,
                total_fee=Decimal("0"),
                realized_pnl_net=pnl,
                construction_basis="fills",
                is_left_censored=bool(spec.get("is_left_censored", False)),
                is_right_censored=False,
                has_exact_fill_times=True,
                has_exact_fill_prices=True,
                has_complete_fees=True,
                matching_evidence_json=json.dumps(matching),
                evidence_summary_json="{}",
                completeness_score=Decimal("1"),
                completeness_status=spec.get("completeness_status", "exact"),
                completeness_json=json.dumps(
                    {"left_boundary_verified": verified}
                ),
                provenance_json="{}",
            )
            session.add(episode)
            session.flush()
            episode_ids.append(int(episode.id))
        build_id = int(build.id)
    return build_id, episode_ids


def _verified_spec(
    *,
    day: int,
    pnl: str,
    direction: str = "long",
    underlying: str = "SYN",
) -> dict:
    return {
        "underlying": underlying,
        "direction": direction,
        "opened_at": datetime(2026, 7, day, 10, 0, tzinfo=ET),
        "realized_pnl_net": Decimal(pnl),
        "verified": True,
    }


def _bucket_map(result) -> dict:
    return {
        (
            bucket.group_kind,
            bucket.group_value,
            bucket.direction,
            bucket.boundary_policy,
        ): bucket
        for bucket in result.buckets
    }


def test_returns_none_without_any_build():
    assert get_latest_review_insights() is None


def test_without_annotations_everything_is_one_unreviewed_count():
    _seed_statement_build()
    result = get_latest_review_insights()

    assert result is not None
    assert result.buckets == ()
    assert result.total_episode_count == 4
    assert result.annotated_episode_count == 0
    assert result.unreviewed.episode_count == 4
    assert result.unreviewed.distinct_trading_day_count == 4
    assert result.source_kind == "csv_batch"
    assert result.min_episode_count == 10
    assert result.min_distinct_trading_day_count == 5


def test_buckets_use_latest_revision_and_key_on_kind_value_direction_boundary():
    build_id, by_underlying = _seed_statement_build()
    _annotate(
        build_id,
        by_underlying["WIN"],
        review_status="completed",
        tags=("momentum", "A+"),
        error_types=("late_entry",),
    )
    _annotate(build_id, by_underlying["LOSS"], tags=("momentum",))
    # A labeled-then-relabeled episode counts only under its latest revision.
    _annotate(build_id, by_underlying["LOSS"], tags=("chase",))
    # An annotation without labels joins no bucket but is annotated.
    _annotate(build_id, by_underlying["MID"], setup_thesis="无标签复盘")

    result = get_latest_review_insights()
    assert result is not None
    assert result.annotated_episode_count == 3
    assert result.unreviewed.episode_count == 1
    buckets = _bucket_map(result)
    assert set(buckets) == {
        ("tag", "momentum", "LONG", BOUNDARY_POLICY_ASSUMED_OR_CENSORED),
        ("tag", "A+", "LONG", BOUNDARY_POLICY_ASSUMED_OR_CENSORED),
        ("tag", "chase", "LONG", BOUNDARY_POLICY_ASSUMED_OR_CENSORED),
        ("error_type", "late_entry", "LONG", BOUNDARY_POLICY_ASSUMED_OR_CENSORED),
    }
    momentum = buckets[
        ("tag", "momentum", "LONG", BOUNDARY_POLICY_ASSUMED_OR_CENSORED)
    ]
    assert momentum.episode_count == 1
    assert momentum.review_completed_count == 1
    # Statement builds are assumed-flat, so no member can carry verified P&L.
    assert momentum.verified_episode_count == 0
    assert momentum.conditional_episode_count == 1
    assert momentum.stats is None
    assert momentum.stats_gate_eligible is False
    assert momentum.stats_gate_reason == STATS_GATE_NO_VERIFIED_PNL


def test_stats_appear_exactly_at_ten_episodes_and_five_days():
    # 10 verified episodes over exactly 5 distinct ET days: 6 wins, 4 losses.
    specs = [
        _verified_spec(day=6 + (index % 5), pnl="10" if index < 6 else "-5")
        for index in range(10)
    ]
    build_id, episode_ids = _seed_synthetic_build(specs)
    for episode_id in episode_ids:
        _annotate(
            build_id,
            episode_id,
            review_status="completed",
            tags=("breakout",),
        )

    result = get_latest_review_insights()
    assert result is not None
    bucket = _bucket_map(result)[
        ("tag", "breakout", "LONG", BOUNDARY_POLICY_VERIFIED)
    ]
    assert bucket.episode_count == 10
    assert bucket.distinct_trading_day_count == 5
    assert bucket.review_completed_count == 10
    assert bucket.verified_episode_count == 10
    assert bucket.verified_distinct_trading_day_count == 5
    assert bucket.conditional_episode_count == 0
    assert bucket.stats_gate_eligible is True
    assert bucket.stats_gate_reason is None
    assert bucket.stats is not None
    assert bucket.stats.win_rate == Decimal("0.6000")
    assert bucket.stats.win_count == 6
    assert bucket.stats.loss_count == 4
    assert bucket.stats.breakeven_count == 0
    assert bucket.stats.sum_pnl == Decimal("40.0000000000")
    assert bucket.stats.avg_pnl == Decimal("4.0000000000")


def test_stats_fail_closed_below_episode_threshold():
    specs = [
        _verified_spec(day=6 + (index % 5), pnl="10") for index in range(9)
    ]
    build_id, episode_ids = _seed_synthetic_build(specs)
    for episode_id in episode_ids:
        _annotate(build_id, episode_id, tags=("breakout",))

    result = get_latest_review_insights()
    assert result is not None
    bucket = _bucket_map(result)[
        ("tag", "breakout", "LONG", BOUNDARY_POLICY_VERIFIED)
    ]
    assert bucket.episode_count == 9
    assert bucket.verified_episode_count == 9
    assert bucket.verified_distinct_trading_day_count == 5
    assert bucket.stats is None
    assert bucket.stats_gate_eligible is False
    assert bucket.stats_gate_reason == STATS_GATE_BELOW_SAMPLE_THRESHOLD


def test_stats_fail_closed_below_distinct_trading_day_threshold():
    specs = [
        _verified_spec(day=6 + (index % 4), pnl="10") for index in range(10)
    ]
    build_id, episode_ids = _seed_synthetic_build(specs)
    for episode_id in episode_ids:
        _annotate(build_id, episode_id, tags=("breakout",))

    result = get_latest_review_insights()
    assert result is not None
    bucket = _bucket_map(result)[
        ("tag", "breakout", "LONG", BOUNDARY_POLICY_VERIFIED)
    ]
    assert bucket.episode_count == 10
    assert bucket.verified_episode_count == 10
    assert bucket.verified_distinct_trading_day_count == 4
    assert bucket.stats is None
    assert bucket.stats_gate_reason == STATS_GATE_BELOW_SAMPLE_THRESHOLD


def test_conditional_pnl_never_enters_stats_even_above_threshold():
    specs = [
        _verified_spec(day=6 + (index % 5), pnl="10" if index < 6 else "-5")
        for index in range(10)
    ]
    # Verified boundary but group-scope fees: conditional despite huge P&L.
    specs.append(
        {
            "underlying": "SYN",
            "direction": "long",
            "opened_at": datetime(2026, 7, 20, 10, 0, tzinfo=ET),
            "realized_pnl_net": Decimal("1000"),
            "verified": True,
            "group_fee_unallocated": True,
        }
    )
    # Verified boundary, still open: no realized result yet.
    specs.append(
        {
            "underlying": "SYN",
            "direction": "long",
            "opened_at": datetime(2026, 7, 21, 10, 0, tzinfo=ET),
            "realized_pnl_net": None,
            "verified": True,
            "lifecycle_status": "open",
        }
    )
    # Assumed-flat member with the same tag must land in a separate bucket.
    specs.append(
        {
            "underlying": "SYN",
            "direction": "long",
            "opened_at": datetime(2026, 7, 22, 10, 0, tzinfo=ET),
            "realized_pnl_net": Decimal("500"),
            "verified": False,
        }
    )
    # A short verified member with the same tag must not mix directions.
    specs.append(
        _verified_spec(day=23, pnl="10", direction="short"),
    )
    build_id, episode_ids = _seed_synthetic_build(specs)
    for episode_id in episode_ids:
        _annotate(build_id, episode_id, tags=("breakout",))

    result = get_latest_review_insights()
    assert result is not None
    buckets = _bucket_map(result)
    assert set(buckets) == {
        ("tag", "breakout", "LONG", BOUNDARY_POLICY_VERIFIED),
        ("tag", "breakout", "LONG", BOUNDARY_POLICY_ASSUMED_OR_CENSORED),
        ("tag", "breakout", "SHORT", BOUNDARY_POLICY_VERIFIED),
    }
    verified_long = buckets[
        ("tag", "breakout", "LONG", BOUNDARY_POLICY_VERIFIED)
    ]
    assert verified_long.episode_count == 12
    assert verified_long.verified_episode_count == 10
    assert verified_long.conditional_episode_count == 2
    assert verified_long.stats_gate_eligible is True
    assert verified_long.stats is not None
    # The +1000 conditional and the open member never touch the ratios.
    assert verified_long.stats.sum_pnl == Decimal("40.0000000000")
    assert verified_long.stats.win_count == 6
    assumed_long = buckets[
        ("tag", "breakout", "LONG", BOUNDARY_POLICY_ASSUMED_OR_CENSORED)
    ]
    assert assumed_long.episode_count == 1
    assert assumed_long.verified_episode_count == 0
    assert assumed_long.stats is None
    assert assumed_long.stats_gate_reason == STATS_GATE_NO_VERIFIED_PNL
    short_bucket = buckets[
        ("tag", "breakout", "SHORT", BOUNDARY_POLICY_VERIFIED)
    ]
    assert short_bucket.episode_count == 1
    assert short_bucket.stats is None
    assert short_bucket.stats_gate_reason == STATS_GATE_BELOW_SAMPLE_THRESHOLD
    # Buckets are ordered by member count, largest observation first.
    assert result.buckets[0] is verified_long


def _table_row_counts() -> dict[str, int]:
    db = get_db()
    with db.session_scope() as session:
        connection = session.connection()
        names = [
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            name: int(
                connection.exec_driver_sql(
                    f'SELECT COUNT(*) FROM "{name}"'
                ).scalar()
            )
            for name in names
        }


def test_aggregation_is_zero_write():
    build_id, by_underlying = _seed_statement_build()
    _annotate(
        build_id,
        by_underlying["WIN"],
        review_status="completed",
        tags=("momentum",),
    )
    before = _table_row_counts()

    first = get_latest_review_insights()
    second = get_latest_review_insights()

    assert first is not None and second is not None
    assert _table_row_counts() == before
    assert [
        (b.group_kind, b.group_value, b.direction, b.boundary_policy)
        for b in first.buckets
    ] == [
        (b.group_kind, b.group_value, b.direction, b.boundary_policy)
        for b in second.buckets
    ]

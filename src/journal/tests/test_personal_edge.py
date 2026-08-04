# -*- coding: utf-8 -*-
"""Zero-write personal-edge aggregation (个人画像回灌) bucket math."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest

from src.journal.ledger.models import (
    EpisodeBuild,
    PositionEpisode,
    StrategyEpisode,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.ledger.episode_repository import EpisodeRepositoryError
from src.journal.personal_edge import (
    DISCIPLINE_BODY_MIN_EPISODE_COUNT,
    DISCIPLINE_EXCLUDE_TOP_N,
    FILL_DETAILED_GOVERNS,
    INTRADAY_LANE_DAILY_TICKET_LIMIT,
    INTRADAY_LANE_DTE,
    INTRADAY_LANE_ET_CUTOFF_HOUR,
    MONTH_BASIS_UTC_MINUS_4,
    OVERNIGHT_LANE_CONCURRENT_LIMIT,
    OVERNIGHT_LANE_MAX_DTE,
    OVERNIGHT_LANE_MIN_DTE,
    OVERNIGHT_LANE_WEAK_ENTRY_ET_HOURS,
    PERSONAL_EDGE_LIMITATIONS,
    REASON_NO_DTE,
    REASON_NO_PREMIUM,
    REASON_ZERO_PREMIUM,
    RULE_COMPLIANCE_CLEAN_BASIS_START,
    RULE_COMPLIANCE_LANE_RULE_IDS,
    RULE_COMPLIANCE_LANE_VERDICTS,
    RULE_COMPLIANCE_LIMITATIONS,
    RULE_LANES,
    RULE_SET_V2_ADOPTED_AT,
    RULE_VERDICTS,
    classify_rule_lane,
    fill_detailed_from_evidence_summary,
    get_personal_edge_stats,
)
from src.storage import get_db


_BUILD_SEQUENCE = 0


def _seed_build(
    specs: list[dict],
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> int:
    """Append-only INSERTs shaped like stored builder output (no links).

    Without canonical/fence source links or an activation row, the shared
    ``_resolve_effective_episode_build`` treats this as the default CSV
    build — the exact resolution the production reads reuse.
    """
    init_ledger_schema()
    db = get_db()
    global _BUILD_SEQUENCE
    _BUILD_SEQUENCE += 1
    with db.session_scope() as session:
        build = EpisodeBuild(
            # build_key 全局唯一：同一测试内可为不同账户各播一个构建。
            build_key=f"synthetic-personal-edge-{account_key}-{_BUILD_SEQUENCE}",
            broker="moomoo",
            account_key=account_key,
            builder_name="synthetic-personal-edge-test",
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
        for index, spec in enumerate(specs):
            lifecycle_status = spec.get("lifecycle_status", "closed")
            opened_at: datetime = spec["opened_at"].astimezone(timezone.utc)
            hold_seconds: Optional[int] = spec.get("hold_seconds", 7_200)
            closed_at = (
                opened_at + timedelta(seconds=hold_seconds or 7_200)
                if lifecycle_status == "closed"
                else None
            )
            pnl = spec.get("realized_pnl_net")
            strategy = StrategyEpisode(
                episode_build_id=build.id,
                episode_key=f"strategy-{index}",
                lineage_key=f"lineage-{index}",
                broker="moomoo",
                account_key=account_key,
                underlying=spec.get("underlying", "SYN"),
                strategy_type="single_position_unclassified",
                direction="long",
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
            session.add(
                PositionEpisode(
                    episode_build_id=build.id,
                    strategy_episode_id=strategy.id,
                    episode_key=f"position-{index}",
                    lineage_key=f"lineage-{index}",
                    broker="moomoo",
                    account_key=account_key,
                    raw_symbol=spec.get("underlying", "SYN") + "260918C100000",
                    asset_type="option",
                    underlying=spec.get("underlying", "SYN"),
                    direction="long",
                    lifecycle_status=lifecycle_status,
                    currency="USD",
                    opened_at=opened_at,
                    closed_at=closed_at,
                    hold_seconds=(
                        hold_seconds if lifecycle_status == "closed" else None
                    ),
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
                    total_fee=spec.get("total_fee"),
                    realized_pnl_net=pnl,
                    dte_at_entry=spec.get("dte_at_entry"),
                    opening_cash_flow=spec.get("opening_cash_flow"),
                    construction_basis="fills",
                    is_left_censored=False,
                    is_right_censored=False,
                    has_exact_fill_times=spec.get("has_exact_fill_times", True),
                    has_exact_fill_prices=True,
                    has_complete_fees=True,
                    matching_evidence_json=json.dumps(
                        {
                            "method": "signed_position_zero_crossing",
                            "opening_boundary_policy": "verified_trade_flow",
                            "contract_multiplier_basis": (
                                "amount_quantity_price_proof"
                            ),
                        }
                    ),
                    # 默认＝由明细成交构建（fill_allocations>0）；spec 可覆盖成
                    # 汇总 ORDER 口径或任意畸形形状，用于口径来源判据的边界测试。
                    evidence_summary_json=spec.get(
                        "evidence_summary_json",
                        '{"allocation_count":3,"fill_allocations":3,'
                        '"order_allocations":0}',
                    ),
                    completeness_score=Decimal("1"),
                    completeness_status="exact",
                    completeness_json="{}",
                    provenance_json="{}",
                )
            )
        session.flush()
        return int(build.id)


def _april(day: int, hour: int = 14) -> datetime:
    return datetime(2026, 4, day, hour, 0, tzinfo=timezone.utc)


def test_no_build_returns_none():
    init_ledger_schema()
    assert get_personal_edge_stats() is None


def test_bucket_math_thresholds_and_exclusions():
    # AAA reaches the n>=5 gate; BBB (4 closed) stays below it.
    specs = [
        # AAA: 3 wins / 2 losses across five hold buckets and five DTE buckets.
        {
            "underlying": "AAA", "opened_at": _april(1), "hold_seconds": 300,
            "realized_pnl_net": Decimal("100"), "total_fee": Decimal("1"),
            "dte_at_entry": 0,
        },
        {
            "underlying": "AAA", "opened_at": _april(2), "hold_seconds": 900,
            "realized_pnl_net": Decimal("-50"), "total_fee": Decimal("1"),
            "dte_at_entry": 2,
        },
        {
            "underlying": "AAA", "opened_at": _april(3), "hold_seconds": 2_400,
            "realized_pnl_net": Decimal("200"), "total_fee": Decimal("1"),
            "dte_at_entry": 5,
        },
        {
            "underlying": "AAA", "opened_at": _april(6), "hold_seconds": 7_200,
            "realized_pnl_net": Decimal("-30"), "total_fee": Decimal("1"),
            "dte_at_entry": 10,
        },
        {
            "underlying": "AAA", "opened_at": _april(7), "hold_seconds": 90_000,
            "realized_pnl_net": Decimal("60"), "total_fee": Decimal("1"),
            "dte_at_entry": 40,
        },
        # BBB: below the underlying threshold; still enters bucket stats.
        {
            "underlying": "BBB", "opened_at": _april(8), "hold_seconds": 300,
            "realized_pnl_net": Decimal("-10"), "total_fee": None,
            "dte_at_entry": None,
        },
        {
            "underlying": "BBB", "opened_at": _april(9), "hold_seconds": 300,
            "realized_pnl_net": Decimal("-10"), "total_fee": Decimal("2"),
            "dte_at_entry": 0,
        },
        {
            "underlying": "BBB", "opened_at": _april(10), "hold_seconds": 13_000,
            "realized_pnl_net": Decimal("20"), "total_fee": Decimal("2"),
            "dte_at_entry": 1,
        },
        {
            "underlying": "BBB", "opened_at": _april(13), "hold_seconds": 30_000,
            "realized_pnl_net": Decimal("20"), "total_fee": Decimal("2"),
            "dte_at_entry": 3,
        },
        # Excluded rows: still-open and closed-without-P&L episodes.
        {
            "underlying": "AAA", "opened_at": _april(14),
            "lifecycle_status": "open", "realized_pnl_net": None,
        },
        {
            "underlying": "AAA", "opened_at": _april(15), "hold_seconds": 600,
            "realized_pnl_net": None, "total_fee": Decimal("9"),
            "dte_at_entry": 0,
        },
    ]
    build_id = _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    assert result.build_id == build_id
    assert result.source_kind == "csv_batch"
    assert result.closed_episode_count == 9
    assert result.excluded_open_count == 1
    assert result.excluded_missing_pnl_count == 1
    assert result.first_opened_at == _april(1)
    assert result.last_closed_at == _april(13) + timedelta(seconds=30_000)

    # Per-underlying gate: only AAA (n=5) qualifies; BBB reported as excluded.
    assert [item.underlying for item in result.underlyings] == ["AAA"]
    aaa = result.underlyings[0]
    assert aaa.n == 5
    assert aaa.net == pytest.approx(280.0)
    assert aaa.win_rate == pytest.approx(0.6)
    assert aaa.fees == pytest.approx(5.0)
    assert result.small_sample_underlying_count == 1

    hold = {item.bucket: item for item in result.hold_time_buckets}
    assert set(hold) == {
        "<10m", "10-30m", "30-60m", "1-3h", "3-6h", "6h-1d", ">1d",
    }
    assert (hold["<10m"].n, hold["<10m"].net) == (3, pytest.approx(80.0))
    assert hold["<10m"].win_rate == pytest.approx(0.3333)
    assert hold["<10m"].avg_win == pytest.approx(100.0)
    assert hold["<10m"].avg_loss == pytest.approx(-10.0)
    assert (hold["10-30m"].n, hold["10-30m"].net) == (1, pytest.approx(-50.0))
    assert hold["10-30m"].avg_win is None
    assert (hold["30-60m"].n, hold["30-60m"].net) == (1, pytest.approx(200.0))
    assert (hold["1-3h"].n, hold["1-3h"].net) == (1, pytest.approx(-30.0))
    assert (hold["3-6h"].n, hold["3-6h"].net) == (1, pytest.approx(20.0))
    assert (hold["6h-1d"].n, hold["6h-1d"].net) == (1, pytest.approx(20.0))
    assert (hold[">1d"].n, hold[">1d"].net) == (1, pytest.approx(60.0))
    # Empty bucket keeps n=0 and null ratios — never zero-filled rates.
    assert result.hold_unknown_count == 0

    dte = {item.bucket: item for item in result.dte_buckets}
    assert set(dte) == {"0", "1-3", "4-7", "8-30", ">30"}
    assert (dte["0"].n, dte["0"].net) == (2, pytest.approx(90.0))
    assert dte["0"].win_rate == pytest.approx(0.5)
    assert (dte["1-3"].n, dte["1-3"].net) == (3, pytest.approx(-10.0))
    assert dte["1-3"].win_rate == pytest.approx(0.6667)
    assert (dte["4-7"].n, dte["4-7"].net) == (1, pytest.approx(200.0))
    assert (dte["8-30"].n, dte["8-30"].net) == (1, pytest.approx(-30.0))
    assert (dte[">30"].n, dte[">30"].net) == (1, pytest.approx(60.0))
    # Missing DTE is reported separately, never folded into 0DTE.
    assert result.dte_unknown.n == 1
    assert result.dte_unknown.net == pytest.approx(-10.0)

    assert [item.month for item in result.monthly] == ["2026-04"]
    month = result.monthly[0]
    assert month.n == 9
    assert month.net == pytest.approx(300.0)
    # AAA 5×$1 + BBB $2×3（首笔 fee 缺失按缺失处理，不以 0 冒充→不计）。
    assert month.fees == pytest.approx(11.0)
    assert month.win_rate == pytest.approx(0.5556)

    assert result.month_basis == MONTH_BASIS_UTC_MINUS_4
    assert result.limitations == PERSONAL_EDGE_LIMITATIONS
    assert "内生性" in result.limitations[0]
    assert "UTC−4" in result.limitations[1]


def test_monthly_bucket_uses_utc_minus_4_approximation():
    # 01:00 UTC on May 1 is still April 21:00 under the ET≈UTC−4 label.
    specs = [
        {
            "underlying": "AAA",
            "opened_at": datetime(2026, 5, 1, 1, 0, tzinfo=timezone.utc),
            "hold_seconds": 600,
            "realized_pnl_net": Decimal("10"),
            "total_fee": Decimal("1"),
            "dte_at_entry": 0,
        },
        {
            "underlying": "AAA",
            "opened_at": datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
            "hold_seconds": 600,
            "realized_pnl_net": Decimal("-5"),
            "total_fee": Decimal("1"),
            "dte_at_entry": 0,
        },
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    assert [
        (item.month, item.n, item.net) for item in result.monthly
    ] == [("2026-04", 1, pytest.approx(10.0)), ("2026-05", 1, pytest.approx(-5.0))]


def test_min_underlying_episode_count_must_be_positive():
    from src.journal.ledger.episode_repository import EpisodeRepositoryError

    with pytest.raises(EpisodeRepositoryError):
        get_personal_edge_stats(min_underlying_episode_count=0)


# --- 规模与频率纪律（discipline）-------------------------------------------


def _evidence_summary(fill_allocations: int, order_allocations: int = 0) -> str:
    return json.dumps(
        {
            "allocation_count": fill_allocations + order_allocations,
            "fill_allocations": fill_allocations,
            "order_allocations": order_allocations,
        }
    )


def _discipline_spec(
    opened_at: datetime,
    pnl: str,
    *,
    opening_cash_flow: Optional[str] = None,
    dte: Optional[int] = 1,
    exact: bool = True,
    fee: Optional[str] = "1",
    evidence_summary_json: Optional[str] = None,
) -> dict:
    spec = {
        "underlying": "AAA",
        "opened_at": opened_at,
        "hold_seconds": 1_200,
        "realized_pnl_net": Decimal(pnl),
        "total_fee": Decimal(fee) if fee is not None else None,
        "dte_at_entry": dte,
        "opening_cash_flow": (
            Decimal(opening_cash_flow) if opening_cash_flow is not None else None
        ),
        "has_exact_fill_times": exact,
    }
    if evidence_summary_json is not None:
        spec["evidence_summary_json"] = evidence_summary_json
    return spec


# --- 口径来源判据（fill_allocations）---------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # 生产形状：明细成交 vs 纯汇总 ORDER 行。
        ('{"allocation_count":6,"fill_allocations":6,"order_allocations":0}', True),
        ('{"allocation_count":2,"fill_allocations":0,"order_allocations":2}', False),
        # build #3 有 2 行带额外 key，必须照常解析，不能因为多字段判成未知。
        (
            '{"allocation_count":2,"fill_allocations":2,"group_fee_unallocated":true,'
            '"order_allocations":0}',
            True,
        ),
        # 以下一律 fail closed 到「未知」，绝不当作全明细。
        (None, None),
        ("", None),
        ("   ", None),
        ("not json", None),
        ("[]", None),
        ("null", None),
        ('"fill_allocations"', None),
        ("{}", None),  # 旧行与既有 fixture 写的就是空对象
        ('{"allocation_count":2,"order_allocations":2}', None),
        ('{"fill_allocations":null}', None),
        ('{"fill_allocations":"3"}', None),
        ('{"fill_allocations":1.5}', None),
        ('{"fill_allocations":-1}', None),
        # bool 是 int 的子类：True 绝不能被读成「1 笔明细成交」。
        ('{"fill_allocations":true}', None),
        ('{"fill_allocations":false}', None),
    ],
)
def test_fill_detailed_discriminator_shape_edge_cases(raw, expected):
    """`fill_allocations == 0` 才是「由汇总 ORDER 行构建」的判据，形状异常一律未知。"""
    assert fill_detailed_from_evidence_summary(raw) is expected


def test_fill_detailed_discriminator_accepts_dict_and_bytes():
    assert fill_detailed_from_evidence_summary({"fill_allocations": 4}) is True
    assert fill_detailed_from_evidence_summary({"fill_allocations": 0}) is False
    assert fill_detailed_from_evidence_summary(b'{"fill_allocations":2}') is True
    assert fill_detailed_from_evidence_summary(b"\xff\xfe") is None


def test_fill_detailed_share_disagrees_with_exact_fill_share_and_is_surfaced():
    """两个字段会不一致（build #3 有 3 笔 4 月回合如此）——差异必须暴露，不能藏。

    构造：4 笔均由明细成交构建（fill_allocations>0），其中 1 笔
    ``has_exact_fill_times=0``。口径可比性看 fill_detailed_share（=1.0，不断裂），
    历史连续性看 exact_fill_share（=0.75）。断裂判定必须只听前者。
    """
    specs = [
        _discipline_spec(
            _april(day, 14),
            "10",
            opening_cash_flow="100",
            exact=(day != 6),
            evidence_summary_json=_evidence_summary(3),
        )
        for day in (6, 7, 8, 9)
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    stats = result.discipline.monthly[0].stats

    assert stats.fill_detailed_share == pytest.approx(1.0)
    assert stats.exact_fill_share == pytest.approx(0.75)
    # 不一致时以 fill_detailed_share 为准：口径没断，只是时点有重建。
    assert stats.basis_break is False
    assert stats.basis_break_reason is None
    assert stats.has_reconstructed_fills is True
    assert stats.fill_detailed_count == 4
    assert stats.aggregate_only_count == 0
    assert stats.fill_provenance_unknown_count == 0


def test_basis_break_flags_aggregate_only_and_unknown_provenance_months():
    """任一非明细成交回合即置位 basis_break，并在原因里说清成因与笔数。"""
    specs = [
        # 4 月：2 笔汇总 ORDER 行（fill_allocations=0）+ 2 笔明细成交。
        _discipline_spec(
            _april(6, 14),
            "10",
            opening_cash_flow="100",
            evidence_summary_json=_evidence_summary(0, 2),
        ),
        _discipline_spec(
            _april(7, 14),
            "10",
            opening_cash_flow="100",
            evidence_summary_json=_evidence_summary(0, 3),
        ),
        _discipline_spec(
            _april(8, 14),
            "10",
            opening_cash_flow="100",
            evidence_summary_json=_evidence_summary(3),
        ),
        _discipline_spec(
            _april(9, 14),
            "10",
            opening_cash_flow="100",
            evidence_summary_json=_evidence_summary(3),
        ),
        # 5 月：1 笔口径来源不可判定（空对象）+ 1 笔明细成交 → 同样不可认证为干净。
        _discipline_spec(
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            "10",
            opening_cash_flow="100",
            evidence_summary_json="{}",
        ),
        _discipline_spec(
            datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc),
            "10",
            opening_cash_flow="100",
            evidence_summary_json=_evidence_summary(3),
        ),
        # 6 月：全部明细成交 → 唯一干净月份。
        _discipline_spec(
            datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc),
            "10",
            opening_cash_flow="100",
            evidence_summary_json=_evidence_summary(2),
        ),
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    by_month = {item.month: item.stats for item in result.discipline.monthly}

    april = by_month["2026-04"]
    assert april.basis_break is True
    assert april.aggregate_only_count == 2
    assert april.fill_detailed_count == 2
    assert april.fill_detailed_share == pytest.approx(0.5)
    assert "汇总 ORDER 行" in april.basis_break_reason
    assert "fill_allocations=0" in april.basis_break_reason
    assert "audit_only_not_execution_cash_flow" in april.basis_break_reason
    assert "2/4" in april.basis_break_reason

    # 来源不可判定同样 fail closed：不可认证为全明细，就必须断开。
    may = by_month["2026-05"]
    assert may.basis_break is True
    assert may.fill_provenance_unknown_count == 1
    assert may.aggregate_only_count == 0
    assert may.fill_detailed_share == pytest.approx(0.5)
    assert "不可判定" in may.basis_break_reason

    june = by_month["2026-06"]
    assert june.basis_break is False
    assert june.basis_break_reason is None
    assert june.fill_detailed_share == pytest.approx(1.0)


# --- 费用门槛与毛口径 -------------------------------------------------------


def test_fee_and_gross_pct_share_the_denominator_and_reconcile():
    """毛 = 净 + 费用，三者同分母——恒等式必须在同一行上严格成立。"""
    specs = [
        _discipline_spec(_april(6, 14), "100", opening_cash_flow="1000", fee="20"),
        _discipline_spec(_april(7, 14), "-40", opening_cash_flow="1000", fee="30"),
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    stats = result.discipline.monthly[0].stats

    assert stats.fees_total == pytest.approx(50.0)
    assert stats.fees_missing_count == 0
    # 净 60/2000 = 3%；费用 50/2000 = 2.5%；毛 110/2000 = 5.5%。
    assert stats.pnl_per_dollar_risked == pytest.approx(0.03)
    assert stats.fee_pct_of_premium_at_risk == pytest.approx(0.025)
    assert stats.gross_pct_of_premium_at_risk == pytest.approx(0.055)
    assert stats.gross_pct_of_premium_at_risk == pytest.approx(
        stats.pnl_per_dollar_risked + stats.fee_pct_of_premium_at_risk
    )
    assert stats.fee_pct_of_premium_at_risk_reason is None
    assert stats.gross_pct_of_premium_at_risk_reason is None


def test_fee_and_gross_pct_fail_closed_on_zero_and_missing_denominators():
    """分母为 0 / 无现金流 / 缺 total_fee：一律 null + 可区分的原因。"""
    # (a) 风险金额合计为 0。
    _seed_build(
        [
            _discipline_spec(_april(6, 14), "5", opening_cash_flow="0"),
            _discipline_spec(_april(7, 14), "-5", opening_cash_flow="0"),
        ],
        account_key="fee_zero_premium",
    )
    zero = get_personal_edge_stats("fee_zero_premium")
    assert zero is not None
    zero_stats = zero.discipline.monthly[0].stats
    assert zero_stats.fee_pct_of_premium_at_risk is None
    assert zero_stats.gross_pct_of_premium_at_risk is None
    assert zero_stats.fee_pct_of_premium_at_risk_reason == REASON_ZERO_PREMIUM
    assert zero_stats.gross_pct_of_premium_at_risk_reason == REASON_ZERO_PREMIUM

    # (b) 完全没有开仓现金流。
    _seed_build(
        [_discipline_spec(_april(6, 14), "5")],
        account_key="fee_no_premium",
    )
    none_premium = get_personal_edge_stats("fee_no_premium")
    assert none_premium is not None
    np_stats = none_premium.discipline.monthly[0].stats
    assert np_stats.fee_pct_of_premium_at_risk is None
    assert np_stats.fee_pct_of_premium_at_risk_reason == REASON_NO_PREMIUM

    # (c) 缺 total_fee：过路费绝不能以 0 冒充，整体 fail-closed。
    _seed_build(
        [
            _discipline_spec(_april(6, 14), "10", opening_cash_flow="100", fee="2"),
            _discipline_spec(_april(7, 14), "10", opening_cash_flow="100", fee=None),
        ],
        account_key="fee_missing",
    )
    missing = get_personal_edge_stats("fee_missing")
    assert missing is not None
    m_stats = missing.discipline.monthly[0].stats
    assert m_stats.fees_missing_count == 1
    assert m_stats.fees_total is None
    assert m_stats.fee_pct_of_premium_at_risk is None
    assert m_stats.gross_pct_of_premium_at_risk is None
    assert "total_fee" in m_stats.fee_pct_of_premium_at_risk_reason
    assert "1/2" in m_stats.fee_pct_of_premium_at_risk_reason
    # 净口径不依赖费用，仍然成立。
    assert m_stats.pnl_per_dollar_risked == pytest.approx(0.1)


# --- 剔除最好 N 笔后的每美元回报 --------------------------------------------


def test_excluding_top_n_math_and_fifteen_episode_gate():
    """剔除最好 5 笔＝同一子集上分子分母同时去尾；<15 笔显式 null + 原因。"""
    specs = [
        # 5 月 15 笔：14 笔 −10（各 100 风险），1 笔尾部赢家 +1000（100 风险）。
        _discipline_spec(
            datetime(2026, 5, day, 14, 0, tzinfo=timezone.utc),
            "-10",
            opening_cash_flow="100",
            fee="1",
        )
        for day in range(1, 15)
    ] + [
        _discipline_spec(
            datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc),
            "1000",
            opening_cash_flow="100",
            fee="1",
        ),
    ] + [
        # 6 月 14 笔：不足门槛。
        _discipline_spec(
            datetime(2026, 6, day, 14, 0, tzinfo=timezone.utc),
            "10",
            opening_cash_flow="100",
            fee="1",
        )
        for day in range(1, 15)
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    by_month = {item.month: item.stats for item in result.discipline.monthly}

    may = by_month["2026-05"]
    assert may.n == 15
    # 含尾部赢家：(1000 - 140) / 1500 = +5.733%。
    assert may.pnl_per_dollar_risked == pytest.approx(0.573333, abs=1e-6)
    # 去掉最好的 5 笔（+1000 与 4 笔 −10）后剩 10 笔 −10，分母同步降到 1000。
    assert may.excluding_top_n_count == 10
    assert may.pnl_per_dollar_excluding_top_n == pytest.approx(-0.1)
    # 毛口径把 10 笔 × $1 费用加回：(−100 + 10) / 1000 = −9%。
    assert may.gross_pct_excluding_top_n == pytest.approx(-0.09)
    assert may.excluding_top_n_reason is None
    # 一笔就把整月每美元回报从 −10% 翻成 +57%：尾部集中度必须能被看见。
    assert may.pnl_per_dollar_risked > 0 > may.pnl_per_dollar_excluding_top_n

    june = by_month["2026-06"]
    assert june.n == 14
    assert june.pnl_per_dollar_excluding_top_n is None
    assert june.gross_pct_excluding_top_n is None
    assert june.excluding_top_n_count is None
    assert "14" in june.excluding_top_n_reason
    assert "15" in june.excluding_top_n_reason


def test_excluding_top_n_gate_counts_only_premium_known_episodes():
    """门槛按「风险金额已知」的样本数判定：分子分母必须同一子集。"""
    specs = [
        _discipline_spec(
            datetime(2026, 5, day, 14, 0, tzinfo=timezone.utc),
            "10",
            opening_cash_flow="100",
        )
        for day in range(1, 15)  # 14 笔有风险金额
    ] + [
        _discipline_spec(
            datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc),
            "10",
            opening_cash_flow=None,  # 第 15 笔缺风险金额，不能凑数
        ),
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    stats = result.discipline.monthly[0].stats
    assert stats.n == 15
    assert stats.premium_known_count == 14
    assert stats.pnl_per_dollar_excluding_top_n is None
    assert "14" in stats.excluding_top_n_reason


def test_discipline_block_publishes_exclude_top_n_and_governing_field():
    _seed_build([_discipline_spec(_april(6, 14), "10", opening_cash_flow="100")])
    result = get_personal_edge_stats()
    assert result is not None
    assert result.discipline.exclude_top_n == result.discipline.body_trim_count
    assert result.discipline.fill_detailed_governs == FILL_DETAILED_GOVERNS
    assert "fill_detailed_share" in result.discipline.fill_detailed_governs
    assert "has_exact_fill_times" in result.discipline.fill_detailed_governs


def test_discipline_monthly_ratios_and_et_day_counting():
    """日均笔数按 ET≈UTC−4 自然日去重；每美元回报＝净盈亏 ÷ Σ|开仓现金流|。"""
    specs = [
        # 同一 ET 交易日（04-06）的两笔，只算一个交易日。
        _discipline_spec(_april(6, 14), "300", opening_cash_flow="-1000", dte=0),
        _discipline_spec(_april(6, 18), "-100", opening_cash_flow="-1000", dte=0),
        # 贷方开仓（正现金流）按绝对值计风险金额；重建成交明细。
        _discipline_spec(
            _april(7, 14), "50", opening_cash_flow="2000", dte=5, exact=False
        ),
        # 04-08 03:00 UTC → ET 04-07 23:00：仍归入 04-07 这个交易日。
        _discipline_spec(
            datetime(2026, 4, 8, 3, 0, tzinfo=timezone.utc), "-50", dte=None
        ),
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None

    assert [item.month for item in result.discipline.monthly] == ["2026-04"]
    stats = result.discipline.monthly[0].stats
    assert stats.n == 4
    assert stats.trading_day_count == 2
    assert stats.trades_per_day == pytest.approx(2.0)
    assert stats.trades_per_day_reason is None
    # 开仓现金流缺失只计缺席，绝不以 0 拉低中位仓位。
    assert (stats.premium_known_count, stats.premium_missing_count) == (3, 1)
    assert stats.median_premium_at_risk == pytest.approx(1000.0)
    assert stats.total_premium_at_risk == pytest.approx(4000.0)
    assert stats.net_pnl == pytest.approx(200.0)
    assert stats.pnl_per_dollar_risked == pytest.approx(0.05)
    assert stats.median_episode_pnl == pytest.approx(0.0)
    # DTE 缺失不进 0DTE 分母。
    assert stats.dte_known_count == 3
    assert stats.zero_dte_share == pytest.approx(0.6667)
    assert stats.exact_fill_share == pytest.approx(0.75)
    assert stats.has_reconstructed_fills is True


def test_discipline_body_pnl_gate_needs_fifteen_episodes():
    """本体盈亏去掉最好/最差各 5 笔；<15 笔显式 null + 原因。"""
    specs = [
        _discipline_spec(
            datetime(2026, 5, day, 14, 0, tzinfo=timezone.utc),
            str(day),
            opening_cash_flow="100",
        )
        for day in range(1, 16)  # 15 笔：P&L 1..15
    ] + [
        _discipline_spec(
            datetime(2026, 6, day, 14, 0, tzinfo=timezone.utc),
            "10",
            opening_cash_flow="100",
        )
        for day in range(1, 15)  # 14 笔：不足门槛
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    by_month = {item.month: item.stats for item in result.discipline.monthly}

    may = by_month["2026-05"]
    assert may.n == 15
    # 去掉 1..5 与 11..15 后剩 6..10 = 40。
    assert may.body_pnl == pytest.approx(40.0)
    assert may.body_episode_count == 5
    assert may.body_pnl_reason is None

    june = by_month["2026-06"]
    assert june.n == 14
    assert june.body_pnl is None
    assert june.body_episode_count is None
    assert june.body_pnl_reason is not None
    assert "14" in june.body_pnl_reason and "15" in june.body_pnl_reason


def test_discipline_current_window_takes_last_twenty_trading_days():
    """当前窗口＝build 内实际存在的最后 20 个交易日，不足即取全部。"""
    specs = [
        _discipline_spec(
            datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc) + timedelta(days=offset),
            "10" if offset % 2 else "-4",
            opening_cash_flow="100",
        )
        for offset in range(25)  # 25 个不同 ET 交易日，每日 1 笔
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    window = result.discipline.current_window

    assert window.requested_trading_days == 20
    assert window.start_date == "2026-06-06"
    assert window.end_date == "2026-06-25"
    assert window.stats.n == 20
    assert window.stats.trading_day_count == 20
    assert window.stats.trades_per_day == pytest.approx(1.0)
    assert window.stats.total_premium_at_risk == pytest.approx(2000.0)
    assert window.stats.exact_fill_share == pytest.approx(1.0)
    assert window.stats.has_reconstructed_fills is False


def test_discipline_fails_closed_on_zero_denominators():
    """无开仓现金流 / 合计为 0 / 无已知 DTE：一律 null + 原因，绝不以 0 冒充。"""
    specs = [
        _discipline_spec(_april(6, 14), "10", dte=None),
        _discipline_spec(_april(7, 14), "-10", dte=None),
    ]
    _seed_build(specs)
    result = get_personal_edge_stats()
    assert result is not None
    stats = result.discipline.monthly[0].stats
    assert stats.median_premium_at_risk is None
    assert stats.total_premium_at_risk is None
    assert stats.premium_reason == REASON_NO_PREMIUM
    assert stats.pnl_per_dollar_risked is None
    assert stats.pnl_per_dollar_risked_reason == REASON_NO_PREMIUM
    assert stats.zero_dte_share is None
    assert stats.zero_dte_reason == REASON_NO_DTE

    # 现金流已知但合计为 0：与「没有现金流」是两回事，原因必须区分。
    _seed_build(
        [
            _discipline_spec(_april(8, 14), "5", opening_cash_flow="0"),
            _discipline_spec(_april(9, 14), "-5", opening_cash_flow="0"),
        ],
        account_key="zero_premium_account",
    )
    zero = get_personal_edge_stats("zero_premium_account")
    assert zero is not None
    zero_stats = zero.discipline.monthly[0].stats
    assert zero_stats.premium_known_count == 2
    assert zero_stats.total_premium_at_risk == pytest.approx(0.0)
    assert zero_stats.pnl_per_dollar_risked is None
    assert zero_stats.pnl_per_dollar_risked_reason == REASON_ZERO_PREMIUM


def test_discipline_limitations_carry_reconstructed_fill_and_gate_notes():
    _seed_build([_discipline_spec(_april(6, 14), "10", opening_cash_flow="100")])
    result = get_personal_edge_stats()
    assert result is not None
    assert result.limitations == PERSONAL_EDGE_LIMITATIONS
    # 口径断裂的成因必须原文出现：汇总 ORDER 行 + 管线自身的 audit-only 标记。
    assert any("fill_allocations" in line for line in result.limitations)
    assert any(
        "audit_only_not_execution_cash_flow" in line for line in result.limitations
    )
    # 「哪个字段说了算」必须随响应下发，避免消费端继续用 has_exact_fill_times 判可比性。
    assert FILL_DETAILED_GOVERNS in result.limitations
    assert any("fail-closed" in line for line in result.limitations)
    # 费用门槛的语义（毛低于门槛即净为负）也要原文携带。
    assert any("门槛" in line for line in result.limitations)


# ---------------------------------------------------------------------------
# 车道遵守度（rule_compliance）：规则 v2 的机械判定 + 前向切片
# ---------------------------------------------------------------------------


def _lane(
    dte: Optional[int],
    hour: Optional[int],
    opened: Optional[str],
    closed: Optional[str],
) -> str:
    return classify_rule_lane(
        dte=dte,
        opened_et_hour=hour,
        opened_trading_day=opened,
        closed_trading_day=closed,
    )


def test_classify_rule_lane_truth_table_covers_every_boundary():
    """车道判定真值表：DTE 0/1/3/4/7/8 + 当日/隔夜边界 + ET 12:00 边界。"""
    same, later = "2026-05-04", "2026-05-05"

    # DTE 0：ET 12:00 是硬边界（11 点仍是日内合规，12 点整即违规 V2-C③）。
    assert _lane(0, 0, same, same) == "intraday_0dte"
    assert _lane(0, 9, same, same) == "intraday_0dte"
    assert _lane(0, 11, same, same) == "intraday_0dte"
    assert _lane(0, 12, same, same) == "late_0dte"
    assert _lane(0, 15, same, same) == "late_0dte"
    # 0DTE 的判定与是否跨日无关：合约当天到期，午前开仓仍算日内车道。
    assert _lane(0, 10, same, later) == "intraday_0dte"

    # DTE 1-3：任何时段、任何持有时长一律违规（V2-C①），两头都不占。
    for dte in (1, 2, 3):
        assert _lane(dte, 9, same, same) == "dte_1_3"
        assert _lane(dte, 9, same, later) == "dte_1_3"
        assert _lane(dte, 13, same, later) == "dte_1_3"

    # DTE 4 与 7 是过夜车道的两个端点（V2-B），跨自然日才成立。
    assert _lane(4, 9, same, later) == "overnight_4_7"
    assert _lane(7, 9, same, later) == "overnight_4_7"
    # 同一批合约当日平掉＝买了时间却不用（V2-C②）。
    assert _lane(4, 9, same, same) == "bought_time_unused"
    assert _lane(7, 9, same, same) == "bought_time_unused"

    # DTE 8 越过过夜车道上界：隔夜＝规则未覆盖，当日平仍是「买了时间却不用」。
    assert _lane(8, 9, same, later) == "other"
    assert _lane(8, 9, same, same) == "bought_time_unused"
    assert _lane(400, 9, same, later) == "other"

    # 缺席即缺席：缺 DTE、缺日期、缺 ET 小时、负 DTE 一律 unknown，绝不并入 other。
    assert _lane(None, 9, same, same) == "unknown"
    assert _lane(-1, 9, same, same) == "unknown"
    assert _lane(0, None, same, same) == "unknown"
    assert _lane(5, 9, same, None) == "unknown"
    assert _lane(5, 9, None, later) == "unknown"


def test_classify_rule_lane_same_day_boundary_is_calendar_not_duration():
    """当日/隔夜按 ET 自然日判定，不按持仓时长——20 小时同日仍是当日平。"""
    assert _lane(5, 4, "2026-05-04", "2026-05-04") == "bought_time_unused"
    # 只跨过一个自然日边界（哪怕只有几分钟）就是过夜。
    assert _lane(5, 23, "2026-05-04", "2026-05-05") == "overnight_4_7"


def test_classify_rule_lane_is_total_over_the_declared_lanes():
    """判定是全函数：任何输入都落在 RULE_LANES 内，且每条车道都有判定与规则号。"""
    for dte in (None, -3, 0, 1, 2, 3, 4, 5, 6, 7, 8, 45, 400):
        for hour in (None, 0, 9, 11, 12, 23):
            for closed in (None, "2026-05-04", "2026-05-05"):
                lane = _lane(dte, hour, "2026-05-04", closed)
                assert lane in RULE_LANES
    assert set(RULE_COMPLIANCE_LANE_VERDICTS) == set(RULE_LANES)
    assert set(RULE_COMPLIANCE_LANE_RULE_IDS) == set(RULE_LANES)
    assert set(RULE_COMPLIANCE_LANE_VERDICTS.values()) <= set(RULE_VERDICTS)


def _compliance_spec(
    opened_at: datetime,
    pnl: str,
    *,
    dte: int,
    hold_seconds: int = 1_200,
    opening_cash_flow: str = "1000",
    fee: Optional[str] = "1",
    evidence_summary_json: Optional[str] = None,
) -> dict:
    return {
        "underlying": "AAA",
        "opened_at": opened_at,
        "hold_seconds": hold_seconds,
        "realized_pnl_net": Decimal(pnl),
        "total_fee": Decimal(fee) if fee is not None else None,
        "dte_at_entry": dte,
        "opening_cash_flow": Decimal(opening_cash_flow),
        "evidence_summary_json": (
            evidence_summary_json
            if evidence_summary_json is not None
            else _evidence_summary(3)
        ),
    }


def _may(day: int, hour: int = 14) -> datetime:
    """UTC moment whose ET≈UTC−4 hour is ``hour - 4`` (14 UTC = 10:00 ET)."""
    return datetime(2026, 5, day, hour, 0, tzinfo=timezone.utc)


def test_rule_compliance_buckets_and_verdict_rollups():
    """车道桶与合规/违规汇总同口径：gross=净+费用，risk=|开仓现金流|。"""
    _seed_build(
        [
            # 日内合规（ET 10:00 开、0DTE）。
            _compliance_spec(_may(4), "100", dte=0),
            _compliance_spec(_may(5), "-40", dte=0),
            # 午后 0DTE（ET 13:00）＝违规 V2-C③。
            _compliance_spec(_may(6, 17), "-30", dte=0),
            # 过夜合规（4DTE，跨自然日）。
            _compliance_spec(_may(7), "300", dte=4, hold_seconds=90_000),
            # 1-3DTE＝违规 V2-C①。
            _compliance_spec(_may(8), "-20", dte=2),
            # 买了时间却不用＝违规 V2-C②。
            _compliance_spec(_may(11), "-50", dte=5),
            # ≥8DTE 隔夜＝规则未覆盖。
            _compliance_spec(_may(12), "10", dte=30, hold_seconds=90_000),
            # 缺 DTE＝不可判定，绝不并入 other。
            {
                "underlying": "AAA",
                "opened_at": _may(13),
                "hold_seconds": 1_200,
                "realized_pnl_net": Decimal("5"),
                "total_fee": Decimal("1"),
                "dte_at_entry": None,
                "opening_cash_flow": Decimal("1000"),
                "evidence_summary_json": _evidence_summary(3),
            },
        ]
    )
    result = get_personal_edge_stats()
    assert result is not None
    compliance = result.rule_compliance
    assert compliance.rule_set_id == "v2"
    assert compliance.adopted_at == RULE_SET_V2_ADOPTED_AT
    assert compliance.clean_basis_start == RULE_COMPLIANCE_CLEAN_BASIS_START
    assert compliance.population_n == 8

    lanes = {lane.key: lane for lane in compliance.all_history.lanes}
    assert set(lanes) == set(RULE_LANES)
    assert lanes["intraday_0dte"].n == 2
    assert lanes["intraday_0dte"].verdict == "compliant"
    assert lanes["intraday_0dte"].rule_id == "V2-A"
    # gross = 净 + 费用 = (100 - 40) + 2 = 62；risk = 2 × 1000。
    assert lanes["intraday_0dte"].gross == pytest.approx(62.0)
    assert lanes["intraday_0dte"].gross_pct == pytest.approx(0.031)
    assert lanes["intraday_0dte"].toll_pct == pytest.approx(0.001)
    assert lanes["intraday_0dte"].win_rate == pytest.approx(0.5)
    assert lanes["intraday_0dte"].risk == pytest.approx(2000.0)
    assert lanes["late_0dte"].n == 1
    assert lanes["late_0dte"].verdict == "violation"
    assert lanes["late_0dte"].rule_id == "V2-C③"
    assert lanes["overnight_4_7"].n == 1
    assert lanes["overnight_4_7"].rule_id == "V2-B"
    assert lanes["dte_1_3"].n == 1
    assert lanes["bought_time_unused"].n == 1
    assert lanes["other"].n == 1
    assert lanes["other"].verdict == "uncovered"
    assert lanes["unknown"].n == 1
    assert lanes["unknown"].verdict == "unknown"

    verdicts = {item.key: item for item in compliance.all_history.verdicts}
    assert verdicts["compliant"].n == 3
    # (100 - 40 + 300) + 3 费用 = 363，除以 3000。
    assert verdicts["compliant"].gross_pct == pytest.approx(0.121)
    assert verdicts["violation"].n == 3
    # (-30 - 20 - 50) + 3 = −97，除以 3000。
    assert verdicts["violation"].gross_pct == pytest.approx(-0.032333)
    assert verdicts["uncovered"].n == 1
    assert verdicts["unknown"].n == 1
    # 每一条边界原文下发，消费端不得自行改写。
    assert compliance.limitations == RULE_COMPLIANCE_LIMITATIONS


def test_rule_compliance_empty_bucket_says_no_sample_not_zero():
    _seed_build([_compliance_spec(_may(4), "100", dte=0)])
    result = get_personal_edge_stats()
    assert result is not None
    lanes = {lane.key: lane for lane in result.rule_compliance.all_history.lanes}
    empty = lanes["overnight_4_7"]
    assert empty.n == 0
    # 空桶不是 0%：比率全部缺席并带原因。
    assert empty.gross_pct is None
    assert empty.toll_pct is None
    assert empty.win_rate is None
    assert empty.risk is None
    assert empty.ratio_reason is not None
    assert empty.gross_pct_excluding_top_n is None
    assert empty.excluding_top_n_reason is not None


def test_rule_compliance_reuses_exclude_top_n_gate():
    """剔除最好 N 笔复用规模与频率同一实现与 n≥15 门槛。"""
    # 14 笔 → 未达门槛：null + 原因（绝不给截断样本的数字）。
    _seed_build(
        [
            _compliance_spec(_may(4), str(index), dte=0)
            for index in range(1, 15)
        ]
    )
    small = get_personal_edge_stats()
    assert small is not None
    lanes = {lane.key: lane for lane in small.rule_compliance.all_history.lanes}
    assert lanes["intraday_0dte"].n == 14
    assert lanes["intraday_0dte"].gross_pct_excluding_top_n is None
    assert str(DISCIPLINE_BODY_MIN_EPISODE_COUNT) in (
        lanes["intraday_0dte"].excluding_top_n_reason or ""
    )

    # 15 笔 → 恰好达标：去掉最好的 5 笔后剩 10 笔。
    _seed_build(
        [
            _compliance_spec(_may(4), str(index), dte=0)
            for index in range(1, 16)
        ],
        account_key="exclude_top_n_account",
    )
    big = get_personal_edge_stats("exclude_top_n_account")
    assert big is not None
    lane = {
        item.key: item for item in big.rule_compliance.all_history.lanes
    }["intraday_0dte"]
    assert lane.n == 15
    assert lane.excluding_top_n_count == 15 - DISCIPLINE_EXCLUDE_TOP_N
    # 剩余 1..10 的净盈亏 55 + 10 费用 = 65，除以 10 × 1000。
    assert lane.gross_pct_excluding_top_n == pytest.approx(0.0065)
    assert lane.excluding_top_n_reason is None
    assert big.rule_compliance.exclude_top_n == DISCIPLINE_EXCLUDE_TOP_N
    assert (
        big.rule_compliance.exclude_top_n_min_episode_count
        == DISCIPLINE_BODY_MIN_EPISODE_COUNT
    )


def test_rule_compliance_clean_basis_excludes_aggregate_and_early_rows():
    """干净口径：早于 2026-04-21、汇总 ORDER 口径、缺风险金额三类各自计缺席。"""
    _seed_build(
        [
            # 早于干净口径起点。
            _compliance_spec(_april(20, 14), "100", dte=0),
            # 汇总 ORDER 口径（fill_allocations=0）。
            _compliance_spec(
                _may(4), "100", dte=0, evidence_summary_json=_evidence_summary(0)
            ),
            # 口径来源不可判定。
            _compliance_spec(_may(5), "100", dte=0, evidence_summary_json="{}"),
            # 缺开仓现金流。
            {
                "underlying": "AAA",
                "opened_at": _may(6),
                "hold_seconds": 1_200,
                "realized_pnl_net": Decimal("100"),
                "total_fee": Decimal("1"),
                "dte_at_entry": 0,
                "opening_cash_flow": None,
                "evidence_summary_json": _evidence_summary(3),
            },
            # 唯一进入统计的回合。
            _compliance_spec(_may(7), "100", dte=0),
        ]
    )
    result = get_personal_edge_stats()
    assert result is not None
    compliance = result.rule_compliance
    assert compliance.population_n == 1
    assert compliance.excluded_before_clean_basis_count == 1
    assert compliance.excluded_aggregate_or_unknown_basis_count == 2
    assert compliance.excluded_missing_premium_count == 1
    # 排除的理由原文可读，不是一个沉默的过滤器。
    assert "fill_allocations" in compliance.clean_basis_reason
    assert RULE_COMPLIANCE_CLEAN_BASIS_START in compliance.clean_basis_reason


def test_rule_compliance_since_cutoff_slices_forward_only():
    _seed_build(
        [
            _compliance_spec(_may(4), "100", dte=0),
            _compliance_spec(_may(20), "-60", dte=0),
            _compliance_spec(_may(21), "40", dte=0),
        ]
    )
    result = get_personal_edge_stats(rule_compliance_since="2026-05-20")
    assert result is not None
    compliance = result.rule_compliance
    assert compliance.adopted_at == "2026-05-20"
    assert compliance.all_history.n == 3
    assert compliance.all_history.state == "ready"
    assert compliance.all_history.start_date == RULE_COMPLIANCE_CLEAN_BASIS_START
    # 切点当天算「采纳后」（>=），5/4 的回合不进入前向切片。
    assert compliance.since_adoption.n == 2
    assert compliance.since_adoption.state == "ready"
    assert compliance.since_adoption.start_date == "2026-05-20"
    forward = {
        item.key: item for item in compliance.since_adoption.lanes
    }["intraday_0dte"]
    assert forward.n == 2
    # (−60 + 40) + 2 费用 = −18，除以 2000。
    assert forward.gross_pct == pytest.approx(-0.009)


def test_rule_compliance_since_adoption_empty_is_explicit_not_zeroed():
    """采纳后尚无样本是事实：显式状态 + 原因，绝不渲染成一张全 0 的表。"""
    _seed_build([_compliance_spec(_may(4), "100", dte=0)])
    result = get_personal_edge_stats()
    assert result is not None
    forward = result.rule_compliance.since_adoption
    assert forward.state == "no_episodes_since_adoption"
    assert forward.n == 0
    assert forward.state_reason is not None
    assert RULE_SET_V2_ADOPTED_AT in forward.state_reason
    # 每条车道都在，但都是「无样本」而不是 0%。
    for lane in forward.lanes:
        assert lane.n == 0
        assert lane.gross_pct is None
        assert lane.ratio_reason is not None


def test_rule_compliance_since_rejects_a_non_date_cutoff():
    _seed_build([_compliance_spec(_may(4), "100", dte=0)])
    with pytest.raises(EpisodeRepositoryError):
        get_personal_edge_stats(rule_compliance_since="not-a-date")


def test_rule_compliance_daily_budget_counts_from_the_build_last_day():
    """V2-D 额度读数带明确 as-of 交易日；午后 0DTE 同样占用当日额度。"""
    _seed_build(
        [
            # 最后一个交易日（ET 2026-05-11）：3 笔 0DTE，其中 1 笔为午后违规单。
            _compliance_spec(_may(11), "10", dte=0),
            _compliance_spec(_may(11), "10", dte=0),
            _compliance_spec(_may(11, 17), "10", dte=0),
            # 更早的一天不进入当日额度。
            _compliance_spec(_may(8), "10", dte=0),
            # 仍未平仓的 4-7DTE＝当前过夜持仓。
            {
                "underlying": "AAA",
                "opened_at": _may(11),
                "lifecycle_status": "open",
                "realized_pnl_net": None,
                "dte_at_entry": 5,
            },
            # 未平仓但 DTE 不在 4-7：不计入过夜持仓。
            {
                "underlying": "AAA",
                "opened_at": _may(11),
                "lifecycle_status": "open",
                "realized_pnl_net": None,
                "dte_at_entry": 30,
            },
            # 未平仓且缺 DTE：单独计缺席，绝不悄悄算作 0。
            {
                "underlying": "AAA",
                "opened_at": _may(11),
                "lifecycle_status": "open",
                "realized_pnl_net": None,
                "dte_at_entry": None,
            },
        ]
    )
    result = get_personal_edge_stats()
    assert result is not None
    budget = result.rule_compliance.daily_budget
    assert budget.as_of_trading_day == "2026-05-11"
    assert budget.intraday_ticket_count == 3
    assert budget.intraday_ticket_limit == INTRADAY_LANE_DAILY_TICKET_LIMIT == 6
    assert budget.intraday_reason is None
    assert budget.overnight_open_count == 1
    assert budget.overnight_concurrent_limit == OVERNIGHT_LANE_CONCURRENT_LIMIT == 3
    assert budget.overnight_unknown_dte_open_count == 1


def test_rule_compliance_constants_match_the_adopted_rule_set():
    """UI 引用规则编号，数值只此一处真源——常量漂移会立刻被这条测试抓到。"""
    assert RULE_SET_V2_ADOPTED_AT == "2026-08-05"
    assert RULE_COMPLIANCE_CLEAN_BASIS_START == "2026-04-21"
    assert INTRADAY_LANE_DTE == 0
    assert INTRADAY_LANE_ET_CUTOFF_HOUR == 12
    assert (OVERNIGHT_LANE_MIN_DTE, OVERNIGHT_LANE_MAX_DTE) == (4, 7)
    assert OVERNIGHT_LANE_WEAK_ENTRY_ET_HOURS == (11, 13)
    assert INTRADAY_LANE_DAILY_TICKET_LIMIT == 6
    assert OVERNIGHT_LANE_CONCURRENT_LIMIT == 3
    # 单一 regime / n=65 / 跳空风险 / 非建议：四条边界必须原文在场。
    joined = "".join(RULE_COMPLIANCE_LIMITATIONS)
    assert "n=65" in joined
    assert "跳空" in joined
    assert "不构成建议" in joined
    assert "只读，不下单" in joined

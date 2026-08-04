# -*- coding: utf-8 -*-
"""交易纪律证据表的聚合算术：价格分档 / DTE × 持有方式 / 时段 / 星期。

这些测试用**构造的 fixture**锁住口径，不依赖本地数据库里那份真实账单：
金额加权（不是逐笔平均）、毛口径 = 净 + 费用、边界 ``[lower, upper)``、
星期表排除缺 DTE 的回合、分母为 0 时 fail closed。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.journal.rules_evidence import (
    DTE_HOLD_EXCLUDE_TOP_N,
    _Episode,
    _aggregate,
    _dte_hold_lanes,
    _et_hours,
    _percentile,
    _position_evidence,
    _price_bands,
    _weekdays,
    ChosenPositionParams,
)


def _episode(
    *,
    pnl: str,
    fee: str,
    risk: str,
    entry_price: str | None = None,
    dte: int | None = 0,
    trading_day: str = "2026-05-04",  # a Monday
    close_trading_day: str | None = "2026-05-04",
    et_hour: int = 10,
    underlying: str = "NVDA",
    lane: str = "intraday_0dte",
) -> _Episode:
    opened_at = datetime.fromisoformat(f"{trading_day}T{et_hour:02d}:30:00").replace(
        tzinfo=timezone.utc
    )
    return _Episode(
        underlying=underlying,
        trading_day=trading_day,
        close_trading_day=close_trading_day,
        et_hour=et_hour,
        weekday=datetime.fromisoformat(trading_day).weekday(),
        dte=dte,
        lane=lane,
        pnl=Decimal(pnl),
        fee=Decimal(fee),
        risk=Decimal(risk),
        entry_price=Decimal(entry_price) if entry_price is not None else None,
        opened_at=opened_at,
        closed_at=opened_at,
    )


class TestAggregate:
    def test_is_amount_weighted_not_per_trade_average(self):
        """一大一小两笔：金额加权 ≠ 逐笔平均，口径不能混。"""
        members = [
            _episode(pnl="100", fee="0", risk="10000"),  # +1%
            _episode(pnl="50", fee="0", risk="100"),  # +50%
        ]

        gross, net, _fee, _win, reason = _aggregate(members)

        # 金额加权：150 / 10100 = 1.4851%（逐笔平均会是 25.5%）
        assert net == pytest.approx(1.4851, abs=1e-4)
        assert gross == pytest.approx(1.4851, abs=1e-4)
        assert reason is None

    def test_gross_is_net_plus_fee(self):
        gross, net, fee, _win, _reason = _aggregate(
            [_episode(pnl="-10", fee="30", risk="1000")]
        )

        assert net == pytest.approx(-1.0)
        assert fee == pytest.approx(3.0)
        assert gross == pytest.approx(2.0)

    def test_zero_denominator_fails_closed(self):
        gross, net, fee, win, reason = _aggregate(
            [_episode(pnl="10", fee="1", risk="0")]
        )

        assert (gross, net, fee, win) == (None, None, None, None)
        assert "0" in reason

    def test_empty_bucket_fails_closed(self):
        assert _aggregate([])[4] == "无样本"

    def test_win_rate_counts_strictly_positive_net(self):
        members = [
            _episode(pnl="10", fee="0", risk="100"),
            _episode(pnl="0", fee="0", risk="100"),
            _episode(pnl="-10", fee="0", risk="100"),
        ]

        assert _aggregate(members)[3] == pytest.approx(100 / 3, abs=1e-4)


class TestPriceBands:
    def test_boundaries_are_lower_inclusive_upper_exclusive(self):
        """$2.00 只能落进 $2-4，绝不同时进 $1-2。"""
        rows = {
            row.label: row
            for row in _price_bands(
                [
                    _episode(pnl="1", fee="0", risk="100", entry_price="0.99"),
                    _episode(pnl="1", fee="0", risk="100", entry_price="1.00"),
                    _episode(pnl="1", fee="0", risk="100", entry_price="2.00"),
                    _episode(pnl="1", fee="0", risk="100", entry_price="8.00"),
                ]
            )
        }

        assert rows["<$1"].n == 1
        assert rows["$1-2"].n == 1
        assert rows["$2-4"].n == 1
        assert rows[">$8"].n == 1
        assert sum(row.n for row in rows.values()) == 4

    def test_cheap_contracts_carry_a_higher_fee_share(self):
        """便宜合约＝张数多＝按张收的费用占权利金比例更高。"""
        rows = {
            row.label: row
            for row in _price_bands(
                [
                    # $0.50 合约：同样 1000 风险，费用 60
                    _episode(pnl="-200", fee="60", risk="1000", entry_price="0.50"),
                    # $5 合约：同样 1000 风险，费用 6
                    _episode(pnl="-200", fee="6", risk="1000", entry_price="5.00"),
                ]
            )
        }

        assert rows["<$1"].fee_pct == pytest.approx(6.0)
        assert rows["$4-8"].fee_pct == pytest.approx(0.6)
        assert rows["<$1"].net_pct == rows["$4-8"].net_pct == pytest.approx(-20.0)
        # 毛口径相同、净口径相同，差别整个来自过路费。
        assert rows["<$1"].gross_pct == pytest.approx(-14.0)
        assert rows["$4-8"].gross_pct == pytest.approx(-19.4)

    def test_missing_entry_price_enters_no_band(self):
        rows = _price_bands([_episode(pnl="1", fee="0", risk="100", entry_price=None)])

        assert sum(row.n for row in rows) == 0
        assert all(row.reason == "无样本" for row in rows)


class TestDteHoldLanes:
    def test_same_day_and_overnight_are_split_by_et_trading_day(self):
        rows = {
            row.label: row
            for row in _dte_hold_lanes(
                [
                    _episode(
                        pnl="10",
                        fee="0",
                        risk="100",
                        dte=5,
                        trading_day="2026-05-04",
                        close_trading_day="2026-05-04",
                    ),
                    _episode(
                        pnl="50",
                        fee="0",
                        risk="100",
                        dte=5,
                        trading_day="2026-05-04",
                        close_trading_day="2026-05-05",
                    ),
                ]
            )
        }

        assert rows["4-7DTE 当日平"].n == 1
        assert rows["4-7DTE 当日平"].gross_pct == pytest.approx(10.0)
        assert rows["4-7DTE 过夜"].n == 1
        assert rows["4-7DTE 过夜"].gross_pct == pytest.approx(50.0)

    def test_still_open_episodes_join_neither_hold_style(self):
        rows = {
            row.label: row
            for row in _dte_hold_lanes(
                [
                    _episode(
                        pnl="10", fee="0", risk="100", dte=5, close_trading_day=None
                    )
                ]
            )
        }

        assert rows["4-7DTE 当日平"].n == 0
        assert rows["4-7DTE 过夜"].n == 0

    def test_ex_top_n_strips_the_biggest_winners(self):
        """剔尾读数用来看「是不是靠少数几笔撑住的」。"""
        members = [
            _episode(pnl="1000", fee="0", risk="100", dte=5, close_trading_day="2026-05-05")
            for _ in range(DTE_HOLD_EXCLUDE_TOP_N)
        ] + [
            _episode(pnl="-50", fee="0", risk="100", dte=5, close_trading_day="2026-05-05")
            for _ in range(5)
        ]

        row = {r.label: r for r in _dte_hold_lanes(members)}["4-7DTE 过夜"]

        assert row.n == 10
        assert row.gross_pct == pytest.approx(475.0)
        # 去掉 5 笔大赢家后只剩亏损。
        assert row.ex_top_n_n == 5
        assert row.ex_top_n_gross_pct == pytest.approx(-50.0)

    def test_ex_top_n_fails_closed_when_sample_is_too_small(self):
        members = [
            _episode(pnl="10", fee="0", risk="100", dte=5, close_trading_day="2026-05-05")
            for _ in range(DTE_HOLD_EXCLUDE_TOP_N)
        ]

        row = {r.label: r for r in _dte_hold_lanes(members)}["4-7DTE 过夜"]

        assert row.ex_top_n_gross_pct is None
        assert str(DTE_HOLD_EXCLUDE_TOP_N) in row.ex_top_n_reason


class TestHoursAndWeekdays:
    def test_hours_report_gross_against_toll_separately(self):
        rows = {
            row.et_hour: row
            for row in _et_hours(
                [
                    _episode(pnl="-10", fee="20", risk="1000", et_hour=9),
                    _episode(pnl="80", fee="20", risk="1000", et_hour=15),
                ]
            )
        }

        assert rows[9].gross_pct == pytest.approx(1.0)
        assert rows[9].fee_pct == pytest.approx(2.0)
        assert rows[9].net_pct == pytest.approx(-1.0)
        assert rows[15].gross_pct == pytest.approx(10.0)

    def test_weekday_table_excludes_episodes_without_a_known_dte(self):
        """缺 DTE 无法判定车道，混入会让「0DTE 可用性」这个因读错。"""
        rows = {
            row.label: row
            for row in _weekdays(
                [
                    _episode(pnl="10", fee="0", risk="100", dte=0),
                    _episode(pnl="-90", fee="0", risk="100", dte=None),
                ]
            )
        }

        assert rows["周一"].n == 1
        assert rows["周一"].gross_pct == pytest.approx(10.0)

    def test_weekday_counts_zero_dte_availability_alongside_the_return(self):
        rows = {
            row.label: row
            for row in _weekdays(
                [
                    _episode(pnl="1", fee="0", risk="100", dte=0),
                    _episode(pnl="1", fee="0", risk="100", dte=2),
                    _episode(pnl="1", fee="0", risk="100", dte=3),
                    # 周二（2026-05-05）：没有 0DTE，只能退而买 1-3DTE
                    _episode(
                        pnl="1", fee="0", risk="100", dte=1, trading_day="2026-05-05"
                    ),
                ]
            )
        }

        assert (rows["周一"].zero_dte_n, rows["周一"].dte_1_3_n) == (1, 2)
        assert (rows["周二"].zero_dte_n, rows["周二"].dte_1_3_n) == (0, 1)


class TestPercentile:
    def test_matches_linear_interpolation(self):
        values = [Decimal(v) for v in ("-1.0", "-0.5", "-0.2", "0.1", "1.0")]

        assert _percentile(values, 0) == Decimal("-1.0")
        assert _percentile(values, 100) == Decimal("1.0")
        assert _percentile(values, 50) == Decimal("-0.2")
        assert _percentile(values, 25) == pytest.approx(Decimal("-0.5"))

    def test_empty_input_is_none(self):
        assert _percentile([], 50) is None


class TestPositionEvidence:
    def test_severity_tiers_convert_the_breaker_into_ticket_counts(self):
        # 20 笔 −100%、60 笔 −30%、20 笔 +50% 的构造分布。
        members = (
            [_episode(pnl="-100", fee="0", risk="100") for _ in range(20)]
            + [_episode(pnl="-30", fee="0", risk="100") for _ in range(60)]
            + [_episode(pnl="50", fee="0", risk="100") for _ in range(20)]
        )
        params = ChosenPositionParams(
            ticket_usd=3_000, daily_breaker_usd=6_000, max_concurrent=2
        )

        result = _position_evidence(members, params=params)
        tiers = {tier.label: tier for tier in result.severity_tiers}

        # 归零永远是 breaker / ticket = 2.0 笔，与样本无关。
        assert tiers["归零"].loss_pct == pytest.approx(-100.0)
        assert tiers["归零"].tickets_to_breaker == pytest.approx(2.0)
        # p5 落在 −100% 段里 → 仍是 2.0 笔；p25 落在 −30% 段 → 6.7 笔。
        assert tiers["严重亏损"].tickets_to_breaker == pytest.approx(2.0)
        assert tiers["平庸亏损"].loss_pct == pytest.approx(-30.0)
        assert tiers["平庸亏损"].tickets_to_breaker == pytest.approx(6.7)

    def test_breaker_frequency_counts_days_not_trades(self):
        members = [
            # 第一天：两笔归零 → −6,000，正好触发
            _episode(pnl="-100", fee="0", risk="100", trading_day="2026-05-04"),
            _episode(pnl="-100", fee="0", risk="100", trading_day="2026-05-04"),
            # 第二天：一笔小亏，不触发
            _episode(pnl="-10", fee="0", risk="100", trading_day="2026-05-05"),
        ]
        params = ChosenPositionParams(
            ticket_usd=3_000, daily_breaker_usd=6_000, max_concurrent=2
        )

        result = _position_evidence(members, params=params)

        assert result.observed_trading_day_count == 2
        assert result.observed_breach_day_count == 1
        assert result.observed_breach_one_per_days == pytest.approx(2.0)

    def test_cadence_caveat_names_the_historical_trade_rate(self):
        members = [
            _episode(pnl="10", fee="0", risk="100", trading_day="2026-05-04")
            for _ in range(8)
        ]
        params = ChosenPositionParams(
            ticket_usd=3_000, daily_breaker_usd=6_000, max_concurrent=2
        )

        result = _position_evidence(members, params=params)

        assert result.observed_median_tickets_per_day == pytest.approx(8.0)
        assert "历史下单节奏" in result.observed_cadence_caveat
        # 熔断与累计回撤必须被明确区分开。
        assert "累计回撤" in result.drawdown_caveat

    def test_drawdown_is_one_realised_path_not_a_simulated_median(self):
        members = [
            _episode(pnl="-50", fee="0", risk="100", trading_day="2026-05-04"),
            _episode(pnl="-50", fee="0", risk="100", trading_day="2026-05-05"),
        ]
        params = ChosenPositionParams(
            ticket_usd=3_000, daily_breaker_usd=6_000, max_concurrent=2
        )

        result = _position_evidence(members, params=params)

        # 10% 仓位、连续两笔 −50%：0.95 × 0.95 = 0.9025 → −9.75%
        assert result.historical_max_drawdown_pct == pytest.approx(-9.75, abs=1e-2)
        assert result.historical_drawdown_sizing_pct == pytest.approx(10.0)

    def test_chosen_params_are_echoed_not_recommended(self):
        params = ChosenPositionParams(
            ticket_usd=1_500, daily_breaker_usd=4_500, max_concurrent=3
        )

        result = _position_evidence(
            [_episode(pnl="1", fee="0", risk="100")], params=params
        )

        assert result.params == params
        assert result.framing == "你选择的参数 + 它们的含义"

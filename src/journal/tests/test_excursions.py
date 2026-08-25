# -*- coding: utf-8 -*-
"""Pure-function tests for MAE/MFE excursions (blueprint 17 §三(b))."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.journal.excursions import (
    EXCURSION_DISCLAIMER,
    EXCURSION_LIMITATIONS,
    STATUS_MISSING_BARS,
    STATUS_NOT_APPLICABLE,
    STATUS_PARTIAL,
    STATUS_READY,
    ExcursionEpisode,
    compute_atr14,
    compute_excursion,
    determine_exposure,
)

ET = ZoneInfo("America/New_York")


def _bar(
    day: str,
    hour: int,
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> dict:
    start = datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=ET
    )
    return {
        "date": start.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
    }


def _episode(
    *,
    asset_type: str = "option",
    direction: str = "long",
    option_right: str | None = "C",
    opened: datetime | None = None,
    closed: datetime | None = None,
) -> ExcursionEpisode:
    return ExcursionEpisode(
        asset_type=asset_type,
        direction=direction,
        option_right=option_right,
        opened_at=opened,
        closed_at=closed,
    )


class TestDetermineExposure:
    def test_blueprint_exposure_table(self):
        # 账本真实词汇：ledger/episodes.py 对正股写 "equity"（复核修复 2）。
        assert determine_exposure("option", "long", "C") == 1
        assert determine_exposure("option", "short", "P") == 1
        assert determine_exposure("equity", "long", None) == 1
        assert determine_exposure("option", "long", "P") == -1
        assert determine_exposure("option", "short", "C") == -1
        assert determine_exposure("equity", "short", None) == -1

    def test_stock_kept_as_alias(self):
        assert determine_exposure("stock", "long", None) == 1
        assert determine_exposure("stock", "short", None) == -1

    def test_unknown_shapes_fail_closed(self):
        assert determine_exposure("option", "long", None) is None
        assert determine_exposure("multi_leg", "long", "C") is None
        assert determine_exposure("option", "mixed", "C") is None
        assert determine_exposure("", "", "") is None


class TestComputeExcursion:
    def test_long_call_deep_mae_then_large_mfe(self):
        day = "2026-08-03"  # Monday
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        closed = datetime(2026, 8, 3, 10, 2, tzinfo=ET)
        bars = [
            _bar(day, 9, 30, 100.0, 100.5, 99.8, 100.2),  # 部分 bar，U0 之前
            _bar(day, 9, 35, 100.0, 100.2, 99.5, 99.6),
            _bar(day, 9, 40, 99.6, 99.7, 97.0, 97.2),   # 深 MAE：−3%
            _bar(day, 9, 45, 97.2, 99.0, 97.1, 98.9),
            _bar(day, 9, 50, 98.9, 104.0, 98.8, 103.8),  # 大 MFE：+4%
            _bar(day, 9, 55, 103.8, 103.9, 103.0, 103.4),
            _bar(day, 10, 0, 103.4, 103.5, 102.9, 103.2),
        ]
        result = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert result.status == STATUS_READY
        assert result.exposure == 1
        # U0 = opened_at 之后第一根 bar（09:35）的 open。
        assert result.u0 == 100.0
        assert result.u0_at.hour == 9 and result.u0_at.minute == 35
        assert result.u0_flag is None
        assert result.mae_underlying_pct == pytest.approx(-0.03)
        assert result.mfe_underlying_pct == pytest.approx(0.04)
        assert result.mae_before_mfe is True
        assert result.bars_used == 6

    def test_short_put_favorable_is_up(self):
        day = "2026-08-03"
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        closed = datetime(2026, 8, 3, 9, 50, tzinfo=ET)
        bars = [
            _bar(day, 9, 35, 200.0, 202.0, 199.0, 201.0),
            _bar(day, 9, 40, 201.0, 206.0, 200.0, 205.0),
            _bar(day, 9, 45, 205.0, 205.5, 196.0, 197.0),
        ]
        result = compute_excursion(
            _episode(direction="short", option_right="P", opened=opened, closed=closed),
            bars,
        )
        assert result.status == STATUS_READY
        assert result.exposure == 1
        assert result.mfe_underlying_pct == pytest.approx(0.03)  # 206/200-1
        assert result.mae_underlying_pct == pytest.approx(-0.02)  # 196/200-1

    def test_long_put_adverse_is_up(self):
        day = "2026-08-03"
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        closed = datetime(2026, 8, 3, 9, 45, tzinfo=ET)
        bars = [
            _bar(day, 9, 35, 100.0, 103.0, 98.0, 99.0),
        ]
        result = compute_excursion(
            _episode(option_right="P", opened=opened, closed=closed), bars
        )
        assert result.exposure == -1
        assert result.mfe_underlying_pct == pytest.approx(0.02)  # low 98
        assert result.mae_underlying_pct == pytest.approx(-0.03)  # high 103

    def test_overnight_gap_shows_in_next_session_first_bar(self):
        monday = "2026-08-03"
        tuesday = "2026-08-04"
        opened = datetime(2026, 8, 3, 15, 31, tzinfo=ET)
        closed = datetime(2026, 8, 4, 9, 40, tzinfo=ET)
        bars = [
            _bar(monday, 15, 35, 100.0, 100.5, 99.9, 100.1),
            _bar(monday, 15, 55, 100.1, 100.2, 99.8, 100.0),
            # 隔夜跳空 +5%，由次日首根 bar 体现。
            _bar(tuesday, 9, 30, 105.0, 106.0, 104.5, 105.5),
            _bar(tuesday, 9, 35, 105.5, 105.8, 105.0, 105.2),
        ]
        result = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert result.status == STATUS_READY
        assert result.mfe_underlying_pct == pytest.approx(0.06)
        assert result.mfe_at.date().isoformat() == tuesday

    def test_no_bars_is_missing_bars_not_zero(self):
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        closed = datetime(2026, 8, 3, 10, 0, tzinfo=ET)
        result = compute_excursion(_episode(opened=opened, closed=closed), [])
        assert result.status == STATUS_MISSING_BARS
        assert result.mfe_underlying_pct is None
        assert result.mae_underlying_pct is None
        assert result.status_reason

    def test_combo_leg_is_not_applicable(self):
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        closed = datetime(2026, 8, 3, 10, 0, tzinfo=ET)
        result = compute_excursion(
            _episode(asset_type="multi_leg", opened=opened, closed=closed),
            [_bar("2026-08-03", 9, 35, 100, 101, 99, 100.5)],
        )
        assert result.status == STATUS_NOT_APPLICABLE
        assert result.mfe_underlying_pct is None

    def test_open_episode_is_not_applicable(self):
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        result = compute_excursion(_episode(opened=opened, closed=None), [])
        assert result.status == STATUS_NOT_APPLICABLE

    def test_missing_middle_weekday_marks_partial(self):
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        closed = datetime(2026, 8, 5, 10, 0, tzinfo=ET)
        bars = [
            _bar("2026-08-03", 9, 35, 100, 101, 99, 100.5),
            # 2026-08-04（周二）整日缺失。
            _bar("2026-08-05", 9, 30, 100.5, 102, 100, 101.5),
        ]
        result = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert result.status == STATUS_PARTIAL
        assert "2026-08-04" in result.missing_sessions
        assert "2026-08-04" in (result.status_reason or "")

    def test_premarket_open_uses_first_rth_bar_with_flag(self):
        opened = datetime(2026, 8, 3, 8, 0, tzinfo=ET)
        closed = datetime(2026, 8, 3, 10, 0, tzinfo=ET)
        bars = [_bar("2026-08-03", 9, 30, 100, 101, 99, 100.5)]
        result = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert result.u0_flag == "premarket_open_first_rth_bar"
        assert result.u0 == 100.0

    def test_afterhours_open_anchors_next_session_with_flag(self):
        opened = datetime(2026, 8, 3, 17, 0, tzinfo=ET)
        closed = datetime(2026, 8, 4, 10, 0, tzinfo=ET)
        bars = [_bar("2026-08-04", 9, 30, 100, 101, 99, 100.5)]
        result = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert result.u0_flag == "entry_outside_rth_next_session_bar"
        assert result.status == STATUS_READY

    def test_rth_entry_with_missing_entry_day_bars_is_data_gap_not_afterhours(
        self,
    ):
        """复核修复 7：RTH 内开仓但入场日整日缺 bar → 数据缺口 flag，
        不得谎称「盘外开仓」（时钟事实与数据事实分开）。"""
        opened = datetime(2026, 8, 3, 12, 48, tzinfo=ET)  # RTH 内
        closed = datetime(2026, 8, 4, 10, 0, tzinfo=ET)
        bars = [_bar("2026-08-04", 9, 30, 100, 101, 99, 100.5)]
        result = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert result.u0_flag == "entry_day_bars_missing_next_session_bar"

    def test_sub_bar_hold_is_granularity_limit_not_missing_bars(self):
        """复核修复 4：持有短于一根 5m bar 且窗口内无 bar 开始时刻 →
        not_applicable（粒度限制），不再谎称 missing_bars。"""
        opened = datetime(2026, 8, 3, 10, 1, 10, tzinfo=ET)
        closed = datetime(2026, 8, 3, 10, 3, 40, tzinfo=ET)  # 不跨 10:05
        bars = [_bar("2026-08-03", 10, 0, 100, 101, 99, 100.5)]
        result = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert result.status == STATUS_NOT_APPLICABLE
        assert "sub_bar_hold_below_5m_granularity" in (
            result.status_reason or ""
        )

    def test_sub_bar_hold_straddling_missing_boundary_stays_missing_bars(
        self,
    ):
        """短持有但窗口理应含 10:05 bar 开始时刻、bar 却缺失 → 仍是
        missing_bars（数据问题），不冒充粒度限制。"""
        opened = datetime(2026, 8, 3, 10, 3, tzinfo=ET)
        closed = datetime(2026, 8, 3, 10, 6, tzinfo=ET)  # 跨 10:05
        result = compute_excursion(_episode(opened=opened, closed=closed), [])
        assert result.status == STATUS_MISSING_BARS

    def test_atr_units_when_atr14_available(self):
        opened = datetime(2026, 8, 3, 9, 31, tzinfo=ET)
        closed = datetime(2026, 8, 3, 10, 0, tzinfo=ET)
        bars = [_bar("2026-08-03", 9, 35, 100.0, 102.0, 99.0, 101.0)]
        result = compute_excursion(
            _episode(opened=opened, closed=closed), bars, atr14=4.0
        )
        # mfe 2% × U0 100 / ATR 4 = 0.5 ATR
        assert result.mfe_atr == pytest.approx(0.5)
        assert result.mae_atr == pytest.approx(-0.25)
        without = compute_excursion(_episode(opened=opened, closed=closed), bars)
        assert without.mfe_atr is None and without.atr14 is None

    def test_naive_timestamps_are_rejected(self):
        opened = datetime(2026, 8, 3, 9, 31)
        closed = datetime(2026, 8, 3, 10, 0, tzinfo=ET)
        with pytest.raises(ValueError):
            compute_excursion(_episode(opened=opened, closed=closed), [])


class TestAtr14:
    def _daily(self, count: int) -> list[dict]:
        rows = []
        day = datetime(2026, 6, 1)
        added = 0
        while added < count:
            if day.weekday() < 5:
                rows.append(
                    {
                        "date": day.strftime("%Y-%m-%d"),
                        "high": 102.0,
                        "low": 98.0,
                        "close": 100.0,
                    }
                )
                added += 1
            day += timedelta(days=1)
        return rows

    def test_constant_range_atr(self):
        rows = self._daily(30)
        atr = compute_atr14(rows, datetime(2026, 8, 1).date())
        assert atr == pytest.approx(4.0)

    def test_insufficient_history_fails_closed(self):
        rows = self._daily(10)
        assert compute_atr14(rows, datetime(2026, 8, 1).date()) is None

    def test_only_bars_before_as_of_are_used(self):
        rows = self._daily(30)
        as_of = datetime(2026, 6, 5).date()
        assert compute_atr14(rows, as_of) is None


def test_disclaimer_is_locked_verbatim():
    """免责文案锁定：不许被改写、必须仍在 limitations 里逐字携带。"""
    assert EXCURSION_DISCLAIMER == (
        "本图不用于设置止损；不得由此推导出场/持有参数。"
        "对尾部结构账户，按 MAE 分位收紧止损最大概率截掉的恰是右尾。"
        "持仓分组（日内/隔夜）存在内生性。"
    )
    assert EXCURSION_DISCLAIMER in EXCURSION_LIMITATIONS


def test_exit_bar_spillover_limitation_is_declared():
    """复核修复 8：出场 bar 溢出（平仓后至多约 5 分钟计入）必须如实声明。"""
    assert any("出场 bar 溢出" in item for item in EXCURSION_LIMITATIONS)


def test_code_version_bumped_for_semantic_changes():
    """复核修复 2/3/4 改变了存储语义：口径版本必须已递增。"""
    from src.journal.excursions import EXCURSION_CODE_VERSION

    assert EXCURSION_CODE_VERSION == "underlying-5m/1.1"


def test_disclaimer_matches_frontend_constant_byte_for_byte():
    """复核修复 11：后端免责常量与前端 reviewHardLines.ts 逐字节一致。

    双语言各持一份常量是漂移风险；本测试把前端文件当数据读进来比对，
    任何一侧改动而另一侧未同步都会在后端测试里报警。
    """
    import re
    from pathlib import Path

    ts_path = (
        Path(__file__).resolve().parents[3]
        / "apps"
        / "dsa-web"
        / "src"
        / "components"
        / "journal"
        / "review"
        / "reviewHardLines.ts"
    )
    assert ts_path.exists(), f"frontend constant file missing: {ts_path}"
    text = ts_path.read_text(encoding="utf-8")
    declaration = re.search(
        r"export const EXCURSION_DISCLAIMER =(.+?);", text, re.S
    )
    assert declaration is not None, "EXCURSION_DISCLAIMER not found in TS"
    segments = re.findall(r"'((?:[^'\\]|\\.)*)'", declaration.group(1))
    assert segments, "no string literals inside the TS declaration"
    frontend_value = "".join(segments)
    assert frontend_value.encode("utf-8") == EXCURSION_DISCLAIMER.encode(
        "utf-8"
    )

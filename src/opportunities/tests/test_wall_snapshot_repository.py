# -*- coding: utf-8 -*-
"""逐日期权墙快照记录器：幂等、append-only、部分覆盖如实、失败不写。"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from src.opportunities.wall_snapshot_repository import (
    MAX_SNAPSHOT_TICKERS,
    OptionWallSnapshotError,
    build_snapshot_key,
    init_wall_snapshot_schema,
    list_wall_snapshots,
    record_wall_snapshots,
)
from src.storage import DatabaseManager


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "wall_snapshots.db"))

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


def _item(
    ticker: str = "NVDA",
    *,
    state: str = "ready",
    call_oi: float = 1_000.0,
    put_oi: float = 500.0,
    coverage: float = 100.0,
    oi_ratio_value=2.0,
    oi_ratio_reason=None,
) -> dict:
    return {
        "ticker": ticker,
        "state": state,
        "source": "moomoo_openapi",
        "fetched_at": "2026-07-22T14:00:00+00:00",
        "quote_as_of": "2026-07-22 10:00:01",
        "formula_version": "gross-gamma-concentration-1pct/v1",
        "spot": 100.0,
        "coverage": {"coverage_percent": coverage},
        "totals": {
            "call_oi": call_oi,
            "put_oi": put_oi,
            "call_volume": 250.0,
            "put_volume": 300.0,
        },
        "ratios": {
            "call_put_oi_ratio": {
                "value": oi_ratio_value,
                "reason": oi_ratio_reason,
            },
            "call_put_volume_ratio": {"value": 250 / 300, "reason": None},
        },
        "oi_weighted_center": {"strike": 99.0},
        "walls": {
            "call_oi": [{"strike": 105.0, "metric_value": 1_000.0}],
            "put_oi": [{"strike": 95.0, "metric_value": 500.0}],
            "gross_gamma_concentration": [
                {"strike": 100.0, "metric_value": 12_345.0}
            ],
        },
    }


def _payload(*items: dict, market_date_et: str = "2026-07-22") -> dict:
    return {
        "schema_version": "option-wall/1.3",
        "generated_at": "2026-07-22T14:00:00+00:00",
        "market_date_et": market_date_et,
        "items": list(items),
    }


def test_writer_records_one_row_per_date_and_ticker(isolated_db):
    results = record_wall_snapshots(_payload(_item()), db_manager=isolated_db)

    assert [(r.ticker, r.written, r.duplicate) for r in results] == [
        ("NVDA", True, False)
    ]
    stored = list_wall_snapshots(db_manager=isolated_db)
    assert len(stored) == 1
    row = stored[0]
    assert row["market_date_et"] == "2026-07-22"
    assert row["ticker"] == "NVDA"
    assert row["call_put_oi_ratio"] == pytest.approx(2.0)
    assert row["top_call_wall_strike"] == pytest.approx(105.0)
    assert row["top_put_wall_strike"] == pytest.approx(95.0)
    assert row["gross_gamma_concentration_strike"] == pytest.approx(100.0)
    assert row["oi_weighted_center_strike"] == pytest.approx(99.0)
    assert row["source"] == "moomoo_openapi"
    assert row["formula_version"] == "gross-gamma-concentration-1pct/v1"
    # as-of 与抓取时刻分开记录，互不冒充。
    assert row["quote_as_of"] == "2026-07-22 10:00:01"
    assert row["fetched_at"] is not None


def test_writer_is_idempotent_per_date_and_ticker(isolated_db):
    record_wall_snapshots(_payload(_item()), db_manager=isolated_db)
    replay = record_wall_snapshots(_payload(_item()), db_manager=isolated_db)

    assert replay[0].written is False
    assert replay[0].duplicate is True
    assert "不覆盖" in replay[0].reason
    assert len(list_wall_snapshots(db_manager=isolated_db)) == 1


def test_same_ticker_on_a_later_date_is_a_new_row(isolated_db):
    record_wall_snapshots(_payload(_item()), db_manager=isolated_db)
    record_wall_snapshots(
        _payload(_item(), market_date_et="2026-07-23"),
        db_manager=isolated_db,
    )

    stored = list_wall_snapshots(db_manager=isolated_db)
    assert [row["market_date_et"] for row in stored] == [
        "2026-07-23",
        "2026-07-22",
    ]


def test_snapshot_key_excludes_wall_clock_so_replays_hit_one_slot():
    assert build_snapshot_key("2026-07-22", "NVDA") == build_snapshot_key(
        "2026-07-22", "NVDA"
    )
    assert build_snapshot_key("2026-07-22", "NVDA") != build_snapshot_key(
        "2026-07-23", "NVDA"
    )


@pytest.mark.parametrize("state", ["unavailable", "not_configured", ""])
def test_failed_fetch_records_nothing_rather_than_zeros(isolated_db, state):
    results = record_wall_snapshots(
        _payload(_item(state=state)),
        db_manager=isolated_db,
    )

    assert results[0].written is False
    assert results[0].duplicate is False
    assert "fail-closed" in results[0].reason
    assert list_wall_snapshots(db_manager=isolated_db) == ()


def test_missing_spot_records_nothing(isolated_db):
    item = _item()
    item["spot"] = None

    results = record_wall_snapshots(_payload(item), db_manager=isolated_db)

    assert results[0].written is False
    assert list_wall_snapshots(db_manager=isolated_db) == ()


def test_partial_coverage_is_recorded_with_its_own_coverage_percent(isolated_db):
    results = record_wall_snapshots(
        _payload(_item(state="partial", coverage=41.5)),
        db_manager=isolated_db,
    )

    assert results[0].written is True
    assert results[0].coverage_percent == pytest.approx(41.5)
    stored = list_wall_snapshots(db_manager=isolated_db)
    assert stored[0]["coverage_percent"] == pytest.approx(41.5)


def test_missing_coverage_percent_records_nothing_not_zero(isolated_db):
    """缺 coverage.coverage_percent：不可变行拒写 + 原因，绝不 0 回填。"""
    without_block = _item()
    without_block.pop("coverage")
    without_value = _item()
    without_value["coverage"] = {"coverage_percent": None}

    for item in (without_block, without_value):
        results = record_wall_snapshots(_payload(item), db_manager=isolated_db)
        assert results[0].written is False
        assert results[0].duplicate is False
        assert "coverage_percent" in results[0].reason
        assert "fail-closed" in results[0].reason

    assert list_wall_snapshots(db_manager=isolated_db) == ()


def test_undefined_ratio_is_stored_as_null_plus_reason(isolated_db):
    record_wall_snapshots(
        _payload(
            _item(
                put_oi=0.0,
                oi_ratio_value=None,
                oi_ratio_reason="分母为 0（该窗口内无对应 put 持仓/成交），比例无定义",
            )
        ),
        db_manager=isolated_db,
    )

    row = list_wall_snapshots(db_manager=isolated_db)[0]
    assert row["call_put_oi_ratio"] is None
    assert "分母为 0" in row["call_put_oi_ratio_reason"]
    # 分母本身仍如实记录，读的人能看出比例为什么无定义。
    assert row["put_oi_total"] == pytest.approx(0.0)


def test_rows_are_append_only(isolated_db):
    record_wall_snapshots(_payload(_item()), db_manager=isolated_db)

    with isolated_db._engine.begin() as connection:
        with pytest.raises(Exception, match="append-only"):
            connection.execute(
                text("UPDATE option_wall_daily_snapshots SET spot = 1")
            )
    with isolated_db._engine.begin() as connection:
        with pytest.raises(Exception, match="append-only"):
            connection.execute(text("DELETE FROM option_wall_daily_snapshots"))

    assert len(list_wall_snapshots(db_manager=isolated_db)) == 1


def test_append_only_guard_triggers_are_installed(isolated_db):
    init_wall_snapshot_schema(isolated_db)

    with isolated_db._engine.connect() as connection:
        names = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='option_wall_daily_snapshots'"
            )
        }

    assert names == {
        "trg_option_wall_daily_snapshots_update_immutable",
        "trg_option_wall_daily_snapshots_delete_immutable",
    }


def test_writer_never_fans_out_beyond_the_requested_deep_lane(isolated_db):
    payload = _payload(_item("NVDA"), _item("TSLA"), _item("AMD"))

    results = record_wall_snapshots(
        payload,
        tickers=["NVDA", "TSLA"],
        db_manager=isolated_db,
    )

    assert {r.ticker for r in results} == {"NVDA", "TSLA"}
    assert {row["ticker"] for row in list_wall_snapshots(db_manager=isolated_db)} == {
        "NVDA",
        "TSLA",
    }


def test_writer_is_bounded_even_if_upstream_returns_too_many(isolated_db):
    payload = _payload(
        *(_item(f"SYM{index}") for index in range(MAX_SNAPSHOT_TICKERS + 7))
    )

    results = record_wall_snapshots(payload, db_manager=isolated_db)

    assert len(results) == MAX_SNAPSHOT_TICKERS


def test_missing_market_date_is_rejected(isolated_db):
    with pytest.raises(OptionWallSnapshotError, match="market_date_et"):
        record_wall_snapshots({"items": [_item()]}, db_manager=isolated_db)


def test_invalid_market_date_is_rejected(isolated_db):
    with pytest.raises(OptionWallSnapshotError, match="ET calendar date"):
        record_wall_snapshots(
            _payload(_item(), market_date_et="22/07/2026"),
            db_manager=isolated_db,
        )

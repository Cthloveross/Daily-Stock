# -*- coding: utf-8 -*-
"""FastAPI TestClient coverage for /api/v1/journal/*."""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "journal"
    / "moomoo_inline_sample.csv"
)

CURRENT_MOOMOO_HEADERS = (
    "Side", "Symbol", "Name", "Order Price", "Order Qty", "Order Amount",
    "Status", "Filled@Avg Price", "Order Time", "Order Type",
    "Time-in-Force", "Allow Pre-Market", "Session", "Trigger price",
    "Position Opening", "Markets", "Currency", "Order Source", "Fill Qty",
    "Fill Price", "Fill Amount", "Fill Time", "Markets", "Currency",
    "Counterparty", "Remarks", "Commission", "Platform Fees", "SEC Fees",
    "Trading Activity Fees", "Options Regulatory Fees", "OCC Fees",
    "Contract Fees", "Consolidated Audit Trail Fees", "Total",
    "Settlement Fees",
)


def _current_moomoo_csv(
    *,
    include_order: bool,
    include_close: bool = False,
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(CURRENT_MOOMOO_HEADERS)
    if include_order:
        def _write_order(
            *,
            side: str,
            price: str,
            amount: str,
            order_time: str,
        ) -> None:
            row = [""] * len(CURRENT_MOOMOO_HEADERS)
            values = {
                "Side": side,
                "Symbol": "EXAMPLE260717C00200000",
                "Name": "Deidentified option",
                "Order Price": price,
                "Order Qty": "1",
                "Order Amount": amount,
                "Status": "Filled",
                "Filled@Avg Price": f"1@{price}",
                "Order Time": order_time,
                "Order Type": "Limit",
                "Time-in-Force": "Day",
                "Session": "Regular Trading Hours",
                "Commission": "0.10",
                "Platform Fees": "0.20",
                "Consolidated Audit Trail Fees": "0.0002",
                "Total": "0.3002",
            }
            for name, value in values.items():
                row[CURRENT_MOOMOO_HEADERS.index(name)] = value
            row[CURRENT_MOOMOO_HEADERS.index("Markets")] = "US"
            row[CURRENT_MOOMOO_HEADERS.index("Currency")] = "USD"
            writer.writerow(row)

        _write_order(
            side="Buy",
            price="2.50",
            amount="250",
            order_time="Jul 20, 2026 09:30:00 ET",
        )
        if include_close:
            _write_order(
                side="Sell",
                price="3.00",
                amount="300",
                order_time="Jul 20, 2026 10:30:00 ET",
            )
    return stream.getvalue().encode("utf-8")


def _matching_openapi_csv(*, include_outside_window: bool = False) -> bytes:
    """One detailed CSV execution that matches ``_openapi_export_payload``."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(CURRENT_MOOMOO_HEADERS)
    row = [""] * len(CURRENT_MOOMOO_HEADERS)
    values = {
        "Side": "Buy",
        "Symbol": "EXAMPLE260717C00200000",
        "Name": "Deidentified option",
        "Order Price": "2.50",
        "Order Qty": "1",
        "Order Amount": "250",
        "Status": "Filled",
        "Filled@Avg Price": "1@2.50",
        "Order Time": "Jul 01, 2026 09:30:00 ET",
        "Order Type": "Limit",
        "Time-in-Force": "Day",
        "Session": "Regular Trading Hours",
        "Fill Qty": "1",
        "Fill Price": "2.50",
        "Fill Amount": "250",
        "Fill Time": "Jul 01, 2026 09:30:30 ET",
        "Commission": "0.10",
        "Platform Fees": "0.20",
        "Consolidated Audit Trail Fees": "0.0002",
        "Total": "0.3002",
    }
    for name, value in values.items():
        row[CURRENT_MOOMOO_HEADERS.index(name)] = value
    first_market = CURRENT_MOOMOO_HEADERS.index("Markets")
    first_currency = CURRENT_MOOMOO_HEADERS.index("Currency")
    row[first_market] = "US"
    row[first_currency] = "USD"
    row[CURRENT_MOOMOO_HEADERS.index("Markets", first_market + 1)] = "US"
    row[CURRENT_MOOMOO_HEADERS.index("Currency", first_currency + 1)] = "USD"
    writer.writerow(row)
    if include_outside_window:
        outside = row.copy()
        replacements = {
            "Order Price": "3.00",
            "Order Amount": "300",
            "Filled@Avg Price": "1@3.00",
            "Order Time": "Jul 02, 2026 09:30:00 ET",
            "Fill Price": "3.00",
            "Fill Amount": "300",
            "Fill Time": "Jul 02, 2026 09:30:30 ET",
        }
        for name, value in replacements.items():
            outside[CURRENT_MOOMOO_HEADERS.index(name)] = value
        writer.writerow(outside)
    return stream.getvalue().encode("utf-8")


def _openapi_export_payload() -> dict:
    window = {
        "start": "2026-07-01T09:00:00-04:00",
        "end": "2026-07-01T16:00:00-04:00",
        "timezone": "America/New_York",
        "chunks": 1,
        "max_chunk_days": 7,
    }
    reconciliation = {
        "filled_orders_without_fills": 0,
        "fills_without_orders": 0,
        "filled_quantity_mismatches": 0,
        "fill_code_mismatches": 0,
        "fill_side_mismatches": 0,
        "fill_average_price_mismatches": 0,
        "filled_orders_without_fees": 0,
    }
    summary = {
        "ok": True,
        "analysis_ready": True,
        "reconciliation_status": "passed",
        "warnings": [],
        "mode": "moomoo_readonly_probe",
        "journal_database_written": False,
        "environment": "LIVE",
        "market": "US",
        "account_selection": "unique_auto",
        "window": window,
        "counts": {
            "orders": 1,
            "filled_orders": 1,
            "fills": 1,
            "fees": 1,
            "unique_instruments": 1,
            "option_activity_rows": 1,
        },
        "activity_sides": {"buy": 1, "sell": 0, "other": 0},
        "fee_totals_by_currency": {"USD": 0.3002},
        "activity_time_range": {
            "first": "2026-07-01 09:30:30",
            "last": "2026-07-01 09:30:30",
        },
        "deduplicated_rows": {"orders": 0, "fills": 0, "fees": 0},
        "reconciliation": reconciliation,
        "fee_batches": 1,
    }
    return {
        "schema": "dsa.moomoo.readonly-export.v1",
        "generated_at": "2026-07-01T20:00:00+00:00",
        "mode": "read_only",
        "journal_database_written": False,
        "account": {
            "environment": "LIVE",
            "market": "US",
            "selection": "unique_auto",
        },
        "window": window,
        "summary": summary,
        "records": {
            "orders": [
                {
                    "order_id": "order-1",
                    "code": "US.EXAMPLE260717C00200000",
                    "stock_name": "Deidentified option",
                    "order_market": "US",
                    "trd_side": "BUY",
                    "order_type": "NORMAL",
                    "order_status": "FILLED_ALL",
                    "qty": 1,
                    "price": 2.5,
                    "create_time": "2026-07-01 09:30:00",
                    "updated_time": "2026-07-01 09:31:00",
                    "dealt_qty": 1,
                    "dealt_avg_price": 2.5,
                    "time_in_force": "DAY",
                    "fill_outside_rth": False,
                    "session": "REGULAR",
                    "currency": "USD",
                    "strategy_type": "NONE",
                    "combo_legs": [],
                }
            ],
            "deals": [
                {
                    "deal_id": "deal-1",
                    "order_id": "order-1",
                    "code": "US.EXAMPLE260717C00200000",
                    "stock_name": "Deidentified option",
                    "deal_market": "US",
                    "trd_side": "BUY",
                    "qty": 1,
                    "price": 2.5,
                    "create_time": "2026-07-01 09:30:30",
                    "status": "OK",
                }
            ],
            "fees": [
                {
                    "order_id": "order-1",
                    "fee_amount": 0.3002,
                    "fee_details": [
                        ["Commission", 0.1],
                        ["Platform Fees", 0.2],
                        ["Consolidated Audit Trail Fees", 0.0002],
                    ],
                }
            ],
        },
    }


def _second_openapi_export_payload() -> dict:
    """A disjoint second window matching the optional Jul 2 CSV row."""
    payload = json.loads(json.dumps(_openapi_export_payload()))
    window = {
        "start": "2026-07-02T09:00:00-04:00",
        "end": "2026-07-02T16:00:00-04:00",
        "timezone": "America/New_York",
        "chunks": 1,
        "max_chunk_days": 7,
    }
    payload["generated_at"] = "2026-07-02T20:00:00+00:00"
    payload["window"] = window
    payload["summary"]["window"] = window
    payload["summary"]["activity_time_range"] = {
        "first": "2026-07-02 09:30:30",
        "last": "2026-07-02 09:30:30",
    }
    order = payload["records"]["orders"][0]
    order.update(
        {
            "order_id": "order-2",
            "price": 3.0,
            "create_time": "2026-07-02 09:30:00",
            "updated_time": "2026-07-02 09:31:00",
            "dealt_avg_price": 3.0,
        }
    )
    deal = payload["records"]["deals"][0]
    deal.update(
        {
            "deal_id": "deal-2",
            "order_id": "order-2",
            "price": 3.0,
            "create_time": "2026-07-02 09:30:30",
        }
    )
    payload["records"]["fees"][0]["order_id"] = "order-2"
    return payload


def _empty_openapi_export_payload() -> dict:
    """A complete zero-activity query beginning at the prior broker watermark."""
    payload = json.loads(json.dumps(_openapi_export_payload()))
    window = {
        "start": "2026-07-01T16:00:00-04:00",
        "end": "2026-07-02T16:00:00-04:00",
        "timezone": "America/New_York",
        "chunks": 1,
        "max_chunk_days": 7,
    }
    payload["generated_at"] = "2026-07-02T20:00:00+00:00"
    payload["window"] = window
    payload["summary"].update(
        window=window,
        warnings=["no_filled_orders_in_window"],
        counts={
            "orders": 0,
            "filled_orders": 0,
            "fills": 0,
            "fees": 0,
            "unique_instruments": 0,
            "option_activity_rows": 0,
        },
        activity_sides={"buy": 0, "sell": 0, "other": 0},
        fee_totals_by_currency={},
        activity_time_range={"first": None, "last": None},
        fee_batches=0,
    )
    payload["records"] = {"orders": [], "deals": [], "fees": []}
    return payload


def _overlapping_openapi_export_payload() -> dict:
    """The first broker rows repeated by a wider immutable probe window."""
    payload = json.loads(json.dumps(_openapi_export_payload()))
    window = {
        "start": "2026-07-01T08:00:00-04:00",
        "end": "2026-07-01T17:00:00-04:00",
        "timezone": "America/New_York",
        "chunks": 1,
        "max_chunk_days": 7,
    }
    payload["generated_at"] = "2026-07-01T21:00:00+00:00"
    payload["window"] = window
    payload["summary"]["window"] = window
    return payload


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal_api.db"))
    import src.config as config_mod
    import src.storage as storage

    config_mod.Config.reset_instance()
    storage.DatabaseManager.reset_instance()
    yield
    storage.DatabaseManager.reset_instance()
    config_mod.Config.reset_instance()


def _client():
    """Build a minimal FastAPI app with just the journal router mounted.

    We avoid pulling the full ``api.app`` to keep the smoke test independent of
    authentication / CORS / lifespan initialisers.
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from api.v1.endpoints import journal

    app = FastAPI()
    app.include_router(journal.router, prefix="/api/v1/journal")
    return TestClient(app)


def _episode_summary(**overrides):
    values = {
        "build_id": 7,
        "build_key": "build-key-7",
        "source_batch_id": 2,
        "account_key": "default_moomoo_us",
        "status": "partial",
        "builder_name": "signed_position_episode_builder",
        "builder_version": "1.1.0",
        "reconciliation_status": "passed",
        "reconciliation_scope": "partial_window",
        "completeness_score": Decimal("0.8333"),
        "opening_boundary_policy": "assumed_flat_unverified",
        "partial_reasons": ("opening_boundary_unverified",),
        "strategy_episode_count": 2,
        "position_episode_count": 2,
        "evidence_allocation_count": 4,
        "unresolved_evidence_count": 0,
        "open_episode_count": 1,
        "closed_episode_count": 1,
        "boundary_unverified_episode_count": 2,
        "left_censored_episode_count": 0,
        "incomplete_episode_count": 2,
        "aggregate_only_episode_count": 1,
        "conditional_closed_episode_count": 1,
        "conditional_realized_pnl_gross": Decimal("125.0000000000"),
        "conditional_total_fee": Decimal("1.2500000000"),
        "conditional_realized_pnl_net": Decimal("123.7500000000"),
        "headline_episode_count": 0,
        "headline_realized_pnl_gross": None,
        "headline_total_fee": None,
        "headline_realized_pnl_net": None,
        "headline_excluded_episode_count": 2,
        "headline_exclusion_counts": {
            "boundary_unverified": 2,
            "open": 1,
            "left_censored": 0,
            "incomplete": 2,
            "pnl_unavailable": 1,
        },
        "source_event_count": 4,
        "aggregate_order_event_count": 1,
        "detailed_fill_event_count": 3,
        "source_known_fee_total": Decimal("1.2500000000"),
        "allocated_known_fee_total": Decimal("1.2500000000"),
        "fee_conserved": True,
        "source_window_start": datetime(2026, 3, 4, tzinfo=timezone.utc),
        "source_cutoff_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        "recorded_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _episode_item(**overrides):
    values = {
        "episode_id": 41,
        "build_id": 7,
        "strategy_episode_id": 51,
        "episode_key": "episode-key-41",
        "lineage_key": "lineage-key-41",
        "strategy_type": "single_position_unclassified",
        "raw_symbol": "EXAMPLE260717C00200000",
        "asset_type": "option",
        "underlying": "EXAMPLE",
        "expiry": date(2026, 7, 17),
        "strike": Decimal("200.0000000000"),
        "option_right": "CALL",
        "contract_multiplier": Decimal("100.00000000"),
        "contract_multiplier_basis": "evidence_derived_from_amount",
        "direction": "long",
        "lifecycle_status": "closed",
        "currency": "USD",
        "opened_at": datetime(2026, 6, 20, 14, 31, tzinfo=timezone.utc),
        "closed_at": datetime(2026, 6, 20, 15, 10, tzinfo=timezone.utc),
        "hold_seconds": 2340,
        "opened_quantity": Decimal("1.0000000000"),
        "closed_quantity": Decimal("1.0000000000"),
        "remaining_quantity": Decimal("0E-10"),
        "average_entry_price": Decimal("2.5000000000"),
        "average_exit_price": Decimal("3.7500000000"),
        "realized_pnl_gross": Decimal("125.0000000000"),
        "total_fee": Decimal("1.2500000000"),
        "realized_pnl_net": Decimal("123.7500000000"),
        "construction_basis": "trade_flow_assumed_flat",
        "opening_boundary_policy": "assumed_flat_unverified",
        "left_boundary_verified": False,
        "is_left_censored": False,
        "is_right_censored": False,
        "completeness_score": Decimal("0.8333"),
        "completeness_status": "partial",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _episode_health():
    return SimpleNamespace(
        batch_id=2,
        reconciliation_window_start=datetime(
            2026, 6, 20, 3, 59, 59, tzinfo=timezone.utc
        ),
        reconciliation_window_end=datetime(
            2026, 7, 20, 3, 59, 59, tzinfo=timezone.utc
        ),
        reconciled_order_observations=1089,
        order_observations=3542,
    )


def _seed():
    """Import the fixture CSV via the storage layer so subsequent API calls see data."""
    from src.journal.brokers.moomoo_us import parse as parse_moomoo
    from src.journal.matcher import match_legs_fifo
    from src.journal.storage import (
        init_journal_schema,
        insert_events_from_orders,
        query_events_for_matching,
        record_import,
        replace_trades,
    )

    content = FIXTURE.read_bytes()
    init_journal_schema()
    orders = parse_moomoo(content)
    import_id = record_import(
        source_path=str(FIXTURE),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
    )
    assert import_id is not None
    insert_events_from_orders(import_id, orders)
    events = query_events_for_matching()
    trades = match_legs_fifo(events)
    replace_trades(trades)


def test_reality_test_endpoint():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/reality-test", params={"top_n": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_trades"] >= 2
    assert "top_n_ids" in body


def test_trades_list_paginated():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/trades", params={"per_page": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert len(body["items"]) == 1
    assert "underlying" in body["items"][0]


def test_trades_list_filter_by_symbol():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/trades", params={"symbol": "NVDA"})
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        assert item["underlying"] == "NVDA"


def test_get_and_patch_trade():
    _seed()
    c = _client()
    listed = c.get("/api/v1/journal/trades").json()
    tid = listed["items"][0]["id"]
    got = c.get(f"/api/v1/journal/trades/{tid}")
    assert got.status_code == 200

    resp = c.patch(
        f"/api/v1/journal/trades/{tid}",
        json={"user_notes": "FOMO", "emotional_state": "fomo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_notes"] == "FOMO"
    assert body["emotional_state"] == "fomo"


def test_get_trade_404():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/trades/999999")
    assert resp.status_code == 404


def test_stats_endpoint():
    _seed()
    c = _client()
    resp = c.get("/api/v1/journal/stats", params={"days": 365})
    assert resp.status_code == 200
    body = resp.json()
    assert body["closed_trade_count"] >= 1
    assert "dte_distribution" in body
    assert "reality_test" in body


def test_legacy_import_endpoint_is_disabled_without_writing_db():
    c = _client()
    db_path = Path(os.environ["DATABASE_PATH"])
    with open(FIXTURE, "rb") as fh:
        resp = c.post(
            "/api/v1/journal/import",
            files={"file": ("moomoo_inline_sample.csv", fh, "text/csv")},
        )
    assert resp.status_code == 410
    assert "legacy Moomoo CSV import is disabled" in resp.json()["detail"]
    assert not db_path.exists()


def test_v2_import_preview_is_read_only_and_marks_aggregate_evidence():
    c = _client()
    db_path = Path(os.environ["DATABASE_PATH"])
    resp = c.post(
        "/api/v1/journal/v2/imports/preview",
        files={
            "file": (
                "history.csv",
                _current_moomoo_csv(include_order=True),
                "text/csv",
            )
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_level"] == "partial"
    assert body["orders_total"] == 1
    assert body["filled_orders"] == 1
    assert body["detail_backed_filled_orders"] == 0
    assert body["aggregate_only_filled_orders"] == 1
    assert body["fill_records"] == 0
    assert body["filled_fee_total"] == "0.3002"
    assert body["journal_database_written"] is False
    assert not db_path.exists()


_COMBO_EXPORT_HEADER = (
    '"Side","Symbol","Name","Order Price","Order Qty","Order Amount",'
    '"Status","Filled@Avg Price","Order Time","Order Type","Time-in-Force",'
    '"Allow Pre-Market","Session","Trigger price","Position Opening",'
    '"Markets","Currency","Order Source","Fill Qty","Fill Price",'
    '"Fill Amount","Fill Time","Markets","Currency","Counterparty",'
    '"Remarks","Commission","Platform Fees","Options Regulatory Fees",'
    '"OCC Fees","Contract Fees","Consolidated Audit Trail Fees","SEC Fees",'
    '"Trading Activity Fees","Total","Settlement Fees"'
)


def _combo_moomoo_csv() -> bytes:
    rows = (
        _COMBO_EXPORT_HEADER,
        '"Buy","MU260731P745000","MU 260731 745.00P","24.35","4","9,740.00",'
        '"Filled","4@24.35","Jul 29, 2026 15:36:46 ET","Limit","Day","","",'
        '"","","US","USD","","4","24.35","9,740.00",'
        '"Jul 29, 2026 15:36:47 ET","US","USD","","","2.6","1.2","0.05",'
        '"0.1","2.6","0","","","6.55",""',
        '"Sell","MU260731P745/760"," Vertical","6.70","2unit(s)","1,340.00",'
        '"Filled","2unit(s)@7.00","Jul 29, 2026 15:35:34 ET","Limit","Day",'
        '"","","","","US","USD","","","","","","","","","","3.98","1.2",'
        '"0.04","0.12","2.6","0","0.12","0.02","8.08",""',
        '"Buy","MU260731P745000","MU 260731 745.00P","","2","","","","","",'
        '"","","","","","","","","1","24.30","2,430.00",'
        '"Jul 29, 2026 15:35:50 ET","US","USD","","","","","","","","","",'
        '"","",""',
        '"","","","","","","","","","","","","","","","","","","1","24.30",'
        '"2,430.00","Jul 29, 2026 15:35:50 ET","US","USD","","","","","",'
        '"","","","","","",""',
        '"Sell","MU260731P760000","MU 260731 760.00P","","2","","","","",'
        '"","","","","","","","","","1","31.30","3,130.00",'
        '"Jul 29, 2026 15:35:50 ET","US","USD","","","","","","","","","",'
        '"","",""',
        '"","","","","","","","","","","","","","","","","","","1","31.30",'
        '"3,130.00","Jul 29, 2026 15:35:50 ET","US","USD","","","","","",'
        '"","","","","","",""',
    )
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_v2_import_preview_reports_combo_parent_counts():
    c = _client()
    db_path = Path(os.environ["DATABASE_PATH"])
    resp = c.post(
        "/api/v1/journal/v2/imports/preview",
        files={"file": ("history.csv", _combo_moomoo_csv(), "text/csv")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_level"] == "partial"
    assert body["orders_total"] == 2
    assert body["combo_parent_orders"] == 1
    assert body["combo_parent_leg_rows"] == 2
    assert body["combo_parent_fee_total"] == "8.08"
    assert body["detail_backed_filled_orders"] == 1
    assert body["aggregate_only_filled_orders"] == 0
    assert body["inconsistent_filled_orders"] == 0
    assert "combo_parent_orders_are_audit_only_evidence" in body["warnings"]
    assert (
        "older_filled_orders_have_aggregate_evidence_only"
        not in body["warnings"]
    )
    assert body["journal_database_written"] is False
    assert not db_path.exists()


def test_v2_import_appends_combo_parent_as_execution_group():
    c = _client()
    resp = c.post(
        "/api/v1/journal/v2/imports?allow_partial=true",
        files={"file": ("history.csv", _combo_moomoo_csv(), "text/csv")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_level"] == "partial"
    assert body["order_observations"] == 1
    assert body["fill_observations"] == 1
    assert body["execution_group_observations"] == 1
    assert body["legacy_journal_written"] is False

    from src.journal.ledger.models import (
        BrokerExecutionGroupLegObservation,
        BrokerExecutionGroupObservation,
        BrokerOrderObservation,
    )
    from src.storage import get_db
    from sqlalchemy import func, select

    db = get_db()
    with db.session_scope() as session:
        assert session.execute(
            select(func.count(BrokerOrderObservation.id))
        ).scalar_one() == 1
        groups = session.execute(
            select(BrokerExecutionGroupObservation)
        ).scalars().all()
        assert len(groups) == 1
        assert groups[0].raw_parent_symbol == "MU260731P745/760"
        assert (
            groups[0].parent_quantity_semantics
            == "csv_combo_package_units_audit_only"
        )
        assert session.execute(
            select(func.count(BrokerExecutionGroupLegObservation.id))
        ).scalar_one() == 0


def test_v2_import_preview_marks_header_only_export_blocked():
    c = _client()
    resp = c.post(
        "/api/v1/journal/v2/imports/preview",
        files={
            "file": (
                "history.csv",
                _current_moomoo_csv(include_order=False),
                "text/csv",
            )
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_level"] == "blocked"
    assert body["orders_total"] == 0
    assert "no_order_rows" in body["warnings"]


def test_v2_openapi_preview_is_strict_and_read_only():
    c = _client()
    db_path = Path(os.environ["DATABASE_PATH"])
    resp = c.post(
        "/api/v1/journal/v2/openapi-imports/preview",
        files={
            "file": (
                "readonly-export.json",
                json.dumps(_openapi_export_payload()),
                "application/json",
            )
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_schema"] == "dsa.moomoo.readonly-export.v1"
    assert body["analysis_level"] == "exact"
    assert body["analysis_ready"] is True
    assert body["reconciliation_status"] == "passed"
    assert body["order_observations"] == 1
    assert body["ordinary_order_observations"] == 1
    assert body["unclassified_parent_observations"] == 0
    assert body["fill_observations"] == 1
    assert body["fee_observations"] == 1
    assert body["contract_spec_observations"] == 0
    assert body["execution_group_observations"] == 0
    assert body["execution_group_leg_observations"] == 0
    assert body["execution_group_fill_links"] == 0
    assert body["execution_group_fee_observations"] == 0
    assert body["fee_totals_by_currency"] == {"USD": "0.3002"}
    assert body["journal_database_written"] is False
    assert not db_path.exists()


def test_v2_openapi_preview_rejects_a_write_capable_claim():
    c = _client()
    payload = _openapi_export_payload()
    payload["journal_database_written"] = True
    resp = c.post(
        "/api/v1/journal/v2/openapi-imports/preview",
        files={
            "file": (
                "unsafe.json",
                json.dumps(payload),
                "application/json",
            )
        },
    )

    assert resp.status_code == 422
    assert "stayed untouched" in resp.json()["detail"]


def test_v2_openapi_plan_without_csv_baseline_is_read_only_and_blocked():
    c = _client()
    response = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={
            "file": (
                "readonly.json",
                json.dumps(_openapi_export_payload()),
                "application/json",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["confirm_allowed"] is False
    assert body["base_scope"] is None
    assert body["journal_database_written"] is False
    assert body["source_scope"]["ordinary_order_observations"] == 1
    assert body["source_scope"]["unclassified_parent_observations"] == 0
    assert body["source_scope"]["contract_spec_observations"] == 0
    assert body["source_scope"]["execution_group_observations"] == 0
    assert body["write_plan"]["ordinary_order_observations"] == 0
    assert body["write_plan"]["unclassified_parent_observations"] == 0
    assert body["canonical_impact"][
        "input_execution_group_observations"
    ] == 0
    assert [item["code"] for item in body["issues"]] == [
        "no_accepted_csv_baseline"
    ]
    db_path = Path(os.environ["DATABASE_PATH"])
    if db_path.exists():
        import sqlite3

        with sqlite3.connect(db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM journal_v2_import_batches"
            ).fetchone()[0]
        assert count == 0


def test_v2_openapi_plan_and_confirm_preserve_execution_group_counts(
    monkeypatch,
):
    from api.v1.endpoints import journal as journal_endpoint

    preview_key = "a" * 64
    canonical_sha256 = "b" * 64
    plan_payload = {
        "preview_key": preview_key,
        "confirm_allowed": False,
        "source_scope": {
            "environment": "LIVE",
            "market": "US",
            "account_selection": "unique_auto",
            "account_bound": True,
            "window_start": "2026-07-01T09:00:00-04:00",
            "window_end": "2026-07-01T16:00:00-04:00",
            "source_timezone": "America/New_York",
            "order_observations": 3,
            "ordinary_order_observations": 1,
            "unclassified_parent_observations": 1,
            "fill_observations": 4,
            "fee_observations": 2,
            "contract_spec_observations": 2,
            "execution_group_observations": 1,
            "execution_group_leg_observations": 2,
            "execution_group_fill_links": 3,
            "execution_group_fee_observations": 1,
            "fee_totals_by_currency": {"USD": "8.08"},
        },
        "base_scope": None,
        "coverage": {
            "scope": "unavailable",
            "matched_orders": 0,
            "csv_only_in_window": 0,
            "api_only_orders": 3,
            "ambiguous_identity_keys": 0,
            "covered_base_orders": 0,
            "base_orders": 0,
            "coverage_ratio": "0",
            "outside_unverified_orders": 0,
        },
        "write_plan": {
            "already_imported": False,
            "order_observations": 3,
            "ordinary_order_observations": 1,
            "unclassified_parent_observations": 1,
            "fill_observations": 4,
            "fee_observations": 2,
            "execution_group_observations": 1,
            "execution_group_leg_observations": 2,
            "execution_group_fill_links": 3,
            "execution_group_fee_observations": 1,
            "order_identity_links": 0,
            "deal_identity_links": 0,
            "fill_set_attestations": 0,
            "canonical_sets": 1,
        },
        "canonical_impact": {
            "input_order_observations": 1,
            "input_fill_observations": 4,
            "canonical_orders": 1,
            "canonical_fills": 4,
            "duplicate_order_observations": 0,
            "duplicate_fill_observations": 0,
            "shadowed_csv_fills": 0,
            "shadowed_aggregate_orders": 0,
            "blocking_issues": 1,
            "analysis_ready": False,
            "canonical_set_sha256": canonical_sha256,
            "input_execution_group_observations": 1,
            "canonical_execution_groups": 1,
            "canonical_execution_group_legs": 2,
            "duplicate_execution_group_observations": 0,
        },
        "issues": [],
        "warnings": [],
        "scope_is_full_batch": False,
        "journal_database_written": False,
    }
    monkeypatch.setattr(
        journal_endpoint,
        "plan_openapi_import",
        lambda *_args, **_kwargs: SimpleNamespace(
            as_dict=lambda: plan_payload
        ),
    )

    client = _client()
    encoded_payload = json.dumps(_openapi_export_payload())
    planned = client.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("readonly.json", encoded_payload, "application/json")},
    )
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    assert plan["source_scope"]["contract_spec_observations"] == 2
    assert plan["source_scope"]["execution_group_leg_observations"] == 2
    assert plan["write_plan"]["unclassified_parent_observations"] == 1
    assert plan["write_plan"]["execution_group_fill_links"] == 3
    assert plan["canonical_impact"]["canonical_execution_groups"] == 1
    assert plan["canonical_impact"]["canonical_execution_group_legs"] == 2

    monkeypatch.setattr(
        journal_endpoint,
        "confirm_openapi_import_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="appended",
            duplicate=False,
            import_batch_id=12,
            canonical_set_id=34,
            canonical_set_sha256=canonical_sha256,
            scope="incremental_tail",
            appended={
                "orders": 2,
                "ordinary_orders": 1,
                "fills": 4,
                "fees": 2,
                "execution_groups": 1,
                "execution_group_legs": 2,
                "execution_group_fill_links": 3,
                "execution_group_fees": 1,
                "order_links": 0,
                "deal_links": 0,
                "fill_set_attestations": 0,
                "canonical_sets": 1,
            },
            canonical={
                "orders": 1,
                "fills": 4,
                "execution_groups": 1,
                "execution_group_legs": 2,
                "shadowed_csv_fills": 0,
                "shadowed_aggregate_orders": 0,
                "blocking_issues": 0,
                "analysis_ready": True,
            },
            message="appended",
        ),
    )
    confirmed = client.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={"preview_key": preview_key},
        files={"file": ("readonly.json", encoded_payload, "application/json")},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["appended"]["ordinary_orders"] == 1
    assert body["appended"]["execution_groups"] == 1
    assert body["appended"]["execution_group_legs"] == 2
    assert body["appended"]["execution_group_fill_links"] == 3
    assert body["appended"]["execution_group_fees"] == 1
    assert body["canonical"]["execution_groups"] == 1
    assert body["canonical"]["execution_group_legs"] == 2


def test_v2_openapi_plan_confirm_is_atomic_and_idempotent():
    c = _client()
    csv_response = c.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    )
    assert csv_response.status_code == 200

    payload = json.dumps(_openapi_export_payload())
    plan_response = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("readonly.json", payload, "application/json")},
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["confirm_allowed"] is True
    assert plan["journal_database_written"] is False
    assert plan["coverage"] == {
        "scope": "full_batch",
        "matched_orders": 1,
        "csv_only_in_window": 0,
        "api_only_orders": 0,
        "overlap_api_only_orders": 0,
        "incremental_api_only_orders": 0,
        "ambiguous_identity_keys": 0,
        "covered_base_orders": 1,
        "base_orders": 1,
        "coverage_ratio": "1.0000",
        "outside_unverified_orders": 0,
    }
    assert plan["canonical_impact"]["canonical_orders"] == 1
    assert plan["canonical_impact"]["canonical_fills"] == 1
    assert plan["canonical_impact"]["blocking_issues"] == 0

    unacknowledged = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={"preview_key": plan["preview_key"]},
        files={"file": ("readonly.json", payload, "application/json")},
    )
    # This fixture covers its entire one-order baseline, so no partial-window
    # acknowledgement is required.
    assert unacknowledged.status_code == 200
    first = unacknowledged.json()
    assert first["status"] == "appended"
    assert first["appended"] == {
        "orders": 1,
        "ordinary_orders": 1,
        "fills": 1,
        "fees": 1,
        "execution_groups": 0,
        "execution_group_legs": 0,
        "execution_group_fill_links": 0,
        "execution_group_fees": 0,
        "order_links": 1,
        "deal_links": 1,
        "fill_set_attestations": 0,
        "canonical_sets": 1,
    }
    assert first["canonical"]["analysis_ready"] is True
    assert first["canonical"]["execution_groups"] == 0
    assert first["canonical"]["execution_group_legs"] == 0
    assert first["legacy_journal_written"] is False
    assert first["episode_build_triggered"] is False
    assert first["trading_action_performed"] is False

    second_plan = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("readonly.json", payload, "application/json")},
    ).json()
    duplicate_response = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={
            "preview_key": second_plan["preview_key"],
            "acknowledge_partial_window": True,
        },
        files={"file": ("readonly.json", payload, "application/json")},
    )
    assert duplicate_response.status_code == 200
    duplicate = duplicate_response.json()
    assert duplicate["status"] == "already_present"
    assert duplicate["duplicate"] is True
    assert all(value == 0 for value in duplicate["appended"].values())
    assert duplicate["canonical_set_sha256"] == first["canonical_set_sha256"]


def test_v2_server_owned_refresh_preview_then_explicit_confirm(monkeypatch):
    from api.v1.endpoints import journal as journal_endpoint
    from src.journal.ledger.models import ImportBatch
    from src.journal.ledger.refresh_models import (
        JournalRefreshArtifact,
        JournalRefreshPublication,
    )
    from src.journal.ledger.refresh_repository import get_journal_refresh_status
    from src.storage import get_db

    client = _client()
    baseline = client.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    )
    assert baseline.status_code == 200

    payload = _openapi_export_payload()
    payload["account"]["binding"] = "d" * 64
    payload["summary"].update(
        retrieval_complete=True,
        coverage_complete=True,
        has_activity=True,
    )
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_ENV", "LIVE")
    monkeypatch.setenv(
        "MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET",
        "server-owned-refresh-test-secret-1234567890",
    )
    monkeypatch.setattr(
        journal_endpoint,
        "suggest_refresh_window",
        lambda *_args, **_kwargs: (
            datetime(
                2026,
                7,
                1,
                9,
                tzinfo=ZoneInfo("America/New_York"),
            ),
            datetime(
                2026,
                7,
                1,
                16,
                tzinfo=ZoneInfo("America/New_York"),
            ),
        ),
    )
    monkeypatch.setattr(
        journal_endpoint,
        "run_readonly_probe",
        lambda _config: SimpleNamespace(export_payload=payload),
    )

    preview_response = client.post(
        "/api/v1/journal/v2/refreshes/preview",
        json={"overlap_days": 7},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["evidence_written"] is False
    assert preview["trading_action_performed"] is False
    assert preview["source"] == {
        "retrieval_complete": True,
        "coverage_complete": True,
        "has_activity": True,
        "broker_queried_through": "2026-07-01T16:00:00-04:00",
        "latest_fill_at": "2026-07-01T09:30:30-04:00",
        "order_observations": 1,
        "ordinary_order_observations": 1,
        "unclassified_parent_observations": 0,
        "fill_observations": 1,
        "fee_observations": 1,
        "contract_spec_observations": 0,
        "execution_group_observations": 0,
        "execution_group_leg_observations": 0,
        "execution_group_fill_links": 0,
        "execution_group_fee_observations": 0,
    }
    assert preview["plan"]["confirm_allowed"] is True, preview["plan"]["issues"]

    db = get_db()
    with db.session_scope() as session:
        assert session.query(JournalRefreshArtifact).count() == 1
        assert session.query(JournalRefreshPublication).count() == 0
        assert session.query(ImportBatch).count() == 1

    confirmed_response = client.post(
        f"/api/v1/journal/v2/refreshes/{preview['artifact_id']}/confirm",
        json={
            "preview_key": preview["plan"]["preview_key"],
            "acknowledge_partial_window": True,
        },
    )
    assert confirmed_response.status_code == 200, confirmed_response.text
    confirmed = confirmed_response.json()
    assert confirmed["artifact_id"] == preview["artifact_id"]
    assert confirmed["trading_action_performed"] is False
    assert confirmed["publication"]["broker_queried_through"] == (
        "2026-07-01T20:00:00Z"
    )
    assert confirmed["imported"]["canonical"]["analysis_ready"] is True

    with db.session_scope() as session:
        assert session.query(JournalRefreshPublication).count() == 1
        assert session.query(ImportBatch).count() == 2

    status = get_journal_refresh_status(
        "default_moomoo_us",
        now=datetime(
            2026,
            7,
            1,
            17,
            tzinfo=ZoneInfo("America/New_York"),
        ),
    )
    assert status.freshness_state == "evidence_current"
    assert status.pending_stage == "build"
    assert status.broker_queried_through == datetime(
        2026,
        7,
        1,
        20,
        tzinfo=timezone.utc,
    )
    assert status.latest_fill_at == datetime(
        2026,
        7,
        1,
        13,
        30,
        30,
        tzinfo=timezone.utc,
    )

    repeated = client.post(
        f"/api/v1/journal/v2/refreshes/{preview['artifact_id']}/confirm",
        json={
            "preview_key": preview["plan"]["preview_key"],
            "acknowledge_partial_window": True,
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["publication"]["publication_id"] == confirmed[
        "publication"
    ]["publication_id"]


def test_v2_server_owned_refresh_rejects_a_gap_after_csv_baseline():
    from dataclasses import replace

    from src.journal.brokers.moomoo_openapi_export import parse_openapi_export
    from src.journal.ledger.openapi_repository import plan_openapi_import
    from src.journal.ledger.refresh_repository import (
        JournalRefreshError,
        save_refresh_artifact,
    )

    client = _client()
    baseline = client.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    )
    assert baseline.status_code == 200

    payload = _openapi_export_payload()
    payload["account"]["binding"] = "d" * 64
    payload["summary"].update(
        retrieval_complete=True,
        coverage_complete=True,
        has_activity=True,
    )
    preview = parse_openapi_export(payload)
    discontinuous = replace(
        preview,
        metadata=replace(
            preview.metadata,
            window_start=datetime(
                2026,
                7,
                1,
                10,
                0,
                tzinfo=ZoneInfo("America/New_York"),
            ),
        ),
    )
    plan = plan_openapi_import(preview, payload)

    with pytest.raises(JournalRefreshError, match="discontinuous"):
        save_refresh_artifact(
            discontinuous,
            payload,
            plan,
            account_key="default_moomoo_us",
        )


def test_v2_server_owned_empty_refresh_advances_query_watermark(monkeypatch):
    from api.v1.endpoints import journal as journal_endpoint

    client = _client()
    baseline = client.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    )
    assert baseline.status_code == 200

    activity_payload = _openapi_export_payload()
    activity_payload["account"]["binding"] = "e" * 64
    activity_payload["summary"].update(
        retrieval_complete=True,
        coverage_complete=True,
        has_activity=True,
    )
    first_start = datetime(
        2026,
        7,
        1,
        9,
        tzinfo=ZoneInfo("America/New_York"),
    )
    first_end = datetime(
        2026,
        7,
        1,
        16,
        tzinfo=ZoneInfo("America/New_York"),
    )
    empty_start = first_end
    empty_end = datetime(
        2026,
        7,
        2,
        16,
        tzinfo=ZoneInfo("America/New_York"),
    )
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_ENV", "LIVE")
    monkeypatch.setenv(
        "MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET",
        "server-owned-empty-refresh-test-secret-1234",
    )
    monkeypatch.setattr(
        journal_endpoint,
        "suggest_refresh_window",
        lambda *_args, **_kwargs: (first_start, first_end),
    )
    monkeypatch.setattr(
        journal_endpoint,
        "run_readonly_probe",
        lambda _config: SimpleNamespace(export_payload=activity_payload),
    )

    first_preview_response = client.post(
        "/api/v1/journal/v2/refreshes/preview",
        json={"overlap_days": 7},
    )
    assert first_preview_response.status_code == 200, first_preview_response.text
    first_preview = first_preview_response.json()
    first_confirm = client.post(
        f"/api/v1/journal/v2/refreshes/{first_preview['artifact_id']}/confirm",
        json={
            "preview_key": first_preview["plan"]["preview_key"],
            "acknowledge_partial_window": True,
        },
    )
    assert first_confirm.status_code == 200, first_confirm.text

    payload = _empty_openapi_export_payload()
    payload["account"]["binding"] = "e" * 64
    payload["summary"].update(
        retrieval_complete=True,
        coverage_complete=True,
        has_activity=False,
    )
    monkeypatch.setattr(
        journal_endpoint,
        "suggest_refresh_window",
        lambda *_args, **_kwargs: (empty_start, empty_end),
    )
    monkeypatch.setattr(
        journal_endpoint,
        "run_readonly_probe",
        lambda _config: SimpleNamespace(export_payload=payload),
    )
    preview_response = client.post(
        "/api/v1/journal/v2/refreshes/preview",
        json={"overlap_days": 7},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["source"]["has_activity"] is False
    assert preview["source"]["latest_fill_at"] is None
    assert preview["source"]["order_observations"] == 0
    assert preview["plan"]["confirm_allowed"] is True, preview["plan"]["issues"]

    confirmed_response = client.post(
        f"/api/v1/journal/v2/refreshes/{preview['artifact_id']}/confirm",
        json={
            "preview_key": preview["plan"]["preview_key"],
            "acknowledge_partial_window": True,
        },
    )
    assert confirmed_response.status_code == 200, confirmed_response.text
    publication = confirmed_response.json()["publication"]
    assert publication["broker_queried_through"] == "2026-07-02T20:00:00Z"
    assert publication["latest_fill_at"] is None


def test_v2_server_owned_refresh_rejects_probe_scope_drift(monkeypatch):
    from api.v1.endpoints import journal as journal_endpoint

    client = _client()
    baseline = client.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    )
    assert baseline.status_code == 200

    payload = _openapi_export_payload()
    payload["account"]["binding"] = "f" * 64
    payload["summary"].update(
        retrieval_complete=True,
        coverage_complete=True,
        has_activity=True,
    )
    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_REFRESH_ENABLED", "true")
    monkeypatch.setenv("MOOMOO_JOURNAL_ENV", "LIVE")
    monkeypatch.setenv(
        "MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET",
        "server-owned-scope-drift-test-secret-12345",
    )
    monkeypatch.setattr(
        journal_endpoint,
        "suggest_refresh_window",
        lambda *_args, **_kwargs: (
            datetime(
                2026,
                7,
                1,
                8,
                tzinfo=ZoneInfo("America/New_York"),
            ),
            datetime(
                2026,
                7,
                1,
                16,
                tzinfo=ZoneInfo("America/New_York"),
            ),
        ),
    )
    monkeypatch.setattr(
        journal_endpoint,
        "run_readonly_probe",
        lambda _config: SimpleNamespace(export_payload=payload),
    )

    response = client.post(
        "/api/v1/journal/v2/refreshes/preview",
        json={"overlap_days": 7},
    )
    assert response.status_code == 409
    assert "server-owned query scope" in response.json()["detail"]


def test_v2_openapi_plan_is_cumulative_across_disjoint_windows():
    c = _client()
    csv_response = c.post(
        "/api/v1/journal/v2/imports",
        files={
            "file": (
                "history.csv",
                _matching_openapi_csv(include_outside_window=True),
                "text/csv",
            )
        },
    )
    assert csv_response.status_code == 200

    first_payload = json.dumps(_openapi_export_payload())
    first_plan = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("first.json", first_payload, "application/json")},
    ).json()
    first_confirm = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={
            "preview_key": first_plan["preview_key"],
            "acknowledge_partial_window": True,
        },
        files={"file": ("first.json", first_payload, "application/json")},
    )
    assert first_confirm.status_code == 200

    second_payload = json.dumps(_second_openapi_export_payload())
    second_plan_response = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("second.json", second_payload, "application/json")},
    )
    assert second_plan_response.status_code == 200
    second_plan = second_plan_response.json()
    assert second_plan["confirm_allowed"] is True
    assert second_plan["canonical_impact"]["input_order_observations"] == 4
    assert second_plan["canonical_impact"]["input_fill_observations"] == 4
    assert second_plan["canonical_impact"]["canonical_orders"] == 2
    assert second_plan["canonical_impact"]["canonical_fills"] == 2

    second_confirm = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={
            "preview_key": second_plan["preview_key"],
            "acknowledge_partial_window": True,
        },
        files={"file": ("second.json", second_payload, "application/json")},
    )
    assert second_confirm.status_code == 200
    second_result = second_confirm.json()
    assert second_result["status"] == "appended"
    assert second_result["canonical"]["orders"] == 2
    assert second_result["canonical"]["fills"] == 2

    duplicate_plan = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("second.json", second_payload, "application/json")},
    ).json()
    assert duplicate_plan["canonical_impact"]["input_order_observations"] == 4
    assert duplicate_plan["canonical_impact"]["input_fill_observations"] == 4
    duplicate_confirm = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={
            "preview_key": duplicate_plan["preview_key"],
            "acknowledge_partial_window": True,
        },
        files={"file": ("second.json", second_payload, "application/json")},
    )
    assert duplicate_confirm.status_code == 200
    duplicate_result = duplicate_confirm.json()
    assert duplicate_result["status"] == "already_present"
    assert all(value == 0 for value in duplicate_result["appended"].values())
    assert (
        duplicate_result["canonical_set_sha256"]
        == second_result["canonical_set_sha256"]
    )


def test_v2_openapi_overlapping_window_reuses_existing_identity_proofs():
    c = _client()
    assert c.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    ).status_code == 200

    first_payload = json.dumps(_openapi_export_payload())
    first_plan = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("first.json", first_payload, "application/json")},
    ).json()
    assert c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={"preview_key": first_plan["preview_key"]},
        files={"file": ("first.json", first_payload, "application/json")},
    ).status_code == 200

    overlap_payload = json.dumps(_overlapping_openapi_export_payload())
    overlap_plan_response = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("overlap.json", overlap_payload, "application/json")},
    )
    assert overlap_plan_response.status_code == 200
    overlap_plan = overlap_plan_response.json()
    assert overlap_plan["confirm_allowed"] is True
    assert overlap_plan["write_plan"] == {
        "already_imported": False,
        "order_observations": 1,
        "ordinary_order_observations": 1,
        "unclassified_parent_observations": 0,
        "fill_observations": 1,
        "fee_observations": 1,
        "execution_group_observations": 0,
        "execution_group_leg_observations": 0,
        "execution_group_fill_links": 0,
        "execution_group_fee_observations": 0,
        "order_identity_links": 0,
        "deal_identity_links": 0,
        "fill_set_attestations": 0,
        "canonical_sets": 1,
    }
    assert overlap_plan["canonical_impact"]["input_order_observations"] == 3
    assert overlap_plan["canonical_impact"]["input_fill_observations"] == 3
    assert overlap_plan["canonical_impact"]["canonical_orders"] == 1
    assert overlap_plan["canonical_impact"]["canonical_fills"] == 1

    overlap_confirm = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={"preview_key": overlap_plan["preview_key"]},
        files={"file": ("overlap.json", overlap_payload, "application/json")},
    )
    assert overlap_confirm.status_code == 200, overlap_confirm.json()
    result = overlap_confirm.json()
    assert result["status"] == "appended"
    assert result["appended"]["orders"] == 1
    assert result["appended"]["fills"] == 1
    assert result["appended"]["fees"] == 1
    assert result["appended"]["order_links"] == 0
    assert result["appended"]["deal_links"] == 0


def test_v2_openapi_partial_window_requires_explicit_acknowledgement():
    c = _client()
    assert c.post(
        "/api/v1/journal/v2/imports",
        files={
            "file": (
                "history.csv",
                _matching_openapi_csv(include_outside_window=True),
                "text/csv",
            )
        },
    ).status_code == 200
    payload = json.dumps(_openapi_export_payload())
    plan = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("readonly.json", payload, "application/json")},
    ).json()
    assert plan["coverage"]["scope"] == "partial_window"
    assert plan["coverage"]["matched_orders"] == 1
    assert plan["coverage"]["base_orders"] == 2
    assert plan["coverage"]["outside_unverified_orders"] == 1

    blocked = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={"preview_key": plan["preview_key"]},
        files={"file": ("readonly.json", payload, "application/json")},
    )
    assert blocked.status_code == 409
    assert "partial_window_requires" in blocked.json()["detail"]

    confirmed = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={
            "preview_key": plan["preview_key"],
            "acknowledge_partial_window": True,
        },
        files={"file": ("readonly.json", payload, "application/json")},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["scope"] == "partial_window"


def test_v2_openapi_confirm_rejects_a_stale_preview_without_rows():
    c = _client()
    assert c.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    ).status_code == 200
    payload = json.dumps(_openapi_export_payload())
    response = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={
            "preview_key": "0" * 64,
            "acknowledge_partial_window": True,
        },
        files={"file": ("readonly.json", payload, "application/json")},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "stale_preview"
    db_path = Path(os.environ["DATABASE_PATH"])
    import sqlite3

    with sqlite3.connect(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM journal_v2_import_batches "
            "WHERE source_kind = 'openapi'"
        ).fetchone()[0]
    assert count == 0


def test_v2_import_requires_confirmation_for_partial_evidence():
    c = _client()
    db_path = Path(os.environ["DATABASE_PATH"])
    resp = c.post(
        "/api/v1/journal/v2/imports",
        files={
            "file": (
                "history.csv",
                _current_moomoo_csv(include_order=True),
                "text/csv",
            )
        },
    )

    assert resp.status_code == 409
    assert not db_path.exists()


def test_v2_import_appends_once_and_never_rebuilds_legacy_journal():
    c = _client()
    files = {
        "file": (
            "history.csv",
            _current_moomoo_csv(include_order=True),
            "text/csv",
        )
    }
    first = c.post(
        "/api/v1/journal/v2/imports",
        params={"allow_partial": "true"},
        files=files,
    )

    assert first.status_code == 200
    body = first.json()
    assert body["duplicate"] is False
    assert body["analysis_level"] == "partial"
    assert body["order_observations"] == 1
    assert body["fill_observations"] == 0
    assert body["legacy_journal_written"] is False

    second = c.post(
        "/api/v1/journal/v2/imports",
        params={"allow_partial": "true"},
        files=files,
    )
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["batch_id"] == body["batch_id"]

    health = c.get("/api/v1/journal/v2/data-health")
    assert health.status_code == 200
    health_body = health.json()
    assert health_body["has_data"] is True
    assert health_body["analysis_level"] == "partial"
    assert health_body["reconciliation_status"] == "not_run"
    assert health_body["reconciliation_scope"] == "not_run"
    assert health_body["reconciled_order_observations"] == 0
    assert health_body["order_observations"] == 1
    assert health_body["fill_observations"] == 0
    assert health_body["aggregate_only_filled_orders"] == 1
    assert health_body["legacy_journal_written"] is False


def test_v2_data_health_is_explicit_when_no_batch_exists():
    c = _client()
    resp = c.get("/api/v1/journal/v2/data-health")

    assert resp.status_code == 200
    assert resp.json() == {
        "has_data": False,
        "batch_id": None,
        "analysis_level": None,
        "reconciliation_status": None,
        "reconciliation_scope": None,
        "reconciliation_window_start": None,
        "reconciliation_window_end": None,
        "reconciled_order_observations": 0,
        "completeness_score": None,
        "order_observations": 0,
        "fill_observations": 0,
        "aggregate_only_filled_orders": 0,
        "window_start": None,
        "window_end": None,
        "recorded_at": None,
        "legacy_journal_written": False,
    }


def test_legacy_import_rejects_header_only_export_without_writing_db():
    c = _client()
    db_path = Path(os.environ["DATABASE_PATH"])
    resp = c.post(
        "/api/v1/journal/import",
        files={
            "file": (
                "history.csv",
                _current_moomoo_csv(include_order=False),
                "text/csv",
            )
        },
    )

    assert resp.status_code == 410
    assert not db_path.exists()


def test_import_rejects_oversized_upload():
    c = _client()
    huge = b"x" * (51 * 1024 * 1024)
    resp = c.post(
        "/api/v1/journal/import",
        files={"file": ("big.csv", huge, "text/csv")},
    )
    assert resp.status_code == 413


def test_import_rejects_empty_upload():
    c = _client()
    resp = c.post(
        "/api/v1/journal/import",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert resp.status_code == 400


def test_legacy_import_does_not_persist_path_traversal_filename():
    """A legacy upload must be rejected before its filename reaches storage."""
    c = _client()
    db_path = Path(os.environ["DATABASE_PATH"])
    with open(FIXTURE, "rb") as fh:
        content = fh.read()
    resp = c.post(
        "/api/v1/journal/import",
        files={"file": ("../../etc/passwd.csv", content, "text/csv")},
    )
    assert resp.status_code == 410
    assert not db_path.exists()


def test_v2_position_episodes_returns_explicit_not_built_state(monkeypatch):
    from api.v1.endpoints import journal

    monkeypatch.setattr(journal, "get_latest_episode_summary", lambda _key: None)
    c = _client()
    resp = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={"page": 2, "per_page": 25},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "data_state": "not_built",
        "build": None,
        "reconciliation": None,
        "summary": None,
        "total": 0,
        "page": 2,
        "per_page": 25,
        "review_queue": {
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "total": 0,
        },
        "items": [],
    }


def test_v2_position_episodes_filters_and_preserves_decimal_strings(monkeypatch):
    from api.v1.endpoints import journal

    captured = {}
    summary = _episode_summary()
    item = _episode_item()

    def _page(account_key, **kwargs):
        captured["account_key"] = account_key
        captured.update(kwargs)
        return SimpleNamespace(
            build_id=7,
            items=(item,),
            total=1,
            page=3,
            per_page=10,
        )

    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: summary,
    )
    monkeypatch.setattr(journal, "get_latest_position_episode_page", _page)
    monkeypatch.setattr(
        journal,
        "get_latest_data_health",
        lambda _key: _episode_health(),
    )
    c = _client()
    resp = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={
            "underlying": "example",
            "lifecycle_status": "closed",
            "completeness_status": "partial",
            "case_focus": "top_profit",
            "page": 3,
            "per_page": 10,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert captured == {
        "account_key": "default_moomoo_us",
        "underlying": "example",
        "lifecycle_status": "closed",
        "completeness_status": "partial",
        "case_focus": "top_profit",
        "page": 3,
        "per_page": 10,
    }
    assert body["data_state"] == "ready"
    assert body["build"]["opening_boundary_policy"] == (
        "assumed_flat_unverified"
    )
    assert body["build"]["assumed_flat_unverified"] is True
    assert body["build"]["source_window_start"] == (
        "2026-03-04T00:00:00Z"
    )
    assert body["build"]["source_cutoff_at"] == (
        "2026-07-19T00:00:00Z"
    )
    assert body["reconciliation"]["scope"] == "partial_window"
    assert body["reconciliation"]["partial_window"] is True
    assert body["reconciliation"]["matched_order_count"] == 1089
    assert body["reconciliation"]["total_order_count"] == 3542
    headline = body["summary"]["headline_pnl"]
    assert headline["eligible_closed_count"] == 0
    assert headline["realized_pnl_gross"] is None
    assert headline["total_fee"] is None
    assert headline["realized_pnl_net"] is None
    conditional = body["summary"]["conditional_pnl"]
    assert conditional == {
        "basis": "closed_known_cash_flows_under_opening_boundary_policy",
        "opening_boundary_policy": "assumed_flat_unverified",
        "count": 1,
        "realized_pnl_gross": "125.0000000000",
        "total_fee": "1.2500000000",
        "realized_pnl_net": "123.7500000000",
        "included_in_headline": False,
    }
    assert body["items"][0]["realized_pnl_net"] == "123.7500000000"
    assert body["items"][0]["instrument"]["strike"] == "200.0000000000"
    assert body["items"][0]["quality"]["pnl_summary_eligible"] is False
    assert "assumed_flat_unverified" in body["items"][0]["quality"][
        "pnl_exclusion_reasons"
    ]


def test_v2_position_episode_filter_validation_is_server_side():
    c = _client()
    invalid_status = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={"lifecycle_status": "all"},
    )
    invalid_page_size = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={"per_page": 201},
    )
    invalid_case_focus = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={"case_focus": "best_trade"},
    )

    assert invalid_status.status_code == 422
    assert invalid_page_size.status_code == 422
    assert invalid_case_focus.status_code == 422


def test_v2_position_episode_case_focus_openapi_contract():
    schema = _client().get("/openapi.json").json()
    operation = schema["paths"][
        "/api/v1/journal/v2/position-episodes"
    ]["get"]
    parameter = next(
        item for item in operation["parameters"] if item["name"] == "case_focus"
    )

    assert parameter["required"] is False
    assert parameter["schema"]["anyOf"][0]["enum"] == [
        "top_profit",
        "top_loss",
        "largest_fee",
        "longest_hold",
        "weakest_evidence",
    ]


def test_v2_position_episode_headline_uses_only_verified_complete_closed(
    monkeypatch,
):
    from api.v1.endpoints import journal

    summary = _episode_summary(
        status="succeeded",
        opening_boundary_policy="complete_snapshot",
        partial_reasons=(),
        position_episode_count=1,
        open_episode_count=0,
        closed_episode_count=1,
        boundary_unverified_episode_count=0,
        incomplete_episode_count=0,
        aggregate_only_episode_count=0,
        headline_episode_count=1,
        headline_realized_pnl_gross=Decimal("125.0000000000"),
        headline_total_fee=Decimal("1.2500000000"),
        headline_realized_pnl_net=Decimal("123.7500000000"),
        headline_excluded_episode_count=0,
        headline_exclusion_counts={
            "boundary_unverified": 0,
            "open": 0,
            "left_censored": 0,
            "incomplete": 0,
            "pnl_unavailable": 0,
        },
    )
    item = _episode_item(
        opening_boundary_policy="complete_snapshot",
        left_boundary_verified=True,
        completeness_score=Decimal("1.0000"),
        completeness_status="exact",
        construction_basis="fills",
    )
    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: summary,
    )
    monkeypatch.setattr(
        journal,
        "get_latest_position_episode_page",
        lambda *_args, **_kwargs: SimpleNamespace(
            build_id=7,
            items=(item,),
            total=1,
            page=1,
            per_page=50,
        ),
    )
    monkeypatch.setattr(
        journal,
        "get_latest_data_health",
        lambda _key: _episode_health(),
    )
    c = _client()
    resp = c.get("/api/v1/journal/v2/position-episodes")

    assert resp.status_code == 200
    body = resp.json()
    headline = body["summary"]["headline_pnl"]
    assert headline["eligible_closed_count"] == 1
    assert headline["realized_pnl_gross"] == "125.0000000000"
    assert headline["total_fee"] == "1.2500000000"
    assert headline["realized_pnl_net"] == "123.7500000000"
    assert body["items"][0]["quality"]["pnl_summary_eligible"] is True
    assert body["items"][0]["quality"]["pnl_exclusion_reasons"] == []


def test_v2_position_episode_detail_contains_real_evidence_links(monkeypatch):
    from api.v1.endpoints import journal

    summary = _episode_summary()
    evidence = SimpleNamespace(
        evidence_id=61,
        evidence_key="fill-observation-61",
        evidence_kind="fill",
        event_role="open",
        allocation_sequence=0,
        evidence_time=datetime(2026, 6, 20, 14, 31, tzinfo=timezone.utc),
        allocated_quantity=Decimal("1.0000000000"),
        allocated_fee=Decimal("0.2500000000"),
        allocated_cash_flow=Decimal("-250.0000000000"),
        allocation_ratio=Decimal("1.0000000000"),
        broker_order_observation_id=None,
        broker_fill_observation_id=101,
        allocation_evidence={
            "timing_precision": "fill_time",
            "source_fee": Decimal("0.2500000000"),
        },
        provenance={"synthetic_fill": False},
    )
    detail = SimpleNamespace(
        episode=_episode_item(),
        matching_evidence={"method": "signed_position_zero_crossing"},
        evidence_summary={"allocation_count": 1},
        completeness={"left_boundary_verified": False},
        provenance={"builder_version": "1.1.0"},
        evidence=(evidence,),
    )
    captured = {}

    def _detail(**kwargs):
        captured.update(kwargs)
        return detail

    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: summary,
    )
    monkeypatch.setattr(
        journal,
        "get_latest_position_episode_detail",
        _detail,
    )
    monkeypatch.setattr(
        journal,
        "get_latest_data_health",
        lambda _key: _episode_health(),
    )
    c = _client()
    resp = c.get("/api/v1/journal/v2/position-episodes/41")

    assert resp.status_code == 200
    body = resp.json()
    assert captured == {
        "episode_id": 41,
        "account_key": "default_moomoo_us",
    }
    assert body["matching"]["method"] == "signed_position_zero_crossing"
    assert body["evidence"][0]["allocated_fee"] == "0.2500000000"
    assert body["evidence"][0]["broker_order_observation_id"] is None
    assert body["evidence"][0]["broker_fill_observation_id"] == 101
    assert body["evidence"][0]["allocation"]["source_fee"] == (
        "0.2500000000"
    )
    assert body["evidence"][0]["provenance"]["synthetic_fill"] is False


def test_v2_position_episode_detail_is_latest_build_scoped(monkeypatch):
    from api.v1.endpoints import journal

    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: _episode_summary(),
    )
    monkeypatch.setattr(
        journal,
        "get_latest_position_episode_detail",
        lambda **_kwargs: None,
    )
    c = _client()
    resp = c.get("/api/v1/journal/v2/position-episodes/999")

    assert resp.status_code == 404
    assert "latest build" in resp.json()["detail"]


def test_v2_episode_build_requires_explicit_assumed_flat_acceptance(monkeypatch):
    from api.v1.endpoints import journal

    monkeypatch.setattr(
        journal,
        "preview_latest_position_episodes",
        lambda *_args, **_kwargs: SimpleNamespace(
            opening_boundary_policy="assumed_flat_unverified"
        ),
    )

    def _must_not_append(*_args, **_kwargs):
        raise AssertionError("unconfirmed build must not be appended")

    monkeypatch.setattr(
        journal,
        "append_latest_position_episode_build",
        _must_not_append,
    )
    c = _client()
    resp = c.post("/api/v1/journal/v2/episode-builds")

    assert resp.status_code == 409
    assert "accept_assumed_flat=true" in resp.json()["detail"]


def test_v2_episode_build_accepts_assumption_and_reports_exclusions(monkeypatch):
    from api.v1.endpoints import journal

    summary = _episode_summary()
    captured = {}
    monkeypatch.setattr(
        journal,
        "preview_latest_position_episodes",
        lambda *_args, **_kwargs: SimpleNamespace(
            opening_boundary_policy="assumed_flat_unverified"
        ),
    )

    def _append(account_key, **kwargs):
        captured["account_key"] = account_key
        captured.update(kwargs)
        return SimpleNamespace(
            build_id=7,
            duplicate=False,
            position_episode_count=2,
            opening_boundary_policy="assumed_flat_unverified",
        )

    monkeypatch.setattr(
        journal,
        "append_latest_position_episode_build",
        _append,
    )
    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: summary,
    )
    monkeypatch.setattr(
        journal,
        "get_latest_data_health",
        lambda _key: _episode_health(),
    )
    c = _client()
    resp = c.post(
        "/api/v1/journal/v2/episode-builds",
        params={"accept_assumed_flat": "true"},
    )

    assert resp.status_code == 200
    assert captured == {
        "account_key": "default_moomoo_us",
        "accept_assumed_flat": True,
    }
    body = resp.json()
    assert body["duplicate"] is False
    assert body["build"]["assumed_flat_unverified"] is True
    assert body["reconciliation"]["partial_window"] is True
    assert body["summary"]["headline_pnl"]["excluded_episode_count"] == 2
    assert body["summary"]["headline_pnl"]["realized_pnl_net"] is None


def test_v2_episode_build_and_latest_read_round_trip_in_temp_db():
    """Exercise the API/repository seam without touching the formal ledger."""
    c = _client()
    imported = c.post(
        "/api/v1/journal/v2/imports",
        params={"allow_partial": "true"},
        files={
            "file": (
                "history.csv",
                _current_moomoo_csv(
                    include_order=True,
                    include_close=True,
                ),
                "text/csv",
            )
        },
    )
    assert imported.status_code == 200

    unconfirmed = c.post("/api/v1/journal/v2/episode-builds")
    assert unconfirmed.status_code == 409

    built = c.post(
        "/api/v1/journal/v2/episode-builds",
        params={"accept_assumed_flat": "true"},
    )
    assert built.status_code == 200, built.text
    built_body = built.json()
    assert built_body["build"]["assumed_flat_unverified"] is True
    assert built_body["summary"]["headline_pnl"][
        "eligible_closed_count"
    ] == 0
    assert built_body["summary"]["headline_pnl"][
        "realized_pnl_net"
    ] is None
    conditional = built_body["summary"]["conditional_pnl"]
    assert conditional["count"] == 1
    assert conditional["realized_pnl_gross"] == "50.0000000000"
    assert conditional["total_fee"] == "0.6004000000"
    assert conditional["realized_pnl_net"] == "49.3996000000"
    assert conditional["included_in_headline"] is False

    listed = c.get("/api/v1/journal/v2/position-episodes")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["data_state"] == "ready"
    assert body["total"] == 1
    assert body["items"][0]["realized_pnl_net"] == "49.3996000000"
    assert body["items"][0]["quality"]["assumed_flat_unverified"] is True

    episode_id = body["items"][0]["id"]
    detailed = c.get(
        f"/api/v1/journal/v2/position-episodes/{episode_id}"
    )
    assert detailed.status_code == 200, detailed.text
    evidence = detailed.json()["evidence"]
    assert len(evidence) == 2
    assert all(item["evidence_kind"] == "order" for item in evidence)
    assert all(item["broker_fill_observation_id"] is None for item in evidence)


def test_canonical_episode_build_preview_is_read_only_and_keeps_default(
    monkeypatch,
):
    from api.v1.endpoints import journal

    canonical_hash = "a" * 64
    build_key = "b" * 64
    captured = {}

    def _preview(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            canonical_set_id=19,
            canonical_set_sha256=canonical_hash,
            build_key=build_key,
            source_batch_ids=(2, 3),
            source_window_start=datetime(
                2026,
                3,
                4,
                tzinfo=timezone.utc,
            ),
            source_cutoff_at=datetime(
                2026,
                7,
                19,
                tzinfo=timezone.utc,
            ),
            source_event_count=5974,
            aggregate_order_event_count=574,
            detailed_fill_event_count=5400,
            episodes=tuple(range(1441)),
            open_episode_count=4,
            closed_episode_count=1437,
            source_known_fee_total=Decimal("151750.7500000000"),
            allocated_known_fee_total=Decimal("151750.7500000000"),
            fee_conserved=True,
            unresolved_evidence_count=0,
            opening_boundary_policy="assumed_flat_unverified",
            partial_reasons=("opening_boundary_unverified",),
        )

    monkeypatch.setattr(journal, "preview_canonical_position_episodes", _preview)
    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: _episode_summary(position_episode_count=1440),
    )
    c = _client()
    response = c.get(
        "/api/v1/journal/v2/episode-builds/canonical/preview",
        params={"canonical_set_id": 19},
    )

    assert response.status_code == 200, response.text
    assert captured == {
        "canonical_set_id": 19,
        "account_key": "default_moomoo_us",
        "assume_flat_if_missing": True,
    }
    body = response.json()
    assert body["canonical_set_id"] == 19
    assert body["canonical_set_sha256"] == canonical_hash
    assert body["build_key"] == build_key
    assert body["source_batch_ids"] == [2, 3]
    assert body["source_event_count"] == 5974
    assert body["planned_position_episode_count"] == 1441
    assert body["planned_open_episode_count"] == 4
    assert body["planned_closed_episode_count"] == 1437
    assert body["source_known_fee_total"] == "151750.7500000000"
    assert body["allocated_known_fee_total"] == "151750.7500000000"
    assert body["fee_conserved"] is True
    assert body["default_build_id"] == 7
    assert body["default_position_episode_count"] == 1440
    assert body["episode_count_delta"] == 1
    assert body["default_will_change"] is False
    assert body["requires_assumed_flat_acceptance"] is True
    assert body["confirm_allowed"] is True
    assert "v2" not in json.dumps(body).lower()


def test_canonical_episode_build_preview_maps_missing_and_invalid(monkeypatch):
    from api.v1.endpoints import journal
    from src.journal.ledger.episode_repository import EpisodeRepositoryError

    monkeypatch.setattr(
        journal,
        "preview_canonical_position_episodes",
        lambda **_kwargs: None,
    )
    c = _client()
    missing = c.get(
        "/api/v1/journal/v2/episode-builds/canonical/preview",
        params={"canonical_set_id": 999},
    )
    assert missing.status_code == 404

    def _invalid(**_kwargs):
        raise EpisodeRepositoryError("canonical set is not analysis-ready")

    monkeypatch.setattr(
        journal,
        "preview_canonical_position_episodes",
        _invalid,
    )
    invalid = c.get(
        "/api/v1/journal/v2/episode-builds/canonical/preview",
        params={"canonical_set_id": 19},
    )
    assert invalid.status_code == 409


def test_canonical_episode_build_confirm_reads_explicit_build_and_keeps_default(
    monkeypatch,
):
    from api.v1.endpoints import journal

    canonical_hash = "a" * 64
    build_key = "b" * 64
    captured = {}

    def _append(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            build_id=12,
            duplicate=False,
            position_episode_count=1441,
            opening_boundary_policy="assumed_flat_unverified",
        )

    summary = _episode_summary(
        build_id=12,
        build_key=build_key,
        source_batch_id=2,
        source_batch_ids=(2, 3),
        source_kind="canonical_set",
        canonical_set_id=19,
        canonical_set_sha256=canonical_hash,
        position_episode_count=1441,
        reconciliation_window_start=datetime(
            2026,
            6,
            20,
            tzinfo=timezone.utc,
        ),
        reconciliation_window_end=datetime(
            2026,
            7,
            19,
            tzinfo=timezone.utc,
        ),
        reconciled_order_count=1089,
        total_order_count=3542,
    )
    explicit_reads = []

    def _read(build_id, account_key):
        explicit_reads.append((build_id, account_key))
        return summary

    monkeypatch.setattr(
        journal,
        "append_canonical_position_episode_build",
        _append,
    )
    monkeypatch.setattr(journal, "get_episode_summary", _read)
    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: pytest.fail("canonical confirm must not read the default build"),
    )
    monkeypatch.setattr(
        journal,
        "get_latest_data_health",
        lambda _key: pytest.fail("frozen reconciliation must be used"),
    )
    c = _client()
    response = c.post(
        "/api/v1/journal/v2/episode-builds/canonical",
        json={
            "canonical_set_id": 19,
            "canonical_set_sha256": canonical_hash,
            "build_key": build_key,
            "accept_assumed_flat": True,
        },
    )

    assert response.status_code == 200, response.text
    assert captured == {
        "canonical_set_id": 19,
        "expected_canonical_set_sha256": canonical_hash,
        "expected_build_key": build_key,
        "account_key": "default_moomoo_us",
        "accept_assumed_flat": True,
        "accept_group_fee_scope": False,
    }
    assert explicit_reads == [(12, "default_moomoo_us")]
    body = response.json()
    assert body["build"]["id"] == 12
    assert body["build"]["source_batch_ids"] == [2, 3]
    assert body["build"]["source_kind"] == "canonical_set"
    assert body["build"]["canonical_set_id"] == 19
    assert body["build"]["canonical_set_sha256"] == canonical_hash
    assert body["reconciliation"]["matched_order_count"] == 1089
    assert body["reconciliation"]["total_order_count"] == 3542
    assert "default position review remains unchanged" in body["message"]
    assert "v2" not in body["message"].lower()


def test_canonical_episode_build_confirm_maps_missing_and_stale(monkeypatch):
    from api.v1.endpoints import journal
    from src.journal.ledger.episode_repository import EpisodeRepositoryError

    canonical_hash = "a" * 64
    build_key = "b" * 64
    request = {
        "canonical_set_id": 19,
        "canonical_set_sha256": canonical_hash,
        "build_key": build_key,
        "accept_assumed_flat": True,
    }

    def _missing(**_kwargs):
        raise EpisodeRepositoryError("canonical evidence set does not exist")

    monkeypatch.setattr(
        journal,
        "append_canonical_position_episode_build",
        _missing,
    )
    c = _client()
    missing = c.post(
        "/api/v1/journal/v2/episode-builds/canonical",
        json=request,
    )
    assert missing.status_code == 404

    def _stale(**_kwargs):
        raise EpisodeRepositoryError(
            "canonical episode plan changed after the confirmed preview"
        )

    monkeypatch.setattr(
        journal,
        "append_canonical_position_episode_build",
        _stale,
    )
    stale = c.post(
        "/api/v1/journal/v2/episode-builds/canonical",
        json=request,
    )
    assert stale.status_code == 409


def test_position_episode_list_supports_explicit_canonical_build(monkeypatch):
    from api.v1.endpoints import journal

    canonical_hash = "a" * 64
    summary = _episode_summary(
        build_id=12,
        source_batch_ids=(2, 3),
        source_kind="canonical_set",
        canonical_set_id=19,
        canonical_set_sha256=canonical_hash,
        reconciliation_window_start=None,
        reconciliation_window_end=None,
        reconciled_order_count=1089,
        total_order_count=3542,
    )
    item = _episode_item(build_id=12)
    captured = {}

    def _page(build_id, account_key, **kwargs):
        captured["build_id"] = build_id
        captured["account_key"] = account_key
        captured.update(kwargs)
        return SimpleNamespace(
            build_id=12,
            items=(item,),
            total=1,
            page=1,
            per_page=25,
        )

    monkeypatch.setattr(
        journal,
        "get_episode_summary",
        lambda build_id, account_key: (
            summary
            if (build_id, account_key) == (12, "default_moomoo_us")
            else None
        ),
    )
    monkeypatch.setattr(journal, "get_position_episode_page", _page)
    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: pytest.fail("explicit read must not select the default build"),
    )
    monkeypatch.setattr(
        journal,
        "get_latest_position_episode_page",
        lambda *_args, **_kwargs: pytest.fail(
            "explicit read must not select the default page"
        ),
    )
    monkeypatch.setattr(
        journal,
        "get_latest_data_health",
        lambda _key: pytest.fail("frozen reconciliation must be used"),
    )
    c = _client()
    response = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={"build_id": 12, "underlying": "example", "per_page": 25},
    )

    assert response.status_code == 200, response.text
    assert captured == {
        "build_id": 12,
        "account_key": "default_moomoo_us",
        "underlying": "example",
        "lifecycle_status": None,
        "completeness_status": None,
        "case_focus": None,
        "page": 1,
        "per_page": 25,
    }
    body = response.json()
    assert body["build"]["id"] == 12
    assert body["build"]["source_kind"] == "canonical_set"
    assert body["build"]["canonical_set_id"] == 19
    assert body["items"][0]["episode_build_id"] == 12

    missing = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={"build_id": 999},
    )
    assert missing.status_code == 404


def test_position_episode_detail_supports_explicit_build(monkeypatch):
    from api.v1.endpoints import journal

    summary = _episode_summary(
        build_id=12,
        source_batch_ids=(2, 3),
        source_kind="canonical_set",
        canonical_set_id=19,
        canonical_set_sha256="a" * 64,
        reconciliation_window_start=None,
        reconciliation_window_end=None,
        reconciled_order_count=0,
        total_order_count=0,
    )
    detail = SimpleNamespace(
        episode=_episode_item(build_id=12),
        matching_evidence={},
        evidence_summary={},
        completeness={},
        provenance={},
        evidence=(),
    )
    captured = {}

    def _detail(**kwargs):
        captured.update(kwargs)
        return detail

    monkeypatch.setattr(
        journal,
        "get_episode_summary",
        lambda build_id, _account_key: summary if build_id == 12 else None,
    )
    monkeypatch.setattr(journal, "get_position_episode_detail", _detail)
    monkeypatch.setattr(
        journal,
        "get_latest_episode_summary",
        lambda _key: pytest.fail("explicit detail must not read the default build"),
    )
    c = _client()
    response = c.get(
        "/api/v1/journal/v2/position-episodes/41",
        params={"build_id": 12},
    )

    assert response.status_code == 200, response.text
    assert captured == {
        "episode_id": 41,
        "build_id": 12,
        "account_key": "default_moomoo_us",
    }
    assert response.json()["build"]["id"] == 12


def test_canonical_episode_api_round_trip_does_not_activate_default_build():
    c = _client()
    imported = c.post(
        "/api/v1/journal/v2/imports",
        files={"file": ("history.csv", _matching_openapi_csv(), "text/csv")},
    )
    assert imported.status_code == 200, imported.text

    export = json.dumps(_openapi_export_payload())
    evidence_plan = c.post(
        "/api/v1/journal/v2/openapi-imports/plan",
        files={"file": ("readonly.json", export, "application/json")},
    )
    assert evidence_plan.status_code == 200, evidence_plan.text
    evidence_plan_body = evidence_plan.json()
    confirmed_evidence = c.post(
        "/api/v1/journal/v2/openapi-imports/confirm",
        params={"preview_key": evidence_plan_body["preview_key"]},
        files={"file": ("readonly.json", export, "application/json")},
    )
    assert confirmed_evidence.status_code == 200, confirmed_evidence.text
    canonical_set_id = confirmed_evidence.json()["canonical_set_id"]

    build_plan = c.get(
        "/api/v1/journal/v2/episode-builds/canonical/preview",
        params={"canonical_set_id": canonical_set_id},
    )
    assert build_plan.status_code == 200, build_plan.text
    plan = build_plan.json()
    assert plan["planned_position_episode_count"] == 1
    assert plan["source_event_count"] == 1
    assert plan["detailed_fill_event_count"] == 1
    assert plan["aggregate_order_event_count"] == 0
    assert plan["fee_conserved"] is True
    assert plan["default_build_id"] is None
    assert plan["default_will_change"] is False

    built = c.post(
        "/api/v1/journal/v2/episode-builds/canonical",
        json={
            "canonical_set_id": plan["canonical_set_id"],
            "canonical_set_sha256": plan["canonical_set_sha256"],
            "build_key": plan["build_key"],
            "accept_assumed_flat": True,
        },
    )
    assert built.status_code == 200, built.text
    built_body = built.json()
    build_id = built_body["build"]["id"]
    assert built_body["build"]["source_kind"] == "canonical_set"
    assert built_body["build"]["canonical_set_id"] == canonical_set_id

    default_page = c.get("/api/v1/journal/v2/position-episodes")
    assert default_page.status_code == 200
    assert default_page.json()["data_state"] == "not_built"

    explicit_page = c.get(
        "/api/v1/journal/v2/position-episodes",
        params={"build_id": build_id},
    )
    assert explicit_page.status_code == 200, explicit_page.text
    explicit_body = explicit_page.json()
    assert explicit_body["data_state"] == "ready"
    assert explicit_body["build"]["id"] == build_id
    assert explicit_body["total"] == 1

    episode_id = explicit_body["items"][0]["id"]
    explicit_detail = c.get(
        f"/api/v1/journal/v2/position-episodes/{episode_id}",
        params={"build_id": build_id},
    )
    assert explicit_detail.status_code == 200, explicit_detail.text
    assert explicit_detail.json()["build"]["id"] == build_id

    activation_before = c.get(
        "/api/v1/journal/v2/episode-builds/activation"
    )
    assert activation_before.status_code == 200, activation_before.text
    assert activation_before.json()["selection_source"] == "none"
    assert activation_before.json()["current_build_id"] is None

    activation_request = {
        "expected_build_key": built_body["build"]["build_key"],
        "expected_current_activation_id": None,
        "expected_current_build_id": None,
        "accept_assumed_flat": False,
    }
    missing_ack = c.post(
        f"/api/v1/journal/v2/episode-builds/{build_id}/activate",
        json=activation_request,
    )
    assert missing_ack.status_code == 409, missing_ack.text
    assert "explicit acceptance" in missing_ack.json()["detail"]

    activation_request["accept_assumed_flat"] = True
    activated = c.post(
        f"/api/v1/journal/v2/episode-builds/{build_id}/activate",
        json=activation_request,
    )
    assert activated.status_code == 200, activated.text
    activated_body = activated.json()
    assert activated_body["duplicate"] is False
    assert activated_body["trading_action_performed"] is False
    assert activated_body["state"]["selection_source"] == "activation"
    assert activated_body["state"]["current_build_id"] == build_id

    activated_default_page = c.get("/api/v1/journal/v2/position-episodes")
    assert activated_default_page.status_code == 200
    assert activated_default_page.json()["data_state"] == "ready"
    assert activated_default_page.json()["build"]["id"] == build_id

    repeated_activation = c.post(
        f"/api/v1/journal/v2/episode-builds/{build_id}/activate",
        json=activation_request,
    )
    assert repeated_activation.status_code == 200, repeated_activation.text
    assert repeated_activation.json()["duplicate"] is True
    assert repeated_activation.json()["activation_id"] == activated_body[
        "activation_id"
    ]


def _activation_state_for_build(build_id: int):
    from src.journal.ledger.activation_repository import (
        EpisodeBuildActivationState,
    )

    return EpisodeBuildActivationState(
        account_key="default_moomoo_us",
        selection_source="activation",
        current_activation_id=3,
        current_activation_sequence=1,
        current_build_id=build_id,
        current_build_key="a" * 64,
        canonical_set_id=9,
        canonical_set_sha256="b" * 64,
        previous_activation_id=None,
        previous_build_id=None,
        activated_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )


def test_activation_endpoint_forwards_left_censored_acceptance(monkeypatch):
    from api.v1.endpoints import journal
    from src.journal.ledger.activation_repository import (
        SNAPSHOT_FENCE_SOURCE_KIND,
        EpisodeBuildActivationResult,
    )

    captured = {}

    def _activate(build_id, expected_build_key, **kwargs):
        captured["build_id"] = build_id
        captured["expected_build_key"] = expected_build_key
        captured.update(kwargs)
        return EpisodeBuildActivationResult(
            activation_id=3,
            activation_key="c" * 64,
            duplicate=False,
            state=_activation_state_for_build(21),
            target_source_kind=SNAPSHOT_FENCE_SOURCE_KIND,
        )

    monkeypatch.setattr(journal, "activate_episode_build", _activate)
    c = _client()
    response = c.post(
        "/api/v1/journal/v2/episode-builds/21/activate",
        json={
            "expected_build_key": "a" * 64,
            "expected_current_activation_id": None,
            "expected_current_build_id": None,
            "accept_left_censored_openings": True,
        },
    )

    assert response.status_code == 200, response.text
    assert captured["build_id"] == 21
    assert captured["accept_left_censored_openings"] is True
    assert captured["accept_assumed_flat"] is False
    assert captured["accept_group_fee_scope"] is False
    body = response.json()
    assert body["message"].startswith(
        "snapshot-fence future Episode build 21 activated"
    )
    assert body["trading_action_performed"] is False
    assert body["state"]["selection_source"] == "activation"


def test_activation_endpoint_defaults_and_maps_left_censored_409(monkeypatch):
    from api.v1.endpoints import journal
    from src.journal.ledger.activation_repository import (
        EpisodeBuildActivationError,
    )

    captured = {}

    def _activate(build_id, expected_build_key, **kwargs):
        captured["build_id"] = build_id
        captured.update(kwargs)
        raise EpisodeBuildActivationError(
            "left-censored openings have no broker cost basis; "
            "explicit acceptance is required"
        )

    monkeypatch.setattr(journal, "activate_episode_build", _activate)
    c = _client()
    # A request that omits the additive flag keeps the old wire shape and
    # defaults to no acceptance server-side.
    response = c.post(
        "/api/v1/journal/v2/episode-builds/21/activate",
        json={
            "expected_build_key": "a" * 64,
            "expected_current_activation_id": None,
            "expected_current_build_id": None,
        },
    )

    assert response.status_code == 409, response.text
    assert captured["accept_left_censored_openings"] is False
    assert "explicit acceptance" in response.json()["detail"]

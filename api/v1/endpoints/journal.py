# -*- coding: utf-8 -*-
"""Journal REST endpoints for the dsa-web frontend.

Stage 7 exposes:
    GET  /api/v1/journal/reality-test
    GET  /api/v1/journal/trades
    GET  /api/v1/journal/trades/{trade_id}
    PATCH /api/v1/journal/trades/{trade_id}
    GET  /api/v1/journal/health-check
    GET  /api/v1/journal/stats
    POST /api/v1/journal/import           (multipart CSV upload)
"""
from __future__ import annotations

import logging
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import and_, func, select

from api.v1.schemas.journal import (
    HealthCheckItem,
    ImportResponse,
    JournalStatsResponse,
    MonthlyReviewGenerateRequest,
    MonthlyReviewItem,
    MonthlyReviewListResponse,
    RealityTestResponse,
    TradeItem,
    TradeListResponse,
    TradeUpdateRequest,
)
from src.journal.analytics import (
    dte_bucket_win_rates,
    dte_distribution,
    reality_test,
)
from src.journal.brokers.moomoo_us import parse as parse_moomoo
from src.journal.matcher import match_legs_fifo
from src.journal.models import (
    JournalHealthCheck,
    JournalMonthlyReview,
    JournalOrder,
    JournalTrade,
)
from src.journal.storage import (
    DEFAULT_PORTFOLIO_LABEL,
    init_journal_schema,
    insert_events_from_orders,
    query_events_for_matching,
    record_import,
    replace_trades,
)
from src.storage import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

_PARSERS = {"moomoo_us": parse_moomoo}


# --- helpers -----------------------------------------------------------------


def _trade_row_to_dict(row: JournalTrade) -> dict:
    return {
        "id": row.id,
        "portfolio_label": row.portfolio_label,
        "is_option": bool(row.is_option),
        "raw_symbol": row.raw_symbol,
        "underlying": row.underlying,
        "expiry": row.expiry,
        "strike": row.strike,
        "right": row.right,
        "direction": row.direction,
        "status": row.status,
        "quantity": int(row.quantity),
        "avg_entry_price": float(row.avg_entry_price),
        "avg_exit_price": row.avg_exit_price,
        "entry_time": row.entry_time,
        "exit_time": row.exit_time,
        "hold_seconds": row.hold_seconds,
        "dte_at_entry": row.dte_at_entry,
        "dte_bucket": row.dte_bucket,
        "pnl_gross": row.pnl_gross,
        "pnl_net": row.pnl_net,
        "pnl_pct": row.pnl_pct,
        "total_fee": row.total_fee,
        "trade_style": row.trade_style,
        "regime_score_at_entry": row.regime_score_at_entry,
        "was_fake_breakout": row.was_fake_breakout,
        "user_notes": row.user_notes,
        "emotional_state": row.emotional_state,
        "strategy_tag_ai": row.strategy_tag_ai,
    }


def _load_trades(portfolio: str, since: Optional[date] = None) -> list[dict]:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        stmt = select(JournalTrade).where(JournalTrade.portfolio_label == portfolio)
        rows = session.execute(stmt).scalars().all()
        out = []
        for r in rows:
            if since and r.entry_time and r.entry_time.date() < since:
                continue
            out.append(_trade_row_to_dict(r))
        return out


# --- endpoints ---------------------------------------------------------------


@router.get("/reality-test", response_model=RealityTestResponse)
def get_reality_test(
    top_n: int = Query(5, ge=0, le=100),
    since: Optional[date] = Query(None),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> RealityTestResponse:
    trades = _load_trades(portfolio, since=since)
    rt = reality_test(trades, top_n=top_n)
    return RealityTestResponse(**rt)


@router.get("/trades", response_model=TradeListResponse)
def list_trades(
    symbol: Optional[str] = Query(None),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    status: Optional[str] = Query(None),
    style: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> TradeListResponse:
    init_journal_schema()
    db = get_db()
    filters = [JournalTrade.portfolio_label == portfolio]
    if symbol:
        filters.append(JournalTrade.underlying == symbol.upper())
    if start:
        filters.append(JournalTrade.entry_time >= datetime.combine(start, datetime.min.time()))
    if end:
        filters.append(JournalTrade.entry_time <= datetime.combine(end, datetime.max.time()))
    if status:
        filters.append(JournalTrade.status == status)
    if style:
        filters.append(JournalTrade.trade_style == style)

    with db.session_scope() as session:
        total = session.execute(
            select(func.count(JournalTrade.id)).where(and_(*filters))
        ).scalar_one()
        stmt = (
            select(JournalTrade)
            .where(and_(*filters))
            .order_by(JournalTrade.entry_time.desc().nulls_last(), JournalTrade.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        rows = session.execute(stmt).scalars().all()
        items = [TradeItem(**_trade_row_to_dict(r)) for r in rows]

    return TradeListResponse(total=int(total), page=page, per_page=per_page, items=items)


@router.get("/trades/{trade_id}", response_model=TradeItem)
def get_trade(trade_id: int) -> TradeItem:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.get(JournalTrade, trade_id)
        if row is None:
            raise HTTPException(status_code=404, detail="trade not found")
        return TradeItem(**_trade_row_to_dict(row))


@router.patch("/trades/{trade_id}", response_model=TradeItem)
def update_trade(trade_id: int, payload: TradeUpdateRequest) -> TradeItem:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        row = session.get(JournalTrade, trade_id)
        if row is None:
            raise HTTPException(status_code=404, detail="trade not found")
        if payload.user_notes is not None:
            row.user_notes = payload.user_notes
        if payload.emotional_state is not None:
            row.emotional_state = payload.emotional_state
        if payload.trade_style is not None:
            row.trade_style = payload.trade_style
        session.flush()
        return TradeItem(**_trade_row_to_dict(row))


@router.get("/health-check", response_model=Optional[HealthCheckItem])
def get_health_check(
    date_: date = Query(..., alias="date"),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
):
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        row = (
            session.execute(
                select(JournalHealthCheck).where(
                    JournalHealthCheck.portfolio_label == portfolio,
                    JournalHealthCheck.check_date == date_,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        import json as _json

        warnings = []
        if row.warnings_json:
            try:
                warnings = _json.loads(row.warnings_json)
            except Exception:  # noqa: BLE001
                warnings = []
        return HealthCheckItem(
            check_date=row.check_date,
            total_orders=int(row.total_orders or 0),
            orders_0dte=int(row.orders_0dte or 0),
            orders_1_3dte=int(row.orders_1_3dte or 0),
            orders_opening_hour=int(row.orders_opening_hour or 0),
            top_underlying=row.top_underlying,
            top_underlying_pct=row.top_underlying_pct,
            warnings_json=warnings,
            pnl_estimate=row.pnl_estimate,
            regime_score=row.regime_score,
        )


@router.get("/stats", response_model=JournalStatsResponse)
def get_stats(
    days: int = Query(90, ge=1, le=3650),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> JournalStatsResponse:
    since = date.today() - timedelta(days=days)
    trades = _load_trades(portfolio, since=since)
    dist = dte_distribution(trades)
    by_bucket = dte_bucket_win_rates(trades)
    rt = reality_test(trades, top_n=5)
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None]
    win_rate = None
    if closed:
        wins = sum(1 for t in closed if t["pnl_net"] > 0)
        win_rate = wins / len(closed)
    return JournalStatsResponse(
        window_days=days,
        closed_trade_count=len(closed),
        total_pnl_net=rt["total_pnl_net"],
        win_rate=win_rate,
        dte_distribution=dist,
        win_rate_by_bucket=by_bucket,
        reality_test=RealityTestResponse(**rt),
    )


@router.post("/import", response_model=ImportResponse)
async def import_csv(
    file: UploadFile = File(...),
    broker: str = Query("moomoo_us"),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> ImportResponse:
    if broker not in _PARSERS:
        raise HTTPException(status_code=400, detail=f"unknown broker: {broker}")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    # Guard against runaway uploads. Phase 0 CSVs are < 1 MB; 50 MB cap
    # protects the process from OOM on malicious input.
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"file too large ({len(content)} bytes, max 52428800)",
        )

    init_journal_schema()
    orders = _PARSERS[broker](content)
    # Sanitize filename to prevent path-traversal in the audit source_path.
    safe_name = Path(file.filename or "upload.csv").name
    src_path = Path(tempfile.gettempdir()) / safe_name
    import_id = record_import(
        source_path=str(src_path),
        content=content,
        broker=broker,
        rows_total=len(orders),
        portfolio_label=portfolio,
    )
    if import_id is None:
        return ImportResponse(
            inserted=0,
            skipped=0,
            trades_rebuilt=0,
            message="CSV already imported (sha256 match); nothing to do.",
        )

    inserted, skipped = insert_events_from_orders(
        import_id, orders, portfolio_label=portfolio
    )
    events = query_events_for_matching(portfolio_label=portfolio)
    trades = match_legs_fifo(events)
    replaced = replace_trades(trades, portfolio_label=portfolio)
    return ImportResponse(
        inserted=inserted,
        skipped=skipped,
        trades_rebuilt=replaced,
        message=(
            f"{inserted} new orders imported, {skipped} dupes skipped; "
            f"{replaced} trades rebuilt."
        ),
    )


# --- monthly review ----------------------------------------------------------


@router.get("/reviews", response_model=MonthlyReviewListResponse)
def list_reviews(portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL)) -> MonthlyReviewListResponse:
    init_journal_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = (
            session.execute(
                select(JournalMonthlyReview)
                .where(JournalMonthlyReview.portfolio_label == portfolio)
                .order_by(JournalMonthlyReview.year_month.desc())
            )
            .scalars()
            .all()
        )
        items = [
            MonthlyReviewItem(
                year_month=r.year_month,
                current_phase=int(r.current_phase or 0),
                review_markdown=r.review_markdown,
                generated_at=r.generated_at,
            )
            for r in rows
        ]
        return MonthlyReviewListResponse(count=len(items), items=items)


@router.get("/reviews/{year}/{month}", response_model=Optional[MonthlyReviewItem])
def get_review(year: int, month: int, portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL)):
    init_journal_schema()
    db = get_db()
    ym = f"{year:04d}-{month:02d}"
    with db.session_scope() as session:
        row = (
            session.execute(
                select(JournalMonthlyReview).where(
                    JournalMonthlyReview.portfolio_label == portfolio,
                    JournalMonthlyReview.year_month == ym,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        return MonthlyReviewItem(
            year_month=row.year_month,
            current_phase=int(row.current_phase or 0),
            review_markdown=row.review_markdown,
            generated_at=row.generated_at,
        )


@router.post("/reviews/{year}/{month}/generate", response_model=MonthlyReviewItem)
def generate_review_endpoint(
    year: int,
    month: int,
    payload: MonthlyReviewGenerateRequest,
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
):
    from src.journal.monthly_review import run as run_review

    ym = f"{year:04d}-{month:02d}"
    try:
        run_review(ym, portfolio=portfolio, dry_run=payload.dry_run)
    except Exception as exc:  # noqa: BLE001
        logger.exception("monthly review generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result = get_review(year, month, portfolio)  # type: ignore[arg-type]
    if result is None:
        raise HTTPException(status_code=500, detail="review missing after generation")
    return result

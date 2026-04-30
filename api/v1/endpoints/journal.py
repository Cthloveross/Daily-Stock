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
    JournalQaRequest,
    JournalQaResponse,
    JournalStatsByStyleResponse,
    JournalStatsResponse,
    MonthlyReviewGenerateRequest,
    MonthlyReviewItem,
    MonthlyReviewListResponse,
    MoomooSyncRequest,
    MoomooSyncResponse,
    RealityTestResponse,
    TradeItem,
    TradeListResponse,
    TradeUpdateRequest,
)
from src.journal.analytics import (
    dte_bucket_win_rates,
    dte_distribution,
    reality_test,
    stats_by_style,
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


# --- stats-by-style + QA -----------------------------------------------------


_DTE_BUCKET_ORDER = ["0DTE", "1-3DTE", "4-7DTE", "8-30DTE", "30+DTE", "equity", "unknown"]


def _filter_trades_by_window(
    trades: list[dict],
    start_date: Optional[date],
    end_date: Optional[date],
) -> list[dict]:
    out = []
    for t in trades:
        ref = t.get("exit_time") or t.get("entry_time")
        if ref is None:
            continue
        ref_date = ref.date() if isinstance(ref, datetime) else ref
        if start_date and ref_date < start_date:
            continue
        if end_date and ref_date > end_date:
            continue
        out.append(t)
    return out


def _compact_trade_for_api(t: dict) -> dict:
    """Minimal trade row for `worst_trades` / `best_trades` transport."""
    return {
        "id": t.get("id"),
        "underlying": t.get("underlying"),
        "direction": t.get("direction"),
        "is_option": t.get("is_option"),
        "dte_bucket": t.get("dte_bucket"),
        "trade_style": t.get("trade_style"),
        "pnl_net": t.get("pnl_net"),
        "pnl_pct": t.get("pnl_pct"),
        "hold_seconds": t.get("hold_seconds"),
        "entry_time": t.get("entry_time").isoformat() if t.get("entry_time") else None,
        "exit_time": t.get("exit_time").isoformat() if t.get("exit_time") else None,
    }


@router.get("/stats-by-style", response_model=JournalStatsByStyleResponse)
def get_stats_by_style(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    top_n: int = Query(5, ge=1, le=20),
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> JournalStatsByStyleResponse:
    """P&L breakdown by trade_style + DTE bucket across an optional date window."""
    trades = _load_trades(portfolio)
    trades = _filter_trades_by_window(trades, start_date, end_date)

    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None]

    by_style_rows = stats_by_style(closed)
    dte_win = dte_bucket_win_rates(closed)
    by_dte_rows = []
    for bucket in _DTE_BUCKET_ORDER:
        info = dte_win.get(bucket)
        if not info or not info.get("count"):
            continue
        by_dte_rows.append(
            {
                "bucket": bucket,
                "count": int(info["count"]),
                "win_rate": float(info.get("win_rate") or 0.0),
                "avg_pnl_net": float(info.get("avg_pnl_net") or 0.0),
                "sum_pnl_net": float(info.get("sum_pnl_net") or 0.0),
            }
        )

    worst = sorted(closed, key=lambda t: t["pnl_net"])[: top_n]
    best = sorted(closed, key=lambda t: t["pnl_net"], reverse=True)[: top_n]

    return JournalStatsByStyleResponse(
        period={
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
        },
        total_count=len(closed),
        total_pnl_net=float(sum(t["pnl_net"] for t in closed)) if closed else 0.0,
        by_style=by_style_rows,
        by_dte=by_dte_rows,
        worst_trades=[_compact_trade_for_api(t) for t in worst],
        best_trades=[_compact_trade_for_api(t) for t in best],
    )


@router.post("/qa", response_model=JournalQaResponse)
def journal_qa(payload: JournalQaRequest) -> JournalQaResponse:
    """Single-turn LLM Q&A over the user's trade journal, anchored on their framework."""
    from src.services.journal_qa_service import generate_answer

    portfolio = DEFAULT_PORTFOLIO_LABEL
    since = date.today() - timedelta(days=payload.trade_window_days)
    trades = _load_trades(portfolio, since=since)
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None]
    closed.sort(key=lambda t: t.get("exit_time") or t.get("entry_time") or datetime.min, reverse=True)
    closed = closed[: payload.trade_limit]

    try:
        answer, fw_hash = generate_answer(
            framework=payload.framework,
            question=payload.question,
            trades=closed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("journal qa failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JournalQaResponse(
        answer=answer,
        trades_considered=len(closed),
        framework_hash=fw_hash,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


# --- moomoo live sync (Phase B) ---------------------------------------------


@router.post("/sync-live", response_model=MoomooSyncResponse)
def sync_live(payload: MoomooSyncRequest) -> MoomooSyncResponse:
    """Pull Moomoo trade-account history and ingest into the journal pipeline.

    Idempotent: re-running the same window is a no-op (external_id hash
    dedup). Requires ``MOOMOO_OPEND_ENABLED=true`` and a logged-in OpenD.
    """
    from src.journal.brokers.moomoo_live import MoomooLiveError
    from src.services.moomoo_sync_service import sync_live_orders

    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid datetime: {s!r}") from exc

    try:
        result = sync_live_orders(
            start=_parse_dt(payload.start),
            end=_parse_dt(payload.end),
            window_days=payload.window_days,
            trd_env=payload.trd_env,
            market=payload.market,
            portfolio=DEFAULT_PORTFOLIO_LABEL,
        )
    except MoomooLiveError as exc:
        # 503 because this is an upstream-not-available kind of failure
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("moomoo sync-live failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return MoomooSyncResponse(**result.to_dict())

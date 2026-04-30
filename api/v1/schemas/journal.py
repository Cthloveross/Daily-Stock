# -*- coding: utf-8 -*-
"""Pydantic schemas for the Journal REST endpoints (Phase 0 v4)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Dict, List

from pydantic import BaseModel, Field


class TradeItem(BaseModel):
    id: int
    portfolio_label: str
    is_option: bool
    raw_symbol: Optional[str] = None
    underlying: str
    expiry: Optional[date] = None
    strike: Optional[float] = None
    right: Optional[str] = None
    direction: str
    status: str
    quantity: int
    avg_entry_price: float
    avg_exit_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    hold_seconds: Optional[int] = None
    dte_at_entry: Optional[int] = None
    dte_bucket: Optional[str] = None
    pnl_gross: Optional[float] = None
    pnl_net: Optional[float] = None
    pnl_pct: Optional[float] = None
    total_fee: Optional[float] = None
    trade_style: Optional[str] = None
    regime_score_at_entry: Optional[int] = None
    was_fake_breakout: Optional[bool] = None
    user_notes: Optional[str] = None
    emotional_state: Optional[str] = None
    strategy_tag_ai: Optional[str] = None


class TradeListResponse(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[TradeItem]


class RealityTestResponse(BaseModel):
    total_trades: int
    total_pnl_net: float
    top_n: int
    top_n_pnl_net: float
    top_n_ids: list[int]
    pnl_without_top_n: float
    top_n_pct_of_total: Optional[float] = None
    median_pnl_net: Optional[float] = None


class HealthCheckItem(BaseModel):
    check_date: date
    total_orders: int
    orders_0dte: int
    orders_1_3dte: int
    orders_opening_hour: int
    top_underlying: Optional[str] = None
    top_underlying_pct: Optional[float] = None
    warnings_json: list[Any] = Field(default_factory=list)
    pnl_estimate: Optional[float] = None
    regime_score: Optional[int] = None


class JournalStatsResponse(BaseModel):
    window_days: int
    closed_trade_count: int
    total_pnl_net: float
    win_rate: Optional[float] = None
    dte_distribution: dict[str, int]
    win_rate_by_bucket: dict[str, dict[str, Any]]
    reality_test: RealityTestResponse


class ImportResponse(BaseModel):
    inserted: int
    skipped: int
    trades_rebuilt: int
    message: str


class TradeUpdateRequest(BaseModel):
    user_notes: Optional[str] = None
    emotional_state: Optional[str] = None
    trade_style: Optional[str] = None


class MonthlyReviewItem(BaseModel):
    year_month: str
    current_phase: int
    review_markdown: str
    generated_at: Optional[datetime] = None


class MonthlyReviewListResponse(BaseModel):
    count: int
    items: list[MonthlyReviewItem]


class MonthlyReviewGenerateRequest(BaseModel):
    dry_run: bool = False


# ============================================================
# Stats-by-style + Journal QA (user-defined framework analysis)
# ============================================================


class StyleBucketStat(BaseModel):
    """Aggregated stats for trades of one `trade_style` label."""
    style: str
    count: int
    win_rate: float = Field(..., description="0..1")
    avg_pnl_net: float
    sum_pnl_net: float
    median_hold_seconds: Optional[int] = None
    avg_pnl_pct: Optional[float] = None


class DteBucketStat(BaseModel):
    """Aggregated stats for trades in one DTE bucket."""
    bucket: str
    count: int
    win_rate: float = Field(..., description="0..1")
    avg_pnl_net: float
    sum_pnl_net: float


class JournalStatsByStyleResponse(BaseModel):
    """Style + DTE P&L breakdown for a date window."""
    period: Dict[str, Optional[str]] = Field(
        ..., description="{'start': YYYY-MM-DD, 'end': YYYY-MM-DD}",
    )
    total_count: int
    total_pnl_net: float
    by_style: List[StyleBucketStat] = Field(default_factory=list)
    by_dte: List[DteBucketStat] = Field(default_factory=list)
    worst_trades: List[Dict[str, Any]] = Field(default_factory=list)
    best_trades: List[Dict[str, Any]] = Field(default_factory=list)


class JournalQaRequest(BaseModel):
    framework: str = Field(..., max_length=10000, description="用户自定义交易框架文本")
    question: str = Field(..., max_length=2000)
    trade_window_days: int = Field(30, ge=1, le=365)
    trade_limit: int = Field(50, ge=1, le=200)


class JournalQaResponse(BaseModel):
    answer: str = Field(..., description="LLM 返回的中文 Markdown 回答")
    trades_considered: int
    framework_hash: str = Field(..., description="sha256(framework) 前 16 位")
    generated_at: str


# ============================================================
# Moomoo live trade-account sync
# ============================================================


class MoomooSyncRequest(BaseModel):
    """Trigger a one-shot sync of the Moomoo live trade account into the journal."""

    start: Optional[str] = Field(
        None,
        description="Window start (ISO 8601). When omitted, end - window_days.",
    )
    end: Optional[str] = Field(
        None,
        description="Window end (ISO 8601). When omitted, current UTC time.",
    )
    window_days: int = Field(
        7, ge=1, le=180, description="Lookback window when `start` is omitted."
    )
    trd_env: Optional[str] = Field(
        None,
        description="Override MOOMOO_TRADE_ENV. Allowed: 'SIMULATE' (default) or 'LIVE'.",
    )
    market: str = Field("US", description="Trade market filter: US / HK / CN.")


class MoomooSyncResponse(BaseModel):
    window_start: str
    window_end: str
    trd_env: str
    market: str
    fetched: int
    inserted: int
    skipped: int
    trades_rebuilt: int
    message: str
    note: Optional[str] = None

# -*- coding: utf-8 -*-
"""Journal SQLAlchemy models (reuse the shared Base from src.storage).

Design note (departure from New-docs/05): the real repo has
``portfolio_trades`` (not ``portfolio_events``) and its ``symbol`` column is
``String(16)`` — too narrow for OCC options (17+ chars). Rather than risk
the stability of the existing Portfolio subsystem with an intrusive ALTER, we
keep all Journal data in a dedicated ``journal_*`` table family. The Portfolio
subsystem is untouched and continues to serve A/HK workflows.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.storage import Base

__all__ = [
    "JournalImport",
    "JournalOrder",
    "JournalTrade",
    "JournalShadowTrade",
    "JournalHealthCheck",
    "JournalMonthlyReview",
    "JournalPhaseState",
]


class JournalImport(Base):
    """CSV import audit trail (one row per CSV file ingested)."""

    __tablename__ = "journal_imports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_path = Column(String(512), nullable=False)
    csv_sha256 = Column(String(64), nullable=False, unique=True)
    broker = Column(String(32), nullable=False)  # 'moomoo_us' etc.
    portfolio_label = Column(String(64), nullable=False, default="default_moomoo_us")
    imported_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    rows_total = Column(Integer, default=0)
    rows_imported = Column(Integer, default=0)
    rows_skipped = Column(Integer, default=0)
    status = Column(String(16), nullable=False, default="success")  # success/partial/failed
    error = Column(Text)


class JournalOrder(Base):
    """One filled order (post fill-merge). Raw event layer; FIFO matcher
    consumes these rows and produces ``JournalTrade`` rows.

    Generous column widths (OCC symbols can be 17+ chars; ``raw_row`` keeps the
    original dict for audit / future schema drift).
    """

    __tablename__ = "journal_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_id = Column(Integer, ForeignKey("journal_imports.id"), nullable=False, index=True)
    portfolio_label = Column(String(64), nullable=False, default="default_moomoo_us", index=True)
    external_id = Column(String(64), nullable=False)  # Moomoo order id or hash fallback

    # Instrument identification
    raw_symbol = Column(String(32), nullable=False, index=True)  # 'TSLA260417P382500' or 'NVDA'
    is_option = Column(Boolean, nullable=False, default=False)
    underlying = Column(String(16), nullable=False, index=True)
    expiry = Column(Date)
    strike = Column(Float)
    right = Column(String(1))  # 'C' / 'P' / NULL for equity

    # Trade information
    side = Column(String(8), nullable=False)  # 'buy' / 'sell'
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    order_time = Column(DateTime, index=True)  # UTC-aware serialized as naive UTC
    session = Column(String(16))  # 'regular' / 'pre' / 'post'
    first_fill_time = Column(DateTime, index=True)
    last_fill_time = Column(DateTime)

    # Fees (per New-docs/05 §1.1)
    commission = Column(Float, default=0.0)
    platform_fee = Column(Float, default=0.0)
    trading_activity_fee = Column(Float, default=0.0)
    options_regulatory_fee = Column(Float, default=0.0)
    occ_fee = Column(Float, default=0.0)
    contract_fee = Column(Float, default=0.0)
    sec_fee = Column(Float, default=0.0)
    settlement_fee = Column(Float, default=0.0)
    total_fee = Column(Float, default=0.0)

    raw_row = Column(Text)  # original CSV row(s) as JSON

    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint("portfolio_label", "external_id", name="uix_journal_orders_external"),
        Index("ix_journal_orders_und_time", "underlying", "first_fill_time"),
    )


class JournalTrade(Base):
    """FIFO-matched complete trade (or open position)."""

    __tablename__ = "journal_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_label = Column(String(64), nullable=False, default="default_moomoo_us", index=True)

    # Instrument
    is_option = Column(Boolean, nullable=False, default=False)
    raw_symbol = Column(String(32), index=True)
    underlying = Column(String(16), nullable=False, index=True)
    expiry = Column(Date)
    strike = Column(Float)
    right = Column(String(1))

    # Trade info
    direction = Column(String(8), nullable=False)  # 'long' / 'short'
    status = Column(String(8), nullable=False, default="closed")  # 'open' / 'closed'
    quantity = Column(Integer, nullable=False)
    avg_entry_price = Column(Float, nullable=False)
    avg_exit_price = Column(Float)
    entry_time = Column(DateTime, nullable=False, index=True)
    exit_time = Column(DateTime)
    hold_seconds = Column(Integer)
    dte_at_entry = Column(Integer)
    dte_bucket = Column(String(16), index=True)  # '0DTE' / '1-3DTE' / '4-7DTE' / '8-30DTE' / '30+DTE'

    # PnL
    pnl_gross = Column(Float)
    total_fee = Column(Float, default=0.0)
    pnl_net = Column(Float)
    pnl_pct = Column(Float)

    # Related orders (JSON arrays of JournalOrder.id)
    open_order_ids = Column(Text)
    close_order_ids = Column(Text)

    # Breakout Filter fields (Stage 5/6 populates these)
    trade_style = Column(String(32))  # 'breakout_chase' / 'retest' / 'pullback' / ...
    regime_score_at_entry = Column(Integer)
    pre_filter_pass = Column(Boolean)
    breakout_volume_mult = Column(Float)
    timeframe_alignment = Column(Integer)
    rs_vs_spy = Column(Float)
    entry_was_retest = Column(Boolean)
    was_fake_breakout = Column(Boolean)

    # AI analysis (Stage 9)
    strategy_tag_ai = Column(String(32))
    entry_reason_ai = Column(Text)
    exit_reason_ai = Column(Text)
    alignment_score = Column(Integer)
    mistakes_ai = Column(Text)  # JSON array
    lesson_ai = Column(Text)
    ai_analyzed_at = Column(DateTime)

    # User fields
    user_notes = Column(Text)
    emotional_state = Column(String(16))
    screenshot_url = Column(String(512))

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("ix_journal_trades_und_entry", "underlying", "entry_time"),
        Index("ix_journal_trades_style", "trade_style"),
        Index("ix_journal_trades_status", "status"),
    )


class JournalShadowTrade(Base):
    """Phase 1 virtual trade tracker. Stage 0/2 only creates the table."""

    __tablename__ = "journal_shadow_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_label = Column(String(64), default="default_moomoo_us", index=True)
    raw_symbol = Column(String(32))
    is_option = Column(Boolean, default=False)
    underlying = Column(String(16))
    expiry = Column(Date)
    strike = Column(Float)
    right = Column(String(1))
    direction = Column(String(8), nullable=False)
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    intended_hold = Column(String(16))
    rationale = Column(Text)
    exit_price = Column(Float)
    exit_time = Column(DateTime)
    current_price = Column(Float)
    current_pnl = Column(Float)
    status = Column(String(8), default="open")
    closed_reason = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)


class JournalHealthCheck(Base):
    """Daily numerical health check. Stage 2 populates the pure-stats fields;
    Stage 4 fills ``regime_score``; Stage 5 fills ``warnings_json``.
    """

    __tablename__ = "journal_health_checks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_label = Column(String(64), default="default_moomoo_us", index=True)
    check_date = Column(Date, nullable=False, index=True)
    total_orders = Column(Integer, default=0)
    orders_0dte = Column(Integer, default=0)
    orders_1_3dte = Column(Integer, default=0)
    orders_opening_hour = Column(Integer, default=0)
    top_underlying = Column(String(16))
    top_underlying_pct = Column(Float)
    warnings_json = Column(Text)  # JSON array of strings
    pnl_estimate = Column(Float)
    regime_score = Column(Integer)
    generated_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("portfolio_label", "check_date", name="uix_journal_health_day"),
    )


class JournalMonthlyReview(Base):
    """AI-generated monthly retrospective. Stage 9 populates."""

    __tablename__ = "journal_monthly_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_label = Column(String(64), default="default_moomoo_us", index=True)
    year_month = Column(String(7), nullable=False)  # 'YYYY-MM'
    current_phase = Column(Integer, nullable=False, default=0)
    stats_json = Column(Text, nullable=False)
    review_markdown = Column(Text, nullable=False)
    generated_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "portfolio_label", "year_month", name="uix_journal_monthly_review"
        ),
    )


class JournalPhaseState(Base):
    """Single-row table tracking current Journey Phase.

    Enforced to one row by CHECK constraint; upsert pattern in storage.py.
    """

    __tablename__ = "journal_phase_state"

    id = Column(Integer, primary_key=True)  # always 1
    phase = Column(Integer, nullable=False, default=0)
    phase_started = Column(Date, nullable=False, default=date.today)
    notes = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

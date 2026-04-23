# -*- coding: utf-8 -*-
"""Regime Classifier SQLAlchemy models.

Single table ``regime_scores`` — one row per date. Structured so each of the
six scorers' contributions is persisted for later reflexivity analysis.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Integer, String, Text

from src.storage import Base

__all__ = ["RegimeScore"]


class RegimeScore(Base):
    """Daily Regime Score snapshot."""

    __tablename__ = "regime_scores"

    date = Column(Date, primary_key=True)
    score = Column(Integer, nullable=False)  # [-50, 100]
    label = Column(String(16), nullable=False)  # aggressive / standard / cautious / no_trade

    # Six-dimension breakdown
    d1_direction = Column(Integer, default=0)
    d2_volatility = Column(Integer, default=0)
    d3_macro_penalty = Column(Integer, default=0)
    d4_sector = Column(Integer, default=0)
    d5_prev_day = Column(Integer, default=0)
    d6_premarket = Column(Integer, default=0)

    snapshot_json = Column(Text)  # raw fetcher payload for debugging / future analysis
    version = Column(String(8), nullable=False, default="v1")

    # Optional reflexivity columns (Phase 1 populates)
    user_perceived_quality = Column(Integer)  # -2 .. +2
    user_did_trade = Column(Integer)  # 0/1

    generated_at = Column(DateTime, default=datetime.now, nullable=False)

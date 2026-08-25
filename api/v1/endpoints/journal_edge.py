# -*- coding: utf-8 -*-
"""Edge-panel REST endpoint (Phase 1 E-5).

Exposes:
    GET /api/v1/journal/edge-panel

Read-only: assembles the 0-1DTE regime gate, the DTE-bucket edge forensics
and the R2/R3 discipline dials from ``src.journal.edge_panel``.  Never
writes, never places orders.  Market-data failures fail closed inside the
service layer (contract of doc 16 E-5), so this endpoint always answers 200
with an honest ``data_status``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from api.v1.endpoints.journal import _load_trades
from api.v1.schemas.journal_edge import EdgePanelResponse
from src.journal.edge_panel import build_edge_panel
from src.journal.storage import DEFAULT_PORTFOLIO_LABEL

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/edge-panel", response_model=EdgePanelResponse)
def get_edge_panel(
    portfolio: str = Query(DEFAULT_PORTFOLIO_LABEL),
) -> EdgePanelResponse:
    """Edge 面板：0-1DTE gate 档位、DTE 分桶期望、频率/费率纪律仪表。

    Gate features (QQQ / SOXX 20d momentum, Moreira-Muir scale) use daily
    closes with information through yesterday, cached in-process for >= 30
    minutes; when market data cannot be fetched the gate ships the
    fail-closed shape (``allow_0_1dte=false``, ``data_status="unavailable"``)
    while the journal-derived blocks stay real.
    """
    trades = _load_trades(portfolio)
    payload = build_edge_panel(trades)
    return EdgePanelResponse(**payload)

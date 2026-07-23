# -*- coding: utf-8 -*-
"""Evidence-first daily opportunity research primitives.

The package deliberately contains no broker actions, LLM calls, or storage
writes.  Its first vertical slice ranks completed-daily-bar research
candidates while preserving missing-data and provenance boundaries.
"""

from src.opportunities.engine import (
    SIGNAL_VERSION,
    DailyHistoryInput,
    build_daily_opportunity_run,
)

__all__ = [
    "SIGNAL_VERSION",
    "DailyHistoryInput",
    "build_daily_opportunity_run",
]

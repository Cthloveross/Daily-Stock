# -*- coding: utf-8 -*-
"""/phase bot command — show current Phase and progression."""
from __future__ import annotations

import logging
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


_PHASE_LABELS = {
    0: "Mirror (照镜子)",
    1: "Lab (实验室)",
    2: "Mixed (策略混合)",
    3: "Core (核心持仓)",
}


class PhaseCommand(BotCommand):
    """Show the current Journey Phase and days in phase."""

    @property
    def name(self) -> str:
        return "phase"

    @property
    def aliases(self) -> List[str]:
        return ["阶段", "phase-status"]

    @property
    def description(self) -> str:
        return "Current Journey Phase + days in phase"

    @property
    def usage(self) -> str:
        return "/phase"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        try:
            from datetime import date

            from src.journal.models import JournalPhaseState
            from src.journal.storage import init_journal_schema
            from src.storage import get_db

            init_journal_schema()
            db = get_db()
            with db.session_scope() as session:
                row = session.get(JournalPhaseState, 1)
                phase = int(row.phase) if row else 0
                started = row.phase_started if row else None
            days = (date.today() - started).days if started else None
            label = _PHASE_LABELS.get(phase, f"Phase {phase}")
            lines = [
                f"🧭 *Phase {phase}* — {label}",
                f"Started: {started or '—'}",
            ]
            if days is not None:
                lines.append(f"In phase for: {days} day(s)")
            return BotResponse.markdown_response("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            logger.exception("phase command failed")
            return BotResponse.error_response(f"phase 命令失败: {exc}")

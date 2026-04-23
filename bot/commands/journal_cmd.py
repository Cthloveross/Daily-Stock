# -*- coding: utf-8 -*-
"""/journal bot command — summarise today's health check and reality test."""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class JournalCommand(BotCommand):
    """Show today's Daily Health Check + Reality Test summary."""

    @property
    def name(self) -> str:
        return "journal"

    @property
    def aliases(self) -> List[str]:
        return ["日志", "今日体检"]

    @property
    def description(self) -> str:
        return "Today's journal health check + Reality Test"

    @property
    def usage(self) -> str:
        return "/journal [today|reality]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        sub = (args[0] if args else "today").lower()
        try:
            if sub == "reality":
                return self._reality_test()
            return self._today_health()
        except Exception as exc:  # noqa: BLE001
            logger.exception("journal command failed")
            return BotResponse.error_response(f"journal 命令失败: {exc}")

    def _today_health(self) -> BotResponse:
        from sqlalchemy import select

        from src.journal.models import JournalHealthCheck
        from src.journal.storage import init_journal_schema
        from src.storage import get_db

        init_journal_schema()
        today = date.today()
        db = get_db()
        with db.session_scope() as session:
            row = (
                session.execute(
                    select(JournalHealthCheck).where(
                        JournalHealthCheck.check_date == today
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return BotResponse.markdown_response(
                    f"📋 *Daily Health Check · {today}*\n\n"
                    "今日尚未生成体检数据。盘后导入 CSV 后重新查询。"
                )
            warnings = []
            if row.warnings_json:
                try:
                    warnings = json.loads(row.warnings_json)
                except Exception:  # noqa: BLE001
                    warnings = []

            lines = [
                f"📋 *Daily Health Check · {row.check_date}*",
                f"Orders: {row.total_orders}  "
                f"(0DTE {row.orders_0dte}, 1-3DTE {row.orders_1_3dte}, "
                f"opening-hour {row.orders_opening_hour})",
            ]
            if row.top_underlying:
                lines.append(
                    f"Top symbol: `{row.top_underlying}` "
                    f"({(row.top_underlying_pct or 0):.1f}%)"
                )
            if row.pnl_estimate is not None:
                sign = "+" if row.pnl_estimate >= 0 else ""
                lines.append(f"Est PnL: {sign}${row.pnl_estimate:,.2f}")
            if row.regime_score is not None:
                lines.append(f"Regime @ open: {row.regime_score}")
            if warnings:
                lines.append("⚠️ Warnings: " + " / ".join(str(w) for w in warnings))
            return BotResponse.markdown_response("\n".join(lines))

    def _reality_test(self) -> BotResponse:
        from src.journal.analytics import reality_test
        from src.journal.models import JournalTrade
        from src.journal.storage import init_journal_schema
        from src.storage import get_db

        init_journal_schema()
        db = get_db()
        with db.session_scope() as session:
            from sqlalchemy import select

            rows = session.execute(select(JournalTrade)).scalars().all()
            trades = [
                {
                    "id": r.id,
                    "status": r.status,
                    "pnl_net": r.pnl_net,
                }
                for r in rows
            ]
        rt = reality_test(trades, top_n=5)
        if rt["total_trades"] == 0:
            return BotResponse.markdown_response(
                "尚无已关闭交易。导入 CSV 后再试。"
            )

        pct = rt["top_n_pct_of_total"]
        pct_str = f"{pct:.1f}%" if pct is not None else "—"
        lines = [
            "📊 *Reality Test*",
            f"Total closed: {rt['total_trades']}",
            f"Total net PnL: ${rt['total_pnl_net']:,.2f}",
            f"Top 5 PnL: ${rt['top_n_pnl_net']:,.2f} ({pct_str} of total)",
            f"Without Top 5: ${rt['pnl_without_top_n']:,.2f}",
        ]
        return BotResponse.markdown_response("\n".join(lines))

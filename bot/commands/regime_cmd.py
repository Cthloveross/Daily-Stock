# -*- coding: utf-8 -*-
"""/regime bot command — show today's Regime Score."""
from __future__ import annotations

import logging
from datetime import date
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class RegimeCommand(BotCommand):
    """Show the latest saved Regime Score."""

    @property
    def name(self) -> str:
        return "regime"

    @property
    def aliases(self) -> List[str]:
        return ["市场环境"]

    @property
    def description(self) -> str:
        return "Latest Regime Score + action hint"

    @property
    def usage(self) -> str:
        return "/regime"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        try:
            from src.regime.classifier import classify
            from src.regime.storage import get_regime_score

            target = date.today()
            if args:
                try:
                    target = date.fromisoformat(args[0])
                except ValueError:
                    return BotResponse.error_response(
                        f"日期格式应为 YYYY-MM-DD，收到：{args[0]}"
                    )

            row = get_regime_score(target)
            if row is None:
                return BotResponse.markdown_response(
                    f"🌅 *Regime {target}*\n\n"
                    "尚未计算。可本地跑 `python -m src.regime.cli --date "
                    f"{target.isoformat()}`，或 dispatch workflow。"
                )
            label, hint = classify(int(row["score"]))
            lines = [
                f"🌅 *Regime · {row['date']}*",
                f"Score `{row['score']:+d}`  Label `{label}`",
                f"_{hint}_",
                "",
                f"• d1 方向: {row['d1_direction']:+d} / 30",
                f"• d2 波动: {row['d2_volatility']:+d} / 20",
                f"• d3 宏观: {row['d3_macro_penalty']:+d} / 0",
                f"• d4 板块: {row['d4_sector']:+d} / 15",
                f"• d5 前日: {row['d5_prev_day']:+d} / 13",
                f"• d6 盘前: {row['d6_premarket']:+d} / 20",
            ]
            return BotResponse.markdown_response("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            logger.exception("regime command failed")
            return BotResponse.error_response(f"regime 命令失败: {exc}")

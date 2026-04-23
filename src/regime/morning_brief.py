# -*- coding: utf-8 -*-
"""Format + dispatch the daily Regime morning brief to Telegram.

``format_brief`` returns the Jinja-rendered markdown.
``send_brief`` additionally pushes it via :class:`TelegramSender`, with an
email fallback when Telegram is not configured.

Usage (CLI, used by the GitHub Actions cron in Stage 4):
    python -m src.regime.morning_brief --send
    python -m src.regime.morning_brief --send --date 2026-04-17
    python -m src.regime.morning_brief --format-only      # print to stdout
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from src.regime.classifier import RegimeResult, compute_regime_score

logger = logging.getLogger(__name__)

__all__ = ["format_brief", "send_brief", "main"]


_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
_TEMPLATE_NAME = "regime_morning_brief.md.j2"


def _jinja_env():
    """Lazy-init Jinja environment pointing at the repo-level ``templates/``."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape([]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _macro_event_strings(events: dict) -> list[str]:
    """Humanise the boolean flags from get_macro_events into bullet lines."""
    out = []
    if events.get("fomc_today"):
        out.append("FOMC / Fed funds rate decision")
    if events.get("cpi_today"):
        out.append("CPI release")
    if events.get("nfp_today"):
        out.append("Nonfarm payrolls")
    count = int(events.get("earnings_count_watchlist") or 0)
    if count:
        out.append(f"{count} watchlist earnings release(s)")
    if events.get("tariff_headline_today"):
        out.append("Tariff / trade-policy headline")
    return out


def _warnings_from(result: RegimeResult) -> list[str]:
    warnings: list[str] = []
    if result.label == "no_trade":
        warnings.append("Regime below 35 -> stand aside, no new risk.")
    if result.d3_macro_penalty <= -30:
        warnings.append("Major macro event day; avoid held overnight deltas.")
    if result.d2_volatility == -15:
        warnings.append("VIX in crisis regime; size down and widen stops.")
    return warnings


def format_brief(result: RegimeResult) -> str:
    """Render a :class:`RegimeResult` through the Jinja template."""
    env = _jinja_env()
    tpl = env.get_template(_TEMPLATE_NAME)
    ctx = {
        "date": result.date.isoformat() if hasattr(result.date, "isoformat") else str(result.date),
        "score": int(result.score),
        "label": result.label,
        "action_hint": result.action_hint,
        "d1_direction": int(result.d1_direction),
        "d2_volatility": int(result.d2_volatility),
        "d3_macro_penalty": int(result.d3_macro_penalty),
        "d4_sector": int(result.d4_sector),
        "d5_prev_day": int(result.d5_prev_day),
        "d6_premarket": int(result.d6_premarket),
        "spy": result.snapshot.get("spy", {}),
        "vix": result.snapshot.get("vix", {}),
        "macro_events": _macro_event_strings(result.snapshot.get("events", {})),
        "warnings": _warnings_from(result),
    }
    return tpl.render(**ctx)


def send_brief(result: RegimeResult) -> bool:
    """Push the rendered brief to Telegram. Returns True on success."""
    body = format_brief(result)
    try:
        from src.config import get_config
        from src.notification_sender.telegram_sender import TelegramSender

        cfg = get_config()
        sender = TelegramSender(cfg)
        if not sender._is_telegram_configured():
            logger.warning("Telegram not configured; printing brief to stdout instead")
            print(body)
            return False
        return bool(sender.send_to_telegram(body))
    except Exception as exc:  # noqa: BLE001
        logger.exception("send_brief failed: %s", exc)
        return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--format-only", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    target = date.fromisoformat(args.date) if args.date else date.today()
    result = compute_regime_score(target_date=target, save_to_db=not args.no_save)

    body = format_brief(result)

    if args.format_only:
        print(body)
        return 0

    if args.send:
        ok = send_brief(result)
        print("sent" if ok else "NOT sent")
        return 0 if ok else 2

    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

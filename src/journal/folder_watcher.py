# -*- coding: utf-8 -*-
"""Watchdog-based folder watcher for broker CSV drops.

Monitors ``INBOX_DIR`` (default ``~/Daily-Stock-Inbox``). When a ``.csv`` file
matching a Moomoo export name pattern lands, it:

1. Waits briefly for the OS to finish writing it (large files).
2. Parses with the configured broker parser.
3. Persists events + rebuilds trades.
4. Moves the file to ``PROCESSED_DIR``.
5. Sends a Telegram summary (best-effort; falls back to log).

Designed to be run as a long-lived process (see :mod:`scripts.run_journal_watcher`).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Optional

from src.journal.brokers.moomoo_us import parse as parse_moomoo
from src.journal.matcher import match_legs_fifo
from src.journal.storage import (
    DEFAULT_PORTFOLIO_LABEL,
    init_journal_schema,
    insert_events_from_orders,
    query_events_for_matching,
    record_import,
    replace_trades,
)

logger = logging.getLogger(__name__)

__all__ = ["MoomooCsvHandler", "start_watching"]


_ALLOWED_PREFIXES = ("History", "Trade", "Orders", "moomoo", "Moomoo")


def _expanduser(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def _resolve_dirs() -> tuple[Path, Path]:
    """Resolve inbox / processed paths from env with sensible defaults."""
    inbox = _expanduser(os.environ.get("INBOX_DIR", "~/Daily-Stock-Inbox"))
    processed = _expanduser(
        os.environ.get("PROCESSED_DIR", "~/Daily-Stock-Processed")
    )
    inbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    return inbox, processed


def _looks_like_moomoo_csv(path: Path) -> bool:
    name = path.name
    if path.suffix.lower() != ".csv":
        return False
    return any(name.startswith(p) or name.lower().startswith(p.lower()) for p in _ALLOWED_PREFIXES)


def _notify_telegram(text: str) -> None:
    """Best-effort Telegram notify. Never raises."""
    try:
        from src.config import get_config
        from src.notification_sender.telegram_sender import TelegramSender

        sender = TelegramSender(get_config())
        if sender._is_telegram_configured():
            sender.send_to_telegram(text)
        else:
            logger.info("Telegram not configured — %s", text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram notify failed: %s", exc)


def process_csv(path: Path, portfolio: str = DEFAULT_PORTFOLIO_LABEL) -> dict:
    """Parse + import + rebuild + move. Returns a summary dict."""
    content = path.read_bytes()
    init_journal_schema()
    orders = parse_moomoo(content)
    import_id = record_import(
        source_path=str(path),
        content=content,
        broker="moomoo_us",
        rows_total=len(orders),
        portfolio_label=portfolio,
    )
    if import_id is None:
        return {"status": "duplicate", "file": path.name, "inserted": 0}
    inserted, skipped = insert_events_from_orders(
        import_id, orders, portfolio_label=portfolio
    )
    events = query_events_for_matching(portfolio_label=portfolio)
    trades = match_legs_fifo(events)
    replaced = replace_trades(trades, portfolio_label=portfolio)
    return {
        "status": "ok",
        "file": path.name,
        "inserted": inserted,
        "skipped": skipped,
        "trades_total": replaced,
    }


class MoomooCsvHandler:
    """Minimal FileSystemEventHandler implementation.

    Kept as a plain class rather than subclassing ``watchdog.events.FileSystemEventHandler``
    so the test suite can import this module without watchdog installed.
    """

    def __init__(self, processed_dir: Path, portfolio: str = DEFAULT_PORTFOLIO_LABEL):
        self._processed = processed_dir
        self._portfolio = portfolio
        self._lock = Lock()

    # Matches watchdog's expected callback surface.
    def dispatch(self, event) -> None:  # pragma: no cover -- thin wrapper
        if getattr(event, "is_directory", False):
            return
        if getattr(event, "event_type", "") not in ("created", "moved"):
            return
        src = Path(getattr(event, "dest_path", None) or event.src_path)
        self.handle_path(src)

    def handle_path(self, path: Path) -> Optional[dict]:
        """Public entry used by dispatch + tests."""
        if not _looks_like_moomoo_csv(path):
            logger.info("Skipping non-Moomoo file: %s", path.name)
            return None
        with self._lock:
            return self._handle_inner(path)

    def _handle_inner(self, path: Path) -> Optional[dict]:
        # Let the OS finish writing large files.
        time.sleep(float(os.environ.get("DS_WATCHER_SETTLE_SECONDS", "1.0")))
        if not path.exists():
            logger.warning("File vanished before we could read it: %s", path)
            return None
        try:
            summary = process_csv(path, portfolio=self._portfolio)
        except Exception as exc:  # noqa: BLE001
            logger.exception("CSV processing failed for %s", path)
            _notify_telegram(f"❌ 处理 {path.name} 失败：{exc}")
            return {"status": "error", "file": path.name, "error": str(exc)}

        # Move to processed (avoid name collisions).
        dest = self._processed / path.name
        if dest.exists():
            dest = self._processed / f"{path.stem}_{int(time.time())}{path.suffix}"
        try:
            shutil.move(str(path), str(dest))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Move %s -> %s failed: %s", path, dest, exc)

        if summary["status"] == "duplicate":
            _notify_telegram(f"ℹ️ {path.name} 已导入过（SHA256 重复），跳过。")
        else:
            _notify_telegram(
                "✅ Journal 导入 `{file}`：新增 {inserted} 单 / 跳过 {skipped} / "
                "总 {total} 笔 trades".format(
                    file=summary["file"],
                    inserted=summary["inserted"],
                    skipped=summary["skipped"],
                    total=summary["trades_total"],
                )
            )
        return summary


def start_watching(portfolio: str = DEFAULT_PORTFOLIO_LABEL) -> None:
    """Block and watch ``INBOX_DIR`` until SIGINT."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:  # pragma: no cover - watcher needs watchdog
        raise SystemExit(
            "watchdog package is required for start_watching(); "
            "install it with `pip install watchdog>=3.0`"
        ) from exc

    inbox, processed = _resolve_dirs()
    handler = MoomooCsvHandler(processed_dir=processed, portfolio=portfolio)

    # Wrap our lightweight handler in a watchdog-compatible adapter.
    class _Adapter(FileSystemEventHandler):  # type: ignore[misc]
        def on_created(self, event):  # noqa: D401 - tiny adapter
            handler.dispatch(event)

        def on_moved(self, event):  # noqa: D401
            handler.dispatch(event)

    observer = Observer()
    observer.schedule(_Adapter(), str(inbox), recursive=False)
    observer.start()
    logger.info("Watching %s (portfolio=%s) — move processed files to %s",
                inbox, portfolio, processed)
    # Sweep once on startup: catch files that landed while we were offline.
    for f in inbox.iterdir():
        if f.is_file():
            handler.handle_path(f)
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Shutting down watcher…")
    finally:
        observer.stop()
        observer.join()

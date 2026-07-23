#!/usr/bin/env python3
"""Run a child process while bounding its stdout and stderr log files.

This wrapper is intended for macOS LaunchAgents, whose ``StandardOutPath`` and
``StandardErrorPath`` files otherwise grow without limit.  Each child stream is
drained independently into a ``RotatingFileHandler``.  Signals sent to the
wrapper are forwarded to the child's process group, and the wrapper exits with
the child's exit status.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Sequence


# Bound a single logging record as well as the file set.  Normal application
# logs are line-oriented and much smaller; this only protects against a child
# emitting an unbounded line without a newline.
_READ_CHUNK_CHARS = 64 * 1024
_PUMP_GRACE_SECONDS = 0.25
_PUMP_SHUTDOWN_SECONDS = 2.0
_GROUP_SHUTDOWN_SECONDS = 1.0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdout-log", required=True, help="child stdout log path")
    parser.add_argument("--stderr-log", required=True, help="child stderr log path")
    parser.add_argument(
        "--max-bytes",
        type=_positive_int,
        default=10 * 1024 * 1024,
        help="maximum size of each active log file (default: 10 MiB)",
    )
    parser.add_argument(
        "--backup-count",
        type=_positive_int,
        default=3,
        help="number of rotated backups per stream (default: 3)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="child command, conventionally separated with --",
    )
    args = parser.parse_args(argv)

    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a child command is required after --")

    stdout_path = Path(args.stdout_log).expanduser().resolve()
    stderr_path = Path(args.stderr_log).expanduser().resolve()
    if stdout_path == stderr_path:
        parser.error("--stdout-log and --stderr-log must be different files")
    return args


def _build_logger(
    name: str,
    path: str,
    *,
    max_bytes: int,
    backup_count: int,
) -> tuple[logging.Logger, RotatingFileHandler]:
    log_path = Path(path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.Logger(name, level=logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    return logger, handler


def _pump(stream: IO[str], logger: logging.Logger) -> None:
    """Drain one child stream without blocking the other stream."""

    try:
        while True:
            chunk = stream.readline(_READ_CHUNK_CHARS)
            if chunk == "":
                return
            # RotatingFileHandler supplies one newline per record.  Removing
            # only transport line endings keeps JSONL and conventional logs
            # parseable while preserving all message content.
            logger.info(chunk.rstrip("\r\n"))
    finally:
        stream.close()


def _signal_process_group(child: subprocess.Popen[str], signum: int) -> None:
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        pass


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - the child group is normally ours
        return True


def _stop_pipe_holders(child: subprocess.Popen[str]) -> None:
    """Stop descendants that inherited a pipe after the direct child exited."""

    if not _process_group_exists(child.pid):
        return
    _signal_process_group(child, signal.SIGTERM)
    deadline = time.monotonic() + _GROUP_SHUTDOWN_SECONDS
    while time.monotonic() < deadline:
        if not _process_group_exists(child.pid):
            return
        time.sleep(0.02)
    _signal_process_group(child, signal.SIGKILL)


def _finish_pumps(
    child: subprocess.Popen[str],
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
) -> bool:
    """Bound pump shutdown even when a descendant keeps the pipes open."""

    for thread in (stdout_thread, stderr_thread):
        thread.join(timeout=_PUMP_GRACE_SECONDS)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        _stop_pipe_holders(child)
    for thread in (stdout_thread, stderr_thread):
        thread.join(timeout=_PUMP_SHUTDOWN_SECONDS)
    return not stdout_thread.is_alive() and not stderr_thread.is_alive()


def _close_handlers(*handlers: RotatingFileHandler) -> None:
    for handler in handlers:
        handler.flush()
        handler.close()


def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    stdout_logger, stdout_handler = _build_logger(
        "child.stdout",
        args.stdout_log,
        max_bytes=args.max_bytes,
        backup_count=args.backup_count,
    )
    try:
        stderr_logger, stderr_handler = _build_logger(
            "child.stderr",
            args.stderr_log,
            max_bytes=args.max_bytes,
            backup_count=args.backup_count,
        )
    except Exception:
        _close_handlers(stdout_handler)
        raise

    child_ref: list[subprocess.Popen[str] | None] = [None]
    pending_signals: list[int] = []
    previous_handlers: dict[int, signal.Handlers] = {}

    def forward_signal(signum: int, _frame: object) -> None:
        child = child_ref[0]
        if child is None:
            pending_signals.append(signum)
            return
        _signal_process_group(child, signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, forward_signal)

    try:
        try:
            child = subprocess.Popen(
                args.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            stderr_logger.error("failed to start child: %s", exc)
            return 127
        except OSError as exc:
            stderr_logger.error("failed to start child: %s", exc)
            return 126

        child_ref[0] = child
        for signum in pending_signals:
            _signal_process_group(child, signum)

        if child.stdout is None or child.stderr is None:  # pragma: no cover
            raise RuntimeError("child pipes were not created")

        stdout_thread = threading.Thread(
            target=_pump,
            args=(child.stdout, stdout_logger),
            name="stdout-log-pump",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_pump,
            args=(child.stderr, stderr_logger),
            name="stderr-log-pump",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        returncode = child.wait()
        if not _finish_pumps(child, stdout_thread, stderr_thread):
            stderr_logger.error(
                "log pump shutdown exceeded %.2fs; abandoning inherited pipe",
                _PUMP_SHUTDOWN_SECONDS,
            )
        return returncode
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        _close_handlers(stdout_handler, stderr_handler)


def main() -> None:
    returncode = run()
    if returncode < 0:
        # Match a child terminated by a signal, including the observable
        # subprocess return code, instead of converting it to an arbitrary
        # positive status.
        signum = -returncode
        # SIGKILL and SIGSTOP cannot have handlers.  Sending SIGKILL directly
        # still gives the wrapper the same observable return code as its child.
        if signum not in {signal.SIGKILL, signal.SIGSTOP}:
            signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
        raise SystemExit(128 + signum)  # pragma: no cover - kill is synchronous
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()

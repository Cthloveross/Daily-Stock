# -*- coding: utf-8 -*-
"""Bounded, read-only-safe runtime helpers for Moomoo OpenD.

The Moomoo SDK's synchronous context constructor retries forever when OpenD
is offline.  Application code must therefore use the asynchronous quote
context path below, which is guarded by a short TCP probe and a finite READY
deadline.  Trade contexts do not expose an asynchronous constructor; callers
must run :func:`ensure_opend_ready` immediately before creating one and keep
the containing workflow inside a parent process with a hard deadline.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_TCP_TIMEOUT_SECONDS = 0.5
DEFAULT_READY_TIMEOUT_SECONDS = 3.0
DEFAULT_READY_POLL_SECONDS = 0.05
DEFAULT_SYNC_QUERY_CONNECT_TIMEOUT_SECONDS = 5.0

# The SDK settings below mutate process-wide globals.  Contexts can be created
# concurrently by the API, market-data fallbacks, and background workers, so
# configure the SDK at most once per process.
_SDK_CONFIG_LOCK = threading.Lock()
_sdk_config_result: Optional[bool] = None


class MoomooRuntimeError(RuntimeError):
    """Raised when OpenD or the Moomoo SDK is not ready within the deadline."""


def probe_opend_tcp(
    host: str,
    port: int,
    *,
    timeout: float = DEFAULT_TCP_TIMEOUT_SECONDS,
) -> bool:
    """Return whether OpenD's TCP listener accepts a connection promptly.

    This probe does not create an SDK context, authenticate an account, unlock
    trading, or consume a market-data quota.
    """
    try:
        timeout_seconds = max(0.01, float(timeout))
        port_number = int(port)
        with socket.create_connection((host, port_number), timeout=timeout_seconds):
            return True
    except (OSError, OverflowError, TypeError, ValueError):
        return False


def _configure_moomoo_file_logging() -> None:
    """Reduce SDK-owned file log volume when its private logger is available.

    ``moomoo.common.ft_logger`` is not a public API.  Keep every access
    defensive so SDK installation, import, and context creation still work if
    that module or its attributes change in a future release.
    """
    try:
        from moomoo.common.ft_logger import logger as sdk_logger
    except Exception as exc:  # noqa: BLE001 - optional private SDK surface
        logger.debug("[moomoo_runtime] SDK file logger is unavailable: %s", exc)
        return

    try:
        sdk_logger.file_level = logging.WARNING
    except Exception as exc:  # noqa: BLE001 - best-effort private SDK surface
        logger.debug("[moomoo_runtime] could not reduce SDK file log level: %s", exc)

    try:
        file_handler = getattr(sdk_logger, "fileHandler", None)
        if file_handler is not None and hasattr(file_handler, "backupCount"):
            file_handler.backupCount = 3
    except Exception as exc:  # noqa: BLE001 - best-effort private SDK surface
        logger.debug("[moomoo_runtime] could not reduce SDK log retention: %s", exc)


def _apply_moomoo_sdk_runtime_config() -> bool:
    """Apply the SDK settings once the caller holds ``_SDK_CONFIG_LOCK``."""
    try:
        from moomoo import SysConfig
    except (ImportError, AttributeError):
        return False
    except Exception as exc:  # noqa: BLE001 - optional SDK initialization
        logger.debug("[moomoo_runtime] could not import SDK runtime config: %s", exc)
        return False

    configured = True
    try:
        SysConfig.enable_console_log(False)
    except Exception as exc:  # noqa: BLE001 - tolerate SDK API drift
        configured = False
        logger.debug("[moomoo_runtime] could not disable SDK console log: %s", exc)

    try:
        SysConfig.set_all_thread_daemon(True)
    except Exception as exc:  # noqa: BLE001 - tolerate SDK API drift
        configured = False
        logger.debug("[moomoo_runtime] could not daemonize SDK workers: %s", exc)

    _configure_moomoo_file_logging()
    return configured


def configure_moomoo_sdk_runtime() -> bool:
    """Apply process-wide SDK safety settings once before contexts are created.

    Console output is disabled to prevent reconnect floods from filling the
    application's stdout logs.  SDK callback workers are made daemon threads
    so a short-lived probe or sync CLI can exit after closing its contexts.
    When the installed SDK exposes its private file logger, its threshold is
    reduced to WARNING and its retained backup count to three.  The first
    result is cached under a lock so concurrent callers never race while
    mutating SDK globals.
    """
    global _sdk_config_result

    if _sdk_config_result is not None:
        return _sdk_config_result

    with _SDK_CONFIG_LOCK:
        if _sdk_config_result is None:
            _sdk_config_result = _apply_moomoo_sdk_runtime_config()
        return _sdk_config_result


def suppress_moomoo_sdk_console() -> bool:
    """Backward-compatible alias for the full SDK runtime configuration."""
    return configure_moomoo_sdk_runtime()


def quote_context_is_ready(ctx: Any) -> bool:
    """Inspect SDK connection state without issuing a potentially blocking RPC."""
    if ctx is None:
        return False
    try:
        from moomoo import ContextStatus

        return getattr(ctx, "status", None) == ContextStatus.READY
    except (ImportError, AttributeError):
        return False


def _close_context(ctx: Any) -> None:
    """Best-effort context cleanup used on every failed readiness path."""
    if ctx is None:
        return
    try:
        ctx.close()
    except Exception as exc:  # noqa: BLE001 - never hide the readiness failure
        logger.debug("[moomoo_runtime] context close failed: %s", exc)


def create_ready_quote_context(
    host: str,
    port: int,
    *,
    tcp_timeout: float = DEFAULT_TCP_TIMEOUT_SECONDS,
    ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_READY_POLL_SECONDS,
    sync_query_connect_timeout: float = DEFAULT_SYNC_QUERY_CONNECT_TIMEOUT_SECONDS,
):
    """Create an async ``OpenQuoteContext`` and return it only after READY.

    Unlike ``OpenQuoteContext(host=..., port=...)``, this function cannot enter
    the SDK's unbounded constructor reconnect loop.  Once READY, it also sets
    the SDK's public synchronous-query connect timeout so a connection loss
    immediately before an RPC cannot wait forever for a reconnect.  Any
    context created on a failure or timeout path is closed before
    :class:`MoomooRuntimeError` is raised.
    """
    if not probe_opend_tcp(host, port, timeout=tcp_timeout):
        raise MoomooRuntimeError(
            f"OpenD TCP endpoint is not reachable at {host}:{port}"
        )

    configure_moomoo_sdk_runtime()
    try:
        from moomoo import ContextStatus, OpenQuoteContext
    except (ImportError, AttributeError) as exc:
        raise MoomooRuntimeError(
            "moomoo-api SDK is not installed or is missing OpenQuoteContext"
        ) from exc

    ctx = None
    keep_open = False
    try:
        ctx = OpenQuoteContext(
            host=host,
            port=int(port),
            is_async_connect=True,
        )
        deadline = time.monotonic() + max(0.0, float(ready_timeout))
        interval = max(0.001, float(poll_interval))
        last_status = None

        while True:
            last_status = getattr(ctx, "status", None)
            if last_status == ContextStatus.READY:
                set_connect_timeout = getattr(
                    ctx,
                    "set_sync_query_connect_timeout",
                    None,
                )
                if not callable(set_connect_timeout):
                    raise MoomooRuntimeError(
                        "Moomoo SDK cannot bound synchronous query reconnects"
                    )
                try:
                    set_connect_timeout(
                        max(0.01, float(sync_query_connect_timeout))
                    )
                except Exception as exc:  # noqa: BLE001 - fail closed on SDK drift
                    raise MoomooRuntimeError(
                        "Moomoo SDK rejected the synchronous query connect timeout"
                    ) from exc
                keep_open = True
                return ctx

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MoomooRuntimeError(
                    "OpenD SDK handshake did not become READY within "
                    f"{max(0.0, float(ready_timeout)):.2f}s "
                    f"(last_status={last_status!r})"
                )
            time.sleep(min(interval, remaining))
    except MoomooRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize optional SDK failures
        raise MoomooRuntimeError(f"OpenD SDK connection failed: {exc}") from exc
    finally:
        if not keep_open:
            _close_context(ctx)


def ensure_opend_ready(
    host: str,
    port: int,
    *,
    tcp_timeout: float = DEFAULT_TCP_TIMEOUT_SECONDS,
    ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS,
) -> None:
    """Perform a bounded quote handshake, then close it.

    This is the preflight for SDK context types, such as
    ``OpenSecTradeContext``, that do not support ``is_async_connect``.  It
    narrows the race but cannot make their constructor bounded; production
    trade-history workflows must still isolate the constructor in a
    terminable subprocess.
    """
    ctx = create_ready_quote_context(
        host,
        port,
        tcp_timeout=tcp_timeout,
        ready_timeout=ready_timeout,
    )
    try:
        return None
    finally:
        _close_context(ctx)


__all__ = [
    "DEFAULT_READY_TIMEOUT_SECONDS",
    "DEFAULT_TCP_TIMEOUT_SECONDS",
    "MoomooRuntimeError",
    "configure_moomoo_sdk_runtime",
    "create_ready_quote_context",
    "ensure_opend_ready",
    "probe_opend_tcp",
    "quote_context_is_ready",
    "suppress_moomoo_sdk_console",
]

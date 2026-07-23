# -*- coding: utf-8 -*-
"""Strictly read-only Moomoo trade-history probe.

This module deliberately sits outside the journal ingestion service.  It
queries account metadata, historical orders, fills and order fees, then
returns an in-memory export payload.  It never imports the journal storage
layer and therefore cannot mutate the project database.

The application-facing environment name is ``LIVE``; Moomoo's Python SDK
calls the same environment ``REAL``.  Account selection always uses the
stable ``acc_id`` returned by ``get_acc_list`` instead of the positional
``acc_index``.
"""
from __future__ import annotations

import math
import multiprocessing
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from src.options.occ_parser import parse_symbol
from src.services.moomoo_runtime import (
    MoomooRuntimeError,
    ensure_opend_ready,
    probe_opend_tcp,
    suppress_moomoo_sdk_console,
)

__all__ = [
    "MoomooReadonlyError",
    "ProbeConfig",
    "ProbeResult",
    "run_readonly_probe",
]

MAX_HISTORY_WINDOW_DAYS = 7
MAX_FEE_BATCH_SIZE = 400
MAX_TOTAL_WINDOW_DAYS = 366
DEFAULT_OVERALL_TIMEOUT_SECONDS = 180.0

MARKET_TIMEZONES = {
    "US": ZoneInfo("America/New_York"),
    "HK": ZoneInfo("Asia/Hong_Kong"),
}

ORDER_EXPORT_FIELDS = (
    "order_id",
    "code",
    "stock_name",
    "order_market",
    "trd_side",
    "order_type",
    "order_status",
    "qty",
    "price",
    "create_time",
    "updated_time",
    "dealt_qty",
    "dealt_avg_price",
    "time_in_force",
    "fill_outside_rth",
    "session",
    "currency",
)

DEAL_EXPORT_FIELDS = (
    "deal_id",
    "order_id",
    "code",
    "stock_name",
    "deal_market",
    "trd_side",
    "qty",
    "price",
    "create_time",
    "status",
)

FEE_EXPORT_FIELDS = (
    "order_id",
    "fee_amount",
    "fee_details",
)

ACCOUNT_SELECTION_FIELDS = (
    "acc_id",
    "trd_env",
    "trdmarket_auth",
    "acc_status",
)


class MoomooReadonlyError(RuntimeError):
    """Safe, user-facing failure raised by the read-only probe."""


@dataclass(frozen=True)
class ProbeConfig:
    """Configuration for one isolated, read-only history query."""

    start: datetime
    end: datetime
    env: str = "LIVE"
    market: str = "US"
    host: str = "127.0.0.1"
    port: int = 11111
    acc_id: Optional[str] = None
    connect_timeout: float = 0.5
    query_timeout: float = 15.0
    retries: int = 2
    retry_delay: float = 0.25
    overall_timeout: float = DEFAULT_OVERALL_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        env = self.env.upper()
        market = self.market.upper()
        if env not in {"LIVE", "SIMULATE"}:
            raise ValueError("env must be LIVE or SIMULATE")
        if market not in MARKET_TIMEZONES:
            raise ValueError("market must be US or HK")
        zone = MARKET_TIMEZONES[market]
        start = (
            self.start.replace(tzinfo=zone)
            if self.start.tzinfo is None
            else self.start.astimezone(zone)
        )
        end = (
            self.end.replace(tzinfo=zone)
            if self.end.tzinfo is None
            else self.end.astimezone(zone)
        )
        object.__setattr__(self, "env", env)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if start >= end:
            raise ValueError("start must be earlier than end")
        if end - start > timedelta(days=MAX_TOTAL_WINDOW_DAYS):
            raise ValueError(
                f"query window cannot exceed {MAX_TOTAL_WINDOW_DAYS} days"
            )
        if not (1 <= int(self.port) <= 65535):
            raise ValueError("port must be between 1 and 65535")
        if (
            self.connect_timeout <= 0
            or self.query_timeout <= 0
            or self.overall_timeout <= 0
        ):
            raise ValueError("timeouts must be positive")
        if self.retries < 0 or self.retries > 5:
            raise ValueError("retries must be between 0 and 5")
        if self.retry_delay < 0:
            raise ValueError("retry_delay cannot be negative")
        if self.acc_id is not None and not str(self.acc_id).strip().isdigit():
            raise ValueError("acc_id must contain digits only")


@dataclass(frozen=True)
class ProbeResult:
    """De-identified summary plus an optional-file-ready record payload."""

    summary: dict[str, Any]
    export_payload: dict[str, Any]


@dataclass(frozen=True, repr=False)
class _SelectedAccount:
    sdk_acc_id: int
    selection: str


def _load_sdk():
    try:
        import moomoo  # type: ignore
    except ImportError as exc:
        raise MoomooReadonlyError(
            "Moomoo Python SDK is not installed; install the configured project dependency first"
        ) from exc
    return moomoo


def _enum_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return value.name.upper()
    text = str(value).strip().upper()
    return text.rsplit(".", 1)[-1]


def _clean_value(value: Any) -> Any:
    """Convert pandas/numpy/SDK values to deterministic JSON-safe values."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _clean_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean_value(v) for v in value]
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    return str(value)


def _records(data: Any, fields: Iterable[str]) -> list[dict[str, Any]]:
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        try:
            rows = data.to_dict(orient="records")
        except TypeError:
            rows = data.to_dict()
            if isinstance(rows, Mapping):
                rows = [rows]
    elif isinstance(data, Mapping):
        rows = [data]
    elif isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        rows = list(data)
    else:
        raise MoomooReadonlyError("Moomoo returned an unsupported response shape")

    allowed = tuple(fields)
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise MoomooReadonlyError("Moomoo returned an unsupported row shape")
        output.append({key: _clean_value(row.get(key)) for key in allowed if key in row})
    return output


def _probe_tcp(host: str, port: int, timeout: float) -> None:
    """Fail fast before constructing a synchronous SDK trade context."""
    if not probe_opend_tcp(host, port, timeout=timeout):
        raise MoomooReadonlyError(
            "OpenD is not reachable; start and sign in to OpenD, then retry"
        )


def _sdk_member(container: Any, name: str, label: str) -> Any:
    try:
        return getattr(container, name)
    except AttributeError as exc:
        raise MoomooReadonlyError(f"installed Moomoo SDK does not support {label}") from exc


def _default_context_factory(config: ProbeConfig, sdk: Any):
    suppress_moomoo_sdk_console()
    try:
        ensure_opend_ready(
            config.host,
            config.port,
            tcp_timeout=config.connect_timeout,
            ready_timeout=min(config.query_timeout, 3.0),
        )
    except MoomooRuntimeError as exc:
        raise MoomooReadonlyError(
            "OpenD did not complete the bounded SDK readiness check"
        ) from exc

    market_enum = _sdk_member(sdk.TrdMarket, config.market, config.market)
    security_firm = _sdk_member(sdk.SecurityFirm, "FUTUINC", "FUTUINC")
    ctx = sdk.OpenSecTradeContext(
        filter_trdmarket=market_enum,
        host=config.host,
        port=config.port,
        security_firm=security_firm,
    )

    # The SDK exposes a connect-timeout setter but no public request-timeout
    # setter.  Its own request loop reads this field and wakes synchronous
    # calls on expiry, so set it explicitly to keep probe calls bounded.
    if hasattr(ctx, "_query_timeout"):
        setattr(ctx, "_query_timeout", config.query_timeout)
    if hasattr(ctx, "set_sync_query_connect_timeout"):
        ctx.set_sync_query_connect_timeout(config.query_timeout)
    return ctx


def _call_with_retry(
    call: Callable[[], tuple[Any, Any]],
    *,
    ret_ok: Any,
    label: str,
    retries: int,
    retry_delay: float,
    sleeper: Callable[[float], None],
) -> Any:
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            ret, data = call()
        except Exception:  # noqa: BLE001 - SDK transport errors vary by version
            ret, data = None, None
        if ret == ret_ok:
            return data
        if attempt + 1 < attempts:
            sleeper(retry_delay * (2**attempt))
    raise MoomooReadonlyError(f"{label} failed after {attempts} bounded attempts")


def _market_authorizations(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {_enum_text(item) for item in value}
    text = str(value).upper()
    for char in "[](){}'\"":
        text = text.replace(char, " ")
    return {part.rsplit(".", 1)[-1] for part in text.replace(",", " ").split()}


def _account_matches(row: Mapping[str, Any], *, sdk_env_name: str, market: str) -> bool:
    if _enum_text(row.get("trd_env")) != sdk_env_name:
        return False
    status = _enum_text(row.get("acc_status"))
    if status and status not in {"ACTIVE", "N/A"}:
        return False
    return market in _market_authorizations(row.get("trdmarket_auth"))


def _select_account(
    rows: list[dict[str, Any]],
    *,
    requested_acc_id: Optional[str],
    sdk_env_name: str,
    market: str,
) -> _SelectedAccount:
    eligible = [
        row
        for row in rows
        if _account_matches(row, sdk_env_name=sdk_env_name, market=market)
    ]

    if requested_acc_id is not None:
        requested = str(requested_acc_id).strip()
        matches = [row for row in eligible if str(row.get("acc_id")) == requested]
        if len(matches) != 1:
            raise MoomooReadonlyError(
                "the requested account is not one active account for the selected environment and market"
            )
        selected = matches[0]
        selection = "explicit"
    else:
        if len(eligible) != 1:
            raise MoomooReadonlyError(
                "account selection is ambiguous; use --acc-id after checking the account in OpenD"
            )
        selected = eligible[0]
        selection = "unique_auto"

    try:
        sdk_acc_id = int(str(selected["acc_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MoomooReadonlyError("selected account has no valid stable account ID") from exc
    return _SelectedAccount(sdk_acc_id=sdk_acc_id, selection=selection)


def _history_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    maximum = timedelta(days=MAX_HISTORY_WINDOW_DAYS)
    while cursor < end:
        chunk_end = min(cursor + maximum, end)
        windows.append((cursor, chunk_end))
        # Adjacent Moomoo windows are inclusive.  Keeping the shared boundary
        # avoids a sub-second gap; stable IDs remove the intentional overlap.
        cursor = chunk_end
    return windows


def _stable_id(value: Any) -> str:
    cleaned = _clean_value(value)
    if cleaned is None:
        return ""
    text = str(cleaned).strip()
    return "" if text.lower() in {"", "nan", "none"} else text


def _dedupe_records(
    records: list[dict[str, Any]],
    *,
    id_field: str,
    latest_field: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    unique: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for row in records:
        stable_id = _stable_id(row.get(id_field))
        if not stable_id:
            raise MoomooReadonlyError(
                f"Moomoo returned a {id_field} row without its stable ID; export aborted"
            )
        row[id_field] = stable_id
        previous = unique.get(stable_id)
        if previous is None:
            unique[stable_id] = row
            continue
        duplicates += 1
        if latest_field and str(row.get(latest_field) or "") >= str(previous.get(latest_field) or ""):
            unique[stable_id] = row

    ordered = sorted(
        unique.values(),
        key=lambda row: (str(row.get("create_time") or ""), str(row.get(id_field) or "")),
    )
    return ordered, duplicates


def _fetch_history(
    ctx: Any,
    *,
    config: ProbeConfig,
    selected: _SelectedAccount,
    sdk: Any,
    env_enum: Any,
    sleeper: Callable[[float], None],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], int]:
    fmt = "%Y-%m-%d %H:%M:%S"
    market_enum = _sdk_member(sdk.TrdMarket, config.market, config.market)
    order_rows: list[dict[str, Any]] = []
    deal_rows: list[dict[str, Any]] = []
    windows = _history_windows(config.start, config.end)

    for chunk_start, chunk_end in windows:
        common = {
            "start": chunk_start.strftime(fmt),
            "end": chunk_end.strftime(fmt),
            "trd_env": env_enum,
            "acc_id": selected.sdk_acc_id,
        }
        data = _call_with_retry(
            lambda common=common: ctx.history_order_list_query(
                order_market=market_enum,
                **common,
            ),
            ret_ok=sdk.RET_OK,
            label="historical order query",
            retries=config.retries,
            retry_delay=config.retry_delay,
            sleeper=sleeper,
        )
        order_rows.extend(_records(data, ORDER_EXPORT_FIELDS))

        if config.env == "LIVE":
            data = _call_with_retry(
                lambda common=common: ctx.history_deal_list_query(
                    deal_market=market_enum,
                    **common,
                ),
                ret_ok=sdk.RET_OK,
                label="historical fill query",
                retries=config.retries,
                retry_delay=config.retry_delay,
                sleeper=sleeper,
            )
            deal_rows.extend(_records(data, DEAL_EXPORT_FIELDS))

    orders, order_duplicates = _dedupe_records(
        order_rows,
        id_field="order_id",
        latest_field="updated_time",
    )
    if deal_rows:
        deals, deal_duplicates = _dedupe_records(deal_rows, id_field="deal_id")
    else:
        deals, deal_duplicates = [], 0
    return (
        orders,
        deals,
        {"orders": order_duplicates, "deals": deal_duplicates},
        len(windows),
    )


def _fetch_fees(
    ctx: Any,
    *,
    order_ids: list[str],
    selected: _SelectedAccount,
    sdk: Any,
    env_enum: Any,
    config: ProbeConfig,
    sleeper: Callable[[float], None],
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    batches = 0
    for offset in range(0, len(order_ids), MAX_FEE_BATCH_SIZE):
        batch = order_ids[offset : offset + MAX_FEE_BATCH_SIZE]
        batches += 1
        data = _call_with_retry(
            lambda batch=batch: ctx.order_fee_query(
                order_id_list=batch,
                acc_id=selected.sdk_acc_id,
                trd_env=env_enum,
            ),
            ret_ok=sdk.RET_OK,
            label="order fee query",
            retries=config.retries,
            retry_delay=config.retry_delay,
            sleeper=sleeper,
        )
        rows.extend(_records(data, FEE_EXPORT_FIELDS))
    if not rows:
        return [], 0, batches
    fees, duplicates = _dedupe_records(rows, id_field="order_id")
    return fees, duplicates, batches


def _float_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _is_option_code(code: Any) -> bool:
    text = str(code or "").upper().split(".", 1)[-1]
    if not text:
        return False
    try:
        return parse_symbol(text).is_option
    except ValueError:
        return False


def _reconcile_live_records(
    *,
    filled_orders: list[dict[str, Any]],
    all_orders: list[dict[str, Any]],
    deals: list[dict[str, Any]],
    fees: list[dict[str, Any]],
) -> dict[str, int]:
    """Cross-check the order, fill and fee facts required by analysis.

    A successful SDK query is not sufficient evidence for post-trade analysis.
    The fill records must agree with their parent order on identity, instrument,
    side, quantity and weighted-average price, and every filled order must have
    a fee response (including legitimate zero-fee rows).
    """
    filled_by_id = {
        str(row.get("order_id")): row
        for row in filled_orders
        if row.get("order_id")
    }
    order_ids = {
        str(row.get("order_id"))
        for row in all_orders
        if row.get("order_id")
    }
    deals_by_order: dict[str, list[dict[str, Any]]] = {}
    for row in deals:
        order_id = str(row.get("order_id") or "")
        if order_id:
            deals_by_order.setdefault(order_id, []).append(row)

    missing_fills = set(filled_by_id) - set(deals_by_order)
    orphan_fills = set(deals_by_order) - order_ids
    quantity_mismatches = 0
    code_mismatches = 0
    side_mismatches = 0
    average_price_mismatches = 0

    for order_id, order in filled_by_id.items():
        order_deals = deals_by_order.get(order_id, [])
        if not order_deals:
            continue

        expected_code = str(order.get("code") or "").strip().upper()
        expected_side = _enum_text(order.get("trd_side"))
        if any(
            str(deal.get("code") or "").strip().upper() != expected_code
            for deal in order_deals
        ):
            code_mismatches += 1
        if any(_enum_text(deal.get("trd_side")) != expected_side for deal in order_deals):
            side_mismatches += 1

        fill_qty = sum(_float_value(deal.get("qty")) for deal in order_deals)
        expected_qty = _float_value(order.get("dealt_qty"))
        if abs(expected_qty - fill_qty) > 1e-8:
            quantity_mismatches += 1

        expected_average = _float_value(order.get("dealt_avg_price"))
        if fill_qty > 0:
            actual_average = sum(
                _float_value(deal.get("qty")) * _float_value(deal.get("price"))
                for deal in order_deals
            ) / fill_qty
            tolerance = max(1e-4, abs(expected_average) * 1e-6)
            if abs(expected_average - actual_average) > tolerance:
                average_price_mismatches += 1

    fee_order_ids = {
        str(row.get("order_id"))
        for row in fees
        if row.get("order_id")
    }
    return {
        "filled_orders_without_fills": len(missing_fills),
        "fills_without_orders": len(orphan_fills),
        "filled_quantity_mismatches": quantity_mismatches,
        "fill_code_mismatches": code_mismatches,
        "fill_side_mismatches": side_mismatches,
        "fill_average_price_mismatches": average_price_mismatches,
        "filled_orders_without_fees": len(set(filled_by_id) - fee_order_ids),
    }


def _build_payload(
    *,
    config: ProbeConfig,
    selected: _SelectedAccount,
    orders: list[dict[str, Any]],
    deals: list[dict[str, Any]],
    fees: list[dict[str, Any]],
    duplicates: dict[str, int],
    window_count: int,
    fee_batch_count: int,
) -> ProbeResult:
    filled_orders = [row for row in orders if _float_value(row.get("dealt_qty")) > 0]
    activity = deals if deals else filled_orders
    side_counts = {"buy": 0, "sell": 0, "other": 0}
    for row in activity:
        side = _enum_text(row.get("trd_side"))
        key = "buy" if "BUY" in side else "sell" if "SELL" in side else "other"
        side_counts[key] += 1

    order_currency = {
        str(row.get("order_id")): str(row.get("currency") or "UNKNOWN")
        for row in orders
    }
    fee_totals: dict[str, float] = {}
    for row in fees:
        currency = order_currency.get(str(row.get("order_id")), "UNKNOWN")
        fee_totals[currency] = fee_totals.get(currency, 0.0) + _float_value(row.get("fee_amount"))
    fee_totals = {key: round(value, 6) for key, value in sorted(fee_totals.items())}

    if config.env == "LIVE":
        reconciliation: dict[str, Optional[int]] = _reconcile_live_records(
            filled_orders=filled_orders,
            all_orders=orders,
            deals=deals,
            fees=fees,
        )
        reconciliation_failures = {
            key: value
            for key, value in reconciliation.items()
            if value not in (None, 0)
        }
        analysis_ready = bool(filled_orders) and not reconciliation_failures
        reconciliation_status = "passed" if not reconciliation_failures else "failed"
        warnings = [
            f"{key}={value}"
            for key, value in sorted(reconciliation_failures.items())
        ]
        if not filled_orders:
            warnings.append("no_filled_orders_in_window")
    else:
        reconciliation = {
            "filled_orders_without_fills": None,
            "fills_without_orders": None,
            "filled_quantity_mismatches": None,
            "fill_code_mismatches": None,
            "fill_side_mismatches": None,
            "fill_average_price_mismatches": None,
            "filled_orders_without_fees": None,
        }
        analysis_ready = False
        reconciliation_status = "not_applicable"
        warnings = ["simulate_history_has_no_live_fill_or_fee_reconciliation"]

    codes = {str(row.get("code")) for row in activity if row.get("code")}
    times = sorted(str(row.get("create_time")) for row in activity if row.get("create_time"))
    query_window = {
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
        "timezone": str(MARKET_TIMEZONES[config.market]),
        "chunks": window_count,
        "max_chunk_days": MAX_HISTORY_WINDOW_DAYS,
    }
    summary = {
        # ``ok`` means the bounded retrieval completed.  Consumers must use
        # ``analysis_ready`` before drawing post-trade conclusions.
        "ok": True,
        "analysis_ready": analysis_ready,
        "reconciliation_status": reconciliation_status,
        "warnings": warnings,
        "mode": "moomoo_readonly_probe",
        "journal_database_written": False,
        "environment": config.env,
        "market": config.market,
        "account_selection": selected.selection,
        "window": query_window,
        "counts": {
            "orders": len(orders),
            "filled_orders": len(filled_orders),
            "fills": len(deals),
            "fees": len(fees),
            "unique_instruments": len(codes),
            "option_activity_rows": sum(1 for row in activity if _is_option_code(row.get("code"))),
        },
        "activity_sides": side_counts,
        "fee_totals_by_currency": fee_totals,
        "activity_time_range": {
            "first": times[0] if times else None,
            "last": times[-1] if times else None,
        },
        "deduplicated_rows": {
            "orders": duplicates.get("orders", 0),
            "fills": duplicates.get("deals", 0),
            "fees": duplicates.get("fees", 0),
        },
        "reconciliation": reconciliation,
        "fee_batches": fee_batch_count,
    }

    export_payload = {
        "schema": "dsa.moomoo.readonly-export.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "journal_database_written": False,
        "account": {
            "environment": config.env,
            "market": config.market,
            "selection": selected.selection,
        },
        "window": query_window,
        "summary": summary,
        "records": {
            "orders": orders,
            "deals": deals,
            "fees": fees,
        },
    }
    return ProbeResult(summary=summary, export_payload=export_payload)


def _run_readonly_probe_in_process(
    config: ProbeConfig,
    *,
    sdk: Any,
    context_factory: Optional[Callable[[ProbeConfig, Any], Any]],
    tcp_probe: Callable[[str, int, float], None],
    sleeper: Callable[[float], None],
) -> ProbeResult:
    """Execute the query in the current process (dependency-injection seam)."""
    sdk = sdk or _load_sdk()
    sdk_env_name = "REAL" if config.env == "LIVE" else "SIMULATE"
    env_enum = _sdk_member(sdk.TrdEnv, sdk_env_name, sdk_env_name)

    tcp_probe(config.host, config.port, config.connect_timeout)
    factory = context_factory or _default_context_factory
    ctx = factory(config, sdk)
    try:
        account_data = _call_with_retry(
            ctx.get_acc_list,
            ret_ok=sdk.RET_OK,
            label="account list query",
            retries=config.retries,
            retry_delay=config.retry_delay,
            sleeper=sleeper,
        )
        accounts = _records(account_data, ACCOUNT_SELECTION_FIELDS)
        selected = _select_account(
            accounts,
            requested_acc_id=config.acc_id,
            sdk_env_name=sdk_env_name,
            market=config.market,
        )
        orders, deals, duplicates, window_count = _fetch_history(
            ctx,
            config=config,
            selected=selected,
            sdk=sdk,
            env_enum=env_enum,
            sleeper=sleeper,
        )
        if config.env == "LIVE":
            fees, fee_duplicates, fee_batch_count = _fetch_fees(
                ctx,
                order_ids=[
                    str(row["order_id"])
                    for row in orders
                    if _float_value(row.get("dealt_qty")) > 0
                ],
                selected=selected,
                sdk=sdk,
                env_enum=env_enum,
                config=config,
                sleeper=sleeper,
            )
        else:
            fees, fee_duplicates, fee_batch_count = [], 0, 0
        duplicates["fees"] = fee_duplicates
        return _build_payload(
            config=config,
            selected=selected,
            orders=orders,
            deals=deals,
            fees=fees,
            duplicates=duplicates,
            window_count=window_count,
            fee_batch_count=fee_batch_count,
        )
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001 - shutdown must not mask query result
            pass


def _probe_process_worker(config: ProbeConfig, sender: Any) -> None:
    """Child-process entrypoint containing every synchronous trade SDK call."""
    try:
        result = _run_readonly_probe_in_process(
            config,
            sdk=None,
            context_factory=None,
            tcp_probe=_probe_tcp,
            sleeper=time.sleep,
        )
        sender.send(("ok", result.summary, result.export_payload))
    except MoomooReadonlyError as exc:
        sender.send(("error", str(exc)))
    except BaseException:  # noqa: BLE001 - never expose raw SDK/account payloads
        sender.send(("error", "unexpected read-only worker failure"))
    finally:
        try:
            sender.close()
        except Exception:  # noqa: BLE001 - child is already terminating
            pass


def _stop_probe_process(process: Any, *, grace_seconds: float = 1.0) -> None:
    """Reap a probe worker, forcefully if an SDK constructor is stuck."""
    process.join(timeout=max(0.0, grace_seconds))
    if process.is_alive():
        process.terminate()
        process.join(timeout=max(0.0, grace_seconds))
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=max(0.0, grace_seconds))


def _run_readonly_probe_bounded(config: ProbeConfig) -> ProbeResult:
    """Run the synchronous SDK boundary in a terminable child process."""
    process_context = multiprocessing.get_context("spawn")
    receiver, sender = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_probe_process_worker,
        args=(config, sender),
        name="dsa-moomoo-readonly",
        daemon=True,
    )
    try:
        process.start()
    except Exception as exc:  # noqa: BLE001 - normalize platform start failures
        receiver.close()
        sender.close()
        raise MoomooReadonlyError("could not start bounded read-only worker") from exc
    sender.close()

    message: Optional[tuple[Any, ...]] = None
    deadline = time.monotonic() + config.overall_timeout
    try:
        while message is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MoomooReadonlyError(
                    "read-only probe exceeded its overall timeout and was stopped"
                )
            if receiver.poll(min(0.1, remaining)):
                try:
                    message = receiver.recv()
                except EOFError as exc:
                    raise MoomooReadonlyError(
                        "read-only worker exited without a result"
                    ) from exc
                break
            if not process.is_alive():
                if receiver.poll(0):
                    continue
                raise MoomooReadonlyError("read-only worker exited without a result")
    finally:
        receiver.close()
        _stop_probe_process(process)

    if not message or message[0] != "ok":
        error = str(message[1]) if message and len(message) > 1 else "read-only probe failed"
        raise MoomooReadonlyError(error)
    return ProbeResult(summary=message[1], export_payload=message[2])


def run_readonly_probe(
    config: ProbeConfig,
    *,
    sdk: Any = None,
    context_factory: Optional[Callable[[ProbeConfig, Any], Any]] = None,
    tcp_probe: Optional[Callable[[str, int, float], None]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
) -> ProbeResult:
    """Query Moomoo without persistence and with a hard process deadline.

    Production calls use a spawned worker because ``OpenSecTradeContext`` has
    no asynchronous constructor and may retry forever if OpenD disappears in a
    narrow preflight race.  Dependency-injected test calls remain in-process.
    """
    if any(value is not None for value in (sdk, context_factory, tcp_probe, sleeper)):
        return _run_readonly_probe_in_process(
            config,
            sdk=sdk,
            context_factory=context_factory,
            tcp_probe=tcp_probe or _probe_tcp,
            sleeper=sleeper or time.sleep,
        )
    return _run_readonly_probe_bounded(config)

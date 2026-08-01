# -*- coding: utf-8 -*-
"""Strictly read-only acquisition of current Moomoo US option positions.

This module intentionally keeps current broker state separate from Journal
trade-history evidence.  A successful result is a locally bracketed current
snapshot; it is never evidence of a historical opening position and it does
not write to any project database.

Production SDK calls run in a terminable child process.  The position list is
requested twice from the Moomoo server and compared only on boundary facts
(instrument, side and signed contract quantity).  Volatile valuation fields
are deliberately absent, while broker cost fields are retained only as
display context and must not be interpreted as realized P&L.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import multiprocessing
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Optional

from src.journal.brokers.moomoo_readonly import (
    ACCOUNT_SELECTION_FIELDS,
    MoomooReadonlyError,
    _call_with_retry,
    _default_context_factory,
    _enum_text,
    _fetch_contract_specs,
    _load_sdk,
    _probe_tcp,
    _records,
    _sdk_member,
    _select_account,
    _stop_probe_process,
)
from src.options.occ_parser import parse_symbol

__all__ = [
    "MoomooPositionSnapshotError",
    "PositionSnapshotConfig",
    "PositionSnapshotResult",
    "run_position_snapshot_probe",
]


POSITION_SNAPSHOT_SCHEMA = "dsa.moomoo.position-snapshot.v1"
DEFAULT_OVERALL_TIMEOUT_SECONDS = 60.0
_ACCOUNT_BINDING_DOMAIN = "dsa-journal-account-binding-v1"
_COST_CONTEXT_SEMANTICS = "broker_display_only_not_realized_pnl"
_TIME_SEMANTICS = (
    "locally_bracketed_acquisition_interval; broker_as_of_not_provided"
)

POSITION_EXPORT_FIELDS = (
    "position_side",
    "code",
    "position_market",
    "qty",
    "can_sell_qty",
    "currency",
    "cost_price",
    "cost_price_valid",
    "average_cost",
    "diluted_cost",
)

_MISSING_DECIMAL_TEXT = frozenset({"", "N/A", "NA", "NAN", "NONE", "--"})


class MoomooPositionSnapshotError(MoomooReadonlyError):
    """Safe failure at the current-position read-only boundary."""


@dataclass(frozen=True)
class PositionSnapshotConfig:
    """Configuration for one current LIVE US option-position acquisition."""

    account_binding_secret: str = field(repr=False)
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
        env = str(self.env).strip().upper()
        market = str(self.market).strip().upper()
        if env != "LIVE":
            raise ValueError("current position snapshots require env=LIVE")
        if market != "US":
            raise ValueError("current position snapshots require market=US")
        object.__setattr__(self, "env", env)
        object.__setattr__(self, "market", market)

        secret = str(self.account_binding_secret or "").strip()
        if len(secret) < 32:
            raise ValueError(
                "account_binding_secret must contain at least 32 characters"
            )
        object.__setattr__(self, "account_binding_secret", secret)

        if not (1 <= int(self.port) <= 65535):
            raise ValueError("port must be between 1 and 65535")
        if self.acc_id is not None and not str(self.acc_id).strip().isdigit():
            raise ValueError("acc_id must contain digits only")
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


@dataclass(frozen=True)
class PositionSnapshotResult:
    """De-identified summary and deterministic current-position payload."""

    summary: dict[str, Any]
    export_payload: dict[str, Any]


@dataclass(frozen=True)
class _NormalizedPositionRead:
    positions_by_symbol: Mapping[str, Mapping[str, Any]]
    boundary_by_symbol: Mapping[str, tuple[str, Decimal]]
    total_rows: int
    filtered_non_us_rows: int
    filtered_non_option_rows: int
    filtered_zero_quantity_rows: int
    issues: tuple[str, ...]
    context_warnings: tuple[str, ...]


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _required_decimal(
    value: Any,
    *,
    strictly_positive: bool,
    allow_negative: bool = False,
) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("missing decimal")
    text = str(value).strip()
    if text.upper() in _MISSING_DECIMAL_TEXT:
        raise ValueError("missing decimal")
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    if not parsed.is_finite():
        raise ValueError("non-finite decimal")
    if strictly_positive and parsed <= 0:
        raise ValueError("decimal must be positive")
    if not strictly_positive and not allow_negative and parsed < 0:
        raise ValueError("decimal cannot be negative")
    return parsed


def _optional_decimal(
    value: Any,
    *,
    allow_negative: bool = False,
) -> Optional[Decimal]:
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in _MISSING_DECIMAL_TEXT:
        return None
    return _required_decimal(
        value,
        strictly_positive=False,
        allow_negative=allow_negative,
    )


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in _MISSING_DECIMAL_TEXT:
        return None
    if text in {"TRUE", "1"}:
        return True
    if text in {"FALSE", "0"}:
        return False
    raise ValueError("invalid boolean")


def _clock_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MoomooPositionSnapshotError(
            "position snapshot clock must return an aware datetime"
        )
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _account_binding(config: PositionSnapshotConfig, sdk_acc_id: int) -> str:
    message = (
        f"{_ACCOUNT_BINDING_DOMAIN}|{config.env}|{config.market}|{sdk_acc_id}"
    ).encode("utf-8")
    return hmac.new(
        config.account_binding_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def _read_positions(
    ctx: Any,
    *,
    config: PositionSnapshotConfig,
    selected: Any,
    sdk: Any,
    env_enum: Any,
    market_enum: Any,
    sleeper: Callable[[float], None],
    label: str,
) -> list[dict[str, Any]]:
    data = _call_with_retry(
        lambda: ctx.position_list_query(
            position_market=market_enum,
            trd_env=env_enum,
            acc_id=selected.sdk_acc_id,
            refresh_cache=True,
            show_option_strategy_view=False,
        ),
        ret_ok=sdk.RET_OK,
        label=label,
        retries=config.retries,
        retry_delay=config.retry_delay,
        sleeper=sleeper,
    )
    return _records(data, POSITION_EXPORT_FIELDS)


def _position_issue(label: str, code: str, issue: str) -> str:
    safe_code = code if code else "<missing-symbol>"
    return f"{label}:{issue}:{safe_code}"


def _normalize_position_read(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> _NormalizedPositionRead:
    positions: dict[str, dict[str, Any]] = {}
    option_row_counts: dict[str, int] = {}
    issues: set[str] = set()
    context_warnings: set[str] = set()
    filtered_non_us = 0
    filtered_non_option = 0
    filtered_zero_quantity = 0

    for row in rows:
        code = str(row.get("code") or "").strip().upper()
        row_market = _enum_text(row.get("position_market"))
        if row_market != "US" or not code.startswith("US."):
            filtered_non_us += 1
            continue

        raw_symbol = code.split(".", 1)[1]
        try:
            instrument = parse_symbol(raw_symbol)
        except ValueError:
            issues.add(_position_issue(label, code, "unrecognized_us_instrument"))
            continue
        if not instrument.is_option or instrument.option is None:
            filtered_non_option += 1
            continue

        try:
            quantity = _required_decimal(
                row.get("qty"),
                strictly_positive=False,
            )
        except ValueError:
            issues.add(_position_issue(label, code, "invalid_contract_quantity"))
            continue
        if quantity == 0:
            filtered_zero_quantity += 1
            continue

        option_row_counts[code] = option_row_counts.get(code, 0) + 1
        side = _enum_text(row.get("position_side"))
        if side not in {"LONG", "SHORT"}:
            issues.add(_position_issue(label, code, "unknown_position_side"))
            continue

        currency = _enum_text(row.get("currency"))
        if currency != "USD":
            issues.add(_position_issue(label, code, "unsupported_currency"))
            continue

        optional_decimals: dict[str, Optional[Decimal]] = {}
        for field_name in (
            "can_sell_qty",
            "cost_price",
            "average_cost",
            "diluted_cost",
        ):
            try:
                optional_decimals[field_name] = _optional_decimal(
                    row.get(field_name),
                    allow_negative=field_name != "can_sell_qty",
                )
            except ValueError:
                optional_decimals[field_name] = None
                context_warnings.add(
                    _position_issue(label, code, f"invalid_{field_name}")
                )
        try:
            cost_price_valid = _optional_bool(row.get("cost_price_valid"))
        except ValueError:
            cost_price_valid = None
            context_warnings.add(
                _position_issue(label, code, "invalid_cost_price_valid")
            )

        signed_quantity = quantity if side == "LONG" else -quantity
        option = instrument.option
        strike = Decimal(str(option.strike))
        positions[code] = {
            "symbol": code,
            "market": "US",
            "asset_type": "option",
            "underlying": option.underlying,
            "expiry": option.expiry.isoformat(),
            "strike": _decimal_text(strike),
            "option_right": option.right,
            "position_side": side,
            "quantity_contracts": _decimal_text(quantity),
            "signed_quantity_contracts": _decimal_text(signed_quantity),
            "can_sell_quantity_contracts": (
                None
                if optional_decimals["can_sell_qty"] is None
                else _decimal_text(optional_decimals["can_sell_qty"])
            ),
            "currency": currency,
            "broker_cost_context": {
                "cost_price": (
                    None
                    if optional_decimals["cost_price"] is None
                    else _decimal_text(optional_decimals["cost_price"])
                ),
                "cost_price_valid": cost_price_valid,
                "average_cost": (
                    None
                    if optional_decimals["average_cost"] is None
                    else _decimal_text(optional_decimals["average_cost"])
                ),
                "diluted_cost": (
                    None
                    if optional_decimals["diluted_cost"] is None
                    else _decimal_text(optional_decimals["diluted_cost"])
                ),
                "semantics": _COST_CONTEXT_SEMANTICS,
            },
        }

    duplicate_symbols = {
        code for code, count in option_row_counts.items() if count > 1
    }
    for code in duplicate_symbols:
        positions.pop(code, None)
        issues.add(_position_issue(label, code, "duplicate_instrument"))

    ordered_positions = {
        code: positions[code]
        for code in sorted(positions)
    }
    boundary = {
        code: (
            str(ordered_positions[code]["position_side"]),
            Decimal(str(ordered_positions[code]["signed_quantity_contracts"])),
        )
        for code in ordered_positions
    }
    return _NormalizedPositionRead(
        positions_by_symbol=ordered_positions,
        boundary_by_symbol=boundary,
        total_rows=len(rows),
        filtered_non_us_rows=filtered_non_us,
        filtered_non_option_rows=filtered_non_option,
        filtered_zero_quantity_rows=filtered_zero_quantity,
        issues=tuple(sorted(issues)),
        context_warnings=tuple(sorted(context_warnings)),
    )


def _boundary_payload(
    boundary: Mapping[str, tuple[str, Decimal]],
) -> list[dict[str, str]]:
    return [
        {
            "symbol": symbol,
            "position_side": boundary[symbol][0],
            "signed_quantity_contracts": _decimal_text(boundary[symbol][1]),
        }
        for symbol in sorted(boundary)
    ]


def _stability_details(
    first: Mapping[str, tuple[str, Decimal]],
    second: Mapping[str, tuple[str, Decimal]],
) -> dict[str, Any]:
    first_symbols = set(first)
    second_symbols = set(second)
    changed = sorted(
        symbol
        for symbol in first_symbols & second_symbols
        if first[symbol] != second[symbol]
    )
    added = sorted(second_symbols - first_symbols)
    removed = sorted(first_symbols - second_symbols)
    return {
        "stable": not (added or removed or changed),
        "comparison_basis": "instrument_position_side_signed_quantity_contracts",
        "added_symbols": added,
        "removed_symbols": removed,
        "changed_symbols": changed,
    }


def _normalize_contract_specs(
    raw_specs: list[dict[str, Any]],
    *,
    expected_codes: tuple[str, ...],
    acquisition_status: str,
) -> tuple[dict[str, dict[str, Any]], str, tuple[str, ...]]:
    expected = set(expected_codes)
    specs: dict[str, dict[str, Any]] = {}
    invalid_codes: set[str] = set()

    for row in raw_specs:
        code = str(row.get("code") or "").strip().upper()
        if code not in expected:
            continue
        try:
            lot_size = _required_decimal(
                row.get("lot_size"), strictly_positive=True
            )
            contract_size = _required_decimal(
                row.get("option_contract_size"), strictly_positive=True
            )
            multiplier = _required_decimal(
                row.get("option_contract_multiplier"), strictly_positive=True
            )
        except ValueError:
            invalid_codes.add(code)
            continue
        if len({lot_size, contract_size, multiplier}) != 1:
            invalid_codes.add(code)
            continue

        payload = {
            "symbol": code,
            "lot_size": _decimal_text(lot_size),
            "option_contract_size": _decimal_text(contract_size),
            "option_contract_multiplier": _decimal_text(multiplier),
            "resolved_multiplier": _decimal_text(multiplier),
            "multiplier_basis": "moomoo_market_snapshot_three_field_consensus",
        }
        payload["source_record_sha256"] = _sha256_json(payload)
        specs[code] = payload

    missing = expected - set(specs)
    issues = tuple(
        f"missing_or_invalid_contract_spec:{code}"
        for code in sorted(missing | invalid_codes)
    )
    if not expected:
        status = "not_applicable"
    elif not missing and not invalid_codes and acquisition_status == "complete":
        status = "complete"
    elif acquisition_status == "unavailable" and not specs:
        status = "unavailable"
    else:
        status = "partial"
    return ({code: specs[code] for code in sorted(specs)}, status, issues)


def _read_metadata(
    normalized: _NormalizedPositionRead,
    *,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    boundary = _boundary_payload(normalized.boundary_by_symbol)
    return {
        "started_at": _iso_utc(started_at),
        "completed_at": _iso_utc(completed_at),
        # Keep the normalized comparison rows, not only a caller-supplied
        # digest.  Persistence re-hashes these rows and derives the diff again
        # before it can accept ``stable=True``.
        "boundary_rows": boundary,
        "boundary_sha256": _sha256_json(boundary),
        "total_rows": normalized.total_rows,
        "recognized_option_positions": len(normalized.positions_by_symbol),
        "filtered_non_us_rows": normalized.filtered_non_us_rows,
        "filtered_non_option_rows": normalized.filtered_non_option_rows,
        "filtered_zero_quantity_rows": normalized.filtered_zero_quantity_rows,
    }


def _validate_time_order(values: tuple[datetime, ...]) -> None:
    if any(current < previous for previous, current in zip(values, values[1:])):
        raise MoomooPositionSnapshotError(
            "position snapshot acquisition clock moved backwards"
        )


def _run_position_snapshot_probe_in_process(
    config: PositionSnapshotConfig,
    *,
    sdk: Any,
    context_factory: Optional[Callable[[PositionSnapshotConfig, Any], Any]],
    quote_context_factory: Optional[
        Callable[[PositionSnapshotConfig, Any], Any]
    ],
    tcp_probe: Callable[[str, int, float], None],
    sleeper: Callable[[float], None],
    clock: Callable[[], datetime],
) -> PositionSnapshotResult:
    requested_at = _clock_utc(clock)
    sdk = sdk or _load_sdk()
    env_enum = _sdk_member(sdk.TrdEnv, "REAL", "REAL")
    market_enum = _sdk_member(sdk.TrdMarket, "US", "US")

    tcp_probe(config.host, config.port, config.connect_timeout)
    factory = context_factory or _default_context_factory
    ctx: Any = factory(config, sdk)
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
            sdk_env_name="REAL",
            market="US",
        )

        first_started_at = _clock_utc(clock)
        first_rows = _read_positions(
            ctx,
            config=config,
            selected=selected,
            sdk=sdk,
            env_enum=env_enum,
            market_enum=market_enum,
            sleeper=sleeper,
            label="first current position query",
        )
        first_completed_at = _clock_utc(clock)

        second_started_at = _clock_utc(clock)
        second_rows = _read_positions(
            ctx,
            config=config,
            selected=selected,
            sdk=sdk,
            env_enum=env_enum,
            market_enum=market_enum,
            sleeper=sleeper,
            label="second current position query",
        )
        second_completed_at = _clock_utc(clock)
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001 - shutdown must not mask query result
            pass

    first = _normalize_position_read(first_rows, label="first_read")
    second = _normalize_position_read(second_rows, label="second_read")
    stability = _stability_details(
        first.boundary_by_symbol,
        second.boundary_by_symbol,
    )

    option_codes = tuple(sorted(second.positions_by_symbol))
    raw_specs, raw_spec_status = _fetch_contract_specs(
        config=config,
        sdk=sdk,
        option_codes=option_codes,
        quote_context_factory=quote_context_factory,
        sleeper=sleeper,
    )
    specs_by_symbol, contract_spec_status, spec_issues = _normalize_contract_specs(
        raw_specs,
        expected_codes=option_codes,
        acquisition_status=raw_spec_status,
    )
    completed_at = _clock_utc(clock)
    _validate_time_order(
        (
            requested_at,
            first_started_at,
            first_completed_at,
            second_started_at,
            second_completed_at,
            completed_at,
        )
    )

    validation_issues = tuple(
        sorted(set(first.issues) | set(second.issues) | set(spec_issues))
    )
    context_warnings = tuple(
        sorted(set(first.context_warnings) | set(second.context_warnings))
    )
    warnings = list(context_warnings)
    if not stability["stable"]:
        warnings.append("position_boundary_changed_between_reads")
    warnings.extend(validation_issues)
    warnings = sorted(set(warnings))

    positions: list[dict[str, Any]] = []
    for symbol in sorted(second.positions_by_symbol):
        row = dict(second.positions_by_symbol[symbol])
        spec = specs_by_symbol.get(symbol)
        row["contract_multiplier"] = (
            None if spec is None else spec["resolved_multiplier"]
        )
        row["contract_multiplier_basis"] = (
            "unavailable" if spec is None else spec["multiplier_basis"]
        )
        row["contract_spec_source_record_sha256"] = (
            None if spec is None else spec["source_record_sha256"]
        )
        positions.append(row)

    binding = _account_binding(config, selected.sdk_acc_id)
    analysis_ready = bool(
        stability["stable"]
        and not validation_issues
        and contract_spec_status in {"complete", "not_applicable"}
    )
    positions_sha256 = _sha256_json(positions)
    contract_specs = [specs_by_symbol[code] for code in sorted(specs_by_symbol)]
    snapshot_sha256 = _sha256_json(
        {
            "schema": POSITION_SNAPSHOT_SCHEMA,
            "account_binding": binding,
            "stability": stability,
            "positions": positions,
            "contract_specs": contract_specs,
        }
    )

    acquisition = {
        "requested_at": _iso_utc(requested_at),
        "first_read": _read_metadata(
            first,
            started_at=first_started_at,
            completed_at=first_completed_at,
        ),
        "second_read": _read_metadata(
            second,
            started_at=second_started_at,
            completed_at=second_completed_at,
        ),
        "completed_at": _iso_utc(completed_at),
        "broker_as_of": None,
        "time_semantics": _TIME_SEMANTICS,
        "stability": stability,
    }
    summary = {
        "ok": True,
        "retrieval_complete": True,
        "analysis_ready": analysis_ready,
        "position_snapshot_complete": analysis_ready,
        "has_positions": bool(positions),
        "stable": bool(stability["stable"]),
        "mode": "moomoo_position_snapshot_probe",
        "journal_database_written": False,
        "trading_actions_performed": False,
        "environment": "LIVE",
        "market": "US",
        "asset_type": "option",
        "account_selection": selected.selection,
        "contract_spec_status": contract_spec_status,
        "counts": {
            "positions": len(positions),
            "contract_specs": len(contract_specs),
            "first_total_rows": first.total_rows,
            "second_total_rows": second.total_rows,
            "first_recognized_option_positions": len(
                first.positions_by_symbol
            ),
            "second_recognized_option_positions": len(
                second.positions_by_symbol
            ),
            "first_filtered_non_us_rows": first.filtered_non_us_rows,
            "first_filtered_non_option_rows": (
                first.filtered_non_option_rows
            ),
            "first_filtered_zero_quantity_rows": (
                first.filtered_zero_quantity_rows
            ),
            "second_filtered_zero_quantity_rows": (
                second.filtered_zero_quantity_rows
            ),
            "second_filtered_non_us_rows": second.filtered_non_us_rows,
            "second_filtered_non_option_rows": second.filtered_non_option_rows,
            "validation_issues": len(validation_issues),
        },
        "validation_issues": list(validation_issues),
        "warnings": warnings,
        "positions_sha256": positions_sha256,
        "snapshot_sha256": snapshot_sha256,
    }
    export_payload = {
        "schema": POSITION_SNAPSHOT_SCHEMA,
        "generated_at": _iso_utc(completed_at),
        "mode": "read_only",
        "journal_database_written": False,
        "trading_actions_performed": False,
        "account": {
            "environment": "LIVE",
            "market": "US",
            "selection": selected.selection,
            "binding": binding,
        },
        "acquisition": acquisition,
        "summary": summary,
        "records": {
            "positions": positions,
            "contract_specs": contract_specs,
        },
    }
    return PositionSnapshotResult(summary=summary, export_payload=export_payload)


def _position_snapshot_process_worker(
    config: PositionSnapshotConfig,
    sender: Any,
) -> None:
    try:
        result = _run_position_snapshot_probe_in_process(
            config,
            sdk=None,
            context_factory=None,
            quote_context_factory=None,
            tcp_probe=_probe_tcp,
            sleeper=time.sleep,
            clock=lambda: datetime.now(timezone.utc),
        )
        sender.send(("ok", result.summary, result.export_payload))
    except MoomooReadonlyError as exc:
        sender.send(("error", str(exc)))
    except BaseException:  # noqa: BLE001 - never expose SDK/account payloads
        sender.send(("error", "unexpected current-position worker failure"))
    finally:
        try:
            sender.close()
        except Exception:  # noqa: BLE001 - child is already terminating
            pass


def _run_position_snapshot_probe_bounded(
    config: PositionSnapshotConfig,
) -> PositionSnapshotResult:
    process_context = multiprocessing.get_context("spawn")
    receiver, sender = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_position_snapshot_process_worker,
        args=(config, sender),
        name="dsa-moomoo-position-snapshot",
        daemon=True,
    )
    try:
        process.start()
    except Exception as exc:  # noqa: BLE001 - normalize platform start failures
        receiver.close()
        sender.close()
        raise MoomooPositionSnapshotError(
            "could not start bounded current-position worker"
        ) from exc
    sender.close()

    message: Optional[tuple[Any, ...]] = None
    deadline = time.monotonic() + config.overall_timeout
    try:
        while message is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MoomooPositionSnapshotError(
                    "current-position probe exceeded its overall timeout and was stopped"
                )
            if receiver.poll(min(0.1, remaining)):
                try:
                    message = receiver.recv()
                except EOFError as exc:
                    raise MoomooPositionSnapshotError(
                        "current-position worker exited without a result"
                    ) from exc
                break
            if not process.is_alive():
                if receiver.poll(0):
                    continue
                raise MoomooPositionSnapshotError(
                    "current-position worker exited without a result"
                )
    finally:
        receiver.close()
        _stop_probe_process(process)

    if not message or message[0] != "ok":
        error = (
            str(message[1])
            if message and len(message) > 1
            else "current-position probe failed"
        )
        raise MoomooPositionSnapshotError(error)
    return PositionSnapshotResult(
        summary=message[1],
        export_payload=message[2],
    )


def run_position_snapshot_probe(
    config: PositionSnapshotConfig,
    *,
    sdk: Any = None,
    context_factory: Optional[Callable[[PositionSnapshotConfig, Any], Any]] = None,
    quote_context_factory: Optional[
        Callable[[PositionSnapshotConfig, Any], Any]
    ] = None,
    tcp_probe: Optional[Callable[[str, int, float], None]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> PositionSnapshotResult:
    """Acquire a de-identified current snapshot without DB or trade actions.

    Production calls run in a spawned worker with a hard deadline.  Supplying
    any dependency-injection seam keeps the call in-process for deterministic
    tests.  The result describes the acquisition interval only; it never
    claims an exact broker ``as_of`` timestamp or a historical opening state.
    """
    if any(
        value is not None
        for value in (
            sdk,
            context_factory,
            quote_context_factory,
            tcp_probe,
            sleeper,
            clock,
        )
    ):
        try:
            return _run_position_snapshot_probe_in_process(
                config,
                sdk=sdk,
                context_factory=context_factory,
                quote_context_factory=quote_context_factory,
                tcp_probe=tcp_probe or _probe_tcp,
                sleeper=sleeper or time.sleep,
                clock=clock or (lambda: datetime.now(timezone.utc)),
            )
        except MoomooPositionSnapshotError:
            raise
        except MoomooReadonlyError as exc:
            raise MoomooPositionSnapshotError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - keep raw SDK payloads private
            raise MoomooPositionSnapshotError(
                "unexpected current-position probe failure"
            ) from exc
    return _run_position_snapshot_probe_bounded(config)

# -*- coding: utf-8 -*-
"""Moomoo US account CSV parser.

Moomoo exports a CSV with interleaved main rows + fill-only rows:
- Main row: ``Side``, ``Symbol``, ``Order Qty`` populated; represents one order
- Fill-only rows: ``Side`` and ``Symbol`` empty; just ``Fill Qty / Fill Price /
  Fill Time`` — these are additional fills for the *previous* main row.

Time format: ``Apr 16, 2026 15:53:57 ET``. Option symbols are OCC variable-
strike (see :mod:`src.options.occ_parser`).

Reference: New-docs/05_JOURNAL_MODULE.md §2.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from src.options.occ_parser import InstrumentInfo, parse_symbol

__all__ = ["MoomooOrder", "parse", "compute_external_id"]

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class MoomooOrder:
    """One filled Moomoo order (main row + all fill-only rows merged)."""

    # Core order fields
    side: str  # 'Buy' / 'Sell'
    symbol: str
    name: str
    order_qty: int
    order_price: float
    order_amount: float
    status: str
    order_time: Optional[datetime]
    order_type: str
    session: str

    # Fees
    commission: float = 0.0
    platform_fee: float = 0.0
    trading_activity_fee: float = 0.0
    options_regulatory_fee: float = 0.0
    occ_fee: float = 0.0
    contract_fee: float = 0.0
    sec_fee: float = 0.0
    settlement_fee: float = 0.0
    total_fee: float = 0.0
    currency: str = "USD"

    # Fills
    fills: list[dict] = field(default_factory=list)

    # Derived
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    first_fill_time: Optional[datetime] = None
    last_fill_time: Optional[datetime] = None
    instrument: Optional[InstrumentInfo] = None
    raw_rows: list[dict] = field(default_factory=list)


_TIME_FORMATS = [
    "%b %d, %Y %H:%M:%S",
    "%b %d, %Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
]


def _parse_trade_time(s: Optional[str]) -> Optional[datetime]:
    """Parse a Moomoo timestamp to a timezone-aware datetime (America/New_York).

    Returns ``None`` for empty / unparseable input.
    """
    if not s:
        return None
    text = s.strip()
    # Strip trailing " ET" / " UTC" etc. before parsing.
    for suffix in (" ET", " EDT", " EST"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    for fmt in _TIME_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=ET)
        except ValueError:
            continue
    return None


def _float_or_zero(v) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def _int_or_zero(v) -> int:
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def _first(row: dict, *keys: str) -> Optional[str]:
    """Return first non-empty value among column aliases."""
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def parse(content: bytes) -> list[MoomooOrder]:
    """Parse Moomoo CSV bytes into a list of filled orders."""
    text = content.decode("utf-8-sig")  # strip BOM if present
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    orders: list[MoomooOrder] = []
    current: Optional[MoomooOrder] = None

    for row in rows:
        side_raw = (_first(row, "Side") or "").strip()
        symbol_raw = (_first(row, "Symbol") or "").strip()
        is_main_row = bool(side_raw and symbol_raw)

        if is_main_row:
            if current:
                orders.append(current)
            current = MoomooOrder(
                side=side_raw,
                symbol=symbol_raw,
                name=(_first(row, "Name") or "").strip(),
                order_qty=_int_or_zero(_first(row, "Order Qty", "Qty")),
                order_price=_float_or_zero(_first(row, "Order Price", "Price")),
                order_amount=_float_or_zero(_first(row, "Order Amount", "Amount")),
                status=(_first(row, "Status") or "").strip(),
                order_time=_parse_trade_time(_first(row, "Order Time")),
                order_type=(_first(row, "Order Type") or "").strip(),
                session=(_first(row, "Session") or "").strip(),
                commission=_float_or_zero(_first(row, "Commission", "Comm")),
                platform_fee=_float_or_zero(_first(row, "Platform Fees", "Platform Fee")),
                trading_activity_fee=_float_or_zero(
                    _first(row, "Trading Activity Fees", "Trading Activity Fee")
                ),
                options_regulatory_fee=_float_or_zero(
                    _first(row, "Options Regulatory Fees", "ORF")
                ),
                occ_fee=_float_or_zero(_first(row, "OCC Fees", "OCC Fee")),
                contract_fee=_float_or_zero(_first(row, "Contract Fees", "Contract Fee")),
                sec_fee=_float_or_zero(_first(row, "SEC Fees", "SEC Fee")),
                settlement_fee=_float_or_zero(
                    _first(row, "Settlement Fees", "Settlement Fee")
                ),
                total_fee=_float_or_zero(_first(row, "Total", "Total Fee", "Total Fees")),
                currency=(_first(row, "Currency") or "USD").strip(),
                raw_rows=[dict(row)],
            )
            try:
                current.instrument = parse_symbol(symbol_raw)
            except ValueError as exc:
                logger.warning("parse_symbol failed for %r: %s", symbol_raw, exc)
                current.instrument = None

            # Main row may already carry one fill
            fill_qty_raw = _first(row, "Fill Qty")
            if fill_qty_raw:
                fq = _int_or_zero(fill_qty_raw)
                if fq > 0:
                    current.fills.append(
                        {
                            "qty": fq,
                            "price": _float_or_zero(_first(row, "Fill Price")),
                            "time": _parse_trade_time(_first(row, "Fill Time")),
                        }
                    )
        else:
            # Fill-only row
            if current is None:
                continue
            fq = _int_or_zero(_first(row, "Fill Qty"))
            if fq <= 0:
                continue
            current.fills.append(
                {
                    "qty": fq,
                    "price": _float_or_zero(_first(row, "Fill Price")),
                    "time": _parse_trade_time(_first(row, "Fill Time")),
                }
            )
            current.raw_rows.append(dict(row))

    if current:
        orders.append(current)

    # Filter to Filled + derive aggregates
    filled: list[MoomooOrder] = []
    for o in orders:
        status_low = (o.status or "").lower()
        if status_low not in ("filled", "executed", "complete"):
            continue
        total_qty = sum(int(f["qty"]) for f in o.fills)
        if total_qty <= 0:
            continue
        total_val = sum(int(f["qty"]) * float(f["price"]) for f in o.fills)
        o.filled_qty = total_qty
        o.avg_fill_price = total_val / total_qty if total_qty else 0.0
        fill_times = [f["time"] for f in o.fills if f["time"]]
        if fill_times:
            o.first_fill_time = min(fill_times)
            o.last_fill_time = max(fill_times)
        filled.append(o)
    return filled


def _canonical_ts(dt: Optional[datetime]) -> str:
    """Stable string representation of a datetime for external_id hashing.

    Normalises to UTC to avoid DST drift producing different ids for the
    same underlying moment in time.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        # Assume it is already in ET (what _parse_trade_time produces) and tag it.
        dt = dt.replace(tzinfo=ET)
    # Format as UTC with second precision — no DST offset leakage.
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_external_id(o: MoomooOrder) -> str:
    """Deterministic ID used for dedup. Stable across re-parses of the same CSV."""
    key = (
        f"{o.symbol}|{o.side}|{_canonical_ts(o.order_time)}|"
        f"{_canonical_ts(o.first_fill_time)}|"
        f"{o.order_qty}|{o.order_price}|{o.avg_fill_price:.6f}|{o.filled_qty}"
    )
    return "moomoo_" + hashlib.sha256(key.encode()).hexdigest()[:16]

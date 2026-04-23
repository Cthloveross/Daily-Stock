# -*- coding: utf-8 -*-
"""FIFO matcher for journal orders -> trades.

Matching key: ``(underlying, expiry, strike, right, is_option)``. Each key gets
its own FIFO queue of open legs. Buy/Sell resolves to open / add / close / open
(opposite direction) based on current net position.

Multipliers: options carry 100 shares/contract; equity is 1.

The matcher is a pure function: it reads plain dicts from
:func:`src.journal.storage.query_events_for_matching` and returns a list of
dicts suitable for :func:`src.journal.storage.replace_trades`.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["match_legs_fifo", "dte_bucket_of"]


def _multiplier(is_option: bool) -> int:
    return 100 if is_option else 1


def _key_of(evt: dict) -> tuple:
    """Grouping key. Equity uses underlying only; option uses full chain locator."""
    if evt.get("is_option"):
        return (
            evt["underlying"],
            evt.get("expiry"),
            evt.get("strike"),
            evt.get("right"),
            True,
        )
    return (evt["underlying"], None, None, None, False)


def _time_of(evt: dict) -> datetime:
    """Best-effort event timestamp for sort/display.

    Prefers first_fill_time, then order_time, then epoch.
    """
    return evt.get("first_fill_time") or evt.get("order_time") or datetime.min


@dataclass
class _OpenLot:
    """A single add-to-position leg still awaiting close."""

    qty_remaining: int
    entry_price: float
    entry_time: datetime
    order_id: int
    external_id: str
    direction: str  # 'long' / 'short'


def _make_trade(
    key: tuple,
    evt: dict,
    direction: str,
    close_evt: dict,
    qty: int,
    entry_price: float,
    entry_time: datetime,
    open_order_ids: list[int],
    close_order_id: int,
    is_option: bool,
    entry_fee_share: float,
    exit_fee_share: float,
) -> dict:
    underlying, expiry, strike, right, _ = key
    multiplier = _multiplier(is_option)

    sign = 1 if direction == "long" else -1
    pnl_gross = sign * (close_evt["price"] - entry_price) * qty * multiplier
    total_fee = entry_fee_share + exit_fee_share
    pnl_net = pnl_gross - total_fee
    cost_basis = entry_price * qty * multiplier
    pnl_pct = (pnl_net / cost_basis * 100.0) if cost_basis else None

    exit_time = _time_of(close_evt)
    hold_seconds = None
    if isinstance(entry_time, datetime) and isinstance(exit_time, datetime):
        hold_seconds = int((exit_time - entry_time).total_seconds())

    dte_at_entry = None
    if is_option and expiry and isinstance(entry_time, datetime):
        dte_at_entry = (expiry - entry_time.date()).days

    return {
        "is_option": is_option,
        "raw_symbol": close_evt.get("raw_symbol") or evt.get("raw_symbol"),
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "right": right,
        "direction": direction,
        "status": "closed",
        "quantity": qty,
        "avg_entry_price": entry_price,
        "avg_exit_price": close_evt["price"],
        "entry_time": entry_time,
        "exit_time": exit_time,
        "hold_seconds": hold_seconds,
        "dte_at_entry": dte_at_entry,
        "dte_bucket": dte_bucket_of(dte_at_entry) if is_option else None,
        "pnl_gross": pnl_gross,
        "total_fee": total_fee,
        "pnl_net": pnl_net,
        "pnl_pct": pnl_pct,
        "open_order_ids": list(open_order_ids),
        "close_order_ids": [close_order_id],
    }


def _make_open_trade(
    key: tuple,
    direction: str,
    open_lots: list[_OpenLot],
    is_option: bool,
    raw_symbol: str,
) -> Optional[dict]:
    if not open_lots:
        return None
    underlying, expiry, strike, right, _ = key
    total_qty = sum(lot.qty_remaining for lot in open_lots)
    if total_qty <= 0:
        return None
    weighted = sum(lot.qty_remaining * lot.entry_price for lot in open_lots) / total_qty
    earliest = min(lot.entry_time for lot in open_lots)
    dte_at_entry = None
    if is_option and expiry and isinstance(earliest, datetime):
        dte_at_entry = (expiry - earliest.date()).days
    return {
        "is_option": is_option,
        "raw_symbol": raw_symbol,
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "right": right,
        "direction": direction,
        "status": "open",
        "quantity": total_qty,
        "avg_entry_price": weighted,
        "avg_exit_price": None,
        "entry_time": earliest,
        "exit_time": None,
        "hold_seconds": None,
        "dte_at_entry": dte_at_entry,
        "dte_bucket": dte_bucket_of(dte_at_entry) if is_option else None,
        "pnl_gross": None,
        "total_fee": 0.0,
        "pnl_net": None,
        "pnl_pct": None,
        "open_order_ids": [lot.order_id for lot in open_lots],
        "close_order_ids": None,
    }


def dte_bucket_of(dte: Optional[int]) -> Optional[str]:
    """Map a days-to-expiry integer into the standard journal buckets."""
    if dte is None:
        return None
    if dte <= 0:
        return "0DTE"
    if dte <= 3:
        return "1-3DTE"
    if dte <= 7:
        return "4-7DTE"
    if dte <= 30:
        return "8-30DTE"
    return "30+DTE"


def match_legs_fifo(events: list[dict]) -> list[dict]:
    """Group events per key, then FIFO-match buys against sells.

    ``events`` must be plain dicts sorted by fill time ascending. The matcher
    is pure: it does not read or write the DB.
    """
    events_sorted = sorted(events, key=_time_of)
    queues: dict[tuple, deque[_OpenLot]] = defaultdict(deque)
    trades: list[dict] = []
    raw_symbols_per_key: dict[tuple, str] = {}

    for evt in events_sorted:
        key = _key_of(evt)
        side = evt["side"].lower()
        qty = int(evt["quantity"])
        if qty <= 0:
            continue
        q = queues[key]
        raw_symbols_per_key[key] = evt.get("raw_symbol") or raw_symbols_per_key.get(
            key, evt["underlying"]
        )

        # Decide action based on existing position direction.
        current_direction = q[0].direction if q else None
        opening = current_direction is None
        if opening:
            direction = "long" if side == "buy" else "short"
            q.append(
                _OpenLot(
                    qty_remaining=qty,
                    entry_price=float(evt["price"]),
                    entry_time=_time_of(evt),
                    order_id=int(evt["id"]),
                    external_id=str(evt["external_id"]),
                    direction=direction,
                )
            )
            continue

        # Add to position?
        add = (current_direction == "long" and side == "buy") or (
            current_direction == "short" and side == "sell"
        )
        if add:
            q.append(
                _OpenLot(
                    qty_remaining=qty,
                    entry_price=float(evt["price"]),
                    entry_time=_time_of(evt),
                    order_id=int(evt["id"]),
                    external_id=str(evt["external_id"]),
                    direction=current_direction,
                )
            )
            continue

        # Closing position (FIFO).
        qty_to_close = qty
        close_fee = float(evt.get("total_fee") or 0.0)
        while qty_to_close > 0 and q:
            head = q[0]
            consumed = min(head.qty_remaining, qty_to_close)
            # Share the exit fee proportionally across each consumed lot.
            fee_share_exit = (
                close_fee * (consumed / qty) if qty > 0 else close_fee
            )
            trades.append(
                _make_trade(
                    key=key,
                    evt=evt,
                    direction=head.direction,
                    close_evt=evt,
                    qty=consumed,
                    entry_price=head.entry_price,
                    entry_time=head.entry_time,
                    open_order_ids=[head.order_id],
                    close_order_id=int(evt["id"]),
                    is_option=evt["is_option"],
                    entry_fee_share=0.0,  # entry fees stored on the open order only
                    exit_fee_share=fee_share_exit,
                )
            )
            head.qty_remaining -= consumed
            qty_to_close -= consumed
            if head.qty_remaining == 0:
                q.popleft()

        if qty_to_close > 0:
            # Closed more than current position => this extra qty opens the opposite direction.
            # (Can happen on partially-complete CSV exports or option assignments handled manually.)
            logger.warning(
                "Over-close on key=%s: extra %d shares flip direction",
                key,
                qty_to_close,
            )
            opposite = "long" if side == "buy" else "short"
            q.append(
                _OpenLot(
                    qty_remaining=qty_to_close,
                    entry_price=float(evt["price"]),
                    entry_time=_time_of(evt),
                    order_id=int(evt["id"]),
                    external_id=str(evt["external_id"]),
                    direction=opposite,
                )
            )

    # Emit remaining open positions.
    for key, q in queues.items():
        if not q:
            continue
        direction = q[0].direction
        open_trade = _make_open_trade(
            key=key,
            direction=direction,
            open_lots=list(q),
            is_option=key[4],
            raw_symbol=raw_symbols_per_key.get(key, key[0]),
        )
        if open_trade:
            trades.append(open_trade)

    return trades

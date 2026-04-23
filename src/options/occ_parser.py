# -*- coding: utf-8 -*-
"""OCC option-symbol parser.

Supports the variable-length strike variant used by Moomoo US
(``^([A-Z]+)(\\d{6})([CP])(\\d+)$`` with strike divided by 1000) as well as
the formal OCC 8-digit padded strike (``(?P<strike>\\d{8})`` divided by 1000).

Non-option symbols (plain equities) are returned as ``InstrumentInfo`` with
``is_option=False``. Malformed symbols raise ``ValueError``.

Reference: New-docs/01_PROJECT_VISION_v4.md ADR-v4-07, New-docs/04_OPTION_SUPPORT_EXTENSION.md §2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

__all__ = ["OptionInfo", "InstrumentInfo", "parse_symbol", "format_occ"]


_MOOMOO_OCC_RE = re.compile(r"^([A-Z][A-Z0-9.\-]*?)(\d{6})([CP])(\d+)$")
_PLAIN_EQUITY_RE = re.compile(r"^[A-Z][A-Z0-9.\-]*$")


@dataclass(frozen=True)
class OptionInfo:
    """Parsed metadata for an option contract."""

    underlying: str
    expiry: date
    right: str  # 'C' or 'P'
    strike: float


@dataclass(frozen=True)
class InstrumentInfo:
    """Uniform descriptor for anything that trades under a symbol.

    For plain equities ``option`` is ``None`` and ``underlying == raw_symbol``.
    """

    is_option: bool
    raw_symbol: str
    underlying: str
    option: Optional[OptionInfo]


def parse_symbol(sym: str) -> InstrumentInfo:
    """Parse a symbol into :class:`InstrumentInfo`.

    Raises :class:`ValueError` for empty / malformed symbols.

    Rules:
      - Option form: ``<UND><YYMMDD><C|P><STRIKE_X1000>`` with strike variable-length.
      - Plain equity: any ``[A-Z][A-Z0-9.-]*`` string.
    """
    if not sym or not isinstance(sym, str):
        raise ValueError(f"Empty or non-string symbol: {sym!r}")

    raw = sym.strip().upper()
    if not raw:
        raise ValueError("Empty symbol after stripping whitespace")

    # Try option form first (longer match wins: option regex requires trailing digits).
    option_match = _MOOMOO_OCC_RE.match(raw)
    if option_match and len(raw) >= len(option_match.group(1)) + 6 + 1 + 1:
        underlying, yymmdd, right, strike_raw = option_match.groups()
        try:
            yy = int(yymmdd[0:2])
            mm = int(yymmdd[2:4])
            dd = int(yymmdd[4:6])
            # Two-digit year -> 2000+YY; YY=99 reserved for 1999-era tape (not expected here).
            year = 2000 + yy
            expiry = date(year, mm, dd)
        except ValueError as exc:
            raise ValueError(f"Invalid expiry in symbol {raw!r}: {exc}") from exc

        if right not in ("C", "P"):
            raise ValueError(f"Invalid right {right!r} in symbol {raw!r}")

        try:
            strike = int(strike_raw) / 1000.0
        except ValueError as exc:
            raise ValueError(f"Invalid strike {strike_raw!r} in symbol {raw!r}") from exc

        if strike <= 0:
            raise ValueError(f"Non-positive strike {strike} in {raw!r}")

        return InstrumentInfo(
            is_option=True,
            raw_symbol=raw,
            underlying=underlying,
            option=OptionInfo(
                underlying=underlying,
                expiry=expiry,
                right=right,
                strike=strike,
            ),
        )

    if _PLAIN_EQUITY_RE.match(raw):
        return InstrumentInfo(
            is_option=False,
            raw_symbol=raw,
            underlying=raw,
            option=None,
        )

    raise ValueError(f"Unrecognised instrument symbol: {raw!r}")


def format_occ(
    underlying: str,
    expiry: date,
    right: str,
    strike: float,
    padded: bool = False,
) -> str:
    """Render an OCC symbol from components.

    When ``padded`` is ``True`` the strike uses the formal OCC 8-digit fixed
    width (``strike*1000`` zero-padded to 8). Otherwise the Moomoo variable-
    length form is used (no leading zeros).
    """
    if not underlying or not isinstance(underlying, str):
        raise ValueError(f"Invalid underlying: {underlying!r}")
    if right not in ("C", "P"):
        raise ValueError(f"Invalid right: {right!r}")
    if strike <= 0:
        raise ValueError(f"Non-positive strike: {strike}")

    und = underlying.strip().upper()
    yymmdd = expiry.strftime("%y%m%d")
    strike_int = int(round(strike * 1000))
    strike_str = f"{strike_int:08d}" if padded else str(strike_int)
    return f"{und}{yymmdd}{right}{strike_str}"

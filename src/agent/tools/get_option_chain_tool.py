# -*- coding: utf-8 -*-
"""Agent tool: option chain + LEAP candidates via yfinance (data_provider)."""
from __future__ import annotations

from dataclasses import asdict

from data_provider.options_chain import OptionsChainFetcher


def get_option_chain_tool(
    symbol: str,
    expiry: str | None = None,
    right: str | None = None,
) -> dict:
    """Returns either a single expiry slice or the list of expirations.

    If ``expiry`` is provided, returns contract quotes for that date. If only
    ``symbol`` is given, returns the list of available expirations.
    """
    fetcher = OptionsChainFetcher()
    if expiry:
        quotes = fetcher.get_chain(symbol, expiry, right=right)
        return {
            "symbol": symbol.upper(),
            "expiry": expiry,
            "right": right or "BOTH",
            "count": len(quotes),
            "quotes": [asdict(q) for q in quotes],
        }
    return {
        "symbol": symbol.upper(),
        "expirations": fetcher.get_expirations(symbol),
    }


def find_leap_candidates_tool(
    symbol: str,
    delta_min: float = 0.70,
    delta_max: float = 0.85,
    min_dte: int = 270,
) -> dict:
    fetcher = OptionsChainFetcher()
    cands = fetcher.find_leap_candidates(
        symbol, delta_min=delta_min, delta_max=delta_max, min_dte=min_dte
    )
    return {
        "symbol": symbol.upper(),
        "delta_band": [delta_min, delta_max],
        "min_dte": min_dte,
        "count": len(cands),
        "candidates": [asdict(c) for c in cands],
    }

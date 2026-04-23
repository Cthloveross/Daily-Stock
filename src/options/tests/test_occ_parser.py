# -*- coding: utf-8 -*-
"""OCC parser unit tests."""
from __future__ import annotations

from datetime import date

import pytest

from src.options.occ_parser import (
    InstrumentInfo,
    OptionInfo,
    format_occ,
    parse_symbol,
)


class TestParseSymbol:
    def test_moomoo_variable_strike_put(self):
        info = parse_symbol("TSLA260417P382500")
        assert info.is_option is True
        assert info.underlying == "TSLA"
        assert info.option == OptionInfo(
            underlying="TSLA",
            expiry=date(2026, 4, 17),
            right="P",
            strike=382.5,
        )

    def test_moomoo_variable_strike_call_integer(self):
        info = parse_symbol("NVDA260417C200000")
        assert info.option.strike == 200.0
        assert info.option.right == "C"

    def test_plain_equity(self):
        info = parse_symbol("NVDA")
        assert info.is_option is False
        assert info.underlying == "NVDA"
        assert info.option is None
        assert info.raw_symbol == "NVDA"

    def test_lowercase_upcased(self):
        info = parse_symbol("nvda")
        assert info.underlying == "NVDA"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_symbol("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            parse_symbol(None)  # type: ignore[arg-type]

    def test_bad_expiry_raises(self):
        # Month 13 not a real date
        with pytest.raises(ValueError):
            parse_symbol("NVDA261301C100000")

    def test_zero_strike_raises(self):
        with pytest.raises(ValueError):
            parse_symbol("NVDA260417C0")


class TestFormatOcc:
    def test_moomoo_variable_format(self):
        s = format_occ("TSLA", date(2026, 4, 17), "P", 382.5)
        assert s == "TSLA260417P382500"
        # Round-trip.
        assert parse_symbol(s).option.strike == 382.5

    def test_padded_format(self):
        s = format_occ("TSLA", date(2026, 4, 17), "P", 382.5, padded=True)
        assert s == "TSLA260417P00382500"
        # Parser still accepts padded form; strike preserved.
        info = parse_symbol(s)
        assert info.option.strike == 382.5

    def test_bad_right(self):
        with pytest.raises(ValueError):
            format_occ("NVDA", date(2026, 1, 1), "X", 100.0)

    def test_non_positive_strike(self):
        with pytest.raises(ValueError):
            format_occ("NVDA", date(2026, 1, 1), "C", 0.0)

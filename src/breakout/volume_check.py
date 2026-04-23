# -*- coding: utf-8 -*-
"""Q3: Volume confirmation check.

A valid breakout should come with above-average volume. The threshold multiple
is configurable (default 1.2-1.5x 20-bar average).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["VolumeCheckResult", "check_volume_confirmation"]


@dataclass(frozen=True)
class VolumeCheckResult:
    passed: bool
    current_volume: float
    reference_volume: float
    multiple: float
    required_multiple: float


def check_volume_confirmation(
    current_volume: float,
    reference_volume: float,
    required_multiple: float = 1.2,
) -> VolumeCheckResult:
    """Return whether ``current_volume >= reference_volume * required_multiple``.

    ``reference_volume`` typically is a 20-bar trailing average at the same
    timeframe. When reference is 0 (no history), check is inconclusive ->
    ``passed=False, multiple=0``.
    """
    if reference_volume <= 0:
        return VolumeCheckResult(
            passed=False,
            current_volume=current_volume,
            reference_volume=reference_volume,
            multiple=0.0,
            required_multiple=required_multiple,
        )
    mult = current_volume / reference_volume
    return VolumeCheckResult(
        passed=mult >= required_multiple,
        current_volume=current_volume,
        reference_volume=reference_volume,
        multiple=mult,
        required_multiple=required_multiple,
    )

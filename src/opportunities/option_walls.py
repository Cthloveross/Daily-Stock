# -*- coding: utf-8 -*-
"""Pure option-wall aggregation with explicit observable/model boundaries.

The input is a normalized, read-only chain snapshot.  Open-interest and
session-volume walls are direct aggregations.  The gamma concentration metric
is deliberately *unsigned*: public OI does not reveal dealer inventory, so the
module must not present a signed dealer-GEX estimate as an observed fact.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Optional


FORMULA_VERSION = "gross-gamma-concentration-1pct/v1"
GAMMA_UNIT = "usd_delta_change_per_1pct_move"
ATM_CALL_IV_METHOD = "nearest_expiry_atm_call_from_same_wall_snapshot"


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _top_levels(
    values: dict[float, float],
    *,
    spot: float,
    unit: str,
    method: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    positive = [
        (strike, value)
        for strike, value in values.items()
        if math.isfinite(value) and value > 0
    ]
    total = sum(value for _, value in positive)
    ordered = sorted(
        positive,
        key=lambda item: (-item[1], abs(item[0] - spot), item[0]),
    )[:limit]

    levels: list[dict[str, Any]] = []
    prior_value: Optional[float] = None
    prior_rank = 0
    for index, (strike, value) in enumerate(ordered, start=1):
        rank = prior_rank if prior_value == value else index
        levels.append(
            {
                "rank": rank,
                "strike": round(strike, 6),
                "distance_from_spot_percent": round(
                    ((strike / spot) - 1.0) * 100.0,
                    6,
                ),
                "metric_value": round(value, 6),
                "share_of_bucket_percent": round(
                    (value / total) * 100.0 if total > 0 else 0.0,
                    6,
                ),
                "unit": unit,
                "method": method,
            }
        )
        prior_value = value
        prior_rank = rank
    return levels


def _atm_call_iv_context(
    snapshot: Any,
    *,
    spot: float,
) -> dict[str, Any]:
    """Select ATM Call IV without issuing another provider request.

    The wall snapshot already contains dynamic observations for every accepted
    contract.  Match the standalone ATM-IV contract by first fixing the
    snapshot's nearest expiry, then choosing the Call strike closest to spot.
    If that exact contract lacks a valid IV, fail closed instead of silently
    substituting another strike or expiry.
    """

    expiries = [
        str(value).strip()
        for value in (getattr(snapshot, "expiries", ()) or ())
        if str(value).strip()
    ]
    expiry = min(expiries) if expiries else None
    calls = [
        contract
        for contract in (getattr(snapshot, "contracts", ()) or ())
        if expiry is not None
        and str(getattr(contract, "expiry", "")).strip() == expiry
        and str(getattr(contract, "right", "")).strip().upper() == "C"
        and (strike := _finite_number(getattr(contract, "strike", None))) is not None
        and strike > 0
    ]
    atm = min(
        calls,
        key=lambda contract: (
            abs(float(getattr(contract, "strike")) - spot),
            float(getattr(contract, "strike")),
            str(getattr(contract, "code", "")),
        ),
        default=None,
    )
    strike = (
        _finite_number(getattr(atm, "strike", None))
        if atm is not None
        else None
    )
    iv_decimal = (
        _finite_number(getattr(atm, "implied_volatility", None))
        if atm is not None
        else None
    )
    if (
        expiry is not None
        and strike is not None
        and strike > 0
        and iv_decimal is not None
        and iv_decimal > 0
    ):
        return {
            "state": "ready",
            "expiry": expiry,
            "strike": round(strike, 6),
            "atm_call_iv_percent": round(iv_decimal * 100.0, 6),
            "selection_method": ATM_CALL_IV_METHOD,
        }
    return {
        "state": "unavailable",
        "expiry": expiry,
        "strike": round(strike, 6) if strike is not None and strike > 0 else None,
        "atm_call_iv_percent": None,
        "selection_method": ATM_CALL_IV_METHOD,
    }


def build_option_wall_payload(
    snapshot: Any,
    *,
    dte_min: int,
    dte_max: int,
    top_n: int = 3,
) -> dict[str, Any]:
    """Aggregate a provider snapshot into transparent wall levels.

    ``snapshot.contracts`` items are expected to expose ``right``, ``strike``,
    ``open_interest``, ``volume``, ``gamma``, ``contract_size`` and
    ``update_time`` attributes.  Invalid optional gamma fields are ignored
    without weakening the directly observed OI/volume walls.
    """

    spot = _finite_number(getattr(snapshot, "spot", None))
    if spot is None or spot <= 0:
        raise ValueError("a finite positive underlying spot is required")

    metrics: dict[str, dict[float, float]] = {
        key: defaultdict(float)
        for key in (
            "call_oi",
            "put_oi",
            "call_volume",
            "put_volume",
            "call_gamma_concentration",
            "put_gamma_concentration",
            "gross_gamma_concentration",
        )
    }
    gamma_contract_count = 0
    quote_times: list[str] = []

    contracts: Iterable[Any] = getattr(snapshot, "contracts", ()) or ()
    for contract in contracts:
        strike = _finite_number(getattr(contract, "strike", None))
        if strike is None or strike <= 0:
            continue
        right = str(getattr(contract, "right", "")).strip().upper()
        if right not in {"C", "P"}:
            continue
        oi = _finite_number(getattr(contract, "open_interest", None))
        volume = _finite_number(getattr(contract, "volume", None))
        if oi is None or oi < 0 or volume is None or volume < 0:
            continue

        side = "call" if right == "C" else "put"
        metrics[f"{side}_oi"][strike] += oi
        metrics[f"{side}_volume"][strike] += volume

        gamma = _finite_number(getattr(contract, "gamma", None))
        contract_size = _finite_number(getattr(contract, "contract_size", None))
        if gamma is not None and contract_size is not None and contract_size > 0:
            gross_gamma = abs(gamma) * oi * contract_size * spot * spot * 0.01
            if math.isfinite(gross_gamma):
                metrics[f"{side}_gamma_concentration"][strike] += gross_gamma
                metrics["gross_gamma_concentration"][strike] += gross_gamma
                gamma_contract_count += 1

        update_time = str(getattr(contract, "update_time", "") or "").strip()
        if update_time:
            quote_times.append(update_time)

    requested = max(0, int(getattr(snapshot, "requested_contract_count", 0) or 0))
    received = max(0, int(getattr(snapshot, "snapshot_received_count", 0) or 0))
    valid = max(0, int(getattr(snapshot, "valid_contract_count", 0) or 0))
    coverage_percent = round((valid / requested) * 100.0, 4) if requested else 0.0

    walls = {
        "call_oi": _top_levels(
            metrics["call_oi"],
            spot=spot,
            unit="contracts",
            method="sum_open_interest",
            limit=top_n,
        ),
        "put_oi": _top_levels(
            metrics["put_oi"],
            spot=spot,
            unit="contracts",
            method="sum_open_interest",
            limit=top_n,
        ),
        "call_volume": _top_levels(
            metrics["call_volume"],
            spot=spot,
            unit="contracts",
            method="sum_session_volume",
            limit=top_n,
        ),
        "put_volume": _top_levels(
            metrics["put_volume"],
            spot=spot,
            unit="contracts",
            method="sum_session_volume",
            limit=top_n,
        ),
        "call_gamma_concentration": _top_levels(
            metrics["call_gamma_concentration"],
            spot=spot,
            unit=GAMMA_UNIT,
            method="gross_gamma_concentration_1pct",
            limit=top_n,
        ),
        "put_gamma_concentration": _top_levels(
            metrics["put_gamma_concentration"],
            spot=spot,
            unit=GAMMA_UNIT,
            method="gross_gamma_concentration_1pct",
            limit=top_n,
        ),
        "gross_gamma_concentration": _top_levels(
            metrics["gross_gamma_concentration"],
            spot=spot,
            unit=GAMMA_UNIT,
            method="gross_gamma_concentration_1pct",
            limit=top_n,
        ),
    }

    return {
        "formula_version": FORMULA_VERSION,
        "spot": round(spot, 6),
        "quote_as_of": max(quote_times) if quote_times else None,
        "atm_call_iv": _atm_call_iv_context(snapshot, spot=spot),
        "scope": {
            "dte_min": dte_min,
            "dte_max": dte_max,
            "expiries": list(getattr(snapshot, "expiries", ()) or ()),
            "standard_contracts_only": True,
        },
        "coverage": {
            "requested_contracts": requested,
            "snapshot_received_contracts": received,
            "valid_contracts": valid,
            "coverage_percent": coverage_percent,
            "failed_batches": max(
                0,
                int(getattr(snapshot, "failed_batch_count", 0) or 0),
            ),
            "excluded_nonstandard_contracts": max(
                0,
                int(getattr(snapshot, "excluded_nonstandard_count", 0) or 0),
            ),
            "excluded_unknown_standard_type_contracts": max(
                0,
                int(
                    getattr(
                        snapshot,
                        "excluded_unknown_standard_type_count",
                        0,
                    )
                    or 0
                ),
            ),
            "gamma_contracts": gamma_contract_count,
        },
        "walls": walls,
        "assumptions": [
            "Gamma concentration uses abs(gamma) × OI × contract size × spot² × 1%.",
            "No dealer-position sign is inferred from public open interest.",
        ],
        "limitations": [
            "Open interest is a cleared-position total and does not reveal buyer/seller or opening/closing direction.",
            "Session volume is cumulative and does not reveal whether positions remain open.",
            "A concentration level is context, not guaranteed support, resistance, pinning, or breakout.",
        ],
    }

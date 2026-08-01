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

# Per-level expiry breakdown stays bounded so payloads remain small.
LEVEL_TOP_EXPIRY_LIMIT = 3

# Settlement/session semantics per metric.  Open interest is a cleared
# prior-session total; volume is current-session cumulative activity; the
# gamma metric is a model value derived from settled OI and snapshot greeks.
METRIC_BASIS_SETTLED_OI = "settled_open_interest_prior_session"
METRIC_BASIS_SESSION_VOLUME = "current_session_cumulative_volume"
METRIC_BASIS_MODEL_GAMMA = "model_from_settled_oi_and_snapshot_greeks"

# Quote fields a per-expiry entry may carry.  Every field must trace back to
# the observed snapshot row backing that entry; a missing field stays ``None``
# and is reported through an explicit ``quote_evidence`` marker instead of
# being zero-filled.
_QUOTE_FIELD_COUNT = 4  # iv_percent, bid, ask, mark


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_dte(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class _ExpiryCell:
    """Per (metric, strike, expiry) contribution with its backing row."""

    __slots__ = ("value", "dte", "contract_count", "contract")

    def __init__(self) -> None:
        self.value = 0.0
        self.dte: Optional[int] = None
        self.contract_count = 0
        self.contract: Any = None

    def add(self, value: float, contract: Any) -> None:
        self.value += value
        self.contract_count += 1
        if self.dte is None:
            self.dte = _safe_dte(getattr(contract, "dte", None))
        # Quote context is only attributable when exactly one snapshot row
        # backs the cell; otherwise per-contract quotes would be misassigned.
        self.contract = contract if self.contract_count == 1 else None


def _quote_context(cell: _ExpiryCell) -> tuple[dict[str, Any], str]:
    """Extract quote fields from the single observed row backing a cell.

    Fields absent from the snapshot row stay ``None``.  The current Moomoo
    wall snapshot rows carry IV and ``update_time`` but no bid/ask/mark, so
    those stay explicitly null until the adapter observes them.
    """

    iv_percent: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mark: Optional[float] = None
    quote_as_of: Optional[str] = None

    contract = cell.contract if cell.contract_count == 1 else None
    if contract is not None:
        iv_decimal = _finite_number(getattr(contract, "implied_volatility", None))
        if iv_decimal is not None and iv_decimal > 0:
            iv_percent = round(iv_decimal * 100.0, 6)
        bid_value = _finite_number(getattr(contract, "bid", None))
        if bid_value is not None and bid_value >= 0:
            bid = round(bid_value, 6)
        ask_value = _finite_number(getattr(contract, "ask", None))
        if ask_value is not None and ask_value >= 0:
            ask = round(ask_value, 6)
        mark_value = _finite_number(getattr(contract, "mark", None))
        if mark_value is not None and mark_value >= 0:
            mark = round(mark_value, 6)
        update_time = str(getattr(contract, "update_time", "") or "").strip()
        quote_as_of = update_time or None

    observed = sum(
        1 for value in (iv_percent, bid, ask, mark) if value is not None
    )
    if observed == _QUOTE_FIELD_COUNT:
        evidence = "observed"
    elif observed > 0:
        evidence = "partial"
    else:
        evidence = "unavailable"
    quote = {
        "iv_percent": iv_percent,
        "bid": bid,
        "ask": ask,
        "mark": mark,
        "quote_as_of": quote_as_of,
    }
    return quote, evidence


def _expiry_breakdown(
    cells: dict[str, _ExpiryCell],
    *,
    level_total: float,
    limit: int = LEVEL_TOP_EXPIRY_LIMIT,
) -> tuple[dict[str, Any], str]:
    entries = sorted(
        (
            (expiry, cell)
            for expiry, cell in cells.items()
            if math.isfinite(cell.value) and cell.value > 0
        ),
        key=lambda item: (-item[1].value, item[0]),
    )
    top = entries[:limit]
    rest = entries[limit:]

    top_payload: list[dict[str, Any]] = []
    evidences: list[str] = []
    for expiry, cell in top:
        quote, evidence = _quote_context(cell)
        evidences.append(evidence)
        top_payload.append(
            {
                "expiry": expiry,
                "dte": cell.dte,
                "metric_value": round(cell.value, 6),
                "share_of_level_percent": round(
                    (cell.value / level_total) * 100.0 if level_total > 0 else 0.0,
                    6,
                ),
                "contract_count": cell.contract_count,
                "quote": quote,
                "quote_evidence": evidence,
            }
        )

    other: Optional[dict[str, Any]] = None
    if rest:
        other_value = sum(cell.value for _, cell in rest)
        other = {
            "expiry_count": len(rest),
            "metric_value": round(other_value, 6),
            "share_of_level_percent": round(
                (other_value / level_total) * 100.0 if level_total > 0 else 0.0,
                6,
            ),
        }

    if evidences and all(evidence == "observed" for evidence in evidences):
        level_evidence = "observed"
    elif any(evidence != "unavailable" for evidence in evidences):
        level_evidence = "partial"
    else:
        level_evidence = "unavailable"

    return {"top_expiries": top_payload, "other": other}, level_evidence


def _top_levels(
    values: dict[float, float],
    *,
    spot: float,
    unit: str,
    method: str,
    side: str,
    metric_basis: str,
    expiry_cells: dict[float, dict[str, _ExpiryCell]],
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
        breakdown, quote_evidence = _expiry_breakdown(
            expiry_cells.get(strike, {}),
            level_total=value,
        )
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
                "side": side,
                "metric_basis": metric_basis,
                "quote_evidence": quote_evidence,
                "expiry_breakdown": breakdown,
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

    Each level additionally carries a bounded per-expiry breakdown
    (``expiry_breakdown``) built from ``expiry``/``dte`` on the same rows,
    plus per-expiry quote context (``iv_percent``/``bid``/``ask``/``mark``)
    taken only from the single observed row backing that expiry cell.  Fields
    the snapshot does not carry stay ``None`` and are surfaced through
    ``quote_evidence`` markers; nothing is zero-filled or estimated.
    """

    spot = _finite_number(getattr(snapshot, "spot", None))
    if spot is None or spot <= 0:
        raise ValueError("a finite positive underlying spot is required")

    metric_keys = (
        "call_oi",
        "put_oi",
        "call_volume",
        "put_volume",
        "call_gamma_concentration",
        "put_gamma_concentration",
        "gross_gamma_concentration",
    )
    metrics: dict[str, dict[float, float]] = {
        key: defaultdict(float) for key in metric_keys
    }
    expiry_cells: dict[str, dict[float, dict[str, _ExpiryCell]]] = {
        key: {} for key in metric_keys
    }

    def _record(metric_key: str, strike: float, expiry: str, value: float, contract: Any) -> None:
        metrics[metric_key][strike] += value
        if value > 0 and expiry:
            cell = (
                expiry_cells[metric_key]
                .setdefault(strike, {})
                .setdefault(expiry, _ExpiryCell())
            )
            cell.add(value, contract)

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
        expiry = str(getattr(contract, "expiry", "") or "").strip()
        _record(f"{side}_oi", strike, expiry, oi, contract)
        _record(f"{side}_volume", strike, expiry, volume, contract)

        gamma = _finite_number(getattr(contract, "gamma", None))
        contract_size = _finite_number(getattr(contract, "contract_size", None))
        if gamma is not None and contract_size is not None and contract_size > 0:
            gross_gamma = abs(gamma) * oi * contract_size * spot * spot * 0.01
            if math.isfinite(gross_gamma):
                _record(
                    f"{side}_gamma_concentration",
                    strike,
                    expiry,
                    gross_gamma,
                    contract,
                )
                _record(
                    "gross_gamma_concentration",
                    strike,
                    expiry,
                    gross_gamma,
                    contract,
                )
                gamma_contract_count += 1

        update_time = str(getattr(contract, "update_time", "") or "").strip()
        if update_time:
            quote_times.append(update_time)

    requested = max(0, int(getattr(snapshot, "requested_contract_count", 0) or 0))
    received = max(0, int(getattr(snapshot, "snapshot_received_count", 0) or 0))
    valid = max(0, int(getattr(snapshot, "valid_contract_count", 0) or 0))
    coverage_percent = round((valid / requested) * 100.0, 4) if requested else 0.0

    metric_specs = {
        "call_oi": ("contracts", "sum_open_interest", "call", METRIC_BASIS_SETTLED_OI),
        "put_oi": ("contracts", "sum_open_interest", "put", METRIC_BASIS_SETTLED_OI),
        "call_volume": (
            "contracts",
            "sum_session_volume",
            "call",
            METRIC_BASIS_SESSION_VOLUME,
        ),
        "put_volume": (
            "contracts",
            "sum_session_volume",
            "put",
            METRIC_BASIS_SESSION_VOLUME,
        ),
        "call_gamma_concentration": (
            GAMMA_UNIT,
            "gross_gamma_concentration_1pct",
            "call",
            METRIC_BASIS_MODEL_GAMMA,
        ),
        "put_gamma_concentration": (
            GAMMA_UNIT,
            "gross_gamma_concentration_1pct",
            "put",
            METRIC_BASIS_MODEL_GAMMA,
        ),
        "gross_gamma_concentration": (
            GAMMA_UNIT,
            "gross_gamma_concentration_1pct",
            "call_put_aggregate",
            METRIC_BASIS_MODEL_GAMMA,
        ),
    }
    walls = {
        key: _top_levels(
            metrics[key],
            spot=spot,
            unit=unit,
            method=method,
            side=side,
            metric_basis=metric_basis,
            expiry_cells=expiry_cells[key],
            limit=top_n,
        )
        for key, (unit, method, side, metric_basis) in metric_specs.items()
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

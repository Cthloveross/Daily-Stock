# -*- coding: utf-8 -*-
"""Pure evidence contracts for a shadow Moomoo premarket prefetch.

This module deliberately owns no provider, process, database, configuration,
or formal Regime integration.  It only validates compact quote summaries that
may later be handed from an isolated producer to a parent process.

Raw one-minute bars are not part of either payload schema.  A symbol is
represented as one of two mutually exclusive states:

* a complete, finite quote summary whose percentage change is reproducible; or
* an unavailable placeholder whose quote fields are all ``None`` and whose
  reason is selected from a small allowlist.

The enclosing bundle freezes one common UTC target for the whole universe and
provides deterministic canonical JSON plus a SHA-256 content hash.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo


PREMARKET_ARTIFACT_SCHEMA_VERSION = "moomoo_premarket_artifact_v1"
DEFAULT_MAX_FRESHNESS_MINUTES = 5
QUALITY_READY = "ready"
QUALITY_PARTIAL = "partial"
QUALITY_UNAVAILABLE = "unavailable"
QUALITY_STATES = frozenset(
    {QUALITY_READY, QUALITY_PARTIAL, QUALITY_UNAVAILABLE}
)
UNAVAILABLE_REASON_CODES = frozenset(
    {
        "invalid_quote",
        "no_quote",
        "permission_denied",
        "provider_unavailable",
        "request_timeout",
        "stale_evidence",
        "symbol_not_found",
        "worker_unavailable",
    }
)

_NEW_YORK = ZoneInfo("America/New_York")
_CHANGE_PCT_ABS_TOLERANCE = 0.01
_OBSERVATION_PAYLOAD_FIELDS = frozenset(
    {
        "symbol",
        "market_date_et",
        "previous_session",
        "requested_as_of",
        "evidence_as_of",
        "price",
        "previous_close",
        "change_pct",
        "unavailable_reason",
    }
)
_BUNDLE_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "source",
        "market_date_et",
        "previous_session",
        "target_as_of",
        "max_freshness_minutes",
        "required_universe",
        "quality",
        "observations",
    }
)

__all__ = [
    "DEFAULT_MAX_FRESHNESS_MINUTES",
    "PREMARKET_ARTIFACT_SCHEMA_VERSION",
    "QUALITY_PARTIAL",
    "QUALITY_READY",
    "QUALITY_STATES",
    "QUALITY_UNAVAILABLE",
    "UNAVAILABLE_REASON_CODES",
    "PremarketArtifactBundle",
    "PremarketArtifactValidationError",
    "PremarketObservation",
    "build_premarket_artifact_bundle",
    "canonical_json",
    "canonical_sha256",
    "normalize_prefetch_symbols",
]


class PremarketArtifactValidationError(ValueError):
    """Raised when a shadow artifact cannot satisfy the evidence contract."""


def _required_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise PremarketArtifactValidationError(f"{field} must be a date")
    return value


def _parse_date(value: Any, *, field: str) -> date:
    if not isinstance(value, str):
        raise PremarketArtifactValidationError(
            f"{field} must be an ISO date string"
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PremarketArtifactValidationError(
            f"{field} must be an ISO date string"
        ) from exc
    if parsed.isoformat() != value:
        raise PremarketArtifactValidationError(
            f"{field} must use YYYY-MM-DD"
        )
    return parsed


def _aware_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise PremarketArtifactValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PremarketArtifactValidationError(
            f"{field} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _parse_aware_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PremarketArtifactValidationError(
            f"{field} must be an ISO datetime string"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PremarketArtifactValidationError(
            f"{field} must be an ISO datetime string"
        ) from exc
    return _aware_utc(parsed, field=field)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise PremarketArtifactValidationError("symbol is required")
    return symbol


def _finite_positive(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PremarketArtifactValidationError(
            f"{field} must be a finite positive number"
        ) from exc
    if not math.isfinite(result) or result <= 0:
        raise PremarketArtifactValidationError(
            f"{field} must be a finite positive number"
        )
    return result


def _finite_number(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PremarketArtifactValidationError(
            f"{field} must be a finite number"
        ) from exc
    if not math.isfinite(result):
        raise PremarketArtifactValidationError(
            f"{field} must be a finite number"
        )
    return result


def _require_exact_fields(
    payload: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    actual = frozenset(str(key) for key in payload)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing={','.join(missing)}")
    if unexpected:
        details.append(f"unexpected={','.join(unexpected)}")
    raise PremarketArtifactValidationError(
        f"{label} payload fields do not match schema ({'; '.join(details)})"
    )


def canonical_json(value: Any) -> str:
    """Return strict canonical JSON for validated artifact payloads."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PremarketArtifactValidationError(
            "artifact payload is not canonical-JSON serializable"
        ) from exc


def canonical_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 digest of canonical JSON."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_prefetch_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    """Normalize a producer universe and always include the SPY benchmark."""

    if isinstance(symbols, (str, bytes)):
        raise PremarketArtifactValidationError(
            "symbols must be a sequence"
        )
    normalized = {_normalize_symbol(symbol) for symbol in symbols}
    normalized.add("SPY")
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class PremarketObservation:
    """One compact Moomoo quote observation at a requested common cutoff."""

    symbol: str
    market_date_et: date
    previous_session: date
    requested_as_of: datetime
    evidence_as_of: Optional[datetime]
    price: Optional[float]
    previous_close: Optional[float]
    change_pct: Optional[float]
    unavailable_reason: Optional[str] = None

    def __post_init__(self) -> None:
        symbol = _normalize_symbol(self.symbol)
        market_date = _required_date(
            self.market_date_et,
            field="market_date_et",
        )
        previous_session = _required_date(
            self.previous_session,
            field="previous_session",
        )
        if previous_session >= market_date:
            raise PremarketArtifactValidationError(
                "previous_session must be before market_date_et"
            )
        requested_at = _aware_utc(
            self.requested_as_of,
            field="requested_as_of",
        )

        reason = str(self.unavailable_reason or "").strip() or None
        quote_values = (
            self.evidence_as_of,
            self.price,
            self.previous_close,
            self.change_pct,
        )
        if reason is not None:
            if reason not in UNAVAILABLE_REASON_CODES:
                raise PremarketArtifactValidationError(
                    "unavailable_reason is not allowlisted"
                )
            if any(value is not None for value in quote_values):
                raise PremarketArtifactValidationError(
                    "unavailable observations must null every quote field"
                )
            evidence_at = None
            price = None
            previous_close = None
            change_pct = None
        else:
            if any(value is None for value in quote_values):
                raise PremarketArtifactValidationError(
                    "available observations require every quote field"
                )
            evidence_at = _aware_utc(
                self.evidence_as_of,
                field="evidence_as_of",
            )
            if evidence_at > requested_at:
                raise PremarketArtifactValidationError(
                    "evidence_as_of must not exceed requested_as_of"
                )
            price = _finite_positive(self.price, field="price")
            previous_close = _finite_positive(
                self.previous_close,
                field="previous_close",
            )
            supplied_change = _finite_number(
                self.change_pct,
                field="change_pct",
            )
            recomputed_change = (
                (price - previous_close) / previous_close * 100.0
            )
            if not math.isclose(
                supplied_change,
                recomputed_change,
                rel_tol=0.0,
                abs_tol=_CHANGE_PCT_ABS_TOLERANCE,
            ):
                raise PremarketArtifactValidationError(
                    "change_pct is not reproducible from price and previous_close"
                )
            # Provider percentages can be rounded.  Freeze the reproducible
            # value so equivalent retries produce one canonical artifact.
            change_pct = round(recomputed_change, 10)

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "market_date_et", market_date)
        object.__setattr__(self, "previous_session", previous_session)
        object.__setattr__(self, "requested_as_of", requested_at)
        object.__setattr__(self, "evidence_as_of", evidence_at)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "previous_close", previous_close)
        object.__setattr__(self, "change_pct", change_pct)
        object.__setattr__(self, "unavailable_reason", reason)

    @property
    def is_available(self) -> bool:
        return self.unavailable_reason is None

    @classmethod
    def available(
        cls,
        *,
        symbol: str,
        market_date_et: date,
        previous_session: date,
        requested_as_of: datetime,
        evidence_as_of: datetime,
        price: float,
        previous_close: float,
        change_pct: float,
    ) -> "PremarketObservation":
        """Construct a fully sourced quote observation."""

        return cls(
            symbol=symbol,
            market_date_et=market_date_et,
            previous_session=previous_session,
            requested_as_of=requested_as_of,
            evidence_as_of=evidence_as_of,
            price=price,
            previous_close=previous_close,
            change_pct=change_pct,
        )

    @classmethod
    def unavailable(
        cls,
        *,
        symbol: str,
        market_date_et: date,
        previous_session: date,
        requested_as_of: datetime,
        reason: str,
    ) -> "PremarketObservation":
        """Construct an explicit null-only failure placeholder."""

        return cls(
            symbol=symbol,
            market_date_et=market_date_et,
            previous_session=previous_session,
            requested_as_of=requested_as_of,
            evidence_as_of=None,
            price=None,
            previous_close=None,
            change_pct=None,
            unavailable_reason=reason,
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the strict JSON-native observation payload."""

        return {
            "symbol": self.symbol,
            "market_date_et": self.market_date_et.isoformat(),
            "previous_session": self.previous_session.isoformat(),
            "requested_as_of": _iso_utc(self.requested_as_of),
            "evidence_as_of": (
                _iso_utc(self.evidence_as_of)
                if self.evidence_as_of is not None
                else None
            ),
            "price": self.price,
            "previous_close": self.previous_close,
            "change_pct": self.change_pct,
            "unavailable_reason": self.unavailable_reason,
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> "PremarketObservation":
        """Validate and reconstruct one strict serialized observation."""

        if not isinstance(payload, Mapping):
            raise PremarketArtifactValidationError(
                "observation payload must be a mapping"
            )
        _require_exact_fields(
            payload,
            _OBSERVATION_PAYLOAD_FIELDS,
            label="observation",
        )
        evidence_raw = payload["evidence_as_of"]
        return cls(
            symbol=payload["symbol"],
            market_date_et=_parse_date(
                payload["market_date_et"],
                field="market_date_et",
            ),
            previous_session=_parse_date(
                payload["previous_session"],
                field="previous_session",
            ),
            requested_as_of=_parse_aware_datetime(
                payload["requested_as_of"],
                field="requested_as_of",
            ),
            evidence_as_of=(
                _parse_aware_datetime(
                    evidence_raw,
                    field="evidence_as_of",
                )
                if evidence_raw is not None
                else None
            ),
            price=payload["price"],
            previous_close=payload["previous_close"],
            change_pct=payload["change_pct"],
            unavailable_reason=payload["unavailable_reason"],
        )


@dataclass(frozen=True)
class PremarketArtifactBundle:
    """One coherent, deterministic shadow artifact for a required universe."""

    market_date_et: date
    previous_session: date
    target_as_of: datetime
    required_universe: Sequence[str]
    observations: Sequence[PremarketObservation]
    max_freshness_minutes: int = DEFAULT_MAX_FRESHNESS_MINUTES

    def __post_init__(self) -> None:
        market_date = _required_date(
            self.market_date_et,
            field="market_date_et",
        )
        previous_session = _required_date(
            self.previous_session,
            field="previous_session",
        )
        if previous_session >= market_date:
            raise PremarketArtifactValidationError(
                "previous_session must be before market_date_et"
            )
        target_at = _aware_utc(self.target_as_of, field="target_as_of")
        if target_at.astimezone(_NEW_YORK).date() != market_date:
            raise PremarketArtifactValidationError(
                "market_date_et must match target_as_of in America/New_York"
            )

        if (
            isinstance(self.max_freshness_minutes, bool)
            or not isinstance(self.max_freshness_minutes, int)
            or self.max_freshness_minutes <= 0
        ):
            raise PremarketArtifactValidationError(
                "max_freshness_minutes must be a positive integer"
            )

        if isinstance(self.required_universe, (str, bytes)):
            raise PremarketArtifactValidationError(
                "required_universe must be a sequence of symbols"
            )
        universe = tuple(
            sorted(_normalize_symbol(symbol) for symbol in self.required_universe)
        )
        if not universe:
            raise PremarketArtifactValidationError(
                "required_universe must not be empty"
            )
        if len(set(universe)) != len(universe):
            raise PremarketArtifactValidationError(
                "required_universe contains duplicate symbols"
            )

        if isinstance(self.observations, (str, bytes)):
            raise PremarketArtifactValidationError(
                "observations must be a sequence"
            )
        normalized_observations = []
        for observation in self.observations:
            if not isinstance(observation, PremarketObservation):
                raise PremarketArtifactValidationError(
                    "observations must contain PremarketObservation values"
                )
            if observation.market_date_et != market_date:
                raise PremarketArtifactValidationError(
                    f"{observation.symbol} market_date_et does not match bundle"
                )
            if observation.previous_session != previous_session:
                raise PremarketArtifactValidationError(
                    f"{observation.symbol} previous_session does not match bundle"
                )
            if observation.requested_as_of != target_at:
                raise PremarketArtifactValidationError(
                    f"{observation.symbol} requested_as_of does not match target_as_of"
                )
            if observation.evidence_as_of is not None:
                freshness = target_at - observation.evidence_as_of
                if freshness > timedelta(
                    minutes=self.max_freshness_minutes
                ):
                    raise PremarketArtifactValidationError(
                        f"{observation.symbol} evidence exceeds freshness limit"
                    )
            normalized_observations.append(observation)

        observation_symbols = [item.symbol for item in normalized_observations]
        if len(set(observation_symbols)) != len(observation_symbols):
            raise PremarketArtifactValidationError(
                "observations contain duplicate symbols"
            )
        if set(observation_symbols) != set(universe):
            missing = sorted(set(universe) - set(observation_symbols))
            unexpected = sorted(set(observation_symbols) - set(universe))
            details = []
            if missing:
                details.append(f"missing={','.join(missing)}")
            if unexpected:
                details.append(f"unexpected={','.join(unexpected)}")
            raise PremarketArtifactValidationError(
                "observations must exactly cover required_universe "
                f"({'; '.join(details)})"
            )

        object.__setattr__(self, "market_date_et", market_date)
        object.__setattr__(self, "previous_session", previous_session)
        object.__setattr__(self, "target_as_of", target_at)
        object.__setattr__(self, "required_universe", universe)
        object.__setattr__(
            self,
            "observations",
            tuple(sorted(normalized_observations, key=lambda item: item.symbol)),
        )

    @property
    def quality(self) -> str:
        """Derive bundle usability without trusting producer-supplied labels."""

        available_symbols = {
            observation.symbol
            for observation in self.observations
            if observation.is_available
        }
        if "SPY" not in available_symbols:
            return QUALITY_UNAVAILABLE
        if available_symbols == set(self.required_universe):
            return QUALITY_READY
        return QUALITY_PARTIAL

    @property
    def payload_sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        """Return the canonical JSON-native bundle without raw minute bars."""

        return {
            "schema_version": PREMARKET_ARTIFACT_SCHEMA_VERSION,
            "source": "moomoo",
            "market_date_et": self.market_date_et.isoformat(),
            "previous_session": self.previous_session.isoformat(),
            "target_as_of": _iso_utc(self.target_as_of),
            "max_freshness_minutes": self.max_freshness_minutes,
            "required_universe": list(self.required_universe),
            "quality": self.quality,
            "observations": [
                observation.to_payload()
                for observation in self.observations
            ],
        }

    def to_canonical_json(self) -> str:
        return canonical_json(self.to_payload())

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> "PremarketArtifactBundle":
        """Strictly validate and replay one serialized artifact payload."""

        if not isinstance(payload, Mapping):
            raise PremarketArtifactValidationError(
                "bundle payload must be a mapping"
            )
        _require_exact_fields(
            payload,
            _BUNDLE_PAYLOAD_FIELDS,
            label="bundle",
        )
        if payload["schema_version"] != PREMARKET_ARTIFACT_SCHEMA_VERSION:
            raise PremarketArtifactValidationError(
                "unsupported premarket artifact schema_version"
            )
        if payload["source"] != "moomoo":
            raise PremarketArtifactValidationError(
                "premarket artifact source must be moomoo"
            )
        raw_universe = payload["required_universe"]
        raw_observations = payload["observations"]
        if not isinstance(raw_universe, list):
            raise PremarketArtifactValidationError(
                "required_universe payload must be a list"
            )
        if not isinstance(raw_observations, list):
            raise PremarketArtifactValidationError(
                "observations payload must be a list"
            )

        bundle = cls(
            market_date_et=_parse_date(
                payload["market_date_et"],
                field="market_date_et",
            ),
            previous_session=_parse_date(
                payload["previous_session"],
                field="previous_session",
            ),
            target_as_of=_parse_aware_datetime(
                payload["target_as_of"],
                field="target_as_of",
            ),
            max_freshness_minutes=payload["max_freshness_minutes"],
            required_universe=tuple(raw_universe),
            observations=tuple(
                PremarketObservation.from_payload(item)
                for item in raw_observations
            ),
        )
        if payload["quality"] not in QUALITY_STATES:
            raise PremarketArtifactValidationError(
                "bundle quality is not an allowed state"
            )
        if payload["quality"] != bundle.quality:
            raise PremarketArtifactValidationError(
                "bundle quality does not match derived evidence quality"
            )
        return bundle


def build_premarket_artifact_bundle(
    *,
    market_date_et: date,
    previous_session: date,
    target_as_of: datetime,
    required_universe: Sequence[str],
    observations: Sequence[PremarketObservation],
    max_freshness_minutes: int = DEFAULT_MAX_FRESHNESS_MINUTES,
) -> PremarketArtifactBundle:
    """Build one validated bundle through the public producer seam.

    Callers should use :func:`normalize_prefetch_symbols` before fetching so
    they produce an observation (or explicit unavailable placeholder) for
    every required symbol, including SPY.
    """

    return PremarketArtifactBundle(
        market_date_et=market_date_et,
        previous_session=previous_session,
        target_as_of=target_as_of,
        required_universe=normalize_prefetch_symbols(required_universe),
        observations=observations,
        max_freshness_minutes=max_freshness_minutes,
    )

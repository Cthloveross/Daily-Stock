# -*- coding: utf-8 -*-
"""Tests for the pure shadow premarket evidence contract."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

import pytest

from src.regime.premarket_artifacts import (
    DEFAULT_MAX_FRESHNESS_MINUTES,
    QUALITY_PARTIAL,
    QUALITY_READY,
    QUALITY_UNAVAILABLE,
    PremarketArtifactBundle,
    PremarketArtifactValidationError,
    PremarketObservation,
    build_premarket_artifact_bundle,
    normalize_prefetch_symbols,
)


MARKET_DATE = date(2026, 7, 24)
PREVIOUS_SESSION = date(2026, 7, 23)
TARGET = datetime(2026, 7, 24, 12, 55, tzinfo=timezone.utc)


def _available(
    symbol: str,
    *,
    requested_as_of: datetime = TARGET,
    evidence_as_of: datetime | None = None,
    market_date_et: date = MARKET_DATE,
    previous_session: date = PREVIOUS_SESSION,
    price: float = 101.0,
    previous_close: float = 100.0,
    change_pct: float = 1.0,
) -> PremarketObservation:
    return PremarketObservation.available(
        symbol=symbol,
        market_date_et=market_date_et,
        previous_session=previous_session,
        requested_as_of=requested_as_of,
        evidence_as_of=evidence_as_of or requested_as_of - timedelta(minutes=2),
        price=price,
        previous_close=previous_close,
        change_pct=change_pct,
    )


def _unavailable(
    symbol: str,
    *,
    reason: str = "request_timeout",
    requested_as_of: datetime = TARGET,
) -> PremarketObservation:
    return PremarketObservation.unavailable(
        symbol=symbol,
        market_date_et=MARKET_DATE,
        previous_session=PREVIOUS_SESSION,
        requested_as_of=requested_as_of,
        reason=reason,
    )


def _bundle(
    observations,
    *,
    universe=("SPY", "AAPL"),
    target_as_of: datetime = TARGET,
    max_freshness_minutes: int = DEFAULT_MAX_FRESHNESS_MINUTES,
) -> PremarketArtifactBundle:
    return PremarketArtifactBundle(
        market_date_et=MARKET_DATE,
        previous_session=PREVIOUS_SESSION,
        target_as_of=target_as_of,
        required_universe=universe,
        observations=observations,
        max_freshness_minutes=max_freshness_minutes,
    )


def test_ready_bundle_is_canonical_replayable_and_contains_no_raw_bars():
    eastern = ZoneInfo("America/New_York")
    local_target = TARGET.astimezone(eastern)
    spy = _available("spy", requested_as_of=local_target)
    aapl = _available(
        "aapl",
        requested_as_of=local_target,
        price=189.125,
        previous_close=187.25,
        change_pct=1.002,
    )

    bundle = _bundle(
        [spy, aapl],
        universe=("spy", "aapl"),
        target_as_of=local_target,
    )
    payload = bundle.to_payload()
    replay = PremarketArtifactBundle.from_payload(payload)

    assert bundle.quality == QUALITY_READY
    assert bundle.target_as_of == TARGET
    assert bundle.required_universe == ("AAPL", "SPY")
    assert [item.symbol for item in bundle.observations] == ["AAPL", "SPY"]
    assert bundle.to_canonical_json() == replay.to_canonical_json()
    assert bundle.payload_sha256 == replay.payload_sha256
    assert len(bundle.payload_sha256) == 64
    assert "bars" not in bundle.to_canonical_json()
    assert payload["observations"][0]["change_pct"] == round(
        (189.125 - 187.25) / 187.25 * 100,
        10,
    )


def test_canonical_payload_and_hash_ignore_input_order_and_timezone_spelling():
    eastern = ZoneInfo("America/New_York")
    first = _bundle(
        [_available("SPY"), _available("AAPL")],
        universe=("SPY", "AAPL"),
    )
    second = _bundle(
        [
            _available("aapl", requested_as_of=TARGET.astimezone(eastern)),
            _available("spy", requested_as_of=TARGET.astimezone(eastern)),
        ],
        universe=("aapl", "spy"),
        target_as_of=TARGET.astimezone(eastern),
    )

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.payload_sha256 == second.payload_sha256


@pytest.mark.parametrize(
    ("observations", "expected_quality"),
    [
        (
            (_available("SPY"), _unavailable("AAPL")),
            QUALITY_PARTIAL,
        ),
        (
            (_unavailable("SPY"), _available("AAPL")),
            QUALITY_UNAVAILABLE,
        ),
        (
            (_unavailable("SPY"), _unavailable("AAPL")),
            QUALITY_UNAVAILABLE,
        ),
    ],
)
def test_quality_is_derived_from_spy_and_complete_universe(
    observations,
    expected_quality,
):
    assert _bundle(observations).quality == expected_quality


def test_non_spy_universe_can_never_be_ready():
    bundle = _bundle(
        [_available("AAPL")],
        universe=("AAPL",),
    )

    assert bundle.quality == QUALITY_UNAVAILABLE


def test_public_builder_normalizes_universe_and_requires_spy_observation():
    assert normalize_prefetch_symbols(("aapl", " SPY ", "AAPL")) == (
        "AAPL",
        "SPY",
    )

    bundle = build_premarket_artifact_bundle(
        market_date_et=MARKET_DATE,
        previous_session=PREVIOUS_SESSION,
        target_as_of=TARGET,
        required_universe=("aapl",),
        observations=(_available("AAPL"), _available("SPY")),
    )
    assert bundle.required_universe == ("AAPL", "SPY")
    assert bundle.quality == QUALITY_READY

    with pytest.raises(
        PremarketArtifactValidationError,
        match="exactly cover required_universe",
    ):
        build_premarket_artifact_bundle(
            market_date_et=MARKET_DATE,
            previous_session=PREVIOUS_SESSION,
            target_as_of=TARGET,
            required_universe=("AAPL",),
            observations=(_available("AAPL"),),
        )


@pytest.mark.parametrize(
    "reason",
    [
        "invalid_quote",
        "no_quote",
        "permission_denied",
        "provider_unavailable",
        "request_timeout",
        "stale_evidence",
        "symbol_not_found",
        "worker_unavailable",
    ],
)
def test_unavailable_observations_are_null_only_with_allowlisted_reason(reason):
    observation = _unavailable("AAPL", reason=reason)
    payload = observation.to_payload()

    assert observation.is_available is False
    assert payload["unavailable_reason"] == reason
    assert payload["evidence_as_of"] is None
    assert payload["price"] is None
    assert payload["previous_close"] is None
    assert payload["change_pct"] is None


def test_unavailable_observation_rejects_unknown_reason_or_leaked_quote_value():
    with pytest.raises(
        PremarketArtifactValidationError,
        match="not allowlisted",
    ):
        _unavailable("AAPL", reason="mystery_failure")

    with pytest.raises(
        PremarketArtifactValidationError,
        match="null every quote field",
    ):
        PremarketObservation(
            symbol="AAPL",
            market_date_et=MARKET_DATE,
            previous_session=PREVIOUS_SESSION,
            requested_as_of=TARGET,
            evidence_as_of=None,
            price=1.0,
            previous_close=None,
            change_pct=None,
            unavailable_reason="no_quote",
        )


def test_available_observation_rejects_partial_or_invalid_quote_values():
    with pytest.raises(
        PremarketArtifactValidationError,
        match="require every quote field",
    ):
        PremarketObservation(
            symbol="AAPL",
            market_date_et=MARKET_DATE,
            previous_session=PREVIOUS_SESSION,
            requested_as_of=TARGET,
            evidence_as_of=TARGET,
            price=101.0,
            previous_close=None,
            change_pct=1.0,
        )

    for invalid in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(
            PremarketArtifactValidationError,
            match="finite positive",
        ):
            _available("AAPL", price=invalid)

    with pytest.raises(
        PremarketArtifactValidationError,
        match="finite number",
    ):
        _available("AAPL", change_pct=math.inf)

    with pytest.raises(
        PremarketArtifactValidationError,
        match="not reproducible",
    ):
        _available("AAPL", change_pct=2.0)


def test_evidence_must_not_be_future_and_must_meet_bundle_freshness():
    with pytest.raises(
        PremarketArtifactValidationError,
        match="must not exceed requested_as_of",
    ):
        _available(
            "SPY",
            evidence_as_of=TARGET + timedelta(microseconds=1),
        )

    at_boundary = _available(
        "SPY",
        evidence_as_of=TARGET - timedelta(minutes=5),
    )
    assert _bundle([at_boundary], universe=("SPY",)).quality == QUALITY_READY

    stale = _available(
        "SPY",
        evidence_as_of=TARGET - timedelta(minutes=5, microseconds=1),
    )
    with pytest.raises(
        PremarketArtifactValidationError,
        match="exceeds freshness limit",
    ):
        _bundle([stale], universe=("SPY",))


def test_bundle_requires_one_common_timezone_aware_utc_target():
    naive_target = TARGET.replace(tzinfo=None)
    with pytest.raises(
        PremarketArtifactValidationError,
        match="target_as_of must be timezone-aware",
    ):
        _bundle(
            [_available("SPY")],
            universe=("SPY",),
            target_as_of=naive_target,
        )

    mismatch = TARGET + timedelta(seconds=1)
    with pytest.raises(
        PremarketArtifactValidationError,
        match="requested_as_of does not match target_as_of",
    ):
        _bundle(
            [_available("SPY", requested_as_of=mismatch)],
            universe=("SPY",),
        )


def test_bundle_validates_market_date_and_previous_session_metadata():
    next_et_day_target = datetime(
        2026,
        7,
        25,
        4,
        1,
        tzinfo=timezone.utc,
    )
    with pytest.raises(
        PremarketArtifactValidationError,
        match="must match target_as_of",
    ):
        _bundle(
            [_available("SPY")],
            universe=("SPY",),
            target_as_of=next_et_day_target,
        )

    with pytest.raises(
        PremarketArtifactValidationError,
        match="previous_session must be before",
    ):
        _available(
            "SPY",
            previous_session=MARKET_DATE,
        )

    wrong_previous = _available(
        "SPY",
        previous_session=date(2026, 7, 22),
    )
    with pytest.raises(
        PremarketArtifactValidationError,
        match="previous_session does not match bundle",
    ):
        _bundle([wrong_previous], universe=("SPY",))


def test_bundle_requires_exact_unique_observation_coverage():
    with pytest.raises(
        PremarketArtifactValidationError,
        match="exactly cover required_universe",
    ):
        _bundle([_available("SPY")])

    with pytest.raises(
        PremarketArtifactValidationError,
        match="duplicate symbols",
    ):
        _bundle(
            [_available("SPY"), _available("SPY")],
            universe=("SPY",),
        )

    with pytest.raises(
        PremarketArtifactValidationError,
        match="duplicate symbols",
    ):
        _bundle(
            [_available("SPY")],
            universe=("SPY", "spy"),
        )


def test_replay_rejects_raw_bars_unknown_schema_and_forged_quality():
    payload = _bundle(
        [_available("SPY"), _unavailable("AAPL")],
    ).to_payload()

    with_bars = deepcopy(payload)
    with_bars["observations"][0]["bars"] = [{"close": 1.0}]
    with pytest.raises(
        PremarketArtifactValidationError,
        match="unexpected=bars",
    ):
        PremarketArtifactBundle.from_payload(with_bars)

    unknown_schema = deepcopy(payload)
    unknown_schema["schema_version"] = "future"
    with pytest.raises(
        PremarketArtifactValidationError,
        match="unsupported",
    ):
        PremarketArtifactBundle.from_payload(unknown_schema)

    forged_quality = deepcopy(payload)
    forged_quality["quality"] = QUALITY_READY
    with pytest.raises(
        PremarketArtifactValidationError,
        match="does not match derived",
    ):
        PremarketArtifactBundle.from_payload(forged_quality)


def test_bundle_rejects_invalid_freshness_configuration():
    for invalid in (True, 0, -1, 5.0):
        with pytest.raises(
            PremarketArtifactValidationError,
            match="positive integer",
        ):
            _bundle(
                [_available("SPY")],
                universe=("SPY",),
                max_freshness_minutes=invalid,
            )

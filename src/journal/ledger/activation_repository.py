# -*- coding: utf-8 -*-
"""Append-only activation of canonical Episode builds.

Canonical builds remain immutable candidates until one is explicitly
activated.  Each selection change appends an event; reads use the newest
event and fall back to the newest CSV-only build when no activation exists.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.journal.ledger.models import (
    CanonicalEvidenceSetRecord,
    EpisodeBuild,
    EpisodeBuildActivation,
    EpisodeBuildCanonicalSource,
    EpisodeBuildSnapshotFenceSource,
)
from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.storage import get_db

__all__ = [
    "EpisodeBuildActivationError",
    "EpisodeBuildActivationResult",
    "EpisodeBuildActivationState",
    "activate_episode_build",
    "get_episode_build_activation_state",
]


EpisodeBuildSelectionSource = Literal[
    "activation",
    "csv_fallback",
    "none",
]
_ACTIVATABLE_BUILD_STATUSES = {"succeeded", "partial"}
_ASSUMED_FLAT_BOUNDARY_POLICIES = {
    "assumed_flat_unverified",
    "mixed_explicit_and_assumed",
}


class EpisodeBuildActivationError(ValueError):
    """Raised when an activation target or compare-and-swap is invalid."""


@dataclass(frozen=True)
class EpisodeBuildActivationState:
    """Effective build selection plus its append-only activation metadata."""

    account_key: str
    selection_source: EpisodeBuildSelectionSource
    current_activation_id: Optional[int]
    current_activation_sequence: Optional[int]
    current_build_id: Optional[int]
    current_build_key: Optional[str]
    canonical_set_id: Optional[int]
    canonical_set_sha256: Optional[str]
    previous_activation_id: Optional[int]
    previous_build_id: Optional[int]
    activated_at: Optional[datetime]


@dataclass(frozen=True)
class EpisodeBuildActivationResult:
    """Outcome of one activation request."""

    activation_id: int
    activation_key: str
    duplicate: bool
    state: EpisodeBuildActivationState


def _sha256_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalized_account_key(account_key: str) -> str:
    normalized = account_key.strip()
    if not normalized:
        raise EpisodeBuildActivationError("account_key cannot be empty")
    return normalized


def _positive_optional_id(value: Optional[int], field_name: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise EpisodeBuildActivationError(f"{field_name} must be positive or null")


def _expected_build_key(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise EpisodeBuildActivationError("expected_build_key must be SHA-256")
    return normalized


def _require_boundary_acceptance(
    build: EpisodeBuild,
    *,
    accept_assumed_flat: bool,
) -> None:
    try:
        report = json.loads(str(build.build_report_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EpisodeBuildActivationError(
            "Episode build has an invalid boundary report"
        ) from exc
    if not isinstance(report, dict):
        raise EpisodeBuildActivationError(
            "Episode build has an invalid boundary report"
        )
    boundary_policy = str(report.get("opening_boundary_policy") or "")
    if (
        boundary_policy in _ASSUMED_FLAT_BOUNDARY_POLICIES
        and not accept_assumed_flat
    ):
        raise EpisodeBuildActivationError(
            "assumed-flat opening boundary requires explicit acceptance"
        )


def _require_group_fee_scope_acceptance(
    build: EpisodeBuild,
    *,
    accept_group_fee_scope: bool,
) -> None:
    try:
        report = json.loads(str(build.build_report_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EpisodeBuildActivationError(
            "Episode build has an invalid execution-group fee report"
        ) from exc
    if not isinstance(report, dict):
        raise EpisodeBuildActivationError(
            "Episode build has an invalid execution-group fee report"
        )
    affected = int(report.get("group_fee_affected_episode_count", 0))
    attribution_complete = bool(
        report.get("leg_fee_attribution_complete", True)
    )
    if affected > 0 and not attribution_complete and not accept_group_fee_scope:
        raise EpisodeBuildActivationError(
            "execution-group fee is exact only at group scope; affected leg "
            "episodes have no fee/net P&L and are excluded from headline; "
            "explicit acceptance is required"
        )


def _latest_activation(
    session: Any,
    account_key: str,
) -> Optional[EpisodeBuildActivation]:
    return session.execute(
        select(EpisodeBuildActivation)
        .where(EpisodeBuildActivation.account_key == account_key)
        .order_by(
            EpisodeBuildActivation.activation_sequence.desc(),
            EpisodeBuildActivation.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def _latest_csv_build(session: Any, account_key: str) -> Optional[EpisodeBuild]:
    canonical_link_exists = select(EpisodeBuildCanonicalSource.id).where(
        EpisodeBuildCanonicalSource.episode_build_id == EpisodeBuild.id
    ).exists()
    # A snapshot-fence future build is never a CSV fallback: appending one
    # must not change the default view while no activation exists.
    fence_link_exists = select(EpisodeBuildSnapshotFenceSource.id).where(
        EpisodeBuildSnapshotFenceSource.episode_build_id == EpisodeBuild.id
    ).exists()
    return session.execute(
        select(EpisodeBuild)
        .where(
            EpisodeBuild.account_key == account_key,
            ~canonical_link_exists,
            ~fence_link_exists,
        )
        .order_by(EpisodeBuild.recorded_at.desc(), EpisodeBuild.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _eligible_canonical_target(
    session: Any,
    *,
    account_key: str,
    build_id: int,
    expected_build_key: str,
) -> tuple[
    EpisodeBuild,
    EpisodeBuildCanonicalSource,
    CanonicalEvidenceSetRecord,
]:
    build = session.execute(
        select(EpisodeBuild).where(
            EpisodeBuild.id == build_id,
            EpisodeBuild.account_key == account_key,
        )
    ).scalar_one_or_none()
    if build is None:
        raise EpisodeBuildActivationError(
            "episode build does not exist for this account"
        )
    if str(build.build_key).lower() != expected_build_key:
        raise EpisodeBuildActivationError(
            "episode build changed after the confirmed selection"
        )
    if str(build.status) not in _ACTIVATABLE_BUILD_STATUSES:
        raise EpisodeBuildActivationError(
            "only succeeded or partial Episode builds can be activated"
        )
    if int(build.unresolved_evidence_count) != 0:
        raise EpisodeBuildActivationError(
            "Episode build has unresolved evidence and cannot be activated"
        )

    row = session.execute(
        select(EpisodeBuildCanonicalSource, CanonicalEvidenceSetRecord)
        .join(
            CanonicalEvidenceSetRecord,
            CanonicalEvidenceSetRecord.id
            == EpisodeBuildCanonicalSource.canonical_set_id,
        )
        .where(EpisodeBuildCanonicalSource.episode_build_id == build.id)
    ).one_or_none()
    if row is None:
        raise EpisodeBuildActivationError(
            "only canonical-linked Episode builds can be activated"
        )
    link, canonical_set = row
    hashes = {
        str(build.evidence_set_sha256).lower(),
        str(link.canonical_set_sha256).lower(),
        str(canonical_set.canonical_set_sha256).lower(),
    }
    if (
        str(build.account_key) != account_key
        or str(canonical_set.account_key) != account_key
        or str(build.broker) != str(canonical_set.broker)
        or len(hashes) != 1
        or not bool(canonical_set.analysis_ready)
        or int(canonical_set.blocking_issue_count) != 0
    ):
        raise EpisodeBuildActivationError(
            "canonical Episode build provenance is not activation-ready"
        )
    return build, link, canonical_set


def _validate_activation_chain(
    session: Any,
    activation: EpisodeBuildActivation,
) -> None:
    sequence = int(activation.activation_sequence)
    previous_id = activation.previous_activation_id
    if sequence == 1:
        if previous_id is not None:
            raise EpisodeBuildActivationError("activation chain is inconsistent")
        return
    if previous_id is None:
        raise EpisodeBuildActivationError("activation chain is inconsistent")
    previous = session.get(EpisodeBuildActivation, int(previous_id))
    if (
        previous is None
        or str(previous.account_key) != str(activation.account_key)
        or int(previous.activation_sequence) != sequence - 1
        or int(previous.episode_build_id)
        != int(activation.previous_episode_build_id or 0)
    ):
        raise EpisodeBuildActivationError("activation chain is inconsistent")


def _resolve_effective_episode_build(
    session: Any,
    account_key: str,
) -> tuple[Optional[EpisodeBuild], Optional[EpisodeBuildActivation]]:
    """Resolve the build used by default reads inside an existing session."""
    activation = _latest_activation(session, account_key)
    if activation is None:
        return _latest_csv_build(session, account_key), None

    _validate_activation_chain(session, activation)
    build, link, _canonical_set = _eligible_canonical_target(
        session,
        account_key=account_key,
        build_id=int(activation.episode_build_id),
        expected_build_key=str(activation.episode_build_key).lower(),
    )
    if (
        int(link.canonical_set_id) != int(activation.canonical_set_id)
        or str(link.canonical_set_sha256).lower()
        != str(activation.canonical_set_sha256).lower()
    ):
        raise EpisodeBuildActivationError(
            "activation does not match its canonical Episode build"
        )
    return build, activation


def _state(
    account_key: str,
    build: Optional[EpisodeBuild],
    activation: Optional[EpisodeBuildActivation],
) -> EpisodeBuildActivationState:
    if activation is None:
        return EpisodeBuildActivationState(
            account_key=account_key,
            selection_source="csv_fallback" if build is not None else "none",
            current_activation_id=None,
            current_activation_sequence=None,
            current_build_id=int(build.id) if build is not None else None,
            current_build_key=str(build.build_key) if build is not None else None,
            canonical_set_id=None,
            canonical_set_sha256=None,
            previous_activation_id=None,
            previous_build_id=None,
            activated_at=None,
        )
    return EpisodeBuildActivationState(
        account_key=account_key,
        selection_source="activation",
        current_activation_id=int(activation.id),
        current_activation_sequence=int(activation.activation_sequence),
        current_build_id=int(activation.episode_build_id),
        current_build_key=str(activation.episode_build_key),
        canonical_set_id=int(activation.canonical_set_id),
        canonical_set_sha256=str(activation.canonical_set_sha256),
        previous_activation_id=(
            int(activation.previous_activation_id)
            if activation.previous_activation_id is not None
            else None
        ),
        previous_build_id=(
            int(activation.previous_episode_build_id)
            if activation.previous_episode_build_id is not None
            else None
        ),
        activated_at=_utc(activation.activated_at),
    )


def get_episode_build_activation_state(
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> EpisodeBuildActivationState:
    """Return the effective default build and its selection provenance."""
    normalized_account = _normalized_account_key(account_key)
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build, activation = _resolve_effective_episode_build(
            session,
            normalized_account,
        )
        return _state(normalized_account, build, activation)


def _result(
    account_key: str,
    build: EpisodeBuild,
    activation: EpisodeBuildActivation,
    *,
    duplicate: bool,
) -> EpisodeBuildActivationResult:
    return EpisodeBuildActivationResult(
        activation_id=int(activation.id),
        activation_key=str(activation.activation_key),
        duplicate=duplicate,
        state=_state(account_key, build, activation),
    )


def activate_episode_build(
    build_id: int,
    expected_build_key: str,
    *,
    expected_current_activation_id: Optional[int],
    expected_current_build_id: Optional[int],
    accept_assumed_flat: bool = False,
    accept_group_fee_scope: bool = False,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
) -> EpisodeBuildActivationResult:
    """Append a canonical build activation using explicit CAS expectations.

    An exact retry of the event that is already current is idempotent.  Any
    other stale expectation is rejected so an older browser view cannot
    silently replace a newer selection.
    """
    if (
        isinstance(build_id, bool)
        or not isinstance(build_id, int)
        or build_id <= 0
    ):
        raise EpisodeBuildActivationError("build_id must be positive")
    _positive_optional_id(
        expected_current_activation_id,
        "expected_current_activation_id",
    )
    _positive_optional_id(
        expected_current_build_id,
        "expected_current_build_id",
    )
    normalized_account = _normalized_account_key(account_key)
    normalized_build_key = _expected_build_key(expected_build_key)
    activation_key = _sha256_json(
        {
            "account_key": normalized_account,
            "episode_build_id": int(build_id),
            "episode_build_key": normalized_build_key,
            "expected_current_activation_id": expected_current_activation_id,
            "expected_current_build_id": expected_current_build_id,
            "accept_assumed_flat": bool(accept_assumed_flat),
            "accept_group_fee_scope": bool(accept_group_fee_scope),
        }
    )

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        target, canonical_link, _canonical_set = _eligible_canonical_target(
            session,
            account_key=normalized_account,
            build_id=int(build_id),
            expected_build_key=normalized_build_key,
        )
        _require_boundary_acceptance(
            target,
            accept_assumed_flat=accept_assumed_flat,
        )
        _require_group_fee_scope_acceptance(
            target,
            accept_group_fee_scope=accept_group_fee_scope,
        )
        current_build, current_activation = _resolve_effective_episode_build(
            session,
            normalized_account,
        )
        current_activation_id = (
            int(current_activation.id) if current_activation is not None else None
        )
        current_build_id = (
            int(current_build.id) if current_build is not None else None
        )

        cas_matches = (
            current_activation_id == expected_current_activation_id
            and current_build_id == expected_current_build_id
        )
        if not cas_matches:
            existing = session.execute(
                select(EpisodeBuildActivation).where(
                    EpisodeBuildActivation.activation_key == activation_key
                )
            ).scalar_one_or_none()
            if (
                existing is not None
                and current_activation is not None
                and int(existing.id) == int(current_activation.id)
                and int(existing.episode_build_id) == int(target.id)
            ):
                return _result(
                    normalized_account,
                    target,
                    existing,
                    duplicate=True,
                )
            raise EpisodeBuildActivationError(
                "activation state changed; refresh and retry with the current IDs"
            )

        if (
            current_activation is not None
            and current_build_id == int(target.id)
        ):
            return _result(
                normalized_account,
                target,
                current_activation,
                duplicate=True,
            )

        activation = EpisodeBuildActivation(
            activation_key=activation_key,
            account_key=normalized_account,
            activation_sequence=(
                int(current_activation.activation_sequence) + 1
                if current_activation is not None
                else 1
            ),
            episode_build_id=int(target.id),
            episode_build_key=str(target.build_key).lower(),
            canonical_set_id=int(canonical_link.canonical_set_id),
            canonical_set_sha256=str(
                canonical_link.canonical_set_sha256
            ).lower(),
            previous_activation_id=current_activation_id,
            previous_episode_build_id=current_build_id,
        )
        session.add(activation)
        try:
            session.flush()
        except IntegrityError as exc:
            raise EpisodeBuildActivationError(
                "activation state changed; refresh and retry with the current IDs"
            ) from exc
        return _result(
            normalized_account,
            target,
            activation,
            duplicate=False,
        )

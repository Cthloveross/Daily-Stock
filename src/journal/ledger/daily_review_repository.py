# -*- coding: utf-8 -*-
"""Append-only daily close-review sessions（蓝图 17 §三(a)/(e)-2）。

复制 ``review_repository`` 的修订链模式：每次写入是一条新 revision，
``previous_session_id`` 串链、``content_sha256`` 幂等去重、deny trigger
拒绝任何 UPDATE/DELETE。会话按 ``(account_key, et_date)`` 幂等。

盲评顺序是数据：

* 密封（``sealed_at``）后内容冻结——之后唯一允许追加的修订是「揭示」
  （``revealed_after_seal=True``，其余内容必须逐字节一致）。
* 揭示必须发生在密封之后；未密封请求揭示直接报错。
* 休息日会话（``session_kind='rest_day'``）与交易日会话同一条链。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select

from src.journal.ledger.repository import (
    DEFAULT_LEDGER_ACCOUNT_KEY,
    init_ledger_schema,
)
from src.journal.ledger.review_flow_models import DailyReviewSession
from src.storage import get_db

__all__ = [
    "DailyReviewAppendResult",
    "DailyReviewRepositoryError",
    "DailyReviewSealedError",
    "DailyReviewSessionInput",
    "StoredDailyReviewSession",
    "append_daily_review_session",
    "get_latest_daily_review_session",
    "list_daily_review_session_history",
    "list_latest_daily_review_sessions",
]

_SCHEMA_VERSION = "daily-review-session/1.0"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_KINDS = {"trading_day", "rest_day"}
_VALID_SCORE_BASIS = {"auto", "manual", "missing"}
_MAX_NOTE_CHARACTERS = 2_000
_MAX_ACK_CHARACTERS = 200
_MAX_ACK_ITEMS = 100
_MAX_STEPS_JSON = 4_000
_MAX_SCORES_JSON = 16_000


class DailyReviewRepositoryError(ValueError):
    """Raised when a session payload violates the persistence contract."""


class DailyReviewSealedError(DailyReviewRepositoryError):
    """Raised when a write tries to change a sealed session's content."""


@dataclass(frozen=True)
class DailyReviewSessionInput:
    """Normalized content for one new session revision."""

    et_date: str
    session_kind: str
    steps: Mapping[str, Any] = field(default_factory=dict)
    process_scores: tuple[Mapping[str, Any], ...] = ()
    violation_acks: tuple[Mapping[str, Any], ...] = ()
    note: str = ""
    sealed: bool = False
    revealed_after_seal: bool = False


@dataclass(frozen=True)
class StoredDailyReviewSession:
    """One immutable stored revision, safe for API projection."""

    session_id: int
    account_key: str
    et_date: str
    session_kind: str
    revision: int
    previous_session_id: Optional[int]
    episode_build_id: Optional[int]
    started_at: datetime
    sealed_at: Optional[datetime]
    revealed_at: Optional[datetime]
    revealed_after_seal: bool
    steps: dict[str, Any]
    process_scores: tuple[dict[str, Any], ...]
    violation_acks: tuple[dict[str, Any], ...]
    note: str
    content_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class DailyReviewAppendResult:
    session: StoredDailyReviewSession
    duplicate: bool


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: Optional[datetime]) -> Optional[datetime]:
    return None if value is None else _utc(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_list(value: str) -> tuple[dict[str, Any], ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, dict))


def _normalize_account_key(account_key: str) -> str:
    normalized = str(account_key or "").strip()
    if not normalized or len(normalized) > 64:
        raise DailyReviewRepositoryError(
            "account_key must contain between 1 and 64 characters"
        )
    return normalized


def _normalize_acks(
    values: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            raise DailyReviewRepositoryError("violation_acks items must be objects")
        try:
            episode_id = int(raw["position_episode_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DailyReviewRepositoryError(
                "violation ack requires an integer position_episode_id"
            ) from exc
        if episode_id <= 0 or episode_id in seen:
            raise DailyReviewRepositoryError(
                "violation ack position_episode_id must be positive and unique"
            )
        seen.add(episode_id)
        ack_text = str(raw.get("ack_text") or "").strip()
        if not ack_text:
            raise DailyReviewRepositoryError(
                "violation ack requires a non-empty one-line ack_text"
            )
        if len(ack_text) > _MAX_ACK_CHARACTERS:
            raise DailyReviewRepositoryError(
                f"ack_text must be at most {_MAX_ACK_CHARACTERS} characters"
            )
        normalized.append(
            {
                "position_episode_id": episode_id,
                "lane": str(raw.get("lane") or ""),
                "rule_id": str(raw.get("rule_id") or ""),
                "ack_text": ack_text,
            }
        )
    if len(normalized) > _MAX_ACK_ITEMS:
        raise DailyReviewRepositoryError(
            f"violation_acks must contain at most {_MAX_ACK_ITEMS} items"
        )
    normalized.sort(key=lambda item: item["position_episode_id"])
    return tuple(normalized)


def _normalize_scores(
    values: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            raise DailyReviewRepositoryError(
                "process_scores items must be objects"
            )
        try:
            metric_id = int(raw["metric_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DailyReviewRepositoryError(
                "process score requires an integer metric_id"
            ) from exc
        if not 1 <= metric_id <= 7 or metric_id in seen:
            raise DailyReviewRepositoryError(
                "process score metric_id must be 1..7 and unique"
            )
        seen.add(metric_id)
        basis = str(raw.get("basis") or "").strip()
        if basis not in _VALID_SCORE_BASIS:
            raise DailyReviewRepositoryError(
                "process score basis must be auto, manual, or missing"
            )
        entry: dict[str, Any] = {"metric_id": metric_id, "basis": basis}
        # value 保持原样（数值/字符串/对象皆可），缺席就是缺席。
        if raw.get("value") is not None:
            entry["value"] = raw["value"]
        if raw.get("reason"):
            entry["reason"] = str(raw["reason"])
        if raw.get("note"):
            entry["note"] = str(raw["note"])[:_MAX_ACK_CHARACTERS]
        normalized.append(entry)
    normalized.sort(key=lambda item: item["metric_id"])
    return tuple(normalized)


def normalize_daily_review_session_input(
    value: DailyReviewSessionInput,
) -> DailyReviewSessionInput:
    """Trim, bound, and canonicalize a session before hashing or storage."""

    et_date = str(value.et_date or "").strip()
    if not _ISO_DATE.fullmatch(et_date):
        raise DailyReviewRepositoryError(
            "et_date must be an ET calendar date (YYYY-MM-DD)"
        )
    session_kind = str(value.session_kind or "").strip().lower()
    if session_kind not in _VALID_KINDS:
        raise DailyReviewRepositoryError(
            "session_kind must be trading_day or rest_day"
        )
    note = str(value.note or "").strip()
    if len(note) > _MAX_NOTE_CHARACTERS:
        raise DailyReviewRepositoryError(
            f"note must be at most {_MAX_NOTE_CHARACTERS} characters"
        )
    if not isinstance(value.steps, Mapping):
        raise DailyReviewRepositoryError("steps must be an object")
    steps = {str(key): value.steps[key] for key in value.steps}
    if len(_canonical_json(steps)) > _MAX_STEPS_JSON:
        raise DailyReviewRepositoryError("steps payload is too large")
    scores = _normalize_scores(value.process_scores)
    if len(_canonical_json(list(scores))) > _MAX_SCORES_JSON:
        raise DailyReviewRepositoryError("process_scores payload is too large")
    revealed = bool(value.revealed_after_seal)
    sealed = bool(value.sealed)
    if revealed and not sealed:
        raise DailyReviewRepositoryError(
            "reveal is only recordable on a sealed session"
        )
    return DailyReviewSessionInput(
        et_date=et_date,
        session_kind=session_kind,
        steps=steps,
        process_scores=scores,
        violation_acks=_normalize_acks(value.violation_acks),
        note=note,
        sealed=sealed,
        revealed_after_seal=revealed,
    )


def _content_payload(value: DailyReviewSessionInput) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "et_date": value.et_date,
        "session_kind": value.session_kind,
        "steps": dict(value.steps),
        "process_scores": [dict(item) for item in value.process_scores],
        "violation_acks": [dict(item) for item in value.violation_acks],
        "note": value.note,
        "sealed": value.sealed,
        "revealed_after_seal": value.revealed_after_seal,
    }


def _content_sha256(value: DailyReviewSessionInput) -> str:
    return hashlib.sha256(
        _canonical_json(_content_payload(value)).encode("utf-8")
    ).hexdigest()


def _frozen_content_sha256(value: DailyReviewSessionInput) -> str:
    """Hash of everything except the reveal flag（密封冻结比较用）。"""
    payload = _content_payload(value)
    payload.pop("revealed_after_seal", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _stored(row: DailyReviewSession) -> StoredDailyReviewSession:
    return StoredDailyReviewSession(
        session_id=int(row.id),
        account_key=str(row.account_key),
        et_date=str(row.et_date),
        session_kind=str(row.session_kind),
        revision=int(row.revision),
        previous_session_id=(
            int(row.previous_session_id)
            if row.previous_session_id is not None
            else None
        ),
        episode_build_id=(
            int(row.episode_build_id)
            if row.episode_build_id is not None
            else None
        ),
        started_at=_utc(row.started_at),
        sealed_at=_optional_utc(row.sealed_at),
        revealed_at=_optional_utc(row.revealed_at),
        revealed_after_seal=bool(row.revealed_after_seal),
        steps=_parse_json_object(str(row.steps_json)),
        process_scores=_parse_json_list(str(row.process_scores_json)),
        violation_acks=_parse_json_list(str(row.violation_acks_json)),
        note=str(row.note),
        content_sha256=str(row.content_sha256),
        created_at=_utc(row.created_at),
    )


def _latest_row(
    session: Any,
    account_key: str,
    et_date: str,
) -> Optional[DailyReviewSession]:
    return session.execute(
        select(DailyReviewSession)
        .where(
            DailyReviewSession.account_key == account_key,
            DailyReviewSession.et_date == et_date,
        )
        .order_by(
            DailyReviewSession.revision.desc(),
            DailyReviewSession.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def append_daily_review_session(
    value: DailyReviewSessionInput,
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    episode_build_id: Optional[int] = None,
) -> DailyReviewAppendResult:
    """Append a revision, or return the latest identical revision.

    密封与揭示规则（写入即校验，绝不静默降级）：

    * 未密封 → 内容自由修订；
    * 已密封 → 只接受「揭示」修订（冻结内容逐字节一致 + reveal 翻真）或
      与最新修订完全一致的幂等重放；其它任何差异抛
      :class:`DailyReviewSealedError`；
    * 揭示不可撤销：已揭示后 reveal 翻假同样报错。
    """
    account_key = _normalize_account_key(account_key)
    normalized = normalize_daily_review_session_input(value)
    content_sha256 = _content_sha256(normalized)

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        if db._is_sqlite_engine:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        latest = _latest_row(session, account_key, normalized.et_date)
        now = datetime.now(timezone.utc)

        if latest is not None and latest.content_sha256 == content_sha256:
            return DailyReviewAppendResult(
                session=_stored(latest), duplicate=True
            )

        sealed_at: Optional[datetime] = None
        revealed_at: Optional[datetime] = None
        started_at = now
        if latest is not None:
            started_at = _utc(latest.started_at)
            latest_sealed = latest.sealed_at is not None
            latest_revealed = bool(latest.revealed_after_seal)
            if latest_sealed:
                if not normalized.sealed:
                    raise DailyReviewSealedError(
                        "sealed session cannot be unsealed"
                    )
                frozen_latest = _frozen_content_sha256(
                    DailyReviewSessionInput(
                        et_date=str(latest.et_date),
                        session_kind=str(latest.session_kind),
                        steps=_parse_json_object(str(latest.steps_json)),
                        process_scores=_parse_json_list(
                            str(latest.process_scores_json)
                        ),
                        violation_acks=_parse_json_list(
                            str(latest.violation_acks_json)
                        ),
                        note=str(latest.note),
                        sealed=True,
                        revealed_after_seal=latest_revealed,
                    )
                )
                if _frozen_content_sha256(normalized) != frozen_latest:
                    raise DailyReviewSealedError(
                        "sealed session content is frozen; only a reveal "
                        "revision may be appended"
                    )
                if latest_revealed and not normalized.revealed_after_seal:
                    raise DailyReviewSealedError(
                        "reveal cannot be revoked once recorded"
                    )
                sealed_at = _utc(latest.sealed_at)
                revealed_at = _optional_utc(latest.revealed_at)
                if normalized.revealed_after_seal and revealed_at is None:
                    revealed_at = now
            else:
                if normalized.revealed_after_seal:
                    # 顺序本身是数据：揭示必须是密封修订**之后**的独立修订，
                    # 不允许在同一笔写入里既密封又揭示。
                    raise DailyReviewRepositoryError(
                        "reveal must be appended after an already-sealed "
                        "revision"
                    )
                if normalized.sealed:
                    sealed_at = now
        else:
            if normalized.revealed_after_seal:
                raise DailyReviewRepositoryError(
                    "reveal must be appended after an already-sealed revision"
                )
            if normalized.sealed:
                sealed_at = now

        row = DailyReviewSession(
            account_key=account_key,
            et_date=normalized.et_date,
            session_kind=normalized.session_kind,
            revision=1 if latest is None else int(latest.revision) + 1,
            previous_session_id=int(latest.id) if latest is not None else None,
            episode_build_id=(
                episode_build_id
                if episode_build_id is not None
                else (
                    int(latest.episode_build_id)
                    if latest is not None and latest.episode_build_id is not None
                    else None
                )
            ),
            started_at=started_at,
            sealed_at=sealed_at,
            revealed_at=revealed_at,
            revealed_after_seal=normalized.revealed_after_seal,
            steps_json=_canonical_json(dict(normalized.steps)),
            process_scores_json=_canonical_json(
                [dict(item) for item in normalized.process_scores]
            ),
            violation_acks_json=_canonical_json(
                [dict(item) for item in normalized.violation_acks]
            ),
            note=normalized.note,
            content_sha256=content_sha256,
        )
        session.add(row)
        session.flush()
        return DailyReviewAppendResult(session=_stored(row), duplicate=False)


def get_latest_daily_review_session(
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    et_date: str,
) -> Optional[StoredDailyReviewSession]:
    """Return the newest revision for one ET date, or None."""
    account_key = _normalize_account_key(account_key)
    if not _ISO_DATE.fullmatch(str(et_date or "")):
        raise DailyReviewRepositoryError(
            "et_date must be an ET calendar date (YYYY-MM-DD)"
        )
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        row = _latest_row(session, account_key, et_date)
        return None if row is None else _stored(row)


def list_daily_review_session_history(
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    et_date: str,
) -> tuple[StoredDailyReviewSession, ...]:
    """Return every immutable revision for one ET date, newest first."""
    account_key = _normalize_account_key(account_key)
    if not _ISO_DATE.fullmatch(str(et_date or "")):
        raise DailyReviewRepositoryError(
            "et_date must be an ET calendar date (YYYY-MM-DD)"
        )
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(DailyReviewSession)
            .where(
                DailyReviewSession.account_key == account_key,
                DailyReviewSession.et_date == et_date,
            )
            .order_by(
                DailyReviewSession.revision.desc(),
                DailyReviewSession.id.desc(),
            )
        ).scalars()
        return tuple(_stored(row) for row in rows)


def list_latest_daily_review_sessions(
    *,
    account_key: str = DEFAULT_LEDGER_ACCOUNT_KEY,
    limit: int = 30,
) -> tuple[StoredDailyReviewSession, ...]:
    """Latest revision per ET date, newest date first (休息纪律连续计数用)."""
    account_key = _normalize_account_key(account_key)
    if limit < 1 or limit > 400:
        raise DailyReviewRepositoryError("limit must be between 1 and 400")
    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        rows = session.execute(
            select(DailyReviewSession)
            .where(DailyReviewSession.account_key == account_key)
            .order_by(
                DailyReviewSession.et_date.desc(),
                DailyReviewSession.revision.desc(),
                DailyReviewSession.id.desc(),
            )
        ).scalars()
        latest_by_date: dict[str, StoredDailyReviewSession] = {}
        for row in rows:
            key = str(row.et_date)
            if key not in latest_by_date:
                latest_by_date[key] = _stored(row)
            if len(latest_by_date) >= limit:
                break
        return tuple(
            latest_by_date[key] for key in sorted(latest_by_date, reverse=True)
        )

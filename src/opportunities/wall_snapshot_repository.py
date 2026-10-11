# -*- coding: utf-8 -*-
"""Bounded, idempotent, fail-closed writer for ``option_wall_daily_snapshots``.

约束（每一条都是刻意的，改动前先读 ``wall_snapshot_models`` 的模块说明）：

* **零新增取数路径**：本模块**不**自己调 provider。调用方传入的是既有期权墙
  车道（``POST /opportunities/option-walls`` 及其缓存）已经算好的 payload，
  这里只做投影与落库。任何在这里新开 fetch 的改动都是错的。
* **有界**：只记录日内深度层的标的，绝不扇出到全部 universe。上限由
  :data:`MAX_SNAPSHOT_TICKERS` 兜底。
* **幂等**：``(market_date_et, ticker)`` 一个槽位。重复调用第一次写入生效，
  之后返回 ``duplicate=True`` 且**不**覆盖——表本身也由 deny trigger 拒绝
  UPDATE/DELETE，两道防线。
* **fail closed**：``state`` 不是 ``ready``/``partial``、spot 不可用、或
  ``coverage.coverage_percent`` 缺席时**什么都不写**（``skipped`` + 原因），
  绝不以 0 冒充一次观测。部分覆盖（有真实覆盖率读数）如实写入并带上它
  自己的 ``coverage_percent``。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select

from src.opportunities.repository import canonical_sha256
from src.storage import Base, DatabaseManager, get_db

from src.opportunities.wall_snapshot_models import OptionWallDailySnapshot

__all__ = [
    "MAX_SNAPSHOT_TICKERS",
    "OptionWallSnapshotError",
    "SnapshotWriteResult",
    "build_snapshot_key",
    "init_wall_snapshot_schema",
    "list_wall_snapshots",
    "record_wall_snapshots",
]


#: 深度层上限的兜底钳制（与 ``INTRADAY_DEEP_LANE_MAX`` 的 1..20 同量级）。
#: 这张表的意义是「少数几个自己真的会做的标的的逐日序列」，不是全市场扫描。
MAX_SNAPSHOT_TICKERS = 20

_APPEND_ONLY_TABLES = ("option_wall_daily_snapshots",)
_APPEND_ONLY_GUARD_MESSAGE = "option wall snapshot rows are append-only"

#: 只有这两个状态携带真实观测；其余一律不写。
_RECORDABLE_STATES = frozenset({"ready", "partial"})


class OptionWallSnapshotError(RuntimeError):
    """Invalid input for the option-wall snapshot writer."""


@dataclass(frozen=True)
class SnapshotWriteResult:
    """One ticker's outcome — written, replayed, or explicitly skipped."""

    ticker: str
    market_date_et: str
    state: str
    written: bool
    duplicate: bool
    reason: Optional[str] = None
    coverage_percent: Optional[float] = None


def build_snapshot_key(market_date_et: str, ticker: str) -> str:
    """Idempotency slot key — deliberately excludes ``fetched_at``.

    Re-running the writer later the same ET day must land on the *same* slot,
    so the key may never contain a wall-clock stamp.
    """

    return "owds_" + canonical_sha256(
        {"market_date_et": market_date_et, "ticker": ticker}
    )


def init_wall_snapshot_schema(
    db_manager: Optional[DatabaseManager] = None,
) -> None:
    """Create the table and install the append-only deny triggers."""

    db = db_manager or get_db()
    Base.metadata.create_all(
        db._engine,
        tables=(OptionWallDailySnapshot.__table__,),
    )
    if db._engine.dialect.name == "sqlite":
        with db._engine.begin() as connection:
            for table_name in _APPEND_ONLY_TABLES:
                for operation in ("UPDATE", "DELETE"):
                    connection.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS "
                        f"trg_{table_name}_{operation.lower()}_immutable "
                        f"BEFORE {operation} ON {table_name} "
                        "BEGIN SELECT RAISE(ABORT, "
                        f"'{_APPEND_ONLY_GUARD_MESSAGE}'); END"
                    )


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _top_level(walls: Mapping[str, Any], key: str) -> tuple[
    Optional[float], Optional[float]
]:
    levels = walls.get(key) or ()
    for level in levels:
        if not isinstance(level, Mapping):
            continue
        strike = _finite(level.get("strike"))
        metric = _finite(level.get("metric_value"))
        if strike is not None and strike > 0:
            return strike, metric
    return None, None


def _parse_fetched_at(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _project(
    item: Mapping[str, Any],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Project one wall payload item into ``(row values, skip reason)``.

    ``(None, reason)`` is the fail-closed path: a failed, disabled,
    spot-less, or coverage-less read records nothing at all rather than a
    row of zeros — the table is append-only, so a zero-filled row could
    never be corrected later.
    """

    state = str(item.get("state") or "").strip()
    if state not in _RECORDABLE_STATES:
        return None, (
            f"该标的本次未取得可用墙位观测（state={state or 'unknown'}），"
            "按 fail-closed 不写入任何行"
        )
    spot = _finite(item.get("spot"))
    if spot is None or spot <= 0:
        return None, "该次响应缺可用 spot，按 fail-closed 不写入任何行"

    totals = item.get("totals")
    ratios = item.get("ratios")
    if not isinstance(totals, Mapping) or not isinstance(ratios, Mapping):
        return None, "该次响应缺 totals/ratios 区块，按 fail-closed 不写入任何行"

    def _total(key: str) -> Optional[float]:
        value = _finite(totals.get(key))
        return value if value is not None and value >= 0 else None

    call_oi = _total("call_oi")
    put_oi = _total("put_oi")
    call_volume = _total("call_volume")
    put_volume = _total("put_volume")
    if None in (call_oi, put_oi, call_volume, put_volume):
        return None, "该次响应 OI/量合计不完整，按 fail-closed 不写入任何行"

    def _ratio(key: str) -> tuple[Optional[float], Optional[str]]:
        block = ratios.get(key)
        if not isinstance(block, Mapping):
            return None, "该次响应缺少比例字段"
        value = _finite(block.get("value"))
        reason = block.get("reason")
        if value is None:
            return None, str(reason or "比例无定义且未给出原因")
        return value, None

    oi_ratio, oi_reason = _ratio("call_put_oi_ratio")
    volume_ratio, volume_reason = _ratio("call_put_volume_ratio")

    walls = item.get("walls")
    walls = walls if isinstance(walls, Mapping) else {}
    top_call_strike, top_call_oi = _top_level(walls, "call_oi")
    top_put_strike, top_put_oi = _top_level(walls, "put_oi")
    gamma_strike, _gamma_value = _top_level(walls, "gross_gamma_concentration")

    center = item.get("oi_weighted_center")
    center_strike = (
        _finite(center.get("strike")) if isinstance(center, Mapping) else None
    )

    coverage = item.get("coverage")
    coverage_percent = (
        _finite(coverage.get("coverage_percent"))
        if isinstance(coverage, Mapping)
        else None
    )
    # 覆盖率缺席 ≠ 覆盖率 0：这张表 append-only、写错无法更正，缺
    # coverage.coverage_percent 时整行拒写（fail closed），绝不 0 回填成
    # 「读到了 0% 覆盖」这样一条假观测。部分覆盖（有读数）仍如实记录。
    if coverage_percent is None:
        return None, (
            "该次响应缺 coverage.coverage_percent：不可变快照拒绝以 0 冒充"
            "覆盖率，按 fail-closed 不写入任何行"
        )
    coverage_percent = max(0.0, min(100.0, coverage_percent))

    return {
        "spot": spot,
        "call_oi_total": call_oi,
        "put_oi_total": put_oi,
        "call_volume_total": call_volume,
        "put_volume_total": put_volume,
        "call_put_oi_ratio": oi_ratio,
        "call_put_oi_ratio_reason": oi_reason,
        "call_put_volume_ratio": volume_ratio,
        "call_put_volume_ratio_reason": volume_reason,
        "top_call_wall_strike": top_call_strike,
        "top_call_wall_oi": top_call_oi,
        "top_put_wall_strike": top_put_strike,
        "top_put_wall_oi": top_put_oi,
        "gross_gamma_concentration_strike": gamma_strike,
        "oi_weighted_center_strike": center_strike,
        "coverage_percent": coverage_percent,
        "source": str(item.get("source") or "unknown"),
        "formula_version": str(item.get("formula_version") or "unknown"),
        "quote_as_of": (
            str(item["quote_as_of"]).strip()[:64]
            if item.get("quote_as_of")
            else None
        ),
        "fetched_at": _parse_fetched_at(item.get("fetched_at")),
    }, None


def record_wall_snapshots(
    payload: Mapping[str, Any],
    *,
    tickers: Optional[Iterable[str]] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> tuple[SnapshotWriteResult, ...]:
    """Append today's wall aggregates for the deep-lane tickers in ``payload``.

    ``payload`` is an already-computed ``POST /opportunities/option-walls``
    response body — **this function never fetches anything**.  ``tickers``
    optionally narrows the payload's items further (the caller's deep lane);
    anything outside it is ignored rather than fanned out to.
    """

    market_date_et = str(payload.get("market_date_et") or "").strip()
    if not market_date_et:
        raise OptionWallSnapshotError("payload is missing market_date_et")
    try:
        parsed_date = date.fromisoformat(market_date_et)
    except ValueError as exc:
        raise OptionWallSnapshotError(
            "market_date_et must be an ET calendar date (YYYY-MM-DD)"
        ) from exc

    items: Sequence[Any] = payload.get("items") or ()
    allowed: Optional[set[str]] = None
    if tickers is not None:
        allowed = {
            str(value or "").strip().upper()
            for value in tickers
            if str(value or "").strip()
        }

    selected: list[tuple[str, Mapping[str, Any]]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        if allowed is not None and ticker not in allowed:
            continue
        selected.append((ticker, item))
        if len(selected) >= MAX_SNAPSHOT_TICKERS:
            # 有界：越过上限就停，绝不因为上游给多了就扇出到全部标的。
            break

    if not selected:
        return ()

    init_wall_snapshot_schema(db_manager)
    db = db_manager or get_db()
    results: list[SnapshotWriteResult] = []

    with db.session_scope() as session:
        for ticker, item in selected:
            state = str(item.get("state") or "unknown")
            values, skip_reason = _project(item)
            if values is None:
                results.append(
                    SnapshotWriteResult(
                        ticker=ticker,
                        market_date_et=market_date_et,
                        state=state,
                        written=False,
                        duplicate=False,
                        reason=(
                            skip_reason
                            or "该标的本次未取得可用墙位观测，"
                            "按 fail-closed 不写入任何行"
                        ),
                    )
                )
                continue

            snapshot_key = build_snapshot_key(market_date_et, ticker)
            existing = session.execute(
                select(OptionWallDailySnapshot.id).where(
                    OptionWallDailySnapshot.snapshot_key == snapshot_key
                )
            ).scalar_one_or_none()
            if existing is not None:
                results.append(
                    SnapshotWriteResult(
                        ticker=ticker,
                        market_date_et=market_date_et,
                        state=state,
                        written=False,
                        duplicate=True,
                        reason="当日该标的已有快照，append-only 下不覆盖",
                        coverage_percent=values["coverage_percent"],
                    )
                )
                continue

            session.add(
                OptionWallDailySnapshot(
                    snapshot_key=snapshot_key,
                    market_date_et=parsed_date,
                    ticker=ticker,
                    recorded_at=datetime.now(timezone.utc),
                    **values,
                )
            )
            session.flush()
            results.append(
                SnapshotWriteResult(
                    ticker=ticker,
                    market_date_et=market_date_et,
                    state=state,
                    written=True,
                    duplicate=False,
                    coverage_percent=values["coverage_percent"],
                )
            )

    return tuple(results)


def list_wall_snapshots(
    *,
    ticker: Optional[str] = None,
    limit: int = 200,
    db_manager: Optional[DatabaseManager] = None,
) -> tuple[dict[str, Any], ...]:
    """Read back the stored series, newest first — pure SELECT."""

    if limit < 1 or limit > 2000:
        raise OptionWallSnapshotError("limit must be between 1 and 2000")
    init_wall_snapshot_schema(db_manager)
    db = db_manager or get_db()
    with db.session_scope() as session:
        statement = select(OptionWallDailySnapshot)
        if ticker:
            statement = statement.where(
                OptionWallDailySnapshot.ticker == str(ticker).strip().upper()
            )
        rows = (
            session.execute(
                statement.order_by(
                    OptionWallDailySnapshot.market_date_et.desc(),
                    OptionWallDailySnapshot.ticker.asc(),
                ).limit(limit)
            )
            .scalars()
            .all()
        )
        return tuple(
            {
                "market_date_et": row.market_date_et.isoformat(),
                "ticker": row.ticker,
                "spot": row.spot,
                "call_oi_total": row.call_oi_total,
                "put_oi_total": row.put_oi_total,
                "call_volume_total": row.call_volume_total,
                "put_volume_total": row.put_volume_total,
                "call_put_oi_ratio": row.call_put_oi_ratio,
                "call_put_oi_ratio_reason": row.call_put_oi_ratio_reason,
                "call_put_volume_ratio": row.call_put_volume_ratio,
                "call_put_volume_ratio_reason": row.call_put_volume_ratio_reason,
                "top_call_wall_strike": row.top_call_wall_strike,
                "top_call_wall_oi": row.top_call_wall_oi,
                "top_put_wall_strike": row.top_put_wall_strike,
                "top_put_wall_oi": row.top_put_wall_oi,
                "gross_gamma_concentration_strike": (
                    row.gross_gamma_concentration_strike
                ),
                "oi_weighted_center_strike": row.oi_weighted_center_strike,
                "coverage_percent": row.coverage_percent,
                "source": row.source,
                "formula_version": row.formula_version,
                "quote_as_of": row.quote_as_of,
                "fetched_at": row.fetched_at,
            }
            for row in rows
        )

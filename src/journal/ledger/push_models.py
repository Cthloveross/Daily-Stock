# -*- coding: utf-8 -*-
"""推送台账表（蓝图 17 §三(e)-4，Phase B）。

一张 append-only 表 ``journal_v2_push_events``，两类行：

* ``record_kind='decision'`` —— 每一条分级决定（P0X/P0-P3 与系统通知），含
  当刻评估的七条件 JSON、去重键、投递通道与被抑制原因。**每条被抑制/
  降级的候选也必须落行**（蓝图硬红线：无静默丢弃）。
* ``record_kind='delivery'`` —— 一次 Telegram 发送的结果行（成功/失败），
  经 ``message_key`` 关联同一条消息覆盖的全部 decision 行。

Append-only：与其余 journal_v2 账本同用 deny trigger（repository 的
``_APPEND_ONLY_TABLE_NAMES``）；此外本模块注册 after_create 钩子（表级 +
metadata 级，模式同 ``position_snapshot_models``），保证无论建表走
``Base.metadata.create_all`` 还是 ``init_ledger_schema``，触发器都随表
就位——不存在「建表后、首次 repo 调用前」的无保护窗口。发送结果晚于
决定产生，因此以第二行追加，绝不回写 decision 行。重启后的去重重建只读
当日 decision 行。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)

from src.storage import Base

__all__ = ["PushEvent"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PushEvent(Base):
    """One immutable push-ledger row (decision or delivery)."""

    __tablename__ = "journal_v2_push_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    push_id = Column(String(32), nullable=False)
    record_kind = Column(String(16), nullable=False)
    et_date = Column(String(10), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)
    tier = Column(String(8), nullable=False)
    # P0X 的盘前/盘后预算必须从不可变 decision 证据重建，不能在重启后
    # 依据「当前时间」反推。普通 tier 也可带此字段用于展示；历史行为空。
    session_scope = Column(String(16))
    rule = Column(String(64), nullable=False)
    ticker = Column(String(16))
    text = Column(Text, nullable=False)
    dedupe_key_json = Column(Text)
    conditions_json = Column(Text)
    lane_rule_id = Column(String(16))
    playbook_ref = Column(String(64))
    option_mid = Column(Numeric(18, 6))
    option_spread_pct = Column(Numeric(10, 4))
    gate_state = Column(String(16))
    delivery_channel = Column(String(24), nullable=False)
    suppressed = Column(Boolean, nullable=False, default=False)
    suppressed_reason = Column(String(128))
    message_key = Column(String(32))
    telegram_ok = Column(Boolean)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        UniqueConstraint("push_id", name="uq_jv2_push_event_push_id"),
        CheckConstraint(
            "record_kind IN ('decision', 'delivery')",
            name="ck_jv2_push_event_kind",
        ),
        CheckConstraint(
            "tier IN ('P0X', 'P0', 'P1', 'P2', 'P3', 'system')",
            name="ck_jv2_push_event_tier",
        ),
        CheckConstraint(
            "session_scope IS NULL OR session_scope IN ("
            "'premarket', 'regular', 'afterhours'"
            ")",
            name="ck_jv2_push_event_session_scope",
        ),
        CheckConstraint(
            # P0X 是盘外专用层；delivery 通过 message_key 关联带盘段的
            # decision，因此无需重复存 session_scope。
            "tier != 'P0X' OR record_kind = 'delivery' OR "
            "(session_scope IS NOT NULL AND "
            "session_scope IN ('premarket', 'afterhours'))",
            name="ck_jv2_push_event_p0x_session_scope",
        ),
        CheckConstraint(
            "delivery_channel IN ("
            "'telegram', 'telegram_silent', 'web_only', 'ledger_only', 'none'"
            ")",
            name="ck_jv2_push_event_channel",
        ),
        CheckConstraint(
            # 被抑制的行必须写明原因——无静默丢弃（蓝图硬红线）。
            "suppressed = 0 OR suppressed_reason IS NOT NULL",
            name="ck_jv2_push_event_suppressed_reason",
        ),
        Index("ix_jv2_push_events_scope", "et_date", "record_kind", "tier"),
        Index("ix_jv2_push_events_ticker", "et_date", "ticker"),
    )


def _push_guard_trigger_ddl(operation: str) -> str:
    """Authoritative deny-trigger DDL for this table (single source).

    Lazy import: ``repository`` imports this module to register the model on
    the shared metadata, so a module-level import here would be circular.
    """
    from src.journal.ledger.repository import ledger_guard_trigger_ddl

    return ledger_guard_trigger_ddl(PushEvent.__tablename__, operation)


def _install_push_guards_after_create(_target, connection, **_kwargs) -> None:
    """Install append-only guards the moment the table itself is created.

    ``DatabaseManager`` runs ``Base.metadata.create_all`` before any
    repository method necessarily calls ``init_ledger_schema``; without this
    table-level hook a freshly created ``journal_v2_push_events`` would sit
    unguarded (UPDATE/DELETE silently succeed) until the first push-ledger
    repo touch.  Same pattern as ``position_snapshot_models``.
    """
    if connection.dialect.name != "sqlite":
        return
    for operation in ("UPDATE", "DELETE"):
        connection.exec_driver_sql(_push_guard_trigger_ddl(operation))


event.listen(
    PushEvent.__table__, "after_create", _install_push_guards_after_create
)


def _repair_push_guards_after_create_all(
    _metadata,
    connection,
    **_kwargs,
) -> None:
    """Repair guards for a push table that predates the table-level hook.

    The table-level hook covers newly created tables.  This metadata-level
    repair also runs when ``create_all(checkfirst=True)`` finds an existing
    database, installing the missing triggers idempotently
    (``CREATE TRIGGER IF NOT EXISTS``); it skips databases whose partial
    schema lacks the table.
    """
    if connection.dialect.name != "sqlite":
        return
    existing_tables = set(inspect(connection).get_table_names())
    if PushEvent.__tablename__ not in existing_tables:
        return
    for operation in ("UPDATE", "DELETE"):
        connection.exec_driver_sql(_push_guard_trigger_ddl(operation))


event.listen(
    Base.metadata,
    "after_create",
    _repair_push_guards_after_create_all,
)

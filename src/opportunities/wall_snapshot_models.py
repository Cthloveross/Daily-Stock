# -*- coding: utf-8 -*-
"""Append-only daily snapshot of option-wall aggregates, keyed (date, ticker).

存在的唯一理由：让两个**目前无法检验**的假设在几个月后变得可检验。

用户问了两个问题——「近期的高 call 比例是不是意味着上涨？」与「一个票今天
最可能向哪个点靠近？」。本仓库**没有任何历史 OI 序列**，因此这两个假设
（call/put 比例预测方向、最大痛点 / gamma 钉仓有引力）在用户自己的数据上
一次都没有被检验过。没有历史就没有回溯，没有回溯就只能凭感觉——这张表就是
把「凭感觉」变成「有样本」的那一步。

设计遵循本仓库既有证据表的约定（对齐 ``opportunity_snapshot_runs``）：

* **append-only**：``trg_*_immutable`` 一对 SQLite deny trigger 拒绝
  UPDATE 与 DELETE，与 journal_v2 / opportunity_* 家族同一套模式；
* **as-of 双时间戳**：``quote_as_of``（行情观测时刻）与 ``fetched_at``
  （抓取时刻）分开记，绝不用一个冒充另一个；
* **source + formula_version** 随行落库，口径变了能分辨；
* **每 (market_date_et, ticker) 一行**，写入幂等：同日同标的重复调用
  第一次写入生效，之后原样返回，绝不覆盖；
* **fail closed**：抓取失败**什么都不写**（不是写 0）；部分覆盖如实写入
  并带上它自己的 ``coverage_percent``，让读的人自己判断够不够用。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from src.storage import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OptionWallDailySnapshot(Base):
    """One immutable per-(ET date, ticker) reading of the wall aggregates."""

    __tablename__ = "option_wall_daily_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: ``owds_<sha256(market_date_et|ticker)>`` —— 幂等槽位键。
    snapshot_key = Column(String(128), nullable=False)
    market_date_et = Column(Date, nullable=False)
    ticker = Column(String(32), nullable=False)

    spot = Column(Float, nullable=False)

    call_oi_total = Column(Float, nullable=False)
    put_oi_total = Column(Float, nullable=False)
    call_volume_total = Column(Float, nullable=False)
    put_volume_total = Column(Float, nullable=False)

    #: 分母为 0 时显式 NULL + reason，绝不以 0 或 1 冒充「有定义」。
    call_put_oi_ratio = Column(Float)
    call_put_oi_ratio_reason = Column(String(255))
    call_put_volume_ratio = Column(Float)
    call_put_volume_ratio_reason = Column(String(255))

    top_call_wall_strike = Column(Float)
    top_call_wall_oi = Column(Float)
    top_put_wall_strike = Column(Float)
    top_put_wall_oi = Column(Float)
    gross_gamma_concentration_strike = Column(Float)

    #: Σ(strike × OI) / Σ(OI)。**不是** max pain 预测：是否有引力未验证。
    oi_weighted_center_strike = Column(Float)

    coverage_percent = Column(Float, nullable=False)
    source = Column(String(64), nullable=False)
    formula_version = Column(String(64), nullable=False)
    #: 行情观测时刻（provider 给的 update_time），与抓取时刻分开。
    quote_as_of = Column(String(64))
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_key",
            name="uq_option_wall_daily_snapshot_key",
        ),
        UniqueConstraint(
            "market_date_et",
            "ticker",
            name="uq_option_wall_daily_snapshot_slot",
        ),
        CheckConstraint("spot > 0", name="ck_option_wall_daily_snapshot_spot"),
        CheckConstraint(
            "coverage_percent >= 0 AND coverage_percent <= 100",
            name="ck_option_wall_daily_snapshot_coverage",
        ),
        CheckConstraint(
            "call_oi_total >= 0 AND put_oi_total >= 0 "
            "AND call_volume_total >= 0 AND put_volume_total >= 0",
            name="ck_option_wall_daily_snapshot_totals",
        ),
        # 比例要么有值、要么有原因，不允许「两个都空」这种静默缺失。
        CheckConstraint(
            "(call_put_oi_ratio IS NOT NULL) "
            "OR (call_put_oi_ratio_reason IS NOT NULL)",
            name="ck_option_wall_daily_snapshot_oi_ratio_explained",
        ),
        CheckConstraint(
            "(call_put_volume_ratio IS NOT NULL) "
            "OR (call_put_volume_ratio_reason IS NOT NULL)",
            name="ck_option_wall_daily_snapshot_volume_ratio_explained",
        ),
        Index(
            "ix_option_wall_daily_snapshot_ticker_date",
            "ticker",
            "market_date_et",
        ),
    )

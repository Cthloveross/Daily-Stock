# -*- coding: utf-8 -*-
"""MAE/MFE（最大不利/有利偏移）——标的 5m 近似口径的纯函数实现（Phase A）。

真源：`New-docs/phase1/17_PRO_PRACTICE_BLUEPRINT.md` §三(b)。要点原样落地：

* 期权盘中逐笔路径不可得（Tradervue 官方公开放弃期权 MFE/MAE），因此一律用
  **持有窗口内标的 5m bar 路径**近似，并诚实标注「标的近似，非期权价格路径」。
* **不做** BS/delta 折算成期权盈亏——那是假精度。
* fail-closed：无 bar、部分覆盖、方向不可判定都给显式 status 与原因，绝不以
  0 冒充偏移；超出 5m 保留窗口的回合由调用方写 `missing_bars` 永久标缺。
* 只读诊断：本模块的输出**禁止**接到任何止损/持有参数或建议上
  （:data:`EXCURSION_DISCLAIMER` 必须随图原样携带，前端有测试锁定）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from src.opportunities.intraday_bursts import filter_regular_session_bars

__all__ = [
    "EXCURSION_CODE_VERSION",
    "EXCURSION_SOURCE",
    "EXCURSION_DISCLAIMER",
    "EXCURSION_LIMITATIONS",
    "STATUS_MISSING_BARS",
    "STATUS_NOT_APPLICABLE",
    "STATUS_PARTIAL",
    "STATUS_READY",
    "ExcursionEpisode",
    "ExcursionResult",
    "compute_atr14",
    "compute_excursion",
    "determine_exposure",
]

_ET = ZoneInfo("America/New_York")
_RTH_OPEN = time(9, 30)
_RTH_LAST_BAR_START = time(15, 55)

# 计算口径版本：公式或窗口口径变更时必须递增，旧行永不改写（append-only）。
# 1.1（2026-08-25 复核修复）：exposure 接受账本真实词汇 "equity"；时间戳一律
# UTC 入库；短于一根 5m bar 的持有改判 not_applicable（粒度限制，非数据缺失）；
# u0_flag 区分「盘外开仓」与「入场日无 bar」。1.0 旧行保留（其中时间戳为
# ET wall-clock 存储，读取时不得当作 UTC 重新贴标）。
EXCURSION_CODE_VERSION = "underlying-5m/1.1"
EXCURSION_SOURCE = "underlying_5m"

STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_MISSING_BARS = "missing_bars"
STATUS_NOT_APPLICABLE = "not_applicable"

# 永久免责（不可关闭，前端组件与测试锁定同一份文案；勿改写措辞）。
EXCURSION_DISCLAIMER = (
    "本图不用于设置止损；不得由此推导出场/持有参数。"
    "对尾部结构账户，按 MAE 分位收紧止损最大概率截掉的恰是右尾。"
    "持仓分组（日内/隔夜）存在内生性。"
)

EXCURSION_LIMITATIONS: tuple[str, ...] = (
    "标的近似口径：用持有窗口内标的 5m bar 的 high/low 近似偏移，"
    "不是期权价格路径，也不做 BS/delta 折算（假精度）。",
    "仅常规时段（RTH）bar：隔夜/盘前盘后路径不可见，跨日跳空由次日首根 bar 体现。",
    "5m 数据仅可回溯约 60 天：更早的回合永久标缺，绝不以 0 或日线冒充。",
    "出场 bar 溢出：窗口按 bar 开始时间截取，平仓时刻所在的整根 bar 计入偏移，"
    "因此平仓之后至多约 5 分钟的行情也会被计入 MAE/MFE。",
    EXCURSION_DISCLAIMER,
)


@dataclass(frozen=True)
class ExcursionEpisode:
    """一个回合被压缩到偏移计算所需的最小事实集合。"""

    asset_type: str
    direction: str
    option_right: Optional[str]
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]


@dataclass(frozen=True)
class ExcursionResult:
    """一次偏移计算的完整结果（含 fail-closed 状态与原因）。"""

    status: str
    status_reason: Optional[str]
    exposure: Optional[int]
    u0: Optional[float]
    u0_at: Optional[datetime]
    u0_flag: Optional[str]
    mfe_underlying_pct: Optional[float]
    mfe_at: Optional[datetime]
    mae_underlying_pct: Optional[float]
    mae_at: Optional[datetime]
    mae_before_mfe: Optional[bool]
    atr14: Optional[float]
    mfe_atr: Optional[float]
    mae_atr: Optional[float]
    bars_used: int
    coverage_start: Optional[datetime]
    coverage_end: Optional[datetime]
    missing_sessions: tuple[str, ...]


def _failed(status: str, reason: str) -> ExcursionResult:
    return ExcursionResult(
        status=status,
        status_reason=reason,
        exposure=None,
        u0=None,
        u0_at=None,
        u0_flag=None,
        mfe_underlying_pct=None,
        mfe_at=None,
        mae_underlying_pct=None,
        mae_at=None,
        mae_before_mfe=None,
        atr14=None,
        mfe_atr=None,
        mae_atr=None,
        bars_used=0,
        coverage_start=None,
        coverage_end=None,
        missing_sessions=(),
    )


def determine_exposure(
    asset_type: str,
    direction: str,
    option_right: Optional[str],
) -> Optional[int]:
    """标的敞口符号：+1（涨对我有利）/ −1（跌对我有利）/ None（不可判定）。

    蓝图判定表：long call / short put / long stock → ``+1``；
    long put / short call / short stock → ``−1``；组合腿、资产类型或
    右侧不明 → ``None``（fail-closed，绝不猜）。

    资产类型词汇以账本实际输出为准：Episode 构建层（``ledger/episodes.py``）
    对正股写 ``"equity"``，这里同义接受；``"stock"`` 保留为别名。
    """
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"long", "short"}:
        return None
    sign = 1 if normalized_direction == "long" else -1
    normalized_asset = str(asset_type or "").strip().lower()
    if normalized_asset in {"equity", "stock"}:
        return sign
    if normalized_asset == "option":
        right = str(option_right or "").strip().upper()
        if right in {"C", "CALL"}:
            return sign
        if right in {"P", "PUT"}:
            return -sign
        return None
    return None


def _to_et(value: datetime) -> datetime:
    if value.tzinfo is None:
        # 无时区时间无法诚实换算，调用方必须传 aware datetime。
        raise ValueError("excursion timestamps must be timezone-aware")
    return value.astimezone(_ET)


def _has_expected_bar_start(opened_et: datetime, closed_et: datetime) -> bool:
    """窗口内是否存在**理论上**应有的 RTH 5m bar 开始时刻（对齐 5 分钟网格）。

    返回 False ＝ 即使数据完备，这个窗口也不可能含任何 bar 开始时间——
    是 5m 粒度限制而非数据缺失。只在窗口很短时调用（循环次数极少）。
    """
    cursor = opened_et.replace(second=0, microsecond=0)
    if cursor.minute % 5:
        cursor += timedelta(minutes=5 - cursor.minute % 5)
    elif cursor < opened_et:
        cursor += timedelta(minutes=5)
    while cursor <= closed_et:
        if (
            cursor.weekday() < 5
            and _RTH_OPEN <= cursor.time() <= _RTH_LAST_BAR_START
        ):
            return True
        cursor += timedelta(minutes=5)
    return False


def _expected_sessions(opened_et: datetime, closed_et: datetime) -> list[str]:
    """持有窗口内**理论上**有 RTH 暴露的自然日（周一至周五）。

    开仓晚于当日 15:55（末根 5m bar 开始）→ 当日无可用 RTH bar，窗口顺延；
    平仓早于 09:30 → 当日 RTH 未开始，窗口止于前一交易日。周末剔除；
    法定休市日无法在不引入交易日历的情况下区分，故只能在 partial 原因里
    如实说明「缺失日可能是休市日」。
    """
    start_date = opened_et.date()
    if opened_et.time() > _RTH_LAST_BAR_START:
        start_date = start_date + timedelta(days=1)
    end_date = closed_et.date()
    if closed_et.time() < _RTH_OPEN:
        end_date = end_date - timedelta(days=1)
    sessions: list[str] = []
    cursor = start_date
    while cursor <= end_date:
        if cursor.weekday() < 5:
            sessions.append(cursor.isoformat())
        cursor = cursor + timedelta(days=1)
    return sessions


def compute_excursion(
    episode: ExcursionEpisode,
    bars: Sequence[Mapping[str, Any]],
    *,
    atr14: Optional[float] = None,
) -> ExcursionResult:
    """按蓝图 §三(b) 公式计算一个已平仓回合的 MAE/MFE（标的近似）。

    ``bars`` 是原始 5m bar 行（``date`` 为 ISO tz-aware 字符串，时间戳＝bar
    **开始**时间；按结束时间打标的源必须先过
    :func:`src.opportunities.intraday_bursts.normalize_bar_label_convention`）。
    只使用常规时段 bar；隔夜跳空由次日首根 bar 体现。

    公式（``exposure`` 判定见 :func:`determine_exposure`）：

    * ``U0`` = opened_at 之后第一根 5m bar 的 open（盘前开仓 → 当日首根
      RTH bar 并记 flag；盘后开仓 → 次一时段首根 bar 并记 flag）。
    * 逐 bar：favorable 用 ``exposure=+1 ? high : low``，adverse 反之；
      ``mfe = max(0, max_t exposure×(fav_t/U0 − 1))``、
      ``mae = min(0, min_t exposure×(adv_t/U0 − 1))``（小数，非百分点），
      并记 ``mfe_at / mae_at / mae_before_mfe``。
    * ``atr14``（标的日线 ATR14，价格单位）可得时补充 ATR 标尺：
      ``x_atr = x × U0 / atr14``；缺则标缺。
    """
    exposure = determine_exposure(
        episode.asset_type, episode.direction, episode.option_right
    )
    if exposure is None:
        return _failed(
            STATUS_NOT_APPLICABLE,
            "标的敞口方向不可判定（组合腿或资产类型/期权方向不明），不猜",
        )
    if episode.opened_at is None:
        return _failed(STATUS_NOT_APPLICABLE, "缺 opened_at，无法定位持有窗口")
    if episode.closed_at is None:
        return _failed(
            STATUS_NOT_APPLICABLE, "回合未平仓：偏移只对已平仓回合计算"
        )
    opened_et = _to_et(episode.opened_at)
    closed_et = _to_et(episode.closed_at)
    if closed_et < opened_et:
        return _failed(STATUS_NOT_APPLICABLE, "closed_at 早于 opened_at，坏数据")

    rows = filter_regular_session_bars(bars)
    window = [
        row
        for row in rows
        if opened_et <= row["start_et"] <= closed_et
    ]
    if not window:
        if closed_et - opened_et < timedelta(
            minutes=5
        ) and not _has_expected_bar_start(opened_et, closed_et):
            # 持有短于一根 5m bar 且窗口内不含任何理论 bar 开始时刻：
            # 这是 5m 粒度限制，不是数据缺失——诚实标不适用，不谎称缺 bar。
            return _failed(
                STATUS_NOT_APPLICABLE,
                "sub_bar_hold_below_5m_granularity：持有时长短于一根 5m bar"
                "（窗口内无任何 bar 开始时刻），5m 粒度下不可计算——"
                "属粒度限制，非数据保留窗口问题",
            )
        return _failed(
            STATUS_MISSING_BARS,
            "持有窗口内没有任何标的常规时段 5m bar",
        )

    u0_row = window[0]
    u0 = float(u0_row["open"])
    if u0 <= 0:
        return _failed(STATUS_MISSING_BARS, "首根 bar 开盘价非正，无法作 U0 基准")
    u0_at: datetime = u0_row["start_et"]
    u0_flag: Optional[str] = None
    if u0_at.date() != opened_et.date():
        entry_time = opened_et.time()
        if entry_time < _RTH_OPEN or entry_time > _RTH_LAST_BAR_START:
            # 时钟事实：开仓在 RTH bar 覆盖时段之外 → 次一时段首根 bar。
            u0_flag = "entry_outside_rth_next_session_bar"
        else:
            # 数据事实：开仓在 RTH 内但入场日无 bar → 缺口顺延，不是盘外开仓。
            u0_flag = "entry_day_bars_missing_next_session_bar"
    elif opened_et.time() < _RTH_OPEN:
        u0_flag = "premarket_open_first_rth_bar"

    max_fav: Optional[float] = None
    max_fav_at: Optional[datetime] = None
    min_adv: Optional[float] = None
    min_adv_at: Optional[datetime] = None
    for row in window:
        favorable = row["high"] if exposure == 1 else row["low"]
        adverse = row["low"] if exposure == 1 else row["high"]
        fav_excursion = exposure * (float(favorable) / u0 - 1.0)
        adv_excursion = exposure * (float(adverse) / u0 - 1.0)
        if max_fav is None or fav_excursion > max_fav:
            max_fav = fav_excursion
            max_fav_at = row["start_et"]
        if min_adv is None or adv_excursion < min_adv:
            min_adv = adv_excursion
            min_adv_at = row["start_et"]

    mfe = max(0.0, max_fav if max_fav is not None else 0.0)
    mae = min(0.0, min_adv if min_adv is not None else 0.0)
    mfe_at = max_fav_at if mfe > 0 else None
    mae_at = min_adv_at if mae < 0 else None
    mae_before_mfe: Optional[bool] = None
    if mfe_at is not None and mae_at is not None:
        mae_before_mfe = mae_at < mfe_at

    covered_sessions = {row["session_date_et"] for row in window}
    expected_sessions = _expected_sessions(opened_et, closed_et)
    missing_sessions = tuple(
        session for session in expected_sessions if session not in covered_sessions
    )
    if missing_sessions:
        status = STATUS_PARTIAL
        status_reason = (
            "持有窗口内以下交易日缺 5m bar（其中可能含休市日，无交易日历"
            "无法区分，如实列出）："
            + ", ".join(missing_sessions)
        )
    else:
        status = STATUS_READY
        status_reason = None

    atr_value: Optional[float] = None
    mfe_atr: Optional[float] = None
    mae_atr: Optional[float] = None
    if atr14 is not None and atr14 > 0:
        atr_value = float(atr14)
        mfe_atr = mfe * u0 / atr_value
        mae_atr = mae * u0 / atr_value

    return ExcursionResult(
        status=status,
        status_reason=status_reason,
        exposure=exposure,
        u0=u0,
        u0_at=u0_at,
        u0_flag=u0_flag,
        mfe_underlying_pct=mfe,
        mfe_at=mfe_at,
        mae_underlying_pct=mae,
        mae_at=mae_at,
        mae_before_mfe=mae_before_mfe,
        atr14=atr_value,
        mfe_atr=mfe_atr,
        mae_atr=mae_atr,
        bars_used=len(window),
        coverage_start=window[0]["start_et"],
        coverage_end=window[-1]["start_et"],
        missing_sessions=missing_sessions,
    )


def compute_atr14(
    daily_bars: Sequence[Mapping[str, Any]],
    as_of: date,
    *,
    period: int = 14,
) -> Optional[float]:
    """标的日线 ATR14（Wilder 平滑），只用 ``as_of`` **之前**的完结日线。

    ``daily_bars`` 行形如 ``{"date": "YYYY-MM-DD…", "high": …, "low": …,
    "close": …}``。数据不足 ``period+1`` 根时返回 ``None``（标缺），
    绝不以更短窗口冒充 14 日 ATR。
    """
    cleaned: list[tuple[str, float, float, float]] = []
    for row in daily_bars or ():
        day = str(row.get("date") or "")[:10]
        try:
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(day) != 10 or high < low or close <= 0:
            continue
        if day >= as_of.isoformat():
            continue
        cleaned.append((day, high, low, close))
    cleaned.sort(key=lambda item: item[0])
    if len(cleaned) < period + 1:
        return None
    true_ranges: list[float] = []
    for previous, current in zip(cleaned, cleaned[1:]):
        previous_close = previous[3]
        true_ranges.append(
            max(
                current[1] - current[2],
                abs(current[1] - previous_close),
                abs(current[2] - previous_close),
            )
        )
    if len(true_ranges) < period:
        return None
    atr = sum(true_ranges[:period]) / period
    for value in true_ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr if atr > 0 else None

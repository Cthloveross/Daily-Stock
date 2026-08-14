# -*- coding: utf-8 -*-
"""盘中机会提示器：把预热扫描已算出的事实推送到 Telegram（手机端）。

用户诉求：「盘前盘中的时候知道什么票有机会，比如高开的、放量的」。本模块
**不新增任何取数路径**——它挂在 :class:`IntradayWarmCacheScheduler` 的预热
循环上，逐 tick 读取 :func:`warm_default_intraday_top_scan` 经 single-flight
算出的同一份两层扫描载荷（独立深拷贝），从**已有字段**里按六条 v1 规则提取
事实并推送。预热调度器关闭时（``INTRADAY_REFRESH_SCHEDULER_ENABLED=false``）
没有载荷可观察，提示器自然零检测。

诚实合同（与扫描面板同一底线）：

- 只陈述载荷里已有的数字与其既有口径，不重算、不预测、不给方向判断；
- 每条消息末尾固定一行「事实描述，非买卖信号」；
- 黑名单标的（见 :mod:`src.opportunities.blacklist`）不压制，只附加
  「⚠️ 你的历史亏钱标的」标注——系统标注，用户过滤；
- 阈值全部是 v1 启发式（沿用扫描面板已校准的常量），不是验证过的交易边际。

防骚扰合同：

- 按（标的 × 规则）× ET 日去重（进程内，ET 日期翻转即清零）；强波段规则
  的去重键额外含波段起点 ``start_et``（一个标的一天可有多个强波段）；
- 单 tick 检出的多条提示合并为**一条** Telegram 消息；
- 全局日上限 ``INTRADAY_ALERTS_MAX_PER_DAY``（默认 20，最小 1）：触顶后补发
  一条「今日提示已达上限 N 条」，当日不再发送；
- 去重状态只在进程内：**中途重启后最坏情况会重复提示**（接受并在文档注明）。

节奏合同：detection 在预热线程内是纯内存操作（微秒级）；Telegram 发送
（含重试链，最长可达数秒）在独立 daemon 线程消化有界队列，绝不阻塞预热
tick。发送失败/恢复只在状态变化时各记一行日志（quiet-unless-incident，
与 warm scheduler host 同一模式），任何异常都不上抛。
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from src.opportunities.blacklist import (
    BLACKLIST_ALERT_TAG,
    BLACKLIST_TICKER_SET,
)
from src.opportunities.intraday_bursts import (
    DISPLACEMENT_ATR_BASIS_DAILY,
    DISPLACEMENT_SURVIVAL_LINE_ATR,
    LEG_MEDIUM_MIN_SCORE,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v1 提示阈值（documented heuristics，与扫描面板同源，不是验证过的边际）。
# ---------------------------------------------------------------------------
# 规则 1（盘前异动）：|pre_change_percent| ≥ 2.0（Moomoo 快照盘前口径，
# 常规字段在盘前仍指向上一常规时段——见 G-12，绝不用 change_percent 冒充）。
PREMARKET_ABS_CHANGE_MIN = 2.0
# 规则 2（放量爆发）：当前 15 分钟窗口 vol_norm ≥ 2.0 且爆发分 ≥ 中波段线
# （LEG_MEDIUM_MIN_SCORE=2.5，单一真源在 intraday_bursts，不另设平行常量）。
VOLUME_ALERT_VOL_NORM_MIN = 2.0
VOLUME_ALERT_MIN_SCORE = LEG_MEDIUM_MIN_SCORE
# 规则 4（位移达标）：|net_move_atr| 首次达到 ±0.5 ATR 参考线（v8 更正后它
# 只描述过去 30 分钟，不承载前向信息）；优先读载荷自带的 survival_line_atr。
DISPLACEMENT_ALERT_LINE_ATR = DISPLACEMENT_SURVIVAL_LINE_ATR
# 规则 6（盘前期权大单）：候选 option_activity.max_single_turnover ≥ $1M。
# 阈值是 v1 启发式圆整数（与 /intraday 盘前期权异常面板同值），不是验证过
# 的边际。盘前读到的异动页是上一时段的成交（盘前绝大多数期权无成交），
# 文案如实标注口径。警告：载荷只带比例/大单里的**大单**一半——call/put
# 偏斜比例在期权墙端点（option-walls）上，不在预热扫描载荷里，本模块
# 不为它新增取数路径，偏斜提示因此不存在。
PREMARKET_LARGE_PRINT_MIN_TURNOVER = 1_000_000.0

ALERT_FOOTER = "事实描述，非买卖信号"
DEFAULT_MAX_ALERTS_PER_DAY = 20
_QUEUE_MAXSIZE_DEFAULT = 8
_NEW_YORK = ZoneInfo("America/New_York")

_DIRECTION_LABELS = {"up": "向上", "down": "向下", "flat": "持平"}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


@dataclass(frozen=True)
class IntradayAlert:
    """One factual alert line, already fully rendered (blacklist tag included)."""

    rule: str
    ticker: Optional[str]
    text: str
    # 该条对应的去重键：队列满被丢弃时用于回滚 _seen，让事实下一 tick 可重检。
    dedupe_key: tuple[str, ...] = ()


def _tagged(ticker: str, text: str) -> str:
    if ticker in BLACKLIST_TICKER_SET:
        return f"{text} {BLACKLIST_ALERT_TAG}"
    return text


def _iter_premarket_rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """深度层候选在前（异动最强的标的已被晋升，宽层 snapshot_only 不再含它们；
    候选上的 ``pre_change_percent`` 是两层扫描从同一批快照原样带过来的），
    宽层剩余行在后（已按 |pre_change| 降序）。"""

    for candidate in payload.get("candidates") or ():
        if isinstance(candidate, Mapping):
            yield candidate
    universe_scan = payload.get("universe_scan")
    if isinstance(universe_scan, Mapping):
        for row in universe_scan.get("snapshot_only") or ():
            if isinstance(row, Mapping):
                yield row


def detect_intraday_alerts(
    payload: Mapping[str, Any],
    *,
    seen: set[tuple[str, ...]],
) -> list[IntradayAlert]:
    """六条 v1 规则的确定性检测；``seen`` 为当日去重键集合（就地登记）。

    只读载荷已有字段：数字与口径原样透传，不重算（唯一的乘法是位移美元
    换算 ``net_move_atr × atr14``，与前端扫描表同一显示口径，且仅在
    ``atr_basis == "atr14_daily"`` 时进行，其余标尺只报 ATR 值）。
    """

    alerts: list[IntradayAlert] = []
    session_state = str(payload.get("session_state") or "")
    quote_scope = str(payload.get("quote_session_scope") or "")
    candidates = [
        candidate
        for candidate in (payload.get("candidates") or ())
        if isinstance(candidate, Mapping)
    ]

    def _emit(key: tuple[str, ...], alert: IntradayAlert) -> None:
        if key in seen:
            return
        seen.add(key)
        alerts.append(replace(alert, dedupe_key=key))

    # -- 规则 5：日型提醒（lane_availability 一旦不再 unknown）--------------
    # 对抗复核修正：day_type 随深度层轮换可能盘中翻转（例如 QQQ 中午被晋升
    # 后 overnight_only → intraday_available）。若按「当日一次」去重，用户会
    # 拿着一句已过时的全天断言（「今日无 0DTE」）不被更正。因此去重键包含
    # 具体值：值变化 → 以「日型更正」前缀重新提醒；同值绝不重复。
    lane = payload.get("lane_availability")
    if isinstance(lane, Mapping):
        day_type = str(lane.get("day_type") or "")
        _DAY_TYPE_TEXTS = {
            "intraday_available": "今日有 0DTE：日内车道可用",
            "overnight_only": "今日无 0DTE：过夜日 · 日内关闭",
        }
        if day_type in _DAY_TYPE_TEXTS:
            previously_alerted = any(
                k[0] == "day_type" and k != ("day_type", day_type) for k in seen
            )
            text = _DAY_TYPE_TEXTS[day_type]
            if previously_alerted:
                text = f"日型更正：{text}（深度层轮换后重新判定）"
            _emit(
                ("day_type", day_type),
                IntradayAlert(rule="day_type", ticker=None, text=text),
            )
        # unknown：未知≠没有，先不提醒，等到能判定为止。

    # -- 规则 1：高开/盘前异动（仅盘前时段；预热窗口天然从 09:00 ET 起）----
    if session_state == "premarket":
        for row in _iter_premarket_rows(payload):
            ticker = str(row.get("ticker") or "").strip().upper()
            pre_change = _finite(row.get("pre_change_percent"))
            if not ticker or pre_change is None:
                continue
            if abs(pre_change) < PREMARKET_ABS_CHANGE_MIN:
                continue
            _emit(
                ("premarket_move", ticker),
                IntradayAlert(
                    rule="premarket_move",
                    ticker=ticker,
                    text=_tagged(ticker, f"{ticker} 盘前 {pre_change:+.1f}%"),
                ),
            )

        # -- 规则 6：盘前期权大单（仅盘前；每标的每日一次）------------------
        # 只读候选已带的 option_activity.max_single_turnover（深度层逐标的
        # 异动页聚合）；盘前该页承载的是上一时段成交，文案如实声明口径。
        # 供应商分类不随大单转述（dominant_sentiment 是整页多数，不是该笔
        # 的分类，转述会张冠李戴）。
        for candidate in candidates:
            ticker = str(candidate.get("ticker") or "").strip().upper()
            activity = candidate.get("option_activity")
            if not ticker or not isinstance(activity, Mapping):
                continue
            max_turnover = _finite(activity.get("max_single_turnover"))
            if (
                max_turnover is None
                or max_turnover < PREMARKET_LARGE_PRINT_MIN_TURNOVER
            ):
                continue
            _emit(
                ("premarket_large_print", ticker),
                IntradayAlert(
                    rule="premarket_large_print",
                    ticker=ticker,
                    text=_tagged(
                        ticker,
                        f"{ticker} 期权大单 ${max_turnover / 1e6:.1f}M"
                        "（上一时段异动页最大单笔）",
                    ),
                ),
            )

    # -- 规则 2：放量（仅常规时段；当前 15 分钟窗口读数）--------------------
    if session_state == "regular":
        for candidate in candidates:
            ticker = str(candidate.get("ticker") or "").strip().upper()
            bursts = candidate.get("session_bursts")
            if not ticker or not isinstance(bursts, Mapping):
                continue
            if bursts.get("state") != "ready":
                continue
            current = bursts.get("current")
            if not isinstance(current, Mapping):
                continue
            vol_norm = _finite(current.get("vol_norm"))
            score = _finite(current.get("score"))
            thrust = _finite(current.get("thrust_percent"))
            if vol_norm is None or score is None or thrust is None:
                continue
            if vol_norm < VOLUME_ALERT_VOL_NORM_MIN or score < VOLUME_ALERT_MIN_SCORE:
                continue
            _emit(
                ("volume_burst", ticker),
                IntradayAlert(
                    rule="volume_burst",
                    ticker=ticker,
                    text=_tagged(
                        ticker,
                        f"{ticker} 放量 {vol_norm:.1f}× · "
                        f"15分推力 {thrust:+.1f}% · 爆发分 {score:.1f}",
                    ),
                ),
            )

    # -- 规则 3/4 只在「当前交易时段」口径下检测：休市载荷展示的是最近一个
    # 已结束时段的波段/位移，提醒它们会把昨天说成现在。-----------------------
    if quote_scope != "current_session":
        return alerts

    # -- 规则 3：强波段入账（新出现的 grade=strong 波段，一段一次）-----------
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").strip().upper()
        bursts = candidate.get("session_bursts")
        if not ticker or not isinstance(bursts, Mapping):
            continue
        for leg in bursts.get("legs") or ():
            if not isinstance(leg, Mapping) or leg.get("grade") != "strong":
                continue
            start_et = str(leg.get("start_et") or "")
            score = _finite(leg.get("score"))
            if not start_et or score is None:
                continue
            direction = _DIRECTION_LABELS.get(
                str(leg.get("direction") or ""), "方向未知"
            )
            _emit(
                ("strong_leg", ticker, start_et),
                IntradayAlert(
                    rule="strong_leg",
                    ticker=ticker,
                    text=_tagged(
                        ticker,
                        f"{ticker} 强波段 {start_et} 分 {score:.0f} {direction}",
                    ),
                ),
            )

    # -- 规则 4：近 30 分位移达 ±0.5 ATR（每标的每方向每日一次）--------------
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").strip().upper()
        displacement = candidate.get("recent_displacement")
        if not ticker or not isinstance(displacement, Mapping):
            continue
        if displacement.get("state") != "ready":
            continue
        net = _finite(displacement.get("net_move_atr"))
        if net is None:
            continue
        line = _finite(displacement.get("survival_line_atr"))
        if line is None or line <= 0:
            line = DISPLACEMENT_ALERT_LINE_ATR
        if net >= line:
            direction = "up"
        elif net <= -line:
            direction = "down"
        else:
            continue
        # 美元换算沿用前端扫描表同一口径：仅日线 ATR14 标尺（atr14_daily）
        # 可换算（net × atr14）；其余标尺不可横向比较，只报 ATR 值。
        dollars: Optional[float] = None
        atr14 = _finite(candidate.get("atr14"))
        if (
            displacement.get("atr_basis") == DISPLACEMENT_ATR_BASIS_DAILY
            and atr14 is not None
            and atr14 > 0
        ):
            dollars = net * atr14
        if dollars is not None:
            sign = "+" if dollars >= 0 else "-"
            text = (
                f"{ticker} 近30分位移 {sign}${abs(dollars):.2f}"
                f"（{net:+.2f} ATR）"
            )
        else:
            text = f"{ticker} 近30分位移 {net:+.2f} ATR"
        _emit(
            ("displacement", ticker, direction),
            IntradayAlert(
                rule="displacement",
                ticker=ticker,
                text=_tagged(ticker, text),
            ),
        )

    return alerts


def format_alert_message(
    alerts: list[IntradayAlert],
    *,
    as_of: Optional[str],
    extra_lines: Optional[list[str]] = None,
) -> str:
    """单 tick 的全部提示合并为一条消息；页脚每条消息只出现一次。"""

    header = "【盘中提示】"
    if as_of:
        try:
            moment = datetime.fromisoformat(str(as_of))
            if moment.tzinfo is not None:
                header = (
                    f"【盘中提示 · {moment.astimezone(_NEW_YORK):%H:%M} ET】"
                )
        except ValueError:
            pass
    lines = [header]
    lines.extend(alert.text for alert in alerts)
    lines.extend(extra_lines or [])
    lines.append(ALERT_FOOTER)
    return "\n".join(lines)


class IntradayOpportunityAlerter:
    """预热载荷观察者：检测在调用线程（微秒级），发送在独立 worker 线程。

    ``observe`` 是给 ``warm_default_intraday_top_scan(payload_observer=...)``
    的回调：**永不抛出、永不阻塞**（有界队列 ``put_nowait``；队列满时丢弃
    本 tick 消息并**回滚去重键**，同一事实下一 tick 可重检——预热节奏永远
    优先）。诚实边界：消息一旦出队、发送失败（如 Telegram 网络错误）不会
    重投也不会回滚去重——该事实当日不再提示；全天通道故障最坏静默耗尽
    当日提示（日志按状态变化各记一行）。
    """

    def __init__(
        self,
        send_text: Callable[[str], bool],
        *,
        max_alerts_per_day: int = DEFAULT_MAX_ALERTS_PER_DAY,
        queue_maxsize: int = _QUEUE_MAXSIZE_DEFAULT,
        async_send: bool = True,
        thread_name: str = "dsa-intraday-alerts",
    ) -> None:
        try:
            cap = int(max_alerts_per_day)
        except (TypeError, ValueError):
            cap = DEFAULT_MAX_ALERTS_PER_DAY
        self._max_alerts_per_day = max(1, cap)
        self._send_text = send_text
        self._async_send = bool(async_send)
        self._queue: "queue.Queue[str]" = queue.Queue(
            maxsize=max(1, int(queue_maxsize))
        )
        self._thread_name = thread_name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # 当日状态（进程内；ET 日期翻转清零；重启即丢失——文档已注明）。
        self._day: Optional[str] = None
        self._seen: set[tuple[str, ...]] = set()
        self._sent_count = 0
        self._cap_notice_sent = False
        # quiet-unless-incident：仅状态变化时记日志。
        self._last_fingerprint: Optional[str] = None

    @property
    def max_alerts_per_day(self) -> int:
        return self._max_alerts_per_day

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self._async_send or self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._drain, name=self._thread_name, daemon=True
        )
        self._thread.start()
        logger.info(
            "[intraday-alerts] started max_per_day=%d", self._max_alerts_per_day
        )

    def stop(self, *, join_timeout_seconds: Optional[float] = 2.0) -> None:
        """有界收尾：发送链（Telegram 重试）可长达数秒，daemon 线程不拖关停。"""

        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        timeout = (
            None
            if join_timeout_seconds is None
            else max(0.0, float(join_timeout_seconds))
        )
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning(
                "[intraday-alerts] stop timed out; daemon sender still draining"
            )
            return
        self._thread = None
        logger.info("[intraday-alerts] stopped")

    # -- observation -------------------------------------------------------
    def observe(self, payload: Mapping[str, Any]) -> None:
        """Warm-path callback：任何异常折叠为状态变化日志，绝不上抛。"""

        try:
            self._observe(payload)
        except Exception as exc:  # noqa: BLE001 - warm cadence first, always
            self._note_state(
                f"observe_error:{type(exc).__name__}",
                logging.WARNING,
                "[intraday-alerts] observe failed error_type=%s; alerts skipped "
                "this tick",
                type(exc).__name__,
            )

    def _observe(self, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            return
        market_date = str(payload.get("market_date_et") or "").strip()
        if not market_date:
            # 没有 ET 日期就没有诚实的去重口径：安静跳过本 tick。
            return
        with self._lock:
            if market_date != self._day:
                self._day = market_date
                self._seen.clear()
                self._sent_count = 0
                self._cap_notice_sent = False
            if self._cap_notice_sent:
                return
            alerts = detect_intraday_alerts(payload, seen=self._seen)
            if not alerts:
                return
            remaining = self._max_alerts_per_day - self._sent_count
            batch = alerts[:remaining]
            self._sent_count += len(batch)
            extra_lines: list[str] = []
            if self._sent_count >= self._max_alerts_per_day:
                extra_lines.append(
                    f"今日提示已达上限 {self._max_alerts_per_day} 条"
                )
                self._cap_notice_sent = True
            message = format_alert_message(
                batch,
                as_of=payload.get("as_of"),
                extra_lines=extra_lines,
            )
            enqueued = self._dispatch(message)
            if not enqueued:
                # 队列满：回滚本批的去重键与计数，让同一事实下一 tick 重检
                # ——否则一次拥塞就把当日这些提示永久吞掉（对抗复核修正）。
                for alert in batch:
                    self._seen.discard(alert.dedupe_key)
                self._sent_count -= len(batch)
                if extra_lines:
                    self._cap_notice_sent = False

    # -- delivery ----------------------------------------------------------
    def _dispatch(self, message: str) -> bool:
        if not self._async_send:
            self._deliver(message)
            return True
        if not self.running:
            self.start()
        try:
            self._queue.put_nowait(message)
            return True
        except queue.Full:
            self._note_state(
                "queue_full",
                logging.WARNING,
                "[intraday-alerts] send queue full; dropping this tick's "
                "message (dedupe keys rolled back so facts can re-detect)",
            )
            return False

    def _drain(self) -> None:
        while not self._stop_event.is_set():
            try:
                message = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._deliver(message)

    def _deliver(self, message: str) -> None:
        error_type: Optional[str] = None
        try:
            ok = bool(self._send_text(message))
        except Exception as exc:  # noqa: BLE001 - sender must never kill us
            ok = False
            error_type = type(exc).__name__
        if ok:
            self._note_state(
                "delivering",
                logging.INFO,
                "[intraday-alerts] telegram delivery healthy",
            )
        else:
            self._note_state(
                f"send_failed:{error_type or 'returned_false'}",
                logging.WARNING,
                "[intraday-alerts] telegram send failed reason=%s; staying "
                "quiet until the state changes",
                error_type or "returned_false",
            )

    def _note_state(
        self, fingerprint: str, level: int, message: str, *args: Any
    ) -> None:
        if fingerprint == self._last_fingerprint:
            return
        logger.log(level, message, *args)
        self._last_fingerprint = fingerprint


__all__ = [
    "ALERT_FOOTER",
    "DEFAULT_MAX_ALERTS_PER_DAY",
    "IntradayAlert",
    "IntradayOpportunityAlerter",
    "PREMARKET_ABS_CHANGE_MIN",
    "PREMARKET_LARGE_PRINT_MIN_TURNOVER",
    "VOLUME_ALERT_MIN_SCORE",
    "VOLUME_ALERT_VOL_NORM_MIN",
    "detect_intraday_alerts",
    "format_alert_message",
]

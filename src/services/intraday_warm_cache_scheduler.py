# -*- coding: utf-8 -*-
"""Server-side warm loop for the intraday-top default scan cache.

盘中工作台此前是纯请求驱动：任何缓存空窗（页面关过一阵、TTL 过期、服务
重启）都会让用户的下一次轮询吃满 10–45 秒的冷路径。本调度器按 XNYS
官方日历与当天真实收盘时间，在 04:00–20:00 ET 合法扩展时段内预热
「页面默认轮询」命中的同一个扫描 key；假日零请求、半日市同步提前停止。

预热必须经由端点同一套 single-flight 机制（join-not-duplicate）：用户请求
正在 leader 时，预热只会加入等待，绝不并发第二个工厂。预热结果与请求驱动
结果不可区分——同一缓存、同一 TTL、同一诚实字段。线程只是本地唤醒机制，
与仓库既有 scheduler host（premarket / outcome）同一模式：默认关闭、
quiet-unless-incident、有界 stop。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from src.services.xnys_session_clock import (
    XNYSSessionClock,
    resolve_xnys_session_clock,
)

logger = logging.getLogger(__name__)

WarmRunner = Callable[[], Mapping[str, Any]]
SessionClockReader = Callable[[datetime], XNYSSessionClock]

# 间隔地板 30s：预热经 join-not-duplicate 单飞（实际受 intraday-top 的 45s
# 租约约束，非 30s 基础租约——对抗复核更正），同 key 至多一个工厂；地板只是
# 防配置把节奏拧到无意义的高频。
INTRADAY_WARM_INTERVAL_FLOOR_SECONDS = 30.0
INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS = 60.0

# P0-X 需要盘前/盘后事实发现，host 覆盖完整美股扩展时段。
WARM_WINDOW_START_MINUTES_ET = 4 * 60
WARM_WINDOW_END_MINUTES_ET = 20 * 60
_NEW_YORK = ZoneInfo("America/New_York")
_BAR_CLOSE_PROBE_DELAY_SECONDS = 5.0
_OPENING_PENDING_RETRY_SECONDS = 2.0
_OPENING_PENDING_RETRY_LIMIT = 4
_OPENING_PENDING_RETRY_START_SECONDS_ET = 9 * 3600 + 31 * 60
_OPENING_PENDING_RETRY_END_SECONDS_ET = 9 * 3600 + 36 * 60
_OPENING_PROGRESSIVE_START_SECONDS_ET = 9 * 3600 + 30 * 60
_OPENING_PROGRESSIVE_END_SECONDS_ET = 9 * 3600 + 35 * 60


def resolve_intraday_signal_symbols(config: Any = None) -> tuple[str, ...]:
    """One uncapped universe for subscription, opening scans, and readiness.

    Callers freeze this result once at startup.  UI focus/deep-lane limits and
    today's plans do not change the signal universe or its calibration hash.
    """

    from src.opportunities.engine import (
        is_supported_us_option_underlying,
        normalize_symbols,
    )

    if config is None:
        from src.config import get_config

        config = get_config()
    configured = getattr(config, "intraday_watchlist", None) or getattr(
        config, "stock_list", None
    ) or ()
    if isinstance(configured, str):
        configured = configured.split(",")
    return tuple(
        symbol
        for symbol in normalize_symbols([str(value) for value in configured])
        if is_supported_us_option_underlying(symbol)
    )


def _opening_failure_retry_marker(observed_at: datetime) -> int:
    """09:31--09:35 关键分钟的异常也进入有界快重试。"""

    et = _aware_utc(observed_at).astimezone(_NEW_YORK)
    seconds_et = et.hour * 3600 + et.minute * 60 + et.second
    return int(
        _OPENING_PENDING_RETRY_START_SECONDS_ET
        <= seconds_et
        < _OPENING_PENDING_RETRY_END_SECONDS_ET
    )


def seconds_until_next_5m_close_probe(
    now: datetime,
    *,
    interval_seconds: float,
) -> float:
    """每个已完成分钟边界后 5 秒唤醒。

    例如 09:34:45 ET 会在 09:35:05 再跑，而不是机械再等 180 秒；
    09:31:06 会直接等到 09:32:05，不在同一分钟额外启动慢扫描。
    5 秒缓冲给供应商完成 bar finalisation；最终仍由纯计算层的
    ``bar_start + bar_minutes <= as_of`` 防止使用未完结 K 线。
    """

    observed = _aware_utc(now).astimezone(_NEW_YORK)
    floor = observed.replace(second=0, microsecond=0)
    probe_minutes = 1
    minutes_to_boundary = (
        probe_minutes - floor.minute % probe_minutes
    ) % probe_minutes
    boundary = floor + timedelta(minutes=minutes_to_boundary)
    probe = boundary + timedelta(seconds=_BAR_CLOSE_PROBE_DELAY_SECONDS)
    if probe <= observed:
        probe += timedelta(minutes=probe_minutes)
    delay = (probe - observed).total_seconds()
    # 可以设置更短的运维心跳，但不能晚于下一分钟 +5s。
    # 严格 2m 层自行拒绝没有新 window_end 的重复评估。
    selected = min(float(interval_seconds), delay)
    return max(0.25, selected)


def crossed_5m_close_probe(started_at: datetime, completed_at: datetime) -> bool:
    """一次慢扫描若跨过下一分钟 probe，立刻补跑。"""

    started = _aware_utc(started_at).astimezone(_NEW_YORK)
    completed = _aware_utc(completed_at).astimezone(_NEW_YORK)
    if completed <= started:
        return False
    cursor = started.replace(second=0, microsecond=0)
    probe_minutes = 1
    cursor += timedelta(
        minutes=(probe_minutes - cursor.minute % probe_minutes) % probe_minutes
    )
    probe = cursor + timedelta(seconds=_BAR_CLOSE_PROBE_DELAY_SECONDS)
    if probe <= started:
        probe += timedelta(minutes=probe_minutes)
    return probe <= completed


@dataclass(frozen=True)
class IntradayWarmTickResult:
    """One warm observation without duplicating provider payloads."""

    action: str  # "idle" | "warmed" | "failed"
    state: str  # session state（premarket/regular/afterhours/closed）
    led_flight: bool
    generated_in_seconds: Optional[float]
    error_code: Optional[str]
    opening_pending_count: int = 0


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("warm scheduler clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def execute_intraday_warm_tick(
    *,
    warm_runner: WarmRunner,
    now: Optional[datetime] = None,
    session_clock_reader: SessionClockReader = resolve_xnys_session_clock,
    defer_during_opening: bool = False,
) -> IntradayWarmTickResult:
    """Warm the default intraday-top scan once, gated by the ET session clock.

    预热窗口＝XNYS 交易日 **04:00–20:00 ET**，用于覆盖 P0-X
    盘前/盘后的事实发现。XNYS 假日一律 idle、零供应商请求；日历
    不可用则 failed/fail-closed，绝不猜一个 16:00 正常收盘。窗口外一律
    idle，不发任何供应商请求。
    预热失败（含 Moomoo 断路器打开时工厂的快速失败）只折叠为
    error_code 返回，绝不抛出——host 保持节奏并按状态变化安静记录。
    """

    observed_at = _aware_utc(now or datetime.now(timezone.utc))
    try:
        session_clock = session_clock_reader(observed_at)
    except Exception as exc:  # noqa: BLE001 - exact calendar is mandatory
        return IntradayWarmTickResult(
            action="failed",
            state="closed",
            led_flight=False,
            generated_in_seconds=None,
            error_code=f"xnys_clock_failed:{type(exc).__name__}",
            opening_pending_count=_opening_failure_retry_marker(observed_at),
        )
    if session_clock.state == "unavailable":
        return IntradayWarmTickResult(
            action="failed",
            state="closed",
            led_flight=False,
            generated_in_seconds=None,
            error_code=session_clock.unavailable_reason or "xnys_clock_unavailable",
            opening_pending_count=_opening_failure_retry_marker(observed_at),
        )
    if session_clock.state != "session":
        return IntradayWarmTickResult(
            action="idle",
            state="closed",
            led_flight=False,
            generated_in_seconds=None,
            error_code=None,
            opening_pending_count=0,
        )
    et = observed_at.astimezone(_NEW_YORK)
    minutes = et.hour * 60 + et.minute
    session_state = session_clock.phase
    # Preserve the public extended-hours labels used elsewhere: before 04:00
    # and from 20:00 ET onward is "closed", while the XNYS calendar remains
    # authoritative for whether the date is a session at all.
    if minutes < 4 * 60 or minutes >= 20 * 60:
        session_state = "closed"
    in_warm_window = (
        WARM_WINDOW_START_MINUTES_ET <= minutes < WARM_WINDOW_END_MINUTES_ET
    )
    if not in_warm_window:
        return IntradayWarmTickResult(
            action="idle",
            state=session_state,
            led_flight=False,
            generated_in_seconds=None,
            error_code=None,
            opening_pending_count=0,
        )
    seconds_et = et.hour * 3600 + et.minute * 60 + et.second
    if (
        defer_during_opening
        and _OPENING_PROGRESSIVE_START_SECONDS_ET
        <= seconds_et < _OPENING_PENDING_RETRY_END_SECONDS_ET
    ):
        # Slow Top/option warming must not compete with the independent 1m
        # opening clock for provider finalisation during critical checkpoints.
        return IntradayWarmTickResult(
            action="idle",
            state=session_state,
            led_flight=False,
            generated_in_seconds=None,
            error_code=None,
            opening_pending_count=0,
        )
    try:
        summary = dict(warm_runner())
    except Exception as exc:  # noqa: BLE001 - keep cadence, redact payload
        return IntradayWarmTickResult(
            action="failed",
            state=session_state,
            led_flight=False,
            generated_in_seconds=None,
            error_code=type(exc).__name__,
            opening_pending_count=_opening_failure_retry_marker(observed_at),
        )
    generated = summary.get("generated_in_seconds")
    try:
        opening_pending_count = max(
            0, int(summary.get("opening_pending_count") or 0)
        )
    except (TypeError, ValueError):
        opening_pending_count = 0
    return IntradayWarmTickResult(
        action="warmed",
        state=session_state,
        led_flight=bool(summary.get("led_flight")),
        generated_in_seconds=(None if generated is None else float(generated)),
        error_code=None,
        opening_pending_count=opening_pending_count,
    )


def opening_pending_retry_delay(
    now: datetime,
    *,
    pending_count: int,
    retries_used: int,
    default_wait_seconds: float,
) -> tuple[float, int]:
    """Bound a provider-finalisation retry burst after each opening checkpoint.

    A completed 1m bar may become queryable a few seconds after its clock
    close.  At most four 2-second retries are allowed per 09:31--09:35 ET
    checkpoint; the scheduler resets ``retries_used`` when the minute bucket
    changes.  Outside that window the ordinary cadence resumes.
    """

    if int(pending_count or 0) <= 0:
        return float(default_wait_seconds), 0
    observed = _aware_utc(now).astimezone(_NEW_YORK)
    seconds_et = observed.hour * 3600 + observed.minute * 60 + observed.second
    used = max(0, int(retries_used))
    if not (
        _OPENING_PENDING_RETRY_START_SECONDS_ET
        <= seconds_et
        < _OPENING_PENDING_RETRY_END_SECONDS_ET
    ) or used >= _OPENING_PENDING_RETRY_LIMIT:
        return float(default_wait_seconds), used
    return min(float(default_wait_seconds), _OPENING_PENDING_RETRY_SECONDS), used + 1


class IntradayWarmCacheScheduler:
    """Bounded daemon host; quiet unless the warm state changes.

    与 premarket/outcome host 同一生命周期合同：start 幂等、stop 默认无界
    join（lifespan 收尾不遗留在途 tick）、测试可传有限超时。日志只在状态
    迁移时各写一行 INFO（idle→warming、warming→failed:<type>、恢复），
    绝不逐 tick 刷屏。
    """

    def __init__(
        self,
        tick: Callable[[], IntradayWarmTickResult],
        *,
        interval_seconds: float = INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS,
        thread_name: str = "dsa-intraday-warm-cache",
        channel_watch: Optional[Callable[[], bool]] = None,
        channel_notifier: Optional[Callable[[str], None]] = None,
        align_to_minute_closes: bool = True,
    ) -> None:
        try:
            interval = float(interval_seconds)
        except (TypeError, ValueError):
            interval = INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS
        if interval < INTRADAY_WARM_INTERVAL_FLOOR_SECONDS:
            logger.info(
                "[intraday-warm] interval %.0fs is below the %.0fs floor; clamped",
                interval,
                INTRADAY_WARM_INTERVAL_FLOOR_SECONDS,
            )
            interval = INTRADAY_WARM_INTERVAL_FLOOR_SECONDS
        self._tick = tick
        self._interval_seconds = interval
        self._thread_name = thread_name
        self._align_to_minute_closes = bool(align_to_minute_closes)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_fingerprint: Optional[str] = None
        # 通道看门狗（可选）：watch() 返回 True＝行情通道断开（如 Moomoo
        # 熔断器 open）。仅在预热窗口内的非 idle tick 评估；状态翻转时各
        # 通知一次（断开→提醒，恢复→提醒），notifier 异常吞噬绝不进循环。
        # 动机：2026-08-14 OpenD 崩溃后静默降级 6 天无人知晓（G-31）。
        self._channel_watch = channel_watch
        self._channel_notifier = channel_notifier
        self._channel_down: Optional[bool] = None
        self._opening_pending_retries = 0
        self._opening_pending_retry_bucket: Optional[tuple[str, int, int]] = None

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=self._thread_name,
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[intraday-warm] started interval=%.0fs", self._interval_seconds
        )

    def stop(self, *, join_timeout_seconds: Optional[float] = None) -> None:
        """Stop the host without abandoning an in-flight tick."""

        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        if thread is threading.current_thread():
            logger.warning(
                "[intraday-warm] stop requested from scheduler thread; "
                "waiting for loop exit is delegated to the lifecycle owner"
            )
            return
        timeout = (
            None
            if join_timeout_seconds is None
            else max(0.0, float(join_timeout_seconds))
        )
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning(
                "[intraday-warm] stop timed out; scheduler thread is still draining"
            )
            return
        self._thread = None
        logger.info("[intraday-warm] stopped")

    def _watch_channel(self) -> None:
        """预热窗口内检查行情通道；状态翻转时各通知一次，异常绝不外抛。"""

        if self._channel_watch is None or self._channel_notifier is None:
            return
        try:
            down = bool(self._channel_watch())
        except Exception:  # noqa: BLE001 - watch 失败视为未知，不通知不崩溃
            return
        if down == self._channel_down:
            return
        previously = self._channel_down
        self._channel_down = down
        try:
            if down:
                self._channel_notifier(
                    "⚠️ 行情通道断开（Moomoo OpenD 无响应，熔断器已打开）："
                    "页面将大片标缺，盘中提示同步受影响。"
                    "重启 OpenD 后系统会自动恢复。"
                )
            elif previously:
                self._channel_notifier("✅ 行情通道已恢复（Moomoo 熔断器闭合）")
        except Exception:  # noqa: BLE001 - 通知失败只能吞，循环优先
            logger.warning("[intraday-warm] channel notifier failed", exc_info=True)

    def _log_transition(self, result: IntradayWarmTickResult) -> None:
        if result.action == "failed":
            fingerprint = f"failed:{result.error_code}"
        elif result.action == "warmed":
            fingerprint = "warming"
        else:
            fingerprint = "idle"
        if fingerprint == self._last_fingerprint:
            return
        if result.action == "failed":
            # 一次状态变化一行 INFO：断路器打开等预期失败不刷 error。
            logger.info(
                "[intraday-warm] warm failed error_type=%s session=%s; "
                "keeping cadence",
                result.error_code,
                result.state,
            )
        elif result.action == "warmed":
            logger.info(
                "[intraday-warm] warm loop active session=%s led_flight=%s "
                "generated_in_seconds=%s",
                result.state,
                result.led_flight,
                result.generated_in_seconds,
            )
        else:
            logger.info(
                "[intraday-warm] idle session=%s", result.state
            )
        self._last_fingerprint = fingerprint

    def _run(self) -> None:
        while not self._stop_event.is_set():
            started_at = datetime.now(timezone.utc)
            opening_pending_count = 0
            try:
                result = self._tick()
                opening_pending_count = getattr(
                    result, "opening_pending_count", 0
                )
                self._log_transition(result)
                if result.action != "idle":
                    self._watch_channel()
            except Exception as exc:  # noqa: BLE001 - keep daemon alive
                fingerprint = f"tick_error:{type(exc).__name__}"
                if fingerprint != self._last_fingerprint:
                    logger.error(
                        "[intraday-warm] tick failed error_type=%s",
                        type(exc).__name__,
                    )
                    self._last_fingerprint = fingerprint
            completed_at = datetime.now(timezone.utc)
            if not self._align_to_minute_closes:
                # Page/option warming uses its configured cadence.  A slow
                # factory never forces immediate catch-up on the signal clock.
                self._stop_event.wait(self._interval_seconds)
                continue
            wait_seconds = seconds_until_next_5m_close_probe(
                completed_at,
                interval_seconds=self._interval_seconds,
            )
            if crossed_5m_close_probe(started_at, completed_at):
                wait_seconds = 0.25
            completed_et = completed_at.astimezone(_NEW_YORK)
            retry_bucket = (
                (
                    completed_et.date().isoformat(),
                    completed_et.hour,
                    completed_et.minute,
                )
                if 9 * 60 + 31
                <= completed_et.hour * 60 + completed_et.minute
                <= 9 * 60 + 35
                else None
            )
            if retry_bucket != self._opening_pending_retry_bucket:
                self._opening_pending_retries = 0
                self._opening_pending_retry_bucket = retry_bucket
            wait_seconds, self._opening_pending_retries = (
                opening_pending_retry_delay(
                    completed_at,
                    pending_count=opening_pending_count,
                    retries_used=self._opening_pending_retries,
                    default_wait_seconds=wait_seconds,
                )
            )
            self._stop_event.wait(wait_seconds)


__all__ = [
    "INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS",
    "INTRADAY_WARM_INTERVAL_FLOOR_SECONDS",
    "IntradayWarmCacheScheduler",
    "IntradayWarmTickResult",
    "crossed_5m_close_probe",
    "execute_intraday_warm_tick",
    "opening_pending_retry_delay",
    "seconds_until_next_5m_close_probe",
]

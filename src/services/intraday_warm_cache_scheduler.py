# -*- coding: utf-8 -*-
"""Server-side warm loop for the intraday-top default scan cache.

盘中工作台此前是纯请求驱动：任何缓存空窗（页面关过一阵、TTL 过期、服务
重启）都会让用户的下一次轮询吃满 10–45 秒的冷路径。本调度器在工作日
04:00–20:00 ET（盘前→盘后，时钟口径完全复用
:func:`src.opportunities.intraday.market_session_state`，不新增时钟逻辑）
内按固定节奏预热「页面默认轮询」命中的同一个扫描 key。

预热必须经由端点同一套 single-flight 机制（join-not-duplicate）：用户请求
正在 leader 时，预热只会加入等待，绝不并发第二个工厂。预热结果与请求驱动
结果不可区分——同一缓存、同一 TTL、同一诚实字段。线程只是本地唤醒机制，
与仓库既有 scheduler host（premarket / outcome）同一模式：默认关闭、
quiet-unless-incident、有界 stop。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from src.opportunities.intraday import market_session_state

logger = logging.getLogger(__name__)

WarmRunner = Callable[[], Mapping[str, Any]]
SessionStateReader = Callable[[datetime], str]

# 间隔地板 30s：预热经 join-not-duplicate 单飞（实际受 intraday-top 的 45s
# 租约约束，非 30s 基础租约——对抗复核更正），同 key 至多一个工厂；地板只是
# 防配置把节奏拧到无意义的高频。
INTRADAY_WARM_INTERVAL_FLOOR_SECONDS = 30.0
INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS = 45.0

# 预热窗口（ET 分钟数，工作日）：09:00–16:15。窄于交易所延长时段是有意的，
# 见 execute_intraday_warm_tick docstring 的配额与假日空转说明。
WARM_WINDOW_START_MINUTES_ET = 9 * 60
WARM_WINDOW_END_MINUTES_ET = 16 * 60 + 15
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class IntradayWarmTickResult:
    """One warm observation without duplicating provider payloads."""

    action: str  # "idle" | "warmed" | "failed"
    state: str  # session state（premarket/regular/afterhours/closed）
    led_flight: bool
    generated_in_seconds: Optional[float]
    error_code: Optional[str]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("warm scheduler clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def execute_intraday_warm_tick(
    *,
    warm_runner: WarmRunner,
    now: Optional[datetime] = None,
    session_state_reader: SessionStateReader = market_session_state,
) -> IntradayWarmTickResult:
    """Warm the default intraday-top scan once, gated by the ET session clock.

    预热窗口＝工作日 **09:00–16:15 ET**（对抗复核收窄，原为整个
    04:00–20:00 延长时段）：用户实际看盘从盘前最后半小时开始，凌晨起就
    预热会在无人观看时自主消耗 30/日深扫标的额度（异动轮换可在中午前
    把 day_promotion_cap 触顶）并放大周中假日（无交易所日历，时钟按
    工作日判定）的空转成本——收窄后假日空转从 ~16h 降到 ~7h，且盘前
    额度留给用户自己的首次浏览。窗口外一律 idle，不发任何供应商请求。
    预热失败（含 Moomoo 断路器打开时工厂的快速失败）只折叠为
    error_code 返回，绝不抛出——host 保持节奏并按状态变化安静记录。
    """

    observed_at = _aware_utc(now or datetime.now(timezone.utc))
    session_state = session_state_reader(observed_at)
    in_warm_window = False
    if session_state != "closed":
        et = observed_at.astimezone(_NEW_YORK)
        minutes = et.hour * 60 + et.minute
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
        )
    generated = summary.get("generated_in_seconds")
    return IntradayWarmTickResult(
        action="warmed",
        state=session_state,
        led_flight=bool(summary.get("led_flight")),
        generated_in_seconds=(None if generated is None else float(generated)),
        error_code=None,
    )


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
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_fingerprint: Optional[str] = None

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
                "[intraday-warm] idle outside the weekday 09:00-16:15 ET warm window"
            )
        self._last_fingerprint = fingerprint

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self._tick()
                self._log_transition(result)
            except Exception as exc:  # noqa: BLE001 - keep daemon alive
                fingerprint = f"tick_error:{type(exc).__name__}"
                if fingerprint != self._last_fingerprint:
                    logger.error(
                        "[intraday-warm] tick failed error_type=%s",
                        type(exc).__name__,
                    )
                    self._last_fingerprint = fingerprint
            self._stop_event.wait(self._interval_seconds)


__all__ = [
    "INTRADAY_WARM_INTERVAL_DEFAULT_SECONDS",
    "INTRADAY_WARM_INTERVAL_FLOOR_SECONDS",
    "IntradayWarmCacheScheduler",
    "IntradayWarmTickResult",
    "execute_intraday_warm_tick",
]

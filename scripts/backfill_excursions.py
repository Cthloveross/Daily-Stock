# -*- coding: utf-8 -*-
"""Backfill MAE/MFE excursions + persist underlying 5m bars (Phase A).

蓝图 17 §三(f)：近 ~60 天窗口内的已平仓回合逐 underlying 抓 5m → 落库 →
逐回合计算偏移；超窗且本地无 bar 的回合写 ``missing_bars`` **永久标缺**
（绝不以 0 或日线冒充）。同一脚本带 ``--daily`` 即为每日增量持久化任务：
只处理近几日活跃的 underlying，让 5m 覆盖率随时间趋近 100%。

Usage:
    python -m scripts.backfill_excursions                # 全量回填（近 ~60 天）
    python -m scripts.backfill_excursions --daily        # 每日增量（挂调度用）
    python -m scripts.backfill_excursions --dry-run      # 只报告，不写库

Scheduling（文档化，不新增守护进程）：收盘后运行一次 ``--daily``。launchd
模板已备好：``scripts/launchagents/com.dailystock.excursion-daily.plist``
——**本仓库不代为加载**，是否加载是用户动作（F-1 同款门槛，见
`New-docs/phase1/18` §8）；``--daily`` 运行结束会打印加载命令。写入面仅两处
且均幂等：``market_5m_bars``（插入-跳过）与
``journal_v2_episode_excursions``（append-only；同 code_version 幂等，仅当
瞬时失败后 bars 补到、且重算结果严格更好时以 attempt+1 追加）。只读红线
不变：不改任何既有行，不下单。
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

logger = logging.getLogger("backfill_excursions")

_FETCH_SLEEP_SECONDS = 0.6  # 顺序抓取，供应商配额友好
_DEFAULT_WINDOW_DAYS = 59   # yfinance 5m 硬约束 ~60 天


def _today_et_date() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=4)
    ).strftime("%Y-%m-%d")


def _et_date(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc) - timedelta(hours=4)
    ).strftime("%Y-%m-%d")


def _fetch_history(symbol: str, period: str, days: int) -> tuple[list, Optional[str]]:
    """走 /stocks/{code}/history 同一服务端 loader（含缓存与 fallback）。"""
    from src.services.stock_service import StockService

    result = StockService().get_history_data(
        symbol, period=period, days=days, include_stock_name=False
    )
    return list(result.get("data") or []), result.get("source")


def _collect_episodes(account_key: str) -> tuple[int, list[dict[str, Any]]]:
    from src.journal.ledger.episode_repository import _latest_build
    from src.journal.ledger.models import PositionEpisode
    from src.journal.ledger.repository import init_ledger_schema
    from src.storage import get_db

    init_ledger_schema()
    db = get_db()
    with db.session_scope() as session:
        build = _latest_build(session, account_key)
        if build is None:
            raise SystemExit("no episode build for account; import evidence first")
        build_id = int(build.id)
        rows = session.execute(
            select(PositionEpisode).where(
                PositionEpisode.episode_build_id == build_id,
                PositionEpisode.lifecycle_status == "closed",
            )
        ).scalars().all()
        episodes = [
            {
                "id": int(row.id),
                "raw_symbol": str(row.raw_symbol),
                "underlying": str(row.underlying).strip().upper(),
                "asset_type": str(row.asset_type),
                "option_right": (
                    str(row.option_right)
                    if row.option_right is not None
                    else None
                ),
                "direction": str(row.direction),
                "opened_at": row.opened_at.replace(tzinfo=timezone.utc)
                if row.opened_at is not None and row.opened_at.tzinfo is None
                else row.opened_at,
                "closed_at": row.closed_at.replace(tzinfo=timezone.utc)
                if row.closed_at is not None and row.closed_at.tzinfo is None
                else row.closed_at,
            }
            for row in rows
        ]
    return build_id, episodes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=None, help="ledger account key")
    parser.add_argument(
        "--window-days",
        type=int,
        default=_DEFAULT_WINDOW_DAYS,
        help="5m fetch window (vendor retention ~60d)",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="incremental mode: only underlyings active in the last few days",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report only, no writes"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="cap processed episodes (0 = all)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    from src.journal.excursions import (
        EXCURSION_CODE_VERSION,
        ExcursionEpisode,
        compute_atr14,
        compute_excursion,
    )
    from src.journal.ledger.excursion_repository import (
        append_episode_excursion,
        get_episode_excursion,
        load_market_5m_bars,
        persist_market_5m_bars,
    )
    from src.journal.ledger.repository import DEFAULT_LEDGER_ACCOUNT_KEY
    from src.opportunities.intraday_bursts import (
        filter_regular_session_bars,
        normalize_bar_label_convention,
    )

    account_key = args.account or DEFAULT_LEDGER_ACCOUNT_KEY
    build_id, episodes = _collect_episodes(account_key)
    today_et = _today_et_date()
    retention_start_et = (
        datetime.now(timezone.utc)
        - timedelta(hours=4)
        - timedelta(days=args.window_days)
    ).strftime("%Y-%m-%d")

    # 待计算＝当前 code_version 尚无记录的已平仓回合；外加重试候选：
    # 既有最优行 status ∈ {missing_bars, partial} 且回合仍在保留窗口内
    # （瞬时抓取失败不该被永久冻结——bars 后到时允许重算，仅在严格更好时
    # 以 attempt+1 追加，无新 bar 则零写）。
    pending = []
    retry_count = 0
    for episode in episodes:
        stored = get_episode_excursion(
            episode_build_id=build_id, position_episode_id=episode["id"]
        )
        if stored is None:
            pending.append(episode)
            continue
        if (
            stored.status in {"missing_bars", "partial"}
            and episode["closed_at"] is not None
            and _et_date(episode["closed_at"]) >= retention_start_et
        ):
            pending.append(episode)
            retry_count += 1
    if args.daily:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=5)
        )
        pending = [
            episode
            for episode in pending
            if episode["closed_at"] is not None
            and episode["closed_at"] >= cutoff
        ]
    if args.limit:
        pending = pending[: args.limit]

    in_window = [
        episode
        for episode in pending
        if episode["closed_at"] is not None
        and _et_date(episode["closed_at"]) >= retention_start_et
    ]
    out_of_window = [ep for ep in pending if ep not in in_window]
    underlyings = sorted({episode["underlying"] for episode in in_window})

    logger.info(
        "build=%s closed=%s pending=%s (retry=%s) in_window=%s "
        "out_of_window=%s underlyings=%s retention_start_et=%s",
        build_id,
        len(episodes),
        len(pending),
        retry_count,
        len(in_window),
        len(out_of_window),
        len(underlyings),
        retention_start_et,
    )

    # --- 逐 underlying：抓 5m（含缓存）→ 落库；抓日线 → ATR14 ---------------
    atr_daily_bars: dict[str, list] = {}
    bar_report: dict[str, dict[str, Any]] = {}
    for symbol in underlyings:
        try:
            raw_bars, source = _fetch_history(symbol, "5m", args.window_days)
            bars = filter_regular_session_bars(
                normalize_bar_label_convention(raw_bars, source=source)
            )
        except Exception as exc:  # noqa: BLE001 - 单标的失败不拖垮回填
            logger.warning("5m fetch failed symbol=%s error=%s", symbol, exc)
            bar_report[symbol] = {"error": type(exc).__name__}
            continue
        written = skipped = 0
        if bars and not args.dry_run:
            written, skipped = persist_market_5m_bars(
                symbol, bars, source=source
            )
        sessions = sorted({row["session_date_et"] for row in bars})
        bar_report[symbol] = {
            "fetched": len(bars),
            "written": written,
            "skipped": skipped,
            "coverage": (
                f"{sessions[0]}..{sessions[-1]} ({len(sessions)} sessions)"
                if sessions
                else "none"
            ),
        }
        try:
            daily_rows, _daily_source = _fetch_history(symbol, "daily", 200)
            atr_daily_bars[symbol] = daily_rows
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily fetch failed symbol=%s error=%s", symbol, exc)
            atr_daily_bars[symbol] = []
        time.sleep(_FETCH_SLEEP_SECONDS)

    # --- 逐回合计算 ---------------------------------------------------------
    status_counts: dict[str, int] = {}
    written_rows = 0
    retry_skipped_rows = 0
    for episode in pending:
        opened_at = episode["opened_at"]
        closed_at = episode["closed_at"]
        if opened_at is None or closed_at is None:
            continue
        start_session = (_et_date(opened_at))
        end_session = _et_date(closed_at)
        bars = load_market_5m_bars(
            episode["underlying"],
            start_session_et=start_session,
            end_session_et=end_session,
        )
        atr14 = compute_atr14(
            atr_daily_bars.get(episode["underlying"], ()),
            datetime.fromisoformat(start_session).date(),
        )
        result = compute_excursion(
            ExcursionEpisode(
                asset_type=episode["asset_type"],
                direction=episode["direction"],
                option_right=episode["option_right"],
                opened_at=opened_at,
                closed_at=closed_at,
            ),
            bars,
            atr14=atr14,
        )
        if (
            result.status == "missing_bars"
            and end_session < retention_start_et
        ):
            # 超出供应商保留窗口且本地无 bar：永久标缺，原因如实写明。
            from dataclasses import replace as _dc_replace

            result = _dc_replace(
                result,
                status_reason=(
                    f"回合平仓于 {end_session}，早于 5m 数据保留窗口起点 "
                    f"{retention_start_et}（约 {args.window_days} 天），且本地"
                    "无持久化 bar——永久标缺，不以 0 或日线冒充"
                ),
            )
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
        if not args.dry_run:
            append_result = append_episode_excursion(
                result,
                account_key=account_key,
                episode_build_id=build_id,
                position_episode_id=episode["id"],
                allow_retry=True,
            )
            if append_result.created:
                written_rows += 1
            else:
                retry_skipped_rows += 1

    # --- 报告 ---------------------------------------------------------------
    print("=== backfill_excursions report ===")
    print(f"build_id={build_id} account={account_key} as_of_et={today_et}")
    print(
        f"code_version={EXCURSION_CODE_VERSION} "
        f"retention_start_et={retention_start_et} dry_run={args.dry_run}"
    )
    print(f"closed_episodes={len(episodes)} pending={len(pending)} "
          f"(retry_candidates={retry_count}) "
          f"excursion_rows_written={written_rows} "
          f"not_better_skipped={retry_skipped_rows}")
    print(f"status_counts={status_counts}")
    print("--- per-symbol 5m coverage ---")
    for symbol in sorted(bar_report):
        print(f"  {symbol}: {bar_report[symbol]}")
    if args.daily:
        # 调度提示（复核修复 10）：plist 模板在仓库内，加载是用户动作。
        print("--- scheduling (user action; NOT loaded by this repo) ---")
        print(
            "  template: scripts/launchagents/"
            "com.dailystock.excursion-daily.plist"
        )
        print(
            "  load it yourself after substituting __PROJECT_DIR__/"
            "__PYTHON__ (see scripts/install_launchagents.sh for the "
            "pattern):"
        )
        print(
            "    launchctl load ~/Library/LaunchAgents/"
            "com.dailystock.excursion-daily.plist"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

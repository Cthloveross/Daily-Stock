# -*- coding: utf-8 -*-
"""Daily opportunity research endpoint.

The endpoint loads daily bars and the stored Regime snapshot, then delegates
all signal logic to the pure ``src.opportunities`` engine.  It never calls an
LLM, writes a signal record, or invokes a broker trading action.
"""
from __future__ import annotations

import copy
import logging
import math
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from api.v1.schemas.opportunities import (
    DailyOpportunityRequest,
    DailyOpportunityResponse,
    IntradayPulseResponse,
    IntradayTopRequest,
    IntradayTopResponse,
    IntradayTrackingRequest,
    IntradayTrackingResponse,
    NearExpiryContractRequest,
    NearExpiryContractResponse,
    OpportunityLearningSummaryResponse,
    OpportunitySnapshotDetailResponse,
    OpportunitySnapshotEnsureResponse,
    OpportunitySnapshotEvaluationResponse,
    OpportunitySnapshotItem,
    OpportunitySnapshotListResponse,
    OptionContextRequest,
    OptionContextResponse,
    OptionOverviewRequest,
    OptionOverviewResponse,
    OptionEventRequest,
    OptionEventResponse,
    OptionWallRequest,
    OptionWallResponse,
    OptionWallSnapshotRequest,
    OptionWallSnapshotResponse,
    OptionWallSnapshotResultItem,
    PremarketCycleRequest,
    PremarketCycleResponse,
    PremarketUniversePutRequest,
    PremarketUniverseResponse,
)
from src.opportunities.repository import SnapshotConflictError
from src.opportunities.wall_snapshot_repository import (
    OptionWallSnapshotError,
    record_wall_snapshots,
)
from src.opportunities.engine import (
    SIGNAL_VERSION,
    DailyHistoryInput,
    build_daily_opportunity_run,
    completed_daily_bars,
    is_supported_us_option_underlying,
    normalize_symbols,
)
from src.opportunities.intraday import (
    ATR14_METHOD,
    INTRADAY_TRACKING_VERSION,
    SESSION_PHASE_HINT_BASIS,
    SESSION_STATE_BASIS,
    VOLUME_PACE_BASIS,
    VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
    compute_atr14,
    compute_prior_full_day_median_volume,
    compute_session_vwap,
    compute_volume_pace,
    market_session_phase,
    market_session_state,
    session_phase_label,
)
from src.opportunities.intraday_bursts import (
    compute_session_burst_profile,
    unavailable_burst_profile,
)
from src.opportunities.intraday_top import (
    EARNINGS_WINDOW_DAYS,
    INTRADAY_TOP_SIGNAL_VERSION,
    MARKET_CONTEXT_TICKER,
    IntradayDailyContext,
    IntradayQuoteInput,
    build_intraday_top_run,
    compute_earnings_proximity,
    compute_intraday_daily_context,
)
from src.opportunities.lane_availability import (
    LANE_AVAILABILITY_MAX_DTE,
    build_lane_availability,
    build_ticker_availability,
)
from src.opportunities.near_expiry_contracts import (
    FORMULA_VERSION as NEAR_EXPIRY_FORMULA_VERSION,
    NEAR_MONEY_MIN_STRIKES_PER_SIDE,
    NEAR_MONEY_PERCENT_BAND,
    STRIKE_WINDOW_BASIS as NEAR_EXPIRY_STRIKE_WINDOW_BASIS,
    build_near_expiry_contract_payload,
)
from src.opportunities.option_walls import (
    ATM_CALL_IV_METHOD as OPTION_WALL_ATM_CALL_IV_METHOD,
    CALL_PUT_RATIO_CAVEAT as OPTION_WALL_CALL_PUT_RATIO_CAVEAT,
    FORMULA_VERSION as OPTION_WALL_FORMULA_VERSION,
    METRIC_BASIS_SESSION_VOLUME as OPTION_WALL_METRIC_BASIS_SESSION_VOLUME,
    METRIC_BASIS_SETTLED_OI as OPTION_WALL_METRIC_BASIS_SETTLED_OI,
    OI_WEIGHTED_CENTER_LABEL as OPTION_WALL_OI_WEIGHTED_CENTER_LABEL,
    OI_WEIGHTED_CENTER_METHOD as OPTION_WALL_OI_WEIGHTED_CENTER_METHOD,
    RATIO_REASON_NO_DATA as OPTION_WALL_RATIO_REASON_NO_DATA,
    build_option_wall_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_NEW_YORK = ZoneInfo("America/New_York")
_MAX_FETCH_WORKERS = 4
_MAX_OPTION_WALL_WORKERS = 5
_SCAN_CACHE_TTL_SECONDS = 30.0
# 完成态缓存条目很小（纯 dict），但 key 空间不小：逐标的的 option_context /
# option_event / near_expiry / option_wall 各占一键。32 的旧上限在 20 标的
# 清单下会互相挤兑（缓存刚写就被淘汰 → 重复供应商请求）；128 覆盖现实
# key 数量仍然有界。
_SCAN_CACHE_MAX_ENTRIES = 128
_SCAN_FLIGHT_LEASE_SECONDS = 30.0
_SCAN_FOLLOWER_WAIT_SECONDS = 30.0
# 单飞计算与请求线程解耦（leader 也有截止时间）：工厂在共享有界线程池上
# 执行，请求线程只做有界等待，租约到点返回 504、孤儿计算继续在后台完成并
# 发布到缓存。池大小覆盖「同时在算的 key 数」的现实上界（逐标的 key 由
# 各自 30–60 秒 TTL + 单飞去重压到个位数～十几个）；极端排队时等待方也
# 只会等到租约到期的 504，绝不无界悬挂。
_SCAN_EXECUTOR_MAX_WORKERS = 16
_OPTION_CONTEXT_VERSION = "nearest_expiry_atm_call_iv_v1"
_OPTION_CONTEXT_SOURCE = "moomoo_openapi"
_OPTION_OVERVIEW_VERSION = "moomoo_option_underlying_overview_v1"
_OPTION_OVERVIEW_SOURCE = "moomoo_openapi"
_OPTION_WALL_VERSION = "observable_option_walls_v1_3"
_OPTION_WALL_SOURCE = "moomoo_openapi"
_OPTION_EVENT_VERSION = "moomoo_unusual_option_events_v1"
_OPTION_EVENT_SOURCE = "moomoo_openapi"
_NEAR_EXPIRY_SCHEMA = "near-expiry-contracts/1.0"
_NEAR_EXPIRY_SOURCE = "moomoo_openapi"
# 临期合约面板是合约选择参考，不是推荐引擎：limitations 随每个响应携带。
_NEAR_EXPIRY_LIMITATIONS = (
    "OI 为上一清算交易日（T-1）结算口径，不是盘中实时持仓。",
    "Bid/Ask、点差与流动性随时变化，快照读数不等于可成交价格。",
    "IV 为供应商模型值，不表达涨跌方向。",
    "本面板仅为合约选择研究参考，不构成合约推荐或买卖建议。",
    "执行前以券商实时盘口为准。",
)
_OPTION_CONTEXT_LIMITATIONS = (
    "仅为 Moomoo 最近到期合约中最接近现价的 Call 单点隐含波动率。",
    "不是 IV Rank 或 IV Percentile，不包含历史隐含波动率分布。",
    "不是异常期权流或期权大单检测，不推断成交方向、开平仓或买卖意图。",
    "仅作研究上下文，不是买卖信号。",
)
_OPTION_OVERVIEW_LIMITATIONS = (
    "Call/Put Volume 是 Moomoo 当前交易时段累计活动，不代表开仓、平仓或方向性资金流。",
    "Open Interest 是 T-1 清算后的未平仓合约总量，本身不代表看多或看空。",
    "IV、IV Rank、IV Percentile 与 HV 均为供应商模型/统计值；IV 不提供涨跌方向。",
    "Put/Call 比率只描述活动或存量结构，不是独立买卖信号。",
)
_OPTION_WALL_LIMITATIONS = (
    "OI 是清算后的未平仓合约总量，不显示买卖方、开平仓或做市商库存方向。",
    "Volume 是当日累计成交量，不代表新增仓位仍然存在。",
    "Gamma 集中墙使用绝对 Gamma，只表示风险集中度，不是真实 Dealer GEX。",
    "墙位不保证支撑、阻力、钉仓、突破或反转，仅作期权结构研究上下文。",
    OPTION_WALL_CALL_PUT_RATIO_CAVEAT,
)
_OPTION_WALL_ASSUMPTIONS = (
    "Gamma concentration = abs(gamma) × OI × contract size × spot² × 1%。",
    "不从公开 OI 推断 dealer 净多或净空 Gamma。",
)
_INTRADAY_TRACKING_SOURCE = "moomoo_openapi"
_INTRADAY_TRACKING_SCHEMA = "intraday-tracking/1.0"
# 日内 Top 榜是盘中滚动研究：60 秒 TTL 与前端 60 秒轮询对齐，一个轮询周期内
# 的重复请求（多标签页/多组件）只触发一次完整装配。
_INTRADAY_TOP_CACHE_TTL_SECONDS = 60.0
# 服务端预热循环使用的默认 limit：与 IntradayTopRequest.limit 默认值及页面
# 默认轮询（limit=5）一致，保证预热命中的正是默认轮询的缓存 key。
_INTRADAY_TOP_DEFAULT_LIMIT = 5
# 20 个标的 × 逐标的期权异动一页读取可能超过默认 30 秒 lease；给装配一个
# 明确的更长租约而不是放大默认值。
_INTRADAY_TOP_LEASE_SECONDS = 45.0
# 异动聚合读取最近一页最多 10 条（Moomoo 单页上限内），与 option-events
# 端点共用同一逐标的 30 秒 TTL 缓存 key。
_INTRADAY_TOP_EVENT_PAGE_SIZE = 10
_INTRADAY_TOP_MAX_EVENT_WORKERS = 4
# 波段爆发的 5m K 线：走与 /stocks/{code}/history?period=5m 相同的服务端
# 加载器；只需要当前 + 上一交易时段，跨周末取 4 个自然日再在纯函数内裁剪。
_INTRADAY_BURST_FETCH_DAYS = 4
_INTRADAY_BURST_MAX_WORKERS = 4
# 逐标的 60 秒 TTL：5m K 线每 5 分钟才推进一个 bucket，60 秒缓存保证同一
# bucket 内最多重取一次，且轮询周期（60s）内的重复请求全部命中缓存。
_INTRADAY_BURST_CACHE_TTL_SECONDS = 60.0
_INTRADAY_BURST_CACHE_MAX_ENTRIES = 64
_INTRADAY_PULSE_SCHEMA = "intraday-pulse/1.0"
_INTRADAY_PULSE_CACHE_TTL_SECONDS = 60.0
# SPY/QQQ 走美股 ETF 快照；VIX 指数在 Moomoo 美股快照中不保证可得，
# 单独隔离请求，失败时显式 unavailable，绝不以 0 或旧值冒充。
_INTRADAY_PULSE_CORE_SYMBOLS = ("SPY", "QQQ")
_INTRADAY_PULSE_OPTIONAL_SYMBOLS = ("VIX",)
_INTRADAY_PULSE_LIMITATIONS = (
    "市场脉搏为 Moomoo 快照读数：涨跌以快照自带前收为基准，休市时段显示最近一个交易时段。",
    "VIX 若供应商快照不可得则显式标缺，不用其他来源或旧值冒充。",
    "VWAP 位置为当日累计成交额 ÷ 累计成交量的近似值，非逐笔官方 VWAP；缺任一输入即显式标缺。",
    "时段标签由 America/New_York 时钟判定（未接入交易所假日日历）；时段提示为用户自身历史交易统计"
    "的硬编码 v1 文案，不是市场统计或买卖信号——系统标注，用户过滤。",
    "仅作盘中背景，不是信号，不进入任何统计。",
)
# v3 财报日历：每个 ET 交易日一次 Finnhub 区间调用（当日 → +5 天），缓存
# ≥1 小时供全 universe 复用；失败结果只短缓存 10 分钟（60 秒轮询不至于打爆
# Finnhub，也不把一次失败冻结一整天）。日历不可得时逐候选显式标缺。
_INTRADAY_EARNINGS_CACHE_TTL_SECONDS = 3600.0
_INTRADAY_EARNINGS_FAILURE_TTL_SECONDS = 600.0
_INTRADAY_EARNINGS_SOURCE = "finnhub_earnings_calendar"
# Completed daily bars only change once per session; memoise the derived
# ATR14 / 20-session median inputs so a 60s polling panel does not re-run the
# daily-history providers on every tick.  Live quote fields are never cached
# here — they go through the shared 30s scan cache only.
_INTRADAY_DAILY_CACHE_TTL_SECONDS = 900.0
_INTRADAY_DAILY_CACHE_MAX_ENTRIES = 64
# --- 今日车道可用性（V2-E）------------------------------------------------
# 「今天这些标的有没有 0DTE」只需要期权到期日元数据：复用临期合约链读取
# 路径的第一步（fetch_expiry_availability_moomoo = 同一个独占 wall lane +
# 同一个 get_option_expiration_date），不发 get_option_chain 日期窗口、不取
# underlying 快照、不发 get_market_snapshot 批次——比整条链读取便宜一个量级。
# 额度护栏（对齐 §2.1 记录的 10 次链查询 / 30 秒）：
#   ① 逐标的按 ET 交易日缓存 1 小时——到期日一天最多变一次，同一交易日内
#      的 60 秒轮询稳态命中缓存、零供应商请求；失败只短缓存 5 分钟，避免
#      一次抖动把一整天钉死在 unknown；
#   ② 单轮新增读取上限 8（60 秒轮询周期内 ≤8 次 < 10 次/30 秒），超出的标的
#      本轮显式记为 unavailable（deferred）并在下一轮补齐——绝不为了凑齐
#      结论而把「没查」说成「没有 0DTE」；
#   ③ 单轮参与判定的标的上限 12（＝ INTRADAY_DEEP_LANE_MAX 默认值），
#      不向宽层全清单扇出。
_LANE_AVAILABILITY_CACHE_TTL_SECONDS = 3600.0
_LANE_AVAILABILITY_FAILURE_TTL_SECONDS = 300.0
_LANE_AVAILABILITY_CACHE_MAX_ENTRIES = 64
_LANE_AVAILABILITY_MAX_SYMBOLS = 12
_LANE_AVAILABILITY_MAX_NEW_FETCHES = 8
_LANE_AVAILABILITY_MAX_WORKERS = 4
_LANE_AVAILABILITY_CHECKED_SCOPE = "intraday_deep_lane_tickers"
# 车道可用性只在日内榜单飞 leader 内被调用，而 leader 自己有 45 秒租约：
# 到期日读取的 wall lane 获取等待必须远小于租约（旧值 30 秒 × 最多 8 个
# 标的可以独占整个租约）。lane 正忙不是失败——该标的推迟到下一轮
# （deferred，不缓存失败），deferred_tickers 机制已如实呈现。
_LANE_AVAILABILITY_LANE_WAIT_SECONDS = 2.5
# --- watchlist v1 两层扫描（INTRADAY_WATCHLIST 配置后启用；未配置零改动）---
# 宽层（tier-1）：整个清单每个 60 秒轮询周期只发 1 次 Moomoo
# ``get_market_snapshot`` 批量快照（官方单次上限 400 个代码）。清单上限 200
# （含 SPY 与计划钉选后仍 << 400），因此永远单请求、无需分片；超出部分显式
# 截断并在 universe_scan 中如实标注 watchlist_truncated，绝不无声丢弃。
# 深度层（tier-2）：仅晋升标的走既有 v4 管线。其中 5m K 线经
# StockService.get_history_data → DataFetcherManager.get_intraday_data 优先命中
# Moomoo ``request_history_kline``（失败回退 yfinance）。Moomoo 历史 K 线额度
# 语义（官方 API Limits）：**30 天滚动窗口内的去重标的数**配额，账户档位
# 100/300/1000/2000；同一标的 30 天内重复请求不再扣额，同一标的的日线 / 5m
# 等不同周期只记 1 个额度。因此深度层的真实约束是「30 天内晋升过的去重标的
# 数」，其上界＝清单长度（晋升只会从清单 + 当日计划中产生）；为防单日异常
# 轮换（movers 高频换血）另设每 ET 日新晋升去重标的数上限（内部常量，非
# 环境变量）。触顶后新标的当日只保留宽层快照行并在 universe_scan 标注
# day_promotion_cap_reached，绝不静默丢弃。
_INTRADAY_WATCHLIST_MAX_SYMBOLS = 200
_MOOMOO_SNAPSHOT_MAX_CODES_PER_REQUEST = 400  # 官方文档单次快照上限
_INTRADAY_DEEP_DAILY_DISTINCT_CAP = 30
# 盘中计划提升（请求内 focus_symbols）的服务端硬上界：与 schema 的 max_length
# 一致（≤8），两处都设是为了「请求侧被绕过时服务端仍有界」。它与配置钉选
# 同语义（不占异动额度、不受日上限约束），因此同样受 30 天去重配额约束。
_INTRADAY_FOCUS_MAX_SYMBOLS = 8
# 扫描表深度层总行数硬顶（用户明确要求「留 8 个」）。异动保底名额保证扫描表
# 始终保留发现能力；被总数挤掉的标的进 universe_scan.trimmed_by_total_cap。
_INTRADAY_DEEP_LANE_TOTAL_MAX = 8
_INTRADAY_DEEP_MOVER_FLOOR = 4
_INTRADAY_TWO_TIER_GATE_BASIS = "abs_change_percent_then_turnover_v1"
# 盘前时段（ET 04:00–09:30）：Moomoo 常规快照字段仍指向上一常规时段，
# 真实盘前变动只在 pre_* 字段。盘前闸门按 |pre_change_rate|→pre_turnover
# 排序；缺盘前字段的标的不可按盘前异动晋升（绝不以 0 冒充「平静」）。
# 若整批快照都无盘前字段（权限缺失等），显式回退常规口径并携带警示，
# 绝不静默假装在按盘前排序。
_INTRADAY_TWO_TIER_PREMARKET_GATE_BASIS = (
    "premarket_pre_price_change_then_pre_turnover_v1"
)
_INTRADAY_PREMARKET_FIELDS_UNAVAILABLE_WARNING = (
    "premarket_fields_unavailable_ranking_reflects_prior_session"
)
# --- 异动闸门 v2：15 分钟动量主导（2026-08-03 首个实盘日校准）----------------
# 校准背景：2026-08-03 为普涨跳空日，v1 闸门按 |当日涨跌幅| 排序，深度层被
# +5%~+11% 的隔夜跳空标的（RBLX/CRWV/TEAM…）长期占满；用户当日唯一认定
# 「真正能交易」的 NVDA（+2.5% 稳步爬升，10:00–10:15 中波段爆发分 5.06）整日
# 没能晋升深度层。结论：「谁今天涨得多」≠「谁现在在动」——隔夜跳空后横盘的
# 标的当日涨跌幅恒定居高，却没有任何盘中动量。v2 在非盘前时段把 K 个异动
# 名额拆成两个子额度：
#   ceil(2K/3) 档按 |mom15|（最近 15 分钟动量，「谁现在在动」）降序；
#   其余名额按 |当日涨跌幅|（「谁今天最大」）降序兜底；
# 动量侧先占位、再去重，两侧各以成交额次序、代码字典序兜底（确定性）。
# mom15 由宽层每轮批量快照喂养的进程内价格滚动历史推导（零新增请求）：取
# 12–18 分钟回看窗内「最老」样本计算 (last/price_15m_ago − 1)×100；窗内无
# 足龄样本（服务重启 / 开盘冷启动 / 样本已过期）时 mom15=None，该标的只能
# 走当日涨跌子额度。整批皆无 mom15 时显式回退 v1 口径并携带 gate_warnings
# 警示，绝不静默。盘前口径（pre_* 字段，G-12）完全不变。
_INTRADAY_TWO_TIER_GATE_BASIS_V2 = "momentum15m_then_day_change_v2"
_INTRADAY_MOMENTUM_WARMING_UP_WARNING = (
    "momentum_history_warming_up_ranking_by_day_change"
)
_INTRADAY_MOMENTUM_LOOKBACK_MIN_SECONDS = 12 * 60
_INTRADAY_MOMENTUM_LOOKBACK_MAX_SECONDS = 18 * 60
# 样本保留是**时间制**（回看窗上限 + 2 分钟缓冲）：旧的 40 条计数上限在
# 工厂每分钟跑 >2 次时（refresh、多调用方）覆盖跌破 12 分钟回看下限，
# mom15 永久饥饿、闸门静默退回 v1。maxlen 仅作硬性兜底（240 条 ≈ 每 5 秒
# 一样本仍覆盖满 20 分钟），真正的裁剪按样本年龄进行。
_INTRADAY_MOMENTUM_HISTORY_MAXLEN = 240
_INTRADAY_MOMENTUM_RETENTION_SECONDS = (
    _INTRADAY_MOMENTUM_LOOKBACK_MAX_SECONDS + 120
)
# 今日深扫账本（display truth）：进程内 per-ET-day 保留每个曾晋升深度层标的
# 最后一次深扫的候选摘要（含分级波段），轮换出深度层后仍以「今日曾深扫」
# as-of 行呈现——2026-08-03 实盘：ORCL 09:40 记录强波段后被后续 movers 挤出
# 可见深度层，用户回看时波段整段消失。账本重启即清空（无 DB 写入），
# basis 字段如实声明只从服务启动后累计。
_INTRADAY_DAY_LEDGER_BASIS = "in_process_since_service_start_resets_on_restart"
# 今日冻结盘前计划标的（深度层钉选）只读缓存：计划盘前冻结后当日不变。
_INTRADAY_PLAN_CACHE_TTL_SECONDS = 300.0
_INTRADAY_TWO_TIER_LIMITATIONS = (
    "两层扫描：全清单每轮仅一次批量快照；只有异动闸门晋升的标的做深度分析"
    "（波段爆发/形态/速度/异动/财报），其余标的仅快照、对应字段一律缺席，"
    "绝不虚构。",
    "异动闸门为启发式（v2）：非盘前时段 K 名额拆两档——约 2/3 按最近 15 分钟"
    "动量 |mom15|、其余按 |当日涨跌幅| 兜底（动量历史不足时显式回退当日涨跌"
    "并警示）；当日冻结盘前计划与用户钉选标的始终占深度位，不占 K 名额。"
    "闸门不是信号，晋升不代表方向或质量结论。",
    "「今日曾深扫」账本为进程内展示缓存：只保留各标的最后一次深扫的 as-of"
    " 摘要，不实时刷新、重启即清空、不写数据库。",
    "深度层 5m K 线走 Moomoo request_history_kline（30 天滚动去重标的数配额，"
    "账户档位 100 起）；每 ET 日新晋升去重标的数另设内部上限，触顶后新标的"
    "当日仅保留快照行并显式标注。",
)
_INTRADAY_TRACKING_LIMITATIONS = (
    "盘中跟踪只对照已冻结的盘前计划，不重新排序，不生成买卖信号。",
    "VWAP 为当日累计成交额 ÷ 累计成交量的近似值，不是逐笔加权的官方 VWAP。",
    "量能节奏对比 20 个交易日的全日成交量中位数，未按盘中时点折算；开盘初段比值偏低属正常。",
    "ATR14 基于已完成日线的 Wilder 平滑，不包含当日未完成 K 线。",
    "盘段状态由 America/New_York 时钟判断，未接入交易所假日日历。",
)
_OPTION_EVENT_LIMITATIONS = (
    "仅返回 Moomoo get_option_event 为该标的识别的最近一页异动成交，不是完整逐笔期权流。",
    "ticker_type 与 sentiment 是 Moomoo 分类，不独立证明主动买卖、开平仓或真实交易意图。",
    "公开成交无法识别对手方或 dealer 仓位，不据此推断 dealer 方向。",
    "此数据暂不进入今日机会排序，仅作候选标的研究上下文，不是买卖信号。",
)
_OPTION_EVENT_FIELDS = (
    "event_id",
    "option_code",
    "owner_code",
    "symbol",
    "fill_time",
    "ticker_type",
    "price",
    "volume",
    "turnover",
    "option_type",
    "strike_price",
    "expiry",
    "dte",
    "underlying_price",
    "bid_price",
    "ask_price",
    "iv_percent",
    "total_volume",
    "total_open_interest",
    "vo_ratio_percent",
    "delta",
    "sentiment",
    "order_types",
    "strategy_type",
)


@dataclass
class _ScanCacheEntry:
    expires_at: float
    result: dict[str, Any]
    # 延迟诊断（additive）：完成态条目记住原始工厂墙钟耗时与发起方，命中
    # 缓存的响应报告**原始**生成耗时，预热条目可被如实标为 warm_cache。
    generated_in_seconds: Optional[float] = None
    generated_by: str = "request"


@dataclass
class _ScanFlight:
    generation: int
    started_at: float
    deadline_at: float
    # 迟到发布用的完成态 TTL：孤儿计算（超过租约后才算完）写缓存时沿用
    # 启动方声明的 TTL，让下一次轮询直接命中，绝不丢弃已算出的结果。
    ttl_seconds: float = _SCAN_CACHE_TTL_SECONDS
    # 发起方标记：预热调度器发起的 flight 记为 warm_scheduler，随完成态
    # 缓存条目一起发布——载荷本身绝不特殊化。
    initiated_by: str = "request"
    generated_in_seconds: Optional[float] = None
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[dict[str, Any]] = None
    error: Optional[BaseException] = None


class OpportunityScanTimeoutError(TimeoutError):
    """Raised when a shared provider scan exceeds its bounded lease."""


_scan_cache_lock = threading.RLock()
_scan_cache: dict[tuple[Any, ...], _ScanCacheEntry] = {}
_scan_flights: dict[tuple[Any, ...], _ScanFlight] = {}
_scan_flight_generation = 0
_intraday_daily_cache: dict[tuple[str, str], _ScanCacheEntry] = {}
_intraday_burst_cache: dict[tuple[str, str, str], _ScanCacheEntry] = {}
_intraday_earnings_cache: dict[str, _ScanCacheEntry] = {}
# 车道可用性：(symbol, ET 交易日) → 到期日读数（成功 1 小时 / 失败 5 分钟）。
_lane_availability_cache: dict[tuple[str, str], _ScanCacheEntry] = {}
_intraday_playbook_cache: dict[str, _ScanCacheEntry] = {}
_intraday_plan_cache: dict[str, _ScanCacheEntry] = {}
# 每 ET 日已进入深度层的去重标的集合（额度护栏，语义见上方常量注释）。
_intraday_deep_promotion_log: dict[str, set[str]] = {}
# 宽层滚动价格历史（mom15 输入）：symbol → deque[(epoch_seconds, last_price)]。
# 仅常规时段喂养——盘前/盘后常规快照字段仍指向上一常规时段，喂入只会污染
# 动量口径。ET 日期切换即整体清空；重启即冷启动（闸门显式回退并警示）。
_intraday_momentum_history: dict[str, deque[tuple[float, float]]] = {}
_intraday_momentum_history_date: Optional[str] = None
# 今日深扫账本：ET 日期 → symbol → 最后一次深扫候选摘要（as-of，不刷新）。
_intraday_day_ledger: dict[str, dict[str, dict[str, Any]]] = {}


def _cache_now() -> float:
    return time.monotonic()


def _scan_cache_key(
    symbols: list[str],
    limit: int,
    market_date_et: Optional[date | str] = None,
) -> tuple[Any, ...]:
    market_date = market_date_et or datetime.now(timezone.utc).astimezone(
        _NEW_YORK
    ).date()
    return (SIGNAL_VERSION, str(market_date), tuple(symbols), int(limit))


def _option_context_cache_key(
    symbol: str,
    enabled: bool,
    market_date_et: str,
) -> tuple[Any, ...]:
    """Cache one symbol so overlapping three-symbol batches share quota work."""

    return (
        "option_context",
        _OPTION_CONTEXT_VERSION,
        bool(enabled),
        symbol,
        market_date_et,
    )


def _option_overview_cache_key(
    symbols: list[str],
    enabled: bool,
    market_date_et: str,
) -> tuple[Any, ...]:
    return (
        "option_overview",
        _OPTION_OVERVIEW_VERSION,
        bool(enabled),
        tuple(symbols),
        market_date_et,
    )


def _option_wall_cache_key(
    symbol: str,
    enabled: bool,
    market_date_et: str,
    dte_min: int,
    dte_max: int,
) -> tuple[Any, ...]:
    return (
        "option_wall",
        _OPTION_WALL_VERSION,
        bool(enabled),
        symbol,
        market_date_et,
        int(dte_min),
        int(dte_max),
    )


def _option_event_cache_key(
    symbol: str,
    enabled: bool,
    market_date_et: str,
    limit_per_symbol: int,
) -> tuple[Any, ...]:
    return (
        "option_event",
        _OPTION_EVENT_VERSION,
        bool(enabled),
        symbol,
        market_date_et,
        int(limit_per_symbol),
    )


def _near_expiry_cache_key(
    symbol: str,
    enabled: bool,
    market_date_et: str,
    max_dte: int,
) -> tuple[Any, ...]:
    """(symbol, max_dte, Moomoo 状态, ET 日期) + 30 秒 TTL ≈ 分钟级新鲜度。

    与兄弟期权端点相同的 30 秒完成态 TTL + single-flight：同一分钟内的
    重复请求（多组件/多标签页）只触发一次链 + 快照读取，refresh 只绕过
    已完成 TTL、仍复用在途请求，避免放大供应商额度消耗。
    """

    return (
        "near_expiry_contracts",
        _NEAR_EXPIRY_SCHEMA,
        bool(enabled),
        symbol,
        market_date_et,
        int(max_dte),
    )


def _reset_scan_cache_for_tests() -> None:
    """Clear completed entries between deterministic tests.

    Production code never calls this helper.
    """

    global _intraday_momentum_history_date
    with _scan_cache_lock:
        _scan_cache.clear()
        _intraday_daily_cache.clear()
        _intraday_burst_cache.clear()
        _intraday_earnings_cache.clear()
        _lane_availability_cache.clear()
        _intraday_playbook_cache.clear()
        _intraday_plan_cache.clear()
        _intraday_deep_promotion_log.clear()
        _intraday_momentum_history.clear()
        _intraday_momentum_history_date = None
        _intraday_day_ledger.clear()
        for flight in _scan_flights.values():
            if flight.error is None:
                flight.error = OpportunityScanTimeoutError(
                    "opportunity scan reset during test"
                )
            flight.event.set()
        _scan_flights.clear()


def _prune_scan_cache(now: float) -> None:
    expired = [key for key, entry in _scan_cache.items() if entry.expires_at <= now]
    for key in expired:
        _scan_cache.pop(key, None)
    while len(_scan_cache) >= _SCAN_CACHE_MAX_ENTRIES:
        oldest_key = min(_scan_cache, key=lambda item: _scan_cache[item].expires_at)
        _scan_cache.pop(oldest_key, None)


_scan_executor: Optional[ThreadPoolExecutor] = None
_scan_executor_lock = threading.Lock()


def _get_scan_executor() -> ThreadPoolExecutor:
    """Lazily create the shared bounded worker pool for scan factories."""

    global _scan_executor
    with _scan_executor_lock:
        if _scan_executor is None:
            _scan_executor = ThreadPoolExecutor(
                max_workers=_SCAN_EXECUTOR_MAX_WORKERS,
                thread_name_prefix="opportunity-scan",
            )
        return _scan_executor


def _finish_scan_flight(
    key: tuple[Any, ...],
    flight: _ScanFlight,
    *,
    result: Optional[dict[str, Any]] = None,
    error: Optional[BaseException] = None,
) -> None:
    """Publish one finished computation — even one that outlived its lease.

    迟到的成功结果**绝不丢弃**：只要该 flight 仍是这个 key 的在册计算，
    就写入完成态缓存并清账，下一次轮询直接命中（活锁修复的核心——旧实现
    把超租约完成的结果扔掉再抛 504，慢供应商下永远无人能读到结果）。
    flight 已被测试重置等原因除名时不再发布，防止跨代际污染。
    """

    completion_time = _cache_now()
    with _scan_cache_lock:
        current = _scan_flights.get(key)
        if error is not None:
            if flight.error is None:
                flight.error = error
        else:
            stored = copy.deepcopy(result)
            flight.result = stored
            if current is flight:
                _prune_scan_cache(completion_time)
                _scan_cache[key] = _ScanCacheEntry(
                    expires_at=completion_time + flight.ttl_seconds,
                    result=stored,
                    generated_in_seconds=flight.generated_in_seconds,
                    generated_by=flight.initiated_by,
                )
        if current is flight:
            _scan_flights.pop(key, None)
        flight.event.set()


def _get_or_compute_scan(
    key: tuple[Any, ...],
    factory: Callable[[], dict[str, Any]],
    *,
    bypass_cache: bool = False,
    wait_timeout_seconds: float = _SCAN_FOLLOWER_WAIT_SECONDS,
    lease_seconds: float = _SCAN_FLIGHT_LEASE_SECONDS,
    ttl_seconds: Optional[float] = None,
    initiator: str = "request",
    meta_out: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return a cached scan or share one in-flight computation per key.

    ``bypass_cache`` skips only a completed TTL entry.  A matching in-flight
    request is still shared so repeated refresh clicks cannot multiply provider
    traffic.

    ``initiator`` 标记本次调用方（"request" / "warm_scheduler"），只随完成态
    缓存条目的元数据传播；``meta_out``（additive，可选）由调用方传入空 dict，
    函数在成功返回前写入 ``source``（"cache"/"flight"）、``led_flight``、
    ``generated_in_seconds``（原始工厂墙钟耗时）与 ``generated_by``。载荷
    本身绝不因发起方不同而变化。

    并发合同（2026-08 16 分钟悬挂事故后收紧）：

    - **每个 key 同时至多一个工厂在执行**。工厂跑在共享有界线程池上；
      后到的请求只会加入等待，绝不会因为租约过期而启动第二个工厂
      （旧实现的「过期逐出 + 新 leader」会自我放大重复扫描）。
    - **发起方与跟随方都有截止时间**：等待不超过
      ``min(wait_timeout_seconds, 租约剩余)``，到点抛
      :class:`OpportunityScanTimeoutError`（端点译为可重试 504），
      而工厂继续在后台完成。
    - **迟到结果发布**：超过租约才算完的工厂照常写完成态缓存
      （见 :func:`_finish_scan_flight`），下一次轮询直接命中。
    """

    if wait_timeout_seconds <= 0 or lease_seconds <= 0:
        raise ValueError("scan wait and lease must be positive")

    global _scan_flight_generation
    now = _cache_now()
    started = False
    with _scan_cache_lock:
        _prune_scan_cache(now)
        cached = _scan_cache.get(key)
        if not bypass_cache and cached is not None and cached.expires_at > now:
            if meta_out is not None:
                meta_out.update(
                    source="cache",
                    led_flight=False,
                    generated_in_seconds=cached.generated_in_seconds,
                    generated_by=cached.generated_by,
                )
            return copy.deepcopy(cached.result)

        flight = _scan_flights.get(key)
        if flight is None:
            _scan_flight_generation += 1
            flight = _ScanFlight(
                generation=_scan_flight_generation,
                started_at=now,
                deadline_at=now + lease_seconds,
                ttl_seconds=(
                    _SCAN_CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
                ),
                initiated_by=initiator,
            )
            _scan_flights[key] = flight
            started = True

    if started:

        def run_scan_factory() -> None:
            factory_started_at = _cache_now()
            try:
                produced = factory()
            except BaseException as exc:  # noqa: BLE001 - published to all waiters
                _finish_scan_flight(key, flight, error=exc)
            else:
                flight.generated_in_seconds = max(
                    0.0, _cache_now() - factory_started_at
                )
                _finish_scan_flight(key, flight, result=produced)

        try:
            _get_scan_executor().submit(run_scan_factory)
        except BaseException as exc:
            _finish_scan_flight(key, flight, error=exc)

    remaining = min(
        wait_timeout_seconds,
        max(0.0, flight.deadline_at - _cache_now()),
    )
    completed = flight.event.wait(timeout=remaining)
    if not completed:
        raise OpportunityScanTimeoutError(
            "opportunity scan is still running; retry shortly"
        )
    if flight.error is not None:
        raise flight.error
    if flight.result is None:
        raise RuntimeError("opportunity scan single-flight completed without a result")
    if meta_out is not None:
        meta_out.update(
            source="flight",
            led_flight=started,
            generated_in_seconds=flight.generated_in_seconds,
            generated_by=flight.initiated_by,
        )
    return copy.deepcopy(flight.result)


def _scan_timeout_response(exc: OpportunityScanTimeoutError) -> HTTPException:
    return HTTPException(
        status_code=504,
        detail={
            "error": "opportunity_scan_timeout",
            "message": "行情研究仍在执行或已超时，请稍后重试。",
            "retryable": True,
            "retry_after_seconds": 2,
        },
    )


def _configured_symbols() -> list[str]:
    from src.config import get_config

    configured = getattr(get_config(), "stock_list", None) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    return normalize_symbols([str(item) for item in configured])[:20]


def _configured_intraday_watchlist() -> list[str]:
    """Read the two-tier wide-lane watchlist; empty = feature off (现状不变)."""

    from src.config import get_config

    configured = getattr(get_config(), "intraday_watchlist", None) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    return normalize_symbols([str(item) for item in configured])


def _configured_intraday_pinned_tickers() -> list[str]:
    """INTRADAY_PINNED_TICKERS：两层模式下用户钉选（始终深扫）；未配置＝无钉选。

    与 INTRADAY_WATCHLIST 同一套 config 管道读取；仅两层模式消费——单层
    （现状）路径与显式 symbols 覆写完全不受影响。
    """

    from src.config import get_config

    configured = getattr(get_config(), "intraday_pinned_tickers", None) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    return normalize_symbols([str(item) for item in configured])


def _configured_deep_lane_max() -> int:
    """INTRADAY_DEEP_LANE_MAX，双重钳制 1..20（config 解析已钳，此处兜底）。"""

    from src.config import get_config

    raw = getattr(get_config(), "intraday_deep_lane_max", 12)
    if raw is None:
        return 12
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 12
    return max(1, min(20, value))


def _premarket_scheduler_enabled() -> bool:
    """Expose the configured host state without making it a page-load trigger."""

    from src.config import get_config

    return bool(
        getattr(get_config(), "premarket_research_scheduler_enabled", False)
    )


def _persisted_premarket_universe():
    from src.services.premarket_research_service import (
        resolve_premarket_research_universe,
    )

    return resolve_premarket_research_universe(
        include_fallback_suggestion=False,
    )


def _create_data_fetcher_manager():
    from data_provider.base import DataFetcherManager

    return DataFetcherManager()


def _load_daily_history(
    symbol: str,
    *,
    as_of: datetime,
    manager,
) -> DailyHistoryInput:
    """Load one symbol through a request-scoped shared DataFetcherManager."""

    try:
        from src.services.stock_service import StockService

        frame, source = manager.get_daily_data(symbol, days=80)
        rows = (
            [StockService._row_to_kline(row, intraday=False) for _, row in frame.iterrows()]
            if frame is not None and not frame.empty
            else []
        )
        return DailyHistoryInput(
            bars=tuple(rows),
            source=source,
            fetched_at=as_of,
            error=None if rows else "daily history returned no rows",
        )
    except Exception as exc:  # noqa: BLE001 - per-symbol graceful degradation
        logger.debug(
            "[opportunities] daily history unavailable symbol=%s error_type=%s",
            symbol,
            type(exc).__name__,
        )
        return DailyHistoryInput(
            bars=(),
            source=None,
            fetched_at=as_of,
            error=f"provider_error:{type(exc).__name__}",
        )


def _load_current_regime(market_date: date) -> Optional[dict[str, Any]]:
    """Read the exact market-date Regime row without creating or mutating it."""

    try:
        from src.regime.models import RegimeScore
        from src.regime.storage import _row_to_dict
        from src.storage import get_db

        db = get_db()
        session = db.get_session()
        try:
            row = session.execute(
                select(RegimeScore).where(RegimeScore.date == market_date).limit(1)
            ).scalar_one_or_none()
            return _row_to_dict(row) if row is not None else None
        finally:
            # A plain SELECT must not pass through the repository's committing
            # session_scope; close the read-only session without a commit.
            session.close()
    except Exception as exc:  # noqa: BLE001 - missing table/storage is an explicit unavailable state
        logger.warning("[opportunities] stored Regime unavailable for %s: %s", market_date, exc)
        return None


def _load_histories_gracefully(
    symbols: list[str],
    *,
    as_of: datetime,
    manager,
) -> dict[str, DailyHistoryInput]:
    histories: dict[str, DailyHistoryInput] = {
        symbol: DailyHistoryInput(
            bars=(),
            source=None,
            fetched_at=as_of,
            error="unsupported_non_us_options_universe",
        )
        for symbol in symbols
        if not is_supported_us_option_underlying(symbol)
    }
    supported = [symbol for symbol in symbols if is_supported_us_option_underlying(symbol)]
    if not supported:
        return histories

    def load(symbol: str) -> DailyHistoryInput:
        try:
            return _load_daily_history(symbol, as_of=as_of, manager=manager)
        except Exception as exc:  # noqa: BLE001 - injected loaders may also fail
            logger.debug(
                "[opportunities] injected daily loader failed symbol=%s error_type=%s",
                symbol,
                type(exc).__name__,
            )
            return DailyHistoryInput(
                bars=(),
                source=None,
                fetched_at=as_of,
                error=f"provider_error:{type(exc).__name__}",
            )

    worker_count = min(_MAX_FETCH_WORKERS, len(supported))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="opportunity-bars") as executor:
        futures = {executor.submit(load, symbol): symbol for symbol in supported}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except Exception as exc:  # pragma: no cover - load itself is fail-open
                logger.debug(
                    "[opportunities] worker failed symbol=%s error_type=%s",
                    symbol,
                    type(exc).__name__,
                )
                histories[symbol] = DailyHistoryInput(
                    bars=(),
                    source=None,
                    fetched_at=as_of,
                    error=f"provider_error:{type(exc).__name__}",
                )
    return histories


_AUTO_REGIME = object()


def _execute_daily_scan(
    symbols: list[str],
    limit: int,
    *,
    as_of: Optional[datetime] = None,
    regime: Any = _AUTO_REGIME,
) -> dict[str, Any]:
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    market_date = as_of.astimezone(_NEW_YORK).date()
    supported = any(is_supported_us_option_underlying(symbol) for symbol in symbols)
    manager = None
    try:
        if supported:
            manager = _create_data_fetcher_manager()
            histories = _load_histories_gracefully(symbols, as_of=as_of, manager=manager)
        else:
            histories = _load_histories_gracefully(symbols, as_of=as_of, manager=None)
    except Exception as exc:  # noqa: BLE001 - manager creation must not fail the batch
        logger.info(
            "[opportunities] daily data manager unavailable error_type=%s",
            type(exc).__name__,
        )
        histories = {
            symbol: DailyHistoryInput(
                bars=(),
                source=None,
                fetched_at=as_of,
                error=f"provider_error:{type(exc).__name__}",
            )
            for symbol in symbols
        }
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as exc:  # noqa: BLE001 - best-effort read-only cleanup
                logger.debug(
                    "[opportunities] data manager close failed error_type=%s",
                    type(exc).__name__,
                )

    selected_regime = (
        _load_current_regime(market_date) if regime is _AUTO_REGIME else regime
    )
    return build_daily_opportunity_run(
        symbols=symbols,
        histories=histories,
        regime=selected_regime,
        as_of=as_of,
        limit=limit,
    )


def _moomoo_opend_enabled() -> bool:
    return (os.environ.get("MOOMOO_OPEND_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _compute_atm_iv_moomoo(symbol: str) -> tuple[Optional[float], str]:
    """Call the existing Quote-only Moomoo option context adapter."""

    from data_provider.moomoo_options import compute_atm_iv_moomoo

    return compute_atm_iv_moomoo(symbol)


def _compute_option_overviews_moomoo(symbols: list[str]):
    """Batch-read provider option overview through a QuoteContext only."""

    from data_provider.moomoo_options import (
        fetch_option_underlying_overviews_moomoo,
    )

    return fetch_option_underlying_overviews_moomoo(symbols)


def _previous_xnys_session_label(market_date: date) -> Optional[str]:
    """Return the latest XNYS session strictly before ``market_date``."""

    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS")
        sessions = calendar.sessions_in_range(
            market_date - timedelta(days=10),
            market_date,
        )
        candidates = [
            value.date()
            for value in sessions
            if value.date() < market_date
        ]
        return candidates[-1].isoformat() if candidates else None
    except Exception as exc:  # noqa: BLE001 - label absence is explicit
        logger.debug("[opportunities] previous XNYS session unavailable: %s", exc)
        return None


def _ratio(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _difference(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None or right is None:
        return None
    value = float(left) - float(right)
    return round(value, 6) if math.isfinite(value) else None


def _empty_option_overview_item(
    *,
    ticker: str,
    state: str,
    fetched_at: datetime,
    market_date_et: str,
    open_interest_as_of: Optional[str],
    message: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "name": None,
        "state": state,
        "source": _OPTION_OVERVIEW_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "session_volume_date": market_date_et,
        "open_interest_as_of": open_interest_as_of,
        "volume_basis": "current_session_cumulative",
        "open_interest_basis": "prior_clearing_session",
        "volatility_basis": "provider_snapshot",
        "call_volume": None,
        "put_volume": None,
        "put_call_volume_ratio": None,
        "call_open_interest": None,
        "put_open_interest": None,
        "put_call_open_interest_ratio": None,
        "iv_percent": None,
        "iv_rank_percent": None,
        "iv_percentile_percent": None,
        "previous_iv_percent": None,
        "iv_change_points": None,
        "hv_30d_percent": None,
        "hv_30d_percentile": None,
        "hv_60d_percent": None,
        "hv_60d_percentile": None,
        "hv_90d_percent": None,
        "hv_90d_percentile": None,
        "hv_120d_percent": None,
        "hv_120d_percentile": None,
        "hv_365d_percent": None,
        "hv_365d_percentile": None,
        "iv_hv30_spread_points": None,
        "message": message,
        "limitations": list(_OPTION_OVERVIEW_LIMITATIONS),
    }


def _execute_option_overview(
    symbols: list[str], *, enabled: bool
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date = requested_at.astimezone(_NEW_YORK).date()
    market_date_et = market_date.isoformat()
    oi_as_of = _previous_xnys_session_label(market_date)
    if not enabled:
        items = [
            _empty_option_overview_item(
                ticker=symbol,
                state="not_configured",
                fetched_at=requested_at,
                market_date_et=market_date_et,
                open_interest_as_of=oi_as_of,
                message="MOOMOO_OPEND_ENABLED 未启用；未读取期权概览。",
            )
            for symbol in symbols
        ]
    else:
        try:
            overviews = _compute_option_overviews_moomoo(symbols)
        except Exception as exc:  # noqa: BLE001 - fail one batch closed
            logger.debug("[opportunities] option overview unavailable: %s", exc)
            overviews = {}
        items = []
        for symbol in symbols:
            item = overviews.get(symbol)
            if item is None:
                items.append(
                    _empty_option_overview_item(
                        ticker=symbol,
                        state="unavailable",
                        fetched_at=requested_at,
                        market_date_et=market_date_et,
                        open_interest_as_of=oi_as_of,
                        message=(
                            "Moomoo 未返回该标的的批量期权概览；"
                            "未以 ATM 单点或历史波动率冒充。"
                        ),
                    )
                )
                continue
            values = {
                field: getattr(item, field, None)
                for field in (
                    "call_volume",
                    "put_volume",
                    "call_open_interest",
                    "put_open_interest",
                    "iv_percent",
                    "iv_rank_percent",
                    "iv_percentile_percent",
                    "previous_iv_percent",
                    "hv_30d_percent",
                    "hv_30d_percentile",
                    "hv_60d_percent",
                    "hv_60d_percentile",
                    "hv_90d_percent",
                    "hv_90d_percentile",
                    "hv_120d_percent",
                    "hv_120d_percentile",
                    "hv_365d_percent",
                    "hv_365d_percentile",
                )
            }
            fetched_at = getattr(item, "fetched_at", requested_at)
            fetched_at_text = (
                fetched_at.isoformat()
                if isinstance(fetched_at, datetime)
                else requested_at.isoformat()
            )
            items.append(
                {
                    "ticker": symbol,
                    "name": getattr(item, "name", None),
                    "state": "ready",
                    "source": _OPTION_OVERVIEW_SOURCE,
                    "fetched_at": fetched_at_text,
                    "session_volume_date": market_date_et,
                    "open_interest_as_of": oi_as_of,
                    "volume_basis": "current_session_cumulative",
                    "open_interest_basis": "prior_clearing_session",
                    "volatility_basis": "provider_snapshot",
                    **values,
                    "put_call_volume_ratio": _ratio(
                        values["put_volume"], values["call_volume"]
                    ),
                    "put_call_open_interest_ratio": _ratio(
                        values["put_open_interest"],
                        values["call_open_interest"],
                    ),
                    "iv_change_points": _difference(
                        values["iv_percent"], values["previous_iv_percent"]
                    ),
                    "iv_hv30_spread_points": _difference(
                        values["iv_percent"], values["hv_30d_percent"]
                    ),
                    "message": (
                        "批量概览已读取：Volume 为当前交易时段累计，"
                        "OI 为上一清算日，IV/HV 为供应商统计快照。"
                    ),
                    "limitations": list(_OPTION_OVERVIEW_LIMITATIONS),
                }
            )
    return {
        "schema_version": "option-overview/1.0",
        "generated_at": requested_at.isoformat(),
        "market_date_et": market_date_et,
        "items": items,
    }


def _compute_option_wall_moomoo(
    symbol: str,
    *,
    dte_min: int,
    dte_max: int,
):
    """Read a normalized full-chain snapshot without broker trade actions."""

    from data_provider.moomoo_options import fetch_option_wall_snapshot_moomoo

    return fetch_option_wall_snapshot_moomoo(
        symbol,
        dte_min=dte_min,
        dte_max=dte_max,
    )


def _compute_option_events_moomoo(symbol: str, *, limit: int):
    """Read Moomoo-classified option events through the Quote-only adapter."""

    from data_provider.moomoo_options import fetch_option_events_moomoo

    return fetch_option_events_moomoo(symbol, limit=limit)


def _compute_near_expiry_chain_moomoo(symbol: str, *, max_dte: int):
    """Read near-the-money 0–max_dte 合约行 through the Quote-only adapter."""

    from data_provider.moomoo_options import fetch_near_expiry_chain_moomoo

    return fetch_near_expiry_chain_moomoo(symbol, max_dte=max_dte)


def _compute_expiry_availability_moomoo(symbol: str, *, max_dte: int):
    """Read today's 0–max_dte 到期日元数据 through the same Quote-only lane.

    与 ``_compute_near_expiry_chain_moomoo`` 共用临期合约链读取路径的第一步，
    但不发链窗口与快照批次（详见 data_provider 侧 docstring 与本模块
    ``_LANE_AVAILABILITY_*`` 常量的额度护栏说明）。lane 获取用短等待
    （``_LANE_AVAILABILITY_LANE_WAIT_SECONDS``）：本读取只发生在日内榜
    单飞 leader 内，lane 正忙时抛 ``MoomooWallLaneBusyError`` 交给调用方
    推迟到下一轮，绝不在 leader 租约内排队 30 秒。
    """

    from data_provider.moomoo_options import fetch_expiry_availability_moomoo

    return fetch_expiry_availability_moomoo(
        symbol,
        max_dte=max_dte,
        lane_wait_seconds=_LANE_AVAILABILITY_LANE_WAIT_SECONDS,
    )


def _option_context_item(
    ticker: str,
    *,
    enabled: bool,
    fetched_at: datetime,
) -> dict[str, Any]:
    limitations = list(_OPTION_CONTEXT_LIMITATIONS)
    fetched_at_text = fetched_at.isoformat()
    if not enabled:
        return {
            "ticker": ticker,
            "state": "not_configured",
            "source": _OPTION_CONTEXT_SOURCE,
            "fetched_at": fetched_at_text,
            "expiry": None,
            "atm_call_iv_percent": None,
            "message": (
                "MOOMOO_OPEND_ENABLED 未启用；未请求 Moomoo 最近到期 ATM Call IV。"
            ),
            "limitations": limitations,
        }

    expiry: Optional[str] = None
    try:
        iv_decimal, raw_expiry = _compute_atm_iv_moomoo(ticker)
        expiry = str(raw_expiry or "").strip() or None
        iv_value = float(iv_decimal) if iv_decimal is not None else None
        if (
            iv_value is not None
            and math.isfinite(iv_value)
            and iv_value > 0
            and expiry is not None
        ):
            return {
                "ticker": ticker,
                "state": "ready",
                "source": _OPTION_CONTEXT_SOURCE,
                "fetched_at": fetched_at_text,
                "expiry": expiry,
                "atm_call_iv_percent": round(iv_value * 100.0, 6),
                "message": (
                    f"已读取最近到期 {expiry} 最接近现价的 Call 单点 IV；"
                    "仅作研究上下文。"
                ),
                "limitations": limitations,
            }
    except Exception as exc:  # noqa: BLE001 - one symbol must not fail the batch
        logger.debug(
            "[opportunities] Moomoo ATM Call IV unavailable for %s: %s",
            ticker,
            exc,
        )

    return {
        "ticker": ticker,
        "state": "unavailable",
        "source": _OPTION_CONTEXT_SOURCE,
        "fetched_at": fetched_at_text,
        "expiry": expiry,
        "atm_call_iv_percent": None,
        "message": (
            "Moomoo OpenAPI 未返回有效的最近到期 ATM Call 单点 IV；"
            "未使用其他来源冒充或回填。"
        ),
        "limitations": limitations,
    }


def _execute_option_context(
    symbols: list[str], *, enabled: bool
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    items = [
        _get_or_compute_scan(
            _option_context_cache_key(symbol, enabled, market_date_et),
            lambda symbol=symbol: _option_context_item(
                symbol,
                enabled=enabled,
                fetched_at=datetime.now(timezone.utc),
            ),
        )
        for symbol in symbols
    ]
    generated_at = max(
        (str(item["fetched_at"]) for item in items),
        default=requested_at.isoformat(),
    )
    return {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "market_date_et": market_date_et,
        "items": items,
    }


def _empty_option_wall_payload(
    *,
    ticker: str,
    state: str,
    fetched_at: datetime,
    dte_min: int,
    dte_max: int,
    message: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "state": state,
        "source": _OPTION_WALL_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "quote_as_of": None,
        "formula_version": OPTION_WALL_FORMULA_VERSION,
        "spot": None,
        "atm_call_iv": {
            "state": (
                "not_configured"
                if state == "not_configured"
                else "unavailable"
            ),
            "expiry": None,
            "strike": None,
            "atm_call_iv_percent": None,
            "selection_method": OPTION_WALL_ATM_CALL_IV_METHOD,
        },
        "scope": {
            "dte_min": dte_min,
            "dte_max": dte_max,
            "expiries": [],
            "standard_contracts_only": True,
        },
        "coverage": {
            "requested_contracts": 0,
            "snapshot_received_contracts": 0,
            "valid_contracts": 0,
            "coverage_percent": 0.0,
            "failed_batches": 0,
            "excluded_nonstandard_contracts": 0,
            "excluded_unknown_standard_type_contracts": 0,
            "gamma_contracts": 0,
        },
        "walls": {
            "call_oi": [],
            "put_oi": [],
            "call_volume": [],
            "put_volume": [],
            "call_gamma_concentration": [],
            "put_gamma_concentration": [],
            "gross_gamma_concentration": [],
        },
        # additive (option-wall/1.3): a failed or disabled read reports the
        # ratios as explicitly undefined with the reason — never as 0 or 1.
        "totals": {
            "call_oi": 0.0,
            "put_oi": 0.0,
            "call_volume": 0.0,
            "put_volume": 0.0,
        },
        "ratios": {
            "call_put_oi_ratio": {
                "value": None,
                "numerator_total": 0.0,
                "denominator_total": 0.0,
                "numerator_side": "call",
                "denominator_side": "put",
                "metric_basis": OPTION_WALL_METRIC_BASIS_SETTLED_OI,
                "reason": OPTION_WALL_RATIO_REASON_NO_DATA,
            },
            "call_put_volume_ratio": {
                "value": None,
                "numerator_total": 0.0,
                "denominator_total": 0.0,
                "numerator_side": "call",
                "denominator_side": "put",
                "metric_basis": OPTION_WALL_METRIC_BASIS_SESSION_VOLUME,
                "reason": OPTION_WALL_RATIO_REASON_NO_DATA,
            },
            "caveat": OPTION_WALL_CALL_PUT_RATIO_CAVEAT,
        },
        "oi_weighted_center": {
            "strike": None,
            "total_open_interest": 0.0,
            "label": OPTION_WALL_OI_WEIGHTED_CENTER_LABEL,
            "method": OPTION_WALL_OI_WEIGHTED_CENTER_METHOD,
            "metric_basis": OPTION_WALL_METRIC_BASIS_SETTLED_OI,
            "validated_as_price_magnet": False,
            "reason": OPTION_WALL_RATIO_REASON_NO_DATA,
        },
        "message": message,
        "assumptions": list(_OPTION_WALL_ASSUMPTIONS),
        "limitations": list(_OPTION_WALL_LIMITATIONS),
    }


def _option_wall_item(
    ticker: str,
    *,
    enabled: bool,
    fetched_at: datetime,
    dte_min: int,
    dte_max: int,
) -> dict[str, Any]:
    if not enabled:
        return _empty_option_wall_payload(
            ticker=ticker,
            state="not_configured",
            fetched_at=fetched_at,
            dte_min=dte_min,
            dte_max=dte_max,
            message="MOOMOO_OPEND_ENABLED 未启用；未读取期权链墙位。",
        )

    try:
        snapshot = _compute_option_wall_moomoo(
            ticker,
            dte_min=dte_min,
            dte_max=dte_max,
        )
        if snapshot is None:
            raise ValueError("Moomoo did not return a usable option-chain snapshot")
        payload = build_option_wall_payload(
            snapshot,
            dte_min=dte_min,
            dte_max=dte_max,
        )
        coverage = payload["coverage"]
        walls = payload["walls"]
        has_observed_walls = bool(walls["call_oi"] or walls["put_oi"])
        if not has_observed_walls:
            raise ValueError("snapshot contained no positive open-interest wall")

        complete = (
            coverage["requested_contracts"] > 0
            and coverage["coverage_percent"] == 100.0
            and coverage["failed_batches"] == 0
            and coverage["gamma_contracts"] == coverage["valid_contracts"]
        )
        state = "ready" if complete else "partial"
        snapshot_fetched_at = getattr(snapshot, "fetched_at", fetched_at)
        if isinstance(snapshot_fetched_at, datetime):
            fetched_at_text = snapshot_fetched_at.isoformat()
        else:
            fetched_at_text = str(snapshot_fetched_at or fetched_at.isoformat())
        message = (
            f"聚合 {dte_min}–{dte_max} DTE 标准合约；"
            f"有效快照 {coverage['valid_contracts']}/{coverage['requested_contracts']}。"
        )
        if state == "partial":
            message += " OI/Volume 墙仍可观察，Gamma 或快照覆盖不完整。"
        return {
            "ticker": ticker,
            "state": state,
            "source": _OPTION_WALL_SOURCE,
            "fetched_at": fetched_at_text,
            **payload,
            "message": message,
            "assumptions": list(_OPTION_WALL_ASSUMPTIONS),
            "limitations": list(_OPTION_WALL_LIMITATIONS),
        }
    except Exception as exc:  # noqa: BLE001 - one symbol must not fail the batch
        logger.debug("[opportunities] option walls unavailable for %s: %s", ticker, exc)
        return _empty_option_wall_payload(
            ticker=ticker,
            state="unavailable",
            fetched_at=fetched_at,
            dte_min=dte_min,
            dte_max=dte_max,
            message=(
                "Moomoo OpenAPI 未返回可用的完整链墙位数据；"
                "未以默认值、旧数据或第三方估算回填。"
            ),
        )


def _execute_option_walls(
    symbols: list[str],
    *,
    enabled: bool,
    dte_min: int,
    dte_max: int,
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    started_at = time.monotonic()

    def load_one(symbol: str) -> dict[str, Any]:
        return _get_or_compute_scan(
            _option_wall_cache_key(
                symbol,
                enabled,
                market_date_et,
                dte_min,
                dte_max,
            ),
            lambda symbol=symbol: _option_wall_item(
                symbol,
                enabled=enabled,
                fetched_at=datetime.now(timezone.utc),
                dte_min=dte_min,
                dte_max=dte_max,
            ),
        )

    if enabled and len(symbols) > 1:
        # One full 0–45 DTE scan usually needs several 400-contract provider
        # batches.  Dedicated QuoteContext lanes make independent underlyings
        # overlap; stable index placement preserves the request order.
        items: list[Optional[dict[str, Any]]] = [None] * len(symbols)
        with ThreadPoolExecutor(
            max_workers=min(_MAX_OPTION_WALL_WORKERS, len(symbols)),
            thread_name_prefix="option-wall",
        ) as executor:
            futures = {
                executor.submit(load_one, symbol): index
                for index, symbol in enumerate(symbols)
            }
            for future in as_completed(futures):
                items[futures[future]] = future.result()
        ordered_items = [item for item in items if item is not None]
    else:
        ordered_items = [load_one(symbol) for symbol in symbols]

    logger.debug(
        "[opportunities] option-wall batch completed symbols=%s duration_ms=%s",
        len(symbols),
        round((time.monotonic() - started_at) * 1000),
    )
    generated_at = max(
        (str(item["fetched_at"]) for item in ordered_items),
        default=requested_at.isoformat(),
    )
    return {
        "schema_version": "option-wall/1.3",
        "generated_at": generated_at,
        "market_date_et": market_date_et,
        "items": ordered_items,
    }


def _empty_option_event_payload(
    *,
    ticker: str,
    state: str,
    fetched_at: datetime,
    message: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "state": state,
        "source": _OPTION_EVENT_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "event_as_of": None,
        "all_count": None,
        "events": [],
        "message": message,
        "limitations": list(_OPTION_EVENT_LIMITATIONS),
    }


def _serialise_option_event(event) -> dict[str, Any]:
    if isinstance(event, dict):
        return {field_name: event.get(field_name) for field_name in _OPTION_EVENT_FIELDS}
    return {
        field_name: getattr(event, field_name, None)
        for field_name in _OPTION_EVENT_FIELDS
    }


def _option_event_item(
    ticker: str,
    *,
    enabled: bool,
    fetched_at: datetime,
    limit_per_symbol: int,
) -> dict[str, Any]:
    if not enabled:
        return _empty_option_event_payload(
            ticker=ticker,
            state="not_configured",
            fetched_at=fetched_at,
            message=(
                "MOOMOO_OPEND_ENABLED 未启用；未读取 Moomoo 异常期权成交。"
            ),
        )

    try:
        snapshot = _compute_option_events_moomoo(
            ticker,
            limit=limit_per_symbol,
        )
        if snapshot is None:
            raise ValueError("Moomoo did not return an option-event snapshot")
        raw_events = tuple(getattr(snapshot, "events", ()) or ())
        events = [_serialise_option_event(event) for event in raw_events]
        snapshot_fetched_at = getattr(snapshot, "fetched_at", fetched_at)
        fetched_at_text = (
            snapshot_fetched_at.isoformat()
            if isinstance(snapshot_fetched_at, datetime)
            else str(snapshot_fetched_at or fetched_at.isoformat())
        )
        all_count = getattr(snapshot, "all_count", None)
        state = "ready" if events else "empty"
        if state == "ready":
            message = (
                f"返回 {len(events)} 条最近异动成交；Moomoo 当前筛选总数 "
                f"{all_count if all_count is not None else '未知'}。"
            )
        else:
            message = "Moomoo 查询成功，但该标的当前未返回异动成交。"
        return {
            "ticker": ticker,
            "state": state,
            "source": _OPTION_EVENT_SOURCE,
            "fetched_at": fetched_at_text,
            "event_as_of": getattr(snapshot, "event_as_of", None),
            "all_count": all_count,
            "events": events,
            "message": message,
            "limitations": list(_OPTION_EVENT_LIMITATIONS),
        }
    except Exception as exc:  # noqa: BLE001 - one symbol must not fail the batch
        logger.debug(
            "[opportunities] unusual option events unavailable for %s: %s",
            ticker,
            exc,
        )
        return _empty_option_event_payload(
            ticker=ticker,
            state="unavailable",
            fetched_at=fetched_at,
            message=(
                "Moomoo OpenAPI 未返回可用的异常期权成交；"
                "未以旧数据、默认值或第三方估算回填。"
            ),
        )


def _execute_option_events(
    symbols: list[str],
    *,
    enabled: bool,
    limit_per_symbol: int,
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    items = [
        _get_or_compute_scan(
            _option_event_cache_key(
                symbol,
                enabled,
                market_date_et,
                limit_per_symbol,
            ),
            lambda symbol=symbol: _option_event_item(
                symbol,
                enabled=enabled,
                fetched_at=datetime.now(timezone.utc),
                limit_per_symbol=limit_per_symbol,
            ),
        )
        for symbol in symbols
    ]
    generated_at = max(
        (str(item["fetched_at"]) for item in items),
        default=requested_at.isoformat(),
    )
    return {
        "schema_version": "option-event/1.0",
        "generated_at": generated_at,
        "market_date_et": market_date_et,
        "items": items,
    }


def _empty_near_expiry_payload(
    *,
    ticker: str,
    state: str,
    fetched_at: datetime,
    max_dte: int,
    open_interest_as_of: Optional[str],
    message: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "state": state,
        "source": _NEAR_EXPIRY_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "formula_version": NEAR_EXPIRY_FORMULA_VERSION,
        "max_dte": int(max_dte),
        "spot": None,
        "spot_as_of": None,
        "open_interest_as_of": open_interest_as_of,
        "open_interest_basis": "prior_clearing_session",
        "strike_window": {
            "percent_band": NEAR_MONEY_PERCENT_BAND,
            "min_strikes_per_side": NEAR_MONEY_MIN_STRIKES_PER_SIDE,
            "basis": NEAR_EXPIRY_STRIKE_WINDOW_BASIS,
        },
        "coverage": {
            "requested_contracts": 0,
            "snapshot_received_contracts": 0,
            "observed_contracts": 0,
            "missing_contracts": 0,
            "failed_batches": 0,
            "excluded_nonstandard_contracts": 0,
            "excluded_unknown_standard_type_contracts": 0,
        },
        "expiries": [],
        # 车道可用性（V2-E，additive）：链读不到时 has_zero_dte 显式 None，
        # 绝不以「读不到」冒充「今天没有 0DTE」。
        "has_zero_dte": None,
        "available_dte_list": [],
        "availability_unavailable_reason": "near_expiry_chain_unavailable",
        "message": message,
        "limitations": list(_NEAR_EXPIRY_LIMITATIONS),
    }


def _near_expiry_item(
    ticker: str,
    *,
    enabled: bool,
    fetched_at: datetime,
    max_dte: int,
    open_interest_as_of: Optional[str],
) -> dict[str, Any]:
    if not enabled:
        return _empty_near_expiry_payload(
            ticker=ticker,
            state="not_configured",
            fetched_at=fetched_at,
            max_dte=max_dte,
            open_interest_as_of=open_interest_as_of,
            message="MOOMOO_OPEND_ENABLED 未启用；未读取临期合约链。",
        )

    try:
        snapshot = _compute_near_expiry_chain_moomoo(ticker, max_dte=max_dte)
        if snapshot is None:
            raise ValueError(
                "Moomoo did not return a usable near-expiry chain snapshot"
            )
        payload = build_near_expiry_contract_payload(snapshot, max_dte=max_dte)
        snapshot_fetched_at = getattr(snapshot, "fetched_at", fetched_at)
        fetched_at_text = (
            snapshot_fetched_at.isoformat()
            if isinstance(snapshot_fetched_at, datetime)
            else str(snapshot_fetched_at or fetched_at.isoformat())
        )
        state = payload["state"]
        if state == "empty":
            message = (
                f"{max_dte} 天内没有该标的的期权到期日；这是诚实空态，"
                "不是数据失败。"
            )
        else:
            coverage = payload["coverage"]
            message = (
                f"临期合约读数已读取：{len(payload['expiries'])} 个到期日、"
                f"观测报价 {coverage['observed_contracts']}/"
                f"{coverage['requested_contracts']}。仅为合约选择参考，"
                "不构成推荐。"
            )
            if state == "partial":
                message += " 部分合约快照缺失，对应行显式标缺。"
            elif state == "unavailable":
                message = (
                    "临期到期日存在，但本次动态快照全部缺失；"
                    "各行显式标缺，未以 0 或旧值回填。"
                )
        # 车道可用性（V2-E，additive）：链已读到即可如实回答「今天这个标的
        # 有没有 0DTE」，零额外抓取——直接由已在手的到期日分组推导。
        # state="empty" 是诚实空态（链可读、窗口内没有到期日）→ False；
        # 只有链本身读不到才是 None（见 _empty_near_expiry_payload）。
        availability = build_ticker_availability(
            ticker,
            payload["expiries"],
            max_dte=max_dte,
        )
        return {
            "ticker": ticker,
            "source": _NEAR_EXPIRY_SOURCE,
            "fetched_at": fetched_at_text,
            "open_interest_as_of": open_interest_as_of,
            "open_interest_basis": "prior_clearing_session",
            **payload,
            "has_zero_dte": availability["has_zero_dte"],
            "available_dte_list": availability["available_dte_list"],
            "availability_unavailable_reason": None,
            "message": message,
            "limitations": list(_NEAR_EXPIRY_LIMITATIONS),
        }
    except Exception as exc:  # noqa: BLE001 - single-symbol read fails closed
        logger.debug(
            "[opportunities] near-expiry contracts unavailable for %s: %s",
            ticker,
            exc,
        )
        return _empty_near_expiry_payload(
            ticker=ticker,
            state="unavailable",
            fetched_at=fetched_at,
            max_dte=max_dte,
            open_interest_as_of=open_interest_as_of,
            message=(
                "Moomoo OpenAPI 未返回可用的临期合约链；"
                "未以默认值、旧数据或第三方估算回填。"
            ),
        )


def _execute_near_expiry_contracts(
    symbol: str,
    *,
    enabled: bool,
    max_dte: int,
) -> dict[str, Any]:
    requested_at = datetime.now(timezone.utc)
    market_date = requested_at.astimezone(_NEW_YORK).date()
    market_date_et = market_date.isoformat()
    item = _near_expiry_item(
        symbol,
        enabled=enabled,
        fetched_at=requested_at,
        max_dte=max_dte,
        open_interest_as_of=_previous_xnys_session_label(market_date),
    )
    # v3 财报临近：复用扫描表同一份逐 ET 日日历缓存（零新增抓取路径），
    # 让「财报临近不交易（期权贵）」在看合约的瞬间可见。与面板自身
    # state 正交：Moomoo 未启用/失败时该字段照常返回；日历不可得时显式
    # unavailable，绝不以缺失冒充「安全」。
    item["earnings_proximity"] = compute_earnings_proximity(
        item["ticker"],
        _load_intraday_earnings_calendar(market_date_et),
        market_date_et=market_date_et,
    )
    return {
        "schema_version": _NEAR_EXPIRY_SCHEMA,
        "generated_at": str(item["fetched_at"]),
        "market_date_et": market_date.isoformat(),
        "item": item,
    }


def _fetch_underlying_session_quotes(symbols: list[str]):
    """Read live session quotes through the Quote-only Moomoo adapter."""

    from data_provider.moomoo_options import (
        fetch_underlying_session_quotes_moomoo,
    )

    return fetch_underlying_session_quotes_moomoo(symbols)


def _intraday_now() -> datetime:
    """Wall clock for the intraday panel; isolated so tests can pin it."""

    return datetime.now(timezone.utc)


def _prune_intraday_daily_cache(now: float) -> None:
    expired = [
        key
        for key, entry in _intraday_daily_cache.items()
        if entry.expires_at <= now
    ]
    for key in expired:
        _intraday_daily_cache.pop(key, None)
    while len(_intraday_daily_cache) >= _INTRADAY_DAILY_CACHE_MAX_ENTRIES:
        oldest = min(
            _intraday_daily_cache,
            key=lambda item: _intraday_daily_cache[item].expires_at,
        )
        _intraday_daily_cache.pop(oldest, None)


def _intraday_daily_inputs(
    symbols: list[str],
    *,
    as_of: datetime,
    market_date_et: str,
) -> dict[str, dict[str, Any]]:
    """Per-symbol ATR14 + 20-session median volume from completed daily bars.

    Uses the same shared daily-history loader as the daily board.  Complete
    derivations are memoised per (symbol, ET market date): completed bars only
    change once per session, so a 60s polling panel must not re-run the daily
    providers on every tick.  Failed or short derivations are never memoised —
    they retry on the next (30s-cached) request instead of freezing a failure.
    """

    now = _cache_now()
    results: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    with _scan_cache_lock:
        _prune_intraday_daily_cache(now)
        for symbol in symbols:
            entry = _intraday_daily_cache.get((symbol, market_date_et))
            if entry is not None and entry.expires_at > now:
                results[symbol] = copy.deepcopy(entry.result)
            else:
                missing.append(symbol)
    if not missing:
        return results

    manager = None
    try:
        manager = _create_data_fetcher_manager()
    except Exception as exc:  # noqa: BLE001 - loader machinery must not 500 the panel
        logger.info(
            "[opportunities] intraday daily manager unavailable error_type=%s",
            type(exc).__name__,
        )
    try:
        histories = _load_histories_gracefully(missing, as_of=as_of, manager=manager)
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                logger.debug(
                    "[opportunities] intraday manager close failed error_type=%s",
                    type(exc).__name__,
                )

    completion_time = _cache_now()
    for symbol in missing:
        history = histories.get(
            symbol, DailyHistoryInput(bars=(), error="loader result missing")
        )
        bars = completed_daily_bars(history.bars, as_of=as_of)
        atr = compute_atr14(bars)
        median, median_reason = compute_prior_full_day_median_volume(bars)
        atr_reason = atr.unavailable_reason
        if not bars and history.error:
            atr_reason = f"daily_history_unavailable:{history.error}"
            median_reason = f"daily_history_unavailable:{history.error}"
        # Additive structural context (prior close / 20d range / EMA) shares
        # this memo so the intraday Top board never re-runs daily providers
        # beyond what the tracking panel already triggers.
        structure = compute_intraday_daily_context(bars)
        payload = {
            "atr14": atr.value,
            "atr14_bar_count": atr.bar_count,
            "atr14_last_bar_date": atr.last_bar_date,
            "atr14_unavailable_reason": atr_reason,
            "source": history.source,
            "prior_20d_median_volume": median,
            "median_unavailable_reason": median_reason,
            **structure,
        }
        results[symbol] = payload
        if atr.value is not None and median is not None:
            with _scan_cache_lock:
                _prune_intraday_daily_cache(completion_time)
                _intraday_daily_cache[(symbol, market_date_et)] = _ScanCacheEntry(
                    expires_at=completion_time + _INTRADAY_DAILY_CACHE_TTL_SECONDS,
                    result=copy.deepcopy(payload),
                )
    return results


def _intraday_tracking_item(
    ticker: str,
    *,
    enabled: bool,
    quote: Any,
    daily: dict[str, Any],
    fetched_at: datetime,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "ticker": ticker,
        "source": _INTRADAY_TRACKING_SOURCE,
        "fetched_at": fetched_at.isoformat(),
        "quote_as_of": None,
        "last_price": None,
        "session_open": None,
        "session_high": None,
        "session_low": None,
        "prev_close": None,
        "session_volume": None,
        "session_turnover": None,
        "vwap": None,
        "vwap_basis": VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
        "vwap_unavailable_reason": None,
        "atr14": daily.get("atr14"),
        "atr14_method": ATR14_METHOD,
        "atr14_bar_count": int(daily.get("atr14_bar_count") or 0),
        "atr14_last_bar_date": daily.get("atr14_last_bar_date"),
        "atr14_source": daily.get("source"),
        "atr14_unavailable_reason": daily.get("atr14_unavailable_reason"),
        "volume_pace_ratio": None,
        "volume_pace_basis": VOLUME_PACE_BASIS,
        "prior_20d_median_volume": daily.get("prior_20d_median_volume"),
        "volume_pace_unavailable_reason": None,
        "limitations": list(_INTRADAY_TRACKING_LIMITATIONS),
    }
    if not enabled:
        item.update(
            state="not_configured",
            vwap_unavailable_reason="moomoo_not_configured",
            volume_pace_unavailable_reason="moomoo_not_configured",
            message=(
                "MOOMOO_OPEND_ENABLED 未启用；未读取实时快照，"
                "仅保留已完成日线派生的 ATR14 与量能基准。"
            ),
        )
        return item
    if quote is None:
        item.update(
            state="unavailable",
            vwap_unavailable_reason="quote_unavailable",
            volume_pace_unavailable_reason="quote_unavailable",
            message="Moomoo 未返回该标的的实时快照；未以 0 或旧值冒充实时行情。",
        )
        return item

    quote_fetched_at = getattr(quote, "fetched_at", None)
    vwap = compute_session_vwap(
        getattr(quote, "turnover", None),
        getattr(quote, "volume", None),
    )
    pace = compute_volume_pace(
        getattr(quote, "volume", None),
        daily.get("prior_20d_median_volume"),
        median_unavailable_reason=daily.get("median_unavailable_reason"),
    )
    item.update(
        fetched_at=(
            quote_fetched_at.isoformat()
            if isinstance(quote_fetched_at, datetime)
            else fetched_at.isoformat()
        ),
        quote_as_of=getattr(quote, "update_time", None),
        last_price=getattr(quote, "last_price", None),
        session_open=getattr(quote, "open_price", None),
        session_high=getattr(quote, "high_price", None),
        session_low=getattr(quote, "low_price", None),
        prev_close=getattr(quote, "prev_close_price", None),
        session_volume=getattr(quote, "volume", None),
        session_turnover=getattr(quote, "turnover", None),
        vwap=vwap.value,
        vwap_unavailable_reason=vwap.unavailable_reason,
        volume_pace_ratio=pace.ratio,
        volume_pace_unavailable_reason=pace.unavailable_reason,
    )
    if pace.prior_median_volume is not None:
        item["prior_20d_median_volume"] = pace.prior_median_volume
    if item["last_price"] is None:
        item.update(
            state="unavailable",
            message="Moomoo 快照缺少有效现价；该行不可用于对照冻结计划。",
        )
        return item
    complete = (
        vwap.value is not None
        and pace.ratio is not None
        and item["atr14"] is not None
    )
    item["state"] = "ready" if complete else "partial"
    item["message"] = (
        "实时行情、VWAP 近似、量能节奏与 ATR14 已就绪；仅对照冻结盘前计划。"
        if complete
        else "现价可用，但部分指标标缺；缺失字段附带明确 reason，未估算回填。"
    )
    return item


def _execute_intraday_tracking(
    symbols: list[str], *, enabled: bool
) -> dict[str, Any]:
    requested_at = _intraday_now()
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    session_state = market_session_state(requested_at)
    daily_inputs = _intraday_daily_inputs(
        symbols,
        as_of=requested_at,
        market_date_et=market_date_et,
    )
    quotes: dict[str, Any] = {}
    if enabled:
        try:
            quotes = _fetch_underlying_session_quotes(symbols) or {}
        except Exception as exc:  # noqa: BLE001 - fail the batch closed per symbol
            logger.debug(
                "[opportunities] intraday session quotes unavailable: %s", exc
            )
            quotes = {}
    items = [
        _intraday_tracking_item(
            symbol,
            enabled=enabled,
            quote=quotes.get(symbol),
            daily=daily_inputs.get(symbol) or {},
            fetched_at=requested_at,
        )
        for symbol in symbols
    ]
    return {
        "schema_version": _INTRADAY_TRACKING_SCHEMA,
        "generated_at": requested_at.isoformat(),
        "market_date_et": market_date_et,
        "session_state": session_state,
        "session_state_basis": SESSION_STATE_BASIS,
        "tracking_basis": "frozen_premarket_plan_readonly",
        "items": items,
        "limitations": list(_INTRADAY_TRACKING_LIMITATIONS),
    }


def _quote_to_intraday_input(quote: Any) -> IntradayQuoteInput:
    return IntradayQuoteInput(
        last_price=getattr(quote, "last_price", None),
        session_open=getattr(quote, "open_price", None),
        session_high=getattr(quote, "high_price", None),
        session_low=getattr(quote, "low_price", None),
        prev_close=getattr(quote, "prev_close_price", None),
        session_volume=getattr(quote, "volume", None),
        session_turnover=getattr(quote, "turnover", None),
        quote_as_of=getattr(quote, "update_time", None),
        fetched_at=getattr(quote, "fetched_at", None),
        source=_INTRADAY_TRACKING_SOURCE,
    )


def _load_intraday_option_event_items(
    symbols: list[str],
    *,
    enabled: bool,
    market_date_et: str,
) -> dict[str, dict[str, Any]]:
    """Per-symbol bounded option-event pages with strict failure isolation.

    Reuses the option-events endpoint's per-symbol cache key (30s TTL +
    single-flight), so the intraday board and the detail panel share quota
    work.  Any per-symbol failure — including a scan-timeout from the shared
    single-flight — degrades that one symbol to an explicit ``unavailable``
    payload instead of failing the batch.
    """

    def load_one(symbol: str) -> dict[str, Any]:
        try:
            return _get_or_compute_scan(
                _option_event_cache_key(
                    symbol,
                    enabled,
                    market_date_et,
                    _INTRADAY_TOP_EVENT_PAGE_SIZE,
                ),
                lambda: _option_event_item(
                    symbol,
                    enabled=enabled,
                    fetched_at=datetime.now(timezone.utc),
                    limit_per_symbol=_INTRADAY_TOP_EVENT_PAGE_SIZE,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - one symbol must not fail the run
            logger.debug(
                "[opportunities] intraday option events unavailable symbol=%s error_type=%s",
                symbol,
                type(exc).__name__,
            )
            return _empty_option_event_payload(
                ticker=symbol,
                state="unavailable",
                fetched_at=datetime.now(timezone.utc),
                message=(
                    "Moomoo 异动读取失败或超时；该标的按无异动数据处理，"
                    "不以旧数据或默认值回填。"
                ),
            )

    if not symbols:
        return {}
    if enabled and len(symbols) > 1:
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(
            max_workers=min(_INTRADAY_TOP_MAX_EVENT_WORKERS, len(symbols)),
            thread_name_prefix="intraday-top-events",
        ) as executor:
            futures = {
                executor.submit(load_one, symbol): symbol for symbol in symbols
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return results
    return {symbol: load_one(symbol) for symbol in symbols}


def _fetch_intraday_5m_bars(symbol: str) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Server-side 5m bars via the same loader as /stocks/{code}/history.

    Isolated so tests can stub it; never called when the symbol is cached.

    v8：这里是盘中 5m K 线进入研究管线（波段爆发 / 近 30 分钟位移 / v4 形态
    对比）的**唯一入口**，因此也是把不同数据源的 K 线时间戳口径统一到「开始
    时间」的唯一位置。Moomoo ``time_key`` 按 K 线**结束**时间打标（2026-08-04
    实测确认，见 intraday_bursts 的口径说明），不归一就会漏掉收盘集合竞价那根、
    反而混进一根盘前 K 线；yfinance 等本来就按开始时间打标的源逐字不动。
    """

    from src.services.stock_service import StockService
    from src.opportunities.intraday_bursts import normalize_bar_label_convention

    result = StockService().get_history_data(
        symbol,
        period="5m",
        days=_INTRADAY_BURST_FETCH_DAYS,
        include_stock_name=False,
    )
    source = result.get("source")
    bars = normalize_bar_label_convention(
        list(result.get("data") or []), source=source
    )
    return bars, source


def _prune_intraday_burst_cache(now: float) -> None:
    expired = [
        key
        for key, entry in _intraday_burst_cache.items()
        if entry.expires_at <= now
    ]
    for key in expired:
        _intraday_burst_cache.pop(key, None)
    while len(_intraday_burst_cache) >= _INTRADAY_BURST_CACHE_MAX_ENTRIES:
        oldest = min(
            _intraday_burst_cache,
            key=lambda item: _intraday_burst_cache[item].expires_at,
        )
        _intraday_burst_cache.pop(oldest, None)


def _load_intraday_burst_profiles(
    symbols: list[str],
    *,
    market_date_et: str,
    quote_session_scope: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Per-symbol rolling-burst profiles from bounded 5m history reads.

    每标的只取当前 + 上一交易时段的常规时段 5m K 线（跨周末多取的自然日在
    纯函数内裁掉）；逐标的 60 秒 TTL 缓存 + 有界线程池并发。任何单标的
    读取失败都只让该标的的波段爆发显式 unavailable，绝不阻塞聚合证据，
    也绝不让整个 Top 榜 500。

    返回 ``(burst_profiles, raw_bars_by_symbol)``：同一批原始 5m K 线随
    profile 一起缓存并返回，供 v4 styleMatch 形态检测复用（零新增请求）。
    """

    now = _cache_now()
    results: dict[str, dict[str, Any]] = {}
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    with _scan_cache_lock:
        _prune_intraday_burst_cache(now)
        for symbol in symbols:
            entry = _intraday_burst_cache.get(
                (symbol, market_date_et, quote_session_scope)
            )
            if entry is not None and entry.expires_at > now:
                cached = copy.deepcopy(entry.result)
                results[symbol] = cached.get("profile") or {}
                bars_by_symbol[symbol] = cached.get("bars") or []
            else:
                missing.append(symbol)
    if not missing:
        return results, bars_by_symbol

    def load_one(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            bars, source = _fetch_intraday_5m_bars(symbol)
        except Exception as exc:  # noqa: BLE001 - one symbol must not fail the run
            logger.debug(
                "[opportunities] intraday 5m bars unavailable symbol=%s error_type=%s",
                symbol,
                type(exc).__name__,
            )
            return (
                unavailable_burst_profile(
                    f"history_5m_unavailable:{type(exc).__name__}",
                    fetched_at=fetched_at,
                ),
                [],
            )
        profile = compute_session_burst_profile(
            bars,
            market_date_et=market_date_et,
            quote_session_scope=quote_session_scope,
            source=source,
            fetched_at=fetched_at,
        )
        return profile, list(bars)

    loaded: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    if len(missing) > 1:
        with ThreadPoolExecutor(
            max_workers=min(_INTRADAY_BURST_MAX_WORKERS, len(missing)),
            thread_name_prefix="intraday-top-bursts",
        ) as executor:
            futures = {
                executor.submit(load_one, symbol): symbol for symbol in missing
            }
            for future in as_completed(futures):
                loaded[futures[future]] = future.result()
    else:
        loaded = {symbol: load_one(symbol) for symbol in missing}

    completion_time = _cache_now()
    for symbol, (profile, raw_bars) in loaded.items():
        results[symbol] = profile
        bars_by_symbol[symbol] = raw_bars
        # 失败/无 K 线的 profile 不落缓存：下一次（60 秒 TTL 内的）请求
        # 直接重试，而不是把失败冻结一个轮询周期。
        if profile.get("state") == "ready":
            with _scan_cache_lock:
                _prune_intraday_burst_cache(completion_time)
                _intraday_burst_cache[
                    (symbol, market_date_et, quote_session_scope)
                ] = _ScanCacheEntry(
                    expires_at=completion_time + _INTRADAY_BURST_CACHE_TTL_SECONDS,
                    result=copy.deepcopy(
                        {"profile": profile, "bars": raw_bars}
                    ),
                )
    return results, bars_by_symbol


# v4 styleMatch：S1/S2/S3 的 Playbook 只读对应关系（本地 SQLite 读取），
# 5 分钟 TTL；读取失败只让形态徽标缺少 Playbook 标注，不影响形态检测本身。
_INTRADAY_PLAYBOOK_CACHE_TTL_SECONDS = 300.0
_SETUP_TITLE_PATTERN = re.compile(r"^(S[123])\b")


def _load_intraday_playbook_refs() -> dict[str, dict[str, Any]]:
    """Read-only S1/S2/S3 playbook linkage for the intraday style match.

    只读取 journal_v2 Playbook 候选表（newest first，取每个 setup key 最新
    一条；``promoted`` 标记来自晋升规则的存在性）。绝不写入、绝不晋升，
    Playbook 规则也不反哺任何评分或排序——这里只是给形态徽标补一行
    「对应 Playbook: S1（候选/已晋升）」的展示信息。
    """

    now = _cache_now()
    with _scan_cache_lock:
        entry = _intraday_playbook_cache.get("default")
        if entry is not None and entry.expires_at > now:
            return copy.deepcopy(entry.result)

    refs: dict[str, dict[str, Any]] = {}
    try:
        from src.journal.ledger.playbook_repository import (
            list_playbook_candidates,
        )

        for candidate in list_playbook_candidates():
            match = _SETUP_TITLE_PATTERN.match(str(candidate.title or "").strip())
            if match is None:
                continue
            key = match.group(1)
            if key in refs:
                continue  # newest first：同 key 只保留最新候选。
            refs[key] = {
                "setup_key": key,
                "candidate_key": candidate.candidate_key,
                "status": "promoted" if candidate.promoted else "candidate",
                "title": candidate.title,
            }
    except Exception as exc:  # noqa: BLE001 - playbook lane must not 500 the board
        logger.debug(
            "[opportunities] intraday playbook refs unavailable error_type=%s",
            type(exc).__name__,
        )
        return {}

    completion_time = _cache_now()
    with _scan_cache_lock:
        _intraday_playbook_cache["default"] = _ScanCacheEntry(
            expires_at=completion_time + _INTRADAY_PLAYBOOK_CACHE_TTL_SECONDS,
            result=copy.deepcopy(refs),
        )
    return refs


def _fetch_earnings_calendar_rows(
    from_date: date, to_date: date
) -> tuple[Optional[list[dict[str, Any]]], Optional[str]]:
    """One bounded Finnhub earnings-calendar range read, isolated for tests.

    Returns ``(rows, unavailable_reason)``.  ``rows=None`` means the calendar
    could not be observed（未配置或请求失败）——绝不以空列表冒充「无财报」。
    """

    from data_provider.finnhub_fetcher import FinnhubFetcher

    fetcher = FinnhubFetcher()
    if not fetcher.configured:
        return None, "finnhub_not_configured"
    rows = fetcher.get_earnings_calendar(from_date, to_date)
    if fetcher.request_succeeded("earnings_calendar") is not True:
        return None, "finnhub_request_failed"
    return list(rows), None


def _load_intraday_earnings_calendar(market_date_et: str) -> dict[str, Any]:
    """Shared per-ET-date earnings snapshot with a bounded range call.

    一次区间调用覆盖整个 universe（当日 → +EARNINGS_WINDOW_DAYS 天），按 ET
    日期缓存：成功 1 小时、失败 10 分钟。任何异常都只让财报上下文显式
    unavailable，绝不拖垮日内 Top 榜。
    """

    now = _cache_now()
    with _scan_cache_lock:
        entry = _intraday_earnings_cache.get(market_date_et)
        if entry is not None and entry.expires_at > now:
            return copy.deepcopy(entry.result)

    fetched_at = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "state": "unavailable",
        "dates_by_symbol": {},
        "window_start": market_date_et,
        "window_end": None,
        "window_days": EARNINGS_WINDOW_DAYS,
        "source": _INTRADAY_EARNINGS_SOURCE,
        "fetched_at": fetched_at,
        "unavailable_reason": None,
    }
    try:
        from_date = date.fromisoformat(market_date_et)
    except ValueError:
        result["unavailable_reason"] = "invalid_market_date"
        return result
    to_date = from_date + timedelta(days=EARNINGS_WINDOW_DAYS)
    result["window_end"] = to_date.isoformat()
    try:
        rows, reason = _fetch_earnings_calendar_rows(from_date, to_date)
    except Exception as exc:  # noqa: BLE001 - earnings lane must not 500 the board
        logger.debug(
            "[opportunities] intraday earnings calendar unavailable error_type=%s",
            type(exc).__name__,
        )
        rows, reason = None, f"finnhub_error:{type(exc).__name__}"
    if rows is None:
        result["unavailable_reason"] = reason or "finnhub_request_failed"
        ttl = _INTRADAY_EARNINGS_FAILURE_TTL_SECONDS
    else:
        dates_by_symbol: dict[str, list[str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            raw_date = str(row.get("date") or "")[:10]
            if not symbol or not raw_date:
                continue
            bucket = dates_by_symbol.setdefault(symbol, [])
            if raw_date not in bucket:
                bucket.append(raw_date)
        result["state"] = "ready"
        result["dates_by_symbol"] = dates_by_symbol
        ttl = _INTRADAY_EARNINGS_CACHE_TTL_SECONDS

    completion_time = _cache_now()
    with _scan_cache_lock:
        _intraday_earnings_cache[market_date_et] = _ScanCacheEntry(
            expires_at=completion_time + ttl,
            result=copy.deepcopy(result),
        )
        # 一天只需要一个 key；顺手清掉旧 ET 日期，缓存永远只有个位数条目。
        for key in [k for k in _intraday_earnings_cache if k != market_date_et]:
            _intraday_earnings_cache.pop(key, None)
    return result


def _prune_lane_availability_cache(now: float) -> None:
    expired = [
        key
        for key, entry in _lane_availability_cache.items()
        if entry.expires_at <= now
    ]
    for key in expired:
        _lane_availability_cache.pop(key, None)
    while len(_lane_availability_cache) >= _LANE_AVAILABILITY_CACHE_MAX_ENTRIES:
        oldest = min(
            _lane_availability_cache,
            key=lambda item: _lane_availability_cache[item].expires_at,
        )
        _lane_availability_cache.pop(oldest, None)


def _load_lane_availability(
    tickers: list[str],
    *,
    enabled: bool,
    market_date_et: str,
) -> dict[str, Any]:
    """今日车道可用性（V2-E）：深度层标的今天有没有 0DTE 可用。

    额度护栏（逐条见 ``_LANE_AVAILABILITY_*`` 常量注释）：逐标的按 ET 交易日
    缓存、单轮新增读取有上限、参与判定的标的数有上限——**不向宽层全清单扇出**。

    fail closed 的三处：Moomoo 未启用、读取失败、本轮额度预算用尽（deferred）
    一律记为该标的 ``unavailable`` + 原因；聚合层只有在「全部标的都读到了链
    且都没有 0DTE」时才敢说 ``overnight_only``，否则一律 ``unknown``。
    未知不等于「今天没有 0DTE」。
    """

    ordered = normalize_symbols(list(tickers))[:_LANE_AVAILABILITY_MAX_SYMBOLS]
    skipped_for_cap = normalize_symbols(list(tickers))[
        _LANE_AVAILABILITY_MAX_SYMBOLS:
    ]
    if not enabled:
        items = [
            build_ticker_availability(
                symbol,
                None,
                max_dte=LANE_AVAILABILITY_MAX_DTE,
                unavailable_reason="moomoo_opend_not_enabled",
            )
            for symbol in ordered
        ]
        return build_lane_availability(
            items,
            max_dte=LANE_AVAILABILITY_MAX_DTE,
            market_date_et=market_date_et,
            checked_scope=_LANE_AVAILABILITY_CHECKED_SCOPE,
            skipped_tickers=skipped_for_cap,
        )

    now = _cache_now()
    cached: dict[str, dict[str, Any]] = {}
    misses: list[str] = []
    with _scan_cache_lock:
        _prune_lane_availability_cache(now)
        for symbol in ordered:
            entry = _lane_availability_cache.get((symbol, market_date_et))
            if entry is not None and entry.expires_at > now:
                cached[symbol] = copy.deepcopy(entry.result)
            else:
                misses.append(symbol)
    # 单轮新增读取预算：超出的标的本轮 deferred（下一轮补齐），绝不为了
    # 凑齐结论而把「没查」说成「没有 0DTE」。
    to_fetch = misses[:_LANE_AVAILABILITY_MAX_NEW_FETCHES]
    deferred = misses[_LANE_AVAILABILITY_MAX_NEW_FETCHES:]

    from data_provider.moomoo_options import MoomooWallLaneBusyError

    def load_one(symbol: str) -> tuple[str, dict[str, Any], float]:
        try:
            snapshot = _compute_expiry_availability_moomoo(
                symbol, max_dte=LANE_AVAILABILITY_MAX_DTE
            )
        except MoomooWallLaneBusyError:
            # lane 正忙 ≠ 供应商失败：推迟到下一轮（TTL=0 → 不缓存失败），
            # 结论侧与额度预算 deferred 同样保持 unknown。
            logger.debug(
                "[opportunities] lane availability deferred (wall lane busy) "
                "for %s",
                symbol,
            )
            return (
                symbol,
                build_ticker_availability(
                    symbol,
                    None,
                    max_dte=LANE_AVAILABILITY_MAX_DTE,
                    unavailable_reason="deferred_wall_lane_busy",
                ),
                0.0,
            )
        except Exception as exc:  # noqa: BLE001 - availability failures degrade
            logger.debug(
                "[opportunities] lane availability unavailable for %s: %s",
                symbol,
                exc,
            )
            snapshot = None
        if snapshot is None:
            return (
                symbol,
                build_ticker_availability(
                    symbol,
                    None,
                    max_dte=LANE_AVAILABILITY_MAX_DTE,
                    unavailable_reason="option_expiry_metadata_unavailable",
                ),
                _LANE_AVAILABILITY_FAILURE_TTL_SECONDS,
            )
        return (
            symbol,
            build_ticker_availability(
                symbol,
                list(getattr(snapshot, "expiries", ()) or ()),
                max_dte=LANE_AVAILABILITY_MAX_DTE,
            ),
            _LANE_AVAILABILITY_CACHE_TTL_SECONDS,
        )

    fetched: list[tuple[str, dict[str, Any], float]] = []
    if to_fetch:
        if len(to_fetch) == 1:
            fetched.append(load_one(to_fetch[0]))
        else:
            with ThreadPoolExecutor(
                max_workers=min(_LANE_AVAILABILITY_MAX_WORKERS, len(to_fetch)),
                thread_name_prefix="lane-availability",
            ) as pool:
                fetched.extend(pool.map(load_one, to_fetch))

    completion_time = _cache_now()
    with _scan_cache_lock:
        _prune_lane_availability_cache(completion_time)
        for symbol, payload, ttl in fetched:
            if ttl <= 0:
                # lane-busy 推迟：不缓存，下一轮直接重试。
                continue
            _lane_availability_cache[(symbol, market_date_et)] = _ScanCacheEntry(
                expires_at=completion_time + ttl,
                result=copy.deepcopy(payload),
            )
        for key in [
            k for k in _lane_availability_cache if k[1] != market_date_et
        ]:
            _lane_availability_cache.pop(key, None)

    by_symbol = {**cached, **{symbol: payload for symbol, payload, _ in fetched}}
    lane_busy_deferred = [
        symbol
        for symbol, payload, _ in fetched
        if payload.get("unavailable_reason") == "deferred_wall_lane_busy"
    ]
    for symbol in deferred:
        by_symbol[symbol] = build_ticker_availability(
            symbol,
            None,
            max_dte=LANE_AVAILABILITY_MAX_DTE,
            unavailable_reason="deferred_provider_quota_budget",
        )
    items = [by_symbol[symbol] for symbol in ordered if symbol in by_symbol]
    return build_lane_availability(
        items,
        max_dte=LANE_AVAILABILITY_MAX_DTE,
        market_date_et=market_date_et,
        checked_scope=_LANE_AVAILABILITY_CHECKED_SCOPE,
        skipped_tickers=[*skipped_for_cap, *deferred, *lane_busy_deferred],
    )


def _execute_intraday_top(
    symbols: list[str], limit: int, *, enabled: bool
) -> dict[str, Any]:
    """Assemble the rolling intraday Top-N run from G-2 machinery, read-only.

    没有冻结、没有 qualification、没有 5D/20D 结果写入：statistics_track 固定
    为 none_intraday_v1_unscored。期权异动只作为活跃度证据聚合，不推断方向。
    """

    requested_at = _intraday_now()
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    session_state = market_session_state(requested_at)
    supported = [
        symbol for symbol in symbols if is_supported_us_option_underlying(symbol)
    ]
    unsupported = [
        symbol for symbol in symbols if not is_supported_us_option_underlying(symbol)
    ]

    daily_raw = _intraday_daily_inputs(
        supported,
        as_of=requested_at,
        market_date_et=market_date_et,
    )
    dailies = {
        symbol: IntradayDailyContext(
            atr14=payload.get("atr14"),
            atr14_last_bar_date=payload.get("atr14_last_bar_date"),
            atr14_unavailable_reason=payload.get("atr14_unavailable_reason"),
            prior_median_volume=payload.get("prior_20d_median_volume"),
            median_unavailable_reason=payload.get("median_unavailable_reason"),
            prior_close=payload.get("prior_close"),
            prior_close_date=payload.get("prior_close_date"),
            prior_high_20d=payload.get("prior_high_20d"),
            prior_low_20d=payload.get("prior_low_20d"),
            ema8=payload.get("ema8"),
            ema13=payload.get("ema13"),
            source=payload.get("source"),
        )
        for symbol, payload in daily_raw.items()
    }

    quotes: dict[str, IntradayQuoteInput] = {}
    spy_quote: Optional[IntradayQuoteInput] = None
    if enabled and supported:
        # v3 大盘对齐：SPY 并入同一批快照（同一 as-of，不新增请求次数）；
        # SPY 本身在 universe 里时不重复。
        quote_symbols = list(supported)
        if MARKET_CONTEXT_TICKER not in quote_symbols:
            quote_symbols.append(MARKET_CONTEXT_TICKER)
        try:
            raw_quotes = _fetch_underlying_session_quotes(quote_symbols) or {}
        except Exception as exc:  # noqa: BLE001 - fail the batch closed per symbol
            logger.debug(
                "[opportunities] intraday top session quotes unavailable: %s", exc
            )
            raw_quotes = {}
        quotes = {
            symbol: _quote_to_intraday_input(quote)
            for symbol, quote in raw_quotes.items()
            if symbol in supported
        }
        spy_raw = raw_quotes.get(MARKET_CONTEXT_TICKER)
        spy_quote = (
            _quote_to_intraday_input(spy_raw) if spy_raw is not None else None
        )
        # G-7b：动量历史由**每一轮 tier-1 批量快照**喂养（单层/两层同源）。
        # 只有两层路径喂养时，单层轮询/并行调用只消耗不生产，mom15 长期
        # 饥饿。仍仅限常规时段——盘前/盘后常规快照价不再前进。
        if session_state == "regular" and raw_quotes:
            _feed_intraday_momentum_history(
                [
                    {
                        "ticker": symbol,
                        "last_price": getattr(quote, "last_price", None),
                    }
                    for symbol, quote in raw_quotes.items()
                    if symbol in supported
                ],
                market_date_et=market_date_et,
                now_epoch=requested_at.timestamp(),
            )

    option_event_items = _load_intraday_option_event_items(
        supported,
        enabled=enabled,
        market_date_et=market_date_et,
    )

    # 波段爆发（v2 主排序信号）：5m K 线与 Moomoo 开关无关，休市时段也读取
    # （附最近一个交易时段的波段供晚间复盘）；单标的失败显式 unavailable。
    burst_scope = "latest_prior_session" if session_state == "closed" else "current_session"
    burst_profiles, setup_bars = _load_intraday_burst_profiles(
        supported,
        market_date_et=market_date_et,
        quote_session_scope=burst_scope,
    )

    # v4 styleMatch：S1/S2/S3 Playbook 只读对应关系（本地读取 + 5 分钟缓存）；
    # 失败只让形态徽标缺少 Playbook 标注，不阻断任何证据。
    playbook_refs = _load_intraday_playbook_refs()

    # v3 财报临近：一次日历区间读取覆盖整个 universe，逐 ET 日期缓存；
    # 失败只让财报列显式标缺，绝不阻断其余证据。
    earnings_calendar = _load_intraday_earnings_calendar(market_date_et)

    return build_intraday_top_run(
        symbols=supported,
        unsupported_symbols=unsupported,
        quotes=quotes,
        dailies=dailies,
        option_event_items=option_event_items,
        as_of=requested_at,
        market_date_et=market_date_et,
        session_state=session_state,
        session_state_basis=SESSION_STATE_BASIS,
        limit=limit,
        moomoo_enabled=enabled,
        burst_profiles=burst_profiles,
        earnings_calendar=earnings_calendar,
        spy_quote=spy_quote,
        setup_bars=setup_bars,
        playbook_refs=playbook_refs,
    )


def _todays_plan_tickers(market_date_et: str) -> list[str]:
    """今日已冻结盘前计划的标的（只读，深度层永远钉选）。

    计划盘前冻结后当日不再变化，按 ET 日期缓存 5 分钟；读取失败只让钉选
    集合为空（深度层退化为纯 movers），绝不 500 日内榜、绝不写入任何数据。
    """

    now = _cache_now()
    with _scan_cache_lock:
        entry = _intraday_plan_cache.get(market_date_et)
        if entry is not None and entry.expires_at > now:
            return list(entry.result.get("tickers") or [])

    tickers: list[str] = []
    try:
        from src.opportunities.repository import list_snapshots

        for snapshot in list_snapshots(limit=10):
            if snapshot.market_date_et.isoformat() != market_date_et:
                continue
            for candidate in snapshot.candidates:
                symbol = str(candidate.ticker or "").strip().upper()
                if symbol and symbol not in tickers:
                    tickers.append(symbol)
    except Exception as exc:  # noqa: BLE001 - plan pinning must not 500 the board
        logger.debug(
            "[opportunities] intraday plan tickers unavailable error_type=%s",
            type(exc).__name__,
        )
        return []

    completion_time = _cache_now()
    with _scan_cache_lock:
        _intraday_plan_cache[market_date_et] = _ScanCacheEntry(
            expires_at=completion_time + _INTRADAY_PLAN_CACHE_TTL_SECONDS,
            result={"tickers": list(tickers)},
        )
        for key in [k for k in _intraday_plan_cache if k != market_date_et]:
            _intraday_plan_cache.pop(key, None)
    return tickers


def _intraday_wide_snapshot_row(
    symbol: str, quote: Any, *, enabled: bool
) -> dict[str, Any]:
    """宽层（仅快照）单行：只有快照可得字段，绝不虚构深度层读数。

    行内没有任何爆发/形态/速度/异动字段——那些属于深度层；缺失即缺席，
    不以 null 占位冒充「已分析但为空」。
    """

    row: dict[str, Any] = {
        "ticker": symbol,
        "state": "unavailable",
        "last_price": None,
        "change_percent": None,
        "change_basis": "moomoo_snapshot_prev_close",
        "session_high": None,
        "session_low": None,
        "volume": None,
        "turnover": None,
        "quote_as_of": None,
        "unavailable_reason": None,
    }
    if not enabled:
        row["unavailable_reason"] = "moomoo_not_configured"
        return row
    if quote is None:
        row["unavailable_reason"] = "snapshot_missing"
        return row
    last_price = getattr(quote, "last_price", None)
    prev_close = getattr(quote, "prev_close_price", None)
    change_percent: Optional[float] = None
    if (
        last_price is not None
        and prev_close is not None
        and prev_close > 0
        and math.isfinite(last_price)
        and math.isfinite(prev_close)
    ):
        change_percent = round((last_price / prev_close - 1.0) * 100.0, 6)
    row.update(
        last_price=last_price,
        change_percent=change_percent,
        session_high=getattr(quote, "high_price", None),
        session_low=getattr(quote, "low_price", None),
        volume=getattr(quote, "volume", None),
        turnover=getattr(quote, "turnover", None),
        quote_as_of=getattr(quote, "update_time", None),
        # 盘前专用读数（additive）：盘前时段常规字段仍指向上一常规时段，
        # 真实盘前变动在 pre_* 字段；缺列 None，绝不 0 回填。
        pre_change_percent=getattr(quote, "pre_change_rate", None),
        pre_turnover=getattr(quote, "pre_turnover", None),
    )
    if last_price is None:
        row["unavailable_reason"] = "missing_last_price"
    elif change_percent is None:
        row["state"] = "partial"
        row["unavailable_reason"] = "missing_snapshot_prev_close"
    else:
        row["state"] = "ready"
    return row


def _rank_intraday_movers(
    wide_rows: list[dict[str, Any]],
    *,
    exclude: set[str],
    premarket: bool = False,
) -> list[str]:
    """异动闸门 v1：|涨跌幅| 主序、成交额次序、代码字典序兜底（确定性）。

    缺 change_percent 的行（快照未解析/缺前收）不可按异动晋升——闸门绝不
    以 0 涨跌冒充「平静」，这些行留在宽层并显式标缺。

    ``premarket=True`` 时改用盘前口径：|pre_change_percent| 主序、
    pre_turnover 次序；缺盘前字段的行同样不可晋升（语义一致）。
    """

    change_key = "pre_change_percent" if premarket else "change_percent"
    turnover_key = "pre_turnover" if premarket else "turnover"
    ranked = [
        row
        for row in wide_rows
        if row["ticker"] not in exclude and row.get(change_key) is not None
    ]
    ranked.sort(
        key=lambda row: (
            -abs(row[change_key]),
            -(row[turnover_key] if row.get(turnover_key) is not None else -1.0),
            row["ticker"],
        )
    )
    return [row["ticker"] for row in ranked]


def _feed_intraday_momentum_history(
    wide_rows: list[dict[str, Any]],
    *,
    market_date_et: str,
    now_epoch: float,
) -> None:
    """把本轮宽层快照的 last_price 追加进 mom15 滚动历史（零新增请求）。

    仅常规时段调用（调用方约束）；ET 日期切换即整体清空——隔日样本对
    「最近 15 分钟动量」毫无意义，绝不跨日比价。缺价/非法价的行不入历史。
    单层与两层路径的每一轮 tier-1 批量快照都喂养同一份历史（G-7b），
    保留按样本年龄裁剪（时间制），调用频率再高也不会把足龄样本挤出去。
    """

    global _intraday_momentum_history_date
    with _scan_cache_lock:
        if _intraday_momentum_history_date != market_date_et:
            _intraday_momentum_history.clear()
            _intraday_momentum_history_date = market_date_et
        for row in wide_rows:
            last_price = row.get("last_price")
            if (
                last_price is None
                or not math.isfinite(last_price)
                or last_price <= 0
            ):
                continue
            history = _intraday_momentum_history.get(row["ticker"])
            if history is None:
                history = deque(maxlen=_INTRADAY_MOMENTUM_HISTORY_MAXLEN)
                _intraday_momentum_history[row["ticker"]] = history
            history.append((float(now_epoch), float(last_price)))
            while (
                history
                and now_epoch - history[0][0]
                > _INTRADAY_MOMENTUM_RETENTION_SECONDS
            ):
                history.popleft()


def _intraday_mom15(
    symbol: str,
    last_price: Optional[float],
    *,
    now_epoch: float,
) -> Optional[float]:
    """mom15 = (last / price_15min_ago − 1)×100，取 12–18 分钟窗内最老样本。

    窗内无足龄样本（重启 / 开盘冷启动 / 样本已老于 18 分钟）返回 None——
    None 表示「无法度量」，绝不以 0 冒充「没在动」。
    """

    if last_price is None or not math.isfinite(last_price) or last_price <= 0:
        return None
    with _scan_cache_lock:
        history = _intraday_momentum_history.get(symbol)
        if not history:
            return None
        for sample_epoch, sample_price in history:  # 队列恒为时间升序
            age = now_epoch - sample_epoch
            if age > _INTRADAY_MOMENTUM_LOOKBACK_MAX_SECONDS:
                continue  # 老于回看窗上限：跳过，继续找更新的样本。
            if age < _INTRADAY_MOMENTUM_LOOKBACK_MIN_SECONDS:
                return None  # 后续样本只会更年轻：窗内无足龄样本。
            if sample_price <= 0:
                return None
            return (float(last_price) / float(sample_price) - 1.0) * 100.0
    return None


def _rank_intraday_movers_v2(
    wide_rows: list[dict[str, Any]],
    *,
    exclude: set[str],
    deep_lane_max: int,
    now_epoch: float,
) -> tuple[list[str], dict[str, float], bool]:
    """异动闸门 v2 排序：动量子额度（ceil(2K/3)）优先、当日涨跌子额度兜底。

    返回 (ordered_symbols, mom15_by_symbol, momentum_available)。ordered 为完整
    确定性顺序（含额度外候选，供 mover_rank 标注与日上限跳位回补）：首段为
    动量子额度选中者（|mom15| 降序）、中段为当日涨跌子额度选中者（|涨跌|
    降序、已选去重）、尾段为其余候选（动量序在前、涨跌序补足）；每段内部
    均以成交额次序、代码字典序兜底。mom15=None 的标的绝不占动量位；整批皆
    无 mom15 时返回 momentum_available=False，调用方显式回退 v1 并警示。
    """

    eligible = [row for row in wide_rows if row["ticker"] not in exclude]
    mom15_by_symbol: dict[str, float] = {}
    momentum_rows: list[dict[str, Any]] = []
    for row in eligible:
        mom15 = _intraday_mom15(
            row["ticker"], row.get("last_price"), now_epoch=now_epoch
        )
        if mom15 is not None:
            mom15_by_symbol[row["ticker"]] = round(mom15, 6)
            momentum_rows.append(row)
    if not momentum_rows:
        return [], {}, False

    def _turnover_key(row: dict[str, Any]) -> float:
        return -(row["turnover"] if row.get("turnover") is not None else -1.0)

    momentum_order = sorted(
        momentum_rows,
        key=lambda row: (
            -abs(mom15_by_symbol[row["ticker"]]),
            _turnover_key(row),
            row["ticker"],
        ),
    )
    day_order = sorted(
        (row for row in eligible if row.get("change_percent") is not None),
        key=lambda row: (
            -abs(row["change_percent"]),
            _turnover_key(row),
            row["ticker"],
        ),
    )
    momentum_quota = math.ceil(2 * deep_lane_max / 3)
    day_quota = deep_lane_max - momentum_quota

    head = [row["ticker"] for row in momentum_order[:momentum_quota]]
    chosen = set(head)
    mid: list[str] = []
    for row in day_order:
        if len(mid) >= day_quota:
            break
        if row["ticker"] in chosen:
            continue
        mid.append(row["ticker"])
        chosen.add(row["ticker"])
    tail = [
        row["ticker"] for row in momentum_order if row["ticker"] not in chosen
    ]
    tail_seen = chosen | set(tail)
    for row in day_order:
        if row["ticker"] not in tail_seen:
            tail.append(row["ticker"])
            tail_seen.add(row["ticker"])
    return [*head, *mid, *tail], mom15_by_symbol, True


def _execute_intraday_top_two_tier(
    watchlist: list[str],
    limit: int,
    *,
    enabled: bool,
    deep_lane_max: int,
    watchlist_configured_total: int,
    pinned_tickers: Optional[list[str]] = None,
    focus_tickers: Optional[list[str]] = None,
) -> dict[str, Any]:
    """watchlist 两层扫描：一次批量快照的宽层 + 异动闸门晋升的深度层。

    宽层每轮只发 1 次 ``get_market_snapshot``（清单 + 计划钉选 + 用户钉选 +
    SPY 并入同一批；单次官方上限 400，本清单有界 200 恒为单请求）。深度层
    完全复用单层模式的 v4 管线（爆发/形态/速度/异动/财报/合约面板资格），
    只对晋升标的执行；宽层其余标的仅保留快照行，深度字段一律缺席。额度
    语义与每日晋升护栏见模块常量 ``_INTRADAY_DEEP_DAILY_DISTINCT_CAP`` 注释；
    闸门 v2（15 分钟动量主导）语义见 ``_INTRADAY_TWO_TIER_GATE_BASIS_V2``。
    """

    requested_at = _intraday_now()
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    session_state = market_session_state(requested_at)

    plan_tickers_all = _todays_plan_tickers(market_date_et)
    pinned_tickers_all = normalize_symbols(list(pinned_tickers or []))
    # 盘中计划提升（focus）：与配置钉选同一语义，只是来源是本次请求。
    focus_tickers_all = normalize_symbols(list(focus_tickers or []))[
        :_INTRADAY_FOCUS_MAX_SYMBOLS
    ]
    # 扫描 universe = 清单 ∪ 今日计划 ∪ 用户钉选 ∪ 盘中计划提升：计划/钉选/
    # 提升标的并入同一批快照（仍 1 次请求），即使不在清单里也能拿到深度层
    # 所需的会话快照——不新增任何取数路径。
    scan_symbols = normalize_symbols(
        [*watchlist, *plan_tickers_all, *pinned_tickers_all, *focus_tickers_all]
    )
    supported = [
        symbol for symbol in scan_symbols if is_supported_us_option_underlying(symbol)
    ]
    unsupported = [
        symbol
        for symbol in scan_symbols
        if not is_supported_us_option_underlying(symbol)
    ]
    supported_set = set(supported)
    plan_tickers = [
        symbol for symbol in plan_tickers_all if symbol in supported_set
    ]
    # 用户钉选与计划钉选去重：同一标的两个身份并存时计划钉选优先标注
    # （深度位语义一致，占位只记一次）。
    pinned_effective = [
        symbol
        for symbol in pinned_tickers_all
        if symbol in supported_set and symbol not in plan_tickers
    ]
    # 盘中计划提升同样与计划/配置钉选去重：同一标的只占一个深度位，
    # 标注取先出现的身份（计划 > 配置钉选 > 盘中计划提升）。
    focus_effective = [
        symbol
        for symbol in focus_tickers_all
        if symbol in supported_set
        and symbol not in plan_tickers
        and symbol not in pinned_effective
    ]

    # -- Tier 1：一次批量快照（SPY 并入，不新增请求次数）---------------------
    raw_quotes: dict[str, Any] = {}
    if enabled and supported:
        quote_symbols = list(supported)
        if MARKET_CONTEXT_TICKER not in quote_symbols:
            quote_symbols.append(MARKET_CONTEXT_TICKER)
        try:
            raw_quotes = _fetch_underlying_session_quotes(quote_symbols) or {}
        except Exception as exc:  # noqa: BLE001 - fail the batch closed per symbol
            logger.debug(
                "[opportunities] intraday two-tier snapshot unavailable: %s", exc
            )
            raw_quotes = {}
    spy_raw = raw_quotes.get(MARKET_CONTEXT_TICKER)
    spy_quote = _quote_to_intraday_input(spy_raw) if spy_raw is not None else None

    wide_rows = [
        _intraday_wide_snapshot_row(symbol, raw_quotes.get(symbol), enabled=enabled)
        for symbol in supported
    ]
    snapshot_unresolved = [
        symbol
        for symbol in supported
        if enabled and raw_quotes.get(symbol) is None
    ]

    # -- 异动闸门（movers gate）----------------------------------------------
    # 盘前时段常规快照字段仍指向上一常规时段：若此时按 change_percent 排序，
    # 深度榜会复现上一时段的异动而非今晨盘前的真实异动。盘前改用 pre_* 口径
    # （G-12，v2 不改动）；整批无盘前字段时显式回退 + 警示，绝不静默。
    # 非盘前时段走 v2：15 分钟动量子额度优先、当日涨跌子额度兜底（校准背景
    # 见 _INTRADAY_TWO_TIER_GATE_BASIS_V2 常量注释）；动量历史冷启动时显式
    # 回退 v1 当日涨跌口径并携带 warming-up 警示。
    plan_set = set(plan_tickers)
    pinned_set = set(pinned_effective)
    focus_set = set(focus_effective)
    always_deep_set = plan_set | pinned_set | focus_set
    session_phase_now = market_session_phase(requested_at)
    now_epoch = requested_at.timestamp()
    premarket_gate = session_phase_now == "premarket" and any(
        row.get("pre_change_percent") is not None for row in wide_rows
    )
    gate_warnings: list[str] = []
    if premarket_gate:
        gate_basis = _INTRADAY_TWO_TIER_PREMARKET_GATE_BASIS
        mover_order = _rank_intraday_movers(
            wide_rows, exclude=always_deep_set, premarket=True
        )
    elif session_phase_now == "premarket":
        gate_basis = _INTRADAY_TWO_TIER_GATE_BASIS
        gate_warnings.append(_INTRADAY_PREMARKET_FIELDS_UNAVAILABLE_WARNING)
        mover_order = _rank_intraday_movers(wide_rows, exclude=always_deep_set)
    else:
        # 动量历史只在常规时段喂养（盘后/休市常规快照价不再前进，喂入只会
        # 把「静止」误记为动量样本）；排序侧任何非盘前时段都先尝试 v2。
        if session_state == "regular":
            _feed_intraday_momentum_history(
                wide_rows, market_date_et=market_date_et, now_epoch=now_epoch
            )
        mover_order, _mom15_by_symbol, momentum_available = (
            _rank_intraday_movers_v2(
                wide_rows,
                exclude=always_deep_set,
                deep_lane_max=deep_lane_max,
                now_epoch=now_epoch,
            )
        )
        if momentum_available:
            gate_basis = _INTRADAY_TWO_TIER_GATE_BASIS_V2
        else:
            # 冷启动（重启/开盘/样本过期）：显式回退 v1 并警示，绝不静默。
            gate_basis = _INTRADAY_TWO_TIER_GATE_BASIS
            gate_warnings.append(_INTRADAY_MOMENTUM_WARMING_UP_WARNING)
            mover_order = _rank_intraday_movers(
                wide_rows, exclude=always_deep_set
            )
    mover_rank_by_symbol = {
        symbol: rank for rank, symbol in enumerate(mover_order, start=1)
    }

    promoted_movers: list[str] = []
    day_cap_reached = False
    with _scan_cache_lock:
        promoted_today = _intraday_deep_promotion_log.setdefault(
            market_date_et, set()
        )
        for key in [k for k in _intraday_deep_promotion_log if k != market_date_et]:
            _intraday_deep_promotion_log.pop(key, None)
        # G-7a：额度判定在**假想集合**上进行；当日账本只在总数硬顶裁剪后
        # 提交「本轮真正进入深度层」的标的——被硬顶挤掉（trimmed_by_total_cap）
        # 的标的从未被深扫，绝不消耗当日/30 天配额记录。
        # 计划/用户钉选/盘中计划提升不受日上限约束，但计入假想去重集合
        # （额度语义一致）。
        tentative_today = set(promoted_today)
        tentative_today.update(plan_tickers)
        tentative_today.update(pinned_effective)
        tentative_today.update(focus_effective)
        for symbol in mover_order:
            if len(promoted_movers) >= deep_lane_max:
                break
            if symbol in tentative_today:
                promoted_movers.append(symbol)
                continue
            if len(tentative_today) >= _INTRADAY_DEEP_DAILY_DISTINCT_CAP:
                # 日上限触顶：该标的当日只保留宽层快照行（显式标注，不静默）。
                day_cap_reached = True
                continue
            tentative_today.add(symbol)
            promoted_movers.append(symbol)

    # -- 深度层总行数硬顶 ----------------------------------------------------
    # 用户要求扫描表「留 8 个，太多也看不过来」。优先级：盘中计划（用户当下
    # 明确挑出来的）→ 用户钉选 → 异动（保底 _INTRADAY_DEEP_MOVER_FLOOR 个位置
    # 给发现，否则扫描表失去意义）→ 盘前计划标的（它们另有「今日计划跟踪」
    # 面板，走独立接口，不依赖本深度层）。被挤掉的一律显式披露，绝不静默丢弃。
    mover_symbols = [
        symbol for symbol in promoted_movers if symbol not in always_deep_set
    ]
    ordered_groups = (
        ("user_focus", list(focus_effective)),
        ("user_pinned", list(pinned_effective)),
        ("mover_rank", mover_symbols),
        ("plan_always_include", list(plan_tickers)),
    )
    total_cap = _INTRADAY_DEEP_LANE_TOTAL_MAX
    reserved_for_movers = min(_INTRADAY_DEEP_MOVER_FLOOR, len(mover_symbols))
    deep_symbols = []
    trimmed_by_total_cap: list[dict[str, Any]] = []
    for group_key, members in ordered_groups:
        for symbol in members:
            if symbol in deep_symbols:
                continue
            remaining = total_cap - len(deep_symbols)
            # 给异动留的保底名额不被前面的组占满之外的组吃掉。
            if group_key != "mover_rank":
                remaining -= max(
                    0,
                    reserved_for_movers
                    - sum(1 for s in deep_symbols if s in set(mover_symbols)),
                )
            if remaining <= 0:
                trimmed_by_total_cap.append(
                    {"ticker": symbol, "would_be_promoted_by": group_key}
                )
                continue
            deep_symbols.append(symbol)
    deep_set = set(deep_symbols)

    # G-7a：当日晋升账本只记「本轮真正进入深度层」的标的——display truth
    # 与供应商配额语义一致（宽层快照行不消耗 request_history_kline 配额）。
    with _scan_cache_lock:
        _intraday_deep_promotion_log.setdefault(market_date_et, set()).update(
            deep_symbols
        )

    # -- Tier 2：既有 v4 管线，仅深度层标的 -----------------------------------
    daily_raw = _intraday_daily_inputs(
        deep_symbols,
        as_of=requested_at,
        market_date_et=market_date_et,
    )
    dailies = {
        symbol: IntradayDailyContext(
            atr14=payload.get("atr14"),
            atr14_last_bar_date=payload.get("atr14_last_bar_date"),
            atr14_unavailable_reason=payload.get("atr14_unavailable_reason"),
            prior_median_volume=payload.get("prior_20d_median_volume"),
            median_unavailable_reason=payload.get("median_unavailable_reason"),
            prior_close=payload.get("prior_close"),
            prior_close_date=payload.get("prior_close_date"),
            prior_high_20d=payload.get("prior_high_20d"),
            prior_low_20d=payload.get("prior_low_20d"),
            ema8=payload.get("ema8"),
            ema13=payload.get("ema13"),
            source=payload.get("source"),
        )
        for symbol, payload in daily_raw.items()
    }
    quotes = {
        symbol: _quote_to_intraday_input(raw_quotes[symbol])
        for symbol in deep_symbols
        if raw_quotes.get(symbol) is not None
    }

    option_event_items = _load_intraday_option_event_items(
        deep_symbols,
        enabled=enabled,
        market_date_et=market_date_et,
    )
    burst_scope = (
        "latest_prior_session" if session_state == "closed" else "current_session"
    )
    burst_profiles, setup_bars = _load_intraday_burst_profiles(
        deep_symbols,
        market_date_et=market_date_et,
        quote_session_scope=burst_scope,
    )
    playbook_refs = _load_intraday_playbook_refs()
    earnings_calendar = _load_intraday_earnings_calendar(market_date_et)

    run = build_intraday_top_run(
        symbols=deep_symbols,
        unsupported_symbols=unsupported,
        quotes=quotes,
        dailies=dailies,
        option_event_items=option_event_items,
        as_of=requested_at,
        market_date_et=market_date_et,
        session_state=session_state,
        session_state_basis=SESSION_STATE_BASIS,
        limit=limit,
        moomoo_enabled=enabled,
        burst_profiles=burst_profiles,
        earnings_calendar=earnings_calendar,
        spy_quote=spy_quote,
        setup_bars=setup_bars,
        playbook_refs=playbook_refs,
        # 深度层名单已由闸门有界：全部返回，不再按 limit 二次截断
        # （否则「已深度分析却无声消失」）。requested_limit 仍如实回显。
        include_all_candidates=True,
    )

    # 每个深度候选标注进入深度层的原因（additive；单层模式无此字段）。
    def _deep_lane_reason(symbol: str) -> dict[str, Any]:
        if symbol in plan_set:
            return {
                "promoted_by": "plan_always_include",
                "mover_rank": None,
                "basis": gate_basis,
            }
        if symbol in pinned_set:
            return {
                "promoted_by": "user_pinned",
                "mover_rank": None,
                "basis": gate_basis,
            }
        if symbol in focus_set:
            return {
                "promoted_by": "user_focus",
                "mover_rank": None,
                "basis": gate_basis,
            }
        return {
            "promoted_by": "mover_rank",
            "mover_rank": mover_rank_by_symbol.get(symbol),
            "basis": gate_basis,
        }

    wide_row_by_ticker = {row["ticker"]: row for row in wide_rows}
    for candidate in run["candidates"]:
        candidate["scan_tier"] = "deep"
        candidate["deep_lane_reason"] = _deep_lane_reason(
            str(candidate.get("ticker") or "")
        )
        # 盘中机会提示器需要深度层标的的盘前读数：盘前异动最强的标的恰好
        # 会被闸门晋升，宽层快照行随之离开 snapshot_only。把同一批快照的
        # pre_change_percent 原样带到候选上（进程内 additive 字段；API 响应
        # 模型未声明它，序列化时被丢弃，客户端载荷逐字节不变）。
        wide_row = wide_row_by_ticker.get(str(candidate.get("ticker") or ""))
        candidate["pre_change_percent"] = (
            wide_row.get("pre_change_percent") if wide_row else None
        )

    # -- 今日深扫账本（display truth）----------------------------------------
    # 曾晋升深度层的标的被 movers 轮换出去后不得无声消失：进程内保留其最后
    # 一次深扫的候选摘要（含分级波段），响应以 rotated_out 行 as-of 呈现。
    # 只覆盖服务启动后的周期（重启即清空），不写数据库，不冒充实时。
    day_ledger_rows: list[dict[str, Any]] = []
    with _scan_cache_lock:
        ledger = _intraday_day_ledger.setdefault(market_date_et, {})
        for key in [k for k in _intraday_day_ledger if k != market_date_et]:
            _intraday_day_ledger.pop(key, None)
        for candidate in run["candidates"]:
            symbol = str(candidate.get("ticker") or "").strip().upper()
            if not symbol:
                continue
            bursts = candidate.get("session_bursts") or {}
            setup_match = candidate.get("setup_match") or {}
            ledger[symbol] = {
                "ticker": symbol,
                "last_seen_at": requested_at.isoformat(),
                "session_bursts_legs": copy.deepcopy(
                    list(bursts.get("legs") or [])
                ),
                "setup_matched_setups": list(
                    setup_match.get("matched_setups") or []
                ),
                "last_change_percent": candidate.get("session_change_percent"),
            }
        day_ledger_rows = [
            {**copy.deepcopy(entry), "state": "rotated_out"}
            for symbol, entry in ledger.items()
            if symbol not in deep_set
        ]
    # 最近离场的排前（同刻按代码字典序），确定性输出。
    day_ledger_rows.sort(key=lambda row: row["ticker"])
    day_ledger_rows.sort(key=lambda row: row["last_seen_at"], reverse=True)

    # 宽层剩余标的：按闸门同口径降序（标缺行恒排最后），诚实可见。
    _snapshot_sort_key = "pre_change_percent" if premarket_gate else "change_percent"
    snapshot_only = sorted(
        (row for row in wide_rows if row["ticker"] not in deep_set),
        key=lambda row: (
            0 if row.get(_snapshot_sort_key) is not None else 1,
            -abs(row.get(_snapshot_sort_key) or 0.0),
            row["ticker"],
        ),
    )

    # -- 今日车道可用性（V2-E，additive）--------------------------------------
    # 只对深度层标的判定：它们已经是今天真正要看的名单，且数量有界；绝不向
    # 宽层全清单扇出。判定输入是当日真实期权到期日元数据，不是星期规则——
    # 假日与特殊到期会让星期规则失效。
    run["lane_availability"] = _load_lane_availability(
        deep_symbols,
        enabled=enabled,
        market_date_et=market_date_et,
    )

    # universe＝宽层实际扫描的全部标的（快照层面全部覆盖），候选＝深度层。
    run["universe"] = list(supported)
    run["universe_scan"] = {
        "mode": "watchlist_two_tier",
        "gate_basis": gate_basis,
        "gate_warnings": gate_warnings,
        "watchlist_total": watchlist_configured_total,
        "watchlist_truncated": watchlist_configured_total > len(watchlist),
        "scanned_total": len(supported),
        "deep_lane_count": len(deep_symbols),
        "deep_lane_max": deep_lane_max,
        "deep_lane": [
            {
                "ticker": symbol,
                "promoted_by": _deep_lane_reason(symbol)["promoted_by"],
                "mover_rank": _deep_lane_reason(symbol)["mover_rank"],
            }
            for symbol in deep_symbols
        ],
        "plan_always_include": list(plan_tickers),
        "user_pinned": list(pinned_effective),
        "user_focus": list(focus_effective),
        "gated_out_count": len(supported) - len(deep_symbols),
        "snapshot_unresolved_symbols": snapshot_unresolved,
        "day_promotion_cap": _INTRADAY_DEEP_DAILY_DISTINCT_CAP,
        "day_promotion_cap_reached": day_cap_reached,
        "day_ledger": day_ledger_rows,
        "day_ledger_basis": _INTRADAY_DAY_LEDGER_BASIS,
        "deep_lane_total_max": _INTRADAY_DEEP_LANE_TOTAL_MAX,
        "trimmed_by_total_cap": trimmed_by_total_cap,
        "snapshot_only": snapshot_only,
        "limitations": list(_INTRADAY_TWO_TIER_LIMITATIONS),
    }
    run["limitations"] = [*run["limitations"], *_INTRADAY_TWO_TIER_LIMITATIONS]
    return run


def _intraday_pulse_item(
    ticker: str,
    quote: Any,
    *,
    enabled: bool,
    fetched_at: datetime,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "ticker": ticker,
        "state": "unavailable",
        "last_price": None,
        "prev_close": None,
        "change_percent": None,
        "change_basis": "moomoo_snapshot_prev_close",
        "vwap": None,
        "vwap_position": "unknown",
        "vwap_basis": VWAP_BASIS_SESSION_TURNOVER_OVER_VOLUME,
        "vwap_unavailable_reason": None,
        "quote_as_of": None,
        "fetched_at": fetched_at.isoformat(),
        "source": _INTRADAY_TRACKING_SOURCE,
        "message": "",
        "limitations": list(_INTRADAY_PULSE_LIMITATIONS),
    }
    if not enabled:
        item.update(
            state="not_configured",
            vwap_unavailable_reason="moomoo_not_configured",
            message="MOOMOO_OPEND_ENABLED 未启用；未读取快照。",
        )
        return item
    if quote is None:
        item.update(
            vwap_unavailable_reason="quote_unavailable",
            message="Moomoo 未返回该代码的快照；显式标缺，不以 0 或旧值冒充。",
        )
        return item
    last_price = getattr(quote, "last_price", None)
    prev_close = getattr(quote, "prev_close_price", None)
    quote_fetched_at = getattr(quote, "fetched_at", None)
    change_percent: Optional[float] = None
    if (
        last_price is not None
        and prev_close is not None
        and prev_close > 0
        and math.isfinite(last_price)
        and math.isfinite(prev_close)
    ):
        change_percent = round((last_price / prev_close - 1.0) * 100.0, 6)
    # v3 大盘对齐输入：会话 VWAP 近似（累计额 ÷ 累计量），任一输入缺失
    # 即显式标缺；VIX 等指数没有成交额属正常标缺，不是错误。
    vwap = compute_session_vwap(
        getattr(quote, "turnover", None),
        getattr(quote, "volume", None),
    )
    vwap_position = "unknown"
    if vwap.value is not None and last_price is not None and math.isfinite(last_price):
        if last_price > vwap.value:
            vwap_position = "above"
        elif last_price < vwap.value:
            vwap_position = "below"
        else:
            vwap_position = "flat"
    item.update(
        last_price=last_price,
        prev_close=prev_close,
        change_percent=change_percent,
        vwap=vwap.value,
        vwap_position=vwap_position,
        vwap_unavailable_reason=vwap.unavailable_reason,
        quote_as_of=getattr(quote, "update_time", None),
        fetched_at=(
            quote_fetched_at.isoformat()
            if isinstance(quote_fetched_at, datetime)
            else fetched_at.isoformat()
        ),
    )
    if last_price is None:
        item.update(
            message="快照缺少有效现价；显式标缺。",
        )
        return item
    if change_percent is None:
        item.update(
            state="partial",
            message="现价可用，但缺快照前收，无法计算涨跌幅；不估算回填。",
        )
        return item
    item.update(state="ready", message="快照读数就绪；仅作盘中背景。")
    return item


def _execute_intraday_pulse(*, enabled: bool) -> dict[str, Any]:
    requested_at = _intraday_now()
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    session_state = market_session_state(requested_at)
    core_quotes: dict[str, Any] = {}
    optional_quotes: dict[str, Any] = {}
    if enabled:
        try:
            core_quotes = (
                _fetch_underlying_session_quotes(
                    list(_INTRADAY_PULSE_CORE_SYMBOLS)
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001 - pulse must degrade per symbol
            logger.debug("[opportunities] pulse core quotes unavailable: %s", exc)
        try:
            # VIX is isolated: an invalid/unsupported index code must not be
            # able to fail the SPY/QQQ batch.
            optional_quotes = (
                _fetch_underlying_session_quotes(
                    list(_INTRADAY_PULSE_OPTIONAL_SYMBOLS)
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[opportunities] pulse VIX quote unavailable: %s", exc)
    merged = {**core_quotes, **optional_quotes}
    items = [
        _intraday_pulse_item(
            ticker,
            merged.get(ticker),
            enabled=enabled,
            fetched_at=requested_at,
        )
        for ticker in (*_INTRADAY_PULSE_CORE_SYMBOLS, *_INTRADAY_PULSE_OPTIONAL_SYMBOLS)
    ]
    session_phase = market_session_phase(requested_at)
    return {
        "schema_version": _INTRADAY_PULSE_SCHEMA,
        "generated_at": requested_at.isoformat(),
        "market_date_et": market_date_et,
        "session_state": session_state,
        "session_state_basis": SESSION_STATE_BASIS,
        "session_phase": session_phase,
        "session_phase_label": session_phase_label(session_phase),
        "session_phase_hint_basis": SESSION_PHASE_HINT_BASIS,
        "items": items,
        "limitations": list(_INTRADAY_PULSE_LIMITATIONS),
    }


@router.post("/daily", response_model=DailyOpportunityResponse)
def daily_opportunities(payload: DailyOpportunityRequest) -> DailyOpportunityResponse:
    """Return an evidence-first daily research list with no opaque total score."""

    symbols = payload.symbols or _configured_symbols()
    symbols = normalize_symbols(symbols)[:20]
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_universe",
                "message": "symbols 为空且服务端 STOCK_LIST 未配置。",
            },
        )

    key = _scan_cache_key(symbols, payload.limit)
    try:
        result = _get_or_compute_scan(
            key,
            lambda: _execute_daily_scan(symbols, payload.limit),
            bypass_cache=payload.refresh,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return DailyOpportunityResponse.model_validate(result)


@router.get("/premarket/universe", response_model=PremarketUniverseResponse)
def get_premarket_research_universe() -> PremarketUniverseResponse:
    """Read the persisted research pool or a non-active STOCK_LIST suggestion."""

    from src.services.premarket_research_service import (
        resolve_premarket_research_universe,
    )

    persisted = resolve_premarket_research_universe(
        include_fallback_suggestion=False,
    )
    if persisted is not None:
        return PremarketUniverseResponse(
            configured=True,
            universe_version_key=persisted.universe_version_key,
            source="persisted",
            symbols=list(persisted.symbols),
            limit=persisted.requested_limit,
            created_at=(
                persisted.created_at.isoformat()
                if persisted.created_at is not None
                else None
            ),
            message="已读取服务端持久化研究池。",
        )

    suggestion = resolve_premarket_research_universe(
        configured_symbols=_configured_symbols(),
        include_fallback_suggestion=True,
    )
    if suggestion is None:
        return PremarketUniverseResponse(
            configured=False,
            source="unavailable",
            symbols=[],
            limit=5,
            message=(
                "尚未配置正式研究池；当前也没有可展示的美股 STOCK_LIST 建议。"
            ),
        )
    return PremarketUniverseResponse(
        configured=False,
        source=suggestion.source,
        symbols=list(suggestion.symbols),
        limit=suggestion.requested_limit,
        message=(
            "这是未启用的 STOCK_LIST 建议；必须显式保存后才会用于盘前研究。"
        ),
    )


@router.put("/premarket/universe", response_model=PremarketUniverseResponse)
def put_premarket_research_universe(
    payload: PremarketUniversePutRequest,
) -> PremarketUniverseResponse:
    """Append one immutable, explicit server-side research-pool revision."""

    from src.opportunities.cycle_repository import append_research_universe
    from src.opportunities.premarket import CANONICAL_SCOPE_KEY

    symbols = normalize_symbols(payload.symbols)
    unsupported = [
        symbol
        for symbol in symbols
        if not is_supported_us_option_underlying(symbol)
    ]
    if not symbols or unsupported:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_premarket_universe",
                "message": "盘前研究池只接受受支持的美股期权标的。",
                "unsupported_symbols": unsupported,
            },
        )
    stored, duplicate = append_research_universe(
        symbols,
        payload.limit,
        scope_key=CANONICAL_SCOPE_KEY,
        source="api",
    )
    return PremarketUniverseResponse(
        configured=True,
        universe_version_key=stored.universe_version_key,
        source="persisted",
        symbols=list(stored.symbols),
        limit=stored.requested_limit,
        created_at=stored.created_at.isoformat(),
        duplicate=duplicate,
        message=(
            "该研究池版本已存在，未重复写入。"
            if duplicate
            else "已保存新的正式研究池版本；后续周期会从该版本开始。"
        ),
    )


@router.post("/premarket/status", response_model=PremarketCycleResponse)
def premarket_research_status(
    payload: PremarketCycleRequest,
) -> PremarketCycleResponse:
    """Read the exact canonical cycle state without starting provider work."""

    from src.opportunities.premarket import PremarketCalendarError
    from src.services.premarket_research_service import (
        status_canonical_premarket_research,
    )

    universe = _persisted_premarket_universe()
    symbols = list(universe.symbols) if universe is not None else []
    limit = universe.requested_limit if universe is not None else payload.limit
    universe_source = universe.source if universe is not None else "unavailable"
    universe_version_key = (
        universe.universe_version_key if universe is not None else None
    )
    try:
        result = status_canonical_premarket_research(
            symbols,
            limit,
            universe_source=universe_source,
            universe_version_key=universe_version_key,
            scheduler_enabled=_premarket_scheduler_enabled(),
        )
    except PremarketCalendarError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "xnys_calendar_unavailable",
                "message": "XNYS 交易日历暂时不可用。",
            },
        ) from exc
    return PremarketCycleResponse.model_validate(result)


@router.post("/premarket/run", response_model=PremarketCycleResponse)
def run_premarket_research(
    payload: PremarketCycleRequest,
) -> PremarketCycleResponse:
    """Explicitly run, or otherwise only read, the canonical cycle."""

    from src.opportunities.premarket import PremarketCalendarError
    from src.services.premarket_research_service import (
        run_canonical_premarket_research,
    )

    if not payload.manual:
        return premarket_research_status(payload)

    universe = _persisted_premarket_universe()
    symbols = list(universe.symbols) if universe is not None else []
    limit = universe.requested_limit if universe is not None else payload.limit
    universe_source = universe.source if universe is not None else "unavailable"
    universe_version_key = (
        universe.universe_version_key if universe is not None else None
    )
    try:
        result = run_canonical_premarket_research(
            symbols,
            limit,
            scan_runner=_execute_daily_scan,
            universe_source=universe_source,
            universe_version_key=universe_version_key,
            trigger="manual",
            scheduler_enabled=_premarket_scheduler_enabled(),
        )
    except PremarketCalendarError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "xnys_calendar_unavailable",
                "message": "XNYS 交易日历暂时不可用。",
            },
        ) from exc
    return PremarketCycleResponse.model_validate(result)


@router.post("/snapshots/freeze", response_model=OpportunitySnapshotItem)
def freeze_opportunity_snapshot(
    payload: DailyOpportunityRequest,
) -> OpportunitySnapshotItem:
    """Explicitly freeze the first immutable research snapshot for a daily slot."""

    from src.services.opportunity_snapshot_service import freeze_daily_snapshot

    symbols = normalize_symbols(payload.symbols or _configured_symbols())[:20]
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_universe",
                "message": "symbols 为空且服务端 STOCK_LIST 未配置。",
            },
        )
    key = _scan_cache_key(symbols, payload.limit)
    try:
        run = _get_or_compute_scan(
            key,
            lambda: _execute_daily_scan(symbols, payload.limit),
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    try:
        result = freeze_daily_snapshot(run)
    except SnapshotConflictError as exc:
        # This should be rare because the service checks the stable slot first;
        # retain an explicit conflict if two different first writers race.
        raise HTTPException(
            status_code=409,
            detail={"error": "immutable_snapshot_conflict", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_snapshot_input", "message": str(exc)},
        ) from exc
    return OpportunitySnapshotItem.model_validate(result)


@router.post(
    "/snapshots/ensure",
    response_model=OpportunitySnapshotEnsureResponse,
)
def ensure_opportunity_snapshot(
    payload: DailyOpportunityRequest,
) -> OpportunitySnapshotEnsureResponse:
    """Idempotently save the official pre-open research version when valid."""

    from src.services.opportunity_snapshot_service import ensure_daily_snapshot

    symbols = normalize_symbols(payload.symbols or _configured_symbols())[:20]
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_universe",
                "message": "symbols 为空且服务端 STOCK_LIST 未配置。",
            },
        )
    key = _scan_cache_key(symbols, payload.limit)
    try:
        run = _get_or_compute_scan(
            key,
            lambda: _execute_daily_scan(symbols, payload.limit),
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    try:
        result = ensure_daily_snapshot(run)
    except SnapshotConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "immutable_snapshot_conflict", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_snapshot_input", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - automatic tracking is non-blocking
        logger.error("[opportunity-snapshot] automatic ensure unavailable: %s", exc)
        result = {
            "schema_version": "opportunity-snapshot-ensure/1.0",
            "state": "unavailable",
            "market_date_et": str(run.get("market_date_et") or ""),
            "snapshot": None,
            "message": "结果跟踪暂时不可用；今日研究清单仍可正常查看。",
        }
    return OpportunitySnapshotEnsureResponse.model_validate(result)


@router.get("/snapshots", response_model=OpportunitySnapshotListResponse)
def opportunity_snapshots(
    limit: int = Query(10, ge=1, le=30),
) -> OpportunitySnapshotListResponse:
    """List immutable research snapshots without mutating outcome state."""

    from src.services.opportunity_snapshot_service import list_snapshot_items

    return OpportunitySnapshotListResponse.model_validate(
        list_snapshot_items(limit=limit)
    )


@router.get(
    "/snapshots/{snapshot_key}",
    response_model=OpportunitySnapshotDetailResponse,
)
def opportunity_snapshot_detail(snapshot_key: str) -> OpportunitySnapshotDetailResponse:
    """Return one immutable snapshot with its frozen run payload, read-only."""

    from src.services.opportunity_snapshot_service import get_snapshot_detail

    if not re.fullmatch(r"ops_[0-9a-f]{64}", snapshot_key):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "snapshot_not_found",
                "message": "snapshot_key 格式不合法或不存在。",
            },
        )
    try:
        result = get_snapshot_detail(snapshot_key)
    except Exception as exc:  # noqa: BLE001 - bounded retryable API state
        logger.error(
            "[opportunity-snapshot] snapshot detail unavailable: %s",
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "snapshot_detail_unavailable",
                "message": "快照读取暂不可用；冻结证据未被修改，请稍后重试。",
            },
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "snapshot_not_found",
                "message": "指定 snapshot_key 不存在。",
            },
        )
    return OpportunitySnapshotDetailResponse.model_validate(result)


@router.post(
    "/snapshots/{snapshot_key}/evaluate",
    response_model=OpportunitySnapshotEvaluationResponse,
)
def evaluate_opportunity_snapshot(
    snapshot_key: str,
) -> OpportunitySnapshotEvaluationResponse:
    """Append only due, target-session-complete underlying outcomes."""

    from src.services.opportunity_snapshot_service import (
        OpportunitySnapshotNotFoundError,
        evaluate_snapshot,
    )

    try:
        result = evaluate_snapshot(snapshot_key)
    except OpportunitySnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "snapshot_not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_outcome_input", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - expose a bounded retryable API state
        logger.error(
            "[opportunity-outcomes] snapshot evaluation unavailable: %s",
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "outcome_evaluation_unavailable",
                "message": "结果评估暂不可用；冻结证据未被修改，请稍后重试。",
            },
        ) from exc
    return OpportunitySnapshotEvaluationResponse.model_validate(result)


@router.get(
    "/learning-summary",
    response_model=OpportunityLearningSummaryResponse,
)
def opportunity_learning_summary() -> OpportunityLearningSummaryResponse:
    """Return guarded descriptive statistics; never change ranking weights."""

    from src.config import get_config
    from src.opportunities.maintenance_repository import (
        OUTCOME_MAINTENANCE_POLICY_VERSION,
        get_latest_outcome_maintenance,
    )
    from src.services.opportunity_snapshot_service import learning_summary

    result = learning_summary()
    result["automatic_maintenance_enabled"] = bool(
        getattr(
            get_config(),
            "opportunity_outcome_scheduler_enabled",
            False,
        )
    )
    result["maintenance_policy_version"] = (
        OUTCOME_MAINTENANCE_POLICY_VERSION
    )
    latest = get_latest_outcome_maintenance()
    if latest is not None:
        result["latest_maintenance"] = {
            "session_date_et": latest.session_date_et.isoformat(),
            "policy_version": latest.policy_version,
            "state": latest.state,
            "attempt_count": latest.attempt_count,
            "completed_at": (
                latest.completed_at.isoformat()
                if latest.completed_at is not None
                else None
            ),
            "next_retry_at": (
                latest.next_retry_at.isoformat()
                if latest.next_retry_at is not None
                else None
            ),
            "due_snapshot_count": int(
                latest.result.get("due_snapshot_count") or 0
            ),
            "inserted_outcomes": int(
                latest.result.get("inserted_outcomes") or 0
            ),
            "data_gap_horizons": int(
                latest.result.get("data_gap_horizons") or 0
            ),
            "last_error_code": latest.last_error_code,
        }
    return OpportunityLearningSummaryResponse.model_validate(result)


@router.post("/option-overview", response_model=OptionOverviewResponse)
def option_overview(payload: OptionOverviewRequest) -> OptionOverviewResponse:
    """Return one batch of time-labelled, quote-only option summary metrics."""

    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    key = _option_overview_cache_key(
        payload.symbols,
        _moomoo_opend_enabled(),
        market_date_et,
    )
    try:
        result = _get_or_compute_scan(
            key,
            lambda: _execute_option_overview(
                payload.symbols,
                enabled=_moomoo_opend_enabled(),
            ),
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return OptionOverviewResponse.model_validate(result)


@router.post("/option-context", response_model=OptionContextResponse)
def option_context(payload: OptionContextRequest) -> OptionContextResponse:
    """Return Quote-only nearest-expiry ATM Call IV context from Moomoo.

    This endpoint does not calculate IV Rank, inspect unusual option flow, infer
    trade direction, place orders, or emit a buy/sell signal.
    """

    symbols = payload.symbols
    enabled = _moomoo_opend_enabled()
    try:
        result = _execute_option_context(symbols, enabled=enabled)
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return OptionContextResponse.model_validate(result)


@router.post("/option-walls", response_model=OptionWallResponse)
def option_walls(payload: OptionWallRequest) -> OptionWallResponse:
    """Return walls plus ATM Call IV derived from the same dynamic snapshot.

    Public open interest does not identify dealer positioning.  Consequently
    this endpoint never labels the unsigned concentration as true dealer GEX,
    never calculates a fake gamma flip, and never invokes a trade API.  The ATM
    field reuses already fetched contracts and does not issue another option
    chain request.
    """

    try:
        result = _execute_option_walls(
            payload.symbols,
            enabled=_moomoo_opend_enabled(),
            dte_min=payload.dte_min,
            dte_max=payload.dte_max,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return OptionWallResponse.model_validate(result)


@router.post(
    "/option-walls/daily-snapshot",
    response_model=OptionWallSnapshotResponse,
)
def option_walls_daily_snapshot(
    payload: OptionWallSnapshotRequest,
) -> OptionWallSnapshotResponse:
    """Append today's wall aggregates for the given deep-lane tickers.

    **显式触发，不是常驻守护进程。**本仓库目前没有一个「盘中每日跑一次」的
    通用调度挂载点适合塞这件事（盘前 orchestration 只覆盖 premarket 周期），
    因此这里按约定暴露为显式端点：由用户、cron 或外部调度每个 ET 交易日调用
    一次即可，重复调用幂等。

    它**复用既有的期权墙车道与其缓存**——同一 ``(symbol, date, dte)`` 键在
    缓存 TTL 内不会产生新的 provider 请求；本端点自身**不新增任何取数路径**，
    也不会扇出到全部 universe：symbols 上限与墙位端点一致（≤5），并由写入层
    再钳一次。抓取失败的标的**不写任何行**（而不是写 0）。
    """

    try:
        result = _execute_option_walls(
            payload.symbols,
            enabled=_moomoo_opend_enabled(),
            dte_min=payload.dte_min,
            dte_max=payload.dte_max,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    try:
        written = record_wall_snapshots(result, tickers=payload.symbols)
    except OptionWallSnapshotError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return OptionWallSnapshotResponse(
        schema_version="option-wall-daily-snapshot/1.0",
        market_date_et=str(result["market_date_et"]),
        generated_at=str(result["generated_at"]),
        results=[
            OptionWallSnapshotResultItem(**asdict(item)) for item in written
        ],
    )


@router.post("/option-events", response_model=OptionEventResponse)
def option_events(payload: OptionEventRequest) -> OptionEventResponse:
    """Return recent Moomoo-classified unusual option transactions.

    The endpoint is Quote-only, requests only the latest bounded page, does not
    infer open/close or dealer direction, and does not alter candidate ranking.
    """

    try:
        result = _execute_option_events(
            payload.symbols,
            enabled=_moomoo_opend_enabled(),
            limit_per_symbol=payload.limit_per_symbol,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return OptionEventResponse.model_validate(result)


@router.post(
    "/near-expiry-contracts",
    response_model=NearExpiryContractResponse,
)
def near_expiry_contracts(
    payload: NearExpiryContractRequest,
) -> NearExpiryContractResponse:
    """Return the read-only near-expiry (0–max_dte) contract panel data.

    合约选择支持，不是推荐引擎：只返回近价窗口内 Call/Put 合约的
    bid/ask/点差/最新价/当日量/T-1 OI/供应商 IV/delta 读数与 as-of，
    按到期日中性分组，不打分、不排序偏好、不生成买卖建议，也不调用
    任何交易接口。缺失字段逐字段显式 null + reason，绝不 0 回填。
    """

    enabled = _moomoo_opend_enabled()
    requested_at = datetime.now(timezone.utc)
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    key = _near_expiry_cache_key(
        payload.symbol,
        enabled,
        market_date_et,
        payload.max_dte,
    )
    try:
        result = _get_or_compute_scan(
            key,
            lambda: _execute_near_expiry_contracts(
                payload.symbol,
                enabled=enabled,
                max_dte=payload.max_dte,
            ),
            bypass_cache=payload.refresh,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return NearExpiryContractResponse.model_validate(result)


@router.post("/intraday-tracking", response_model=IntradayTrackingResponse)
def intraday_tracking(payload: IntradayTrackingRequest) -> IntradayTrackingResponse:
    """Track the live session against the frozen premarket plan, read-only.

    冻结的盘前 Top 5 是唯一对照基准：本接口不重新排序、不生成买卖信号。
    实时字段来自 Moomoo Quote-only 快照；VWAP 是当日累计成交额/成交量近似；
    ATR14 与量能中位数来自与每日榜相同的已完成日线加载器。Moomoo 未启用或
    不可用时逐标的显式 not_configured/unavailable，绝不以 0 冒充实时数据。
    """

    enabled = _moomoo_opend_enabled()
    requested_at = _intraday_now()
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    key = (
        "intraday_tracking",
        INTRADAY_TRACKING_VERSION,
        enabled,
        tuple(payload.symbols),
        market_date_et,
    )
    try:
        result = _get_or_compute_scan(
            key,
            lambda: _execute_intraday_tracking(payload.symbols, enabled=enabled),
            bypass_cache=payload.refresh,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return IntradayTrackingResponse.model_validate(result)


@router.post("/intraday-top", response_model=IntradayTopResponse)
def intraday_top(payload: IntradayTopRequest) -> IntradayTopResponse:
    """Return the rolling intraday Top-N research queue, read-only.

    与「周内 Top 5 · 盘前冻结」互不替代：本接口盘中滚动重排、不冻结版本、
    不写快照/qualification/5D/20D 结果（statistics_track=none_intraday_v1_unscored）。
    v2 盘中主排序信号 = 15 分钟波段爆发（5m K 线滚动推力×量比，逐标的当前
    + 上一交易时段有界读取）；聚合证据（缺口/量能节奏/VWAP/波幅扩张 + 有界
    Moomoo 异动计数）退居次序。异动是供应商分类，不推断开平仓或真实主动
    方向。休市时段仍可读取：排序退回证据计数，但附最近一个交易时段的波段。

    universe 解析：显式 ``symbols``（≤20）优先；空 symbols 时若配置了
    ``INTRADAY_WATCHLIST`` 走两层扫描（宽层批量快照 → 异动闸门 → 深度层，
    响应附 ``universe_scan``），否则回退 ``STOCK_LIST``（与既有行为一致）。
    ``focus_symbols``（≤8，additive）是「盘中计划」手动提升的标的：**不参与
    universe 解析**（因此不会关掉两层扫描），只在两层模式下并入深度层，
    与既有用户钉选同语义（不占异动额度、与计划/钉选去重、并入同一批快照），
    深度位标注 ``promoted_by="user_focus"``；单层路径完全不受影响。
    """

    key, factory = _intraday_top_key_and_factory(
        list(payload.symbols),
        list(payload.focus_symbols),
        payload.limit,
    )
    meta: dict[str, Any] = {}
    try:
        result = _get_or_compute_scan(
            key,
            factory,
            bypass_cache=payload.refresh,
            ttl_seconds=_INTRADAY_TOP_CACHE_TTL_SECONDS,
            lease_seconds=_INTRADAY_TOP_LEASE_SECONDS,
            wait_timeout_seconds=_INTRADAY_TOP_LEASE_SECONDS,
            meta_out=meta,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    _stamp_intraday_top_latency(result, meta)
    return IntradayTopResponse.model_validate(result)


def _intraday_top_key_and_factory(
    symbols_requested: list[str],
    focus_symbols_requested: list[str],
    limit: int,
) -> tuple[tuple[Any, ...], Callable[[], dict[str, Any]]]:
    """Resolve the intraday-top universe into one scan key + factory.

    端点与服务端预热循环共用本函数：预热以空 symbols / 空 focus / 默认
    limit 调用，保证预热的正是页面默认轮询命中的同一个 key（同一工厂、
    同一 TTL、同一诚实字段），绝不产生并行实现。

    两层模式仅在「未显式传 symbols 且 INTRADAY_WATCHLIST 已配置」时启用；
    显式 symbols（≤20）与未配置清单的路径与既有行为逐字节一致。
    """

    enabled = _moomoo_opend_enabled()
    requested_at = _intraday_now()
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()

    watchlist_configured = (
        _configured_intraday_watchlist() if not symbols_requested else []
    )
    if not symbols_requested and watchlist_configured:
        watchlist = watchlist_configured[:_INTRADAY_WATCHLIST_MAX_SYMBOLS]
        deep_lane_max = _configured_deep_lane_max()
        pinned_configured = _configured_intraday_pinned_tickers()
        # 盘中计划提升进入缓存 key：不同 focus 名单是不同的深度层名单，
        # 共用一个 key 会让后提升的标的读到不含它的旧结果。
        focus_requested = focus_symbols_requested[:_INTRADAY_FOCUS_MAX_SYMBOLS]
        key = (
            "intraday_top",
            INTRADAY_TOP_SIGNAL_VERSION,
            enabled,
            (
                "watchlist_two_tier",
                tuple(watchlist),
                tuple(pinned_configured),
                tuple(focus_requested),
                deep_lane_max,
            ),
            int(limit),
            market_date_et,
        )
        factory: Callable[[], dict[str, Any]] = (
            lambda: _execute_intraday_top_two_tier(
                watchlist,
                limit,
                enabled=enabled,
                deep_lane_max=deep_lane_max,
                watchlist_configured_total=len(watchlist_configured),
                pinned_tickers=pinned_configured,
                focus_tickers=focus_requested,
            )
        )
        return key, factory

    symbols = symbols_requested or _configured_symbols()
    symbols = normalize_symbols(symbols)[:20]
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_universe",
                "message": "symbols 为空且服务端 STOCK_LIST 未配置。",
            },
        )
    key = (
        "intraday_top",
        INTRADAY_TOP_SIGNAL_VERSION,
        enabled,
        tuple(symbols),
        int(limit),
        market_date_et,
    )
    factory = lambda: _execute_intraday_top(  # noqa: E731 - mirrors two-tier arm
        symbols, limit, enabled=enabled
    )
    return key, factory


def _stamp_intraday_top_latency(
    result: dict[str, Any],
    meta: dict[str, Any],
) -> None:
    """Attach the additive latency diagnostics to one response copy.

    ``result`` 是 :func:`_get_or_compute_scan` 返回的独立深拷贝——就地打点
    绝不污染共享缓存。语义：

    - ``served_from="fresh"``：本次响应等到了一个工厂运行完成（leader 或
      加入在途计算的 follower）。
    - ``served_from="cache"`` / ``"warm_cache"``：命中完成态 TTL 缓存，
      按条目发起方区分；``generated_in_seconds`` 报告**原始**生成耗时。
    - meta 为空（例如上游被测试替换）时两个字段保持缺席——additive 合同。
    """

    if not meta:
        return
    generated = meta.get("generated_in_seconds")
    result["generated_in_seconds"] = (
        None if generated is None else round(float(generated), 3)
    )
    if meta.get("source") == "cache":
        result["served_from"] = (
            "warm_cache"
            if meta.get("generated_by") == "warm_scheduler"
            else "cache"
        )
    else:
        result["served_from"] = "fresh"


def warm_default_intraday_top_scan(
    payload_observer: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Warm the page's default intraday-top poll through the same single flight.

    服务端预热循环（`IntradayWarmCacheScheduler`）每 tick 调用一次：以
    「无显式 symbols、无 focus、默认 limit」解析出与页面默认轮询完全相同
    的 key/工厂，并以 ``bypass_cache=True`` 走 :func:`_get_or_compute_scan`
    ——与用户点「刷新」同语义：只绕过已完成 TTL 条目，仍然加入同 key 的
    在途请求（join-not-duplicate，绝不并发第二个工厂）。返回小结摘要，
    载荷本身只通过共享缓存被后续请求读取。

    ``payload_observer``（盘中机会提示器专用）：预热成功后把本次载荷的
    **独立深拷贝**交给观察者做纯内存的事实检测——零新增取数，绝不改写
    缓存。观察者按合同永不抛出，这里仍兜一层：预热节奏永远优先。
    """

    key, factory = _intraday_top_key_and_factory(
        [], [], _INTRADAY_TOP_DEFAULT_LIMIT
    )
    meta: dict[str, Any] = {}
    result = _get_or_compute_scan(
        key,
        factory,
        bypass_cache=True,
        ttl_seconds=_INTRADAY_TOP_CACHE_TTL_SECONDS,
        lease_seconds=_INTRADAY_TOP_LEASE_SECONDS,
        wait_timeout_seconds=_INTRADAY_TOP_LEASE_SECONDS,
        initiator="warm_scheduler",
        meta_out=meta,
    )
    if payload_observer is not None:
        try:
            payload_observer(result)
        except Exception:  # noqa: BLE001 - alert detection must never break warming
            logger.debug(
                "[opportunities] intraday alert observer failed", exc_info=True
            )
    return {
        "led_flight": bool(meta.get("led_flight")),
        "generated_in_seconds": meta.get("generated_in_seconds"),
        "generated_by": meta.get("generated_by"),
    }


@router.get("/intraday-pulse", response_model=IntradayPulseResponse)
def intraday_pulse() -> IntradayPulseResponse:
    """Return the SPY/QQQ/VIX market pulse snapshot, read-only.

    VIX 请求与 SPY/QQQ 隔离；供应商不可得时逐代码显式标缺。
    """

    enabled = _moomoo_opend_enabled()
    requested_at = _intraday_now()
    market_date_et = requested_at.astimezone(_NEW_YORK).date().isoformat()
    key = ("intraday_pulse", _INTRADAY_PULSE_SCHEMA, enabled, market_date_et)
    try:
        result = _get_or_compute_scan(
            key,
            lambda: _execute_intraday_pulse(enabled=enabled),
            ttl_seconds=_INTRADAY_PULSE_CACHE_TTL_SECONDS,
        )
    except OpportunityScanTimeoutError as exc:
        raise _scan_timeout_response(exc) from exc
    return IntradayPulseResponse.model_validate(result)

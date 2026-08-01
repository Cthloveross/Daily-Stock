import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  fetchDailyOpportunities,
  fetchPremarketCycleStatus,
  evaluateOpportunitySnapshot,
  fetchOpportunityLearningSummary,
  fetchOpportunityOptionEvents,
  fetchOpportunityOptionOverview,
  fetchOpportunityOptionWalls,
  fetchOpportunitySnapshots,
  normalizeSupportedUsOptionUnderlying,
  runPremarketCycle,
} from '../../api/opportunities';
import type {
  DailyOpportunityRun,
  OpportunityCandidate,
  OpportunityEvidence,
  OpportunityLearningHorizon,
  OpportunityLearningSummaryResponse,
  OpportunityOptionContextItem,
  OpportunityOptionEvent,
  OpportunityOptionEventItem,
  OpportunityOptionOverviewItem,
  OpportunityOptionWallItem,
  OpportunityOptionWallLevel,
  OpportunityOutcomeProgress,
  OpportunityReadinessState,
  OpportunityResearchState,
  OpportunitySnapshot,
  PremarketCycleResponse,
  PremarketCycleState,
} from '../../types/opportunities';
import { Button, EmptyState } from '../ui';
import { parseApiTimestamp } from '../../utils/marketTime';

const STATE_LABELS: Record<OpportunityResearchState, string> = {
  research_ready: '基础门禁通过',
  watch_only: '基础候选 · 非信号',
  context_only: '仅作背景',
  blocked: '数据阻断',
};

const STATE_TEXT_STYLES: Record<OpportunityResearchState, string> = {
  research_ready: 'text-text-1',
  watch_only: 'text-text-2',
  context_only: 'text-text-3',
  blocked: 'text-warning',
};

const STATE_DOT_STYLES: Record<OpportunityResearchState, string> = {
  research_ready: 'bg-[color:var(--text-1)]',
  watch_only: 'bg-[color:var(--text-2)]',
  context_only: 'bg-[color:var(--text-3)]',
  blocked: 'bg-[color:var(--warn-strong)]',
};

const READINESS_LABELS: Record<OpportunityReadinessState, string> = {
  ready: '可用',
  partial: '部分',
  stale: '过期',
  background_only: '仅背景',
  not_configured: '未配置',
  unavailable: '不可用',
};

const DOMAIN_LABELS: Record<string, string> = {
  daily_history: '完整日线',
  regime: 'Regime',
  options_flow: '期权流（未入榜）',
  unusual_options_flow: '期权流（未入榜）',
  dark_pool: '场外 / 暗池背景',
  off_exchange_prints: '场外 / TRF 大额成交',
  underlying_daily: '标的日线',
  underlying_activity: '标的活跃度',
  technical_structure: '技术结构',
  market_regime: '市场 Regime',
  universe: '研究范围',
};

const METRIC_LABELS: Record<string, string> = {
  last_completed_close: '上一完整日收盘',
  completed_day_return: '上一完整日涨跌',
  ema8_ema13_alignment: 'EMA8 / EMA13 结构',
  prior_20d_range_position: '相对前 20 日区间',
  volume_vs_prior_20d_median: '成交量 / 前 20 日中位',
  dollar_volume_vs_prior_20d_median: '成交额 / 前 20 日中位',
  stored_regime: '当日已保存 Regime',
  close_above_ema8_above_ema13: '收盘 > EMA8 > EMA13',
  close_below_ema8_below_ema13: '收盘 < EMA8 < EMA13',
  daily_close_above_prior_20d_high: '突破前 20 日高',
  daily_close_below_prior_20d_low: '跌破前 20 日低',
  dollar_volume_above_prior_20d_median: '成交额放大',
  volume_above_prior_20d_median: '成交量放大',
  context: '方向',
  close: '收盘',
  ema8: 'EMA8',
  ema13: 'EMA13',
  priorHigh: '前 20 日高',
  priorLow: '前 20 日低',
  derived: '派生',
  observed: '直接观测',
  reported: '数据源上报',
  estimated: '估算',
  mixed: '混合',
  missing: '缺失',
};

const DIRECTION_LABELS: Record<string, string> = {
  bullish: '看多结构',
  bearish: '看空结构',
  mixed: '方向混合',
  neutral: '方向中性',
};

const SNAPSHOT_ELIGIBILITY_LABELS: Record<string, string> = {
  not_frozen_before_entry_open: '在美股开盘后冻结，仅保存研究记录，不纳入正式统计',
  signal_session_not_complete_at_freeze: '参考交易日尚未完成',
  missing_reference_session: '缺少冻结参考交易日',
  missing_reference_close: '缺少冻结参考收盘价',
  missing_reference_source: '缺少冻结行情来源',
  candidate_blocked: '候选在冻结时未通过基础数据门禁',
  no_candidates: '本次没有可冻结候选',
};

const REGIME_QUALITY_DOMAIN_LABELS: Record<string, string> = {
  spy: 'SPY 趋势',
  vix: 'VIX',
  events: '宏观事件',
  sectors: '板块广度',
  prev_day: '昨日结构',
  premarket: '盘前行情',
};

const QUALITY_REASON_STATE_LABELS: Record<string, string> = {
  degraded: '降级',
  unavailable: '不可用',
  partial: '部分可用',
  stale: '已过期',
};

function formatAnalysisQualityReason(reason: string): string {
  if (reason === 'analysis_quality_not_eligible') return '完整研究数据质量未达到统计口径';
  if (reason === 'supporting_regime_partial') return '市场背景支持证据部分可用';
  const supportingMatch = reason.match(/^regime_supporting_(.+)_(degraded|unavailable|partial|stale)$/);
  if (supportingMatch) {
    const [, domain, state] = supportingMatch;
    return `${REGIME_QUALITY_DOMAIN_LABELS[domain] ?? domain.replaceAll('_', ' ')}${QUALITY_REASON_STATE_LABELS[state] ?? state}`;
  }
  return reason.replaceAll('_', ' ');
}

function formatSnapshotEligibilityReason(reason: string): string {
  if (SNAPSHOT_ELIGIBILITY_LABELS[reason]) return SNAPSHOT_ELIGIBILITY_LABELS[reason];
  if (reason.startsWith('calendar_contract_unavailable:')) return '美股交易日历暂不可用，已停止计入统计';
  return formatAnalysisQualityReason(reason);
}

const CONTEXT_LABELS: Record<string, string> = {
  ...DIRECTION_LABELS,
  inside_range: '前 20 日区间内',
  breakout: '突破前 20 日高',
  breakdown: '跌破前 20 日低',
  unknown: '未知',
};

const GATE_LABELS: Record<string, string> = {
  us_options_universe: '标的不在支持范围',
  completed_daily_history: '完整日线不足',
  latest_completed_daily_bar_freshness: '日线时效不合格',
  current_regime_available: '缺少当日 Regime',
  regime_allows_new_risk: '市场环境未放行',
  confirmed_playbook_available: '个人 Playbook 未确认',
};

const SOURCE_LABELS: Record<string, string> = {
  YfinanceFetcher: 'yfinance',
  MoomooFetcher: 'Moomoo',
};

type OptionContextDisplayState = 'loading' | 'settled' | 'unavailable' | 'not_scanned';

interface CandidateOptionContext {
  item?: OpportunityOptionContextItem;
  state: OptionContextDisplayState;
  message?: string | null;
}

type OptionWallDisplayState = 'loading' | 'settled' | 'unavailable' | 'unsupported';

interface CandidateOptionWallContext {
  item?: OpportunityOptionWallItem;
  state: OptionWallDisplayState;
  message?: string | null;
}

function optionContextFromWall(
  wallContext: CandidateOptionWallContext,
): CandidateOptionContext {
  if (wallContext.state === 'loading') {
    return { state: 'loading', message: null };
  }
  if (wallContext.state === 'unsupported') {
    return {
      state: 'unavailable',
      message: '当前标的不在支持的美股期权 underlying 范围内。',
    };
  }
  if (wallContext.state === 'unavailable' || !wallContext.item) {
    return {
      state: 'unavailable',
      message: wallContext.message || '同批期权墙快照不可用。',
    };
  }

  const wall = wallContext.item;
  const atm = wall.atmCallIv;
  if (!atm) {
    return {
      state: 'unavailable',
      message: '期权墙响应尚未包含同批 ATM Call IV；请刷新后重试。',
    };
  }
  const ready = atm.state === 'ready'
    && typeof atm.atmCallIvPercent === 'number'
    && Boolean(atm.expiry);
  return {
    state: 'settled',
    item: {
      ticker: wall.ticker,
      state: ready ? 'ready' : atm.state,
      source: `${wall.source} · 同批期权墙快照`,
      fetchedAt: wall.fetchedAt,
      expiry: atm.expiry,
      atmCallIvPercent: ready ? atm.atmCallIvPercent : null,
      message: ready
        ? `执行价 ${formatNumber(atm.strike)}；复用 Top 5 墙同批动态快照，未再次请求期权链。`
        : atm.state === 'not_configured'
          ? 'Moomoo 期权墙未配置；未请求 ATM Call IV。'
          : '同批期权墙快照未返回有效的最近到期 ATM Call IV；未使用其他来源回填。',
      limitations: [
        '仅为最近到期、最接近现价的 Call 单点隐含波动率。',
        '不是 IV Rank、异常期权流或买卖信号。',
      ],
    },
    message: null,
  };
}

type OptionEventDisplayState = 'loading' | 'settled' | 'unavailable' | 'unsupported';

interface CandidateOptionEventContext {
  item?: OpportunityOptionEventItem;
  state: OptionEventDisplayState;
  message?: string | null;
}

type OptionWallScopePreset = '0-7' | '8-45' | '0-45';

type ResearchStageState = 'pending' | 'loading' | 'ready' | 'degraded' | 'failed';
type OpportunityBaselineSource = 'canonical' | 'preview';
type PremarketActivity = 'status' | 'run' | 'preview' | null;
type PremarketLoadIntent = 'initial' | 'refresh' | 'run' | 'poll';
const MAX_PREMARKET_STATUS_POLLS = 12;
const PREVIEW_SCHEDULE_GUARD_MS = 60_000;

const RESEARCH_STAGE_LABELS: Record<ResearchStageState, string> = {
  pending: '等待',
  loading: '加载中',
  ready: '就绪',
  degraded: '降级',
  failed: '阻断',
};

const PREMARKET_STATE_LABELS: Record<PremarketCycleState, string> = {
  non_session: '非交易日',
  waiting_window: '等待窗口',
  ready_to_run: '可生成',
  research_pool_missing: '研究池未保存',
  running: '生成中',
  published: '已发布',
  blocked: '质量门禁阻断',
  window_closed: '窗口已关闭',
  failed: '生成失败',
};

const PREMARKET_STAGE_LABELS: Record<string, string> = {
  resolve_window: '解析窗口',
  compute_regime: '计算 Regime',
  scan_completed_bars: '扫描 T-1 日线',
  quality_gate: '质量门禁',
  persist_snapshot: '保存快照',
};

const PREMARKET_STAGE_STATE_LABELS: Record<string, string> = {
  pending: '等待',
  running: '运行中',
  completed: '完成',
  degraded: '降级',
  blocked: '阻断',
  failed: '失败',
  skipped: '跳过',
};

const OPTION_WALL_SCOPES: Record<OptionWallScopePreset, { label: string; dteMin: number; dteMax: number }> = {
  '0-7': { label: '0–7', dteMin: 0, dteMax: 7 },
  '8-45': { label: '8–45', dteMin: 8, dteMax: 45 },
  '0-45': { label: '0–45', dteMin: 0, dteMax: 45 },
};

function formatMetric(raw: string): string {
  return METRIC_LABELS[raw] ?? DOMAIN_LABELS[raw] ?? raw.replaceAll('_', ' ');
}

function readinessLabel(
  state: OpportunityReadinessState,
  actionability?: string,
): string {
  if (actionability === 'not_available') {
    return state === 'ready' ? '已接入' : '未纳入排序';
  }
  if (actionability === 'background_only') {
    return state === 'ready' ? '背景可用' : '可选背景';
  }
  return READINESS_LABELS[state];
}

function candidateStateLabel(candidate: OpportunityCandidate): string {
  const failedGateIds = new Set(
    candidate.hardGates
      .filter((gate) => gate.status === 'failed')
      .map((gate) => gate.gateId),
  );
  if (candidate.researchState === 'blocked') {
    if (failedGateIds.has('us_options_universe')) return '标的不支持';
    if (failedGateIds.has('completed_daily_history')) {
      const history = candidate.readiness.find((item) => item.domain === 'daily_history');
      return history?.state === 'unavailable' ? '行情源暂不可用' : '日线不足';
    }
    if (failedGateIds.has('latest_completed_daily_bar_freshness')) return '日线已过期';
    return '数据阻断';
  }
  if (
    candidate.researchState === 'context_only'
    && failedGateIds.has('regime_allows_new_risk')
  ) {
    return '市场风险关闭';
  }
  return STATE_LABELS[candidate.researchState];
}

function hasUsableBaseCandidate(run: DailyOpportunityRun): boolean {
  return run.candidates.some((candidate) => {
    const history = candidate.readiness.find((item) => item.domain === 'daily_history');
    return candidate.researchState !== 'blocked'
      && (history?.state === 'ready' || history?.state === 'stale');
  });
}

function hasSameUniverse(left: DailyOpportunityRun, right: DailyOpportunityRun): boolean {
  const normalize = (values: string[]) => values.map((value) => value.trim().toUpperCase()).sort();
  return JSON.stringify(normalize(left.universe)) === JSON.stringify(normalize(right.universe));
}

function isRepeatedGlobalUnknown(message: string): boolean {
  return /Playbook|逐笔期权流|NBBO|FINRA ATS|暗池/.test(message);
}

function formatValue(item: OpportunityEvidence): string {
  const value = item.value;
  if (value === null || value === undefined || value === '') return '未知';
  if (typeof value === 'number') {
    const formatted = Math.abs(value) >= 1_000_000
      ? value.toLocaleString('en-US', { maximumFractionDigits: 0 })
      : value.toLocaleString('en-US', { maximumFractionDigits: 2 });
    const unit = item.unit === 'percent'
      ? '%'
      : item.unit === 'ratio'
        ? '×'
        : item.unit === 'price'
          ? ''
          : item.unit;
    return `${formatted}${unit ? ` ${unit}` : ''}`;
  }
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, nested]) => nested !== null && nested !== undefined)
      .map(([key, nested]) => {
        const shown = typeof nested === 'number'
          ? nested.toLocaleString('en-US', { maximumFractionDigits: 2 })
          : key === 'context'
            ? CONTEXT_LABELS[String(nested)] ?? String(nested)
            : String(nested);
        return `${formatMetric(key)} ${shown}`;
      })
      .join(' · ');
  }
  return `${String(value)}${item.unit ? ` ${item.unit}` : ''}`;
}

function formatCompletedBar(raw?: string | null): string {
  if (!raw) return '未知';
  return raw.slice(0, 10) || raw;
}

function formatEtDateTime(raw?: string | null): string {
  const parsed = parseApiTimestamp(raw);
  if (!parsed) return '未报告';
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(parsed);
  const get = (type: Intl.DateTimeFormatPartTypes) => (
    parts.find((part) => part.type === type)?.value ?? ''
  );
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}

function formatEtWindow(start?: string | null, end?: string | null): string {
  if (!start || !end) return '未报告';
  return `${formatEtDateTime(start)}–${formatEtDateTime(end).slice(11)} ET`;
}

function canStartManualPremarketRun(cycle: PremarketCycleResponse): boolean {
  if (!['ready_to_run', 'blocked', 'failed'].includes(cycle.state)) return false;
  const now = Date.now();
  const primaryAt = parseApiTimestamp(cycle.primaryScheduledAt)?.getTime();
  const latestStartAt = parseApiTimestamp(cycle.latestStartAt)?.getTime();
  if (primaryAt === undefined || now < primaryAt) return false;
  if (latestStartAt !== undefined && now >= latestStartAt) return false;
  if (cycle.state === 'ready_to_run') return true;
  if (!cycle.recoverable) return false;
  const nextScheduledAt = parseApiTimestamp(cycle.nextScheduledAt)?.getTime();
  return nextScheduledAt === undefined || now >= nextScheduledAt;
}

function hasImminentPremarketProviderWork(cycle: PremarketCycleResponse): boolean {
  if (['ready_to_run', 'running'].includes(cycle.state)) return true;
  const nextScheduledAt = parseApiTimestamp(cycle.nextScheduledAt)?.getTime();
  if (nextScheduledAt === undefined) return false;
  const delay = nextScheduledAt - Date.now();
  return delay >= 0 && delay <= PREVIEW_SCHEDULE_GUARD_MS;
}

function completedEvidenceDateRange(run: DailyOpportunityRun): string {
  const dates = [...new Set(run.candidates
    .map((candidate) => (
      candidate.referenceSessionDate || formatCompletedBar(candidate.lastCompletedBarAt)
    ))
    .filter((value) => value && value !== '未知'))].sort();
  if (dates.length === 0) return '未报告';
  if (dates.length === 1) return dates[0];
  return `${dates[0]}–${dates[dates.length - 1]}（混合）`;
}

function formatIvPercent(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function getEvidence(candidate: OpportunityCandidate, metric: string): OpportunityEvidence | undefined {
  return candidate.evidence.find((item) => item.metric === metric);
}

function getNumericEvidence(candidate: OpportunityCandidate, metric: string): number | null {
  const value = getEvidence(candidate, metric)?.value;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function getObjectEvidence(
  candidate: OpportunityCandidate,
  metric: string,
): Record<string, unknown> | null {
  const value = getEvidence(candidate, metric)?.value;
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function formatNumber(value: number | null, suffix = ''): string {
  if (value === null) return '—';
  return `${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}${suffix}`;
}

function setupSummary(candidate: OpportunityCandidate): string {
  const range = getObjectEvidence(candidate, 'prior_20d_range_position');
  const context = range?.context;
  if (typeof context === 'string' && CONTEXT_LABELS[context]) return CONTEXT_LABELS[context];
  const firstTag = candidate.setupTags[0];
  return firstTag ? formatMetric(firstTag) : '结构待确认';
}

function trendSummary(candidate: OpportunityCandidate): string {
  const ema = getObjectEvidence(candidate, 'ema8_ema13_alignment');
  const context = ema?.context;
  return typeof context === 'string'
    ? DIRECTION_LABELS[context] ?? context
    : DIRECTION_LABELS[candidate.directionalContext] ?? candidate.directionalContext ?? '方向待确认';
}

function formatResearchLevel(value: unknown): string | null {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-US', { maximumFractionDigits: 2 })
    : null;
}

function candidateResearchPlan(candidate: OpportunityCandidate): {
  confirmation: string;
  invalidation: string;
} {
  const range = getObjectEvidence(candidate, 'prior_20d_range_position');
  const ema = getObjectEvidence(candidate, 'ema8_ema13_alignment');
  const priorHigh = formatResearchLevel(range?.priorHigh);
  const priorLow = formatResearchLevel(range?.priorLow);
  const ema8 = formatResearchLevel(ema?.ema8);
  const ema13 = formatResearchLevel(ema?.ema13);
  const rangeContext = typeof range?.context === 'string' ? range.context : 'unknown';
  const direction = candidate.directionalContext;

  if (direction === 'bullish') {
    const confirmation = priorHigh
      ? `${rangeContext === 'breakout' ? '守住' : '突破'} 20日高 ${priorHigh}，EMA8 > EMA13`
      : '价格结构转强，EMA8 > EMA13';
    const invalidation = priorHigh
      ? `收盘跌回 ${priorHigh} 下方或 EMA8 下穿 EMA13`
      : `收盘跌破 EMA13${ema13 ? ` ${ema13}` : ''} 或均线转空`;
    return { confirmation, invalidation };
  }
  if (direction === 'bearish') {
    const confirmation = priorLow
      ? `${rangeContext === 'breakdown' ? '维持' : '跌破'} 20日低 ${priorLow}，EMA8 < EMA13`
      : '价格结构转弱，EMA8 < EMA13';
    const invalidation = priorLow
      ? `收盘重回 ${priorLow} 上方或 EMA8 上穿 EMA13`
      : `收盘站回 EMA13${ema13 ? ` ${ema13}` : ''} 或均线转多`;
    return { confirmation, invalidation };
  }
  return {
    confirmation: priorHigh && priorLow
      ? `脱离 ${priorLow}–${priorHigh} 区间，且 EMA8/13 同向`
      : '价格脱离整理区间，且 EMA8/13 同向',
    invalidation: ema8 && ema13
      ? `价格重回区间或 EMA8/13（${ema8}/${ema13}）再次交叉`
      : '价格重回区间或 EMA8/13 再次交叉',
  };
}

function optionOverviewValue(
  item: OpportunityOptionOverviewItem | undefined,
  state: 'loading' | 'settled' | 'unavailable',
  field: 'iv' | 'rank',
): string {
  if (state === 'loading') return '加载中…';
  if (state === 'unavailable' || !item || item.state !== 'ready') return '—';
  const value = field === 'iv' ? item.ivPercent : item.ivRankPercent;
  return typeof value === 'number' ? `${formatIvPercent(value)}%` : '—';
}

function optionOverviewOiLabel(
  item: OpportunityOptionOverviewItem | undefined,
  state: 'loading' | 'settled' | 'unavailable',
): string {
  if (state === 'loading') return '加载中…';
  if (state === 'unavailable' || !item || item.state !== 'ready') return '—';
  const compact = (value: number | null) => {
    if (value === null || !Number.isFinite(value)) return '—';
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
    return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
  };
  return `C ${compact(item.callOpenInterest)} / P ${compact(item.putOpenInterest)}`;
}

function formatDistance(value: number): string {
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 2 })}%`;
}

function formatCompactMetric(level: OpportunityOptionWallLevel): string {
  const value = level.metricValue;
  if (level.unit === 'usd_delta_change_per_1pct_move') {
    if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B / 1%`;
    if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M / 1%`;
    if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K / 1%`;
    return `$${value.toFixed(0)} / 1%`;
  }
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function optionWallTableLabel(context: CandidateOptionWallContext): string {
  if (context.state === 'unsupported') return '不支持';
  if (context.state === 'loading') return '加载中…';
  if (context.state === 'unavailable' || !context.item) return '不可用';
  if (context.item.state === 'not_configured') return '未配置';
  if (context.item.state === 'unavailable') return '不可用';
  const call = context.item.walls.callOi[0];
  const put = context.item.walls.putOi[0];
  const coveragePrefix = context.item.state === 'partial' ? '部分覆盖 · ' : '';
  if (!call && !put) return `${coveragePrefix}无有效集中位`;
  return `${coveragePrefix}${call ? `C ${call.strike}` : 'C —'} / ${put ? `P ${put.strike}` : 'P —'}`;
}

function formatOptionWallRequestError(error: unknown): string {
  const message = error instanceof Error ? error.message : '';
  if (/method not allowed/i.test(message)) {
    return '期权墙接口尚未在当前服务进程中启用，请重启后端服务后重试。';
  }
  return message || '期权墙暂不可用，不影响基础候选与 ATM IV。';
}

function formatOptionEventRequestError(error: unknown): string {
  const message = error instanceof Error ? error.message : '';
  if (/method not allowed/i.test(message)) {
    return '异常期权成交接口尚未在当前服务进程中启用，请重启后端服务后重试。';
  }
  return message || '异常期权成交暂不可用，不影响基础候选与期权墙。';
}

function formatEventTime(value: string | null): string {
  if (!value) return '—';
  const normalized = value.replace('T', ' ');
  const match = normalized.match(/(\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
  if (match) return `${match[1]} ${match[2]}`;
  return normalized.replace(/(?:Z|[+-]\d{2}:?\d{2})$/, '').slice(0, 19);
}

function formatCompactCurrency(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (absolute >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (absolute >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return `$${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function formatInteger(value: number | null): string {
  return value === null || !Number.isFinite(value)
    ? '—'
    : value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function optionTypeLabel(value: string | null): string {
  const normalized = value?.toUpperCase();
  if (normalized === 'CALL') return 'Call';
  if (normalized === 'PUT') return 'Put';
  return value || '类型未知';
}

function eventDirectionLabel(value: string | null): string {
  const normalized = value?.toUpperCase();
  if (normalized === 'BUY') return '买方分类';
  if (normalized === 'SELL') return '卖方分类';
  if (normalized === 'NEUTRAL') return '中性分类';
  return value || '未分类';
}

function eventSentimentLabel(value: string | null): string {
  const normalized = value?.toUpperCase();
  if (normalized === 'BULLISH') return '偏多标签';
  if (normalized === 'BEARISH') return '偏空标签';
  if (normalized === 'NEUTRAL') return '中性标签';
  return value || '情绪未知';
}

function eventOrderTypeLabel(value: string): string {
  const normalized = value.toUpperCase();
  if (normalized === 'NORMAL') return '普通';
  if (normalized === 'SWEEP') return 'Sweep';
  if (normalized === 'CROSS') return 'Cross';
  if (normalized === 'FLOOR') return 'Floor';
  return value;
}

function eventStrategyLabel(value: string | null): string {
  const normalized = value?.toUpperCase();
  if (normalized === 'SINGLE_LEG') return '单腿';
  if (normalized === 'MULTI_LEG') return '多腿';
  return value || '策略未知';
}

function StateMark({ candidate }: { candidate: OpportunityCandidate }) {
  const state = candidate.researchState;
  return (
    <span className={`inline-flex items-center gap-1.5 text-caption ${STATE_TEXT_STYLES[state]}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${STATE_DOT_STYLES[state]}`} />
      {candidateStateLabel(candidate)}
    </span>
  );
}

function ReadinessMark({
  state,
  actionability,
}: {
  state: OpportunityReadinessState;
  actionability?: string;
}) {
  const tone = state === 'ready'
    ? 'text-text-2'
    : state === 'partial' || state === 'background_only'
      ? 'text-warning'
      : 'text-text-3';
  const dot = state === 'ready'
    ? 'bg-[color:var(--text-2)]'
    : state === 'partial' || state === 'background_only'
      ? 'bg-[color:var(--warn-strong)]'
      : 'bg-[color:var(--text-4)]';
  return (
    <span className={`inline-flex items-center gap-1.5 text-caption ${tone}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {readinessLabel(state, actionability)}
    </span>
  );
}

function OptionContextSnapshot({
  item,
  state,
  fallbackMessage,
}: {
  item?: OpportunityOptionContextItem;
  state: OptionContextDisplayState;
  fallbackMessage?: string | null;
}) {
  if (state === 'not_scanned') {
    return <div className="text-body-sm text-text-3">ATM IV 快照 · 首批未扫描</div>;
  }
  if (state === 'loading') {
    return <div className="text-body-sm text-text-3">ATM IV 快照 · 加载中…</div>;
  }
  if (state === 'unavailable' || !item) {
    return (
      <div className="text-body-sm text-text-3">
        ATM IV 快照 · 不可用 · {fallbackMessage || '接口未返回该标的的期权上下文。'}
      </div>
    );
  }

  if (item.state === 'ready' && typeof item.atmCallIvPercent === 'number' && item.expiry) {
    return (
      <div
        className="space-y-1 text-body-sm text-text-2"
        aria-label={`ATM IV 快照，可用：最近到期 ${item.expiry}，ATM Call IV ${formatIvPercent(item.atmCallIvPercent)}%`}
      >
        <div className="font-mono text-mono-sm text-text-1">
          最近到期 {item.expiry} · ATM Call IV {formatIvPercent(item.atmCallIvPercent)}%
        </div>
        <div className="text-caption text-text-3">
          {item.source ? `来源 ${item.source}` : '来源未报告'}
          {item.fetchedAt ? ` · 抓取 ${item.fetchedAt}` : ''}
        </div>
        {item.message && <div className="text-caption text-text-3">{item.message}</div>}
        <div className="text-caption text-text-3">
          单点快照；不代表历史分位、异常成交或方向判断。
        </div>
      </div>
    );
  }

  const stateLabel = item.state === 'not_configured' ? '未配置' : '不可用';
  const message = item.state === 'ready'
    ? '返回的快照字段不完整。'
    : item.message || '当前没有可用的期权上下文。';
  return (
    <div
      className="text-body-sm text-text-3"
      aria-label={`ATM IV 快照，${stateLabel}：${message}`}
    >
      ATM IV 快照 · {stateLabel} · {message}
    </div>
  );
}

function WallLevelList({
  title,
  levels,
  emptyMessage,
}: {
  title: string;
  levels: OpportunityOptionWallLevel[];
  emptyMessage: string;
}) {
  return (
    <div>
      <div className="grid grid-cols-[minmax(0,1fr)_64px_78px] gap-2 border-b border-subtle pb-1 text-caption text-text-3">
        <span>{title}</span>
        <span className="text-right">距现价</span>
        <span className="text-right">规模</span>
      </div>
      {levels.length > 0 ? (
        <ol className="divide-y divide-subtle">
          {levels.slice(0, 3).map((level) => (
            <li
              key={`${level.method}-${level.rank}-${level.strike}`}
              className="grid grid-cols-[minmax(0,1fr)_64px_78px] gap-2 py-1.5 text-caption"
            >
              <span className="font-mono text-mono-xs text-text-1">
                #{level.rank} · {level.strike.toLocaleString('en-US', { maximumFractionDigits: 2 })}
              </span>
              <span className="text-right font-mono text-mono-xs text-text-2">
                {formatDistance(level.distanceFromSpotPercent)}
              </span>
              <span className="text-right font-mono text-mono-xs text-text-2">
                {formatCompactMetric(level)}
              </span>
              <span className="col-span-3 text-right text-[11px] text-text-3">
                该 DTE 桶占比 {level.shareOfBucketPercent.toLocaleString('en-US', { maximumFractionDigits: 1 })}%
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <div className="py-2 text-caption text-text-3">{emptyMessage}</div>
      )}
    </div>
  );
}

function OptionWallSnapshot({ context }: { context: CandidateOptionWallContext }) {
  if (context.state === 'unsupported') {
    return <p className="text-body-sm text-text-3">当前标的格式不在美股期权墙研究范围内。</p>;
  }
  if (context.state === 'loading') {
    return <p className="text-body-sm text-text-3">期权墙 · 加载中…</p>;
  }
  if (context.state === 'unavailable' || !context.item) {
    return (
      <p className="text-body-sm text-text-3">
        期权墙 · 不可用 · {context.message || '接口未返回该标的的期权墙上下文。'}
      </p>
    );
  }

  const item = context.item;
  if (item.state === 'not_configured' || item.state === 'unavailable') {
    const stateLabel = item.state === 'not_configured' ? '未配置' : '不可用';
    return (
      <div
        className="space-y-1 text-body-sm text-text-3"
        aria-label={`期权墙，${stateLabel}：${item.message}`}
      >
        <div>期权墙 · {stateLabel} · {item.message}</div>
        <div className="text-caption">
          未取得真实按执行价数据时，不会用 ATM IV 推断 Call Wall 或 Put Wall。
        </div>
      </div>
    );
  }

  return (
    <div
      className="space-y-3"
      aria-label={`${item.ticker} 期权墙，${item.state === 'partial' ? '部分覆盖' : '可用'}`}
    >
      {item.state === 'partial' && (
        <div className="border-l-2 border-[color:var(--warn-muted)] pl-3 text-caption text-warning">
          期权墙部分覆盖 · 有效合约 {item.coverage.validContracts}/{item.coverage.requestedContracts}
          {' · '}{item.coverage.coveragePercent.toFixed(1)}%
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-caption">
        <div className="text-text-3">标的现价</div>
        <div className="text-right font-mono text-mono-xs text-text-1">
          {item.spot === null ? '—' : item.spot.toLocaleString('en-US', { maximumFractionDigits: 2 })}
        </div>
        <div className="text-text-3">范围</div>
        <div className="text-right font-mono text-mono-xs text-text-2">
          DTE {item.scope.dteMin}–{item.scope.dteMax} · {item.scope.expiries.length} 个到期日
        </div>
        <div className="text-text-3">合约覆盖</div>
        <div className="text-right font-mono text-mono-xs text-text-2">
          {item.coverage.validContracts}/{item.coverage.requestedContracts} · {item.coverage.coveragePercent.toFixed(1)}%
        </div>
      </div>

      <WallLevelList
        title="Call OI 墙（集中位）"
        levels={item.walls.callOi}
        emptyMessage="没有有效的 Call OI 集中位。"
      />
      <WallLevelList
        title="Put OI 墙（集中位）"
        levels={item.walls.putOi}
        emptyMessage="没有有效的 Put OI 集中位。"
      />
      <WallLevelList
        title="Gross Gamma 集中位"
        levels={item.walls.grossGammaConcentration}
        emptyMessage="Gamma 覆盖不足，未生成集中位。"
      />

      <details className="border-t border-subtle pt-2">
        <summary className="cursor-pointer text-caption text-text-2 hover:text-text-1">
          查看当日成交量集中位
        </summary>
        <div className="mt-2 space-y-3">
          <WallLevelList
            title="Call Volume 集中位"
            levels={item.walls.callVolume}
            emptyMessage="没有有效的 Call 当日成交量集中位。"
          />
          <WallLevelList
            title="Put Volume 集中位"
            levels={item.walls.putVolume}
            emptyMessage="没有有效的 Put 当日成交量集中位。"
          />
        </div>
      </details>

      <div className="border-t border-subtle pt-2 text-caption text-text-3">
        <div>
          来源 {item.source} · 报价 {item.quoteAsOf || item.fetchedAt} · 公式 {item.formulaVersion}
        </div>
        <div className="mt-1">
          OI / Volume 不提供成交方向；Gross Gamma 仅为无符号风险集中度，不是真实 Dealer GEX，也不计算 Gamma Flip。
        </div>
      </div>

      {(item.assumptions.length > 0 || item.limitations.length > 0) && (
        <details>
          <summary className="cursor-pointer text-caption text-text-2 hover:text-text-1">方法与限制</summary>
          <ul className="mt-2 space-y-1 text-caption text-text-3">
            {[...item.assumptions, ...item.limitations].map((line) => <li key={line}>· {line}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function OptionEventRow({ event }: { event: OpportunityOptionEvent }) {
  const eventTypes = [
    ...event.orderTypes.map(eventOrderTypeLabel),
    eventStrategyLabel(event.strategyType),
  ];
  return (
    <li className="min-w-0 py-2.5" aria-label={`${event.optionCode} 异常期权成交`}>
      <div className="grid min-w-0 grid-cols-[94px_minmax(0,1fr)_auto] items-baseline gap-2">
        <span className="whitespace-nowrap font-mono text-[11px] text-text-2">
          {formatEventTime(event.fillTime)}
        </span>
        <span className="min-w-0 truncate font-mono text-mono-xs text-text-1">
          {optionTypeLabel(event.optionType)} {event.strikePrice === null
            ? '—'
            : event.strikePrice.toLocaleString('en-US', { maximumFractionDigits: 2 })}
          {' · '}{event.dte === null ? 'DTE —' : `${event.dte} DTE`}
        </span>
        <span className="whitespace-nowrap text-right font-mono text-mono-xs font-medium text-text-1">
          {formatCompactCurrency(event.turnover)}
        </span>
      </div>

      <div className="mt-1 flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[11px] text-text-3">
        <span>{event.expiry || '到期日未知'}</span>
        <span className="font-mono">
          {formatInteger(event.volume)} 张 @ {event.price === null
            ? '—'
            : `$${event.price.toLocaleString('en-US', { maximumFractionDigits: 2 })}`}
        </span>
      </div>

      <div className="mt-1 grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-caption">
        <span className="min-w-0 text-text-2">
          {eventDirectionLabel(event.tickerType)} · {eventSentimentLabel(event.sentiment)}
        </span>
        <span className="min-w-0 break-words text-right text-text-2">
          {eventTypes.filter(Boolean).join(' · ')}
        </span>
      </div>

      <div className="mt-1 min-w-0 break-words font-mono text-[11px] text-text-3">
        IV {event.ivPercent === null ? '—' : `${event.ivPercent.toFixed(1)}%`}
        {' · '}V/OI {event.voRatioPercent === null ? '—' : `${event.voRatioPercent.toFixed(1)}%`}
        {' · '}Delta {event.delta === null ? '—' : event.delta.toFixed(3)}
      </div>
      <div className="mt-0.5 min-w-0 break-all font-mono text-[11px] leading-4 text-text-3">
        {event.optionCode}
      </div>
    </li>
  );
}

function OptionEventSnapshot({ context }: { context: CandidateOptionEventContext }) {
  if (context.state === 'unsupported') {
    return <p className="text-body-sm text-text-3">当前标的格式不在美股异常期权成交研究范围内。</p>;
  }
  if (context.state === 'loading') {
    return <p className="text-body-sm text-text-3">异常期权成交 · 加载中…</p>;
  }
  if (context.state === 'unavailable' || !context.item) {
    return (
      <p className="text-body-sm text-text-3">
        异常期权成交 · 不可用 · {context.message || '接口未返回该标的的异常成交。'}
      </p>
    );
  }

  const item = context.item;
  if (item.state === 'not_configured' || item.state === 'unavailable') {
    const stateLabel = item.state === 'not_configured' ? '未配置' : '不可用';
    return (
      <div className="space-y-1 text-body-sm text-text-3">
        <div>异常期权成交 · {stateLabel} · {item.message || '当前没有可用的事件数据。'}</div>
        <div className="text-caption">未取得真实事件数据时，不会由成交量、OI 或 ATM IV 推断异常成交。</div>
      </div>
    );
  }

  if (item.state === 'empty' || item.events.length === 0) {
    return (
      <div className="space-y-1 text-body-sm text-text-3" aria-label={`${item.ticker} 最近交易时段异常期权成交，无事件`}>
        <div>当前筛选范围内没有异常期权成交事件。</div>
        <div className="text-caption">
          来源 {item.source} · 抓取 {item.fetchedAt} · 成功查询不等于确认不存在全部异常活动。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2" aria-label={`${item.ticker} 最近交易时段异常期权成交，可用`}>
      <ol
        className="min-w-0 divide-y divide-subtle border-y border-subtle"
        aria-label={`${item.ticker} 异常期权成交事件列表`}
      >
        {item.events.slice(0, 5).map((event) => <OptionEventRow key={event.eventId} event={event} />)}
      </ol>
      <div className="text-caption text-text-3">
        <div>
          来源 {item.source} · 事件截至 {item.eventAsOf || item.fetchedAt} · 返回 {item.events.length}
          {item.allCount === null ? '' : ` / 共 ${item.allCount} 条`}
        </div>
        <div className="mt-1">
          Moomoo 的方向、情绪与事件标签不等于开平仓、真实主动买卖方向或 Dealer 仓位；本区块暂不进入候选排名。
        </div>
      </div>
      {item.limitations.length > 0 && (
        <details>
          <summary className="cursor-pointer text-caption text-text-2 hover:text-text-1">数据限制</summary>
          <ul className="mt-2 space-y-1 text-caption text-text-3">
            {item.limitations.map((line) => <li key={line}>· {line}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function aggregateOutcomeProgress(
  snapshots: OpportunitySnapshot[],
  horizonSessions: 5 | 20,
  track: 'full-research' | 'underlying-path' = 'full-research',
): OpportunityOutcomeProgress {
  return snapshots.reduce<OpportunityOutcomeProgress>((total, snapshot) => {
    const source = track === 'underlying-path'
      ? snapshot.underlyingPathProgress ?? snapshot.outcomeProgress
      : snapshot.fullResearchProgress ?? snapshot.outcomeProgress;
    const progress = source.find(
      (item) => item.horizonSessions === horizonSessions,
    );
    if (!progress) return total;
    return {
      horizonSessions,
      eligibleCount: total.eligibleCount + progress.eligibleCount,
      matureCount: total.matureCount + progress.matureCount,
      pendingCount: total.pendingCount + progress.pendingCount,
      partialCount: total.partialCount + progress.partialCount,
      dataGapCount: (total.dataGapCount ?? 0) + (progress.dataGapCount ?? 0),
    };
  }, {
    horizonSessions,
    eligibleCount: 0,
    matureCount: 0,
    pendingCount: 0,
    partialCount: 0,
    dataGapCount: 0,
  });
}

function LearningHorizonStatus({
  horizonSessions,
  progress,
  learning,
  minimumSummarySamples,
  minimumInvestigationSamples,
}: {
  horizonSessions: 5 | 20;
  progress: OpportunityOutcomeProgress;
  learning?: OpportunityLearningHorizon;
  minimumSummarySamples: number;
  minimumInvestigationSamples: number;
}) {
  const directionalSamples = learning?.directionalSampleCount ?? 0;
  const hasStatisticalSamples = progress.eligibleCount > 0 || directionalSamples > 0;
  const hitRate = learning?.summaryVisible && learning.contextHitRatePercent !== null
    ? `${learning.contextHitRatePercent.toLocaleString('en-US', { maximumFractionDigits: 1 })}%`
    : null;
  return (
    <div className="min-w-0 px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-mono-sm font-semibold text-text-1">{horizonSessions}D</span>
        <span className="font-mono text-mono-xs text-text-2">
          {hasStatisticalSamples
            ? `已回填 ${progress.matureCount} · 等待目标日 ${progress.pendingCount}`
            : '尚无可统计样本'}
        </span>
      </div>
      {progress.partialCount > 0 && (
        <div className="mt-1 text-caption text-warning">数据不完整 {progress.partialCount}</div>
      )}
      {(progress.dataGapCount ?? 0) > 0 && (
        <div className="mt-1 text-caption text-warning">已到期，待补数据 {progress.dataGapCount}</div>
      )}
      {hasStatisticalSamples && (
        <div className="mt-1 text-caption text-text-3">
          {hitRate
            ? `标的方向命中率 ${hitRate} · ${learning?.distinctSignalSessions ?? 0} 个独立交易日`
            : `方向样本 ${directionalSamples}/${minimumSummarySamples}；不足门槛不显示命中率`}
        </div>
      )}
      <div className="mt-1 text-[11px] text-text-3">
        {!hasStatisticalSamples
          ? '尚无符合严格统计口径的冻结候选。'
          : learning?.investigationReady
            ? '已达人工调查门槛；仍不会自动调权'
            : `人工调查须满 ${minimumInvestigationSamples} 个方向样本且 20 个独立交易日`}
      </div>
      {hasStatisticalSamples && (
        <div className="mt-1 text-[11px] text-text-3">仅统计同一 setup / Regime / 方向与策略版本。</div>
      )}
      {(learning?.excludedQualityCount ?? 0) > 0 && (
        <div className="mt-1 text-[11px] text-warning">
          复权或来源连续性不成立，已排除 {learning?.excludedQualityCount}
        </div>
      )}
    </div>
  );
}

function hasTrackableUnderlyingPath(snapshot: OpportunitySnapshot): boolean {
  if (typeof snapshot.underlyingPathCandidateCount === 'number') {
    return snapshot.underlyingPathCandidateCount > 0;
  }
  const rawTrack = snapshot.qualification?.tracks.find(
    (track) => track.trackKey === 'raw_underlying_path_v1',
  );
  if (rawTrack) {
    return rawTrack.qualifiedCount > 0 && rawTrack.prospectiveCount > 0;
  }
  return snapshot.validationEligible;
}

function OpportunityLearningPanel({
  snapshots,
  summary,
  loading,
  evaluating,
  error,
  notice,
  onEvaluate,
}: {
  snapshots: OpportunitySnapshot[];
  summary: OpportunityLearningSummaryResponse | null;
  loading: boolean;
  evaluating: boolean;
  error: string | null;
  notice: string | null;
  onEvaluate: () => void;
}) {
  const latestSnapshot = snapshots[0];
  const fiveDay = aggregateOutcomeProgress(snapshots, 5);
  const twentyDay = aggregateOutcomeProgress(snapshots, 20);
  const rawFiveDay = aggregateOutcomeProgress(snapshots, 5, 'underlying-path');
  const rawTwentyDay = aggregateOutcomeProgress(snapshots, 20, 'underlying-path');
  const minimumSummarySamples = summary?.minimumSummarySamples ?? 20;
  const minimumInvestigationSamples = summary?.minimumInvestigationSamples ?? 20;
  const fiveDayLearning = summary?.horizons.find((item) => item.horizonSessions === 5);
  const twentyDayLearning = summary?.horizons.find((item) => item.horizonSessions === 20);
  const hasEvaluableSnapshot = snapshots.some(hasTrackableUnderlyingPath);
  const hasStatisticalSamples = [
    fiveDay.eligibleCount,
    twentyDay.eligibleCount,
    fiveDayLearning?.directionalSampleCount ?? 0,
    twentyDayLearning?.directionalSampleCount ?? 0,
  ].some((count) => count > 0);
  const hasUnderlyingPathSamples = (
    rawFiveDay.eligibleCount > fiveDay.eligibleCount
    || rawTwentyDay.eligibleCount > twentyDay.eligibleCount
  );
  const latestFullResearchCount = latestSnapshot?.fullResearchCandidateCount
    ?? latestSnapshot?.qualification?.tracks.find(
      (track) => track.trackKey === 'canonical_full_research_v1',
    )?.qualifiedCount
    ?? latestSnapshot?.eligibleCandidateCount
    ?? 0;
  const latestEligibilityReasons = latestSnapshot?.analysisQualityEligible === false
    && (latestSnapshot.analysisQualityReasons?.length ?? 0) > 0
    ? latestSnapshot.analysisQualityReasons ?? []
    : latestSnapshot?.eligibilityReasons ?? [];

  return (
    <details className="group border-b border-subtle bg-bg-0">
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2.5 hover:bg-bg-1">
        <span className="inline-flex min-w-0 items-center gap-2">
          <span
            id="opportunity-learning-title"
            className="text-body-sm font-semibold text-text-1"
          >
            研究结果跟踪
          </span>
          <span className="text-caption text-text-3">展开查看统计口径</span>
        </span>
        <span className="font-mono text-mono-xs text-text-2">
          {hasStatisticalSamples
            ? `5D 已回填 ${fiveDay.matureCount} / 等待目标日 ${fiveDay.pendingCount} · 20D 已回填 ${twentyDay.matureCount} / 等待目标日 ${twentyDay.pendingCount}`
            : '尚无可统计样本'}
        </span>
      </summary>

      <section
        className="border-t border-subtle px-4 py-3"
        aria-labelledby="opportunity-learning-title"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-4xl text-caption text-text-3">
            这里只读展示已保存研究版本及 5 / 20 个交易日结果；服务器会在 XNYS 收盘后自动回填，到期前不会请求行情。
          </p>
          <Button
            variant="ghost"
            size="sm"
            disabled={!hasEvaluableSnapshot || evaluating}
            onClick={onEvaluate}
          >
            {evaluating ? '更新中…' : '手动重试到期缺口'}
          </Button>
        </div>

        <div className="mt-2 grid border-y border-subtle sm:grid-cols-[1fr_1fr_1.15fr] sm:divide-x sm:divide-subtle">
          <LearningHorizonStatus
            horizonSessions={5}
            progress={fiveDay}
            learning={fiveDayLearning}
            minimumSummarySamples={minimumSummarySamples}
            minimumInvestigationSamples={minimumInvestigationSamples}
          />
          <LearningHorizonStatus
            horizonSessions={20}
            progress={twentyDay}
            learning={twentyDayLearning}
            minimumSummarySamples={minimumSummarySamples}
            minimumInvestigationSamples={minimumInvestigationSamples}
          />
          <div className="min-w-0 border-t border-subtle px-3 py-2.5 sm:border-t-0">
            <div className="text-caption font-medium text-text-2">学习纪律</div>
            <div className="mt-1 text-caption text-text-3">
              不足 {minimumSummarySamples} 个方向样本不显示命中率；满 {minimumInvestigationSamples} 个方向样本且
              20 个独立交易日后，才进入人工调查。
            </div>
            <div className="mt-1 text-[11px] font-medium text-text-2">绝不自动调权。</div>
          </div>
        </div>

        {hasUnderlyingPathSamples && (
          <div
            className="mt-2 text-caption text-text-3"
            aria-label="标的路径审计进度"
          >
            标的路径审计（不计完整研究命中率）：
            5D 已回填 {rawFiveDay.matureCount} / 等待目标日 {rawFiveDay.pendingCount}
            {' · '}
            20D 已回填 {rawTwentyDay.matureCount} / 等待目标日 {rawTwentyDay.pendingCount}
            {((rawFiveDay.dataGapCount ?? 0) + (rawTwentyDay.dataGapCount ?? 0)) > 0
              ? ` · 待补数据 ${(rawFiveDay.dataGapCount ?? 0) + (rawTwentyDay.dataGapCount ?? 0)}`
              : ''}
          </div>
        )}

        <div className="mt-2 flex flex-wrap items-start justify-between gap-x-4 gap-y-1 text-caption text-text-3">
          <span>
            {loading && snapshots.length === 0
              ? '读取保存记录与学习进度…'
              : latestSnapshot
                ? `最近保存 ${latestSnapshot.marketDateEt} · 完整研究 ${latestFullResearchCount}/${latestSnapshot.candidateCount}`
                : '尚无保存记录；普通预览不会创建旧 v1 快照，也不能在窗口外补写官方盘前版本。'}
          </span>
          {summary && (
            <span>
              结果自动回填：{summary.automaticMaintenanceEnabled ? '已启用' : '未启用'}
              {' · '}自动调权：关闭
            </span>
          )}
        </div>
        {summary?.latestMaintenance && (
          <div className="mt-1 text-caption text-text-3">
            最近自动检查 {summary.latestMaintenance.sessionDateEt} ET
            {' · '}{summary.latestMaintenance.state}
            {' · '}第 {summary.latestMaintenance.attemptCount} 次
            {' · '}新增 {summary.latestMaintenance.insertedOutcomes}
            {' · '}缺口 {summary.latestMaintenance.dataGapHorizons}
          </div>
        )}
        {latestSnapshot && !latestSnapshot.validationEligible && latestEligibilityReasons.length > 0 && (
          <div className="mt-1 text-caption text-warning">
            最近保存版本未纳入统计：
            {latestEligibilityReasons.map(formatSnapshotEligibilityReason).join('；')}
          </div>
        )}
        {notice && <div className="mt-1 text-caption text-text-2" role="status">{notice}</div>}
        {error && (
          <div className="mt-1 text-caption text-warning" role="status">
            学习面板暂不可用：{error}。候选扫描与期权研究不受影响。
          </div>
        )}
      </section>
    </details>
  );
}

function CandidateDetail({
  candidate,
  optionContext,
  optionEventContext,
  optionWallContext,
  optionWallScope,
  onOptionWallScopeChange,
  signalVersion,
  rankingMethod,
  strategyValidationState,
  strategyValidationMessage,
  officialSnapshotKey,
}: {
  candidate: OpportunityCandidate;
  optionContext: CandidateOptionContext;
  optionEventContext: CandidateOptionEventContext;
  optionWallContext: CandidateOptionWallContext;
  optionWallScope: OptionWallScopePreset;
  onOptionWallScopeChange: (scope: OptionWallScopePreset) => void;
  signalVersion: string;
  rankingMethod: DailyOpportunityRun['rankingMethod'];
  strategyValidationState: DailyOpportunityRun['strategyValidationState'];
  strategyValidationMessage: string;
  officialSnapshotKey: string | null;
}) {
  const navigate = useNavigate();
  const supports = candidate.evidence.filter((item) => item.status === 'supports');
  const counter = candidate.evidence.filter((item) => item.status === 'contradicts');
  const failedGates = candidate.hardGates.filter((gate) => gate.status === 'failed');
  const missingCoreDomains = candidate.readiness
    .filter((source) => (
      source.state !== 'ready'
      && ['research_input', 'research_scope', 'execution_gate'].includes(source.actionability)
    ))
    .map((source) => formatMetric(source.domain));
  const missingContextDomains = candidate.readiness
    .filter((source) => source.state !== 'ready' && source.actionability === 'research_context')
    .map((source) => formatMetric(source.domain));
  const candidateSpecificUnknowns = candidate.unknowns.filter(
    (unknown) => !isRepeatedGlobalUnknown(unknown),
  );

  const close = getNumericEvidence(candidate, 'last_completed_close');
  const dayReturn = getNumericEvidence(candidate, 'completed_day_return');
  const volumeRatio = getNumericEvidence(candidate, 'volume_vs_prior_20d_median');
  const dollarVolumeRatio = getNumericEvidence(candidate, 'dollar_volume_vs_prior_20d_median');
  const ema = getEvidence(candidate, 'ema8_ema13_alignment');
  const range = getEvidence(candidate, 'prior_20d_range_position');
  const researchPlan = candidateResearchPlan(candidate);

  const reproducibleMetrics = [
    { label: '完整日收盘', value: formatNumber(close) },
    { label: '完整日涨跌', value: formatNumber(dayReturn, '%') },
    { label: 'EMA8 / EMA13', value: ema ? formatValue(ema) : '—' },
    { label: '20 日位置', value: range ? formatValue(range) : '—' },
    { label: '成交量倍率', value: formatNumber(volumeRatio, '×') },
    { label: '成交额倍率', value: formatNumber(dollarVolumeRatio, '×') },
  ];

  return (
    <aside className="min-w-0 border-t border-subtle bg-bg-1 lg:border-l lg:border-t-0" aria-label="所选候选研究详情">
      <header className="flex items-start justify-between gap-3 border-b border-subtle px-4 py-3">
        <div>
          <h3 className="text-h3 font-semibold text-text-1">{candidate.ticker} · 研究详情</h3>
          <div className="mt-1 text-caption text-text-3">
            完整日线 {formatCompletedBar(candidate.lastCompletedBarAt)}
            {' · '}{SOURCE_LABELS[candidate.source ?? ''] ?? candidate.source ?? '来源未报告'}
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate(opportunityDetailPath(candidate.ticker, officialSnapshotKey))}
          aria-label={`打开 ${candidate.ticker} 完整机会分析`}
        >
          完整分析
          <ChevronRight size={14} />
        </Button>
      </header>

      <div className="space-y-4 p-4">
        <section className="grid gap-2 sm:grid-cols-2" aria-label={`${candidate.ticker} 触发与失效观察`}>
          <div className="border-l-2 border-[color:var(--text-2)] pl-3">
            <div className="text-caption font-medium text-text-2">触发确认</div>
            <div className="mt-1 text-caption text-text-1">{researchPlan.confirmation}</div>
          </div>
          <div className="border-l-2 border-[color:var(--warn-muted)] pl-3">
            <div className="text-caption font-medium text-text-2">失效观察</div>
            <div className="mt-1 text-caption text-text-1">{researchPlan.invalidation}</div>
          </div>
        </section>

        <section aria-labelledby="reproducible-calculation-title">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h4 id="reproducible-calculation-title" className="text-body-sm font-semibold text-text-1">
              计算可复现
            </h4>
            <span className="font-mono text-mono-xs text-text-3">{signalVersion}</span>
          </div>
          <p className="mt-1 text-caption text-text-3">
            以下值只使用已完成日线，并保留观察窗口、来源和抓取时间。
          </p>
          <dl className="mt-2 divide-y divide-subtle border-y border-subtle">
            {reproducibleMetrics.map((metric) => (
              <div key={metric.label} className="grid grid-cols-[112px_minmax(0,1fr)] gap-3 py-2">
                <dt className="text-caption text-text-3">{metric.label}</dt>
                <dd className="min-w-0 text-right font-mono text-mono-xs text-text-1">{metric.value}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="border-l-2 border-[color:var(--warn-muted)] pl-3" aria-labelledby="strategy-unvalidated-title">
          <h4 id="strategy-unvalidated-title" className="text-body-sm font-semibold text-text-1">
            策略尚未验证
          </h4>
          <div className="mt-1 text-body-sm text-text-2">
            研究用途：{candidateStateLabel(candidate)} · 策略验证：
            {!strategyValidationState || strategyValidationState === 'not_validated'
              ? '未验证'
              : strategyValidationState}
          </div>
          <div className="mt-1 text-caption text-text-3">
            {strategyValidationMessage || '尚未用冻结样本验证候选排序与后续收益、MFE/MAE 的关系。'}
          </div>
          <div className="mt-1 text-caption text-text-3">
            排序方法：{!rankingMethod || rankingMethod === 'rule_based_evidence_count'
              ? '规则证据计数（非胜率评分）'
              : rankingMethod}
          </div>
        </section>

        <section aria-labelledby="supporting-evidence-title">
          <h4 id="supporting-evidence-title" className="text-body-sm font-semibold text-text-1">主要支持证据</h4>
          {supports.length > 0 ? (
            <ul className="mt-2 divide-y divide-subtle border-y border-subtle">
              {supports.slice(0, 4).map((evidence) => (
                <li key={evidence.evidenceId} className="flex items-start justify-between gap-3 py-2 text-caption">
                  <span className="text-text-2">{formatMetric(evidence.metric)}</span>
                  <span className="text-right font-mono text-mono-xs text-text-1">{formatValue(evidence)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-body-sm text-text-3">暂无足够的支持证据。</p>
          )}
        </section>

        <section aria-labelledby="research-gates-title">
          <h4 id="research-gates-title" className="text-body-sm font-semibold text-text-1">基础门禁</h4>
          <div className="mt-2 text-body-sm text-text-2">
            {counter.length > 0 || failedGates.length > 0
              ? [
                ...counter.map((item) => formatMetric(item.metric)),
                ...failedGates.map((gate) => GATE_LABELS[gate.gateId] ?? gate.gateId),
              ].slice(0, 4).join(' · ')
              : '基础行情与标的范围门禁已通过。'}
          </div>
          {missingCoreDomains.length > 0 && (
            <div className="mt-1 text-caption text-warning">
              基础数据待恢复：{[...new Set(missingCoreDomains)].join(' · ')}
            </div>
          )}
          {missingContextDomains.length > 0 && (
            <div className="mt-1 text-caption text-text-3">
              市场背景未就绪：{[...new Set(missingContextDomains)].join(' · ')}；不阻断基础量价结构浏览。
            </div>
          )}
        </section>

        <section className="border-t border-subtle pt-4" aria-labelledby="option-structure-title">
          <h4 id="option-structure-title" className="text-body-sm font-semibold text-text-1">期权结构</h4>
          <div className="mt-2">
            <OptionContextSnapshot
              item={optionContext.item}
              state={optionContext.state}
              fallbackMessage={optionContext.message}
            />
          </div>
          <div className="mt-3 border-t border-subtle pt-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-body-sm font-medium text-text-2">期权墙</div>
                <div className="mt-0.5 text-caption text-text-3">按需读取 · 只读 · DTE</div>
              </div>
              <div
                className="inline-flex overflow-hidden rounded-ds-sm border border-subtle"
                role="group"
                aria-label="期权墙 DTE 范围"
              >
                {(Object.entries(OPTION_WALL_SCOPES) as Array<[
                  OptionWallScopePreset,
                  (typeof OPTION_WALL_SCOPES)[OptionWallScopePreset],
                ]>).map(([value, scope]) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={optionWallScope === value}
                    onClick={() => onOptionWallScopeChange(value)}
                    className={`border-r border-subtle px-2 py-1 font-mono text-mono-xs last:border-r-0 ${optionWallScope === value ? 'bg-bg-3 text-text-1' : 'bg-bg-1 text-text-3 hover:bg-bg-2 hover:text-text-2'}`}
                  >
                    {scope.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-2"><OptionWallSnapshot context={optionWallContext} /></div>
          </div>
          <div className="mt-3 border-t border-subtle pt-3">
            <div className="text-body-sm font-medium text-text-2">最近交易时段异常期权成交</div>
            <div className="mt-0.5 text-caption text-text-3">按需读取 · 最近 5 条 · Moomoo 事件分类</div>
            <div className="mt-2"><OptionEventSnapshot context={optionEventContext} /></div>
          </div>
        </section>

        <details className="border-t border-subtle pt-3">
          <summary className="cursor-pointer text-body-sm text-text-2 hover:text-text-1">
            查看全部证据与数据状态
          </summary>
          <div className="mt-3 space-y-4">
            <div>
              <div className="text-caption font-medium text-text-2">全部证据</div>
              <ul className="mt-2 space-y-3">
                {candidate.evidence.map((evidence) => (
                  <li key={evidence.evidenceId} className="text-caption text-text-2">
                    <div className="flex flex-wrap justify-between gap-2">
                      <span className="font-medium text-text-1">{formatMetric(evidence.metric)}</span>
                      <span className="font-mono text-mono-xs">{formatValue(evidence)}</span>
                    </div>
                    <div className="mt-0.5 text-text-3">
                      {evidence.source} · {evidence.observationWindow}
                      {evidence.observedAt ? ` · ${evidence.observedAt}` : ''}
                    </div>
                    <div className="text-text-3">
                      数据质量：{formatMetric(evidence.qualityState)}
                      {evidence.limitations.length > 0 ? ` · ${evidence.limitations.join('；')}` : ''}
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <div className="text-caption font-medium text-text-2">数据域状态</div>
              <ul className="mt-2 divide-y divide-subtle border-y border-subtle">
                {candidate.readiness.map((source) => (
                  <li key={`${candidate.candidateId}-${source.domain}`} className="py-2 text-caption">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-text-1">{formatMetric(source.domain)}</span>
                      <ReadinessMark state={source.state} actionability={source.actionability} />
                    </div>
                    <div className="mt-1 text-text-3">{source.message}</div>
                  </li>
                ))}
              </ul>
            </div>

            {candidateSpecificUnknowns.length > 0 && (
              <div>
                <div className="text-caption font-medium text-text-2">候选特有未知项</div>
                <ul className="mt-2 space-y-1 text-caption text-text-3">
                  {candidateSpecificUnknowns.map((unknown) => <li key={unknown}>· {unknown}</li>)}
                </ul>
              </div>
            )}
          </div>
        </details>
      </div>
    </aside>
  );
}

function CanonicalPremarketCard({
  cycle,
  error,
  activity,
  baselineSource,
}: {
  cycle: PremarketCycleResponse | null;
  error: string | null;
  activity: PremarketActivity;
  baselineSource: OpportunityBaselineSource | null;
}) {
  const publishedSnapshot = cycle?.state === 'published' ? cycle.snapshot : null;
  const qualificationTrackLabels = [
    ['raw_underlying_path_v1', '标的路径'],
    ['underlying_daily_selection_v1', '日线选股'],
    ['canonical_full_research_v1', '完整研究'],
  ] as const;
  const qualificationCounts = publishedSnapshot?.qualification
    ? qualificationTrackLabels.map(([trackKey, label]) => {
      const track = publishedSnapshot.qualification?.tracks.find(
        (item) => item.trackKey === trackKey,
      );
      if (!track) return null;
      const total = track.qualifiedCount + track.excludedCount + track.unverifiedCount;
      return `${label} ${track.qualifiedCount}/${total}`;
    })
    : null;
  const qualificationCountsLabel = qualificationCounts?.every(
    (item): item is string => item !== null,
  )
    ? qualificationCounts.join('、')
    : null;
  const qualityLabel = cycle?.quality === 'ready'
    ? '完整'
    : cycle?.quality === 'degraded'
      ? '支持证据降级'
      : cycle?.quality === 'blocked'
        ? '阻断'
        : '待判定';
  const publicationLabel = !cycle
    ? '读取中'
    : cycle.state === 'published'
      ? '已发布'
      : cycle.state === 'running'
        ? '生成中'
        : ['waiting_window', 'ready_to_run'].includes(cycle.state)
          ? '待发布'
          : cycle.state === 'research_pool_missing'
            ? '研究池未配置'
            : cycle.state === 'non_session'
              ? '今日不发布'
              : '未发布';
  const publicationTone = cycle?.state === 'published'
    ? 'text-success'
    : cycle?.state === 'blocked' || cycle?.state === 'failed'
      ? 'text-warning'
      : 'text-text-2';
  const statisticsLabel = !cycle
    ? '读取中'
    : cycle.state !== 'published'
      ? '尚未产生'
      : !publishedSnapshot
        ? '元数据缺失'
        : qualificationCountsLabel
          ? qualificationCountsLabel
          : publishedSnapshot.validationEligible
            ? `已纳入 ${publishedSnapshot.eligibleCandidateCount}/${publishedSnapshot.candidateCount}`
            : `未纳入 ${publishedSnapshot.eligibleCandidateCount}/${publishedSnapshot.candidateCount}`;
  const statisticsTone = publishedSnapshot?.validationEligible
    ? 'text-success'
    : publishedSnapshot
      ? 'text-warning'
      : 'text-text-2';
  const activityLabel = activity === 'status'
    ? '读取今日研究状态…'
    : activity === 'run'
      ? '生成今日研究…'
      : activity === 'preview'
        ? '刷新只读预览…'
        : null;
  const showNoCanonical = cycle
    && cycle.state !== 'published'
    && ['non_session', 'window_closed'].includes(cycle.state);
  const universeVersion = cycle?.universeVersionKey
    ? cycle.universeVersionKey.slice(0, 16)
    : '尚未保存';
  const primaryTime = cycle?.primaryScheduledAt
    ? `${formatEtDateTime(cycle.primaryScheduledAt).slice(11)} ET`
    : '09:12 ET';
  const retryTime = cycle?.retryScheduledAt
    ? `${formatEtDateTime(cycle.retryScheduledAt).slice(11)} ET`
    : '09:17 ET';

  return (
    <section
      className="border-b border-subtle bg-bg-1 px-4 py-3"
      aria-label="官方盘前研究状态"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-body-sm font-semibold text-text-1">官方盘前研究</h3>
            {baselineSource && (
              <span className="text-caption text-text-3">
                当前榜单：{baselineSource === 'canonical' ? '官方版本' : '只读预览'}
              </span>
            )}
          </div>
          <div
            className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-caption"
            aria-label="官方盘前研究三层状态"
          >
            <span aria-label={`发布状态：${publicationLabel}`}>
              <span className="text-text-3">发布状态</span>
              {' '}
              <strong className={`font-medium ${publicationTone}`}>{publicationLabel}</strong>
            </span>
            <span aria-label={`数据质量：${qualityLabel}`}>
              <span className="text-text-3">数据质量</span>
              {' '}
              <strong className={`font-medium ${cycle?.quality === 'degraded' || cycle?.quality === 'blocked' ? 'text-warning' : 'text-text-2'}`}>
                {qualityLabel}
              </strong>
            </span>
            <span aria-label={`统计入样：${statisticsLabel}`}>
              <span className="text-text-3">统计入样</span>
              {' '}
              <strong className={`font-medium ${statisticsTone}`}>{statisticsLabel}</strong>
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-caption text-text-3">
            <span>
              市场日 <strong className="font-mono font-medium text-text-2">{cycle?.marketDateEt ?? '读取中'} ET</strong>
            </span>
            <span>
              T-1 证据 <strong className="font-mono font-medium text-text-2">{cycle?.previousSession ?? '待报告'}</strong>
            </span>
            <span>
              窗口 <strong className="font-mono font-medium text-text-2">
                {cycle ? formatEtWindow(cycle.windowStartAt, cycle.windowEndAt) : '读取中'}
              </strong>
            </span>
            {cycle?.latestStartAt && (
              <span>
                最晚启动 <strong className="font-mono font-medium text-text-2">
                  {formatEtDateTime(cycle.latestStartAt).slice(11)} ET
                </strong>
              </span>
            )}
            <span>
              后台自动研究 <strong className="font-medium text-text-2">
                {cycle?.schedulerEnabled ? '已启用' : '未启用'}
              </strong>
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-caption text-text-3">
            <span>
              官方池 <strong className="font-medium text-text-2">
                {cycle ? `${cycle.universe.length} 只` : '读取中'}
              </strong>
            </span>
            <span aria-label={cycle?.universeVersionKey ? `完整版本 ${cycle.universeVersionKey}` : undefined}>
              版本 <strong className="font-mono font-medium text-text-2">{universeVersion}</strong>
            </span>
            <span>
              自动时点 <strong className="font-mono font-medium text-text-2">
                {primaryTime} / {retryTime}
              </strong>
            </span>
            {cycle?.nextScheduledAt && (
              <span>
                下一步 <strong className="font-mono font-medium text-text-2">
                  {formatEtDateTime(cycle.nextScheduledAt)} ET
                </strong>
              </span>
            )}
          </div>
          <div className="mt-1 text-caption text-text-3">
            最近执行{' '}
            {cycle?.attemptKey ? (
              <>
                <strong
                  className="font-mono font-medium text-text-2"
                  aria-label={`完整执行编号 ${cycle.attemptKey}`}
                >
                  {cycle.attemptKey.slice(0, 16)}
                </strong>
                {' · '}{cycle.attemptTrigger === 'scheduler' ? '后台' : '手动'}
                {' · '}{PREMARKET_STATE_LABELS[cycle.state]}
                {cycle.attemptStartedAt
                  ? ` · ${formatEtDateTime(cycle.attemptStartedAt)} ET`
                  : ''}
                {cycle.state === 'running' && cycle.leaseExpiresAt
                  ? ` · 租约至 ${formatEtDateTime(cycle.leaseExpiresAt).slice(11)} ET`
                  : ''}
                {['blocked', 'failed'].includes(cycle.state)
                  ? cycle.recoverable ? ' · 可恢复重试' : ' · 尝试已用尽'
                  : ''}
                {cycle.recoveredFromAttemptKey ? ' · 已从上一执行恢复' : ''}
              </>
            ) : (
              <span className="text-text-2">暂无持久化执行记录</span>
            )}
          </div>
        </div>
        {activityLabel && <span className="text-caption text-text-2">{activityLabel}</span>}
      </div>

      {error ? (
        <p className="mt-2 text-caption text-warning">
          今日盘前研究状态或生成暂不可用：{error}。
          {baselineSource === 'canonical'
            ? ' 当前继续显示上一份已发布批次。'
            : baselineSource === 'preview'
              ? ' 当前继续显示只读预览，不会写入旧快照。'
              : ' 为避免与未知的服务端任务并发，当前没有启动预览。'}
        </p>
      ) : cycle?.state === 'published' ? (
        <div className="mt-2 border-l-2 border-success/30 pl-3 text-caption text-text-2">
          {publishedSnapshot ? (
            <>
              <div>
                不可变快照 <span className="font-mono text-mono-xs text-text-1">{publishedSnapshot.snapshotKey}</span>
                {' · '}冻结 {formatEtDateTime(publishedSnapshot.frozenAt)} ET
              </div>
              <div className="mt-0.5 text-text-3">
                {qualificationCountsLabel
                  ? `统计入样 ${qualificationCountsLabel}`
                  : `统计入样 ${publishedSnapshot.eligibleCandidateCount}/${publishedSnapshot.candidateCount}`}
                {cycle.idempotentReplay ? ' · 返回既有批次' : ' · 本次发布'}
              </div>
              {cycle.quality === 'degraded' && (
                <div className="mt-0.5 text-warning">
                  官方版本已发布，可用于今日研究；支持证据降级
                  {!publishedSnapshot.validationEligible ? '，本批次未纳入严格结果统计。' : '。'}
                </div>
              )}
            </>
          ) : (
            <div className="text-warning">服务端报告已发布，但未返回快照元数据；请刷新状态核对。</div>
          )}
        </div>
      ) : cycle?.state === 'waiting_window' ? (
        <p className="mt-2 text-caption text-text-3">
          等待 {primaryTime} 官方研究时点。页面只读取状态与预览；后台未到时点前不会启动 Regime、行情计算或冻结快照。
        </p>
      ) : cycle?.state === 'ready_to_run' ? (
        <p className="mt-2 text-caption text-text-2">
          {cycle.schedulerEnabled
            ? '已到官方研究时点。后台调度会受控执行；也可点击“立即生成”。'
            : '已到官方研究时点，但后台自动研究未启用；可点击“立即生成”。'}
          基础发布门禁通过且仍在窗口内即可发布；支持证据降级时会保留官方版本，但不纳入严格结果统计。
        </p>
      ) : cycle?.state === 'research_pool_missing' ? (
        <p className="mt-2 text-caption text-warning">
          尚未保存官方盘前研究池。请到 Watchlist 显式选择并保存；后台不会从本地列表或其他配置静默取数。
        </p>
      ) : cycle?.state === 'running' ? (
        <p className="mt-2 text-caption text-text-2">
          服务端今日盘前研究仍在执行。
          {cycle.retryAfterSeconds ? ` 建议 ${cycle.retryAfterSeconds} 秒后刷新状态。` : ''}
        </p>
      ) : showNoCanonical ? (
        <p className="mt-2 text-caption text-warning">
          今日未生成官方盘前版本，当前仅为预览。
          {cycle.state === 'non_session' ? ' 今天不是 XNYS 交易日。' : ' 盘前发布窗口已经关闭。'}
        </p>
      ) : cycle && ['blocked', 'failed'].includes(cycle.state) ? (
        <div className="mt-2 text-caption text-warning">
          <p>
            {cycle.state === 'blocked'
              ? '质量门禁未通过，今日官方盘前版本未发布。'
              : '今日盘前研究暂时失败，未发布官方版本。'}
          </p>
          {cycle.errorCode && <p className="mt-0.5 font-mono text-mono-xs">原因 {cycle.errorCode}</p>}
        </div>
      ) : (
        <p className="mt-2 text-caption text-text-3">正在读取服务端只读状态；尚未启动任何写入。</p>
      )}

      {cycle && cycle.stages.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-caption text-text-3" aria-label="官方盘前服务端阶段">
          {cycle.stages.map((stage) => (
            <span key={`${stage.name}-${stage.startedAt ?? ''}`}>
              {PREMARKET_STAGE_LABELS[stage.name] ?? stage.name}
              {' · '}{PREMARKET_STAGE_STATE_LABELS[stage.state] ?? stage.state}
            </span>
          ))}
        </div>
      )}

      {cycle && cycle.qualityReasons.length > 0 && (
        <details className="mt-2 text-caption text-text-3">
          <summary className="cursor-pointer text-text-2 hover:text-text-1">
            质量原因 {cycle.qualityReasons.length}
          </summary>
          <ul className="mt-1 space-y-0.5">
            {cycle.qualityReasons.map((reason) => (
              <li key={reason}>· {formatAnalysisQualityReason(reason)}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/**
 * 详情页深链：官方 canonical 榜单携带 snapshotKey，让详情页优先读取同一份
 * 冻结证据；preview/即时扫描没有官方快照可绑，只传 ticker。
 */
function opportunityDetailPath(ticker: string, officialSnapshotKey: string | null): string {
  return officialSnapshotKey
    ? `/regime/opportunity/${ticker}?snapshotKey=${encodeURIComponent(officialSnapshotKey)}`
    : `/regime/opportunity/${ticker}`;
}

export function DailyOpportunityList({ symbols }: { symbols: string[] }) {
  const navigate = useNavigate();
  const [run, setRun] = useState<DailyOpportunityRun | null>(null);
  const runRef = useRef<DailyOpportunityRun | null>(null);
  const [baselineSource, setBaselineSource] = useState<OpportunityBaselineSource | null>(null);
  const [premarketCycle, setPremarketCycle] = useState<PremarketCycleResponse | null>(null);
  const officialSnapshotKey = baselineSource === 'canonical'
    ? premarketCycle?.snapshot?.snapshotKey ?? null
    : null;
  const [premarketError, setPremarketError] = useState<string | null>(null);
  const [premarketActivity, setPremarketActivity] = useState<PremarketActivity>('status');
  const [premarketPollSequence, setPremarketPollSequence] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const selectedCandidateIdRef = useRef<string | null>(null);
  const [optionContextRefresh, setOptionContextRefresh] = useState({ runId: '', sequence: 0 });
  const [optionOverviewLoad, setOptionOverviewLoad] = useState<{
    requestKey: string;
    state: 'settled' | 'unavailable';
    items: Record<string, OpportunityOptionOverviewItem>;
    message: string | null;
  }>({ requestKey: '', state: 'settled', items: {}, message: null });
  const [optionWallSummaryLoad, setOptionWallSummaryLoad] = useState<{
    requestKey: string;
    state: 'settled' | 'unavailable';
    items: Record<string, OpportunityOptionWallItem>;
    message: string | null;
  }>({ requestKey: '', state: 'settled', items: {}, message: null });
  const [optionWallDetailLoad, setOptionWallDetailLoad] = useState<{
    requestKey: string;
    state: 'settled' | 'unavailable';
    item?: OpportunityOptionWallItem;
    message: string | null;
  }>({ requestKey: '', state: 'settled', item: undefined, message: null });
  const [optionWallScope, setOptionWallScope] = useState<OptionWallScopePreset>('0-45');
  const optionWallScopeRef = useRef<OptionWallScopePreset>('0-45');
  const [optionWallRefresh, setOptionWallRefresh] = useState({
    runId: '',
    ticker: '',
    dteMin: 0,
    dteMax: 45,
    sequence: 0,
  });
  const [optionEventLoad, setOptionEventLoad] = useState<{
    requestKey: string;
    state: 'settled' | 'unavailable';
    item?: OpportunityOptionEventItem;
    message: string | null;
  }>({ requestKey: '', state: 'settled', item: undefined, message: null });
  const [optionEventRefresh, setOptionEventRefresh] = useState({
    runId: '',
    ticker: '',
    sequence: 0,
  });
  const [learningSnapshots, setLearningSnapshots] = useState<OpportunitySnapshot[]>([]);
  const [learningSummary, setLearningSummary] = useState<OpportunityLearningSummaryResponse | null>(null);
  const [learningLoading, setLearningLoading] = useState(true);
  const [learningError, setLearningError] = useState<string | null>(null);
  const [learningNotice, setLearningNotice] = useState<string | null>(null);
  const [evaluatingSnapshots, setEvaluatingSnapshots] = useState(false);
  const requestSequence = useRef(0);
  const learningRequestSequence = useRef(0);
  const optionOverviewRequestSequence = useRef(0);
  const optionEventRequestSequence = useRef(0);
  const optionWallSummaryRequestSequence = useRef(0);
  const optionWallRequestSequence = useRef(0);
  const premarketPollCount = useRef(0);
  const premarketPollingAttemptKey = useRef<string | null>(null);

  const normalizedSymbols = useMemo(
    () => symbols.map((symbol) => symbol.trim().toUpperCase()).filter(Boolean).slice(0, 20),
    [symbols],
  );

  const selectedCandidate = run?.candidates.find(
    (candidate) => candidate.candidateId === selectedCandidateId,
  ) ?? run?.candidates[0] ?? null;
  const primaryCandidates = useMemo(() => run?.candidates.slice(0, 5) ?? [], [run]);
  const secondaryCandidates = useMemo(() => run?.candidates.slice(5) ?? [], [run]);

  const shouldRefreshOptionContext = Boolean(
    run && optionContextRefresh.runId === run.runId,
  );

  const optionOverviewSymbols = useMemo(
    () => run?.candidates
      .map((candidate) => normalizeSupportedUsOptionUnderlying(candidate.ticker))
      .filter((ticker): ticker is string => ticker !== null)
      .slice(0, 15) ?? [],
    [run],
  );
  const optionOverviewRequestKey = run && optionOverviewSymbols.length > 0
    ? `${run.runId}:${optionOverviewSymbols.join(',')}:${shouldRefreshOptionContext ? `refresh-${optionContextRefresh.sequence}` : 'cached'}`
    : '';
  const optionWallSummarySymbols = useMemo(
    () => primaryCandidates
      .map((candidate) => normalizeSupportedUsOptionUnderlying(candidate.ticker))
      .filter((ticker): ticker is string => ticker !== null),
    [primaryCandidates],
  );
  const optionWallSummaryRequestKey = run && optionWallSummarySymbols.length > 0
    ? `${run.runId}:${optionWallSummarySymbols.join(',')}:0:45:${shouldRefreshOptionContext ? `refresh-${optionContextRefresh.sequence}` : 'cached'}`
    : '';

  const loadLearning = useCallback(async () => {
    const requestId = learningRequestSequence.current + 1;
    learningRequestSequence.current = requestId;
    setLearningLoading(true);
    const [snapshotResult, summaryResult] = await Promise.allSettled([
      fetchOpportunitySnapshots(10),
      fetchOpportunityLearningSummary(),
    ]);
    if (learningRequestSequence.current !== requestId) return;

    const messages: string[] = [];
    if (snapshotResult.status === 'fulfilled') {
      setLearningSnapshots(snapshotResult.value.items);
    } else {
      messages.push(snapshotResult.reason instanceof Error
        ? snapshotResult.reason.message
        : '保存记录读取失败');
    }
    if (summaryResult.status === 'fulfilled') {
      setLearningSummary(summaryResult.value);
    } else {
      messages.push(summaryResult.reason instanceof Error
        ? summaryResult.reason.message
        : '学习摘要读取失败');
    }
    setLearningError(messages.length > 0 ? messages.join('；') : null);
    setLearningLoading(false);
  }, []);

  const load = useCallback(async (intent: PremarketLoadIntent = 'initial') => {
    const explicitRun = intent === 'run';
    const refreshPreview = intent === 'refresh';
    const userInitiated = explicitRun || refreshPreview;
    const statusOnly = intent === 'poll';
    let continueStatusPolling = false;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setLoading(true);
    setError(null);
    setPremarketError(null);
    setPremarketActivity('status');
    try {
      let cycle: PremarketCycleResponse | null = null;
      let nextRun: DailyOpportunityRun | null = null;
      let nextBaselineSource: OpportunityBaselineSource = 'preview';
      let previewAllowed = true;

      try {
        cycle = await fetchPremarketCycleStatus(5);
        if (requestSequence.current !== requestId) return;
        setPremarketCycle(cycle);
        continueStatusPolling = cycle.state === 'running';
        if (continueStatusPolling) {
          const pollingKey = cycle.attemptKey ?? cycle.cycleKey;
          if (premarketPollingAttemptKey.current !== pollingKey) {
            premarketPollingAttemptKey.current = pollingKey;
            premarketPollCount.current = 0;
          }
          if (intent !== 'poll') {
            premarketPollCount.current = 0;
            setPremarketPollSequence((current) => current + 1);
          }
        } else {
          premarketPollingAttemptKey.current = null;
          premarketPollCount.current = 0;
        }
      } catch (caught) {
        if (requestSequence.current !== requestId) return;
        const message = caught instanceof Error ? caught.message : '只读状态读取失败';
        setPremarketError(message);
        setError(`官方盘前研究状态不可用，已停止新的预览请求：${message}`);
        // The status failure could hide a live server-owned attempt. Fail
        // closed for every intent so /daily never races unknown provider work.
        previewAllowed = false;
        if (statusOnly) continueStatusPolling = true;
      }

      const explicitRunAvailable = Boolean(
        cycle && explicitRun && canStartManualPremarketRun(cycle),
      );
      if (explicitRun && !explicitRunAvailable) previewAllowed = false;

      if (cycle && explicitRunAvailable) {
        setPremarketActivity('run');
        try {
          cycle = await runPremarketCycle(5);
          if (requestSequence.current !== requestId) return;
          setPremarketCycle(cycle);
          if (cycle.run) {
            nextRun = cycle.run;
            nextBaselineSource = cycle.state === 'published' ? 'canonical' : 'preview';
          }
        } catch (caught) {
          if (requestSequence.current !== requestId) return;
          setPremarketError(caught instanceof Error ? caught.message : '今日盘前研究生成失败');
          try {
            cycle = await fetchPremarketCycleStatus(5);
            if (requestSequence.current !== requestId) return;
            setPremarketCycle(cycle);
            if (!['blocked', 'failed'].includes(cycle.state)) {
              setPremarketError(null);
            }
            if (cycle.state === 'published' && cycle.run) {
              nextRun = cycle.run;
              nextBaselineSource = 'canonical';
            } else if (cycle.run) {
              nextRun = cycle.run;
            } else if (cycle.state === 'running') {
              previewAllowed = false;
            }
          } catch {
            // The timed-out run may still own server-side provider work. Without
            // a readable status, do not launch a second /daily scan beside it.
            previewAllowed = false;
          }
        }
      } else if (cycle?.state === 'published' && cycle.run) {
        nextRun = cycle.run;
        nextBaselineSource = 'canonical';
      } else if (cycle?.run) {
        nextRun = cycle.run;
      } else if (
        statusOnly
        || (cycle ? hasImminentPremarketProviderWork(cycle) : false)
        || cycle?.state === 'published'
      ) {
        previewAllowed = false;
      }

      if (!nextRun && previewAllowed) {
        setPremarketActivity('preview');
        nextRun = await fetchDailyOpportunities(
          normalizedSymbols,
          10,
          { refresh: refreshPreview },
        );
        if (requestSequence.current !== requestId) return;
      }
      if (!nextRun) return;

      if (requestSequence.current === requestId) {
        const previousRun = runRef.current;
        if (
          refreshPreview
          && nextBaselineSource === 'preview'
          && previousRun
          && hasSameUniverse(previousRun, nextRun)
          && hasUsableBaseCandidate(previousRun)
          && !hasUsableBaseCandidate(nextRun)
        ) {
          setError('本次刷新没有取得可用日线，已保留上一次成功结果；请稍后重试');
          return;
        }
        const nextSelectedCandidate = nextRun.candidates.find(
          (candidate) => candidate.candidateId === selectedCandidateIdRef.current,
        ) ?? nextRun.candidates[0] ?? null;
        const nextSelectedId = nextSelectedCandidate?.candidateId ?? null;
        const nextWallTicker = nextSelectedCandidate
          ? normalizeSupportedUsOptionUnderlying(nextSelectedCandidate.ticker) ?? ''
          : '';
        runRef.current = nextRun;
        setRun(nextRun);
        setBaselineSource(nextBaselineSource);
        selectedCandidateIdRef.current = nextSelectedId;
        setSelectedCandidateId(nextSelectedId);
        setOptionContextRefresh((current) => {
          if (userInitiated) return { runId: nextRun.runId, sequence: current.sequence + 1 };
          return current.runId ? { ...current, runId: '' } : current;
        });
        setOptionWallRefresh((current) => {
          if (userInitiated) {
            const refreshedScope = OPTION_WALL_SCOPES[optionWallScopeRef.current];
            return {
              runId: nextRun.runId,
              ticker: nextWallTicker,
              dteMin: refreshedScope.dteMin,
              dteMax: refreshedScope.dteMax,
              sequence: current.sequence + 1,
            };
          }
          return current.runId ? { ...current, runId: '', ticker: '' } : current;
        });
        setOptionEventRefresh((current) => {
          if (userInitiated) {
            return {
              runId: nextRun.runId,
              ticker: nextWallTicker,
              sequence: current.sequence + 1,
            };
          }
          return current.runId ? { ...current, runId: '', ticker: '' } : current;
        });
      }
    } catch (caught) {
      if (requestSequence.current === requestId) {
        setError(caught instanceof Error ? caught.message : '每日机会扫描失败');
      }
    } finally {
      if (requestSequence.current === requestId) {
        setLoading(false);
        setPremarketActivity(null);
        if (statusOnly && continueStatusPolling) {
          premarketPollCount.current += 1;
          if (premarketPollCount.current < MAX_PREMARKET_STATUS_POLLS) {
            setPremarketPollSequence((current) => current + 1);
          }
        }
      }
    }
  }, [normalizedSymbols]);

  const selectCandidate = useCallback((candidateId: string) => {
    selectedCandidateIdRef.current = candidateId;
    setSelectedCandidateId(candidateId);
  }, []);

  const selectOptionWallScope = useCallback((scope: OptionWallScopePreset) => {
    optionWallScopeRef.current = scope;
    setOptionWallScope(scope);
  }, []);

  const evaluateMatureOutcomes = useCallback(async () => {
    const targets = learningSnapshots.filter(hasTrackableUnderlyingPath);
    if (targets.length === 0) return;
    setEvaluatingSnapshots(true);
    setLearningError(null);
    setLearningNotice(null);
    try {
      const results: PromiseSettledResult<Awaited<ReturnType<typeof evaluateOpportunitySnapshot>>>[] = [];
      // Two snapshots at a time keeps provider traffic bounded while avoiding
      // a long fully-serial refresh as the immutable history grows.
      for (let index = 0; index < targets.length; index += 2) {
        const batch = targets.slice(index, index + 2);
        results.push(...await Promise.allSettled(
          batch.map((snapshot) => evaluateOpportunitySnapshot(snapshot.snapshotKey)),
        ));
      }
      const successful = results.filter((result) => result.status === 'fulfilled');
      const failed = results.length - successful.length;
      const insertedOutcomes = successful.reduce((total, result) => (
        result.status === 'fulfilled' ? total + result.value.insertedOutcomes : total
      ), 0);
      const pendingHorizons = successful.reduce((total, result) => (
        result.status === 'fulfilled' ? total + result.value.pendingHorizons : total
      ), 0);
      const dataGapHorizons = successful.reduce((total, result) => (
        result.status === 'fulfilled' ? total + result.value.dataGapHorizons : total
      ), 0);
      setLearningNotice(
        `已检查最近 ${targets.length} 个统计快照，新增 ${insertedOutcomes} 条已回填结果；`
        + `${pendingHorizons} 个观察窗口仍待目标交易日，${dataGapHorizons} 个已到期窗口待补数据。`,
      );
      await loadLearning();
      if (failed > 0) setLearningError(`${failed} 个快照更新失败，可稍后重试`);
    } catch (caught) {
      setLearningError(caught instanceof Error ? caught.message : '更新到期结果失败');
    } finally {
      setEvaluatingSnapshots(false);
    }
  }, [learningSnapshots, loadLearning]);

  useEffect(() => {
    void load('initial');
    return () => {
      requestSequence.current += 1;
    };
  }, [load]);

  useEffect(() => {
    if (premarketCycle?.state !== 'running') return undefined;
    const delay = Math.min(
      5_000,
      Math.max(2_000, (premarketCycle.retryAfterSeconds ?? 3) * 1_000),
    );
    const timer = window.setTimeout(() => {
      void load('poll');
    }, delay);
    return () => {
      window.clearTimeout(timer);
    };
  }, [
    load,
    premarketCycle?.attemptKey,
    premarketCycle?.cycleKey,
    premarketCycle?.retryAfterSeconds,
    premarketCycle?.state,
    premarketPollSequence,
  ]);

  useEffect(() => {
    void loadLearning();
    return () => {
      learningRequestSequence.current += 1;
    };
  }, [loadLearning]);

  useEffect(() => {
    if (!optionOverviewRequestKey || optionOverviewSymbols.length === 0) return undefined;
    const requestId = optionOverviewRequestSequence.current + 1;
    optionOverviewRequestSequence.current = requestId;
    const optionOverviewRequest = shouldRefreshOptionContext
      ? fetchOpportunityOptionOverview(optionOverviewSymbols, { refresh: true })
      : fetchOpportunityOptionOverview(optionOverviewSymbols);
    void optionOverviewRequest
      .then((response) => {
        if (optionOverviewRequestSequence.current !== requestId) return;
        setOptionOverviewLoad({
          requestKey: optionOverviewRequestKey,
          state: 'settled',
          items: Object.fromEntries(response.items.map((item) => [item.ticker.trim().toUpperCase(), item])),
          message: null,
        });
      })
      .catch((caught) => {
        if (optionOverviewRequestSequence.current !== requestId) return;
        setOptionOverviewLoad({
          requestKey: optionOverviewRequestKey,
          state: 'unavailable',
          items: {},
          message: caught instanceof Error ? caught.message : '期权概览暂不可用。',
        });
      });
    return () => {
      optionOverviewRequestSequence.current += 1;
    };
  }, [optionOverviewRequestKey, optionOverviewSymbols, shouldRefreshOptionContext]);

  useEffect(() => {
    if (!optionWallSummaryRequestKey || optionWallSummarySymbols.length === 0) return undefined;
    const requestId = optionWallSummaryRequestSequence.current + 1;
    optionWallSummaryRequestSequence.current = requestId;
    const optionWallSummaryRequest = shouldRefreshOptionContext
      ? fetchOpportunityOptionWalls(optionWallSummarySymbols, 0, 45, { refresh: true })
      : fetchOpportunityOptionWalls(optionWallSummarySymbols, 0, 45);
    void optionWallSummaryRequest
      .then((response) => {
        if (optionWallSummaryRequestSequence.current !== requestId) return;
        setOptionWallSummaryLoad({
          requestKey: optionWallSummaryRequestKey,
          state: 'settled',
          items: Object.fromEntries(response.items.map((item) => [
            item.ticker.trim().toUpperCase(),
            item,
          ])),
          message: null,
        });
      })
      .catch((caught) => {
        if (optionWallSummaryRequestSequence.current !== requestId) return;
        setOptionWallSummaryLoad({
          requestKey: optionWallSummaryRequestKey,
          state: 'unavailable',
          items: {},
          message: formatOptionWallRequestError(caught),
        });
      });
    return () => {
      optionWallSummaryRequestSequence.current += 1;
    };
  }, [optionWallSummaryRequestKey, optionWallSummarySymbols, shouldRefreshOptionContext]);

  const getCandidateOptionWallSummary = useCallback((
    candidate: OpportunityCandidate,
  ): CandidateOptionWallContext => {
    const normalizedTicker = normalizeSupportedUsOptionUnderlying(candidate.ticker);
    if (!normalizedTicker) return { state: 'unsupported', message: null };
    if (
      optionWallSummaryLoad.requestKey !== optionWallSummaryRequestKey
      || !optionWallSummarySymbols.includes(normalizedTicker)
    ) {
      return { state: 'loading', message: null };
    }
    if (optionWallSummaryLoad.state === 'unavailable') {
      return {
        state: 'unavailable',
        message: optionWallSummaryLoad.message,
      };
    }
    const item = optionWallSummaryLoad.items[normalizedTicker];
    return item
      ? { state: 'settled', item, message: null }
      : {
        state: 'unavailable',
        message: '接口未返回该标的的期权墙。',
      };
  }, [
    optionWallSummaryLoad,
    optionWallSummaryRequestKey,
    optionWallSummarySymbols,
  ]);

  const selectedWallTicker = selectedCandidate
    ? normalizeSupportedUsOptionUnderlying(selectedCandidate.ticker)
    : null;
  const selectedUsesSummaryWall = Boolean(
    selectedWallTicker
    && optionWallScope === '0-45'
    && optionWallSummarySymbols.includes(selectedWallTicker),
  );
  const shouldRefreshOptionWall = Boolean(
    run
    && selectedWallTicker
    && !selectedUsesSummaryWall
    && optionWallRefresh.runId === run.runId
    && optionWallRefresh.ticker === selectedWallTicker
    && optionWallRefresh.dteMin === OPTION_WALL_SCOPES[optionWallScope].dteMin
    && optionWallRefresh.dteMax === OPTION_WALL_SCOPES[optionWallScope].dteMax,
  );
  const selectedOptionWallScope = OPTION_WALL_SCOPES[optionWallScope];
  const optionWallRequestKey = run && selectedWallTicker && !selectedUsesSummaryWall
    ? `${run.runId}:${selectedWallTicker}:${selectedOptionWallScope.dteMin}:${selectedOptionWallScope.dteMax}:${shouldRefreshOptionWall ? `refresh-${optionWallRefresh.sequence}` : 'cached'}`
    : '';

  useEffect(() => {
    if (!optionWallRequestKey || !selectedWallTicker) return undefined;

    const requestId = optionWallRequestSequence.current + 1;
    optionWallRequestSequence.current = requestId;
    const optionWallRequest = shouldRefreshOptionWall
      ? fetchOpportunityOptionWalls(
        [selectedWallTicker],
        selectedOptionWallScope.dteMin,
        selectedOptionWallScope.dteMax,
        { refresh: true },
      )
      : fetchOpportunityOptionWalls(
        [selectedWallTicker],
        selectedOptionWallScope.dteMin,
        selectedOptionWallScope.dteMax,
      );
    void optionWallRequest
      .then((response) => {
        if (optionWallRequestSequence.current !== requestId) return;
        const item = response.items.find(
          (candidate) => candidate.ticker.trim().toUpperCase() === selectedWallTicker,
        );
        setOptionWallDetailLoad({
          requestKey: optionWallRequestKey,
          state: item ? 'settled' : 'unavailable',
          item,
          message: item ? null : '接口未返回所选标的的期权墙。',
        });
      })
      .catch((caught) => {
        if (optionWallRequestSequence.current !== requestId) return;
        setOptionWallDetailLoad({
          requestKey: optionWallRequestKey,
          state: 'unavailable',
          item: undefined,
          message: formatOptionWallRequestError(caught),
        });
      });

    return () => {
      optionWallRequestSequence.current += 1;
    };
  }, [optionWallRequestKey, selectedOptionWallScope, selectedWallTicker, shouldRefreshOptionWall]);

  const selectedOptionWallContext: CandidateOptionWallContext = !selectedWallTicker
    ? { state: 'unsupported', message: null }
    : selectedUsesSummaryWall && selectedCandidate
      ? getCandidateOptionWallSummary(selectedCandidate)
      : optionWallDetailLoad.requestKey !== optionWallRequestKey
        ? { state: 'loading', message: null }
        : {
          state: optionWallDetailLoad.state,
          item: optionWallDetailLoad.item,
          message: optionWallDetailLoad.message,
        };
  const selectedAtmOptionWallContext = (
    selectedCandidate
    && selectedWallTicker
    && optionWallSummarySymbols.includes(selectedWallTicker)
  )
    ? getCandidateOptionWallSummary(selectedCandidate)
    : selectedOptionWallContext;

  const shouldRefreshOptionEvent = Boolean(
    run
    && selectedWallTicker
    && optionEventRefresh.runId === run.runId
    && optionEventRefresh.ticker === selectedWallTicker,
  );
  const optionEventRequestKey = run && selectedWallTicker
    ? `${run.runId}:${selectedWallTicker}:${shouldRefreshOptionEvent ? `refresh-${optionEventRefresh.sequence}` : 'cached'}`
    : '';

  useEffect(() => {
    if (!optionEventRequestKey || !selectedWallTicker) return undefined;

    const requestId = optionEventRequestSequence.current + 1;
    optionEventRequestSequence.current = requestId;
    const optionEventRequest = shouldRefreshOptionEvent
      ? fetchOpportunityOptionEvents([selectedWallTicker], 5, { refresh: true })
      : fetchOpportunityOptionEvents([selectedWallTicker], 5);
    void optionEventRequest
      .then((response) => {
        if (optionEventRequestSequence.current !== requestId) return;
        const item = response.items.find(
          (candidate) => candidate.ticker.trim().toUpperCase() === selectedWallTicker,
        );
        setOptionEventLoad({
          requestKey: optionEventRequestKey,
          state: item ? 'settled' : 'unavailable',
          item,
          message: item ? null : '接口未返回所选标的的异常期权成交。',
        });
      })
      .catch((caught) => {
        if (optionEventRequestSequence.current !== requestId) return;
        setOptionEventLoad({
          requestKey: optionEventRequestKey,
          state: 'unavailable',
          item: undefined,
          message: formatOptionEventRequestError(caught),
        });
      });

    return () => {
      optionEventRequestSequence.current += 1;
    };
  }, [optionEventRequestKey, selectedWallTicker, shouldRefreshOptionEvent]);

  const selectedOptionEventContext: CandidateOptionEventContext = !selectedWallTicker
    ? { state: 'unsupported', message: null }
    : optionEventLoad.requestKey !== optionEventRequestKey
      ? { state: 'loading', message: null }
      : {
        state: optionEventLoad.state,
        item: optionEventLoad.item,
        message: optionEventLoad.message,
      };
  const coreRunReadiness = run?.runReadiness.filter(
    (source) => !['not_available', 'background_only'].includes(source.actionability),
  ) ?? [];
  const optionalRunReadiness = run?.runReadiness.filter(
    (source) => ['not_available', 'background_only'].includes(source.actionability),
  ) ?? [];
  const universeSourceLabel = normalizedSymbols.length > 0
    ? `我的 Watchlist · 扫描 ${normalizedSymbols.length} 只`
    : run
      ? `默认美股池 · 扫描 ${run.universe.length} 只`
      : '默认美股池';
  const dailyRunReadiness = run?.runReadiness.find(
    (source) => source.domain === 'daily_history',
  );
  const baseStageState: ResearchStageState = loading
    ? 'loading'
    : !run
      ? error
        ? 'failed'
        : 'pending'
      : !hasUsableBaseCandidate(run)
        ? 'failed'
        : dailyRunReadiness?.state && dailyRunReadiness.state !== 'ready'
          ? 'degraded'
          : 'ready';
  const overviewStageState: ResearchStageState = !run
    ? 'pending'
    : optionOverviewSymbols.length === 0
      ? 'degraded'
      : optionOverviewLoad.requestKey !== optionOverviewRequestKey
        ? 'loading'
        : optionOverviewLoad.state === 'unavailable'
          ? 'degraded'
          : optionOverviewSymbols.some((ticker) => {
            const item = optionOverviewLoad.items[ticker];
            return !item || item.state !== 'ready';
          })
            ? 'degraded'
            : 'ready';
  const wallStageState: ResearchStageState = !run
    ? 'pending'
    : optionWallSummarySymbols.length === 0
      ? 'degraded'
      : optionWallSummaryLoad.requestKey !== optionWallSummaryRequestKey
        ? 'loading'
        : optionWallSummaryLoad.state === 'unavailable'
          ? 'degraded'
          : optionWallSummarySymbols.some((ticker) => {
            const item = optionWallSummaryLoad.items[ticker];
            return !item || item.state !== 'ready';
          })
            ? 'degraded'
            : 'ready';
  const researchStages = [
    { label: '基础榜', state: baseStageState },
    { label: '期权概览', state: overviewStageState },
    { label: 'Top 5 墙', state: wallStageState },
  ] satisfies Array<{ label: string; state: ResearchStageState }>;
  const activeStageIndex = researchStages.findIndex((stage) => stage.state === 'loading');
  const researchCycleLoading = activeStageIndex >= 0;
  const baseIsStale = dailyRunReadiness?.state === 'stale'
    || Boolean(run?.candidates.some((candidate) => (
      candidate.readiness.some((source) => (
        source.domain === 'daily_history' && source.state === 'stale'
      ))
    )));
  const degradedEnhancements = researchStages
    .slice(1)
    .filter((stage) => stage.state === 'degraded')
    .map((stage) => stage.label);
  const researchStatus = run && loading
    ? '刷新中 · 显示旧结果'
    : run && error
      ? '刷新失败 · 显示旧结果'
      : activeStageIndex >= 0
        ? `阶段 ${activeStageIndex + 1}/3 · ${researchStages[activeStageIndex].label}`
        : baseStageState === 'failed'
          ? '基础扫描阻断'
          : baseIsStale
            ? '基础日线过期 · 仅作背景'
            : degradedEnhancements.length > 0
              ? '基础榜可用 · 增强降级'
              : '研究数据就绪';
  const researchStatusTone = run && (loading || error)
    ? 'text-warning'
    : baseStageState === 'failed' || baseIsStale || degradedEnhancements.length > 0
      ? 'text-warning'
      : activeStageIndex >= 0
        ? 'text-text-2'
        : 'text-text-1';
  const canonicalRetryAvailable = Boolean(
    premarketCycle && canStartManualPremarketRun(premarketCycle),
  );
  const idleRefreshLabel = canonicalRetryAvailable
    ? premarketCycle?.state === 'ready_to_run'
      ? '立即生成'
      : '立即重试'
    : premarketCycle?.state === 'published'
      ? '读取今日研究状态'
      : premarketCycle?.state === 'running'
        ? '读取今日研究状态'
        : premarketError
          ? '重试今日研究状态'
          : '刷新只读预览';
  const refreshButtonLabel = premarketActivity === 'status'
    ? '读取今日研究状态'
    : premarketActivity === 'run'
      ? '正在生成'
      : premarketActivity === 'preview'
        ? '刷新只读预览'
        : activeStageIndex >= 0
          ? `阶段 ${activeStageIndex + 1}/3 · ${researchStages[activeStageIndex].label}`
          : idleRefreshLabel;

  const candidateTableHead = () => (
    <thead className="sticky top-0 z-sticky bg-bg-1">
      <tr className="border-b border-subtle text-left text-caption text-text-3">
        <th className="w-10 px-3 py-2 font-medium">#</th>
        <th className="px-3 py-2 font-medium">标的</th>
        <th className="px-3 py-2 font-medium">结构</th>
        <th className="px-3 py-2 text-right font-medium">量能</th>
        <th className="px-3 py-2 text-right font-medium">IV / Rank</th>
        <th className="px-3 py-2 font-medium">OI / 执行价墙</th>
        <th className="px-3 py-2 font-medium">研究用途</th>
      </tr>
    </thead>
  );

  const candidateRow = (
    candidate: OpportunityCandidate,
    index: number,
    showWallSummary: boolean,
  ) => {
    const overviewTicker = normalizeSupportedUsOptionUnderlying(candidate.ticker) ?? '';
    const overviewIsCurrent = optionOverviewLoad.requestKey === optionOverviewRequestKey;
    const overviewState = !overviewIsCurrent
      ? 'loading' as const
      : optionOverviewLoad.state;
    const overview = overviewIsCurrent ? optionOverviewLoad.items[overviewTicker] : undefined;
    const selected = candidate.candidateId === selectedCandidate?.candidateId;
    const volumeRatio = getNumericEvidence(candidate, 'volume_vs_prior_20d_median');
    const dollarVolumeRatio = getNumericEvidence(candidate, 'dollar_volume_vs_prior_20d_median');
    const researchPlan = candidateResearchPlan(candidate);
    const wallContext = showWallSummary
      ? getCandidateOptionWallSummary(candidate)
      : selected
        ? selectedOptionWallContext
        : null;
    return (
      <tr
        key={candidate.candidateId}
        aria-selected={selected}
        className={`cursor-pointer border-b border-subtle last:border-b-0 ${selected ? 'border-l-2 border-l-[color:var(--text-2)] bg-bg-2' : 'border-l-2 border-l-transparent hover:bg-bg-0'}`}
        onClick={() => selectCandidate(candidate.candidateId)}
      >
        <td className="px-3 py-3 font-mono text-mono-xs text-text-3">{index + 1}</td>
        <td className="px-3 py-3">
          <button
            type="button"
            aria-label={`打开 ${candidate.ticker} 完整机会分析`}
            onClick={(event) => {
              event.stopPropagation();
              navigate(opportunityDetailPath(candidate.ticker, officialSnapshotKey));
            }}
            className="text-left hover:text-text-1"
          >
            <span className="block font-mono text-mono-sm font-semibold text-text-1">{candidate.ticker}</span>
            <span className="mt-0.5 block text-caption text-text-3">
              {formatCompletedBar(candidate.lastCompletedBarAt)}
            </span>
            <span className="mt-1 block text-[11px] text-text-2">完整分析 →</span>
          </button>
        </td>
        <td className="px-3 py-3">
          <div className="text-body-sm text-text-1">{setupSummary(candidate)}</div>
          <div className="mt-0.5 text-caption text-text-3">{trendSummary(candidate)}</div>
        </td>
        <td className="px-3 py-3 text-right">
          <div className="font-mono text-mono-xs text-text-1">量 {formatNumber(volumeRatio, '×')}</div>
          <div className="mt-0.5 font-mono text-mono-xs text-text-3">额 {formatNumber(dollarVolumeRatio, '×')}</div>
        </td>
        <td className="px-3 py-3 text-right font-mono text-mono-xs text-text-2">
          <div>{optionOverviewValue(overview, overviewState, 'iv')}</div>
          <div className="mt-0.5 text-text-3">Rank {optionOverviewValue(overview, overviewState, 'rank')}</div>
        </td>
        <td className="px-3 py-3 font-mono text-mono-xs text-text-3">
          <div>{optionOverviewOiLabel(overview, overviewState)}</div>
          <div className="mt-0.5 text-[11px] text-text-2">
            {wallContext
              ? `墙位 ${optionWallTableLabel(wallContext)}`
              : '进入 Top 5 后自动加载墙位'}
          </div>
        </td>
        <td className="min-w-[210px] px-3 py-3 align-top">
          <StateMark candidate={candidate} />
          <div className="mt-1.5 text-[11px] leading-4 text-text-3">
            <span className="text-text-2">确认</span> {researchPlan.confirmation}
          </div>
          <div className="mt-0.5 text-[11px] leading-4 text-text-3">
            <span className="text-text-2">失效</span> {researchPlan.invalidation}
          </div>
        </td>
      </tr>
    );
  };

  return (
    <section className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-subtle px-4 py-3">
        <div>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 className="text-h2 font-semibold text-text-1">今日机会研究</h2>
            {run && <span className="font-mono text-mono-xs text-text-3">{run.marketDateEt} · ET</span>}
            <span className="text-caption font-medium text-text-2">{universeSourceLabel}</span>
            <button
              type="button"
              onClick={() => navigate('/watchlist')}
              className="text-caption text-text-2 underline-offset-2 hover:text-text-1 hover:underline"
            >
              管理 / 导入自选
            </button>
            <span className="text-caption text-text-3">只读研究，不是交易指令</span>
          </div>
          <p className="mt-1 max-w-4xl text-body-sm text-text-3">
            Top 5 规则匹配候选由上一完整交易日的 EMA8/13、20 日位置与量能确定性计算；计算可复现，策略有效性另行验证。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          disabled={loading || researchCycleLoading}
          onClick={() => void load(canonicalRetryAvailable ? 'run' : 'refresh')}
        >
          <RefreshCw size={14} className={loading || researchCycleLoading ? 'animate-spin' : undefined} />
          {refreshButtonLabel}
        </Button>
      </header>

      <CanonicalPremarketCard
        cycle={premarketCycle}
        error={premarketError}
        activity={premarketActivity}
        baselineSource={baselineSource}
      />

      <div
        className="border-b border-subtle bg-bg-0 px-4 py-2.5"
        aria-label="今日机会研究状态"
        aria-live="polite"
      >
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-text-3">
            <span>请求日 <strong className="font-mono font-medium text-text-2">{run?.marketDateEt ?? '读取中'} ET</strong></span>
            <span>基础证据 <strong className="font-mono font-medium text-text-2">{run ? completedEvidenceDateRange(run) : '读取中'}</strong></span>
            <span>生成 <strong className="font-mono font-medium text-text-2">{run ? `${formatEtDateTime(run.generatedAt)} ET` : '读取中'}</strong></span>
          </div>
          <span className={`text-caption font-medium ${researchStatusTone}`}>{researchStatus}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption" aria-label="研究加载阶段">
          {researchStages.map((stage, index) => {
            const stageTone = stage.state === 'failed' || stage.state === 'degraded'
              ? 'text-warning'
              : stage.state === 'loading'
                ? 'text-text-1'
                : stage.state === 'ready'
                  ? 'text-text-2'
                  : 'text-text-3';
            return (
              <span key={stage.label} className="inline-flex items-center gap-2">
                {index > 0 && <span aria-hidden className="text-text-4">→</span>}
                <span className={stageTone}>
                  {index + 1} {stage.label} · {RESEARCH_STAGE_LABELS[stage.state]}
                </span>
              </span>
            );
          })}
        </div>
        {!researchCycleLoading && degradedEnhancements.length > 0 && (
          <div className="mt-1 text-caption text-warning">
            增强降级：{degradedEnhancements.join('、')}部分或不可用；基础 Top 5 仍可研究。
          </div>
        )}
      </div>

      {run && loading && (
        <div className="border-b border-subtle bg-bg-0 px-4 py-2 text-caption text-text-2" role="status">
          {premarketActivity === 'run'
            ? '正在执行服务端官方盘前研究；'
            : premarketActivity === 'status'
              ? '正在读取今日盘前研究状态；'
              : '正在刷新基础榜预览（请求最长 35 秒）；'}
          当前继续显示
          {' '}{formatEtDateTime(run.generatedAt)} ET 的上一次成功结果。
        </div>
      )}
      {run && error && (
        <div className="border-b border-[color:var(--warn-muted)] bg-bg-0 px-4 py-2 text-caption text-warning" role="status">
          {error}。当前仍显示 {formatEtDateTime(run.generatedAt)} ET 的上一次可用结果。
        </div>
      )}

      <OpportunityLearningPanel
        snapshots={learningSnapshots}
        summary={learningSummary}
        loading={learningLoading}
        evaluating={evaluatingSnapshots}
        error={learningError}
        notice={learningNotice}
        onEvaluate={() => void evaluateMatureOutcomes()}
      />

      {run && coreRunReadiness.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-subtle bg-bg-0 px-4 py-2">
          <span className="text-caption font-medium text-text-2">基础扫描</span>
          {coreRunReadiness.map((source) => (
            <span
              key={source.domain}
              aria-label={`${formatMetric(source.domain)}，${readinessLabel(source.state, source.actionability)}：${source.message}`}
              className="inline-flex items-center gap-2"
            >
              <span className="text-caption text-text-3">{formatMetric(source.domain)}</span>
              <ReadinessMark state={source.state} actionability={source.actionability} />
            </span>
          ))}
        </div>
      )}

      {run && optionalRunReadiness.length > 0 && (
        <details className="border-b border-subtle bg-bg-1 px-4 py-2">
          <summary className="cursor-pointer text-caption text-text-2 hover:text-text-1">
            排名外增强与研究边界
            <span className="ml-2 text-text-3">缺失不阻断基础扫描</span>
          </summary>
          <div className="mt-2 grid gap-2 text-caption sm:grid-cols-2">
            {optionalRunReadiness.map((source) => (
              <div
                key={source.domain}
                className="border-l border-subtle pl-3"
                aria-label={`${formatMetric(source.domain)}，${readinessLabel(source.state, source.actionability)}：${source.message}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="text-text-2">{formatMetric(source.domain)}</span>
                  <ReadinessMark state={source.state} actionability={source.actionability} />
                </div>
                <div className="mt-1 text-text-3">{source.message}</div>
              </div>
            ))}
            <div className="border-l border-subtle pl-3 text-text-3">
              Playbook 只影响个人风格匹配；异常期权成交、期权墙与暗池背景都不会被冒充为基础量价证据。
            </div>
          </div>
        </details>
      )}

      {error && !run ? (
        <div className="px-4 py-6">
          <EmptyState
            title="机会扫描暂不可用"
            description={`${error}。原有复盘和行情不受影响。`}
            size="sm"
            action={{
              label: '重试',
              onClick: () => void load(canonicalRetryAvailable ? 'run' : 'refresh'),
            }}
          />
        </div>
      ) : loading && !run ? (
        <div className="space-y-2 p-4">
          <div className="pb-1 text-caption text-text-3">
            正在读取上一完整交易日行情；35 秒内若数据源未响应会明确失败，不会无限停在扫描中。
          </div>
          {[0, 1, 2].map((item) => (
            <div key={item} className="h-12 animate-pulse rounded-ds-sm bg-bg-2" />
          ))}
        </div>
      ) : !run && premarketCycle?.state === 'running' ? (
        <EmptyState
          title="官方盘前研究正在生成"
          description={premarketCycle.retryAfterSeconds
            ? `服务端仍在运行；请约 ${premarketCycle.retryAfterSeconds} 秒后刷新状态。为避免重复行情扫描，当前不会并发启动普通预览。`
            : '服务端仍在运行。为避免重复行情扫描，当前不会并发启动普通预览。'}
          size="sm"
        />
      ) : !run && premarketCycle && hasImminentPremarketProviderWork(premarketCycle) ? (
        <EmptyState
          title={canonicalRetryAvailable
            ? '官方盘前研究等待生成'
            : '后台盘前研究即将开始'}
          description={canonicalRetryAvailable
            ? (
              premarketCycle.schedulerEnabled
                ? '后台调度正在接管这个官方时点；也可以点击“立即生成”。当前不会并发启动普通预览。'
                : '后台自动研究未启用；可点击“立即生成”。当前不会并发启动普通预览。'
            )
            : '距离后台研究时点不足 60 秒；当前暂停普通预览，避免两套行情任务并发。'}
          size="sm"
        />
      ) : !run || run.candidates.length === 0 ? (
        <EmptyState
          title="当前没有可展示的候选"
          description="请先在 Watchlist 加入股票；若使用服务端股票池，检查 STOCK_LIST 和行情源。"
          size="sm"
        />
      ) : (
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
            <span>
              扫描 {run.universe.length} 个标的 · Top 5 规则匹配候选
              {secondaryCandidates.length > 0 ? ` · 其余 ${secondaryCandidates.length} 个已折叠` : ''}
            </span>
            <span>证据版本 {run.signalVersion} · {run.runType.replaceAll('_', ' ')}</span>
          </div>

          <div className="border-b border-subtle bg-bg-1 px-4 py-2 text-caption text-text-3">
            数据时点：价格、技术结构与相对量能＝上一完整交易日（相对之前 20 个 session）；期权 Call/Put Volume＝当前交易日累计；OI＝上一清算日；IV / Rank＝Moomoo 当前快照。IV 只表示预期波动幅度，不表示方向。
          </div>

          <div className="grid lg:grid-cols-[minmax(0,1.45fr)_minmax(380px,0.9fr)]">
            <div className="max-h-[680px] min-w-0 overflow-auto">
              <table className="w-full min-w-[840px] border-collapse" aria-label="每日机会候选表">
                {candidateTableHead()}
                <tbody>
                  {primaryCandidates.map((candidate, index) => candidateRow(candidate, index, true))}
                </tbody>
              </table>

              {secondaryCandidates.length > 0 && (
                <details className="border-t border-subtle bg-bg-0">
                  <summary className="cursor-pointer px-4 py-2.5 text-caption font-medium text-text-2 hover:bg-bg-1 hover:text-text-1">
                    其余候选 · {secondaryCandidates.length} 个
                    <span className="ml-2 font-normal text-text-3">展开后可选择研究；墙位只为 Top 5 自动加载</span>
                  </summary>
                  <div className="overflow-auto border-t border-subtle">
                    <table className="w-full min-w-[840px] border-collapse" aria-label="其余机会候选表">
                      {candidateTableHead()}
                      <tbody>
                        {secondaryCandidates.map((candidate, index) => (
                          candidateRow(candidate, index + 5, false)
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
            </div>

            {selectedCandidate && (
              <CandidateDetail
                candidate={selectedCandidate}
                optionContext={optionContextFromWall(selectedAtmOptionWallContext)}
                optionEventContext={selectedOptionEventContext}
                optionWallContext={selectedOptionWallContext}
                optionWallScope={optionWallScope}
                onOptionWallScopeChange={selectOptionWallScope}
                signalVersion={run.signalVersion}
                rankingMethod={run.rankingMethod}
                strategyValidationState={run.strategyValidationState}
                strategyValidationMessage={run.strategyValidationMessage}
                officialSnapshotKey={officialSnapshotKey}
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

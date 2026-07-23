import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  ensureOpportunitySnapshot,
  fetchDailyOpportunities,
  evaluateOpportunitySnapshot,
  fetchOpportunityLearningSummary,
  fetchOpportunityOptionContext,
  fetchOpportunityOptionEvents,
  fetchOpportunityOptionOverview,
  fetchOpportunityOptionWalls,
  fetchOpportunitySnapshots,
  normalizeSupportedUsOptionUnderlying,
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
} from '../../types/opportunities';
import { Button, EmptyState } from '../ui';

const STATE_LABELS: Record<OpportunityResearchState, string> = {
  research_ready: '重点研究',
  watch_only: '等待确认',
  context_only: '背景观察',
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
  candidate_blocked: '候选在冻结时处于证据不足状态',
  no_candidates: '本次没有可冻结候选',
};

function formatSnapshotEligibilityReason(reason: string): string {
  if (SNAPSHOT_ELIGIBILITY_LABELS[reason]) return SNAPSHOT_ELIGIBILITY_LABELS[reason];
  if (reason.startsWith('calendar_contract_unavailable:')) return '美股交易日历暂不可用，已停止计入统计';
  return reason.replaceAll('_', ' ');
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

type OptionEventDisplayState = 'loading' | 'settled' | 'unavailable' | 'unsupported';

interface CandidateOptionEventContext {
  item?: OpportunityOptionEventItem;
  state: OptionEventDisplayState;
  message?: string | null;
}

type OptionWallScopePreset = '0-7' | '8-45' | '0-45';

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
  if (!call && !put) return '无有效集中位';
  return `${call ? `C ${call.strike}` : 'C —'} / ${put ? `P ${put.strike}` : 'P —'}`;
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
    <div className="space-y-3" aria-label={`${item.ticker} 期权墙，可用`}>
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
): OpportunityOutcomeProgress {
  return snapshots.reduce<OpportunityOutcomeProgress>((total, snapshot) => {
    const progress = snapshot.outcomeProgress.find(
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
  const directionalSamples = learning
    ? learning.contextHitCount + learning.contextMissCount + learning.neutralCount
    : 0;
  const hitRate = learning?.summaryVisible && learning.contextHitRatePercent !== null
    ? `${learning.contextHitRatePercent.toLocaleString('en-US', { maximumFractionDigits: 1 })}%`
    : null;
  return (
    <div className="min-w-0 px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-mono-sm font-semibold text-text-1">{horizonSessions}D</span>
        <span className="font-mono text-mono-xs text-text-2">
          成熟 {progress.matureCount} · 待观察 {progress.pendingCount}
        </span>
      </div>
      {progress.partialCount > 0 && (
        <div className="mt-1 text-caption text-warning">数据不完整 {progress.partialCount}</div>
      )}
      {(progress.dataGapCount ?? 0) > 0 && (
        <div className="mt-1 text-caption text-warning">已到期，待补数据 {progress.dataGapCount}</div>
      )}
      <div className="mt-1 text-caption text-text-3">
        {hitRate
          ? `标的方向命中率 ${hitRate} · ${learning?.distinctSignalSessions ?? 0} 个独立交易日`
          : `方向样本 ${directionalSamples}/${minimumSummarySamples}；不足门槛不显示命中率`}
      </div>
      <div className="mt-1 text-[11px] text-text-3">
        {learning?.investigationReady
          ? '已达人工调查门槛；仍不会自动调权'
          : `人工调查须满 ${minimumInvestigationSamples} 个方向样本且 20 个独立交易日`}
      </div>
      <div className="mt-1 text-[11px] text-text-3">仅统计同一 setup / Regime / 方向与策略版本。</div>
      {(learning?.excludedQualityCount ?? 0) > 0 && (
        <div className="mt-1 text-[11px] text-warning">
          复权或来源连续性不成立，已排除 {learning?.excludedQualityCount}
        </div>
      )}
    </div>
  );
}

function OpportunityLearningPanel({
  snapshots,
  summary,
  loading,
  saving,
  evaluating,
  error,
  notice,
  onEvaluate,
}: {
  snapshots: OpportunitySnapshot[];
  summary: OpportunityLearningSummaryResponse | null;
  loading: boolean;
  saving: boolean;
  evaluating: boolean;
  error: string | null;
  notice: string | null;
  onEvaluate: () => void;
}) {
  const latestSnapshot = snapshots[0];
  const fiveDay = aggregateOutcomeProgress(snapshots, 5);
  const twentyDay = aggregateOutcomeProgress(snapshots, 20);
  const minimumSummarySamples = summary?.minimumSummarySamples ?? 10;
  const minimumInvestigationSamples = summary?.minimumInvestigationSamples ?? 20;
  const fiveDayLearning = summary?.horizons.find((item) => item.horizonSessions === 5);
  const twentyDayLearning = summary?.horizons.find((item) => item.horizonSessions === 20);
  const hasEvaluableSnapshot = snapshots.some((snapshot) => snapshot.validationEligible);

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
          5D 成熟 {fiveDay.matureCount} / 待观察 {fiveDay.pendingCount}
          {' · '}
          20D 成熟 {twentyDay.matureCount} / 待观察 {twentyDay.pendingCount}
        </span>
      </summary>

      <section
        className="border-t border-subtle px-4 py-3"
        aria-labelledby="opportunity-learning-title"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-4xl text-caption text-text-3">
            清单每天使用上一完整交易日数据自动更新；合法盘前窗口会自动保存当天研究版本，再按 5 / 20 个交易日观察标的路径。
          </p>
          <Button
            variant="ghost"
            size="sm"
            disabled={!hasEvaluableSnapshot || saving || evaluating}
            onClick={onEvaluate}
          >
            {evaluating ? '更新中…' : '更新成熟结果'}
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

        <div className="mt-2 flex flex-wrap items-start justify-between gap-x-4 gap-y-1 text-caption text-text-3">
          <span>
            {loading && snapshots.length === 0
              ? '读取保存记录与学习进度…'
              : saving
                ? '正在检查今天是否处于自动保存窗口…'
              : latestSnapshot
                ? `最近保存 ${latestSnapshot.marketDateEt} · ${latestSnapshot.eligibleCandidateCount}/${latestSnapshot.candidateCount} 个候选可验证`
                : '尚无保存记录；系统只在美股交易日开盘前保存一次，不会在周末或盘后重复创建。'}
          </span>
          {summary && <span>状态 {summary.strategyState.replaceAll('_', ' ')} · 自动调权：关闭</span>}
        </div>
        {latestSnapshot && !latestSnapshot.validationEligible && latestSnapshot.eligibilityReasons.length > 0 && (
          <div className="mt-1 text-caption text-warning">
            最近保存版本暂不可验证：{latestSnapshot.eligibilityReasons.map(formatSnapshotEligibilityReason).join('；')}
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
          onClick={() => navigate(`/regime/opportunity/${candidate.ticker}`)}
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
            研究状态：{candidateStateLabel(candidate)} · 策略验证：
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

export function DailyOpportunityList({ symbols }: { symbols: string[] }) {
  const navigate = useNavigate();
  const [run, setRun] = useState<DailyOpportunityRun | null>(null);
  const runRef = useRef<DailyOpportunityRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const selectedCandidateIdRef = useRef<string | null>(null);
  const [optionContextLoad, setOptionContextLoad] = useState<{
    requestKey: string;
    state: 'settled' | 'unavailable';
    items: Record<string, OpportunityOptionContextItem>;
    message: string | null;
  }>({ requestKey: '', state: 'settled', items: {}, message: null });
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
  const [freezingSnapshot, setFreezingSnapshot] = useState(false);
  const [evaluatingSnapshots, setEvaluatingSnapshots] = useState(false);
  const requestSequence = useRef(0);
  const learningRequestSequence = useRef(0);
  const optionContextRequestSequence = useRef(0);
  const optionOverviewRequestSequence = useRef(0);
  const optionEventRequestSequence = useRef(0);
  const optionWallSummaryRequestSequence = useRef(0);
  const optionWallRequestSequence = useRef(0);
  const ensuredRunIdsRef = useRef(new Set<string>());

  const normalizedSymbols = useMemo(
    () => symbols.map((symbol) => symbol.trim().toUpperCase()).filter(Boolean).slice(0, 20),
    [symbols],
  );

  const selectedCandidate = run?.candidates.find(
    (candidate) => candidate.candidateId === selectedCandidateId,
  ) ?? run?.candidates[0] ?? null;
  const primaryCandidates = useMemo(() => run?.candidates.slice(0, 5) ?? [], [run]);
  const secondaryCandidates = useMemo(() => run?.candidates.slice(5) ?? [], [run]);

  const optionContextSymbols = useMemo(
    () => {
      const ticker = selectedCandidate
        ? normalizeSupportedUsOptionUnderlying(selectedCandidate.ticker)
        : null;
      return ticker ? [ticker] : [];
    },
    [selectedCandidate],
  );
  const shouldRefreshOptionContext = Boolean(
    run && optionContextRefresh.runId === run.runId,
  );
  const optionContextRequestKey = run && optionContextSymbols.length > 0
    ? `${run.runId}:${optionContextSymbols.join(',')}:${shouldRefreshOptionContext ? `refresh-${optionContextRefresh.sequence}` : 'cached'}`
    : '';

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

  const load = useCallback(async (refresh = false) => {
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setLoading(true);
    setError(null);
    try {
      const nextRun = await fetchDailyOpportunities(normalizedSymbols, 10, { refresh });
      if (requestSequence.current === requestId) {
        const previousRun = runRef.current;
        if (
          refresh
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
        selectedCandidateIdRef.current = nextSelectedId;
        setSelectedCandidateId(nextSelectedId);
        setOptionContextRefresh((current) => {
          if (refresh) return { runId: nextRun.runId, sequence: current.sequence + 1 };
          return current.runId ? { ...current, runId: '' } : current;
        });
        setOptionWallRefresh((current) => {
          if (refresh) {
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
          if (refresh) {
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
      if (requestSequence.current === requestId) setLoading(false);
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
    const targets = learningSnapshots.filter((snapshot) => snapshot.validationEligible);
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
        `已检查最近 ${targets.length} 个可验证快照，新增 ${insertedOutcomes} 条成熟结果；`
        + `${pendingHorizons} 个观察窗口仍待目标交易日，${dataGapHorizons} 个已到期窗口待补数据。`,
      );
      await loadLearning();
      if (failed > 0) setLearningError(`${failed} 个快照更新失败，可稍后重试`);
    } catch (caught) {
      setLearningError(caught instanceof Error ? caught.message : '更新成熟结果失败');
    } finally {
      setEvaluatingSnapshots(false);
    }
  }, [learningSnapshots, loadLearning]);

  useEffect(() => {
    void load(false);
    return () => {
      requestSequence.current += 1;
    };
  }, [load]);

  useEffect(() => {
    void loadLearning();
    return () => {
      learningRequestSequence.current += 1;
    };
  }, [loadLearning]);

  useEffect(() => {
    if (!run || loading || ensuredRunIdsRef.current.has(run.runId)) return undefined;
    const ensureSymbols = normalizedSymbols.length > 0 ? normalizedSymbols : run.universe;
    if (ensureSymbols.length === 0) return undefined;
    ensuredRunIdsRef.current.add(run.runId);
    let active = true;
    setFreezingSnapshot(true);
    void ensureOpportunitySnapshot(ensureSymbols, 10)
      .then(async (response) => {
        if (!active) return;
        if (response.snapshot) {
          setLearningSnapshots((current) => [
            response.snapshot as OpportunitySnapshot,
            ...current.filter((item) => item.snapshotKey !== response.snapshot?.snapshotKey),
          ].slice(0, 10));
          await loadLearning();
        }
        if (!active) return;
        const friendlyMessage = response.state === 'saved'
          ? `已自动保存 ${response.marketDateEt} 的研究版本；仅用于后续复盘，不会下单。`
          : response.state === 'existing'
            ? `${response.marketDateEt} 的研究版本已保存，无需重复操作。`
            : response.state === 'outside_window'
              ? '当前不在美股开盘前保存窗口；列表仍使用上一完整交易日数据正常更新。'
              : response.message;
        setLearningNotice(friendlyMessage);
      })
      .catch((caught) => {
        if (!active) return;
        setLearningError(caught instanceof Error ? caught.message : '自动保存检查失败');
      })
      .finally(() => {
        if (active) setFreezingSnapshot(false);
      });
    return () => {
      active = false;
    };
  }, [loadLearning, loading, normalizedSymbols, run]);

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

  useEffect(() => {
    if (!optionContextRequestKey || optionContextSymbols.length === 0) return undefined;

    const requestId = optionContextRequestSequence.current + 1;
    optionContextRequestSequence.current = requestId;
    const optionContextRequest = shouldRefreshOptionContext
      ? fetchOpportunityOptionContext(optionContextSymbols, { refresh: true })
      : fetchOpportunityOptionContext(optionContextSymbols);
    void optionContextRequest
      .then((response) => {
        if (optionContextRequestSequence.current !== requestId) return;
        const items = Object.fromEntries(response.items.map((item) => [
          item.ticker.trim().toUpperCase(),
          item,
        ]));
        setOptionContextLoad({
          requestKey: optionContextRequestKey,
          state: 'settled',
          items,
          message: null,
        });
      })
      .catch((caught) => {
        if (optionContextRequestSequence.current !== requestId) return;
        setOptionContextLoad({
          requestKey: optionContextRequestKey,
          state: 'unavailable',
          items: {},
          message: caught instanceof Error
            ? caught.message
            : '期权上下文暂不可用，不影响基础候选。',
        });
      });

    return () => {
      optionContextRequestSequence.current += 1;
    };
  }, [optionContextRequestKey, optionContextSymbols, shouldRefreshOptionContext]);

  const getCandidateOptionContext = useCallback((candidate: OpportunityCandidate): CandidateOptionContext => {
    const contextIsCurrent = optionContextLoad.requestKey === optionContextRequestKey;
    const normalizedTicker = normalizeSupportedUsOptionUnderlying(candidate.ticker);
    const isInOptionContextBatch = normalizedTicker !== null
      && optionContextSymbols.includes(normalizedTicker);
    const state: OptionContextDisplayState = !isInOptionContextBatch
      ? 'not_scanned'
      : !contextIsCurrent
        ? 'loading'
        : optionContextLoad.state;
    return {
      item: contextIsCurrent ? optionContextLoad.items[normalizedTicker ?? ''] : undefined,
      state,
      message: contextIsCurrent ? optionContextLoad.message : null,
    };
  }, [optionContextLoad, optionContextRequestKey, optionContextSymbols]);

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

  const candidateTableHead = () => (
    <thead className="sticky top-0 z-sticky bg-bg-1">
      <tr className="border-b border-subtle text-left text-caption text-text-3">
        <th className="w-10 px-3 py-2 font-medium">#</th>
        <th className="px-3 py-2 font-medium">标的</th>
        <th className="px-3 py-2 font-medium">结构</th>
        <th className="px-3 py-2 text-right font-medium">量能</th>
        <th className="px-3 py-2 text-right font-medium">IV / Rank</th>
        <th className="px-3 py-2 font-medium">OI / 执行价墙</th>
        <th className="px-3 py-2 font-medium">研究状态</th>
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
              navigate(`/regime/opportunity/${candidate.ticker}`);
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
          disabled={loading}
          onClick={() => void load(true)}
        >
          <RefreshCw size={14} />
          {loading ? (run ? '后台更新中…' : '读取日线中…') : '重新扫描'}
        </Button>
      </header>

      {run && loading && (
        <div className="border-b border-subtle bg-bg-0 px-4 py-2 text-caption text-text-2" role="status">
          正在更新基础日线（请求最长 35 秒）；上一次成功结果继续保留，期权增强不会锁住主列表。
        </div>
      )}
      {run && error && (
        <div className="border-b border-[color:var(--warn-muted)] bg-bg-0 px-4 py-2 text-caption text-warning" role="status">
          {error}。当前仍显示上一次可用结果。
        </div>
      )}

      <OpportunityLearningPanel
        snapshots={learningSnapshots}
        summary={learningSummary}
        loading={learningLoading}
        saving={freezingSnapshot}
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
            action={{ label: '重试', onClick: () => void load(true) }}
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
            数据时点：价格与技术结构＝上一完整交易日；Volume＝本交易日累计；OI＝上一清算日；IV / Rank＝Moomoo 当前快照。IV 只表示预期波动幅度，不表示方向。
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
                optionContext={getCandidateOptionContext(selectedCandidate)}
                optionEventContext={selectedOptionEventContext}
                optionWallContext={selectedOptionWallContext}
                optionWallScope={optionWallScope}
                onOptionWallScopeChange={selectOptionWallScope}
                signalVersion={run.signalVersion}
                rankingMethod={run.rankingMethod}
                strategyValidationState={run.strategyValidationState}
                strategyValidationMessage={run.strategyValidationMessage}
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

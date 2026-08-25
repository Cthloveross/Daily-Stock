import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Crosshair,
  Database,
  SkipForward,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  createPositionEpisodeAiReview,
  fetchLatestPositionEpisodeReviewAnnotation,
  fetchPositionEpisodeDetail,
  fetchPositionEpisodeReviewAnnotationHistory,
  savePositionEpisodeReviewAnnotation,
} from '../api/journal';
import { stocksApi } from '../api/stocks';
import { parseApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert } from '../components/common/ApiErrorAlert';
import { InlineAlert } from '../components/common/InlineAlert';
import { CandlestickChart, type Candle } from '../components/charts/CandlestickChart';
import { EpisodePlaybookLinksPanel } from '../components/journal/review/EpisodePlaybookLinksPanel';
import { TradeLogicDraftPanel } from '../components/journal/review/TradeLogicDraftPanel';
import {
  buildEmaOverlay,
  filterCandlesByUsTradingSession,
  findEvidenceAtChartTime,
  isTimeInUsTradingSession,
  mapEvidenceToCandles,
  type EvidenceChartLink,
  type UsTradingSession,
} from '../components/journal/review/episodeReviewMapping';
import {
  buildOrderAwareEvidenceMarkers,
  consolidateEvidenceLinks,
  type EvidenceOrderGroup,
} from '../components/journal/review/evidenceConsolidation';
import { findNextReviewEpisode } from '../components/journal/review/nextReviewEpisode';
import {
  DecisionSnapshotPanel,
  EpisodeWhatIfPanel,
  QuadrantChip,
} from '../components/journal/review/episodeReviewZones';
import { ExcursionDiagnostics } from '../components/journal/review/ExcursionDiagnostics';
import { fetchEpisodeExcursion } from '../api/journalReviewFlow';
import type { EpisodeVerdictBlock } from '../types/journalReviewFlow';
import {
  clearEpisodeReviewDraft,
  compactEpisodeReviewUserContext,
  emptyEpisodeReviewDraft,
  episodeReviewAnnotationToDraft,
  hasEpisodeReviewDraft,
  loadEpisodeReviewDraft,
} from '../components/journal/review/episodeReviewDraft';
import {
  journalReturnPath,
  positionReviewPath,
} from '../components/journal/review/journalReviewRouting';
import type {
  PositionEpisodeAiReviewResponse,
  PositionEpisodeDetailResponse,
  PositionEpisodeReviewAnnotation,
  PositionEpisodeReviewWorkspaceDraft,
} from '../types/journal';
import type { StockHistory, StockKLine, Timeframe } from '../types/stockHistory';

type ReviewTimeframe = '1m' | '2m' | '5m' | '15m' | '30m' | '1h' | 'daily';
type HistoryMode = 'intraday' | 'daily' | 'unavailable' | 'empty';

interface ReviewHistoryState {
  history: StockHistory | null;
  candles: Candle[];
  period: Timeframe;
  mode: HistoryMode;
  fallbackReason?: string;
  explicit: boolean;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const INTRADAY_MAX_AGE_DAYS = 60;
const REVIEW_TIMEFRAMES: { value: ReviewTimeframe; label: string; longLabel: string }[] = [
  { value: '1m', label: '1m', longLabel: '1 分钟' },
  { value: '2m', label: '2m', longLabel: '2 分钟' },
  { value: '5m', label: '5m', longLabel: '5 分钟' },
  { value: '15m', label: '15m', longLabel: '15 分钟' },
  { value: '30m', label: '30m', longLabel: '30 分钟' },
  { value: '1h', label: '1h', longLabel: '1 小时' },
  { value: 'daily', label: '1D', longLabel: '日线' },
];

function timeframeLabel(period: Timeframe): string {
  return REVIEW_TIMEFRAMES.find((item) => item.value === period)?.longLabel ?? period;
}

function parsePositiveInt(value?: string | null): number | undefined {
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function toEt(value?: string | null, includeSeconds = false): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...(includeSeconds ? { second: '2-digit' as const } : {}),
    hour12: false,
  }).format(parsed);
}

function normaliseDecimal(value: string): { sign: string; whole: string; fraction: string } {
  const raw = value.trim();
  const sign = raw.startsWith('-') ? '−' : raw.startsWith('+') ? '+' : '';
  const unsigned = raw.replace(/^[+-]/, '');
  const [whole = '0', fraction = ''] = unsigned.split('.', 2);
  return {
    sign,
    whole: whole.replace(/\B(?=(\d{3})+(?!\d))/g, ','),
    fraction,
  };
}

function formatDecimal(value?: string | null, money = false): string {
  if (value == null || !value.trim()) return '—';
  const { sign, whole, fraction } = normaliseDecimal(value);
  const significantFraction = fraction.replace(/0+$/, '');
  const displayFraction = money ? significantFraction.padEnd(2, '0') : significantFraction;
  return `${sign}${money ? '$' : ''}${whole}${displayFraction ? `.${displayFraction}` : ''}`;
}

function moneyTone(value?: string | null): string {
  if (!value) return 'text-text-2';
  if (value.trim().startsWith('-')) return 'text-down-strong';
  if (/^\+?0(?:\.0+)?$/.test(value.trim())) return 'text-text-2';
  return 'text-up-strong';
}

function eventRoleLabel(role: string): string {
  const labels: Record<string, string> = {
    open: '开仓',
    add: '加仓',
    reduce: '减仓',
    close: '平仓',
  };
  return labels[role] ?? role;
}

function directionLabel(direction: string): string {
  if (direction === 'long') return '多头';
  if (direction === 'short') return '空头';
  return direction;
}

function lifecycleLabel(status: string): string {
  if (status === 'open') return '证据窗口末未归零';
  if (status === 'closed') return '证据窗口内已归零';
  return status;
}

function klineTime(kline: StockKLine, period: Timeframe): number | string | null {
  if (period === 'daily' || period === 'weekly' || period === 'monthly') {
    const day = kline.date.slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : null;
  }
  const parsed = Date.parse(kline.date);
  return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
}

function historyToCandles(history: StockHistory): Candle[] {
  const candles: Candle[] = [];
  for (const kline of history.data) {
    const time = klineTime(kline, history.period);
    if (time == null) continue;
    candles.push({
      time,
      open: kline.open,
      high: kline.high,
      low: kline.low,
      close: kline.close,
      ...(kline.volume == null ? {} : { volume: kline.volume }),
    });
  }
  return candles.sort((left, right) => {
    const leftTime = typeof left.time === 'number' ? left.time : Date.parse(left.time) / 1000;
    const rightTime = typeof right.time === 'number' ? right.time : Date.parse(right.time) / 1000;
    return leftTime - rightTime;
  });
}

function reviewStart(detail: PositionEpisodeDetailResponse): number {
  const evidenceTimes = detail.evidence
    .map((item) => Date.parse(item.evidenceTime))
    .filter(Number.isFinite);
  const openedAt = Date.parse(detail.item.openedAt);
  return Math.min(...evidenceTimes, Number.isFinite(openedAt) ? openedAt : Date.now());
}

function historyRange(history: StockHistory): string {
  const start = history.coverageStart ?? history.data[0]?.date;
  const end = history.coverageEnd ?? history.data[history.data.length - 1]?.date;
  if (!start && !end) return '覆盖范围未知';
  return `${start ?? '—'} – ${end ?? '—'}`;
}

function HistoryNotice({ state }: { state: ReviewHistoryState }) {
  if (state.mode === 'daily') {
    return (
      <InlineAlert
        variant="warning"
        title={state.explicit ? '当前查看日线' : '分钟行情无法覆盖这次回合，已自动降级为日线'}
        message={(
          <span>
            {state.fallbackReason} 日线只能帮助观察大方向，<strong>不能用于精确判断入场、MFE 或 MAE</strong>。
          </span>
        )}
      />
    );
  }
  if (state.mode === 'unavailable') {
    return (
      <InlineAlert
        variant="warning"
        title={`${timeframeLabel(state.period)}行情无法覆盖这次回合`}
        message={(
          <span>
            {state.fallbackReason}
            {state.explicit && ' 已保留你选择的周期，没有自动换成其他周期，也不会把邻近 K 线伪装成精确证据。'}
          </span>
        )}
      />
    );
  }
  if (state.mode === 'empty') {
    return (
      <InlineAlert
        variant="warning"
        title="底层行情暂不可用"
        message={`证据时间线仍可审阅，但${timeframeLabel(state.period)}数据当前不可用，无法绘制 K 线，也不能判断精确入场、MFE 或 MAE。`}
      />
    );
  }
  return null;
}

function sideLabelOf(side: 'buy' | 'sell' | 'unknown'): string {
  return side === 'buy' ? '买入' : side === 'sell' ? '卖出' : '方向未知';
}

function sideToneOf(side: 'buy' | 'sell' | 'unknown'): string {
  return side === 'buy' ? 'text-chart-5' : side === 'sell' ? 'text-down-strong' : 'text-text-2';
}

function ProxyBadge({ proxy }: { proxy: boolean }) {
  return (
    <span className={`rounded-full border px-2 py-1 text-caption ${
      proxy
        ? 'border-warn-strong/30 bg-warn-subtle text-warn-strong'
        : 'border-chart-5/30 bg-chart-5/10 text-chart-5'
    }`}>
      {proxy ? '订单时间代理' : '真实逐笔成交'}
    </span>
  );
}

function EvidenceFillButton({
  link,
  badge,
  selected,
  selectedRef,
  usTradingSession,
  onSelect,
}: {
  link: EvidenceChartLink;
  badge: React.ReactNode;
  selected: boolean;
  selectedRef?: React.MutableRefObject<HTMLButtonElement | null>;
  usTradingSession?: UsTradingSession;
  onSelect: (link: EvidenceChartLink) => void;
}) {
  const evidence = link.evidence;
  const outsideSelectedSession = usTradingSession != null
    && !isTimeInUsTradingSession(evidence.evidenceTime, usTradingSession);
  return (
    <button
      ref={selected ? selectedRef : undefined}
      type="button"
      aria-pressed={selected}
      onClick={() => onSelect(link)}
      className={`w-full rounded-ds-md border p-3 text-left transition-colors ${
        selected
          ? 'border-accent bg-accent-subtle-bg'
          : 'border-subtle bg-bg-1 hover:border-default hover:bg-bg-2'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          {badge}
          <div>
            <div className={`text-body-sm font-medium ${sideToneOf(link.cashFlowSide)}`}>
              {sideLabelOf(link.cashFlowSide)}
            </div>
            <div className="text-caption text-text-3">回合角色：{eventRoleLabel(evidence.eventRole)}</div>
            <div className="font-mono text-mono-xs text-text-3">{toEt(evidence.evidenceTime, true)} ET</div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <ProxyBadge proxy={link.orderTimeProxy} />
          {link.chartTime == null && (
            <span className="rounded-full border border-down-strong/30 bg-down-subtle px-2 py-1 text-caption text-down-strong">
              {outsideSelectedSession ? '所选时段外' : '行情缺口'}
            </span>
          )}
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2">
        <div>
          <dt className="text-caption text-text-3">分配数量</dt>
          <dd className="font-mono text-mono-xs text-text-1">{formatDecimal(evidence.allocatedQuantity)}</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">现金流</dt>
          <dd className="font-mono text-mono-xs text-text-1">{formatDecimal(evidence.allocatedCashFlow, true)}</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">费用</dt>
          <dd className="font-mono text-mono-xs text-text-1">{formatDecimal(evidence.allocatedFee, true)}</dd>
        </div>
      </dl>
      <p className="mt-2 font-mono text-[11px] text-text-4">
        {evidence.brokerFillObservationId != null
          ? `fill observation ${evidence.brokerFillObservationId}`
          : `order observation ${evidence.brokerOrderObservationId ?? '—'}`}
      </p>
    </button>
  );
}

function ConsolidatedOrderRow({
  group,
  index,
  selectedEvidenceKey,
  usTradingSession,
  onSelect,
}: {
  group: EvidenceOrderGroup;
  index: number;
  selectedEvidenceKey?: string | null;
  usTradingSession?: UsTradingSession;
  onSelect: (link: EvidenceChartLink) => void;
}) {
  const [fillsExpanded, setFillsExpanded] = useState(false);
  const selected = group.links.some(
    (link) => link.evidence.evidenceKey === selectedEvidenceKey,
  );
  const aggregate = group.aggregate;
  const orderObservationId = group.links[0].evidence.brokerOrderObservationId;
  return (
    <div className={`rounded-ds-md border transition-colors ${
      selected ? 'border-accent bg-accent-subtle-bg' : 'border-subtle bg-bg-1'
    }`}>
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(group.links[0])}
        className="w-full p-3 text-left"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full border border-subtle font-mono text-mono-xs text-text-3">
              {index + 1}
            </span>
            <div>
              <div className={`text-body-sm font-medium ${sideToneOf(group.cashFlowSide)}`}>
                {sideLabelOf(group.cashFlowSide)} · 一笔订单
              </div>
              <div className="text-caption text-text-3">
                回合角色：{group.eventRoles.map(eventRoleLabel).join(' / ')}
              </div>
              <div className="font-mono text-mono-xs text-text-3">
                {toEt(aggregate?.firstEvidenceTime, true)} → {toEt(aggregate?.lastEvidenceTime, true)} ET
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <span className="rounded-full border border-accent-subtle-border bg-accent-subtle-bg px-2 py-1 text-caption text-accent">
              分 {group.links.length} 笔成交
            </span>
            <ProxyBadge proxy={group.orderTimeProxy} />
            {group.chartTime == null && (
              <span className="rounded-full border border-down-strong/30 bg-down-subtle px-2 py-1 text-caption text-down-strong">
                行情缺口
              </span>
            )}
          </div>
        </div>
        <dl className="mt-3 grid grid-cols-3 gap-2">
          <div>
            <dt className="text-caption text-text-3">总数量</dt>
            <dd className="font-mono text-mono-xs text-text-1">
              {formatDecimal(aggregate?.totalQuantity)}
            </dd>
          </div>
          <div>
            <dt className="text-caption text-text-3">现金流加权均价</dt>
            <dd className="font-mono text-mono-xs text-text-1">
              {formatDecimal(aggregate?.weightedAveragePrice, true)}
            </dd>
          </div>
          <div>
            <dt className="text-caption text-text-3">合计费用</dt>
            <dd className={`font-mono text-mono-xs ${aggregate?.feeComplete ? 'text-text-1' : 'text-warn-strong'}`}>
              {aggregate?.feeComplete ? formatDecimal(aggregate?.totalFee, true) : '费用不完整'}
            </dd>
          </div>
        </dl>
        <p className="mt-2 font-mono text-[11px] text-text-4">
          order observation {orderObservationId ?? '—'} · 均价 = ∑|现金流| ÷ (∑数量 × 合约乘数)
        </p>
      </button>
      <div className="border-t border-subtle px-3 py-2">
        <button
          type="button"
          aria-expanded={fillsExpanded}
          className="inline-flex items-center gap-1.5 text-caption text-text-2 hover:text-text-1"
          onClick={() => setFillsExpanded((value) => !value)}
        >
          {fillsExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {fillsExpanded ? '收起逐笔成交' : `展开 ${group.links.length} 笔逐笔成交`}
        </button>
        {fillsExpanded && (
          <ol className="mt-2 space-y-2" aria-label="逐笔成交明细">
            {group.links.map((link, memberIndex) => (
              <li key={link.evidence.evidenceKey}>
                <EvidenceFillButton
                  link={link}
                  badge={(
                    <span className="flex h-6 shrink-0 items-center justify-center rounded-full border border-subtle px-2 font-mono text-[10px] text-text-3">
                      第 {memberIndex + 1} 笔
                    </span>
                  )}
                  selected={link.evidence.evidenceKey === selectedEvidenceKey}
                  usTradingSession={usTradingSession}
                  onSelect={onSelect}
                />
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function EvidenceTimeline({
  groups,
  selectedEvidenceKey,
  usTradingSession,
  onSelect,
}: {
  groups: EvidenceOrderGroup[];
  selectedEvidenceKey?: string | null;
  usTradingSession?: UsTradingSession;
  onSelect: (link: EvidenceChartLink) => void;
}) {
  const selectedRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    selectedRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [selectedEvidenceKey]);

  if (!groups.length) {
    return (
      <div className="rounded-ds-md border border-dashed border-default p-6 text-center text-body-sm text-text-3">
        这个回合没有可展示的 execution evidence；K 线不会生成伪造的进出场标记。
      </div>
    );
  }

  return (
    <ol className="space-y-2" aria-label="成交证据时间线">
      {groups.map((group, index) => (
        <li key={group.groupKey}>
          {group.consolidated ? (
            <ConsolidatedOrderRow
              group={group}
              index={index}
              selectedEvidenceKey={selectedEvidenceKey}
              usTradingSession={usTradingSession}
              onSelect={onSelect}
            />
          ) : (
            <EvidenceFillButton
              link={group.links[0]}
              badge={(
                <span className="flex h-6 w-6 items-center justify-center rounded-full border border-subtle font-mono text-mono-xs text-text-3">
                  {index + 1}
                </span>
              )}
              selected={group.links[0].evidence.evidenceKey === selectedEvidenceKey}
              selectedRef={selectedRef}
              usTradingSession={usTradingSession}
              onSelect={onSelect}
            />
          )}
        </li>
      ))}
    </ol>
  );
}

function reviewHistoryDays(period: ReviewTimeframe, ageDays: number): number {
  const maximum = ({
    '1m': 60,
    '2m': 60,
    '5m': 60,
    '15m': 60,
    '30m': 60,
    '1h': 730,
    daily: 1000,
  } satisfies Record<ReviewTimeframe, number>)[period];
  const minimum = period === 'daily' ? 120 : 7;
  return Math.min(maximum, Math.max(minimum, ageDays + (period === 'daily' ? 45 : 3)));
}

function coversAllEvidence(
  detail: PositionEpisodeDetailResponse,
  candles: Candle[],
  period: Timeframe,
): boolean {
  if (!detail.evidence.length) return true;
  const links = mapEvidenceToCandles(detail.evidence, candles, period);
  return links.every((link) => link.chartTime != null);
}

async function loadReviewHistory(
  detail: PositionEpisodeDetailResponse,
  requestedPeriod: ReviewTimeframe | null,
): Promise<ReviewHistoryState> {
  const earliest = reviewStart(detail);
  const ageDays = Math.max(1, Math.ceil((Date.now() - earliest) / DAY_MS));
  let fallbackReason = '';

  if (requestedPeriod) {
    const history = await stocksApi.getHistory(
      detail.item.instrument.underlying,
      requestedPeriod,
      reviewHistoryDays(requestedPeriod, ageDays),
    );
    const candles = historyToCandles(history);
    if (!candles.length) {
      return {
        history,
        candles: [],
        period: requestedPeriod,
        mode: 'empty',
        fallbackReason: `${timeframeLabel(requestedPeriod)}没有返回 K 线。`,
        explicit: true,
      };
    }
    if (!coversAllEvidence(detail, candles, requestedPeriod)) {
      return {
        history,
        candles: [],
        period: requestedPeriod,
        mode: 'unavailable',
        fallbackReason: `${timeframeLabel(requestedPeriod)}没有覆盖全部 execution evidence。`,
        explicit: true,
      };
    }
    return {
      history,
      candles,
      period: requestedPeriod,
      mode: requestedPeriod === 'daily' ? 'daily' : 'intraday',
      fallbackReason: requestedPeriod === 'daily' ? '你主动选择了日线。' : undefined,
      explicit: true,
    };
  }

  if (ageDays <= INTRADAY_MAX_AGE_DAYS) {
    try {
      const intradayDays = Math.min(INTRADAY_MAX_AGE_DAYS, Math.max(7, ageDays + 3));
      const intraday = await stocksApi.getHistory(
        detail.item.instrument.underlying,
        '5m',
        intradayDays,
      );
      const candles = historyToCandles(intraday);
      const allEvidenceCovered = coversAllEvidence(detail, candles, '5m');
      if (candles.length > 0 && allEvidenceCovered) {
        return { history: intraday, candles, period: '5m', mode: 'intraday', explicit: false };
      }
      fallbackReason = candles.length === 0
        ? '数据源没有返回 5 分钟 K 线。'
        : '5 分钟 K 线没有覆盖全部 execution evidence。';
    } catch {
      fallbackReason = '5 分钟行情请求失败。';
    }
  } else {
    fallbackReason = '这次回合早于当前分钟行情的可靠可用窗口。';
  }

  const dailyDays = Math.min(1000, Math.max(120, ageDays + 45));
  const daily = await stocksApi.getHistory(
    detail.item.instrument.underlying,
    'daily',
    dailyDays,
  );
  const candles = historyToCandles(daily);
  if (!candles.length) {
    return { history: daily, candles: [], period: 'daily', mode: 'empty', fallbackReason, explicit: false };
  }
  if (!coversAllEvidence(detail, candles, 'daily')) {
    return {
      history: daily,
      candles: [],
      period: 'daily',
      mode: 'unavailable',
      fallbackReason: `${fallbackReason} 日线也没有覆盖全部 execution evidence。`,
      explicit: false,
    };
  }
  return { history: daily, candles, period: 'daily', mode: 'daily', fallbackReason, explicit: false };
}

function formatReturn(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '未知';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function ReviewMarkdown({ markdown, emptyMessage }: { markdown: string; emptyMessage: string }) {
  if (!markdown.trim()) return <p className="text-body-sm text-text-3">{emptyMessage}</p>;
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h3 className="mb-2 mt-4 text-h2 text-text-1 first:mt-0">{children}</h3>,
        h2: ({ children }) => <h3 className="mb-2 mt-4 text-h3 text-text-1 first:mt-0">{children}</h3>,
        h3: ({ children }) => <h4 className="mb-1 mt-3 text-body font-semibold text-text-1">{children}</h4>,
        p: ({ children }) => <p className="my-2 text-body-sm leading-relaxed text-text-2">{children}</p>,
        ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 text-body-sm text-text-2">{children}</ul>,
        ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 text-body-sm text-text-2">{children}</ol>,
        strong: ({ children }) => <strong className="font-semibold text-text-1">{children}</strong>,
        code: ({ children }) => <code className="rounded bg-bg-2 px-1 py-0.5 font-mono text-mono-xs text-text-1">{children}</code>,
        img: ({ alt }) => (
          <span className="rounded bg-bg-2 px-1 py-0.5 text-caption text-text-3">
            [外部图片已隐藏{alt ? `：${alt}` : ''}]
          </span>
        ),
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}

function AiReviewPanel({
  episodeId,
  buildId,
  userContext,
}: {
  episodeId: number;
  buildId: number;
  userContext: PositionEpisodeReviewWorkspaceDraft;
}) {
  const navigate = useNavigate();
  const [review, setReview] = useState<PositionEpisodeAiReviewResponse | null>(null);
  const [loadingMode, setLoadingMode] = useState<'evidence' | 'model' | null>(null);
  const [lastRequestedMode, setLastRequestedMode] = useState<'evidence' | 'model'>('evidence');
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [lastDurationSeconds, setLastDurationSeconds] = useState<number | null>(null);
  const [submittedContextSignature, setSubmittedContextSignature] = useState<string | null>(null);
  const loading = loadingMode != null;
  const compactUserContext = compactEpisodeReviewUserContext(userContext);
  const userContextFieldCount = Object.keys(compactUserContext).length;
  const currentContextSignature = JSON.stringify(compactUserContext);

  useEffect(() => {
    if (!loading) return undefined;
    const startedAt = performance.now();
    const updateElapsed = () => {
      setElapsedSeconds(Math.max(0, Math.floor((performance.now() - startedAt) / 1000)));
    };
    updateElapsed();
    const intervalId = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(intervalId);
  }, [loading]);

  const generate = async (mode: 'evidence' | 'model') => {
    const startedAt = performance.now();
    const requestedContext = compactEpisodeReviewUserContext(userContext);
    const requestedContextSignature = JSON.stringify(requestedContext);
    setElapsedSeconds(0);
    setLastDurationSeconds(null);
    setLoadingMode(mode);
    setLastRequestedMode(mode);
    setError(null);
    try {
      const response = await createPositionEpisodeAiReview(
        episodeId,
        buildId,
        mode === 'model',
        Object.keys(requestedContext).length > 0 ? requestedContext : undefined,
      );
      setReview(response);
      setSubmittedContextSignature(requestedContextSignature);
    } catch (reason) {
      setError(parseApiError(reason));
    } finally {
      setLastDurationSeconds(Math.max(0.1, (performance.now() - startedAt) / 1000));
      setLoadingMode(null);
    }
  };

  const context = review?.marketContext;
  const modelEnhanced = review?.analysisMode === 'model_enhanced';
  const modelUnavailable = review?.analysisMode === 'deterministic'
    && review.dataState === 'llm_unavailable';
  const evidenceMarkdown = review?.evidenceMarkdown?.trim()
    || review?.analysisMarkdown?.trim()
    || '';
  const modelAnalysisMarkdown = review?.modelAnalysisMarkdown?.trim() || '';
  const usingLegacyAnalysisFallback = review != null && !review.evidenceMarkdown?.trim();

  return (
    <section className="card-base overflow-hidden" aria-label="复盘助手">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-subtle px-4 py-4">
        <div>
          <div className="flex items-center gap-2 text-label uppercase tracking-label text-text-3">
            <Sparkles size={14} /> 复盘助手
          </div>
          <h2 className="mt-1 text-h2 text-text-1">证据复盘始终可用，模型只做增强</h2>
          <p className="mt-1 text-caption text-text-3">先用本地规则整理事实；需要时再调用已配置模型。两种方式都不会修改账本或下单。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={review ? 'btn-secondary' : 'btn-primary'}
            disabled={loading}
            onClick={() => void generate('evidence')}
          >
            {loadingMode === 'evidence' ? '整理证据中…' : review ? '重新生成证据复盘' : '生成证据复盘'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={loading}
            onClick={() => void generate('model')}
          >
            {loadingMode === 'model' ? '模型增强中…' : '尝试模型增强'}
          </button>
        </div>
      </div>

      <div className="space-y-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-ds-md border border-subtle bg-bg-2 px-3 py-2 text-caption text-text-3">
          <span>
            {userContextFieldCount > 0
              ? `证据复盘会将 ${userContextFieldCount} 项草稿发送给本机服务；模型增强还会发送给你配置的第三方模型供应商。空字段不发送。`
              : '当前没有交易逻辑上下文；生成时只使用成交与行情证据。'}
          </span>
          {review && submittedContextSignature !== currentContextSignature && (
            <span className="text-warn-strong">草稿已更新，重新生成后才会进入复盘。</span>
          )}
        </div>

        <div className="grid gap-2 md:grid-cols-3">
          <div className="rounded-ds-md border border-up-strong/20 bg-up-subtle p-3">
            <div className="text-label uppercase tracking-label text-up-strong">事实</div>
            <p className="mt-1 text-caption text-text-2">不可变 execution evidence、费用、构建质量和带来源的同期行情。</p>
          </div>
          <div className="rounded-ds-md border border-accent-subtle-border bg-accent-subtle-bg p-3">
            <div className="text-label uppercase tracking-label text-accent">推断</div>
            <p className="mt-1 text-caption text-text-2">只有模型增强会提出带置信度的可能解释；本地证据复盘不猜交易动机。</p>
          </div>
          <div className="rounded-ds-md border border-warn-strong/20 bg-warn-subtle p-3">
            <div className="text-label uppercase tracking-label text-warn-strong">未知</div>
            <p className="mt-1 text-caption text-text-2">未覆盖行情、未验证边界、交易动机和当时不可观测的信息会保持未知。</p>
          </div>
        </div>

        <InlineAlert
          variant="warning"
          title="分析边界"
          message="单笔交易不能证明策略存在 edge；模型不能从成交单独还原你的原始动机。系统保持 Moomoo 只读，不会下单。"
        />

        {loading && (
          <div className="rounded-ds-md border border-subtle bg-bg-2 p-8 text-center text-body-sm text-text-3" role="status">
            <p className="text-text-2">
              {loadingMode === 'evidence'
                ? '正在并行读取成交证据、底层与基准行情…'
                : review && elapsedSeconds < 4
                  ? '证据复盘仍然保留；正在连接复盘专用模型…'
                  : review
                    ? '证据复盘仍然保留；正在受限预算内等待模型增强…'
                    : elapsedSeconds < 4
                      ? '正在整理本地证据并连接复盘专用模型…'
                      : '正在受限预算内等待模型；若增强失败仍会返回本地证据复盘。'}
            </p>
            <p className="mt-2 font-mono text-mono-xs text-text-4">
              {loadingMode === 'evidence'
                ? `已等待 ${elapsedSeconds}s · 不调用外部模型`
                : `已等待 ${elapsedSeconds}s · 每模型 10s · 路由总预算 16s · 服务端硬截止 20s`}
            </p>
          </div>
        )}

        {error && (
          <div>
            <ApiErrorAlert
              error={error}
              actionLabel={lastRequestedMode === 'model' ? '重试模型增强' : '重试证据复盘'}
              onAction={() => void generate(lastRequestedMode)}
            />
            <p className="mt-2 text-caption text-text-3">网络请求失败不会影响 K 线、原始 evidence 或已经生成的证据复盘。</p>
          </div>
        )}

        {!loading && !error && !review && (
          <div className="rounded-ds-md border border-dashed border-default px-4 py-8 text-center">
            <p className="text-body-sm text-text-2">尚未生成证据复盘</p>
            <p className="mt-1 text-caption text-text-3">先点击“生成证据复盘”；它固定使用仓位回合 #{episodeId} 与构建 #{buildId}，无需模型 API。</p>
          </div>
        )}

        {review && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-ds-md border border-subtle bg-bg-2 p-3">
              <div>
                <div className={`inline-flex rounded-full border px-2 py-1 text-caption ${
                  modelEnhanced
                    ? 'border-accent/30 bg-accent-subtle-bg text-accent'
                    : 'border-up-strong/25 bg-up-subtle text-up-strong'
                }`}>
                  {modelEnhanced ? '已使用模型增强' : '本地证据引擎'}
                </div>
                <p className="mt-2 text-caption text-text-3">
                  {modelEnhanced
                    ? '模型输出已经通过结构门禁；事实边界和未知项仍需保留。'
                    : '下面的内容由确定性规则生成，不依赖 Gemini、GPT 或其他外部模型。'}
                </p>
              </div>
              {!modelEnhanced && (
                <button type="button" className="btn-ghost" onClick={() => navigate('/settings')}>
                  打开模型设置
                </button>
              )}
            </div>

            {modelUnavailable && (
              <InlineAlert
                variant="warning"
                title="模型增强未成功，已自动保留证据复盘"
                message="当前模型未配置、超时或供应商不可用。网站内自动调用 GPT 需要服务端 API Key；当前 Codex/ChatGPT 会话不会被网页直接借用。"
              />
            )}

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
                <div className="text-caption text-text-3">市场状态</div>
                <div className="mt-1 text-body-sm text-text-1">{context?.regimeLabel ?? '未知'}</div>
              </div>
              <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
                <div className="text-caption text-text-3">底层区间收益</div>
                <div className={`mt-1 font-mono text-mono-sm ${moneyTone(String(context?.underlyingReturnPct ?? 0))}`}>
                  {formatReturn(context?.underlyingReturnPct)}
                </div>
              </div>
              <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
                <div className="text-caption text-text-3">基准 {context?.benchmark ?? '未知'}</div>
                <div className={`mt-1 font-mono text-mono-sm ${moneyTone(String(context?.benchmarkReturnPct ?? 0))}`}>
                  {formatReturn(context?.benchmarkReturnPct)}
                </div>
              </div>
              <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
                <div className="text-caption text-text-3">相对收益</div>
                <div className={`mt-1 font-mono text-mono-sm ${moneyTone(String(context?.relativeReturnPct ?? 0))}`}>
                  {formatReturn(context?.relativeReturnPct)}
                </div>
              </div>
            </div>

            <section className="overflow-hidden rounded-ds-md border border-up-strong/25 bg-bg-1" aria-labelledby="evidence-review-layer-title">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-up-strong/20 bg-up-subtle px-4 py-3">
                <div>
                  <div className="text-label uppercase tracking-label text-up-strong">Evidence layer</div>
                  <h3 id="evidence-review-layer-title" className="mt-1 text-h3 text-text-1">证据事实层</h3>
                  <p className="mt-1 text-caption text-text-3">由成交、质量边界和行情事实确定性生成；模型增强不能替换这一层。</p>
                </div>
                <div className="text-right text-caption text-text-3">
                  <div>市场窗口 {context?.windowStart ? toEt(context.windowStart) : '未知'} – {context?.windowEnd ? toEt(context.windowEnd) : '未知'} ET</div>
                  <div className="mt-1">
                    {lastDurationSeconds != null && `本次用时 ${lastDurationSeconds.toFixed(1)}s · `}
                    生成于 {toEt(review.generatedAt, true)} ET
                  </div>
                </div>
              </div>
              <div className="p-4">
                <ReviewMarkdown markdown={evidenceMarkdown} emptyMessage="证据引擎没有返回事实层正文。" />
                {usingLegacyAnalysisFallback && (
                  <p className="mt-3 border-t border-subtle pt-3 text-[11px] text-text-4">
                    当前响应使用旧版 analysisMarkdown 兼容显示；服务升级后会返回独立证据层字段。
                  </p>
                )}
              </div>
            </section>

            {modelAnalysisMarkdown && (
              <section className="overflow-hidden rounded-ds-md border border-accent-subtle-border bg-bg-1" aria-labelledby="model-review-layer-title">
                <div className="border-b border-accent-subtle-border bg-accent-subtle-bg px-4 py-3">
                  <div className="text-label uppercase tracking-label text-accent">Model inference layer</div>
                  <h3 id="model-review-layer-title" className="mt-1 text-h3 text-text-1">模型推断层</h3>
                  <p className="mt-1 text-caption text-text-3">来自你配置的第三方模型供应商；属于可能解释与建议，不是 execution evidence。</p>
                </div>
                <div className="p-4">
                  <ReviewMarkdown markdown={modelAnalysisMarkdown} emptyMessage="模型没有返回推断正文。" />
                </div>
              </section>
            )}

            {(context?.provenance?.length ?? 0) > 0 && (
              <details className="rounded-ds-md border border-subtle bg-bg-2 p-3">
                <summary className="cursor-pointer text-body-sm text-text-2">市场数据来源与计算依据</summary>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-caption text-text-3">
                  {context?.provenance.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </details>
            )}

            {review.warnings.length > 0 && (
              <ul className="rounded-ds-md border border-warn-strong/25 bg-warn-subtle p-3 text-caption text-warn-strong">
                {review.warnings.map((warning) => <li key={warning}>• {warning}</li>)}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}

const JournalEpisodeReviewPage: React.FC = () => {
  const navigate = useNavigate();
  const params = useParams<{ episodeId: string }>();
  const [searchParams] = useSearchParams();
  const episodeId = parsePositiveInt(params.episodeId);
  const buildId = parsePositiveInt(searchParams.get('build_id'));
  const [detail, setDetail] = useState<PositionEpisodeDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [detailError, setDetailError] = useState<ParsedApiError | null>(null);
  const [historyState, setHistoryState] = useState<ReviewHistoryState | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<ParsedApiError | null>(null);
  const [requestedPeriod, setRequestedPeriod] = useState<ReviewTimeframe | null>(null);
  const [tradingSession, setTradingSession] = useState<UsTradingSession>('regular');
  const [selectedEvidenceKey, setSelectedEvidenceKey] = useState<string | null>(null);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [tradeLogicDraft, setTradeLogicDraft] = useState<PositionEpisodeReviewWorkspaceDraft>(
    () => emptyEpisodeReviewDraft(),
  );
  const [reviewAnnotation, setReviewAnnotation] = useState<PositionEpisodeReviewAnnotation | null>(null);
  const [reviewAnnotationLoading, setReviewAnnotationLoading] = useState(false);
  const [reviewRestoreNotice, setReviewRestoreNotice] = useState<string | null>(null);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [reviewSaveError, setReviewSaveError] = useState<string | null>(null);
  const [reviewSaveNotice, setReviewSaveNotice] = useState<string | null>(null);
  const [reviewHistory, setReviewHistory] = useState<PositionEpisodeReviewAnnotation[] | null>(null);
  const [reviewHistoryLoading, setReviewHistoryLoading] = useState(false);
  const [reviewHistoryError, setReviewHistoryError] = useState<string | null>(null);
  const [queueBusy, setQueueBusy] = useState<'skip' | 'draft' | 'complete' | null>(null);
  const [queueNotice, setQueueNotice] = useState<string | null>(null);
  // 区②折叠揭示：布局强制顺序（蓝图 17 §三(b)）——结果默认折叠，
  // 揭示后才可见盈亏/K 线/成交路径，且区③标签才可编辑。
  const [resultsRevealed, setResultsRevealed] = useState(false);
  const [verdictBlock, setVerdictBlock] = useState<EpisodeVerdictBlock | null>(null);

  const returnToJournal = useCallback(() => {
    navigate(journalReturnPath(searchParams));
  }, [navigate, searchParams]);

  useEffect(() => {
    if (episodeId == null) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setDetailLoading(true);
      setDetailError(null);
      setDetail(null);
      setHistoryState(null);
      setRequestedPeriod(null);
      setTradingSession('regular');
      setTradeLogicDraft(emptyEpisodeReviewDraft());
      setReviewAnnotation(null);
      setReviewAnnotationLoading(false);
      setReviewRestoreNotice(null);
      setReviewSaving(false);
      setReviewSaveError(null);
      setReviewSaveNotice(null);
      setReviewHistory(null);
      setReviewHistoryLoading(false);
      setReviewHistoryError(null);
      setQueueBusy(null);
      setQueueNotice(null);
      setResultsRevealed(false);
      setVerdictBlock(null);
    });
    void fetchPositionEpisodeDetail(episodeId, buildId)
      .then((response) => {
        if (!cancelled) {
          setDetail(response);
          setSelectedEvidenceKey(response.evidence[0]?.evidenceKey ?? null);
          setTradeLogicDraft(loadEpisodeReviewDraft(response.build.id, episodeId));
          // 区①/③/④的机械判定（车道、四象限、两个反事实）由服务端计算。
          void fetchEpisodeExcursion(episodeId, response.build.id)
            .then((excursionResponse) => {
              if (!cancelled) setVerdictBlock(excursionResponse.verdict ?? null);
            })
            .catch(() => {
              if (!cancelled) setVerdictBlock(null);
            });
        }
      })
      .catch((reason) => {
        if (!cancelled) setDetailError(parseApiError(reason));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [buildId, episodeId, reloadVersion]);

  useEffect(() => {
    if (!detail || episodeId == null) return;
    let cancelled = false;
    setReviewAnnotationLoading(true);
    setReviewRestoreNotice(null);
    void fetchLatestPositionEpisodeReviewAnnotation(episodeId, detail.build.id)
      .then((response) => {
        if (cancelled) return;
        const latest = response.annotation ?? null;
        const localDraft = loadEpisodeReviewDraft(detail.build.id, episodeId);
        setReviewAnnotation(latest);
        if (hasEpisodeReviewDraft(localDraft)) {
          setTradeLogicDraft(localDraft);
          setReviewRestoreNotice(
            latest
              ? `检测到本机未提交草稿，已优先保留；服务器最新为版本 #${latest.revision}。`
              : '已恢复本机未提交草稿；服务器尚无复盘版本。',
          );
        } else if (latest) {
          setTradeLogicDraft(episodeReviewAnnotationToDraft(latest));
          setReviewRestoreNotice(`已从服务器恢复复盘版本 #${latest.revision}。`);
        } else {
          setTradeLogicDraft(emptyEpisodeReviewDraft());
          setReviewRestoreNotice('服务器尚无复盘版本；输入后先作为本机未提交草稿保存。');
        }
      })
      .catch((reason) => {
        if (cancelled) return;
        const parsed = parseApiError(reason);
        setReviewRestoreNotice(`服务器复盘读取失败：${parsed.message}。本机草稿仍可继续编辑。`);
      })
      .finally(() => {
        if (!cancelled) setReviewAnnotationLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [detail, episodeId]);

  useEffect(() => {
    // K 线属于区②（结果）：揭示前不加载，也不请求任何行情。
    if (!detail || !resultsRevealed) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setHistoryLoading(true);
      setHistoryError(null);
      setHistoryState(null);
    });
    void loadReviewHistory(detail, requestedPeriod)
      .then((state) => {
        if (!cancelled) setHistoryState(state);
      })
      .catch((reason) => {
        if (!cancelled) {
          setHistoryError(parseApiError(reason));
          setHistoryState({
            history: null,
            candles: [],
            period: requestedPeriod ?? '5m',
            mode: 'empty',
            explicit: requestedPeriod != null,
          });
        }
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [detail, requestedPeriod, resultsRevealed]);

  const chartCandles = useMemo(() => {
    const candles = historyState?.candles ?? [];
    if (historyState?.mode !== 'intraday') return candles;
    return filterCandlesByUsTradingSession(candles, tradingSession);
  }, [historyState?.candles, historyState?.mode, tradingSession]);

  const links = useMemo(() => mapEvidenceToCandles(
    detail?.evidence ?? [],
    chartCandles,
    historyState?.period ?? '5m',
  ), [chartCandles, detail?.evidence, historyState?.period]);

  const evidenceGroups = useMemo(() => consolidateEvidenceLinks(
    links,
    historyState?.period ?? '5m',
    detail?.item.instrument.contractMultiplier,
  ), [detail?.item.instrument.contractMultiplier, historyState?.period, links]);

  const markers = useMemo(
    () => buildOrderAwareEvidenceMarkers(evidenceGroups, selectedEvidenceKey),
    [evidenceGroups, selectedEvidenceKey],
  );
  const emaOverlays = useMemo(() => {
    return [
      buildEmaOverlay(chartCandles, 8, '#22d3ee', 'EMA 8'),
      buildEmaOverlay(chartCandles, 13, '#f59e0b', 'EMA 13'),
    ];
  }, [chartCandles]);
  const selectedLink = links.find((link) => link.evidence.evidenceKey === selectedEvidenceKey);
  const activePeriod = requestedPeriod ?? historyState?.period ?? '5m';

  const selectEvidence = (link: EvidenceChartLink) => {
    setSelectedEvidenceKey(link.evidence.evidenceKey);
  };

  const selectFromChart = (time: number | string, markerId?: string) => {
    if (markerId != null) {
      // 合并 marker 的 id 是订单组键；点击选中该合并行（以首笔 fill 为锚点）。
      const group = evidenceGroups.find((candidate) => candidate.groupKey === markerId);
      if (group) {
        setSelectedEvidenceKey(group.links[0].evidence.evidenceKey);
        return;
      }
    }
    const evidence = markerId
      ? detail?.evidence.find((item) => item.evidenceKey === markerId)
      : findEvidenceAtChartTime(links, time);
    if (evidence) setSelectedEvidenceKey(evidence.evidenceKey);
  };

  const loadSavedReviewHistory = () => {
    if (!detail || episodeId == null || reviewHistoryLoading || reviewHistory !== null) return;
    setReviewHistoryLoading(true);
    setReviewHistoryError(null);
    void fetchPositionEpisodeReviewAnnotationHistory(episodeId, detail.build.id)
      .then((response) => setReviewHistory(response.items))
      .catch((reason) => {
        setReviewHistory(null);
        setReviewHistoryError(parseApiError(reason).message);
      })
      .finally(() => setReviewHistoryLoading(false));
  };

  const saveReviewWorkspace = async (
    reviewStatus: 'in_progress' | 'completed',
  ): Promise<boolean> => {
    if (!detail || episodeId == null) return false;
    const selfAuthoredFieldCount = Object.keys(
      compactEpisodeReviewUserContext(tradeLogicDraft),
    ).length;
    if (reviewStatus === 'completed' && selfAuthoredFieldCount === 0) {
      setReviewSaveError('至少填写一项用户自述后才能标记复盘完成');
      return false;
    }
    if (!hasEpisodeReviewDraft(tradeLogicDraft)) {
      setReviewSaveError('至少填写一项内容后才能保存复盘');
      return false;
    }

    setReviewSaving(true);
    setReviewSaveError(null);
    setReviewSaveNotice(null);
    try {
      const response = await savePositionEpisodeReviewAnnotation(episodeId, {
        buildId: detail.build.id,
        reviewStatus,
        ...tradeLogicDraft,
      });
      setReviewAnnotation(response.annotation);
      clearEpisodeReviewDraft(detail.build.id, episodeId);
      setReviewRestoreNotice(null);
      setReviewSaveNotice(
        response.idempotentReplay
          ? `内容未变化，沿用服务器版本 #${response.annotation.revision}。`
          : `已保存服务器版本 #${response.annotation.revision}（${reviewStatus === 'completed' ? '已完成' : '进行中'}）。`,
      );
      try {
        const savedHistory = await fetchPositionEpisodeReviewAnnotationHistory(
          episodeId,
          detail.build.id,
        );
        setReviewHistory(savedHistory.items);
        setReviewHistoryError(null);
      } catch {
        setReviewHistory(null);
        setReviewHistoryError('复盘已保存，但版本历史暂时读取失败');
      }
      return true;
    } catch (reason) {
      setReviewSaveError(parseApiError(reason).message);
      return false;
    } finally {
      setReviewSaving(false);
    }
  };

  /**
   * 解析并跳转「下一笔」：与复盘工作台 CTA 共用 `findNextReviewEpisode`
   * （进行中 → top_loss 未开始 → 最近未开始），排除当前回合；无命中时
   * 如实提示队列已清空并提供返回列表入口。
   */
  const goToNextEpisode = useCallback(async (): Promise<void> => {
    const next = await findNextReviewEpisode({
      buildId,
      excludeEpisodeId: episodeId,
    });
    if (next) {
      setQueueNotice(null);
      navigate(positionReviewPath(next.id, searchParams));
    } else {
      setQueueNotice('全部回合已完成复盘；当前构建没有下一笔待复盘回合。');
    }
  }, [buildId, episodeId, navigate, searchParams]);

  const skipToNextEpisode = async () => {
    if (queueBusy || reviewSaving) return;
    setQueueBusy('skip');
    setQueueNotice(null);
    try {
      await goToNextEpisode();
    } catch (reason) {
      setQueueNotice(`定位下一笔失败：${parseApiError(reason).message}`);
    } finally {
      setQueueBusy(null);
    }
  };

  const saveAndNextEpisode = async (reviewStatus: 'in_progress' | 'completed') => {
    if (queueBusy || reviewSaving) return;
    setQueueBusy(reviewStatus === 'completed' ? 'complete' : 'draft');
    setQueueNotice(null);
    try {
      const saved = await saveReviewWorkspace(reviewStatus);
      // 保存失败（校验或网络）时停留在当前页并展示错误，不吞掉草稿。
      if (saved) await goToNextEpisode();
    } catch (reason) {
      setQueueNotice(`定位下一笔失败：${parseApiError(reason).message}`);
    } finally {
      setQueueBusy(null);
    }
  };

  if (episodeId == null) {
    return (
      <div className="mx-auto max-w-3xl p-4 lg:p-6">
        <button type="button" className="btn-ghost" onClick={returnToJournal}>← 返回仓位复盘</button>
        <ApiErrorAlert
          className="mt-4"
          error={parseApiError(new Error('仓位回合编号无效。'))}
        />
      </div>
    );
  }

  if (detailLoading && !detail) {
    return (
      <div className="mx-auto max-w-[1440px] p-4 lg:p-6">
        <button type="button" className="btn-ghost" onClick={returnToJournal}>← 返回仓位复盘</button>
        <div className="card-base mt-4 p-12 text-center text-body-sm text-text-3">正在加载完整复盘…</div>
      </div>
    );
  }

  if (detailError && !detail) {
    return (
      <div className="mx-auto max-w-3xl p-4 lg:p-6">
        <button type="button" className="btn-ghost" onClick={returnToJournal}>← 返回仓位复盘</button>
        <ApiErrorAlert
          className="mt-4"
          error={detailError}
          actionLabel="重新加载"
          onAction={() => setReloadVersion((value) => value + 1)}
        />
      </div>
    );
  }

  if (!detail) return null;

  const item = detail.item;
  const conditional = !item.quality.pnlSummaryEligible;
  const displayedNet = item.lifecycleStatus === 'closed' ? item.realizedPnlNet : null;

  return (
    <div className="mx-auto max-w-[1720px] space-y-4 p-4 lg:p-6">
      <div
        className="sticky top-0 z-30 -mx-4 border-b border-subtle bg-bg-0/95 px-4 py-2.5 backdrop-blur lg:-mx-6 lg:px-6"
        aria-label="复盘快捷操作"
        data-testid="review-action-bar"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 text-body-sm text-text-3 hover:text-text-1"
            onClick={returnToJournal}
          >
            <ArrowLeft size={15} /> 返回仓位复盘
          </button>
          <h1 className="font-mono text-h3 text-text-1">{item.instrument.rawSymbol}</h1>
          <span className="rounded-full border border-subtle bg-bg-2 px-2.5 py-1 text-caption text-text-2">
            {lifecycleLabel(item.lifecycleStatus)}
          </span>
          {conditional && (
            <span className="rounded-full border border-warn-strong/30 bg-warn-subtle px-2.5 py-1 text-caption text-warn-strong">
              条件性结果 · 不进 Headline
            </span>
          )}
          {/* 反 outcome-bias：结果（含 Net）折叠在区②，揭示后才显示。 */}
          <span className={`rounded-full border px-2.5 py-1 text-caption ${
            resultsRevealed
              ? 'border-accent-subtle-border bg-accent-subtle-bg text-accent'
              : 'border-subtle bg-bg-2 text-text-3'
          }`}>
            {resultsRevealed ? '结果已揭示' : '盲评中 · 结果已折叠'}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="btn-ghost inline-flex items-center gap-1.5"
              disabled={queueBusy != null || reviewSaving}
              onClick={() => void skipToNextEpisode()}
            >
              <SkipForward size={14} /> {queueBusy === 'skip' ? '定位下一笔…' : '跳过，下一笔 →'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={queueBusy != null || reviewSaving || !resultsRevealed}
              onClick={() => void saveAndNextEpisode('in_progress')}
            >
              {queueBusy === 'draft' ? '保存草稿中…' : '保存草稿并下一笔'}
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={queueBusy != null || reviewSaving || !resultsRevealed}
              onClick={() => void saveAndNextEpisode('completed')}
            >
              {queueBusy === 'complete' ? '完成复盘中…' : '完成复盘并下一笔'}
            </button>
          </div>
        </div>
        <p className="mt-1 text-caption text-text-3">
          {item.instrument.underlying} 底层行情 · 构建 #{detail.build.id} · 不可变 execution evidence · Moomoo 只读 · 不下单 · 下一笔优先级：进行中 → 亏损最大的未复盘 → 最近未开始
        </p>
        {queueNotice && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-caption text-warn-strong" role="status">
            <span>{queueNotice}</span>
            <button
              type="button"
              className="text-accent underline-offset-2 hover:underline"
              onClick={returnToJournal}
            >
              返回仓位列表
            </button>
          </div>
        )}
        {reviewSaveError && (
          <p className="mt-2 text-caption text-down-strong" role="alert">
            保存未成功：{reviewSaveError}
          </p>
        )}
      </div>

      {item.quality.assumedFlatUnverified && (
        <InlineAlert
          variant="warning"
          title="期初持仓边界未验证"
          message="历史左边界没有券商持仓快照证明；即使另有当前时点的券商持仓快照，也不能倒推这个历史证据窗口的期初持仓。这里展示的是期初空仓假设下的条件性复盘，不能被人工操作改成 Verified，也不会进入 Headline。"
        />
      )}
      {item.lifecycleStatus === 'open' && (
        <InlineAlert
          variant="info"
          title="“证据窗口末未归零”不等于券商当前持仓"
          message="系统没有证据窗口末之后的完整成交与持仓快照；证据窗口末数量只表示回放到该 as-of 时点时尚未归零。"
        />
      )}

      {/* 区①：决策时快照——默认展开，只列进场时刻可知的信息。 */}
      <DecisionSnapshotPanel
        detail={detail}
        verdict={verdictBlock}
        annotation={reviewAnnotation}
      />

      {/* 区②：结果揭示——默认折叠（代码层约束，反 outcome-bias）。 */}
      {!resultsRevealed && (
        <section
          className="card-base p-6 text-center"
          aria-label="结果揭示"
          data-testid="reveal-gate"
        >
          <h2 className="text-h2 text-text-1">区② 结果揭示（默认折叠）</h2>
          <p className="mx-auto mt-2 max-w-xl text-body-sm text-text-3">
            先读决策快照、在心里给过程结论；点击揭示后才显示盈亏、K 线、成交路径与
            AI 分析，区③双轨标签也才可编辑。先决策快照、后结果——顺序是机制，不是排版。
          </p>
          <button
            type="button"
            className="btn-primary mt-4"
            data-testid="reveal-results"
            onClick={() => setResultsRevealed(true)}
          >
            揭示结果
          </button>
        </section>
      )}

      {resultsRevealed && (
      <>
      {/*
        ≥1280px（xl）双列：左列 = 摘要 + K 线与事件 + 成交证据时间线 + 偏移
        诊断（区②）；右列 = 四象限 + 复盘工作单（区③，sticky）。窄屏单列且
        工作单紧跟顶部操作条（order-1），证据区随后。
      */}
      <section
        className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(400px,460px)]"
        aria-label="复盘主区"
        data-testid="review-columns"
      >
        <div className="order-2 min-w-0 space-y-4 xl:order-1" data-testid="review-evidence-column">

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" aria-label="回合摘要">
        {[
          ['方向', directionLabel(item.direction), '合约仓位方向'],
          ['开仓时间 ET', toEt(item.openedAt), '第一条分配证据'],
          ['期权开仓均价', formatDecimal(item.averageEntryPrice, true), '只显示，不画入底层价轴'],
          ['期权平仓均价', formatDecimal(item.averageExitPrice, true), item.closedAt ? toEt(item.closedAt) : '证据窗口末未归零'],
          ['费用', formatDecimal(item.totalFee, true), '后端持久化结果'],
          ['Net', formatDecimal(displayedNet, true), item.lifecycleStatus === 'open' ? '证据窗口末未归零，不显示' : conditional ? '条件值' : 'Verified'],
        ].map(([label, value, caption]) => (
          <div key={label} className="rounded-ds-md border border-subtle bg-bg-1 p-3">
            <div className="text-caption text-text-3">{label}</div>
            <div className={`mt-1 break-words font-mono text-mono-sm ${label === 'Net' ? moneyTone(displayedNet) : 'text-text-1'}`}>
              {value}
            </div>
            <div className="mt-1 text-[11px] text-text-4">{caption}</div>
          </div>
        ))}
      </section>

      {historyState && <HistoryNotice state={historyState} />}
      {historyError && <ApiErrorAlert error={historyError} />}

        <div className="card-base min-w-0 overflow-hidden">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-subtle px-4 py-3">
            <div>
              <div className="flex items-center gap-2 text-label uppercase tracking-label text-text-3">
                <Crosshair size={14} /> 底层 K 线与事件
              </div>
              <p className="mt-1 text-caption text-text-3">纵轴只显示 {item.instrument.underlying} 底层价格；期权成交价绝不画入这个价轴。</p>
            </div>
            <div className="flex flex-col items-end gap-2">
              <div className="inline-flex rounded-ds-md border border-subtle bg-bg-2 p-1" role="group" aria-label="K 线周期">
                {REVIEW_TIMEFRAMES.map((timeframe) => (
                  <button
                    key={timeframe.value}
                    type="button"
                    aria-label={`切换到${timeframe.longLabel} K 线`}
                    aria-pressed={activePeriod === timeframe.value}
                    className={`rounded-ds-sm px-2.5 py-1 font-mono text-mono-xs transition-colors ${
                      activePeriod === timeframe.value
                        ? 'bg-accent text-white'
                        : 'text-text-3 hover:bg-bg-3 hover:text-text-1'
                    }`}
                    onClick={() => setRequestedPeriod(timeframe.value)}
                  >
                    {timeframe.label}
                  </button>
                ))}
              </div>
              {historyState?.mode === 'intraday' && (
                <div className="inline-flex rounded-ds-md border border-subtle bg-bg-2 p-1" role="group" aria-label="美股交易时段">
                  {([
                    ['regular', '常规时段', '09:30–16:00 ET'],
                    ['extended', '含盘前盘后', '04:00–20:00 ET'],
                  ] as const).map(([value, label, hours]) => (
                    <button
                      key={value}
                      type="button"
                      aria-label={`使用${label} ${hours}`}
                      aria-pressed={tradingSession === value}
                      className={`rounded-ds-sm px-2.5 py-1 text-caption transition-colors ${
                        tradingSession === value
                          ? 'bg-accent text-white'
                          : 'text-text-3 hover:bg-bg-3 hover:text-text-1'
                      }`}
                      onClick={() => setTradingSession(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {historyState?.history && (
                <div className="text-right text-caption text-text-3">
                  <div className="font-mono text-text-2">{timeframeLabel(historyState.period)} · {historyState.history.source ?? '未知数据源'}</div>
                  <div>{historyRange(historyState.history)}</div>
                  {historyState.history.lastBarAt && <div>最后一根 {historyState.history.lastBarAt}</div>}
                  {historyState.mode === 'intraday' && (
                    <div className="mt-1 text-text-2">
                      EMA 口径：{tradingSession === 'regular' ? '常规时段 09:30–16:00 ET' : '含盘前盘后 04:00–20:00 ET'} · {chartCandles.length} 根
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          <div className="p-3">
            {historyLoading && (
              <div className="flex h-[480px] items-center justify-center text-body-sm text-text-3">正在匹配底层行情与 execution evidence…</div>
            )}
            {!historyLoading && historyState && chartCandles.length > 0 && (
              <CandlestickChart
                data={chartCandles}
                overlays={emaOverlays}
                markers={markers}
                focusedTime={selectedLink?.chartTime}
                onTimeSelect={selectFromChart}
                height={480}
                timeZone="America/New_York"
                timeZoneLabel="ET"
              />
            )}
            {!historyLoading && historyState && chartCandles.length === 0 && (
              <div className="flex h-[360px] flex-col items-center justify-center rounded-ds-md border border-dashed border-default px-6 text-center">
                <TriangleAlert size={24} className="text-warn-strong" />
                <p className="mt-3 text-body-sm text-text-2">
                  {historyState.candles.length > 0 ? '所选交易时段没有可展示的 K 线' : '没有可覆盖这次回合的底层 K 线'}
                </p>
                <p className="mt-1 max-w-lg text-caption text-text-3">不会移动 evidence 时间，也不会用邻近日线伪装成精确入场点。</p>
              </div>
            )}
          </div>
          <div className="border-t border-subtle px-4 py-3 text-caption text-text-3">
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              <span><span className="font-semibold text-[#22d3ee]">━</span> EMA 8（底层收盘价）</span>
              <span><span className="font-semibold text-[#f59e0b]">━</span> EMA 13（底层收盘价）</span>
              <span><span className="text-chart-5">▲</span> 买入（分配现金流 &lt; 0）</span>
              <span><span className="text-down-strong">▼</span> 卖出（分配现金流 &gt; 0）</span>
              <span><span className="text-warn-strong">■</span> 方块表示订单时间代理（非逐笔成交时间）</span>
              <span><span className="text-[#f5d06f]">●</span> 当前聚焦 evidence</span>
            </div>
            <p className="mt-2 text-[11px] text-text-4">
              EMA 首值使用前 N 根收盘价的 SMA，之后按 α=2/(N+1) 递推；切换交易时段会基于当前可见 K 线重新计算。历史 K 线显示最终收盘值，不代表成交瞬间已知的未完成 bar 值。
            </p>
            <p className="mt-1 text-[11px] text-text-4">横轴与十字光标：纽约时间 ET（America/New_York，自动处理夏令时）。</p>
          </div>
        </div>

        <aside className="card-base min-h-0 overflow-hidden">
          <div className="border-b border-subtle px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-label uppercase tracking-label text-text-3">Execution evidence</div>
                <h2 className="mt-1 text-h2 text-text-1">成交证据时间线</h2>
              </div>
              <span className="font-mono text-mono-xs text-text-3">
                {evidenceGroups.length} 项 · {links.length} 条证据
              </span>
            </div>
            <p className="mt-1 text-caption text-text-3">
              点击证据聚焦 K 线；点击带事件的 K 线 bar 会高亮对应证据。同一订单的分批成交已合并展示，可展开逐笔审阅。
            </p>
          </div>
          <div className="max-h-[590px] overflow-y-auto p-3">
            <EvidenceTimeline
              groups={evidenceGroups}
              selectedEvidenceKey={selectedEvidenceKey}
              usTradingSession={historyState?.mode === 'intraday' ? tradingSession : undefined}
              onSelect={selectEvidence}
            />
          </div>
        </aside>

        {/* 偏移诊断（区②）：只读 MAE/MFE + 聚合散点，永久免责。 */}
        <ExcursionDiagnostics
          key={`excursion:${episodeId}:${detail.build.id}`}
          episodeId={episodeId}
          buildId={detail.build.id}
        />
        </div>

        <div className="order-1 min-w-0 xl:order-2" data-testid="review-worksheet-column">
          <div className="xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)] xl:overflow-y-auto xl:pr-1">
            {/* 区③：四象限落格（服务端判定；「侥幸」标红）+ 双轨标签。 */}
            {verdictBlock && (
              <div className="mb-3 flex flex-wrap items-center gap-2 rounded-ds-md border border-subtle bg-bg-1 px-3 py-2" data-testid="quadrant-strip">
                <span className="text-caption text-text-3">四象限（过程轴 × 结果轴，永不合成）</span>
                <QuadrantChip
                  quadrant={verdictBlock.quadrant}
                  label={verdictBlock.quadrantLabel}
                />
              </div>
            )}
            <TradeLogicDraftPanel
              key={`draft:${episodeId}:${detail.build.id}`}
              episodeId={episodeId}
              buildId={detail.build.id}
              draft={tradeLogicDraft}
              onChange={(nextDraft) => {
                setTradeLogicDraft(nextDraft);
                setReviewRestoreNotice(null);
                setReviewSaveNotice(null);
                setReviewSaveError(null);
              }}
              annotation={reviewAnnotation}
              annotationLoading={reviewAnnotationLoading}
              saving={reviewSaving}
              saveError={reviewSaveError}
              saveNotice={reviewSaveNotice}
              restoreNotice={reviewRestoreNotice}
              history={reviewHistory}
              historyLoading={reviewHistoryLoading}
              historyError={reviewHistoryError}
              onSave={(status) => {
                void saveReviewWorkspace(status);
              }}
              onLoadHistory={loadSavedReviewHistory}
            />
          </div>
        </div>
      </section>

      <AiReviewPanel
        key={`ai:${episodeId}:${detail.build.id}`}
        episodeId={episodeId}
        buildId={detail.build.id}
        userContext={tradeLogicDraft}
      />

      {/* 区④：机械反事实（只读，两个、永不叠加）。 */}
      <EpisodeWhatIfPanel verdict={verdictBlock} />
      </>
      )}

      <EpisodePlaybookLinksPanel
        key={`playbook-links:${episodeId}:${detail.build.id}`}
        episodeId={episodeId}
        buildId={detail.build.id}
      />

      <details className="card-base p-4" data-testid="evidence-details">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-body-sm text-text-2">
          <Database size={14} /> 证据明细 · 证据窗口、窗口末投影与技术来源（默认折叠）
        </summary>
        <div className="mt-4 space-y-4">
          <section className="rounded-ds-md border border-accent/20 bg-accent/5 p-4" aria-label="复盘口径">
            <div className="text-label uppercase tracking-label text-accent">Historical reconstruction · Not live positions</div>
            <h2 className="mt-1 text-h2 text-text-1">证据窗口与窗口末投影</h2>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-caption text-text-3">证据窗口（ET）起—止</dt>
                <dd className="mt-1 font-mono text-mono-xs text-text-1">
                  {toEt(detail.build.sourceWindowStart, true)} — {toEt(detail.build.sourceCutoffAt, true)}
                </dd>
              </div>
              <div>
                <dt className="text-caption text-text-3">窗口末投影 as-of（ET）</dt>
                <dd className="mt-1 font-mono text-mono-xs text-text-1">{toEt(detail.build.sourceCutoffAt, true)}</dd>
              </div>
            </dl>
            <p className="mt-3 text-caption leading-relaxed text-text-3">
              本页重放到上述 as-of 时点。“证据窗口末数量”只属于这段历史证据，不等于券商当前持仓。
            </p>
          </section>
          <div className="grid gap-3 lg:grid-cols-3">
            {[
              ['Matching', detail.matching],
              ['Completeness', detail.completeness],
              ['Provenance', detail.provenance],
            ].map(([title, value]) => (
              <section key={title as string} className="rounded-ds-md border border-subtle bg-bg-2 p-3">
                <h3 className="text-label uppercase tracking-label text-text-3">{title as string}</h3>
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-text-2">
                  {JSON.stringify(value, null, 2)}
                </pre>
              </section>
            ))}
          </div>
        </div>
      </details>
    </div>
  );
};

export default JournalEpisodeReviewPage;

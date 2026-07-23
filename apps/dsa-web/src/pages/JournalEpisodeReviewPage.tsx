import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Crosshair, Database, Sparkles, TriangleAlert } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { createPositionEpisodeAiReview, fetchPositionEpisodeDetail } from '../api/journal';
import { stocksApi } from '../api/stocks';
import { parseApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert } from '../components/common/ApiErrorAlert';
import { InlineAlert } from '../components/common/InlineAlert';
import { CandlestickChart, type Candle } from '../components/charts/CandlestickChart';
import { TradeLogicDraftPanel } from '../components/journal/review/TradeLogicDraftPanel';
import {
  buildEmaOverlay,
  buildEvidenceMarkers,
  filterCandlesByUsTradingSession,
  findEvidenceAtChartTime,
  isTimeInUsTradingSession,
  mapEvidenceToCandles,
  type EvidenceChartLink,
  type UsTradingSession,
} from '../components/journal/review/episodeReviewMapping';
import {
  compactEpisodeReviewUserContext,
  emptyEpisodeReviewDraft,
  loadEpisodeReviewDraft,
} from '../components/journal/review/episodeReviewDraft';
import { journalReturnPath } from '../components/journal/review/journalReviewRouting';
import type {
  PositionEpisodeAiReviewResponse,
  PositionEpisodeTradeLogicDraft,
  PositionEpisodeDetailResponse,
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
  if (status === 'open') return '账单窗口内未归零';
  if (status === 'closed') return '账单窗口内已归零';
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

function EvidenceTimeline({
  links,
  selectedEvidenceKey,
  usTradingSession,
  onSelect,
}: {
  links: EvidenceChartLink[];
  selectedEvidenceKey?: string | null;
  usTradingSession?: UsTradingSession;
  onSelect: (link: EvidenceChartLink) => void;
}) {
  const selectedRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    selectedRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [selectedEvidenceKey]);

  if (!links.length) {
    return (
      <div className="rounded-ds-md border border-dashed border-default p-6 text-center text-body-sm text-text-3">
        这个回合没有可展示的 execution evidence；K 线不会生成伪造的进出场标记。
      </div>
    );
  }

  return (
    <ol className="space-y-2" aria-label="成交证据时间线">
      {links.map((link, index) => {
        const evidence = link.evidence;
        const selected = evidence.evidenceKey === selectedEvidenceKey;
        const outsideSelectedSession = usTradingSession != null
          && !isTimeInUsTradingSession(evidence.evidenceTime, usTradingSession);
        const sideLabel = link.cashFlowSide === 'buy'
          ? '买入'
          : link.cashFlowSide === 'sell'
            ? '卖出'
            : '方向未知';
        return (
          <li key={evidence.evidenceKey}>
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
                  <span className="flex h-6 w-6 items-center justify-center rounded-full border border-subtle font-mono text-mono-xs text-text-3">
                    {index + 1}
                  </span>
                  <div>
                    <div className={`text-body-sm font-medium ${
                      link.cashFlowSide === 'buy'
                        ? 'text-chart-5'
                        : link.cashFlowSide === 'sell'
                          ? 'text-down-strong'
                          : 'text-text-2'
                    }`}>{sideLabel}</div>
                    <div className="text-caption text-text-3">回合角色：{eventRoleLabel(evidence.eventRole)}</div>
                    <div className="font-mono text-mono-xs text-text-3">{toEt(evidence.evidenceTime, true)} ET</div>
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  <span className={`rounded-full border px-2 py-1 text-caption ${
                    link.orderTimeProxy
                      ? 'border-warn-strong/30 bg-warn-subtle text-warn-strong'
                      : 'border-chart-5/30 bg-chart-5/10 text-chart-5'
                  }`}>
                    {link.orderTimeProxy ? '订单时间代理' : '真实逐笔成交'}
                  </span>
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
          </li>
        );
      })}
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
  userContext: PositionEpisodeTradeLogicDraft;
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
  const [tradeLogicDraft, setTradeLogicDraft] = useState<PositionEpisodeTradeLogicDraft>(
    () => emptyEpisodeReviewDraft(),
  );

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
    });
    void fetchPositionEpisodeDetail(episodeId, buildId)
      .then((response) => {
        if (!cancelled) {
          setDetail(response);
          setSelectedEvidenceKey(response.evidence[0]?.evidenceKey ?? null);
          setTradeLogicDraft(loadEpisodeReviewDraft(response.build.id, episodeId));
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
    if (!detail) return;
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
  }, [detail, requestedPeriod]);

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

  const markers = useMemo(
    () => buildEvidenceMarkers(links, selectedEvidenceKey),
    [links, selectedEvidenceKey],
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
    const evidence = markerId
      ? detail?.evidence.find((item) => item.evidenceKey === markerId)
      : findEvidenceAtChartTime(links, time);
    if (evidence) setSelectedEvidenceKey(evidence.evidenceKey);
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
    <div className="mx-auto max-w-[1440px] space-y-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <button
            type="button"
            className="mb-3 inline-flex items-center gap-1.5 text-body-sm text-text-3 hover:text-text-1"
            onClick={returnToJournal}
          >
            <ArrowLeft size={15} /> 返回仓位复盘
          </button>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-h1 text-text-1">{item.instrument.rawSymbol}</h1>
            <span className="rounded-full border border-subtle bg-bg-2 px-2.5 py-1 text-caption text-text-2">
              {lifecycleLabel(item.lifecycleStatus)}
            </span>
            {conditional && (
              <span className="rounded-full border border-warn-strong/30 bg-warn-subtle px-2.5 py-1 text-caption text-warn-strong">
                条件性结果 · 不进 Headline
              </span>
            )}
          </div>
          <p className="mt-1 text-body-sm text-text-3">
            {item.instrument.underlying} 底层行情 · 构建 #{detail.build.id} · 不可变 execution evidence
          </p>
        </div>
        <span className="rounded-full border border-up-strong/25 bg-up-subtle px-3 py-1 text-caption text-up-strong">
          Moomoo 只读 · 不下单
        </span>
      </header>

      {item.quality.assumedFlatUnverified && (
        <InlineAlert
          variant="warning"
          title="期初持仓边界未验证"
          message="左边界没有券商持仓快照证明；这里展示的是期初空仓假设下的条件性复盘，不能被人工操作改成 Verified，也不会进入 Headline。"
        />
      )}
      {item.lifecycleStatus === 'open' && (
        <InlineAlert
          variant="info"
          title="“账单窗口内未归零”不等于当前持仓"
          message="当前没有期末 position snapshot；Remaining 只表示导入证据回放到窗口末端时尚未归零。"
        />
      )}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" aria-label="回合摘要">
        {[
          ['方向', directionLabel(item.direction), '合约仓位方向'],
          ['开仓时间 ET', toEt(item.openedAt), '第一条分配证据'],
          ['期权开仓均价', formatDecimal(item.averageEntryPrice, true), '只显示，不画入底层价轴'],
          ['期权平仓均价', formatDecimal(item.averageExitPrice, true), item.closedAt ? toEt(item.closedAt) : '尚未归零'],
          ['费用', formatDecimal(item.totalFee, true), '后端持久化结果'],
          ['Net', formatDecimal(displayedNet, true), item.lifecycleStatus === 'open' ? '未归零不显示' : conditional ? '条件值' : 'Verified'],
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

      <section className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1fr)_390px]">
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
              <span className="font-mono text-mono-xs text-text-3">{links.length} 条</span>
            </div>
            <p className="mt-1 text-caption text-text-3">点击证据聚焦 K 线；点击带事件的 K 线 bar 会高亮对应证据。</p>
          </div>
          <div className="max-h-[590px] overflow-y-auto p-3">
            <EvidenceTimeline
              links={links}
              selectedEvidenceKey={selectedEvidenceKey}
              usTradingSession={historyState?.mode === 'intraday' ? tradingSession : undefined}
              onSelect={selectEvidence}
            />
          </div>
        </aside>
      </section>

      <TradeLogicDraftPanel
        key={`draft:${episodeId}:${detail.build.id}`}
        episodeId={episodeId}
        buildId={detail.build.id}
        draft={tradeLogicDraft}
        onChange={setTradeLogicDraft}
      />

      <AiReviewPanel
        key={`ai:${episodeId}:${detail.build.id}`}
        episodeId={episodeId}
        buildId={detail.build.id}
        userContext={tradeLogicDraft}
      />

      <details className="card-base p-4">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-body-sm text-text-2">
          <Database size={14} /> 查看技术证据与来源信息
        </summary>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
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
      </details>
    </div>
  );
};

export default JournalEpisodeReviewPage;

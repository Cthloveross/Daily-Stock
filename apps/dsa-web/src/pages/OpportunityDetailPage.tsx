import type React from 'react';
import { Fragment, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Clock3, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  fetchDailyOpportunities,
  fetchOpportunitySnapshotDetail,
  fetchOpportunityOptionContext,
  fetchOpportunityOptionEvents,
  fetchOpportunityOptionOverview,
  fetchOpportunityOptionWalls,
} from '../api/opportunities';
import { stocksApi } from '../api/stocks';
import { CandlestickChart, type Candle, type MAOverlay } from '../components/charts/CandlestickChart';
import { WallLevelExpiryBreakdown } from '../components/opportunities/WallLevelExpiryBreakdown';
import { Tabs } from '../components/ui';
import type {
  OpportunityCandidate,
  OpportunityEvidence,
  OpportunityOptionContextItem,
  OpportunityOptionEvent,
  OpportunityOptionEventItem,
  OpportunityOptionOverviewItem,
  OpportunityOptionWallItem,
  OpportunityOptionWallLevel,
} from '../types/opportunities';
import type { StockHistory, StockKLine, Timeframe } from '../types/stockHistory';
import { computeSmaSeededEmaSeries } from '../utils/ema';
import { calculateImpliedMove } from '../utils/impliedMove';
import {
  filterByUsTradingSession,
  type UsTradingSession,
} from '../utils/usTradingSession';

const TIMEFRAMES: Array<{ value: Timeframe; label: string }> = [
  { value: '1m', label: '1m' },
  { value: '2m', label: '2m' },
  { value: '5m', label: '5m' },
  { value: '15m', label: '15m' },
  { value: '30m', label: '30m' },
  { value: '60m', label: '1h' },
  { value: 'daily', label: '1D' },
];

const STATE_LABELS: Record<OpportunityCandidate['researchState'], string> = {
  research_ready: '重点研究',
  watch_only: '等待确认',
  context_only: '背景观察',
  blocked: '数据门禁未通过',
};

const WALL_TITLES = {
  callOi: 'Call OI 集中位',
  putOi: 'Put OI 集中位',
  callVolume: 'Call 当日成交量集中位',
  putVolume: 'Put 当日成交量集中位',
  grossGammaConcentration: '总 Gamma 集中度（无方向）',
} as const satisfies Partial<Record<keyof OpportunityOptionWallItem['walls'], string>>;

const PRIMARY_WALL_KEYS = ['callOi', 'putOi', 'grossGammaConcentration'] as const;
const SESSION_WALL_KEYS = ['callVolume', 'putVolume'] as const;

type ResearchTab = 'volatility' | 'walls' | 'events' | 'method';
type ProbabilityHorizon = 1 | 5 | 20;

const RESEARCH_TABS = [
  { value: 'volatility', label: '波动与情景' },
  { value: 'walls', label: '期权墙' },
  { value: 'events', label: '异常成交' },
  { value: 'method', label: '数据说明' },
];

const PROBABILITY_HORIZONS: Array<{ value: ProbabilityHorizon; label: string }> = [
  { value: 1, label: '1D' },
  { value: 5, label: '5D' },
  { value: 20, label: '20D' },
];

function daysForTimeframe(timeframe: Timeframe): number {
  switch (timeframe) {
    case '1m': return 7;
    case '2m': return 60;
    case '5m': return 30;
    case '15m':
    case '30m': return 60;
    case '60m':
    case '1h': return 180;
    case '90m': return 60;
    case 'daily': return 400;
    case 'weekly': return 180;
    case 'monthly': return 240;
  }
}

function isIntradayTimeframe(timeframe: Timeframe): boolean {
  return !['daily', 'weekly', 'monthly'].includes(timeframe);
}

function toCandles(klines: StockKLine[]): Candle[] {
  return klines
    .map((bar) => ({
      time: Math.floor(Date.parse(bar.date) / 1000),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      volume: bar.volume ?? undefined,
    }))
    .filter((bar) => Number.isFinite(bar.time) && bar.time > 0)
    .sort((left, right) => Number(left.time) - Number(right.time));
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: digits });
}

function formatPercent(value: number | null | undefined, digits = 2): string {
  const formatted = formatNumber(value, digits);
  return formatted === '—' ? formatted : `${formatted}%`;
}

function formatRatio(value: number | null | undefined): string {
  const formatted = formatNumber(value, 2);
  return formatted === '—' ? formatted : `${formatted}×`;
}

function formatCompact(value: number | null | undefined, currency = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const prefix = currency ? '$' : '';
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${prefix}${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${prefix}${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${prefix}${(value / 1_000).toFixed(1)}K`;
  return `${prefix}${formatNumber(value, 0)}`;
}

function formatEtTime(value: string | null | undefined): string {
  if (!value) return '未报告';
  const trimmed = value.trim();
  // Moomoo event timestamps are ET wall-clock strings without an offset.
  // Parsing them with Date would incorrectly treat them as the browser's local zone.
  const hasExplicitOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed);
  if (!hasExplicitOffset) {
    const wallClock = trimmed.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    return wallClock ? `${wallClock[1]} ${wallClock[2]} ET` : trimmed;
  }
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) return trimmed.replace('T', ' ');
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed) + ' ET';
}

function requestError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return '数据暂不可用，请稍后重试。';
}

function getEvidence(candidate: OpportunityCandidate | null, metric: string): OpportunityEvidence | undefined {
  return candidate?.evidence.find((item) => item.metric === metric);
}

function getNumericEvidence(candidate: OpportunityCandidate | null, metric: string): number | null {
  const value = getEvidence(candidate, metric)?.value;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function getObjectEvidence(candidate: OpportunityCandidate | null, metric: string): Record<string, unknown> | null {
  const value = getEvidence(candidate, metric)?.value;
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function objectNumber(value: Record<string, unknown> | null, key: string): number | null {
  const candidate = value?.[key];
  return typeof candidate === 'number' && Number.isFinite(candidate) ? candidate : null;
}

function StatusMessage({ loading, error, message }: { loading: boolean; error: string | null; message: string }) {
  if (loading) return <div className="py-8 text-center text-body-sm text-text-3">{message}</div>;
  if (error) {
    return (
      <div className="flex items-start gap-2 border-l-2 border-warn-strong py-1 pl-3 text-body-sm text-text-2">
        <TriangleAlert size={15} className="mt-0.5 shrink-0 text-warn-strong" />
        <span>{error}</span>
      </div>
    );
  }
  return null;
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="min-w-0 border-r border-subtle px-4 py-3 last:border-r-0">
      <div className="text-caption font-medium uppercase tracking-wide text-text-3">{label}</div>
      <div className="mt-1 font-mono text-mono-md font-semibold tabular-nums text-text-1">{value}</div>
      <div className="mt-1 text-caption leading-4 text-text-3">{detail}</div>
    </div>
  );
}

function WallTable({ title, levels }: { title: string; levels: OpportunityOptionWallLevel[] }) {
  return (
    <div className="min-w-0">
      <h3 className="text-body-sm font-semibold text-text-1">{title}</h3>
      {levels.length === 0 ? (
        <p className="mt-3 text-caption text-text-3">当前范围没有有效集中位。</p>
      ) : (
        <table className="mt-2 w-full table-fixed border-collapse text-left">
          <thead className="border-y border-subtle text-caption text-text-3">
            <tr><th className="py-2 font-medium">行权价</th><th className="py-2 text-right font-medium">距现价</th><th className="py-2 text-right font-medium">规模</th></tr>
          </thead>
          <tbody className="divide-y divide-subtle">
            {levels.slice(0, 3).map((level) => (
              <Fragment key={`${level.method}-${level.rank}-${level.strike}`}>
                <tr className="font-mono text-mono-xs tabular-nums text-text-2">
                  <td className="py-2 text-text-1">{formatNumber(level.strike)}</td>
                  <td className="py-2 text-right">{level.distanceFromSpotPercent > 0 ? '+' : ''}{formatPercent(level.distanceFromSpotPercent)}</td>
                  <td className="py-2 text-right">
                    {level.unit === 'usd_delta_change_per_1pct_move'
                      ? `${formatCompact(level.metricValue, true)} / 1%`
                      : formatCompact(level.metricValue)}
                  </td>
                </tr>
                {level.expiryBreakdown && level.expiryBreakdown.topExpiries.length > 0 && (
                  <tr>
                    <td colSpan={3} className="pb-2">
                      <WallLevelExpiryBreakdown level={level} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function eventContract(event: OpportunityOptionEvent): string {
  const type = event.optionType?.toUpperCase().startsWith('C') ? 'C' : event.optionType?.toUpperCase().startsWith('P') ? 'P' : '—';
  if (event.expiry && event.strikePrice !== null) return `${event.expiry} ${formatNumber(event.strikePrice)}${type}`;
  return event.symbol ?? event.optionCode;
}

type OfficialBinding =
  | { state: 'official'; snapshotKey: string; frozenAt: string }
  | { state: 'fallback'; snapshotKey: string; reason: string }
  | { state: 'live_scan' };

const SNAPSHOT_KEY_PATTERN = /^ops_[0-9a-f]{64}$/;

const OpportunityDetailPage: React.FC = () => {
  const { ticker: tickerParam = '' } = useParams<{ ticker: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedSnapshotKey = searchParams.get('snapshotKey');
  const officialSnapshotKey = requestedSnapshotKey && SNAPSHOT_KEY_PATTERN.test(requestedSnapshotKey)
    ? requestedSnapshotKey
    : null;
  const ticker = tickerParam.trim().toUpperCase();
  const [refreshKey, setRefreshKey] = useState(0);
  const [timeframe, setTimeframe] = useState<Timeframe>('5m');
  const [tradingSession, setTradingSession] = useState<UsTradingSession>('regular');
  const [probabilityHorizon, setProbabilityHorizon] = useState<ProbabilityHorizon>(1);
  const [researchTab, setResearchTab] = useState<ResearchTab>('volatility');

  const [candidate, setCandidate] = useState<OpportunityCandidate | null>(null);
  const [runMeta, setRunMeta] = useState<{ marketDateEt: string; asOf: string; signalVersion: string } | null>(null);
  const [candidateLoading, setCandidateLoading] = useState(true);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [officialBinding, setOfficialBinding] = useState<OfficialBinding>({ state: 'live_scan' });

  const [overview, setOverview] = useState<OpportunityOptionOverviewItem | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [optionContext, setOptionContext] = useState<OpportunityOptionContextItem | null>(null);
  const [contextLoading, setContextLoading] = useState(true);
  const [contextError, setContextError] = useState<string | null>(null);
  const [wall, setWall] = useState<OpportunityOptionWallItem | null>(null);
  const [wallLoading, setWallLoading] = useState(true);
  const [wallError, setWallError] = useState<string | null>(null);
  const [eventItem, setEventItem] = useState<OpportunityOptionEventItem | null>(null);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [eventsError, setEventsError] = useState<string | null>(null);

  const [history, setHistory] = useState<StockHistory | null>(null);
  const [chartLoading, setChartLoading] = useState(true);
  const [chartError, setChartError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const refresh = refreshKey > 0;

    const resolveCandidate = async () => {
      if (officialSnapshotKey) {
        try {
          const detail = await fetchOpportunitySnapshotDetail(officialSnapshotKey);
          if (cancelled) return;
          const frozen = detail.run.candidates
            .find((item) => item.ticker.toUpperCase() === ticker) ?? null;
          if (frozen) {
            setCandidateError(null);
            setCandidate(frozen);
            setRunMeta({
              marketDateEt: detail.run.marketDateEt,
              asOf: detail.run.asOf,
              signalVersion: detail.run.signalVersion,
            });
            setOfficialBinding({
              state: 'official',
              snapshotKey: officialSnapshotKey,
              frozenAt: detail.snapshot.frozenAt,
            });
            return;
          }
          setOfficialBinding({
            state: 'fallback',
            snapshotKey: officialSnapshotKey,
            reason: '官方快照中没有该标的的冻结候选，以下为即时扫描结果。',
          });
        } catch {
          if (cancelled) return;
          setOfficialBinding({
            state: 'fallback',
            snapshotKey: officialSnapshotKey,
            reason: '官方快照读取失败，以下为即时扫描结果。',
          });
        }
      } else {
        setOfficialBinding({ state: 'live_scan' });
      }
      const run = await fetchDailyOpportunities([ticker], 1, { refresh });
      if (cancelled) return;
      setCandidateError(null);
      setCandidate(run.candidates.find((item) => item.ticker.toUpperCase() === ticker) ?? run.candidates[0] ?? null);
      setRunMeta({ marketDateEt: run.marketDateEt, asOf: run.asOf, signalVersion: run.signalVersion });
    };

    void resolveCandidate()
      .catch((error: unknown) => { if (!cancelled) setCandidateError(requestError(error)); })
      .finally(() => { if (!cancelled) setCandidateLoading(false); });

    void fetchOpportunityOptionOverview([ticker], { refresh })
      .then((response) => {
        if (!cancelled) {
          setOverviewError(null);
          setOverview(response.items[0] ?? null);
        }
      })
      .catch((error: unknown) => { if (!cancelled) setOverviewError(requestError(error)); })
      .finally(() => { if (!cancelled) setOverviewLoading(false); });

    void fetchOpportunityOptionContext([ticker], { refresh })
      .then((response) => {
        if (!cancelled) {
          setContextError(null);
          setOptionContext(response.items[0] ?? null);
        }
      })
      .catch((error: unknown) => { if (!cancelled) setContextError(requestError(error)); })
      .finally(() => { if (!cancelled) setContextLoading(false); });

    void fetchOpportunityOptionWalls([ticker], 0, 45, { refresh })
      .then((response) => {
        if (!cancelled) {
          setWallError(null);
          setWall(response.items[0] ?? null);
        }
      })
      .catch((error: unknown) => { if (!cancelled) setWallError(requestError(error)); })
      .finally(() => { if (!cancelled) setWallLoading(false); });

    void fetchOpportunityOptionEvents([ticker], 10, { refresh })
      .then((response) => {
        if (!cancelled) {
          setEventsError(null);
          setEventItem(response.items[0] ?? null);
        }
      })
      .catch((error: unknown) => { if (!cancelled) setEventsError(requestError(error)); })
      .finally(() => { if (!cancelled) setEventsLoading(false); });

    return () => { cancelled = true; };
  }, [officialSnapshotKey, refreshKey, ticker]);

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    void stocksApi.getHistory(ticker, timeframe, daysForTimeframe(timeframe), { refresh: refreshKey > 0 })
      .then((response) => {
        if (!cancelled) {
          setChartError(null);
          setHistory(response);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setHistory(null);
        setChartError(requestError(error));
      })
      .finally(() => { if (!cancelled) setChartLoading(false); });
    return () => { cancelled = true; };
  }, [refreshKey, ticker, timeframe]);

  const allCandles = useMemo(() => toCandles(history?.data ?? []), [history]);
  const intradayTimeframe = isIntradayTimeframe(timeframe);
  const candles = useMemo(
    () => intradayTimeframe
      ? filterByUsTradingSession(allCandles, tradingSession)
      : allCandles,
    [allCandles, intradayTimeframe, tradingSession],
  );
  const overlays = useMemo<MAOverlay[]>(() => [
    { period: 8, label: 'EMA 8', color: '#d29922', data: computeSmaSeededEmaSeries(candles, 8) },
    { period: 13, label: 'EMA 13', color: '#5b8def', data: computeSmaSeededEmaSeries(candles, 13) },
  ], [candles]);

  const range = getObjectEvidence(candidate, 'prior_20d_range_position');
  const emaEvidence = getObjectEvidence(candidate, 'ema8_ema13_alignment');
  const priorHigh = objectNumber(range, 'priorHigh');
  const priorLow = objectNumber(range, 'priorLow');
  const dailyClose = getNumericEvidence(candidate, 'last_completed_close') ?? candidate?.referenceClose ?? null;
  const volumeRatio = getNumericEvidence(candidate, 'volume_vs_prior_20d_median');
  const dollarVolumeRatio = getNumericEvidence(candidate, 'dollar_volume_vs_prior_20d_median');
  const modelPriceBasis = wall?.spot ?? dailyClose;
  const impliedMove = calculateImpliedMove(
    modelPriceBasis ?? Number.NaN,
    overview?.ivPercent ?? Number.NaN,
    probabilityHorizon,
  );
  const oneDayImpliedMove = calculateImpliedMove(
    modelPriceBasis ?? Number.NaN,
    overview?.ivPercent ?? Number.NaN,
    1,
  );
  const modelBasisLabel = wall?.spot !== null && wall?.spot !== undefined
    ? `Moomoo spot · ${formatEtTime(wall.quoteAsOf)}`
    : `上一完整日收盘 · ${candidate?.referenceSessionDate ?? '日期未报告'}`;
  const latestCandle = candles.at(-1);
  const latestVisibleBarAt = intradayTimeframe && typeof latestCandle?.time === 'number'
    ? new Date(Number(latestCandle.time) * 1000).toISOString()
    : history?.lastBarAt;
  const latestEma8 = overlays[0].data.at(-1)?.value;
  const latestEma13 = overlays[1].data.at(-1)?.value;
  const rangeContext = typeof range?.context === 'string' ? range.context : 'unknown';
  const emaContext = typeof emaEvidence?.context === 'string' ? emaEvidence.context : 'unknown';
  const dailyEma8 = objectNumber(emaEvidence, 'ema8');
  const dailyEma13 = objectNumber(emaEvidence, 'ema13');
  const structureReadout = rangeContext === 'breakout'
    ? `上一完整日收盘高于前 20 日高点 ${formatNumber(priorHigh)}；EMA 结构为 ${emaContext}。这是突破背景，不等于盘中追价条件。`
    : rangeContext === 'breakdown'
      ? `上一完整日收盘低于前 20 日低点 ${formatNumber(priorLow)}；EMA 结构为 ${emaContext}。这是破位背景，不等于自动做空条件。`
      : `价格仍在前 20 日区间 ${formatNumber(priorLow)}–${formatNumber(priorHigh)} 内；EMA 结构为 ${emaContext}。`;
  const ivRank = overview?.ivRankPercent;
  const isOfficialBinding = officialBinding.state === 'official';
  // D-4 摘要同证据束：官方绑定时，摘要句中织入的增强数据数值（非冻结 bundle）必须带 as-of 内联标注；
  // 冻结 bundle 数值（20 日区间 / EMA / 量能比率）不加注，页头已声明冻结绑定。
  const liveEnhancementNote = isOfficialBinding
    ? `（当前增强数据 ${formatEtTime(overview?.fetchedAt)}，非冻结榜单证据）`
    : '';
  const volatilityZone = typeof ivRank !== 'number'
    ? 'IV Rank 暂不可用，不能判断当前 IV 在自身历史区间的位置。'
    : ivRank >= 75
      ? `IV Rank ${formatPercent(ivRank)}${liveEnhancementNote}，处于数据商历史区间偏高位置；高 IV 仍可能继续上升，不自动等于卖出波动率。`
      : ivRank <= 25
        ? `IV Rank ${formatPercent(ivRank)}${liveEnhancementNote}，处于数据商历史区间偏低位置；低 IV 不自动等于应买入期权。`
        : `IV Rank ${formatPercent(ivRank)}${liveEnhancementNote}，位于数据商历史区间中段。`;
  const callOiWall = wall?.walls.callOi[0]?.strike;
  const putOiWall = wall?.walls.putOi[0]?.strike;
  const confirmationReadout = priorHigh === null || priorLow === null
    ? '20 日区间暂不完整；等待价格结构、量能和合约盘口同时可验证后，才考虑从研究升级为交易计划。'
    : `向上先观察能否站上前 20 日高点 ${formatNumber(priorHigh)} 并获得量能确认；向下风险先观察日线 EMA13 ${formatNumber(dailyEma13)}，再看前 20 日低点 ${formatNumber(priorLow)}。期权墙只作位置背景。`;
  const researchStateReason = candidate?.hardGates.find(
    (gate) => gate.gateId === 'research_evidence_ready',
  )?.reason;
  const executionReadout = candidate?.researchState === 'research_ready'
    ? '基础量价证据已达到重点研究门槛。执行前仍必须选择具体合约，并核对实时 Bid/Ask、Spread%、盘口深度、成交持续性和事件风险。'
    : candidate?.researchState === 'watch_only'
      ? '价格结构与量能尚未同时确认；先等待另一侧证据，再进入具体合约的流动性检查。'
      : candidate?.researchState === 'context_only'
        ? '当前只保留市场背景，不进入合约筛选；需要先形成一致方向结构和独立量能确认。'
        : '核心行情或标的范围门禁尚未通过，不进入期权合约研究。';
  const limitations = [...new Set([
    ...(overview?.limitations ?? []),
    ...(optionContext?.limitations ?? []),
    ...(wall?.limitations ?? []),
    ...(eventItem?.limitations ?? []),
    ...(candidate?.unknowns ?? []),
  ])];

  if (!ticker) {
    return <div className="p-6 text-body text-text-2">缺少股票代码。</div>;
  }

  return (
    <div className="min-h-full bg-bg-0">
      <header className="border-b border-subtle bg-bg-1 px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-[1720px] flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <button
              type="button"
              onClick={() => navigate('/regime')}
              className="mt-0.5 inline-flex h-8 items-center gap-1 rounded-ds-sm border border-subtle bg-bg-1 px-2.5 text-body-sm text-text-2 hover:bg-bg-2 hover:text-text-1"
            >
              <ArrowLeft size={14} /> 返回机会清单
            </button>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="font-mono text-xl font-semibold tracking-tight text-text-1">{ticker}</h1>
                <span className="rounded-ds-sm border border-[color:var(--accent-subtle-border)] bg-[color:var(--accent-subtle-bg)] px-2 py-0.5 text-caption font-medium text-accent">
                  专业研究视图
                </span>
                <span className="inline-flex items-center gap-1 rounded-ds-sm border border-subtle px-2 py-0.5 text-caption text-text-3">
                  <ShieldCheck size={12} /> 只读 · 不下单
                </span>
              </div>
              <p className="mt-1 text-body-sm text-text-3">
                {overview?.name || history?.stockName || '期权日内研究'} · 信号日 {runMeta?.marketDateEt ?? '读取中'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              setCandidateLoading(true);
              setOverviewLoading(true);
              setContextLoading(true);
              setWallLoading(true);
              setEventsLoading(true);
              setChartLoading(true);
              setCandidateError(null);
              setOverviewError(null);
              setContextError(null);
              setWallError(null);
              setEventsError(null);
              setChartError(null);
              setRefreshKey((value) => value + 1);
            }}
            className="inline-flex h-8 items-center gap-1.5 rounded-ds-sm border border-subtle bg-bg-1 px-3 text-body-sm text-text-2 hover:bg-bg-2 hover:text-text-1"
          >
            <RefreshCw size={14} className={candidateLoading || overviewLoading ? 'animate-spin' : undefined} /> 刷新研究数据
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-[1720px] space-y-4 p-4 sm:p-6">
        <section
          className="flex flex-wrap items-center gap-x-5 gap-y-1 border border-subtle bg-bg-1 px-4 py-2 text-caption text-text-3"
          aria-label="数据时点"
        >
          <span className="inline-flex items-center gap-1.5 font-medium text-text-2"><Clock3 size={13} /> 数据时点</span>
          <span>信号 {runMeta?.marketDateEt ?? '—'}</span>
          <span>K线 {history?.period ?? timeframe} · {formatEtTime(latestVisibleBarAt)}</span>
          <span>Volume {overview?.sessionVolumeDate ?? '—'} 当日累计</span>
          <span>OI {overview?.openInterestAsOf ?? '—'} · T-1 清算</span>
          {officialBinding.state === 'official' ? (
            <span className="inline-flex items-center gap-1 rounded-ds-sm border border-[color:var(--accent-subtle-border)] bg-[color:var(--accent-subtle-bg)] px-2 py-0.5 font-medium text-accent">
              官方快照 {officialBinding.snapshotKey.slice(0, 12)}… · 冻结于 {officialBinding.frozenAt}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-ds-sm border border-subtle px-2 py-0.5 text-text-3">
              即时扫描 · 未绑定官方快照
            </span>
          )}
        </section>

        {officialBinding.state === 'fallback' && (
          <section
            className="border border-[color:var(--warning-subtle-border,#8a6d1a)] bg-bg-1 px-4 py-2 text-caption text-text-2"
            role="status"
          >
            {officialBinding.reason}（请求的快照 {officialBinding.snapshotKey.slice(0, 12)}…）
          </section>
        )}

        {officialBinding.state === 'official'
          && candidate?.referenceClose != null
          && Number.isFinite(candidate.referenceClose)
          && candidate.referenceClose > 0
          && wall?.spot != null
          && Number.isFinite(wall.spot) && (
          <section
            className="border border-subtle bg-bg-1 px-4 py-2 text-caption text-text-3"
            aria-label="冻结与当前差异"
          >
            冻结基准 close {formatNumber(candidate.referenceClose)}
            （{candidate.referenceSessionDate ?? '日期未报告'} 收盘）
            {' → '}当前 spot {formatNumber(wall.spot)}
            （{wall.quoteAsOf ?? '时点未报告'} · Moomoo）：
            <span className="font-mono text-text-2">
              {(() => {
                const drift = ((wall.spot - candidate.referenceClose) / candidate.referenceClose) * 100;
                return `${drift >= 0 ? '+' : ''}${drift.toFixed(2)}%`;
              })()}
            </span>
            。差异只反映快照冻结后的价格变动，不改变冻结榜单结论。
          </section>
        )}

        <section className="border border-subtle bg-bg-1" aria-labelledby="professional-summary-title">
          <header className="flex flex-wrap items-start justify-between gap-3 border-b border-subtle px-4 py-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="professional-summary-title" className="text-body font-semibold text-text-1">专业结论与波动情景</h2>
                <span className="rounded-ds-sm border border-subtle px-2 py-0.5 text-caption text-text-3">
                  {candidate ? STATE_LABELS[candidate.researchState] : '读取中'}
                </span>
              </div>
              <p className="mt-1 text-caption text-text-3">IV 模型终值分布 · 不是历史真实胜率、盘中触及概率或方向预测</p>
            </div>
            <div className="inline-flex overflow-hidden rounded-ds-sm border border-subtle" role="group" aria-label="概率期限">
              {PROBABILITY_HORIZONS.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  aria-pressed={probabilityHorizon === item.value}
                  onClick={() => setProbabilityHorizon(item.value)}
                  className={`border-r border-subtle px-3 py-1.5 font-mono text-mono-xs last:border-r-0 ${probabilityHorizon === item.value ? 'bg-bg-3 text-text-1' : 'bg-bg-1 text-text-3 hover:bg-bg-2 hover:text-text-2'}`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </header>

          <div className="grid xl:grid-cols-[minmax(0,1.55fr)_minmax(340px,0.8fr)]">
            <div className="min-w-0 p-4 sm:p-5">
              {impliedMove ? (
                <>
                  <div className="flex flex-wrap items-end justify-between gap-3">
                    <div>
                      <div className="text-caption text-text-3">约 68% 模型终值区间 · {probabilityHorizon} 个交易日</div>
                      <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-text-1">
                        ${formatNumber(impliedMove.oneSigma.lowerPrice)} – ${formatNumber(impliedMove.oneSigma.upperPrice)}
                      </div>
                      <div className="mt-1 text-caption text-text-3">
                        基准 ${formatNumber(impliedMove.spot)} · 下行 {formatPercent(impliedMove.oneSigma.downsideMovePercent)} · 上行 {formatPercent(impliedMove.oneSigma.upsideMovePercent)}
                      </div>
                    </div>
                    <div className="text-right text-caption text-text-3">
                      <div>IV {formatPercent(impliedMove.annualizedIvPercent)}</div>
                      <div className="mt-0.5">{modelBasisLabel}</div>
                      {isOfficialBinding && (
                        <div className="mt-0.5">IV 为当前增强数据（{formatEtTime(overview?.fetchedAt)}），非冻结榜单证据</div>
                      )}
                    </div>
                  </div>

                  <div className="mt-4 grid gap-2 sm:grid-cols-3">
                    <div className="border border-subtle bg-bg-2 p-3">
                      <div className="font-mono text-mono-md font-semibold text-text-1">68.27%</div>
                      <div className="mt-1 text-caption text-text-2">终值落在主区间内</div>
                    </div>
                    <div className="border border-subtle bg-bg-2 p-3">
                      <div className="font-mono text-mono-md font-semibold text-text-1">15.87%</div>
                      <div className="mt-1 text-caption text-text-2">终值高于 ${formatNumber(impliedMove.oneSigma.upperPrice)}</div>
                    </div>
                    <div className="border border-subtle bg-bg-2 p-3">
                      <div className="font-mono text-mono-md font-semibold text-text-1">15.87%</div>
                      <div className="mt-1 text-caption text-text-2">终值低于 ${formatNumber(impliedMove.oneSigma.lowerPrice)}</div>
                    </div>
                  </div>

                  <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-bg-3" aria-label="模型概率分布条">
                    <div className="bg-[color:var(--down-muted)]" style={{ width: '15.865%' }} />
                    <div className="bg-[color:var(--accent)]" style={{ width: '68.27%' }} />
                    <div className="bg-[color:var(--up-muted)]" style={{ width: '15.865%' }} />
                  </div>
                  <div className="mt-2 flex justify-between gap-3 text-[11px] text-text-3">
                    <span>下尾 15.87%</span>
                    <span>主区间 68.27%</span>
                    <span>上尾 15.87%</span>
                  </div>

                  <div className="mt-4 border-t border-subtle pt-3 text-body-sm text-text-2">
                    约 95.45% 的模型终值区间为
                    <span className="ml-1 font-mono text-mono-sm text-text-1">
                      ${formatNumber(impliedMove.twoSigma.lowerPrice)} – ${formatNumber(impliedMove.twoSigma.upperPrice)}
                    </span>
                    <span className="ml-2 text-caption text-text-3">区间外合计约 4.55%</span>
                  </div>
                </>
              ) : (
                <StatusMessage loading={overviewLoading || wallLoading} error={overviewError || wallError} message="读取 IV 与价格基准后生成概率区间…" />
              )}
            </div>

            <aside className="border-t border-subtle bg-bg-0 p-4 xl:border-l xl:border-t-0" aria-label="专业解读">
              <h3 className="text-body-sm font-semibold text-text-1">交易研究结论</h3>
              <div className="mt-3 space-y-3">
                <div>
                  <div className="text-caption font-medium text-text-3">当前定位 · {candidate ? STATE_LABELS[candidate.researchState] : '读取中'}</div>
                  <p className="mt-1 text-body-sm leading-5 text-text-2">{structureReadout} {volatilityZone}</p>
                  {researchStateReason && (
                    <p className="mt-1 text-caption leading-5 text-text-3">{researchStateReason}</p>
                  )}
                </div>
                <div>
                  <div className="text-caption font-medium text-text-3">升级确认与反证</div>
                  <p className="mt-1 text-body-sm leading-5 text-text-2">{confirmationReadout}</p>
                </div>
                <div>
                  <div className="text-caption font-medium text-text-3">执行前门禁</div>
                  <p className="mt-1 text-body-sm leading-5 text-text-2">{executionReadout}</p>
                </div>
              </div>
              <div className="mt-4 border-l-2 border-[color:var(--warn-strong)] pl-3 text-caption leading-5 text-text-2">
                方向概率尚未校准。EMA、Put/Call 与 OI 墙不能直接变成“上涨概率”；需要历史同类信号的样本外结果后才能显示真实统计。
              </div>
            </aside>
          </div>
        </section>

        {candidateError && <StatusMessage loading={false} error={candidateError} message="" />}

        <section className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,0.75fr)]">
          <div className="border border-subtle bg-bg-1">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-subtle px-4 py-3">
              <div>
                <h2 className="text-body font-semibold text-text-1">价格结构</h2>
                <p className="mt-0.5 text-caption text-text-3">
                  美东时间 · EMA8 / EMA13 · {intradayTimeframe
                    ? tradingSession === 'regular' ? '常规时段 09:30–16:00 ET' : '含盘前盘后 04:00–20:00 ET'
                    : '日线（无盘前盘后切换）'}
                </p>
              </div>
              <div className="flex max-w-full flex-col items-end gap-2">
                <div className="inline-flex max-w-full overflow-x-auto rounded-ds-sm border border-subtle" role="group" aria-label="K线周期">
                  {TIMEFRAMES.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      aria-pressed={timeframe === item.value}
                      onClick={() => {
                        if (item.value === timeframe) return;
                        setChartLoading(true);
                        setChartError(null);
                        setTimeframe(item.value);
                      }}
                      className={`border-r border-subtle px-2.5 py-1.5 font-mono text-mono-xs last:border-r-0 ${timeframe === item.value ? 'bg-bg-3 text-text-1' : 'bg-bg-1 text-text-3 hover:bg-bg-2 hover:text-text-2'}`}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                {intradayTimeframe && (
                  <div className="inline-flex rounded-ds-sm border border-subtle bg-bg-2 p-0.5" role="group" aria-label="美股交易时段">
                    {([
                      ['regular', '常规时段', '09:30–16:00 ET'],
                      ['extended', '含盘前盘后', '04:00–20:00 ET'],
                    ] as const).map(([value, label, hours]) => (
                      <button
                        key={value}
                        type="button"
                        aria-label={`使用${label} ${hours}`}
                        aria-pressed={tradingSession === value}
                        onClick={() => setTradingSession(value)}
                        className={`rounded-ds-sm px-2.5 py-1 text-caption transition-colors ${
                          tradingSession === value
                            ? 'bg-accent text-white'
                            : 'text-text-3 hover:bg-bg-3 hover:text-text-1'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-subtle px-4 py-2 text-caption">
              <span className="inline-flex items-center gap-1.5 text-text-2"><span className="h-0.5 w-4 bg-[#d29922]" />EMA 8 {formatNumber(latestEma8)}</span>
              <span className="inline-flex items-center gap-1.5 text-text-2"><span className="h-0.5 w-4 bg-[#5b8def]" />EMA 13 {formatNumber(latestEma13)}</span>
              <span className="text-text-3">基于当前可见 {candles.length} 根 K 线重算</span>
              <span className="ml-auto font-mono text-mono-xs text-text-3">Last {formatNumber(latestCandle?.close)}</span>
            </div>
            <div className="min-h-[430px] p-2">
              {chartLoading ? (
                <StatusMessage loading error={null} message={`读取 ${timeframe} K 线…`} />
              ) : chartError ? (
                <div className="p-4"><StatusMessage loading={false} error={chartError} message="" /></div>
              ) : candles.length > 0 ? (
                <CandlestickChart data={candles} overlays={overlays} height={420} timeZone="America/New_York" timeZoneLabel="ET" />
              ) : (
                <div className="py-16 text-center text-body-sm text-text-3">当前周期没有可显示的 K 线。</div>
              )}
            </div>
          </div>

          <aside className="border border-subtle bg-bg-1" aria-labelledby="checklist-title">
            <div className="border-b border-subtle px-4 py-3">
              <h2 id="checklist-title" className="text-body font-semibold text-text-1">关键价位与确认条件</h2>
              <p className="mt-1 text-caption text-text-3">只保留能改变研究判断的事实。</p>
            </div>
            <dl className="divide-y divide-subtle px-4">
              <div className="py-3">
                <dt className="text-caption text-text-3">20 日关键区间</dt>
                <dd className="mt-1 font-mono text-mono-sm text-text-1">High {formatNumber(priorHigh)} · Low {formatNumber(priorLow)}</dd>
              </div>
              <div className="py-3">
                <dt className="text-caption text-text-3">日线趋势结构</dt>
                <dd className="mt-1 font-mono text-mono-sm text-text-1">EMA8 {formatNumber(dailyEma8)} · EMA13 {formatNumber(dailyEma13)}</dd>
                <div className="mt-1 text-caption text-text-3">{emaContext} · 昨收 {formatNumber(dailyClose)}</div>
              </div>
              <div className="py-3">
                <dt className="text-caption text-text-3">主要 OI 集中位</dt>
                <dd className="mt-1 font-mono text-mono-sm text-text-1">Call {formatNumber(callOiWall)} · Put {formatNumber(putOiWall)}</dd>
                <div className="mt-1 text-caption text-text-3">观察位置，不是确定支撑 / 阻力</div>
              </div>
              <div className="py-3">
                <dt className="text-caption text-text-3">量能确认</dt>
                <dd className="mt-1 font-mono text-mono-sm text-text-1">成交量 {formatRatio(volumeRatio)} · 成交额 {formatRatio(dollarVolumeRatio)}</dd>
                <div className="mt-1 text-caption text-text-3">相对前 20 日中位数</div>
              </div>
              <div className="py-3">
                <dt className="text-caption text-text-3">执行门禁</dt>
                <dd className="mt-1 text-body-sm leading-5 text-text-2">具体合约 Bid/Ask、Spread%、盘口深度与事件风险尚未进入本页概率模型；缺任一项都不应把研究情景当成交易计划。</dd>
              </div>
            </dl>
          </aside>
        </section>

        <section className="border border-subtle bg-bg-1" aria-labelledby="research-tabs-title">
          <div className="px-4 pt-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h2 id="research-tabs-title" className="text-body font-semibold text-text-1">期权研究细节</h2>
                <p className="mt-1 text-caption text-text-3">按需查看，避免相同指标在同一页面重复出现。</p>
              </div>
              {eventItem && <div className="text-caption text-text-3">异常成交截至 {formatEtTime(eventItem.eventAsOf)}</div>}
            </div>
            <div className="overflow-x-auto">
              <Tabs
                value={researchTab}
                onChange={(value) => setResearchTab(value as ResearchTab)}
                items={RESEARCH_TABS.map((item) => (
                  item.value === 'events' ? { ...item, count: eventItem?.events.length } : item
                ))}
                className="mt-2 min-w-max"
              />
            </div>
          </div>

          <div className="p-4">
            {researchTab === 'volatility' && (
              <div>
                <StatusMessage loading={overviewLoading} error={overviewError} message="读取 IV、OI 与成交量概览…" />
                {!overviewLoading && !overviewError && overview && (
                  <>
                    <div className="grid border border-subtle sm:grid-cols-2 xl:grid-cols-4">
                      <MetricCard
                        label="当前 IV"
                        value={formatPercent(overview.ivPercent)}
                        detail={`前值 ${formatPercent(overview.previousIvPercent)} · 变化 ${formatNumber(overview.ivChangePoints)}pt`}
                      />
                      <MetricCard
                        label="IV Rank"
                        value={formatPercent(overview.ivRankPercent)}
                        detail={`Percentile ${formatPercent(overview.ivPercentilePercent)} · 数据商口径`}
                      />
                      <MetricCard
                        label="IV − 30D HV"
                        value={`${formatNumber(overview.ivHv30SpreadPoints)}pt`}
                        detail={`30D HV ${formatPercent(overview.hv30dPercent)} · 不代表波动率交易信号`}
                      />
                      <MetricCard
                        label="1D · 68% 模型区间"
                        value={oneDayImpliedMove ? `$${formatNumber(oneDayImpliedMove.oneSigma.lowerPrice)} – $${formatNumber(oneDayImpliedMove.oneSigma.upperPrice)}` : '—'}
                        detail="IV 终值分布，不是日内高低点预测"
                      />
                    </div>

                    <div className="mt-4 grid gap-4 lg:grid-cols-3">
                      <div className="border-l-2 border-subtle pl-3">
                        <div className="text-caption text-text-3">当日累计成交量</div>
                        <div className="mt-1 font-mono text-mono-sm text-text-1">Call {formatCompact(overview.callVolume)} · Put {formatCompact(overview.putVolume)}</div>
                        <div className="mt-1 text-caption text-text-3">P/C {formatRatio(overview.putCallVolumeRatio)} · {overview.sessionVolumeDate}</div>
                      </div>
                      <div className="border-l-2 border-subtle pl-3">
                        <div className="text-caption text-text-3">上一清算日未平仓量</div>
                        <div className="mt-1 font-mono text-mono-sm text-text-1">Call {formatCompact(overview.callOpenInterest)} · Put {formatCompact(overview.putOpenInterest)}</div>
                        <div className="mt-1 text-caption text-text-3">P/C {formatRatio(overview.putCallOpenInterestRatio)} · {overview.openInterestAsOf ?? 'T-1'}</div>
                      </div>
                      <div className="border-l-2 border-subtle pl-3">
                        <div className="text-caption text-text-3">近 ATM Call IV</div>
                        <div className="mt-1 font-mono text-mono-sm text-text-1">{formatPercent(optionContext?.atmCallIvPercent)}</div>
                        <div className="mt-1 text-caption text-text-3">到期日 {optionContext?.expiry ?? '未报告'} · {formatEtTime(optionContext?.fetchedAt)}</div>
                        {contextError && <div className="mt-1 text-caption text-warn-strong">{contextError}</div>}
                        {contextLoading && <div className="mt-1 text-caption text-text-3">读取中…</div>}
                      </div>
                    </div>

                    <details className="mt-4 border-t border-subtle pt-3 text-body-sm text-text-2">
                      <summary className="cursor-pointer select-none text-text-2 hover:text-text-1">查看各期限历史波动率</summary>
                      <div className="mt-3 grid border border-subtle sm:grid-cols-2 xl:grid-cols-5">
                        {[
                          ['30D HV', overview.hv30dPercent, overview.hv30dPercentile],
                          ['60D HV', overview.hv60dPercent, overview.hv60dPercentile],
                          ['90D HV', overview.hv90dPercent, overview.hv90dPercentile],
                          ['120D HV', overview.hv120dPercent, overview.hv120dPercentile],
                          ['365D HV', overview.hv365dPercent, overview.hv365dPercentile],
                        ].map(([label, hv, percentile]) => (
                          <MetricCard
                            key={String(label)}
                            label={String(label)}
                            value={formatPercent(hv as number | null)}
                            detail={`历史分位 ${formatPercent(percentile as number | null)}`}
                          />
                        ))}
                      </div>
                    </details>
                  </>
                )}
              </div>
            )}

            {researchTab === 'walls' && (
              <div>
                <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
                  <p className="max-w-4xl text-caption leading-5 text-text-3">DTE 0–45。OI 墙是持仓集中位置，不代表多空；总 Gamma 为绝对值集中度，不是 dealer GEX 或 gamma flip。</p>
                  {wall && <div className="text-right text-caption text-text-3">Spot {formatNumber(wall.spot)} · 覆盖 {formatPercent(wall.coverage.coveragePercent)} · {formatEtTime(wall.quoteAsOf)}</div>}
                </div>
                <StatusMessage loading={wallLoading} error={wallError} message="读取期权链并计算集中位，可能需要数秒…" />
                {!wallLoading && !wallError && wall && (wall.state === 'ready' || wall.state === 'partial') && (
                  <>
                    {wall.state === 'partial' && <div className="mb-4 border-l-2 border-warn-strong pl-3 text-caption text-text-2">链覆盖不完整，请结合覆盖率和失败批次数解读。</div>}
                    <div className="grid gap-x-10 gap-y-6 md:grid-cols-2 xl:grid-cols-3">
                      {PRIMARY_WALL_KEYS.map((key) => (
                        <WallTable key={key} title={WALL_TITLES[key]} levels={wall.walls[key]} />
                      ))}
                    </div>
                    <details className="mt-5 border-t border-subtle pt-3 text-body-sm text-text-2">
                      <summary className="cursor-pointer select-none text-text-2 hover:text-text-1">查看当日成交量集中位</summary>
                      <div className="mt-4 grid gap-x-10 gap-y-6 md:grid-cols-2">
                        {SESSION_WALL_KEYS.map((key) => (
                          <WallTable key={key} title={WALL_TITLES[key]} levels={wall.walls[key]} />
                        ))}
                      </div>
                    </details>
                    <div className="mt-4 border-t border-subtle pt-3 text-caption text-text-3">
                      有效合约 {wall.coverage.validContracts}/{wall.coverage.requestedContracts} · Gamma 可计算 {wall.coverage.gammaContracts} · 失败批次 {wall.coverage.failedBatches} · 公式 {wall.formulaVersion}
                    </div>
                  </>
                )}
                {!wallLoading && !wallError && wall && wall.state !== 'ready' && wall.state !== 'partial' && (
                  <p className="text-body-sm text-text-3">{wall.message}</p>
                )}
              </div>
            )}

            {researchTab === 'events' && (
              <div>
                <p className="mb-3 text-caption leading-5 text-text-3">BUY / SELL、情绪与订单类型均为数据商分类，不能据此反推交易者真实意图。</p>
                <StatusMessage loading={eventsLoading} error={eventsError} message="读取异常期权成交…" />
                {!eventsLoading && !eventsError && eventItem && eventItem.events.length > 0 && (
                  <>
                    <div className="hidden overflow-x-auto lg:block">
                      <table className="w-full min-w-[780px] border-collapse text-left">
                        <thead className="border-y border-subtle text-caption text-text-3">
                          <tr>
                            <th className="py-2 pr-3 font-medium">时间</th>
                            <th className="py-2 pr-3 font-medium">合约 / 成交</th>
                            <th className="py-2 pr-3 text-right font-medium">名义金额</th>
                            <th className="py-2 pr-3 text-right font-medium">Bid / Ask</th>
                            <th className="py-2 pr-3 text-right font-medium">Vol / OI</th>
                            <th className="py-2 font-medium">数据商标签</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-subtle">
                          {eventItem.events.map((event) => (
                            <tr key={event.eventId} className="text-body-sm text-text-2">
                              <td className="whitespace-nowrap py-2.5 pr-3 font-mono text-mono-xs">{formatEtTime(event.fillTime)}</td>
                              <td className="whitespace-nowrap py-2.5 pr-3">
                                <div className="font-mono text-mono-xs text-text-1">{eventContract(event)}</div>
                                <div className="mt-0.5 text-caption text-text-3">{formatCompact(event.volume)} @ {formatNumber(event.price)} · IV {formatPercent(event.ivPercent)}</div>
                              </td>
                              <td className="py-2.5 pr-3 text-right font-mono text-mono-xs">{formatCompact(event.turnover, true)}</td>
                              <td className="py-2.5 pr-3 text-right font-mono text-mono-xs">{formatNumber(event.bidPrice)} / {formatNumber(event.askPrice)}</td>
                              <td className="py-2.5 pr-3 text-right font-mono text-mono-xs">{formatRatio(event.voRatioPercent === null ? null : event.voRatioPercent / 100)}</td>
                              <td className="py-2.5 text-caption text-text-2">{[event.sentiment, event.strategyType, ...event.orderTypes].filter(Boolean).join(' · ') || '未标注'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div className="divide-y divide-subtle lg:hidden">
                      {eventItem.events.map((event) => (
                        <article key={event.eventId} className="py-3 first:pt-0 last:pb-0">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="font-mono text-mono-xs text-text-1">{eventContract(event)}</div>
                              <div className="mt-1 text-caption text-text-3">{formatEtTime(event.fillTime)}</div>
                            </div>
                            <div className="text-right font-mono text-mono-xs text-text-1">{formatCompact(event.turnover, true)}</div>
                          </div>
                          <div className="mt-2 grid grid-cols-2 gap-2 text-caption text-text-2">
                            <span>{formatCompact(event.volume)} @ {formatNumber(event.price)}</span>
                            <span className="text-right">Bid/Ask {formatNumber(event.bidPrice)} / {formatNumber(event.askPrice)}</span>
                            <span>IV {formatPercent(event.ivPercent)}</span>
                            <span className="text-right">Vol/OI {formatRatio(event.voRatioPercent === null ? null : event.voRatioPercent / 100)}</span>
                          </div>
                          <div className="mt-2 text-caption text-text-3">{[event.sentiment, event.strategyType, ...event.orderTypes].filter(Boolean).join(' · ') || '未标注'}</div>
                        </article>
                      ))}
                    </div>
                  </>
                )}
                {!eventsLoading && !eventsError && eventItem && eventItem.events.length === 0 && (
                  <p className="text-body-sm text-text-3">{eventItem.message || '当前窗口没有返回异常成交事件。'}</p>
                )}
              </div>
            )}

            {researchTab === 'method' && (
              <div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {[
                    ['信号截面', runMeta?.marketDateEt ?? '—'],
                    ['K线更新', formatEtTime(history?.lastBarAt)],
                    ['期权成交量', `${overview?.sessionVolumeDate ?? '—'} 当日累计`],
                    ['未平仓量', `${overview?.openInterestAsOf ?? '—'} · T-1 清算`],
                  ].map(([label, value]) => (
                    <div key={label} className="border border-subtle bg-bg-2 p-3">
                      <div className="text-caption text-text-3">{label}</div>
                      <div className="mt-1 font-mono text-mono-sm text-text-1">{value}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-5 grid gap-5 text-body-sm leading-6 text-text-2 lg:grid-cols-2">
                  <div>
                    <h3 className="font-semibold text-text-1">概率区间怎么算</h3>
                    <p className="mt-2">先将年化 IV 换算为期限波动率 v = IV × √(N/252)，再用 S × exp(−0.5v² ± z·v) 计算终值边界。z=1 对应约 68.27%，z=2 对应约 95.45%。</p>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-text-3">
                      <li>这是零漂移近似下的 IV 对数正态模型分布，不是历史样本概率。</li>
                      <li>它不是上涨/下跌预测、目标价、策略胜率或盘中触及概率。</li>
                      <li>方向概率需等历史同类信号积累后，再做样本外校准。</li>
                    </ul>
                  </div>
                  <div>
                    <h3 className="font-semibold text-text-1">使用限制与执行门禁</h3>
                    <ul className="mt-2 list-disc space-y-1 pl-5">
                      <li>OI 与当日成交量的时点不同，不能单独判断流动性或方向。</li>
                      <li>期权墙是集中位置，不是确定支撑、阻力或 dealer 仓位。</li>
                      <li>执行前仍需检查具体合约的实时 Bid/Ask、价差、深度和事件风险。</li>
                    </ul>
                  </div>
                </div>

                <details className="mt-5 border-t border-subtle pt-3 text-body-sm text-text-2">
                  <summary className="cursor-pointer select-none text-text-2 hover:text-text-1">查看数据源返回的限制</summary>
                  {limitations.length > 0 ? (
                    <ul className="mt-3 list-disc space-y-1 pl-5">{limitations.slice(0, 8).map((item) => <li key={item}>{item}</li>)}</ul>
                  ) : (
                    <p className="mt-3 text-text-3">没有额外限制说明；仍应遵守上方通用边界。</p>
                  )}
                </details>
                <div className="mt-4 border-t border-subtle pt-3 font-mono text-mono-xs text-text-3">Signal {runMeta?.signalVersion ?? '—'} · Option source {overview?.source ?? wall?.source ?? eventItem?.source ?? '未报告'} · Page is read-only</div>
              </div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
};

export default OpportunityDetailPage;

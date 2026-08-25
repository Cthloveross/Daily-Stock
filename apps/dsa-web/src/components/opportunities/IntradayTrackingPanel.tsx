import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import {
  fetchIntradayTracking,
  normalizeSupportedUsOptionUnderlying,
} from '../../api/opportunities';
import type {
  IntradaySessionState,
  IntradayTrackingItem,
  IntradayTrackingResponse,
  OpportunityCandidate,
} from '../../types/opportunities';
import { Button } from '../ui';
import { parseApiTimestamp } from '../../utils/marketTime';
import {
  computePlanDistances,
  deriveFrozenPlanLevels,
  formatAtrMultiple,
  formatSignedDollars,
  type PlanDistance,
} from './intradayTracking';

/** 自动刷新间隔：仅在页面可见且盘段为盘前/盘中时轮询。 */
export const INTRADAY_POLL_INTERVAL_MS = 60_000;

const SESSION_STATE_LABELS: Record<IntradaySessionState, string> = {
  premarket: '盘前',
  regular: '盘中',
  afterhours: '盘后',
  closed: '休市',
};

const ITEM_STATE_LABELS: Record<IntradayTrackingItem['state'], string> = {
  ready: '实时',
  partial: '部分标缺',
  not_configured: '未配置',
  unavailable: '不可用',
};

const MISSING_REASON_LABELS: Record<string, string> = {
  moomoo_not_configured: 'Moomoo 未启用',
  quote_unavailable: '快照不可用',
  missing_or_nonpositive_volume: '缺累计成交量',
  missing_or_nonpositive_turnover: '缺累计成交额',
  missing_turnover_and_volume: '缺累计量额',
  missing_session_volume: '缺累计成交量',
};

function formatPrice(value: number | null): string {
  if (value === null) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatQuoteAsOf(item: IntradayTrackingItem): string {
  const fromQuote = item.quoteAsOf?.match(/\d{2}:\d{2}(?::\d{2})?/)?.[0];
  if (fromQuote) return `${fromQuote} ET`;
  const parsed = parseApiTimestamp(item.fetchedAt);
  if (!parsed) return '时点未报告';
  return `${new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(parsed)} ET`;
}

function formatEtDateTime(raw: string | null | undefined): string {
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

function missingLabel(reason: string | null): string {
  if (!reason) return '标缺';
  const mapped = MISSING_REASON_LABELS[reason];
  return mapped ? `标缺 · ${mapped}` : '标缺';
}

function vwapCell(item: IntradayTrackingItem): { primary: string; secondary: string } {
  if (item.vwap === null || item.lastPrice === null) {
    return {
      primary: missingLabel(item.vwapUnavailableReason),
      secondary: '成交额/成交量近似',
    };
  }
  const side = item.lastPrice > item.vwap ? 'VWAP 上方' : item.lastPrice < item.vwap ? 'VWAP 下方' : 'VWAP 持平';
  return { primary: side, secondary: `≈ ${formatPrice(item.vwap)} · 额/量近似` };
}

function paceCell(item: IntradayTrackingItem): { primary: string; secondary: string } {
  if (item.volumePaceRatio === null) {
    return {
      primary: missingLabel(item.volumePaceUnavailableReason),
      secondary: 'vs 20日全日中位',
    };
  }
  return {
    primary: `${item.volumePaceRatio.toLocaleString('en-US', { maximumFractionDigits: 2 })}×`,
    secondary: 'vs 20日全日中位（未按时点折算）',
  };
}

function distanceCell(
  label: string,
  level: number | null,
  distanceValue: PlanDistance,
): { primary: string; secondary: string } {
  if (level === null || distanceValue.dollars === null) {
    return { primary: '标缺', secondary: label };
  }
  return {
    primary: `${formatSignedDollars(distanceValue.dollars)} · ${formatAtrMultiple(distanceValue.atrMultiple)}`,
    secondary: `${label} ${formatPrice(level)}`,
  };
}

export function IntradayTrackingPanel({ candidates }: { candidates: OpportunityCandidate[] }) {
  const tracked = useMemo(
    () => candidates
      .map((candidate) => ({
        candidate,
        ticker: normalizeSupportedUsOptionUnderlying(candidate.ticker),
      }))
      .filter((entry): entry is { candidate: OpportunityCandidate; ticker: string } => (
        entry.ticker !== null
      ))
      .slice(0, 5),
    [candidates],
  );
  const symbols = useMemo(() => tracked.map((entry) => entry.ticker), [tracked]);

  const [data, setData] = useState<IntradayTrackingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [pageVisible, setPageVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState === 'visible',
  );
  const requestSequence = useRef(0);

  const load = useCallback(async (refresh: boolean) => {
    if (symbols.length === 0) return;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setLoading(true);
    try {
      const result = await fetchIntradayTracking(symbols, { refresh });
      if (requestSequence.current !== requestId) return;
      setData(result);
      setError(null);
    } catch (caught) {
      if (requestSequence.current !== requestId) return;
      setError(caught instanceof Error ? caught.message : '今日计划跟踪读取失败');
    } finally {
      if (requestSequence.current === requestId) setLoading(false);
    }
  }, [symbols]);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    const onVisibilityChange = () => {
      setPageVisible(document.visibilityState === 'visible');
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

  const sessionState = data?.sessionState ?? null;
  // 只在页面可见且盘段为盘前/盘中时轮询；其他情况彻底停表，不做后台请求。
  const pollingActive = autoRefresh
    && pageVisible
    && symbols.length > 0
    && (sessionState === 'regular' || sessionState === 'premarket');

  useEffect(() => {
    if (!pollingActive) return undefined;
    const timer = window.setInterval(() => {
      void load(false);
    }, INTRADAY_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [pollingActive, load]);

  if (tracked.length === 0) return null;

  const sessionLabel = sessionState ? SESSION_STATE_LABELS[sessionState] : '读取中';
  const itemsByTicker = new Map((data?.items ?? []).map((item) => [item.ticker, item]));

  return (
    <section
      aria-label="今日计划跟踪"
      className="border-t border-subtle bg-bg-1"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-subtle px-4 py-3">
        <div>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h3 className="text-h3 font-semibold text-text-1">
              今日计划跟踪 · 盘前冻结计划走到哪了
            </h3>
            <span className="text-caption text-text-3">
              跟踪冻结的盘前计划 · 不是信号 · 不改变盘前排名
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-text-3">
            <span>
              数据时点 <strong className="font-mono font-medium text-text-2">
                {data ? `${formatEtDateTime(data.generatedAt)} ET` : '读取中'}
              </strong>
            </span>
            <span>
              盘段 <strong className="font-medium text-text-2">{sessionLabel}</strong>
            </span>
            <span>确认/失效价取自冻结候选证据（前20日高低 / EMA13）</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-caption text-text-2">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
              aria-label="自动刷新今日计划跟踪"
            />
            自动刷新（60 秒 · 仅盘前/盘中且页面可见）
          </label>
          <Button
            variant="secondary"
            size="sm"
            disabled={loading}
            onClick={() => void load(true)}
            aria-label="手动刷新今日计划跟踪"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : undefined} />
            刷新
          </Button>
        </div>
      </header>

      {error && (
        <div className="border-b border-[color:var(--warn-muted)] bg-bg-0 px-4 py-2 text-caption text-warning" role="status">
          今日计划跟踪暂不可用：{error}
          {data ? `。仍显示 ${formatEtDateTime(data.generatedAt)} ET 的上一次结果。` : '。'}
        </div>
      )}

      <div className="overflow-auto">
        <table className="w-full min-w-[880px] border-collapse" aria-label="今日计划跟踪表">
          <thead>
            <tr className="border-b border-subtle text-left text-caption text-text-3">
              <th className="px-3 py-2 font-medium">标的</th>
              <th className="px-3 py-2 text-right font-medium">现价（as-of）</th>
              <th className="px-3 py-2 text-right font-medium">距确认位</th>
              <th className="px-3 py-2 text-right font-medium">距失效位</th>
              <th className="px-3 py-2 font-medium">VWAP</th>
              <th className="px-3 py-2 text-right font-medium">量能节奏</th>
              <th className="px-3 py-2 font-medium">盘段状态</th>
            </tr>
          </thead>
          <tbody>
            {tracked.map(({ candidate, ticker }) => {
              const item = itemsByTicker.get(ticker);
              const levels = deriveFrozenPlanLevels(candidate);
              const distances = computePlanDistances(
                levels,
                item?.lastPrice ?? null,
                item?.atr14 ?? null,
              );
              const confirm = distanceCell(levels.confirmLabel, levels.confirmLevel, distances.confirm);
              const invalidation = distanceCell(
                levels.invalidationLabel,
                levels.invalidationLevel,
                distances.invalidation,
              );
              const vwap = item ? vwapCell(item) : null;
              const pace = item ? paceCell(item) : null;
              const directionLabel = levels.direction === 'bullish'
                ? '看多计划'
                : levels.direction === 'bearish'
                  ? '看空计划'
                  : '方向未定';
              return (
                <tr key={ticker} className="border-b border-subtle last:border-b-0">
                  <td className="px-3 py-2.5">
                    <div className="font-mono text-mono-sm font-semibold text-text-1">{`${ticker} · ${directionLabel}`}</div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-sm text-text-1">
                      {item ? formatPrice(item.lastPrice) : '读取中'}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">
                      {item ? formatQuoteAsOf(item) : '…'}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-xs text-text-1">{item ? confirm.primary : '读取中'}</div>
                    <div className="mt-0.5 text-caption text-text-3">{confirm.secondary}</div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-xs text-text-1">{item ? invalidation.primary : '读取中'}</div>
                    <div className="mt-0.5 text-caption text-text-3">{invalidation.secondary}</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="text-body-sm text-text-1">{vwap ? vwap.primary : '读取中'}</div>
                    <div className="mt-0.5 text-caption text-text-3">{vwap ? vwap.secondary : '…'}</div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-xs text-text-1">{pace ? pace.primary : '读取中'}</div>
                    <div className="mt-0.5 text-caption text-text-3">{pace ? pace.secondary : '…'}</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="text-body-sm text-text-1">
                      {item
                        ? item.state === 'ready' || item.state === 'partial'
                          ? sessionLabel
                          : ITEM_STATE_LABELS[item.state]
                        : '读取中'}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">
                      {item ? ITEM_STATE_LABELS[item.state] : '…'}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
        VWAP＝当日累计成交额 ÷ 累计成交量（近似，非逐笔官方 VWAP）；量能节奏＝当日累计 vs 20 日全日中位（未按盘中时点折算）；ATR14＝已完成日线 Wilder 平滑。缺失字段显式标缺，不以 0 冒充。
      </div>
    </section>
  );
}

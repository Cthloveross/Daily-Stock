import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import {
  fetchIntradayPulse,
  fetchIntradayTop,
  fetchPremarketCycleStatus,
} from '../api/opportunities';
import type {
  IntradayPulseResponse,
  IntradayTopResponse,
  OpportunityCandidate,
} from '../types/opportunities';
import { useUserWatchlistStore } from '../stores/userWatchlistStore';
import { IntradayPulseStrip } from '../components/opportunities/IntradayPulseStrip';
import { IntradayScanTable } from '../components/opportunities/IntradayScanTable';
import { IntradayOptionEventFeed } from '../components/opportunities/IntradayOptionEventFeed';
import { IntradayTrackingPanel } from '../components/opportunities/IntradayTrackingPanel';
import { Button } from '../components/ui';

/** 自动刷新间隔：仅在页面可见且盘段为盘前/盘中时轮询（与盘中跟踪面板一致）。 */
export const INTRADAY_PAGE_POLL_INTERVAL_MS = 60_000;

/**
 * 日内工作台：交易时段的单屏主界面。
 *
 * 自上而下：市场脉搏（SPY/QQQ/VIX + 盘段 + 刷新指示）→ 今日计划（冻结盘前
 * Top 5 对照，复用盘中跟踪面板）→ 日内扫描表（盘中滚动证据排名）+ 期权异动
 * feed。全部内容是盘中滚动研究：不是信号、不冻结、不进入统计；期权异动是
 * Moomoo 分类，不证明方向。周内（盘前冻结）研究仍在 /regime。
 */
const IntradayPage: React.FC = () => {
  const userTickers = useUserWatchlistStore((state) => state.tickers);
  const symbols = useMemo(() => userTickers.slice(0, 20), [userTickers]);

  const [pulse, setPulse] = useState<IntradayPulseResponse | null>(null);
  const [pulseError, setPulseError] = useState<string | null>(null);
  const [top, setTop] = useState<IntradayTopResponse | null>(null);
  const [topError, setTopError] = useState<string | null>(null);
  // 初始即为加载中：挂载 effect 内不做同步 setState（react-hooks 规则），
  // 手动刷新在事件回调里显式置回 loading。
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [pageVisible, setPageVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState === 'visible',
  );
  const [planCandidates, setPlanCandidates] = useState<OpportunityCandidate[] | null>(null);
  const [planMessage, setPlanMessage] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const load = useCallback(async (refresh: boolean) => {
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    try {
      const [pulseResult, topResult] = await Promise.allSettled([
        fetchIntradayPulse(),
        fetchIntradayTop(symbols, { limit: 5, refresh }),
      ]);
      if (requestSequence.current !== requestId) return;
      if (pulseResult.status === 'fulfilled') {
        setPulse(pulseResult.value);
        setPulseError(null);
      } else {
        setPulseError(
          pulseResult.reason instanceof Error ? pulseResult.reason.message : '脉搏读取失败',
        );
      }
      if (topResult.status === 'fulfilled') {
        setTop(topResult.value);
        setTopError(null);
      } else {
        setTopError(
          topResult.reason instanceof Error ? topResult.reason.message : '日内扫描读取失败',
        );
      }
    } finally {
      if (requestSequence.current === requestId) setLoading(false);
    }
  }, [symbols]);

  useEffect(() => {
    void load(false);
  }, [load]);

  // 冻结盘前计划只读一次：它在盘中不会变化，变化的只有对照它的实时行。
  useEffect(() => {
    let cancelled = false;
    fetchPremarketCycleStatus()
      .then((cycle) => {
        if (cancelled) return;
        const candidates = cycle.run?.candidates ?? [];
        if (cycle.state === 'published' && candidates.length > 0) {
          setPlanCandidates(candidates.slice(0, 5));
          setPlanMessage(null);
        } else {
          setPlanCandidates([]);
          setPlanMessage(
            cycle.state === 'published'
              ? '今日官方盘前版本没有可对照的候选。'
              : '今日尚无已发布的冻结盘前计划；日内扫描仍可用，但没有计划对照基准。',
          );
        }
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setPlanCandidates([]);
        setPlanMessage(
          `盘前计划状态读取失败：${caught instanceof Error ? caught.message : '未知错误'}`,
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onVisibilityChange = () => {
      setPageVisible(document.visibilityState === 'visible');
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

  const sessionState = top?.sessionState ?? pulse?.sessionState ?? null;
  // 只在页面可见且盘段为盘前/盘中时轮询；其他情况彻底停表，不做后台请求。
  const pollingActive = autoRefresh
    && pageVisible
    && (sessionState === 'regular' || sessionState === 'premarket');

  useEffect(() => {
    if (!pollingActive) return undefined;
    const timer = window.setInterval(() => {
      void load(false);
    }, INTRADAY_PAGE_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [pollingActive, load]);

  return (
    <div className="mx-auto max-w-[1720px] space-y-4 p-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-h2 font-semibold text-text-1">日内工作台</h1>
            <span className="text-caption text-text-3">
              盘中滚动研究 · 不是信号 · 不进入统计
            </span>
          </div>
          <p className="mt-1 max-w-4xl text-body-sm text-text-3">
            当日交易的单屏视图：市场脉搏、冻结盘前计划对照、日内滚动扫描与期权异动。
            周内（盘前冻结 · 数日至数周）研究在
            {' '}
            <Link to="/regime" className="text-text-2 underline-offset-2 hover:text-text-1 hover:underline">
              周内研究 →
            </Link>
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-caption text-text-2">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
              aria-label="自动刷新日内工作台"
            />
            自动刷新（60 秒 · 仅盘前/盘中且页面可见）
          </label>
          <Button
            variant="secondary"
            size="sm"
            disabled={loading}
            onClick={() => {
              setLoading(true);
              void load(true);
            }}
            aria-label="手动刷新日内工作台"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : undefined} />
            刷新
          </Button>
        </div>
      </header>

      <IntradayPulseStrip
        data={pulse}
        loading={loading}
        error={pulseError}
        pollingActive={pollingActive}
      />

      <section aria-label="今日计划" className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
          <h2 className="text-h3 font-semibold text-text-1">今日计划 · 盘前冻结对照</h2>
          <span className="text-caption text-text-3">
            冻结的官方盘前 Top 5 是唯一对照基准 · 盘中不重排该计划
          </span>
        </div>
        {planCandidates === null ? (
          <div className="px-4 py-4 text-body-sm text-text-3">盘前计划读取中…</div>
        ) : planCandidates.length > 0 ? (
          <IntradayTrackingPanel candidates={planCandidates} />
        ) : (
          <div className="px-4 py-4 text-body-sm text-text-3">{planMessage}</div>
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(340px,1fr)]">
        <IntradayScanTable data={top} loading={loading} error={topError} />
        <IntradayOptionEventFeed
          events={top?.recentOptionEvents ?? []}
          quoteSessionLabel={top?.quoteSessionLabel ?? null}
        />
      </div>
    </div>
  );
};

export default IntradayPage;

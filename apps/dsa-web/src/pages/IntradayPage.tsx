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
import { selectPlanTickers, useIntradayPlanStore } from '../stores/intradayPlanStore';
import { IntradayPulseStrip } from '../components/opportunities/IntradayPulseStrip';
import { IntradayDisciplineStrip } from '../components/opportunities/IntradayDisciplineStrip';
import { LaneChecklistPanel } from '../components/opportunities/LaneChecklistPanel';
import { IntradayPlanPanel } from '../components/opportunities/IntradayPlanPanel';
import { IntradayScanTable } from '../components/opportunities/IntradayScanTable';
import { PremarketOptionAnomalyPanel } from '../components/opportunities/PremarketOptionAnomalyPanel';
import { IntradayTrackingPanel } from '../components/opportunities/IntradayTrackingPanel';
import { Button } from '../components/ui';

/** 自动刷新间隔：仅在页面可见且盘段为盘前/盘中时轮询（与今日计划跟踪面板一致）。 */
export const INTRADAY_PAGE_POLL_INTERVAL_MS = 60_000;

/**
 * 日内工作台：交易时段的单屏主界面。
 *
 * 自上而下（2026-08-05 拆成「计划 / 扫描」两段）：市场脉搏（SPY/QQQ/VIX +
 * 盘段 + 刷新指示）→ 规模与频率（近 20 个交易日的镜子读数）→ 车道清单
 * （今日哪条车道成立）→ **盘中计划**（用户手动提升上来的标的，逐条机械核对
 * + 合约候选 + 张数/手续费 + 失效位提示）→ **实时扫描（滚动）**（现在谁在动，
 * 提升来源；盘中滚动证据排名 + 今日曾深扫账本）→ 今日计划跟踪（盘前冻结
 * 计划走到哪了）→ **盘前期权异常**（侧栏：深度层标的上一时段的 call/put
 * 偏斜与大单事实，盘前 + 开盘 30 分钟醒目、其后收起）。全部内容是盘中滚动
 * 研究：不是信号、不冻结、不进入统计；期权分类是 Moomoo 标签，不证明方向。
 * 周内研究仍在 /regime。2026-08-14 起盘中期权异动 feed 移入 Journal 仓位
 * 复盘面（用户定位：异动是复盘证据，不是盘中决策输入）。
 *
 * 盘中计划的标的以 additive 的 `focusSymbols` 传给服务端（**不进 `symbols`**）：
 * 服务端只在「空 symbols + 已配置 INTRADAY_WATCHLIST」时走两层扫描，混进
 * `symbols` 会像 `useUserWatchlistStore` 那样把两层模式整个关掉。
 */
const IntradayPage: React.FC = () => {
  const userTickers = useUserWatchlistStore((state) => state.tickers);
  const symbols = useMemo(() => userTickers.slice(0, 20), [userTickers]);
  // 盘中计划提升的标的走 additive 的 focusSymbols（**不进 symbols**）：
  // 服务端只在「空 symbols + 已配置 INTRADAY_WATCHLIST」时走两层扫描，
  // 混进 symbols 会像 userWatchlist 那样把两层模式整个关掉。
  const planTickers = useIntradayPlanStore(selectPlanTickers);

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
        fetchIntradayTop(symbols, { limit: 5, refresh, focusSymbols: planTickers }),
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
          topResult.reason instanceof Error ? topResult.reason.message : '实时扫描读取失败',
        );
      }
    } finally {
      if (requestSequence.current === requestId) setLoading(false);
    }
  }, [symbols, planTickers]);

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
              : '今日尚无已发布的冻结盘前计划；实时扫描仍可用，但没有计划对照基准。',
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
            当日交易的单屏视图：市场脉搏、冻结盘前计划对照、日内滚动扫描与盘前期权异常。
            期权异动 feed 已移至 Journal 仓位复盘（复盘证据，不是盘中输入）。
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

      {/* 规模与频率：紧随脉搏的一行镜子读数（每美元回报才是真账），不是警报。 */}
      <IntradayDisciplineStrip />

      {/* 开仓前车道检查：对照用户自己那套规则（V2-A…V2-E）的清单——今日无
          0DTE 时整块警示并把日内车道置灰；ET 时钟复用脉搏时点。 */}
      <LaneChecklistPanel pulse={pulse} top={top} loading={loading} />

      {/* 主次顺序：盘中计划（真正干活的地方）在最上，实时扫描（滚动，谁现在
          在动）在其下作为提升来源，今日计划跟踪其后；侧栏为盘前期权异常
          （昨日 call/put 偏斜与大单事实——盘前 + 开盘 30 分钟醒目，其后收起；
          盘中期权异动 feed 已移入 Journal 仓位复盘）。 */}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(340px,1fr)]">
        <div className="min-w-0 space-y-4">
          <IntradayPlanPanel pulse={pulse} top={top} loading={loading} />
          <IntradayScanTable data={top} loading={loading} error={topError} />
          <section aria-label="今日计划" className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1">
            {planCandidates !== null && planCandidates.length > 0 ? (
              // 面板自带「今日计划跟踪 · 盘前冻结计划走到哪了」标题与刷新控件。
              <IntradayTrackingPanel candidates={planCandidates} />
            ) : (
              <>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
                  <h2 className="text-h3 font-semibold text-text-1">
                    今日计划跟踪 · 盘前冻结计划走到哪了
                  </h2>
                  <span className="text-caption text-text-3">
                    冻结的官方盘前 Top 5 是唯一对照基准 · 盘中不重排该计划
                  </span>
                </div>
                <div className="px-4 py-4 text-body-sm text-text-3">
                  {planCandidates === null ? '盘前计划读取中…' : planMessage}
                </div>
              </>
            )}
          </section>
        </div>
        <PremarketOptionAnomalyPanel top={top} />
      </div>
    </div>
  );
};

export default IntradayPage;

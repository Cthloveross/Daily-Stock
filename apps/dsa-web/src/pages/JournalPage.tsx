import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import JournalImport from '../components/journal/JournalImport';
import EpisodeBuildManagementPanel from '../components/journal/EpisodeBuildManagementPanel';
import ReviewWorkbenchHeader from '../components/journal/ReviewWorkbenchHeader';
import DTEDistribution from '../components/journal/DTEDistribution';
import MonthlyReviewPanel from '../components/journal/MonthlyReviewPanel';
import RealityTestCard from '../components/journal/RealityTestCard';
import TradeTable from '../components/journal/TradeTable';
import StyleBreakdown from '../components/journal/StyleBreakdown';
import PnLByDte from '../components/journal/PnLByDte';
import FrameworkPanel from '../components/journal/FrameworkPanel';
import AskJournalChat from '../components/journal/AskJournalChat';
import PositionEpisodesPanel from '../components/journal/PositionEpisodesPanel';
import ReviewInsightsPanel from '../components/journal/ReviewInsightsPanel';
import DisciplineMonthlyPanel from '../components/journal/DisciplineMonthlyPanel';
import RuleCompliancePanel from '../components/journal/RuleCompliancePanel';
import JournalOptionEventReview from '../components/journal/JournalOptionEventReview';
import PlaybookPanel from '../components/journal/PlaybookPanel';
import CurrentPositionsSnapshotCard from '../components/journal/CurrentPositionsSnapshotCard';
import EdgePanelCard from '../components/journal/EdgePanelCard';
import { DailyReviewEntryCard } from '../components/journal/DailyReviewEntryCard';
import { positionReviewPath } from '../components/journal/review/journalReviewRouting';
import { useJournalStore } from '../stores/journalStore';
import { usePositionEpisodes } from '../hooks/usePositionEpisodes';
import { fetchStatsByStyle } from '../api/journal';
import { parseApiError, type ParsedApiError } from '../api/error';
import type {
  JournalStatsByStyleResponse,
  PositionEpisodeFilters,
  PositionEpisodeItem,
  TradeItem,
} from '../types/journal';

type Tab = 'positions' | 'import' | 'overview' | 'analysis' | 'trades' | 'reality' | 'framework' | 'ask' | 'reviews';

const TAB_ORDER: Tab[] = ['positions', 'import', 'overview', 'analysis', 'trades', 'reality', 'framework', 'ask', 'reviews'];

const TAB_LABEL: Record<Tab, string> = {
  positions: '仓位复盘',
  import: '数据与构建',
  overview: 'Overview · Legacy 存档',
  analysis: 'Analysis · Legacy',
  trades: 'Trades · Legacy',
  reality: 'Reality · Legacy',
  framework: 'Framework · Legacy',
  ask: 'Ask AI · Legacy',
  reviews: 'Reviews · Legacy',
};

const POSITION_STATUSES = new Set(['open', 'closed']);
const POSITION_COMPLETENESS = new Set(['exact', 'complete', 'partial']);
const POSITION_CASE_FOCUS = new Set([
  'top_profit',
  'top_loss',
  'largest_fee',
  'longest_hold',
  'weakest_evidence',
]);
const POSITION_REVIEW_STATUSES = new Set(['not_started', 'in_progress', 'completed']);

const fmtMoney = (n?: number | null) => {
  if (n == null) return '—';
  const sign = n > 0 ? '+' : n < 0 ? '\u2212' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
};
const fmtPct = (n?: number | null) => (n == null ? '—' : `${(n * 100).toFixed(0)}%`);

const JournalPage: React.FC = () => {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestedTab = (params.get('tab') as Tab) || 'positions';
  const tab = TAB_ORDER.includes(requestedTab) ? requestedTab : 'positions';
  const initialStyle = params.get('style') ?? '';

  const [symbol, setSymbol] = useState('');
  const [style, setStyle] = useState(initialStyle);
  const [statusFilter, setStatusFilter] = useState('');
  const [selected, setSelected] = useState<TradeItem | null>(null);
  const overviewLoadedRef = useRef(false);
  const previousTabRef = useRef<Tab | null>(null);

  // analysis state
  const [statsByStyle, setStatsByStyle] = useState<JournalStatsByStyleResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<ParsedApiError | null>(null);
  // Bump to reload the Playbook list after a bucket was saved as a candidate.
  const [playbookRefreshToken, setPlaybookRefreshToken] = useState(0);

  const { loadStats, loadTrades, stats, trades, tradesLoading } =
    useJournalStore();

  const positionFilters = useMemo<PositionEpisodeFilters>(() => {
    const lifecycleStatus = params.get('status') ?? '';
    const completenessStatus = params.get('completeness') ?? '';
    const caseFocus = params.get('case') ?? '';
    const reviewStatus = params.get('review_status') ?? '';
    const requestedPage = Number(params.get('page') ?? '1');
    const requestedBuildId = Number(params.get('build_id') ?? '');
    return {
      underlying: params.get('symbol')?.trim().toUpperCase() || undefined,
      lifecycleStatus: POSITION_STATUSES.has(lifecycleStatus)
        ? lifecycleStatus as PositionEpisodeFilters['lifecycleStatus']
        : '',
      completenessStatus: POSITION_COMPLETENESS.has(completenessStatus)
        ? completenessStatus as PositionEpisodeFilters['completenessStatus']
        : '',
      caseFocus: POSITION_CASE_FOCUS.has(caseFocus)
        ? caseFocus as PositionEpisodeFilters['caseFocus']
        : '',
      reviewStatus: POSITION_REVIEW_STATUSES.has(reviewStatus)
        ? reviewStatus as PositionEpisodeFilters['reviewStatus']
        : '',
      buildId: Number.isInteger(requestedBuildId) && requestedBuildId > 0
        ? requestedBuildId
        : undefined,
      page: Number.isInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1,
      perPage: 50,
    };
  }, [params]);

  // The controller feeds both the review workspace (positions) and the
  // build/activation management area now living on the data tab (import).
  const positionController = usePositionEpisodes({
    active: tab === 'positions' || tab === 'import',
    filters: positionFilters,
  });
  const dailyRefreshSectionRef = useRef<HTMLDivElement | null>(null);

  const setTab = (next: Tab) => {
    // keep url in sync for deep links (StyleBreakdown clicks `?tab=trades&style=xxx`)
    const nextParams = new URLSearchParams(params);
    nextParams.set('tab', next);
    setParams(nextParams, { replace: true });
  };

  // Legacy endpoints are intentionally dormant until their archived tab is
  // opened. This keeps the active position review free from old stats/trades logs.
  useEffect(() => {
    if (tab === 'overview' && !overviewLoadedRef.current) {
      overviewLoadedRef.current = true;
      void loadStats();
      void loadTrades({ perPage: 100, style: initialStyle || undefined });
    }
    const enteredLegacyTrades = tab === 'trades' && previousTabRef.current !== 'trades';
    previousTabRef.current = tab;
    if (enteredLegacyTrades) {
      void loadTrades({
        symbol: symbol || undefined,
        style: style || undefined,
        status: statusFilter || undefined,
        perPage: 100,
      });
    }
    // Entering a Legacy tab is the load boundary; draft filter edits do not
    // issue requests until Apply is clicked.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // Lazy-load the analysis breakdown when user enters that tab for the first time.
  useEffect(() => {
    if (tab !== 'analysis' || statsByStyle || statsLoading) return;
    let cancelled = false;
    const run = async () => {
      setStatsLoading(true);
      setStatsError(null);
      try {
        const resp = await fetchStatsByStyle({ topN: 5 });
        if (!cancelled) setStatsByStyle(resp);
      } catch (e) {
        if (!cancelled) setStatsError(parseApiError(e));
      } finally {
        if (!cancelled) setStatsLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [tab, statsByStyle, statsLoading]);

  // Keep style filter in sync with URL when Trades tab is active.
  useEffect(() => {
    if (tab !== 'trades') return;
    const urlStyle = params.get('style') ?? '';
    if (urlStyle !== style) {
      setStyle(urlStyle);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, params]);

  const refreshLegacyTrades = () => {
    void loadTrades({
      symbol: symbol || undefined,
      style: style || undefined,
      status: statusFilter || undefined,
      perPage: 100,
    });
  };

  const items = useMemo(() => trades?.items ?? [], [trades]);

  const applyPositionFilters = (filters: PositionEpisodeFilters) => {
    const next = new URLSearchParams(params);
    next.set('tab', 'positions');
    next.delete('style');
    const setOrDelete = (key: string, value?: string) => {
      if (value) next.set(key, value);
      else next.delete(key);
    };
    setOrDelete('symbol', filters.underlying);
    setOrDelete('status', filters.lifecycleStatus);
    setOrDelete('completeness', filters.completenessStatus);
    setOrDelete('case', filters.caseFocus);
    setOrDelete('review_status', filters.reviewStatus);
    if ((filters.page ?? 1) > 1) next.set('page', String(filters.page));
    else next.delete('page');
    setParams(next, { replace: true });
  };

  const changePositionPage = (page: number) => {
    applyPositionFilters({ ...positionFilters, page });
  };

  const selectPositionBuild = (buildId?: number) => {
    const next = new URLSearchParams(params);
    next.set('tab', 'positions');
    next.delete('page');
    if (buildId != null) next.set('build_id', String(buildId));
    else next.delete('build_id');
    setParams(next, { replace: true });
  };

  const refreshPositionEvidence = () => {
    positionController.reload();
    positionController.reloadCanonicalPreview();
  };

  const openPositionReview = (item: PositionEpisodeItem) => {
    navigate(positionReviewPath(item.id, params));
  };

  return (
    <div className="mx-auto max-w-6xl p-4">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Journal · Evidence first</div>
          <h1 className="mt-1 text-h1 text-text-1">期权交易复盘</h1>
          <p className="mt-1 text-body-sm text-text-3">当前分析主线是不可变证据账本与 PositionEpisode；旧 FIFO 页面仅作存档查阅。</p>
        </div>
        <span className="rounded-full border border-up-strong/25 bg-up-subtle px-3 py-1 text-caption text-up-strong">Moomoo 只读 · 不下单</span>
      </div>
      <div className="mb-6 flex flex-wrap items-center gap-2 border-b border-subtle">
        {TAB_ORDER.map((t) => (
          <button
            key={t}
            type="button"
            className={`px-3 py-2 text-body-sm font-medium ${
              tab === t
                ? 'border-b-2 border-accent text-text-1'
                : 'text-text-2 hover:text-text-1'
            }`}
            onClick={() => setTab(t)}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      {tab !== 'positions' && tab !== 'import' && (
        <div className="mb-4 rounded-ds-md border border-warn-strong/25 bg-warn-subtle px-4 py-3 text-body-sm text-warn-strong" role="status">
          Legacy / 存档视图：这里的数据来自旧 Journal，不参与当前仓位复盘与核心指标。
        </div>
      )}

      {tab === 'positions' && (
        <div className="space-y-4">
          {/* 日终复盘入口（蓝图 17 Phase A）：未开始/进行中/已密封/休息日。 */}
          <DailyReviewEntryCard />
          {/* Edge 面板（doc 16 E-5）：今日 0-1DTE gate 档位 + 分桶期望 + R2/R3
              纪律红灯。gate 仅作防御（只关不开），行情取不到时 fail closed。 */}
          <EdgePanelCard />
          <ReviewWorkbenchHeader
            build={positionController.list?.build ?? null}
            reviewQueue={positionController.list?.reviewQueue ?? null}
            notBuilt={positionController.list?.dataState === 'not_built'}
            loading={positionController.loading}
            viewingBuildId={positionFilters.buildId}
            onOpenReview={openPositionReview}
            onOpenBuildTools={() => setTab('import')}
          />
          <PositionEpisodesPanel
            key={`${positionFilters.buildId ?? 'default'}:${positionFilters.underlying ?? ''}:${positionFilters.lifecycleStatus ?? ''}:${positionFilters.completenessStatus ?? ''}:${positionFilters.caseFocus ?? ''}:${positionFilters.reviewStatus ?? ''}:${positionFilters.page ?? 1}`}
            filters={positionFilters}
            controller={positionController}
            onApplyFilters={applyPositionFilters}
            onPageChange={changePositionPage}
            onSelectBuild={selectPositionBuild}
            onOpenReview={openPositionReview}
            onOpenBuildTools={() => setTab('import')}
          />
          <ReviewInsightsPanel
            onCandidateSaved={() => setPlaybookRefreshToken((token) => token + 1)}
          />
          {/* 模式观察的同层兄弟：规模与频率（每美元回报才是真账）按月摊开。 */}
          <DisciplineMonthlyPanel />
          {/* 规模与频率的同层兄弟：两车道规则的遵守度记账（合规单 vs 违规单）。 */}
          <RuleCompliancePanel />
          {/* 期权异动 feed（2026-08-14 自 /intraday 迁入）：复盘证据——回看
              当天/最近时段发生了什么；只读一次、不轮询，分类不证明方向。 */}
          <JournalOptionEventReview />
          <PlaybookPanel refreshToken={playbookRefreshToken} />
        </div>
      )}

      {tab === 'overview' && (
        <div className="space-y-4">
          {/* Descriptive mix metrics. Neither metric is a standalone profitability
              or risk verdict; users should compare them with payoff, fees, and P&L. */}
          {stats && (
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <div className="text-label uppercase text-text-3">Win rate</div>
                <div className="mt-1 font-mono text-mono-lg tabular-nums text-text-1">
                  {stats.winRate == null ? '—' : fmtPct(stats.winRate)}
                </div>
                <div className="mt-1 text-caption text-text-3">
                  Descriptive only · combine with payoff ratio and fees
                </div>
              </div>
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <div className="text-label uppercase text-text-3">0DTE share</div>
                <div className="mt-1 font-mono text-mono-lg tabular-nums text-text-1">
                  {(() => {
                    const total = Object.values(stats.dteDistribution).reduce((a, b) => a + b, 0);
                    const zero = stats.dteDistribution['0DTE'] ?? 0;
                    return total ? fmtPct(zero / total) : '—';
                  })()}
                </div>
                <div className="mt-1 text-caption text-text-3">
                  Trade mix only · compare P&amp;L, fees, and risk by DTE
                </div>
              </div>
            </div>
          )}

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <RealityTestCard />
            <DTEDistribution stats={stats} />
          </div>

          <div className="rounded-ds-md border border-subtle bg-bg-1">
            <div className="flex items-center justify-between border-b border-subtle px-4 py-3">
              <div className="text-label uppercase text-text-3">Most recent closed trades</div>
              <button
                type="button"
                onClick={() => setTab('trades')}
                className="text-body-sm text-text-2 hover:text-text-1"
              >
                View all →
              </button>
            </div>
            <TradeTable
              items={items.filter((i) => i.status === 'closed').slice(0, 10)}
              loading={tradesLoading}
              onRowClick={setSelected}
            />
          </div>
        </div>
      )}

      {tab === 'analysis' && (
        <div className="space-y-4">
          {statsError && (
            <div className="rounded-ds-md border border-subtle bg-bg-1 p-4 text-body-sm text-down-strong">
              {statsError.message}
            </div>
          )}
          {statsLoading && !statsByStyle && (
            <div className="rounded-ds-md border border-subtle bg-bg-1 p-8 text-center text-body-sm text-text-3">
              加载分类统计中…
            </div>
          )}
          {statsByStyle && (
            <>
              <div className="grid gap-4 lg:grid-cols-2">
                <StyleBreakdown items={statsByStyle.byStyle} />
                <PnLByDte items={statsByStyle.byDte} />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="rounded-ds-md border border-subtle bg-bg-1 p-4">
                  <div className="flex items-center justify-between">
                    <div className="text-label uppercase text-text-3">Worst trades</div>
                    <div className="font-mono text-mono-xs text-text-3">
                      {statsByStyle.worstTrades.length}
                    </div>
                  </div>
                  <ul className="mt-3 divide-y divide-[color:var(--border-subtle)]">
                    {statsByStyle.worstTrades.map((t) => (
                      <li key={t.id ?? Math.random()} className="flex items-center justify-between py-2 text-body-sm">
                        <span className="font-mono text-mono-sm text-text-1">
                          {t.underlying ?? '?'} · {t.tradeStyle ?? '—'}
                        </span>
                        <span className="font-mono tabular-nums text-down-strong">
                          {fmtMoney(t.pnlNet)}
                          {t.pnlPct != null && <span className="ml-1 text-text-3">({fmtPct(t.pnlPct / 100)})</span>}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="rounded-ds-md border border-subtle bg-bg-1 p-4">
                  <div className="flex items-center justify-between">
                    <div className="text-label uppercase text-text-3">Best trades</div>
                    <div className="font-mono text-mono-xs text-text-3">
                      {statsByStyle.bestTrades.length}
                    </div>
                  </div>
                  <ul className="mt-3 divide-y divide-[color:var(--border-subtle)]">
                    {statsByStyle.bestTrades.map((t) => (
                      <li key={t.id ?? Math.random()} className="flex items-center justify-between py-2 text-body-sm">
                        <span className="font-mono text-mono-sm text-text-1">
                          {t.underlying ?? '?'} · {t.tradeStyle ?? '—'}
                        </span>
                        <span className="font-mono tabular-nums text-up-strong">
                          {fmtMoney(t.pnlNet)}
                          {t.pnlPct != null && <span className="ml-1 text-text-3">({fmtPct(t.pnlPct / 100)})</span>}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>

              <div className="rounded-ds-md border border-subtle bg-bg-1 px-4 py-3 text-body-sm text-text-3">
                共 {statsByStyle.totalCount} 笔已平仓 · 合计{' '}
                <span
                  className={`font-mono tabular-nums ${
                    statsByStyle.totalPnlNet >= 0 ? 'text-up-strong' : 'text-down-strong'
                  }`}
                >
                  {fmtMoney(statsByStyle.totalPnlNet)}
                </span>
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'trades' && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <input
              className="input-base"
              placeholder="Symbol (e.g. NVDA)"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
            <input
              className="input-base"
              placeholder="Style"
              value={style}
              onChange={(e) => setStyle(e.target.value)}
            />
            <select
              className="input-base"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">Status: All</option>
              <option value="closed">closed</option>
              <option value="open">open</option>
            </select>
            <button type="button" className="btn-primary" onClick={refreshLegacyTrades}>
              Apply
            </button>
          </div>
          <TradeTable items={items} loading={tradesLoading} onRowClick={setSelected} />
        </div>
      )}

      {tab === 'reality' && (
        <div className="space-y-4">
          <RealityTestCard topN={5} />
          <RealityTestCard topN={10} />
          <div className="rounded-ds-md border border-subtle bg-bg-1 p-4 text-body-sm text-text-3">
            <p>
              Reality Test 是 Phase 0 的灵魂指标：去掉 Top-N 最大盈利后，你真实的 PnL 是多少？
              当你的业绩高度依赖少数几笔爆发，说明方法论尚未稳定——这不是失败，而是数据提醒。
            </p>
          </div>
        </div>
      )}

      {tab === 'framework' && <FrameworkPanel />}

      {tab === 'ask' && <AskJournalChat />}

      {tab === 'reviews' && (
        <div className="space-y-4">
          <MonthlyReviewPanel />
        </div>
      )}

      {tab === 'import' && (
        <div className="space-y-8">
          <div ref={dailyRefreshSectionRef} className="space-y-4">
            <JournalImport onImported={refreshPositionEvidence} />
            <div className="rounded-ds-md border border-subtle bg-bg-1 p-4 text-body-sm text-text-3">
              <p>
                文件会先做只读证据检查。CSV 可在明确确认后幂等追加到可信证据账本；
                OpenAPI JSON 会先生成零证据行写入计划，明确确认后原子、幂等追加。
                旧 FIFO、交易备注、历史复盘和 Episode 不会被自动重建。
              </p>
            </div>
          </div>

          <section className="space-y-4" aria-label="当前持仓快照与未来构建">
            <div>
              <div className="text-label uppercase tracking-label text-text-3">Data · Snapshot</div>
              <h2 className="mt-0.5 text-h2 text-text-1">当前持仓快照与未来构建</h2>
            </div>
            <CurrentPositionsSnapshotCard
              onOpenEvidence={() => dailyRefreshSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            />
          </section>

          <section className="space-y-4" aria-label="构建与默认视图管理">
            <div>
              <div className="text-label uppercase tracking-label text-text-3">Data · Builds</div>
              <h2 className="mt-0.5 text-h2 text-text-1">构建与默认视图管理</h2>
            </div>
            <EpisodeBuildManagementPanel
              filters={positionFilters}
              controller={positionController}
              onSelectBuild={selectPositionBuild}
            />
          </section>
        </div>
      )}

      {selected && (
        <div
          className="fixed inset-0 flex items-start justify-end bg-black/40"
          onClick={() => setSelected(null)}
        >
          <div className="h-full w-full max-w-lg overflow-y-auto bg-bg-0 p-6" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-baseline justify-between">
              <h2 className="text-h2 text-text-1">{selected.rawSymbol ?? selected.underlying}</h2>
              <button type="button" className="btn-ghost" onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
            <pre className="overflow-auto rounded-ds-sm bg-bg-1 p-3 text-body-sm text-text-1">
              {JSON.stringify(selected, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

export default JournalPage;

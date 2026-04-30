import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import JournalImport from '../components/journal/JournalImport';
import DTEDistribution from '../components/journal/DTEDistribution';
import MonthlyReviewPanel from '../components/journal/MonthlyReviewPanel';
import RealityTestCard from '../components/journal/RealityTestCard';
import TradeTable from '../components/journal/TradeTable';
import StyleBreakdown from '../components/journal/StyleBreakdown';
import PnLByDte from '../components/journal/PnLByDte';
import FrameworkPanel from '../components/journal/FrameworkPanel';
import AskJournalChat from '../components/journal/AskJournalChat';
import { useJournalStore } from '../stores/journalStore';
import { fetchStatsByStyle } from '../api/journal';
import { parseApiError, type ParsedApiError } from '../api/error';
import type { JournalStatsByStyleResponse, TradeItem } from '../types/journal';

type Tab = 'overview' | 'analysis' | 'trades' | 'reality' | 'framework' | 'ask' | 'reviews' | 'import';

const TAB_ORDER: Tab[] = ['overview', 'analysis', 'trades', 'reality', 'framework', 'ask', 'reviews', 'import'];

const TAB_LABEL: Record<Tab, string> = {
  overview: 'Overview',
  analysis: 'Analysis',
  trades: 'Trades',
  reality: 'Reality',
  framework: 'Framework',
  ask: 'Ask AI',
  reviews: 'Reviews',
  import: 'Import',
};

const fmtMoney = (n?: number | null) => {
  if (n == null) return '—';
  const sign = n > 0 ? '+' : n < 0 ? '\u2212' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
};
const fmtPct = (n?: number | null) => (n == null ? '—' : `${(n * 100).toFixed(0)}%`);

const JournalPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const initialTab = (params.get('tab') as Tab) || 'overview';
  const initialStyle = params.get('style') ?? '';

  const [tab, setTabRaw] = useState<Tab>(TAB_ORDER.includes(initialTab) ? initialTab : 'overview');
  const [symbol, setSymbol] = useState('');
  const [style, setStyle] = useState(initialStyle);
  const [statusFilter, setStatusFilter] = useState('');
  const [selected, setSelected] = useState<TradeItem | null>(null);

  // analysis state
  const [statsByStyle, setStatsByStyle] = useState<JournalStatsByStyleResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<ParsedApiError | null>(null);

  const { loadStats, loadTrades, loadRealityTest, stats, trades, tradesLoading } =
    useJournalStore();

  const setTab = (next: Tab) => {
    setTabRaw(next);
    // keep url in sync for deep links (StyleBreakdown clicks `?tab=trades&style=xxx`)
    const nextParams = new URLSearchParams(params);
    nextParams.set('tab', next);
    setParams(nextParams, { replace: true });
  };

  useEffect(() => {
    void loadStats();
    void loadRealityTest();
    void loadTrades({ perPage: 100, style: initialStyle || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const refresh = () => {
    void loadStats();
    void loadRealityTest();
    void loadTrades({
      symbol: symbol || undefined,
      style: style || undefined,
      status: statusFilter || undefined,
      perPage: 100,
    });
  };

  const applyStyleFromUrl = () => {
    void loadTrades({
      symbol: symbol || undefined,
      style: style || undefined,
      status: statusFilter || undefined,
      perPage: 100,
    });
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(applyStyleFromUrl, [style]);

  const items = useMemo(() => trades?.items ?? [], [trades]);

  return (
    <div className="mx-auto max-w-6xl p-4">
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

      {tab === 'overview' && (
        <div className="space-y-4">
          {/* Two small tiles that DON'T overlap with what RealityTestCard shows
              (total_trades / total_pnl_net / median). Win rate + 0DTE share
              are the two most useful "are we violating the framework" signals. */}
          {stats && (
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <div className="text-label uppercase text-text-3">Win rate</div>
                <div className="mt-1 font-mono text-mono-lg tabular-nums text-text-1">
                  {stats.winRate == null ? '—' : fmtPct(stats.winRate)}
                </div>
                <div className="mt-1 text-caption text-text-3">
                  {stats.winRate != null && stats.winRate >= 0.5
                    ? 'above break-even'
                    : 'below break-even'}
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
                <div className="mt-1 text-caption text-text-3">dangerous if &gt; 30%</div>
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
            <button type="button" className="btn-primary" onClick={refresh}>
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
        <div className="space-y-4">
          <JournalImport onImported={refresh} />
          <div className="rounded-ds-md border border-subtle bg-bg-1 p-4 text-body-sm text-text-3">
            <p>Import 之后 FIFO 会自动重配；CSV 已处理过会被跳过。</p>
          </div>
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

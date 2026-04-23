import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import JournalImport from '../components/journal/JournalImport';
import DTEDistribution from '../components/journal/DTEDistribution';
import MonthlyReviewPanel from '../components/journal/MonthlyReviewPanel';
import RealityTestCard from '../components/journal/RealityTestCard';
import TradeTable from '../components/journal/TradeTable';
import { useJournalStore } from '../stores/journalStore';
import type { TradeItem } from '../types/journal';

type Tab = 'overview' | 'trades' | 'reality' | 'reviews' | 'import';

const JournalPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [symbol, setSymbol] = useState('');
  const [style, setStyle] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [selected, setSelected] = useState<TradeItem | null>(null);

  const { loadStats, loadTrades, loadRealityTest, stats, trades, tradesLoading } =
    useJournalStore();

  useEffect(() => {
    void loadStats();
    void loadRealityTest();
    void loadTrades({ perPage: 100 });
  }, [loadStats, loadRealityTest, loadTrades]);

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

  const items = useMemo(() => trades?.items ?? [], [trades]);

  return (
    <div className="mx-auto max-w-6xl p-4">
      <div className="mb-6 flex flex-wrap items-center gap-2 border-b border-base-subtle">
        {(['overview', 'trades', 'reality', 'reviews', 'import'] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`px-3 py-2 text-sm font-medium capitalize ${
              tab === t ? 'border-b-2 border-cyan text-primary' : 'text-muted'
            }`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="space-y-4">
          <RealityTestCard />
          <DTEDistribution stats={stats} />
          <div className="card-base p-4">
            <h3 className="text-lg font-semibold">Most recent closed trades</h3>
            <div className="mt-3">
              <TradeTable
                items={items.filter((i) => i.status === 'closed').slice(0, 10)}
                loading={tradesLoading}
                onRowClick={setSelected}
              />
            </div>
          </div>
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
          <div className="card-base p-4 text-sm text-muted">
            <p>
              Reality Test 是 Phase 0 的灵魂指标：去掉 Top-N 最大盈利后，你真实的 PnL 是多少？
              当你的业绩高度依赖少数几笔爆发，说明方法论尚未稳定——这不是失败，而是数据提醒。
            </p>
          </div>
        </div>
      )}

      {tab === 'reviews' && (
        <div className="space-y-4">
          <MonthlyReviewPanel />
        </div>
      )}

      {tab === 'import' && (
        <div className="space-y-4">
          <JournalImport onImported={refresh} />
          <div className="card-base p-4 text-sm text-muted">
            <p>Import 之后 FIFO 会自动重配；CSV 已处理过会被跳过。</p>
          </div>
        </div>
      )}

      {selected && (
        <div
          className="fixed inset-0 flex items-start justify-end bg-black/40"
          onClick={() => setSelected(null)}
        >
          <div className="h-full w-full max-w-lg overflow-y-auto bg-base p-6" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-baseline justify-between">
              <h2 className="text-xl font-semibold">{selected.rawSymbol ?? selected.underlying}</h2>
              <button type="button" className="btn-ghost" onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
            <pre className="overflow-auto rounded bg-base-subtle p-3 text-xs">
              {JSON.stringify(selected, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

export default JournalPage;

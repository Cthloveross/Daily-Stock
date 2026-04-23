import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Columns3, Download, Search, SlidersHorizontal } from 'lucide-react';
import { DataTable, EmptyState, Input, Tabs, type ColumnDef } from '../components/ui';
import { PriceCell } from '../components/data/PriceCell';
import { ChangeCell } from '../components/data/ChangeCell';
import { Sparkline } from '../components/data/Sparkline';
import { MASlopeCell, type MATrend } from '../components/data/MASlopeCell';
import { useRegimeStore } from '../stores/regimeStore';
import { exportCsv } from '../utils/exportCsv';
import { cn } from '../utils/cn';

type FilterKey = 'all' | 'gainers' | 'losers' | 'movers';

interface WatchlistRow {
  ticker: string;
  last: number | undefined;
  chgPct: number | undefined;
  chgAbs: number | undefined;
  premkt: number | undefined;
  volRatio: number | undefined;
  ma3: MATrend;
  ma5: MATrend;
  ma13: MATrend;
  nextEvent: string | undefined;
  sparkline: number[];
}

function derive(raw: Record<string, unknown>): Omit<WatchlistRow, 'ticker'> {
  const num = (k: string): number | undefined =>
    typeof raw[k] === 'number' ? (raw[k] as number) : undefined;
  const trend = (k: string): MATrend => {
    const v = raw[k];
    if (v === 'up' || v === 'flat' || v === 'down') return v;
    return 'flat';
  };
  return {
    last: num('last'),
    chgPct: num('chg_pct'),
    chgAbs: num('chg_abs'),
    premkt: num('premkt_pct'),
    volRatio: num('vol_ratio'),
    ma3: trend('ma3_trend'),
    ma5: trend('ma5_trend'),
    ma13: trend('ma13_trend'),
    nextEvent: typeof raw.next_event === 'string' ? (raw.next_event as string) : undefined,
    sparkline: Array.isArray(raw.sparkline) ? (raw.sparkline as number[]) : [],
  };
}

const DEFAULT_VISIBLE: Record<string, boolean> = {
  ticker: true,
  last: true,
  chgPct: true,
  chgAbs: true,
  premkt: true,
  volRatio: true,
  ma: true,
  nextEvent: true,
  sparkline: true,
};

const WatchlistPage: React.FC = () => {
  const navigate = useNavigate();
  const today = useRegimeStore((s) => s.today);
  const loadToday = useRegimeStore((s) => s.loadToday);
  const todayLoading = useRegimeStore((s) => s.todayLoading);

  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FilterKey>('all');
  const [visible, setVisible] = useState<Record<string, boolean>>(DEFAULT_VISIBLE);
  const [columnsOpen, setColumnsOpen] = useState(false);

  useEffect(() => {
    if (!today) void loadToday();
  }, [today, loadToday]);

  const rows = useMemo<WatchlistRow[]>(() => {
    const wl = (today?.snapshot?.watchlist ?? {}) as Record<string, unknown>;
    const out: WatchlistRow[] = [];
    for (const [ticker, raw] of Object.entries(wl)) {
      out.push({ ticker: ticker.toUpperCase(), ...derive((raw ?? {}) as Record<string, unknown>) });
    }
    return out;
  }, [today]);

  const filtered = useMemo(() => {
    let r = rows;
    const q = query.trim().toUpperCase();
    if (q) r = r.filter((x) => x.ticker.includes(q));
    if (filter === 'gainers') r = r.filter((x) => (x.chgPct ?? 0) > 0);
    if (filter === 'losers') r = r.filter((x) => (x.chgPct ?? 0) < 0);
    if (filter === 'movers') r = r.filter((x) => Math.abs(x.chgPct ?? 0) >= 1);
    return r;
  }, [rows, query, filter]);

  const allColumns: ColumnDef<WatchlistRow, unknown>[] = [
    { id: 'ticker', accessorKey: 'ticker', header: 'Ticker', size: 80 },
    {
      id: 'last',
      accessorKey: 'last',
      header: 'Last',
      size: 90,
      meta: { align: 'right' },
      cell: ({ getValue }) => <PriceCell value={getValue() as number | undefined} />,
    },
    {
      id: 'chgPct',
      accessorKey: 'chgPct',
      header: 'Chg%',
      size: 80,
      meta: { align: 'right' },
      cell: ({ getValue }) => <ChangeCell value={getValue() as number | undefined} mode="percent" />,
    },
    {
      id: 'chgAbs',
      accessorKey: 'chgAbs',
      header: 'Chg$',
      size: 80,
      meta: { align: 'right' },
      cell: ({ getValue }) => <ChangeCell value={getValue() as number | undefined} mode="absolute" />,
    },
    {
      id: 'premkt',
      accessorKey: 'premkt',
      header: 'Pmkt',
      size: 80,
      meta: { align: 'right' },
      cell: ({ getValue }) => <ChangeCell value={getValue() as number | undefined} mode="percent" />,
    },
    {
      id: 'volRatio',
      accessorKey: 'volRatio',
      header: 'Vol/Avg',
      size: 80,
      meta: { align: 'right' },
      cell: ({ getValue }) => <PriceCell value={getValue() as number | undefined} decimals={1} />,
    },
    {
      id: 'ma',
      header: 'MA 3/5/13',
      size: 100,
      cell: ({ row }) => (
        <MASlopeCell ma3={row.original.ma3} ma5={row.original.ma5} ma13={row.original.ma13} />
      ),
    },
    {
      id: 'nextEvent',
      accessorKey: 'nextEvent',
      header: 'Next',
      size: 120,
      cell: ({ getValue }) => (
        <span className="text-body-sm text-text-2">{(getValue() as string | undefined) ?? '—'}</span>
      ),
    },
    {
      id: 'sparkline',
      header: '30d',
      size: 100,
      cell: ({ row }) =>
        row.original.sparkline.length > 1 ? (
          <Sparkline data={row.original.sparkline} colorize />
        ) : (
          <span className="text-text-3 text-mono-xs">—</span>
        ),
    },
  ];

  const columns = allColumns.filter((c) => visible[c.id as string] ?? true);

  const handleExport = () => {
    exportCsv(
      `watchlist-${new Date().toISOString().slice(0, 10)}.csv`,
      [
        { key: 'ticker', label: 'Ticker', value: (r) => r.ticker },
        { key: 'last', label: 'Last', value: (r) => r.last ?? '' },
        { key: 'chg_pct', label: 'Chg%', value: (r) => r.chgPct ?? '' },
        { key: 'chg_abs', label: 'Chg$', value: (r) => r.chgAbs ?? '' },
        { key: 'premkt', label: 'Pmkt%', value: (r) => r.premkt ?? '' },
        { key: 'vol_ratio', label: 'Vol/Avg', value: (r) => r.volRatio ?? '' },
        { key: 'ma3', label: 'MA3', value: (r) => r.ma3 },
        { key: 'ma5', label: 'MA5', value: (r) => r.ma5 },
        { key: 'ma13', label: 'MA13', value: (r) => r.ma13 },
        { key: 'next_event', label: 'Next', value: (r) => r.nextEvent ?? '' },
      ],
      filtered,
    );
  };

  return (
    <div className="mx-auto max-w-7xl p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-label uppercase text-text-3">Watchlist</div>
          <h1 className="text-h1 text-text-1">Tickers</h1>
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={query}
            onChange={setQuery}
            placeholder="Filter…"
            iconLeft={Search}
            size="sm"
            className="w-48"
          />
          <div className="relative">
            <button
              type="button"
              onClick={() => setColumnsOpen((v) => !v)}
              className="inline-flex h-7 items-center gap-1.5 rounded-ds-sm border border-subtle bg-bg-1 px-3 text-body-sm text-text-2 hover:border-default hover:text-text-1"
            >
              <Columns3 size={14} strokeWidth={1.5} />
              Columns
            </button>
            {columnsOpen && (
              <>
                <div
                  className="fixed inset-0 z-dropdown"
                  onClick={() => setColumnsOpen(false)}
                  aria-hidden
                />
                <div className="absolute right-0 top-full z-dropdown mt-1 w-48 rounded-ds-md border border-default bg-bg-2 py-2 shadow-md">
                  {allColumns.map((c) => {
                    const id = c.id as string;
                    const on = visible[id] ?? true;
                    return (
                      <label
                        key={id}
                        className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-body-sm text-text-1 hover:bg-bg-3"
                      >
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={(e) => setVisible({ ...visible, [id]: e.target.checked })}
                        />
                        <span>{(c.header as string) ?? id}</span>
                      </label>
                    );
                  })}
                </div>
              </>
            )}
          </div>
          <button
            type="button"
            onClick={handleExport}
            className="inline-flex h-7 items-center gap-1.5 rounded-ds-sm border border-subtle bg-bg-1 px-3 text-body-sm text-text-2 hover:border-default hover:text-text-1"
          >
            <Download size={14} strokeWidth={1.5} />
            Export
          </button>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <SlidersHorizontal size={12} strokeWidth={1.5} className="text-text-3" />
        <Tabs
          variant="pills"
          value={filter}
          onChange={(v) => setFilter(v as FilterKey)}
          items={[
            { value: 'all', label: 'All', count: rows.length },
            { value: 'gainers', label: 'Gainers' },
            { value: 'losers', label: 'Losers' },
            { value: 'movers', label: 'Movers (\u2265 1%)' },
          ]}
        />
      </div>

      <section
        className={cn(
          'mt-4 rounded-ds-md border border-subtle bg-bg-1',
          todayLoading && !today && 'opacity-60',
        )}
      >
        {filtered.length === 0 ? (
          <EmptyState
            title={rows.length === 0 ? 'No watchlist data available.' : 'No rows match current filters.'}
            description={
              rows.length === 0
                ? 'Watchlist data comes from the latest regime snapshot. Compute regime on /regime first.'
                : undefined
            }
            size="md"
          />
        ) : (
          <DataTable
            data={filtered}
            columns={columns}
            density="compact"
            stickyHeader
            getRowId={(r) => r.ticker}
            onRowClick={(r) => navigate(`/stocks/${r.ticker}`)}
          />
        )}
      </section>
    </div>
  );
};

export default WatchlistPage;

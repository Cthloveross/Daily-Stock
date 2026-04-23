import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRegimeStore } from '../stores/regimeStore';
import type { RegimeScoreItem } from '../types/regime';
import { RegimeScore, type RegimeState } from '../components/regime/RegimeScore';
import { ContributionList } from '../components/regime/ContributionList';
import { DataSourceStatus, type DataSource } from '../components/system/DataSourceStatus';
import { RegimeHistoryChart } from '../components/regime/RegimeHistoryChart';
import BreakoutSignalsList from '../components/breakout/BreakoutSignalsList';
import { StatBar } from '../components/data/StatBar';
import { DataTable, EmptyState, type ColumnDef } from '../components/ui';
import { PriceCell } from '../components/data/PriceCell';
import { ChangeCell } from '../components/data/ChangeCell';
import { Tabs } from '../components/ui';

function toRegimeState(label?: string | null): RegimeState {
  const k = (label ?? '').toLowerCase();
  if (k.includes('aggressive')) return 'aggressive';
  if (k.includes('caution')) return 'cautious';
  if (k.includes('no_trade') || k.includes('no trade')) return 'no_trade';
  return 'standard';
}

function num(o: unknown, key: string): number | undefined {
  if (!o || typeof o !== 'object') return undefined;
  const v = (o as Record<string, unknown>)[key];
  return typeof v === 'number' ? v : undefined;
}

function deriveStatBar(item: RegimeScoreItem): React.ComponentProps<typeof StatBar>['items'] {
  const spy = (item.snapshot?.spy ?? {}) as Record<string, unknown>;
  const vix = (item.snapshot?.vix ?? {}) as Record<string, unknown>;
  const sectors = (item.snapshot?.sectors ?? {}) as Record<string, unknown>;
  const pre = (item.snapshot?.premarket ?? {}) as Record<string, unknown>;

  const spyClose = num(spy, 'close');
  const spyChgPct = num(spy, 'chg_pct');
  const spyMa20 = num(spy, 'ma20');
  const vixLevel = num(vix, 'level');
  const vixChgPct = num(vix, 'chg_pct');
  const breadthRaw = num(sectors, 'sectors_above_ma20');
  const preSpy = num(pre, 'spy_pre_pct');

  const fmt = (n?: number, d = 2) =>
    n === undefined ? '—' : n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtPct = (n?: number) =>
    n === undefined ? '—' : `${n > 0 ? '+' : n < 0 ? '\u2212' : ''}${Math.abs(n).toFixed(2)}%`;

  return [
    {
      label: 'SPY',
      value: fmt(spyClose),
      delta: fmtPct(spyChgPct),
      deltaPositive: spyChgPct === undefined ? undefined : spyChgPct >= 0,
      sub: spyMa20 !== undefined ? `MA20 ${fmt(spyMa20)}` : undefined,
    },
    {
      label: 'VIX',
      value: fmt(vixLevel),
      delta: fmtPct(vixChgPct),
      deltaPositive: vixChgPct === undefined ? undefined : vixChgPct <= 0,
    },
    {
      label: 'Breadth',
      // sectors_above_ma20 upstream: raw count of sector ETFs with price > MA20.
      value: breadthRaw !== undefined ? `${breadthRaw.toFixed(0)}` : '—',
      sub: 'sectors >MA20',
    },
    {
      label: 'Premkt',
      value: fmtPct(preSpy),
      deltaPositive: preSpy === undefined ? undefined : preSpy >= 0,
      sub: 'SPY fut',
    },
    {
      label: 'Updated',
      value: item.generatedAt
        ? new Date(item.generatedAt.endsWith('Z') ? item.generatedAt : `${item.generatedAt}Z`).toLocaleTimeString(
            [],
            { hour: '2-digit', minute: '2-digit' },
          )
        : '—',
      sub: `${item.version} \u00b7 ET`,
    },
  ];
}

function deriveDataSources(item: RegimeScoreItem): DataSource[] {
  const spy = (item.snapshot?.spy ?? {}) as Record<string, unknown>;
  const vix = (item.snapshot?.vix ?? {}) as Record<string, unknown>;
  const sectors = (item.snapshot?.sectors ?? {}) as Record<string, unknown>;
  const prev = (item.snapshot?.prev_day ?? {}) as Record<string, unknown>;
  const pre = (item.snapshot?.premarket ?? {}) as Record<string, unknown>;
  const ev = (item.snapshot?.events ?? {}) as Record<string, unknown>;

  const hasNum = (o: Record<string, unknown>, k: string) => typeof o[k] === 'number';
  const yfChecks = [
    hasNum(spy, 'close'),
    hasNum(vix, 'level'),
    hasNum(sectors, 'sectors_above_ma20'),
    hasNum(prev, 'close_vs_high_pct'),
  ];
  const yfOk = yfChecks.filter(Boolean).length;
  const yfStatus: DataSource['status'] =
    yfOk === yfChecks.length ? 'ok' : yfOk > 0 ? 'partial' : 'missing';
  const alpacaStatus: DataSource['status'] = hasNum(pre, 'spy_pre_pct')
    ? pre.spy_pre_pct !== 0
      ? 'ok'
      : 'partial'
    : 'missing';
  const eventsHasKeys = Object.keys(ev).length > 0;
  const anyEventTrue =
    Boolean(ev.fomc_today) || Boolean(ev.cpi_today) || Boolean(ev.nfp_today) || Boolean(ev.tariff_headline_today);
  const hasEarningsCount = typeof ev.earnings_count_watchlist === 'number';
  const finnhubStatus: DataSource['status'] =
    anyEventTrue || hasEarningsCount ? 'ok' : eventsHasKeys ? 'partial' : 'missing';

  return [
    { name: 'yfinance', status: yfStatus, detail: `${yfOk}/${yfChecks.length} blocks` },
    { name: 'Alpaca', status: alpacaStatus },
    { name: 'Finnhub', status: finnhubStatus },
  ];
}

interface WatchlistRow {
  ticker: string;
  last: number | undefined;
  chgPct: number | undefined;
  premkt: number | undefined;
  volRatio: number | undefined;
  nextEvent: string | undefined;
}

function deriveWatchlist(item: RegimeScoreItem): WatchlistRow[] {
  const watchlist = (item.snapshot?.watchlist ?? {}) as Record<string, unknown>;
  const entries = Object.entries(watchlist);
  return entries.map(([ticker, raw]) => {
    const r = (raw ?? {}) as Record<string, unknown>;
    return {
      ticker: ticker.toUpperCase(),
      last: typeof r.last === 'number' ? r.last : undefined,
      chgPct: typeof r.chg_pct === 'number' ? r.chg_pct : undefined,
      premkt: typeof r.premkt_pct === 'number' ? r.premkt_pct : undefined,
      volRatio: typeof r.vol_ratio === 'number' ? r.vol_ratio : undefined,
      nextEvent: typeof r.next_event === 'string' ? r.next_event : undefined,
    };
  });
}

const watchlistColumns: ColumnDef<WatchlistRow, unknown>[] = [
  { accessorKey: 'ticker', header: 'Ticker', size: 80 },
  {
    accessorKey: 'last',
    header: 'Last',
    size: 90,
    meta: { align: 'right' },
    cell: ({ getValue }) => <PriceCell value={getValue() as number | undefined} />,
  },
  {
    accessorKey: 'chgPct',
    header: 'Chg%',
    size: 80,
    meta: { align: 'right' },
    cell: ({ getValue }) => <ChangeCell value={getValue() as number | undefined} mode="percent" />,
  },
  {
    accessorKey: 'premkt',
    header: 'Pmkt',
    size: 80,
    meta: { align: 'right' },
    cell: ({ getValue }) => <ChangeCell value={getValue() as number | undefined} mode="percent" />,
  },
  {
    accessorKey: 'volRatio',
    header: 'Vol/Avg',
    size: 80,
    meta: { align: 'right' },
    cell: ({ getValue }) => <PriceCell value={getValue() as number | undefined} decimals={1} />,
  },
  {
    accessorKey: 'nextEvent',
    header: 'Next',
    size: 120,
    cell: ({ getValue }) => (
      <span className="text-body-sm text-text-2">{(getValue() as string | undefined) ?? '—'}</span>
    ),
  },
];

const RegimePage: React.FC = () => {
  const navigate = useNavigate();
  const { today, todayLoading, loadToday, recompute } = useRegimeStore();
  const [historyDays, setHistoryDays] = useState<'30d' | '60d' | '90d'>('30d');
  const [recomputing, setRecomputing] = useState(false);

  useEffect(() => {
    void loadToday();
  }, [loadToday]);

  const handleRecompute = async () => {
    setRecomputing(true);
    try {
      await recompute();
    } finally {
      setRecomputing(false);
    }
  };

  const contributionItems = useMemo(() => {
    if (!today) return [];
    return [
      { label: 'Direction', value: today.d1Direction, description: 'MA slope + 50D trend' },
      { label: 'Volatility', value: today.d2Volatility, description: 'VIX level + term' },
      { label: 'Macro', value: today.d3MacroPenalty, description: 'FOMC / CPI / NFP' },
      { label: 'Sector', value: today.d4Sector, description: 'Breadth of watchlist' },
      { label: 'Prev Day', value: today.d5PrevDay, description: 'Close vs intraday high' },
      { label: 'Premarket', value: today.d6Premarket, description: 'SPY/QQQ premkt' },
    ];
  }, [today]);

  const watchlist = useMemo(() => (today ? deriveWatchlist(today) : []), [today]);
  const statItems = useMemo(() => (today ? deriveStatBar(today) : []), [today]);
  const sources = useMemo(() => (today ? deriveDataSources(today) : []), [today]);
  const days = historyDays === '30d' ? 30 : historyDays === '60d' ? 60 : 90;

  if (todayLoading && !today) {
    return (
      <div className="mx-auto max-w-7xl space-y-4 p-4">
        <div className="h-12 animate-pulse rounded-ds-md bg-bg-1" />
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          <div className="h-36 animate-pulse rounded-ds-md bg-bg-1" />
          <div className="h-36 animate-pulse rounded-ds-md bg-bg-1" />
        </div>
      </div>
    );
  }

  if (!today) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <EmptyState
          title="Regime not computed yet"
          description="No score for today. Click below to run analysis (~30s) or run `python -m src.regime.cli` in the terminal."
          action={{ label: recomputing ? 'Computing…' : 'Compute regime', onClick: () => void handleRecompute() }}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4">
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <RegimeScore
          score={today.score}
          state={toRegimeState(today.label)}
          note={today.actionHint ?? undefined}
          updatedAt={today.generatedAt ?? undefined}
          version={today.version}
          onRecompute={() => void handleRecompute()}
          recomputing={recomputing}
        />
        <div className="rounded-ds-md border border-subtle bg-bg-1 p-4">
          <div className="mb-2 text-label uppercase text-text-3">Contributions</div>
          <ContributionList items={contributionItems} maxAbsValue={25} />
        </div>
      </div>

      <StatBar items={statItems} />

      <DataSourceStatus sources={sources} variant="bar" />

      <section className="rounded-ds-md border border-subtle bg-bg-1">
        <div className="flex items-center justify-between border-b border-subtle px-4 py-3">
          <div className="text-label uppercase text-text-3">Watchlist premarket</div>
          <div className="font-mono text-mono-sm text-text-3">
            {watchlist.length} of {watchlist.length}
          </div>
        </div>
        {watchlist.length === 0 ? (
          <EmptyState title="No watchlist data in scope." size="sm" />
        ) : (
          <DataTable
            data={watchlist}
            columns={watchlistColumns}
            density="regular"
            stickyHeader
            getRowId={(r) => r.ticker}
            onRowClick={(r) => navigate(`/stocks/${r.ticker}`)}
          />
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-label uppercase text-text-3">Regime history</div>
          <Tabs
            variant="pills"
            value={historyDays}
            onChange={(v) => setHistoryDays(v as '30d' | '60d' | '90d')}
            items={[
              { value: '30d', label: '30d' },
              { value: '60d', label: '60d' },
              { value: '90d', label: '90d' },
            ]}
          />
        </div>
        <RegimeHistoryChart days={days} />
      </section>

      <section>
        <BreakoutSignalsList limit={15} />
      </section>
    </div>
  );
};

export default RegimePage;

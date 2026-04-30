import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRegimeStore } from '../stores/regimeStore';
import { useUserWatchlistStore } from '../stores/userWatchlistStore';
import { useTickerQuotes } from '../hooks/useTickerQuotes';
import type { StockQuote } from '../api/stocks';
import type { RegimeScoreItem } from '../types/regime';
import { type RegimeState } from '../components/regime/RegimeScore';
import { RegimeGauge } from '../components/regime/RegimeGauge';
import { ContributionList } from '../components/regime/ContributionList';
import { ContributionInfo } from '../components/regime/ContributionInfo';
import { DataSourceStatus, type DataSource } from '../components/system/DataSourceStatus';
import { RegimeHistoryChart } from '../components/regime/RegimeHistoryChart';
import BreakoutSignalsList from '../components/breakout/BreakoutSignalsList';
import { TradingViewWidget } from '../components/charts/TradingViewWidget';
import { Search, Trash2, X } from 'lucide-react';
import { Input, toast } from '../components/ui';
import { TickerPicker } from '../components/data/TickerPicker';
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
  source: 'user' | 'regime';
}

function deriveWatchlist(
  item: RegimeScoreItem | null,
  userTickers: string[],
  quotes: Record<string, StockQuote | null>,
): WatchlistRow[] {
  const snapshot = (item?.snapshot?.watchlist ?? {}) as Record<string, unknown>;
  const snapByKey = new Map<string, Record<string, unknown>>();
  for (const [k, v] of Object.entries(snapshot)) {
    snapByKey.set(k.toUpperCase(), (v ?? {}) as Record<string, unknown>);
  }

  const toRow = (ticker: string, r: Record<string, unknown>, source: 'user' | 'regime'): WatchlistRow => {
    const q = quotes[ticker] ?? null;
    const numFromSnap = (k: string): number | undefined =>
      typeof r[k] === 'number' ? (r[k] as number) : undefined;
    return {
      ticker,
      // Prefer live quote when available; fall back to snapshot fields.
      last: numFromSnap('last') ?? q?.currentPrice,
      chgPct: numFromSnap('chg_pct') ?? q?.changePercent ?? undefined,
      premkt: numFromSnap('premkt_pct'),
      volRatio: numFromSnap('vol_ratio'),
      nextEvent: typeof r.next_event === 'string' ? r.next_event : undefined,
      source,
    };
  };

  const out: WatchlistRow[] = [];
  const userSet = new Set(userTickers.map((t) => t.toUpperCase()));

  // 1) user tickers first (keeping the order they were added)
  for (const t of userTickers) {
    const upper = t.toUpperCase();
    out.push(toRow(upper, snapByKey.get(upper) ?? {}, 'user'));
  }
  // 2) regime snapshot additions not already in the user list
  for (const [t, raw] of snapByKey.entries()) {
    if (userSet.has(t)) continue;
    out.push(toRow(t, raw, 'regime'));
  }
  return out;
}

const watchlistColumns: ColumnDef<WatchlistRow, unknown>[] = [
  {
    id: 'ticker',
    accessorKey: 'ticker',
    header: 'Ticker',
    size: 110,
    cell: ({ row }) => (
      <span className="inline-flex items-center gap-2">
        <span
          aria-hidden
          title={row.original.source === 'user' ? '来自本地自选' : '来自 regime snapshot'}
          className={
            row.original.source === 'user'
              ? 'inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-accent'
              : 'inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[color:var(--text-3)]'
          }
        />
        <span className="font-mono text-mono-sm text-text-1">{row.original.ticker}</span>
      </span>
    ),
  },
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
  const userTickers = useUserWatchlistStore((s) => s.tickers);
  const addUserTicker = useUserWatchlistStore((s) => s.add);
  const removeUserTicker = useUserWatchlistStore((s) => s.remove);
  const [historyDays, setHistoryDays] = useState<'30d' | '60d' | '90d'>('30d');
  const [recomputing, setRecomputing] = useState(false);
  // Inline chart: clicking a watchlist row shows a TradingView widget below
  // the table instead of navigating away from /regime.
  const [inlineTicker, setInlineTicker] = useState<string | null>(null);
  // Watchlist filter + add state. No raw-typed add anymore — TickerPicker
  // validates against the local stock index and only fires onAdd with a
  // canonical code, so typos can't reach the store.
  const [wlQuery, setWlQuery] = useState('');
  const [showHidden, setShowHidden] = useState(false);

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
    const snapshot = (today.snapshot ?? {}) as Record<string, unknown>;
    const events = snapshot.events;
    const prevDay = snapshot.prev_day;
    const sectors = snapshot.sectors;
    const premarket = snapshot.premarket;
    const isEmptyObj = (v: unknown) =>
      v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0);
    const macroHasData = Array.isArray(events) && events.length > 0;
    const prevDayHasData = !isEmptyObj(prevDay);
    const sectorHasData = !isEmptyObj(sectors);
    const premarketHasData = !isEmptyObj(premarket);
    return [
      { label: 'Direction', value: today.d1Direction, description: 'MA slope + 50D trend' },
      { label: 'Volatility', value: today.d2Volatility, description: 'VIX level + term' },
      {
        label: 'Macro',
        value: today.d3MacroPenalty,
        description: 'FOMC / CPI / NFP',
        status: macroHasData ? ('computed' as const) : ('no_data' as const),
        noDataHint: '未配置 Finnhub 或今日无事件',
      },
      {
        label: 'Sector',
        value: today.d4Sector,
        description: 'Breadth of watchlist',
        status: sectorHasData ? ('computed' as const) : ('no_data' as const),
        noDataHint: '未配置板块数据源',
      },
      {
        label: 'Prev Day',
        value: today.d5PrevDay,
        description: 'Close vs intraday high',
        status: prevDayHasData ? ('computed' as const) : ('no_data' as const),
        noDataHint: 'yfinance 未返回昨日 OHLC',
      },
      {
        label: 'Premarket',
        value: today.d6Premarket,
        description: 'SPY/QQQ premkt',
        status: premarketHasData ? ('computed' as const) : ('no_data' as const),
        noDataHint: '未配置 Alpaca 或当前非盘前时段',
      },
    ];
  }, [today]);

  // Fetch live quotes only for user-added tickers — regime snapshot already has
  // its own fields for the server-configured pool.
  const { quotes } = useTickerQuotes(userTickers);
  const allWatchlist = useMemo(
    () => deriveWatchlist(today ?? null, userTickers, quotes),
    [today, userTickers, quotes],
  );

  // A row is "empty" when it has no Last price, no regime snapshot fields — i.e.
  // the ticker returned no data anywhere. We split these out so typos like AMAZ
  // don't clutter the main list; they're still reachable via the "hidden" toggle.
  const { watchlist, hiddenInvalid } = useMemo(() => {
    const visible: typeof allWatchlist = [];
    const hidden: typeof allWatchlist = [];
    for (const r of allWatchlist) {
      const anyData =
        r.last != null ||
        r.chgPct != null ||
        r.premkt != null ||
        r.volRatio != null ||
        r.nextEvent != null;
      if (anyData || r.source === 'regime') {
        visible.push(r);
      } else {
        hidden.push(r);
      }
    }
    // Apply text filter on visible rows only.
    const q = wlQuery.trim().toUpperCase();
    const filtered = q ? visible.filter((r) => r.ticker.includes(q)) : visible;
    return { watchlist: filtered, hiddenInvalid: hidden };
  }, [allWatchlist, wlQuery]);

  const handlePickerAdd = (canonicalCode: string, name?: string) => {
    const ok = addUserTicker(canonicalCode);
    const label = name ? `${canonicalCode} · ${name}` : canonicalCode;
    toast[ok ? 'success' : 'info'](ok ? `已加入自选: ${label}` : `${canonicalCode} 已在自选里`);
  };
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
        <RegimeGauge
          score={today.score}
          state={toRegimeState(today.label)}
          note={today.actionHint ?? undefined}
          updatedAt={today.generatedAt ?? undefined}
          version={today.version}
          onRecompute={() => void handleRecompute()}
          recomputing={recomputing}
        />
        <div className="rounded-ds-md border border-subtle bg-bg-1 p-4">
          <div className="mb-2 flex items-center gap-2">
            <div className="text-label uppercase text-text-3">Contributions</div>
            <ContributionInfo />
          </div>
          <ContributionList items={contributionItems} maxAbsValue={25} />
        </div>
      </div>

      <StatBar items={statItems} />

      <DataSourceStatus sources={sources} variant="bar" />

      <section className="rounded-ds-md border border-subtle bg-bg-1">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-subtle px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="text-label uppercase text-text-3">Watchlist</div>
            <div className="font-mono text-mono-xs text-text-3">
              {watchlist.length}
              {wlQuery && allWatchlist.length !== watchlist.length && ` of ${allWatchlist.length}`}
              {hiddenInvalid.length > 0 && ` · ${hiddenInvalid.length} hidden`}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <TickerPicker onAdd={handlePickerAdd} className="w-[340px]" />
            <div className="mx-1 h-5 w-px bg-[color:var(--border-subtle)]" aria-hidden />
            <Input
              value={wlQuery}
              onChange={setWlQuery}
              placeholder="Filter…"
              iconLeft={Search}
              size="sm"
              className="w-36"
            />
          </div>
        </div>
        {allWatchlist.length === 0 ? (
          <EmptyState
            title="自选列表为空"
            description="上方输入 ticker 加入自选（保存在本地，刷新不丢）；accent 点 = 本地自选，灰点 = regime snapshot。"
            size="sm"
            action={{ label: 'Go to watchlist page', onClick: () => navigate('/watchlist') }}
          />
        ) : watchlist.length === 0 ? (
          <EmptyState
            title="当前过滤条件下没有匹配的 ticker"
            size="sm"
          />
        ) : (
          <DataTable
            data={watchlist}
            columns={watchlistColumns}
            density="regular"
            stickyHeader
            getRowId={(r) => r.ticker}
            onRowClick={(r) => setInlineTicker(r.ticker)}
          />
        )}

        {/* Hidden-tickers surface: so typos like AMAZ don't vanish silently. */}
        {hiddenInvalid.length > 0 && (
          <div className="border-t border-subtle px-4 py-2">
            <button
              type="button"
              onClick={() => setShowHidden((v) => !v)}
              className="text-caption text-text-3 hover:text-text-1"
            >
              {showHidden ? '收起' : '展开'} {hiddenInvalid.length} 个暂无数据的 ticker（多半是拼写错误）
            </button>
            {showHidden && (
              <ul className="mt-2 flex flex-wrap gap-2">
                {hiddenInvalid.map((r) => (
                  <li
                    key={r.ticker}
                    className="inline-flex items-center gap-1 rounded-full border border-subtle bg-bg-2 px-2 py-0.5 text-caption text-text-2"
                  >
                    <span className="font-mono">{r.ticker}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${r.ticker}`}
                      title="从自选移除"
                      onClick={() => {
                        removeUserTicker(r.ticker);
                        toast.info(`已移除: ${r.ticker}`);
                      }}
                      className="text-text-3 hover:text-down-strong"
                    >
                      <Trash2 size={12} strokeWidth={1.5} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {/* Inline chart: shows below the watchlist table when the user clicks a row. */}
      {inlineTicker && (
        <section className="rounded-ds-md border border-subtle bg-bg-1">
          <div className="flex items-center justify-between border-b border-subtle px-4 py-3">
            <div className="flex items-baseline gap-3">
              <div className="text-label uppercase text-text-3">Chart</div>
              <span className="font-mono text-mono-md text-text-1">{inlineTicker}</span>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => navigate(`/stocks/${inlineTicker}`)}
                className="rounded-ds-sm px-2 py-1 text-body-sm text-text-2 hover:bg-bg-2 hover:text-text-1"
                title="打开完整详情页（K 线 + MA + 新闻 + 总结）"
              >
                Open detail →
              </button>
              <button
                type="button"
                aria-label="Close inline chart"
                onClick={() => setInlineTicker(null)}
                className="inline-flex h-7 w-7 items-center justify-center rounded-ds-sm text-text-3 hover:bg-bg-2 hover:text-text-1"
              >
                <X size={14} strokeWidth={1.5} />
              </button>
            </div>
          </div>
          <div className="p-2">
            <TradingViewWidget symbol={inlineTicker} interval="15" theme="dark" height={460} />
          </div>
        </section>
      )}

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

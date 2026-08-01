import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRegimeStore } from '../stores/regimeStore';
import { useUserWatchlistStore } from '../stores/userWatchlistStore';
import { useTickerQuotes } from '../hooks/useTickerQuotes';
import type { StockQuote } from '../api/stocks';
import type { RegimeQualityState, RegimeScoreItem } from '../types/regime';
import { type RegimeState } from '../components/regime/RegimeScore';
import { RegimeGauge } from '../components/regime/RegimeGauge';
import { ContributionList } from '../components/regime/ContributionList';
import { ContributionInfo } from '../components/regime/ContributionInfo';
import { DataSourceStatus, type DataSource } from '../components/system/DataSourceStatus';
import { RegimeHistoryChart } from '../components/regime/RegimeHistoryChart';
import BreakoutSignalsList from '../components/breakout/BreakoutSignalsList';
import { DailyOpportunityList } from '../components/opportunities/DailyOpportunityList';
import { TradingViewWidget } from '../components/charts/TradingViewWidget';
import { Search, Trash2, X } from 'lucide-react';
import { Input, toast } from '../components/ui';
import { TickerPicker } from '../components/data/TickerPicker';
import { StatBar } from '../components/data/StatBar';
import { DataTable, EmptyState, type ColumnDef } from '../components/ui';
import { PriceCell } from '../components/data/PriceCell';
import { ChangeCell } from '../components/data/ChangeCell';
import { Tabs } from '../components/ui';
import { formatEtClock } from '../utils/marketTime';

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

function numAny(o: unknown, ...keys: string[]): number | undefined {
  for (const key of keys) {
    const value = num(o, key);
    if (value !== undefined) return value;
  }
  return undefined;
}

function block(
  snapshot: Record<string, unknown>,
  snakeKey: string,
  camelKey: string,
): Record<string, unknown> {
  return ((snapshot[snakeKey] ?? snapshot[camelKey] ?? {}) as Record<string, unknown>);
}

const DOMAIN_LABELS: Record<string, string> = {
  spy: 'SPY 趋势',
  vix: 'VIX',
  events: '宏观事件',
  sectors: '板块广度',
  prev_day: '昨日结构',
  premarket: '盘前行情',
};

function regimeQuality(item: RegimeScoreItem): {
  state: RegimeQualityState;
  incomplete: string[];
  domains: Record<string, RegimeQualityState>;
} {
  const snapshotQuality = (item.snapshot?.quality ?? {}) as Record<string, unknown>;
  const stateRaw = item.qualityState ?? snapshotQuality.state;
  const state: RegimeQualityState =
    stateRaw === 'ready' || stateRaw === 'degraded' || stateRaw === 'unavailable'
      ? stateRaw
      : 'unavailable';
  const domainRaw =
    item.domainQuality ??
    ((snapshotQuality.domainStates ?? snapshotQuality.domain_states ?? {}) as Record<string, RegimeQualityState>);
  const domains: Record<string, RegimeQualityState> = {
    ...domainRaw,
    prev_day: domainRaw.prev_day ?? domainRaw.prevDay,
  };
  const incompleteRaw =
    item.incompleteDomains ??
    (Array.isArray(snapshotQuality.incompleteDomains ?? snapshotQuality.incomplete_domains)
      ? ((snapshotQuality.incompleteDomains ?? snapshotQuality.incomplete_domains) as string[])
      : []);
  return {
    state,
    incomplete: incompleteRaw.map((domain) => domain === 'prevDay' ? 'prev_day' : domain),
    domains,
  };
}

function deriveStatBar(item: RegimeScoreItem): React.ComponentProps<typeof StatBar>['items'] {
  const snapshot = item.snapshot ?? {};
  const spy = block(snapshot, 'spy', 'spy');
  const vix = block(snapshot, 'vix', 'vix');
  const sectors = block(snapshot, 'sectors', 'sectors');
  const pre = block(snapshot, 'premarket', 'premarket');

  const spyClose = numAny(spy, 'close');
  const spyChgPct = numAny(spy, 'chg_pct', 'chgPct');
  const spyMa20 = numAny(spy, 'ma20');
  const vixLevel = numAny(vix, 'level');
  const vixChgPct = numAny(vix, 'chg_pct', 'chgPct');
  const breadthRaw = numAny(sectors, 'sectors_above_ma20', 'sectorsAboveMa20');
  const preSpy = numAny(pre, 'spy_pre_pct', 'spyPrePct');

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
      value: formatEtClock(item.generatedAt) || '—',
      sub: `${item.version} \u00b7 ET`,
    },
  ];
}

function deriveDataSources(item: RegimeScoreItem): DataSource[] {
  const snapshot = item.snapshot ?? {};
  const spy = block(snapshot, 'spy', 'spy');
  const vix = block(snapshot, 'vix', 'vix');
  const sectors = block(snapshot, 'sectors', 'sectors');
  const prev = block(snapshot, 'prev_day', 'prevDay');
  const pre = block(snapshot, 'premarket', 'premarket');
  const ev = block(snapshot, 'events', 'events');

  const quality = regimeQuality(item);
  const hasQuality = Object.keys(quality.domains).length > 0;
  const hasNum = (o: Record<string, unknown>, ...keys: string[]) =>
    keys.some((key) => typeof o[key] === 'number');
  const domainOk = (domain: string) => quality.domains[domain] === 'ready';
  const domainSeen = (domain: string) =>
    quality.domains[domain] === 'ready' || quality.domains[domain] === 'degraded';
  const yfChecks = [
    hasQuality ? domainOk('spy') : hasNum(spy, 'close'),
    hasQuality ? domainOk('vix') : hasNum(vix, 'level'),
    hasQuality ? domainOk('sectors') : hasNum(sectors, 'sectors_above_ma20', 'sectorsAboveMa20'),
    hasQuality ? domainOk('prev_day') : hasNum(prev, 'close_vs_high_pct', 'closeVsHighPct'),
  ];
  const yfOk = yfChecks.filter(Boolean).length;
  const yfStatus: DataSource['status'] =
    yfOk === yfChecks.length ? 'ok' : yfOk > 0 ? 'partial' : 'missing';
  const alpacaStatus: DataSource['status'] = hasQuality
    ? domainOk('premarket')
      ? 'ok'
      : domainSeen('premarket')
        ? 'partial'
        : 'missing'
    : hasNum(pre, 'spy_pre_pct', 'spyPrePct')
      ? 'partial'
      : 'missing';
  const eventsHasKeys = Object.keys(ev).length > 0;
  const anyEventTrue =
    Boolean(ev.fomc_today ?? ev.fomcToday) ||
    Boolean(ev.cpi_today ?? ev.cpiToday) ||
    Boolean(ev.nfp_today ?? ev.nfpToday) ||
    Boolean(ev.tariff_headline_today ?? ev.tariffHeadlineToday);
  const hasEarningsCount =
    typeof (ev.earnings_count_watchlist ?? ev.earningsCountWatchlist) === 'number';
  const finnhubStatus: DataSource['status'] = hasQuality
    ? domainOk('events')
      ? 'ok'
      : domainSeen('events')
        ? 'partial'
        : 'missing'
    : anyEventTrue || hasEarningsCount
      ? 'ok'
      : eventsHasKeys
        ? 'partial'
        : 'missing';
  const marketSources = new Set<string>();
  for (const payload of [spy, vix, prev]) {
    const source = payload._source ?? payload.source;
    if (typeof source === 'string' && source) marketSources.add(source);
  }
  const sectorSources = (sectors._sources ?? sectors.sources) as
    | Record<string, unknown>
    | undefined;
  if (sectorSources) {
    for (const source of Object.values(sectorSources)) {
      if (typeof source === 'string' && source) marketSources.add(source);
    }
  }
  const marketSourceLabel =
    marketSources.size > 0 ? [...marketSources].join(' / ') : '市场日线';

  return [
    { name: marketSourceLabel, status: yfStatus, detail: `${yfOk}/${yfChecks.length} blocks` },
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
    const quality = regimeQuality(today);
    const snapshot = (today.snapshot ?? {}) as Record<string, unknown>;
    const events = block(snapshot, 'events', 'events');
    const prevDay = block(snapshot, 'prev_day', 'prevDay');
    const sectors = block(snapshot, 'sectors', 'sectors');
    const premarket = block(snapshot, 'premarket', 'premarket');
    const isEmptyObj = (v: unknown) =>
      v == null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0);
    const domainReady = (domain: string, fallback: boolean) =>
      Object.keys(quality.domains).length > 0
        ? quality.domains[domain] === 'ready'
        : fallback;
    const spyHasData = domainReady('spy', !isEmptyObj(block(snapshot, 'spy', 'spy')));
    const vixHasData = domainReady('vix', !isEmptyObj(block(snapshot, 'vix', 'vix')));
    const macroHasData = domainReady('events', !isEmptyObj(events));
    const prevDayHasData = domainReady('prev_day', !isEmptyObj(prevDay));
    const sectorHasData = domainReady('sectors', !isEmptyObj(sectors));
    const premarketHasData = domainReady('premarket', !isEmptyObj(premarket));
    return [
      {
        label: 'Direction',
        value: today.d1Direction,
        description: 'MA slope + 50D trend',
        status: spyHasData ? ('computed' as const) : ('no_data' as const),
        noDataHint: 'SPY 趋势输入不完整',
      },
      {
        label: 'Volatility',
        value: today.d2Volatility,
        description: 'VIX level + term',
        status: vixHasData ? ('computed' as const) : ('no_data' as const),
        noDataHint: 'VIX 输入不完整',
      },
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
  const quality = useMemo(() => (today ? regimeQuality(today) : null), [today]);
  const days = historyDays === '30d' ? 30 : historyDays === '60d' ? 60 : 90;

  if (todayLoading && !today) {
    return (
      <div className="mx-auto max-w-7xl space-y-4 p-4">
        <DailyOpportunityList symbols={userTickers} />
        <div aria-label="Regime 数据读取中" className="space-y-4">
          <div className="h-12 animate-pulse rounded-ds-md bg-bg-1" />
          <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
            <div className="h-36 animate-pulse rounded-ds-md bg-bg-1" />
            <div className="h-36 animate-pulse rounded-ds-md bg-bg-1" />
          </div>
        </div>
      </div>
    );
  }

  if (!today) {
    return (
      <div className="mx-auto max-w-7xl space-y-4 p-4">
        <DailyOpportunityList symbols={userTickers} />
        <div className="mx-auto max-w-3xl py-4">
          <EmptyState
            title="Regime not computed yet"
            description="今日机会仍可用，但当前没有 Regime 证据。点击下方生成市场状态，或在终端运行 `python -m src.regime.cli`。"
            action={{ label: recomputing ? 'Computing…' : 'Compute regime', onClick: () => void handleRecompute() }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4">
      <DailyOpportunityList symbols={userTickers} />

      {quality && quality.state !== 'ready' && (
        <div
          role="status"
          className="rounded-ds-md border border-warn-strong/30 bg-warn-strong/5 px-4 py-3 text-body-sm text-text-2"
        >
          <span className="font-medium text-text-1">
            {quality.state === 'unavailable' ? 'Regime 暂不可用' : 'Regime 数据降级'}
          </span>
          {' · '}
          {quality.incomplete.length > 0
            ? quality.incomplete.map((domain) => DOMAIN_LABELS[domain] ?? domain).join('、')
            : '输入质量无法确认'}
          。{quality.state === 'unavailable' ? '页面不会把存储的 0 展示成真实 no_trade。' : '当前分档仅作背景，不应单独驱动交易。'}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <RegimeGauge
          score={today.score}
          state={toRegimeState(today.label)}
          note={today.actionHint ?? undefined}
          updatedAt={today.generatedAt ?? undefined}
          version={today.version}
          onRecompute={() => void handleRecompute()}
          recomputing={recomputing}
          qualityState={quality?.state}
          incompleteDomains={quality?.incomplete.map((domain) => DOMAIN_LABELS[domain] ?? domain)}
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
                aria-label={`打开 ${inlineTicker} 完整详情页，包含 K 线、均线、新闻和总结`}
                onClick={() => navigate(`/stocks/${inlineTicker}`)}
                className="rounded-ds-sm px-2 py-1 text-body-sm text-text-2 hover:bg-bg-2 hover:text-text-1"
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

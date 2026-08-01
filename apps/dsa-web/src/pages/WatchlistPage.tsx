import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Columns3,
  Download,
  RefreshCw,
  Save,
  Search,
  SlidersHorizontal,
  Upload,
  X,
} from 'lucide-react';
import { DataTable, EmptyState, Input, Tabs, toast, type ColumnDef } from '../components/ui';
import { fetchPremarketUniverse, savePremarketUniverse } from '../api/opportunities';
import type { PremarketUniverseResponse } from '../types/opportunities';
import { PriceCell } from '../components/data/PriceCell';
import { ChangeCell } from '../components/data/ChangeCell';
import { Sparkline } from '../components/data/Sparkline';
import { MASlopeCell, type MATrend } from '../components/data/MASlopeCell';
import { TickerPicker } from '../components/data/TickerPicker';
import { useRegimeStore } from '../stores/regimeStore';
import { useUserWatchlistStore } from '../stores/userWatchlistStore';
import { useTickerQuotes } from '../hooks/useTickerQuotes';
import { exportCsv } from '../utils/exportCsv';
import { cn } from '../utils/cn';
import { parseTradingViewWatchlist } from '../utils/tradingViewWatchlist';
import {
  hasSameOrderedPremarketUniverse,
  preparePremarketUniverse,
} from '../utils/premarketUniverse';

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
  /** 是否来自本地自选（而非 regime snapshot） */
  isUserAdded: boolean;
}

function derive(raw: Record<string, unknown>): Omit<WatchlistRow, 'ticker' | 'isUserAdded'> {
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
  actions: true,
};

const EMPTY_ROW: Omit<WatchlistRow, 'ticker' | 'isUserAdded'> = {
  last: undefined,
  chgPct: undefined,
  chgAbs: undefined,
  premkt: undefined,
  volRatio: undefined,
  ma3: 'flat',
  ma5: 'flat',
  ma13: 'flat',
  nextEvent: undefined,
  sparkline: [],
};

const PREMARKET_UNIVERSE_SOURCE_LABELS: Record<PremarketUniverseResponse['source'], string> = {
  persisted: '显式保存',
  stock_list_fallback: 'STOCK_LIST 建议（未启用）',
  request_fallback: '请求建议（未启用）',
  unavailable: '尚未配置',
};

function formatUniverseUpdatedAt(raw: string | null): string {
  if (!raw) return '尚未保存';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).format(parsed);
}

const WatchlistPage: React.FC = () => {
  const navigate = useNavigate();
  const today = useRegimeStore((s) => s.today);
  const loadToday = useRegimeStore((s) => s.loadToday);
  const todayLoading = useRegimeStore((s) => s.todayLoading);
  const userTickers = useUserWatchlistStore((s) => s.tickers);
  const addTicker = useUserWatchlistStore((s) => s.add);
  const addManyTickers = useUserWatchlistStore((s) => s.addMany);
  const removeTicker = useUserWatchlistStore((s) => s.remove);

  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FilterKey>('all');
  const [visible, setVisible] = useState<Record<string, boolean>>(DEFAULT_VISIBLE);
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [officialUniverse, setOfficialUniverse] = useState<PremarketUniverseResponse | null>(null);
  const [officialUniverseLoading, setOfficialUniverseLoading] = useState(true);
  const [officialUniverseSaving, setOfficialUniverseSaving] = useState(false);
  const [officialUniverseError, setOfficialUniverseError] = useState<string | null>(null);

  const preparedPremarketUniverse = useMemo(
    () => preparePremarketUniverse(userTickers),
    [userTickers],
  );
  const officialUniverseSymbolsInSync = Boolean(
    officialUniverse?.configured
    && hasSameOrderedPremarketUniverse(
      preparedPremarketUniverse.symbols,
      officialUniverse.symbols,
    ),
  );
  const officialUniverseInSync = Boolean(
    officialUniverseSymbolsInSync && officialUniverse?.limit === 5,
  );

  const loadOfficialUniverse = useCallback(async () => {
    setOfficialUniverseLoading(true);
    setOfficialUniverseError(null);
    try {
      setOfficialUniverse(await fetchPremarketUniverse());
    } catch (caught) {
      setOfficialUniverseError(
        caught instanceof Error ? caught.message : '官方盘前研究池读取失败',
      );
    } finally {
      setOfficialUniverseLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!today) void loadToday();
  }, [today, loadToday]);

  useEffect(() => {
    void loadOfficialUniverse();
  }, [loadOfficialUniverse]);

  const { quotes } = useTickerQuotes(userTickers);

  const rows = useMemo<WatchlistRow[]>(() => {
    const snapshot = (today?.snapshot?.watchlist ?? {}) as Record<string, unknown>;
    const snapshotByKey = new Map<string, Record<string, unknown>>();
    for (const [k, v] of Object.entries(snapshot)) {
      snapshotByKey.set(k.toUpperCase(), (v ?? {}) as Record<string, unknown>);
    }

    const out: WatchlistRow[] = [];
    const userSet = new Set(userTickers);

    // 1) 用户自选优先展示（保留添加顺序）
    for (const t of userTickers) {
      const snap = snapshotByKey.get(t);
      const base = snap ? derive(snap) : EMPTY_ROW;
      const q = quotes[t] ?? null;
      out.push({
        ticker: t,
        isUserAdded: true,
        ...base,
        // Prefer live quote when the snapshot didn't contain this ticker.
        last: base.last ?? q?.currentPrice,
        chgPct: base.chgPct ?? q?.changePercent ?? undefined,
        chgAbs: base.chgAbs ?? q?.change ?? undefined,
      });
    }
    // 2) 追加 regime snapshot 里、不在自选里的条目
    for (const [t, raw] of snapshotByKey.entries()) {
      if (userSet.has(t)) continue;
      out.push({ ticker: t, isUserAdded: false, ...derive(raw) });
    }
    return out;
  }, [today, userTickers, quotes]);

  const handlePickerAdd = (canonicalCode: string, name?: string) => {
    const ok = addTicker(canonicalCode);
    const label = name ? `${canonicalCode} · ${name}` : canonicalCode;
    toast[ok ? 'success' : 'info'](ok ? `已加入自选: ${label}` : `${canonicalCode} 已在自选里`);
  };

  const handleRemoveTicker = (t: string) => {
    removeTicker(t);
    toast.info(`已移除: ${t}`);
  };

  const handleTradingViewImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    try {
      const parsed = parseTradingViewWatchlist(await file.text());
      if (parsed.symbols.length === 0) {
        toast.error('没有识别到可用于美股期权研究的 ticker。');
        return;
      }
      const added = addManyTickers(parsed.symbols);
      const ignoredMessage = parsed.ignored.length > 0
        ? `；忽略 ${parsed.ignored.length} 个非标准美股标的`
        : '';
      toast.success(
        `TradingView 自选已合并：新增 ${added}，识别 ${parsed.symbols.length}${ignoredMessage}。今日机会扫描使用前 20 只。`,
      );
    } catch {
      toast.error('TradingView TXT 读取失败，请重新导出后再试。');
    }
  };

  const handleSaveOfficialUniverse = async () => {
    if (preparedPremarketUniverse.symbols.length === 0) {
      toast.error('本地自选里没有可用于美股期权研究的标的。');
      return;
    }
    setOfficialUniverseSaving(true);
    setOfficialUniverseError(null);
    try {
      const saved = await savePremarketUniverse(preparedPremarketUniverse.symbols, 5);
      setOfficialUniverse(saved);
      toast.success(
        saved.duplicate
          ? '本地研究池与已保存版本相同，无需重复写入。'
          : `已保存 ${saved.symbols.length} 只标的为官方盘前研究池。`,
      );
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '官方盘前研究池保存失败';
      setOfficialUniverseError(message);
      toast.error(message);
    } finally {
      setOfficialUniverseSaving(false);
    }
  };

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
    {
      id: 'actions',
      header: '',
      size: 36,
      cell: ({ row }) =>
        row.original.isUserAdded ? (
          <button
            type="button"
            aria-label={`Remove ${row.original.ticker}`}
            onClick={(e) => {
              e.stopPropagation();
              handleRemoveTicker(row.original.ticker);
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded-ds-sm text-text-3 transition-colors hover:bg-bg-3 hover:text-down-strong"
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        ) : (
          <span aria-hidden className="inline-block h-6 w-6" />
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
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-label uppercase text-text-3">Watchlist</div>
          <h1 className="text-h1 text-text-1">Tickers</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <TickerPicker onAdd={handlePickerAdd} className="w-[360px]" />
          <label className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-ds-sm border border-subtle bg-bg-1 px-3 text-body-sm text-text-2 hover:border-default hover:text-text-1">
            <Upload size={14} strokeWidth={1.5} />
            导入 TradingView TXT
            <input
              type="file"
              accept=".txt,text/plain"
              className="sr-only"
              onChange={(event) => void handleTradingViewImport(event)}
              aria-label="导入 TradingView 自选 TXT"
            />
          </label>
          <div className="mx-1 h-5 w-px bg-[color:var(--border-subtle)]" aria-hidden />
          <Input
            value={query}
            onChange={setQuery}
            placeholder="Filter…"
            iconLeft={Search}
            size="sm"
            className="w-44"
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

      <section
        className="mt-4 rounded-ds-md border border-subtle bg-bg-1 px-4 py-3"
        aria-label="官方盘前研究池"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-body-sm font-semibold text-text-1">官方盘前研究池</h2>
              <span className={`text-caption ${officialUniverseInSync ? 'text-success' : 'text-text-3'}`}>
                {officialUniverseLoading
                  ? '读取中'
                  : officialUniverseInSync
                    ? '与本地可研究列表一致'
                    : officialUniverse?.configured
                      ? officialUniverseSymbolsInSync
                        ? 'Top 数量需更新'
                        : '与本地列表不同'
                      : '尚未保存'}
              </span>
            </div>
            <p className="mt-1 max-w-3xl text-caption text-text-3">
              后台 09:12 / 09:17 ET 的官方研究只使用这里显式保存的版本。保存顺序会进入版本记录；
              本地增删不会自动改动服务器。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void handleSaveOfficialUniverse()}
            disabled={
              officialUniverseLoading
              || officialUniverseSaving
              || officialUniverseInSync
              || preparedPremarketUniverse.symbols.length === 0
            }
            className="inline-flex h-7 items-center gap-1.5 rounded-ds-sm border border-default bg-bg-2 px-3 text-body-sm text-text-1 hover:bg-bg-3 disabled:cursor-not-allowed disabled:border-subtle disabled:bg-bg-1 disabled:text-text-3"
          >
            {officialUniverseSaving
              ? <RefreshCw size={14} className="animate-spin" />
              : <Save size={14} strokeWidth={1.5} />}
            {officialUniverseInSync
              ? '已与官方池一致'
              : officialUniverseSaving
                ? '正在保存'
                : '保存为官方盘前研究池'}
          </button>
        </div>

        <div className="mt-3 grid gap-3 text-caption md:grid-cols-2">
          <div className="border-l border-subtle pl-3">
            <div className="text-text-3">本地可研究列表</div>
            <div className="mt-0.5 text-text-1">
              {preparedPremarketUniverse.symbols.length} 只
              {preparedPremarketUniverse.symbols.length > 0
                ? ` · ${preparedPremarketUniverse.symbols.join(' · ')}`
                : ' · 暂无受支持的美股期权标的'}
            </div>
          </div>
          <div className="border-l border-subtle pl-3">
            <div className="text-text-3">服务器正式版本</div>
            {officialUniverse?.configured ? (
              <>
                <div className="mt-0.5 text-text-1">
                  {officialUniverse.symbols.length} 只 · Top {officialUniverse.limit}
                  {' · '}{officialUniverse.symbols.join(' · ')}
                </div>
                <div className="mt-0.5 text-text-3">
                  版本{' '}
                  <span
                    className="font-mono text-mono-xs text-text-2"
                    aria-label={officialUniverse.universeVersionKey
                      ? `完整版本 ${officialUniverse.universeVersionKey}`
                      : undefined}
                  >
                    {officialUniverse.universeVersionKey?.slice(0, 20) ?? '未报告'}
                  </span>
                  {' · '}更新 {formatUniverseUpdatedAt(officialUniverse.createdAt)} ET
                  {' · '}来源 {PREMARKET_UNIVERSE_SOURCE_LABELS[officialUniverse.source]}
                </div>
              </>
            ) : (
              <div className="mt-0.5 text-text-2">
                尚未保存正式版本
                {officialUniverse?.symbols.length
                  ? ` · 服务端仅建议 ${officialUniverse.symbols.join(' · ')}`
                  : ''}
                {officialUniverse
                  ? ` · 来源 ${PREMARKET_UNIVERSE_SOURCE_LABELS[officialUniverse.source]}`
                  : ''}
              </div>
            )}
          </div>
        </div>

        {(preparedPremarketUniverse.unsupportedSymbols.length > 0
          || preparedPremarketUniverse.overflowCount > 0) && (
          <p className="mt-2 text-caption text-warning">
            保存时
            {preparedPremarketUniverse.unsupportedSymbols.length > 0
              ? `忽略 ${preparedPremarketUniverse.unsupportedSymbols.length} 个非美股期权标的（${preparedPremarketUniverse.unsupportedSymbols.join('、')}）`
              : ''}
            {preparedPremarketUniverse.unsupportedSymbols.length > 0
              && preparedPremarketUniverse.overflowCount > 0
              ? '；'
              : ''}
            {preparedPremarketUniverse.overflowCount > 0
              ? `仅保留顺序中的前 20 只，另有 ${preparedPremarketUniverse.overflowCount} 只不写入`
              : ''}
            。
          </p>
        )}

        {officialUniverseError && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-caption text-warning" role="status">
            <span>官方盘前研究池暂不可用：{officialUniverseError}</span>
            <button
              type="button"
              onClick={() => void loadOfficialUniverse()}
              className="underline underline-offset-2 hover:text-text-1"
            >
              重试读取
            </button>
          </div>
        )}
      </section>

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

      <p className="mt-2 text-caption text-text-3">
        TradingView 暂无面向个人账户的公开自选 REST API；请在 Advanced View 下载 TXT 后在此合并导入。
        页面预览从本地可研究标的中筛选；后台官方 Top 5 只使用上方已保存的正式研究池。
      </p>

      <section
        className={cn(
          'mt-4 rounded-ds-md border border-subtle bg-bg-1',
          todayLoading && !today && 'opacity-60',
        )}
      >
        {filtered.length === 0 ? (
          <EmptyState
            title={rows.length === 0 ? '自选列表为空' : 'No rows match current filters.'}
            description={
              rows.length === 0
                ? '在上方输入框输入 ticker（比如 AMZN / TSLA），点 Add 加入自选。自选保存在本地，刷新不会丢。Regime 里配置过的 ticker 也会显示在这里。'
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

import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, Play, Plus, Trash2 } from 'lucide-react';
import { Button, Tabs, toast } from '../components/ui';
import { ApiErrorAlert, Loading } from '../components/common';
import { CandlestickChart, type Candle, type MAOverlay } from '../components/charts/CandlestickChart';
import { TradePlan } from '../components/trade/TradePlan';
import { NewsList, type NewsItem } from '../components/content/NewsList';
import { PriceCell } from '../components/data/PriceCell';
import { ChangeCell } from '../components/data/ChangeCell';
import { stocksApi } from '../api/stocks';
import { parseApiError, type ParsedApiError } from '../api/error';
import { useUserWatchlistStore } from '../stores/userWatchlistStore';
import type {
  StockKLine,
  StockNewsItem,
  StockNewsDigestResponse,
  Timeframe,
} from '../types/stockHistory';

const TIMEFRAME_ITEMS: { value: Timeframe; label: string }[] = [
  { value: '5m', label: '5m' },
  { value: '15m', label: '15m' },
  { value: '60m', label: '1h' },
  { value: 'daily', label: '1D' },
  { value: 'weekly', label: '1W' },
  { value: 'monthly', label: '1M' },
];

function daysForTimeframe(tf: Timeframe): number {
  switch (tf) {
    case '1m': return 7;
    case '5m': return 30;
    case '15m': return 60;
    case '30m': return 60;
    case '60m':
    case '1h': return 180;
    case '90m': return 60;
    case 'daily': return 400;
    case 'weekly': return 180;
    case 'monthly': return 240;
  }
}

function toUnixSeconds(iso: string): number {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 0;
  return Math.floor(t / 1000);
}

function klinesToCandles(klines: StockKLine[]): Candle[] {
  return klines
    .map((k) => ({
      time: toUnixSeconds(k.date),
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
      volume: k.volume ?? undefined,
    }))
    .filter((c) => c.time > 0)
    .sort((a, b) => (a.time as number) - (b.time as number));
}

const MA_CONFIG: { period: number; color: string }[] = [
  { period: 8, color: '#d29922' },
  { period: 13, color: '#7170ff' },
  { period: 144, color: '#ef4444' },
  { period: 169, color: '#22c55e' },
];

function computeMA(candles: Candle[], period: number) {
  if (candles.length < period) return [];
  const out: { time: number | string; value: number }[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
    out.push({ time: candles[i].time, value: sum / period });
  }
  return out;
}

const OVERALL_COLOR: Record<number, string> = {
  2: '#16a34a',
  1: '#22c55e',
  0: '#9ca3af',
  [-1]: '#ef4444',
  [-2]: '#b91c1c',
};

const DigestView: React.FC<{ digest: StockNewsDigestResponse }> = ({ digest }) => {
  if (digest.newsCount === 0) {
    return (
      <div className="py-6 text-body-sm text-text-3">
        该 ticker 最近没抓到相关新闻，无法生成 sentiment 与中文摘要。
        <span className="ml-1">请确认 ticker 拼写、或过一会儿重试。</span>
      </div>
    );
  }
  const color = OVERALL_COLOR[digest.overallScore] ?? '#9ca3af';
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-body-sm"
          style={{ borderColor: `${color}66`, color }}
        >
          {digest.overallLabel}
        </span>
        <span className="text-body text-text-1">{digest.summary}</span>
      </div>
      {digest.bullets.length > 0 && (
        <ul className="ml-5 list-disc space-y-1 text-body-sm text-text-1">
          {digest.bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      )}
      <div className="text-caption text-text-3">
        基于最近 {digest.newsCount} 条新闻 · {digest.cached ? '缓存结果' : '实时生成'}
      </div>
    </div>
  );
};

// Hand-curated typo map for common US tickers. Edit distance 1-2 suggests
// the user probably meant a neighbor; if none matches we just fall through
// to the generic tip.
const COMMON_TYPOS: Record<string, string> = {
  AMAZ: 'AMZN',
  APPL: 'AAPL',
  APLE: 'AAPL',
  GOOG: 'GOOGL',
  GOOLG: 'GOOGL',
  MICROSOFT: 'MSFT',
  MICR: 'MSFT',
  MSFR: 'MSFT',
  TESL: 'TSLA',
  TLSA: 'TSLA',
  META1: 'META',
  FACBOOK: 'META',
  NVDA1: 'NVDA',
  NVIDIA: 'NVDA',
  QQQQ: 'QQQ',
  SPYY: 'SPY',
  VIXX: 'VIX',
  BRKA: 'BRK.A',
  BRKB: 'BRK.B',
};
function suggestTicker(ticker: string): string | null {
  return COMMON_TYPOS[ticker.toUpperCase()] ?? null;
}

function toNewsItems(items: StockNewsItem[]): NewsItem[] {
  return items.map((it, idx) => ({
    id: `${idx}-${it.url}`,
    title: it.title,
    source: it.source ?? '',
    excerpt: it.snippet,
    url: it.url,
    publishedAt: it.publishedAt ?? new Date().toISOString(),
  }));
}

const StockDetailPage: React.FC = () => {
  const params = useParams<{ ticker: string }>();
  const navigate = useNavigate();
  const ticker = (params.ticker ?? '').toUpperCase();

  const userTickers = useUserWatchlistStore((s) => s.tickers);
  const addTicker = useUserWatchlistStore((s) => s.add);
  const removeTicker = useUserWatchlistStore((s) => s.remove);
  const inWatchlist = userTickers.includes(ticker);

  const [tab, setTab] = useState<'news' | 'summary' | 'events' | 'history' | 'trace'>('news');
  const [timeframe, setTimeframe] = useState<Timeframe>('daily');

  const [digest, setDigest] = useState<StockNewsDigestResponse | null>(null);
  const [digestLoading, setDigestLoading] = useState(false);
  const [digestError, setDigestError] = useState<ParsedApiError | null>(null);

  const [candles, setCandles] = useState<Candle[]>([]);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<ParsedApiError | null>(null);

  const [news, setNews] = useState<NewsItem[]>([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsError, setNewsError] = useState<ParsedApiError | null>(null);
  const [newsProvider, setNewsProvider] = useState<string | null>(null);

  const [stockName, setStockName] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const run = async () => {
      setChartLoading(true);
      setChartError(null);
      try {
        const resp = await stocksApi.getHistory(ticker, timeframe, daysForTimeframe(timeframe));
        if (cancelled) return;
        setCandles(klinesToCandles(resp.data));
        if (resp.stockName) setStockName(resp.stockName);
      } catch (err) {
        if (cancelled) return;
        setCandles([]);
        setChartError(parseApiError(err));
      } finally {
        if (!cancelled) setChartLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [ticker, timeframe]);

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const run = async () => {
      setNewsLoading(true);
      setNewsError(null);
      // ticker 切换时重置摘要
      setDigest(null);
      setDigestError(null);
      try {
        const resp = await stocksApi.getNews(ticker, 15);
        if (cancelled) return;
        setNews(toNewsItems(resp.items));
        setNewsProvider(resp.provider ?? null);
      } catch (err) {
        if (cancelled) return;
        setNews([]);
        setNewsError(parseApiError(err));
      } finally {
        if (!cancelled) setNewsLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  // 切到 Summary tab 且还没生成过 digest → 按需触发一次 LLM 调用
  useEffect(() => {
    if (tab !== 'summary') return;
    if (!ticker) return;
    if (digest || digestLoading || digestError) return;
    let cancelled = false;
    const run = async () => {
      setDigestLoading(true);
      setDigestError(null);
      try {
        const resp = await stocksApi.getNewsDigest(ticker, { limit: 10 });
        if (cancelled) return;
        setDigest(resp);
      } catch (err) {
        if (cancelled) return;
        setDigestError(parseApiError(err));
      } finally {
        if (!cancelled) setDigestLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [tab, ticker, digest, digestLoading, digestError]);

  // digest 里的 sentiment 合并到 News tab 的每一条（按 url 对齐）
  const newsWithSentiment = useMemo(() => {
    if (!digest) return news;
    const byUrl = new Map(digest.items.map((it) => [it.url, it]));
    return news.map((n) => {
      const match = byUrl.get(n.url);
      return match
        ? { ...n, sentiment: match.score, sentimentReason: match.reason ?? undefined }
        : n;
    });
  }, [news, digest]);

  const overlays: MAOverlay[] = useMemo(() => {
    return MA_CONFIG
      .filter((c) => candles.length >= c.period)
      .map((c) => ({ period: c.period, color: c.color, data: computeMA(candles, c.period) }));
  }, [candles]);

  // "ticker 可能不存在"的判定：K 线 + 新闻 + digest 三条都空，且都加载完、没 error。
  // 只有三个同时空才报错，避免停牌/节假日误伤。
  const invalidTicker = useMemo(() => {
    if (chartLoading || newsLoading || digestLoading) return false;
    if (chartError || newsError) return false;
    if (candles.length > 0) return false;
    if (news.length > 0) return false;
    if (digest && digest.newsCount > 0) return false;
    return true;
  }, [chartLoading, newsLoading, digestLoading, chartError, newsError, candles.length, news.length, digest]);

  const last = candles[candles.length - 1];
  const prev = candles[candles.length - 2];
  const chgAbs = last && prev ? last.close - prev.close : 0;
  const chgPct = last && prev ? (chgAbs / prev.close) * 100 : 0;

  const lastStamp = useMemo(() => {
    if (!last) return '';
    const d = new Date((last.time as number) * 1000);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString([], {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }, [last]);

  const tradeSample: React.ComponentProps<typeof TradePlan> = useMemo(() => {
    if (!last) {
      return {
        side: 'long',
        entry: { price: 0 },
        stop: { price: 0 },
        target: { price: 0 },
      };
    }
    return {
      side: 'long',
      entry: { price: last.close * 0.97, rationale: 'Pullback to MA13 support' },
      entryAlt: { price: last.close * 1.01, rationale: 'Reclaim MA8 on volume' },
      stop: { price: last.close * 0.95, rationale: 'Close below 2 days' },
      target: { price: last.close * 1.06, rationale: 'MA144 resistance' },
    };
  }, [last]);

  if (!ticker) {
    return <div className="p-8 text-body text-text-2">Ticker is missing.</div>;
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-label uppercase text-text-3">Stock</div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-h1 text-text-1">{ticker}</h1>
            {stockName && <span className="text-body text-text-2">{stockName}</span>}
          </div>
          <div className="mt-3 flex items-baseline gap-4">
            <PriceCell value={last?.close} size="md" />
            <ChangeCell value={chgPct} mode="percent" />
            <ChangeCell value={chgAbs} mode="absolute" />
            {lastStamp && <span className="text-caption text-text-3">{lastStamp}</span>}
          </div>
          {last && (
            <div className="mt-1 font-mono text-mono-sm text-text-3">
              Hi {last.high.toFixed(2)} · Lo {last.low.toFixed(2)} · Open {last.open.toFixed(2)}
              {typeof last.volume === 'number' && (
                <> · Vol {(last.volume / 1_000_000).toFixed(1)}M</>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="primary"
            size="md"
            iconLeft={Play}
            onClick={() => toast.info(`Analyze ${ticker}`)}
          >
            Run analysis
          </Button>
          {inWatchlist ? (
            <Button
              variant="secondary"
              size="md"
              iconLeft={Trash2}
              onClick={() => {
                removeTicker(ticker);
                toast.info(`${ticker} 已从自选移出`);
              }}
              title="从自选移除"
            >
              已自选
            </Button>
          ) : (
            <Button
              variant="secondary"
              size="md"
              iconLeft={Plus}
              onClick={() => {
                const ok = addTicker(ticker);
                toast[ok ? 'success' : 'info'](
                  ok ? `${ticker} 已加入自选` : `${ticker} 已在自选里`,
                );
              }}
              title="加入自选（本地保存，刷新不丢）"
            >
              Add
            </Button>
          )}
          <Button
            variant="ghost"
            size="md"
            onClick={() => navigate('/watchlist')}
            title="查看自选列表"
          >
            Watchlist →
          </Button>
        </div>
      </header>

      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Tabs
            value={timeframe}
            onChange={(v) => setTimeframe(v as Timeframe)}
            items={TIMEFRAME_ITEMS}
            variant="pills"
          />
          <span className="text-caption text-text-3">
            {candles.length > 0 ? `${candles.length} bars` : ''}
          </span>
        </div>
        <div className="rounded-ds-md border border-subtle bg-bg-1 p-2">
          {chartLoading && <Loading label="加载 K 线中" />}
          {chartError && (
            <ApiErrorAlert
              error={chartError}
              className="mb-2"
              onDismiss={() => setChartError(null)}
            />
          )}
          {!chartLoading && !chartError && candles.length === 0 && invalidTicker && (
            <div className="mx-auto max-w-xl px-4 py-8 text-center">
              <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-warn-subtle text-warn-strong">
                <AlertTriangle size={20} strokeWidth={1.5} />
              </div>
              <div className="text-body text-text-1">
                未找到 <span className="font-mono">{ticker}</span> 的数据
              </div>
              {suggestTicker(ticker) ? (
                <p className="mt-2 text-body-sm text-text-2">
                  你是不是想输入{' '}
                  <button
                    type="button"
                    className="font-mono text-accent underline-offset-2 hover:underline"
                    onClick={() => navigate(`/stocks/${suggestTicker(ticker)}`)}
                  >
                    {suggestTicker(ticker)}
                  </button>
                  ？
                </p>
              ) : (
                <p className="mt-2 text-body-sm text-text-2">
                  请检查拼写（常见：<span className="font-mono">AMAZ → AMZN</span>、
                  <span className="font-mono">GOOGL / GOOG</span>、
                  <span className="font-mono">BRK.A / BRK.B</span>）。
                  若是港股或 A 股请用完整代码，如 <span className="font-mono">hk00700</span>、
                  <span className="font-mono">600519</span>。
                </p>
              )}
              <div className="mt-3">
                <Button variant="secondary" size="sm" onClick={() => navigate('/watchlist')}>
                  Browse watchlist
                </Button>
              </div>
            </div>
          )}
          {!chartLoading && !chartError && candles.length === 0 && !invalidTicker && (
            <div className="px-4 py-12 text-center text-body-sm text-text-3">
              暂无 K 线数据（停牌 / 非交易时段 / 数据源延迟）
            </div>
          )}
          {!chartLoading && candles.length > 0 && (
            <>
              <CandlestickChart data={candles} overlays={overlays} height={500} />
              <div className="mt-2 flex items-center gap-4 text-caption text-text-3">
                {overlays.map((o) => {
                  const cfg = MA_CONFIG.find((c) => c.period === o.period);
                  return (
                    <span key={o.period}>
                      <span style={{ color: cfg?.color }}>■</span> MA{o.period}
                    </span>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,380px)_1fr]">
        <TradePlan {...tradeSample} />
        <div className="rounded-ds-md border border-subtle bg-bg-1 p-4">
          <div className="text-label uppercase text-text-3">Analysis</div>
          <p className="mt-2 text-body text-text-1">
            {last
              ? `${ticker} 最近一根 ${timeframe} K 线收于 ${last.close.toFixed(2)}；MA8 / MA13 / MA144 / MA169 用于多周期趋势确认。`
              : `${ticker} 数据加载中…`}
          </p>
          <div className="mt-4 flex items-center gap-2 text-body-sm">
            <span className="text-text-3">Stance</span>
            <span className="font-mono uppercase text-text-1">NEUTRAL</span>
            <span className="text-text-3">·</span>
            <span className="text-text-3">Confidence</span>
            <span className="font-mono tabular-nums text-text-1">48</span>
          </div>
        </div>
      </section>

      <section>
        <Tabs
          value={tab}
          onChange={(v) => setTab(v as 'news' | 'summary' | 'events' | 'history' | 'trace')}
          items={[
            { value: 'news', label: 'News', count: news.length },
            { value: 'summary', label: '中文总结' },
            { value: 'events', label: 'Events' },
            { value: 'history', label: 'Analysis history' },
            { value: 'trace', label: 'Trace' },
          ]}
        />
        <div className="mt-2 rounded-ds-md border border-subtle bg-bg-1">
          {tab === 'news' && (
            <>
              {newsLoading && <Loading label="加载新闻中" />}
              {newsError && (
                <div className="p-3">
                  <ApiErrorAlert error={newsError} onDismiss={() => setNewsError(null)} />
                </div>
              )}
              {!newsLoading && !newsError && news.length === 0 && (
                <div className="px-4 py-12 text-center text-body-sm text-text-3">
                  {newsProvider === null
                    ? '未配置新闻搜索 provider（在系统配置里填入 SerpAPI / Tavily / Brave 等 Key 后重试）'
                    : '最近没有相关新闻'}
                </div>
              )}
              {!newsLoading && news.length > 0 && <NewsList items={newsWithSentiment} />}
            </>
          )}
          {tab === 'summary' && (
            <div className="p-4">
              {digestLoading && <Loading label="生成中文总结中（首次 3-8 秒）" />}
              {digestError && (
                <ApiErrorAlert error={digestError} onDismiss={() => setDigestError(null)} />
              )}
              {!digestLoading && !digestError && digest && <DigestView digest={digest} />}
              {!digestLoading && !digestError && !digest && (
                <div className="py-8 text-center text-body-sm text-text-3">
                  切换到该标签页时会按需生成中文摘要，请稍候…
                </div>
              )}
            </div>
          )}
          {tab === 'events' && (
            <div className="px-4 py-12 text-center text-body-sm text-text-3">
              No upcoming events in scope.
            </div>
          )}
          {tab === 'history' && (
            <div className="px-4 py-12 text-center text-body-sm text-text-3">
              Analysis history wires to existing /api/v1/history endpoint — deferred.
            </div>
          )}
          {tab === 'trace' && (
            <div className="px-4 py-12 text-center text-body-sm text-text-3">
              Agent trace viewer — deferred.
            </div>
          )}
        </div>
      </section>
    </div>
  );
};

export default StockDetailPage;

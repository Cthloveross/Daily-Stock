import { useEffect, useMemo, useState } from 'react';
import type {
  IntradayTopResponse,
  OpportunityOptionEvent,
  OpportunityOptionEventItem,
  OpportunityOptionWallItem,
  OpportunityOptionWallRatio,
} from '../../types/opportunities';
import {
  fetchOpportunityOptionEvents,
  fetchOpportunityOptionWalls,
} from '../../api/opportunities';
import { formatCompactUsd } from './intradayFormat';

/**
 * 盘前期权异常 · 昨日事实（premarket options briefing）。
 *
 * 用户诉求：「开盘前我要知道有什么票的期权不对劲，比如特别大单或
 * call/put 比例不一样」——专业盘前简报把**上一时段**的异常期权活动
 * （大单 prints、P/C 偏斜）当作事实陈列。本面板对深度层名单（≤8 檔）
 * 陈列两类上一时段事实：
 *
 * - call/put 成交量比与 OI 比（来自既有 option-walls 端点的 G-24 比例，
 *   0–45 DTE 窗口）；
 * - 最近一页异动里的最大单笔成交（来自既有 option-events 端点，
 *   每檔 ≤10 条）。
 *
 * 零新增取数路径：全部复用既有端点、既有 sessionCache 与在途去重；
 * 墙读取每批 ≤5 檔、异动读取每批 ≤3 檔（端点合同），合计对 8 檔名单
 * 最多 2+3 次请求。逐檔 fail-closed：任何一路失败该檔显式标缺，绝不
 * 用 0 或空值冒充读数。
 *
 * 展示节奏：盘前与开盘 30 分钟（sessionPhase premarket/opening_probe）
 * 自动展开（此时它是简报）；其后默认收起为次要参考（此时它只是昨日
 * 旧事实），用户可手动展开/收起。收起时不发起任何请求。
 *
 * 诚实边界（footer 固定携带）：OI 是上一清算时段口径；成交量与大单是
 * 上一时段累计（盘前绝大多数期权尚无成交）；分类来自供应商，不构成
 * 方向证明；全部阈值是 v1 启发式，未经验证。
 */

/** 面板只覆盖深度层名单：与深度层 8 行硬顶同值，绝不向宽层全清单扇出。 */
export const PREMARKET_OPTION_PANEL_MAX_TICKERS = 8;

/**
 * 「偏斜」阈值（v1 启发式，**未经验证**）：call/put 成交量比或 OI 比
 * ≥3（call 侧）或 ≤0.33（≈1/3，put 侧对称）。圆整数只求可辩护的保守
 * 取值，不是任何统计校准的产物；偏斜是事实标记，不是方向结论。
 */
export const OPTION_SKEW_RATIO_HIGH = 3;
export const OPTION_SKEW_RATIO_LOW = 0.33;

/** 「大单」阈值（v1 启发式，**未经验证**）：单笔成交金额 ≥ $1M。 */
export const LARGE_PRINT_MIN_TURNOVER_USD = 1_000_000;

/** 既有端点合同：option-events 每次 ≤3 檔；option-walls 每次 ≤5 檔。 */
const EVENTS_BATCH_SIZE = 3;
const WALLS_BATCH_SIZE = 5;
/** 异动每檔取最近一页上限（供应商单页 1–10）：找最大单笔要尽量看满一页。 */
const EVENTS_PER_SYMBOL = 10;

const SENTIMENT_LABELS: Record<string, string> = {
  BULLISH: '偏多',
  BEARISH: '偏空',
  NEUTRAL: '中性',
};

const OPTION_TYPE_LABELS: Record<string, string> = {
  CALL: 'Call',
  PUT: 'Put',
};

interface TickerOptionFacts {
  ticker: string;
  /** null＝该檔墙读取失败或无返回行（标缺）。 */
  wall: OpportunityOptionWallItem | null;
  /** null＝该檔异动读取失败或无返回行（标缺）。 */
  events: OpportunityOptionEventItem | null;
}

function chunk<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let index = 0; index < list.length; index += size) {
    out.push(list.slice(index, index + size));
  }
  return out;
}

function isSkewed(value: number | null): boolean {
  return (
    value !== null
    && Number.isFinite(value)
    && (value >= OPTION_SKEW_RATIO_HIGH || value <= OPTION_SKEW_RATIO_LOW)
  );
}

/** 比例读数：value 为 null 时必带 reason（分母 0 等）——无定义就是无定义。 */
function ratioOf(
  wall: OpportunityOptionWallItem | null,
  kind: 'volume' | 'oi',
): OpportunityOptionWallRatio | null {
  const ratios = wall?.ratios ?? null;
  if (!ratios) return null;
  return kind === 'volume' ? ratios.callPutVolumeRatio : ratios.callPutOiRatio;
}

/** 最近一页异动内的最大单笔（按 turnover）；无金额字段的行不参与。 */
function largestPrint(
  item: OpportunityOptionEventItem | null,
): OpportunityOptionEvent | null {
  if (!item) return null;
  let best: OpportunityOptionEvent | null = null;
  for (const event of item.events) {
    if (event.turnover === null || !Number.isFinite(event.turnover)) continue;
    if (!best || (best.turnover ?? 0) < event.turnover) best = event;
  }
  return best;
}

function printDetailLabel(event: OpportunityOptionEvent): string {
  const type = event.optionType
    ? OPTION_TYPE_LABELS[event.optionType.toUpperCase()] ?? event.optionType
    : '—';
  const strike = event.strikePrice !== null
    ? event.strikePrice.toLocaleString('en-US', { maximumFractionDigits: 2 })
    : '—';
  const expiry = event.expiry ?? '到期未报告';
  const dte = event.dte !== null ? ` · ${event.dte}DTE` : '';
  const size = event.volume !== null ? ` · ${event.volume} 张` : '';
  // 分类原样转述为「供应商分类」，绝不改写成方向结论。
  const sentiment = event.sentiment
    ? `${SENTIMENT_LABELS[event.sentiment.toUpperCase()] ?? event.sentiment}（Moomoo 分类）`
    : '未分类';
  return `${type} ${strike} · ${expiry}${dte}${size} · ${formatCompactUsd(event.turnover)} · ${sentiment}`;
}

function formatRatio(value: number | null): string {
  return value !== null && Number.isFinite(value) ? value.toFixed(2) : '标缺';
}

const SKEW_CHIP_CLASS =
  'inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning';

export function PremarketOptionAnomalyPanel({
  top,
}: {
  top: IntradayTopResponse | null;
}) {
  // 深度层名单优先（两层模式的真实深扫名单）；单层模式退回候选列表。
  const tickers = useMemo(() => {
    const source = top?.universeScan?.deepLane?.map((entry) => entry.ticker)
      ?? top?.candidates?.map((candidate) => candidate.ticker)
      ?? [];
    return [...new Set(source)].slice(0, PREMARKET_OPTION_PANEL_MAX_TICKERS);
  }, [top]);
  const tickersKey = tickers.join(',');

  const sessionPhase = top?.sessionPhase ?? null;
  // 盘前 + 开盘 30 分钟（opening_probe=09:30–10:00）＝简报时段，自动展开；
  // 其后收起为次要参考——它承载的是昨日事实，不该与盘中读数抢注意力。
  const prominent = sessionPhase === 'premarket' || sessionPhase === 'opening_probe';
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null);
  const expanded = userExpanded ?? prominent;

  const [facts, setFacts] = useState<Record<string, TickerOptionFacts>>({});
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  // 加载态是纯派生值（展开 + 有名单 + 尚未按当前名单取完），不设并行 state。
  const loading = expanded && tickers.length > 0 && loadedKey !== tickersKey;

  useEffect(() => {
    // 只在展开且名单变化时取数：请求有界（≤2 次墙 + ≤3 次异动），
    // API 层自带 sessionCache 与在途去重，深度层轮换才会重取。
    if (!expanded || tickers.length === 0 || loadedKey === tickersKey) return undefined;
    let cancelled = false;
    const load = async () => {
      const [wallResults, eventResults] = await Promise.all([
        Promise.allSettled(
          chunk(tickers, WALLS_BATCH_SIZE).map((batch) =>
            fetchOpportunityOptionWalls(batch)),
        ),
        Promise.allSettled(
          chunk(tickers, EVENTS_BATCH_SIZE).map((batch) =>
            fetchOpportunityOptionEvents(batch, EVENTS_PER_SYMBOL)),
        ),
      ]);
      if (cancelled) return;
      const wallByTicker = new Map<string, OpportunityOptionWallItem>();
      for (const result of wallResults) {
        if (result.status !== 'fulfilled') continue;
        for (const item of result.value.items) wallByTicker.set(item.ticker, item);
      }
      const eventsByTicker = new Map<string, OpportunityOptionEventItem>();
      for (const result of eventResults) {
        if (result.status !== 'fulfilled') continue;
        for (const item of result.value.items) eventsByTicker.set(item.ticker, item);
      }
      setFacts(Object.fromEntries(tickers.map((ticker) => [
        ticker,
        {
          ticker,
          wall: wallByTicker.get(ticker) ?? null,
          events: eventsByTicker.get(ticker) ?? null,
        },
      ])));
      setLoadedKey(tickersKey);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [expanded, tickersKey, loadedKey, tickers]);

  return (
    <section
      aria-label="盘前期权异常"
      className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1 self-start"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-h3 font-semibold text-text-1">盘前期权异常 · 昨日事实</h2>
          <span className="text-caption text-text-3">
            深度层 ≤{PREMARKET_OPTION_PANEL_MAX_TICKERS} 檔 · 上一时段 call/put 偏斜与大单 · 事实描述，非信号
          </span>
        </div>
        <button
          type="button"
          onClick={() => setUserExpanded(!expanded)}
          aria-expanded={expanded}
          aria-label="展开或收起盘前期权异常面板"
          className="rounded-ds-sm border border-subtle px-2 py-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
        >
          {expanded ? '收起 ▴' : '展开 ▾'}
        </button>
      </header>

      {!expanded ? (
        <div className="px-4 py-2 text-caption text-text-3">
          盘前与开盘 30 分钟自动展开；当前为次要参考（昨日事实），点「展开」查看。
        </div>
      ) : tickers.length === 0 ? (
        <div className="px-4 py-4 text-body-sm text-text-3">
          暂无深度层名单可查（扫描未返回或两层模式未启用时如实为空）。
        </div>
      ) : loading ? (
        <div className="space-y-2 p-4">
          {[0, 1, 2].map((item) => (
            <div key={item} className="h-9 animate-pulse rounded-ds-sm bg-bg-2" />
          ))}
        </div>
      ) : (
        <ul className="space-y-1.5 px-4 py-3">
          {tickers.map((ticker) => {
            const row = facts[ticker];
            const wall = row?.wall ?? null;
            const events = row?.events ?? null;
            const volumeRatio = ratioOf(wall, 'volume');
            const oiRatio = ratioOf(wall, 'oi');
            const print = largestPrint(events);
            const printLarge =
              print !== null
              && (print.turnover ?? 0) >= LARGE_PRINT_MIN_TURNOVER_USD;
            const volumeSkewed = isSkewed(volumeRatio?.value ?? null);
            const oiSkewed = isSkewed(oiRatio?.value ?? null);
            const hasFlags = volumeSkewed || oiSkewed || printLarge;
            return (
              <li
                key={ticker}
                className="rounded-ds-sm border border-subtle bg-bg-0 px-3 py-2"
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="font-mono text-mono-sm font-semibold text-text-1">
                    {ticker}
                  </span>
                  {volumeSkewed && (
                    <span className={SKEW_CHIP_CLASS}>
                      偏斜 · 量比 {formatRatio(volumeRatio?.value ?? null)}
                    </span>
                  )}
                  {oiSkewed && (
                    <span className={SKEW_CHIP_CLASS}>
                      偏斜 · OI比 {formatRatio(oiRatio?.value ?? null)}
                    </span>
                  )}
                  {printLarge && print && (
                    <span className={SKEW_CHIP_CLASS}>
                      大单 {formatCompactUsd(print.turnover)}
                    </span>
                  )}
                  {!hasFlags && (
                    <span className="text-caption text-text-3">无异常标记</span>
                  )}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-caption text-text-3">
                  <span>
                    {wall && volumeRatio
                      ? `量比 C/P ${formatRatio(volumeRatio.value)}${
                        volumeRatio.value === null && volumeRatio.reason
                          ? `（${volumeRatio.reason}）`
                          : ''
                      }`
                      : '量比标缺'}
                  </span>
                  <span>
                    {wall && oiRatio
                      ? `OI比 C/P ${formatRatio(oiRatio.value)}${
                        oiRatio.value === null && oiRatio.reason
                          ? `（${oiRatio.reason}）`
                          : ''
                      }`
                      : 'OI比标缺'}
                  </span>
                  <span>
                    {events === null
                      ? '大单标缺'
                      : print === null
                        ? '最近一页无带金额成交'
                        : `最大单 ${formatCompactUsd(print.turnover)}`}
                  </span>
                </div>
                {print && (
                  <div className="mt-0.5 text-caption text-text-3">
                    大单明细：{printDetailLabel(print)}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {expanded && (
        <div className="space-y-1 border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
          <div>
            口径：OI＝上一清算时段结算；成交量与大单＝供应商快照/异动页的会话累计——盘前绝大多数期权尚无成交，此刻读到的即上一时段事实，缺失显式标缺。大单取自最近一页异动（≤{EVENTS_PER_SYMBOL} 条/檔），不是全时段全量最大；比例窗口 0–45 DTE。
          </div>
          <div>
            阈值：偏斜＝量比或 OI比 ≥{OPTION_SKEW_RATIO_HIGH} 或 ≤{OPTION_SKEW_RATIO_LOW}；大单＝单笔 ≥$1M——v1 启发式，未经验证。
          </div>
          <div>
            分类来自供应商，不构成方向证明；比例异常≠会涨会跌——该假说在你的数据上尚未检验（期权墙逐日快照正在积累样本）。
          </div>
        </div>
      )}
    </section>
  );
}

import { useEffect, useState, type ReactNode } from 'react';
import type {
  IntradayTopResponse,
  OpportunityOptionEvent,
  OpportunityOptionEventItem,
  OpportunityOptionWallItem,
  OpportunityOptionWallRatio,
} from '../../types/opportunities';
import { fetchOpportunityOptionEvents } from '../../api/opportunities';
import {
  DEEP_LANE_OPTION_WALL_MAX_TICKERS,
  chunkSymbols,
  useDeepLaneOptionWalls,
} from '../../hooks/useDeepLaneOptionWalls';
import { InfoHint } from '../common/InfoHint';
import { Tooltip } from '../common/Tooltip';
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
 *   0–45 DTE 窗口；墙读取经 useDeepLaneOptionWalls 与实时扫描表「期权墙」
 *   列共用同一次请求与缓存）；
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
 * 布局（2026-08-15 依用户反馈整理）：每檔一行事实一条线、标签对齐；
 * 大单章把方向写进标题（「大单 Call $7.9M」）；比例无定义时可见文案收敛为
 * 紧凑短语（如「近月窗口无合约」），服务端原因逐字连同「为什么」挂 ⓘ
 * tooltip。诚实边界原文一字不删，收进标题行的 ⓘ（界面披露策略）。
 */

/** 面板只覆盖深度层名单：与深度层 8 行硬顶同值，绝不向宽层全清单扇出。 */
export const PREMARKET_OPTION_PANEL_MAX_TICKERS = DEEP_LANE_OPTION_WALL_MAX_TICKERS;

/**
 * 「偏斜」阈值（v1 启发式，**未经验证**）：call/put 成交量比或 OI 比
 * ≥3（call 侧）或 ≤0.33（≈1/3，put 侧对称）。圆整数只求可辩护的保守
 * 取值，不是任何统计校准的产物；偏斜是事实标记，不是方向结论。
 */
export const OPTION_SKEW_RATIO_HIGH = 3;
export const OPTION_SKEW_RATIO_LOW = 0.33;

/** 「大单」阈值（v1 启发式，**未经验证**）：单笔成交金额 ≥ $1M。 */
export const LARGE_PRINT_MIN_TURNOVER_USD = 1_000_000;

/** 既有端点合同：option-events 每次 ≤3 檔。 */
const EVENTS_BATCH_SIZE = 3;
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

/**
 * 诚实边界原文（逐字，2026-08-15 从可见 footer/标题行迁入 ⓘ tooltip——
 * 隐藏 ≠ 删除；可见界面只保留逐读数的标缺原因与 as-of）。
 */
export const PREMARKET_PANEL_HONESTY_NOTE = [
  '事实描述，非信号。',
  `口径：OI＝上一清算时段结算；成交量与大单＝供应商快照/异动页的会话累计——盘前绝大多数期权尚无成交，此刻读到的即上一时段事实，缺失显式标缺。大单取自最近一页异动（≤${EVENTS_PER_SYMBOL} 条/檔），不是全时段全量最大；比例窗口 0–45 DTE。`,
  `阈值：偏斜＝量比或 OI比 ≥${OPTION_SKEW_RATIO_HIGH} 或 ≤${OPTION_SKEW_RATIO_LOW}；大单＝单笔 ≥$1M——v1 启发式，未经验证。`,
  '分类来自供应商，不构成方向证明；比例异常≠会涨会跌——该假说在你的数据上尚未检验（期权墙逐日快照正在积累样本）。',
].join('\n');

/** 服务端「窗口内无有效合约」原因（src/opportunities/option_walls.py 逐字）。 */
const RATIO_REASON_NO_DATA = '该窗口内无有效合约，无法计算比例';

/**
 * 比例无定义时的紧凑可见文案：长原因收进 ⓘ tooltip，可见行只留一个短语。
 * 「近月窗口无合约」＝比例窗口（0–45 DTE）内没有任何有效合约——像 CRWV
 * 这种大单落在远月 LEAP 上的檔就是这个原因。
 */
function ratioCompactReason(reason: string | null): string {
  if (reason === RATIO_REASON_NO_DATA || (reason ?? '').includes('无有效合约')) {
    return '近月窗口无合约';
  }
  if ((reason ?? '').includes('分母为 0')) return '分母为 0';
  return '无法计算';
}

/** 比例无定义 ⓘ tooltip：服务端原因逐字 + 为什么会这样（教学句）。 */
function ratioMissingTooltip(reason: string | null): string {
  return [
    `服务端原因（逐字）：${reason ?? '未报告'}`,
    '比例窗口只扫 0–45 DTE 的近月合约（与期权墙同一窗口）。若该檔的期权活动集中在远月'
    + '——例如 CRWV 的大单落在数百 DTE 的 LEAP 上——近月窗口内可能没有任何有效合约：'
    + '比例无定义，如实标缺，不折算成 0 或无穷大。',
  ].join('\n');
}

interface TickerOptionFacts {
  ticker: string;
  /** null＝该檔墙读取失败或无返回行（标缺）。 */
  wall: OpportunityOptionWallItem | null;
  /** null＝该檔异动读取失败或无返回行（标缺）。 */
  events: OpportunityOptionEventItem | null;
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

/** 大单方向标签（Call/Put）；供应商未报告时返回 null，不猜方向。 */
function printSideLabel(event: OpportunityOptionEvent): string | null {
  if (!event.optionType) return null;
  return OPTION_TYPE_LABELS[event.optionType.toUpperCase()] ?? event.optionType;
}

function printDetailLabel(event: OpportunityOptionEvent): string {
  const type = printSideLabel(event) ?? '方向未报告';
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

/** 一檔一组事实行：标签列对齐（w-20），一行一个事实，不重复说明文字。 */
function FactRow({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <div data-fact-id={id} className="flex items-baseline gap-x-2">
      <span className="w-20 shrink-0 text-caption text-text-3">{label}</span>
      <span className="min-w-0 text-caption text-text-2">{children}</span>
    </div>
  );
}

/** 比例事实行内容：有值给值；无定义给紧凑原因 + ⓘ；墙整路失败给标缺。 */
function ratioFactValue(
  wall: OpportunityOptionWallItem | null,
  ratio: OpportunityOptionWallRatio | null,
) {
  if (!wall || !ratio) {
    return <span className="text-text-3">标缺（该檔墙读取失败或无返回）</span>;
  }
  if (ratio.value !== null && Number.isFinite(ratio.value)) {
    return <span>{formatRatio(ratio.value)}</span>;
  }
  const compact = ratioCompactReason(ratio.reason);
  const tooltip = ratioMissingTooltip(ratio.reason);
  return (
    <span className="text-text-3">
      标缺 ·{' '}
      <Tooltip focusable contentClassName="whitespace-pre-line" content={tooltip}>
        <span
          aria-label={tooltip}
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          {compact} ⓘ
        </span>
      </Tooltip>
    </span>
  );
}

export function PremarketOptionAnomalyPanel({
  top,
}: {
  top: IntradayTopResponse | null;
}) {
  const sessionPhase = top?.sessionPhase ?? null;
  // 盘前 + 开盘 30 分钟（opening_probe=09:30–10:00）＝简报时段，自动展开；
  // 其后收起为次要参考——它承载的是昨日事实，不该与盘中读数抢注意力。
  const prominent = sessionPhase === 'premarket' || sessionPhase === 'opening_probe';
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null);
  const expanded = userExpanded ?? prominent;

  // 墙读取走共享 hook（与实时扫描表「期权墙」列同一次请求与缓存）；
  // 收起时 enabled=false，零请求。名单在 hook 内已按 ≤8 檔硬顶截断
  // （PREMARKET_OPTION_PANEL_MAX_TICKERS 与之同值），引用稳定。
  const walls = useDeepLaneOptionWalls(top, expanded);
  const tickers = walls.tickers;
  const tickersKey = tickers.join(',');

  const [eventsByTicker, setEventsByTicker] = useState<
    Map<string, OpportunityOptionEventItem>
  >(() => new Map());
  const [eventsLoadedKey, setEventsLoadedKey] = useState<string | null>(null);
  // 加载态是纯派生值（展开 + 有名单 + 任一路尚未取完），不设并行 state。
  const eventsLoading =
    expanded && tickers.length > 0 && eventsLoadedKey !== tickersKey;
  const loading = walls.loading || eventsLoading;

  useEffect(() => {
    // 只在展开且名单变化时取异动（≤3 次批请求）；API 层自带 sessionCache
    // 与在途去重，深度层轮换才会重取。墙的取数节奏由共享 hook 管。
    if (!expanded || tickers.length === 0 || eventsLoadedKey === tickersKey) {
      return undefined;
    }
    let cancelled = false;
    const load = async () => {
      const results = await Promise.allSettled(
        chunkSymbols(tickers, EVENTS_BATCH_SIZE).map((batch) =>
          fetchOpportunityOptionEvents(batch, EVENTS_PER_SYMBOL)),
      );
      if (cancelled) return;
      const next = new Map<string, OpportunityOptionEventItem>();
      for (const result of results) {
        if (result.status !== 'fulfilled') continue;
        for (const item of result.value.items) next.set(item.ticker, item);
      }
      setEventsByTicker(next);
      setEventsLoadedKey(tickersKey);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [expanded, tickersKey, eventsLoadedKey, tickers]);

  return (
    <section
      aria-label="盘前期权异常"
      className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1 self-start"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-h3 font-semibold text-text-1">盘前期权异常 · 昨日事实</h2>
          <span className="text-caption text-text-3">
            深度层 ≤{PREMARKET_OPTION_PANEL_MAX_TICKERS} 檔 · 上一时段 call/put 偏斜与大单
          </span>
          {/* 界面披露策略：诚实边界原文一字不删，收进这枚 ⓘ。 */}
          <InfoHint text={PREMARKET_PANEL_HONESTY_NOTE} label="盘前期权异常口径" />
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
            const row: TickerOptionFacts = {
              ticker,
              wall: walls.wallByTicker.get(ticker) ?? null,
              events: eventsByTicker.get(ticker) ?? null,
            };
            const { wall, events } = row;
            const volumeRatio = ratioOf(wall, 'volume');
            const oiRatio = ratioOf(wall, 'oi');
            const print = largestPrint(events);
            const printLarge =
              print !== null
              && (print.turnover ?? 0) >= LARGE_PRINT_MIN_TURNOVER_USD;
            const printSide = print ? printSideLabel(print) : null;
            const volumeSkewed = isSkewed(volumeRatio?.value ?? null);
            const oiSkewed = isSkewed(oiRatio?.value ?? null);
            const hasFlags = volumeSkewed || oiSkewed || printLarge;
            return (
              <li
                key={ticker}
                aria-label={`${ticker} 盘前期权事实`}
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
                  {/* 大单章把方向写进标题（用户要求「明显一点」）：
                      「大单 Call $7.9M」；供应商未报告方向时如实只给金额。 */}
                  {printLarge && print && (
                    <span className={SKEW_CHIP_CLASS}>
                      大单{printSide ? ` ${printSide}` : ''} {formatCompactUsd(print.turnover)}
                    </span>
                  )}
                  {!hasFlags && (
                    <span className="text-caption text-text-3">无异常标记</span>
                  )}
                </div>
                {/* 一行一个事实、标签对齐；最大单一行给全明细，不再重复金额行。 */}
                <div className="mt-1 space-y-0.5">
                  <FactRow id="volume_ratio" label="量比 C/P">
                    {ratioFactValue(wall, volumeRatio)}
                  </FactRow>
                  <FactRow id="oi_ratio" label="OI比 C/P">
                    {ratioFactValue(wall, oiRatio)}
                  </FactRow>
                  <FactRow id="largest_print" label="最大单">
                    {events === null ? (
                      <span className="text-text-3">标缺（该檔异动读取失败或无返回）</span>
                    ) : print === null ? (
                      <span className="text-text-3">最近一页无带金额成交</span>
                    ) : (
                      printDetailLabel(print)
                    )}
                  </FactRow>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

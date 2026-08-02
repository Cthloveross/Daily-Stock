import { Fragment, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type {
  IntradayTopCandidate,
  IntradayTopResponse,
} from '../../types/opportunities';
import { parseApiTimestamp } from '../../utils/marketTime';
import { formatCompactUsd, formatRatio, formatSignedPercent } from './intradayFormat';
import { NearExpiryContractPanel } from './NearExpiryContractPanel';

const STATE_LABELS: Record<IntradayTopCandidate['researchState'], string> = {
  active: '盘中活跃',
  watch: '观察',
  insufficient: '数据不足',
};

const STATE_STYLES: Record<IntradayTopCandidate['researchState'], string> = {
  active: 'text-text-1',
  watch: 'text-text-2',
  insufficient: 'text-warning',
};

const SENTIMENT_LABELS: Record<string, string> = {
  bullish: '偏多',
  bearish: '偏空',
  neutral: '中性',
  mixed: '多空分歧',
  unknown: '未知',
};

const VWAP_LABELS: Record<IntradayTopCandidate['vwapPosition'], string> = {
  above: 'VWAP 上方',
  below: 'VWAP 下方',
  flat: 'VWAP 持平',
  unknown: '标缺',
};

export type IntradaySortKey =
  | 'burst'
  | 'change'
  | 'gap'
  | 'pace'
  | 'expansion'
  | 'events';

interface SortState {
  key: IntradaySortKey;
  direction: 'desc' | 'asc';
}

function formatPrice(value: number | null): string {
  if (value === null) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function quoteAsOfLabel(item: IntradayTopCandidate): string {
  const fromQuote = item.quoteAsOf?.match(/\d{2}:\d{2}(?::\d{2})?/)?.[0];
  if (fromQuote) return `${fromQuote} ET`;
  const parsed = parseApiTimestamp(item.fetchedAt);
  if (!parsed) return '时点未报告';
  return `${new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(parsed)} ET`;
}

function sortValue(item: IntradayTopCandidate, key: IntradaySortKey): number | null {
  switch (key) {
    case 'burst':
      return item.sessionBursts.state === 'ready'
        ? item.sessionBursts.current?.score ?? null
        : null;
    case 'change':
      return item.sessionChangePercent;
    case 'gap':
      return item.gapPercent === null ? null : Math.abs(item.gapPercent);
    case 'pace':
      return item.volumePaceRatio;
    case 'expansion':
      return item.atrRangeExpansion;
    case 'events':
      return item.optionActivity.count;
    default:
      return null;
  }
}

const DIRECTION_ARROWS: Record<'up' | 'down' | 'flat', string> = {
  up: '↑',
  down: '↓',
  flat: '·',
};

function formatBurstThrust(value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

/** 「N 波：09:40↓ · 15:15↑」——当日（或最近一个交易时段）的独立波段摘要。 */
function legsSummary(item: IntradayTopCandidate): string | null {
  const bursts = item.sessionBursts;
  if (bursts.state !== 'ready') return null;
  if (bursts.legs.length === 0) return '0 波';
  const parts = bursts.legs.map(
    (leg) => `${leg.startEt}${DIRECTION_ARROWS[leg.direction]}`,
  );
  return `${bursts.legs.length} 波：${parts.join(' · ')}`;
}

function legsDetailLabel(item: IntradayTopCandidate): string | undefined {
  const bursts = item.sessionBursts;
  if (bursts.state !== 'ready' || bursts.legs.length === 0) return undefined;
  return `波段明细：${bursts.legs
    .map(
      (leg) =>
        `${leg.startEt}-${leg.endEt} ${DIRECTION_ARROWS[leg.direction]} `
        + `${formatBurstThrust(leg.thrustPercent)} · 爆发分 ${leg.score?.toFixed(1) ?? '—'}`,
    )
    .join('；')}`;
}

function optionActivityLabel(item: IntradayTopCandidate): string {
  const activity = item.optionActivity;
  if (activity.state === 'not_configured') return '未配置';
  if (activity.state === 'unavailable') return '标缺';
  if (activity.count === 0) return '0 笔';
  const sentiment = SENTIMENT_LABELS[activity.dominantSentiment] ?? activity.dominantSentiment;
  return `${activity.count} 笔 · ${sentiment} · 最大单 ${formatCompactUsd(activity.maxSingleTurnover)}`;
}

/**
 * 日内扫描表：确定性证据计数排名的盘中滚动 Top N。
 * 列头可点击做客户端排序（不改变服务端排名口径）；行点击进入即时扫描详情页。
 *
 * 临期合约交互取「行点击不变 + 每行显式按钮」：整行点击仍是既有的详情页
 * 导航（不改变肌肉记忆），行尾「临期合约」按钮在行下方展开只读合约面板；
 * 同一时刻只展开一行，保持表格可用性。
 */
export function IntradayScanTable({
  data,
  loading,
  error,
}: {
  data: IntradayTopResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const navigate = useNavigate();
  const [sort, setSort] = useState<SortState | null>(null);
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);

  const rows = useMemo(() => {
    const candidates = data?.candidates ?? [];
    if (!sort) return candidates;
    const sorted = [...candidates].sort((left, right) => {
      const a = sortValue(left, sort.key);
      const b = sortValue(right, sort.key);
      if (a === null && b === null) return 0;
      if (a === null) return 1; // 标缺永远排最后，不参与方向。
      if (b === null) return -1;
      return sort.direction === 'desc' ? b - a : a - b;
    });
    return sorted;
  }, [data, sort]);

  const toggleSort = (key: IntradaySortKey) => {
    setSort((current) => {
      if (!current || current.key !== key) return { key, direction: 'desc' };
      if (current.direction === 'desc') return { key, direction: 'asc' };
      return null; // 第三次点击回到服务端证据排名。
    });
  };

  const sortMark = (key: IntradaySortKey): string => {
    if (!sort || sort.key !== key) return '';
    return sort.direction === 'desc' ? ' ↓' : ' ↑';
  };

  const sortableHeader = (key: IntradaySortKey, label: string) => (
    <button
      type="button"
      onClick={() => toggleSort(key)}
      className="font-medium text-text-3 hover:text-text-1"
      aria-label={`按${label}排序`}
    >
      {label}
      {sortMark(key)}
    </button>
  );

  return (
    <section aria-label="日内扫描" className="overflow-hidden rounded-ds-md border border-subtle bg-bg-1">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-subtle px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-h3 font-semibold text-text-1">日内扫描 · 盘中滚动</h2>
          <span className="text-caption text-text-3">
            {data?.quoteSessionScope === 'latest_prior_session'
              ? '休市 · 按最近交易时段最强波段排序'
              : '波段爆发优先排名'}
            （{data?.signalVersion ?? 'intraday_session_evidence_v2'}）· 不冻结 · 不入统计
          </span>
        </div>
        {data && (
          <span className="text-caption text-text-3">
            {data.quoteSessionLabel} · 扫描 {data.universe.length} 个标的
            {data.unsupportedSymbols.length > 0
              ? ` · ${data.unsupportedSymbols.length} 个非美股期权标的未纳入`
              : ''}
          </span>
        )}
      </header>

      {error && (
        <div className="border-b border-[color:var(--warn-muted)] bg-bg-0 px-4 py-2 text-caption text-warning" role="status">
          日内扫描暂不可用：{error}
          {data ? '。仍显示上一次成功结果。' : ''}
        </div>
      )}

      {!data && loading ? (
        <div className="space-y-2 p-4">
          {[0, 1, 2].map((item) => (
            <div key={item} className="h-10 animate-pulse rounded-ds-sm bg-bg-2" />
          ))}
        </div>
      ) : !data || rows.length === 0 ? (
        <div className="px-4 py-6 text-body-sm text-text-3">
          当前没有可展示的日内候选。请先在 Watchlist 加入美股期权标的，或检查服务端 STOCK_LIST 与 Moomoo 行情配置。
        </div>
      ) : (
        <div className="overflow-auto">
          <table className="w-full min-w-[1180px] border-collapse" aria-label="日内扫描表">
            <thead>
              <tr className="border-b border-subtle text-left text-caption text-text-3">
                <th className="px-3 py-2 font-medium">标的</th>
                <th className="px-3 py-2 text-right">{sortableHeader('burst', '当前爆发')}</th>
                <th className="px-3 py-2 font-medium">今日波段</th>
                <th className="px-3 py-2 text-right">{sortableHeader('change', '现价 / 当日')}</th>
                <th className="px-3 py-2 text-right">{sortableHeader('gap', '缺口')}</th>
                <th className="px-3 py-2 text-right">{sortableHeader('pace', '量能节奏')}</th>
                <th className="px-3 py-2 font-medium">VWAP</th>
                <th className="px-3 py-2 text-right">{sortableHeader('expansion', '波幅扩张(ATR)')}</th>
                <th className="px-3 py-2">{sortableHeader('events', '期权异动')}</th>
                <th className="px-3 py-2 font-medium">研究状态</th>
                <th className="px-3 py-2 font-medium">合约</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <Fragment key={item.ticker}>
                <tr
                  onClick={() => navigate(`/regime/opportunity/${item.ticker}`)}
                  className="cursor-pointer border-b border-subtle last:border-b-0 hover:bg-bg-2"
                  aria-label={`打开 ${item.ticker} 即时扫描详情`}
                >
                  <td className="px-3 py-2.5">
                    <div className="font-mono text-mono-sm font-semibold text-text-1">{item.ticker}</div>
                    <div className="mt-0.5 text-caption text-text-3">
                      {item.supportingEvidenceCount} 项证据支持
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {item.sessionBursts.state === 'ready' && item.sessionBursts.current ? (
                      <>
                        <div className="font-mono text-mono-sm text-text-1">
                          {item.sessionBursts.current.score === null
                            ? '—'
                            : item.sessionBursts.current.score.toFixed(1)}
                          <span className={`ml-1 ${
                            item.sessionBursts.current.direction === 'up'
                              ? 'text-up-strong'
                              : item.sessionBursts.current.direction === 'down'
                                ? 'text-down-strong'
                                : 'text-text-3'
                          }`}
                          >
                            {DIRECTION_ARROWS[item.sessionBursts.current.direction]}
                          </span>
                        </div>
                        <div className="mt-0.5 text-caption text-text-3">
                          15分 {formatBurstThrust(item.sessionBursts.current.thrustPercent)}
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="font-mono text-mono-sm text-text-3">—</div>
                        <div className="mt-0.5 text-caption text-text-3">
                          {item.sessionBursts.state === 'insufficient_bars' ? 'K线不足' : '标缺'}
                        </div>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="text-body-sm text-text-1" aria-label={legsDetailLabel(item)}>
                      {legsSummary(item) ?? '标缺'}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">
                      {item.sessionBursts.medianBasis === 'prior_session_fallback'
                        ? '基准回退上一时段'
                        : '≥30 分钟独立波段'}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-sm text-text-1">
                      {formatPrice(item.lastPrice)}
                      <span className={`ml-2 ${
                        item.sessionChangePercent === null
                          ? 'text-text-3'
                          : item.sessionChangePercent > 0
                            ? 'text-up-strong'
                            : item.sessionChangePercent < 0
                              ? 'text-down-strong'
                              : 'text-text-3'
                      }`}
                      >
                        {formatSignedPercent(item.sessionChangePercent)}
                      </span>
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">{quoteAsOfLabel(item)}</div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-xs text-text-1">{formatSignedPercent(item.gapPercent)}</div>
                    <div className="mt-0.5 text-caption text-text-3">
                      {item.gapPercent === null
                        ? '标缺'
                        : item.gapAtrMultiple !== null
                          ? `${item.gapAtrMultiple.toFixed(2)}×ATR${item.gapBasis === 'session_open_vs_moomoo_snapshot_prev_close' ? ' · 快照前收' : ''}`
                          : item.gapBasis === 'session_open_vs_moomoo_snapshot_prev_close'
                            ? '对快照前收'
                            : '对上一完整日收盘'}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-xs text-text-1">{formatRatio(item.volumePaceRatio)}</div>
                    <div className="mt-0.5 text-caption text-text-3">vs 20日全日中位</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="text-body-sm text-text-1">{VWAP_LABELS[item.vwapPosition]}</div>
                    <div className="mt-0.5 text-caption text-text-3">
                      {item.vwap !== null ? `≈ ${formatPrice(item.vwap)} · 额/量近似` : '额/量近似'}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-xs text-text-1">{formatRatio(item.atrRangeExpansion)}</div>
                    <div className="mt-0.5 text-caption text-text-3">当日高低 ÷ ATR14</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div
                      className="text-body-sm text-text-1"
                      aria-label={
                        item.optionActivity.limitations.length > 0
                          ? `期权异动限制：${item.optionActivity.limitations.join('；')}`
                          : undefined
                      }
                    >
                      {optionActivityLabel(item)}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">Moomoo 分类 · 不证明方向</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className={`text-body-sm font-medium ${STATE_STYLES[item.researchState]}`}>
                      {STATE_LABELS[item.researchState]}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <button
                      type="button"
                      onClick={(event) => {
                        // 阻断整行导航：按钮只负责展开/收起合约面板。
                        event.stopPropagation();
                        setExpandedTicker((current) => (
                          current === item.ticker ? null : item.ticker
                        ));
                      }}
                      className="whitespace-nowrap rounded-ds-sm border border-subtle px-2 py-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
                      aria-expanded={expandedTicker === item.ticker}
                      aria-label={`展开 ${item.ticker} 临期合约`}
                    >
                      临期合约
                      {expandedTicker === item.ticker ? ' ▴' : ' ▾'}
                    </button>
                  </td>
                </tr>
                {expandedTicker === item.ticker && (
                  <tr className="border-b border-subtle last:border-b-0">
                    <td colSpan={11} className="bg-bg-0 px-3 py-3">
                      <NearExpiryContractPanel symbol={item.ticker} />
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
        当前爆发＝最近 15 分钟（3 根 5m K 线）|收−开| ÷ 当日 5m 波幅中位 × 窗口量比（阈值按 2026-07-31 标注样本校准，盘中排序优先，不是信号）；今日波段＝爆发分 ≥ 8 且起点相隔 ≥30 分钟的独立窗口（≤4 个，休市显示最近一个交易时段）；缺口＝开盘价对参考前收（休市时段改用快照前收并标注）；量能节奏＝当日累计 vs 20 日全日中位（未按时点折算）；波幅扩张＝当日高低价差 ÷ ATR14；期权异动＝最近一页 Moomoo 分类计数，不推断开平仓。缺失字段显式标缺，不以 0 冒充。
      </div>
    </section>
  );
}

import { Fragment, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type {
  IntradaySetupMatch,
  IntradayTopCandidate,
  IntradayTopResponse,
} from '../../types/opportunities';
import { parseApiTimestamp } from '../../utils/marketTime';
import { Tooltip } from '../common/Tooltip';
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

/** v3 大盘对齐：顺势/逆势/标缺——仅作标注，不隐藏行、不改变排序。 */
const ALIGNMENT_LABELS: Record<IntradayTopCandidate['marketAlignment']['state'], string> = {
  aligned: '顺势',
  against: '逆势',
  unknown: '标缺',
};

/** v3 速度分级：加速/减速/持平/标缺（5m 窗口近似，减速＝用户 R1 离场提示）。 */
const SPEED_LABELS: Record<IntradayTopCandidate['sessionBursts']['speed']['state'], string> = {
  accelerating: '加速',
  decelerating: '减速',
  flat: '持平',
  unknown: '标缺',
};

const SPEED_STYLES: Record<IntradayTopCandidate['sessionBursts']['speed']['state'], string> = {
  accelerating: 'text-up-strong',
  decelerating: 'text-down-strong',
  flat: 'text-text-2',
  unknown: 'text-text-3',
};

export type IntradaySortKey =
  | 'burst'
  | 'change'
  | 'gap'
  | 'pace'
  | 'expansion'
  | 'events'
  | 'earnings';

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
    case 'earnings':
      // 数值排序按距财报天数；日历标缺或窗口内无财报的行落到最后。
      return item.earningsProximity.state === 'ready'
        ? item.earningsProximity.daysToEarnings
        : null;
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

/** v3 财报列：回避窗内醒目标注「财报 N 天内 · 期权贵」；标缺绝不冒充安全。 */
function earningsLabel(item: IntradayTopCandidate): {
  text: string;
  emphasized: boolean;
  caption: string;
} {
  const proximity = item.earningsProximity;
  if (proximity.state !== 'ready') {
    return { text: '标缺', emphasized: false, caption: '财报日历不可得 · 未知≠安全' };
  }
  if (proximity.daysToEarnings === null) {
    return {
      text: `${proximity.windowDays} 天内无`,
      emphasized: false,
      caption: 'Finnhub 前向窗口',
    };
  }
  if (proximity.withinBlackout) {
    return {
      text: proximity.daysToEarnings === 0
        ? '今日财报 · 期权贵'
        : `财报 ${proximity.daysToEarnings} 天内 · 期权贵`,
      emphasized: true,
      caption: `${proximity.earningsDate ?? ''} · 你的回避规则（≤${proximity.blackoutDays} 天）`,
    };
  }
  return {
    text: `${proximity.daysToEarnings} 天后财报`,
    emphasized: false,
    caption: proximity.earningsDate ?? '',
  };
}

const ALIGNMENT_DIRECTION_LABELS: Record<string, string> = {
  up: '爆发↑',
  down: '爆发↓',
  flat: '爆发·',
};

const ALIGNMENT_SPY_LABELS: Record<string, string> = {
  above: 'SPY VWAP上',
  below: 'SPY VWAP下',
  flat: 'SPY VWAP平',
};

function alignmentCaption(item: IntradayTopCandidate): string {
  const alignment = item.marketAlignment;
  if (alignment.state === 'unknown') return 'SPY 或爆发方向标缺';
  return `${ALIGNMENT_DIRECTION_LABELS[alignment.burstDirection ?? ''] ?? ''} · ${
    ALIGNMENT_SPY_LABELS[alignment.spyVwapPosition ?? ''] ?? ''
  }`;
}

function speedCaption(item: IntradayTopCandidate): string {
  const speed = item.sessionBursts.speed;
  if (speed.state === 'unknown' || speed.delta === null) return '相邻 15 分钟窗口';
  const sign = speed.delta > 0 ? '+' : speed.delta < 0 ? '−' : '';
  return `Δ ${sign}${Math.abs(speed.delta).toFixed(1)}`;
}

/**
 * v4 styleMatch：形态徽标 tooltip——matched 给证据行，partial 给未满足的原因，
 * 有 Playbook 对应时附一行只读标注（候选/已晋升）。
 */
function setupTooltip(setup: IntradaySetupMatch): string {
  const parts: string[] = [`${setup.label}（${setup.title}）：${setup.reason}`];
  if (setup.evidenceLines.length > 0) parts.push(setup.evidenceLines.join('；'));
  if (setup.playbook) {
    parts.push(
      `对应 Playbook: ${setup.playbook.setupKey}（${
        setup.playbook.status === 'promoted' ? '已晋升' : '候选'
      }）`,
    );
  }
  return parts.join('\n');
}

/** 形态列内容：matched=实底徽标、partial=描边徽标、无相似=—、不可评估=标缺。 */
function setupMatchCell(item: IntradayTopCandidate) {
  const profile = item.setupMatch;
  if (!profile || profile.state === 'unavailable') {
    return (
      <>
        <div className="text-body-sm text-text-3">标缺</div>
        <div className="mt-0.5 text-caption text-text-3">K线/快照输入不足</div>
      </>
    );
  }
  const visible = profile.setups.filter(
    (setup) => setup.state === 'matched' || setup.state === 'partial',
  );
  if (visible.length === 0) {
    return (
      <>
        <div className="text-body-sm text-text-3">—</div>
        <div className="mt-0.5 text-caption text-text-3">无相似形态 · 非信号</div>
      </>
    );
  }
  return (
    <>
      <div className="flex flex-wrap gap-1">
        {visible.map((setup) => (
          <Tooltip
            key={setup.setupKey}
            focusable
            content={
              <span className="whitespace-pre-line">{setupTooltip(setup)}</span>
            }
          >
            <span
              aria-label={setupTooltip(setup)}
              className={
                setup.state === 'matched'
                  ? 'inline-block whitespace-nowrap rounded-ds-sm border border-subtle bg-bg-2 px-1.5 py-0.5 text-caption font-medium text-text-1'
                  : 'inline-block whitespace-nowrap rounded-ds-sm border border-dashed border-subtle px-1.5 py-0.5 text-caption text-text-2'
              }
            >
              {setup.label}
              {setup.state === 'partial' ? ' · 似' : ''}
            </span>
          </Tooltip>
        ))}
      </div>
      <div className="mt-0.5 text-caption text-text-3">v1 几何 · 非信号</div>
    </>
  );
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
            （{data?.signalVersion ?? 'intraday_session_evidence_v4'}）· 不冻结 · 不入统计
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
          <table className="w-full min-w-[1560px] border-collapse" aria-label="日内扫描表">
            <thead>
              <tr className="border-b border-subtle text-left text-caption text-text-3">
                <th className="px-3 py-2 font-medium">标的</th>
                <th className="px-3 py-2 text-right">{sortableHeader('burst', '当前爆发')}</th>
                <th className="px-3 py-2 font-medium">速度</th>
                <th className="px-3 py-2 font-medium">今日波段</th>
                <th className="px-3 py-2 font-medium">形态</th>
                <th className="px-3 py-2 text-right">{sortableHeader('change', '现价 / 当日')}</th>
                <th className="px-3 py-2 text-right">{sortableHeader('gap', '缺口')}</th>
                <th className="px-3 py-2 text-right">{sortableHeader('pace', '量能节奏')}</th>
                <th className="px-3 py-2 font-medium">VWAP</th>
                <th className="px-3 py-2 font-medium">大盘</th>
                <th className="px-3 py-2 text-right">{sortableHeader('expansion', '波幅扩张(ATR)')}</th>
                <th className="px-3 py-2">{sortableHeader('earnings', '财报')}</th>
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
                    <div className={`text-body-sm font-medium ${SPEED_STYLES[item.sessionBursts.speed.state]}`}>
                      {SPEED_LABELS[item.sessionBursts.speed.state]}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">{speedCaption(item)}</div>
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
                  <td className="px-3 py-2.5">{setupMatchCell(item)}</td>
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
                  <td className="px-3 py-2.5">
                    <div className={`text-body-sm ${item.marketAlignment.state === 'unknown' ? 'text-text-3' : 'text-text-1'}`}>
                      {ALIGNMENT_LABELS[item.marketAlignment.state]}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">{alignmentCaption(item)}</div>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-xs text-text-1">{formatRatio(item.atrRangeExpansion)}</div>
                    <div className="mt-0.5 text-caption text-text-3">当日高低 ÷ ATR14</div>
                  </td>
                  <td className="px-3 py-2.5">
                    {(() => {
                      const earnings = earningsLabel(item);
                      return (
                        <>
                          <div
                            className={
                              earnings.emphasized
                                ? 'inline-block rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning'
                                : 'text-body-sm text-text-2'
                            }
                          >
                            {earnings.text}
                          </div>
                          <div className="mt-0.5 text-caption text-text-3">{earnings.caption}</div>
                        </>
                      );
                    })()}
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
                    <td colSpan={15} className="bg-bg-0 px-3 py-3">
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
        当前爆发＝最近 15 分钟（3 根 5m K 线）|收−开| ÷ 当日 5m 波幅中位 × 窗口量比（阈值按 2026-07-31 标注样本校准，盘中排序优先，不是信号）；速度＝相邻两个 15 分钟窗口爆发分之差（5m 近似，非 1m/2m 秒级；减速=你的离场信号，R1）；今日波段＝爆发分 ≥ 8 且起点相隔 ≥30 分钟的独立窗口（≤4 个，休市显示最近一个交易时段）；缺口＝开盘价对参考前收（休市时段改用快照前收并标注）；量能节奏＝当日累计 vs 20 日全日中位（未按时点折算）；大盘＝候选爆发方向 vs SPY 会话 VWAP 位置（累计额/量近似）；波幅扩张＝当日高低价差 ÷ ATR14；财报＝Finnhub 前向 5 天窗口，≤3 天标「期权贵」（你的回避规则）；期权异动＝最近一页 Moomoo 分类计数，不推断开平仓。时段/财报/大盘/速度均为 v3 上下文标注——系统标注，用户过滤：不隐藏行、不阻断操作、不参与排序。形态＝styleMatch v1（S1 低点抬高 / S2 跳空托举 / S3 高开遇阻，与你的 Playbook setup 的形状对比；「· 似」=部分相似，缺 K 线或快照输入时标缺）：形态相似度为 v1 几何检测（5m近似），不含你的进场确认帧（2m/1m 回踩8/13EMA），不是信号。缺失字段显式标缺，不以 0 冒充。
      </div>
    </section>
  );
}

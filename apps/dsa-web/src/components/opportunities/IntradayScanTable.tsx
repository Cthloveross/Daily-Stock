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

/** v3 速度分级：加速↑/减速↓/持平/标缺（5m 窗口近似，减速＝用户 R1 离场提示）。 */
const SPEED_LABELS: Record<IntradayTopCandidate['sessionBursts']['speed']['state'], string> = {
  accelerating: '加速↑',
  decelerating: '减速↓',
  flat: '持平',
  unknown: '标缺',
};

const SPEED_STYLES: Record<IntradayTopCandidate['sessionBursts']['speed']['state'], string> = {
  accelerating: 'text-up-strong',
  decelerating: 'text-down-strong',
  flat: 'text-text-2',
  unknown: 'text-text-3',
};

/** v5 波段分级 chip 文案：强＝爆发分 ≥8 暴动、中＝≥2.5 持续推升。 */
const GRADE_LABELS: Record<'strong' | 'medium', string> = {
  strong: '强',
  medium: '中',
};

const GRADE_DETAIL_LABELS: Record<'strong' | 'medium', string> = {
  strong: '强波段',
  medium: '中波段',
};

/** 强波段＝实底警示底色 chip；中波段/未分级＝描边 chip。 */
const LEG_CHIP_STRONG_CLASS =
  'inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning';
const LEG_CHIP_DEFAULT_CLASS =
  'inline-block whitespace-nowrap rounded-ds-sm border border-subtle px-1.5 py-0.5 text-caption text-text-2';

export type IntradaySortKey = 'burst' | 'change';

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
    default:
      return null;
  }
}

const DIRECTION_ARROWS: Record<'up' | 'down' | 'flat', string> = {
  up: '↑',
  down: '↓',
  flat: '·',
};

/** 盘前闸门口径标注：常规快照字段在盘前仍指向上一常规时段，须如实声明。 */
const PREMARKET_GATE_BASIS = 'premarket_pre_price_change_then_pre_turnover_v1';
const PREMARKET_GATE_CAPTION = '盘前异动排序（盘前价 vs 前收 · 盘前成交额次序）';
const PREMARKET_FIELDS_UNAVAILABLE_WARNING =
  'premarket_fields_unavailable_ranking_reflects_prior_session';
const PREMARKET_FALLBACK_CHIP = '盘前字段不可用 · 当前排序反映上一常规时段';

/** 仅快照次级列表默认只展示前 N 檔，其余一键展开（重排可见性，不删除数据）。 */
const SNAPSHOT_ONLY_COLLAPSED_COUNT = 5;

const RANK_HEADER_TOOLTIP =
  '服务端排名（盘中爆发分优先，休市按最近交易时段证据计数）；点击其他列头做客户端排序时，此排名不变。';

const ALIGNMENT_HEADER_TOOLTIP =
  '顺势/逆势＝候选当前波段（爆发）方向 vs SPY 会话 VWAP 位置，不是个股自身涨跌方向：'
  + '上涨股在 SPY 处于 VWAP 下方时同样标「逆势」。任一侧标缺时显示标缺。';

/**
 * 完整口径原文（逐字保留）：默认收进「完整口径」切换——隐藏 ≠ 删除，
 * 文本本身必须可一键展开查看。
 */
const FULL_METHODOLOGY_TEXT =
  '当前爆发＝最近 15 分钟（3 根 5m K 线）|收−开| ÷ 当日 5m 波幅中位 × 窗口量比（阈值按 2026-07-31 标注样本校准，盘中排序优先，不是信号）；速度＝相邻两个 15 分钟窗口爆发分之差（5m 近似，非 1m/2m 秒级；减速=你的离场信号，R1）；今日波段＝爆发分分级记录的独立窗口（强 ≥8 按 2026-07-31 暴动样本校准、中 ≥2.5 按 2026-08-03 NVDA 上午持续推升校准；起点相隔 ≥30 分钟，≤4 个，休市显示最近一个交易时段）；缺口＝开盘价对参考前收（休市时段改用快照前收并标注）；量能节奏＝当日累计 vs 20 日全日中位（未按时点折算）；大盘＝候选爆发方向 vs SPY 会话 VWAP 位置（累计额/量近似）；波幅扩张＝当日高低价差 ÷ ATR14；财报＝Finnhub 前向 5 天窗口，≤3 天标「期权贵」（你的回避规则）；期权异动＝最近一页 Moomoo 分类计数，不推断开平仓。时段/财报/大盘/速度均为 v3 上下文标注——系统标注，用户过滤：不隐藏行、不阻断操作、不参与排序。形态＝styleMatch v1（S1 低点抬高 / S2 跳空托举 / S3 高开遇阻，与你的 Playbook setup 的形状对比；「· 似」=部分相似，缺 K 线或快照输入时标缺）：形态相似度为 v1 几何检测（5m近似），不含你的进场确认帧（2m/1m 回踩8/13EMA），不是信号。缺失字段显式标缺，不以 0 冒充。';

function formatBurstThrust(value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

function legsDetailLabel(item: IntradayTopCandidate): string | undefined {
  const bursts = item.sessionBursts;
  if (bursts.state !== 'ready' || bursts.legs.length === 0) return undefined;
  return `波段明细：${bursts.legs
    .map(
      (leg) =>
        `${leg.startEt}-${leg.endEt} ${DIRECTION_ARROWS[leg.direction]} `
        + `${formatBurstThrust(leg.thrustPercent)} · 爆发分 ${leg.score?.toFixed(1) ?? '—'}`
        + `${leg.grade ? `（${GRADE_DETAIL_LABELS[leg.grade]}）` : ''}`,
    )
    .join('；')}`;
}

function legsBasisCaption(item: IntradayTopCandidate): string {
  return item.sessionBursts.medianBasis === 'prior_session_fallback'
    ? '基准回退上一时段'
    : '≥30 分钟独立波段';
}

/** 两层模式深度位徽标：计划钉选 or 异动排名（闸门标注，非信号）。 */
function deepLaneBadge(item: IntradayTopCandidate): string | null {
  const reason = item.deepLaneReason;
  if (!reason) return null;
  if (reason.promotedBy === 'plan_always_include') return '计划钉选';
  return reason.moverRank !== null ? `异动 #${reason.moverRank}` : '异动晋升';
}

function optionActivityLabel(item: IntradayTopCandidate): string {
  const activity = item.optionActivity;
  if (activity.state === 'not_configured') return '未配置';
  if (activity.state === 'unavailable') return '标缺';
  if (activity.count === 0) return '0 笔';
  const sentiment = SENTIMENT_LABELS[activity.dominantSentiment] ?? activity.dominantSentiment;
  return `${activity.count} 笔 · ${sentiment} · 最大单 ${formatCompactUsd(activity.maxSingleTurnover)}`;
}

/** v3 财报读数：回避窗内醒目标注「财报 N 天内 · 期权贵」；标缺绝不冒充安全。 */
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

/**
 * 标的格次行的财报警示徽标：回避窗内＝实底警示 chip；日历标缺＝显式
 * 「财报标缺」（未知≠安全，绝不以无徽标冒充安全）；已知且窗外不占默认格，
 * 全文在展开行「研究读数」内一键可达。
 */
function earningsTickerBadge(item: IntradayTopCandidate) {
  const proximity = item.earningsProximity;
  if (proximity.state !== 'ready') {
    const caption = '财报日历不可得 · 未知≠安全';
    return (
      <Tooltip focusable content={caption}>
        <span
          aria-label={caption}
          className="inline-block whitespace-nowrap rounded-ds-sm border border-dashed border-subtle px-1.5 py-0.5 text-caption text-text-3"
        >
          财报标缺
        </span>
      </Tooltip>
    );
  }
  if (!proximity.withinBlackout) return null;
  const earnings = earningsLabel(item);
  return (
    <Tooltip focusable content={earnings.caption}>
      <span
        aria-label={`${earnings.text}：${earnings.caption}`}
        className="inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning"
      >
        {earnings.text}
      </span>
    </Tooltip>
  );
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

function gapCaption(item: IntradayTopCandidate): string {
  if (item.gapPercent === null) return '标缺';
  if (item.gapAtrMultiple !== null) {
    return `${item.gapAtrMultiple.toFixed(2)}×ATR${
      item.gapBasis === 'session_open_vs_moomoo_snapshot_prev_close' ? ' · 快照前收' : ''
    }`;
  }
  return item.gapBasis === 'session_open_vs_moomoo_snapshot_prev_close'
    ? '对快照前收'
    : '对上一完整日收盘';
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
 * 展开行「研究读数」网格：从默认网格移出的次要指标（量能节奏/缺口/波幅/
 * VWAP/期权异动/财报全文/研究状态），一键可达——重排可见性，不删除任何读数；
 * 缺失字段照旧显式标缺，不以 0 冒充。
 */
function ResearchReadoutsGrid({ item }: { item: IntradayTopCandidate }) {
  const earnings = earningsLabel(item);
  return (
    <div aria-label={`${item.ticker} 研究读数`} className="mb-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-body-sm font-medium text-text-2">研究读数</span>
        <span className="text-caption text-text-3">
          次要指标 · 缺失显式标缺，不以 0 冒充
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4 lg:grid-cols-7">
        <div>
          <dt className="text-caption text-text-3">量能节奏</dt>
          <dd className="mt-0.5 font-mono text-mono-xs text-text-1">
            {formatRatio(item.volumePaceRatio)}
          </dd>
          <dd className="text-caption text-text-3">vs 20日全日中位</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">缺口</dt>
          <dd className="mt-0.5 font-mono text-mono-xs text-text-1">
            {formatSignedPercent(item.gapPercent)}
          </dd>
          <dd className="text-caption text-text-3">{gapCaption(item)}</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">波幅扩张(ATR)</dt>
          <dd className="mt-0.5 font-mono text-mono-xs text-text-1">
            {formatRatio(item.atrRangeExpansion)}
          </dd>
          <dd className="text-caption text-text-3">当日高低 ÷ ATR14</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">VWAP</dt>
          <dd className="mt-0.5 text-body-sm text-text-1">{VWAP_LABELS[item.vwapPosition]}</dd>
          <dd className="text-caption text-text-3">
            {item.vwap !== null ? `≈ ${formatPrice(item.vwap)} · 额/量近似` : '额/量近似'}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">期权异动</dt>
          <dd
            className="mt-0.5 text-body-sm text-text-1"
            aria-label={
              item.optionActivity.limitations.length > 0
                ? `期权异动限制：${item.optionActivity.limitations.join('；')}`
                : undefined
            }
          >
            {optionActivityLabel(item)}
          </dd>
          <dd className="text-caption text-text-3">Moomoo 分类 · 不证明方向</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">财报</dt>
          <dd
            className={
              earnings.emphasized
                ? 'mt-0.5 inline-block rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning'
                : 'mt-0.5 text-body-sm text-text-2'
            }
          >
            {earnings.text}
          </dd>
          <dd className="text-caption text-text-3">{earnings.caption}</dd>
        </div>
        <div>
          <dt className="text-caption text-text-3">研究状态</dt>
          <dd className={`mt-0.5 text-body-sm font-medium ${STATE_STYLES[item.researchState]}`}>
            {STATE_LABELS[item.researchState]}
          </dd>
          <dd className="text-caption text-text-3">
            {item.supportingEvidenceCount} 项证据支持
          </dd>
        </div>
      </dl>
    </div>
  );
}

/**
 * 日内扫描表：确定性证据计数排名的盘中滚动 Top N。
 *
 * 两级布局（2026-08-03 声效整理）：默认网格只保留 9 列交易关键读数
 * （排名/标的/涨跌%/当前爆发/今日波段/速度/形态/波段vs大盘/详情），
 * 次要指标移入行内展开区「研究读数」网格（临期合约面板上方）——
 * 重排可见性 ≠ 删除，所有读数一键可达，标缺语义不变。
 *
 * 行点击仍是既有的详情页导航（不改变肌肉记忆），行尾「详情」按钮在行
 * 下方展开研究读数 + 只读临期合约面板；同一时刻只展开一行。
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
  const [snapshotListExpanded, setSnapshotListExpanded] = useState(false);
  const [methodologyExpanded, setMethodologyExpanded] = useState(false);

  // 盘前口径：闸门/宽层排序按盘前字段；回退时携带显式警示，绝不静默。
  const premarketBasis = data?.universeScan?.gateBasis === PREMARKET_GATE_BASIS;
  const premarketFieldsUnavailable = (data?.universeScan?.gateWarnings ?? []).includes(
    PREMARKET_FIELDS_UNAVAILABLE_WARNING,
  );

  // 排名列＝服务端排名（响应内顺序）；客户端排序只重排行，不改写此排名。
  const serverRankByTicker = useMemo(() => {
    const map = new Map<string, number>();
    (data?.candidates ?? []).forEach((item, index) => {
      map.set(item.ticker, index + 1);
    });
    return map;
  }, [data]);

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

  const snapshotRows = data?.universeScan?.snapshotOnly ?? [];
  const visibleSnapshotRows = snapshotListExpanded
    ? snapshotRows
    : snapshotRows.slice(0, SNAPSHOT_ONLY_COLLAPSED_COUNT);

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
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-caption text-text-3">
            <span>
              {data.quoteSessionLabel}
              {data.universeScan
                ? ` · 全清单 ${data.universeScan.scannedTotal} 檔快照 · 深度分析 ${data.universeScan.deepLaneCount} 檔`
                : ` · 扫描 ${data.universe.length} 个标的`}
              {premarketBasis ? ` · ${PREMARKET_GATE_CAPTION}` : ''}
              {data.unsupportedSymbols.length > 0
                ? ` · ${data.unsupportedSymbols.length} 个非美股期权标的未纳入`
                : ''}
            </span>
            {premarketFieldsUnavailable && (
              <span className="inline-block whitespace-nowrap rounded-ds-sm border border-[color:var(--warn-muted)] bg-bg-0 px-1.5 py-0.5 text-caption font-medium text-warning">
                {PREMARKET_FALLBACK_CHIP}
              </span>
            )}
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
          <table className="w-full min-w-[1080px] border-collapse" aria-label="日内扫描表">
            <thead>
              <tr className="border-b border-subtle text-left text-caption text-text-3">
                <th className="px-3 py-2 text-right font-medium">
                  <Tooltip focusable content={RANK_HEADER_TOOLTIP}>
                    <span aria-label={RANK_HEADER_TOOLTIP}>排名</span>
                  </Tooltip>
                </th>
                <th className="px-3 py-2 font-medium">标的</th>
                <th className="px-3 py-2 text-right">{sortableHeader('change', '涨跌%')}</th>
                <th className="px-3 py-2 text-right">{sortableHeader('burst', '当前爆发')}</th>
                <th className="px-3 py-2 font-medium">今日波段</th>
                <th className="px-3 py-2 font-medium">速度</th>
                <th className="px-3 py-2 font-medium">形态</th>
                <th className="px-3 py-2 font-medium">
                  <Tooltip focusable content={ALIGNMENT_HEADER_TOOLTIP}>
                    <span aria-label={ALIGNMENT_HEADER_TOOLTIP}>波段vs大盘</span>
                  </Tooltip>
                </th>
                <th className="px-3 py-2 font-medium">详情</th>
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
                  <td className="px-3 py-2.5 text-right">
                    <div className="font-mono text-mono-sm text-text-2">
                      #{serverRankByTicker.get(item.ticker) ?? '—'}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="font-mono text-mono-sm font-semibold text-text-1">{item.ticker}</div>
                    {(() => {
                      const badge = deepLaneBadge(item);
                      const earningsBadge = earningsTickerBadge(item);
                      if (!badge && !earningsBadge) return null;
                      return (
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {badge && (
                            <span
                              className="inline-block whitespace-nowrap rounded-ds-sm border border-subtle px-1.5 py-0.5 text-caption text-text-2"
                              aria-label={`深度位原因：${badge}（闸门标注，非信号）`}
                            >
                              {badge}
                            </span>
                          )}
                          {earningsBadge}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className={`font-mono text-mono-sm ${
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
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">
                      {formatPrice(item.lastPrice)} · {quoteAsOfLabel(item)}
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
                    {item.sessionBursts.state === 'ready' ? (
                      item.sessionBursts.legs.length > 0 ? (
                        <>
                          <div className="flex flex-wrap gap-1" aria-label={legsDetailLabel(item)}>
                            {item.sessionBursts.legs.map((leg) => (
                              <span
                                key={`${leg.startEt}-${leg.endEt}`}
                                className={
                                  leg.grade === 'strong'
                                    ? LEG_CHIP_STRONG_CLASS
                                    : LEG_CHIP_DEFAULT_CLASS
                                }
                              >
                                {leg.startEt}
                                {DIRECTION_ARROWS[leg.direction]}
                                {leg.grade ? ` ${GRADE_LABELS[leg.grade]}` : ''}
                              </span>
                            ))}
                          </div>
                          <div className="mt-0.5 text-caption text-text-3">{legsBasisCaption(item)}</div>
                        </>
                      ) : (
                        <>
                          <div className="text-body-sm text-text-3">—</div>
                          <div className="mt-0.5 text-caption text-text-3">本时段无记录波段</div>
                        </>
                      )
                    ) : (
                      <>
                        <div className="text-body-sm text-text-3">标缺</div>
                        <div className="mt-0.5 text-caption text-text-3">
                          {item.sessionBursts.state === 'insufficient_bars' ? 'K线不足' : '波段读数不可得'}
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
                  <td className="px-3 py-2.5">{setupMatchCell(item)}</td>
                  <td className="px-3 py-2.5">
                    <div className={`text-body-sm ${item.marketAlignment.state === 'unknown' ? 'text-text-3' : 'text-text-1'}`}>
                      {ALIGNMENT_LABELS[item.marketAlignment.state]}
                    </div>
                    <div className="mt-0.5 text-caption text-text-3">{alignmentCaption(item)}</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <button
                      type="button"
                      onClick={(event) => {
                        // 阻断整行导航：按钮只负责展开/收起研究读数 + 合约面板。
                        event.stopPropagation();
                        setExpandedTicker((current) => (
                          current === item.ticker ? null : item.ticker
                        ));
                      }}
                      className="whitespace-nowrap rounded-ds-sm border border-subtle px-2 py-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
                      aria-expanded={expandedTicker === item.ticker}
                      aria-label={`展开 ${item.ticker} 研究读数与临期合约`}
                    >
                      详情
                      {expandedTicker === item.ticker ? ' ▴' : ' ▾'}
                    </button>
                  </td>
                </tr>
                {expandedTicker === item.ticker && (
                  <tr className="border-b border-subtle last:border-b-0">
                    <td colSpan={9} className="bg-bg-0 px-3 py-3">
                      <ResearchReadoutsGrid item={item} />
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

      {data?.universeScan && (
        <div
          aria-label="仅快照标的"
          className="border-t border-subtle bg-bg-0 px-4 py-3"
        >
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-body-sm font-medium text-text-2">
              仅快照 · 未做深度分析（{data.universeScan.snapshotOnly.length} 檔）
            </span>
            <span className="text-caption text-text-3">
              {premarketBasis ? '按 |盘前涨跌幅| 排序（盘前价 vs 前收）' : '按 |涨跌幅| 排序'}
              {' '}· 闸门之外无爆发/形态/速度读数——缺席即缺席，不以 0 冒充
            </span>
          </div>
          {visibleSnapshotRows.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {visibleSnapshotRows.map((row) => (
                <li
                  key={row.ticker}
                  className="inline-flex items-baseline gap-1.5 rounded-ds-sm border border-subtle bg-bg-1 px-2 py-1"
                >
                  <span className="font-mono text-mono-xs font-medium text-text-1">{row.ticker}</span>
                  {premarketBasis ? (
                    // 盘前口径：只展示真实盘前读数；缺盘前字段显式「盘前标缺」，
                    // 绝不把上一常规时段涨跌冒充成盘前变动。
                    row.preChangePercent !== null && row.preChangePercent !== undefined ? (
                      <>
                        <span
                          className={`font-mono text-mono-xs ${
                            row.preChangePercent > 0
                              ? 'text-up-strong'
                              : row.preChangePercent < 0
                                ? 'text-down-strong'
                                : 'text-text-3'
                          }`}
                        >
                          盘前 {formatSignedPercent(row.preChangePercent)}
                        </span>
                        <span className="font-mono text-mono-xs text-text-3">
                          {row.preTurnover !== null && row.preTurnover !== undefined
                            ? formatCompactUsd(row.preTurnover)
                            : '标缺'}
                        </span>
                      </>
                    ) : (
                      <span className="font-mono text-mono-xs text-text-3">盘前标缺</span>
                    )
                  ) : (
                    <>
                      <span
                        className={`font-mono text-mono-xs ${
                          row.changePercent === null
                            ? 'text-text-3'
                            : row.changePercent > 0
                              ? 'text-up-strong'
                              : row.changePercent < 0
                                ? 'text-down-strong'
                                : 'text-text-3'
                        }`}
                      >
                        {formatSignedPercent(row.changePercent)}
                      </span>
                      <span className="font-mono text-mono-xs text-text-3">
                        {row.turnover !== null ? formatCompactUsd(row.turnover) : '标缺'}
                      </span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
          {snapshotRows.length > SNAPSHOT_ONLY_COLLAPSED_COUNT && (
            <button
              type="button"
              onClick={() => setSnapshotListExpanded((current) => !current)}
              aria-expanded={snapshotListExpanded}
              className="mt-2 rounded-ds-sm border border-subtle px-2 py-1 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
            >
              {snapshotListExpanded
                ? `收起 · 只显示前 ${SNAPSHOT_ONLY_COLLAPSED_COUNT} 檔`
                : `展开全部 ${snapshotRows.length} 檔`}
            </button>
          )}
          {data.universeScan.snapshotUnresolvedSymbols.length > 0 && (
            <div className="mt-2 text-caption text-warning">
              快照未解析 {data.universeScan.snapshotUnresolvedSymbols.length} 檔（供应商无返回行，显式标缺）：
              {data.universeScan.snapshotUnresolvedSymbols.join('、')}
            </div>
          )}
          {data.universeScan.dayPromotionCapReached && (
            <div className="mt-1 text-caption text-warning">
              今日新晋升深度位已达上限（{data.universeScan.dayPromotionCap} 檔 · K 线额度护栏）：
              新异动标的今日仅保留快照行。
            </div>
          )}
          {data.universeScan.watchlistTruncated && (
            <div className="mt-1 text-caption text-warning">
              清单超出服务端上限，超出部分未纳入扫描（配置 {data.universeScan.watchlistTotal} 檔）。
            </div>
          )}
        </div>
      )}

      {data?.universeScan && (
        <div className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
          全清单 {data.universeScan.scannedTotal} 檔快照 · 深度分析前 {data.universeScan.deepLaneMax} 檔
          {premarketBasis
            ? ` · ${PREMARKET_GATE_CAPTION} · 其余仅快照；`
            : '（|涨跌|→成交额）· 其余仅快照；'}
          计划钉选 {data.universeScan.planAlwaysInclude.length} 檔始终占深度位（不占 K 名额）。
          {premarketFieldsUnavailable ? `${PREMARKET_FALLBACK_CHIP}。` : ''}
          闸门为 v1 启发式，晋升不代表方向或质量结论。
        </div>
      )}

      <div className="border-t border-subtle bg-bg-0 px-4 py-2 text-caption text-text-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span>
            排序与口径说明：盘中排序＝爆发分优先（休市按最近交易时段）；今日波段分级
            强＝爆发分 ≥8 暴动 / 中＝≥2.5 持续推升；速度/波段vs大盘/财报/形态均为上下文标注——
            不隐藏行、不参与排序、不是信号；缺失字段显式标缺，不以 0 冒充。
          </span>
          <button
            type="button"
            onClick={() => setMethodologyExpanded((current) => !current)}
            aria-expanded={methodologyExpanded}
            aria-label="展开完整口径说明"
            className="whitespace-nowrap rounded-ds-sm border border-subtle px-2 py-0.5 text-caption text-text-2 hover:bg-bg-2 hover:text-text-1"
          >
            完整口径
            {methodologyExpanded ? ' ▴' : ' ▾'}
          </button>
        </div>
        {methodologyExpanded && <div className="mt-2">{FULL_METHODOLOGY_TEXT}</div>}
      </div>
    </section>
  );
}

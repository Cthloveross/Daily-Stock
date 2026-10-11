/**
 * 订单级分批成交合并（display-only）。
 *
 * 很多交易是“一笔决策、分批成交”：同一个 broker order 会拆成多条 fill
 * evidence。本模块把同一订单的多条 fill 合并成一行展示（方向、总数量、
 * 现金流加权均价、成交时间范围、合计费用、「分 N 笔成交」），但**不改变
 * 底层证据**：每条 fill 仍原样保留、可展开逐笔审阅，合并只发生在展示层。
 *
 * 口径与诚实边界：
 * - 分组键：`evidenceKind === 'fill'` 且 `brokerOrderObservationId` 非空的
 *   证据按订单观察 ID 分组；订单代理证据与无订单关联的证据保持单条展示。
 * - 加权均价 = ∑|allocated_cash_flow| ÷ (∑allocated_quantity × 合约乘数)，
 *   使用 BigInt 十进制精确运算（half-up 保留 4 位），绝不用浮点；任一成员缺
 *   数量或现金流、或总数量/乘数不可用时均价显示为不可得，不做估算。
 * - 合计费用只有在**每条**成员 fill 都有费用证据时才求和；否则标注
 *   「费用不完整」，绝不把部分和冒充总费用。
 * - 任一成员是订单时间代理时，合并行保留「订单时间代理」标注，
 *   不冒充真实逐笔成交。
 *
 * 图表 marker 合并规则（与 `episodeReviewMapping.buildEvidenceMarkers` 配合）：
 * - 合并组默认每单只画一个 marker，锚定在首笔已映射 fill 的 K 线上，
 *   marker id 使用组键，点击选中合并行；
 * - 例外：同一订单的 fill 在当前周期上相距**超过一根 K 线**（分钟线按
 *   bar 间隔秒数，日线及以上按是否落在同一根 bar）时，保留逐笔 marker，
 *   不隐藏真实的时间分散执行。
 */
import type { CandleMarker } from '../../charts/CandlestickChart';
import type { Timeframe } from '../../../types/stockHistory';
import {
  buildEvidenceMarkers,
  chartBarIntervalSeconds,
  type EvidenceChartLink,
} from './episodeReviewMapping';

// ---------------------------------------------------------------------------
// BigInt decimal helpers (exact, no floating point)
// ---------------------------------------------------------------------------

interface ScaledDecimal {
  /** Signed integer units at `scale` decimal places. */
  units: bigint;
  scale: number;
}

const ZERO: ScaledDecimal = { units: 0n, scale: 0 };

function parseDecimal(value?: string | null): ScaledDecimal | null {
  if (value == null) return null;
  const match = /^\s*([+-]?)(\d+)(?:\.(\d+))?\s*$/.exec(value);
  if (!match) return null;
  const [, sign, whole, fraction = ''] = match;
  const units = BigInt(whole + fraction);
  return { units: sign === '-' ? -units : units, scale: fraction.length };
}

function alignedUnits(a: ScaledDecimal, b: ScaledDecimal): [bigint, bigint, number] {
  const scale = Math.max(a.scale, b.scale);
  return [
    a.units * 10n ** BigInt(scale - a.scale),
    b.units * 10n ** BigInt(scale - b.scale),
    scale,
  ];
}

function addDecimal(a: ScaledDecimal, b: ScaledDecimal): ScaledDecimal {
  const [left, right, scale] = alignedUnits(a, b);
  return { units: left + right, scale };
}

function absDecimal(a: ScaledDecimal): ScaledDecimal {
  return { units: a.units < 0n ? -a.units : a.units, scale: a.scale };
}

function multiplyDecimal(a: ScaledDecimal, b: ScaledDecimal): ScaledDecimal {
  return { units: a.units * b.units, scale: a.scale + b.scale };
}

function formatScaled(a: ScaledDecimal): string {
  const negative = a.units < 0n;
  const digits = (negative ? -a.units : a.units).toString().padStart(a.scale + 1, '0');
  const whole = digits.slice(0, digits.length - a.scale) || '0';
  const fraction = a.scale > 0 ? digits.slice(digits.length - a.scale) : '';
  const trimmedFraction = fraction.replace(/0+$/, '');
  return `${negative ? '-' : ''}${whole}${trimmedFraction ? `.${trimmedFraction}` : ''}`;
}

/** Exact decimal division rounded half-up to `scale` places; null when dividing by zero. */
function divideToScale(
  numerator: ScaledDecimal,
  denominator: ScaledDecimal,
  scale: number,
): string | null {
  if (denominator.units === 0n) return null;
  const negative = (numerator.units < 0n) !== (denominator.units < 0n);
  const n = numerator.units < 0n ? -numerator.units : numerator.units;
  const d = denominator.units < 0n ? -denominator.units : denominator.units;
  const scaledNumerator = n * 10n ** BigInt(denominator.scale + scale);
  const scaledDenominator = d * 10n ** BigInt(numerator.scale);
  const quotient = (scaledNumerator * 2n + scaledDenominator) / (scaledDenominator * 2n);
  return formatScaled({ units: negative ? -quotient : quotient, scale });
}

// ---------------------------------------------------------------------------
// Order-level grouping
// ---------------------------------------------------------------------------

export interface EvidenceOrderGroupAggregate {
  /** ∑分配数量；任一成员缺数量证据时为 null。 */
  totalQuantity: string | null;
  /**
   * 现金流加权均价 = ∑|分配现金流| ÷ (∑数量 × 合约乘数)，half-up 保留 4 位。
   * 缺少任一输入时为 null（显示为不可得，不估算）。
   */
  weightedAveragePrice: string | null;
  /** 仅当每条成员 fill 都有费用证据时的 ∑费用；否则 null（费用不完整）。 */
  totalFee: string | null;
  feeComplete: boolean;
  firstEvidenceTime: string;
  lastEvidenceTime: string;
}

export interface EvidenceOrderGroup {
  /** 合并组为 `order:{brokerOrderObservationId}`；单条证据为其 evidenceKey。 */
  groupKey: string;
  /** 成员按原始证据顺序保留，逐笔细节完整可审计。 */
  links: EvidenceChartLink[];
  /** true = 同一订单的多笔 fill 已合并展示。 */
  consolidated: boolean;
  /** 任一成员为订单时间代理时为 true（合并行保留代理标注）。 */
  orderTimeProxy: boolean;
  /** 成员方向一致时取该方向；混合或未知时为 'unknown'。 */
  cashFlowSide: 'buy' | 'sell' | 'unknown';
  /** 成员事件角色去重（如开仓+加仓）。 */
  eventRoles: string[];
  /** marker 锚点：首个已映射到 K 线的成员时间；全部未映射时为 null。 */
  chartTime: number | string | null;
  /** 成员 fill 在当前周期上相距超过一根 K 线时为 true（保留逐笔 marker）。 */
  chartDispersed: boolean;
  /** 仅合并组携带聚合口径；单条证据保持原样展示。 */
  aggregate: EvidenceOrderGroupAggregate | null;
}

function toUnixSeconds(value: number | string): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
}

function groupCashFlowSide(links: EvidenceChartLink[]): 'buy' | 'sell' | 'unknown' {
  const sides = new Set(links.map((link) => link.cashFlowSide));
  if (sides.size === 1) {
    const [only] = sides;
    return only;
  }
  return 'unknown';
}

function groupChartDispersed(links: EvidenceChartLink[], timeframe: Timeframe): boolean {
  const mapped = links
    .map((link) => link.chartTime)
    .filter((time): time is number | string => time != null);
  if (mapped.length <= 1) return false;
  const interval = chartBarIntervalSeconds(timeframe);
  if (interval == null) {
    // Calendar-bucket timeframes (daily/weekly/monthly): only same-bar fills merge.
    return new Set(mapped.map((time) => String(time))).size > 1;
  }
  const seconds = mapped
    .map((time) => toUnixSeconds(time))
    .filter((value): value is number => value != null);
  if (seconds.length <= 1) return false;
  return Math.max(...seconds) - Math.min(...seconds) > interval;
}

function groupAggregate(
  links: EvidenceChartLink[],
  contractMultiplier?: string | null,
): EvidenceOrderGroupAggregate {
  let quantity = ZERO;
  let cashAbs = ZERO;
  let fee = ZERO;
  let quantityComplete = true;
  let cashComplete = true;
  let feeComplete = true;
  for (const link of links) {
    const q = parseDecimal(link.evidence.allocatedQuantity);
    if (q == null) quantityComplete = false;
    else quantity = addDecimal(quantity, absDecimal(q));
    const c = parseDecimal(link.evidence.allocatedCashFlow);
    if (c == null) cashComplete = false;
    else cashAbs = addDecimal(cashAbs, absDecimal(c));
    const f = parseDecimal(link.evidence.allocatedFee);
    if (f == null) feeComplete = false;
    else fee = addDecimal(fee, f);
  }
  const multiplier = parseDecimal(contractMultiplier);
  const weightedAveragePrice = quantityComplete
    && cashComplete
    && quantity.units > 0n
    && multiplier != null
    && multiplier.units > 0n
    ? divideToScale(cashAbs, multiplyDecimal(quantity, multiplier), 4)
    : null;
  return {
    totalQuantity: quantityComplete ? formatScaled(quantity) : null,
    weightedAveragePrice,
    totalFee: feeComplete ? formatScaled(fee) : null,
    feeComplete,
    firstEvidenceTime: links[0].evidence.evidenceTime,
    lastEvidenceTime: links[links.length - 1].evidence.evidenceTime,
  };
}

/**
 * 把 evidence-chart links 按 broker order 合并为展示组。
 *
 * 只有 `evidenceKind === 'fill'` 且带 `brokerOrderObservationId` 的证据参与
 * 合并；组顺序按首个成员在原始证据序列中的位置保留。单成员组按原样
 * （非合并）展示。
 */
export function consolidateEvidenceLinks(
  links: EvidenceChartLink[],
  timeframe: Timeframe,
  contractMultiplier?: string | null,
): EvidenceOrderGroup[] {
  const grouped = new Map<string, EvidenceChartLink[]>();
  for (const link of links) {
    const orderId = link.evidence.brokerOrderObservationId
      ?? link.evidence.parentBrokerOrderObservationId;
    const key = link.evidence.evidenceKind === 'fill' && orderId != null
      ? `order:${orderId}`
      : link.evidence.evidenceKey;
    const bucket = grouped.get(key);
    if (bucket) bucket.push(link);
    else grouped.set(key, [link]);
  }
  return [...grouped.entries()].map(([groupKey, members]) => {
    const consolidated = members.length > 1;
    return {
      groupKey,
      links: members,
      consolidated,
      orderTimeProxy: members.some((link) => link.orderTimeProxy),
      cashFlowSide: groupCashFlowSide(members),
      eventRoles: [...new Set(members.map((link) => link.evidence.eventRole))],
      chartTime: members.find((link) => link.chartTime != null)?.chartTime ?? null,
      chartDispersed: consolidated ? groupChartDispersed(members, timeframe) : false,
      aggregate: consolidated ? groupAggregate(members, contractMultiplier) : null,
    } satisfies EvidenceOrderGroup;
  });
}

/**
 * 每单一个 marker：合并组画在首笔已映射 fill 的 K 线上（id=组键，文案带
 * ×N），点击选中合并行；成员相距超过一根 K 线（`chartDispersed`）或非合并
 * 组时保留逐笔 marker，不隐藏真实的时间分散执行。
 */
export function buildOrderAwareEvidenceMarkers(
  groups: EvidenceOrderGroup[],
  selectedEvidenceKey?: string | null,
): CandleMarker[] {
  const markers: CandleMarker[] = [];
  for (const group of groups) {
    if (!group.consolidated || group.chartDispersed) {
      markers.push(...buildEvidenceMarkers(group.links, selectedEvidenceKey));
      continue;
    }
    if (group.chartTime == null) continue;
    const selected = group.links.some(
      (link) => link.evidence.evidenceKey === selectedEvidenceKey,
    );
    const buy = group.cashFlowSide === 'buy';
    const sell = group.cashFlowSide === 'sell';
    const label = `${buy ? '买' : sell ? '卖' : '方向未知'}×${group.links.length}`;
    markers.push({
      id: group.groupKey,
      time: group.chartTime,
      position: sell ? 'aboveBar' : buy ? 'belowBar' : 'inBar',
      shape: group.orderTimeProxy ? 'square' : sell ? 'arrowDown' : buy ? 'arrowUp' : 'circle',
      color: selected ? '#f5d06f' : sell ? '#f85149' : buy ? '#22d3ee' : '#8a8a94',
      text: group.orderTimeProxy ? `代理·${label}` : label,
      size: selected ? 2 : 1.5,
    } satisfies CandleMarker);
  }
  return markers.sort((a, b) => {
    const left = toUnixSeconds(a.time) ?? 0;
    const right = toUnixSeconds(b.time) ?? 0;
    return left - right;
  });
}

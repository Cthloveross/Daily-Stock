import type { OpportunityCandidate } from '../../types/opportunities';

export interface FrozenPlanLevels {
  direction: 'bullish' | 'bearish' | 'mixed';
  confirmLevel: number | null;
  confirmLabel: string;
  invalidationLevel: number | null;
  invalidationLabel: string;
}

function numericField(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function evidenceObject(
  candidate: OpportunityCandidate,
  metric: string,
): Record<string, unknown> | null {
  const value = candidate.evidence.find((item) => item.metric === metric)?.value;
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * 从冻结候选的结构化证据里取「研究用途」句子的同一数值锚点：
 * 看多＝前 20 日高（跌回其下方失效，退化用 EMA13）；
 * 看空＝前 20 日低（重回其上方失效，退化用 EMA13）；
 * 方向混合/未知没有单一触发价，显式标缺而不是猜一个。
 * 只读取候选 evidence（prior_20d_range_position / ema8_ema13_alignment），
 * 不解析展示字符串，也不改变盘前排名。
 */
export function deriveFrozenPlanLevels(candidate: OpportunityCandidate): FrozenPlanLevels {
  const range = evidenceObject(candidate, 'prior_20d_range_position');
  const ema = evidenceObject(candidate, 'ema8_ema13_alignment');
  const priorHigh = numericField(range?.priorHigh);
  const priorLow = numericField(range?.priorLow);
  const ema13 = numericField(ema?.ema13);

  if (candidate.directionalContext === 'bullish') {
    return {
      direction: 'bullish',
      confirmLevel: priorHigh,
      confirmLabel: priorHigh !== null ? '前20日高' : '结构确认价标缺',
      invalidationLevel: priorHigh ?? ema13,
      invalidationLabel: priorHigh !== null
        ? '跌回前20日高下方'
        : ema13 !== null
          ? '跌破 EMA13'
          : '失效价标缺',
    };
  }
  if (candidate.directionalContext === 'bearish') {
    return {
      direction: 'bearish',
      confirmLevel: priorLow,
      confirmLabel: priorLow !== null ? '前20日低' : '结构确认价标缺',
      invalidationLevel: priorLow ?? ema13,
      invalidationLabel: priorLow !== null
        ? '重回前20日低上方'
        : ema13 !== null
          ? '站回 EMA13'
          : '失效价标缺',
    };
  }
  return {
    direction: 'mixed',
    confirmLevel: null,
    confirmLabel: '方向未定',
    invalidationLevel: null,
    invalidationLabel: '方向未定',
  };
}

export interface PlanDistance {
  dollars: number | null;
  atrMultiple: number | null;
}

/**
 * 距离符号约定（沿交易方向）：
 * 距确认位＞0＝尚未触发（还差这么多），＜0＝已越过确认价；
 * 距失效位＞0＝仍有缓冲，＜0＝已跌破/站上失效价。
 * ATR 倍数＝美元距离 ÷ ATR14（Wilder，日线）；ATR 缺失时只给美元距离。
 */
export function computePlanDistances(
  levels: FrozenPlanLevels,
  lastPrice: number | null,
  atr14: number | null,
): { confirm: PlanDistance; invalidation: PlanDistance } {
  const sign = levels.direction === 'bearish' ? -1 : 1;
  const distance = (level: number | null, towardLevel: boolean): PlanDistance => {
    if (level === null || lastPrice === null || levels.direction === 'mixed') {
      return { dollars: null, atrMultiple: null };
    }
    const dollars = towardLevel
      ? sign * (level - lastPrice)
      : sign * (lastPrice - level);
    return {
      dollars,
      atrMultiple: atr14 !== null && atr14 > 0 ? dollars / atr14 : null,
    };
  };
  return {
    confirm: distance(levels.confirmLevel, true),
    invalidation: distance(levels.invalidationLevel, false),
  };
}

export function formatSignedDollars(value: number | null): string {
  if (value === null) return '标缺';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}$${Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatAtrMultiple(value: number | null): string {
  if (value === null) return 'ATR 标缺';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 1 })} ATR`;
}

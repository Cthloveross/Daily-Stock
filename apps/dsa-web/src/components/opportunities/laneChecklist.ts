import type {
  IntradayEarningsProximity,
  IntradayExecutionPolicy,
  IntradayLaneAvailability,
  IntradayTopCandidate,
} from '../../types/opportunities';
import { parseApiTimestamp } from '../../utils/marketTime';

/**
 * INTRA-0-7/v1 的前端纯判定层。
 *
 * 活动车道只有一条：0..7DTE 均可进入，但所有仓位必须在同一 ET 交易日平仓。
 * 服务端新 policy 字段是第一真源；旧载荷只从逐标的到期日列表安全推导，旧
 * `day_type` 仅用于兼容说明，绝不能再把「没有 0DTE」解释成车道关闭。
 */

export type LaneId = 'intra_0_7';

/** `neutral` 表示纯描述读数，既不是符合也不是不符合。 */
export type CheckStatus = 'pass' | 'fail' | 'missing' | 'requirement' | 'neutral';

export interface LaneCheck {
  id: string;
  label: string;
  status: CheckStatus;
  reason: string;
}

export interface LaneHardBlock {
  id: string;
  ruleId: string;
  reason: string;
}

export interface LaneChecklistInput {
  lane: LaneId;
  /** 用户填写的进场 DTE；未填写时显示固定要求，不当作缺失数据。 */
  dte: number | null;
  pulseGeneratedAt: string | null;
  executionPolicy?: IntradayExecutionPolicy | null;
  nowMs?: number;
  laneAvailability: IntradayLaneAvailability | null;
  loading?: boolean;
}

export type LaneDayTypeView = 'available' | 'unavailable' | 'unknown' | 'loading';

export interface LaneDayTypeReading {
  state: LaneDayTypeView;
  text: string;
  tooltip: string;
  /** 当前规则下确证至少有一个 0..7DTE 到期的标的。 */
  eligibleTickers: string[];
  /** 兼容展示：旧接口确证有 0DTE 的标的，不再决定车道开关。 */
  zeroDteTickers: string[];
  /** 判定真源，便于测试锁定“新 policy 优先”。 */
  source: 'policy_v2' | 'derived_tickers' | 'legacy_day_type' | 'missing';
}

export interface LaneChecklistResult {
  lane: LaneId;
  etHour: number | null;
  etClock: string | null;
  pulseStaleMinutes: number | null;
  dayType: LaneDayTypeReading;
  checks: LaneCheck[];
  hardBlocks: LaneHardBlock[];
  reminders: string[];
}

export const ACTIVE_DTE_MIN = 0;
export const ACTIVE_DTE_MAX = 7;
export const PULSE_STALE_AFTER_MS = 5 * 60 * 1000;

export const LANE_LABELS: Record<LaneId, string> = {
  intra_0_7: 'INTRA-0-7',
};

const POLICY_VERSION = 'INTRA-0-7/v1';
const HOLDING_POLICY = 'same_et_day_only';

function normalizeTickers(values: readonly string[] | null | undefined): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of values ?? []) {
    const ticker = raw.trim().toUpperCase();
    if (!ticker || seen.has(ticker)) continue;
    seen.add(ticker);
    result.push(ticker);
  }
  return result;
}

function policyTooltip(lines: string[]): string {
  return [
    `规则：${POLICY_VERSION} · DTE ${ACTIVE_DTE_MIN}–${ACTIVE_DTE_MAX}`,
    `持仓：${HOLDING_POLICY}（必须同一 ET 交易日平仓）`,
    ...lines,
  ].join('\n');
}

function scopeLine(availability: IntradayLaneAvailability): string {
  return `判定范围：今日深度层 ${availability.checkedCount} 个标的`
    + `（可读 ${availability.readableCount} / 读不到 ${availability.unavailableCount}），`
    + '依据当日真实期权到期日，不是星期规则。';
}

/** 活动车道只在确证可用时返回；未知/不可用都不猜。 */
export function laneForDayType(dayType: LaneDayTypeReading): LaneId | null {
  return dayType.state === 'available' ? 'intra_0_7' : null;
}

/** 名称为兼容旧调用；现在只表示整个 0..7DTE 窗口确证不可用。 */
export function isIntradayLaneClosed(dayType: LaneDayTypeReading): boolean {
  return dayType.state === 'unavailable';
}

/**
 * 新 policy 字段优先；缺席时从逐标的 0..7DTE 元数据安全推导。
 * legacy `overnight_only` 只代表没有 0DTE，不能推出 1..7DTE 也没有。
 */
export function evaluateLaneDayType(
  availability: IntradayLaneAvailability | null | undefined,
  options: { loading?: boolean } = {},
): LaneDayTypeReading {
  if (!availability && options.loading) {
    return {
      state: 'loading',
      text: 'INTRA-0-7 可用性读取中…',
      tooltip: policyTooltip(['首轮盘中扫描仍在进行；读取中不等于不可用。']),
      eligibleTickers: [],
      zeroDteTickers: [],
      source: 'missing',
    };
  }
  if (!availability) {
    return {
      state: 'unknown',
      text: '今日：INTRA-0-7 可用性标缺',
      tooltip: policyTooltip([
        '扫描未返回车道可用性区块；未知不等于没有 0..7DTE。',
        '开仓前需核对券商合约列表。',
      ]),
      eligibleTickers: [],
      zeroDteTickers: [],
      source: 'missing',
    };
  }

  const zeroDteTickers = normalizeTickers(availability.zeroDteTickers);
  const policyState = availability.policyState;
  if (policyState === 'available' || policyState === 'unavailable' || policyState === 'unknown') {
    const eligibleTickers = normalizeTickers(availability.eligibleTickers);
    const reason = availability.policyStateReason?.trim() || '服务端未提供补充原因';
    const version = availability.policyVersion?.trim() || POLICY_VERSION;
    const holding = availability.holdingPolicy?.trim() || HOLDING_POLICY;
    const minDte = Number.isFinite(availability.eligibleDteMin)
      ? availability.eligibleDteMin
      : ACTIVE_DTE_MIN;
    const maxDte = Number.isFinite(availability.eligibleDteMax)
      ? availability.eligibleDteMax
      : ACTIVE_DTE_MAX;
    if (policyState === 'available') {
      return {
        state: 'available',
        text: eligibleTickers.length > 0
          ? `今日：INTRA-0-7 可用（${eligibleTickers.join('/')} 有 0–7DTE）`
          : '今日：INTRA-0-7 可用',
        tooltip: policyTooltip([
          `服务端 policy：${version} · ${holding} · DTE ${minDte}–${maxDte}。`,
          reason,
          scopeLine(availability),
        ]),
        eligibleTickers,
        zeroDteTickers,
        source: 'policy_v2',
      };
    }
    if (policyState === 'unavailable') {
      return {
        state: 'unavailable',
        text: '今日：0–7DTE 均不可用 · INTRA-0-7 暂停',
        tooltip: policyTooltip([
          `服务端 policy：${version} · ${holding} · DTE ${minDte}–${maxDte}。`,
          reason,
          scopeLine(availability),
        ]),
        eligibleTickers: [],
        zeroDteTickers,
        source: 'policy_v2',
      };
    }
    return {
      state: 'unknown',
      text: '今日：INTRA-0-7 可用性标缺',
      tooltip: policyTooltip([
        `服务端 policy：${version} · ${holding} · DTE ${minDte}–${maxDte}。`,
        reason,
        '未知不等于没有 0..7DTE，也不自动放行。',
        scopeLine(availability),
      ]),
      eligibleTickers: [],
      zeroDteTickers,
      source: 'policy_v2',
    };
  }

  const rows = availability.tickers ?? [];
  const eligibleTickers = normalizeTickers(rows
    .filter((row) => {
      if (row.state !== 'ready') return false;
      if (typeof row.hasEligible0_7 === 'boolean') return row.hasEligible0_7;
      return (row.availableDteList ?? []).some(
        (dte) => Number.isInteger(dte) && dte >= ACTIVE_DTE_MIN && dte <= ACTIVE_DTE_MAX,
      );
    })
    .map((row) => row.ticker));
  if (eligibleTickers.length > 0) {
    return {
      state: 'available',
      text: `今日：INTRA-0-7 可用（${eligibleTickers.join('/')} 有 0–7DTE）`,
      tooltip: policyTooltip([
        '新 policy 字段缺席；已从逐标的 available_dte_list 0..7 安全推导。',
        availability.dayType === 'overnight_only'
          ? '兼容说明：旧 day_type=overnight_only 只表示无 0DTE，不影响 1–7DTE 可用性。'
          : `兼容说明：旧 day_type=${availability.dayType}。`,
        scopeLine(availability),
      ]),
      eligibleTickers,
      zeroDteTickers,
      source: 'derived_tickers',
    };
  }

  const deferred = normalizeTickers(availability.deferredTickers);
  const allRowsReadable = rows.length > 0
    && rows.every((row) => row.state === 'ready')
    && deferred.length === 0;
  if (allRowsReadable) {
    return {
      state: 'unavailable',
      text: '今日：0–7DTE 均不可用 · INTRA-0-7 暂停',
      tooltip: policyTooltip([
        '新 policy 字段缺席；逐标的到期日均已读完，未发现任何 0..7DTE。',
        scopeLine(availability),
      ]),
      eligibleTickers: [],
      zeroDteTickers,
      source: 'derived_tickers',
    };
  }

  // 旧接口若确证有 0DTE，仍是 0..7DTE 的充分正证据；其余 legacy day_type
  // 都不能安全推出新规则的 available/unavailable。
  if (zeroDteTickers.length > 0) {
    return {
      state: 'available',
      text: `今日：INTRA-0-7 可用（${zeroDteTickers.join('/')} 有 0DTE）`,
      tooltip: policyTooltip([
        '兼容旧接口：zero_dte_tickers 是 0..7DTE 可用的充分正证据。',
        scopeLine(availability),
      ]),
      eligibleTickers: zeroDteTickers,
      zeroDteTickers,
      source: 'legacy_day_type',
    };
  }

  return {
    state: 'unknown',
    text: '今日：INTRA-0-7 可用性标缺',
    tooltip: policyTooltip([
      availability.dayType === 'overnight_only'
        ? '兼容说明：旧 day_type=overnight_only 只表示无 0DTE；不能据此关闭 0..7DTE 车道。'
        : `兼容说明：旧 day_type=${availability.dayType}，不足以判定新规则。`,
      availability.dayTypeReason,
      deferred.length > 0 ? `本轮未查：${deferred.join('、')}。` : '',
      '未知不等于没有 0..7DTE，也不自动放行。',
      scopeLine(availability),
    ].filter(Boolean)),
    eligibleTickers: [],
    zeroDteTickers,
    source: 'legacy_day_type',
  };
}

/** ET 小时：与市场脉搏使用同一 America/New_York 时钟源。 */
export function etHourFromPulse(generatedAt: string | null): number | null {
  const minuteOfDay = etMinuteOfDayFromPulse(generatedAt);
  return minuteOfDay === null ? null : Math.floor(minuteOfDay / 60);
}

/** ET 当日分钟数：时间边界必须按分钟判定，不能把 14:29 与 14:30 混为一档。 */
export function etMinuteOfDayFromPulse(generatedAt: string | null): number | null {
  const parsed = parseApiTimestamp(generatedAt);
  if (!parsed) return null;
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(parsed);
  const hour = Number(parts.find((part) => part.type === 'hour')?.value);
  const minute = Number(parts.find((part) => part.type === 'minute')?.value);
  return Number.isFinite(hour) && Number.isFinite(minute) ? hour * 60 + minute : null;
}

export function etClockFromPulse(generatedAt: string | null): string | null {
  const parsed = parseApiTimestamp(generatedAt);
  if (!parsed) return null;
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(parsed);
}

/** 深度层候选里查这个标的的财报邻近度；不在深度层＝查不到，不是安全。 */
function findEarnings(
  ticker: string | null,
  candidates: IntradayTopCandidate[],
): IntradayEarningsProximity | null {
  if (!ticker) return null;
  const wanted = ticker.trim().toUpperCase();
  if (!wanted) return null;
  return candidates.find((item) => item.ticker.trim().toUpperCase() === wanted)
    ?.earningsProximity ?? null;
}

export function dteRequirementText(): string {
  return 'INTRA-0-7：只允许 0–7DTE；所有合约必须在同一 ET 交易日平仓';
}

function dteCheck(dte: number | null): LaneCheck {
  if (dte === null) {
    return {
      id: 'dte',
      label: '合约期限',
      status: 'requirement',
      reason: dteRequirementText(),
    };
  }
  if (Number.isInteger(dte) && dte >= ACTIVE_DTE_MIN && dte <= ACTIVE_DTE_MAX) {
    return {
      id: 'dte',
      label: '合约期限',
      status: 'pass',
      reason: `INTRA-0-7：当前 ${dte}DTE 在 0–7DTE 合规窗口内`,
    };
  }
  return {
    id: 'dte',
    label: '合约期限',
    status: 'fail',
    reason: `INTRA-0-7：当前 ${dte}DTE 超出 0–7DTE 合规窗口`,
  };
}

function holdingCheck(): LaneCheck {
  return {
    id: 'holding',
    label: '持仓边界',
    status: 'requirement',
    reason: '固定要求：所有 0–7DTE 仓位必须在同一 ET 交易日平仓，不留过夜仓位',
  };
}

function clockCheck(
  dte: number | null,
  policy: IntradayExecutionPolicy | null | undefined,
  loading: boolean,
  nowMs: number,
): LaneCheck {
  const row = (status: CheckStatus, reason: string): LaneCheck => ({
    id: 'clock', label: '开仓时点', status, reason,
  });
  if (!policy || policy.policyVersion !== 'intraday_execution_policy_v1') {
    return row(loading ? 'requirement' : 'missing', loading
      ? '服务端交易日与执行窗口读取中…'
      : '服务端交易日与执行窗口不可得，不能确认当前可开仓');
  }
  const asOf = parseApiTimestamp(policy.asOf)?.getTime();
  const expiresAt = parseApiTimestamp(policy.expiresAt)?.getTime();
  if (asOf === undefined || expiresAt === undefined || expiresAt <= asOf
    || asOf > nowMs + 30_000 || nowMs >= expiresAt || nowMs - asOf > 120_000) {
    return row('missing', '服务端执行窗口已过时或时间无效，等待刷新；不沿用旧许可');
  }
  if (policy.state === 'unavailable') {
    return row('missing', `交易日历不可确认：${policy.reason}（未知≠允许）`);
  }
  if (policy.state === 'closed') {
    return row('fail', `当前不是可执行交易时段：${policy.reason}`);
  }
  if (policy.state !== 'ready') return row('missing', '服务端执行状态无法识别');
  if (dte !== null && (!Number.isInteger(dte) || dte < ACTIVE_DTE_MIN || dte > ACTIVE_DTE_MAX)) {
    return row('requirement', '先选择 0–7DTE 合规合约，再核对开仓时点');
  }
  const allowed = dte === 0 ? policy.zeroDteEntryAllowed : policy.ordinaryEntryAllowed;
  if (typeof allowed !== 'boolean' || (dte === null && typeof policy.zeroDteEntryAllowed !== 'boolean')) {
    return row('missing', '服务端未给出执行窗口许可，不能确认当前可开仓');
  }
  const cutoff = dte === 0 ? policy.zeroDteEntryCutoffEt : policy.ordinaryEntryCutoffEt;
  const windowLabel = `${policy.entryStartEt ?? '—'}≤ET<${cutoff ?? '—'}`;
  const closeLabel = `今日实际收盘 ${policy.closeEt ?? '—'} ET`;
  if (!allowed) return row('fail', `不在${dte === null ? '普通候选' : `${dte}DTE`}开仓窗口 ${windowLabel}；${closeLabel}`);
  if (dte === null && policy.zeroDteEntryAllowed === false) {
    return row('requirement', `1–7DTE 窗口 ${windowLabel}；0DTE 当前不在执行窗口（截止 ${policy.zeroDteEntryCutoffEt ?? '—'}）；${closeLabel}`);
  }
  return row('pass', `服务端确认${dte === null ? '0–7DTE' : `${dte}DTE`}时窗符合 ${windowLabel}；${closeLabel}。仅时窗符合，不是执行许可`);
}

/** 财报回避检查：查不到就标缺，绝不以「无徽标」冒充安全。 */
export function earningsCheck(
  ticker: string | null,
  candidates: IntradayTopCandidate[],
): LaneCheck {
  if (!ticker || !ticker.trim()) {
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'missing',
      reason: '未填写标的，今日财报状态无法核对（未知≠安全）',
    };
  }
  const symbol = ticker.trim().toUpperCase();
  const proximity = findEarnings(symbol, candidates);
  if (!proximity) {
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'missing',
      reason: `${symbol} 不在今日深度扫描层，没有可复用的财报日历读数（未知≠安全）`,
    };
  }
  if (proximity.state !== 'ready') {
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'missing',
      reason: proximity.unavailableReason
        ? `财报日历不可得：${proximity.unavailableReason}（未知≠安全）`
        : '财报日历不可得（未知≠安全）',
    };
  }
  if (proximity.daysToEarnings === 0) {
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'fail',
      reason: `${symbol} 今日财报 · INTRA-0-7 当日交易会跨财报事件，硬拦截`,
    };
  }
  return {
    id: 'earnings',
    label: '财报回避',
    status: 'pass',
    reason: proximity.daysToEarnings === null
      ? `${symbol} 前向 ${proximity.windowDays} 天内无财报`
      : `${symbol} 距财报 ${proximity.daysToEarnings} 天；INTRA-0-7 必须当日平仓，不跨财报事件`,
  };
}

function hardBlocks(
  input: LaneChecklistInput,
  clock: LaneCheck,
  policy: LaneDayTypeReading,
): LaneHardBlock[] {
  const blocks: LaneHardBlock[] = [];
  if (policy.state === 'unavailable') {
    blocks.push({
      id: 'policy_unavailable',
      ruleId: POLICY_VERSION,
      reason: '已确证今日扫描范围内没有任何 0–7DTE 到期，INTRA-0-7 暂停',
    });
  }
  if (clock.status === 'fail') {
    blocks.push({
      id: input.dte === 0 && input.executionPolicy?.ordinaryEntryAllowed === true
        ? 'late_0dte' : 'outside_entry_window',
      ruleId: POLICY_VERSION,
      reason: clock.reason,
    });
  }
  return blocks;
}

/** 按唯一活动车道给出逐条机械核对。 */
export function evaluateLaneChecklist(input: LaneChecklistInput): LaneChecklistResult {
  const clockTimestamp = input.executionPolicy?.asOf ?? input.pulseGeneratedAt;
  const etHour = etHourFromPulse(clockTimestamp);
  const etClock = etClockFromPulse(clockTimestamp);
  const loading = Boolean(input.loading);
  const dayType = evaluateLaneDayType(input.laneAvailability, { loading });
  const nowMs = input.nowMs ?? Date.now();
  const parsed = parseApiTimestamp(clockTimestamp);
  const ageMs = parsed ? nowMs - parsed.getTime() : null;
  const pulseStaleMinutes = ageMs !== null && ageMs > PULSE_STALE_AFTER_MS
    ? Math.round(ageMs / 60_000)
    : null;

  const clock = clockCheck(input.dte, input.executionPolicy, loading, nowMs);
  return {
    lane: input.lane,
    etHour,
    etClock,
    pulseStaleMinutes,
    dayType,
    checks: [
      dteCheck(input.dte),
      holdingCheck(),
      clock,
    ],
    hardBlocks: hardBlocks(input, clock, dayType),
    reminders: [
      'INTRA-0-7：0–7DTE 均可；所有仓位必须在同一 ET 交易日平仓，不留过夜仓位',
    ],
  };
}

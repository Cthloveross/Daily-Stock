import type {
  IntradayEarningsProximity,
  IntradayTopCandidate,
} from '../../types/opportunities';
import type { RuleComplianceDailyBudget } from '../../types/journal';
import { parseApiTimestamp } from '../../utils/marketTime';

/**
 * 开仓前车道检查清单的纯判定层（组件只负责渲染，判定全部在这里，便于逐条测试）。
 *
 * 这是**对用户自己那套规则**（Playbook 候选「V2-0」…「V2-D」）的对照检查，
 * 不是推荐、不是信号、不含概率，也永远不会下单——本系统只读。
 *
 * 每条检查只有三种结果：`pass` / `fail` / `missing`（标缺）。缺输入一律 `missing`
 * 并给出原因：**未知不等于安全**，绝不以「没查到」冒充「合规」。
 *
 * 规则出处（数字来自用户自身 build #3 干净口径样本 n=1,407，2026-04-21 起、仅明细
 * 成交构建；毛口径＝净＋费用，风险＝|开仓现金流|）：
 * - V2-A 日内车道：仅 0DTE，优先 ET 09:30–11:00，ET 12:00 后不开新的 0DTE，必须
 *   当日平。0DTE 当日 +3.44%（n=556，过路费 1.76%），9 点 +10.6%（n=160）、
 *   10 点 +2.7%、11 点 +4.9%，12 点后 −8.19%（剔除最好 5 笔 −15.54%）。
 * - V2-B 过夜车道：4-7DTE，至少持有到下一交易日；避开 ET 11:00–12:00 与
 *   13:00–14:00（隔夜单在这两小时 −1.76% / −2.99%，其余时段 +21%～+36%）；本车道
 *   不适用速度衰竭离场。4-7DTE 隔夜毛 +34.13%（n=65，胜率 61.5%，过路费 1.55%，
 *   剔除最好 3 笔仍 +22.27%、最好 5 笔仍 +19.13%）——全样本唯一非尾部驱动的组合。
 * - V2-C 硬禁止三条：① 1-3DTE 一律不开新仓（当日平 −2.94%，n=469；隔夜 +6.98%
 *   但剔除最好 5 笔 −4.03%）；② 4-7DTE 不得当日平掉（−4.04%，n=57，胜率 22.8%）；
 *   ③ ET 12:00 后不开 0DTE。
 * - V2-D 仓位：日内单每日最多 6 笔；同时持有的过夜单不超过 3 个。
 *
 * 边界：样本窗口仅 2026-04→07 一个市场状态（SPY 上行，隔夜多头天然占优），过夜
 * 车道 n=65 偏小且隔夜跳空风险在该窗口内未被充分体现。描述统计，不构成建议。
 */

export type LaneId = 'intraday' | 'overnight';

export type CheckStatus = 'pass' | 'fail' | 'missing';

export interface LaneCheck {
  /** 稳定标识，供测试与 aria-label 使用。 */
  id: string;
  label: string;
  status: CheckStatus;
  /** 一行原因，必须引用规则编号。 */
  reason: string;
}

export interface LaneHardBlock {
  id: string;
  ruleId: string;
  reason: string;
}

export interface LaneBudgetReading {
  id: string;
  label: string;
  /** 可算出来时是「x/6」这样的文本，算不出来时是 `null`（渲染成标缺）。 */
  text: string | null;
  limit: number;
  reason: string | null;
  /** 达到或超过额度：只是如实标注，不阻断任何操作。 */
  atLimit: boolean;
}

export interface LaneChecklistInput {
  lane: LaneId;
  /** 用户填写的进场 DTE；未填写＝标缺，不猜。 */
  dte: number | null;
  /** 用户填写的标的；未填写＝标缺。 */
  ticker: string | null;
  /** 用户自述「打算今天就平掉」——V2-C② 的唯一输入。 */
  intendsToCloseToday: boolean;
  /** 脉搏端点的 `generatedAt`（服务端时点）；缺席时 ET 时钟标缺。 */
  pulseGeneratedAt: string | null;
  /** 日内扫描的深度层候选（财报邻近度的唯一来源）。 */
  candidates: IntradayTopCandidate[];
  /** 脉搏/扫描报告的 ET 市场日，用来判断额度读数是否已过期。 */
  marketDateEt: string | null;
  budget: RuleComplianceDailyBudget | null;
  /** 额度读数不可得的原因（端点失败 / Journal 未构建）。 */
  budgetUnavailableReason: string | null;
}

export interface LaneChecklistResult {
  lane: LaneId;
  etHour: number | null;
  etClock: string | null;
  checks: LaneCheck[];
  hardBlocks: LaneHardBlock[];
  reminders: string[];
  budget: LaneBudgetReading[];
}

/** V2-A / V2-C③：ET 12:00 是 0DTE 的开仓截止线。 */
export const INTRADAY_ET_CUTOFF_HOUR = 12;
/** V2-B：过夜车道的 DTE 区间。 */
export const OVERNIGHT_MIN_DTE = 4;
export const OVERNIGHT_MAX_DTE = 7;
/** V2-B：隔夜单明显偏弱的两个 ET 小时。 */
export const OVERNIGHT_WEAK_ENTRY_ET_HOURS = [11, 13];
/** V2-B 的提醒行：本车道的设计前提就是跨日。 */
export const OVERNIGHT_NO_SPEED_EXIT_REMINDER = '本车道不适用速度衰竭离场（V2-B）';

export const LANE_LABELS: Record<LaneId, string> = {
  intraday: '日内',
  overnight: '过夜',
};

/** ET 小时：与脉搏条同一时钟源（`generatedAt` → America/New_York），不另起时钟。 */
export function etHourFromPulse(generatedAt: string | null): number | null {
  const parsed = parseApiTimestamp(generatedAt);
  if (!parsed) return null;
  const text = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    hourCycle: 'h23',
  }).format(parsed);
  const hour = Number(text);
  return Number.isFinite(hour) ? hour : null;
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
  const hit = candidates.find((item) => item.ticker.trim().toUpperCase() === wanted);
  return hit ? hit.earningsProximity : null;
}

function dteCheckIntraday(dte: number | null): LaneCheck {
  if (dte === null) {
    return {
      id: 'dte',
      label: '合约期限',
      status: 'missing',
      reason: '未填写进场 DTE，无法对照 V2-A（日内车道仅 0DTE）',
    };
  }
  if (dte === 0) {
    return {
      id: 'dte',
      label: '合约期限',
      status: 'pass',
      reason: 'V2-A：日内车道仅 0DTE，当前填写 0DTE',
    };
  }
  return {
    id: 'dte',
    label: '合约期限',
    status: 'fail',
    reason: `V2-A：日内车道仅 0DTE，当前填写 ${dte}DTE`,
  };
}

function dteCheckOvernight(dte: number | null): LaneCheck {
  if (dte === null) {
    return {
      id: 'dte',
      label: '合约期限',
      status: 'missing',
      reason: `未填写进场 DTE，无法对照 V2-B（过夜车道 ${OVERNIGHT_MIN_DTE}-${OVERNIGHT_MAX_DTE}DTE）`,
    };
  }
  if (dte >= OVERNIGHT_MIN_DTE && dte <= OVERNIGHT_MAX_DTE) {
    return {
      id: 'dte',
      label: '合约期限',
      status: 'pass',
      reason: `V2-B：过夜车道 ${OVERNIGHT_MIN_DTE}-${OVERNIGHT_MAX_DTE}DTE，当前填写 ${dte}DTE`,
    };
  }
  return {
    id: 'dte',
    label: '合约期限',
    status: 'fail',
    reason: `V2-B：过夜车道 ${OVERNIGHT_MIN_DTE}-${OVERNIGHT_MAX_DTE}DTE，当前填写 ${dte}DTE`,
  };
}

function clockCheckIntraday(etHour: number | null, etClock: string | null): LaneCheck {
  if (etHour === null) {
    return {
      id: 'clock',
      label: '开仓时点',
      status: 'missing',
      reason: '市场脉搏时点不可得，ET 时钟标缺——无法对照 V2-A / V2-C③ 的 12:00 截止线',
    };
  }
  if (etHour < INTRADAY_ET_CUTOFF_HOUR) {
    return {
      id: 'clock',
      label: '开仓时点',
      status: 'pass',
      reason: `V2-A：ET ${INTRADAY_ET_CUTOFF_HOUR}:00 前可开 0DTE，当前 ${etClock ?? '—'} ET`,
    };
  }
  return {
    id: 'clock',
    label: '开仓时点',
    status: 'fail',
    reason: `V2-C③：ET ${INTRADAY_ET_CUTOFF_HOUR}:00 后不开 0DTE，当前 ${etClock ?? '—'} ET`,
  };
}

function clockCheckOvernight(etHour: number | null, etClock: string | null): LaneCheck {
  if (etHour === null) {
    return {
      id: 'clock',
      label: '开仓时点',
      status: 'missing',
      reason: '市场脉搏时点不可得，ET 时钟标缺——无法对照 V2-B 的偏弱时段',
    };
  }
  if (OVERNIGHT_WEAK_ENTRY_ET_HOURS.includes(etHour)) {
    return {
      id: 'clock',
      label: '开仓时点',
      status: 'fail',
      reason: `V2-B：避开 ET 11:00–12:00 与 13:00–14:00（这两小时隔夜单 −1.76% / −2.99%），当前 ${etClock ?? '—'} ET`,
    };
  }
  return {
    id: 'clock',
    label: '开仓时点',
    status: 'pass',
    reason: `V2-B：当前 ${etClock ?? '—'} ET 不在偏弱的两小时（11:00–12:00 / 13:00–14:00）内`,
  };
}

/** 日内车道的财报回避检查：查不到就标缺，绝不以「无徽标」冒充安全。 */
function earningsCheck(
  ticker: string | null,
  candidates: IntradayTopCandidate[],
): LaneCheck {
  if (!ticker || !ticker.trim()) {
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'missing',
      reason: '未填写标的，财报回避窗无法核对（未知≠安全）',
    };
  }
  const proximity = findEarnings(ticker, candidates);
  if (!proximity) {
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'missing',
      reason: `${ticker.trim().toUpperCase()} 不在今日深度扫描层，没有可复用的财报日历读数（未知≠安全）`,
    };
  }
  if (proximity.state !== 'ready') {
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'missing',
      reason:
        proximity.unavailableReason
          ? `财报日历不可得：${proximity.unavailableReason}（未知≠安全）`
          : '财报日历不可得（未知≠安全）',
    };
  }
  if (proximity.withinBlackout) {
    const days = proximity.daysToEarnings;
    return {
      id: 'earnings',
      label: '财报回避',
      status: 'fail',
      reason:
        days === 0
          ? `${ticker.trim().toUpperCase()} 今日财报 · 期权贵 · 命中你的回避窗（≤${proximity.blackoutDays} 天）`
          : `${ticker.trim().toUpperCase()} 财报 ${days} 天内 · 期权贵 · 命中你的回避窗（≤${proximity.blackoutDays} 天）`,
    };
  }
  return {
    id: 'earnings',
    label: '财报回避',
    status: 'pass',
    reason:
      proximity.daysToEarnings === null
        ? `${ticker.trim().toUpperCase()} 前向 ${proximity.windowDays} 天内无财报`
        : `${ticker.trim().toUpperCase()} 距财报 ${proximity.daysToEarnings} 天，在回避窗（≤${proximity.blackoutDays} 天）之外`,
  };
}

/** V2-C 的三条硬禁止：与所选车道无关，命中即列出，绝不因为「选了另一条车道」而消失。 */
function hardBlocks(input: LaneChecklistInput, etHour: number | null): LaneHardBlock[] {
  const blocks: LaneHardBlock[] = [];
  const { dte } = input;
  if (dte !== null && dte >= 1 && dte <= 3) {
    blocks.push({
      id: 'dte_1_3',
      ruleId: 'V2-C①',
      reason:
        '1-3DTE 一律不开新仓：当日平 −2.94%（n=469，剔除最好 5 笔 −6.45%），'
        + '隔夜 +6.98% 但剔除最好 5 笔 −4.03%（纯尾部驱动）——两头都不占',
    });
  }
  if (
    dte !== null
    && dte >= OVERNIGHT_MIN_DTE
    && dte <= OVERNIGHT_MAX_DTE
    && input.intendsToCloseToday
  ) {
    blocks.push({
      id: 'bought_time_unused',
      ruleId: 'V2-C②',
      reason:
        `${dte}DTE 不得当日平掉：若开仓当天就想平，一开始就该走日内车道用 0DTE`
        + '（4-7DTE 当日平 −4.04%，n=57，胜率 22.8%）',
    });
  }
  if (dte === 0 && etHour !== null && etHour >= INTRADAY_ET_CUTOFF_HOUR) {
    blocks.push({
      id: 'late_0dte',
      ruleId: 'V2-C③',
      reason:
        `ET ${INTRADAY_ET_CUTOFF_HOUR}:00 后不开 0DTE：−8.19%（剔除最好 5 笔 −15.54%）`,
    });
  }
  return blocks;
}

/**
 * V2-D 的两个额度读数。
 *
 * 计数来自 `GET /journal/v2/personal-edge` 的 `rule_compliance.daily_budget`，
 * 它是**证据 build 的读数**，带自己的 `asOfTradingDay`。build 的最后一个交易日
 * 不是今天时，读数已过期——显式标缺并说明，**绝不显示 0**（0 会被读成「今天还没
 * 开过单」，而事实是「这个系统还不知道今天」）。
 */
function budgetReadings(input: LaneChecklistInput): LaneBudgetReading[] {
  const { budget, marketDateEt, budgetUnavailableReason } = input;
  const intradayLimit = budget?.intradayTicketLimit ?? 6;
  const overnightLimit = budget?.overnightConcurrentLimit ?? 3;

  const unavailable = (reason: string): LaneBudgetReading[] => [
    {
      id: 'intraday_tickets',
      label: '今日日内单',
      text: null,
      limit: intradayLimit,
      reason,
      atLimit: false,
    },
    {
      id: 'overnight_positions',
      label: '当前过夜持仓',
      text: null,
      limit: overnightLimit,
      reason,
      atLimit: false,
    },
  ];

  if (!budget) {
    return unavailable(
      budgetUnavailableReason
        ?? '车道遵守度读数不可得（Journal 未构建或端点不可得）——不以 0 冒充额度',
    );
  }
  const asOf = budget.asOfTradingDay;
  if (!asOf) {
    return unavailable(
      budget.intradayReason
        ?? '证据 build 内没有可定位的最后一个交易日——不以 0 冒充额度',
    );
  }
  if (marketDateEt && asOf !== marketDateEt) {
    return unavailable(
      `证据 build 截至 ${asOf}，不含今日（${marketDateEt} ET）的成交：`
      + '额度读数已过期，显示 0 会被误读为「今天还没开过单」',
    );
  }
  if (!marketDateEt) {
    return unavailable(
      `无法确定今日 ET 市场日，因此无法判断 build 的 ${asOf} 读数是否就是今天`,
    );
  }

  const intradayCount = budget.intradayTicketCount;
  const overnightCount = budget.overnightOpenCount;
  const unknownOpen = budget.overnightUnknownDteOpenCount;
  return [
    {
      id: 'intraday_tickets',
      label: '今日日内单',
      text: intradayCount === null ? null : `${intradayCount}/${intradayLimit}`,
      limit: intradayLimit,
      reason:
        intradayCount === null
          ? budget.intradayReason ?? '当日 0DTE 笔数不可得'
          : `V2-D③：日内单每日最多 ${intradayLimit} 笔 · 证据 build 截至 ${asOf}`
            + '（含 ET 12:00 后开的违规单，它们同样占用额度）',
      atLimit: intradayCount !== null && intradayCount >= intradayLimit,
    },
    {
      id: 'overnight_positions',
      label: '当前过夜持仓',
      text: overnightCount === null ? null : `${overnightCount}/${overnightLimit}`,
      limit: overnightLimit,
      reason:
        overnightCount === null
          ? budget.overnightReason ?? '当前未平仓的 4-7DTE 回合数不可得'
          : `V2-D③：同时持有的过夜单不超过 ${overnightLimit} 个 · 证据 build 截至 ${asOf}`
            + (unknownOpen > 0
              ? ` · 另有 ${unknownOpen} 个未平仓回合缺 DTE，无法归入本计数`
              : ''),
      atLimit: overnightCount !== null && overnightCount >= overnightLimit,
    },
  ];
}

/** 按所选车道给出逐条检查、硬禁止、提醒与额度读数。判定不排序、不打分、不下单。 */
export function evaluateLaneChecklist(input: LaneChecklistInput): LaneChecklistResult {
  const etHour = etHourFromPulse(input.pulseGeneratedAt);
  const etClock = etClockFromPulse(input.pulseGeneratedAt);

  const checks: LaneCheck[] = input.lane === 'intraday'
    ? [
      dteCheckIntraday(input.dte),
      clockCheckIntraday(etHour, etClock),
      earningsCheck(input.ticker, input.candidates),
    ]
    : [
      dteCheckOvernight(input.dte),
      clockCheckOvernight(etHour, etClock),
    ];

  const reminders = input.lane === 'overnight'
    ? [
      OVERNIGHT_NO_SPEED_EXIT_REMINDER,
      'V2-B：进场时必须能说清「为什么今天不一定走完」；改用日线级结构位或预设时间上限',
    ]
    : ['V2-A：日内单必须当日平；本车道靠尾部盈利，纪律重点是少做、只做真加速'];

  return {
    lane: input.lane,
    etHour,
    etClock,
    checks,
    hardBlocks: hardBlocks(input, etHour),
    reminders,
    budget: budgetReadings(input),
  };
}

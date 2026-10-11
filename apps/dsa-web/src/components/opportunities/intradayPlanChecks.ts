import type {
  IntradayBurstWindow,
  IntradayExecutionPolicy,
  IntradayLaneAvailability,
  IntradayTopCandidate,
} from '../../types/opportunities';
import {
  earningsCheck,
  evaluateLaneChecklist,
  evaluateLaneDayType,
  laneForDayType,
  ACTIVE_DTE_MAX,
  ACTIVE_DTE_MIN,
  type CheckStatus,
  type LaneDayTypeReading,
  type LaneId,
} from './laneChecklist';

/**
 * 「盘中计划」逐标的的**机械核对**判定层（组件只渲染，判定全在这里）。
 *
 * 它把两类东西拼在一起，**都不是买卖建议**：
 * 1. INTRA-0-7/v1 在**今天这个标的**上的逐条状态——车道规则
 *    与 ET 时钟直接复用 `laneChecklist.ts` 的同一套判定（不另写一份，避免两处
 *    规则漂移），财报回避复用同文件的 `earningsCheck`；
 * 2. 扫描已经算好的研究读数（近 30 分位移，以及 legacy 5m 的速度 / 哑火 /
 *    形态 / 波段 vs 大盘 / 今日波段）——原样透出，不重算、不打分、不排序；
 *    legacy 读数一律 neutral，绝不参与 P0/P0-X 或手动合约分级。
 *
 * 五种状态：`pass` / `fail` / `missing`（查过了拿不到）/ `requirement`（系统
 * 已知的硬性要求，例如「必须同一 ET 交易日平仓」）/ `neutral`（纯描述读数——近 30 分
 * 位移对 0.5 ATR 参考线只描述、不判定，v8 循环性更正后不再有「符合/不符合」
 * 框架）。**没有概率、没有胜率、没有目标价**。
 *
 * 引用的历史数字一律带样本量与口径（build #3 干净口径，n=1,407，
 * 2026-04-21→07-31）；边界：样本仅 2026-04→07 一个市场状态，规则由同一份
 * 样本内推出（in-sample），前向验证见 /journal 的规则遵守度。
 */

export interface PlanCheckRow {
  id: string;
  label: string;
  status: CheckStatus;
  /** 一行原因。 */
  reason: string;
  /** 规则编号；扫描读数没有规则编号时为 null，改标口径。 */
  ruleId: string | null;
  /** 读数口径（ruleId 为 null 时展示），例如「你自己 766 笔的 0.5 ATR 存活线」。 */
  basis: string | null;
}

export interface PlanTickerView {
  ticker: string;
  /** 今日唯一活动车道；标缺/不可用/读取中为 null，不猜。 */
  lane: LaneId | null;
  dayType: LaneDayTypeReading;
  /** 今日车道那一行文字（面板直接渲染）。 */
  laneText: string;
  /** 该标的在不在今日深度层：不在＝没有扫描读数，逐条标缺而不是「没问题」。 */
  inDeepLane: boolean;
  checks: PlanCheckRow[];
  /** 今日波段 chips：原样透出服务端读数。 */
  legs: IntradayBurstWindow[];
  /** 合约区固定请求 0..7DTE。 */
  contractMaxDte: number;
  /** 合约区固定从 0DTE 开始显示。 */
  contractMinDte: number;
  /**
   * 合约窗口口径固定为唯一活动车道；0..7DTE 连续显示。
   */
  contractWindowMode: 'intra_0_7';
  contractDteLabel: string;
  /** 失效位参考（只给已有观测值，不算任何目标价）。 */
  invalidationReference: string | null;
  /**
   * 今日财报长权利金硬性警示：标的确证在当前 ET 日发布财报
   * 且本面板合约候选为买入权利金时非空。只警示、
   * 不禁止、不隐藏任何内容——系统只读。
   */
  earningsHardBlock: string | null;
}

/**
 * 硬性警示文案（证据引用固定，测试锁定）：de Silva, Smith & So (2026,
 * Rev. Finance)——财报前买权利金平均亏损 5-9%，高预期波动事件 10-14%。
 */
export const EARNINGS_PREMIUM_HARD_BLOCK_TEXT =
  '硬性警示：今日财报 · 本面板车道为买入权利金——'
  + '财报前买权利金是文献最确定亏损模式之一（IV 买贵＋点差＋拖延平仓）'
  + '——de Silva, Smith & So (2026, Rev. Finance)：平均亏损 5-9%，'
  + '高预期波动事件 10-14%。系统只读：只警示，不禁止任何操作。';

/** 用户自己 766 笔回合推出的经验线（描述统计，不是预测）。 */
export const DISPLACEMENT_SURVIVAL_LINE_ATR = 0.5;

const DISPLACEMENT_BASIS =
  '你自己 766 笔回合（2026-06-08→07-31）：速死单仅 18.3% 达到 ≥0.5 ATR，'
  + '走出来的赢家 71.2% 达到。【v8 更正】该分层按持仓时长分组，而持仓时长由'
  + '结果决定——分层按构造就是循环的；只从第 15 分钟检查点向前计分后，差距从'
  + ' 37.6pp 塌到 0.1pp（n=3,492）。因此 0.5 只是参考刻度，本行是纯描述读数：'
  + '不含前向信息、非判定、非预测、非信号';

function laneRuleChecks(
  lane: LaneId,
  pulseGeneratedAt: string | null,
  laneAvailability: IntradayLaneAvailability | null,
  loading: boolean,
  nowMs?: number,
  executionPolicy?: IntradayExecutionPolicy | null,
): PlanCheckRow[] {
  // 直接复用车道清单的判定（dte 留空 → 输出「今天的要求」而不是标缺）。
  const result = evaluateLaneChecklist({
    lane,
    dte: null,
    pulseGeneratedAt,
    laneAvailability,
    loading,
    nowMs,
    executionPolicy,
  });
  return result.checks.map((check) => ({
    id: check.id,
    label: check.label,
    status: check.status,
    reason: check.reason,
    ruleId: 'INTRA-0-7/v1',
    basis: null,
  }));
}

function displacementRow(item: IntradayTopCandidate | null): PlanCheckRow {
  const base = { id: 'displacement', label: '近30分位移', ruleId: null, basis: DISPLACEMENT_BASIS };
  const reading = item?.recentDisplacement;
  if (!item || !reading || reading.state !== 'ready' || reading.netMoveAtr === null) {
    return {
      ...base,
      status: 'missing',
      reason: !item
        ? '该标的不在今日深度层，没有位移读数（未知≠「没动」）'
        : `位移读数标缺：${reading?.unavailableReason ?? 'K 线不足或不可得'}`,
    };
  }
  const value = reading.netMoveAtr;
  const abs = Math.abs(value);
  const comparability = reading.atrScaleComparability === 'intraday_scale_not_comparable'
    ? ' · ATR 标尺为盘中代理，与日线 ATR14 不可横向比较'
    : '';
  // 断档诚实化：窗口实际跨度 > 名义 30 分钟时如实标注，不冒充连续窗口。
  const span = reading.windowSpanMinutes ?? null;
  const gapNote = span !== null && span > reading.windowMinutes
    ? `（最近 ${reading.windowMinutes / 5} 根 5m K 线含断档，跨 ${span} 分钟）`
    : '';
  // v8：0.5 这条线的分层循环性已被量化推翻——达线/未达线只是**描述**，
  // 不是判定（neutral），永不渲染成「符合/不符合」。
  return {
    ...base,
    status: 'neutral',
    reason:
      `近 ${reading.windowMinutes} 分净位移 ${value >= 0 ? '+' : ''}${value.toFixed(2)} ATR${gapNote}`
      + `（${abs >= DISPLACEMENT_SURVIVAL_LINE_ATR ? '达' : '未达'} `
      + `${DISPLACEMENT_SURVIVAL_LINE_ATR} ATR 参考线——v8 更正：原分层按持仓时长`
      + '定义、循环性成立，此线不含前向信息，仅作刻度）'
      + comparability,
  };
}

function speedRow(item: IntradayTopCandidate | null): PlanCheckRow {
  const base = {
    id: 'speed',
    label: '速度',
    ruleId: null,
    basis: '相邻两个滚动 15 分钟窗口爆发分之差（5m K 线近似）',
  };
  const speed = item?.sessionBursts.speed;
  if (!item || !speed || speed.state === 'unknown') {
    return {
      ...base,
      status: 'missing',
      reason: !item
        ? '该标的不在今日深度层，没有速度读数'
        : `速度标缺：${speed?.unavailableReason ?? '窗口不足'}`,
    };
  }
  if (speed.state === 'accelerating') {
    return { ...base, status: 'neutral', reason: '加速中（观察值；不构成进场或持有指令）' };
  }
  if (speed.state === 'decelerating') {
    return {
      ...base,
      status: 'neutral',
      reason: '减速中（观察值；不映射旧 R1，也不构成离场指令）',
    };
  }
  return { ...base, status: 'neutral', reason: '持平（观察值；两个窗口爆发分基本相同）' };
}

function fizzleRow(item: IntradayTopCandidate | null): PlanCheckRow {
  const flag = item?.fizzleFlag ?? item?.sessionBursts.fizzleFlag ?? null;
  const base = {
    id: 'fizzle',
    label: '哑火形态',
    ruleId: null,
    basis: '起速回放研究（9,173 次爆发起点）；方向 AUC 全在 0.48–0.52，非方向判断',
  };
  if (!item || !flag || flag.state === 'unavailable') {
    return {
      ...base,
      status: 'missing',
      reason: !item ? '该标的不在今日深度层，没有形态读数' : '哑火读数标缺（当前窗口不可判定）',
    };
  }
  if (flag.state === 'flagged') {
    const ref = flag.reference;
    return {
      ...base,
      status: 'neutral',
      reason:
        `旧 5m 研究读数命中哑火形态：该形态 30 分钟内达到 ≥0.5 ATR 有利位移 `
        + `${(ref.inSampleRate * 100).toFixed(1)}%（样本内 n=${ref.nIn}）/ `
        + `${(ref.outOfSampleRate * 100).toFixed(1)}%（样本外 n=${ref.nOut}），`
        + `基准 ${(ref.baseRateIn * 100).toFixed(1)}%/${(ref.baseRateOut * 100).toFixed(1)}%`,
    };
  }
  return {
    ...base,
    status: 'neutral',
    reason: '旧 5m 研究读数未命中哑火形态；不参与 P0/P0-X 或合约分级',
  };
}

function setupRow(item: IntradayTopCandidate | null): PlanCheckRow {
  const base = {
    id: 'setup',
    label: '形态匹配 S1/S2/S3',
    ruleId: null,
    basis: 'styleMatch v1：5m 聚合到 15m 近似，不是 2m/1m 确认帧；纯标注，不参与排序',
  };
  const profile = item?.setupMatch;
  if (!item || !profile || profile.state !== 'ready') {
    return {
      ...base,
      status: 'missing',
      reason: !item
        ? '该标的不在今日深度层，没有形态读数'
        : `形态读数标缺：${profile?.unavailableReason ?? '不可得'}`,
    };
  }
  if (profile.matchedSetups.length > 0) {
    return {
      ...base,
      status: 'neutral',
      reason: `旧 5m 研究标注命中 ${profile.matchedSetups.join('/')}${
        profile.partialSetups.length > 0 ? ` · 部分命中 ${profile.partialSetups.join('/')}` : ''
      }；不参与分级`,
    };
  }
  if (profile.partialSetups.length > 0) {
    return {
      ...base,
      status: 'neutral',
      reason: `旧 5m 研究标注仅部分命中 ${profile.partialSetups.join('/')}；不参与分级`,
    };
  }
  return {
    ...base,
    status: 'neutral',
    reason: '旧 5m 研究标注未匹配 S1/S2/S3；不参与分级',
  };
}

function alignmentRow(item: IntradayTopCandidate | null): PlanCheckRow {
  const base = {
    id: 'alignment',
    label: '波段vs大盘',
    ruleId: null,
    basis: '候选当前爆发方向 vs SPY 会话 VWAP 位置（累计额/量近似），仅标注',
  };
  const alignment = item?.marketAlignment;
  if (!item || !alignment || alignment.state === 'unknown') {
    return {
      ...base,
      status: 'missing',
      reason: !item
        ? '该标的不在今日深度层，没有大盘对齐读数'
        : `对齐读数标缺：${alignment?.unavailableReason ?? '不可得'}`,
    };
  }
  return alignment.state === 'aligned'
    ? {
      ...base,
      status: 'neutral',
      reason: '旧 5m 爆发方向与 SPY 会话 VWAP 位置同向；仅作研究上下文',
    }
    : {
      ...base,
      status: 'neutral',
      reason: '旧 5m 爆发方向与 SPY 会话 VWAP 位置相逆；仅作研究上下文',
    };
}

function legsRow(item: IntradayTopCandidate | null): PlanCheckRow {
  const base = {
    id: 'legs',
    label: '今日波段',
    ruleId: null,
    basis: 'legacy 5m 滚动 15 分钟启发式；只作兼容研究，不参与提醒、排序或分级',
  };
  const bursts = item?.sessionBursts;
  if (!item || !bursts || bursts.state !== 'ready') {
    return {
      ...base,
      status: 'missing',
      reason: !item
        ? '该标的不在今日深度层，没有波段读数'
        : bursts?.state === 'insufficient_bars'
          ? '5m K 线不足，波段读数标缺'
          : `波段读数标缺：${bursts?.unavailableReason ?? '不可得'}`,
    };
  }
  if (bursts.legs.length === 0) {
    return {
      ...base,
      status: 'neutral',
      reason: 'legacy 5m 本时段无记录波段；不代表没有严格 2m P0',
    };
  }
  const strong = bursts.legs.filter((leg) => leg.grade === 'strong').length;
  return {
    ...base,
    status: 'neutral',
    reason: `legacy 5m 本时段 ${bursts.legs.length} 段研究波段${
      strong > 0 ? `（旧 strong 标签 ${strong} 段）` : ''
    }；不参与 P0/P0-X 或合约分级`,
  };
}

/** 失效位参考：只引用会话低/高；legacy 5m 波段不参与。 */
function invalidationReference(
  item: IntradayTopCandidate | null,
): string | null {
  if (!item) return null;
  const parts: string[] = [];
  if (item.sessionLow !== null) parts.push(`今日最低 ${item.sessionLow.toFixed(2)}`);
  if (item.sessionHigh !== null) parts.push(`今日最高 ${item.sessionHigh.toFixed(2)}`);
  return parts.length > 0 ? parts.join(' · ') : null;
}

export interface PlanTickerInput {
  ticker: string;
  candidates: IntradayTopCandidate[];
  laneAvailability: IntradayLaneAvailability | null;
  pulseGeneratedAt: string | null;
  executionPolicy?: IntradayExecutionPolicy | null;
  loading?: boolean;
  /** 「现在」的毫秒时戳（测试注入用）；缺省取 `Date.now()`。 */
  nowMs?: number;
}

/** 逐标的核对：车道规则（复用车道清单）+ 财报回避 + 扫描读数，四态如实。 */
export function evaluatePlanTicker(input: PlanTickerInput): PlanTickerView {
  const wanted = input.ticker.trim().toUpperCase();
  const loading = Boolean(input.loading);
  const dayType = evaluateLaneDayType(input.laneAvailability, { loading });
  const lane = laneForDayType(dayType);
  const item = input.candidates.find(
    (candidate) => candidate.ticker.trim().toUpperCase() === wanted,
  ) ?? null;

  // 活动车道只有一条；可用性未知时仍可写出固定要求，但 `lane` 保持 null，
  // 绝不把未知冒充已放行。
  const effectiveLane: LaneId = lane ?? 'intra_0_7';
  const earnings = earningsCheck(wanted, input.candidates);
  const checks: PlanCheckRow[] = [
    ...laneRuleChecks(
      effectiveLane,
      input.pulseGeneratedAt,
      input.laneAvailability,
      loading,
      input.nowMs,
      input.executionPolicy,
    ),
    {
      id: earnings.id,
      label: earnings.label,
      status: earnings.status,
      reason: earnings.reason,
      ruleId: null,
      basis: 'INTRA-0-7 当前规则：仅当日财报硬拦；未来事件不跨越；未知≠安全',
    },
    displacementRow(item),
    speedRow(item),
    fizzleRow(item),
    setupRow(item),
    alignmentRow(item),
    legsRow(item),
  ];

  // 合约候选永远读取完整 0..7DTE；可用性未知也不再拆成旧日内/过夜窗口。
  const contractWindowMode: PlanTickerView['contractWindowMode'] = 'intra_0_7';
  return {
    ticker: wanted,
    lane,
    dayType,
    laneText: dayType.text,
    inDeepLane: item !== null,
    checks,
    legs: item && item.sessionBursts.state === 'ready' ? item.sessionBursts.legs : [],
    contractMaxDte: ACTIVE_DTE_MAX,
    contractMinDte: ACTIVE_DTE_MIN,
    contractWindowMode,
    contractDteLabel: '0–7DTE（INTRA-0-7 · 必须同一 ET 交易日平仓）',
    invalidationReference: invalidationReference(item),
    // earningsCheck 只在确证“今日财报”时 fail；未来事件不跨越，不做硬拦。
    earningsHardBlock: earnings.status === 'fail'
      ? EARNINGS_PREMIUM_HARD_BLOCK_TEXT
      : null,
  };
}

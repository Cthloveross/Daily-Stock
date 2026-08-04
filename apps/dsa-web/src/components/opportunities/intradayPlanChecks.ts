import type {
  IntradayBurstWindow,
  IntradayLaneAvailability,
  IntradayTopCandidate,
} from '../../types/opportunities';
import {
  earningsCheck,
  evaluateLaneChecklist,
  evaluateLaneDayType,
  isIntradayLaneClosed,
  laneForDayType,
  OVERNIGHT_MAX_DTE,
  OVERNIGHT_MIN_DTE,
  type CheckStatus,
  type LaneDayTypeReading,
  type LaneId,
} from './laneChecklist';

/**
 * 「盘中计划」逐标的的**机械核对**判定层（组件只渲染，判定全在这里）。
 *
 * 它把两类东西拼在一起，**都不是买卖建议**：
 * 1. 用户自己那套规则（V2-A/B/C/E）在**今天这个标的**上的逐条状态——车道规则
 *    与 ET 时钟直接复用 `laneChecklist.ts` 的同一套判定（不另写一份，避免两处
 *    规则漂移），财报回避复用同文件的 `earningsCheck`；
 * 2. 扫描已经算好的读数（近 30 分位移 / 速度 / 哑火 / 形态 / 波段 vs 大盘 /
 *    今日波段）——原样透出，不重算、不打分、不排序。
 *
 * 四种状态：`pass` / `fail` / `missing`（查过了拿不到）/ `requirement`（系统
 * 已知的硬性要求，例如「今日只能 4-7DTE」）。**没有概率、没有胜率、没有目标价**。
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
  /** 规则编号（V2-A/B/C/E）；扫描读数没有规则编号时为 null，改标口径。 */
  ruleId: string | null;
  /** 读数口径（ruleId 为 null 时展示），例如「你自己 766 笔的 0.5 ATR 存活线」。 */
  basis: string | null;
}

export interface PlanTickerView {
  ticker: string;
  /** 今日成立的车道（V2-E）；标缺/读取中为 null，不猜。 */
  lane: LaneId | null;
  dayType: LaneDayTypeReading;
  /** 今日车道那一行文字（面板直接渲染）。 */
  laneText: string;
  /** 该标的在不在今日深度层：不在＝没有扫描读数，逐条标缺而不是「没问题」。 */
  inDeepLane: boolean;
  checks: PlanCheckRow[];
  /** 今日波段 chips：原样透出服务端读数。 */
  legs: IntradayBurstWindow[];
  /** 合约区请求的 DTE 上界（日内＝0，过夜＝7）。 */
  contractMaxDte: number;
  /** 合约区只看这个下界及以上的到期（过夜＝4，日内＝0）。 */
  contractMinDte: number;
  contractDteLabel: string;
  /** 失效位参考（只给已有观测值，不算任何目标价）。 */
  invalidationReference: string | null;
}

/** 用户自己 766 笔回合推出的经验线（描述统计，不是预测）。 */
export const DISPLACEMENT_SURVIVAL_LINE_ATR = 0.5;

const DISPLACEMENT_BASIS =
  '你自己 766 笔回合（2026-06-08→07-31）：速死单仅 18.3% 达到 ≥0.5 ATR，'
  + '走出来的赢家 71.2% 达到——描述统计，非预测';

function laneRuleChecks(
  lane: LaneId,
  pulseGeneratedAt: string | null,
  laneAvailability: IntradayLaneAvailability | null,
  loading: boolean,
): PlanCheckRow[] {
  // 直接复用车道清单的判定（dte 留空 → 输出「今天的要求」而不是标缺）。
  const result = evaluateLaneChecklist({
    lane,
    dte: null,
    intendsToCloseToday: false,
    pulseGeneratedAt,
    laneAvailability,
    loading,
  });
  return result.checks.map((check) => ({
    id: check.id,
    label: check.label,
    status: check.status,
    reason: check.reason,
    ruleId: check.id === 'dte'
      ? (lane === 'intraday' ? 'V2-A' : 'V2-B')
      : (lane === 'intraday' ? 'V2-A/C③' : 'V2-B'),
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
  return {
    ...base,
    status: abs >= DISPLACEMENT_SURVIVAL_LINE_ATR ? 'pass' : 'fail',
    reason:
      `近 ${reading.windowMinutes} 分净位移 ${value >= 0 ? '+' : ''}${value.toFixed(2)} ATR`
      + `（${abs >= DISPLACEMENT_SURVIVAL_LINE_ATR ? '已越过' : '未到'} `
      + `${DISPLACEMENT_SURVIVAL_LINE_ATR} ATR 存活线）${comparability}`,
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
    return { ...base, status: 'pass', reason: '加速中（当前窗口爆发分高于上一窗口）' };
  }
  if (speed.state === 'decelerating') {
    return {
      ...base,
      status: 'fail',
      reason: '减速中——对应你自己 R1 的离场提示，不是系统信号',
    };
  }
  return { ...base, status: 'fail', reason: '持平（两个窗口爆发分基本相同）' };
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
      status: 'fail',
      reason:
        `当前窗口命中哑火形态：该形态 30 分钟内达到 ≥0.5 ATR 有利位移 `
        + `${(ref.inSampleRate * 100).toFixed(1)}%（样本内 n=${ref.nIn}）/ `
        + `${(ref.outOfSampleRate * 100).toFixed(1)}%（样本外 n=${ref.nOut}），`
        + `基准 ${(ref.baseRateIn * 100).toFixed(1)}%/${(ref.baseRateOut * 100).toFixed(1)}%`,
    };
  }
  return { ...base, status: 'pass', reason: '当前窗口未命中哑火形态' };
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
      status: 'pass',
      reason: `命中 ${profile.matchedSetups.join('/')}${
        profile.partialSetups.length > 0 ? ` · 部分命中 ${profile.partialSetups.join('/')}` : ''
      }`,
    };
  }
  if (profile.partialSetups.length > 0) {
    return {
      ...base,
      status: 'fail',
      reason: `仅部分命中 ${profile.partialSetups.join('/')}，无完整形态匹配`,
    };
  }
  return { ...base, status: 'fail', reason: '当前时段几何不匹配 S1/S2/S3 中的任何一个' };
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
    ? { ...base, status: 'pass', reason: '当前爆发方向与 SPY 会话 VWAP 位置同向' }
    : { ...base, status: 'fail', reason: '当前爆发方向与 SPY 会话 VWAP 位置相逆' };
}

function legsRow(item: IntradayTopCandidate | null): PlanCheckRow {
  const base = {
    id: 'legs',
    label: '今日波段',
    ruleId: null,
    basis: '滚动 15 分钟推力×量比；strong=暴动口径 ≥8.0，medium=持续推升 ≥2.5',
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
    return { ...base, status: 'fail', reason: '本时段无记录波段' };
  }
  const strong = bursts.legs.filter((leg) => leg.grade === 'strong').length;
  return {
    ...base,
    status: 'pass',
    reason: `本时段 ${bursts.legs.length} 段记录波段${strong > 0 ? `（其中暴动 ${strong} 段）` : ''}`,
  };
}

/** 失效位参考：只引用已有观测值（会话低/高与最近一段反向波段），不算目标价。 */
function invalidationReference(
  item: IntradayTopCandidate | null,
): string | null {
  if (!item) return null;
  const parts: string[] = [];
  if (item.sessionLow !== null) parts.push(`今日最低 ${item.sessionLow.toFixed(2)}`);
  if (item.sessionHigh !== null) parts.push(`今日最高 ${item.sessionHigh.toFixed(2)}`);
  const legs = item.sessionBursts.state === 'ready' ? item.sessionBursts.legs : [];
  const lastDown = [...legs].reverse().find((leg) => leg.direction === 'down');
  const lastUp = [...legs].reverse().find((leg) => leg.direction === 'up');
  if (lastDown) parts.push(`最近一段下行波段 ${lastDown.startEt}–${lastDown.endEt}`);
  if (lastUp) parts.push(`最近一段上行波段 ${lastUp.startEt}–${lastUp.endEt}`);
  return parts.length > 0 ? parts.join(' · ') : null;
}

export interface PlanTickerInput {
  ticker: string;
  candidates: IntradayTopCandidate[];
  laneAvailability: IntradayLaneAvailability | null;
  pulseGeneratedAt: string | null;
  loading?: boolean;
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

  // 今日车道未定（标缺/读取中）时，车道规则按「日内」写出要求并如实标注
  // 车道可用性状态——绝不因为读不到就默认日内成立。
  const effectiveLane: LaneId = lane ?? 'intraday';
  const checks: PlanCheckRow[] = [
    ...laneRuleChecks(effectiveLane, input.pulseGeneratedAt, input.laneAvailability, loading),
    (() => {
      const check = earningsCheck(wanted, input.candidates);
      return {
        id: check.id,
        label: check.label,
        status: check.status,
        reason: check.reason,
        ruleId: null,
        basis: '你自己的财报回避窗（服务端 blackout_days）；未知≠安全',
      };
    })(),
    displacementRow(item),
    speedRow(item),
    fizzleRow(item),
    setupRow(item),
    alignmentRow(item),
    legsRow(item),
  ];

  const overnight = isIntradayLaneClosed(dayType) || lane === 'overnight';
  return {
    ticker: wanted,
    lane,
    dayType,
    laneText: dayType.text,
    inDeepLane: item !== null,
    checks,
    legs: item && item.sessionBursts.state === 'ready' ? item.sessionBursts.legs : [],
    contractMaxDte: overnight ? OVERNIGHT_MAX_DTE : 0,
    contractMinDte: overnight ? OVERNIGHT_MIN_DTE : 0,
    contractDteLabel: overnight
      ? `${OVERNIGHT_MIN_DTE}-${OVERNIGHT_MAX_DTE}DTE（V2-B 过夜车道）`
      : '0DTE（V2-A 日内车道）',
    invalidationReference: invalidationReference(item),
  };
}

import { describe, expect, it } from 'vitest';
import type {
  IntradayLaneAvailability,
  IntradayExecutionPolicy,
  IntradayTopCandidate,
} from '../../../types/opportunities';
import {
  EARNINGS_PREMIUM_HARD_BLOCK_TEXT,
  evaluatePlanTicker,
} from '../intradayPlanChecks';

const MARKET_DATE = '2026-08-05';
/** 2026-08-05 14:00Z = 10:00 ET（EDT），在共同开仓窗口内。 */
const PULSE_AT = '2026-08-05T14:00:00Z';
/** 「现在」＝脉搏后 1 分钟：新鲜时点（测试注入，避免真实时钟把脉搏判成过时）。 */
const NOW_MS = Date.parse(PULSE_AT) + 60_000;
const EXECUTION_POLICY: IntradayExecutionPolicy = {
  policyVersion: 'intraday_execution_policy_v1', state: 'ready',
  asOf: PULSE_AT, expiresAt: new Date(Date.parse(PULSE_AT) + 120_000).toISOString(),
  marketDateEt: MARKET_DATE, reason: 'regular', entryStartEt: '09:45',
  ordinaryEntryCutoffEt: '15:15', zeroDteEntryCutoffEt: '14:30',
  exitDeadlineEt: '15:45', closeEt: '16:00',
  ordinaryEntryAllowed: true, zeroDteEntryAllowed: true,
};

function availability(
  overrides: Partial<IntradayLaneAvailability> = {},
): IntradayLaneAvailability {
  return {
    formulaVersion: 'lane-availability/v1',
    marketDateEt: MARKET_DATE,
    maxDte: 7,
    dayType: 'intraday_available',
    dayTypeReason: '',
    basis: 'per_ticker_option_expiry_metadata_within_0_7_dte_v1',
    checkedScope: 'intraday_deep_lane_tickers',
    checkedCount: 1,
    readableCount: 1,
    unavailableCount: 0,
    zeroDteTickers: ['NVDA'],
    deferredTickers: [],
    tickers: [],
    limitations: [],
    ...overrides,
  };
}

function candidate(overrides: Partial<IntradayTopCandidate> = {}): IntradayTopCandidate {
  return {
    ticker: 'NVDA',
    sessionLow: 127.5,
    sessionHigh: 131,
    earningsProximity: {
      state: 'ready',
      daysToEarnings: 30,
      earningsDate: '2026-09-04',
      withinBlackout: false,
      blackoutDays: 3,
      windowDays: 60,
      basis: 'finnhub_earnings_calendar_forward_window',
      source: 'finnhub',
      fetchedAt: PULSE_AT,
      unavailableReason: null,
    },
    recentDisplacement: {
      state: 'ready',
      windowMinutes: 30,
      netMoveAtr: 0.72,
      highExcursionAtr: 0.91,
      lowExcursionAtr: -0.14,
      absRangeAtr: 1.05,
      atrBasis: 'atr14_daily',
      survivalLineAtr: 0.5,
      barCount: 40,
      unavailableReason: null,
    },
    marketAlignment: {
      state: 'aligned',
      burstDirection: 'up',
      spyVwapPosition: 'above',
      basis: 'candidate_current_burst_direction_vs_spy_session_vwap_position',
      unavailableReason: null,
    },
    setupMatch: {
      state: 'ready',
      styleMatchVersion: 'style_match_v1',
      quoteSessionScope: 'current_session',
      sessionDateEt: MARKET_DATE,
      barCount5M: 78,
      barCount15M: 26,
      matchedSetups: ['S1'],
      partialSetups: [],
      setups: [],
      basis: 'x',
      unavailableReason: null,
      limitations: [],
    },
    sessionBursts: {
      state: 'ready',
      sessionDateEt: MARKET_DATE,
      barCount: 78,
      medianBarRange: 0.5,
      medianBarVolume: 1000,
      medianBasis: 'current_session_bars_so_far',
      windowMinutes: 15,
      current: null,
      legs: [
        {
          startEt: '09:40',
          endEt: '09:55',
          thrustPercent: -0.93,
          thrustNorm: 3.1,
          volNorm: 2.9,
          score: 9.3,
          direction: 'down',
          grade: 'strong',
        },
      ],
      speed: {
        state: 'accelerating',
        currentScore: 8.6,
        previousScore: 5.1,
        delta: 3.5,
        basis: 'consecutive_rolling_15m_window_burst_score_delta_5m_bars',
        unavailableReason: null,
      },
      fizzleFlag: {
        state: 'not_flagged',
        efficiency: 0.4,
        volNorm: 3.0,
        stratum: 'intraday',
        reason: '',
        basis: '',
        reference: {
          sample: 'x',
          inSampleRate: 0.268,
          outOfSampleRate: 0.254,
          baseRateIn: 0.478,
          baseRateOut: 0.505,
          nIn: 291,
          nOut: 177,
        },
      },
      unavailableReason: null,
      source: 'fixture',
      fetchedAt: PULSE_AT,
      basis: 'rolling_15m_thrust_over_median_range_times_volume_ratio',
      limitations: [],
    },
    ...overrides,
  } as unknown as IntradayTopCandidate;
}

function byId(view: ReturnType<typeof evaluatePlanTicker>, id: string) {
  return view.checks.find((check) => check.id === id);
}

describe('evaluatePlanTicker', () => {
  it('raises the earnings hard block only on the actual earnings day', () => {
    const blocked = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate({
        earningsProximity: {
          state: 'ready',
          daysToEarnings: 0,
          earningsDate: '2026-08-05',
          withinBlackout: true,
          blackoutDays: 3,
          windowDays: 60,
          basis: 'finnhub_earnings_calendar_forward_window',
          source: 'finnhub',
          fetchedAt: PULSE_AT,
          unavailableReason: null,
        },
      })],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });
    expect(blocked.earningsHardBlock).toBe(EARNINGS_PREMIUM_HARD_BLOCK_TEXT);
    // 证据引用与红字语义（测试锁定原文关键片段）。
    expect(blocked.earningsHardBlock).toContain(
      'de Silva, Smith & So (2026, Rev. Finance)',
    );
    expect(blocked.earningsHardBlock).toContain(
      '财报前买权利金是文献最确定亏损模式之一',
    );
    expect(blocked.earningsHardBlock).toContain('只警示，不禁止');

    // 未来 1 天即使旧字段 withinBlackout=true，也因当日平仓不跨事件而放行。
    const tomorrow = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate({
        earningsProximity: {
          state: 'ready',
          daysToEarnings: 1,
          earningsDate: '2026-08-06',
          withinBlackout: true,
          blackoutDays: 3,
          windowDays: 60,
          basis: 'finnhub_earnings_calendar_forward_window',
          source: 'finnhub',
          fetchedAt: PULSE_AT,
          unavailableReason: null,
        },
      })],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });
    expect(tomorrow.earningsHardBlock).toBeNull();
    expect(byId(tomorrow, 'earnings')?.status).toBe('pass');
    expect(byId(tomorrow, 'earnings')?.reason).toContain('不跨财报事件');

    // 窗外 → 无硬性警示。
    const clear = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate()],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });
    expect(clear.earningsHardBlock).toBeNull();

    // 日历标缺（missing ≠ fail）→ 不冒充命中，也不冒充安全：无硬块，
    // 财报行保持标缺状态。
    const unknown = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });
    expect(unknown.earningsHardBlock).toBeNull();
    expect(byId(unknown, 'earnings')?.status).toBe('missing');
  });

  it('does not apply the removed overnight forward-window warning', () => {
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate({
        earningsProximity: {
          state: 'ready',
          daysToEarnings: 4,
          earningsDate: '2026-08-09',
          withinBlackout: false,
          blackoutDays: 3,
          windowDays: 5,
          basis: 'finnhub_earnings_calendar_forward_window',
          source: 'finnhub',
          fetchedAt: PULSE_AT,
          unavailableReason: null,
        },
      })],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });
    expect(view.contractWindowMode).toBe('intra_0_7');
    expect(view.earningsHardBlock).toBeNull();
    expect(byId(view, 'earnings')?.status).toBe('pass');
  });

  it('passes every reading on a clean INTRA-0-7 day', () => {
    const view = evaluatePlanTicker({
      ticker: 'nvda',
      candidates: [candidate()],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
      executionPolicy: EXECUTION_POLICY,
    });

    expect(view.ticker).toBe('NVDA');
    expect(view.lane).toBe('intra_0_7');
    expect(view.inDeepLane).toBe(true);
    expect(view.contractMinDte).toBe(0);
    expect(view.contractMaxDte).toBe(7);
    expect(view.contractDteLabel).toContain('0–7DTE');

    // 车道规则复用车道清单：未填 DTE ⇒ 写出要求，不是标缺。
    expect(byId(view, 'dte')?.status).toBe('requirement');
    expect(byId(view, 'dte')?.reason).toContain('0–7DTE');
    expect(byId(view, 'dte')?.ruleId).toBe('INTRA-0-7/v1');
    expect(byId(view, 'holding')?.status).toBe('requirement');
    expect(byId(view, 'holding')?.reason).toContain('同一 ET 交易日平仓');
    expect(byId(view, 'clock')?.status).toBe('pass');
    expect(byId(view, 'clock')?.ruleId).toBe('INTRA-0-7/v1');

    expect(byId(view, 'earnings')?.status).toBe('pass');
    // v8：位移是纯描述读数（neutral），达线不是「符合」，也不再有存活框架。
    expect(byId(view, 'displacement')?.status).toBe('neutral');
    expect(byId(view, 'displacement')?.reason).toContain('0.5 ATR 参考线');
    expect(byId(view, 'displacement')?.reason).toContain('不含前向信息');
    expect(byId(view, 'displacement')?.reason).not.toContain('存活线');
    expect(byId(view, 'speed')?.status).toBe('neutral');
    expect(byId(view, 'speed')?.reason).not.toContain('R1');
    expect(byId(view, 'fizzle')?.status).toBe('neutral');
    expect(byId(view, 'setup')?.status).toBe('neutral');
    expect(byId(view, 'setup')?.reason).toContain('S1');
    expect(byId(view, 'alignment')?.status).toBe('neutral');
    expect(byId(view, 'legs')?.status).toBe('neutral');
    expect(view.legs).toHaveLength(1);
    // 失效位只引用已有观测值，绝不算出任何目标价。
    expect(view.invalidationReference).toContain('今日最低 127.50');
    expect(view.invalidationReference).not.toContain('波段');
  });

  it('fails the readings that genuinely fail, one line each', () => {
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate({
        earningsProximity: {
          state: 'ready',
          daysToEarnings: 0,
          earningsDate: '2026-08-05',
          withinBlackout: true,
          blackoutDays: 3,
          windowDays: 60,
          basis: 'finnhub_earnings_calendar_forward_window',
          source: 'finnhub',
          fetchedAt: PULSE_AT,
          unavailableReason: null,
        },
        recentDisplacement: {
          state: 'ready',
          windowMinutes: 30,
          netMoveAtr: 0.12,
          highExcursionAtr: 0.2,
          lowExcursionAtr: -0.1,
          absRangeAtr: 0.3,
          atrBasis: 'atr14_daily',
          survivalLineAtr: 0.5,
          barCount: 40,
          unavailableReason: null,
        },
        marketAlignment: {
          state: 'against',
          burstDirection: 'up',
          spyVwapPosition: 'below',
          basis: 'candidate_current_burst_direction_vs_spy_session_vwap_position',
          unavailableReason: null,
        },
      })],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });

    expect(byId(view, 'earnings')?.status).toBe('fail');
    expect(byId(view, 'earnings')?.reason).toContain('今日财报');
    // v8：未达参考线同样是 neutral 描述，不是「不符合」——达线/未达线不构成判定。
    expect(byId(view, 'displacement')?.status).toBe('neutral');
    expect(byId(view, 'displacement')?.reason).toContain('未达');
    expect(byId(view, 'displacement')?.reason).toContain('参考线');
    expect(byId(view, 'alignment')?.status).toBe('neutral');
    expect(byId(view, 'alignment')?.reason).toContain('仅作研究上下文');
  });

  it('marks 标缺 for a ticker that is not in today deep lane — never a silent pass', () => {
    const view = evaluatePlanTicker({
      ticker: 'MU',
      candidates: [candidate()],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });

    expect(view.inDeepLane).toBe(false);
    for (const id of ['earnings', 'displacement', 'speed', 'fizzle', 'setup', 'alignment', 'legs']) {
      expect(byId(view, id)?.status).toBe('missing');
    }
    expect(byId(view, 'earnings')?.reason).toContain('未知≠安全');
    expect(byId(view, 'displacement')?.reason).toContain('未知≠「没动」');
    // 规则本身仍然写得出来（它不依赖扫描读数）。
    expect(byId(view, 'dte')?.status).toBe('requirement');
    expect(view.invalidationReference).toBeNull();
  });

  it('keeps 0..7 contracts available when legacy day_type has no 0DTE but 1..7 exists', () => {
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate()],
      laneAvailability: availability({
        dayType: 'overnight_only',
        zeroDteTickers: [],
        tickers: [{
          ticker: 'NVDA',
          state: 'ready',
          hasZeroDte: false,
          availableDteList: [1, 3, 6],
          expiries: [{ expiry: '2026-08-06', dte: 1 }],
          unavailableReason: null,
        }],
      }),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });

    expect(view.lane).toBe('intra_0_7');
    expect(view.contractMinDte).toBe(0);
    expect(view.contractMaxDte).toBe(7);
    expect(view.contractDteLabel).toContain('0–7DTE');
    expect(byId(view, 'dte')?.reason).toContain('0–7DTE');
  });

  it('reports 读取中 rather than 标缺 during a cold start', () => {
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [],
      laneAvailability: null,
      pulseGeneratedAt: null,
      loading: true,
    });

    expect(view.dayType.state).toBe('loading');
    expect(byId(view, 'clock')?.status).toBe('requirement');
    expect(byId(view, 'clock')?.reason).toContain('服务端交易日与执行窗口读取中');
    expect(byId(view, 'dte')?.reason).toContain('0–7DTE');
  });

  it('marks the clock missing when the server policy expires and DTE is not selected', () => {
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate()],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: Date.parse(PULSE_AT) + 20 * 60_000,
      executionPolicy: EXECUTION_POLICY,
    });

    expect(byId(view, 'clock')?.status).toBe('missing');
    expect(byId(view, 'clock')?.reason).toContain('已过时');
  });

  it('shows the full 0..7DTE window even when policy availability is unknown', () => {
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate()],
      laneAvailability: null,
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });

    expect(view.lane).toBeNull();
    expect(view.contractWindowMode).toBe('intra_0_7');
    expect(view.contractMinDte).toBe(0);
    expect(view.contractMaxDte).toBe(7);
    expect(view.contractDteLabel).toContain('0–7DTE');
    expect(view.contractDteLabel).toContain('同一 ET 交易日平仓');
  });

  it('annotates a gapped displacement window with its true span', () => {
    const base = candidate();
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate({
        recentDisplacement: {
          ...base.recentDisplacement!,
          windowSpanMinutes: 40,
        },
      })],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });

    expect(byId(view, 'displacement')?.reason).toContain('含断档');
    expect(byId(view, 'displacement')?.reason).toContain('跨 40 分钟');
  });

  it('flags a fizzle window with both sample sizes and never as a sell signal', () => {
    const base = candidate();
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate({
        sessionBursts: {
          ...base.sessionBursts,
          fizzleFlag: {
            ...base.sessionBursts.fizzleFlag!,
            state: 'flagged',
          },
        },
      })],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
      nowMs: NOW_MS,
    });

    const row = byId(view, 'fizzle');
    expect(row?.status).toBe('neutral');
    expect(row?.reason).toContain('n=291');
    expect(row?.reason).toContain('n=177');
    expect(row?.basis).toContain('非方向判断');
    expect(row?.reason).not.toMatch(/建议|应该|目标价|胜率/);
  });
});

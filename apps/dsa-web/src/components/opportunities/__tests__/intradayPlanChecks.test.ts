import { describe, expect, it } from 'vitest';
import type {
  IntradayLaneAvailability,
  IntradayTopCandidate,
} from '../../../types/opportunities';
import { evaluatePlanTicker } from '../intradayPlanChecks';

const MARKET_DATE = '2026-08-05';
/** 2026-08-05 13:30Z = 09:30 ET（EDT）。 */
const PULSE_AT = '2026-08-05T13:30:00Z';

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
  it('passes every reading on a clean 日内 day and cites the V2 rule ids', () => {
    const view = evaluatePlanTicker({
      ticker: 'nvda',
      candidates: [candidate()],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
    });

    expect(view.ticker).toBe('NVDA');
    expect(view.lane).toBe('intraday');
    expect(view.inDeepLane).toBe(true);
    expect(view.contractMinDte).toBe(0);
    expect(view.contractMaxDte).toBe(0);
    expect(view.contractDteLabel).toContain('0DTE');

    // 车道规则复用车道清单：未填 DTE ⇒ 写出要求，不是标缺。
    expect(byId(view, 'dte')?.status).toBe('requirement');
    expect(byId(view, 'dte')?.reason).toContain('今日只能 0DTE');
    expect(byId(view, 'dte')?.ruleId).toBe('V2-A');
    expect(byId(view, 'clock')?.status).toBe('pass');
    expect(byId(view, 'clock')?.ruleId).toBe('V2-A/C③');

    expect(byId(view, 'earnings')?.status).toBe('pass');
    expect(byId(view, 'displacement')?.status).toBe('pass');
    expect(byId(view, 'displacement')?.reason).toContain('0.5 ATR 存活线');
    expect(byId(view, 'speed')?.status).toBe('pass');
    expect(byId(view, 'fizzle')?.status).toBe('pass');
    expect(byId(view, 'setup')?.status).toBe('pass');
    expect(byId(view, 'setup')?.reason).toContain('S1');
    expect(byId(view, 'alignment')?.status).toBe('pass');
    expect(byId(view, 'legs')?.status).toBe('pass');
    expect(view.legs).toHaveLength(1);
    // 失效位只引用已有观测值，绝不算出任何目标价。
    expect(view.invalidationReference).toContain('今日最低 127.50');
    expect(view.invalidationReference).toContain('最近一段下行波段 09:40–09:55');
  });

  it('fails the readings that genuinely fail, one line each', () => {
    const view = evaluatePlanTicker({
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
    });

    expect(byId(view, 'earnings')?.status).toBe('fail');
    expect(byId(view, 'earnings')?.reason).toContain('回避窗');
    expect(byId(view, 'displacement')?.status).toBe('fail');
    expect(byId(view, 'displacement')?.reason).toContain('未到');
    expect(byId(view, 'alignment')?.status).toBe('fail');
  });

  it('marks 标缺 for a ticker that is not in today deep lane — never a silent pass', () => {
    const view = evaluatePlanTicker({
      ticker: 'MU',
      candidates: [candidate()],
      laneAvailability: availability(),
      pulseGeneratedAt: PULSE_AT,
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

  it('switches the whole card to the 过夜 lane on an overnight-only day', () => {
    const view = evaluatePlanTicker({
      ticker: 'NVDA',
      candidates: [candidate()],
      laneAvailability: availability({ dayType: 'overnight_only', zeroDteTickers: [] }),
      pulseGeneratedAt: PULSE_AT,
    });

    expect(view.lane).toBe('overnight');
    expect(view.contractMinDte).toBe(4);
    expect(view.contractMaxDte).toBe(7);
    expect(view.contractDteLabel).toContain('4-7DTE');
    expect(byId(view, 'dte')?.reason).toContain('今日只能 4-7DTE');
    expect(byId(view, 'dte')?.ruleId).toBe('V2-B');
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
    expect(byId(view, 'clock')?.reason).toContain('读取中');
    expect(byId(view, 'dte')?.reason).toContain('今日车道可用性读取中');
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
    });

    const row = byId(view, 'fizzle');
    expect(row?.status).toBe('fail');
    expect(row?.reason).toContain('n=291');
    expect(row?.reason).toContain('n=177');
    expect(row?.basis).toContain('非方向判断');
    expect(row?.reason).not.toMatch(/建议|应该|目标价|胜率/);
  });
});

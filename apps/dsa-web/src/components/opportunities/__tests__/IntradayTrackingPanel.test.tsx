import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IntradayTrackingPanel } from '../IntradayTrackingPanel';
import {
  computePlanDistances,
  deriveFrozenPlanLevels,
  formatAtrMultiple,
  formatSignedDollars,
} from '../intradayTracking';
import { fetchIntradayTracking } from '../../../api/opportunities';
import type {
  IntradaySessionState,
  IntradayTrackingItem,
  IntradayTrackingResponse,
  OpportunityCandidate,
} from '../../../types/opportunities';

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return {
    ...actual,
    fetchIntradayTracking: vi.fn(),
  };
});

function candidate(overrides: Partial<OpportunityCandidate> = {}): OpportunityCandidate {
  return {
    candidateId: 'opc_fixture_nvda',
    ticker: 'NVDA',
    researchState: 'watch_only',
    directionalContext: 'bullish',
    setupTags: ['daily_close_above_prior_20d_high'],
    lastCompletedBarAt: '2026-07-27T16:00:00-04:00',
    referenceSessionDate: '2026-07-27',
    referenceClose: 126,
    referencePriceBasis: 'prior_completed_close',
    source: 'MoomooFetcher',
    styleMatch: {
      status: 'unknown',
      source: 'unverified',
      matchedRules: [],
      conflictingRules: [],
      unknownFields: [],
    },
    hardGates: [],
    evidence: [
      {
        evidenceId: 'NVDA:prior_20d_range_position',
        domain: 'technical_structure',
        metric: 'prior_20d_range_position',
        value: { context: 'breakout', priorHigh: 128.5, priorLow: 120 },
        unit: null,
        status: 'supports',
        source: 'MoomooFetcher',
        observedAt: '2026-07-27T16:00:00-04:00',
        publishedAt: null,
        fetchedAt: '2026-07-28T12:00:00+00:00',
        observationWindow: 'latest_completed_close_vs_prior_20_completed_sessions',
        qualityState: 'derived',
        actionability: 'research_input',
        limitations: [],
      },
      {
        evidenceId: 'NVDA:ema8_ema13_alignment',
        domain: 'technical_structure',
        metric: 'ema8_ema13_alignment',
        value: { context: 'bullish', close: 126, ema8: 125.2, ema13: 124.1 },
        unit: null,
        status: 'supports',
        source: 'MoomooFetcher',
        observedAt: '2026-07-27T16:00:00-04:00',
        publishedAt: null,
        fetchedAt: '2026-07-28T12:00:00+00:00',
        observationWindow: 'all_available_completed_bars_with_full_ema_seed',
        qualityState: 'derived',
        actionability: 'research_input',
        limitations: [],
      },
    ],
    readiness: [],
    supportingEvidenceCount: 2,
    dataCompleteness: { state: 'complete', availableCount: 7, expectedCount: 7 },
    unknowns: [],
    ...overrides,
  };
}

function trackingItem(overrides: Partial<IntradayTrackingItem> = {}): IntradayTrackingItem {
  return {
    ticker: 'NVDA',
    state: 'ready',
    source: 'moomoo_openapi',
    fetchedAt: '2026-07-28T14:30:05+00:00',
    quoteAsOf: '2026-07-28 10:30:04',
    lastPrice: 126.5,
    sessionOpen: 125,
    sessionHigh: 127,
    sessionLow: 124.5,
    prevClose: 124,
    sessionVolume: 2500,
    sessionTurnover: 318_000,
    vwap: 125.9,
    vwapBasis: 'session_turnover_over_volume',
    vwapUnavailableReason: null,
    atr14: 2.5,
    atr14Method: 'wilder_smoothing_14_daily_completed_bars',
    atr14BarCount: 25,
    atr14LastBarDate: '2026-07-27',
    atr14Source: 'fixture',
    atr14UnavailableReason: null,
    volumePaceRatio: 2.5,
    volumePaceBasis: 'session_cumulative_vs_prior_20_session_full_day_median',
    prior20dMedianVolume: 1000,
    volumePaceUnavailableReason: null,
    message: '实时行情、VWAP 近似、量能节奏与 ATR14 已就绪；仅对照冻结盘前计划。',
    limitations: ['盘中跟踪只对照已冻结的盘前计划，不重新排序，不生成买卖信号。'],
    ...overrides,
  };
}

function trackingResponse(
  sessionState: IntradaySessionState,
  items: IntradayTrackingItem[] = [trackingItem()],
): IntradayTrackingResponse {
  return {
    schemaVersion: 'intraday-tracking/1.0',
    generatedAt: '2026-07-28T14:30:05+00:00',
    marketDateEt: '2026-07-28',
    sessionState,
    sessionStateBasis: 'america_new_york_clock_v1',
    trackingBasis: 'frozen_premarket_plan_readonly',
    items,
    limitations: [],
  };
}

function setDocumentVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('IntradayTrackingPanel pure helpers', () => {
  it('derives bullish confirm/invalidation anchors from frozen evidence, not display strings', () => {
    const levels = deriveFrozenPlanLevels(candidate());
    expect(levels.direction).toBe('bullish');
    expect(levels.confirmLevel).toBe(128.5);
    expect(levels.confirmLabel).toBe('前20日高');
    expect(levels.invalidationLevel).toBe(128.5);
    expect(levels.invalidationLabel).toBe('跌回前20日高下方');
  });

  it('derives bearish anchors from the prior 20d low', () => {
    const levels = deriveFrozenPlanLevels(candidate({ directionalContext: 'bearish' }));
    expect(levels.confirmLevel).toBe(120);
    expect(levels.confirmLabel).toBe('前20日低');
    expect(levels.invalidationLevel).toBe(120);
  });

  it('refuses to invent a single trigger for mixed direction', () => {
    const levels = deriveFrozenPlanLevels(candidate({ directionalContext: 'mixed' }));
    expect(levels.confirmLevel).toBeNull();
    expect(levels.invalidationLevel).toBeNull();
    expect(levels.confirmLabel).toBe('方向未定');
  });

  it('falls back to EMA13 for the bullish invalidation when priorHigh is missing', () => {
    const withoutRange = candidate();
    withoutRange.evidence = withoutRange.evidence.filter(
      (item) => item.metric !== 'prior_20d_range_position',
    );
    const levels = deriveFrozenPlanLevels(withoutRange);
    expect(levels.confirmLevel).toBeNull();
    expect(levels.invalidationLevel).toBe(124.1);
    expect(levels.invalidationLabel).toBe('跌破 EMA13');
  });

  it('computes signed ATR-normalized distances along the trade direction', () => {
    const distances = computePlanDistances(deriveFrozenPlanLevels(candidate()), 126.5, 2.5);
    // Bullish trigger 128.5, price 126.5 → +$2.00 still to travel = +0.8 ATR.
    expect(distances.confirm.dollars).toBeCloseTo(2.0, 6);
    expect(distances.confirm.atrMultiple).toBeCloseTo(0.8, 6);
    // Same anchor as invalidation: price is $2 below it → −0.8 ATR buffer.
    expect(distances.invalidation.dollars).toBeCloseTo(-2.0, 6);
    expect(distances.invalidation.atrMultiple).toBeCloseTo(-0.8, 6);
    expect(formatAtrMultiple(distances.confirm.atrMultiple)).toBe('+0.8 ATR');
    expect(formatSignedDollars(distances.confirm.dollars)).toBe('+$2.00');
  });

  it('keeps distances explicit when ATR or price is missing', () => {
    const noAtr = computePlanDistances(deriveFrozenPlanLevels(candidate()), 126.5, null);
    expect(noAtr.confirm.dollars).toBeCloseTo(2.0, 6);
    expect(noAtr.confirm.atrMultiple).toBeNull();
    expect(formatAtrMultiple(noAtr.confirm.atrMultiple)).toBe('ATR 标缺');
    const noPrice = computePlanDistances(deriveFrozenPlanLevels(candidate()), null, 2.5);
    expect(noPrice.confirm.dollars).toBeNull();
  });
});

describe('IntradayTrackingPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchIntradayTracking).mockReset();
    vi.mocked(fetchIntradayTracking).mockResolvedValue(trackingResponse('regular'));
    setDocumentVisibility('visible');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders the frozen-plan notice, as-of time, and all tracking columns', async () => {
    render(<IntradayTrackingPanel candidates={[candidate()]} />);

    expect(await screen.findByText('跟踪冻结的盘前计划 · 不是信号 · 不改变盘前排名')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: '今日计划跟踪表' })).toBeInTheDocument();
    for (const header of ['现价（as-of）', '距确认位', '距失效位', 'VWAP', '量能节奏', '盘段状态']) {
      expect(screen.getByText(header)).toBeInTheDocument();
    }
    await waitFor(() => {
      expect(screen.getByText('10:30:04 ET')).toBeInTheDocument();
    });
    expect(screen.getByText('126.5')).toBeInTheDocument();
    // ATR-normalized distances to the frozen confirm / invalidation anchors.
    expect(screen.getByText('+$2.00 · +0.8 ATR')).toBeInTheDocument();
    expect(screen.getByText('−$2.00 · −0.8 ATR')).toBeInTheDocument();
    expect(screen.getByText('VWAP 上方')).toBeInTheDocument();
    expect(screen.getByText('2.5×')).toBeInTheDocument();
    expect(screen.getByText(/vs 20日全日中位（未按时点折算）/)).toBeInTheDocument();
    expect(fetchIntradayTracking).toHaveBeenCalledWith(['NVDA'], { refresh: false });
  });

  it('marks a missing VWAP explicitly instead of showing zero', async () => {
    vi.mocked(fetchIntradayTracking).mockResolvedValue(trackingResponse('regular', [
      trackingItem({
        vwap: null,
        vwapUnavailableReason: 'missing_or_nonpositive_volume',
        volumePaceRatio: null,
        volumePaceUnavailableReason: 'missing_session_volume',
        state: 'partial',
      }),
    ]));
    render(<IntradayTrackingPanel candidates={[candidate()]} />);

    expect(await screen.findAllByText('标缺 · 缺累计成交量')).toHaveLength(2);
    expect(screen.queryByText('VWAP 上方')).not.toBeInTheDocument();
    expect(screen.queryByText('VWAP 下方')).not.toBeInTheDocument();
  });

  it('shows the not-configured state without fabricating live numbers', async () => {
    vi.mocked(fetchIntradayTracking).mockResolvedValue(trackingResponse('regular', [
      trackingItem({
        state: 'not_configured',
        lastPrice: null,
        quoteAsOf: null,
        vwap: null,
        vwapUnavailableReason: 'moomoo_not_configured',
        volumePaceRatio: null,
        volumePaceUnavailableReason: 'moomoo_not_configured',
        sessionVolume: null,
        sessionTurnover: null,
      }),
    ]));
    render(<IntradayTrackingPanel candidates={[candidate()]} />);

    expect(await screen.findAllByText('未配置')).not.toHaveLength(0);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getAllByText('标缺 · Moomoo 未启用')).toHaveLength(2);
  });

  it('polls every 60s only while the tab is visible and the session is open', async () => {
    vi.useFakeTimers();
    render(<IntradayTrackingPanel candidates={[candidate()]} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(2);

    // Hidden tab: the poller must stop completely.
    act(() => {
      setDocumentVisibility('hidden');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(2);

    // Back to visible: polling resumes on the next tick.
    act(() => {
      setDocumentVisibility('visible');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(3);
  });

  it('does not poll outside premarket/regular sessions', async () => {
    vi.useFakeTimers();
    vi.mocked(fetchIntradayTracking).mockResolvedValue(trackingResponse('closed'));
    render(<IntradayTrackingPanel candidates={[candidate()]} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300_000);
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText('休市').length).toBeGreaterThan(0);
  });

  it('stops polling when auto refresh is unchecked and refreshes manually with bypass', async () => {
    vi.useFakeTimers();
    render(<IntradayTrackingPanel candidates={[candidate()]} />);
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('checkbox', { name: '自动刷新今日计划跟踪' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '手动刷新今日计划跟踪' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchIntradayTracking).toHaveBeenCalledTimes(2);
    expect(fetchIntradayTracking).toHaveBeenLastCalledWith(['NVDA'], { refresh: true });
  });

  it('renders nothing without supported US underlyings', () => {
    const { container } = render(
      <IntradayTrackingPanel candidates={[candidate({ ticker: '600519' })]} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(fetchIntradayTracking).not.toHaveBeenCalled();
  });
});

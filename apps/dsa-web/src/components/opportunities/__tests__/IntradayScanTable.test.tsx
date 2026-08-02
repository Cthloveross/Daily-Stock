import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntradayScanTable } from '../IntradayScanTable';
import { fetchNearExpiryContracts } from '../../../api/opportunities';
import type {
  IntradayTopCandidate,
  IntradayTopResponse,
  NearExpiryContractResponse,
} from '../../../types/opportunities';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return {
    ...actual,
    fetchNearExpiryContracts: vi.fn(),
  };
});

function candidate(overrides: Partial<IntradayTopCandidate> = {}): IntradayTopCandidate {
  return {
    ticker: 'NVDA',
    researchState: 'active',
    stateReason: '',
    supportingEvidenceCount: 3,
    source: 'moomoo_openapi',
    fetchedAt: '2026-08-04T14:30:05+00:00',
    quoteAsOf: '2026-08-04 10:30:04',
    lastPrice: 130.5,
    sessionOpen: 128.0,
    sessionHigh: 131.0,
    sessionLow: 127.5,
    sessionChangePercent: 5.24,
    sessionChangeBasis: 'moomoo_snapshot_prev_close',
    gapPercent: 3.23,
    gapAtrMultiple: 2.67,
    gapBasis: 'session_open_vs_prior_completed_close_daily_loader',
    gapUnavailableReason: null,
    volumePaceRatio: 2.5,
    volumePaceBasis: 'session_cumulative_vs_prior_20_session_full_day_median',
    volumePaceUnavailableReason: null,
    vwap: 130.5,
    vwapPosition: 'flat',
    vwapBasis: 'session_turnover_over_volume',
    vwapUnavailableReason: null,
    atr14: 1.5,
    atr14LastBarDate: '2026-08-01',
    atrRangeExpansion: 2.33,
    rangeExpansionUnavailableReason: null,
    sessionBursts: {
      state: 'unavailable',
      sessionDateEt: null,
      barCount: 0,
      medianBarRange: null,
      medianBarVolume: null,
      medianBasis: null,
      windowMinutes: 15,
      current: null,
      legs: [],
      unavailableReason: 'history_5m_unavailable:RuntimeError',
      source: null,
      fetchedAt: null,
      basis: 'rolling_15m_thrust_over_median_range_times_volume_ratio',
      limitations: [],
    },
    optionActivity: {
      state: 'empty',
      count: 0,
      allCount: 0,
      bullishCount: 0,
      bearishCount: 0,
      neutralCount: 0,
      unclassifiedCount: 0,
      dominantSentiment: 'unknown',
      maxSingleTurnover: null,
      eventAsOf: null,
      fetchedAt: '2026-08-04T14:30:06+00:00',
      source: 'moomoo_openapi',
      limitations: [],
    },
    priorDayContext: {
      priorClose: 124.0,
      priorCloseDate: '2026-08-01',
      priorHigh20d: 124.5,
      priorLow20d: 104.5,
      rangePosition: 'above_prior_20d_high',
      emaAlignment: 'bullish',
    },
    evidence: [],
    message: '',
    limitations: [],
    ...overrides,
  };
}

function topResponse(): IntradayTopResponse {
  return {
    schemaVersion: 'intraday-top/1.0',
    runId: 'itr_fixture',
    generatedAt: '2026-08-04T14:30:06+00:00',
    asOf: '2026-08-04T14:30:05+00:00',
    marketDateEt: '2026-08-04',
    sessionState: 'regular',
    sessionStateBasis: 'america_new_york_clock_v1',
    quoteSessionScope: 'current_session',
    quoteSessionLabel: '当前交易时段',
    signalVersion: 'intraday_session_evidence_v2',
    rankingMethod: 'burst_score_first_then_evidence_count',
    statisticsTrack: 'none_intraday_v1_unscored',
    moomooEnabled: true,
    universe: ['NVDA', 'MU'],
    unsupportedSymbols: [],
    requestedLimit: 5,
    candidateCount: 2,
    candidates: [candidate(), candidate({ ticker: 'MU', lastPrice: 100.5 })],
    recentOptionEvents: [],
    limitations: [],
  };
}

function nearExpiryEmpty(ticker: string): NearExpiryContractResponse {
  return {
    schemaVersion: 'near-expiry-contracts/1.0',
    generatedAt: '2026-08-04T14:30:06+00:00',
    marketDateEt: '2026-08-04',
    item: {
      ticker,
      state: 'empty',
      source: 'moomoo_openapi',
      fetchedAt: '2026-08-04T14:30:06+00:00',
      formulaVersion: 'near-expiry-contracts/v1',
      maxDte: 3,
      spot: 100,
      spotAsOf: '2026-08-04 10:30:04',
      openInterestAsOf: '2026-08-01',
      openInterestBasis: 'prior_clearing_session',
      strikeWindow: { percentBand: 5, minStrikesPerSide: 8, basis: 'fixture' },
      coverage: {
        requestedContracts: 0,
        snapshotReceivedContracts: 0,
        observedContracts: 0,
        missingContracts: 0,
        failedBatches: 0,
        excludedNonstandardContracts: 0,
        excludedUnknownStandardTypeContracts: 0,
      },
      expiries: [],
      message: '3 天内没有该标的的期权到期日；这是诚实空态，不是数据失败。',
      limitations: [],
    },
  };
}

describe('IntradayScanTable 临期合约 row interaction', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.mocked(fetchNearExpiryContracts).mockReset();
    vi.mocked(fetchNearExpiryContracts).mockImplementation(
      async (symbol: string) => nearExpiryEmpty(symbol),
    );
  });

  it('keeps row click navigating to the detail page', () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('打开 NVDA 即时扫描详情'));

    expect(navigateMock).toHaveBeenCalledWith('/regime/opportunity/NVDA');
  });

  it('expands the near-expiry panel via the explicit button without navigating', async () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('展开 NVDA 临期合约'));

    await waitFor(() => {
      expect(screen.getByLabelText('NVDA 临期合约')).toBeInTheDocument();
    });
    expect(navigateMock).not.toHaveBeenCalled();
    expect(fetchNearExpiryContracts).toHaveBeenCalledWith('NVDA', 3, { refresh: false });

    // 再次点击收起。
    fireEvent.click(screen.getByLabelText('展开 NVDA 临期合约'));
    expect(screen.queryByLabelText('NVDA 临期合约')).not.toBeInTheDocument();
  });

  it('keeps a single expanded row so the table stays usable', async () => {
    render(<IntradayScanTable data={topResponse()} loading={false} error={null} />);

    fireEvent.click(screen.getByLabelText('展开 NVDA 临期合约'));
    await waitFor(() => {
      expect(screen.getByLabelText('NVDA 临期合约')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('展开 MU 临期合约'));
    await waitFor(() => {
      expect(screen.getByLabelText('MU 临期合约')).toBeInTheDocument();
    });
    expect(screen.queryByLabelText('NVDA 临期合约')).not.toBeInTheDocument();
  });
});

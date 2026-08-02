import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  NearExpiryContractPanel,
  SPREAD_ILLIQUID_THRESHOLD_PERCENT,
} from '../NearExpiryContractPanel';
import { fetchNearExpiryContracts } from '../../../api/opportunities';
import type {
  IntradayEarningsProximity,
  NearExpiryContractItem,
  NearExpiryContractResponse,
  NearExpiryContractRow,
} from '../../../types/opportunities';

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return {
    ...actual,
    fetchNearExpiryContracts: vi.fn(),
  };
});

function contractRow(overrides: Partial<NearExpiryContractRow> = {}): NearExpiryContractRow {
  return {
    code: 'US.MU260804C100000',
    right: 'C',
    strike: 100,
    expiry: '2026-08-04',
    dte: 2,
    bid: 1.0,
    ask: 1.2,
    mid: 1.1,
    spreadPercent: 18.181818,
    spreadUnavailableReason: null,
    lastPrice: 1.1,
    sessionVolume: 321,
    openInterest: 1500,
    ivPercent: 52.5,
    delta: 0.51,
    quoteAsOf: '2026-08-04 15:59:58',
    quoteState: 'observed',
    unavailableReason: null,
    isAtm: false,
    ...overrides,
  };
}

function earningsProximity(
  overrides: Partial<IntradayEarningsProximity> = {},
): IntradayEarningsProximity {
  return {
    state: 'ready',
    daysToEarnings: null,
    earningsDate: null,
    withinBlackout: false,
    blackoutDays: 3,
    windowDays: 5,
    basis: 'finnhub_earnings_calendar_forward_window',
    source: 'finnhub_earnings_calendar',
    fetchedAt: '2026-08-04T20:00:00+00:00',
    unavailableReason: null,
    ...overrides,
  };
}

function panelItem(overrides: Partial<NearExpiryContractItem> = {}): NearExpiryContractItem {
  return {
    ticker: 'MU',
    state: 'ready',
    source: 'moomoo_openapi',
    fetchedAt: '2026-08-04T20:00:00+00:00',
    formulaVersion: 'near-expiry-contracts/v1',
    maxDte: 3,
    spot: 100,
    spotAsOf: '2026-08-04 15:59:59',
    openInterestAsOf: '2026-07-31',
    openInterestBasis: 'prior_clearing_session',
    strikeWindow: {
      percentBand: 5,
      minStrikesPerSide: 8,
      basis: 'abs(strike/spot-1) <= 5% 与现价上下各最近 8 档行权价的并集',
    },
    coverage: {
      requestedContracts: 2,
      snapshotReceivedContracts: 2,
      observedContracts: 2,
      missingContracts: 0,
      failedBatches: 0,
      excludedNonstandardContracts: 0,
      excludedUnknownStandardTypeContracts: 0,
    },
    expiries: [
      {
        expiry: '2026-08-04',
        dte: 2,
        state: 'ready',
        contractCount: 2,
        observedQuoteCount: 2,
        contracts: [
          contractRow({ isAtm: true }),
          contractRow({
            code: 'US.MU260804P100000',
            right: 'P',
            isAtm: true,
            delta: -0.49,
          }),
        ],
      },
    ],
    earningsProximity: earningsProximity(),
    message: '临期合约读数已读取。',
    limitations: [
      'OI 为上一清算交易日（T-1）结算口径，不是盘中实时持仓。',
      '本面板仅为合约选择研究参考，不构成合约推荐或买卖建议。',
      '执行前以券商实时盘口为准。',
    ],
    ...overrides,
  };
}

function response(item: NearExpiryContractItem): NearExpiryContractResponse {
  return {
    schemaVersion: 'near-expiry-contracts/1.0',
    generatedAt: item.fetchedAt,
    marketDateEt: '2026-08-04',
    item,
  };
}

describe('NearExpiryContractPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchNearExpiryContracts).mockReset();
  });

  it('renders expiry groups with the honesty header, spot as-of and quote as-of', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem()));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getByText(/2 DTE · 2026-08-04/)).toBeInTheDocument();
    });
    expect(
      screen.getByText('合约选择参考 · 不构成推荐 · 以券商实时盘口为准'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Spot 100\.00/)).toBeInTheDocument();
    expect(screen.getAllByText('15:59:58 ET').length).toBe(2);
    expect(screen.getByText(/OI 为 T-1 清算口径（截至 2026-07-31）/)).toBeInTheDocument();
    expect(screen.getByText('2/2 张有观测报价')).toBeInTheDocument();
    // Honesty limitations are surfaced verbatim.
    expect(
      screen.getByText('本面板仅为合约选择研究参考，不构成合约推荐或买卖建议。'),
    ).toBeInTheDocument();
  });

  it('flags 流动性差 strictly above the 15% threshold, not at it', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem({
      expiries: [
        {
          expiry: '2026-08-04',
          dte: 2,
          state: 'ready',
          contractCount: 2,
          observedQuoteCount: 2,
          contracts: [
            contractRow({
              code: 'AT_THRESHOLD',
              spreadPercent: SPREAD_ILLIQUID_THRESHOLD_PERCENT,
            }),
            contractRow({
              code: 'ABOVE_THRESHOLD',
              strike: 101,
              spreadPercent: SPREAD_ILLIQUID_THRESHOLD_PERCENT + 0.1,
            }),
          ],
        },
      ],
    })));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getAllByText(/流动性差/).length).toBeGreaterThan(0);
    });
    // 阈值行不标记（严格大于才标），仅超阈值行标记 → 恰好一个 badge，
    // 另外一个「流动性差」出现在页脚阈值说明中。
    const badges = screen.getAllByText('流动性差');
    expect(badges.length).toBe(1);
  });

  it('renders 标缺 for missing bid/ask instead of zero-filled spread', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem({
      state: 'partial',
      expiries: [
        {
          expiry: '2026-08-04',
          dte: 2,
          state: 'partial',
          contractCount: 2,
          observedQuoteCount: 1,
          contracts: [
            contractRow(),
            contractRow({
              code: 'MISSING',
              strike: 101,
              bid: null,
              ask: null,
              mid: null,
              spreadPercent: null,
              spreadUnavailableReason: 'bid_or_ask_unavailable',
              lastPrice: null,
              sessionVolume: null,
              openInterest: null,
              ivPercent: null,
              quoteAsOf: null,
              quoteState: 'unavailable',
              unavailableReason: 'snapshot_missing',
              isAtm: false,
            }),
          ],
        },
      ],
    })));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getByText('部分合约快照缺失，对应行显式标缺')).toBeInTheDocument();
    });
    // 缺失行的 Bid×Ask、最新价、量、OI、IV、as-of 全部显式标缺。
    expect(screen.getAllByText('标缺').length).toBeGreaterThanOrEqual(6);
    expect(screen.queryByText('0.00 × 0.00')).not.toBeInTheDocument();
  });

  it('highlights ATM rows with a position-only badge', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem()));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getAllByText('ATM').length).toBe(2);
    });
  });

  it('shows the honest empty-window message without fabricating rows', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem({
      state: 'empty',
      expiries: [],
      message: '3 天内没有该标的的期权到期日；这是诚实空态，不是数据失败。',
    })));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(
        screen.getByText('3 天内没有该标的的期权到期日；这是诚实空态，不是数据失败。'),
      ).toBeInTheDocument();
    });
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('surfaces fetch failures honestly instead of rendering an empty table', async () => {
    vi.mocked(fetchNearExpiryContracts).mockRejectedValue(new Error('网络超时'));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getByText(/临期合约暂不可用：网络超时/)).toBeInTheDocument();
    });
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows a prominent header badge when earnings fall inside the blackout window', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem({
      earningsProximity: earningsProximity({
        daysToEarnings: 2,
        earningsDate: '2026-08-06',
        withinBlackout: true,
      }),
    })));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(
        screen.getByText('财报 2 天内 · 期权贵 · 你的回避规则'),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText('财报日历标缺 · 未知≠安全')).not.toBeInTheDocument();
  });

  it('labels a same-day earnings blackout as 今日财报', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem({
      earningsProximity: earningsProximity({
        daysToEarnings: 0,
        earningsDate: '2026-08-04',
        withinBlackout: true,
      }),
    })));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(
        screen.getByText('今日财报 · 期权贵 · 你的回避规则'),
      ).toBeInTheDocument();
    });
  });

  it('renders no earnings marker when ready and outside the blackout window', async () => {
    // 默认 fixture：state ready、窗口内无财报 → 不加任何财报标注（不制造噪音）。
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem()));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getByText(/2 DTE · 2026-08-04/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/期权贵/)).not.toBeInTheDocument();
    expect(screen.queryByText('财报日历标缺 · 未知≠安全')).not.toBeInTheDocument();
  });

  it('marks a missing earnings calendar as unknown, never implied-safe', async () => {
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem({
      earningsProximity: earningsProximity({
        state: 'unavailable',
        withinBlackout: null,
        unavailableReason: 'finnhub_not_configured',
      }),
    })));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getByText('财报日历标缺 · 未知≠安全')).toBeInTheDocument();
    });
    expect(screen.queryByText(/期权贵/)).not.toBeInTheDocument();
  });

  it('treats a legacy payload without the earnings field as unknown', async () => {
    // additive 兼容：旧 30 秒会话缓存里可能还有不带该字段的载荷。
    vi.mocked(fetchNearExpiryContracts).mockResolvedValue(response(panelItem({
      earningsProximity: undefined,
    })));

    render(<NearExpiryContractPanel symbol="MU" />);

    await waitFor(() => {
      expect(screen.getByText('财报日历标缺 · 未知≠安全')).toBeInTheDocument();
    });
  });
});

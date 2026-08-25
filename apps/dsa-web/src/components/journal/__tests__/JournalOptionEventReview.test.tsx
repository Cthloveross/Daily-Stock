import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import JournalOptionEventReview from '../JournalOptionEventReview';
import { fetchIntradayTop } from '../../../api/opportunities';
import type { IntradayTopResponse } from '../../../types/opportunities';

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return {
    ...actual,
    fetchIntradayTop: vi.fn(),
  };
});

/** 组件只消费 recentOptionEvents 与 quoteSessionLabel：夹具只带这两块。 */
function topStub(): IntradayTopResponse {
  return {
    quoteSessionLabel: '最近一个交易时段',
    recentOptionEvents: [
      {
        ticker: 'NVDA',
        eventId: 'evt_1',
        optionCode: 'US.NVDA260918C100000',
        fillTime: '2026-08-13 15:42:00',
        tickerType: 'BUY',
        price: 5.0,
        volume: 100,
        turnover: 250_000,
        optionType: 'CALL',
        strikePrice: 100,
        expiry: '2026-09-18',
        dte: 35,
        sentiment: 'BULLISH',
        orderTypes: ['SWEEP'],
        strategyType: 'SINGLE_LEG',
      },
    ],
  } as unknown as IntradayTopResponse;
}

describe('JournalOptionEventReview（复盘用期权异动，自 /intraday 迁入）', () => {
  beforeEach(() => {
    vi.mocked(fetchIntradayTop).mockReset();
  });

  it('reads intraday-top once (no polling) and renders the intact feed', async () => {
    vi.mocked(fetchIntradayTop).mockResolvedValue(topStub());
    render(<JournalOptionEventReview />);

    // 等真正的 feed 内容出现（加载占位与 feed 共用同一 aria-label）。
    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    const feed = screen.getByLabelText('期权异动');
    // 组件保持原样：时段口径 + 事件行 + 诚实边界固定脚注。
    expect(within(feed).getByText(/最近一个交易时段/)).toBeInTheDocument();
    expect(within(feed).getByText('偏多（Moomoo 分类）')).toBeInTheDocument();
    expect(
      within(feed).getByText(/不推断开平仓，不证明真实主动买卖方向，不是信号/),
    ).toBeInTheDocument();
    // 复盘面只读一次：空 symbols 走服务端既有扫描缓存，零新增取数路径。
    expect(fetchIntradayTop).toHaveBeenCalledTimes(1);
    expect(fetchIntradayTop).toHaveBeenCalledWith([], { limit: 5 });
  });

  it('states the loading and failure states honestly instead of faking an empty feed', async () => {
    let reject: (error: Error) => void = () => {};
    vi.mocked(fetchIntradayTop).mockImplementation(
      () => new Promise<IntradayTopResponse>((_resolve, r) => {
        reject = r;
      }),
    );
    render(<JournalOptionEventReview />);
    expect(screen.getByText('期权异动读取中…')).toBeInTheDocument();

    reject(new Error('scan down'));
    expect(
      await screen.findByText('期权异动暂不可用：scan down'),
    ).toBeInTheDocument();
    // 失败态绝不渲染空 feed 冒充「无异动」。
    expect(screen.queryByText(/当前自选池暂无可展示的异动成交/)).not.toBeInTheDocument();
  });
});

import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntradayAlertFeedCard } from '../IntradayAlertFeedCard';
import { fetchPushLedgerDaily } from '../../../api/pushLedger';
import type { PushDeliveryStatus, PushLedgerDailyResponse } from '../../../types/pushLedger';

vi.mock('../../../api/pushLedger', () => ({
  fetchPushLedgerDaily: vi.fn(),
}));

function response(
  overrides: Partial<PushLedgerDailyResponse> = {},
): PushLedgerDailyResponse {
  return {
    etDate: '2026-08-25',
    events: [
      {
        pushId: 'a1',
        recordKind: 'decision',
        etDate: '2026-08-25',
        ts: '2026-08-25T14:31:00+00:00',
        tier: 'P2',
        sessionScope: 'regular',
        rule: 'volume_burst',
        ticker: 'NVDA',
        text: 'NVDA 2m 异动形成中，18m 位移未到 0.50 ATR',
        deliveryChannel: 'web_only',
        suppressed: false,
        suppressedReason: null,
        gateState: null,
        laneRuleId: null,
        messageKey: null,
        telegramOk: null,
        conditions: [],
      },
      {
        pushId: 'a2',
        recordKind: 'decision',
        etDate: '2026-08-25',
        ts: '2026-08-25T14:32:00+00:00',
        tier: 'P1',
        sessionScope: 'regular',
        rule: 'p1_near_miss',
        ticker: 'TSLA',
        text: 'TSLA 开盘顺序候选：累计证据已确认',
        deliveryChannel: 'telegram_silent',
        suppressed: false,
        suppressedReason: null,
        gateState: 'missing',
        laneRuleId: 'V2-B',
        messageKey: 'mk1',
        telegramOk: null,
        conditions: [],
      },
      {
        pushId: 'a3',
        recordKind: 'decision',
        etDate: '2026-08-25',
        ts: '2026-08-25T14:33:00+00:00',
        tier: 'P3',
        sessionScope: 'regular',
        rule: 'p0_candidate_eval',
        ticker: 'MRVL',
        text: 'MRVL 2m P0 四条件未全部满足',
        deliveryChannel: 'ledger_only',
        suppressed: true,
        suppressedReason: 'p0_conditions_short:差 2 条',
        gateState: 'missing',
        laneRuleId: 'V2-B',
        messageKey: null,
        telegramOk: null,
        conditions: [],
      },
    ],
    budgets: {
      openingUsedToday: 1,
      openingDailyBudget: 3,
      p0UsedToday: 0,
      p0DailyBudget: 2,
      p0UsedWeek: 1,
      p0WeeklyBudget: 7,
      p0xUsedTodayBySession: {
        premarket: 1,
        regular: 0,
        afterhours: 0,
      },
      p0xDailyBudgetBySession: {
        premarket: 1,
        afterhours: 1,
      },
      p0xUsedWeek: 2,
      p0xWeeklyBudget: 5,
      p1UsedToday: 1,
      p1DailyBudget: 3,
      telegramLinesUsed: 1,
      telegramLinesCap: 20,
    },
    p0PassRate: {
      status: 'missing',
      reason: '尚无已投递的 P0',
      window: 20,
      delivered: 0,
      passed: 0,
      rate: null,
      threshold: 0.7,
      autoTightenEnabled: false,
      limitations: ['P0 passed 率是机械代理'],
    },
    limitations: ['推送台账是 append-only 记账（无静默丢弃）'],
    ...overrides,
  };
}

describe('IntradayAlertFeedCard', () => {
  beforeEach(() => {
    vi.mocked(fetchPushLedgerDaily).mockReset();
  });

  it('renders user-visible decisions newest-first and keeps P3 ledger-only', async () => {
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(response());
    render(<IntradayAlertFeedCard pollMs={0} />);

    await waitFor(() => {
      expect(screen.getByTestId('push-feed-list')).toBeInTheDocument();
    });
    const rows = screen
      .getByTestId('push-feed-list')
      .querySelectorAll('[data-push-tier]');
    expect(rows).toHaveLength(2);
    // P3(MRVL) 保持在持久化台账，但不污染用户主 feed；P1 → P2 最新在前。
    expect(rows[0].getAttribute('data-push-tier')).toBe('P1');
    expect(rows[1].getAttribute('data-push-tier')).toBe('P2');
    expect(screen.queryByText(/p0_conditions_short/)).not.toBeInTheDocument();
    // 通道标签。
    expect(screen.getByText('静默')).toBeInTheDocument();
    expect(screen.getByText('仅页面')).toBeInTheDocument();
  });

  it('shows budgets and keeps the frozen thresholds explicit', async () => {
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(response());
    render(<IntradayAlertFeedCard pollMs={0} />);

    await waitFor(() => {
      expect(screen.getByTestId('push-budget-strip')).toBeInTheDocument();
    });
    expect(screen.getByText(/P0 0\/2 日 · 1\/7 周/)).toBeInTheDocument();
    expect(
      screen.getByText(/P0-X 盘前 1\/1 日 · 盘后 0\/1 日 · 2\/5 周/),
    ).toBeInTheDocument();
    expect(screen.getByText(/开盘响铃 1\/3 日/)).toBeInTheDocument();
    expect(screen.getByText(/P1 1\/3 日/)).toBeInTheDocument();
    const passRate = screen.getByTestId('p0-pass-rate');
    expect(passRate.textContent).toContain('机械代理');
    expect(passRate.textContent).toContain('标缺');
    expect(passRate.textContent).toContain('不自动改变 P95 / 1.5x / 0.50 ATR');
  });

  it('renders a P0-X badge and readable versioned 2m evidence', async () => {
    const candidate = response().events[0];
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(
      response({
        events: [{
          ...candidate,
          pushId: 'p0x-1',
          tier: 'P0X',
          sessionScope: 'premarket',
          rule: 'p0_2m_candidate',
          text: 'TSLA 向上 · 触发价 $350.25 · 盘外仅作正股信息提醒',
          p0SignalEvidenceV2: {
              signalVersion: 'p0_2m_signal_v1',
              state: 'qualified',
              alertQualified: true,
              alertTier: 'P0X',
              symbol: 'TSLA',
              universeHash: 'sha256:abc123',
              sessionScope: 'premarket',
              windowStart: '2026-08-25T09:10:00-04:00',
              impulseWindowStart: '2026-08-25T09:20:00-04:00',
              displacementWindowStart: '2026-08-25T09:10:00-04:00',
              windowEnd: '2026-08-25T09:28:00-04:00',
              direction: 'up',
              triggerPrice: 350.25,
              impulse8mLog: 0.0123,
              impulse8mPercent: 1.24,
              pricePercentile: 0.983333,
              rvol8m: 1.876,
              referenceMedianVolume8m: 100000,
              displacement18mPercent: 2.15,
              displacementAtr: 0.624,
              priorDailyAtr14: 12.5,
              baselineCount: 60,
              baselineFirstDate: '2026-05-29',
              baselineLastDate: '2026-08-24',
              source: 'moomoo_1m_exact_2m',
              dataQuality: { status: 'complete', issues: [] },
              optionCandidates: [],
              researchFields: {},
              failedChecks: [],
              unavailableReason: null,
              thresholds: {},
              basis: 'same-clock strict 2m',
              limitations: [],
          },
          conditions: [],
        }],
      }),
    );
    render(<IntradayAlertFeedCard pollMs={0} />);

    expect(await screen.findByText('P0-X 盘外异动')).toBeInTheDocument();
    expect(screen.getByText(/TSLA 向上/)).toBeInTheDocument();
    const details = screen.getByTestId('p0-2m-evidence');
    expect(details).toHaveTextContent('方向 向上');
    expect(details).toHaveTextContent('盘段 盘前');
    expect(details).toHaveTextContent('窗口截止 09:28 ET');
    expect(details).toHaveTextContent('同钟价格分位 98.3%');
    expect(details).toHaveTextContent('8m RVOL 1.88x');
    expect(details).toHaveTextContent('18m 位移 0.62 ATR');
    expect(details).toHaveTextContent('基线 60日');
    expect(details).toHaveTextContent('数据质量 完整');
    expect(details).toHaveTextContent('数据源 moomoo_1m_exact_2m');
    expect(details).toHaveTextContent('版本 p0_2m_signal_v1');
    expect(details).toHaveTextContent('自选快照 sha256:abc123');
  });

  it('uses the delivery channel as truth for a ringing P1-O event', async () => {
    const opening = response().events[1];
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(
      response({
        events: [{
          ...opening,
          rule: 'opening_impulse_v2',
          text: 'TSLA 开盘累计至 09:33 顺序同向确认',
          deliveryChannel: 'telegram',
        }],
      }),
    );
    render(<IntradayAlertFeedCard pollMs={0} />);

    await waitFor(() => {
      expect(screen.getByText('TSLA 开盘累计至 09:33 顺序同向确认')).toBeInTheDocument();
    });
    expect(screen.getByText('P1-O 开盘')).toBeInTheDocument();
    expect(screen.queryByText('P1 预备')).not.toBeInTheDocument();
    expect(screen.getByText('响铃')).toBeInTheDocument();
  });

  it('labels an asynchronous P0 option supplement without pretending it is a new P0', async () => {
    const supplement = response().events[0];
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(
      response({
        events: [{
          ...supplement,
          pushId: 'p0-option-1',
          tier: 'P2',
          rule: 'p0_2m_option_update',
          text: 'TSLA P0 补充：当前暂无可执行期权快照',
          deliveryChannel: 'telegram_silent',
        }],
      }),
    );
    render(<IntradayAlertFeedCard pollMs={0} />);

    expect(await screen.findByText('P0 期权补充')).toBeInTheDocument();
    expect(screen.getByText('静默')).toBeInTheDocument();
    expect(screen.queryByText('P2 页面')).not.toBeInTheDocument();
  });

  it('labels research separately from formal P0 and shows its independent budgets', async () => {
    const base = response();
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(response({
      events: [{
        ...base.events[0],
        rule: 'p0_2m_shadow_research',
        text: 'NVDA 严格 2m 研究提醒',
        deliveryChannel: 'telegram',
        messageKey: 'research-1',
        deliveryStatus: 'succeeded',
      }],
      budgets: {
        ...base.budgets,
        researchRthUsedToday: 1,
        researchRthDailyBudget: 2,
        researchRthUsedWeek: 3,
        researchRthWeeklyBudget: 7,
        researchExtendedUsedTodayBySession: { premarket: 1, afterhours: 0 },
        researchExtendedDailyBudgetBySession: { premarket: 1, afterhours: 1 },
        researchExtendedUsedWeek: 2,
        researchExtendedWeeklyBudget: 5,
      },
    }));
    render(<IntradayAlertFeedCard pollMs={0} />);

    expect(await screen.findByText('2m 研究提醒（未验证收益）')).toBeInTheDocument();
    expect(screen.queryByText('P2 页面')).not.toBeInTheDocument();
    expect(screen.queryByText('P0 RTH 异动')).not.toBeInTheDocument();
    expect(screen.getByText(/2m 研究 RTH 1\/2 日 · 3\/7 周/)).toBeInTheDocument();
    expect(screen.getByText(/2m 研究盘外 盘前 1\/1 日 · 盘后 0\/1 日 · 2\/5 周/)).toBeInTheDocument();
    expect(screen.getByText(/P0 0\/2 日 · 1\/7 周/)).toBeInTheDocument();
    expect(screen.getByText(/Telegram 已接收/)).toBeInTheDocument();
    expect(screen.getByLabelText(
      /今日提示流口径：[\s\S]*Telegram 接收结果不等于手机响铃或已读/,
    )).toBeInTheDocument();
  });

  it.each<[PushDeliveryStatus, string]>([
    ['pending', '等待发送回执'],
    ['succeeded', 'Telegram 已接收'],
    ['failed', '发送失败'],
    ['indeterminate', '结果未确认'],
    ['expired_unconfirmed', '已过期 · 未确认'],
  ])('shows the projected %s delivery status', async (deliveryStatus, label) => {
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(response({
      events: [{
        ...response().events[1],
        deliveryStatus,
      }],
    }));
    render(<IntradayAlertFeedCard pollMs={0} />);
    expect(await screen.findByText(`· ${label}`)).toBeInTheDocument();
  });

  it('joins older API receipts by message key, with success winning over previous failures', async () => {
    const decision = response().events[1];
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(response({
      events: [
        decision,
        { ...decision, pushId: 'failed-1', recordKind: 'delivery', telegramOk: false },
        { ...decision, pushId: 'success', recordKind: 'delivery', telegramOk: true },
        { ...decision, pushId: 'failed-2', recordKind: 'delivery', telegramOk: false },
      ],
    }));
    render(<IntradayAlertFeedCard pollMs={0} />);

    expect(await screen.findByText(/Telegram 已接收/)).toBeInTheDocument();
    expect(screen.queryByText(/发送失败/)).not.toBeInTheDocument();
    expect(screen.getByTestId('push-feed-list').children).toHaveLength(1);
  });

  it('merges a failed receipt into its decision but keeps unmatched failures visible', async () => {
    const decision = response().events[1];
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(response({
      events: [
        decision,
        { ...decision, pushId: 'failed', recordKind: 'delivery', telegramOk: false },
        {
          ...decision,
          pushId: 'orphan-earlier-failed',
          recordKind: 'delivery',
          messageKey: 'missing-decision',
          telegramOk: false,
        },
        {
          ...decision,
          pushId: 'orphan-failed',
          recordKind: 'delivery',
          messageKey: 'missing-decision',
          telegramOk: false,
        },
        {
          ...decision,
          pushId: 'orphan-recovered-failed',
          recordKind: 'delivery',
          messageKey: 'recovered-decision',
          telegramOk: false,
        },
        {
          ...decision,
          pushId: 'orphan-recovered-success',
          recordKind: 'delivery',
          messageKey: 'recovered-decision',
          telegramOk: true,
        },
      ],
    }));
    render(<IntradayAlertFeedCard pollMs={0} />);

    await screen.findAllByText(/发送失败/);
    expect(screen.getByTestId('push-feed-list').children).toHaveLength(2);
    expect(screen.getAllByText(/发送失败/)).toHaveLength(2);
  });

  it('does not present page-only candidates as research or pending phone messages', async () => {
    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(response({
      events: [{ ...response().events[0], rule: 'p0_2m_candidate' }],
    }));
    render(<IntradayAlertFeedCard pollMs={0} />);

    expect(await screen.findByText('P2 页面')).toBeInTheDocument();
    expect(screen.getByText('仅页面')).toBeInTheDocument();
    expect(screen.queryByText(/等待发送回执|结果未确认|未验证收益/)).not.toBeInTheDocument();
  });

  it('surfaces fetch errors honestly and keeps the empty state honest', async () => {
    vi.mocked(fetchPushLedgerDaily).mockRejectedValue(new Error('网络断了'));
    render(<IntradayAlertFeedCard pollMs={0} />);
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('网络断了');
    });

    vi.mocked(fetchPushLedgerDaily).mockResolvedValue(
      response({ events: [] }),
    );
    render(<IntradayAlertFeedCard pollMs={0} />);
    await waitFor(() => {
      expect(
        screen.getByText(/预期空转日＝合法休息/),
      ).toBeInTheDocument();
    });
  });
});

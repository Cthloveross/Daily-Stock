import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import JournalDailyReviewPage from '../JournalDailyReviewPage';
import type {
  DailyReviewFlowResponse,
  DailyReviewSession,
} from '../../types/journalReviewFlow';

const apiMocks = vi.hoisted(() => ({
  fetchDailyReviewFlow: vi.fn(),
  postDailyReviewFlow: vi.fn(),
  postDailyReviewReveal: vi.fn(),
}));

vi.mock('../../api/journalReviewFlow', () => ({
  fetchDailyReviewFlow: apiMocks.fetchDailyReviewFlow,
  postDailyReviewFlow: apiMocks.postDailyReviewFlow,
  postDailyReviewReveal: apiMocks.postDailyReviewReveal,
}));

const baseFlow: DailyReviewFlowResponse = {
  dataState: 'ready',
  accountKey: 'default_moomoo_us',
  etDate: '2026-08-20',
  buildId: 1,
  buildKey: 'build-1',
  session: null,
  restDayCandidate: false,
  restStreak: 0,
  episodesToday: [
    {
      episodeId: 11,
      rawSymbol: 'AAA260820C00100000',
      underlying: 'AAA',
      direction: 'long',
      lifecycleStatus: 'closed',
      openedAt: '2026-08-20T13:45:00Z',
      closedAt: '2026-08-20T15:00:00Z',
      dteAtEntry: 0,
      lane: 'intraday_0dte',
      ruleId: 'V2-A',
      verdict: 'compliant',
      openedToday: true,
      closedToday: true,
      needsAck: false,
      acked: false,
    },
    {
      episodeId: 12,
      rawSymbol: 'BBB260822C00100000',
      underlying: 'BBB',
      direction: 'long',
      lifecycleStatus: 'closed',
      openedAt: '2026-08-20T14:15:00Z',
      closedAt: '2026-08-20T18:00:00Z',
      dteAtEntry: 2,
      lane: 'dte_1_3',
      ruleId: 'V2-C①',
      verdict: 'violation',
      openedToday: true,
      closedToday: true,
      needsAck: true,
      acked: false,
    },
  ],
  openPositions: [
    {
      episodeId: 13,
      rawSymbol: 'CCC260827C00100000',
      underlying: 'CCC',
      openedAt: '2026-08-20T14:30:00Z',
      dteAtEntry: 7,
      hasExitPlan: false,
      exitPlanText: '',
      exitPlanRegisteredAt: null,
    },
  ],
  processMetrics: [
    { metricId: 1, name: '推送依从率', basis: 'missing', valueRatio: null, valueText: null, numerator: null, denominator: null, reason: 'push ledger 未建（Phase B 起自动）', manualAllowed: true, manualValue: null },
    { metricId: 2, name: '结构依从率（合规车道风险占比）', basis: 'auto', valueRatio: 0.4545, valueText: null, numerator: 250, denominator: 550, reason: null, manualAllowed: false, manualValue: null },
    { metricId: 3, name: '出场规则依从率（Phase A＝出场计划在册率）', basis: 'missing', valueRatio: null, valueText: null, numerator: null, denominator: null, reason: '出场预登记自今日启用', manualAllowed: false, manualValue: null },
    { metricId: 4, name: '有效点差支付', basis: 'missing', valueRatio: null, valueText: null, numerator: null, denominator: null, reason: '需下单时 mid 快照（Phase B/C 起自动）', manualAllowed: true, manualValue: null },
    { metricId: 5, name: '费用占比（对照 1.25% 基线）', basis: 'auto', valueRatio: 0.0047, valueText: '基线 1.25%（F4）', numerator: 2.6, denominator: 550, reason: null, manualAllowed: false, manualValue: null },
    { metricId: 6, name: '决策日志完整率', basis: 'auto', valueRatio: 0, valueText: null, numerator: 0, denominator: 2, reason: null, manualAllowed: false, manualValue: null },
    { metricId: 7, name: '休息纪律', basis: 'auto', valueRatio: null, valueText: '今日有交易活动；已连续 0 个已密封休息日', numerator: 0, denominator: null, reason: null, manualAllowed: false, manualValue: null },
  ],
  mistakeVocabulary: ['freelance_no_push', 'plan_absent'],
  reveal: null,
  limitations: ['盲评优先：密封并主动揭示之前不返回任何盈亏字段'],
};

function sessionOf(overrides: Partial<DailyReviewSession>): DailyReviewSession {
  return {
    sessionId: 1,
    accountKey: 'default_moomoo_us',
    etDate: '2026-08-20',
    sessionKind: 'trading_day',
    revision: 1,
    previousSessionId: null,
    episodeBuildId: 1,
    startedAt: '2026-08-20T21:00:00Z',
    sealedAt: null,
    revealedAt: null,
    revealedAfterSeal: false,
    steps: {},
    processScores: [],
    violationAcks: [],
    note: '',
    contentSha256: 'a'.repeat(64),
    createdAt: '2026-08-20T21:00:00Z',
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/journal/review/daily']}>
      <JournalDailyReviewPage />
    </MemoryRouter>,
  );
}

describe('JournalDailyReviewPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchDailyReviewFlow.mockResolvedValue(baseFlow);
    apiMocks.postDailyReviewFlow.mockResolvedValue({
      created: true,
      idempotentReplay: false,
      session: sessionOf({}),
      reveal: null,
    });
  });

  it('walks S0→S1 and blocks the step until every violation is acknowledged', async () => {
    const { container } = renderPage();

    expect(await screen.findByText(/日终复盘 · 2026-08-20/)).toBeInTheDocument();
    // 盲评：密封揭示前页面不出现任何盈亏（$ 数字或象限）。
    expect(container.textContent).not.toMatch(/\$\d/);
    expect(screen.getByText(/2 笔新开/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '进入违规扫描 →' }));
    await screen.findByLabelText('S1 违规扫描', { selector: 'section' });
    expect(screen.getByText('V2-C① · 违规')).toBeInTheDocument();
    expect(screen.getByText('V2-A · 合规')).toBeInTheDocument();

    const advanceButton = screen.getByRole('button', { name: /还有 1 条违规待确认/ });
    expect(advanceButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText('一句话确认（必填）'), {
      target: { value: '2DTE 无聊单，不该开' },
    });
    const readyButton = screen.getByRole('button', { name: '确认并进入过程打分 →' });
    expect(readyButton).toBeEnabled();
    fireEvent.click(readyButton);
    await waitFor(() => expect(apiMocks.postDailyReviewFlow).toHaveBeenCalledWith(
      expect.objectContaining({
        etDate: '2026-08-20',
        violationAcks: [{ positionEpisodeId: 12, ackText: '2DTE 无聊单，不该开' }],
      }),
    ));
  });

  it('renders the seven process metrics with source badges and no P&L coloring', async () => {
    apiMocks.fetchDailyReviewFlow.mockResolvedValue({
      ...baseFlow,
      episodesToday: baseFlow.episodesToday.map((item) => ({ ...item, needsAck: false })),
    });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: '进入违规扫描 →' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认并进入过程打分 →' }));

    await screen.findByLabelText('S2 过程打分卡', { selector: 'section' });
    expect(screen.getByText(/1\. 推送依从率/)).toBeInTheDocument();
    expect(screen.getByText(/7\. 休息纪律/)).toBeInTheDocument();
    expect(screen.getAllByText('自动').length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText('标缺').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('45.5%')).toBeInTheDocument();
    // 手填只有 是/否/标缺 三态。
    const manualGroup = screen.getByRole('group', { name: '推送依从率 手填' });
    expect(within(manualGroup).getByRole('button', { name: '是' })).toBeInTheDocument();
    expect(within(manualGroup).getByRole('button', { name: '否' })).toBeInTheDocument();
  });

  it('registers exit plans in S3 and seals in S4; reveal appears only after sealing', async () => {
    apiMocks.fetchDailyReviewFlow.mockResolvedValue({
      ...baseFlow,
      episodesToday: baseFlow.episodesToday.map((item) => ({ ...item, needsAck: false })),
    });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: '进入违规扫描 →' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认并进入过程打分 →' }));
    fireEvent.click(await screen.findByRole('button', { name: '进入出场预登记 →' }));

    await screen.findByLabelText('S3 出场预登记', { selector: 'section' });
    expect(screen.getByText('无在册计划')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('CCC260827C00100000 出场计划'), {
      target: { value: '跌破上周低点即离场' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登记并进入封卷 →' }));
    await waitFor(() => expect(apiMocks.postDailyReviewFlow).toHaveBeenCalledWith(
      expect.objectContaining({
        exitPlans: [{ positionEpisodeId: 13, planText: '跌破上周低点即离场' }],
      }),
    ));

    await screen.findByLabelText('S4 封卷', { selector: 'section' });
    expect(screen.queryByRole('button', { name: /揭示当日结果/ })).not.toBeInTheDocument();

    // 密封后（GET 返回 sealed session）出现揭示按钮。
    apiMocks.fetchDailyReviewFlow.mockResolvedValue({
      ...baseFlow,
      session: sessionOf({ sealedAt: '2026-08-20T21:05:00Z' }),
    });
    apiMocks.postDailyReviewFlow.mockResolvedValue({
      created: true,
      idempotentReplay: false,
      session: sessionOf({ sealedAt: '2026-08-20T21:05:00Z' }),
      reveal: null,
    });
    fireEvent.click(screen.getByRole('button', { name: /密封今日复盘/ }));
    expect(await screen.findByRole('button', { name: /揭示当日结果/ })).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.postDailyReviewFlow).toHaveBeenCalledWith(
      expect.objectContaining({ seal: true }),
    ));

    // 自愿揭示：最小请求（服务端重放密封内容），四象限 + 侥幸标红 + 合计。
    apiMocks.postDailyReviewReveal.mockResolvedValue({
      created: true,
      idempotentReplay: false,
      session: sessionOf({
        revision: 2,
        sealedAt: '2026-08-20T21:05:00Z',
        revealedAt: '2026-08-20T21:06:00Z',
        revealedAfterSeal: true,
      }),
      reveal: {
        etDate: '2026-08-20',
        closedEpisodeCount: 2,
        pnlKnownCount: 2,
        totalNet: 207.4,
        quadrantCounts: { deservedWin: 1, undeservedWin: 1 },
        episodes: [
          { episodeId: 11, rawSymbol: 'AAA260820C00100000', verdict: 'compliant', quadrant: 'deserved_win', quadrantLabel: '应得的赢', pnlNet: 148.7 },
          { episodeId: 12, rawSymbol: 'BBB260822C00100000', verdict: 'violation', quadrant: 'undeserved_win', quadrantLabel: '侥幸', pnlNet: 58.7 },
        ],
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /揭示当日结果/ }));
    expect(await screen.findByTestId('reveal-block')).toBeInTheDocument();
    // 揭示走最小请求：只带 etDate + 链位，不重发内容（复核修复 1）。
    expect(apiMocks.postDailyReviewReveal).toHaveBeenCalledWith('2026-08-20', 1);
    expect(screen.getByText('+$207.40')).toBeInTheDocument();
    const luckyChip = screen.getAllByTestId('quadrant-chip')
      .find((chip) => chip.getAttribute('data-quadrant') === 'undeserved_win');
    expect(luckyChip).toBeDefined();
    expect(luckyChip?.textContent).toContain('侥幸');
    expect(luckyChip?.className).toContain('down');
  });

  it('offers one-click rest-day confirmation on a no-activity day', async () => {
    apiMocks.fetchDailyReviewFlow.mockResolvedValue({
      ...baseFlow,
      restDayCandidate: true,
      restStreak: 2,
      episodesToday: [],
      openPositions: [],
    });
    renderPage();

    expect(await screen.findByText(/今日（ET）无任何新开或平仓回合/)).toBeInTheDocument();
    expect(screen.getByText(/已连续 2 个已密封休息日/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '休息日确认（一键完成）' }));
    await waitFor(() => expect(apiMocks.postDailyReviewFlow).toHaveBeenCalledWith(
      expect.objectContaining({ sessionKind: 'rest_day', seal: true }),
    ));
  });

  it('never renders banned metrics on the daily flow (S0 and S2)', async () => {
    // 复核修复 12：负断言不只覆盖 S0，还要覆盖真正渲染指标卡的 S2。
    apiMocks.fetchDailyReviewFlow.mockResolvedValue({
      ...baseFlow,
      episodesToday: baseFlow.episodesToday.map((item) => ({ ...item, needsAck: false })),
    });
    const { container } = renderPage();
    await screen.findByText(/日终复盘 · 2026-08-20/);
    const banned = ['SQN', 'Zella', '系统质量数', '综合评分', '最优持有时间', 'MAE 止损', '建议止损位'];
    for (const term of banned) {
      expect(container.textContent ?? '').not.toContain(term);
    }
    // 走到 S2 过程打分卡（七项指标全部渲染后再扫一遍）。
    fireEvent.click(await screen.findByRole('button', { name: '进入违规扫描 →' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认并进入过程打分 →' }));
    await screen.findByLabelText('S2 过程打分卡', { selector: 'section' });
    expect(screen.getByText(/7\. 休息纪律/)).toBeInTheDocument();
    for (const term of banned) {
      expect(container.textContent ?? '').not.toContain(term);
    }
  });
});

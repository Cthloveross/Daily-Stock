import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RulesPage from '../RulesPage';
import type { PlaybookListResponse, RulesEvidenceResponse } from '../../types/journal';

const mocks = vi.hoisted(() => ({
  fetchRulesEvidence: vi.fn(),
  fetchPlaybook: vi.fn(),
}));

vi.mock('../../api/journal', () => ({
  fetchRulesEvidence: mocks.fetchRulesEvidence,
  fetchPlaybook: mocks.fetchPlaybook,
}));

function cell(overrides: Partial<RulesEvidenceResponse['priceBands'][number]> = {}) {
  return {
    label: '$2-4',
    n: 544,
    grossPct: 4.35,
    netPct: 3.12,
    feePct: 1.23,
    winRatePct: 33.0,
    reason: null,
    lower: 2,
    upper: 4,
    ...overrides,
  };
}

const EVIDENCE: RulesEvidenceResponse = {
  schemaVersion: 'journal-rules-evidence/1.0',
  dataState: 'ready',
  accountKey: 'default_moomoo_us',
  buildId: 3,
  buildKey: 'abc',
  sourceKind: 'canonical',
  computedAt: '2026-08-04T12:00:00Z',
  cleanBasisStart: '2026-04-21',
  cleanBasisReason: '干净口径原因',
  ruleSetAdoptedAt: '2026-08-05',
  sampleEpisodeCount: 1407,
  excludedBeforeCleanBasis: 241,
  excludedAggregateOrUnknownBasis: 5,
  excludedMissingPremium: 0,
  excludedNotClosedOrMissingPnl: 10,
  firstTradingDay: '2026-04-21',
  lastTradingDay: '2026-07-31',
  banner: '这一页是你自己的历史统计，不是建议；规则由这段样本推出，前向验证见 /journal 规则遵守度',
  priceBandHeadline: '同样金额买便宜合约＝张数多＝手续费按张收，$1 以下净 −14%。',
  priceBandBoundaryPolicy: 'lower_inclusive_upper_exclusive',
  priceBands: [
    cell({ label: '<$1', n: 72, feePct: 5.56, netPct: -14.25, grossPct: -8.69, lower: null, upper: 1 }),
    cell({ label: '$1-2', n: 0, feePct: null, netPct: null, grossPct: null, reason: '无样本' }),
    cell(),
  ],
  holdStyleBasis: 'compare_et_trading_day_of_close_against_open',
  dteHoldLanes: [
    {
      label: '0DTE 当日平',
      n: 556,
      grossPct: 3.44,
      netPct: 1.67,
      feePct: 1.77,
      winRatePct: 32.55,
      reason: null,
      dteMin: 0,
      dteMax: 0,
      holdStyle: 'intraday',
      excludedTopN: 5,
      exTopNGrossPct: 0.41,
      exTopNN: 551,
      exTopNReason: null,
    },
    {
      label: '4-7DTE 过夜',
      n: 65,
      grossPct: 34.13,
      netPct: 32.58,
      feePct: 1.55,
      winRatePct: 61.54,
      reason: null,
      dteMin: 4,
      dteMax: 7,
      holdStyle: 'overnight',
      excludedTopN: 5,
      exTopNGrossPct: 19.13,
      exTopNN: 60,
      exTopNReason: null,
    },
  ],
  etHours: [
    {
      label: '09:00 ET',
      n: 321,
      grossPct: 0.14,
      netPct: -1.18,
      feePct: 1.32,
      winRatePct: 30,
      reason: null,
      etHour: 9,
    },
  ],
  weekdayHeadline: '「周二/周四亏钱」不是星期效应，是合约可用性造成的合约选择问题',
  weekdays: [
    {
      label: '周一',
      n: 263,
      grossPct: 5.61,
      netPct: 3.42,
      feePct: 1.36,
      winRatePct: 35,
      reason: null,
      weekday: 0,
      zeroDteN: 141,
      dte13N: 50,
    },
    {
      label: '周二',
      n: 271,
      grossPct: -2.61,
      netPct: -3.74,
      feePct: 1.14,
      winRatePct: 25,
      reason: null,
      weekday: 1,
      zeroDteN: 36,
      dte13N: 206,
    },
  ],
  feeThreshold: {
    feePctOfPremium: 1.25,
    n: 1407,
    reason: null,
    defaultTicketUsd: 3000,
    defaultTicketsPerDay: 4,
    tradingDaysPerMonth: 21,
    note: '计算器的输入只在浏览器里，不落库；系统不知道也不猜你的账户规模。',
  },
  position: {
    framing: '你选择的参数 + 它们的含义',
    params: { ticketUsd: 3000, dailyBreakerUsd: 6000, maxConcurrent: 2 },
    severityTiers: [
      {
        label: '归零',
        definition: '单笔亏掉全部权利金（−100%）',
        lossPct: -100,
        ticketsToBreaker: 2,
        reason: null,
      },
      {
        label: '严重亏损',
        definition: '全样本单笔净回报 p5',
        lossPct: -61.86,
        ticketsToBreaker: 3.2,
        reason: null,
      },
    ],
    observedBreachDayCount: 22,
    observedTradingDayCount: 70,
    observedBreachOnePerDays: 3.2,
    observedMedianTicketsPerDay: 20,
    observedCadenceCaveat: '这个触发频率绑定的是历史下单节奏',
    historicalMaxDrawdownPct: -92.12,
    historicalDrawdownSizingPct: 10,
    drawdownCaveat: '每日熔断与累计回撤是两件不同的事',
    reason: null,
  },
  correlation: {
    tickers: [
      { ticker: 'TSLA', n: 109 },
      { ticker: 'NVDA', n: 62 },
    ],
    compliantEpisodeCount: 532,
    maxConcurrent: 2,
    note: '同时持有 2 个＝实质上是一个双倍仓位',
  },
  overnightGapNote: '每日熔断保护不了过夜仓位',
  limitations: ['全部为描述统计，不是建议', '样本只覆盖 2026-04→07 单一行情段'],
};

const PLAYBOOK: PlaybookListResponse = {
  accountKey: 'default_moomoo_us',
  candidates: [
    {
      id: 2,
      candidateKey: 'cand-2',
      accountKey: 'default_moomoo_us',
      title: 'V2-E 按合约可用性决定今天做不做日内',
      ruleText: '周二/周四没有 0DTE 时改走过夜车道。',
      sourceBucket: null,
      evidenceSnapshot: {},
      evidenceSnapshotSha256: 'b'.repeat(64),
      promoted: false,
      createdAt: '2026-08-01T00:00:00Z',
    },
  ],
  rules: [
    {
      id: 1,
      ruleKey: 'rule-1',
      lineageKey: 'lin-1',
      accountKey: 'default_moomoo_us',
      version: 1,
      status: 'active',
      promotedFromCandidateId: 1,
      promotedFromCandidateKey: 'cand-1',
      previousRuleId: null,
      title: 'V2-A 日内车道只做 0DTE',
      ruleText: 'ET 12:00 之后不开新的 0DTE。',
      evidenceSnapshot: {},
      evidenceSnapshotSha256: 'a'.repeat(64),
      isLatestVersion: true,
      createdAt: '2026-08-01T00:00:00Z',
    },
  ],
} as PlaybookListResponse;

function renderPage() {
  return render(
    <MemoryRouter>
      <RulesPage />
    </MemoryRouter>,
  );
}

describe('RulesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchRulesEvidence.mockResolvedValue(EVIDENCE);
    mocks.fetchPlaybook.mockResolvedValue(PLAYBOOK);
  });

  it('renders the standing banner and links to the compliance panel', async () => {
    renderPage();

    const note = await screen.findByLabelText('交易纪律页说明');
    expect(note).toHaveTextContent('这一页是你自己的历史统计，不是建议');
    expect(note).toHaveTextContent('前向验证见 /journal 规则遵守度');
    expect(within(note).getByRole('link')).toHaveAttribute(
      'href',
      '/journal?tab=positions',
    );
  });

  it('renders the live playbook rules and candidates with their status', async () => {
    renderPage();

    const section = await screen.findByLabelText('规则清单');
    expect(within(section).getByText('V2-A 日内车道只做 0DTE')).toBeInTheDocument();
    expect(within(section).getByText(/已采纳/)).toBeInTheDocument();
    expect(
      within(section).getByText('V2-E 按合约可用性决定今天做不做日内'),
    ).toBeInTheDocument();
    expect(within(section).getByText('候选')).toBeInTheDocument();
  });

  it('renders the contract price band table with its headline', async () => {
    renderPage();

    const section = await screen.findByLabelText('合约价格甜蜜区');
    expect(section).toHaveTextContent('$1 以下净 −14%');
    const cheap = within(section).getByRole('row', { name: /<\$1/ });
    expect(cheap).toHaveTextContent('n=72');
    expect(cheap).toHaveTextContent('5.56%');
    expect(cheap).toHaveTextContent('-14.25%');
    // 空档位给原因而不是 0。
    const empty = within(section).getByRole('row', { name: /\$1-2/ });
    expect(empty).toHaveTextContent('无样本');
  });

  it('renders the DTE x hold-style table including the ex-top-N column', async () => {
    renderPage();

    const section = await screen.findByLabelText('DTE × 持有方式');
    const overnight = within(section).getByRole('row', { name: /4-7DTE 过夜/ });
    expect(overnight).toHaveTextContent('n=65');
    expect(overnight).toHaveTextContent('+34.13%');
    expect(overnight).toHaveTextContent('61.5%');
    expect(overnight).toHaveTextContent('+19.13%');
  });

  it('renders the ET hour table with gross against the toll', async () => {
    renderPage();

    const section = await screen.findByLabelText('时段（毛口径 vs 过路费）');
    const row = within(section).getByRole('row', { name: /09:00 ET/ });
    expect(row).toHaveTextContent('n=321');
    expect(row).toHaveTextContent('1.32%');
  });

  it('renders the weekday table with 0DTE availability counts', async () => {
    renderPage();

    const section = await screen.findByLabelText('星期 × 0DTE 可用性');
    expect(section).toHaveTextContent('不是星期效应');
    const tuesday = within(section).getByRole('row', { name: /周二/ });
    expect(tuesday).toHaveTextContent('-2.61%');
    expect(tuesday).toHaveTextContent('36');
    expect(tuesday).toHaveTextContent('206');
  });

  it('renders the correlated cluster and the overnight-gap note', async () => {
    renderPage();

    const cluster = await screen.findByLabelText('相关性提醒');
    expect(cluster).toHaveTextContent('TSLA · 109');
    expect(cluster).toHaveTextContent('双倍仓位');

    const gap = await screen.findByLabelText('过夜跳空');
    expect(gap).toHaveTextContent('每日熔断保护不了过夜仓位');
  });

  it('shows the chosen position params as chosen, never as a recommendation', async () => {
    renderPage();

    const section = await screen.findByLabelText('仓位与回撤');
    expect(section).toHaveTextContent('你选择的参数 + 它们的含义');
    expect(section).toHaveTextContent('$3,000');
    expect(section).toHaveTextContent('−$6,000');
    expect(within(section).getByRole('row', { name: /归零/ })).toHaveTextContent('2.0 笔');
    // 熔断与累计回撤必须被明确区分。
    expect(section).toHaveTextContent('每日熔断与累计回撤是两件不同的事');
    expect(section).toHaveTextContent('-92.1%');
  });

  describe('fee calculator', () => {
    it('seeds itself from backend anchors and computes the monthly toll', async () => {
      renderPage();

      const section = await screen.findByLabelText('手续费门槛');
      expect(section).toHaveTextContent('1.25%');
      // 3000 × 1.25% = 37.5 → $38；38 (rounded per trade) × 4 × 21 = $3,150
      expect(section).toHaveTextContent('$38');
      expect(section).toHaveTextContent('$3,150');
    });

    it('recomputes when the user changes either input', async () => {
      renderPage();

      const section = await screen.findByLabelText('手续费门槛');
      const ticket = within(section).getByLabelText('单笔金额（美元）');
      fireEvent.change(ticket, { target: { value: '6000' } });

      // 6000 × 1.25% = $75 每笔；75 × 4 × 21 = $6,300 每月
      expect(section).toHaveTextContent('$75');
      expect(section).toHaveTextContent('$6,300');

      const perDay = within(section).getByLabelText('每天笔数');
      fireEvent.change(perDay, { target: { value: '2' } });

      // 75 × 2 × 21 = $3,150
      expect(section).toHaveTextContent('$3,150');
    });

    it('never persists the calculator inputs', async () => {
      renderPage();

      const section = await screen.findByLabelText('手续费门槛');
      const ticket = within(section).getByLabelText('单笔金额（美元）');
      fireEvent.change(ticket, { target: { value: '9000' } });

      // 只有首次加载的两个 GET，输入不触发任何写请求。
      expect(mocks.fetchRulesEvidence).toHaveBeenCalledTimes(1);
      expect(mocks.fetchRulesEvidence).toHaveBeenCalledWith();
      expect(section).toHaveTextContent('不落库');
    });
  });

  it('renders limitations verbatim', async () => {
    renderPage();

    const section = await screen.findByLabelText('口径与告警');
    expect(section).toHaveTextContent('全部为描述统计，不是建议');
    expect(section).toHaveTextContent('单一行情段');
  });

  it('reports the missing-fee exclusion count in the caveats footer', async () => {
    mocks.fetchRulesEvidence.mockResolvedValue({ ...EVIDENCE, feeUnknownCount: 3 });

    renderPage();

    const section = await screen.findByLabelText('口径与告警');
    expect(section).toHaveTextContent('缺 total_fee（不入毛口径/费率） 3');
  });

  it('marks stale tables with the last successful read time when a refresh fails', async () => {
    renderPage();
    await screen.findByLabelText('合约价格甜蜜区');

    // 第二次刷新失败：旧表仍在，但必须声明这是上次成功读取的数据。
    mocks.fetchRulesEvidence.mockRejectedValueOnce(new Error('boom'));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    expect(
      await screen.findByText(/显示的是上次成功读取（/),
    ).toBeInTheDocument();
    expect(screen.getByText(/本次刷新未成功/)).toBeInTheDocument();
    // 旧表仍然可见（配合上面的 as-of 声明，而不是无声地冒充新数据）。
    expect(screen.getByLabelText('合约价格甜蜜区')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('states an unbuilt account instead of rendering empty tables', async () => {
    mocks.fetchRulesEvidence.mockResolvedValue({
      ...EVIDENCE,
      dataState: 'not_built',
      sampleEpisodeCount: 0,
      priceBands: [],
      position: null,
      feeThreshold: null,
      correlation: null,
    });

    renderPage();

    expect(
      await screen.findByText('该账户还没有 episode build，暂无可用证据。'),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('合约价格甜蜜区')).not.toBeInTheDocument();
  });

  it('still renders the evidence tables when the playbook read fails', async () => {
    mocks.fetchPlaybook.mockRejectedValue(new Error('boom'));

    renderPage();

    expect(await screen.findByLabelText('合约价格甜蜜区')).toBeInTheDocument();
    expect(
      screen.getByText('Playbook 暂不可读，证据表不受影响。'),
    ).toBeInTheDocument();
  });
});

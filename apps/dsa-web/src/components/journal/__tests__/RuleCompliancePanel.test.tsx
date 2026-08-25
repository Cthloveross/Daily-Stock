import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RuleCompliancePanel, {
  EMPTY_SINCE_ADOPTION_TEXT,
} from '../RuleCompliancePanel';
import { fetchPersonalEdge } from '../../../api/journal';
import type {
  PersonalEdgeResponse,
  RuleComplianceLaneStat,
  RuleComplianceSlice,
  RuleComplianceStat,
} from '../../../types/journal';

vi.mock('../../../api/journal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/journal')>();
  return { ...actual, fetchPersonalEdge: vi.fn() };
});

const LIMITATION = '样本窗口仅 2026-04→07 一个市场状态（SPY 上行），隔夜多头在该状态下天然占优';

function stat(key: string, over: Partial<RuleComplianceStat> = {}): RuleComplianceStat {
  return {
    key,
    n: 0,
    risk: null,
    net: null,
    gross: null,
    grossPct: null,
    tollPct: null,
    winRate: null,
    grossPctExcludingTopN: null,
    excludingTopNCount: null,
    excludingTopNReason: '该车道在本区间无样本',
    ratioReason: '该车道在本区间无样本',
    ...over,
  };
}

function lane(
  key: string,
  verdict: RuleComplianceLaneStat['verdict'],
  ruleId: string,
  over: Partial<RuleComplianceStat> = {},
): RuleComplianceLaneStat {
  return { ...stat(key, over), verdict, ruleId };
}

/** build #3 干净口径的真实读数（n=1,407），用来锁定渲染口径。 */
function populatedSlice(): RuleComplianceSlice {
  return {
    state: 'ready',
    stateReason: null,
    startDate: '2026-04-21',
    n: 1407,
    lanes: [
      lane('intraday_0dte', 'compliant', 'V2-A', {
        n: 467, risk: 4114732, gross: 236249, grossPct: 0.057417,
        tollPct: 0.017631, winRate: 0.3383, grossPctExcludingTopN: 0.021285,
        excludingTopNCount: 462, excludingTopNReason: null, ratioReason: null,
      }),
      lane('overnight_4_7', 'compliant', 'V2-B', {
        n: 65, risk: 441113, gross: 150554, grossPct: 0.341311,
        tollPct: 0.015473, winRate: 0.6154, grossPctExcludingTopN: 0.191341,
        excludingTopNCount: 60, excludingTopNReason: null, ratioReason: null,
      }),
      lane('dte_1_3', 'violation', 'V2-C①', {
        n: 602, risk: 5071400, gross: -69513, grossPct: -0.013707,
        tollPct: 0.011979, winRate: 0.2857, grossPctExcludingTopN: -0.044254,
        excludingTopNCount: 597, excludingTopNReason: null, ratioReason: null,
      }),
      lane('bought_time_unused', 'violation', 'V2-C②', {
        n: 81, risk: 780155, gross: -29050, grossPct: -0.037236,
        tollPct: 0.006236, winRate: 0.2222, grossPctExcludingTopN: -0.087809,
        excludingTopNCount: 76, excludingTopNReason: null, ratioReason: null,
      }),
      lane('late_0dte', 'violation', 'V2-C③', {
        n: 89, risk: 814720, gross: -66716, grossPct: -0.081888,
        tollPct: 0.017764, winRate: 0.2584, grossPctExcludingTopN: -0.155351,
        excludingTopNCount: 84, excludingTopNReason: null, ratioReason: null,
      }),
      lane('other', 'uncovered', 'V2-0', {
        n: 86, risk: 1281456, gross: 20128, grossPct: 0.015707,
        tollPct: 0.004855, winRate: 0.4186, grossPctExcludingTopN: -0.032797,
        excludingTopNCount: 81, excludingTopNReason: null, ratioReason: null,
      }),
      // 样本不足 15 笔：剔尾读数必须缺席 + 原因，绝不给截断样本的数字。
      lane('unknown', 'unknown', 'V2-0', {
        n: 14, risk: 595728, gross: -19063, grossPct: -0.031999,
        tollPct: 0.001114, winRate: 0.2941,
        excludingTopNReason: '风险金额已知样本 14 笔 < 15 笔，剔除最好 5 笔后不成立',
        ratioReason: null,
      }),
    ],
    verdicts: [
      stat('compliant', {
        n: 532, risk: 4555845, gross: 386803, grossPct: 0.084898,
        tollPct: 0.017423, winRate: 0.3722, grossPctExcludingTopN: 0.051402,
        excludingTopNCount: 527, excludingTopNReason: null, ratioReason: null,
      }),
      stat('violation', {
        n: 772, risk: 6666275, gross: -165279, grossPct: -0.024793,
        tollPct: 0.011997, winRate: 0.2759, grossPctExcludingTopN: -0.048134,
        excludingTopNCount: 767, excludingTopNReason: null, ratioReason: null,
      }),
      stat('uncovered', { n: 86, grossPct: 0.015707, ratioReason: null }),
      stat('unknown', { n: 14, grossPct: -0.031999, ratioReason: null }),
    ],
  };
}

function emptyForwardSlice(): RuleComplianceSlice {
  return {
    state: 'no_episodes_since_adoption',
    stateReason:
      '规则采纳日（2026-08-05，ET）之后尚无已平仓回合进入该 build：前向样本为空是事实，不以 0 冒充读数',
    startDate: '2026-08-05',
    n: 0,
    lanes: [
      lane('intraday_0dte', 'compliant', 'V2-A'),
      lane('overnight_4_7', 'compliant', 'V2-B'),
    ],
    verdicts: [stat('compliant'), stat('violation')],
  };
}

function ready(
  sinceAdoption: RuleComplianceSlice = emptyForwardSlice(),
): PersonalEdgeResponse {
  return {
    schemaVersion: 'journal-personal-edge/1.0',
    dataState: 'ready',
    accountKey: 'default_moomoo_us',
    buildId: 3,
    buildKey: 'abc',
    sourceKind: 'csv_batch',
    computedAt: '2026-08-05T13:30:00Z',
    firstOpenedAt: null,
    lastClosedAt: null,
    closedEpisodeCount: 1653,
    excludedOpenCount: 8,
    excludedMissingPnlCount: 0,
    underlyingMinEpisodeCount: 5,
    underlyings: [],
    smallSampleUnderlyingCount: 0,
    holdTimeBuckets: [],
    holdUnknownCount: 0,
    dteBuckets: [],
    dteUnknown: null,
    monthly: [],
    monthBasis: 'opened_at_utc_minus_4_approximation',
    limitations: [],
    ruleCompliance: {
      ruleSetId: 'v2',
      adoptedAt: '2026-08-05',
      cleanBasisStart: '2026-04-21',
      cleanBasisReason: '样本＝已平仓、净盈亏与开仓现金流均已知、且由明细成交构建',
      populationN: 1407,
      excludedBeforeCleanBasisCount: 241,
      excludedAggregateOrUnknownBasisCount: 5,
      excludedMissingPremiumCount: 0,
      excludeTopN: 5,
      excludeTopNMinEpisodeCount: 15,
      intradayLaneDte: 0,
      intradayLaneEtCutoffHour: 12,
      overnightLaneMinDte: 4,
      overnightLaneMaxDte: 7,
      overnightLaneWeakEntryEtHours: [11, 13],
      allHistory: populatedSlice(),
      sinceAdoption,
      // dailyBudget 已移除：Journal 永远不含今天，该读数恒为过期。
      limitations: [LIMITATION],
    },
  };
}

function laneRow(key: string) {
  return document.querySelector(`[data-lane="${key}"]`) as HTMLElement | null;
}

describe('RuleCompliancePanel', () => {
  beforeEach(() => {
    vi.mocked(fetchPersonalEdge).mockReset();
  });

  it('renders the lane buckets with gross, toll, win rate and the exclude-top-N column', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<RuleCompliancePanel />);

    await waitFor(() => expect(laneRow('overnight_4_7')).toBeInTheDocument());
    // 唯一尾部稳健的组合：毛 +34.13%，剔除最好 5 笔仍 +19.13%。
    expect(laneRow('overnight_4_7')).toHaveTextContent('+34.13%');
    expect(laneRow('overnight_4_7')).toHaveTextContent('+19.13%');
    expect(laneRow('overnight_4_7')).toHaveTextContent('1.55%');
    expect(laneRow('overnight_4_7')).toHaveTextContent('61.5%');
    expect(laneRow('overnight_4_7')).toHaveTextContent('V2-B');
    expect(laneRow('overnight_4_7')?.dataset.verdict).toBe('compliant');

    expect(laneRow('late_0dte')).toHaveTextContent('−8.19%');
    expect(laneRow('late_0dte')).toHaveTextContent('−15.54%');
    expect(laneRow('late_0dte')?.dataset.verdict).toBe('violation');
    expect(laneRow('bought_time_unused')).toHaveTextContent('V2-C②');
    expect(laneRow('other')?.dataset.verdict).toBe('uncovered');
  });

  it('puts 合规单 vs 违规单 gross_pct front and centre', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<RuleCompliancePanel />);

    await waitFor(() =>
      expect(document.querySelector('[data-headline="compliant"]')).toBeInTheDocument(),
    );
    const compliant = document.querySelector('[data-headline="compliant"]') as HTMLElement;
    const violation = document.querySelector('[data-headline="violation"]') as HTMLElement;
    expect(compliant).toHaveTextContent('合规单');
    expect(compliant).toHaveTextContent('532 笔');
    expect(compliant).toHaveTextContent('+8.49%');
    expect(violation).toHaveTextContent('违规单');
    expect(violation).toHaveTextContent('772 笔');
    expect(violation).toHaveTextContent('−2.48%');
  });

  it('marks an under-gate exclude-top-N reading 标缺 with its reason', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<RuleCompliancePanel />);

    await waitFor(() => expect(laneRow('unknown')).toBeInTheDocument());
    expect(laneRow('unknown')).toHaveTextContent('标缺');
    const cell = laneRow('unknown')?.querySelector('[aria-label*="15 笔"]');
    expect(cell).toBeTruthy();
  });

  it('shows an honest empty line for the since-adoption slice, never a zeroed table', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<RuleCompliancePanel />);

    await waitFor(() =>
      expect(
        document.querySelector('[data-empty-slice="no_episodes_since_adoption"]'),
      ).toBeInTheDocument(),
    );
    const empty = document.querySelector(
      '[data-empty-slice="no_episodes_since_adoption"]',
    ) as HTMLElement;
    expect(empty).toHaveTextContent(EMPTY_SINCE_ADOPTION_TEXT);
    expect(empty).toHaveTextContent('不以 0 冒充读数');
    // 空切片不得渲染出任何 0.00% 的读数。
    expect(empty).not.toHaveTextContent('0.00%');
    // 采纳后区块里不出现车道表格行。
    expect(document.querySelectorAll('[data-lane="intraday_0dte"]')).toHaveLength(1);
  });

  it('renders a populated since-adoption slice once forward episodes exist', async () => {
    const forward: RuleComplianceSlice = {
      state: 'ready',
      stateReason: null,
      startDate: '2026-08-05',
      n: 3,
      lanes: [
        lane('intraday_0dte', 'compliant', 'V2-A', {
          n: 2, risk: 8000, gross: 240, grossPct: 0.03, tollPct: 0.012,
          winRate: 0.5, ratioReason: null,
        }),
        lane('dte_1_3', 'violation', 'V2-C①', {
          n: 1, risk: 4000, gross: -120, grossPct: -0.03, tollPct: 0.012,
          winRate: 0, ratioReason: null,
        }),
      ],
      verdicts: [
        stat('compliant', { n: 2, grossPct: 0.03, tollPct: 0.012, ratioReason: null }),
        stat('violation', { n: 1, grossPct: -0.03, tollPct: 0.012, ratioReason: null }),
      ],
    };
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready(forward));
    render(<RuleCompliancePanel />);

    await waitFor(() =>
      expect(screen.getByText(/采纳后（前向验证）/)).toBeInTheDocument(),
    );
    expect(
      document.querySelector('[data-empty-slice="no_episodes_since_adoption"]'),
    ).toBeNull();
    // 两个切片各渲染一次同名车道行。
    expect(document.querySelectorAll('[data-lane="intraday_0dte"]')).toHaveLength(2);
    expect(document.querySelectorAll('[data-headline="compliant"]')).toHaveLength(2);
  });

  it('states the mechanical basis, the clean-basis exclusions and carries limitations verbatim', async () => {
    vi.mocked(fetchPersonalEdge).mockResolvedValue(ready());
    render(<RuleCompliancePanel />);

    await waitFor(() => expect(screen.getByText(/纯机械/)).toBeInTheDocument());
    expect(screen.getByText(/无从知道进场当时的意图/)).toBeInTheDocument();
    const basis = document.querySelector('[data-basis="clean"]') as HTMLElement;
    expect(basis).toHaveTextContent('1,407');
    expect(basis).toHaveTextContent('241');
    expect(basis).toHaveTextContent('2026-04-21');
    // 界面披露策略（2026-08-15）：limitations 原文不再常驻底部列表，逐字
    // 收进标题旁 ⓘ 的 tooltip/aria-label——隐藏 ≠ 删除。
    expect(
      screen.queryByText(new RegExp(LIMITATION.slice(0, 12))),
    ).not.toBeInTheDocument();
    const hint = screen.getByLabelText(new RegExp(LIMITATION.slice(0, 12)));
    expect(hint.getAttribute('aria-label')).toContain(LIMITATION);
    // 「规则由这段样本推出，因此它必然好看」必须写明。
    expect(screen.getByText(/规则由这段样本推出，因此它必然好看/)).toBeInTheDocument();
  });

  it('says the block is 标缺 when the endpoint omits it, and explains not_built', async () => {
    const withoutBlock = ready();
    withoutBlock.ruleCompliance = null;
    vi.mocked(fetchPersonalEdge).mockResolvedValue(withoutBlock);
    const { unmount } = render(<RuleCompliancePanel />);
    await waitFor(() =>
      expect(screen.getByText(/规则遵守度读数标缺（端点未返回该区块）/)).toBeInTheDocument(),
    );
    unmount();

    vi.mocked(fetchPersonalEdge).mockResolvedValue({
      ...withoutBlock,
      dataState: 'not_built',
    });
    render(<RuleCompliancePanel />);
    expect(await screen.findByText(/还没有 Episode 构建/)).toBeInTheDocument();
  });
});

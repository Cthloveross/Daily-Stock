import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ReviewInsightsPanel from '../ReviewInsightsPanel';
import { createPlaybookCandidate, fetchReviewInsights } from '../../../api/journal';
import type { ReviewInsightBucket, ReviewInsightsResponse } from '../../../types/journal';

vi.mock('../../../api/journal', () => ({
  fetchReviewInsights: vi.fn(),
  createPlaybookCandidate: vi.fn(),
}));

const fetchInsightsMock = vi.mocked(fetchReviewInsights);
const createCandidateMock = vi.mocked(createPlaybookCandidate);

const baseResponse: ReviewInsightsResponse = {
  schemaVersion: 'journal-review-insights/1.0',
  dataState: 'ready',
  buildId: 7,
  buildKey: 'build-key-7',
  sourceKind: 'csv_batch',
  accountKey: 'default_moomoo_us',
  generatedAt: '2026-07-31T12:00:00Z',
  thresholds: { minEpisodeCount: 10, minDistinctTradingDayCount: 5 },
  totalEpisodeCount: 4545,
  annotatedEpisodeCount: 3,
  unreviewed: { episodeCount: 4542, distinctTradingDayCount: 180 },
  buckets: [],
};

const bucket = (overrides: Partial<ReviewInsightBucket>): ReviewInsightBucket => ({
  groupKind: 'tag',
  groupValue: 'momentum',
  direction: 'LONG',
  boundaryPolicy: 'assumed_or_censored',
  episodeCount: 2,
  distinctTradingDayCount: 2,
  reviewCompletedCount: 1,
  verifiedEpisodeCount: 0,
  verifiedDistinctTradingDayCount: 0,
  conditionalEpisodeCount: 2,
  stats: null,
  statsGate: { eligible: false, reason: 'no_verified_pnl_episodes' },
  ...overrides,
});

describe('ReviewInsightsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the not-built empty state', async () => {
    fetchInsightsMock.mockResolvedValue({
      ...baseResponse,
      dataState: 'not_built',
      buildId: null,
      buildKey: null,
      sourceKind: null,
      generatedAt: null,
      totalEpisodeCount: 0,
      annotatedEpisodeCount: 0,
      unreviewed: null,
      buckets: [],
    });
    render(<ReviewInsightsPanel />);

    expect(await screen.findByText(/还没有 Episode 构建/)).toBeInTheDocument();
    expect(screen.queryByText(/未复盘/)).not.toBeInTheDocument();
  });

  it('renders the unreviewed count row and the labeled-annotation empty hint', async () => {
    fetchInsightsMock.mockResolvedValue({ ...baseResponse, buckets: [] });
    render(<ReviewInsightsPanel />);

    expect(await screen.findByText(/未复盘/)).toBeInTheDocument();
    expect(screen.getByText(/4,542 笔 · 180 个交易日/)).toBeInTheDocument();
    expect(screen.getByText(/仅计数，永不显示盈亏统计/)).toBeInTheDocument();
    expect(screen.getByText(/尚无带标签的复盘标注/)).toBeInTheDocument();
    expect(
      screen.getByText(/观察到的模式 ≠ 已验证规则；保存候选与晋升规则都是你的显式操作，系统永不自动晋升/),
    ).toBeInTheDocument();
  });

  it('shows counts only below the sample threshold, without any ratio', async () => {
    fetchInsightsMock.mockResolvedValue({
      ...baseResponse,
      buckets: [
        bucket({
          boundaryPolicy: 'verified',
          episodeCount: 9,
          distinctTradingDayCount: 5,
          verifiedEpisodeCount: 9,
          verifiedDistinctTradingDayCount: 5,
          conditionalEpisodeCount: 0,
          statsGate: { eligible: false, reason: 'below_sample_threshold' },
        }),
      ],
    });
    render(<ReviewInsightsPanel />);

    expect(await screen.findByText(/标签 · momentum/)).toBeInTheDocument();
    expect(screen.getByText(/9 笔 · 5 个交易日 · 已复盘 1/)).toBeInTheDocument();
    expect(
      screen.getByText(/样本不足（<10 笔或 <5 个交易日），仅显示计数/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/胜率/)).not.toBeInTheDocument();
  });

  it('renders stats above the threshold and labels the conditional split', async () => {
    fetchInsightsMock.mockResolvedValue({
      ...baseResponse,
      buckets: [
        bucket({
          boundaryPolicy: 'verified',
          episodeCount: 12,
          distinctTradingDayCount: 6,
          reviewCompletedCount: 12,
          verifiedEpisodeCount: 10,
          verifiedDistinctTradingDayCount: 5,
          conditionalEpisodeCount: 2,
          stats: {
            winRate: '0.6000',
            avgPnl: '4.0000000000',
            sumPnl: '40.0000000000',
            winCount: 6,
            lossCount: 4,
            breakevenCount: 0,
          },
          statsGate: { eligible: true, reason: null },
        }),
        bucket({
          groupKind: 'error_type',
          groupValue: 'late_entry',
          direction: 'SHORT',
          episodeCount: 1,
          distinctTradingDayCount: 1,
          conditionalEpisodeCount: 1,
        }),
      ],
    });
    render(<ReviewInsightsPanel />);

    expect(await screen.findByText(/胜率 60%（6 胜 \/ 4 负）/)).toBeInTheDocument();
    expect(screen.getByText(/合计 \+\$40\.00 · 单笔均值 \+\$4\.00/)).toBeInTheDocument();
    expect(screen.getByText(/条件性 P&L 2 笔 · 不参与统计/)).toBeInTheDocument();
    expect(screen.getByText('边界已验证')).toBeInTheDocument();
    expect(screen.getByText(/错误类型 · late_entry/)).toBeInTheDocument();
    expect(screen.getByText('做空')).toBeInTheDocument();
    expect(screen.getByText('边界假设/截断')).toBeInTheDocument();
    expect(screen.getByText(/无已验证 P&L 样本，仅显示计数/)).toBeInTheDocument();
  });

  it('surfaces the request error', async () => {
    fetchInsightsMock.mockRejectedValue(new Error('network down'));
    render(<ReviewInsightsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('gates the save-as-candidate form until both title and rule text exist', async () => {
    fetchInsightsMock.mockResolvedValue({ ...baseResponse, buckets: [bucket({})] });
    render(<ReviewInsightsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '保存为候选' }));
    const submit = screen.getByRole('button', { name: '保存候选' });
    expect(submit).toBeDisabled();
    expect(
      screen.getByText(/候选与规则不会影响系统评分或榜单/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('候选标题'), { target: { value: '动量候选' } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('规则描述'), { target: { value: '   ' } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('规则描述'), { target: { value: '只做计划内触发。' } });
    expect(submit).toBeEnabled();
    expect(createCandidateMock).not.toHaveBeenCalled();
  });

  it('submits the bucket echo explicitly and reports the saved candidate', async () => {
    fetchInsightsMock.mockResolvedValue({ ...baseResponse, buckets: [bucket({})] });
    createCandidateMock.mockResolvedValue({
      dataState: 'ready',
      created: true,
      idempotentReplay: false,
      candidate: {
        schemaVersion: 'playbook-candidate/1.0',
        id: 1,
        candidateKey: 'c'.repeat(64),
        accountKey: 'default_moomoo_us',
        title: '动量候选',
        ruleText: '只做计划内触发。',
        sourceBucket: {
          groupKind: 'tag',
          groupValue: 'momentum',
          direction: 'LONG',
          boundaryPolicy: 'assumed_or_censored',
        },
        evidenceSnapshot: {},
        evidenceSnapshotSha256: 'e'.repeat(64),
        promoted: false,
        createdAt: '2026-08-01T12:00:00Z',
      },
    });
    const onCandidateSaved = vi.fn();
    render(<ReviewInsightsPanel onCandidateSaved={onCandidateSaved} />);

    fireEvent.click(await screen.findByRole('button', { name: '保存为候选' }));
    fireEvent.change(screen.getByLabelText('候选标题'), { target: { value: '动量候选' } });
    fireEvent.change(screen.getByLabelText('规则描述'), { target: { value: '只做计划内触发。' } });
    fireEvent.click(screen.getByRole('button', { name: '保存候选' }));

    await waitFor(() => {
      expect(createCandidateMock).toHaveBeenCalledWith({
        title: '动量候选',
        ruleText: '只做计划内触发。',
        sourceBucket: {
          groupKind: 'tag',
          groupValue: 'momentum',
          direction: 'LONG',
          boundaryPolicy: 'assumed_or_censored',
        },
      });
    });
    expect(await screen.findByText(/已保存为候选，可在下方 Playbook 面板显式晋升/)).toBeInTheDocument();
    expect(onCandidateSaved).toHaveBeenCalledTimes(1);
  });

  it('surfaces a save conflict without hiding the form', async () => {
    fetchInsightsMock.mockResolvedValue({ ...baseResponse, buckets: [bucket({})] });
    createCandidateMock.mockRejectedValue(new Error('conflict'));
    render(<ReviewInsightsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '保存为候选' }));
    fireEvent.change(screen.getByLabelText('候选标题'), { target: { value: '动量候选' } });
    fireEvent.change(screen.getByLabelText('规则描述'), { target: { value: '只做计划内触发。' } });
    fireEvent.click(screen.getByRole('button', { name: '保存候选' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: '保存候选' })).toBeInTheDocument();
  });
});

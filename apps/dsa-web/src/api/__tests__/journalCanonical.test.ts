import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createCanonicalPositionEpisodeBuild,
  createPositionEpisodeAiReview,
  fetchCanonicalEpisodeBuildPreview,
  fetchPositionEpisodeDetail,
  fetchPositionEpisodes,
} from '../journal';

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../index', () => ({ default: apiMocks }));

describe('journal canonical episode API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps explicit list and detail reads pinned to one build', async () => {
    apiMocks.get.mockResolvedValue({
      data: { data_state: 'ready', total: 0, page: 1, per_page: 50, items: [] },
    });

    await fetchPositionEpisodes({ buildId: 9, underlying: 'NVDA', caseFocus: 'largest_fee' });
    await fetchPositionEpisodeDetail(41, 9);

    expect(apiMocks.get).toHaveBeenNthCalledWith(
      1,
      '/api/v1/journal/v2/position-episodes',
      expect.objectContaining({
        params: expect.objectContaining({ build_id: 9, underlying: 'NVDA', case_focus: 'largest_fee' }),
      }),
    );
    expect(apiMocks.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/journal/v2/position-episodes/41',
      { params: { build_id: 9 } },
    );
  });

  it('requests AI review for one immutable episode build', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        data_state: 'ready',
        episode_id: 41,
        build_id: 9,
        generated_at: '2026-07-22T00:00:00Z',
        analysis_markdown: '## Review',
        evidence_markdown: '## Evidence',
        model_analysis_markdown: '## Inference',
        market_context: { benchmark: 'SPY', provenance: [] },
        warnings: [],
      },
    });

    const response = await createPositionEpisodeAiReview(41, 9);
    expect(response.analysisMarkdown).toBe('## Review');
    expect(response.evidenceMarkdown).toBe('## Evidence');
    expect(response.modelAnalysisMarkdown).toBe('## Inference');
    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/position-episodes/41/ai-review',
      undefined,
      { params: { build_id: 9, enhance: true }, timeout: 45000 },
    );
  });

  it('sends only non-empty review context with explicit snake_case fields', async () => {
    apiMocks.post.mockResolvedValue({ data: { analysis_markdown: '## Review' } });

    await createPositionEpisodeAiReview(41, 9, false, {
      setupThesis: '  回踩 8 EMA 后延续  ',
      entryTrigger: '收回前高',
      invalidationPlan: '   ',
      positionRationale: '半仓控制波动风险',
      exitReason: '跌破失效点',
      postTradeReflection: '加仓需要新触发',
    });

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/position-episodes/41/ai-review',
      {
        user_context: {
          setup_thesis: '回踩 8 EMA 后延续',
          entry_trigger: '收回前高',
          position_rationale: '半仓控制波动风险',
          exit_reason: '跌破失效点',
          post_trade_reflection: '加仓需要新触发',
        },
      },
      { params: { build_id: 9, enhance: false }, timeout: 45000 },
    );
  });

  it('maps the preview response and sends immutable confirmation keys', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        data_state: 'ready',
        canonical_set_id: 1,
        canonical_set_sha256: 'a'.repeat(64),
        build_key: 'b'.repeat(64),
        source_batch_ids: [2, 3],
        source_event_count: 12,
        aggregate_order_event_count: 2,
        detailed_fill_event_count: 10,
        planned_position_episode_count: 4,
        planned_open_episode_count: 1,
        planned_closed_episode_count: 3,
        fee_conserved: true,
        requires_assumed_flat_acceptance: true,
        default_position_episode_count: 3,
        episode_count_delta: 1,
        default_will_change: false,
        confirm_allowed: true,
        warnings: [],
      },
    });
    apiMocks.post.mockResolvedValue({ data: { data_state: 'ready', build: { id: 9 } } });

    const preview = await fetchCanonicalEpisodeBuildPreview();
    expect(preview.canonicalSetId).toBe(1);
    expect(preview.plannedPositionEpisodeCount).toBe(4);

    await createCanonicalPositionEpisodeBuild({
      canonicalSetId: 1,
      canonicalSetSha256: 'a'.repeat(64),
      buildKey: 'b'.repeat(64),
      acceptAssumedFlat: true,
    });
    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/episode-builds/canonical',
      {
        canonical_set_id: 1,
        canonical_set_sha256: 'a'.repeat(64),
        build_key: 'b'.repeat(64),
        accept_assumed_flat: true,
      },
      { params: { account_key: undefined } },
    );
  });
});

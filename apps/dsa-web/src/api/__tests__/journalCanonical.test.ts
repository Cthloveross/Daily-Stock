import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  activateEpisodeBuild,
  createCanonicalPositionEpisodeBuild,
  createPositionEpisodeAiReview,
  fetchCanonicalEpisodeBuildPreview,
  fetchEpisodeBuildActivation,
  fetchLatestPositionEpisodeReviewAnnotation,
  fetchPositionEpisodeDetail,
  fetchPositionEpisodeReviewAnnotationHistory,
  fetchPositionEpisodes,
  savePositionEpisodeReviewAnnotation,
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

    await fetchPositionEpisodes({
      buildId: 9,
      underlying: 'NVDA',
      caseFocus: 'largest_fee',
      reviewStatus: 'in_progress',
    });
    await fetchPositionEpisodeDetail(41, 9);

    expect(apiMocks.get).toHaveBeenNthCalledWith(
      1,
      '/api/v1/journal/v2/position-episodes',
      expect.objectContaining({
        params: expect.objectContaining({
          build_id: 9,
          underlying: 'NVDA',
          case_focus: 'largest_fee',
          review_status: 'in_progress',
        }),
      }),
    );
    expect(apiMocks.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/journal/v2/position-episodes/41',
      { params: { build_id: 9 } },
    );
  });

  it('loads and saves user-authored review annotations with explicit snake_case fields', async () => {
    apiMocks.get
      .mockResolvedValueOnce({
        data: {
          data_state: 'ready',
          annotation: {
            id: 71,
            episode_build_id: 9,
            position_episode_id: 41,
            revision: 2,
            review_status: 'in_progress',
            setup_thesis: '趋势延续',
            tags: ['早盘'],
            error_types: ['追高'],
            created_at: '2026-07-22T09:30:00Z',
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          data_state: 'ready',
          annotations: [{ id: 71, revision: 2, review_status: 'in_progress' }],
        },
      });
    apiMocks.post.mockResolvedValue({
      data: {
        data_state: 'ready',
        created: true,
        idempotent_replay: false,
        annotation: { id: 72, revision: 3, review_status: 'completed' },
      },
    });

    const latest = await fetchLatestPositionEpisodeReviewAnnotation(41, 9);
    expect(latest.annotation?.episodeBuildId).toBe(9);
    expect(latest.annotation?.errorTypes).toEqual(['追高']);
    const history = await fetchPositionEpisodeReviewAnnotationHistory(41, 9);
    expect(history.items).toHaveLength(1);
    await savePositionEpisodeReviewAnnotation(41, {
      buildId: 9,
      reviewStatus: 'completed',
      setupThesis: '  趋势延续  ',
      entryTrigger: '收回前高',
      invalidationPlan: '',
      positionRationale: '半仓',
      exitReason: '达到目标',
      postTradeReflection: '等待新触发',
      tags: [' 早盘 ', '早盘'],
      errorTypes: ['追高'],
    });

    expect(apiMocks.get).toHaveBeenNthCalledWith(
      1,
      '/api/v1/journal/v2/position-episodes/41/review-annotations/latest',
      { params: { build_id: 9 } },
    );
    expect(apiMocks.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/journal/v2/position-episodes/41/review-annotations',
      { params: { build_id: 9 } },
    );
    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/position-episodes/41/review-annotations',
      {
        build_id: 9,
        review_status: 'completed',
        setup_thesis: '趋势延续',
        entry_trigger: '收回前高',
        invalidation_plan: '',
        position_rationale: '半仓',
        exit_reason: '达到目标',
        post_trade_reflection: '等待新触发',
        tags: ['早盘'],
        error_types: ['追高'],
      },
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
      acceptGroupFeeScope: false,
    });
    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/episode-builds/canonical',
      {
        canonical_set_id: 1,
        canonical_set_sha256: 'a'.repeat(64),
        build_key: 'b'.repeat(64),
        accept_assumed_flat: true,
        accept_group_fee_scope: false,
      },
      { params: { account_key: undefined } },
    );
  });

  it('maps activation state and sends the complete compare-and-swap payload', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        account_key: 'default_moomoo_us',
        selection_source: 'csv_fallback',
        current_activation_id: null,
        current_activation_sequence: null,
        current_build_id: 7,
        current_build_key: 'c'.repeat(64),
        canonical_set_id: null,
        previous_activation_id: null,
        previous_build_id: null,
        activated_at: null,
      },
    });
    apiMocks.post.mockResolvedValue({
      data: {
        activation_id: 12,
        activation_key: 'd'.repeat(64),
        duplicate: false,
        state: {
          account_key: 'default_moomoo_us',
          selection_source: 'activation',
          current_activation_id: 12,
          current_activation_sequence: 1,
          current_build_id: 9,
          current_build_key: 'b'.repeat(64),
          canonical_set_id: 1,
          previous_activation_id: null,
          previous_build_id: 7,
          activated_at: '2026-07-30T14:00:00Z',
        },
        message: 'canonical Episode build 9 activated',
        trading_action_performed: false,
      },
    });

    const state = await fetchEpisodeBuildActivation();
    expect(apiMocks.get).toHaveBeenCalledWith(
      '/api/v1/journal/v2/episode-builds/activation',
    );
    expect(state.selectionSource).toBe('csv_fallback');
    expect(state.currentBuildId).toBe(7);

    const response = await activateEpisodeBuild(9, {
      expectedBuildKey: 'b'.repeat(64),
      expectedCurrentActivationId: state.currentActivationId,
      expectedCurrentBuildId: state.currentBuildId,
      acceptAssumedFlat: true,
      acceptGroupFeeScope: false,
    });
    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/episode-builds/9/activate',
      {
        expected_build_key: 'b'.repeat(64),
        expected_current_activation_id: null,
        expected_current_build_id: 7,
        accept_assumed_flat: true,
        accept_group_fee_scope: false,
      },
    );
    expect(response.activationId).toBe(12);
    expect(response.state.currentActivationSequence).toBe(1);
    expect(response.tradingActionPerformed).toBe(false);
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  confirmJournalRefresh,
  fetchJournalRefreshStatus,
  previewJournalRefresh,
} from '../journal';

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../index', () => ({ default: apiMocks }));

describe('journal server-owned refresh API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('maps the separate broker, evidence, build, and active-view watermarks', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        refresh_enabled: true,
        refresh_configured: true,
        freshness_state: 'current',
        pending_stage: 'none',
        expected_complete_through: '2026-07-29T20:00:00Z',
        broker_queried_through: '2026-07-29T20:00:00Z',
        latest_fill_at: '2026-07-29T15:12:00Z',
        evidence_published_through: '2026-07-29T20:00:00Z',
        publication_recorded_at: '2026-07-30T01:30:00Z',
        active_selection_source: 'activation',
        active_build_id: 14,
        active_source_through: '2026-07-29T20:00:00Z',
        account_bound: true,
      },
    });

    const status = await fetchJournalRefreshStatus();

    expect(apiMocks.get).toHaveBeenCalledWith('/api/v1/journal/v2/refresh-status');
    expect(status.brokerQueriedThrough).toBe('2026-07-29T20:00:00Z');
    expect(status.latestFillAt).toBe('2026-07-29T15:12:00Z');
    expect(status.evidencePublishedThrough).toBe('2026-07-29T20:00:00Z');
    expect(status.activeBuildId).toBe(14);
  });

  it('requests a bounded zero-write preview and maps overlap versus incremental tail', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        artifact_id: 31,
        artifact_key: 'artifact-31',
        expires_at: '2026-07-30T03:00:00Z',
        source: {
          retrieval_complete: true,
          coverage_complete: true,
          has_activity: true,
          broker_queried_through: '2026-07-29T20:00:00Z',
          latest_fill_at: '2026-07-29T15:12:00Z',
          order_observations: 14,
          fill_observations: 18,
          fee_observations: 12,
        },
        plan: {
          preview_key: 'p'.repeat(64),
          confirm_allowed: true,
          scope_is_full_batch: false,
          coverage: {
            overlap_api_only_orders: 0,
            incremental_api_only_orders: 4,
          },
        },
        evidence_written: false,
        trading_action_performed: false,
      },
    });

    const preview = await previewJournalRefresh(7);

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/refreshes/preview',
      { overlap_days: 7 },
      { timeout: 210000 },
    );
    expect(preview.evidenceWritten).toBe(false);
    expect(preview.tradingActionPerformed).toBe(false);
    expect(preview.plan.scopeIsFullBatch).toBe(false);
    expect(preview.plan.coverage.overlapApiOnlyOrders).toBe(0);
    expect(preview.plan.coverage.incrementalApiOnlyOrders).toBe(4);
  });

  it('confirms the exact frozen artifact with an explicit scope acknowledgement', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        artifact_id: 31,
        publication: {
          publication_id: 8,
          broker_queried_through: '2026-07-29T20:00:00Z',
          evidence_published_through: '2026-07-29T20:00:00Z',
          recorded_at: '2026-07-30T01:30:00Z',
        },
        imported: {
          canonical_set_id: 5,
          trading_action_performed: false,
        },
        trading_action_performed: false,
      },
    });

    const result = await confirmJournalRefresh(31, 'p'.repeat(64), true);

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/refreshes/31/confirm',
      {
        preview_key: 'p'.repeat(64),
        acknowledge_partial_window: true,
      },
      { timeout: 60000 },
    );
    expect(result.publication.publicationId).toBe(8);
    expect(result.imported.canonicalSetId).toBe(5);
    expect(result.tradingActionPerformed).toBe(false);
  });
});

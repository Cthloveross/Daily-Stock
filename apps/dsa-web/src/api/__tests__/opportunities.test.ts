import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  evaluateOpportunitySnapshot,
  fetchDailyOpportunities,
  fetchOpportunityLearningSummary,
  fetchOpportunityOptionEvents,
  fetchOpportunitySnapshots,
  freezeOpportunitySnapshot,
} from '../opportunities';
import { sessionCache } from '../../utils/sessionCache';

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../index', () => ({ default: apiMocks }));

describe('daily opportunity API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionCache.clearAll();
  });

  it('keeps the base candidate request on a bounded timeout independent of option enrichment', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        schema_version: 'daily-opportunity/1.0',
        run_id: 'run-1',
        universe: ['NVDA'],
        candidates: [],
      },
    });

    const result = await fetchDailyOpportunities(['NVDA'], 10);

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/opportunities/daily',
      { symbols: ['NVDA'], limit: 10, refresh: false },
      { timeout: 35_000 },
    );
    expect(result.runId).toBe('run-1');
  });

  it('passes an explicit refresh flag so the server also bypasses its short TTL', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        schema_version: 'daily-opportunity/1.0',
        run_id: 'run-refresh',
        universe: ['NVDA'],
        candidates: [],
      },
    });

    await fetchDailyOpportunities(['NVDA'], 10, { refresh: true });

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/opportunities/daily',
      { symbols: ['NVDA'], limit: 10, refresh: true },
      { timeout: 35_000 },
    );
  });

  it('does not replace a cached usable list with an all-blocked transient refresh', async () => {
    const candidate = {
      candidate_id: 'candidate-aapl',
      ticker: 'AAPL',
      research_state: 'watch_only',
      readiness: [{ domain: 'daily_history', state: 'ready' }],
    };
    apiMocks.post
      .mockResolvedValueOnce({
        data: {
          schema_version: 'daily-opportunity/1.0',
          run_id: 'run-good',
          universe: ['AAPL'],
          candidates: [candidate],
        },
      })
      .mockResolvedValueOnce({
        data: {
          schema_version: 'daily-opportunity/1.0',
          run_id: 'run-transient-blocked',
          universe: ['AAPL'],
          candidates: [{
            ...candidate,
            research_state: 'blocked',
            readiness: [{ domain: 'daily_history', state: 'unavailable' }],
          }],
        },
      });

    await fetchDailyOpportunities(['AAPL'], 10);
    await fetchDailyOpportunities(['AAPL'], 10, { refresh: true });
    const cached = await fetchDailyOpportunities(['AAPL'], 10);

    expect(cached.runId).toBe('run-good');
    expect(apiMocks.post).toHaveBeenCalledTimes(2);
  });
});

describe('opportunity option-event API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionCache.clearAll();
  });

  it('normalizes supported symbols, sends the explicit row limit, and maps snake_case deeply', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        schema_version: 'option-event/1.0',
        market_date_et: '2026-07-22',
        generated_at: '2026-07-22T14:16:00+00:00',
        items: [{
          ticker: 'AAPL',
          state: 'ready',
          source: 'moomoo_openapi',
          fetched_at: '2026-07-22T14:16:00+00:00',
          event_as_of: '2026-07-22T10:15:31-04:00',
          all_count: 18,
          events: [{
            event_id: 'evt-1',
            option_code: 'US.AAPL260724C00200000',
            owner_code: 'US.AAPL',
            symbol: 'AAPL 260724 200C',
            fill_time: '2026-07-22T10:15:31-04:00',
            ticker_type: 'BUY',
            order_types: ['SWEEP'],
            strategy_type: 'SINGLE_LEG',
          }],
        }],
      },
    });

    const result = await fetchOpportunityOptionEvents([' us.aapl ', '0700', 'AAPL'], 5);

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/opportunities/option-events',
      { symbols: ['AAPL'], limit_per_symbol: 5 },
      { timeout: 30_000 },
    );
    expect(result.items[0].eventAsOf).toBe('2026-07-22T10:15:31-04:00');
    expect(result.items[0].events[0]).toMatchObject({
      eventId: 'evt-1',
      ownerCode: 'US.AAPL',
      fillTime: '2026-07-22T10:15:31-04:00',
      orderTypes: ['SWEEP'],
      strategyType: 'SINGLE_LEG',
    });
  });

  it('deduplicates the same in-flight request', async () => {
    let resolveRequest!: (value: { data: Record<string, unknown> }) => void;
    apiMocks.post.mockReturnValue(new Promise((resolve) => {
      resolveRequest = resolve;
    }));

    const first = fetchOpportunityOptionEvents(['NVDA'], 5);
    const second = fetchOpportunityOptionEvents(['NVDA'], 5);
    expect(apiMocks.post).toHaveBeenCalledTimes(1);

    resolveRequest({
      data: {
        schema_version: 'option-event/1.0',
        market_date_et: '2026-07-22',
        generated_at: '2026-07-22T14:16:00+00:00',
        items: [],
      },
    });
    await expect(first).resolves.toMatchObject({ schemaVersion: 'option-event/1.0' });
    await expect(second).resolves.toMatchObject({ schemaVersion: 'option-event/1.0' });
  });
});

describe('opportunity outcome-learning API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('freezes normalized symbols and maps immutable reference progress', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        schema_version: 'opportunity-snapshot/1.0',
        snapshot_key: 'snapshot/2026-07-22',
        market_date_et: '2026-07-22',
        source_run_id: 'run-1',
        frozen_at: '2026-07-22T12:05:00Z',
        signal_version: 'daily_completed_bars_v1',
        candidate_count: 2,
        eligible_candidate_count: 1,
        validation_eligible: true,
        eligibility_reasons: [],
        outcome_progress: [{
          horizon_sessions: 5,
          eligible_count: 1,
          mature_count: 0,
          pending_count: 1,
          partial_count: 0,
        }],
        idempotent_replay: true,
      },
    });

    const snapshot = await freezeOpportunitySnapshot([' nvda ', 'NVDA', 'aapl'], 10);

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/opportunities/snapshots/freeze',
      { symbols: ['NVDA', 'AAPL'], limit: 10 },
      { timeout: 120_000 },
    );
    expect(snapshot).toMatchObject({
      snapshotKey: 'snapshot/2026-07-22',
      eligibleCandidateCount: 1,
      idempotentReplay: true,
      outcomeProgress: [{ horizonSessions: 5, pendingCount: 1 }],
    });
  });

  it('reads snapshot history and a gated learning summary without changing weights', async () => {
    apiMocks.get
      .mockResolvedValueOnce({
        data: {
          schema_version: 'opportunity-snapshot-list/1.0',
          items: [{ snapshot_key: 'snapshot-1', outcome_progress: [] }],
        },
      })
      .mockResolvedValueOnce({
        data: {
          schema_version: 'opportunity-learning/1.0',
          generated_at: '2026-07-22T12:06:00Z',
          strategy_state: 'collecting',
          auto_adjustment: false,
          minimum_summary_samples: 10,
          minimum_investigation_samples: 20,
          horizons: [{
            horizon_sessions: 20,
            mature_count: 3,
            distinct_signal_sessions: 3,
            context_hit_count: 2,
            context_miss_count: 1,
            neutral_count: 0,
            non_directional_count: 0,
            context_hit_rate_percent: null,
            summary_visible: false,
            investigation_ready: false,
          }],
          limitations: [],
        },
      });

    const snapshots = await fetchOpportunitySnapshots(10);
    const summary = await fetchOpportunityLearningSummary();

    expect(apiMocks.get).toHaveBeenNthCalledWith(
      1,
      '/api/v1/opportunities/snapshots',
      { params: { limit: 10 }, timeout: 30_000 },
    );
    expect(apiMocks.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/opportunities/learning-summary',
      { timeout: 30_000 },
    );
    expect(snapshots.items[0].snapshotKey).toBe('snapshot-1');
    expect(summary).toMatchObject({
      autoAdjustment: false,
      minimumSummarySamples: 10,
      horizons: [{ horizonSessions: 20, contextHitRatePercent: null }],
    });
  });

  it('encodes the immutable snapshot key when evaluating mature outcomes', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        schema_version: 'opportunity-outcome-evaluation/1.0',
        snapshot_key: 'snapshot/2026-07-22',
        evaluated_at: '2026-07-29T12:00:00Z',
        candidate_count: 1,
        inserted_outcomes: 1,
        already_recorded: 0,
        pending_horizons: 1,
        data_gap_horizons: 0,
        outcome_progress: [],
        message: 'updated',
      },
    });

    const result = await evaluateOpportunitySnapshot('snapshot/2026-07-22');

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/opportunities/snapshots/snapshot%2F2026-07-22/evaluate',
      undefined,
      { timeout: 120_000 },
    );
    expect(result).toMatchObject({ insertedOutcomes: 1, pendingHorizons: 1 });
  });
});

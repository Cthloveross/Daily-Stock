import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  evaluateOpportunitySnapshot,
  fetchDailyOpportunities,
  fetchPremarketCycleStatus,
  fetchPremarketUniverse,
  fetchOpportunityLearningSummary,
  fetchOpportunityOptionContext,
  fetchOpportunityOptionEvents,
  fetchOpportunityOptionOverview,
  fetchOpportunityOptionWalls,
  fetchOpportunitySnapshots,
  freezeOpportunitySnapshot,
  runPremarketCycle,
  savePremarketUniverse,
} from '../opportunities';
import { sessionCache } from '../../utils/sessionCache';

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
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

  it('keeps every optional research stage on its own bounded timeout', async () => {
    apiMocks.post
      .mockResolvedValueOnce({
        data: {
          schema_version: 'option-context/1.0',
          market_date_et: '2026-07-22',
          generated_at: '2026-07-22T12:00:00+00:00',
          items: [],
        },
      })
      .mockResolvedValueOnce({
        data: {
          schema_version: 'option-overview/1.0',
          market_date_et: '2026-07-22',
          generated_at: '2026-07-22T12:00:00+00:00',
          items: [],
        },
      })
      .mockResolvedValueOnce({
        data: {
          schema_version: 'option-wall/1.1',
          market_date_et: '2026-07-22',
          generated_at: '2026-07-22T12:00:00+00:00',
          items: [],
        },
      });

    await fetchOpportunityOptionContext(['NVDA']);
    await fetchOpportunityOptionOverview(['NVDA']);
    await fetchOpportunityOptionWalls(['NVDA'], 0, 45);

    expect(apiMocks.post).toHaveBeenNthCalledWith(
      1,
      '/api/v1/opportunities/option-context',
      { symbols: ['NVDA'] },
      { timeout: 30_000 },
    );
    expect(apiMocks.post).toHaveBeenNthCalledWith(
      2,
      '/api/v1/opportunities/option-overview',
      { symbols: ['NVDA'] },
      { timeout: 30_000 },
    );
    expect(apiMocks.post).toHaveBeenNthCalledWith(
      3,
      '/api/v1/opportunities/option-walls',
      { symbols: ['NVDA'], dte_min: 0, dte_max: 45 },
      { timeout: 45_000 },
    );
  });
});

describe('canonical premarket API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses the canonical limit-5 contracts, bounded timeouts, and deep camel-case mapping', async () => {
    apiMocks.post
      .mockResolvedValueOnce({
        data: {
          schema_version: 'canonical-premarket-cycle/1.0',
          cycle_key: 'cycle-status',
          state: 'waiting_window',
          quality: 'unknown',
          market_date_et: '2026-07-23',
          previous_session: '2026-07-22',
          window_start_at: '2026-07-23T12:45:00+00:00',
          latest_start_at: '2026-07-23T13:18:00+00:00',
          window_end_at: '2026-07-23T13:20:00+00:00',
          quality_reasons: [],
          stages: [],
          regime_quality: {},
          run: null,
          snapshot: null,
        },
      })
      .mockResolvedValueOnce({
        data: {
          schema_version: 'canonical-premarket-cycle/1.0',
          cycle_key: 'cycle-run',
          state: 'published',
          quality: 'degraded',
          market_date_et: '2026-07-23',
          quality_reasons: ['supporting_regime_partial'],
          stages: [{
            name: 'quality_gate',
            state: 'degraded',
            completed_at: '2026-07-23T12:50:00+00:00',
          }],
          regime_quality: { spy: 'ready' },
          run: { run_id: 'canonical-run' },
          snapshot: {
            snapshot_key: 'snapshot-canonical',
            qualification: {
              assessment_key: 'opqa-canonical',
              policy_version: 'opportunity_qualification_v2',
              publication_state: 'canonical_published',
              analysis_quality_state: 'degraded',
              assessed_at: '2026-07-23T12:50:00+00:00',
              reason_codes: ['supporting_regime_partial'],
              tracks: [{
                track_key: 'raw_underlying_path_v1',
                qualified_count: 1,
                excluded_count: 0,
                unverified_count: 0,
                prospective_count: 1,
                retrospective_count: 0,
                observation_ready_count: 1,
              }],
            },
          },
        },
      });

    const status = await fetchPremarketCycleStatus(5);
    const published = await runPremarketCycle(5);

    expect(apiMocks.post).toHaveBeenNthCalledWith(
      1,
      '/api/v1/opportunities/premarket/status',
      { symbols: [], limit: 5, manual: false },
      { timeout: 30_000 },
    );
    expect(apiMocks.post).toHaveBeenNthCalledWith(
      2,
      '/api/v1/opportunities/premarket/run',
      { symbols: [], limit: 5, manual: true },
      { timeout: 35_000 },
    );
    expect(status).toMatchObject({
      cycleKey: 'cycle-status',
      previousSession: '2026-07-22',
      windowStartAt: '2026-07-23T12:45:00+00:00',
      latestStartAt: '2026-07-23T13:18:00+00:00',
    });
    expect(published).toMatchObject({
      cycleKey: 'cycle-run',
      qualityReasons: ['supporting_regime_partial'],
      stages: [{ completedAt: '2026-07-23T12:50:00+00:00' }],
      run: { runId: 'canonical-run' },
      snapshot: {
        snapshotKey: 'snapshot-canonical',
        qualification: {
          assessmentKey: 'opqa-canonical',
          analysisQualityState: 'degraded',
          reasonCodes: ['supporting_regime_partial'],
          tracks: [{
            trackKey: 'raw_underlying_path_v1',
            qualifiedCount: 1,
            observationReadyCount: 1,
          }],
        },
      },
    });
  });

  it('deduplicates matching status and run requests while each request is in flight', async () => {
    let resolveStatus!: (value: { data: Record<string, unknown> }) => void;
    apiMocks.post.mockReturnValueOnce(new Promise((resolve) => {
      resolveStatus = resolve;
    }));
    const firstStatus = fetchPremarketCycleStatus(5);
    const secondStatus = fetchPremarketCycleStatus(5);
    expect(apiMocks.post).toHaveBeenCalledTimes(1);
    resolveStatus({ data: { cycle_key: 'status', state: 'waiting_window' } });
    await Promise.all([firstStatus, secondStatus]);

    let resolveRun!: (value: { data: Record<string, unknown> }) => void;
    apiMocks.post.mockReturnValueOnce(new Promise((resolve) => {
      resolveRun = resolve;
    }));
    const firstRun = runPremarketCycle(5);
    const secondRun = runPremarketCycle(5);
    expect(apiMocks.post).toHaveBeenCalledTimes(2);
    resolveRun({ data: { cycle_key: 'run', state: 'published' } });
    await Promise.all([firstRun, secondRun]);
  });

  it('reads and explicitly versions the server-owned research pool', async () => {
    apiMocks.get.mockResolvedValueOnce({
      data: {
        schema_version: 'premarket-research-universe/1.0',
        configured: false,
        universe_version_key: null,
        source: 'stock_list_fallback',
        symbols: ['NVDA'],
        limit: 5,
        created_at: null,
        duplicate: false,
        message: '这是建议，尚未启用。',
      },
    });
    apiMocks.put.mockResolvedValueOnce({
      data: {
        schema_version: 'premarket-research-universe/1.0',
        configured: true,
        universe_version_key: 'opru_v1',
        source: 'persisted',
        symbols: ['NVDA', 'AAPL'],
        limit: 5,
        created_at: '2026-07-23T13:00:00+00:00',
        duplicate: false,
        message: '已保存。',
      },
    });

    const suggestion = await fetchPremarketUniverse();
    const saved = await savePremarketUniverse(
      [' nvda ', 'HK.00700', 'US.AAPL', 'NVDA'],
      5,
    );

    expect(apiMocks.get).toHaveBeenCalledWith(
      '/api/v1/opportunities/premarket/universe',
      { timeout: 30_000 },
    );
    expect(apiMocks.put).toHaveBeenCalledWith(
      '/api/v1/opportunities/premarket/universe',
      { symbols: ['NVDA', 'AAPL'], limit: 5 },
      { timeout: 30_000 },
    );
    expect(suggestion).toMatchObject({
      configured: false,
      universeVersionKey: null,
      source: 'stock_list_fallback',
    });
    expect(saved).toMatchObject({
      configured: true,
      universeVersionKey: 'opru_v1',
      createdAt: '2026-07-23T13:00:00+00:00',
    });
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
        analysis_quality_eligible: true,
        analysis_quality_reasons: [],
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
      analysisQualityEligible: true,
      analysisQualityReasons: [],
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
          minimum_summary_samples: 20,
          minimum_investigation_samples: 20,
          automatic_maintenance_enabled: true,
          maintenance_policy_version: 'xnys-close-qualified-raw-path-v2',
          latest_maintenance: null,
          horizons: [{
            horizon_sessions: 20,
            mature_count: 3,
            distinct_signal_sessions: 3,
            directional_sample_count: 3,
            context_hit_count: null,
            context_miss_count: null,
            neutral_count: null,
            non_directional_count: null,
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
    expect(snapshots.items[0].analysisQualityEligible).toBeUndefined();
    expect(snapshots.items[0].analysisQualityReasons).toBeUndefined();
    expect(snapshots.items[0].qualification).toBeUndefined();
    expect(summary).toMatchObject({
      autoAdjustment: false,
      minimumSummarySamples: 20,
      automaticMaintenanceEnabled: true,
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

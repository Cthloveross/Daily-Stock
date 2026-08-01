import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  confirmFuturePositionEpisodeBuild,
  confirmPositionSnapshot,
  fetchFuturePositionEpisodePreview,
  fetchLatestPositionSnapshot,
  fetchPositionSnapshotContinuity,
  previewPositionSnapshot,
} from '../journalPositions';

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../index', () => ({ default: apiMocks }));

const rawPosition = {
  symbol: 'US.NVDA260918C00150000',
  underlying: 'NVDA',
  expiry: '2026-09-18',
  strike: '150',
  option_right: 'C',
  position_side: 'LONG',
  quantity_contracts: '2',
  signed_quantity_contracts: '2',
  can_sell_quantity_contracts: '2',
  currency: 'USD',
  contract_multiplier: '100',
  basis: 'moomoo_market_snapshot_three_field_consensus',
  context: {
    cost_price: '2.5',
    cost_price_valid: true,
    average_cost: '2.4',
    diluted_cost: '2.3',
    semantics: 'broker_display_context_only',
  },
};

const rawObservation = {
  observed_from: '2026-07-30T13:00:00Z',
  observed_through: '2026-07-30T13:00:02Z',
  operation_completed_at: '2026-07-30T13:00:03Z',
  broker_as_of: null,
  stable: true,
  stability: {
    stable: true,
    comparison_basis: 'instrument_position_side_signed_quantity_contracts',
    added_symbols: [],
    removed_symbols: [],
    changed_symbols: [],
  },
  retrieval_complete: true,
  position_snapshot_complete: true,
  contract_spec_status: 'complete',
  analysis_ready: true,
  content_state: 'positions',
  position_count: 1,
  total_contracts: '2',
  future_anchor_candidate: true,
  historical_opening_proven: false,
  filter_counts: {
    first_total_rows: 2,
    second_total_rows: 3,
    filtered_non_us_rows: 0,
    filtered_non_option_rows: 0,
    filtered_zero_quantity_rows: 2,
    contract_spec_count: 1,
    validation_issue_count: 0,
  },
  positions: [rawPosition],
  hashes: {
    positions_sha256: 'a'.repeat(64),
    snapshot_sha256: 'b'.repeat(64),
  },
};

const rawSnapshot = {
  snapshot_id: 17,
  snapshot_key: 'c'.repeat(64),
  recorded_at: '2026-07-30T13:01:00Z',
  acknowledged_future_only: true,
  freshness: {
    status: 'fresh',
    age_seconds: 60,
    future_skew_seconds: 0,
    threshold_seconds: 1800,
    evaluated_at: '2026-07-30T13:01:03Z',
    age_basis: 'operation_completed_at',
    time_semantics: 'locally_bracketed_observation_not_broker_as_of',
  },
  account_binding_status: 'matches_latest_publication',
  publication_anchor_status: 'latest',
  is_current: true,
  observation: rawObservation,
};

const rawLatestCurrentState = {
  latest_is_current: true,
  latest_account_binding_matches: true,
  latest_publication_anchor_matches: true,
};

const rawContinuityReady = {
  policy_version: 'position-snapshot-continuity/1.0',
  status: 'ready',
  ready_for_episode_build: true,
  fence_key: 'f'.repeat(64),
  account_key: 'moomoo-margin-3705',
  snapshot_id: 17,
  snapshot_key: 'c'.repeat(64),
  anchor_publication_id: 31,
  anchor_publication_key: 'a'.repeat(64),
  target_publication_id: 32,
  target_publication_key: 'e'.repeat(64),
  target_canonical_set_id: 9,
  target_canonical_set_sha256: 'd'.repeat(64),
  boundary_at: '2026-07-30T13:00:03Z',
  guard_started_at: '2026-07-30T13:00:00Z',
  guard_completed_at: '2026-07-30T13:00:03Z',
  publication_ids: [32],
  source_batch_ids: [41],
  counts: {
    snapshot_member_count: 1,
    chain_publication_count: 1,
    chain_batch_count: 1,
    target_order_count: 2,
    target_fill_count: 2,
    guard_fill_count: 0,
    pre_boundary_fill_count: 0,
    post_boundary_fill_count: 2,
    aggregate_changed_pre_boundary_count: 0,
    straddling_order_count: 0,
    straddling_group_count: 0,
    unbacked_post_boundary_count: 0,
    option_identity_mismatch_count: 0,
  },
  reasons: [],
  evidence_written: false,
  trading_action_performed: false,
};

const rawFutureEpisodePreview = {
  projection_name: 'position_snapshot_fenced_canonical_projection',
  projection_version: '1.0.0',
  continuity_policy_version: 'position-snapshot-continuity/1.0',
  account_key: 'moomoo-margin-3705',
  scope: 'moomoo_live_us_options',
  fence_key: 'f'.repeat(64),
  snapshot_id: 17,
  snapshot_key: 'c'.repeat(64),
  target_publication_id: 32,
  target_publication_key: 'e'.repeat(64),
  target_canonical_set_id: 9,
  target_canonical_set_sha256: 'd'.repeat(64),
  canonical_source_batch_ids: [41],
  publication_source_batch_ids: [41],
  boundary_at: '2026-07-30T13:00:03Z',
  source_cutoff_at: '2026-07-31T20:00:00Z',
  builder_name: 'canonical_position_episode_builder',
  builder_version: '1.0.0',
  builder_config_sha256: '1'.repeat(64),
  evidence_set_sha256: '2'.repeat(64),
  planned_build_key: '3'.repeat(64),
  opening_boundary_policy: 'complete_snapshot',
  max_multiplier_proof_residual: '0',
  source_known_fee_total: '1.20',
  accounted_known_fee_total: '1.20',
  retained_execution_group_fee_total: '0',
  fee_conservation_by_currency: { USD: { source_known: '1.20', accounted: '1.20' } },
  fee_conserved: true,
  headline_realized_pnl_gross: '50',
  headline_total_fee: '1.20',
  headline_realized_pnl_net: '48.80',
  counts: {
    canonical_member_count: 5,
    opening_position_count: 1,
    opening_contract_count: '2.0000000000',
    post_boundary_order_event_count: 1,
    post_boundary_fill_event_count: 2,
    supporting_order_count: 1,
    excluded_non_option_event_count: 0,
    execution_group_count: 0,
    source_event_count: 3,
    planned_position_episode_count: 2,
    planned_open_episode_count: 1,
    planned_closed_episode_count: 1,
    left_censored_episode_count: 1,
    right_censored_episode_count: 1,
    headline_episode_count: 1,
    headline_excluded_episode_count: 1,
    group_fee_affected_episode_count: 0,
  },
  warnings: ['opening_positions_remain_left_censored'],
  preview_only: true,
  business_data_written: false,
  episode_build_written: false,
  activation_changed: false,
  evidence_written: false,
  trading_action_performed: false,
};

const rawFutureBuildConfirmRequest = {
  expectedFenceKey: 'f'.repeat(64),
  expectedBuildKey: '3'.repeat(64),
  expectedEvidenceSetSha256: '2'.repeat(64),
  acceptLeftCensoredOpenings: true,
  acceptGroupFeeScope: false,
} as const;

const rawFutureBuildConfirmResponse = {
  data_state: 'ready',
  duplicate: false,
  build: {
    id: 77,
    build_key: '3'.repeat(64),
    builder_name: 'canonical_position_episode_builder',
    builder_version: '1.0.0',
    status: 'partial',
    source_batch_ids: [41],
    source_kind: 'position_snapshot_fenced_canonical',
    canonical_set_id: 9,
    canonical_set_sha256: 'd'.repeat(64),
    source_window_start: '2026-07-30T13:00:03Z',
    source_cutoff_at: '2026-07-31T20:00:00Z',
    position_episode_count: 2,
    unresolved_evidence_count: 0,
    completeness_score: '0.95',
    opening_boundary_policy: 'complete_snapshot',
    assumed_flat_unverified: false,
    execution_group_count: 0,
    group_fee_affected_episode_count: 0,
    retained_execution_group_fee_total: '0',
    fee_conservation_by_currency: {},
    leg_fee_attribution_complete: true,
    partial_reasons: ['left_censored_episodes'],
    recorded_at: '2026-07-31T20:05:00Z',
  },
  reconciliation: {
    status: 'passed',
    scope: 'window',
    partial_window: true,
    window_start: '2026-07-30T13:00:03Z',
    window_end: '2026-07-31T20:00:00Z',
    matched_order_count: 1,
    total_order_count: 1,
  },
  summary: {
    total_episode_count: 2,
    open_episode_count: 1,
    closed_episode_count: 1,
    boundary_unverified_episode_count: 0,
    left_censored_episode_count: 1,
    incomplete_episode_count: 0,
    aggregate_only_episode_count: 0,
    group_fee_affected_episode_count: 0,
    headline_pnl: {
      eligible_closed_count: 1,
      excluded_episode_count: 1,
      eligibility_rule: 'closed_net_known_boundary_verified_not_left_censored_complete',
      exclusion_counts: { left_censored: 1 },
      realized_pnl_gross: '50',
      total_fee: '1.20',
      realized_pnl_net: '48.80',
    },
    conditional_pnl: {
      basis: 'closed_known_cash_flows_under_opening_boundary_policy',
      opening_boundary_policy: 'complete_snapshot',
      count: 1,
      included_in_headline: false,
    },
  },
  snapshot_fence: {
    source_kind: 'position_snapshot_fenced_canonical',
    snapshot_id: 17,
    snapshot_key: 'c'.repeat(64),
    fence_key: 'f'.repeat(64),
    continuity_policy_version: 'position-snapshot-continuity/1.0',
    target_publication_id: 32,
    target_publication_key: 'e'.repeat(64),
    target_canonical_set_id: 9,
    target_canonical_set_sha256: 'd'.repeat(64),
    boundary_at: '2026-07-30T13:00:03Z',
    source_cutoff_at: '2026-07-31T20:00:00Z',
    projection_name: 'position_snapshot_fenced_canonical_projection',
    projection_version: '1.0.0',
    link_key: '4'.repeat(64),
  },
  message: 'future position episode build appended: 2 episodes. The default position review remains unchanged; use the explicit build ID to inspect this result.',
  activation_changed: false,
  trading_action_performed: false,
};

describe('current option-position snapshot API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('normalizes latest current-position evidence and broker cost context', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        enabled: true,
        configured: true,
        continuity_ready: true,
        continuity_reason: null,
        ...rawLatestCurrentState,
        latest: rawSnapshot,
        trading_action_performed: false,
      },
    });

    const response = await fetchLatestPositionSnapshot();

    expect(apiMocks.get).toHaveBeenCalledWith(
      '/api/v1/journal/v2/position-snapshots/latest',
    );
    expect(response.latest?.observation.positions[0]).toMatchObject({
      symbol: 'US.NVDA260918C00150000',
      basis: 'moomoo_market_snapshot_three_field_consensus',
      brokerCostContext: {
        costPrice: '2.5',
        averageCost: '2.4',
      },
    });
    expect(response.latest?.observation.filterCounts.filteredZeroQuantityRows).toBe(2);
    expect(response.latest?.observation.historicalOpeningProven).toBe(false);
    expect(response.latest?.observation.positionSnapshotComplete).toBe(true);
    expect(response.latest?.freshness).toMatchObject({
      status: 'fresh',
      ageSeconds: 60,
      thresholdSeconds: 1800,
      ageBasis: 'operation_completed_at',
    });
    expect(response.latest?.isCurrent).toBe(true);
    expect(response.latestIsCurrent).toBe(true);
    expect(response.latest?.observation.stability).toEqual({
      stable: true,
      comparisonBasis: 'instrument_position_side_signed_quantity_contracts',
      addedSymbols: [],
      removedSymbols: [],
      changedSymbols: [],
    });
  });

  it('requests a zero-write preview and maps the frozen observation', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        artifact_id: 31,
        artifact_key: 'artifact-31',
        preview_key: 'p'.repeat(64),
        expires_at: '2026-07-30T13:10:00Z',
        confirm_allowed: true,
        blocking_reasons: [],
        warnings: ['filtered_zero_quantity_rows:2'],
        observation: rawObservation,
        evidence_written: false,
        trading_action_performed: false,
      },
    });

    const response = await previewPositionSnapshot();

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/position-snapshots/preview',
      {},
      { timeout: 180000 },
    );
    expect(response.artifactId).toBe(31);
    expect(response.artifactKey).toBe('artifact-31');
    expect(response.previewKey).toBe('p'.repeat(64));
    expect(response.evidenceWritten).toBe(false);
    expect(response.observation.positionCount).toBe(1);
  });

  it('confirms the exact preview with the mandatory future-only acknowledgement', async () => {
    apiMocks.post.mockResolvedValue({
      data: {
        snapshot: rawSnapshot,
        duplicate: false,
        trading_action_performed: false,
      },
    });

    const response = await confirmPositionSnapshot(31, 'p'.repeat(64), true);

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/position-snapshots/31/confirm',
      {
        preview_key: 'p'.repeat(64),
        acknowledge_future_only: true,
      },
    );
    expect(response.snapshot.id).toBe(17);
    expect(response.duplicate).toBe(false);
    expect(response.tradingActionPerformed).toBe(false);
  });

  it('normalizes a zero-write Episode continuity-fence assessment', async () => {
    apiMocks.get.mockResolvedValue({
      data: rawContinuityReady,
    });

    const response = await fetchPositionSnapshotContinuity();

    expect(apiMocks.get).toHaveBeenCalledWith(
      '/api/v1/journal/v2/position-snapshots/episode-boundary-readiness',
    );
    expect(response).toMatchObject({
      status: 'ready',
      readyForEpisodeBuild: true,
      snapshotId: 17,
      targetCanonicalSetId: 9,
      evidenceWritten: false,
      tradingActionPerformed: false,
    });
    expect(response.counts.postBoundaryFillCount).toBe(2);
  });

  it('normalizes an exact fence-bound future Episode preview', async () => {
    apiMocks.get.mockResolvedValue({ data: rawFutureEpisodePreview });

    const response = await fetchFuturePositionEpisodePreview('f'.repeat(64));

    expect(apiMocks.get).toHaveBeenCalledWith(
      '/api/v1/journal/v2/episode-builds/position-snapshot/preview',
      { params: { expected_fence_key: 'f'.repeat(64) } },
    );
    expect(response.counts).toMatchObject({
      openingContractCount: '2.0000000000',
      plannedPositionEpisodeCount: 2,
      plannedOpenEpisodeCount: 1,
      plannedClosedEpisodeCount: 1,
    });
    expect(response.previewOnly).toBe(true);
    expect(response.businessDataWritten).toBe(false);
    expect(response.episodeBuildWritten).toBe(false);
    expect(response.activationChanged).toBe(false);
  });

  it.each([
    'fence_key',
    'snapshot_key',
    'target_publication_key',
    'target_canonical_set_sha256',
    'builder_config_sha256',
    'evidence_set_sha256',
    'planned_build_key',
  ])('rejects a future preview with malformed %s', async (field) => {
    apiMocks.get.mockResolvedValue({
      data: { ...rawFutureEpisodePreview, [field]: 'A'.repeat(64) },
    });

    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /64 位小写 SHA-256/,
    );
  });

  it('rejects unsafe future preview counts and decimal strings', async () => {
    apiMocks.get.mockResolvedValueOnce({
      data: {
        ...rawFutureEpisodePreview,
        counts: { ...rawFutureEpisodePreview.counts, source_event_count: -1 },
      },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /sourceEventCount 不是非负安全整数/,
    );

    apiMocks.get.mockResolvedValueOnce({
      data: {
        ...rawFutureEpisodePreview,
        counts: { ...rawFutureEpisodePreview.counts, opening_contract_count: 'NaN' },
      },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /openingContractCount 不是非负有限十进数字符串/,
    );
  });

  it('rejects inconsistent lifecycle and Headline totals', async () => {
    apiMocks.get.mockResolvedValueOnce({
      data: {
        ...rawFutureEpisodePreview,
        counts: { ...rawFutureEpisodePreview.counts, planned_open_episode_count: 2 },
      },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /总回合数与 open\/closed 不一致/,
    );

    apiMocks.get.mockResolvedValueOnce({
      data: {
        ...rawFutureEpisodePreview,
        counts: { ...rawFutureEpisodePreview.counts, headline_episode_count: 2 },
      },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /Headline 纳入\/排除计数不一致/,
    );
  });

  it('rejects inverted windows, event drift, impossible censored counts, and non-finite money', async () => {
    apiMocks.get.mockResolvedValueOnce({
      data: { ...rawFutureEpisodePreview, source_cutoff_at: '2026-07-29T20:00:00Z' },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /sourceCutoffAt 早于 boundaryAt/,
    );

    apiMocks.get.mockResolvedValueOnce({
      data: {
        ...rawFutureEpisodePreview,
        counts: { ...rawFutureEpisodePreview.counts, source_event_count: 4 },
      },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /来源事件计数不一致/,
    );

    apiMocks.get.mockResolvedValueOnce({
      data: {
        ...rawFutureEpisodePreview,
        counts: { ...rawFutureEpisodePreview.counts, left_censored_episode_count: 3 },
      },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /leftCensoredEpisodeCount 超过计划总回合数/,
    );

    apiMocks.get.mockResolvedValueOnce({
      data: { ...rawFutureEpisodePreview, headline_realized_pnl_net: 'Infinity' },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /headlineRealizedPnlNet 不是有限十进数字符串或 null/,
    );
  });

  it.each([
    'business_data_written',
    'episode_build_written',
    'activation_changed',
    'evidence_written',
    'trading_action_performed',
  ])('rejects a future preview that violates %s=false', async (field) => {
    apiMocks.get.mockResolvedValue({
      data: { ...rawFutureEpisodePreview, [field]: true },
    });

    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /未满足只读安全契约/,
    );
  });

  it('rejects a future preview that is not preview-only or fee-conserved', async () => {
    apiMocks.get.mockResolvedValueOnce({
      data: { ...rawFutureEpisodePreview, preview_only: false },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /preview_only 未满足面向未来的证据契约/,
    );

    apiMocks.get.mockResolvedValueOnce({
      data: { ...rawFutureEpisodePreview, fee_conserved: false },
    });
    await expect(fetchFuturePositionEpisodePreview('f'.repeat(64))).rejects.toThrow(
      /fee_conserved 未满足面向未来的证据契约/,
    );
  });

  it.each([
    { field: 'fence_key', value: null },
    { field: 'fence_key', value: 'F'.repeat(64) },
    { field: 'snapshot_id', value: 0 },
    { field: 'snapshot_key', value: 'not-a-snapshot-hash' },
    { field: 'target_publication_id', value: null },
    { field: 'target_publication_key', value: 'not-a-publication-hash' },
    { field: 'target_canonical_set_id', value: -1 },
    { field: 'target_canonical_set_sha256', value: 'D'.repeat(64) },
    { field: 'boundary_at', value: '2026-07-30T13:00:03' },
  ])('rejects ready continuity with an invalid frozen $field', async ({ field, value }) => {
    apiMocks.get.mockResolvedValue({
      data: {
        ...rawContinuityReady,
        [field]: value,
      },
    });

    await expect(fetchPositionSnapshotContinuity()).rejects.toThrow(
      new RegExp(String(field)),
    );
  });

  it('rejects a continuity assessment that calls a blocked fence ready', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        policy_version: 'position-snapshot-continuity/1.0',
        status: 'blocked',
        ready_for_episode_build: true,
        counts: {},
        reasons: [],
        evidence_written: false,
        trading_action_performed: false,
      },
    });

    await expect(fetchPositionSnapshotContinuity()).rejects.toThrow(
      /连续性门禁状态自相矛盾/,
    );
  });

  it('rejects a non-ready continuity assessment that carries a usable fence', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        ...rawContinuityReady,
        status: 'blocked',
        ready_for_episode_build: false,
      },
    });

    await expect(fetchPositionSnapshotContinuity()).rejects.toThrow(
      /尚未 ready 却返回 fence/,
    );
  });

  it('rejects an unknown continuity status instead of silently mapping it to blocked', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        ...rawContinuityReady,
        status: 'future',
        ready_for_episode_build: false,
        fence_key: null,
      },
    });

    await expect(fetchPositionSnapshotContinuity()).rejects.toThrow(
      /连续性门禁状态无效/,
    );
  });

  it('rejects a response that tries to claim historical-opening proof', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        enabled: true,
        configured: true,
        continuity_ready: true,
        ...rawLatestCurrentState,
        latest: {
          ...rawSnapshot,
          observation: {
            ...rawObservation,
            historical_opening_proven: true,
          },
        },
        trading_action_performed: false,
      },
    });

    await expect(fetchLatestPositionSnapshot()).rejects.toThrow(
      /historical_opening_proven 未满足只读安全契约/,
    );
  });

  it('rejects a confirmed snapshot without an explicit future-only acknowledgement', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        enabled: true,
        configured: true,
        continuity_ready: true,
        ...rawLatestCurrentState,
        latest: {
          ...rawSnapshot,
          acknowledged_future_only: undefined,
        },
        trading_action_performed: false,
      },
    });

    await expect(fetchLatestPositionSnapshot()).rejects.toThrow(
      /acknowledged_future_only 未满足面向未来的证据契约/,
    );
  });

  it('keeps an unavailable contract multiplier as null', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        enabled: true,
        configured: true,
        continuity_ready: true,
        ...rawLatestCurrentState,
        latest: {
          ...rawSnapshot,
          observation: {
            ...rawObservation,
            positions: [{ ...rawPosition, contract_multiplier: null }],
          },
        },
        trading_action_performed: false,
      },
    });

    const response = await fetchLatestPositionSnapshot();
    expect(response.latest?.observation.positions[0].contractMultiplier).toBeNull();
  });

  it('keeps stale or old-account evidence explicitly non-current', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        enabled: true,
        configured: true,
        continuity_ready: true,
        continuity_reason: 'account_binding_mismatch',
        latest_is_current: false,
        latest_account_binding_matches: false,
        latest_publication_anchor_matches: true,
        latest: {
          ...rawSnapshot,
          freshness: {
            ...rawSnapshot.freshness,
            status: 'stale',
            age_seconds: 7200,
          },
          account_binding_status: 'mismatch',
          is_current: false,
        },
        trading_action_performed: false,
      },
    });

    const response = await fetchLatestPositionSnapshot();
    expect(response.latestIsCurrent).toBe(false);
    expect(response.latestAccountBindingMatches).toBe(false);
    expect(response.latest?.freshness.status).toBe('stale');
    expect(response.latest?.accountBindingStatus).toBe('mismatch');
    expect(response.latest?.isCurrent).toBe(false);
  });

  it('rejects a response that calls stale evidence current', async () => {
    apiMocks.get.mockResolvedValue({
      data: {
        enabled: true,
        configured: true,
        continuity_ready: true,
        ...rawLatestCurrentState,
        latest: {
          ...rawSnapshot,
          freshness: { ...rawSnapshot.freshness, status: 'stale', age_seconds: 7200 },
        },
        trading_action_performed: false,
      },
    });

    await expect(fetchLatestPositionSnapshot()).rejects.toThrow(
      /状态自相矛盾/,
    );
  });

  it('confirms a formal future build with the exact echoed preview identity', async () => {
    apiMocks.post.mockResolvedValue({ data: rawFutureBuildConfirmResponse });

    const response = await confirmFuturePositionEpisodeBuild(rawFutureBuildConfirmRequest);

    expect(apiMocks.post).toHaveBeenCalledWith(
      '/api/v1/journal/v2/episode-builds/position-snapshot',
      {
        expected_fence_key: 'f'.repeat(64),
        expected_build_key: '3'.repeat(64),
        expected_evidence_set_sha256: '2'.repeat(64),
        accept_left_censored_openings: true,
        accept_group_fee_scope: false,
      },
    );
    expect(response.dataState).toBe('ready');
    expect(response.duplicate).toBe(false);
    expect(response.build).toMatchObject({
      id: 77,
      buildKey: '3'.repeat(64),
      sourceKind: 'position_snapshot_fenced_canonical',
      positionEpisodeCount: 2,
    });
    expect(response.snapshotFence).toMatchObject({
      sourceKind: 'position_snapshot_fenced_canonical',
      snapshotId: 17,
      fenceKey: 'f'.repeat(64),
      linkKey: '4'.repeat(64),
    });
    expect(response.activationChanged).toBe(false);
    expect(response.tradingActionPerformed).toBe(false);
  });

  it.each([
    ['expectedFenceKey', 'expected_fence_key'],
    ['expectedBuildKey', 'expected_build_key'],
    ['expectedEvidenceSetSha256', 'expected_evidence_set_sha256'],
  ] as const)('rejects a malformed %s without sending a confirm request', async (field, label) => {
    for (const badValue of ['F'.repeat(64), 'abc', `${'f'.repeat(63)} `]) {
      await expect(confirmFuturePositionEpisodeBuild({
        ...rawFutureBuildConfirmRequest,
        [field]: badValue,
      })).rejects.toThrow(new RegExp(`${label} 不是 64 位小写 SHA-256，未发起正式构建请求`));
    }
    expect(apiMocks.post).not.toHaveBeenCalled();
  });

  it('rejects a confirm response whose echoed build or fence identity drifted', async () => {
    apiMocks.post.mockResolvedValueOnce({
      data: {
        ...rawFutureBuildConfirmResponse,
        build: { ...rawFutureBuildConfirmResponse.build, build_key: '5'.repeat(64) },
      },
    });
    await expect(confirmFuturePositionEpisodeBuild(rawFutureBuildConfirmRequest))
      .rejects.toThrow(/身份与本次确认不一致/);

    apiMocks.post.mockResolvedValueOnce({
      data: {
        ...rawFutureBuildConfirmResponse,
        snapshot_fence: {
          ...rawFutureBuildConfirmResponse.snapshot_fence,
          fence_key: '5'.repeat(64),
        },
      },
    });
    await expect(confirmFuturePositionEpisodeBuild(rawFutureBuildConfirmRequest))
      .rejects.toThrow(/身份与本次确认不一致/);
  });

  it('rejects a confirm response that is not a snapshot-fence build or claims activation', async () => {
    apiMocks.post.mockResolvedValueOnce({
      data: {
        ...rawFutureBuildConfirmResponse,
        build: { ...rawFutureBuildConfirmResponse.build, source_kind: 'csv_batch' },
      },
    });
    await expect(confirmFuturePositionEpisodeBuild(rawFutureBuildConfirmRequest))
      .rejects.toThrow(/source_kind 不是 snapshot-fence 构建来源/);

    apiMocks.post.mockResolvedValueOnce({
      data: { ...rawFutureBuildConfirmResponse, activation_changed: true },
    });
    await expect(confirmFuturePositionEpisodeBuild(rawFutureBuildConfirmRequest))
      .rejects.toThrow(/activation_changed 未满足只读安全契约/);
  });
});

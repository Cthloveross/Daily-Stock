import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DailyOpportunityList } from '../DailyOpportunityList';
import {
  evaluateOpportunitySnapshot,
  fetchDailyOpportunities,
  fetchIntradayTracking,
  fetchPremarketCycleStatus,
  fetchOpportunityLearningSummary,
  fetchOpportunityOptionContext,
  fetchOpportunityOptionEvents,
  fetchOpportunityOptionOverview,
  fetchOpportunityOptionWalls,
  fetchOpportunitySnapshots,
  runPremarketCycle,
} from '../../../api/opportunities';
import type {
  DailyOpportunityRun,
  IntradayTrackingResponse,
  OpportunityLearningSummaryResponse,
  OpportunityOptionEvent,
  OpportunityOptionEventItem,
  OpportunityOptionEventResponse,
  OpportunityOptionOverviewItem,
  OpportunityOptionOverviewResponse,
  OpportunityOptionWallItem,
  OpportunityOptionWallLevel,
  OpportunityOptionWallResponse,
  OpportunityQualificationSummary,
  OpportunitySnapshot,
  OpportunitySnapshotEvaluationResponse,
  OpportunitySnapshotListResponse,
  PremarketCycleResponse,
} from '../../../types/opportunities';

vi.mock('../../../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/opportunities')>();
  return {
    ...actual,
    evaluateOpportunitySnapshot: vi.fn(),
    fetchDailyOpportunities: vi.fn(),
    fetchIntradayTracking: vi.fn(),
    fetchPremarketCycleStatus: vi.fn(),
    fetchOpportunityLearningSummary: vi.fn(),
    fetchOpportunityOptionContext: vi.fn(),
    fetchOpportunityOptionEvents: vi.fn(),
    fetchOpportunityOptionOverview: vi.fn(),
    fetchOpportunityOptionWalls: vi.fn(),
    fetchOpportunitySnapshots: vi.fn(),
    runPremarketCycle: vi.fn(),
  };
});

const run = {
  schemaVersion: '1.0',
  runId: 'opr_2026-07-22_fixture',
  runType: 'morning_prior_close',
  marketDateEt: '2026-07-22',
  asOf: '2026-07-22T12:00:00+00:00',
  generatedAt: '2026-07-22T12:00:00+00:00',
  signalVersion: 'daily_completed_bars_v1',
  rankingMethod: 'rule_based_evidence_count',
  strategyValidationState: 'not_validated',
  strategyValidationMessage: '当前排序是确定性研究队列，不是胜率或预期收益模型；尚未积累冻结信号后的 5 日/20 日 MFE、MAE 与相对 SPY 结果。',
  universe: ['NVDA'],
  requestedLimit: 10,
  candidateCount: 1,
  runReadiness: [
    {
      domain: 'daily_history',
      state: 'ready',
      source: 'per_candidate',
      asOf: '2026-07-22T12:00:00+00:00',
      actionability: 'research_input',
      message: '候选仅使用截至 as_of 已完成的日线；每个标的保留独立来源和失败状态。',
    },
    {
      domain: 'regime',
      state: 'unavailable',
      source: null,
      asOf: null,
      actionability: 'research_context',
      message: '读取当前交易日已存储 Regime，不在本接口重算或写入。',
    },
    {
      domain: 'options_flow',
      state: 'not_configured',
      source: null,
      asOf: null,
      actionability: 'not_available',
      message: '第一阶段未接入逐笔期权成交与 NBBO。',
    },
    {
      domain: 'dark_pool',
      state: 'not_configured',
      source: null,
      asOf: null,
      actionability: 'background_only',
      message: '第一阶段未加载延迟 ATS 周报，且该域不得伪装成实时暗池信号。',
    },
  ],
  candidates: [
    {
      candidateId: 'opc_fixture_nvda',
      ticker: 'NVDA',
      researchState: 'watch_only',
      directionalContext: 'bullish',
      setupTags: [
        'close_above_ema8_above_ema13',
        'daily_close_above_prior_20d_high',
        'dollar_volume_above_prior_20d_median',
        'volume_above_prior_20d_median',
      ],
      lastCompletedBarAt: '2026-07-21T16:00:00-04:00',
      referenceSessionDate: '2026-07-21',
      referenceClose: 128,
      referencePriceBasis: 'prior_completed_close',
      source: 'MoomooFetcher',
      styleMatch: {
        status: 'unknown',
        source: 'unverified',
        matchedRules: [],
        conflictingRules: [],
        unknownFields: [
          'setup',
          'direction',
          'strategy_structure',
          'dte',
          'delta',
          'entry_session',
          'holding_horizon',
          'event_policy',
          'liquidity_rules',
        ],
      },
      hardGates: [
        {
          gateId: 'us_options_universe',
          status: 'passed',
          reason: '标的符合第一阶段美股期权 underlying 格式边界；是否实际有上市期权仍待期权链验证。',
          evidenceRefs: [],
        },
        {
          gateId: 'completed_daily_history',
          status: 'passed',
          reason: '已取得 25 根已完成日线，可计算 20 日上下文。',
          evidenceRefs: [],
        },
        {
          gateId: 'latest_completed_daily_bar_freshness',
          status: 'passed',
          reason: '最新完整日线距 market_date_et 1 个自然日；允许上限为 4 日以覆盖周末和三日休市周末。',
          evidenceRefs: ['NVDA:last_completed_close'],
        },
        {
          gateId: 'current_regime_available',
          status: 'unknown',
          reason: '当前交易日没有已存储 Regime。',
          evidenceRefs: [],
        },
        {
          gateId: 'regime_allows_new_risk',
          status: 'unknown',
          reason: '缺少当前 Regime，不能判断市场环境门禁。',
          evidenceRefs: [],
        },
        {
          gateId: 'confirmed_playbook_available',
          status: 'unknown',
          reason: '尚无用户确认并版本化的 Playbook；第一阶段不执行个人风格硬门禁。',
          evidenceRefs: [],
        },
      ],
      evidence: [
        {
          evidenceId: 'NVDA:last_completed_close',
          domain: 'underlying_daily',
          metric: 'last_completed_close',
          value: 128,
          unit: 'price',
          status: 'neutral',
          source: 'MoomooFetcher',
          observedAt: '2026-07-21T16:00:00-04:00',
          publishedAt: null,
          fetchedAt: '2026-07-22T12:00:00+00:00',
          observationWindow: 'latest_completed_daily_bar',
          qualityState: 'observed',
          actionability: 'research_input',
          limitations: [],
        },
        {
          evidenceId: 'NVDA:completed_day_return',
          domain: 'underlying_daily',
          metric: 'completed_day_return',
          value: 2.4,
          unit: 'percent',
          status: 'neutral',
          source: 'MoomooFetcher',
          observedAt: '2026-07-21T16:00:00-04:00',
          publishedAt: null,
          fetchedAt: '2026-07-22T12:00:00+00:00',
          observationWindow: 'latest_vs_previous_completed_close',
          qualityState: 'observed',
          actionability: 'research_input',
          limitations: [],
        },
        {
          evidenceId: 'NVDA:ema8_ema13_alignment',
          domain: 'technical_structure',
          metric: 'ema8_ema13_alignment',
          value: { context: 'bullish', close: 128, ema8: 126.4, ema13: 124.9 },
          unit: null,
          status: 'supports',
          source: 'MoomooFetcher',
          observedAt: '2026-07-21T16:00:00-04:00',
          publishedAt: null,
          fetchedAt: '2026-07-22T12:00:00+00:00',
          observationWindow: 'all_available_completed_bars_with_full_ema_seed',
          qualityState: 'derived',
          actionability: 'research_input',
          limitations: ['EMA 是已完成日线的确定性派生值，不证明交易 edge。'],
        },
        {
          evidenceId: 'NVDA:prior_20d_range_position',
          domain: 'technical_structure',
          metric: 'prior_20d_range_position',
          value: { context: 'breakout', priorHigh: 127, priorLow: 105 },
          unit: null,
          status: 'supports',
          source: 'MoomooFetcher',
          observedAt: '2026-07-21T16:00:00-04:00',
          publishedAt: null,
          fetchedAt: '2026-07-22T12:00:00+00:00',
          observationWindow: 'latest_completed_close_vs_prior_20_completed_sessions',
          qualityState: 'derived',
          actionability: 'research_input',
          limitations: [],
        },
        {
          evidenceId: 'NVDA:volume_vs_prior_20d_median',
          domain: 'underlying_activity',
          metric: 'volume_vs_prior_20d_median',
          value: 1.8,
          unit: 'ratio',
          status: 'supports',
          source: 'MoomooFetcher',
          observedAt: '2026-07-21T16:00:00-04:00',
          publishedAt: null,
          fetchedAt: '2026-07-22T12:00:00+00:00',
          observationWindow: 'latest_completed_volume_vs_prior_20_completed_sessions',
          qualityState: 'derived',
          actionability: 'research_input',
          limitations: [],
        },
        {
          evidenceId: 'NVDA:dollar_volume_vs_prior_20d_median',
          domain: 'underlying_activity',
          metric: 'dollar_volume_vs_prior_20d_median',
          value: 1.95,
          unit: 'ratio',
          status: 'supports',
          source: 'MoomooFetcher',
          observedAt: '2026-07-21T16:00:00-04:00',
          publishedAt: null,
          fetchedAt: '2026-07-22T12:00:00+00:00',
          observationWindow: 'latest_completed_dollar_volume_vs_prior_20_completed_sessions',
          qualityState: 'derived',
          actionability: 'research_input',
          limitations: [],
        },
        {
          evidenceId: 'NVDA:stored_regime',
          domain: 'market_regime',
          metric: 'stored_regime',
          value: null,
          unit: null,
          status: 'unknown',
          source: 'regime_scores',
          observedAt: null,
          publishedAt: null,
          fetchedAt: '2026-07-22T12:00:00+00:00',
          observationWindow: 'current_market_date',
          qualityState: 'missing',
          actionability: 'research_input',
          limitations: ['Regime 是版本化市场上下文，不单独证明个股机会。'],
        },
      ],
      readiness: [
        {
          domain: 'universe',
          state: 'ready',
          source: 'us_option_underlying_format_v1',
          asOf: '2026-07-22T12:00:00+00:00',
          actionability: 'research_scope',
          message: '第一阶段支持美股期权 underlying；实际期权可用性尚待后续期权链验证。',
        },
        {
          domain: 'daily_history',
          state: 'ready',
          source: 'MoomooFetcher',
          asOf: '2026-07-21T16:00:00-04:00',
          actionability: 'research_input',
          message: '25 根已完成日线，最新为 2026-07-21.',
        },
        {
          domain: 'regime',
          state: 'unavailable',
          source: null,
          asOf: null,
          actionability: 'research_context',
          message: '当前交易日没有已存储 Regime。',
        },
        {
          domain: 'options_flow',
          state: 'not_configured',
          source: null,
          asOf: null,
          actionability: 'not_available',
          message: '未接入逐笔期权成交与 NBBO；不能声称检测到期权大单。',
        },
        {
          domain: 'dark_pool',
          state: 'not_configured',
          source: null,
          asOf: null,
          actionability: 'background_only',
          message: '未加载 FINRA ATS 延迟周报；该类数据即使接入也只能作为延迟背景。',
        },
      ],
      supportingEvidenceCount: 4,
      dataCompleteness: { state: 'partial', availableCount: 6, expectedCount: 7 },
      unknowns: [
        '尚无用户确认的 Playbook，不能声称候选符合个人交易风格。',
        '第一阶段未接入逐笔期权成交，不能识别期权大单方向或开平仓意图。',
        '第一阶段未加载 FINRA ATS 延迟周报；即使后续加载也只能作为背景，不能称为实时暗池流。',
        '当前交易日没有已存储 Regime，候选只能进入观察队列。',
      ],
    },
  ],
} satisfies DailyOpportunityRun;

function runForSymbols(tickers: string[]): DailyOpportunityRun {
  const candidate = run.candidates[0];
  return {
    ...run,
    runId: `opr_2026-07-22_${tickers.map((ticker) => ticker.toLowerCase()).join('_')}`,
    universe: tickers,
    candidateCount: tickers.length,
    candidates: tickers.map((ticker) => ({
      ...candidate,
      candidateId: `opc_fixture_${ticker.toLowerCase()}`,
      ticker,
      hardGates: candidate.hardGates.map((gate) => ({
        ...gate,
        evidenceRefs: gate.evidenceRefs.map((reference) => reference.replace('NVDA:', `${ticker}:`)),
      })),
      evidence: candidate.evidence.map((evidence) => ({
        ...evidence,
        evidenceId: evidence.evidenceId.replace('NVDA:', `${ticker}:`),
      })),
    })),
  };
}

function runFor(ticker: string): DailyOpportunityRun {
  return runForSymbols([ticker]);
}

function blockedHistoryRun(ticker: string): DailyOpportunityRun {
  const base = runFor(ticker);
  return {
    ...base,
    runId: `${base.runId}_history_unavailable`,
    candidates: base.candidates.map((candidate) => ({
      ...candidate,
      researchState: 'blocked',
      lastCompletedBarAt: null,
      source: null,
      hardGates: candidate.hardGates.map((gate) => (
        gate.gateId === 'completed_daily_history'
          ? { ...gate, status: 'failed', reason: '没有可用的已完成日线。' }
          : gate
      )),
      readiness: candidate.readiness.map((source) => (
        source.domain === 'daily_history'
          ? {
            ...source,
            state: 'unavailable',
            source: null,
            asOf: null,
            message: 'Moomoo OpenD 暂时不可用。',
          }
          : source
      )),
    })),
  };
}

function optionOverviewItem(
  ticker: string,
  overrides: Partial<OpportunityOptionOverviewItem> = {},
): OpportunityOptionOverviewItem {
  return {
    ticker,
    name: null,
    state: 'ready',
    source: 'moomoo_openapi',
    fetchedAt: '2026-07-22T12:00:02+00:00',
    sessionVolumeDate: '2026-07-22',
    openInterestAsOf: '2026-07-21',
    volumeBasis: 'current_session_cumulative',
    openInterestBasis: 'prior_clearing_session',
    volatilityBasis: 'provider_snapshot',
    callVolume: 120_000,
    putVolume: 72_000,
    putCallVolumeRatio: 0.6,
    callOpenInterest: 340_000,
    putOpenInterest: 255_000,
    putCallOpenInterestRatio: 0.75,
    ivPercent: 42.35,
    ivRankPercent: 68.4,
    ivPercentilePercent: 73.2,
    previousIvPercent: 41.9,
    ivChangePoints: 0.45,
    hv30dPercent: 36.2,
    hv30dPercentile: 61.5,
    hv60dPercent: 34.8,
    hv60dPercentile: 58.1,
    hv90dPercent: 33.4,
    hv90dPercentile: 55.2,
    hv120dPercent: 32.9,
    hv120dPercentile: 53.7,
    hv365dPercent: 39.1,
    hv365dPercentile: 64.8,
    ivHv30SpreadPoints: 6.15,
    message: '标的期权统计概览已读取。',
    limitations: ['OI 来自上一清算交易日。'],
    ...overrides,
  };
}

function optionOverviewResponse(
  items: OpportunityOptionOverviewItem[],
): OpportunityOptionOverviewResponse {
  return {
    schemaVersion: 'option-overview/1.0',
    marketDateEt: '2026-07-22',
    generatedAt: '2026-07-22T12:00:02+00:00',
    items,
  };
}

function optionWallLevel(
  rank: number,
  strike: number,
  overrides: Partial<OpportunityOptionWallLevel> = {},
): OpportunityOptionWallLevel {
  return {
    rank,
    strike,
    distanceFromSpotPercent: ((strike / 128) - 1) * 100,
    metricValue: 1_500 - ((rank - 1) * 250),
    shareOfBucketPercent: 24 - ((rank - 1) * 3),
    unit: 'contracts',
    method: 'sum_open_interest',
    ...overrides,
  };
}

function optionWallItem(
  ticker: string,
  overrides: Partial<OpportunityOptionWallItem> = {},
): OpportunityOptionWallItem {
  return {
    ticker,
    state: 'not_configured',
    source: 'moomoo_opra',
    fetchedAt: '2026-07-22T12:00:03+00:00',
    quoteAsOf: null,
    formulaVersion: 'gross-gamma-concentration-1pct/v1',
    spot: null,
    atmCallIv: {
      state: 'not_configured',
      expiry: null,
      strike: null,
      atmCallIvPercent: null,
      selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot',
    },
    scope: {
      dteMin: 0,
      dteMax: 45,
      expiries: [],
      standardContractsOnly: true,
    },
    coverage: {
      requestedContracts: 0,
      snapshotReceivedContracts: 0,
      validContracts: 0,
      coveragePercent: 0,
      failedBatches: 0,
      excludedNonstandardContracts: 0,
      excludedUnknownStandardTypeContracts: 0,
      gammaContracts: 0,
    },
    walls: {
      callOi: [],
      putOi: [],
      callVolume: [],
      putVolume: [],
      callGammaConcentration: [],
      putGammaConcentration: [],
      grossGammaConcentration: [],
    },
    message: 'Moomoo OpenD 期权墙数据源未配置。',
    assumptions: [],
    limitations: [],
    ...overrides,
  };
}

function readyOptionWallItem(
  ticker: string,
  dteMin = 0,
  dteMax = 45,
): OpportunityOptionWallItem {
  return optionWallItem(ticker, {
    state: 'ready',
    spot: 128,
    quoteAsOf: '2026-07-22T09:45:00-04:00',
    atmCallIv: {
      state: 'ready',
      expiry: '2026-07-24',
      strike: 130,
      atmCallIvPercent: 42.5,
      selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot',
    },
    scope: {
      dteMin,
      dteMax,
      expiries: ['2026-07-24', '2026-07-31'],
      standardContractsOnly: true,
    },
    coverage: {
      requestedContracts: 210,
      snapshotReceivedContracts: 207,
      validContracts: 203,
      coveragePercent: 96.67,
      failedBatches: 0,
      excludedNonstandardContracts: 4,
      excludedUnknownStandardTypeContracts: 0,
      gammaContracts: 196,
    },
    walls: {
      callOi: [
        optionWallLevel(1, 135, {
          side: 'call',
          metricBasis: 'settled_open_interest_prior_session',
          quoteEvidence: 'partial',
          expiryBreakdown: {
            topExpiries: [
              {
                expiry: '2026-07-24',
                dte: 2,
                metricValue: 900,
                shareOfLevelPercent: 60,
                contractCount: 1,
                quote: {
                  ivPercent: 41.2,
                  bid: null,
                  ask: null,
                  mark: null,
                  quoteAsOf: '2026-07-22 09:44:00',
                },
                quoteEvidence: 'partial',
              },
              {
                expiry: '2026-07-31',
                dte: 9,
                metricValue: 600,
                shareOfLevelPercent: 40,
                contractCount: 1,
                quote: { ivPercent: null, bid: null, ask: null, mark: null, quoteAsOf: null },
                quoteEvidence: 'unavailable',
              },
            ],
            other: null,
          },
        }),
        optionWallLevel(2, 140),
        optionWallLevel(3, 130),
      ],
      putOi: [
        optionWallLevel(1, 120),
        optionWallLevel(2, 115),
        optionWallLevel(3, 125),
      ],
      callVolume: [],
      putVolume: [],
      callGammaConcentration: [],
      putGammaConcentration: [],
      grossGammaConcentration: [
        optionWallLevel(1, 125, {
          metricValue: 18_500_000,
          unit: 'usd_delta_change_per_1pct_move',
          method: 'gross_gamma_concentration_1pct',
        }),
      ],
    },
    message: '期权墙已按可观察 OI、成交量与无符号 Gamma 集中度聚合。',
    assumptions: ['No dealer-position sign is inferred from public open interest.'],
    limitations: ['A concentration level is context, not guaranteed support or resistance.'],
  });
}

function optionWallResponse(items: OpportunityOptionWallItem[]): OpportunityOptionWallResponse {
  return {
    schemaVersion: 'option-wall/1.2',
    marketDateEt: '2026-07-22',
    generatedAt: '2026-07-22T12:00:04+00:00',
    items,
  };
}

function optionEvent(
  eventId: string,
  overrides: Partial<OpportunityOptionEvent> = {},
): OpportunityOptionEvent {
  return {
    eventId,
    optionCode: 'US.NVDA260724C00135000',
    ownerCode: 'US.NVDA',
    symbol: 'NVDA 260724 135C',
    fillTime: '2026-07-22T10:15:31-04:00',
    tickerType: 'BUY',
    price: 4.2,
    volume: 850,
    turnover: 357_000,
    optionType: 'CALL',
    strikePrice: 135,
    expiry: '2026-07-24',
    dte: 2,
    underlyingPrice: 128,
    bidPrice: 4.1,
    askPrice: 4.25,
    ivPercent: 48.2,
    totalVolume: 4_500,
    totalOpenInterest: 1_900,
    voRatioPercent: 236.84,
    delta: 0.42,
    sentiment: 'BULLISH',
    orderTypes: ['SWEEP'],
    strategyType: 'SINGLE_LEG',
    ...overrides,
  };
}

function optionEventItem(
  ticker: string,
  overrides: Partial<OpportunityOptionEventItem> = {},
): OpportunityOptionEventItem {
  return {
    ticker,
    state: 'empty',
    source: 'moomoo_openapi',
    fetchedAt: '2026-07-22T14:16:00+00:00',
    eventAsOf: null,
    allCount: 0,
    events: [],
    message: '当前筛选范围内没有异常期权成交。',
    limitations: [],
    ...overrides,
  };
}

function optionEventResponse(items: OpportunityOptionEventItem[]): OpportunityOptionEventResponse {
  return {
    schemaVersion: 'option-event/1.0',
    marketDateEt: '2026-07-22',
    generatedAt: '2026-07-22T14:16:00+00:00',
    items,
  };
}

function opportunitySnapshot(
  overrides: Partial<OpportunitySnapshot> = {},
): OpportunitySnapshot {
  return {
    schemaVersion: 'opportunity-snapshot/1.0',
    snapshotKey: 'snapshot-2026-07-22-morning',
    marketDateEt: '2026-07-22',
    sourceRunId: run.runId,
    frozenAt: '2026-07-22T12:05:00+00:00',
    signalVersion: run.signalVersion,
    candidateCount: 1,
    eligibleCandidateCount: 1,
    validationEligible: true,
    eligibilityReasons: [],
    outcomeProgress: [
      { horizonSessions: 5, eligibleCount: 1, matureCount: 0, pendingCount: 1, partialCount: 0 },
      { horizonSessions: 20, eligibleCount: 1, matureCount: 0, pendingCount: 1, partialCount: 0 },
    ],
    idempotentReplay: false,
    ...overrides,
  };
}

function opportunityQualification({
  analysisQualityState = 'ready',
  fullResearchQualified = true,
}: {
  analysisQualityState?: OpportunityQualificationSummary['analysisQualityState'];
  fullResearchQualified?: boolean;
} = {}): OpportunityQualificationSummary {
  return {
    assessmentKey: 'opqa-fixture',
    policyVersion: 'opportunity_qualification_v2',
    publicationState: 'canonical_published',
    analysisQualityState,
    assessedAt: '2026-07-22T12:05:00+00:00',
    reasonCodes: analysisQualityState === 'ready' ? [] : ['analysis_quality_degraded'],
    tracks: [
      {
        trackKey: 'raw_underlying_path_v1',
        qualifiedCount: 1,
        excludedCount: 0,
        unverifiedCount: 0,
        prospectiveCount: 1,
        retrospectiveCount: 0,
        observationReadyCount: 1,
      },
      {
        trackKey: 'underlying_daily_selection_v1',
        qualifiedCount: 1,
        excludedCount: 0,
        unverifiedCount: 0,
        prospectiveCount: 1,
        retrospectiveCount: 0,
        observationReadyCount: 1,
      },
      {
        trackKey: 'canonical_full_research_v1',
        qualifiedCount: fullResearchQualified ? 1 : 0,
        excludedCount: fullResearchQualified ? 0 : 1,
        unverifiedCount: 0,
        prospectiveCount: 1,
        retrospectiveCount: 0,
        observationReadyCount: 1,
      },
    ],
  };
}

function intradayTrackingResponse(): IntradayTrackingResponse {
  return {
    schemaVersion: 'intraday-tracking/1.0',
    generatedAt: '2026-07-22T14:30:00+00:00',
    marketDateEt: '2026-07-22',
    sessionState: 'closed',
    sessionStateBasis: 'america_new_york_clock_v1',
    trackingBasis: 'frozen_premarket_plan_readonly',
    items: [],
    limitations: [],
  };
}

function snapshotList(items: OpportunitySnapshot[]): OpportunitySnapshotListResponse {
  return { schemaVersion: 'opportunity-snapshot-list/1.0', items };
}

function premarketCycleResponse(
  overrides: Partial<PremarketCycleResponse> = {},
): PremarketCycleResponse {
  return {
    schemaVersion: 'canonical-premarket-cycle/1.0',
    cycleVersion: 'canonical_premarket_cycle_v1',
    freezePolicyVersion: 'canonical_premarket_xnys_v2',
    scopeKey: 'canonical_premarket_research',
    cycleKey: 'pmr-fixture',
    state: 'waiting_window',
    quality: 'unknown',
    marketDateEt: '2026-07-22',
    previousSession: '2026-07-21',
    regularOpenAt: '2026-07-22T13:30:00+00:00',
    windowStartAt: null,
    windowEndAt: null,
    latestStartAt: null,
    cycleAsOf: null,
    startedAt: null,
    completedAt: null,
    retryAfterSeconds: null,
    universe: ['NVDA'],
    requestedLimit: 5,
    universeSource: 'persisted',
    universeVersionKey: 'opru_fixture',
    attemptKey: null,
    attemptTrigger: null,
    attemptStartedAt: null,
    leaseExpiresAt: null,
    recoveredFromAttemptKey: null,
    recoverable: false,
    nextScheduledAt: '2026-07-22T13:12:00+00:00',
    schedulerEnabled: true,
    primaryScheduledAt: '2026-07-22T13:12:00+00:00',
    retryScheduledAt: '2026-07-22T13:17:00+00:00',
    stages: [],
    regimeQuality: {},
    qualityReasons: [],
    run: null,
    snapshot: null,
    idempotentReplay: false,
    errorCode: null,
    message: '等待 canonical 盘前窗口开始。',
    ...overrides,
  };
}

function learningSummary(
  overrides: Partial<OpportunityLearningSummaryResponse> = {},
): OpportunityLearningSummaryResponse {
  return {
    schemaVersion: 'opportunity-learning/1.0',
    generatedAt: '2026-07-22T12:06:00+00:00',
    strategyState: 'collecting',
    autoAdjustment: false,
    minimumSummarySamples: 20,
    minimumInvestigationSamples: 20,
    automaticMaintenanceEnabled: true,
    maintenancePolicyVersion: 'xnys-close-qualified-raw-path-v2',
    latestMaintenance: null,
    horizons: [
      {
        horizonSessions: 5,
        matureCount: 0,
        distinctSignalSessions: 0,
        directionalSampleCount: 0,
        contextHitCount: null,
        contextMissCount: null,
        neutralCount: null,
        nonDirectionalCount: null,
        contextHitRatePercent: null,
        summaryVisible: false,
        investigationReady: false,
      },
      {
        horizonSessions: 20,
        matureCount: 0,
        distinctSignalSessions: 0,
        directionalSampleCount: 0,
        contextHitCount: null,
        contextMissCount: null,
        neutralCount: null,
        nonDirectionalCount: null,
        contextHitRatePercent: null,
        summaryVisible: false,
        investigationReady: false,
      },
    ],
    limitations: [],
    ...overrides,
  };
}

function evaluationResponse(
  overrides: Partial<OpportunitySnapshotEvaluationResponse> = {},
): OpportunitySnapshotEvaluationResponse {
  return {
    schemaVersion: 'opportunity-outcome-evaluation/1.0',
    snapshotKey: 'snapshot-2026-07-22-morning',
    evaluatedAt: '2026-07-29T12:00:00+00:00',
    candidateCount: 1,
    insertedOutcomes: 1,
    alreadyRecorded: 0,
    pendingHorizons: 1,
    dataGapHorizons: 0,
    outcomeProgress: [
      { horizonSessions: 5, eligibleCount: 1, matureCount: 1, pendingCount: 0, partialCount: 0 },
      { horizonSessions: 20, eligibleCount: 1, matureCount: 0, pendingCount: 1, partialCount: 0 },
    ],
    message: '已更新到期结果。',
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function selectCandidateRow(ticker: string): void {
  const row = screen.getAllByRole('row').find((item) => item.textContent?.includes(ticker));
  expect(row).toBeDefined();
  fireEvent.click(row as HTMLElement);
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="current route">{`${location.pathname}${location.search}`}</output>;
}

describe('DailyOpportunityList', () => {
  beforeEach(() => {
    vi.mocked(evaluateOpportunitySnapshot).mockReset();
    vi.mocked(evaluateOpportunitySnapshot).mockResolvedValue(evaluationResponse());
    vi.mocked(fetchDailyOpportunities).mockReset();
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(run);
    vi.mocked(fetchIntradayTracking).mockReset();
    vi.mocked(fetchIntradayTracking).mockResolvedValue(intradayTrackingResponse());
    vi.mocked(fetchPremarketCycleStatus).mockReset();
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse());
    vi.mocked(fetchOpportunityLearningSummary).mockReset();
    vi.mocked(fetchOpportunityLearningSummary).mockResolvedValue(learningSummary());
    vi.mocked(fetchOpportunityOptionContext).mockReset();
    vi.mocked(fetchOpportunityOptionEvents).mockReset();
    vi.mocked(fetchOpportunityOptionEvents).mockImplementation(async (symbols) => (
      optionEventResponse(symbols.map((ticker) => optionEventItem(ticker)))
    ));
    vi.mocked(fetchOpportunityOptionOverview).mockReset();
    vi.mocked(fetchOpportunityOptionOverview).mockImplementation(async (symbols) => (
      optionOverviewResponse(symbols.map((ticker) => optionOverviewItem(ticker)))
    ));
    vi.mocked(fetchOpportunityOptionWalls).mockReset();
    vi.mocked(fetchOpportunityOptionWalls).mockImplementation(async (symbols) => (
      optionWallResponse(symbols.map((ticker) => optionWallItem(ticker)))
    ));
    vi.mocked(fetchOpportunitySnapshots).mockReset();
    vi.mocked(fetchOpportunitySnapshots).mockResolvedValue(snapshotList([]));
    vi.mocked(runPremarketCycle).mockReset();
    vi.mocked(runPremarketCycle).mockResolvedValue(premarketCycleResponse());
  });

  it('renders the backend evidence contract without inventing a total score', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByText('基础候选 · 非信号')).toBeInTheDocument();
    expect(screen.getAllByText('EMA8 / EMA13 结构')).not.toHaveLength(0);
    expect(screen.getAllByText('成交量 / 前 20 日中位')).not.toHaveLength(0);
    // 数据时点提示必须把基础相对量标为上一完整交易日，只有期权成交量才是当日累计。
    expect(screen.getByText(/相对量能＝上一完整交易日（相对之前 20 个 session）/)).toBeInTheDocument();
    expect(screen.queryByText(/；Volume＝本交易日累计/)).not.toBeInTheDocument();
    expect(screen.queryByText(/总分/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^score$/i)).not.toBeInTheDocument();
    expect(fetchDailyOpportunities).toHaveBeenCalledWith(['NVDA'], 10, { refresh: false });
  });

  it('shows the request date, evidence date, ET generation time, and three research stages', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('基础榜可用 · 增强降级')).toBeInTheDocument();
    const status = screen.getByLabelText('今日机会研究状态');
    expect(status).toHaveTextContent('请求日 2026-07-22 ET');
    expect(status).toHaveTextContent('基础证据 2026-07-21');
    expect(status).toHaveTextContent('生成 2026-07-22 08:00 ET');
    expect(status).toHaveTextContent('1 基础榜 · 就绪');
    expect(status).toHaveTextContent('2 期权概览 · 就绪');
    expect(status).toHaveTextContent('3 Top 5 墙 · 降级');
    expect(status).toHaveTextContent('增强降级：Top 5 墙部分或不可用');
    const canonical = screen.getByLabelText('官方盘前研究状态');
    expect(canonical).toHaveTextContent('后台自动研究 已启用');
    expect(canonical).toHaveTextContent('官方池 1 只');
    expect(canonical).toHaveTextContent('版本 opru_fixture');
    expect(canonical).toHaveTextContent('自动时点 09:12 ET / 09:17 ET');
    expect(canonical).toHaveTextContent('暂无持久化执行记录');
    expect(screen.getByRole('button', { name: '刷新只读预览' })).toBeEnabled();
  });

  it('shows a mixed evidence-date range instead of implying one shared completed session', async () => {
    const mixedRun = runForSymbols(['NVDA', 'AAPL']);
    mixedRun.candidates[1] = {
      ...mixedRun.candidates[1],
      lastCompletedBarAt: '2026-07-20T16:00:00-04:00',
      referenceSessionDate: '2026-07-20',
    };
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(mixedRun);

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA', 'AAPL']} />
      </MemoryRouter>,
    );

    const status = await screen.findByLabelText('今日机会研究状态');
    await waitFor(() => {
      expect(status).toHaveTextContent('基础证据 2026-07-20–2026-07-21（混合）');
    });
  });

  it('labels the server-side default universe without presenting it as a probability model', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={[]} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByText('默认美股池 · 扫描 1 只')).toBeInTheDocument();
    expect(screen.getByText(/Top 5 规则匹配候选由/)).toBeInTheDocument();
    expect(screen.queryByText(/最有可能/)).not.toBeInTheDocument();
    expect(fetchDailyOpportunities).toHaveBeenCalledWith([], 10, { refresh: false });
  });

  it('shows strict outcome-learning boundaries without exposing an under-sampled hit rate', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    const learningDisclosure = screen.getByText('研究结果跟踪').closest('details');
    expect(learningDisclosure).not.toHaveAttribute('open');
    expect(screen.getAllByText('尚无可统计样本')).toHaveLength(3);
    expect(screen.getByText(/这里只读展示已保存研究版本/)).toBeInTheDocument();
    expect(screen.getByText(/普通预览不会创建旧 v1 快照/)).toBeInTheDocument();
    expect(screen.queryByText('方向样本 0/20；不足门槛不显示命中率')).not.toBeInTheDocument();
    expect(screen.getByText(/不足 20 个方向样本不显示命中率/)).toBeInTheDocument();
    expect(screen.getByText(/20 个独立交易日后，才进入人工调查/)).toBeInTheDocument();
    expect(screen.getByText('绝不自动调权。')).toBeInTheDocument();
    expect(screen.getByText(/结果自动回填：已启用/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '冻结今日研究' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '手动重试到期缺口' })).toBeDisabled();
    expect(screen.queryByText(/标的方向命中率/)).not.toBeInTheDocument();
  });

  it('explains an analysis-quality exclusion as statistical ineligibility, not an unusable candidate', async () => {
    vi.mocked(fetchOpportunitySnapshots).mockResolvedValue(snapshotList([
      opportunitySnapshot({
        eligibleCandidateCount: 0,
        validationEligible: false,
        eligibilityReasons: ['legacy_combined_reason'],
        analysisQualityEligible: false,
        analysisQualityReasons: [
          'regime_supporting_events_degraded',
          'regime_supporting_premarket_degraded',
        ],
        outcomeProgress: [
          { horizonSessions: 5, eligibleCount: 0, matureCount: 0, pendingCount: 0, partialCount: 0 },
          { horizonSessions: 20, eligibleCount: 0, matureCount: 0, pendingCount: 0, partialCount: 0 },
        ],
      }),
    ]));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByText(/最近保存 2026-07-22 · 完整研究 0\/1/)).toBeInTheDocument();
    const exclusionNotice = screen.getByText(/最近保存版本未纳入统计/);
    expect(exclusionNotice).toHaveTextContent('宏观事件降级；盘前行情降级');
    expect(screen.getByText('基础候选 · 非信号')).toBeInTheDocument();
    expect(screen.queryByText(/暂不可验证/)).not.toBeInTheDocument();
  });

  it('reads canonical status before requesting a window-outside preview', async () => {
    const statusRequest = deferred<PremarketCycleResponse>();
    vi.mocked(fetchPremarketCycleStatus).mockReturnValue(statusRequest.promise);
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(fetchPremarketCycleStatus).toHaveBeenCalledWith(5);
    });
    expect(fetchDailyOpportunities).not.toHaveBeenCalled();
    expect(runPremarketCycle).not.toHaveBeenCalled();

    await act(async () => {
      statusRequest.resolve(premarketCycleResponse());
      await statusRequest.promise;
    });

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(fetchDailyOpportunities).toHaveBeenCalledWith(['NVDA'], 10, { refresh: false });
    expect(runPremarketCycle).not.toHaveBeenCalled();
  });

  it('fails closed without a preview when the initial canonical status is unreadable', async () => {
    vi.mocked(fetchPremarketCycleStatus).mockRejectedValue(
      new Error('status timeout'),
    );

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('机会扫描暂不可用')).toBeInTheDocument();
    expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent(
      '今日盘前研究状态或生成暂不可用：status timeout',
    );
    expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent(
      '为避免与未知的服务端任务并发，当前没有启动预览',
    );
    expect(fetchDailyOpportunities).not.toHaveBeenCalled();
    expect(runPremarketCycle).not.toHaveBeenCalled();
  });

  it('keeps the existing preview and starts no new scan when a manual status refresh fails', async () => {
    vi.mocked(fetchPremarketCycleStatus)
      .mockResolvedValueOnce(premarketCycleResponse())
      .mockRejectedValueOnce(new Error('database is locked'));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: '刷新只读预览' }));

    await waitFor(() => {
      expect(fetchPremarketCycleStatus).toHaveBeenCalledTimes(2);
    });
    expect(fetchDailyOpportunities).toHaveBeenCalledTimes(1);
    expect(runPremarketCycle).not.toHaveBeenCalled();
    expect(screen.getByText(/上一次可用结果/)).toBeInTheDocument();
    expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent(
      'database is locked',
    );
  });

  it('does not auto-run from ready_to_run and only publishes after an explicit click', async () => {
    const snapshot = opportunitySnapshot({
      idempotentReplay: false,
      eligibleCandidateCount: 0,
      validationEligible: false,
      eligibilityReasons: [
        'regime_supporting_events_degraded',
        'regime_supporting_premarket_degraded',
      ],
      analysisQualityEligible: false,
      analysisQualityReasons: [
        'regime_supporting_events_degraded',
        'regime_supporting_premarket_degraded',
      ],
      qualification: opportunityQualification({
        analysisQualityState: 'degraded',
        fullResearchQualified: false,
      }),
      outcomeProgress: [
        { horizonSessions: 5, eligibleCount: 0, matureCount: 0, pendingCount: 0, partialCount: 0 },
        { horizonSessions: 20, eligibleCount: 0, matureCount: 0, pendingCount: 0, partialCount: 0 },
      ],
    });
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse({
      state: 'ready_to_run',
      schedulerEnabled: false,
      message: '已进入 canonical 盘前窗口，可以启动研究周期。',
    }));
    vi.mocked(runPremarketCycle).mockResolvedValue(premarketCycleResponse({
      state: 'published',
      quality: 'degraded',
      run,
      snapshot,
      completedAt: snapshot.frozenAt,
      stages: [
        {
          name: 'resolve_window',
          state: 'completed',
          startedAt: '2026-07-22T12:45:00+00:00',
          completedAt: '2026-07-22T12:45:00+00:00',
          errorCode: null,
        },
      ],
      qualityReasons: [
        'regime_supporting_events_degraded',
        'regime_supporting_premarket_degraded',
      ],
      message: '今日 canonical 盘前研究已发布并保存为不可变快照。',
    }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('官方盘前研究等待生成')).toBeInTheDocument();
    expect(runPremarketCycle).not.toHaveBeenCalled();
    expect(fetchDailyOpportunities).not.toHaveBeenCalled();
    expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent(
      '后台自动研究未启用；可点击“立即生成”',
    );

    fireEvent.click(await screen.findByRole('button', { name: '立即生成' }));

    await waitFor(() => {
      expect(runPremarketCycle).toHaveBeenCalledWith(5);
    });
    const canonical = screen.getByLabelText('官方盘前研究状态');
    await waitFor(() => expect(canonical).toHaveTextContent('已发布'));
    expect(canonical).toHaveTextContent('当前榜单：官方版本');
    expect(canonical).toHaveTextContent(snapshot.snapshotKey);
    expect(screen.getByLabelText('发布状态：已发布')).toBeInTheDocument();
    expect(screen.getByLabelText('数据质量：支持证据降级')).toBeInTheDocument();
    expect(screen.getByLabelText(
      '统计入样：标的路径 1/1、日线选股 1/1、完整研究 0/1',
    )).toBeInTheDocument();
    expect(canonical).not.toHaveTextContent('canonical_full_research_v1');
    expect(canonical).toHaveTextContent(
      '官方版本已发布，可用于今日研究；支持证据降级，本批次未纳入严格结果统计。',
    );
    expect(canonical).toHaveTextContent('宏观事件降级');
    expect(canonical).toHaveTextContent('盘前行情降级');
    expect(canonical).not.toHaveTextContent('只有质量门禁通过');
    expect(canonical).toHaveTextContent('解析窗口 · 完成');
  });

  it('pauses preview and refuses provider work in the final minute before 09:12 ET', async () => {
    const primaryScheduledAt = new Date(Date.now() + 60_000).toISOString();
    const latestStartAt = new Date(Date.now() + 6 * 60_000).toISOString();
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse({
      state: 'waiting_window',
      primaryScheduledAt,
      latestStartAt,
      nextScheduledAt: primaryScheduledAt,
    }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('后台盘前研究即将开始')).toBeInTheDocument();
    const refresh = await screen.findByRole('button', { name: '刷新只读预览' });
    fireEvent.click(refresh);

    await waitFor(() => {
      expect(fetchPremarketCycleStatus).toHaveBeenCalledTimes(2);
    });
    expect(fetchDailyOpportunities).not.toHaveBeenCalled();
    expect(runPremarketCycle).not.toHaveBeenCalled();
  });

  it('does not backfill after the canonical window closes and labels the daily result as preview', async () => {
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse({
      state: 'window_closed',
      message: '今日 canonical 盘前窗口已关闭，不允许事后补写。',
    }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(runPremarketCycle).not.toHaveBeenCalled();
    expect(fetchDailyOpportunities).toHaveBeenCalledWith(['NVDA'], 10, { refresh: false });
    const canonical = screen.getByLabelText('官方盘前研究状态');
    expect(canonical).toHaveTextContent('今日未生成官方盘前版本，当前仅为预览');
    expect(canonical).toHaveTextContent('当前榜单：只读预览');
  });

  it('does not start a parallel daily preview while the server reports a running cycle', async () => {
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse({
      state: 'running',
      retryAfterSeconds: 2,
      attemptKey: 'opa_scheduler_fixture',
      attemptTrigger: 'scheduler',
      attemptStartedAt: '2026-07-22T13:12:00+00:00',
      leaseExpiresAt: '2026-07-22T13:14:00+00:00',
      stages: [{
        name: 'compute_regime',
        state: 'running',
        startedAt: '2026-07-22T12:45:00+00:00',
        completedAt: null,
        errorCode: null,
      }],
      message: '同一研究周期仍在执行；请稍后重试状态接口。',
    }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent(
        '服务端今日盘前研究仍在执行',
      );
    });
    expect(fetchDailyOpportunities).not.toHaveBeenCalled();
    expect(runPremarketCycle).not.toHaveBeenCalled();
    const canonical = screen.getByLabelText('官方盘前研究状态');
    expect(canonical).toHaveTextContent('计算 Regime · 运行中');
    expect(canonical).toHaveTextContent('opa_scheduler_fi · 后台 · 生成中 · 2026-07-22 09:12 ET');
    expect(screen.getByRole('button', { name: '读取今日研究状态' })).toBeEnabled();
  });

  it('polls a running attempt read-only and adopts the published result', async () => {
    vi.mocked(fetchPremarketCycleStatus)
      .mockResolvedValueOnce(premarketCycleResponse({
        state: 'running',
        retryAfterSeconds: 1,
        attemptKey: 'opa_poll_fixture',
        attemptTrigger: 'scheduler',
        attemptStartedAt: '2026-07-22T13:12:00+00:00',
      }))
      .mockResolvedValueOnce(premarketCycleResponse({
        state: 'published',
        quality: 'ready',
        run,
        snapshot: opportunitySnapshot({
          qualification: opportunityQualification(),
        }),
        attemptKey: 'opa_poll_fixture',
        attemptTrigger: 'scheduler',
        attemptStartedAt: '2026-07-22T13:12:00+00:00',
      }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('官方盘前研究正在生成')).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchPremarketCycleStatus).toHaveBeenCalledTimes(2);
    }, { timeout: 3_000 });
    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent('已发布');
    expect(screen.getByLabelText('发布状态：已发布')).toBeInTheDocument();
    expect(screen.getByLabelText('数据质量：完整')).toBeInTheDocument();
    expect(screen.getByLabelText(
      '统计入样：标的路径 1/1、日线选股 1/1、完整研究 1/1',
    )).toBeInTheDocument();
    expect(screen.getByLabelText('官方盘前研究状态')).not.toHaveTextContent(
      'raw_underlying_path_v1',
    );
    expect(runPremarketCycle).not.toHaveBeenCalled();
    expect(fetchDailyOpportunities).not.toHaveBeenCalled();
  });

  it('keeps the legacy aggregate statistics label when qualification is absent', async () => {
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse({
      state: 'published',
      quality: 'ready',
      run,
      snapshot: opportunitySnapshot(),
    }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByLabelText('发布状态：已发布')).toBeInTheDocument();
    expect(screen.getByLabelText('数据质量：完整')).toBeInTheDocument();
    expect(screen.getByLabelText('统计入样：已纳入 1/1')).toBeInTheDocument();
    expect(screen.getByLabelText('官方盘前研究状态')).not.toHaveTextContent(
      '标的路径',
    );
  });

  it('deep-links detail pages with the official snapshot key when the canonical cycle is published', async () => {
    const officialKey = `ops_${'a'.repeat(64)}`;
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse({
      state: 'published',
      quality: 'ready',
      run,
      snapshot: opportunitySnapshot({ snapshotKey: officialKey }),
    }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
        <LocationProbe />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: '打开 NVDA 完整机会分析' })[0]);
    expect(screen.getByLabelText('current route')).toHaveTextContent(
      `/regime/opportunity/NVDA?snapshotKey=${officialKey}`,
    );
  });

  it('re-checks status after a manually started run times out and suppresses concurrent preview', async () => {
    vi.mocked(fetchPremarketCycleStatus)
      .mockResolvedValueOnce(premarketCycleResponse({ state: 'waiting_window' }))
      .mockResolvedValueOnce(premarketCycleResponse({ state: 'ready_to_run' }))
      .mockResolvedValueOnce(premarketCycleResponse({ state: 'ready_to_run' }))
      .mockResolvedValueOnce(premarketCycleResponse({
        state: 'running',
        retryAfterSeconds: 2,
      }));
    vi.mocked(runPremarketCycle).mockRejectedValue(new Error('timeout of 35000ms exceeded'));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: '刷新只读预览' }));
    expect(await screen.findByRole('button', { name: '立即生成' })).toBeEnabled();
    expect(runPremarketCycle).not.toHaveBeenCalled();
    expect(fetchDailyOpportunities).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '立即生成' }));

    await waitFor(() => {
      expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent(
        '服务端今日盘前研究仍在执行',
      );
    });
    expect(fetchPremarketCycleStatus).toHaveBeenCalledTimes(4);
    expect(runPremarketCycle).toHaveBeenCalledTimes(1);
    expect(fetchDailyOpportunities).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('官方盘前研究状态')).toHaveTextContent('服务端今日盘前研究仍在执行');
    expect(screen.queryByText(/timeout of 35000ms exceeded/)).not.toBeInTheDocument();
  });

  it('does not use a window timer to re-read status or start provider work', async () => {
    const windowStart = new Date(Date.now() + 20).toISOString();
    const windowEnd = new Date(Date.now() + 60_000).toISOString();
    vi.mocked(fetchPremarketCycleStatus).mockResolvedValue(premarketCycleResponse({
      state: 'waiting_window',
      windowStartAt: windowStart,
      windowEndAt: windowEnd,
    }));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 400));
    });
    expect(fetchPremarketCycleStatus).toHaveBeenCalledTimes(1);
    expect(runPremarketCycle).not.toHaveBeenCalled();
    expect(fetchDailyOpportunities).toHaveBeenCalledTimes(1);
  });

  it('shows mature and pending progress, reveals qualified hit rate, and evaluates frozen snapshots', async () => {
    const snapshot = opportunitySnapshot({
      outcomeProgress: [
        { horizonSessions: 5, eligibleCount: 20, matureCount: 20, pendingCount: 0, partialCount: 0 },
        { horizonSessions: 20, eligibleCount: 20, matureCount: 5, pendingCount: 15, partialCount: 0 },
      ],
    });
    vi.mocked(fetchOpportunitySnapshots).mockResolvedValue(snapshotList([snapshot]));
    vi.mocked(fetchOpportunityLearningSummary).mockResolvedValue(learningSummary({
      strategyState: 'descriptive_summary_available',
      horizons: [
        {
          horizonSessions: 5,
          matureCount: 20,
          distinctSignalSessions: 20,
          directionalSampleCount: 20,
          contextHitCount: 16,
          contextMissCount: 4,
          neutralCount: 0,
          nonDirectionalCount: 0,
          contextHitRatePercent: 80,
          summaryVisible: true,
          investigationReady: true,
        },
        {
          horizonSessions: 20,
          matureCount: 5,
          distinctSignalSessions: 3,
          directionalSampleCount: 5,
          contextHitCount: null,
          contextMissCount: null,
          neutralCount: null,
          nonDirectionalCount: null,
          contextHitRatePercent: null,
          summaryVisible: false,
          investigationReady: false,
        },
      ],
    }));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('已回填 20 · 等待目标日 0')).toBeInTheDocument();
    expect(screen.getByText('已回填 5 · 等待目标日 15')).toBeInTheDocument();
    expect(screen.getByText('标的方向命中率 80% · 20 个独立交易日')).toBeInTheDocument();
    expect(screen.getByText('方向样本 5/20；不足门槛不显示命中率')).toBeInTheDocument();

    vi.mocked(evaluateOpportunitySnapshot).mockResolvedValueOnce(evaluationResponse({
      dataGapHorizons: 2,
    }));
    fireEvent.click(screen.getByRole('button', { name: '手动重试到期缺口' }));
    await waitFor(() => {
      expect(evaluateOpportunitySnapshot).toHaveBeenCalledWith(snapshot.snapshotKey);
    });
    expect(await screen.findByText(/新增 1 条已回填结果/)).toBeInTheDocument();
    expect(screen.getByText(/1 个观察窗口仍待目标交易日，2 个已到期窗口待补数据/)).toBeInTheDocument();
  });

  it('allows raw-path retry for a prospective degraded canonical snapshot', async () => {
    const snapshot = opportunitySnapshot({
      eligibleCandidateCount: 0,
      validationEligible: false,
      analysisQualityEligible: false,
      qualification: opportunityQualification({
        analysisQualityState: 'degraded',
        fullResearchQualified: false,
      }),
      underlyingPathCandidateCount: 1,
      underlyingPathProgress: [
        { horizonSessions: 5, eligibleCount: 1, matureCount: 0, pendingCount: 0, partialCount: 0, dataGapCount: 1 },
        { horizonSessions: 20, eligibleCount: 1, matureCount: 0, pendingCount: 1, partialCount: 0, dataGapCount: 0 },
      ],
      outcomeProgress: [
        { horizonSessions: 5, eligibleCount: 0, matureCount: 0, pendingCount: 0, partialCount: 0 },
        { horizonSessions: 20, eligibleCount: 0, matureCount: 0, pendingCount: 0, partialCount: 0 },
      ],
    });
    vi.mocked(fetchOpportunitySnapshots).mockResolvedValue(snapshotList([snapshot]));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    const retry = await screen.findByRole('button', { name: '手动重试到期缺口' });
    expect(retry).toBeEnabled();
    expect(screen.getByLabelText('标的路径审计进度')).toHaveTextContent(
      '5D 已回填 0 / 等待目标日 0 · 20D 已回填 0 / 等待目标日 1 · 待补数据 1',
    );
    fireEvent.click(retry);
    await waitFor(() => {
      expect(evaluateOpportunitySnapshot).toHaveBeenCalledWith(snapshot.snapshotKey);
    });
  });

  it('keeps candidates usable when snapshot and learning-summary requests fail', async () => {
    vi.mocked(fetchOpportunitySnapshots).mockRejectedValue(new Error('snapshot offline'));
    vi.mocked(fetchOpportunityLearningSummary).mockRejectedValue(new Error('summary offline'));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(await screen.findByText(/学习面板暂不可用：snapshot offline；summary offline/)).toBeInTheDocument();
    expect(screen.getByRole('table', { name: '每日机会候选表' })).toBeInTheDocument();
    expect(screen.getByText('NVDA · 研究详情')).toBeInTheDocument();
  });

  it('shows overall domain states and base price-volume completeness', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    await screen.findByText('NVDA');
    expect(screen.getByLabelText(
      '完整日线，可用：候选仅使用截至 as_of 已完成的日线；每个标的保留独立来源和失败状态。',
    )).toBeInTheDocument();
    expect(screen.getByLabelText(
      'Regime，不可用：读取当前交易日已存储 Regime，不在本接口重算或写入。',
    )).toBeInTheDocument();
    expect(screen.getByLabelText(
      '期权流（未入榜），未纳入排序：第一阶段未接入逐笔期权成交与 NBBO。',
    )).toBeInTheDocument();
    expect(screen.getByLabelText(
      '场外 / 暗池背景，可选背景：第一阶段未加载延迟 ATS 周报，且该域不得伪装成实时暗池信号。',
    )).toBeInTheDocument();
    expect(screen.getByRole('table', { name: '每日机会候选表' })).toBeInTheDocument();
    expect(screen.getByText('计算可复现')).toBeInTheDocument();
    expect(screen.getByText('策略尚未验证')).toBeInTheDocument();
    expect(screen.getByText(
      '市场背景未就绪：Regime；不阻断基础量价结构浏览。',
    )).toBeInTheDocument();
    expect(screen.queryByText(/待补证据：.*Playbook/)).not.toBeInTheDocument();
  });

  it('keeps a no-Regime candidate visible as watch-only research', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByText('基础候选 · 非信号')).toBeInTheDocument();
    fireEvent.click(screen.getByText('查看全部证据与数据状态'));
    expect(screen.getByText('当日已保存 Regime')).toBeInTheDocument();
    expect(screen.getByText('当前交易日没有已存储 Regime。')).toBeInTheDocument();
    expect(screen.getByText(/当前交易日没有已存储 Regime，候选只能进入观察队列/)).toBeInTheDocument();
  });

  it('runs a non-cached refresh from the button', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );
    await screen.findByText('NVDA');
    await waitFor(() => expect(fetchOpportunityOptionEvents).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(fetchOpportunityOptionOverview).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1));
    expect(fetchOpportunityOptionContext).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '刷新只读预览' }));
    await waitFor(() => {
      expect(fetchDailyOpportunities).toHaveBeenLastCalledWith(['NVDA'], 10, { refresh: true });
    });
    await waitFor(() => {
      expect(fetchOpportunityOptionOverview).toHaveBeenLastCalledWith(['NVDA'], { refresh: true });
    });
    await waitFor(() => {
      expect(fetchOpportunityOptionWalls).toHaveBeenLastCalledWith(['NVDA'], 0, 45, { refresh: true });
    });
    await waitFor(() => {
      expect(fetchOpportunityOptionEvents).toHaveBeenLastCalledWith(['NVDA'], 5, { refresh: true });
    });
    expect(fetchOpportunityOptionContext).not.toHaveBeenCalled();
  });

  it('keeps the last successful run visible and dated while a refresh is pending or fails', async () => {
    const refreshRequest = deferred<DailyOpportunityRun>();
    vi.mocked(fetchDailyOpportunities)
      .mockResolvedValueOnce(runFor('NVDA'))
      .mockReturnValueOnce(refreshRequest.promise);
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: '刷新只读预览' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '刷新只读预览' }));

    expect(await screen.findByText('刷新中 · 显示旧结果')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新只读预览' })).toBeDisabled();
    expect(screen.getByText(/当前继续显示 2026-07-22 08:00 ET 的上一次成功结果/)).toBeInTheDocument();
    expect(screen.getAllByText('NVDA').length).toBeGreaterThan(0);

    await act(async () => {
      refreshRequest.reject(new Error('daily source timeout'));
      await refreshRequest.promise.catch(() => undefined);
    });

    expect(await screen.findByText('刷新失败 · 显示旧结果')).toBeInTheDocument();
    expect(screen.getByText(/daily source timeout.*2026-07-22 08:00 ET 的上一次可用结果/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新只读预览' })).toBeEnabled();
    expect(screen.getAllByText('NVDA').length).toBeGreaterThan(0);
  });

  it('names the actual base-data blocker instead of calling every gap insufficient evidence', async () => {
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(blockedHistoryRun('NVDA'));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('行情源暂不可用')).toBeInTheDocument();
    expect(screen.getByText('基础数据待恢复：完整日线')).toBeInTheDocument();
    expect(screen.queryByText('证据不足')).not.toBeInTheDocument();
  });

  it('keeps the last usable run when a refresh returns only temporary data blockers', async () => {
    vi.mocked(fetchDailyOpportunities)
      .mockResolvedValueOnce(runFor('NVDA'))
      .mockResolvedValueOnce(blockedHistoryRun('NVDA'));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('基础候选 · 非信号')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '刷新只读预览' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '刷新只读预览' }));
    expect(await screen.findByText(
      /本次刷新没有取得可用日线，已保留上一次成功结果/,
    )).toBeInTheDocument();
    expect(screen.getByText('基础候选 · 非信号')).toBeInTheDocument();
    expect(screen.queryByText('行情源暂不可用')).not.toBeInTheDocument();
  });

  it('renders the base candidate before asynchronously adding IV and IV Rank to the list', async () => {
    const overviewRequest = deferred<OpportunityOptionOverviewResponse>();
    vi.mocked(fetchOpportunityOptionOverview).mockReturnValueOnce(overviewRequest.promise);
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByText('IV / Rank')).toBeInTheDocument();
    expect(screen.getAllByText('加载中…').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '阶段 2/3 · 期权概览' })).toBeDisabled();
    expect(screen.getByText('2 期权概览 · 加载中')).toBeInTheDocument();
    expect(fetchOpportunityOptionOverview).toHaveBeenCalledWith(['NVDA']);

    await act(async () => {
      overviewRequest.resolve(optionOverviewResponse([
        optionOverviewItem('NVDA', {
          ivPercent: 42.35,
          ivRankPercent: 68.4,
        }),
      ]));
      await overviewRequest.promise;
    });

    expect(await screen.findByText('42.35%')).toBeInTheDocument();
    expect(screen.getByText(/Rank 68\.4%/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新只读预览' })).toBeEnabled();
  });

  it('advances to the Top 5 wall stage after overview settles', async () => {
    const overviewRequest = deferred<OpportunityOptionOverviewResponse>();
    const wallRequest = deferred<OpportunityOptionWallResponse>();
    vi.mocked(fetchOpportunityOptionOverview).mockReturnValueOnce(overviewRequest.promise);
    vi.mocked(fetchOpportunityOptionWalls).mockReturnValueOnce(wallRequest.promise);
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole(
      'button',
      { name: '阶段 2/3 · 期权概览' },
    )).toBeDisabled();
    expect(screen.queryByRole(
      'button',
      { name: '阶段 3/3 · Top 5 墙' },
    )).not.toBeInTheDocument();
    expect(screen.getByText('2 期权概览 · 加载中')).toBeInTheDocument();
    expect(screen.getByText('3 Top 5 墙 · 加载中')).toBeInTheDocument();
    expect(fetchOpportunityOptionOverview).toHaveBeenCalledWith(['NVDA']);
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledWith(['NVDA'], 0, 45);

    await act(async () => {
      overviewRequest.resolve(optionOverviewResponse([optionOverviewItem('NVDA')]));
      await overviewRequest.promise;
    });

    expect(await screen.findByRole('button', { name: '阶段 3/3 · Top 5 墙' })).toBeDisabled();
    expect(screen.getByText('1 基础榜 · 就绪')).toBeInTheDocument();
    expect(screen.getByText('2 期权概览 · 就绪')).toBeInTheDocument();
    expect(screen.getByText('3 Top 5 墙 · 加载中')).toBeInTheDocument();

    await act(async () => {
      wallRequest.resolve(optionWallResponse([optionWallItem('NVDA')]));
      await wallRequest.promise;
    });

    expect(await screen.findByText('基础榜可用 · 增强降级')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新只读预览' })).toBeEnabled();
  });

  it('marks optional failures and partial walls as enhancement degradation', async () => {
    vi.mocked(fetchOpportunityOptionOverview).mockRejectedValue(new Error('overview offline'));
    vi.mocked(fetchOpportunityOptionWalls).mockResolvedValue(optionWallResponse([
      {
        ...readyOptionWallItem('NVDA'),
        state: 'partial',
        coverage: {
          ...readyOptionWallItem('NVDA').coverage,
          validContracts: 120,
          requestedContracts: 210,
          coveragePercent: 57.14,
          failedBatches: 2,
        },
      },
    ]));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('基础榜可用 · 增强降级')).toBeInTheDocument();
    const status = screen.getByLabelText('今日机会研究状态');
    expect(status).toHaveTextContent('2 期权概览 · 降级');
    expect(status).toHaveTextContent('3 Top 5 墙 · 降级');
    expect(status).toHaveTextContent('增强降级：期权概览、Top 5 墙部分或不可用');
    expect(screen.getByText('墙位 部分覆盖 · C 135 / P 120')).toBeInTheDocument();
    expect(screen.getByLabelText('NVDA 期权墙，部分覆盖')).toBeInTheDocument();
    expect(screen.getByText(/期权墙部分覆盖 · 有效合约 120\/210 · 57\.1%/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新只读预览' })).toBeEnabled();
  });

  it('surfaces stale completed bars as a base-data warning', async () => {
    const staleRun = runFor('NVDA');
    staleRun.runReadiness = staleRun.runReadiness.map((source) => (
      source.domain === 'daily_history' ? { ...source, state: 'stale' as const } : source
    ));
    staleRun.candidates = staleRun.candidates.map((candidate) => ({
      ...candidate,
      readiness: candidate.readiness.map((source) => (
        source.domain === 'daily_history' ? { ...source, state: 'stale' as const } : source
      )),
    }));
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(staleRun);
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('基础日线过期 · 仅作背景')).toBeInTheDocument();
    expect(screen.getByText('1 基础榜 · 降级')).toBeInTheDocument();
    expect(screen.queryByText('证据不足')).not.toBeInTheDocument();
  });

  it('requests option overview for every candidate and renders IV and Rank beyond the first three', async () => {
    const tickers = ['NVDA', 'AAPL', 'MSFT', 'TSLA'];
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(runForSymbols(tickers));
    vi.mocked(fetchOpportunityOptionOverview).mockResolvedValue(optionOverviewResponse([
      optionOverviewItem('NVDA', { ivPercent: 42.35, ivRankPercent: 68.4 }),
      optionOverviewItem('AAPL', { ivPercent: 31.2, ivRankPercent: 45.6 }),
      optionOverviewItem('MSFT', { ivPercent: 28.1, ivRankPercent: 39.5 }),
      optionOverviewItem('TSLA', { ivPercent: 56.7, ivRankPercent: 82.3 }),
    ]));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={tickers} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('TSLA')).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchOpportunityOptionOverview).toHaveBeenCalledWith(tickers);
    });
    expect(await screen.findByText('56.7%')).toBeInTheDocument();
    expect(screen.getByText(/Rank 82\.3%/)).toBeInTheDocument();
    expect(screen.queryByText(/首批未扫描/)).not.toBeInTheDocument();
  });

  it('uses a compact master-detail layout and keeps unavailable walls explicit', async () => {
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(runForSymbols(['NVDA', 'AAPL']));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA', 'AAPL']} />
        <LocationProbe />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByText('NVDA · 研究详情')).toBeInTheDocument();
    expect(screen.getByText('我的 Watchlist · 扫描 2 只')).toBeInTheDocument();
    expect(screen.getByText(/Top 5 规则匹配候选由/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '管理 / 导入自选' }));
    expect(screen.getByLabelText('current route')).toHaveTextContent('/watchlist');
    expect(screen.getByText('计算可复现')).toBeInTheDocument();
    expect(screen.getByText('策略尚未验证')).toBeInTheDocument();
    expect(screen.queryByText('选择查看执行价墙')).not.toBeInTheDocument();
    expect(await screen.findAllByText('墙位 未配置')).toHaveLength(2);
    expect(screen.getAllByText(/守住 20日高 127，EMA8 > EMA13/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/收盘跌回 127 下方或 EMA8 下穿 EMA13/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: '打开 NVDA 完整机会分析' })[0]);
    expect(screen.getByLabelText('current route')).toHaveTextContent('/regime/opportunity/NVDA');
    // 非官方（preview）榜单不得伪装官方快照绑定。
    expect(screen.getByLabelText('current route')).not.toHaveTextContent('snapshotKey');
    expect(await screen.findByText(
      '未取得真实按执行价数据时，不会用 ATM IV 推断 Call Wall 或 Put Wall。',
    )).toBeInTheDocument();

    selectCandidateRow('AAPL');
    expect(screen.getByText('AAPL · 研究详情')).toBeInTheDocument();
    expect(screen.queryByText('NVDA · 研究详情')).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: '打开 AAPL 完整机会分析' })[0]);
    expect(screen.getByLabelText('current route')).toHaveTextContent('/regime/opportunity/AAPL');
  });

  it('batch-loads observable option walls for the Top 5 and reuses them in selected detail', async () => {
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(runForSymbols(['NVDA', 'AAPL']));
    vi.mocked(fetchOpportunityOptionWalls).mockImplementation(async (symbols, dteMin, dteMax) => (
      optionWallResponse(symbols.map((ticker) => readyOptionWallItem(ticker, dteMin, dteMax)))
    ));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA', 'AAPL']} />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('NVDA 期权墙，可用')).toBeInTheDocument();
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledWith(['NVDA', 'AAPL'], 0, 45);
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText('墙位 C 135 / P 120')).toHaveLength(2);
    expect(screen.getByText('Call OI 墙（集中位）')).toBeInTheDocument();
    expect(screen.getByText('Put OI 墙（集中位）')).toBeInTheDocument();
    expect(screen.getByText('Gross Gamma 集中位')).toBeInTheDocument();
    expect(screen.getByText(/不是真实 Dealer GEX，也不计算 Gamma Flip/)).toBeInTheDocument();

    expect(
      screen.getByText('到期分布 · OI＝T-1 清算 · 报价证据部分缺失'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '2026-07-24 · DTE 2 · 占该位 60% · IV 41.2% · Bid/Ask/Mark 标缺（快照未含盘口报价） · 2026-07-22 09:44:00',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '2026-07-31 · DTE 9 · 占该位 40% · IV 标缺 · Bid/Ask/Mark 标缺（快照未含盘口报价）',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('标缺字段为快照未提供的数据，未用估算或旧值回填。'),
    ).toBeInTheDocument();

    selectCandidateRow('AAPL');
    expect(await screen.findByLabelText('AAPL 期权墙，可用')).toBeInTheDocument();
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1);
  });

  it('switches DTE scope by refetching only the selected symbol and one bucket at a time', async () => {
    vi.mocked(fetchOpportunityOptionWalls).mockImplementation(async (symbols, dteMin, dteMax) => (
      optionWallResponse(symbols.map((ticker) => ({
        ...readyOptionWallItem(ticker, dteMin, dteMax),
        atmCallIv: {
          state: 'ready',
          expiry: dteMin === 0 ? '2026-07-24' : '2026-08-21',
          strike: 130,
          atmCallIvPercent: dteMin === 0 ? 42.5 : 99,
          selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot',
        },
      })))
    ));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('NVDA 期权墙，可用')).toBeInTheDocument();
    expect(screen.getByText(
      '最近到期 2026-07-24 · ATM Call IV 42.5%',
    )).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '0–45' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '0–7' }));
    await waitFor(() => {
      expect(fetchOpportunityOptionWalls).toHaveBeenLastCalledWith(['NVDA'], 0, 7);
    });
    expect(screen.getByRole('button', { name: '0–7' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByText('DTE 0–7 · 2 个到期日')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '8–45' }));
    await waitFor(() => {
      expect(fetchOpportunityOptionWalls).toHaveBeenLastCalledWith(['NVDA'], 8, 45);
    });
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(3);
    expect(screen.getByText(
      '最近到期 2026-07-24 · ATM Call IV 42.5%',
    )).toBeInTheDocument();
    expect(screen.queryByText(/ATM Call IV 99%/)).not.toBeInTheDocument();
  });

  it('does not let an old Top-5 option-wall request overwrite a newer symbol run', async () => {
    const oldWallRequest = deferred<OpportunityOptionWallResponse>();
    const newWallRequest = deferred<OpportunityOptionWallResponse>();
    vi.mocked(fetchDailyOpportunities).mockImplementation(async (symbols) => runForSymbols(symbols));
    vi.mocked(fetchOpportunityOptionWalls)
      .mockImplementationOnce(() => oldWallRequest.promise)
      .mockImplementationOnce(() => newWallRequest.promise);
    const view = render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA', 'AAPL']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA · 研究详情')).toBeInTheDocument();
    await waitFor(() => expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1));

    view.rerender(
      <MemoryRouter>
        <DailyOpportunityList symbols={['MSFT', 'TSLA']} />
      </MemoryRouter>,
    );
    expect(await screen.findByText('MSFT · 研究详情')).toBeInTheDocument();
    await waitFor(() => expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(2));

    await act(async () => {
      newWallRequest.resolve(optionWallResponse([
        readyOptionWallItem('MSFT'),
        readyOptionWallItem('TSLA'),
      ]));
      await newWallRequest.promise;
    });
    expect(await screen.findByLabelText('MSFT 期权墙，可用')).toBeInTheDocument();

    await act(async () => {
      oldWallRequest.resolve(optionWallResponse([
        readyOptionWallItem('NVDA'),
        readyOptionWallItem('AAPL'),
      ]));
      await oldWallRequest.promise;
    });
    expect(screen.getByLabelText('MSFT 期权墙，可用')).toBeInTheDocument();
    expect(screen.queryByLabelText('NVDA 期权墙，可用')).not.toBeInTheDocument();
  });

  it('shows only the Top 5 in the primary table and folds the remaining candidates', async () => {
    const tickers = ['NVDA', 'AAPL', 'MSFT', 'TSLA', 'AMD', 'META', 'GOOGL'];
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(runForSymbols(tickers));
    vi.mocked(fetchOpportunityOptionWalls).mockImplementation(async (symbols) => (
      optionWallResponse(symbols.map((ticker) => readyOptionWallItem(ticker)))
    ));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={tickers} />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('NVDA 期权墙，可用')).toBeInTheDocument();
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledWith(tickers.slice(0, 5), 0, 45);
    expect(screen.getByRole('table', { name: '每日机会候选表' }).querySelectorAll('tbody tr')).toHaveLength(5);
    const remainingDisclosure = screen.getByText('其余候选 · 2 个', { exact: false }).closest('details');
    expect(remainingDisclosure).not.toHaveAttribute('open');
    expect(screen.getByRole('table', { name: '其余机会候选表' }).querySelectorAll('tbody tr')).toHaveLength(2);
    expect(screen.queryByText('选择查看执行价墙')).not.toBeInTheDocument();
  });

  it('reuses the default Top 5 wall response for selected ATM IV without option-context', async () => {
    const tickers = ['NVDA', 'AAPL', 'MSFT', 'TSLA', 'AMZN'];
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(runForSymbols(tickers));
    vi.mocked(fetchOpportunityOptionWalls).mockImplementation(async (symbols, dteMin, dteMax) => (
      optionWallResponse(symbols.map((ticker, index) => ({
        ...readyOptionWallItem(ticker, dteMin, dteMax),
        atmCallIv: {
          state: 'ready',
          expiry: '2026-07-24',
          strike: 130 + index,
          atmCallIvPercent: 40 + index,
          selectionMethod: 'nearest_expiry_atm_call_from_same_wall_snapshot',
        },
      })))
    ));

    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={tickers} />
      </MemoryRouter>,
    );

    expect(await screen.findByText(
      '最近到期 2026-07-24 · ATM Call IV 40%',
    )).toBeInTheDocument();
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledWith(tickers, 0, 45);
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1);
    expect(fetchOpportunityOptionContext).not.toHaveBeenCalled();
    expect(screen.getByText(/复用 Top 5 墙同批动态快照，未再次请求期权链/)).toBeInTheDocument();

    selectCandidateRow('AMZN');
    expect(await screen.findByText(
      '最近到期 2026-07-24 · ATM Call IV 44%',
    )).toBeInTheDocument();
    expect(fetchOpportunityOptionWalls).toHaveBeenCalledTimes(1);
    expect(fetchOpportunityOptionContext).not.toHaveBeenCalled();
  });

  it('loads and labels recent unusual option events only for the selected symbol', async () => {
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(runForSymbols(['NVDA', 'AAPL']));
    vi.mocked(fetchOpportunityOptionEvents).mockImplementation(async (symbols) => (
      optionEventResponse(symbols.map((ticker) => optionEventItem(ticker, {
        state: 'ready',
        eventAsOf: '2026-07-22T10:15:31-04:00',
        allCount: 18,
        events: [optionEvent(`${ticker}-event`, {
          optionCode: `US.${ticker}260724C00135000`,
          ownerCode: `US.${ticker}`,
          symbol: `${ticker} 260724 135C`,
        })],
      })))
    ));
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA', 'AAPL']} />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('NVDA 最近交易时段异常期权成交，可用')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'NVDA 异常期权成交事件列表' })).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: 'US.NVDA260724C00135000 异常期权成交' })).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'NVDA 异常期权成交事件表' })).not.toBeInTheDocument();
    expect(fetchOpportunityOptionEvents).toHaveBeenCalledWith(['NVDA'], 5);
    expect(fetchOpportunityOptionEvents).not.toHaveBeenCalledWith(['AAPL'], 5);
    expect(screen.getByText('买方分类 · 偏多标签')).toBeInTheDocument();
    expect(screen.getByText('Sweep · 单腿')).toBeInTheDocument();
    expect(screen.getByText('$357.0K')).toBeInTheDocument();
    expect(screen.getByText('48.2% · V/OI 236.8% · Delta 0.420', { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/不等于开平仓、真实主动买卖方向或 Dealer 仓位/)).toBeInTheDocument();
    expect(screen.getByText(/本区块暂不进入候选排名/)).toBeInTheDocument();

    selectCandidateRow('AAPL');
    await waitFor(() => {
      expect(fetchOpportunityOptionEvents).toHaveBeenLastCalledWith(['AAPL'], 5);
    });
    expect(await screen.findByLabelText('AAPL 最近交易时段异常期权成交，可用')).toBeInTheDocument();
  });

  it('distinguishes a successful empty event query from an unavailable source', async () => {
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('NVDA 最近交易时段异常期权成交，无事件')).toBeInTheDocument();
    expect(screen.getByText('当前筛选范围内没有异常期权成交事件。')).toBeInTheDocument();
    expect(screen.getByText(/成功查询不等于确认不存在全部异常活动/)).toBeInTheDocument();

    vi.mocked(fetchOpportunityOptionEvents).mockRejectedValueOnce(new Error('event source offline'));
    fireEvent.click(screen.getByRole('button', { name: '刷新只读预览' }));
    expect(await screen.findByText(/异常期权成交 · 不可用 · event source offline/)).toBeInTheDocument();
  });

  it('does not let an old event request overwrite the newly selected symbol', async () => {
    const oldEventRequest = deferred<OpportunityOptionEventResponse>();
    const newEventRequest = deferred<OpportunityOptionEventResponse>();
    vi.mocked(fetchDailyOpportunities).mockResolvedValue(runForSymbols(['NVDA', 'AAPL']));
    vi.mocked(fetchOpportunityOptionEvents)
      .mockImplementationOnce(() => oldEventRequest.promise)
      .mockImplementationOnce(() => newEventRequest.promise);
    render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA', 'AAPL']} />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NVDA · 研究详情')).toBeInTheDocument();
    await waitFor(() => expect(fetchOpportunityOptionEvents).toHaveBeenCalledTimes(1));
    selectCandidateRow('AAPL');
    await waitFor(() => expect(fetchOpportunityOptionEvents).toHaveBeenCalledTimes(2));

    await act(async () => {
      newEventRequest.resolve(optionEventResponse([optionEventItem('AAPL', {
        state: 'ready',
        eventAsOf: '2026-07-22T10:15:31-04:00',
        allCount: 1,
        events: [optionEvent('aapl-event', { ownerCode: 'US.AAPL' })],
      })]));
      await newEventRequest.promise;
    });
    expect(await screen.findByLabelText('AAPL 最近交易时段异常期权成交，可用')).toBeInTheDocument();

    await act(async () => {
      oldEventRequest.resolve(optionEventResponse([optionEventItem('NVDA', {
        state: 'ready',
        eventAsOf: '2026-07-22T10:15:31-04:00',
        allCount: 1,
        events: [optionEvent('nvda-event')],
      })]));
      await oldEventRequest.promise;
    });
    expect(screen.getByLabelText('AAPL 最近交易时段异常期权成交，可用')).toBeInTheDocument();
    expect(screen.queryByLabelText('NVDA 最近交易时段异常期权成交，可用')).not.toBeInTheDocument();
  });

  it('does not let a slower old request overwrite results for newer symbols', async () => {
    const oldRequest = deferred<DailyOpportunityRun>();
    const newRequest = deferred<DailyOpportunityRun>();
    vi.mocked(fetchDailyOpportunities)
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => newRequest.promise);

    const view = render(
      <MemoryRouter>
        <DailyOpportunityList symbols={['NVDA']} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(fetchDailyOpportunities).toHaveBeenCalledTimes(1));

    view.rerender(
      <MemoryRouter>
        <DailyOpportunityList symbols={['AAPL']} />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(fetchDailyOpportunities).toHaveBeenLastCalledWith(['AAPL'], 10, { refresh: false });
    });

    await act(async () => {
      newRequest.resolve(runFor('AAPL'));
      await newRequest.promise;
    });
    expect(await screen.findByText('AAPL')).toBeInTheDocument();

    await act(async () => {
      oldRequest.resolve(runFor('NVDA'));
      await oldRequest.promise;
    });
    expect(screen.getByText('AAPL')).toBeInTheDocument();
    expect(screen.queryByText('NVDA')).not.toBeInTheDocument();
  });
});

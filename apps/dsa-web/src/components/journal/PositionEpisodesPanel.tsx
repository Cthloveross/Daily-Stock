import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiErrorAlert } from '../common/ApiErrorAlert';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { Drawer } from '../common/Drawer';
import { InlineAlert } from '../common/InlineAlert';
import { Pagination } from '../common/Pagination';
import { parseApiError, type ParsedApiError } from '../../api/error';
import {
  activateEpisodeBuild,
  fetchEpisodeBuildActivation,
} from '../../api/journal';
import type {
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildActivationResponse,
  EpisodeBuildActivationState,
  EpisodeBuildResponse,
  PositionEpisodeDetailResponse,
  PositionEpisodeEvidenceItem,
  PositionEpisodeFilters,
  PositionEpisodeItem,
  PositionEpisodeListResponse,
} from '../../types/journal';

interface PositionEpisodeController {
  list: PositionEpisodeListResponse | null;
  loading: boolean;
  error: ParsedApiError | null;
  reload: () => void;
  detailById: Record<number, PositionEpisodeDetailResponse>;
  detailLoadingId: number | null;
  detailError: ParsedApiError | null;
  loadDetail: (episodeId: number) => Promise<void>;
  building: boolean;
  buildResult: { message: string } | null;
  buildAssumedFlat: () => Promise<unknown>;
  canonicalPreview: CanonicalEpisodeBuildPlanResponse | null;
  canonicalPreviewLoading: boolean;
  canonicalPreviewError: ParsedApiError | null;
  reloadCanonicalPreview: () => void;
  canonicalBuilding: boolean;
  canonicalBuildResult: EpisodeBuildResponse | null;
  buildCanonical: (
    acceptAssumedFlat: boolean,
    acceptGroupFeeScope: boolean,
  ) => Promise<EpisodeBuildResponse>;
}

interface PositionEpisodesPanelProps {
  filters: PositionEpisodeFilters;
  controller: PositionEpisodeController;
  onApplyFilters: (filters: PositionEpisodeFilters) => void;
  onPageChange: (page: number) => void;
  onSelectBuild: (buildId?: number) => void;
  onOpenReview?: (item: PositionEpisodeItem) => void;
  onImported?: () => void;
}

function normaliseDecimal(value: string): { sign: string; whole: string; fraction: string } {
  const raw = value.trim();
  const sign = raw.startsWith('-') ? '−' : raw.startsWith('+') ? '+' : '';
  const unsigned = raw.replace(/^[+-]/, '');
  const [whole = '0', fraction = ''] = unsigned.split('.', 2);
  return { sign, whole: whole.replace(/\B(?=(\d{3})+(?!\d))/g, ','), fraction };
}

function formatDecimalDisplay(value?: string | null, money = false): string {
  if (value == null || !value.trim()) return '—';
  const { sign, whole, fraction } = normaliseDecimal(value);
  const significantFraction = fraction.replace(/0+$/, '');
  const displayFraction = money
    ? significantFraction.padEnd(2, '0')
    : significantFraction;
  const body = `${whole}${displayFraction ? `.${displayFraction}` : ''}`;
  return money ? `${sign}$${body}` : `${sign}${body}`;
}

function formatCurrencyAmount(currency: string, value?: string | null): string {
  const amount = formatDecimalDisplay(value, false);
  return amount === '—' ? '—' : `${currency} ${amount}`;
}

function decimalTone(value?: string | null): string {
  if (!value) return 'text-text-3';
  if (value.trim().startsWith('-')) return 'text-down-strong';
  if (/^\+?0(?:\.0+)?$/.test(value.trim())) return 'text-text-2';
  return 'text-up-strong';
}

function formatEt(value?: string | null, includeSeconds = false): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...(includeSeconds ? { second: '2-digit' as const } : {}),
    hour12: false,
  }).format(parsed);
}

function formatHold(seconds?: number | null): string {
  if (seconds == null) return '—';
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function qualityLabel(item: PositionEpisodeItem): string {
  if (item.quality.assumedFlatUnverified) return '期初假设';
  if (item.quality.isLeftCensored) return '左截断';
  if (item.quality.completenessStatus === 'partial') return '部分证据';
  return '证据完整';
}

function lifecycleLabel(status: string): string {
  if (status === 'open') return '证据窗口末未归零';
  if (status === 'closed') return '证据窗口内已归零';
  return status;
}

function reviewStatusLabel(status?: string): string {
  if (status === 'in_progress') return '进行中';
  if (status === 'completed') return '已完成';
  return '未开始';
}

const CASE_FOCUS_LABELS: Record<string, string> = {
  top_profit: '盈利最多',
  top_loss: '亏损最多',
  largest_fee: '费用最高',
  longest_hold: '持有最长',
  weakest_evidence: '证据最不完整',
};

function canonicalWarningLabel(warning: string): string {
  const labels: Record<string, string> = {
    opening_boundary_unverified: '期初持仓边界尚未由持仓快照验证。',
    source_batch_partial: '来源批次包含仅有订单汇总证据的历史成交。',
    episode_quality_partial: '部分仓位回合仍保留“部分证据”质量标记。',
    no_execution_episodes: '当前事实集没有可生成仓位回合的执行事件。',
    unresolved_execution_evidence: '仍有执行证据尚未分配到仓位回合。',
    execution_group_fee_retained_unallocated: '组合执行组费用已完整保留在组级；受影响腿不显示费用或净收益，也不进入严格 Headline。',
    'explicit assumed-flat acceptance is required': '生成前必须明确接受未验证的期初空仓假设。',
    'execution-group fee remains exact only at group scope; affected leg episodes have no fee/net P&L and require explicit acceptance': '组合执行组费用已完整保留在组级；受影响腿不显示费用或净收益，也不进入严格 Headline。',
    'confirming this plan will not replace the default position review': '确认生成后，默认复盘构建不会被替换。',
  };
  return labels[warning] ?? warning;
}

function activationSourceLabel(source?: EpisodeBuildActivationState['selectionSource']): string {
  if (source === 'activation') return '显式默认选择记录';
  if (source === 'csv_fallback') return '历史 CSV 默认构建';
  if (source === 'none') return '尚未选择';
  return '读取中';
}

function MetaBlock({ title, value }: { title: string; value: Record<string, unknown> }) {
  const entries = Object.entries(value);
  if (!entries.length) return null;
  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1 p-3">
      <h3 className="text-label uppercase tracking-label text-text-3">{title}</h3>
      <dl className="mt-2 grid gap-x-4 gap-y-2 text-body-sm sm:grid-cols-2">
        {entries.map(([key, item]) => (
          <div key={key} className="min-w-0">
            <dt className="text-text-3">{key}</dt>
            <dd className="break-words font-mono text-mono-xs text-text-1">
              {typeof item === 'string' ? item : JSON.stringify(item)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function isOrderTimeProxy(evidence: PositionEpisodeEvidenceItem): boolean {
  return evidence.evidenceKind === 'order'
    || evidence.allocation.timingPrecision === 'order_time_proxy';
}

function EvidenceCard({ evidence }: { evidence: PositionEpisodeEvidenceItem }) {
  const orderProxy = isOrderTimeProxy(evidence);
  return (
    <li className="rounded-ds-md border border-subtle bg-bg-1 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-mono-xs text-text-3">#{evidence.allocationSequence}</span>
          <span className="text-body-sm font-medium text-text-1">
            {evidence.eventRole} · {evidence.evidenceKind}
          </span>
        </div>
        {orderProxy && (
          <span className="rounded-full border border-warn-strong/30 bg-warn-subtle px-2 py-1 text-caption text-warn-strong">
            订单时间代理 · 非逐笔成交时间
          </span>
        )}
      </div>
      <div className="mt-3 grid gap-2 text-body-sm sm:grid-cols-2">
        <div><span className="text-text-3">证据时间（ET）</span><div className="font-mono text-mono-xs text-text-1">{formatEt(evidence.evidenceTime)}</div></div>
        <div><span className="text-text-3">分配数量</span><div className="font-mono text-mono-xs text-text-1">{formatDecimalDisplay(evidence.allocatedQuantity)}</div></div>
        <div><span className="text-text-3">分配费用</span><div className="font-mono text-mono-xs text-text-1">{formatDecimalDisplay(evidence.allocatedFee, true)}</div></div>
        <div><span className="text-text-3">分配现金流</span><div className="font-mono text-mono-xs text-text-1">{formatDecimalDisplay(evidence.allocatedCashFlow, true)}</div></div>
      </div>
      <p className="mt-2 break-all font-mono text-[11px] text-text-4">
        {evidence.brokerFillObservationId != null
          ? `fill observation ${evidence.brokerFillObservationId}`
          : `order observation ${evidence.brokerOrderObservationId ?? '—'}`}
      </p>
    </li>
  );
}

function EpisodeDrawer({
  item,
  detail,
  loading,
  error,
  onOpenFullReview,
  onClose,
}: {
  item: PositionEpisodeItem | null;
  detail?: PositionEpisodeDetailResponse;
  loading: boolean;
  error: ParsedApiError | null;
  onOpenFullReview?: (item: PositionEpisodeItem) => void;
  onClose: () => void;
}) {
  return (
    <Drawer
      isOpen={item != null}
      onClose={onClose}
      title={item ? `${item.instrument.underlying} · 仓位回合` : undefined}
      width="max-w-3xl"
      backdropClassName="bg-black/55"
    >
      {item && (
        <div className="space-y-4">
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[
              ['合约', item.instrument.rawSymbol],
              ['状态', lifecycleLabel(item.lifecycleStatus)],
              ['方向', item.direction],
              ['开仓时间（ET）', formatEt(item.openedAt)],
              ['平仓时间（ET）', formatEt(item.closedAt)],
              ['持有', formatHold(item.holdSeconds)],
              ['开仓数量', formatDecimalDisplay(item.openedQuantity)],
              ['已平数量', formatDecimalDisplay(item.closedQuantity)],
              ['证据窗口末数量', formatDecimalDisplay(item.remainingQuantity)],
              ['净盈亏', item.lifecycleStatus === 'open' ? '—' : formatDecimalDisplay(item.realizedPnlNet, true)],
              ['证据完整度', item.quality.completenessScore],
              ['构建依据', item.quality.constructionBasis],
            ].map(([label, value]) => (
              <div key={label} className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <div className="text-caption text-text-3">{label}</div>
                <div className="mt-1 break-words font-mono text-mono-sm text-text-1">{value}</div>
              </div>
            ))}
          </section>

          {item.quality.assumedFlatUnverified && (
            <InlineAlert
              variant="warning"
              title="未验证期初空仓假设"
              message="此回合的历史左边界没有持仓快照证明；当前时点的券商持仓快照也不能倒推该历史期初。盈亏属于条件性结果，不进入 Headline。"
            />
          )}

          {item.quality.groupFeeUnallocated && (
            <InlineAlert
              variant="warning"
              title="组合费用仅在执行组层精确保留"
              message="这条腿的成交证据完整，但券商只提供整组费用。系统没有猜测分摊，因此本回合不显示腿级费用或净收益，也不进入 Headline。"
            />
          )}

          {onOpenFullReview && (
            <button
              type="button"
              className="btn-primary w-full justify-center"
              onClick={() => onOpenFullReview(item)}
            >
              打开完整复盘 · K 线与成交证据
            </button>
          )}

          {error && <ApiErrorAlert error={error} />}
          {loading && !detail && (
            <div className="rounded-ds-md border border-subtle bg-bg-1 p-6 text-center text-body-sm text-text-3">
              正在按需加载证据链…
            </div>
          )}
          {detail && (
            <>
              <MetaBlock title="Matching" value={detail.matching} />
              <MetaBlock title="Completeness" value={detail.completeness} />
              <MetaBlock title="Provenance" value={detail.provenance} />
              <section>
                <div className="flex items-center justify-between">
                  <h3 className="text-label uppercase tracking-label text-text-3">Evidence chain</h3>
                  <span className="font-mono text-mono-xs text-text-3">{detail.evidence.length} 条</span>
                </div>
                <ul className="mt-2 space-y-2">
                  {detail.evidence.map((evidence) => (
                    <EvidenceCard key={evidence.id} evidence={evidence} />
                  ))}
                </ul>
              </section>
            </>
          )}
        </div>
      )}
    </Drawer>
  );
}

function CanonicalEpisodeBuildCard({
  preview,
  loading,
  error,
  building,
  result,
  viewedBuildId,
  onRetry,
  onBuild,
  onViewBuild,
  onActivated,
}: {
  preview: CanonicalEpisodeBuildPlanResponse | null;
  loading: boolean;
  error: ParsedApiError | null;
  building: boolean;
  result: EpisodeBuildResponse | null;
  viewedBuildId?: number;
  onRetry: () => void;
  onBuild: (
    acceptAssumedFlat: boolean,
    acceptGroupFeeScope: boolean,
  ) => Promise<EpisodeBuildResponse>;
  onViewBuild: (buildId?: number) => void;
  onActivated: (response: EpisodeBuildActivationResponse) => void;
}) {
  const [acceptedPreviewKey, setAcceptedPreviewKey] = useState<string | null>(null);
  const [acceptedGroupFeePreviewKey, setAcceptedGroupFeePreviewKey] = useState<string | null>(null);
  const [acceptedActivationBuildId, setAcceptedActivationBuildId] = useState<number | null>(null);
  const [acceptedGroupFeeActivationBuildId, setAcceptedGroupFeeActivationBuildId] = useState<number | null>(null);
  const [activationState, setActivationState] = useState<EpisodeBuildActivationState | null>(null);
  const [activationLoading, setActivationLoading] = useState(true);
  const [activationSubmitting, setActivationSubmitting] = useState(false);
  const [activationError, setActivationError] = useState<ParsedApiError | null>(null);
  const [activationConflict, setActivationConflict] = useState(false);
  const [activationResult, setActivationResult] = useState<EpisodeBuildActivationResponse | null>(null);

  const loadActivationState = useCallback(async () => {
    setActivationLoading(true);
    setActivationError(null);
    setActivationConflict(false);
    try {
      const state = await fetchEpisodeBuildActivation();
      setActivationState(state);
      return state;
    } catch (reason) {
      setActivationError(parseApiError(reason));
      return null;
    } finally {
      setActivationLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!preview || preview.dataState !== 'ready' || preview.canonicalSetId == null) {
      setActivationLoading(false);
      return;
    }
    void loadActivationState();
  }, [loadActivationState, preview]);

  useEffect(() => {
    setAcceptedActivationBuildId(null);
    setAcceptedGroupFeeActivationBuildId(null);
    setActivationResult(null);
    setActivationError(null);
    setActivationConflict(false);
  }, [result?.build.id]);

  if (loading && !preview) {
    return (
      <section className="card-base p-5" aria-label="可信事实集构建预览">
        <div className="text-label uppercase tracking-label text-text-3">可信事实集构建预览</div>
        <p className="mt-2 text-body-sm text-text-3">正在核对事实集、费用与仓位回合计划…</p>
      </section>
    );
  }

  if (error && !preview) {
    return (
      <section className="card-base p-5" aria-label="可信事实集构建预览">
        <div className="text-label uppercase tracking-label text-text-3">可信事实集构建预览</div>
        <ApiErrorAlert className="mt-3" error={error} actionLabel="重新加载预览" onAction={onRetry} />
      </section>
    );
  }

  if (!preview || preview.dataState !== 'ready' || preview.canonicalSetId == null) {
    return (
      <section className="card-base p-5" aria-label="可信事实集构建预览">
        <div className="text-label uppercase tracking-label text-text-3">可信事实集构建预览</div>
        <h2 className="mt-2 text-h2 text-text-1">暂时没有可构建的可信事实集</h2>
        <p className="mt-1 text-body-sm text-text-3">先在“交易证据”中完成事实集确认，再回到这里生成可追溯的仓位复盘。</p>
      </section>
    );
  }

  const hasImmutableKeys = Boolean(preview.canonicalSetSha256 && preview.buildKey);
  const previewKey = `${preview.canonicalSetId}:${preview.canonicalSetSha256 ?? ''}`;
  const assumptionAccepted = acceptedPreviewKey === previewKey;
  const groupFeeScopeAccepted = acceptedGroupFeePreviewKey === previewKey;
  const assumptionConfirmed = !preview.requiresAssumedFlatAcceptance || assumptionAccepted;
  const groupFeeScopeConfirmed = (
    !preview.requiresGroupFeeScopeAcceptance || groupFeeScopeAccepted
  );
  const canBuild = preview.confirmAllowed
    && preview.feeConserved
    && !preview.defaultWillChange
    && !error
    && hasImmutableKeys
    && assumptionConfirmed
    && groupFeeScopeConfirmed;
  const deltaLabel = preview.episodeCountDelta > 0
    ? `+${preview.episodeCountDelta.toLocaleString()}`
    : preview.episodeCountDelta.toLocaleString();
  const warningLabels = Array.from(new Set(preview.warnings.map(canonicalWarningLabel)));
  const isViewingResult = result?.build.id === viewedBuildId;
  const targetRequiresAssumedFlat = Boolean(result?.build.assumedFlatUnverified);
  const targetRequiresGroupFeeScope = Boolean(
    result
    && result.build.groupFeeAffectedEpisodeCount > 0
    && !result.build.legFeeAttributionComplete,
  );
  const activationAssumptionAccepted = (
    result != null && acceptedActivationBuildId === result.build.id
  );
  const activationGroupFeeScopeAccepted = (
    result != null && acceptedGroupFeeActivationBuildId === result.build.id
  );
  const targetIsCurrent = (
    result != null
    && activationState?.selectionSource === 'activation'
    && activationState.currentBuildId === result.build.id
  );
  const targetHasImmutableKey = Boolean(
    result?.build.buildKey && /^[0-9a-f]{64}$/i.test(result.build.buildKey),
  );
  const canActivate = Boolean(
    result
    && activationState
    && targetHasImmutableKey
    && !targetIsCurrent
    && !activationLoading
    && !activationSubmitting
    && !activationError
    && (!targetRequiresAssumedFlat || activationAssumptionAccepted)
    && (!targetRequiresGroupFeeScope || activationGroupFeeScopeAccepted),
  );

  const handleActivate = async () => {
    if (!result || !activationState || !canActivate) return;
    setActivationSubmitting(true);
    setActivationError(null);
    setActivationConflict(false);
    try {
      const response = await activateEpisodeBuild(result.build.id, {
        expectedBuildKey: result.build.buildKey,
        expectedCurrentActivationId: activationState.currentActivationId ?? null,
        expectedCurrentBuildId: activationState.currentBuildId ?? null,
        acceptAssumedFlat: targetRequiresAssumedFlat && activationAssumptionAccepted,
        acceptGroupFeeScope: (
          targetRequiresGroupFeeScope && activationGroupFeeScopeAccepted
        ),
      });
      if (
        response.tradingActionPerformed !== false
        || response.state.currentBuildId !== result.build.id
        || response.state.selectionSource !== 'activation'
      ) {
        throw new Error('默认设置结果未通过只读复盘校验，页面没有切换默认复盘构建。');
      }
      setActivationState(response.state);
      setActivationResult(response);
      setAcceptedActivationBuildId(null);
      setAcceptedGroupFeeActivationBuildId(null);
      onActivated(response);
    } catch (reason) {
      const parsed = parseApiError(reason);
      const stale = parsed.status === 409
        && parsed.rawMessage.toLowerCase().includes('state changed');
      if (stale) {
        let stateReloaded = false;
        try {
          const current = await fetchEpisodeBuildActivation();
          setActivationState(current);
          stateReloaded = true;
        } catch {
          // Preserve the actionable CAS conflict; the retry button can reload
          // state again if this secondary read also failed.
        }
        setAcceptedActivationBuildId(null);
        setAcceptedGroupFeeActivationBuildId(null);
        setActivationConflict(true);
        setActivationError({
          ...parsed,
          title: '默认复盘构建已变化',
          message: stateReloaded
            ? '另一页面刚刚选择了不同的默认复盘构建。当前状态已重新读取，请核对后再次确认。'
            : '另一页面刚刚选择了不同的默认复盘构建。请重新读取当前状态，再核对并确认。',
        });
      } else {
        setActivationError(parsed);
      }
    } finally {
      setActivationSubmitting(false);
    }
  };

  return (
    <section className="card-base overflow-hidden" aria-label="可信事实集构建预览">
      <div className="border-b border-subtle bg-bg-2/60 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-label uppercase tracking-label text-text-3">可信事实集构建预览</div>
            <h2 className="mt-1 text-h2 text-text-1">从已确认事实生成一份可对比的仓位复盘</h2>
            <p className="mt-1 text-body-sm text-text-3">
              生成只会新增一份可追溯构建，<strong className="text-text-1">不会替换默认复盘构建</strong>。
            </p>
          </div>
          <span className="rounded-full border border-up-strong/25 bg-up-subtle px-2.5 py-1 text-caption text-up-strong">
            事实集 #{preview.canonicalSetId}
          </span>
        </div>
      </div>

      <div className="p-5">
        <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
            <dt className="text-caption text-text-3">可信事实</dt>
            <dd className="mt-1 font-mono text-mono-md text-text-1">{preview.sourceEventCount.toLocaleString()} 条事件</dd>
            <div className="mt-1 text-caption text-text-3">
              {preview.aggregateOrderEventCount.toLocaleString()} 聚合订单 · {preview.detailedFillEventCount.toLocaleString()} 逐笔成交
            </div>
          </div>
          <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
            <dt className="text-caption text-text-3">计划回合</dt>
            <dd className="mt-1 font-mono text-mono-md text-text-1">{preview.plannedPositionEpisodeCount.toLocaleString()}</dd>
            <div className="mt-1 text-caption text-text-3">
              {preview.plannedClosedEpisodeCount.toLocaleString()} 证据窗口内已归零 · {preview.plannedOpenEpisodeCount.toLocaleString()} 证据窗口末未归零
            </div>
          </div>
          <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
            <dt className="text-caption text-text-3">费用守恒</dt>
            <dd className={`mt-1 font-mono text-mono-md ${preview.feeConserved ? 'text-up-strong' : 'text-down-strong'}`}>
              {preview.feeConserved ? '已通过' : '未通过'}
            </dd>
            <div className="mt-1 text-caption text-text-3">
              已知来源 {formatDecimalDisplay(preview.sourceKnownFeeTotal, true)} · 已入账 {formatDecimalDisplay(preview.allocatedKnownFeeTotal, true)}
            </div>
            {preview.executionGroupCount > 0 && (
              <div className="mt-1 text-caption text-warn-strong">
                其中组合组费用 {formatDecimalDisplay(preview.retainedExecutionGroupFeeTotal, true)}
              </div>
            )}
          </div>
          <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
            <dt className="text-caption text-text-3">相对默认复盘构建</dt>
            <dd className="mt-1 font-mono text-mono-md text-text-1">{deltaLabel} 个回合</dd>
            <div className="mt-1 text-caption text-text-3">
              当前 {preview.defaultPositionEpisodeCount.toLocaleString()} → 计划 {preview.plannedPositionEpisodeCount.toLocaleString()}
            </div>
          </div>
        </dl>

        {preview.executionGroupCount > 0 && (
          <section
            className="mt-4 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-4"
            aria-label="组合执行组费用口径"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-label uppercase tracking-label text-warn-strong">Execution group accounting</div>
                <h3 className="mt-1 text-body font-semibold text-text-1">
                  {preview.executionGroupCount.toLocaleString()} 个组合执行组 · {preview.groupFeeAffectedEpisodeCount.toLocaleString()} 个腿回合受影响
                </h3>
                <p className="mt-1 max-w-3xl text-caption leading-relaxed text-text-3">
                  券商费用在整组层面精确，系统不会按腿猜测分摊。受影响腿保留成交和 Gross 证据，但费用与 Net 显示为空，并从严格 Headline 排除。
                </p>
              </div>
              <span className="rounded-full border border-warn-strong/30 px-2.5 py-1 text-caption text-warn-strong">
                腿级费用未归因
              </span>
            </div>
            {Object.keys(preview.feeConservationByCurrency).length > 0 && (
              <dl className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {Object.entries(preview.feeConservationByCurrency).map(([currency, values]) => (
                  <div key={currency} className="rounded-ds-sm border border-warn-strong/20 bg-bg-1/70 p-3">
                    <dt className="font-mono text-mono-xs text-text-2">{currency}</dt>
                    <dd className="mt-1 text-caption text-text-3">
                      来源 {formatCurrencyAmount(currency, values.sourceKnown)} · 已入账 {formatCurrencyAmount(currency, values.accounted)}
                    </dd>
                    <div className="mt-1 text-caption text-warn-strong">
                      组费用 {formatCurrencyAmount(currency, values.retainedExecutionGroup)}
                    </div>
                  </div>
                ))}
              </dl>
            )}
          </section>
        )}

        <div className="mt-3 text-caption text-text-3">
          <span>
            来源批次 {preview.sourceBatchIds.length ? preview.sourceBatchIds.join(', ') : '—'}
            {preview.canonicalSetSha256 && (
              <> · 指纹 <span className="font-mono" aria-label={`完整指纹 ${preview.canonicalSetSha256}`}>{preview.canonicalSetSha256.slice(0, 12)}…</span></>
            )}
          </span>
        </div>

        <section className="mt-4 rounded-ds-md border border-subtle bg-bg-1 p-3" aria-label="构建证据窗口">
          <div className="text-label uppercase tracking-label text-text-3">Evidence window · Historical projection</div>
          <dl className="mt-2 grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-caption text-text-3">证据窗口（ET）起—止</dt>
              <dd className="mt-1 font-mono text-mono-xs text-text-1">
                {formatEt(preview.sourceWindowStart, true)} — {formatEt(preview.sourceWindowEnd, true)}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-text-3">窗口末投影 as-of（ET）</dt>
              <dd className="mt-1 font-mono text-mono-xs text-text-1">{formatEt(preview.sourceWindowEnd, true)}</dd>
            </div>
          </dl>
          <p className="mt-2 text-caption text-text-3">
            计划中的未归零数量只投影到证据窗口末，不代表券商此刻持仓。
          </p>
        </section>

        <section className="mt-4 rounded-ds-md border border-subtle bg-bg-1 p-3" aria-label="默认复盘构建">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-label uppercase tracking-label text-text-3">默认复盘构建</div>
              <div className="mt-1 text-body-sm font-medium text-text-1">
                {activationLoading && !activationState
                  ? '正在读取默认复盘构建…'
                  : activationState?.currentBuildId != null
                    ? `构建 #${activationState.currentBuildId}`
                    : '尚无默认构建'}
              </div>
              <p className="mt-1 text-caption text-text-3">
                {activationSourceLabel(activationState?.selectionSource)}
                {activationState?.currentActivationSequence != null
                  ? ` · append-only 序号 #${activationState.currentActivationSequence}`
                  : ''}
              </p>
            </div>
            <span className="rounded-full border border-subtle px-2.5 py-1 text-caption text-text-2">
              本地复盘选择
            </span>
          </div>
          <p className="mt-2 text-caption text-text-3">
            设为默认只会新增一条本地复盘选择记录；不会修改 Moomoo 数据，也不会产生、修改或提交任何交易订单。
          </p>
        </section>

        {warningLabels.length > 0 && (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-caption text-warn-strong">
            {warningLabels.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}

        {error && <ApiErrorAlert className="mt-4" error={error} actionLabel="重新加载预览" onAction={onRetry} />}

        {result && (
          <div className="mt-4 space-y-3">
            <InlineAlert
              variant="success"
              title={`构建 #${result.build.id} 已就绪`}
              message="新构建已追加保存；默认复盘构建没有改变。请先显式查看，需要时再单独设为默认。"
              action={(
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={isViewingResult}
                  onClick={() => onViewBuild(result.build.id)}
                >
                  {isViewingResult ? '正在查看这个构建' : '查看这个构建'}
                </button>
              )}
            />

            <section className="rounded-ds-md border border-accent/25 bg-accent/5 p-4" aria-label={`将构建 #${result.build.id} 设为默认`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-label uppercase tracking-label text-text-3">第二步 · 显式设为默认</div>
                  <h3 className="mt-1 text-body font-semibold text-text-1">
                    将构建 #{result.build.id} 设为默认复盘构建
                  </h3>
                  <p className="mt-1 text-caption text-text-3">
                    目标指纹 <span className="font-mono">{result.build.buildKey.slice(0, 12)}…</span>
                    {' '}· 当前 {activationState?.currentBuildId != null ? `#${activationState.currentBuildId}` : '无默认构建'}
                  </p>
                </div>
                <span className="rounded-full border border-up-strong/25 bg-up-subtle px-2.5 py-1 text-caption text-up-strong">
                  零交易动作
                </span>
              </div>

              {targetRequiresAssumedFlat && !targetIsCurrent && (
                <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
                    checked={activationAssumptionAccepted}
                    onChange={(event) => setAcceptedActivationBuildId(
                      event.target.checked ? result.build.id : null,
                    )}
                  />
                  <span>
                    <strong className="text-text-1">设为默认时，我再次接受未验证的期初空仓假设。</strong>
                    {' '}当前时点的券商持仓快照不能倒推历史期初。这是独立于“生成构建”的第二次确认；受影响盈亏仍保持条件性标记。
                  </span>
                </label>
              )}

              {targetRequiresGroupFeeScope && !targetIsCurrent && (
                <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
                    checked={activationGroupFeeScopeAccepted}
                    onChange={(event) => setAcceptedGroupFeeActivationBuildId(
                      event.target.checked ? result.build.id : null,
                    )}
                  />
                  <span>
                    <strong className="text-text-1">设为默认时，我再次确认组合费用仅保留在执行组层。</strong>
                    {' '}受影响腿的费用与 Net 仍为空，并继续从严格 Headline 排除。
                  </span>
                </label>
              )}

              {activationError && (
                <ApiErrorAlert
                  className="mt-3"
                  error={activationError}
                  actionLabel={activationConflict ? '重新读取默认构建状态' : '刷新默认构建状态'}
                  onAction={() => void loadActivationState()}
                />
              )}

              {activationResult && (
                <InlineAlert
                  className="mt-3"
                  variant="success"
                  title={`构建 #${activationResult.state.currentBuildId} 已设为默认复盘构建`}
                  message={activationResult.duplicate
                    ? '该构建已经是默认复盘构建；默认读取已按服务器状态重新验证。'
                    : 'append-only 默认选择记录已保存；默认读取将重新验证此构建。'}
                />
              )}

              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!canActivate}
                  onClick={() => void handleActivate()}
                >
                  {activationSubmitting
                    ? '设置中…'
                    : targetIsCurrent
                      ? '已设为默认'
                      : '设为默认复盘构建'}
                </button>
                {activationLoading && (
                  <span className="text-caption text-text-3">正在读取默认构建状态，完成后才能安全设置。</span>
                )}
                {!activationLoading && !activationState && !activationError && (
                  <span className="text-caption text-down-strong">缺少默认构建状态，不能提交设置。</span>
                )}
                {!targetHasImmutableKey && (
                  <span className="text-caption text-down-strong">构建指纹无效，请重新生成。</span>
                )}
                {targetRequiresAssumedFlat && !activationAssumptionAccepted && !targetIsCurrent && (
                  <span className="text-caption text-warn-strong">再次确认边界假设后才能设为默认。</span>
                )}
                {targetRequiresGroupFeeScope && !activationGroupFeeScopeAccepted && !targetIsCurrent && (
                  <span className="text-caption text-warn-strong">再次确认组合费用口径后才能设为默认。</span>
                )}
              </div>
            </section>
          </div>
        )}

        {preview.requiresAssumedFlatAcceptance && (
          <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
              checked={assumptionAccepted}
              onChange={(event) => setAcceptedPreviewKey(event.target.checked ? previewKey : null)}
            />
            <span>
              <strong className="text-text-1">我接受未验证的期初空仓假设。</strong>
              {' '}左边界没有持仓快照证明；即使另有当前时点的券商持仓快照，也不能倒推这个历史证据窗口的期初持仓。受影响结果会保留条件性标记，不进入严格 Headline。
            </span>
          </label>
        )}


        {preview.requiresGroupFeeScopeAcceptance && (
          <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
              checked={groupFeeScopeAccepted}
              onChange={(event) => setAcceptedGroupFeePreviewKey(
                event.target.checked ? previewKey : null,
              )}
            />
            <span>
              <strong className="text-text-1">我理解组合费用只在执行组层精确保留。</strong>
              {' '}系统不会猜测腿级分摊；受影响腿不显示费用或 Net，也不进入严格 Headline。
            </span>
          </label>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-primary"
            disabled={!canBuild || building}
            onClick={() => void onBuild(
              assumptionAccepted,
              groupFeeScopeAccepted,
            ).catch(() => undefined)}
          >
            {building ? '生成中…' : '生成仓位复盘构建'}
          </button>
          {!preview.confirmAllowed && (
            <span className="text-caption text-down-strong">当前预览未通过安全检查，不能生成。</span>
          )}
          {preview.confirmAllowed && !preview.feeConserved && (
            <span className="text-caption text-down-strong">费用尚未守恒，不能生成。</span>
          )}
          {preview.defaultWillChange && (
            <span className="text-caption text-down-strong">此计划可能改变默认视图，已禁止生成。</span>
          )}
          {preview.requiresAssumedFlatAcceptance && !assumptionAccepted && (
            <span className="text-caption text-warn-strong">勾选边界假设后才能生成。</span>
          )}
          {preview.requiresGroupFeeScopeAcceptance && !groupFeeScopeAccepted && (
            <span className="text-caption text-warn-strong">确认组合费用口径后才能生成。</span>
          )}
        </div>
      </div>
    </section>
  );
}

export const PositionEpisodesPanel: React.FC<PositionEpisodesPanelProps> = ({
  filters,
  controller,
  onApplyFilters,
  onPageChange,
  onSelectBuild,
  onOpenReview,
  onImported,
}) => {
  const [draftSymbol, setDraftSymbol] = useState(filters.underlying ?? '');
  const [draftStatus, setDraftStatus] = useState(filters.lifecycleStatus ?? '');
  const [draftCompleteness, setDraftCompleteness] = useState(filters.completenessStatus ?? '');
  const [draftCaseFocus, setDraftCaseFocus] = useState(filters.caseFocus ?? '');
  const [draftReviewStatus, setDraftReviewStatus] = useState(filters.reviewStatus ?? '');
  const [selected, setSelected] = useState<PositionEpisodeItem | null>(null);
  const [confirmBuild, setConfirmBuild] = useState(false);

  const totalPages = Math.max(1, Math.ceil((controller.list?.total ?? 0) / (controller.list?.perPage ?? 50)));
  const selectedDetail = selected ? controller.detailById[selected.id] : undefined;
  const summary = controller.list?.summary;
  const build = controller.list?.build;
  const reconciliation = controller.list?.reconciliation;
  const headline = summary?.headlinePnl;
  const conditionalPnl = summary?.conditionalPnl;
  const reviewQueue = controller.list?.reviewQueue;
  const items = useMemo(() => controller.list?.items ?? [], [controller.list?.items]);

  const openEpisode = (item: PositionEpisodeItem) => {
    setSelected(item);
    if (!controller.detailById[item.id]) void controller.loadDetail(item.id);
  };

  const apply = () => {
    onApplyFilters({
      underlying: draftSymbol.trim().toUpperCase() || undefined,
      lifecycleStatus: draftStatus as PositionEpisodeFilters['lifecycleStatus'],
      completenessStatus: draftCompleteness as PositionEpisodeFilters['completenessStatus'],
      caseFocus: draftCaseFocus as PositionEpisodeFilters['caseFocus'],
      reviewStatus: draftReviewStatus as PositionEpisodeFilters['reviewStatus'],
      page: 1,
      perPage: filters.perPage ?? 50,
    });
  };

  const clearFilters = () => {
    setDraftSymbol('');
    setDraftStatus('');
    setDraftCompleteness('');
    setDraftCaseFocus('');
    setDraftReviewStatus('');
    onApplyFilters({ page: 1, perPage: filters.perPage ?? 50 });
  };

  const canonicalBuildCard = (
    <CanonicalEpisodeBuildCard
      preview={controller.canonicalPreview}
      loading={controller.canonicalPreviewLoading}
      error={controller.canonicalPreviewError}
      building={controller.canonicalBuilding}
      result={controller.canonicalBuildResult}
      viewedBuildId={filters.buildId}
      onRetry={controller.reloadCanonicalPreview}
      onBuild={controller.buildCanonical}
      onViewBuild={(buildId) => onSelectBuild(buildId)}
      onActivated={() => {
        onSelectBuild();
        controller.reload();
        controller.reloadCanonicalPreview();
        onImported?.();
      }}
    />
  );

  if (controller.loading && !controller.list) {
    return (
      <div className="space-y-4">
        {canonicalBuildCard}
        <div className="card-base p-8 text-center text-body-sm text-text-3">加载仓位回合…</div>
      </div>
    );
  }

  if (controller.list?.dataState === 'not_built') {
    return (
      <div className="space-y-4">
        {canonicalBuildCard}
        {controller.error && <ApiErrorAlert error={controller.error} actionLabel="重试" onAction={controller.reload} />}
        <div className="card-base p-6">
          <div className="max-w-2xl">
            <div className="text-label uppercase tracking-label text-text-3">仓位生命周期</div>
            <h2 className="mt-2 text-h1 text-text-1">证据已存在，尚未构建仓位回合</h2>
            <p className="mt-2 text-body-sm leading-relaxed text-text-3">
              构建会按合约的有符号持仓归零点划分回合。当前没有经过验证的历史期初持仓快照，必须明确接受“证据窗口开始时持仓为 0”的条件性假设；当前时点的持仓快照也不能倒推这个历史期初。
            </p>
            <button type="button" className="btn-primary mt-4" onClick={() => setConfirmBuild(true)} disabled={controller.building}>
              {controller.building ? '构建中…' : '按未验证期初空仓构建'}
            </button>
          </div>
        </div>
        <ConfirmDialog
          isOpen={confirmBuild}
          title="确认未验证的期初空仓假设"
          message="当前证据没有经过验证的历史期初持仓快照。继续会把证据窗口开始时的持仓假设为 0；即使另有当前时点的券商持仓快照，也不能倒推这个历史期初。由此产生的盈亏属于条件性结果，不会进入页面 Headline。确认接受这个未验证假设并构建吗？"
          confirmText="接受假设并构建"
          onCancel={() => setConfirmBuild(false)}
          onConfirm={() => {
            setConfirmBuild(false);
            void controller.buildAssumedFlat().catch(() => undefined);
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {canonicalBuildCard}
      {filters.buildId != null && (
        <InlineAlert
          variant="info"
          title={`正在查看构建 #${filters.buildId}`}
          message="这是显式打开的对比视图；默认复盘构建没有改变。"
          action={<button type="button" className="btn-ghost" onClick={() => onSelectBuild()}>回到默认复盘构建</button>}
        />
      )}
      {controller.error && <ApiErrorAlert error={controller.error} actionLabel="重试" onAction={controller.reload} />}
      {controller.buildResult && (
        <InlineAlert variant="success" title="仓位回合已就绪" message={controller.buildResult.message} />
      )}
      {reconciliation?.partialWindow && (
        <InlineAlert
          variant="warning"
          title="API 对账仅覆盖部分窗口"
          message={(
            <span>
              已逐单核对 <strong>{reconciliation.matchedOrderCount.toLocaleString()} / {reconciliation.totalOrderCount.toLocaleString()}</strong> 笔订单
              （ET：{formatEt(reconciliation.windowStart, true)} – {formatEt(reconciliation.windowEnd, true)}）；窗口外证据尚未经过 API 逐单核对，<strong>这不代表整批通过</strong>。
            </span>
          )}
        />
      )}
      {build?.assumedFlatUnverified && (
        <InlineAlert
          variant="warning"
          title="回合基于未验证的期初空仓假设"
          message="历史左边界没有持仓快照证明；即使另有当前时点的券商持仓快照，也不能倒推这个历史证据窗口的期初持仓。所有受影响回合均标记为条件性，相关盈亏不会计入 Headline。"
        />
      )}
      {(summary?.groupFeeAffectedEpisodeCount ?? 0) > 0 && (
        <InlineAlert
          variant="warning"
          title={`${summary?.groupFeeAffectedEpisodeCount.toLocaleString()} 个回合含组合组级费用`}
          message={(
            <span>
              组合成交已按真实腿进入仓位回合；整组费用
              <strong> {formatDecimalDisplay(build?.retainedExecutionGroupFeeTotal, true)} </strong>
              精确保留，但没有按腿猜测分摊。相关腿的 Net 为空，并从严格 Headline 排除。
            </span>
          )}
        />
      )}

      {build ? (
        <section className="rounded-ds-md border border-accent/20 bg-accent/5 p-4" aria-label="复盘口径">
          <div className="text-label uppercase tracking-label text-accent">Historical reconstruction · Not live positions</div>
          <h2 className="mt-1 text-h2 text-text-1">证据窗口与窗口末投影</h2>
          <dl className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-caption text-text-3">证据窗口（ET）起—止</dt>
              <dd className="mt-1 font-mono text-mono-xs text-text-1">
                {formatEt(build.sourceWindowStart, true)} — {formatEt(build.sourceCutoffAt, true)}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-text-3">窗口末投影 as-of（ET）</dt>
              <dd className="mt-1 font-mono text-mono-xs text-text-1">{formatEt(build.sourceCutoffAt, true)}</dd>
            </div>
          </dl>
          <p className="mt-3 text-caption leading-relaxed text-text-3">
            “证据窗口末数量”只表示导入证据回放到上述 as-of 时点后的投影。系统没有该时点之后的完整成交与持仓快照，因此它不等于券商当前持仓。
          </p>
        </section>
      ) : (
        <InlineAlert
          variant="info"
          title="证据窗口末数量不是实时持仓"
          message="证据窗口元数据暂不可用；列表数量仍只代表历史证据回放结果，不等于券商当前持仓。"
        />
      )}

      {conditionalPnl && (
        <section className="rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-4" aria-label="按期初空仓假设的条件性结果">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-label uppercase tracking-label text-warn-strong">Conditional · Not Headline</div>
              <h2 className="mt-1 text-h2 text-text-1">按期初空仓假设的条件性结果</h2>
              <p className="mt-1 text-caption text-text-3">
                后端按 {conditionalPnl.openingBoundaryPolicy} 口径持久化；未计入 Verified Net，浏览器未重新汇总。
              </p>
            </div>
            <span className="rounded-full border border-warn-strong/30 px-2 py-1 text-caption text-warn-strong">不进入 Headline</span>
          </div>
          <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div><dt className="text-caption text-text-3">已平回合</dt><dd className="mt-1 font-mono text-mono-md text-text-1">{conditionalPnl.count.toLocaleString()}</dd></div>
            <div><dt className="text-caption text-text-3">Gross</dt><dd className={`mt-1 font-mono text-mono-md ${decimalTone(conditionalPnl.realizedPnlGross)}`}>{formatDecimalDisplay(conditionalPnl.realizedPnlGross, true)}</dd></div>
            <div><dt className="text-caption text-text-3">Fees</dt><dd className="mt-1 font-mono text-mono-md text-text-1">{formatDecimalDisplay(conditionalPnl.totalFee, true)}</dd></div>
            <div><dt className="text-caption text-text-3">Conditional Net</dt><dd className={`mt-1 font-mono text-mono-md ${decimalTone(conditionalPnl.realizedPnlNet)}`}>{formatDecimalDisplay(conditionalPnl.realizedPnlNet, true)}</dd></div>
          </dl>
        </section>
      )}

      {summary && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['仓位回合', summary.totalEpisodeCount.toLocaleString(), '当前构建的回合数'],
            ['证据窗口末未归零', summary.openEpisodeCount.toLocaleString(), '证据窗口末数量不等于券商当前持仓；Net 显示 —'],
            ['Headline eligible', (headline?.eligibleClosedCount ?? 0).toLocaleString(), '仅边界已验证且证据完整的已平回合'],
            ['Verified Net', formatDecimalDisplay(headline?.realizedPnlNet, true), `${headline?.excludedEpisodeCount ?? 0} 个条件性/不完整回合已排除`],
          ].map(([label, value, caption]) => (
            <div key={label} className="rounded-ds-md border border-subtle bg-bg-1 p-4">
              <div className="text-label uppercase tracking-label text-text-3">{label}</div>
              <div className={`mt-1 font-mono text-mono-lg tabular-nums ${label === 'Verified Net' ? decimalTone(headline?.realizedPnlNet) : 'text-text-1'}`}>{value}</div>
              <div className="mt-1 text-caption text-text-3">{caption}</div>
            </div>
          ))}
        </div>
      )}

      {reviewQueue && (
        <section className="rounded-ds-md border border-subtle bg-bg-1 p-4" aria-label="复盘队列">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-label uppercase tracking-label text-text-3">Review queue</div>
              <h2 className="mt-1 text-h2 text-text-1">复盘进度</h2>
            </div>
            <span className="text-caption text-text-3">共 {reviewQueue.total.toLocaleString()} 个回合</span>
          </div>
          <dl className="mt-3 grid grid-cols-3 gap-2">
            {[
              ['未开始', reviewQueue.pending, 'text-text-1'],
              ['进行中', reviewQueue.inProgress, 'text-accent'],
              ['已完成', reviewQueue.completed, 'text-up-strong'],
            ].map(([label, value, tone]) => (
              <div key={label as string} className="rounded-ds-sm border border-subtle bg-bg-2 p-3">
                <dt className="text-caption text-text-3">{label}</dt>
                <dd className={`mt-1 font-mono text-mono-md ${tone}`}>{Number(value).toLocaleString()}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <div className="card-base p-4">
        <div className="flex flex-wrap gap-2">
          <input
            className="input-base min-w-44"
            aria-label="筛选标的"
            placeholder="标的，例如 NVDA"
            value={draftSymbol}
            onChange={(event) => setDraftSymbol(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') apply();
            }}
          />
          <select className="input-base" aria-label="筛选回合状态" value={draftStatus} onChange={(event) => setDraftStatus(event.target.value as typeof draftStatus)}>
            <option value="">状态：全部</option>
            <option value="open">证据窗口末未归零</option>
            <option value="closed">证据窗口内已归零</option>
          </select>
          <select className="input-base" aria-label="筛选证据完整度" value={draftCompleteness} onChange={(event) => setDraftCompleteness(event.target.value as typeof draftCompleteness)}>
            <option value="">完整度：全部</option>
            <option value="exact">Exact</option>
            <option value="complete">Complete</option>
            <option value="partial">Partial</option>
          </select>
          <select className="input-base" aria-label="案例精选" value={draftCaseFocus} onChange={(event) => setDraftCaseFocus(event.target.value as typeof draftCaseFocus)}>
            <option value="">案例精选：最近</option>
            <option value="top_profit">盈利最多</option>
            <option value="top_loss">亏损最多</option>
            <option value="largest_fee">费用最高</option>
            <option value="longest_hold">持有最长</option>
            <option value="weakest_evidence">证据最不完整</option>
          </select>
          <select className="input-base" aria-label="筛选复盘状态" value={draftReviewStatus} onChange={(event) => setDraftReviewStatus(event.target.value as typeof draftReviewStatus)}>
            <option value="">复盘：全部</option>
            <option value="not_started">未开始</option>
            <option value="in_progress">进行中</option>
            <option value="completed">已完成</option>
          </select>
          <button type="button" className="btn-primary" onClick={apply}>应用筛选</button>
          {(filters.underlying || filters.lifecycleStatus || filters.completenessStatus || filters.caseFocus || filters.reviewStatus) && (
            <button type="button" className="btn-ghost" onClick={clearFilters}>清除筛选</button>
          )}
        </div>
        <p className="mt-2 text-caption text-text-3">
          案例精选只改变后端排序，不改变证据口径；任何 Net 都可能仍是条件性结果，请以每行“条件值 · 不进 Headline”标记为准。
        </p>
        {filters.caseFocus && (
          <div className="mt-3 rounded-ds-md border border-warn-strong/25 bg-warn-subtle px-3 py-2 text-caption text-warn-strong" role="status">
            当前为“{CASE_FOCUS_LABELS[filters.caseFocus] ?? filters.caseFocus}”案例精选。排序使用后端持久化字段；列表中的 Net 仍可能是条件性数据，不代表已经通过边界验证。
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-caption text-text-3">
          <span>仅展示后端持久化的 Decimal 字符串；浏览器不重新汇总会计结果。</span>
          {build && <span>构建 #{build.id} · {build.builderName} {build.builderVersion}</span>}
        </div>
      </div>

      <div className="card-base overflow-hidden">
        {controller.loading && <div className="border-b border-subtle px-4 py-2 text-caption text-text-3">更新列表中…</div>}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] text-body-sm">
            <thead className="bg-bg-2 text-label uppercase tracking-label text-text-3">
              <tr>
                <th className="px-3 py-2 text-left">合约 / 标的</th>
                <th className="px-3 py-2 text-left">状态</th>
                <th className="px-3 py-2 text-left">复盘</th>
                <th className="px-3 py-2 text-left">方向</th>
                <th className="px-3 py-2 text-left">开仓时间 ET</th>
                <th className="px-3 py-2 text-right">开仓</th>
                <th className="px-3 py-2 text-right">已平</th>
                <th className="px-3 py-2 text-right">证据窗口末数量</th>
                <th className="px-3 py-2 text-right">Net</th>
                <th className="px-3 py-2 text-left">证据</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const displayedPnl = item.lifecycleStatus === 'open' ? null : item.realizedPnlNet;
                return (
                  <tr
                    key={item.id}
                    tabIndex={0}
                    onClick={() => openEpisode(item)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') openEpisode(item);
                    }}
                    className="cursor-pointer border-t border-subtle transition-colors hover:bg-bg-2 focus:bg-bg-2 focus:outline-none"
                  >
                    <td className="px-3 py-3">
                      <div className="font-mono text-mono-sm text-text-1">{item.instrument.rawSymbol}</div>
                      <div className="mt-0.5 text-caption text-text-3">{item.instrument.underlying} · {item.strategyType}</div>
                    </td>
                    <td className="px-3 py-3"><span className="rounded-full border border-subtle bg-bg-2 px-2 py-1 text-caption text-text-2">{lifecycleLabel(item.lifecycleStatus)}</span></td>
                    <td className="px-3 py-3">
                      <span className={`rounded-full border px-2 py-1 text-caption ${
                        item.reviewStatus === 'completed'
                          ? 'border-up-strong/25 bg-up-subtle text-up-strong'
                          : item.reviewStatus === 'in_progress'
                            ? 'border-accent-subtle-border bg-accent-subtle-bg text-accent'
                            : 'border-subtle bg-bg-2 text-text-3'
                      }`}>
                        {reviewStatusLabel(item.reviewStatus)}
                        {item.reviewRevision != null ? ` · #${item.reviewRevision}` : ''}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-text-2">{item.direction}</td>
                    <td className="px-3 py-3 font-mono text-mono-xs text-text-2">{formatEt(item.openedAt)}</td>
                    <td className="px-3 py-3 text-right font-mono text-mono-sm text-text-2">{formatDecimalDisplay(item.openedQuantity)}</td>
                    <td className="px-3 py-3 text-right font-mono text-mono-sm text-text-2">{formatDecimalDisplay(item.closedQuantity)}</td>
                    <td className="px-3 py-3 text-right font-mono text-mono-sm text-text-1">{formatDecimalDisplay(item.remainingQuantity)}</td>
                    <td className={`px-3 py-3 text-right font-mono text-mono-sm ${decimalTone(displayedPnl)}`}>
                      {item.lifecycleStatus === 'open' ? '—' : formatDecimalDisplay(displayedPnl, true)}
                      {!item.quality.pnlSummaryEligible && item.lifecycleStatus === 'closed' && <div className="text-[10px] text-warn-strong">条件值 · 不进 Headline</div>}
                    </td>
                    <td className="px-3 py-3">
                      <span className={item.quality.assumedFlatUnverified || item.quality.groupFeeUnallocated || item.quality.completenessStatus === 'partial' ? 'text-warn-strong' : 'text-text-2'}>{qualityLabel(item)}</span>
                      {item.quality.groupFeeUnallocated && (
                        <div className="mt-1 text-[10px] text-warn-strong">组合费仅组级 · Net 留空</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!items.length && !controller.loading && (
          <div className="p-8 text-center text-body-sm text-text-3">没有符合当前筛选条件的仓位回合。</div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-caption text-text-3">共 {controller.list?.total.toLocaleString() ?? 0} 个回合</span>
        <Pagination currentPage={controller.list?.page ?? 1} totalPages={totalPages} onPageChange={onPageChange} />
      </div>

      <EpisodeDrawer
        item={selected}
        detail={selectedDetail}
        loading={controller.detailLoadingId === selected?.id}
        error={controller.detailError}
        onOpenFullReview={onOpenReview}
        onClose={() => setSelected(null)}
      />
    </div>
  );
};

export default PositionEpisodesPanel;

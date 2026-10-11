import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { ApiErrorAlert } from '../common/ApiErrorAlert';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { InlineAlert } from '../common/InlineAlert';
import { parseApiError, type ParsedApiError } from '../../api/error';
import {
  activateEpisodeBuild,
  fetchEpisodeBuildActivation,
} from '../../api/journal';
import {
  formatCurrencyAmount,
  formatDecimalDisplay,
  formatEt,
} from './episodeFormat';
import type { PositionEpisodeController } from './PositionEpisodesPanel';
import type {
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildActivationResponse,
  EpisodeBuildActivationState,
  EpisodeBuildMetadata,
  EpisodeBuildResponse,
  PositionEpisodeFilters,
  PositionEpisodeSummary,
} from '../../types/journal';

const CANONICAL_SOURCE_KIND = 'canonical_set';
const SNAPSHOT_FENCE_SOURCE_KIND = 'position_snapshot_fenced_canonical';
const ACTIVATION_IRREVERSIBLE_WARNING = '激活后默认复盘视图将切换到此构建；之后可以再激活其他构建切回，但无法回到「零激活」的 CSV 默认状态。';

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
  const [acceptedLeftCensoredActivationBuildId, setAcceptedLeftCensoredActivationBuildId] = useState<number | null>(null);
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
    setAcceptedLeftCensoredActivationBuildId(null);
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
        <p className="mt-1 text-body-sm text-text-3">先在上方“每日刷新 / 历史导入”完成事实集确认，再回到这里生成可追溯的仓位复盘。</p>
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
  const targetRequiresLeftCensored = Boolean(
    result && result.summary.leftCensoredEpisodeCount > 0,
  );
  const activationAssumptionAccepted = (
    result != null && acceptedActivationBuildId === result.build.id
  );
  const activationGroupFeeScopeAccepted = (
    result != null && acceptedGroupFeeActivationBuildId === result.build.id
  );
  const activationLeftCensoredAccepted = (
    result != null && acceptedLeftCensoredActivationBuildId === result.build.id
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
    && (!targetRequiresGroupFeeScope || activationGroupFeeScopeAccepted)
    && (!targetRequiresLeftCensored || activationLeftCensoredAccepted),
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
        acceptLeftCensoredOpenings: (
          targetRequiresLeftCensored && activationLeftCensoredAccepted
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
      setAcceptedLeftCensoredActivationBuildId(null);
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
        setAcceptedLeftCensoredActivationBuildId(null);
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

              {targetRequiresLeftCensored && !targetIsCurrent && (
                <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
                    checked={activationLeftCensoredAccepted}
                    onChange={(event) => setAcceptedLeftCensoredActivationBuildId(
                      event.target.checked ? result.build.id : null,
                    )}
                  />
                  <span>
                    <strong className="text-text-1">设为默认时，我接受 left-censored 回合无券商成本。</strong>
                    {' '}{result.summary.leftCensoredEpisodeCount.toLocaleString()} 个回合缺少边界前的真实开仓成本与费用；系统不会伪造这些数字，相关结果不进入严格 Headline。
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

              {!targetIsCurrent && (
                <p className="mt-3 text-caption text-warn-strong">
                  {ACTIVATION_IRREVERSIBLE_WARNING}
                </p>
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
                {targetRequiresLeftCensored && !activationLeftCensoredAccepted && !targetIsCurrent && (
                  <span className="text-caption text-warn-strong">确认 left-censored 回合口径后才能设为默认。</span>
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

function viewedBuildSourceLabel(build: EpisodeBuildMetadata): string {
  if (build.sourceKind === SNAPSHOT_FENCE_SOURCE_KIND) {
    return `快照围栏 future build · 目标事实集 #${build.canonicalSetId ?? '—'}`;
  }
  return `可信事实集构建 · 事实集 #${build.canonicalSetId ?? '—'}`;
}

function ViewedBuildActivationCard({
  build,
  summary,
  onActivated,
}: {
  build: EpisodeBuildMetadata;
  summary: PositionEpisodeSummary | null;
  onActivated: (response: EpisodeBuildActivationResponse) => void;
}) {
  const [activationState, setActivationState] = useState<EpisodeBuildActivationState | null>(null);
  const [activationLoading, setActivationLoading] = useState(true);
  const [activationSubmitting, setActivationSubmitting] = useState(false);
  const [activationError, setActivationError] = useState<ParsedApiError | null>(null);
  const [activationConflict, setActivationConflict] = useState(false);
  const [acceptedAssumedFlat, setAcceptedAssumedFlat] = useState(false);
  const [acceptedGroupFeeScope, setAcceptedGroupFeeScope] = useState(false);
  const [acceptedLeftCensored, setAcceptedLeftCensored] = useState(false);

  const loadActivationState = useCallback(async () => {
    setActivationLoading(true);
    setActivationError(null);
    setActivationConflict(false);
    try {
      const state = await fetchEpisodeBuildActivation();
      setActivationState(state);
    } catch (reason) {
      setActivationError(parseApiError(reason));
    } finally {
      setActivationLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadActivationState();
  }, [loadActivationState]);

  const isFenceBuild = build.sourceKind === SNAPSHOT_FENCE_SOURCE_KIND;
  const requiresAssumedFlat = build.assumedFlatUnverified;
  const requiresGroupFeeScope = (
    build.groupFeeAffectedEpisodeCount > 0 && !build.legFeeAttributionComplete
  );
  const leftCensoredCount = summary?.leftCensoredEpisodeCount ?? 0;
  const requiresLeftCensored = leftCensoredCount > 0;
  const targetIsCurrent = (
    activationState?.selectionSource === 'activation'
    && activationState.currentBuildId === build.id
  );
  const hasImmutableKey = /^[0-9a-f]{64}$/i.test(build.buildKey);
  const canActivate = Boolean(
    activationState
    && hasImmutableKey
    && !targetIsCurrent
    && !activationLoading
    && !activationSubmitting
    && !activationError
    && (!requiresAssumedFlat || acceptedAssumedFlat)
    && (!requiresGroupFeeScope || acceptedGroupFeeScope)
    && (!requiresLeftCensored || acceptedLeftCensored),
  );

  const handleActivate = async () => {
    if (!activationState || !canActivate) return;
    setActivationSubmitting(true);
    setActivationError(null);
    setActivationConflict(false);
    try {
      const response = await activateEpisodeBuild(build.id, {
        expectedBuildKey: build.buildKey,
        expectedCurrentActivationId: activationState.currentActivationId ?? null,
        expectedCurrentBuildId: activationState.currentBuildId ?? null,
        acceptAssumedFlat: requiresAssumedFlat && acceptedAssumedFlat,
        acceptGroupFeeScope: requiresGroupFeeScope && acceptedGroupFeeScope,
        acceptLeftCensoredOpenings: requiresLeftCensored && acceptedLeftCensored,
      });
      if (
        response.tradingActionPerformed !== false
        || response.state.currentBuildId !== build.id
        || response.state.selectionSource !== 'activation'
      ) {
        throw new Error('默认设置结果未通过只读复盘校验，页面没有切换默认复盘构建。');
      }
      setActivationState(response.state);
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
          // Keep the actionable CAS conflict; retry can reload state again.
        }
        setAcceptedAssumedFlat(false);
        setAcceptedGroupFeeScope(false);
        setAcceptedLeftCensored(false);
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
    <section
      className="rounded-ds-md border border-accent/25 bg-accent/5 p-4"
      aria-label={`将当前查看的构建 #${build.id} 设为默认`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">显式设为默认复盘构建</div>
          <h3 className="mt-1 text-body font-semibold text-text-1">
            将构建 #{build.id} 设为默认复盘构建
          </h3>
          <p className="mt-1 text-caption text-text-3">
            {viewedBuildSourceLabel(build)}
            {' '}· 目标指纹 <span className="font-mono">{build.buildKey.slice(0, 12)}…</span>
            {' '}· 当前 {activationState?.currentBuildId != null ? `#${activationState.currentBuildId}` : '无默认构建'}
          </p>
        </div>
        <span className="rounded-full border border-up-strong/25 bg-up-subtle px-2.5 py-1 text-caption text-up-strong">
          零交易动作
        </span>
      </div>

      {isFenceBuild && (
        <p className="mt-2 text-caption text-text-3">
          这是快照围栏 future build：回合仅覆盖快照边界之后的执行窗口，激活身份绑定其冻结的目标事实集。
        </p>
      )}

      {requiresAssumedFlat && !targetIsCurrent && (
        <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
            checked={acceptedAssumedFlat}
            onChange={(event) => setAcceptedAssumedFlat(event.target.checked)}
          />
          <span>
            <strong className="text-text-1">设为默认时，我再次接受未验证的期初空仓假设。</strong>
            {' '}受影响盈亏仍保持条件性标记，不进入严格 Headline。
          </span>
        </label>
      )}

      {requiresGroupFeeScope && !targetIsCurrent && (
        <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
            checked={acceptedGroupFeeScope}
            onChange={(event) => setAcceptedGroupFeeScope(event.target.checked)}
          />
          <span>
            <strong className="text-text-1">设为默认时，我再次确认组合费用仅保留在执行组层。</strong>
            {' '}受影响腿的费用与 Net 仍为空，并继续从严格 Headline 排除。
          </span>
        </label>
      )}

      {requiresLeftCensored && !targetIsCurrent && (
        <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
            checked={acceptedLeftCensored}
            onChange={(event) => setAcceptedLeftCensored(event.target.checked)}
          />
          <span>
            <strong className="text-text-1">设为默认时，我接受 {leftCensoredCount.toLocaleString()} 个 left-censored 回合无券商成本。</strong>
            {' '}snapshot 继承仓位缺少边界前的真实开仓成本与费用；系统不会伪造这些数字，相关结果不进入严格 Headline。
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

      {!targetIsCurrent && (
        <p className="mt-3 text-caption text-warn-strong">
          {ACTIVATION_IRREVERSIBLE_WARNING}
        </p>
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
        {!hasImmutableKey && (
          <span className="text-caption text-down-strong">构建指纹无效，不能提交设置。</span>
        )}
        {requiresLeftCensored && !acceptedLeftCensored && !targetIsCurrent && (
          <span className="text-caption text-warn-strong">确认 left-censored 回合口径后才能设为默认。</span>
        )}
      </div>
    </section>
  );
}

interface EpisodeBuildManagementPanelProps {
  filters: PositionEpisodeFilters;
  controller: PositionEpisodeController;
  onSelectBuild: (buildId?: number) => void;
  onImported?: () => void;
}

/**
 * 「构建与默认视图管理」：canonical 构建预览/生成、显式激活（设为默认）与
 * 首次 assumed-flat 构建。复盘工作台（仓位复盘 tab）不再承载这些数据管线；
 * 该面板挂载在「数据与构建」tab。
 */
export const EpisodeBuildManagementPanel: React.FC<EpisodeBuildManagementPanelProps> = ({
  filters,
  controller,
  onSelectBuild,
  onImported,
}) => {
  const [confirmBuild, setConfirmBuild] = useState(false);

  const summary = controller.list?.summary ?? null;
  const build = controller.list?.build ?? null;
  const notBuilt = controller.list?.dataState === 'not_built';

  const handleActivated = () => {
    onSelectBuild();
    controller.reload();
    controller.reloadCanonicalPreview();
    onImported?.();
  };

  return (
    <div className="space-y-4">
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
        onActivated={handleActivated}
      />

      {filters.buildId != null
        && build != null
        && build.id === filters.buildId
        && (build.sourceKind === CANONICAL_SOURCE_KIND
          || build.sourceKind === SNAPSHOT_FENCE_SOURCE_KIND)
        && controller.canonicalBuildResult?.build.id !== build.id && (
        <ViewedBuildActivationCard
          key={build.id}
          build={build}
          summary={summary}
          onActivated={handleActivated}
        />
      )}

      {controller.buildResult && (
        <InlineAlert variant="success" title="仓位回合已就绪" message={controller.buildResult.message} />
      )}

      {notBuilt && (
        <>
          {controller.error && (
            <ApiErrorAlert error={controller.error} actionLabel="重试" onAction={controller.reload} />
          )}
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
        </>
      )}
    </div>
  );
};

export default EpisodeBuildManagementPanel;

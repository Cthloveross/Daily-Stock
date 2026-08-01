import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import {
  confirmFuturePositionEpisodeBuild,
  fetchFuturePositionEpisodePreview,
} from '../../api/journalPositions';
import { parseApiError, type ParsedApiError } from '../../api/error';
import type {
  ConfirmedPositionSnapshot,
  FuturePositionEpisodeBuildConfirmResponse,
  FuturePositionEpisodePreviewResponse,
  PositionSnapshotContinuityAssessment,
} from '../../types/journalPositions';
import { ApiErrorAlert } from '../common/ApiErrorAlert';
import { InlineAlert } from '../common/InlineAlert';

interface FutureEpisodePreviewSummaryProps {
  assessment: PositionSnapshotContinuityAssessment | null;
  latestSnapshot: Pick<ConfirmedPositionSnapshot, 'id' | 'key'> | null;
  identityBlocked: boolean;
  onRetry: () => void;
}

class PreviewIdentityMismatchError extends Error {}

type FutureBuildConfirmPhase =
  | { requestKey: string; status: 'in_flight' }
  | {
    requestKey: string;
    status: 'success';
    response: FuturePositionEpisodeBuildConfirmResponse;
  }
  | { requestKey: string; status: 'conflict'; error: ParsedApiError }
  | { requestKey: string; status: 'failed'; error: ParsedApiError };

function compactHash(value: string): string {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function decimalDisplay(value: string): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 10 }).format(parsed);
}

function formatEt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
}

function warningLabel(value: string): string {
  const labels: Record<string, string> = {
    opening_positions_remain_left_censored: '边界前已存在的仓位仍是左删失回合。',
    execution_group_fee_retained_unallocated: '组合费用只在执行组层精确保留，不猜测腿级分摊。',
    non_option_evidence_excluded_by_scope: '非美股期权事件已按本次范围排除。',
    no_post_boundary_execution_events: '边界后没有执行事件；计划仅延续已确认的边界仓位。',
  };
  return labels[value] ?? value;
}

function identityMatches(
  preview: FuturePositionEpisodePreviewResponse,
  assessment: PositionSnapshotContinuityAssessment,
): boolean {
  return preview.accountKey === assessment.accountKey
    && preview.fenceKey === assessment.fenceKey
    && preview.snapshotId === assessment.snapshotId
    && preview.snapshotKey === assessment.snapshotKey
    && preview.targetPublicationId === assessment.targetPublicationId
    && preview.targetPublicationKey === assessment.targetPublicationKey
    && preview.targetCanonicalSetId === assessment.targetCanonicalSetId
    && preview.targetCanonicalSetSha256 === assessment.targetCanonicalSetSha256
    && preview.boundaryAt === assessment.boundaryAt;
}

const FutureEpisodePreviewSummary: React.FC<FutureEpisodePreviewSummaryProps> = ({
  assessment,
  latestSnapshot,
  identityBlocked,
  onRetry,
}) => {
  const [loadState, setLoadState] = useState<{
    requestKey: string;
    preview: FuturePositionEpisodePreviewResponse | null;
    error: ParsedApiError | null;
  } | null>(null);
  const [confirmPhase, setConfirmPhase] = useState<FutureBuildConfirmPhase | null>(null);
  const [acceptLeftCensored, setAcceptLeftCensored] = useState(false);
  const [acceptGroupFee, setAcceptGroupFee] = useState(false);
  const requestGeneration = useRef(0);
  const autoRetriedRequestKey = useRef<string | null>(null);
  const onRetryRef = useRef(onRetry);

  useEffect(() => {
    onRetryRef.current = onRetry;
  }, [onRetry]);

  const eligible = Boolean(
    assessment?.status === 'ready'
    && assessment.readyForEpisodeBuild
    && assessment.fenceKey
    && latestSnapshot
    && latestSnapshot.id === assessment.snapshotId
    && latestSnapshot.key === assessment.snapshotKey
    && !identityBlocked,
  );
  const requestKey = eligible && assessment
    ? [
        assessment.fenceKey,
        assessment.snapshotId,
        assessment.snapshotKey,
        assessment.targetPublicationId,
        assessment.targetPublicationKey,
        assessment.targetCanonicalSetId,
        assessment.targetCanonicalSetSha256,
        assessment.boundaryAt,
      ].join(':')
    : null;

  useEffect(() => {
    const generation = ++requestGeneration.current;
    if (!requestKey || !assessment?.fenceKey) {
      return undefined;
    }
    void fetchFuturePositionEpisodePreview(assessment.fenceKey)
      .then((response) => {
        if (generation !== requestGeneration.current) return;
        if (!identityMatches(response, assessment)) {
          throw new PreviewIdentityMismatchError('未来回合预览的冻结身份已变化，页面已停止展示该结果。');
        }
        autoRetriedRequestKey.current = null;
        setLoadState({ requestKey, preview: response, error: null });
        // A fresh zero-write preview resets the explicit acceptances and
        // clears any stale conflict; a matching success note may stay.
        setAcceptLeftCensored(false);
        setAcceptGroupFee(false);
        setConfirmPhase((phase) => (
          phase?.status === 'success'
          && phase.requestKey === requestKey
          && phase.response.build.buildKey === response.plannedBuildKey
            ? phase
            : null
        ));
      })
      .catch((reason) => {
        if (generation !== requestGeneration.current) return;
        const parsed = parseApiError(reason);
        setLoadState({ requestKey, preview: null, error: parsed });
        if (
          (reason instanceof PreviewIdentityMismatchError || parsed.status === 409)
          && autoRetriedRequestKey.current !== requestKey
        ) {
          autoRetriedRequestKey.current = requestKey;
          onRetryRef.current();
        }
      });
    return () => {
      requestGeneration.current += 1;
    };
  }, [assessment, requestKey]);

  if (!eligible) return null;
  const currentState = loadState?.requestKey === requestKey ? loadState : null;
  const preview = currentState?.preview ?? null;
  const error = currentState?.error ?? null;
  const loading = currentState == null;

  if (loading && !preview) {
    return (
      <section className="rounded-ds-md border border-subtle bg-bg-1 p-4" aria-label="未来回合预览">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-body font-semibold text-text-1">未来回合预览</h3>
          <span className="rounded-full border border-subtle bg-bg-2 px-2.5 py-1 text-caption text-text-2">只读 · 尚未构建</span>
        </div>
        <p className="mt-2 text-caption text-text-3">正在用已核验的快照边界与 Canonical 事实计算内存预览…</p>
      </section>
    );
  }

  if (error && !preview) {
    return (
      <section className="rounded-ds-md border border-subtle bg-bg-1 p-4" aria-label="未来回合预览">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-body font-semibold text-text-1">未来回合预览</h3>
          <span className="rounded-full border border-down-strong/30 bg-down-subtle px-2.5 py-1 text-caption text-down-strong">只读预览已阻断</span>
        </div>
        <ApiErrorAlert className="mt-3" error={error} actionLabel="重新核对状态" onAction={onRetry} />
      </section>
    );
  }

  if (!preview) return null;
  const counts = preview.counts;
  const confirmCurrent = confirmPhase?.requestKey === requestKey ? confirmPhase : null;
  const confirming = confirmCurrent?.status === 'in_flight';
  const confirmedBuild = confirmCurrent?.status === 'success' ? confirmCurrent.response : null;
  const requiresLeftCensoredAcceptance = counts.leftCensoredEpisodeCount > 0;
  const requiresGroupFeeAcceptance = counts.groupFeeAffectedEpisodeCount > 0;
  const acceptancesSatisfied = (!requiresLeftCensoredAcceptance || acceptLeftCensored)
    && (!requiresGroupFeeAcceptance || acceptGroupFee);
  const canConfirmBuild = acceptancesSatisfied && !confirming && !confirmedBuild;

  const handleConfirmBuild = async () => {
    if (!requestKey || confirming || confirmedBuild || !acceptancesSatisfied) return;
    setConfirmPhase({ requestKey, status: 'in_flight' });
    try {
      const response = await confirmFuturePositionEpisodeBuild({
        expectedFenceKey: preview.fenceKey,
        expectedBuildKey: preview.plannedBuildKey,
        expectedEvidenceSetSha256: preview.evidenceSetSha256,
        acceptLeftCensoredOpenings: requiresLeftCensoredAcceptance && acceptLeftCensored,
        acceptGroupFeeScope: requiresGroupFeeAcceptance && acceptGroupFee,
      });
      setConfirmPhase({ requestKey, status: 'success', response });
    } catch (reason) {
      const parsed = parseApiError(reason);
      setConfirmPhase({
        requestKey,
        status: parsed.status === 409 ? 'conflict' : 'failed',
        error: parsed,
      });
    }
  };

  if (confirmCurrent?.status === 'conflict') {
    // The server refused the exact echoed plan: the preview shown before is
    // stale evidence now. Hide it entirely and require a fresh zero-write
    // preview before any further confirm.
    return (
      <section className="rounded-ds-md border border-subtle bg-bg-1 p-4" aria-label="未来回合预览">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-body font-semibold text-text-1">未来回合预览</h3>
          <span className="rounded-full border border-down-strong/30 bg-down-subtle px-2.5 py-1 text-caption text-down-strong">预览已失效</span>
        </div>
        <InlineAlert
          className="mt-3"
          variant="danger"
          title="正式 Future Build 未写入（409）"
          message={confirmCurrent.error.message}
        />
        <p className="mt-3 text-caption text-text-2">
          计划已变化或 fence 过期——请重新运行零写预览后再确认。原预览内容已作废，页面不再展示。
        </p>
        <button type="button" className="btn-primary mt-3" onClick={onRetry}>
          重新运行零写预览
        </button>
      </section>
    );
  }

  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1 p-4" aria-label="未来回合预览">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Future PositionEpisode · In-memory plan</div>
          <h3 className="mt-1 text-body font-semibold text-text-1">未来回合预览</h3>
          <p className="mt-1 text-caption text-text-3">
            快照 #{preview.snapshotId} → Canonical #{preview.targetCanonicalSetId} · 预计 {counts.plannedPositionEpisodeCount.toLocaleString()} 个回合：{counts.plannedClosedEpisodeCount.toLocaleString()} 已归零 / {counts.plannedOpenEpisodeCount.toLocaleString()} 未归零
          </p>
        </div>
        <span className="rounded-full border border-up-strong/25 bg-up-subtle px-2.5 py-1 text-caption text-up-strong">只读 · 尚未构建</span>
      </div>

      <dl className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
          <dt className="text-caption text-text-3">边界持仓</dt>
          <dd className="mt-1 font-mono text-body-sm text-text-1">{counts.openingPositionCount.toLocaleString()} 个身份 · {decimalDisplay(counts.openingContractCount)} 张</dd>
        </div>
        <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
          <dt className="text-caption text-text-3">边界后执行</dt>
          <dd className="mt-1 font-mono text-body-sm text-text-1">{counts.postBoundaryOrderEventCount.toLocaleString()} orders · {counts.postBoundaryFillEventCount.toLocaleString()} fills</dd>
        </div>
        <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
          <dt className="text-caption text-text-3">计划回合</dt>
          <dd className="mt-1 font-mono text-body-sm text-text-1">{counts.plannedClosedEpisodeCount.toLocaleString()} closed · {counts.plannedOpenEpisodeCount.toLocaleString()} open</dd>
        </div>
        <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
          <dt className="text-caption text-text-3">证据 / 费用</dt>
          <dd className="mt-1 text-body-sm text-up-strong">费用守恒已通过</dd>
          <p className="mt-1 text-[10px] text-text-4">Headline {counts.headlineEpisodeCount} · 排除 {counts.headlineExcludedEpisodeCount}</p>
        </div>
      </dl>

      {counts.sourceEventCount === 0 && (
        <p className="mt-3 rounded-ds-md border border-subtle bg-bg-2 px-3 py-2 text-caption text-text-2">
          边界后暂无执行事件；预览仍有效，计划结果仅延续已确认的边界仓位。
        </p>
      )}

      {counts.leftCensoredEpisodeCount > 0 && (
        <p className="mt-3 rounded-ds-md border border-warn-strong/25 bg-warn-subtle px-3 py-2 text-caption text-text-2">
          <strong className="text-text-1">{counts.leftCensoredEpisodeCount.toLocaleString()} 个左删失回合。</strong>
          {' '}仓位边界已验证，但边界前开仓成本与费用未知；系统不会伪造 P&amp;L。
        </p>
      )}

      <details className="mt-3 rounded-ds-md border border-subtle bg-bg-2 px-3 py-2">
        <summary className="cursor-pointer text-caption text-text-3">审计详情与警告</summary>
        <div className="mt-2 space-y-2 text-caption text-text-2">
          <p>边界（ET）{formatEt(preview.boundaryAt)} → 事实截止（ET）{formatEt(preview.sourceCutoffAt)}</p>
          {preview.warnings.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-warn-strong">
              {preview.warnings.map((warning) => <li key={warning}>{warningLabel(warning)}</li>)}
            </ul>
          )}
          <dl className="grid gap-1 font-mono text-[10px] text-text-4 sm:grid-cols-2">
            <div><dt className="inline">Fence </dt><dd className="inline">{compactHash(preview.fenceKey)}</dd></div>
            <div><dt className="inline">Snapshot </dt><dd className="inline">{compactHash(preview.snapshotKey)}</dd></div>
            <div><dt className="inline">Canonical </dt><dd className="inline">{compactHash(preview.targetCanonicalSetSha256)}</dd></div>
            <div><dt className="inline">Builder config </dt><dd className="inline">{compactHash(preview.builderConfigSha256)}</dd></div>
            <div><dt className="inline">Evidence </dt><dd className="inline">{compactHash(preview.evidenceSetSha256)}</dd></div>
            <div><dt className="inline">Planned build </dt><dd className="inline">{compactHash(preview.plannedBuildKey)}</dd></div>
          </dl>
        </div>
      </details>

      <section
        className="mt-3 rounded-ds-md border border-subtle bg-bg-2 p-3"
        aria-label="正式 Future Build 确认"
      >
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Formal build · Append-only</div>
          <h4 className="mt-1 text-body-sm font-semibold text-text-1">正式 Future Build</h4>
          <p className="mt-1 text-caption text-text-3">
            把上方零写预览按原样追加为一条正式 build（append-only）。写入不会激活为默认复盘视图，也不会执行任何交易动作。
          </p>
          <dl className="mt-2 grid gap-1 font-mono text-[10px] text-text-4 sm:grid-cols-2">
            <div><dt className="inline">计划 build </dt><dd className="inline">{compactHash(preview.plannedBuildKey)}</dd></div>
            <div><dt className="inline">证据指纹 </dt><dd className="inline">{compactHash(preview.evidenceSetSha256)}</dd></div>
          </dl>
          <p className="mt-1 text-caption text-text-3">
            计划回合 {counts.plannedPositionEpisodeCount.toLocaleString()}（{counts.plannedClosedEpisodeCount.toLocaleString()} 已归零 · {counts.plannedOpenEpisodeCount.toLocaleString()} 未归零）· 左删失 {counts.leftCensoredEpisodeCount.toLocaleString()} · 组费影响 {counts.groupFeeAffectedEpisodeCount.toLocaleString()}
          </p>
        </div>

        {requiresLeftCensoredAcceptance && (
          <label className="mt-3 flex items-start gap-3 rounded-ds-md border border-subtle bg-bg-1 p-3 text-body-sm text-text-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
              checked={acceptLeftCensored}
              disabled={confirming || Boolean(confirmedBuild)}
              onChange={(event) => setAcceptLeftCensored(event.target.checked)}
            />
            <span>
              <strong className="text-text-1">接受 snapshot 继承仓位为 left-censored（无券商成本）。</strong>
              {' '}{counts.leftCensoredEpisodeCount.toLocaleString()} 个回合缺少边界前的真实开仓成本与费用，系统不会伪造这些数字。
            </span>
          </label>
        )}

        {requiresGroupFeeAcceptance && (
          <label className="mt-3 flex items-start gap-3 rounded-ds-md border border-subtle bg-bg-1 p-3 text-body-sm text-text-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
              checked={acceptGroupFee}
              disabled={confirming || Boolean(confirmedBuild)}
              onChange={(event) => setAcceptGroupFee(event.target.checked)}
            />
            <span>
              <strong className="text-text-1">接受组合单费用仅组级精确。</strong>
              {' '}{counts.groupFeeAffectedEpisodeCount.toLocaleString()} 个回合的组合费用只在执行组层保留，不做腿级分摊猜测。
            </span>
          </label>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-primary"
            disabled={!canConfirmBuild}
            onClick={() => void handleConfirmBuild()}
          >
            {confirming ? '正在写入…' : '写入正式 Future Build'}
          </button>
          {!confirming && !confirmedBuild && !acceptancesSatisfied && (
            <span className="text-caption text-warn-strong">接受上方全部前提后才能写入。</span>
          )}
        </div>

        {confirmCurrent?.status === 'failed' && (
          <ApiErrorAlert className="mt-3" error={confirmCurrent.error} />
        )}

        {confirmedBuild && (
          <div className="mt-3 space-y-2">
            <InlineAlert
              variant="success"
              title={confirmedBuild.duplicate ? '该 build 已存在（幂等重放）' : '正式 Future Build 已写入'}
              message={`Build #${confirmedBuild.build.id} · ${compactHash(confirmedBuild.build.buildKey)} · ${confirmedBuild.build.positionEpisodeCount.toLocaleString()} 个回合。`}
            />
            <p className="rounded-ds-md border border-warn-strong/25 bg-warn-subtle px-3 py-2 text-body-sm text-text-2">
              <strong className="text-text-1">已构建 ≠ 已生效：激活前默认复盘视图不变。</strong>
              {' '}如需生效，请在“仓位回合”页用上方 build 编号显式打开该构建后单独激活；激活会切换默认视图，且之后无法回到「零激活」的 CSV 默认状态。
            </p>
          </div>
        )}
      </section>

      <p className="mt-3 text-[10px] text-text-4">
        {confirmedBuild
          ? '预览自身零写 · 本次已显式追加 1 条正式 build · 默认激活变更 0 · 交易动作 0'
          : '业务写入 0 · Episode 构建 0 · 默认激活变更 0 · 交易动作 0'}
      </p>
    </section>
  );
};

export default FutureEpisodePreviewSummary;

import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiErrorAlert } from '../common/ApiErrorAlert';
import { InlineAlert } from '../common/InlineAlert';
import FutureEpisodePreviewSummary from './FutureEpisodePreviewSummary';
import { parseApiError, type ParsedApiError } from '../../api/error';
import {
  confirmPositionSnapshot,
  fetchLatestPositionSnapshot,
  fetchPositionSnapshotContinuity,
  previewPositionSnapshot,
} from '../../api/journalPositions';
import type {
  ConfirmedPositionSnapshot,
  CurrentOptionPosition,
  LatestPositionSnapshotResponse,
  PositionSnapshotContinuityAssessment,
  PositionSnapshotContinuityReason,
  PositionSnapshotObservation,
  PositionSnapshotPreviewResponse,
} from '../../types/journalPositions';

interface CurrentPositionsSnapshotCardProps {
  onOpenEvidence?: () => void;
}

interface SnapshotIdentityIssue {
  kind: 'checking' | 'unavailable' | 'mismatch';
  message: string;
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

function decimalDisplay(value?: string | null): string {
  if (value == null || !value.trim()) return '—';
  const trimmed = value.trim();
  if (!trimmed.includes('.')) return trimmed;
  return trimmed.replace(/0+$/, '').replace(/\.$/, '') || '0';
}

function durationDisplay(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '时长未知';
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours < 24) return remainingMinutes > 0
    ? `${hours} 小时 ${remainingMinutes} 分钟`
    : `${hours} 小时`;
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return remainingHours > 0 ? `${days} 天 ${remainingHours} 小时` : `${days} 天`;
}

function currentnessCopy(
  snapshot: ConfirmedPositionSnapshot,
  latest: LatestPositionSnapshotResponse,
): { title: string; message: string } {
  const age = durationDisplay(snapshot.freshness.ageSeconds);
  const threshold = durationDisplay(snapshot.freshness.thresholdSeconds);
  if (
    snapshot.accountBindingStatus === 'mismatch'
    || latest.latestAccountBindingMatches === false
  ) {
    return {
      title: '历史快照 · 当前账户不匹配',
      message: `这份已确认快照属于另一账户绑定，不能作为当前 OpenD 账户的持仓状态。它仍保留作审计；请对当前账户重新执行只读检查。${snapshot.freshness.status === 'stale' ? ` 同时它的采集完成时间距今 ${age}。` : ''}`,
    };
  }
  if (
    snapshot.publicationAnchorStatus === 'superseded'
    || latest.latestPublicationAnchorMatches === false
  ) {
    return {
      title: '历史快照 · 账户证据锚点已更新',
      message: '这份快照绑定的 Journal 刷新已不是当前最新锚点，不能继续称为当前仓位。旧记录仍保留作审计；请重新执行只读检查。',
    };
  }
  if (
    snapshot.freshness.status === 'stale'
    && (snapshot.freshness.futureSkewSeconds ?? 0) > 0
  ) {
    return {
      title: '历史快照 · 本地时间异常',
      message: `快照的采集完成时间比本次服务器评估时间晚 ${durationDisplay(snapshot.freshness.futureSkewSeconds)}，已超出允许的时钟偏差。修正本机时间并重新只读检查前，不能把它视为当前仓位。`,
    };
  }
  if (snapshot.freshness.status === 'stale') {
    return {
      title: '历史快照 · 已过期',
      message: `这份快照的采集完成时间距今 ${age}，已超过“当前”门槛（${threshold}）。它仍是有效历史证据，但在重新只读检查前不能代表现在的仓位。`,
    };
  }
  return {
    title: '历史快照 · 当前性未证明',
    message: '这份已确认快照的账户、publication 或时间状态未通过当前性检查；页面只把它作为历史证据展示。请重新执行只读检查。',
  };
}

function positionSideLabel(value: string): string {
  if (value === 'LONG') return '多头';
  if (value === 'SHORT') return '空头';
  return value;
}

function optionRightLabel(value: string): string {
  if (value === 'C' || value === 'CALL') return 'Call';
  if (value === 'P' || value === 'PUT') return 'Put';
  return value;
}

function contractSpecLabel(value: string): string {
  if (value === 'complete') return '规格完整';
  if (value === 'not_applicable') return '空仓无需规格';
  if (value === 'partial') return '规格不完整';
  if (value === 'unavailable') return '规格读取不可用';
  return value || '未知';
}

function comparisonBasisLabel(value: string): string {
  if (value === 'instrument_position_side_signed_quantity_contracts') {
    return '合约代码 + 方向 + 有符号张数';
  }
  return value || '未提供';
}

function warningLabel(value: string): string {
  const labels: Record<string, string> = {
    position_boundary_changed_between_reads: '两次读取之间的仓位边界发生变化。',
    contract_specs_unavailable: '合约规格读取不可用。',
    retrieval_incomplete: '本次仓位读取未完整完成。',
    stability_not_proven: '两次读取尚未证明仓位边界稳定。',
    analysis_not_ready: '当前证据尚未通过分析可用性检查。',
    account_continuity_not_ready: '尚未建立同一账户的证据连续性锚点。',
    account_continuity_not_established: '尚未建立同一账户的证据连续性；请先确认一次 OpenD 只读交易证据刷新。',
    account_binding_mismatch: '当前 OpenD 账户与最近确认的交易证据账户不一致；请核对账户后重新检查。',
    position_retrieval_incomplete: '当前仓位读取未完整；不能把本次结果视为完整仓位。',
    position_boundary_unstable: '两次读取的仓位不一致；请等待交易或同步结束后重新检查。',
    position_snapshot_incomplete: '当前快照未通过完整性检查；本次预览不能确认。',
    position_validation_issues: '当前仓位存在字段或合约规格验证问题；请根据下方警告核对。',
  };
  if (labels[value]) return labels[value];
  const zeroQuantityMatch = value.match(/^filtered_zero_quantity_rows:(\d+)$/);
  if (zeroQuantityMatch) {
    return `已过滤 ${Number(zeroQuantityMatch[1]).toLocaleString()} 行零数量缓存记录；这些记录不属于持仓。`;
  }
  if (value.startsWith('missing_or_invalid_contract_spec:')) {
    return `合约规格缺失或无效：${value.split(':').slice(1).join(':')}`;
  }
  return value;
}

function continuityReasonLabel(reason: PositionSnapshotContinuityReason): string {
  const labels: Record<string, string> = {
    no_confirmed_snapshot: '尚无已确认的当前仓位快照。',
    snapshot_anchor_missing: '快照绑定的交易证据发布记录不存在或无法验证。',
    snapshot_not_future_anchor: '快照没有通过完整、稳定和 future-only 门禁。',
    no_later_publication: '尚无晚于快照的已确认交易证据刷新。',
    publication_binding_mismatch: '后续刷新与快照不是同一账户绑定。',
    publication_chain_gap: '后续刷新链没有连续覆盖快照采集区间。',
    target_canonical_unavailable: '后续刷新没有可验证的 canonical 事实集。',
    guard_fill_detected: '快照采集守护区间内存在成交，无法确定唯一边界。',
    aggregate_changed_pre_boundary: '只有订单时间、没有精确成交时间的汇总成交跨入边界不确定区。',
    straddling_order: '同一订单的逐笔成交跨越边界，费用与数量不能安全切分。',
    straddling_group: '同一组合执行组跨越边界，不能安全拆分为前后两段。',
    unbacked_post_boundary: '边界后的成交来源没有全部落在已发布的刷新链内。',
    option_identity_mismatch: '快照合约与后续事实的期权身份或乘数不兼容。',
  };
  const detail = labels[reason.code] || reason.message || '连续性证据未通过。';
  return reason.count > 1 ? `${detail}（${reason.count.toLocaleString()} 项）` : detail;
}

function compactHash(value?: string | null): string {
  if (!value) return '—';
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function EpisodeBoundaryReadiness({
  assessment,
  loading,
  error,
  continuityReady,
  snapshotIdentityIssue,
  onRetry,
  onOpenEvidence,
}: {
  assessment: PositionSnapshotContinuityAssessment | null;
  loading: boolean;
  error: ParsedApiError | null;
  continuityReady: boolean;
  snapshotIdentityIssue: SnapshotIdentityIssue | null;
  onRetry: () => void;
  onOpenEvidence?: () => void;
}) {
  if (loading && !assessment) {
    return (
      <section className="rounded-ds-md border border-subtle bg-bg-1 p-4" aria-label="未来 Episode 连续性门禁">
        <p className="text-body-sm text-text-3">正在核对未来 Episode 连续性…</p>
      </section>
    );
  }
  if (error && !assessment) {
    return <ApiErrorAlert error={error} actionLabel="重新核对连续性" onAction={onRetry} />;
  }
  if (!assessment) return null;

  const presentation = snapshotIdentityIssue ? {
    label: snapshotIdentityIssue.kind === 'checking' ? '身份核对中' : '核对阻断',
    tone: snapshotIdentityIssue.kind === 'checking'
      ? 'border-subtle bg-bg-2 text-text-2'
      : 'border-down-strong/30 bg-down-subtle text-down-strong',
    title: snapshotIdentityIssue.kind === 'checking'
      ? '正在交叉核对最新快照'
      : snapshotIdentityIssue.kind === 'unavailable'
        ? '状态无法交叉核对'
        : '状态已变化，请重新核对',
    message: snapshotIdentityIssue.message,
  } : ({
    no_snapshot: {
      label: '等待仓位快照',
      tone: 'border-subtle bg-bg-2 text-text-2',
      title: '还没有可核对的未来边界',
      message: continuityReady
        ? '账户证据锚点已经具备；下一步是执行只读检查并显式确认一份当前仓位快照。'
        : '先在上方「每日刷新」确认一次同账户的只读刷新，再执行并确认当前仓位快照。',
    },
    awaiting_refresh: {
      label: '等待后续刷新',
      tone: 'border-warn-strong/30 bg-warn-subtle text-warn-strong',
      title: '快照已冻结，仍缺快照之后的成交覆盖',
      message: '下一完整交易日结束后，在上方「每日刷新」确认一次覆盖采集区间的只读刷新。快照即使已经过 30 分钟，仍可接受这项历史连续性核对。',
    },
    blocked: {
      label: '门禁阻断',
      tone: 'border-down-strong/30 bg-down-subtle text-down-strong',
      title: '后续证据无法证明唯一、安全的 Episode 边界',
      message: '系统不会把这份快照自动写进 Episode，也不会用模糊时间、跨界订单或未发布来源补造持仓历史。',
    },
    ready: {
      label: '连续性已证明',
      tone: 'border-up-strong/30 bg-up-subtle text-up-strong',
      title: '可以进入下一步的零写 Episode 构建预览',
      message: '账户、刷新覆盖、采集守护区间与后续来源门禁均已通过；当前尚未生成、激活或改写任何 Episode。',
    },
  }[assessment.status]);
  const counts = assessment.counts;
  const showAuditCounts = assessment.snapshotId != null;

  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1 p-4" aria-label="未来 Episode 连续性门禁">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Future Episode · Zero-write fence</div>
          <h3 className="mt-1 text-body font-semibold text-text-1">{presentation.title}</h3>
          <p className="mt-1 max-w-3xl text-caption text-text-3">{presentation.message}</p>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-caption font-medium ${presentation.tone}`}>
          {presentation.label}
        </span>
      </div>

      {showAuditCounts && (
        <dl className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
            <dt className="text-caption text-text-3">边界 / 快照</dt>
            <dd className="mt-1 text-body-sm text-text-1">{formatEt(assessment.boundaryAt, true)} ET · #{assessment.snapshotId}</dd>
          </div>
          <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
            <dt className="text-caption text-text-3">后续发布链</dt>
            <dd className="mt-1 font-mono text-body-sm text-text-1">{counts.chainPublicationCount} 次 / {counts.chainBatchCount} 批</dd>
          </div>
          <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
            <dt className="text-caption text-text-3">守护区成交</dt>
            <dd className={`mt-1 font-mono text-body-sm ${counts.guardFillCount === 0 ? 'text-text-1' : 'text-down-strong'}`}>{counts.guardFillCount}</dd>
          </div>
          <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
            <dt className="text-caption text-text-3">跨界订单 / 组合</dt>
            <dd className={`mt-1 font-mono text-body-sm ${counts.straddlingOrderCount + counts.straddlingGroupCount === 0 ? 'text-text-1' : 'text-down-strong'}`}>
              {counts.straddlingOrderCount} / {counts.straddlingGroupCount}
            </dd>
          </div>
        </dl>
      )}

      {assessment.reasons.length > 0 && (
        <details className="mt-3 rounded-ds-md border border-subtle bg-bg-2 px-3 py-2">
          <summary className="cursor-pointer text-caption text-text-3">查看门禁依据（{assessment.reasons.length}）</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-caption text-text-2">
            {assessment.reasons.map((reason, index) => (
              <li key={`${reason.code}-${index}`}>{continuityReasonLabel(reason)}</li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 text-[10px] text-text-4">
        <span>只读核对 · 证据写入 0 · 交易动作 0</span>
        {!snapshotIdentityIssue && assessment.targetCanonicalSetId != null && (
          <span>Canonical #{assessment.targetCanonicalSetId} · {compactHash(assessment.targetCanonicalSetSha256)}</span>
        )}
        {!snapshotIdentityIssue && assessment.fenceKey && <span>Fence {compactHash(assessment.fenceKey)}</span>}
        {snapshotIdentityIssue && snapshotIdentityIssue.kind !== 'checking' && (
          <button type="button" className="btn-ghost ml-auto" onClick={onRetry}>重新核对状态</button>
        )}
        {!snapshotIdentityIssue && onOpenEvidence && (assessment.status === 'awaiting_refresh' || assessment.status === 'blocked' || (!continuityReady && assessment.status === 'no_snapshot')) && (
          <button type="button" className="btn-ghost ml-auto" onClick={onOpenEvidence}>前往每日刷新</button>
        )}
      </div>
    </section>
  );
}

function isCompleteEmpty(observation: PositionSnapshotObservation): boolean {
  return observation.contentState === 'empty'
    && observation.positionCount === 0
    && observation.stable
    && observation.retrievalComplete
    && observation.positionSnapshotComplete
    && observation.analysisReady;
}

function isFailedOrIncomplete(observation: PositionSnapshotObservation): boolean {
  return !observation.retrievalComplete
    || !observation.stable
    || !observation.positionSnapshotComplete
    || !observation.analysisReady
    || observation.contentState === 'failed'
    || observation.contentState === 'incomplete';
}

function BrokerCostContext({ position }: { position: CurrentOptionPosition }) {
  const context = position.brokerCostContext;
  return (
    <div className="min-w-44 text-left">
      <div className="text-caption text-text-2">
        成本价 {position.currency} {decimalDisplay(context.costPrice)} · 平均 {decimalDisplay(context.averageCost)}
      </div>
      <div className="mt-0.5 text-[10px] text-text-4">
        券商成本上下文 · 摊薄 {decimalDisplay(context.dilutedCost)}
      </div>
      {context.costPriceValid === false && (
        <div className="mt-0.5 text-[10px] text-warn-strong">券商标记该成本字段无效</div>
      )}
    </div>
  );
}

function ObservationDetails({
  observation,
  defaultOpen = false,
}: {
  observation: PositionSnapshotObservation;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const hashes = Object.entries(observation.hashes);
  const counts = observation.filterCounts;
  return (
    <details
      className="rounded-ds-md border border-subtle bg-bg-1"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer px-4 py-3 text-body-sm font-medium text-text-2">
        查看采集与仓位明细
      </summary>
      <div className="space-y-4 border-t border-subtle p-4">
        <dl className="grid gap-2 text-caption sm:grid-cols-2 lg:grid-cols-4">
          <div><dt className="text-text-3">第一次原始行</dt><dd className="mt-1 font-mono text-text-1">{counts.firstTotalRows.toLocaleString()}</dd></div>
          <div><dt className="text-text-3">第二次原始行</dt><dd className="mt-1 font-mono text-text-1">{counts.secondTotalRows.toLocaleString()}</dd></div>
          <div><dt className="text-text-3">过滤非美股 / 非期权</dt><dd className="mt-1 font-mono text-text-1">{counts.filteredNonUsRows.toLocaleString()} / {counts.filteredNonOptionRows.toLocaleString()}</dd></div>
          <div><dt className="text-text-3">过滤零数量缓存行</dt><dd className="mt-1 font-mono text-text-1">{counts.filteredZeroQuantityRows.toLocaleString()}</dd></div>
          <div><dt className="text-text-3">已核对规格行</dt><dd className="mt-1 font-mono text-text-1">{counts.contractSpecCount.toLocaleString()}</dd></div>
          <div><dt className="text-text-3">验证问题</dt><dd className="mt-1 font-mono text-text-1">{counts.validationIssueCount.toLocaleString()}</dd></div>
          <div><dt className="text-text-3">规格状态</dt><dd className="mt-1 text-text-1">{contractSpecLabel(observation.contractSpecStatus)}</dd></div>
          <div><dt className="text-text-3">快照完整</dt><dd className="mt-1 text-text-1">{observation.positionSnapshotComplete ? '是' : '否'}</dd></div>
          <div><dt className="text-text-3">券商 as-of</dt><dd className="mt-1 font-mono text-text-1">{observation.brokerAsOf ? `${formatEt(observation.brokerAsOf, true)} ET` : '未提供'}</dd></div>
          <div><dt className="text-text-3">采集操作完成（ET）</dt><dd className="mt-1 font-mono text-text-1">{formatEt(observation.operationCompletedAt, true)}</dd></div>
        </dl>

        <div className="rounded-ds-md border border-subtle bg-bg-2 px-3 py-2 text-caption text-text-3">
          <div>
            两次读取比对：<span className="text-text-1">{observation.stability.stable ? '一致' : '不一致'}</span>
            {' '}· {comparisonBasisLabel(observation.stability.comparisonBasis)}
          </div>
          {!observation.stability.stable && (
            <div className="mt-1 text-warn-strong">
              新增 {observation.stability.addedSymbols.length} · 减少 {observation.stability.removedSymbols.length} · 数量或方向变化 {observation.stability.changedSymbols.length}
            </div>
          )}
        </div>

        {observation.positions.length > 0 ? (
          <div className="overflow-x-auto rounded-ds-md border border-subtle">
            <table className="w-full min-w-[920px] text-body-sm" aria-label="当前仓位明细">
              <thead className="bg-bg-2 text-label uppercase tracking-label text-text-3">
                <tr>
                  <th className="px-3 py-2 text-left">合约</th>
                  <th className="px-3 py-2 text-left">方向</th>
                  <th className="px-3 py-2 text-right">张数</th>
                  <th className="px-3 py-2 text-right">可卖</th>
                  <th className="px-3 py-2 text-right">乘数</th>
                  <th className="px-3 py-2 text-left">券商成本上下文</th>
                </tr>
              </thead>
              <tbody>
                {observation.positions.map((position) => (
                  <tr key={position.symbol} className="border-t border-subtle">
                    <td className="px-3 py-3">
                      <div className="font-mono text-mono-sm text-text-1">{position.symbol}</div>
                      <div className="mt-0.5 text-caption text-text-3">
                        {position.underlying} · {position.expiry} · {decimalDisplay(position.strike)} {optionRightLabel(position.optionRight)}
                      </div>
                    </td>
                    <td className="px-3 py-3 text-text-2">{positionSideLabel(position.positionSide)}</td>
                    <td className="px-3 py-3 text-right font-mono text-text-1">{decimalDisplay(position.signedQuantityContracts)}</td>
                    <td className="px-3 py-3 text-right font-mono text-text-2">{decimalDisplay(position.canSellQuantityContracts)}</td>
                    <td className="px-3 py-3 text-right">
                      <div className="font-mono text-text-1">{decimalDisplay(position.contractMultiplier)}</div>
                      <div className="mt-0.5 text-[10px] text-text-4">{position.basis}</div>
                    </td>
                    <td className="px-3 py-3"><BrokerCostContext position={position} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="rounded-ds-md border border-subtle bg-bg-2 px-3 py-2 text-caption text-text-3">
            已完成的两次读取均没有有效期权持仓行。
          </div>
        )}

        <details className="rounded-ds-md border border-subtle bg-bg-2 px-3 py-2">
          <summary className="cursor-pointer text-caption text-text-3">证据指纹</summary>
          {hashes.length > 0 ? (
            <dl className="mt-2 grid gap-2 sm:grid-cols-2">
              {hashes.map(([key, value]) => (
                <div key={key} className="min-w-0">
                  <dt className="text-[10px] text-text-4">{key}</dt>
                  <dd className="truncate font-mono text-[10px] text-text-2" title={value}>{value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="mt-2 text-caption text-text-4">没有可显示的指纹。</p>
          )}
        </details>
      </div>
    </details>
  );
}

const CurrentPositionsSnapshotCard: React.FC<CurrentPositionsSnapshotCardProps> = ({
  onOpenEvidence,
}) => {
  const [latest, setLatest] = useState<LatestPositionSnapshotResponse | null>(null);
  const [latestLoading, setLatestLoading] = useState(true);
  const [latestError, setLatestError] = useState<ParsedApiError | null>(null);
  const [boundaryReadiness, setBoundaryReadiness] = useState<PositionSnapshotContinuityAssessment | null>(null);
  const [boundaryLoading, setBoundaryLoading] = useState(true);
  const [boundaryError, setBoundaryError] = useState<ParsedApiError | null>(null);
  const [preview, setPreview] = useState<PositionSnapshotPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<ParsedApiError | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmedMessage, setConfirmedMessage] = useState<string | null>(null);
  const [acknowledgedFutureOnly, setAcknowledgedFutureOnly] = useState(false);
  const latestRequestGeneration = useRef(0);
  const boundaryRequestGeneration = useRef(0);
  const lastResumeRefreshAt = useRef(0);

  const loadLatest = useCallback(async () => {
    const requestGeneration = ++latestRequestGeneration.current;
    setLatestLoading(true);
    setLatestError(null);
    try {
      const response = await fetchLatestPositionSnapshot();
      if (requestGeneration !== latestRequestGeneration.current) return;
      setLatest(response);
    } catch (reason) {
      if (requestGeneration !== latestRequestGeneration.current) return;
      setLatestError(parseApiError(reason));
    } finally {
      if (requestGeneration === latestRequestGeneration.current) {
        setLatestLoading(false);
      }
    }
  }, []);

  const loadBoundaryReadiness = useCallback(async () => {
    const requestGeneration = ++boundaryRequestGeneration.current;
    setBoundaryLoading(true);
    setBoundaryError(null);
    setBoundaryReadiness(null);
    try {
      const response = await fetchPositionSnapshotContinuity();
      if (requestGeneration !== boundaryRequestGeneration.current) return;
      setBoundaryReadiness(response);
    } catch (reason) {
      if (requestGeneration !== boundaryRequestGeneration.current) return;
      setBoundaryReadiness(null);
      setBoundaryError(parseApiError(reason));
    } finally {
      if (requestGeneration === boundaryRequestGeneration.current) {
        setBoundaryLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadLatest();
    void loadBoundaryReadiness();
    return () => {
      latestRequestGeneration.current += 1;
      boundaryRequestGeneration.current += 1;
    };
  }, [loadBoundaryReadiness, loadLatest]);

  useEffect(() => {
    const current = latest?.latest;
    if (!current?.isCurrent || current.freshness.ageSeconds == null) return undefined;
    const remainingSeconds = Math.max(
      0,
      current.freshness.thresholdSeconds - current.freshness.ageSeconds,
    );
    const snapshotId = current.id;
    const timer = window.setTimeout(() => {
      // The client may only fail closed.  The server GET below remains the
      // authority that can classify a later observation as current again.
      setLatest((value) => {
        if (!value?.latest || value.latest.id !== snapshotId || !value.latest.isCurrent) {
          return value;
        }
        return {
          ...value,
          latestIsCurrent: false,
          latest: {
            ...value.latest,
            isCurrent: false,
            freshness: {
              ...value.latest.freshness,
              status: 'stale',
              ageSeconds: value.latest.freshness.thresholdSeconds,
            },
          },
        };
      });
      void loadLatest();
    }, remainingSeconds * 1000 + 100);
    return () => window.clearTimeout(timer);
  }, [latest?.latest, loadLatest]);

  useEffect(() => {
    const refreshAfterResume = () => {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - lastResumeRefreshAt.current < 500) return;
      lastResumeRefreshAt.current = now;
      void loadLatest();
      void loadBoundaryReadiness();
    };
    window.addEventListener('focus', refreshAfterResume);
    document.addEventListener('visibilitychange', refreshAfterResume);
    return () => {
      window.removeEventListener('focus', refreshAfterResume);
      document.removeEventListener('visibilitychange', refreshAfterResume);
    };
  }, [loadBoundaryReadiness, loadLatest]);

  const handlePreview = async () => {
    setPreviewLoading(true);
    setPreviewError(null);
    setConfirmedMessage(null);
    setAcknowledgedFutureOnly(false);
    try {
      const response = await previewPositionSnapshot();
      setPreview(response);
    } catch (reason) {
      setPreview(null);
      setPreviewError(parseApiError(reason));
    } finally {
      setPreviewLoading(false);
    }
  };

  let snapshotIdentityIssue: SnapshotIdentityIssue | null = null;
  if (boundaryReadiness?.status === 'ready') {
    if (latestLoading) {
      snapshotIdentityIssue = {
        kind: 'checking',
        message: '连续性评估已经返回，但最新快照仍在读取。交叉核对完成前，页面不会把门禁显示为可用。',
      };
    } else if (latestError || !latest?.latest) {
      snapshotIdentityIssue = {
        kind: 'unavailable',
        message: '最新快照缺失或读取失败，页面无法证明连续性评估仍针对同一份快照。重新读取两项状态前，门禁保持阻断。',
      };
    } else if (
      latest.latest.id !== boundaryReadiness.snapshotId
      || latest.latest.key !== boundaryReadiness.snapshotKey
    ) {
      const idChanged = latest.latest.id !== boundaryReadiness.snapshotId;
      snapshotIdentityIssue = {
        kind: 'mismatch',
        message: idChanged
          ? `当前最新快照为 #${latest.latest.id}，连续性评估仍针对 #${boundaryReadiness.snapshotId}。为避免把过期证据标记为可用，页面已按阻断处理。`
          : `当前最新快照 #${latest.latest.id} 的不可变指纹与连续性评估不一致。为避免把过期或漂移的证据标记为可用，页面已按阻断处理。`,
      };
    }
  }
  const futurePreviewEligible = Boolean(
    boundaryReadiness?.status === 'ready'
    && latest?.latest
    && latest.latest.id === boundaryReadiness.snapshotId
    && latest.latest.key === boundaryReadiness.snapshotKey
    && !snapshotIdentityIssue,
  );

  const canConfirm = Boolean(
    preview
    && preview.confirmAllowed
    && preview.observation.futureAnchorCandidate
    && latest?.continuityReady
    && !snapshotIdentityIssue
    && acknowledgedFutureOnly
    && !confirming,
  );

  const handleConfirm = async () => {
    if (!preview || !canConfirm) return;
    setConfirming(true);
    setPreviewError(null);
    try {
      const response = await confirmPositionSnapshot(
        preview.artifactId,
        preview.previewKey,
        true,
      );
      setConfirmedMessage(response.duplicate
        ? '这份当前仓位证据已确认；页面已重新读取服务器记录。'
        : '当前仓位证据已保存，可作为后续证据连续性的起点。');
      setPreview(null);
      setAcknowledgedFutureOnly(false);
      await Promise.all([loadLatest(), loadBoundaryReadiness()]);
    } catch (reason) {
      setPreviewError(parseApiError(reason));
    } finally {
      setConfirming(false);
    }
  };

  const statusLabel = useMemo(() => {
    if (latestLoading && !latest) return '读取状态中';
    if (latestError && !latest) return '状态读取失败';
    if (!latest) return '状态不可用';
    if (!latest.enabled) return '功能未启用';
    if (!latest.configured) return '尚未配置';
    if (!latest.latest) return '尚无已确认快照';
    if (!latest.latestIsCurrent || !latest.latest.isCurrent) {
      if (
        latest.latest.accountBindingStatus === 'mismatch'
        || latest.latestAccountBindingMatches === false
      ) return '历史快照 · 账户不匹配';
      if (
        latest.latest.freshness.status === 'stale'
        && (latest.latest.freshness.futureSkewSeconds ?? 0) > 0
      ) return '历史快照 · 时间异常';
      if (latest.latest.freshness.status === 'stale') return '历史快照 · 已过期';
      return '历史快照 · 非当前';
    }
    if (isCompleteEmpty(latest.latest.observation)) return '当前快照 · 完整空仓';
    return '当前快照 · 已确认';
  }, [latest, latestError, latestLoading]);

  const latestObservation = latest?.latest?.observation;
  const latestCurrentness = latest?.latest && latest.continuityReady && !latest.latestIsCurrent
    ? currentnessCopy(latest.latest, latest)
    : null;

  return (
    <section className="card-base overflow-hidden" aria-label="当前期权仓位快照">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-subtle px-5 py-4">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Moomoo · 当前仓位证据</div>
          <h2 className="mt-1 text-h2 text-text-1">当前期权仓位快照</h2>
          <p className="mt-1 text-caption text-text-3">只读获取 Moomoo 当前时点的期权仓位；不会下单，也不会改写历史成交。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-subtle bg-bg-2 px-2.5 py-1 text-caption text-text-2">{statusLabel}</span>
          <button
            type="button"
            className="btn-primary"
            disabled={previewLoading || !latest?.enabled || !latest.configured}
            onClick={() => void handlePreview()}
          >
            {previewLoading ? '正在只读检查…' : '只读检查当前持仓'}
          </button>
        </div>
      </div>

      <div className="space-y-4 p-5">
        <div className="rounded-ds-md border border-warn-strong/25 bg-warn-subtle px-3 py-2 text-body-sm text-text-2">
          <strong className="text-text-1">当前时点 ≠ 历史期初。</strong>
          {' '}这张卡只建立面向未来的连续性证据，不能倒推任何历史证据窗口的期初持仓。
        </div>

        {latestError && (
          <ApiErrorAlert error={latestError} actionLabel="重新读取状态" onAction={() => void loadLatest()} />
        )}

        {!latestLoading && latest && (!latest.enabled || !latest.configured) && (
          <InlineAlert
            variant="warning"
            title={latest.enabled ? '当前仓位只读检查尚未配置' : '当前仓位快照功能未启用'}
            message="请先完成本机 OpenD、只读账户绑定与当前仓位快照配置；在此之前不会发起仓位查询。"
          />
        )}

        {latest?.enabled && latest.configured && !latest.continuityReady && (
          <InlineAlert
            variant="warning"
            title="尚无账户连续性锚点"
            message={latest.continuityReason
              ? warningLabel(latest.continuityReason)
              : '请先在上方「每日刷新」确认一次 OpenD 只读刷新，再把当前仓位证据保存为面向未来的连续性起点。'}
            action={onOpenEvidence ? (
              <button type="button" className="btn-ghost" onClick={onOpenEvidence}>前往每日刷新</button>
            ) : undefined}
          />
        )}

        {latest?.enabled && latest.configured && latest.latest && latestCurrentness && (
          <InlineAlert
            variant="warning"
            title={latestCurrentness.title}
            message={latestCurrentness.message}
          />
        )}

        {((latest?.enabled && latest.configured) || boundaryReadiness?.status === 'ready') && (
          <EpisodeBoundaryReadiness
            assessment={boundaryReadiness}
            loading={boundaryLoading}
            error={boundaryError}
            continuityReady={latest?.continuityReady ?? false}
            snapshotIdentityIssue={snapshotIdentityIssue}
            onRetry={() => {
              void Promise.all([loadLatest(), loadBoundaryReadiness()]);
            }}
            onOpenEvidence={onOpenEvidence}
          />
        )}

        {futurePreviewEligible && (
          <FutureEpisodePreviewSummary
            assessment={boundaryReadiness}
            latestSnapshot={latest?.latest ?? null}
            identityBlocked={false}
            onRetry={() => {
              void Promise.all([loadLatest(), loadBoundaryReadiness()]);
            }}
          />
        )}

        {latestObservation ? (
          <>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="最近已确认仓位快照摘要">
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <dt className="text-caption text-text-3">内容状态</dt>
                <dd className="mt-1 text-body-sm font-medium text-text-1">
                  {isCompleteEmpty(latestObservation) ? '完整空仓' : `${latestObservation.positionCount.toLocaleString()} 行有效期权持仓`}
                </dd>
              </div>
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <dt className="text-caption text-text-3">持仓张数</dt>
                <dd className="mt-1 font-mono text-mono-md text-text-1">{decimalDisplay(latestObservation.totalContracts)}</dd>
              </div>
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3 sm:col-span-2">
                <dt className="text-caption text-text-3">快照时效</dt>
                <dd className={`mt-1 text-body-sm font-medium ${latest?.latest?.isCurrent ? 'text-up-strong' : 'text-warn-strong'}`}>
                  采集完成距评估 {durationDisplay(latest?.latest?.freshness.ageSeconds)}
                </dd>
                <p className="mt-1 text-[10px] text-text-4">
                  {latest?.latest?.isCurrent ? '账户与证据锚点一致' : '当前性未通过，原因见上方提示'} · 当前门槛 {durationDisplay(latest?.latest?.freshness.thresholdSeconds)}
                </p>
              </div>
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3 sm:col-span-2 lg:col-span-4">
                <dt className="text-caption text-text-3">采集区间（ET）</dt>
                <dd className="mt-1 font-mono text-mono-xs text-text-1">
                  {formatEt(latestObservation.observedFrom, true)} — {formatEt(latestObservation.observedThrough, true)}
                </dd>
                <p className="mt-1 text-[10px] text-text-4">券商未提供 as-of 时，不用本地完成时间冒充券商时间。</p>
              </div>
            </dl>
            <ObservationDetails observation={latestObservation} />
          </>
        ) : (
          !latestLoading && latest?.enabled && latest.configured && (
            <p className="text-body-sm text-text-3">尚未保存当前仓位证据。可以先执行一次只读检查，核对后再确认。</p>
          )
        )}

        {confirmedMessage && (
          <InlineAlert variant="success" title="当前仓位证据已确认" message={confirmedMessage} />
        )}

        {previewError && <ApiErrorAlert error={previewError} />}

        {preview && (
          <section className="space-y-3 rounded-ds-md border border-subtle bg-bg-2 p-4" aria-label="当前仓位检查预览">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-label uppercase tracking-label text-text-3">只读预览 · 尚未写入证据</div>
                <h3 className="mt-1 text-body font-semibold text-text-1">当前仓位检查预览</h3>
                <p className="mt-1 text-caption text-text-3">
                  采集区间（ET）{formatEt(preview.observation.observedFrom, true)} — {formatEt(preview.observation.observedThrough, true)}
                </p>
              </div>
              <span className={`rounded-full border px-2.5 py-1 text-caption ${preview.observation.stable ? 'border-up-strong/25 bg-up-subtle text-up-strong' : 'border-warn-strong/30 bg-warn-subtle text-warn-strong'}`}>
                {preview.observation.stable ? '两次读取稳定' : '两次读取不稳定'}
              </span>
            </div>

            {isFailedOrIncomplete(preview.observation) ? (
              <InlineAlert
                variant="danger"
                title="读取失败或不完整，不是空仓"
                message="只有两次读取均完成、边界稳定且验证通过的零持仓结果，才能标记为完整空仓。当前结果不能确认。"
              />
            ) : isCompleteEmpty(preview.observation) ? (
              <InlineAlert
                variant="success"
                title="完整空仓快照候选"
                message="两次读取均完成且稳定，没有有效期权持仓行；过滤掉的零数量缓存行不计入持仓。"
              />
            ) : (
              <InlineAlert
                variant="info"
                title={`读取到 ${preview.observation.positionCount.toLocaleString()} 行有效期权持仓`}
                message={`合计 ${decimalDisplay(preview.observation.totalContracts)} 张；确认前请核对方向、数量与合约乘数。`}
              />
            )}

            <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3"><dt className="text-caption text-text-3">读取完整</dt><dd className="mt-1 text-body-sm text-text-1">{preview.observation.retrievalComplete ? '是' : '否'}</dd></div>
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3"><dt className="text-caption text-text-3">快照完整</dt><dd className="mt-1 text-body-sm text-text-1">{preview.observation.positionSnapshotComplete ? '是' : '否'}</dd></div>
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3"><dt className="text-caption text-text-3">持仓行 / 总张数</dt><dd className="mt-1 font-mono text-body-sm text-text-1">{preview.observation.positionCount} / {decimalDisplay(preview.observation.totalContracts)}</dd></div>
              <div className="rounded-ds-md border border-subtle bg-bg-1 p-3"><dt className="text-caption text-text-3">预览有效至（ET）</dt><dd className="mt-1 font-mono text-mono-xs text-text-1">{formatEt(preview.expiresAt, true)}</dd></div>
            </dl>

            {preview.warnings.length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-caption text-warn-strong" aria-label="当前仓位检查警告">
                {preview.warnings.map((warning) => <li key={warning}>{warningLabel(warning)}</li>)}
              </ul>
            )}

            {preview.blockingReasons.length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-caption text-down-strong" aria-label="当前仓位确认阻断原因">
                {preview.blockingReasons.map((reason) => <li key={reason}>{warningLabel(reason)}</li>)}
              </ul>
            )}

            <ObservationDetails observation={preview.observation} defaultOpen />

            {!latest?.continuityReady && (
              <InlineAlert
                variant="warning"
                title="可以检查，但暂不能确认"
                message="请先在上方「每日刷新」确认一次 OpenD 刷新，建立同一账户的连续性锚点。"
                action={onOpenEvidence ? (
                  <button type="button" className="btn-ghost" onClick={onOpenEvidence}>前往每日刷新</button>
                ) : undefined}
              />
            )}

            <label className="flex items-start gap-3 rounded-ds-md border border-subtle bg-bg-1 p-3 text-body-sm text-text-2">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
                checked={acknowledgedFutureOnly}
                disabled={!preview.confirmAllowed || !preview.observation.futureAnchorCandidate || !latest?.continuityReady || Boolean(snapshotIdentityIssue)}
                onChange={(event) => setAcknowledgedFutureOnly(event.target.checked)}
              />
              <span>
                <strong className="text-text-1">仅作为面向未来的当前仓位证据，不能倒推历史期初。</strong>
                {' '}确认只会追加只读证据，不会修改 Moomoo 或提交订单。
              </span>
            </label>

            <div className="flex flex-wrap items-center gap-3">
              <button type="button" className="btn-primary" disabled={!canConfirm} onClick={() => void handleConfirm()}>
                {confirming ? '确认中…' : '确认并保存当前仓位证据'}
              </button>
              {!preview.confirmAllowed && <span className="text-caption text-down-strong">服务器未允许确认，请先处理阻断原因。</span>}
              {preview.confirmAllowed && !preview.observation.futureAnchorCandidate && <span className="text-caption text-warn-strong">当前读取不能作为未来连续性锚点。</span>}
              {snapshotIdentityIssue && <span className="text-caption text-down-strong">状态尚未完成一致性核对，请重新核对后再确认。</span>}
              {preview.confirmAllowed && preview.observation.futureAnchorCandidate && latest?.continuityReady && !snapshotIdentityIssue && !acknowledgedFutureOnly && <span className="text-caption text-warn-strong">接受用途边界后才能确认。</span>}
            </div>
          </section>
        )}
      </div>
    </section>
  );
};

export default CurrentPositionsSnapshotCard;

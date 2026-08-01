import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import {
  confirmJournalRefresh,
  confirmJournalOpenApiImport,
  fetchJournalRefreshStatus,
  fetchLedgerDataHealth,
  importJournalLedgerCsv,
  planJournalOpenApiImport,
  previewJournalRefresh,
  previewJournalCsv,
} from '../../api/journal';
import { parseApiError, type ParsedApiError } from '../../api/error';
import { ApiErrorAlert } from '../common/ApiErrorAlert';
import type {
  JournalRefreshFreshnessState,
  JournalRefreshStatus,
  LedgerDataHealth,
  MoomooJournalRefreshConfirmResponse,
  MoomooJournalRefreshPreview,
  MoomooOpenApiImportPlan,
  MoomooStatementPreview,
} from '../../types/journal';

const LEVEL_LABEL: Record<MoomooStatementPreview['analysisLevel'], string> = {
  exact: '逐笔证据完整',
  partial: '部分历史仅有汇总成交',
  blocked: '数据暂不可分析',
};

const LEVEL_STYLE: Record<MoomooStatementPreview['analysisLevel'], string> = {
  exact: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  partial: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  blocked: 'border-red-500/30 bg-red-500/10 text-red-300',
};

const REFRESH_STATE: Record<JournalRefreshFreshnessState, {
  label: string;
  summary: string;
  tone: string;
}> = {
  never_synced: {
    label: '尚未发布证据',
    summary: '先从 Moomoo 做一次只读检查。',
    tone: 'border-warn-strong/30 bg-warn-subtle text-warn-strong',
  },
  stale: {
    label: '券商查询水位已过期',
    summary: '需要重新检查，不能用最近一笔成交时间代替同步水位。',
    tone: 'border-warn-strong/30 bg-warn-subtle text-warn-strong',
  },
  evidence_blocked: {
    label: '证据存在阻断',
    summary: '当前证据没有通过发布条件，请重新检查差异与阻断原因。',
    tone: 'border-down-strong/30 bg-down-subtle text-down-strong',
  },
  evidence_current: {
    label: '证据已发布',
    summary: '券商证据已更新，下一步是生成新的仓位复盘构建。',
    tone: 'border-accent-subtle-border bg-accent-subtle-bg text-accent',
  },
  build_ready: {
    label: '复盘构建待启用',
    summary: '最新构建已就绪，需在仓位复盘页明确选择或启用。',
    tone: 'border-accent-subtle-border bg-accent-subtle-bg text-accent',
  },
  current: {
    label: '当前版本已同步',
    summary: '证据、构建和默认复盘构建处于同一已发布水位。',
    tone: 'border-up-strong/30 bg-up-subtle text-up-strong',
  },
};

function formatDateTime(
  value?: string | null,
  timeZone = 'America/New_York',
): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '时间无效';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone,
  }).format(parsed);
}

function compactHash(value?: string | null): string {
  if (!value) return '—';
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}

function refreshWarningLabel(value: string): string {
  const comboMatch = value.match(/^combo_order_requires_group_projection=(\d+)$/);
  if (comboMatch) {
    return `这份旧版预览检测到 ${Number(comboMatch[1]).toLocaleString()} 笔组合期权父单，但未包含执行组投影；请重新执行只读检查。`;
  }
  const skewMatch = value.match(/^broker_fill_precedes_order_create_within_1s=(\d+)$/);
  if (skewMatch) {
    return `${Number(skewMatch[1]).toLocaleString()} 笔成交存在不超过 1 秒的券商跨记录时间反序；原始时间已保留并带审计标记。`;
  }
  const reconciliationMatch = value.match(/^(filled_orders_without_fills|fills_without_orders|filled_quantity_mismatches|fill_code_mismatches|fill_side_mismatches|fill_average_price_mismatches|filled_orders_without_fees)=(\d+)$/);
  if (reconciliationMatch) {
    const count = Number(reconciliationMatch[2]).toLocaleString();
    const labels: Record<string, string> = {
      filled_orders_without_fills: `${count} 笔已成交父单尚无逐笔成交明细；无法证明执行事实。`,
      fills_without_orders: `${count} 组逐笔成交找不到对应父单；无法建立完整券商证据链。`,
      filled_quantity_mismatches: `${count} 笔订单的父单成交数量与逐笔成交合计不一致。`,
      fill_code_mismatches: `${count} 笔订单的成交标的与父单或声明组合腿不一致。`,
      fill_side_mismatches: `${count} 笔订单的成交方向与父单或声明组合腿不一致。`,
      fill_average_price_mismatches: `${count} 笔普通单腿订单的券商均价与逐笔成交加权均价不一致。`,
      filled_orders_without_fees: `${count} 笔已成交订单尚未取得券商费用明细；为避免净收益失真，本批保持阻断，可稍后重新检查。`,
    };
    return labels[reconciliationMatch[1]];
  }
  return value;
}

function refreshIssueLabel(issue: MoomooOpenApiImportPlan['issues'][number]): string {
  if (issue.code === 'combo_order_requires_group_projection') {
    return '这是旧版计划留下的兼容阻断；请重新检查以生成“组合执行组 → 稳定腿”证据，逐腿成交不会冒充普通单父单。';
  }
  if (issue.code === 'combo_csv_overlap_unprovable') {
    return '组合期权单落在 CSV 重叠区；当前 CSV 没有足够的组合父标识，无法安全证明多条腿属于同一父单。';
  }
  if (issue.code === 'combo_outside_csv_baseline_unprovable') {
    return '组合期权单早于当前 CSV 基线；现有账单无法证明其跨来源身份，本批证据不会发布。';
  }
  if (issue.code === 'combo_definition_capability_unproved') {
    return '这份旧证据没有证明券商响应包含组合策略与腿定义字段；为避免把价差单误当单腿，本批证据不会发布。';
  }
  if (issue.code === 'contract_multiplier_unproved') {
    return '部分期权成交尚无可追溯合约乘数；在取得合约规格证据前不能计算可信现金流。';
  }
  if (issue.code === 'execution_group_partial_semantics_unproved') {
    return '组合执行组尚未达到 FILLED_ALL 终态；当前版本不会推断部分成交、改单或撤单后的多腿经济含义。';
  }
  if (issue.code === 'execution_group_package_quantity_incomplete') {
    return '组合父单的已成交组数与下单组数不一致；无法证明整组执行完成。';
  }
  if (issue.code === 'execution_group_fee_missing') {
    return '组合执行组缺少券商组级费用证据；不会把未知费用估算后分摊到腿。';
  }
  if (issue.code === 'execution_group_fill_unassigned') {
    return '至少一条组合成交无法唯一映射到声明腿；为避免错误归因，本批证据不会发布。';
  }
  if (issue.code === 'execution_group_leg_quantity_mismatch') {
    return '组合腿成交量不等于“成交组数 × 声明腿比例”；当前无法证明腿数量守恒。';
  }
  if (issue.code === 'execution_group_contract_multiplier_unproved') {
    return '至少一条已成交期权腿缺少冻结的券商合约乘数；无法计算可信现金流。';
  }
  return issue.message;
}

function sourceSelectionLabel(value: JournalRefreshStatus['activeSelectionSource']): string {
  if (value === 'activation') return '已明确启用';
  if (value === 'csv_fallback') return 'CSV 基线';
  return '尚无版本';
}

function formatFeeTotals(values: Record<string, string>): string {
  const entries = Object.entries(values).sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) return '—';
  return entries
    .map(([currency, amount]) => new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
    }).format(Number(amount)))
    .join(' · ');
}

interface ExecutionGroupEvidenceCounts {
  ordinaryOrders: number;
  executionGroups: number;
  executionGroupLegs: number;
  executionGroupFillLinks: number;
  executionGroupFees: number;
  contractSpecs?: number;
  unclassifiedParents?: number;
}

const ExecutionGroupEvidenceSummary: React.FC<{
  title: string;
  counts: ExecutionGroupEvidenceCounts;
}> = ({ title, counts }) => {
  const cards: Array<[string, number]> = [
    ['普通单父记录', counts.ordinaryOrders],
    ['组合执行组', counts.executionGroups],
    ['组合腿', counts.executionGroupLegs],
    ['成交 → 腿链接', counts.executionGroupFillLinks],
    ['组合组级费用', counts.executionGroupFees],
  ];
  if (counts.contractSpecs != null) {
    cards.push(['合约规格快照', counts.contractSpecs]);
  }

  return (
    <section
      className="rounded-ds-md border border-subtle bg-bg-2 p-3"
      aria-label={title}
    >
      <h4 className="text-body-sm font-semibold text-text-1">{title}</h4>
      <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
        {cards.map(([label, value]) => (
          <div
            key={label}
            className="rounded-ds-sm border border-subtle bg-bg-1 px-3 py-2"
            aria-label={`${label}：${value}`}
          >
            <div className="text-caption text-text-3">{label}</div>
            <div className="mt-1 font-mono text-mono-sm font-semibold text-text-1">
              {value.toLocaleString()}
            </div>
          </div>
        ))}
      </div>
      {counts.executionGroups > 0 && (
        <p className="mt-2 text-caption text-text-3">
          组合费用只按券商原值保留在 execution group 层，不估算、不平均分摊到各腿；
          因此受影响腿的费用与净收益会保持不可用，也不会进入净收益排行。
        </p>
      )}
      {(counts.unclassifiedParents ?? 0) > 0 && (
        <p className="mt-2 text-caption text-down-strong">
          另有 {(counts.unclassifiedParents ?? 0).toLocaleString()} 条父记录未能证明是普通单或组合执行组，当前计划应保持阻断。
        </p>
      )}
    </section>
  );
};

function reconciliationLabel(health: LedgerDataHealth): string {
  if (health.reconciliationStatus === 'passed') {
    if (health.reconciliationScope === 'full_batch') return '记录范围：全批次';
    if (health.reconciliationScope === 'partial_window') {
      return `部分窗口已通过（${health.reconciledOrderObservations.toLocaleString()} / ${health.orderObservations.toLocaleString()} 笔订单）`;
    }
    return '已通过（覆盖范围未知）';
  }
  if (health.reconciliationStatus === 'not_run') return '未运行';
  if (health.reconciliationStatus === 'failed') return '未通过';
  return health.reconciliationStatus || '未知';
}

export const JournalImport: React.FC<{ onImported?: () => void }> = ({ onImported }) => {
  const [refreshStatus, setRefreshStatus] = useState<JournalRefreshStatus | null>(null);
  const [refreshStatusLoading, setRefreshStatusLoading] = useState(true);
  const [refreshError, setRefreshError] = useState<ParsedApiError | null>(null);
  const [refreshPreview, setRefreshPreview] = useState<MoomooJournalRefreshPreview | null>(null);
  const [refreshResult, setRefreshResult] = useState<MoomooJournalRefreshConfirmResponse | null>(null);
  const [refreshScanning, setRefreshScanning] = useState(false);
  const [refreshConfirming, setRefreshConfirming] = useState(false);
  const [refreshAcknowledged, setRefreshAcknowledged] = useState(false);
  const [overlapDays, setOverlapDays] = useState(7);
  const [sourceMode, setSourceMode] = useState<'csv' | 'openapi'>('csv');
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<MoomooStatementPreview | null>(null);
  const [openApiPlan, setOpenApiPlan] = useState<MoomooOpenApiImportPlan | null>(null);
  const [partialWindowAcknowledged, setPartialWindowAcknowledged] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [health, setHealth] = useState<LedgerDataHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  const loadRefreshStatus = useCallback(async () => {
    setRefreshStatusLoading(true);
    try {
      const value = await fetchJournalRefreshStatus();
      setRefreshStatus(value);
      setRefreshError(null);
      return value;
    } catch (reason) {
      setRefreshError(parseApiError(reason));
      return null;
    } finally {
      setRefreshStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchJournalRefreshStatus()
      .then((value) => {
        if (active) {
          setRefreshStatus(value);
          setRefreshError(null);
        }
      })
      .catch((reason) => {
        if (active) setRefreshError(parseApiError(reason));
      })
      .finally(() => {
        if (active) setRefreshStatusLoading(false);
      });
    void fetchLedgerDataHealth()
      .then((value) => {
        if (active) setHealth(value);
      })
      .catch(() => {
        // Preview remains usable even when the optional health read fails.
      });
    return () => {
      active = false;
    };
  }, []);

  const handleRefreshPreview = async () => {
    if (
      refreshScanning
      || refreshConfirming
      || !refreshStatus?.refreshEnabled
      || !refreshStatus.refreshConfigured
    ) return;
    setRefreshScanning(true);
    setRefreshError(null);
    setRefreshPreview(null);
    setRefreshResult(null);
    setRefreshAcknowledged(false);
    try {
      const response = await previewJournalRefresh(overlapDays);
      if (response.evidenceWritten || response.tradingActionPerformed) {
        throw new Error('只读检查返回了不符合安全契约的结果，已停止。');
      }
      setRefreshPreview(response);
      await loadRefreshStatus();
    } catch (reason) {
      setRefreshError(parseApiError(reason));
    } finally {
      setRefreshScanning(false);
    }
  };

  const handleRefreshConfirm = async () => {
    if (!refreshPreview || refreshConfirming || !refreshPreview.plan.confirmAllowed) return;
    const requiresPartialAcknowledgement = !refreshPreview.plan.scopeIsFullBatch;
    if (requiresPartialAcknowledgement && !refreshAcknowledged) return;
    setRefreshConfirming(true);
    setRefreshError(null);
    setRefreshResult(null);
    try {
      const response = await confirmJournalRefresh(
        refreshPreview.artifactId,
        refreshPreview.plan.previewKey,
        requiresPartialAcknowledgement && refreshAcknowledged,
      );
      if (response.tradingActionPerformed || response.imported.tradingActionPerformed) {
        throw new Error('证据发布返回了不符合只读契约的结果，已停止后续刷新。');
      }
      setRefreshResult(response);
      const [, nextHealth] = await Promise.all([
        loadRefreshStatus(),
        fetchLedgerDataHealth().catch(() => null),
      ]);
      if (nextHealth) setHealth(nextHealth);
      onImported?.();
    } catch (reason) {
      setRefreshError(parseApiError(reason));
    } finally {
      setRefreshConfirming(false);
    }
  };

  const handleCsvChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setSelectedFile(file);
    setOpenApiPlan(null);
    setPartialWindowAcknowledged(false);
    setError(null);
    setPreview(null);
    setResult(null);
    setPreviewing(true);
    try {
      setPreview(await previewJournalCsv(file));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setPreviewing(false);
    }
    // Reset the input so the same file can be re-selected.
    event.target.value = '';
  };

  const handleOpenApiChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setSelectedFile(file);
    setPreview(null);
    setError(null);
    setOpenApiPlan(null);
    setPartialWindowAcknowledged(false);
    setResult(null);
    setPreviewing(true);
    try {
      setOpenApiPlan(await planJournalOpenApiImport(file));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setPreviewing(false);
    }
    event.target.value = '';
  };

  const handleOpenApiConfirm = async () => {
    const requiresAcknowledgement = Boolean(openApiPlan && !openApiPlan.scopeIsFullBatch);
    if (
      !selectedFile
      || !openApiPlan
      || !openApiPlan.confirmAllowed
      || (requiresAcknowledgement && !partialWindowAcknowledged)
    ) return;
    setSaving(true);
    setError(null);
    setResult(null);
    try {
      const response = await confirmJournalOpenApiImport(
        selectedFile,
        openApiPlan.previewKey,
        requiresAcknowledgement && partialWindowAcknowledged,
      );
      if (response.duplicate) {
        setResult(
          `该证据批次已经存在，本次新增 0 条；canonical hash：${response.canonicalSetSha256}。当前事实集含 ${response.canonical.orders.toLocaleString()} 笔普通单、${response.canonical.executionGroups.toLocaleString()} 个组合执行组和 ${response.canonical.executionGroupLegs.toLocaleString()} 条组合腿。未触发交易或 Episode 重建。`,
        );
      } else {
        setResult(
          `已向本地可信账本追加 ${response.appended.orders.toLocaleString()} 条父记录（普通单 ${response.appended.ordinaryOrders.toLocaleString()} / 组合执行组 ${response.appended.executionGroups.toLocaleString()}）、${response.appended.fills.toLocaleString()} 条逐笔成交和 ${response.appended.fees.toLocaleString()} 条费用证据；组合部分含 ${response.appended.executionGroupLegs.toLocaleString()} 条腿、${response.appended.executionGroupFillLinks.toLocaleString()} 条成交链接和 ${response.appended.executionGroupFees.toLocaleString()} 条组级费用。Canonical 为 ${response.canonical.orders.toLocaleString()} 笔普通单 / ${response.canonical.executionGroups.toLocaleString()} 个组合执行组 / ${response.canonical.executionGroupLegs.toLocaleString()} 条组合腿。组合费用未分摊到腿；未触发交易或 Episode 重建。`,
        );
      }
      const [, nextHealth] = await Promise.all([
        loadRefreshStatus(),
        fetchLedgerDataHealth().catch(() => null),
      ]);
      if (nextHealth) setHealth(nextHealth);
      onImported?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    if (!selectedFile || !preview || preview.analysisLevel === 'blocked') return;
    setSaving(true);
    setError(null);
    setResult(null);
    try {
      const response = await importJournalLedgerCsv(
        selectedFile,
        preview.analysisLevel === 'partial',
      );
      setResult(
        response.duplicate
          ? '这份证据批次已经存在，没有重复写入。'
          : `已保存 ${response.orderObservations.toLocaleString()} 条订单与 ${response.fillObservations.toLocaleString()} 条逐笔成交；旧 Journal 未改动。`,
      );
      setHealth(await fetchLedgerDataHealth());
      await loadRefreshStatus();
      onImported?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const refreshState = refreshStatus ? REFRESH_STATE[refreshStatus.freshnessState] : null;
  const refreshConfigured = Boolean(
    refreshStatus?.refreshEnabled && refreshStatus.refreshConfigured,
  );
  const refreshPlan = refreshPreview?.plan;
  const requiresPartialAcknowledgement = Boolean(
    refreshPlan?.confirmAllowed && !refreshPlan.scopeIsFullBatch,
  );
  const refreshCoverageEvaluated = Boolean(
    refreshPlan && refreshPlan.coverage.scope !== 'not_evaluated_combo_blocked',
  );
  const refreshCanConfirm = Boolean(
    refreshPlan?.confirmAllowed
    && (!requiresPartialAcknowledgement || refreshAcknowledged),
  );

  return (
    <div className="space-y-4">
      <section className="card-base overflow-hidden" aria-label="OpenD 只读刷新">
        <div className="border-b border-subtle bg-bg-2/60 px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-label uppercase tracking-label text-text-3">Moomoo · Read only</div>
              <h2 className="mt-1 text-h2 text-text-1">OpenD 只读刷新</h2>
              <p className="mt-1 max-w-3xl text-body-sm text-text-3">
                从已绑定账户读取订单、逐笔成交与费用，先生成零证据写入预览；只有你明确确认后才会发布到本地可信账本。不会解锁交易或下单。
              </p>
            </div>
            {refreshState && (
              <span className={`rounded-full border px-2.5 py-1 text-caption font-medium ${refreshState.tone}`}>
                {refreshState.label}
              </span>
            )}
          </div>
        </div>

        <div className="space-y-4 p-5">
          {refreshStatusLoading && !refreshStatus && (
            <p className="text-body-sm text-text-3" role="status">正在读取刷新状态…</p>
          )}

          {refreshError && (
            <ApiErrorAlert
              error={refreshError}
              actionLabel="重新读取状态"
              onAction={() => void loadRefreshStatus()}
            />
          )}

          {refreshStatus && refreshState && (
            <>
              <div className={`rounded-ds-md border px-4 py-3 ${refreshState.tone}`} role="status">
                <p className="text-body-sm font-semibold">{refreshState.summary}</p>
                <p className="mt-1 text-caption opacity-85">
                  系统期望查询完整至 {formatDateTime(refreshStatus.expectedCompleteThrough)} ET
                  {refreshStatus.pendingStage !== 'none' ? ` · 下一步：${{
                    refresh: '重新检查',
                    confirm: '确认发布证据',
                    build: '生成仓位复盘',
                    activate: '启用复盘版本',
                    none: '无需操作',
                  }[refreshStatus.pendingStage]}` : ''}
                </p>
              </div>

              <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Journal 数据水位">
                <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                  <dt className="text-caption text-text-3">券商查询完整至</dt>
                  <dd className="mt-1 font-mono text-mono-sm text-text-1">
                    {formatDateTime(refreshStatus.brokerQueriedThrough)} ET
                  </dd>
                  <p className="mt-1 text-caption text-text-3">判断是否同步的正式水位</p>
                </div>
                <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                  <dt className="text-caption text-text-3">最新成交</dt>
                  <dd className="mt-1 font-mono text-mono-sm text-text-1">
                    {formatDateTime(refreshStatus.latestFillAt)} ET
                  </dd>
                  <p className="mt-1 text-caption text-text-3">仅表示交易活动，不代表查询完整度</p>
                </div>
                <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                  <dt className="text-caption text-text-3">证据发布完整至</dt>
                  <dd className="mt-1 font-mono text-mono-sm text-text-1">
                    {formatDateTime(refreshStatus.evidencePublishedThrough)} ET
                  </dd>
                  <p className="mt-1 text-caption text-text-3">
                    发布记录 {formatDateTime(refreshStatus.publicationRecordedAt, 'Asia/Shanghai')} 中国时间
                  </p>
                </div>
                <div className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                  <dt className="text-caption text-text-3">默认复盘构建</dt>
                  <dd className="mt-1 font-mono text-mono-sm text-text-1">
                    {refreshStatus.activeBuildId != null ? `构建 #${refreshStatus.activeBuildId}` : '—'}
                  </dd>
                  <p className="mt-1 text-caption text-text-3">
                    {sourceSelectionLabel(refreshStatus.activeSelectionSource)} · 来源至 {formatDateTime(refreshStatus.activeSourceThrough)} ET
                  </p>
                </div>
              </dl>

              {(refreshStatus.latestCanonicalSetId != null || refreshStatus.latestCanonicalBuildId != null) && (
                <p className="text-caption text-text-3">
                  最新可信事实集 {refreshStatus.latestCanonicalSetId != null ? `#${refreshStatus.latestCanonicalSetId}` : '—'}
                  {' '}· 来源至 {formatDateTime(refreshStatus.latestCanonicalSourceThrough)} ET
                  {' '}· 最新构建 {refreshStatus.latestCanonicalBuildId != null ? `#${refreshStatus.latestCanonicalBuildId}` : '—'}
                  {refreshStatus.latestCanonicalSetSha256
                    ? ` · 指纹 ${compactHash(refreshStatus.latestCanonicalSetSha256)}`
                    : ''}
                </p>
              )}
            </>
          )}

          {refreshStatus && !refreshConfigured && (
            <div className="rounded-ds-md border border-warn-strong/30 bg-warn-subtle px-4 py-3 text-body-sm text-warn-strong" role="alert">
              <p className="font-semibold">OpenD 只读刷新尚未就绪</p>
              <p className="mt-1 text-caption opacity-90">
                {refreshStatus.refreshEnabled
                  ? '服务器尚未完成 LIVE 只读账户绑定或 OpenD 配置；配置完成前不会发起账户查询。'
                  : '服务器尚未启用 Journal 只读刷新；仍可在下方使用手动文件导入。'}
              </p>
            </div>
          )}

          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-40 text-caption text-text-3">
              与既有证据重叠核对
              <select
                className="input-base mt-1 w-full"
                aria-label="重叠核对天数"
                value={overlapDays}
                onChange={(event) => setOverlapDays(Number(event.target.value))}
                disabled={refreshScanning || refreshConfirming}
              >
                <option value={3}>最近 3 天</option>
                <option value={7}>最近 7 天</option>
                <option value={14}>最近 14 天</option>
                <option value={30}>最近 30 天</option>
              </select>
            </label>
            <button
              type="button"
              className="btn-primary"
              onClick={() => void handleRefreshPreview()}
              disabled={!refreshConfigured || refreshScanning || refreshConfirming}
            >
              {refreshScanning ? '正在只读检查…' : '检查上一完整交易日'}
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => void loadRefreshStatus()}
              disabled={refreshStatusLoading || refreshScanning || refreshConfirming}
            >
              {refreshStatusLoading ? '读取中…' : '刷新状态'}
            </button>
            <p className="max-w-xl text-caption text-text-3">
              扫描最多等待约 3 分钟，只查询最近一个已收盘的美股交易日，不混入今天盘中尚未结算的成交。此操作只冻结一份待确认预览，不发布证据，也不执行交易。
            </p>
          </div>

          {refreshPreview && refreshPlan && (
            <section className="rounded-ds-md border border-subtle bg-bg-1" aria-label="Moomoo 刷新预览">
              <div className="border-b border-subtle px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="text-body-sm font-semibold text-text-1">本次只读检查</h3>
                    <p className="mt-1 text-caption text-text-3">
                      券商查询完整至 {formatDateTime(refreshPreview.source.brokerQueriedThrough)} ET ·
                      最新成交 {formatDateTime(refreshPreview.source.latestFillAt)} ET ·
                      预览有效至 {formatDateTime(refreshPreview.expiresAt, 'Asia/Shanghai')} 中国时间
                    </p>
                  </div>
                  <span className="rounded-full border border-up-strong/25 bg-up-subtle px-2.5 py-1 text-caption text-up-strong">
                    零证据写入 · 零交易动作
                  </span>
                </div>
              </div>

              <div className="space-y-4 p-4">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="rounded-ds-sm border border-subtle bg-bg-2 p-3">
                    <div className="text-caption text-text-3">读取结果</div>
                    <div className="mt-1 text-body-sm font-semibold text-text-1">
                      {refreshPreview.source.retrievalComplete && refreshPreview.source.coverageComplete
                        ? '读取与覆盖完整'
                        : '读取或覆盖不完整'}
                    </div>
                  </div>
                  <div className="rounded-ds-sm border border-subtle bg-bg-2 p-3">
                    <div className="text-caption text-text-3">订单 / 成交 / 费用</div>
                    <div className="mt-1 font-mono text-mono-sm text-text-1">
                      {refreshPreview.source.orderObservations.toLocaleString()} /{' '}
                      {refreshPreview.source.fillObservations.toLocaleString()} /{' '}
                      {refreshPreview.source.feeObservations.toLocaleString()}
                    </div>
                  </div>
                  <div className="rounded-ds-sm border border-subtle bg-bg-2 p-3">
                    <div className="text-caption text-text-3">新增尾部订单</div>
                    <div className="mt-1 font-mono text-mono-sm text-text-1">
                      {refreshCoverageEvaluated
                        ? refreshPlan.coverage.incrementalApiOnlyOrders.toLocaleString()
                        : '—'}
                    </div>
                    <p className="mt-1 text-caption text-text-3">
                      {refreshCoverageEvaluated ? '既有证据截止点之后' : '组合单阻断前未执行跨源核对'}
                    </p>
                  </div>
                  <div className="rounded-ds-sm border border-subtle bg-bg-2 p-3">
                    <div className="text-caption text-text-3">重叠窗口异常</div>
                    <div className={`mt-1 font-mono text-mono-sm ${
                      !refreshCoverageEvaluated
                        ? 'text-text-3'
                        : refreshPlan.coverage.overlapApiOnlyOrders > 0
                      || refreshPlan.coverage.csvOnlyInWindow > 0
                      || refreshPlan.coverage.ambiguousIdentityKeys > 0
                        ? 'text-down-strong'
                        : 'text-up-strong'
                    }`}>
                      {refreshCoverageEvaluated
                        ? (refreshPlan.coverage.overlapApiOnlyOrders
                          + refreshPlan.coverage.csvOnlyInWindow
                          + refreshPlan.coverage.ambiguousIdentityKeys).toLocaleString()
                        : '—'}
                    </div>
                    <p className="mt-1 text-caption text-text-3">
                      {refreshCoverageEvaluated ? 'API 独有 / CSV 独有 / 身份歧义' : '等待组合执行组支持'}
                    </p>
                  </div>
                </div>

                <ExecutionGroupEvidenceSummary
                  title="本次读取的执行证据结构"
                  counts={{
                    ordinaryOrders: refreshPlan.sourceScope.ordinaryOrderObservations,
                    executionGroups: refreshPlan.sourceScope.executionGroupObservations,
                    executionGroupLegs: refreshPlan.sourceScope.executionGroupLegObservations,
                    executionGroupFillLinks: refreshPlan.sourceScope.executionGroupFillLinks,
                    executionGroupFees: refreshPlan.sourceScope.executionGroupFeeObservations,
                    contractSpecs: refreshPlan.sourceScope.contractSpecObservations,
                    unclassifiedParents: refreshPlan.sourceScope.unclassifiedParentObservations,
                  }}
                />

                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="rounded-ds-md border border-subtle bg-bg-2 p-3 text-body-sm text-text-2">
                    <p className="font-semibold text-text-1">重叠核对</p>
                    <p className="mt-1 text-caption text-text-3">
                      {refreshCoverageEvaluated
                        ? (
                          <>匹配 {refreshPlan.coverage.matchedOrders.toLocaleString()} ·
                            重叠内仅 API {refreshPlan.coverage.overlapApiOnlyOrders.toLocaleString()} ·
                            仅 CSV {refreshPlan.coverage.csvOnlyInWindow.toLocaleString()} ·
                            身份歧义 {refreshPlan.coverage.ambiguousIdentityKeys.toLocaleString()}</>
                        )
                        : '尚未执行：先处理组合期权执行组，避免产生错误的一对一身份结论。'}
                    </p>
                  </div>
                  <div className="rounded-ds-md border border-subtle bg-bg-2 p-3 text-body-sm text-text-2">
                    <p className="font-semibold text-text-1">确认后发布</p>
                    <p className="mt-1 text-caption text-text-3">
                      普通单 {refreshPlan.writePlan.ordinaryOrderObservations.toLocaleString()} ·
                      组合执行组 {refreshPlan.writePlan.executionGroupObservations.toLocaleString()} ·
                      组合腿 {refreshPlan.writePlan.executionGroupLegObservations.toLocaleString()} ·
                      成交链接 {refreshPlan.writePlan.executionGroupFillLinks.toLocaleString()} ·
                      组级费用 {refreshPlan.writePlan.executionGroupFeeObservations.toLocaleString()}
                    </p>
                    <p className="mt-1 text-caption text-text-3">
                      Canonical {refreshPlan.canonicalImpact.canonicalOrders.toLocaleString()} 笔普通单 /
                      {' '}{refreshPlan.canonicalImpact.canonicalExecutionGroups.toLocaleString()} 个组合执行组 /
                      {' '}{refreshPlan.canonicalImpact.canonicalExecutionGroupLegs.toLocaleString()} 条组合腿
                    </p>
                  </div>
                </div>

                {(refreshPlan.warnings.length > 0 || refreshPlan.issues.length > 0) && (
                  <details className="rounded-ds-md border border-subtle bg-bg-2 px-3 py-2">
                    <summary className="cursor-pointer text-body-sm font-medium text-text-2">
                      查看证据提示与阻断原因（{refreshPlan.warnings.length + refreshPlan.issues.length}）
                    </summary>
                    <ul className="mt-2 space-y-1 text-caption text-text-3">
                      {refreshPlan.warnings.map((warning) => (
                        <li key={`refresh-${warning}`}>· {refreshWarningLabel(warning)}</li>
                      ))}
                      {refreshPlan.issues.map((issue, index) => (
                        <li key={`${issue.code}-${issue.entityKey}-${index}`}>
                          · {refreshIssueLabel(issue)}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}

                {requiresPartialAcknowledgement && (
                  <label className="flex items-start gap-3 rounded-ds-md border border-warn-strong/30 bg-warn-subtle p-3 text-body-sm text-text-2">
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 shrink-0"
                      checked={refreshAcknowledged}
                      onChange={(event) => setRefreshAcknowledged(event.target.checked)}
                    />
                    <span>
                      我确认本次 OpenD 查询只核对上述时间窗口；窗口外历史仍保留原证据口径，不把局部核对表述为全部历史已验证。
                    </span>
                  </label>
                )}

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => void handleRefreshConfirm()}
                    disabled={!refreshCanConfirm || refreshConfirming || refreshScanning}
                  >
                    {refreshConfirming ? '正在发布…' : '确认发布到可信账本'}
                  </button>
                  {!refreshPlan.confirmAllowed && (
                    <span className="text-caption text-down-strong">预览存在阻断，不能发布证据。</span>
                  )}
                  {requiresPartialAcknowledgement && !refreshAcknowledged && refreshPlan.confirmAllowed && (
                    <span className="text-caption text-warn-strong">确认局部窗口口径后才能发布。</span>
                  )}
                </div>
              </div>
            </section>
          )}

          {refreshResult && (
            <div className="rounded-ds-md border border-up-strong/30 bg-up-subtle px-4 py-3 text-body-sm text-up-strong" role="status">
              <p className="font-semibold">可信证据已发布</p>
              <p className="mt-1 text-caption opacity-90">
                证据发布完整至 {formatDateTime(refreshResult.publication.evidencePublishedThrough)} ET ·
                canonical 事实集 #{refreshResult.imported.canonicalSetId} ·
                新增普通单 {refreshResult.imported.appended.ordinaryOrders.toLocaleString()} / 组合执行组 {refreshResult.imported.appended.executionGroups.toLocaleString()} /
                {' '}{refreshResult.imported.appended.executionGroupLegs.toLocaleString()} 条组合腿 /
                {' '}{refreshResult.imported.appended.executionGroupFillLinks.toLocaleString()} 条成交链接 /
                {' '}{refreshResult.imported.appended.executionGroupFees.toLocaleString()} 条组级费用。
                下一步请进入“仓位复盘”明确生成新的不可变构建。
              </p>
              {refreshResult.imported.canonical.executionGroups > 0 && (
                <p className="mt-1 text-caption opacity-90">
                  组合费用只保留在执行组层，不会分摊到腿或写入腿级净收益。
                </p>
              )}
            </div>
          )}
        </div>
      </section>

      <details className="card-base overflow-hidden">
        <summary className="cursor-pointer px-5 py-4 text-body-sm font-semibold text-text-2">
          高级 / 首次导入：CSV 账单或只读 JSON
        </summary>
        <div className="border-t border-subtle p-4">
      <h3 className="text-lg font-semibold">Moomoo 数据检查</h3>
      <p className="mt-1 text-sm text-muted">
        先检查订单、逐笔成交和费用覆盖；此步骤只读，不会写入旧 Journal。
      </p>
      {health?.hasData && (
        <div className="mt-4 rounded-lg border border-border/60 bg-background/30 px-3 py-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-semibold">可信证据账本已就绪</span>
            <span className="text-xs text-muted">
              对账：{reconciliationLabel(health)}
            </span>
          </div>
          <p className="mt-2 text-xs text-muted">
            {health.orderObservations.toLocaleString()} 条订单 · {health.fillObservations.toLocaleString()} 条逐笔成交 ·
            完整度 {health.completenessScore ? `${(Number(health.completenessScore) * 100).toFixed(1)}%` : '—'} ·
            旧 Journal 未改动
          </p>
          {health.reconciliationScope === 'partial_window' && (
            <p className="mt-2 text-xs text-amber-200/90">
              API 对账窗口（ET）：{formatDateTime(health.reconciliationWindowStart)} – {formatDateTime(health.reconciliationWindowEnd)}；窗口外证据尚未经过 API 逐单核对。
            </p>
          )}
        </div>
      )}
      <div className="mt-4 flex flex-wrap gap-2" aria-label="数据来源">
        <button
          type="button"
          className={sourceMode === 'csv' ? 'btn-primary' : 'btn-secondary'}
          onClick={() => {
            setSourceMode('csv');
            setError(null);
            setResult(null);
            setFileName(null);
            setSelectedFile(null);
            setPreview(null);
            setOpenApiPlan(null);
            setPartialWindowAcknowledged(false);
          }}
        >
          CSV 账单
        </button>
        <button
          type="button"
          className={sourceMode === 'openapi' ? 'btn-primary' : 'btn-secondary'}
          onClick={() => {
            setSourceMode('openapi');
            setError(null);
            setResult(null);
            setFileName(null);
            setSelectedFile(null);
            setPreview(null);
            setOpenApiPlan(null);
            setPartialWindowAcknowledged(false);
          }}
        >
          OpenAPI 只读 JSON
        </button>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <label className="btn-primary cursor-pointer">
          {sourceMode === 'csv' ? (
            <input
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={handleCsvChange}
              disabled={previewing}
            />
          ) : (
            <input
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={handleOpenApiChange}
              disabled={previewing}
            />
          )}
          {previewing
            ? '检查中…'
            : sourceMode === 'csv'
              ? '选择 CSV'
              : '选择只读 API JSON'}
        </label>
        {fileName && <span className="text-sm text-muted">{fileName}</span>}
      </div>
      {sourceMode === 'csv' && preview && (
        <div className="mt-4 space-y-4" aria-live="polite">
          <div className={`rounded-lg border px-3 py-2 text-sm ${LEVEL_STYLE[preview.analysisLevel]}`}>
            <span className="font-semibold">{LEVEL_LABEL[preview.analysisLevel]}</span>
            <span className="ml-2 opacity-80">证据行写入：0（仅计划）</span>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {[
              ['订单', preview.ordersTotal.toLocaleString()],
              ['Filled', preview.filledOrders.toLocaleString()],
              ['逐笔 fill', preview.fillRecords.toLocaleString()],
              ['仅汇总订单', preview.aggregateOnlyFilledOrders.toLocaleString()],
              ['Filled 费用', `$${Number(preview.filledFeeTotal).toLocaleString(undefined, { minimumFractionDigits: 2 })}`],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-border/60 bg-background/30 px-3 py-2">
                <div className="text-xs text-muted">{label}</div>
                <div className="mt-1 font-mono text-base font-semibold">{value}</div>
              </div>
            ))}
          </div>

          <div className="grid gap-2 text-xs text-muted sm:grid-cols-2">
            <p>
              订单范围（ET）：{formatDateTime(preview.orderTimeRange.first)} – {formatDateTime(preview.orderTimeRange.last)}
            </p>
            <p>
              逐笔成交范围（ET）：{formatDateTime(preview.fillDetailTimeRange.first)} – {formatDateTime(preview.fillDetailTimeRange.last)}
            </p>
          </div>

          {preview.aggregateOnlyFilledOrders > 0 && (
            <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-sm text-amber-100/90">
              {preview.aggregateOnlyFilledOrders.toLocaleString()} 笔较早成交只有数量、均价和费用，
              可用于持仓与盈亏复盘，但不会用于精确进场时刻、滑点或 MFE/MAE。
            </p>
          )}

          {preview.analysisLevel !== 'blocked' && (
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="btn-primary"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? '保存中…' : '保存到可信证据账本'}
              </button>
              <p className="text-xs text-muted">
                追加写入且按文件哈希幂等；不会触发旧 FIFO 重建。
              </p>
            </div>
          )}
        </div>
      )}
      {sourceMode === 'openapi' && openApiPlan && (
        <div className="mt-4 space-y-4" aria-live="polite">
          <div className={`rounded-lg border px-3 py-2 text-sm ${openApiPlan.confirmAllowed && openApiPlan.canonicalImpact.analysisReady
            ? LEVEL_STYLE.exact
            : LEVEL_STYLE.blocked}`}>
            <span className="font-semibold">
              {openApiPlan.confirmAllowed && openApiPlan.canonicalImpact.analysisReady
                ? 'OpenAPI 只读证据计划可确认'
                : 'OpenAPI 证据计划暂不可确认'}
            </span>
            <span className="ml-2 opacity-80">证据行写入：0（仅计划）</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[
              ['API 订单 observations', openApiPlan.sourceScope.orderObservations.toLocaleString()],
              ['API 逐笔 fill', openApiPlan.sourceScope.fillObservations.toLocaleString()],
              ['API 费用 observations', openApiPlan.sourceScope.feeObservations.toLocaleString()],
              ['API 费用合计', formatFeeTotals(openApiPlan.sourceScope.feeTotalsByCurrency)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-border/60 bg-background/30 px-3 py-2">
                <div className="text-xs text-muted">{label}</div>
                <div className="mt-1 font-mono text-base font-semibold">{value}</div>
              </div>
            ))}
          </div>
          <ExecutionGroupEvidenceSummary
            title="API 执行证据结构"
            counts={{
              ordinaryOrders: openApiPlan.sourceScope.ordinaryOrderObservations,
              executionGroups: openApiPlan.sourceScope.executionGroupObservations,
              executionGroupLegs: openApiPlan.sourceScope.executionGroupLegObservations,
              executionGroupFillLinks: openApiPlan.sourceScope.executionGroupFillLinks,
              executionGroupFees: openApiPlan.sourceScope.executionGroupFeeObservations,
              contractSpecs: openApiPlan.sourceScope.contractSpecObservations,
              unclassifiedParents: openApiPlan.sourceScope.unclassifiedParentObservations,
            }}
          />
          <p className="text-xs text-muted">
            {openApiPlan.sourceScope.environment} · {openApiPlan.sourceScope.market} ·{' '}
            {openApiPlan.sourceScope.accountSelection} ·{' '}
            API 窗口（{openApiPlan.sourceScope.sourceTimezone}）：
            {formatDateTime(openApiPlan.sourceScope.windowStart, openApiPlan.sourceScope.sourceTimezone)} –{' '}
            {formatDateTime(openApiPlan.sourceScope.windowEnd, openApiPlan.sourceScope.sourceTimezone)}
          </p>
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-3 text-sm text-amber-100/90">
            <p className="font-semibold">
              局部覆盖：窗口内匹配 {openApiPlan.coverage.matchedOrders.toLocaleString()} /{' '}
              {openApiPlan.baseScope
                ? openApiPlan.baseScope.orderObservations.toLocaleString()
                : '—'} 笔 CSV 订单
            </p>
            <p className="mt-1 text-xs">
              窗口外 {openApiPlan.coverage.outsideUnverifiedOrders.toLocaleString()} 笔订单尚未经过 API 核验；
              此计划不代表全部历史已核验。
            </p>
            {(openApiPlan.coverage.csvOnlyInWindow > 0
              || openApiPlan.coverage.apiOnlyOrders > 0
              || openApiPlan.coverage.ambiguousIdentityKeys > 0) && (
              <p className="mt-1 text-xs">
                窗口差异：仅 CSV {openApiPlan.coverage.csvOnlyInWindow.toLocaleString()} ·
                仅 API {openApiPlan.coverage.apiOnlyOrders.toLocaleString()} ·
                身份歧义 {openApiPlan.coverage.ambiguousIdentityKeys.toLocaleString()}
              </p>
            )}
          </div>

          <div>
            <h4 className="text-sm font-semibold">确认后追加</h4>
            <div className="mt-2 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {[
                ['普通单证据', openApiPlan.writePlan.ordinaryOrderObservations],
                ['组合执行组', openApiPlan.writePlan.executionGroupObservations],
                ['组合腿', openApiPlan.writePlan.executionGroupLegObservations],
                ['成交 → 腿链接', openApiPlan.writePlan.executionGroupFillLinks],
                ['组合组级费用', openApiPlan.writePlan.executionGroupFeeObservations],
                ['逐笔成交证据', openApiPlan.writePlan.fillObservations],
                ['费用证据', openApiPlan.writePlan.feeObservations],
                ['订单身份链接', openApiPlan.writePlan.orderIdentityLinks],
                ['成交身份链接', openApiPlan.writePlan.dealIdentityLinks],
                ['fill-set 证明', openApiPlan.writePlan.fillSetAttestations],
                ['canonical sets', openApiPlan.writePlan.canonicalSets],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-border/60 bg-background/30 px-3 py-2">
                  <div className="text-xs text-muted">{label}</div>
                  <div className="mt-1 font-mono text-base font-semibold">
                    {Number(value).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-3 text-sm text-cyan-100/90">
            <p className="font-semibold">
              Canonical 结果：{openApiPlan.canonicalImpact.canonicalOrders.toLocaleString()} 笔普通单 ·{' '}
              {openApiPlan.canonicalImpact.canonicalFills.toLocaleString()} 条成交 ·{' '}
              {openApiPlan.canonicalImpact.canonicalExecutionGroups.toLocaleString()} 个组合执行组 ·{' '}
              {openApiPlan.canonicalImpact.canonicalExecutionGroupLegs.toLocaleString()} 条组合腿
            </p>
            <p className="mt-1 text-xs">
              Canonical 计算将 {openApiPlan.canonicalImpact.shadowedCsvFills.toLocaleString()} 条 CSV fill 标记为 shadow；
              {openApiPlan.canonicalImpact.shadowedAggregateOrders.toLocaleString()} 笔汇总订单保留为 shadow。阻断问题：
              {openApiPlan.canonicalImpact.blockingIssues.toLocaleString()}。
            </p>
            <p className="mt-1 break-all font-mono text-[11px] text-cyan-100/70">
              canonical hash: {openApiPlan.canonicalImpact.canonicalSetSha256}
            </p>
          </div>

          {(openApiPlan.warnings.length > 0 || openApiPlan.issues.length > 0) && (
            <div className="rounded-lg border border-border/60 bg-background/30 px-3 py-3 text-sm">
              <h4 className="font-semibold">计划提示</h4>
              <ul className="mt-2 space-y-1 text-xs text-muted">
                {openApiPlan.warnings.map((warning) => (
                  <li key={`warning-${warning}`}>· {warning}</li>
                ))}
                {openApiPlan.issues.map((issue, index) => (
                  <li key={`${issue.code}-${issue.entityKey}-${index}`}>
                    · [{issue.severity}] {issue.code}：{issue.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {!openApiPlan.scopeIsFullBatch && (
            <label className="flex items-start gap-3 rounded-lg border border-border/60 bg-background/30 px-3 py-3 text-sm">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4"
                checked={partialWindowAcknowledged}
                onChange={(event) => setPartialWindowAcknowledged(event.target.checked)}
              />
              <span>
                我已确认：此次只覆盖上述 OpenAPI 时间窗口，不代表全部历史已核验；保存仅向本地可信账本追加只读证据，
                不触发任何交易，也不会自动重建 Episode。
              </span>
            </label>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn-primary"
              onClick={handleOpenApiConfirm}
              disabled={
                saving
                || !openApiPlan.confirmAllowed
                || (!openApiPlan.scopeIsFullBatch && !partialWindowAcknowledged)
                || !selectedFile
              }
            >
              {saving ? '追加中…' : '确认追加只读证据'}
            </button>
            <p className="text-xs text-muted">
              append-only 且按证据哈希幂等；不会向 Moomoo 写入，也不会改旧 Journal。
            </p>
          </div>
        </div>
      )}
      {result && <p className="mt-3 rounded bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">{result}</p>}
      {error && <p className="mt-3 rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{error}</p>}
        </div>
      </details>
    </div>
  );
};

export default JournalImport;

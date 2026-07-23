import type React from 'react';
import { useEffect, useState } from 'react';
import {
  confirmJournalOpenApiImport,
  fetchLedgerDataHealth,
  importJournalLedgerCsv,
  planJournalOpenApiImport,
  previewJournalCsv,
} from '../../api/journal';
import type {
  LedgerDataHealth,
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

function formatDateTime(
  value?: string | null,
  timeZone = 'America/New_York',
): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone,
  }).format(new Date(value));
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

  useEffect(() => {
    let active = true;
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
    if (
      !selectedFile
      || !openApiPlan
      || !openApiPlan.confirmAllowed
      || !partialWindowAcknowledged
    ) return;
    setSaving(true);
    setError(null);
    setResult(null);
    try {
      const response = await confirmJournalOpenApiImport(
        selectedFile,
        openApiPlan.previewKey,
        partialWindowAcknowledged,
      );
      if (response.duplicate) {
        setResult(
          `该证据批次已经存在，本次新增 0 条；canonical hash：${response.canonicalSetSha256}。未触发交易或 Episode 重建。`,
        );
      } else {
        setResult(
          `已向本地可信账本追加 ${response.appended.orders.toLocaleString()} 条订单、${response.appended.fills.toLocaleString()} 条逐笔成交和 ${response.appended.fees.toLocaleString()} 条费用证据；canonical ${response.canonical.orders.toLocaleString()} 笔订单 / ${response.canonical.fills.toLocaleString()} 条成交。未触发交易或 Episode 重建。`,
        );
      }
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
      onImported?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card-base p-4">
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
                ['订单证据', openApiPlan.writePlan.orderObservations],
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
              Canonical 结果：{openApiPlan.canonicalImpact.canonicalOrders.toLocaleString()} 笔订单 ·{' '}
              {openApiPlan.canonicalImpact.canonicalFills.toLocaleString()} 条成交
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

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn-primary"
              onClick={handleOpenApiConfirm}
              disabled={
                saving
                || !openApiPlan.confirmAllowed
                || !partialWindowAcknowledged
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
  );
};

export default JournalImport;

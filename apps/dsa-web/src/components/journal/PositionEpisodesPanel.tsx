import type React from 'react';
import { useMemo, useState } from 'react';
import { ApiErrorAlert } from '../common/ApiErrorAlert';
import { Drawer } from '../common/Drawer';
import { InlineAlert } from '../common/InlineAlert';
import { Pagination } from '../common/Pagination';
import { type ParsedApiError } from '../../api/error';
import {
  decimalTone,
  formatDecimalDisplay,
  formatEt,
  formatHold,
} from './episodeFormat';
import type {
  CanonicalEpisodeBuildPlanResponse,
  EpisodeBuildResponse,
  PositionEpisodeDetailResponse,
  PositionEpisodeEvidenceItem,
  PositionEpisodeFilters,
  PositionEpisodeItem,
  PositionEpisodeListResponse,
} from '../../types/journal';

export interface PositionEpisodeController {
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
  onOpenBuildTools?: () => void;
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

export const PositionEpisodesPanel: React.FC<PositionEpisodesPanelProps> = ({
  filters,
  controller,
  onApplyFilters,
  onPageChange,
  onSelectBuild,
  onOpenReview,
  onOpenBuildTools,
}) => {
  const [draftSymbol, setDraftSymbol] = useState(filters.underlying ?? '');
  const [draftStatus, setDraftStatus] = useState(filters.lifecycleStatus ?? '');
  const [draftCompleteness, setDraftCompleteness] = useState(filters.completenessStatus ?? '');
  const [draftCaseFocus, setDraftCaseFocus] = useState(filters.caseFocus ?? '');
  const [draftReviewStatus, setDraftReviewStatus] = useState(filters.reviewStatus ?? '');
  const [selected, setSelected] = useState<PositionEpisodeItem | null>(null);

  const totalPages = Math.max(1, Math.ceil((controller.list?.total ?? 0) / (controller.list?.perPage ?? 50)));
  const selectedDetail = selected ? controller.detailById[selected.id] : undefined;
  const summary = controller.list?.summary;
  const build = controller.list?.build;
  const reconciliation = controller.list?.reconciliation;
  const headline = summary?.headlinePnl;
  const conditionalPnl = summary?.conditionalPnl;
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

  if (controller.loading && !controller.list) {
    return (
      <div className="card-base p-8 text-center text-body-sm text-text-3">加载仓位回合…</div>
    );
  }

  if (controller.list?.dataState === 'not_built') {
    return (
      <div className="space-y-4">
        {controller.error && <ApiErrorAlert error={controller.error} actionLabel="重试" onAction={controller.reload} />}
        <div className="card-base p-6">
          <div className="max-w-2xl">
            <div className="text-label uppercase tracking-label text-text-3">复盘工作台</div>
            <h2 className="mt-2 text-h1 text-text-1">尚未构建仓位回合</h2>
            <p className="mt-2 text-body-sm leading-relaxed text-text-3">
              复盘列表需要一份从证据生成的仓位回合构建。请先到「数据与构建」完成证据导入与构建，再回到这里逐笔复盘。
            </p>
            {onOpenBuildTools && (
              <button type="button" className="btn-primary mt-4" onClick={onOpenBuildTools}>
                前往数据与构建
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {filters.buildId != null && (
        <InlineAlert
          variant="info"
          title={`正在查看构建 #${filters.buildId}`}
          message="这是显式打开的对比视图；默认复盘构建没有改变。若要把它设为默认，请到「数据与构建」的构建管理区操作。"
          action={<button type="button" className="btn-ghost" onClick={() => onSelectBuild()}>回到默认复盘构建</button>}
        />
      )}
      {controller.error && <ApiErrorAlert error={controller.error} actionLabel="重试" onAction={controller.reload} />}
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

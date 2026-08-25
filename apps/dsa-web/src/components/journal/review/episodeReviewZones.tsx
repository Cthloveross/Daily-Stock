import { Compass, Scale } from 'lucide-react';
import type {
  PositionEpisodeDetailResponse,
  PositionEpisodeReviewAnnotation,
} from '../../../types/journal';
import type { EpisodeVerdictBlock } from '../../../types/journalReviewFlow';
import { QUADRANT_TONES } from './reviewHardLines';

/**
 * 单笔深潜的区①（决策时快照）与区④（机械反事实）＋四象限徽章。
 * 布局强制顺序（蓝图 17 §三(b)）：区① 默认展开 → 区② 折叠揭示 →
 * 区③ 双轨标签（四象限，「侥幸」标红）→ 区④ 只读反事实。
 */

function toEt(value?: string | null): string {
  if (!value) return '标缺';
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

function MissingBadge({ reason }: { reason: string }) {
  return (
    <span className="rounded-full border border-subtle bg-bg-2 px-2 py-0.5 text-caption text-text-3">
      标缺 · {reason}
    </span>
  );
}

export function QuadrantChip({
  quadrant,
  label,
}: {
  quadrant: string;
  label: string;
}) {
  const tone = QUADRANT_TONES[quadrant] ?? QUADRANT_TONES.not_judged;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-caption ${tone}`}
      data-testid="quadrant-chip"
      data-quadrant={quadrant}
    >
      {quadrant === 'undeserved_win' && <span aria-hidden>⚠</span>}
      {label}
    </span>
  );
}

export function DecisionSnapshotPanel({
  detail,
  verdict,
  annotation,
}: {
  detail: PositionEpisodeDetailResponse;
  verdict: EpisodeVerdictBlock | null;
  annotation: PositionEpisodeReviewAnnotation | null;
}) {
  const item = detail.item;
  const exitPlan = annotation?.invalidationPlan?.trim() ?? '';
  return (
    <section className="card-base overflow-hidden" aria-label="决策时快照" data-testid="decision-snapshot">
      <div className="border-b border-subtle px-4 py-3">
        <div className="flex items-center gap-2 text-label uppercase tracking-label text-text-3">
          <Compass size={14} /> 区① 决策时快照
        </div>
        <p className="mt-1 text-caption text-text-3">
          只列进场时刻可知的信息；结果（盈亏、K 线、成交路径）折叠在下方，先评过程再看结果。
        </p>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <div className="text-caption text-text-3">方向 / DTE</div>
          <div className="mt-1 font-mono text-mono-sm text-text-1">
            {item.direction === 'long' ? '多头' : item.direction === 'short' ? '空头' : item.direction}
            {' · '}
            {item.instrument.expiry ? `${item.instrument.expiry} 到期` : 'DTE 标缺'}
          </div>
        </div>
        <div>
          <div className="text-caption text-text-3">开仓时间 ET</div>
          <div className="mt-1 font-mono text-mono-sm text-text-1">{toEt(item.openedAt)}</div>
        </div>
        <div>
          <div className="text-caption text-text-3">车道判定（机械）</div>
          <div className="mt-1">
            {verdict ? (
              <span className={`rounded-full border px-2 py-0.5 text-caption ${
                verdict.verdict === 'violation'
                  ? 'border-down-strong/40 bg-down-subtle text-down-strong'
                  : verdict.verdict === 'compliant'
                    ? 'border-up-strong/30 bg-up-subtle text-up-strong'
                    : 'border-subtle bg-bg-2 text-text-3'
              }`}>
                {verdict.ruleId} · {verdict.verdict === 'compliant' ? '合规'
                  : verdict.verdict === 'violation' ? '违规'
                    : verdict.verdict === 'uncovered' ? '规则未覆盖' : '不可判定'}
              </span>
            ) : (
              <MissingBadge reason="判定读取中或不可得" />
            )}
          </div>
        </div>
        <div>
          <div className="text-caption text-text-3">在册出场计划（S3 预登记）</div>
          <div className="mt-1 text-body-sm text-text-1">
            {exitPlan ? (
              <>
                <span className="whitespace-pre-wrap">{exitPlan}</span>
                <span className="ml-2 font-mono text-mono-xs text-text-3">
                  登记于 {toEt(annotation?.createdAt)}
                </span>
              </>
            ) : (
              <MissingBadge reason="无在册出场计划" />
            )}
          </div>
        </div>
        <div>
          <div className="text-caption text-text-3">进场时 Regime / gate</div>
          <div className="mt-1"><MissingBadge reason="regime_score_at_entry 回填未到（E-4 / Phase C）" /></div>
        </div>
        <div>
          <div className="text-caption text-text-3">触发推送</div>
          <div className="mt-1"><MissingBadge reason="push ledger 未建（Phase B）" /></div>
        </div>
      </div>
    </section>
  );
}

export function EpisodeWhatIfPanel({
  verdict,
}: {
  verdict: EpisodeVerdictBlock | null;
}) {
  return (
    <section className="card-base overflow-hidden" aria-label="机械反事实" data-testid="what-if-panel">
      <div className="border-b border-subtle px-4 py-3">
        <div className="flex items-center gap-2 text-label uppercase tracking-label text-text-3">
          <Scale size={14} /> 区④ 机械反事实（只读）
        </div>
        <p className="mt-1 text-caption text-text-3">
          只有两个反事实，永不叠加展示为最优组合；移除式反事实假设无替代行为——
          这是对本人历史的记账，非预测。
        </p>
      </div>
      <div className="space-y-3 p-4">
        <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
          <div className="text-caption text-text-3">反事实 A · 仅合规车道</div>
          <p className="mt-1 text-body-sm text-text-1">
            {verdict ? verdict.counterfactualCompliantText : '判定读取中…'}
          </p>
        </div>
        <div className="rounded-ds-md border border-subtle bg-bg-2 p-3">
          <div className="text-caption text-text-3">反事实 B · gate 执行</div>
          <p className="mt-1 text-body-sm text-text-2">
            {verdict ? verdict.counterfactualGateReason : '判定读取中…'}
          </p>
        </div>
      </div>
    </section>
  );
}

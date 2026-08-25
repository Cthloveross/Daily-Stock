import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, CheckCircle2, Lock, Moon, Eye } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  fetchDailyReviewFlow,
  postDailyReviewFlow,
  postDailyReviewReveal,
} from '../api/journalReviewFlow';
import { parseApiError } from '../api/error';
import { ApiErrorAlert } from '../components/common/ApiErrorAlert';
import type { ParsedApiError } from '../api/error';
import type {
  DailyReviewFlowResponse,
  ProcessMetric,
  RevealSummary,
} from '../types/journalReviewFlow';
import { QuadrantChip } from '../components/journal/review/episodeReviewZones';

/**
 * 引导式日终复盘流（蓝图 17 §三(a)）：S0 检票 → S1 违规扫描 →
 * S2 过程打分 → S3 出场预登记 → S4 封卷（→ 自愿揭示）。
 *
 * 步进器而非面板堆；目标 ≤5 分钟。盲评是核心机制：密封并主动点击
 * 「揭示当日结果」之前，页面与 API 载荷都没有任何盈亏。每步可跳过但
 * 记标缺，绝不静默补默认值。判定全部来自服务端。
 */

type StepId = 's0' | 's1' | 's2' | 's3' | 's4';

const STEP_TITLES: Record<StepId, string> = {
  s0: 'S0 检票',
  s1: 'S1 违规扫描',
  s2: 'S2 过程打分',
  s3: 'S3 出场预登记',
  s4: 'S4 封卷',
};

const STEP_ORDER: StepId[] = ['s0', 's1', 's2', 's3', 's4'];

function toEtTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
}

function BasisBadge({ basis }: { basis: ProcessMetric['basis'] }) {
  if (basis === 'auto') {
    return <span className="rounded-full border border-up-strong/25 bg-up-subtle px-2 py-0.5 text-caption text-up-strong">自动</span>;
  }
  if (basis === 'manual') {
    return <span className="rounded-full border border-accent-subtle-border bg-accent-subtle-bg px-2 py-0.5 text-caption text-accent">手填</span>;
  }
  return <span className="rounded-full border border-subtle bg-bg-2 px-2 py-0.5 text-caption text-text-3">标缺</span>;
}

function verdictChip(verdict: string, ruleId: string) {
  const tone = verdict === 'violation'
    ? 'border-down-strong/40 bg-down-subtle text-down-strong'
    : verdict === 'compliant'
      ? 'border-up-strong/30 bg-up-subtle text-up-strong'
      : 'border-subtle bg-bg-2 text-text-3';
  const label = verdict === 'compliant' ? '合规'
    : verdict === 'violation' ? '违规'
      : verdict === 'uncovered' ? '规则未覆盖' : '不可判定';
  return (
    <span className={`rounded-full border px-2 py-0.5 text-caption ${tone}`}>
      {ruleId} · {label}
    </span>
  );
}

function RevealBlock({ reveal }: { reveal: RevealSummary }) {
  return (
    <div className="space-y-3" data-testid="reveal-block">
      <h3 className="text-h3 text-text-1">当日结果（已按你的选择揭示）</h3>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-body-sm text-text-2">
          平仓 {reveal.closedEpisodeCount} 笔 · 盈亏已知 {reveal.pnlKnownCount} 笔
        </span>
        <span className={`font-mono text-mono-lg ${
          reveal.totalNet == null ? 'text-text-3'
            : reveal.totalNet < 0 ? 'text-down-strong' : 'text-up-strong'
        }`}>
          {reveal.totalNet == null
            ? '合计标缺'
            : `${reveal.totalNet >= 0 ? '+' : ''}$${reveal.totalNet.toFixed(2)}`}
        </span>
      </div>
      <ul className="space-y-2">
        {reveal.episodes.map((episode) => (
          <li key={episode.episodeId} className="flex flex-wrap items-center justify-between gap-2 rounded-ds-md border border-subtle bg-bg-1 px-3 py-2">
            <span className="font-mono text-mono-xs text-text-1">{episode.rawSymbol}</span>
            <span className="flex items-center gap-2">
              <QuadrantChip quadrant={episode.quadrant} label={episode.quadrantLabel} />
              <span className={`font-mono text-mono-sm ${
                episode.pnlNet == null ? 'text-text-3'
                  : episode.pnlNet < 0 ? 'text-down-strong' : 'text-up-strong'
              }`}>
                {episode.pnlNet == null ? '标缺' : `${episode.pnlNet >= 0 ? '+' : ''}$${episode.pnlNet.toFixed(2)}`}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-caption text-text-3">
        「侥幸」＝违规∧盈利：记录其趋势，月度首看。过程与结果两本账，永不合成一个分数。
      </p>
    </div>
  );
}

const JournalDailyReviewPage: React.FC = () => {
  const [flow, setFlow] = useState<DailyReviewFlowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [step, setStep] = useState<StepId>('s0');
  const [doneSteps, setDoneSteps] = useState<Record<string, boolean>>({});
  const [ackDrafts, setAckDrafts] = useState<Record<number, string>>({});
  const [planDrafts, setPlanDrafts] = useState<Record<number, string>>({});
  const [manualScores, setManualScores] = useState<Record<number, 'yes' | 'no' | 'missing'>>({});
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [reveal, setReveal] = useState<RevealSummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetchDailyReviewFlow();
      setFlow(response);
      setReveal(response.reveal);
      if (response.session?.sealedAt) {
        setStep('s4');
        setDoneSteps({ s0: true, s1: true, s2: true, s3: true, s4: true });
        setNote(response.session.note);
      }
    } catch (reason) {
      setError(parseApiError(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sealed = flow?.session?.sealedAt != null;
  const violations = useMemo(
    () => (flow?.episodesToday ?? []).filter((item) => item.needsAck),
    [flow],
  );
  const unackedViolations = violations.filter(
    (item) => !item.acked && !(ackDrafts[item.episodeId] ?? '').trim(),
  );
  const positionsNeedingPlan = (flow?.openPositions ?? []).filter(
    (item) => !item.hasExitPlan,
  );

  const save = useCallback(
    async (
      overrides: {
        seal?: boolean;
        reveal?: boolean;
        sessionKind?: 'trading_day' | 'rest_day';
        markStep?: StepId;
      } = {},
    ): Promise<boolean> => {
      if (!flow) return false;
      setSaving(true);
      setSaveError(null);
      const steps: Record<string, boolean> = { ...doneSteps };
      if (overrides.markStep) steps[overrides.markStep] = true;
      try {
        const response = await postDailyReviewFlow({
          etDate: flow.etDate,
          sessionKind: overrides.sessionKind,
          steps,
          violationAcks: violations
            .filter((item) => (ackDrafts[item.episodeId] ?? '').trim())
            .map((item) => ({
              positionEpisodeId: item.episodeId,
              ackText: (ackDrafts[item.episodeId] ?? '').trim(),
            })),
          exitPlans: Object.entries(planDrafts)
            .filter(([, text]) => text.trim())
            .map(([episodeId, text]) => ({
              positionEpisodeId: Number(episodeId),
              planText: text.trim(),
            })),
          manualScores: Object.entries(manualScores).map(([metricId, value]) => ({
            metricId: Number(metricId),
            value,
          })),
          note,
          seal: overrides.seal ?? false,
          reveal: overrides.reveal ?? false,
        });
        if (response.reveal) setReveal(response.reveal);
        if (overrides.markStep) {
          setDoneSteps((current) => ({ ...current, [overrides.markStep as string]: true }));
        }
        setPlanDrafts({});
        const refreshed = await fetchDailyReviewFlow();
        setFlow(refreshed);
        if (refreshed.reveal) setReveal(refreshed.reveal);
        return true;
      } catch (reason) {
        setSaveError(parseApiError(reason).message);
        return false;
      } finally {
        setSaving(false);
      }
    },
    [ackDrafts, doneSteps, flow, manualScores, note, planDrafts, violations],
  );

  const advance = (next: StepId, markStep: StepId) => {
    void save({ markStep }).then((ok) => {
      if (ok) setStep(next);
    });
  };

  /**
   * 揭示是最小请求：服务端逐字重放已密封修订（复核修复 1）。
   * 不重发内容——页面重载或密封后的数据漂移都不影响揭示可达性。
   */
  const revealSealed = useCallback(async () => {
    if (!flow) return;
    setSaving(true);
    setSaveError(null);
    try {
      const response = await postDailyReviewReveal(
        flow.etDate,
        flow.session?.revision,
      );
      if (response.reveal) setReveal(response.reveal);
      const refreshed = await fetchDailyReviewFlow();
      setFlow(refreshed);
      if (refreshed.reveal) setReveal(refreshed.reveal);
    } catch (reason) {
      setSaveError(parseApiError(reason).message);
    } finally {
      setSaving(false);
    }
  }, [flow]);

  if (loading) {
    return (
      <div className="mx-auto max-w-3xl p-4 lg:p-6">
        <div className="card-base p-10 text-center text-body-sm text-text-3">正在装配今日复盘…</div>
      </div>
    );
  }
  if (error || !flow) {
    return (
      <div className="mx-auto max-w-3xl p-4 lg:p-6">
        <ApiErrorAlert error={error ?? parseApiError(new Error('未知错误'))} actionLabel="重试" onAction={() => void load()} />
      </div>
    );
  }
  if (flow.dataState === 'not_built') {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-4 lg:p-6">
        <Link to="/journal" className="inline-flex items-center gap-1.5 text-body-sm text-text-3 hover:text-text-1">
          <ArrowLeft size={15} /> 返回复盘
        </Link>
        <div className="card-base p-8 text-center text-body-sm text-text-2">
          尚无 Episode 构建：先在 Journal 导入券商证据并构建，再进行日终复盘。
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 lg:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link to="/journal" className="inline-flex items-center gap-1.5 text-body-sm text-text-3 hover:text-text-1">
            <ArrowLeft size={15} /> 返回复盘
          </Link>
          <h1 className="mt-1 text-h1 text-text-1">日终复盘 · {flow.etDate}</h1>
          <p className="mt-1 text-caption text-text-3">
            盲评模式：密封前不显示任何当日盈亏。目标 ≤5 分钟；跳过即标缺，绝不补默认值。
          </p>
        </div>
        {sealed && (
          <span className="inline-flex items-center gap-1 rounded-full border border-up-strong/25 bg-up-subtle px-3 py-1 text-caption text-up-strong">
            <Lock size={12} /> 已密封
          </span>
        )}
      </div>

      {/* 步进器导航 */}
      <ol className="flex flex-wrap gap-2" aria-label="复盘步骤">
        {STEP_ORDER.map((id) => (
          <li key={id}>
            <span className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-caption ${
              step === id
                ? 'border-accent bg-accent-subtle-bg text-accent'
                : doneSteps[id]
                  ? 'border-up-strong/25 bg-up-subtle text-up-strong'
                  : 'border-subtle bg-bg-2 text-text-3'
            }`}>
              {doneSteps[id] && <CheckCircle2 size={12} />}
              {STEP_TITLES[id]}
            </span>
          </li>
        ))}
      </ol>

      {saveError && (
        <p className="text-body-sm text-down-strong" role="alert">保存失败：{saveError}</p>
      )}

      {/* S0 检票 */}
      {step === 's0' && (
        <section className="card-base p-4" aria-label="S0 检票">
          {flow.restDayCandidate ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-body text-text-1">
                <Moon size={16} /> 今日（ET）无任何新开或平仓回合。
              </div>
              <p className="text-body-sm text-text-3">
                「不交易也是仓位」：休息日确认本身就是过程指标（已连续 {flow.restStreak} 个已密封休息日）。
              </p>
              <button
                type="button"
                className="btn-primary"
                disabled={saving || sealed}
                onClick={() => void save({ sessionKind: 'rest_day', seal: true, markStep: 's0' }).then((ok) => { if (ok) setStep('s4'); })}
              >
                {saving ? '确认中…' : '休息日确认（一键完成）'}
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-body text-text-1">
                今日活动：{flow.episodesToday.filter((item) => item.openedToday).length} 笔新开 ·
                {' '}{flow.episodesToday.filter((item) => item.closedToday).length} 笔平仓 ·
                {' '}{flow.openPositions.length} 个未平仓位
              </p>
              <button type="button" className="btn-primary" onClick={() => advance('s1', 's0')} disabled={saving || sealed}>
                进入违规扫描 →
              </button>
            </div>
          )}
        </section>
      )}

      {/* S1 违规扫描 */}
      {step === 's1' && (
        <section className="card-base space-y-3 p-4" aria-label="S1 违规扫描">
          <p className="text-caption text-text-3">
            车道判定纯机械（V-规则分类器）；每条违规必须写一句话确认，不写不能进下一步。
          </p>
          <ul className="space-y-2">
            {flow.episodesToday.map((item) => (
              <li key={item.episodeId} className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-mono-xs text-text-1">{item.rawSymbol}</span>
                  <span className="flex items-center gap-2">
                    <span className="text-caption text-text-3">
                      {toEtTime(item.openedAt)} ET 开{item.closedToday ? ` · ${toEtTime(item.closedAt)} ET 平` : ' · 未平'}
                    </span>
                    {verdictChip(item.verdict, item.ruleId)}
                  </span>
                </div>
                {item.needsAck && !item.acked && (
                  <div className="mt-2">
                    <label className="text-caption text-text-3" htmlFor={`ack-${item.episodeId}`}>
                      一句话确认（必填）
                    </label>
                    <input
                      id={`ack-${item.episodeId}`}
                      type="text"
                      maxLength={200}
                      className="mt-1 w-full rounded-ds-sm border border-subtle bg-bg-0 px-2 py-1.5 text-body-sm text-text-1"
                      placeholder="为什么这单出现在违规车道？"
                      value={ackDrafts[item.episodeId] ?? ''}
                      onChange={(event) => setAckDrafts((current) => ({
                        ...current,
                        [item.episodeId]: event.target.value,
                      }))}
                    />
                  </div>
                )}
                {item.needsAck && item.acked && (
                  <p className="mt-2 text-caption text-up-strong">已确认（append-only 留痕）</p>
                )}
              </li>
            ))}
            {flow.episodesToday.length === 0 && (
              <li className="text-body-sm text-text-3">今日无回合。</li>
            )}
          </ul>
          <button
            type="button"
            className="btn-primary"
            disabled={saving || sealed || unackedViolations.length > 0}
            onClick={() => advance('s2', 's1')}
          >
            {unackedViolations.length > 0
              ? `还有 ${unackedViolations.length} 条违规待确认`
              : '确认并进入过程打分 →'}
          </button>
        </section>
      )}

      {/* S2 过程打分卡 */}
      {step === 's2' && (
        <section className="card-base space-y-3 p-4" aria-label="S2 过程打分卡">
          <p className="text-caption text-text-3">
            七项过程指标：自动优先，手动兜底，缺席标缺。打分卡上没有任何盈亏字段，也不按盈亏着色。
          </p>
          <ul className="space-y-2">
            {flow.processMetrics.map((metric) => (
              <li key={metric.metricId} className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-body-sm text-text-1">{metric.metricId}. {metric.name}</span>
                  <span className="flex items-center gap-2">
                    {metric.basis === 'auto' && metric.valueRatio != null && (
                      <span className="font-mono text-mono-sm text-text-1">
                        {(metric.valueRatio * 100).toFixed(1)}%
                      </span>
                    )}
                    {metric.basis === 'auto' && metric.valueRatio == null && metric.valueText && (
                      <span className="text-body-sm text-text-2">{metric.valueText}</span>
                    )}
                    <BasisBadge basis={manualScores[metric.metricId] && manualScores[metric.metricId] !== 'missing' ? 'manual' : metric.basis} />
                  </span>
                </div>
                {metric.basis === 'auto' && metric.valueText && metric.valueRatio != null && (
                  <p className="mt-1 text-caption text-text-3">{metric.valueText}</p>
                )}
                {metric.reason && (
                  <p className="mt-1 text-caption text-text-3">{metric.reason}</p>
                )}
                {metric.manualAllowed && !sealed && (
                  <div className="mt-2 flex items-center gap-2" role="group" aria-label={`${metric.name} 手填`}>
                    {(['yes', 'no', 'missing'] as const).map((value) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={(manualScores[metric.metricId] ?? 'missing') === value}
                        className={`rounded-ds-sm border px-2.5 py-1 text-caption ${
                          (manualScores[metric.metricId] ?? 'missing') === value
                            ? 'border-accent bg-accent-subtle-bg text-accent'
                            : 'border-subtle bg-bg-2 text-text-3 hover:text-text-1'
                        }`}
                        onClick={() => setManualScores((current) => ({
                          ...current,
                          [metric.metricId]: value,
                        }))}
                      >
                        {value === 'yes' ? '是' : value === 'no' ? '否' : '标缺'}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
          <button type="button" className="btn-primary" disabled={saving || sealed} onClick={() => advance('s3', 's2')}>
            进入出场预登记 →
          </button>
        </section>
      )}

      {/* S3 持仓出场预登记 */}
      {step === 's3' && (
        <section className="card-base space-y-3 p-4" aria-label="S3 出场预登记">
          <p className="text-caption text-text-3">
            为未平仓位登记明天的出场计划（写入 append-only 复盘链，锁定时间戳＝此后
            「规则出场 vs 情绪出场」判定的唯一依据）。
          </p>
          {flow.openPositions.length === 0 && (
            <p className="text-body-sm text-text-3">当前无未平仓位。</p>
          )}
          <ul className="space-y-2">
            {flow.openPositions.map((position) => (
              <li key={position.episodeId} className="rounded-ds-md border border-subtle bg-bg-1 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-mono-xs text-text-1">{position.rawSymbol}</span>
                  {position.hasExitPlan ? (
                    <span className="rounded-full border border-up-strong/25 bg-up-subtle px-2 py-0.5 text-caption text-up-strong">
                      已在册
                    </span>
                  ) : (
                    <span className="rounded-full border border-warn-strong/30 bg-warn-subtle px-2 py-0.5 text-caption text-warn-strong">
                      无在册计划
                    </span>
                  )}
                </div>
                {position.hasExitPlan ? (
                  <p className="mt-2 whitespace-pre-wrap text-body-sm text-text-2">{position.exitPlanText}</p>
                ) : (
                  <textarea
                    aria-label={`${position.rawSymbol} 出场计划`}
                    className="mt-2 w-full rounded-ds-sm border border-subtle bg-bg-0 px-2 py-1.5 text-body-sm text-text-1"
                    rows={2}
                    maxLength={2000}
                    placeholder="失效条件 / 出场计划（一两句话）"
                    value={planDrafts[position.episodeId] ?? ''}
                    onChange={(event) => setPlanDrafts((current) => ({
                      ...current,
                      [position.episodeId]: event.target.value,
                    }))}
                  />
                )}
              </li>
            ))}
          </ul>
          <button type="button" className="btn-primary" disabled={saving || sealed} onClick={() => advance('s4', 's3')}>
            {positionsNeedingPlan.length > 0 && Object.values(planDrafts).every((text) => !text.trim())
              ? '跳过（未登记将记标缺）→'
              : '登记并进入封卷 →'}
          </button>
        </section>
      )}

      {/* S4 封卷 + 自愿揭示 */}
      {step === 's4' && (
        <section className="card-base space-y-3 p-4" aria-label="S4 封卷">
          {!sealed ? (
            <>
              <label className="text-caption text-text-3" htmlFor="daily-note">可选一句话日志</label>
              <input
                id="daily-note"
                type="text"
                maxLength={2000}
                className="w-full rounded-ds-sm border border-subtle bg-bg-0 px-2 py-1.5 text-body-sm text-text-1"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
              <p className="text-caption text-text-3">
                密封后过程内容冻结（append-only，含 sha256）；之后才会出现「揭示当日结果」按钮，点不点由你。
              </p>
              <button
                type="button"
                className="btn-primary inline-flex items-center gap-1.5"
                disabled={saving}
                onClick={() => void save({ seal: true, markStep: 's4' })}
              >
                <Lock size={14} /> {saving ? '密封中…' : '密封今日复盘'}
              </button>
            </>
          ) : (
            <div className="space-y-3">
              <p className="text-body text-text-1">
                {flow.session?.sessionKind === 'rest_day' ? '休息日已确认并密封。' : '今日过程复盘已密封。'}
              </p>
              {flow.session?.note && (
                <p className="text-body-sm text-text-2">日志：{flow.session.note}</p>
              )}
              {!reveal && flow.session?.sessionKind !== 'rest_day' && (
                <button
                  type="button"
                  className="btn-secondary inline-flex items-center gap-1.5"
                  disabled={saving}
                  onClick={() => void revealSealed()}
                >
                  <Eye size={14} /> {saving ? '揭示中…' : '揭示当日结果（自愿；揭示顺序会入库）'}
                </button>
              )}
              {reveal && <RevealBlock reveal={reveal} />}
            </div>
          )}
        </section>
      )}

      <details className="card-base p-3 text-caption text-text-3">
        <summary className="cursor-pointer">口径与边界</summary>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          {flow.limitations.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </details>
    </div>
  );
};

export default JournalDailyReviewPage;

import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { fetchPlaybook, promotePlaybookCandidate, retirePlaybookRule } from '../../api/journal';
import { parseApiError, type ParsedApiError } from '../../api/error';
import type { PlaybookCandidate, PlaybookListResponse, PlaybookRule } from '../../types/journal';

/**
 * Playbook（合同切片 C-2）：append-only 候选（L2）与规则版本链（L3）。
 * 晋升 / 退役都是显式用户操作（CAS + 确认），系统永不自动晋升；
 * 规则不会影响系统评分或榜单，仅是用户自己的决策清单。
 */

const fmtDate = (raw: string): string => {
  const value = new Date(raw);
  return Number.isNaN(value.getTime()) ? raw : value.toLocaleDateString('zh-CN');
};

const snapshotSummary = (snapshot: Record<string, unknown>): string | null => {
  if (snapshot['snapshotKind'] === 'free_form') return '自由候选 · 未绑定观察桶';
  const counts = snapshot['counts'] as Record<string, number> | undefined;
  const buildId = snapshot['buildId'];
  if (!counts || buildId == null) return null;
  return `证据快照 · Build #${buildId} · ${counts['episodeCount'] ?? 0} 笔（条件性 ${
    counts['conditionalEpisodeCount'] ?? 0
  } 笔）`;
};

const BucketChip: React.FC<{ candidate: PlaybookCandidate }> = ({ candidate }) => {
  const bucket = candidate.sourceBucket;
  if (!bucket) return null;
  const kindLabel = bucket.groupKind === 'tag' ? '标签' : '错误类型';
  const directionLabel = bucket.direction === 'LONG' ? '做多' : bucket.direction === 'SHORT' ? '做空' : bucket.direction;
  const boundaryLabel = bucket.boundaryPolicy === 'verified' ? '边界已验证' : '边界假设/截断';
  return (
    <span className="rounded-full bg-accent/10 px-2 py-0.5 text-caption text-accent">
      {kindLabel} · {bucket.groupValue} · {directionLabel} · {boundaryLabel}
    </span>
  );
};

const CandidateRow: React.FC<{
  candidate: PlaybookCandidate;
  onPromoted: () => void;
}> = ({ candidate, onPromoted }) => {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const promote = async () => {
    setBusy(true);
    setError(null);
    try {
      await promotePlaybookCandidate(candidate.candidateKey);
      setConfirming(false);
      onPromoted();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex flex-col gap-1 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body-sm text-text-1">{candidate.title}</span>
        <BucketChip candidate={candidate} />
        {candidate.promoted ? (
          <span className="rounded-full bg-up-subtle px-2 py-0.5 text-caption text-up-strong">已晋升</span>
        ) : confirming ? (
          <span className="flex items-center gap-2">
            <span className="text-caption text-text-2">确认晋升？将重新冻结当前证据快照。</span>
            <button type="button" className="btn-primary text-caption" onClick={() => void promote()} disabled={busy}>
              确认晋升
            </button>
            <button type="button" className="btn-ghost text-caption" onClick={() => setConfirming(false)} disabled={busy}>
              取消
            </button>
          </span>
        ) : (
          <button type="button" className="btn-ghost text-caption" onClick={() => setConfirming(true)}>
            晋升为规则
          </button>
        )}
      </div>
      <p className="text-caption text-text-2">{candidate.ruleText}</p>
      <div className="flex flex-wrap items-center gap-2 text-caption text-text-3">
        <span>{snapshotSummary(candidate.evidenceSnapshot) ?? '证据快照已冻结'}</span>
        <span>创建于 {fmtDate(candidate.createdAt)}</span>
      </div>
      {error && (
        <p className="text-caption text-down-strong" role="alert">
          {error.message}
        </p>
      )}
    </li>
  );
};

const RuleRow: React.FC<{
  rule: PlaybookRule;
  onRetired: () => void;
}> = ({ rule, onRetired }) => {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const active = rule.status === 'active';

  const retire = async () => {
    setBusy(true);
    setError(null);
    try {
      await retirePlaybookRule(rule.lineageKey, rule.version);
      setConfirming(false);
      onRetired();
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex flex-col gap-1 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-mono-xs tabular-nums text-text-3">v{rule.version}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-caption ${
            active ? 'bg-up-subtle text-up-strong' : 'bg-bg-0 text-text-3'
          }`}
        >
          {active ? '生效中' : '已退役'}
        </span>
        <span className="text-body-sm text-text-1">{rule.title}</span>
        {active && rule.isLatestVersion && (
          confirming ? (
            <span className="flex items-center gap-2">
              <span className="text-caption text-text-2">确认退役？将追加一条退役版本记录，不删除历史。</span>
              <button type="button" className="btn-primary text-caption" onClick={() => void retire()} disabled={busy}>
                确认退役
              </button>
              <button type="button" className="btn-ghost text-caption" onClick={() => setConfirming(false)} disabled={busy}>
                取消
              </button>
            </span>
          ) : (
            <button type="button" className="btn-ghost text-caption" onClick={() => setConfirming(true)}>
              退役
            </button>
          )
        )}
      </div>
      <p className="text-caption text-text-2">{rule.ruleText}</p>
      <div className="flex flex-wrap items-center gap-2 text-caption text-text-3">
        <span>{snapshotSummary(rule.evidenceSnapshot) ?? '证据快照已冻结'}</span>
        <span>记录于 {fmtDate(rule.createdAt)}</span>
      </div>
      {error && (
        <p className="text-caption text-down-strong" role="alert">
          {error.message}
        </p>
      )}
    </li>
  );
};

export const PlaybookPanel: React.FC<{ refreshToken?: number }> = ({ refreshToken = 0 }) => {
  const [playbook, setPlaybook] = useState<PlaybookListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPlaybook(await fetchPlaybook());
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const candidates = playbook?.candidates ?? [];
  const rules = playbook?.rules ?? [];

  return (
    <section className="rounded-ds-md border border-subtle bg-bg-1" aria-label="Playbook">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Playbook</div>
          <h2 className="mt-0.5 text-h2 text-text-1">Playbook · 决策清单</h2>
        </div>
        <button type="button" className="btn-ghost text-body-sm" onClick={() => void load()} disabled={loading}>
          刷新
        </button>
      </div>

      <div className="space-y-3 px-4 py-3">
        <p className="text-caption text-text-3">
          规则不会影响系统评分或榜单，仅是你的决策清单；晋升与退役都是显式操作，历史版本永不删除。
        </p>

        {loading && !playbook && <p className="py-4 text-center text-body-sm text-text-3">加载 Playbook 中…</p>}

        {error && (
          <p className="text-body-sm text-down-strong" role="alert">
            {error.message}
          </p>
        )}

        {!error && playbook && (
          <>
            <div>
              <h3 className="text-label uppercase tracking-label text-text-3">候选（观察，待验证）</h3>
              {candidates.length === 0 ? (
                <p className="mt-1 text-body-sm text-text-3">
                  还没有候选。在上方「模式观察」中对某个分桶点「保存为候选」即可创建。
                </p>
              ) : (
                <ul className="divide-y divide-[color:var(--border-subtle)]">
                  {candidates.map((candidate) => (
                    <CandidateRow key={candidate.candidateKey} candidate={candidate} onPromoted={() => void load()} />
                  ))}
                </ul>
              )}
            </div>

            <div>
              <h3 className="text-label uppercase tracking-label text-text-3">规则（已显式晋升）</h3>
              {rules.length === 0 ? (
                <p className="mt-1 text-body-sm text-text-3">还没有晋升过的规则。晋升会冻结当时的证据快照。</p>
              ) : (
                <ul className="divide-y divide-[color:var(--border-subtle)]">
                  {rules.map((rule) => (
                    <RuleRow key={rule.ruleKey} rule={rule} onRetired={() => void load()} />
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
};

export default PlaybookPanel;

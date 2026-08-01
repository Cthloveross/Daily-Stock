import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { fetchEpisodePlaybookLinks } from '../../../api/journal';
import { parseApiError, type ParsedApiError } from '../../../api/error';
import type { EpisodePlaybookLink, EpisodePlaybookLinksResponse } from '../../../types/journal';

/**
 * Playbook 关联（合同切片 C-3）：零写反向链接。
 * 只展示证据快照（在候选创建/规则晋升时冻结、且构建身份一致）中确认包含
 * 本回合的候选/规则；因抽样截断而无法确认的引用单独列出并明确标注，
 * 绝不伪装为已确认的关联。
 */

const TRUNCATED_NOTICE = '证据快照抽样截断，无法确认该回合是否在样本内';

function bucketLabel(bucket?: EpisodePlaybookLink['bucket']): string | null {
  if (!bucket) return null;
  const kindLabel = bucket.groupKind === 'tag' ? '标签' : '错误类型';
  const directionLabel = bucket.direction === 'LONG'
    ? '做多'
    : bucket.direction === 'SHORT'
      ? '做空'
      : bucket.direction;
  const boundaryLabel = bucket.boundaryPolicy === 'verified' ? '边界已验证' : '边界假设/截断';
  return `${kindLabel} · ${bucket.groupValue} · ${directionLabel} · ${boundaryLabel}`;
}

function kindLabel(link: EpisodePlaybookLink): string {
  if (link.kind === 'rule') {
    const statusLabel = link.status === 'active' ? '生效中' : '已退役';
    return `规则 v${link.version ?? '?'} · ${statusLabel}`;
  }
  return link.promoted ? '候选 · 已晋升' : '候选';
}

function linkRowKey(link: EpisodePlaybookLink): string {
  return `${link.kind}:${link.lineageKey ?? link.candidateKey ?? link.title}:${link.linkState}`;
}

const LinkRow: React.FC<{ link: EpisodePlaybookLink }> = ({ link }) => {
  const bucket = bucketLabel(link.bucket);
  return (
    <li className="flex flex-col gap-1 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2 py-0.5 text-caption ${
            link.kind === 'rule'
              ? link.status === 'active'
                ? 'bg-up-subtle text-up-strong'
                : 'bg-bg-0 text-text-3'
              : 'bg-accent/10 text-accent'
          }`}
        >
          {kindLabel(link)}
        </span>
        <span className="text-body-sm text-text-1">{link.title}</span>
        {bucket && (
          <span className="rounded-full bg-bg-2 px-2 py-0.5 text-caption text-text-2">{bucket}</span>
        )}
      </div>
      <p className="text-caption text-text-2">{link.ruleText}</p>
      {link.linkState === 'possible_truncated' && (
        <p className="text-caption text-warn-strong">{TRUNCATED_NOTICE}</p>
      )}
    </li>
  );
};

export const EpisodePlaybookLinksPanel: React.FC<{
  episodeId: number;
  buildId: number;
}> = ({ episodeId, buildId }) => {
  const [response, setResponse] = useState<EpisodePlaybookLinksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setResponse(await fetchEpisodePlaybookLinks(episodeId, buildId));
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setLoading(false);
    }
  }, [buildId, episodeId]);

  useEffect(() => {
    void load();
  }, [load]);

  const links = response?.links ?? [];
  const confirmed = links.filter((link) => link.linkState === 'confirmed');
  const possible = links.filter((link) => link.linkState === 'possible_truncated');

  return (
    <section className="card-base" aria-label="Playbook 关联">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-3">
        <div>
          <div className="text-label uppercase tracking-label text-text-3">Playbook</div>
          <h2 className="mt-0.5 text-h2 text-text-1">Playbook 关联</h2>
        </div>
        <button type="button" className="btn-ghost text-body-sm" onClick={() => void load()} disabled={loading}>
          刷新
        </button>
      </div>
      <div className="space-y-3 px-4 py-3">
        <p className="text-caption text-text-3">
          仅显示证据快照（构建 #{buildId}，冻结于候选创建 / 规则晋升时）中引用了本回合的候选与规则；
          关联不会影响系统评分或榜单。
        </p>

        {loading && !response && (
          <p className="py-3 text-center text-body-sm text-text-3">正在读取 Playbook 关联…</p>
        )}

        {error && (
          <p className="text-body-sm text-down-strong" role="alert">
            {error.message}
          </p>
        )}

        {!error && response && links.length === 0 && (
          <p className="text-body-sm text-text-3">该回合未被任何 Playbook 候选或规则引用。</p>
        )}

        {!error && confirmed.length > 0 && (
          <ul className="divide-y divide-[color:var(--border-subtle)]" aria-label="已确认的 Playbook 关联">
            {confirmed.map((link) => (
              <LinkRow key={linkRowKey(link)} link={link} />
            ))}
          </ul>
        )}

        {!error && possible.length > 0 && (
          <div>
            <h3 className="text-label uppercase tracking-label text-text-3">可能相关（无法确认）</h3>
            <ul className="divide-y divide-[color:var(--border-subtle)]" aria-label="无法确认的 Playbook 关联">
              {possible.map((link) => (
                <LinkRow key={linkRowKey(link)} link={link} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
};

export default EpisodePlaybookLinksPanel;

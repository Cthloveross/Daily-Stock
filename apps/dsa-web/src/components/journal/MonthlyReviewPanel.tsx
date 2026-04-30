import type React from 'react';
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import apiClient from '../../api';
import { toCamelCase } from '../../api/utils';
import { parseApiError } from '../../api/error';
import { toast } from '../ui';

interface ReviewItem {
  yearMonth: string;
  currentPhase: number;
  reviewMarkdown: string;
  generatedAt?: string | null;
}

interface ReviewListResponse {
  count: number;
  items: ReviewItem[];
}

// LLM monthly review takes 30-180 s. apiClient default 30s timeout would
// always abort. Use a generous 4-minute window matching backend limits.
const REGENERATE_TIMEOUT_MS = 240_000;

export const MonthlyReviewPanel: React.FC = () => {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [selected, setSelected] = useState<ReviewItem | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateYm, setGenerateYm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const { data } = await apiClient.get('/api/v1/journal/reviews');
    const resp = toCamelCase<ReviewListResponse>(data);
    setItems(resp.items);
    if (!selected && resp.items[0]) setSelected(resp.items[0]);
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const regenerate = async () => {
    if (!generateYm) return;
    if (!/^\d{4}-\d{2}$/.test(generateYm)) {
      setError('格式应为 YYYY-MM，例如 2026-04');
      return;
    }
    setGenerating(true);
    setError(null);
    try {
      const [year, month] = generateYm.split('-');
      await apiClient.post(
        `/api/v1/journal/reviews/${year}/${month}/generate`,
        { dry_run: false },
        { timeout: REGENERATE_TIMEOUT_MS },
      );
      toast.success(`${generateYm} 复盘已生成`);
      // Refresh + auto-select the new review.
      await load();
      // After load, find and select the just-generated month.
      const target = `${year}-${month}`;
      setSelected((prev) => prev?.yearMonth === target ? prev : (items.find((i) => i.yearMonth === target) ?? prev));
    } catch (e) {
      const parsed = parseApiError(e);
      setError(parsed.message || '生成失败，请稍后再试');
      toast.error(`生成失败: ${parsed.message ?? '请查看后端日志'}`);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="grid gap-4 md:grid-cols-[200px_1fr]">
      <div className="card-base p-3">
        <h4 className="text-sm font-semibold">Months</h4>
        {items.length === 0 && <p className="mt-2 text-xs text-muted">尚无复盘</p>}
        <ul className="mt-2 space-y-1">
          {items.map((it) => (
            <li key={it.yearMonth}>
              <button
                type="button"
                className={`w-full rounded px-2 py-1 text-left text-sm ${
                  selected?.yearMonth === it.yearMonth ? 'bg-base-subtle' : ''
                }`}
                onClick={() => setSelected(it)}
              >
                {it.yearMonth}
                <span className="ml-1 text-xs text-muted">Ph{it.currentPhase}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="mt-4 space-y-2 border-t border-base-subtle pt-3">
          <h4 className="text-sm font-semibold">Generate</h4>
          <input
            className="input-base w-full text-xs"
            placeholder="YYYY-MM (e.g. 2026-04)"
            value={generateYm}
            onChange={(e) => {
              setGenerateYm(e.target.value);
              setError(null);
            }}
          />
          <button
            type="button"
            className="btn-primary w-full text-xs"
            disabled={generating || !generateYm}
            onClick={() => void regenerate()}
          >
            {generating ? '生成中…30-180 秒' : 'Run retrospective'}
          </button>
          {generating && (
            <p className="text-[10px] text-text-3">
              LLM 调用要等一会儿，别关 tab。
            </p>
          )}
          {error && (
            <p className="rounded-ds-sm border border-down-strong/30 bg-down-strong/10 p-2 text-[11px] text-down-strong">
              {error}
            </p>
          )}
        </div>
      </div>
      <div className="card-base p-4">
        {!selected ? (
          <p className="text-muted">选一个月份以查看复盘。</p>
        ) : (
          <article className="prose prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.reviewMarkdown}</ReactMarkdown>
          </article>
        )}
      </div>
    </div>
  );
};

export default MonthlyReviewPanel;

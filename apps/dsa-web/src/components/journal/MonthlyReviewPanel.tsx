import type React from 'react';
import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import apiClient from '../../api';
import { toCamelCase } from '../../api/utils';

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

export const MonthlyReviewPanel: React.FC = () => {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [selected, setSelected] = useState<ReviewItem | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateYm, setGenerateYm] = useState('');

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
    setGenerating(true);
    try {
      const [year, month] = generateYm.split('-');
      await apiClient.post(
        `/api/v1/journal/reviews/${year}/${month}/generate`,
        { dry_run: false },
      );
      await load();
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
            placeholder="YYYY-MM"
            value={generateYm}
            onChange={(e) => setGenerateYm(e.target.value)}
          />
          <button
            type="button"
            className="btn-primary w-full text-xs"
            disabled={generating || !generateYm}
            onClick={() => void regenerate()}
          >
            {generating ? 'Running…' : 'Run retrospective'}
          </button>
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

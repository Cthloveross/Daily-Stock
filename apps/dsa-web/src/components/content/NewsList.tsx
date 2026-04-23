import type React from 'react';
import { ExternalLink, ChevronsUp, ChevronUp, ChevronDown, ChevronsDown, Minus } from 'lucide-react';
import { cn } from '../../utils/cn';

export type NewsSentiment = -2 | -1 | 0 | 1 | 2;

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  excerpt?: string;
  url: string;
  publishedAt: Date | string;
  tickers?: string[];
  sentiment?: NewsSentiment;
  sentimentReason?: string | null;
}

export interface NewsListProps {
  items: NewsItem[];
  onItemClick?: (item: NewsItem) => void;
  className?: string;
}

const SENTIMENT_META: Record<NewsSentiment, { label: string; color: string; Icon: typeof ChevronUp }> = {
  2:  { label: '强烈利好', color: '#16a34a', Icon: ChevronsUp },
  1:  { label: '偏利好',   color: '#22c55e', Icon: ChevronUp },
  0:  { label: '中性',     color: '#9ca3af', Icon: Minus },
  [-1]: { label: '偏利空', color: '#ef4444', Icon: ChevronDown },
  [-2]: { label: '强烈利空', color: '#b91c1c', Icon: ChevronsDown },
};

function SentimentBadge({ score, reason }: { score: NewsSentiment; reason?: string | null }) {
  const meta = SENTIMENT_META[score];
  const { Icon } = meta;
  const title = reason ? `${meta.label}：${reason}` : meta.label;
  return (
    <span
      className="inline-flex shrink-0 items-center gap-0.5 rounded-full border px-1.5 py-0.5 text-caption"
      style={{ borderColor: `${meta.color}55`, color: meta.color }}
      title={title}
      aria-label={title}
    >
      <Icon size={14} strokeWidth={2.5} />
      <span className="hidden sm:inline">{meta.label}</span>
    </span>
  );
}

function formatRelative(d: Date | string): string {
  const date = typeof d === 'string' ? new Date(d) : d;
  if (Number.isNaN(date.getTime())) return '';
  const diffMs = Date.now() - date.getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return '< 1m ago';
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  const day = Math.floor(h / 24);
  if (day < 14) return `${day}d ago`;
  return date.toLocaleDateString();
}

export const NewsList: React.FC<NewsListProps> = ({ items, onItemClick, className }) => {
  if (items.length === 0) {
    return (
      <div className={cn('py-12 text-center text-body-sm text-text-3', className)}>
        No news items in scope.
      </div>
    );
  }
  return (
    <ul className={cn('divide-y divide-[color:var(--border-subtle)]', className)}>
      {items.map((it) => {
        const hasRealUrl = it.url && it.url !== '#' && !it.url.startsWith('javascript:');
        return (
        <li key={it.id}>
          <a
            href={hasRealUrl ? it.url : undefined}
            target={hasRealUrl ? '_blank' : undefined}
            rel={hasRealUrl ? 'noreferrer noopener' : undefined}
            onClick={(e) => {
              if (onItemClick) {
                e.preventDefault();
                onItemClick(it);
                return;
              }
              if (!hasRealUrl) e.preventDefault();
            }}
            className="flex flex-col gap-1 px-4 py-3 transition-colors hover:bg-bg-1 aria-disabled:cursor-default"
            aria-disabled={!hasRealUrl && !onItemClick}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-2 min-w-0">
                {typeof it.sentiment === 'number' && (
                  <SentimentBadge score={it.sentiment as NewsSentiment} reason={it.sentimentReason} />
                )}
                <div className="text-body text-text-1">{it.title}</div>
              </div>
              {hasRealUrl && (
                <ExternalLink size={12} strokeWidth={1.5} className="mt-1 shrink-0 text-text-3" />
              )}
            </div>
            {it.excerpt && (
              <p className="line-clamp-2 text-body-sm text-text-2">{it.excerpt}</p>
            )}
            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-caption text-text-3">
              <span className="uppercase">{it.source}</span>
              <span>·</span>
              <span>{formatRelative(it.publishedAt)}</span>
              {it.tickers && it.tickers.length > 0 && (
                <>
                  <span>·</span>
                  <span className="font-mono">{it.tickers.join(' ')}</span>
                </>
              )}
            </div>
          </a>
        </li>
        );
      })}
    </ul>
  );
};

export default NewsList;

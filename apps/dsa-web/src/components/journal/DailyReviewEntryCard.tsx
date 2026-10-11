import { useEffect, useState } from 'react';
import { CalendarCheck2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { fetchDailyReviewFlow } from '../../api/journalReviewFlow';
import type { DailyReviewFlowResponse } from '../../types/journalReviewFlow';

/**
 * Journal 首页入口卡：「今日复盘 未开始 / 进行中 / 已密封 / 休息日」。
 * 只读一次；读取失败静默降级为入口链接（不拖垮 Journal 主页）。
 */
export function DailyReviewEntryCard() {
  const [flow, setFlow] = useState<DailyReviewFlowResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchDailyReviewFlow()
      .then((response) => {
        if (!cancelled) setFlow(response);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  let statusLabel = '状态读取中…';
  let tone = 'border-subtle bg-bg-2 text-text-3';
  if (failed) {
    statusLabel = '状态不可用';
  } else if (flow) {
    const session = flow.session;
    if (session?.sealedAt) {
      if (session.sessionKind === 'rest_day') {
        statusLabel = '休息日已确认';
        tone = 'border-up-strong/25 bg-up-subtle text-up-strong';
      } else {
        statusLabel = session.revealedAfterSeal ? '已密封 · 已揭示' : '已密封';
        tone = 'border-up-strong/25 bg-up-subtle text-up-strong';
      }
    } else if (session) {
      statusLabel = '进行中';
      tone = 'border-accent-subtle-border bg-accent-subtle-bg text-accent';
    } else {
      statusLabel = flow.restDayCandidate ? '休息日待确认' : '未开始';
      tone = flow.restDayCandidate
        ? 'border-subtle bg-bg-2 text-text-2'
        : 'border-warn-strong/30 bg-warn-subtle text-warn-strong';
    }
  }

  return (
    <div className="card-base flex flex-wrap items-center justify-between gap-3 p-4" data-testid="daily-review-entry">
      <div className="flex items-center gap-3">
        <CalendarCheck2 size={18} className="text-text-3" />
        <div>
          <div className="text-body font-medium text-text-1">今日复盘（引导式 · 盲评）</div>
          <div className="mt-0.5 text-caption text-text-3">
            S0 检票 → S1 违规扫描 → S2 过程打分 → S3 出场预登记 → S4 封卷；≤5 分钟，密封前不见盈亏。
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className={`rounded-full border px-2.5 py-1 text-caption ${tone}`}>{statusLabel}</span>
        <Link to="/journal/review/daily" className="btn-primary">进入</Link>
      </div>
    </div>
  );
}

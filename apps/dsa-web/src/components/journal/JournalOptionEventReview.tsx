import type React from 'react';
import { useEffect, useState } from 'react';
import { fetchIntradayTop } from '../../api/opportunities';
import type { IntradayTopResponse } from '../../types/opportunities';
import { IntradayOptionEventFeed } from '../opportunities/IntradayOptionEventFeed';

/**
 * 复盘用期权异动 feed（2026-08-14 自 /intraday 迁入）。
 *
 * 用户定位：期权异动是**复盘证据**——回看当天/最近一个时段发生了什么，
 * 不是盘中决策输入（「期权异动我不要盘中的，这个是作为复盘的」）。因此它
 * 落在 Journal 仓位复盘面：进入页签读取一次、不轮询；数据来自既有
 * intraday-top 载荷（服务端 60 秒 TTL + 预热缓存，零新增取数路径），
 * `quoteSessionLabel` 如实声明读数属于哪个时段（休市时＝最近一个交易
 * 时段）。加载/失败态显式陈述，绝不用空 feed 冒充「无异动」。
 */
const JournalOptionEventReview: React.FC = () => {
  const [data, setData] = useState<IntradayTopResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchIntradayTop([], { limit: 5 })
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : '异动读取失败');
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (data) {
    return (
      <IntradayOptionEventFeed
        events={data.recentOptionEvents}
        quoteSessionLabel={data.quoteSessionLabel}
      />
    );
  }
  return (
    <section
      aria-label="期权异动"
      className="rounded-ds-md border border-subtle bg-bg-1 px-4 py-3 text-body-sm text-text-3"
    >
      {error === null ? '期权异动读取中…' : `期权异动暂不可用：${error}`}
    </section>
  );
};

export default JournalOptionEventReview;

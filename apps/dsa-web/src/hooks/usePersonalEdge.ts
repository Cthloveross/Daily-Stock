import { useEffect, useState } from 'react';
import { fetchPersonalEdge } from '../api/journal';
import type { PersonalEdgeResponse } from '../types/journal';

/**
 * 个人画像回灌读取状态：
 * - `loading`：请求进行中；
 * - `unavailable`：端点失败或 Journal 尚未构建（not_built）→ 消费方显式
 *   「标缺」，绝不以 0 冒充；
 * - `ready`：默认 build 的描述统计（fetch 层带 2 分钟 sessionCache，激活新 build 后旧数最多存活 2 分钟）。
 */
export type PersonalEdgeView =
  | { state: 'loading' }
  | { state: 'unavailable' }
  | { state: 'ready'; data: PersonalEdgeResponse };

export function usePersonalEdge(): PersonalEdgeView {
  const [view, setView] = useState<PersonalEdgeView>({ state: 'loading' });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchPersonalEdge();
        if (cancelled) return;
        if (data && data.dataState === 'ready') {
          setView({ state: 'ready', data });
        } else {
          setView({ state: 'unavailable' });
        }
      } catch {
        if (!cancelled) setView({ state: 'unavailable' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return view;
}

import { useEffect, useMemo, useState } from 'react';
import { fetchOpportunityOptionWalls } from '../api/opportunities';
import type {
  IntradayTopResponse,
  OpportunityOptionWallItem,
} from '../types/opportunities';

/**
 * 深度层期权墙共享读取 hook（2026-08-15）。
 *
 * 实时扫描表「期权墙」列与盘前期权异常面板共用**同一条**取数路径：
 * 名单派生（深度层 ≤8 檔，单层模式退回候选列表）、每批 ≤5 檔的分批、
 * API 层 sessionCache 与在途去重全部只在这里发生一次——两个消费方同时
 * 挂载时第二次调用命中缓存或在途请求，不产生额外供应商请求。
 *
 * 诚实边界：墙位＝上一清算时段 OI 的未平仓分布事实；任何一批失败该批
 * 内的檔显式缺行（消费方渲染标缺），绝不用 0 或空墙冒充读数。
 */

/** 覆盖深度层名单：与深度层 8 行硬顶同值，绝不向宽层全清单扇出。 */
export const DEEP_LANE_OPTION_WALL_MAX_TICKERS = 8;

/** 既有端点合同：option-walls 每次 ≤5 檔。 */
const WALLS_BATCH_SIZE = 5;

export function chunkSymbols<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let index = 0; index < list.length; index += size) {
    out.push(list.slice(index, index + size));
  }
  return out;
}

/** 深度层名单派生：两层模式取真实深扫名单；单层模式退回候选列表。 */
export function deepLaneWallTickers(top: IntradayTopResponse | null): string[] {
  const source = top?.universeScan?.deepLane?.map((entry) => entry.ticker)
    ?? top?.candidates?.map((candidate) => candidate.ticker)
    ?? [];
  return [...new Set(source)].slice(0, DEEP_LANE_OPTION_WALL_MAX_TICKERS);
}

export interface DeepLaneOptionWallsView {
  /** 墙读取覆盖的名单（≤8 檔）；名单外的行如实标「未取墙」。 */
  tickers: string[];
  /** 逐檔墙读数；缺 key＝该檔读取失败或供应商无返回行（标缺）。 */
  wallByTicker: ReadonlyMap<string, OpportunityOptionWallItem>;
  /** 按当前名单尚未取完（enabled 且名单非空时才可能为 true）。 */
  loading: boolean;
  /** 当前名单已取完一轮（成功与否都算完成，失败檔缺行）。 */
  loaded: boolean;
}

export function useDeepLaneOptionWalls(
  top: IntradayTopResponse | null,
  enabled = true,
): DeepLaneOptionWallsView {
  const tickers = useMemo(() => deepLaneWallTickers(top), [top]);
  const tickersKey = tickers.join(',');

  const [wallByTicker, setWallByTicker] = useState<Map<string, OpportunityOptionWallItem>>(
    () => new Map(),
  );
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  // 加载态是纯派生值（启用 + 有名单 + 尚未按当前名单取完），不设并行 state。
  const loading = enabled && tickers.length > 0 && loadedKey !== tickersKey;

  useEffect(() => {
    // 只在启用且名单变化时取数：请求有界（8 檔 → ≤2 次），API 层自带
    // sessionCache 与在途去重，深度层轮换才会重取。
    if (!enabled || tickers.length === 0 || loadedKey === tickersKey) return undefined;
    let cancelled = false;
    const load = async () => {
      const results = await Promise.allSettled(
        chunkSymbols(tickers, WALLS_BATCH_SIZE).map((batch) =>
          fetchOpportunityOptionWalls(batch)),
      );
      if (cancelled) return;
      const next = new Map<string, OpportunityOptionWallItem>();
      for (const result of results) {
        if (result.status !== 'fulfilled') continue;
        for (const item of result.value.items) next.set(item.ticker, item);
      }
      setWallByTicker(next);
      setLoadedKey(tickersKey);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [enabled, tickersKey, loadedKey, tickers]);

  return {
    tickers,
    wallByTicker,
    loading,
    loaded: loadedKey === tickersKey,
  };
}

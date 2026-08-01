/**
 * 共享 EMA 实现：SMA seed（前 period 根 close 的均值，落在第 period 根 bar 上），
 * 之后按 alpha = 2 / (period + 1) 递推。
 *
 * 与后端 `src/opportunities/engine.py` 的 `_ema_last`（statistics.fmean seed +
 * 相同 alpha 递推）语义一致；样本不足一个完整 seed 时返回空序列（fail closed），
 * 不用首 close 或 0 冒充。所有 K 线图 EMA overlay 必须走这里，禁止再各自实现。
 */

export interface EmaSeriesPoint {
  time: number | string;
  value: number;
}

export function computeSmaSeededEmaSeries(
  bars: ReadonlyArray<{ time: number | string; close: number }>,
  period: number,
): EmaSeriesPoint[] {
  if (!Number.isInteger(period) || period <= 0 || bars.length < period) {
    return [];
  }

  const seed = bars
    .slice(0, period)
    .reduce((total, bar) => total + bar.close, 0) / period;
  const multiplier = 2 / (period + 1);
  let value = seed;
  const data: EmaSeriesPoint[] = [{ time: bars[period - 1].time, value }];

  for (let index = period; index < bars.length; index += 1) {
    value += (bars[index].close - value) * multiplier;
    data.push({ time: bars[index].time, value });
  }

  return data;
}

import type React from 'react';
import { useEffect } from 'react';
import { useJournalStore } from '../../stores/journalStore';

const fmtUSD = (n: number | null | undefined): string => {
  if (n === null || n === undefined) return '—';
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const fmtPct = (n: number | null | undefined): string => {
  if (n === null || n === undefined) return '—';
  return `${n.toFixed(1)}%`;
};

export const RealityTestCard: React.FC<{ topN?: number }> = ({ topN = 5 }) => {
  const { realityTest, realityLoading, loadRealityTest } = useJournalStore();

  useEffect(() => {
    void loadRealityTest(topN);
  }, [topN, loadRealityTest]);

  if (realityLoading) {
    return (
      <div className="card-base p-4">
        <h3 className="text-lg font-semibold">Reality Test</h3>
        <p className="text-muted">Loading…</p>
      </div>
    );
  }

  if (!realityTest || realityTest.totalTrades === 0) {
    return (
      <div className="card-base p-4">
        <h3 className="text-lg font-semibold">Reality Test</h3>
        <p className="text-muted">
          尚无已关闭交易。导入 CSV 后将显示"去掉 Top {topN} 的盈亏"。
        </p>
      </div>
    );
  }

  const { totalPnlNet, topNPnlNet, pnlWithoutTopN, topNPctOfTotal, medianPnlNet, totalTrades } =
    realityTest;
  const withoutTopNPositive = pnlWithoutTopN >= 0;

  return (
    <div className="card-base p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-lg font-semibold">Reality Test</h3>
        <span className="text-xs text-muted">n = {totalTrades} closed trades</span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-4 md:grid-cols-4">
        <div>
          <div className="text-xs text-muted">Total net PnL</div>
          <div className={`text-xl font-semibold ${totalPnlNet >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {fmtUSD(totalPnlNet)}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">Top {realityTest.topN} PnL</div>
          <div className="text-xl font-semibold">{fmtUSD(topNPnlNet)}</div>
          <div className="text-xs text-muted">{fmtPct(topNPctOfTotal)} of total</div>
        </div>
        <div>
          <div className="text-xs text-muted">Without Top {realityTest.topN}</div>
          <div
            className={`text-xl font-semibold ${withoutTopNPositive ? 'text-emerald-400' : 'text-red-400'}`}
          >
            {fmtUSD(pnlWithoutTopN)}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">Median per trade</div>
          <div
            className={`text-xl font-semibold ${medianPnlNet !== null && medianPnlNet !== undefined && medianPnlNet >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
          >
            {fmtUSD(medianPnlNet)}
          </div>
        </div>
      </div>

      {topNPctOfTotal !== null && topNPctOfTotal !== undefined && topNPctOfTotal > 80 && (
        <p className="mt-3 rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
          ⚠ Top {realityTest.topN} 交易占总盈亏 {fmtPct(topNPctOfTotal)} —— 你的业绩高度依赖少数几笔爆发。
        </p>
      )}
    </div>
  );
};

export default RealityTestCard;

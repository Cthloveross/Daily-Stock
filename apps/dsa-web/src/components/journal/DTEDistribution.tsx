import type React from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { JournalStatsResponse } from '../../types/journal';

const BUCKET_ORDER = ['0DTE', '1-3DTE', '4-7DTE', '8-30DTE', '30+DTE', 'equity'];

export const DTEDistribution: React.FC<{ stats: JournalStatsResponse | null }> = ({ stats }) => {
  if (!stats) {
    return null;
  }
  const data = BUCKET_ORDER.filter((b) => b in stats.dteDistribution).map((bucket) => ({
    bucket,
    count: stats.dteDistribution[bucket] ?? 0,
    winRate:
      (stats.winRateByBucket[bucket]?.winRate ?? 0) *
      (stats.winRateByBucket[bucket] ? 100 : 0),
    avgPnl: stats.winRateByBucket[bucket]?.avgPnlNet ?? 0,
  }));

  if (data.length === 0 || data.every((d) => d.count === 0)) {
    return (
      <div className="card-base p-4">
        <h3 className="text-lg font-semibold">DTE Distribution</h3>
        <p className="text-muted">无数据。</p>
      </div>
    );
  }

  return (
    <div className="card-base p-4">
      <h3 className="text-lg font-semibold">DTE Distribution</h3>
      <p className="mt-1 text-xs text-muted">
        过去 {stats.windowDays} 天 · 单位：订单数 / 胜率
      </p>
      <div className="mt-4 h-56">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
            <XAxis dataKey="bucket" tick={{ fontSize: 12 }} />
            <YAxis yAxisId="left" />
            <YAxis yAxisId="right" orientation="right" domain={[0, 100]} unit="%" />
            <Tooltip
              formatter={(value, key) => {
                const n = typeof value === 'number' ? value : Number(value);
                if (Number.isNaN(n)) return String(value);
                if (key === 'winRate') return `${n.toFixed(1)}%`;
                if (key === 'avgPnl') return `$${n.toFixed(2)}`;
                return n;
              }}
            />
            <Bar yAxisId="left" dataKey="count" name="Trades" fill="#60a5fa" />
            <Bar yAxisId="right" dataKey="winRate" name="Win rate" fill="#34d399" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default DTEDistribution;

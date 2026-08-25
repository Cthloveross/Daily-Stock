import { useEffect, useMemo, useState } from 'react';
import { Activity } from 'lucide-react';
import {
  fetchEpisodeExcursion,
  fetchExcursionScatter,
} from '../../../api/journalReviewFlow';
import { parseApiError } from '../../../api/error';
import type {
  EpisodeExcursionResponse,
  ExcursionScatterItem,
  ExcursionScatterResponse,
} from '../../../types/journalReviewFlow';
import { EXCURSION_DISCLAIMER } from './reviewHardLines';

/**
 * MAE/MFE 只读诊断（蓝图 17 §三(b)）：单笔偏移条 + 聚合散点
 * （MAE% × 最终 R，按持有结构分层）。只看形态不定参数；
 * 永久免责不可关闭（测试锁定）。标缺显式给原因，绝不画 0。
 */

const STATUS_LABEL: Record<string, string> = {
  ready: '完整覆盖',
  partial: '部分覆盖',
  missing_bars: '标缺（无 5m bar）',
  not_applicable: '不适用',
};

/**
 * U0 锚定 flag 的展示文案：时钟事实（盘前/盘外开仓）与数据事实
 * （入场日无 bar）分开陈述，缺口不谎称盘外开仓。
 */
const U0_FLAG_LABEL: Record<string, string> = {
  premarket_open_first_rth_bar: '盘前开仓：U0 用当日首根 RTH bar',
  entry_outside_rth_next_session_bar: '盘外开仓：U0 用次一时段首根 bar',
  entry_day_bars_missing_next_session_bar:
    '入场日无 5m bar（数据缺口，非盘外开仓）：U0 用其后首根 bar',
};

function pct(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '标缺';
  return `${(value * 100).toFixed(2)}%`;
}

export function ExcursionDisclaimer() {
  return (
    <p
      className="rounded-ds-md border border-warn-strong/30 bg-warn-subtle px-3 py-2 text-caption text-warn-strong"
      data-testid="excursion-disclaimer"
    >
      {EXCURSION_DISCLAIMER}
    </p>
  );
}

function ExcursionBar({ mae, mfe }: { mae: number | null; mfe: number | null }) {
  if (mae == null && mfe == null) return null;
  const maxAbs = Math.max(Math.abs(mae ?? 0), Math.abs(mfe ?? 0), 0.0001);
  const maeWidth = (Math.abs(mae ?? 0) / maxAbs) * 50;
  const mfeWidth = (Math.abs(mfe ?? 0) / maxAbs) * 50;
  return (
    <div className="flex h-4 w-full items-stretch overflow-hidden rounded-full bg-bg-2" aria-hidden>
      <div className="flex w-1/2 justify-end">
        <div className="h-full bg-down-strong/70" style={{ width: `${maeWidth * 2}%` }} />
      </div>
      <div className="w-px bg-border-default" />
      <div className="flex w-1/2 justify-start">
        <div className="h-full bg-up-strong/70" style={{ width: `${mfeWidth * 2}%` }} />
      </div>
    </div>
  );
}

function ScatterPlot({
  items,
  highlightEpisodeId,
}: {
  items: ExcursionScatterItem[];
  highlightEpisodeId?: number;
}) {
  const points = useMemo(
    () => items.filter(
      (item) => item.maeUnderlyingPct != null && item.rMultiple != null,
    ),
    [items],
  );
  if (points.length === 0) {
    return (
      <p className="text-caption text-text-3">
        暂无可绘制的散点（需要 MAE 与 R 同时可得的回合）。标缺回合不会被画成 0。
      </p>
    );
  }
  const width = 520;
  const height = 240;
  const pad = 36;
  const maeValues = points.map((item) => Math.abs(item.maeUnderlyingPct ?? 0));
  const rValues = points.map((item) => item.rMultiple ?? 0);
  const maeMax = Math.max(...maeValues, 0.001);
  const rMin = Math.min(...rValues, 0);
  const rMax = Math.max(...rValues, 0.001);
  const x = (mae: number) => pad + (Math.abs(mae) / maeMax) * (width - pad * 2);
  const y = (r: number) => height - pad - ((r - rMin) / (rMax - rMin)) * (height - pad * 2);
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full"
      role="img"
      aria-label="MAE 与最终 R 的只读诊断散点"
    >
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="currentColor" strokeOpacity={0.25} />
      <line x1={pad} y1={pad} x2={pad} y2={height - pad} stroke="currentColor" strokeOpacity={0.25} />
      {rMin < 0 && rMax > 0 && (
        <line x1={pad} y1={y(0)} x2={width - pad} y2={y(0)} stroke="currentColor" strokeOpacity={0.15} strokeDasharray="4 4" />
      )}
      <text x={width - pad} y={height - pad + 14} textAnchor="end" fontSize="10" fill="currentColor" fillOpacity={0.6}>
        |MAE|（标的%，最大 {(maeMax * 100).toFixed(1)}%）
      </text>
      <text x={pad} y={pad - 8} fontSize="10" fill="currentColor" fillOpacity={0.6}>
        最终 R（净盈亏/权利金）
      </text>
      {points.map((item) => {
        const cx = x(item.maeUnderlyingPct ?? 0);
        const cy = y(item.rMultiple ?? 0);
        const highlighted = item.episodeId === highlightEpisodeId;
        const overnight = item.holdStructure === 'overnight';
        return (
          <g key={item.episodeId}>
            {overnight ? (
              <rect
                x={cx - 3}
                y={cy - 3}
                width={6}
                height={6}
                fill={highlighted ? '#f5d06f' : 'currentColor'}
                fillOpacity={highlighted ? 1 : 0.45}
              />
            ) : (
              <circle
                cx={cx}
                cy={cy}
                r={highlighted ? 5 : 3}
                fill={highlighted ? '#f5d06f' : 'currentColor'}
                fillOpacity={highlighted ? 1 : 0.45}
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function ExcursionDiagnostics({
  episodeId,
  buildId,
}: {
  episodeId: number;
  buildId: number;
}) {
  const [record, setRecord] = useState<EpisodeExcursionResponse | null>(null);
  const [scatter, setScatter] = useState<ExcursionScatterResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setRecord(null);
      setScatter(null);
      setError(null);
    });
    void Promise.all([
      fetchEpisodeExcursion(episodeId, buildId),
      fetchExcursionScatter(),
    ])
      .then(([excursionResponse, scatterResponse]) => {
        if (cancelled) return;
        setRecord(excursionResponse);
        setScatter(scatterResponse);
      })
      .catch((reason) => {
        if (!cancelled) setError(parseApiError(reason).message);
      });
    return () => {
      cancelled = true;
    };
  }, [episodeId, buildId]);

  const excursion = record?.excursion ?? null;

  return (
    <section className="card-base overflow-hidden" aria-label="偏移诊断">
      <div className="border-b border-subtle px-4 py-3">
        <div className="flex items-center gap-2 text-label uppercase tracking-label text-text-3">
          <Activity size={14} /> MAE / MFE 偏移诊断（只读）
        </div>
        <p
          className="mt-1 text-caption text-text-3"
          title="出场 bar 溢出：窗口按 bar 开始时间截取，平仓时刻所在的整根 bar 计入偏移，平仓之后至多约 5 分钟的行情也会被计入。"
        >
          标的 5m bar 近似口径——不是期权价格路径，也不做 BS/delta 折算。RTH bar
          only，盘后路径不可见；隔夜跳空由次日首根 bar 体现。出场 bar
          溢出：平仓所在整根 bar 计入，平仓后至多约 5 分钟的行情也被计入。
        </p>
      </div>
      <div className="space-y-3 p-4">
        <ExcursionDisclaimer />
        {error && (
          <p className="text-caption text-down-strong" role="alert">偏移诊断读取失败：{error}</p>
        )}
        {record && !excursion && (
          <div className="rounded-ds-md border border-dashed border-default px-4 py-4 text-body-sm text-text-3">
            标缺：{record.missingReason ?? '该回合尚无偏移记录'}
          </div>
        )}
        {excursion && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-subtle bg-bg-2 px-2 py-1 text-caption text-text-2">
                {STATUS_LABEL[excursion.status] ?? excursion.status}
              </span>
              <span className="font-mono text-mono-xs text-text-3">
                {excursion.barsUsed} 根 5m bar · {excursion.codeVersion}
              </span>
              {excursion.u0Flag && (
                <span className="rounded-full border border-warn-strong/30 bg-warn-subtle px-2 py-1 text-caption text-warn-strong">
                  {U0_FLAG_LABEL[excursion.u0Flag] ?? excursion.u0Flag}
                </span>
              )}
            </div>
            {excursion.statusReason && (
              <p className="text-caption text-warn-strong">{excursion.statusReason}</p>
            )}
            {(excursion.status === 'ready' || excursion.status === 'partial') && (
              <>
                <ExcursionBar
                  mae={excursion.maeUnderlyingPct}
                  mfe={excursion.mfeUnderlyingPct}
                />
                <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <div>
                    <dt className="text-caption text-text-3">MAE（标的）</dt>
                    <dd className="font-mono text-mono-sm text-down-strong">
                      {pct(excursion.maeUnderlyingPct)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-caption text-text-3">MFE（标的）</dt>
                    <dd className="font-mono text-mono-sm text-up-strong">
                      {pct(excursion.mfeUnderlyingPct)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-caption text-text-3">MAE（ATR14）</dt>
                    <dd className="font-mono text-mono-sm text-text-1">
                      {excursion.maeAtr != null ? excursion.maeAtr.toFixed(2) : '标缺'}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-caption text-text-3">先 MAE 后 MFE</dt>
                    <dd className="font-mono text-mono-sm text-text-1">
                      {excursion.maeBeforeMfe == null
                        ? '不适用'
                        : excursion.maeBeforeMfe
                          ? '是'
                          : '否'}
                    </dd>
                  </div>
                </dl>
              </>
            )}
          </div>
        )}
        <div className="border-t border-subtle pt-3">
          <h3 className="text-body-sm font-medium text-text-1">
            聚合散点：|MAE| × 最终 R（按持有结构分层）
          </h3>
          <p className="mt-1 text-caption text-text-3">
            ● 日内 · ■ 隔夜 · 当前回合高亮。只看形态，不由此推导任何参数。
          </p>
          <div className="mt-2 text-text-2">
            {scatter ? (
              <ScatterPlot
                items={scatter.items}
                highlightEpisodeId={episodeId}
              />
            ) : (
              !error && <p className="text-caption text-text-3">正在读取散点数据…</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { HelpCircle, X } from 'lucide-react';
import { cn } from '../../utils/cn';

/**
 * Floating help popover anchored to a "?" button. Contents document how each
 * of the 6 Regime Score dimensions is computed — mirrors the formulas in
 * `src/regime/scorers.py`. Kept as a self-contained component so the
 * RegimePage stays readable.
 */

interface DimDoc {
  label: string;
  range: string;
  summary: string;
  rules: { when: string; delta: string }[];
}

const DIMS: DimDoc[] = [
  {
    label: 'Direction',
    range: '0 .. +30',
    summary: 'SPY 相对 MA20 / MA50 的趋势 + 近 5 日动能（对应 F&G 的 Market Momentum 子项）',
    rules: [
      { when: 'close > MA20', delta: '+10' },
      { when: 'MA20 > MA50', delta: '+10' },
      { when: '近 5 日涨跌幅 ×2，截到 [-10, +10]', delta: '±10' },
    ],
  },
  {
    label: 'Volatility',
    range: '-15 .. +20',
    summary: 'VIX 区间分档 + 快速抬升惩罚（对应 F&G 的 Market Volatility 子项）',
    rules: [
      { when: 'VIX < 15', delta: '+20' },
      { when: '15 ≤ VIX < 20', delta: '+10' },
      { when: '20 ≤ VIX < 25', delta: '0' },
      { when: '25 ≤ VIX < 30', delta: '−10' },
      { when: 'VIX ≥ 30', delta: '−15' },
      { when: 'VIX 5 日涨幅 > 25%', delta: '再 −5' },
    ],
  },
  {
    label: 'Macro',
    range: '-50 .. 0',
    summary: '宏观事件日惩罚（Finnhub 日历 + 关税头条）',
    rules: [
      { when: 'FOMC 利率决议日', delta: '−30' },
      { when: 'CPI 发布日', delta: '−20' },
      { when: 'NFP 非农日', delta: '−15' },
      { when: '自选 ≥ 3 家当日财报', delta: '−15' },
      { when: '自选 ≥ 1 家当日财报', delta: '−5' },
      { when: '今日有关税头条', delta: '−10' },
    ],
  },
  {
    label: 'Sector',
    range: '-5 .. +15',
    summary: '11 个 S&P 板块 ETF 中站上 MA20 的个数（广度）',
    rules: [
      { when: '分值 = round(N/11 × 20 − 5)', delta: '线性' },
      { when: '防御板块领先且分值 > 0', delta: '再 −3' },
    ],
  },
  {
    label: 'Prev Day',
    range: '-2 .. +13',
    summary: '昨日收盘在日内高点位置 + 日内振幅',
    rules: [
      { when: '昨收 ≥ 日高 90%（贴近高点）', delta: '+10' },
      { when: '昨收 70–90% 之间', delta: '+5' },
      { when: '昨收 ≤ 日高 30%（贴近低点）', delta: '−2' },
      { when: '昨日振幅 > 2%', delta: '+3' },
    ],
  },
  {
    label: 'Premarket',
    range: '0 .. +20',
    summary: '盘前 SPY + 自选股涨跌人数',
    rules: [
      { when: 'SPY 盘前涨幅 ≥ 0.3%', delta: '+8' },
      { when: 'SPY 盘前涨幅 0–0.3%', delta: '+3' },
      { when: 'SPY 盘前跌幅 ≥ 0.5%', delta: '−5' },
      { when: '盘前 +5% 的自选 × 2（封顶 10）', delta: '+2/个' },
      { when: '盘前 −5% 的自选 × 1（封顶 5）', delta: '−1/个' },
    ],
  },
];

export const ContributionInfo: React.FC<{ className?: string }> = ({ className }) => {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  return (
    <div ref={wrapperRef} className={cn('relative inline-block', className)}>
      <button
        type="button"
        aria-label="How are contributions computed?"
        title="如何计算每个维度？"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-text-3 hover:bg-bg-2 hover:text-text-1"
      >
        <HelpCircle size={14} strokeWidth={1.5} />
      </button>

      {open && (
        <div
          role="dialog"
          className="absolute left-0 top-7 z-popover w-[440px] max-w-[calc(100vw-2rem)] rounded-ds-md border border-default bg-bg-2 p-4 shadow-md"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between">
            <div className="text-label uppercase text-text-3">Contributions · 计算说明</div>
            <button
              type="button"
              aria-label="Close"
              onClick={() => setOpen(false)}
              className="inline-flex h-6 w-6 items-center justify-center rounded-ds-sm text-text-3 hover:bg-bg-3 hover:text-text-1"
            >
              <X size={14} strokeWidth={1.5} />
            </button>
          </div>
          <p className="mt-2 text-caption text-text-2">
            六个维度是纯函数打分，合计理论区间 <span className="font-mono">−72 … +98</span>，分类器再
            clamp 到 −100…+100。设计思路参考 CNN 的{' '}
            <a
              href="https://edition.cnn.com/markets/fear-and-greed"
              target="_blank"
              rel="noreferrer noopener"
              className="text-accent underline-offset-2 hover:underline"
            >
              Fear &amp; Greed Index
            </a>
            ：把多组市场健康指标折叠成单一刻度，便于"一眼判"。源码见{' '}
            <code className="font-mono text-text-1">src/regime/scorers.py</code>。
          </p>

          <div className="mt-3 space-y-3">
            {DIMS.map((d) => (
              <div key={d.label} className="rounded-ds-sm border border-subtle bg-bg-1 p-3">
                <div className="flex items-baseline justify-between">
                  <div className="text-body text-text-1">{d.label}</div>
                  <div className="font-mono text-mono-xs text-text-3">{d.range}</div>
                </div>
                <div className="mt-1 text-caption text-text-2">{d.summary}</div>
                <ul className="mt-2 space-y-1 text-caption text-text-2">
                  {d.rules.map((r, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="w-[14ch] shrink-0 font-mono text-text-1">{r.delta}</span>
                      <span className="flex-1">{r.when}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default ContributionInfo;

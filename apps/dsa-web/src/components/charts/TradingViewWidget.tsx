import type React from 'react';
import { useEffect, useId, useMemo, useRef } from 'react';

interface Props {
  symbol: string;
  interval?: string;
  theme?: 'light' | 'dark';
  studies?: string[];
  height?: number;
}

/**
 * TradingView Advanced Chart widget (iframe-free version that uses
 * the `tv.js` loader). The widget mounts into a div and draws
 * autosize to the container.
 */
interface TradingViewWidgetCtor {
  new (opts: Record<string, unknown>): unknown;
}
interface TradingViewNamespace {
  widget: TradingViewWidgetCtor;
}

declare global {
  interface Window {
    TradingView?: TradingViewNamespace;
  }
}

const SCRIPT_URL = 'https://s3.tradingview.com/tv.js';

const loadScript = (): Promise<void> =>
  new Promise((resolve, reject) => {
    if (typeof document === 'undefined') {
      resolve();
      return;
    }
    if (window.TradingView) {
      resolve();
      return;
    }
    const existing = document.querySelector(`script[src="${SCRIPT_URL}"]`);
    if (existing) {
      existing.addEventListener('load', () => resolve());
      existing.addEventListener('error', () => reject(new Error('tv.js failed to load')));
      return;
    }
    const s = document.createElement('script');
    s.src = SCRIPT_URL;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('tv.js failed to load'));
    document.body.appendChild(s);
  });

export const TradingViewWidget: React.FC<Props> = ({
  symbol,
  interval = '5',
  theme = 'dark',
  studies = ['MASimple@tv-basicstudies', 'VWAP@tv-basicstudies'],
  height = 480,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const reactId = useId();
  const containerId = useRef(`tv_chart_${reactId.replace(/[^a-zA-Z0-9]/g, '')}`);
  // Keep a stable cache-key for `studies`; consumers often pass an inline
  // literal which would create a fresh array on every render and thrash the
  // widget unnecessarily.
  const studiesKey = useMemo(() => studies.join('|'), [studies]);

  useEffect(() => {
    let cancelled = false;
    loadScript()
      .then(() => {
        if (cancelled || !window.TradingView || !containerRef.current) return;
        // Wipe any prior mount.
        containerRef.current.innerHTML = '';
        const inner = document.createElement('div');
        inner.id = containerId.current;
        inner.style.height = '100%';
        containerRef.current.appendChild(inner);
        new window.TradingView.widget({
          container_id: containerId.current,
          symbol,
          interval,
          theme,
          studies,
          autosize: true,
          hide_side_toolbar: false,
          timezone: 'America/New_York',
        });
      })
      .catch((err) => {
        if (cancelled) return;
        console.warn('TradingView widget failed to mount', err);
      });
    return () => {
      cancelled = true;
    };
    // studiesKey stands in for the array reference so that passing a new
    // literal array of the same values does not re-mount the widget.
  }, [symbol, interval, theme, studiesKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="card-base overflow-hidden p-2">
      <div
        ref={containerRef}
        style={{ height, width: '100%' }}
        className="rounded bg-base-subtle"
      />
      <p className="px-2 pt-2 text-xs text-muted">
        TradingView Advanced Chart · symbol={symbol} · interval={interval}
      </p>
    </div>
  );
};

export default TradingViewWidget;

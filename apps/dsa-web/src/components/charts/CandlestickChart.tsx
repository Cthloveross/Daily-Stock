import type React from 'react';
import { useEffect, useRef } from 'react';
import {
  createChart,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type CandlestickData,
  type HistogramData,
  type Time,
} from 'lightweight-charts';

export interface Candle {
  time: number | string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface MAOverlay {
  period: number;
  color: string;
  data: { time: number | string; value: number }[];
}

export interface CandlestickChartProps {
  data: Candle[];
  overlays?: MAOverlay[];
  height?: number;
  className?: string;
}

export const CandlestickChart: React.FC<CandlestickChartProps> = ({
  data,
  overlays = [],
  height = 500,
  className,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { color: 'transparent' },
        textColor: '#a5a5ad',
        fontFamily: 'Geist Mono, ui-monospace, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1d1e22', style: LineStyle.Dashed },
        horzLines: { color: '#1d1e22', style: LineStyle.Dashed },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#6f6f78', style: LineStyle.Dashed, width: 1 },
        horzLine: { color: '#6f6f78', style: LineStyle.Dashed, width: 1 },
      },
      rightPriceScale: { borderColor: '#1d1e22' },
      timeScale: { borderColor: '#1d1e22', timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    const candleSeries: ISeriesApi<'Candlestick'> = chart.addCandlestickSeries({
      upColor: 'transparent',
      downColor: '#f85149',
      borderUpColor: '#3fb950',
      borderDownColor: '#f85149',
      wickUpColor: '#3fb950',
      wickDownColor: '#f85149',
    });
    candleSeries.setData(data as CandlestickData<Time>[]);

    if (data.some((d) => typeof d.volume === 'number')) {
      const volSeries = chart.addHistogramSeries({
        priceScaleId: 'vol',
        priceFormat: { type: 'volume' },
        color: '#238636',
      });
      chart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.75, bottom: 0 },
      });
      const volData = data.map((d) => ({
        time: d.time as Time,
        value: d.volume ?? 0,
        color: d.close >= d.open ? 'rgba(63,185,80,0.4)' : 'rgba(248,81,73,0.4)',
      })) as HistogramData<Time>[];
      volSeries.setData(volData);
    }

    for (const ov of overlays) {
      const line = chart.addLineSeries({
        color: ov.color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      line.setData(ov.data as LineData<Time>[]);
    }

    const resize = () => {
      if (el) chart.applyOptions({ width: el.clientWidth });
    };
    window.addEventListener('resize', resize);

    chart.timeScale().fitContent();

    return () => {
      window.removeEventListener('resize', resize);
      chart.remove();
      chartRef.current = null;
    };
  }, [data, overlays, height]);

  return <div ref={containerRef} className={className} style={{ height, width: '100%' }} />;
};

export default CandlestickChart;

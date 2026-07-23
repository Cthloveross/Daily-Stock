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
  type SeriesMarker,
  type TickMarkType,
  type Time,
} from 'lightweight-charts';
import { formatChartTickInTimeZone, formatChartTimeInTimeZone } from './chartTimeFormatting';

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
  label?: string;
  data: { time: number | string; value: number }[];
}

export interface CandleMarker {
  id: string;
  time: number | string;
  position: 'aboveBar' | 'belowBar' | 'inBar';
  shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown';
  color: string;
  text?: string;
  size?: number;
}

export interface CandlestickChartProps {
  data: Candle[];
  overlays?: MAOverlay[];
  markers?: CandleMarker[];
  focusedTime?: number | string | null;
  onTimeSelect?: (time: number | string, markerId?: string) => void;
  height?: number;
  className?: string;
  timeZone?: string;
  timeZoneLabel?: string;
}

export const CandlestickChart: React.FC<CandlestickChartProps> = ({
  data,
  overlays = [],
  markers = [],
  focusedTime,
  onTimeSelect,
  height = 500,
  className,
  timeZone,
  timeZoneLabel,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markersRef = useRef(markers);
  const onTimeSelectRef = useRef(onTimeSelect);

  useEffect(() => {
    markersRef.current = markers;
  }, [markers]);

  useEffect(() => {
    onTimeSelectRef.current = onTimeSelect;
  }, [onTimeSelect]);

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
      timeScale: {
        borderColor: '#1d1e22',
        timeVisible: true,
        secondsVisible: false,
        ...(timeZone ? {
          tickMarkFormatter: (time: Time, type: TickMarkType) => (
            formatChartTickInTimeZone(time, type, timeZone)
          ),
        } : {}),
      },
      ...(timeZone ? {
        localization: {
          timeFormatter: (time: Time) => formatChartTimeInTimeZone(
            time,
            timeZone,
            timeZoneLabel ?? timeZone,
          ),
        },
      } : {}),
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
    candleSeries.setMarkers(markersRef.current as SeriesMarker<Time>[]);
    candleSeriesRef.current = candleSeries;

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
        title: ov.label,
        color: ov.color,
        lineWidth: 2,
        priceScaleId: 'right',
        priceLineVisible: false,
        lastValueVisible: false,
      });
      line.setData(ov.data as LineData<Time>[]);
    }

    const resize = () => {
      if (el) chart.applyOptions({ width: el.clientWidth });
    };
    window.addEventListener('resize', resize);

    const handleClick = (param: { time?: Time; hoveredObjectId?: unknown }) => {
      if (param.time == null || !onTimeSelectRef.current) return;
      const markerId = typeof param.hoveredObjectId === 'string'
        ? param.hoveredObjectId
        : undefined;
      onTimeSelectRef.current(param.time as number | string, markerId);
    };
    chart.subscribeClick(handleClick);

    chart.timeScale().fitContent();

    return () => {
      window.removeEventListener('resize', resize);
      chart.unsubscribeClick(handleClick);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, [data, overlays, height, timeZone, timeZoneLabel]);

  useEffect(() => {
    candleSeriesRef.current?.setMarkers(markers as SeriesMarker<Time>[]);
  }, [markers]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!chart || !series || focusedTime == null || data.length === 0) return;
    const index = data.findIndex((candle) => String(candle.time) === String(focusedTime));
    if (index < 0) return;
    const candle = data[index];
    chart.setCrosshairPosition(candle.close, candle.time as Time, series);
    if (data.length > 60) {
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, index - 30),
        to: Math.min(data.length - 1, index + 30),
      });
    }
  }, [data, focusedTime]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ height, width: '100%' }}
      aria-label="K 线图"
    />
  );
};

export default CandlestickChart;

import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { Activity } from 'lucide-react';
import apiClient from '../../api';
import { cn } from '../../utils/cn';

interface HealthLayer {
  layer: string;
  state: 'ok' | 'degraded' | 'down' | 'disabled' | 'unknown';
  detail: string;
  as_of?: string;
}

interface HealthLayersResponse {
  schema_version: string;
  generated_at: string;
  overall: 'ok' | 'degraded' | 'down';
  layers: HealthLayer[];
}

const LAYER_LABELS: Record<string, string> = {
  api_process: 'API 进程',
  opend_tcp: 'OpenD 端口',
  moomoo_sdk: 'Moomoo SDK',
  journal_refresh_config: 'Journal 刷新配置',
  premarket_publication: '盘前官方发布',
  outcome_maintenance: '结果回填维护',
};

const STATE_STYLES: Record<HealthLayer['state'], string> = {
  ok: 'bg-up-strong',
  degraded: 'bg-warn-strong',
  unknown: 'bg-warn-strong',
  down: 'bg-down-strong',
  disabled: 'bg-text-3',
};

const STATE_LABELS: Record<HealthLayer['state'], string> = {
  ok: '正常',
  degraded: '降级',
  unknown: '未知',
  down: '不可用',
  disabled: '未启用',
};

/**
 * 分层健康弹层：点击时按需拉取 /system/health-layers，展示每个健康域的
 * 独立状态。数据是只读探测结果；OpenD 端口可达不代表已登录或有权限。
 */
export const SystemHealthPopover: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<HealthLayersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (!next) return;
    setLoading(true);
    setError(null);
    apiClient.get<HealthLayersResponse>('/api/v1/system/health-layers', { timeout: 10_000 })
      .then((response) => { if (aliveRef.current) setData(response.data); })
      .catch(() => { if (aliveRef.current) setError('健康状态读取失败'); })
      .finally(() => { if (aliveRef.current) setLoading(false); });
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="系统健康分层"
        aria-expanded={open}
        onClick={toggle}
        className="inline-flex items-center gap-1 rounded-full border border-subtle bg-bg-1 px-2 py-0.5 text-caption text-text-3 hover:bg-bg-2 hover:text-text-2"
      >
        <Activity size={11} strokeWidth={2} />
        健康
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="系统健康分层详情"
          className="absolute right-0 top-full z-50 mt-1 w-80 border border-subtle bg-bg-1 p-3 shadow-lg"
        >
          {loading && <p className="text-caption text-text-3">读取中…</p>}
          {error && <p className="text-caption text-warn-strong">{error}</p>}
          {data && (
            <ul className="space-y-2">
              {data.layers.map((layer) => (
                <li key={layer.layer} className="flex items-start gap-2 text-caption">
                  <span
                    aria-hidden
                    className={cn('mt-1 h-2 w-2 shrink-0 rounded-full', STATE_STYLES[layer.state] ?? 'bg-text-3')}
                  />
                  <span className="min-w-0">
                    <span className="font-medium text-text-2">
                      {LAYER_LABELS[layer.layer] ?? layer.layer}
                    </span>
                    <span className="ml-1 text-text-3">{STATE_LABELS[layer.state] ?? layer.state}</span>
                    <span className="block break-words text-text-3">{layer.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};

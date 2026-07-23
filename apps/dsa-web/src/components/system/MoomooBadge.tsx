import type React from 'react';
import { useEffect, useState } from 'react';
import { LockKeyhole, Power, AlertTriangle } from 'lucide-react';
import apiClient from '../../api';
import { toCamelCase } from '../../api/utils';
import { Tooltip } from '../common/Tooltip';
import { cn } from '../../utils/cn';

interface MoomooStatus {
  enabled: boolean;
  sdkInstalled: boolean;
  connected: boolean;
  host: string;
  port: number;
  trdEnv: string;
  sdkVersion?: string | null;
  probeLevel?: string | null;
  readOnly?: boolean;
  message?: string | null;
}

const POLL_INTERVAL_MS = 30_000;

export const MoomooBadge: React.FC = () => {
  const [status, setStatus] = useState<MoomooStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const { data } = await apiClient.get('/api/v1/system/moomoo-status', {
          timeout: 8000,
        });
        if (cancelled) return;
        setStatus(toCamelCase<MoomooStatus>(data));
      } catch {
        if (cancelled) return;
        setStatus(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void probe();
    const id = window.setInterval(() => void probe(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (loading || !status) return null;

  // Three states with progressively-degraded colour.  `connected` is a
  // bounded TCP reachability check, not proof that trade history is synced.
  //   reachable → green, explicitly read-only
  //   enabled+offline → amber, "Moomoo offline" (open OpenD)
  //   disabled  → grey, "yfinance"
  const reachable = status.enabled && status.sdkInstalled && status.connected;
  const halfBaked = status.enabled && !status.connected;

  if (reachable) {
    const detail =
      `Moomoo OpenD 端口可达 · 项目只读，不解锁或下单` +
      ` · 端口可达不代表交割单已经同步` +
      (status.sdkVersion ? ` · SDK ${status.sdkVersion}` : '');
    return (
      <Tooltip content={detail} side="bottom" focusable>
        <span
          aria-label={detail}
          className={cn(
            'inline-flex items-center gap-1 rounded-full border border-up-strong/40 bg-up-strong/10 px-2 py-0.5 text-caption text-up-strong',
          )}
        >
          <LockKeyhole size={11} strokeWidth={2} />
          Moomoo 可达 · 只读
        </span>
      </Tooltip>
    );
  }

  if (halfBaked) {
    const detail = status.message ?? 'Moomoo enabled but OpenD not reachable';
    return (
      <Tooltip content={detail} side="bottom" focusable>
        <span
          aria-label={detail}
          className="inline-flex items-center gap-1 rounded-full border border-warn-strong/40 bg-warn-strong/10 px-2 py-0.5 text-caption text-warn-strong"
        >
          <AlertTriangle size={11} strokeWidth={2} />
          Moomoo offline
        </span>
      </Tooltip>
    );
  }

  const detail = status.message ?? 'Moomoo disabled — using yfinance';
  return (
    <Tooltip content={detail} side="bottom" focusable>
      <span
        aria-label={detail}
        className="inline-flex items-center gap-1 rounded-full border border-subtle bg-bg-2 px-2 py-0.5 text-caption text-text-3"
      >
        <Power size={11} strokeWidth={2} />
        yfinance
      </span>
    </Tooltip>
  );
};

export default MoomooBadge;

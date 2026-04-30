import type React from 'react';
import { useEffect, useState } from 'react';
import { Activity, Power, AlertTriangle } from 'lucide-react';
import apiClient from '../../api';
import { toCamelCase } from '../../api/utils';
import { cn } from '../../utils/cn';

interface MoomooStatus {
  enabled: boolean;
  sdkInstalled: boolean;
  connected: boolean;
  host: string;
  port: number;
  trdEnv: string;
  sdkVersion?: string | null;
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

  // Three states with progressively-degraded colour:
  //   live      → green, "Moomoo Live"
  //   enabled+offline → amber, "Moomoo offline" (open OpenD)
  //   disabled  → grey, "yfinance"
  const live = status.enabled && status.sdkInstalled && status.connected;
  const halfBaked = status.enabled && !status.connected;

  if (live) {
    const isLive = status.trdEnv === 'LIVE';
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-caption',
          isLive
            ? 'border-down-strong/40 bg-down-strong/10 text-down-strong'
            : 'border-up-strong/40 bg-up-strong/10 text-up-strong',
        )}
        title={
          `Moomoo OpenD live · ${status.host}:${status.port}` +
          ` · trd_env=${status.trdEnv}` +
          (status.sdkVersion ? ` · SDK ${status.sdkVersion}` : '')
        }
      >
        <Activity size={11} strokeWidth={2} />
        {isLive ? 'MOOMOO LIVE 🔴' : 'MOOMOO LIVE'}
      </span>
    );
  }

  if (halfBaked) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full border border-warn-strong/40 bg-warn-strong/10 px-2 py-0.5 text-caption text-warn-strong"
        title={status.message ?? 'Moomoo enabled but OpenD not reachable'}
      >
        <AlertTriangle size={11} strokeWidth={2} />
        Moomoo offline
      </span>
    );
  }

  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-subtle bg-bg-2 px-2 py-0.5 text-caption text-text-3"
      title={status.message ?? 'Moomoo disabled — using yfinance'}
    >
      <Power size={11} strokeWidth={2} />
      yfinance
    </span>
  );
};

export default MoomooBadge;

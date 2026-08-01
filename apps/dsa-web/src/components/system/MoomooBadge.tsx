import type React from 'react';
import { LockKeyhole, Power, AlertTriangle } from 'lucide-react';
import { Tooltip } from '../common/Tooltip';
import { cn } from '../../utils/cn';
import { useMoomooStatusMonitor } from './moomooStatusMonitor';

export const MoomooBadge: React.FC = () => {
  const { status, loading, unavailable } = useMoomooStatusMonitor();

  if (loading) return null;

  if (unavailable || !status) {
    const detail = (
      '无法确认 Moomoo OpenD 状态；页面重新可见或网络恢复时会自动重试'
    );
    return (
      <Tooltip content={detail} side="bottom" focusable>
        <span
          aria-label={detail}
          className="inline-flex items-center gap-1 rounded-full border border-warn-strong/40 bg-warn-strong/10 px-2 py-0.5 text-caption text-warn-strong"
        >
          <AlertTriangle size={11} strokeWidth={2} />
          Moomoo 状态未知
        </span>
      </Tooltip>
    );
  }

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

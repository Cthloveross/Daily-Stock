import { useSyncExternalStore } from 'react';
import apiClient from '../../api';
import { toCamelCase } from '../../api/utils';

export interface MoomooStatus {
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

export interface MoomooStatusSnapshot {
  status: MoomooStatus | null;
  loading: boolean;
  unavailable: boolean;
  lastCheckedAt: number | null;
}

type SharedStatusEnvelope = {
  version: 1;
  checkedAt: number;
  nextProbeAt: number;
  failureCount: number;
  outcome: 'success' | 'error';
  status: MoomooStatus | null;
};

type SharedLease = {
  owner: string;
  expiresAt: number;
};

export const MOOMOO_STATUS_STORAGE_KEY = 'dsa-moomoo-status-monitor-v1';
const LEASE_STORAGE_KEY = `${MOOMOO_STATUS_STORAGE_KEY}:lease`;
const BROADCAST_CHANNEL_NAME = MOOMOO_STATUS_STORAGE_KEY;
const WEB_LOCK_NAME = MOOMOO_STATUS_STORAGE_KEY;

// A TCP reachability badge does not need tick-level polling. One successful
// probe per browser profile per minute is fresh enough for the TopBar, while
// visibility/online events still trigger prompt recovery checks.
const HEALTHY_POLL_INTERVAL_MS = 60_000;
const MAX_SUCCESS_AGE_MS = 75_000;
const RETRY_DELAYS_MS = [15_000, 30_000, 60_000, 120_000] as const;
const BUSY_RECHECK_MS = 1_500;
const FALLBACK_LEASE_MS = 12_000;

const INITIAL_SNAPSHOT: MoomooStatusSnapshot = {
  status: null,
  loading: true,
  unavailable: false,
  lastCheckedAt: null,
};

const subscribers = new Set<() => void>();
const instanceId = globalThis.crypto?.randomUUID?.()
  ?? `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;

let snapshot: MoomooStatusSnapshot = INITIAL_SNAPSHOT;
let started = false;
let timerId: number | null = null;
let activeProbe: Promise<void> | null = null;
let broadcastChannel: BroadcastChannel | null = null;

function isVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden';
}

function getStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isMoomooStatus(value: unknown): value is MoomooStatus {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<MoomooStatus>;
  return (
    typeof candidate.enabled === 'boolean'
    && typeof candidate.sdkInstalled === 'boolean'
    && typeof candidate.connected === 'boolean'
    && typeof candidate.host === 'string'
    && typeof candidate.port === 'number'
    && Number.isFinite(candidate.port)
    && typeof candidate.trdEnv === 'string'
  );
}

function parseEnvelope(raw: string | null): SharedStatusEnvelope | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<SharedStatusEnvelope>;
    if (
      value.version !== 1
      || typeof value.checkedAt !== 'number'
      || !Number.isFinite(value.checkedAt)
      || typeof value.nextProbeAt !== 'number'
      || !Number.isFinite(value.nextProbeAt)
      || typeof value.failureCount !== 'number'
      || !Number.isFinite(value.failureCount)
      || (value.outcome !== 'success' && value.outcome !== 'error')
    ) {
      return null;
    }
    if (value.outcome === 'success' && !isMoomooStatus(value.status)) {
      return null;
    }
    if (value.outcome === 'error' && value.status !== null) {
      return null;
    }
    return value as SharedStatusEnvelope;
  } catch {
    return null;
  }
}

function readSharedEnvelope(): SharedStatusEnvelope | null {
  return parseEnvelope(getStorage()?.getItem(MOOMOO_STATUS_STORAGE_KEY) ?? null);
}

function emit(): void {
  subscribers.forEach((listener) => listener());
}

function setSnapshot(next: MoomooStatusSnapshot): void {
  snapshot = next;
  emit();
}

function applyEnvelope(envelope: SharedStatusEnvelope): void {
  if (
    snapshot.lastCheckedAt !== null
    && envelope.checkedAt < snapshot.lastCheckedAt
  ) {
    return;
  }

  const successIsFresh = (
    envelope.outcome === 'success'
    && Date.now() - envelope.checkedAt <= MAX_SUCCESS_AGE_MS
  );
  setSnapshot({
    status: successIsFresh ? envelope.status : null,
    loading: false,
    unavailable: envelope.outcome === 'error' || !successIsFresh,
    lastCheckedAt: envelope.checkedAt,
  });
}

function expireCurrentSuccessIfNeeded(): void {
  if (
    snapshot.status
    && snapshot.lastCheckedAt !== null
    && Date.now() - snapshot.lastCheckedAt > MAX_SUCCESS_AGE_MS
  ) {
    setSnapshot({
      status: null,
      loading: false,
      unavailable: true,
      lastCheckedAt: snapshot.lastCheckedAt,
    });
  }
}

function writeSharedEnvelope(envelope: SharedStatusEnvelope): void {
  try {
    getStorage()?.setItem(MOOMOO_STATUS_STORAGE_KEY, JSON.stringify(envelope));
  } catch {
    // In-memory subscribers still receive the result when storage is blocked.
  }
  try {
    broadcastChannel?.postMessage(envelope);
  } catch {
    // Storage events remain the fallback for cross-tab propagation.
  }
  applyEnvelope(envelope);
}

function clearTimer(): void {
  if (timerId !== null) {
    window.clearTimeout(timerId);
    timerId = null;
  }
}

function scheduleAt(timestamp: number): void {
  clearTimer();
  if (!started || !isVisible()) return;
  timerId = window.setTimeout(() => {
    timerId = null;
    void requestProbe();
  }, Math.max(0, timestamp - Date.now()));
}

function scheduleFromEnvelope(envelope: SharedStatusEnvelope): void {
  if (envelope.outcome === 'success') {
    scheduleAt(Math.min(
      envelope.nextProbeAt,
      envelope.checkedAt + MAX_SUCCESS_AGE_MS,
    ));
    return;
  }
  scheduleAt(envelope.nextProbeAt);
}

function retryDelay(failureCount: number): number {
  const index = Math.min(
    Math.max(0, Math.trunc(failureCount) - 1),
    RETRY_DELAYS_MS.length - 1,
  );
  return RETRY_DELAYS_MS[index];
}

function parseLease(raw: string | null): SharedLease | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<SharedLease>;
    if (
      typeof value.owner !== 'string'
      || typeof value.expiresAt !== 'number'
      || !Number.isFinite(value.expiresAt)
    ) {
      return null;
    }
    return value as SharedLease;
  } catch {
    return null;
  }
}

async function withFallbackLease(task: () => Promise<void>): Promise<boolean> {
  const storage = getStorage();
  if (!storage) {
    await task();
    return true;
  }

  const now = Date.now();
  const existing = parseLease(storage.getItem(LEASE_STORAGE_KEY));
  if (existing && existing.owner !== instanceId && existing.expiresAt > now) {
    return false;
  }

  const lease: SharedLease = {
    owner: instanceId,
    expiresAt: now + FALLBACK_LEASE_MS,
  };
  try {
    storage.setItem(LEASE_STORAGE_KEY, JSON.stringify(lease));
    const confirmed = parseLease(storage.getItem(LEASE_STORAGE_KEY));
    if (!confirmed || confirmed.owner !== instanceId) return false;
    await task();
    return true;
  } finally {
    const current = parseLease(storage.getItem(LEASE_STORAGE_KEY));
    if (current?.owner === instanceId) {
      storage.removeItem(LEASE_STORAGE_KEY);
    }
  }
}

async function withCrossTabLock(task: () => Promise<void>): Promise<boolean> {
  const lockManager = typeof navigator === 'undefined' ? undefined : navigator.locks;
  if (lockManager) {
    try {
      let acquired = false;
      await lockManager.request(
        WEB_LOCK_NAME,
        { mode: 'exclusive', ifAvailable: true },
        async (lock) => {
          if (!lock) return;
          acquired = true;
          await task();
        },
      );
      return acquired;
    } catch {
      // Older embedded browsers may expose a partial Locks API. Fall back to
      // a short localStorage lease instead of losing status refresh entirely.
    }
  }
  return withFallbackLease(task);
}

function shouldReuseSharedResult(
  envelope: SharedStatusEnvelope,
  forceAfterConnectivity: boolean,
): boolean {
  if (
    forceAfterConnectivity
    && envelope.outcome === 'error'
  ) {
    return false;
  }
  return Date.now() < envelope.nextProbeAt;
}

async function performApiProbe(
  previous: SharedStatusEnvelope | null,
): Promise<SharedStatusEnvelope> {
  try {
    const { data } = await apiClient.get('/api/v1/system/moomoo-status', {
      timeout: 8000,
    });
    const status = toCamelCase<MoomooStatus>(data);
    if (!isMoomooStatus(status)) {
      throw new Error('Invalid Moomoo status payload');
    }
    const checkedAt = Date.now();
    const envelope: SharedStatusEnvelope = {
      version: 1,
      checkedAt,
      nextProbeAt: checkedAt + HEALTHY_POLL_INTERVAL_MS,
      failureCount: 0,
      outcome: 'success',
      status,
    };
    writeSharedEnvelope(envelope);
    return envelope;
  } catch {
    const checkedAt = Date.now();
    const failureCount = previous?.outcome === 'error'
      ? previous.failureCount + 1
      : 1;
    const envelope: SharedStatusEnvelope = {
      version: 1,
      checkedAt,
      nextProbeAt: checkedAt + retryDelay(failureCount),
      failureCount,
      outcome: 'error',
      status: null,
    };
    writeSharedEnvelope(envelope);
    return envelope;
  }
}

async function runProbe(forceAfterConnectivity: boolean): Promise<void> {
  let sharedResult: SharedStatusEnvelope | null = null;
  const acquired = await withCrossTabLock(async () => {
    sharedResult = readSharedEnvelope();
    if (
      sharedResult
      && shouldReuseSharedResult(sharedResult, forceAfterConnectivity)
    ) {
      applyEnvelope(sharedResult);
      return;
    }
    sharedResult = await performApiProbe(sharedResult);
  });

  if (!started || !isVisible()) return;
  if (!acquired) {
    // The owner will normally publish through BroadcastChannel/storage. This
    // bounded recheck also covers browsers that suppress those events.
    scheduleAt(Date.now() + BUSY_RECHECK_MS);
    return;
  }
  const latest = sharedResult ?? readSharedEnvelope();
  if (latest) {
    scheduleFromEnvelope(latest);
  } else {
    scheduleAt(Date.now() + BUSY_RECHECK_MS);
  }
}

function requestProbe(forceAfterConnectivity = false): Promise<void> {
  if (!started || !isVisible()) return Promise.resolve();
  expireCurrentSuccessIfNeeded();
  if (activeProbe) return activeProbe;

  activeProbe = runProbe(forceAfterConnectivity).finally(() => {
    activeProbe = null;
  });
  return activeProbe;
}

function acceptExternalEnvelope(envelope: SharedStatusEnvelope): void {
  applyEnvelope(envelope);
  if (started && isVisible()) {
    scheduleFromEnvelope(envelope);
  }
}

function handleStorage(event: StorageEvent): void {
  if (event.key !== MOOMOO_STATUS_STORAGE_KEY) return;
  const envelope = parseEnvelope(event.newValue);
  if (envelope) acceptExternalEnvelope(envelope);
}

function handleVisibilityChange(): void {
  clearTimer();
  if (!isVisible()) return;

  const shared = readSharedEnvelope();
  if (shared) applyEnvelope(shared);
  expireCurrentSuccessIfNeeded();
  void requestProbe();
}

function handleOnline(): void {
  if (!isVisible()) return;
  clearTimer();
  void requestProbe(true);
}

function startMonitor(): void {
  if (started || typeof window === 'undefined') return;
  started = true;

  window.addEventListener('storage', handleStorage);
  window.addEventListener('online', handleOnline);
  document.addEventListener('visibilitychange', handleVisibilityChange);

  if (typeof window.BroadcastChannel === 'function') {
    broadcastChannel = new window.BroadcastChannel(BROADCAST_CHANNEL_NAME);
    broadcastChannel.addEventListener('message', (event: MessageEvent<unknown>) => {
      const envelope = parseEnvelope(JSON.stringify(event.data));
      if (envelope) acceptExternalEnvelope(envelope);
    });
  }

  const shared = readSharedEnvelope();
  if (shared) {
    applyEnvelope(shared);
    if (shouldReuseSharedResult(shared, false)) {
      scheduleFromEnvelope(shared);
      return;
    }
  }
  if (isVisible()) void requestProbe();
}

function stopMonitor(): void {
  if (!started || typeof window === 'undefined') return;
  started = false;
  clearTimer();
  window.removeEventListener('storage', handleStorage);
  window.removeEventListener('online', handleOnline);
  document.removeEventListener('visibilitychange', handleVisibilityChange);
  broadcastChannel?.close();
  broadcastChannel = null;
}

function subscribe(listener: () => void): () => void {
  subscribers.add(listener);
  if (subscribers.size === 1) startMonitor();
  return () => {
    subscribers.delete(listener);
    if (subscribers.size === 0) stopMonitor();
  };
}

function getSnapshot(): MoomooStatusSnapshot {
  return snapshot;
}

export function useMoomooStatusMonitor(): MoomooStatusSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function __resetMoomooStatusMonitorForTests(): void {
  stopMonitor();
  subscribers.clear();
  snapshot = INITIAL_SNAPSHOT;
  activeProbe = null;
  const storage = getStorage();
  storage?.removeItem(MOOMOO_STATUS_STORAGE_KEY);
  storage?.removeItem(LEASE_STORAGE_KEY);
}

/**
 * Tiny TTL cache backed by sessionStorage (clears when the tab closes).
 *
 * Usage: wrap any async API call that's idempotent for a short window.
 *
 *   const cached = sessionCache.get<Resp>(`stock:history:${code}:${tf}`);
 *   if (cached) return cached;
 *   const fresh = await api(...);
 *   sessionCache.set(key, fresh);
 *
 * We default to 10 minutes — matches the backend news-digest cache so
 * the user doesn't see "staler-than-server" artifacts.
 */

const DEFAULT_TTL_MS = 10 * 60 * 1000;
// Bump this suffix to invalidate every cached entry across all tabs when
// cache-write semantics change (e.g. when we added "don't cache empty").
const STORAGE_PREFIX = 'dsa-cache:v2:';

// In-memory mirror so we don't pay JSON.parse for every hot read. Also lets
// the cache keep working on SSR / embedded webviews without sessionStorage.
const mem = new Map<string, { value: unknown; exp: number }>();

function storage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export const sessionCache = {
  get<T>(key: string): T | null {
    const now = Date.now();
    const hit = mem.get(key);
    if (hit && hit.exp > now) return hit.value as T;
    if (hit) mem.delete(key);

    const s = storage();
    if (!s) return null;
    const raw = s.getItem(STORAGE_PREFIX + key);
    if (!raw) return null;
    try {
      const { v, e } = JSON.parse(raw) as { v: T; e: number };
      if (e <= now) {
        s.removeItem(STORAGE_PREFIX + key);
        return null;
      }
      mem.set(key, { value: v, exp: e });
      return v;
    } catch {
      s.removeItem(STORAGE_PREFIX + key);
      return null;
    }
  },
  set<T>(key: string, value: T, ttlMs: number = DEFAULT_TTL_MS) {
    const exp = Date.now() + ttlMs;
    mem.set(key, { value, exp });
    const s = storage();
    if (!s) return;
    try {
      s.setItem(STORAGE_PREFIX + key, JSON.stringify({ v: value, e: exp }));
    } catch {
      // Quota or JSON serialization failure — silently fall back to memory.
    }
  },
  invalidate(key: string) {
    mem.delete(key);
    const s = storage();
    if (s) s.removeItem(STORAGE_PREFIX + key);
  },
  clearAll() {
    mem.clear();
    const s = storage();
    if (!s) return;
    const keys: string[] = [];
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i);
      if (k && k.startsWith(STORAGE_PREFIX)) keys.push(k);
    }
    keys.forEach((k) => s.removeItem(k));
  },
};

export const DEFAULT_CACHE_TTL_MS = DEFAULT_TTL_MS;

import { createContext, useContext, useMemo, useSyncExternalStore } from 'react';
import type { Api, PlanQuota } from './api';

/** The hub pushes each new reading on its stream; asking now and then only covers a stream that is down. */
export const QUOTA_REFRESH_MS = 5 * 60_000;
export type QuotaReading = { quota: PlanQuota | null; loading: boolean; error: string | null; failedAt: string | null };

/** A mounted account has one refresh loop and one snapshot across every quota surface. */
export function createQuotaReading(api: Api, agent: string) {
  let snapshot: QuotaReading = { quota: api.cachedAgentQuota(agent), loading: false, error: null, failedAt: null };
  const listeners = new Set<() => void>();
  let pending: Promise<void> | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  // Bumped by each push: an answer to a request sent before it is older than what is shown.
  let pushes = 0;
  const publish = (next: QuotaReading) => { snapshot = next; listeners.forEach(listener => listener()); };
  const read = (refresh = false): Promise<void> => {
    if (pending) return pending;
    publish({ ...snapshot, loading: true });
    const asked = pushes;
    pending = api.agentQuota(agent, refresh).then(
      quota => publish(pushes === asked ? { quota, loading: false, error: null, failedAt: null } : { ...snapshot, loading: false }),
      error => publish(pushes === asked
        ? { quota: api.cachedAgentQuota(agent), loading: false, error: error instanceof Error ? error.message : String(error), failedAt: new Date().toISOString() }
        : { ...snapshot, loading: false }),
    ).finally(() => { pending = null; });
    return pending;
  };
  return {
    getSnapshot: () => snapshot,
    pushed: (quota: PlanQuota) => { pushes++; publish({ quota, loading: snapshot.loading, error: null, failedAt: null }); },
    refresh: () => read(true),
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      if (listeners.size === 1) {
        void read();
        timer = setInterval(() => { if (!document.hidden) void read(); }, QUOTA_REFRESH_MS);
      }
      return () => {
        listeners.delete(listener);
        if (!listeners.size && timer) { clearInterval(timer); timer = null; }
      };
    },
  };
}

const stores = new WeakMap<Api, Map<string, ReturnType<typeof createQuotaReading>>>();

function quotaReadingFor(api: Api, agent: string) {
  let accounts = stores.get(api);
  if (!accounts) { accounts = new Map(); stores.set(api, accounts); }
  let reading = accounts.get(agent);
  if (!reading) { reading = createQuotaReading(api, agent); accounts.set(agent, reading); }
  return reading;
}

export const refreshQuota = (api: Api, agent: string) => quotaReadingFor(api, agent).refresh();

/** A reading the hub pushed: every surface showing this account moves at once. */
export function quotaPushed(api: Api, agent: string, quota: PlanQuota): void {
  api.rememberAgentQuota(agent, quota);
  quotaReadingFor(api, agent).pushed(quota);
}

export function useAgentQuota(api: Api, agent: string) {
  const store = useMemo(() => quotaReadingFor(api, agent), [api, agent]);
  const reading = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return { ...reading, refresh: store.refresh };
}

/** Full quota navigation is supplied by the hub, so inspecting an account never selects it. */
export const QuotaNavigation = createContext<(() => void) | null>(null);
export const useQuotaNavigation = () => useContext(QuotaNavigation);

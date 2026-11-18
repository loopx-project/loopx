import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { ApiError, type Api, type ThreadOutputs, type ThreadStats } from './api';

export type OverviewReading = {
  outputs: ThreadOutputs | null;
  stats: ThreadStats | null;
  loading: boolean;
  failed: boolean;
};

const EMPTY: ThreadOutputs = { changes: { files: [], added: 0, removed: 0, total: 0 }, images: [], files: [], seq: 0 };

/** One overview reading per API connection and thread. Failed refreshes keep valid data. */
export function createOverviewReading(api: Pick<Api, 'outputs' | 'stats'>, threadId: string) {
  let value: OverviewReading = { outputs: null, stats: null, loading: false, failed: false };
  const listeners = new Set<() => void>();
  let pending: Promise<void> | null = null;
  let statsPending: Promise<void> | null = null;
  let checkedAt = 0;
  let revision = '';
  let queuedRevision: string | null = null;
  const publish = (patch: Partial<OverviewReading>) => { value = { ...value, ...patch }; listeners.forEach(listener => listener()); };
  const stats = (): Promise<void> => {
    if (statsPending) return statsPending;
    statsPending = api.stats(threadId).then(stats => publish({ stats }), () => undefined).finally(() => { statsPending = null; });
    return statsPending;
  };
  const refresh = (nextRevision = revision): Promise<void> => {
    if (pending) { if (nextRevision !== revision) queuedRevision = nextRevision; return pending; }
    revision = nextRevision;
    publish({ loading: true, failed: false });
    void stats();
    pending = api.outputs(threadId).then(
      outputs => publish({ outputs, loading: false, failed: false }),
      error => error instanceof ApiError && error.status === 404
        ? publish({ outputs: EMPTY, loading: false, failed: false })
        : publish({ loading: false, failed: true }),
    ).finally(() => {
      checkedAt = Date.now(); pending = null;
      if (queuedRevision !== null) { const next = queuedRevision; queuedRevision = null; void refresh(next); }
    });
    return pending;
  };
  return {
    getSnapshot: () => value,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    refresh,
    stats,
    stale: (nextRevision: string) => !value.outputs || revision !== nextRevision || Date.now() - checkedAt > 2000,
    inactive: () => !listeners.size && !pending && !statsPending,
  };
}

const stores = new WeakMap<Api, Map<string, ReturnType<typeof createOverviewReading>>>();
export function overviewReadingFor(api: Api, threadId: string) {
  let threads = stores.get(api);
  if (!threads) { threads = new Map(); stores.set(api, threads); }
  let store = threads.get(threadId);
  if (!store) {
    if (threads.size >= 32) for (const [id, old] of threads) if (old.inactive()) { threads.delete(id); break; }
    store = createOverviewReading(api, threadId);
    threads.set(threadId, store);
  }
  return store;
}

/** A cold popover waits at its existing trigger until its contents have a known size. */
export function prepareOverview(api: Api, threadId: string, revision: string): Promise<void> {
  const store = overviewReadingFor(api, threadId);
  return store.getSnapshot().outputs ? Promise.resolve() : store.refresh(revision);
}

export function useOverviewReading(api: Api, threadId: string, revision: string, active: boolean) {
  const store = useMemo(() => overviewReadingFor(api, threadId), [api, threadId]);
  const reading = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useEffect(() => {
    if (!store.stale(revision) || (reading.failed && !reading.outputs)) return;
    const timer = window.setTimeout(() => { void store.refresh(revision); }, reading.outputs ? 400 : 0);
    return () => window.clearTimeout(timer);
    // A revision, rather than each response, schedules the next reading.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, revision]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden && (active || store.getSnapshot().stats?.running)) void store.stats();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [store, active]);
  return { ...reading, refresh: () => store.refresh(revision) };
}

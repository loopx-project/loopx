/**
 * The `WebHost` an extension view receives: the thread summaries, the stream's health, a few
 * actions and the host components. It is built from what the page already holds (App's thread
 * list, refreshed from /api/stream). Native memory is queried separately through a permissioned
 * facade; views never see the token or the raw Api.
 */
import type { ExtensionState, ThreadSummary, WorkProjection } from '@calli/extension-api';
import type { AgentInfo as HostAgentInfo, ConnectionState, MachineState, WebHost, WebHostNavigate, WebHostStorage, WebHostUi } from '@calli/extension-api/web';
import type { AgentInfo } from '../api';
import type { Api } from '../api';
import { memoryMessages } from './memory-messages';
import { locale } from '../i18n';
import { hasPermission } from './permissions';

export interface HostInputs {
  extension: ExtensionState | null;
  threads: ThreadSummary[];
  workSources?: WorkProjection[];
  machines?: MachineState[];
  connection: ConnectionState;
  /** ISO time of the last thread list read from the daemon. */
  observedAt: string | null;
  agents: AgentInfo[];
}

export interface LiveHost {
  /** Everything but `narrow`, which the view passes in on each render. */
  host: Omit<WebHost, 'narrow'>;
  /** Hands in what the page holds now; subscribers hear about what actually changed. */
  update(next: HostInputs): void;
}

/** Per device, under `calli.ext.<id>.`, so one extension cannot read or clobber another's keys. */
export function extensionStorage(extensionId: string, store: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null = typeof localStorage === 'undefined' ? null : localStorage): WebHostStorage {
  const prefix = `calli.ext.${extensionId}.`;
  return {
    get(key) {
      try {
        return store?.getItem(prefix + key) ?? null;
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        if (value === null) store?.removeItem(prefix + key);
        else store?.setItem(prefix + key, value);
      } catch {
        // Storage full or unavailable: the choice lasts as long as the page.
      }
    },
  };
}

export function createWebHost(
  extensionId: string,
  initial: HostInputs,
  deps: { navigate?: Partial<WebHostNavigate>; askAssistant?: (text: string) => Promise<void>; setThreadStage?: (id: string, stage: 'backlog' | 'doing' | 'blocked' | 'review') => Promise<void>; api?: Pick<Api, 'memorySources' | 'memoryList' | 'memoryRead'> & Partial<Pick<Api, 'autotitleSettings' | 'saveAutotitleSettings' | 'previewAutotitle' | 'organizeAutotitle' | 'models'>>; openThread: (id: string) => void; setThreadDone?: (id: string, done: boolean) => Promise<void>; ui: (agents: () => AgentInfo[]) => WebHostUi; storage?: WebHostStorage; onError?: (error: unknown) => void },
): LiveHost {
  let inputs = initial;
  const allowed = (value: HostInputs) => value.extension?.manifest.id === extensionId && hasPermission(value.extension, 'events.summary');
  // A view without `progress.report` never sees what agents said, only that the thread was marked done.
  const visible = (value: HostInputs): ThreadSummary[] => !allowed(value) ? []
    : hasPermission(value.extension, 'progress.report') ? value.threads : value.threads.map((t) => (t.report ? { ...t, report: null } : t));
  let threadsKey = JSON.stringify(visible(initial));
  function notify<T>(listeners: Set<(value: T) => void>, value: T): void {
    for (const listener of listeners) {
      try { listener(value); } catch (error) {
        deps.onError?.(error);
      }
    }
  }
  const workListeners = new Set<(items: WorkProjection[]) => void>();
  const workAllowed = (v: HostInputs) => v.extension?.manifest.id === extensionId && hasPermission(v.extension, 'worksource.read') ? v.workSources ?? [] : [];
  const threadListeners = new Set<(threads: ThreadSummary[]) => void>();
  const connectionListeners = new Set<(state: ConnectionState) => void>();
  const ui = deps.ui(() => inputs.agents);

  const memoryAllowed = () => inputs.extension?.manifest.id === extensionId && hasPermission(inputs.extension, 'memory.read');
  async function memoryRequest<T>(read: () => Promise<T>): Promise<T> {
    if (!memoryAllowed() || !deps.api) throw new Error('Memory permission required');
    const value = await read();
    if (!memoryAllowed()) throw new Error('Memory permission revoked');
    return value;
  }

  const autotitleApi = deps.api;
  const host: Omit<WebHost, 'narrow'> = {
    extensionId,
    // Only the built-in auto-titles extension gets its settings; the hub checks its grant again.
    ...(extensionId === 'autotitle' && autotitleApi?.autotitleSettings ? {
      autotitle: {
        load: () => autotitleApi.autotitleSettings!(),
        save: (patch) => autotitleApi.saveAutotitleSettings!(patch),
        preview: (threadId) => autotitleApi.previewAutotitle!(threadId),
        organize: () => autotitleApi.organizeAutotitle!(),
        models: async (agentId) => (await autotitleApi.models!(agentId)).map(m => ({ id: m.id, label: m.label })),
      },
    } : {}),
    memory: {
      sources: () => memoryRequest(() => deps.api!.memorySources(extensionId)),
      list: (sourceId) => memoryRequest(() => deps.api!.memoryList(extensionId, sourceId)),
      read: (sourceId, fileId) => memoryRequest(() => deps.api!.memoryRead(extensionId, sourceId, fileId)),
      messages: memoryMessages,
      subscribe(listener) {
        const refresh = () => {
          if (memoryAllowed() && typeof document !== 'undefined' && document.visibilityState === 'visible') listener();
        };
        const connection = () => { if (inputs.connection === 'live') refresh(); };
        const timer = setInterval(refresh, 30_000);
        if (typeof document !== 'undefined') document.addEventListener('visibilitychange', refresh);
        connectionListeners.add(connection);
        return () => {
          clearInterval(timer);
          if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', refresh);
          connectionListeners.delete(connection);
        };
      },
    },
    machines: () => allowed(inputs) ? inputs.machines ?? [] : [],
    ...(initial.extension?.manifest.permissions.includes('assistant.ask') ? { assistant: {
      async ask(text: string) {
        if (inputs.extension?.manifest.id !== extensionId || !inputs.extension?.installed || !inputs.extension.enabled || !inputs.extension.manifest.permissions.includes('assistant.ask') || !deps.askAssistant) throw new Error('Assistant permission required');
        await deps.askAssistant(text);
      },
      threadLink(id: string) {
        const thread = visible(inputs).find(t => t.id === id);
        if (!thread) throw new Error('Thread permission required');
        const machine = thread.machine ? thread.machine.id : inputs.machines?.[0]?.id ?? null;
        return `#/thread/${encodeURIComponent(id)}${machine === null ? '' : `?machine=${encodeURIComponent(machine)}`}`;
      },
    } } : {}),
    threads: {
      snapshot: () => visible(inputs),
      subscribe(listener) {
        threadListeners.add(listener);
        return () => void threadListeners.delete(listener);
      },
      async setStage(threadId, stage) {
        if (!visible(inputs).some(t => t.id === threadId) || !hasPermission(inputs.extension, 'progress.report') || !deps.setThreadStage) throw new Error('Progress permission required');
        await deps.setThreadStage(threadId, stage);
      },
      async setDone(threadId, done) {
        if (!visible(inputs).some((t) => t.id === threadId) || !deps.setThreadDone) throw new Error('Thread permission required');
        await deps.setThreadDone(threadId, done);
      },
    },
    workSources: { snapshot: () => workAllowed(inputs), subscribe(listener) { workListeners.add(listener); return () => void workListeners.delete(listener); } },
    connection: {
      state: () => inputs.connection,
      observedAt: () => inputs.observedAt,
      subscribe(listener) {
        connectionListeners.add(listener);
        return () => void connectionListeners.delete(listener);
      },
    },
    agents: (): HostAgentInfo[] => inputs.agents.map((a) => ({ id: a.id, label: a.label, available: a.available, ...(a.capabilities?.permissionModes ? { permissionModes: a.capabilities.permissionModes } : {}) })),
    navigate: {
      openThread: id => deps.openThread(id),
      peekThread: id => (deps.navigate?.peekThread ?? deps.openThread)(id),
      threadMenu: (id, at) => deps.navigate?.threadMenu?.(id, at),
      newThread: options => deps.navigate?.newThread?.(options),
    },
    locale,
    storage: deps.storage ?? extensionStorage(extensionId),
    ui,
  };

  return {
    host,
    update(next) {
      const before = inputs;
      inputs = next;
      if (JSON.stringify(workAllowed(before)) !== JSON.stringify(workAllowed(next))) notify(workListeners, workAllowed(next));
      // The page re-reads the list on every event; listeners hear only when a summary changed.
      const key = JSON.stringify(visible(next));
      if (key !== threadsKey || allowed(next) !== allowed(before) || JSON.stringify(next.machines) !== JSON.stringify(before.machines)) {
        threadsKey = key;
        if (allowed(next)) notify(threadListeners, visible(next));
      }
      if (next.connection !== before.connection || next.observedAt !== before.observedAt) notify(connectionListeners, next.connection);
    },
  };
}

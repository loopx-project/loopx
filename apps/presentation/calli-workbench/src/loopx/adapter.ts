/**
 * LoopXApiAdapter — the M1 read-only replacement for Calli's web Api.
 *
 * Reads: {statusBase}/status.json, /ssh-hosts and {chatBase}/api/chat/sessions,
 * /api/chat/sessions/{id}, /api/chat/capabilities, /api/chat/endpoints,
 * /api/chat/goals/contexts — GET only, always through zod contracts
 * (status.json through parseStatusPayload, which also gates the contract
 * schema_version).
 *
 * SSE is replaced by polling at the dashboard conversation cadence, behind the
 * same stream() signature Calli components already call. Every Calli write
 * method throws ReadOnlyError. No localStorage/sessionStorage anywhere: the
 * adapter is a pure data layer (display preferences stay in the web app).
 */
import type { z } from 'zod';
import type {
  AgentInfo,
  GeneralSettings,
  HostInfo,
  ModelOption,
  Schedule,
  Section,
  StoredEvent,
  Thread,
  Todo,
  TriageView,
  WorkdirsConfig,
} from './types.ts';
import { loadConfig, type LoopXAdapterConfig } from './config.ts';
import { ContractFormatError, ReadOnlyError, SourceUnavailableError } from './errors.ts';
import {
  chatCapabilitiesSchema,
  chatEndpointListSchema,
  chatGoalContextsSchema,
  chatSessionListSchema,
  chatSessionSnapshotSchema,
  sshHostCatalogSchema,
  type ChatCapabilities,
  type ChatGoalContext,
  type ChatSessionSummary,
} from './contracts/chat-schema.ts';
import { parseStatusPayload } from './schema-guard.ts';
import type { StatusPayload } from './contracts/status-schema.ts';
import { mapSessionsToThreads } from './mappers/threads.ts';
import { eventsAfter, eventsBefore, mapMessagesToEvents } from './mappers/events.ts';
import {
  mapAdaptersToAgents,
  mapGeneralSettings,
  mapHealth,
  mapModels,
  mapSections,
  mapSshHostsToHosts,
  mapWorkdirs,
} from './mappers/misc.ts';
import { mapTodos } from './mappers/todos.ts';
import { deriveTriage } from './mappers/triage.ts';
import { demoEndpoints, type FixtureData } from './fixtures.ts';
import { pollLoop } from './poller.ts';
import { placeholder } from './placeholders.ts';

const GLOBAL_STREAM_PATH = '/api/stream';
const THREAD_STREAM_RE = /^\/api\/threads\/([^/?]+)\/stream/;

export class LoopXApiAdapter {
  private readonly config: LoopXAdapterConfig;
  /** Set for `?fixture=1`: every read comes from the bundle; fetch is never called. */
  private readonly fixture: FixtureData | null;
  private statusCache: { at: number; payload: StatusPayload } | null = null;
  private readonly statusCacheTtlMs = 1_000;

  constructor(config: Partial<LoopXAdapterConfig> & { fixture?: FixtureData } = {}) {
    const { fixture, ...rest } = config;
    this.fixture = fixture ?? null;
    this.config = loadConfig(rest);
  }

  // --- transport ---

  private async fetchRaw(url: string, endpoint: string): Promise<unknown> {
    let res: Response;
    try {
      res = await fetch(url, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(this.config.requestTimeoutMs),
      });
    } catch (e) {
      throw new SourceUnavailableError(`LoopX service unreachable: ${endpoint} (${String(e)})`, endpoint);
    }
    if (!res.ok) throw new SourceUnavailableError(`LoopX ${endpoint} answered HTTP ${res.status}`, endpoint);
    try {
      return await res.json();
    } catch {
      throw new ContractFormatError(`LoopX ${endpoint} returned non-JSON body`, endpoint, ['body: not json']);
    }
  }

  private async fetchJson<S extends z.ZodType>(url: string, schema: S, endpoint: string): Promise<z.output<S>> {
    const parsed = schema.safeParse(await this.fetchRaw(url, endpoint));
    if (!parsed.success) {
      const issues = parsed.error.issues.map((i) => `${i.path.join('.') || 'root'}: ${i.message}`);
      throw new ContractFormatError(`LoopX ${endpoint} failed its contract`, endpoint, issues);
    }
    return parsed.data;
  }

  private statusUrl(path: string): string {
    return `${this.config.statusBase}${path}`;
  }

  private chatUrl(path: string): string {
    return `${this.config.chatBase}${path}`;
  }

  // --- projection reads (1s TTL so one UI tick does not hit status.json repeatedly) ---

  async fetchStatus(): Promise<StatusPayload> {
    if (this.statusCache && Date.now() - this.statusCache.at < this.statusCacheTtlMs) return this.statusCache.payload;
    const payload = this.fixture
      ? parseStatusPayload(this.fixture.status)
      : parseStatusPayload(await this.fetchRaw(this.statusUrl('/status.json'), '/status.json'));
    this.statusCache = { at: Date.now(), payload };
    return payload;
  }

  async fetchSessions(): Promise<ChatSessionSummary[]> {
    if (this.fixture) return this.fixture.sessions;
    const list = await this.fetchJson(this.chatUrl('/api/chat/sessions'), chatSessionListSchema, '/api/chat/sessions');
    return list.sessions;
  }

  async fetchCapabilities(): Promise<ChatCapabilities> {
    if (this.fixture) {
      if (this.fixture.capabilities) return this.fixture.capabilities;
      throw new SourceUnavailableError('demo fixture has no capabilities payload', 'fixture');
    }
    return this.fetchJson(this.chatUrl('/api/chat/capabilities'), chatCapabilitiesSchema, '/api/chat/capabilities');
  }

  private async fetchGoalContexts(): Promise<ChatGoalContext[]> {
    if (this.fixture) return this.fixture.goalContexts ?? [];
    const body = await this.fetchJson(this.chatUrl('/api/chat/goals/contexts'), chatGoalContextsSchema, '/api/chat/goals/contexts');
    return body.goals;
  }

  /** goal_id → repository label (display name only; no local absolute paths). */
  private async repoByGoal(): Promise<Map<string, string>> {
    const contexts = await this.fetchGoalContexts().catch(() => [] as ChatGoalContext[]);
    return new Map(
      contexts
        .filter((g) => g.repository?.label)
        .map((g) => [g.goal_id, g.repository!.label!]),
    );
  }

  // --- Calli Api subset ---

  async health(): Promise<{ ok: boolean; version: string }> {
    const [status, capabilities] = await Promise.all([
      this.fetchStatus(),
      this.fetchCapabilities().catch(() => null),
    ]);
    return mapHealth(status, capabilities);
  }

  async agents(): Promise<AgentInfo[]> {
    if (this.fixture) return mapAdaptersToAgents(this.fixture.endpoints ?? demoEndpoints(this.fixture.sessions));
    const endpoints = await this.fetchJson(this.chatUrl('/api/chat/endpoints'), chatEndpointListSchema, '/api/chat/endpoints');
    return mapAdaptersToAgents(endpoints.endpoints);
  }

  async workdirs(): Promise<WorkdirsConfig> {
    const [status, contexts] = await Promise.all([
      this.fetchStatus(),
      this.fetchGoalContexts().catch(() => [] as ChatGoalContext[]),
    ]);
    return mapWorkdirs(status, contexts);
  }

  async sections(): Promise<Section[]> {
    // Goal-derived groups + the trailing unassigned group; sessions are not
    // consulted (session-only goal ids fold into unassigned, never titled).
    return mapSections(await this.fetchStatus());
  }

  async generalSettings(): Promise<GeneralSettings> {
    return mapGeneralSettings();
  }

  async schedules(): Promise<Schedule[]> {
    return placeholder<Schedule[]>('Schedule[]');
  }

  async models(agent: string): Promise<ModelOption[]> {
    const [sessions, capabilities] = await Promise.all([
      this.fetchSessions().catch(() => [] as ChatSessionSummary[]),
      this.fetchCapabilities().catch(() => null),
    ]);
    return mapModels(agent, sessions, capabilities);
  }

  async discoverAgents(): Promise<[]> {
    return placeholder<[]>('discoverAgents()/discoverAccounts()');
  }

  async discoverAccounts(): Promise<[]> {
    return placeholder<[]>('discoverAgents()/discoverAccounts()');
  }

  async threads(_opts: { archived?: boolean } = {}): Promise<Thread[]> {
    const [status, sessions, repos] = await Promise.all([
      this.fetchStatus(),
      this.fetchSessions(),
      this.repoByGoal(),
    ]);
    const goalsById = new Map((status.run_history?.goals ?? []).map((g) => [g.id, g]));
    return mapSessionsToThreads(sessions, goalsById, repos);
  }

  async thread(id: string): Promise<Thread> {
    const found = (await this.threads()).find((t) => t.id === id);
    if (!found) throw new SourceUnavailableError(`No LoopX chat session ${id}`, `/api/chat/sessions/${id}`);
    return found;
  }

  private async sessionEvents(id: string): Promise<StoredEvent[]> {
    if (this.fixture) {
      const session = this.fixture.sessions.find((s) => s.session_id === id);
      return mapMessagesToEvents(id, this.fixture.messagesBySession?.[id] ?? [], session?.last_error_code);
    }
    const snapshot = await this.fetchJson(
      this.chatUrl(`/api/chat/sessions/${encodeURIComponent(id)}`),
      chatSessionSnapshotSchema,
      `/api/chat/sessions/${id}`,
    );
    return mapMessagesToEvents(id, snapshot.messages ?? [], snapshot.session.last_error_code);
  }

  async events(id: string, after = 0): Promise<StoredEvent[]> {
    return eventsAfter(await this.sessionEvents(id), after);
  }

  async eventsTail(id: string): Promise<StoredEvent[]> {
    return this.sessionEvents(id);
  }

  async eventsBefore(id: string, before: number): Promise<StoredEvent[]> {
    return eventsBefore(await this.sessionEvents(id), before);
  }

  async hosts(): Promise<HostInfo[]> {
    if (this.fixture) {
      return (this.fixture.sshHosts ?? []).map((alias) => ({
        id: alias,
        label: alias,
        target: alias,
        status: placeholder<HostInfo['status']>('HostInfo.status'),
      }));
    }
    const catalog = await this.fetchJson(this.statusUrl('/ssh-hosts'), sshHostCatalogSchema, '/ssh-hosts');
    return mapSshHostsToHosts(catalog);
  }

  async todos(): Promise<Todo[]> {
    return mapTodos(await this.fetchStatus());
  }

  async triage(): Promise<TriageView> {
    const [status, sessions] = await Promise.all([
      this.fetchStatus(),
      this.fetchSessions().catch(() => [] as ChatSessionSummary[]),
    ]);
    return deriveTriage(status, sessions);
  }

  // --- SSE → polling ---

  async stream<T = StoredEvent>(
    path: string,
    onEvent: (ev: T) => void,
    signal: AbortSignal,
    _idleMs?: number,
    onOpen?: () => void,
  ): Promise<void> {
    if (path === GLOBAL_STREAM_PATH) return this.streamGlobal(onEvent as (ev: StoredEvent) => void, signal, onOpen);
    const threadMatch = THREAD_STREAM_RE.exec(path);
    if (threadMatch) {
      const after = Number(new URL(path, 'http://loopx.local').searchParams.get('after') ?? '0') || 0;
      return this.streamThread(threadMatch[1], after, onEvent as (ev: StoredEvent) => void, signal, onOpen);
    }
    throw new SourceUnavailableError(`LoopX adapter does not serve stream path: ${path}`, path);
  }

  private digestOf(status: StatusPayload, sessions: ChatSessionSummary[]): string {
    return JSON.stringify({
      ok: status.ok,
      goalCount: status.goal_count,
      runCount: status.run_count,
      attention: (status.attention_queue.items ?? []).map((i) => [i.goal_id, i.status, i.waiting_on, i.severity]),
      todos: (status.todo_index?.items ?? []).map((t) => [t.todo_id ?? t.index, t.done, t.status]),
      goals: (status.run_history?.goals ?? []).map((g) => [g.id, g.status, g.lifecycle_phase]),
      sessions: sessions.map((s) => [s.session_id, s.updated_at, s.active_turn_id, s.last_error_code]),
    });
  }

  private async streamGlobal(onEvent: (ev: StoredEvent) => void, signal: AbortSignal, onOpen?: () => void): Promise<void> {
    let digest = '';
    await pollLoop({
      signal,
      intervalMs: this.config.pollIntervalMs,
      backoffMaxMs: this.config.pollBackoffMaxMs,
      onFirstTick: onOpen,
      tick: async () => {
        this.statusCache = null; // a poll must see fresh data
        const [status, sessions] = await Promise.all([this.fetchStatus(), this.fetchSessions()]);
        const next = this.digestOf(status, sessions);
        if (next !== digest) {
          const changed = digest !== '';
          digest = next;
          if (changed) onEvent({ threadId: '', seq: 0, turnId: null, type: 'loopx.stateChanged', ts: new Date().toISOString(), data: {} });
        }
      },
    });
  }

  private async streamThread(threadId: string, after: number, onEvent: (ev: StoredEvent) => void, signal: AbortSignal, onOpen?: () => void): Promise<void> {
    let cursor = after;
    await pollLoop({
      signal,
      intervalMs: this.config.pollIntervalMs,
      backoffMaxMs: this.config.pollBackoffMaxMs,
      onFirstTick: onOpen,
      tick: async () => {
        const fresh = eventsAfter(await this.sessionEvents(threadId), cursor);
        if (fresh.length) {
          cursor = fresh[fresh.length - 1].seq;
          for (const ev of fresh) onEvent(ev);
        }
      },
    });
  }

  // --- Calli write paths: always refused (async rejection, like Calli's network paths) ---

  private refuse(method: string): Promise<never> {
    return Promise.reject(new ReadOnlyError(method));
  }

  startTurn(): Promise<never> { return this.refuse('startTurn'); }
  cancel(): Promise<never> { return this.refuse('cancel'); }
  decide(): Promise<never> { return this.refuse('decide'); }
  answerInput(): Promise<never> { return this.refuse('answerInput'); }
  createThread(): Promise<never> { return this.refuse('createThread'); }
  updateThread(): Promise<never> { return this.refuse('updateThread'); }
  deleteThread(): Promise<never> { return this.refuse('deleteThread'); }
  createTodo(): Promise<never> { return this.refuse('createTodo'); }
  updateTodo(): Promise<never> { return this.refuse('updateTodo'); }
  deleteTodo(): Promise<never> { return this.refuse('deleteTodo'); }
  moveTodo(): Promise<never> { return this.refuse('moveTodo'); }
  linkTodo(): Promise<never> { return this.refuse('linkTodo'); }
  unlinkTodo(): Promise<never> { return this.refuse('unlinkTodo'); }
  createSection(): Promise<never> { return this.refuse('createSection'); }
  renameSection(): Promise<never> { return this.refuse('renameSection'); }
  deleteSection(): Promise<never> { return this.refuse('deleteSection'); }
  addWorkdir(): Promise<never> { return this.refuse('addWorkdir'); }
  removeWorkdir(): Promise<never> { return this.refuse('removeWorkdir'); }
  saveGeneralSettings(): Promise<never> { return this.refuse('saveGeneralSettings'); }
}

import { Api, type AgentInfo, type GeneralSettings, type HostInfo, type ModelOption, type Schedule, type SearchHit, type Section, type Thread, type Todo, type TodoNote, type WorkdirsConfig } from '../api';
import type { StoredEvent } from '../timeline';
import { LoopXApiAdapter } from './adapter.ts';
import { createDemoFixture } from './fixtures.ts';
import demoStatus from './fixtures/status.demo.json';
import type { LoopXAdapterConfig } from './config.ts';
import { ReadOnlyError, SourceUnavailableError } from './errors.ts';
import type { TriageView } from './types.ts';

/**
 * The workbench's data layer over LoopX projections. Every read comes from the adapter; every
 * other `Api` method ends in `send()`, which is closed here, so nothing the page does can write.
 * The few methods that reach the network without `send()` are closed one by one below.
 */
export class LoopxApi extends Api {
  override readonly readOnly = true;
  private readonly source: LoopXApiAdapter;

  /** `fixture` answers from the bundled demo data and makes no request at all. */
  constructor({ fixture = false, ...bases }: Partial<Pick<LoopXAdapterConfig, 'statusBase' | 'chatBase'>> & { fixture?: boolean } = {}) {
    super('');
    this.source = new LoopXApiAdapter({ ...bases, ...(fixture ? { fixture: createDemoFixture(demoStatus) } : {}) });
  }

  // --- the one door every Api method goes through ---

  protected override send<T>(method: string, url: string): Promise<T> {
    return Promise.reject(new ReadOnlyError(`${method} ${url}`));
  }

  override threadFile(): Promise<Blob> {
    return Promise.reject(new SourceUnavailableError('LoopX has no thread files', '/files'));
  }

  override attachment(): Promise<Blob> {
    return Promise.reject(new SourceUnavailableError('LoopX has no attachments', '/attachments'));
  }

  // --- reads ---

  override health(): Promise<{ ok: boolean; version: string }> { return this.source.health(); }
  override agents(): Promise<AgentInfo[]> { return this.source.agents(); }
  override workdirs(): Promise<WorkdirsConfig> { return this.source.workdirs(); }
  override sections(): Promise<Section[]> { return this.source.sections(); }
  override generalSettings(): Promise<GeneralSettings> { return this.source.generalSettings(); }
  override schedules(): Promise<Schedule[]> { return this.source.schedules(); }
  override models(agent: string): Promise<ModelOption[]> { return this.source.models(agent); }
  override discoverAgents(): Promise<[]> { return this.source.discoverAgents(); }
  override discoverAccounts(): Promise<[]> { return this.source.discoverAccounts(); }
  override threads(opts: { archived?: boolean } = {}): Promise<Thread[]> { return this.source.threads(opts); }
  override thread(id: string): Promise<Thread> { return this.source.thread(id); }
  override events(id: string, after = 0): Promise<StoredEvent[]> { return this.source.events(id, after); }
  override eventsTail(id: string): Promise<StoredEvent[]> { return this.source.eventsTail(id); }
  override eventsBefore(id: string, before: number): Promise<StoredEvent[]> { return this.source.eventsBefore(id, before); }
  override hosts(): Promise<HostInfo[]> { return this.source.hosts(); }
  override todos(): Promise<Todo[]> { return this.source.todos(); }
  triage(): Promise<TriageView> { return this.source.triage(); }

  /** LoopX keeps no per-matter note and no cross-thread search; both are empty rather than errors. */
  override todoNote(): Promise<TodoNote> { return Promise.resolve({ text: '', updatedAt: null, threadId: null, rev: 0 }); }
  override search(): Promise<SearchHit[]> { return Promise.resolve([]); }

  override stream<T>(path: string, onEvent: (ev: T) => void, signal: AbortSignal, idleMs?: number, onOpen?: () => void): Promise<void> {
    return this.source.stream(path, onEvent, signal, idleMs, onOpen);
  }
}

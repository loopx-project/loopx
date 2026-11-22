import { Api, type FolderListing, type SearchHit, type TextFile, type Thread } from '../api';
import type { DemoData } from './data';
import { t } from '../i18n';

/**
 * What the demo cannot do. `write` is set for anything that would change something: the page
 * answers that by asking to connect. A read the demo has nothing for is quiet.
 */
export class DemoError extends Error {
  constructor(readonly write: boolean) {
    super(write ? t('demo.needsHub') : t('demo.notInDemo'));
  }
}

/** Waits for the signal, then settles; an aborted wait is how a demo stream ends. */
const untilAborted = (signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    if (signal.aborted) resolve();
    else signal.addEventListener('abort', () => resolve(), { once: true });
  });

/**
 * The client of the made-up workspace. Reads come from the demo data, loaded on first use so a
 * connected page never downloads it; writes are refused with a DemoError. Nothing goes over the
 * network.
 */
export class DemoApi extends Api {
  override readonly demo = true;
  private data: Promise<DemoData> | null = null;

  constructor(private readonly load: () => Promise<DemoData> = () => import('./data').then((m) => m.demoData())) {
    super('');
  }

  private workspace(): Promise<DemoData> {
    return (this.data ??= this.load());
  }

  protected override async send<T>(method: string, url: string, body?: unknown): Promise<T> {
    const data = await this.workspace();
    const answer = route(data, method, url, body);
    if (answer === undefined) throw new DemoError(method !== 'GET');
    // A copy, so a page that changes what it was given cannot change the demo under it.
    return structuredClone(answer) as T;
  }

  override stream<T>(_path: string, _onEvent: (ev: T) => void, signal: AbortSignal): Promise<void> {
    // Nothing ever happens in the demo. Settling only once aborted keeps callers from reconnecting.
    return untilAborted(signal);
  }

  override threadFile(_threadId: string, _path: string): Promise<Blob> {
    return Promise.reject(new DemoError(false));
  }

  override attachment(_threadId: string, _attachmentId: string): Promise<Blob> {
    return Promise.reject(new DemoError(false));
  }
}

/** The demo's answer to one request, or undefined when it has none. Exported for the tests. */
export function route(data: DemoData, method: string, url: string, body?: unknown): unknown {
  const { pathname, searchParams: q } = new URL(url, 'http://demo');
  const parts = pathname.split('/').filter(Boolean).slice(1); // without the leading "api"
  const thread = (id: string) => data.threads.find((t) => t.id === id);

  if (method === 'PATCH' && parts[0] === 'threads' && parts.length === 2) {
    // Opening a thread marks it read. That much is kept here, so the dot goes away as it would.
    const t = thread(parts[1]);
    const patch = (body ?? {}) as Partial<Thread>;
    if (!t || Object.keys(patch).join() !== 'unread') return undefined;
    t.unread = Boolean(patch.unread);
    return t;
  }
  if (method !== 'GET') return undefined;

  const path = parts.join('/');
  switch (path) {
    case 'agents':
      return data.agents;
    case 'agents/discover':
    case 'agents/accounts/discover':
    case 'agents/supported':
    case 'hosts':
      return [];
    case 'config':
      return data.config;
    case 'threads':
      return q.get('archived') ? [] : data.threads;
    case 'sections':
      return data.sections;
    case 'todos':
      return data.todos;
    case 'schedules':
      return data.schedules;
    case 'worktree-options':
      return { available: false };
    case 'search':
      return search(data, q.get('q') ?? '');
    case 'activity':
      return { days: data.activity };
  }

  if (parts[0] === 'agents' && parts.length === 3) {
    if (parts[2] === 'models') return data.models[parts[1]] ?? [];
    if (parts[2] === 'commands') return [];
  }
  if (parts[0] === 'schedules' && parts[2] === 'runs') {
    const run = data.schedules.find((s) => s.id === parts[1])?.lastRun;
    return run ? [run] : [];
  }
  if (parts[0] === 'threads' && parts[1]) {
    const t = thread(parts[1]);
    if (!t) return undefined;
    const events = data.events[t.id] ?? [];
    switch (parts[2]) {
      case undefined:
        return t;
      case 'system-prompt':
        return data.systemPrompts[t.id] ?? undefined;
      case 'trace':
        return data.traces[t.id] ?? undefined;
      case 'events': {
        const after = Number(q.get('after') ?? 0);
        const before = Number(q.get('before') ?? Infinity);
        return events.filter((ev) => ev.seq > after && ev.seq < before);
      }
      case 'coordination':
        return null;
      case 'fork-options':
        return { worktree: { available: false } };
      case 'tree': {
        const at = q.get('path') ?? '';
        const entries = data.folders[`${t.cwd}:${at}`];
        return entries ? ({ root: t.cwd, path: at, entries, truncated: false } satisfies FolderListing) : undefined;
      }
      case 'text': {
        const at = q.get('path') ?? '';
        const text = data.files[`${t.cwd}:${at}`];
        return text === undefined ? undefined : ({ root: t.cwd, path: at, size: text.length, text, truncated: false } satisfies TextFile);
      }
      case 'find': {
        const words = (q.get('q') ?? '').toLowerCase().split(/\s+/).filter(Boolean);
        const paths = Object.keys(data.files)
          .filter((key) => key.startsWith(`${t.cwd}:`))
          .map((key) => key.slice(t.cwd.length + 1))
          .filter((p) => words.every((w) => p.toLowerCase().includes(w)));
        return { root: t.cwd, paths, truncated: false };
      }
    }
  }
  return undefined;
}

/** Turns in which the words were said: what the user asked and what came back. */
function search(data: DemoData, query: string): SearchHit[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const hits: SearchHit[] = [];
  for (const t of data.threads) {
    const said = new Map<string, { role: SearchHit['role']; text: string; ts: string }>();
    for (const ev of data.events[t.id] ?? []) {
      if (!ev.turnId) continue;
      if (ev.type === 'turn.started') said.set(`${ev.turnId}:user`, { role: 'user', text: String(ev.data.text ?? ''), ts: ev.ts });
      if (ev.type === 'message.delta') {
        const key = `${ev.turnId}:assistant`;
        const prev = said.get(key);
        said.set(key, { role: 'assistant', text: (prev?.text ?? '') + String(ev.data.text ?? ''), ts: ev.ts });
      }
    }
    for (const [key, { role, text, ts }] of said) {
      const at = text.toLowerCase().indexOf(needle);
      if (at < 0) continue;
      const snippet = text.slice(Math.max(0, at - 30), at + needle.length + 60);
      hits.push({ threadId: t.id, turnId: key.slice(0, key.lastIndexOf(':')), role, ts, snippet, title: t.title, agent: t.agent, cwd: t.cwd, archived: false });
    }
  }
  return hits;
}

import type { ExtensionManifest, ExtensionState, Permission } from '@calli/extension-api';
import { Api, ApiError, type FolderListing, type MachineUsageReport, type SearchHit, type TextFile, type Thread, type ThreadOutputs, type UsagePoint, type UsageTokens, type VideoRef } from '../api';
import { BUILTIN_MANIFESTS } from '../extensions/catalog';
import { demoPicture, type DemoData } from './data';
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

  protected override async send<T>(method: string, url: string, body?: unknown, _opts: { keepalive?: boolean } = {}): Promise<T> {
    const extensions = this.extensionAnswer(method, url, body);
    if (extensions !== undefined) return structuredClone(extensions) as T;
    const data = await this.workspace();
    const answer = route(data, method, url, body);
    if (answer === undefined) throw new DemoError(method !== 'GET');
    // A copy, so a page that changes what it was given cannot change the demo under it.
    return structuredClone(answer) as T;
  }

  /**
   * Extensions can be tried in the demo: the catalog is the one the page ships with, and what is
   * installed is kept on this page only, so the board can be seen on the made-up threads.
   */
  private readonly installed = new Map<string, { enabled: boolean; granted: Permission[]; at: string }>();

  private extensionAnswer(method: string, url: string, body: unknown): unknown {
    const parts = new URL(url, 'http://demo').pathname.split('/').filter(Boolean).slice(1);
    if (parts[0] !== 'extensions') return undefined;
    const state = (m: ExtensionManifest): ExtensionState => {
      const r = this.installed.get(m.id);
      return { manifest: m, source: 'builtin', installed: Boolean(r), enabled: Boolean(r?.enabled), granted: r?.granted ?? [], installedAt: r?.at ?? null, updatedAt: r?.at ?? null };
    };
    if (method === 'GET' && !parts[1]) return BUILTIN_MANIFESTS.map(state);
    const m = BUILTIN_MANIFESTS.find((x) => x.id === parts[1]);
    if (!m) throw new ApiError(t('demo.notInDemo'), {}, 404);
    const at = new Date().toISOString();
    if (method === 'GET' && parts[2] === 'memory' && parts[3] === 'sources') {
      const r = this.installed.get(m.id);
      if (!r?.enabled || !r.granted.includes('memory.read')) throw new ApiError(t('demo.notInDemo'), {}, 403);
      return [];
    }
    if (method === 'POST' && parts[2] === 'install' && !parts[3]) {
      const granted = (body as { granted?: unknown } | undefined)?.granted;
      const exact = Array.isArray(granted) && granted.length === m.permissions.length && m.permissions.every((p) => granted.includes(p));
      if (!exact) throw new ApiError(t('demo.notInDemo'), {}, 400);
      this.installed.set(m.id, { enabled: true, granted: [...m.permissions], at });
      return state(m);
    }
    if (method === 'PATCH' && !parts[2]) {
      const r = this.installed.get(m.id);
      if (!r) throw new ApiError(t('demo.notInDemo'), {}, 409);
      this.installed.set(m.id, { ...r, enabled: (body as { enabled?: unknown } | undefined)?.enabled === true, at });
      return state(m);
    }
    if (method === 'DELETE' && !parts[2]) {
      this.installed.delete(m.id);
      return null;
    }
    return undefined;
  }

  override stream<T>(_path: string, _onEvent: (ev: T) => void, signal: AbortSignal): Promise<void> {
    // Nothing ever happens in the demo. Settling only once aborted keeps callers from reconnecting.
    return untilAborted(signal);
  }

  override uploadVideo(_threadId: string, _file: File): Promise<VideoRef> {
    return Promise.reject(new DemoError(true));
  }

  override threadFile(_threadId: string, path: string): Promise<Blob> {
    const picture = demoPicture(path);
    return picture ? Promise.resolve(new Blob([picture], { type: 'image/svg+xml' })) : Promise.reject(new DemoError(false));
  }

  /** A page the demo wrote: a small stand-in with its name, served the way a signed link would be. */
  override previewLink(_threadId: string, path: string): Promise<{ url: string }> {
    const picture = demoPicture(path);
    const name = path.split('/').pop() ?? path;
    // The demo's stand-in page is written by an agent, not by the interface, so it stays in the
    // demo's own words: a real hub serves the page the agent left in the thread's folder.
    const page = picture ?? `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${name}</title></head><body style="font:16px/1.6 system-ui,sans-serif;margin:0;padding:40px;color:#1f1e1c;background:#f8f6f1"><h1 style="margin:0 0 8px;font-size:22px">${name}</h1><p style="margin:0;color:#6d6a63">Example page: in a real hub this is the page the agent wrote into the thread's folder.</p></body></html>`;
    const type = picture ? 'image/svg+xml' : 'text/html';
    return Promise.resolve({ url: `data:${type};charset=utf-8,${encodeURIComponent(page)}` });
  }

  override attachment(_threadId: string, _attachmentId: string): Promise<Blob> {
    return Promise.reject(new DemoError(false));
  }
}

/** What a thread with nothing to show is answered with: an overview that exists but is empty. */
const NO_OUTPUTS: ThreadOutputs = { changes: { files: [], added: 0, removed: 0, total: 0 }, images: [], files: [], seq: 0 };

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
    case 'machine-usage':
      return demoMachineUsage(q);
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
      case 'stats':
        return data.stats[t.id] ?? undefined;
      case 'events': {
        const after = Number(q.get('after') ?? 0);
        const before = Number(q.get('before') ?? Infinity);
        return events.filter((ev) => ev.seq > after && ev.seq < before);
      }
      case 'coordination':
        return data.coordinations[t.id] ?? null;
      case 'outputs':
        return data.outputs[t.id] ?? NO_OUTPUTS;
      case 'history': {
        // The one change row opens each of its turns' diffs; the demo reads them from the very
        // tool events the made-up thread ran.
        if (parts[3] !== 'detail') return undefined;
        const turn = q.get('turn') ?? '';
        const id = Number(q.get('id'));
        const start = events.find((ev) => ev.turnId === turn && ev.type === 'tool.start' && ev.data.id === id);
        return start ? { version: Number(q.get('version') ?? 1), content: start.data.content ?? null, nextCursor: null } : undefined;
      }
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

/** The demo machine's made-up usage: a day-shaped curve with an offline night and idle steps, so
 *  the machines tab has something to read. Every step's value is seeded by the step itself, so a
 *  poll keeps the past where it was. Shares stay unfiltered whatever the query filters. */
function demoMachineUsage(q: URLSearchParams): MachineUsageReport {
  const asked = Number(q.get('hours'));
  const hours: 1 | 24 | 168 = asked === 1 || asked === 168 ? asked : 24;
  const step = hours === 1 ? 60 : hours === 24 ? 600 : 3600;
  const modelFilter = q.get('model');
  // An empty launcher (a session the hub did not start) travels as `-`, the daemon's convention.
  const launcherFilter = q.get('launcher');
  const wanted = (model: string, launcher: string) =>
    (!modelFilter || model === modelFilter) && (launcherFilter === null || (launcherFilter === '-' ? '' : launcherFilter) === launcher);
  const models = [{ name: 'opus', share: 0.4 }, { name: 'sonnet', share: 0.6 }];
  const launchers = [{ name: 'claude', share: 0.5 }, { name: 'codex', share: 0.3 }, { name: '', share: 0.2 }];
  const noise = (n: number) => { const x = Math.sin(n * 12.9898) * 43758.5453; return x - Math.floor(x); };
  const zero = (): UsageTokens => ({ input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0 });
  const total = zero();
  const byModel = new Map(models.map(m => [m.name, { name: m.name, ...zero() }]));
  const byLauncher = new Map(launchers.map(l => [l.name, { name: l.name, ...zero() }]));
  // The four kinds' share of a row's tokens: cache-heavy, the way a long agent day looks.
  const split: Array<[keyof UsageTokens, number]> = [['input', 0.26], ['output', 0.07], ['cacheWrite', 0.11], ['cacheRead', 0.56]];

  const stepMs = step * 1000;
  const now = Math.floor(Date.now() / stepMs) * stepMs;
  const start = now - (hours * 3_600_000 - stepMs);
  const points: UsagePoint[] = [];
  for (let at = start; at <= now; at += stepMs) {
    const index = at / stepMs;
    const point: UsagePoint = { t: at / 1000, ...zero(), gap: true };
    const hour = new Date(at).getHours();
    // The machine was off overnight — ten minutes ago, within the hour view.
    const offline = hours === 1 ? now - at >= 40 * 60_000 && now - at < 50 * 60_000 : hour >= 1 && hour < 7;
    if (!offline) {
      point.gap = false;
      const day = hour >= 10 && hour < 20 ? 1 : 0.35;
      const volume = 5_000 * (step / 60) * day * (0.3 + 1.4 * noise(index));
      for (const model of models) for (const launcher of launchers) {
        const row = volume * model.share * launcher.share * (noise(index * 3 + model.share * 10 + launcher.share * 7) < 0.25 ? 0 : 1);
        if (!row) continue;
        const count = Math.max(1, Math.round(row / 9_000));
        for (const into of [byModel.get(model.name)!, byLauncher.get(launcher.name)!, ...wanted(model.name, launcher.name) ? [point, total] : []]) {
          for (const [kind, share] of split) into[kind] += row * share;
          into.replies += count;
        }
      }
    }
    points.push(point);
  }
  const round = (u: UsageTokens): UsageTokens => ({
    input: Math.round(u.input), cacheWrite: Math.round(u.cacheWrite), cacheRead: Math.round(u.cacheRead),
    output: Math.round(u.output), replies: Math.round(u.replies),
  });
  const shares = (map: Map<string, UsageTokens & { name: string }>) => [...map.values()]
    .map(share => ({ name: share.name, ...round(share) }))
    .filter(share => share.input + share.cacheWrite + share.cacheRead + share.output > 0)
    .sort((a, b) => (b.input + b.cacheWrite + b.cacheRead + b.output) - (a.input + a.cacheWrite + a.cacheRead + a.output));
  return {
    hours, step, total: round(total),
    points: points.map(point => ({ t: point.t, gap: point.gap, ...round(point) })),
    models: shares(byModel), launchers: shares(byLauncher),
  };
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

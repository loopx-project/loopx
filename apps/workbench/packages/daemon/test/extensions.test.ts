import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import type { DaemonExtension, Envelope, ExtensionManifest, ExtensionState } from '@calli/extension-api';
import { AttachmentStore } from '../src/attachments.js';
import type { StoredEvent } from '../src/events.js';
import { ExtensionHost, threadSummary, type HostSource } from '../src/extension-host.js';
import { builtinManifestPaths, checkManifest, loadCatalog } from '../src/extensions.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager, type ThreadView } from '../src/threads.js';

const TOKEN = 'test-token-extensions';
const SECRET = 'SECRET-conversation-text';
const flush = () => new Promise<void>((r) => setImmediate(r));

const text = (s: string) => ({ zh: s, en: s, ja: s });
const manifest = (id: string, extra: Partial<ExtensionManifest> = {}): ExtensionManifest => ({
  id,
  version: '1.0.0',
  apiVersion: 1,
  name: text(id),
  description: text(`${id} extension`),
  permissions: ['events.summary'],
  contributes: { views: [{ id, title: text(id), icon: 'board', route: `#/${id}`, sidebar: { pinned: true } }] },
  ...extra,
});

const V1_HOOKS = ['machine.ready', 'thread.created', 'thread.changed', 'turn.started', 'turn.finished', 'attention.raised', 'attention.resolved'] as const;

// ---------------------------------------------------------------- install state over HTTP

async function serve(dir: string, catalog: ExtensionManifest[]) {
  const store = new Store(dir);
  const manager = new ThreadManager(store, new Map(), [dir], new AttachmentStore(dir));
  const extensions = new ExtensionHost({ store: store.extensions, source: manager, catalog });
  extensions.start();
  const server = createHttpServer({ manager, extensions, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) =>
    fetch(base + path, { method, headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  return {
    api,
    base,
    async close() {
      extensions.dispose();
      await new Promise<void>((r) => server.close(() => r()));
      server.closeAllConnections();
      store.close();
    },
  };
}

test('extensions install, toggle and uninstall over the API, and the state outlives a restart', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-ext-'));
  const catalog = [manifest('board'), manifest('probe', { permissions: ['events.summary', 'notify'] })];
  try {
    let hub = await serve(dir, catalog);
    let list = (await (await hub.api('/api/extensions')).json()) as ExtensionState[];
    assert.deepEqual(list.map((e) => [e.manifest.id, e.source, e.installed, e.enabled]), [['board', 'builtin', false, false], ['probe', 'builtin', false, false]]);

    // Changes are announced on the list stream, so other devices refetch.
    const ctrl = new AbortController();
    const stream = await fetch(`${hub.base}/api/stream`, { headers: { Authorization: `Bearer ${TOKEN}` }, signal: ctrl.signal });
    const reader = stream.body!.getReader();
    let heard = '';
    const reading = (async () => {
      for (;;) {
        const { value, done } = await reader.read().catch(() => ({ value: undefined, done: true }));
        if (done) return;
        heard += new TextDecoder().decode(value);
      }
    })();

    // The grant has to be exactly what the manifest asks for.
    for (const granted of [undefined, [], ['events.summary'], ['events.summary', 'notify', 'files.read'], ['events.summary', 'events.summary'], 'events.summary']) {
      const res = await hub.api('/api/extensions/probe/install', 'POST', { granted });
      assert.equal(res.status, 400, JSON.stringify(granted));
    }
    const installed = await hub.api('/api/extensions/probe/install', 'POST', { granted: ['notify', 'events.summary'] });
    assert.equal(installed.status, 200);
    const state = (await installed.json()) as ExtensionState;
    assert.equal(state.installed, true);
    assert.equal(state.enabled, true);
    assert.deepEqual(state.granted, ['events.summary', 'notify']);
    assert.ok(state.installedAt);

    assert.equal((await hub.api('/api/extensions/probe', 'PATCH', { enabled: 'no' })).status, 400);
    const off = (await (await hub.api('/api/extensions/probe', 'PATCH', { enabled: false })).json()) as ExtensionState;
    assert.equal(off.installed, true);
    assert.equal(off.enabled, false);
    // Not installed: nothing to switch.
    assert.equal((await hub.api('/api/extensions/board', 'PATCH', { enabled: true })).status, 409);

    // Unknown ids are not found, whatever is asked.
    assert.equal((await hub.api('/api/extensions/nope/install', 'POST', { granted: [] })).status, 404);
    assert.equal((await hub.api('/api/extensions/nope', 'PATCH', { enabled: true })).status, 404);
    assert.equal((await hub.api('/api/extensions/nope', 'DELETE')).status, 404);

    await new Promise((r) => setTimeout(r, 50));
    assert.ok(heard.includes('event: extensions.changed'), 'the stream announced the change');
    ctrl.abort();
    await reading;
    await hub.close();

    // A new daemon on the same data reads the same state.
    hub = await serve(dir, catalog);
    list = (await (await hub.api('/api/extensions')).json()) as ExtensionState[];
    const probe = list.find((e) => e.manifest.id === 'probe')!;
    assert.deepEqual([probe.installed, probe.enabled, probe.granted], [true, false, ['events.summary', 'notify']]);
    assert.equal((await (await hub.api('/api/extensions/probe', 'PATCH', { enabled: true })).json()).enabled, true);

    assert.equal((await hub.api('/api/extensions/probe', 'DELETE')).status, 204);
    list = (await (await hub.api('/api/extensions')).json()) as ExtensionState[];
    assert.deepEqual(list.map((e) => e.installed), [false, false]);
    // Removing what is not installed is no error.
    assert.equal((await hub.api('/api/extensions/probe', 'DELETE')).status, 204);
    await hub.close();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a grant made for fewer permissions does not switch on a version that asks for more', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-ext-'));
  try {
    let hub = await serve(dir, [manifest('probe')]);
    assert.equal((await hub.api('/api/extensions/probe/install', 'POST', { granted: ['events.summary'] })).status, 200);
    await hub.close();
    hub = await serve(dir, [manifest('probe', { version: '2.0.0', permissions: ['events.summary', 'notify'] })]);
    const [probe] = (await (await hub.api('/api/extensions')).json()) as ExtensionState[];
    assert.deepEqual([probe.installed, probe.enabled], [true, false]);
    assert.equal((await hub.api('/api/extensions/probe', 'PATCH', { enabled: true })).status, 409);
    assert.equal((await hub.api('/api/extensions/probe/install', 'POST', { granted: ['events.summary', 'notify'] })).status, 200);
    await hub.close();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('the built-in catalog is read from the extension packages, and a broken manifest is left out', () => {
  const catalog = loadCatalog();
  assert.ok(catalog.some((m) => m.id === 'board' && m.permissions.includes('events.summary')), 'packages/ext-board/manifest.json');
  assert.ok(builtinManifestPaths().every((p) => p.endsWith('manifest.json')));

  const dir = mkdtempSync(join(tmpdir(), 'calli-ext-'));
  try {
    writeFileSync(join(dir, 'good.json'), JSON.stringify(manifest('good')));
    writeFileSync(join(dir, 'bad.json'), '{ not json');
    writeFileSync(join(dir, 'clash.json'), JSON.stringify(manifest('clash', { contributes: { views: [{ id: 'v', title: text('v'), icon: 'board', route: '#/todos' }] } })));
    const warnings: string[] = [];
    const read = loadCatalog([join(dir, 'good.json'), join(dir, 'bad.json'), join(dir, 'clash.json'), join(dir, 'missing.json')], (m) => warnings.push(m));
    assert.deepEqual(read.map((m) => m.id), ['good']);
    assert.equal(warnings.length, 3);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  assert.throws(() => checkManifest({ ...manifest('x'), apiVersion: 2 }), /apiVersion/);
  assert.throws(() => checkManifest({ ...manifest('x'), permissions: ['root'] }), /permission/);
  assert.throws(() => checkManifest({ ...manifest('X') }), /id/);
});

// ---------------------------------------------------------------- the notification bus

function view(id: string, extra: Partial<ThreadView> = {}): ThreadView {
  return {
    id, agent: 'claude', cwd: '/work/app', title: 'Fix the build', agentSessionId: null, status: 'idle', pinned: false,
    permissionMode: 'auto_edit', model: null, effort: null, archivedAt: null, sectionId: null, forkedFrom: null,
    unread: false, hubTurnAt: null, externalAt: null, schedule: null, createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z',
    // Content that must never reach an extension.
    preview: SECRET, queued: [{ id: 'q', text: SECRET, images: 0, videos: 0 }],
    worktree: null, resumeCommand: null, cwdReal: '/work/app', waiting: false, waitingReason: null, terminalActivity: false, scratch: false, runningSince: null,
    ...extra,
  };
}

function fakeSource(threads: Map<string, ThreadView>) {
  const listeners = new Set<(ev: StoredEvent) => void>();
  let seq = 0;
  const source: HostSource = {
    subscribe(l) { listeners.add(l); return () => listeners.delete(l); },
    listThreads: () => [...threads.values()],
    threadView: (id) => { const v = threads.get(id); if (!v) throw new Error('gone'); return v; },
  };
  const emit = (threadId: string, turnId: string | null, type: StoredEvent['type'], data: Record<string, unknown>) => {
    const ev = { threadId, seq: ++seq, turnId, type, ts: new Date().toISOString(), data };
    for (const l of listeners) l(ev);
  };
  return { source, emit };
}

function probe(id: string, extra: Partial<ExtensionManifest> = {}) {
  const got: Envelope[] = [];
  const impl: DaemonExtension = { manifest: manifest(id, { subscribes: [...V1_HOOKS], ...extra }), notify: (e) => void got.push(e) };
  return { impl, got, hooks: () => got.map((e) => e.hook) };
}

async function bus(opts: { queueLimit?: number } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-ext-bus-'));
  const store = new Store(dir);
  const threads = new Map([['t1', view('t1')]]);
  const { source, emit } = fakeSource(threads);
  const p = probe('probe');
  const quiet = probe('quiet', { permissions: [] });
  const host = new ExtensionHost({ store: store.extensions, source, catalog: [p.impl.manifest, quiet.impl.manifest], queueLimit: opts.queueLimit });
  host.register(p.impl);
  host.register(quiet.impl);
  host.install('probe', ['events.summary']);
  host.install('quiet', []);
  host.start();
  await flush();
  return { host, threads, emit, p, quiet, close: () => { host.dispose(); store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('subscribers get whitelisted summaries: no text, tool calls, answers, preview or queued messages', async () => {
  const h = await bus();
  try {
    assert.deepEqual(h.p.hooks(), ['extension.activated', 'machine.ready']);
    assert.equal(h.p.got[0].generation, h.host.generation);
    h.p.got.length = 0;

    h.threads.set('t1', view('t1', { status: 'running', runningSince: '2026-01-01T00:01:00.000Z' }));
    h.emit('t1', 'turn-1', 'turn.started', { text: SECRET, displayText: SECRET, images: [{ id: 'img', name: SECRET }], coordination: { messageId: 'm', from: 'lead' } });
    h.threads.set('t1', view('t1', { status: 'running', runningSince: '2026-01-01T00:01:00.000Z', waiting: true, waitingReason: 'approval' }));
    h.emit('t1', 'turn-1', 'approval.request', { approvalId: 'a1', title: SECRET, options: [{ optionId: 'allow', name: SECRET }], toolCall: { title: SECRET, rawInput: { command: SECRET } } });
    h.threads.set('t1', view('t1', { status: 'running', runningSince: '2026-01-01T00:01:00.000Z' }));
    h.emit('t1', 'turn-1', 'approval.resolved', { approvalId: 'a1', decision: { optionId: 'allow' } });
    h.emit('t1', 'turn-1', 'input.request', { inputId: 'i1', kind: 'plan', questions: [{ id: 'q', question: SECRET, options: [{ label: SECRET, value: SECRET }] }] });
    h.emit('t1', 'turn-1', 'input.resolved', { inputId: 'i1', answer: { answers: { q: [SECRET] } } });
    // A request that arrives already answered (full access approves a plan itself) raises no
    // attention: nothing is being asked, so the hooks below stay as they are.
    h.emit('t1', 'turn-1', 'input.request', { inputId: 'i2', kind: 'plan', auto: { answers: { approval: ['exit-plan-bypass'] } }, questions: [{ id: 'approval', question: SECRET, options: [{ label: SECRET, value: SECRET }] }] });
    h.emit('t1', 'turn-1', 'message.delta', { text: SECRET });
    h.emit('t1', 'turn-1', 'tool.start', { id: 'tool', title: SECRET, rawInput: SECRET });
    h.emit('t1', 'turn-1', 'turn.done', { stopReason: 'end_turn' });
    h.threads.set('t1', view('t1', { unread: true }));
    h.emit('t1', null, 'thread.updated', {});
    await flush();

    const all = JSON.stringify(h.p.got);
    assert.ok(!all.includes(SECRET), all);
    assert.deepEqual(h.p.hooks(), [
      'turn.started', 'thread.changed',
      'attention.raised', 'thread.changed',
      'attention.resolved', 'thread.changed',
      'attention.raised',
      'attention.resolved',
      'turn.finished',
      'thread.changed',
    ]);
    const byHook = (hook: string) => h.p.got.filter((e) => e.hook === hook).map((e) => e.data as any);
    assert.deepEqual(byHook('turn.started')[0].turn.origin, { initiator: 'coordination', imported: false, fromTerminal: false, control: false });
    assert.deepEqual(byHook('attention.raised').map((d) => d.wait), [
      { threadId: 't1', turnId: 'turn-1', kind: 'approval', id: 'a1' },
      { threadId: 't1', turnId: 'turn-1', kind: 'plan', id: 'i1' },
    ]);
    assert.deepEqual(byHook('attention.resolved').map((d) => [d.wait.kind, d.how]), [['approval', 'answered'], ['plan', 'answered']]);
    const finished = byHook('turn.finished')[0].turn;
    assert.equal(finished.outcome, 'ok');
    assert.equal(finished.origin.initiator, 'coordination');
    const changes = byHook('thread.changed');
    assert.deepEqual(changes[1].thread.attention, { kind: 'approval' });
    assert.deepEqual(changes.at(-1).thread.attention, { kind: 'unread' });
    assert.deepEqual(changes.at(-1).fields.sort(), ['attention', 'runningSince', 'status', 'unread']);
    for (const { thread } of changes) {
      assert.ok(!('preview' in thread) && !('queued' in thread));
      assert.equal(thread.project, '/work/app');
    }
    // Every notification is numbered within its thread, in order.
    const seqs = h.p.got.map((e) => e.seq);
    assert.deepEqual(seqs, [...seqs].sort((a, b) => a - b));

    // Without events.summary nothing about threads arrives, and the host API refuses.
    assert.deepEqual(h.quiet.hooks(), ['extension.activated', 'machine.ready']);
    assert.throws(() => h.host.threadsList('quiet'), /events\.summary/);
    const listed = h.host.threadsList('probe');
    assert.equal(listed.generation, h.host.generation);
    assert.ok(!JSON.stringify(listed).includes(SECRET));
    assert.equal(h.host.threadsGet('probe', 'nope'), null);
  } finally {
    h.close();
  }
});

test('a queue that runs over is dropped for host.resync, and a disabled extension hears nothing more', async () => {
  const h = await bus({ queueLimit: 4 });
  try {
    h.p.got.length = 0;
    for (let i = 0; i < 20; i++) {
      h.threads.set('t1', view('t1', { title: `title ${i}` }));
      h.emit('t1', null, 'thread.updated', {});
    }
    await flush();
    const resyncs = h.p.got.filter((e) => e.hook === 'host.resync');
    assert.ok(resyncs.length >= 1);
    assert.deepEqual(resyncs[0].data, { reason: 'overflow' });
    assert.ok(h.p.got.length < 20, `only what fit was delivered (${h.p.got.length})`);
    // What arrives after the resync is the newest state.
    assert.equal((h.p.got.at(-1)!.data as any).thread.title, 'title 19');

    // Disabled with notifications still queued: they are dropped, and later ones never come.
    h.p.got.length = 0;
    h.emit('t1', 'turn-2', 'turn.started', { text: 'x' });
    h.host.setEnabled('probe', false);
    h.emit('t1', 'turn-2', 'turn.done', { stopReason: 'cancelled' });
    await flush();
    assert.deepEqual(h.p.got.map((e) => [e.hook, e.data]), [['extension.deactivating', { extensionId: 'probe', reason: 'disabled' }]]);

    // Enabled again: it starts over from extension.activated, without machine.ready.
    h.host.setEnabled('probe', true);
    await flush();
    assert.deepEqual(h.p.hooks().slice(1), ['extension.activated']);
    h.host.uninstall('probe');
    assert.equal(h.p.got.at(-1)!.hook, 'extension.deactivating');
    assert.deepEqual(h.p.got.at(-1)!.data, { extensionId: 'probe', reason: 'uninstalled' });
  } finally {
    h.close();
  }
});

test('the summary of a thread is the sidebar\'s view of it', () => {
  const s = threadSummary(view('t2', {
    worktree: { root: '/work/app-wt', branch: 'fix', of: '/work/app', ofReal: '/work/app' },
    cwd: '/work/app-wt/sub', schedule: { id: 's', name: 'nightly' }, handover: { sourceId: 't1', title: 'x', agent: 'codex' },
    coordination: { rootId: 't2', children: 3, paused: false }, status: 'error', unread: true, archivedAt: '2026-01-02T00:00:00.000Z',
  }));
  assert.deepEqual(s, {
    id: 't2', title: 'Fix the build', agent: 'claude', project: '/work/app', projectGone: undefined, status: 'error', attention: { kind: 'error' }, runningSince: null,
    unread: true, pinned: false, archived: true, scheduled: true, worktree: true, handover: true, teamSize: 3,
    doneAt: null, report: null, createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z',
  });
  // The progress line is content: daemon-side extensions never get it, only the done mark.
  const reported = threadSummary(view('r', { doneAt: '2026-01-03T00:00:00.000Z', report: { stage: 'review', note: 'secret', at: '2026-01-03T00:00:00.000Z' } }));
  assert.deepEqual([reported.doneAt, reported.report], ['2026-01-03T00:00:00.000Z', null]);
  // A member counts no team of its own.
  assert.equal(threadSummary(view('m', { coordination: { rootId: 't2', children: 3, paused: false } })).teamSize, 0);
});

for (const [name, previous, current] of [
  ['shrinking', ['events.summary', 'notify'], ['events.summary']],
  ['replacing', ['events.summary'], ['notify']],
  ['removing all', ['events.summary'], []],
] as const) {
  test(`changed grants require confirmation after ${name} permissions`, async () => {
    const dir = mkdtempSync(join(tmpdir(), 'calli-ext-grants-'));
    const store = new Store(dir);
    const { source } = fakeSource(new Map([['t1', view('t1')]]));
    store.extensions.install('probe', '1.0.0', [...previous]);
    const host = new ExtensionHost({ store: store.extensions, source, catalog: [manifest('probe', { permissions: [...current] })] });
    try {
      assert.equal(host.list()[0].enabled, false);
      assert.throws(() => host.setEnabled('probe', true), (e: any) => e.status === 409);
      assert.throws(() => host.threadsList('probe'), (e: any) => e.status === 403);
      assert.throws(() => host.threadsGet('probe', 't1'), (e: any) => e.status === 403);
      host.install('probe', [...current]);
      assert.equal(host.list()[0].enabled, true);
      if (!current.includes('events.summary' as never)) assert.throws(() => host.threadsList('probe'), (e: any) => e.status === 403);
    } finally { host.dispose(); store.close(); rmSync(dir, { recursive: true, force: true }); }
  });
}

test('thread.created distinguishes imported history from a new thread', async () => {
  const h = await bus();
  try {
    h.emit('t1', null, 'thread.created', { imported: true });
    h.emit('t1', null, 'thread.created', {});
    await flush();
    assert.deepEqual(h.p.got.filter(e => e.hook === 'thread.created').map(e => (e.data as any).origin), ['import', 'new']);
  } finally { h.close(); }
});

test('board upgrades defer only assistant.ask, preserve enablement, and grant it explicitly', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-board-upgrade-'));
  const old = manifest('board', { permissions: ['events.summary', 'progress.report', 'worksource.read'] });
  const next = { ...old, version: '2.0.0', permissions: [...old.permissions, 'assistant.ask' as const] };
  let hub = await serve(dir, [old]);
  try {
    assert.equal((await hub.api('/api/extensions/board/install', 'POST', { granted: old.permissions })).status, 200);
    await hub.close();
    hub = await serve(dir, [next]);
    let state = (await (await hub.api('/api/extensions')).json() as ExtensionState[])[0];
    assert.equal(state.installed, true);
    assert.equal(state.enabled, true);
    assert.deepEqual(state.granted, old.permissions, 'reading the upgraded catalog never grants assistant access');
    assert.equal((await hub.api('/api/extensions/board', 'PATCH', { enabled: false })).status, 200);
    assert.equal((await hub.api('/api/extensions/board', 'PATCH', { enabled: true })).status, 200);
    assert.equal((await hub.api('/api/extensions/board/install', 'POST', { granted: old.permissions })).status, 400, 'install still requires the exact manifest');
    assert.equal((await hub.api('/api/extensions/board/install', 'POST', { granted: next.permissions })).status, 200);
    state = (await (await hub.api('/api/extensions')).json() as ExtensionState[])[0];
    assert.ok(state.granted.includes('assistant.ask'));
    await hub.close();
    hub = await serve(dir, [next]);
    assert.ok(((await (await hub.api('/api/extensions')).json() as ExtensionState[])[0]).granted.includes('assistant.ask'), 'grant survives restart');
  } finally { await hub.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('deferred assistant access neither authorizes the call nor waives other permission changes', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-board-permissions-'));
  const store = new Store(dir);
  const old = manifest('board', { permissions: ['events.summary', 'progress.report'] });
  const next = { ...old, permissions: [...old.permissions, 'assistant.ask' as const] };
  store.extensions.install('board', old.version, old.permissions);
  const source = fakeSource(new Map()).source;
  const host = new ExtensionHost({ store: store.extensions, source, catalog: [next] });
  try {
    assert.equal(host.allows('board', 'events.summary'), true);
    assert.equal(host.allows('board', 'progress.report'), true);
    assert.equal(host.allows('board', 'assistant.ask'), false);
    assert.throws(() => host.demand('board', 'assistant.ask'));
    host.install('board', next.permissions);
    assert.equal(host.allows('board', 'assistant.ask'), true);
    host.setEnabled('board', false);
    assert.equal(host.allows('board', 'assistant.ask'), false);
    assert.doesNotThrow(() => checkManifest(next));
    store.extensions.install('board', '1.0.0', ['events.summary']);
    assert.equal(host.list()[0].enabled, false, 'missing progress.report still disables the update');
  } finally { host.dispose(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

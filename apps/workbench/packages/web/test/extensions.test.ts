import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ExtensionManifest, ExtensionState, ThreadSummary } from '@calli/extension-api';
import type { Thread } from '../src/api';
import { DemoApi } from '../src/demo/DemoApi';
import { demoData } from '../src/demo/data';
import { BUILTIN_MANIFESTS } from '../src/extensions/catalog';
import { hasPermission, needsConfirm } from '../src/extensions/permissions';
import { createWebHost, extensionStorage, type HostInputs } from '../src/extensions/host';
import { machineSummaries, threadMachine, parseThreadLink, threadSummaries, threadSummary } from '../src/extensions/project';
import { enabledViews, extensionHash, looksLikeExtensionRoute, resolveExtensionRoute } from '../src/extensions/routes';

const SECRET = 'SECRET what the user asked';

function thread(id: string, over: Partial<Thread> = {}): Thread {
  return {
    id, agent: 'claude', cwd: '/work/code', cwdReal: '/work/code', title: id, agentSessionId: null, status: 'idle', pinned: false,
    permissionMode: 'auto_edit', model: null, effort: null, archivedAt: null, sectionId: null, forkedFrom: null, unread: false,
    terminalActivity: false, waiting: false, resumeCommand: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-02T00:00:00Z',
    preview: SECRET, queued: [{ id: 'q', text: SECRET, images: 0 } as Thread['queued'][number]],
    ...over,
  };
}

test('a thread summary carries the whitelisted fields and nothing a conversation said', () => {
  const s = threadSummary(
    thread('a', {
      status: 'running', runningSince: '2026-01-02T00:00:00Z', unread: true, schedule: { id: 's', name: 'nightly' },
      worktree: { root: '/work/code-wt', branch: 'fix', of: '/work/code', ofReal: '/work/code' }, cwd: '/work/code-wt',
      handover: { sourceId: 'b', title: 'x', agent: 'codex' }, coordination: { rootId: 'a', children: 2, paused: false },
    }),
    null,
  );
  assert.deepEqual(Object.keys(s).sort(), [
    'agent', 'archived', 'attention', 'createdAt', 'doneAt', 'handover', 'id', 'pinned', 'project', 'projectGone', 'report', 'runningSince',
    'scheduled', 'status', 'teamSize', 'title', 'unread', 'updatedAt', 'worktree',
  ]);
  assert.ok(!JSON.stringify(s).includes(SECRET));
  assert.equal(s.project, '/work/code', 'a worktree counts under the project it was made from');
  assert.deepEqual([s.scheduled, s.worktree, s.handover, s.teamSize, s.runningSince], [true, true, true, 2, '2026-01-02T00:00:00Z']);
  assert.deepEqual(s.attention, { kind: 'unread' });

  // The thread on screen is never unread; a finished one has no running time.
  const open = threadSummary(thread('a', { unread: true, status: 'error', runningSince: 'stale' }), 'a');
  assert.deepEqual([open.unread, open.attention, open.runningSince], [false, null, null]);
  assert.deepEqual(threadSummary(thread('b', { unread: true, status: 'error' }), 'a').attention, { kind: 'error' });
  assert.deepEqual(threadSummary(thread('c', { waiting: true, waitingReason: 'question' }), null).attention, { kind: 'question' });
});

test('views get live threads, with team members only where their lead is missing', () => {
  const list = threadSummaries(
    [
      thread('lead', { coordination: { rootId: 'lead', children: 1, paused: false } }),
      thread('member', { coordination: { rootId: 'lead', children: 1, paused: false } }),
      thread('orphan', { coordination: { rootId: 'gone', children: 1, paused: false } }),
      thread('old', { archivedAt: '2026-01-01T00:00:00Z' }),
    ],
    null,
  );
  assert.deepEqual(list.map((t) => t.id), ['lead', 'orphan']);
});

const text = (s: string) => ({ zh: s, en: s, ja: s });
const manifest = (id: string, route?: `#/${string}`): ExtensionManifest => ({
  id, version: '1.0.0', apiVersion: 1, name: text(id), description: text(id), permissions: ['events.summary'],
  contributes: { views: [{ id: 'main', title: text(id), icon: 'board', ...(route ? { route } : {}), sidebar: { pinned: true } }] },
});
const state = (m: ExtensionManifest, installed: boolean, enabled = installed): ExtensionState => ({
  manifest: m, source: 'builtin', installed, enabled, granted: installed ? m.permissions : [], installedAt: null, updatedAt: null,
});

test('extension addresses: an alias or #/ext/<id>/<view>, only while installed and switched on', () => {
  const board = manifest('board', '#/board');
  const plain = manifest('plain');
  const on = [state(board, true), state(plain, true)];
  assert.deepEqual(resolveExtensionRoute('#/board', on), { extensionId: 'board', view: board.contributes.views![0] });
  assert.equal(resolveExtensionRoute('#/ext/board/main', on)?.extensionId, 'board');
  assert.equal(resolveExtensionRoute('#/ext/plain', on)?.view.id, 'main', 'without a view, its first');
  assert.equal(resolveExtensionRoute('#/ext/plain/nope', on), null);
  assert.equal(resolveExtensionRoute('#/nope', on), null);
  assert.equal(extensionHash(enabledViews(on)[0]), '#/board');
  assert.equal(extensionHash(enabledViews(on)[1]), '#/ext/plain/main');
  // Not installed, or switched off: nothing answers, and the page falls back to the threads.
  assert.equal(resolveExtensionRoute('#/board', [state(board, false)]), null);
  assert.equal(resolveExtensionRoute('#/board', [state(board, true, false)]), null);
  assert.equal(resolveExtensionRoute('#/ext/board/main', [state(board, true, false)]), null);

  const core = ['#/todos', '#/extensions'];
  assert.equal(looksLikeExtensionRoute('#/board', core), true);
  assert.equal(looksLikeExtensionRoute('#/ext/x/y', core), true);
  assert.equal(looksLikeExtensionRoute('#/todos', core), false);
  assert.equal(looksLikeExtensionRoute('', core), false);
  assert.equal(looksLikeExtensionRoute('#section-2', core), false);
});

test('the web host tells views about real changes only, and keeps their storage apart', () => {
  const inputs = (threads: ThreadSummary[], connection: HostInputs['connection'] = 'live'): HostInputs => ({ threads, connection, observedAt: null, agents: [], extension: state(manifest('board'), true) });
  const opened: string[] = [];
  const a = threadSummary(thread('a'), null);
  const live = createWebHost('board', inputs([a]), { openThread: (id) => opened.push(id), ui: () => ({}) as never, storage: extensionStorage('board', null) });
  const heard: string[] = [];
  const off = live.host.threads.subscribe((list) => heard.push(list.map((t) => t.title).join()));
  live.host.connection.subscribe((s) => heard.push(s));

  live.update(inputs([{ ...a }])); // the same list read again
  assert.deepEqual(heard, []);
  live.update(inputs([{ ...a, title: 'renamed' }]));
  live.update(inputs([{ ...a, title: 'renamed' }], 'reconnecting'));
  assert.deepEqual(heard, ['renamed', 'reconnecting']);
  assert.equal(live.host.threads.snapshot()[0].title, 'renamed');
  assert.equal(live.host.connection.state(), 'reconnecting');
  off();
  live.update(inputs([{ ...a, title: 'again' }], 'reconnecting'));
  assert.deepEqual(heard, ['renamed', 'reconnecting']);
  live.host.navigate.openThread('a');
  assert.deepEqual(opened, ['a']);

  const kept = new Map<string, string>();
  const store = { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v), removeItem: (k: string) => void kept.delete(k) };
  const mine = extensionStorage('board', store);
  mine.set('filters', '{"project":"x"}');
  assert.deepEqual([...kept.keys()], ['calli.ext.board.filters']);
  assert.equal(mine.get('filters'), '{"project":"x"}');
  assert.equal(extensionStorage('other', store).get('filters'), null);
  mine.set('filters', null);
  assert.equal(kept.size, 0);
});

test('the demo lists the built-in extensions and installs them for the page only', async () => {
  const api = new DemoApi(async () => demoData(Date.parse('2026-10-16T08:00:00Z')));
  assert.ok(BUILTIN_MANIFESTS.some((m) => m.id === 'board'));
  let list = await api.extensions();
  assert.deepEqual(list.map((e) => [e.manifest.id, e.installed]), BUILTIN_MANIFESTS.map((m) => [m.id, false]));
  const board = list.find((e) => e.manifest.id === 'board')!;
  await assert.rejects(api.installExtension('board', []), (e: Error & { status?: number }) => e.status === 400);
  const installed = await api.installExtension('board', board.manifest.permissions);
  assert.deepEqual([installed.installed, installed.enabled], [true, true]);
  assert.equal((await api.setExtensionEnabled('board', false)).enabled, false);
  await api.uninstallExtension('board');
  list = await api.extensions();
  assert.equal(list.find((e) => e.manifest.id === 'board')!.installed, false);
  // Another demo page starts with nothing installed.
  assert.equal((await new DemoApi(async () => demoData(0)).extensions()).some((e) => e.installed), false);
});

const authorizedInputs = (extra: Partial<HostInputs> = {}): HostInputs => ({
  threads: [threadSummary(thread('private-thread'), null)], connection: 'live', observedAt: '2026-01-02T00:00:00Z', agents: [],
  extension: state(manifest('probe'), true), ...extra,
});

test('web host gates snapshots and notifications on the current exact permission grant', () => {
  const noPermissions = state({ ...manifest('probe'), permissions: [] }, true);
  const live = createWebHost('probe', authorizedInputs({ extension: noPermissions }), { openThread: () => {}, ui: () => ({}) as never });
  const heard: ThreadSummary[][] = [];
  live.host.threads.subscribe(list => heard.push(list));
  assert.deepEqual(live.host.threads.snapshot(), []);
  live.update(authorizedInputs({ extension: noPermissions, threads: [threadSummary(thread('second'), null)] }));
  assert.deepEqual(heard, []);
  live.update(authorizedInputs());
  assert.equal(heard.length, 1);
  assert.equal(live.host.threads.snapshot()[0].id, 'private-thread');
  const deniedStates: Array<ExtensionState | null> = [null, state(manifest('probe'), false), state(manifest('probe'), true, false), { ...state(manifest('probe'), true), granted: ['events.summary', 'notify'] }, noPermissions];
  for (const denied of deniedStates) {
    const count: number = heard.length;
    live.update(authorizedInputs({ extension: denied }));
    assert.deepEqual(live.host.threads.snapshot(), []);
    assert.equal(heard.length, count, 'no delivery while access is denied');
  }
});

test('one throwing extension subscriber cannot escape the host or block other callbacks', () => {
  const failures: unknown[] = [];
  const live = createWebHost('probe', authorizedInputs(), { openThread: () => {}, ui: () => ({}) as never, onError: (error: unknown) => failures.push(error) });
  let heard = 0;
  live.host.threads.subscribe(() => { throw new Error('broken thread consumer'); });
  live.host.threads.subscribe(() => heard++);
  live.host.connection.subscribe(() => { throw new Error('broken connection consumer'); });
  live.host.connection.subscribe(() => heard++);
  assert.doesNotThrow(() => live.update(authorizedInputs({ threads: [], connection: 'reconnecting' })));
  assert.equal(heard, 2);
  assert.equal(failures.length, 2);
});

test('connection subscribers hear snapshot freshness changes without a connection transition', () => {
  const live = createWebHost('probe', authorizedInputs(), { openThread: () => {}, ui: () => ({}) as never });
  const times: Array<string | null> = [];
  live.host.connection.subscribe(() => times.push(live.host.connection.observedAt()));
  live.update(authorizedInputs({ observedAt: '2026-01-03T00:00:00Z' }));
  assert.deepEqual(times, ['2026-01-03T00:00:00Z']);
});

test('malformed extension routes safely fall back to threads', () => {
  for (const hash of ['#/ext/%', '#/ext/probe/%', '#/ext/%E0%A4%A/main']) {
    assert.equal(resolveExtensionRoute(hash, [state(manifest('probe'), true)]), null);
  }
});

test('work projections update live and disappear immediately when content permission is revoked', () => {
  const m = manifest('work-view'); m.permissions.push('worksource.read');
  const initial: HostInputs = { extension: state(m, true), threads: [], agents: [], connection: 'live', observedAt: null, workSources: [] };
  const live = createWebHost('work-view', initial, { openThread: () => {}, ui: () => ({}) as never });
  const received: unknown[] = [];
  live.host.workSources.subscribe(items => received.push(items));
  const projection = { sourceId: 'source', sourceName: text('Source'), binding: { id: 'b', externalId: 'g', settings: {}, threadIds: ['a'], consumers: [] }, snapshot: null, freshness: 'unknown' as const, observedAt: null, generation: 'g', revision: 1 };
  live.update({ ...initial, workSources: [projection] });
  assert.deepEqual(live.host.workSources.snapshot(), [projection]);
  live.update({ ...initial, extension: { ...initial.extension!, granted: ['events.summary'] }, workSources: [projection] });
  assert.deepEqual(live.host.workSources.snapshot(), []);
  assert.deepEqual(received, [[projection], []]);
});

test('the progress line reaches only views granted it, and marking done checks the grant first', async () => {
  const report = { stage: 'review' as const, note: 'ready', at: '2026-01-02T00:00:00Z' };
  const listed = threadSummaries([thread('a', { doneAt: null, report }), thread('b', { doneAt: '2026-01-03T00:00:00Z' })], null);
  assert.deepEqual(listed.map((t) => [t.doneAt, t.report]), [[null, report], ['2026-01-03T00:00:00Z', null]]);
  const m = manifest('board'); m.permissions.push('progress.report');
  const initial: HostInputs = { extension: state(m, true), threads: listed, agents: [], connection: 'live', observedAt: null };
  const marked: Array<[string, boolean]> = [];
  const live = createWebHost('board', initial, { openThread: () => {}, ui: () => ({}) as never, setThreadDone: async (id, done) => void marked.push([id, done]) });
  assert.deepEqual(live.host.threads.snapshot()[0].report, report);
  await live.host.threads.setDone('a', true);
  await assert.rejects(live.host.threads.setDone('missing', true));
  assert.deepEqual(marked, [['a', true]]);

  // Without the grant the line is gone, and taking the grant away is news.
  const received: ThreadSummary[][] = [];
  live.host.threads.subscribe((next) => received.push(next));
  live.update({ ...initial, extension: { ...initial.extension!, manifest: manifest('board'), granted: ['events.summary'] } });
  assert.equal(live.host.threads.snapshot()[0].report, null);
  assert.equal(received.at(-1)?.[0].report, null);
  assert.ok(!JSON.stringify(live.host.threads.snapshot()).includes('ready'));
  const plain = createWebHost('probe', { ...initial, extension: state(manifest('probe'), true) }, { openThread: () => {}, ui: () => ({}) as never });
  assert.deepEqual(plain.host.threads.snapshot().map((t) => t.report), [null, null]);
});

test('memory host never queries without installation and drops results after revocation', async () => {
  const memoryManifest = { ...manifest('memory'), permissions: ['memory.read'] as const };
  const m: ExtensionManifest = { ...memoryManifest, permissions: [...memoryManifest.permissions] };
  const input = (installed: boolean, enabled = installed): HostInputs => ({ ...authorizedInputs(), extension: state(m, installed, enabled) });
  let reads = 0;
  let deliver!: (value: import('@calli/extension-api').MemoryDocument) => void;
  const live = createWebHost('memory', input(false), {
    openThread: () => {}, ui: () => ({}) as never,
    api: {
      memorySources: async () => { reads++; return [{ id: 'source', agents: [] }]; },
      memoryList: async () => ({ state: 'empty', files: [], checkedAt: '', partial: false }),
      memoryRead: () => new Promise(resolve => { reads++; deliver = resolve; }),
    },
  });
  await assert.rejects(live.host.memory.sources());
  assert.equal(reads, 0);
  live.update(input(true));
  assert.equal((await live.host.memory.sources())[0].id, 'source');
  const pending = live.host.memory.read('source', 'MEMORY.md');
  live.update(input(true, false));
  deliver({ id: 'MEMORY.md', text: 'private', modifiedAt: '', truncated: false });
  await assert.rejects(pending);
  await assert.rejects(live.host.memory.sources());
  assert.equal(reads, 2);
});


test('merged summaries label each machine and route UUIDs without falling back locally', () => {
  const sources = [{ hostId: null, label: 'Here', threads: [thread('a')] }, { hostId: 'dev', label: 'Dev', threads: [thread('b')] }];
  const merged = machineSummaries(sources, null, true);
  assert.deepEqual(merged.map(t => t.machine), [{ id: null, label: 'Here' }, { id: 'dev', label: 'Dev' }]);
  assert.equal(merged[1].report, null, 'remote machines without progress provide no invented report');
  assert.equal(machineSummaries(sources.slice(1), null, false)[0].machine, undefined);
  assert.equal(threadMachine(sources, 'a'), null);
  assert.equal(threadMachine(sources, 'b'), 'dev');
  assert.throws(() => threadMachine(sources, 'missing'));
});

test('machine freshness notifies thread subscribers; stage needs progress; thread links preserve machines', async () => {
  const board = { ...manifest('board'), permissions: ['events.summary', 'progress.report', 'assistant.ask'] as const };
  const extension = state({ ...board, permissions: [...board.permissions] }, true);
  const inputs: HostInputs = { extension, threads: [
    { ...threadSummary(thread('remote'), null), machine: { id: 'dev box', label: 'Dev' } },
    { ...threadSummary(thread('local'), null), machine: { id: null, label: 'Here' } },
  ], machines: [{ id: 'dev box', label: 'Dev', state: 'cached', observedAt: null }, { id: null, label: 'Here', state: 'live', observedAt: null }], connection: 'live', observedAt: null, agents: [] };
  const calls: unknown[] = [];
  const live = createWebHost('board', inputs, { openThread: id => calls.push(['open', id]),
    setThreadStage: async (id, stage) => { calls.push(['stage', id, stage]); },
    setThreadDone: async (id, done) => { calls.push(['done', id, done]); },
    askAssistant: async text => { calls.push(['ask', text]); },
    navigate: { peekThread: id => calls.push(['peek', id]), threadMenu: (id, at) => calls.push(['menu', id, at]), newThread: options => calls.push(['new', options]) },
    ui: () => ({}) as never });
  let notified = 0;
  live.host.threads.subscribe(() => { notified++; });
  live.update({ ...inputs, machines: inputs.machines!.map(m => ({ ...m, state: 'failed' })) });
  assert.equal(notified, 1);
  assert.equal(live.host.machines()[0].state, 'failed');
  assert.equal(live.host.assistant!.threadLink('remote'), '#/thread/remote?machine=dev%20box');
  assert.equal(live.host.assistant!.threadLink('local'), '#/thread/local');
  assert.deepEqual(parseThreadLink(live.host.assistant!.threadLink('remote')), { id: 'remote', machine: 'dev box' });
  assert.deepEqual(parseThreadLink('#/thread/old'), { id: 'old', machine: null });
  assert.equal(parseThreadLink('#/thread/%zz'), null);
  await live.host.threads.setStage('remote', 'review');
  await live.host.threads.setDone('remote', true);
  live.host.navigate.peekThread('remote');
  live.host.navigate.threadMenu('remote', { x: 1, y: 2 });
  live.host.navigate.newThread({ project: '/work/app', machine: 'dev box' });
  assert.deepEqual(calls.slice(0, 2), [['stage', 'remote', 'review'], ['done', 'remote', true]]);
  assert.deepEqual(calls.slice(2), [['peek', 'remote'], ['menu', 'remote', { x: 1, y: 2 }], ['new', { project: '/work/app', machine: 'dev box' }]]);
  live.update({ ...inputs, extension: state(manifest('board'), true) });
  await assert.rejects(live.host.threads.setStage('remote', 'doing'), /permission/i);
  await assert.rejects(live.host.assistant!.ask('summary'), /permission/i);
});


test('only the built-in board can defer assistant consent without losing its original grant', () => {
  const m = { ...manifest('board'), permissions: ['events.summary', 'progress.report', 'assistant.ask'] as import('@calli/extension-api').Permission[] };
  const upgraded = { ...state(m, true), granted: ['events.summary', 'progress.report'] as import('@calli/extension-api').Permission[] };
  assert.equal(needsConfirm(upgraded), false);
  assert.equal(hasPermission(upgraded, 'events.summary'), true);
  assert.equal(hasPermission(upgraded, 'assistant.ask'), false);
  assert.equal(needsConfirm({ ...upgraded, source: 'external' }), true);
  assert.equal(needsConfirm({ ...upgraded, granted: ['events.summary'] }), true);
  assert.equal(hasPermission({ ...upgraded, enabled: false }, 'events.summary'), false);
  assert.equal(hasPermission({ ...upgraded, granted: m.permissions }, 'assistant.ask'), true);
});

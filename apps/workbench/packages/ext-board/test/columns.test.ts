import test from 'node:test';
import assert from 'node:assert/strict';
import type { AttentionKind, ProgressStage, ThreadSummary } from '@calli/extension-api';
import { parseBacklogOpen, buildColumns, cardFor, GONE_PROJECT, markAllDone, matchesFilters, projectKey, projectLabels, projectOptions, DONE_WINDOW_MS, emptyFilters, firstColumn, parseFilters, projectName, REVIEW_WINDOW_MS, showStaleWarning } from '../src/columns';
import { t } from '../src/i18n';
import manifest from '../manifest.json';

const now = Date.parse('2026-10-18T12:00:00Z');
const iso = (time: number) => new Date(time).toISOString();

test('shows stale warning only when disconnected after a successful sync', () => {
  for (const state of ['live', 'reconnecting', 'offline'] as const) {
    assert.equal(showStaleWarning(state, null), false, `${state} before first sync or in demo`);
    assert.equal(showStaleWarning(state, iso(now)), state !== 'live', `${state} after sync`);
  }
});

const thread = (id: string, patch: Partial<ThreadSummary> = {}): ThreadSummary => ({
  id, title: id, agent: 'agent-1', project: '/projects/one', status: 'idle', attention: null,
  runningSince: null, unread: false, pinned: false, archived: false, scheduled: false,
  worktree: false, handover: false, teamSize: 0, doneAt: null, report: null, createdAt: iso(now), updatedAt: iso(now), ...patch,
});
const report = (stage: ProgressStage, at = now) => ({ stage, note: 'n', at: iso(at) });
const columnOf = (item: ThreadSummary) => cardFor(item).column;

for (const kind of ['approval', 'question', 'plan', 'error'] satisfies AttentionKind[]) {
  test(`${kind} needs the person, even while running or reported`, () => {
    const item = thread(kind, { attention: { kind }, status: 'running', report: report('review') });
    assert.deepEqual(cardFor(item).reason, { kind: 'attention', attention: kind });
    assert.deepEqual(buildColumns([item], emptyFilters(), now).map((column) => column.total), [0, 0, 1, 0, 0]);
  });
}

test('running is in progress, whatever the agent last reported', () => {
  assert.deepEqual(cardFor(thread('a', { status: 'running', runningSince: iso(now), report: report('review') })).reason, { kind: 'running', since: iso(now) });
});

test('a finished turn without a report waits to be checked, unread or not', () => {
  assert.deepEqual(cardFor(thread('a')).reason, { kind: 'idle', unread: false });
  const unread = cardFor(thread('a', { unread: true, attention: { kind: 'unread' } }));
  assert.equal(unread.column, 'review');
  assert.deepEqual(unread.reason, { kind: 'idle', unread: true });
});

test('the agent report moves an idle thread between doing, waiting and review', () => {
  assert.equal(columnOf(thread('a', { report: report('doing') })), 'doing');
  assert.equal(columnOf(thread('a', { report: report('blocked') })), 'waiting');
  assert.equal(columnOf(thread('a', { report: report('review') })), 'review');
  assert.equal(cardFor(thread('a', { report: report('review') })).reason.kind, 'report');
});

test('only the person makes a thread done, and it stays done', () => {
  assert.equal(columnOf(thread('a', { doneAt: iso(now), attention: { kind: 'error' }, report: report('blocked') })), 'done');
});

test('sorts pinned first, then by the latest move, without changing the input', () => {
  const items = [
    thread('older', { updatedAt: iso(now - 2000) }),
    thread('reported', { updatedAt: iso(now - 5000), report: report('review', now + 1) }),
    thread('newest'),
    thread('pinned-old', { pinned: true, updatedAt: iso(now - 5000) }),
  ];
  const before = structuredClone(items);
  assert.deepEqual(buildColumns(items, emptyFilters(), now)[3].cards.map((card) => card.thread.id), ['pinned-old', 'reported', 'newest', 'older']);
  assert.deepEqual(items, before);
});

test('folds threads to review after 7 days and done ones after a day, per column', () => {
  const items = [
    thread('recent'),
    thread('boundary', { updatedAt: iso(now - REVIEW_WINDOW_MS) }),
    thread('old', { updatedAt: iso(now - REVIEW_WINDOW_MS - 1) }),
    thread('old-waiting', { updatedAt: iso(now - REVIEW_WINDOW_MS - 1), attention: { kind: 'question' } }),
    thread('done', { doneAt: iso(now - 1000), updatedAt: iso(now - REVIEW_WINDOW_MS - 1) }),
    thread('done-old', { doneAt: iso(now - DONE_WINDOW_MS - 1) }),
  ];
  const columns = buildColumns(items, emptyFilters(), now);
  assert.deepEqual(columns.map((column) => column.total), [0, 0, 1, 3, 2]);
  assert.deepEqual(columns.map((column) => column.olderCount), [0, 0, 0, 1, 1]);
  assert.deepEqual(columns[3].cards.map((card) => card.thread.id), ['recent', 'boundary']);
  assert.deepEqual(columns[4].cards.map((card) => card.thread.id), ['done']);
  const opened = buildColumns(items, emptyFilters(), now, new Set(['done']));
  assert.deepEqual([opened[3].cards.length, opened[4].cards.length], [2, 2], 'opening one column leaves the other folded');
});

test('combines project, instance and schedule filters and leaves archived threads out', () => {
  const items = [
    thread('match-one', { scheduled: true }),
    thread('match-two', { project: '/projects/two', agent: 'agent-2', scheduled: true }),
    thread('other-project', { project: '/projects/three', scheduled: true }),
    thread('not-scheduled'),
    thread('archived', { archived: true }),
  ];
  const ids = (filters: Parameters<typeof buildColumns>[1]) => buildColumns(items, filters, now)[3].cards.map((card) => card.thread.id).sort();
  assert.deepEqual(ids({ tags: [], project: ['/projects/one', '/projects/two'], agent: ['agent-1', 'agent-2'], scheduled: [true], machine: [] }), ['match-one', 'match-two']);
  assert.deepEqual(ids({ ...emptyFilters(), scheduled: [false] }), ['not-scheduled']);
  assert.equal(buildColumns(items, emptyFilters(), now)[3].total, 4);
});

test('backlog is the person\'s word: done, waiting and running outrank it, unread does not', () => {
  const at = new Date(now).toISOString();
  const later = { stage: 'backlog' as const, note: '', at, by: 'user' as const };
  assert.equal(cardFor(thread('a', { report: later })).column, 'backlog');
  assert.equal(cardFor(thread('a', { report: later, unread: true, attention: { kind: 'unread' } })).column, 'backlog');
  assert.equal(cardFor(thread('a', { report: later, status: 'running', runningSince: at })).column, 'doing');
  assert.equal(cardFor(thread('a', { report: later, attention: { kind: 'approval' } })).column, 'waiting');
  assert.equal(cardFor(thread('a', { report: later, doneAt: at })).column, 'done');
  const old = thread('old', { report: { ...later, at: new Date(now - 30 * 24 * 3600_000).toISOString() }, updatedAt: new Date(now - 30 * 24 * 3600_000).toISOString() });
  const columns = buildColumns([old], emptyFilters(), now);
  assert.deepEqual(columns.map((column) => column.id), ['backlog', 'doing', 'waiting', 'review', 'done']);
  assert.deepEqual([columns[0].cards.length, columns[0].olderCount], [1, 0], 'never folded by age');
});

test('the backlog rail starts collapsed and only an explicit true opens it', () => {
  assert.equal(parseBacklogOpen(null), false);
  assert.equal(parseBacklogOpen('true'), true);
  for (const bad of ['', '{bad', 'false', '"true"', '1', 'null', '{}']) assert.equal(parseBacklogOpen(bad), false);
});

test('phones open on the first column that asks something of the person', () => {
  const first = (items: ThreadSummary[]) => firstColumn(buildColumns(items, emptyFilters(), now));
  assert.equal(first([]), 'waiting');
  assert.equal(first([thread('a', { status: 'running' })]), 'doing');
  assert.equal(first([thread('a', { status: 'running' }), thread('b')]), 'review');
  assert.equal(first([thread('b'), thread('c', { attention: { kind: 'approval' } })]), 'waiting');
  const later = thread('l', { report: { stage: 'backlog', note: '', at: new Date(now).toISOString(), by: 'user' } });
  assert.equal(first([later]), 'waiting', 'never opens on the backlog');
  assert.equal(first([later, thread('b')]), 'review');
});

test('invalid persisted filters fall back safely and mixed values are sanitized', () => {
  for (const input of [null, '', '{bad', 'null', '42']) assert.deepEqual(parseFilters(input), emptyFilters());
  assert.deepEqual(parseFilters('{"v":2,"project":["/a",1,"/a"],"agent":false,"scheduled":[true,"false",false,true]}'), {
    tags: [], project: ['/a'], agent: [], scheduled: [true, false], machine: [],
  });
  // Projects saved before they were resolved paths are dropped once; the other filters stay.
  assert.deepEqual(parseFilters('{"project":["/home/me/code"],"agent":["codex"]}'), { tags: [], project: [], agent: ['codex'], scheduled: [], machine: [] });
});

test('project labels use the final directory segment', () => {
  assert.equal(projectName('/projects/one/'), 'one');
  assert.equal(projectName('C:\\projects\\two\\'), 'two');
  assert.equal(projectName('/'), '/');
});

test('translations keep numeric messages and English plurals', () => {
  assert.equal(t('zh', 'runningMinutes', 0), '刚开始运行');
  assert.equal(t('ja', 'runningMinutes', 1), '1 分間実行中');
  assert.equal(t('en', 'runningMinutes', 2), 'Running for 2 minutes');
  assert.equal(t('en', 'olderDone', 1), 'Show 1 thread done earlier');
  assert.equal(t('en', 'olderReview', 3), 'Show 3 threads older than 3 days');
  assert.equal(t('zh', 'team', 3), '协作 3 人');
});

test('manifest asks for summaries, the progress line, shared work and the assistant', () => {
  assert.equal(manifest.id, 'board');
  assert.deepEqual(manifest.permissions, ['events.summary', 'progress.report', 'worksource.read', 'assistant.ask']);
  assert.deepEqual(manifest.contributes.views, [{
    id: 'board', title: manifest.name, icon: 'board', route: '#/board', sidebar: { pinned: true },
  }]);
});

test('old threads waiting to be checked are listed for clearing, and clearing reports progress and failures', async () => {
  const now = Date.parse('2026-11-10T12:00:00.000Z');
  const iso = (ms: number) => new Date(ms).toISOString();
  const [, , , review] = buildColumns([
    thread('fresh', { updatedAt: iso(now - 1000) }),
    thread('stale', { updatedAt: iso(now - REVIEW_WINDOW_MS - 1) }),
    thread('staler', { updatedAt: iso(now - REVIEW_WINDOW_MS - 2) }),
  ], emptyFilters(), now);
  assert.deepEqual(review.older.map((card) => card.thread.id), ['stale', 'staler']);
  assert.equal(REVIEW_WINDOW_MS, 3 * 24 * 60 * 60 * 1000);

  const marked: string[] = [];
  const seen: number[] = [];
  const failed = await markAllDone(['a', 'b', 'bad', 'c', 'd', 'e'], async (id) => {
    if (id === 'bad') throw new Error('offline');
    marked.push(id);
  }, (done) => seen.push(done));
  assert.equal(failed, 1);
  assert.deepEqual(marked.sort(), ['a', 'b', 'c', 'd', 'e']);
  assert.deepEqual(seen, [1, 2, 3, 4, 5, 6]);
  assert.equal(await markAllDone([], async () => undefined, () => undefined), 0);
});

test('projects are picked by resolved directory, most threads first, named only as long as needed', () => {
  const threads = [
    thread('a', { project: '/data/me/code/remote-agent-hub' }),
    thread('b', { project: '/data/me/code/remote-agent-hub' }),
    thread('c', { project: '/data/me/code/personal' }),
    thread('d', { project: '/data/me/x/personal' }),
    thread('e', { project: '' }),
    thread('f', { project: '' }),
    thread('g', { project: '/data/me/evals/.work/101-task-abc', projectGone: true }),
    thread('h', { project: '/data/me/evals/.work/102-task-def', projectGone: true }),
    thread('old', { project: '/data/me/archived', archived: true }),
  ];
  assert.equal(projectKey(threads[6]), GONE_PROJECT);
  assert.deepEqual(projectOptions(threads, ['/kept']).map(({ key, count }) => [key, count]), [
    ['', 2], [':gone', 2], ['/data/me/code/remote-agent-hub', 2], ['/data/me/code/personal', 1], ['/data/me/x/personal', 1], ['/kept', 0],
  ]);
  const labels = projectLabels(['', GONE_PROJECT, '/data/me/code/remote-agent-hub', '/data/me/code/personal', '/data/me/x/personal', '/a/same', '/b/a/same']);
  assert.deepEqual([...labels], [
    ['/data/me/code/remote-agent-hub', 'remote-agent-hub'],
    ['/data/me/code/personal', 'code/personal'],
    ['/data/me/x/personal', 'x/personal'],
    ['/a/same', 'a/same'],
    ['/b/a/same', 'b/a/same'],
  ]);
  const filters = { ...emptyFilters(), project: [GONE_PROJECT] };
  assert.deepEqual(threads.filter((item) => matchesFilters(item, filters)).map((item) => item.id), ['g', 'h']);
});

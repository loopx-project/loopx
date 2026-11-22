import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Section, Thread } from '../src/api.js';
import { buildActivity, buildMerged, buildSidebar, clampSidebarWidth, handoverGroups, markTerms, navOverflow, defaultSidebarWidth, savedSidebarWidth, searchTerms, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, taggedAgents, threadAttention, type HostSource } from '../src/sidebar.js';
import { threadMark } from '../src/notices.js';

let clock = 0;
function thread(id: string, over: Partial<Thread> = {}): Thread {
  clock += 1;
  return {
    id,
    agent: 'claude',
    cwd: '/work/code',
    title: id,
    agentSessionId: null,
    preview: null,
    status: 'idle',
    pinned: false,
    permissionMode: 'auto_edit',
    model: null,
    effort: null,
    archivedAt: null,
    sectionId: null,
    forkedFrom: null,
    unread: false,
    terminalActivity: false,
    waiting: false,
    queued: [],
    resumeCommand: null,
    createdAt: '2026-01-01T00:00:00Z',
    // Later calls are older, like the server's newest-first order.
    updatedAt: new Date(Date.UTC(2026, 0, 1) - clock * 1000).toISOString(),
    ...over,
    // Unless a test says otherwise, the directory is not reached through a symlink.
    cwdReal: over.cwdReal ?? over.cwd ?? '/work/code',
  };
}
const sections: Section[] = [
  { id: 's2', name: '调研', position: 2 },
  { id: 's1', name: '支付重构', position: 1 },
];
const ids = (list: Thread[]) => list.map((t) => t.id);

test('the desktop sidebar width is rounded, bounded and restored safely', () => {
  assert.equal(clampSidebarWidth(180), SIDEBAR_MIN_WIDTH);
  assert.equal(clampSidebarWidth(360.6), 361);
  assert.equal(clampSidebarWidth(900), SIDEBAR_MAX_WIDTH);
  assert.equal(savedSidebarWidth(null), null, 'nothing dragged: the width follows the window');
  assert.equal(savedSidebarWidth('not-a-width'), null);
  assert.equal(savedSidebarWidth('420'), 420);
  assert.equal(savedSidebarWidth('999'), SIDEBAR_MAX_WIDTH);
});

test('until it is dragged, the sidebar takes a quarter of the window, between 288 and 360', () => {
  assert.equal(defaultSidebarWidth(1024), 288);
  assert.equal(defaultSidebarWidth(1280), 320);
  assert.equal(defaultSidebarWidth(1440), 360);
  assert.equal(defaultSidebarWidth(2560), 360);
});

test('each thread appears once: pinned first, then its section, then its project', () => {
  const model = buildSidebar(
    [thread('a', { pinned: true, sectionId: 's1' }), thread('b', { sectionId: 's1' }), thread('c'), thread('d', { cwd: '/work/other' })],
    sections,
    '',
  );
  assert.deepEqual(ids(model.pinned), ['a']);
  assert.deepEqual(model.sections.map((s) => [s.section.name, ids(s.threads)]), [['支付重构', ['b']], ['调研', []]]);
  assert.deepEqual(model.projects.map((p) => [p.name, ids(p.threads)]), [['code', ['c']], ['other', ['d']]]);
  assert.equal(model.count, 4);
});

test('sections follow their position; empty ones stay visible so threads can be filed into them', () => {
  const model = buildSidebar([], sections, '');
  assert.deepEqual(model.sections.map((s) => s.section.id), ['s1', 's2']);
});

test('a thread whose section no longer exists goes back under its project', () => {
  const model = buildSidebar([thread('x', { sectionId: 'deleted' })], sections, '');
  assert.deepEqual(model.projects.map((p) => ids(p.threads)), [['x']]);
});

test('projects are ordered by their most recent thread', () => {
  const newest = thread('n', { cwd: '/work/b' });
  const older = thread('o', { cwd: '/work/a' });
  assert.deepEqual(buildSidebar([newest, older], [], '').projects.map((p) => p.name), ['b', 'a']);
  assert.deepEqual(buildSidebar([older, newest], [], '').projects.map((p) => p.name), ['b', 'a']);
});

test('search matches title or directory, ignores case, and hides sections with no match', () => {
  const list = [thread('Fix Login', { sectionId: 's1' }), thread('other'), thread('z', { cwd: '/work/LOGIN-service' })];
  const model = buildSidebar(list, sections, ' login ');
  assert.deepEqual(model.sections.map((s) => s.section.name), ['支付重构']);
  assert.deepEqual(model.projects.map((p) => p.name), ['LOGIN-service']);
  assert.equal(model.count, 2);
  assert.equal(buildSidebar(list, sections, 'nothing').count, 0);
});

test('one folder reached by two spellings is one project', () => {
  // The newer thread came from the agent, which reports the directory with symlinks resolved.
  const imported = thread('imported', { cwd: '/data/home/me/code', cwdReal: '/data/home/me/code' });
  const older = thread('older', { cwd: '/home/me/code', cwdReal: '/data/home/me/code' });
  const elsewhere = thread('elsewhere', { cwd: '/home/me/code-old', cwdReal: '/data/home/me/code-old' });
  const model = buildSidebar([imported, older, elsewhere], [], '');
  assert.deepEqual(model.projects.map((p) => [p.name, ids(p.threads)]), [['code', ['imported', 'older']], ['code-old', ['elsewhere']]]);
  assert.equal(model.projects[0].key, '/data/home/me/code');
  assert.equal(model.projects[0].cwd, '/data/home/me/code', 'the group shows the spelling of its most recent thread');
});

test('a mark several agents share gets the name only on the agents after its first one', () => {
  // Two accounts of one CLI, another agent, and one from a daemon that sends no marks.
  const agents = [{ id: 'claude', icon: 'M0 0h1' }, { id: 'claude-2', icon: 'M0 0h1' }, { id: 'pi', icon: 'M2 2h1' }, {}, {}];
  assert.deepEqual([...taggedAgents(agents)], ['claude-2']);
  assert.equal(taggedAgents([{ id: 'claude', icon: 'M0 0h1' }, { id: 'pi', icon: 'M2 2h1' }]).size, 0);
});

test('by activity: what wants attention or is at work comes first, the rest by the day it last moved', () => {
  // Local times, so the days fall the same in any time zone.
  const now = new Date(2026, 9, 12, 15, 0);
  const at = (daysAgo: number, hour = 12) => new Date(2026, 9, 12 - daysAgo, hour).toISOString();
  const list = [
    thread('pinned-old', { pinned: true, sectionId: 's1', updatedAt: at(40) }),
    thread('running', { status: 'running', updatedAt: at(0, 14) }),
    thread('today-late', { updatedAt: at(0, 13) }),
    thread('unread-old', { unread: true, updatedAt: at(3) }),
    thread('today-early', { updatedAt: at(0, 1) }),
    thread('yesterday', { updatedAt: at(1, 23) }),
    thread('waiting', { status: 'running', waiting: true, updatedAt: at(0, 9) }),
    thread('week', { updatedAt: at(7) }),
    thread('unread-new', { unread: true, status: 'error', updatedAt: at(0, 10) }),
    thread('month', { updatedAt: at(8) }),
    thread('failed-and-seen', { status: 'error', updatedAt: at(2) }),
  ];
  const model = buildActivity(list, '', now, null);
  assert.deepEqual(model.groups.map((g) => [g.label, ids(g.threads)]), [
    ['优先级', ['waiting', 'unread-new', 'unread-old', 'running']],
    ['今天', ['today-late', 'today-early']],
    ['昨天', ['yesterday']],
    ['过去 7 天', ['failed-and-seen', 'week']],
    ['过去 30 天', ['month']],
    ['更早', ['pinned-old']],
  ]);
  assert.equal(model.count, list.length);

  // The thread on screen is being read: its unread mark does not hold it at the top.
  const reading = buildActivity(list, '', now, 'unread-old');
  assert.deepEqual(ids(reading.groups[0].threads), ['waiting', 'unread-new', 'running']);
  assert.deepEqual(ids(reading.groups.find((g) => g.key === 'week')!.threads), ['failed-and-seen', 'unread-old', 'week']);

  const found = buildActivity(list, 'UNREAD', now, null);
  assert.deepEqual(found.groups.map((g) => [g.label, ids(g.threads)]), [['优先级', ['unread-new', 'unread-old']]]);
  assert.equal(found.count, 2);
  assert.deepEqual(buildActivity([], '', now, null), { groups: [], count: 0 });
});

test('pages beyond the first two sit behind the "more" button unless on screen or still live', () => {
  const pages = [
    { key: 'schedules', active: false },
    { key: 'desktop', active: false, live: false },
    { key: 'board', active: false },
  ];
  assert.deepEqual(navOverflow(pages), { shown: [], hidden: pages });
  // The page on screen and a desktop still connected stay rows of their own, in their order.
  assert.deepEqual(navOverflow([
    { key: 'schedules', active: true },
    { key: 'desktop', active: false, live: true },
    { key: 'board', active: false },
  ]), {
    shown: [{ key: 'schedules', active: true }, { key: 'desktop', active: false, live: true }],
    hidden: [{ key: 'board', active: false }],
  });
  assert.deepEqual(navOverflow([]), { shown: [], hidden: [] });
});

test('threads in no project sit together after the projects, unless pinned or filed in a section', () => {
  const model = buildSidebar(
    [
      thread('s1', { scratch: true, cwd: '/data/scratch/2026-10-12-aaaa' }),
      thread('p', { cwd: '/work/code' }),
      thread('s2', { scratch: true, cwd: '/data/scratch/2026-10-12-bbbb' }),
      thread('s3', { scratch: true, pinned: true, cwd: '/data/scratch/2026-10-12-cccc' }),
      thread('s4', { scratch: true, sectionId: 's1', cwd: '/data/scratch/2026-10-12-dddd' }),
    ],
    sections,
    '',
  );
  assert.deepEqual(ids(model.scratch), ['s1', 's2']);
  assert.deepEqual(model.projects.map((p) => p.name), ['code'], 'each scratch folder is not a project of its own');
  assert.deepEqual(ids(model.pinned), ['s3']);
  assert.deepEqual(ids(model.sections[0].threads), ['s4']);
});


test('handovers group by explicit ancestry and open the latest successor even when the original is active', () => {
  const a = thread('origin', { updatedAt: '2030-01-01' });
  const b = thread('successor', { handover: { sourceId: a.id, title: a.title, agent: a.agent, mode: 'continue' } });
  const c = thread('third', { handover: { sourceId: b.id, title: b.title, agent: b.agent, mode: 'continue' }, createdAt: '2027-01-01' });
  const unrelated = thread('same-title', { title: a.title });
  const groups = handoverGroups([a, unrelated, c, b]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].latest.id, c.id);
  assert.deepEqual(new Set(ids(groups[0].members)), new Set([a.id, b.id, c.id]));
  assert.equal(handoverGroups([a, unrelated])[0].members.length, 1, 'separate sections do not pull threads in');
  assert.equal(handoverGroups([c])[0].latest.id, c.id, 'missing parents stay usable');
  b.handover!.detached = true;
  const split = handoverGroups([a, b, c]);
  assert.equal(split.length, 2, 'a detached edge becomes two sidebar cards');
  assert.ok(split.some(group => ids(group.members).join() === a.id));
  assert.ok(split.some(group => group.members.length === 2 && ids(group.members).every(id => id === b.id || id === c.id)));
});

test('forks stay separate, and corrupt handover cycles terminate', () => {
  const a = thread('a');
  const b = thread('b', { forkedFrom: a.id });
  assert.equal(handoverGroups([a, b]).length, 2);
  a.handover = { sourceId: b.id, title: b.title, agent: b.agent, mode: 'continue' };
  b.handover = { sourceId: a.id, title: a.title, agent: a.agent, mode: 'continue' };
  assert.equal(handoverGroups([a, b]).length, 1);
});

test('the searched words are marked in a found passage, whatever their case and however they overlap', () => {
  assert.deepEqual(searchTerms('  Quota  额度 quota '), ['quota', '额度']);
  assert.deepEqual(markTerms('The Quota Board 的额度', 'quota 额度'), [
    { text: 'The ', hit: false }, { text: 'Quota', hit: true }, { text: ' Board 的', hit: false }, { text: '额度', hit: true },
  ]);
  // The longer word wins where two start at the same place.
  assert.deepEqual(markTerms('额度看板', '额度 额度看板'), [{ text: '额度看板', hit: true }]);
  assert.deepEqual(markTerms('没有命中', '别的'), [{ text: '没有命中', hit: false }]);
  assert.deepEqual(markTerms('原样', '  '), [{ text: '原样', hit: false }]);
});

test('threadAttention: waits first, in the kind asked, then results nobody has looked at', () => {
  assert.deepEqual(threadAttention(thread('a', { waiting: true, waitingReason: 'approval', unread: true, status: 'running' }), null), { kind: 'approval' });
  assert.deepEqual(threadAttention(thread('a', { waiting: true, waitingReason: 'question' }), null), { kind: 'question' });
  assert.deepEqual(threadAttention(thread('a', { waiting: true, waitingReason: 'plan' }), null), { kind: 'plan' });
  // Older daemons only say that something is awaited.
  assert.deepEqual(threadAttention(thread('a', { waiting: true }), null), { kind: 'approval' });
  // A wait stays a wait on screen: it is the thread asking, not news.
  assert.deepEqual(threadAttention(thread('a', { waiting: true, waitingReason: 'question' }), 'a'), { kind: 'question' });
  assert.deepEqual(threadAttention(thread('a', { unread: true, status: 'error' }), null), { kind: 'error' });
  assert.deepEqual(threadAttention(thread('a', { unread: true }), null), { kind: 'unread' });
  assert.equal(threadAttention(thread('a', { unread: true, status: 'error' }), 'a'), null, 'the thread on screen is being read');
  assert.equal(threadAttention(thread('a', { status: 'error' }), null), null, 'a failure already looked at wants nothing');
  assert.equal(threadAttention(thread('a', { status: 'running' }), null), null);
});

test('the sidebar dot, the activity order and threadAttention agree', () => {
  const cases = [
    thread('w', { waiting: true, waitingReason: 'approval', status: 'running' }),
    thread('e', { unread: true, status: 'error' }),
    thread('u', { unread: true }),
    thread('r', { status: 'running' }),
    thread('i'),
  ];
  const marks = cases.map((c) => threadMark(c, false));
  assert.deepEqual(marks, ['waiting', 'error', 'unread', 'running', null]);
  const priority = buildActivity(cases, '', new Date('2026-01-02T00:00:00Z'), null).groups.find((g) => g.key === 'priority')!;
  assert.deepEqual(priority.threads.map((x) => x.id), ['w', 'e', 'u', 'r']);
  assert.deepEqual(cases.map((c) => threadAttention(c, null)?.kind ?? null), ['approval', 'error', 'unread', null, null]);
});


test('manual and legacy handovers group in the sidebar just like automatic handovers', () => {
  const a = thread('source');
  const b = thread('branch', { handover: { sourceId: a.id, title: a.title, agent: a.agent, mode: 'branch' } });
  assert.equal(handoverGroups([a, b]).length, 1);
  delete b.handover!.mode;
  assert.equal(handoverGroups([a, b]).length, 1);
});

test('the merged list puts every machine in one activity order, names where each thread is, and keeps team members under their lead', () => {
  const now = new Date('2026-10-21T12:00:00');
  const th = (id: string, at: string, extra: Partial<Thread> = {}) => ({ id, title: id, cwd: '/w/p', agent: 'pi', status: 'idle', updatedAt: at, ...extra }) as Thread;
  const sources: HostSource[] = [
    { hostId: null, label: '本机', state: 'live', at: '2026-10-21T11:59:00', threads: [th('here', '2026-10-21T11:00:00'), th('lead', '2026-10-20T10:00:00', { coordination: { rootId: 'lead' } as Thread['coordination'] }), th('member', '2026-10-21T11:30:00', { coordination: { rootId: 'lead' } as Thread['coordination'] })] },
    { hostId: 'devbox', label: 'devbox', state: 'live', at: '2026-10-21T11:59:00', threads: [th('there', '2026-10-21T11:40:00'), th('asks', '2026-10-19T09:00:00', { waiting: true })] },
    { hostId: 'gpu', label: 'gpu', state: 'offline', at: '2026-10-20T08:00:00', threads: [th('old', '2026-10-20T07:00:00')] },
    { hostId: 'new', label: 'new', state: 'loading', at: null, threads: [] },
    { hostId: 'warm', label: 'warm', state: 'cached', at: '2026-10-21T11:00:00', threads: [] },
  ];
  const model = buildMerged(sources, '', now, 'here');
  const flat = model.groups.flatMap((g) => g.items.map((i) => `${g.key}:${i.hostId ?? 'local'}:${i.thread.id}`));
  assert.deepEqual(flat, ['priority:devbox:asks', 'today:devbox:there', 'today:local:here', 'yesterday:local:lead', 'yesterday:gpu:old']);
  assert.equal(model.count, 5, 'the member is under its lead, not a row of its own');
  assert.deepEqual(model.notes.map((n) => `${n.hostId}:${n.state}`), ['gpu:offline', 'new:loading'], 'a remembered list being refreshed needs no note');
  assert.deepEqual(buildMerged(sources, 'there', now, null).groups.flatMap((g) => g.items.map((i) => i.thread.id)), ['there']);
});

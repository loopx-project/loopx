import test from 'node:test';
import assert from 'node:assert/strict';
import type { ProgressStage, ThreadSummary } from '@calli/extension-api';
import type { MachineState } from '@calli/extension-api/web';
import { buildColumns, cardFor, emptyFilters, LOCAL_MACHINE, machineKey, machineOptions, matchesFilters, newThreadOptions, parseFilters, staleMachines, FILTERS_VERSION, GONE_PROJECT } from '../src/columns';
import { applyDrop, createLongPress, dropAction, withGuesses, type Guess } from '../src/drag';
import { buildSummary, pickCards, SUMMARY_LIMIT, type SummaryContext } from '../src/assist';

const now = Date.parse('2026-10-18T12:00:00Z');
const iso = (time: number) => new Date(time).toISOString();
const thread = (id: string, patch: Partial<ThreadSummary> = {}): ThreadSummary => ({
  id, title: id, agent: 'a', project: '/p/one', status: 'idle', attention: null,
  runningSince: null, unread: false, pinned: false, archived: false, scheduled: false,
  worktree: false, handover: false, teamSize: 0, doneAt: null, report: null, createdAt: iso(now), updatedAt: iso(now), ...patch,
});
const report = (stage: ProgressStage, note = 'n', at = now) => ({ stage, note, at: iso(at) });

test('a card goes to any column by hand, except that running and waiting ones can only be finished', () => {
  const idle = cardFor(thread('a'));
  assert.deepEqual(dropAction(idle, 'doing'), { kind: 'setStage', stage: 'doing' });
  assert.deepEqual(dropAction(idle, 'waiting'), { kind: 'setStage', stage: 'blocked' });
  assert.deepEqual(dropAction(idle, 'review'), { kind: 'none' });
  assert.deepEqual(dropAction(idle, 'done'), { kind: 'setDone' });
  assert.deepEqual(dropAction(idle, 'backlog'), { kind: 'setStage', stage: 'backlog' });
  const later = cardFor(thread('g', { report: report('backlog') }));
  assert.deepEqual(dropAction(later, 'review'), { kind: 'setStage', stage: 'review' });
  assert.deepEqual(dropAction(later, 'backlog'), { kind: 'none' });
  const reported = cardFor(thread('b', { report: report('doing') }));
  assert.deepEqual(dropAction(reported, 'review'), { kind: 'setStage', stage: 'review' });
  // Out of done is a stage, which clears done.
  const done = cardFor(thread('c', { doneAt: iso(now) }));
  assert.deepEqual(dropAction(done, 'review'), { kind: 'setStage', stage: 'review' });
  assert.deepEqual(dropAction(done, 'done'), { kind: 'none' });
  const running = cardFor(thread('d', { status: 'running', runningSince: iso(now) }));
  assert.deepEqual(dropAction(running, 'review'), { kind: 'reject', reason: 'running' });
  assert.deepEqual(dropAction(running, 'backlog'), { kind: 'reject', reason: 'running' });
  assert.deepEqual(dropAction(running, 'done'), { kind: 'setDone' });
  const asking = cardFor(thread('e', { attention: { kind: 'approval' } }));
  assert.deepEqual(dropAction(asking, 'doing'), { kind: 'reject', reason: 'needsYou' });
  assert.deepEqual(dropAction(asking, 'backlog'), { kind: 'reject', reason: 'needsYou' });
  assert.deepEqual(dropAction(asking, 'done'), { kind: 'setDone' });
  // An unread result is only a finished turn: it can be placed.
  assert.deepEqual(dropAction(cardFor(thread('f', { unread: true, attention: { kind: 'unread' } })), 'doing'), { kind: 'setStage', stage: 'doing' });
});

test('a drop shows at once and is forgotten when the host answers or the drop fails', () => {
  const base = thread('a');
  const action = dropAction(cardFor(base), 'doing');
  const guessed = applyDrop(base, action, now);
  assert.equal(cardFor(guessed).column, 'doing');
  assert.equal(cardFor(guessed).thread.report?.by, 'user');
  const guesses = new Map<string, Guess>([['a', { base, thread: guessed }]]);
  assert.equal(cardFor(withGuesses([base], guesses)[0]).column, 'doing');
  const moved = thread('a', { report: report('doing', '', now + 1000), updatedAt: iso(now + 1000) });
  assert.equal(withGuesses([moved], guesses)[0], moved);
  assert.equal(withGuesses([base], new Map())[0], base);
  assert.equal(applyDrop(thread('x', { report: report('review') }), { kind: 'setDone' }, now).doneAt, iso(now));
});

test('a long press fires after the delay unless the finger moves or lifts first', () => {
  const pending: { fn: () => void; ms: number; id: number }[] = [];
  let next = 0;
  const timers = { set: (fn: () => void, ms: number) => { const id = ++next; pending.push({ fn, ms, id }); return id; }, clear: (id: unknown) => { const i = pending.findIndex((item) => item.id === id); if (i >= 0) pending.splice(i, 1); } };
  const fired: [number, number][] = [];
  const press = createLongPress((x, y) => fired.push([x, y]), timers);
  press.start(10, 20);
  assert.equal(pending[0].ms, 500);
  press.move(15, 25);
  assert.equal(pending.length, 1, 'a small wobble keeps it');
  press.move(30, 20);
  assert.equal(pending.length, 0, 'moving past 10px is a scroll');
  press.start(1, 2);
  pending.shift()!.fn();
  assert.deepEqual(fired, [[1, 2]]);
  assert.equal(press.consumeClick(), true);
  assert.equal(press.consumeClick(), false, 'only the one click after it');
  press.start(0, 0);
  press.release();
  assert.equal(pending.length, 0);
  press.start(0, 0);
  press.trigger();
  assert.deepEqual(fired.at(-1), [0, 0]);
  press.release();
  pending.shift()!.fn();
  assert.equal(press.consumeClick(), false, 'a click that never came does not stay swallowed');
});

const local: MachineState = { id: null, label: 'hub', state: 'live', observedAt: iso(now) };
const dev: MachineState = { id: 'dev', label: 'devbox', state: 'failed', observedAt: iso(now - 180_000) };
const remote = (id: string | null, label: string) => ({ machine: { id, label } });

test('machines filter threads, count them, and never show a loading machine as empty', () => {
  const items = [thread('a', remote(null, 'hub')), thread('b', remote('dev', 'devbox')), thread('c', remote('dev', 'devbox')), thread('plain')];
  assert.equal(machineKey({ id: null }), LOCAL_MACHINE);
  const ids = (machine: string[]) => items.filter((item) => matchesFilters(item, { ...emptyFilters(), machine })).map((item) => item.id);
  assert.deepEqual(ids(['dev']), ['b', 'c', 'plain'], 'threads without a machine are not machine-filtered');
  assert.deepEqual(ids([LOCAL_MACHINE]), ['a', 'plain']);
  const loading: MachineState = { id: 'new', label: 'newbox', state: 'loading', observedAt: null };
  const never: MachineState = { id: 'gone', label: 'gone', state: 'offline', observedAt: null };
  const options = machineOptions(items, [local, dev, loading, never], ['old']);
  assert.deepEqual(options.map((option) => [option.key, option.count]), [[LOCAL_MACHINE, 1], ['dev', 2], ['new', null], ['gone', null], ['old', 0]]);
  assert.deepEqual(staleMachines([local, dev, loading, never]).map((machine) => machine.label), ['devbox', 'gone']);
});

test('a new chat takes the project and machine only when exactly one is chosen', () => {
  assert.deepEqual(newThreadOptions(emptyFilters()), {});
  assert.deepEqual(newThreadOptions({ ...emptyFilters(), project: ['/p/one'], machine: ['dev'] }), { project: '/p/one', machine: 'dev' });
  assert.deepEqual(newThreadOptions({ ...emptyFilters(), project: [''], machine: [LOCAL_MACHINE] }), { machine: null }, 'no project is not a project');
  assert.deepEqual(newThreadOptions({ ...emptyFilters(), project: [GONE_PROJECT] }), {});
  assert.deepEqual(newThreadOptions({ ...emptyFilters(), project: ['/a', '/b'], machine: ['x', 'y'] }), {});
});

test('saved filters read back with machines, and older versions lose only what they could not hold', () => {
  const saved = JSON.stringify({ ...emptyFilters(), project: ['/a'], machine: ['dev', ':local'], v: FILTERS_VERSION });
  assert.deepEqual(parseFilters(saved), { ...emptyFilters(), project: ['/a'], machine: ['dev', ':local'] });
  assert.deepEqual(parseFilters('{"v":2,"project":["/a"],"machine":["dev"]}'), { ...emptyFilters(), project: ['/a'] });
  assert.deepEqual(parseFilters('{"v":3,"machine":"dev"}'), emptyFilters());
});

const ctx: SummaryContext = {
  locale: 'en', now,
  threadLink: (id) => `calli://thread/${id}`,
  projectLabel: (key) => key.split('/').pop() || 'none',
  projectOf: (card) => card.thread.project,
};

test('the summary lists waiting first, then recent reviews, and counts what it leaves out', () => {
  const minutes = (n: number) => iso(now - n * 60_000);
  const items = [
    thread('run', { status: 'running', runningSince: minutes(5) }),
    thread('old-review', { updatedAt: minutes(300) }),
    thread('new-review', { updatedAt: minutes(10), report: report('review', 'diff ready\nsee [x]', now - 10 * 60_000) }),
    thread('ask', { title: '[Fix] Pay [v2]', attention: { kind: 'approval' }, updatedAt: minutes(120), ...remote('dev', 'devbox') }),
    thread('done', { doneAt: minutes(1) }),
  ];
  const columns = buildColumns(items, emptyFilters(), now, new Set(['backlog', 'doing', 'waiting', 'review', 'done']));
  assert.deepEqual(pickCards(columns).cards.map((card) => card.thread.id), ['ask', 'new-review', 'old-review', 'run']);
  const text = buildSummary(columns, ctx);
  assert.match(text, /^Please look at these 4 threads/);
  assert.match(text, /1\. \[Pay \\\[v2\\\]\]\(calli:\/\/thread\/ask\) — Needs you · Waiting for approval · stopped 2 hours ago · one · devbox/);
  assert.match(text, /2\. \[new-review\].*\n {3}> diff ready see \[x\]/);
  assert.match(text, /4\. \[run\].*Running for 5 minutes/);
  assert.doesNotMatch(text, /\[done\]/);
  assert.doesNotMatch(text, /put off until later/);
  assert.match(text, /Which ones really need me now\?/);

  const cut = buildSummary(columns, ctx, 2);
  assert.match(cut, /2 more threads are not listed \(1 in To review, 1 in In progress\)\./);
  assert.deepEqual(pickCards(columns, 2).omitted, { review: 1, doing: 1 });
});

test('the summary leaves backlog cards out and says how many', () => {
  const items = [thread('now'), thread('l1', { report: report('backlog') }), thread('l2', { report: report('backlog') })];
  const columns = buildColumns(items, emptyFilters(), now, new Set(['backlog', 'doing', 'waiting', 'review', 'done']));
  assert.deepEqual(pickCards(columns).cards.map((card) => card.thread.id), ['now']);
  const text = buildSummary(columns, ctx);
  assert.doesNotMatch(text, /\[l1\]/);
  assert.match(text, /2 more threads put off until later are not listed\./);
  assert.match(buildSummary(columns, { ...ctx, locale: 'zh' }), /另有 2 个以后再做的线程没有列出。/);
});

test('the summary stops at about 80 cards', () => {
  const items = Array.from({ length: 100 }, (_, i) => thread(`t${i}`, { updatedAt: iso(now - i * 1000) }));
  const columns = buildColumns(items, emptyFilters(), now, new Set(['backlog', 'doing', 'waiting', 'review', 'done']));
  const picked = pickCards(columns);
  assert.equal(picked.cards.length, SUMMARY_LIMIT);
  assert.equal(picked.omitted.review, 20);
  assert.equal(picked.cards[0].thread.id, 't0', 'latest first');
  assert.match(buildSummary(columns, { ...ctx, locale: 'zh' }), /另有 20 个线程没有列出（待验收 20 个）/);
});

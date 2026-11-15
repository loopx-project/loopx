import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ProactiveRun, Todo } from '../src/api.js';
import { addRepo, askLine, askingPins, diffTotals, fileName, isDecision, matterRuns, needTarget, settingsPayload, proactiveCount, proactiveMatters, removeRepo, replaceRun, runControls, startConflict } from '../src/proactive.js';

let n = 0;
function run(over: Partial<ProactiveRun> = {}): ProactiveRun {
  n += 1;
  return { id: `r${n}`, matterId: 'm1', kind: 'sandbox', threadId: `t${n}`, startedAt: new Date(2026, 10, 1, 10, n).toISOString(), status: 'ready', ...over };
}
const todo = (id: string, count?: number) => ({ id, text: id, proactiveRunCount: count }) as Todo;

test('repositories are added once and removed by path', () => {
  const settings = { mode: 'auto' as const, enabled: true, repos: ['/a'] };
  assert.deepEqual(addRepo(settings, '/b').repos, ['/a', '/b']);
  assert.equal(addRepo(settings, '/a'), settings);
  assert.deepEqual(removeRepo(settings, '/a').repos, []);
});

test('a run offers what can still be decided, as its status moves', () => {
  assert.deepEqual(runControls(run({ status: 'running' })), []);
  assert.deepEqual(runControls(run({ status: 'ready' })), ['diff', 'merge', 'discard']);
  assert.deepEqual(runControls(run({ status: 'merged' })), ['revert']);
  assert.deepEqual(runControls(run({ status: 'reverted' })), []);
  assert.deepEqual(runControls(run({ status: 'discarded' })), []);
  assert.deepEqual(runControls(run({ status: 'failed', branch: 'calli/proactive/m1' })), ['discard']);
  assert.deepEqual(runControls(run({ status: 'failed' })), [], 'failed before a sandbox existed: nothing to clear');
  assert.deepEqual(runControls(run({ kind: 'prepare', status: 'ready' })), [], 'materials ask for no decision');
});

test('an action replaces only its own run', () => {
  const [a, b] = [run(), run()];
  const next = replaceRun([a, b], { ...a, status: 'merged' });
  assert.equal(next[0].status, 'merged');
  assert.equal(next[1], b);
});

test('a dispatch proposal offers to begin or pass, and only while it asks', () => {
  const asking = run({ kind: 'dispatch', status: 'asking', threadId: '', agent: 'codex', cwd: '~/code/app', task: '补测试' });
  assert.deepEqual(runControls(asking), ['start', 'dismiss']);
  assert.deepEqual(runControls(run({ kind: 'dispatch', status: 'answered', threadId: 'w1' })), [], 'once begun there is nothing left to decide');
  assert.deepEqual(runControls(run({ kind: 'dispatch', status: 'discarded' })), []);
});

test('a refused start jumps only to a thread that truly began', () => {
  assert.deepEqual(startConflict({ run: { kind: 'dispatch', status: 'answered', threadId: 'w9' } }), { threadId: 'w9' });
  assert.equal(startConflict({ error: '派活正在开始', run: { kind: 'dispatch', status: 'asking', threadId: '' } }), null, 'message text alone never implies a concurrent start');
  assert.equal(startConflict({ starting: true, run: { kind: 'dispatch', status: 'asking', threadId: '' } }), 'starting', 'the explicit flag wins over the message text');
  assert.equal(startConflict({ error: 'agent unavailable', run: { kind: 'dispatch', status: 'asking', threadId: '' } }), null, 'an ordinary validation failure stays visible and retryable');
  assert.equal(startConflict({ run: { kind: 'dispatch', status: 'discarded', threadId: '' } }), null);
  assert.equal(startConflict({ run: { kind: 'advance', status: 'answered', threadId: 'w9' } }), null, 'not a dispatch: no jump to read into');
  assert.equal(startConflict({}), null);
  assert.equal(startConflict({ run: 'gone' }), null);
});

test('the panel pins what asks the person, newest first, and nothing else', () => {
  const older = run({ kind: 'dispatch', status: 'asking', startedAt: new Date(2026, 10, 1, 9).toISOString() });
  const newer = run({ kind: 'advance', status: 'asking', startedAt: new Date(2026, 10, 1, 11).toISOString() });
  const quiet = run({ status: 'waiting' });
  assert.deepEqual(askingPins([quiet, older, newer]).map(item => item.id), [newer.id, older.id]);
  assert.deepEqual(askingPins([]), []);
  assert.deepEqual(askingPins(null), []);
});

test('a spread shows open runs and the latest of each kind, not old attempts or quiet no-ops', () => {
  const oldDiscard = run({ status: 'discarded' });
  const oldMerged = run({ status: 'merged' });
  const failed = run({ status: 'failed' });
  const quiet = run({ kind: 'prepare', status: 'discarded' });
  const other = run({ matterId: 'm2' });
  const shown = matterRuns([oldDiscard, oldMerged, failed, quiet, other], 'm1').map(item => item.id);
  assert.deepEqual(shown, [failed.id, oldMerged.id]);
  const material = run({ kind: 'prepare', status: 'ready' });
  assert.ok(matterRuns([quiet, material], 'm1').some(item => item.id === material.id));
});

test('the filter lists each matter once, by the run still open to a decision', () => {
  const merged = run({ matterId: 'a', status: 'merged' });
  const laterFailure = run({ matterId: 'a', status: 'failed' });
  const gone = run({ matterId: 'gone', status: 'reverted' });
  const matters = proactiveMatters([merged, laterFailure, gone], [todo('a')]);
  assert.deepEqual(matters.map(item => item.matterId).sort(), ['a', 'gone']);
  assert.equal(matters.find(item => item.matterId === 'a')!.run.id, merged.id, 'revert stays in reach');
  assert.equal(matters.find(item => item.matterId === 'gone')!.todo, null);
});

test('sessions that belong to no matter are listed one row each, by session', () => {
  const first = run({ kind: 'advance', matterId: '', threadId: 't1', status: 'asking', reason: 'user' });
  const second = run({ kind: 'advance', matterId: '', threadId: 't2', status: 'asking', reason: 'user' });
  const rows = proactiveMatters([first, second], []);
  assert.deepEqual(rows.map(row => row.matterId).sort(), ['thread:t1', 'thread:t2']);
  assert.ok(rows.every(row => row.todo === null));
});

test('the filter count comes from the plans, without reading the runs', () => {
  assert.equal(proactiveCount([todo('a', 2), todo('b', 0), todo('c')]), 1);
});

test('file names and diff totals', () => {
  assert.equal(fileName('matters/m1/artifacts/outline.md'), 'outline.md');
  assert.deepEqual(diffTotals([{ path: 'a', additions: 2, deletions: 1 }, { path: 'b', additions: 3, deletions: 0 }]), { additions: 5, deletions: 1 });
  assert.deepEqual(diffTotals([]), { additions: 0, deletions: 0 });
});

test('an observation can be approved once, and approval leaves only the record', () => {
  assert.deepEqual(runControls(run({ status: 'observed' })), ['approve']);
  assert.deepEqual(runControls(run({ kind: 'prepare', status: 'observed' })), ['approve']);
  assert.deepEqual(runControls(run({ status: 'approved' })), []);
  const observed = run({ status: 'observed' });
  const [next] = replaceRun([observed], { ...observed, status: 'approved' });
  assert.equal(next.status, 'approved');
  assert.deepEqual(matterRuns([next], 'm1').map(item => item.status), ['approved'], 'the card stays');
});

test('observations sit with the materials; only live sandbox work asks for a decision first', () => {
  assert.equal(isDecision(run({ status: 'ready' })), true);
  assert.equal(isDecision(run({ status: 'observed' })), false);
  assert.equal(isDecision(run({ status: 'approved' })), false);
  assert.equal(isDecision(run({ kind: 'prepare', status: 'ready' })), false);
});

test('discarded work leaves no card and no filter row', () => {
  const discarded = run({ status: 'discarded', discardReason: '测试未通过' });
  assert.deepEqual(matterRuns([discarded], 'm1'), []);
  assert.deepEqual(proactiveMatters([discarded], [todo('m1')]), []);
});

test('saving sends the mode and repositories, never the read-only enabled', () => {
  assert.deepEqual(settingsPayload({ mode: 'observe', repos: ['/a'], enabled: true }), { mode: 'observe', repos: ['/a'] });
});

test('advance cards offer only the actions legal for their current state', () => {
  assert.deepEqual(runControls(run({ kind: 'advance', status: 'asking', reason: 'user' })), ['answer']);
  assert.deepEqual(runControls(run({ kind: 'advance', status: 'asking', reason: 'transient' })), ['answer']);
  assert.deepEqual(runControls(run({ kind: 'advance', status: 'asking', reason: 'external' })), ['answer']);
  assert.deepEqual(runControls(run({ kind: 'advance', status: 'asking', reason: 'done' })), ['close-matter', 'dismiss']);
  assert.deepEqual(runControls(run({ kind: 'advance', status: 'waiting' })), ['dismiss']);
  assert.deepEqual(runControls(run({ kind: 'sandbox', needsRepo: true, status: 'observed' })), ['allow-repo', 'dismiss']);
  for (const status of ['answered', 'continued', 'closed', 'discarded'] as const) {
    assert.deepEqual(runControls(run({ kind: 'advance', status })), []);
  }
});

test('open questions, waits and repository permissions survive newer records', () => {
  const question = run({ kind: 'advance', status: 'asking', reason: 'user' });
  const wait = run({ kind: 'advance', status: 'waiting', reason: 'external' });
  const permission = run({ needsRepo: true, status: 'observed' });
  const closed = run({ kind: 'advance', status: 'closed' });
  const approved = run({ status: 'approved' });
  const all = [question, wait, permission, closed, approved];
  assert.deepEqual(new Set(matterRuns(all, 'm1').map(item => item.id)), new Set(all.map(item => item.id)));
  for (const item of [question, wait, permission]) assert.equal(isDecision(item), true);
  assert.equal(isDecision(closed), false);
  const plan = run({ kind: 'prepare', variant: 'plan', status: 'ready' });
  assert.deepEqual(runControls(plan), []);
  assert.equal(isDecision(plan), false);
});

test('a folded ask is known by its first meaningful line', () => {
  assert.equal(askLine(run({ kind: 'advance', status: 'asking', question: { text: '\n  确认要不要替换周报\n细节', options: [] } })), '确认要不要替换周报');
  assert.equal(askLine(run({ kind: 'dispatch', status: 'asking', title: '', task: '修登录\n步骤…' })), '修登录', 'a dispatch without a title falls back to its task');
  assert.equal(askLine(run({ kind: 'advance', status: 'asking', line: '等你决定目录' })), '等你决定目录');
  assert.equal(askLine(run({ kind: 'advance', status: 'asking' })), '');
});

test('a needs-you row leads somewhere real, never a thread id nothing answers', () => {
  const asking = run({ id: 'a1', kind: 'advance', status: 'asking' });
  const answered = run({ id: 'a2', kind: 'advance', status: 'answered', threadId: 't9' });
  const dismissed = run({ id: 'a3', kind: 'dispatch', status: 'discarded', threadId: '' });
  const runs = [asking, answered, dismissed];
  assert.deepEqual(needTarget('t123', runs), { kind: 'thread', id: 't123' }, "a session's own entry opens as itself");
  assert.deepEqual(needTarget('run:a1', runs), { kind: 'ask', run: asking }, 'an ask opens as the tray, where it is answered');
  assert.deepEqual(needTarget('run:a2', runs), { kind: 'thread', id: 't9' }, 'an ask already over opens the session it became');
  assert.deepEqual(needTarget('run:a3', runs), { kind: 'gone' }, 'a dismissed dispatch that began no session opens nothing');
  assert.deepEqual(needTarget('run:nope', runs), { kind: 'gone' }, 'an id no run answers opens nothing');
  assert.deepEqual(needTarget('run:a1', null), { kind: 'gone' }, 'before the runs load, nothing is opened on a guess');
});

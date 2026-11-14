import { test } from 'node:test';
import assert from 'node:assert/strict';
import { channelRows, turnChannel } from '../src/timeline.js';
import {
  buildTimeline,
  stepCommand,
  compactionLabel,
  coordinationNote,
  deliveredPreview,
  clockLabel,
  durationLabel,
  groupByTurn,
  runSummary,
  splitThinking,
  splitUserQuote,
  thinkingPreview,
  stepDurationLabel,
  stepStatusLabel,
  stepTitle,
  toMarkdown,
  turnPreviews,
  workSections,
  withQuote,
  withNotes,
  splitNotes,
  type Step,
  type StoredEvent,
} from '../src/timeline.js';

let seq = 0;
function ev(type: string, data: Record<string, any> = {}, turnId: string | null = 't1', ts = '2026-10-10T00:00:00.000Z'): StoredEvent {
  seq += 1;
  return { threadId: 'th', seq, turnId, type, ts, data };
}

test('compaction is a chronological separator, deduplicated without adding tool steps', () => {
  const events = [ev('turn.started', { text: 'work' }),
    ev('tool.start', { id: 'before', kind: 'read' }), ev('tool.end', { id: 'before', status: 'completed' }),
    ev('context.compaction', { id: 'c1', status: 'in_progress', automatic: true }),
    ev('context.compaction', { id: 'c1', status: 'completed', automatic: true }),
    ev('tool.start', { id: 'after', kind: 'execute' }), ev('tool.end', { id: 'after', status: 'completed' }),
    ev('message.delta', { text: 'answer' }),
    ev('context.compaction', { id: 'c1', status: 'completed' }),
    ev('context.compaction', { id: 'c1', status: 'in_progress' }), ev('turn.done')];
  const work = buildTimeline(events).items.find(i => i.kind === 'work');
  assert.ok(work?.kind === 'work');
  assert.equal(work.steps.length, 2);
  const sections = workSections(work.entries);
  assert.deepEqual(sections.map(s => s.kind), ['run', 'compaction', 'run']);
  const compact = sections[1];
  assert.ok(compact.kind === 'compaction');
  assert.equal(compactionLabel(compact), '上下文已自动压缩');
  assert.ok(buildTimeline(events).items.some(i => i.kind === 'assistant' && i.text === 'answer'));
});

test('compaction distinguishes running, completed, failed, cancelled, skipped and interrupted', () => {
  for (const [status, label] of [['in_progress', '正在压缩上下文…'], ['completed', '上下文已自动压缩'],
    ['failed', '上下文压缩失败'], ['cancelled', '上下文压缩已停止'], ['skipped', '已跳过上下文压缩']]) {
    const work = buildTimeline([ev('turn.started'), ev('context.compaction', { id: 'c', status, automatic: true })]).items.find(i => i.kind === 'work');
    assert.ok(work?.kind === 'work');
    const entry = work.entries[0];
    assert.ok(entry.kind === 'compaction');
    assert.equal(compactionLabel(entry), label);
  }
  for (const end of [ev('turn.done', { stopReason: 'cancelled' }), ev('turn.error', { message: 'crash' }), ev('turn.started', {}, 't2')]) {
    const work = buildTimeline([ev('turn.started'), ev('context.compaction', { id: 'c', status: 'in_progress' }), end]).items.find(i => i.kind === 'work');
    assert.ok(work?.kind === 'work');
    const entry = work.entries[0];
    assert.ok(entry.kind === 'compaction');
    assert.equal(compactionLabel(entry), '上下文压缩未完成');
  }
});

test('terminal-only and repeated compactions survive replay with turn-scoped identities', () => {
  const events = [ev('turn.started'), ev('context.compaction', { id: 'c', status: 'completed' }),
    ev('context.compaction', { id: 'c2', status: 'completed', automatic: true }), ev('turn.done'),
    ev('turn.started', {}, 't2'), ev('context.compaction', { id: 'c', status: 'completed' }, 't2'), ev('turn.done', {}, 't2')];
  const work = buildTimeline(events).items.filter(i => i.kind === 'work');
  assert.deepEqual(work.map(w => w.entries.length), [2, 1]);
  assert.deepEqual(buildTimeline(JSON.parse(JSON.stringify(events))), buildTimeline(events));
  const entry = work[0].entries[0];
  assert.ok(entry.kind === 'compaction');
  assert.equal(compactionLabel(entry), '上下文已压缩');
});

test('collaboration turns expose their source and hide runtime instructions', () => {
  const tl = buildTimeline([ev('turn.started', { text: 'internal instructions', displayText: '来自 审查成员 的结果\n\n状态：本轮已返回，仍需验收\n完成', coordination: { from: 'reviewer' } })]);
  const item = tl.items[0];
  assert.equal(item.kind, 'user');
  if (item.kind !== 'user') return;
  assert.equal(item.text, '完成');
  assert.deepEqual(item.coordination, { from: '审查成员', kind: 'result', failed: false });
});

test('a delivered message is split into who sent it, how it went and its text', () => {
  assert.deepEqual(coordinationNote('来自 pi-daemon 的结果\n\n状态：本轮已返回，仍需验收\n## 结论\n\n没有问题。'), { note: { from: 'pi-daemon', kind: 'result', failed: false }, text: '## 结论\n\n没有问题。' });
  assert.deepEqual(coordinationNote('来自 主智能体 的任务\n\n状态：先别动\n再说'), { note: { from: '主智能体', kind: 'task', failed: false }, text: '状态：先别动\n再说' });
  assert.deepEqual(coordinationNote('来自 w 的结果\n\n状态：失败\nboom').note, { from: 'w', kind: 'result', failed: true });
  assert.deepEqual(coordinationNote('来自 w 的结果\n\n无法启动任务：x'), { note: { from: 'w', kind: 'result', failed: false }, text: '无法启动任务：x' });
  assert.deepEqual(coordinationNote('something else'), { note: { from: '', kind: 'result', failed: false }, text: 'something else' });
  assert.equal(deliveredPreview('## 结论\n\n- **没有**问题\n| a | b |\n|---|---|\n1. `x` 要改'), '结论 没有问题 x 要改');
});

test('user text and streamed assistant text are grouped per turn', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'hi' }),
    ev('message.delta', { text: 'Hel' }),
    ev('message.delta', { text: 'lo' }),
    ev('turn.done', { stopReason: 'end_turn' }),
  ]);
  assert.equal(tl.running, false);
  assert.deepEqual(
    tl.items.map((i) => i.kind),
    ['user', 'assistant', 'actions'],
  );
  const a = tl.items[1];
  assert.equal(a.kind === 'assistant' && a.text, 'Hello');
  assert.equal(a.kind === 'assistant' && a.done, true);
});

test('tool steps are grouped in a work item and text after tools starts a new assistant item', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'do it' }),
    ev('reasoning.delta', { text: 'thinking' }),
    ev('tool.start', { id: 'a', title: 'ls', kind: 'execute', status: 'in_progress', input: { command: 'ls' } }),
    ev('tool.end', { id: 'a', status: 'completed', output: 'a.txt' }),
    ev('message.delta', { text: 'done' }),
    ev('turn.done', {}),
  ]);
  assert.deepEqual(tl.items.map((i) => i.kind), ['user', 'work', 'assistant', 'actions']);
  const work = tl.items[1];
  assert.equal(work.kind, 'work');
  if (work.kind !== 'work') return;
  assert.deepEqual(work.entries.map((e) => e.kind), ['thought', 'step'], 'what the turn did, in the order it happened');
  assert.equal(work.entries[0].kind === 'thought' && work.entries[0].thought.text, 'thinking');
  assert.equal(work.steps.length, 1);
  assert.equal(work.steps[0].status, 'completed');
  assert.equal(work.steps[0].output, 'a.txt');
  assert.equal(work.done, true);
});

test('tool.update changes status without creating a new step', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'x' }),
    ev('tool.start', { id: 'b', title: 'read', kind: 'read' }),
    ev('tool.update', { id: 'b', status: 'in_progress', output: 'partial' }),
  ]);
  const work = tl.items[1];
  assert.equal(work.kind === 'work' && work.steps.length, 1);
  assert.equal(work.kind === 'work' && work.steps[0].output, 'partial');
  assert.equal(tl.running, true);
});

test('a command that surfaced as "Terminal" takes its name and input from the update that names it', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'x' }),
    ev('tool.start', { id: 's', title: 'Terminal', kind: 'execute', input: {} }),
    ev('tool.update', { id: 's', title: 'npm test', kind: 'execute', input: { command: 'npm test', description: 'Run tests' } }),
    ev('tool.end', { id: 's', status: 'completed', output: '2 passing' }),
  ]);
  const work = tl.items[1];
  const step = work.kind === 'work' ? work.steps[0] : undefined;
  assert.equal(step && stepTitle(step), '运行了 npm test');
  assert.equal(step && stepCommand(step), 'npm test');
  assert.equal(step?.output, '2 passing');
  assert.equal(stepCommand({ kind: 'read', input: { command: 'x' } }), null, 'only a command run has a command line');
  assert.equal(stepCommand({ kind: 'execute', input: {} }), null);
});

test('approval is shown and its decision is attached when resolved', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'x' }),
    ev('approval.request', { approvalId: 'ap1', title: 'Edit file', options: [{ optionId: 'allow', name: 'Allow', kind: 'allow_once' }] }),
    ev('approval.resolved', { approvalId: 'ap1', decision: { optionId: 'allow' } }),
  ]);
  const item = tl.items[1];
  assert.equal(item.kind, 'approval');
  assert.deepEqual(item.kind === 'approval' && item.decision, { optionId: 'allow' });
});

test('turn.error adds an error item and clears running', () => {
  seq = 0;
  const tl = buildTimeline([ev('turn.started', { text: 'x' }), ev('turn.error', { message: 'boom' })]);
  assert.equal(tl.running, false);
  assert.equal(tl.items.at(-1)?.kind, 'error');
});

test('lastSeq tracks the highest seq seen so the stream can resume', () => {
  seq = 0;
  const tl = buildTimeline([ev('turn.started', { text: 'x' }), ev('message.delta', { text: 'y' })]);
  assert.equal(tl.lastSeq, 2);
  assert.equal(buildTimeline([]).lastSeq, 0);
});

test('durationLabel formats seconds, minutes and hours, carrying rounded seconds across boundaries', () => {
  assert.equal(durationLabel('2026-10-10T00:00:00Z', '2026-10-10T00:00:25Z'), '25 秒');
  assert.equal(durationLabel('2026-10-10T00:00:00Z', '2026-10-10T00:05:25Z'), '5 分 25 秒');
  assert.equal(durationLabel('2026-10-10T00:00:00Z', '2026-10-10T00:59:59Z'), '59 分 59 秒');
  assert.equal(durationLabel('2026-10-10T00:00:00Z', '2026-10-10T00:59:59.600Z'), '1 小时 0 分 0 秒');
  assert.equal(durationLabel('2026-10-10T00:00:00Z', '2026-10-10T01:05:30Z'), '1 小时 5 分 30 秒');
  assert.equal(durationLabel('2026-10-10T23:00:00Z', '2026-10-12T00:02:03Z'), '25 小时 2 分 3 秒');
  assert.equal(durationLabel('invalid', '2026-10-10T01:00:00Z'), '');
  assert.equal(durationLabel('2026-10-10T00:00:00Z', null), '');
});

const diff = (path: string, oldText: string | null, newText: string) => ({ type: 'diff', path, oldText, newText });

test('a finished answer gets an action row carrying its text; a running or failed turn does not', () => {
  seq = 0;
  const done = buildTimeline([ev('turn.started', { text: 'q' }), ev('message.delta', { text: 'first' }), ev('tool.start', { id: 't' }), ev('message.delta', { text: 'final answer' }), ev('turn.done', {})]);
  const actions = done.items.at(-1);
  assert.equal(actions?.kind === 'actions' && actions.text, 'final answer', 'copies the last message, not the first');

  seq = 0;
  const live = buildTimeline([ev('turn.started', { text: 'q' }), ev('message.delta', { text: 'typing' })]);
  assert.equal(live.items.some((i) => i.kind === 'actions'), false);

  seq = 0;
  const failed = buildTimeline([ev('turn.started', { text: 'q' }), ev('message.delta', { text: 'partial' }), ev('turn.error', { message: 'boom' })]);
  assert.equal(failed.items.some((i) => i.kind === 'actions'), false);
});

test('the action row carries when the turn ended, unless the hub only copied the turn in', () => {
  seq = 0;
  const seen = buildTimeline([ev('turn.started', { text: 'q' }), ev('message.delta', { text: 'a' }), ev('turn.done', {}, 't1', '2026-10-10T05:11:00.000Z')]);
  const row = seen.items.at(-1);
  assert.equal(row?.kind === 'actions' && row.at, '2026-10-10T05:11:00.000Z');

  // An imported turn and one synced from the terminal were written down later than they happened.
  for (const flag of ['imported', 'fromTerminal']) {
    seq = 0;
    const copied = buildTimeline([ev('turn.started', { text: 'q', [flag]: true }), ev('message.delta', { text: 'a' }), ev('turn.done', { [flag]: true })]);
    const last = copied.items.at(-1);
    assert.equal(last?.kind, 'actions');
    assert.equal(last?.kind === 'actions' && 'at' in last, false, `${flag}: no time to show`);
  }
});

test('clockLabel says only as much of the date as is needed', () => {
  const now = new Date(2026, 9, 11, 9, 30);
  assert.equal(clockLabel(new Date(2026, 9, 11, 8, 5).toISOString(), now), '08:05');
  assert.equal(clockLabel(new Date(2026, 9, 10, 13, 11).toISOString(), now), '10月10日 13:11');
  assert.equal(clockLabel(new Date(2025, 11, 31, 23, 59).toISOString(), now), '2025年12月31日 23:59');
  assert.equal(clockLabel('not a date', now), '');
});

test('user images are kept on the user item', () => {
  seq = 0;
  const tl = buildTimeline([ev('turn.started', { text: 'look', images: [{ id: 'a1', mimeType: 'image/png', size: 10 }] })]);
  const user = tl.items[0];
  assert.deepEqual(user.kind === 'user' && user.images.map((i) => i.id), ['a1']);
  seq = 0;
  const plain = buildTimeline([ev('turn.started', { text: 'hi' })]).items[0];
  assert.deepEqual(plain.kind === 'user' && plain.images, []);
});

test('completed edits are summed per file into one changes item at the end of the turn', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'edit' }),
    ev('tool.start', { id: 'a', title: 'Edit', kind: 'edit', status: 'pending' }),
    ev('tool.update', { id: 'a', content: [diff('/w/src/app.ts', 'a\nb', 'a\nB\nc')] }),
    ev('tool.end', { id: 'a', status: 'completed', output: 'ok' }),
    ev('tool.start', { id: 'b', title: 'Write', kind: 'edit', content: [diff('/w/new.txt', null, 'x\ny\nz')] }),
    ev('tool.end', { id: 'b', status: 'completed' }),
    ev('tool.start', { id: 'c', title: 'Edit', kind: 'edit', content: [diff('/w/src/app.ts', 'q', 'r')] }),
    ev('tool.end', { id: 'c', status: 'completed' }),
    ev('message.delta', { text: 'done' }),
    ev('turn.done', {}),
  ]);
  assert.deepEqual(tl.items.map((i) => i.kind), ['user', 'work', 'assistant', 'changes', 'actions']);
  const changes = tl.items[3];
  assert.equal(changes.kind, 'changes');
  if (changes.kind !== 'changes') return;
  assert.deepEqual(changes.files.map((f) => [f.path, f.added, f.removed, f.diffs.length]), [
    ['/w/src/app.ts', 3, 2, 2],
    ['/w/new.txt', 3, 0, 1],
  ]);
  assert.deepEqual([changes.added, changes.removed], [6, 2]);
  // The step keeps its text output, and the diff is not duplicated into it.
  const work = tl.items[1];
  assert.equal(work.kind === 'work' && work.steps[0].output, 'ok');
  assert.equal(work.kind === 'work' && work.steps[0].diffs.length, 1);
});

test('edits that failed, were refused, or are still pending are not counted as changes', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'edit' }),
    ev('tool.start', { id: 'a', kind: 'edit', content: [diff('/w/a.ts', 'x', 'y')] }),
    ev('tool.end', { id: 'a', status: 'failed' }),
    ev('tool.start', { id: 'b', kind: 'edit', status: 'pending', content: [diff('/w/b.ts', 'x', 'y')] }),
  ]);
  assert.equal(tl.items.some((i) => i.kind === 'changes'), false);
});

test('completed edits are not summed into a changes item until the turn has finished', () => {
  seq = 0;
  const events = [
    ev('turn.started', { text: 'edit then keep going' }),
    ev('tool.start', { id: 'a', kind: 'edit', content: [diff('/w/a.ts', 'x', 'y')] }),
    ev('tool.end', { id: 'a', status: 'completed' }),
    ev('message.delta', { text: 'on the way' }),
    ev('tool.start', { id: 'b', title: 'Bash', kind: 'other' }),
  ];
  // Still running: the edit is done, but the turn has not ended — no summary card, no actions row.
  const running = buildTimeline(events);
  assert.equal(running.running, true);
  assert.deepEqual(running.items.map((i) => i.kind), ['user', 'work']);
  // The same turn, ended: the card and the answer row appear.
  const done = buildTimeline([...events, ev('turn.done', {})]);
  assert.deepEqual(done.items.map((i) => i.kind), ['user', 'work', 'assistant', 'changes', 'actions']);
  const changes = done.items[3];
  assert.equal(changes.kind === 'changes' && changes.files.length, 1);
});

test('each turn gets its own changes item, placed before the next user message', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'one' }, 't1'),
    ev('tool.start', { id: 'a', kind: 'edit', content: [diff('/w/a.ts', null, 'x')] }, 't1'),
    ev('tool.end', { id: 'a', status: 'completed' }, 't1'),
    ev('turn.done', {}, 't1'),
    ev('turn.started', { text: 'two' }, 't2'),
    ev('message.delta', { text: 'nothing to change' }, 't2'),
    ev('turn.done', {}, 't2'),
  ]);
  assert.deepEqual(tl.items.map((i) => i.kind), ['user', 'work', 'changes', 'user', 'assistant', 'actions']);
});

test('an approval shows the diff or the command it is asking about, and marks the timeline as waiting', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'x' }),
    ev('approval.request', { approvalId: 'w', title: 'Write /w/a.txt', options: [], toolCall: { kind: 'edit', content: [diff('/w/a.txt', null, 'alpha\nbeta')] } }),
    ev('approval.request', { approvalId: 'c', title: 'Run', options: [], toolCall: { kind: 'execute', rawInput: { command: 'npm run build' } } }),
  ]);
  const [, write, run] = tl.items;
  assert.equal(write.kind === 'approval' && write.diffs[0].added, 2);
  assert.equal(write.kind === 'approval' && write.command, null);
  assert.equal(run.kind === 'approval' && run.command, 'npm run build');
  assert.equal(tl.awaitingApproval, true);

  seq = 0;
  const answered = buildTimeline([
    ev('turn.started', { text: 'x' }),
    ev('approval.request', { approvalId: 'w', title: 't', options: [], toolCall: {} }),
    ev('approval.resolved', { approvalId: 'w', decision: { optionId: 'allow' } }),
  ]);
  assert.equal(answered.awaitingApproval, false);
});

test('toMarkdown writes the conversation with file changes and without tool noise', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'fix it', images: [{ id: 'a', mimeType: 'image/png' }] }),
    ev('reasoning.delta', { text: 'thinking hard' }),
    ev('tool.start', { id: 'a', kind: 'edit', content: [diff('src/a.ts', 'x', 'y')] }),
    ev('tool.end', { id: 'a', status: 'completed', output: 'tool noise' }),
    ev('message.delta', { text: 'Fixed.' }),
    ev('turn.done', {}),
  ]);
  const md = toMarkdown('My thread', tl.items);
  assert.ok(md.startsWith('# My thread\n\n## 你\n\nfix it\n\n（附 1 张图片）\n\nFixed.'), md);
  assert.ok(md.includes('**已更改 1 个文件** (+1 −1)\n- `src/a.ts` +1 −1'));
  assert.equal(md.includes('thinking hard') || md.includes('tool noise'), false);
});

test('turns imported from history show their steps without a duration; new turns are timed', () => {
  seq = 0;
  const { items, running } = buildTimeline([
    ev('thread.created', { imported: true }, null),
    ev('turn.started', { text: 'earlier question', imported: true }, 'old'),
    ev('tool.start', { id: 't1', title: 'ls', kind: 'execute', status: 'in_progress' }, 'old'),
    ev('tool.end', { id: 't1', status: 'completed', output: 'a.txt' }, 'old'),
    ev('message.delta', { text: 'earlier answer' }, 'old'),
    ev('turn.done', { stopReason: 'end_turn', imported: true }, 'old'),
    ev('turn.started', { text: 'next' }, 'new'),
    ev('reasoning.delta', { text: 'thinking' }, 'new'),
    ev('turn.done', { stopReason: 'end_turn' }, 'new'),
  ]);
  assert.equal(running, false);
  const work = items.flatMap((it) => (it.kind === 'work' ? [[it.turnId, it.timed, it.done]] : []));
  assert.deepEqual(work, [['old', false, true], ['new', true, true]]);
  // Imported answers get the same action row as any other finished answer.
  assert.ok(items.some((it) => it.kind === 'actions' && it.turnId === 'old' && it.text === 'earlier answer'));
});

test('a turn refused for lack of permission explains the way out; other errors are shown as reported', () => {
  seq = 0;
  const { items } = buildTimeline([
    ev('turn.started', { text: 'run it' }, 'a'),
    ev('turn.error', { message: 'Antigravity refused RunCommand: …', code: 'permission_refused', refused: ['RunCommand', 'WriteFile'] }, 'a'),
    ev('turn.started', { text: 'again' }, 'b'),
    ev('turn.error', { message: 'quota exceeded' }, 'b'),
  ]);
  const errors = items.flatMap((it) => (it.kind === 'error' ? [it.message] : []));
  assert.match(errors[0], /RunCommand、WriteFile/);
  assert.match(errors[0], /完全访问/);
  assert.equal(errors[1], 'quota exceeded');
});

import { imagePathsIn, usageSummary } from '../src/timeline.js';

test('a plan is one item per turn, updated in place, and does not open a work group', () => {
  seq = 0;
  const entries = (statuses: string[]) => statuses.map((status, i) => ({ content: `step ${i + 1}`, status, priority: 'medium' }));
  const { items } = buildTimeline([
    ev('turn.started', { text: 'do it' }, 'a'),
    ev('plan.updated', { entries: entries(['in_progress', 'pending']) }, 'a'),
    ev('tool.start', { id: 't', title: 'ls', kind: 'execute' }, 'a'),
    ev('plan.updated', { entries: entries(['completed', 'in_progress']) }, 'a'),
    ev('turn.done', {}, 'a'),
    ev('turn.started', { text: 'again' }, 'b'),
    ev('plan.updated', { entries: [] }, 'b'),
    ev('plan.updated', { entries: entries(['pending']) }, 'b'),
  ]);
  assert.deepEqual(items.map((i) => i.kind), ['user', 'plan', 'work', 'user', 'plan']);
  const [first, second] = items.filter((i) => i.kind === 'plan') as Array<Extract<(typeof items)[number], { kind: 'plan' }>>;
  assert.deepEqual(first.entries.map((e) => e.status), ['completed', 'in_progress'], 'the later update replaced the earlier one');
  assert.equal(first.done, true);
  assert.deepEqual([second.turnId, second.entries.length, second.done], ['b', 1, false], 'an empty plan does not make a card');
});

test('usage is the latest the agent reported; the summary rounds it and flags a nearly full context', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'hi' }),
    ev('usage.updated', { used: 1000, size: 200000 }),
    ev('usage.updated', { used: 52000, size: 200000, cost: { amount: 0.4231, currency: 'USD' } }),
    ev('usage.updated', { used: 5, size: 0 }),
    ev('turn.done', {}),
  ]);
  assert.deepEqual(tl.usage, { used: 52000, size: 200000, cost: { amount: 0.4231, currency: 'USD' } });
  assert.deepEqual(usageSummary(tl.usage!), { percent: 26, high: false, detail: '上下文已用 52k / 200k · $0.42' });
  assert.deepEqual(usageSummary({ used: 900_000, size: 1_000_000 }), { percent: 90, high: true, detail: '上下文已用 900k / 1.0M' });
  assert.equal(usageSummary({ used: 3_000_000, size: 1_000_000 }).percent, 100);
  assert.equal(buildTimeline([]).usage, null);
});

test('unknown context clears the previous reading and a later measurement restores it', () => {
  const events = [
    ev('usage.updated', { used: 183510, size: 258400 }),
    ev('usage.updated', { used: null, size: 258400 }),
  ];
  assert.equal(buildTimeline(events).usage, null);
  events.push(ev('usage.updated', { used: 12000, size: 258400 }));
  assert.deepEqual(buildTimeline(events).usage, { used: 12000, size: 258400 });
  for (const used of [-1, NaN, Infinity]) events.push(ev('usage.updated', { used, size: 258400 }));
  assert.deepEqual(buildTimeline(events).usage, { used: 12000, size: 258400 });
});

test('imagePathsIn finds local image files in an answer and leaves web addresses alone', () => {
  assert.deepEqual(imagePathsIn('Saved to /work/app/out/chart.png and also `docs/logo.webp`.'), ['/work/app/out/chart.png', 'docs/logo.webp']);
  assert.deepEqual(imagePathsIn('![the chart](out/chart.png "Chart")\n\n![remote](https://example.com/a.png)'), ['out/chart.png']);
  assert.deepEqual(imagePathsIn('图片在：/work/图表/结果.jpg。另一张是 ./a b.png'), ['/work/图表/结果.jpg', 'b.png']);
  assert.deepEqual(imagePathsIn('see file:///work/app/shot.jpeg\nor "~/x/y.gif"'), ['/work/app/shot.jpeg', '~/x/y.gif']);
  assert.deepEqual(imagePathsIn('saved to:\n👉 [dino.svg](file:///work/app/dino.svg)\n\nand [二](file:///work/app/a%20b/%E5%9B%BE.svg)'), ['/work/app/dino.svg', '/work/app/a b/图.svg'], 'a linked SVG, with the address decoded');
  assert.deepEqual(imagePathsIn('https://example.com/pic.png and http://x.test/a/b.jpg'), [], 'part of a URL is not a path');
  assert.deepEqual(imagePathsIn('edit main.ts and style.css, then run npm test'), []);
  assert.deepEqual(imagePathsIn('a.png a.png A.PNG'), ['a.png', 'A.PNG'], 'each file once');
  assert.equal(imagePathsIn(Array.from({ length: 20 }, (_, i) => `shot-${i}.png`).join(' ')).length, 6, 'no more than six');
  assert.deepEqual(imagePathsIn(''), []);
  assert.deepEqual(imagePathsIn('[截图](</tmp/report/截图 (1).png>)'), ['/tmp/report/截图 (1).png']);
  assert.deepEqual(imagePathsIn('[截图](/tmp/report/a%20b.png) [missing.png](https://example.com/a.png)'), ['/tmp/report/a b.png']);
});

test('image paths deduplicate workspace spellings without hiding another directory’s image', () => {
  const text = '![fox](/linked/project/fox.jpeg)\nSaved `fox.jpeg`, `./fox.jpeg`, /real/project/fox.jpeg and /other/fox.jpeg';
  assert.deepEqual(imagePathsIn(text, ['/linked/project', '/real/project']), ['/linked/project/fox.jpeg', '/other/fox.jpeg']);
  assert.deepEqual(imagePathsIn('a.png A.PNG'), ['a.png', 'A.PNG'], 'case still identifies different files');
});

test('fromTerminal marks a turn that arrived through sync, not from this page', () => {
  seq = 0;
  const tl = buildTimeline([ev('turn.started', { text: 'typed in a terminal', fromTerminal: true }), ev('turn.done', {})]);
  const user = tl.items[0];
  assert.equal(user.kind === 'user' && user.fromTerminal, true);
});

test('thinking timing is tracked for the "思考 · X 秒" label', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'x' }),
    ev('reasoning.delta', { text: 'hmm' }, 't1', '2026-10-10T00:00:01.000Z'),
    ev('reasoning.delta', { text: ' more' }, 't1', '2026-10-10T00:00:04.000Z'),
    ev('turn.done', {}),
  ]);
  const work = tl.items[1];
  if (work.kind !== 'work') return assert.fail('expected work');
  const [entry] = work.entries;
  if (entry.kind !== 'thought') return assert.fail('expected a thought');
  assert.equal(entry.thought.text, 'hmm more');
  assert.equal(durationLabel(entry.thought.startedAt, entry.thought.endedAt), '3 秒');
});

test('steps carry their own timing for the duration label', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'x' }),
    ev('tool.start', { id: 'a', title: 'npm test', kind: 'execute' }, 't1', '2026-10-10T00:00:00.000Z'),
    ev('tool.end', { id: 'a', status: 'completed' }, 't1', '2026-10-10T00:00:02.500Z'),
  ]);
  const step = tl.items[1].kind === 'work' ? tl.items[1].steps[0] : null;
  assert.ok(step);
  assert.equal(step!.startedAt, '2026-10-10T00:00:00.000Z');
  assert.equal(step!.endedAt, '2026-10-10T00:00:02.500Z');
});

test('splitThinking keeps real paragraphs and unjams sentences pasted together', () => {
  const parts = splitThinking(
    'Let me look at the codebase.Let me check the tests next.\n\nNow a new paragraph.\n\nDone.',
  );
  assert.deepEqual(parts, [
    'Let me look at the codebase.',
    'Let me check the tests next.',
    'Now a new paragraph.',
    'Done.',
  ]);
});

test('splitThinking leaves common abbreviations alone', () => {
  assert.deepEqual(splitThinking('Use e.g. npm here. Then run it.'), ['Use e.g. npm here. Then run it.']);
});

test('splitThinking splits CJK sentences without spaces', () => {
  assert.deepEqual(splitThinking('先看目录。再看代码。'), ['先看目录。', '再看代码。']);
});

test('stepTitle puts a verb first and stepStatusLabel shows the state', () => {
  const base = { id: 'a', title: 'npm test', diffs: [] };
  assert.equal(stepTitle({ ...base, kind: 'execute', status: 'completed', startedAt: '2026-10-10T00:00:00.000Z', endedAt: '2026-10-10T00:00:00.900Z' }), '运行了 npm test');
  assert.equal(stepStatusLabel({ ...base, kind: 'execute', status: 'completed', startedAt: '2026-10-10T00:00:00.000Z', endedAt: '2026-10-10T00:00:00.900Z' }), '0.9 秒');
  assert.equal(stepStatusLabel({ ...base, kind: 'read', status: 'in_progress' }), '运行中');
  assert.equal(stepStatusLabel({ ...base, kind: 'other', status: 'failed' }), '已结束');
  assert.equal(stepStatusLabel({ ...base, kind: 'execute', status: 'failed', startedAt: '2026-10-10T00:00:00.000Z', endedAt: '2026-10-10T00:00:00.900Z' }), '0.9 秒');
  assert.equal(stepTitle({ ...base, kind: 'other', status: 'completed' }), 'npm test');
  // A command is named by what it runs; the directory it steps into first says nothing in one line.
  assert.equal(stepTitle({ ...base, kind: 'execute', status: 'completed', title: 'cd /srv/app && git status --short' }), '运行了 git status --short');
  assert.equal(stepTitle({ ...base, kind: 'execute', status: 'completed', title: 'cd "/srv/my app"; ls' }), '运行了 ls');
  assert.equal(stepTitle({ ...base, kind: 'execute', status: 'completed', title: 'cd /srv/app' }), '运行了 cd /srv/app');
});

test('stepDurationLabel keeps one decimal for short calls', () => {
  assert.equal(stepDurationLabel('2026-10-10T00:00:00.000Z', '2026-10-10T00:00:00.400Z'), '0.4 秒');
  assert.equal(stepDurationLabel('2026-10-10T00:00:00.000Z', '2026-10-10T00:00:12.000Z'), '12 秒');
  assert.equal(stepDurationLabel('2026-10-10T00:00:00.000Z', '2026-10-10T00:01:15.000Z'), '1 分 15 秒');
  assert.equal(stepDurationLabel('2026-10-10T00:00:00.000Z', '2026-10-10T00:00:59.600Z'), '1 分 0 秒');
  assert.equal(stepDurationLabel('2026-10-10T00:00:00.000Z', '2026-10-10T00:59:59.600Z'), '1 小时 0 分 0 秒');
  assert.equal(stepDurationLabel('2026-10-10T00:00:00.000Z', '2026-10-10T02:05:30.000Z'), '2 小时 5 分 30 秒');
  assert.equal(stepDurationLabel(undefined, '2026-10-10T00:00:00.000Z'), '');
});

test('groupByTurn splits at user items and keeps strays in their own group', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'one' }, 't1'),
    ev('message.delta', { text: 'a' }, 't1'),
    ev('turn.done', {}, 't1'),
    ev('turn.started', { text: 'two' }, 't2'),
    ev('message.delta', { text: 'b' }, 't2'),
  ]);
  const groups = groupByTurn(tl.items);
  assert.deepEqual(
    groups.map((g) => [g.turnId, g.items.map((i) => i.kind)]),
    [
      ['t1', ['user', 'assistant', 'actions']],
      ['t2', ['user', 'assistant']],
    ],
  );
});

test('turnPreviews pairs each turn with the start of its answer', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'what is pi?' }, 't1'),
    ev('message.delta', { text: 'an agent CLI' }, 't1'),
    ev('turn.done', {}, 't1'),
    ev('turn.started', { text: 'and the web?' }, 't2'),
    ev('message.delta', { text: 'a browser UI' }, 't2'),
  ]);
  const previews = turnPreviews(tl.items);
  assert.equal(previews.get('t1')?.question, 'what is pi?');
  assert.equal(previews.get('t1')?.answer, 'an agent CLI');
  assert.equal(previews.get('t2')?.answer, 'a browser UI');
});

test('a turn is one work block in the order things happened, and the answer is what follows it', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'q' }, 't1', '2026-10-10T00:00:00Z'),
    ev('reasoning.delta', { text: 'plan' }, 't1', '2026-10-10T00:00:01Z'),
    ev('message.delta', { text: '先看文件，' }, 't1', '2026-10-10T00:00:02Z'),
    ev('tool.start', { id: 'a', title: 'ls', kind: 'execute', status: 'in_progress' }, 't1', '2026-10-10T00:00:03Z'),
    ev('tool.end', { id: 'a', status: 'completed' }, 't1', '2026-10-10T00:00:04Z'),
    ev('reasoning.delta', { text: 'next' }, 't1', '2026-10-10T00:00:05Z'),
    ev('message.delta', { text: '再跑测试，' }, 't1', '2026-10-10T00:00:06Z'),
    ev('tool.start', { id: 'b', title: 'npm test', kind: 'execute', status: 'in_progress' }, 't1', '2026-10-10T00:00:07Z'),
    ev('tool.end', { id: 'b', status: 'completed' }, 't1', '2026-10-10T00:00:08Z'),
    ev('message.delta', { text: '都好。' }, 't1', '2026-10-10T00:00:09Z'),
    ev('turn.done', {}, 't1', '2026-10-10T00:00:10Z'),
  ]);
  assert.deepEqual(tl.items.map((i) => i.kind), ['user', 'work', 'assistant', 'actions']);
  const [, work, answer] = tl.items;
  if (work.kind !== 'work') return assert.fail('expected work');
  // Thinking after a tool call comes after it, and what was said on the way sits where it was said.
  assert.deepEqual(
    work.entries.map((e) => (e.kind === 'thought' ? `thought:${e.thought.text}` : e.kind === 'step' ? `step:${e.step.title}` : e.kind === 'note' ? `note:${e.text}` : e.kind)),
    ['thought:plan', 'note:先看文件，', 'step:ls', 'thought:next', 'note:再跑测试，', 'step:npm test'],
  );
  assert.deepEqual(work.steps.map((s) => s.title), ['ls', 'npm test']);
  assert.equal(answer.kind === 'assistant' && answer.text, '都好。');
  // The block's time is the turn's: from the question to the end.
  assert.deepEqual([work.startedAt, work.endedAt, work.done], ['2026-10-10T00:00:00Z', '2026-10-10T00:00:10Z', true]);
});

test('while a turn runs, the text being written is outside the block until the agent does more', () => {
  seq = 0;
  const events = [
    ev('turn.started', { text: 'q' }),
    ev('tool.start', { id: 'a', title: 'ls', kind: 'execute', status: 'in_progress' }),
    ev('tool.end', { id: 'a', status: 'completed' }),
    ev('message.delta', { text: '再跑测试，' }),
  ];
  const writing = buildTimeline(events);
  assert.equal(writing.running, true);
  assert.deepEqual(writing.items.map((i) => i.kind), ['user', 'work', 'assistant'], 'it may be the answer');
  assert.equal(writing.items.some((i) => i.kind === 'actions'), false);

  // It was a remark on the way: it becomes the block's latest entry, under the same key, so the
  // view moves the text instead of writing it again.
  const more = buildTimeline([...events, ev('tool.start', { id: 'b', title: 'npm test', kind: 'execute', status: 'in_progress' })]);
  assert.deepEqual(more.items.map((i) => i.kind), ['user', 'work']);
  const work = more.items[1];
  if (work.kind !== 'work') return assert.fail('expected work');
  assert.deepEqual(work.entries.map((e) => e.kind), ['step', 'note', 'step']);
  const said = writing.items[2];
  assert.equal(work.entries[1].kind === 'note' && work.entries[1].key, said.key);
  assert.equal(work.done, false);
});

test('a turn that says something and ends on a tool call still has an answer: its last remark', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'q' }),
    ev('message.delta', { text: '我来写文件。' }),
    ev('tool.start', { id: 'a', title: 'a.ts', kind: 'edit', status: 'in_progress' }),
    ev('tool.end', { id: 'a', status: 'completed' }),
    ev('turn.done'),
  ]);
  assert.deepEqual(tl.items.map((i) => i.kind), ['user', 'work', 'assistant', 'actions']);
  const [, work, answer] = tl.items;
  assert.deepEqual(work.kind === 'work' && work.entries.map((e) => e.kind), ['step']);
  assert.equal(answer.kind === 'assistant' && answer.text, '我来写文件。');
});

test('thinkingPreview shows where a long thought has got to, not where it began', () => {
  assert.equal(thinkingPreview([]), '');
  assert.equal(thinkingPreview(['first.', 'short last.']), 'short last.');
  const long = `${'start of a long plan '.repeat(20)}and this is the end of it.`;
  const preview = thinkingPreview(['earlier', long]);
  assert.ok(preview.startsWith('…') && preview.endsWith('and this is the end of it.'));
  assert.ok(preview.length < 160);
  assert.ok(thinkingPreview(['思'.repeat(300)]).length <= 141, 'text without spaces is cut as it is');
});

test('a tool that finishes after text began still updates the row it started in', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'q' }),
    ev('tool.start', { id: 'a', title: 'npm test', kind: 'execute', status: 'in_progress' }),
    ev('message.delta', { text: '测试在跑，' }),
    ev('tool.update', { id: 'a', output: 'half' }),
    ev('tool.end', { id: 'a', status: 'completed', output: '12 passed' }, 't1', '2026-10-10T00:00:09Z'),
    ev('message.delta', { text: '跑完了。' }),
    ev('turn.done'),
  ]);
  // The late result opens no group of its own and does not cut the text in two.
  assert.deepEqual(tl.items.map((i) => i.kind), ['user', 'work', 'assistant', 'actions']);
  const [, work, text] = tl.items;
  assert.ok(work.kind === 'work');
  assert.deepEqual([work.steps[0].status, work.steps[0].output, work.steps[0].endedAt], ['completed', '12 passed', '2026-10-10T00:00:09Z']);
  assert.equal(text.kind === 'assistant' && text.text, '测试在跑，跑完了。');
});

test('a tool result whose call is not in the loaded events is left out', () => {
  seq = 0;
  const tl = buildTimeline([ev('turn.started', { text: 'q' }), ev('tool.end', { id: 'gone', status: 'completed' }), ev('message.delta', { text: 'ok' })]);
  assert.deepEqual(tl.items.map((i) => i.kind), ['user', 'assistant']);
});

test('a turn that never got its ending is closed where the next one starts', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'cut off' }, 't1'),
    ev('tool.start', { id: 'a', title: 'ls', kind: 'execute', status: 'in_progress' }, 't1'),
    ev('message.delta', { text: 'half' }, 't1'),
    ev('turn.started', { text: 'next' }, 't2'),
    ev('message.delta', { text: 'answer' }, 't2'),
    ev('turn.done', {}, 't2'),
  ]);
  assert.equal(tl.running, false);
  const cut = tl.items.filter((i) => i.turnId === 't1' && (i.kind === 'work' || i.kind === 'assistant'));
  assert.equal(cut.length, 2);
  assert.ok(cut.every((i) => (i.kind === 'work' || i.kind === 'assistant') && i.done), 'nothing of the cut-off turn still reads as working');
  assert.equal(tl.items.some((i) => i.kind === 'actions' && i.turnId === 't1'), false, 'it did not finish, so it gets no action row');
});

test('a turn the hub restart interrupted says so in plain words', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'q' }),
    ev('turn.error', { message: 'The hub restarted while this turn was running.', code: 'interrupted' }),
  ]);
  const error = tl.items.find((i) => i.kind === 'error');
  assert.match(error?.kind === 'error' ? error.message : '', /重启.*中断/);
  assert.equal(tl.running, false);
});


test('a turn that failed because the plan ran out is marked for a takeover, and says why', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'q' }),
    ev('turn.error', { message: "Internal error: You've hit your session limit · resets 10:10am (UTC)", code: 'plan_exhausted' }),
    ev('turn.started', { text: 'again' }),
    ev('turn.error', { message: 'something else' }),
  ]);
  const [ran, other] = tl.items.filter((i) => i.kind === 'error');
  assert.ok(ran?.kind === 'error' && ran.planExhausted);
  assert.match(ran?.kind === 'error' ? ran.message : '', /额度用完了（You've hit your session limit/);
  assert.ok(other?.kind === 'error' && !other.planExhausted);
});

const step = (id: string, kind: string, extra: Partial<Step> = {}): Step => ({ id, title: id, kind, status: 'completed', diffs: [], ...extra });

test('workSections keeps what was said apart and gathers the calls and thinking between into runs', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'q' }),
    ev('reasoning.delta', { text: 'hmm' }),
    ev('tool.start', { id: 'a', kind: 'read' }),
    ev('tool.end', { id: 'a', status: 'completed' }),
    ev('tool.start', { id: 'b', kind: 'search' }),
    ev('tool.end', { id: 'b', status: 'completed' }),
    ev('message.delta', { text: 'found it, fixing' }),
    ev('tool.start', { id: 'c', kind: 'edit' }),
  ]);
  const work = tl.items.find((i) => i.kind === 'work');
  assert.ok(work?.kind === 'work');
  const sections = workSections(work.entries);
  assert.deepEqual(sections.map((s) => s.kind), ['run', 'note', 'run']);
  const [first, , last] = sections;
  assert.ok(first.kind === 'run' && last.kind === 'run');
  assert.deepEqual(first.entries.map((e) => e.kind), ['thought', 'step', 'step'], 'thinking stays in the run, in order');
  assert.deepEqual(first.steps.map((s) => s.id), ['a', 'b']);
  assert.deepEqual(last.steps.map((s) => s.id), ['c']);
  // A run is still the same run when more calls join it: its key is its first entry's.
  assert.notEqual(first.key, last.key);
  assert.equal(workSections(work.entries.slice(0, 2))[0].key, first.key);
});

test('runSummary counts the calls by kind, in one line', () => {
  const summary = runSummary([step('1', 'read'), step('2', 'execute'), step('3', 'read'), step('4', 'search'), step('5', 'mcp-thing')]);
  assert.equal(summary.text, '已浏览 2 个文件，搜索 1 次，运行 1 条命令，调用 1 次工具');
  assert.equal(summary.kind, 'read', 'the kind there is most of');
});

test('runSummary counts edited files once each and includes calls regardless of their result', () => {
  const d = (path: string) => ({ path, oldText: '', newText: 'x', added: 1, removed: 0 });
  const summary = runSummary([
    step('1', 'edit', { diffs: [d('a.ts')] }),
    step('2', 'edit', { diffs: [d('a.ts')] }),
    step('3', 'edit', { diffs: [d('b.ts')] }),
    step('4', 'edit'), // does not say what it wrote
    step('5', 'execute', { status: 'failed' }),
  ]);
  assert.equal(summary.text, '已编辑 3 个文件，运行 1 条命令');
  assert.equal(summary.kind, 'edit');
  assert.equal(runSummary([]).text, '');
});

test('withQuote carries the passage as Markdown quote lines', () => {
  assert.equal(withQuote('one line', 'why?'), '> one line\n\nwhy?');
  // Several lines keep one marker each; empty ones stay empty quote lines.
  assert.equal(withQuote('first\n\nsecond', 'and this?'), '> first\n>\n> second\n\nand this?');
  // A quote alone is a whole message; so is text without one.
  assert.equal(withQuote('  only the quote  ', ''), '> only the quote');
});

test('splitUserQuote reads the leading quote back out of a sent message', () => {
  const roundTrip = splitUserQuote(withQuote('first\nsecond', 'the question'));
  assert.equal(roundTrip.quote, 'first\nsecond');
  assert.equal(roundTrip.body, 'the question');

  // Text with no quote lines up front comes back whole, body only.
  assert.deepEqual(splitUserQuote('plain message'), { quote: null, body: 'plain message' });
  // Only the leading block counts: a quote typed in the middle stays in the body.
  const inner = splitUserQuote('before\n> later');
  assert.equal(inner.quote, null);
  assert.equal(inner.body, 'before\n> later');
  // A quote-only message has no body, and surrounding whitespace is dropped.
  assert.deepEqual(splitUserQuote('> quoted'), { quote: 'quoted', body: '' });
  assert.equal(splitUserQuote('>  spaced  ').quote, 'spaced');
});

test('withNotes numbers each passage, with its comment when there is one, and splitNotes reads them back', () => {
  const sent = withNotes(
    [
      { quote: '输入 0.042 美元', comment: '这个核实了吗？' },
      { quote: 'first\n\nsecond', comment: '' },
    ],
    '  都看一下  ',
  );
  assert.equal(sent, '[注释 1]\n> 输入 0.042 美元\n评论：这个核实了吗？\n\n[注释 2]\n> first\n>\n> second\n\n都看一下');
  assert.deepEqual(splitNotes(sent), {
    notes: [
      { quote: '输入 0.042 美元', comment: '这个核实了吗？' },
      { quote: 'first\n\nsecond', comment: '' },
    ],
    body: '都看一下',
  });
  // Notes alone are a message too; a comment typed over several lines is kept on one.
  assert.equal(withNotes([{ quote: 'x', comment: 'a\n b' }], ''), '[注释 1]\n> x\n评论：a b');
  assert.deepEqual(splitNotes('[注释 1]\n> x'), { notes: [{ quote: 'x', comment: '' }], body: '' });
  // A note picked out of a file carries the file's path on its own line, and it comes back too.
  assert.equal(
    withNotes([{ quote: '写不清楚', comment: '删', file: 'writing/思考是人类的事情.md' }], '好了'),
    '[注释 1]\n文件：writing/思考是人类的事情.md\n> 写不清楚\n评论：删\n\n好了',
  );
  assert.deepEqual(splitNotes('[注释 1]\n文件：writing/思考是人类的事情.md\n> 写不清楚\n评论：删\n\n好了'), {
    notes: [{ quote: '写不清楚', comment: '删', file: 'writing/思考是人类的事情.md' }],
    body: '好了',
  });
  // Anything else is left as it is, including the older single quote.
  assert.deepEqual(splitNotes('> quoted\n\nwhy?'), { notes: [], body: '> quoted\n\nwhy?' });
  assert.deepEqual(splitNotes('see [注释 1] above'), { notes: [], body: 'see [注释 1] above' });
});

test('a stopped turn says so, keeps what was said, and offers nothing to copy or fork from', () => {
  seq = 0;
  const tl = buildTimeline([
    // Stopped at once: only the line.
    ev('turn.started', { text: '你好' }, 't1', '2026-10-10T00:00:00.000Z'),
    ev('turn.done', { stopReason: 'cancelled' }, 't1', '2026-10-10T00:00:00.400Z'),
    // Stopped mid-answer: the words so far are process output, not a final answer.
    ev('turn.started', { text: 'hhh' }, 't2', '2026-10-10T00:00:01.000Z'),
    ev('message.delta', { text: '半句' }, 't2', '2026-10-10T00:00:02.000Z'),
    ev('turn.done', { stopReason: 'cancelled' }, 't2', '2026-10-10T00:00:04.000Z'),
    // Stopped while working: the work block says it and keeps the latest process note.
    ev('turn.started', { text: 'run' }, 't3', '2026-10-10T00:00:05.000Z'),
    ev('tool.start', { id: 'a', title: 'ls', kind: 'execute', status: 'in_progress' }, 't3', '2026-10-10T00:00:06.000Z'),
    ev('message.delta', { text: '还在处理' }, 't3', '2026-10-10T00:00:06.500Z'),
    ev('turn.done', { stopReason: 'cancelled' }, 't3', '2026-10-10T00:00:07.000Z'),
    // A turn that finished as usual still gets its row.
    ev('turn.started', { text: 'again' }, 't4', '2026-10-10T00:00:08.000Z'),
    ev('message.delta', { text: 'answer' }, 't4', '2026-10-10T00:00:09.000Z'),
    ev('turn.done', { stopReason: 'end_turn' }, 't4', '2026-10-10T00:00:10.000Z'),
  ]);
  assert.deepEqual(
    tl.items.map((i) => i.kind),
    ['user', 'stopped', 'user', 'work', 'user', 'work', 'user', 'assistant', 'actions'],
  );
  const noteOnly = tl.items[3];
  assert.equal(noteOnly.kind === 'work' && noteOnly.stopped, true);
  assert.ok(noteOnly.kind === 'work' && noteOnly.entries.some(e => e.kind === 'note' && e.text === '半句'));
  const work = tl.items[5];
  assert.equal(work.kind === 'work' && work.stopped, true);
  assert.ok(work.kind === 'work' && work.entries.some(e => e.kind === 'note' && e.text === '还在处理'));
  assert.ok(!tl.items.some(i => i.kind === 'assistant' && (i.text === '半句' || i.text === '还在处理')));
  assert.equal(tl.items.filter((i) => i.kind === 'actions').length, 1);
});

test('native proposed plans replace draft deltas, remain separate from progress and export their final text', () => {
  const tl = buildTimeline([
    ev('turn.started', { text: '分析一下', workMode: 'plan' }),
    ev('plan.updated', { entries: [{ content: '读取', status: 'in_progress' }] }),
    ev('plan.delta', { id: 'proposal', text: '草稿内容' }),
    ev('plan.completed', { id: 'proposal', text: '# 实施计划\n\n最终方案' }),
    ev('turn.done'),
  ]);
  const proposal = tl.items.find(it => it.kind === 'proposal');
  assert.ok(proposal && proposal.kind === 'proposal');
  assert.equal(proposal.text, '# 实施计划\n\n最终方案');
  assert.equal(tl.items.filter(it => it.kind === 'plan').length, 1);
  assert.match(toMarkdown('会话', tl.items), /最终方案/);
  assert.doesNotMatch(toMarkdown('会话', tl.items), /草稿内容/);
});

test('native questions replay answers and cease waiting after cancellation or daemon restart', () => {
  const asked = [ev('turn.started', { text: '计划' }), ev('input.request', { inputId: 'q1', title: '选择范围', questions: [{ id: 'scope', question: '范围', options: [] }] })];
  assert.equal(buildTimeline(asked).awaitingApproval, true);
  const answered = buildTimeline([...asked, ev('input.resolved', { inputId: 'q1', answer: { answers: { scope: ['手机'] } } })]);
  assert.equal(answered.awaitingApproval, false);
  const item = answered.items.find(it => it.kind === 'input');
  assert.ok(item && item.kind === 'input' && item.answer && 'answers' in item.answer);
  assert.deepEqual(item.answer.answers, { scope: ['手机'] });
  const interrupted = buildTimeline([...asked, ev('turn.error', { code: 'interrupted' })]);
  const old = interrupted.items.find(it => it.kind === 'input');
  assert.ok(old && old.kind === 'input');
  assert.deepEqual(old.answer, { cancelled: true, ended: true });
  assert.equal(interrupted.awaitingApproval, false);
});

test('work after a question, plan or approval lands below the card, and tools started above still get their result', () => {
  for (const card of [
    ev('input.request', { inputId: 'q1', title: '范围', questions: [{ id: 'scope', question: '范围？', options: [{ value: '桌面', label: '桌面' }] }] }),
    ev('approval.request', { approvalId: 'ap', title: 'Run', options: [{ optionId: 'ok', name: '允许这一次', kind: 'allow_once' }] }),
    ev('plan.delta', { id: 'plan', text: '# 方案' }),
  ]) {
    seq = 0;
    const events = [
      ev('turn.started', { text: 'go' }),
      ev('reasoning.delta', { text: 'thinking above' }),
      ev('tool.start', { id: 'above', title: 'read', kind: 'read' }),
      card,
      ev('reasoning.delta', { text: 'thinking below' }),
      ev('tool.end', { id: 'above', status: 'completed', output: 'result' }),
      ev('message.delta', { text: 'answer' }),
      ev('turn.done', {}),
    ];
    const tl = buildTimeline(events);
    const kinds = tl.items.map(i => i.kind);
    const cardAt = kinds.indexOf(card.type === 'input.request' ? 'input' : card.type === 'approval.request' ? 'approval' : 'proposal');
    const works = tl.items.filter(i => i.kind === 'work');
    assert.equal(works.length, 2, `${card.type}: the card ends the block above it`);
    assert.ok(tl.items.indexOf(works[0]) < cardAt && cardAt < tl.items.indexOf(works[1]), `${card.type}: the new block is below the card`);
    assert.ok(works[0].kind === 'work' && works[0].steps[0].output === 'result', `${card.type}: a tool started above the card still gets its result`);
    assert.ok(works[1].kind === 'work' && works[1].entries[0].kind === 'thought', `${card.type}: thinking after the card goes into the new block`);
    assert.ok(works.every(w => w.kind === 'work' && w.done), `${card.type}: every block is done when the turn ends`);
  }
});

test('only the last work block hands its closing remark over as the answer', () => {
  seq = 0;
  const tl = buildTimeline([
    ev('turn.started', { text: 'go' }),
    ev('message.delta', { text: '第一段话' }),
    ev('tool.start', { id: 'a', title: 'ls', kind: 'execute' }),
    ev('input.request', { inputId: 'q1', title: '范围', questions: [{ id: 'scope', question: '范围？', options: [] }] }),
    ev('input.resolved', { inputId: 'q1', answer: { cancelled: true } }),
    ev('message.delta', { text: '最后一段话' }),
    ev('tool.start', { id: 'b', title: 'ls', kind: 'execute' }),
    ev('turn.done', {}),
  ]);
  const works = tl.items.filter(i => i.kind === 'work');
  assert.equal(works.length, 2);
  // The earlier remark stays a note inside its block; the last one becomes the answer.
  assert.ok(works[0].kind === 'work' && works[0].entries.some(e => e.kind === 'note' && e.text === '第一段话'));
  assert.ok(!tl.items.some(i => i.kind === 'assistant' && i.text === '第一段话'));
  assert.ok(tl.items.some(i => i.kind === 'assistant' && i.text === '最后一段话'));
});

test('events arriving after their turn ended close as their own stretch, never a box that keeps working', () => {
  // Two daemons once overlapped on one data directory: the second wrote the turn's ending while
  // the first kept streaming, so the log held tool events under a turn id that was already closed.
  // The next turn must not merge into that leftover, or its timer counts from the old turn's start.
  const events = [
    ev('turn.started', { text: '旁路验证' }, 'A', '2026-10-16T09:22:49.000Z'),
    ev('tool.start', { id: 'a1', kind: 'execute' }, 'A', '2026-10-16T09:23:00.000Z'),
    ev('tool.end', { id: 'a1', status: 'completed' }, 'A', '2026-10-16T09:23:01.000Z'),
    ev('turn.error', { message: 'The hub restarted while this turn was running.', code: 'interrupted' }, 'A', '2026-10-16T09:34:56.000Z'),
    // The first daemon's last events: same turn, after its ending.
    ev('tool.start', { id: 'a2', kind: 'execute' }, 'A', '2026-10-16T09:35:10.000Z'),
    ev('tool.end', { id: 'a2', status: 'completed' }, 'A', '2026-10-16T09:35:36.000Z'),
    // Three days later the user writes again.
    ev('turn.started', { text: '报告一下进度' }, 'B', '2026-10-19T12:02:18.000Z'),
    ev('tool.start', { id: 'b1', kind: 'execute' }, 'B', '2026-10-19T12:02:53.000Z'),
    ev('tool.end', { id: 'b1', status: 'completed' }, 'B', '2026-10-19T12:02:54.000Z'),
    ev('turn.done', { stopReason: 'end_turn' }, 'B', '2026-10-19T12:03:10.000Z'),
  ];
  const timeline = buildTimeline(events);
  assert.equal(timeline.running, false);
  const works = timeline.items.filter(i => i.kind === 'work');
  assert.ok(works.length > 0);
  assert.ok(works.every(w => w.kind === 'work' && w.done), 'nothing keeps ticking as if a turn were still running');
  // The new turn's block measures from its own start, not from the ended turn three days back.
  const turnB = works.find(w => w.kind === 'work' && w.turnId === 'B');
  assert.ok(turnB);
  assert.equal(turnB.startedAt, '2026-10-19T12:02:18.000Z');
  assert.equal(turnB.endedAt, '2026-10-19T12:03:10.000Z');
  // The late events read as their own finished stretch, not as work the old turn began.
  const late = works.find(w => w.kind === 'work' && w.turnId === '');
  assert.ok(late);
  assert.equal(late.startedAt, '2026-10-16T09:35:10.000Z');
  assert.equal(late.endedAt, '2026-10-16T09:35:36.000Z');
});

test('a deferred stretch that outlived its turn ending shows its own duration once loaded', () => {
  // The history endpoint compresses runs of tool events into placeholders that expand on demand.
  // One such stretch followed its turn's ending — two daemons once overlapped — so no turn.done
  // will ever say how long it ran; the placeholder carries its last timestamp instead.
  const events = [
    ev('turn.started', { text: 'go' }, 'A', '2026-10-16T09:22:49.000Z'),
    ev('history.work', { count: 1, version: 3, files: [], endedAt: '2026-10-16T09:23:40.000Z' }, 'A', '2026-10-16T09:23:00.000Z'),
    ev('turn.error', { message: 'The hub restarted while this turn was running.', code: 'interrupted' }, 'A', '2026-10-16T09:34:56.000Z'),
    ev('history.work', { count: 2, version: 9, files: [], endedAt: '2026-10-16T09:40:27.000Z' }, 'A', '2026-10-16T09:35:10.000Z'),
    ev('turn.started', { text: '报告一下进度' }, 'B', '2026-10-19T12:02:18.000Z'),
    ev('turn.done', { stopReason: 'end_turn' }, 'B', '2026-10-19T12:03:10.000Z'),
  ];
  const works = buildTimeline(events).items.filter(i => i.kind === 'work');
  assert.ok(works.length === 2);
  assert.ok(works.every(w => w.kind === 'work' && w.done));
  const late = works.find(w => w.kind === 'work' && w.turnId === '');
  assert.ok(late);
  assert.equal(late.startedAt, '2026-10-16T09:35:10.000Z');
  assert.equal(late.endedAt, '2026-10-16T09:40:27.000Z');
});

test('handover summary activity stays one separate turn across live updates', () => {
  const events = [ev('turn.started', { text: 'task' }, 'task'), ev('message.delta', { text: 'answer' }, 'task'), ev('turn.done', {}, 'task'),
    ev('history.handover', { target: 'Agent 2', done: false, version: 4 }, 'summary'),
    ev('history.handover', { target: 'Agent 2', done: true, version: 7 }, 'summary')];
  const { items, running } = buildTimeline(events);
  const markers = items.filter(i => i.kind === 'handover-summary');
  assert.equal(markers.length, 1); assert.equal(markers[0].done, true); assert.equal(running, false);
  assert.equal(items.filter(i => i.kind === 'user').length, 1);
  assert.deepEqual(groupByTurn(items).map(g => g.turnId), ['task', 'summary']);
});


test('a failed summary stops its preparing label and keeps the error visible', () => {
  const { items, running } = buildTimeline([ev('history.handover', { target: 'Agent 2', done: false, version: 1 }), ev('turn.error', { message: 'summary failed' })]);
  assert.equal(items.find(i => i.kind === 'handover-summary')?.done, true);
  assert.equal(items.find(i => i.kind === 'error')?.message, 'summary failed');
  assert.equal(running, false);
});

test('agent notices preserve their text and confirmation link marker without changing turn outcome', () => {
  const timeline = buildTimeline([
    ev('turn.started', { text: 'Check the draft' }),
    ev('agent.notice', { text: 'The draft is ready.' }),
    ev('agent.notice', { text: '待你确认：Review report', confirmation: true }),
    ev('turn.done'),
  ]);
  const notices = timeline.items.filter(item => item.kind === 'notice');
  assert.equal(notices.length, 2);
  assert.deepEqual(notices.map(item => [item.text, item.confirmation]), [['The draft is ready.', false], ['待你确认：Review report', true]]);
  assert.equal(timeline.running, false);
});

test('a quota-resume marker divides the timeline where the daemon continued the thread by itself', () => {
  const timeline = buildTimeline([
    ev('turn.started', { text: 'keep working' }),
    ev('turn.error', { message: 'You have exhausted your quota on this model.', code: 'plan_exhausted' }),
    ev('handover.resume', { resetsAt: '2026-10-12T09:00:00.000Z' }, null),
    ev('turn.started', { text: 'keep working' }, 't2'),
    ev('message.delta', { text: 'resumed' }, 't2'),
    ev('turn.done', {}, 't2'),
  ]);
  const marker = timeline.items.find(item => item.kind === 'quota-resume');
  assert.ok(marker?.kind === 'quota-resume');
  assert.equal(marker.resetsAt, '2026-10-12T09:00:00.000Z');
  assert.ok(!marker.turnId);
  assert.ok(timeline.items.some(item => item.kind === 'error' && item.planExhausted === true));
});

test('a turn a channel started is marked by its source, never by its words', () => {
  const user = (data: Record<string, any>) => buildTimeline([ev('turn.started', data)]).items.find(i => i.kind === 'user') as any;
  assert.deepEqual(user({ text: 'raw', displayText: '飞书 · 3 条新信号', channel: { kind: 'lark', count: 3 } }).channel, { kind: 'lark', count: 3 });
  assert.equal(user({ text: '飞书 · 3 条新信号' }).channel, undefined);
  assert.equal(user({ text: 'x', channel: { count: 2 } }).channel, undefined);
});

test('a mixed batch keeps its parts in order: a person\'s words stay words, the rest are quiet lines', () => {
  const parts = [{ kind: 'lark', count: 2 }, { kind: 'person', text: '先记下周三的会' }, { kind: 'worker', count: 1 }, { kind: 'person', text: '买票' }];
  const channel = turnChannel({ kind: 'mixed', count: 5, parts })!;
  assert.deepEqual(channel.parts, parts);
  assert.deepEqual(channelRows(channel, 'joined'), [
    { kind: 'note', text: '飞书 · 2 条新信号' }, { kind: 'person', text: '先记下周三的会' }, { kind: 'note', text: '工作 · 1 条进展' }, { kind: 'person', text: '买票' },
  ]);
});

test('single channels are one quiet line, and a batch that cannot say what it holds stays a message', () => {
  assert.deepEqual(channelRows(turnChannel({ kind: 'worker', count: 3 })!, 'x'), [{ kind: 'note', text: '工作 · 3 条进展' }]);
  assert.deepEqual(channelRows(turnChannel({ kind: 'future', count: 1 })!, 'as sent'), [{ kind: 'note', text: 'as sent' }]);
  assert.equal(turnChannel({ kind: 'mixed', count: 2 }), null);
  assert.equal(turnChannel({ kind: 'mixed', count: 2, parts: [{ kind: 'nonsense' }] }), null);
  assert.deepEqual(turnChannel({ kind: 'mixed', count: 2, parts: [{ kind: 'nonsense' }, { kind: 'person', text: '话' }] })!.parts, [{ kind: 'person', text: '话' }]);
});

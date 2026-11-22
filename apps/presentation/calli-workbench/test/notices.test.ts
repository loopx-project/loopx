import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Thread } from '../src/api.js';
import { attentionCount, pageTitle, threadMark, threadNotices, waitingLabel } from '../src/notices.js';

const thread = (id: string, over: Partial<Thread> = {}): Thread => ({
  id,
  agent: 'claude',
  cwd: '/work/code',
  title: `Thread ${id}`,
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
  cwdReal: '/work/code',
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  ...over,
});

test('threadNotices keeps successful completion quiet and reports errors elsewhere', () => {
  // "b" ran and finished between the two snapshots; it is reported all the same.
  const before = [thread('a', { status: 'running' }), thread('b'), thread('c', { status: 'running' })];
  const after = [thread('a', { unread: true }), thread('b', { status: 'error', unread: true }), thread('c', { status: 'running' })];
  assert.deepEqual(
    threadNotices(before, after, null).map((n) => [n.threadId, n.kind]),
    [['b', 'error']],
  );
  assert.match(threadNotices(before, after, null)[0].message, /Thread b.*出错了/);
  // A turn the user stopped is not marked unread, so it is not announced.
  assert.deepEqual(threadNotices([thread('a', { status: 'running' })], [thread('a')], null), []);
});

test('threadNotices reports a thread that starts waiting for approval, once', () => {
  const idle = [thread('a', { status: 'running' })];
  const waiting = [thread('a', { status: 'running', waiting: true, waitingReason: 'approval' })];
  assert.deepEqual(threadNotices(idle, waiting, null).map((n) => [n.kind, n.action]), [['waiting', '去批准']]);
  assert.deepEqual(threadNotices(waiting, waiting, null), [], 'no repeat while it keeps waiting');
});

test('threadNotices says nothing about the thread on screen, or about threads it has not seen before', () => {
  const before = [thread('a', { status: 'running' })];
  const after = [thread('a', { unread: true }), thread('new', { waiting: true })];
  assert.deepEqual(threadNotices(before, after, 'a'), []);
  assert.deepEqual(threadNotices([], after, null), [], 'the first load is not news');
});

test('long titles are shortened in a notice', () => {
  const title = 'A very long thread title that would not fit in a toast at all';
  const [notice] = threadNotices([thread('a', { title, status: 'running' })], [thread('a', { title, status: 'error', unread: true })], null);
  assert.ok(notice.message.includes('…'));
  assert.ok(notice.message.length < 40);
});

test('attentionCount counts waiting threads and unread ones that are not on screen', () => {
  const list = [thread('a', { unread: true }), thread('b', { waiting: true }), thread('c', { unread: true }), thread('d')];
  assert.equal(attentionCount(list, null), 3);
  assert.equal(attentionCount(list, 'a'), 2, 'the open thread is being read');
  assert.equal(attentionCount(list, 'b'), 3, 'an approval still needs answering even when open');
  assert.equal(pageTitle('Calli', 3), '(3) Calli');
  assert.equal(pageTitle('Calli', 0), 'Calli');
  assert.equal(pageTitle('(13) (13) Calli', 2), '(2) Calli');
  assert.equal(pageTitle('(13) Calli', 0), 'Calli');
});

test('threadMark picks one dot: waiting, then running, then error, then unread', () => {
  assert.equal(threadMark(thread('a', { waiting: true, status: 'running', unread: true }), false), 'waiting');
  assert.equal(threadMark(thread('a', { status: 'running', unread: true }), false), 'running');
  assert.equal(threadMark(thread('a', { status: 'error', unread: true }), false), 'error');
  assert.equal(threadMark(thread('a', { status: 'error' }), false), null, 'a failure already looked at keeps no dot');
  assert.equal(threadMark(thread('a', { status: 'error', unread: true }), true), null, 'the open thread shows its error itself');
  assert.equal(threadMark(thread('a', { unread: true }), false), 'unread');
  assert.equal(threadMark(thread('a', { unread: true }), true), null, 'the open thread is not shown as unread');
  assert.equal(threadMark(thread('a'), false), null);
});

test('threadNotices does not repeat a thread that stays unread, nor one marked unread by hand while running', () => {
  const unread = [thread('a', { unread: true })];
  assert.deepEqual(threadNotices(unread, unread, null), []);
  // Still running: the next turn of a queue has started, nothing to announce yet.
  assert.deepEqual(threadNotices([thread('a')], [thread('a', { status: 'running', unread: true })], null), []);
});

test('waiting labels and notices distinguish questions, plans, approvals and older daemons', () => {
  const cases = [
    ['approval', '等待批准', '去批准'],
    ['question', '等待回答', '去回答'],
    ['plan', '计划待确认', '查看计划'],
    [undefined, '等待处理', '去查看'],
  ] as const;
  for (const [waitingReason, label, action] of cases) {
    const pending = thread('a', { waiting: true, waitingReason });
    assert.equal(waitingLabel(pending), label);
    assert.equal(threadNotices([thread('a')], [pending], null)[0].action, action);
    assert.deepEqual(threadNotices([pending], [pending], null), []);
    assert.deepEqual(threadNotices([thread('a')], [pending], 'a'), []);
    assert.equal(threadMark(pending, false), 'waiting');
    assert.equal(attentionCount([pending], null), 1);
  }
  const question = thread('a', { waiting: true, waitingReason: 'question' });
  const plan = thread('a', { waiting: true, waitingReason: 'plan' });
  assert.equal(threadNotices([question], [plan], null)[0].action, '查看计划');
  assert.deepEqual(threadNotices([plan], [thread('a')], null), []);
});

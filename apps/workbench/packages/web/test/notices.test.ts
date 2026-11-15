import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Thread } from '../src/api.js';
import { attentionCount, pageTitle, threadMark, waitingLabel } from '../src/notices.js';

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

test('waiting labels distinguish questions, plans, approvals and older daemons', () => {
  const cases = [
    ['approval', '等待批准'],
    ['question', '等待回答'],
    ['plan', '计划待确认'],
    [undefined, '等待处理'],
  ] as const;
  for (const [waitingReason, label] of cases) {
    const pending = thread('a', { waiting: true, waitingReason });
    assert.equal(waitingLabel(pending), label);
    assert.equal(threadMark(pending, false), 'waiting');
    assert.equal(attentionCount([pending], null), 1);
  }
});

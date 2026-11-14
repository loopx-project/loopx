import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentSession } from '../src/api.js';
import { filterSessions, sessionTitle, timeAgo } from '../src/sessions.js';

const session = (id: string, over: Partial<AgentSession> = {}): AgentSession => ({
  id,
  cwd: '/work/docs-site',
  title: id,
  updatedAt: null,
  threadId: null,
  ...over,
});

test('filterSessions matches the title or the directory, ignoring case, and keeps the order', () => {
  const list = [
    session('a', { title: 'Fix the Login bug' }),
    session('b', { title: 'Tidy imports', cwd: '/work/login-service' }),
    session('c', { title: 'Write release notes' }),
  ];
  assert.deepEqual(filterSessions(list, 'login').map((s) => s.id), ['a', 'b']);
  assert.deepEqual(filterSessions(list, '  RELEASE ').map((s) => s.id), ['c']);
  assert.equal(filterSessions(list, ''), list, 'no search text returns the list as it is');
  assert.deepEqual(filterSessions(list, 'nothing like this'), []);
});

test('sessionTitle falls back to the directory when the agent has no title', () => {
  assert.equal(sessionTitle(session('a', { title: 'Fix the login bug' })), 'Fix the login bug');
  assert.equal(sessionTitle(session('a', { title: '  ' })), 'docs-site 里的未命名会话');
});

test('timeAgo is relative for the last week and a date after that', () => {
  const now = Date.parse('2026-05-20T12:00:00');
  const ago = (ms: number) => new Date(now - ms).toISOString();
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  assert.equal(timeAgo(ago(20_000), now), '刚刚');
  assert.equal(timeAgo(ago(5 * minute), now), '5 分钟前');
  assert.equal(timeAgo(ago(59 * minute + 59_000), now), '59 分钟前');
  assert.equal(timeAgo(ago(3 * hour), now), '3 小时前');
  assert.equal(timeAgo(ago(2 * day), now), '2 天前');
  assert.equal(timeAgo(ago(6 * day + 23 * hour), now), '6 天前');
  assert.equal(timeAgo('2026-05-03T09:00:00', now), '5月3日');
  assert.equal(timeAgo('2025-11-30T09:00:00', now), '2025年11月30日', 'another year is spelled out');
  // A clock that is slightly ahead on the other machine must not show a negative time.
  assert.equal(timeAgo(ago(-30_000), now), '刚刚');
  assert.equal(timeAgo(null, now), '');
  assert.equal(timeAgo('not a date', now), '');
});

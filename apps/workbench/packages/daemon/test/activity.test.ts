import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { Activity, mcpCallOf } from '../src/activity.js';

/** The tables Activity reads, with the columns it reads, in an in-memory database. */
function open() {
  const db = new DatabaseSync(':memory:');
  db.exec(`
    CREATE TABLE threads (
      id TEXT PRIMARY KEY, agent TEXT NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL,
      agent_session_id TEXT, status TEXT NOT NULL DEFAULT 'idle', pinned INTEGER NOT NULL DEFAULT 0,
      permission_mode TEXT NOT NULL DEFAULT 'auto_edit', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      archived_at TEXT
    );
    CREATE TABLE events (
      thread_id TEXT NOT NULL, seq INTEGER NOT NULL, turn_id TEXT, type TEXT NOT NULL, ts TEXT NOT NULL, data TEXT NOT NULL,
      PRIMARY KEY (thread_id, seq)
    );
    CREATE TABLE turn_text (
      thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, role TEXT NOT NULL, ts TEXT NOT NULL, text TEXT NOT NULL,
      PRIMARY KEY (thread_id, turn_id, role)
    );
  `);
  const thread = (id: string, agent = 'claude', title = id, archived: string | null = null) =>
    db.prepare('INSERT INTO threads (id, agent, cwd, title, agent_session_id, created_at, updated_at, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
      .run(id, agent, '/project', title, `${id}-session`, '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z', archived);
  const ask = (threadId: string, turnId: string, ts: string, text: string) =>
    db.prepare('INSERT INTO turn_text (thread_id, turn_id, role, ts, text) VALUES (?, ?, ?, ?, ?)').run(threadId, turnId, 'user', ts, text);
  const tool = (threadId: string, seq: number, ts: string, data: Record<string, unknown>) =>
    db.prepare('INSERT INTO events (thread_id, seq, turn_id, type, ts, data) VALUES (?, ?, ?, ?, ?, ?)').run(threadId, seq, 't', 'tool.start', ts, JSON.stringify(data));
  return { db, thread, ask, tool };
}

/** A local time `daysAgo` days back, at the hour given — day grouping is the daemon's own. */
const localTs = (daysAgo: number, hour: number): string => {
  const base = new Date();
  const day = new Date(base.getFullYear(), base.getMonth(), base.getDate() - daysAgo, hour, 30, 0);
  return day.toISOString();
};

test('the local day a timestamp belongs to, not the UTC one it is written in', () => {
  const h = open();
  try {
    // Both around local midnight: one minute before belongs to yesterday, one after to today.
    const base = new Date();
    const midnight = new Date(base.getFullYear(), base.getMonth(), base.getDate(), 0, 1);
    const before = new Date(midnight.getTime() - 2 * 60_000).toISOString();
    h.thread('a');
    h.ask('a', 'late', before, '深夜的一问');
    h.ask('a', 'early', midnight.toISOString(), '刚过零点的一问');
    const days = new Activity(h.db).read(1);
    assert.equal(days.length, 2);
    assert.equal(days[0].prompts[0].text, '深夜的一问', 'the earlier day holds the ask from before midnight');
    assert.equal(days[1].prompts[0].text, '刚过零点的一问');
  } finally { h.db.close(); }
});

test('asks are newest first with their thread, and a flooded day says how much was held back', () => {
  const h = open();
  try {
    h.thread('a', 'claude', '长任务');
    h.thread('b', 'codex', '另一个');
    for (let i = 0; i < 55; i++) h.ask('a', `t${i}`, localTs(0, 9 + Math.floor(i / 30)), `第 ${i} 个问题`);
    h.ask('b', 'only', localTs(0, 12), '另一个线程的问题');
    const [day] = new Activity(h.db).read(1);
    assert.equal(day.turns, 56);
    assert.equal(day.threads, 2);
    assert.equal(day.prompts.length, 50);
    assert.equal(day.morePrompts, 6);
    assert.ok(day.prompts[0].ts >= day.prompts[1].ts, 'newest ask first');
    assert.equal(day.prompts[0].threadId, 'b');
    assert.equal(day.prompts[0].title, '另一个');
    // A long ask is cut, and says so.
    h.ask('a', 'long', localTs(0, 15), '长'.repeat(600));
    const [flooded] = new Activity(h.db).read(1);
    assert.equal(flooded.morePrompts, 7);
  } finally { h.db.close(); }
});

test('tools count by raw name, old rows by kind, and MCP calls are told apart', () => {
  const h = open();
  try {
    h.thread('a');
    h.tool('a', 1, localTs(0, 10), { tool: 'Read', kind: 'read' });
    h.tool('a', 2, localTs(0, 10), { tool: 'Read', kind: 'read' });
    h.tool('a', 3, localTs(0, 11), { tool: 'mcp__github__create_issue', kind: 'other' });
    h.tool('a', 4, localTs(0, 11), { tool: 'create_issue', server: 'github', kind: 'other' });
    h.tool('a', 5, localTs(0, 12), { kind: 'edit' }); // a row from before names were kept
    h.tool('a', 6, localTs(0, 12), { kind: 'execute' });
    h.tool('a', 7, localTs(0, 12), { kind: 'execute' });
    h.tool('a', 8, localTs(1, 10), { tool: 'Read', kind: 'read' }); // yesterday
    const [yesterday, today] = new Activity(h.db).read(2);
    assert.equal(yesterday.tools.length, 1);
    assert.deepEqual(today.tools.map((t) => [t.name, t.count, t.mcp]), [
      ['Read', 2, false],
      ['运行命令', 2, false],
      ['create_issue', 1, true],
      ['mcp__github__create_issue', 1, true],
      ['编辑文件', 1, false],
    ], 'ties break by name');
    assert.equal(today.tools.find((t) => t.name === 'mcp__github__create_issue')?.server, 'github');
    assert.equal(today.tools.find((t) => t.name === 'create_issue')?.server, 'github');
    assert.deepEqual(today.mcp, [{ server: 'github', count: 2 }]);
    // Threads that only called a tool still count for the day, archived ones included.
    assert.equal(today.threads, 1);
  } finally { h.db.close(); }
});

test('the agent filter narrows both asks and tools; the range clamps', () => {
  const h = open();
  try {
    h.thread('a', 'claude');
    h.thread('b', 'codex');
    h.ask('a', 't1', localTs(0, 10), 'claude 的问题');
    h.ask('b', 't1', localTs(0, 11), 'codex 的问题');
    h.tool('a', 1, localTs(0, 10), { tool: 'Read' });
    h.tool('b', 2, localTs(0, 11), { tool: 'shell' });
    const [day] = new Activity(h.db).read(14, 'codex');
    assert.equal(day.prompts.length, 1);
    assert.equal(day.prompts[0].agent, 'codex');
    assert.deepEqual(day.tools.map((t) => t.name), ['shell']);
    assert.equal(day.threads, 1);
    // An absurd range clamps to one day, not to nothing.
    const clamped = new Activity(h.db).read(-5);
    assert.equal(clamped.length, new Activity(h.db).read(1).length);
    assert.ok(clamped.length >= 1);
  } finally { h.db.close(); }
});

test('an MCP call is recognized from either spelling, and near-misses are not', () => {
  assert.deepEqual(mcpCallOf({ server: 'github', tool: 'create_issue' }), { server: 'github', tool: 'create_issue' });
  assert.deepEqual(mcpCallOf({ tool: 'mcp__botmux__run' }), { server: 'botmux', tool: 'run' });
  assert.deepEqual(mcpCallOf({ tool: 'mcp__a__b__c' }), { server: 'a', tool: 'b__c' }, 'a tool name may contain the separator');
  assert.equal(mcpCallOf({ tool: 'Read' }), null);
  assert.equal(mcpCallOf({ tool: 'mcp__onlyserver' }), null, 'a server alone is not a tool');
  assert.equal(mcpCallOf({ tool: 'mcp__' }), null);
  assert.equal(mcpCallOf({ server: '' }), null);
});

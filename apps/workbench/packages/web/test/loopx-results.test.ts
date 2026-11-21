import assert from 'node:assert/strict';
import test from 'node:test';
import { parseResultPage, readResult, resultKey } from '../src/loopx/results.js';

const row = { todo_id: 't1', title: 'Report', producer_agent_id: 'codex', sha256: 'abc', content_type: 'text/markdown', size_bytes: 10, completed_at: '2026-11-16T01:00:00Z' };
const page = (over: Record<string, unknown> = {}) => ({ ok: true, items: [row], total: 1, next_cursor: null, unavailable_count: 0, unavailable_todo_ids: [], ...over });

test('a complete inventory parses, and rows without an identity are dropped', () => {
  const parsed = parseResultPage(page({ items: [row, { title: 'no id' }] }));
  assert.equal(parsed.items.length, 1);
  assert.equal(parsed.items[0].producer, 'codex');
  assert.equal(parsed.nextCursor, null);
});

test('an inventory that does not add up is refused rather than shown', () => {
  assert.throws(() => parseResultPage(page({ items: 'x' })));
  assert.throws(() => parseResultPage(page({ unavailable_count: 2, unavailable_todo_ids: ['a'] })));
  assert.throws(() => parseResultPage(page({ next_cursor: 5 })));
});

test('opening a report checks it is still the row that was listed', async () => {
  const listed = parseResultPage(page()).items[0];
  const answer = (over: Record<string, unknown> = {}) => ({ ok: true, goal_id: 'g', todo_id: 't1', text: '# hi', result: { sha256: 'abc', producer_agent_id: 'codex', content_type: 'text/markdown' }, ...over });
  const original = globalThis.fetch;
  try {
    for (const [body, ok] of [[answer(), true], [answer({ result: { sha256: 'zzz', producer_agent_id: 'codex', content_type: 'text/markdown' } }), false], [answer({ goal_id: 'other' }), false], [answer({ text: 3 }), false]] as const) {
      globalThis.fetch = (async () => new Response(JSON.stringify(body), { status: 200 })) as typeof fetch;
      if (ok) assert.equal((await readResult('t', 'g', listed)).text, '# hi');
      else await assert.rejects(readResult('t', 'g', listed));
    }
    globalThis.fetch = (async () => new Response('{}', { status: 409 })) as typeof fetch;
    await assert.rejects(readResult('t', 'g', listed));
  } finally { globalThis.fetch = original; }
  assert.equal(resultKey(listed), JSON.stringify(['t1', 'abc', 'codex', 'text/markdown']));
});

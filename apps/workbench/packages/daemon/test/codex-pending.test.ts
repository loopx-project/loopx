import test from 'node:test';
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { TraexAcpAdapter } from '../src/adapters/traex.js';
import type { EmittedEvent } from '../src/events.js';

const here = dirname(fileURLToPath(import.meta.url));
async function setup(name: 'Codex' | 'TRAE') {
  const adapter = name === 'Codex'
    ? new CodexAppServerAdapter([process.execPath, join(here, 'fixtures/fake-codex.mjs')])
    : new TraexAcpAdapter([process.execPath, join(here, 'fixtures/fake-acp.mjs')]);
  const session = await adapter.start({ cwd: here });
  const send = async (text: string) => {
    const events: EmittedEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const result = await Promise.race([
        session.prompt({ text, images: [] }, { emit: event => events.push(event), requestApproval: async () => ({ cancelled: true }) }),
        new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(`lost completion: ${text}`)), 3000); }),
      ]);
      return { result, events, text: events.filter(e => e.type === 'message.delta').map(e => e.data.text).join('') };
    } finally { clearTimeout(timer); }
  };
  return { session, send };
}
for (const name of ['Codex', 'TRAE'] as const) {
test(`${name} handles completion and items in the same batch as the turn start response`, async () => {
  const { session, send } = await setup(name);
  try {
    for (const text of ['batch-completed', 'batch-item-first', 'batch-completed-only', 'batch-failed', 'batch-rejected']) {
      const answer = await send(text);
      assert.equal(answer.result.stopReason, text === 'batch-failed' || text === 'batch-rejected' ? 'error' : 'end_turn');
      if (text === 'batch-completed' || text === 'batch-item-first') assert.equal(answer.text, 'Immediate answer');
      if (text === 'batch-failed') assert.equal(answer.result.error, 'fixture turn failed');
      if (text === 'batch-rejected') assert.match(answer.result.error ?? '', /fixture start rejected/);
      assert.equal((await send('hello')).result.stopReason, 'end_turn', 'a settled pending turn never blocks the next turn');
    }
  } finally { await session.close(); }
});
test(`${name} ignores late previous-turn notifications while a new start is pending`, async t => {
  const { session, send } = await setup(name);
  const warnings = t.mock.method(console, 'warn', () => undefined);
  try {
    for (const mode of ['batch-late-previous', 'batch-late-no-start', 'batch-mismatched-start', 'batch-noise']) {
      assert.equal((await send('hello')).result.stopReason, 'end_turn');
      const answer = await send(mode);
      assert.equal(answer.result.stopReason, 'end_turn');
      assert.equal(answer.text, 'Immediate answer');
      assert.ok(!answer.events.some(e => e.type === 'usage.updated' && e.data.used === 999999));
    }
    assert.equal(warnings.mock.callCount(), 1);
    assert.match(String(warnings.mock.calls[0].arguments[0]), /turn\/start.*id mismatch/);
    assert.equal((await send('hello')).result.stopReason, 'end_turn');
  } finally { await session.close(); }
});
}

import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexAppServerAdapter, turnsFromCodexHistory } from '../src/adapters/codex.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { mapAcpUpdate } from '../src/adapters/acp.js';
import { TraexAcpAdapter } from '../src/adapters/traex.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { ZcodeAdapter, mapZcodeEvent } from '../src/adapters/zcode.js';
import type { AgentAdapter, TurnHooks } from '../src/adapters/types.js';
import type { EmittedEvent } from '../src/events.js';

const fixture = (name: string) => [process.execPath, fileURLToPath(new URL(`./fixtures/fake-${name}.mjs`, import.meta.url))];
const dataDir = mkdtempSync(join(tmpdir(), 'calli-auto-compaction-'));
after(() => rmSync(dataDir, { recursive: true, force: true }));
const zcode = () => new ZcodeAdapter(fixture('zcode'), { ZCODE_DATA_BASE_DIR: dataDir });
const adapters: Array<() => AgentAdapter> = [
  () => new CodexAppServerAdapter(fixture('codex')),
  () => new TraexAcpAdapter(fixture('acp'), { FAKE_ACP_FLAVOR: 'traex' }),
  () => new ClaudeAcpAdapter(fixture('acp')),
  () => new PiRpcAdapter(fixture('pi')),
  zcode,
];
const collect = () => {
  const events: EmittedEvent[] = [];
  const hooks: TurnHooks = { emit: e => events.push(e), requestApproval: async () => ({ cancelled: true }) };
  return { events, hooks, compactions: () => events.filter(e => e.type === 'context.compaction') };
};

test('native auto compaction is streamed by all five adapters without counting as a tool', async t => {
  for (const make of adapters) {
    const adapter = make();
    await t.test(adapter.id, async t => {
      const session = await adapter.start({ cwd: process.cwd() });
      t.after(() => session.close());
      const c = collect();
      const result = await session.prompt({ text: 'auto-compact', images: [] }, c.hooks);
      assert.equal(result.stopReason, 'end_turn');
      assert.equal(c.compactions()[0]?.data.status, 'in_progress');
      assert.equal(c.compactions().at(-1)?.data.status, 'completed');
      assert.ok(c.compactions().every(e => e.data.automatic === true));
      assert.equal(new Set(c.compactions().map(e => e.data.id)).size, 1);
      assert.ok(!c.events.some(e => e.type === 'tool.start' && /compact/i.test(String(e.data.title))));
      const manual = collect();
      assert.equal((await session.prompt({ text: '/compact', images: [] }, manual.hooks)).stopReason, 'end_turn');
      assert.equal(manual.compactions().length, 0, 'manual wrapper does not duplicate native signals');
      assert.equal(manual.events.filter(e => e.type === 'tool.start' && e.data.title === '压缩上下文').length, 1);
    });
  }
});

test('Pi supports current and legacy signals, repeated operations and unsuccessful outcomes', async t => {
  const session = await new PiRpcAdapter(fixture('pi')).start({ cwd: process.cwd() });
  t.after(() => session.close());
  for (const [text, status] of [['legacy', 'completed'], ['fail', 'failed'], ['cancel', 'cancelled'], ['skip', 'skipped'], ['twice', 'completed']]) {
    const c = collect();
    await session.prompt({ text: `auto-compact ${text}`, images: [] }, c.hooks);
    assert.equal(c.compactions().at(-1)?.data.status, status);
    assert.equal(new Set(c.compactions().map(e => e.data.id)).size, text === 'twice' ? 2 : 1);
  }
});

test('Claude explicit compaction updates preserve failures and cancellation', async t => {
  const session = await new ClaudeAcpAdapter(fixture('acp')).start({ cwd: process.cwd() });
  t.after(() => session.close());
  for (const [text, status] of [['native', 'completed'], ['native fail', 'failed'], ['native cancel', 'cancelled']]) {
    const c = collect();
    await session.prompt({ text: `auto-compact ${text}`, images: [] }, c.hooks);
    assert.equal(c.compactions().at(-1)?.data.status, status);
  }
  assert.equal(mapAcpUpdate({ sessionUpdate: 'tool_call', title: 'Compact conversation', toolCallId: 'ordinary' })?.type, 'tool.start', 'a title alone is not a native compaction signal');
});

test('ZCode requires native operation metadata and preserves skipped outcomes', async t => {
  assert.equal(mapZcodeEvent({ type: 'session.updated', payload: { status: 'completed' } }), null);
  const session = await zcode().start({ cwd: process.cwd() });
  t.after(() => session.close());
  for (const [text, status] of [['fail', 'failed'], ['skip', 'skipped']]) {
    const c = collect();
    await session.prompt({ text: `auto-compact ${text}`, images: [] }, c.hooks);
    assert.equal(c.compactions().at(-1)?.data.status, status);
  }
});

test('usage reductions do not synthesize compaction events', async t => {
  const session = await new CodexAppServerAdapter(fixture('codex')).start({ cwd: process.cwd() });
  t.after(() => session.close());
  const c = collect();
  await session.prompt({ text: 'usage', images: [] }, c.hooks);
  await session.prompt({ text: 'usage compacted', images: [] }, c.hooks);
  assert.equal(c.compactions().length, 0);
  assert.equal(c.events.filter(e => e.type === 'usage.updated').at(-1)?.data.used, 12000);
});

test('Codex native history preserves completed compaction without guessing its trigger', () => {
  const history = turnsFromCodexHistory([{ id: 'turn', items: [
    { type: 'userMessage', content: [{ type: 'text', text: 'hello' }] },
    { type: 'contextCompaction', id: 'compact' },
    { type: 'agentMessage', text: 'done' },
  ] }]);
  assert.deepEqual(history[0].events.filter(e => e.type === 'context.compaction'), [
    { type: 'context.compaction', data: { id: 'compact', status: 'in_progress' } },
    { type: 'context.compaction', data: { id: 'compact', status: 'completed' } },
  ]);
});

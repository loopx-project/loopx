import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { REMIND_EVERY } from '../src/injections.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

// Protocol blocks the hub appends to messages are read once per session, not on every turn.

const prompts: string[] = [];
const echoAdapter = {
  label: 'Echo',
  available: async () => true,
  capabilities: { resume: false, approvals: false, permissionModes: [], effortLevels: [], images: false, deleteSession: false, history: false, fork: false },
  resumeCommand: (id: string) => `echo resume ${id}`,
  async commands() { return []; },
  async start() {
    return {
      agentSessionId: 'echo-session',
      async prompt(input: { text: string }, hooks: { emit(event: { type: string; data: Record<string, unknown> }): void }) {
        prompts.push(input.text);
        hooks.emit({ type: 'message.delta', data: { text: 'ok' } });
        return { stopReason: 'end_turn' };
      },
      async cancel() {},
      async close() {},
    };
  },
} as unknown as AgentAdapter;

async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-injections-'));
  const work = mkdtempSync(join(tmpdir(), 'calli-injections-work-'));
  const store = new Store(dir);
  const open = () => {
    const manager = new ThreadManager(store, new Map([['echo', echoAdapter]]), [work], new AttachmentStore(dir));
    manager.progressWanted = () => true;
    return manager;
  };
  let manager = open();
  const thread = await manager.createThread({ agent: 'echo', cwd: work });
  const say = async (text: string) => {
    const before = prompts.length;
    await manager.startTurn(thread.id, { text });
    for (let i = 0; i < 300 && (prompts.length === before || manager.threadView(thread.id).status === 'running'); i++) await new Promise(r => setTimeout(r, 10));
    return prompts.at(-1)!;
  };
  return {
    store, thread, say, manager: () => manager,
    async restart() { await manager.shutdown(); manager = open(); },
    async close() { await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); rmSync(work, { recursive: true, force: true }); },
  };
}
const carried = (prompt: string) => prompt.includes('<calli-progress>');

test('a block is attached to the first turn and left out until it is worth a reminder', async () => {
  const h = await setup();
  try {
    assert.ok(carried(await h.say('一')), 'the first turn carries it');
    for (let i = 0; i < REMIND_EVERY; i++) assert.equal(await h.say(`轮 ${i}`), `轮 ${i}`, 'later turns go as written');
    assert.ok(carried(await h.say('提醒')), 'a reminder after enough turns');
    assert.equal(await h.say('之后'), '之后');
    const shown = h.store.listEventsOfType(h.thread.id, 'turn.started').filter(e => carried(String(e.data.text))).map(e => e.data.displayText);
    assert.deepEqual(shown, ['一', '提醒'], 'the person sees what they wrote, with or without the block');
  } finally { await h.close(); }
});

test('the block comes back after compaction, a failed turn or a new agent session, and a restart does not repeat it', async () => {
  const h = await setup();
  try {
    assert.ok(carried(await h.say('一')));
    assert.equal(await h.say('二'), '二');

    (h.manager() as unknown as { emitThreadEvent(...a: unknown[]): void }).emitThreadEvent(h.thread.id, null, 'context.compaction', { status: 'completed' });
    assert.ok(carried(await h.say('压缩后')), 'what was said may have been summarised away');
    assert.equal(await h.say('四'), '四');

    (h.manager() as unknown as { emitThreadEvent(...a: unknown[]): void }).emitThreadEvent(h.thread.id, 'failed-turn', 'turn.error', { message: 'x' });
    assert.ok(carried(await h.say('失败后')), 'a turn that failed may never have been read');

    h.store.updateThread(h.thread.id, { agentSessionId: 'another-session' });
    assert.ok(carried(await h.say('换会话')), 'another session has not read it');
    assert.equal(await h.say('同一会话'), '同一会话');

    await h.restart();
    assert.equal(await h.say('重启后'), '重启后', 'a restart keeps the session, and the record');
  } finally { await h.close(); }
});

test('records go with their thread, and a thread without any gets the block', async () => {
  const h = await setup();
  try {
    await h.say('一');
    assert.ok(h.store.injection(h.thread.id, 'progress'));
    h.store.deleteThread(h.thread.id);
    assert.equal(h.store.injection(h.thread.id, 'progress'), null);
  } finally { await h.close(); }
});

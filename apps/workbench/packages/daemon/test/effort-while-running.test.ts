import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { AttachmentStore } from '../src/attachments.js';

const here = dirname(fileURLToPath(import.meta.url));

async function idle(manager: ThreadManager, id: string): Promise<void> {
  for (let i = 0; i < 500; i++) {
    if (manager.threadView(id).status !== 'running') return;
    await new Promise((r) => setTimeout(r, 20));
  }
  assert.fail('thread still running');
}

async function waitRunning(manager: ThreadManager, id: string): Promise<void> {
  for (let i = 0; i < 500; i++) {
    if (manager.threadView(id).status === 'running') return;
    await new Promise((r) => setTimeout(r, 20));
  }
  assert.fail('turn never started');
}

/**
 * Changing the model, effort or speed while a turn runs is accepted, not refused: the setting is
 * stored at once, the running turn keeps the old one, and the session is rebuilt before the next
 * turn so it resumes the agent session with the new setting. That is /model's semantics in
 * Claude Code, and what Codex does by passing the model with the next turn's start.
 */
test('an effort change during a running turn is stored and reaches the next turn', { timeout: 60_000 }, async () => {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-effort-data-'));
  const workdir = mkdtempSync(join(tmpdir(), 'calli-effort-work-'));
  const effortLog = join(dataDir, 'effort.log');
  // The fake pi holds its first turn open long enough to change the setting mid-turn, refuses the
  // in-place change while streaming, and logs every thinking-level attempt.
  process.env.FAKE_PI_FIRST_OUTPUT_DELAY_MS = '2500';
  process.env.FAKE_PI_EFFORT_BUSY = '1';
  process.env.FAKE_PI_EFFORT_LOG = effortLog;
  let manager: ThreadManager | null = null;
  let store: Store | null = null;
  try {
    const adapters = new Map<string, AgentAdapter>([['pi', new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')])]]);
    store = new Store(dataDir);
    manager = new ThreadManager(store, adapters, new Workdirs([workdir], join(dataDir, 'workdirs.json'), join(dataDir, 'scratch')), new AttachmentStore(dataDir));
    const thread = await manager.createThread({ agent: 'pi', cwd: workdir });

    await manager.startTurn(thread.id, { text: 'wait-first' });
    await waitRunning(manager, thread.id);
    // The change lands while the turn is in flight. Before, this was a 409.
    const updated = await manager.updateThread(thread.id, { effort: 'high' });
    assert.equal(updated.effort, 'high');
    assert.equal(manager.threadView(thread.id).effort, 'high');

    await idle(manager, thread.id);
    // The next turn must not reuse the stale session: it starts with the stored effort.
    await manager.startTurn(thread.id, { text: 'and again' });
    await idle(manager, thread.id);

    const attempts = readFileSync(effortLog, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    const during = attempts.filter((a) => a.running);
    const after = attempts.filter((a) => !a.running && a.level === 'high');
    assert.ok(during.length >= 1, 'the busy session was asked and refused');
    assert.ok(after.length >= 1, 'the rebuilt session started with the new effort');
  } finally {
    if (manager) await manager.shutdown();
    store?.close();
    delete process.env.FAKE_PI_FIRST_OUTPUT_DELAY_MS;
    delete process.env.FAKE_PI_EFFORT_BUSY;
    delete process.env.FAKE_PI_EFFORT_LOG;
    rmSync(dataDir, { recursive: true, force: true });
    rmSync(workdir, { recursive: true, force: true });
  }
});

/** The same deferral resets a stored effort or model back to the agent's default. */
test('clearing the effort mid-turn also waits for the turn', { timeout: 60_000 }, async () => {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-effort-clear-'));
  const workdir = mkdtempSync(join(tmpdir(), 'calli-effort-clear-work-'));
  process.env.FAKE_PI_FIRST_OUTPUT_DELAY_MS = '2500';
  process.env.FAKE_PI_EFFORT_BUSY = '1';
  let manager: ThreadManager | null = null;
  let store: Store | null = null;
  try {
    const adapters = new Map<string, AgentAdapter>([['pi', new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')])]]);
    store = new Store(dataDir);
    manager = new ThreadManager(store, adapters, new Workdirs([workdir], join(dataDir, 'workdirs.json'), join(dataDir, 'scratch')), new AttachmentStore(dataDir));
    const thread = await manager.createThread({ agent: 'pi', cwd: workdir, effort: 'high' });

    await manager.startTurn(thread.id, { text: 'wait-first' });
    await waitRunning(manager, thread.id);
    const updated = await manager.updateThread(thread.id, { effort: null });
    assert.equal(updated.effort, null);
    await idle(manager, thread.id);
    // The next turn still runs: the reset did not break the session it was deferred to.
    await manager.startTurn(thread.id, { text: 'still here' });
    await idle(manager, thread.id);
    assert.equal(manager.threadView(thread.id).status, 'idle');
  } finally {
    if (manager) await manager.shutdown();
    store?.close();
    delete process.env.FAKE_PI_FIRST_OUTPUT_DELAY_MS;
    delete process.env.FAKE_PI_EFFORT_BUSY;
    rmSync(dataDir, { recursive: true, force: true });
    rmSync(workdir, { recursive: true, force: true });
  }
});

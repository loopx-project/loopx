import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { AttachmentStore } from '../src/attachments.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

const here = dirname(fileURLToPath(import.meta.url));

async function waitUntil<T>(fn: () => T | null | undefined, timeoutMs = 5000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const value = fn();
    if (value) return value;
    if (Date.now() - start > timeoutMs) throw new Error('timed out waiting');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

test('model queue status is exposed while a turn waits and cleared when it ends', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'rah-model-queue-'));
  const store = new Store(dir);
  const manager = new ThreadManager(
    store,
    new Map([['codex', new CodexAppServerAdapter([process.execPath, join(here, 'fixtures/fake-codex.mjs')])]]),
    [dir],
    new AttachmentStore(dir),
  );
  try {
    const thread = await manager.createThread({ agent: 'codex', cwd: dir });
    await manager.startTurn(thread.id, { text: 'modelqueue slow' });
    const queued = await waitUntil(() => manager.threadView(thread.id).modelQueue?.position === 330 ? manager.threadView(thread.id).modelQueue : null);
    assert.deepEqual(queued, {
      state: 'waiting',
      position: 330,
      operation: null,
      message: 'Too many current requests. Your queue position is 330. Please wait for a while.',
      updatedAt: queued.updatedAt,
    });
    assert.ok(store.listEventsOfType(thread.id, 'turn.phase').some(e => e.data.phase === 'model_queue' && e.data.position === 330));

    await manager.cancelTurn(thread.id);
    await waitUntil(() => manager.threadView(thread.id).status !== 'running' ? true : null);
    assert.equal(manager.threadView(thread.id).modelQueue, null);
  } finally {
    await manager.shutdown();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

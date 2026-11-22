import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AgyAdapter } from '../src/adapters/agy.js';
import { AttachmentStore } from '../src/attachments.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

test('grouped catalogs preserve legacy thread, archive and per-account preference choices', async () => {
  const root = mkdtempSync(join(tmpdir(), 'agy-family-migration-'));
  const catalog = join(root, 'models.json');
  const native = [{ id: 'fast-high', label: 'Fast (High)' }, { id: 'fast-low', label: 'Fast (Low)' }, { id: 'solo-medium', label: 'Solo (Medium)' }];
  writeFileSync(catalog, JSON.stringify({ models: native }));
  const adapter = new AgyAdapter([process.execPath, fileURLToPath(new URL('./fixtures/fake-agy.mjs', import.meta.url))], { FAKE_AGY_MODELS_FILE: catalog });
  const other = new AgyAdapter([process.execPath, fileURLToPath(new URL('./fixtures/fake-agy.mjs', import.meta.url))]);
  const store = new Store(join(root, 'data'));
  const manager = new ThreadManager(store, new Map([['secondary', adapter], ['other', other]]), [root], new AttachmentStore(root));
  const updated: string[] = [];
  const off = manager.subscribe(event => { if (event.type === 'thread.updated') updated.push(event.threadId); });
  try {
    const active = await manager.createThread({ agent: 'secondary', cwd: root });
    const archived = await manager.createThread({ agent: 'secondary', cwd: root });
    const fixed = await manager.createThread({ agent: 'secondary', cwd: root });
    const elsewhere = await manager.createThread({ agent: 'other', cwd: root });
    store.updateThread(active.id, { model: 'fast-high', effort: null });
    store.updateThread(archived.id, { model: 'fast-low', effort: 'high', archivedAt: new Date().toISOString() });
    store.updateThread(fixed.id, { model: 'solo-medium', effort: null });
    store.updateThread(elsewhere.id, { model: 'fast-low', effort: null });
    store.saveHubSetting('draft.model.preferences', {
      secondary: { model: 'fast-high', effort: null, speed: null, perModel: { 'solo-medium': { effort: null, speed: null } } },
      other: { model: 'fast-low', effort: null, speed: null },
    });
    updated.length = 0;
    await manager.listModels('secondary');
    assert.equal(store.getThread(active.id)?.model, 'fast');
    assert.equal(store.getThread(active.id)?.effort, 'high');
    assert.equal(store.getThread(archived.id)?.model, 'fast');
    assert.equal(store.getThread(archived.id)?.effort, 'high', 'an explicit effort wins over the old suffix');
    assert.ok(store.getThread(archived.id)?.archivedAt);
    assert.equal(store.getThread(fixed.id)?.model, 'solo-medium', 'a fixed-effort model keeps its native id');
    assert.equal(store.getThread(elsewhere.id)?.model, 'fast-low');
    const preferences = await manager.draftModelPreferences();
    assert.equal(preferences.secondary.model, 'fast');
    assert.equal(preferences.secondary.effort, 'high');
    assert.ok(preferences.secondary.perModel?.['solo-medium']);
    assert.equal(preferences.other.model, 'fast-low');
    assert.deepEqual(updated.sort(), [active.id, archived.id].sort());
    updated.length = 0;
    await manager.listModels('secondary', true);
    assert.deepEqual(updated, [], 'catalog refresh does not repeatedly rewrite migrated threads');

    // Unknown or unavailable catalogs do not erase choices or invent a family mapping.
    store.updateThread(active.id, { model: 'future-high', effort: null });
    writeFileSync(catalog, JSON.stringify({ error: true }));
    await assert.rejects(manager.listModels('secondary', true));
    assert.equal(store.getThread(active.id)?.model, 'future-high');
    assert.deepEqual(await manager.draftModelPreferences(), preferences);
  } finally { off(); await manager.shutdown(); store.close(); rmSync(root, { recursive: true, force: true }); }
});

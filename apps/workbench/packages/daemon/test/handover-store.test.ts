import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { Store } from '../src/store.js';

test('legacy handovers recover only from explicit local first-turn coordinates and survive source deletion', () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-handover-store-'));
  let store = new Store(root);
  try {
    const create = (id: string, cwd = '/project') => store.createThread({ id, cwd, agent: 'test', title: 'Same title', permissionMode: 'auto_edit', model: null, effort: null });
    create('source');
    const envelope = '# 交接信息\n\n- Hub 线程 ID：source\n\n## 接手要求\nContinue';
    for (const [id, text, cwd] of [
      ['valid', '来源机器：本机（当前 hub）\n\n' + envelope, '/project'],
      ['remote', '来源机器：开发机\n\n' + envelope, '/project'],
      ['quoted', 'Here is an example:\n' + envelope, '/project'],
      ['other-dir', envelope, '/other'],
      ['later', 'ordinary request', '/project'],
    ]) {
      create(id, cwd);
      store.appendEvent(id, 'one', 'turn.started', { text });
    }
    store.appendEvent('later', 'two', 'turn.started', { text: envelope });
    store.close();
    const db = new DatabaseSync(join(root, 'hub.db'));
    db.exec("UPDATE threads SET created_at = '2020-01-01' WHERE id = 'source'; ALTER TABLE threads DROP COLUMN handover;");
    db.close();
    store = new Store(root);
    assert.deepEqual(store.getThread('valid')?.handover, { sourceId: 'source', title: 'Same title', agent: 'test' });
    for (const id of ['remote', 'quoted', 'other-dir', 'later']) assert.equal(store.getThread(id)?.handover, null);
    store.deleteThread('source');
    store.close();
    store = new Store(root);
    assert.equal(store.getThread('valid')?.handover?.title, 'Same title');
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});

test('lastContextUsed reads the newest reported context, and null when none was reported', () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-context-used-'));
  const store = new Store(root);
  try {
    store.createThread({ id: 't', cwd: '/project', agent: 'test', title: 't', permissionMode: 'auto_edit', model: null, effort: null });
    assert.equal(store.lastContextUsed('t'), null);
    store.appendEvent('t', 'one', 'usage.updated', { used: 126_000, size: 200_000 });
    store.appendEvent('t', 'one', 'usage.updated', { used: null, size: 200_000 });
    assert.equal(store.lastContextUsed('t'), null);
    store.appendEvent('t', 'two', 'usage.updated', { used: 30_000, size: 200_000 });
    assert.equal(store.lastContextUsed('t'), 30_000);
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});

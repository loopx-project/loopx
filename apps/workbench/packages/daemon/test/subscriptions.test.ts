import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { Store, type TodoSubscription } from '../src/store.js';

const subscription = (ref: string): TodoSubscription => ({ kind: 'lark-thread', ref, at: '2026-11-01T00:00:00Z' });
test('subscriptions migrate empty, persist, deduplicate by kind/ref and enforce twenty unique entries atomically', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-subscriptions-')); let store = new Store(dir);
  try {
    store.createTodo('old', 'Existing matter', null); store.close();
    const db = new DatabaseSync(join(dir, 'hub.db'));
    try { db.exec('ALTER TABLE todos DROP COLUMN subscriptions'); } finally { db.close(); }
    store = new Store(dir); assert.deepEqual(store.getTodo('old')!.subscriptions, []);
    const initial = store.createTodo('one', 'Subscribed matter', null, { subscriptions: [subscription('ref'), subscription('ref'), { ...subscription('ref'), kind: 'lark-task' }] });
    assert.equal(initial.subscriptions.length, 2);
    assert.deepEqual(store.subscribedTodos({ kind: 'lark-thread', ref: 'ref' }).map(t => t.id), ['one']);
    assert.deepEqual(store.subscribedTodos({ kind: 'lark-calendar', ref: 'ref' }), []);
    assert.deepEqual(store.subscribedTodos({ kind: 'lark-thread', ref: 'REF' }), []);
    const twenty = Array.from({ length: 20 }, (_, i) => subscription(`ref-${i}`));
    const full = store.updateTodo('one', { subscriptions: [...twenty, ...twenty] }, initial.rev);
    assert.equal(full.subscriptions.length, 20);
    assert.throws(() => store.updateTodo('one', { subscriptions: [...twenty, subscription('overflow')] }, full.rev), /20 subscriptions/);
    assert.equal(store.getTodo('one')!.rev, full.rev);
    assert.throws(() => store.createTodo('invalid', 'Overflow', null, { subscriptions: [...twenty, subscription('overflow')] }), /20 subscriptions/);
    assert.equal(store.getTodo('invalid'), null);
    for (const bad of [{ ...subscription('ref'), kind: 'manual' }, subscription(''), subscription('white space'), subscription('x'.repeat(201)), { ...subscription('ref'), at: 'invalid' }]) {
      assert.throws(() => store.updateTodo('one', { subscriptions: [bad as TodoSubscription] }), /subscription/);
    }
    store.close(); store = new Store(dir);
    assert.equal(store.getTodo('one')!.subscriptions.length, 20);
    assert.deepEqual(store.subscribedTodos({ kind: 'lark-thread', ref: 'ref-19' }).map(t => t.id), ['one']);
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});

import test, { type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { Store, type SignalDisposition } from '../src/store.js';
import type { MeetingReference, SignalMaterialRead } from '../src/channels/lark-material.js';

const at = '2026-11-01T00:00:00.000Z';
const ready: SignalMaterialRead = {
  material: { state: 'ready', kind: 'note', checkedAt: at, partial: true },
  content: 'Store-only meeting material\n## TODO\n- Review the summary',
};

function fixture(t: TestContext) {
  const directory = mkdtempSync(join(tmpdir(), 'calli-signals-'));
  let store: Store | undefined;
  const database = <T>(work: (db: DatabaseSync) => T): T => {
    const db = new DatabaseSync(join(directory, 'hub.db'));
    try { return work(db); } finally { db.close(); }
  };
  t.after(() => {
    try { store?.close(); } finally { rmSync(directory, { recursive: true, force: true }); }
  });
  const reopen = (beforeOpen?: (db: DatabaseSync) => void) => {
    store?.close(); store = undefined;
    if (beforeOpen) database(beforeOpen);
    store = new Store(directory);
  };
  reopen();
  return { get store() { assert.ok(store); return store; }, database, reopen };
}

test('signals deduplicate, persist updated destinations, query matters and prune after ninety days', () => {
  const directory = mkdtempSync(join(tmpdir(), 'calli-signals-'));
  let store = new Store(directory);
  try {
    const now = new Date('2026-11-01T00:00:00.000Z');
    const boundary = new Date(now.getTime() - 90 * 86400000).toISOString();
    const payload = { summary: '请看材料', source: { type: 'lark-message', ref: 'om_first' } };
    assert.equal(store.insertSignal('lark-message:om_first', payload, { outcome: 'router', pending: true }, boundary), true);
    assert.equal(store.insertSignal('lark-message:om_first', { different: true }, { outcome: 'filtered' }), false);
    store.updateSignalDisposition('lark-message:om_first', { outcome: 'delivered', threadId: 'worker', matterIds: ['one', 'two'] }, now.toISOString());
    assert.deepEqual(store.getSignal('lark-message:om_first')?.payload, payload);
    assert.equal(store.listSignals().length, 1);
    assert.equal(store.listSignals({ pending: true }).length, 0);
    assert.equal(store.listSignals({ matterId: 'two' })[0].disposition.outcome, 'delivered');
    assert.deepEqual(store.listSignals({ matterId: 'absent' }), []);
    store.insertSignal('old', {}, { outcome: 'filtered', reason: 'outside-watch-scope' }, new Date(Date.parse(boundary) - 1).toISOString());
    store.close(); store = new Store(directory);
    assert.equal(store.getSignal('lark-message:om_first')?.updatedAt, now.toISOString());
    store.pruneSignals(now);
    assert.equal(store.getSignal('old'), null);
    assert.equal(store.listSignals().length, 1, 'exact ninety-day boundary remains');
  } finally { store.close(); rmSync(directory, { recursive: true, force: true }); }
});

test('signal reads expose scoped material metadata, never the body or another scope', t => {
  const f = fixture(t);
  const payload = { summary: 'Meeting ended', source: { type: 'lark-meeting', ref: 'event-a' }, meeting: { event: 'ended', meetingId: 'meeting-a' } };
  const disposition: SignalDisposition = { outcome: 'router', pending: true, matterIds: ['matter-a'] };
  f.store.insertSignal('cached', payload, disposition, at);
  f.store.insertSignal('uncached', {}, { outcome: 'delivered' }, at);
  f.store.saveSignalMaterial('cached', 'scope-a', 'reference-a', ready);
  const cached = { key: 'cached', at, payload, disposition, updatedAt: at };
  const uncached = { key: 'uncached', at, payload: {}, disposition: { outcome: 'delivered' }, updatedAt: at };
  assert.deepEqual(f.store.getSignal('cached'), cached);
  assert.deepEqual(f.store.getSignal('cached', 'scope-a'), { ...cached, material: ready.material });
  assert.deepEqual(f.store.getSignal('cached', 'scope-b'), cached);
  assert.equal(f.store.getSignal('missing', 'scope-a'), null);
  assert.deepEqual(f.store.listSignals(), [cached, uncached]);
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope-a' }), [{ ...cached, material: ready.material }, uncached]);
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope-b' }), [cached, uncached]);
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope-a', matterId: 'matter-a', pending: true }), [{ ...cached, material: ready.material }]);
  assert.deepEqual(f.store.getSignalMaterial('cached', 'scope-a', 'reference-a'), ready);
  f.database(db => {
    const row = db.prepare('SELECT s.payload, s.disposition, m.material, m.content FROM signals s JOIN signal_materials m ON m.signal_key = s.key WHERE s.key = ?')
      .get('cached') as { payload: string; disposition: string; material: string; content: string };
    assert.deepEqual(JSON.parse(row.payload), payload);
    assert.deepEqual(JSON.parse(row.disposition), disposition);
    assert.deepEqual(JSON.parse(row.material), ready.material);
    assert.equal(row.content, ready.content);
  });
});

test('material reads require exact key, scope and reference, and saves replace the entire cache entry', t => {
  const f = fixture(t);
  f.store.insertSignal('signal', {}, { outcome: 'router' }, at);
  f.store.insertSignal('other', {}, { outcome: 'router' }, at);
  f.store.saveSignalMaterial('signal', 'scope-a', 'reference-a', ready);
  f.store.saveSignalMaterial('other', 'scope-a', 'reference-a', ready);
  for (const [key, scope, reference] of [
    ['missing', 'scope-a', 'reference-a'], ['signal', 'scope-b', 'reference-a'],
    ['signal', 'SCOPE-A', 'reference-a'], ['signal', 'scope-a', 'reference-b'],
    ['signal', 'scope-a', 'REFERENCE-A'],
  ]) assert.equal(f.store.getSignalMaterial(key, scope, reference), null, `${key}/${scope}/${reference}`);
  const notReady: SignalMaterialRead = { material: { state: 'not-ready', kind: 'minutes', checkedAt: at }, content: null };
  f.store.saveSignalMaterial('signal', 'scope-a', 'reference-b', notReady);
  assert.equal(f.store.getSignalMaterial('signal', 'scope-a', 'reference-a'), null);
  assert.deepEqual(f.store.getSignalMaterial('signal', 'scope-a', 'reference-b'), notReady);
  const replacement: SignalMaterialRead = { material: { state: 'ready', kind: 'minutes', checkedAt: at }, content: 'Replacement minutes' };
  f.store.saveSignalMaterial('signal', 'scope-b', 'reference-c', replacement);
  assert.equal(f.store.getSignalMaterial('signal', 'scope-a', 'reference-b'), null);
  assert.equal(f.store.getSignalMaterial('signal', 'scope-a', 'reference-c'), null);
  assert.deepEqual(f.store.getSignalMaterial('signal', 'scope-b', 'reference-c'), replacement);
  assert.equal(f.store.getSignal('signal', 'scope-a')?.material, undefined);
  assert.deepEqual(f.store.getSignal('signal', 'scope-b')?.material, replacement.material);
  const failed: SignalMaterialRead = { material: { state: 'failed', checkedAt: at, error: 'material_permission_denied' }, content: null };
  f.store.saveSignalMaterial('signal', 'scope-b', 'reference-c', failed);
  assert.deepEqual(f.store.getSignalMaterial('signal', 'scope-b', 'reference-c'), failed);
  assert.deepEqual(f.store.getSignal('signal', 'scope-b')?.material, failed.material);
  assert.deepEqual(f.store.getSignalMaterial('other', 'scope-a', 'reference-a'), ready);
  assert.equal(f.store.getSignal('signal')?.updatedAt, at, 'cache writes do not update the signal');
  f.database(db => assert.equal((db.prepare('SELECT COUNT(*) AS count FROM signal_materials WHERE signal_key = ?').get('signal') as { count: number }).count, 1));
  assert.throws(() => f.store.saveSignalMaterial('missing', 'scope-a', 'reference-a', ready), /FOREIGN KEY constraint failed/);
  assert.equal(f.store.getSignalMaterial('missing', 'scope-a', 'reference-a'), null);
});

test('cached null bodies retain distinct unread, not-ready, empty and failed metadata', t => {
  const f = fixture(t);
  f.store.insertSignal('uncached', {}, { outcome: 'router' }, at);
  assert.equal(f.store.getSignalMaterial('uncached', 'scope', 'reference'), null);
  assert.equal(f.store.getSignal('uncached', 'scope')?.material, undefined);
  const reads: SignalMaterialRead[] = [
    { material: { state: 'unread' }, content: null },
    { material: { state: 'not-ready', kind: 'note', checkedAt: at }, content: null },
    { material: { state: 'empty', kind: 'note', checkedAt: at, partial: true }, content: null },
    { material: { state: 'failed', checkedAt: at, error: 'material_not_found' }, content: null },
  ];
  for (const read of reads) {
    const key = read.material.state;
    f.store.insertSignal(key, {}, { outcome: 'router' }, at);
    f.store.saveSignalMaterial(key, 'scope', 'reference', ready);
    f.store.saveSignalMaterial(key, 'scope', 'reference', read);
    assert.deepEqual(f.store.getSignalMaterial(key, 'scope', 'reference'), read);
    assert.deepEqual(f.store.getSignal(key, 'scope')?.material, read.material);
    assert.deepEqual(f.store.listSignals({ materialScope: 'scope' }).find(row => row.key === key)?.material, read.material);
  }
});

test('clearing material caches removes all scopes without changing signals or dispositions', t => {
  const f = fixture(t);
  for (const key of ['a', 'b']) {
    f.store.insertSignal(key, { summary: key }, { outcome: 'router', pending: true, matterIds: ['matter'] }, at);
    f.store.saveSignalMaterial(key, `scope-${key}`, `reference-${key}`, ready);
  }
  const signals = f.store.listSignals();
  f.store.clearSignalMaterials(); f.store.clearSignalMaterials();
  assert.deepEqual(f.store.listSignals(), signals);
  for (const key of ['a', 'b']) {
    assert.equal(f.store.getSignalMaterial(key, `scope-${key}`, `reference-${key}`), null);
    assert.equal(f.store.getSignal(key, `scope-${key}`)?.material, undefined);
  }
  f.reopen();
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope-a' }), signals);
  f.database(db => assert.equal((db.prepare('SELECT COUNT(*) AS count FROM signal_materials').get() as { count: number }).count, 0));
});

test('reopening preserves material metadata, nullable bodies and scope/reference isolation', t => {
  const f = fixture(t);
  f.store.insertSignal('ready', { meeting: { event: 'ended', meetingId: 'meeting-a' } }, { outcome: 'delivered', matterIds: ['matter'] }, at);
  f.store.insertSignal('empty', {}, { outcome: 'router', pending: true }, at);
  const empty: SignalMaterialRead = { material: { state: 'empty', kind: 'minutes', checkedAt: at }, content: null };
  f.store.saveSignalMaterial('ready', 'scope-a', 'reference-a', ready);
  f.store.saveSignalMaterial('empty', 'scope-b', 'reference-b', empty);
  const plain = f.store.listSignals();
  const scoped = f.store.listSignals({ materialScope: 'scope-a' });
  for (let i = 0; i < 2; i++) {
    f.reopen();
    assert.deepEqual(f.store.listSignals(), plain);
    assert.deepEqual(f.store.listSignals({ materialScope: 'scope-a' }), scoped);
    assert.deepEqual(f.store.getSignalMaterial('ready', 'scope-a', 'reference-a'), ready);
    assert.deepEqual(f.store.getSignalMaterial('empty', 'scope-b', 'reference-b'), empty);
    assert.equal(f.store.getSignalMaterial('ready', 'scope-b', 'reference-a'), null);
    assert.equal(f.store.getSignalMaterial('ready', 'scope-a', 'reference-b'), null);
    assert.deepEqual(f.store.getSignal('empty', 'scope-b')?.material, empty.material);
  }
});

test('legacy signals reopen without material schema and gain the meeting index and cascading cache table', t => {
  const f = fixture(t);
  f.store.insertSignal('legacy-meeting', { summary: 'Existing meeting', meeting: { event: 'ended', meetingId: 'meeting-a' } }, { outcome: 'router', pending: true }, at);
  f.store.insertSignal('legacy-message', { summary: 'Existing message', source: { type: 'lark-message', ref: 'message-a' } }, { outcome: 'filtered', reason: 'outside-watch-scope' }, at);
  const signals = f.store.listSignals();
  f.reopen(db => db.exec('DROP TABLE signal_materials; DROP INDEX signals_meeting;'));
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope' }), signals);
  assert.deepEqual(f.store.meetingSignals('meeting-a'), [f.store.getSignal('legacy-meeting')]);
  assert.equal(f.store.getSignalMaterial('legacy-meeting', 'scope', 'reference'), null);
  f.database(db => {
    assert.deepEqual((db.prepare('PRAGMA table_info(signals)').all() as Array<{ name: string }>).map(column => column.name), ['key', 'at', 'payload', 'disposition', 'updated_at']);
    assert.equal((db.prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'signals_meeting'").get() as { name: string }).name, 'signals_meeting');
    const foreignKeys = db.prepare('PRAGMA foreign_key_list(signal_materials)').all() as Array<{ table: string; from: string; to: string; on_delete: string }>;
    assert.deepEqual(foreignKeys.map(row => ({ table: row.table, from: row.from, to: row.to, on_delete: row.on_delete })), [
      { table: 'signals', from: 'signal_key', to: 'key', on_delete: 'CASCADE' },
    ]);
    assert.deepEqual(db.prepare('PRAGMA foreign_key_check').all(), []);
  });
  f.store.saveSignalMaterial('legacy-meeting', 'scope', 'reference', ready);
  f.reopen();
  assert.deepEqual(f.store.listSignals(), signals);
  assert.deepEqual(f.store.getSignalMaterial('legacy-meeting', 'scope', 'reference'), ready);
  assert.deepEqual(f.store.getSignal('legacy-meeting', 'scope')?.material, ready.material);
});

test('meeting updates invalidate only changed signals while preserving payload, disposition and original time', t => {
  const f = fixture(t);
  const meeting: MeetingReference = { event: 'ended', meetingId: 'meeting-a', title: 'Meeting' };
  const payload = { summary: 'Keep summary', source: { type: 'lark-meeting', ref: 'event-a' }, extra: { keep: true }, meeting };
  const disposition: SignalDisposition = { outcome: 'router', pending: true, matterIds: ['matter'] };
  f.store.insertSignal('changed', payload, disposition, at);
  f.store.insertSignal('other', { meeting }, { outcome: 'delivered' }, at);
  f.store.insertSignal('no-meeting', { summary: 'Keep this too', extra: { keep: true } }, { outcome: 'router' }, at);
  f.store.saveSignalMaterial('changed', 'scope-a', 'old-reference', ready);
  f.store.saveSignalMaterial('other', 'scope-b', 'other-reference', ready);
  const unchanged = f.store.getSignal('changed', 'scope-a');
  const other = f.store.getSignal('other', 'scope-b');
  const updatedAt = '2026-11-02T00:00:00.000Z';
  f.store.updateSignalMeeting('changed', { ...meeting }, updatedAt);
  f.store.updateSignalMeeting('missing', meeting, updatedAt);
  assert.deepEqual(f.store.getSignal('changed', 'scope-a'), unchanged);
  assert.deepEqual(f.store.getSignalMaterial('changed', 'scope-a', 'old-reference'), ready);
  assert.equal(f.store.getSignal('missing'), null);
  const enriched: MeetingReference = { ...meeting, noteId: 'note-a', noteDocToken: 'doc-a' };
  f.store.updateSignalMeeting('changed', enriched, updatedAt);
  assert.deepEqual(f.store.getSignal('changed', 'scope-a'), { key: 'changed', at, payload: { ...payload, meeting: enriched }, disposition, updatedAt });
  assert.equal(f.store.getSignalMaterial('changed', 'scope-a', 'old-reference'), null);
  assert.deepEqual(f.store.getSignal('other', 'scope-b'), other);
  assert.deepEqual(f.store.getSignalMaterial('other', 'scope-b', 'other-reference'), ready);
  f.store.updateSignalMeeting('no-meeting', enriched, updatedAt);
  assert.deepEqual(f.store.getSignal('no-meeting')?.payload, { summary: 'Keep this too', extra: { keep: true }, meeting: enriched });
  f.store.saveSignalMaterial('changed', 'scope-a', 'new-reference', ready);
  f.store.updateSignalMeeting('changed', { ...enriched }, '2026-11-03T00:00:00.000Z');
  assert.equal(f.store.getSignal('changed')?.updatedAt, updatedAt);
  assert.deepEqual(f.store.getSignalMaterial('changed', 'scope-a', 'new-reference'), ready);
});

test('meeting updates roll payload and timestamp back when cache invalidation fails', t => {
  const f = fixture(t);
  const meeting: MeetingReference = { event: 'ended', meetingId: 'meeting-a' };
  f.store.insertSignal('signal', { summary: 'Keep', meeting }, { outcome: 'router', matterIds: ['matter'] }, at);
  f.store.saveSignalMaterial('signal', 'scope', 'reference', ready);
  const before = f.store.getSignal('signal', 'scope');
  f.database(db => db.exec(`CREATE TRIGGER fail_material_invalidation BEFORE DELETE ON signal_materials
    WHEN OLD.signal_key = 'signal' BEGIN SELECT RAISE(ABORT, 'fixture invalidation failed'); END;`));
  assert.throws(() => f.store.updateSignalMeeting('signal', { ...meeting, noteId: 'note-a' }, '2026-11-02T00:00:00.000Z'), /fixture invalidation failed/);
  assert.deepEqual(f.store.getSignal('signal', 'scope'), before);
  assert.deepEqual(f.store.getSignalMaterial('signal', 'scope', 'reference'), ready);
  f.database(db => db.exec('DROP TRIGGER fail_material_invalidation'));
  f.store.updateSignalMeeting('signal', { ...meeting, noteId: 'note-a' }, '2026-11-02T00:00:00.000Z');
  assert.equal(f.store.getSignalMaterial('signal', 'scope', 'reference'), null);
  assert.equal(f.store.getSignal('signal')?.updatedAt, '2026-11-02T00:00:00.000Z');
});

test('pruning cascades to material bodies by original signal time and keeps the exact ninety-day boundary', t => {
  const f = fixture(t);
  const now = new Date(at);
  const boundary = now.getTime() - 90 * 86400000;
  for (const [key, offset] of [['old', -1], ['boundary', 0], ['newer', 1]] as const) {
    f.store.insertSignal(key, { summary: key }, { outcome: 'router' }, new Date(boundary + offset).toISOString());
    f.store.saveSignalMaterial(key, `scope-${key}`, `reference-${key}`, ready);
  }
  f.store.updateSignalDisposition('old', { outcome: 'delivered' }, at);
  const survivors = f.store.listSignals().filter(row => row.key !== 'old');
  f.reopen();
  f.store.pruneSignals(now); f.store.pruneSignals(now);
  assert.equal(f.store.getSignal('old'), null);
  assert.equal(f.store.getSignalMaterial('old', 'scope-old', 'reference-old'), null);
  assert.deepEqual(f.store.listSignals(), survivors);
  for (const key of ['boundary', 'newer']) assert.deepEqual(f.store.getSignalMaterial(key, `scope-${key}`, `reference-${key}`), ready);
  f.database(db => {
    assert.equal((db.prepare('SELECT COUNT(*) AS count FROM signal_materials').get() as { count: number }).count, 2);
    assert.deepEqual(db.prepare('PRAGMA foreign_key_check').all(), []);
  });
  f.store.insertSignal('old', { summary: 'New signal reusing the key' }, { outcome: 'router' }, at);
  f.reopen();
  assert.equal(f.store.getSignal('old', 'scope-old')?.material, undefined);
  assert.equal(f.store.getSignalMaterial('old', 'scope-old', 'reference-old'), null);
});

test('meetingSignals matches only explicit exact string identities, excludes filtered rows and sorts without material', t => {
  const f = fixture(t);
  const meeting = { event: 'ended', meetingId: 'meeting-a', title: 'Same title', noteDocToken: 'same-document' };
  f.store.insertSignal('a-router', { meeting }, { outcome: 'router', pending: true }, at);
  f.store.insertSignal('b-delivered', { meeting: { ...meeting, event: 'note-generated' } }, { outcome: 'delivered' }, at);
  f.store.insertSignal('older', { meeting: { ...meeting, event: 'minutes-generated' } }, { outcome: 'router' }, '2026-10-31T00:00:00.000Z');
  f.store.insertSignal('filtered', { meeting }, { outcome: 'filtered', reason: 'outside-watch-scope' }, '2026-11-02T00:00:00.000Z');
  const misses = [
    { meeting: { ...meeting, meetingId: 'meeting-a-suffix' } },
    { meeting: { ...meeting, meetingId: 'MEETING-A' } },
    { meeting: { event: 'ended', title: 'Same title', noteDocToken: 'same-document' } },
    { meeting: { event: 'ended', title: 'meeting-a', noteId: 'meeting-a', noteDocToken: 'meeting-a', minuteToken: 'meeting-a', calendarEventId: 'meeting-a', meetingNumber: 'meeting-a' } },
    { source: { type: 'lark-meeting', ref: 'meeting-a' } },
    { meetingId: 'meeting-a' }, { meeting: { meetingId: 123 } }, { meeting: { meetingId: null } }, {},
  ];
  misses.forEach((payload, index) => f.store.insertSignal(`miss-${index}`, payload, { outcome: 'router' }, at));
  f.store.saveSignalMaterial('a-router', 'scope', 'reference', ready);
  assert.deepEqual(f.store.meetingSignals('meeting-a'), ['a-router', 'b-delivered', 'older'].map(key => f.store.getSignal(key)));
  assert.deepEqual(f.store.meetingSignals('meeting-a '), []);
  assert.deepEqual(f.store.meetingSignals('123'), [], 'numeric IDs must not match string IDs');
  assert.deepEqual(f.store.meetingSignals('absent'), []);
  assert.deepEqual(f.store.meetingSignals('MEETING-A').map(row => row.key), ['miss-1']);
});

test('associateSignal propagates across non-filtered meeting events, deduplicates and preserves each disposition', t => {
  const f = fixture(t);
  f.store.createTodo('target', 'Target matter', null);
  f.store.createTodo('second', 'Second matter', null);
  const meeting: MeetingReference = { event: 'ended', meetingId: 'meeting-a', title: 'Same title', noteDocToken: 'same-document' };
  const rows: Array<{ key: string; meeting: MeetingReference; disposition: SignalDisposition }> = [
    { key: 'ended', meeting, disposition: { outcome: 'router', pending: true, reason: 'awaiting-router', matterIds: ['existing'], threadId: 'router-thread' } },
    { key: 'note', meeting: { ...meeting, event: 'note-generated' }, disposition: { outcome: 'delivered', matterIds: ['other'], threadIds: ['worker'], deliveries: [{ matterId: 'other', threadId: 'worker' }] } },
    { key: 'minutes', meeting: { ...meeting, event: 'minutes-generated' }, disposition: { outcome: 'router', pending: false } },
    { key: 'filtered', meeting, disposition: { outcome: 'filtered', reason: 'outside-watch-scope' } },
    { key: 'different', meeting: { ...meeting, meetingId: 'meeting-b' }, disposition: { outcome: 'router' } },
    { key: 'unidentified', meeting: { event: 'ended', title: meeting.title, noteDocToken: meeting.noteDocToken }, disposition: { outcome: 'router' } },
  ];
  for (const row of rows) f.store.insertSignal(row.key, { summary: row.key, meeting: row.meeting }, row.disposition, at);
  f.store.saveSignalMaterial('note', 'scope', 'reference', ready);
  const before = f.store.listSignals();
  const matters = f.store.listTodos();
  f.store.associateSignal('ended', 'target');
  f.store.associateSignal('note', 'target');
  for (const row of rows) {
    const after = f.store.getSignal(row.key)!;
    if (['ended', 'note', 'minutes'].includes(row.key)) {
      assert.deepEqual(after.disposition, { ...row.disposition, matterIds: [...(row.disposition.matterIds ?? []), 'target'] });
      assert.deepEqual(after.payload, { summary: row.key, meeting: row.meeting });
      assert.equal(after.at, at);
    } else assert.deepEqual(after, before.find(record => record.key === row.key));
  }
  f.store.insertSignal('late', { meeting: { ...meeting, event: 'note-generated', noteId: 'late-note' } }, { outcome: 'router' }, at);
  f.store.associateSignal('late', 'target');
  f.store.associateSignal('minutes', 'second');
  for (const matterId of ['target', 'second']) assert.deepEqual(f.store.listSignals({ matterId }).map(row => row.key), ['ended', 'late', 'minutes', 'note']);
  assert.deepEqual(f.store.getSignal('ended')?.disposition.matterIds, ['existing', 'target', 'second']);
  assert.deepEqual(f.store.getSignal('late')?.disposition.matterIds, ['target', 'second']);
  assert.deepEqual(f.store.getSignalMaterial('note', 'scope', 'reference'), ready);
  assert.deepEqual(f.store.getSignal('note', 'scope')?.material, ready.material);
  assert.deepEqual(f.store.listTodos(), matters, 'association does not edit the matters themselves');
});

test('associateSignal without a meeting identity associates only the selected message or meeting', t => {
  const f = fixture(t);
  f.store.createTodo('target', 'Target matter', null);
  const message = { summary: 'Message', source: { type: 'lark-message', ref: 'same-reference' } };
  const meeting = { meeting: { event: 'ended', title: 'Same title', noteDocToken: 'same-document' } };
  for (const [key, payload] of [['message', message], ['other-message', message], ['meeting', meeting], ['other-meeting', meeting]] as const)
    f.store.insertSignal(key, payload, { outcome: 'router' }, at);
  const otherMessage = f.store.getSignal('other-message');
  const otherMeeting = f.store.getSignal('other-meeting');
  f.store.associateSignal('message', 'target');
  f.store.associateSignal('meeting', 'target');
  assert.deepEqual(f.store.listSignals({ matterId: 'target' }).map(row => row.key), ['meeting', 'message']);
  assert.deepEqual(f.store.getSignal('other-message'), otherMessage);
  assert.deepEqual(f.store.getSignal('other-meeting'), otherMeeting);
});

test('associateSignal reports missing signal or matter as 404 without changing related records or caches', t => {
  const f = fixture(t);
  f.store.createTodo('target', 'Target matter', null);
  for (const key of ['signal', 'related']) f.store.insertSignal(key, { meeting: { event: 'ended', meetingId: 'meeting-a' } }, { outcome: 'router', pending: true }, at);
  f.store.saveSignalMaterial('signal', 'scope', 'reference', ready);
  const before = f.store.listSignals({ materialScope: 'scope' });
  assert.throws(() => f.store.associateSignal('missing', 'target'), { status: 404, message: 'signal not found' });
  assert.throws(() => f.store.associateSignal('signal', 'missing'), { status: 404, message: 'todo not found' });
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope' }), before);
  assert.equal(f.store.getSignal('missing'), null);
  assert.equal(f.store.getTodo('missing'), null);
  assert.deepEqual(f.store.getSignalMaterial('signal', 'scope', 'reference'), ready);
});

test('associateSignal rolls earlier sibling updates back if a later disposition write fails', t => {
  const f = fixture(t);
  f.store.createTodo('target', 'Target matter', null);
  for (const key of ['a-seed', 'b-related', 'c-related'])
    f.store.insertSignal(key, { meeting: { event: 'ended', meetingId: 'meeting-a' } }, { outcome: 'router', pending: true, matterIds: ['existing'] }, at);
  f.store.saveSignalMaterial('a-seed', 'scope', 'reference', ready);
  const before = f.store.listSignals({ materialScope: 'scope' });
  const matter = f.store.getTodo('target');
  f.database(db => db.exec(`CREATE TRIGGER fail_signal_association BEFORE UPDATE OF disposition ON signals
    WHEN OLD.key = 'b-related' BEGIN SELECT RAISE(ABORT, 'fixture association failed'); END;`));
  assert.throws(() => f.store.associateSignal('a-seed', 'target'), /fixture association failed/);
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope' }), before, 'including timestamps on the already-updated seed');
  assert.deepEqual(f.store.getTodo('target'), matter);
  assert.deepEqual(f.store.getSignalMaterial('a-seed', 'scope', 'reference'), ready);
  f.database(db => db.exec('DROP TRIGGER fail_signal_association'));
  f.store.associateSignal('a-seed', 'target');
  for (const row of f.store.meetingSignals('meeting-a')) assert.deepEqual(row.disposition.matterIds, ['existing', 'target']);
});

test('associateSignal participates in outer matter transactions, rolling back and committing all related provenance', t => {
  const f = fixture(t);
  for (const key of ['signal', 'related']) f.store.insertSignal(key, { meeting: { event: 'ended', meetingId: 'meeting-a' } }, { outcome: 'router' }, at);
  f.store.saveSignalMaterial('signal', 'scope', 'reference', ready);
  const before = f.store.listSignals({ materialScope: 'scope' });
  assert.throws(() => f.store.transaction(() => {
    f.store.createTodo('new-matter', 'New matter', null);
    f.store.associateSignal('signal', 'new-matter');
    assert.deepEqual(f.store.listSignals({ matterId: 'new-matter' }).map(row => row.key), ['related', 'signal']);
    throw new Error('fixture outer matter write failed');
  }), /fixture outer matter write failed/);
  assert.equal(f.store.getTodo('new-matter'), null);
  assert.deepEqual(f.store.listSignals({ materialScope: 'scope' }), before);
  assert.deepEqual(f.store.getSignalMaterial('signal', 'scope', 'reference'), ready);
  f.store.transaction(() => {
    f.store.createTodo('new-matter', 'New matter', null);
    f.store.associateSignal('signal', 'new-matter');
  });
  f.reopen();
  assert.ok(f.store.getTodo('new-matter'));
  assert.deepEqual(f.store.listSignals({ matterId: 'new-matter' }).map(row => row.key), ['related', 'signal']);
  assert.deepEqual(f.store.getSignalMaterial('signal', 'scope', 'reference'), ready);
});

test('disposition updates preserve and union matterIds while replacing delivery fields, pending status and timestamps', t => {
  const f = fixture(t);
  const payload = { summary: 'Delivery in flight' };
  f.store.insertSignal('signal', payload, { outcome: 'router', pending: true, reason: 'awaiting-router', threadId: 'router', matterIds: ['manual', 'existing', 'manual'] }, at);
  f.store.saveSignalMaterial('signal', 'scope', 'reference', ready);
  const updates: Array<{ disposition: SignalDisposition; matterIds: string[] }> = [
    { disposition: { outcome: 'delivered', threadId: 'worker' }, matterIds: ['manual', 'existing'] },
    { disposition: { outcome: 'delivered', threadIds: ['worker', 'other-worker'], deliveries: [{ matterId: 'automatic', threadId: 'worker' }], matterIds: ['automatic', 'manual', 'automatic'] }, matterIds: ['manual', 'existing', 'automatic'] },
    { disposition: { outcome: 'router', pending: false, matterIds: [] }, matterIds: ['manual', 'existing', 'automatic'] },
    { disposition: { outcome: 'filtered', reason: 'already-handled' }, matterIds: ['manual', 'existing', 'automatic'] },
  ];
  for (const [index, update] of updates.entries()) {
    const updatedAt = new Date(Date.parse(at) + (index + 1) * 1000).toISOString();
    f.store.updateSignalDisposition('signal', update.disposition, updatedAt);
    assert.deepEqual(f.store.getSignal('signal', 'scope'), {
      key: 'signal', at, payload, disposition: { ...update.disposition, matterIds: update.matterIds }, updatedAt, material: ready.material,
    });
    for (const matterId of update.matterIds) assert.deepEqual(f.store.listSignals({ matterId }).map(row => row.key), ['signal']);
    assert.deepEqual(f.store.listSignals({ pending: true }), []);
    assert.deepEqual(f.store.getSignalMaterial('signal', 'scope', 'reference'), ready);
  }
  assert.deepEqual(f.store.listSignals({ matterId: 'auto' }), [], 'matter IDs are exact, not substrings');
  const before = f.store.getSignal('signal', 'scope');
  f.store.updateSignalDisposition('missing', { outcome: 'delivered', matterIds: ['manual'] }, at);
  assert.equal(f.store.getSignal('missing'), null);
  f.reopen();
  assert.deepEqual(f.store.getSignal('signal', 'scope'), before);
});

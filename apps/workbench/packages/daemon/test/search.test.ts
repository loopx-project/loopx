import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { Store } from '../src/store.js';

function open() {
  const root = mkdtempSync(join(tmpdir(), 'calli-search-'));
  const store = new Store(root);
  const create = (id: string, title = id) => store.createThread({ id, cwd: '/project', agent: 'test', title, permissionMode: 'auto_edit', model: null, effort: null });
  /** A turn as it is really logged: the answer in pieces, with work in between. */
  const turn = (thread: string, id: string, asked: string, answer: string, extra: Record<string, unknown> = {}) => {
    store.appendEvent(thread, id, 'turn.started', { text: asked, ...extra });
    const pieces = answer.match(/[\s\S]{1,3}/g) ?? [];
    pieces.forEach((text, i) => {
      if (i === 2) store.appendEvent(thread, id, 'tool.start', { title: '额度看板 in a tool title is not searched' });
      store.appendEvent(thread, id, 'message.delta', { text });
    });
    store.appendEvent(thread, id, 'turn.done', {});
  };
  return { root, store, create, turn };
}

test('words are found in what was asked and in an answer that streamed in pieces', () => {
  const h = open();
  try {
    h.create('a', '线程甲');
    h.create('b', '线程乙');
    h.turn('a', 't1', '额度看板怎么排序', '当前智能体排在最前面，其余保持原来的顺序。');
    h.turn('b', 't2', '改一下 README', 'Updated the Quota Board section.');
    const asked = h.store.searchText('额度看板');
    assert.deepEqual(asked.map((x) => [x.threadId, x.turnId, x.role, x.title]), [['a', 't1', 'user', '线程甲']]);
    // The phrase straddles several stored pieces; it is found whole, with the words around it.
    const [said] = h.store.searchText('保持原来');
    assert.deepEqual([said.threadId, said.turnId, said.role], ['a', 't1', 'assistant']);
    assert.match(said.snippet, /其余保持原来的顺序/);
    assert.equal(h.store.searchText('quota board')[0].threadId, 'b', 'Latin letters match whatever their case');
    // Every word has to be there, in any order.
    assert.equal(h.store.searchText('顺序 智能体').length, 1);
    assert.equal(h.store.searchText('顺序 README').length, 0);
    assert.deepEqual(h.store.searchText('a'), [], 'one character is not a search');
    assert.deepEqual(h.store.searchText('   '), []);
  } finally { h.store.close(); rmSync(h.root, { recursive: true, force: true }); }
});

test('archived threads are found and marked; hub-made turns and deleted threads are not; three hits a thread', () => {
  const h = open();
  try {
    h.create('old');
    h.create('team');
    h.create('long');
    h.turn('old', 't1', '迁移数据库', '完成。');
    h.store.updateThread('old', { archivedAt: new Date().toISOString() });
    h.turn('team', 't1', '[协作消息] 迁移数据库', '成员的回答里也提到迁移数据库。', { coordination: { from: 'x' } });
    for (let i = 0; i < 5; i++) h.turn('long', `t${i}`, `第 ${i} 次迁移数据库`, '好。');
    const hits = h.store.searchText('迁移数据库');
    assert.equal(hits.find((x) => x.threadId === 'old')?.archived, true);
    assert.deepEqual(hits.filter((x) => x.threadId === 'team').map((x) => x.role), ['assistant']);
    assert.equal(hits.filter((x) => x.threadId === 'long').length, 3);
    assert.ok(hits.every((x, i) => i === 0 || hits[i - 1].ts >= x.ts), 'newest first');
    h.store.deleteThread('long');
    assert.equal(h.store.searchText('迁移数据库').some((x) => x.threadId === 'long'), false);
  } finally { h.store.close(); rmSync(h.root, { recursive: true, force: true }); }
});

test('a database from before search is read through once, and a copied thread is searchable', () => {
  const h = open();
  let store = h.store;
  try {
    h.create('a');
    h.turn('a', 't1', '部署脚本在哪', '在 scripts 目录下。');
    store.close();
    const db = new DatabaseSync(join(h.root, 'hub.db'));
    db.exec('DROP TABLE turn_text');
    db.close();
    store = new Store(h.root);
    assert.deepEqual(store.searchText('scripts 目录').map((x) => [x.threadId, x.role]), [['a', 'assistant']]);
    assert.equal(store.searchText('部署脚本').length, 1);
    store.createThread({ id: 'copy', cwd: '/project', agent: 'test', title: 'copy', permissionMode: 'auto_edit', model: null, effort: null });
    store.copyEvents('a', 'copy');
    assert.deepEqual(store.searchText('部署脚本').map((x) => x.threadId).sort(), ['a', 'copy']);
  } finally { store.close(); rmSync(h.root, { recursive: true, force: true }); }
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/store.js';

test('voice-only history pages and reconnect retain speaker text without fake tasks', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-voice-history-'));
  const store = new Store(dir);
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  store.createThread({ id: 'voice', agent: 'provider', cwd: dir, title: 'voice',
    agentSessionId: null, permissionMode: 'default', model: null, effort: null });
  for (let i = 0; i < 20; i++) store.appendEvent('voice', null, 'voice.message', {
    itemId: `speech-${i}`, role: i % 2 ? 'assistant' : 'user', text: `message ${i}`,
    final: true, afterSeq: i, at: new Date().toISOString(),
  });
  const page = await store.history.page('voice');
  assert.equal(page.events.length, 8);
  assert.equal(page.hasOlder, true);
  assert.equal(page.events[0].data.itemId, 'speech-12');
  const older = await store.history.page('voice', page.nextCursor!);
  assert.equal(older.events.length, 8);
  assert.equal(older.events[0].data.itemId, 'speech-4');
  const reconnect = await store.history.since('voice', page.snapshotSeq - 2);
  assert.equal(reconnect.events.length, 2);
  assert.deepEqual(reconnect.turns, []);
  assert.equal(store.openTurn('voice'), null);
});

test('speech during a background task never splits its work projection or hides its final answer', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-voice-history-'));
  const store = new Store(dir);
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  store.createThread({ id: 'voice', agent: 'provider', cwd: dir, title: 'voice',
    agentSessionId: null, permissionMode: 'default', model: null, effort: null });
  store.appendEvent('voice', 'task', 'turn.started', { text: '', source: 'voice' });
  store.appendEvent('voice', 'task', 'tool.start', { id: 'search', title: 'Search' });
  for (let i = 0; i < 12; i++) store.appendEvent('voice', null, 'voice.message', {
    itemId: `speech-${i}`, role: 'user', text: `message ${i}`, final: true, afterSeq: i,
  });
  store.appendEvent('voice', 'task', 'tool.end', { id: 'search', status: 'completed' });
  store.appendEvent('voice', 'task', 'message.delta', { text: '**News**\n\nFinal answer' });
  store.appendEvent('voice', 'task', 'turn.done', {});
  const page = await store.history.page('voice');
  assert.equal(page.hasOlder, false);
  assert.equal(page.events.filter(e => e.type === 'history.work').length, 1);
  assert.equal(page.events.find(e => e.type === 'history.work')?.data.count, 1);
  assert.equal(page.events.filter(e => e.type === 'voice.message').length, 12);
  assert.equal(page.events.find(e => e.type === 'message.delta')?.data.text, '**News**\n\nFinal answer');
});


test('closed call history is metadata-only until scoped, paged transcript expansion', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-voice-fold-'));
  const store = new Store(dir);
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  store.createThread({ id: 'voice', agent: 'provider', cwd: dir, title: 'voice', agentSessionId: null, permissionMode: 'default', model: null, effort: null });
  for (let i = 0; i < 70; i++) store.appendEvent('voice', null, 'voice.message', { sessionId: 'call', itemId: `call:${i}`, role: 'user', text: 'private body', final: true });
  store.appendEvent('voice', null, 'voice.closed', { sessionId: 'call', startSeq: 1, endSeq: 70, count: 70, startedAt: '2026-01-01', endedAt: '2026-01-02' });
  for (const read of [await store.history.page('voice'), await store.history.since('voice', 0)]) {
    assert.equal(read.events.filter(e => e.type === 'voice.message').length, 0);
    assert.equal(read.events.filter(e => e.type === 'voice.closed').length, 1);
    assert.ok(!JSON.stringify(read).includes('private body'));
  }
  const first = store.history.voiceText('voice', 'call');
  assert.equal(first.events.length, 64);
  assert.equal(store.history.voiceText('voice', 'call', first.nextCursor!).events.length, 6);
  assert.throws(() => store.history.voiceText('another-thread', 'call'), /not found/);
});


test('summary context remains in the source log but is omitted from the history read model', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-voice-summary-view-'));
  const store = new Store(dir);
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  store.createThread({ id: 'voice', agent: 'provider', cwd: dir, title: 'voice', agentSessionId: null, permissionMode: 'default', model: null, effort: null });
  store.appendEvent('voice', 'summary', 'turn.started', { voiceSummary: 'call', text: 'private context', displayText: 'private context' });
  store.appendEvent('voice', 'summary', 'message.delta', { text: 'Written result' });
  store.appendEvent('voice', 'summary', 'turn.done', {});
  const page = await store.history.page('voice');
  assert.ok(!JSON.stringify(page).includes('private context'));
  assert.ok(JSON.stringify(page).includes('Written result'));
  assert.equal(store.listEventsOfType('voice', 'turn.started')[0].data.text, 'private context');
});

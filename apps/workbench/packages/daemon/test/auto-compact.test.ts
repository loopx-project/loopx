import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { Workdirs } from '../src/workdirs.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { chunkText, transcript } from '../src/auto-compact.js';
import { compressPrompt, mergePrompt, SUMMARY_SECTIONS } from '../src/compress-prompts.js';
import { HttpError } from '../src/errors.js';
import type { StoredEvent } from '../src/events.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const wait = async (check: () => unknown, timeout = 15000) => {
  const end = Date.now() + timeout;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};

const turn = (turnId: string, text: string, answer: string): StoredEvent[] => [
  { threadId: 't', seq: 1, turnId, type: 'turn.started', ts: new Date().toISOString(), data: { text } },
  { threadId: 't', seq: 2, turnId, type: 'message.delta', ts: new Date().toISOString(), data: { text: answer } },
  { threadId: 't', seq: 3, turnId, type: 'turn.done', ts: new Date().toISOString(), data: {} },
];

test('transcript keeps asks and answers, drops handover and control turns', () => {
  const events = [
    ...turn('a', '把视频渲染出来', '渲染完成'),
    { threadId: 't', seq: 4, turnId: 'b', type: 'turn.started', ts: new Date().toISOString(), data: { text: '请写一份交接摘要', handover: 'agy2' } },
    { threadId: 't', seq: 5, turnId: 'c', type: 'turn.started', ts: new Date().toISOString(), data: { text: '/progress', control: true } },
    ...turn('d', '继续', '好的'),
  ];
  const text = transcript(events);
  assert.ok(text.includes('把视频渲染出来') && text.includes('渲染完成'));
  assert.ok(text.includes('继续') && text.includes('好的'));
  assert.ok(!text.includes('交接摘要') && !text.includes('/progress'));
});

test('chunkText slices with overlap and leaves short text whole', () => {
  assert.deepEqual(chunkText('short', 1000), ['short']);
  const chunks = chunkText('x'.repeat(2500), 1000);
  assert.equal(chunks.length, 3);
  assert.ok(chunks.every(c => c.length <= 1000));
  assert.equal(chunks[0].length + (chunks[1].length - 100) + (chunks[2].length - 100), 2500);
});

async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-autocompact-'));
  const store = new Store(dir);
  const adapters = new Map<string, AgentAdapter>();
  for (const id of ['agy', 'agy2']) {
    writeFileSync(join(dir, id + '.json'), JSON.stringify({ 'Gemini Models': 10 }));
    adapters.set(id, new AgyAdapter([process.execPath, join(fixtures, 'fake-agy.mjs')], { FAKE_HANDOVER_QUOTA: join(dir, id + '.json') }));
  }
  adapters.set('offline', new AgyAdapter(['nonexistent-compression-fixture']));
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'dirs.json'), join(dir, 'scratch')), new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: 'agy' })));
  const thread = await manager.createThread({ agent: 'agy', cwd: dir, model: 'gemini-pro' });
  const compact = (over: Record<string, unknown> = {}) => manager.autoCompact.configure({ enabled: true, threshold: 85, agents: [{ agent: 'agy2' }], minChars: 100, ...over });
  return { dir, store, manager, thread, compact,
    async close() { await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('configure validates and orders the compaction before the handover', async () => {
  const h = await setup();
  try {
    assert.equal(h.manager.autoCompact.settings().enabled, false);
    assert.throws(() => h.manager.autoCompact.configure({ enabled: true, threshold: 'x' }), HttpError);
    assert.throws(() => h.manager.autoCompact.configure({ enabled: true, threshold: 85, agents: [{ agent: 'nope' }] }), HttpError);
    assert.throws(() => h.compact({ agents: [{ agent: 'agy2' }, { agent: 'agy2' }] }), HttpError, 'a repeat is refused');
    assert.throws(() => h.compact({ agents: [{ agent: 'agy2', model: '' }] }), HttpError);
    assert.deepEqual(h.compact().agents, [{ agent: 'agy2' }]);
    assert.deepEqual(h.compact({ agents: [{ agent: 'agy2', model: 'fast' }, { agent: 'agy' }] }).agents, [{ agent: 'agy2', model: 'fast' }, { agent: 'agy' }]);
    h.manager.autoHandover.configure({ enabled: true, threshold: 90, orders: {} });
    assert.throws(() => h.compact({ threshold: 90 }), /before the handover/);
    // Native mode: no compression agent, the thread itself runs /compact.
    assert.deepEqual(h.compact({ agents: [], threshold: 85 }).agents, []);
  } finally { await h.close(); }
});

test('a near-limit turn continues in a fresh session of the same account, from a compressed brief', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 95, orders: {} });
    h.compact();
    // The account crosses the compaction threshold before the turn ends.
    writeFileSync(join(h.dir, 'agy.json'), JSON.stringify({ 'Gemini Models': 90 }));
    await h.manager.startTurn(h.thread.id, { text: '把视频渲染出来，然后用飞书发给我，注意音画同步' });
    await wait(() => h.manager.threadView(h.thread.id).status !== 'running');
    await wait(() => h.manager.autoCompact.status().last);
    const last = h.manager.autoCompact.status().last!;
    assert.equal(last.native, false);
    assert.ok(last.successorId && last.successorId !== h.thread.id);
    const successor = h.manager.threadView(last.successorId!);
    assert.equal(successor.agent, 'agy');
    assert.ok(successor.handover, 'the successor continues the source thread');
    // The brief says what it is, and carries the compressor's notes plus the log facts.
    const first = h.store.listEvents(last.successorId!, 0, -1).find(e => e.type === 'turn.started')!;
    const brief = String(first.data.text);
    assert.ok(brief.includes('上下文压缩'));
    assert.ok(brief.includes('echo:')); // the fake compressor's answer
    // One compaction per session: a second settled run on the source starts nothing.
    writeFileSync(join(h.dir, 'agy.json'), JSON.stringify({ 'Gemini Models': 92 }));
    assert.equal(await h.manager.autoCompact.settled(h.thread.id, (h.store.listTurns(h.thread.id).at(-1)?.turnId)!), false);
  } finally { await h.close(); }
});

test('a small transcript or a healthy quota compacts nothing', async () => {
  const h = await setup();
  try {
    h.compact({ minChars: 240_000 }); // the default: the tiny test transcript is far below it
    writeFileSync(join(h.dir, 'agy.json'), JSON.stringify({ 'Gemini Models': 90 }));
    await h.manager.startTurn(h.thread.id, { text: '你好' });
    await wait(() => h.manager.threadView(h.thread.id).status !== 'running');
    const turnId = h.store.listTurns(h.thread.id).at(-1)!.turnId;
    assert.equal(await h.manager.autoCompact.settled(h.thread.id, turnId), false);
    assert.equal(h.manager.autoCompact.status().skipped?.reason, 'transcript-too-small');
  } finally { await h.close(); }
});

test('a relayed thread compacts the whole chain, not its own small tail', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 95, orders: {} });
    h.compact(); // minChars 100
    // The source generation carries the bulk of the work...
    await h.manager.startTurn(h.thread.id, { text: '把视频渲染出来，然后用飞书发给我，注意音画同步，别丢字幕' });
    await wait(() => h.manager.threadView(h.thread.id).status !== 'running');
    // ...the work relays, and the successor's own generation is tiny.
    const successor = await h.manager.createHandoverThread(h.thread.id, 'agy');
    writeFileSync(join(h.dir, 'agy.json'), JSON.stringify({ 'Gemini Models': 90 }));
    await h.manager.startTurn(successor.id, { text: '继续' });
    await wait(() => h.manager.threadView(successor.id).status !== 'running');
    await wait(() => h.manager.autoCompact.status().last);
    const last = h.manager.autoCompact.status().last!;
    assert.ok(last.successorId, 'the inherited chain is long enough to compact even though this generation is not');
    const first = h.store.listEvents(last.successorId!, 0, -1).find(e => e.type === 'turn.started')!;
    assert.ok(String(first.data.text).includes('把视频渲染出来'), 'the brief builds on the whole chain');
  } finally { await h.close(); }
});

test('a failing quota probe still compacts on the last good reading', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: true, threshold: 95, orders: {} });
    h.compact();
    // A first turn puts the thread on the watch; the background watch then fills the last-good
    // reading at 90%, and the probe breaks before the turn that should compact.
    await h.manager.startTurn(h.thread.id, { text: '你好' });
    await wait(() => h.manager.threadView(h.thread.id).status !== 'running');
    // The handover decision probes the account after the turn; while it runs the thread is held
    // and a fresh send is refused. Let that window close before racing the second turn in.
    await wait(() => !h.manager.autoHandover.held(h.thread.id));
    writeFileSync(join(h.dir, 'agy.json'), JSON.stringify({ 'Gemini Models': 90 }));
    await h.manager.refreshQuotas();
    writeFileSync(join(h.dir, 'agy.json'), 'not json');
    await h.manager.startTurn(h.thread.id, { text: '把视频渲染出来，然后用飞书发给我，注意音画同步' });
    await wait(() => h.manager.threadView(h.thread.id).status !== 'running');
    await wait(() => h.manager.autoCompact.status().last);
    const last = h.manager.autoCompact.status().last!;
    assert.ok(last.successorId, 'the cached reading alone moved the compaction');
  } finally { await h.close(); }
});

test('a handover the source cannot summarize is summarized by the compression agent, then falls back to the log', async () => {
  const h = await setup();
  try {
    await h.manager.startTurn(h.thread.id, { text: '把视频渲染出来，然后用飞书发给我，注意音画同步' });
    await wait(() => h.manager.threadView(h.thread.id).status !== 'running');
    // No compression agent: the brief is the one assembled from the log, as before.
    h.compact({ agents: [] });
    const plain = await h.manager.handoverSummary(h.thread.id, null, { compress: true });
    assert.ok('summary' in plain && !plain.summary.includes('## 进度（由 '));
    // The summary a view asks for is never held up by the compression agent.
    h.compact();
    const view = await h.manager.handoverSummary(h.thread.id, null);
    assert.ok('summary' in view && !view.summary.includes('## 进度（由 '));
    // With one, its notes (the fake echoes its prompt) sit between the pointer to the source and the log facts.
    const rich = await h.manager.handoverSummary(h.thread.id, null, { compress: true });
    assert.ok('summary' in rich);
    const text = rich.summary;
    assert.ok(text.includes('## 进度（由 '));
    assert.ok(text.includes('echo:') && text.includes('把视频渲染出来'), 'the compressor read the record');
    assert.ok(text.indexOf('# 接手') < text.indexOf('## 进度（由 ') && text.indexOf('## 进度（由 ') < text.indexOf('hub 从记录整理，可能不全'));
  } finally { await h.close(); }
});

test('settings saved with one agent and its model read as a list of one', async () => {
  const h = await setup();
  try {
    h.store.saveHubSetting('autocompact.settings', { enabled: true, threshold: 85, agent: 'agy2', model: 'fast' });
    const settings = h.manager.autoCompact.settings();
    assert.deepEqual(settings.agents, [{ agent: 'agy2', model: 'fast' }]);
    assert.ok(!('agent' in settings) && !('model' in settings));
    // An older client still saving the single agent lands on the same list.
    const saved = h.manager.autoCompact.configure({ enabled: true, threshold: 85, agent: 'agy2' });
    assert.deepEqual(saved.agents, [{ agent: 'agy2' }]);
    assert.ok(!('agent' in h.store.hubSetting<Record<string, unknown>>('autocompact.settings', {})));
  } finally { await h.close(); }
});

test('the next compression agent takes over when one is unavailable, and none left means the log brief', async () => {
  const h = await setup();
  try {
    await h.manager.startTurn(h.thread.id, { text: '把视频渲染出来，然后用飞书发给我，注意音画同步' });
    await wait(() => h.manager.threadView(h.thread.id).status !== 'running');
    h.compact({ agents: [{ agent: 'offline' }, { agent: 'agy2' }] });
    const second = await h.manager.autoCompact.summarizeForHandover(h.thread.id);
    assert.ok(second?.includes('## 进度（由 ') && second.includes('echo:'), 'the second agent wrote it');
    h.compact({ agents: [{ agent: 'offline' }] });
    assert.equal(await h.manager.autoCompact.summarizeForHandover(h.thread.id), null);
  } finally { await h.close(); }
});

test('the prompts name every section and carry the record', () => {
  const one = compressPrompt(1, 2, '<<record>>');
  const merged = mergePrompt(['a-note', 'b-note']);
  for (const section of SUMMARY_SECTIONS) assert.ok(one.includes(`## ${section}`) && merged.includes(`## ${section}`), section);
  assert.ok(one.includes('第 1/2 段') && one.includes('<<record>>'));
  assert.ok(merged.includes('a-note') && merged.includes('b-note'));
});

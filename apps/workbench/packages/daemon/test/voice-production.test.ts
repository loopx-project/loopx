import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { AttachmentStore } from '../src/attachments.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import type { StoredEvent } from '../src/events.js';
import type { VoiceSignal } from '../src/voice-sessions.js';
import { turnAnswer } from '../src/coordination.js';

async function until(check: () => boolean) {
  const end = Date.now() + 5000;
  while (!check()) {
    if (Date.now() > end) throw new Error('fixture condition timed out');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
async function setup(t: TestContext, env: Record<string, string> = {}, permissionMode?: 'ask' | 'auto_edit' | 'full_access') {
  const directory = mkdtempSync(join(tmpdir(), 'calli-voice-production-'));
  const log = join(directory, 'rpc.jsonl');
  const adapter = new CodexAppServerAdapter([process.execPath, fileURLToPath(new URL('./fixtures/fake-codex.mjs', import.meta.url))], {
    ...env, FAKE_CODEX_RPC_LOG: log,
  });
  const store = new Store(directory);
  const adapters = new Map([['provider', adapter]]);
  const manager = new ThreadManager(store, adapters, [directory], new AttachmentStore(directory));
  const events: StoredEvent[] = [];
  manager.subscribe(event => events.push(event));
  const thread = await manager.createThread({ agent: 'provider', cwd: directory, ...(permissionMode ? { permissionMode } : {}) });
  t.after(async () => { await manager.shutdown(); store.close(); rmSync(directory, { recursive: true, force: true }); });
  return { adapter, adapters, manager, thread, events, store,
    methods: () => existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line).method) : [],
  };
}

test('protocol availability is read-only and unsupported versions cannot advertise voice', async t => {
  const a = await setup(t);
  assert.deepEqual(await a.adapter.realtimeSupport(), { supported: true, login: 'ready' });
  assert.ok(!a.methods().includes('thread/start'));
  assert.ok(!a.methods().includes('thread/realtime/start'));
  const b = await setup(t, { FAKE_CODEX_NO_VOICE: '1' });
  assert.deepEqual(await b.adapter.realtimeSupport(), { supported: false });
});

test('native voice work retains approvals, survives voice stop, and drains queued text exactly once', async t => {
  const { manager, thread, events, methods } = await setup(t, { FAKE_CODEX_VOICE_TASK: 'approve' }, 'ask');
  const voice = manager.voiceTransport(thread.id);
  const signals: VoiceSignal[] = [];
  await voice.start('fixture-offer', signal => signals.push(signal));
  await until(() => events.some(e => e.type === 'approval.request'));
  const native = events.find(e => e.type === 'turn.started' && e.data.source === 'voice')!;
  const approval = events.find(e => e.type === 'approval.request')!;
  assert.equal(approval.turnId, native.turnId);
  assert.equal(manager.getThread(thread.id).status, 'running');
  const queued = await manager.startTurn(thread.id, { text: 'after voice', queue: true });
  assert.ok('queued' in queued);
  await voice.stop(); voice.dispose();
  assert.equal(manager.getThread(thread.id).status, 'running');
  assert.ok(!methods().includes('turn/interrupt'));
  const option = (approval.data.options as { optionId: string }[])[0].optionId;
  manager.resolveApproval(String(approval.data.approvalId), { optionId: option });
  await until(() => events.filter(e => e.type === 'turn.done').length === 3);
  assert.equal(events.filter(e => e.type === 'turn.started').length, 3);
  assert.equal(methods().filter(m => m === 'turn/start').length, 2);
  assert.ok(signals.some(s => s.type === 'transcript'));
  const retained = events.find(e => e.type === 'voice.message');
  assert.equal(retained?.data.text, 'temporary caption');
  assert.equal(retained?.data.final, false, 'stopping retains an unfinished utterance without inventing a final');
  assert.ok(events.filter(e => e.turnId === native.turnId && e.type === 'message.delta').map(e => e.data.text).join('').includes('approved'));
  assert.equal(manager.getThread(thread.id).status, 'idle');
});

test('typed speech retains separate speakers and canonical corrections across history reload', async t => {
  const { manager, thread, events, store, methods } = await setup(t, { FAKE_CODEX_VOICE_CONVERSATION: '1' });
  const voice = manager.voiceTransport(thread.id);
  const signals: VoiceSignal[] = [];
  await voice.start('fixture-offer', s => signals.push(s));
  await until(() => events.filter(e => e.type === 'voice.message').length === 4);
  await voice.stop(); voice.dispose();
  const messages = events.filter(e => e.type === 'voice.message');
  assert.equal(new Set(messages.map(e => e.data.itemId)).size, 4);
  assert.equal(messages.find(e => e.data.text === '帮我整理最新的科技新闻。')?.data.role, 'user');
  assert.equal(messages.filter(e => e.data.role === 'assistant').length, 2);
  assert.ok(signals.some(s => s.type === 'transcript' && s.role === 'user' && !s.final && s.text === '帮我整理最新的新闻。'));
  assert.equal(events.filter(e => e.type === 'turn.started').length, 0);
  const history = await store.history.page(thread.id);
  assert.equal(history.events.filter(e => e.type === 'voice.message').length, 0);
  const recap = history.events.find(e => e.type === 'voice.closed')!;
  assert.equal(recap.data.count, 4);
  assert.equal(store.history.voiceText(thread.id, recap.data.sessionId).events.length, 4);
  assert.ok(!methods().includes('turn/start'));
});

test('revocation guards transcript persistence before a late canonical item reaches history', async t => {
  const { manager, thread, events } = await setup(t, { FAKE_CODEX_VOICE_CONVERSATION: '1' });
  let allowed = true;
  const voice = manager.voiceTransport(thread.id, () => { if (!allowed) throw new Error('revoked'); });
  const signals: VoiceSignal[] = [];
  await voice.start('fixture-offer', s => signals.push(s));
  await until(() => events.filter(e => e.type === 'voice.message').length === 3);
  allowed = false;
  await until(() => signals.some(s => s.type === 'error'));
  await voice.stop(); voice.dispose();
  assert.ok(!events.some(e => e.type === 'voice.message' && e.data.text === '帮我整理最新的科技新闻。'));
  const draft = events.find(e => e.type === 'voice.message' && e.data.text === '帮我整理最新的新闻。');
  assert.equal(draft?.data.final, false, 'only speech received before revocation is retained');
});

for (const task of ['voice-final-news', 'completed-only']) test(`native authoritative answers are retained once: ${task}`, async t => {
  const { manager, thread, events, store } = await setup(t, { FAKE_CODEX_VOICE_TASK: task });
  const voice = manager.voiceTransport(thread.id);
  await voice.start('fixture-offer', () => {});
  await until(() => events.some(e => e.type === 'turn.done'));
  const turnId = events.find(e => e.type === 'turn.started')!.turnId;
  const answer = turnAnswer(events, turnId);
  assert.ok(answer.startsWith('## 科技新闻'));
  assert.ok(!answer.includes('newz'));
  assert.equal(answer.split('## 科技新闻').length, 2);
  const history = await store.history.page(thread.id);
  assert.equal(turnAnswer(history.events as StoredEvent[], turnId), answer);
  assert.equal(store.searchText('newz').length, 0);
  assert.ok(store.searchText('测试新闻标题').some(hit => hit.threadId === thread.id));
  assert.equal(events.filter(e => e.type === 'message.replace').length, task === 'completed-only' ? 0 : 1);
  await voice.stop(); voice.dispose();
});

test('opening and closing voice during an existing task preserves its process and cancellation ownership', async t => {
  const { manager, thread, events, methods } = await setup(t);
  await manager.startTurn(thread.id, { text: 'slow' });
  await until(() => methods().includes('turn/start'));
  const voice = manager.voiceTransport(thread.id);
  await voice.start('fixture-offer', () => {});
  await voice.stop(); voice.dispose();
  assert.equal(manager.getThread(thread.id).status, 'running');
  assert.equal(methods().filter(m => m === 'thread/start').length, 1);
  assert.ok(!methods().includes('turn/interrupt'));
  await manager.cancelTurn(thread.id);
  await until(() => events.some(e => e.type === 'turn.done'));
  assert.equal(methods().filter(m => m === 'turn/interrupt').length, 1);
});

test('a clean native close permits another voice session on the same live thread', async t => {
  const { manager, thread, methods } = await setup(t);
  for (let i = 0; i < 2; i++) {
    const voice = manager.voiceTransport(thread.id);
    await voice.start('fixture-offer', () => {});
    await voice.stop(); voice.dispose();
  }
  assert.equal(methods().filter(m => m === 'thread/realtime/start').length, 2);
  assert.equal(methods().filter(m => m === 'thread/start').length, 1);
});

test('disposing an unclean voice source blocks unsafe reuse without replacing the task thread', async t => {
  const { manager, thread, methods } = await setup(t, { FAKE_CODEX_VOICE_NO_CLOSE: '1' });
  const voice = manager.voiceTransport(thread.id);
  await voice.start('fixture-offer', () => {});
  const stop = voice.stop(); voice.dispose(); await stop;
  const next = manager.voiceTransport(thread.id);
  await assert.rejects(next.start('another-offer', () => {}), /voice_session_unavailable/);
  next.dispose();
  assert.equal(methods().filter(m => m === 'thread/realtime/start').length, 1);
  assert.equal(methods().filter(m => m === 'thread/start').length, 1);
});

test('API key accounts fail locally without a realtime start or fallback', async t => {
  const { manager, thread, methods } = await setup(t, { FAKE_CODEX_VOICE_API_KEY: '1' });
  const voice = manager.voiceTransport(thread.id);
  await assert.rejects(voice.start('fixture-offer', () => {}), /chatgpt_login_required/);
  voice.dispose();
  assert.ok(!methods().includes('thread/realtime/start'));
});

test('a lease cancelled during process initialization never starts a late upstream voice session', async t => {
  const { manager, thread, methods } = await setup(t);
  const voice = manager.voiceTransport(thread.id);
  const pending = voice.start('fixture-offer', () => {});
  await voice.stop(); voice.dispose();
  await assert.rejects(pending, /voice_session_ended/);
  assert.ok(!methods().includes('thread/realtime/start'));
});


test('a native task starting in the same stdout batch as a prompted completion remains running', async t => {
  const { manager, thread, events } = await setup(t, { FAKE_CODEX_VOICE_AFTER_PROMPT: '1' }, 'ask');
  await manager.startTurn(thread.id, { text: 'first' });
  await until(() => events.some(e => e.type === 'approval.request'));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(manager.getThread(thread.id).status, 'running');
  const approval = events.find(e => e.type === 'approval.request')!;
  const native = events.find(e => e.type === 'turn.started' && e.data.source === 'voice')!;
  assert.equal(approval.turnId, native.turnId);
  assert.ok('queued' in await manager.startTurn(thread.id, { text: 'next', queue: true }));
  manager.resolveApproval(String(approval.data.approvalId), { optionId: 'accept' });
  await until(() => events.filter(e => e.type === 'turn.done').length === 3);
  assert.equal(manager.getThread(thread.id).status, 'idle');
});


test('replacing an idle provider cannot reuse the previous account connection for voice', async t => {
  const { adapter, adapters, manager, thread, methods } = await setup(t);
  const first = manager.voiceTransport(thread.id);
  await first.start('fixture-offer', () => {});
  await first.stop(); first.dispose();
  const before = methods().length;
  const replacement = new CodexAppServerAdapter([process.execPath, fileURLToPath(new URL('./fixtures/fake-codex.mjs', import.meta.url))], { FAKE_CODEX_VOICE_API_KEY: '1' });
  assert.notEqual(adapter, replacement);
  adapters.set('provider', replacement);
  const next = manager.voiceTransport(thread.id);
  await assert.rejects(next.start('fixture-offer', () => {}), /chatgpt_login_required/);
  next.dispose();
  assert.equal(methods().length, before, 'the old account must not receive new RPCs');
});


test('typed voice uses appendText and does not create a normal turn for a greeting', async t => {
  const { manager, thread, events, methods, store } = await setup(t);
  const voice = manager.voiceTransport(thread.id);
  const ready: VoiceSignal[] = [];
  await voice.start('fixture-offer', signal => ready.push(signal));
  await until(() => ready.some(s => s.type === 'sdp'));
  assert.equal(store.getThread(thread.id)?.title, 'New thread');
  await voice.appendText!('Hello');
  await until(() => events.some(e => e.type === 'voice.message' && e.data.text === 'Hello'));
  await voice.stop(); voice.dispose();
  assert.equal(methods().filter(m => m === 'thread/realtime/appendText').length, 1);
  assert.equal(methods().filter(m => m === 'turn/start').length, 0);
  // A thread started by voice is named by the first thing said in it.
  assert.equal(store.getThread(thread.id)?.title, 'Hello');
});

test('substantial speech creates one summary, repeated close and reopening do not replay it', async t => {
  const { manager, thread, events, methods, store } = await setup(t);
  const voice = manager.voiceTransport(thread.id);
  const ready: VoiceSignal[] = [];
  await voice.start('fixture-offer', signal => ready.push(signal));
  await until(() => ready.some(s => s.type === 'sdp'));
  for (let i = 0; i < 6; i++) await voice.appendText!(`Please record decision number ${i} for the next step.`);
  await until(() => events.filter(e => e.type === 'voice.message' && e.data.final).length === 6);
  await Promise.all([voice.stop(), voice.stop()]); voice.dispose();
  await until(() => events.some(e => e.type === 'turn.done'));
  assert.equal(events.filter(e => e.type === 'voice.closed').length, 1);
  assert.equal(events.filter(e => e.type === 'turn.started' && e.data.voiceSummary).length, 1);
  assert.equal(methods().filter(m => m === 'turn/start').length, 1);
  // The summary turn is not the thread's first message: it must not rename it.
  assert.equal(store.getThread(thread.id)?.title, 'Please record decision number 0 for the next step.');
  const reopened = manager.voiceTransport(thread.id);
  await reopened.start('fixture-offer', () => {});
  await reopened.stop(); reopened.dispose();
  assert.equal(methods().filter(m => m === 'turn/start').length, 1);
});

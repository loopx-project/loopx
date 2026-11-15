import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { CoordinationManager, coordinationServer } from '../src/coordination.js';
import { listenPrivate, createHttpServer } from '../src/http.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { TraexAcpAdapter } from '../src/adapters/traex.js';
import { ZcodeAdapter } from '../src/adapters/zcode.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import type { VoiceSignal } from '../src/voice-sessions.js';
import { ExtensionHost } from '../src/extension-host.js';
import { VoiceService } from '../src/voice-service.js';
import type { AddressInfo } from 'node:net';
import type { ExtensionManifest } from '@calli/extension-api';

const fixture = (name: string) => [process.execPath, fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url))];
const until = async (predicate: () => boolean) => {
  const end = Date.now() + 12000;
  while (!predicate()) { if (Date.now() > end) throw new Error('voice collaboration timed out'); await new Promise(resolve => setTimeout(resolve, 15)); }
};

async function setup(t: TestContext, target = 'worker', env: Record<string, string> = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-voice-team-'));
  const log = join(dir, 'rpc.jsonl');
  const adapters = new Map<string, AgentAdapter>([
    ['speaker', new CodexAppServerAdapter(fixture('fake-codex.mjs'), { FAKE_CODEX_RPC_LOG: log,
      FAKE_CODEX_VOICE_TASK: 'voice-delegate', FAKE_CODEX_VOICE_RELAY: '1', ...env })],
    ['worker', new PiRpcAdapter(fixture('fake-pi.mjs'))],
    ['reviewer', new ClaudeAcpAdapter(fixture('fake-acp.mjs'))],
    ['terminal', new AgyAdapter(fixture('fake-agy.mjs'))],
    ['alternate', new TraexAcpAdapter(fixture('fake-acp.mjs'))],
    ['z-worker', new ZcodeAdapter(fixture('fake-zcode.mjs'), { ZCODE_DATA_BASE_DIR: dir })],
  ]);
  const store = new Store(dir);
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir));
  const socket = join(dir, 'calli.sock');
  const team = new CoordinationManager(store, manager, socket);
  const ipc = coordinationServer(team); await listenPrivate(ipc, socket);
  t.after(async () => {
    team.dispose(); await manager.shutdown(); await new Promise<void>(resolve => ipc.close(() => resolve()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  });
  const thread = await manager.createThread({ agent: target, cwd: dir,
    permissionMode: adapters.get(target)!.capabilities.permissionModes.includes('ask') ? 'ask' : undefined });
  return { store, manager, thread, team, adapters,
    calls: () => readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line)),
    open() { const signals: VoiceSignal[] = []; const voice = manager.voiceTransport(thread.id, () => {}, 'speaker');
      return { voice, signals, start: () => voice.start('fixture-offer', signal => signals.push(signal)) }; },
  };
}

test('native voice prepares collaboration, delegates to Pi and relays its asynchronous result once', async t => {
  const h = await setup(t, 'speaker');
  const { voice, signals, start } = h.open();
  t.after(() => voice.dispose());
  await start();
  await until(() => signals.some(s => s.type === 'transcript' && s.text.includes('已收到成员结果')));
  const state = h.team.view(h.thread.id)!;
  assert.equal(state.members.length, 1);
  assert.equal(state.members[0].thread!.agent, 'worker');
  assert.equal(state.messages.filter(m => m.kind === 'message').length, 1);
  assert.equal(state.messages.filter(m => m.kind === 'result').length, 1);
  assert.match(state.messages.find(m => m.kind === 'message')!.result, /calli-ready:true/);
  assert.equal(signals.filter(s => s.type === 'transcript' && s.text.includes('已收到成员结果')).length, 1);
  const request = h.calls().find(c => c.method === 'thread/realtime/start');
  assert.match(request.params.realtimeStartInstructions, /calli spawn/);
  assert.equal(request.params.clientManagedHandoffs, true);
  assert.equal(h.calls().filter(c => c.method === 'thread/realtime/appendSpeech').length, 2);
  assert.equal(h.calls().filter(c => c.method === 'turn/start').length, 1, 'only the result starts a prompted lead turn');
  await voice.stop();
  assert.ok(!h.calls().some(c => c.method === 'turn/interrupt'));
});

for (const target of ['worker', 'reviewer', 'terminal', 'alternate', 'z-worker']) {
  test(`existing ${target} conversation receives voice tasks through its original adapter`, async t => {
    const h = await setup(t, target);
    await h.manager.startTurn(h.thread.id, { text: 'existing conversation' });
    await until(() => h.manager.threadView(h.thread.id).status === 'idle');
    const nativeId = h.manager.threadView(h.thread.id).agentSessionId;
    const permission = h.manager.threadView(h.thread.id).permissionMode;
    const { voice, signals, start } = h.open(); t.after(() => voice.dispose());
    await start();
    await until(() => signals.some(s => s.type === 'transcript' && s.text.includes('已收到成员结果')));
    const state = h.team.view(h.thread.id)!;
    assert.equal(state.members.length, 1);
    const coordinator = state.members[0];
    assert.equal(coordinator.voiceTarget, h.thread.id);
    assert.equal(coordinator.thread!.agent, 'speaker');
    assert.equal(h.manager.threadView(h.thread.id).agentSessionId, nativeId);
    assert.equal(h.manager.threadView(h.thread.id).permissionMode, permission);
    assert.equal(state.messages.length, 2, 'one task and its result, no acknowledgement loop');
    assert.equal(state.messages[0].to, h.thread.id);
    assert.equal(state.messages[1].to, coordinator.id);
    assert.equal(h.store.listEventsOfType(h.thread.id, 'voice.message').filter(e => e.data.role === 'assistant').length, 2);
    const instructions = h.calls().find(c => c.method === 'thread/realtime/start').params.realtimeStartInstructions;
    assert.match(instructions, /existing conversation/);
    assert.match(instructions, new RegExp(`calli send ${h.thread.id}`));
    await assert.rejects(h.team.command(coordinator.id, { op: 'spawn', name: 'unexpected', agent: target, text: 'x', requestId: 'bad' }), /绑定/);
    await voice.stop(); voice.dispose();
    const reopened = h.open(); t.after(() => reopened.voice.dispose()); await reopened.start();
    await until(() => h.manager.threadView(coordinator.id).status === 'idle');
    assert.equal(h.team.view(h.thread.id)!.members.length, 1, 'reuse the same coordinator');
    assert.equal(h.team.view(h.thread.id)!.messages.filter(m => m.kind === 'message').length, 1, 'same native request key cannot run twice');
    await reopened.voice.stop();
  });
}

test('closing voice keeps delegated work and its original approval alive', async t => {
  const h = await setup(t, 'reviewer', { FAKE_CODEX_VOICE_MESSAGE: 'approve' });
  const { voice, signals, start } = h.open(); t.after(() => voice.dispose()); await start();
  await until(() => h.manager.threadView(h.thread.id).waiting);
  await until(() => signals.some(s => s.type === 'transcript' && s.text.includes('等待批准')));
  const approval = h.store.listEventsOfType(h.thread.id, 'approval.request').at(-1)!;
  await voice.stop(); voice.dispose();
  const reports = h.calls().filter(c => c.method === 'thread/realtime/appendSpeech').length;
  assert.equal(h.manager.threadView(h.thread.id).waiting, true);
  assert.equal(h.manager.threadView(h.thread.id).status, 'running');
  h.manager.resolveApproval(String(approval.data.approvalId), { optionId: (approval.data.options as { optionId: string }[])[0].optionId });
  await until(() => h.team.view(h.thread.id)!.messages.every(m => m.state === 'returned'));
  assert.equal(h.calls().filter(c => c.method === 'thread/realtime/appendSpeech').length, reports, 'late results stay in the conversation after voice closes');
  assert.ok(!h.calls().some(c => c.method === 'turn/interrupt'));
});

test('voice target scope rejects another member and preserves task idempotency', async t => {
  const h = await setup(t);
  const voiceId = await h.team.voiceThread(h.thread.id, 'speaker');
  const other = await h.team.command(h.thread.id, { op: 'spawn', agent: 'worker', name: 'other', text: 'hello', requestId: 'other' }) as { to: string };
  await assert.rejects(h.team.command(voiceId, { op: 'send', target: other.to, text: 'x', requestId: 'wrong' }), /绑定/);
  const command = { op: 'send', target: h.thread.id, text: 'hello', requestId: 'same' };
  const first = await h.team.command(voiceId, command) as { id: string };
  assert.equal((await h.team.command(voiceId, command) as { id: string }).id, first.id);
});

test('voice HTTP chooses the native provider for an existing text target and keeps the target binding', async t => {
  const h = await setup(t);
  const manifest: ExtensionManifest = { id: 'voice', version: '0.1.0', apiVersion: 1,
    name: { zh: '语音', en: 'Voice', ja: '音声' }, description: { zh: '语音', en: 'Voice', ja: '音声' },
    permissions: ['voice.control'], requires: { agentCapabilities: ['realtimeVoice'] }, contributes: {} };
  const extensions = new ExtensionHost({ store: h.store.extensions, source: h.manager, catalog: [manifest] }); extensions.start();
  const voice = new VoiceService({ adapters: h.adapters, extensions, manager: h.manager });
  const server = createHttpServer({ manager: h.manager, voice, extensions, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/`;
  const request = (path: string, method: string, body?: unknown, owner = 'voice-browser-test-aaaaaaaa') => fetch(base + path, {
    method, headers: { authorization: 'Bearer test-token', 'content-type': 'application/json', 'x-calli-voice-owner': owner },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  t.after(async () => { await voice.dispose(); server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); extensions.dispose(); });
  const open = () => request('voice', 'POST', { extensionId: 'voice', threadId: h.thread.id, offer: 'fixture-offer', providerId: 'untrusted' });
  assert.equal((await open()).status, 403);
  assert.equal((await request('extensions/voice/install', 'POST', { granted: ['voice.control'] })).status, 200);
  const response = await open(); assert.equal(response.status, 201);
  const session = await response.json() as { id: string; providerId: string; threadId: string };
  assert.equal(session.providerId, 'speaker'); assert.equal(session.threadId, h.thread.id);
  assert.equal((await open()).status, 409);
  assert.equal((await request(`voice/${session.id}/heartbeat`, 'POST', undefined, 'voice-browser-test-bbbbbbbb')).status, 404);
  await until(() => h.team.view(h.thread.id)?.members.length === 1);
  assert.equal((await request(`voice/${session.id}`, 'DELETE')).status, 204);
});

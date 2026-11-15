import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { CodexAppServerAdapter, codexModelOptions } from '../src/adapters/codex.js';
import { AgentRegistry } from '../src/agents.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
const here = dirname(fileURLToPath(import.meta.url));
const fixture = [process.execPath, join(here, 'fixtures/fake-codex.mjs')];

test('speed capability follows the native catalog, including legacy and future tiers', () => {
  const models = codexModelOptions([
    { id: 'current', serviceTiers: [{ id: 'priority', name: 'Fast' }, { id: 'ultrafast', name: 'Ultrafast' }] },
    { id: 'legacy', additionalSpeedTiers: ['fast'] },
    { id: 'unsupported', serviceTiers: [], additionalSpeedTiers: ['fast'] },
    { id: 'unknown' }, { id: 'future', serviceTiers: [{ id: 'custom-tier', name: 'Custom' }] },
    { id: 'economy', serviceTiers: [{ id: 'flex', name: 'Flex' }] },
  ]);
  assert.deepEqual(models[0].speeds?.map(s => s.id), ['default', 'priority', 'ultrafast']);
  assert.deepEqual(models[1].speeds?.map(s => s.id), ['default', 'priority']);
  assert.equal(models[2].speeds, undefined); assert.equal(models[3].speeds, undefined);
  assert.equal(models[4].speeds?.[1].id, 'custom-tier');
  assert.deepEqual(models[0].speeds?.filter(s => s.isFast).map(s => s.id), ['priority', 'ultrafast']);
  assert.equal(models[1].speeds?.[1].isFast, true);
  assert.equal(models[4].speeds?.[1].isFast, false);
  assert.equal(models[5].speeds?.[1].isFast, false);
});

test('Codex sends speed in native settings, Plan turns and goal settings without changing effort', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-speed-native-'));
  const log = join(dir, 'rpc.jsonl');
  const adapter = new CodexAppServerAdapter(fixture, { FAKE_CODEX_SPEED: '1', FAKE_CODEX_RPC_LOG: log });
  const s = await adapter.start({ cwd: dir, workMode: 'plan', speed: 'priority', effort: 'high', permissionMode: 'full_access' });
  let output = '';
  const hooks = { emit: (e: any) => { if (e.type === 'message.delta') output += e.data.text; }, requestApproval: async () => ({ cancelled: true as const }) };
  try {
    await s.prompt({ text: 'speed?', images: [] }, hooks);
    assert.match(output, /effort:high;speed:priority;mode:plan;approval:never/);
    await s.setSpeed!('default'); output = '';
    await s.prompt({ text: 'speed?', images: [] }, hooks);
    assert.match(output, /speed:default/);
    await s.setSpeed!(null);
    await s.setWorkMode!('execute');
    await s.setSpeed!('ultrafast');
    await s.prompt({ text: '/goal Verify native speed', images: [] }, hooks);
    const calls = readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line));
    assert.ok(calls.some(c => c.method === 'turn/start' && c.params.serviceTier === 'priority' && c.params.collaborationMode.mode === 'plan'));
    assert.ok(calls.some(c => c.method === 'thread/settings/update' && c.params.serviceTier === null));
    assert.ok(calls.some(c => c.method === 'thread/settings/update' && c.params.serviceTier === 'ultrafast' && c.params.effort === 'high'));
  } finally { await s.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('HTTP speed validates models, survives restore, and clears incompatible tiers after a model change', { timeout: 15000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-speed-'));
  const store = new Store(dir);
  const registry = new AgentRegistry([{ id: 'codex', adapter: 'codex', command: fixture, env: { FAKE_CODEX_SPEED: '1' } }, { id: 'pi', adapter: 'pi', command: [process.execPath, join(here, 'fixtures/fake-pi.mjs')] }], null);
  let manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir));
  let server = createHttpServer({ manager, registry, token: 'speed-test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const call = (path: string, method = 'GET', body?: unknown) => fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}${path}`, { method, headers: { Authorization: 'Bearer speed-test-token', 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const idle = async (id: string) => { for (let i = 0; i < 250; i++) { if (manager.threadView(id).status !== 'running') return; await new Promise(r => setTimeout(r, 20)); } assert.fail('thread still running'); };
  try {
    for (const body of [{ agent: 'codex', model: 'gpt-small', speed: 'priority' }, { agent: 'codex', speed: 'invented' }, { agent: 'pi', speed: 'priority' }]) {
      assert.equal((await call('/api/threads', 'POST', { cwd: dir, ...body })).status, 400);
    }
    const res = await call('/api/threads', 'POST', { agent: 'codex', cwd: dir, speed: 'priority', effort: 'high', workMode: 'plan' });
    assert.equal(res.status, 201); const thread = await res.json() as any;
    assert.equal(thread.speed, 'priority');
    const reopened = new Store(dir); assert.equal(reopened.getThread(thread.id)?.speed, 'priority'); reopened.close();
    assert.equal((await call(`/api/threads/${thread.id}`, 'PATCH', { speed: 'bogus', model: 'gpt-small' })).status, 400);
    assert.equal(store.getThread(thread.id)?.model, null);
    await call(`/api/threads/${thread.id}/turns`, 'POST', { text: 'speed?' }); await idle(thread.id);
    assert.match(store.listEvents(thread.id).filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), /speed:priority/);
    await manager.shutdown(); await new Promise<void>(r => server.close(() => r()));
    manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir));
    server = createHttpServer({ manager, registry, token: 'speed-test-token', webDir: null, version: 'test' });
    await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
    const changed = await call(`/api/threads/${thread.id}`, 'PATCH', { model: 'gpt-small' });
    assert.equal(changed.status, 200); assert.equal((await changed.json() as any).speed, 'default');
    await call(`/api/threads/${thread.id}/turns`, 'POST', { text: 'speed?' }); await idle(thread.id);
    assert.match(store.listEvents(thread.id).filter(e => e.type === 'message.delta').map(e => e.data.text).join(''), /speed:default/);
    assert.equal(store.getThread(thread.id)?.effort, 'high');
    assert.equal(store.getThread(thread.id)?.workMode, 'plan');
    assert.equal((await call(`/api/threads/${thread.id}`, 'PATCH', { speed: 'priority' })).status, 400);
  } finally { await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('Claude discovers Fast per model, preserves cleared intent across unsupported models, and reports native fallback', async () => {
  const { ClaudeAcpAdapter } = await import('../src/adapters/claude.js');
  const adapter = new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures/fake-acp.mjs')], { FAKE_ACP_SPEED: '1', FAKE_ACP_SPEED_REVOKED: '1' });
  const models = await adapter.models();
  // Catalogs stay light since the per-model probe was dropped: speeds come from a live session.
  assert.equal(models.find(m => m.id === 'big')?.speeds, undefined);
  assert.equal(models.find(m => m.id === 'small')?.speeds, undefined);
  const changes: string[] = [];
  const s = await adapter.start({ cwd: here, model: 'big', speed: 'fast', onSpeed: speed => changes.push(speed) });
  let text = '';
  const hooks = { emit: (e: any) => { if (e.type === 'message.delta') text += e.data.text; }, requestApproval: async () => ({ cancelled: true as const }) };
  try {
    await s.prompt({ text: 'speed?', images: [] }, hooks);
    assert.match(text, /speed:on/); assert.equal(changes.at(-1), 'default');
    await s.setSpeed!('fast');
    await s.setModel!('small'); await s.setSpeed!('default');
    await assert.rejects(s.setSpeed!('fast'), /不支持快速模式/);
    await s.setModel!('big'); text = '';
    await s.prompt({ text: 'speed?', images: [] }, hooks);
    assert.match(text, /speed:off/);
  } finally { await s.close(); }
});

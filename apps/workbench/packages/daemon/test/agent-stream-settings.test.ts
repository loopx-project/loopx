import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import type { AddressInfo } from 'node:net';
import { AgentRegistry } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { createHttpServer } from '../src/http.js';
import { createFakeDoubaoRuntime, fakeDoubaoBundle, writeFakeDoubaoManifest } from './fixtures/doubao-runtime.mjs';
import { validateDoubaoStreamRuntime } from '../src/adapters/doubao-runtime.js';

const wait = async (check: () => unknown, timeout = 8_000) => {
  const until = Date.now() + timeout;
  while (!check()) { if (Date.now() > until) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};
async function setup(t: { after(fn: () => unknown): void }) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-agent-stream-settings-'));
  const root = join(dir, 'runtime'), bin = createFakeDoubaoRuntime(root);
  const file = join(dir, 'agents.json'), log = join(dir, 'calls.jsonl');
  const instance = { id: 'doubao', adapter: 'doubao' as const, label: 'Local CLI', command: [bin], hidden: true, account: 'local@example.com', env: {
    CALLI_DOUBAO_STREAM: '0', FAKE_DOUBAO_LOG: log, KEEP: 'instance-setting', HOME: join(dir, 'account-home'),
  } };
  const registry = new AgentRegistry([instance, { id: 'pi', adapter: 'pi', command: ['true'] }], file);
  const store = new Store(join(dir, 'data'));
  const manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir), () => registry.list());
  const server = createHttpServer({ manager, registry, token: 'settings-test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const api = (path: string, method = 'GET', body?: unknown, authenticated = true) => fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}${path}`, {
    method, headers: { 'Content-Type': 'application/json', ...(authenticated ? { Authorization: 'Bearer settings-test-token' } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const calls = () => existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line)) : [];
  t.after(async () => { registry.dispose(); await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); });
  return { dir, root, bin, instance, registry, store, manager, api, calls, file };
}

test('stream bridge settings validate read-only, persist only their own env and reach subsequent clients', async t => {
  const h = await setup(t);
  const original = readFileSync(join(h.root, 'resources/js_app/entry-cli-router/main.js'), 'utf8');
  const supported = await (await h.api('/api/agents/supported')).json() as any[];
  assert.deepEqual(supported.find(a => a.adapter === 'doubao').capabilities.streamBridge, { platform: 'Linux x64', temporaryMegabytes: 106 });
  assert.equal(supported.find(a => a.adapter === 'pi').capabilities.streamBridge, undefined);
  const before = await (await h.api('/api/agents')).json() as any[];
  assert.equal(before.find(a => a.id === 'doubao').streamBridgeEnabled, false);
  assert.equal(before.find(a => a.id === 'pi').streamBridgeEnabled, undefined);
  assert.equal((await h.api('/api/agents/doubao', 'PATCH', { streamBridge: true }, false)).status, 401);
  await validateDoubaoStreamRuntime([h.bin], {}, h.dir);
  assert.deepEqual(h.calls(), [], 'compatibility validation never prompts, launches or copies the runtime');
  const old = h.registry.adapters.get('doubao');
  const saved = await h.api('/api/agents/doubao', 'PATCH', { streamBridge: true });
  assert.equal(saved.status, 200);
  assert.deepEqual(h.registry.get('doubao'), { ...h.instance, env: { ...h.instance.env, CALLI_DOUBAO_STREAM: '1' } });
  assert.notEqual(h.registry.adapters.get('doubao'), old);
  assert.equal(h.registry.adapters.get('doubao')!.streamBridgeEnabled, true);
  assert.equal(readFileSync(join(h.root, 'resources/js_app/entry-cli-router/main.js'), 'utf8'), original);
  assert.deepEqual(h.calls(), []);
  const after = await (await h.api('/api/agents')).json() as any[];
  assert.equal(after.find(a => a.id === 'doubao').streamBridgeEnabled, true);
  const reloaded = AgentRegistry.fromEnv({ dataDir: h.dir, env: {}, defaults: [] });
  assert.equal(reloaded.adapters.get('doubao')!.streamBridgeEnabled, true);
  assert.equal(reloaded.get('doubao').hidden, true);
  assert.equal(reloaded.get('doubao').account, h.instance.account);
  assert.equal(reloaded.get('doubao').env?.KEEP, 'instance-setting');
  assert.equal((await (await h.api('/api/agents/doubao/diagnostics')).json() as any).protocol, 'doubao stream-json bridge (experimental)');
  await h.api('/api/agents/doubao', 'PATCH', { streamBridge: false });
  assert.equal((await (await h.api('/api/agents/doubao/diagnostics')).json() as any).protocol, 'doubao -p (text)', 'settings invalidate cached diagnostics');
});

test('invalid settings do not partially update; incompatible installs save and report a fallback', async t => {
  const h = await setup(t), original = h.registry.get('doubao'), adapter = h.registry.adapters.get('doubao');
  for (const value of [null, 'true', 1, {}, []]) {
    assert.equal((await h.api('/api/agents/doubao', 'PATCH', { label: 'Changed', streamBridge: value })).status, 400);
    assert.deepEqual(h.registry.get('doubao'), original);
    assert.equal(h.registry.adapters.get('doubao'), adapter);
  }
  for (const value of [false, true]) assert.equal((await h.api('/api/agents/pi', 'PATCH', { streamBridge: value })).status, 400);
  // A CLI update that keeps the output seams still passes; compatibility ignores the version.
  const manifestPath = join(h.root, 'manifest.json'), manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  writeFileSync(manifestPath, JSON.stringify({ ...manifest, version: '2.32.0' }));
  assert.deepEqual(await (await h.api('/api/agents/doubao/stream-bridge')).json(), { compatible: true });
  const saved = await h.api('/api/agents/doubao', 'PATCH', { streamBridge: true });
  assert.equal(saved.status, 200);
  assert.deepEqual(h.registry.get('doubao'), { ...h.instance, env: { ...h.instance.env, CALLI_DOUBAO_STREAM: '1' } });
  // Changed output seams are reported by the status endpoint, but saving consent never fails.
  const bundlePath = join(h.root, 'resources/js_app/entry-cli-router/main.js'), bundle = readFileSync(bundlePath, 'utf8');
  writeFileSync(bundlePath, fakeDoubaoBundle('entry-cli-router').replace('case"stream-json":', 'case"new":'));
  writeFakeDoubaoManifest(h.root);
  const status = await (await h.api('/api/agents/doubao/stream-bridge')).json() as any;
  assert.equal(status.compatible, false); assert.match(status.reason, /输出结构/);
  assert.equal((await h.api('/api/agents/doubao/stream-bridge', 'GET', undefined, false)).status, 401);
  assert.equal((await h.api('/api/agents/pi/stream-bridge')).status, 400);
  assert.equal((await h.api('/api/agents/missing/stream-bridge')).status, 404);
  assert.equal((await h.api('/api/agents/doubao', 'PATCH', { streamBridge: false })).status, 200, 'disabling works on incompatible installs');
  assert.equal((await h.api('/api/agents/doubao', 'PATCH', { streamBridge: true })).status, 200, 'enabling saves even when incompatible');
  assert.equal(h.registry.adapters.get('doubao')!.streamBridgeEnabled, true);
  writeFileSync(bundlePath, bundle); writeFakeDoubaoManifest(h.root); writeFileSync(manifestPath, JSON.stringify(manifest));
  assert.deepEqual(await (await h.api('/api/agents/doubao/stream-bridge')).json(), { compatible: true });
  assert.deepEqual(h.calls(), []);
});

test('adding a local CLI persists explicit consent, and enabling can be rolled back on disk errors', async t => {
  const h = await setup(t);
  const res = await h.api('/api/agents', 'POST', { id: 'doubao-2', adapter: 'doubao', command: [h.bin], streamBridge: true, env: { KEEP: 'second' } });
  assert.equal(res.status, 201);
  assert.deepEqual(h.registry.get('doubao-2').env, { KEEP: 'second', CALLI_DOUBAO_STREAM: '1' });
  assert.equal((await h.api('/api/agents', 'POST', { id: 'pi-2', adapter: 'pi', command: ['true'], streamBridge: false })).status, 400);
  assert.equal(h.registry.list().some(a => a.id === 'pi-2'), false);
  const original = h.registry.get('doubao'), adapter = h.registry.adapters.get('doubao');
  rmSync(h.file); mkdirSync(h.file);
  await assert.rejects(h.registry.update('doubao', { label: 'Changed', streamBridge: true }));
  assert.deepEqual(h.registry.get('doubao'), original);
  assert.equal(h.registry.adapters.get('doubao'), adapter);
  assert.equal(readdirSync(h.dir).some(name => name.endsWith('.tmp')), false);
});

test('a saved bridge switch does not interrupt the current turn and applies on the next native process', async t => {
  const h = await setup(t);
  const thread = await h.manager.createThread({ agent: 'doubao', cwd: h.dir });
  await h.manager.startTurn(thread.id, { text: 'slow' });
  await wait(() => h.store.listEvents(thread.id, 0, -1).some(e => e.type === 'message.delta' && e.data.text === 'working'));
  const sid = h.manager.getThread(thread.id).agentSessionId;
  assert.ok(sid);
  const previous = h.calls()[0];
  assert.equal(previous.runtime, h.bin);
  assert.equal((await h.api('/api/agents/doubao', 'PATCH', { streamBridge: true })).status, 200);
  assert.equal(h.manager.threadView(thread.id).status, 'running');
  assert.equal(h.calls().length, 1, 'changing settings starts no task and kills no active native process');
  await h.manager.cancelTurn(thread.id);
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  const next = await h.manager.startTurn(thread.id, { text: 'stream-tool' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.equal(h.manager.getThread(thread.id).agentSessionId, sid);
  assert.ok(h.store.listTurnEvents(thread.id, next.turnId).some(e => e.type === 'tool.start'));
  const bridged = h.calls()[1];
  assert.notEqual(bridged.runtime, h.bin);
  assert.ok(bridged.args.includes(sid));
  await h.api('/api/agents/doubao', 'PATCH', { streamBridge: false });
  await h.manager.startTurn(thread.id, { text: 'after disable' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.equal(h.calls()[2].runtime, h.bin);
  assert.equal(h.manager.getThread(thread.id).agentSessionId, sid);
  assert.equal(existsSync(dirname(dirname(bridged.runtime))), false, 'replacing the idle handle releases the patched copy');
});

test('an explicit disabled setting overrides an inherited global bridge default', async t => {
  const h = await setup(t), old = process.env.CALLI_DOUBAO_STREAM;
  process.env.CALLI_DOUBAO_STREAM = '1';
  try {
    await h.registry.add({ id: 'doubao-inherited', adapter: 'doubao', command: [h.bin] });
    assert.equal(h.registry.adapters.get('doubao-inherited')!.streamBridgeEnabled, true);
    await h.registry.update('doubao-inherited', { streamBridge: false });
    assert.equal(h.registry.adapters.get('doubao-inherited')!.streamBridgeEnabled, false);
    assert.equal(h.registry.get('doubao-inherited').env?.CALLI_DOUBAO_STREAM, '0');
  } finally {
    if (old === undefined) delete process.env.CALLI_DOUBAO_STREAM; else process.env.CALLI_DOUBAO_STREAM = old;
  }
});

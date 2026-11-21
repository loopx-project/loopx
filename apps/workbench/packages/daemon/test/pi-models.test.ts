import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { ModelOption, AgentAdapter } from '../src/adapters/types.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { AttachmentStore } from '../src/attachments.js';
import type { StoredEvent } from '../src/events.js';

const fixture = fileURLToPath(new URL('./fixtures/fake-pi.mjs', import.meta.url));
const wait = async (condition: () => boolean | Promise<boolean>) => {
  for (let i = 0; i < 300; i++) { if (await condition()) return; await new Promise(r => setTimeout(r, 10)); }
  throw new Error('fixture did not reach the expected state');
};
const setup = (initial = {}, vision = true) => {
  const dir = mkdtempSync(join(tmpdir(), 'pi-catalog-')), state = join(dir, 'state'), log = join(dir, 'log');
  const write = (value: object) => writeFileSync(state, JSON.stringify(value)); write(initial); writeFileSync(log, '');
  const adapter = new PiRpcAdapter([process.execPath, fixture], { FAKE_PI_CATALOG_FILE: state, FAKE_PI_PROBE_LOG: log, FAKE_PI_REQUIRE_NO_MCP: '1', FAKE_PI_MCP: vision ? '1' : '0' });
  const calls = () => readFileSync(log, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
  return { dir, adapter, write, calls, async clean() {
    write({});
    await wait(() => calls().filter(c => c.kind === 'mcp').length === calls().filter(c => c.kind === 'mcp-done').length);
    rmSync(dir, { recursive: true, force: true });
  } };
};

test('pi model catalog disables MCP and returns before its blocked handshake, then adds vision metadata', async () => {
  const h = setup({ mcpHold: true });
  try {
    const changes: ModelOption[][] = [];
    h.adapter.onModelsChanged = models => { changes.push(models); };
    const models = await h.adapter.models();
    assert.equal(changes.length, 0);
    assert.deepEqual(models.map(m => m.id), ['fake/think', 'fake/plain', 'fake/loose']);
    assert.equal(models[0].isDefault, true); assert.equal(models[0].images, true);
    assert.deepEqual(models[0].efforts, ['low', 'high', 'max']);
    assert.equal(models[1].images, undefined, 'pending MCP detection must not claim images are unsupported');
    assert.equal(models[1].imagesVia, undefined);
    assert.equal(h.calls().find(call => call.kind === 'catalog')?.noMcp, true);
    h.write({});
    await wait(async () => (await h.adapter.models())[1].imagesVia === 'analyze_image');
    assert.equal((await h.adapter.models())[1].images, false);
    assert.equal(changes.length, 1, 'the completed capability probe announces exactly once');
    assert.equal(changes[0][1].imagesVia, 'analyze_image');
    await h.adapter.models();
    assert.equal(changes.length, 1, 'cache hits do not announce again');
    assert.equal(h.calls().filter(call => call.kind === 'catalog').length, 1, 'metadata update reuses the catalog');
  } finally { h.write({}); await h.clean(); }
});

test('pi catalogs are cached per instance for ten minutes and refresh stale data in the background', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_000_000 });
  const a = setup({ label: 'Account A' }), b = setup({ label: 'Account B' });
  try {
    const [one, two, other] = await Promise.all([a.adapter.models(), a.adapter.models(), b.adapter.models()]);
    assert.equal(one[0].label, 'Account A'); assert.equal(two[0].label, 'Account A'); assert.equal(other[0].label, 'Account B');
    assert.equal(a.calls().filter(call => call.kind === 'catalog').length, 1);
    a.write({ label: 'New catalog', hold: true });
    t.mock.timers.setTime(1_000_000 + 9 * 60_000);
    assert.equal((await a.adapter.models())[0].label, 'Account A');
    assert.equal(a.calls().filter(call => call.kind === 'catalog').length, 1);
    t.mock.timers.setTime(1_000_000 + 10 * 60_000);
    assert.equal((await a.adapter.models())[0].label, 'Account A', 'stale return never waits for the held refresh');
    await wait(() => a.calls().filter(call => call.kind === 'catalog').length === 2);
    assert.equal((await a.adapter.models())[0].label, 'Account A');
    assert.equal(a.calls().filter(call => call.kind === 'catalog').length, 2, 'refresh is single-flight');
    a.write({ label: 'New catalog' });
    await wait(async () => (await a.adapter.models())[0].label === 'New catalog');
    assert.equal(other[0].label, 'Account B', 'instances never share catalog state');
  } finally { a.write({}); b.write({}); await a.clean(); await b.clean(); }
});

test('pi catalog refresh failures retain the last list, back off and allow explicit retry', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_000_000 });
  const h = setup({ label: 'Last known models' });
  try {
    await h.adapter.models(); h.write({ fail: true });
    t.mock.timers.setTime(1_000_000 + 10 * 60_000);
    assert.equal((await h.adapter.models())[0].label, 'Last known models', 'failed background refresh still serves the cache');
    await assert.rejects(h.adapter.models(true), /fixture catalog failure/);
    assert.equal((await h.adapter.models())[0].label, 'Last known models');
    assert.equal(h.calls().filter(call => call.kind === 'catalog').length, 2);
    h.write({ label: 'Recovered' });
    assert.equal((await h.adapter.models())[0].label, 'Last known models', 'automatic retry is backed off');
    assert.equal((await h.adapter.models(true))[0].label, 'Recovered');
    assert.equal(h.calls().filter(call => call.kind === 'catalog').length, 3);
  } finally { await h.clean(); }
});

test('pi catalog retries a failed cold load instead of caching its rejection', async () => {
  const h = setup({ fail: true });
  try {
    await assert.rejects(h.adapter.models(), /fixture catalog failure/);
    h.write({ label: 'Recovered' });
    assert.equal((await h.adapter.models())[0].label, 'Recovered');
  } finally { await h.clean(); }
});


test('pi native images bypass pending MCP, while indirect images wait and reuse its result', async () => {
  const h = setup({ mcpHold: true });
  try {
    assert.equal((await h.adapter.modelForImages(null))?.images, true);
    let settled = false;
    const pending = h.adapter.modelForImages('fake/plain').then(option => { settled = true; return option; });
    await wait(() => h.calls().some(c => c.kind === 'mcp'));
    await new Promise(r => setTimeout(r, 30));
    assert.equal(settled, false);
    h.write({});
    assert.equal((await pending)?.imagesVia, 'analyze_image');
    assert.equal((await h.adapter.modelForImages('fake/plain'))?.imagesVia, 'analyze_image');
    assert.equal(h.calls().filter(c => c.kind === 'mcp').length, 1);
  } finally { await h.clean(); }
});

test('deferred Pi capabilities broadcast the configured instance and reject unsupported images before a turn starts', async () => {
  const h = setup({ mcpHold: true }, false), store = new Store(h.dir);
  const adapters = new Map<string, AgentAdapter>([['personal-instance', h.adapter]]);
  const manager = new ThreadManager(store, adapters, new Workdirs([h.dir], join(h.dir, 'dirs')), new AttachmentStore(h.dir));
  const events: StoredEvent[] = []; const unsubscribe = manager.subscribe(event => { events.push(event); });
  try {
    const models = await manager.listModels('personal-instance');
    assert.equal(models[1].images, undefined);
    const thread = await manager.createThread({ agent: 'personal-instance', cwd: h.dir, model: 'fake/plain' });
    let settled = false;
    const pending = manager.startTurn(thread.id, { text: 'inspect image', images: [{ mimeType: 'image/png', data: 'unused' }] })
      .then(() => { settled = true; return null; }, error => { settled = true; return error; });
    await wait(() => h.calls().some(c => c.kind === 'mcp'));
    await new Promise(r => setTimeout(r, 30));
    assert.equal(settled, false, 'the unknown capability must be resolved before accepting an image');
    h.write({});
    assert.match(String(await pending), /cannot read images/);
    const updates = events.filter(event => event.type === 'agent.modelsChanged');
    assert.equal(updates.length, 1);
    assert.equal(updates[0].data.agent, 'personal-instance');
    assert.equal((updates[0].data.models as ModelOption[])[1].images, false);
    assert.equal(events.some(event => event.type === 'turn.started'), false);
    await manager.listModels('personal-instance');
    assert.equal(events.filter(event => event.type === 'agent.modelsChanged').length, 1);
  } finally { unsubscribe(); await manager.shutdown(); store.close(); await h.clean(); }
});

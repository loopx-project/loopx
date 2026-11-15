import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, cpSync, readFileSync, readdirSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { c as createTar } from 'tar';
import { ExternalExtensions, externalManifest, loadSupportedExternalCatalog } from '../src/external-extensions.js';
import { ExtensionHost } from '../src/extension-host.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { runExtensionCommand, ExtensionProcess } from '../src/extension-process.js';
import type { AddressInfo } from 'node:net';

const packageDir = resolve('../ext-loopx');
const executable = resolve('test/fixtures/fake-loopx.mjs');
const manifest = JSON.parse(readFileSync(join(packageDir, 'manifest.json'), 'utf8'));
const board = { ...manifest, id: 'board', permissions: ['events.summary', 'worksource.read'], contributes: { views: [] }, process: undefined };
function setup(t: any) {
  const root = mkdtempSync(join(tmpdir(), 'calli-external-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = { subscribe: (_f: any) => () => {}, listThreads: () => [], threadView: (id: string) => { if (!['a', 'b'].includes(id)) throw new Error('missing'); return { id, cwd: root } as any; } };
  const ext = new ExternalExtensions(join(root, 'extensions'), source, 'test-generation', () => {}, () => ['board']);
  ext.start(); t.after(() => ext.dispose());
  const config = { settings: { executable }, bindings: [{ id: 'binding', externalId: 'probe', settings: { registry: join(root, 'registry.json') }, threadIds: ['a', 'b'], consumers: ['board'] }] };
  return { root, ext, config, source };
}
async function install(ext: ExternalExtensions, path = packageDir) { const preview = await ext.prepare(path); return ext.install(preview.token, preview.manifest.permissions); }

test('external package is independently installed, detected, projected and disabled', async t => {
  const { ext, config } = setup(t);
  const preview = await ext.prepare(packageDir);
  assert.equal(ext.list().length, 0, 'inspection does not install or execute');
  await assert.rejects(ext.install(preview.token, []), /permissions/);
  await ext.install(preview.token, preview.manifest.permissions);
  await ext.configure('loopx', config);
  assert.deepEqual(await ext.detect('loopx'), { ok: true, version: '1.4.2', missing: undefined });
  const [work] = await ext.projections('board', 'a', true);
  assert.equal(work.snapshot?.title, 'Review the integration');
  assert.equal(work.snapshot?.complete, false, 'a partial list is not called complete');
  assert.equal(work.freshness, 'fresh');
  assert.deepEqual(work.binding.settings, {}, 'registry and executable stay out of view data');
  assert.equal((await ext.projections('ungranted', 'a')).length, 0);
  assert.equal((await ext.projections('board', 'not-linked')).length, 0);
  ext.setEnabled('loopx', false);
  assert.equal((await ext.projections('board'))[0].freshness, 'unavailable');
  ext.uninstall('loopx'); assert.deepEqual(await ext.projections(), []);
});

test('settings, bindings and version history survive a restart; rollback preserves scope', async t => {
  const { root, ext, config, source } = setup(t);
  await install(ext); await ext.configure('loopx', config);
  const next = join(root, 'next'); cpSync(packageDir, next, { recursive: true });
  writeFileSync(join(next, 'manifest.json'), JSON.stringify({ ...manifest, version: '0.2.0' }));
  await install(ext, next);
  assert.equal(ext.list()[0].previousVersion, '0.1.0');
  assert.equal(ext.rollback('loopx').manifest.version, '0.1.0');
  ext.dispose();
  const restored = new ExternalExtensions(join(root, 'extensions'), source, 'new-generation', () => {}, () => ['board']);
  t.after(() => restored.dispose());
  assert.deepEqual(restored.configuration('loopx'), config);
  restored.start();
  assert.equal((await restored.projections('board', 'a', true))[0].generation, 'new-generation');
  writeFileSync(join(next, 'manifest.json'), JSON.stringify({ ...manifest, version: '0.3.0' }));
  await install(restored, next);
  assert.equal(readdirSync(join(root, 'extensions/packages')).length, 2, 'only current and rollback artifacts remain');
  restored.uninstall('loopx');
  assert.equal(readdirSync(join(root, 'extensions/packages')).length, 0, 'uninstall removes all installed artifacts');
});

test('unbinding during an in-flight read drops both the response and the read result', async t => {
  const { ext, config } = setup(t);
  await install(ext);
  config.bindings[0].externalId = 'slow';
  await ext.configure('loopx', config);
  const reading = ext.projections('board', 'a', true);
  await new Promise(r => setTimeout(r, 100));
  await ext.configure('loopx', { ...config, bindings: [] });
  assert.deepEqual(await reading, []);
  assert.deepEqual(await ext.projections(), []);
});

test('manifest and local package validation rejects traversal, links and unsupported capabilities', async t => {
  const { root, ext } = setup(t);
  assert.throws(() => externalManifest({ ...manifest, process: { entry: '../escape.mjs' } }));
  assert.throws(() => externalManifest({ ...manifest, permissions: ['task.control'] }));
  assert.throws(() => externalManifest({ ...manifest, commands: [{ ...manifest.commands[0], args: [{ setting: 'unknown' }] }] }));
  const linked = join(root, 'linked'); mkdirSync(linked); symlinkSync(packageDir, join(linked, 'escape'));
  await assert.rejects(ext.prepare(linked), /links/);
  await assert.rejects(ext.prepare('relative-path'), /absolute/);
  await assert.rejects(ext.prepare('https://example.invalid/package.tgz'), /SHA-256/);
});

test('archive install validates checksum and loads npm pack layout without install scripts', async t => {
  const { root, ext } = setup(t);
  const dir = join(root, 'archive'); mkdirSync(dir); cpSync(packageDir, join(dir, 'package'), { recursive: true });
  const archive = join(root, 'plugin.tgz'); await createTar({ cwd: dir, file: archive, gzip: true }, ['package']);
  const bytes = readFileSync(archive), hash = createHash('sha256').update(bytes).digest('hex');
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(bytes);
  t.after(() => { globalThis.fetch = original; });
  await assert.rejects(ext.prepare('https://example.invalid/v0.1.0.tgz', '0'.repeat(64)), /checksum/);
  const preview = await ext.prepare('https://example.invalid/v0.1.0.tgz', hash);
  assert.equal((await ext.install(preview.token, preview.manifest.permissions)).manifest.id, 'loopx');
});

test('commands are argument arrays, omit hub credentials, bound output and cancel by owned child', async t => {
  const { root } = setup(t);
  const signal = new AbortController();
  const result = await runExtensionCommand(process.execPath, ['-e', 'console.log(JSON.stringify([process.argv[1], process.env.CALLI_TOKEN ?? null]))', '$(echo should-not-execute)'], root, {}, signal.signal);
  assert.deepEqual(JSON.parse(result.stdout), ['$(echo should-not-execute)', null]);
  await assert.rejects(runExtensionCommand(process.execPath, ['-e', 'process.stdout.write("x".repeat(3e6))'], root, {}, signal.signal), /output_limit/);
  const pending = runExtensionCommand(process.execPath, ['-e', 'setTimeout(()=>{},60000)'], root, {}, signal.signal); signal.abort();
  await assert.rejects(pending, /cancelled/);
  const entry = join(root, 'crash.mjs'); writeFileSync(entry, 'process.exit(1)');
  const transport = new ExtensionProcess(root, 'crash.mjs', async () => null);
  for (let i = 0; i < 3; i++) await assert.rejects(transport.request('test', {}), /exited/);
  await assert.rejects(transport.request('test', {}), /unavailable/); transport.stop();
});

test('all external HTTP endpoints use the authenticated host, with explicit view content grants', async t => {
  const { root, source } = setup(t);
  const store = new Store(join(root, 'store'));
  const extensions = new ExtensionHost({ store: store.extensions, source, catalog: [board], externalCatalog: loadSupportedExternalCatalog(), externalDir: join(root, 'http-extensions') });
  extensions.start();
  const server = createHttpServer({ manager: source as any, extensions, token: 'external-test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  t.after(async () => { extensions.dispose(); server.closeAllConnections(); await new Promise<void>(r => server.close(() => r())); store.close(); });
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + path, { method, headers: { Authorization: 'Bearer external-test-token', 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  assert.equal((await fetch(base + '/api/extensions')).status, 401);
  const catalog = await (await api('/api/extensions')).json();
  const supported = catalog.find((s: any) => s.manifest.id === 'loopx');
  assert.equal(supported.preset, true);
  assert.equal(supported.installed, false);
  assert.equal(supported.enabled, false);
  assert.deepEqual(extensions.external!.list(), [], 'discovery does not install or start a plugin');
  assert.deepEqual(await extensions.external!.projections(), []);
  const preview = await (await api('/api/extensions/prepare', 'POST', { origin: supported.origin })).json();
  assert.equal((await api('/api/extensions/commit', 'POST', { token: preview.token, granted: preview.manifest.permissions })).status, 200);
  const installed = (await (await api('/api/extensions')).json()).filter((s: any) => s.manifest.id === 'loopx');
  assert.equal(installed.length, 1, 'installed entry replaces the preset');
  assert.equal(installed[0].installed, true);
  assert.equal(installed[0].preset, true);
  assert.equal((await api('/api/extensions/loopx/configuration')).status, 200);
  assert.equal((await api('/api/extensions/loopx/configuration', 'PUT', { settings: { executable }, bindings: [] })).status, 200);
  assert.equal((await api('/api/extensions/loopx/detect', 'POST')).status, 200);
  assert.equal((await api('/api/work-sources?consumer=board')).status, 403);
  extensions.install('board', board.permissions);
  assert.equal((await api('/api/work-sources?consumer=board')).status, 200);
  assert.equal((await api('/api/extensions/loopx/rollback', 'POST')).status, 400);
  assert.equal((await api('/api/extensions/loopx', 'PATCH', { enabled: false })).status, 200);
  assert.equal((await api('/api/extensions/loopx', 'DELETE')).status, 204);
  const removed = (await (await api('/api/extensions')).json()).find((s: any) => s.manifest.id === 'loopx');
  assert.equal(removed.preset, true);
  assert.equal(removed.installed, false);
  assert.equal(removed.origin, supported.origin, 'uninstall restores the catalog source');
});

test('failed upgrade handshake keeps the running version; the old preview cannot be replayed', async t => {
  const { root, ext, config } = setup(t);
  await install(ext); await ext.configure('loopx', config);
  const broken = join(root, 'broken'); cpSync(packageDir, broken, { recursive: true });
  writeFileSync(join(broken, 'manifest.json'), JSON.stringify({ ...manifest, version: '0.2.0' }));
  writeFileSync(join(broken, 'src/index.mjs'), 'process.exit(1)');
  const preview = await ext.prepare(broken);
  await assert.rejects(ext.install(preview.token, preview.manifest.permissions), /exited/);
  assert.equal(ext.list()[0].manifest.version, '0.1.0');
  assert.equal((await ext.detect('loopx')).ok, true);
  await assert.rejects(ext.install(preview.token, preview.manifest.permissions), /expired/);
});

test('unknown tool versions and malformed CLI output never become a fresh work projection', async t => {
  const { root, ext, config } = setup(t);
  const newer = join(root, 'newer'); cpSync(packageDir, newer, { recursive: true });
  const m = structuredClone(manifest); m.commands[0].env.FAKE_LOOPX_VERSION = '2.0.0';
  writeFileSync(join(newer, 'manifest.json'), JSON.stringify(m));
  await install(ext, newer); await ext.configure('loopx', config);
  assert.equal((await ext.detect('loopx')).ok, false);
  assert.equal((await ext.projections('board', 'a', true))[0].freshness, 'unknown');
  await install(ext);
  config.bindings[0].externalId = 'invalid'; await ext.configure('loopx', config);
  assert.equal((await ext.projections('board', 'a', true))[0].snapshot, null);
  const bad = structuredClone(config); bad.bindings[0].settings.registry = 'relative.json';
  await assert.rejects(ext.configure('loopx', bad), /absolute/);
});

test('many goals serialize source commands without confusing equal goal IDs in different registries', async t => {
  const { root, ext, config } = setup(t);
  await install(ext);
  config.bindings = Array.from({ length: 8 }, (_, n) => ({ ...config.bindings[0], id: `binding${n}`, settings: { registry: join(root, `registry${n}.json`) }, threadIds: [n % 2 ? 'a' : 'b'] }));
  await ext.configure('loopx', config);
  const rows = await ext.projections('board', undefined, true);
  assert.equal(rows.length, 8);
  assert.ok(rows.every(row => row.freshness === 'fresh'));
  assert.equal(new Set(rows.map(row => row.binding.id)).size, 8);
});

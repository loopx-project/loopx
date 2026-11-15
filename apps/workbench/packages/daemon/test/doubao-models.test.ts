import { test } from 'node:test';
import assert from 'node:assert/strict';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { runInNewContext } from 'node:vm';
import { spawn } from 'node:child_process';
import { DoubaoAdapter } from '../src/adapters/doubao.js';
import { doubaoModels, readDoubaoModels, waitForDoubaoCatalogExit } from '../src/adapters/doubao-models.js';
import { patchDoubaoModelBundle, prepareDoubaoStreamRuntime } from '../src/adapters/doubao-runtime.js';
import { createFakeDoubaoRuntime, fakeDoubaoBundle, writeFakeDoubaoManifest } from './fixtures/doubao-runtime.mjs';

const option = { modelId: 'model-id', name: 'Native Model', available: true, isDefault: true,
  effortAdjustable: true, efforts: [{ level: 'none' }, { level: 'high' }] };
const envelope = (models: unknown[]) => JSON.stringify({ type: 'calli_models', models });
function setup(t: { after(fn: () => unknown): void }) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-doubao-model-test-')), root = join(dir, 'native');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const bin = createFakeDoubaoRuntime(root), log = join(dir, 'models.jsonl');
  const env = { FAKE_DOUBAO_MODEL_LOG: log, FAKE_DOUBAO_MARKER: 'selected-account', CALLI_DOUBAO_STREAM: '0' };
  const calls = () => existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line)) : [];
  return { dir, root, bin, log, env, calls };
}

test('catalog patch runs only the original model loader in both native entries', async () => {
  for (const bundle of ['entry-cli-router', 'entry-cli-chat'] as const) {
    const router = bundle === 'entry-cli-router';
    const method = router ? 'resolveSettings(e,t){return G({im:this.im,botId:this.botId},e,t)}' : 'resolveSettings(e,t){return et({im:this.im,botId:this.botId},e,t)}';
    const source = `class Chat{${method}};async function list(e,t){let o={kind:"list"};if("list"===o.kind)return ${router ? 'p' : 'b'}(e,t);throw Error("must not send")};const endpoint="/alice/slot/action_bar_v3/brief_list";`;
    let loads = 0, output = '';
    const loader = async () => { loads++; return { models: [{ option }] }; };
    const patched = patchDoubaoModelBundle(source, bundle);
    await runInNewContext(patched + ';list({chat:new Chat(),output:{stdout:write}},{});', { U: { Fo: loader }, W: loader, write: (text: string) => { output += text; } });
    assert.equal(loads, 1);
    assert.deepEqual(doubaoModels(output), [{ id: 'model-id', label: 'Native Model', isDefault: true, efforts: ['off', 'high'], images: false }]);
    assert.throws(() => patchDoubaoModelBundle(patched, bundle), /不兼容/);
    assert.throws(() => patchDoubaoModelBundle(source + source, bundle), /不兼容/);
    assert.throws(() => patchDoubaoModelBundle(source.replace('resolveSettings', 'renamed'), bundle), /不兼容/);
  }
});

test('catalog maps actual availability and efforts, rejecting malformed or unknown records', () => {
  assert.equal(doubaoModels(envelope([{ ...option, available: false }])).length, 0);
  assert.deepEqual(doubaoModels(envelope([{ ...option, effortAdjustable: false }]))[0].efforts, []);
  assert.deepEqual(doubaoModels(envelope([])), []);
  for (const value of ['invalid', 'null', '{}', '{"type":"session_list","models":[]}', envelope([option, option]),
    envelope([{ ...option, modelId: 1 }]), envelope([{ ...option, available: undefined }]),
    envelope([{ ...option, efforts: [{ level: 'unknown' }] }])]) assert.throws(() => doubaoModels(value));
});

test('catalog snapshots patch only copies, preserve text mode and remove their runtime', async t => {
  const h = setup(t), path = join(h.root, 'resources/js_app/entry-cli-router/main.js'), original = readFileSync(path, 'utf8');
  const runtime = await prepareDoubaoStreamRuntime([h.bin], h.env, h.dir, 'models');
  t.after(() => runtime.close());
  const patched = readFileSync(join(dirname(runtime.command[0]), 'resources/js_app/entry-cli-router/main.js'), 'utf8');
  assert.ok(patched.includes('calliReadModels'));
  assert.ok(patched.includes('output_format:"text"'));
  assert.equal(patched.includes('calli_bridge_tool'), false);
  assert.equal(readFileSync(path, 'utf8'), original);
  await runtime.close(); assert.equal(existsSync(runtime.directory), false);
});

test('adapter strips all business flags, deduplicates concurrent probes and caches per instance', async t => {
  const h = setup(t);
  const adapter = new DoubaoAdapter([h.bin, '--model=native-fast', '--effort', 'high', '-p', 'must not send', '--resume', 'other', '-C', '/other', '--permission-mode', 'always_allow', '--list'], { ...h.env, FAKE_DOUBAO_MODEL_DELAY: '40' });
  const a = adapter.models(), b = adapter.models(true);
  assert.equal(a, b);
  const models = await a;
  assert.equal(models.find(model => model.id === 'native-fast')?.isDefault, true);
  assert.equal(models.find(model => model.id === 'native-default')?.isDefault, false);
  await adapter.models(); assert.equal(h.calls().length, 1);
  await adapter.models(true); assert.equal(h.calls().length, 2);
  const other = new DoubaoAdapter([h.bin], { ...h.env, FAKE_DOUBAO_MARKER: 'other-account' });
  await other.models(); assert.equal(h.calls().length, 3);
  assert.deepEqual(h.calls().map(call => call.args), [['--list'], ['--list'], ['--list']]);
  assert.deepEqual(h.calls().map(call => call.marker), ['selected-account', 'selected-account', 'other-account']);
  for (const call of h.calls()) assert.equal(existsSync(dirname(call.runtime)), false, 'probe copy was removed after pipes closed');
});

test('catalog failures do not poison a usable cache and failed initial reads can retry', async t => {
  const h = setup(t), env: Record<string, string> = { ...h.env }, adapter = new DoubaoAdapter([h.bin], env);
  const good = await adapter.models();
  env.FAKE_DOUBAO_MODEL_ERROR = 'Not logged in';
  await assert.rejects(adapter.models(true), /Not logged in/);
  assert.deepEqual(await adapter.models(), good);
  const initial = new DoubaoAdapter([h.bin], env);
  await assert.rejects(initial.models(), /Not logged in/);
  delete env.FAKE_DOUBAO_MODEL_ERROR;
  assert.deepEqual(await initial.models(), good);
  for (const call of h.calls()) assert.equal(existsSync(dirname(call.runtime)), false);
});

test('catalog process errors, oversized records and deadlines clean up their own copy', async t => {
  const h = setup(t);
  for (const [env, pattern, timeout] of [
    [{ FAKE_DOUBAO_MODEL_ERROR: 'Authentication required' }, /Authentication/, 5000],
    [{ FAKE_DOUBAO_MODEL_OUTPUT: 'invalid JSON' }, /无效模型目录/, 5000],
    [{ FAKE_DOUBAO_MODEL_LARGE: '1' }, /大小限制/, 5000],
    [{ FAKE_DOUBAO_MODEL_DELAY: '60000' }, /超时/, 200],
  ] as const) await assert.rejects(readDoubaoModels([h.bin], { ...process.env, ...h.env, ...env }, h.dir, timeout), pattern);
  assert.equal(h.calls().length, 4);
  for (const call of h.calls()) assert.equal(existsSync(dirname(call.runtime)), false);
});

test('a changed loader stops before starting any native catalog process, a newer version with the same anchors does not', async t => {
  const h = setup(t), manifest = join(h.root, 'manifest.json'), original = readFileSync(manifest, 'utf8');
  const bundle = join(h.root, 'resources/js_app/entry-cli-router/main.js');
  writeFileSync(bundle, fakeDoubaoBundle('entry-cli-router').replace('resolveSettings', 'renamed'));
  writeFakeDoubaoManifest(h.root);
  await assert.rejects(readDoubaoModels([h.bin], h.env, h.dir), /不兼容/);
  assert.deepEqual(h.calls(), []);
  // Compatibility follows the anchors, not the version number.
  writeFileSync(bundle, fakeDoubaoBundle('entry-cli-router'));
  writeFakeDoubaoManifest(h.root);
  writeFileSync(manifest, readFileSync(manifest, 'utf8').replace('2.31.2', '2.99.0'));
  await readDoubaoModels([h.bin], h.env, h.dir);
  assert.equal(h.calls().length, 1);
});


test('a native launcher ignoring interrupt is force-stopped with its child before cleanup', { timeout: 10_000 }, async t => {
  const h = setup(t), started = Date.now();
  await assert.rejects(readDoubaoModels([h.bin], { ...process.env, ...h.env, FAKE_DOUBAO_MODEL_DELAY: '60000', FAKE_DOUBAO_IGNORE_SIGINT: '1', FAKE_DOUBAO_LAUNCHER: '1' }, h.dir, 250), /超时/);
  assert.ok(Date.now() - started >= 5000);
  assert.equal(h.calls().length, 1);
  assert.equal(existsSync(dirname(h.calls()[0].runtime)), false);
});


test('catalog cleanup lets the idle native service exit naturally and never removes a live runtime', async t => {
  const h = setup(t), helper = join(h.root, 'catalog-helper');
  copyFileSync(process.execPath, helper);
  const worker = spawn(helper, ['-e', 'setTimeout(()=>{}, 450)'], { stdio: 'ignore' });
  const exit = new Promise<void>(resolve => worker.once('exit', () => resolve()));
  t.after(async () => { if (worker.exitCode === null) worker.kill(); await exit; });
  await new Promise(resolve => worker.once('spawn', resolve));
  await assert.rejects(waitForDoubaoCatalogExit(h.root, 10), /后台服务尚未退出/);
  assert.ok(existsSync(helper), 'timeout must retain the executable used for peer identity');
  assert.equal(worker.exitCode, null, 'the shared service is not stopped');
  await waitForDoubaoCatalogExit(h.root);
  await exit;
  assert.equal(worker.exitCode, 0, 'the service exits of its own accord');
});

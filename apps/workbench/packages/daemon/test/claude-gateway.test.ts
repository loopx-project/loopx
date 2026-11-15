import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { gatewayQuota, gatewayWindowLabel } from '../src/adapters/gateway-quota.js';
import { modelGateway } from './fixtures/model-gateway.mjs';

test('Claude gateway discovery reads account settings and capabilities without selecting every model', async t => {
  const gateway = await modelGateway();
  t.after(() => gateway.close());
  const configDir = mkdtempSync(join(tmpdir(), 'loochat-gateway-account-'));
  t.after(() => rmSync(configDir, { recursive: true, force: true }));
  gateway.state.models = [
    { id: 'claude-gpt-5-6-sol', display_name: 'Gateway GPT 5.6', capabilities: { effort: { supported: true, low: { supported: true }, high: { supported: true } }, image_input: { supported: true } } },
    { id: 'claude-openrouter-4o', display_name: 'Gateway Opus', capabilities: { effort: { supported: true, max: { supported: true } }, image_input: { supported: false } } },
    { id: 'claude-gpt-5-4', display_name: 'Gateway GPT 5.4', capabilities: { effort: { supported: false } } },
    { id: 'claude-openrouter-3o', display_name: 'Gateway Opus 4.8' },
  ];
  writeFileSync(join(configDir, 'settings.json'), JSON.stringify({
    env: { ANTHROPIC_BASE_URL: gateway.url, ANTHROPIC_AUTH_TOKEN: 'gateway-test-token', CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1' },
    model: 'claude-gpt-5-6-sol',
  }));
  // No ACP executable: a discovery request must not spawn a query or try switching an Opus model.
  const adapter = new ClaudeAcpAdapter(['nonexistent-acp-for-gateway-test'], { CLAUDE_CONFIG_DIR: configDir, ANTHROPIC_BASE_URL: 'http://127.0.0.1:1' });
  const models = await adapter.models();
  assert.deepEqual(models.map(m => m.id), ['claude-openrouter-4o', 'claude-gpt-5-6-sol']);
  assert.deepEqual(models.map(m => m.efforts), [['max'], ['low', 'high']]);
  assert.deepEqual(models.map(m => m.images), [false, true]);
  assert.equal(models[1].isDefault, true);
  assert.equal(models[0].label, 'Gateway Opus');
  assert.equal(gateway.state.requests.length, 2, 'all discovery pages are read');
  assert.ok(gateway.state.requests.every(r => r.authorization === 'Bearer gateway-test-token'));
  assert.equal(await adapter.models(), models, 'reuse the discovered catalog until refreshed');
  gateway.state.models.push({ id: 'claude-gpt-6-sol', display_name: 'Gateway GPT 6' });
  assert.equal((await adapter.models(true)).length, 3, 'refresh discovers a new visible model');
});

test('Claude gateway discovery shows only GPT 5.5+ and openrouter-4o families', async t => {
  const ferryHome = mkdtempSync(join(tmpdir(), 'loochat-gateway-no-ferry-'));
  t.after(() => rmSync(ferryHome, { recursive: true, force: true }));
  const gateway = await modelGateway();
  t.after(() => gateway.close());
  gateway.state.models = [
    { id: 'claude-gpt-6-1-sol', display_name: 'Ferry GPT-6.1-Sol' },
    { id: 'claude-gpt-6-astra', display_name: 'Ferry GPT-6-Astra' },
    { id: 'claude-gpt-5-6-luna', display_name: 'Ferry GPT-5.6-Luna' },
    { id: 'claude-gpt-5-5', display_name: 'Ferry GPT-5.5' },
    { id: 'claude-gpt-5-4', display_name: 'Ferry GPT-5.4' },
    { id: 'claude-gpt-5-2', display_name: 'Ferry GPT-5.2' },
    { id: 'claude-openrouter-4o', display_name: 'Ferry Opus 5.5' },
    { id: 'claude-openrouter-4o-max[1m]', display_name: 'Ferry Opus 5.5 Max 1M' },
    { id: 'claude-openrouter-3o', display_name: 'Ferry Opus 4.8' },
    { id: 'claude-seed-evolving', display_name: 'Ferry Seed-Evolving' },
  ];
  const adapter = new ClaudeAcpAdapter(['nonexistent-acp-for-gateway-test'], {
    ANTHROPIC_BASE_URL: gateway.url, CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1', LOOCHAT_TRAEX_BIN: 'nonexistent-traex-for-gateway-test', FERRY_HOME: ferryHome,
  });
  assert.deepEqual((await adapter.models()).map(m => m.id), [
    'claude-openrouter-4o',
    'claude-openrouter-4o-max[1m]',
    'claude-gpt-6-1-sol',
    'claude-gpt-6-astra',
    'claude-gpt-5-6-luna',
    'claude-gpt-5-5',
  ]);
});

test('Claude gateway failures stay visible and are retried instead of caching an empty or native-only list', async t => {
  const gateway = await modelGateway();
  t.after(() => gateway.close());
  const adapter = new ClaudeAcpAdapter(['nonexistent-acp-for-gateway-test'], {
    ANTHROPIC_BASE_URL: gateway.url + '/v1', ANTHROPIC_API_KEY: 'gateway-test-key', CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1',
  });
  gateway.state.status = 503;
  await assert.rejects(adapter.models(), /503/);
  gateway.state.status = 200;
  gateway.state.models = [{ id: 'claude-gpt-5-5', display_name: 'Gateway GPT 5.5' }];
  assert.equal((await adapter.models())[0].id, 'claude-gpt-5-5');
  assert.equal(gateway.state.requests.at(-1).apiKey, 'gateway-test-key');
  gateway.state.status = 503;
  await assert.rejects(adapter.models(true), /503/);
  gateway.state.status = 200;
  gateway.state.models = [{ id: 'claude-openrouter-4o' }];
  assert.equal((await adapter.models())[0].id, 'claude-openrouter-4o');
});

test('local Ferry models carry upstream loads without inventing queue sizes, and refresh drops failed reads', async t => {
  // Use a fake metadata CLI: discovery must not run inference or query a real user's account.
  const gateway = await modelGateway();
  t.after(() => gateway.close());
  const configDir = mkdtempSync(join(tmpdir(), 'loochat-ferry-status-'));
  t.after(() => rmSync(configDir, { recursive: true, force: true }));
  const fake = fileURLToPath(new URL('./fixtures/fake-acp.mjs', import.meta.url));
  const cli = join(configDir, 'traex');
  writeFileSync(cli, `#!/bin/sh\nexec '${process.execPath}' '${fake}' "$@"\n`, { mode: 0o755 });
  const file = join(configDir, 'models.json');
  const seed = (load: number) => writeFileSync(file, JSON.stringify([
    { name: 'GPT-6.1-Sol', config_name: 'gpt-6.1-sol', _meta: { trae: { load: { percent: load } } } },
    { name: 'GPT-6-Astra', config_name: 'gpt-6-astra', _meta: { trae: { load: { percent: 0 } } } },
    { name: 'Ferry Opus 5.5', config_name: 'unrelated', _meta: { trae: { load: { percent: 99 } } } },
  ]));
  seed(184);
  const queueFile = join(configDir, 'queue-seen.json');
  const observedAt = Date.now() - 120_000;
  const seedQueue = (position: number) => writeFileSync(queueFile, JSON.stringify({
    'gpt-6.1-sol': { position, at: observedAt },
    'gpt-6-astra': { at: observedAt },
    'gpt-5.5': { position: 900, at: observedAt },
  }));
  seedQueue(550);
  gateway.state.models = [
    { id: 'claude-gpt-6-1-sol', display_name: 'Ferry GPT-6.1-Sol' },
    { id: 'claude-gpt-6-1-sol-max[1m]', display_name: 'Ferry GPT-6.1-Sol Max 1M' },
    { id: 'claude-gpt-6-astra', display_name: 'Ferry GPT-6-Astra' },
    { id: 'claude-openrouter-4o', display_name: 'Ferry Opus 5.5' },
    { id: 'claude-gpt-5-5', display_name: 'Another gateway GPT-5.5' },
  ];
  const env = {
    CLAUDE_CONFIG_DIR: configDir, ANTHROPIC_BASE_URL: gateway.url, ANTHROPIC_MODEL: 'claude-gpt-6-1-sol',
    CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1', LOOCHAT_TRAEX_BIN: cli, FAKE_TRAEX_MODELS_FILE: file,
    FAKE_TRAEX_MODELS_FAIL: '0', FERRY_HOME: configDir,
  };
  const adapter = new ClaudeAcpAdapter(['nonexistent-acp-for-gateway-test'], env);
  const models = await adapter.models();
  const standard = models.find(m => m.id === 'claude-gpt-6-1-sol')!;
  assert.equal(standard.loadPercent, 184);
  assert.equal(standard.isDefault, true);
  assert.equal(models.find(m => m.id.endsWith('[1m]'))?.loadPercent, 184);
  assert.equal(models.find(m => m.id.includes('astra'))?.loadPercent, 0);
  assert.equal(models[0].loadPercent, undefined, 'a similar display name is not a config match');
  assert.ok(models.filter(m => m.label.startsWith('Ferry ')).every(m => m.queueSize === null));
  assert.equal(models.at(-1)?.queueSize, undefined, 'unrelated models never receive local status');
  assert.equal(models.at(-1)?.queuePosition, undefined, 'even a matching cache entry cannot enrich a non-Ferry model');
  assert.equal(standard.queuePosition, 550);
  assert.equal(standard.queueObservedAt, observedAt);
  assert.equal(models.find(m => m.id.endsWith('[1m]'))?.queuePosition, 550);
  assert.equal(models.find(m => m.id.includes('astra'))?.queuePosition, 0, 'only an explicit observation means nonqueued');
  assert.equal(models[0].queuePosition, null, 'an unobserved model is not nonqueued');
  assert.equal(models[0].queueObservedAt, undefined);
  seed(23);
  seedQueue(37);
  assert.equal(await adapter.models(), models, 'reuse status with the catalog until explicitly refreshed');
  assert.equal(standard.queuePosition, 550);
  const refreshed = await adapter.models(true);
  assert.equal(refreshed.find(m => m.id === standard.id)?.loadPercent, 23);
  assert.equal(refreshed.find(m => m.id === standard.id)?.queuePosition, 37);
  env.FAKE_TRAEX_MODELS_FAIL = '1';
  const failed = await adapter.models(true);
  assert.equal(failed.length, models.length, 'metadata failure keeps the selectable catalog');
  assert.ok(failed.every(m => m.loadPercent === undefined), 'do not retain a previous load after failure');
  assert.equal(failed[0].queueSize, null, 'unavailable never becomes zero');
  assert.ok(failed.filter(m => m.label.startsWith('Ferry ')).every(m => m.loadError === true));
  assert.equal(failed.find(m => m.id === standard.id)?.queuePosition, 37, 'CLI failure must not hide a cached position');
  assert.equal(failed.at(-1)?.loadError, undefined, 'unrelated models keep their existing metadata');
  env.FAKE_TRAEX_MODELS_FAIL = '0';
  const recovered = await adapter.models(true);
  assert.equal(recovered.find(m => m.id === standard.id)?.loadPercent, 23);
  assert.ok(recovered.every(m => m.loadError === undefined), 'a recovered read clears the failure state');
  // Cache loss/corruption must clear observations without losing the catalog or healthy loads.
  for (const content of ['broken cache', JSON.stringify({ 'gpt-6.1-sol': { at: Date.now() + 60_000, position: 9 } })]) {
    writeFileSync(queueFile, content);
    const invalid = await adapter.models(true);
    assert.equal(invalid.length, models.length);
    const model = invalid.find(m => m.id === standard.id)!;
    assert.equal(model.loadPercent, 23);
    assert.equal(model.queuePosition, null);
    assert.equal(model.queueObservedAt, undefined);
  }
  rmSync(queueFile);
  const missing = await adapter.models(true);
  assert.equal(missing.find(m => m.id === standard.id)?.queuePosition, null);
  assert.equal(missing.find(m => m.id === standard.id)?.queueObservedAt, undefined);
  assert.equal(missing.find(m => m.id === standard.id)?.loadPercent, 23);
});

test('remote Ferry catalogs never receive the local TRAE account status', async t => {
  // A Ferry-looking label is insufficient: local metadata does not describe a remote provider.
  const ferryHome = mkdtempSync(join(tmpdir(), 'loochat-remote-ferry-'));
  t.after(() => rmSync(ferryHome, { recursive: true, force: true }));
  writeFileSync(join(ferryHome, 'queue-seen.json'), JSON.stringify({ 'gpt-6.1-sol': { position: 550, at: Date.now() } }));
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({
    data: [{ id: 'claude-gpt-6-1-sol', display_name: 'Ferry GPT-6.1-Sol' }], has_more: false,
  }), { headers: { 'content-type': 'application/json' } }));
  const adapter = new ClaudeAcpAdapter(['nonexistent-acp-for-gateway-test'], {
    ANTHROPIC_BASE_URL: 'https://gateway.test', CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1',
    LOOCHAT_TRAEX_BIN: 'nonexistent-traex-for-gateway-test', FERRY_HOME: ferryHome,
  });
  const [model] = await adapter.models();
  assert.equal(model.loadPercent, undefined);
  assert.equal(model.queueSize, undefined);
  assert.equal(model.queuePosition, undefined);
  assert.equal(model.queueObservedAt, undefined);
  assert.equal(model.loadError, undefined);
});

test('without Ferry installed, native and unrelated gateway model discovery keep their existing behavior', async t => {
  // No Ferry or TRAE binary/cache is required to use a native agent or a different gateway.
  const home = mkdtempSync(join(tmpdir(), 'loochat-native-no-ferry-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const env = {
    HOME: home, CLAUDE_CONFIG_DIR: home, FERRY_HOME: join(home, 'not-installed'),
    LOOCHAT_TRAEX_BIN: 'nonexistent-traex-for-gateway-test', CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '0',
  };
  const fake = fileURLToPath(new URL('./fixtures/fake-acp.mjs', import.meta.url));
  const native = new ClaudeAcpAdapter([process.execPath, fake], env);
  const nativeModels = await native.models();
  assert.ok(nativeModels.length > 0);
  assert.ok(nativeModels.every(model => model.queuePosition === undefined && model.queueObservedAt === undefined && model.loadError === undefined));
  const gateway = await modelGateway();
  t.after(() => gateway.close());
  gateway.state.models = [{ id: 'claude-gpt-6-1-sol', display_name: 'Gateway GPT-6.1-Sol' }];
  const adapter = new ClaudeAcpAdapter(['nonexistent-acp-for-gateway-test'], {
    ...env, ANTHROPIC_BASE_URL: gateway.url, CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1',
  });
  const [model] = await adapter.models();
  assert.equal(model.id, gateway.state.models[0].id);
  assert.equal(model.queuePosition, undefined);
  assert.equal(model.queueSize, undefined);
  assert.equal(model.loadError, undefined);
});

test('Claude gateway discovery preserves native slash command discovery', async t => {
  const gateway = await modelGateway();
  t.after(() => gateway.close());
  gateway.state.models = [{ id: 'claude-gpt-5-5' }];
  const fakeAcp = fileURLToPath(new URL('./fixtures/fake-acp.mjs', import.meta.url));
  const adapter = new ClaudeAcpAdapter([process.execPath, fakeAcp], {
    ANTHROPIC_BASE_URL: gateway.url, CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1',
  });
  await adapter.models();
  assert.deepEqual((await adapter.commands()).map(c => c.name), ['compact', 'review', 'goal', 'cost', 'stats', 'context']);
});

test('Claude on a gateway reads the plans and balances behind its own models from the gateway', async t => {
  const gateway = await modelGateway();
  t.after(() => gateway.close());
  const home = mkdtempSync(join(tmpdir(), 'loochat-gateway-quota-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  gateway.state.models = [{ id: 'zcode/GLM-5.3' }, { id: 'zcode/GLM-5.3-Flash' }, { id: 'deepseek/deepseek-flash' }];
  gateway.state.quotas = [
    { provider: 'zcode', name: 'ZCode', kind: 'subscription', plan: 'GLM Coding Pro', windows: [
      { name: '5 hours', used: 100, remaining: 0, resetsAt: '2026-11-13T05:46:34.931Z' },
      { name: 'Weekly', used: 37.02, remaining: 62.98, resetsAt: '2026-11-18T06:39:24.999Z' },
      { name: 'Search', unlimited: true, used: 0 },
    ] },
    { provider: 'deepseek', name: 'DeepSeek', kind: 'balance', windows: [], balance: '¥60.52' },
    // A plan the instance's catalog cannot reach is not this account's to show.
    { provider: 'workbuddy', name: 'WorkBuddy', kind: 'subscription', plan: 'Free plan', windows: [{ name: 'Credits', used: 10 }] },
  ];
  const adapter = new ClaudeAcpAdapter(['nonexistent-acp-for-gateway-test'], {
    HOME: home, CLAUDE_CONFIG_DIR: home, ANTHROPIC_BASE_URL: gateway.url, ANTHROPIC_AUTH_TOKEN: 'gateway-test-token', CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1',
  });
  const quota = await adapter.fetchQuota();
  const glm = ['zcode/GLM-5.3', 'zcode/GLM-5.3-Flash'];
  assert.deepEqual(quota.windows, [
    { label: '5H', scope: 'GLM Coding Pro', modelIds: glm, usedPercent: 100, resetsAt: '2026-11-13T05:46:34.931Z' },
    { label: '7D', scope: 'GLM Coding Pro', modelIds: glm, usedPercent: 37.02, resetsAt: '2026-11-18T06:39:24.999Z' },
  ]);
  assert.deepEqual(quota.balances, [{ scope: 'DeepSeek', amount: '¥60.52', modelIds: ['deepseek/deepseek-flash'] }]);
  assert.equal(quota.note, undefined);
  const read = gateway.state.requests.find(r => r.path === '/v1/magpie/quotas');
  assert.equal(read?.authorization, 'Bearer gateway-test-token', 'the gateway key is the only credential sent');
});

test('gateway quota names a failed read and tells two accounts of one plan apart', () => {
  const quota = gatewayQuota({ data: [
    { provider: 'zcode', plan: 'GLM Coding Pro', user: 'a', windows: [{ name: '5 hours', used: 10 }] },
    { provider: 'zcode', plan: 'GLM Coding Pro', user: 'b', windows: [{ name: '5 hours', used: 20 }] },
    { provider: 'deepseek', name: 'DeepSeek', windows: [], error: 'HTTP 401' },
  ] }, null, '2026-11-13T00:00:00.000Z');
  assert.deepEqual(quota.windows.map(w => [w.scope, w.usedPercent, w.modelIds]), [['GLM Coding Pro · a', 10, undefined], ['GLM Coding Pro · b', 20, undefined]]);
  assert.equal(quota.note, 'DeepSeek: HTTP 401');
  assert.equal(quota.balances, undefined);
  assert.deepEqual(['5 hours', 'Weekly', '3 days', 'Credits'].map(gatewayWindowLabel), ['5H', '7D', '3D', 'Credits']);
});

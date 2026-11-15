import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { TraexAcpAdapter, parseTraexModelStatus } from '../src/adapters/traex.js';

const fake = fileURLToPath(new URL('./fixtures/fake-acp.mjs', import.meta.url));

test('TRAE metadata keeps overload distinct from weekly quota and tolerates missing fields', () => {
  const parsed = parseTraexModelStatus(JSON.stringify([
    { name: 'A', _meta: { trae: { load: { percent: 236 }, weeklyQuota: { applies: true, remainingPercent: 99, resetTime: 1792339199 } } } },
    { name: 'B', _meta: { trae: { load: { percent: 0 }, weeklyQuota: { applies: false, usedPercent: 30 } } } },
    { name: 'C', _meta: { trae: { weeklyQuota: { applies: true, isDepleted: true, resetTime: 1e100 } } } },
    { name: 'D', _meta: { trae: { load: { percent: -1 }, weeklyQuota: { applies: true, usedPercent: '9' } } } },
    null,
  ]));
  assert.equal(parsed[0].loadPercent, 236);
  assert.deepEqual(parsed[0].quota, { label: '7D', scope: 'A', usedPercent: 1, resetsAt: '2026-10-18T15:59:59.000Z' });
  assert.equal(parsed[1].loadPercent, 0);
  assert.equal(parsed[1].quota, undefined);
  assert.equal(parsed[2].quota?.usedPercent, 100);
  assert.equal(parsed[2].quota?.resetsAt, undefined);
  assert.equal(parsed[3].loadPercent, undefined);
  assert.equal(parsed[3].quota, undefined);
  assert.throws(() => parseTraexModelStatus('{}'));
});

test('TRAE enriches ACP models, retains defaults, refreshes metadata and reports quota', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-traex-status-'));
  try {
    const file = join(root, 'models.json');
    const seed = (load: number, used: number) => writeFileSync(file, JSON.stringify([{ name: 'Big 1.0', config_name: 'big', _meta: { trae: { load: { percent: load }, weeklyQuota: { applies: true, usedPercent: used } } } }]));
    seed(184, 1);
    const adapter = new TraexAcpAdapter([process.execPath, fake], { FAKE_ACP_FLAVOR: 'traex', FAKE_TRAEX_MODELS_FILE: file });
    const models = await adapter.models();
    assert.deepEqual(models.map((m) => m.id), ['default', 'big', 'small', 'broken']);
    assert.equal(models[0].description, 'Big 1.0');
    assert.equal(models[0].isDefault, true);
    assert.equal(models[0].loadPercent, 184, 'the default alias carries its concrete model load');
    assert.equal(models[1].loadPercent, 184);
    assert.match(models[1].description!, /模型负载 184%.*每周额度剩余 99%/);
    assert.equal(adapter.capabilities.quota, true);
    assert.equal((await adapter.fetchQuota()).windows[0].usedPercent, 1);
    assert.equal((await adapter.fetchQuota()).windows[0].loadPercent, 184);
    seed(23, 10);
    const refreshed = await adapter.models();
    assert.equal(refreshed[0].loadPercent, 23);
    assert.equal(refreshed[1].loadPercent, 23);
    assert.match(refreshed[1].description!, /模型负载 23%.*每周额度剩余 90%/);
    assert.equal((await adapter.fetchQuota()).windows[0].usedPercent, 10);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('TRAE quota windows carry their own model\'s load, and none when the CLI reports none', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-traex-load-'));
  try {
    const file = join(root, 'models.json');
    const weeklyQuota = { applies: true, usedPercent: 5 };
    writeFileSync(file, JSON.stringify([
      { name: 'Big 1.0', config_name: 'big', _meta: { trae: { load: { percent: 184 }, weeklyQuota } } },
      { name: 'Free', _meta: { trae: { load: { percent: 60 } } } },
      { name: 'Small 1.0', config_name: 'small', _meta: { trae: { load: { percent: 100 }, weeklyQuota } } },
      { name: 'Quiet', _meta: { trae: { weeklyQuota } } },
    ]));
    const adapter = new TraexAcpAdapter([process.execPath, fake], { FAKE_ACP_FLAVOR: 'traex', FAKE_TRAEX_MODELS_FILE: file });
    const { windows } = await adapter.fetchQuota();
    assert.deepEqual(windows.map((w) => [w.scope, w.loadPercent]), [['Big 1.0', 184], ['Small 1.0', 100], ['Quiet', undefined]]);
    assert.equal('loadPercent' in windows[2], false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('old TRAE CLIs still offer ACP models when metadata fails; quota failure is explicit', async () => {
  const adapter = new TraexAcpAdapter([process.execPath, fake], { FAKE_ACP_FLAVOR: 'traex', FAKE_TRAEX_MODELS_FAIL: '1' });
  assert.equal((await adapter.models())[1].description, 'For hard work');
  await assert.rejects(adapter.fetchQuota());
});

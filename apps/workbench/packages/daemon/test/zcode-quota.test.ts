import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createCipheriv, createHash, randomBytes } from 'node:crypto';
import { ZcodeAdapter } from '../src/adapters/zcode.js';
import { zcodeQuotaWindows } from '../src/adapters/zcode-quota.js';
// @ts-ignore JavaScript fake provider shared with the browser test.
import { fakeZcodeQuota } from './fixtures/fake-zcode-quota.mjs';

for (const family of ['bigmodel', 'zai']) test(`zcode ${family}: reads only selected account, decrypts credentials, and reports quota`, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'zcode-quota-'));
  const server = await fakeZcodeQuota();
  const provider = `account:${family}-individual-coding-plan`;
  const identity = 'selected/account';
  const encrypt = (value: string) => {
    const iv = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', createHash('sha256').update('test-secret').digest(), iv);
    const bytes = Buffer.concat([cipher.update(value), cipher.final()]);
    return `enc:v1:${iv.toString('base64url')}.${cipher.getAuthTag().toString('base64url')}.${bytes.toString('base64url')}`;
  };
  try {
    const configDir = join(dir, '.zcode', 'v2');
    await mkdir(configDir, { recursive: true });
    const config = join(configDir, 'provider_config.json');
    const credentials = join(configDir, 'credentials.json');
    await writeFile(config, JSON.stringify({ config: { defaultModelSelection: { providerId: provider } } }));
    const original = JSON.stringify({ [`account-provider:${provider}:identity`]: encrypt(identity), [`account-provider:coding-plan:${provider}:account:${encodeURIComponent(identity)}:api-key`]: encrypt('fixture-key'), [`account-provider:coding-plan:${provider}:account:other:api-key`]: encrypt('wrong-account') });
    await writeFile(credentials, original);
    const adapter = new ZcodeAdapter(['unused'], { ZCODE_DATA_BASE_DIR: dir, ZCODE_PERSONAL_PROVIDER_CONFIG_FILE: config, ZCODE_CREDENTIAL_SECRET: 'test-secret', BIGMODEL_API_BASE_URL: server.url, ZAI_BUSINESS_BASE_URL: server.url, ZCODE_BIGMODEL_USAGE_API_KEY: '', BIGMODEL_USAGE_API_KEY: '', ZCODE_BIGMODEL_USAGE_QUOTA_URL: '', BIGMODEL_USAGE_QUOTA_URL: '' });
    assert.equal(adapter.capabilities.quota, true);
    const quota = await adapter.fetchQuota();
    assert.deepEqual(quota.windows.map((w) => [w.label, w.usedPercent]), [['5H', 12], ['7D', 98]]);
    assert.ok(quota.windows[1].resetsAt);
    assert.equal(server.state.requests[0].authorization, 'fixture-key');
    assert.equal(server.state.requests[0].url, '/api/monitor/usage/quota/limit');
    server.state.status = 401;
    assert.match((await adapter.fetchQuota()).note!, /失效/);
    server.state.status = 200;
    server.state.body = { code: 500, msg: 'fixture-key should never be displayed' };
    assert.equal(JSON.stringify(await adapter.fetchQuota()).includes('fixture-key'), false);
    await writeFile(credentials, '{}');
    const count = server.state.requests.length;
    assert.match((await adapter.fetchQuota()).note!, /凭据/);
    assert.equal(server.state.requests.length, count);
    await writeFile(credentials, original);
    await writeFile(config, JSON.stringify({ config: { defaultModelSelection: { providerId: 'custom' } } }));
    assert.match((await adapter.fetchQuota()).note!, /未支持/);
    assert.equal(server.state.requests.length, count);
    assert.equal(await readFile(credentials, 'utf8'), original);
  } finally { await server.close(); await rm(dir, { recursive: true, force: true }); }
});

test('zcode quota preserves zero, bounds percentages and rejects malformed data', () => {
  assert.deepEqual(zcodeQuotaWindows({ data: { limits: [{ type: 'TOKENS_LIMIT', percentage: 0 }, { type: 'TIME_LIMIT', percentage: 120 }, { type: 'TOKENS_LIMIT', percentage: '50' }] } }).map((w) => w.usedPercent), [0, 100]);
  assert.throws(() => zcodeQuotaWindows({ data: {} }));
});

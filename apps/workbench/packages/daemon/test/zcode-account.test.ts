import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { createCipheriv, createHash, randomBytes } from 'node:crypto';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadZcodeAccounts } from '../src/adapters/zcode-account.js';
import { ZcodeAdapter } from '../src/adapters/zcode.js';

const here = dirname(fileURLToPath(import.meta.url));
const command = [process.execPath, join(here, 'fixtures/fake-zcode.mjs')];
const hooks = { emit: () => {}, requestApproval: async () => ({ cancelled: true as const }) };
const input = { text: 'hello', images: [] };
const hash = (s: string) => createHash('sha256').update(s).digest('hex');
const encrypt = (s: string) => {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', createHash('sha256').update('test-secret').digest(), iv);
  const bytes = Buffer.concat([cipher.update(s), cipher.final()]);
  return `enc:v1:${iv.toString('base64url')}.${cipher.getAuthTag().toString('base64url')}.${bytes.toString('base64url')}`;
};

async function fixture(family = 'bigmodel') {
  const dir = await mkdtemp(join(tmpdir(), 'zcode-account-'));
  const configDir = join(dir, '.zcode', 'v2');
  await mkdir(configDir, { recursive: true });
  const provider = `account:${family}-individual-coding-plan`;
  const builtin = join(dir, 'builtin.json');
  const credentials = join(configDir, 'credentials.json');
  const config = join(configDir, 'provider_config.json');
  const identity = 'fixture/account';
  const contents = (key: string, account = identity) => JSON.stringify({
    [`account-provider:${provider}:identity`]: encrypt(account),
    [`account-provider:coding-plan:${provider}:account:${encodeURIComponent(account)}:api-key`]: encrypt(key),
  });
  await writeFile(credentials, contents('fixture-key'));
  await writeFile(builtin, JSON.stringify({ schemaVersion: 1, revision: 7, config: { providerConfigRules: { providerRules: [{ providerId: provider, config: { access: { type: 'zhipu-account', mode: 'individual-coding-plan', accountType: family }, builtinModelIds: ['small', 'large'] } }] } } }));
  await writeFile(config, JSON.stringify({ schemaVersion: 1, config: { defaultModelSelection: { providerId: provider, modelId: 'small' } } }));
  const env = {
    ZCODE_DATA_BASE_DIR: dir, ZCODE_BUILTIN_PROVIDER_CONFIG_FILE: builtin, ZCODE_PERSONAL_PROVIDER_CONFIG_FILE: config,
    ZCODE_CREDENTIAL_SECRET: 'test-secret', FAKE_ZCODE_ACCOUNT_PROVIDER: provider, FAKE_ZCODE_API_KEY: 'fixture-key', FAKE_ZCODE_LOG: join(dir, 'wire.jsonl'),
  };
  return { dir, env, provider, builtin, credentials, config, contents, clean: () => rm(dir, { recursive: true, force: true }) };
}

for (const family of ['bigmodel', 'zai']) test(`zcode ${family} account: discovers, selects, resumes and authenticates without changing config`, async () => {
  const f = await fixture(family);
  const original = await Promise.all([f.credentials, f.config, f.builtin].map(p => readFile(p, 'utf8')));
  const adapter = new ZcodeAdapter(command, f.env);
  let session;
  try {
    const models = await adapter.models();
    assert.equal(models.length, 2);
    assert.equal(models[0].isDefault, true);
    assert.deepEqual(models[0].efforts, ['low', 'high']);
    assert.equal(JSON.parse(models[0].id)[0], f.provider);
    session = await adapter.start({ cwd: f.dir, model: models[0].id, effort: 'high' });
    assert.equal((await session.prompt(input, hooks)).stopReason, 'end_turn');
    const id = session.agentSessionId;
    await session.close();
    session = await adapter.start({ cwd: f.dir, resumeId: id, model: models[1].id });
    assert.equal((await session.prompt(input, hooks)).stopReason, 'end_turn');
    const logs = await readFile(f.env.FAKE_ZCODE_LOG, 'utf8');
    assert.equal(logs.includes('fixture-key'), false);
    assert.equal(logs.includes('fixture/account'), false);
    const methods = logs.trim().split('\n').map(l => JSON.parse(l).method).filter(Boolean);
    assert.equal(methods[0], 'provider/updateAccountConfig');
    assert.deepEqual(await Promise.all([f.credentials, f.config, f.builtin].map(p => readFile(p, 'utf8'))), original);
    await writeFile(f.credentials, '{}');
    await assert.rejects(session.prompt(input, hooks), /登录已失效/);
  } finally { await session?.close(); await f.clean(); }
});

test('zcode account callbacks reject unrelated requests, reread rotated keys, and revoke switched accounts', async () => {
  const f = await fixture();
  try {
    const accounts = (await loadZcodeAccounts(f.env, f.dir))!;
    const request = { sessionId: 's', providerId: f.provider, modelSelection: { providerId: f.provider }, accountAccess: { mode: 'individual-coding-plan' } };
    for (const invalid of [{ ...request, sessionId: 'other' }, { ...request, providerId: 'custom' }, { ...request, modelSelection: { providerId: 'other' } }, { ...request, accountAccess: { mode: 'team' } }]) {
      assert.equal((await accounts.authorize(invalid, 's') as any).headersApplied, false);
    }
    await writeFile(f.credentials, f.contents('rotated-key'));
    assert.deepEqual(await accounts.authorize(request, 's'), { headersApplied: true, requestAuth: { apiKey: 'rotated-key' } });
    await writeFile(f.credentials, f.contents('new-account-key', 'other-account'));
    const result = await accounts.authorize(request, 's');
    assert.equal((result as any).headersApplied, false);
    assert.equal(JSON.stringify(result).includes('new-account-key'), false);
  } finally { await f.clean(); }
});

test('zcode does not treat account receipt as applied when the native registry rejects the revision', async () => {
  const f = await fixture();
  try {
    const adapter = new ZcodeAdapter(command, { ...f.env, FAKE_ZCODE_ACCOUNT_STALE: '1' });
    assert.deepEqual(await adapter.models(), []);
    await assert.rejects(adapter.start({ cwd: f.dir }), /没有可用模型/);
  } finally { await f.clean(); }
});

test('zcode locates a unique endpoint-specific catalog and refuses ambiguous versions', async () => {
  const f = await fixture();
  try {
    const platform = `${process.platform === 'win32' ? 'windows' : process.platform}-${process.arch === 'x64' ? 'x86_64' : process.arch === 'arm64' ? 'aarch64' : process.arch}`;
    const root = join(f.dir, '.zcode/v2/runtime/provider', platform);
    const suffix = `endpoint-${hash('https://zcode.z.ai').slice(0, 32)}`;
    const candidate = join(root, 'version-one', suffix, 'zcode-builtin.json');
    await mkdir(dirname(candidate), { recursive: true });
    await writeFile(candidate, await readFile(f.builtin));
    const env = { ...f.env, ZCODE_BUILTIN_PROVIDER_CONFIG_FILE: '' };
    assert.equal((await loadZcodeAccounts(env, f.dir))?.environment.ZCODE_BUILTIN_PROVIDER_CONFIG_FILE, candidate);
    assert.equal((await loadZcodeAccounts({ ...env, ZCODE_BASE_URL: ' https://zcode.z.ai/path ', ZCODE_ENDPOINT_ORIGIN: 'https://unused.invalid' }, f.dir))?.environment.ZCODE_BUILTIN_PROVIDER_CONFIG_FILE, candidate);
    const other = join(root, 'version-two', suffix, 'zcode-builtin.json');
    await mkdir(dirname(other), { recursive: true });
    await writeFile(other, await readFile(f.builtin));
    await assert.rejects(loadZcodeAccounts(env, f.dir), /无法唯一确定/);
    assert.equal((await loadZcodeAccounts({ ...env, ZCODE_APP_VERSION: 'version-two' }, f.dir))?.environment.ZCODE_BUILTIN_PROVIDER_CONFIG_FILE, other);
  } finally { await f.clean(); }
});

test('zcode preserves API-only configurations and reports corrupt account credentials without disclosing them', async () => {
  const f = await fixture();
  try {
    await writeFile(f.credentials, '{}');
    assert.equal(await loadZcodeAccounts(f.env, f.dir), null);
    await writeFile(f.credentials, JSON.stringify({ [`account-provider:${f.provider}:identity`]: 'enc:v1:bad.secret.bytes' }));
    await assert.rejects(loadZcodeAccounts(f.env, f.dir), error => error instanceof Error && /无法解密/.test(error.message) && !error.message.includes('secret'));
  } finally { await f.clean(); }
});

import { startAgyLogin } from '../src/adapters/agy-account.js';
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import type { Server } from 'node:http';
import { AgentRegistry } from '../src/agents.js';
import { startClaudeLogin } from '../src/adapters/claude-account.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

const fixture = fileURLToPath(new URL('./fixtures/fake-claude-auth.mjs', import.meta.url));
const acp = fileURLToPath(new URL('./fixtures/fake-acp.mjs', import.meta.url));
const token = 'fixture-account-tests';
let root: string, source: string, base: string, registry: AgentRegistry, server: Server, store: Store, manager: ThreadManager;
async function api(path: string, method = 'GET', body?: unknown, authenticated = true) {
  return fetch(base + path, { method, headers: { 'Content-Type': 'application/json', ...(authenticated ? { Authorization: `Bearer ${token}` } : {}) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}
async function waitFor<T>(fn: () => T | Promise<T>, accept: (value: T) => boolean): Promise<T> {
  for (let i = 0; i < 150; i++) { const result = await fn(); if (accept(result)) return result; await new Promise(r => setTimeout(r, 30)); }
  throw new Error('timed out');
}
before(async () => {
  root = mkdtempSync(join(tmpdir(), 'loochat-account-tests-')); source = join(root, 'original'); mkdirSync(source);
  mkdirSync(join(source, 'skills')); mkdirSync(join(source, 'skills', 'sample'));
  writeFileSync(join(source, 'skills', 'sample', 'SKILL.md'), 'Original skill');
  symlinkSync(join(source, 'skills', 'sample'), join(source, 'skills', 'linked'));
  writeFileSync(join(source, 'settings.json'), JSON.stringify({ model: 'sonnet', apiKeyHelper: 'secret-helper', env: { ANTHROPIC_API_KEY: 'old-key', CLAUDE_CONFIG_DIR: source, KEEP: 'yes' } }));
  writeFileSync(join(source, '.claude.json'), JSON.stringify({ oauthAccount: { email: 'old@example.com' }, projects: { private: {} }, mcpServers: { docs: { command: 'docs' } } }));
  writeFileSync(join(source, '.credentials.json'), 'original credentials');
  mkdirSync(join(source, 'projects')); writeFileSync(join(source, 'projects', 'old.jsonl'), 'old conversation');
  registry = new AgentRegistry([{ id: 'claude', adapter: 'claude', command: [process.execPath, acp], env: { CLAUDE_CONFIG_DIR: source, LOOCHAT_CLAUDE_BIN: fixture } }], join(root, 'agents.json'));
  store = new Store(root);
  manager = new ThreadManager(store, registry.adapters, [root], new AttachmentStore(root), () => registry.list().map(item => ({ ...item, activeLogin: registry.activeLogin(item.id) })));
  server = createHttpServer({ manager, registry, token, webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
after(async () => { registry?.dispose(); await manager?.shutdown(); await new Promise<void>(resolve => server?.close(() => resolve())); store?.close(); rmSync(root, { recursive: true, force: true }); });

test('account API copies configuration into a private independent directory and persists it', async () => {
  assert.equal((await api('/api/agents/accounts', 'POST', { adapter: 'claude' }, false)).status, 401);
  const supported = await (await api('/api/agents/supported')).json() as any[];
  assert.equal(supported.find(item => item.adapter === 'claude').capabilities.createAccount, true);
  const res = await api('/api/agents/accounts', 'POST', { adapter: 'claude', label: 'Second' });
  assert.equal(res.status, 201);
  const instance = await res.json() as any;
  assert.equal(instance.id, 'claude-2'); assert.equal(instance.account, null);
  const dir = instance.env.CLAUDE_CONFIG_DIR;
  assert.notEqual(dir, source); assert.equal(statSync(dir).mode & 0o777, 0o700);
  assert.equal(existsSync(join(dir, '.credentials.json')), false); assert.equal(existsSync(join(dir, 'projects')), false);
  assert.deepEqual(JSON.parse(readFileSync(join(dir, 'settings.json'), 'utf8')), { model: 'sonnet', env: { KEEP: 'yes' } });
  assert.deepEqual(JSON.parse(readFileSync(join(dir, '.claude.json'), 'utf8')), { mcpServers: { docs: { command: 'docs' } } });
  assert.equal(instance.env.ANTHROPIC_API_KEY, '');
  writeFileSync(join(dir, 'skills', 'linked', 'SKILL.md'), 'Changed copy');
  assert.equal(readFileSync(join(source, 'skills', 'sample', 'SKILL.md'), 'utf8'), 'Original skill');
  assert.equal(JSON.parse(readFileSync(join(root, 'agents.json'), 'utf8')).agents.find((a: any) => a.id === instance.id).env.CLAUDE_CONFIG_DIR, dir);
  const agents = await (await api('/api/agents')).json() as any[];
  assert.equal(agents.find(a => a.id === instance.id).login, 'wizard');
  assert.equal(agents.find(a => a.id === instance.id).account, null);
});

test('invalid sources fail and concurrent creations allocate different ids', async () => {
  assert.equal((await api('/api/agents/accounts', 'POST', null)).status, 400);
  assert.equal((await api('/api/agents/accounts', 'POST', { adapter: 'claude', label: 12 })).status, 400);
  assert.equal((await api('/api/agents/accounts', 'POST', { adapter: 'unknown' })).status, 400);
  assert.equal((await api('/api/agents/accounts', 'POST', { adapter: 'pi', sourceId: 'claude' })).status, 400);
  const results = await Promise.all([registry.createAccount({ adapter: 'claude' }), registry.createAccount({ adapter: 'claude' })]);
  assert.notEqual(results[0].id, results[1].id);
  assert.notEqual(results[0].env?.CLAUDE_CONFIG_DIR, results[1].env?.CLAUDE_CONFIG_DIR);
});

test('copy errors roll back the directory and registration', async () => {
  const before = registry.list().length;
  const dirs = readdirSync(join(root, 'agent-accounts'));
  writeFileSync(join(source, 'settings.local.json'), '{broken');
  try { await assert.rejects(registry.createAccount({ adapter: 'claude' })); }
  finally { rmSync(join(source, 'settings.local.json')); }
  assert.equal(registry.list().length, before);
  assert.deepEqual(readdirSync(join(root, 'agent-accounts')), dirs);
});

test('login API streams a link, accepts a code, verifies native status and updates the account', async () => {
  const path = '/api/agents/claude-2/login';
  assert.equal((await api(path, 'POST', undefined, false)).status, 401);
  const login = await (await api(path, 'POST')).json() as any;
  assert.equal((await (await api(path, 'POST')).json() as any).id, login.id, 'double clicks reuse the process');
  const sessionPath = `${path}/${login.id}`;
  const state = await waitFor(async () => (await api(sessionPath)).json() as any, item => item.status === 'waiting');
  assert.match(state.url, /^https:\/\/claude.com\/cai\/oauth\/authorize\?/);
  assert.equal((await api(sessionPath)).headers.get('Cache-Control'), 'no-store');
  assert.equal((await api(sessionPath, 'POST', { code: 'code\ncommand' })).status, 400);
  assert.equal((await api(sessionPath, 'POST', { code: 'test-code#test-state' })).status, 200);
  assert.equal((await api(sessionPath, 'POST', { code: 'test-code#test-state' })).status, 409);
  const done = await waitFor(async () => (await api(sessionPath)).json() as any, item => item.status === 'success');
  assert.equal(done.url, undefined); assert.equal(JSON.stringify(done).includes('fixture-only'), false);
  assert.equal(registry.get('claude-2').account, 'new@example.com');
  assert.equal(readFileSync(join(source, '.credentials.json'), 'utf8'), 'original credentials');
  assert.equal(JSON.parse(readFileSync(join(root, 'agents.json'), 'utf8')).agents.find((a: any) => a.id === 'claude-2').account, 'new@example.com');
});

test('bad codes can restart; stale sessions and cancelled sessions cannot accept codes', async () => {
  const path = '/api/agents/claude-3/login';
  const first = await (await api(path, 'POST')).json() as any;
  await waitFor(async () => (await api(`${path}/${first.id}`)).json() as any, item => item.status === 'waiting');
  await api(`${path}/${first.id}`, 'POST', { code: 'wrong' });
  const failed = await waitFor(async () => (await api(`${path}/${first.id}`)).json() as any, item => item.status === 'error');
  assert.equal(failed.error, 'failed');
  const second = await (await api(path, 'POST')).json() as any;
  assert.notEqual(second.id, first.id);
  assert.equal((await api(`${path}/${first.id}`)).status, 404);
  assert.equal((await api(`${path}/${second.id}`, 'DELETE')).status, 204);
  assert.equal((await api(`${path}/${second.id}`, 'POST', { code: 'test-code#test-state' })).status, 409);
  assert.equal((await (await api(`${path}/${second.id}`)).json() as any).status, 'cancelled');
  assert.equal(registry.activeLogin('claude-3'), undefined);
});

test('native login times out, refuses unsuccessful status, and handles missing CLI', async () => {
  const expired = startClaudeLogin(fixture, source, {}, async () => { assert.fail('expired login succeeded'); }, 50);
  await waitFor(() => expired.state(), state => state.status === 'error'); assert.equal(expired.state().error, 'expired');
  const missing = startClaudeLogin(join(root, 'missing'), source, {}, async () => {});
  await waitFor(() => missing.state(), state => state.status === 'error'); assert.equal(missing.state().error, 'unavailable');
  const dir = join(root, 'bad-status'); mkdirSync(dir);
  const bad = startClaudeLogin(fixture, dir, { FAKE_AUTH_BAD_STATUS: '1' }, async () => { assert.fail('unverified login succeeded'); });
  await waitFor(() => bad.state(), state => state.status === 'waiting'); bad.submit('test-code#test-state');
  await waitFor(() => bad.state(), state => state.status === 'error'); assert.equal(bad.state().error, 'verification');
});


test('concurrent account metadata writes remain consistent with disk', async () => {
  await Promise.all([registry.update('claude-2', { label: 'Renamed' }), registry.update('claude-3', { hidden: true })]);
  const saved = JSON.parse(readFileSync(join(root, 'agents.json'), 'utf8')).agents;
  assert.deepEqual(saved, registry.list());
  assert.equal(registry.get('claude-2').label, 'Renamed');
  assert.equal(registry.get('claude-3').hidden, true);
});

test('persistence errors leave no new account directory or live registration', async () => {
  const broken = join(root, 'unwritable-file'); mkdirSync(broken);
  const failing = new AgentRegistry([registry.get('claude')], broken, [], root);
  const dirs = readdirSync(join(root, 'agent-accounts'));
  await assert.rejects(failing.createAccount({ adapter: 'claude' }));
  assert.equal(failing.list().length, 1);
  assert.deepEqual(readdirSync(join(root, 'agent-accounts')), dirs);
  assert.equal(readdirSync(root).some(name => name.startsWith('unwritable-file.') && name.endsWith('.tmp')), false);
});

test('agy account API isolates native home, copies portable settings, and keeps credentials and trust behind', async () => {
  const home = join(root, "agy's original home");
  const config = join(home, '.gemini', 'antigravity-cli');
  mkdirSync(join(config, 'skills', 'sample'), { recursive: true });
  writeFileSync(join(config, 'skills', 'sample', 'SKILL.md'), 'Original agy skill');
  symlinkSync(join(config, 'skills', 'sample'), join(config, 'skills', 'linked'));
  mkdirSync(join(home, '.agents', 'skills', 'shared'), { recursive: true });
  writeFileSync(join(home, '.agents', 'skills', 'shared', 'SKILL.md'), 'Shared skill');
  writeFileSync(join(config, 'settings.json'), JSON.stringify({ colorScheme: 'dark', trustedWorkspaces: ['/private'], apiKey: 'original-key', auth: { token: 'original-token' } }));
  mkdirSync(join(home, '.gemini', 'config'));
  writeFileSync(join(home, '.gemini', 'config', 'mcp_config.json'), JSON.stringify({ mcpServers: { docs: { command: 'docs' } }, oauthTokens: { docs: 'old-mcp-token' } }));
  writeFileSync(join(config, 'antigravity-oauth-token'), 'original agy credentials');
  mkdirSync(join(config, 'conversations')); writeFileSync(join(config, 'conversations', 'original.db'), 'original session');
  const command = [process.execPath, fileURLToPath(new URL('./fixtures/fake-agy.mjs', import.meta.url))];
  await registry.add({ id: 'agy', adapter: 'agy', command, env: { HOME: home, LOOCHAT_AGY_HOME: config,
    GOOGLE_API_KEY: 'old-api-key', GEMINI_API_KEY: 'old-gemini-key', GOOGLE_APPLICATION_CREDENTIALS: '/old/credentials',
    FAKE_AGY_ACCOUNT_CHECK: '1', FAKE_LIVE_USAGE: '1' } });
  const supported = await (await api('/api/agents/supported')).json() as any[];
  assert.equal(supported.find(item => item.adapter === 'agy').capabilities.createAccount, true);
  const response = await api('/api/agents/accounts', 'POST', { adapter: 'agy', sourceId: 'agy', label: 'Independent agy' });
  assert.equal(response.status, 201);
  const instance = await response.json() as any;
  assert.equal(instance.id, 'agy-2'); assert.equal(instance.account, null);
  assert.deepEqual(instance.command, command);
  const directory = instance.env.LOOCHAT_AGY_HOME;
  assert.equal(directory, join(instance.env.HOME, '.gemini', 'antigravity-cli'));
  assert.notEqual(instance.env.HOME, home);
  assert.equal(statSync(instance.env.HOME).mode & 0o777, 0o700);
  assert.equal(instance.env.XDG_CONFIG_HOME, join(instance.env.HOME, '.config'));
  for (const key of ['GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS']) assert.equal(instance.env[key], '');
  assert.equal(existsSync(join(directory, 'antigravity-oauth-token')), false);
  assert.equal(existsSync(join(directory, 'conversations')), false);
  assert.deepEqual(JSON.parse(readFileSync(join(directory, 'settings.json'), 'utf8')), { colorScheme: 'dark' });
  assert.deepEqual(JSON.parse(readFileSync(join(instance.env.HOME, '.gemini', 'config', 'mcp_config.json'), 'utf8')), { mcpServers: { docs: { command: 'docs' } } });
  writeFileSync(join(directory, 'skills', 'linked', 'SKILL.md'), 'Changed new agy skill');
  assert.equal(readFileSync(join(config, 'skills', 'sample', 'SKILL.md'), 'utf8'), 'Original agy skill');
  assert.equal(readFileSync(join(instance.env.HOME, '.agents', 'skills', 'shared', 'SKILL.md'), 'utf8'), 'Shared skill');
  const agents = await (await api('/api/agents')).json() as any[];
  assert.equal(agents.find(a => a.id === 'agy-2').login, 'wizard');
  const before = readdirSync(join(root, 'agent-accounts'));
  writeFileSync(join(config, 'settings.json'), '{broken');
  try { await assert.rejects(registry.createAccount({ adapter: 'agy', sourceId: 'agy' })); }
  finally { writeFileSync(join(config, 'settings.json'), '{}'); }
  assert.deepEqual(readdirSync(join(root, 'agent-accounts')), before);
  assert.equal(readFileSync(join(config, 'antigravity-oauth-token'), 'utf8'), 'original agy credentials');
});

test('agy native login accepts a callback URL, persists identity, and uses that account for quota, models and resumed turns', async () => {
  const path = '/api/agents/agy-2/login';
  const login = await (await api(path, 'POST')).json() as any;
  assert.equal((await (await api(path, 'POST')).json() as any).id, login.id);
  const sessionPath = `${path}/${login.id}`;
  const waiting = await waitFor(async () => (await api(sessionPath)).json() as any, item => item.status === 'waiting');
  assert.equal(new URL(waiting.url).hostname, 'accounts.google.com');
  assert.equal(new URL(waiting.url).searchParams.get('redirect_uri'), 'https://antigravity.google/oauth-callback');
  assert.equal(waiting.input, 'code-or-url');
  const listed = await api('/api/agents');
  assert.equal(listed.headers.get('Cache-Control'), 'no-store');
  const resumed = (await listed.json() as any[]).find(item => item.id === 'agy-2').activeLogin;
  assert.deepEqual(resumed, waiting, 'another client can recover the same pending login');
  assert.equal(JSON.stringify(resumed).includes('fixture-agy-access'), false);
  assert.equal((await api('/api/agents', 'GET', undefined, false)).status, 401);
  assert.equal(JSON.stringify(JSON.parse(readFileSync(join(root, 'agents.json'), 'utf8'))).includes('activeLogin'), false);

  assert.equal((await api(sessionPath, 'POST', { code: 'code\ncommand' })).status, 400);
  assert.equal((await api(sessionPath, 'POST', { code: 'https://antigravity.google/oauth-callback?code=test-agy-code&state=fixture' })).status, 200);
  const done = await waitFor(async () => (await api(sessionPath)).json() as any, item => item.status === 'success');
  assert.equal(done.url, undefined); assert.doesNotMatch(JSON.stringify(done), /fixture-agy/);
  assert.equal(registry.get('agy-2').account, 'agy-new@example.com');
  assert.equal(registry.activeLogin('agy-2'), undefined);
  assert.equal((await (await api('/api/agents')).json() as any[]).find(item => item.id === 'agy-2').activeLogin, undefined);
  const saved = JSON.parse(readFileSync(join(root, 'agents.json'), 'utf8')).agents.find((a: any) => a.id === 'agy-2');
  assert.equal(saved.account, 'agy-new@example.com');
  assert.deepEqual(saved.env, registry.get('agy-2').env);
  const adapter = registry.adapters.get('agy-2')!;
  assert.ok((await adapter.models()).length > 0);
  assert.ok((await adapter.fetchQuota!()).windows.length > 0);
  const hooks = { emit: () => {}, requestApproval: async () => ({ cancelled: true as const }) };
  let session = await adapter.start({ cwd: root });
  try { assert.equal((await session.prompt({ text: 'live usage', images: [] }, hooks)).stopReason, 'end_turn'); }
  finally { await session.close(); }
  const id = session.agentSessionId!;
  session = await adapter.start({ cwd: root, resumeId: id });
  try { assert.equal((await session.prompt({ text: 'again', images: [] }, hooks)).stopReason, 'end_turn'); }
  finally { await session.close(); }
  const config = saved.env.LOOCHAT_AGY_HOME;
  const location = await adapter.sessionLocation!(id, root);
  assert.equal(location.configDir, config); assert.ok(location.sessionPath?.startsWith(config));
  assert.match(adapter.resumeCommand!(id), /env HOME=/);
  assert.doesNotMatch(adapter.resumeCommand!(id), /old-api-key|old-gemini-key/);
  assert.match(adapter.resumeCommand!(id), /GOOGLE_API_KEY=''/);
  assert.equal(readFileSync(join(registry.get('agy').env!.LOOCHAT_AGY_HOME, 'antigravity-oauth-token'), 'utf8'), 'original agy credentials');
});

test('agy login handles invalid codes, cancellation, timeout and native success without saved credentials', async () => {
  const instance = await registry.createAccount({ adapter: 'agy', sourceId: 'agy' });
  const command = instance.command!;
  const make = (env = instance.env!, timeout?: number) => startAgyLogin(command, env, async () => { assert.fail('invalid login succeeded'); }, timeout);
  const invalid = make();
  await waitFor(() => invalid.state(), s => s.status === 'waiting'); invalid.submit('wrong');
  await waitFor(() => invalid.state(), s => s.status === 'error'); assert.equal(invalid.state().error, 'failed');
  const cancelled = make();
  await waitFor(() => cancelled.state(), s => s.status === 'waiting'); cancelled.cancel();
  assert.equal(cancelled.state().status, 'cancelled'); assert.throws(() => cancelled.submit('test-agy-code'));
  const expired = make(instance.env!, 50);
  await waitFor(() => expired.state(), s => s.status === 'error'); assert.equal(expired.state().error, 'expired');
  const missing = startAgyLogin([join(root, 'missing-agy')], instance.env!, async () => {});
  await waitFor(() => missing.state(), s => s.status === 'error'); assert.equal(missing.state().error, 'unavailable');
  const unverified = make({ ...instance.env!, FAKE_AGY_AUTH_BAD_STATUS: '1' });
  await waitFor(() => unverified.state(), s => s.status === 'waiting'); unverified.submit('test-agy-code');
  await waitFor(() => unverified.state(), s => s.status === 'error'); assert.equal(unverified.state().error, 'verification');
});

test('agy account API treats empty optional configuration as absent without changing the source', async () => {
  const source = registry.get('agy').env!;
  const settings = join(source.LOOCHAT_AGY_HOME, 'settings.json');
  const mcp = join(source.HOME, '.gemini', 'config', 'mcp_config.json');
  const original = [readFileSync(settings, 'utf8'), readFileSync(mcp, 'utf8')];
  try {
    for (const [settingsText, mcpText] of [
      ['{}', ''], ['', '{"mcpServers":{"docs":{"command":"docs"}}}'], [' \n\t', '\n '],
    ]) {
      writeFileSync(settings, settingsText); writeFileSync(mcp, mcpText);
      const response = await api('/api/agents/accounts', 'POST', { adapter: 'agy', sourceId: 'agy' });
      assert.equal(response.status, 201);
      const instance = await response.json() as any;
      assert.equal(instance.account, null);
      assert.equal(existsSync(join(instance.env.LOOCHAT_AGY_HOME, 'antigravity-oauth-token')), false);
      assert.equal(existsSync(join(instance.env.LOOCHAT_AGY_HOME, 'settings.json')), !!settingsText.trim());
      const copiedMcp = join(instance.env.HOME, '.gemini', 'config', 'mcp_config.json');
      assert.equal(existsSync(copiedMcp), !!mcpText.trim());
      if (mcpText.trim()) assert.deepEqual(JSON.parse(readFileSync(copiedMcp, 'utf8')), JSON.parse(mcpText));
      assert.equal(readFileSync(settings, 'utf8'), settingsText);
      assert.equal(readFileSync(mcp, 'utf8'), mcpText);
    }
  } finally { writeFileSync(settings, original[0]); writeFileSync(mcp, original[1]); }
});

test('signed-out model catalog offers typed login recovery and publishes models for the same account', async () => {
  const home = join(root, 'recovery-home');
  const config = join(home, '.gemini', 'antigravity-cli');
  mkdirSync(config, { recursive: true });
  const id = 'agy-recovery';
  const command = [process.execPath, fileURLToPath(new URL('./fixtures/fake-agy.mjs', import.meta.url))];
  await registry.add({ id, adapter: 'agy', command, env: { HOME: home, LOOCHAT_AGY_HOME: config, FAKE_AGY_LOGIN_REQUIRED: '1' } });
  const denied = await api(`/api/agents/${id}/models`);
  assert.equal(denied.status, 409, 'agent login must not invalidate the Workbench token');
  assert.deepEqual(await denied.json(), { error: 'Antigravity is not signed in', code: 'agent_login_required' });
  const events: any[] = [];
  const off = manager.subscribe(event => { if (event.type === 'agent.modelsChanged') events.push(event); });
  try {
    const login = await (await api(`/api/agents/${id}/login`, 'POST')).json() as any;
    const sessionPath = `/api/agents/${id}/login/${login.id}`;
    await waitFor(async () => (await api(sessionPath)).json() as any, state => state.status === 'waiting');
    await api(sessionPath, 'POST', { code: 'test-agy-code' });
    await waitFor(() => events, list => list.some(event => event.data.agent === id && event.data.models.length > 0));
    const response = await api(`/api/agents/${id}/models`);
    assert.equal(response.status, 200);
    assert.ok((await response.json() as any[]).some(model => model.id === 'fast-low'));
    assert.equal(registry.get(id).account, 'agy-new@example.com');
  } finally { off(); }
});

test('non-authentication model failures retain their error meaning', async () => {
  const command = [process.execPath, fileURLToPath(new URL('./fixtures/fake-agy.mjs', import.meta.url))];
  await registry.add({ id: 'agy-network-error', adapter: 'agy', command, env: { FAKE_AGY_MODELS_ERROR: 'upstream timed out; please check your connection' } });
  const response = await api('/api/agents/agy-network-error/models');
  assert.equal(response.status, 500);
  const body = await response.json() as any;
  assert.equal(body.code, undefined);
  assert.match(body.error, /upstream timed out/);
});

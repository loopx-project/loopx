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
  root = mkdtempSync(join(tmpdir(), 'calli-account-tests-')); source = join(root, 'original'); mkdirSync(source);
  mkdirSync(join(source, 'skills')); mkdirSync(join(source, 'skills', 'sample'));
  writeFileSync(join(source, 'skills', 'sample', 'SKILL.md'), 'Original skill');
  symlinkSync(join(source, 'skills', 'sample'), join(source, 'skills', 'linked'));
  writeFileSync(join(source, 'settings.json'), JSON.stringify({ model: 'sonnet', apiKeyHelper: 'secret-helper', env: { ANTHROPIC_API_KEY: 'old-key', CLAUDE_CONFIG_DIR: source, KEEP: 'yes' } }));
  writeFileSync(join(source, '.claude.json'), JSON.stringify({ oauthAccount: { email: 'old@example.com' }, projects: { private: {} }, mcpServers: { docs: { command: 'docs' } } }));
  writeFileSync(join(source, '.credentials.json'), 'original credentials');
  mkdirSync(join(source, 'projects')); writeFileSync(join(source, 'projects', 'old.jsonl'), 'old conversation');
  registry = new AgentRegistry([{ id: 'claude', adapter: 'claude', command: [process.execPath, acp], env: { CLAUDE_CONFIG_DIR: source, CALLI_CLAUDE_BIN: fixture } }], join(root, 'agents.json'));
  store = new Store(root);
  manager = new ThreadManager(store, registry.adapters, [root], new AttachmentStore(root), () => registry.list());
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

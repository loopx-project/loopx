import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { DoubaoAdapter, doubaoArgs } from '../src/adapters/doubao.js';
import { createFakeDoubaoRuntime } from './fixtures/doubao-runtime.mjs';
import { AgentRegistry } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { loadConfig } from '../src/config.js';
import type { TurnHooks } from '../src/adapters/types.js';
import type { EmittedEvent } from '../src/events.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

const fixture = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'fake-doubao.mjs');
const command = [process.execPath, fixture];
const turn = (text: string) => ({ text, images: [] });
const wait = async (check: () => unknown, timeout = 8_000) => {
  const end = Date.now() + timeout;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};
function collecting() {
  const events: EmittedEvent[] = [];
  const hooks: TurnHooks = { emit: event => events.push(event), requestApproval: async () => { throw new Error('no native approvals in print mode'); } };
  const answer = () => events.filter(event => event.type === 'message.delta').map(event => event.data.text).join('');
  return { events, hooks, answer };
}

function setup(t: { after(fn: () => unknown): void }, base: string[] = [], env: Record<string, string> = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-doubao-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const log = join(dir, 'calls.jsonl');
  const bin = createFakeDoubaoRuntime(join(dir, 'native'));
  const adapter = new DoubaoAdapter([...command, ...base], { CALLI_DOUBAO_RUNTIME: join(dir, 'native'), FAKE_DOUBAO_LOG: log, FAKE_DOUBAO_MARKER: 'instance-one', CALLI_DOUBAO_STREAM: '', ...env });
  const calls = () => existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line)) : [];
  return { dir, log, bin, adapter, calls };
}

test('Doubao advertises a guarded native catalog, and catalog reads never prompt', async t => {
  const h = setup(t);
  assert.equal(await h.adapter.available(), true);
  assert.deepEqual((await h.adapter.models()).map(model => model.id), ['native-default', 'native-fast']);
  assert.deepEqual(await h.adapter.commands(), []);
  assert.deepEqual(h.calls(), []);
  const cap = h.adapter.capabilities;
  assert.equal(cap.resume, true);
  assert.equal(cap.coordination, true);
  assert.equal(cap.approvals, false);
  assert.deepEqual(cap.permissionModes, ['auto_edit', 'full_access']);
  assert.equal(cap.defaultPermissionMode, 'auto_edit');
  for (const key of ['images', 'history', 'fork', 'forkAt', 'forkInto', 'deleteSession', 'quota', 'multiAccount'] as const) assert.equal(cap[key], false, key);
  assert.deepEqual(cap.effortLevels, []);
  assert.equal(cap.modelCatalog, true);
  assert.equal(cap.modelSwitch, true);
  assert.deepEqual(await h.adapter.diagnose(), { version: '2.31.2', protocol: 'doubao -p (text)' });
  assert.deepEqual(h.calls(), [], 'the diagnostic is only --version');
  assert.equal(await new DoubaoAdapter(['/nonexistent/doubao']).available(), false);
  assert.equal(new DoubaoAdapter([]).resumeCommand('session-id'), "'doubao' '--resume' 'session-id'");
});

test('Doubao argv preserves opaque prompt text and maps safe and full-access permission flags', () => {
  const text = "hello\n'$(not-a-command)' --resume other";
  assert.deepEqual(doubaoArgs({ cwd: '/project with spaces' }, text), ['-C', '/project with spaces', '--permission-mode', 'ask_risky', '-p', text]);
  assert.deepEqual(doubaoArgs({ cwd: '/project', resumeId: '38450000000000001', permissionMode: 'full_access', model: 'native-model', effort: 'off' }, text), [
    '-C', '/project', '--dangerously-bypass-approvals-and-sandbox', '--model', 'native-model', '--effort', 'none', '--resume', '38450000000000001', '-p', text,
  ]);
  const base = ['wrapper.mjs', '--model=old', '--effort', 'low', '--permission-mode=ask_always', '--resume', 'other', '-C', '/other', '-p', 'old', '--list'];
  assert.deepEqual(doubaoArgs({ cwd: '/project', permissionMode: 'auto_edit', model: 'new', effort: 'high' }, 'new text', base), [
    'wrapper.mjs', '-C', '/project', '--permission-mode', 'ask_risky', '--model', 'new', '--effort', 'high', '-p', 'new text',
  ]);
  assert.deepEqual(doubaoArgs({ cwd: '/project' }, text, ['wrapper.mjs', '--model', 'configured', '--effort=low']), [
    'wrapper.mjs', '--model', 'configured', '--effort=low', '-C', '/project', '--permission-mode', 'ask_risky', '-p', text,
  ]);
  assert.throws(() => doubaoArgs({ cwd: '/project', permissionMode: 'ask' }, text), /不能请求人工审批/);
});

test('Doubao lazily announces a native id and later -p processes resume it with instance environment', async t => {
  const h = setup(t, ['--model', 'configured', '--effort', 'low']);
  const ids: string[] = [];
  const session = await h.adapter.start({ cwd: h.dir, onSessionId: id => ids.push(id), coordinationEnv: { CALLI_TEAM_SOCKET: '/fixture/socket', CALLI_TEAM_KEY: 'fixture-key', CALLI_BIN_DIR: h.dir } });
  t.after(() => session.close());
  assert.equal(session.agentSessionId, '');
  assert.deepEqual(h.calls(), [], 'starting a handle does not create a native conversation');
  for (const text of ['first 中文', 'second']) {
    const c = collecting();
    assert.deepEqual(await session.prompt(turn(text), c.hooks), { stopReason: 'end_turn' });
    assert.equal(c.answer(), `doubao:${text}\n`);
    assert.ok(c.events.every(event => event.type === 'message.delta'), 'no synthetic tools, reasoning or usage');
  }
  assert.deepEqual(ids, ['38450000000000001']);
  assert.equal(session.agentSessionId, ids[0]);
  const calls = h.calls();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].args.includes('--resume'), false);
  assert.equal(calls[1].args[calls[1].args.indexOf('--resume') + 1], ids[0]);
  assert.notEqual(calls[0].pid, calls[1].pid, 'one CLI process per turn');
  assert.equal(calls[0].marker, 'instance-one');
  assert.equal(calls[0].ready, true);
  assert.ok(calls[0].path.startsWith(h.dir));
  assert.ok(calls.every((call: any) => call.cwd === h.dir));
  await session.setPermissionMode!('full_access');
  await session.prompt(turn('third'), collecting().hooks);
  assert.ok(h.calls()[2].args.includes('--dangerously-bypass-approvals-and-sandbox'));
  assert.equal(h.calls()[2].args.includes('--permission-mode'), false);
  await session.setPermissionMode!('auto_edit');
  await session.prompt(turn('fourth'), collecting().hooks);
  assert.ok(h.calls()[3].args.includes('ask_risky'));
  assert.deepEqual(ids, ['38450000000000001']);
});

test('Doubao treats JSON-looking stdout as reply text and handles final stderr without a newline', async t => {
  const h = setup(t);
  const session = await h.adapter.start({ cwd: h.dir });
  t.after(() => session.close());
  const json = collecting();
  await session.prompt(turn('json-text'), json.hooks);
  assert.equal(json.answer(), '{"type":"session","session_id":"reply-not-metadata"}\n');
  assert.equal(session.agentSessionId, '38450000000000001');
  for (const text of ['no-newline', 'stderr-noise', '中文🙂']) {
    const c = collecting();
    assert.equal((await session.prompt(turn(text), c.hooks)).stopReason, 'end_turn');
    assert.equal(c.answer(), `doubao:${text}\n`);
  }
});

test('Doubao rejects unsupported input before starting a process and never sends TUI commands to a model', async t => {
  const h = setup(t);
  await assert.rejects(h.adapter.start({ cwd: h.dir, workMode: 'plan' }), /不支持计划模式/);
  await assert.rejects(h.adapter.start({ cwd: h.dir, permissionMode: 'ask' }), /不能请求人工审批/);
  const session = await h.adapter.start({ cwd: h.dir });
  t.after(() => session.close());
  await assert.rejects(session.setPermissionMode!('ask'), /不能请求人工审批/);
  await assert.rejects(session.prompt(turn('  '), collecting().hooks), /非空提示词/);
  await assert.rejects(session.prompt({ text: 'image', images: [{ data: 'png', mimeType: 'image/png' }] }, collecting().hooks), /不支持图片输入/);
  for (const text of ['/model', '/compact', ' /unknown arg\n']) assert.equal((await session.prompt(turn(text), collecting().hooks)).stopReason, 'error');
  assert.deepEqual(h.calls(), []);
  await session.close();
  await assert.rejects(session.prompt(turn('closed'), collecting().hooks), /会话已关闭/);
});

test('Doubao native errors, missing identity and empty replies are not successful turns', async t => {
  const h = setup(t);
  for (const [text, message] of [['start-error', /Signed out/], ['fail', /A native task failed/], ['no-output', /未返回回复/], ['no-session-id', /未返回原生会话 ID/]] as const) {
    const session = await h.adapter.start({ cwd: h.dir });
    t.after(() => session.close());
    const result = await session.prompt(turn(text), collecting().hooks);
    assert.equal(result.stopReason, 'error');
    assert.match(result.error!, message);
    if (text === 'no-session-id') await assert.rejects(session.prompt(turn('again'), collecting().hooks), /会话已关闭/);
  }
  const missing = await new DoubaoAdapter(['/nonexistent/doubao']).start({ cwd: h.dir });
  t.after(() => missing.close());
  assert.match((await missing.prompt(turn('hello'), collecting().hooks)).error!, /无法启动.*ENOENT/);
});

test('Doubao cancellation stops only the active print process and keeps the conversation resumable', async t => {
  const h = setup(t);
  const session = await h.adapter.start({ cwd: h.dir });
  t.after(() => session.close());
  const c = collecting();
  const pending = session.prompt(turn('slow'), c.hooks);
  await wait(() => c.answer() === 'working');
  await assert.rejects(session.prompt(turn('parallel'), c.hooks), /already running/);
  await session.cancel();
  assert.deepEqual(await pending, { stopReason: 'cancelled' });
  const next = collecting();
  assert.equal((await session.prompt(turn('next'), next.hooks)).stopReason, 'end_turn');
  assert.equal(next.answer(), 'doubao:next\n');
  assert.ok(h.calls()[1].args.includes('38450000000000001'));
  const before = await h.adapter.start({ cwd: h.dir });
  t.after(() => before.close());
  const early = before.prompt(turn('slow-before-id'), c.hooks);
  await wait(() => h.calls().length === 3);
  await before.close();
  assert.equal((await early).stopReason, 'cancelled');
  assert.equal(before.agentSessionId, '');
});

test('Doubao force cancellation releases a launcher and native child that ignore interrupt', { timeout: 12_000 }, async t => {
  const h = setup(t, [], { FAKE_DOUBAO_LAUNCHER: '1', FAKE_DOUBAO_IGNORE_SIGINT: '1' });
  const session = await h.adapter.start({ cwd: h.dir });
  t.after(() => session.close());
  const c = collecting();
  const pending = session.prompt(turn('slow'), c.hooks);
  await wait(() => c.answer() === 'working');
  const started = Date.now();
  await session.cancel();
  assert.equal((await pending).stopReason, 'cancelled');
  assert.ok(Date.now() - started >= 4_500, 'the native process ignored SIGINT until the grace period ended');
});

test('Doubao refuses a different native conversation id and callback failures stop the task', async t => {
  const h = setup(t);
  const session = await h.adapter.start({ cwd: h.dir, resumeId: '38450000000000001' });
  t.after(() => session.close());
  assert.match((await session.prompt(turn('wrong-session'), collecting().hooks)).error!, /不同的原生会话 ID/);
  assert.equal(session.agentSessionId, '38450000000000001');
  const failed = await h.adapter.start({ cwd: h.dir, onSessionId: () => { throw new Error('could not persist identity'); } });
  t.after(() => failed.close());
  assert.match((await failed.prompt(turn('slow'), collecting().hooks)).error!, /could not persist identity/);
});

test('Doubao terminal commands preserve wrapper and account paths but do not start print tasks', async t => {
  const h = setup(t);
  const adapter = new DoubaoAdapter([...command, '--model=configured', '--effort', 'low', '--resume', 'old', '-p', 'old prompt', '-C', h.dir], { HOME: join(h.dir, "account's home") });
  const login = adapter.loginCommand();
  const resume = adapter.resumeCommand('38450000000000001');
  assert.match(login, /^env HOME=/);
  assert.match(login, /'\\''/);
  assert.ok(login.endsWith("'login'"));
  assert.equal(login.includes('-p'), false);
  assert.equal(login.includes('--model'), false);
  assert.ok(resume.includes('--model=configured'));
  assert.ok(resume.includes("'--effort' 'low'"));
  assert.ok(resume.endsWith("'--resume' '38450000000000001'"));
  assert.equal(resume.includes('old prompt'), false);
  assert.deepEqual(h.calls(), []);
  const diagnostic = await adapter.diagnose();
  assert.equal(diagnostic.version, '2.31.2');
});

test('Doubao configuration has a PATH default and an executable override', () => {
  assert.deepEqual(loadConfig({ CALLI_TOKEN: 'test-token-0123456789' }).doubaoCommand, ['doubao']);
  assert.deepEqual(loadConfig({ CALLI_TOKEN: 'test-token-0123456789', CALLI_DOUBAO_BIN: '/opt/doubao' }).doubaoCommand, ['/opt/doubao']);
});

async function apiSetup(t: { after(fn: () => unknown): void }) {
  const h = setup(t);
  const registry = new AgentRegistry([{ id: 'doubao', adapter: 'doubao', command, env: { FAKE_DOUBAO_LOG: h.log, CALLI_DOUBAO_RUNTIME: join(h.dir, 'native') } }], null);
  const store = new Store(h.dir);
  let manager = new ThreadManager(store, registry.adapters, [h.dir], new AttachmentStore(h.dir));
  let server = createHttpServer({ manager, registry, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = () => `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown, token = 'test-token') => fetch(base() + path, {
    method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}),
  });
  t.after(async () => { await manager.shutdown(); await new Promise<void>(resolve => server.close(() => resolve())); store.close(); });
  return { ...h, registry, store, get manager() { return manager; }, api,
    async restart() {
      await manager.shutdown();
      await new Promise<void>(resolve => server.close(() => resolve()));
      manager = new ThreadManager(store, registry.adapters, [h.dir], new AttachmentStore(h.dir));
      server = createHttpServer({ manager, registry, token: 'test-token', webDir: null, version: 'test' });
      await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    },
  };
}

test('Doubao API persists a newly announced id during a turn, and resumes it after manager restart', async t => {
  const h = await apiSetup(t);
  assert.equal((await h.api('/api/agents', 'GET', undefined, 'wrong')).status, 401);
  const agents = await (await h.api('/api/agents')).json() as any[];
  assert.ok(agents[0].available);
  assert.equal(agents[0].capabilities.approvals, false);
  assert.deepEqual((await (await h.api('/api/agents/doubao/models')).json() as any[]).map(model => model.id), ['native-default', 'native-fast']);
  const diagnostic = await (await h.api('/api/agents/doubao/diagnostics')).json() as any;
  assert.equal(diagnostic.state, 'ready');
  assert.equal(diagnostic.capabilities.modelCatalog, true);
  assert.match(diagnostic.message, /模型读取成功/);
  assert.deepEqual(h.calls(), [], 'diagnostics only read the CLI version');
  const made = await h.api('/api/threads', 'POST', { agent: 'doubao', cwd: h.dir });
  assert.equal(made.status, 201);
  const thread = await made.json() as { id: string };
  assert.equal(h.manager.getThread(thread.id).agentSessionId, null);
  assert.equal((await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'slow' })).status, 202);
  await wait(() => h.manager.getThread(thread.id).agentSessionId);
  assert.equal(h.manager.threadView(thread.id).status, 'running');
  assert.match(h.manager.threadView(thread.id).resumeCommand!, /38450000000000001/);
  assert.equal((await h.api(`/api/threads/${thread.id}/cancel`, 'POST', {})).status, 200);
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  const id = h.manager.getThread(thread.id).agentSessionId;
  await h.restart();
  assert.equal(h.manager.getThread(thread.id).agentSessionId, id);
  assert.equal((await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'after restart' })).status, 202);
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.equal(h.manager.getThread(thread.id).agentSessionId, id);
  assert.ok(h.calls()[1].args.includes('--resume'));
  assert.ok(h.calls()[1].args.includes(id));
  const reply = h.store.listEvents(thread.id, 0, -1).filter(event => event.type === 'message.delta').map(event => event.data.text).join('');
  assert.ok(reply.includes('doubao:after restart'));
  assert.equal(h.manager.threadView(thread.id).status, 'idle');
});

test('Doubao API rejects invented catalog capabilities and keeps native defaults untouched', async t => {
  const h = await apiSetup(t);
  const thread = await (await h.api('/api/threads', 'POST', { agent: 'doubao', cwd: h.dir })).json() as { id: string; permissionMode: string };
  assert.equal(thread.permissionMode, 'auto_edit');
  for (const patch of [{ model: 'invented' }, { effort: 'max' }, { permissionMode: 'ask' }, { workMode: 'plan' }]) {
    assert.equal((await h.api(`/api/threads/${thread.id}`, 'PATCH', patch)).status, 400, JSON.stringify(patch));
  }
  assert.equal((await h.api('/api/agents/doubao/quota')).status, 400);
  assert.equal((await h.api(`/api/threads/${thread.id}/sync`, 'POST', {})).status, 400);
  assert.deepEqual(h.calls(), [], 'unsupported settings and catalog reads do not run tasks');
  assert.equal((await h.api(`/api/threads/${thread.id}`, 'PATCH', { permissionMode: 'full_access' })).status, 200);
  await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'hello' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.ok(h.calls()[0].args.includes('--dangerously-bypass-approvals-and-sandbox'));
});


test('Doubao API selects real models and efforts, switches on the next turn and preserves identity after restart', async t => {
  const h = await apiSetup(t);
  assert.equal((await h.api('/api/threads', 'POST', { agent: 'doubao', cwd: h.dir, model: 'native-locked' })).status, 400);
  const made = await h.api('/api/threads', 'POST', { agent: 'doubao', cwd: h.dir, model: 'native-default', effort: 'high' });
  assert.equal(made.status, 201);
  const thread = await made.json() as { id: string };
  await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'first' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  const id = h.manager.getThread(thread.id).agentSessionId;
  assert.ok(id);
  assert.equal(h.calls()[0].args[h.calls()[0].args.indexOf('--model') + 1], 'native-default');
  assert.equal(h.calls()[0].args[h.calls()[0].args.indexOf('--effort') + 1], 'high');
  await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'slow' });
  await wait(() => h.calls().length === 2);
  const changed = await h.api(`/api/threads/${thread.id}`, 'PATCH', { model: 'native-fast' });
  assert.equal(changed.status, 200);
  assert.equal(h.manager.threadView(thread.id).status, 'running', 'a model change must not interrupt the active task');
  assert.equal(h.manager.getThread(thread.id).effort, null, 'unsupported effort is reset when changing models');
  assert.equal((await h.api(`/api/threads/${thread.id}`, 'PATCH', { effort: 'high' })).status, 400);
  await h.api(`/api/threads/${thread.id}/cancel`, 'POST', {});
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'fast' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.equal(h.calls()[2].args[h.calls()[2].args.indexOf('--model') + 1], 'native-fast');
  assert.equal(h.calls()[2].args.includes('--effort'), false);
  assert.equal(h.manager.getThread(thread.id).agentSessionId, id);
  await h.restart();
  await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'after restart' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.equal(h.calls()[3].args[h.calls()[3].args.indexOf('--model') + 1], 'native-fast');
  assert.equal(h.calls()[3].args[h.calls()[3].args.indexOf('--resume') + 1], id);
  assert.equal((await h.api(`/api/threads/${thread.id}`, 'PATCH', { model: 'native-default', effort: 'off' })).status, 200);
  await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'no reasoning' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.equal(h.calls()[4].args[h.calls()[4].args.indexOf('--effort') + 1], 'none');
  assert.equal((await h.api(`/api/threads/${thread.id}`, 'PATCH', { model: null, effort: null })).status, 200);
  await h.api(`/api/threads/${thread.id}/turns`, 'POST', { text: 'defaults' });
  await wait(() => h.manager.threadView(thread.id).status !== 'running');
  assert.equal(h.calls()[5].args.includes('--model'), false);
  assert.equal(h.calls()[5].args.includes('--effort'), false);
  assert.equal(h.manager.getThread(thread.id).agentSessionId, id);
});

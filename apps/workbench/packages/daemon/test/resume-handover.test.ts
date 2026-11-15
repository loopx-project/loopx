import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { createHttpServer } from '../src/http.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
process.env.CALLI_CLAUDE_QUOTA_BIN = join(fixtures, 'fake-claude-usage.sh');
const wait = async (check: () => unknown, timeout = 8000) => {
  const end = Date.now() + timeout;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};

test('a Claude transcript is copied into another account under a new id, the original untouched', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-import-session-'));
  try {
    const project = join(root, 'one', 'projects', '-work-app');
    mkdirSync(project, { recursive: true });
    const oldId = '11111111-2222-3333-4444-555555555555';
    const source = join(project, `${oldId}.jsonl`);
    const saved = `${project}/${oldId}/tool-results/big.txt`;
    const body = [
      JSON.stringify({ type: 'user', sessionId: oldId, message: { content: 'hello' } }),
      JSON.stringify({ type: 'assistant', sessionId: oldId, message: { content: `Output saved to ${saved}` } }),
    ].join('\n') + '\n';
    writeFileSync(source, body);
    const id = await new ClaudeAcpAdapter(['unused'], { CLAUDE_CONFIG_DIR: join(root, 'two') }).importSession(source);
    assert.notEqual(id, oldId);
    const copy = join(root, 'two', 'projects', '-work-app', `${id}.jsonl`);
    const lines = readFileSync(copy, 'utf8').trim().split('\n').map(l => JSON.parse(l));
    assert.deepEqual(lines.map(l => l.sessionId), [id, id]);
    // A path into the original's folder still points there.
    assert.equal(lines[1].message.content, `Output saved to ${saved}`);
    assert.equal(readFileSync(source, 'utf8'), body);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('two fake Codex instances hand the rollout itself over, staged and forked by path', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-resume-handover-codex-'));
  const homes = { codex: join(dir, 'home-one'), codex2: join(dir, 'home-two') };
  const adapters = new Map<string, AgentAdapter>();
  for (const [id, home] of Object.entries(homes)) {
    adapters.set(id, new CodexAppServerAdapter([process.execPath, join(fixtures, 'fake-codex.mjs')], {
      CODEX_HOME: home, ...(id === 'codex2' ? { FAKE_CODEX_PLAN_LEFT: '1' } : {}),
    }));
  }
  const store = new Store(dir);
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: 'codex' })));
  Object.defineProperty(manager.autoHandover, 'delayMs', { value: 80 });
  manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { codex: [{ agent: 'codex2' }] } });
  try {
    const thread = await manager.createThread({ agent: 'codex', cwd: dir });
    await manager.startTurn(thread.id, { text: 'warm up' });
    await wait(() => manager.threadView(thread.id).status !== 'running' && !manager.autoHandover.held(thread.id));
    const sourceSession = manager.getThread(thread.id).agentSessionId!;
    await manager.startTurn(thread.id, { text: 'quota-exhausted' });
    await wait(() => manager.listThreads().some(t => t.handover?.sourceId === thread.id));
    const next = manager.listThreads().find(t => t.handover?.sourceId === thread.id)!;
    await wait(() => store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.done' || e.type === 'turn.error'));
    assert.equal(manager.getThread(next.id).agent, 'codex2');
    assert.equal(manager.getThread(next.id).handover?.mode, 'continue');
    // The fork of the staged rollout is the successor's session, under the second account's own tree.
    const staged = join(homes.codex2, 'sessions', 'imported', `rollout-fixture-${sourceSession}.jsonl`);
    assert.ok(existsSync(staged));
    assert.equal(manager.getThread(next.id).agentSessionId, `imported-rollout-fixture-${sourceSession}`);
    // The failed ask is resent as it was, with no brief in front of it.
    const starts = store.listEvents(next.id, 0, -1).filter(e => e.type === 'turn.started');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].data.text, 'quota-exhausted');
    assert.ok(store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.done'), 'the second account finishes the retried ask');
    // The copy is a portable rollout: it carries no sealed reasoning of the source account.
    assert.ok(!readFileSync(staged, 'utf8').includes('encrypted_content'));
  } finally { await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

async function setup(method?: 'resume' | 'summary') {
  const dir = mkdtempSync(join(tmpdir(), 'calli-resume-handover-'));
  mkdirSync(join(dir, 'account-two'));
  const store = new Store(dir);
  const loads = join(dir, 'loads.log');
  const accounts = { claude: join(dir, 'account-one'), claude2: join(dir, 'account-two') };
  const adapters = new Map<string, AgentAdapter>();
  for (const [id, configDir] of Object.entries(accounts)) {
    adapters.set(id, new ClaudeAcpAdapter([process.execPath, join(fixtures, 'fake-acp.mjs')], {
      CLAUDE_CONFIG_DIR: configDir, FAKE_SESSION_ROOT: configDir, FAKE_ACP_LOAD_LOG: loads,
      FAKE_CLAUDE_SESSION_USED: '10', ...(id === 'claude2' ? { FAKE_ACP_PLAN_LEFT: '1' } : {}),
    }));
  }
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: 'claude' })));
  Object.defineProperty(manager.autoHandover, 'delayMs', { value: 80 });
  manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { claude: [{ agent: 'claude2' }] }, ...(method ? { method } : {}) });
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + path, { method, headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const thread = await manager.createThread({ agent: 'claude', cwd: dir });
  const done = (id: string) => wait(() => manager.threadView(id).status !== 'running' && !manager.autoHandover.held(id));
  const successor = async () => {
    await wait(() => manager.listThreads().some(t => t.handover?.sourceId === thread.id));
    const next = manager.listThreads().find(t => t.handover?.sourceId === thread.id)!;
    await wait(() => store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.done' || e.type === 'turn.error'));
    return manager.getThread(next.id);
  };
  return { dir, store, manager, thread, accounts, loads, api, done, successor,
    async close() { await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('an exhausted account hands the conversation itself to another account of the same agent', async () => {
  const h = await setup();
  try {
    await h.manager.startTurn(h.thread.id, { text: 'warm up' }); await h.done(h.thread.id);
    const sourceSession = h.manager.getThread(h.thread.id).agentSessionId!;
    assert.ok(sourceSession);
    await h.manager.startTurn(h.thread.id, { text: 'out of plan: finish the report' }); await h.done(h.thread.id);
    const next = await h.successor();
    assert.equal(next.agent, 'claude2');
    assert.equal(next.handover?.mode, 'continue');
    // The successor goes on in a copy filed under the second account, not in the original.
    assert.ok(next.agentSessionId && next.agentSessionId !== sourceSession);
    const project = h.dir.replace(/[^a-zA-Z0-9]/g, '-');
    assert.ok(existsSync(join(h.accounts.claude2, 'projects', project, `${next.agentSessionId}.jsonl`)));
    assert.ok(readFileSync(h.loads, 'utf8').includes(`${h.accounts.claude2}\t${next.agentSessionId}\n`));
    // The failed ask is resent as it was, with no brief in front of it.
    const starts = h.store.listEvents(next.id, 0, -1).filter(e => e.type === 'turn.started');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].data.text, 'out of plan: finish the report');
    assert.ok(h.store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.done'));
  } finally { await h.close(); }
});

test('the brief method still hands over with a summary', async () => {
  const h = await setup('summary');
  try {
    await h.manager.startTurn(h.thread.id, { text: 'warm up' }); await h.done(h.thread.id);
    await h.manager.startTurn(h.thread.id, { text: 'out of plan: finish the report' }); await h.done(h.thread.id);
    const next = await h.successor();
    assert.equal(next.agent, 'claude2');
    const start = h.store.listEvents(next.id, 0, -1).find(e => e.type === 'turn.started')!;
    assert.match(String(start.data.text), /## 继续未完成的这一轮\n\nout of plan: finish the report/);
  } finally { await h.close(); }
});

test('a conversation that cannot be copied falls back to a brief', async () => {
  const h = await setup();
  try {
    await h.manager.startTurn(h.thread.id, { text: 'warm up' }); await h.done(h.thread.id);
    // The second account's session folder cannot be made, so the copy fails.
    writeFileSync(join(h.accounts.claude2, 'projects'), '');
    await h.manager.startTurn(h.thread.id, { text: 'out of plan: finish the report' }); await h.done(h.thread.id);
    const next = await h.successor();
    const start = h.store.listEvents(next.id, 0, -1).find(e => e.type === 'turn.started')!;
    assert.match(String(start.data.text), /## 继续未完成的这一轮\n\nout of plan: finish the report/);
  } finally { await h.close(); }
});

test('the handover method defaults to resume and only takes known values', async () => {
  const h = await setup();
  try {
    const read = async () => (await (await h.api('/api/handover-settings')).json()) as { method: string };
    assert.equal((await read()).method, 'resume');
    const base = { enabled: true, threshold: 90, orders: {} };
    assert.equal((await h.api('/api/handover-settings', 'PUT', { ...base, method: 'teleport' })).status, 400);
    assert.equal((await h.api('/api/handover-settings', 'PUT', { ...base, method: 'summary' })).status, 200);
    assert.equal((await read()).method, 'summary');
    // A save from an older page that does not know the field keeps the choice.
    assert.equal((await h.api('/api/handover-settings', 'PUT', base)).status, 200);
    assert.equal((await read()).method, 'summary');
  } finally { await h.close(); }
});

test('taking over by hand also goes on in the conversation, with the note after the failed ask', async () => {
  const h = await setup();
  try {
    h.manager.autoHandover.configure({ enabled: false, threshold: 90, orders: {} });
    await h.manager.startTurn(h.thread.id, { text: 'warm up' }); await h.done(h.thread.id);
    await h.manager.startTurn(h.thread.id, { text: 'out of plan: finish the report' }); await h.done(h.thread.id);
    assert.equal(h.manager.listThreads().filter(t => t.handover).length, 0, 'automatic handover is off');
    const res = await h.api(`/api/threads/${h.thread.id}/takeover`, 'POST', { agent: 'claude2', note: 'keep it short' });
    assert.equal(res.status, 201);
    const next = await h.successor();
    assert.equal(next.handover?.mode, 'continue');
    assert.ok(readFileSync(h.loads, 'utf8').includes(`${h.accounts.claude2}\t${next.agentSessionId}\n`));
    const start = h.store.listEvents(next.id, 0, -1).find(e => e.type === 'turn.started')!;
    assert.equal(start.data.text, 'out of plan: finish the report\n\nkeep it short');
  } finally { await h.close(); }
});

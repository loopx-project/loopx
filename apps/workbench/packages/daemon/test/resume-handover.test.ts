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
process.env.LOOCHAT_CLAUDE_QUOTA_BIN = join(fixtures, 'fake-claude-usage.sh');
const wait = async (check: () => unknown, timeout = 8000) => {
  const end = Date.now() + timeout;
  while (!check()) { if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 10)); }
};

test('a Claude transcript is copied into another account under a new id, the original untouched', async () => {
  const root = mkdtempSync(join(tmpdir(), 'loochat-import-session-'));
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
    assert.equal(lines[1].message.content, `Output saved to ${saved}`);
    assert.equal(readFileSync(source, 'utf8'), body);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('an exhausted Codex account auto-forks to a lightweight continuation on the fallback account with 0-token anchor', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-autofork-codex-'));
  const homes = { codex: join(dir, 'home-one'), codex2: join(dir, 'home-two') };
  const adapters = new Map<string, AgentAdapter>();
  for (const [id, home] of Object.entries(homes)) {
    adapters.set(id, new CodexAppServerAdapter([process.execPath, join(fixtures, 'fake-codex.mjs')], {
      CODEX_HOME: home, ...(id === 'codex2' ? { FAKE_CODEX_PLAN_LEFT: '1' } : {}),
    }));
  }
  const store = new Store(dir);
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: 'codex' })));
  manager.autoHandover.configure({ enabled: true, threshold: 90, orders: { codex: [{ agent: 'codex2' }] } });
  try {
    const thread = await manager.createThread({ agent: 'codex', cwd: dir, title: 'Codex 主线' });
    await manager.startTurn(thread.id, { text: 'warm up' });
    await wait(() => manager.threadView(thread.id).status !== 'running' && !manager.autoHandover.held(thread.id));
    await manager.startTurn(thread.id, { text: 'quota-exhausted' });
    await wait(() => manager.listThreads().some(t => t.forkedFrom === thread.id));
    const next = manager.listThreads().find(t => t.forkedFrom === thread.id)!;
    await wait(() => store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.done' || e.type === 'turn.error'));
    assert.equal(manager.getThread(next.id).agent, 'codex2');
    assert.equal(manager.getThread(next.id).forkGroup, thread.id);
    const starts = store.listEvents(next.id, 0, -1).filter(e => e.type === 'turn.started');
    assert.equal(starts.length, 1);
    assert.match(String(starts[0].data.text), /<loochat-fork-anchor>/);
    assert.match(String(starts[0].data.text), /warm up/);
    assert.equal(String(starts[0].data.displayText ?? starts[0].data.text), 'quota-exhausted');
    assert.ok(store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.done'));
  } finally { await manager.shutdown(); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('taking over by hand via POST /takeover still copies a Claude session for legacy callers', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-resume-handover-'));
  mkdirSync(join(dir, 'account-two'));
  const store = new Store(dir);
  const loads = join(dir, 'loads.log');
  const accounts = { claude: join(dir, 'account-one'), claude2: join(dir, 'account-two') };
  const adapters = new Map<string, AgentAdapter>();
  for (const [id, configDir] of Object.entries(accounts)) {
    adapters.set(id, new ClaudeAcpAdapter([process.execPath, join(fixtures, 'fake-acp.mjs')], {
      CLAUDE_CONFIG_DIR: configDir, FAKE_SESSION_ROOT: configDir, FAKE_ACP_LOAD_LOG: loads,
      FAKE_CLAUDE_SESSION_USED: '10',
      ...(id === 'claude2' ? { FAKE_ACP_PLAN_LEFT: '1' } : {}),
    }));
  }
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir), () => [...adapters.keys()].map(id => ({ id, adapter: 'claude' })));
  const server = createHttpServer({ manager, token: 'test-token', webDir: null, version: 'test' });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + path, { method, headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const done = (id: string) => wait(() => manager.threadView(id).status !== 'running' && !manager.autoHandover.held(id));
  try {
    const thread = await manager.createThread({ agent: 'claude', cwd: dir });
    await manager.startTurn(thread.id, { text: 'warm up' }); await done(thread.id);
    await manager.startTurn(thread.id, { text: 'out of plan: finish the report' }); await done(thread.id);
    assert.equal(manager.listThreads().filter(t => t.handover).length, 0, 'automatic continuation is off');
    const res = await api(`/api/threads/${thread.id}/takeover`, 'POST', { agent: 'claude2', note: 'keep it short' });
    assert.equal(res.status, 201);
    await wait(() => manager.listThreads().some(t => t.handover?.sourceId === thread.id));
    const next = manager.listThreads().find(t => t.handover?.sourceId === thread.id)!;
    await wait(() => store.listEvents(next.id, 0, -1).some(e => e.type === 'turn.done' || e.type === 'turn.error'));
    assert.equal(next.handover?.mode, 'continue');
    assert.ok(readFileSync(loads, 'utf8').includes(`${accounts.claude2}\t${next.agentSessionId}\n`));
    const start = store.listEvents(next.id, 0, -1).find(e => e.type === 'turn.started')!;
    assert.equal(start.data.text, 'out of plan: finish the report\n\nkeep it short');
  } finally { await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); rmSync(dir, { recursive: true, force: true }); }
});

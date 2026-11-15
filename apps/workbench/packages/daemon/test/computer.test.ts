import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BrowserComputer, browserExecutable } from '../src/browser-computer.js';
import { codexComputerTool } from '../src/adapters/codex-computer.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import type { EmittedEvent } from '../src/events.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { Workdirs } from '../src/workdirs.js';
import { BorrowingService } from '../src/borrowing.js';

const fixture = join(dirname(fileURLToPath(import.meta.url)), 'fixtures/fake-codex.mjs');
const browserTest = { skip: browserExecutable() ? false : 'Chrome/Chromium is required for browser execution tests' };
async function target() {
  const submitted: string[] = [];
  const server = createServer((req, res) => {
    if (req.url?.startsWith('/submit')) submitted.push(new URL(req.url, 'http://localhost').searchParams.get('name') ?? '');
    res.setHeader('Content-Type', 'text/html');
    res.end(`<!doctype html><title>Computer test</title><h1>Browser test</h1>
      <form action="/submit"><input name="name" aria-label="Name" style="position:absolute;left:60px;top:100px;width:250px;height:36px">
      <button style="position:absolute;left:60px;top:160px;width:140px;height:40px">Submit</button></form><p style="margin-top:230px">${submitted.length ? 'Submitted: Calli' : 'Enter a name'}</p>`);
  });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/`;
  return { url, submitted, close: () => new Promise<void>(r => server.close(() => r())) };
}

test('computer task validation requires HTTP(S), rejects extra fields and never accepts answer-only evidence', () => {
  for (const input of [{ task: 'click' }, { task: 'click', url: 'file:///secret' }, { task: 'click', url: 'https://user:password@example.com' }, { task: 'click', url: 'https://example.com', shell: true }]) {
    assert.throws(() => codexComputerTool.prepare(input));
  }
  assert.throws(() => codexComputerTool.collect([], 'I clicked everything', {}), /verified browser/);
  const { input } = codexComputerTool.prepare({ task: ' Inspect page ', url: 'https://example.com' });
  assert.deepEqual(input, { task: 'Inspect page', url: 'https://example.com/' });
});

test('browser runtime validates before launch, serializes actions, saves screenshot and closes after finish', browserTest, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-computer-test-')), site = await target();
  const computer = new BrowserComputer(dir);
  try {
    for (const action of [{ action: 'navigate', url: 'file:///secret' }, { action: 'click', x: -1, y: 10 }, { action: 'key', key: 'F12' }, { action: 'screenshot', script: 'x' }]) await assert.rejects(computer.execute(action));
    await computer.execute({ action: 'navigate', url: site.url });
    await Promise.all([
      computer.execute({ action: 'click', x: 150, y: 120 }),
      computer.execute({ action: 'type', text: 'Calli' }),
      computer.execute({ action: 'click', x: 100, y: 180 }),
    ]);
    assert.deepEqual(site.submitted, ['Calli']);
    const result = await computer.execute({ action: 'finish' });
    assert.equal(result.evidence.scope, 'browser');
    assert.equal(result.evidence.actions, 5);
    assert.equal(result.image.readUInt32BE(16), 1280);
    assert.equal(result.image.readUInt32BE(20), 800);
    assert.deepEqual(readFileSync(result.evidence.screenshot!), result.image);
    await assert.rejects(computer.execute({ action: 'screenshot' }), /closed/);
  } finally { await computer.close(); await site.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('browser cancellation during startup closes the launch and rejects future actions', browserTest, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-computer-cancel-'));
  const computer = new BrowserComputer(dir);
  try {
    const result = computer.execute({ action: 'navigate', url: 'https://example.com/' });
    const rejected = assert.rejects(result);
    await new Promise(r => setTimeout(r, 5));
    await computer.close(); await rejected;
    await assert.rejects(computer.execute({ action: 'screenshot' }), /closed/);
  } finally { await computer.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('borrowed Codex dynamic tools execute real browser actions and persist a verified artifact', browserTest, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-computer-job-')), site = await target();
  const adapter = new CodexAppServerAdapter([process.execPath, fixture], { FAKE_CODEX_COMPUTER: 'success' });
  const adapters = new Map([['visual-agent', adapter]]), store = new Store(dir);
  const manager = new ThreadManager(store, adapters, new Workdirs([dir], join(dir, 'dirs.json'), join(dir, 'scratch')), new AttachmentStore(dir));
  const service = new BorrowingService(store, manager, adapters, dir, join(dir, 'tools.sock'));
  try {
    const started = await service.command('operator', { op: 'tools.run', tool: 'visual-agent/computer.use', requestId: 'form-test', input: { url: site.url, task: 'Fill Name with Calli and submit.' } }) as any;
    const deadline = Date.now() + 20_000;
    let job;
    do { await new Promise(r => setTimeout(r, 50)); job = store.toolJobs.get(started.id)!; }
    while (['queued', 'running'].includes(job.state) && Date.now() < deadline);
    assert.equal(job.state, 'succeeded', JSON.stringify(job.error));
    assert.deepEqual(site.submitted, ['Calli']);
    assert.deepEqual(job.result, { runtime: 'calli-browser', scope: 'browser', url: `${site.url}submit?name=Calli`, actions: 5 });
    assert.equal(job.artifacts.length, 1);
    assert.equal(readFileSync(job.artifacts[0].path).readUInt32BE(16), 1280);
    const events = store.listEvents(job.threadId!, 0, -1);
    assert.equal(events.filter(e => (e.data.computer as any)?.runtime === 'calli-browser').length, 5);
    assert.ok(!JSON.stringify(events).includes('data:image/png;base64,'), 'screenshots must not inflate persisted events');
    assert.equal(service.environment(job.threadId!), undefined);
  } finally { await service.dispose(); await manager.shutdown(); store.close(); await site.close(); rmSync(dir, { recursive: true, force: true }); }
});

for (const mode of ['no-evidence', 'wrong-turn', 'slow']) test(`Codex computer ${mode} cannot report a verified result`, browserTest, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-computer-rpc-')), site = await target();
  const adapter = new CodexAppServerAdapter([process.execPath, fixture], { FAKE_CODEX_COMPUTER: mode });
  const events: EmittedEvent[] = [];
  const session = await adapter.start({ cwd: dir, borrowedTool: 'computer.use' });
  try {
    const pending = session.prompt({ text: codexComputerTool.prepare({ url: site.url, task: 'Fill the form' }).text, images: [] }, { emit: e => events.push(e), requestApproval: async () => ({ cancelled: true }) });
    if (mode === 'slow') {
      const deadline = Date.now() + 10_000;
      while (!events.some(e => e.data.computer) && Date.now() < deadline) await new Promise(r => setTimeout(r, 25));
      assert.ok(events.some(e => e.data.computer));
      await session.cancel();
    }
    const result = await pending;
    if (mode === 'slow') assert.equal(result.stopReason, 'cancelled');
    assert.throws(() => codexComputerTool.collect(events, 'success', {}), /verified browser/);
    assert.deepEqual(site.submitted, []);
  } finally { await session.close(); await site.close(); rmSync(dir, { recursive: true, force: true }); }
});

test('ordinary Codex sessions do not receive the dynamic browser tool', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-computer-ordinary-')), log = join(dir, 'rpc.jsonl');
  const adapter = new CodexAppServerAdapter([process.execPath, fixture], { FAKE_CODEX_RPC_LOG: log });
  const session = await adapter.start({ cwd: dir });
  try {
    const start = readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line)).find(m => m.method === 'thread/start');
    assert.equal(start.params.dynamicTools, undefined);
  } finally { await session.close(); rmSync(dir, { recursive: true, force: true }); }
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { Workdirs } from '../src/workdirs.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { BorrowingService } from '../src/borrowing.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { listenPrivate } from '../src/http.js';
import type { AgentAdapter } from '../src/adapters/types.js';

const here = dirname(fileURLToPath(import.meta.url));
const exec = promisify(execFile);
async function setup(mode = 'success') {
  const dir = mkdtempSync(join(tmpdir(), 'calli-web-'));
  const cwd = join(dir, 'project'); mkdirSync(cwd);
  const adapters = new Map<string, AgentAdapter>([
    ['research-account', new CodexAppServerAdapter([process.execPath, join(here, 'fixtures/fake-codex.mjs')], { FAKE_CODEX_WEB: mode })],
    ['google-account', new AgyAdapter([process.execPath, join(here, 'fixtures/fake-agy.mjs')], { FAKE_AGY_WEB: mode })],
  ]);
  const store = new Store(dir);
  const manager = new ThreadManager(store, adapters, new Workdirs([cwd], join(dir, 'dirs.json'), join(dir, 'scratch')), new AttachmentStore(dir));
  const socket = join(dir, 'ipc/calli.sock');
  const service = new BorrowingService(store, manager, adapters, dir, socket);
  const server = borrowingServer(service); await listenPrivate(server, socket);
  const root = await manager.createThread({ agent: 'research-account', cwd });
  const env = { ...process.env, ...service.environment(root.id) };
  const cli = async (...args: string[]) => JSON.parse((await exec(process.execPath, [join(here, '../bin/calli'), ...args], { env })).stdout);
  const call = (body: unknown) => service.command(root.id, body) as Promise<any>;
  return { dir, adapters, store, manager, service, call, cli, async close() {
    await service.dispose(); await manager.shutdown(); await new Promise<void>(r => server.close(() => r()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  } };
}

test('search discovery stays small; unsupported browser tools explain the missing runtime without starting jobs', async () => {
  const h = await setup();
  try {
    const list = await h.cli('tools', 'search', '搜索');
    assert.deepEqual(list.map((t: any) => t.id).sort(), ['google-account/web.search', 'research-account/web.search']);
    assert.ok(list.every((t: any) => !t.inputSchema && !t.resultKind));
    const computers = await h.cli('tools', 'search', 'Computeruse');
    assert.equal(computers.length, 2);
    const unsupported = computers.filter((t: any) => t.id === 'google-account/browser.use');
    assert.equal(unsupported.length, 1);
    assert.ok(unsupported.every((t: any) => t.availability === 'unavailable' && t.reason));
    for (const tool of unsupported) {
      await assert.rejects(h.call({ op: 'tools.run', tool: tool.id, requestId: 'computer', input: { task: 'Read example.com' } }), /运行环境/);
    }
    assert.equal(h.store.toolJobs.list().length, 0);
    const spec = await h.cli('tools', 'describe', 'research-account/web.read');
    assert.equal(spec.resultKind, 'text');
    assert.deepEqual(spec.inputSchema.required, ['url']);
  } finally { await h.close(); }
});

for (const [tool, input] of [
  ['research-account/web.search', { query: 'example domains' }],
  ['research-account/web.read', { url: 'https://example.com/' }],
  ['google-account/web.search', { query: 'example domains' }],
] as const) {
  test(`${tool} returns a persistent text result through the scoped CLI, without requiring image artifacts`, async () => {
    const h = await setup();
    try {
      const started = await h.call({ op: 'tools.run', tool, input, requestId: 'web' });
      const result = await h.cli('jobs', 'wait', started.id, '--timeout', '10');
      assert.equal(result.state, 'succeeded');
      assert.deepEqual(result.artifacts, []);
      assert.match(result.summary, /documentation/);
      assert.equal(result.result.nativeCalls, 1);
      assert.equal(result.result.sources[0].url, 'https://example.com/');
      assert.equal(result.result.sources[0].provenance, tool.startsWith('google') ? 'answer' : 'native');
      assert.equal(result.summaryTruncated, false);
      assert.equal(h.manager.getThread(result.threadId).permissionMode, tool.startsWith('google') ? 'auto_edit' : 'ask');
      assert.equal(h.service.environment(result.threadId), undefined);
      assert.deepEqual(h.store.toolJobs.get(started.id)!.result, result.result);
      assert.equal((await h.call({ op: 'tools.run', tool, input, requestId: 'web' })).id, started.id);
    } finally { await h.close(); }
  });
}

for (const mode of ['no-evidence', 'wrong-action']) {
  test(`native web ${mode} does not become a successful answer`, async () => {
    const h = await setup(mode);
    try {
      const job = await h.call({ op: 'tools.run', tool: 'research-account/web.search', input: { query: 'examples' }, requestId: 'bad' });
      await assert.rejects(h.cli('jobs', 'wait', job.id, '--timeout', '10'));
      const result = await h.cli('jobs', 'get', job.id);
      assert.equal(result.state, 'failed');
      assert.equal(result.error.code, 'result_unverified');
      assert.deepEqual(result.artifacts, []);
    } finally { await h.close(); }
  });
}

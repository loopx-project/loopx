import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { CodexMemoryReader } from '../src/adapters/codex-memory.js';
import { AttachmentStore } from '../src/attachments.js';
import { ExtensionHost } from '../src/extension-host.js';
import { loadCatalog } from '../src/extensions.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

const fixture = new URL('./fixtures/codex-memory/', import.meta.url);
const temp = () => mkdtempSync(join(tmpdir(), 'calli-memory-'));

test('native memory discovery is read-only, distinguishes states and reads the fixture layout', async () => {
  const dir = temp();
  try {
    const root = join(dir, 'memories');
    const reader = new CodexMemoryReader(root);
    assert.equal((await reader.list()).state, 'missing');
    mkdirSync(root);
    assert.equal((await reader.list()).state, 'empty');
    writeFileSync(join(root, 'unknown.bin'), 'future format');
    assert.equal((await reader.list()).state, 'unsupported');
    cpSync(fixture, root, { recursive: true });
    const before = readFileSync(join(root, 'MEMORY.md'), 'utf8');
    const index = await reader.list();
    assert.equal(index.state, 'available');
    assert.deepEqual(index.files.map(f => f.kind), ['summary', 'durable', 'evidence']);
    assert.equal(index.files[0].title, '工作方式与项目线索');
    assert.equal((await reader.read('MEMORY.md')).text, before);
    assert.equal(readFileSync(join(root, 'MEMORY.md'), 'utf8'), before);
    assert.equal(index.partial, false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('memory reads reject traversal, symlinks, arbitrary home files and binary content; long text is bounded', async () => {
  const dir = temp();
  try {
    const root = join(dir, 'memories');
    cpSync(fixture, root, { recursive: true });
    const reader = new CodexMemoryReader(root);
    writeFileSync(join(dir, 'secret.md'), 'private');
    symlinkSync(join(dir, 'secret.md'), join(root, 'rollout_summaries', 'escape.md'));
    symlinkSync(dir, join(root, 'skills'));
    for (const id of ['../secret.md', '/secret.md', 'rollout_summaries/../MEMORY.md', 'auth.json', 'rollout_summaries/escape.md', 'skills/secret.md', 'MEMORY.md\0']) {
      await assert.rejects(reader.read(id), (e: { status?: number }) => e.status === 404, id);
    }
    writeFileSync(join(root, 'raw_memories.md'), Buffer.from([0, 1, 2]));
    await assert.rejects(reader.read('raw_memories.md'), (e: { status?: number }) => e.status === 415);
    const list = await reader.list();
    assert.equal(list.partial, true);
    assert.ok(!JSON.stringify(list).includes('private'));
    writeFileSync(join(root, 'MEMORY.md'), '# Large\n' + 'long line\n'.repeat(100_000));
    const long = await reader.read('MEMORY.md');
    assert.equal(long.truncated, true);
    assert.ok(Buffer.byteLength(long.text) <= 512 * 1024);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('memory scan has a visible limit rather than claiming a complete result', async () => {
  const dir = temp();
  try {
    const root = join(dir, 'memories');
    mkdirSync(join(root, 'rollout_summaries'), { recursive: true });
    for (let i = 0; i < 505; i++) writeFileSync(join(root, 'rollout_summaries', `${i}.md`), '# Record\n');
    const result = await new CodexMemoryReader(root).list();
    assert.equal(result.files.length, 500);
    assert.equal(result.partial, true);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('memory HTTP API authenticates, merges shared stores, tracks external updates and revokes access', async () => {
  const dir = temp();
  const store = new Store(join(dir, 'hub'));
  const home = join(dir, 'agent');
  cpSync(fixture, join(home, 'memories'), { recursive: true });
  const a = new CodexAppServerAdapter(['never-launch-this-agent'], { CODEX_HOME: home });
  const b = new CodexAppServerAdapter(['never-launch-this-agent'], { HOME: join(dir, 'other'), CODEX_HOME: home });
  const adapters = new Map([['primary', a], ['shared', b]]);
  const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir));
  const extensions = new ExtensionHost({ store: store.extensions, source: manager, adapters, catalog: loadCatalog() });
  extensions.start();
  const server = createHttpServer({ manager, extensions, token: 'test-memory-token', webDir: null, version: 'test' });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const api = (path: string, method = 'GET', body?: unknown) => fetch(base + '/api/extensions/' + path, {
    method, headers: { Authorization: 'Bearer test-memory-token', 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  try {
    assert.equal((await fetch(base + '/api/extensions/memory/memory/sources')).status, 401);
    assert.equal((await api('memory/memory/sources')).status, 403);
    assert.equal((await api('memory/install', 'POST', { granted: ['events.summary'] })).status, 400);
    assert.equal((await api('memory/install', 'POST', { granted: ['memory.read'] })).status, 200);
    const sources = await (await api('memory/memory/sources')).json();
    assert.equal(sources.length, 1);
    assert.deepEqual(sources[0].agents.map((x: { id: string }) => x.id), ['primary', 'shared']);
    assert.ok(!JSON.stringify(sources).includes(home));
    const prefix = `memory/memory/${sources[0].id}`;
    assert.equal((await (await api(prefix + '/files')).json()).files.length, 3);
    writeFileSync(join(home, 'memories', 'memory_summary.md'), '# Externally updated\nNew content');
    assert.equal((await (await api(prefix + '/files')).json()).files[0].title, 'Externally updated');
    assert.equal((await (await api(prefix + '/document?id=memory_summary.md')).json()).text, '# Externally updated\nNew content');
    assert.equal((await api(prefix + '/document?id=..%2Fsecret.md')).status, 404);
    assert.equal((await api('memory/memory/unknown/files')).status, 404);
    assert.equal((await api(prefix + '/document?id=MEMORY.md', 'POST', {})).status, 404);
    await api('board/install', 'POST', { granted: ['events.summary'] });
    assert.equal((await api(`board/memory/${sources[0].id}/files`)).status, 403);
    await api('memory', 'PATCH', { enabled: false });
    assert.equal((await api(prefix + '/document?id=MEMORY.md')).status, 403);
    await api('memory', 'PATCH', { enabled: true });
    assert.equal((await api(prefix + '/files')).status, 200);
    await api('memory', 'DELETE');
    assert.equal((await api('memory/memory/sources')).status, 403);
  } finally {
    extensions.dispose(); server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  }
});

test('the host discards a memory read completed after permission revocation', async () => {
  const dir = temp();
  const store = new Store(dir);
  try {
    const adapter = new CodexAppServerAdapter(['never-launch-this-agent'], { CODEX_HOME: dir });
    const original = adapter.memoryReader();
    let finish!: (value: Awaited<ReturnType<typeof original.read>>) => void;
    const pending = new Promise<Awaited<ReturnType<typeof original.read>>>(resolve => { finish = resolve; });
    let entered!: () => void;
    const started = new Promise<void>(resolve => { entered = resolve; });
    original.read = () => { entered(); return pending; };
    adapter.memoryReader = () => original;
    const adapters = new Map([['codex', adapter]]);
    const manager = new ThreadManager(store, adapters, [dir], new AttachmentStore(dir));
    const host = new ExtensionHost({ store: store.extensions, source: manager, adapters, catalog: loadCatalog() });
    host.install('memory', ['memory.read']);
    const [source] = await host.memorySources('memory');
    const result = host.memoryRead('memory', source.id, 'MEMORY.md');
    await started;
    host.setEnabled('memory', false);
    host.setEnabled('memory', true); // Re-enabling cannot revive a read under the old grant.
    finish({ id: 'MEMORY.md', text: 'private', modifiedAt: new Date().toISOString(), truncated: false });
    await assert.rejects(result, (e: { status?: number }) => e.status === 403);
    host.dispose();
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});

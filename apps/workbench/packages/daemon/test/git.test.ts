import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { GitManager } from '../src/git.js';
import { HttpError } from '../src/errors.js';
import { ThreadManager } from '../src/threads.js';
import { Store } from '../src/store.js';
import { AttachmentStore } from '../src/attachments.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { createHttpServer } from '../src/http.js';

const run = (cwd: string, ...args: string[]) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
const configure = (cwd: string) => { run(cwd, 'config', 'user.name', 'Test'); run(cwd, 'config', 'user.email', 'test@example.com'); };
const commit = (cwd: string, message = 'change') => { run(cwd, 'add', '.'); run(cwd, 'commit', '-m', message); };
const refusal = (code: string) => (error: unknown) => error instanceof HttpError && error.details?.gitCode === code;
function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-git-test-')), repo = join(dir, 'repo'); mkdirSync(repo);
  run(repo, 'init', '-b', 'main'); configure(repo);
  const service = new GitManager(() => repo, () => {}, () => false);
  return { dir, repo, service, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

test('Git reads unborn, staged/unstaged, rename, unusual names, binary, deleted and bounded files', async () => {
  const h = setup();
  try {
    assert.equal((await h.service.status('t')).head, null);
    assert.deepEqual((await h.service.log('t')).commits, []);
    writeFileSync(join(h.repo, 'hello.txt'), 'one\n'); commit(h.repo, 'initial');
    const rootCommit = run(h.repo, 'rev-parse', 'HEAD');
    assert.equal((await h.service.commitFiles('t', rootCommit)).files[0].path, 'hello.txt');
    assert.match((await h.service.diff('t', 'hello.txt', true, rootCommit)).text, /\+one/);
    writeFileSync(join(h.repo, 'hello.txt'), 'two\n'); run(h.repo, 'add', 'hello.txt'); writeFileSync(join(h.repo, 'hello.txt'), 'three\n');
    const file = (await h.service.status('t')).files[0];
    assert.equal(file.index, 'M'); assert.equal(file.worktree, 'M');
    assert.match((await h.service.diff('t', 'hello.txt', true)).text, /\+two/);
    assert.match((await h.service.diff('t', 'hello.txt')).text, /\+three/);
    run(h.repo, 'reset', '--hard', 'HEAD');
    const name = '中文 \t\nrenamed.txt'; run(h.repo, 'mv', 'hello.txt', name);
    const renamed = (await h.service.status('t')).files[0];
    assert.equal(renamed.path, name); assert.equal(renamed.originalPath, 'hello.txt');
    assert.match((await h.service.diff('t', name, true)).text, /\+one/);
    commit(h.repo, 'rename');
    rmSync(join(h.repo, name));
    assert.match((await h.service.diff('t', name)).text, /-one/);
    writeFileSync(join(h.repo, 'binary'), Buffer.from([0, 1, 2]));
    assert.equal((await h.service.diff('t', 'binary')).binary, true);
    writeFileSync(join(h.repo, 'large'), 'some line\n'.repeat(90_000));
    const large = await h.service.diff('t', 'large'); assert.equal(large.truncated, true); assert.ok(large.text.length <= 512 * 1024);
    assert.ok(large.text.split('\n').length <= 4001);
    writeFileSync(join(h.repo, ':(glob)*'), 'literal\n');
    assert.equal((await h.service.diff('t', ':(glob)*')).text, 'literal\n');
  } finally { h.cleanup(); }
});

test('Git history uses a stable head, paginates, compares merge commits to first parent and bounds patches', async () => {
  const h = setup();
  try {
    writeFileSync(join(h.repo, 'file'), 'first\n'); commit(h.repo, 'initial');
    for (let i = 0; i < 32; i++) run(h.repo, 'commit', '--allow-empty', '-m', `commit ${i}`);
    const page = await h.service.log('t'); assert.equal(page.commits.length, 30); assert.equal(page.more, true);
    run(h.repo, 'commit', '--allow-empty', '-m', 'newer');
    const rest = await h.service.log('t', page.head, 30); assert.equal(rest.commits.length, 3); assert.equal(rest.more, false);
    run(h.repo, 'checkout', '-b', 'feature'); writeFileSync(join(h.repo, 'added'), 'feature\n'); commit(h.repo);
    run(h.repo, 'checkout', 'main'); run(h.repo, 'merge', '--no-ff', 'feature', '-m', 'merge');
    const sha = run(h.repo, 'rev-parse', 'HEAD');
    assert.deepEqual((await h.service.commitFiles('t', sha)).files.map(f => f.path), ['added']);
    assert.match((await h.service.diff('t', 'added', true, sha)).text, /\+feature/);
    writeFileSync(join(h.repo, 'file'), 'long diff line\n'.repeat(60_000));
    const diff = await h.service.diff('t', 'file'); assert.equal(diff.truncated, true); assert.ok(diff.text.length <= 512 * 1024);
  } finally { h.cleanup(); }
});

test('Git rejects traversal, symlinks escaping the checkout, invalid revisions and unavailable files', async () => {
  const h = setup();
  try {
    writeFileSync(join(h.repo, 'file'), 'hello\n'); commit(h.repo);
    writeFileSync(join(h.dir, 'secret'), 'private'); symlinkSync(join(h.dir, 'secret'), join(h.repo, 'link'));
    await assert.rejects(h.service.diff('t', 'link'), refusal('invalid_path'));
    for (const path of ['../secret', '.git/config', '/etc/passwd', 'file\0other', './file']) await assert.rejects(h.service.diff('t', path), refusal('invalid_path'));
    await assert.rejects(h.service.commitFiles('t', '--all'), refusal('invalid_commit'));
    await assert.rejects(h.service.log('t', null, -1), refusal('invalid_page'));
    await assert.rejects(h.service.diff('t', 'absent'), refusal('file_changed'));
    const plain = new GitManager(() => h.dir, () => {}, () => false); assert.equal((await plain.status('t')).isRepo, false);
  } finally { h.cleanup(); }
});

test('Git fetch keeps the checkout, pull fast-forwards only, and refuses dirty, detached, busy and diverged trees', async () => {
  const h = setup();
  try {
    writeFileSync(join(h.repo, 'file'), 'base\n'); commit(h.repo);
    await assert.rejects(h.service.operate('t', 'pull'), refusal('no_upstream'));
    await assert.rejects(h.service.operate('t', 'fetch'), refusal('no_remote'));
    const bare = join(h.dir, 'remote.git'); run(h.dir, 'clone', '--bare', h.repo, bare); run(h.repo, 'remote', 'add', 'origin', bare); run(h.repo, 'push', '-u', 'origin', 'main');
    const peer = join(h.dir, 'peer'); run(h.dir, 'clone', bare, peer); configure(peer);
    writeFileSync(join(peer, 'file'), 'remote\n'); commit(peer); run(peer, 'push');
    const old = run(h.repo, 'rev-parse', 'HEAD');
    const fetched = await h.service.operate('t', 'fetch'); assert.equal(fetched.status?.behind, 1); assert.ok(fetched.status?.fetchedAt); assert.equal(run(h.repo, 'rev-parse', 'HEAD'), old);
    writeFileSync(join(h.repo, 'untracked'), 'keep'); await assert.rejects(h.service.operate('t', 'pull'), refusal('dirty')); rmSync(join(h.repo, 'untracked'));
    const busy = new GitManager(() => h.repo, () => {}, () => true); await assert.rejects(busy.operate('t', 'pull'), refusal('agent_busy'));
    writeFileSync(join(h.repo, '.git', 'MERGE_HEAD'), old); await assert.rejects(h.service.operate('t', 'pull'), refusal('operation_in_progress')); rmSync(join(h.repo, '.git', 'MERGE_HEAD'));
    run(h.repo, 'checkout', '--detach'); await assert.rejects(h.service.operate('t', 'pull'), refusal('detached')); run(h.repo, 'checkout', 'main');
    const pulled = await h.service.operate('t', 'pull'); assert.equal(pulled.changed, true); assert.equal(readFileSync(join(h.repo, 'file'), 'utf8'), 'remote\n');
    assert.equal((await h.service.operate('t', 'pull')).changed, false);
    writeFileSync(join(h.repo, 'local'), 'local\n'); commit(h.repo);
    writeFileSync(join(peer, 'other'), 'remote\n'); commit(peer); run(peer, 'push'); await h.service.operate('t', 'fetch');
    const before = run(h.repo, 'rev-parse', 'HEAD'); await assert.rejects(h.service.operate('t', 'pull'), refusal('diverged')); assert.equal(run(h.repo, 'rev-parse', 'HEAD'), before);
    // A local non-repository failure is immediate, and still must not leak any remote details.
    run(h.repo, 'remote', 'set-url', 'origin', join(h.dir, 'missing-secret'));
    await assert.rejects(h.service.operate('t', 'fetch'), error => error instanceof HttpError && !JSON.stringify(error).includes('missing-secret'));
  } finally { h.cleanup(); }
});

test('Git HTTP endpoints authenticate, stay in the thread worktree, and protect another running thread in that checkout', async () => {
  const h = setup(), data = join(h.dir, 'data'); mkdirSync(data);
  const store = new Store(data);
  const adapter = new PiRpcAdapter([process.execPath, fileURLToPath(new URL('fixtures/fake-pi.mjs', import.meta.url))]);
  const manager = new ThreadManager(store, new Map([['pi', adapter]]), [h.dir], new AttachmentStore(data));
  const server = createHttpServer({ manager, token: 'git-test-token', webDir: null, version: 'test' });
  try {
    writeFileSync(join(h.repo, 'file'), 'initial\n'); commit(h.repo);
    const worktree = join(h.dir, 'tree'); run(h.repo, 'worktree', 'add', '-b', 'side', worktree);
    const thread = await manager.createThread({ agent: 'pi', cwd: worktree });
    const another = await manager.createThread({ agent: 'pi', cwd: worktree });
    await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/threads/${thread.id}/git`;
    const get = (action: string, method = 'GET') => fetch(`${base}/${action}`, { method, headers: { Authorization: 'Bearer git-test-token' } });
    assert.equal((await fetch(`${base}/status`)).status, 401);
    const status = await (await get('status')).json(); assert.equal(status.branch, 'side'); assert.equal(status.root, worktree); assert.equal('commits' in status, false);
    const log = await (await get('log')).json(); const sha = log.commits[0].hash;
    assert.equal((await (await get(`commit?commit=${sha}`)).json()).files[0].path, 'file');
    assert.match((await (await get(`diff?commit=${sha}&path=file`)).json()).text, /\+initial/);
    assert.equal((await get('diff?path=..%2Fsecret')).status, 400);
    await manager.startTurn(another.id, { text: 'slow' });
    assert.equal((await (await get('status')).json()).agentBusy, true);
    const refused = await get('pull', 'POST'); assert.equal(refused.status, 409); assert.equal((await refused.json()).gitCode, 'agent_busy');
    const fetchResponse = await get('fetch', 'POST'); assert.equal(fetchResponse.status, 409); assert.equal((await fetchResponse.json()).gitCode, 'no_remote');
    assert.equal(run(h.repo, 'branch', '--show-current'), 'main');
  } finally { await manager.shutdown(); await new Promise<void>(r => server.close(() => r())); store.close(); h.cleanup(); }
});

test('Git serializes network operations across linked worktrees and holds new turns out during pull', async () => {
  const h = setup();
  try {
    writeFileSync(join(h.repo, 'file'), 'base\n'); commit(h.repo);
    const remote = join(h.dir, 'remote.git'), other = join(h.dir, 'other');
    run(h.dir, 'clone', '--bare', h.repo, remote); run(h.repo, 'remote', 'add', 'origin', remote); run(h.repo, 'push', '-u', 'origin', 'main');
    run(h.repo, 'worktree', 'add', '-b', 'other', other);
    const marker = join(h.dir, 'started'), upload = join(h.dir, 'upload');
    writeFileSync(upload, `#!/bin/sh\nprintf ready > '${marker}'\nsleep 0.5\nexec git-upload-pack "$@"\n`, { mode: 0o755 });
    run(h.repo, 'config', 'remote.origin.uploadpack', upload);
    const service = new GitManager(id => id === 'other' ? other : h.repo, () => {}, () => false);
    const pulling = service.operate('main', 'pull');
    for (let i = 0; i < 200; i++) {
      try { if (readFileSync(marker, 'utf8') === 'ready') break; } catch { /* Wait for the network stage. */ }
      await new Promise(r => setTimeout(r, 10));
    }
    assert.equal(readFileSync(marker, 'utf8'), 'ready');
    assert.throws(() => service.assertTurnAllowed(h.repo), refusal('busy'));
    service.assertTurnAllowed(other);
    await assert.rejects(service.operate('other', 'fetch'), refusal('busy'));
    await pulling;
    service.assertTurnAllowed(h.repo);
    // A post-operation read failure must not invite the person to repeat a successful mutation.
    const original = service.status.bind(service); let reads = 0;
    service.status = async id => { if (++reads === 2) throw new Error('read failed'); return original(id); };
    const result = await service.operate('main', 'pull');
    assert.equal(result.statusError, true); assert.equal(result.status, null);
  } finally { h.cleanup(); }
});

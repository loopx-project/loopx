import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { exchangeDirectory, handoffFile, listRemoteDir, readRemoteText, readResult, resultFile, writeHandoff, writeRemoteText, type RemoteExec } from '../src/remote-files.js';

/** The same scripts run under this machine's sh; the "remote" folder is a temp directory. */
const local: RemoteExec = (command, opts = {}) => new Promise((resolve, reject) => {
  const child = spawn('sh', ['-c', command], { stdio: ['pipe', 'pipe', 'pipe'] });
  const out: Buffer[] = [];
  let stderr = '';
  child.stdout.on('data', (chunk: Buffer) => out.push(chunk));
  child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8'); });
  child.on('error', reject);
  child.on('exit', (code) => resolve({ stdout: Buffer.concat(out), stderr, code: code ?? 255 }));
  child.stdin.end(opts.input ?? '');
});

function workspace() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-remote-files-'));
  const root = join(dir, 'project'); mkdirSync(root);
  return { dir, root, done: () => rmSync(dir, { recursive: true, force: true }) };
}

test('read returns text, size, hash and pages; a binary file is refused', async () => {
  const w = workspace();
  try {
    writeFileSync(join(w.root, 'a.txt'), 'remote content\n');
    const whole = await readRemoteText(local, w.root, 'a.txt');
    assert.equal(whole.text, 'remote content\n');
    assert.equal(whole.size, 15);
    assert.equal(whole.truncated, false);
    assert.match(whole.hash, /^[a-f0-9]{64}$/);
    const page = await readRemoteText(local, w.root, 'a.txt', { offset: 7, limit: 7 });
    assert.equal(page.text, 'content');
    assert.equal(page.truncated, true);
    assert.match(page.hash, /^[a-f0-9]{64}$/);
    assert.notEqual(page.hash, whole.hash, 'the hash describes the part read');
    writeFileSync(join(w.root, 'bin'), Buffer.from([0, 1, 2]));
    await assert.rejects(readRemoteText(local, w.root, 'bin'), (err: any) => err.status === 415);
  } finally { w.done(); }
});

test('paths outside the member folder are refused, including through a symlink', async () => {
  const w = workspace();
  try {
    writeFileSync(join(w.dir, 'outside.txt'), 'outside\n');
    await assert.rejects(readRemoteText(local, w.root, '../outside.txt'), (err: any) => err.status === 403);
    await assert.rejects(readRemoteText(local, w.root, join(w.dir, 'outside.txt')), (err: any) => err.status === 403);
    symlinkSync(join(w.dir, 'outside.txt'), join(w.root, 'link.txt'));
    await assert.rejects(readRemoteText(local, w.root, 'link.txt'), (err: any) => err.status === 403);
    await assert.rejects(writeRemoteText(local, w.root, '../outside.txt', 'no'), (err: any) => err.status === 403);
    assert.equal(readFileSync(join(w.dir, 'outside.txt'), 'utf8'), 'outside\n');
  } finally { w.done(); }
});

test('write creates and replaces atomically, keeps the mode, and refuses a stale hash or a missing parent', async () => {
  const w = workspace();
  try {
    const created = await writeRemoteText(local, w.root, 'new.txt', 'one\n');
    assert.equal(created.size, 4);
    assert.equal(readFileSync(join(w.root, 'new.txt'), 'utf8'), 'one\n');
    chmodSync(join(w.root, 'new.txt'), 0o640);
    await writeRemoteText(local, w.root, 'new.txt', 'two\n');
    assert.equal(readFileSync(join(w.root, 'new.txt'), 'utf8'), 'two\n');
    assert.equal(statSync(join(w.root, 'new.txt')).mode & 0o777, 0o640, 'the old mode stays');
    await assert.rejects(
      writeRemoteText(local, w.root, 'new.txt', 'three\n', { expectHash: '0'.repeat(64) }),
      (err: any) => err.status === 409,
    );
    await assert.rejects(writeRemoteText(local, w.root, 'gone/x.txt', 'x'), (err: any) => err.status === 400);
    assert.equal(readFileSync(join(w.root, 'new.txt'), 'utf8'), 'two\n');
  } finally { w.done(); }
});

test('the write size cap is enforced before anything reaches the machine', async () => {
  const w = workspace();
  try {
    await assert.rejects(writeRemoteText(local, w.root, 'big.txt', 'x'.repeat(1024 * 1024 + 1)), (err: any) => err.status === 413);
  } finally { w.done(); }
});

test('a listing shows folders first, hides .git, and marks a cut list', async () => {
  const w = workspace();
  try {
    mkdirSync(join(w.root, 'src'));
    mkdirSync(join(w.root, '.git'));
    writeFileSync(join(w.root, 'b.txt'), 'b');
    writeFileSync(join(w.root, 'a.txt'), 'aaa');
    const listed = await listRemoteDir(local, w.root, '.');
    assert.deepEqual(listed.entries.map(e => e.name), ['src', 'a.txt', 'b.txt']);
    assert.equal(listed.truncated, false);
    const src = await listRemoteDir(local, w.root, 'src');
    assert.deepEqual(src.entries, []);
  } finally { w.done(); }
});

test('the handoff and the result are files in a hub-owned exchange folder', async () => {
  const w = workspace();
  try {
    const dir = exchangeDirectory(join(w.dir, 'exchange'), 'thread-1', 'message-1');
    assert.equal(dir, join(w.dir, 'exchange', 'thread-1', 'message-1'));
    assert.equal(handoffFile(dir), `${dir}/handoff.md`);
    assert.equal(resultFile(dir), `${dir}/result.md`);
    await writeHandoff(local, dir, '# 交接\n\n做事\n');
    assert.equal(readFileSync(handoffFile(dir), 'utf8'), '# 交接\n\n做事\n');
    assert.equal(await readResult(local, dir), null, 'no result yet is not an error');
    writeFileSync(resultFile(dir), '# REPORT\n\nall done\n');
    const got = await readResult(local, dir);
    assert.equal(got?.text, '# REPORT\n\nall done\n');
    assert.equal(got?.truncated, false);
    assert.equal(statSync(handoffFile(dir)).mode & 0o777, 0o600);
    assert.throws(() => exchangeDirectory(join(w.dir, 'exchange'), '../thread', 'message'), (err: any) => err.status === 400);
  } finally { w.done(); }
});

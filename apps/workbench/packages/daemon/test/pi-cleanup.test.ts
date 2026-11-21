import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PiRpcAdapter } from '../src/adapters/pi.js';

const fixture = fileURLToPath(new URL('./fixtures/fake-pi.mjs', import.meta.url));
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    if (process.platform === 'linux' && readFileSync('/proc/' + pid + '/stat', 'utf8').split(') ')[1].startsWith('Z')) return false;
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ESRCH' || (error as NodeJS.ErrnoException).code === 'ENOENT') return false;
    throw error;
  }
}
for (const kind of ['session', 'mcp'] as const) {
  test('pi ' + kind + ' cleanup terminates its stubborn MCP descendant', { skip: process.platform !== 'linux' }, async () => {
    const dir = mkdtempSync(join(tmpdir(), 'pi-cleanup-'));
    const path = join(dir, 'children');
    const children = () => existsSync(path) ? readFileSync(path, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line) as { pid: number; kind: string }) : [];
    const adapter = new PiRpcAdapter([process.execPath, fixture], { FAKE_PI_CHILDREN: path, FAKE_PI_MCP: '1' });
    let session: Awaited<ReturnType<PiRpcAdapter['start']>> | undefined;
    try {
      if (kind === 'session') {
        session = await adapter.start({ cwd: dir });
        await session.close();
      } else {
        await adapter.models();
        await adapter.modelForImages('fake/plain');
      }
      const child = children().find(row => row.kind === kind);
      assert.ok(child, 'fixture must start an MCP descendant');
      for (let i = 0; i < 40 && alive(child.pid); i++) await pause(25);
      assert.equal(alive(child.pid), false, 'MCP descendant remains alive after pi exits');
    } finally {
      await session?.close();
      for (const child of children()) {
        if (alive(child.pid)) process.kill(child.pid, 'SIGKILL');
      }
      rmSync(dir, { recursive: true, force: true });
    }
  });
}

test('closing one pi session leaves another session MCP running', { skip: process.platform !== 'linux' }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pi-isolation-'));
  const path = join(dir, 'children');
  const adapter = new PiRpcAdapter([process.execPath, fixture], { FAKE_PI_CHILDREN: path });
  const sessions: Awaited<ReturnType<PiRpcAdapter['start']>>[] = [];
  const children = () => existsSync(path) ? readFileSync(path, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line) as {pid: number}) : [];
  try {
    sessions.push(await adapter.start({ cwd: dir }));
    sessions.push(await adapter.start({ cwd: dir }));
    const [one, two] = children();
    await sessions[0].close();
    for (let i = 0; i < 40 && alive(one.pid); i++) await pause(25);
    assert.equal(alive(one.pid), false);
    assert.equal(alive(two.pid), true, 'cleanup must not kill another session MCP');
    await sessions[1].close();
    for (let i = 0; i < 40 && alive(two.pid); i++) await pause(25);
    assert.equal(alive(two.pid), false);
  } finally {
    for (const session of sessions) await session.close();
    for (const child of children()) if (alive(child.pid)) process.kill(child.pid, 'SIGKILL');
    rmSync(dir, { recursive: true, force: true });
  }
});

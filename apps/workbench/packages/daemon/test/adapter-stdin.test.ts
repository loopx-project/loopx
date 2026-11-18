import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';

for (const backend of ['mcp', 'pi']) {
  test(`${backend} stdin EPIPE settles requests and cleans up without crashing its host`, async (t) => {
    const dir = mkdtempSync(join(tmpdir(), 'adapter-stdin-'));
    const pidFile = join(dir, 'backend.pid');
    t.after(() => {
      try { process.kill(Number(readFileSync(pidFile, 'utf8')), 'SIGKILL'); } catch { /* Already reaped. */ }
      rmSync(dir, { recursive: true, force: true });
    });
    const script = `
      import assert from 'node:assert/strict';
      import { readFileSync } from 'node:fs';
      import { probeMcpToolNames } from './src/adapters/mcp-probe.ts';
      import { PiRpcAdapter } from './src/adapters/pi.ts';
      const command = [process.execPath, './test/fixtures/closed-stdin.mjs'];
      if (${JSON.stringify(backend)} === 'mcp') {
        assert.deepEqual(await probeMcpToolNames({ name: 'closed', command: command[0], args: command.slice(1), env: {} }, 2000), []);
      } else {
        const session = await new PiRpcAdapter(command).start({ cwd: process.cwd() });
        const results = await Promise.allSettled([session.setEffort('low'), session.setModel('fake/model')]);
        for (const result of results) {
          assert.equal(result.status, 'rejected');
          assert.match(result.reason.message, /EPIPE/);
        }
        await assert.rejects(session.setEffort('low'), /not running/);
        await session.close();
        await session.close();
      }
      console.log('settled');
    `;
    const { stdout } = await promisify(execFile)(process.execPath, ['--import', 'tsx', '--input-type=module', '--eval', script], {
      env: { ...process.env, CLOSED_STDIN_PID: pidFile }, timeout: 5000,
    });
    assert.match(stdout, /settled/);
    const pid = Number(readFileSync(pidFile, 'utf8'));
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' }, 'the backend is reaped when its host exits');
  });
}

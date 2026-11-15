// Restart.sh waits for the hub to go idle before restarting it. These tests run the real script
// end to end, offline, with fake curl, fake systemctl and a fake hub (fixtures/restart/), so the
// promise "never restart under a running turn" is checked instead of assumed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const e2e = join(dirname(fileURLToPath(import.meta.url)), '..');
const repo = join(e2e, '../..');
const script = join(repo, 'scripts/restart.sh');
const fakeDir = join(repo, 'packages/e2e/fixtures/restart');

const RUNNING = JSON.stringify([{ id: 't1', title: 'menu work', agent: 'codex', status: 'running' }]);
const IDLE = JSON.stringify([{ id: 't1', title: 'menu work', agent: 'codex', status: 'idle' }]);
// A scripted poll sequence: each answer serves one poll; the last becomes the final state.
const seq = (...answers) => answers.map((a) => `+${a}`).join('\n') + `\n${answers.at(-1)}`;

/** A reserved-then-released port, so parallel tests and daemons do not collide. */
function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

/**
 * One restart.sh run against the fakes. `hub` is the curl fake's mode ("up", "down", "flaky");
 * `threadsScript` seeds threads-active.json (see fixtures/restart/hub.mjs). Returns the run()
 * helper and readers for the fake state.
 */
async function fixture(t, { hub = 'up', threadsScript = IDLE, serviceDir = repo } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-restart-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const state = join(dir, 'state');
  mkdirSync(state, { recursive: true });
  const port = await freePort();
  for (const [name, content] of [
    ['mode', hub],
    ['threads-active.json', threadsScript],
    ['health.json', '{}'],
    ['service-dir', `${serviceDir}\n`],
    ['env', `CALLI_HOST=127.0.0.1\nCALLI_PORT=${port}\nCALLI_TOKEN=test-token\n`],
  ]) writeFileSync(join(state, name), content);
  // The hub runs detached; the curl fake talks to it, and both share the state directory.
  const hubProc = spawn(process.execPath, [join(fakeDir, 'hub.mjs'), String(port), state], {
    stdio: ['ignore', 'ignore', 'pipe'],
    detached: true,
  });
  hubProc.stderr.on('data', (chunk) => writeFileSync(join(state, 'hub-error.log'), chunk, { flag: 'a' }));
  hubProc.unref();
  t.after(() => hubProc.kill());
  // curl and systemctl resolve to shims on PATH that exec the node fakes with the state dir.
  for (const [name, target] of [['curl', 'curl-fake.mjs'], ['systemctl', 'systemctl-fake.mjs']]) {
    const shim = join(state, name);
    writeFileSync(shim, `#!/bin/sh\nCALLI_FAKE_STATE="${state}" exec "${process.execPath}" "${join(fakeDir, target)}" "$@"\n`);
    chmodSync(shim, 0o755);
  }
  const run = (args = []) =>
    spawnSync('bash', [script, ...args], {
      cwd: repo,
      env: {
        ...process.env,
        PATH: `${state}:${process.env.PATH}`,
        XDG_STATE_HOME: join(dir, 'xdg-state'),
        CALLI_RESTART_POLL: '0.1',
        CALLI_RESTART_CLEAN_POLLS: '3',
      },
      encoding: 'utf8',
      timeout: 8_000,
    });
  const read = (name) => (existsSync(join(state, name)) ? readFileSync(join(state, name), 'utf8') : '');
  return { state, run, read };
}

test('waits while a turn is running and restarts once it ends', async (t) => {
  const f = await fixture(t, { threadsScript: seq(RUNNING, RUNNING, IDLE) });
  const result = f.run(['--no-build']);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Waiting for 1 running thread/);
  assert.doesNotMatch(result.stdout, /did not answer/);
  assert.match(f.read('systemctl.log'), /restart .*calli/);
});

test('a hub that drops one poll still counts as busy and delays the restart', async (t) => {
  const f = await fixture(t, { hub: 'flaky', threadsScript: seq(RUNNING, RUNNING, RUNNING) });
  writeFileSync(join(f.state, 'fail-next'), '');
  const result = f.run(['--no-build']);
  // Never idle: the script must keep waiting until the timeout kills it, never restart.
  assert.equal(result.status, null, result.stdout + result.stderr);
  assert.equal(result.signal, 'SIGTERM');
  assert.match(result.stdout, /did not answer; retrying/);
  assert.match(result.stdout, /Waiting for 1 running thread/);
  assert.equal(f.read('systemctl.log'), '');
});

test('an idle hub is restarted after confirmation polls, without waiting messages', async (t) => {
  const f = await fixture(t, { threadsScript: IDLE });
  const result = f.run(['--no-build']);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.doesNotMatch(result.stdout, /Waiting for/);
  assert.doesNotMatch(result.stdout, /did not answer/);
  assert.match(f.read('systemctl.log'), /restart .*calli/);
});

test('a hub with nothing listening is restarted right away', async (t) => {
  const f = await fixture(t, { hub: 'down' });
  const result = f.run(['--no-build']);
  assert.match(result.stdout, /Nothing answers/);
  assert.match(f.read('systemctl.log'), /restart .*calli/);
});

test('a failed systemctl restart fails the script', async (t) => {
  const f = await fixture(t, { threadsScript: IDLE });
  writeFileSync(join(f.state, 'restart-exit'), '1');
  const result = f.run(['--no-build']);
  assert.equal(result.status, 1);
  assert.match(f.read('systemctl.log'), /restart .*calli/);
});

test('a service pointing at another checkout stops before restarting', async (t) => {
  const f = await fixture(t, { threadsScript: IDLE, serviceDir: join(tmpdir(), 'calli-restart-elsewhere') });
  const result = f.run(['--no-build']);
  assert.equal(result.status, 1);
  assert.ok(result.stderr.includes(`The service runs ${join(tmpdir(), 'calli-restart-elsewhere')}, not ${repo}. Restart it from there.`), result.stderr);
  assert.equal(f.read('systemctl.log'), '');
});

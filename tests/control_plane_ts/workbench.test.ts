import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { launchEnvironment, workbench } from '../../loopx/control_plane/runtime/workbench.ts';

test('a new daemon does not inherit its enclosing Workbench identity or relay authority', () => {
  const before = { ...process.env };
  try {
    Object.assign(process.env, { LOOCHAT_TOKEN: 'ambient', LOOCHAT_TOOLS_KEY: 'ambient-tools',
      CALLI_TOKEN: 'legacy', RAH_SOCKET: 'legacy.sock', LOOCHAT_HUB_URL: 'https://relay.invalid',
      LOOCHAT_HUB_TOKEN: 'relay-secret', LOOCHAT_AGENTS_JSON: 'ambient-agents' });
    const env = launchEnvironment({ LOOCHAT_TOKEN: 'explicit', LOOCHAT_LOOPX_CORE_URL: 'http://localhost:9876' }, '/tmp/sample-state', '/tmp/sample-app');
    assert.equal(env.LOOCHAT_TOKEN, 'explicit');
    assert.equal(env.LOOCHAT_LOOPX_CORE_URL, 'http://localhost:9876');
    for (const name of ['LOOCHAT_TOOLS_KEY', 'CALLI_TOKEN', 'RAH_SOCKET', 'LOOCHAT_HUB_URL', 'LOOCHAT_HUB_TOKEN', 'LOOCHAT_AGENTS_JSON']) assert.equal(env[name], undefined);
    assert.equal(env.LOOCHAT_SOCKET, '/tmp/sample-state/daemon.sock');
    assert.equal(env.LOOCHAT_LIFECYCLE_SOCKET, '/tmp/sample-state/lifecycle.sock');
    const relative = launchEnvironment({ LOOCHAT_DATA_DIR: './existing-data' }, '/tmp/sample-state', '/tmp/sample-app');
    assert.equal(relative.LOOCHAT_DATA_DIR, '/tmp/sample-app/packages/daemon/existing-data');
  } finally {
    for (const key of Object.keys(process.env)) if (!(key in before)) delete process.env[key];
    Object.assign(process.env, before);
  }
});

test('old PID records cannot authorize a signal; absent status and stop preserve files', async () => {
  const state = mkdtempSync(join(tmpdir(), 'wb-pid-'));
  try {
    const pid = join(state, 'daemon.pid');
    writeFileSync(pid, String(process.pid));
    for (const action of ['status', 'stop'] as const) {
      const result = await workbench({ action, stateDir: state, sourceRoot: state });
      assert.equal(result.state, 'stopped');
      assert.equal(existsSync(pid), true);
    }
    assert.equal(existsSync(join(state, '.env')), false);
    assert.equal(existsSync(join(state, 'launch.json')), false);
  } finally { rmSync(state, { recursive: true, force: true }); }
});


test('launch rejects overlong Unix socket paths before they can be silently truncated', () => {
  assert.throws(() => launchEnvironment({}, '/tmp/' + 'x'.repeat(100), '/tmp/app'), /socket path is too long/);
  assert.throws(() => launchEnvironment({ LOOCHAT_DATA_DIR: '/tmp/' + 'x'.repeat(100) }, '/tmp/state', '/tmp/app'), /socket path is too long/);
});

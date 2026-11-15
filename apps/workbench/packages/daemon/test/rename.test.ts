// What still works under the names from before the rename to Calli (RAH_*, LIMI_*, remote-agent-hub, ~/.limi).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { adoptLegacyEnv, homeDirectory, LEGACY_SOCKET_IN_HOME, loadConfig, SOCKET_IN_HOME } from '../src/config.js';
import { progressInstructions } from '../src/progress-report.js';

const here = dirname(fileURLToPath(import.meta.url));
const exec = promisify(execFile);

test('RAH_* and LIMI_* set their CALLI_* names, never over a new one, and are dropped', () => {
  const env: NodeJS.ProcessEnv = {
    RAH_TOKEN: 'old-token-0123456789', RAH_PORT: '9000', CALLI_PORT: '9100', LIMI_SOCKET: '/team.sock', LIMI_KEY: 'k', LIMI_TOOLS_KEY: 't', OTHER: 'x',
  };
  adoptLegacyEnv(env);
  assert.deepEqual(env, {
    CALLI_TOKEN: 'old-token-0123456789', CALLI_PORT: '9100', CALLI_TEAM_SOCKET: '/team.sock', CALLI_TEAM_KEY: 'k', CALLI_TOOLS_KEY: 't', OTHER: 'x',
  });
  assert.equal(loadConfig({ ...env, HOME: tmpdir() }).token, 'old-token-0123456789', 'the token kept under its old name still signs in');
});

test('the socket and ~/.calli stay where an installation from before the rename has them', (t) => {
  const home = mkdtempSync(join(tmpdir(), 'calli-rename-home-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const config = (h: string) => loadConfig({ CALLI_TOKEN: 'token-0123456789abcdef', HOME: h });

  assert.equal(config(home).socket, join(home, SOCKET_IN_HOME), 'a new installation uses the new place');
  assert.equal(homeDirectory(home), join(home, '.calli'));

  mkdirSync(join(home, dirname(LEGACY_SOCKET_IN_HOME)), { recursive: true });
  mkdirSync(join(home, '.limi'));
  assert.equal(config(home).socket, join(home, LEGACY_SOCKET_IN_HOME), 'only the old directory exists: it is used, not moved');
  assert.equal(homeDirectory(home), join(home, '.limi'));

  mkdirSync(join(home, dirname(SOCKET_IN_HOME)), { recursive: true });
  mkdirSync(join(home, '.calli'));
  assert.equal(config(home).socket, join(home, SOCKET_IN_HOME), 'once moved, the new place wins');
  assert.equal(homeDirectory(home), join(home, '.calli'));
});

test('a message that already asks for progress under the old mark is not asked twice', () => {
  const old = 'hello\n\n<limi-progress>\n...\n</limi-progress>';
  assert.equal(progressInstructions(old), old);
  assert.match(progressInstructions('hello'), /<calli-progress>/);
});

test('limi still runs the CLI, and reads the hub address and token under their old names', async () => {
  const run = (bin: string, env: Record<string, string>) =>
    exec(process.execPath, [join(here, '../bin', bin), 'team', 'ls'], { env: { PATH: process.env.PATH ?? '', HOME: process.env.HOME ?? '', ...env } })
      .then(() => '', (err: { stderr: string }) => err.stderr);
  // No hub listens there: getting as far as connecting shows the token was found.
  const env = { LIMI_URL: 'http://127.0.0.1:9', RAH_TOKEN: 'token-0123456789abcdef' };
  for (const bin of ['calli', 'limi']) {
    const stderr = await run(bin, env);
    assert.doesNotMatch(stderr, /Set CALLI_TOKEN/, bin);
    assert.match(stderr, /fetch failed|ECONNREFUSED/, bin);
  }
  assert.match(await run('calli', { LIMI_URL: 'http://127.0.0.1:9' }), /Set CALLI_TOKEN/);
});

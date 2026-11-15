// install.sh is the one command a newcomer runs. These tests run the real script offline: it
// clones a throwaway repository holding the real setup.mjs and install-service.sh, with fake npm
// and systemctl on PATH (fixtures/install/) and a temporary HOME, so nothing on this machine changes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = join(dirname(fileURLToPath(import.meta.url)), '../../..');
const fakes = join(repo, 'packages/e2e/fixtures/install');
const git = (cwd, ...args) => {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
};

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-install-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const home = join(dir, 'home');
  const state = join(dir, 'state');
  const source = join(dir, 'source');
  for (const d of [home, state, join(source, 'scripts')]) mkdirSync(d, { recursive: true });
  for (const f of ['.env.example', 'scripts/setup.mjs', 'scripts/install-service.sh']) copyFileSync(join(repo, f), join(source, f));
  copyFileSync(join(fakes, 'restart.sh'), join(source, 'scripts/restart.sh'));
  git(source, 'init', '-q', '-b', 'main');
  git(source, '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '.');
  git(source, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'calli');
  const env = {
    PATH: `${fakes}:${process.env.PATH}`, HOME: home, USER: process.env.USER ?? 'user', SHELL: '/bin/bash',
    CALLI_REPO: `file://${source}`, CALLI_FAKE_STATE: state,
  };
  const run = (...args) => spawnSync('bash', [join(repo, 'install.sh'), ...args], { env, encoding: 'utf8' });
  const read = (name) => existsSync(join(state, name)) ? readFileSync(join(state, name), 'utf8') : '';
  const paths = {
    app: join(home, '.local/share/calli'),
    env: join(home, '.config/calli/.env'),
    unit: join(home, '.config/systemd/user/calli.service'),
  };
  return { run, read, state, home, paths };
}

const token = (file) => readFileSync(file, 'utf8').match(/^CALLI_TOKEN=(.*)$/m)?.[1];

test('a first install builds, writes the settings, starts the service and prints address and token', (t) => {
  const { run, read, home, paths } = fixture(t);
  const r = run();
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const env = readFileSync(paths.env, 'utf8');
  assert.match(token(paths.env), /^[0-9a-f]{48}$/);
  assert.match(env, new RegExp(`^CALLI_DATA_DIR=${home}/.local/state/calli/data$`, 'm'));
  assert.match(env, new RegExp(`^CALLI_WORKDIRS=${home}$`, 'm'));
  assert.deepEqual(read('npm.log').trim().split('\n'), ['install --no-audit --no-fund', 'run build']);
  assert.match(readFileSync(paths.unit, 'utf8'), new RegExp(`^WorkingDirectory=${paths.app}$`, 'm'));
  assert.match(read('systemctl.log'), /restart calli/);
  assert.match(r.stdout, /Calli is running: http:\/\/127\.0\.0\.1:8787\//);
  assert.match(r.stdout, new RegExp(`Token: ${token(paths.env)}`));
});

test('running it again updates in place: same token, and the restart waits for idle threads', (t) => {
  const { run, read, paths } = fixture(t);
  assert.equal(run().status, 0);
  const first = token(paths.env);
  const r = run();
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.equal(token(paths.env), first);
  assert.match(r.stdout, /Already there; kept as it is/);
  assert.equal(read('restart.log').trim(), 'restart.sh --no-build');
});

test('a dry run changes nothing', (t) => {
  const { run, read, home } = fixture(t);
  const r = run('--dry-run');
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /git clone/);
  assert.match(r.stdout, /Dry run: nothing was changed/);
  assert.equal(existsSync(join(home, '.local')), false);
  assert.equal(existsSync(join(home, '.config')), false);
  assert.equal(read('npm.log'), '');
});

test('a service that runs another checkout is left alone', (t) => {
  const { run, paths } = fixture(t);
  mkdirSync(dirname(paths.unit), { recursive: true });
  writeFileSync(paths.unit, '[Service]\nWorkingDirectory=/elsewhere/calli\n');
  const r = run();
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /already runs \/elsewhere\/calli; it was not changed/);
  assert.match(readFileSync(paths.unit, 'utf8'), /WorkingDirectory=\/elsewhere\/calli/);
  assert.match(r.stdout, /To start Calli:/);
});

test('without systemd it says how to start by hand', (t) => {
  const { run, state } = fixture(t);
  writeFileSync(join(state, 'no-systemd'), '');
  const r = run();
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /No systemd user session/);
  assert.match(r.stdout, /CALLI_ENV_FILE=.*npm start -w @calli\/daemon/);
});

test('a failed dependency install names the build tools to get', (t) => {
  const { run, state, paths } = fixture(t);
  writeFileSync(join(state, 'npm-install-fails'), '');
  const r = run();
  assert.notEqual(r.status, 0);
  assert.match(r.stdout, /build-essential/);
  assert.equal(existsSync(paths.env), false);
});

test('uninstall removes the service and the code, and keeps the settings', (t) => {
  const { run, paths } = fixture(t);
  assert.equal(run().status, 0);
  const r = run('--uninstall');
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.equal(existsSync(paths.app), false);
  assert.equal(existsSync(paths.unit), false);
  assert.equal(existsSync(paths.env), true);
});

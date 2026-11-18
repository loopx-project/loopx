// install.sh is the one command a newcomer runs. These tests run the real script offline: it
// sparsely clones a throwaway monorepo holding the real setup.mjs and install-service.sh, with fake npm
// and systemctl/ss/loginctl on PATH (fixtures/install/) and a temporary HOME, so no host service or
// port state affects the result and nothing on this machine changes.
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
  return r.stdout.trim();
};

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-install-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const home = join(dir, 'home');
  const state = join(dir, 'state');
  const source = join(dir, 'source');
  for (const d of [home, state, join(source, 'apps/workbench/scripts')]) mkdirSync(d, { recursive: true });
  for (const f of ['.env.example', 'scripts/setup.mjs', 'scripts/install-service.sh']) copyFileSync(join(repo, f), join(source, 'apps/workbench', f));
  copyFileSync(join(fakes, 'restart.sh'), join(source, 'apps/workbench/scripts/restart.sh'));
  writeFileSync(join(source, 'apps/workbench/package.json'), '{}');
  writeFileSync(join(source, 'apps/workbench/revision.txt'), 'first');
  mkdirSync(join(source, 'unrelated'));
  writeFileSync(join(source, 'unrelated/keep.txt'), 'not part of Workbench');
  git(source, 'init', '-q', '-b', 'main');
  git(source, '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '.');
  git(source, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'loochat');
  const env = {
    PATH: `${fakes}:${process.env.PATH}`, HOME: home, USER: process.env.USER ?? 'user', SHELL: '/bin/bash',
    LOOCHAT_REPO: `file://${source}`, LOOCHAT_FAKE_STATE: state, LOOCHAT_LOOPX_CORE_URL: 'http://127.0.0.1:18792',
  };
  const run = (...args) => spawnSync('bash', [join(repo, 'install.sh'), ...args], { env, encoding: 'utf8' });
  const read = (name) => existsSync(join(state, name)) ? readFileSync(join(state, name), 'utf8') : '';
  const paths = {
    checkout: join(home, '.local/share/loochat'),
    app: join(home, '.local/share/loochat/apps/workbench'),
    data: join(home, '.local/state/loochat/data'),
    env: join(home, '.config/loochat/.env'),
    unit: join(home, '.config/systemd/user/loochat.service'),
  };
  const runRef = (ref, ...args) => spawnSync('bash', [join(repo, 'install.sh'), ...args], { env: { ...env, LOOCHAT_REF: ref }, encoding: 'utf8' });
  return { run, runRef, source, read, state, home, paths };
}

const token = (file) => readFileSync(file, 'utf8').match(/^LOOCHAT_TOKEN=(.*)$/m)?.[1];

test('a first install builds, writes the settings, starts the service and prints address and token', (t) => {
  for (const ports of ['empty', 'unavailable', 'occupied']) {
    const { run, read, source, state, home, paths } = fixture(t);
    writeFileSync(join(state, 'ports'), ports);
    const r = run();
    assert.equal(r.status, 0, r.stdout + r.stderr);
    const env = readFileSync(paths.env, 'utf8');
    assert.match(token(paths.env), /^[0-9a-f]{48}$/);
    assert.match(env, new RegExp(`^LOOCHAT_DATA_DIR=${home}/.local/state/loochat/data$`, 'm'));
    assert.match(env, new RegExp(`^LOOCHAT_WORKDIRS=${home}$`, 'm'));
    assert.deepEqual(read('npm.log').trim().split('\n'), ['install --no-audit --no-fund', 'run build']);
    assert.match(readFileSync(paths.unit, 'utf8'), new RegExp(`^WorkingDirectory=${paths.app}$`, 'm'));
    assert.match(read('systemctl.log'), /restart loochat/);
    assert.match(r.stdout, /LooChat is running: http:\/\/127\.0\.0\.1:8787\//);
    assert.match(r.stdout, new RegExp(`Token: ${token(paths.env)}`));
    const revision = git(source, 'rev-parse', 'HEAD');
    assert.match(revision, /^[a-f0-9]{40}$/);
    assert.ok(r.stdout.includes(`Installed Workbench revision: ${revision}`));
    assert.ok(r.stdout.includes(`Workbench directory: ${paths.app}`));
    assert.equal(git(paths.checkout, 'rev-parse', 'HEAD'), revision);
    assert.equal(git(paths.checkout, 'sparse-checkout', 'list'), 'apps/workbench');
    assert.equal(existsSync(join(paths.app, 'packages/daemon/dist')), true);
    assert.equal(existsSync(join(paths.checkout, 'packages/daemon/dist')), false);
    assert.equal(existsSync(join(paths.checkout, 'unrelated')), false);
    assert.equal(read('ss.log').trim(), '-ltnp');
    assert.match(read('loginctl.log'), /show-user .* -p Linger --value/);
    if (ports === 'occupied') assert.match(r.stdout, /Port 8787 is held by another process \(PID 12345\)/);
    else assert.doesNotMatch(r.stdout, /Port 8787 is held/);
    assert.doesNotMatch(r.stdout, /To start LooChat:/);
  }
  const failed = fixture(t);
  writeFileSync(join(failed.state, 'restart-fails'), '');
  const result = failed.run();
  assert.notEqual(result.status, 0, result.stdout + result.stderr);
  assert.match(failed.read('systemctl.log'), /restart loochat/);
  assert.doesNotMatch(result.stdout, /LooChat is running:|To start LooChat:/);
});

test('running it again updates in place: same token, and the restart waits for idle threads', (t) => {
  const { run, read, state, paths } = fixture(t);
  assert.equal(run().status, 0);
  const first = token(paths.env);
  const settings = readFileSync(paths.env, 'utf8');
  mkdirSync(paths.data, { recursive: true });
  const data = join(paths.data, 'saved-thread.json');
  writeFileSync(data, '{"text":"keep this thread"}');
  const r = run();
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.equal(token(paths.env), first);
  assert.equal(readFileSync(paths.env, 'utf8'), settings);
  assert.equal(readFileSync(data, 'utf8'), '{"text":"keep this thread"}');
  assert.match(r.stdout, /Already there; kept as it is/);
  assert.equal(read('restart.log').trim(), 'restart.sh --no-build');
  writeFileSync(join(state, 'restart-fails'), '');
  const failed = run();
  assert.notEqual(failed.status, 0, failed.stdout + failed.stderr);
  assert.doesNotMatch(failed.stdout, /LooChat is running:|To start LooChat:/);
});

test('setup preserves current and historical tokens without appending a second current key', t => {
  const { run, paths } = fixture(t);
  assert.equal(run().status, 0);
  for (const key of ['LOOCHAT_TOKEN', 'CALLI_TOKEN', 'RAH_TOKEN']) {
    writeFileSync(paths.env, `${key}=fixture-existing-token\n`);
    const result = spawnSync(process.execPath, [join(paths.app, 'scripts/setup.mjs'), '--quiet', '--env', paths.env], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readFileSync(paths.env, 'utf8'), `${key}=fixture-existing-token\n`);
  }
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
  writeFileSync(paths.unit, '[Service]\nWorkingDirectory=/elsewhere/loochat\n');
  const r = run();
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /already runs \/elsewhere\/loochat; it was not changed/);
  assert.match(readFileSync(paths.unit, 'utf8'), /WorkingDirectory=\/elsewhere\/loochat/);
  assert.match(r.stdout, /To start LooChat:/);
});

test('without systemd it says how to start by hand', (t) => {
  const { run, state } = fixture(t);
  writeFileSync(join(state, 'no-systemd'), '');
  const r = run();
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /No systemd user session/);
  assert.match(r.stdout, /LOOCHAT_ENV_FILE=.*npm start -w @loopx\/loochat/);
});

test('a failed dependency install names the build tools to get', (t) => {
  const { run, state, paths } = fixture(t);
  writeFileSync(join(state, 'npm-install-fails'), '');
  const r = run();
  assert.notEqual(r.status, 0);
  assert.match(r.stdout, /build-essential/);
  assert.equal(existsSync(paths.env), false);
});

test('uninstall removes the service and the code, and keeps the settings and data', (t) => {
  const { run, paths } = fixture(t);
  assert.equal(run().status, 0);
  const settings = readFileSync(paths.env, 'utf8');
  mkdirSync(paths.data, { recursive: true });
  const data = join(paths.data, 'saved-thread.json');
  writeFileSync(data, '{"text":"keep this thread"}');
  const preview = run('--uninstall', '--dry-run');
  assert.equal(preview.status, 0, preview.stdout + preview.stderr);
  assert.equal(existsSync(paths.checkout), true);
  assert.equal(existsSync(paths.unit), true);
  const r = run('--uninstall');
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.equal(existsSync(paths.app), false);
  assert.equal(existsSync(paths.checkout), false);
  assert.equal(existsSync(paths.unit), false);
  assert.equal(existsSync(paths.env), true);
  assert.equal(readFileSync(paths.env, 'utf8'), settings);
  assert.equal(readFileSync(data, 'utf8'), '{"text":"keep this thread"}');
});

test('branch updates advance, while tags and full commit refs select exact versions', t => {
  const { run, runRef, source, paths } = fixture(t);
  const checkout = paths.checkout;
  const head = cwd => git(cwd, 'rev-parse', 'HEAD');
  const first = head(source);
  git(source, 'tag', 'release-test');
  writeFileSync(join(source, 'apps/workbench/revision.txt'), 'next');
  git(source, 'add', '.');
  git(source, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'next');
  const second = head(source);
  assert.notEqual(first, second);
  const install = runRef('release-test', '--no-service');
  assert.equal(install.status, 0, install.stdout + install.stderr);
  assert.equal(head(checkout), first);
  assert.equal(readFileSync(join(paths.app, 'revision.txt'), 'utf8'), 'first');
  assert.ok(install.stdout.includes(`Installed Workbench revision: ${first}`));
  assert.equal(run('--no-service').status, 0);
  assert.equal(head(checkout), second);
  assert.equal(readFileSync(join(paths.app, 'revision.txt'), 'utf8'), 'next');
  assert.equal(runRef(first, '--no-service').status, 0);
  assert.equal(head(checkout), first);
  const pinned = runRef(second, '--no-service');
  assert.equal(pinned.status, 0, pinned.stderr);
  assert.equal(head(checkout), second);
  assert.match(pinned.stdout, new RegExp('Installed Workbench revision: ' + second));
});

test('local edits and commits stop an update without overwriting the checkout', t => {
  const { run, paths } = fixture(t);
  assert.equal(run('--no-service').status, 0);
  const checkout = paths.checkout;
  const file = join(paths.app, 'package.json');
  writeFileSync(file, '{"local":true}');
  const dirty = run('--no-service');
  assert.notEqual(dirty.status, 0);
  assert.match(dirty.stderr, /local changes/);
  assert.equal(readFileSync(file, 'utf8'), '{"local":true}');
  git(checkout, 'add', file);
  git(checkout, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'local');
  const committed = run('--no-service');
  assert.notEqual(committed.status, 0);
  assert.match(committed.stderr, /local commits/);
  assert.equal(readFileSync(file, 'utf8'), '{"local":true}');
});

test('--no-service builds and prints a usable command without registering a service', t => {
  const { run, read, paths } = fixture(t);
  const result = run('--no-service');
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(read('systemctl.log'), '');
  assert.equal(existsSync(paths.unit), false);
  assert.equal(existsSync(join(paths.app, 'packages/daemon/dist')), true);
  assert.ok(result.stdout.includes(`cd ${paths.app} && LOOCHAT_ENV_FILE=${paths.env} npm start -w @loopx/loochat`));
});

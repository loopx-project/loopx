// Offline regression tests for scripts/update-node.sh against the LoopX monorepo layout
// (root package.json without "build", application under apps/workbench/).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const workbenchRoot = join(dirname(fileURLToPath(import.meta.url)), '../../..');
const updateScript = join(workbenchRoot, 'scripts/update-node.sh');
const realNpm = spawnSync('sh', ['-c', 'command -v npm'], { encoding: 'utf8' }).stdout.trim();

function git(cwd, ...args) {
  const res = spawnSync('git', args, { cwd, encoding: 'utf8' });
  assert.equal(res.status, 0, res.stderr || res.stdout);
  return res.stdout.trim();
}

function makeFixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'loochat-update-node-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const home = join(root, 'home');
  const bin = join(root, 'bin');
  const state = join(root, 'state');
  const upstream = join(root, 'upstream');
  for (const dir of [home, bin, state, join(upstream, 'apps/workbench/scripts'), join(upstream, 'unrelated')]) {
    mkdirSync(dir, { recursive: true });
  }

  // Root monorepo package.json deliberately has no "build" script, matching LoopX root.
  writeFileSync(join(upstream, 'package.json'), JSON.stringify({ name: 'loopx-monorepo-root', private: true }, null, 2));
  writeFileSync(join(upstream, 'unrelated/readme.txt'), 'not workbench\n');
  writeFileSync(
    join(upstream, 'apps/workbench/package.json'),
    JSON.stringify({
      name: '@loopx/workbench',
      private: true,
      scripts: {
        start: 'node start.mjs',
        build: 'node -e "require(\'fs\').mkdirSync(\'dist\', { recursive: true }); require(\'fs\').writeFileSync(\'dist/built.txt\', \'ok\')"',
      },
    }, null, 2),
  );
  writeFileSync(join(upstream, 'apps/workbench/start.mjs'), "console.log('fixture service started');\n");
  const restartScript = '#!/usr/bin/env sh\necho "restart $*" >> "$LOOCHAT_TEST_STATE/restart.log"\n[ ! -e "$LOOCHAT_TEST_STATE/restart-fails" ] || exit 1\ncd "$(dirname "$0")/.." && npm start --silent\n';
  writeFileSync(join(upstream, 'apps/workbench/scripts/restart.sh'), restartScript);
  chmodSync(join(upstream, 'apps/workbench/scripts/restart.sh'), 0o755);

  git(upstream, 'init', '-q', '-b', 'main');
  git(upstream, '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '.');
  git(upstream, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'v1');

  // Stand-in tools: systemctl tracks unit + drop-ins; curl returns idle threads + healthy server;
  // npm skips network install while delegating `npm run build` to the real npm binary.
  const systemctlShim = `#!/usr/bin/env bash
set -euo pipefail
UNIT="$HOME/.config/systemd/user/loochat.service"
DROP_DIR="$HOME/.config/systemd/user/loochat.service.d"
echo "$*" >> "$LOOCHAT_TEST_STATE/systemctl.log"
if [[ " $* " == *" show -p FragmentPath "* ]]; then
  [[ -f "$UNIT" ]] && echo "$UNIT"
  exit 0
fi
if [[ " $* " == *" show -p WorkingDirectory "* ]]; then
  pin=""
  if [[ -d "$DROP_DIR" ]]; then
    for f in $(ls "$DROP_DIR"/*.conf 2>/dev/null | sort); do
      val=$(sed -n 's/^WorkingDirectory=//p' "$f" | tail -n 1)
      [[ -n "$val" ]] && pin="$val"
    done
  fi
  if [[ -n "$pin" ]]; then echo "$pin"; elif [[ -f "$UNIT" ]]; then sed -n 's/^WorkingDirectory=//p' "$UNIT"; fi
  exit 0
fi
exit 0
`;
  const curlShim = `#!/usr/bin/env sh
case "$*" in
  */api/threads*) printf '[]' ;;
  */api/health*) exit 0 ;;
  *) exit 1 ;;
esac
`;
  const npmShim = `#!/usr/bin/env sh
echo "$*" >> "$LOOCHAT_TEST_STATE/npm.log"
if [ "$1" = "install" ]; then exit 0; fi
exec "${realNpm}" "$@"
`;
  for (const [name, body] of [['systemctl', systemctlShim], ['curl', curlShim], ['npm', npmShim]]) {
    const file = join(bin, name);
    writeFileSync(file, body);
    chmodSync(file, 0o755);
  }

  const clone = join(home, 'code/loopx');
  git(root, 'clone', '-q', upstream, clone);
  const unitDir = join(home, '.config/systemd/user');
  mkdirSync(unitDir, { recursive: true });
  writeFileSync(join(unitDir, 'loochat.service'), `[Service]\nWorkingDirectory=${join(clone, 'apps/workbench')}\n`);

  const advanceUpstream = (label) => {
    writeFileSync(join(upstream, 'apps/workbench/revision.txt'), label);
    git(upstream, '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '.');
    git(upstream, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', label);
    return git(upstream, 'rev-parse', '--short', 'HEAD');
  };

  const runUpdater = (repoArg, dry = '0') =>
    spawnSync('sh', [updateScript, 'loochat', join(home, 'daemon.sock'), repoArg, dry, '0'], {
      env: { ...process.env, HOME: home, PATH: `${bin}:${process.env.PATH}`, LOOCHAT_TEST_STATE: state },
      encoding: 'utf8',
    });

  return { root, home, state, upstream, clone, advanceUpstream, runUpdater };
}

// Fetch metadata may change; the installed checkout, including untracked runtime files, may not.
function checkoutBytes(root) {
  return Object.fromEntries(readdirSync(root, { withFileTypes: true }).filter(entry => entry.name !== '.git')
    .flatMap(entry => entry.isDirectory()
      ? Object.entries(checkoutBytes(join(root, entry.name))).map(([path, bytes]) => [`${entry.name}/${path}`, bytes])
      : [[entry.name, readFileSync(join(root, entry.name)).toString('base64')]]));
}

for (const mode of ['branch', 'installed-detached', 'detached']) {
  test(`first update with a failed build preserves the live checkout and can restart it (${mode})`, t => {
    const { home, state, upstream, clone, advanceUpstream, runUpdater } = makeFixture(t);
    if (mode !== 'branch') git(clone, 'checkout', '--detach', '-q', 'HEAD');
    if (mode === 'installed-detached') {
      git(clone, 'sparse-checkout', 'set', 'apps/workbench');
      writeFileSync(join(clone, '.git/loochat-installed-revision'), `${git(clone, 'rev-parse', 'HEAD')}\n`);
    }
    const oldWorkdir = join(clone, 'apps/workbench');
    writeFileSync(join(oldWorkdir, 'runtime-local.txt'), 'keep the installed runtime bytes\n');
    const marker = join(clone, '.git/loochat-installed-revision');
    const before = {
      head: git(clone, 'rev-parse', 'HEAD'),
      marker: existsSync(marker) ? readFileSync(marker, 'utf8') : null,
      bytes: checkoutBytes(clone),
    };
    // The candidate cannot start without a successful build. A checkout/merge in place
    // would destroy the old service's npm start even though the candidate never passed.
    writeFileSync(join(upstream, 'apps/workbench/package.json'), JSON.stringify({
      name: '@loopx/workbench', private: true, scripts: { build: 'exit 1' },
    }));
    const candidate = advanceUpstream('first-update-broken');
    const failed = runUpdater(clone);
    assert.equal(failed.status, 0, failed.stdout + failed.stderr);
    assert.match(failed.stdout, /^fail build failed; the running daemon was left alone$/m);
    assert.equal(existsSync(join(home, `.local/share/loochat-${candidate}`)), false);
    assert.equal(existsSync(join(home, '.config/systemd/user/loochat.service.d')), false, 'no service switch before a successful build');
    assert.equal(existsSync(join(state, 'restart.log')), false, 'the updater did not restart the active service');
    const restarted = spawnSync(join(oldWorkdir, 'scripts/restart.sh'), ['--no-build'], {
      env: { ...process.env, HOME: home, LOOCHAT_TEST_STATE: state }, encoding: 'utf8',
    });
    assert.deepEqual({
      head: git(clone, 'rev-parse', 'HEAD'),
      marker: existsSync(marker) ? readFileSync(marker, 'utf8') : null,
      bytes: checkoutBytes(clone), restartStatus: restarted.status,
    }, { ...before, restartStatus: 0 }, restarted.stderr);
    assert.match(restarted.stdout, /fixture service started/);
  });
}

test('update-node builds apps/workbench when given either monorepo root or apps/workbench subdirectory', (t) => {
  const { home, state, clone, advanceUpstream, runUpdater } = makeFixture(t);

  // --check selects the fetched revision without advancing the service's original checkout.
  const originalHead = git(clone, 'rev-parse', 'HEAD');
  writeFileSync(join(clone, 'apps/workbench/runtime-local.txt'), 'not release source');
  const rev2 = advanceUpstream('v2');
  const check = runUpdater(clone, '1');
  assert.match(check.stdout, new RegExp(`would update: .* -> loochat-${rev2}`));
  assert.equal(git(clone, 'rev-parse', 'HEAD'), originalHead);

  const res2 = runUpdater(clone, '0');
  assert.equal(res2.status, 0, res2.stdout + res2.stderr);
  assert.match(res2.stdout, new RegExp(`done: the service runs loochat-${rev2}`));
  const snap2 = join(home, `.local/share/loochat-${rev2}`);
  assert.equal(readFileSync(join(snap2, 'dist/built.txt'), 'utf8'), 'ok');
  assert.equal(readFileSync(join(snap2, 'revision.txt'), 'utf8'), 'v2', 'build the fetched tree, not the original checkout');
  assert.equal(existsSync(join(snap2, 'runtime-local.txt')), false, 'untracked runtime data does not enter a release');
  assert.equal(git(clone, 'rev-parse', 'HEAD'), originalHead, 'the old startup directory remains restartable after switching too');
  assert.equal(existsSync(join(snap2, 'unrelated')), false, 'snapshot contains workbench app root, not monorepo extras');
  const pinPath = join(home, '.config/systemd/user/loochat.service.d/90-loochat-release.conf');
  assert.match(readFileSync(pinPath, 'utf8'), new RegExp(`^WorkingDirectory=${snap2}$`, 'm'));
  assert.match(readFileSync(join(state, 'restart.log'), 'utf8'), /restart --no-build/);

  // Re-running on an already-switched snapshot reports current without rebuilding.
  const same = runUpdater(clone, '0');
  assert.equal(same.status, 0);
  assert.match(same.stdout, new RegExp(`^current: the service already runs loochat-${rev2}$`, 'm'));

  // Advance again and pass <clone>/apps/workbench directly.
  const rev3 = advanceUpstream('v3');
  const res3 = runUpdater(join(clone, 'apps/workbench'), '0');
  assert.equal(res3.status, 0, res3.stdout + res3.stderr);
  assert.match(res3.stdout, new RegExp(`done: the service runs loochat-${rev3}`));
  assert.match(readFileSync(pinPath, 'utf8'), new RegExp(`^WorkingDirectory=${join(home, `.local/share/loochat-${rev3}`)}$`, 'm'));

  // Unrelated subdirectories inside the monorepo still fail closed.
  const bad = runUpdater(join(clone, 'unrelated'), '0');
  assert.match(bad.stdout, /^fail no clone at /m);
});

test('update-node leaves the active service untouched when build fails and supports detached install.sh clones', (t) => {
  const { home, upstream, advanceUpstream, runUpdater } = makeFixture(t);

  // Simulate an install.sh sparse detached checkout at ~/.local/share/loochat with default empty repo arg.
  const sparseClone = join(home, '.local/share/loochat');
  mkdirSync(dirname(sparseClone), { recursive: true });
  git(home, 'clone', '--filter=blob:none', '--depth', '1', '--no-checkout', `file://${upstream}`, sparseClone);
  git(sparseClone, 'sparse-checkout', 'set', 'apps/workbench');
  git(sparseClone, 'fetch', '--depth', '1', 'origin', 'main');
  git(sparseClone, 'checkout', '--detach', '-q', 'FETCH_HEAD');
  writeFileSync(join(sparseClone, '.git/loochat-installed-revision'), `${git(sparseClone, 'rev-parse', 'HEAD')}\n`);
  const installed = readFileSync(join(sparseClone, '.git/loochat-installed-revision'), 'utf8');
  writeFileSync(join(home, '.config/systemd/user/loochat.service'), `[Service]\nWorkingDirectory=${join(sparseClone, 'apps/workbench')}\n`);

  const rev2 = advanceUpstream('v2-sparse');
  const ok = runUpdater('', '0');
  assert.equal(ok.status, 0, ok.stdout + ok.stderr);
  assert.match(ok.stdout, new RegExp(`done: the service runs loochat-${rev2}`));
  assert.equal(git(sparseClone, 'rev-parse', 'HEAD'), installed.trim());
  assert.equal(readFileSync(join(sparseClone, '.git/loochat-installed-revision'), 'utf8'), installed);
  assert.equal(readFileSync(join(home, `.local/share/loochat-${rev2}/revision.txt`), 'utf8'), 'v2-sparse');

  // Break the workbench build on upstream and verify active WorkingDirectory stays on rev2.
  writeFileSync(
    join(upstream, 'apps/workbench/package.json'),
    JSON.stringify({ name: '@loopx/workbench', private: true, scripts: { build: 'exit 1' } }, null, 2),
  );
  git(upstream, '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '.');
  git(upstream, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'broken-build');
  const revBroken = git(upstream, 'rev-parse', '--short', 'HEAD');

  const failed = runUpdater('', '0');
  assert.match(failed.stdout, /^fail build failed; the running daemon was left alone$/m);
  assert.equal(existsSync(join(home, `.local/share/loochat-${revBroken}`)), false);
  const pinPath = join(home, '.config/systemd/user/loochat.service.d/90-loochat-release.conf');
  assert.match(readFileSync(pinPath, 'utf8'), new RegExp(`^WorkingDirectory=${join(home, `.local/share/loochat-${rev2}`)}$`, 'm'));
});

test('a failed first service switch can restart the untouched original checkout', t => {
  const { home, state, upstream, clone, advanceUpstream, runUpdater } = makeFixture(t);
  const oldWorkdir = join(clone, 'apps/workbench');
  const head = git(clone, 'rev-parse', 'HEAD'), bytes = checkoutBytes(clone);
  writeFileSync(join(upstream, 'apps/workbench/scripts/restart.sh'), '#!/usr/bin/env sh\nexit 1\n');
  advanceUpstream('cannot-start-candidate');
  const failed = runUpdater(clone);
  assert.equal(failed.status, 0, failed.stderr);
  assert.match(failed.stdout, /^fail the switch failed; back on workbench$/m);
  assert.match(failed.stdout, /fixture service started/, 'rollback executed the original npm start');
  assert.equal(git(clone, 'rev-parse', 'HEAD'), head);
  assert.deepEqual(checkoutBytes(clone), bytes);
  assert.match(readFileSync(join(home, '.config/systemd/user/loochat.service.d/90-loochat-release.conf'), 'utf8'),
    new RegExp(`^WorkingDirectory=${oldWorkdir}$`, 'm'));
  assert.match(readFileSync(join(state, 'restart.log'), 'utf8'), /restart --no-build/);
});

test('branch selection preserves local-ahead releases and refuses divergent upstream without merging', t => {
  const { home, clone, advanceUpstream, runUpdater } = makeFixture(t);
  writeFileSync(join(clone, 'apps/workbench/local.txt'), 'local-ahead revision');
  git(clone, 'add', 'apps/workbench/local.txt');
  git(clone, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'local-ahead');
  const head = git(clone, 'rev-parse', 'HEAD'), short = git(clone, 'rev-parse', '--short', 'HEAD');
  const ok = runUpdater(clone);
  assert.match(ok.stdout, new RegExp(`done: the service runs loochat-${short}`));
  assert.equal(readFileSync(join(home, `.local/share/loochat-${short}/local.txt`), 'utf8'), 'local-ahead revision');
  const divergent = advanceUpstream('upstream-diverged');
  const refused = runUpdater(clone);
  assert.match(refused.stdout, /^fail the clone has moved away from origin\/main; it needs a person$/m);
  assert.equal(git(clone, 'rev-parse', 'HEAD'), head);
  assert.equal(existsSync(join(home, `.local/share/loochat-${divergent}`)), false);
});

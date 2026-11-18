// Offline regression tests for scripts/update-node.sh against the LoopX monorepo layout
// (root package.json without "build", application under apps/workbench/).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
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
      scripts: { build: 'node -e "require(\'fs\').mkdirSync(\'dist\', { recursive: true }); require(\'fs\').writeFileSync(\'dist/built.txt\', \'ok\')"' },
    }, null, 2),
  );
  const restartScript = '#!/usr/bin/env sh\necho "restart $*" >> "$LOOCHAT_TEST_STATE/restart.log"\n[ ! -e "$LOOCHAT_TEST_STATE/restart-fails" ]\n';
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

test('update-node builds apps/workbench when given either monorepo root or apps/workbench subdirectory', (t) => {
  const { home, state, clone, advanceUpstream, runUpdater } = makeFixture(t);

  // Advance upstream, run --check first (which fast-forwards the clone without switching), then run real update.
  const rev2 = advanceUpstream('v2');
  const check = runUpdater(clone, '1');
  assert.match(check.stdout, new RegExp(`would update: .* -> loochat-${rev2}`));

  const res2 = runUpdater(clone, '0');
  assert.equal(res2.status, 0, res2.stdout + res2.stderr);
  assert.match(res2.stdout, new RegExp(`done: the service runs loochat-${rev2}`));
  const snap2 = join(home, `.local/share/loochat-${rev2}`);
  assert.equal(readFileSync(join(snap2, 'dist/built.txt'), 'utf8'), 'ok');
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
  writeFileSync(join(home, '.config/systemd/user/loochat.service'), `[Service]\nWorkingDirectory=${join(sparseClone, 'apps/workbench')}\n`);

  const rev2 = advanceUpstream('v2-sparse');
  const ok = runUpdater('', '0');
  assert.equal(ok.status, 0, ok.stdout + ok.stderr);
  assert.match(ok.stdout, new RegExp(`done: the service runs loochat-${rev2}`));

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

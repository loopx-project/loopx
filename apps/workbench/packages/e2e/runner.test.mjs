import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const rootPackage = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8'));
const e2ePackage = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

// Exercise the real npm entry points in a tiny repository, without Vite or browsers.
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'calli-e2e-runner-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const e2e = join(root, 'packages/e2e');
  mkdirSync(join(e2e, 'test'), { recursive: true });
  writeFileSync(join(root, 'package.json'), JSON.stringify({
    private: true,
    workspaces: ['packages/*'],
    scripts: { build: 'node build.mjs', 'test:e2e': rootPackage.scripts['test:e2e'] },
  }));
  writeFileSync(join(e2e, 'package.json'), JSON.stringify(e2ePackage));
  copyFileSync(new URL('./run.mjs', import.meta.url), join(e2e, 'run.mjs'));
  writeFileSync(join(root, 'events'), '');
  writeFileSync(join(root, 'build.mjs'), `
    import { appendFileSync } from 'node:fs';
    appendFileSync('events', 'build:start\\n');
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (process.env.RUNNER_BUILD_FAIL) process.exit(7);
    appendFileSync('events', 'build:done\\n');
  `);
  for (const name of ['one', 'two', 'three']) {
    writeFileSync(join(e2e, 'test', `${name}.spec.mjs`), `
      import { test } from 'node:test';
      import assert from 'node:assert/strict';
      import { appendFileSync, readFileSync } from 'node:fs';
      const events = new URL('../../../events', import.meta.url);
      test('selected ${name}', async () => {
        assert.match(readFileSync(events, 'utf8'), /build:done/);
        appendFileSync(events, 'start:${name}\\n');
        await new Promise((resolve) => setTimeout(resolve, 300));
        appendFileSync(events, 'end:${name}\\n');
        assert.ok(!process.env.RUNNER_TEST_FAIL);
      });
      test('unselected ${name}', () => assert.ok(!process.env.RUNNER_FILTER));
    `);
  }
  return {
    run(args = ['run', 'test:e2e'], env = {}) {
      const childEnv = { ...process.env, ...env };
      // This is a separate CLI invocation, not a nested node:test worker.
      delete childEnv.NODE_TEST_CONTEXT;
      return spawnSync('npm', args, {
        cwd: root,
        env: childEnv,
        encoding: 'utf8',
        timeout: 20_000,
      });
    },
    events() { return readFileSync(join(root, 'events'), 'utf8').trim().split('\n'); },
  };
}

test('root E2E entry builds once before workers and limits concurrency to two', (t) => {
  const repo = fixture(t);
  const result = repo.run();
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const events = repo.events();
  assert.deepEqual(events.slice(0, 2), ['build:start', 'build:done']);
  assert.equal(events.filter((line) => line === 'build:start').length, 1);
  let active = 0;
  let peak = 0;
  for (const event of events.slice(2)) {
    active += event.startsWith('start:') ? 1 : -1;
    peak = Math.max(peak, active);
    assert.ok(active >= 0 && active <= 2, events.join('\n'));
  }
  assert.equal(active, 0);
  assert.equal(peak, 2);
  assert.equal(events.filter((line) => line.startsWith('end:')).length, 3);
});

test('workspace E2E entry forwards file selection and Node flags, rebuilding on each invocation', (t) => {
  const repo = fixture(t);
  for (let i = 0; i < 2; i++) {
    const result = repo.run([
      'test', '-w', '@calli/e2e', '--', '--test-name-pattern=^selected', 'test/two.spec.mjs',
    ], { RUNNER_FILTER: '1' });
    assert.equal(result.status, 0, result.stdout + result.stderr);
  }
  assert.deepEqual(repo.events(), [
    'build:start', 'build:done', 'start:two', 'end:two',
    'build:start', 'build:done', 'start:two', 'end:two',
  ]);
});

test('a concurrency override without file paths still runs the whole suite', (t) => {
  const repo = fixture(t);
  const result = repo.run(['run', 'test:e2e', '--', '--test-concurrency=1']);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.deepEqual(repo.events(), [
    'build:start', 'build:done',
    'start:one', 'end:one', 'start:three', 'end:three', 'start:two', 'end:two',
  ]);
});

test('a failed build stops before any test starts', (t) => {
  const repo = fixture(t);
  const result = repo.run(undefined, { RUNNER_BUILD_FAIL: '1' });
  assert.equal(result.status, 7, result.stdout + result.stderr);
  assert.deepEqual(repo.events(), ['build:start']);
});

test('a failing test makes the E2E command fail', (t) => {
  const repo = fixture(t);
  const result = repo.run(['run', 'test:e2e', '--', 'test/one.spec.mjs'], { RUNNER_TEST_FAIL: '1' });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.deepEqual(repo.events(), ['build:start', 'build:done', 'start:one', 'end:one']);
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { resolveTestPython } from "../../scripts/test-python.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));

test("test subprocess discovery selects a compatible checkout Python", () => {
  const python = resolveTestPython();
  const probe = spawnSync(python, ["-c", "import json,sys,loopx; print(json.dumps({'version':list(sys.version_info[:2]),'source':loopx.__file__}))"], {
    cwd: root, encoding: "utf8",
  });
  assert.equal(probe.status, 0, probe.stderr);
  const result = JSON.parse(probe.stdout);
  assert.ok(result.version[0] > 3 || result.version[0] === 3 && result.version[1] >= 11);
  const sourceInCheckout = relative(root, resolve(result.source));
  assert.ok(sourceInCheckout && !sourceInCheckout.startsWith("..") && !isAbsolute(sourceInCheckout), result.source);
});

test("an invalid explicit test Python never falls back silently", t => {
  const directory = mkdtempSync(join(tmpdir(), "loopx-test-python-invalid-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const missing = join(directory, "missing-python");
  assert.throws(() => resolveTestPython({ env: { ...process.env, LOOPX_TEST_PYTHON: missing } }),
    /LOOPX_TEST_PYTHON does not resolve to Python 3\.11\+/);
  const current = resolveTestPython();
  assert.equal(resolveTestPython({ env: {
    ...process.env,
    LOOPX_TEST_PYTHON: current,
    LOOPX_PYTHON_BIN: missing,
    LOOPX_PYTHON: missing,
  } }), current, "the test override takes precedence over legacy browser overrides");
});

test("a reported Python 3.9 is rejected even when the executable starts", t => {
  if (process.platform === "win32") {
    t.skip("the POSIX fake executable is covered on the Unix test lane");
    return;
  }
  const directory = mkdtempSync(join(tmpdir(), "loopx-test-python-old-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const oldPython = join(directory, "python3");
  writeFileSync(oldPython, "#!/bin/sh\nprintf '%s\\n' '{\"executable\":\"/usr/bin/python3\",\"version\":[3,9]}'\n", { mode: 0o755 });
  assert.throws(() => resolveTestPython({ env: { ...process.env, LOOPX_TEST_PYTHON: oldPython } }),
    /LOOPX_TEST_PYTHON does not resolve to Python 3\.11\+/);
});

test("a worktree venv wins over an unusable system python3", t => {
  if (process.platform === "win32") {
    t.skip("POSIX launcher precedence; Windows discovery runs in the native CI lane");
    return;
  }
  const directory = mkdtempSync(join(tmpdir(), "loopx-test-python-venv-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  mkdirSync(join(directory, "scripts"));
  mkdirSync(join(directory, ".venv", "bin"), { recursive: true });
  const fakeBin = join(directory, "bin");
  mkdirSync(fakeBin);
  copyFileSync(join(root, "scripts", "loopx-python.sh"), join(directory, "scripts", "loopx-python.sh"));
  symlinkSync(resolveTestPython(), join(directory, ".venv", "bin", "python"));
  const systemMarker = join(directory, "system-python-was-used");
  const fakeSystem = join(fakeBin, "python3");
  writeFileSync(fakeSystem, `#!/bin/sh\ntouch '${systemMarker}'\nexit 1\n`, { mode: 0o755 });
  writeFileSync(join(directory, ".loopx-python"), `${fakeSystem}\n`);
  const env: NodeJS.ProcessEnv = { ...process.env, PATH: `${fakeBin}:/usr/bin:/bin` };
  delete env.LOOPX_TEST_PYTHON;
  delete env.LOOPX_PYTHON_BIN;
  delete env.LOOPX_PYTHON;
  delete env.VIRTUAL_ENV;
  const selected = resolveTestPython({ env, repoRoot: directory });
  assert.equal(selected, join(directory, ".venv", "bin", "python"));
  assert.equal(existsSync(systemMarker), false);
});

test("test and browser smokes may not introduce bare python or python3 subprocess fallbacks", () => {
  const directories = ["tests/control_plane_ts", "examples", "apps/presentation/dashboard/smoke"];
  const offenders: string[] = [];
  // A bare `python` alias is not guaranteed to exist (and may point at an
  // incompatible interpreter), so direct launches, fallbacks and assigned
  // defaults must both route through resolveTestPython().
  const direct = /\b(?:spawn|spawnSync|execFile|execFileSync)\s*\(\s*["']python3?["']/;
  // A promisified or aliased launcher starts the same bare alias, so the guard
  // must see through the wrapper instead of accepting the indirection.
  const launchers = "spawn|spawnSync|execFile|execFileSync";
  const promisified = new RegExp(`\\b(?:promisify|util\\s*\\.\\s*promisify)\\s*\\(\\s*(?:${launchers})\\s*\\)\\s*\\(\\s*["']python3?["']`);
  const aliasBinding = new RegExp(`\\b(?:const|let|var)\\s+(\\w+)\\s*=\\s*(?:promisify|util\\s*\\.\\s*promisify)\\s*\\(\\s*(?:${launchers})\\s*\\)`, "g");
  const aliasedLaunch = (source: string): boolean => [...source.matchAll(aliasBinding)]
    .map(match => new RegExp(`\\b${match[1]}\\s*\\(\\s*["']python3?["']`))
    .some(pattern => pattern.test(source));
  const fallback = /(?:\?\?|\|\|)\s*["']python3?["']/;
  const conditionalFallback = /\?\s*[^:;\n]+\s*:\s*["']python3?["']/;
  const assigned = /\b(?:const|let)\s+\w+\s*=\s*["']python3?["']/;
  const bare = JSON.stringify("python3");
  const barePython = JSON.stringify("python");
  assert.ok(direct.test(`spawn(${bare}, ["-m", "loopx.cli"])`));
  assert.ok(direct.test(`spawn(${barePython}, ["-m", "loopx.cli"])`));
  assert.ok(direct.test(`spawnSync(${barePython}, ["-c", "raise SystemExit(0)"])`));
  assert.ok(promisified.test(`promisify(execFile)(${bare}, ["-m", "loopx.cli"])`));
  assert.ok(promisified.test(`util.promisify(execFileSync)(${barePython}, [])`));
  assert.ok(aliasedLaunch(`const run = promisify(execFile);\nrun(${bare}, ["-m", "loopx.cli"]);`));
  assert.ok(aliasedLaunch(`const run = util.promisify(spawnSync);\nawait run(${barePython}, []);`));
  assert.ok(fallback.test(`process.env.LOOPX_TEST_PYTHON ?? ${bare}`));
  assert.ok(fallback.test(`process.env.LOOPX_TEST_PYTHON ?? ${barePython}`));
  assert.ok(fallback.test(`process.env.NEW_TEST_PYTHON || ${bare}`));
  assert.ok(fallback.test(`process.env.NEW_TEST_PYTHON || ${barePython}`));
  assert.ok(conditionalFallback.test(
    `process.env.LOOPX_TEST_PYTHON ?? (existsSync(repositoryPython) ? repositoryPython : ${bare})`,
  ));
  assert.ok(conditionalFallback.test(
    `process.env.LOOPX_TEST_PYTHON ?? (existsSync(repositoryPython) ? repositoryPython : ${barePython})`,
  ));
  assert.ok(assigned.test(`const PYTHON = ${bare}`));
  assert.ok(assigned.test(`const PYTHON = ${barePython}`));
  assert.ok(assigned.test(`const testInterpreter = ${bare}`));
  assert.equal(direct.test(`validation_command_argv: [${bare}, "-m", "pytest"]`), false);
  assert.equal(direct.test(`validation_command_argv: [${barePython}, "-m", "pytest"]`), false);
  assert.equal(promisified.test(`promisify(execFile)(${JSON.stringify("/usr/bin/python3")}, [])`), false);
  // A resolved interpreter or an unrelated helper argument is not a launch.
  assert.equal(aliasedLaunch(`const run = promisify(execFile);\nrun(PYTHON, ["-m", "loopx.cli"]);`), false);
  assert.equal(direct.test(`qualificationHelperArgv(${bare})`), false);
  // An absolute path or a versioned executable is a resolved interpreter, not a bare alias.
  assert.equal(direct.test(`spawnSync(${JSON.stringify("/usr/bin/python3")}, [])`), false);
  assert.equal(assigned.test(`const executable = ${JSON.stringify("/opt/loopx-qualification/bin/python")}`), false);
  function inspect(directory: string) {
    for (const entry of readdirSync(join(root, directory), { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) inspect(path);
      else if (/\.(?:cjs|js|mjs|mts|ts)$/.test(entry.name)) {
        const source = readFileSync(join(root, path), "utf8");
        if (direct.test(source) || promisified.test(source) || aliasedLaunch(source)
          || fallback.test(source) || conditionalFallback.test(source)
          || assigned.test(source)) offenders.push(path);
      }
    }
  }
  for (const directory of directories) inspect(directory);
  assert.deepEqual(offenders, [], `Use scripts/test-python.mjs for Python subprocesses: ${offenders.join(", ")}`);
});

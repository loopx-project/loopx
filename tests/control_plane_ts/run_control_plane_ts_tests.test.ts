import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdtempSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";

import {
  analyzeCliArgs,
  buildControlPlaneRunPlan,
  buildNodeTestArgs,
  createScopedCompileCache,
  HEAVY_CONTROL_PLANE_TEST_FILES,
  listDefaultControlPlaneTestFiles,
  resolveTestConcurrency,
  runControlPlaneTests,
  shouldEnableCompileCache,
} from "../../scripts/run_control_plane_ts_tests.mjs";

const RUNNER_SCRIPT = fileURLToPath(
  new URL("../../scripts/run_control_plane_ts_tests.mjs", import.meta.url),
);

function makeTempWorkspace(t: TestContext, prefix = "loopx-ts-runner-test-") {
  const root = mkdtempSync(join(tmpdir(), prefix));
  chmodSync(root, 0o700);
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

function cleanRunnerEnv(extra: Record<string, string | undefined> = {}) {
  const env: Record<string, string | undefined> = { ...process.env };
  delete env.LOOPX_TS_TEST_CONCURRENCY;
  delete env.NODE_COMPILE_CACHE;
  delete env.NODE_DISABLE_COMPILE_CACHE;
  // Isolate nested `node --test` subprocesses from the outer runner's context.
  delete env.NODE_TEST_CONTEXT;
  // Clear inherited coverage directories so compile-cache tests observe clean opt-outs.
  env.NODE_V8_COVERAGE = "";
  for (const [key, value] of Object.entries(extra)) {
    if (value === undefined) {
      delete env[key];
    } else {
      env[key] = value;
    }
  }
  return env;
}

function runRunnerSubprocess(
  args: readonly string[],
  env: Record<string, string | undefined>,
) {
  return new Promise<{
    code: number | null;
    signal: NodeJS.Signals | null;
    stdout: string;
    stderr: string;
  }>((resolve, reject) => {
    const child = spawn(process.execPath, [RUNNER_SCRIPT, ...args], {
      env: env as NodeJS.ProcessEnv,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", reject);
    child.once("close", (code, signal) => {
      resolve({ code, signal, stdout, stderr });
    });
  });
}

test("resolveTestConcurrency clamps adaptive half-CPU sizing within [4, 8]", () => {
  const cases: readonly [number, number][] = [
    [1, 4],
    [2, 4],
    [4, 4],
    [8, 4],
    [9, 4],
    [10, 5],
    [12, 6],
    [14, 7],
    [16, 8],
    [18, 8],
    [32, 8],
    [0, 4],
    [Number.NaN, 4],
  ];
  for (const [cpus, expected] of cases) {
    assert.equal(
      resolveTestConcurrency({}, cpus),
      expected,
      `expected concurrency ${expected} for ${cpus} logical CPUs`,
    );
  }
});

test("resolveTestConcurrency honors positive integer LOOPX_TS_TEST_CONCURRENCY and rejects invalid values", () => {
  assert.equal(resolveTestConcurrency({ LOOPX_TS_TEST_CONCURRENCY: "1" }, 16), 1);
  assert.equal(resolveTestConcurrency({ LOOPX_TS_TEST_CONCURRENCY: "7" }, 16), 7);
  assert.equal(
    resolveTestConcurrency({ LOOPX_TS_TEST_CONCURRENCY: "  16  " }, 8),
    16,
  );

  for (const invalid of [
    "",
    "   ",
    "0",
    "-4",
    "4.5",
    "4abc",
    "Infinity",
    "NaN",
    "99999999999999999999",
  ]) {
    assert.equal(
      resolveTestConcurrency({ LOOPX_TS_TEST_CONCURRENCY: invalid }, 16),
      8,
      `invalid override ${JSON.stringify(invalid)} must fall back to clamped CPU concurrency`,
    );
  }
});

test("shouldEnableCompileCache and createScopedCompileCache enforce opt-outs and 0o700 permissions", (t) => {
  const tmpRoot = makeTempWorkspace(t);

  assert.equal(shouldEnableCompileCache({}), true);
  assert.equal(shouldEnableCompileCache({ NODE_V8_COVERAGE: "" }), true);
  assert.equal(
    shouldEnableCompileCache({ NODE_COMPILE_CACHE: "/tmp/custom-cache" }),
    false,
  );
  assert.equal(
    shouldEnableCompileCache({ NODE_DISABLE_COMPILE_CACHE: "1" }),
    false,
  );
  assert.equal(
    shouldEnableCompileCache({ NODE_V8_COVERAGE: "/tmp/v8-cov" }),
    false,
  );

  const disabled = createScopedCompileCache({
    env: { NODE_DISABLE_COMPILE_CACHE: "1" },
    tmpRoot,
  });
  assert.equal(disabled.cacheDir, null);
  assert.deepEqual(readdirSync(tmpRoot), []);

  const enabled = createScopedCompileCache({ env: {}, tmpRoot });
  assert.notEqual(enabled.cacheDir, null);
  assert.equal(existsSync(enabled.cacheDir!), true);
  if (typeof process.getuid === "function") {
    assert.equal(lstatSync(enabled.cacheDir!).mode & 0o777, 0o700);
  }
  enabled.cleanup();
  enabled.cleanup();
  assert.equal(existsSync(enabled.cacheDir!), false);
});

test("buildNodeTestArgs resolves sorted defaults, handles flag-only argv, and preserves explicit targets", (t) => {
  const repoRoot = makeTempWorkspace(t);
  const testDir = join(repoRoot, "tests", "control_plane_ts");
  mkdirSync(testDir, { recursive: true });
  writeFileSync(join(testDir, "zeta.test.ts"), "");
  writeFileSync(join(testDir, "alpha.test.ts"), "");
  writeFileSync(join(testDir, "helper.ts"), "");
  mkdirSync(join(testDir, "nested.test.ts"));

  assert.deepEqual(listDefaultControlPlaneTestFiles(repoRoot), [
    "tests/control_plane_ts/alpha.test.ts",
    "tests/control_plane_ts/zeta.test.ts",
  ]);

  const flagOnly = buildNodeTestArgs(
    ["--test-name-pattern", "matching", "--test-reporter=tap"],
    { env: {}, availableParallelism: 12, repoRoot },
  );
  assert.deepEqual(flagOnly, [
    "--no-warnings",
    "--experimental-sqlite",
    "--experimental-strip-types",
    "--test",
    "--test-concurrency=6",
    "--test-name-pattern",
    "matching",
    "--test-reporter=tap",
    "tests/control_plane_ts/alpha.test.ts",
    "tests/control_plane_ts/zeta.test.ts",
  ]);

  const explicitTarget = buildNodeTestArgs(
    ["--test-concurrency=2", "tests/control_plane_ts/custom.test.ts"],
    { env: {}, availableParallelism: 12, repoRoot },
  );
  assert.deepEqual(explicitTarget, [
    "--no-warnings",
    "--experimental-sqlite",
    "--experimental-strip-types",
    "--test",
    "--test-concurrency=2",
    "tests/control_plane_ts/custom.test.ts",
  ]);

  assert.deepEqual(
    analyzeCliArgs(["--test-name-pattern", "plain-word"]),
    { hasExplicitTestTargets: false, hasExplicitConcurrency: false },
  );
  assert.deepEqual(
    analyzeCliArgs(["--", "tests/control_plane_ts/alpha.test.ts"]),
    { hasExplicitTestTargets: true, hasExplicitConcurrency: false },
  );
});

test("runner creates and cleans 0o700 NODE_COMPILE_CACHE, respects opt-outs, and propagates args and failures", async (t) => {
  const root = makeTempWorkspace(t);
  const probeTmp = join(root, "tmp");
  const callerCache = join(root, "caller-cache");
  const coverageDir = join(root, "v8-cov");
  mkdirSync(probeTmp, { mode: 0o700 });
  mkdirSync(callerCache, { mode: 0o700 });
  mkdirSync(coverageDir, { mode: 0o700 });

  const probeTest = join(root, "probe.test.ts");
  writeFileSync(
    probeTest,
    `import assert from "node:assert/strict";
import { lstatSync } from "node:fs";
import test from "node:test";

test("selected pass case", () => {
  const cacheDir = process.env.NODE_COMPILE_CACHE ?? null;
  const cacheMode = cacheDir ? (lstatSync(cacheDir).mode & 0o777) : null;
  console.log("PROBE_ENV=" + JSON.stringify({ cacheDir, cacheMode, cov: process.env.NODE_V8_COVERAGE || null }));
});

test("optional fail case", () => {
  assert.equal(process.env.LOOPX_PROBE_SHOULD_FAIL, undefined, "intentional probe failure");
});
`,
  );

  // 1. Default auto-created compile cache (0o700) + forwarded --test-name-pattern + cleanup.
  const autoPass = await runRunnerSubprocess(
    ["--test-name-pattern", "selected pass case", probeTest],
    cleanRunnerEnv({ TMPDIR: probeTmp, TMP: probeTmp, TEMP: probeTmp }),
  );
  assert.equal(autoPass.code, 0, autoPass.stderr);
  const autoMatch = autoPass.stdout.match(/PROBE_ENV=(\{.*\})/);
  assert.ok(autoMatch, `missing PROBE_ENV in:\n${autoPass.stdout}`);
  const autoPayload = JSON.parse(autoMatch[1]!) as {
    cacheDir: string | null;
    cacheMode: number | null;
    cov: string | null;
  };
  assert.ok(autoPayload.cacheDir?.startsWith(probeTmp));
  if (typeof process.getuid === "function") {
    assert.equal(autoPayload.cacheMode, 0o700);
  }
  assert.equal(existsSync(autoPayload.cacheDir!), false);
  assert.deepEqual(readdirSync(probeTmp), []);

  // 2. Failing test propagates non-zero exit code and still cleans up compile cache.
  const autoFail = await runRunnerSubprocess(
    ["--test-name-pattern", "optional fail case", probeTest],
    cleanRunnerEnv({
      TMPDIR: probeTmp,
      TMP: probeTmp,
      TEMP: probeTmp,
      LOOPX_PROBE_SHOULD_FAIL: "1",
    }),
  );
  assert.notEqual(autoFail.code, 0);
  assert.deepEqual(readdirSync(probeTmp), []);

  // 3. Explicit NODE_COMPILE_CACHE is preserved intact without creating a scoped dir.
  const explicit = await runRunnerSubprocess(
    ["--test-name-pattern", "selected pass case", probeTest],
    cleanRunnerEnv({
      TMPDIR: probeTmp,
      TMP: probeTmp,
      TEMP: probeTmp,
      NODE_COMPILE_CACHE: callerCache,
    }),
  );
  assert.equal(explicit.code, 0, explicit.stderr);
  const explicitPayload = JSON.parse(
    explicit.stdout.match(/PROBE_ENV=(\{.*\})/)![1]!,
  ) as { cacheDir: string | null };
  assert.equal(explicitPayload.cacheDir, callerCache);
  assert.equal(existsSync(callerCache), true);
  assert.deepEqual(readdirSync(probeTmp), []);

  // 4. NODE_DISABLE_COMPILE_CACHE=1 and NODE_V8_COVERAGE suppress temp cache creation.
  for (const extra of [
    { NODE_DISABLE_COMPILE_CACHE: "1" },
    { NODE_V8_COVERAGE: coverageDir },
  ]) {
    const suppressed = await runRunnerSubprocess(
      ["--test-name-pattern", "selected pass case", probeTest],
      cleanRunnerEnv({ TMPDIR: probeTmp, TMP: probeTmp, TEMP: probeTmp, ...extra }),
    );
    assert.equal(suppressed.code, 0, suppressed.stderr);
    const suppressedPayload = JSON.parse(
      suppressed.stdout.match(/PROBE_ENV=(\{.*\})/)![1]!,
    ) as { cacheDir: string | null; cov: string | null };
    assert.equal(suppressedPayload.cacheDir, null);
    if ("NODE_V8_COVERAGE" in extra) {
      assert.equal(suppressedPayload.cov, coverageDir);
    }
    assert.deepEqual(readdirSync(probeTmp), []);
  }
});

test(
  "runner cleans NODE_COMPILE_CACHE and propagates signal on SIGTERM",
  { skip: process.platform === "win32" },
  async (t) => {
    const root = makeTempWorkspace(t);
    const probeTmp = join(root, "tmp");
    const readyFile = join(root, "cache-ready.txt");
    const readyTmp = join(root, "cache-ready.tmp");
    mkdirSync(probeTmp, { mode: 0o700 });
    const waitTest = join(root, "wait.test.ts");
    writeFileSync(
      waitTest,
      `import { renameSync, writeFileSync } from "node:fs";
import test from "node:test";
test("blocks until terminated", async () => {
  writeFileSync(${JSON.stringify(readyTmp)}, process.env.NODE_COMPILE_CACHE ?? "", "utf8");
  renameSync(${JSON.stringify(readyTmp)}, ${JSON.stringify(readyFile)});
  await new Promise((resolve) => setTimeout(resolve, 30_000));
});
`,
    );

    const child = spawn(process.execPath, [RUNNER_SCRIPT, waitTest], {
      env: cleanRunnerEnv({
        TMPDIR: probeTmp,
        TMP: probeTmp,
        TEMP: probeTmp,
      }) as NodeJS.ProcessEnv,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });

    let stderr = "";
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });

    const deadline = Date.now() + 15_000;
    while (!existsSync(readyFile)) {
      if (Date.now() > deadline) {
        child.kill("SIGKILL");
        assert.fail(`timed out waiting for readyFile (${stderr})`);
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    const cacheDir = readFileSync(readyFile, "utf8").trim();
    assert.ok(cacheDir.length > 0, "expected NODE_COMPILE_CACHE in worker env");
    assert.equal(existsSync(cacheDir), true);
    child.kill("SIGTERM");

    const closed = await new Promise<{
      code: number | null;
      signal: NodeJS.Signals | null;
    }>((resolve) => {
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    assert.equal(closed.signal, "SIGTERM");
    assert.equal(existsSync(cacheDir), false);
    assert.deepEqual(readdirSync(probeTmp), []);
  },
);

test("buildControlPlaneRunPlan partitions heavy conformance tests and falls back to single lane", (t) => {
  const repoRoot = makeTempWorkspace(t);
  const testDir = join(repoRoot, "tests", "control_plane_ts");
  mkdirSync(testDir, { recursive: true });
  for (const name of [
    "alpha.test.ts",
    "authority_store.test.ts",
    "nokv_authority_store.test.ts",
    "nokv_jsonl_transport.test.ts",
    "sqlite_authority_store.test.ts",
    "zeta.test.ts",
  ]) {
    writeFileSync(join(testDir, name), "");
  }

  const twoLane = buildControlPlaneRunPlan([], {
    env: {},
    availableParallelism: 18,
    repoRoot,
  });
  assert.deepEqual(twoLane, {
    mode: "two_lane",
    totalConcurrency: 8,
    heavyConcurrency: 2,
    standardConcurrency: 6,
    heavyFiles: [...HEAVY_CONTROL_PLANE_TEST_FILES],
    standardFiles: [
      "tests/control_plane_ts/alpha.test.ts",
      "tests/control_plane_ts/nokv_authority_store.test.ts",
      "tests/control_plane_ts/nokv_jsonl_transport.test.ts",
      "tests/control_plane_ts/zeta.test.ts",
    ],
  });
  assert.deepEqual(HEAVY_CONTROL_PLANE_TEST_FILES, [
    "tests/control_plane_ts/sqlite_authority_store.test.ts",
    "tests/control_plane_ts/authority_store.test.ts",
  ]);

  // Fallback on concurrency < 6, NODE_V8_COVERAGE, explicit flags, or explicit targets.
  for (const [argv, env, cpus] of [
    [[], {}, 8],
    [[], { LOOPX_TS_TEST_CONCURRENCY: "5" }, 18],
    [[], { NODE_V8_COVERAGE: "/tmp/cov" }, 18],
    [["--test-reporter=tap"], {}, 18],
    [["tests/control_plane_ts/alpha.test.ts"], {}, 18],
  ] as const) {
    const fallback = buildControlPlaneRunPlan([...argv], {
      env: { ...env },
      availableParallelism: cpus,
      repoRoot,
    });
    assert.equal(fallback.mode, "single");
  }
});

test("two-lane runner executes both lanes with shared compile cache and propagates heavy failures", async (t) => {
  const root = makeTempWorkspace(t);
  const repoRoot = join(root, "repo");
  const probeTmp = join(root, "tmp");
  const testDir = join(repoRoot, "tests", "control_plane_ts");
  mkdirSync(testDir, { recursive: true });
  mkdirSync(probeTmp, { mode: 0o700 });

  const envLog = join(root, "lanes.jsonl");
  const passTemplate = (label: string) => `import { appendFileSync } from "node:fs";
import test from "node:test";
test("${label}", () => {
  appendFileSync(${JSON.stringify(envLog)}, JSON.stringify({
    label: ${JSON.stringify(label)},
    cache: process.env.NODE_COMPILE_CACHE ?? null,
  }) + "\\n");
});
`;
  writeFileSync(join(testDir, "sqlite_authority_store.test.ts"), passTemplate("heavy-sqlite"));
  writeFileSync(join(testDir, "authority_store.test.ts"), passTemplate("heavy-file"));
  writeFileSync(join(testDir, "alpha.test.ts"), passTemplate("std-alpha"));
  writeFileSync(join(testDir, "beta.test.ts"), passTemplate("std-beta"));

  const ok = await runControlPlaneTests([], {
    env: cleanRunnerEnv({ TMPDIR: probeTmp, TMP: probeTmp, TEMP: probeTmp }),
    availableParallelism: 18,
    repoRoot,
    tmpRoot: probeTmp,
  });
  assert.equal(ok.status, 0);
  assert.equal(ok.signal, null);
  assert.notEqual(ok.cacheDir, null);
  assert.equal(existsSync(ok.cacheDir!), false);
  assert.deepEqual(readdirSync(probeTmp), []);

  const records = readFileSync(envLog, "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as { label: string; cache: string | null });
  assert.deepEqual(
    records.map((entry) => entry.label).sort(),
    ["heavy-file", "heavy-sqlite", "std-alpha", "std-beta"],
  );
  assert.ok(records.every((entry) => entry.cache === ok.cacheDir));

  // Failing heavy-lane file must fail the overall run even when standard lane passes.
  writeFileSync(
    join(testDir, "authority_store.test.ts"),
    `import assert from "node:assert/strict";
import test from "node:test";
test("heavy fail", () => { assert.equal(1, 2); });
`,
  );
  const failed = await runControlPlaneTests([], {
    env: cleanRunnerEnv({ TMPDIR: probeTmp, TMP: probeTmp, TEMP: probeTmp }),
    availableParallelism: 18,
    repoRoot,
    tmpRoot: probeTmp,
  });
  assert.notEqual(failed.status, 0);
  assert.deepEqual(readdirSync(probeTmp), []);
});

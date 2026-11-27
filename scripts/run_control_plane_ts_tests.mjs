#!/usr/bin/env node
/**
 * Adaptive runner for TypeScript control-plane tests (`npm run test:control-plane`).
 *
 * Uses APFS-safe adaptive `--test-concurrency` (`LOOPX_TS_TEST_CONCURRENCY` or
 * clamped `Math.floor(availableParallelism / 2)` within `[4, 8]`), 2-lane
 * heavy-first scheduling for the default full suite when concurrency >= 6, and a
 * private run-scoped `NODE_COMPILE_CACHE` directory unless caller cache,
 * disable, or coverage environment settings apply.
 */

import { spawn } from "node:child_process";
import {
  chmodSync,
  mkdtempSync,
  readdirSync,
  realpathSync,
  rmSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
export const DEFAULT_TEST_DIR = "tests/control_plane_ts";
export const DEFAULT_CONCURRENCY_MIN = 4;
export const DEFAULT_CONCURRENCY_MAX = 8;
export const HEAVY_LANE_CONCURRENCY = 2;
export const HEAVY_CONTROL_PLANE_TEST_FILES = [
  "tests/control_plane_ts/sqlite_authority_store.test.ts",
  "tests/control_plane_ts/authority_store.test.ts",
];

const VALUE_FLAGS = new Set([
  "-C",
  "-r",
  "--conditions",
  "--cpu-prof-dir",
  "--diagnostic-dir",
  "--env-file",
  "--env-file-if-exists",
  "--heap-prof-dir",
  "--import",
  "--loader",
  "--require",
  "--test-concurrency",
  "--test-name-pattern",
  "--test-reporter",
  "--test-reporter-destination",
  "--test-shard",
  "--test-skip-pattern",
  "--test-timeout",
]);

const BASE_NODE_TEST_ARGS = [
  "--no-warnings",
  "--experimental-sqlite",
  "--experimental-strip-types",
  "--test",
];

/**
 * Resolve worker concurrency from `LOOPX_TS_TEST_CONCURRENCY` or adaptive CPU
 * scaling clamped to `[4, 8]`.
 */
export function resolveTestConcurrency(
  env = process.env,
  availableParallelism = os.availableParallelism(),
) {
  const raw = env?.LOOPX_TS_TEST_CONCURRENCY;
  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (/^\d+$/.test(trimmed)) {
      const parsed = Number.parseInt(trimmed, 10);
      if (Number.isSafeInteger(parsed) && parsed > 0) {
        return parsed;
      }
    }
  }
  const detected =
    Number.isFinite(availableParallelism) && availableParallelism > 0
      ? Math.floor(availableParallelism)
      : DEFAULT_CONCURRENCY_MIN;
  return Math.min(
    DEFAULT_CONCURRENCY_MAX,
    Math.max(DEFAULT_CONCURRENCY_MIN, Math.floor(detected / 2)),
  );
}

/**
 * Enable a private run-scoped compile cache only when no caller cache,
 * explicit disable flag, or V8 coverage collection is active.
 */
export function shouldEnableCompileCache(env = process.env) {
  if (!env) return true;
  if (env.NODE_COMPILE_CACHE) return false;
  if (env.NODE_DISABLE_COMPILE_CACHE === "1") return false;
  if (env.NODE_V8_COVERAGE) return false;
  return true;
}

/**
 * Discover and sort `tests/control_plane_ts/*.test.ts` deterministically.
 */
export function listDefaultControlPlaneTestFiles(repoRoot = REPO_ROOT) {
  const testDir = path.join(repoRoot, DEFAULT_TEST_DIR);
  const entries = readdirSync(testDir, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".test.ts"))
    .map((entry) => `${DEFAULT_TEST_DIR}/${entry.name}`)
    .sort();
}

/**
 * Inspect forwarded CLI arguments to distinguish Node test flags from explicit
 * positional test targets without discarding multi-token flags.
 */
export function analyzeCliArgs(argv = []) {
  let hasExplicitTestTargets = false;
  let hasExplicitConcurrency = false;
  let expectingFlagValue = false;
  let afterDoubleDash = false;

  for (const token of argv) {
    if (afterDoubleDash) {
      hasExplicitTestTargets = true;
      continue;
    }
    if (expectingFlagValue) {
      expectingFlagValue = false;
      continue;
    }
    if (token === "--") {
      afterDoubleDash = true;
      continue;
    }
    if (
      token === "--test-concurrency" ||
      token.startsWith("--test-concurrency=")
    ) {
      hasExplicitConcurrency = true;
    }
    if (token.startsWith("-")) {
      if (!token.includes("=") && VALUE_FLAGS.has(token)) {
        expectingFlagValue = true;
      }
      continue;
    }
    hasExplicitTestTargets = true;
  }

  return { hasExplicitTestTargets, hasExplicitConcurrency };
}

/**
 * Build the complete `node` argv for executing control-plane TS tests.
 */
export function buildNodeTestArgs(
  argv = [],
  {
    env = process.env,
    availableParallelism = os.availableParallelism(),
    repoRoot = REPO_ROOT,
  } = {},
) {
  const { hasExplicitTestTargets, hasExplicitConcurrency } = analyzeCliArgs(argv);
  const args = [...BASE_NODE_TEST_ARGS];
  if (!hasExplicitConcurrency) {
    const concurrency = resolveTestConcurrency(env, availableParallelism);
    args.push(`--test-concurrency=${concurrency}`);
  }
  args.push(...argv);
  if (!hasExplicitTestTargets) {
    args.push(...listDefaultControlPlaneTestFiles(repoRoot));
  }
  return args;
}

/**
 * Plan single-process fallback or 2-lane heavy-first execution for the control-plane suite.
 */
export function buildControlPlaneRunPlan(
  argv = [],
  {
    env = process.env,
    availableParallelism = os.availableParallelism(),
    repoRoot = REPO_ROOT,
  } = {},
) {
  const totalConcurrency = resolveTestConcurrency(env, availableParallelism);
  if (argv.length > 0 || totalConcurrency < 6 || Boolean(env?.NODE_V8_COVERAGE)) {
    return {
      mode: "single",
      totalConcurrency,
      args: buildNodeTestArgs(argv, { env, availableParallelism, repoRoot }),
    };
  }
  const allFiles = listDefaultControlPlaneTestFiles(repoRoot);
  const allFileSet = new Set(allFiles);
  const heavyFiles = HEAVY_CONTROL_PLANE_TEST_FILES.filter((file) =>
    allFileSet.has(file),
  );
  const heavySet = new Set(heavyFiles);
  const standardFiles = allFiles.filter((file) => !heavySet.has(file));
  if (heavyFiles.length === 0 || standardFiles.length === 0) {
    return {
      mode: "single",
      totalConcurrency,
      args: buildNodeTestArgs(argv, { env, availableParallelism, repoRoot }),
    };
  }
  return {
    mode: "two_lane",
    totalConcurrency,
    heavyConcurrency: HEAVY_LANE_CONCURRENCY,
    standardConcurrency: totalConcurrency - HEAVY_LANE_CONCURRENCY,
    heavyFiles,
    standardFiles,
  };
}

/**
 * Create a mode-`0o700` temporary compile-cache directory scoped to one run.
 */
export function createScopedCompileCache({
  env = process.env,
  tmpRoot = os.tmpdir(),
} = {}) {
  if (!shouldEnableCompileCache(env)) {
    return { cacheDir: null, cleanup: () => {} };
  }
  const cacheDir = mkdtempSync(path.join(tmpRoot, "loopx-ts-compile-cache-"));
  chmodSync(cacheDir, 0o700);
  let cleaned = false;
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    try {
      rmSync(cacheDir, { recursive: true, force: true });
    } catch {
      // Best-effort cleanup on exit/signal; never mask child test failure.
    }
  };
  return { cacheDir, cleanup };
}

function terminateWithSignal(signal) {
  try {
    process.kill(process.pid, signal);
  } catch {
    process.exitCode = 1;
  }
}

function attachProcessSignalHandlers(onCleanup, onSignal) {
  const signals =
    process.platform === "win32"
      ? ["SIGINT", "SIGTERM"]
      : ["SIGINT", "SIGTERM", "SIGHUP"];
  const signalListeners = new Map();
  process.on("exit", onCleanup);
  for (const signal of signals) {
    const listener = () => onSignal(signal);
    signalListeners.set(signal, listener);
    process.on(signal, listener);
  }
  return () => {
    process.off("exit", onCleanup);
    for (const [signal, listener] of signalListeners.entries()) {
      process.off(signal, listener);
    }
    signalListeners.clear();
  };
}

function spawnManagedProcess({ execPath, args, cwd, env, stdio, activeChildren }) {
  const child = spawn(execPath, args, {
    cwd,
    env,
    stdio,
    windowsHide: true,
  });
  activeChildren.add(child);
  return child;
}

function runInheritedLane({ execPath, args, cwd, env, activeChildren }) {
  return new Promise((resolve) => {
    const child = spawnManagedProcess({
      execPath,
      args,
      cwd,
      env,
      stdio: "inherit",
      activeChildren,
    });
    let settled = false;
    child.once("error", (error) => {
      if (settled) return;
      settled = true;
      activeChildren.delete(child);
      console.error(`Failed to launch Node test runner: ${error.message}`);
      resolve({ status: 1, signal: null });
    });
    child.once("close", (status, signal) => {
      if (settled) return;
      settled = true;
      activeChildren.delete(child);
      resolve({ status, signal });
    });
  });
}

function runBufferedHeavyFile({ execPath, file, cwd, env, activeChildren }) {
  return new Promise((resolve) => {
    const args = [...BASE_NODE_TEST_ARGS, "--test-concurrency=1", file];
    const child = spawnManagedProcess({
      execPath,
      args,
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      activeChildren,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr?.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", (error) => {
      if (settled) return;
      settled = true;
      activeChildren.delete(child);
      resolve({
        file,
        status: 1,
        signal: null,
        stdout,
        stderr: `${stderr}Failed to launch ${file}: ${error.message}\n`,
      });
    });
    child.once("close", (status, signal) => {
      if (settled) return;
      settled = true;
      activeChildren.delete(child);
      resolve({ file, status, signal, stdout, stderr });
    });
  });
}

function flushHeavyResult(result) {
  const outcome =
    result.signal !== null
      ? `signal=${result.signal}`
      : `exit=${result.status ?? 1}`;
  process.stdout.write(`\n[heavy-lane] ${result.file} (${outcome})\n`);
  if (result.stdout) {
    process.stdout.write(result.stdout);
    if (!result.stdout.endsWith("\n")) process.stdout.write("\n");
  }
  if (result.stderr) {
    process.stderr.write(result.stderr);
    if (!result.stderr.endsWith("\n")) process.stderr.write("\n");
  }
}

export async function runControlPlaneTests(
  argv = process.argv.slice(2),
  {
    env = process.env,
    availableParallelism = os.availableParallelism(),
    repoRoot = REPO_ROOT,
    tmpRoot = os.tmpdir(),
    execPath = process.execPath,
  } = {},
) {
  const { cacheDir, cleanup } = createScopedCompileCache({ env, tmpRoot });
  const childEnv = { ...env };
  if (cacheDir !== null) {
    childEnv.NODE_COMPILE_CACHE = cacheDir;
  }

  let plan;
  try {
    plan = buildControlPlaneRunPlan(argv, {
      env,
      availableParallelism,
      repoRoot,
    });
  } catch (error) {
    cleanup();
    throw error;
  }

  const activeChildren = new Set();
  let aborted = false;
  let forwardedSignal = null;

  const detachListeners = attachProcessSignalHandlers(cleanup, (signal) => {
    aborted = true;
    forwardedSignal ??= signal;
    for (const child of activeChildren) {
      if (!child.killed) {
        try {
          child.kill(signal);
        } catch {
          // Child may have already exited while signal arrived.
        }
      }
    }
  });

  try {
    if (plan.mode === "single") {
      const outcome = await runInheritedLane({
        execPath,
        args: plan.args,
        cwd: repoRoot,
        env: childEnv,
        activeChildren,
      });
      return {
        status: outcome.status,
        signal: outcome.signal ?? forwardedSignal,
        cacheDir,
      };
    }

    const standardArgs = [
      ...BASE_NODE_TEST_ARGS,
      `--test-concurrency=${plan.standardConcurrency}`,
      ...plan.standardFiles,
    ];
    let standardDone = false;
    const pendingHeavyOutputs = [];

    const standardPromise = runInheritedLane({
      execPath,
      args: standardArgs,
      cwd: repoRoot,
      env: childEnv,
      activeChildren,
    }).then((result) => {
      standardDone = true;
      while (pendingHeavyOutputs.length > 0) {
        flushHeavyResult(pendingHeavyOutputs.shift());
      }
      return result;
    });

    const queue = [...plan.heavyFiles];
    const heavyResults = [];
    const runWorker = async () => {
      while (!aborted && queue.length > 0) {
        const file = queue.shift();
        const result = await runBufferedHeavyFile({
          execPath,
          file,
          cwd: repoRoot,
          env: childEnv,
          activeChildren,
        });
        heavyResults.push(result);
        if (standardDone) {
          flushHeavyResult(result);
        } else {
          pendingHeavyOutputs.push(result);
        }
      }
    };
    const workerCount = Math.min(plan.heavyConcurrency, queue.length);
    const heavyPromise = Promise.all(
      Array.from({ length: workerCount }, () => runWorker()),
    );

    const [standardOutcome] = await Promise.all([standardPromise, heavyPromise]);
    const heavySignal =
      heavyResults.find((item) => item.signal !== null)?.signal ?? null;
    const failedHeavy = heavyResults.find(
      (item) => item.status !== 0 || item.signal !== null,
    );
    const missingHeavy = heavyResults.length !== plan.heavyFiles.length;
    const combinedSignal =
      standardOutcome.signal ?? heavySignal ?? forwardedSignal;
    let combinedStatus = 0;
    if (standardOutcome.status !== 0) {
      combinedStatus = standardOutcome.status ?? 1;
    } else if (failedHeavy) {
      combinedStatus = failedHeavy.status ?? 1;
    } else if (missingHeavy) {
      combinedStatus = 1;
    }

    const heavyPassed = heavyResults.filter(
      (item) => item.status === 0 && item.signal === null,
    ).length;
    process.stdout.write(
      `[run_control_plane_ts_tests] 2-lane summary: heavy=${heavyPassed}/${plan.heavyFiles.length} (concurrency=${plan.heavyConcurrency}), standard_files=${plan.standardFiles.length} (concurrency=${plan.standardConcurrency}, exit=${standardOutcome.status ?? standardOutcome.signal})\n`,
    );

    return {
      status: combinedSignal !== null ? null : combinedStatus,
      signal: combinedSignal,
      cacheDir,
    };
  } finally {
    detachListeners();
    cleanup();
  }
}

function isMainModule() {
  if (!process.argv[1]) return false;
  try {
    return (
      realpathSync(fileURLToPath(import.meta.url)) ===
      realpathSync(process.argv[1])
    );
  } catch {
    return false;
  }
}

if (isMainModule()) {
  const result = await runControlPlaneTests();
  if (result.signal !== null) {
    terminateWithSignal(result.signal);
  } else {
    process.exit(result.status ?? 1);
  }
}

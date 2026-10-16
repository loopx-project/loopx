import { lstatSync, mkdirSync, type Stats } from "node:fs";
import { readFile, rm } from "node:fs/promises";
import { enableCompileCache, flushCompileCache, getCompileCacheDir } from "node:module";
import { dirname, isAbsolute, join } from "node:path";
import { isMainThread, parentPort, Worker, workerData } from "node:worker_threads";

// Local synchronization states, not a persisted or caller-facing protocol.
const FLUSHING = 0;
const FLUSHED = 1;
const RETIRING = 2;
const CACHE_FLUSH_BUDGET_MS = 1_000;

interface RuntimeLocator {
  infoPath: string;
  token: string;
  pid: number;
  fingerprint: string;
}

interface RetirementWatchdog {
  kind: "compile_cache_retirement";
  locator: RuntimeLocator;
  state: SharedArrayBuffer;
}

async function locatorRetirement(locator: RuntimeLocator): Promise<() => Promise<void>> {
  // Load before flushing so normal process exit cannot write newly compiled
  // helpers back into a namespace its caller has already removed.
  const { withFileMutationLock } = await import("./effect_runtime_io.ts");
  return () => withFileMutationLock(locator.infoPath, async () => {
    let published: Record<string, unknown>;
    try {
      published = JSON.parse(await readFile(locator.infoPath, "utf8"));
    } catch {
      return;
    }
    // Both retirement paths share the original fence and exact identity check.
    // An old runtime must never erase a replacement server's locator.
    if (published.token === locator.token && published.pid === locator.pid &&
        published.fingerprint === locator.fingerprint) {
      await rm(locator.infoPath, { force: true });
    }
  });
}

async function exitAfterRetirement(retire: () => Promise<void>): Promise<void> {
  try {
    await retire();
  } finally {
    // Node runs the calling environment's native exit hooks even for self-kill.
    // Only our cache-disabled worker bypasses the main environment's cache.
    // The main-thread fallback remains best effort if no supervisor can start.
    // A worker shares this PID and cannot target a later process reusing it.
    process.kill(process.pid, "SIGKILL");
  }
}

async function superviseCache(data: RetirementWatchdog): Promise<void> {
  const retire = await locatorRetirement(data.locator);
  const state = new Int32Array(data.state);
  parentPort!.once("message", () => {
    setTimeout(() => {
      if (Atomics.compareExchange(state, 0, FLUSHING, RETIRING) === FLUSHING) {
        void exitAfterRetirement(retire);
      }
    }, CACHE_FLUSH_BUDGET_MS);
  });
  parentPort!.postMessage(null);
}

// Called only after the listener closes and every admitted effect settles.
export async function retireRuntime(locator: RuntimeLocator): Promise<void> {
  const retire = await locatorRetirement(locator);
  if (getCompileCacheDir() === undefined) {
    await retire();
    return;
  }
  const state = new Int32Array(new SharedArrayBuffer(4));
  let watchdog: Worker | undefined;
  try {
    watchdog = new Worker(new URL(import.meta.url), {
      workerData: { kind: "compile_cache_retirement", locator, state: state.buffer } satisfies RetirementWatchdog,
      execArgv: ["--no-warnings", "--experimental-strip-types"],
      env: { ...process.env, NODE_DISABLE_COMPILE_CACHE: "1" },
    });
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("cache supervisor unavailable")), CACHE_FLUSH_BUDGET_MS);
      watchdog!.once("message", () => { clearTimeout(timer); resolve(); });
      watchdog!.once("error", error => { clearTimeout(timer); reject(error); });
      watchdog!.once("exit", () => { clearTimeout(timer); reject(new Error("cache supervisor exited")); });
    });
  } catch {
    // There is no cache-free environment to terminate from in this fallback.
    // Still retire the locator; native exit-time cache flushing is best effort.
    void watchdog?.terminate();
    await exitAfterRetirement(retire);
    return;
  }
  watchdog.postMessage(null);
  let flushed = false;
  try {
    flushCompileCache();
    flushed = true;
  } catch {
    // Keep the ready worker armed. Main-thread self-kill would run the native
    // flush again, outside the optional-cache allowance we just supervised.
  }
  if (flushed && Atomics.compareExchange(state, 0, FLUSHING, FLUSHED) === FLUSHING) {
    await watchdog.terminate();
    await retire();
    return;
  }
  // The worker owns failed or overdue persistence and fenced cleanup. Never
  // terminate it while it holds the locator lock, including a late flush return.
  await new Promise<void>(resolve => watchdog.once("exit", () => resolve()));
  await exitAfterRetirement(retire);
}

function privateDirectory(stat: Stats): boolean {
  return stat.isDirectory() && !stat.isSymbolicLink() &&
    (typeof process.getuid !== "function" ||
      (stat.uid === process.getuid() && (stat.mode & 0o077) === 0));
}

// Preload before the server's static imports. This caches compilation, never
// requests, source fingerprints, authority reads, or decisions. Node validates
// source contents and keeps caches from different Node versions separate.
function enableRuntimeCompileCache(): void {
  if (process.env.NODE_DISABLE_COMPILE_CACHE === "1" ||
      process.env.NODE_V8_COVERAGE || getCompileCacheDir() !== undefined) return;
  const index = process.argv.indexOf("--info");
  const info = index < 0 ? undefined : process.argv[index + 1];
  if (!info || !isAbsolute(info)) return;
  try {
    // The launcher owns this private namespace. Never repair permissions on an
    // existing cache, follow a cache symlink, or create a second runtime root.
    const root = dirname(info);
    if (!privateDirectory(lstatSync(root))) return;
    const cache = join(root, "compile-cache");
    mkdirSync(cache, { recursive: true, mode: 0o700 });
    if (!privateDirectory(lstatSync(cache))) return;
    enableCompileCache(cache);
  } catch {
    // An unavailable cache is only a missed optimization. The original server
    // still loads, and its configuration/authentication failures remain fatal.
  }
}

if (!isMainThread && workerData?.kind === "compile_cache_retirement") {
  void superviseCache(workerData as RetirementWatchdog);
} else {
  enableRuntimeCompileCache();
}

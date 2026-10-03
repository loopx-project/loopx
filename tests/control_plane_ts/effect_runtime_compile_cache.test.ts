import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, lstatSync, mkdtempSync, mkdirSync, readdirSync, readFileSync,
  rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";
import { pathToFileURL } from "node:url";

const preloadSource = new URL(
  "../../loopx/control_plane/effect_runtime_compile_cache.ts", import.meta.url,
);

function fixture(t: TestContext) {
  const root = mkdtempSync(join(tmpdir(), "loopx-compile-cache-"));
  chmodSync(root, 0o700);
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const preload = join(root, "preload # % 中文.ts");
  writeFileSync(preload, readFileSync(preloadSource));
  const script = join(root, "caller.ts"), source = join(root, "value.ts");
  writeFileSync(source, "export const value: number = 1;\n");
  writeFileSync(script, `import { value } from './value.ts';
    import { getCompileCacheDir } from 'node:module';
    console.log(JSON.stringify({ value, enabled: getCompileCacheDir() !== undefined }));\n`);
  const cache = join(root, "compile-cache");
  const environment = { ...process.env };
  delete environment.NODE_COMPILE_CACHE;
  delete environment.NODE_DISABLE_COMPILE_CACHE;
  delete environment.NODE_COMPILE_CACHE_PORTABLE;
  // Node propagates a parent's coverage directory when this key is absent.
  // Explicitly isolate only these cache-qualification children; the coverage
  // negative case below still supplies its own directory and must stay off.
  environment.NODE_V8_COVERAGE = "";
  function run(overrides: Record<string, string> = {}, info = join(root, "runtime.json")) {
    const result = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types",
      "--import", pathToFileURL(preload).href, script, "--info", info], {
      env: { ...environment, ...overrides }, encoding: "utf8", timeout: 10_000,
    });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr, "");
    return JSON.parse(result.stdout);
  }
  return { root, source, cache, run };
}

function cacheFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap(entry =>
    entry.isDirectory() ? cacheFiles(join(root, entry.name)) : [join(root, entry.name)]);
}

test("preload enables compilation before static TS imports; same-path edits invalidate it", t => {
  const { source, cache, run } = fixture(t);
  assert.deepEqual(run(), { value: 1, enabled: true });
  if (typeof process.getuid === "function") {
    assert.equal(lstatSync(cache).mode & 0o077, 0);
  }
  assert.ok(cacheFiles(cache).length > 0, "normal exit must populate the real Node cache");
  assert.deepEqual(run(), { value: 1, enabled: true });
  writeFileSync(source, "export const value: number = 2;\n");
  assert.deepEqual(run(), { value: 2, enabled: true });
});

test("corrupt code cache falls back to compiling the original source", t => {
  const { cache, run } = fixture(t);
  assert.equal(run().enabled, true);
  const files = cacheFiles(cache);
  assert.ok(files.length > 0);
  for (const file of files) writeFileSync(file, "not a V8 code cache");
  assert.deepEqual(run(), { value: 1, enabled: true });
});

test("disabled and coverage runs neither enable nor create the default cache", t => {
  const { root, run } = fixture(t);
  assert.deepEqual(run({ NODE_DISABLE_COMPILE_CACHE: "1" }), { value: 1, enabled: false });
  assert.deepEqual(run({ NODE_V8_COVERAGE: join(root, "coverage") }), { value: 1, enabled: false });
  assert.ok(!readdirSync(root).includes("compile-cache"));
});

test("explicit pre-enabled Node cache is preserved without an unused LoopX cache", t => {
  const { root, run } = fixture(t);
  const explicit = join(root, "explicit-cache");
  assert.deepEqual(run({ NODE_COMPILE_CACHE: explicit }), { value: 1, enabled: true });
  assert.ok(cacheFiles(explicit).length > 0);
  assert.ok(!readdirSync(root).includes("compile-cache"));
});

test("unavailable and non-private caches leave source execution working", t => {
  const { cache, run } = fixture(t);
  writeFileSync(cache, "existing non-cache file");
  assert.deepEqual(run(), { value: 1, enabled: false });
  assert.equal(readFileSync(cache, "utf8"), "existing non-cache file");
  rmSync(cache);
  mkdirSync(cache, { mode: 0o700 });
  if (typeof process.getuid === "function") {
    chmodSync(cache, 0o755);
    assert.deepEqual(run(), { value: 1, enabled: false });
    assert.equal(lstatSync(cache).mode & 0o777, 0o755);
    chmodSync(cache, 0o700);
    if (process.getuid() !== 0) {
      chmodSync(cache, 0o500);
      assert.deepEqual(run(), { value: 1, enabled: false });
    }
  }
});

test("non-private parents and absent absolute info paths do not create a cache", t => {
  const { root, run } = fixture(t);
  assert.deepEqual(run({}, "relative/runtime.json"), { value: 1, enabled: false });
  assert.deepEqual(run({}, join(root, "missing", "runtime.json")), { value: 1, enabled: false });
  if (typeof process.getuid === "function") {
    chmodSync(root, 0o755);
    assert.deepEqual(run(), { value: 1, enabled: false });
    chmodSync(root, 0o700);
  }
  assert.ok(!readdirSync(root).includes("compile-cache"));
});

test("a cache symlink is not followed or permission-repaired", {
  skip: process.platform === "win32" ? "Windows symlink creation needs host privileges" : false,
}, t => {
  const { root, cache, run } = fixture(t);
  const target = join(root, "untouched");
  mkdirSync(target, { mode: 0o755 });
  symlinkSync(target, cache, "dir");
  assert.deepEqual(run(), { value: 1, enabled: false });
  assert.deepEqual(readdirSync(target), []);
  assert.equal(lstatSync(target).mode & 0o777, 0o755);
});

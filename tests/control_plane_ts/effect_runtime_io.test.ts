import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import { mkdtemp, open, readFile, rm, stat, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";

import {
  acquireFileMutationLock,
  appendJsonLine,
  appendJsonLineSync,
  claimFileMutationLock,
  mutationLockOwner,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";

async function workspace(t: TestContext): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "loopx-effect-runtime-io-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

for (const scenario of [
  {name: "Windows replacement open recovers", platform: "win32", contended: true, persistent: false, writeFault: false, timeout: 500, code: "EPERM", expected: null},
  {name: "Windows initial permission denial fails", platform: "win32", contended: false, persistent: true, writeFault: false, timeout: 500, code: "EPERM", expected: "EPERM"},
  {name: "Windows persistent replacement denial keeps its error", platform: "win32", contended: true, persistent: true, writeFault: false, timeout: 250, code: "EPERM", expected: "EPERM"},
  {name: "non-Windows replacement denial fails", platform: "linux", contended: true, persistent: false, writeFault: false, timeout: 500, code: "EPERM", expected: "EPERM"},
  {name: "unrelated Windows open failure fails", platform: "win32", contended: true, persistent: false, writeFault: false, timeout: 500, code: "EIO", expected: "EIO"},
  {name: "Windows failure after creation is not retried", platform: "win32", contended: true, persistent: false, writeFault: true, timeout: 500, code: "EPERM", expected: "EPERM"},
  {name: "zero-wait live contention does not gain a retry", platform: "win32", contended: true, persistent: false, writeFault: false, timeout: 0, code: "EPERM", expected: "mutation_lock_timeout"},
]) {
  test(scenario.name, async t => {
    const root = await workspace(t), target = join(root, "state");
    // Only the selected open/write failure and platform predicate are injected.
    // All lock bytes, handles, identities, live-owner checks and release use
    // the production filesystem owner in an isolated child.
    const script = `
      import assert from "node:assert/strict";
      import * as fs from "node:fs/promises";
      import {mock} from "node:test";
      const [module, target, scenario] = JSON.parse(process.argv[1]);
      const path = target + ".ts-effect.lock";
      let attempts = 0;
      if (scenario.contended) {
        await fs.writeFile(path, JSON.stringify({pid: process.pid, token: "live-holder"}));
      }
      const {default: ignored, ...namedExports} = fs;
      mock.module("node:fs/promises", {namedExports: {...namedExports,
        open: async (file, flags, ...args) => {
          if (file !== path || flags !== "wx") return fs.open(file, flags, ...args);
          attempts++;
          if (scenario.contended && attempts === 1) return fs.open(file, flags, ...args);
          if (scenario.contended && attempts === 2) await fs.rm(path);
          const fault = Object.assign(new Error("controlled filesystem failure"),
            {code: scenario.code, syscall: scenario.writeFault ? "write" : "open"});
          if (!scenario.writeFault && (scenario.persistent || attempts === 2)) throw fault;
          const handle = await fs.open(file, flags, ...args);
          if (scenario.writeFault) handle.writeFile = async () => { throw fault; };
          return handle;
        },
      }});
      const io = await import(module);
      Object.defineProperty(process, "platform", {value: scenario.platform});
      let code = null;
      const started = Date.now(); // Match the acquisition deadline's clock.
      try {
        const lock = await io.acquireFileMutationLock(target, process.pid, scenario.timeout);
        assert.equal((await io.mutationLockOwner(target)).token, lock.token);
        assert.equal(await io.releaseFileMutationLock(target, lock.token), true);
        await assert.rejects(fs.stat(path), {code: "ENOENT"});
      } catch (error) { code = error.code ?? String(error); }
      if (scenario.writeFault) await assert.rejects(fs.stat(path), {code: "ENOENT"});
      console.log(JSON.stringify({code, attempts, elapsed: Date.now() - started}));
    `;
    const result = spawnSync(process.execPath, [
      "--no-warnings", "--experimental-test-module-mocks", "--experimental-strip-types",
      "--input-type=module", "-e", script,
      JSON.stringify([new URL("../../loopx/control_plane/effect_runtime_io.ts", import.meta.url).href,
        target, scenario]),
    ], {encoding: "utf8", timeout: 10_000});
    assert.equal(result.status, 0, result.stderr);
    const observed = JSON.parse(result.stdout.trim());
    assert.equal(observed.code, scenario.expected);
    if (scenario.expected === null) assert.equal(observed.attempts, 3);
    else if (!scenario.contended || scenario.timeout === 0) assert.equal(observed.attempts, 1);
    else if (scenario.persistent) {
      assert.ok(observed.attempts > 2);
      assert.ok(observed.elapsed >= scenario.timeout);
      assert.ok(observed.elapsed < 2_000, "permission denial must keep a bounded wait");
    } else assert.equal(observed.attempts, 2);
  });
}

test("JSONL append starts a new row after an unterminated tail", async t => {
  const root = await workspace(t);
  const path = join(root, "index.jsonl");
  await writeFile(path, '{"interrupted":', "utf8");

  await appendJsonLine(path, {goal_id: "goal-a"});

  assert.equal(
    await readFile(path, "utf8"),
    '{"interrupted":\n{"goal_id":"goal-a"}\n',
  );
});

test("synchronous JSONL append starts a new row after an unterminated tail", async t => {
  const root = await workspace(t);
  const path = join(root, "index.jsonl");
  await writeFile(path, '{"interrupted":', "utf8");

  appendJsonLineSync(path, {goal_id: "goal-a"});

  assert.equal(
    await readFile(path, "utf8"),
    '{"interrupted":\n{"goal_id":"goal-a"}\n',
  );
});

test("zero-wait acquisition retries a reclaimed dead owner but preserves a live owner", async t => {
  const root = await workspace(t), target = join(root, "state");
  const dead = spawnSync(process.execPath, ["-e", "process.exit(0)"]);
  assert.equal(dead.status, 0);
  await writeFile(`${target}.ts-effect.lock`, JSON.stringify({pid: dead.pid, token: "dead"}));
  const acquired = await acquireFileMutationLock(target, process.pid, 0);
  await assert.rejects(acquireFileMutationLock(target, process.pid, 0), {code: "mutation_lock_timeout"});
  assert.equal((await mutationLockOwner(target))?.token, acquired.token);
  assert.equal(await releaseFileMutationLock(target, acquired.token), true);
});

test("token-safe release cannot remove a replacement lock", async (t) => {
  const root = await workspace(t);
  const target = join(root, "state");
  const first = await acquireFileMutationLock(target);
  assert.equal(await releaseFileMutationLock(target, first.token), true);

  const replacement = await acquireFileMutationLock(target);
  assert.equal(await releaseFileMutationLock(target, first.token), false);
  assert.deepEqual(await mutationLockOwner(target), {
    pid: process.pid,
    token: replacement.token,
  });
  assert.equal(await releaseFileMutationLock(target, replacement.token), true);
});

test("release cleans its claim when the lock owner is replaced", async (t) => {
  const root = await workspace(t);
  const target = join(root, "state");
  const lockPath = `${target}.ts-effect.lock`;
  const first = await acquireFileMutationLock(target);
  const claim = await claimFileMutationLock(target, first.token);
  assert.ok(claim);

  // Simulate a replacement after fence_close has claimed the old token.  The
  // replacement must remain, while the old caller's claim must be retired.
  await writeFile(
    lockPath,
    JSON.stringify({ pid: process.pid, token: "replacement-token" }),
    "utf8",
  );
  assert.equal(
    await releaseFileMutationLock(target, first.token, claim, true),
    false,
  );
  await assert.rejects(stat(claim!.claimPath), { code: "ENOENT" });
  assert.deepEqual(await mutationLockOwner(target), {
    pid: process.pid,
    token: "replacement-token",
  });

  await rm(lockPath, { force: true });
});

test("malformed stale lock owners are reclaimable without path traversal", async (t) => {
  const root = await workspace(t);
  const target = join(root, "state");
  const lockPath = `${target}.ts-effect.lock`;
  await writeFile(
    lockPath,
    JSON.stringify({ pid: process.pid, token: "   " }),
    "utf8",
  );
  const old = new Date(Date.now() - 60_000);
  await utimes(lockPath, old, old);

  const claim = await claimFileMutationLock(target, "../unsafe/token");
  assert.ok(claim);
  assert.match(claim!.claimPath, /\.claim\.[a-f0-9]{64}$/u);
  await rm(claim!.claimPath, { force: true });

  const acquired = await acquireFileMutationLock(target);
  assert.equal((await mutationLockOwner(target))?.token, acquired.token);
  assert.equal(await releaseFileMutationLock(target, acquired.token), true);
});

test("blank mutation lock tokens are not treated as valid owners", async (t) => {
  const root = await workspace(t);
  const target = join(root, "state");
  const lockPath = `${target}.ts-effect.lock`;
  await writeFile(
    lockPath,
    JSON.stringify({ pid: process.pid, token: "" }),
    "utf8",
  );
  assert.equal(await mutationLockOwner(target), null);
});

test("acquire rejects success when its lock inode is replaced before owner publication", async (t) => {
  const root = await workspace(t);
  const target = join(root, "state");
  const lockPath = `${target}.ts-effect.lock`;
  const replacement = { pid: process.pid, token: "replacement-token" };
  const probe = await open(join(root, "probe"), "w");
  const prototype = Object.getPrototypeOf(probe) as {
    writeFile(data: string, encoding: BufferEncoding): Promise<void>;
  };
  await probe.close();
  const writeFileToHandle = prototype.writeFile;
  let replaced = false;

  prototype.writeFile = async function (data, encoding) {
    if (!replaced) {
      replaced = true;
      await rm(lockPath, { force: true });
      await writeFile(lockPath, JSON.stringify(replacement), "utf8");
    }
    await writeFileToHandle.call(this, data, encoding);
  };

  try {
    await assert.rejects(
      acquireFileMutationLock(target, process.pid, 0),
      { code: "mutation_lock_timeout" },
    );
    assert.equal(replaced, true);
    assert.deepEqual(await mutationLockOwner(target), replacement);
  } finally {
    prototype.writeFile = writeFileToHandle;
    await rm(lockPath, { force: true });
  }
});

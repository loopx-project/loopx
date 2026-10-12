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

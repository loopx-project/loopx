import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import {createHash} from "node:crypto";
import {mkdir, mkdtemp, writeFile, chmod, symlink, rm} from "node:fs/promises";
import {join} from "node:path";
import {test} from "node:test";
import {promisify} from "node:util";
import {
  openPrivateResponseSink, readPrivateJsonSnapshot, writePrivateResponse,
  MAX_LOCAL_SNAPSHOT_BYTES,
} from "../../loopx/control_plane/effect_runtime_snapshot.ts";
import {resolveTestPython} from "../../scripts/test-python.mjs";

const python = process.platform === "win32" ? resolveTestPython() : null;
if (python) process.env.LOOPX_EFFECT_RUNTIME_PYTHON_BIN = python;
const execute = promisify(execFile);

async function createFixtureDirectory() {
  const local = join(process.cwd(), ".local");
  await mkdir(local, {recursive: true});
  return mkdtemp(join(local, "loopx-effect-"));
}

async function protectWindowsFixture(directory: string, path: string) {
  if (!python) return;
  const source = [
    "import sys",
    "from pathlib import Path",
    "from loopx.control_plane.runtime.private_permissions import protect_private_path",
    "protect_private_path(Path(sys.argv[1]), directory=True)",
    "protect_private_path(Path(sys.argv[2]))",
  ].join("\n");
  await execute(python, ["-c", source, directory, path], {timeout: 10_000});
}

async function removeFixture(directory: string) {
  await rm(directory, {recursive: true, force: true, maxRetries: 10, retryDelay: 100});
}

test("local snapshot accepts exact private bytes and rejects altered or unsafe references", async () => {
  const directory = await createFixtureDirectory();
  try {
    const path = join(directory, "params.json");
    const bytes = Buffer.from(JSON.stringify({context: "x".repeat(2_200_000)}));
    await writeFile(path, bytes, {mode: 0o600});
    await protectWindowsFixture(directory, path);
    const ref = {path, byte_count: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex")};
    assert.deepEqual(await readPrivateJsonSnapshot(ref), JSON.parse(bytes.toString()));
    await assert.rejects(readPrivateJsonSnapshot({...ref, sha256: "0".repeat(64)}),
      /snapshot unavailable or invalid/);
    await assert.rejects(readPrivateJsonSnapshot({...ref, byte_count: bytes.length - 1}),
      /snapshot unavailable or invalid/);
    await assert.rejects(readPrivateJsonSnapshot({...ref, byte_count: MAX_LOCAL_SNAPSHOT_BYTES + 1}),
      /snapshot unavailable or invalid/);
    // Windows has no POSIX mode denial: the private-mode rejection is only
    // observable where process.getuid exists.
    if (process.getuid) {
      await chmod(path, 0o644);
      await assert.rejects(readPrivateJsonSnapshot(ref), /snapshot unavailable or invalid/);
    }
    // File symlink creation needs Developer Mode or admin on Windows.
    if (process.platform !== "win32") {
      const link = join(directory, "link.json");
      await symlink(path, link);
      await assert.rejects(readPrivateJsonSnapshot({...ref, path: link}),
        /snapshot unavailable or invalid/);
    }
  } finally {
    await removeFixture(directory);
  }
});

test("large response sink is private and digest-bound", async () => {
  const directory = await createFixtureDirectory();
  try {
    const path = join(directory, "response.json");
    await writeFile(path, "", {mode: 0o600});
    await protectWindowsFixture(directory, path);
    const file = await openPrivateResponseSink(path);
    const bytes = Buffer.from(JSON.stringify({result: "a".repeat(2_200_000)}));
    try {
      const ref = await writePrivateResponse(file, bytes);
      assert.equal(ref.byte_count, bytes.length);
      assert.deepEqual(await readPrivateJsonSnapshot({...ref, path}),
        JSON.parse(bytes.toString()));
    } finally {
      await file.close();
    }
  } finally {
    await removeFixture(directory);
  }
});

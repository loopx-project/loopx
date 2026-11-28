import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import {createHash} from "node:crypto";
import {mkdir, mkdtemp, writeFile, chmod, symlink, rm, link, readFile, lstat} from "node:fs/promises";
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

function hardLinkUnsupported(error: unknown): boolean {
  const code = typeof error === "object" && error && "code" in error ? String(error.code) : "";
  return ["EPERM", "ENOTSUP", "EOPNOTSUPP", "EXDEV", "ENOSYS"].includes(code);
}

/** Same-uid hard link of an outside sentinel into a private effect directory. */
async function hardLinkedPrivateLeaf(name: string): Promise<{
  directory: string; outside: string; path: string; sentinel: string; bytes: Buffer;
} | null> {
  const directory = await createFixtureDirectory();
  const outside = await mkdtemp(join(process.cwd(), ".local", "loopx-sentinel-"));
  try {
    const sentinel = join(outside, "sentinel.json");
    const bytes = Buffer.from(JSON.stringify({sentinel: "keep", leaf: name}));
    await writeFile(sentinel, bytes, {mode: 0o600});
    await chmod(sentinel, 0o600);
    await chmod(directory, 0o700);
    const path = join(directory, name);
    try {
      await link(sentinel, path);
    } catch (error) {
      if (!hardLinkUnsupported(error)) throw error;
      await removeFixture(directory);
      await removeFixture(outside);
      return null;
    }
    await protectWindowsFixture(directory, path);
    const entry = await lstat(path);
    const external = await lstat(sentinel);
    assert.equal(entry.isSymbolicLink(), false);
    assert.equal(entry.isFile(), true);
    assert.equal(entry.dev, external.dev);
    assert.equal(entry.ino, external.ino);
    assert.equal(entry.nlink, 2);
    assert.deepEqual(await readFile(sentinel), bytes);
    return {directory, outside, path, sentinel, bytes};
  } catch (error) {
    await removeFixture(directory);
    await removeFixture(outside);
    throw error;
  }
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

test("hard-linked private snapshot is rejected and the external file stays unchanged", async (t) => {
  const fixture = await hardLinkedPrivateLeaf("params.json");
  if (fixture === null) {
    t.skip("this host cannot create a hard link");
    return;
  }
  const {directory, outside, path, sentinel, bytes} = fixture;
  try {
    const ref = {path, byte_count: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex")};
    let value: unknown;
    let error: unknown;
    try {
      value = await readPrivateJsonSnapshot(ref);
    } catch (caught) {
      error = caught;
    }
    assert.deepEqual(await readFile(sentinel), bytes);
    assert.equal(value, undefined);
    assert.match(error instanceof Error ? error.message : "",
      /private snapshot unavailable or invalid/);
  } finally {
    await removeFixture(directory);
    await removeFixture(outside);
  }
});

test("hard-linked response sink is rejected without clobbering the external file", async (t) => {
  const fixture = await hardLinkedPrivateLeaf("response.json");
  if (fixture === null) {
    t.skip("this host cannot create a hard link");
    return;
  }
  const {directory, outside, path, sentinel, bytes} = fixture;
  try {
    const payload = Buffer.from(JSON.stringify({clobber: true}));
    let error: unknown;
    try {
      const file = await openPrivateResponseSink(path);
      try {
        await writePrivateResponse(file, payload);
      } finally {
        await file.close();
      }
    } catch (caught) {
      error = caught;
    }
    assert.deepEqual(await readFile(sentinel), bytes);
    assert.match(error instanceof Error ? error.message : "",
      /private response sink unavailable or invalid/);
  } finally {
    await removeFixture(directory);
    await removeFixture(outside);
  }
});

import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtemp, writeFile, chmod, symlink, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {test} from "node:test";
import {
  openPrivateResponseSink, readPrivateJsonSnapshot, writePrivateResponse,
} from "../../loopx/control_plane/effect_runtime_snapshot.ts";

test("local snapshot accepts exact private bytes and rejects altered or unsafe references", async () => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-effect-"));
  try {
    const path = join(directory, "params.json");
    const bytes = Buffer.from(JSON.stringify({context: "x".repeat(2_200_000)}));
    await writeFile(path, bytes, {mode: 0o600});
    const ref = {path, byte_count: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex")};
    assert.deepEqual(await readPrivateJsonSnapshot(ref), JSON.parse(bytes.toString()));
    await assert.rejects(readPrivateJsonSnapshot({...ref, sha256: "0".repeat(64)}),
      /snapshot unavailable or invalid/);
    await assert.rejects(readPrivateJsonSnapshot({...ref, byte_count: bytes.length - 1}),
      /snapshot unavailable or invalid/);
    await chmod(path, 0o644);
    await assert.rejects(readPrivateJsonSnapshot(ref), /snapshot unavailable or invalid/);
    const link = join(directory, "link.json");
    await symlink(path, link);
    await assert.rejects(readPrivateJsonSnapshot({...ref, path: link}),
      /snapshot unavailable or invalid/);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test("large response sink is private and digest-bound", async () => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-effect-"));
  try {
    const path = join(directory, "response.json");
    await writeFile(path, "", {mode: 0o600});
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
    await rm(directory, {recursive: true, force: true});
  }
});

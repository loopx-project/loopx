import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtemp, writeFile, chmod, symlink, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {test} from "node:test";
import {execFile} from "node:child_process";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {fileURLToPath} from "node:url";

async function windowsFixture(directory: string, path: string): Promise<void> {
  if (process.platform !== "win32") return;
  const python = process.env.LOOPX_EFFECT_RUNTIME_PYTHON_BIN ?? resolveTestPython();
  process.env.LOOPX_EFFECT_RUNTIME_PYTHON_BIN = python;
  const helper = fileURLToPath(new URL("../../loopx/control_plane/runtime/private_permissions.py", import.meta.url));
  await command(python, ["-c", "import runpy,sys;from pathlib import Path;m=runpy.run_path(sys.argv[1]);m['protect_private_path'](Path(sys.argv[2]),directory=True);m['protect_private_path'](Path(sys.argv[3]))", helper, directory, path]);
}

function command(exe: string, args: string[], env = process.env): Promise<void> {
  return new Promise((resolve, reject) => execFile(exe, args,
    {env, windowsHide: true, timeout: 30000}, error => error ? reject(error) : resolve()));
}
import {
  openPrivateResponseSink, readPrivateJsonSnapshot, writePrivateResponse,
  MAX_LOCAL_SNAPSHOT_BYTES,
} from "../../loopx/control_plane/effect_runtime_snapshot.ts";

test("local snapshot accepts exact private bytes and rejects altered or unsafe references", async () => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-effect-"));
  try {
    const path = join(directory, "params.json");
    const bytes = Buffer.from(JSON.stringify({context: "x".repeat(2_200_000)}));
    await writeFile(path, bytes, {mode: 0o600});
    await windowsFixture(directory, path);
    const ref = {path, byte_count: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex")};
    assert.deepEqual(await readPrivateJsonSnapshot(ref), JSON.parse(bytes.toString()));
    await assert.rejects(readPrivateJsonSnapshot({...ref, sha256: "0".repeat(64)}),
      /snapshot unavailable or invalid/);
    await assert.rejects(readPrivateJsonSnapshot({...ref, byte_count: bytes.length - 1}),
      /snapshot unavailable or invalid/);
    await assert.rejects(readPrivateJsonSnapshot({...ref, byte_count: MAX_LOCAL_SNAPSHOT_BYTES + 1}),
      /snapshot unavailable or invalid/);
    if (process.platform === "win32") {
      await command("icacls.exe", [path, "/grant", "*S-1-1-0:F"]);
    } else await chmod(path, 0o644);
    await assert.rejects(readPrivateJsonSnapshot(ref), /snapshot unavailable or invalid/);
    if (process.platform === "win32") {
      const link = join(directory, "loopx-effect-junction");
      await command("pwsh", ["-NoLogo", "-NoProfile", "-Command",
        "New-Item -ItemType Junction -Path $env:LX_LINK -Target $env:LX_TARGET | Out-Null"],
        {...process.env, LX_LINK: link, LX_TARGET: directory});
      // A real Windows junction is the reparse-path analogue; no symlink privilege needed.
      await assert.rejects(readPrivateJsonSnapshot({...ref, path: join(link, "params.json")}),
        /snapshot unavailable or invalid/);
    } else {
      const link = join(directory, "link.json");
      await symlink(path, link);
      await assert.rejects(readPrivateJsonSnapshot({...ref, path: link}),
        /snapshot unavailable or invalid/);
    }
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test("large response sink is private and digest-bound", async () => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-effect-"));
  try {
    const path = join(directory, "response.json");
    await writeFile(path, "", {mode: 0o600});
    await windowsFixture(directory, path);
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

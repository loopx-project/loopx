import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { resolveTestPython } from "../../scripts/test-python.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const shellPath = (path: string) => process.platform === "win32"
  ? path.replace(/\\/g, "/").replace(/^([A-Za-z]):/, (_, drive: string) => `/${drive.toLowerCase()}`)
  : path;
const quote = (value: string) => `'${value.replace(/'/g, `'"'"'`)}'`;

for (const [name, ending, recordedWorks, explicit] of [
  ["LF record", "\n", true, ""],
  ["CRLF record", "\r\n", true, ""],
  ["record without final newline", "", true, ""],
  ["stale CRLF record", "\r\n", false, ""],
  ["explicit runtime precedes record", "\r\n", true, "valid"],
  ["invalid explicit runtime stops discovery", "\r\n", true, "missing"],
] as const) {
  test(`recorded Python: ${name}`, t => {
    const directory = mkdtempSync(join(tmpdir(), "loopx-python-record-"));
    t.after(() => rmSync(directory, { recursive: true, force: true }));
    const scripts = join(directory, "scripts");
    mkdirSync(scripts);
    copyFileSync(join(root, "scripts/loopx-python.sh"), join(scripts, "loopx-python.sh"));
    const selected = join(directory, "recorded runtime with spaces");
    const fallback = join(directory, ".venv/bin/python");
    mkdirSync(join(directory, ".venv/bin"), { recursive: true });
    const python = resolveTestPython();
    const override = join(directory, "explicit runtime");
    for (const [path, label] of [[selected, "recorded"], [fallback, "fallback"], [override, "explicit"]]) {
      writeFileSync(path, `#!/bin/sh\nexport LOOPX_SELECTED=${label}\nexec ${quote(shellPath(python))} "$@"\n`, { mode: 0o755 });
    }
    writeFileSync(join(directory, ".loopx-python"), shellPath(recordedWorks ? selected : selected + "-missing") + ending);
    // Resolve Bash before restricting the child's PATH, including on Git Bash.
    const discovered = spawnSync("bash", ["-c", process.platform === "win32"
      ? 'cygpath -w "$(command -v bash)"' : "command -v bash"], { encoding: "utf8" });
    assert.equal(discovered.status, 0, discovered.stderr);
    const env: NodeJS.ProcessEnv = { ...process.env, HOME: shellPath(directory), PATH: "/usr/bin:/bin" };
    delete env.LOOPX_PYTHON;
    if (explicit) env.LOOPX_PYTHON = shellPath(explicit === "valid" ? override : override + "-missing");
    const result = spawnSync(discovered.stdout.trim(), [join(scripts, "loopx-python.sh"), "--exec", "-c",
      "import json,os,sys; print(json.dumps([os.environ['LOOPX_SELECTED'],sys.executable]))"], {
      cwd: directory, env, encoding: "utf8", timeout: 10_000,
    });
    if (explicit === "missing") {
      assert.notEqual(result.status, 0);
      assert.equal(result.stdout, "");
      assert.match(result.stderr, /LOOPX_PYTHON is set but does not resolve/);
    } else {
      assert.equal(result.status, 0, result.stderr);
      assert.deepEqual(JSON.parse(result.stdout.trim()), [explicit ? "explicit" : recordedWorks ? "recorded" : "fallback", python]);
      if (recordedWorks) assert.equal(result.stderr, "");
      else assert.match(result.stderr, /Ignoring non-functional Python recorded/);
    }
  });
}

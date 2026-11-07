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

for (const [name, firstWorks, firstMinor, secondMinor, expected] of [
  ["broken shadow does not hide a working equal minor", false, 98, 98, "second"],
  ["equal minors retain PATH precedence", true, 98, 98, "first"],
  ["higher minor wins across directories", true, 97, 98, "second"],
  ["broken higher minor falls back to a working lower minor", false, 98, 97, "second"],
] as const) {
  test(`Python PATH discovery: ${name}`, t => {
    const directory = mkdtempSync(join(tmpdir(), "loopx-python-path-"));
    t.after(() => rmSync(directory, { recursive: true, force: true }));
    const scripts = join(directory, "scripts");
    mkdirSync(scripts);
    copyFileSync(join(root, "scripts/loopx-python.sh"), join(scripts, "loopx-python.sh"));
    const python = resolveTestPython();
    const bins = [join(directory, "first bin"), join(directory, "second bin")];
    for (const [index, minor] of [firstMinor, secondMinor].entries()) {
      mkdirSync(bins[index]);
      const label = index === 0 ? "first" : "second";
      const body = index === 0 && !firstWorks ? "exit 1"
        : `export LOOPX_SELECTED=${label}\nexec ${quote(shellPath(python))} "$@"`;
      writeFileSync(join(bins[index], `python3.${minor}`), `#!/bin/sh\n${body}\n`, { mode: 0o755 });
    }
    // An older working alias makes an incorrect recovery observable without
    // depending on a Python executable installed by the host test runner.
    writeFileSync(join(bins[0], "python3"), `#!/bin/sh\nexport LOOPX_SELECTED=older\nexec ${quote(shellPath(python))} "$@"\n`, { mode: 0o755 });
    const discovered = spawnSync("bash", ["-c", process.platform === "win32"
      ? 'cygpath -w "$(command -v bash)"' : "command -v bash"], { encoding: "utf8" });
    assert.equal(discovered.status, 0, discovered.stderr);
    const env: NodeJS.ProcessEnv = {
      ...process.env, HOME: shellPath(directory), PATH: `${bins.map(shellPath).join(":")}:/usr/bin:/bin`,
    };
    delete env.LOOPX_PYTHON;
    const result = spawnSync(discovered.stdout.trim(), [join(scripts, "loopx-python.sh"), "--exec", "-c",
      "import json,os,sys; print(json.dumps([os.environ['LOOPX_SELECTED'],sys.executable]))"], {
      cwd: directory, env, encoding: "utf8", timeout: 10_000,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout.trim()), [expected, python]);
  });
}

import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const contract = "loopx/control_plane/runtime/node_version.ts";
const shellPath = (path: string): string => process.platform === "win32"
  ? path.replaceAll("\\", "/").replace(/^([A-Za-z]):/, (_, drive: string) => `/${drive.toLowerCase()}`)
  : path;
const quote = (text: string): string => `'${text.replaceAll("'", "'\\''")}'`;

// Expected admission comes from the runtime floor and prerelease contract,
// independently of the launcher's implementation or the installed Node version.
const cases: Array<[string, string | null, boolean]> = [
  ["20.19.0", null, false],
  ["22.12.0", null, false],
  ["22.22.2", null, false],
  ["22.22.3-rc.1", null, false],
  ["22.22.3", null, true],
  ["22.22.3+build.1", null, true],
  ["22.22.4-rc.1", null, true],
  ["24.0.0", null, true],
  ["20.19.0", "24.0.0", true],
  ["22.22.2", "22.22.3", true],
];

for (const [onPath, inNvm, allowed] of cases) {
  test(`dashboard admits PATH ${onPath}, NVM ${inNvm ?? "absent"}: ${allowed}`, t => {
    const discovery = spawnSync("bash", ["-c", process.platform === "win32"
      ? 'cygpath -w "$(command -v bash)"' : "command -v bash"], {encoding: "utf8"});
    if (discovery.status !== 0) {
      t.skip("Bash is required for the source dashboard launcher");
      return;
    }
    const directory = mkdtempSync(join(tmpdir(), "loopx-dashboard-node-"));
    t.after(() => rmSync(directory, {recursive: true, force: true}));
    const scripts = join(directory, "scripts");
    const dashboard = join(directory, "apps/presentation/dashboard");
    const bin = join(directory, "bin");
    for (const path of [scripts, dashboard, bin, dirname(join(directory, contract))]) mkdirSync(path, {recursive: true});
    for (const name of ["dashboard-dev.sh", "loopx-python.sh"]) copyFileSync(join(root, "scripts", name), join(scripts, name));
    copyFileSync(join(root, contract), join(directory, contract));
    const log = join(directory, "npm.log");
    const npm = `#!/usr/bin/env bash\nprintf '%s\\n' "$*" >> "$LOOPX_NPM_LOG"\nexit 0\n`;
    const node = (path: string, version: string) => {
      mkdirSync(dirname(path), {recursive: true});
      const preload = `${path}.cjs`;
      writeFileSync(preload, `Object.defineProperty(process.versions, "node", {value: ${JSON.stringify(version)}});\n`);
      writeFileSync(path, `#!/usr/bin/env bash\nexec ${quote(shellPath(process.execPath))} --require ${quote(shellPath(preload))} "$@"\n`, {mode: 0o755});
      writeFileSync(join(dirname(path), "npm"), npm, {mode: 0o755});
    };
    node(join(bin, "node"), onPath);
    const nvm = join(directory, "nvm");
    if (inNvm) node(join(nvm, "versions/node", `v${inNvm}`, "bin/node"), inNvm);
    const env: NodeJS.ProcessEnv = {...process.env, PATH: `${shellPath(bin)}:/usr/bin:/bin`,
      HOME: shellPath(directory), NVM_DIR: shellPath(nvm), LOOPX_NPM_LOG: shellPath(log),
      // Force the existing UI-only route; no backend, agent or model may start.
      LOOPX_PYTHON: shellPath(join(directory, "missing-python"))};
    delete env.NODE_OPTIONS;
    const result = spawnSync(discovery.stdout.trim(), [shellPath(join(scripts, "dashboard-dev.sh"))], {
      env, encoding: "utf8", timeout: 20_000,
    });
    assert.equal(result.error, undefined);
    assert.equal(result.status, allowed ? 0 : 1, result.stdout + result.stderr);
    if (allowed) {
      assert.deepEqual(readFileSync(log, "utf8").trim().split(/\r?\n/), ["ci", "run dev:web"]);
      if (inNvm) assert.match(result.stdout, /Using compatible Node\.js/);
    } else {
      assert.equal(existsSync(log), false, "Reject before npm installation or launch");
      assert.match(result.stderr, /22\.22\.3/);
    }
  });
}

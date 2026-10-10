import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, readFile, realpath, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {repoRoot, outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";
import {resolveTestPython} from "../../scripts/test-python.mjs";

// Only the backup routes use a real disposable server. Other workspace state
// uses the shared browser fixture; no Host, model or live runtime is contacted.
const serverCode = `
import json,pathlib,sys,loopx
from loopx.chat_server import ChatHTTPServer,ChatRequestHandler
r=pathlib.Path(sys.argv[1]); runtime=r/'runtime'; registry=r/'registry.json'
registry.write_text(json.dumps({'common_runtime_root':str(runtime),'goals':[{'id':'fixture','repo':str(r),'control_plane':{'optional':{'context':'complete '*10000}}}]}))
p=runtime/'machine/configuration.json';p.parent.mkdir(parents=True)
p.write_text(json.dumps({'schema_version':'loopx_machine_configuration_v0','namespaces':{'goal_storage':{'schema_version':'loopx_goal_storage_defaults_v0','new_goal_provider':'sqlite'}}}))
s=ChatHTTPServer(('127.0.0.1',0),ChatRequestHandler);s.registry_path=registry;s.runtime_root=runtime;s.verbose=False
print(json.dumps({'port':s.server_address[1],'python':sys.executable,'module_path':loopx.__file__}),flush=True);s.serve_forever()
`;

export const configurationBackupScenario = {
  id: "configuration-backup",
  async run({browser, url}) {
    const python = resolveTestPython({repoRoot});
    const explicitPython = ["LOOPX_TEST_PYTHON", "LOOPX_PYTHON_BIN", "LOOPX_PYTHON"].some(key => process.env[key]);
    const env = {...process.env};
    if (!explicitPython) env.PYTHONPATH = repoRoot;
    const root = await realpath(await mkdtemp(join(tmpdir(), "loopx-configuration-browser-")));
    const server = spawn(python, [...(explicitPython ? ["-I"] : []), "-u", "-c", serverCode, root], {cwd: repoRoot, env, stdio: ["ignore", "pipe", "pipe"]});
    let diagnostics = "", closed = false;
    server.stderr.on("data", data => {diagnostics = (diagnostics + data).slice(-8000);});
    server.on("error", error => {diagnostics = (diagnostics + error.message).slice(-8000);});
    const closedPromise = new Promise(accept => server.once("close", () => {closed = true; accept();}));
    let context;
    try {
      const backend = await new Promise((accept, reject) => {
        let output = "";
        const cleanup = () => {
          clearTimeout(timer);
          server.stdout.off("data", onData);
          server.off("error", onError);
          server.off("exit", onExit);
        };
        const fail = message => {cleanup(); reject(new Error(`${message}: ${diagnostics}`));};
        const onError = error => fail(`backup backend spawn failed: ${error.message}`);
        const onExit = (code, signal) => fail(`backup backend exited ${code ?? signal}`);
        const onData = data => {
          output += data;
          const newline = output.indexOf("\n");
          if (newline < 0) return;
          try {
            const ready = JSON.parse(output.slice(0, newline));
            assert.ok(Number.isInteger(ready.port) && ready.port > 0 && ready.port <= 65535);
            assert.equal(ready.python, python, "backup backend must use the selected interpreter");
            assert.equal(typeof ready.module_path, "string");
            cleanup(); accept(ready);
          } catch (error) {fail(`invalid backup backend readiness: ${error.message}`);}
        };
        const timer = setTimeout(() => fail("backup backend did not start within 30 seconds"), 30000);
        server.stdout.on("data", onData);
        server.once("error", onError);
        server.once("exit", onExit);
      });
      const {port} = backend;
      context = await openWorkspacePage(browser, url, {beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/configuration-backup/**", async route => {
          const path = new URL(route.request().url()).pathname;
          const response = await route.fetch({url: `http://127.0.0.1:${port}${path}`});
          await route.fulfill({response});
        });
      }});
      const {page} = context;
      await page.getByRole("button", {name: "设置", exact: true}).click();
      await page.locator(".personal-settings-tabs").getByRole("button", {name: "能力中心", exact: true}).click();
      const panel = page.locator("details").filter({has: page.getByText("配置备份与恢复", {exact: true})});
      await panel.locator("summary").click();
      const downloaded = page.waitForEvent("download");
      await panel.getByRole("button", {name: "下载配置备份"}).click();
      const download = await downloaded, bytes = await readFile(await download.path());
      const backup = JSON.parse(bytes);
      assert.equal(backup.data.machine_configuration.namespaces.goal_storage.new_goal_provider, "sqlite");
      assert.equal(backup.data.goals[0].goal_configuration.control_plane.optional.context.length, 90000);
      const input = panel.getByLabel("恢复备份文件");
      await input.setInputFiles({name: "backup.json", mimeType: "application/json", buffer: bytes});
      await panel.getByRole("button", {name: "恢复为隔离检查点"}).click();
      await panel.getByText("检查点已恢复并核对；当前设置及存储 provider 未改变。", {exact: false}).waitFor();
      await page.screenshot({path: resolve(outputDir, "configuration-backup-restored.png"), animations: "disabled"});
      await input.setInputFiles({name: "bad.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({...backup, sha256: "changed"}))});
      await panel.getByRole("alert").waitFor();
      assert.equal(await panel.getByRole("button", {name: "恢复为隔离检查点"}).count(), 0);
      await page.setViewportSize({width: 390, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      return {coverageEntries: context.coverageEntries, errors: context.errors, backend, verified: "Packaged download, full real-backend checkpoint recovery, damaged-file rejection and narrow viewport"};
    } finally {
      try {await context?.close();} finally {
        if (!closed) {
          server.kill("SIGTERM");
          const timer = setTimeout(() => server.kill("SIGKILL"), 5000);
          try {await closedPromise;} finally {clearTimeout(timer);}
        }
        await rm(root, {recursive: true, force: true});
      }
    }
  },
};

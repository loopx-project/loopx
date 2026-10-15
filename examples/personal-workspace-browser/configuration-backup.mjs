import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, readFile, realpath, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {repoRoot, outputDir} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";
import {resolveTestPython} from "../../scripts/test-python.mjs";

// Only the backup routes use a real disposable server. Other workspace state
// uses the shared browser fixture; no Host, model or live runtime is contacted.
const serverCode = `
import json,pathlib,sys,loopx,subprocess,os
from loopx.chat_server import ChatHTTPServer,ChatRequestHandler
from loopx.state_backup import build_state_backup_plan,execute_state_backup_plan
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
r=pathlib.Path(sys.argv[1]); runtime=r/'runtime'; registry=r/'registry.json'
registry.write_text(json.dumps({'common_runtime_root':str(runtime),'goals':[{'id':'fixture','repo':str(r),'control_plane':{'optional':{'context':'complete '*10000}}}]}))
p=runtime/'machine/configuration.json';p.parent.mkdir(parents=True)
p.write_text(json.dumps({'schema_version':'loopx_machine_configuration_v0','namespaces':{'goal_storage':{'schema_version':'loopx_goal_storage_defaults_v0','new_goal_provider':'sqlite'}}}))
source_project=r/'source-project';source_project.mkdir()
source_runtime=r/'source-runtime';source_registry=r/'source-registry.json'
(source_project/'ACTIVE.md').write_text('# Original Goal\\n\\nPreserve every unfinished commitment.\\n')
source_registry.write_text(json.dumps({'common_runtime_root':str(source_runtime),'goals':[{'id':'recovery-fixture','repo':str(source_project),'state_file':'ACTIVE.md','coordination':{'registered_agents':['worker']}}]}))
projection=build_todo_runtime_shadow_projection(goal_id='recovery-fixture',todos=[{'schema_version':'todo_item_v0','todo_id':'todo_recovery_work','role':'agent','status':'open','done':False,'archive_state':'active','source_section':'Agent Todo','text':'Preserve the original commitment','task_class':'advancement_task'}],handoff_mode='soft_claim')
provider=(pathlib.Path(loopx.__file__).parent/'control_plane/coordination/local_authority_provider.ts').as_uri()
source_provider=os.environ.get('LOOPX_RECOVERY_TEST_PROVIDER','sqlite');assert source_provider in ('file','sqlite')
script='import {selectLocalSqliteAuthority,openLocalAuthorityStore} from '+json.dumps(provider)+';let input="";for await(const c of process.stdin)input+=c;const r=JSON.parse(input);if(r.provider==="sqlite")await selectLocalSqliteAuthority(r.root,r.goal,true);const s=await openLocalAuthorityStore(r.root,r.goal);const x=await s.commitAuthority({expected_provider_revision:null,operation_id:"original-work",events:[],receipts:[],next_projection:r.projection});if(x.status!=="applied")throw new Error(JSON.stringify(x));'
subprocess.run(['node','--no-warnings','--experimental-sqlite','--experimental-strip-types','--input-type=module','-e',script],input=json.dumps({'root':str(source_runtime),'goal':'recovery-fixture','provider':source_provider,'projection':projection}),text=True,check=True,capture_output=True)
saved=execute_state_backup_plan(build_state_backup_plan(project=source_project,runtime_root=source_runtime,registry_path=source_registry,output_dir=r/'source-backup',backup_id='recovery-fixture',include_registry_projects=False,include_automations=False,include_skills=False))
source_project.rename(r/'unavailable-project');source_runtime.rename(r/'unavailable-runtime');source_registry.rename(r/'unavailable-registry.json')
assert not source_project.exists() and not source_runtime.exists() and not source_registry.exists()
s=ChatHTTPServer(('127.0.0.1',0),ChatRequestHandler);s.registry_path=registry;s.runtime_root=runtime;s.verbose=False
print(json.dumps({'port':s.server_address[1],'python':sys.executable,'module_path':loopx.__file__,'archive':saved['archive_path'],'manifest':saved['manifest_path'],'source_provider':source_provider,'original_sources_absent':True}),flush=True);s.serve_forever()
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
          const response = await fetchWorkspaceBackend(route, `http://127.0.0.1:${port}`);
          await route.fulfill({response});
        });
        await page.route("**/api/chat/state-recovery**", async route => {
          const response = await fetchWorkspaceBackend(route, `http://127.0.0.1:${port}`);
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
      await panel.getByRole("radio", {name: "完整归档检查", exact: true}).check();
      const recovery = panel.getByRole("region", {name: "完整备份检查"});
      await recovery.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "state-recovery-ready.png"), animations: "disabled"});
      await recovery.getByLabel("完整归档", {exact: true}).setInputFiles(backend.archive);
      await recovery.getByLabel("配套清单", {exact: true}).setInputFiles(backend.manifest);
      await recovery.getByLabel("配套清单", {exact: true}).focus();
      await page.keyboard.press("Tab");
      assert.equal(await page.evaluate(() => document.activeElement.textContent), "验证并建立隔离副本");
      const uploadedArchive = page.waitForResponse(response =>
        new URL(response.url()).pathname === "/api/chat/state-recovery/archive"
        && response.request().method() === "POST");
      await page.keyboard.press("Enter");
      const uploadResponse = await uploadedArchive;
      assert.ok(uploadResponse.ok(), `Archive upload failed (HTTP ${uploadResponse.status()})`);
      await recovery.getByText("隔离副本已核对；Goal 尚未启用。", {exact: true}).waitFor();
      await recovery.getByText("recovery-fixture", {exact: false}).first().waitFor();
      await recovery.getByText("保存的任务: 1", {exact: false}).waitFor();
      const verificationDetails = recovery.locator("details");
      await verificationDetails.locator("summary").click();
      await verificationDetails.getByText("归档字节:", {exact: false}).waitFor();
      const recordedMeasurements = await verificationDetails.locator("p").innerText();
      await verificationDetails.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "state-recovery-measurements.png"), animations: "disabled"});
      await verificationDetails.locator("summary").click();
      assert.equal(backend.original_sources_absent, true);
      const registryAfter = JSON.parse(await readFile(join(root, "registry.json"), "utf8"));
      assert.deepEqual(registryAfter.goals.map(goal => goal.id), ["fixture"], "inspection must not register the restored Goal");
      await recovery.getByRole("status").scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "state-recovery-audit.png"), animations: "disabled"});
      await recovery.getByLabel("完整归档", {exact: true}).setInputFiles({name: "damaged.tar.gz", mimeType: "application/gzip", buffer: Buffer.from("damaged")});
      await recovery.getByRole("button", {name: "验证并建立隔离副本", exact: true}).click();
      await recovery.getByRole("alert").waitFor();
      assert.equal(await recovery.getByText("保存的任务: 1", {exact: false}).count(), 0, "a changed input must clear the old success view");
      // Optional real capacity fixture, produced through the normal backup
      // command. The ordinary browser smoke does not seed 20,001 transactions.
      if (process.env.LOOPX_RECOVERY_HISTORY_OVERFLOW_ARCHIVE) {
        assert.ok(process.env.LOOPX_RECOVERY_HISTORY_OVERFLOW_MANIFEST);
        await recovery.getByLabel("完整归档", {exact: true}).setInputFiles(process.env.LOOPX_RECOVERY_HISTORY_OVERFLOW_ARCHIVE);
        await recovery.getByLabel("配套清单", {exact: true}).setInputFiles(process.env.LOOPX_RECOVERY_HISTORY_OVERFLOW_MANIFEST);
        await recovery.getByRole("button", {name: "验证并建立隔离副本", exact: true}).click();
        await recovery.getByRole("alert").filter({hasText: "20,000"}).waitFor();
        assert.equal(await recovery.getByRole("button", {name: "检查是否已有结果", exact: true}).count(), 0,
          "a definitive pre-publication refusal must not suggest a lost response");
        assert.equal(await recovery.getByText("保存的任务: 1", {exact: false}).count(), 0);
        await page.screenshot({path: resolve(outputDir, "state-recovery-history-capacity.png"), animations: "disabled"});
      }
      await recovery.getByRole("button", {name: "历史检查结果", exact: true}).click();
      const history = recovery.getByLabel("历史备份检查", {exact: true});
      await history.getByRole("button").click();
      await recovery.getByText("已读取历史检查记录；尚未重新验证副本。", {exact: true}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      await recovery.getByRole("status").scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "state-recovery-mobile.png"), animations: "disabled"});
      await recovery.getByRole("status").evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "state-recovery-mobile-result.png"), animations: "disabled"});
      await recovery.getByRole("button", {name: "历史检查结果", exact: true}).click();
      await recovery.getByLabel("历史备份检查", {exact: true}).getByRole("button").click();
      await page.setViewportSize({width: 1512, height: 982});
      await page.reload();
      await page.getByRole("button", {name: "设置", exact: true}).or(page.locator(".personal-settings-tabs")).first().waitFor();
      if (await page.getByRole("button", {name: "设置", exact: true}).count()) await page.getByRole("button", {name: "设置", exact: true}).click();
      await page.locator(".personal-settings-tabs").getByRole("button", {name: "能力中心", exact: true}).click();
      const reopened = page.locator("details").filter({has: page.getByText("配置备份与恢复", {exact: true})});
      await reopened.locator("summary").click();
      await reopened.getByRole("radio", {name: "完整归档检查", exact: true}).check();
      await reopened.getByRole("button", {name: "历史检查结果", exact: true}).click();
      await reopened.getByLabel("历史备份检查", {exact: true}).getByRole("button").click();
      await reopened.getByText("已读取历史检查记录；尚未重新验证副本。", {exact: true}).waitFor();
      await reopened.locator(".personal-recovery-result details summary").click();
      assert.equal(await reopened.locator(".personal-recovery-result details p").innerText(), recordedMeasurements,
        "historical readback must retain original measured values, not time the reload");
      await page.getByRole("button", {name: "语言", exact: true}).click();
      await page.getByRole("radio", {name: "English", exact: true}).check();
      await page.locator(".personal-settings-tabs").getByRole("button", {name: /Capabilit/}).click();
      const english = page.locator("details").filter({has: page.getByText("Configuration backup and recovery", {exact: true})});
      await english.locator("summary").click();
      await english.getByRole("radio", {name: "Full archive inspection", exact: true}).check();
      await english.getByRole("button", {name: "Previous inspections", exact: true}).click();
      await english.getByLabel("Previous backup inspections", {exact: true}).getByRole("button").click();
      await english.getByText("Historical inspection loaded; the copy has not been reverified.", {exact: true}).waitFor();
      await english.getByRole("status").scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "state-recovery-english.png"), animations: "disabled"});
      return {coverageEntries: context.coverageEntries, errors: context.errors, backend, verified: `Packaged configuration recovery and full archive inspection; real ${backend.source_provider} head/history after original-source loss, no Goal registration, keyboard, corrupt-input recovery, immutable history reload and narrow viewport`};
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

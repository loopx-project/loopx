// Packaged Goal settings -> real HTTP -> typed cold-source owner.
// Workspace discovery is synthetic; every storage response comes from the
// production handler and disposable source, never a browser response fixture.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {mkdtemp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../dashboard-browser-smoke-support.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";
import {outputDir, packaged, port, repoRoot, startServer} from "./fixture.mjs";

async function startAuthority({unsettled = true} = {}) {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-old-storage-browser-"));
  const source = "---\ngoal_id: multi-agent-projection\nhandoff_mode: soft_claim\n---\n" +
    "## Agent Todo\n- [ ] Private current requirement\n" +
    "  <!-- loopx:todo todo_id=todo_current role=agent status=open claimed_by=agent-a -->\n" +
    "## Todo Archive\n- [x] Private full archived requirement\n" +
    "  <!-- loopx:todo todo_id=todo_old role=agent status=done -->\n";
  await writeFile(resolve(root, "state.md"), source);
  await writeFile(resolve(root, "registry.json"), JSON.stringify({
    common_runtime_root: resolve(root, "runtime"), goals: [{id: "multi-agent-projection",
      repo: root, state_file: "state.md", coordination: {registered_agents: ["agent-a"]}}],
  }));
  const leases = resolve(root, "runtime/goals/multi-agent-projection/task-leases");
  await mkdir(leases, {recursive: true});
  if (unsettled) await writeFile(resolve(leases, "removed.json"), JSON.stringify({schema_version: "task_lease_v0",
    goal_id: "multi-agent-projection", todo_id: "removed", owner: "agent-a", status: "active",
    idempotency_key: "original-private-key", version: 4, lease_epoch: 2,
    expires_at: "2000-01-01T00:00:00Z", write_scopes: []}));
  const child = spawn(resolveTestPython(), ["-I", "-u", "-c", `
from pathlib import Path
import sys
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path = Path(sys.argv[1]) / "registry.json"
server.runtime_root = Path(sys.argv[1]) / "runtime"
server.runtime_root_override = str(server.runtime_root)
server.verbose = False
print(server.server_port, flush=True)
server.serve_forever()
`, root], {cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"]});
  const lines = createInterface({input: child.stdout});
  let diagnostic = "";
  child.stderr.on("data", chunk => { diagnostic = (diagnostic + chunk).slice(-2000); });
  try {
    const port = await new Promise((accept, reject) => {
      const timeout = setTimeout(() => reject(new Error(`Storage authority timeout: ${diagnostic}`)), 20_000);
      child.once("error", error => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error(`Storage authority exited: ${diagnostic}`)); });
      lines.once("line", line => { clearTimeout(timeout); accept(Number(line)); });
    });
    assert.ok(Number.isSafeInteger(port) && port > 0);
    return {root, source, url: `http://127.0.0.1:${port}`, async close() {
      lines.close();
      if (child.exitCode === null && child.signalCode === null) {
        const exited = new Promise(accept => child.once("exit", accept));
        child.kill("SIGTERM"); await exited;
      }
      await rm(root, {recursive: true, force: true});
    }};
  } catch (error) {
    lines.close(); child.kill("SIGTERM"); await rm(root, {recursive: true, force: true}); throw error;
  }
}

async function importSettled(browser, url, provider) {
  const authority = await startAuthority({unsettled: false});
  let context;
  let applyCount = 0;
  try {
    context = await openWorkspacePage(browser, url, {beforeGoto: async (_api, page) => {
      await page.route(/\/api\/chat\/goal-(storage|ownership)(?:[/?]|$)/, async route => {
        const parsed = new URL(route.request().url());
        const response = await route.fetch({url: authority.url + parsed.pathname + parsed.search});
        if (parsed.pathname.endsWith("/import/apply") && ++applyCount === 1) {
          assert.equal(response.status(), 200); // Commit succeeded; only its response is lost.
          await route.abort("failed");
        } else await route.fulfill({response});
      });
    }});
    const {page} = context;
    async function open() {
      const navigation = page.getByRole("button", {name: "打开 Goal 导航", exact: true});
      if (await navigation.isVisible()) await navigation.click();
      await page.locator(".personal-goal-link", {hasText: "Multi Agent Projection"}).click();
      await page.getByRole("button", {name: "Goal 设置", exact: true}).click();
      await page.getByRole("button", {name: "任务所有权", exact: true}).click();
      return page.getByRole("region", {name: "Goal 数据存储"});
    }
    let panel = await open();
    await panel.getByText("1 项当前任务 · 1 项归档任务 · 0 项未结算 lease", {exact: true}).waitFor();
    await panel.getByRole("combobox", {name: "目标存储", exact: true}).selectOption(provider);
    await panel.getByRole("combobox", {name: "新策略", exact: true}).selectOption("hard_lease");
    await panel.getByRole("button", {name: "备份并预览导入", exact: true}).click();
    const apply = panel.getByRole("button", {name: "导入已审核的 Markdown 来源", exact: true});
    try { await panel.getByRole("checkbox").waitFor(); }
    catch (error) { throw new Error(`${error.message}; panel=${await panel.innerText()}`); }
    assert.ok(await apply.isDisabled());
    const saved = await page.evaluate(() => localStorage.getItem("loopx-storage-preview:multi-agent-projection"));
    assert.ok(saved && !saved.includes(authority.root) && !saved.includes("Private"));
    await page.reload({waitUntil: "networkidle"});
    panel = await open();
    await panel.getByRole("checkbox").waitFor();
    await panel.getByRole("status").filter({hasText: "已准备"}).waitFor();
    assert.ok(await panel.getByRole("button", {name: "导入已审核的 Markdown 来源", exact: true}).isDisabled());
    assert.equal(applyCount, 0, "reload observes the original prepared operation without applying");
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await panel.getByRole("checkbox").focus();
    await page.keyboard.press("Space");
    assert.equal(await panel.getByRole("checkbox").isChecked(), true);
    await panel.getByRole("button", {name: "导入已审核的 Markdown 来源", exact: true}).click();
    await panel.getByRole("alert").waitFor();
    assert.equal(applyCount, 1);
    assert.equal(await page.evaluate(() => localStorage.getItem("loopx-storage-preview:multi-agent-projection")), saved);
    await page.reload({waitUntil: "networkidle"});
    panel = await open();
    await panel.getByRole("status").filter({hasText: "原导入回执已核验"}).waitFor();
    assert.equal(applyCount, 1, "completed readback does not apply again");
    await panel.getByText(provider, {exact: true}).waitFor();
    assert.equal(await panel.getByRole("checkbox").count(), 0);
    assert.equal(await readFile(resolve(authority.root, "state.md"), "utf8"), authority.source);
    await panel.getByRole("status").filter({hasText: "原导入回执已核验"}).scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(outputDir, `goal-storage-import-${provider}-mobile.png`), animations: "disabled"});
    assert.equal(context.errors.filter(e => !e.includes("ERR_FAILED")).length, 0, context.errors.join("; "));
  } finally { await context?.close(); await authority.close(); }
}

const goalStorageScenario = {
  id: "goal-storage",
  async run({browser, url}) {
    const authority = await startAuthority();
    let context;
    let writes = 0;
    try {
      context = await openWorkspacePage(browser, url, {beforeGoto: async (_api, page) => {
        await page.route(/\/api\/chat\/goal-(storage|ownership)(?:[/?]|$)/, async route => {
          if (route.request().method() !== "GET") writes++;
          const parsed = new URL(route.request().url());
          await route.fulfill({response: await route.fetch({url: authority.url + parsed.pathname + parsed.search})});
        });
      }});
      const {page} = context;
      async function open(language = "zh") {
        await page.locator(".personal-goal-link", {hasText: "Multi Agent Projection"}).click();
        await page.getByRole("button", {name: language === "zh" ? "Goal 设置" : "Goal settings", exact: true}).click();
        await page.getByRole("button", {name: language === "zh" ? "任务所有权" : "Task ownership", exact: true}).click();
        return page.getByRole("region", {name: language === "zh" ? "Goal 数据存储" : "Goal data storage"});
      }
      let panel = await open();
      await panel.getByText("1 项当前任务 · 1 项归档任务 · 1 项未结算 lease", {exact: true}).waitFor();
      assert.equal(await panel.getByRole("checkbox").count(), 0);
      assert.equal(await panel.getByRole("combobox").count(), 2);
      assert.ok((await panel.innerText()).includes("此操作不会替你停止 Host"));
      await panel.getByRole("combobox", {name: "新策略", exact: true}).selectOption("soft_claim");
      await panel.getByRole("button", {name: "备份并预览导入", exact: true}).click();
      await panel.getByText("cold_import_lease_requires_settlement", {exact: true}).waitFor();
      assert.equal(await panel.getByRole("checkbox").count(), 0);
      assert.ok(!(await panel.innerText()).includes("Private"));
      await page.screenshot({path: resolve(outputDir, "goal-storage-cold-desktop.png"), animations: "disabled"});
      const outbox = resolve(authority.root, "runtime/authority-shadow/outbox/multi-agent-projection/todos");
      await mkdir(outbox, {recursive: true});
      const residue = resolve(outbox, "original.json");
      await writeFile(residue, "{unrecognized original bytes");
      await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
      await panel.getByText("已发现 outbox 文件，处理结果尚未验证。", {exact: true}).waitFor();
      await writeFile(resolve(authority.root, "state.md"), authority.source.replace("todo_old", "todo_current"));
      await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
      await panel.getByRole("alert").waitFor();
      assert.equal(await panel.getByText(/1 项当前任务/).count(), 0, "failed read must clear stale facts");
      await writeFile(resolve(authority.root, "state.md"), authority.source);
      await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
      await panel.getByText("1 项当前任务 · 1 项归档任务 · 1 项未结算 lease", {exact: true}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      await panel.getByRole("button", {name: "读回当前存储", exact: true}).focus();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      await page.screenshot({path: resolve(outputDir, "goal-storage-cold-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({waitUntil: "networkidle"});
      panel = await open("en");
      await panel.getByText("1 active tasks · 1 archived tasks · 1 unsettled leases", {exact: true}).waitFor();
      assert.ok((await panel.innerText()).includes("This does not stop Hosts"));
      assert.equal(writes, 1, "only the explicit refused preview posts");
      assert.equal(await readFile(resolve(authority.root, "state.md"), "utf8"), authority.source);
      assert.equal(await readFile(residue, "utf8"), "{unrecognized original bytes");
      // Deliberately induced HTTP failures may be logged by the browser.
      assert.equal(context.errors.filter(e => !e.includes("503") && !e.includes("409")).length, 0, context.errors.join("; "));
      for (const provider of ["file", "sqlite"]) await importSettled(browser, url, provider);
      return {note: "Cold/archived tasks, expired orphan lease, original outbox, unavailable source and fresh recovery; refused expired lease; confirmed File/SQLite import, readonly reload and lost-response original receipt recovery; packaged Chinese/English desktop/mobile."};
    } finally { await context?.close(); await authority.close(); }
  },
};

// Standalone entry keeps this bounded acceptance out of unrelated scenario lists.
await mkdir(outputDir, {recursive: true});
const server = await startServer();
let browser;
try {
  const url = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  const result = await goalStorageScenario.run({browser: {newPage: options => browser.newPage({locale: "zh-CN", ...options})}, url});
  console.log(JSON.stringify({status: "PASS", ...result}));
} finally { await browser?.close(); server.kill("SIGTERM"); }

// Packaged Goal settings -> real HTTP -> typed cold-source owner.
// Workspace discovery is synthetic; every storage response comes from the
// production handler and disposable source, never a browser response fixture.
import assert from "node:assert/strict";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {resolve} from "node:path";
import {launchBrowser, loadPlaywright, waitForHttp} from "../dashboard-browser-smoke-support.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";
import {outputDir, packaged, port, startServer} from "./fixture.mjs";

import {startAuthority} from "./goal-settings-backend.mjs";

async function importSettled(browser, url, provider) {
  const authority = await startAuthority({unsettled: false});
  let context;
  let applyCount = 0;
  try {
    const connect = () => openWorkspacePage(browser, url, {beforeGoto: async (_api, page) => {
      await page.route(/\/api\/chat\/goal-(storage|ownership)(?:[/?]|$)/, async route => {
        const parsed = new URL(route.request().url());
        const response = await fetchWorkspaceBackend(route, authority.url);
        if (parsed.pathname.endsWith("/import/apply") && ++applyCount === 1) {
          assert.equal(response.status(), 200); // Commit succeeded; only its response is lost.
          await route.abort("failed");
        } else await route.fulfill({response});
      });
    }});
    context = await connect();
    let {page} = context;
    async function open() {
      const existing = page.getByRole("region", {name: "Goal 数据存储"});
      if (await existing.isVisible()) return existing;
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
    // A native process gets a fresh loopback origin. A fresh browser context
    // likewise has no origin-local carrier; only the real server can recover it.
    await context.close();
    context = await connect();
    ({page} = context);
    panel = await open();
    await panel.getByRole("checkbox").waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem("loopx-storage-preview:multi-agent-projection")), saved);
    assert.equal(applyCount, 0, "a fresh context must recover the same prepared intent without applying");
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await panel.locator('input[type="checkbox"]:enabled').waitFor();
    await panel.getByRole("checkbox").focus();
    await page.keyboard.press("Space");
    assert.equal(await panel.getByRole("checkbox").isChecked(), true);
    await panel.getByRole("button", {name: "导入已审核的 Markdown 来源", exact: true}).click();
    await panel.getByRole("alert").waitFor();
    assert.equal(applyCount, 1);
    assert.equal(await page.evaluate(() => localStorage.getItem("loopx-storage-preview:multi-agent-projection")), saved);
    await context.close();
    context = await connect();
    ({page} = context);
    panel = await open();
    await panel.getByRole("status").filter({hasText: "原导入回执已核验"}).waitFor();
    assert.equal(applyCount, 1, "completed readback does not apply again");
    await panel.getByText(provider, {exact: true}).waitFor();
    assert.equal(await panel.getByRole("checkbox").count(), 0);
    await panel.getByRole("button", {name: "放弃原预览", exact: true}).click();
    await panel.getByRole("button", {name: "预览存储变更", exact: true}).waitFor();
    await context.close();
    context = await connect();
    ({page} = context);
    panel = await open();
    await panel.getByRole("button", {name: "预览存储变更", exact: true}).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem("loopx-storage-preview:multi-agent-projection")), null);
    assert.equal(await readFile(resolve(authority.root, "state.md"), "utf8"), authority.source);
    await panel.scrollIntoViewIfNeeded();
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
          await route.fulfill({response: await fetchWorkspaceBackend(route, authority.url)});
        });
      }});
      const {page} = context;
      async function open(language = "zh") {
        const existing = page.getByRole("region", {name: language === "zh" ? "Goal 数据存储" : "Goal data storage"});
        if (await existing.isVisible()) return existing;
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
      return {note: "Cold/archived tasks, expired orphan lease and outbox refusals; confirmed File/SQLite import, readonly reload/fresh-context recovery, lost-response original receipt and durable dismissal; packaged Chinese/English desktop/mobile."};
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

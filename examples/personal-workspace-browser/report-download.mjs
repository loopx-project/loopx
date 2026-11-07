import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

export const reportDownloadScenario = {
  id: "report-download",
  async run({browser, collectCoverage, url}) {
    const backend = spawn(resolveTestPython({repoRoot}), ["-u", resolve(repoRoot, "examples/personal-workspace-browser/report_download_fixture.py")], {
      cwd: repoRoot, env: {...process.env, PYTHONPATH: "", NODE_OPTIONS: "--experimental-sqlite"}, stdio: ["pipe", "pipe", "pipe"],
    });
    const closed = once(backend, "close");
    let diagnostics = "", context;
    backend.stderr.on("data", chunk => {diagnostics = (diagnostics + chunk).slice(-4000);});
    const records = createInterface({input: backend.stdout})[Symbol.asyncIterator]();
    async function receive(event) {
      let timer;
      try {
        const row = await Promise.race([records.next(), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(`Report backend timeout: ${diagnostics}`)), 60000);
        })]);
        assert.equal(row.done, false, diagnostics);
        const result = JSON.parse(row.value);
        assert.equal(result.event, event);
        return result;
      } finally {clearTimeout(timer);}
    }
    async function command(event) {backend.stdin.write(`${event}\n`); return receive(event);}
    try {
      const ready = await receive("ready");
      let reportRequests = 0;
      const bodyReads = [];
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (api, page) => {
        api.transformStatus = fixture => Object.assign(fixture, JSON.parse(JSON.stringify(fixture).replaceAll("product-release", ready.goal_id)));
        await page.route("**/api/chat/goal-results**", async route => {
          reportRequests++;
          assert.equal(route.request().method(), "GET");
          const requestUrl = new URL(route.request().url());
          if (requestUrl.pathname !== "/api/chat/goal-results") bodyReads.push(requestUrl.pathname);
          await route.fulfill({response: await fetchWorkspaceBackend(route, ready.origin)});
        });
      }});
      const {page, api} = context;
      await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "成果", exact: true}).click();
      const results = page.getByRole("region", {name: "已验收的团队报告"});
      const button = results.getByRole("button", {name: "下载原文", exact: true});
      const report = results.getByRole("navigation", {name: "选择团队报告"})
        .getByRole("button", {name: /todo_lead-report/});
      await report.waitFor();
      assert.deepEqual(bodyReads, [], "Files initially reads the catalog without exporting or loading a report body");
      assert.equal(await button.count(), 0, "Download is offered only after choosing a report");
      await report.click();
      await button.waitFor();
      assert.deepEqual(bodyReads, ["/api/chat/goal-results/todo_lead-report"], "Choosing a report reads only its exact native result");
      const readsBefore = reportRequests;
      async function download(keyboard = false) {
        const pending = page.waitForEvent("download");
        if (keyboard) {await button.focus(); await button.press("Enter");} else await button.click();
        const saved = await pending;
        assert.equal(saved.suggestedFilename(), `report-accepted-report-${ready.sha256.slice(0, 12)}.json`);
        const bytes = await readFile(await saved.path());
        assert.equal(bytes.toString("utf8"), ready.text);
        assert.equal(createHash("sha256").update(bytes).digest("hex"), ready.sha256);
      }
      await page.evaluate(() => {window.__reportCreateURL = URL.createObjectURL; URL.createObjectURL = () => {throw new Error("unavailable");};});
      await button.click();
      await results.getByRole("alert").filter({hasText: "无法开始下载"}).waitFor();
      await page.evaluate(() => {URL.createObjectURL = window.__reportCreateURL; delete window.__reportCreateURL;});
      await download(true);
      assert.equal(await results.getByRole("alert").count(), 0);
      await download();
      assert.equal(reportRequests, readsBefore, "Downloads must reuse the displayed read, not invoke validators again");
      await results.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "report-download-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await button.scrollIntoViewIfNeeded();
      assert(await results.evaluate(el => el.scrollWidth <= el.clientWidth), "Report controls fit narrow screens");
      await download();
      await page.screenshot({path: resolve(outputDir, "report-download-mobile.png"), animations: "disabled"});
      await command("withdraw");
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await page.waitForFunction(() => !document.querySelector('.goal-managed-results[aria-busy="true"]'));
      assert.equal(await button.count(), 0, "Withdrawn report must not remain downloadable");
      await command("restore");
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await button.waitFor();
      await download();
      // Switching Goals withdraws the reader and must not export the previous Goal's report.
      await page.setViewportSize({width: 1512, height: 982});
      await page.locator(".personal-goal-link", {hasText: "Research Monitor"}).click();
      await page.waitForFunction(() => !document.querySelector('.goal-managed-results[aria-busy="true"]'));
      assert.equal(await page.getByRole("button", {name: "下载原文", exact: true}).count(), 0);
      const state = await command("inspect");
      assert.equal(state.authority_unchanged, true);
      assert.equal(state.posts, 0);
      assert.equal(state.sha256, ready.sha256);
      assert.equal(api.turnRequests.length, 0);
      assert.equal(api.actionApplies.length, 0);
      return {note: "Catalog-first exact canonical report download, repeated/keyboard/narrow, source withdrawal/recovery and Goal isolation", coverageEntries: await context.close()};
    } finally {
      if (context && !context.page.isClosed()) await context.close();
      backend.stdin.end();
      const [code] = await closed;
      assert.equal(code, 0, diagnostics);
    }
  },
};

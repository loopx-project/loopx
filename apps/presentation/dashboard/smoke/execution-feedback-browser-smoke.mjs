// The same native Explore records cross HTTP, client decoding and both readers.
// Fault injection changes only transport; source state and authority stay intact.
import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {startHistoryOwner} from "../../../../examples/personal-workspace-browser/research-history.mjs";
import {launchBrowser, loadPlaywright} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const root = resolve(import.meta.dirname, "../../../..");
const output = resolve(root, ".local/feedback-qualification");
await mkdir(output, {recursive: true});
const missing = process.env.LOOPX_FEEDBACK_EXPECT_MISSING === "1";
const browser = await launchBrowser(loadPlaywright().chromium);
try {
  for (const provider of ["file", "sqlite"]) {
    const owner = await startHistoryOwner(resolve(root, "apps/presentation/dashboard/src/data/fixtures/execution-feedback-readback.json"),
      {provider, assetsDir: process.env.LOOPX_FEEDBACK_ASSETS});
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, locale: "zh-CN"});
    let corrupt = false;
    try {
      await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
      await page.route("**/api/chat/explore-results?*", async route => {
        if (!corrupt || new URL(route.request().url()).searchParams.get("view") !== "findings") return route.continue();
        const response = await route.fetch(), packet = await response.json();
        const changed = packet.items.find(item => item.execution_observation);
        changed.execution_observation.result_candidate_sha256 = "b".repeat(64);
        return route.fulfill({response, json: packet});
      });
      const url = new URL("/chat/", owner.base);
      url.search = new URLSearchParams({goalId: "feedback-readback", view: "research", research_goal: "feedback-readback",
        research_reading: "history", research_node: "method-choice", research_finding: "result-11"});
      await page.goto(url.href);
      const saved = page.locator(".research-saved-evidence");
      await saved.locator(":scope > summary").click();
      const evidence = saved.locator(".goal-explore-results");
      const reader = evidence.locator(".goal-team-result-reader");
      await reader.waitFor();
      const observation = reader.locator("details").filter({has: page.locator("summary", {hasText: "实际执行与资源观测"})});
      if (missing) {
        assert.equal(await observation.count(), 0, "Baseline saved findings discarded execution observations");
        await reader.scrollIntoViewIfNeeded();
        await page.screenshot({path: resolve(output, `before-${provider}-desktop.png`), animations: "disabled"});
        continue;
      }
      assert.equal(await observation.count(), 1);
      assert.equal(await observation.getAttribute("open"), null, "Execution detail stays on demand");
      await observation.locator(":scope > summary").click();
      const text = await observation.innerText();
      assert.match(text, /broad-search → targeted-check/);
      assert.match(text, /counterexample ruled out/);
      assert.match(text, /12\.5 \/ 60/);
      assert.match(text, /未测量/);
      assert.match(text, /input-v1/);
      assert.match(text, /artifact:negative-result/);
      assert.match(text, /不等于方法有效/);
      await observation.scrollIntoViewIfNeeded();
      await observation.screenshot({path: resolve(output, `after-${provider}-detail.png`), animations: "disabled"});
      await page.screenshot({path: resolve(output, `after-${provider}-desktop.png`), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await observation.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Narrow reader has no horizontal overflow");
      await page.screenshot({path: resolve(output, `after-${provider}-mobile.png`), animations: "disabled"});
      await evidence.getByRole("combobox", {name: "选择结论"}).selectOption("result-0");
      assert.equal(await observation.count(), 0, "Missing observation adds no invented execution block");
      await evidence.getByRole("combobox", {name: "选择结论"}).selectOption("result-11");
      corrupt = true;
      await evidence.getByRole("button", {name: "刷新证据"}).click();
      await evidence.getByRole("alert").waitFor();
      assert.equal(await reader.count(), 0, "Wrong-candidate evidence cannot leave old facts visible");
      corrupt = false;
      await evidence.getByRole("button", {name: "刷新证据"}).click();
      await reader.waitFor();
      assert.equal(await observation.count(), 1, "Valid source recovers in place");
      const history = page.locator(".research-history-detail");
      const historicObservation = history.locator("details").filter({has: page.locator("summary", {hasText: "实际执行与资源观测"})});
      await historicObservation.first().locator(":scope > summary").click();
      assert.match(await historicObservation.first().innerText(), /broad-search → targeted-check/, "Append-only history shares the same reader");
      const readback = await (await fetch(`${owner.base}/fixture/readback`)).json();
      assert.equal(readback.source_unchanged, true);
      assert.equal(readback.mutation_attempts, 0);
      console.log(`${provider}: HTTP -> saved/history reader, missing/rejected/recovery, desktop/narrow PASS`);
    } finally {await page.close(); await owner.close();}
  }
} finally {await browser.close();}

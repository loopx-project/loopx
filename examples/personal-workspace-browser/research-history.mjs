import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {readFile, writeFile} from "node:fs/promises";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

async function startHistoryOwner() {
  const child = spawn(resolveTestPython(), ["-u", "examples/personal-workspace-browser/research-history-backend.py"],
    {cwd: repoRoot, env: {...process.env, LOOPX_USAGE_PING: "0"}, stdio: ["ignore", "pipe", "pipe"]});
  let diagnostic = "";
  child.stderr.on("data", chunk => {diagnostic = (diagnostic + chunk).slice(-4000);});
  const lines = createInterface({input: child.stdout});
  const close = async () => {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(accept => child.once("exit", accept));
      child.kill("SIGTERM"); await exited;
    }
  };
  try {
    const ready = await new Promise((accept, reject) => {
      const timer = setTimeout(() => reject(new Error(`History owner startup timed out: ${diagnostic}`)), 20_000);
      child.once("error", error => {clearTimeout(timer); reject(error);});
      child.once("exit", () => {clearTimeout(timer); reject(new Error(`History owner exited: ${diagnostic}`));});
      lines.once("line", line => {
        clearTimeout(timer);
        try {accept(JSON.parse(line));} catch (failure) {reject(failure);}
      });
    });
    assert(Number.isSafeInteger(ready.port) && ready.port > 0);
    return {...ready, base: `http://127.0.0.1:${ready.port}`, close};
  } catch (failure) {await close(); throw failure;}
}

export const researchHistoryScenario = {
  id: "research-history",
  async run({browser, collectCoverage, url}) {
    const capture = JSON.parse(await readFile(resolve(repoRoot, "apps/presentation/dashboard/src/data/fixtures/edgebench-research-history.json")));
    const owner = await startHistoryOwner();
    let context;
    const pages = [];
    let unavailable = false;
    try {
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_, page) => {
        await page.route("**/status.json*", async route => {
          const response = await fetchWorkspaceBackend(route, owner.base);
          assert.equal(response.status(), 200);
          await route.fulfill({response});
        });
        await page.route("**/api/chat/explore-results?*", async route => {
          if (unavailable) return route.fulfill({status: 503, json: {error: "Isolated history transport unavailable"}});
          const response = await fetchWorkspaceBackend(route, owner.base);
          assert.equal(response.status(), 200);
          const value = await response.json();
          if (new URL(route.request().url()).searchParams.get("view") === "history") pages.push(value);
          await route.fulfill({response, json: value});
        });
      }});
      const {page, api} = context;
      await page.locator(".personal-goal-link", {hasText: "EdgeBench · 脱敏研究历史"}).click();
      const history = page.getByRole("region", {name: "研究历程", exact: true});
      await history.getByRole("status").filter({hasText: "364 / 364"}).waitFor();
      assert.equal(pages.length, 10, "The complete capture requires every bounded HTTP page");
      assert.deepEqual(pages.flatMap(value => value.items).map(row => row.event_id), capture.events.map(row => row.event_id));
      await history.locator(".research-phases").waitFor();
      const target = await history.locator(".research-timeline-marker").first().evaluate(button => {
        const box = button.getBoundingClientRect(), dot = getComputedStyle(button, "::after");
        return {width: box.width, height: box.height, dotWidth: dot.width, dotHeight: dot.height};
      });
      assert(target.width >= 44 && target.height >= 44, "A readable marker has a separate touch target");
      assert.equal(target.dotWidth, target.dotHeight, "A point is not stretched into a status bar by button minimums");
      const day = history.locator(".research-unlinked-days button").first();
      await day.click();
      await history.locator(".research-history-detail").waitFor();
      assert(new URL(page.url()).searchParams.has("research_group"), "A dense date group opens in one interaction");
      await history.locator(".research-history-detail").press("Escape");
      await history.getByRole("button", {name: "关系图", exact: true}).click();
      assert.equal(await history.locator(".research-history-edges g").count(), 3, "Only the three recorded typed edges use the solid relationship layer");
      assert.equal(await history.locator(".research-history-edges .is-grouping").count(), 12, "Parentage remains a distinct display relation");
      await page.screenshot({path: resolve(outputDir, "research-history-recorded-overview.png"), animations: "disabled"});
      await history.getByRole("button", {name: /^EdgeBench · 长程研究与证据积累/}).click();
      await history.locator(".research-history-detail").waitFor();
      const selected = new URL(page.url()).searchParams.get("research_node");
      await page.reload({waitUntil: "networkidle"});
      await history.locator(".research-history-detail").waitFor();
      assert.equal(new URL(page.url()).searchParams.get("research_node"), selected);
      assert.match(await history.textContent(), /364 \/ 364/);
      await page.screenshot({path: resolve(outputDir, "research-history-recorded-desktop.png"), animations: "disabled"});
      await history.screenshot({path: resolve(outputDir, "research-history-recorded-detail.png"), animations: "disabled"});
      unavailable = true;
      await history.getByRole("button", {name: "刷新记录", exact: true}).click();
      await history.getByRole("alert").waitFor();
      assert.match(await history.textContent(), /364 \/ 364/, "A failed refresh cannot erase a verified complete capture");
      assert.equal(new URL(page.url()).searchParams.get("research_node"), selected);
      unavailable = false;
      await history.getByRole("button", {name: "刷新记录", exact: true}).click();
      await history.getByRole("alert").waitFor({state: "hidden"});
      await history.locator(".research-history-detail").press("Escape");
      assert.equal(await history.locator(".research-history-detail").count(), 0);
      await page.setViewportSize({width: 390, height: 844});
      await page.emulateMedia({reducedMotion: "reduce"});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, "research-history-recorded-mobile.png"), animations: "disabled"});
      assert.equal(api.turnRequests.length, 0, "Reading the graph cannot start an Agent");
      const readback = await (await fetch(`${owner.base}/fixture/readback`)).json();
      assert.equal(readback.source_unchanged, true);
      assert.equal(readback.mutation_attempts, 0);
      await writeFile(resolve(outputDir, "research-history-readback.json"), JSON.stringify({
        source: "redacted operator structure; rewritten prose and opaque references", event_count: 364,
        explicit_edges: 3, parent_nodes: 12, initial_pages: 10, ...readback,
      }, null, 2));
      return {note: "Redacted real structure through production history HTTP + packaged UI; full pagination, typed/parent edges, focus/reload, narrow layout and failed-refresh recovery. No benchmark qualification or model dispatch.",
        coverageEntries: await context.close()};
    } finally {
      if (context && !context.page.isClosed()) await context.close();
      await owner.close();
    }
  },
};

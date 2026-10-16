import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const managedGoalResultsScenario = {
  id: "managed-goal-results",
  async run({browser, collectCoverage, url}) {
    const context = await openWorkspacePage(browser, url, {collectCoverage});
    const {page, api} = context;
    const digest = "a".repeat(64);
    let stale = false;
    let malformed = false;
    let reads = 0;
    await page.route("**/api/chat/goal-results**", route => {
      const request = new URL(route.request().url());
      if (request.pathname.endsWith("/todo_lead-report")) {
        reads++;
        return stale
          ? route.fulfill({status: 409, json: {error: "acceptance changed"}})
          : route.fulfill({json: {
            ok: true, goal_id: "product-release", todo_id: "todo_lead-report",
            result: {sha256: digest, content_type: "text/markdown", producer_agent_id: "lead"},
            text: "# Revised conclusion\n\n| Measure | Value |\n| --- | --- |\n| Free cash flow | 25 |\n",
          }});
      }
      if (malformed) return route.fulfill({json: {ok: true}});
      return route.fulfill({json: {
        ok: true, items: [{todo_id: "todo_lead-report", title: "Revised cash flow",
          producer_agent_id: "lead", sha256: digest, content_type: "text/markdown", size_bytes: 80}],
        total: 1, next_cursor: null, unavailable_count: 0, unavailable_todo_ids: [],
      }});
    });
    try {
      assert.equal(reads, 0, "The directory must not read a Goal's report body");
      await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
      const overviewResults = page.getByRole("region", {name: "已验收的团队报告"});
      await overviewResults.getByRole("table").waitFor();
      assert.equal(reads, 1, "The visible overview Results must exact-read the selected report");
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "成果", exact: true}).click();
      const results = page.getByRole("region", {name: "已验收的团队报告"});
      await results.getByRole("table").waitFor();
      assert.equal(reads, 2, "Opening Files must revalidate the body after its fresh inventory read");
      assert.match(await results.textContent(), /Revised cash flow/);
      assert.equal(await results.locator("script,img").count(), 0);
      await page.screenshot({path: resolve(outputDir, "managed-goal-results-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      assert(await results.evaluate(el => el.scrollWidth <= el.clientWidth), "Files report must fit a phone");
      await page.screenshot({path: resolve(outputDir, "managed-goal-results-mobile.png"), animations: "disabled"});
      stale = true;
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await results.getByRole("alert").waitFor();
      assert.equal(await results.getByRole("table").count(), 0, "Stale content must be cleared");
      malformed = true;
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await results.getByRole("alert").filter({hasText: "报告列表响应不完整"}).waitFor();
      assert.equal(await results.getByRole("table").count(), 0, "Malformed inventory must not restore stale content");
      assert(await page.getByTestId("personal-goal-outputs").getByRole("button", {name: /Product Release milestone report/}).isVisible(),
        "Malformed managed results must not crash the existing Files view");
      assert.equal(api.turnRequests.length, 0, "Report reading must not start a model");
      await page.screenshot({path: resolve(outputDir, "managed-goal-results-stale.png"), animations: "disabled"});
      return {note: "Packaged Files shows only exact-read reports and clears stale results", coverageEntries: await context.close()};
    } finally {
      if (!page.isClosed()) await context.close();
    }
  },
};

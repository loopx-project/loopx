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
    let expiredPage = false;
    let reads = 0;
    const readOrder = [];
    let delayedRead = null;
    const rows = [
      {todo_id: "todo_lead-report", title: "Revised cash flow", producer_agent_id: "lead", sha256: digest},
      {todo_id: "todo_review-report", title: "Revised cash flow", producer_agent_id: "reviewer", sha256: "b".repeat(64)},
      {todo_id: "todo_later-report", title: "Later accepted report", producer_agent_id: "lead", sha256: "c".repeat(64)},
    ].map(row => ({...row, content_type: "text/markdown", size_bytes: 80, completed_at: "2026-01-15T10:00:00Z"}));
    await page.route("**/api/chat/goal-results**", async route => {
      const request = new URL(route.request().url());
      const row = rows.find(item => request.pathname.endsWith(`/${item.todo_id}`));
      if (row) {
        reads++;
        readOrder.push(row.todo_id);
        if (delayedRead) await delayedRead;
        return stale
          ? route.fulfill({status: 409, json: {error: "acceptance changed"}})
          : route.fulfill({json: {
            ok: true, goal_id: "product-release", todo_id: row.todo_id,
            result: {sha256: row.sha256, content_type: "text/markdown", producer_agent_id: row.producer_agent_id},
            text: "# Revised conclusion\n\n| Measure | Value |\n| --- | --- |\n| Free cash flow | 25 |\n",
          }});
      }
      const cursor = request.searchParams.get("cursor");
      readOrder.push(cursor ?? "inventory");
      if (malformed) return route.fulfill({json: {ok: true}});
      if (expiredPage && cursor) return route.fulfill({status: 410, json: {error: "catalog cursor expired"}});
      return route.fulfill({json: {
        ok: true, items: cursor === "empty-page" ? [] : cursor === "last-page" ? [rows[2]] : rows.slice(0, 2),
        total: 3, next_cursor: cursor === "last-page" ? null : cursor === "empty-page" ? "last-page" : "empty-page",
        unavailable_count: 0, unavailable_todo_ids: [],
      }});
    });
    try {
      assert.equal(reads, 0, "The directory must not read a Goal's report body");
      await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
      const goalNav = page.getByRole("navigation", {name: "Goal 视图"});
      await goalNav.getByRole("button", {name: "概览", exact: true}).click();
      await page.locator(".goal-overview").waitFor();
      assert.equal(await goalNav.getByRole("button", {name: "概览", exact: true}).getAttribute("aria-current"), "page");
      assert.equal(await page.getByRole("region", {name: "已验收的团队报告"}).count(), 0,
        "Full reports belong in Files, not a second copy in Overview");
      assert.equal(reads, 0, "Overview must not read a report body");
      await goalNav.getByRole("button", {name: "成果", exact: true}).click();
      const results = page.getByRole("region", {name: "已验收的团队报告"});
      const reviewer = results.getByRole("button", {name: /Revised cash flow.*reviewer/});
      await reviewer.waitFor();
      assert.equal(await results.locator(".result-catalog-row").count(), 2, "Equal titles retain both native report identities");
      assert.equal(reads, 0, "Files initially reads only the catalog");
      assert.equal(await results.getByRole("table").count(), 0);
      const catalogViewport = page.viewportSize();
      await results.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "managed-result-catalog-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await results.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "managed-result-catalog-mobile.png"), animations: "disabled"});
      await page.setViewportSize(catalogViewport);
      await reviewer.click();
      await results.getByRole("table").waitFor();
      assert.equal(reads, 1, "Files must revalidate the selected body once");
      assert.deepEqual(readOrder, ["inventory", "todo_review-report"]);
      assert.match(await results.textContent(), /Revised cash flow/);
      assert.equal(await results.locator("script,img").count(), 0);
      await page.screenshot({path: resolve(outputDir, "managed-goal-results-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      assert(await results.evaluate(el => el.scrollWidth <= el.clientWidth), "Files report must fit a phone");
      await results.getByRole("button", {name: "下载原文", exact: true}).scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "managed-goal-results-mobile.png"), animations: "disabled"});
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await results.getByRole("table").waitFor();
      assert.equal(readOrder.at(-1), "todo_review-report", "Refresh retains the chosen native report, even with duplicate titles");
      const back = results.getByRole("button", {name: "返回成果目录", exact: true});
      await back.click();
      assert(await reviewer.evaluate(el => new Promise(done => requestAnimationFrame(() => done(el === document.activeElement)))), "Returning restores the exact catalog row");
      let releaseRead;
      delayedRead = new Promise(resolveRead => {releaseRead = resolveRead;});
      const lead = results.getByRole("button", {name: /Revised cash flow.*lead/});
      const pending = page.waitForRequest(request => request.url().includes("goal-results/todo_lead-report"));
      await lead.click();
      await pending;
      await back.click();
      assert(await lead.evaluate(el => new Promise(done => requestAnimationFrame(() => done(el === document.activeElement)))), "Cancel restores the originating row while a body read is pending");
      const late = page.waitForResponse(response => response.url().includes("goal-results/todo_lead-report"));
      releaseRead(); delayedRead = null;
      await late;
      await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
      assert.equal(await results.getByRole("table").count(), 0, "A canceled late read cannot reopen the body");
      await results.getByRole("button", {name: "下一页", exact: true}).click();
      await results.getByText("本页没有可核验的报告，可继续下一页。", {exact: true}).waitFor();
      await results.getByRole("button", {name: "下一页", exact: true}).click();
      const later = results.getByRole("button", {name: /Later accepted report/});
      await later.click();
      await results.getByRole("table").waitFor();
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await results.getByRole("table").waitFor();
      assert.deepEqual(readOrder.slice(-2), ["last-page", "todo_later-report"], "Refreshing a later page revalidates its exact selection");
      stale = true;
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await results.getByRole("alert").waitFor();
      assert.equal(await results.getByRole("table").count(), 0, "Stale content must be cleared");
      await back.click();
      assert(await later.evaluate(el => new Promise(done => requestAnimationFrame(() => done(el === document.activeElement)))), "A failed read can return to its original row");
      stale = false;
      await later.click();
      await results.getByRole("table").waitFor();
      malformed = true;
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      await results.getByRole("alert").filter({hasText: "报告列表响应不完整"}).waitFor();
      assert.equal(await results.getByRole("table").count(), 0, "Malformed inventory must not restore stale content");
      assert.equal(await back.count(), 0, "An unavailable inventory returns to catalog recovery without keeping a later-page reader selected");
      assert(await page.getByTestId("personal-goal-outputs").getByRole("button", {name: /Product Release milestone report/}).isVisible(),
        "Malformed managed results must not crash the existing Files view");
      malformed = false; stale = false; expiredPage = true;
      const recovery = page.waitForRequest(request => new URL(request.url()).pathname === "/api/chat/goal-results");
      await results.getByRole("button", {name: "刷新", exact: true}).click();
      assert.equal(new URL((await recovery).url()).searchParams.get("cursor"), null,
        "After a failed inventory read, Refresh restarts pagination rather than retrying an expired later-page cursor");
      await reviewer.waitFor();
      assert.equal(await results.getByRole("table").count(), 0, "Inventory recovery keeps explicit body selection");
      assert.equal(api.turnRequests.length, 0, "Report reading must not start a model");
      await page.screenshot({path: resolve(outputDir, "managed-goal-results-stale.png"), animations: "disabled"});
      return {note: "Packaged Files lists native report identities before selected reads, retains later-page versions, cancels late responses, and returns focus after errors", coverageEntries: await context.close()};
    } finally {
      if (!page.isClosed()) await context.close();
    }
  },
};

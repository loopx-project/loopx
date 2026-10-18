import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// A fresh install opens to the manager home with zero registered Goals. The
// status source is healthy (a missing default registry is a warning, not an
// error), so the home must show its first-Goal state instead of empty lanes or
// a health banner, and example requests may only fill the composer. A source
// that is actually unhealthy keeps its banner in owner language and does not
// invite the first Goal.

function emptyWorkspace(fixture, { healthy = true } = {}) {
  fixture.ok = healthy;
  fixture.goal_count = 0;
  fixture.run_count = 0;
  fixture.run_history.goals = [];
  fixture.run_history.recent_runs = [];
  fixture.run_history.goal_count = 0;
  fixture.run_history.run_count = 0;
  fixture.attention_queue.items = [];
  fixture.attention_queue.item_count = 0;
  fixture.attention_queue.needs_user_or_controller = 0;
  fixture.attention_queue.needs_codex = 0;
  fixture.attention_queue.watching_external_evidence = 0;
  if (fixture.usage_summary) fixture.usage_summary.goals = [];
  fixture.contract = healthy
    ? { ok: true, summary: { errors: 0, warnings: 1, checks: 3 }, errors: [],
        warnings: ["registry file does not exist: [local-path]"],
        checks: ["runtime root resolved: [local-path]", "run-history goals=0 runs=0", "public boundary scan deferred for status hot path"] }
    : { ok: false, summary: { errors: 1, warnings: 0, checks: 2 }, errors: ["registry file is not valid JSON"], warnings: [],
        checks: ["runtime root resolved: [local-path]", "run-history goals=0 runs=0"] };
}

export const firstGoalHomeScenario = {
  id: "first-goal-home",
  async run({ browser, collectCoverage, url }) {
    const coverageEntries = [];
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 },
      beforeGoto: (api) => { api.transformStatus = (fixture) => emptyWorkspace(fixture); } });
    const { api, page } = session;
    // "Sends nothing" means no Turn is created. Session admission on focus is
    // not a send, so only turn creation and steering count.
    const turnRequests = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && /\/api\/chat\/sessions\/[^/]+\/turns/.test(request.url())) turnRequests.push(request.url());
    });
    try {
      const firstGoal = page.getByTestId("personal-first-goal");
      await firstGoal.waitFor({ state: "visible", timeout: 15_000 });
      assert.equal(await page.locator(".personal-system-health-banner").count(), 0, "A missing default registry is not a health concern");
      assert.equal(await page.locator(".personal-home-section-title").count(), 0, "No empty 'All Goals' heading in the first-Goal state");
      assert.match(await firstGoal.locator("h2").innerText(), /还没有 Goal/);
      assert.match(await page.locator(".personal-manager-greeting p").innerText(), /我们从第一个 Goal 开始/);
      // Status source details stay collapsed and carry the source's own warning.
      const details = firstGoal.locator("details.personal-first-goal-diagnostics");
      assert.equal(await details.getAttribute("open"), null, "Diagnostics are collapsed by default");
      assert.match((await details.locator("li").first().textContent()) ?? "", /registry file does not exist/);
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-desktop.png"), animations: "disabled" });

      // An example request only fills the composer; nothing is sent.
      const composer = page.locator(".personal-channel-composer textarea");
      const example = firstGoal.locator(".personal-first-goal-examples button").first();
      const exampleText = (await example.innerText()).trim();
      await example.click();
      assert.equal((await composer.inputValue()).trim(), exampleText, "The example fills the composer verbatim");
      assert.equal(turnRequests.length, 0, `Selecting an example sends nothing: ${turnRequests.join(", ")}`);
      await page.keyboard.press("Escape");

      // Create a Goal opens the existing typed Goal form in place.
      await firstGoal.locator(".personal-first-goal-create").click();
      const form = page.locator("dialog.personal-action-form");
      await form.waitFor({ state: "visible", timeout: 10_000 });
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-create.png"), animations: "disabled" });
      await page.keyboard.press("Escape");
      await form.waitFor({ state: "hidden", timeout: 10_000 });

      // A warm refresh that fails is not an empty workspace: both status reads
      // answer HTTP 500, the invitation is withdrawn, the failure is shown with
      // a retry, and the header never reports an update. Repairing the source
      // and retrying restores the first-Goal state.
      api.statusFailure = 500;
      const refresh = page.getByRole("button", { name: "刷新状态", exact: true });
      await refresh.click();
      const sourceRetry = page.getByTestId("personal-source-retry");
      await sourceRetry.waitFor({ state: "visible", timeout: 15_000 });
      assert.equal(await firstGoal.count(), 0, "A failed read withdraws the first-Goal invitation");
      const failedBanner = page.locator(".personal-system-health-banner");
      assert.match(await failedBanner.innerText(), /状态来源读取失败/, "The failure is shown in owner language");
      assert.match(await failedBanner.innerText(), /最近一次读取失败：/);
      const refreshFeedback = page.locator(".personal-refresh-control").getByRole("status");
      await page.locator("small", { hasText: "刷新失败" }).first().waitFor({ state: "visible", timeout: 10_000 });
      assert.equal(await refreshFeedback.textContent(), "", "A failed refresh never announces an update");
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-source-failed.png"), animations: "disabled" });
      api.statusFailure = null;
      // Success is announced in the accessible status region. Observe the
      // transient announcement from the retry, alongside the restored board.
      await Promise.all([
        refreshFeedback.filter({ hasText: "刚刚更新" }).waitFor({ state: "attached", timeout: 10_000 }),
        sourceRetry.click(),
      ]);
      await firstGoal.waitFor({ state: "visible", timeout: 15_000 });
      assert.equal(await page.locator(".personal-system-health-banner").count(), 0, "A repaired source clears the failure");

      // English copy and the phone composition.
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({ waitUntil: "networkidle" });
      await firstGoal.waitFor({ state: "visible", timeout: 15_000 });
      assert.match(await firstGoal.locator("h2").innerText(), /No Goals yet/);
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-en.png"), animations: "disabled" });
      await page.evaluate(() => localStorage.removeItem("loopx-pw-locale"));
      await page.setViewportSize({ width: 390, height: 844 });
      await page.reload({ waitUntil: "networkidle" });
      await firstGoal.waitFor({ state: "visible", timeout: 15_000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, "No horizontal overflow on a phone");
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-phone.png"), animations: "disabled" });
      coverageEntries.push(...await session.close());
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }

    // Negative: an unhealthy source with zero Goals keeps its banner and does
    // not present the first-Goal invitation.
    const unhealthy = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 },
      beforeGoto: (api) => { api.transformStatus = (fixture) => emptyWorkspace(fixture, { healthy: false }); } });
    try {
      const banner = unhealthy.page.locator(".personal-system-health-banner");
      await banner.waitFor({ state: "visible", timeout: 15_000 });
      const bannerText = await banner.innerText();
      assert.match(bannerText, /控制面契约检查未通过：1 项错误，0 项警告/, "Health issues read as owner language");
      assert.doesNotMatch(bannerText, /payload\.ok/, "No protocol fragment in primary copy");
      assert.equal(await banner.locator("details.personal-system-health-diagnostics").getAttribute("open"), null, "Source lines stay collapsed");
      assert.match((await banner.locator("details li").first().textContent()) ?? "", /registry file is not valid JSON/);
      assert.equal(await unhealthy.page.getByTestId("personal-first-goal").count(), 0, "An unhealthy source does not invite a first Goal");
      await unhealthy.page.screenshot({ path: resolve(outputDir, "first-goal-home-unhealthy.png"), animations: "disabled" });
      coverageEntries.push(...await unhealthy.close());
    } catch (error) {
      await unhealthy.close().catch(() => {});
      throw error;
    }
    return { coverageEntries };
  },
};

import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";
import { installShowcaseWorkspace, openShowcaseGoal } from "./showcase-workspace.mjs";

// Visual-quality contract for the Goal overview, rendered on the showcase
// workspace: one hero, a folded composer, exact observed counts with a short swap transition, reduced motion, and a region failure that
// stays announced without becoming a banner.

const workValue = (page) => page.getByTestId("goal-vitals").locator(".goal-vital").nth(1).locator(".goal-vital-value").innerText();
const progressIsReadable = page => page.locator(".goal-overview-progress > span").first().evaluate(node => {
  const rect = node.getBoundingClientRect();
  return rect.height <= parseFloat(getComputedStyle(node).lineHeight) + 1 && node.scrollWidth <= node.clientWidth;
});

export const goalShowcaseScenario = {
  id: "goal-showcase",
  async run({ browser, collectCoverage, url }) {
    const coverageEntries = [];
    const state = { done: 23, cost: 24.8, deliveryFails: false };
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: (api, page) => installShowcaseWorkspace(api, page, state) });
    const { page } = session;
    try {
      await openShowcaseGoal(page);
      const overview = page.locator(".goal-overview");
      assert.equal(await overview.getByRole("alert").count(), 0, "The showcase state has no region failure");
      assert.match(await page.locator(".goal-overview-objective").innerText(), /ledger API/);
      assert.match(await page.locator(".goal-overview-progress").innerText(), /2\/4/);
      assert.ok(await progressIsReadable(page), "The short criterion count fits on one line, rather than being squeezed by its meter");
      assert.equal(await page.locator(".goal-criteria-meter i").count(), 4, "The meter has one segment per criterion");
      assert.equal(await page.locator(".goal-vital-dot.is-live").count(), 1, "A host-recorded open turn is the one live glyph");
      assert.equal(await workValue(page), "23/28");

      assert.equal(await overview.getByRole("heading", { level: 2, name: "概览" }).count(), 0, "The tab already names the view; the body does not repeat it");
      assert.equal(await page.getByTestId("overview-composer-pill").count(), 1, "Overview folds the composer to one line");
      assert.equal(await page.getByRole("textbox", { name: /发送消息|Send a message/ }).count(), 0);
      await page.screenshot({ path: resolve(outputDir, "goal-showcase-overview.png"), animations: "disabled" });
      const suggestions = page.locator(".personal-composer-folded .personal-composer-tools > summary");
      await suggestions.click();
      await page.getByRole("button", { name: "配置定时检查" }).waitFor({ state: "visible" });
      await page.keyboard.press("Escape");
      assert.ok(await suggestions.evaluate((node) => node === document.activeElement), "Escape returns focus to the suggestions trigger");
      await suggestions.click();
      await page.getByRole("button", { name: "配置定时检查" }).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "goal-showcase-suggestions.png"), animations: "disabled" });
      await suggestions.click();

      // A refresh may show either observation, never an interpolated task or cost.
      state.done = 24;
      state.cost = 25.4;
      const samples = page.evaluate(() => new Promise((accept) => {
        const values = [];
        const started = performance.now();
        const read = () => {
          const value = document.querySelectorAll('[data-testid="goal-vitals"] .goal-vital')[1]?.querySelector(".goal-vital-value")?.textContent ?? "";
          values.push(value);
          if (performance.now() - started < 2_500 && value !== "24/29") requestAnimationFrame(read);
          else accept(values);
        };
        read();
      }));
      await page.getByRole("button", { name: /^(刷新状态|Refresh status)$/ }).click();
      const observed = await samples;
      assert.equal(observed.at(-1), "24/29", `The count settles on the new observation: ${observed.slice(-5)}`);
      for (const value of observed) {
        assert.ok(["23/28", "24/29"].includes(value), `The refresh invented an intermediate count: ${value}`);
      }
      await page.waitForTimeout(700);
      const cost = await page.getByTestId("goal-vitals").locator(".goal-vital").nth(4).locator(".goal-vital-value").innerText();
      assert.equal(cost, "$25.40", "Cost displays the exact observation");

      // Usage changes window without briefly retaining a value from the old window.
      state.usageWindow = "24h";
      await page.getByRole("button", { name: /^(刷新状态|Refresh status)$/ }).click();
      const costCell = page.getByTestId("goal-vitals").locator(".goal-vital").nth(4);
      await costCell.locator(".goal-vital-value").filter({hasText: "$6.20"}).waitFor();
      assert.match(await costCell.innerText(), /24/);
      state.usageWindow = "unknown";
      await page.getByRole("button", { name: /^(刷新状态|Refresh status)$/ }).click();
      await costCell.locator(".goal-vital-value").filter({hasText: "—"}).waitFor();
      assert.doesNotMatch(await costCell.innerText(), /\$0|\$6|\$25/, "Unknown usage retains no measured cost");
      state.usageWindow = "7d";
      await page.getByRole("button", { name: /^(刷新状态|Refresh status)$/ }).click();
      await costCell.locator(".goal-vital-value").filter({hasText: "$25.40"}).waitFor();

      await page.emulateMedia({ reducedMotion: "reduce" });
      const liveAnimation = await page.locator(".goal-vital-dot.is-live").evaluate((node) => getComputedStyle(node, "::after").animationName);
      assert.equal(liveAnimation, "none", "Reduced motion stops the live arc; the ring and core still mark it live");
      await page.emulateMedia({ reducedMotion: null });

      state.deliveryFails = true;
      await page.locator(".delivery-review-toolbar button").first().click();
      const failure = overview.getByRole("alert").filter({ hasText: "刷新失败" });
      await failure.waitFor();
      // Resolve and measure the current error node in one browser action:
      // background readback can replace it between waitFor and boundingBox.
      const errorHeight = await failure.evaluate(node => node.getBoundingClientRect().height);
      assert.ok(errorHeight > 0 && errorHeight <= 28, `A region failure is one announced line, not a banner (${errorHeight}px)`);
      await page.screenshot({ path: resolve(outputDir, "goal-showcase-region-error.png"), animations: "disabled" });
      assert.match(await page.locator(".goal-overview-progress").innerText(), /当前保留旧快照/,
        "A failed owning read cannot present retained criteria as currently passing");
      state.deliveryFails = false;
      await page.locator(".delivery-review-toolbar button").first().click();
      await page.locator(".goal-overview-progress").filter({ hasText: "2/4 项条件当前通过" }).waitFor();
      await failure.waitFor({ state: "detached" });

      await page.setViewportSize({ width: 1024, height: 768 });
      await page.screenshot({ path: resolve(outputDir, "goal-showcase-1024.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(await progressIsReadable(page), "The phone criterion summary remains readable when details are folded");
      await page.screenshot({ path: resolve(outputDir, "goal-showcase-phone.png"), animations: "disabled" });
      const overflow = await page.getByTestId("goal-vitals").evaluate((strip) => strip.scrollWidth - strip.clientWidth);
      assert.ok(overflow <= 1, "The phone vitals fit without horizontal scrolling");
      await suggestions.click();
      const phoneMonitor = page.getByRole("button", { name: "配置定时检查" });
      await phoneMonitor.waitFor({ state: "visible" });
      assert.ok(await phoneMonitor.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return hit !== null && node.contains(hit);
      }), "The phone monitor shortcut is reachable without being clipped or covered");
      await page.keyboard.press("Escape");

      await page.setViewportSize({ width: 1440, height: 900 });
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
      if (!(await page.locator(".goal-overview").isVisible())) await openShowcaseGoal(page);
      await page.locator(".goal-work-lenses").getByRole("button", {name: "Map", exact: true}).click();
      await page.getByRole("region", {name:"Now",exact:true}).getByRole("button", {name:"Map",exact:true}).click();
      await page.locator(".work-map").waitFor({ timeout: 15_000 });
      await page.screenshot({ path: resolve(outputDir, "goal-showcase-en.png"), animations: "disabled" });
      // Focus belongs to the committed input, even before another frame runs.
      // Freeze at a future virtual instant; a sampled "now" can already be
      // in the past when the next browser command arrives.
      const clockStart = Date.UTC(2030, 0, 1);
      await page.clock.install({time: clockStart});
      await page.clock.pauseAt(clockStart + 60_000);
      await page.getByTestId("overview-composer-pill").click();
      const composer = page.getByRole("textbox", { name: /发送消息|Send a message/ });
      await composer.waitFor();
      assert.ok(await composer.evaluate((node) => node === document.activeElement), "Opening the folded composer focuses it");
      await page.clock.resume();
      await page.locator(".personal-goal-tabs button", { hasText: /^(任务|Tasks)$/ }).click();
      await page.locator(".personal-goal-tabs button", { hasText: /^(概览|Overview)$/ }).click();
      await page.getByTestId("overview-composer-pill").waitFor();

      assert.equal(session.errors.filter((error) => !/503/.test(error)).length, 0, session.errors.join(" | "));
      coverageEntries.push(...await session.close());
    } catch (error) {
      await page.screenshot({ path: resolve(outputDir, "goal-showcase-failed.png") });
      await session.close();
      throw error;
    }
    return { coverageEntries, note: "showcase Goal composition, exact values across usage windows, folded composer, quiet region failure" };
  },
};

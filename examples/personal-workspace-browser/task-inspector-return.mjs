import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const taskInspectorReturnScenario = {
  id: "task-inspector-return",
  async run({ browser, collectCoverage, url }) {
    const coverageEntries = [];
    for (const width of [1512, 390]) {
      const context = await openWorkspacePage(browser, url, {
        collectCoverage, viewport: { width, height: 844 },
      });
      const { page, api } = context;
      try {
        await page.emulateMedia({ reducedMotion: "reduce" });
        const menu = page.locator(".personal-mobile-menu");
        if (await menu.isVisible()) await menu.click();
        await page.locator(".personal-goal-link", { hasText: "Progress Projection" }).click();
        const card = page.locator(".personal-task-card", { hasText: "Current Todo" });
        const opener = card.locator(":scope > button");
        await opener.waitFor();
        assert.match(await opener.innerText(), /未分配/, "An unclaimed task must never inherit a Goal or execution owner");
        const drawer = page.getByRole("dialog", { name: "Todo 详情" });

        // WebKit on macOS can leave focus on the preceding control after a
        // pointer click. Preserve that condition independently of the browser
        // engine, so Chromium CI also exercises the installed App's failure.
        await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Tasks|任务)$/ }).focus();
        await opener.evaluate(element => element.addEventListener("mousedown", event => event.preventDefault()));
        await opener.click();
        await drawer.waitFor();
        await drawer.locator("dl > div", { has: page.getByText("Owner", { exact: true }) }).getByText("未分配", { exact: true }).waitFor();
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Current Todo"));
        assert.equal(await opener.evaluate(element => document.activeElement === element), true, "Escape must return to the pointer-opened Task");
        await page.keyboard.press("Enter");
        await drawer.waitFor();
        await drawer.getByRole("button", { name: /关闭详情/ }).click();
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Current Todo"));

        const more = card.locator(".personal-task-card-actions > button").last();
        await more.evaluate(element => element.addEventListener("mousedown", event => event.preventDefault()));
        await more.click();
        await drawer.waitFor();
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.classList.contains("personal-task-card-actions") || document.activeElement?.parentElement?.classList.contains("personal-task-card-actions"));
        assert.equal(await more.evaluate(element => document.activeElement === element), true, "The actions opener retains its own keyboard context");
        assert.equal(api.turnRequests.length, 0, "Inspecting Tasks never starts model work");
        assert.equal(api.actionApplies.length, 0, "Focus recovery never changes Todo authority");
        assert.equal(context.errors.length, 0, context.errors.join(" | "));
        await page.screenshot({ path: resolve(outputDir, `task-inspector-return-${width}.png`), animations: "disabled" });
      } finally {
        coverageEntries.push(...await context.close());
      }
    }
    return { coverageEntries, note: "Pointer-opened Task and actions restore their exact opener after Escape/close, and Enter reopens the same task on desktop/390px with reduced motion; no authority or model write." };
  },
};

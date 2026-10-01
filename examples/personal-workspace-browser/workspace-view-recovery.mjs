import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const workspaceViewRecoveryScenario = {
  id: "workspace-view-recovery",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { page, api } = context;
    const managerTabs = page.getByRole("navigation", { name: "管家视图" });
    const goalTabs = page.getByRole("navigation", { name: "Goal 视图" });
    const active = async (tabs, name) => {
      const button = tabs.getByRole("button", { name, exact: true });
      try { await button.and(page.locator('[aria-current="page"]')).waitFor({ state: "visible", timeout: 6000 }); }
      catch (error) { throw new Error(`View ${name} not restored at ${page.url()}; ${await tabs.innerText()}`, { cause: error }); }
      return button.getAttribute("aria-current");
    };
    try {
      assert.equal(await active(managerTabs, "总览"), "page", "A fresh entry keeps the existing overview");
      await managerTabs.getByRole("button", { name: "对话", exact: true }).click();
      const draft = "接着刚才的研究，先只看微软。";
      await page.getByLabel("向 LoopX 发送消息").fill(draft);
      await page.reload({ waitUntil: "networkidle" });
      assert.equal(await active(managerTabs, "对话"), "page", "Reload must retain the chosen conversation");
      assert.equal(await page.getByLabel("向 LoopX 发送消息").inputValue(), draft);
      await page.screenshot({ path: resolve(outputDir, "workspace-view-recovery-chat.png") });
      const conversationUrl = page.url();
      await managerTabs.getByRole("button", { name: "总览", exact: true }).click();
      await page.waitForURL(value => value.href !== conversationUrl);
      await page.goBack({ waitUntil: "networkidle" });
      assert.equal(await active(managerTabs, "对话"), "page", "Back returns to the conversation");
      await page.goForward({ waitUntil: "networkidle" });
      assert.equal(await active(managerTabs, "总览"), "page", "Forward restores the overview");

      await page.locator(".personal-goal-link").filter({ hasText: "Product Release" }).click();
      assert.equal(await active(goalTabs, "任务"), "page", "Selecting a Goal keeps the existing task-first entry");
      await goalTabs.getByRole("button", { name: "成果", exact: true }).click();
      await page.reload({ waitUntil: "networkidle" });
      assert.equal(await active(goalTabs, "成果"), "page", "The same route owner restores Goal views");
      await goalTabs.getByRole("button", { name: "对话", exact: true }).click();
      await page.reload({ waitUntil: "networkidle" });
      assert.equal(await active(goalTabs, "对话"), "page");
      await page.goBack({ waitUntil: "networkidle" });
      assert.equal(await active(goalTabs, "成果"), "page", "Back restores the Goal's prior view");
      assert.equal(new URL(page.url()).searchParams.get("goalId"), "product-release");
      assert.equal(new URL(page.url()).searchParams.get("statusUrl"), "/status.json");
      assert.equal(api.turnRequests.length, 0, "Navigation and reload never start or replay model work");
      assert.equal(api.actionApplies.length, 0, "Navigation does not change owning Goal state");
      await page.setViewportSize({ width: 390, height: 844 });
      await goalTabs.getByRole("button", { name: "对话", exact: true }).focus();
      await page.keyboard.press("Enter");
      await page.reload({ waitUntil: "networkidle" });
      assert.equal(await active(goalTabs, "对话"), "page", "Narrow keyboard navigation also survives reload");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      await page.screenshot({ path: resolve(outputDir, "workspace-view-recovery-narrow.png") });
      // A route without an explicit view retains its established default,
      // even after navigating back from a different controlled view.
      const freshUrl = new URL(url);
      await page.goto(freshUrl.href, { waitUntil: "networkidle" });
      await managerTabs.getByRole("button", { name: "对话", exact: true }).click();
      await page.goBack({ waitUntil: "networkidle" });
      assert.equal(await active(managerTabs, "总览"), "page");
      assert.equal(api.turnRequests.length, 0);
      assert.equal(api.actionApplies.length, 0);
      return { coverageEntries: context.coverageEntries, note: "Chosen manager/Goal views and composer draft survive reload and browser history; no Turn or Goal write is issued." };
    } finally { await context.close(); }
  },
};

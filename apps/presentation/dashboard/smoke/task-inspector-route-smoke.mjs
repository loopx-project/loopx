// Packaged frontend routing; authority and model writes remain in the fixture.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { launchBrowser, loadPlaywright, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
const { outputDir, port, startServer } = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const { openWorkspacePage } = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
let server, browser;
try {
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  await mkdir(outputDir, { recursive: true });
  for (const width of [1512, 390]) {
    const workspace = await openWorkspacePage({ newPage: options => browser.newPage({ locale: "zh-CN", ...options }) }, url, { viewport: { width, height: 844 } });
    const { page, api } = workspace;
    try {
      await page.emulateMedia({ reducedMotion: "reduce" });
      const menu = page.locator(".personal-mobile-menu");
      if (await menu.isVisible()) await menu.click();
      await page.locator(".personal-goal-link", { hasText: "Progress Projection" }).click();
      const activeTab = page.locator('.personal-goal-tabs button[aria-current="page"]');
      await activeTab.filter({ hasText: "概览" }).waitFor();
      const goalLink = new URL(page.url());
      goalLink.searchParams.delete("view");
      await page.goto(goalLink.href, { waitUntil: "networkidle" });
      await activeTab.filter({ hasText: "概览" }).waitFor();
      await page.reload({ waitUntil: "networkidle" });
      await activeTab.filter({ hasText: "概览" }).waitFor();
      await page.screenshot({ path: resolve(outputDir, `goal-default-overview-${width}.png`), animations: "disabled" });
      for (const [view, label] of [["conversation", "对话"], ["files", "成果"], ["tasks", "任务"]]) {
        const explicitLink = new URL(goalLink);
        explicitLink.searchParams.set("view", view);
        await page.goto(explicitLink.href, { waitUntil: "networkidle" });
        await activeTab.filter({ hasText: label }).waitFor();
      }
      const opener = page.locator(".personal-task-card", { hasText: "Current Todo" }).locator(":scope > button");
      const drawer = page.getByRole("dialog", { name: "Todo 详情" });
      const scope = () => {
        const search = new URL(page.url()).searchParams;
        return [search.get("goalId"), search.get("statusUrl"), search.get("view")];
      };
      await opener.waitFor();
      const initialScope = scope();
      await opener.focus();
      await page.keyboard.press("Enter");
      await drawer.waitFor();
      await page.waitForURL(value => value.searchParams.get("todo") === "todo-progress-current");
      const deepLink = page.url();
      await page.keyboard.press("Escape");
      await drawer.waitFor({ state: "hidden" });
      await page.waitForURL(value => !value.searchParams.has("todo"), { timeout: 3000 });
      assert.deepEqual(scope(), initialScope, "Closing a Task preserves the selected Goal, source and view");
      await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Current Todo"));
      assert.equal(await opener.evaluate(element => document.activeElement === element), true);
      await page.reload({ waitUntil: "networkidle" });
      await opener.waitFor();
      assert.equal(await drawer.count(), 0, "Reload cannot resurrect a dismissed Task");
      assert.deepEqual(scope(), initialScope);

      await opener.click();
      await drawer.waitFor();
      await page.waitForURL(value => value.searchParams.get("todo") === "todo-progress-current");
      await drawer.getByRole("button", { name: /关闭详情/ }).click();
      await page.waitForURL(value => !value.searchParams.has("todo"));
      await drawer.waitFor({ state: "hidden" });
      await page.goBack();
      await page.waitForURL(value => value.searchParams.get("todo") === "todo-progress-current");
      await drawer.waitFor();
      await page.goForward();
      await page.waitForURL(value => !value.searchParams.has("todo"));
      await drawer.waitFor({ state: "hidden" });
      await page.goto(deepLink, { waitUntil: "networkidle" });
      await drawer.waitFor();
      await page.reload({ waitUntil: "networkidle" });
      await drawer.waitFor();
      assert.equal(new URL(page.url()).searchParams.get("todo"), "todo-progress-current", "A deliberate deep link remains restorable");
      await drawer.getByRole("button", { name: /关闭详情/ }).click();
      await page.waitForURL(value => !value.searchParams.has("todo"));
      await page.reload({ waitUntil: "networkidle" });
      await opener.waitFor();
      assert.equal(await drawer.count(), 0);
      const unavailable = new URL(deepLink);
      unavailable.searchParams.set("todo", "todo-missing-route");
      await page.goto(unavailable.href, { waitUntil: "networkidle" });
      await drawer.getByRole("button", { name: "重试读取完整要求" }).waitFor();
      await page.keyboard.press("Escape");
      await page.waitForURL(value => !value.searchParams.has("todo"));
      await page.reload({ waitUntil: "networkidle" });
      await opener.waitFor();
      assert.equal(await drawer.count(), 0, "An unavailable deep link can be dismissed without a recovery loop");
      assert.deepEqual(scope(), initialScope);
      assert.equal(api.turnRequests.length, 0, "Reading and returning cannot launch model work");
      assert.equal(api.actionApplies.length, 0, "Closing a Task cannot mutate its authority");
      assert.equal(workspace.errors.length, 0, workspace.errors.join(" | "));
      await page.screenshot({ path: resolve(outputDir, `task-inspector-route-${width}.png`), animations: "disabled" });
    } finally {
      await workspace.close();
    }
  }
  console.log("task-inspector-route: passed (packaged desktop/390px, default overview and explicit view links, keyboard/reduced motion, close/reload, Back/Forward, deliberate and unavailable deep links, no model or authority writes)");
} finally {
  await browser?.close();
  server?.kill("SIGTERM");
}

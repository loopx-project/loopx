// Packaged frontend routing; authority and model writes remain in the fixture.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { launchBrowser, loadPlaywright, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
const { outputDir, port, startServer } = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const { openWorkspacePage } = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
const viewports = [
  { height: 844, width: 1512 },
  { height: 768, width: 1024 },
  { height: 1024, width: 768 },
  { height: 844, width: 390 },
  { height: 844, width: 320 },
];
let server, browser;
try {
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  await mkdir(outputDir, { recursive: true });
  for (const { height, width } of viewports) {
    const workspace = await openWorkspacePage({ newPage: options => browser.newPage({ locale: "zh-CN", ...options }) }, url, { viewport: { width, height } });
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
      const overlayExpected = width <= 1050;
      const scope = () => {
        const search = new URL(page.url()).searchParams;
        return [search.get("goalId"), search.get("statusUrl"), search.get("view")];
      };
      const assertModality = async (modal) => {
        await page.waitForFunction(({ modal }) => {
          const contextDrawer = document.querySelector('[data-context-drawer]');
          const contextDialog = document.querySelector('.personal-context-drawer[data-context-kind="todo"]');
          const workspaceMain = document.querySelector(".personal-workspace-main");
          if (!contextDrawer || !contextDialog || !workspaceMain) return false;
          return (contextDialog.getAttribute("aria-modal") === "true") === modal
            && (workspaceMain.getAttribute("aria-hidden") === "true") === modal
            && workspaceMain.inert === modal
            && (getComputedStyle(contextDrawer).position === "fixed") === modal
            && (getComputedStyle(document.documentElement).overflow === "hidden") === modal
            && (getComputedStyle(document.body).overflow === "hidden") === modal;
        }, { modal });
      };
      await opener.waitFor();
      const initialScope = scope();
      await opener.focus();
      await page.keyboard.press("Enter");
      await drawer.waitFor();
      await page.waitForURL(value => value.searchParams.get("todo") === "todo-progress-current");
      const deepLink = page.url();
      await assertModality(overlayExpected);
      const backgroundControl = page.locator('.personal-goal-tabs button[aria-current="page"]');
      assert.equal(
        await backgroundControl.evaluate(element => {
          element.focus();
          return document.activeElement === element;
        }),
        !overlayExpected,
        overlayExpected
          ? "A narrow Task inspector must make the background unavailable to focus"
          : "A wide Task inspector remains non-modal",
      );
      if (!overlayExpected) {
        await page.setViewportSize({ height: 844, width: 1024 });
        await assertModality(true);
        await page.waitForFunction(() => document.querySelector(".personal-context-drawer")?.contains(document.activeElement));
        await page.setViewportSize({ height, width });
        await assertModality(false);
      }
      if (overlayExpected) {
        await drawer.locator("#personal-drawer-title").focus();
        for (const key of ["Shift+Tab", "Tab", "Tab", "Shift+Tab"]) {
          await page.keyboard.press(key);
          assert.equal(
            await drawer.evaluate(element => element.contains(document.activeElement)),
            true,
            `${key} must remain inside the narrow Task inspector`,
          );
        }
      }
      if (width === 1024) {
        await drawer.locator(".personal-inspector-size").click();
        await assertModality(true);
        assert.equal(await page.locator("[data-context-drawer]").getAttribute("data-drawer-mode"), "inspector-full");
        await drawer.locator(".personal-inspector-size").click();
        await assertModality(true);
        assert.equal(await page.locator("[data-context-drawer]").getAttribute("data-drawer-mode"), "inspector");
      }
      await page.screenshot({ path: resolve(outputDir, `task-inspector-modal-${width}.png`), animations: "disabled" });

      const management = drawer.locator(".personal-task-management");
      await management.locator(":scope > summary").click();
      assert.notEqual(await management.getAttribute("open"), null, "Manage task opens as the nested top layer");
      if (width === 390) {
        await page.screenshot({ path: resolve(outputDir, "task-inspector-menu-390.png"), animations: "disabled" });
      }
      await page.keyboard.press("Escape");
      await drawer.waitFor();
      assert.equal(await management.getAttribute("open"), null, "Escape closes the nested menu before the inspector");
      assert.equal(
        await management.locator(":scope > summary").evaluate(element => document.activeElement === element),
        true,
        "Closing the nested menu restores its summary focus",
      );
      if (!overlayExpected) {
        await page.evaluate(() => {
          const dialog = document.createElement("dialog");
          dialog.dataset.taskInspectorTopLayer = "true";
          const button = document.createElement("button");
          button.textContent = "Nested dialog";
          dialog.append(button);
          document.body.append(dialog);
          dialog.showModal();
          button.focus();
        });
        await page.keyboard.press("Escape");
        await page.waitForFunction(() => document.querySelector("[data-task-inspector-top-layer]")?.open === false);
        assert.equal(await drawer.isVisible(), true, "Escape closes a topmost dialog before the Task inspector");
        await page.locator("[data-task-inspector-top-layer]").evaluate(element => element.remove());
      }

      const proof = drawer.locator(".personal-task-completion-proof");
      await proof.locator(":scope > summary").click();
      const proofField = proof.getByRole("textbox", { name: "原执行证明（可选）" });
      const draft = `draft survives resize at ${width}`;
      await proofField.fill(draft);
      const resizedWidth = overlayExpected ? 1512 : 1024;
      await page.setViewportSize({ height: 844, width: resizedWidth });
      await assertModality(!overlayExpected);
      assert.equal(await drawer.isVisible(), true, "Crossing the inspector breakpoint keeps the selected Task");
      assert.equal(new URL(page.url()).searchParams.get("todo"), "todo-progress-current");
      assert.equal(await proofField.inputValue(), draft, "Crossing the inspector breakpoint preserves the draft");
      await page.setViewportSize({ height, width });
      await assertModality(overlayExpected);
      assert.equal(await proofField.inputValue(), draft, "Returning across the inspector breakpoint preserves the draft");

      await page.keyboard.press("Escape");
      await drawer.waitFor({ state: "hidden" });
      await page.waitForURL(value => !value.searchParams.has("todo"), { timeout: 3000 });
      assert.deepEqual(scope(), initialScope, "Closing a Task preserves the selected Goal, source and view");
      await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Current Todo"));
      assert.equal(await opener.evaluate(element => document.activeElement === element), true);
      await page.waitForFunction(() => {
        const workspaceMain = document.querySelector(".personal-workspace-main");
        return !workspaceMain?.inert
          && getComputedStyle(document.documentElement).overflow !== "hidden"
          && getComputedStyle(document.body).overflow !== "hidden";
      });
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
      if (width === 1512) {
        await opener.click();
        await drawer.waitFor();
        await page.evaluate(() => {
          const dialog = document.createElement("dialog");
          dialog.dataset.taskInspectorModelessDialog = "true";
          const button = document.createElement("button");
          button.textContent = "Modeless dialog";
          dialog.append(button);
          document.body.append(dialog);
          dialog.show();
          button.focus();
        });
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        assert.equal(
          await page.locator("[data-task-inspector-modeless-dialog]").evaluate(element => element.open),
          true,
          "A modeless dialog does not consume Escape from the Task inspector",
        );
        await page.locator("[data-task-inspector-modeless-dialog]").evaluate(element => element.remove());
      }
      if (width === 390) {
        await opener.click();
        await drawer.waitFor();
        await opener.evaluate(element => element.remove());
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.classList.contains("personal-task-board"));
        assert.equal(
          await page.locator(".personal-task-board").evaluate(element => document.activeElement === element),
          true,
          "A removed Task opener falls back to the Task list instead of another Task",
        );
      }
      assert.equal(api.turnRequests.length, 0, "Reading and returning cannot launch model work");
      assert.equal(api.actionApplies.length, 0, "Closing a Task cannot mutate its authority");
      assert.equal(workspace.errors.length, 0, workspace.errors.join(" | "));
      await page.screenshot({ path: resolve(outputDir, `task-inspector-route-${width}.png`), animations: "disabled" });
    } finally {
      await workspace.close();
    }
  }
  console.log("task-inspector-route: passed (packaged 1512/1024/768/390/320px, responsive modal/focus/background/draft behavior, nested Escape, opener-loss fallback, routing, no model or authority writes)");
} finally {
  await browser?.close();
  server?.kill("SIGTERM");
}

import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const privateStewardScopeScenario = {
  id: "private-steward-scope",
  async run({browser, collectCoverage, url}) {
    const writes = [];
    const row = {binding_id: "synthetic-steward", app_ref: "mew", context_kind: "steward",
      project_ref: "synthetic-workspace", project_title: "Personal workspace", context_available: true,
      executor_endpoint_id: "codex", grant: "portfolio_read", goal_count: 1, goal_scope: "selected",
      listener_status: "listening", pending_count: 0, recovery_count: 0};
    const context = await openWorkspacePage(browser, url, {collectCoverage,
      beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/projects", route => route.fulfill({json: {ok: true,
          projects: [{project_ref: row.project_ref, title: row.project_title, grant: "workspace_write"}]}}));
        await page.route("**/api/chat/lark/private-conversations", async route => {
          if (route.request().method() === "POST") {
            const body = route.request().postDataJSON();
            if (body.app_ref !== row.app_ref || body.project_ref !== row.project_ref
                || body.context_kind !== "steward" || body.goal_scope !== "all_registered") {
              throw new Error("Scope upgrade did not retain its target and select all registered work");
            }
            writes.push(body);
            if (writes.length === 1) {
              await route.fulfill({status: 500, json: {ok: false, error: "Scope publication failed; retry the upgrade."}});
              return;
            }
            Object.assign(row, {goal_scope: "all_registered", goal_count: 150});
          }
          await route.fulfill({json: {ok: true, revision: writes.length + 1, connections: [row]}});
        });
      },
    });
    const {page} = context;
    try {
      async function settings() {
        await page.getByRole("button", {name: "设置", exact: true}).click();
        await page.locator(".personal-settings-tabs").getByRole("button", {name: "Lark", exact: true}).click();
      }
      await settings();
      const panel = page.getByRole("region", {name: "本人飞书私聊"});
      await panel.getByText("LoopX 管家 · 已选范围 · 1 个 Goal", {exact: true}).waitFor();
      const upgrade = panel.getByRole("button", {name: "授权全部已注册工作", exact: true});
      await upgrade.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "private-steward-scope-before.png"), animations: "disabled"});
      await upgrade.focus();
      await page.keyboard.press("Enter");
      await panel.getByRole("alert").filter({hasText: "Scope publication failed; retry the upgrade."}).waitFor();
      await panel.getByText("LoopX 管家 · 已选范围 · 1 个 Goal", {exact: true}).waitFor();
      if (await upgrade.isDisabled()) throw new Error("Failed upgrade must allow an explicit retry");
      await upgrade.focus();
      await page.keyboard.press("Enter");
      await panel.getByText("LoopX 管家 · 全部已注册工作 · 150 个 Goal", {exact: true}).waitFor();
      if (writes.length !== 2 || await upgrade.count() || await panel.getByRole("alert").count()) {
        throw new Error("Retry must read back the upgraded scope and clear the failure");
      }
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "private-steward-scope-after.png"), animations: "disabled"});
      await page.reload();
      await page.getByTestId("personal-goal-home").waitFor();
      await settings();
      await panel.getByText("LoopX 管家 · 全部已注册工作 · 150 个 Goal", {exact: true}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "private-steward-scope-mobile.png"), animations: "disabled"});
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("Scope readback overflows");
      const expectedErrors = context.errors.filter(error => error === "Failed to load resource: the server responded with a status of 500 (Internal Server Error)");
      if (expectedErrors.length !== 1 || context.errors.length !== 1) throw new Error(context.errors.join(" | "));
      return {coverageEntries: await context.close(), note: "Failed scope publication retains selected scope and allows keyboard retry; success clears the failure and survives reload; desktop/mobile, synthetic API paired with native backend fault regressions"};
    } catch (error) {await context.close(); throw error;}
  },
};

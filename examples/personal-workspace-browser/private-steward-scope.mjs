import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const privateStewardScopeScenario = {
  id: "private-steward-scope",
  async run({browser, collectCoverage, url}) {
    const writes = [];
    let projectsAvailable = true, hasConnection = true, extraRows = [];
    const row = {binding_id: "synthetic-steward", app_ref: "mew", context_kind: "steward",
      project_ref: "synthetic-workspace", project_title: "Personal workspace", context_available: true,
      executor_endpoint_id: "codex", grant: "portfolio_read", goal_count: 1, goal_scope: "selected",
      listener_status: "listening", pending_count: 0, recovery_count: 0};
    const context = await openWorkspacePage(browser, url, {collectCoverage,
      beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/projects", route => route.fulfill({json: projectsAvailable ? {ok: true,
          projects: [{project_ref: row.project_ref, title: row.project_title, grant: "workspace_write"}]} : {ok: true}}));
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
          await route.fulfill({json: {ok: true, revision: writes.length + 1, connections: hasConnection ? [row, ...extraRows] : []}});
        });
      },
    });
    const {page} = context;
    try {
      async function settings() {
        // On phone widths Goal navigation is a closed drawer; Settings lives inside it.
        const navigation = page.locator(".personal-mobile-menu");
        if (await navigation.isVisible()) await navigation.click();
        await page.getByRole("button", {name: "设置", exact: true}).click();
        await page.locator(".personal-settings-tabs").getByRole("button", {name: "Lark", exact: true}).click();
      }
      async function reloadSettings() {
        await page.reload();
        await page.locator(".personal-settings-page").getByRole("heading", {name: "Lark", exact: true}).waitFor();
      }
      await settings();
      const panel = page.getByRole("region", {name: "飞书项目助手与管家"});
      await panel.getByText("LoopX 管家 · 已选范围 · 1 个 Goal", {exact: true}).waitFor();
      await page.screenshot({path: resolve(outputDir, "lark-connections-default-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await page.screenshot({path: resolve(outputDir, "lark-connections-default-narrow.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      const setup = panel.locator(".personal-private-setup");
      const appSelect = panel.getByRole("combobox", {name: "助手 App", exact: true});
      const commands = panel.getByRole("button", {name: "聊天与委托命令", exact: true});
      const commandHelp = panel.getByRole("tooltip").filter({hasText: "聊天与委托命令"});
      if (await appSelect.isVisible() || await commandHelp.isVisible()) throw new Error("Existing connections are buried under optional setup or command instructions");
      await commands.focus();
      await commandHelp.getByText(/后续消息进入原会话队列/u).waitFor();
      await commandHelp.getByText(/15 分钟过期/u).waitFor();
      await page.screenshot({path: resolve(outputDir, "lark-conversation-help-desktop.png"), animations: "disabled"});
      await page.keyboard.press("Escape");
      if (!await commands.evaluate(node => node === document.activeElement) || await commandHelp.isVisible() || writes.length) throw new Error("Closing command help lost focus or performed a write");
      await setup.locator("summary").focus();
      await page.keyboard.press("Enter");
      await appSelect.waitFor();
      const audienceSelect = panel.getByRole("combobox", {name: "使用位置", exact: true});
      const appBounds = await appSelect.boundingBox(), audienceBounds = await audienceSelect.boundingBox();
      if (!appBounds || !audienceBounds || Math.abs(appBounds.y - audienceBounds.y) > 2) throw new Error("Desktop setup did not group related fields into columns");
      await panel.getByRole("status").filter({hasText: "更改权限会建立新绑定和新会话"}).waitFor();
      await page.screenshot({path: resolve(outputDir, "lark-conversation-setup-desktop.png"), fullPage: true, animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await appSelect.scrollIntoViewIfNeeded();
      const narrowApp = await appSelect.boundingBox(), narrowAudience = await audienceSelect.boundingBox();
      if (!narrowApp || !narrowAudience || narrowAudience.y <= narrowApp.y || await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("Narrow setup did not remain in a readable single column");
      await panel.getByRole("status").filter({hasText: "更改权限会建立新绑定和新会话"}).scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "lark-conversation-setup-narrow.png"), fullPage: true, animations: "disabled"});
      await commands.click();
      await commandHelp.getByText(/恢复保留原线程及累计用量/u).waitFor();
      await page.screenshot({path: resolve(outputDir, "lark-conversation-help-narrow.png"), animations: "disabled"});
      await page.keyboard.press("Escape");
      await setup.locator("summary").click();
      await page.setViewportSize({width: 1512, height: 982});
      const upgrade = panel.getByRole("button", {name: "授权全部已注册工作", exact: true});
      await upgrade.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "private-steward-scope-before.png"), animations: "disabled"});
      await upgrade.focus();
      await page.keyboard.press("Enter");
      await panel.getByRole("alert").filter({hasText: "Scope publication failed; retry the upgrade."}).waitFor();
      await panel.getByText("LoopX 管家 · 已选范围 · 1 个 Goal", {exact: true}).waitFor();
      if (await upgrade.isDisabled()) throw new Error("Failed upgrade must allow an explicit retry");
      await panel.getByRole("button", {name: "刷新状态", exact: true}).click();
      await panel.getByText("LoopX 管家 · 已选范围 · 1 个 Goal", {exact: true}).waitFor();
      if (await appSelect.isVisible() || writes.length !== 1) throw new Error("Refreshing a failed operation requires hidden setup or replays the write");
      await upgrade.click({trial: true});
      await upgrade.focus();
      await page.keyboard.press("Enter");
      await panel.getByText("LoopX 管家 · 全部已注册工作 · 150 个 Goal", {exact: true}).waitFor();
      if (writes.length !== 2 || await upgrade.count() || await panel.getByRole("alert").count()) {
        throw new Error("Retry must read back the upgraded scope and clear the failure");
      }
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "private-steward-scope-after.png"), animations: "disabled"});
      await reloadSettings();
      await panel.getByText("LoopX 管家 · 全部已注册工作 · 150 个 Goal", {exact: true}).waitFor();
      // The same populated settings journey must distinguish a ready stream
      // from source coverage. Neither a gap nor failed observation is empty.
      row.ingress = {status: "gap", last_checked_at: 1000, last_complete_at: 995,
        source_gap_count: 1, without_reply_count: 1};
      await reloadSettings();
      await panel.getByRole("alert").filter({hasText: "1 条原消息缺少收件记录"}).waitFor();
      await panel.getByText("待处理或回复：0", {exact: true}).waitFor();
      await page.screenshot({path: resolve(outputDir, "private-ingress-gap-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "private-steward-scope-mobile.png"), animations: "disabled"});
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("Scope readback overflows");
      row.ingress.status = "unavailable";
      await reloadSettings();
      await panel.getByRole("alert").filter({hasText: "原消息核对暂不可用"}).waitFor();
      await page.screenshot({path: resolve(outputDir, "private-ingress-unavailable-mobile.png"), animations: "disabled"});
      row.ingress = {...row.ingress, status: "verified", source_gap_count: 0, without_reply_count: 0};
      await reloadSettings();
      await panel.getByRole("status").filter({hasText: "原消息已核对"}).waitFor();
      if (await panel.getByRole("alert").count() || writes.length !== 2) throw new Error("Source readback must clear stale warnings without creating another write");
      // An old or malformed workspace catalog cannot erase an independently
      // verified existing connection or crash the rest of Lark settings.
      projectsAvailable = false;
      await reloadSettings();
      await panel.getByRole("alert").filter({hasText: "The workspace list could not be read"}).waitFor();
      await panel.getByText("LoopX 管家 · 全部已注册工作 · 150 个 Goal", {exact: true}).waitFor();
      if (!await panel.getByRole("button", {name: "继续原会话", exact: true}).isEnabled()) {
        throw new Error("A workspace catalog failure disabled a verified original conversation");
      }
      await page.getByRole("button", {name: "连接 Lark App", exact: true}).waitFor();
      if (writes.length !== 2) throw new Error("Failed catalog reads created a connection write");
      projectsAvailable = true;
      await reloadSettings();
      await panel.getByRole("option", {name: "Personal workspace", exact: true}).waitFor({state: "attached"});
      if (await panel.getByRole("alert").count()) throw new Error("Recovered workspace catalog retained a stale failure");
      // Long titles and multiple connections expose composition failures that
      // a quiet single-row fixture cannot. Readback never retries their work.
      row.owner_attention = {available: true, enabled: true, revision: 1, last_checked_at: Date.now() / 1000,
        error_code: "source_incomplete", source_coverage: {goal_count: 150, read_goal_count: 147, unavailable_goal_count: 3},
        pending_count: 2, unconfirmed_count: 1, failed_count: 0};
      extraRows = [{...row, binding_id: "synthetic-long-steward", app_ref: "synthetic-other-app",
        project_title: "长期研究工作区 · Extended research workspace · 原始证据与跨会话接续记录 ".repeat(4),
        context_available: true, pending_count: 3, recovery_count: 1, listener_status: "retrying"},
        {...row, binding_id: "synthetic-unavailable-steward", app_ref: "synthetic-unavailable-app",
          context_available: false, owner_attention: {available: false, error_code: "context_unavailable"}}];
      await page.setViewportSize({width: 1512, height: 982});
      await reloadSettings();
      await panel.getByText("3 个 Goal 暂时不可读；其余 147 个继续自动检查。", {exact: true}).first().waitFor();
      await panel.getByRole("alert").filter({hasText: "工作区授权已失效"}).waitFor();
      await page.screenshot({path: resolve(outputDir, "lark-conversation-mixed-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await panel.getByText(`${extraRows[0].app_ref} · ${extraRows[0].project_title}`, {exact: true}).scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "lark-conversation-mixed-narrow.png"), animations: "disabled"});
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth) || writes.length !== 2 || await appSelect.isVisible()) throw new Error("Long mixed connections overflowed, replayed a write, or expanded optional setup");
      hasConnection = false;
      await reloadSettings();
      await panel.getByText("尚未连接助手。", {exact: true}).waitFor();
      await appSelect.waitFor();
      if (!await setup.evaluate(node => node.open) || writes.length !== 2) throw new Error("An empty connection list must expose setup without creating a connection");
      await page.screenshot({path: resolve(outputDir, "lark-conversation-empty-narrow.png"), fullPage: true, animations: "disabled"});
      const expectedErrors = context.errors.filter(error => error === "Failed to load resource: the server responded with a status of 500 (Internal Server Error)");
      if (expectedErrors.length !== 1 || context.errors.length !== 1) throw new Error(context.errors.join(" | "));
      return {coverageEntries: await context.close(), note: "Failed scope publication retains selected scope and allows keyboard retry; success clears the failure and survives reload; existing connections remain primary, setup is keyboard-disclosed and opens for an empty list, full command help works with focus/tap/Escape; desktop/mobile, synthetic API paired with native backend fault regressions"};
    } catch (error) {await context.close(); throw error;}
  },
};

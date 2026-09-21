import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const loopxModeScenario = {
  id: "loopx-mode",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { api, page } = context;
    let releaseSnapshot;
    const heldSnapshot = new Promise(resolve => { releaseSnapshot = resolve; });
    let firstSnapshot = true;
    await page.route("**/api/chat/sessions/*/loopx", async route => {
      if (firstSnapshot && route.request().method() === "GET") {
        firstSnapshot = false;
        await heldSnapshot;
      }
      await route.fallback();
    });
    const snapshotRequested = page.waitForRequest(request => request.url().endsWith("/loopx"));
    try {
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" })
        .getByRole("button", { name: "对话", exact: true }).click();

      const enable = page.getByRole("button", { name: "开启 LoopX 模式", exact: true });
      await enable.waitFor({ state: "visible" });
      await snapshotRequested;
      await enable.click();
      releaseSnapshot();

      const settings = page.locator(".goal-loopx-mode-settings");
      await settings.waitFor({ state: "visible" });
      await settings.getByLabel("已注册的协调身份").selectOption("lead");
      await settings.getByLabel("协调员总 token 额度").fill("100000");
      const executionConfig = settings.getByLabel("成员执行绑定文件（Goal 配置）");
      if (await executionConfig.inputValue() !== ".loopx/config/delegations.json") {
        throw new Error("LoopX mode did not read the Goal-owned execution configuration");
      }
      if (await executionConfig.isEditable()) {
        throw new Error("LoopX mode must not edit the Goal-owned execution configuration");
      }
      await settings.getByRole("button", { name: "保存设置", exact: true }).click();
      await settings.waitFor({ state: "detached" });

      const request = api.loopxModeRequests.findLast(row => row.operation === "configure");
      if (request?.operation !== "configure"
        || request.settings?.agent_id !== "lead"
        || request.settings?.token_budget !== 100000
        || Object.hasOwn(request.settings ?? {}, "execution_config")) {
        throw new Error(`LoopX mode settings did not round-trip through the real frontend API: ${JSON.stringify(request)}`);
      }
      if (api.turnRequests.length) throw new Error("Configuring LoopX mode started work without explicit activation");
      await page.getByText("普通对话", { exact: true }).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "goal-loopx-mode-configured.png"), fullPage: false, animations: "disabled" });

      if (await page.locator(".personal-composer-tools").getAttribute("open") !== null) throw new Error("Suggestions displaced the default conversation");
      if (await page.locator(".personal-run-row").count()) throw new Error("An idle conversation was presented as waiting execution");
      const composer = page.getByLabel("向 LoopX 发送消息");
      const composerBox = await composer.boundingBox();
      if (!composerBox || composerBox.width < 300 || composerBox.y + composerBox.height > 982) throw new Error("Composer lost usable viewport space");
      const scrollBefore = await page.locator(".personal-channel-scroll").evaluate(el => el.scrollTop);
      if (api.loopxModeRequests.filter(row => row.operation === "operations").length !== 1) throw new Error("Configured results must load once, not on ordinary polling");
      if (api.loopxModeRequests.some(row => row.operation === "read" || row.operation === "inspect")) throw new Error("Result inventory read artifact bodies or ran preflight without selection");
      await page.getByRole("button", {name: "团队执行情况", exact: true}).click();
      const team = page.getByRole("region", {name: "团队执行详情"});
      await team.getByText("local-analyst · 已通过当前验收", {exact: true}).waitFor();
      await team.getByText("本页有无法核验的工作，请检查原请求；不要直接重新派工。", {exact: true}).waitFor();
      await team.getByRole("button", {name: "检查启动条件", exact: true}).click();
      await team.getByText(/运行时可用性尚未验证/).waitFor();
      await page.screenshot({path: resolve(outputDir, "goal-team-execution-desktop.png"), fullPage: false, animations: "disabled"});
      await team.getByRole("button", {name: "下一页", exact: true}).click();
      await team.getByText("cloud-reviewer · 需要恢复原执行", {exact: true}).waitFor();
      if (await team.getByText("cloud-reviewer · 执行中", {exact: true}).count()) throw new Error("Stopped worker was labeled executing");
      await page.setViewportSize({width: 390, height: 844});
      await page.screenshot({path: resolve(outputDir, "goal-team-execution-mobile.png"), fullPage: false, animations: "disabled"});
      if (api.turnRequests.length) throw new Error("Inspecting the team started a model turn");
      const dialog = page.getByRole("dialog", {name: "团队执行情况"});
      const dialogBox = await dialog.boundingBox();
      if (!dialogBox || dialogBox.x < 15 || Math.abs(dialogBox.x + dialogBox.width / 2 - 195) > 1 || dialogBox.y + dialogBox.height > 844) throw new Error("Team dialog lost its centered, bounded mobile layout");
      await page.keyboard.press("Escape");
      await dialog.waitFor({state: "hidden"});
      if (!await page.locator(".goal-loopx-team-trigger").evaluate(el => el === document.activeElement)) throw new Error("Closing team details lost keyboard focus");
      if (Math.abs(await page.locator(".personal-channel-scroll").evaluate(el => el.scrollTop) - scrollBefore) > 1) throw new Error("Team details changed conversation scroll");
      const mobileComposer = await composer.boundingBox();
      if (!mobileComposer || mobileComposer.width < 200 || mobileComposer.y + mobileComposer.height > 844) throw new Error("Mobile composer is not usable");
      await page.screenshot({path: resolve(outputDir, "goal-conversation-mobile.png"), fullPage: false, animations: "disabled"});

      const mode = page.__loopxRuntime.loopxModes.get(request.sessionId);
      Object.assign(mode, {enabled: true, active_turn_id: "fixture-loopx-turn", native: {status: "active", tokensUsed: 120, tokenBudget: 100000}});
      await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
      await page.getByLabel("消息处理方式").selectOption("steer");
      await page.getByRole("button", {name: "暂停协调员", exact: true}).click();
      await page.getByText("LoopX · 已暂停", {exact: true}).waitFor();
      if (api.loopxModeRequests.at(-1)?.operation !== "pause") throw new Error("Pause did not call the existing control boundary");
      const paused = page.__loopxRuntime.loopxModes.get(request.sessionId);
      paused.native.status = "blocked";
      paused.deliveries = [{operation_id: "failed-check", agent_id: "local-analyst", todo_id: "todo_analysis", status: "rejected"}];
      await page.getByText("LoopX · 需要处理阻塞", {exact: true}).waitFor();
      await page.getByRole("button", {name: /最近成员回读有未通过或无法核验/}).waitFor();
      const beforeInspect = api.turnRequests.length;
      await page.getByRole("button", {name: /最近成员回读有未通过或无法核验/}).click();
      await team.getByText("本页有无法核验的工作，请检查原请求；不要直接重新派工。", {exact: true}).waitFor();
      if (api.turnRequests.length !== beforeInspect) throw new Error("Reviewing a failed result launched an Agent");
      await page.keyboard.press("Escape");
      await page.route("**/api/chat/sessions/*/loopx", async route => {
        if (route.request().method() === "GET") return route.fulfill({status: 503, json: {error: "Observation unavailable"}});
        return route.fallback();
      });
      await page.getByRole("alert").filter({hasText: "Observation unavailable"}).waitFor();
      if (await page.getByRole("dialog").isVisible()) throw new Error("Observation failure opened a blocking dialog");

      return {
        coverageEntries: await context.close(),
        note: "Conversation-first desktop/mobile layout; one-step team inspection preserves unknowns, recovery, focus and scroll; active pause/steer, blocked/rejected and unavailable observations remain visible without model launch.",
      };
    } catch (error) {
      releaseSnapshot();
      await context.close();
      throw error;
    }
  },
};

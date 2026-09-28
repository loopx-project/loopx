/** Exercise the existing packaged Team execution UI with the shared TS read model. */
import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {delegationPreflight} from "../../../../loopx/control_plane/collaboration/delegation.ts";
import {launchBrowser, loadPlaywright, waitForHttp} from "../../../../examples/dashboard-browser-smoke-support.mjs";
import {outputDir, packaged, port, startServer} from "../../../../examples/personal-workspace-browser/fixture.mjs";
import {openWorkspacePage} from "../../../../examples/personal-workspace-browser/scenario-context.mjs";

await mkdir(outputDir, {recursive: true});
const server = startServer();
let browser;
try {
  const url = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  for (const zh of [true, false]) {
    const context = await openWorkspacePage({newPage: options => browser.newPage({
      ...options, locale: zh ? "zh-CN" : "en-US",
    })}, url);
    const {api, page} = context;
    let state = "legacy_error";
    const inspected = [];
    try {
      await page.route("**/api/chat/sessions/*/loopx", async route => {
        const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
        if (body.operation !== "inspect" || state === "available") return route.fallback();
        inspected.push(body.binding_id);
        if (state === "legacy_error") return route.fulfill({status: 409,
          json: {error: "delegation workspace unavailable"}});
        const binding = {id: body.binding_id, agent_id: "fixture-worker", todo_id: "fixture-task"};
        const check = delegationPreflight({binding, workspace: {state}, authority: null,
          preview: null, acceptance: null, validation_files_current: false});
        await route.fulfill({json: check});
      });
      await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
      await page.getByRole("navigation", {name: zh ? "Goal 视图" : "Goal view"})
        .getByRole("button", {name: zh ? "对话" : "Chat", exact: true}).click();
      await page.getByRole("button", {name: zh ? "开启 LoopX 模式" : "Enable LoopX", exact: true}).click();
      const settings = page.locator(".goal-loopx-mode-settings");
      await settings.getByLabel(zh ? "已注册的协调身份" : "Registered coordinator").selectOption("lead");
      await settings.getByLabel(zh ? "协调员总 token 额度" : "Coordinator total token allowance").fill("100000");
      assert.equal(await settings.getByLabel(zh ? "成员执行绑定文件（Goal 配置）" : "Member execution bindings (Goal configuration)").isEditable(), false);
      await settings.getByRole("button", {name: zh ? "保存设置" : "Save settings", exact: true}).click();
      await page.getByRole("button", {name: zh ? "团队执行情况" : "Team execution", exact: true}).click();
      const team = page.getByRole("region", {name: zh ? "团队执行详情" : "Team execution details"});
      // Render the error response reproduced on the base, not a baseline UI build.
      await team.getByRole("button", {name: zh ? "检查整个团队" : "Check whole team", exact: true}).click();
      await team.locator(".goal-team-bindings > li > p[role=alert]").nth(2).waitFor();
      for (const [size, viewport] of [["desktop", {width: 1512, height: 982}], ["mobile", {width: 390, height: 844}]]) {
        await page.setViewportSize(viewport);
        await page.screenshot({path: resolve(outputDir, `workspace-preflight-before-response-${zh ? "zh" : "en"}-${size}.png`), animations: "disabled"});
      }
      assert.equal(context.errors.length, 3, "Only the three injected baseline HTTP refusals are expected");
      for (const error of context.errors) assert.match(error,
        /^Failed to load resource: the server responded with a status of 409 \(Conflict\)$/);
      context.errors.splice(0); // Keep subsequent recovery checks error-free.
      for (const fault of ["missing", "not_directory", "unavailable"]) {
        state = fault;
        await team.getByRole("button", {name: zh ? "检查整个团队" : "Check whole team", exact: true}).click();
        await team.getByRole("button", {name: zh ? "检查整个团队" : "Check whole team", exact: true}).waitFor({state: "visible"});
        const expected = zh
          ? {missing: "目录不存在", not_directory: "绑定位置不是目录", unavailable: "目录无法读取"}[fault]
          : {missing: "Directory missing", not_directory: "Bound location is not a directory", unavailable: "Directory could not be read"}[fault];
        const observations = team.locator(".goal-team-bindings > li > p[role=status]");
        await observations.filter({hasText: expected}).nth(2).waitFor();
        assert.equal(await observations.count(), 3);
        assert.equal(new Set(inspected.slice(-3)).size, 3);
        const content = await observations.first().innerText();
        assert.match(content, zh ? /核对原执行配置的工作目录/ : /review the workspace in the original execution configuration/);
        assert.match(content, zh ? /权限、验收与运行时尚未检查/ : /Authority, acceptance and runtime uninspected/);
        assert.doesNotMatch(content, /晋级|promotion|Canonical authority unavailable|缺少规范权限状态/);
      }
      const dialog = page.getByRole("dialog", {name: zh ? "团队执行情况" : "Team execution", exact: true});
      for (const [size, viewport] of [["desktop", {width: 1512, height: 982}], ["mobile", {width: 390, height: 844}]]) {
        await page.setViewportSize(viewport);
        assert(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth), "Workspace recovery copy must not overflow the dialog");
        await page.screenshot({path: resolve(outputDir, `workspace-preflight-${zh ? "zh" : "en"}-${size}.png`), animations: "disabled"});
      }
      const member = team.locator(".goal-team-bindings > li").filter({hasText: "local-analyst"});
      await member.locator("summary").click();
      state = "available";
      await member.getByRole("button", {name: zh ? "重新检查此成员" : "Recheck this member", exact: true}).click();
      await member.getByText(zh ? /运行时可用性尚未验证/ : /Runtime availability unverified/).waitFor();
      assert.equal(await member.getByText(zh ? /绑定工作目录不可用/ : /Bound workspace unavailable/).count(), 0);
      assert.equal(api.turnRequests.length, 0, "Inspection/recovery must not start model work");
      assert.equal(context.errors.length, 0, context.errors.join("\n"));
      console.log(`workspace preflight ${zh ? "Chinese" : "English"}: three faults, exact recheck and desktop/mobile passed`);
    } finally {
      await context.close();
    }
  }
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}

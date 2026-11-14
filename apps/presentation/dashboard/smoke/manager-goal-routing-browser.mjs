// Exercise the rendered conversation against the repository's disposable API fixture.
import assert from "node:assert/strict";
import {createServer} from "node:net";
import {once} from "node:events";

const probe = createServer();
await new Promise(resolve => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
process.env.LOOPX_PERSONAL_WORKSPACE_PORT = String(port);
const {packaged, startServer} = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const {openWorkspacePage} = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
const {launchBrowser, loadPlaywright, waitForHttp} = await import("../../../../examples/dashboard-browser-smoke-support.mjs");

const server = await startServer();
let browser;
try {
  const url = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  const localized = {newPage: options => browser.newPage({locale: "zh-CN", ...options})};
  const opened = await openWorkspacePage(localized, url, {
    beforeGoto(api) {
      api.transformStatus = fixture => {
        const source = fixture.run_history.goals.find(goal => goal.id === "product-release");
        const goal = JSON.parse(JSON.stringify(source).replaceAll("product-release", "manager"));
        goal.display_name = "Manager Goal";
        fixture.run_history.goals.push(goal);
      };
    },
  });
  const {page, api} = opened;
  try {
    const requests = [];
    page.on("request", request => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/chat/sessions") {
        requests.push(request.postDataJSON());
      }
    });
    await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    const composer = page.getByLabel("向 LoopX 发送消息");
    await composer.fill("保留原管家草稿");
    await page.getByText("Manager Goal", {exact: true}).first().click();
    await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    assert.equal(await composer.inputValue(), "", "Goal must not inherit the steward draft");
    await composer.fill("仅发给同名 Goal");
    const beforeGoal = api.turnRequests.length;
    await page.getByRole("button", {name: "发送", exact: true}).click();
    const goalDeadline = Date.now() + 10_000;
    while (api.turnRequests.length === beforeGoal && Date.now() < goalDeadline) await page.waitForTimeout(25);
    assert.equal(api.turnRequests.length, beforeGoal + 1, "Goal send must reach one Session");
    await page.locator(".personal-message-pending").waitFor({state: "hidden"});
    assert.ok(requests.some(body => body.context_kind === "goal" && body.goal_id === "manager"));
    const goalSession = api.turnRequests.at(-1)?.sessionId;
    assert.ok(goalSession);
    await page.locator(".personal-manager-link").first().click();
    assert.equal(await composer.inputValue(), "保留原管家草稿", "the old steward draft stays intact");
    assert.equal(await page.getByText("仅发给同名 Goal", {exact: true}).count(), 0);
    await composer.fill("仅发给管家");
    const beforeManager = api.turnRequests.length;
    await page.getByRole("button", {name: "发送", exact: true}).click();
    const managerDeadline = Date.now() + 10_000;
    while (api.turnRequests.length === beforeManager && Date.now() < managerDeadline) await page.waitForTimeout(25);
    assert.equal(api.turnRequests.length, beforeManager + 1, "steward send must reach one Session");
    await page.locator(".personal-message-pending").waitFor({state: "hidden"});
    assert.ok(requests.some(body => body.context_kind === "manager" && !body.goal_id));
    const managerSession = api.turnRequests.at(-1)?.sessionId;
    assert.ok(managerSession);
    assert.notEqual(managerSession, goalSession, JSON.stringify({requests, turns: api.turnRequests}));

    // A reload must recover each channel from its own stored Session.
    await page.reload({waitUntil: "domcontentloaded"});
    await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    await page.getByText("仅发给管家", {exact: true}).waitFor({state: "visible"});
    assert.equal(await page.getByText("仅发给同名 Goal", {exact: true}).count(), 0);
    await page.getByText("Manager Goal", {exact: true}).first().click();
    await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    await page.getByText("仅发给同名 Goal", {exact: true}).waitFor({state: "visible"});
    assert.equal(await page.getByText("仅发给管家", {exact: true}).count(), 0);
    assert.equal(api.turnRequests.length, beforeManager + 1, "recovery must not create another turn");

    for (const [sessionId, kind] of [[goalSession, "goal"], [managerSession, "manager"]]) {
      const turnId = `running-${kind}`;
      const pattern = `**/api/chat/sessions/${sessionId}/turns/${turnId}/events`;
      const held = [];
      page.__loopxRuntime.turnMessages.set(turnId, `正在执行 ${kind}`);
      page.__loopxRuntime.sessions.set(sessionId, {...page.__loopxRuntime.sessions.get(sessionId), active_turn_id: turnId, status: "busy"});
      await page.route(pattern, route => { held.push(route); });
      await page.reload({waitUntil: "domcontentloaded"});
      if (kind === "goal") {
        await page.getByText("Manager Goal", {exact: true}).first().click();
        await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
      } else {
        await page.locator(".personal-manager-link").first().click();
        await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
      }
      const stop = page.getByRole("button", {name: "中断本轮", exact: true});
      await stop.waitFor({state: "visible", timeout: 10_000});
      const streamDeadline = Date.now() + 10_000;
      while (!held.length && Date.now() < streamDeadline) await page.waitForTimeout(25);
      assert.ok(held.length, `${kind} must adopt the running Session before interruption`);
      await stop.click();
      const deadline = Date.now() + 10_000;
      while (!api.interrupts.some(row => row.sessionId === sessionId && row.turnId === turnId) && Date.now() < deadline) await page.waitForTimeout(25);
      assert.deepEqual(api.interrupts.at(-1), {sessionId, turnId}, `${kind} interrupt must reach its own Session; alerts=${JSON.stringify(await page.getByRole("alert").allTextContents())}`);
      await page.unroute(pattern);
      await Promise.all(held.map(route => route.abort().catch(() => {})));
    }

    await page.getByText("Manager Goal", {exact: true}).first().click();
    await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    await page.getByRole("button", {name: "开启 LoopX 模式", exact: true}).click();
    await page.getByLabel("已注册的协调身份").selectOption("lead");
    await page.getByLabel("协调员总 token 额度").fill("100000");
    await page.getByRole("button", {name: "保存设置", exact: true}).click();
    await page.locator(".goal-loopx-mode-settings").waitFor({state: "detached"});
    const beforeStart = api.loopxModeRequests.filter(row => row.operation === "start").length;
    await page.getByRole("button", {name: "开启 LoopX 模式", exact: true}).click();
    const startDeadline = Date.now() + 10_000;
    while (api.loopxModeRequests.filter(row => row.operation === "start").length === beforeStart && Date.now() < startDeadline) await page.waitForTimeout(25);
    const starts = api.loopxModeRequests.filter(row => row.operation === "start");
    assert.equal(starts.length, beforeStart + 1, "starting LoopX must submit one request");
    assert.equal(starts.at(-1)?.sessionId, goalSession, "LoopX must start in the Goal Session");
    console.log(`manager-goal browser (${packaged ? "packaged" : "development"}): draft, session, send, reload, interruption and LoopX isolation passed`);
  } finally {
    await opened.close();
  }
} finally {
  await browser?.close();
  if (server.exitCode === null && server.signalCode === null) {
    const exited = once(server, "exit");
    server.kill("SIGTERM");
    await exited;
  }
}

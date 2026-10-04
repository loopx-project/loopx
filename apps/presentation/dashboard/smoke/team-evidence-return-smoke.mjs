// A reader can follow version evidence and return without rediscovering the work.
// Uses the packaged UI and existing synthetic API fixture; no live Goal/model writes.
import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {launchBrowser, loadPlaywright, waitForHttp} from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
const {outputDir, port, startServer} = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const {openWorkspacePage} = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
let server, browser, workspace;
try {
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  workspace = await openWorkspacePage({newPage: options => browser.newPage({locale: "zh-CN", ...options})}, url);
  const {page, api} = workspace;
  await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
  await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "对话", exact: true}).click();
  await page.getByRole("button", {name: "开启 LoopX 模式", exact: true}).click();
  await page.getByLabel("已注册的协调身份").selectOption("lead");
  await page.getByLabel("协调员总 token 额度").fill("100000");
  await page.getByRole("button", {name: "保存设置", exact: true}).click();
  const configured = api.loopxModeRequests.findLast(row => row.operation === "configure");
  const mode = page.__loopxRuntime.loopxModes.get(configured.sessionId);
  Object.assign(mode, {enabled: true, paused: false, active_turn_id: "fixture-loopx-turn",
    native: {status: "active", tokenBudget: 100000}, fixtureCorrectionEpisode: true});
  await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
  await page.getByRole("button", {name: "团队执行情况", exact: true}).click();
  const dialog = page.getByRole("dialog", {name: "团队执行情况"});
  const openEvidence = dialog.getByRole("button", {name: "查看证据与反馈", exact: true});
  await openEvidence.first().click();
  const evidence = dialog.getByRole("region", {name: "执行证据"});
  const original = () => evidence.getByLabel("证据内容: report.json");
  await original().waitFor();
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await evidence.getByRole("button", {name: "阅读回应与证据", exact: true}).click();
  await evidence.getByLabel("证据内容: objection.json").waitFor();
  await evidence.getByRole("button", {name: "original-analysis", exact: true}).click();
  await original().waitFor();
  assert.match(await original().textContent(), /cash_flow.*90/);
  const back = dialog.getByRole("button", {name: "返回上一份证据", exact: true});
  await back.waitFor({timeout: 3000});
  await page.keyboard.press("Enter");
  await evidence.getByLabel("证据内容: objection.json").waitFor();
  assert.ok(await back.evaluate(el => el === document.activeElement), "Returning retains keyboard navigation in evidence");
  const readsBefore = api.loopxModeRequests.filter(row => row.operation === "read").length;
  await back.click();
  await original().waitFor();
  assert.match(await original().textContent(), /cash_flow.*75/);
  assert.equal(api.loopxModeRequests.filter(row => row.operation === "read").length, readsBefore + 1,
    "Return rechecks the original operation instead of replaying cached acceptance");
  assert.equal(await back.count(), 0, "Root evidence returns to the execution list");
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await evidence.getByRole("button", {name: "阅读原始产物", exact: true}).click();
  await original().waitFor();
  await mkdir(outputDir, {recursive: true});
  await page.screenshot({path: resolve(outputDir, "team-evidence-return-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  await page.emulateMedia({reducedMotion: "reduce"});
  assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
  await page.screenshot({path: resolve(outputDir, "team-evidence-return-mobile.png"), animations: "disabled"});
  const failedReturn = async route => {
    const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
    if (body.operation === "read" && body.operation_id === "accepted-analysis") {
      return route.fulfill({status: 409, json: {error: "original acceptance revoked"}});
    }
    return route.fallback();
  };
  await page.route("**/api/chat/sessions/*/loopx", failedReturn);
  await back.click();
  await evidence.getByRole("alert").filter({hasText: "已清除上次证据"}).waitFor();
  assert.equal(await original().count(), 0, "Returning after revocation cannot restore the old report");
  await dialog.getByRole("button", {name: "返回执行列表", exact: true}).click();
  assert.ok(await openEvidence.first().evaluate(el => el === document.activeElement));
  assert.equal(api.turnRequests.length, 0);
  assert.equal(api.loopxModeRequests.filter(row => row.operation === "message").length, 0);
  console.log("team-evidence-return: passed (packaged navigation, multi-step keyboard return, fresh acceptance, revoked return, mobile and no execution)");
} finally {
  await workspace?.close();
  await browser?.close();
  server?.kill("SIGTERM");
}

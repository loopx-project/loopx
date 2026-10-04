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
    native: {status: "active", tokenBudget: 100000}, fixtureCorrectionEpisode: true, fixtureAdoptionState: "current"});
  await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
  await page.getByRole("button", {name: "团队执行情况", exact: true}).click();
  const dialog = page.getByRole("dialog", {name: "团队执行情况"});
  const openEvidence = dialog.getByRole("button", {name: "查看证据与反馈", exact: true});
  await openEvidence.first().click();
  const evidence = dialog.getByRole("region", {name: "执行证据"});
  const original = () => evidence.getByLabel("证据内容: report.json");
  await original().waitFor();
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  const verificationGap = evidence.getByText("当前读回未提供独立验收者与指定版本回执。", {exact: true});
  await verificationGap.waitFor({timeout: 3000});
  await evidence.getByText("后续结果 · 当前验收与采用记录有效", {exact: false}).waitFor();
  await mkdir(outputDir, {recursive: true});
  await verificationGap.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-verifier-gap-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  await page.emulateMedia({reducedMotion: "reduce"});
  assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
  await verificationGap.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-verifier-gap-mobile.png"), animations: "disabled"});
  await page.setViewportSize({width: 1512, height: 980});
  // Losing an optional adoption cannot erase freshly verified correction evidence.
  const unavailableDownstream = route => route.request().postDataJSON()?.operation === "read"
    && route.request().postDataJSON()?.operation_id === "accepted-synthesis"
    ? route.fulfill({status: 503, json: {error: "downstream observation unavailable"}}) : route.fallback();
  await page.route("**/api/chat/sessions/*/loopx", unavailableDownstream);
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  const adoptionGap = evidence.getByText("采用证据无法核验", {exact: false});
  await adoptionGap.waitFor({timeout: 3000});
  await verificationGap.waitFor();
  assert.equal(await evidence.getByRole("button", {name: "阅读原始产物", exact: true}).count(), 1);
  assert.equal(await evidence.getByRole("button", {name: "阅读回应与证据", exact: true}).count(), 1);
  assert.equal(await evidence.getByRole("button", {name: "阅读后续结果", exact: true}).count(), 0);
  await page.screenshot({path: resolve(outputDir, "team-adoption-unavailable-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
  await adoptionGap.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-adoption-unavailable-mobile.png"), animations: "disabled"});
  await page.setViewportSize({width: 1512, height: 980});
  await page.unroute("**/api/chat/sessions/*/loopx", unavailableDownstream);
  // Revoke the refreshed owner receipt after this reader's earlier current observation.
  mode.fixtureAdoptionState = "unavailable";
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await adoptionGap.waitFor({timeout: 3000});
  assert.equal(await evidence.getByRole("button", {name: "阅读后续结果", exact: true}).count(), 0);
  await evidence.getByRole("button", {name: "重新读取证据", exact: true}).click();
  await original().waitFor();
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await adoptionGap.waitFor({timeout: 3000});
  mode.fixtureAdoptionState = "current";
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await evidence.getByRole("button", {name: "阅读后续结果", exact: true}).waitFor();
  const unavailableCore = route => route.request().postDataJSON()?.operation === "read"
    && route.request().postDataJSON()?.operation_id === "review-objection"
    ? route.fulfill({status: 409, json: {error: "review version revoked"}}) : route.fallback();
  await page.route("**/api/chat/sessions/*/loopx", unavailableCore);
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await evidence.getByRole("alert").filter({hasText: "关联执行或版本已变化"}).waitFor();
  assert.equal(await verificationGap.count(), 0, "A lost core revision still clears the trace");
  assert.equal(await evidence.getByRole("button", {name: "阅读原始产物", exact: true}).count(), 0);
  await page.unroute("**/api/chat/sessions/*/loopx", unavailableCore);
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
    if (body.operation === "operations") {
      return route.fulfill({json: {items: [{record_id: "a".repeat(64), operation_id: "accepted-analysis",
        status: "unavailable", recovery_required: null}], has_more: false, next_cursor: null, page_readback_complete: false}});
    }
    return route.fallback();
  };
  await page.route("**/api/chat/sessions/*/loopx", failedReturn);
  await back.click();
  await evidence.getByRole("alert").filter({hasText: "已清除上次证据"}).waitFor();
  assert.equal(await original().count(), 0, "Returning after revocation cannot restore the old report");
  assert.equal(await verificationGap.count(), 0, "Unavailable evidence cannot retain a prior correction trace");
  await dialog.getByRole("button", {name: "返回执行列表", exact: true}).click();
  await dialog.locator('.goal-team-record[data-state="unavailable"]').waitFor({timeout: 3000});
  assert.equal(await dialog.locator('.goal-team-pulse [data-bucket="accepted"] strong').textContent(), "0",
    "Returning to the list must withdraw accepted counts when current evidence is unavailable");
  assert.ok(await openEvidence.first().evaluate(el => el === document.activeElement));
  await page.unroute("**/api/chat/sessions/*/loopx", failedReturn);
  const refresh = dialog.getByRole("button", {name: "重新核验", exact: true});
  await refresh.click();
  await dialog.locator('.goal-team-record[data-state="accepted"]').waitFor();
  await openEvidence.first().click();
  await original().waitFor();
  const failedList = async route => route.request().postDataJSON()?.operation === "operations"
    ? route.fulfill({status: 503, json: {error: "delegation inventory unavailable"}}) : route.fallback();
  await page.route("**/api/chat/sessions/*/loopx", failedList);
  await dialog.getByRole("button", {name: "返回执行列表", exact: true}).click();
  await dialog.getByRole("alert").filter({hasText: "delegation inventory unavailable"}).waitFor();
  assert.equal(await dialog.locator(".goal-team-pulse").count(), 0, "Failed readback cannot retain cached counts");
  assert.equal(await openEvidence.count(), 0, "Failed readback cannot retain cached execution records");
  assert.ok(await refresh.evaluate(el => el === document.activeElement), "Unavailable list returns focus to recovery");
  await page.unroute("**/api/chat/sessions/*/loopx", failedList);
  await refresh.click();
  await dialog.locator('.goal-team-record[data-state="accepted"]').waitFor();
  await dialog.getByRole("button", {name: "下一页", exact: true}).click();
  await dialog.locator('.goal-team-record[data-state="recovery_required"]').waitFor();
  await openEvidence.first().click();
  await evidence.waitFor();
  const listReadsBefore = api.loopxModeRequests.filter(row => row.operation === "operations").length;
  await dialog.getByRole("button", {name: "返回执行列表", exact: true}).click();
  await dialog.locator('.goal-team-record[data-state="recovery_required"]').waitFor();
  const listReads = api.loopxModeRequests.filter(row => row.operation === "operations");
  assert.equal(listReads.length, listReadsBefore + 1, "List return performs one read, without polling");
  assert.equal(listReads.at(-1).cursor, "b".repeat(64), "Return retains the original page instead of jumping to the first page");
  assert.ok(await openEvidence.first().evaluate(el => el === document.activeElement));
  assert.equal(api.turnRequests.length, 0);
  assert.equal(api.loopxModeRequests.filter(row => row.operation === "message").length, 0);
  console.log("team-evidence-return: passed (packaged navigation, downstream loss/revocation/restoration, core loss, keyboard return, fresh evidence/list, pagination, mobile and no execution)");
} finally {
  await workspace?.close();
  await browser?.close();
  server?.kill("SIGTERM");
}

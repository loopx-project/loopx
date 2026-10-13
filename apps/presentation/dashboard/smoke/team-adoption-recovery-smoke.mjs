// Packaged report interaction + real scoped HTTP/delegation/SQLite adoption.
// Navigation is synthetic; no acceptance/adoption rule or result is mocked.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createInterface} from "node:readline";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
const {repoRoot, outputDir, port, startServer} = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const {openWorkspacePage} = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
const fixture = spawn(resolveTestPython({repoRoot}), ["-u", "apps/presentation/dashboard/smoke/team-adoption-http-fixture.py"],
  {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
const exited = once(fixture, "exit");
const lines = createInterface({input: fixture.stdout});
const iterator = lines[Symbol.asyncIterator]();
let stderr = "";
fixture.stderr.on("data", chunk => {stderr += String(chunk);});
async function next() {const line = await iterator.next(); assert.equal(line.done, false, stderr); return JSON.parse(line.value);}
async function command(value) {fixture.stdin.write(`${value}\n`); return next();}
let server, browser, workspace;
try {
  const {origin} = await next();
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
  const writes = [];
  let loseAck = true;
  await page.route("**/api/chat/sessions/*/loopx", async route => {
    const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
    if (!["read", "operations", "adopt"].includes(body.operation)) return route.fallback();
    const response = await route.fetch({url: `${origin}/api/chat/sessions/adoption/loopx`,
      headers: {...route.request().headers(), origin}});
    if (body.operation === "adopt") {
      writes.push(body);
      if (loseAck) {
        assert.equal(response.status(), 200, await response.text());
        loseAck = false;
        return route.fulfill({status: 503, json: {error: "Synthetic acknowledgement loss after real commit"}});
      }
    }
    return route.fulfill({response});
  });
  Object.assign(mode, {enabled: true, paused: true, active_turn_id: null, native: {status: "paused", tokenBudget: 100000}});
  const results = page.getByRole("region", {name: "团队成果", exact: true});
  await results.getByRole("button", {name: "analyst · output.json", exact: true}).click();
  await results.locator(".goal-team-result-reader > details > summary").click();
  const adoption = results.locator(".goal-team-adoption");
  await adoption.locator("summary").click();
  await adoption.getByRole("button", {name: "查找使用此版本的后续结果", exact: true}).click();
  await adoption.getByLabel("后续结果", {exact: true}).waitFor();
  assert.equal(writes.length, 0);
  assert.equal((await command("inspect")).result.adoptions, undefined);
  await adoption.getByRole("button", {name: "阅读后续结果", exact: true}).click();
  await adoption.getByLabel("证据内容: report.md").waitFor();
  assert.equal(writes.length, 0);
  const confirm = adoption.getByRole("button", {name: "确认采用于此结果", exact: true});
  await confirm.focus(); await page.keyboard.press("Enter");
  await adoption.getByText("采用已记录，指定版本与后续结果当前有效。", {exact: true}).waitFor();
  assert.equal(writes.length, 1, "Lost acknowledgement reconciles the actual committed receipt without another write");
  assert.deepEqual(writes[0], {operation: "adopt", operation_id: "analysis-1", consumer_operation_id: "synthesis-1"});
  const recorded = await command("inspect");
  assert.equal(recorded.result.adoptions.length, 1);
  assert.equal(recorded.result.adoptions[0].state, "current");
  assert.equal(recorded.coordinator_paused, true, "The real session remains paused, as shown in the report");
  assert.ok(recorded.session_unchanged, "No native lifecycle, mode, wake or extra coordinator Turn changed");
  assert.deepEqual(recorded.host_invocations, ["1", "1"]);
  await mkdir(outputDir, {recursive: true});
  await adoption.getByText("采用已记录，指定版本与后续结果当前有效。", {exact: true}).scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-adoption-owner-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844}); await page.emulateMedia({reducedMotion: "reduce"});
  assert.ok(await adoption.evaluate(el => el.scrollWidth <= el.clientWidth));
  await adoption.getByText("采用已记录，指定版本与后续结果当前有效。", {exact: true}).scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-adoption-owner-mobile.png"), animations: "disabled"});
  // Current source still renders, while a real changed receiver input revokes adoption.
  await command("withdraw");
  await results.getByRole("button", {name: "刷新成果", exact: true}).click();
  await results.locator(".goal-team-result-reader > details > summary").click();
  await results.getByText("采用证据已失效或无法核验", {exact: true}).waitFor();
  assert.equal(await adoption.getByText("采用已记录，指定版本与后续结果当前有效。", {exact: true}).count(), 0);
  await adoption.locator("summary").click();
  await adoption.getByRole("button", {name: "查找使用此版本的后续结果", exact: true}).click();
  await adoption.getByText("已检查的工作中没有可核验的使用结果。", {exact: true}).waitFor();
  const refused = await fetch(`${origin}/api/chat/sessions/adoption/loopx`, {method: "POST", headers: {"Content-Type": "application/json", Origin: origin}, body: JSON.stringify(writes[0])});
  assert.equal(refused.status, 409, await refused.text());
  await command("restore");
  await results.getByRole("button", {name: "刷新成果", exact: true}).click();
  await results.locator(".goal-team-result-reader > details > summary").click();
  await results.getByText("已记录采用 · 后续结果验收有效", {exact: true}).waitFor();
  assert.equal(writes.length, 1, "Readback recovery never records a new adoption");
  assert.equal(api.turnRequests.length, 0);
  console.log("team-adoption-recovery: passed (packaged report, real HTTP/SQLite/checks/receipts, explicit decision, lost ACK, exact use withdrawal/recovery, no dispatch, mobile/keyboard)");
} catch (error) {
  if (workspace) console.error({body: (await workspace.page.locator("body").innerText()).slice(-3000), errors: workspace.errors});
  throw error;
} finally {
  await workspace?.close(); await browser?.close(); server?.kill("SIGTERM");
  fixture.stdin.end(); if (fixture.exitCode === null) await exited; lines.close();
}

// Packaged reader + production Chat HTTP, delegation workers and independent checks.
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
const fixture = spawn(resolveTestPython({repoRoot}), ["-u", "tests/presentation/team_result_use_fixture.py"], {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
const exited = once(fixture, "exit");
const lines = createInterface({input: fixture.stdout});
const iterator = lines[Symbol.asyncIterator]();
let stderr = "";
fixture.stderr.on("data", chunk => {stderr += chunk;});
async function next() {const line = await iterator.next(); assert.equal(line.done, false, stderr); return JSON.parse(line.value);}
async function command(value) {fixture.stdin.write(`${value}\n`); return next();}
let server, browser, workspace;
try {
  const {origin, goal_id} = await next();
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
  Object.assign(page.__loopxRuntime.loopxModes.get(configured.sessionId), {enabled: true, paused: false,
    active_turn_id: "fixture-loopx-turn", native: {status: "active", tokenBudget: 100000}});
  let selected = "synthesis-1", realInventory = false;
  await page.route("**/api/chat/sessions/*/loopx", async route => {
    const body = route.request().postDataJSON();
    if (realInventory && body?.operation === "operations") {
      const response = await page.request.post(`${origin}/api/chat/sessions/recovery/loopx`, {data: {operation: "operations"}});
      const actual = await response.json();
      return route.fulfill({json: {...actual, items: actual.items.filter(row => row.operation_id === "synthesis-1"),
        has_more: false, next_cursor: null}});
    }
    if (!["read", "revalidate"].includes(body?.operation)) return route.fallback();
    const response = await page.request.post(`${origin}/api/chat/sessions/recovery/loopx`, {data: {
      operation: body.operation, operation_id: ["accepted-analysis", "synthesis-1"].includes(body.operation_id) ? selected : body.operation_id}});
    return route.fulfill({response});
  });
  await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
  realInventory = true;
  const results = page.getByRole("region", {name: "团队成果", exact: true});
  await results.getByRole("button", {name: "刷新成果", exact: true}).click();
  await results.getByRole("button", {name: "reviewer · output.json", exact: true}).click();
  await results.getByLabel("当前报告", {exact: true}).waitFor();
  await page.getByRole("button", {name: "团队执行情况", exact: true}).click();
  const dialog = page.getByRole("dialog", {name: "团队执行情况"});
  await dialog.getByRole("button", {name: "查看证据与反馈", exact: true}).first().click();
  const evidence = dialog.getByRole("region", {name: "执行证据"});
  await evidence.getByLabel("证据内容: output.json").waitFor();
  assert.deepEqual((await command("withdraw")).host_calls, ["1", "1", "1"]);
  const recheck = evidence.getByRole("button", {name: "重新读取证据", exact: true});
  await recheck.click();
  await evidence.getByText("接收方输入不可用", {exact: true}).waitFor();
  await evidence.getByRole("button", {name: "查看阻断来源", exact: true}).waitFor();
  assert.equal(await evidence.getByLabel("证据内容: output.json").count(), 0);
  await mkdir(outputDir, {recursive: true});
  await page.screenshot({path: resolve(outputDir, "team-result-use-blocked.png")});
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
  await page.screenshot({path: resolve(outputDir, "team-result-use-blocked-mobile.png")});
  await dialog.getByRole("button", {name: "关闭", exact: true}).click();
  await results.getByRole("button", {name: "刷新成果", exact: true}).click();
  await results.getByText("已检查的页面没有可读取的已验收产物。", {exact: true}).waitFor();
  assert.equal(await results.getByLabel("当前报告", {exact: true}).count(), 0);
  assert.deepEqual((await command("restore")).host_calls, ["1", "1", "1"]);
  await results.getByRole("button", {name: "刷新成果", exact: true}).click();
  await results.getByLabel("当前报告", {exact: true}).waitFor();
  await page.getByRole("button", {name: "团队执行情况", exact: true}).click();
  await dialog.getByRole("button", {name: "查看证据与反馈", exact: true}).first().click();
  // Wait for the reader's initial request and navigation focus before testing
  // keyboard recheck; opening the view must not race its mount effects.
  await evidence.getByLabel("证据内容: output.json").waitFor();
  await recheck.press("Enter");
  await evidence.getByLabel("证据内容: output.json").waitFor();
  assert.equal(await evidence.getByText("接收方输入不可用", {exact: true}).count(), 0);
  const restored = await command("inspect");
  assert.equal(restored.operation.current_use.state, "current");
  assert.equal(restored.historical_unchanged, true);
  assert.deepEqual(restored.host_calls, ["1", "1", "1"]);
  console.log("PASS: packaged source withdrawal, current evidence recovery, immutable history and no redispatch");
} catch (error) {
  if (workspace) console.error({body: (await workspace.page.locator("body").innerText()).slice(-3000), errors: workspace.errors});
  throw error;
} finally {
  await workspace?.close(); await browser?.close(); server?.kill("SIGTERM");
  fixture.stdin.end(); const [code] = await exited; lines.close(); assert.equal(code, 0, stderr);
}

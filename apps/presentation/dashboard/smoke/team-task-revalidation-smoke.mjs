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
const fixture = spawn(resolveTestPython({repoRoot}), ["-u", "tests/presentation/team_task_revalidation_fixture.py"], {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
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
  let selected = "failure-1";
  await page.route("**/api/chat/sessions/*/loopx", async route => {
    const body = route.request().postDataJSON();
    if (!["read", "revalidate"].includes(body?.operation)) return route.fallback();
    const response = await page.request.post(`${origin}/api/chat/sessions/recovery/loopx`, {data: {
      operation: body.operation, operation_id: ["accepted-analysis", "synthesis-1"].includes(body.operation_id) ? selected : body.operation_id}});
    return route.fulfill({response});
  });
  await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
  await command("task-failure");
  await page.getByRole("button", {name: "团队执行情况", exact: true}).click();
  const dialog = page.getByRole("dialog", {name: "团队执行情况"});
  await dialog.getByRole("button", {name: "查看证据与反馈", exact: true}).first().click();
  const evidence = dialog.getByRole("region", {name: "执行证据"});
  const recheck = evidence.getByRole("button", {name: "重新读取证据", exact: true});
  await mkdir(outputDir, {recursive: true});
  await page.setViewportSize({width: 390, height: 844});
  const failure = evidence.getByRole("region", {name: "原任务验收失败"});
  await failure.getByRole("button", {name: "复核原任务", exact: true}).waitFor();
  const before = await command("task-inspect");
  await failure.getByRole("button", {name: "复核原任务", exact: true}).click();
  await failure.getByRole("button", {name: "复核原任务", exact: true}).waitFor();
  const unchanged = await command("task-inspect");
  assert.equal(unchanged.operation.status, "rejected");
  assert.equal(unchanged.host_calls, "1");
  await command("task-repair");
  await command("task-commit-response-lost");
  await recheck.click();
  const recover = evidence.getByRole("button", {name: "恢复原执行", exact: true});
  await recover.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-original-recovery-mobile.png")});
  await recover.click();
  for (let attempt = 0; attempt < 30; attempt++) {
    const actual = await command("task-inspect");
    assert.equal(actual.host_calls, "1");
    if (actual.operation.status === "accepted") break;
    await new Promise(done => setTimeout(done, 500));
  }
  await recheck.click();
  await evidence.getByLabel("证据内容: output.json").waitFor();
  assert.equal(await failure.count(), 0);
  const after = await command("task-inspect");
  assert.equal(after.operation.status, "accepted");
  assert.equal(before.operation.operation_id, after.operation.operation_id);
  assert.equal(after.host_calls, "1");
  console.log("PASS: packaged original task failure, unchanged failure and committed response-loss recovery without repeating Host");
} catch (error) {
  if (workspace) console.error({body: (await workspace.page.locator("body").innerText()).slice(-3000), errors: workspace.errors});
  throw error;
} finally {
  await workspace?.close(); await browser?.close(); server?.kill("SIGTERM");
  fixture.stdin.end(); const [code] = await exited; lines.close(); assert.equal(code, 0, stderr);
}

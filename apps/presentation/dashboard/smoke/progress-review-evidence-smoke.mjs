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
const fixture = spawn(resolveTestPython({repoRoot}), ["-u", "tests/presentation/progress_review_evidence_fixture.py"], {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
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
  await mkdir(outputDir, {recursive: true});
  await page.setViewportSize({width: 390, height: 844});
  await page.route("**/api/chat/goal-configuration?*", async route => {
    const response = await page.request.get(`${origin}/api/chat/goal-configuration?goal_id=${encodeURIComponent(goal_id)}`);
    return route.fulfill({response});
  });
  await page.getByRole("button", {name: "Goal 设置", exact: true}).click();
  await page.getByRole("button", {name: "能力中心", exact: true}).click();
  await page.getByRole("button", {name: /进展评估哨兵/}).click();
  const review = page.getByRole("region", {name: "审查证据读回"});
  await review.getByText("绑定任务的规范验收条款", {exact: true}).waitFor();
  await review.getByText("与目标的关系: off_goal", {exact: true}).waitFor();
  await review.getByText("新证据增量: new_evidence", {exact: true}).waitFor();
  await review.getByText("仅观察声明文件在两个检查点之间的净变化", {exact: true}).waitFor();
  const coverage = review.getByText("仅观察声明文件在两个检查点之间的净变化", {exact: true});
  await coverage.scrollIntoViewIfNeeded();
  await review.getByText("观察范围与条款版本", {exact: true}).click();
  await review.locator("code").scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "progress-review-mixed-dimensions-mobile.png")});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  await page.setViewportSize({width: 1280, height: 960});
  await review.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "progress-review-mixed-dimensions-desktop.png")});
  await command("review-task-edit");
  await review.getByRole("button", {name: "重新读取审查证据", exact: true}).click();
  await review.getByText("条款或任务关联已变化，旧判断不可作为当前依据。", {exact: true}).waitFor();
  await review.getByText("最新审查: stale", {exact: true}).waitFor();
  await command("review-missing");
  await review.getByRole("button", {name: "重新读取审查证据", exact: true}).click();
  await review.getByText("尚无审查回执。", {exact: true}).waitFor();
  assert.equal(await review.getByText("与目标的关系: off_goal", {exact: true}).count(), 0);
  console.log("PASS: packaged scoped shadow readback, separate dimensions, changed-task withdrawal and missing evidence via production HTTP");
} catch (error) {
  if (workspace) console.error({body: (await workspace.page.locator("body").innerText()).slice(-3000), errors: workspace.errors});
  throw error;
} finally {
  await workspace?.close(); await browser?.close(); server?.kill("SIGTERM");
  fixture.stdin.end(); const [code] = await exited; lines.close(); assert.equal(code, 0, stderr);
}

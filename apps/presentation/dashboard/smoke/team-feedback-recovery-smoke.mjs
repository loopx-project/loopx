// Packaged evidence UI + production inbox HTTP/store; no model or active Goal writes.
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
const fixture = spawn(resolveTestPython({repoRoot}), ["-u", "apps/presentation/dashboard/smoke/team-feedback-http-fixture.py"],
  {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
const exited = once(fixture, "exit");
const lines = createInterface({input: fixture.stdout});
const iterator = lines[Symbol.asyncIterator]();
let stderr = "";
fixture.stderr.on("data", chunk => {stderr += String(chunk);});
async function next() {
  const line = await iterator.next();
  assert.equal(line.done, false, stderr);
  return JSON.parse(line.value);
}
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
  Object.assign(mode, {enabled: true, paused: false, active_turn_id: "fixture-loopx-turn",
    native: {status: "active", tokenBudget: 100000}, fixtureCorrectionEpisode: true});
  await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
  const posts = [];
  let loseAcknowledgement = true;
  await page.route("**/api/chat/sessions/*/loopx", async route => {
    const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
    if (body.operation !== "message") return route.fallback();
    posts.push(body);
    const response = await route.fetch({url: `${origin}/api/chat/sessions/feedback/loopx`});
    if (loseAcknowledgement) {
      assert.equal(response.status(), 200, await response.text());
      loseAcknowledgement = false;
      return route.fulfill({status: 503, json: {error: "Synthetic acknowledgement loss after commit"}});
    }
    return route.fulfill({response});
  });
  const open = () => page.getByRole("button", {name: "团队执行情况", exact: true}).click();
  const dialog = page.getByRole("dialog", {name: "团队执行情况"});
  const evidence = page.getByRole("region", {name: "执行证据", exact: true});
  const feedback = evidence.getByLabel("向协调员反馈此执行", {exact: true});
  const select = () => dialog.getByRole("button", {name: "查看证据与反馈", exact: true}).first().click();
  await open(); await select();
  await feedback.fill("Use the revised disclosure, and keep the source comparison.");
  await evidence.getByRole("button", {name: "发送反馈", exact: true}).click();
  await evidence.getByRole("alert").filter({hasText: "未确认投递"}).waitFor();
  const first = posts[0];
  assert.match(first.message, /accepted-analysis/);
  assert.match(first.message, /sha256:/);
  const committed = await command("inspect");
  assert.equal(committed.ingress.length, 1, "The failed response follows an actual durable inbox commit");
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await evidence.getByRole("button", {name: "阅读原始产物", exact: true}).click();
  await evidence.getByLabel("向协调员反馈此执行", {exact: true}).waitFor();
  assert.equal(await feedback.inputValue(), "", "Feedback is scoped to its originating operation");
  await dialog.getByRole("button", {name: "返回上一份证据", exact: true}).click();
  await feedback.waitFor();
  assert.equal(await feedback.inputValue(), "Use the revised disclosure, and keep the source comparison.",
    "Returning must retain an uncertain submission rather than mint another feedback identity");
  await page.reload();
  await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
  const unavailableEvidence = route => route.request().method() === "POST"
    && route.request().postDataJSON()?.operation === "read"
    && route.request().postDataJSON()?.operation_id === "accepted-analysis"
    ? route.fulfill({status: 409, json: {error: "Synthetic output version withdrawn"}}) : route.fallback();
  await page.route("**/api/chat/sessions/*/loopx", unavailableEvidence);
  await open(); await select();
  await evidence.getByRole("alert").filter({hasText: "已清除上次证据"}).waitFor();
  await evidence.getByRole("button", {name: "重试同一条反馈", exact: true}).waitFor();
  assert.equal(posts.length, 1, "Reload and evidence navigation cannot send feedback");
  await mkdir(outputDir, {recursive: true});
  await evidence.getByRole("button", {name: "重试同一条反馈", exact: true}).scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-feedback-unconfirmed-desktop.png"), animations: "disabled"});
  await evidence.getByRole("button", {name: "重试同一条反馈", exact: true}).focus();
  await page.keyboard.press("Enter");
  await evidence.getByText("已进入协调员收件箱，等待读取", {exact: true}).waitFor();
  assert.deepEqual(posts[1], first, "Retry preserves the exact id, body and observed artifact versions");
  const recovered = await command("inspect");
  assert.equal(recovered.ingress.length, 1);
  assert.equal(recovered.messages.filter(row => row.origin === "loopx_inbox").length, 1);
  assert.equal(recovered.turn_count, committed.turn_count, "Feedback never dispatches another model Turn");
  assert.equal(await evidence.getByText("已进入协调员收件箱，等待读取", {exact: true}).count(), 1,
    "An inbox receipt is presented as awaiting read, never as applied feedback");
  await page.unroute("**/api/chat/sessions/*/loopx", unavailableEvidence);
  await evidence.getByRole("button", {name: "重新读取证据", exact: true}).click();
  await evidence.getByLabel("证据内容: report.json").waitFor();
  await mkdir(outputDir, {recursive: true});
  await feedback.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-feedback-recovered-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  await page.emulateMedia({reducedMotion: "reduce"});
  assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
  await evidence.getByText("已进入协调员收件箱，等待读取", {exact: true}).scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "team-feedback-recovered-mobile.png"), animations: "disabled"});
  await evidence.getByRole("button", {name: "撰写新的反馈", exact: true}).click();
  await feedback.fill("A separate explicitly requested correction.");
  await command("pause");
  await evidence.getByRole("button", {name: "发送反馈", exact: true}).click();
  await evidence.getByRole("alert").filter({hasText: "未确认投递"}).waitFor();
  assert.match(posts.at(-1).message, /A separate explicitly requested correction/);
  assert.notEqual(posts.at(-1).operation_id, first.operation_id);
  const refused = await command("inspect");
  assert.equal(refused.ingress.length, 1, "Real typed admission rejects feedback to a paused coordinator");
  assert.equal(refused.turn_count, committed.turn_count);
  await page.evaluate(sessionId => sessionStorage.setItem(`loopx-team-feedback:${JSON.stringify([sessionId, "original-analysis"])}`, "malformed"), configured.sessionId);
  await evidence.getByRole("button", {name: "核验关联执行", exact: true}).click();
  await evidence.getByRole("button", {name: "阅读原始产物", exact: true}).click();
  await evidence.getByRole("alert").filter({hasText: "无法恢复上次反馈"}).waitFor();
  assert.ok(await evidence.getByRole("button", {name: "发送反馈", exact: true}).isDisabled());
  assert.equal(posts.length, 3, "Malformed local recovery cannot silently create a replacement submission");
  console.log("team-feedback-recovery: passed (packaged UI, production inbox/store, lost receipt, navigation/reload, exact retry, scoped input, paused admission, mobile/keyboard)");
} catch (error) {
  if (workspace) console.error({body: (await workspace.page.locator("body").innerText()).slice(-3000), errors: workspace.errors});
  throw error;
} finally {
  await workspace?.close(); await browser?.close(); server?.kill("SIGTERM");
  fixture.stdin.end(); if (fixture.exitCode === null) await exited; lines.close();
}

// Packaged UI with the production Chat HTTP/store; unrelated workspace APIs
// use the existing synthetic presentation fixture. No model or live Goal writes.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { launchBrowser, loadPlaywright, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
const { repoRoot, outputDir, port, startServer } = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const { openWorkspacePage } = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
await verifyBoundReadRecovery();
if (process.argv.includes("--bound-permission-only")) process.exit(0);
const fixture = spawn(resolveTestPython({ repoRoot }), ["-u", "apps/presentation/dashboard/smoke/conversation-history-http-fixture.py", "--manager"],
  { cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(fixture, "exit");
const lines = createInterface({ input: fixture.stdout });
const iterator = lines[Symbol.asyncIterator]();
let stderr = "";
fixture.stderr.on("data", chunk => { stderr += String(chunk); });
async function next() {
  const line = await iterator.next();
  assert.equal(line.done, false, stderr);
  return JSON.parse(line.value);
}
async function command(value) { fixture.stdin.write(`${value}\n`); return next(); }
let server, browser, workspace;
const reads = [];
try {
  const { origin } = await next();
  await command("recover");
  assert.deepEqual(await command("history"), {history_rows: 451});
  await command("page-fault");
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  workspace = await openWorkspacePage({ newPage: options => browser.newPage({ locale: "zh-CN", ...options }) }, url, {
    beforeGoto(_api, page) {
      return page.route("**/api/chat/sessions**", async route => {
        const path = new URL(route.request().url()).pathname;
        if (!/^\/api\/chat\/sessions(?:\/(?:old|current|other-channel))?$/u.test(path)) return route.fallback();
        // Existing UI admission calls resume_latest even for a readable stored
        // session. Seed that admission from the real snapshot; this test does
        // not certify host admission or run a model. Goal admission stays in
        // the unrelated workspace fixture.
        if (route.request().method() === "POST" && path === "/api/chat/sessions") {
          const body = route.request().postDataJSON();
          if (body.context_kind !== "manager") return route.fallback();
          assert.equal(body.mode, "resume_latest");
          const response = await route.fetch({ url: `${origin}/api/chat/sessions/current`, method: "GET", postData: undefined });
          const snapshot = await response.json();
          return route.fulfill({ status: 201, json: { ok: true, resumed: true, session_id: "current", session: snapshot.session } });
        }
        assert.equal(route.request().method(), "GET", "Observation cannot start work");
        const response = await route.fetch({ url: new URL(path + new URL(route.request().url()).search, origin).href });
        const snapshot = response.status() === 200 ? await response.json() : null;
        reads.push({ path, query: new URL(route.request().url()).search, status: response.status(), offset: snapshot?.message_page?.offset,
          sessions: snapshot?.sessions?.map(row => ({id: row.session_id, revision: row.transcript_revision})) });
        await route.fulfill({ response });
      });
    },
  });
  const { page, api } = workspace;
  const chat = () => page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
  await chat();
  const notice = page.getByRole("status").filter({hasText: "部分历史暂时无法读取"});
  await notice.waitFor({state: "visible"});
  assert.ok(reads.some(row => row.path === "/api/chat/sessions/old" && row.status === 503));
  assert.equal(await page.getByText("Earlier public report", {exact: true}).count(), 0,
    "The first real HTTP page must not masquerade as a complete history");
  const composer = page.getByLabel("向 LoopX 发送消息");
  await composer.fill("Keep this draft while original history recovers.");
  await mkdir(outputDir, {recursive: true});
  await page.screenshot({path: resolve(outputDir, "conversation-pagination-before-desktop.png"), animations: "disabled"});
  await command("recover");
  await notice.getByRole("button", {name: "重试读取", exact: true}).click();
  await page.getByText("Earlier public report", { exact: true }).waitFor({ state: "visible" });
  await page.getByText("Paged original row 449", {exact: true}).waitFor({state: "visible"});
  assert.equal(await page.getByText("Original full body. ".repeat(600).trim(), {exact: true}).count(), 1);
  await notice.waitFor({state: "hidden"});
  assert.deepEqual(reads.filter(row => row.path === "/api/chat/sessions/old" && row.status === 200 && typeof row.offset === "number").slice(-3).map(row => row.offset), [0, 200, 400]);
  assert.equal(await composer.inputValue(), "Keep this draft while original history recovers.");
  await page.screenshot({path: resolve(outputDir, "conversation-pagination-after-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
  await page.screenshot({path: resolve(outputDir, "conversation-pagination-after-narrow.png"), animations: "disabled"});
  await page.setViewportSize({width: 1512, height: 982});
  if (process.argv.includes("--pagination-only")) {
    assert.equal(api.turnRequests.length, 0);
    assert.deepEqual(await command("inspect"), {store_unchanged: true, turn_count: 0});
    console.log("conversation-pagination-browser: passed (packaged UI, real HTTP/store, 451 complete rows, second-page failure/retry, full body, draft and zero Turns)");
  } else {
  await command("result");
  await page.getByText("First checked result", { exact: true }).waitFor({ state: "visible", timeout: 12_000 });
  const firstLink = page.locator('a[href="https://example.org/forms/result"]');
  assert.equal(await firstLink.count(), 1, "A real stored return exposes its plain destination");
  assert.equal(await firstLink.getAttribute("target"), "_blank");
  await page.waitForTimeout(3500);
  const oldReadCount = () => reads.filter(row => row.path === "/api/chat/sessions/old").length;
  const settledReads = oldReadCount();
  await page.waitForTimeout(6500);
  assert.equal(oldReadCount(), settledReads, "Quiet completed transcripts must not be downloaded repeatedly");
  await page.locator(".personal-goal-link").first().click();
  const failedRead = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/sessions/old" && response.status() === 503);
  await command("fault");
  await command("revision");
  await failedRead;
  const recoveredRead = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/sessions/old" && response.status() === 200);
  await command("recover");
  assert.ok((await (await recoveredRead).text()).includes("Revised checked result"), "A non-current conversation recovers before navigation back");
  await page.locator(".personal-manager-link").click();
  await chat();
  assert.equal(await composer.inputValue(), "Keep this draft while original history recovers.",
    "Goal navigation and background recovery must preserve the steward draft");
  await page.getByText("Revised checked result", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await page.getByText("First checked result", { exact: true }).count(), 1);
  assert.equal(await page.getByText("Revised checked result", { exact: true }).count(), 1);
  assert.equal(await page.locator('a[href="https://example.org/forms/revision"]').count(), 1,
    "A recovered revision retains its own usable destination");
  assert.equal(await page.getByText("Current public report", { exact: true }).count(), 1);
  assert.equal(api.turnRequests.length, 0);
  assert.deepEqual(await command("inspect"), { store_unchanged: true, turn_count: 0 });
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({ path: resolve(outputDir, "conversation-return-revisions-desktop.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: resolve(outputDir, "conversation-return-revisions-mobile.png"), animations: "disabled" });
  console.log("conversation-return-browser: passed (packaged UI, real HTTP/store, quiet reads, background revision, read recovery, no duplicate or model Turn)");
  }
} catch (error) {
  console.error(JSON.stringify({recent_history_reads: reads.filter(row => row.path !== "/api/chat/sessions").slice(-10),
    recent_indexes: reads.filter(row => row.path === "/api/chat/sessions").slice(-8)}, null, 2));
  throw error;
} finally {
  await workspace?.page.unrouteAll({ behavior: "wait" });
  await workspace?.close();
  await browser?.close();
  server?.kill("SIGTERM");
  fixture.stdin.end();
  if (fixture.exitCode === null) await exited;
  lines.close();
}

async function verifyBoundReadRecovery() {
  // Exercise the real binding owner as well as the packaged reader. A provider
  // coming back must not restore a grant the owner explicitly disconnected.
  const child = spawn(resolveTestPython({repoRoot}), ["-u",
    "apps/presentation/dashboard/smoke/bound-conversation-http-fixture.py", "--steward"],
    {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
  const exited = once(child, "exit");
  const lines = createInterface({input: child.stdout});
  const iterator = lines[Symbol.asyncIterator]();
  let stderr = "", browser;
  child.stderr.on("data", chunk => stderr += String(chunk));
  async function next() {
    for (;;) {
      let timer;
      try {
        const line = await Promise.race([iterator.next(), new Promise((_, reject) => {
          timer = setTimeout(() => reject(Error(`Bound fixture timed out: ${stderr}`)), 20_000);
        })]);
        assert.equal(line.done, false, stderr);
        if (line.value.startsWith("{")) return JSON.parse(line.value);
      } finally {clearTimeout(timer);}
    }
  }
  async function command(value) {child.stdin.write(`${value}\n`); return next();}
  try {
    const {origin, bindingId, sessionId} = await next();
    // The first provider announces its identity before the second has started.
    // Serving HTTP is the shared fixture's completed-startup boundary.
    await waitForHttp(`${origin}/api/chat/lark/private-conversations`);
    await command("populate");
    const before = await command("inspect");
    browser = await launchBrowser(loadPlaywright().chromium);
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    await page.addInitScript(() => {
      if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "zh-CN");
    });
    const writes = [], errors = [];
    page.on("request", request => {if (request.method() !== "GET") writes.push(request.url());});
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/chat/?conversationBinding=${bindingId}&conversationSession=${sessionId}&view=conversation`);
    await page.getByRole("heading", {name: "合成报告 6 / Synthetic report", exact: true}).waitFor();
    const composer = page.getByLabel("向 LoopX 发送消息"), send = page.getByRole("button", {name: "发送", exact: true});
    const draft = "Keep this draft in the original conversation; read without sending.";
    await composer.fill(draft);
    const notice = page.getByRole("status").filter({hasText: "会话历史暂时无法读取"});
    // Source discovery can succeed while the original transcript read fails.
    // Inject only the HTTP outage; the binding and Session stay in real Core.
    const snapshotRoute = `**/api/chat/sessions/${sessionId}*`;
    await page.route(snapshotRoute, route => new URL(route.request().url()).pathname === `/api/chat/sessions/${sessionId}`
      ? route.fulfill({status: 503, json: {ok: false, error: "Synthetic transcript read outage"}})
      : route.fallback());
    await page.reload();
    await notice.waitFor();
    assert.equal(await send.isDisabled(), true);
    assert.equal(await page.getByText("今天的工作区很安静", {exact: true}).count(), 0,
      "A verified source with unreadable history is not an empty workspace");
    await mkdir(outputDir, {recursive: true});
    await page.screenshot({path: resolve(outputDir, "conversation-history-unavailable-desktop.png"), animations: "disabled"});
    await page.unroute(snapshotRoute);
    await notice.waitFor({state: "hidden"});
    await page.getByRole("heading", {name: "合成报告 6 / Synthetic report", exact: true}).waitFor();
    assert.equal(await composer.inputValue(), draft);
    await command("deny");
    await notice.waitFor();
    assert.equal(await send.isDisabled(), true);
    assert.equal(await composer.inputValue(), draft);
    await command("recover");
    await notice.waitFor({state: "hidden"});
    assert.equal(await send.isEnabled(), true);
    assert.equal(await composer.inputValue(), draft);
    assert.equal(await page.getByText("Synthetic private conversation: review the research notes.", {exact: true}).count(), 1);
    await command("revoke");
    await notice.waitFor();
    await command("recover");
    await notice.getByRole("button", {name: "重试读取", exact: true}).click();
    await notice.waitFor();
    assert.equal(await send.isDisabled(), true);
    assert.equal(await composer.inputValue(), draft);
    const sourceRead = await page.request.get(`${origin}/api/chat/sessions?conversation_binding_id=${bindingId}`);
    assert.equal(sourceRead.status(), 409);
    const connections = await (await page.request.get(`${origin}/api/chat/lark/private-conversations`)).json();
    assert.ok(!connections.connections.some(row => row.binding_id === bindingId));
    assert.equal(connections.connections.length, 1, "The other original source remains available");
    await page.reload();
    await page.getByRole("alert").filter({hasText: "原会话暂不可用"}).waitFor();
    await mkdir(outputDir, {recursive: true});
    await page.screenshot({path: resolve(outputDir, "conversation-binding-unavailable-desktop.png"), animations: "disabled"});
    assert.equal(await page.getByText("今天的工作区很安静", {exact: true}).count(), 0,
      "An unreadable original conversation is not an empty workspace");
    assert.equal(await page.getByTestId("runtime-readiness-line").count(), 0,
      "A failed source read cannot diagnose a missing execution runtime");
    assert.ok((await page.locator(".personal-timeline-empty").innerText()).includes("原会话暂不可用"));
    assert.equal(await composer.inputValue(), draft);
    assert.equal(await send.isDisabled(), true);
    assert.equal(await page.getByText("Unrelated desktop conversation — must remain separate.", {exact: true}).count(), 0);
    assert.equal(await page.getByText("Different private steward: keep this source separate.", {exact: true}).count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
    await page.screenshot({path: resolve(outputDir, "conversation-binding-unavailable-narrow.png"), animations: "disabled"});
    await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
    await page.reload();
    await page.getByRole("alert").filter({hasText: "Original conversation unavailable"}).waitFor();
    assert.ok((await page.locator(".personal-timeline-empty").innerText()).includes("Original conversation unavailable"));
    assert.equal(await page.getByTestId("runtime-readiness-line").count(), 0);
    assert.equal(await page.getByLabel("Send a message to LoopX").inputValue(), draft);
    assert.deepEqual(await command("inspect"), before, "Reads/retry/reopen preserve all Sessions, original messages and model requests");
    assert.deepEqual(writes, []);
    assert.deepEqual(errors, []);
    console.log("conversation-binding-recovery-browser: passed (real HTTP/Core, source recovery vs revocation, honest unavailable state, retained draft, no fallback or writes)");
  } finally {
    await browser?.close();
    child.stdin.end("stop\n");
    if (child.exitCode === null) await exited;
    lines.close();
  }
}

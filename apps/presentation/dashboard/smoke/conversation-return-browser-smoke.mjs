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

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
try {
  const { origin } = await next();
  await command("recover");
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  const reads = [];
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
        reads.push({ path, status: response.status() });
        await route.fulfill({ response });
      });
    },
  });
  const { page, api } = workspace;
  const chat = () => page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
  await chat();
  await page.getByText("Earlier public report", { exact: true }).waitFor({ state: "visible" });
  await command("result");
  await page.getByText("First checked result", { exact: true }).waitFor({ state: "visible", timeout: 12_000 });
  await page.waitForTimeout(3500);
  const oldReadCount = () => reads.filter(row => row.path === "/api/chat/sessions/old").length;
  const settledReads = oldReadCount();
  await page.waitForTimeout(6500);
  assert.equal(oldReadCount(), settledReads, "Quiet completed transcripts must not be downloaded repeatedly");
  await page.locator(".personal-goal-link").first().click();
  await command("fault");
  await command("revision");
  const failedRead = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/sessions/old" && response.status() === 503);
  await failedRead;
  const recoveredRead = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/sessions/old" && response.status() === 200);
  await command("recover");
  assert.ok((await (await recoveredRead).text()).includes("Revised checked result"), "A non-current conversation recovers before navigation back");
  await page.locator(".personal-manager-link").click();
  await chat();
  await page.getByText("Revised checked result", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await page.getByText("First checked result", { exact: true }).count(), 1);
  assert.equal(await page.getByText("Revised checked result", { exact: true }).count(), 1);
  assert.equal(await page.getByText("Current public report", { exact: true }).count(), 1);
  assert.equal(api.turnRequests.length, 0);
  assert.deepEqual(await command("inspect"), { store_unchanged: true, turn_count: 0 });
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({ path: resolve(outputDir, "conversation-return-revisions-desktop.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: resolve(outputDir, "conversation-return-revisions-mobile.png"), animations: "disabled" });
  console.log("conversation-return-browser: passed (packaged UI, real HTTP/store, quiet reads, background revision, read recovery, no duplicate or model Turn)");
} finally {
  await workspace?.close();
  await browser?.close();
  server?.kill("SIGTERM");
  fixture.stdin.end();
  if (fixture.exitCode === null) await exited;
  lines.close();
}

#!/usr/bin/env node
// A delayed or failed Goal must not hide the Workspace or its ready peers.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanupBrowserSmoke, launchBrowser, loadPlaywright, waitForHttp } from "./dashboard-browser-smoke-support.mjs";
import { resolveTestPython } from "../scripts/test-python.mjs";
const root = fileURLToPath(new URL("../", import.meta.url));
process.env.LOOPX_PLAYWRIGHT_PACKAGE ??= resolve(root, "apps/presentation/dashboard/node_modules/playwright");
const require = createRequire(import.meta.url);
const port = Number(process.env.LOOPX_PROGRESSIVE_PORT ?? "5204");
const origin = `http://127.0.0.1:${port}`;
const directory = { ok: true, schema_version: "loopx_workspace_directory_v1", registry_revision: "r1",
  goals: ["slow", "ready", "retry", "archived"].map((id) => ({ id, display_name: `${id} project`, activation_state: id === "archived" ? "stopped" : "active", registry_member: true })) };
function snapshot(id, readyText = "Review public evidence") {
  const payload = structuredClone(require(resolve(root, "examples/status.example.json")));
  payload.run_history.goals = [{ ...payload.run_history.goals[0], id, display_name: `${id} project`, activation_state: id === "archived" ? "stopped" : "active", registry_member: true }];
  for (const item of payload.attention_queue.items) item.goal_id = id;
  if (id === "ready") {
    const native = { done: false, text: readyText, todo_id: "todo_native_ready" };
    payload.attention_queue.items[0].agent_todos.items.unshift(native);
    payload.attention_queue.items[0].project_asset = {
      owner: "agent", gate: "none", next_action: "review", stop_condition: "accepted",
      agent_todos: { items: [{ ...native, index: null }],
        recent_completed_advancement_items: [{ ...native, text: "Completed public evidence", todo_id: "todo_native_done", done: true }] },
    };
    payload.todo_index.items.unshift({ ...native, index: null, goal_id: id });
  }
  payload.workspace_registry_revision = directory.registry_revision;
  return payload;
}
const server = spawn(resolveTestPython(), ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", resolve(root, "loopx/web")], { stdio: "ignore" });
let browser;
let releaseSlow;
const slowGate = new Promise((done) => { releaseSlow = done; });
try {
  await waitForHttp(`${origin}/chat/`);
  browser = process.env.LOOPX_PROGRESSIVE_ENGINE === "webkit"
    ? await loadPlaywright().webkit.launch({ headless: true })
    : process.platform === "win32" ? await loadPlaywright().chromium.launch({ headless: true })
    : await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let readyFailures = 1;
  let retryFails = true;
  let revisionMismatch = false;
  let active = 0;
  let peak = 0;
  const requested = [];
  await page.route("**/status.json*", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("view") === "workspace-directory") {
      return route.fulfill({ json: directory });
    }
    const id = url.searchParams.get("goal_id");
    assert.ok(id, "supported server must not receive an aggregate status request");
    requested.push(id); active++; peak = Math.max(peak, active);
    if (id === "slow") await slowGate;
    active--;
    if (id === "ready" && readyFailures-- > 0) return route.fulfill({ status: 503, json: { ok: false } });
    return route.fulfill(id === "retry" && retryFails ? { status: 503, json: { ok: false } } : { json: revisionMismatch && id === "retry" ? { ...snapshot(id), workspace_registry_revision: "changed" } : snapshot(id) });
  });
  await page.route("**/api/**", (route) => route.fulfill({ status: 503, json: { ok: false } }));
  const start = Date.now();
  await page.goto(`${origin}/chat/?statusUrl=${encodeURIComponent(`${origin}/status.json`)}`);
  await page.locator('.personal-goal-link').filter({ hasText: "ready project" }).waitFor();
  const directoryMs = Date.now() - start;
  await page.locator('.personal-goal-link').filter({ hasText: "ready project" }).click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="goal-status-loading"]'));
  assert.equal(await page.locator('.personal-goal-link').filter({ hasText: "slow project" }).innerText().then((s) => /加载|Loading/.test(s)), true);
  await page.locator('.personal-goal-link').filter({ hasText: "retry project" }).click();
  await page.getByTestId("goal-status-loading").getByRole("button").waitFor();
  assert.ok(requested.includes("retry"), "a blocked first Goal must not block the queue");
  assert.equal(requested.filter((id) => id === "retry").length, 3, "ordinary 5xx retains three attempts");
  const out = resolve(root, "output/playwright/workspace-progressive");
  await mkdir(out, { recursive: true });
  await page.screenshot({ path: resolve(out, "partial-desktop.png") });
  releaseSlow();
  await page.waitForFunction(() => [...document.querySelectorAll('.personal-goal-link')].find((el) => el.textContent.includes('slow project'))?.textContent.match(/加载|Loading/) === null);
  await page.waitForFunction(() => [...document.querySelectorAll('.personal-goal-link')].find((el) => el.textContent.includes('ready project'))?.textContent.match(/加载|Loading/) === null);
  retryFails = false;
  revisionMismatch = true;
  await page.getByTestId("goal-status-loading").getByRole("button").click();
  while (requested.filter((id) => id === "retry").length < 2) {
    await new Promise((done) => setTimeout(done, 10));
  }
  await page.getByTestId("goal-status-loading").getByRole("button").waitFor();
  revisionMismatch = false;
  const clickedRevisionRetry = await page.evaluate(() => {
    const button = document.querySelector('[data-testid="goal-status-loading"] button');
    if (!button) return false;
    button.click();
    return true;
  });
  assert.equal(clickedRevisionRetry, true, "revision error should keep a manual retry button mounted");
  await page.getByTestId("goal-status-loading").waitFor({ state: "hidden" });
  assert.ok(requested.filter((id) => id === "ready").length >= 2, "a transient service error should recover automatically");
  assert.ok(!requested.includes("archived"), "stopped history must not compete with the active first screen");
  await page.locator(".personal-stopped-goals summary").click();
  await Promise.all([
    page.waitForResponse((response) => response.url().includes("goal_id=archived")),
    page.locator('.personal-goal-link').filter({ hasText: "archived project" }).click(),
  ]);
  await page.getByTestId("goal-status-loading").waitFor({ state: "hidden" });
  assert.ok(requested.includes("archived"), "selected stopped Goal loads on demand");
  assert.ok(peak <= 2, `bounded fanout exceeded: ${peak}`);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(out, "ready-mobile.png") });
  assert.deepEqual(errors, []);
  for (const locale of ["zh-CN", "en"]) {
    const accessPage = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    await accessPage.addInitScript((value) => localStorage.setItem("loopx-pw-locale", value), locale);
    accessPage.on("pageerror", (error) => errors.push(error.message));
    const accessDirectory = { ...directory, goals: ["ready", "access"].map((id) => ({
      id, display_name: `${id} project`, activation_state: "active", registry_member: true,
    })) };
    const counts = { ready: 0, access: 0 };
    let denied = true;
    let readyText = "Review public evidence";
    let historyText = "Completed public evidence";
    let historyRequests = 0;
    await accessPage.route("**/status.json*", (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.has("view")) return route.fulfill({ json: accessDirectory });
      const id = url.searchParams.get("goal_id");
      assert.ok(id in counts);
      counts[id]++;
      return route.fulfill(id === "access" && denied
        ? { status: 500, json: { ok: false, error_code: "workspace_status_access_denied" } }
        : { json: snapshot(id, readyText) });
    });
    await accessPage.route("**/api/**", (route) => route.fulfill({ status: 503, json: { ok: false } }));
    await accessPage.route("**/api/chat/completed-todos?*", (route) => {
      historyRequests++;
      return route.fulfill({ json: { ok: true, total: 1, next_cursor: null, items: [{
        todo_id: "todo_native_done", text: historyText, claimed_by: null,
        evidence: "Verified public evidence", priority: "P2", task_class: "advancement_task",
      }] } });
    });
    await accessPage.goto(`${origin}/chat/`);
    await accessPage.locator(".personal-goal-link").filter({ hasText: "ready project" }).click();
    await accessPage.getByTestId("goal-status-loading").waitFor({ state: "hidden" });
    await accessPage.locator(".personal-goal-link").filter({ hasText: "access project" }).click();
    const panel = accessPage.getByTestId("goal-status-loading");
    await panel.getByRole("button").waitFor();
    assert.match(await panel.innerText(), locale === "en" ? /account running that source/ : /来源运行账户/);
    await accessPage.screenshot({ path: resolve(out, `access-${locale}-desktop.png`) });
    await accessPage.setViewportSize({ width: 390, height: 844 });
    await accessPage.screenshot({ path: resolve(out, `access-${locale}-mobile.png`) });
    // Outwait the first backoff: terminal access failures must not retry themselves.
    await accessPage.waitForTimeout(1_200);
    assert.deepEqual(counts, { ready: 1, access: 1 });
    await accessPage.setViewportSize({ width: 1440, height: 960 });
    // A workspace refresh must observe external Todo changes even while a
    // different Goal still fails. Directory lifecycle identity is unchanged.
    await accessPage.locator(".personal-goal-link").filter({ hasText: "ready project" }).click();
    await accessPage.locator(".personal-goal-tabs").getByRole("button", { name: locale === "en" ? "Tasks" : "任务", exact: true }).click();
    await accessPage.getByText(readyText, { exact: true }).waitFor();
    const history = accessPage.getByTestId("completed-task-lane");
    await history.getByText(historyText, { exact: true }).waitFor();
    await accessPage.getByRole("button", { name: locale === "en" ? "List" : "列表", exact: true }).click();
    const expandHistory = history.getByRole("button", { name: locale === "en" ? /Completed/ : /已完成/ }).first();
    await expandHistory.click();
    assert.equal(await expandHistory.getAttribute("aria-expanded"), "true");
    const beforeHistoryRefresh = historyRequests;
    readyText = "Review updated public evidence";
    historyText = "Updated completed public evidence";
    await accessPage.getByRole("button", { name: locale === "en" ? "Refresh status" : "刷新状态", exact: true }).click();
    await accessPage.getByText(readyText, { exact: true }).waitFor({ timeout: 5_000 });
    await history.getByText(historyText, { exact: true }).waitFor();
    assert.equal(await history.getByText("Completed public evidence", { exact: true }).count(), 0);
    assert.equal(await expandHistory.getAttribute("aria-expanded"), "true", "refresh preserves list expansion");
    assert.ok(historyRequests > beforeHistoryRefresh, "full refresh opens a fresh history cursor even at equal count");
    assert.deepEqual(counts, { ready: 2, access: 2 }, "workspace refresh re-reads successful peers despite a failed Goal");
    await accessPage.locator(".personal-goal-link").filter({ hasText: "access project" }).click();
    denied = false;
    await panel.getByRole("button").click();
    await panel.waitFor({ state: "hidden" });
    assert.deepEqual(counts, { ready: 2, access: 3 }, "manual retry preserves successful peers at the same revision");
    denied = true;
    await accessPage.getByRole("button", { name: locale === "en" ? "Refresh status" : "刷新状态", exact: true }).click();
    await panel.getByRole("button").waitFor();
    assert.deepEqual(counts, { ready: 3, access: 4 }, "a failed re-read exposes its error instead of presenting the retained snapshot as current");
    await accessPage.close();
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ ok: true, directory_ms: directoryMs, peak_concurrent_requests: peak, checks: ["directory-before-slow-goal", "ready-peer-usable", "queue-progress", "isolated-failure", "registry-revision-fence", "retry", "service-restart-recovery", "lazy-stopped-goals", "access-no-auto-retry", "access-manual-recovery-preserves-peers", "full-refresh-observes-external-todo", "full-refresh-invalidates-completed-history", "failed-reread-replaces-stale-snapshot", "bilingual-access", "mobile", "no-render-errors"] }));
} finally {
  releaseSlow();
  await cleanupBrowserSmoke({ browser, server, fixturePaths: [] });
}

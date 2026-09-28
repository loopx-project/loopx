#!/usr/bin/env node
// Exercise the real status parser, workspace mapper and selected detail drawer.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launchBrowser, loadPlaywright, startViteDashboardServer, waitForHttp } from "./dashboard-browser-smoke-support.mjs";
import { resolveTestPython } from "../scripts/test-python.mjs";

const require = createRequire(import.meta.url);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dashboardDir = resolve(root, "apps/presentation/dashboard");
const port = Number(process.env.LOOPX_ATTENTION_DETAILS_PORT ?? 5293);
const packaged = process.env.LOOPX_ATTENTION_DETAILS_PACKAGED === "1";
const output = resolve(root, "output/playwright/attention-details");
const server = packaged
  ? spawn(resolveTestPython(), ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", resolve(root, "loopx/web")], { stdio: "ignore" })
  : startViteDashboardServer({ dashboardDir, port });
const url = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`;
const requestBody = "Review the public release evidence and the selected publication channel. ".repeat(5) + "Publish version 2.0 to stable only after acceptance.";
let browser;
try {
  await waitForHttp(url);
  const { chromium } = loadPlaywright();
  browser = await launchBrowser(chromium);
  await mkdir(output, { recursive: true });
  for (const [locale, viewport, readOnly] of [["zh-CN", { width: 1440, height: 1000 }, false], ["en", { width: 390, height: 844 }, false], ["en", { width: 1440, height: 1000 }, true]]) {
    const page = await browser.newPage({ viewport });
    await page.addInitScript((value) => localStorage.setItem("loopx-pw-locale", value), locale);
    page.on("pageerror", (error) => console.error(error.message));
    let state = "open";
    let mutationCount = 0;
    const writes = [];
    const statusUrl = `http://127.0.0.1:${port}/${readOnly ? "remote-status" : "status"}.json`;
    if (readOnly) await page.addInitScript((statusUrl) => localStorage.setItem("loopx-status-source-catalog-v1", JSON.stringify({
      schemaVersion: 1, sources: [{ kind: "ssh_tunnel", label: "Read-only fixture", statusUrl }],
    })), statusUrl);
    await page.route("**/api/**", (route) => {
      if (route.request().method() !== "GET") writes.push(route.request().url());
      if (route.request().method() !== "GET" && new URL(route.request().url()).pathname.startsWith("/api/actions")) mutationCount += 1;
      return route.fulfill({ status: 200, json: { ok: true, agents: [], sessions: [], contexts: [], connections: [], items: [] } });
    });
    await page.route("**/ssh-hosts*", (route) => route.fulfill({ json: { hosts: [] } }));
    await page.route(`${statusUrl}*`, (route) => {
      if (state === "offline") return route.fulfill({ status: 503, json: { error: "Synthetic source unavailable" } });
      const fixture = structuredClone(require(resolve(root, "examples/status.example.json")));
      const queue = fixture.attention_queue.items[0];
      const goal = fixture.run_history.goals.find((item) => item.id === queue.goal_id);
      goal.activation_state = "active";
      goal.registry_member = true;
      fixture.run_history.goals = [goal];
      fixture.attention_queue.items = [queue];
      queue.waiting_on = "user_or_controller";
      if (queue.project_asset) delete queue.project_asset.user_todos;
      const original = {
        index: 1, todo_id: "todo_original", role: "user", task_class: "user_gate",
        done: state === "superseded", status: state === "superseded" ? "done" : "open",
        title: "Release review", text: requestBody, note: "A direction choice is needed before todo_target can continue.",
        evidence: "review:bounded-validation", blocks_agent: "worker-one", unblocks_todo_id: "todo_target",
        decision_scope: { schema_version: "decision_scope_v0", kind: "direction", granularity: "action", scope_key: "route-one" },
        ...(state === "superseded" ? { superseded_by: "todo_replacement" } : {}),
      };
      const replacement = { ...original, index: 2, todo_id: "todo_replacement", title: "Review the replacement direction", text: "Review the replacement direction", done: false, status: "open", superseded_by: undefined };
      queue.user_todos = { items: state === "missing" ? [replacement] : state === "superseded" ? [original, replacement] : [original], total_count: 2, open_count: 1 };
      if (locale === "en") queue.project_asset = { owner: "fixture-owner", gate: "pending", next_action: "Review direction", stop_condition: "Await decision", ...(queue.project_asset ?? {}), user_todos: { items: queue.user_todos.items, total: 2, open: 1 } };
      return route.fulfill({ json: fixture });
    });
    await page.goto(readOnly ? url.replace("/status.json", encodeURIComponent(statusUrl)) : url, { waitUntil: "networkidle" });
    await page.getByTestId("personal-goal-home").waitFor({ state: "visible", timeout: 10000 }).catch(async (error) => { console.error((await page.locator("body").innerText()).slice(0, 3000)); throw error; });
    await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
    await page.locator(".personal-object-list").first().getByRole("button").first().click();
    const drawer = page.locator(".personal-drawer-body");
    for (const value of [requestBody, "worker-one", "todo_target", "direction · action · route-one", "review:bounded-validation", "A direction choice is needed before todo_target can continue."]) {
      await drawer.getByText(value, { exact: true }).waitFor({ state: "visible" });
    }
    await page.screenshot({ path: resolve(output, `${packaged ? "packaged" : "dev"}-${locale}${readOnly ? "-readonly" : ""}.png`), fullPage: false });
    if (mutationCount) throw new Error("Reading attention details triggered a mutation");
    if (readOnly) {
      if (await drawer.locator(".personal-primary-action").count()) throw new Error("Read-only detail exposed decision action");
      if (writes.length) throw new Error(`Read-only inspection wrote to local APIs: ${writes.join(", ")}`);
      await page.close(); continue;
    }
    if (viewport.width < 640) { await page.close(); continue; }
    state = "offline";
    await page.locator(".personal-refresh-control .personal-icon-button").click();
    await drawer.getByText(/来源尚未确认当前事项|The source has not confirmed this item/).waitFor({ state: "visible", timeout: 5000 });
    if (await drawer.locator(".personal-primary-action").count()) throw new Error("Failed source still exposes decisions from retained data");
    state = "superseded";
    // Existing refresh control rereads the status while preserving drawer selection.
    await page.locator(".personal-refresh-control .personal-icon-button").click();
    const replacementButton = drawer.getByRole("button", { name: /打开替代事项|Open replacement/ });
    await replacementButton.waitFor({ state: "visible" });
    if (await drawer.locator(".personal-primary-action").count()) throw new Error("Superseded selection still exposes decision actions");
    await replacementButton.click();
    await drawer.getByRole("heading", { name: "Review the replacement direction" }).waitFor({ state: "visible" });
    state = "missing";
    // Remove the selected identity entirely, retaining another unrelated request.
    await page.unroute(`${statusUrl}*`);
    await page.route(`${statusUrl}*`, (route) => {
      const fixture = structuredClone(require(resolve(root, "examples/status.example.json")));
      fixture.attention_queue.items.forEach((item) => { item.user_todos = { items: [], total_count: 0, open_count: 0 }; if (item.project_asset) delete item.project_asset.user_todos; });
      return route.fulfill({ json: fixture });
    });
    await page.locator(".personal-refresh-control .personal-icon-button").click();
    await drawer.getByText(/来源尚未确认当前事项|The source has not confirmed this item/).waitFor({ state: "visible" });
    if (await drawer.locator(".personal-primary-action").count()) throw new Error("Missing selection still exposes decision actions");
    if (mutationCount) throw new Error("Readback/successor navigation triggered a mutation");
    await page.close();
  }
  console.log(`attention-details-browser-smoke (${packaged ? "packaged" : "development"}): ok`);
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}

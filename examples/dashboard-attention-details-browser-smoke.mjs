#!/usr/bin/env node
// Exercise the real status parser, workspace mapper and selected detail drawer.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
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
const requestBody = "Review **version 2.0** for the stable channel.\n\n- Publish after independent acceptance.\n- Keep the preview channel available.\n\nPublish version 2.0 to stable only after acceptance.";
const reason = "Recommend **waiting for acceptance**. Publishing now would omit the independent check; waiting keeps the preview usable.";
const evidence = "[Release checklist](https://example.org/release-checklist)\n\nIndependent acceptance is still pending.";
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
    let preview;
    const writes = [];
    const statusUrl = `http://127.0.0.1:${port}/${readOnly ? "remote-status" : "status"}.json`;
    if (readOnly) await page.addInitScript((statusUrl) => localStorage.setItem("loopx-status-source-catalog-v1", JSON.stringify({
      schemaVersion: 1, sources: [{ kind: "ssh_tunnel", label: "Read-only fixture", statusUrl }],
    })), statusUrl);
    await page.route("**/api/**", (route) => {
      if (route.request().method() !== "GET") writes.push(route.request().url());
      if (route.request().method() !== "GET" && new URL(route.request().url()).pathname.startsWith("/api/actions")) mutationCount += 1;
      if (new URL(route.request().url()).pathname === "/api/actions/preview") {
        preview = route.request().postDataJSON();
        return route.fulfill({ status: 201, json: { ok: true, proposal: {
          schema_version: "loopx_chat_action_proposal_v1", proposal_id: "proposal-decision", action_kind: preview.action_kind,
          summary: preview.summary, normalized_parameters: preview.normalized_parameters, context: preview.context,
          expected_state_fingerprint: "fixture-r1", permission_classification: "durable_write", validation_evidence: ["fixture validation"],
          available_transitions: ["apply", "cancel"], status: "preview_ready", receipt: null, stale: null,
          created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:00Z",
        } } });
      }
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
        title: "Release review", text: requestBody, note: reason, updated_at: "2026-08-13T00:30:00Z",
        evidence, blocks_agent: "worker-one", unblocks_todo_id: "todo_target",
        decision_scope: { schema_version: "decision_scope_v0", kind: "direction", granularity: "action", scope_key: "route-one" },
        ...(state === "superseded" ? { superseded_by: "todo_replacement" } : {}),
      };
      const replacement = { ...original, index: 2, todo_id: "todo_replacement", title: "Review the replacement direction", text: "Review the replacement direction", done: false, status: "open", superseded_by: undefined };
      if (state === "unsafe") original.text += '\n\n<img src=x onerror="window.decisionHtmlExecuted=true">\n\n[Unsafe](javascript:alert(1))';
      const unrelated = { ...replacement, todo_id: "todo_unrelated", text: "Review another step", title: "Review another step" };
      queue.user_todos = { items: state === "summary" ? [] : state === "missing" ? [unrelated] : state === "superseded" ? [original, replacement] : [original], total_count: 2, open_count: 1 };
      if (state === "summary") goal.latest_runs = [{ generated_at: "2026-08-13T01:00:00Z", goal_id: goal.id, classification: "operator_gated",
        operator_gate: { operator_question: "Review candidate direction", reason_summary: "Independent acceptance is pending" } }];
      if (locale === "en") queue.project_asset = { owner: "fixture-owner", gate: "pending", next_action: "Review direction", stop_condition: "Await decision", ...(queue.project_asset ?? {}), user_todos: { items: queue.user_todos.items, total: 2, open: 1 } };
      return route.fulfill({ json: fixture });
    });
    await page.goto(readOnly ? url.replace("/status.json", encodeURIComponent(statusUrl)) : url, { waitUntil: "networkidle" });
    await page.getByTestId("personal-goal-home").waitFor({ state: "visible", timeout: 10000 }).catch(async (error) => { console.error((await page.locator("body").innerText()).slice(0, 3000)); throw error; });
    await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
    const entry = page.locator(".personal-object-list").first().getByRole("button").first();
    await entry.click();
    const drawer = page.locator(".personal-drawer-body");
    await page.screenshot({ path: resolve(output, `${packaged ? "packaged" : "dev"}-${locale}${readOnly ? "-readonly" : ""}.png`), fullPage: false });
    assert.equal(await drawer.locator(".personal-detail-card").count(), 1, "One request brief replaces duplicated summary and detail cards");
    const brief = drawer.locator(".personal-attention-brief");
    await brief.getByText("Publish version 2.0 to stable only after acceptance.", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await brief.locator("strong").filter({ hasText: "version 2.0" }).count(), 1);
    assert.equal(await brief.locator("li").count(), 2);
    assert.equal(await brief.getByRole("link", { name: "Release checklist" }).getAttribute("href"), "https://example.org/release-checklist");
    assert.ok(!(await drawer.innerText()).includes("todo_original"), "Default decision view hides technical identifiers");
    const dimensions = await drawer.evaluate(element => ({ width: element.clientWidth, content: element.scrollWidth }));
    assert.ok(dimensions.content <= dimensions.width + 1, "Decision text fits the narrow drawer");
    const diagnostics = brief.locator("summary");
    await diagnostics.focus();
    await diagnostics.press("Enter");
    for (const value of ["worker-one", "todo_target", "direction · action · route-one"]) await drawer.getByText(value, { exact: true }).waitFor({ state: "visible" });
    await diagnostics.press("Enter");
    await drawer.getByText("todo_target", { exact: true }).waitFor({ state: "hidden" });
    if (mutationCount) throw new Error("Reading attention details triggered a mutation");
    if (readOnly) {
      if (await drawer.locator(".personal-primary-action").count()) throw new Error("Read-only detail exposed decision action");
      if (writes.length) throw new Error(`Read-only inspection wrote to local APIs: ${writes.join(", ")}`);
      await page.close(); continue;
    }
    if (viewport.width < 640) { await page.close(); continue; }
    const more = drawer.locator(".personal-compact-menu > summary");
    await more.focus(); await more.press("Tab");
    assert.ok(await page.locator(".personal-drawer-close").evaluate(element => element === document.activeElement), "Closed menus do not strand keyboard focus");
    await drawer.getByRole("button", { name: /^(批准|Approve)$/ }).click();
    await page.locator('[data-context-kind="proposal"]').waitFor({ state: "visible" });
    assert.equal(preview.action_kind, "gate.resolve");
    assert.equal(preview.normalized_parameters.goal_id, "loopx-meta");
    assert.equal(preview.normalized_parameters.todo_id, "todo_original");
    assert.equal(preview.normalized_parameters.decision, "approve");
    assert.equal(preview.normalized_parameters.agent_id, "worker-one", "The decision is recorded for the Agent the request blocks");
    await page.locator(".personal-drawer-close").press("Escape");
    await entry.click();
    state = "unsafe";
    await page.locator(".personal-refresh-control .personal-icon-button").click();
    await drawer.getByText(/onerror=/).waitFor({ state: "visible" });
    assert.equal(await drawer.locator("img, script, a[href^='javascript:']").count(), 0, "Source prose cannot execute HTML or script links");
    assert.equal(await page.evaluate(() => window.decisionHtmlExecuted), undefined);
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
    await drawer.getByText("Review the replacement direction", { exact: true }).waitFor({ state: "visible" });
    state = "missing";
    // Remove the selected identity entirely, retaining another unrelated request.
    await page.locator(".personal-refresh-control .personal-icon-button").click();
    await drawer.getByText(/来源尚未确认当前事项|The source has not confirmed this item/).waitFor({ state: "visible" });
    if (await drawer.locator(".personal-primary-action").count()) throw new Error("Missing selection still exposes decision actions");
    state = "summary";
    await page.goto(url, { waitUntil: "networkidle" });
    await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
    await page.locator(".personal-object-list").first().getByRole("button").first().click();
    await drawer.getByText(/目前只有摘要|Only a summary is available/).waitFor({ state: "visible" });
    assert.equal(await drawer.getByRole("heading", { name: /现有摘要|Available summary/ }).count(), 1);
    await page.locator(".personal-drawer-close").press("Escape");
    await page.waitForFunction(() => !document.querySelector('[data-context-kind="attention"]'));
    assert.ok(await entry.evaluate(element => element === document.activeElement), "Escape restores the originating request focus");
    assert.equal(mutationCount, 1, "Reading, disclosures, refresh and replacement cannot issue extra decisions");
    assert.equal(writes.filter(url => new URL(url).pathname.startsWith("/api/actions/") && !url.endsWith("/api/actions/preview")).length, 0, "No decision was applied");
    await page.close();
  }
  console.log(`attention-details-browser-smoke (${packaged ? "packaged" : "development"}): ok`);
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}

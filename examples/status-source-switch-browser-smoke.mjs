#!/usr/bin/env node
// Regression smoke for local/SSH status-source request ordering.

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  cleanupBrowserSmoke,
  launchBrowser,
  loadPlaywright,
  startViteDashboardServer,
  waitForHttp,
} from "./dashboard-browser-smoke-support.mjs";
import { resolveTestPython } from "../scripts/test-python.mjs";

const require = createRequire(import.meta.url);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dashboardDir = resolve(repoRoot, "apps/presentation/dashboard");
const outputDir = resolve(repoRoot, "output/playwright/status-source-switch");
const port = Number(process.env.LOOPX_STATUS_SOURCE_SWITCH_PORT ?? "5197");
const packaged = process.env.LOOPX_STATUS_SOURCE_SWITCH_PACKAGED === "1";

function startServer() {
  if (packaged) {
    return spawn(resolveTestPython(), [
      "-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", resolve(repoRoot, "loopx/web"),
    ], {
      cwd: repoRoot,
      env: { ...process.env },
      stdio: "ignore",
    });
  }
  return startViteDashboardServer({ dashboardDir, port });
}

function statusPayload(goalId, displayName) {
  const payload = structuredClone(require(resolve(repoRoot, "examples/status.example.json")));
  const goal = payload.run_history.goals[0];
  goal.id = goalId;
  goal.display_name = displayName;
  for (const item of payload.attention_queue.items ?? []) {
    item.goal_id = goalId;
    for (const todo of item.agent_todos?.items ?? []) todo.goal_id = goalId;
    for (const todo of item.user_todos?.items ?? []) todo.goal_id = goalId;
  }
  return payload;
}

function deferred() {
  let resolvePromise;
  const promise = new Promise((resolveDeferred) => { resolvePromise = resolveDeferred; });
  return { promise, resolve: resolvePromise };
}

async function selectedSourceLabel(select) {
  return select.locator(".personal-select-value").innerText();
}

async function selectSource(page, select, label) {
  await select.click();
  await page.getByRole("listbox", { name: "选择控制面来源" }).getByRole("option", { name: label, exact: true }).click();
}

async function main() {
  const { chromium } = loadPlaywright();
  await mkdir(outputDir, { recursive: true });
  const server = startServer();
  let browser;
  try {
    const appUrl = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}`;
    await waitForHttp(appUrl);
    browser = await launchBrowser(chromium);
    for (const storageFailure of ["getter", "methods"]) {
      const isolated = await browser.newPage({ locale: "zh-CN", viewport: { width: 1512, height: 982 } });
      const errors = [];
      isolated.on("pageerror", (error) => errors.push(error.message));
      await isolated.addInitScript((failure) => {
        const deny = () => { throw new DOMException("Storage unavailable", "SecurityError"); };
        if (failure === "getter") {
          Object.defineProperty(window, "localStorage", { get: deny });
        } else {
          Storage.prototype.getItem = deny;
          Storage.prototype.setItem = deny;
        }
      }, storageFailure);
      await isolated.route(`http://127.0.0.1:${port}/ssh-hosts`, (route) => route.fulfill({
        json: { ok: true, schema_version: "ssh_host_catalog_v0", hosts: [] },
      }));
      await isolated.route(`http://127.0.0.1:${port}/status.json*`, (route) => route.fulfill({
        json: statusPayload("local-goal", "Local Goal Only"),
      }));
      await isolated.route("http://127.0.0.1:8976/status.json*", (route) => route.fulfill({
        json: statusPayload("remote-b-goal", "Remote B Goal Only"),
      }));
      await isolated.goto(appUrl, { waitUntil: "networkidle" });
      await isolated.getByText("Local Goal Only", { exact: true }).first().waitFor({ timeout: 10_000 });
      const select = isolated.getByRole("combobox", { name: "选择控制面来源" });
      if (await selectedSourceLabel(select) !== "本机") throw new Error(`${storageFailure}: missing local source fallback`);
      await isolated.getByRole("button", { name: "添加 SSH 隧道来源" }).click();
      await isolated.getByRole("tab", { name: "手动 URL" }).click();
      await isolated.getByLabel("名称").fill("Session Remote");
      await isolated.getByLabel("本地转发 URL").fill("http://127.0.0.1:8976/status.json");
      await isolated.getByRole("button", { name: "添加只读来源" }).click();
      await isolated.getByText("Remote B Goal Only", { exact: true }).first().waitFor({ timeout: 10_000 });
      await isolated.locator(".personal-read-only-source", { hasText: "Session Remote" }).waitFor();
      await selectSource(isolated, select, "本机");
      await isolated.getByText("Local Goal Only", { exact: true }).first().waitFor({ timeout: 10_000 });
      await isolated.reload({ waitUntil: "networkidle" });
      await isolated.getByText("Local Goal Only", { exact: true }).first().waitFor({ timeout: 10_000 });
      if (errors.length) throw new Error(`${storageFailure}: uncaught page errors: ${errors.join("; ")}`);
      await isolated.close();
    }
    const page = await browser.newPage({ locale: "zh-CN", viewport: { width: 1512, height: 982 } });
    const state = {
      ensureGates: new Map(),
      ensureStartedByHost: new Map(),
      statusGates: new Map(),
      statusRequestsByPort: new Map(),
      statusStartedByPort: new Map(),
      lifecycleRequests: [],
    };
    const payloads = new Map([
      ["local", statusPayload("local-goal", "Local Goal Only")],
      ["8766", statusPayload("local-goal", "Local Goal Only")],
      ["8876", statusPayload("remote-a-goal", "Remote A Goal Only")],
      ["8976", statusPayload("remote-b-goal", "Remote B Goal Only")],
    ]);

    await page.addInitScript(() => {
      localStorage.setItem("loopx-status-source-catalog-v1", JSON.stringify({
        schemaVersion: 1,
        sources: [
          { hostAlias: "remote-a", kind: "ssh_tunnel", label: "Remote A", statusUrl: "http://127.0.0.1:8876/status.json" },
          { hostAlias: "remote-b", kind: "ssh_tunnel", label: "Remote B", statusUrl: "http://127.0.0.1:8976/status.json" },
        ],
      }));
    });
    await page.route(`http://127.0.0.1:${port}/ssh-hosts`, (route) => route.fulfill({
      contentType: "application/json",
      json: { ok: true, schema_version: "ssh_host_catalog_v0", hosts: [{ alias: "remote-a" }, { alias: "remote-b" }] },
      status: 200,
    }));
    await page.route(`http://127.0.0.1:${port}/api/ssh-source/ensure`, async (route) => {
      const body = route.request().postDataJSON();
      const host = String(body.host_alias ?? "");
      state.ensureStartedByHost.get(host)?.();
      await state.ensureGates.get(host);
      await route.fulfill({
        contentType: "application/json",
        json: { ok: true, remote_started: true, status_url: `http://127.0.0.1:${body.local_port}/status.json`, tunnel_required: true },
        status: 200,
      });
    });
    await page.route(`http://127.0.0.1:${port}/api/ssh-source/goal-lifecycle`, async (route) => {
      const body = route.request().postDataJSON();
      state.lifecycleRequests.push(body);
      const payload = payloads.get("8976");
      if (body.host_alias === "remote-b" && body.goal_id === "remote-b-goal" && body.operation === "stop") {
        payload.run_history.goals[0].activation_state = "stopped";
      }
      await route.fulfill({
        contentType: "application/json",
        json: {
          activation_state: "stopped",
          changed: true,
          goal_id: body.goal_id,
          host_alias: body.host_alias,
          ok: true,
          operation: body.operation,
          projection_verified: true,
          schema_version: "loopx_remote_goal_lifecycle_v1",
        },
        status: 200,
      });
    });
    const installStatusRoute = async (url, key) => {
      await page.route(`${url}*`, async (route) => {
        state.statusRequestsByPort.set(key, (state.statusRequestsByPort.get(key) ?? 0) + 1);
        state.statusStartedByPort.get(key)?.();
        await state.statusGates.get(key);
        await route.fulfill({ contentType: "application/json", json: payloads.get(key), status: 200 });
      });
    };
    await installStatusRoute(`http://127.0.0.1:${port}/status.json`, "local");
    await installStatusRoute("http://127.0.0.1:8766/status.json", "8766");
    await installStatusRoute("http://127.0.0.1:8876/status.json", "8876");
    await installStatusRoute("http://127.0.0.1:8976/status.json", "8976");

    await page.goto(appUrl, { waitUntil: "networkidle" });
    await page.getByText("Local Goal Only", { exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
    if ((state.statusRequestsByPort.get("local") ?? 0) === 0) throw new Error("The bare Dashboard did not request its same-origin /status.json source");
    if ((state.statusRequestsByPort.get("8766") ?? 0) !== 0) throw new Error("The bare Dashboard bypassed the Vite proxy and requested browser-local port 8766");
    const sourceSelect = page.getByRole("combobox", { name: "选择控制面来源" });

    const remoteAStatusGate = deferred();
    const remoteAStatusStarted = deferred();
    state.statusGates.set("8876", remoteAStatusGate.promise);
    state.statusStartedByPort.set("8876", remoteAStatusStarted.resolve);
    await selectSource(page, sourceSelect, "Remote A");
    await remoteAStatusStarted.promise;
    await selectSource(page, sourceSelect, "Remote B");
    await page.getByText("Remote B Goal Only", { exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
    const remoteAResponse = page.waitForResponse(
      (response) => response.url().startsWith("http://127.0.0.1:8876/status.json"),
    );
    remoteAStatusGate.resolve();
    await remoteAResponse;
    await page.evaluate(() => new Promise((resolveFrame) => requestAnimationFrame(() => requestAnimationFrame(resolveFrame))));
    if (await selectedSourceLabel(sourceSelect) !== "Remote B") throw new Error("A stale status response moved the source selector away from Remote B");
    if (await page.getByText("Remote A Goal Only", { exact: true }).count()) throw new Error("A stale Remote A payload replaced Remote B goals");
    if (!new URL(page.url()).searchParams.get("statusUrl")?.includes("8976")) throw new Error(`The route did not retain Remote B: ${page.url()}`);
    const remotePause = page.getByRole("button", { name: "停止 Remote B Goal Only", exact: true });
    await remotePause.waitFor({ state: "visible" });
    await page.screenshot({ path: resolve(outputDir, "remote-goal-pause.png"), fullPage: false, animations: "disabled" });
    await remotePause.click();
    await page.getByText("Remote B Goal Only", { exact: true }).first().waitFor({ state: "hidden" });
    const lifecycleRequest = state.lifecycleRequests.at(-1);
    if (lifecycleRequest?.host_alias !== "remote-b" || lifecycleRequest?.goal_id !== "remote-b-goal" || lifecycleRequest?.operation !== "stop") {
      throw new Error(`Remote Goal pause did not preserve host and Goal identity: ${JSON.stringify(lifecycleRequest)}`);
    }

    state.statusGates.delete("8876");
    state.statusRequestsByPort.set("8876", 0);
    const remoteAEnsureGate = deferred();
    const remoteAEnsureStarted = deferred();
    state.ensureGates.set("Remote A", remoteAEnsureGate.promise);
    state.ensureStartedByHost.set("Remote A", remoteAEnsureStarted.resolve);
    await selectSource(page, sourceSelect, "Remote A");
    await remoteAEnsureStarted.promise;
    await selectSource(page, sourceSelect, "本机");
    await page.getByText("Local Goal Only", { exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
    const remoteAEnsureResponse = page.waitForResponse((response) => response.url().endsWith("/api/ssh-source/ensure"));
    remoteAEnsureGate.resolve();
    await remoteAEnsureResponse;
    await page.evaluate(() => new Promise((resolveFrame) => requestAnimationFrame(() => requestAnimationFrame(resolveFrame))));
    if (await selectedSourceLabel(sourceSelect) !== "本机") throw new Error("A late SSH ensure completion overrode the newer local selection");
    if ((state.statusRequestsByPort.get("8876") ?? 0) !== 0) throw new Error("A superseded SSH selection still started its status request");
    if (await page.getByText("Remote A Goal Only", { exact: true }).count()) throw new Error("A superseded SSH selection replaced local goals");
    await page.screenshot({ path: resolve(outputDir, "local-after-races.png"), fullPage: false, animations: "disabled" });
    console.log(`status source switch browser smoke (${packaged ? "packaged" : "development"}): ok`);
  } finally {
    await cleanupBrowserSmoke({ browser, fixturePaths: [], server });
  }
}

await main();

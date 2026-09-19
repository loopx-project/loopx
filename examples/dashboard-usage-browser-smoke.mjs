#!/usr/bin/env node
// Browser-level contract for measured usage in the personal workspace drawer.

import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  launchBrowser,
  loadPlaywright,
  startViteDashboardServer,
  waitForHttp,
} from "./dashboard-browser-smoke-support.mjs";

const require = createRequire(import.meta.url);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dashboardDir = resolve(repoRoot, "apps/presentation/dashboard");
const fixtureName = "status.usage.browser-smoke.json";
const outputDir = resolve(repoRoot, "output/playwright/dashboard-usage");
const port = Number(process.env.LOOPX_DASHBOARD_USAGE_SMOKE_PORT ?? "5199");
const packaged = process.env.LOOPX_DASHBOARD_USAGE_PACKAGED === "1";

const cases = [
  { id: "usage-unknown", name: "Usage unknown", usage: {} },
  { id: "usage-zero", name: "Usage zero", usage: { input_tokens_24h: 0, input_tokens_7d: 0, output_tokens_24h: 0, output_tokens_7d: 0, cost_usd_24h: 0, cost_usd_7d: 0, duration_ms_24h: 0, duration_ms_7d: 0 } },
  { id: "usage-partial", name: "Usage partial", usage: { input_tokens_24h: 1200, output_tokens_24h: 300, input_tokens_7d: 2400, output_tokens_7d: 600 } },
  { id: "usage-full", name: "Usage full", usage: { input_tokens_24h: 1000, output_tokens_24h: 250, input_tokens_7d: 4000, output_tokens_7d: 1000, cost_usd_24h: 1.25, cost_usd_7d: 5, duration_ms_24h: 61000, duration_ms_7d: 181000 } },
];

function fixture() {
  const payload = structuredClone(require(resolve(repoRoot, "examples/status.example.json")));
  const source = payload.run_history.goals[0];
  payload.goal_count = cases.length;
  payload.run_history.goals = cases.map(({ id, name }) => ({
    ...source, id, display_name: name, activation_state: "active", registry_member: true,
    latest_runs: (source.latest_runs ?? []).map((run) => ({ ...run, goal_id: id })),
  }));
  payload.usage_summary = {
    ...payload.usage_summary,
    goals: cases.map(({ id, usage }) => ({ goal_id: id, runs_24h: 1, runs_7d: 1, project_share_24h: 0.25, ...usage })),
  };
  return payload;
}

async function openDrawer(page, name) {
  const mobileNavigation = page.locator(".personal-mobile-menu");
  if (await mobileNavigation.isVisible()) {
    await mobileNavigation.click();
    await page.getByRole("dialog", { name: /Goal navigation|Goal 导航/ }).waitFor({ state: "visible" });
  }
  await page.getByRole("button", { name: new RegExp(name, "i") }).first().click();
  await page.getByRole("button", { name: /^(Overview|概览)$/, exact: true }).click();
  await page.getByRole("button", { name: /^(Goal information|Goal 信息)$/, exact: true }).click();
  await page.locator(".personal-context-drawer").waitFor({ state: "visible" });
  return page.locator(".personal-context-drawer").innerText();
}

function requireText(body, values, label) {
  const missing = values.filter((value) => !body.includes(value));
  if (missing.length) throw new Error(`${label} missing: ${missing.join(", ")}\n${body}`);
}

function rejectText(body, values, label) {
  const present = values.filter((value) => body.includes(value));
  if (present.length) throw new Error(`${label} unexpectedly included: ${present.join(", ")}\n${body}`);
}

async function main() {
  await mkdir(outputDir, { recursive: true });
  const { chromium } = loadPlaywright();
  const server = packaged
    ? spawn(process.env.LOOPX_PYTHON_BIN || "python3", ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", resolve(repoRoot, "loopx/web")], { stdio: "ignore" })
    : startViteDashboardServer({ dashboardDir, port });
  let browser;
  try {
    const baseUrl = `http://127.0.0.1:${port}${packaged ? "/chat" : ""}`;
    await waitForHttp(baseUrl);
    browser = await launchBrowser(chromium);
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.setDefaultTimeout(10_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route((url) => new URL(url).pathname === `/${fixtureName}`, (route) => route.fulfill({ contentType: "application/json", json: fixture() }));
    await page.addInitScript(() => {
      if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "en");
    });
    await page.goto(`${baseUrl}/?statusUrl=/${fixtureName}`, { waitUntil: "networkidle" });
    try {
      await page.getByRole("button", { name: /Usage unknown/ }).first().waitFor({ state: "visible", timeout: 15_000 });
    } catch (error) {
      throw new Error(`${error.message}; url=${page.url()}; errors=${errors.join(" | ")}; body=${(await page.locator("body").innerText()).slice(0, 1000)}`);
    }
    requireText(await openDrawer(page, "Usage unknown"), ["Not measured"], "unknown measurement");
    await page.locator(".personal-drawer-close").click();
    requireText(await openDrawer(page, "Usage zero"), ["0 / 0", "$0.00 / $0.00", "0ms / 0ms"], "measured zero");
    rejectText(await page.locator(".personal-channel-title").innerText(), ["0 tokens", "$0.00", "0ms"], "usage belongs in overview and details, not repeated in the header");
    await page.locator(".personal-drawer-close").click();
    const partial = await openDrawer(page, "Usage partial");
    requireText(partial, ["1.5k / 3.0k", "Not measured"], "partial measurement");
    rejectText(await page.locator(".personal-channel-title").innerText(), ["Cost:"], "partial header");
    await page.locator(".personal-drawer-close").click();
    requireText(await openDrawer(page, "Usage full"), ["1.3k / 5.0k", "$1.25 / $5.00", "1.0m / 3.0m"], "full measurement");
    await page.screenshot({ path: resolve(outputDir, "desktop-usage-full-en.png"), fullPage: false, animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: "networkidle" });
    await openDrawer(page, "Usage full");
    await page.screenshot({ path: resolve(outputDir, "mobile-usage-full-en.png"), fullPage: false, animations: "disabled" });
    console.log("dashboard-usage-browser-smoke: mobile passed");
    await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
    await page.reload({ waitUntil: "networkidle" });
    requireText(await openDrawer(page, "Usage unknown"), ["未采集"], "Chinese unknown measurement");
    console.log("dashboard-usage-browser-smoke: Chinese passed");
    if (errors.length) throw new Error(`Dashboard page errors: ${errors.join(" | ")}`);
    console.log(`dashboard-usage-browser-smoke ok\npreview=${baseUrl}/?statusUrl=/${fixtureName}\nscreenshots=${outputDir}`);
  } finally {
    await browser?.close();
    if (server.exitCode === null && server.signalCode === null) server.kill("SIGTERM");
  }
}

await main();

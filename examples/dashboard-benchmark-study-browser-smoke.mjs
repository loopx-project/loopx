#!/usr/bin/env node
// Browser-level smoke for the public-safe, read-only benchmark study route.

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dashboardDir = resolve(repoRoot, "apps/presentation/dashboard");
const port = Number(process.env.LOOPX_BENCHMARK_STUDY_SMOKE_PORT ?? "5199");

function loadPlaywright() {
  const candidates = [
    process.env.LOOPX_PLAYWRIGHT_PACKAGE,
    resolve(dashboardDir, "node_modules/playwright"),
    resolve(homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"),
  ].filter(Boolean);
  try {
    return require("playwright");
  } catch {
    // Try the explicitly configured or bundled runtime below.
  }
  for (const candidate of candidates) {
    if (!candidate || !existsSync(candidate)) continue;
    try {
      return require(candidate);
    } catch {
      // Keep looking.
    }
  }
  throw new Error("Playwright package not found; install playwright or set LOOPX_PLAYWRIGHT_PACKAGE");
}

async function launchBrowser(chromium) {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return chromium.launch({ headless: true });
  }
}

function startDashboardServer() {
  const viteBin = resolve(dashboardDir, "node_modules/vite/bin/vite.js");
  if (!existsSync(viteBin)) throw new Error(`Vite package not installed: ${viteBin}`);
  return spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
    cwd: dashboardDir,
    stdio: "ignore",
  });
}

async function waitForDashboard(url) {
  const deadline = Date.now() + 20_000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveTimeout) => setTimeout(resolveTimeout, 250));
  }
  throw lastError ?? new Error(`Timed out waiting for ${url}`);
}

async function assertNoHorizontalOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    scroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
  }));
  if (dimensions.scroll > dimensions.viewport + 2) {
    throw new Error(`${label} horizontal overflow: viewport=${dimensions.viewport} scroll=${dimensions.scroll}`);
  }
}

async function assertCommonSurface(page, label) {
  await page.waitForSelector("main.benchmark-page", { timeout: 10_000 });
  const body = await page.locator("body").innerText();
  for (const required of [
    "Four-arm software-engineering study",
    "Derived read-only projection",
    "Score-countable cells",
    "RUNTIME HEALTH",
    "Dashboard cannot launch, grade, or mutate runs.",
  ]) {
    if (!body.includes(required)) throw new Error(`${label} missing text: ${required}`);
  }
  if (await page.locator("form").count()) throw new Error(`${label} unexpectedly exposes a form`);
  if (await page.locator('input, textarea, select, [contenteditable="true"]').count()) {
    throw new Error(`${label} unexpectedly exposes editable controls`);
  }
  await assertNoHorizontalOverflow(page, label);
}

async function assertCaseScopedRunSelection(page, url) {
  const fixture = JSON.parse(readFileSync(resolve(dashboardDir, "public/benchmark-study.example.json"), "utf8"));
  const localIds = new Map(fixture.runs.map((run) => [run.run_id, run.arm_id]));
  const packet = JSON.parse(JSON.stringify(fixture, (_key, value) => localIds.get(value) ?? value));
  await page.route("**/case-local-runs.json", (route) => route.fulfill({ json: packet }));
  const target = new URL(url);
  target.search = new URLSearchParams({ dashboardUrl: "/case-local-runs.json", view: "cases" }).toString();
  await page.goto(target.toString(), { waitUntil: "networkidle" });
  const secondCase = page.getByRole("row").filter({ has: page.getByText("case-state-transition", { exact: true }) });
  await secondCase.getByRole("button", { name: /Countable/ }).first().click();
  const detail = page.getByRole("complementary", { name: "Run detail for goal_plain" });
  await detail.waitFor();
  if (process.env.LOOPX_BENCHMARK_STUDY_SCREENSHOT_DIR) {
    mkdirSync(process.env.LOOPX_BENCHMARK_STUDY_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: resolve(process.env.LOOPX_BENCHMARK_STUDY_SCREENSHOT_DIR, "case-local-run.png"), fullPage: true });
  }
  if (!(await detail.innerText()).includes("case-state-transition · goal_plain")) {
    throw new Error("case-cell navigation opened another case's reused run id");
  }
  if (new URL(page.url()).searchParams.get("caseId") !== "case-state-transition") {
    throw new Error("run link lost its case identity");
  }
  await page.reload({ waitUntil: "networkidle" });
  await detail.getByText("case-state-transition · goal_plain", { exact: true }).waitFor();
  const firstCase = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "case-api-contract", exact: true }) });
  await firstCase.getByRole("button", { name: "goal_plain", exact: true }).click();
  await detail.getByText("case-api-contract · goal_plain", { exact: true }).waitFor();

  for (const caseId of ["", "missing-case"]) {
    target.search = new URLSearchParams({ dashboardUrl: "/case-local-runs.json", view: "runs", runId: "goal_plain", caseId }).toString();
    await page.goto(target.toString(), { waitUntil: "networkidle" });
    await page.getByText("Select a run from the table to view its details.", { exact: true }).waitFor();
    if (!caseId && process.env.LOOPX_BENCHMARK_STUDY_SCREENSHOT_DIR) {
      await page.screenshot({ path: resolve(process.env.LOOPX_BENCHMARK_STUDY_SCREENSHOT_DIR, "ambiguous-run-link.png"), fullPage: true });
    }
    if (await page.getByRole("complementary", { name: /Run detail for/ }).count()) {
      throw new Error("ambiguous or stale run link silently selected a different run");
    }
  }
  // A unique legacy runId bookmark remains valid without a caseId parameter.
  target.search = new URLSearchParams({ view: "runs", runId: "run-goal-state" }).toString();
  await page.goto(target.toString(), { waitUntil: "networkidle" });
  await page.getByRole("complementary").getByText("case-state-transition · goal_plain", { exact: true }).waitFor();
}

async function main() {
  const { chromium } = loadPlaywright();
  const server = process.env.LOOPX_BENCHMARK_STUDY_URL ? null : startDashboardServer();
  let browser;
  const pageErrors = [];
  try {
    const url = process.env.LOOPX_BENCHMARK_STUDY_URL ?? `http://127.0.0.1:${port}/benchmarks/study`;
    await waitForDashboard(url);
    browser = await launchBrowser(chromium);

    const desktop = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    desktop.on("pageerror", (error) => pageErrors.push(error.message));
    await desktop.goto(url, { waitUntil: "networkidle" });
    await assertCommonSurface(desktop, "desktop campaign");
    if ((await desktop.getByRole("navigation", { name: "Benchmark dashboard views" }).count()) !== 1) {
      throw new Error("benchmark view navigation must expose one accessible label");
    }
    if ((await desktop.getByText("6 / 8", { exact: true }).count()) !== 1) {
      throw new Error("campaign countability denominator is not rendered exactly once");
    }

    await desktop.getByRole("button", { name: "Arms", exact: true }).click();
    await desktop.getByText("2/2 countable", { exact: true }).first().waitFor();
    await desktop.getByText("goal-v1 (2)", { exact: true }).waitFor();
    await desktop.getByText("contract_miss (1)", { exact: true }).waitFor();
    await desktop.getByRole("button", { name: "Cases", exact: true }).click();
    await desktop.getByRole("columnheader", { name: "Largest eligible delta", exact: true }).waitFor();
    await desktop.getByText("goal_hint: +2", { exact: true }).waitFor();
    await desktop.getByText("preservation: 20/20 tests", { exact: true }).first().waitFor();
    await desktop.getByText("reward: 0", { exact: true }).first().waitFor();
    await desktop.getByRole("button", { name: /Countable/ }).first().click();
    await desktop.getByRole("complementary", { name: /Run detail for/ }).waitFor();
    await desktop.getByText("Upload provenance", { exact: true }).waitFor();
    await desktop.getByText("Implication: Keep the external contract check.", { exact: true }).waitFor();
    await desktop.getByText("Evidence: public-receipt:demo123", { exact: true }).waitFor();

    const refresh = desktop.getByRole("button", { name: "Refresh local readback", exact: true });
    if ((await refresh.count()) !== 1) throw new Error("readback refresh must be a real accessible button");
    await refresh.focus();
    if (!(await refresh.evaluate((element) => element === document.activeElement))) {
      throw new Error("readback refresh is not keyboard focusable");
    }

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    mobile.on("pageerror", (error) => pageErrors.push(error.message));
    await mobile.goto(url, { waitUntil: "networkidle" });
    await assertCommonSurface(mobile, "mobile campaign");

    const rejected = await browser.newPage({ viewport: { width: 800, height: 700 } });
    await rejected.goto(`${url}?dashboardUrl=${encodeURIComponent("https://example.com/study.json")}`, { waitUntil: "networkidle" });
    await rejected.getByText("Benchmark dashboard source must use same-origin local readback", { exact: true }).waitFor();

    await assertCaseScopedRunSelection(desktop, url);
    if (pageErrors.length) throw new Error(`browser errors: ${pageErrors.join(" | ")}`);
    console.log("benchmark study browser smoke passed");
  } finally {
    await browser?.close();
    server?.kill("SIGTERM");
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

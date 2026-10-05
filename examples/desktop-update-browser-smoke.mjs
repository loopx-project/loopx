// Production bundle, synthetic status and native IPC double. Never installs software.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname } from "node:path";
import { launchBrowser, loadPlaywright } from "./dashboard-browser-smoke-support.mjs";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "output/playwright/desktop-update");
const status = await readFile(resolve(root, "examples/status.example.json"));
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".woff2": "font/woff2", ".png": "image/png" };
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/status.json") { res.setHeader("Content-Type", "application/json"); res.end(status); return; }
  if (url.pathname.startsWith("/api/")) { res.setHeader("Content-Type", "application/json"); res.end('{"ok":true,"sessions":[],"adapters":[]}'); return; }
  const relative = url.pathname === "/chat/" ? "/chat/index.html" : url.pathname;
  const base = url.pathname.startsWith("/boot/") ? resolve(root, "apps/desktop/loopx-control-plane/static") : resolve(root, "loopx/web");
  const path = resolve(base, `.${url.pathname.startsWith("/boot/") ? url.pathname.slice(5) : relative}`);
  if (!path.startsWith(`${base}/`)) { res.writeHead(403).end(); return; }
  try { res.setHeader("Content-Type", types[extname(path)] ?? "application/octet-stream"); res.end(await readFile(path)); }
  catch { res.writeHead(404).end(); }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await launchBrowser(loadPlaywright().chromium);
try {
  await mkdir(output, { recursive: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const calls = [];
  let nativeState = null;
  let nativeActionSettled = Promise.resolve();
  let nativeRuntimeSelection;
  let startupTiming = null;
  let failUpdate = false;
  let checkFailure = null;
  let checkUpToDate = false;
  let statusFailure = false;
  let environmentTelemetry = null;
  await page.exposeFunction("nativeInvoke", async (command, args) => {
    if (command === "desktop_update_status") {
      if (statusFailure) throw new Error("Command desktop_update_status not allowed by ACL");
      return { state: nativeState, runtime_selection: nativeRuntimeSelection, startup: startupTiming, app_version: "0.5.4", rollback_available: true, environment: environmentTelemetry };
    }
    calls.push({ command, args });
    if (checkFailure && args.action === "check") return { phase: "error", details: { code: checkFailure } };
    if (checkUpToDate && args.action === "check") {
      nativeState = { phase: "up_to_date", details: { channel: args.channel } };
      return nativeState;
    }
    if (failUpdate) throw new Error("private diagnostic must not be displayed");
    nativeActionSettled = new Promise((done) => setTimeout(done, 150)).then(() => {
      nativeState = {
        phase: args.action === "check" ? "available" : ["align_runtime", "use_installed_runtime", "forget_runtime_selection"].includes(args.action) ? "connecting" : "restart_required",
        details: { version: "0.5.5", channel: args.channel },
      };
      return nativeState;
    });
    return await nativeActionSettled;
  });
  await page.addInitScript(() => {
    localStorage.setItem("loopx-pw-locale", "zh-CN");
    window.__TAURI__ = { core: { invoke: (...args) => window.nativeInvoke(...args) } };
  });
  await page.goto(`${origin}/chat/?statusUrl=/status.json`);
  const trigger = page.getByRole("button", { name: "有可用更新" });
  await trigger.waitFor();
  assert.equal(await trigger.getAttribute("aria-expanded"), "false");
  const footerBefore = await page.locator(".personal-sidebar-footer").boundingBox();
  await page.screenshot({ path: resolve(output, "update-collapsed.png") });
  await trigger.click();
  assert.deepEqual(await page.locator(".personal-sidebar-footer").boundingBox(), footerBefore, "opening updates must not shrink Goal navigation");
  assert.equal(await page.getByRole("combobox", { name: "更新通道" }).isVisible(), false);
  await page.keyboard.press("Escape");
  await trigger.click();
  await page.getByRole("button", { name: "更新并准备重启", exact: true }).waitFor();
  assert.deepEqual(calls.map((call) => call.args?.action), ["check"], "auto-check must not mutate installation");
  await page.screenshot({ path: resolve(output, "update-panel.png") });
  await page.getByRole("button", { name: "更新并准备重启", exact: true }).click();
  await page.locator(".personal-update-panel").getByRole("button", { name: "重启完成更新", exact: true }).waitFor();
  assert.deepEqual(calls.map((call) => call.args?.action), ["check", "apply"]);
  await page.reload();
  await page.getByRole("button", { name: /重启完成更新/ }).waitFor();
  assert.deepEqual(calls.map((call) => call.args?.action), ["check", "apply"], "reload must recover native progress, not repeat install");
  nativeState = { phase: "available", details: { channel: "stable", version: "0.5.5" } };
  failUpdate = true;
  await page.reload();
  await page.getByRole("button", { name: "有可用更新" }).click();
  await page.getByRole("button", { name: "更新并准备重启", exact: true }).click();
  await page.getByText("更新未完成。请重试；启动失败可尝试修复当前版本。").waitFor();
  assert.ok(!(await page.locator("body").innerText()).includes("private diagnostic"));
  assert.equal(await page.getByRole("button", { name: "更新并准备重启", exact: true }).count(), 0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开 Goal 导航" }).click();
  await page.getByRole("button", { name: "更新需重试" }).waitFor({ state: "visible" });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: resolve(output, "update-mobile.png") });

  await page.setViewportSize({ width: 1280, height: 900 });
  statusFailure = true;
  await page.reload();
  await page.getByRole("button", { name: "更新需重试" }).click();
  await page.getByText("无法读取 App 更新状态。请重启 App 后再试；若仍失败，请重新安装最新 App。").waitFor();
  assert.ok(!(await page.locator("body").innerText()).includes("not allowed by ACL"));
  statusFailure = false;
  failUpdate = false;
  checkFailure = "update_feed_unavailable";
  await page.getByRole("button", { name: "检查更新", exact: true }).click();
  await page.getByText("此通道的更新源尚未就绪或暂时不可用。可稍后重新检查，当前版本仍可继续使用。").waitFor();
  await page.getByText("0.5.4 · 稳定版", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "更新并准备重启", exact: true }).count(), 0);
  checkFailure = null;
  await page.getByRole("button", { name: "检查更新", exact: true }).click();
  await page.getByRole("button", { name: "更新并准备重启", exact: true }).waitFor();

  await nativeActionSettled;
  // The native owner decides whether Repair can maintain this installation.
  // The workspace consumes that projection rather than inferring from a path.
  nativeState = { phase: "ready", details: {} };
  nativeRuntimeSelection = { explicit: false, remembered: true, bundled_repair_available: false };
  await page.reload();
  await page.getByRole("button", { name: "更新 LoopX", exact: true }).click();
  await page.getByText("高级选项", { exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "修复当前版本", exact: true }).isEnabled(), false);
  await page.getByText(/当前运行时由其他安装方式或启动参数管理/).waitFor();
  const beforeForget = calls.length;
  await page.getByRole("button", { name: "清除记住的运行时选择", exact: true }).click();
  await page.getByText("正在连接更新后的服务…", { exact: true }).waitFor();
  assert.deepEqual(calls.slice(beforeForget).map((call) => call.args.action), ["forget_runtime_selection"]);
  await nativeActionSettled;
  nativeState = { phase: "ready", details: {} };
  nativeRuntimeSelection = { explicit: true, remembered: true, bundled_repair_available: false };
  await page.reload();
  await page.getByRole("button", { name: "更新 LoopX", exact: true }).click();
  await page.getByText("高级选项", { exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "修复当前版本", exact: true }).isEnabled(), false);
  assert.equal(await page.getByRole("button", { name: "清除记住的运行时选择", exact: true }).count(), 0, "forgetting a preference cannot clear an environment pin");
  nativeRuntimeSelection = undefined;
  nativeState = { phase: "idle", details: {} };
  await page.reload();
  await page.getByRole("button", { name: "更新 LoopX", exact: true }).click();
  await page.getByText(/App 启动时会检查并安装当前通道/).waitFor();
  await page.getByText("高级选项", { exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "修复当前版本", exact: true }).isEnabled(), true, "legacy native status retains its available repair action");

  const missing = await browser.newPage();
  await missing.route("**/assets/*.js", (route) => route.abort());
  await missing.goto(`${origin}/chat/`);
  await missing.getByText("正在加载工作区… / Loading workspace…").waitFor();
  await missing.getByRole("link", { name: "重新加载 / Reload" }).waitFor();
  await missing.screenshot({ path: resolve(output, "missing-assets.png") });
  await missing.unroute("**/assets/*.js");
  await missing.getByRole("link", { name: "重新加载 / Reload" }).click();
  // This page has no injected native bridge: recovery loads the browser
  // workspace, whose updater entry is intentionally unavailable.
  await missing.getByRole("button", { name: "更新 LoopX" }).waitFor({ state: "hidden" });
  await missing.locator(".personal-workspace-shell").waitFor();

  await page.setViewportSize({ width: 1280, height: 900 });
  nativeState = null;
  failUpdate = false;
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "no-preference" });
  await page.goto(`${origin}/boot/index.html`);

  const startupPanel = page.locator("main");
  const startupDots = page.locator(".status-dots");
  const startupMotion = async () => page.evaluate(() => ({
    markAnimation: getComputedStyle(document.querySelector(".mark")).animationName,
    dotAnimation: getComputedStyle(document.querySelector(".status-dots i")).animationName,
    progressAnimation: getComputedStyle(document.querySelector(".progress"), "::after").animationName,
    progressTransform: getComputedStyle(document.querySelector(".progress"), "::after").transform,
    progressWidth: Number.parseFloat(getComputedStyle(document.querySelector(".progress"), "::after").width),
    trackWidth: Number.parseFloat(getComputedStyle(document.querySelector(".progress")).width),
    warning: getComputedStyle(document.documentElement).getPropertyValue("--warning").trim(),
  }));

  assert.equal(await startupPanel.getAttribute("data-state"), "loading");
  assert.equal(await startupPanel.getAttribute("aria-busy"), "true");
  assert.equal(await startupDots.isVisible(), true);
  const progressTransforms = [];
  for (let index = 0; index < 4; index += 1) {
    progressTransforms.push((await startupMotion()).progressTransform);
    await page.waitForTimeout(120);
  }
  const loadingMotion = await startupMotion();
  assert.equal(loadingMotion.markAnimation, "mark-breathe");
  assert.equal(loadingMotion.dotAnimation, "dot-pulse");
  assert.equal(loadingMotion.progressAnimation, "boot-progress");
  assert.ok(new Set(progressTransforms).size > 1, "startup progress indicator must visibly move");
  assert.equal(loadingMotion.warning, "#f5a623");
  await page.screenshot({ path: resolve(output, "startup-loading-light.png") });

  await page.evaluate(() => window.loopxBootFailed("启动失败，请重试。"));
  assert.equal(await startupPanel.getAttribute("data-state"), "error");
  assert.equal(await startupPanel.getAttribute("aria-busy"), "false");
  assert.equal(await page.locator("#status").innerText(), "启动失败，请重试。");
  assert.equal(await startupDots.isVisible(), false);
  const failedMotion = await startupMotion();
  assert.equal(failedMotion.markAnimation, "none");
  assert.equal(failedMotion.progressAnimation, "none");
  assert.ok(Math.abs(failedMotion.progressWidth - failedMotion.trackWidth) < 0.5, "failure progress indicator must fill its track");
  await page.screenshot({ path: resolve(output, "startup-error-light.png") });

  await page.evaluate(() => window.loopxBootRetrying());
  assert.equal(await startupPanel.getAttribute("data-state"), "loading");
  assert.equal(await startupPanel.getAttribute("aria-busy"), "true");
  assert.equal(await page.locator("#status").innerText(), "正在重新连接本地控制面");
  assert.equal(await startupDots.isVisible(), true);
  const retryMotion = await startupMotion();
  assert.equal(retryMotion.markAnimation, "mark-breathe");
  assert.equal(retryMotion.dotAnimation, "dot-pulse");
  assert.equal(retryMotion.progressAnimation, "boot-progress");

  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "no-preference" });
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--canvas").trim()), "#09090b");
  await page.screenshot({ path: resolve(output, "startup-retry-dark.png") });

  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  const reducedMotion = await startupMotion();
  assert.equal(reducedMotion.markAnimation, "none");
  assert.equal(reducedMotion.dotAnimation, "none");
  assert.equal(reducedMotion.progressAnimation, "none");
  assert.equal(reducedMotion.progressTransform, "none");
  assert.ok(Math.abs(reducedMotion.progressWidth - reducedMotion.trackWidth) < 0.5, "reduced-motion progress indicator must fill its track");
  await page.screenshot({ path: resolve(output, "startup-reduced-motion.png") });

  await page.emulateMedia({ colorScheme: "light", reducedMotion: "no-preference" });
  await page.getByText("恢复与更新 / Recovery & updates").click();
  await page.getByRole("button", { name: "检查更新 / Check for updates" }).click();
  await page.getByRole("button", { name: "更新并准备重启 / Install update" }).waitFor();
  await page.locator("#channel").selectOption("main");
  // Cross an actual native-state polling tick after changing the selection.
  await page.waitForTimeout(1200);
  assert.equal(await page.locator("#update").innerText(), "检查更新 / Check for updates");
  await page.locator("#update").click();
  await page.getByRole("button", { name: "更新并准备重启 / Install update" }).waitFor();
  assert.deepEqual(calls.at(-1).args, { action: "check", channel: "main" });
  await page.reload();
  await page.getByText("恢复与更新 / Recovery & updates").click();
  await page.waitForFunction(() => document.querySelector("#channel").value === "main");
  nativeState = { phase: "service_error", details: { code: "service_start_failed" } };
  await page.getByText("运行时已安装，但服务尚未连接。可检查更新、修复或恢复上版；连接仍会自动重试。").waitFor();
  for (const selector of ["#update", "#repair", "#rollback", "#channel"]) {
    assert.equal(await page.locator(selector).isEnabled(), true, `${selector} remains usable after service failure`);
  }
  await page.screenshot({ path: resolve(output, "startup-recovery.png") });

  // A snapshot stuck in a terminal phase must turn the main status line into
  // its error projection (fresh Mac without Python 3.11+: installer exit 2),
  // not the pre-fix permanent loading shape. The projection is derived by the
  // page from desktop_update_status itself.
  environmentTelemetry = { os_version: "26.5", arch: "aarch64", runtime_executable_found: false, python3_found: false, python3_version: null };
  nativeState = { phase: "error", details: { code: "runtime_install_exit_2" } };
  await page.reload();
  await page.waitForFunction(() => document.querySelector("main").dataset.state === "error");
  assert.equal(await startupPanel.getAttribute("aria-busy"), "false");
  const bootHeadline = await page.locator("#status").innerText();
  assert.ok(bootHeadline.includes("安装程序退出（2）"), bootHeadline);
  assert.ok(bootHeadline.includes("Python 3.11+"), bootHeadline);
  assert.ok(bootHeadline.includes("恢复与更新"), bootHeadline);
  assert.equal(await startupDots.isVisible(), false);
  assert.equal(await page.locator("details.recovery").getAttribute("open"), "");
  assert.equal(await page.locator("#boot-elapsed").innerText(), "等待恢复");
  assert.ok((await page.locator("#update-status").innerText()).includes("本机多半缺少可用的 Python 3.11+"));
  const bootDiagnostics = JSON.parse(await page.locator("#diagnostics").inputValue());
  assert.equal(bootDiagnostics.schema_version, "desktop_recovery_diagnostics_v2");
  assert.equal(bootDiagnostics.error_code, "runtime_install_exit_2");
  assert.deepEqual(bootDiagnostics.environment, environmentTelemetry);
  await page.screenshot({ path: resolve(output, "startup-runtime-install-error.png") });

  // A transient repair phase clears the escalation while it runs, and a
  // terminal snapshot re-escalates with the matching runtime_required copy.
  nativeState = { phase: "installing_runtime", details: {} };
  await page.waitForFunction(() => document.querySelector("main").dataset.state === "loading");
  assert.equal(await startupPanel.getAttribute("aria-busy"), "true");
  nativeState = { phase: "runtime_required", details: { code: "runtime_setup_required" } };
  await page.waitForFunction(() => document.querySelector("main").dataset.state === "error" && document.querySelector("#status").innerText.includes("请修复当前版本，成功后重启"));
  environmentTelemetry = null;
  // Production boot surface must expose native installation and service
  // stages even while recovery is collapsed; reload keeps native elapsed time.
  nativeState = { phase: "installing_runtime", details: {} };
  startupTiming = { elapsed_ms: 35000, phase_elapsed_ms: 32000 };
  await page.reload();
  await page.getByText("正在准备所需组件", { exact: true }).waitFor();
  assert.ok((await page.locator("#boot-elapsed").innerText()).includes("35 秒"));
  assert.equal(await page.locator("details.recovery").getAttribute("open"), null);
  await page.screenshot({ path: resolve(output, "startup-installing-progress.png") });
  await page.reload();
  await page.getByText("正在准备所需组件", { exact: true }).waitFor();
  assert.ok((await page.locator("#boot-elapsed").innerText()).includes("35 秒"));
  nativeState = { phase: "connecting", details: { service: "chat" } };
  await page.getByText("正在打开工作区", { exact: true }).waitFor();
  await page.getByText(/启动用时较长/).waitFor();
  nativeState = { phase: "service_error", details: { code: "service_start_failed" } };
  await page.getByText("本地服务连接失败，正在等待重试", { exact: true }).waitFor();
  console.log("desktop-update-browser-smoke: passed (confirmation, failure redaction, mobile, missing assets + reload, startup motion states, startup recovery, immediate terminal recovery, automatic startup presentation)");
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}

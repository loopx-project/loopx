// Packaged settings + real Chat HTTP/native account store; synthetic process IO.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { cleanupBrowserSmoke, launchBrowser, loadPlaywright, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
const { repoRoot, outputDir, port, startServer } = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const { openWorkspacePage, fetchWorkspaceBackend } = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
const fixture = spawn(resolveTestPython({ repoRoot }), ["-u", "apps/presentation/dashboard/smoke/codex-account-clients-fixture.py"],
  { cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(fixture, "exit");
let stderr = "";
fixture.stderr.on("data", value => { stderr += String(value); });
const iterator = createInterface({ input: fixture.stdout })[Symbol.asyncIterator]();
async function next() {
  const line = await iterator.next();
  assert.equal(line.done, false, stderr);
  return JSON.parse(line.value);
}
async function command(value) { fixture.stdin.write(`${value}\n`); return next(); }
let server, browser, workspace;
const requests = [];
try {
  const { origin } = await next();
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  workspace = await openWorkspacePage({newPage: options => browser.newPage({locale: "zh-CN", ...options})}, url, {
    beforeGoto(_api, page) {
      return page.route("**/api/chat/codex-accounts**", async route => {
        requests.push({method: route.request().method(), path: new URL(route.request().url()).pathname});
        assert.equal(route.request().method(), "GET", "Inspection must never start an account operation");
        await route.fulfill({response: await fetchWorkspaceBackend(route, origin)});
      });
    },
  });
  const { page, api } = workspace;
  await page.getByRole("button", {name: "设置", exact: true}).click();
  await page.locator(".personal-settings-tabs").getByRole("button", {name: "模型 Provider 配置", exact: true}).click();
  const panel = page.getByTestId("codex-account-holders");
  const summary = panel.locator("summary");
  await summary.filter({hasText: "21 个占用 · 2 个暂不能自动重启"}).waitFor();
  assert.equal(await panel.getAttribute("open"), null, "The process table must not displace account status");
  await mkdir(outputDir, {recursive: true});
  await page.screenshot({path: resolve(outputDir, "auth-holders-default-desktop.png"), animations: "disabled"});
  await summary.focus();
  await page.keyboard.press("Enter");
  await panel.getByText("colima-example", {exact: false}).waitFor();
  assert.equal(await panel.locator("li").count(), 4, "Group one App's helpers without repeating restart copy");
  for (let pid = 1000; pid < 1018; pid++) assert.ok((await panel.innerText()).includes(String(pid)), "Every observed PID remains readable");
  assert.match(await panel.locator("li").first().innerText(), /外部工作负载/);
  assert.match(await panel.innerText(), /dev.example.renamed-chat-with-a-long-installed-service-label/);
  await page.screenshot({path: resolve(outputDir, "auth-holders-expanded-desktop.png"), animations: "disabled"});
  const inventoryReads = () => requests.filter(row => row.path.endsWith("/clients")).length;
  const beforePolling = inventoryReads();
  await page.waitForTimeout(5500); // Cross one existing status timer tick.
  assert.equal(inventoryReads(), beforePolling, "The status timer must not repeatedly scan processes");
  await page.setViewportSize({width: 390, height: 844});
  await panel.scrollIntoViewIfNeeded();
  assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
  await page.screenshot({path: resolve(outputDir, "auth-holders-expanded-narrow.png"), animations: "disabled"});
  for (const mode of ["unavailable", "partial", "empty", "sparse", "many"]) {
    await command(mode);
    await panel.getByRole("button", {name: "重新检查占用", exact: true}).click();
    const expected = mode === "unavailable" || mode === "partial" ? "占用情况暂时无法确认"
      : mode === "empty" ? "本次未发现登录占用" : mode === "sparse" ? "1 个占用 · 1 个暂不能自动重启" : "21 个占用 · 2 个暂不能自动重启";
    await summary.filter({hasText: expected}).waitFor();
    assert.equal(await panel.locator("li").count(), mode === "many" ? 4 : mode === "sparse" ? 1 : 0);
    if (mode === "unavailable") await page.screenshot({path: resolve(outputDir, "auth-holders-unavailable-narrow.png"), animations: "disabled"});
  }
  await page.setViewportSize({width: 1512, height: 982});
  await page.locator(".personal-settings-tabs").getByRole("button", {name: "语言", exact: true}).click();
  await page.getByRole("radio", {name: "English", exact: true}).click();
  await page.locator(".personal-settings-tabs").getByRole("button", {name: "Model provider", exact: true}).click();
  await page.getByTestId("codex-account-holders").locator("summary").filter({hasText: "21 holders · 2 cannot restart automatically"}).waitFor();
  await page.getByTestId("codex-account-holders").locator("summary").click();
  await page.getByText("External workload idle and recovery conditions are unverified", {exact: true}).waitFor();
  await page.screenshot({path: resolve(outputDir, "auth-holders-expanded-en.png"), animations: "disabled"});
  const state = await command("inspect");
  assert.equal(state.state_unchanged, true);
  assert.equal(state.watcher_started, false);
  assert.ok(state.calls.every(operation => ["status", "clients"].includes(operation)));
  assert.equal(api.turnRequests.length, 0);
  assert.deepEqual(workspace.errors, []);
  console.log(JSON.stringify({ok: true, boundary: "packaged UI / real HTTP and native policy store / synthetic process IO",
    inventoryReads: inventoryReads(), effects: 0, fullOwnerCount: 21, recovery: ["unavailable", "partial", "empty", "sparse", "many"], languages: ["zh-CN", "en"]}));
} catch (error) {
  if (workspace) console.error((await workspace.page.locator("body").innerText()).slice(0, 2500));
  throw error;
} finally {
  await workspace?.close();
  await browser?.close();
  fixture.stdin.end();
  if (fixture.exitCode === null) await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 5000))]);
  if (fixture.exitCode === null) fixture.kill("SIGTERM");
  await cleanupBrowserSmoke({server, fixturePaths: []});
}

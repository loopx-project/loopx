// Packaged settings, real HTTP and private store; synthetic OpenAI/native IO.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { cleanupBrowserSmoke, launchBrowser, loadPlaywright, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
process.env.LOOPX_API_FALLBACK_FIXTURE = "1";
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
let loseNextAdd = false;
let addRequests = 0;
const syntheticKey = index => `sk-${"synthetic-only-".repeat(4)}${index}`;
try {
  const { origin } = await next();
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  workspace = await openWorkspacePage({newPage: options => browser.newPage({locale: "zh-CN", ...options})}, url, {
    beforeGoto(_api, page) {
      return page.route("**/api/chat/codex-accounts**", async route => {
        const request = route.request();
        const body = request.method() === "POST" ? request.postDataJSON() : null;
        if (body?.operation === "keys-add") addRequests++;
        const response = await fetchWorkspaceBackend(route, origin);
        assert.ok(!(await response.text()).includes("synthetic-only-"), "No response may contain the complete key");
        if (body?.operation === "keys-add" && loseNextAdd) {
          loseNextAdd = false;
          await route.abort("failed");
        } else await route.fulfill({response});
      });
    },
  });
  const {page, api} = workspace;
  await page.getByRole("button", {name: "设置", exact: true}).click();
  await page.locator(".personal-settings-tabs").getByRole("button", {name: "模型 Provider 配置", exact: true}).click();
  const panel = page.getByTestId("openai-key-settings");
  await panel.getByText("尚未添加 Key", {exact: true}).waitFor();
  await mkdir(outputDir, {recursive: true});
  await page.screenshot({path: resolve(outputDir, "openai-keys-empty-desktop.png"), animations: "disabled"});
  async function add(index, label) {
    await panel.getByRole("button", {name: "添加 Key", exact: true}).click();
    await panel.getByLabel("名称", {exact: true}).fill(label);
    await panel.getByLabel("API Key", {exact: true}).fill(syntheticKey(index));
    await panel.getByRole("button", {name: "保存 Key", exact: true}).click();
    await panel.getByText(label, {exact: true}).waitFor();
    if (await panel.locator('input[type="password"]').count()) {
      assert.equal(await panel.locator('input[type="password"]').inputValue(), "", "A lost response must not retain a key draft");
    }
  }
  await add(1, "Primary project");
  await command("key-permission_denied");
  await panel.locator("article").first().getByRole("button", {name: "检查连接"}).click();
  await panel.getByText("无模型列表读取权限", {exact: true}).waitFor();
  await command("key-models_readable");
  await panel.locator("article").first().getByRole("button", {name: "检查连接"}).click();
  await panel.getByText("模型列表读取成功", {exact: true}).waitFor();
  await add(2, "第二个项目：用于检验很长的配置名称在窄屏上仍然完整可读");
  await command("key-rate_limited");
  await panel.locator("article").nth(1).getByRole("button", {name: "检查连接"}).click();
  await panel.getByText("检查被限流，余额仍未知", {exact: true}).waitFor();
  loseNextAdd = true;
  await add(3, "Recovery project");
  await panel.getByRole("alert").waitFor();
  await panel.getByRole("button", {name: "取消", exact: true}).click();
  assert.equal(addRequests, 3, "A lost write response must be read back, never replayed");
  assert.equal(await panel.locator("article").count(), 3);
  await page.screenshot({path: resolve(outputDir, "openai-keys-populated-desktop.png"), animations: "disabled"});
  const native = page.getByTestId("codex-account-settings");
  await native.getByRole("checkbox", {name: "额度耗尽后自动切换", exact: true}).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="codex-account-settings"] input[type="checkbox"]')?.checked);
  await panel.getByRole("checkbox", {name: "订阅耗尽后使用 API（按用量计费）", exact: true}).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="openai-key-settings"] input[type="checkbox"]')?.checked);
  await native.getByRole("button", {name: "检查额度", exact: true}).click();
  await native.locator("header").getByText(/当前 OPENAI-API/).waitFor();
  await command("api-observation");
  await panel.getByText(/API 已接受请求 · example-selected-model/).waitFor();
  await panel.getByText(/限流窗口请求余量 42 · 限流窗口 Token 余量 12000/).waitFor();
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "openai-fallback-active-desktop.png"), animations: "disabled"});
  await command("subscription-recovered");
  await native.getByRole("button", {name: "检查额度", exact: true}).click();
  await native.locator("header").getByText(/当前 B/).waitFor();
  await page.setViewportSize({width: 390, height: 844});
  await panel.scrollIntoViewIfNeeded();
  assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
  await page.screenshot({path: resolve(outputDir, "openai-keys-populated-narrow.png"), animations: "disabled"});
  await panel.getByRole("button", {name: "API Key 与额度", exact: true}).click();
  await page.getByText(/项目 Key 无法查询账单余额/).waitFor();
  await page.screenshot({path: resolve(outputDir, "openai-keys-help-narrow.png"), animations: "disabled"});
  await page.keyboard.press("Escape");
  await panel.locator("article").last().getByRole("button", {name: "移除", exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="openai-key-settings"] article').length === 2);
  await page.setViewportSize({width: 1512, height: 982});
  await page.locator(".personal-settings-tabs").getByRole("button", {name: "语言", exact: true}).click();
  await page.getByRole("radio", {name: "English", exact: true}).click();
  await page.locator(".personal-settings-tabs").getByRole("button", {name: "Model provider", exact: true}).click();
  await page.getByTestId("openai-key-settings").getByText("2 saved", {exact: true}).waitFor();
  const state = await command("inspect");
  assert.equal(state.state_unchanged, false, "Explicit handoff configuration must persist");
  assert.equal(state.watcher_started, true);
  assert.equal(state.calls.filter(operation => operation === "switch").length, 2, "API fallback then recovered subscription return");
  assert.equal(api.turnRequests.length, 0);
  await command("unregistered");
  const register = native.getByRole("button", {name: "Register current Codex login", exact: true});
  await register.waitFor();
  await register.click();
  await native.getByRole("alert").waitFor();
  await command("registration-ready");
  await register.click();
  await native.locator("header").getByText(/Current PRIMARY/).waitFor();
  assert.equal(await native.getByRole("checkbox", {name: "Switch automatically when quota runs out", exact: true}).isChecked(), false);
  assert.equal(await panel.getByRole("checkbox", {name: "Use API after subscription exhaustion (usage based billing)", exact: true}).isChecked(), false);
  // The lost write response and occupied-login registration are deliberate failures.
  assert.equal(workspace.errors.filter(error => error.includes("status of 409")).length, 1);
  assert.ok(workspace.errors.every(error => error.includes("net::ERR_FAILED") || error.includes("status of 409")), workspace.errors.join("\n"));
  console.log(JSON.stringify({ok: true, boundary: "packaged UI / real HTTP, native policy and private store / synthetic process and OpenAI IO", added: 3, removed: 1, lostWriteReplayed: false, automaticSwitches: 2, registrationRecovery: true}));
} finally {
  await workspace?.close();
  await browser?.close();
  fixture.stdin.end();
  if (fixture.exitCode === null) await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 5000))]);
  if (fixture.exitCode === null) fixture.kill("SIGTERM");
  await cleanupBrowserSmoke({server, fixturePaths: []});
}

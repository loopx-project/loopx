// PR-scoped regression: a packaged switch uses real preview/apply/readback,
// survives reload, rejects stale writes and removes its opt-in on disablement.
// Isolated machine, empty registry, unavailable native host; no model calls.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, mkdir, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const repoRoot = resolve(import.meta.dirname, "../../../..");
const root = await mkdtemp(join(tmpdir(), "loopx-compaction-preference-"));
const port = Number(process.env.LOOPX_COMPACTION_MEMORY_PORT ?? 5414);
const screenshots = process.env.LOOPX_COMPACTION_MEMORY_SCREENSHOTS;
const registry = join(root, "registry.json");
await writeFile(registry, '{"goals":[]}');
await mkdir(join(root, "home"));
if (screenshots) await mkdir(screenshots, {recursive: true});
const server = spawn(resolveTestPython({repoRoot}), ["-m", "loopx.cli", "--registry", registry,
  "--runtime-root", join(root, "runtime"), "chat", "--port", String(port), "--no-open",
  "--codex-bin", join(root, "unavailable-native-host"), "--assets-dir", join(repoRoot, "loopx/web/chat")], {
  cwd: root, env: {...process.env, HOME: join(root, "home"), PYTHONPATH: repoRoot, LOOPX_USAGE_PING: "0"},
  stdio: ["ignore", "pipe", "pipe"],
});
let serverError = "", browser;
for (const stream of [server.stdout, server.stderr]) stream.on("data", chunk => {serverError += String(chunk);});
try {
  const base = `http://127.0.0.1:${port}`;
  await waitForHttp(`${base}/chat/`).catch(error => {throw new Error(`${error.message}\n${serverError}`);});
  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({locale: "en-US", viewport: {width: 1440, height: 1000}});
  const workRequests = [];
  page.on("request", request => {
    if (request.method() === "POST") {
      const path = new URL(request.url()).pathname;
      if (path === "/api/chat/sessions" || /^\/api\/chat\/sessions\/[^/]+\/turns$/u.test(path)) workRequests.push(path);
    }
  });
  const open = async () => {
    await page.goto(`${base}/chat/?settings_scope=machine&settings_tab=capabilities`, {waitUntil: "networkidle"});
    await page.getByText("Compaction working memory (experimental)", {exact: true}).first().click();
  };
  const toggle = page.getByRole("switch", {name: "Enable experimental compaction working memory"});
  const configuration = async () => (await (await page.request.get(`${base}/api/chat/machine-configuration`)).json()).machine_configuration;
  const capture = async name => {
    if (screenshots) await page.screenshot({path: join(screenshots, name)});
  };
  await open();
  assert.equal(await toggle.isChecked(), false);
  await capture("desktop-off.png");
  let beforeSave = workRequests.length;
  await toggle.click();
  await page.getByText("Compaction working memory preference saved.", {exact: true}).waitFor();
  assert.equal(workRequests.length, beforeSave, "Enabling does not create a conversation or Turn");
  assert.equal(await toggle.isChecked(), true);
  assert.equal((await configuration()).namespaces.compaction_memory.enabled, true);
  await open();
  assert.equal(await toggle.isChecked(), true, "Persisted readback survives a fresh page");
  await capture("desktop-on.png");
  await page.setViewportSize({width: 430, height: 932});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await capture("narrow-on.png");
  await page.setViewportSize({width: 1440, height: 1000});

  // Keep the actual backend and invalidate only the request's CAS expectation.
  const apply = "**/api/chat/machine-configuration/apply";
  await page.route(apply, route => route.continue({postData: JSON.stringify({
    ...route.request().postDataJSON(), expected_plan_revision: `sha256:${"0".repeat(64)}`,
  })}));
  beforeSave = workRequests.length;
  await toggle.click();
  await page.getByText(/machine-configuration plan revision changed/u).waitFor();
  assert.equal(workRequests.length, beforeSave, "A rejected setting starts no work");
  assert.equal(await toggle.isChecked(), true, "A rejected save must not look disabled");
  assert.equal((await configuration()).namespaces.compaction_memory.enabled, true);
  await capture("desktop-rejected.png");
  await page.unroute(apply);
  beforeSave = workRequests.length;
  await toggle.click();
  await page.getByText("Compaction working memory preference saved.", {exact: true}).waitFor();
  assert.equal(workRequests.length, beforeSave, "Disabling starts no work");
  assert.equal(await toggle.isChecked(), false);
  assert.equal((await configuration())?.namespaces.compaction_memory, undefined, "Disable restores older-editor compatibility");
  await open();
  assert.equal(await toggle.isChecked(), false);
  // Exercise the shared save helper with the pre-existing switches while the
  // experiment is absent. Each setting preserves the other's confirmed value.
  await page.getByText("Progressive context", {exact: true}).first().click();
  const progressive = page.getByRole("switch", {name: "Read context progressively"});
  const delta = page.getByRole("switch", {name: "Use cross-turn delta automatically on supported hosts"});
  assert.equal(await progressive.isChecked(), true);
  assert.equal(await delta.isChecked(), true);
  await progressive.click();
  await page.getByText("Progressive reads preference saved.", {exact: true}).waitFor();
  assert.equal((await configuration()).namespaces.context_delivery.mode, "off");
  assert.equal(await delta.isChecked(), true);
  await delta.click();
  await page.getByText("Cross-turn delta preference saved.", {exact: true}).waitFor();
  assert.equal((await configuration()).namespaces.context_delivery.delta_delivery, "off");
  assert.equal(await progressive.isChecked(), false);
  assert.equal((await configuration()).namespaces.compaction_memory, undefined);
  await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
  await page.reload({waitUntil: "networkidle"});
  await page.getByText("压缩工作记忆（实验）", {exact: true}).first().click();
  assert.equal(await page.getByRole("switch", {name: "启用实验性压缩工作记忆"}).isChecked(), false);
  await capture("chinese-off.png");
  // Existing Workbench entry may create Session metadata; no Turn is dispatched.
  assert.equal(workRequests.some(path => path.endsWith("/turns")), false);
  console.log("compaction working memory: packaged enable/reload/stale-save/disable/Chinese readback passed");
} finally {
  await browser?.close();
  if (server.exitCode === null) {
    const exited = new Promise(resolveExit => server.once("exit", resolveExit));
    server.kill();
    await exited;
  }
  await rm(root, {recursive: true, force: true});
}

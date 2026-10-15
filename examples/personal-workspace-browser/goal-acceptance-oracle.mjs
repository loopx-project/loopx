// Packaged settings and evidence with a real disposable SQLite/HTTP/CLI owner.
import assert from "node:assert/strict";
import {spawn, execFileSync} from "node:child_process";
import {createInterface} from "node:readline";
import {mkdtemp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../dashboard-browser-smoke-support.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";
import {outputDir, packaged, port, repoRoot, startServer} from "./fixture.mjs";

const root = await mkdtemp(resolve(tmpdir(), "loopx-oracle-browser-"));
const child = spawn(resolveTestPython(), ["-u", "examples/personal-workspace-browser/goal-acceptance-oracle-backend.py", root], {cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"]});
let diagnostic = "";
child.stderr.on("data", chunk => diagnostic = (diagnostic + chunk).slice(-4000));
const lines = createInterface({input: child.stdout});
let browser, server, context;
try {
  const authority = await new Promise((accept, reject) => {
    const timer = setTimeout(() => reject(Error(`Oracle fixture did not start: ${diagnostic}`)), 20000);
    child.once("exit", () => {clearTimeout(timer); reject(Error(`Oracle fixture exited: ${diagnostic}`));});
    lines.once("line", line => {clearTimeout(timer); accept(JSON.parse(line));});
  });
  const backend = `http://127.0.0.1:${authority.port}`;
  server = await startServer();
  const url = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  context = await openWorkspacePage(browser, url, {beforeGoto: async (_api, page) => {
    await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
    await page.route("**/api/chat/goal-acceptance**", async route => route.fulfill({response: await fetchWorkspaceBackend(route, backend)}));
    await page.route("**/api/actions**", async route => route.fulfill({response: await fetchWorkspaceBackend(route, backend)}));
    await page.route("**/api/chat/delivery-review**", async route => route.fulfill({response: await fetchWorkspaceBackend(route, backend)}));
  }});
  const {page} = context;
  const cli = (...args) => JSON.parse(execFileSync(resolveTestPython(), ["-m", "loopx.cli", "--format", "json", "--registry", authority.registry, "--runtime-root", authority.runtime,
    "goal-acceptance", ...args, "--goal-id", "multi-agent-projection"], {cwd: repoRoot, encoding: "utf8"}));
  const read = () => cli("inspect");
  assert.equal(read().goal_acceptance_contract.enabled, false);
  await page.locator(".personal-goal-link", {hasText: "Multi Agent Projection"}).click();
  await page.getByRole("button", {name: "概览", exact: true}).click();
  // The Agent does the technical preparation. The owner does not enter settings,
  // inspect revisions, upload JSON or confirm the same facts in a second form.
  cli("prepare", "--agent-id", "agent-a", "--document", resolve(root, "owner-contract.json"),
    "--operation-id", "older-export-plan", "--execute");
  const prepared = cli("prepare", "--agent-id", "agent-a", "--document", resolve(root, "owner-contract.json"),
    "--operation-id", "prepare-export-check", "--execute");
  assert.equal(prepared.executed, false);
  assert.equal(read().goal_acceptance_contract.enabled, false, "preparation is not owner approval");
  const panel = page.locator(".delivery-acceptance-contract");
  const adopt = panel.getByRole("button", {name: "采用并检查", exact: true});
  await adopt.waitFor();
  await adopt.scrollIntoViewIfNeeded();
  await mkdir(outputDir, {recursive: true});
  await page.screenshot({path: resolve(outputDir, "oracle-plan-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  await adopt.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "oracle-plan-mobile.png"), animations: "disabled"});
  await page.setViewportSize({width: 1512, height: 982});
  let applyCount = 0;
  page.on("request", req => {if (req.method() === "POST" && /\/api\/actions\/[^/]+\/apply$/.test(new URL(req.url()).pathname)) applyCount++;});
  await adopt.focus(); await page.keyboard.press("Enter");
  await panel.getByText("当前版本已核对", {exact: true}).waitFor();
  assert.equal(applyCount, 1, "one owner action adopts and checks the prepared plan");
  assert.equal(read().goal_acceptance_contract.criteria[0].oracle.state, "current");
  assert.equal(await panel.locator('input[type="file"]').isVisible(), false, "technical import is optional");
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "oracle-current-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  await page.emulateMedia({reducedMotion: "reduce"});
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "oracle-current-mobile.png"), animations: "disabled"});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const run = panel.getByRole("button", {name: "检查交付", exact: true});
  await writeFile(resolve(root, "project/result.txt"), "new version without verification");
  // Read-only observation invalidates the old green state without rerunning.
  await panel.getByText("来源或版本已变化，请重新检查", {exact: true}).waitFor({timeout: 25000});
  assert.equal(await panel.getByText("产物检查通过", {exact: true}).count(), 0);
  const oldOperation = read().goal_acceptance_contract.verification.operation_id;
  assert.equal(applyCount, 1);
  await run.click();
  await panel.getByText("检查失败；修复交付物后可再次检查", {exact: true}).waitFor();
  assert.notEqual(read().goal_acceptance_contract.verification.operation_id, oldOperation);
  await writeFile(resolve(root, "project/result.txt"), "usable export");
  await run.click(); await panel.getByText("当前版本已核对", {exact: true}).waitFor();
  // Advanced migration remains available; a rejected configuration cannot erase
  // the working contract. Recovery reads, rather than automatically executes.
  await panel.getByText("高级配置与诊断", {exact: true}).click();
  const initial = read();
  const invalid = JSON.parse(await readFile(resolve(root, "owner-contract.json"), "utf8"));
  invalid.criteria[0].validation_argv = ["/usr/bin/true", "check.py"];
  await writeFile(resolve(root, "invalid-contract.json"), JSON.stringify(invalid));
  await panel.locator('input[type="file"]').setInputFiles(resolve(root, "invalid-contract.json"));
  const rejectedSave = page.waitForResponse(response => response.url().includes("/api/chat/goal-acceptance") && response.request().method() === "POST");
  await panel.getByRole("button", {name: "保存配置", exact: true}).click();
  assert.equal((await rejectedSave).status(), 409);
  await panel.getByRole("alert").waitFor();
  assert.deepEqual(read(), initial);
  await panel.getByRole("alert").getByRole("button", {name: "刷新结果", exact: true}).click();
  await panel.getByRole("button", {name: "停用检查", exact: true}).click();
  await panel.waitFor({state: "hidden"});
  assert.equal(read().goal_acceptance_contract.enabled, false);
  const expected = "Failed to load resource: the server responded with a status of 409 (Conflict)";
  const at = context.errors.indexOf(expected); if (at >= 0) context.errors.splice(at, 1);
  assert.deepEqual(context.errors.filter(value => !value.includes("404")), []);
  console.log(JSON.stringify({status: "PASS", boundary: "Packaged UI with actual disposable SQLite/HTTP/CLI; Agent preparation is scripted, not live model or installed App evidence", checks: ["automatic prepared-plan discovery", "one contextual owner action", "no mandatory JSON/revision form", "exact native readback", "changed output clears green without execution", "failure/recovery", "advanced import rejection", "disable/default-off", "390px", "keyboard/reduced-motion"]}));
} catch (error) {
  if (context?.page) {
    await mkdir(outputDir, {recursive: true});
    await context.page.screenshot({path: resolve(outputDir, "oracle-failure.png"), animations: "disabled"});
    console.error(JSON.stringify({errors: context.errors, body: (await context.page.locator("body").innerText()).slice(-6000)}));
  }
  throw error;
} finally {
  await context?.close(); await browser?.close(); server?.kill("SIGTERM");
  lines.close(); child.kill("SIGTERM");
  await rm(root, {recursive: true, force: true});
}

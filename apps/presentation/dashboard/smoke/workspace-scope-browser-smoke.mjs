// Packaged steward conversation scoped to a host-granted workspace, against the
// production Chat server and store. A synthetic workspace and the repository's
// fake Codex app-server stand in for the host; no model, registry or Goal is used.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, rename, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { launchBrowser, loadPlaywright, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

const repoRoot = resolve(import.meta.dirname, "../../../..");
const python = resolveTestPython({ repoRoot });
const port = Number(process.env.LOOPX_WORKSPACE_SCOPE_PORT ?? 5413);
const screenshots = process.env.LOOPX_WORKSPACE_SCOPE_SCREENSHOTS;
const root = await mkdtemp(join(tmpdir(), "loopx-workspace-scope-"));
const workspace = join(root, "notes");
await mkdir(workspace);
await mkdir(join(root, "home"));
const codex = join(root, "codex");
const written = spawnSync(python, ["-c", [
  "import pathlib, runpy, sys",
  "source = runpy.run_path('examples/loopx-chat-runtime-smoke.py')['FAKE_CODEX']",
  "path = pathlib.Path(sys.argv[1]); path.write_text(source); path.chmod(0o700)",
].join("\n"), codex], { cwd: repoRoot, encoding: "utf8" });
assert.equal(written.status, 0, written.stderr);

let serverError = "";
function startServer(grant) {
  const child = spawn(python, ["-c", "import sys; from loopx.entrypoint import main; sys.exit(main())",
    "chat", "--no-open", "--port", String(port), "--codex-bin", codex, "--scan-root", workspace,
    "--global-registry", "--project-workspace-grant", grant],
  { cwd: root, env: { ...process.env, HOME: join(root, "home"), PYTHONPATH: repoRoot, LOOPX_USAGE_PING: "0" }, stdio: ["ignore", "pipe", "pipe"] });
  for (const stream of [child.stdout, child.stderr]) stream.on("data", (chunk) => { serverError += String(chunk); });
  return child;
}
let server = startServer("workspace_read");
async function stopServer() {
  if (server.exitCode !== null) return;
  const exited = new Promise((resolveExit) => server.once("exit", resolveExit));
  server.kill();
  await exited;
}
// The first-run usage notice is unrelated to this journey; keep evidence focused.
async function capture(target, name) {
  if (!screenshots) return;
  const dismiss = target.getByRole("button", { name: "收起统计告知" });
  if (await dismiss.isVisible()) await dismiss.click();
  await target.screenshot({ path: join(screenshots, name) });
}
let browser;
try {
  const url = `http://127.0.0.1:${port}/chat/`;
  await waitForHttp(url).catch((error) => { throw new Error(`${error.message}\n${serverError}`); });
  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({ locale: "zh-CN", viewport: { width: 1440, height: 900 } });
  const sessionRequests = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/chat/sessions") sessionRequests.push(request.postDataJSON());
  });
  await page.goto(url, { waitUntil: "networkidle" });
  const scope = page.getByRole("combobox", { name: "范围" });
  assert.equal(await scope.count(), 0, "The steward overview keeps its first screen; scope belongs to the conversation");

  await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "对话" }).click();
  await scope.click();
  await page.getByRole("option", { name: "notes" }).click();
  await page.getByText("工作区对话 · 只读").waitFor();
  const workspaceUrl = page.url();
  assert.match(workspaceUrl, /[?&]workspace=[0-9a-f]{24}(?:&|$)/u);
  const question = "整理一下最近的素材";
  await page.getByLabel("发送消息").fill(question);
  await page.keyboard.press("Enter");
  await page.getByText("Runtime response.").first().waitFor({ timeout: 15_000 });
  // Upgrade a persisted read-only workspace through the same ordinary Scope entry.
  await stopServer();
  server = startServer("workspace_write");
  await waitForHttp(url).catch((error) => { throw new Error(`${error.message}\n${serverError}`); });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByText("工作区对话 · 读写").waitFor({ timeout: 15_000 });
  await page.getByText(question).first().waitFor({ timeout: 15_000 });
  await page.getByLabel("发送消息").fill("继续整理素材");
  await page.keyboard.press("Enter");
  await page.getByText("Runtime response.").nth(1).waitFor({ timeout: 15_000 });
  await capture(page, "ordinary-workspace-conversation.png");

  const projectRequests = sessionRequests.filter((body) => body.context_kind === "project");
  assert.ok(projectRequests.length > 0, "The workspace scope opens a project Session");
  for (const body of projectRequests) {
    assert.equal("goal_id" in body, false, "A workspace Session never names a Goal");
    assert.match(body.project_ref, /^[0-9a-f]{24}$/u);
  }

  await page.reload({ waitUntil: "networkidle" });
  await page.getByText(question).first().waitFor({ timeout: 15_000 });
  await scope.click();
  await page.getByRole("option", { name: "LoopX 管家" }).click();
  await page.waitForURL((current) => !current.searchParams.has("workspace"));
  await page.getByRole("combobox", { name: "范围" }).waitFor();
  assert.equal(await page.getByText(question).count(), 0, "The steward scope does not show the workspace exchange");
  assert.ok(sessionRequests.filter((body) => body.context_kind === "manager").every((body) => !("project_ref" in body)));

  if (screenshots) {
    const narrow = await browser.newPage({ locale: "zh-CN", viewport: { width: 390, height: 844 } });
    await narrow.goto(workspaceUrl, { waitUntil: "networkidle" });
    await narrow.getByText(question).first().waitFor({ timeout: 15_000 });
    await capture(narrow, "ordinary-workspace-conversation-narrow.png");
    await narrow.close();
  }

  await rename(workspace, `${workspace}.moved`);
  await page.goto(workspaceUrl, { waitUntil: "networkidle" });
  await page.getByTestId("workspace-scope-unavailable").waitFor({ timeout: 15_000 });
  await page.getByText(question).first().waitFor({ timeout: 15_000 });
  await page.getByLabel("发送消息").fill("还能继续吗");
  assert.equal(await page.getByRole("button", { name: "发送", exact: true }).isDisabled(), true,
    "A revoked grant keeps history and blocks new messages");
  await capture(page, "ordinary-workspace-grant-rejection.png");
  console.log("workspace scope browser smoke ok");
} finally {
  await browser?.close();
  await stopServer();
  await rm(root, { force: true, recursive: true });
}

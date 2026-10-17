import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import { chmod, mkdtemp, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { waitForHttp } from "../dashboard-browser-smoke-support.mjs";
import { outputDir, packaged, repoRoot } from "./fixture.mjs";
import { fetchWorkspaceBackend, openWorkspacePage } from "./scenario-context.mjs";

// A fresh install without a usable Agent: the service refuses the first Goal's
// preview with a typed gate (which Agent, the catalog's reason). The page must
// tell the owner in their own language what is missing and what to do, never
// show the protocol sentence, write nothing, keep the form's draft, and go
// through once the Agent is available. The fixture answers the preview with
// the same gate shape the real service sends.

function emptyWorkspace(fixture) {
  fixture.goal_count = 0;
  fixture.run_count = 0;
  fixture.run_history.goals = [];
  fixture.run_history.recent_runs = [];
  fixture.run_history.goal_count = 0;
  fixture.run_history.run_count = 0;
  fixture.attention_queue.items = [];
  fixture.attention_queue.item_count = 0;
  fixture.attention_queue.needs_user_or_controller = 0;
  fixture.attention_queue.needs_codex = 0;
  fixture.attention_queue.watching_external_evidence = 0;
  if (fixture.usage_summary) fixture.usage_summary.goals = [];
  fixture.contract = { ok: true, summary: { errors: 0, warnings: 1, checks: 1 }, errors: [],
    warnings: ["registry file does not exist: [local-path]"], checks: ["run-history goals=0 runs=0"] };
}

async function realUnavailableAgent({ browser, collectCoverage, url }) {
  const root = await mkdtemp(join(tmpdir(), "loopx-first-goal-no-agent-"));
  const python = resolveTestPython({ repoRoot });
  const child = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/fresh-install-backend.py"), root, "--no-agent"], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ["ignore", "pipe", "pipe"] });
  const closed = once(child, "close");
  const lines = createInterface({ input: child.stdout });
  let diagnostics = "", session;
  child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-4000); });
  try {
    const ready = await Promise.race([once(lines, "line").then(([line]) => JSON.parse(line)), closed.then(() => { throw new Error(`fresh-install backend exited: ${diagnostics}`); })]);
    const backend = `http://127.0.0.1:${ready.port}`;
    await waitForHttp(`${backend}/status.json?view=workspace-directory`);
    const catalog = await (await fetch(`${backend}/api/chat/capabilities`)).json();
    assert.equal(catalog.adapters.find((row) => row.agent_id === "codex").available, false);
    session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: async (_api, page) => {
      const proxied = new Set(["/status.json", "/api/chat/capabilities", "/api/chat/projects"]);
      await page.route((target) => proxied.has(target.pathname) || target.pathname.startsWith("/api/actions"), async (route) => {
        const response = await fetchWorkspaceBackend(route, backend);
        await route.fulfill({ response });
      });
    } });
    const { page } = session;
    const requests = [];
    const previews = [];
    page.on("request", (request) => {
      if (request.method() === "POST") requests.push(new URL(request.url()).pathname);
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/actions/preview") previews.push(request.postDataJSON());
    });
    const firstGoal = page.getByTestId("personal-first-goal");
    await firstGoal.waitFor({ state: "visible", timeout: 20_000 });
    const title = "Track the weekly release checklist";
    async function preview() {
      await firstGoal.locator(".personal-first-goal-create").click();
      const form = page.locator("dialog.personal-action-form");
      await form.waitFor({ state: "visible" });
      await form.getByLabel("目标").fill(title);
      await form.getByLabel("完成标准").fill("A checklist exists and is reviewed every week");
      await form.locator("button[type=submit]").click();
    }
    await preview();
    const drawer = page.locator(".personal-context-drawer");
    await drawer.waitFor({ state: "visible", timeout: 20_000 });
    const text = (await drawer.innerText()).replace(/\s+/g, " ");
    assert.match(text, /还不能开始这个 Goal/);
    assert.match(text, /codex/i);
    assert.match(text, /根据上述原因修复/);
    assert.match(text, /没有写入任何 Goal/);
    assert.doesNotMatch(text, /endpoint is not healthy|invalid_action_preview/);
    assert.doesNotMatch(text, /提案状态刷新失败/, "A refused local preview has no stored action to read back");
    assert.equal(await drawer.getByRole("button", { name: "稍后", exact: true }).count(), 0, "No persisted defer/reject operation exists for a refused preview");
    assert.equal(await drawer.getByRole("button", { name: "拒绝", exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: resolve(outputDir, "first-goal-agent-unavailable-live-phone.png"), animations: "disabled" });
    const registry = join(ready.project, ".loopx", "registry.json");
    await assert.rejects(stat(registry), { code: "ENOENT" });
    assert.deepEqual((await (await fetch(`${backend}/status.json?view=workspace-directory`)).json()).goals, []);

    // Only a temporary exit-only executable becomes available. Preview never
    // applies a Goal or starts a Turn; real credentials/binaries stay isolated.
    await writeFile(join(ready.bins, "codex"), "#!/bin/sh\nexit 0\n");
    await chmod(join(ready.bins, "codex"), 0o755);
    await drawer.getByRole("button", { name: "按最新状态重新检查", exact: true }).click();
    await drawer.getByText("新 Goal · 待你确认", { exact: true }).waitFor({ state: "visible", timeout: 20_000 });
    await assert.rejects(stat(registry), { code: "ENOENT" });
    assert.equal(requests.filter((path) => path === "/api/actions/preview").length, 2);
    assert.equal(previews[0].normalized_parameters.title, title);
    assert.deepEqual(previews[1], previews[0], "Rechecking retains the owner's original draft and operation identity");
    assert.ok(requests.every((path) => !/\/apply$|\/turns$/.test(path)), "preview recovery grants no apply or Turn");
    return await session.close();
  } catch (error) {
    if (diagnostics) process.stderr.write(diagnostics);
    await session?.close().catch(() => {});
    throw error;
  } finally {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) { child.kill("SIGTERM"); const kill = setTimeout(() => child.kill("SIGKILL"), 5000); try { await closed; } finally { clearTimeout(kill); } }
  }
}


async function realRemoteMapping({ browser, collectCoverage, url, locale }) {
  const root = await mkdtemp(join(tmpdir(), "loopx-first-goal-mapping-"));
  const python = resolveTestPython({ repoRoot });
  const child = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/fresh-install-backend.py"), root, "--no-agent"], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ["ignore", "pipe", "pipe"] });
  const closed = once(child, "close"), lines = createInterface({ input: child.stdout });
  let diagnostics = "", session;
  child.stderr.on("data", chunk => diagnostics = (diagnostics + chunk).slice(-4000));
  try {
    const ready = await Promise.race([once(lines, "line").then(([line]) => JSON.parse(line)), closed.then(() => { throw new Error(`mapping backend exited: ${diagnostics}`); })]);
    const backend = `http://127.0.0.1:${ready.port}`;
    await waitForHttp(`${backend}/status.json?view=workspace-directory`);
    const binary = join(ready.bins, "remote-fixture");
    const marker = join(root, "remote-was-executed");
    await writeFile(binary, `#!/bin/sh\necho unexpected > "${marker}"\nexit 0\n`); await chmod(binary, 0o755);
    const register = mapped => execFileSync(python, ["-c", `
import json, sys
from pathlib import Path
from loopx.chat_endpoints import AgentEndpointRegistry
runtime, binary, project, mapped = sys.argv[1:]
endpoint = {"agent_id":"remote-first", "display_name":"Remote fixture", "command":[binary], "location":"remote"}
if mapped == "yes": endpoint["workspace_mapping"] = [{"local_root":project,"agent_root":"/synthetic/workspace"}]
AgentEndpointRegistry(Path(runtime)/"chat").upsert(endpoint)
`, ready.runtime, binary, ready.project, mapped ? "yes" : "no"], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot } });
    register(false);
    const catalog = await (await fetch(`${backend}/api/chat/capabilities`)).json();
    const agent = catalog.adapters.find(row => row.agent_id === "remote-first");
    assert.equal(agent.available, true); assert.equal(agent.tool_calls, true); assert.equal(agent.trust_scope, "read_only");
    const posts = [], previews = [], responses = [];
    {
      session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: locale === "en" ? 390 : 1440, height: 900 }, beforeGoto: async (_api, page) => {
        await page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale);
        const proxied = new Set(["/status.json", "/api/chat/capabilities", "/api/chat/projects"]);
        await page.route(target => proxied.has(target.pathname) || target.pathname.startsWith("/api/actions"), async route => route.fulfill({ response: await fetchWorkspaceBackend(route, backend) }));
      } });
      const { page } = session;
      await page.emulateMedia({ reducedMotion: "reduce" });
      page.on("request", req => { if (req.method() === "POST") posts.push(new URL(req.url()).pathname); });
      page.on("response", response => { if (new URL(response.url()).pathname === "/api/actions/preview") responses.push(response.status()); });
      page.on("request", req => { if (req.method() === "POST" && new URL(req.url()).pathname === "/api/actions/preview") previews.push(req.postDataJSON()); });
      const first = page.getByTestId("personal-first-goal"); await first.waitFor();
      await first.locator(".personal-first-goal-create").click();
      const form = page.locator("dialog.personal-action-form"); await form.waitFor();
      const objective = form.getByLabel(locale === "en" ? "Objective" : "目标", { exact: true });
      const completion = form.getByLabel(locale === "en" ? "Completion criteria" : "完成标准", { exact: true });
      await objective.fill("Review the remote release checklist"); await completion.fill("A reviewed checklist is retained");
      const before = previews.length;
      await form.locator("button[type=submit]").click();
      const notice = page.getByRole("alert").filter({ hasText: "remote Agent endpoint needs a workspace mapping" });
      await notice.waitFor({ timeout: 10_000 });
      assert.equal(previews.at(-1).normalized_parameters.agent_id, "remote-first");
      assert.equal(responses.at(-1), 400);
      assert.equal(await form.isVisible(), true);
      assert.equal(await objective.inputValue(), "Review the remote release checklist");
      assert.equal(await completion.inputValue(), "A reviewed checklist is retained");
      assert.doesNotMatch(await page.locator("body").innerText(), /先安装并登录|not installed or not signed in|Install and sign in/);
      await page.screenshot({ path: resolve(outputDir, `first-goal-mapping-${locale}.png`), animations: "disabled" });
      const registry = join(ready.project, ".loopx", "registry.json");
      await assert.rejects(stat(registry), { code: "ENOENT" });
      assert.deepEqual((await (await fetch(`${backend}/api/actions`)).json()).proposals, []);
      let refused = previews.length - 1;
      if (locale === "zh-CN") {
        // A deliberate edit is a new request; unchanged retry must retain it.
        await objective.fill("Review the corrected remote release checklist");
        const rejectedAgain = page.waitForResponse(response => new URL(response.url()).pathname === "/api/actions/preview");
        await form.locator("button[type=submit]").click();
        assert.equal((await rejectedAgain).status(), 400);
        refused = previews.length - 1;
        assert.notEqual(previews[refused].idempotency_key, previews[before].idempotency_key);
        assert.notEqual(previews[refused].normalized_parameters.goal_id, previews[before].normalized_parameters.goal_id);
      }
      register(true);
      await form.locator("button[type=submit]").click();
      const drawer = page.locator(".personal-context-drawer"); await drawer.waitFor();
      await drawer.getByText(locale === "en" ? "New Goal · Awaiting you" : "新 Goal · 待你确认", { exact: true }).waitFor();
      assert.equal(responses.at(-1), 201);
      assert.equal(previews.length, refused + 2);
      assert.deepEqual(previews[refused + 1], previews[refused], "Only repairing mapping replays the exact request, without retyping");
      await assert.rejects(stat(registry), { code: "ENOENT" });
      await assert.rejects(stat(marker), { code: "ENOENT" });
      assert.ok(posts.every(path => !/\/apply$|\/turns$|\/sessions$/.test(path)), "Mapping repair grants no Goal apply, Session or model Turn");
      await page.keyboard.press("Escape");
      await session.close(); session = null;
    }
    return [];
  } catch (error) {
    if (diagnostics) process.stderr.write(diagnostics);
    await session?.page.screenshot({ path: resolve(outputDir, "first-goal-mapping-failed.png") }).catch(() => {});
    throw error;
  } finally {
    await session?.close().catch(() => {}); lines.close();
    if (child.exitCode === null && child.signalCode === null) { child.kill("SIGTERM"); const kill = setTimeout(() => child.kill("SIGKILL"), 5000); try { await closed; } finally { clearTimeout(kill); } }
  }
}

export const firstGoalAgentUnavailableScenario = {
  id: "first-goal-agent-unavailable",
  async run({ browser, collectCoverage, url }) {
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 },
      beforeGoto: (api) => { api.transformStatus = (fixture) => emptyWorkspace(fixture); } });
    const { api, page } = session;
    try {
      const firstGoal = page.getByTestId("personal-first-goal");
      await firstGoal.waitFor({ state: "visible", timeout: 15_000 });
      await firstGoal.locator(".personal-first-goal-create").click();
      const form = page.locator("dialog.personal-action-form");
      await form.waitFor({ state: "visible", timeout: 10_000 });
      await form.getByLabel("目标").fill("Track the weekly release checklist");
      await form.getByLabel("完成标准").fill("A checklist exists and is reviewed every week");
      api.nextActionPreviewGate = { kind: "agent_unavailable", agent_id: "codex", registered: true, unavailable_reason: "executable_not_found", executable: "/opt/loopx/bin/codex",
        summary: "The selected Agent (codex) is not available to start this Goal.", next_action: "Install and sign in to the Agent, then create the Goal again.", detail: "selected Agent endpoint is not healthy" };
      await form.locator("button[type=submit]").click();
      const drawer = page.locator(".personal-context-drawer");
      await drawer.waitFor({ state: "visible", timeout: 10_000 });
      const text = (await drawer.innerText()).replace(/\s+/g, " ");
      assert.match(text, /还不能开始这个 Goal/, `the owner reads which Agent cannot start the Goal: ${text.slice(0, 300)}`);
      assert.match(text, /本机没有找到命令 \/opt\/loopx\/bin\/codex/, "the catalog's own reason is shown");
      assert.match(text, /根据上述原因修复/, "the next action follows the reported availability fact");
      assert.match(text, /没有写入任何 Goal/, "nothing was written");
      assert.doesNotMatch(text, /endpoint is not healthy|not available to start/, "no protocol sentence reaches the owner");
      await page.screenshot({ path: resolve(outputDir, "first-goal-agent-unavailable.png"), animations: "disabled" });
      assert.equal(api.actionPreviews.filter((row) => row.gated).length, 1, "exactly one refused preview");

      // Once the Agent is available the same request previews normally.
      await page.locator(".personal-drawer-close").click();
      await firstGoal.locator(".personal-first-goal-create").click();
      await form.waitFor({ state: "visible", timeout: 10_000 });
      await form.getByLabel("目标").fill("Track the weekly release checklist");
      await form.getByLabel("完成标准").fill("A checklist exists and is reviewed every week");
      await form.locator("button[type=submit]").click();
      await drawer.waitFor({ state: "visible", timeout: 10_000 });
      await drawer.getByText("新 Goal · 待你确认", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
      const coverageEntries = await session.close();
      if (packaged) {
        coverageEntries.push(...await realUnavailableAgent({ browser, collectCoverage, url }));
        for (const locale of ["zh-CN", "en"]) coverageEntries.push(...await realRemoteMapping({ browser, collectCoverage, url, locale }));
      }
      return { coverageEntries };
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }
  },
};

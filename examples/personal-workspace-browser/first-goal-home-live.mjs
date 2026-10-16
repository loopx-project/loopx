import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, packaged, repoRoot } from "./fixture.mjs";
import { fetchWorkspaceBackend, openWorkspacePage } from "./scenario-context.mjs";

// End to end against the real service: a disposable `loopx chat` on a fresh
// runtime with no registry at all, as an installed LoopX opens for the first
// time. The page reads the real discovery view, which states its source facts:
// zero Goals and the service's own missing-registry warning. The home must
// open to the first-Goal state, not a health banner, keep that warning in the
// collapsed diagnostics without a local path, let an example fill the composer
// without sending, and open the typed Goal form fed by the real project catalog.
// Confirming the first Goal goes through the real action owner. The only Agent
// is a stub that exits at once, so no model ever runs: the service registers
// the Goal and its first task, reports the first Turn it could not start, and
// offers the original operation to retry; a false success would be the defect.

export const firstGoalHomeLiveScenario = {
  id: "first-goal-home-live",
  async run({ browser, collectCoverage, url }) {
    // The real service serves the packaged bundle; the development lane has none to serve.
    if (!packaged) return { coverageEntries: [], note: "live first-Goal home runs against the packaged bundle only" };
    const root = await mkdtemp(join(tmpdir(), "loopx-first-goal-live-"));
    const python = resolveTestPython({ repoRoot });
    const child = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/fresh-install-backend.py"), root], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ["ignore", "pipe", "pipe"] });
    const closed = once(child, "close");
    const lines = createInterface({ input: child.stdout });
    let diagnostics = "", session;
    child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-4000); });
    try {
      const ready = await Promise.race([once(lines, "line").then(([line]) => JSON.parse(line)), closed.then(() => { throw new Error(`fresh-install backend exited: ${diagnostics}`); })]);
      const backend = `http://127.0.0.1:${ready.port}`;
      for (let attempt = 0; attempt < 100; attempt += 1) {
        try { if ((await fetch(`${backend}/status.json?view=workspace-directory`)).ok) break; } catch { /* not listening yet */ }
        await new Promise((resolveWait) => setTimeout(resolveWait, 200));
      }
      const directory = await (await fetch(`${backend}/status.json?view=workspace-directory`)).json();
      assert.equal(directory.ok, true);
      assert.deepEqual(directory.goals, []);
      assert.ok(directory.contract?.warnings.some((row) => /^registry file does not exist/.test(row)), `the real discovery view names the missing registry: ${JSON.stringify(directory.contract)}`);
      assert.ok(!JSON.stringify(directory.contract).includes(root), "no local path leaves the service");
      session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: async (_api, page) => {
        // The document URL also ends in "status.json" (its statusUrl query), so match paths, not a glob.
        const proxied = new Set(["/status.json", "/api/chat/capabilities", "/api/chat/projects"]);
        await page.route((target) => proxied.has(target.pathname) || target.pathname.startsWith("/api/actions"), async (route) => {
          try { const response = await fetchWorkspaceBackend(route, backend); await route.fulfill({ response }); }
          catch { await route.abort().catch(() => {}); }
        });
      } });
      const { page } = session;
      const turnRequests = [];
      page.on("request", (request) => { if (request.method() === "POST" && /\/api\/chat\/sessions\/[^/]+\/turns/.test(request.url())) turnRequests.push(request.url()); });
      const firstGoal = page.getByTestId("personal-first-goal");
      await firstGoal.waitFor({ state: "visible", timeout: 20_000 });
      assert.equal(await page.locator(".personal-system-health-banner").count(), 0, "The real missing registry is not shown as a health concern");
      assert.match(await firstGoal.locator("h2").innerText(), /还没有 Goal/);
      const details = firstGoal.locator("details.personal-first-goal-diagnostics");
      await details.waitFor({ state: "visible", timeout: 10_000 });
      assert.equal(await details.getAttribute("open"), null, "Diagnostics stay collapsed");
      // The disclosure stays closed, so read its lines, not the visible text.
      const reasons = await details.locator("li").allTextContents();
      assert.ok(reasons.some((row) => /registry file does not exist/.test(row)), `the diagnostics carry the service's own warning: ${reasons.join(" | ")}`);
      assert.ok(reasons.every((row) => !row.includes(root)), "the diagnostics show no local path");
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-live.png"), animations: "disabled" });

      // An example only fills the composer; nothing is sent to the real service.
      const composer = page.locator(".personal-channel-composer textarea");
      const example = firstGoal.locator(".personal-first-goal-examples button").first();
      const exampleText = (await example.innerText()).trim();
      await example.click();
      assert.equal((await composer.inputValue()).trim(), exampleText);
      assert.equal(turnRequests.length, 0, `an example sends nothing: ${turnRequests.join(", ")}`);
      await page.keyboard.press("Escape");

      // Create a Goal opens the typed form; its working directories come from the real project catalog.
      await firstGoal.locator(".personal-first-goal-create").click();
      const form = page.locator("dialog.personal-action-form");
      await form.waitFor({ state: "visible", timeout: 10_000 });
      const projects = await (await fetch(`${backend}/api/chat/projects`)).json();
      assert.ok(projects.projects.length >= 1, "the real service registers the scan root as a project");
      await form.getByLabel("工作目录").locator("option", { hasText: projects.projects[0].title }).waitFor({ state: "attached", timeout: 10_000 });
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-live-create.png"), animations: "disabled" });

      // The first Goal is created from this page through the real action
      // owner: review, confirm, readback. No terminal, no pre-existing registry.
      const title = "Track the weekly release checklist";
      await form.getByLabel("目标").fill(title);
      await form.getByLabel("完成标准").fill("A checklist exists and is reviewed every week");
      await form.getByLabel("工作目录").selectOption({ label: projects.projects[0].title });
      await form.locator("button[type=submit]").click();
      const drawer = page.locator(".personal-context-drawer");
      await drawer.waitFor({ state: "visible", timeout: 20_000 });
      await drawer.getByText("新 Goal · 待你确认", { exact: true }).waitFor({ state: "visible", timeout: 20_000 });
      // The snapshot readback completes shortly after the card appears; a stale-card notice must not remain.
      await drawer.getByText("提案状态刷新失败", { exact: false }).waitFor({ state: "hidden", timeout: 15_000 });
      await drawer.locator(".personal-primary-action").click();
      const applyButton = drawer.getByRole("button", { name: "确认并应用", exact: true });
      if (await applyButton.count()) await applyButton.click();
      await drawer.getByText("新 Goal · 出错", { exact: true }).waitFor({ state: "visible", timeout: 60_000 });
      const outcome = (await drawer.innerText()).replace(/\s+/g, " ");
      assert.match(outcome, /Goal 初始化尚未完成/, `the service names what is not finished: ${outcome.slice(0, 300)}`);
      assert.match(outcome, /Goal ID: goal-[0-9a-f-]+ · 已应用/, "the Goal itself was registered before the first Turn failed");
      assert.ok(await drawer.getByRole("button", { name: "重试原操作", exact: true }).count(), "the original operation can be retried");
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-live-applied.png"), animations: "disabled" });

      // The real service now has a registry with exactly this Goal, and the
      // home shows it instead of the first-Goal state.
      const { existsSync, readFileSync, readdirSync } = await import("node:fs");
      const registryPath = join(ready.project, ".loopx", "registry.json");
      assert.ok(existsSync(registryPath), "the first creation writes the registry");
      // The Goal persists where the serving Chat keeps its own state (its
      // selected runtime root), never in the implicit default beside it.
      const registry = JSON.parse(readFileSync(registryPath, "utf8"));
      assert.equal(resolve(String(registry.common_runtime_root)), resolve(ready.runtime), `the registry records the service's runtime root: ${registry.common_runtime_root}`);
      const goalId = registry.goals[0].id;
      const underRuntime = (dir) => existsSync(dir) ? readdirSync(dir, { recursive: true }).map(String) : [];
      assert.ok(underRuntime(ready.runtime).some((entry) => entry.includes(goalId)), "the Goal's canonical state lives under the service's runtime root");
      assert.ok(!underRuntime(ready.default_runtime).some((entry) => entry.includes(goalId)), "nothing of the Goal lands in the implicit default runtime root");
      const after = await (await fetch(`${backend}/status.json?view=workspace-directory`)).json();
      assert.deepEqual(after.goals.map((goal) => goal.display_name), [title], "the real discovery view lists the created Goal");
      assert.deepEqual(after.contract.warnings, [], "no missing-registry warning remains");
      await page.locator(".personal-drawer-close").click();
      await page.getByRole("button", { name: "刷新状态", exact: true }).click();
      await page.locator(".personal-goal-row", { hasText: title }).first().waitFor({ state: "visible", timeout: 20_000 });
      await firstGoal.waitFor({ state: "hidden", timeout: 20_000 });
      await page.screenshot({ path: resolve(outputDir, "first-goal-home-live-home.png"), animations: "disabled" });
      return { coverageEntries: await session.close(), note: "real loopx chat on a fresh runtime: zero-Goal discovery with its own warning; first Goal registered from the page through the real action owner with the stub Agent's failed first Turn reported honestly; registry written, directory lists it" };
    } catch (error) {
      if (diagnostics) process.stderr.write(diagnostics);
      await session?.close().catch(() => {});
      throw error;
    } finally {
      lines.close();
      if (child.exitCode === null && child.signalCode === null) { child.kill("SIGTERM"); const kill = setTimeout(() => child.kill("SIGKILL"), 5000); try { await closed; } finally { clearTimeout(kill); } }
    }
  },
};

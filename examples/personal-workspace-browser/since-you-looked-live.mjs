import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { promisify } from "node:util";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { fetchWorkspaceBackend, openWorkspacePage } from "./scenario-context.mjs";

// End to end against the real service: a disposable real Chat/Todo owner
// serves status.json while the owner looks at a Goal, leaves, and the CLI
// changes the Goal's Todos. An optional user action raised while away is not
// a decision; a completed agent task and a gate are, counted exactly as the
// typed attention lane counts them; Mark seen advances the marker. Only the
// status path is proxied; everything else keeps the fixture.

const goalId = "progress-projection";
const optionalText = "Consider renaming the export folder";
const gateText = "Approve the restated FY2023 operating cash flow";
const doneText = "Reconcile the March export";

export const sinceYouLookedLiveScenario = {
  id: "since-you-looked-live",
  async run({ browser, collectCoverage, url }) {
    const root = await mkdtemp(join(tmpdir(), "loopx-seen-live-"));
    const python = resolveTestPython({ repoRoot });
    const child = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/goal-workspace-todo-backend.py"), root], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ["ignore", "pipe", "pipe"] });
    const closed = once(child, "close");
    const lines = createInterface({ input: child.stdout });
    let diagnostics = "", ready, session;
    child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-4000); });
    const cli = (...args) => promisify(execFile)(python, ["-c", "from loopx.entrypoint import main; main()", "--format", "json", "--registry", ready.registry, "todo", ...args], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot } }).then(({ stdout }) => JSON.parse(stdout));
    try {
      ready = await Promise.race([once(lines, "line").then(([line]) => JSON.parse(line)), closed.then(() => { throw new Error(`todo backend exited: ${diagnostics}`); })]);
      const backend = `http://127.0.0.1:${ready.port}`;
      session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: async (_api, page) => {
        // The document URL also ends in "status.json" (its statusUrl query), so match the path, not a glob.
        await page.route((target) => target.pathname === "/status.json", async (route) => {
          try { const response = await fetchWorkspaceBackend(route, backend); await route.fulfill({ response }); }
          catch { await route.abort().catch(() => {}); }
        });
      } });
      const { page } = session;
      const line = page.getByTestId("goal-since-you-looked");
      const overview = page.locator(".goal-overview");
      const openGoal = async () => {
        await page.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
        await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 20_000 });
        await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
      };
      const home = () => page.getByRole("button", { name: /^LoopX 管家/ }).first().click();
      const refresh = () => page.getByRole("button", { name: "刷新状态", exact: true }).click();
      await openGoal();
      await page.waitForTimeout(1_000);
      assert.equal(await line.count(), 0, "A first look has nothing to compare");
      await home();

      // Away: an optional reminder for the owner arrives. It is listed, but it is not a decision.
      assert.equal((await cli("add", "--goal-id", goalId, "--role", "user", "--task-class", "user_action", "--text", optionalText)).ok, true);
      await refresh();
      await openGoal();
      await overview.getByText(optionalText).first().waitFor({ state: "visible", timeout: 20_000 });
      await page.waitForTimeout(1_500);
      assert.equal(await line.count(), 0, "An optional user action is not reported as a new decision");
      await home();

      // Away: an agent task is added and completed, and a gate is raised.
      const task = await cli("add", "--goal-id", goalId, "--role", "agent", "--task-class", "advancement_task", "--text", doneText);
      assert.equal(task.ok, true);
      const completed = await cli("complete", "--goal-id", goalId, "--role", "agent", "--todo-id", task.todo_id);
      assert.equal(completed.ok, true, JSON.stringify(completed).slice(0, 200));
      assert.equal((await cli("add", "--goal-id", goalId, "--role", "user", "--task-class", "user_gate", "--text", gateText)).ok, true);
      await refresh();
      await openGoal();
      await line.waitFor({ state: "visible", timeout: 20_000 });
      const text = await line.innerText();
      assert.match(text, /新完成 1 项/, `the real completion is counted once: ${text}`);
      // The decision count is the typed attention lane's own count of items waiting on the owner.
      const waiting = (await page.getByTestId("goal-vitals").innerText()).replace(/\s+/g, " ").match(/(\d+) 等你决定/);
      assert.ok(waiting && Number(waiting[1]) >= 1, "the lane shows at least the gate waiting on the owner");
      assert.match(text, new RegExp(`新增 ${waiting[1]} 项等你决定`), `decisions match the lane (${waiting[1]}): ${text}`);
      await page.screenshot({ path: resolve(outputDir, "since-you-looked-live.png"), animations: "disabled" });
      await line.getByRole("button", { name: "标记已读" }).click();
      await line.waitFor({ state: "hidden", timeout: 5_000 });
      await home();
      await openGoal();
      await page.waitForTimeout(1_000);
      assert.equal(await line.count(), 0, "Nothing new after Mark seen");
      return { coverageEntries: await session.close(), note: "real Todo owner and status; CLI optional action, completion and gate reach the return line" };
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

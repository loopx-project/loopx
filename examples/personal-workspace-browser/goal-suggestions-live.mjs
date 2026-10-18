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
// serves status.json. An optional user action raised through the CLI is
// listed but never becomes a "decision" suggestion; a real user gate does,
// counted as the typed attention lane counts it, and acting on it opens the
// overview that shows the gate. Only the status path is proxied.

const goalId = "progress-projection";
const optionalText = "Consider renaming the export folder";
const gateText = "Approve the restated FY2023 operating cash flow";

export const goalSuggestionsLiveScenario = {
  id: "goal-suggestions-live",
  async run({ browser, collectCoverage, url }) {
    const root = await mkdtemp(join(tmpdir(), "loopx-suggest-live-"));
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
      const summary = page.locator(".personal-composer-tools > summary");
      const openConversation = async () => {
        await page.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
        await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 20_000 });
        await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
        await summary.waitFor({ state: "visible", timeout: 20_000 });
      };
      const home = () => page.getByRole("button", { name: /^LoopX 管家/ }).first().click();
      const refresh = () => page.getByRole("button", { name: "刷新状态", exact: true }).click();

      // An optional user action is listed by the real owner but is not a decision.
      assert.equal((await cli("add", "--goal-id", goalId, "--role", "user", "--task-class", "user_action", "--text", optionalText)).ok, true);
      await openConversation();
      await page.waitForTimeout(1_000);
      const before = (await summary.innerText()).trim();
      assert.doesNotMatch(before, /等你决定/, `an optional user action is not a decision suggestion: ${before}`);
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
      const vitals = (await page.getByTestId("goal-vitals").innerText()).replace(/\s+/g, " ");
      assert.match(vitals, /0 等你决定|可选/, `the lane itself shows the item as optional: ${vitals}`);
      await home();

      // A real gate is a decision; the count is the lane's own count.
      assert.equal((await cli("add", "--goal-id", goalId, "--role", "user", "--task-class", "user_gate", "--text", gateText)).ok, true);
      await refresh();
      await openConversation();
      await summary.filter({ hasText: /等你决定/ }).waitFor({ state: "visible", timeout: 20_000 });
      const after = (await summary.innerText()).trim();
      const count = after.match(/先处理 (\d+) 项等你决定的事项/);
      assert.ok(count, `the first suggestion is the decision: ${after}`);
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
      const lane = (await page.getByTestId("goal-vitals").innerText()).replace(/\s+/g, " ").match(/(\d+) 等你决定/);
      assert.ok(lane, "the lane counts the gate");
      assert.equal(count[1], lane[1], `the suggestion count equals the lane's count (${lane[1]})`);
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
      await summary.waitFor({ state: "visible" });
      await summary.click();
      const row = page.getByTestId("goal-suggestions");
      await row.waitFor({ state: "visible", timeout: 10_000 });
      await page.screenshot({ path: resolve(outputDir, "goal-suggestions-live.png"), animations: "disabled" });
      await row.locator('button[data-suggestion="open_decisions"]').click();
      await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 10_000 });
      await page.locator(".goal-overview-attention").getByRole("button").filter({ hasText: gateText })
        .waitFor({ state: "visible", timeout: 10_000 });
      return { coverageEntries: await session.close(), note: "real Todo owner and status; CLI user_action stays optional, user_gate becomes the decision suggestion" };
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

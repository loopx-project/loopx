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
// serves status; a user gate is added through the CLI while the owner is
// away; the enabled page's background re-read hits the real status and
// notifies once with the gate's text; clicking opens that real item.

const gateText = "Approve the restated FY2023 operating cash flow";

export const attentionNotificationsLiveScenario = {
  id: "attention-notifications-live",
  async run({ browser, collectCoverage, url }) {
    const root = await mkdtemp(join(tmpdir(), "loopx-notify-live-"));
    const python = resolveTestPython({ repoRoot });
    const child = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/goal-workspace-todo-backend.py"), root], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ["ignore", "pipe", "pipe"] });
    const closed = once(child, "close");
    const lines = createInterface({ input: child.stdout });
    let diagnostics = "";
    child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-4000); });
    let session;
    try {
      const ready = await Promise.race([once(lines, "line").then(([line]) => JSON.parse(line)), closed.then(() => { throw new Error(`todo backend exited: ${diagnostics}`); })]);
      const backend = `http://127.0.0.1:${ready.port}`;
      session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: async (_api, page) => {
        await page.addInitScript(() => {
          window.__loopxNotifications = [];
          class FakeNotification {
            static permission = "default";
            static async requestPermission() { FakeNotification.permission = "granted"; return "granted"; }
            constructor(title, options) { this.title = title; this.body = options?.body ?? ""; this.tag = options?.tag ?? ""; this.closed = false; window.__loopxNotifications.push(this); }
            close() { this.closed = true; }
          }
          window.Notification = FakeNotification;
          document.hasFocus = () => false;
        });
        // Only the status read is proxied: the document URL also ends in
        // "status.json" (its statusUrl query), so a glob would swallow the page.
        // The page aborts superseded status reads; an aborted proxy fetch is not a failure.
        await page.route((target) => target.pathname === "/status.json", async (route) => {
          try { const response = await fetchWorkspaceBackend(route, backend); await route.fulfill({ response }); }
          catch { await route.abort().catch(() => {}); }
        });
      } });
      const { page } = session;
      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "外观", exact: true }).click();
      const settings = page.getByTestId("attention-notification-settings");
      await settings.waitFor({ state: "visible" });
      await page.clock.install();
      await settings.getByRole("checkbox").click();
      await page.waitForFunction(() => document.querySelector('[data-testid="attention-notification-permission"]')?.textContent?.includes("已允许"));
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      assert.equal((await page.evaluate(() => window.__loopxNotifications.length)), 0);

      // An optional user action raised in the real Todo owner is listed but is
      // not a decision: the background re-read sees it and nothing notifies.
      const optional = JSON.parse((await promisify(execFile)(python, ["-c", "from loopx.entrypoint import main; main()", "--format", "json", "--registry", ready.registry, "todo", "add", "--goal-id", "progress-projection", "--role", "user", "--task-class", "user_action", "--text", "Consider renaming the export folder"], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot } })).stdout);
      assert.equal(optional.ok, true);
      await page.clock.runFor(31_000);
      await page.waitForTimeout(1_500);
      assert.equal((await page.evaluate(() => window.__loopxNotifications.length)), 0, "An optional user action does not interrupt the owner");

      // A decision raised in the real Todo owner while the owner is away.
      // A second optional item first seen in the SAME read as the gate must
      // remain optional too; remembering an older optional identity alone is insufficient.
      const simultaneous = JSON.parse((await promisify(execFile)(python, ["-c", "from loopx.entrypoint import main; main()", "--format", "json", "--registry", ready.registry, "todo", "add", "--goal-id", "progress-projection", "--role", "user", "--task-class", "user_action", "--text", "Consider grouping the export notes"], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot } })).stdout);
      assert.equal(simultaneous.ok, true);
      const { stdout } = await promisify(execFile)(python, ["-c", "from loopx.entrypoint import main; main()", "--format", "json", "--registry", ready.registry, "todo", "add", "--goal-id", "progress-projection", "--role", "user", "--task-class", "user_gate", "--text", gateText], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot } });
      const added = JSON.parse(stdout);
      assert.equal(added.ok, true);
      await page.clock.runFor(31_000);
      await page.waitForFunction(() => window.__loopxNotifications.length > 0, null, { timeout: 20_000 });
      const [first] = await page.evaluate(() => window.__loopxNotifications.map((row) => ({ title: row.title, body: row.body })));
      assert.equal(first.body, gateText, "The notification carries the real gate's text");
      assert.equal((await page.evaluate(() => window.__loopxNotifications.length)), 1, "Only the gate notified, never the optional action");
      await page.evaluate(() => window.__loopxNotifications[0].onclick());
      const drawer = page.locator(".personal-context-drawer");
      await drawer.waitFor({ state: "visible", timeout: 15_000 });
      assert.match(await drawer.innerText(), new RegExp(gateText.slice(0, 24)), "Clicking opens the real item");
      await page.screenshot({ path: resolve(outputDir, "attention-notifications-live.png"), animations: "disabled" });
      return { coverageEntries: await session.close(), note: "real Todo owner and status; CLI-created gate reaches the page through the background re-read" };
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

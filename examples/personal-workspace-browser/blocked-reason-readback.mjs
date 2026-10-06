import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { promisify } from "node:util";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const REASON = "Waiting for the upstream dataset export to be published";
const CHANGED_REASON = "The replacement export requires independent validation";
const execute = promisify(execFile);

// A disposable canonical File authority, real CLI add/update and real status
// HTTP owner. No live Goal, Todo or lease is touched.
async function startBlockedAuthority() {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-blocked-reason-"));
  const child = spawn(resolveTestPython(), ["-u", "-c", `
import json, os, runpy, signal, subprocess, sys
from pathlib import Path
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import restart_effect_runtime
root, reason = Path(sys.argv[1]), sys.argv[2]
fixture = runpy.run_path('tests/control_plane/canonical_authority_fixture.py')
registry, runtime, state = fixture['promoted_create_fixture'](root)
base = [sys.executable, '-c', 'from loopx.entrypoint import main; main()', '--format', 'json', '--registry', str(registry), '--runtime-root', str(runtime)]
def cli(*args):
    result = subprocess.run(base + list(args), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)
def blocked(text, *extra):
    created = cli('todo', 'add', '--goal-id', 'goal-a', '--role', 'agent', '--text', text, '--priority', 'P2')
    assert created['ok'], created
    todo_id = created['todo']['todo_id']
    updated = cli('todo', 'update', '--goal-id', 'goal-a', '--role', 'agent', '--todo-id', todo_id, '--status', 'blocked', *extra)
    assert updated['ok'], updated
    return todo_id
try:
    with_reason = blocked('Synthetic blocked readback', '--reason', reason)
    without_reason = blocked('Synthetic unexplained blocker')
    server = ChatHTTPServer(('127.0.0.1', 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.verbose = registry, runtime, False
    server.runtime_root_override, server.selected_goal_id, server.scan_roots, server.limit = runtime, 'goal-a', [], 10
    print(json.dumps({'port': server.server_address[1], 'registry': str(registry), 'runtime': str(runtime), 'with_reason': with_reason, 'without_reason': without_reason}), flush=True)
    def terminate(*_): raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    server.serve_forever()
finally:
    restart_effect_runtime()
`, root, REASON], { cwd: repoRoot, env: { ...process.env, TMPDIR: root, TEMP: root, TMP: root, LOOPX_USAGE_PING: "0" }, stdio: ["ignore", "pipe", "pipe"] });
  let diagnostic = "";
  child.stderr.on("data", (chunk) => { diagnostic = (diagnostic + chunk).slice(-3000); });
  const lines = createInterface({ input: child.stdout });
  const close = async () => {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise((accept) => child.once("exit", accept));
      child.kill("SIGTERM");
      await exited;
    }
    await rm(root, { recursive: true, force: true });
  };
  try {
    const readback = await new Promise((accept, reject) => {
      const timeout = setTimeout(() => reject(new Error(`Blocked authority startup timed out: ${diagnostic}`)), 30_000);
      child.once("error", (error) => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error(`Blocked authority exited: ${diagnostic}`)); });
      lines.once("line", (line) => { clearTimeout(timeout); accept(JSON.parse(line)); });
    });
    assert.ok(Number.isSafeInteger(readback.port) && readback.port > 0);
    const cli = async (...args) => {
      const result = await execute(resolveTestPython(), ["-c", "from loopx.entrypoint import main; main()", "--format", "json",
        "--registry", readback.registry, "--runtime-root", readback.runtime, ...args], { cwd: repoRoot, timeout: 30_000 });
      const packet = JSON.parse(result.stdout);
      assert.equal(packet.ok, true, result.stdout);
      return packet;
    };
    return { ...readback, url: `http://127.0.0.1:${readback.port}`, cli, close };
  } catch (error) { await close(); throw error; }
}

export const blockedReasonReadbackScenario = {
  id: "blocked-reason-readback",
  async run({ browser, collectCoverage, url }) {
    const authority = await startBlockedAuthority();
    const coverageEntries = [];
    const receipts = [];
    try {
      for (const locale of ["zh-CN", "en"]) {
        for (const mobile of [false, true]) {
          await authority.cli("todo", "update", "--goal-id", "goal-a", "--role", "agent", "--todo-id", authority.with_reason,
            "--status", "blocked", "--text", "Synthetic blocked readback", "--reason", REASON);
          let writes = 0;
          let omitReason = false;
          const context = await openWorkspacePage({ newPage: (options) => browser.newPage({ ...options, locale }) }, url, {
            collectCoverage, viewport: mobile ? { width: 390, height: 844 } : { width: 1512, height: 982 }, isMobile: mobile,
            beforeGoto: async (_api, page) => {
              await page.route("**/status.json*", async (route) => {
                if (new URL(route.request().url()).pathname !== "/status.json") { await route.fallback(); return; }
                const response = await route.fetch({ url: `${authority.url}/status.json` });
                assert.equal(response.status(), 200);
                const payload = await response.json();
                // Missing optional fields are a transport counterexample,
                // never a mutation of the disposable canonical source.
                if (omitReason) {
                  const omit = value => {
                    if (!value || typeof value !== "object") return;
                    if (value.todo_id === authority.with_reason) delete value.reason;
                    Object.values(value).forEach(omit);
                  };
                  omit(payload);
                }
                await route.fulfill({ response, json: payload });
              });
              await page.route("**/api/chat/todo/detail?*", async route => {
                const response = await route.fetch({ url: `${authority.url}${new URL(route.request().url()).pathname}${new URL(route.request().url()).search}` });
                await route.fulfill({ response });
              });
              await page.route("**/api/chat/completed-todos?*", async route => {
                const response = await route.fetch({ url: `${authority.url}${new URL(route.request().url()).pathname}${new URL(route.request().url()).search}` });
                await route.fulfill({ response });
              });
              page.on("request", (request) => { if (request.method() !== "GET" && /actions|todo/.test(request.url())) writes += 1; });
            },
          });
          const { page, errors } = context;
          const suffix = `${locale}-${mobile ? "mobile" : "desktop"}`;
          try {
            const openTodo = async (text) => {
              const card = page.locator("button[aria-pressed]", { hasText: text });
              if (await page.getByRole("dialog").isVisible()) {
                await page.keyboard.press("Escape");
                await page.getByRole("dialog").waitFor({ state: "hidden" });
              }
              if (!await card.isVisible()) {
                if (mobile) await page.getByRole("button", { name: locale === "en" ? "Open Goal navigation" : "打开 Goal 导航", exact: true }).click();
                await page.locator(".personal-goal-link", { hasText: "Goal A" }).click();
                await page.getByRole("button", { name: locale === "en" ? "Tasks" : "任务", exact: true }).click();
              }
              await card.click();
              await page.getByRole("dialog").waitFor();
              await page.locator(".personal-task-request [role]").waitFor({ state: "hidden" });
              return page.getByRole("dialog");
            };
            const field = (drawer, label) => drawer.locator("dl > div", { has: page.locator("dt", { hasText: label }) }).locator("dd").innerText();
            const refresh = async () => {
              await Promise.all([
                page.waitForResponse(response => new URL(response.url()).pathname === "/status.json" && response.status() === 200),
                page.getByRole("button", { name: locale === "en" ? "Refresh status" : "刷新状态", exact: true }).click(),
              ]);
              await page.locator(".personal-refresh-control.is-done").waitFor();
            };

            let drawer = await openTodo("Synthetic blocked readback");
            await drawer.screenshot({ path: resolve(outputDir, `blocked-reason-${suffix}.png`) });
            await page.screenshot({ path: resolve(outputDir, `blocked-reason-viewport-${suffix}.png`) });
            const cause = drawer.getByRole("region", { name: locale === "en" ? "Why it is blocked" : "为何受阻" });
            assert.match(await cause.innerText(), new RegExp(REASON));
            assert.equal(await field(drawer, locale === "en" ? "Next transition" : "下一转换"),
              locale === "en" ? "Resume once the blocker is resolved" : "受阻原因解除后恢复执行");
            assert.equal(await drawer.evaluate((element) => element.scrollWidth > element.clientWidth + 1), false, "Drawer overflows horizontally");

            // Keep the same inspector open through successful real authority
            // changes. Static opening alone cannot establish current readback.
            if (!mobile) {
              await authority.cli("todo", "update", "--goal-id", "goal-a", "--role", "agent", "--todo-id", authority.with_reason,
                "--status", "blocked", "--reason", CHANGED_REASON);
              await refresh();
              await cause.getByText(CHANGED_REASON, { exact: true }).waitFor({ timeout: 8_000 });
              assert.doesNotMatch(await cause.innerText(), new RegExp(REASON));
              omitReason = true;
              await refresh();
              await cause.getByText(locale === "en" ? /No reason was recorded/ : /未记录原因/).waitFor();
              omitReason = false;
              await authority.cli("todo", "update", "--goal-id", "goal-a", "--role", "agent", "--todo-id", authority.with_reason,
                "--status", "open", "--text", "Synthetic validated readback");
              await refresh();
              await drawer.locator(".personal-task-inspector-summary h3", { hasText: "Synthetic validated readback" }).waitFor();
              await drawer.locator(".personal-task-request [role]").waitFor({ state: "hidden" });
              assert.equal(await cause.count(), 0, "Resolved work must not retain its old blocked cause");
              assert.equal(await field(drawer, locale === "en" ? "Status" : "状态"), locale === "en" ? "Ready" : "待执行");
              await drawer.screenshot({ path: resolve(outputDir, `blocked-reason-resolved-${suffix}.png`) });
            }

            drawer = await openTodo("Synthetic unexplained blocker");
            await drawer.screenshot({ path: resolve(outputDir, `blocked-reason-missing-${suffix}.png`) });
            const missing = drawer.getByRole("region", { name: locale === "en" ? "Why it is blocked" : "为何受阻" });
            assert.match(await missing.innerText(), locale === "en" ? /No reason was recorded/ : /未记录原因/);
            assert.doesNotMatch(await missing.innerText(), new RegExp(REASON));
            if (locale === "en" && mobile) {
              await page.setViewportSize({ width: 1512, height: 982 });
              await authority.cli("todo", "complete", "--goal-id", "goal-a", "--role", "agent", "--todo-id", authority.without_reason,
                "--agent-id", "agent-a", "--evidence", "Synthetic independent fixture acceptance");
              await authority.cli("todo", "archive-completed", "--goal-id", "goal-a", "--max-active-done", "0", "--execute");
              await refresh();
              await drawer.getByText("Current task state is unavailable. Refresh or open its retained history before acting.", { exact: true }).waitFor();
              assert.equal(await drawer.locator(".personal-task-inspector-actions").count(), 0, "Missing active records cannot retain old execution controls");
              assert.equal(await missing.count(), 0, "Missing active records cannot retain old blocked advice");
              await page.keyboard.press("Escape");
              await drawer.waitFor({ state: "hidden" });
              await page.locator(".personal-completed-row button", { hasText: "Synthetic unexplained blocker" }).click();
              await drawer.locator(".personal-task-completed-note").waitFor();
              await drawer.locator(".personal-task-request [role]").waitFor({ state: "hidden" });
              assert.equal(await missing.count(), 0, "Archived completed history must not display blocked advice");
              assert.equal(await field(drawer, "Status"), "Completed");
              await drawer.screenshot({ path: resolve(outputDir, "blocked-reason-archived-history.png") });
            }
            assert.equal(writes, 0, "Reading a blocked Todo emitted a write");
            assert.deepEqual(errors, []);
            receipts.push({ locale, mobile, todo_id: authority.with_reason, reason_rendered: true,
              dynamic_refresh: !mobile, missing_optional_reason_transport: !mobile,
              archived_history_readback: locale === "en" && mobile, missing_reason_todo_id: authority.without_reason, writes });
          } finally {
            await page.unrouteAll({ behavior: "wait" });
            coverageEntries.push(...await context.close());
          }
        }
      }
      await writeFile(resolve(outputDir, "blocked-reason-receipts.json"), JSON.stringify({ synthetic: true, real_cli_http_owner: true, receipts }, null, 2));
      return { coverageEntries, note: "Canonical CLI→HTTP→packaged drawer; desktop/mobile Chinese/English, same open desktop inspector follows changed cause, injected missing optional reason and real blocked→open. Real completion/archive becomes unavailable in the active view and reopens through retained history, zero UI writes." };
    } finally { await authority.close(); }
  },
};

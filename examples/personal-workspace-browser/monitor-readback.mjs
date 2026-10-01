import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// A disposable canonical File authority, real CLI create/list and real status
// HTTP owner. No live Goal, collector, order, or confirmation is touched.
async function startMonitorAuthority() {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-monitor-readback-"));
  const child = spawn(resolveTestPython(), ["-u", "-c", `
import json, runpy, signal, subprocess, sys
from pathlib import Path
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import restart_effect_runtime
root = Path(sys.argv[1])
fixture = runpy.run_path('tests/control_plane/canonical_authority_fixture.py')
registry, runtime, state = fixture['promoted_create_fixture'](root)
base = [sys.executable, '-c', 'from loopx.entrypoint import main; main()', '--format', 'json', '--registry', str(registry), '--runtime-root', str(runtime)]
def cli(*args):
    result = subprocess.run(base + list(args), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)
try:
    created = cli('todo', 'add', '--goal-id', 'goal-a', '--role', 'agent', '--text', 'Synthetic monitor readback',
        '--task-class', 'continuous_monitor', '--claimed-by', 'agent-a', '--target-key', 'synthetic-readback',
        '--cadence', '6h', '--next-due-at', '2030-10-02T18:58:16+08:00',
        '--expires-at', '2030-10-05T10:58:16Z', '--watch-only')
    assert created['ok'], created
    listed = cli('todo', 'list', '--goal-id', 'goal-a', '--role', 'agent')
    todo = next(t for t in listed['todos'] if t.get('target_key') == 'synthetic-readback')
    server = ChatHTTPServer(('127.0.0.1', 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.verbose = registry, runtime, False
    server.runtime_root_override, server.selected_goal_id, server.scan_roots, server.limit = runtime, 'goal-a', [], 10
    print(json.dumps({'port': server.server_address[1], 'pid': __import__('os').getpid(), 'todo': todo}), flush=True)
    def terminate(*_): raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    server.serve_forever()
finally:
    restart_effect_runtime()
`, root], { cwd: repoRoot, env: { ...process.env, TMPDIR: root, TEMP: root, TMP: root, LOOPX_USAGE_PING: "0" }, stdio: ["ignore", "pipe", "pipe"] });
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
      const timeout = setTimeout(() => reject(new Error(`Monitor authority startup timed out: ${diagnostic}`)), 30_000);
      child.once("error", (error) => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error(`Monitor authority exited: ${diagnostic}`)); });
      lines.once("line", (line) => { clearTimeout(timeout); accept(JSON.parse(line)); });
    });
    assert.ok(Number.isSafeInteger(readback.port) && readback.port > 0);
    return { ...readback, url: `http://127.0.0.1:${readback.port}`, close };
  } catch (error) { await close(); throw error; }
}

export const monitorReadbackScenario = {
  id: "monitor-readback",
  async run({ browser, collectCoverage, url }) {
    const authority = await startMonitorAuthority();
    const coverageEntries = [];
    const receipts = [];
    try {
      for (const locale of ["zh-CN", "en"]) {
        for (const mobile of [false, true]) {
          let partialReadback = false;
          let writes = 0;
          const context = await openWorkspacePage({ newPage: (options) => browser.newPage({ ...options, locale }) }, url, {
            collectCoverage, viewport: mobile ? { width: 390, height: 844 } : { width: 1512, height: 982 }, isMobile: mobile,
            beforeGoto: async (_api, page) => {
              await page.route("**/status.json*", async (route) => {
                if (new URL(route.request().url()).pathname !== "/status.json") { await route.fallback(); return; }
                const response = await route.fetch({ url: `${authority.url}/status.json` });
                assert.equal(response.status(), 200);
                const payload = await response.json();
                // Inject a missing-fields legacy/partial projection only in the
                // response; the canonical owner remains unchanged.
                if (partialReadback) {
                  const visit = (value) => {
                    if (!value || typeof value !== "object") return;
                    if (value.todo_id === authority.todo.todo_id) {
                      for (const field of ["claimed_by", "cadence", "next_due_at", "expires_at", "last_checked_at", "watch_only", "resume_when"]) delete value[field];
                    }
                    Object.values(value).forEach(visit);
                  };
                  visit(payload);
                }
                await route.fulfill({ response, json: payload });
              });
              page.on("request", (request) => { if (request.method() !== "GET" && /actions|monitor/.test(request.url())) writes += 1; });
            },
          });
          const { page, errors } = context;
          try {
            const openMonitor = async () => {
              if (mobile) await page.getByRole("button", { name: locale === "en" ? "Open Goal navigation" : "打开 Goal 导航", exact: true }).click();
              await page.locator(".personal-goal-link", { hasText: "Goal A" }).click();
              await page.getByRole("button", { name: locale === "en" ? "Tasks" : "任务", exact: true }).click();
              await page.getByRole("button", { name: /Synthetic monitor readback/ }).click();
              await page.getByRole("dialog").waitFor();
            };
            await openMonitor();
            const drawer = page.getByRole("dialog");
            const field = async (label) => drawer.locator("dl > div", { has: page.locator("dt", { hasText: label }) }).locator("dd").innerText();
            assert.equal(await field(locale === "en" ? "Responsible Agent" : "责任 Agent"), authority.todo.claimed_by);
            assert.match(await field(locale === "en" ? "Next check" : "下次检查"), /10:58:16.*UTC/);
            assert.match(await field(locale === "en" ? "Monitor expires" : "监控到期"), locale === "en" ? /10\/05\/2030/ : /2030\/10\/05/);
            assert.equal(await field(locale === "en" ? "Check interval" : "检查间隔"), "6h");
            assert.match(await field(locale === "en" ? "Last check" : "上次检查"), locale === "en" ? /Unknown/ : /未知/);
            assert.match(await field(locale === "en" ? "Watch only" : "仅观察"), locale === "en" ? /observation only/ : /仅观察/);
            await drawer.screenshot({ path: resolve(outputDir, `monitor-readback-${locale}-${mobile ? "mobile" : "desktop"}.png`) });
            assert.equal(await drawer.evaluate((element) => element.scrollWidth > element.clientWidth + 1), false, "Drawer overflows horizontally");
            await page.keyboard.press("Escape");
            await drawer.waitFor({ state: "hidden" });
            partialReadback = true;
            await page.reload({ waitUntil: "networkidle" });
            await openMonitor();
            const partial = await drawer.innerText();
            assert.doesNotMatch(partial, /Goal completes or owner stops it|Goal 完成或 owner 停止|Waiting for schedule|等待调度|Not run yet|尚未执行|2030/);
            assert.match(await field(locale === "en" ? "Responsible Agent" : "责任 Agent"), locale === "en" ? /Unknown/ : /未知/);
            assert.equal(writes, 0, "Reading a schedule emitted a monitor action");
            assert.deepEqual(errors, []);
            receipts.push({ locale, mobile, responsible_agent: authority.todo.claimed_by, next_due_at: authority.todo.next_due_at, expires_at: authority.todo.expires_at, writes });
          } finally { coverageEntries.push(...await context.close()); }
        }
      }
      await writeFile(resolve(outputDir, "monitor-readback-receipts.json"), JSON.stringify({ synthetic: true, real_cli_http_owner: true, receipts }, null, 2));
      return { coverageEntries, note: "Canonical CLI→HTTP→packaged drawer readback; desktop/mobile, Chinese/English, unknown partial response, zero schedule writes." };
    } finally { await authority.close(); }
  },
};

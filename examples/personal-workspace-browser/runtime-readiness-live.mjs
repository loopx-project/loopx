import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { chmod, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { waitForHttp } from "../dashboard-browser-smoke-support.mjs";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, packaged, repoRoot } from "./fixture.mjs";
import { fetchWorkspaceBackend, openWorkspacePage } from "./scenario-context.mjs";

// End to end against the real service: a disposable `loopx chat` whose Codex,
// Claude Code and Kiro executables do not exist. The packaged page reads the
// real catalog, names the real missing paths, and after the scenario installs
// a Codex executable, Check again re-reads the real probe and withdraws the
// notice. Nothing is synthetic on this path except the browser.

export const runtimeReadinessLiveScenario = {
  id: "runtime-readiness-live",
  async run({ browser, collectCoverage, url }) {
    if (!packaged) return { coverageEntries: [], note: "live runtime readiness runs against the packaged bundle only" };
    const root = await mkdtemp(join(tmpdir(), "loopx-readiness-live-"));
    const python = resolveTestPython({ repoRoot });
    const child = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/runtime-readiness-backend.py"), root], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ["ignore", "pipe", "pipe"] });
    const closed = once(child, "close");
    const lines = createInterface({ input: child.stdout });
    let diagnostics = "";
    child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-4000); });
    let session;
    try {
      const ready = await Promise.race([
        once(lines, "line").then(([line]) => JSON.parse(line)),
        closed.then(() => { throw new Error(`readiness backend exited: ${diagnostics}`); }),
      ]);
      const backend = `http://127.0.0.1:${ready.port}`;
      await waitForHttp(`${backend}/chat/`);
      session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: async (_api, page) => {
        for (const pattern of ["**/status.json*", "**/api/chat/capabilities", "**/api/chat/projects"]) {
          await page.route(pattern, async (route) => { const response = await fetchWorkspaceBackend(route, backend); await route.fulfill({ response }); });
        }
      } });
      const { page } = session;
      const notice = page.getByTestId("runtime-readiness");
      await notice.waitFor({ state: "visible", timeout: 20_000 });
      const text = await notice.innerText();
      assert.match(text, new RegExp(`Codex：未在 PATH 中找到命令「${ready.bins.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}/codex」`), `The real service names the probed path: ${text}`);
      assert.match(text, /Claude API：未设置环境变量 ANTHROPIC_API_KEY/, "The real service names the unset variable");
      await page.screenshot({ path: resolve(outputDir, "runtime-readiness-live.png"), animations: "disabled" });

      // On a phone the real probed paths must wrap inside the card: every
      // paragraph, list item and code token ends within the notice and the
      // viewport. The document's own scroll width cannot prove this.
      await page.setViewportSize({ width: 390, height: 844 });
      await notice.waitFor({ state: "visible" });
      const bounds = await notice.evaluate((node) => {
        const card = node.getBoundingClientRect();
        const children = [...node.querySelectorAll("p, li, code, h2")].map((child) => { const box = child.getBoundingClientRect(); return { tag: child.tagName, right: box.right, width: box.width }; });
        return { card: { right: card.right, width: card.width }, children, viewport: window.innerWidth };
      });
      for (const child of bounds.children) {
        assert.ok(child.right <= bounds.card.right + 1 && child.right <= bounds.viewport, `${child.tag} ends at ${child.right} beyond the card (${bounds.card.right}) or the viewport (${bounds.viewport})`);
      }
      await page.screenshot({ path: resolve(outputDir, "runtime-readiness-live-phone.png"), animations: "disabled" });
      await page.setViewportSize({ width: 1440, height: 900 });

      // The owner installs Codex; Check again re-reads the real probe.
      await writeFile(join(ready.bins, "codex"), "#!/bin/sh\nexit 0\n");
      await chmod(join(ready.bins, "codex"), 0o755);
      await notice.getByRole("button", { name: "重新检查" }).click();
      await notice.waitFor({ state: "hidden", timeout: 20_000 });
      const catalog = await (await fetch(`${backend}/api/chat/capabilities`)).json();
      const codex = catalog.adapters.find((row) => row.agent_id === "codex");
      assert.equal(codex.available, true, "The real catalog now reports Codex available");
      assert.equal(codex.unavailable_reason, undefined);
      return { coverageEntries: await session.close(), note: "real loopx chat service, live PATH probe, real re-check" };
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

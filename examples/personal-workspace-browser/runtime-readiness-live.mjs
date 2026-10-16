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
// notice. The Goal and exit-only executable are synthetic; catalog, messages,
// persistence and HTTP are the real local owners. No model is called.

export const runtimeReadinessLiveScenario = {
  id: "runtime-readiness-live",
  async run({ browser, collectCoverage, url }) {
    if (!packaged) return { coverageEntries: [], note: "live runtime readiness runs against the packaged bundle only" };
    const root = await mkdtemp(join(tmpdir(), "loopx-readiness-live-"));
    const python = resolveTestPython({ repoRoot });
    const child = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/runtime-readiness-backend.py"), root, "--with-goal"], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ["ignore", "pipe", "pipe"] });
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
        await page.route((target) => target.pathname === "/status.json" || target.pathname.startsWith("/api/chat/") || target.pathname.startsWith("/api/actions") || target.pathname.startsWith("/extension-"), async (route) => { const response = await fetchWorkspaceBackend(route, backend); await route.fulfill({ response }); });
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

      const turns = [];
      const admissions = [];
      page.on("request", (request) => {
        if (request.method() !== "POST") return;
        const path = new URL(request.url()).pathname;
        if (/\/turns$/.test(path)) turns.push(request.url());
        if (path === "/api/chat/sessions") admissions.push(request.url());
      });
      const receipts = [];
      const projectRefusals = [];
      for (const locale of ["zh-CN", "en"]) {
        const zh = locale === "zh-CN";
        await page.evaluate((value) => localStorage.setItem("loopx-pw-locale", value), locale);
        await page.reload({ waitUntil: "networkidle" });
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.locator(".personal-manager-link").click();
        await page.getByRole("navigation", { name: zh ? "管家视图" : "Manager view" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
        for (const scope of ["manager", "goal"]) {
          if (scope === "goal") {
            await page.locator(".personal-goal-link").first().click();
            await page.getByRole("navigation", { name: zh ? "Goal 视图" : "Goal view" }).getByRole("button", { name: zh ? "对话" : "Chat", exact: true }).click();
          }
          await page.getByRole("combobox", { name: zh ? "选择聊天 Runtime" : "Select chat runtime", exact: true }).click();
          await page.getByRole("option", { name: zh ? "仅查状态" : "Status only", exact: true }).click();
          const question = zh ? `当前有哪些 Goal？${scope}` : `Which Goals are present? ${scope}`;
          const saved = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/chat/projection-messages" && response.request().method() === "POST");
          await page.getByLabel(zh ? "向 LoopX 发送消息" : "Send a message to LoopX").fill(question);
          await page.getByRole("button", { name: zh ? "发送" : "Send", exact: true }).click();
          const response = await saved;
          assert.equal(response.status(), 201, "Status-only still persists the accepted question and answer");
          const receipt = await response.json();
          const stored = await (await fetch(`${backend}/api/chat/sessions/${receipt.session_id}`)).json();
          assert.equal(stored.session.adapter_kind, "status_projection");
          assert.ok(stored.messages.some((message) => message.message_id === receipt.user_message_id && message.text === question));
          const answer = stored.messages.find((message) => message.message_id === receipt.answer_message_id);
          assert.ok(answer?.text);
          await page.getByText(question, { exact: true }).waitFor({ state: "visible" });
          await page.setViewportSize({ width: 390, height: 844 });
          const line = page.getByTestId("runtime-readiness-line");
          const copy = await line.innerText();
          assert.doesNotMatch(copy, /无法发送消息|messages cannot be sent/, "Execution diagnosis must not deny the successful read-only task");
          assert.match(copy, zh ? /Agent 执行.*仅查状态/ : /Agent execution.*Status only/);
          await page.screenshot({ path: resolve(outputDir, `runtime-readiness-${scope}-${locale}-phone.png`), animations: "disabled" });
          receipts.push({ scope, locale, session_id: receipt.session_id, user_message_id: receipt.user_message_id, answer_message_id: receipt.answer_message_id, status: response.status() });
          await page.setViewportSize({ width: 1440, height: 900 });
        }
        // A project conversation has no status-only executor. Its diagnosis
        // must not promise a readonly entry that this context does not offer.
        await page.locator(".personal-manager-link").click();
        await page.getByRole("navigation", { name: zh ? "管家视图" : "Manager view" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
        const projects = await (await fetch(`${backend}/api/chat/projects`)).json();
        assert.ok(projects.projects.length > 0);
        await page.getByRole("combobox", { name: zh ? "范围" : "Scope", exact: true }).click();
        await page.getByRole("option", { name: projects.projects[0].title, exact: true }).click();
        const projectLine = page.getByTestId("runtime-readiness-line");
        await projectLine.waitFor({ state: "visible" });
        assert.match(await projectLine.innerText(), zh ? /Agent 执行尚未就绪/ : /Agent execution is not ready/);
        assert.doesNotMatch(await projectLine.innerText(), /仅查状态|Status only/);
        await page.getByRole("combobox", { name: zh ? "选择聊天 Runtime" : "Select chat runtime", exact: true }).click();
        assert.equal(await page.getByRole("option", { name: zh ? "仅查状态" : "Status only", exact: true }).count(), 0);
        await page.keyboard.press("Escape");
        const admissionsBefore = admissions.length;
        const draft = zh ? "保留这条未发送的项目请求" : "Retain this unsent project request";
        const input = page.getByLabel(zh ? "向 LoopX 发送消息" : "Send a message to LoopX");
        await input.fill(draft);
        await page.getByRole("button", { name: zh ? "发送" : "Send", exact: true }).click();
        await page.locator(".personal-action-feedback").getByText(zh ? /发送失败.*需先修复运行环境/ : /Send failed.*repair the runtime first/).waitFor();
        assert.equal(await input.inputValue(), draft, "A refused execution keeps the owner's draft");
        assert.equal(admissions.length, admissionsBefore, "An unavailable route cannot request a Session");
        projectRefusals.push({ locale, draft_retained: true, session_admission_delta: 0 });
      }
      assert.equal(turns.length, 0, "Status-only never starts a model Turn");
      await writeFile(resolve(outputDir, "runtime-readiness-status-only-receipts.json"), JSON.stringify({ real_local_service: true, receipts, project_refusals: projectRefusals, model_turns: turns.length }, null, 2));

      // Return to the same home notice; a repaired execution CLI still clears it.
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
      await page.reload({ waitUntil: "networkidle" });
      await page.locator(".personal-manager-link").click();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "总览", exact: true }).click();

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
      await session?.page.screenshot({ path: resolve(outputDir, "runtime-readiness-live-failure.png"), animations: "disabled" }).catch(() => {});
      if (session?.errors.length) process.stderr.write(`${JSON.stringify(session.errors)}\n`);
      if (diagnostics) process.stderr.write(diagnostics);
      await session?.close().catch(() => {});
      throw error;
    } finally {
      lines.close();
      if (child.exitCode === null && child.signalCode === null) { child.kill("SIGTERM"); const kill = setTimeout(() => child.kill("SIGKILL"), 5000); try { await closed; } finally { clearTimeout(kill); } }
    }
  },
};

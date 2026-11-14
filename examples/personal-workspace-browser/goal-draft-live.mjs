import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, stat} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {waitForHttp} from "../dashboard-browser-smoke-support.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

// Conversation delivery is synthetic. Discovery, preview, apply and recovery
// use the real Chat owner on disposable state. The stub Agent's failed first
// Turn must never be presented as completed work.
export const goalDraftLiveScenario = {
  id: "goal-draft-live", requiresBackendReadback: true,
  async run({browser, collectCoverage, url}) {
    const coverageEntries = [];
    for (const available of [true, false]) {
      const root = await mkdtemp(join(tmpdir(), "loopx-goal-draft-live-"));
      const child = spawn(resolveTestPython({repoRoot}), ["-u",
        resolve(repoRoot, "examples/personal-workspace-browser/fresh-install-backend.py"),
        root, ...(available ? [] : ["--no-agent"])], {cwd: repoRoot,
        env: {...process.env, PYTHONPATH: repoRoot, LOOPX_USAGE_PING: "0"}, stdio: ["ignore", "pipe", "pipe"]});
      const closed = once(child, "close"), lines = createInterface({input: child.stdout});
      let session, diagnostics = "", loseApplyResponse = available;
      const previews = [], applies = [];
      child.stderr.on("data", chunk => {diagnostics = (diagnostics + chunk).slice(-4000);});
      try {
        const ready = await Promise.race([once(lines, "line").then(([line]) => JSON.parse(line)),
          closed.then(() => {throw new Error(`fresh backend exited: ${diagnostics}`);})]);
        const backend = `http://127.0.0.1:${ready.port}`;
        await waitForHttp(`${backend}/status.json?view=workspace-directory`);
        const registry = join(ready.project, ".loopx/registry.json");
        const draft = {objective: "Prepare the release checklist", completion_criteria: "A checklist with validation steps",
          execution_boundary: "Read project files only", question: "", options: []};
        session = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (api, page) => {
          api.answerForMessage = () => ({message: "The goal is ready to review.", goal_draft: draft});
          // Keep the synthetic conversation runtime distinct from the real
          // action executor catalog (which preview validates independently).
          const paths = new Set(["/status.json", "/api/chat/projects"]);
          await page.route(target => paths.has(target.pathname) || target.pathname.startsWith("/api/actions"), async route => {
            const request = route.request(), pathname = new URL(request.url()).pathname;
            if (pathname === "/api/actions/preview") previews.push(request.postDataJSON());
            if (pathname.endsWith("/apply")) applies.push(pathname);
            try {
              const response = await fetchWorkspaceBackend(route, backend);
              if (pathname.endsWith("/apply") && loseApplyResponse) {loseApplyResponse = false; await route.abort("failed");}
              else await route.fulfill({response});
            } catch {await route.abort().catch(() => {});}
          });
        }});
        const {page} = session;
        await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: "对话", exact: true}).click();
        await page.locator(".personal-channel-composer textarea").fill(draft.objective);
        await page.getByRole("button", {name: "发送", exact: true}).click();
        const card = page.getByRole("region", {name: "目标草稿"}).last();
        await card.waitFor();
        if (!available) {
          await card.getByRole("alert").filter({hasText: /Agent 当前不可用/}).waitFor();
          await assert.rejects(stat(registry), {code: "ENOENT"});
          assert.equal(applies.length, 0, "An unavailable Agent never receives apply");
          await card.getByRole("button", {name: "查看并处理", exact: true}).click();
          await page.locator(".personal-context-drawer").waitFor({state: "visible"});
          await assert.rejects(stat(registry), {code: "ENOENT"});
        } else {
          const create = card.getByRole("button", {name: "创建并开始", exact: true});
          await create.waitFor();
          assert.equal(await page.locator('.personal-proposal-row[data-action-kind="goal.create"]').count(), 0);
          await card.getByText(/^默认：project/).waitFor();
          assert.ok(!(await card.innerText()).includes(root), "The resolved scope is public safe");
          await assert.rejects(stat(registry), {code: "ENOENT"});
          assert.equal(applies.length, 0, "Preparation never applies a Goal");
          await page.screenshot({path: resolve(outputDir, "goal-draft-live-ready.png"), animations: "disabled"});
          await page.setViewportSize({width: 390, height: 844});
          await card.scrollIntoViewIfNeeded();
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
          await page.screenshot({path: resolve(outputDir, "goal-draft-live-ready-narrow.png"), animations: "disabled"});
          await create.evaluate(button => {button.click(); button.click();});
          await card.getByRole("button", {name: "重试创建", exact: true}).waitFor({timeout: 60_000});
          const first = JSON.parse(await readFile(registry, "utf8"));
          assert.equal(first.goals.length, 1);
          const proposalId = applies[0].split("/").at(-2);
          const original = (await (await fetch(`${backend}/api/actions/${proposalId}`)).json()).proposal;
          assert.equal(original.normalized_parameters.goal_id, first.goals[0].id);
          assert.equal(original.normalized_parameters.heartbeat.enabled, false);
          assert.equal(original.normalized_parameters.permission, "read_only");
          assert.notEqual(original.status, "applied", "A failed first Turn is not completed creation");
          assert.equal(await card.getByRole("button", {name: "已创建", exact: true}).count(), 0);
          assert.equal(applies.length, 1, "Repeated clicks apply once even when the response is lost");
          await page.reload({waitUntil: "networkidle"});
          await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: "对话", exact: true}).click();
          await card.getByRole("button", {name: "重试创建", exact: true}).click();
          await card.getByRole("button", {name: "重试创建", exact: true}).waitFor({timeout: 60_000});
          assert.deepEqual(applies, [applies[0], applies[0]], "Recovery retries the original operation");
          assert.equal(previews.length, 1, "Reload reads the original preview");
          assert.deepEqual(JSON.parse(await readFile(registry, "utf8")).goals.map(goal => goal.id), [first.goals[0].id]);
          await page.screenshot({path: resolve(outputDir, "goal-draft-live-recovery.png"), animations: "disabled"});
        }
        coverageEntries.push(...await session.close());
      } catch (error) {
        await session?.page.screenshot({path: resolve(outputDir, "goal-draft-live-failed.png")}).catch(() => {});
        const body = await session?.page.locator("body").innerText().catch(() => "");
        await session?.close().catch(() => {});
        throw new Error(`${error.message}; body=${body?.slice(-6000)}; backend=${diagnostics}`);
      } finally {
        lines.close();
        if (child.exitCode === null && child.signalCode === null) {
          child.kill("SIGTERM");
          const kill = setTimeout(() => child.kill("SIGKILL"), 5000);
          try {await closed;} finally {clearTimeout(kill);}
        }
      }
    }
    return {coverageEntries, backendReadbackVerified: true, note: "Synthetic conversation, real discovery/actions: prepare without Goal writes, one confirmation, lost-response readback, original retry and one registry Goal; unavailable Agent writes nothing; stub first-Turn failure stays explicit."};
  },
};

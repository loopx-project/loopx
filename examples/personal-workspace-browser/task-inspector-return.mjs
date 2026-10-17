import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir, repoRoot } from "./fixture.mjs";
import {spawn, execFile} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {promisify} from "node:util";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import { openWorkspacePage, fetchWorkspaceBackend } from "./scenario-context.mjs";

export const taskInspectorReturnScenario = {
  id: "task-inspector-return",
  async run({ browser, collectCoverage, url }) {
    const coverageEntries = [];
    const tail = " FINAL_ACCEPTANCE: every requirement is visible; stale or unavailable reads remain explicit.";
    const prefix = "[P0] Full queue follow-up. ";
    const request = prefix + "A".repeat(988 - prefix.length - tail.length) + tail;
    for (const width of [1512, 390]) {
      const context = await openWorkspacePage(browser, url, {
        collectCoverage, viewport: { width, height: 844 },
        beforeGoto(api) {
          api.todoRequestTexts.set(JSON.stringify(["progress-projection", "todo-progress-full"]), request);
        },
      });
      const { page, api } = context;
      try {
        await page.emulateMedia({ reducedMotion: "reduce" });
        const menu = page.locator(".personal-mobile-menu");
        if (await menu.isVisible()) await menu.click();
        await page.locator(".personal-goal-link", { hasText: "Progress Projection" }).click();
        await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Tasks|任务)$/ }).click();
        const card = page.locator(".personal-task-card", { hasText: "Current Todo" });
        const opener = card.locator(":scope > button");
        await opener.waitFor();
        assert.match(await opener.innerText(), /未分配/, "An unclaimed task must never inherit a Goal or execution owner");
        const drawer = page.getByRole("dialog", { name: "Todo 详情" });

        // WebKit on macOS can leave focus on the preceding control after a
        // pointer click. Preserve that condition independently of the browser
        // engine, so Chromium CI also exercises the installed App's failure.
        await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Tasks|任务)$/ }).focus();
        await opener.evaluate(element => element.addEventListener("mousedown", event => event.preventDefault()));
        await opener.click();
        await drawer.waitFor();
        await drawer.locator("dl > div", { has: page.getByText("Owner", { exact: true }) }).getByText("未分配", { exact: true }).waitFor();
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Current Todo"));
        assert.equal(await opener.evaluate(element => document.activeElement === element), true, "Escape must return to the pointer-opened Task");
        await page.keyboard.press("Enter");
        await drawer.waitFor();
        await drawer.getByRole("button", { name: /关闭详情/ }).click();
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Current Todo"));

        const more = card.locator(".personal-task-card-actions > button").last();
        await more.evaluate(element => element.addEventListener("mousedown", event => event.preventDefault()));
        await more.click();
        await drawer.waitFor();
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.classList.contains("personal-task-card-actions") || document.activeElement?.parentElement?.classList.contains("personal-task-card-actions"));
        assert.equal(await more.evaluate(element => document.activeElement === element), true, "The actions opener retains its own keyboard context");

        const longOpener = page.locator(".personal-task-card", { hasText: "Idless long Todo" }).locator(":scope > button");
        assert.ok((await longOpener.innerText()).length < 150, "The card keeps its bounded preview");
        await longOpener.click();
        await drawer.waitFor();
        assert.match(await drawer.locator(".personal-task-request .personal-md").innerText(), /keeps one card$/, "Opening a Task must retain its original requirements beyond the card preview");
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Idless long Todo"));
        assert.equal(await longOpener.evaluate(element => document.activeElement === element), true);

        const fullOpener = page.locator(".personal-task-card", { hasText: "Full queue follow-up" }).locator(":scope > button");
        const body = drawer.locator(".personal-task-request .personal-md");
        const waitForFull = () => page.waitForFunction(text => document.querySelector(".personal-task-request .personal-md")?.textContent === text, request);
        await fullOpener.click();
        await waitForFull();
        assert.equal(await body.innerText(), request, "A cold read restores every one of the 988 source characters beyond the 500-character status projection");
        await page.screenshot({ path: resolve(outputDir, `task-request-full-${width}.png`), animations: "disabled" });
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });

        // A failed or mismatched read must discard an earlier successful body,
        // preserve the received summary, and offer a real retry in the drawer.
        let nextRead = "failed";
        let releaseLate;
        let lateFinished;
        await page.route("**/api/chat/todo/detail?*", async route => {
          if (new URL(route.request().url()).searchParams.get("todo_id") !== "todo-progress-full" || !nextRead) return route.fallback();
          const outcome = nextRead;
          nextRead = null;
          if (outcome === "late") {
            await new Promise(resolveWait => { releaseLate = resolveWait; });
            await route.fulfill({ json: { ok: true, goal_id: "progress-projection", todo_id: "todo-progress-full", text: request, status: "open", archive_state: "active", updated_at: null } });
            lateFinished();
            return;
          }
          await route.fulfill({ json: outcome === "failed" ? { ok: false, error: "Source unavailable" } : {
            ok: true, goal_id: "another-goal", todo_id: "todo-progress-full", text: request, status: "open", archive_state: "active", updated_at: null,
          } });
        });
        for (const outcome of ["failed", "mismatched"]) {
          nextRead = outcome;
          await fullOpener.click();
          const retry = drawer.getByRole("button", { name: "重试读取完整要求" });
          await retry.waitFor();
          assert.equal((await body.innerText()).length, 500);
          assert.ok(!(await body.innerText()).includes("FINAL_ACCEPTANCE"), "A previous successful body is never reused after a failed exact read");
          await retry.click();
          await waitForFull();
          await page.keyboard.press("Escape");
          await drawer.waitFor({ state: "hidden" });
        }
        nextRead = "late";
        const finished = new Promise(resolveWait => { lateFinished = resolveWait; });
        await fullOpener.click();
        await drawer.getByRole("status").waitFor();
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await opener.click();
        await page.waitForFunction(() => document.querySelector(".personal-task-request .personal-md")?.textContent === "Current Todo");
        releaseLate();
        await finished;
        assert.equal(await body.innerText(), "Current Todo", "A late response from the closed Task cannot overwrite the next selection");
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        assert.ok(api.todoRequestReads.every(read => read.todoId), "A display-only legacy identity must never be sent as an authority id");
        assert.equal(api.turnRequests.length, 0, "Inspecting Tasks never starts model work");
        assert.equal(api.actionApplies.length, 0, "Focus recovery never changes Todo authority");
        assert.equal(context.errors.length, 0, context.errors.join(" | "));
        await page.screenshot({ path: resolve(outputDir, `task-inspector-return-${width}.png`), animations: "disabled" });
      } finally {
        coverageEntries.push(...await context.close());
      }
    }
    // Shipped editing reaches the same real source and typed write owner.
    const root = await mkdtemp(resolve(tmpdir(), "loopx-task-editor-"));
    const python = await resolveTestPython();
    const backend = spawn(python, ["-u", resolve(repoRoot, "examples/personal-workspace-browser/goal-workspace-todo-backend.py"), root, "requirements"], {cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"]});
    let backendError = "";
    backend.stderr.on("data", bytes => { backendError += bytes; });
    let editing;
    try {
      const info = await new Promise((resolveInfo, reject) => {
        let data = "";
        backend.stdout.on("data", bytes => {
          data += bytes;
          if (data.includes("\n")) { try {resolveInfo(JSON.parse(data.split("\n")[0]));} catch(error) {reject(error);} }
        });
        backend.once("exit", code => reject(new Error(`Task backend exited ${code}: ${backendError}`)));
        backend.once("error", reject);
      });
      const endpoint = `http://127.0.0.1:${info.port}`;
      let applyCount = 0;
      editing = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (api, page) => {
        api.transformStatus = payload => {
          const queue = payload.attention_queue.items.find(item => item.goal_id === "progress-projection");
          for (const source of [queue?.agent_todos, queue?.project_asset?.agent_todos]) {
            for (const item of source?.items ?? []) if (item.todo_id === "todo-progress-full") { item.claimed_by = "agent-a"; item.todo_id = "todo_requirements_source"; }
          }
        };
        await page.route("**/api/chat/todo/detail?*", async route => route.fulfill({response: await fetchWorkspaceBackend(route, endpoint)}));
        await page.route("**/api/actions**", async route => {
          if (new URL(route.request().url()).pathname.endsWith("/apply")) applyCount++;
          await route.fulfill({response: await fetchWorkspaceBackend(route, endpoint)});
        });
      }});
      const {page} = editing;
      await page.emulateMedia({reducedMotion: "reduce"});
      await page.locator(".personal-goal-link", {hasText: "Progress Projection"}).click();
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: /^(Tasks|任务)$/}).click();
      await page.locator(".personal-task-card", {hasText: "Full queue follow-up"}).locator(":scope > button").click();
      const drawer = page.getByRole("dialog", {name: "Todo 详情"});
      const read = async () => (await (await page.request.get(`${endpoint}/api/chat/todo/detail?goal_id=progress-projection&todo_id=todo_requirements_source`)).json());
      const initial = await read();
      assert.ok(initial.text?.indexOf("FINAL_ACCEPTANCE") > 500 && initial.review_basis, JSON.stringify(initial));
      await drawer.getByRole("button", {name: "修改要求", exact: true}).click();
      const field = drawer.getByRole("textbox", {name: "完整任务要求"});
      assert.equal(await field.inputValue(), initial.text, "Editing must start from the exact source, never the 500-character list");
      await drawer.getByRole("button", {name:"标记完成",exact:true}).waitFor({state:"hidden"});
      const next = initial.text + " Keep the additional delivery condition.";
      await field.fill(next);
      await page.screenshot({path: resolve(outputDir, "task-requirements-edit-desktop.png"), animations: "disabled"});
      await field.press("Tab");
      await page.keyboard.press("Enter");
      await drawer.getByRole("status").filter({hasText: "要求已保存并读回"}).waitFor();
      await drawer.getByRole("button", {name:"标记完成",exact:true}).waitFor({state:"visible"});
      assert.equal(applyCount, 1, "One save decision invokes the existing native action once");
      assert.equal((await read()).text, next);
      assert.equal(await page.locator('.personal-context-drawer[data-context-kind="proposal"]').count(), 0, "An ordinary edit must not require another approval form");
      await page.setViewportSize({width:390,height:844});
      await drawer.getByRole("button", {name: "修改要求", exact: true}).click();
      assert.equal(await field.inputValue(), next);
      await field.fill(next + " Preserve this draft after a concurrent update.");
      // Same real CLI and canonical writer, an independent intervening write.
      const changed = await promisify(execFile)(python, ["-m", "loopx.cli", "--registry", info.registry,
        "--format", "json", "todo", "update", "--goal-id", "progress-projection", "--todo-id", "todo_requirements_source",
        "--agent-id", "agent-a", "--note", "Concurrent progress"], {cwd: repoRoot});
      assert.equal(JSON.parse(changed.stdout).ok, true);
      await drawer.getByRole("button", {name:"保存要求",exact:true}).click();
      await drawer.getByRole("alert").filter({hasText:"Task source changed"}).waitFor();
      await page.screenshot({path: resolve(outputDir, "task-requirements-edit-mobile-conflict.png"), animations: "disabled"});
      assert.equal(applyCount, 1, "A stale source must fail before apply");
      assert.equal((await read()).text, next);
      assert.match(await field.inputValue(), /Preserve this draft/);
      await drawer.getByRole("button", {name:"读取当前要求并保留草稿",exact:true}).click();
      await drawer.getByRole("alert").waitFor({state:"hidden"});
      await drawer.getByRole("button", {name:"保存要求",exact:true}).click();
      await drawer.getByRole("status").filter({hasText: "要求已保存并读回"}).waitFor();
      assert.equal(applyCount, 2);
      assert.equal((await read()).text, next + " Preserve this draft after a concurrent update.");
      const afterConflict = (await read()).text;
      await drawer.getByRole("button", {name:"修改要求",exact:true}).click();
      await field.fill(afterConflict + " Recover the original committed response.");
      let loseResponse = true;
      await page.route("**/api/actions/*/apply", async route => {
        if (!loseResponse) return route.fallback();
        loseResponse = false;
        applyCount++;
        const committed = await fetchWorkspaceBackend(route, endpoint);
        assert.equal(committed.ok(), true);
        await route.abort("failed");
      });
      await drawer.getByRole("button", {name:"保存要求",exact:true}).click();
      await drawer.getByRole("status").filter({hasText:"要求已保存并读回"}).waitFor();
      assert.equal(applyCount, 3, "A lost successful reply is reconciled from the original journal without a second apply");
      assert.equal((await read()).text, afterConflict + " Recover the original committed response.");

      // If both the apply reply and journal read are lost, retain the original
      // proposal. Retrying during the outage must never create/apply anew.
      const beforeOutage = (await read()).text;
      await drawer.getByRole("button", {name:"修改要求",exact:true}).click();
      const outageDraft = beforeOutage + " Retain the operation throughout an outage.";
      await field.fill(outageDraft);
      loseResponse = true;
      let journalUnavailable = true;
      await page.route("**/api/actions**", async route => {
        if (journalUnavailable && route.request().method() === "GET") return route.abort("failed");
        return route.fallback();
      });
      await drawer.getByRole("button", {name:"保存要求",exact:true}).click();
      await drawer.getByRole("alert").waitFor();
      assert.equal(applyCount, 4);
      assert.equal((await read()).text, outageDraft);
      await drawer.getByRole("button", {name:"重试读回",exact:true}).click();
      await drawer.getByRole("button", {name:"重试读回",exact:true}).waitFor({state:"visible"});
      assert.equal(await field.inputValue(), outageDraft);
      assert.equal(applyCount, 4, "An unavailable original journal must not trigger another apply");
      journalUnavailable = false;
      await drawer.getByRole("button", {name:"重试读回",exact:true}).click();
      await drawer.getByRole("status").filter({hasText:"要求已保存并读回"}).waitFor();
      assert.equal(applyCount, 4, "Reconnect reads the original committed receipt without a new operation");

      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      assert.equal(editing.api.turnRequests.length, 0);
      assert.ok(editing.errors.every(error => /status of 400|net::ERR_FAILED/.test(error)), editing.errors.join(" | "));
      await page.screenshot({path: resolve(outputDir, "task-requirements-edit-mobile.png"), animations: "disabled"});
    } finally {
      if (editing) coverageEntries.push(...await editing.close());
      const exited = new Promise(resolveExit => backend.once("exit", resolveExit));
      backend.kill("SIGTERM");
      await exited;
      await rm(root, {recursive:true,force:true});
    }
    return { coverageEntries, note: "The 988-character source survives a bounded 500-character status; failed/mismatched reads, retry and late selection responses are explicit on packaged desktop/390px. Pointer/Escape/Enter/close/More retain the opener with reduced motion. Editing uses a disposable real File authority and Chat HTTP owner: one save, retained concurrent-conflict draft and lost successful response recovery without another apply. Workspace navigation/status is synthetic. File/SQLite hard-lease semantics are qualified separately by HTTP+CLI tests; no active Goal or model execution." };
  },
};

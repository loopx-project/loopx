import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

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
        assert.match(await drawer.locator(".personal-task-inspector-summary h3").innerText(), /keeps one card$/, "Opening a Task must retain its original requirements beyond the card preview");
        await page.keyboard.press("Escape");
        await drawer.waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card")?.textContent.includes("Idless long Todo"));
        assert.equal(await longOpener.evaluate(element => document.activeElement === element), true);

        const fullOpener = page.locator(".personal-task-card", { hasText: "Full queue follow-up" }).locator(":scope > button");
        const body = drawer.locator(".personal-task-inspector-summary h3");
        const waitForFull = () => page.waitForFunction(text => document.querySelector(".personal-task-inspector-summary h3")?.textContent === text, request);
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
        await page.waitForFunction(() => document.querySelector(".personal-task-inspector-summary h3")?.textContent === "Current Todo");
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
    return { coverageEntries, note: "The 988-character source survives a bounded 500-character status; failed/mismatched reads, retry and late selection responses are explicit on packaged desktop/390px. Pointer/Escape/Enter/close/More retain the opener with reduced motion; no authority or model write. Backend authority is qualified separately by the real File/SQLite HTTP+CLI tests." };
  },
};

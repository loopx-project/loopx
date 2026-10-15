import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// RFC goal-workspace-surface-v0 §5.7 / V5: a returning owner sees what changed
// since their last acknowledged look at a Goal, from a browser-local marker
// (typed counts and ids, no cost, no service write). A first visit seeds the
// marker silently; a no-change return shows nothing; known work and decision
// changes appear and can be dismissed; leaving after the line was shown
// advances the marker so the next return starts clean.

function firstGoal(fixture) { return fixture.attention_queue.items[0]; }

function addGate(fixture, todoId, text) {
  const first = firstGoal(fixture);
  first.waiting_on = "user_or_controller";
  first.user_todos ??= { items: [], open_count: 0, source_section: "User Todo", total_count: 0 };
  first.user_todos.items.push({ done: false, status: "open", goal_id: first.goal_id, index: first.user_todos.items.length, role: "user", task_class: "user_gate", todo_id: todoId, text, updated_at: "2026-10-15T08:00:00Z" });
  first.user_todos.open_count = first.user_todos.items.filter((todo) => !todo.done).length;
  first.user_todos.total_count = first.user_todos.items.length;
}

function addDone(fixture, todoId, text) {
  const first = firstGoal(fixture);
  const todos = first.project_asset?.agent_todos ?? first.agent_todos;
  todos.items.push({ done: true, status: "done", role: "agent", task_class: "advancement_task", goal_id: first.goal_id, todo_id: todoId, text, title: text, completed_at: "2026-10-15T09:00:00Z" });
  if (typeof todos.advancement_done_count === "number") todos.advancement_done_count += 1;
  if (typeof todos.done === "number") todos.done += 1;
}

export const sinceYouLookedScenario = {
  id: "since-you-looked",
  async run({ browser, collectCoverage, url }) {
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 } });
    const { api, page } = session;
    const writes = [];
    page.on("request", (request) => { if (request.method() !== "GET" && /\/api\//.test(request.url())) writes.push(`${request.method()} ${new URL(request.url()).pathname}`); });
    const openGoal = async () => {
      await page.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
      await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
    };
    const home = () => page.getByRole("button", { name: /^LoopX 管家/ }).first().click();
    const refresh = () => page.getByRole("button", { name: "刷新状态", exact: true }).click();
    const line = page.getByTestId("goal-since-you-looked");
    try {
      await openGoal();
      const goalId = await page.evaluate(() => Object.keys(localStorage).filter((key) => key.startsWith("loopx-pw-seen:/status.json:")).map((key) => key.split(":").pop())[0] ?? null);
      assert.ok(goalId, "The first visit seeds a marker for this source and Goal");
      assert.equal(await line.count(), 0, "A first visit has nothing to compare");
      const marker = await page.evaluate((id) => JSON.parse(localStorage.getItem(`loopx-pw-seen:/status.json:${id}`)), goalId);
      assert.equal(marker.schema, "goal_seen_marker_v1");
      assert.deepEqual(Object.keys(marker).sort(), ["counts", "decisionIds", "doneTodoIds", "observedAt", "resultIds", "schema"], "typed counts and ids only");

      // A return with nothing new stays quiet.
      await home(); await refresh(); await openGoal();
      await page.waitForTimeout(500);
      assert.equal(await line.count(), 0, "No change stays absent");

      // Work completed and a decision raised while away appear, and can be dismissed.
      await home();
      api.transformStatus = (fixture) => { addDone(fixture, "seen-done-1", "Reconcile the March export"); addGate(fixture, "seen-gate-1", "Approve the restated FY2023 operating cash flow"); };
      await refresh();
      await openGoal();
      await line.waitFor({ state: "visible", timeout: 15_000 });
      const text = await line.innerText();
      assert.match(text, /自你上次查看（.+）：/);
      assert.match(text, /新完成 1 项/, `newly done counted once: ${text}`);
      assert.match(text, /新增 1 项等你决定/, `new decision counted once: ${text}`);
      assert.doesNotMatch(text, /成本|\$|¥/, "No cost in the delta");
      await page.screenshot({ path: resolve(outputDir, "since-you-looked-line.png"), animations: "disabled" });
      await line.getByRole("button", { name: "标记已读" }).click();
      await line.waitFor({ state: "hidden", timeout: 5_000 });
      const after = await page.evaluate((id) => JSON.parse(localStorage.getItem(`loopx-pw-seen:/status.json:${id}`)), goalId);
      assert.ok(after.doneTodoIds.includes("seen-done-1") && after.decisionIds.includes("seen-gate-1"), "Mark seen advances the marker to the current observation");

      // Leaving after the line was shown also advances it; the next return starts clean.
      await home();
      api.transformStatus = (fixture) => { addDone(fixture, "seen-done-1", "Reconcile the March export"); addGate(fixture, "seen-gate-1", "Approve the restated FY2023 operating cash flow"); addGate(fixture, "seen-gate-2", "Choose the segment disclosure source"); };
      await refresh();
      await openGoal();
      await line.waitFor({ state: "visible", timeout: 15_000 });
      assert.match(await line.innerText(), /新增 1 项等你决定/);
      await home();
      await openGoal();
      await page.waitForTimeout(500);
      assert.equal(await line.count(), 0, "Leaving the Goal after the line was shown acknowledged it");

      // An explicit Mark seen during a visit is never undone by leaving: the
      // owner returns to two new decisions, refreshes to a third while looking,
      // marks seen, leaves, and comes back to silence with all three retained.
      await home();
      const threeGates = (fixture) => { addDone(fixture, "seen-done-1", "Reconcile the March export"); addGate(fixture, "seen-gate-1", "Approve the restated FY2023 operating cash flow"); addGate(fixture, "seen-gate-2", "Approve the vendor list"); addGate(fixture, "seen-gate-3", "Approve the Q4 calendar"); };
      api.transformStatus = threeGates;
      await refresh();
      await openGoal();
      await line.waitFor({ state: "visible", timeout: 15_000 });
      api.transformStatus = (fixture) => { threeGates(fixture); addGate(fixture, "seen-gate-4", "Approve the audit scope"); };
      await refresh();
      await page.waitForTimeout(500);
      await line.getByRole("button", { name: "标记已读" }).click();
      await line.waitFor({ state: "hidden", timeout: 5_000 });
      const acknowledged = await page.evaluate((id) => JSON.parse(localStorage.getItem(`loopx-pw-seen:/status.json:${id}`)), goalId);
      assert.ok(acknowledged.decisionIds.includes("seen-gate-4"), "Mark seen records the refreshed look");
      await home();
      const afterLeave = await page.evaluate((id) => JSON.parse(localStorage.getItem(`loopx-pw-seen:/status.json:${id}`)), goalId);
      assert.ok(afterLeave.decisionIds.includes("seen-gate-4"), "Leaving does not move the marker back behind the acknowledgment");
      assert.ok(afterLeave.observedAt >= acknowledged.observedAt);
      await openGoal();
      await page.waitForTimeout(500);
      assert.equal(await line.count(), 0, "Nothing is new after the acknowledged look");

      // Reload keeps the marker; nothing was written to the service.
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible", timeout: 15_000 });
      await openGoal();
      await page.waitForTimeout(500);
      assert.equal(await line.count(), 0, "The marker survives a reload");
      assert.deepEqual(writes.filter((row) => !/\/api\/chat\/(sessions|capabilities|agent-model-options|projection-messages|lark|goal-results)/.test(row)), [], `no service writes for the marker: ${writes.join(", ")}`);

      // Past the marker's id cap an unchanged set stays silent: 201 decisions
      // and 201 completed rows with no done total on the first look, the same
      // on the return, no "new" decision and no "newly done".
      const cappedHistory = (fixture) => {
        for (let i = 0; i <= 200; i += 1) { addGate(fixture, `seen-cap-${i}`, `Approve item ${i}`); addDone(fixture, `seen-done-cap-${i}`, `Completed item ${i}`); }
        const first = firstGoal(fixture);
        for (const group of [first.agent_todos, first.project_asset?.agent_todos]) {
          if (!group) continue;
          for (const key of ["advancement_done_count", "done_count", "done"]) delete group[key];
        }
      };
      const capped = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 },
        beforeGoto: (cappedApi) => { cappedApi.transformStatus = cappedHistory; } });
      try {
        const cappedPage = capped.page;
        const cappedLine = cappedPage.getByTestId("goal-since-you-looked");
        const openCapped = async () => {
          await cappedPage.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
          await cappedPage.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
          await cappedPage.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
        };
        await openCapped();
        await cappedPage.waitForTimeout(500);
        const cappedMarker = await cappedPage.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith("loopx-pw-seen:")).map(([, value]) => JSON.parse(value))[0]);
        assert.ok(cappedMarker && cappedMarker.decisionIds.length === 200 && cappedMarker.counts.decisions >= 201, `the marker keeps 200 ids and the owner's count: ${JSON.stringify(cappedMarker?.counts)}`);
        assert.ok(cappedMarker.doneTodoIds.length === 200 && cappedMarker.counts.done === null, `the done history is capped with no total: ${JSON.stringify(cappedMarker.counts)}`);
        await cappedPage.getByRole("button", { name: /^LoopX 管家/ }).first().click();
        await openCapped();
        await cappedPage.waitForTimeout(800);
        assert.equal(await cappedLine.count(), 0, "201 unchanged decisions and 201 unchanged completions with no total report nothing");
        await capped.close();
      } catch (error) {
        await capped.close().catch(() => {});
        throw error;
      }

      // A browser that denies storage (the property getter itself throws) keeps
      // the Goal readable; only the local look memory is lost.
      await home();
      await page.evaluate(() => { Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new DOMException("Storage is disabled", "SecurityError"); } }); });
      await openGoal();
      await page.waitForTimeout(500);
      assert.equal(await line.count(), 0, "Without storage there is no marker to compare");
      assert.doesNotMatch(await page.locator("body").innerText(), /页面暂时无法显示|Page unavailable/, "Denied storage never takes the Goal down");
      await line.waitFor({ state: "hidden" });
      await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 5_000 });
      return { coverageEntries: await session.close() };
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }
  },
};

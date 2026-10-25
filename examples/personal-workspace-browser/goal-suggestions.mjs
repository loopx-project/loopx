import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// The Goal composer's suggestions are derived from the Goal's own projection.
// The fixture's first Goal carries blocked advancement tasks and, once a gate
// is added, a decision waiting on the owner; each suggestion must cite those
// facts and either act in place or send a question that names them. A stopped
// Goal gets no derived suggestion, and the shipped steward prompt row stays.

const gateText = "Approve the restated FY2023 operating cash flow";

function addGate(fixture) {
  const first = fixture.attention_queue.items[0];
  first.waiting_on = "user_or_controller";
  first.user_todos ??= { items: [], open_count: 0, source_section: "User Todo", total_count: 0 };
  first.user_todos.items.push({ done: false, status: "open", goal_id: first.goal_id, index: first.user_todos.items.length, role: "user", task_class: "user_gate", todo_id: "gate-restatement", text: gateText, updated_at: "2026-10-15T08:00:00Z" });
  first.user_todos.open_count = first.user_todos.items.filter((todo) => !todo.done).length;
  first.user_todos.total_count = first.user_todos.items.length;
}

// An optional user action (no gate) is listed but is not a decision, and a
// partial projection (five open by the owner's count, none listed) is never
// "all done": both are legal states the shared overview already shows truthfully.
function optionalAndPartial(fixture) {
  const first = fixture.attention_queue.items[0];
  // Nothing waits on the owner: the Goal waits on its Agent, and the only user item is optional.
  first.waiting_on = "codex";
  first.user_todos = { items: [{ done: false, status: "open", goal_id: first.goal_id, index: 0, role: "user", task_class: "user_action", todo_id: "optional-rename", text: "Consider renaming the export folder", updated_at: "2026-10-15T08:00:00Z" }], open_count: 1, source_section: "User Todo", total_count: 1 };
  if (first.project_asset?.user_todos) first.project_asset.user_todos = { ...first.project_asset.user_todos, items: first.user_todos.items, open: 1, done: 0, total: 1 };
  // The global index lists the same Agent items; drop them so nothing is listed for this Goal.
  if (fixture.todo_index?.items) {
    fixture.todo_index.items = fixture.todo_index.items.filter((item) => !(item.goal_id === first.goal_id && item.role === "agent"));
    fixture.todo_index.total_count = fixture.todo_index.items.length;
  }
  // Agent rows contribute their current Todo to the Goal's list; none is current here.
  for (const agent of fixture.agent_management_projection?.agents ?? []) {
    if (agent.current_todo?.goal_id === first.goal_id) agent.current_todo = null;
  }
  // Every queue item of this Goal carries the same emptied listing and owner counts.
  const groups = fixture.attention_queue.items.filter((item) => item.goal_id === first.goal_id).flatMap((item) => [item.agent_todos, item.project_asset?.agent_todos]);
  for (const group of groups) {
    if (!group) continue;
    // No listed items at all (every list the projection carries), while the owner still counts five open.
    for (const [key, value] of Object.entries(group)) if (Array.isArray(value)) group[key] = [];
    if ("open_count" in group) group.open_count = 5;
    if ("open" in group) group.open = 5;
    if ("done_count" in group) group.done_count = 3;
    if ("done" in group) group.done = 3;
    if ("total_count" in group) group.total_count = 8;
    if ("total" in group) group.total = 8;
  }
}

export const goalSuggestionsScenario = {
  id: "goal-suggestions",
  async run({ browser, collectCoverage, url }) {
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 },
      beforeGoto: (api) => { api.transformStatus = addGate; } });
    const { api, page } = session;
    try {
      await page.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
      await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
      const tools = page.locator(".personal-composer-tools");
      const summary = tools.locator("> summary");
      await summary.waitFor({ state: "visible", timeout: 15_000 });
      await page.screenshot({ path: resolve(outputDir, "goal-suggestions-collapsed.png"), animations: "disabled" });
      assert.equal(await summary.getAttribute("aria-label"), "添加附件与快捷操作", "One compact entry contains the Goal actions");
      await summary.click();
      const row = page.getByTestId("goal-suggestions");
      await row.waitFor({ state: "visible" });
      const labels = (await row.locator("button").allInnerTexts()).map((label) => label.trim());
      assert.ok(labels.length >= 2 && labels.length <= 3, `two or three suggestions, got ${labels.join(" / ")}`);
      assert.match(labels[0], /^先处理 \d+ 项等你决定的事项$/);
      assert.match(labels[1], /^解释 \d+ 项受阻任务$/);
      const stewardRow = page.locator(".personal-quick-prompts").first();
      assert.match(await stewardRow.innerText(), /询问下一步[\s\S]*看阻塞[\s\S]*查证据/, "The shipped steward prompt row stays below");
      await page.screenshot({ path: resolve(outputDir, "goal-suggestions-row.png"), animations: "disabled" });

      // Decisions open the owner rail as a modal; return through its close control.
      await row.locator('button[data-suggestion="open_decisions"]').click();
      await page.getByTestId("goal-vitals").waitFor({ state: "visible" });
      const overview = page.locator(".goal-overview, [data-testid=\"goal-overview\"]").first();
      await overview.waitFor({ state: "visible", timeout: 10_000 });
      const decisions = page.getByRole("dialog", { name: "全部工作", exact: true });
      await decisions.waitFor({ state: "visible" });
      assert.match(await decisions.innerText(), new RegExp(gateText), "The decision rail shows the waiting decision");
      await page.screenshot({ path: resolve(outputDir, "goal-suggestions-decisions.png"), animations: "disabled" });
      await decisions.getByRole("button", { name: "关闭全部工作", exact: true }).click();
      await decisions.waitFor({ state: "hidden" });
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
      await summary.waitFor({ state: "visible", timeout: 15_000 });
      if (!(await row.isVisible())) await summary.click();

      // Blocked work: the question names the real blocked tasks and sends immediately.
      await row.locator('button[data-suggestion="explain_blocked"]').click();
      let turn;
      for (let attempt = 0; attempt < 60 && !turn; attempt += 1) {
        turn = api.turnRequests.find((request) => /受阻：/.test(request.message));
        if (!turn) await page.waitForTimeout(50);
      }
      assert.ok(turn, "Explaining blocked work sends a Turn");
      assert.match(turn.message, /这个 Goal 里有 \d+ 项任务受阻：「[^」]+」/, "The question quotes a real blocked task title");
      assert.ok(/Rerun SkillsBench|Run the next SkillsBench|Run the SkillsBench/.test(turn.message), `names a fixture task, got: ${turn.message.slice(0, 160)}`);
      assert.equal(await page.getByLabel("向 LoopX 发送消息").inputValue(), "", "A suggestion sends immediately and leaves no draft");

      // A stopped Goal invites nothing derived; the steward row remains.
      const stoppedGoals = page.locator(".personal-stopped-goals");
      if (await stoppedGoals.count()) await stoppedGoals.locator("summary").click();
      const stopped = stoppedGoals.locator(".personal-goal-row").first();
      if (await stopped.count()) {
        await stopped.locator(".personal-goal-link").click();
        await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
        await summary.waitFor({ state: "visible" });
        assert.equal(await summary.getAttribute("aria-label"), "添加附件与快捷操作");
        await summary.click();
        assert.equal(await page.getByTestId("goal-suggestions").count(), 0);
        await page.locator(".personal-quick-prompts").first().waitFor({ state: "visible" });
      }
      const coverageEntries = await session.close();

      // Counterexamples: optional attention and a partial listing suggest neither a decision nor completion.
      const second = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 },
        beforeGoto: (secondApi) => { secondApi.transformStatus = optionalAndPartial; } });
      try {
        const { page: other } = second;
        await other.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
        await other.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
        const vitals = (await other.getByTestId("goal-vitals").innerText()).replace(/\s+/g, " ");
        assert.match(vitals, /0 等你决定|可选/, `the lane shows the item as optional: ${vitals}`);
        assert.match(vitals, /已列出 5 项中的 0 项/, `the lane shows the partial listing truthfully: ${vitals}; listed: ${(await other.locator(".goal-overview").innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 400)}`);
        await other.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
        const otherSummary = other.locator(".personal-composer-tools > summary");
        await otherSummary.waitFor({ state: "visible", timeout: 15_000 });
        await other.waitForTimeout(500);
        const text = (await otherSummary.innerText()).trim();
        assert.doesNotMatch(text, /等你决定/, `an optional user action is not a decision: ${text}`);
        assert.doesNotMatch(text, /全部完成|查看成果/, `five open by the owner's count with none listed is not finished: ${text}`);
        await otherSummary.click();
        const labels = (await other.getByTestId("goal-suggestions").locator("button").allInnerTexts().catch(() => [])).join(" / ");
        assert.doesNotMatch(labels, /等你决定|全部完成|查看成果/, `no derived decision or completion: ${labels}`);
        return { coverageEntries: [...coverageEntries, ...await second.close()] };
      } catch (error) {
        await second.close().catch(() => {});
        throw error;
      }
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }
  },
};
